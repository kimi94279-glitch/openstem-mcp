"""tectonic(LaTeX 엔진)을 못 찾으면 공식 GitHub 릴리스를 받아 캐시 폴더에 설치한다(M6, 원클릭 설치용).

- 버전·파일 이름·크기·SHA-256은 코드에 고정한다(GitHub 릴리스 API의 digest와 같은 값). 새 버전으로 올릴 때는
  `gh api repos/tectonic-typesetting/tectonic/releases/tags/tectonic@<버전>`으로 값을 다시 받아 바꾼다.
- 압축 파일은 설치 폴더 안 임시 파일로 받고(크기 상한), 해시가 맞아야만 연다. 그 안에서 실행 파일 하나만
  꺼내 같은 폴더 임시 파일에 쓰고, `--version`으로 실행되는지 본 뒤 이름을 바꿔(원자적으로) 둔다.
- 스레드·프로세스 둘이 동시에 받지 않게 잠근다(서버 여러 개, `--warmup`이 함께 돌 수 있다).
- 환경변수 OPENSTEM_NO_DOWNLOAD가 있으면 받지 않는다. 릴리스가 없는 OS·CPU(Windows ARM, 32비트 등)도 받지 않는다.

이 모듈은 latex.py를 import하지 않는다(설치 폴더는 부르는 쪽이 준다). 에러 메시지는 LLM에 그대로 가므로 짧은 영어,
사람이 터미널에서 보는 진행 글(progress)은 한국어로 쓴다.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import http.client
import os
import platform
import ssl
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from openstem import __version__, output, sandbox

VERSION = "0.17.0"
NO_DOWNLOAD_ENV = "OPENSTEM_NO_DOWNLOAD"
RELEASE_URL = "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{version}/{asset}"
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024  # 압축 파일 상한(실제 10~22 MB)
MAX_EXE_BYTES = 250 * 1024 * 1024  # 압축을 푼 실행 파일 상한(실제 약 50 MB)
TOTAL_TIMEOUT = 300.0  # 잠금 대기 + 내려받기 + 풀기 전체
SOCKET_TIMEOUT = 30.0  # 연결·읽기 한 번
PROBE_TIMEOUT = 60.0
CHUNK = 256 * 1024
LOCK_NAME = ".tectonic-install.lock"

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Asset:
    name: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return RELEASE_URL.format(version=VERSION, asset=self.name)


# (sys.platform, 정규화한 CPU) → 릴리스 파일. Linux는 정적 링크(musl) 빌드라 배포판과 상관없이 돈다.
ASSETS: dict[tuple[str, str], Asset] = {
    ("win32", "x86_64"): Asset(
        "tectonic-0.17.0-x86_64-pc-windows-msvc.zip",
        "f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f",
        21_060_223,
    ),
    ("darwin", "aarch64"): Asset(
        "tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
        "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667",
        21_704_674,
    ),
    ("darwin", "x86_64"): Asset(
        "tectonic-0.17.0-x86_64-apple-darwin.tar.gz",
        "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80",
        21_790_177,
    ),
    ("linux", "x86_64"): Asset(
        "tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz",
        "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7",
        10_151_914,
    ),
    ("linux", "aarch64"): Asset(
        "tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz",
        "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8",
        9_926_834,
    ),
}

# platform.machine()은 OS마다 이름이 다르다(Windows "AMD64"·"ARM64", macOS "arm64", Linux "aarch64").
_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


class SetupError(RuntimeError):
    """자동 설치 실패. 메시지는 LLM에 그대로 가는 짧은 영어다."""


def platform_key(system: str | None = None, machine: str | None = None) -> tuple[str, str] | None:
    system = sys.platform if system is None else system
    if system.startswith("linux"):
        system = "linux"
    arch = _ARCH.get((platform.machine() if machine is None else machine).strip().lower())
    return (system, arch) if arch else None


def asset_for(system: str | None = None, machine: str | None = None) -> Asset | None:
    """이 OS·CPU용 릴리스 파일. 없으면 None(설치 안내로 돌아간다)."""
    key = platform_key(system, machine)
    return ASSETS.get(key) if key else None


def exe_name(system: str | None = None) -> str:
    return "tectonic.exe" if (sys.platform if system is None else system) == "win32" else "tectonic"


def download_disabled() -> bool:
    value = output.env_value(NO_DOWNLOAD_ENV)
    return value is not None and value.lower() not in ("0", "false", "no", "off")


def can_download() -> bool:
    """이 PC에서 자동 설치를 시도할 수 있나(꺼져 있지 않고, 맞는 릴리스가 있다)."""
    return not download_disabled() and asset_for() is not None


# ── 설치 ─────────────────────────────────────────────────────────────

_thread_lock = threading.Lock()


def install(
    bin_dir: Path,
    *,
    asset: Asset | None = None,
    url: str | None = None,
    progress: Progress | None = None,
    timeout: float = TOTAL_TIMEOUT,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    probe: bool = True,
) -> Path:
    """tectonic을 bin_dir에 설치하고 실행 파일 경로를 돌려준다. 이미 있으면 받지 않고 그대로 돌려준다.

    asset·url은 시험용(가짜 압축 파일·로컬 서버). probe=False면 `--version` 실행 확인을 건너뛴다.
    실패하면 SetupError를 내고, bin_dir에는 잠금 파일 말고 아무것도 남기지 않는다.
    """
    asset = asset or asset_for()
    if asset is None:
        raise SetupError(f"No prebuilt tectonic for this platform ({sys.platform}, {platform.machine()}).")
    say = progress or (lambda _message: None)
    target = bin_dir / exe_name()
    deadline = time.monotonic() + timeout
    if not _thread_lock.acquire(timeout=timeout):
        raise SetupError("Another tectonic download is still running; try again in a minute.")
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
        with _file_lock(bin_dir / LOCK_NAME, deadline):
            if target.is_file():  # 기다리는 동안 다른 스레드·프로세스가 설치했다
                return target
            say(f"tectonic {VERSION}(LaTeX 엔진)을 공식 릴리스에서 내려받는 중입니다(약 {asset.size / 1e6:.0f} MB)...")
            archive = _download(url or asset.url, bin_dir, asset, deadline, max_bytes)
            try:
                _extract(archive, asset.name, target, probe)
            finally:
                archive.unlink(missing_ok=True)
            say(f"tectonic 설치 완료: {target}")
            return target
    except OSError as exc:  # 캐시 폴더에 못 쓴다(권한, 디스크 공간)
        raise SetupError(f"Could not install tectonic into {bin_dir} ({_short(exc)}).") from exc
    finally:
        _thread_lock.release()


@contextlib.contextmanager
def _file_lock(path: Path, deadline: float) -> Iterator[None]:
    """다른 프로세스와 동시에 받지 않게 잠근다. 잠근 프로세스가 죽으면 OS가 푼다."""
    with open(path, "a+b") as fh:
        while True:
            try:
                _lock(fh)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SetupError("Another program is still installing tectonic; try again in a minute.") from None
                time.sleep(0.5)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                _unlock(fh)


if sys.platform == "win32":
    import msvcrt

    def _lock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub은 릴리스 파일을 다른 https 호스트로 넘긴다. http로 내려가는 리디렉션은 거절한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise SetupError("tectonic download was redirected to a non-HTTPS address; refused.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ssl_context() -> ssl.SSLContext:
    """운영체제 인증서 + certifi 묶음(있으면). uv가 받은 파이썬은 OS 인증서를 못 찾는 경우가 있다."""
    context = ssl.create_default_context()
    with contextlib.suppress(ImportError, OSError, ssl.SSLError):
        import certifi

        context.load_verify_locations(certifi.where())
    return context


def _short(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    text = str(getattr(exc, "reason", None) or exc) or type(exc).__name__
    return text.splitlines()[0][:200]


def _download(url: str, bin_dir: Path, asset: Asset, deadline: float, max_bytes: int) -> Path:
    """url을 bin_dir 안 임시 파일로 받고 SHA-256을 확인한다. 실패하면 임시 파일을 지우고 SetupError."""
    fd, name = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=bin_dir)
    part = Path(name)
    try:
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as out:
            opener = urllib.request.build_opener(_HttpsOnlyRedirect, urllib.request.HTTPSHandler(context=_ssl_context()))
            request = urllib.request.Request(url, headers={"User-Agent": f"openstem-mcp/{__version__}"})
            with opener.open(request, timeout=SOCKET_TIMEOUT) as response:
                length = response.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise SetupError(f"tectonic download is too large ({int(length)} bytes, limit {max_bytes}); refused.")
                while chunk := response.read(CHUNK):
                    total += len(chunk)
                    if total > max_bytes:
                        raise SetupError(f"tectonic download is larger than the {max_bytes}-byte limit; refused.")
                    if time.monotonic() > deadline:
                        raise SetupError("tectonic download took too long (over 5 minutes); check the network and retry.")
                    digest.update(chunk)
                    out.write(chunk)
        if digest.hexdigest() != asset.sha256:
            raise SetupError(
                f"tectonic download failed the SHA-256 check ({total} bytes; corrupted or altered file). Nothing was installed."
            )
        return part
    except SetupError:
        part.unlink(missing_ok=True)
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:  # URLError·HTTPError·시간 초과는 OSError
        part.unlink(missing_ok=True)
        raise SetupError(f"Could not download tectonic ({_short(exc)}). Check the internet connection or firewall.") from exc


def _safe_name(name: str) -> bool:
    """압축 안 이름이 절대 경로·드라이브 문자·상위 폴더(..)가 아닌지."""
    unified = name.replace("\\", "/")
    parts = PurePosixPath(unified).parts
    return bool(unified) and not unified.startswith("/") and ":" not in unified and ".." not in parts


def _extract(archive: Path, asset_name: str, target: Path, probe: bool) -> None:
    """압축 파일에서 실행 파일(target.name) 하나만 꺼내 target에 둔다."""
    exe = target.name
    try:
        if asset_name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                _check_names(i.filename for i in infos)
                matches = [i for i in infos if PurePosixPath(i.filename.replace("\\", "/")).name == exe]
                if len(matches) != 1 or matches[0].is_dir():
                    raise SetupError(f"The tectonic archive does not contain exactly one {exe}; refused.")
                if matches[0].file_size > MAX_EXE_BYTES:
                    raise SetupError("The tectonic executable in the archive is unexpectedly large; refused.")
                with zf.open(matches[0]) as src:
                    _write_exe(src, target, probe)
        elif asset_name.endswith(".tar.gz"):
            with tarfile.open(archive, "r:gz") as tf:
                members = tf.getmembers()
                _check_names(m.name for m in members)
                matches = [m for m in members if PurePosixPath(m.name).name == exe]
                if len(matches) != 1 or not matches[0].isfile():  # 링크·장치 파일은 거절
                    raise SetupError(f"The tectonic archive does not contain exactly one regular file {exe}; refused.")
                if matches[0].size > MAX_EXE_BYTES:
                    raise SetupError("The tectonic executable in the archive is unexpectedly large; refused.")
                src = tf.extractfile(matches[0])
                if src is None:
                    raise SetupError(f"Could not read {exe} from the tectonic archive.")
                with src:
                    _write_exe(src, target, probe)
        else:
            raise SetupError(f"Unknown archive type: {asset_name}")
    except (zipfile.BadZipFile, tarfile.TarError, gzip.BadGzipFile, zlib.error, EOFError) as exc:
        raise SetupError(f"The tectonic archive is damaged ({_short(exc)}).") from exc


def _check_names(names: Iterable[str]) -> None:
    for name in names:
        if not _safe_name(name):
            raise SetupError(f"The tectonic archive has an unsafe file name ({name[:80]!r}); refused.")


def _write_exe(src: IO[bytes], target: Path, probe: bool) -> None:
    fd, name = tempfile.mkstemp(prefix=".tectonic-", suffix=target.suffix, dir=target.parent)
    tmp = Path(name)
    try:
        copied = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := src.read(CHUNK):
                copied += len(chunk)
                if copied > MAX_EXE_BYTES:
                    raise SetupError("The tectonic executable in the archive is unexpectedly large; refused.")
                out.write(chunk)
        if sys.platform != "win32":
            tmp.chmod(0o755)
        if probe:
            _probe(tmp)
        _replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _probe(exe: Path) -> None:
    """받은 실행 파일이 이 PC에서 실제로 뜨는지(필요한 시스템 라이브러리, CPU, 서명)."""
    try:
        res = sandbox.run([str(exe), "--version"], cwd=exe.parent, timeout=PROBE_TIMEOUT)
    except OSError as exc:
        raise SetupError(f"The downloaded tectonic cannot start on this computer ({_short(exc)}).") from exc
    if res.timed_out or res.returncode != 0 or "tectonic" not in res.stdout.lower():
        code = "timeout" if res.timed_out else f"exit code {res.returncode}"
        raise SetupError(f"The downloaded tectonic cannot start on this computer ({code}).")


def _replace(tmp: Path, target: Path) -> None:
    """원자적으로 이름을 바꾼다. Windows에서는 백신 검사가 잠깐 파일을 잡고 있을 수 있어 몇 번 다시 한다."""
    for attempt in range(10):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.5)
