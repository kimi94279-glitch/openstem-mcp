"""sandbox.run_python: 파이썬 작업자를 격리된 서브프로세스 + 타임아웃으로 돌린다."""

import sys
from pathlib import Path
from typing import ClassVar

import pytest

from openstem import sandbox

PAYLOAD = {"smiles": "CCO", "kind": "molecule", "legend": None, "show_atom_numbers": False}


def test_worker_returns_json_and_writes_into_cwd(tmp_path):
    result = sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path)
    assert result["ok"] is True and result["formula"] == "C2H6O"
    assert (tmp_path / result["pdf"]).stat().st_size > 0


def test_worker_ignores_modules_planted_in_cwd(tmp_path):
    """-I(격리 모드)라서 작업 폴더에 같은 이름의 모듈을 두어도 가로채지 못한다."""
    (tmp_path / "rdkit.py").write_text("raise SystemExit('hijacked')\n", encoding="utf-8")
    (tmp_path / "json.py").write_text("raise SystemExit('hijacked')\n", encoding="utf-8")
    assert sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path)["ok"] is True


def test_worker_timeout_is_reported(tmp_path):
    """타임아웃은 import가 끝난 뒤 입력 처리 시간만 센다: 오래 걸리는 적분을 0.5초 안에 끊는다."""
    slow = {"op": "integrate", "expr": "exp(sin(x)**3)*log(x)**5", "latex": False}
    with pytest.raises(sandbox.WorkerFailed) as info:
        sandbox.run_python("openstem.workers.verify", slow, tmp_path, timeout=0.5)
    assert info.value.timed_out


def test_import_time_does_not_count_against_timeout(tmp_path, monkeypatch):
    """미리 띄운 작업자 없이(새로 import) 짧은 타임아웃이어도, 가벼운 입력은 끝난다."""
    monkeypatch.setattr(sandbox, "SPARES_ENABLED", False)
    sandbox._stop_spares()
    result = sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path, timeout=3)
    assert result["ok"] is True


def test_warm_workers_leaves_a_ready_spare(tmp_path):
    sandbox._stop_spares()
    sandbox.warm_workers(["openstem.workers.molecule"])
    spare = sandbox._spares["openstem.workers.molecule"]
    assert spare.poll() is None
    assert sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path)["ok"] is True
    assert spare.returncode is not None


def test_worker_without_result_is_reported(tmp_path):
    with pytest.raises(sandbox.WorkerFailed, match="without a result") as info:
        sandbox.run_python("openstem.workers.does_not_exist", {}, tmp_path)
    assert not info.value.timed_out


