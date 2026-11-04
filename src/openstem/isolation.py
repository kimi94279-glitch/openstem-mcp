"""운영체제 수준 격리: 입력 검사를 빠져나간 우회가 있어도 사용자 파일에 닿지 못하게 하는 두 번째 벽.

tectonic과 파이썬 작업자를 운영체제 샌드박스 안에서 돌린다(→ docs/decisions/0014-os-isolation.md).
- Linux: bubblewrap(`bwrap`). 빈 루트에 시스템·파이썬·tectonic만 읽기 전용으로 붙이고, 쓰기는 작업 폴더만.
  홈 폴더는 보이지 않는다(tectonic 캐시만 예외). 파이썬 작업자는 네트워크도 끈다.
- macOS: `sandbox-exec` 프로필. 읽기는 시스템·파이썬·캐시·작업 폴더만, 쓰기는 작업 폴더만, 작업자는 네트워크 거부.
- Windows: 아직 없다(AppContainer·제한 토큰은 다음 단계).

`OPENSTEM_ISOLATION=auto|off|require`(기본 auto). auto는 쓸 수 있으면 격리하고, 없으면(bwrap 미설치, 사용자
네임스페이스 금지, 중첩 샌드박스 등) 지금처럼 돌며 로그·`--doctor`에 한 줄을 남긴다. require는 격리를 못 하면
툴이 짧은 에러를 돌려준다. 쓸 수 있는지는 처음 한 번 실제로 작은 파이썬을 샌드박스 안에서 띄워 본다.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MODE_ENV = "OPENSTEM_ISOLATION"
EXTRA_READ_ENV = "OPENSTEM_ISOLATION_READ"  # 격리 안에서 더 읽게 할 경로(os.pathsep로 구분). 다른 TeX 배포판 등
MODES = ("auto", "off", "require")
PROBE_TIMEOUT = 30.0

REQUIRE_ERROR = (
    "OS isolation is required (OPENSTEM_ISOLATION=require) but is not available on this computer: {reason}. "
    "Ask the user to run `openstem-mcp --doctor`, or to set OPENSTEM_ISOLATION=auto."
)

# 시험이 subprocess.Popen을 가짜로 바꿔 끼워도 실제로 띄워 보게 한다.
_POPEN = subprocess.Popen


class Unavailable(RuntimeError):
    """OPENSTEM_ISOLATION=require인데 격리를 쓸 수 없다. 메시지는 LLM에 그대로 간다(영어, 짧게)."""


def mode() -> str:
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    return raw if raw in MODES else "auto"


@dataclass(frozen=True)
class Spec:
    """한 번의 격리 실행이 무엇에 닿을 수 있는지.

    write: 쓸 수 있는 폴더(작업 폴더). read: 더 읽을 파일·폴더(tectonic 실행 파일 등).
    python: 파이썬 작업자인가(인터프리터·site-packages를 읽게 한다). network: 네트워크를 열어 둘까(tectonic 번들).
    """

    write: tuple[Path, ...] = ()
    read: tuple[Path, ...] = ()
    python: bool = False
    network: bool = False


@dataclass
class Status:
    active: bool
    backend: str = ""  # "bwrap" | "sandbox-exec" | ""
    reason: str = ""  # 꺼진 이유(영어, 짧게)
    extra: dict[str, str] = field(default_factory=dict)


_status: Status | None = None
_status_lock = threading.Lock()


def reset() -> None:
    """다시 감지하게 한다(시험용, 설정을 바꾼 뒤)."""
    global _status, _python_paths_cache
    with _status_lock:
        _status = None
        _python_paths_cache = None


def status() -> Status:
    """격리를 쓸 수 있는지(처음 한 번 감지하고 기억한다). 처음 감지할 때 로그에 한 줄을 남긴다."""
    global _status
    with _status_lock:
        if _status is None:
            _status = _detect()
            if _status.active:
                log.info("OS isolation: on (%s).", _status.backend)
            elif mode() == "off":
                log.info("OS isolation: off (%s=off).", MODE_ENV)
            elif mode() == "require":
                log.warning("OS isolation is required but unavailable (%s); tools that process input will fail.", _status.reason)
            else:
                log.warning("OS isolation: unavailable, running without it (%s).", _status.reason)
        return _status


def enabled() -> bool:
    """이번 실행을 격리해야 하는가. require인데 못 하면 Unavailable."""
    if mode() == "off":
        return False
    st = status()
    if st.active:
        return True
    if mode() == "require":
        raise Unavailable(REQUIRE_ERROR.format(reason=st.reason))
    return False


def wrap(argv: list[str], spec: Spec) -> list[str]:
    """argv를 격리해서 실행하는 명령으로 바꾼다. 격리하지 않을 때(off, auto인데 못 씀)는 그대로 돌려준다."""
    if not enabled():
        return argv
    return _build(status().backend, argv, spec)


def describe() -> str:
    """--doctor용 한 줄(한국어)."""
    m = mode()
    if m == "off":
        return f"꺼짐({MODE_ENV}=off)"
    st = status()
    if st.active:
        return f"켜짐({st.backend}: 홈 폴더 숨김·쓰기는 작업 폴더만·작업자 네트워크 차단, {MODE_ENV}={m})"
    return f"못 씀 → {'툴이 에러를 돌려줌' if m == 'require' else '격리 없이 동작'}: {st.reason}"


# ── 감지 ────────────────────────────────────────────────────────────


def _detect() -> Status:
    if mode() == "off":
        return Status(False, reason=f"{MODE_ENV}=off")
    if sys.platform.startswith("linux"):
        backend = "bwrap"
        if not shutil.which("bwrap"):
            return Status(False, reason="bubblewrap (bwrap) is not installed")
    elif sys.platform == "darwin":
        backend = "sandbox-exec"
        if not Path(_SANDBOX_EXEC).is_file():
            return Status(False, reason="sandbox-exec is not available")
    else:
        return Status(False, reason=f"not supported on {sys.platform} yet")
    return _probe(backend)


def _probe(backend: str) -> Status:
    """작은 파이썬을 실제 작업자와 같은 설정으로 띄워 본다. 네임스페이스 금지(AppArmor)·중첩 샌드박스 등은 여기서 걸린다."""
    box = Path(tempfile.mkdtemp(prefix="openstem-probe-"))
    try:
        code = "import openstem.workers, pathlib; pathlib.Path('ok').write_text('ok')"
        argv = _build(backend, [sys.executable, "-I", "-c", code], Spec(write=(box,), python=True))
        try:
            proc = _POPEN(argv, cwd=box, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _out, err = proc.communicate(timeout=PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return Status(False, reason=f"{backend} did not start within {PROBE_TIMEOUT:.0f} s")
        except OSError as exc:
            return Status(False, reason=f"{backend} could not start ({exc})")
        if proc.returncode != 0 or not (box / "ok").is_file():
            tail = [ln.strip() for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()][-1:]
            return Status(False, reason=f"{backend} failed: " + (tail[0][:200] if tail else f"exit code {proc.returncode}"))
        return Status(True, backend)
    finally:
        shutil.rmtree(box, ignore_errors=True)


# ── 읽을 경로 ────────────────────────────────────────────────────────

_LINUX_SYSTEM = (
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32",
    # /etc는 필요한 것만: 동적 링커, 이름 풀이(tectonic 번들), 인증서, 글꼴 설정, 시간대, 대체 링크, TeX 설정
    "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/alternatives",
    "/etc/resolv.conf", "/etc/hosts", "/etc/host.conf", "/etc/nsswitch.conf", "/etc/gai.conf",
    "/etc/ssl", "/etc/pki", "/etc/ca-certificates", "/etc/crypto-policies",
    "/etc/fonts", "/etc/localtime", "/etc/timezone", "/etc/texmf", "/etc/passwd", "/etc/group",
)  # fmt: skip
_MACOS_SYSTEM = (
    "/System", "/usr", "/bin", "/sbin", "/Library", "/dev", "/private/etc", "/private/var/db/timezone",
    "/private/var/db/dyld", "/opt/homebrew", "/opt/local",
)  # fmt: skip
_CERT_ENVS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

_python_paths_cache: list[str] | None = None


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def python_paths() -> list[str]:
    """작업자(`python -I`)가 읽어야 하는 곳: 인터프리터, 표준 라이브러리, site-packages(편집 설치 경로 포함).

    서버 자신의 sys.path에는 서버를 띄운 폴더가 들어 있을 수 있어서, `-I`로 띄운 파이썬에게 직접 묻는다.
    홈 폴더나 그 위(`/`, `/home`)는 넣지 않는다.
    """
    global _python_paths_cache
    if _python_paths_cache is not None:
        return _python_paths_cache
    paths = [sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix, sys.executable]
    code = "import json, sys; print(json.dumps(sys.path))"
    try:
        proc = _POPEN([sys.executable, "-I", "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _err = proc.communicate(timeout=PROBE_TIMEOUT)
        paths += [p for p in json.loads(out.decode("utf-8")) if isinstance(p, str)]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        paths += sys.path[1:]
    home = os.path.realpath(Path.home())
    kept: list[str] = []
    for p in paths:
        if not p or not os.path.isabs(p) or not os.path.exists(p):
            continue
        real = os.path.realpath(p)
        if any(_under(home, x) for x in (os.path.abspath(p), real)):
            continue  # 홈 폴더 자체나 그 위: 통째로 보이게 하지 않는다
        kept.append(os.path.abspath(p))
    _python_paths_cache = sorted(set(kept))
    return _python_paths_cache


def extra_read_paths() -> list[str]:
    raw = os.environ.get(EXTRA_READ_ENV, "")
    return [p for p in raw.split(os.pathsep) if p.strip() and os.path.isabs(p.strip())]


def _cert_paths() -> list[str]:
    return [v for k in _CERT_ENVS if (v := os.environ.get(k, "")) and os.path.isabs(v)]


def _read_paths(spec: Spec, system: Iterable[str]) -> list[str]:
    paths = list(system) + _cert_paths() + extra_read_paths() + [str(p) for p in spec.read]
    if spec.python:
        paths += python_paths()
    return paths


# ── bwrap ───────────────────────────────────────────────────────────


class _Mounts:
    """bwrap 인자 목록. 경로 중간의 심볼릭 링크는 링크로 다시 만들고, 실제 경로를 붙인다."""

    def __init__(self) -> None:
        self.binds: dict[str, str] = {}  # 경로 → "ro" | "rw"
        self.links: dict[str, str] = {}  # 경로 → 링크 내용

    def _covered(self, path: str, kinds: tuple[str, ...]) -> bool:
        return any(_under(path, b) and k in kinds for b, k in self.binds.items())

    def add(self, path: str, kind: str, depth: int = 0) -> None:
        path = os.path.abspath(path)
        if path == "/" or depth > 20 or not os.path.lexists(path):
            return
        cur = ""
        for part in Path(path).parts[1:]:
            cur = f"{cur}/{part}"
            if os.path.islink(cur):
                if not self._covered(cur, ("ro", "rw")):
                    self.links[cur] = os.readlink(cur)
                rest = os.path.relpath(path, cur)
                target = os.path.realpath(cur)
                self.add(target if rest == "." else os.path.join(target, rest), kind, depth + 1)
                return
        if not os.path.exists(path):
            return
        self.binds[path] = "rw" if kind == "rw" or self.binds.get(path) == "rw" else "ro"

    def args(self) -> list[str]:
        out: list[str] = []
        entries: list[tuple[int, str, list[str]]] = []
        for path, kind in self.binds.items():
            # 이미 붙인 위 폴더 안에 같은 권한(이상)으로 들어 있으면 다시 붙이지 않는다
            parents = [k for b, k in self.binds.items() if b != path and _under(path, b)]
            if (kind == "ro" and parents) or (kind == "rw" and "rw" in parents):
                continue
            flag = "--bind" if kind == "rw" else "--ro-bind"
            entries.append((path.count("/"), path, [flag, path, path]))
        for path, target in self.links.items():
            if any(_under(path, b) for b in self.binds):
                continue  # 붙인 폴더 안의 링크는 이미 보인다
            entries.append((path.count("/"), path, ["--symlink", target, path]))
        for _depth, _path, item in sorted(entries):
            out += item
        return out


def _bwrap_args(argv: list[str], spec: Spec) -> list[str]:
    mounts = _Mounts()
    for p in _read_paths(spec, _LINUX_SYSTEM):
        mounts.add(p, "ro")
    for p in spec.write:
        mounts.add(str(p), "rw")
    args = [shutil.which("bwrap") or "bwrap", "--unshare-all"]
    if spec.network:
        args.append("--share-net")
    args += ["--die-with-parent", "--new-session", "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    args += mounts.args()
    if spec.write:
        args += ["--chdir", str(spec.write[0])]
    return [*args, "--", *argv]


# ── macOS sandbox-exec ──────────────────────────────────────────────

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _sb_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(spec: Spec) -> str:
    """sandbox-exec 프로필(SBPL). 나중 규칙이 앞 규칙보다 우선한다.

    읽기: 모든 파일 읽기를 막은 뒤 시스템·파이썬·캐시·작업 폴더·입력 파일만 연다. 파일 정보(stat)는 어디서나 허용한다
    (경로를 따라가며 상위 폴더를 확인하는 동작 때문에: 내용은 읽지 못한다). 쓰기: 작업 폴더와 /dev의 몇 곳만.
    """
    reads = sorted({os.path.realpath(p) for p in _read_paths(spec, _MACOS_SYSTEM) if os.path.exists(p)})
    writes = sorted({os.path.realpath(str(p)) for p in spec.write})
    lines = [
        "(version 1)",
        "(allow default)",
        '(deny file-read* (subpath "/"))',
        "(allow file-read-metadata)",
        '(allow file-read* (literal "/"))',
    ]
    allowed = [f"(subpath {_sb_quote(p)})" for p in reads + writes]
    lines.append("(allow file-read* " + " ".join(allowed) + ")")
    lines.append('(deny file-write* (subpath "/"))')
    write_rules = [f"(subpath {_sb_quote(p)})" for p in writes]
    write_rules += ['(literal "/dev/null")', '(literal "/dev/tty")', '(literal "/dev/dtracehelper")', '(regex #"^/dev/fd/")']
    lines.append("(allow file-write* " + " ".join(write_rules) + ")")
    if not spec.network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def _build(backend: str, argv: list[str], spec: Spec) -> list[str]:
    if backend == "bwrap":
        return _bwrap_args(argv, spec)
    if backend == "sandbox-exec":
        return [_SANDBOX_EXEC, "-p", macos_profile(spec), *argv]
    return argv


# ── tectonic ────────────────────────────────────────────────────────


def tectonic_cache_dirs() -> list[Path]:
    """tectonic이 TeX 번들을 받아 두는 폴더. 격리 안에서도 쓸 수 있게 붙인다(홈 폴더의 유일한 예외)."""
    configured = os.environ.get("TECTONIC_CACHE_DIR", "").strip()
    if configured:
        return [Path(configured)]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Caches" / "Tectonic"]
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return [base / "Tectonic", base / "tectonic"]  # tectonic 버전에 따라 이름이 다르다


def tectonic_spec(exe: str, workdir: Path) -> Spec:
    """tectonic 한 번: 작업 폴더와 번들 캐시만 쓰고, 실행 파일을 읽고, 번들을 받으려고 네트워크를 연다."""
    caches = tectonic_cache_dirs()
    for cache in caches:
        try:
            cache.mkdir(parents=True, exist_ok=True)  # 없으면 격리 안에서 만들지 못해 매번 다시 받는다
        except OSError:
            pass
    return Spec(write=(workdir, *caches), read=(Path(exe),), network=True)
