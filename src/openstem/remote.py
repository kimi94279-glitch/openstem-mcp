"""HTTP 모드(`openstem-mcp --http`): ChatGPT 같은 원격 MCP 클라이언트가 붙을 수 있게 streamable HTTP로 연다.

- 주소: http://127.0.0.1:<포트>/<비밀 키>/mcp (또는 /mcp + `Authorization: Bearer <키>`). 키가 틀리면 그냥 404.
- 비밀 키는 설정 폴더의 `http-secret`에 저장해 다음 실행에도 같은 주소를 쓴다(`--new-secret`으로 교체).
- `--tunnel`: 설치된 cloudflared로 임시 HTTPS 주소(*.trycloudflare.com)를 열어 붙일 전체 URL을 출력한다.
  cloudflared는 내려받지 않는다(설치 방법만 안내).
- 툴은 stdio와 같다(server.py의 같은 MCPServer). 툴 함수는 동기 함수라 SDK가 anyio.to_thread.run_sync로
  작업 스레드에서 돌리고(mcpserver/utilities/func_metadata.py) 요청마다 따로 처리하므로, 오래 걸리는 툴이
  다른 요청을 막지 않는다(tests/test_remote.py에서 확인).

사람이 터미널에서 보는 출력은 한국어다. LLM에 가는 글은 툴이 돌려주는 것뿐이라 여기서는 만들지 않는다.
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from openstem import __version__
from openstem.output import env_value, output_dir

if TYPE_CHECKING:
    import uvicorn
    from mcp.server.mcpserver import MCPServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MCP_PATH = "/mcp"
SECRET_FILE = "http-secret"
CONFIG_DIR_ENV = "OPENSTEM_CONFIG_DIR"  # 설정 폴더를 바꾼다(테스트·여러 벌 띄우기용)
CLOUDFLARED_ENV = "OPENSTEM_CLOUDFLARED"
TUNNEL_TIMEOUT = 30.0
REDACTED = "<비밀 키>"

# secrets.token_urlsafe(32)는 43글자. 손으로 고친 파일도 받되 너무 짧거나 다른 글자가 섞이면 새로 만든다.
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
# 임시 터널 주소. api.trycloudflare.com은 cloudflared가 터널을 요청하는 곳이라 에러 줄에도 나온다.
_TUNNEL_URL_RE = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\b", re.IGNORECASE)


# ── 비밀 키 ─────────────────────────────────────────────────────────


def config_dir() -> Path:
    """설정 폴더: Windows %APPDATA%\\openstem, macOS ~/Library/Application Support/openstem,
    Linux $XDG_CONFIG_HOME/openstem(없으면 ~/.config/openstem). OPENSTEM_CONFIG_DIR이 있으면 그 폴더."""
    override = env_value(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "openstem"


def secret_path() -> Path:
    return config_dir() / SECRET_FILE


def load_secret(*, regenerate: bool = False) -> tuple[str, bool]:
    """(비밀 키, 새로 만들었는지). 저장된 키가 없거나 형식이 깨졌거나 regenerate면 새로 만들어 저장한다."""
    path = secret_path()
    if not regenerate:
        try:
            text = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            text = ""
        if _SECRET_RE.fullmatch(text):
            return text, False
    secret = secrets.token_urlsafe(32)  # 256비트
    _write_private(path, secret + "\n")
    return secret, True


def _write_private(path: Path, text: str) -> None:
    """나만 읽을 수 있게 저장한다. POSIX는 폴더 0700·파일 0600(mkstemp가 0600으로 만든다).
    Windows는 %APPDATA%가 사용자 프로필 안이라 ACL이 이미 본인(과 관리자)으로 제한되어 있다(chmod는 읽기 전용만 바꾼다).
    임시 파일에 쓴 뒤 바꿔치기해서, 쓰다가 끊겨도 반쯤 쓴 키가 남지 않는다."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".http-secret-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if os.name == "posix":
        os.chmod(path, 0o600)


# ── ASGI 앱 ─────────────────────────────────────────────────────────