def test_spare_worker_is_reused_and_works_in_each_cwd(tmp_path):
    """두 번째 호출은 미리 띄워 둔 작업자를 쓴다. 그래도 각자 받은 폴더에 파일을 쓴다."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    sandbox.run_python("openstem.workers.molecule", PAYLOAD, first)
    files_in_first = sorted(first.iterdir())
    spare = sandbox._spares["openstem.workers.molecule"]
    result = sandbox.run_python("openstem.workers.molecule", PAYLOAD, second)
    assert spare.returncode is not None  # 미리 띄운 그 작업자가 일하고 끝났다
    assert (second / result["pdf"]).exists() and sorted(first.iterdir()) == files_in_first


def test_worker_input_error_comes_back_as_errors(tmp_path):
    result = sandbox.run_python("openstem.workers.molecule", dict(PAYLOAD, smiles="C1CC"), tmp_path)
    assert result["ok"] is False and result["errors"] and result["hint"]


# ── 자식 프로세스 환경: 비밀 환경변수를 물려주지 않는다 ─────────────────

SECRETS = {
    "OPENSTEM_LMS_TOKEN": "tok-123",
    "OPENSTEM_LMS_CALENDAR_URL": "https://lms.example/feed?secret=1",
    "OPENSTEM_LMS_SCHOOL": "example",
    "GITHUB_TOKEN": "ghp_x",
    "MY_API_KEY": "k",
    "AWS_SECRET_ACCESS_KEY": "s",
    "DB_PASSWORD": "p",
    "SOME_CREDENTIALS_FILE": "/c",
}
KEPT = {
    "HTTPS_PROXY": "http://proxy:3128",
    "SSL_CERT_FILE": "/etc/ssl/cert.pem",
    "XDG_CACHE_HOME": "/tmp/cache",
    "LOCALAPPDATA": "C:/Users/me/AppData/Local",
    "OPENSTEM_TIMEOUT": "20",
}


def test_child_env_drops_secrets_and_keeps_proxy_and_cache(monkeypatch):
    for key, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(key, value)
    env = sandbox.child_env({"MPLBACKEND": "Agg"})
    assert not set(SECRETS) & set(env)
    assert all(env[key] == value for key, value in KEPT.items())
    assert env["MPLBACKEND"] == "Agg"
    assert "HOME" in env or "USERPROFILE" in env


def test_external_commands_do_not_see_secrets(monkeypatch, tmp_path):
    import json

    for key, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(key, value)
    res = sandbox.run([sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"], cwd=tmp_path)
    seen = json.loads(res.stdout)
    assert not set(SECRETS) & set(seen) and seen["HTTPS_PROXY"] == KEPT["HTTPS_PROXY"]


def test_workers_do_not_see_secrets(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured.update(kwargs)

    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sandbox.subprocess, "Popen", FakePopen)
    sandbox._spawn("openstem.workers.verify")
    assert not set(SECRETS) & set(captured["env"])
    assert captured["env"]["MPLBACKEND"] == "Agg"


# ── 작업자 메모리 상한(POSIX RLIMIT_AS) ────────────────────────────────

WORKERS = ["convert", "documents", "molecule", "plot", "plotdata", "units", "verify"]


def _worker_pid(pid: int) -> int:
    """운영체제 격리(bwrap)를 쓰면 Popen의 pid는 bwrap이고 작업자는 그 자손이다. 가장 깊은 자손을 찾는다."""
    while True:
        children = []
        for task in Path(f"/proc/{pid}/task").iterdir():
            children += (task / "children").read_text().split()
        if not children:
            return pid
        pid = int(children[0])


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS는 Linux에서만 확실히 지켜진다(macOS는 무시, Windows는 없음)")
def test_all_workers_start_under_the_memory_limit():
    """기본 상한 아래에서 작업자 7종이 모두 무거운 import를 마치고 준비 신호를 보낸다. 상한이 실제로 걸려 있다."""
    from openstem.workers import DEFAULT_MEMORY_MB

    for name in WORKERS:
        proc = sandbox._spawn(f"openstem.workers.{name}")
        try:
            assert sandbox._wait_ready(proc, sandbox.IMPORT_TIMEOUT), name
            limits = Path(f"/proc/{_worker_pid(proc.pid)}/limits").read_text()
            line = next(ln for ln in limits.splitlines() if ln.startswith("Max address space"))
            assert str(DEFAULT_MEMORY_MB * 1024 * 1024) in line, (name, line)
        finally:
            proc.kill()
            proc.communicate()


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS는 Linux에서만 확실히 지켜진다(macOS는 무시, Windows는 없음)")
def test_memory_blowup_is_reported_as_too_large(monkeypatch):
    """2^2^2^2^2^2는 수식 검사를 통과하고 메모리를 계속 키운다. 상한에 걸리면 짧은 에러로 돌아온다."""
    from openstem.tools.verify import verify_math

    monkeypatch.setenv("OPENSTEM_WORKER_MEMORY_MB", "700")
    monkeypatch.setattr(sandbox, "SPARES_ENABLED", False)
    sandbox._stop_spares()  # 예비 작업자는 이전 환경(기본 상한)으로 떠 있다
    result = verify_math("simplify", "2^2^2^2^2^2")
    assert not result.ok
    assert "too large to compute" in " ".join(result.data["errors"])


def test_memory_limit_setting(monkeypatch):
    from openstem import workers

    monkeypatch.setenv("OPENSTEM_WORKER_MEMORY_MB", "1234")
    assert workers.memory_limit_mb() == 1234
    monkeypatch.setenv("OPENSTEM_WORKER_MEMORY_MB", "0")
    assert workers.memory_limit_mb() == 0 and workers.limit_memory() is None
    monkeypatch.setenv("OPENSTEM_WORKER_MEMORY_MB", "lots")
    assert workers.memory_limit_mb() == workers.DEFAULT_MEMORY_MB


# ── Windows 작업자 메모리 상한(Job Object) ─────────────────────────────


class FakeJob:
    created: ClassVar[list[int]] = []

    def __init__(self, memory_mb: int) -> None:
        FakeJob.created.append(memory_mb)
        self.assigned: list[object] = []

    def assign(self, proc) -> None:
        self.assigned.append(proc)


def test_job_limits_flags():
    flags, limit = sandbox.job_limits(3072)
    assert flags & sandbox.JOB_OBJECT_LIMIT_PROCESS_MEMORY and flags & sandbox.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert limit == 3072 * 1024 * 1024
    flags, limit = sandbox.job_limits(0)  # 상한을 끈 경우: 서버가 끝나면 작업자도 끝나는 것만 남긴다
    assert flags == sandbox.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and limit == 0


def test_confine_windows_makes_one_job_and_reuses_it(monkeypatch):
    FakeJob.created = []
    monkeypatch.setenv("OPENSTEM_WORKER_MEMORY_MB", "1500")
    monkeypatch.setattr(sandbox, "_job", None)
    monkeypatch.setattr(sandbox, "_job_factory", FakeJob)
    assert sandbox.confine_windows("proc-1") and sandbox.confine_windows("proc-2")
    assert FakeJob.created == [1500]
    assert sandbox._job.assigned == ["proc-1", "proc-2"]


def test_confine_windows_failure_is_quiet_and_logged_once(monkeypatch, caplog):
    calls = []

    def broken(memory_mb: int):
        calls.append(memory_mb)
        raise OSError("nested jobs not supported")

    monkeypatch.setattr(sandbox, "_job", None)
    monkeypatch.setattr(sandbox, "_job_factory", broken)
    with caplog.at_level("WARNING", logger="openstem.sandbox"):
        assert sandbox.confine_windows("proc-1") is False
        assert sandbox.confine_windows("proc-2") is False
    assert len(calls) == 1
    assert len([r for r in caplog.records if "memory limit is off" in r.getMessage()]) == 1


def test_confine_windows_assign_failure_turns_the_limit_off(monkeypatch, caplog):
    class RefusingJob(FakeJob):
        def assign(self, proc) -> None:
            raise OSError("access denied")

    monkeypatch.setattr(sandbox, "_job", None)
    monkeypatch.setattr(sandbox, "_job_factory", RefusingJob)
    with caplog.at_level("WARNING", logger="openstem.sandbox"):
        assert sandbox.confine_windows("proc-1") is False
        assert sandbox.confine_windows("proc-2") is False
    assert len([r for r in caplog.records if "memory limit is off" in r.getMessage()]) == 1


def test_spawn_confines_workers_only_on_windows(monkeypatch):
    confined = []
    monkeypatch.setattr(sandbox, "confine_windows", confined.append)

    class FakePopen:
        def __init__(self, args, **kwargs):
            pass

    monkeypatch.setattr(sandbox.subprocess, "Popen", FakePopen)
    sandbox._spawn("openstem.workers.verify")
    assert len(confined) == (1 if sys.platform == "win32" else 0)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object는 Windows에만 있다")
def test_windows_worker_runs_inside_the_memory_job(monkeypatch):
    """실제 job을 만들고 작업자(예비 포함)를 넣는다. job에 상한이 걸려 있고 작업자가 그 안에서 일한다."""
    import ctypes
    from ctypes import wintypes

    from openstem.workers import DEFAULT_MEMORY_MB

    monkeypatch.delenv("OPENSTEM_WORKER_MEMORY_MB", raising=False)
    monkeypatch.setattr(sandbox, "_job", None)
    monkeypatch.setattr(sandbox, "SPARES_ENABLED", False)
    proc = sandbox._spawn("openstem.workers.verify")
    try:
        job = sandbox._job
        assert job, "job object was not created"
        flags, limit = job.limits()
        assert flags & sandbox.JOB_OBJECT_LIMIT_PROCESS_MEMORY and flags & sandbox.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert limit == DEFAULT_MEMORY_MB * 1024 * 1024
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        inside = wintypes.BOOL()
        assert kernel32.IsProcessInJob(int(proc._handle), job.handle, ctypes.byref(inside))
        assert inside.value
        assert sandbox._wait_ready(proc, sandbox.IMPORT_TIMEOUT)
    finally:
        proc.kill()
        proc.communicate()


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object는 Windows에만 있다")
def test_windows_memory_blowup_is_reported_as_too_large(monkeypatch):
    """Windows에서도 폭주 입력이 PC 메모리를 다 쓰기 전에 짧은 에러로 돌아온다(job의 프로세스 커밋 상한)."""
    from openstem.tools.verify import verify_math

    monkeypatch.setenv("OPENSTEM_WORKER_MEMORY_MB", "900")
    monkeypatch.setattr(sandbox, "_job", None)  # 새 상한으로 job을 다시 만든다(예전 job은 그대로 열어 둔다)
    monkeypatch.setattr(sandbox, "SPARES_ENABLED", False)
    sandbox._stop_spares()
    result = verify_math("simplify", "2^2^2^2^2^2")
    assert not result.ok
    assert "too large to compute" in " ".join(result.data["errors"])
