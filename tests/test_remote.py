"""HTTP 모드(remote.py): 비밀 키 주소·404·Bearer·동시 호출·키 저장·로그 가리기·터널 주소 읽기.

진짜 cloudflared는 띄우지 않는다(가짜 스크립트로 출력만 흉내 낸다).
"""

import contextlib
import io
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.shared._httpx_utils import create_mcp_http_client

from openstem import remote
from openstem.server import main as server_main
from openstem.server import mcp

SECRET = "test-secret-0123456789abcdefghijklmnopqrstuv"  # 형식만 맞춘 고정 키(in-process 서버용)
JSON_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
URL_LINE = re.compile(r"MCP 주소: (http://127\.0\.0\.1:\d+)/([A-Za-z0-9_-]+)/mcp")


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    monkeypatch.setenv(remote.CONFIG_DIR_ENV, str(path))
    return path


@contextlib.contextmanager
def running(server: MCPServer, secret: str = SECRET, on_exit=None):
    """uvicorn을 이 프로세스의 스레드에서 빈 포트로 띄운다."""
    sock = remote.bind("127.0.0.1", 0)
    uv = remote.make_uvicorn(remote.build_app(server, secret), secret, on_exit=on_exit)
    thread = threading.Thread(target=uv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not uv.started:
        assert thread.is_alive() and time.monotonic() < deadline, "HTTP 서버가 뜨지 않았다"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        uv.should_exit = True
        thread.join(20)
        sock.close()


# ── 툴 호출 ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "auto"])  # 2025 핸드셰이크 방식과 2026 방식(server/discover) 둘 다
async def test_tools_over_http_with_secret_path(mode):
    with running(mcp) as base:
        async with Client(f"{base}/{SECRET}/mcp", mode=mode) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"verify_math", "get_template", "render_tikz", "export_document"} <= names

            check = await client.call_tool("verify_math", {"op": "solve", "expr": "x**2 = 9"})
            assert not check.is_error, check.content[0].text
            assert [s["result"] for s in json.loads(check.content[0].text)["solutions"]] == ["-3", "3"]

            catalog = await client.call_tool("get_template", {})
            assert not catalog.is_error
            assert "circuits/rc_series" in catalog.content[0].text


@pytest.mark.anyio
async def test_bearer_header_on_plain_mcp_path():
    with running(mcp) as base:
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {SECRET}"})
        async with http, Client(streamable_http_client(f"{base}/mcp", http_client=http)) as client:
            catalog = await client.call_tool("get_template", {})
            assert "circuits/rc_series" in catalog.content[0].text


