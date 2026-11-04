"""외부 입력을 다루는 서브프로세스 실행.

외부 입력(LaTeX 코드, 수식 문자열, SMILES)을 처리하는 프로그램은 전부 여기를 거친다.
서버 프로세스 안에서 직접 처리하지 않고, 별도 프로세스 + 타임아웃으로 격리한다.
- `run()`: 외부 명령(tectonic 등)
- `run_python()`: 파이썬 작업자 모듈(openstem.workers.*). sympy 파싱(eval)·RDKit 처리를 여기서 한다.
쓸 수 있으면 운영체제 샌드박스(isolation.py: Linux bwrap, macOS sandbox-exec) 안에서 돌린다.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openstem import isolation

DEFAULT_TIMEOUT = 20.0
TIMEOUT_ENV = "OPENSTEM_TIMEOUT"


def default_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_TIMEOUT


# 자식 프로세스(작업자·tectonic·pandoc)는 외부 입력을 다루므로 서버의 비밀 환경변수를 물려주지 않는다.
# 프록시·인증서·캐시 폴더(HTTPS_PROXY, SSL_CERT_FILE, HOME, LOCALAPPDATA, XDG_CACHE_HOME 등)는 남긴다:
# tectonic이 TeX 번들을 받으려면 필요하다.
_SECRET_PREFIXES = ("OPENSTEM_LMS_",)
_SECRET_NAME_RE = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|CREDENTIAL|PRIVATE_?KEY", re.IGNORECASE)


def _is_secret(name: str) -> bool:
    return name.upper().startswith(_SECRET_PREFIXES) or bool(_SECRET_NAME_RE.search(name))


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """자식 프로세스에 줄 환경: os.environ에서 비밀로 보이는 변수를 뺀 것 + extra."""
    env = {k: v for k, v in os.environ.items() if not _is_secret(k)}
    env.update(extra or {})
    return env


@dataclass
class RunResult:
    returncode: int | None  # 타임아웃이면 None
    stdout: str
    stderr: str
    timed_out: bool


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def run(
    args: list[str],
    cwd: Path,
    timeout: float | None = None,
    input: bytes | None = None,
    env: dict[str, str] | None = None,
    isolate: isolation.Spec | None = None,
) -> RunResult:
    """`args`를 셸 없이 실행한다. 시간이 넘으면 프로세스를 죽이고 timed_out=True를 돌려준다.

    env를 주지 않으면 `child_env()`(비밀 환경변수를 뺀 서버 환경)를 쓴다.
    isolate를 주면 운영체제 샌드박스 안에서 돌린다(격리를 못 쓰면 auto는 그대로, require는 isolation.Unavailable).

    stdin은 `input`만 넘기고 막는다: MCP stdio 서버에서 자식이 서버의 stdin(프로토콜 스트림)을 읽으면 안 된다.
    """
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    if input is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input
    if isolate is not None:
        args = isolation.wrap(args, isolate)
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            timeout=timeout if timeout is not None else default_timeout(),
            env=env if env is not None else child_env(),
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(None, _decode(exc.stdout), _decode(exc.stderr), True)
    return RunResult(proc.returncode, _decode(proc.stdout), _decode(proc.stderr), False)


class WorkerFailed(RuntimeError):
    """작업자가 시간 초과로 죽었거나 결과 없이 끝났다."""

    def __init__(self, message: str, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


# 작업자는 sympy·RDKit import에만 몇 초가 걸린다. 모듈마다 하나씩 미리 띄워 두면(import를 끝내고 stdin을
# 기다리는 상태) 다음 호출은 계산 시간만 든다. 요청 하나에 프로세스 하나라는 격리는 그대로다.
SPARES_ENABLED = True
_spares: dict[str, subprocess.Popen[bytes]] = {}
_spares_lock = threading.Lock()


# 격리(isolation)를 쓸 때 예비 작업자는 무엇을 처리할지 모르는 채로 미리 뜬다. 그래서 작업자마다 전용 폴더(box)를
# 하나 만들어 그 폴더만 쓰게 하고, 호출이 오면 입력 파일(payload의 INPUT_KEYS)을 box/in에 복사해 넘기고,
# 작업자가 box/work에 만든 파일(일반 파일만, 심볼릭 링크는 버린다)을 호출한 작업 폴더로 옮긴다.
INPUT_KEYS = ("src", "path")  # convert의 src, documents의 path
_boxes: set[Path] = set()
_boxes_lock = threading.Lock()


_mpl_learned: dict[str, bytes] = {}  # 사용자 캐시가 없을 때 첫 격리 작업자가 만든 글꼴 목록(이 서버 프로세스 동안만)
MPL_CACHE_MAX = 4 * 1024 * 1024


def _mpl_seed() -> Path | None:
    """matplotlib 글꼴 목록 캐시가 있는 곳(격리 안 작업자에게 복사해 줘서 매번 글꼴을 다시 훑지 않게 한다)."""
    configured = os.environ.get("MPLCONFIGDIR")
    candidates = [Path(configured)] if configured else []
    if sys.platform.startswith("linux"):
        candidates.append(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "matplotlib")
    candidates.append(Path.home() / ".matplotlib")
    return next((c for c in candidates if any(c.glob("fontlist-*.json"))), None)


def _font_lists(folder: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    with contextlib.suppress(OSError):
        for path in folder.glob("fontlist-*.json"):
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= MPL_CACHE_MAX:
                found[path.name] = path.read_bytes()
    return found


def _new_box() -> Path:
    box = Path(tempfile.mkdtemp(prefix="openstem-box-"))
    (box / "work").mkdir()
    (box / "in").mkdir()
    mpl = box / "mpl"
    mpl.mkdir()
    seed = _mpl_seed()
    for name, data in (_font_lists(seed) if seed is not None else dict(_mpl_learned)).items():
        (mpl / name).write_bytes(data)
    with _boxes_lock:
        _boxes.add(box)
    return box


def _drop_box(box: Path | None) -> None:
    if box is None:
        return
    if not _mpl_learned and _mpl_seed() is None:
        _mpl_learned.update(_font_lists(box / "mpl"))
    shutil.rmtree(box, ignore_errors=True)
    with _boxes_lock:
        _boxes.discard(box)


def _spawn(module: str) -> subprocess.Popen[bytes]:
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    argv = [sys.executable, "-I", "-m", module]
    env = child_env({"MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8"})
    cwd = tempfile.gettempdir()
    box = None
    if isolation.enabled():  # require인데 못 쓰면 isolation.Unavailable
        box = _new_box()
        argv = isolation.wrap(argv, isolation.Spec(write=(box,), python=True, network=False))
        # 홈 폴더는 격리 안에서 보이지 않는다: 캐시·임시 파일은 box 안에 쓰게 한다
        env.update(HOME=str(box), TMPDIR=str(box), MPLCONFIGDIR=str(box / "mpl"), XDG_CACHE_HOME=str(box))
        cwd = str(box)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **kwargs,
        )
    except BaseException:
        _drop_box(box)
        raise
    proc.openstem_box = box  # type: ignore[attr-defined]
    if sys.platform == "win32":
        confine_windows(proc)
    return proc


def _box_of(proc: subprocess.Popen[bytes]) -> Path | None:
    box = getattr(proc, "openstem_box", None)
    return box if isinstance(box, Path) else None


def _box_payload(box: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """입력 파일을 box/in에 복사하고 payload의 경로를 그 복사본으로 바꾼다(작업자는 원본 위치를 보지 못한다)."""
    payload = dict(payload)
    for key in INPUT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value and Path(value).is_file():
            src = Path(value)
            dest = box / "in" / (src.name or "input")
            shutil.copyfile(src, dest)
            payload[key] = str(dest)
    return {**payload, "_cwd": str(box / "work")}


def _collect_box(box: Path, cwd: Path) -> None:
    """작업자가 box/work에 만든 일반 파일을 호출한 작업 폴더로 옮긴다. 링크·폴더·특수 파일은 버린다."""
    with os.scandir(box / "work") as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                shutil.copyfile(entry.path, cwd / entry.name, follow_symlinks=False)


# ── Windows 작업자 메모리 상한(Job Object) ───────────────────────────
# POSIX 작업자는 workers.serve()가 RLIMIT_AS를 스스로 건다. Windows에는 그것이 없어서 서버가 작업자를 띄운 직후
# Job Object 하나에 넣는다: 프로세스마다 커밋 메모리 상한(JOB_OBJECT_LIMIT_PROCESS_MEMORY, 넘으면 할당 실패 →
# 파이썬 MemoryError → "too large to compute")과, 서버가 끝나 job 핸들이 닫히면 작업자도 같이 끝나게 하는
# KILL_ON_JOB_CLOSE. job은 서버 프로세스에 하나만 만들어 끝까지 연다(닫으면 작업자가 다 죽는다).
# 실패하면(중첩 job을 못 쓰는 Windows 7 등) 상한 없이 그대로 돌리고 경고를 한 번만 남긴다.

log = logging.getLogger(__name__)

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


def job_limits(memory_mb: int) -> tuple[int, int]:
    """(LimitFlags, ProcessMemoryLimit 바이트). memory_mb가 0 이하면 메모리 상한 없이 KILL_ON_JOB_CLOSE만."""
    if memory_mb <= 0:
        return JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0
    return JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY, memory_mb * 1024 * 1024


class _Win32Job:
    """ctypes로 Job Object를 만들고 프로세스를 넣는다. Windows에서만 만든다(시험은 가짜로 바꿔 끼운다)."""

    def __init__(self, memory_mb: int) -> None:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]  # fmt: skip

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._ctypes = ctypes
        self._kernel32 = kernel32
        self._info_type = JOBOBJECT_EXTENDED_LIMIT_INFORMATION

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        flags, limit = job_limits(memory_mb)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = flags
        info.ProcessMemoryLimit = limit
        if not kernel32.SetInformationJobObject(
            handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, proc: subprocess.Popen[bytes]) -> None:
        ctypes = self._ctypes
        if not self._kernel32.AssignProcessToJobObject(self.handle, int(proc._handle)):  # type: ignore[attr-defined]
            raise ctypes.WinError(ctypes.get_last_error())

    def limits(self) -> tuple[int, int]:
        """(LimitFlags, ProcessMemoryLimit) — 시험·진단용으로 job에 실제로 걸린 값을 읽는다."""
        ctypes = self._ctypes
        info = self._info_type()
        if not self._kernel32.QueryInformationJobObject(
            self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.BasicLimitInformation.LimitFlags, info.ProcessMemoryLimit


_job: Any = None  # 서버에 하나. 만들지 못했으면 False(다시 시도하지 않는다)
_job_lock = threading.Lock()
_job_factory: Any = _Win32Job


def confine_windows(proc: subprocess.Popen[bytes]) -> bool:
    """작업자를 메모리 상한 job에 넣는다. 넣었으면 True. 실패하면 경고 한 줄(처음 한 번)만 남기고 False."""
    global _job
    # openstem.workers 패키지는 가벼운 설정 함수만 쓴다(무거운 라이브러리는 각 작업자 모듈이 import한다).
    from openstem.workers import memory_limit_mb

    with _job_lock:
        if _job is None:
            try:
                _job = _job_factory(memory_limit_mb())
            except (OSError, AttributeError, ValueError) as exc:
                _job = False
                log.warning("Worker memory limit is off: could not create a Windows job object (%s).", exc)
        job = _job
    if job is False:
        return False
    try:
        job.assign(proc)
    except (OSError, AttributeError, ValueError) as exc:
        with _job_lock:
            if _job is job:
                _job = False  # 같은 경고를 작업자마다 남기지 않는다
        log.warning("Worker memory limit is off: could not put the worker into the job object (%s).", exc)
        return False
    return True


def _take_worker(module: str) -> subprocess.Popen[bytes]:
    """미리 띄워 둔 작업자를 꺼내고, 다음 호출에 쓸 작업자를 하나 더 띄운다."""
    isolated = isolation.enabled()  # require인데 못 쓰면 여기서 isolation.Unavailable
    with _spares_lock:
        spare = _spares.pop(module, None)
        if SPARES_ENABLED:
            _spares[module] = _spawn(module)
    # 설정이 바뀌기 전에 띄운 예비 작업자(격리 여부가 다름)나 이미 끝난 작업자는 버린다
    if spare is not None and spare.poll() is None and (_box_of(spare) is not None) == isolated:
        return spare
    if spare is not None:
        spare.kill()
        with contextlib.suppress(Exception):
            spare.communicate(timeout=5)
        _drop_box(_box_of(spare))
    return _spawn(module)


@atexit.register
def _stop_spares() -> None:
    with _spares_lock:
        for proc in _spares.values():
            proc.kill()
            with contextlib.suppress(Exception):
                proc.communicate(timeout=5)
            _drop_box(_box_of(proc))
        _spares.clear()
    with _boxes_lock:
        left = list(_boxes)
    for box in left:  # --doctor처럼 띄웠다 끝낸 작업자의 폴더
        _drop_box(box)


# 작업자는 import를 마치면 이 줄을 먼저 쓴다. import 시간(처음 실행이면 .pyc 만들기·글꼴 캐시까지 수십 초)은
# 입력 처리 타임아웃과 따로 센다: 외부 입력을 다루는 시간만 timeout(기본 20초)으로 제한한다.
READY_LINE = b"OPENSTEM-READY"
IMPORT_TIMEOUT = 180.0


def _wait_ready(proc: subprocess.Popen[bytes], timeout: float) -> bool | None:
    """준비 신호를 기다린다. True: 준비됨, False: 신호 없이 끝남(import 실패), None: 시간 초과.

    신호는 한 번만 읽을 수 있으므로, 읽은 결과를 프로세스에 적어 둔다(warm_workers가 먼저 읽은 예비 작업자).
    """
    if getattr(proc, "openstem_ready", False):
        return True
    box: dict[str, bytes] = {}
    reader = threading.Thread(target=lambda: box.setdefault("line", proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        return None
    proc.openstem_ready = box["line"].strip() == READY_LINE  # type: ignore[attr-defined]
    return proc.openstem_ready  # type: ignore[attr-defined]


def warm_workers(modules: list[str]) -> None:
    """작업자를 한 번씩 띄워 import까지만 해 둔다(.pyc·글꼴 캐시를 미리 만든다). 준비된 것은 예비로 남긴다."""
    for module in modules:
        try:
            proc = _spawn(module)
        except isolation.Unavailable:
            return  # require인데 격리를 못 쓴다: 툴을 부를 때 에러를 돌려준다(로그는 isolation이 남겼다)
        if _wait_ready(proc, IMPORT_TIMEOUT) and SPARES_ENABLED:
            with _spares_lock:
                if module not in _spares:
                    _spares[module] = proc
                    continue
        proc.kill()
        proc.communicate()
        _drop_box(_box_of(proc))


def worker_file(cwd: Path, name: Any) -> Path:
    """작업자가 결과에 적은 파일 이름을 작업 폴더 안의 경로로 바꾼다. 폴더 밖을 가리키면 거절한다."""
    path = (cwd / str(name)).resolve()
    if not isinstance(name, str) or path.parent != cwd.resolve() or not path.is_file():
        raise WorkerFailed("The worker returned an invalid file name.")
    return path


def run_python(module: str, payload: dict[str, Any], cwd: Path, timeout: float | None = None) -> dict[str, Any]:
    """`python -I -m <module>` 작업자에게 payload를 맡긴다. payload는 stdin JSON, 결과는 stdout 마지막 줄 JSON.

    -I(격리 모드): 현재 폴더·PYTHON* 환경변수·사용자 site-packages를 보지 않는다.
    작업자는 payload의 `_cwd`(=cwd)로 옮겨 가서 일한다. 규약은 openstem.workers.serve()가 맞춘다.
    운영체제 격리를 쓰면 작업자는 자기 box/work에서 일하고, 끝나면 만든 파일을 cwd로 옮긴다(위 INPUT_KEYS 설명).
    OPENSTEM_ISOLATION=require인데 격리를 못 쓰면 WorkerFailed(짧은 안내).
    """
    timeout = timeout if timeout is not None else default_timeout()
    try:
        proc = _take_worker(module)
    except isolation.Unavailable as exc:
        raise WorkerFailed(str(exc)) from None
    box = _box_of(proc)
    try:
        return _talk(proc, box, payload, cwd, timeout)
    finally:
        _drop_box(box)


def _talk(proc: subprocess.Popen[bytes], box: Path | None, payload: dict[str, Any], cwd: Path, timeout: float) -> dict[str, Any]:
    ready = _wait_ready(proc, IMPORT_TIMEOUT)
    if ready is None:
        proc.kill()
        proc.communicate()
        raise WorkerFailed(f"Worker startup (loading libraries) did not finish within {IMPORT_TIMEOUT:.0f} s.", timed_out=True)
    sent = _box_payload(box, payload) if box is not None else {**payload, "_cwd": str(cwd)}
    data = json.dumps(sent).encode("utf-8")
    try:
        stdout, stderr = proc.communicate(data if ready else None, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise WorkerFailed(f"Timed out ({timeout:.0f} s)", timed_out=True) from None
    lines = _decode(stdout).strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError):
        tail = [ln for ln in _decode(stderr).strip().splitlines() if ln.strip()][-3:]
        raise WorkerFailed("The worker exited without a result: " + " / ".join(tail or [f"exit code {proc.returncode}"])) from None
    if not isinstance(result, dict):
        raise WorkerFailed("The worker returned a malformed result.")
    if box is not None:
        _collect_box(box, cwd)
    return result
