"""sandbox.run_python: 파이썬 작업자를 격리된 서브프로세스 + 타임아웃으로 돌린다."""

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
