"""tectonic으로 LaTeX를 PDF로 컴파일하고, 로그에서 LLM이 고칠 수 있는 줄만 뽑는다."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openstem import __version__, output, sandbox
from openstem.render import tectonic_setup

TECTONIC_ENV = "OPENSTEM_TECTONIC"
MAX_ERROR_LINES = 10
LOG_LINE_WIDTH = 79  # TeX는 로그를 79자에서 줄바꿈한다
WARMUP_TIMEOUT = 900.0
WARMUP_MARKER = f"warm-{__version__}"  # 버전이 바뀌면(새 템플릿·패키지) 다시 준비한다

INSTALL_HINT = """\
tectonic (the LaTeX engine) was not found. Install it, then restart the MCP host (Claude Desktop etc.).
- Windows: download x86_64-pc-windows-msvc.zip from https://github.com/tectonic-typesetting/tectonic/releases and put tectonic.exe in a folder on PATH
- macOS: brew install tectonic
- Linux: get tectonic with curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh and put it on PATH
- If it is not on PATH, set the environment variable OPENSTEM_TECTONIC to the executable's path
After installing, run `uvx openstem-mcp --warmup` once in a terminal to pre-download the TeX packages (3-5 min)."""

TIMEOUT_HINT = (
    "Compile timed out ({timeout:.0f} s). On first run TeX packages may still be downloading: call again in about a minute "
    "(running `uvx openstem-mcp --warmup` once in a terminal fixes this). "
    "Otherwise check for a figure that is too heavy (\\foreach ranges, pgfplots samples) or a loop that never ends."
)


DOWNLOAD_FAILED = (
    "Could not download TeX packages (network problem: the tectonic bundle server is unreachable). Not a code problem. "
    "Ask the user to check the internet connection or a school/company firewall, then call again once connected."
)
_DOWNLOAD_MARKERS = ("couldn't get it from the internet", "check your network connection", "failure fetching", "error sending request")
MAX_ERROR_CHARS = 300


PREPARING_TECTONIC = (
    "tectonic (the LaTeX engine) is being downloaded and set up now (first run only, a few minutes). "
    "Tell the user, then call again with the same input in about a minute."
)
AUTO_INSTALL_FAILED = "Could not install tectonic automatically: {error}\n" + INSTALL_HINT


class TectonicNotFound(RuntimeError):
    def __init__(self, message: str = INSTALL_HINT) -> None:
        super().__init__(message)


def tectonic_bin_dir() -> Path:
    """자동 설치한 tectonic을 두는 폴더(캐시 폴더/bin)."""
    return _cache_dir() / "bin"


def _locate_tectonic() -> str | None:
    """찾는 순서: OPENSTEM_TECTONIC → PATH → 자동 설치한 캐시 폴더/bin. 찾기만 한다(안내·설치 없음)."""
    configured = output.env_value(TECTONIC_ENV)
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("tectonic")
    if found:
        return found
    cached = tectonic_bin_dir() / tectonic_setup.exe_name()
    return str(cached) if cached.is_file() else None


def find_tectonic() -> str:
    found = _locate_tectonic()
    if found:
        return found
    configured = output.env_value(TECTONIC_ENV)
    if configured:
        raise TectonicNotFound(f"No file at the tectonic path set in {TECTONIC_ENV}: {configured}. Fix the path, then restart the MCP host.")
    raise TectonicNotFound(_missing_message())


_setup_error: str | None = None  # 마지막 자동 설치 실패 이유(성공하면 지운다)


def _missing_message() -> str:
    """tectonic이 없을 때 LLM에 돌려줄 글. 서버에서 자동 설치가 실패했던 경우면 다시 시도를 건다."""
    if not tectonic_setup.can_download():
        return INSTALL_HINT
    if BACKGROUND_WARMUP.retry():
        return PREPARING_TECTONIC
    if _setup_error:
        return AUTO_INSTALL_FAILED.format(error=_setup_error)
    return INSTALL_HINT


def _stderr_progress(message: str) -> None:
    """진행 글은 stderr로 쓴다(stdio 서버에서 stdout은 MCP 프로토콜). --warmup이면 터미널, 서버면 호스트 로그.

    한국어 Windows에서 파이프로 이어진 stderr는 cp949라 호스트 로그(UTF-8로 읽음)에서 깨져서 UTF-8 바이트로 쓴다.
    """
    try:
        sys.stderr.flush()
        sys.stderr.buffer.write((message + "\n").encode("utf-8"))
        sys.stderr.buffer.flush()
    except (AttributeError, OSError, ValueError):
        print(message, file=sys.stderr, flush=True)


def ensure_tectonic(progress: Callable[[str], None] | None = None) -> str:
    """tectonic 경로. 없으면 공식 릴리스를 받아 캐시 폴더/bin에 설치한다.

    OPENSTEM_TECTONIC을 적어 둔 경우, OPENSTEM_NO_DOWNLOAD가 있는 경우, 릴리스가 없는 OS·CPU면 받지 않고
    find_tectonic과 같은 TectonicNotFound를 낸다. 진행 글은 progress(기본 stderr)로 한국어로 쓴다.
    """
    global _setup_error
    found = _locate_tectonic()
    if found:
        return found
    if output.env_value(TECTONIC_ENV) or not tectonic_setup.can_download():
        return find_tectonic()  # 알맞은 안내로 TectonicNotFound
    try:
        path = tectonic_setup.install(tectonic_bin_dir(), progress=progress or _stderr_progress)
    except tectonic_setup.SetupError as exc:
        _setup_error = str(exc)
        raise TectonicNotFound(AUTO_INSTALL_FAILED.format(error=exc)) from exc
    _setup_error = None
    return str(path)


@dataclass
class CompileResult:
    ok: bool
    pdf_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timed_out: bool = False
    download_failed: bool = False


_LINE_RE = re.compile(r"l\.(\d+)(.*)")
_INPUT_LINE_RE = re.compile(r"input line (\d+)")
_MISSING_CHAR_RE = re.compile(r"Missing character: There is no (.+?) (?:\(U\+[0-9A-Fa-f]+\) )?in font")


def _unwrap(lines: list[str], i: int) -> str:
    """79자에서 잘린 `!` 줄을 다음 줄들과 이어 붙인다."""
    line = lines[i]
    while len(lines[i]) == LOG_LINE_WIDTH and i + 1 < len(lines):
        i += 1
        line += lines[i]
    return line.rstrip()


def extract_errors(log: str, line_offset: int = 0, code_line_count: int | None = None) -> list[str]:
    """로그에서 `!` 줄과 `l.<번호>` 줄만 뽑는다.

    `l.<번호>`는 사용자가 보낸 code 기준(1부터)으로 바꾼다. 서버가 붙인 머리말·꼬리말에서 난
    에러는 그렇다고 표시한다.
    """

    def in_code(n: int) -> bool:
        return n >= 1 and (code_line_count is None or n <= code_line_count)

    def map_input_line(m: re.Match[str]) -> str:
        n = int(m.group(1)) - line_offset
        return f"input line {n}" if in_code(n) else "input line (outside code)"

    out: list[str] = []
    lines = log.splitlines()
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if line.startswith("!"):
            item = _INPUT_LINE_RE.sub(map_input_line, _unwrap(lines, i))
        elif m := _LINE_RE.match(line):
            n = int(m.group(1)) - line_offset
            rest = m.group(2).rstrip()
            if in_code(n):
                item = f"l.{n}{rest}"
            elif n < 1:
                item = f"(preamble){rest}"
            else:
                item = f"(after the end of code: check for an unclosed environment, brace or missing semicolon){rest}"
        else:
            continue
        if len(item) > MAX_ERROR_CHARS:
            item = item[:MAX_ERROR_CHARS] + " …(cut)"
        if not out or out[-1] != item:
            out.append(item)
        if len(out) >= MAX_ERROR_LINES:
            break
    return out


def extract_warnings(log: str) -> list[str]:
    missing = sorted({m.group(1) for m in _MISSING_CHAR_RE.finditer(log)})
    if not missing:
        return []
    chars = " ".join(missing[:10])
    return [f"Characters missing from the font were dropped: {chars} (use a similar character or a math symbol, e.g. $\\le$ for ≤)"]


def _stderr_errors(stderr: str) -> list[str]:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip().startswith("error:")]
    return lines[:MAX_ERROR_LINES]


def compile_tex(
    source: str,
    workdir: Path,
    *,
    line_offset: int = 0,
    code_line_count: int | None = None,
    timeout: float | None = None,
    reruns: int | None = None,
) -> CompileResult:
    """`source`를 workdir/figure.tex로 쓰고 PDF로 컴파일한다. shell-escape 등은 --untrusted로 끈다.

    reruns를 주면 tectonic이 스스로 판단하는 재컴파일 횟수를 그 수로 고정한다(0이면 한 번만).
    """
    exe = find_tectonic()
    tex = workdir / "figure.tex"
    tex.write_text(source, encoding="utf-8")
    timeout = timeout if timeout is not None else sandbox.default_timeout()
    extra = ["-r", str(reruns)] if reruns is not None else []
    res = sandbox.run(
        [exe, "--untrusted", "--keep-logs", "--chatter", "minimal", "--color", "never", *extra, tex.name],
        cwd=workdir,
        timeout=timeout,
    )
    if res.timed_out:
        return CompileResult(ok=False, errors=[TIMEOUT_HINT.format(timeout=timeout)], timed_out=True)

    log_path = workdir / "figure.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    pdf = workdir / "figure.pdf"
    warnings = extract_warnings(log)
    if res.returncode == 0 and pdf.exists():
        return CompileResult(ok=True, pdf_path=pdf, warnings=warnings)

    if any(marker in res.stderr for marker in _DOWNLOAD_MARKERS):
        return CompileResult(ok=False, errors=[DOWNLOAD_FAILED], warnings=warnings, download_failed=True)
    errors = extract_errors(log, line_offset, code_line_count) or _stderr_errors(res.stderr)
    if not errors:
        errors = ["Compile failed but the log shows no cause. Simplify the code to narrow it down."]
    return CompileResult(ok=False, errors=errors, warnings=warnings)


def _cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "openstem"


def is_warm() -> bool:
    return (_cache_dir() / WARMUP_MARKER).exists()


def mark_warm() -> None:
    path = _cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / WARMUP_MARKER).write_text("ok\n", encoding="utf-8")


def warmup(
    sources: list[str], timeout: float = WARMUP_TIMEOUT, progress: Callable[[str], None] | None = None
) -> CompileResult:
    """처음 한 번: tectonic이 없으면 먼저 받아 두고(ensure_tectonic, 못 구하면 TectonicNotFound),
    툴들이 쓰는 패키지를 모두 쓰는 문서들을 긴 타임아웃으로 컴파일해 TeX 번들을 받아 둔다."""
    ensure_tectonic(progress)
    result = CompileResult(ok=False, errors=["No documents to warm up."])
    for source in sources:
        with tempfile.TemporaryDirectory(prefix="openstem-warmup-") as tmp:
            result = compile_tex(source, Path(tmp), timeout=timeout)
        if not result.ok:
            return result
    mark_warm()
    return result


class BackgroundWarmup:
    """서버 시작 때 tectonic이 없거나 캐시가 비어 있으면 백그라운드에서 준비한다(tectonic 자동 설치 → TeX 번들).

    렌더링은 끝날 때까지 기다린다(그동안 툴은 "준비 중, 잠시 후 다시"를 돌려준다).
    """

    RETRY_AFTER = 30.0  # 자동 설치가 실패한 뒤(네트워크가 늦게 붙는 등) 이만큼 지나 렌더링 요청이 오면 다시 해 본다

    def __init__(self) -> None:
        self._done = threading.Event()
        self._done.set()
        self._lock = threading.Lock()
        self._make_sources: Callable[[], list[str]] | None = None
        self._finished_at = float("-inf")
        self.error: str | None = None

    def start(self, make_sources: Callable[[], list[str]]) -> bool:
        """make_sources는 백그라운드 스레드에서 부른다(pandoc 변환처럼 시간이 드는 준비가 있어서).

        준비를 시작했거나 이미 도는 중이면 True.
        """
        with self._lock:
            self._make_sources = make_sources
            if not self._done.is_set():
                return True
            have = _locate_tectonic() is not None
            if have and is_warm():
                return False
            if not have and (output.env_value(TECTONIC_ENV) or not tectonic_setup.can_download()):
                return False  # 렌더링 때 설치 안내를 돌려준다
            self._done.clear()
            self.error = None
        threading.Thread(target=self._run, args=(make_sources,), name="openstem-warmup", daemon=True).start()
        return True

    def retry(self) -> bool:
        """서버에서 시작했던 준비가 실패했으면 다시 건다. 다시 걸었거나 아직 도는 중이면 True."""
        if self._make_sources is None:  # 서버가 아니다(--warmup, 시험)
            return False
        if not self._done.is_set():
            return True
        if time.monotonic() - self._finished_at < self.RETRY_AFTER:
            return False
        return self.start(self._make_sources)

    def _run(self, make_sources: Callable[[], list[str]]) -> None:
        try:
            ensure_tectonic()
            if not is_warm():
                result = warmup(make_sources())
                if not result.ok:
                    self.error = "; ".join(result.errors)
        except Exception as exc:  # 백그라운드 작업이 서버를 죽이지 않게
            self.error = str(exc)
        finally:
            self._finished_at = time.monotonic()
            self._done.set()

    def wait(self, timeout: float) -> bool:
        """준비가 끝났으면 True. timeout 안에 안 끝나면 False."""
        return self._done.wait(timeout)


BACKGROUND_WARMUP = BackgroundWarmup()
