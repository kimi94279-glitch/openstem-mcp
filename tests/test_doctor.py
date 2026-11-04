"""openstem-mcp --doctor(doctor.py): 점검 결과, 비밀·경로 가리기, 출력 형식. 네트워크는 쓰지 않는다."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

from openstem import doctor
from openstem.doctor import FAIL, OK, SKIP, WARN, Check

TOKEN = "tok-THIS-MUST-NOT-LEAK-1234567890"
CALENDAR = "https://lms.example.ac.kr/feeds/calendars/user_SECRETFEEDvalue1234567890.ics"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """결과물·설정·캐시 폴더를 tmp_path 안으로 돌리고, LMS 비밀을 넣어 둔다."""
    from openstem.render import latex

    monkeypatch.setenv("OPENSTEM_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("OPENSTEM_INPUT_DIR", raising=False)
    monkeypatch.setenv("OPENSTEM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(latex, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", TOKEN)
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", CALENDAR)
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "성균관대")
    return tmp_path


def test_lms_shows_only_set_or_not(isolated):
    check = doctor.check_lms()
    assert check.status == OK
    assert "토큰 설정됨" in check.detail and "캘린더 주소 설정됨" in check.detail and "주소 안 됨" in check.detail
    assert TOKEN not in check.detail and "SECRETFEED" not in check.detail and "성균관대" not in check.detail


def test_lms_not_configured_is_skipped(monkeypatch):
    for name in ("TOKEN", "CALENDAR_URL", "SCHOOL", "URL", "TYPE"):
        monkeypatch.delenv(f"OPENSTEM_LMS_{name}", raising=False)
    assert doctor.check_lms().status == SKIP


def test_http_secret_reports_presence_not_value(isolated):
    from openstem import remote

    assert "없음" in doctor.check_http_secret().detail
    secret, _ = remote.load_secret()
    check = doctor.check_http_secret()
    assert check.status == OK and "있음" in check.detail and secret not in check.detail


def test_tex_bundle_marker(isolated):
    from openstem.render import latex

    check = doctor.check_tex_bundle()
    assert check.status == WARN and latex.WARMUP_MARKER in check.detail
    (isolated / "cache").mkdir()
    (isolated / "cache" / "warm-0.0.1").write_text("ok\n")
    assert "warm-0.0.1" in doctor.check_tex_bundle().detail  # 예전 버전 표시는 따로 알린다
    latex.mark_warm()
    assert doctor.check_tex_bundle().status == OK


def test_tectonic_missing_and_found(isolated, monkeypatch):
    from openstem.render import latex

    monkeypatch.delenv(latex.TECTONIC_ENV, raising=False)
    monkeypatch.setattr(latex, "_locate_tectonic", lambda: None)
    monkeypatch.setenv("OPENSTEM_NO_DOWNLOAD", "1")
    assert doctor.check_tectonic().status == FAIL
    monkeypatch.delenv("OPENSTEM_NO_DOWNLOAD")
    monkeypatch.setattr(latex.tectonic_setup, "can_download", lambda: True)
    assert doctor.check_tectonic().status == WARN

    # 진짜 실행 파일 대신 "tectonic 0.17.0"을 찍는 파이썬
    fake = isolated / "fake_tectonic.py"
    fake.write_text("print('tectonic 0.17.0')\n")
    monkeypatch.setattr(latex, "_locate_tectonic", lambda: sys.executable)
    real_run = doctor.sandbox.run
    monkeypatch.setattr(doctor.sandbox, "run", lambda args, **kw: real_run([args[0], str(fake)], **kw))
    check = doctor.check_tectonic()
    assert check.status == OK and "tectonic 0.17.0" in check.detail


def test_bundle_server_offline_is_skipped():
    assert doctor.check_bundle_server(offline=True).status == SKIP


def test_bundle_server_results():
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen = {}

    def ok(request, timeout):
        seen.update(method=request.get_method(), timeout=timeout)
        return Response()

    assert doctor.check_bundle_server(False, opener=ok).status == OK
    assert seen == {"method": "HEAD", "timeout": doctor.NETWORK_TIMEOUT}

    def blocked(request, timeout):
        raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

    check = doctor.check_bundle_server(False, opener=blocked)
    assert check.status == FAIL and "403" in check.detail and check.hint

    def http_404(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    assert doctor.check_bundle_server(False, opener=http_404).status == WARN  # 서버까지는 닿았다


def test_folders_are_created_and_writable(isolated):
    out = doctor.check_output_folder()
    mats = doctor.check_materials_folder()
    assert out.status == mats.status == OK
    assert (isolated / "out").is_dir() and (isolated / "out" / "자료").is_dir()
    assert not list((isolated / "out").glob(".openstem-doctor-*"))  # 쓰기 시험 파일은 남지 않는다


def test_unwritable_folder_fails(isolated, monkeypatch):
    monkeypatch.setattr(doctor, "_writable", lambda path: "Permission denied")
    check = doctor.check_output_folder()
    assert check.status == FAIL and "Permission denied" in check.detail and check.hint


def test_workers_report_import_times():
    check = doctor.check_workers(["openstem.workers.units"])
    assert check.status in (OK, WARN), check.detail
    assert "units " in check.detail and "메모리 상한" in check.detail


def test_broken_worker_is_reported(tmp_path):
    check = doctor.check_workers(["openstem.workers.does_not_exist"], timeout=60)
    assert check.status == FAIL and "does_not_exist" in check.detail


def test_redact_paths_user_and_secrets():
    text = "C:\\Users\\Kim\\OpenSTEM · /home/kim/x · user kim · token abc123secret · kimchi"
    out = doctor.redact(text, home="C:\\Users\\Kim", user="kim", secrets=["abc123secret"])
    assert "~\\OpenSTEM" in out and "<사용자>" in out and "<비밀>" in out
    assert "Kim" not in out.replace("kimchi", "") and "abc123secret" not in out
    assert "kimchi" in out  # 단어 일부는 건드리지 않는다


def test_summary_and_json_hide_secrets_and_home(isolated, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_workers", lambda workers: Check("workers", "작업자", OK, f"{TOKEN} at {Path.home()}"))
    code = doctor.main(["--doctor", "--offline"], [])
    out = capsys.readouterr().out
    summary = out.split("이슈에 붙여 넣을 요약", 1)[1]
    assert TOKEN not in summary and "SECRETFEED" not in summary and str(Path.home()) not in summary
    assert "<비밀>" in summary
    assert code in (0, 1)

    doctor.main(["--doctor", "--offline", "--json"], [])
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert data["version"] and {c["id"] for c in data["checks"]} >= {"versions", "tectonic", "lms", "http_secret", "workers"}
    assert TOKEN not in raw and "SECRETFEED" not in raw and str(Path.home()) not in raw
    assert next(c for c in data["checks"] if c["id"] == "bundle_server")["status"] == SKIP


def test_exit_code_follows_failures(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda workers, offline: [Check("a", "a", OK, "x"), Check("b", "b", WARN, "y")])
    assert doctor.main(["--doctor"], []) == 0
    monkeypatch.setattr(doctor, "run_checks", lambda workers, offline: [Check("a", "a", FAIL, "x")])
    assert doctor.main(["--doctor"], []) == 1
    capsys.readouterr()


def test_a_crashing_check_does_not_stop_the_rest(monkeypatch):
    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(doctor, "check_lms", boom)
    monkeypatch.setattr(doctor, "check_workers", lambda workers: Check("workers", "w", OK, ""))
    checks = doctor.run_checks([], offline=True)
    assert any(c.status == FAIL and "unexpected" in c.detail for c in checks)
    assert any(c.id == "http_secret" for c in checks)