def test_wrong_or_missing_secret_is_plain_404():
    with running(mcp) as base:
        bad = [
            ("POST", f"{base}/wrong-secret/mcp", {}),
            ("POST", f"{base}/{SECRET[:-1]}/mcp", {}),
            ("POST", f"{base}/{SECRET}x/mcp", {}),
            ("POST", f"{base}/{SECRET}/other", {}),
            ("POST", f"{base}/{SECRET}/mcp/extra", {}),
            ("POST", f"{base}/x/{SECRET}/mcp", {}),
            ("POST", f"{base}/mcp", {}),
            ("POST", f"{base}/mcp", {"Authorization": "Bearer wrong"}),
            ("POST", f"{base}/mcp", {"Authorization": f"Basic {SECRET}"}),
            ("GET", f"{base}/", {}),
            ("GET", f"{base}/mcp", {}),
            ("GET", f"{base}/.well-known/oauth-protected-resource", {}),
            ("GET", f"{base}/.well-known/oauth-authorization-server", {}),
        ]
        for method, url, headers in bad:
            r = httpx2.request(method, url, json=INITIALIZE, headers={**JSON_HEADERS, **headers})
            assert (r.status_code, r.text) == (404, "Not Found"), (method, url, headers)
            assert SECRET not in r.text and "server" not in {k.lower() for k in r.headers}

        ok = httpx2.post(f"{base}/{SECRET}/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert ok.status_code == 200 and ok.json()["result"]["serverInfo"]["name"] == "openstem"
        assert ok.headers["content-type"].startswith("application/json")  # SSE가 아니라 JSON 한 번에

        # 터널을 거치면 Host가 터널 주소다. 키가 맞으면 받아야 한다(421이면 안 된다).
        tunnel_host = {**JSON_HEADERS, "Host": "brave-lion-example.trycloudflare.com"}
        assert httpx2.post(f"{base}/{SECRET}/mcp", json=INITIALIZE, headers=tunnel_host).status_code == 200

        # 서버가 먼저 보낼 것이 없어 GET 스트림은 405(명세의 "스트림 없음")
        stream = httpx2.get(f"{base}/{SECRET}/mcp", headers={"Accept": "text/event-stream"})
        assert stream.status_code == 405 and stream.headers["allow"] == "POST"


# ── 동시 호출 ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_slow_sync_tools_do_not_block_each_other():
    slow = MCPServer("slow-test")
    spans: list[tuple[float, float]] = []

    @slow.tool()
    def nap(seconds: float) -> str:
        begin = time.monotonic()
        time.sleep(seconds)  # 툴 함수처럼 동기로 오래 걸린다
        spans.append((begin, time.monotonic()))
        return "ok"

    stopped = threading.Event()
    with running(slow, on_exit=stopped.set) as base:
        async with Client(f"{base}/{SECRET}/mcp", mode="legacy") as client:
            await client.list_tools()
            results = []

            async def call():
                results.append(await client.call_tool("nap", {"seconds": 1.5}))

            async with anyio.create_task_group() as tg:
                tg.start_soon(call)
                tg.start_soon(call)
    assert [r.content[0].text for r in results] == ["ok", "ok"]
    # 걸린 시간 대신 두 호출이 실제로 겹쳐 돌았는지 본다(PC가 바쁘면 시간 기준은 흔들린다)
    (a_begin, a_end), (b_begin, b_end) = spans
    assert max(a_begin, b_begin) < min(a_end, b_end), "두 호출이 차례로 돌았다"
    assert stopped.is_set()  # 서버가 끝나면 on_exit(터널 끄기)를 부른다


# ── 비밀 키 저장 ────────────────────────────────────────────────────


def test_secret_persists_and_new_secret_changes_it(config_dir):
    first, created = remote.load_secret()
    assert created and len(first) >= 43
    assert (config_dir / remote.SECRET_FILE).read_text(encoding="ascii").strip() == first
    assert remote.load_secret() == (first, False)

    renewed, created = remote.load_secret(regenerate=True)
    assert created and renewed != first
    assert remote.load_secret() == (renewed, False)

    (config_dir / remote.SECRET_FILE).write_text("short\n", encoding="ascii")  # 깨진 파일은 새로 만든다
    fixed, created = remote.load_secret()
    assert created and fixed not in (first, renewed, "short")
    assert not list(config_dir.glob(".http-secret-*"))  # 임시 파일이 남지 않는다
    if os.name == "posix":
        assert (config_dir / remote.SECRET_FILE).stat().st_mode & 0o777 == 0o600


def test_config_dir_follows_platform(tmp_path, monkeypatch):
    monkeypatch.delenv(remote.CONFIG_DIR_ENV, raising=False)
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert remote.config_dir() == tmp_path / "openstem"
    elif sys.platform == "darwin":
        monkeypatch.setenv("HOME", str(tmp_path))
        assert remote.config_dir() == tmp_path / "Library" / "Application Support" / "openstem"
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert remote.config_dir() == tmp_path / "openstem"
        monkeypatch.delenv("XDG_CONFIG_HOME")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert remote.config_dir() == tmp_path / ".config" / "openstem"


# ── 로그 가리기 ─────────────────────────────────────────────────────


def test_log_lines_never_show_the_secret():
    assert remote.redact(f"POST /{SECRET}/mcp 200", SECRET) == f"POST /{remote.REDACTED}/mcp 200"

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    logger = logging.getLogger("openstem.test.redact")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        remote.install_redaction(SECRET)  # 이미 있는 핸들러 모두에 필터를 단다
        logger.warning('127.0.0.1 - "POST /%s/mcp HTTP/1.1" 200', SECRET)
        logger.warning(f"Bearer {SECRET}")
        try:
            raise ValueError(f"/{SECRET}/mcp")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    text = buffer.getvalue()
    assert "Traceback" in text and SECRET not in text  # 메시지 줄도, 예외 traceback도
    assert text.count(remote.REDACTED) == 3


# ── 터널 ───────────────────────────────────────────────────────────

CLOUDFLARED_LOG = [
    "2026-11-03T01:02:03Z INF Thank you for trying Cloudflare Tunnel. Doing so, without a Cloudflare account, "
    "is a quick way to experiment and try it out. (https://www.cloudflare.com/website-terms/)",
    "2026-11-03T01:02:03Z INF Requesting new quick Tunnel on trycloudflare.com...",
    "2026-11-03T01:02:05Z INF +--------------------------------------------------------------------------------------------+",
    "2026-11-03T01:02:05Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |",
    "2026-11-03T01:02:05Z INF |  https://brave-lion-example-test.trycloudflare.com                                         |",
    "2026-11-03T01:02:05Z INF +--------------------------------------------------------------------------------------------+",
    "2026-11-03T01:02:06Z INF Registered tunnel connection connIndex=0 connection=abc event=0 ip=198.41.200.13 location=icn01 protocol=quic",
]


def test_parse_tunnel_url():
    found = [url for line in CLOUDFLARED_LOG if (url := remote.parse_tunnel_url(line))]
    assert found == ["https://brave-lion-example-test.trycloudflare.com"]
    error = 'ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": dial tcp: i/o timeout'
    assert remote.parse_tunnel_url(error) is None
    assert remote.parse_tunnel_url("INF |  https://Upper-Case-Words.trycloudflare.com  |") == "https://upper-case-words.trycloudflare.com"
    assert remote.parse_tunnel_url("https://evil.example.com/?x=.trycloudflare.com") is None
    assert remote.tunnel_command("cloudflared", "http://127.0.0.1:8765") == [
        "cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8765",
    ]  # fmt: skip


def _fake_cloudflared(tmp_path: Path, lines: list[str], stay: bool) -> list[str]:
    """cloudflared처럼 stderr에 로그를 쓰는 가짜 프로그램."""
    script = tmp_path / "fake_cloudflared.py"
    script.write_text(
        "import sys, time\n"
        f"for line in {lines!r}:\n"
        "    sys.stderr.write(line + '\\n')\n"
        "    sys.stderr.flush()\n"
        + ("time.sleep(120)\n" if stay else "sys.exit(1)\n"),
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_tunnel_announces_connector_url_and_stops(tmp_path, capsys):
    tunnel = remote.Tunnel(_fake_cloudflared(tmp_path, CLOUDFLARED_LOG, stay=True))
    tunnel.start()
    try:
        remote._announce_tunnel(tunnel, SECRET, "http://127.0.0.1:8765", True, 20)
        assert tunnel.running()
    finally:
        tunnel.stop()
    assert not tunnel.running() and tunnel.proc.returncode is not None
    out = capsys.readouterr().out
    assert f"ChatGPT 커넥터 URL: https://brave-lion-example-test.trycloudflare.com/{SECRET}/mcp" in out
    assert "개발자 모드" in out and "'없음'" in out
    assert out.count(SECRET) == 1


def test_tunnel_failure_falls_back_to_local_url(tmp_path, capsys):
    error = "2026-11-03T01:02:03Z ERR failed to request quick Tunnel: dial tcp: lookup api.trycloudflare.com: no such host"
    tunnel = remote.Tunnel(_fake_cloudflared(tmp_path, [error], stay=False))
    tunnel.start()
    remote._announce_tunnel(tunnel, SECRET, "http://127.0.0.1:8765", True, 20)
    out = capsys.readouterr().out
    assert "주소를 알려 주기 전에 끝났습니다" in out and "no such host" in out
    assert f"MCP 주소: http://127.0.0.1:8765/{SECRET}/mcp" in out
    assert out.count(SECRET) == 1


def test_find_cloudflared_uses_env_path(tmp_path, monkeypatch):
    exe = tmp_path / "cloudflared.exe"
    monkeypatch.setenv(remote.CLOUDFLARED_ENV, str(exe))
    assert remote.find_cloudflared() is None  # 적힌 파일이 없으면 PATH를 뒤지지 않는다
    exe.write_bytes(b"")
    assert remote.find_cloudflared() == str(exe)


def test_host_helpers():
    assert remote.is_loopback("127.0.0.1") and remote.is_loopback("localhost") and remote.is_loopback("::1")
    assert not remote.is_loopback("0.0.0.0") and not remote.is_loopback("192.168.0.10")
    assert remote.url_host("0.0.0.0") == "127.0.0.1" and remote.url_host("::1") == "[::1]"


# ── CLI ────────────────────────────────────────────────────────────


def test_http_options_need_http_flag(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["openstem-mcp", "--tunnel"])
    with pytest.raises(SystemExit) as exc:
        server_main()
    assert exc.value.code == 2 and "--http와 함께" in capsys.readouterr().err


class Cli:
    """`python -m openstem.server --http ...`를 띄우고 출력을 모은다."""

    def __init__(self, *args: str, env: dict[str, str]) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0  # Ctrl+Break를 이 프로세스에만
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "openstem.server", "--http", "--port", "0", *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        self.lines: list[str] = []
        self._ready = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        assert self._ready.wait(120), "시작 줄이 나오지 않았다:\n" + self.output
        match = next(m for line in self.lines if (m := URL_LINE.search(line)))
        self.base, self.secret = match.group(1), match.group(2)

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
            if URL_LINE.search(line):
                self._ready.set()
        self._ready.set()

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def stop(self) -> int:
        """Ctrl+C(Windows는 Ctrl+Break)처럼 끝낸다."""
        self.proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        try:
            code = self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise
        self._reader.join(5)
        return code


@pytest.fixture
def cli_env(config_dir, output_dir):
    return {**os.environ, "PYTHONIOENCODING": "utf-8", remote.CONFIG_DIR_ENV: str(config_dir)}


def test_cli_http_mode(cli_env, tmp_path):
    first = Cli(env=cli_env)
    try:
        assert "비밀 키를 새로 만들어" in first.output
        r = httpx2.post(f"{first.base}/{first.secret}/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert r.status_code == 200
        assert httpx2.post(f"{first.base}/nope/mcp", json=INITIALIZE, headers=JSON_HEADERS).status_code == 404

        async def call_template():
            async with Client(f"{first.base}/{first.secret}/mcp", mode="legacy") as client:
                return await client.call_tool("get_template", {})

        assert "circuits/rc_series" in anyio.run(call_template).content[0].text
    finally:
        code = first.stop()
    assert code == 0 and "서버를 껐습니다." in first.output
    assert first.output.count(first.secret) == 1, first.output  # 시작 줄 한 번만(요청 로그에 안 나온다)

    # 다음 실행도 같은 키. cloudflared가 없으면 설치 안내를 하고 로컬로만 연다.
    second = Cli("--tunnel", env={**cli_env, remote.CLOUDFLARED_ENV: str(tmp_path / "missing" / "cloudflared.exe")})
    try:
        assert second.secret == first.secret
        assert "winget install --id Cloudflare.cloudflared" in second.output
        assert "brew install cloudflared" in second.output and "Secure MCP Tunnel" in second.output
    finally:
        second.stop()
    assert second.output.count(second.secret) == 1

    third = Cli("--new-secret", env=cli_env)
    try:
        assert third.secret != first.secret and "비밀 키를 새로 만들었습니다" in third.output
        old = httpx2.post(f"{third.base}/{first.secret}/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert old.status_code == 404
    finally:
        third.stop()