class SecretGate:
    """비밀 키가 맞는 요청만 SDK 앱(/mcp)으로 넘긴다. 그 밖의 모든 경로·키 없는 요청은 같은 404."""

    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self._key = secret.encode("ascii")

    def _authorized(self, scope: Scope) -> bool:
        path: str = scope["path"]
        parts = path.split("/")  # "/<키>/mcp" → ["", 키, "mcp"]
        if len(parts) in (3, 4) and parts[0] == "" and parts[2] == "mcp" and parts[3:] in ([], [""]):
            return hmac.compare_digest(parts[1].encode("utf-8", "replace"), self._key)
        if path in (MCP_PATH, MCP_PATH + "/"):
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    scheme, _, token = value.partition(b" ")
                    return scheme.lower() == b"bearer" and hmac.compare_digest(token.strip(), self._key)
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":  # SDK 세션 관리자를 시작·정리한다
            await self.app(scope, receive, send)
            return
        if kind != "http":
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if not self._authorized(scope):
            # 키가 틀렸는지·경로가 없는지 구분하지 않는다(키를 추측하는 데 쓸 정보를 주지 않는다).
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        if scope["method"] == "GET":
            # GET은 서버가 먼저 보내는 SSE 스트림인데 이 서버는 보낼 것이 없다. 명세가 허용하는 "스트림 없음" 405로 답한다.
            # stateless 모드에서 SDK에 넘기면 아무것도 오지 않는 연결이 열린 채 남는다.
            await Response(status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        # 키를 뺀 경로로 바꿔 넘긴다. 원래 scope는 고치지 않는다.
        await self.app(dict(scope, path=MCP_PATH, raw_path=MCP_PATH.encode()), receive, send)


def build_app(server: MCPServer, secret: str) -> SecretGate:
    """MCPServer의 streamable HTTP 앱을 비밀 키 검사로 감싼다."""
    inner = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        # stateless + JSON 응답: 툴만 있는 서버라 세션 상태가 필요 없고 서버가 먼저 보내는 알림도 없다.
        # 세션이 없으면 오래 쉬었다 와도(세션 만료) 끊기지 않고, SSE를 안 쓰면 SSE를 버퍼링하거나
        # 지원하지 않는 터널·프록시를 거쳐도 응답이 한 번에 간다.
        stateless_http=True,
        json_response=True,
        # Host 검사(DNS 리바인딩 방지)는 끈다. 터널을 거친 요청의 Host는 *.trycloudflare.com 같은 터널 주소이고,
        # 다른 터널이나 같은 네트워크 주소로 쓰면 또 다른 이름이 와서 허용 목록을 미리 정할 수 없다.
        # 리바인딩 공격은 브라우저의 악성 페이지가 127.0.0.1로 요청을 보내게 하는 것인데, 경로나 Authorization 헤더에
        # 비밀 키(256비트)가 없으면 SecretGate가 SDK에 닿기 전에 404로 끝내므로 키를 모르는 페이지는 아무것도 못 한다.
        # 키를 아는 쪽은 Host와 상관없이 터널 주소로 바로 부를 수 있으니 Host 검사가 더 막아 주는 것이 없다.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return SecretGate(inner, secret)


# ── 로그에서 키 가리기 ───────────────────────────────────────────────


def redact(text: str, secret: str) -> str:
    return text.replace(secret, REDACTED) if secret else text


class RedactSecret(logging.Filter):
    """로그 줄에 비밀 키가 들어 있으면 가린다. 접근 로그는 아예 끄지만(make_uvicorn) 다른 경로로 새는 것도 막는다."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if self.secret in message:
            record.msg, record.args = redact(message, self.secret), None
            record.__dict__.pop("color_message", None)  # uvicorn 서식이 원래 형식 문자열을 다시 쓰지 않게
        if record.exc_info and not record.exc_text:  # 서식기보다 먼저 traceback 글을 만들어 가린다
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text and self.secret in record.exc_text:
            record.exc_text = redact(record.exc_text, self.secret)
        return True


def install_redaction(secret: str) -> None:
    """지금 있는 모든 로그 핸들러에 키 가리기 필터를 단다(uvicorn 설정이 핸들러를 만든 뒤에 부른다)."""
    loggers = [logging.getLogger(), *(lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    for lg in loggers:
        for handler in lg.handlers:
            if not any(isinstance(f, RedactSecret) and f.secret == secret for f in handler.filters):
                handler.addFilter(RedactSecret(secret))


# ── 소켓·uvicorn ────────────────────────────────────────────────────


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def url_host(host: str) -> str:
    """URL에 쓸 호스트. 모든 주소(0.0.0.0, ::)로 열었으면 이 PC 주소를 쓴다."""
    host = host.strip("[]")
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    return f"[{host}]" if ":" in host else host


def bind(host: str, port: int) -> socket.socket:
    """소켓을 직접 열어 uvicorn에 넘긴다. 포트 0이면 빈 포트를 고르고, 실패는 OSError로 바로 알 수 있다.
    Windows는 SO_REUSEADDR이 이미 쓰는 포트를 빼앗을 수 있게 하는 옵션이라 대신 SO_EXCLUSIVEADDRUSE를 쓴다."""
    family, kind, proto, _, address = socket.getaddrinfo(
        host.strip("[]") or None, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(family, kind, proto)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def make_uvicorn(app: ASGIApp, secret: str, on_exit: Callable[[], None] | None = None) -> uvicorn.Server:
    """on_exit은 서버가 정리를 마친 직후(신호로 끝날 때도) 부른다."""
    import uvicorn  # HTTP 모드에서만 쓴다(import 0.5초 남짓이라 stdio 시작을 늦추지 않게)

    class _Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[override]
            with super().capture_signals():
                try:
                    yield
                finally:
                    if on_exit is not None:
                        on_exit()
                    # uvicorn은 정리를 마친 뒤 받은 신호를 다시 일으킨다. SIGTERM·Ctrl+Break(SIGBREAK)는 기본 동작이
                    # finally도 거치지 않고 프로세스를 끝내므로, 이미 정리했으니 다시 일으키지 않고 serve()가 그냥 끝나게 한다.
                    getattr(self, "_captured_signals", []).clear()

    config = uvicorn.Config(
        app,
        lifespan="on",
        ws="none",
        log_level="warning",
        access_log=False,  # 접근 로그는 요청 경로(= 비밀 키)를 찍는다
        server_header=False,
        timeout_graceful_shutdown=5,  # Ctrl+C 때 오래 걸리는 툴 호출을 끝없이 기다리지 않게
    )
    install_redaction(secret)  # uvicorn.Config가 로그 핸들러를 만든 뒤
    return _Server(config)


# ── 터널(cloudflared) ───────────────────────────────────────────────


def parse_tunnel_url(line: str) -> str | None:
    """cloudflared 출력 한 줄에서 https://<임의>.trycloudflare.com 주소를 찾는다."""
    match = _TUNNEL_URL_RE.search(line)
    return match.group(0).lower() if match else None


def find_cloudflared() -> str | None:
    """OPENSTEM_CLOUDFLARED(실행 파일 경로)가 있으면 그것, 없으면 PATH에서 찾는다."""
    configured = env_value(CLOUDFLARED_ENV)
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    return shutil.which("cloudflared")


def tunnel_command(exe: str, local_url: str) -> list[str]:
    return [exe, "tunnel", "--no-autoupdate", "--url", local_url]


class Tunnel:
    """cloudflared 자식 프로세스. 출력(cloudflared는 stderr에 쓴다)을 계속 읽으며 터널 주소를 찾는다."""

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.proc: subprocess.Popen[str] | None = None
        self.url: str | None = None
        self._settled = threading.Event()  # 주소를 찾았거나 프로세스가 끝났다
        self._tail: collections.deque[str] = collections.deque(maxlen=6)

    def start(self) -> None:
        # 콘솔은 물려준다: 터미널 창을 닫으면 cloudflared도 같이 끝나게.
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        atexit.register(self.stop)
        threading.Thread(target=self._read, name="openstem-tunnel", daemon=True).start()

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:  # 주소를 찾은 뒤에도 계속 읽어 비운다(파이프가 차면 cloudflared가 멈춘다)
            if self.url is None:
                url = parse_tunnel_url(line)
                if url:
                    self.url = url
                    self._settled.set()
                elif line.strip():
                    self._tail.append(line.rstrip())
        with contextlib.suppress(subprocess.TimeoutExpired):  # 출력이 닫힌 직후엔 아직 끝나는 중일 수 있다
            self.proc.wait(5)
        self._settled.set()

    def wait_url(self, timeout: float = TUNNEL_TIMEOUT) -> str | None:
        self._settled.wait(timeout)
        return self.url

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def tail(self) -> list[str]:
        return list(self._tail)

    def stop(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)


# ── 실행 ────────────────────────────────────────────────────────────


def _say(*lines: str) -> None:
    for line in lines:
        print(line, flush=True)


def _print_local(base: str, secret: str, loopback: bool) -> None:
    # 비밀 키가 화면에 나오는 줄은 시작할 때 이 한 줄(터널을 쓰면 터널 URL 한 줄)뿐이다.
    _say(
        f"MCP 주소: {base}/{secret}{MCP_PATH}",
        "  · 주소 가운데의 긴 문자열이 비밀 키입니다. 남에게 보여 주지 마세요(바꾸려면 --new-secret).",
        f"  · 키를 헤더로 넣는 클라이언트는 {base}{MCP_PATH} 에 'Authorization: Bearer <위 키>'를 보내도 됩니다.",
    )
    if loopback:
        _say("  · 이 주소는 이 PC 안에서만 열립니다. ChatGPT처럼 인터넷에서 붙는 앱은 --tunnel을 함께 쓰세요.")


def _print_cloudflared_missing(base: str) -> None:
    configured = env_value(CLOUDFLARED_ENV)
    if configured:
        _say(f"{CLOUDFLARED_ENV}에 적힌 파일이 없습니다: {configured}")
    else:
        _say("cloudflared를 찾지 못해 터널을 열지 못했습니다. 설치한 뒤 다시 실행하세요:")
    _say(
        "  Windows: winget install --id Cloudflare.cloudflared",
        "  macOS:   brew install cloudflared",
        "  Linux:   Cloudflare 문서(Cloudflare Tunnel → Downloads)의 배포판별 패키지 설치 안내",
        f"  설치한 곳이 PATH에 없으면 환경변수 {CLOUDFLARED_ENV}에 실행 파일 경로를 넣으세요.",
        f"  다른 방법: OpenAI의 'Secure MCP Tunnel' 같은 다른 터널로 아래 로컬 주소({base}/...)를 연결해도 됩니다.",
        "지금은 터널 없이 이 PC 안에서만 열었습니다.",
    )


def _announce_tunnel(tunnel: Tunnel, secret: str, base: str, loopback: bool, timeout: float) -> None:
    url = tunnel.wait_url(timeout)
    if url is None:
        reason = f"{timeout:.0f}초 안에 주소를 알려 주지 않았습니다" if tunnel.running() else "주소를 알려 주기 전에 끝났습니다"
        tunnel.stop()
        _say(f"cloudflared가 {reason}. 마지막 출력:", *(f"  {line}" for line in tunnel.tail() or ["(출력 없음)"]))
        _say("인터넷 연결·방화벽을 확인하고 다시 실행해 보세요. 지금은 터널 없이 이 PC 안에서만 열려 있습니다.")
        _print_local(base, secret, loopback)
        return
    _say(
        f"ChatGPT 커넥터 URL: {url}/{secret}{MCP_PATH}",
        "  · 주소 가운데의 긴 문자열이 비밀 키입니다. 남에게 보여 주지 마세요(바꾸려면 --new-secret).",
        "  · 터널이 실제로 열리기까지 몇십 초 더 걸릴 수 있습니다.",
        "  · 임시 터널 주소는 실행할 때마다 바뀝니다. 다시 켜면 ChatGPT에 새 URL을 넣어야 합니다.",
        "ChatGPT에 연결하기(메뉴 이름은 바뀔 수 있습니다):",
        "  1. ChatGPT 설정 → 앱(또는 커넥터) → 고급 설정에서 '개발자 모드'를 켭니다.",
        "  2. 앱 만들기(사용자 지정 MCP 서버 추가)를 누르고 위 URL을 붙여 넣습니다.",
        "  3. 인증은 '없음'을 고릅니다. URL 안의 비밀 키가 인증 역할을 합니다.",
        "이 창을 닫거나 PC가 잠들면 연결이 끊깁니다.",
    )


def serve(
    server: MCPServer,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    new_secret: bool = False,
    tunnel: bool = False,
    before_serve: Callable[[], None] | None = None,
    tunnel_timeout: float = TUNNEL_TIMEOUT,
) -> int:
    """`openstem-mcp --http`의 본체. 끝날 때까지 돌고 종료 코드를 돌려준다."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    # SDK는 요청마다 INFO 로그를 남긴다(stateless라 매번 "Terminating session"). 사람이 보는 창이라 경고 이상만.
    logging.getLogger().setLevel(logging.WARNING)

    try:
        secret, created = load_secret(regenerate=new_secret)
    except OSError as exc:
        _say(f"비밀 키를 {secret_path()}에 저장하지 못했습니다: {exc}")
        return 1
    try:
        sock = bind(host, port)
    except OSError as exc:
        _say(
            f"{host}:{port}에서 열지 못했습니다: {exc.strerror or exc}",
            "다른 프로그램(또는 이미 켜 둔 openstem-mcp)이 이 포트를 쓰고 있으면 --port로 다른 번호를 고르세요.",
        )
        return 1
    port = sock.getsockname()[1]
    base = f"http://{url_host(host)}:{port}"
    loopback = is_loopback(host)

    app = build_app(server, secret)
    if before_serve:
        before_serve()

    _say(f"openstem-mcp {__version__} HTTP 모드: {url_host(host)}:{port}에서 기다립니다. 끄려면 Ctrl+C를 누르세요.")
    if not loopback:
        _say(
            f"경고: --host {host}는 이 PC 밖(같은 네트워크의 다른 기기)에서도 접속할 수 있게 엽니다.",
            "  암호화(HTTPS)가 없어 같은 네트워크의 누군가가 오가는 내용과 비밀 키를 엿볼 수 있습니다.",
            "  인터넷에서 쓰려면 --host 없이 --tunnel(HTTPS)을 쓰세요.",
        )
    if new_secret:
        _say("비밀 키를 새로 만들었습니다. 예전 주소는 이제 404가 되니 연결해 둔 앱에 새 주소를 다시 넣으세요.")
    elif created:
        _say(f"비밀 키를 새로 만들어 {secret_path()}에 저장했습니다. 다음 실행에도 같은 주소를 씁니다.")
    _say(f"결과 파일(그림·문서)은 이 PC의 {output_dir()}에 저장됩니다.")

    tun: Tunnel | None = None
    if tunnel:
        exe = find_cloudflared()
        if exe is None:
            _print_cloudflared_missing(base)
            _print_local(base, secret, loopback)
        else:
            _say(f"cloudflared로 임시 HTTPS 주소를 여는 중입니다(최대 {tunnel_timeout:.0f}초)...")
            tun = Tunnel(tunnel_command(exe, base))
            try:
                tun.start()
            except OSError as exc:
                _say(f"cloudflared를 실행하지 못했습니다: {exc}")
                tun = None
                _print_local(base, secret, loopback)
            else:
                threading.Thread(
                    target=_announce_tunnel,
                    args=(tun, secret, base, loopback, tunnel_timeout),
                    name="openstem-tunnel-wait",
                    daemon=True,
                ).start()
    else:
        _print_local(base, secret, loopback)

    uv = make_uvicorn(app, secret, on_exit=tun.stop if tun is not None else None)
    try:
        uv.run(sockets=[sock])
    except KeyboardInterrupt:  # 시작이 끝나기 전에 Ctrl+C를 누른 경우
        pass
    finally:
        if tun is not None:
            tun.stop()
        sock.close()
    _say("서버를 껐습니다.")
    return 0
