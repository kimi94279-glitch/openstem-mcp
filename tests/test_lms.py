"""lms 툴: 가짜 Canvas·Moodle 서버로 모든 action, 설정, 토큰 보호, 내려받기 안전장치를 본다.

학교 목록(이름·포털 → API 주소), LearningX 포털의 419, 한국어 에러, 401의 두 뜻, 캘린더 모드(.ics)도 본다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fake_lms import SEOUL, FakeCanvas, FakeMoodle, FakePortal, FakeStorage, fold

from openstem.lms import client as lms_client
from openstem.lms import ical, schools
from openstem.lms.common import due_in, html_to_text, iso, safe_name
from openstem.tools import lms as lms_tool

ENVS = ("OPENSTEM_LMS_URL", "OPENSTEM_LMS_TOKEN", "OPENSTEM_LMS_TYPE", "OPENSTEM_LMS_SCHOOL",
        "OPENSTEM_LMS_CALENDAR_URL")
SECRETS = (FakeCanvas.TOKEN, FakeMoodle.TOKEN, FakeCanvas.FEED_KEY, FakeMoodle.FEED_KEY)


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENSTEM_INPUT_DIR", str(tmp_path / "materials"))
    monkeypatch.setenv("OPENSTEM_OUTPUT_DIR", str(tmp_path / "out"))
    lms_tool.reset_cache()
    yield
    lms_tool.reset_cache()


@pytest.fixture
def storage():
    with FakeStorage() as server:
        yield server


@pytest.fixture
def canvas(monkeypatch, storage):
    with FakeCanvas(storage) as server:
        monkeypatch.setenv("OPENSTEM_LMS_URL", server.url)
        monkeypatch.setenv("OPENSTEM_LMS_TOKEN", FakeCanvas.TOKEN)
        yield server


@pytest.fixture
def moodle(monkeypatch, storage):
    with FakeMoodle(storage) as server:
        monkeypatch.setenv("OPENSTEM_LMS_URL", server.url)
        monkeypatch.setenv("OPENSTEM_LMS_TOKEN", FakeMoodle.TOKEN)
        yield server


def run(*args, **kwargs):
    """툴을 부르고, 결과 어디에도 토큰·캘린더 주소의 비밀이 없는지 늘 확인한다."""
    result = lms_tool.lms(*args, **kwargs)
    text = json.dumps(result.data, ensure_ascii=False)
    assert not any(secret in text for secret in SECRETS), text
    assert "/feeds/calendars/" not in text and "authtoken=M" not in text, text
    return result


def ok(*args, **kwargs):
    result = run(*args, **kwargs)
    assert result.ok, result.data
    return result.data


def fail(*args, **kwargs):
    result = run(*args, **kwargs)
    assert not result.ok, result.data
    return result.data


def materials(tmp_path: Path) -> Path:
    return tmp_path / "materials"


# ── 설정 ──


def test_missing_config_explains_how_to_connect():
    data = fail("courses")
    assert "not connected" in data["errors"][0]
    assert "New Access Token" in data["hint"] and "Security keys" in data["hint"]
    assert "password" in data["hint"]


def test_placeholder_config_is_unset(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_URL", "${user_config.lms_url}")
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "${user_config.lms_token}")
    monkeypatch.setenv("OPENSTEM_LMS_TYPE", "${user_config.lms_type}")
    data = fail("deadlines")
    assert "not connected" in data["errors"][0]


def test_plain_http_and_credentials_in_url_refused(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "abc123")
    monkeypatch.setenv("OPENSTEM_LMS_URL", "http://canvas.school.ac.kr")
    assert "https://" in fail("courses")["errors"][0]
    monkeypatch.setenv("OPENSTEM_LMS_URL", "https://user:pw@canvas.school.ac.kr")
    assert "user name or password" in fail("courses")["errors"][0]
    monkeypatch.setenv("OPENSTEM_LMS_URL", "https://canvas.school.ac.kr")
    monkeypatch.setenv("OPENSTEM_LMS_TYPE", "blackboard")
    assert "canvas" in fail("courses")["errors"][0]


def test_url_normalization():
    assert lms_client.normalize_url("canvas.school.ac.kr/login/canvas") == "https://canvas.school.ac.kr"
    assert lms_client.normalize_url("https://lms.school.ac.kr/moodle/login/index.php") == "https://lms.school.ac.kr/moodle"
    assert lms_client.normalize_url("https://lms.school.ac.kr/moodle/my/") == "https://lms.school.ac.kr/moodle"
    assert lms_client.normalize_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"


def test_bad_action_and_days(canvas):
    assert "action" in fail("submit")["errors"][0]
    assert "days" in fail("deadlines", days=0)["errors"][0]
    assert "days" in fail("deadlines", days=121)["errors"][0]


# ── Canvas ──


def test_canvas_courses_autodetect_and_pagination(canvas, storage):
    data = ok("courses")
    assert data["lms"] == "canvas"
    assert [c["id"] for c in data["courses"]] == [101, 102, 103]  # 2쪽까지 따라감
    assert data["courses"][0]["term"] == "2026-2"
    api = [r for r in canvas.requests if r.path.startswith("/api/")]
    assert all(r.headers.get("authorization") == f"Bearer {FakeCanvas.TOKEN}" for r in api)
    assert not storage.requests


def test_canvas_pagination_never_follows_other_origin(canvas, storage):
    canvas.next_link = f"{storage.url}/api/v1/courses?page=2"
    data = ok("courses")
    assert [c["id"] for c in data["courses"]] == [101, 102]
    assert not storage.requests  # 다른 호스트로는 한 번도 가지 않았다


def test_canvas_wrong_token(canvas, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "wrong-token-000")
    data = fail("courses")
    assert "rejected the token" in data["errors"][0]
    assert "New Access Token" in data["hint"]


def test_canvas_deadlines(canvas):
    data = ok("deadlines", days=14)
    titles = [d["title"] for d in data["deadlines"]]
    assert titles == ["지난 과제(미제출)", "과제 3: 운동량", "퀴즈 2"]  # 제출한 지난 과제·공지·30일 뒤는 빠짐
    overdue, hw, quiz = data["deadlines"]
    assert overdue["due_in"] == "overdue"
    assert hw["due_in"].startswith("2d ") and hw["course"] == "일반물리학 1"
    assert hw["url"].startswith(canvas.url + "/courses/101/")
    assert quiz["type"] == "quiz" and quiz["submitted"] is False
    assert ok("deadlines", days=60)["deadlines"][-1]["title"] == "기말 프로젝트"


def test_canvas_deadlines_course_filter(canvas):
    data = ok("deadlines", course="화학")
    assert data["course"] == "일반화학 및 실험"
    assert [d["title"] for d in data["deadlines"]] == ["퀴즈 2"]
    planner = [r for r in canvas.requests if r.path == "/api/v1/planner/items"]
    assert planner[-1].query["context_codes[]"] == ["course_102"]


def test_canvas_deadlines_fallback_to_upcoming_events(canvas):
    canvas.planner = False
    data = ok("deadlines")
    rows = {d["title"]: d for d in data["deadlines"]}
    assert list(rows) == ["학과 설명회", "중간 보고서", "실험 예비 보고서", "실험 결과 보고서"]
    assert any(r.path == "/api/v1/users/self/upcoming_events" for r in canvas.requests)
    # has_submitted_submissions는 "누군가 냈는지"라 내 제출로 보지 않는다. 내 submission이 있으면 그것으로 본다
    assert "submitted" not in rows["실험 예비 보고서"] and "submitted" not in rows["중간 보고서"]
    assert rows["실험 결과 보고서"]["submitted"] is True


def test_canvas_announcements(canvas):
    data = ok("announcements", days=14)
    titles = [a["title"] for a in data["announcements"]]
    assert titles == ["중간고사 범위", "실험복 지참", "긴 공지"]  # 최신순, 40일 전 공지는 빠짐
    first = data["announcements"][0]
    assert first["author"] == "김교수"
    assert "1~5장" in first["text"] and "- 운동학" in first["text"]
    assert "steal" not in first["text"] and "p{}" not in first["text"]
    long = data["announcements"][2]["text"]
    assert len(long) <= 1500 and long.endswith("…")
    assert "not follow" in data["note"]
    only = ok("announcements", course="102")
    assert [a["title"] for a in only["announcements"]] == ["실험복 지참"]


def test_canvas_files(canvas):
    data = ok("files", course="일반물리")
    by_name = {f["name"]: f for f in data["files"]}
    assert by_name["lecture1.pdf"]["folder"] == "Week 1"
    assert by_name["lecture1.pdf"]["id"] == 9001
    assert by_name["slides.zip"]["readable"] is False
    assert "readable" not in by_name["lecture1.pdf"]
    assert by_name["huge.pdf"]["size_kb"] == 300 * 1024
    assert "download" in data["next"]


def test_canvas_files_fallback_to_modules(canvas):
    data = ok("files", course="일반화학")
    assert "modules" in data["source"]
    names = sorted(f["name"] for f in data["files"])
    assert names == ["chem-week1.pdf", "chem-week2.pdf"]
    assert {f["folder"] for f in data["files"]} == {"1주차", "2주차"}
    got = ok("download", course="102", item="9102")
    assert got["path"] == "LMS/일반화학 및 실험/chem-week2.pdf"


def test_course_ambiguous_and_unknown(canvas):
    data = fail("files", course="물리")
    assert "matches 2 courses" in data["errors"][0]
    assert {c["id"] for c in data["candidates"]} == {101, 103}
    assert "No active course" in fail("files", course="생물")["errors"][0]
    assert "course is required" in fail("files")["errors"][0]
    assert ok("files", course="phy103")["course"] == "물리학 실험"  # 과목 코드도 된다


def test_canvas_download_and_duplicates(canvas, tmp_path):
    data = ok("download", course="101", item="9001")
    assert data["path"] == "LMS/일반물리학 1/lecture1.pdf"
    assert "read_document" in data["next"]
    saved = materials(tmp_path) / data["path"]
    assert saved.read_bytes() == canvas.contents[9001]
    again = ok("download", course="101", item="9001")
    assert again["path"] == data["path"] and "Already" in again["note"]
    canvas.contents[9001] = b"%PDF-1.4 lecture one, revised"
    third = ok("download", course="101", item="9001")
    assert third["path"] == "LMS/일반물리학 1/lecture1 (2).pdf"
    assert not list(saved.parent.glob("*.part"))


def test_canvas_download_sanitizes_names(canvas, tmp_path):
    evil = ok("download", course="101", item="9004")
    assert evil["path"] == "LMS/일반물리학 1/evil.pdf"
    con = ok("download", course="101", item="9005")
    assert con["path"] == "LMS/일반물리학 1/_CON.pdf"
    root = materials(tmp_path).resolve()
    for path in root.rglob("*"):
        assert root in path.resolve().parents
    assert not (tmp_path / "evil.pdf").exists()


def test_canvas_download_refuses_bad_type_and_size(canvas, tmp_path, monkeypatch):
    assert ".zip" in fail("download", course="101", item="9003")["errors"][0]
    assert "too large" in fail("download", course="101", item="9006")["errors"][0]
    monkeypatch.setattr(lms_tool, "MAX_DOWNLOAD_BYTES", 100_000)
    assert "too large" in fail("download", course="101", item="9007")["errors"][0]  # 크기를 속인 파일
    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    assert not [p for p in folder.glob("*") if p.is_file()]
    assert "item" in fail("download", course="101")["errors"][0]
    assert "file id" in fail("download", course="101", item="../../etc/passwd")["errors"][0]
    assert "No file" in fail("download", course="101", item="12345")["errors"][0]


def test_canvas_download_redirect_drops_authorization(canvas, storage, tmp_path):
    data = ok("download", course="101", item="9008")
    assert (materials(tmp_path) / data["path"]).read_bytes() == FakeStorage.CONTENT
    hop = [r for r in canvas.requests if r.path == "/files/9008/download"]
    assert hop and hop[0].headers.get("authorization") == f"Bearer {FakeCanvas.TOKEN}"
    assert storage.requests and all("authorization" not in r.headers for r in storage.requests)
    assert all(FakeCanvas.TOKEN not in json.dumps(r.__dict__) for r in storage.requests)


def test_canvas_download_direct_other_host_has_no_token(canvas, storage):
    ok("download", course="101", item="9009")
    assert [r.path for r in storage.requests] == ["/direct/9009.pdf"]
    assert "authorization" not in storage.requests[0].headers


def test_explicit_type_skips_detection(canvas, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TYPE", "canvas")
    ok("courses")
    assert not any(r.path == "/api/v1/users/self" for r in canvas.requests)


def test_detection_is_cached(canvas):
    ok("courses")
    ok("deadlines")
    assert sum(r.path == "/api/v1/users/self" for r in canvas.requests) == 1


# ── Moodle ──


def test_moodle_courses_autodetect(moodle):
    data = ok("courses")
    assert data["lms"] == "moodle"
    assert [c["name"] for c in data["courses"]] == ["공업수학 1", "회로이론"]  # 지난 학기 과목은 빠짐
    rest = [r for r in moodle.requests if r.path == "/webservice/rest/server.php"]
    assert rest and all(r.method == "POST" and not r.query for r in rest)  # 토큰은 주소가 아니라 본문에


def test_moodle_wrong_token(moodle, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "wrong-token-000")
    data = fail("courses")
    assert "rejected the token" in data["errors"][0]
    assert "Security keys" in data["hint"]


def test_moodle_exception_json(moodle):
    moodle.disabled.add("core_calendar_get_action_events_by_timesort")
    data = fail("deadlines")
    assert "does not allow 'core_calendar_get_action_events_by_timesort'" in data["errors"][0]


def test_moodle_deadlines(moodle):
    data = ok("deadlines", days=14)
    assert [d["title"] for d in data["deadlines"]] == ["지난 과제", "과제 1", "회로 퀴즈"]
    assert data["deadlines"][0]["due_in"] == "overdue"
    assert data["deadlines"][1]["course"] == "공업수학 1"
    assert "token=***" in data["deadlines"][2]["url"]  # 주소 속 토큰도 지운다
    assert [d["title"] for d in ok("deadlines", course="회로")["deadlines"]] == ["회로 퀴즈"]


def test_moodle_announcements(moodle):
    data = ok("announcements", days=14)
    assert [a["title"] for a in data["announcements"]] == ["휴강 안내", "회로 시험"]
    first = data["announcements"][0]
    assert first["text"] == "다음 주 휴강입니다." and first["author"] == "이교수"
    assert first["url"].endswith("/mod/forum/discuss.php?d=7001")
    assert [a["title"] for a in ok("announcements", course="공업", days=60)["announcements"]] == ["휴강 안내", "학기 초 안내"]


def test_moodle_announcements_old_function_fallback(moodle):
    moodle.missing.add("mod_forum_get_forum_discussions")
    data = ok("announcements", course="11")
    assert [a["title"] for a in data["announcements"]] == ["휴강 안내"]


def test_moodle_files_and_download(moodle, storage, tmp_path):
    data = ok("files", course="공업수학")
    names = [f["name"] for f in data["files"]]
    assert "index.html" not in names
    assert names == ["lecture1.pdf", "data.csv", "tool.exe", "outside.pdf"]
    ids = {f["name"]: f["id"] for f in data["files"]}
    csv = next(f for f in data["files"] if f["name"] == "data.csv")
    assert csv["id"].startswith("202.") and csv["folder"] == "1주차 / 실습 폴더 / lab"
    assert len(set(ids.values())) == len(ids)
    assert next(f for f in data["files"] if f["name"] == "tool.exe")["readable"] is False

    got = ok("download", course="11", item=ids["lecture1.pdf"])
    assert got["path"] == "LMS/공업수학 1/lecture1.pdf"
    assert (materials(tmp_path) / got["path"]).read_bytes() == b"content of lecture1.pdf"
    csv_got = ok("download", course="11", item=ids["data.csv"])  # /pluginfile.php → /webservice/pluginfile.php
    assert (materials(tmp_path) / csv_got["path"]).read_bytes() == b"content of data.csv"
    hits = [r for r in moodle.requests if r.path.startswith("/webservice/pluginfile.php/")]
    assert len(hits) == 2 and all(r.q("token") == FakeMoodle.TOKEN for r in hits)

    assert ".exe" in fail("download", course="11", item=ids["tool.exe"])["errors"][0]
    assert "another site" in fail("download", course="11", item=ids["outside.pdf"])["errors"][0]
    assert "No file with id" in fail("download", course="11", item="202.1")["errors"][0]  # 예전 순번 id
    assert not storage.requests


# ── sync: 과목 자료 한 번에 받기 ──


def test_canvas_sync_new_then_up_to_date_then_updated(canvas, tmp_path):
    first = ok("sync", course="101")
    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    got = {d["path"]: d["status"] for d in first["downloaded"]}
    assert got["LMS/일반물리학 1/lecture1.pdf"] == "new"
    assert "LMS/일반물리학 1/과제 안내.hwp" in got and "LMS/일반물리학 1/_CON.pdf" in got
    assert first["skipped"]["not_readable"] == ["slides.zip"] and first["skipped"]["too_large"] == ["huge.pdf"]
    assert "remaining" not in first and "read_document" in first["next"]
    assert (folder / ".openstem-sync.json").is_file() and not list(folder.glob("*.part"))

    second = ok("sync", course="101")
    assert second["downloaded"] == [] and second["up_to_date"] == len(first["downloaded"])
    assert "Nothing new" in second["note"]

    canvas.contents[9001] = b"%PDF-1.4 lecture one, revised"
    canvas.modified[9001] = canvas.file_time + timedelta(hours=1)
    third = ok("sync", course="101")
    assert third["downloaded"] == [{"path": "LMS/일반물리학 1/lecture1.pdf", "status": "updated"}]
    assert (folder / "lecture1.pdf").read_bytes() == b"%PDF-1.4 lecture one, revised"
    assert not (folder / "lecture1 (2).pdf").exists()  # 바뀐 파일은 덮어쓴다


def test_sync_stops_at_the_limit_and_continues(canvas, monkeypatch):
    monkeypatch.setattr(lms_tool, "SYNC_MAX_FILES", 2)
    first = ok("sync", course="101")
    assert len(first["downloaded"]) == 2 and first["remaining"] > 0
    assert 'action="sync", course="101"' in first["next"]
    second = ok("sync", course="101")
    assert {d["path"] for d in first["downloaded"]}.isdisjoint(d["path"] for d in second["downloaded"])
    assert second["up_to_date"] == 2


def test_sync_reuses_a_file_from_download_and_ignores_tampered_state(canvas, tmp_path):
    ok("download", course="101", item="9001")
    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    (folder / ".openstem-sync.json").write_text(
        json.dumps({"version": 1, "files": {"9002": {"name": "../../outside.hwp", "updated": "x", "size": 1}}}),
        encoding="utf-8")
    data = ok("sync", course="101")
    paths = {d["path"] for d in data["downloaded"]}
    assert "LMS/일반물리학 1/lecture1.pdf" not in paths and data["up_to_date"] >= 1  # 같은 내용은 다시 쓰지 않는다
    assert "LMS/일반물리학 1/과제 안내.hwp" in paths
    assert not (materials(tmp_path).parent / "outside.hwp").exists()
    assert not list(folder.glob("lecture1 (*"))


def test_moodle_sync(moodle, storage, tmp_path):
    data = ok("sync", course="11")
    assert {d["path"] for d in data["downloaded"]} == {"LMS/공업수학 1/lecture1.pdf", "LMS/공업수학 1/data.csv"}
    assert data["skipped"]["not_readable"] == ["tool.exe"]
    assert data["failed"][0]["name"] == "outside.pdf" and "another site" in data["failed"][0]["error"]
    assert not storage.requests
    assert ok("sync", course="11")["downloaded"] == []


def test_moodle_explicit_type(moodle, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TYPE", "moodle")
    ok("courses")
    assert not any(r.path == "/api/v1/users/self" for r in moodle.requests)


def test_not_an_lms(storage, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_URL", storage.url)
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "some-token-123")
    assert "Could not recognise" in fail("courses")["errors"][0]


def test_json_size_cap(canvas, monkeypatch):
    monkeypatch.setattr(lms_client, "MAX_JSON_BYTES", 100)
    monkeypatch.setenv("OPENSTEM_LMS_TYPE", "canvas")
    assert "too large" in fail("courses")["errors"][0]


def test_slow_drip_server_is_cut_off(canvas, monkeypatch):
    canvas.slow = True
    monkeypatch.setattr(lms_client, "JSON_DEADLINE", 0.5)
    assert "too slowly" in fail("courses")["errors"][0]


def test_call_budget(canvas, monkeypatch):
    monkeypatch.setattr(lms_client, "CALL_BUDGET", 0.0)
    assert "too slow" in fail("courses")["errors"][0]


def test_korean_domain_origin():
    a = lms_client.origin_of("https://canvas.학교.ac.kr/api")
    b = lms_client.origin_of("https://CANVAS." + "학교".encode("idna").decode() + ".ac.kr:443/x")
    assert a == b


def test_unreachable_lms(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "some-token-123")
    assert "Could not connect" in fail("courses")["errors"][0]


# ── 도우미 ──


def test_safe_name():
    assert safe_name("../../x.pdf") == "x.pdf"
    assert safe_name("..\\..\\x.pdf") == "x.pdf"
    assert safe_name("CON.pdf") == "_CON.pdf"
    assert safe_name("nul") == "_nul"
    assert safe_name("a\x00b‮.pdf") == "ab.pdf"
    assert safe_name("report. . ") == "report"
    assert safe_name("물리 I/II", basename=False) == "물리 I_II"
    assert safe_name("..", fallback="file") == "file"
    long = safe_name("가" * 300 + ".pdf")
    assert len(long) <= 120 and long.endswith(".pdf")


def test_html_to_text_and_due_in():
    assert html_to_text("<p>a&amp;b</p><script>x</script><p>c</p>") == "a&b\n\nc"
    assert html_to_text(None) == ""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert due_in(now + timedelta(days=3, hours=4, minutes=5), now) == "3d 4h"
    assert due_in(now + timedelta(hours=5, minutes=10), now) == "5h 10m"
    assert due_in(now - timedelta(seconds=1), now) == "overdue"


def test_scrub_removes_token_everywhere():
    data = {"a": "https://x/y?token=abc&z=1", "b": ["secret-tok-1 here"], "c": {"d": "wstoken=zzz"}}
    out = lms_client.scrub(data, "secret-tok-1")
    assert "abc" not in json.dumps(out) and "secret-tok-1" not in json.dumps(out)


# ── 학교 목록 ──


def test_directory_covers_survey():
    assert len(schools.SCHOOLS) == 49  # 조사한 48개 대학 + 울산대 의과대학(따로 쓰는 Moodle)
    by = {}
    for school in schools.SCHOOLS:
        by.setdefault(school.platform, []).append(school)
        assert school.platform in schools.PLATFORMS
        if school.supported:
            assert school.api.startswith("https://") and not school.site
        else:
            assert school.site.startswith("https://") and not school.api
        assert all(p.startswith("https://") for p in school.portals)
    assert len(by["learningx"]) == 11
    assert len(by["coursemos"]) + len(by["moodle"]) == 22
    assert len(by["ilos"]) == 9 and len(by["blackboard"]) == 3 and len(by["other"]) == 4
    owners = {}
    for school in schools.SCHOOLS:  # 이름·줄임말이 두 학교에 겹치지 않는다
        for k in schools._keys(school):
            assert owners.setdefault(k, school.name) == school.name, k


@pytest.mark.parametrize(("name", "expected"), [
    ("성대", "성균관대학교"), ("SKKU", "성균관대학교"), ("skku", "성균관대학교"), ("성균관대학교", "성균관대학교"),
    (" 서울대 ", "서울대학교"), ("고려대학교", "고려대학교"), ("KAIST", "한국과학기술원"), ("카이스트", "한국과학기술원"),
    ("코리아텍", "한국기술교육대학교"), ("울산의대", "울산대학교 의과대학"), ("한국산업기술대", "한국공학대학교"),
    ("이화 여대", "이화여자대학교"), ("POSTECH", "포항공과대학교"),
])
def test_school_alias_resolution(name, expected):
    assert schools.find(name).name == expected


def test_school_env_sets_address_and_type(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "abc123")
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "성대")
    config = lms_client.load_config()
    assert (config.base, config.kind, config.mode) == ("https://canvas.skku.edu", "canvas", "api")
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "연세대")
    assert (lms_client.load_config().base, lms_client.load_config().kind) == ("https://ys.learnus.org", "moodle")
    monkeypatch.setenv("OPENSTEM_LMS_URL", "https://lms.example.ac.kr")  # 직접 정한 주소가 이긴다
    config = lms_client.load_config()
    assert config.base == "https://lms.example.ac.kr" and config.kind is None


@pytest.mark.parametrize(("url", "api"), [
    ("https://icampus.skku.edu/", "https://canvas.skku.edu"),
    ("icampus.skku.edu/login", "https://canvas.skku.edu"),
    ("https://canvas.cau.ac.kr", "https://eclass3.cau.ac.kr"),  # 중앙대는 이름이 거꾸로
    ("https://www.rec.ac.kr/gnu", "https://canvas.rec.ac.kr"),
    ("https://lms1.knu.ac.kr", "https://canvas.knu.ac.kr"),
    ("https://canvas.knu.ac.kr", "https://canvas.knu.ac.kr"),
])
def test_portal_url_maps_to_api_host(monkeypatch, url, api):
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "abc123")
    monkeypatch.setenv("OPENSTEM_LMS_URL", url)
    config = lms_client.load_config()
    assert config.base == api and config.kind == "canvas"


@pytest.fixture
def test_school(monkeypatch, canvas):
    """가짜 포털(포트 A)과 가짜 Canvas(포트 B)를 학교 목록에 넣는다."""
    with FakePortal(canvas.url) as portal:
        school = schools.School("테스트대학교", ("테대",), "Test LMS", "learningx", api=canvas.url,
                                portals=(portal.url,))
        monkeypatch.setattr(schools, "SCHOOLS", (school,))
        yield portal


def test_portal_mapping_with_fake_hosts(test_school, canvas, monkeypatch):
    portal = test_school
    monkeypatch.setenv("OPENSTEM_LMS_URL", portal.url + "/login")
    data = ok("courses")
    assert data["lms"] == "canvas" and len(data["courses"]) == 3
    assert not portal.requests  # 포털에는 토큰을 한 번도 보내지 않았다
    assert not any(r.path == "/api/v1/users/self" for r in canvas.requests)  # 목록에서 종류를 알아 감지하지 않는다
    monkeypatch.delenv("OPENSTEM_LMS_URL")
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "테대")
    assert ok("deadlines")["deadlines"]


def test_unknown_portal_gives_419_hint(monkeypatch):
    with FakePortal() as portal:
        monkeypatch.setenv("OPENSTEM_LMS_URL", portal.url)
        monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "some-token-123")
        data = fail("courses")
        assert "419" in data["errors"][0] and "LearningX portal" in data["errors"][0]
        assert "OPENSTEM_LMS_SCHOOL" in data["hint"] and "Canvas API host" in data["hint"]
        monkeypatch.setenv("OPENSTEM_LMS_TYPE", "moodle")  # Moodle POST도 419
        lms_tool.reset_cache()
        assert "LearningX portal" in fail("courses")["errors"][0]


def test_html_instead_of_api(monkeypatch):
    with FakePortal() as portal:
        portal.api_status = 200
        monkeypatch.setenv("OPENSTEM_LMS_URL", portal.url)
        monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "some-token-123")
        data = fail("courses")
        assert "Could not recognise" in data["errors"][0] and "LearningX portal" in data["hint"]
        monkeypatch.setenv("OPENSTEM_LMS_TYPE", "canvas")  # 종류를 정해도 빈 목록으로 보이지 않는다
        assert "web page" in fail("courses")["errors"][0]
        monkeypatch.setenv("OPENSTEM_LMS_TYPE", "moodle")
        assert "web page" in fail("courses")["errors"][0]


def test_canvas_guess():
    assert lms_client.canvas_guess("lms1.knu.ac.kr") == "https://canvas.knu.ac.kr"
    assert lms_client.canvas_guess("nlms.dankook.ac.kr") == "https://canvas.dankook.ac.kr"
    assert lms_client.canvas_guess("portal.example.com") == "https://canvas.example.com"
    assert lms_client.canvas_guess("127.0.0.1") is None


def test_unknown_school_lists_close_matches(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "성균대")
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "abc123")
    data = fail("courses")
    assert "Unknown school" in data["errors"][0] and "성균관대학교" in data["hint"]
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "zzzz")
    assert 'action="schools"' in fail("courses")["hint"]


def test_unsupported_school(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "abc123")
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "서강대")
    data = fail("deadlines")
    assert "ILOS" in data["errors"][0] and "cannot read" in data["errors"][0]
    assert "materials folder" in data["hint"] and "read_document" in data["hint"]
    assert "OPENSTEM_LMS_CALENDAR_URL" not in data["hint"]  # ILOS에는 캘린더 주소도 없다
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "아주대")
    data = fail("deadlines")
    assert "Blackboard" in data["errors"][0] and "OPENSTEM_LMS_CALENDAR_URL" in data["hint"]
    monkeypatch.delenv("OPENSTEM_LMS_SCHOOL")
    monkeypatch.setenv("OPENSTEM_LMS_URL", "https://eclass.sogang.ac.kr/ilos/main/main_form.acl")
    assert "서강대학교" in fail("courses")["errors"][0]  # 주소로도 알아본다


def test_missing_config_mentions_school_first():
    hint = fail("deadlines")["hint"]
    assert hint.index("OPENSTEM_LMS_SCHOOL") < hint.index("token")
    assert "새 액세스 토큰" in hint and "보안 키" in hint and "OPENSTEM_LMS_CALENDAR_URL" in hint
    assert "캘린더 피드" in hint and "일정 내보내기" in hint


def test_school_without_token_gives_platform_steps(monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "성균관대")
    data = fail("courses")
    assert "성균관대학교" in data["errors"][0]
    assert "새 액세스 토큰" in data["hint"] and "보안 키" not in data["hint"] and "캘린더 피드" in data["hint"]
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "연세대")  # 모바일 서비스가 꺼진 학교: 캘린더 주소를 먼저
    hint = fail("courses")["hint"]
    assert hint.index("일정 내보내기") < hint.index("보안 키") and "새 액세스 토큰" not in hint


def test_schools_action():
    data = ok("schools")
    assert data["count"] == 49
    assert "성균관대학교" in data["supported"]["LearningX (Canvas)"]
    assert "연세대학교" in data["supported"]["Coursemos/Moodle"]
    assert "서강대학교" in data["unsupported"]["ILOS"] and "아주대학교" in data["unsupported"]["Blackboard"]
    assert len(json.dumps(data, ensure_ascii=False)) < 3000  # LLM에게 짧게
    one = ok("schools", course="성대")["schools"]
    assert len(one) == 1 and one[0]["address"] == "https://canvas.skku.edu" and "새 액세스 토큰" in one[0]["token_steps"]
    yonsei = ok("schools", course="LearnUs")["schools"][0]
    assert yonsei["token"].startswith("unlikely") and "일정 내보내기" in yonsei["calendar_steps"]
    ilos = ok("schools", course="ilos")["schools"]
    assert len(ilos) == 9 and all(not r["supported"] and "what_works" in r for r in ilos)
    assert ok("schools", course="없는학교이름")["schools"] == []


# ── 한국어 에러·401의 두 뜻 ──


def test_canvas_wrong_token_without_header(canvas, monkeypatch):
    canvas.www_auth = False  # 헤더가 없으면 /users/self로 토큰을 확인해 가른다
    canvas.self_404 = True  # LearningX: 인증 안 된 /users/self는 404 JSON
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "wrong-token-000")
    data = fail("courses")
    assert "rejected the token" in data["errors"][0] and "New Access Token" in data["hint"]


def test_canvas_detects_with_courses_when_users_self_is_html(canvas):
    canvas.self_html = True
    assert ok("courses")["lms"] == "canvas"
    assert any(r.path == "/api/v1/courses" and r.q("per_page") == "1" for r in canvas.requests)


def test_canvas_401_without_access_is_not_token_error(canvas, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_TYPE", "canvas")  # 감지를 건너뛰어 /users/self는 토큰 확인으로만 부른다
    data = ok("files", course="102")  # 파일 메뉴: 401 "권한이 없음", 토큰은 살아 있다 → 모듈로 대신
    assert "modules" in data["source"]
    assert any(r.path == "/api/v1/users/self" for r in canvas.requests)


def test_moodle_korean_errors_judged_by_errorcode(moodle, monkeypatch):
    moodle.missing.add("mod_forum_get_forum_discussions")  # 한국어 메시지여도 invalidrecord + 표 이름으로 안다
    assert ok("announcements", course="11")["announcements"]
    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "wrong-token-000")
    assert "rejected the token" in fail("deadlines")["errors"][0]


def test_moodle_without_news_forum_notes_board(moodle):
    moodle.news = False
    data = ok("announcements")
    assert data["announcements"] == []
    assert "notice board" in data["note"] and "not follow" in data["note"]


# ── 캘린더 모드 ──


def _expected_due(dt):
    return iso(dt)


def test_calendar_only_canvas_feed(canvas, monkeypatch):
    monkeypatch.delenv("OPENSTEM_LMS_TOKEN")
    monkeypatch.delenv("OPENSTEM_LMS_URL")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", canvas.feed_url)
    data = ok("deadlines", days=14)
    rows = {d["title"]: d for d in data["deadlines"]}
    assert list(rows) == ["지난 과제", "과제 3: 운동량", "아주 긴 제목의 실험 보고서 과제입니다 반드시 양식을 지켜 제출하세요",
                          "학과 행사"]  # 취소·40일 뒤는 빠짐, 접힌 긴 제목도 그대로
    a = canvas.anchor
    assert rows["과제 3: 운동량"]["course"] == "일반물리학 1"
    assert rows["과제 3: 운동량"]["due"] == _expected_due(a + timedelta(days=2, hours=4))
    assert rows["과제 3: 운동량"]["type"] == "assignment" and rows["과제 3: 운동량"]["url"].endswith("/assignments/501")
    assert rows["지난 과제"]["due_in"] == "past"
    long = rows["아주 긴 제목의 실험 보고서 과제입니다 반드시 양식을 지켜 제출하세요"]
    assert long["course"] == "일반화학 및 실험" and long["details"].startswith("1. 표지 2. 결과, 고찰; 참고문헌")
    assert len(long["details"]) <= 200
    assert rows["학과 행사"]["due"] == (a + timedelta(days=5)).astimezone(SEOUL).date().isoformat()  # 종일
    assert "calendar" in data["source"] and "submitted" in data["note"]
    assert canvas.requests and all("authorization" not in r.headers for r in canvas.requests)
    assert [d["title"] for d in ok("deadlines", course="화학")["deadlines"]] == [long["title"]]
    assert ok("deadlines", days=60)["deadlines"][-1]["title"] == "기말 프로젝트"
    assert "url" not in ok("deadlines", days=60)["deadlines"][-1]  # javascript: 주소는 버린다


def test_calendar_only_moodle_feed(moodle, monkeypatch):
    monkeypatch.delenv("OPENSTEM_LMS_TOKEN")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", moodle.feed_url)
    data = ok("deadlines")
    rows = {d["title"]: d for d in data["deadlines"]}
    assert list(rows) == ["과제 1 마감", "회로 퀴즈 마감"]
    assert rows["과제 1 마감"]["course"] == "공업수학 1" and rows["과제 1 마감"]["details"] == "보고서를 PDF로 내세요."
    assert rows["회로 퀴즈 마감"]["due"] == _expected_due(moodle.anchor + timedelta(days=4))  # TZID=Asia/Seoul
    assert rows["회로 퀴즈 마감"]["type"] == "quiz"


def test_calendar_tzid_without_tzdata(moodle, monkeypatch):
    """Windows에 tzdata가 없을 때: Asia/Seoul은 +09:00으로 본다."""
    from zoneinfo import ZoneInfoNotFoundError

    def missing(name):
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(ical, "_load_zone", missing)
    monkeypatch.delenv("OPENSTEM_LMS_TOKEN")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", moodle.feed_url)
    rows = {d["title"]: d for d in ok("deadlines")["deadlines"]}
    assert rows["회로 퀴즈 마감"]["due"] == _expected_due(moodle.anchor + timedelta(days=4))


def test_calendar_only_other_actions_explain(canvas, monkeypatch):
    monkeypatch.delenv("OPENSTEM_LMS_TOKEN")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", canvas.feed_url)
    monkeypatch.setenv("OPENSTEM_LMS_SCHOOL", "성균관대")
    data = fail("courses")
    assert "Only action=\"deadlines\"" in data["errors"][0]
    assert "OPENSTEM_LMS_TOKEN" in data["hint"] and "새 액세스 토큰" in data["hint"]


def test_calendar_url_is_secret(canvas, moodle, monkeypatch):
    monkeypatch.delenv("OPENSTEM_LMS_TOKEN")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", canvas.feed_url.replace(FakeCanvas.FEED_KEY, FakeCanvas.FEED_KEY + "x"))
    data = fail("deadlines")  # run()이 주소의 비밀 조각이 결과에 없는지 본다
    assert "refused the calendar link" in data["errors"][0] and "copy the calendar URL again" in data["hint"]
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", moodle.feed_url.replace(FakeMoodle.FEED_KEY, "Md5wrongTokenValue1"))
    data = fail("deadlines")
    assert "did not return a calendar" in data["errors"][0]
    assert "Md5wrongTokenValue1" not in json.dumps(data, ensure_ascii=False)
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", "http://lms.example.ac.kr/feeds/calendars/user_abc.ics")
    data = fail("deadlines")
    assert "https://" in data["errors"][0] and "user_abc" not in json.dumps(data)
    config_secrets = lms_client.calendar_secrets(moodle.feed_url)
    assert FakeMoodle.FEED_KEY in config_secrets and moodle.feed_url in config_secrets
    scrubbed = lms_client.scrub({"x": f"see {canvas.feed_url} and user_{FakeCanvas.FEED_KEY}"},
                                *lms_client.calendar_secrets(canvas.feed_url))
    assert FakeCanvas.FEED_KEY not in json.dumps(scrubbed)


def test_api_and_calendar_merge(canvas, monkeypatch):
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", canvas.feed_url)
    data = ok("deadlines", days=14)
    titles = [d["title"] for d in data["deadlines"]]
    assert titles.count("과제 3: 운동량") == 1  # API와 피드에 다 있는 것은 한 번만
    added = [d for d in data["deadlines"] if d.get("from") == "calendar"]
    assert {d["title"] for d in added} == {"아주 긴 제목의 실험 보고서 과제입니다 반드시 양식을 지켜 제출하세요", "학과 행사"}
    assert "지난 과제" not in titles  # 같은 시각의 "지난 과제(미제출)"와 같은 것으로 본다
    assert "calendar feed" in data["note"]
    canvas.FEED_KEY = "changedKey000000"  # 피드가 실패해도 API 결과는 그대로
    data = ok("deadlines", days=14)
    assert "Calendar feed skipped" in data["note"] and "과제 3: 운동량" in [d["title"] for d in data["deadlines"]]


# ── iCalendar 읽기 ──


def _cal(*lines):
    return b"".join(fold(x) for x in ("BEGIN:VCALENDAR", *lines, "END:VCALENDAR"))


def test_ical_unfold_escapes_and_params():
    title = "가" * 40 + " [과목, 이름]"  # 75바이트마다 접히며 한글 글자 가운데서 끊긴다
    data = _cal("BEGIN:VEVENT", f"SUMMARY:{title}", 'DTSTART;TZID="Asia/Seoul":20261105T235900',
                "DESCRIPTION:a\\nb\\, c\\\\d", "CATEGORIES:물리\\, 1,화학", "END:VEVENT")
    assert b"\r\n " in data
    (event,), truncated = ical.parse(data)
    assert not truncated and event.summary == title
    assert event.description == "a\nb, c\\d" and event.categories == ["물리, 1", "화학"]
    assert event.when == datetime(2026, 11, 5, 14, 59, tzinfo=timezone.utc)
    assert ical.summary_course(event) == (title, "물리, 1")  # CATEGORIES가 먼저


def test_ical_times():
    data = _cal(
        "BEGIN:VTIMEZONE", "TZID:My Zone", "BEGIN:STANDARD", "TZOFFSETTO:+0530", "END:STANDARD", "END:VTIMEZONE",
        "BEGIN:VTODO", "SUMMARY:todo", "DUE:20261105T120000Z", "END:VTODO",
        "BEGIN:VEVENT", "SUMMARY:zone", "DTSTART;TZID=My Zone:20261105T120000", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:kst", "DTSTART;TZID=Korea Standard Time:20261105T120000", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:day", "DTSTART;VALUE=DATE:20261105", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:bad", "DTSTART:20261345T000000Z", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:old", "DTSTART;VALUE=DATE:19000101", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:floating", "DTSTART:19000101T000000", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:alarm", "DTSTART:20261106T000000Z",
        "BEGIN:VALARM", "TRIGGER:-PT15M", "DESCRIPTION:not this", "END:VALARM", "END:VEVENT",
    )
    events, _ = ical.parse(data)
    when = {e.summary: e for e in events}
    assert "bad" not in when  # 날짜가 이상하면 뺀다
    assert when["todo"].when == datetime(2026, 11, 5, 12, tzinfo=timezone.utc) and when["todo"].component == "VTODO"
    assert when["zone"].when.utcoffset() == timedelta(hours=5, minutes=30)  # VTIMEZONE 오프셋
    assert when["kst"].when == datetime(2026, 11, 5, 3, tzinfo=timezone.utc)  # Windows 이름도 서울로
    assert when["day"].all_day and when["day"].when.hour == 23
    assert when["alarm"].description == ""  # VALARM 안의 값은 일정 것이 아니다


def test_ical_limits(monkeypatch):
    with pytest.raises(ical.IcalError):
        ical.parse(b"<html>login</html>")
    with pytest.raises(ical.IcalError):
        ical.parse(b"BEGIN:VCALENDAR\r\n" + b"X" * (ical.MAX_BYTES + 1))
    many = _cal(*[x for i in range(30) for x in ("BEGIN:VEVENT", f"SUMMARY:e{i}", "DTSTART:20261105T000000Z", "END:VEVENT")])
    monkeypatch.setattr(ical, "MAX_EVENTS", 10)
    events, truncated = ical.parse(many)
    assert len(events) == 10 and truncated
    start = datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert ical.parse(many, window=(start, start + timedelta(days=1)))[0] == []  # 기간 밖은 세지 않는다


# ── 보안 감사(2026-11-03) 후속: 로그, 파일 id, sync 안전, 시간 상한, 이상한 응답 ──


LOG_SCRIPT = r"""
import json, logging, os, sys
sys.path.insert(0, sys.argv[1])
import openstem.server  # noqa: F401 — 실제 서버처럼 SDK가 루트 로깅(INFO, stderr)을 설정한다
from fake_lms import FakeCanvas, FakeMoodle
from openstem.tools import lms as lms_tool

out = {}
with FakeMoodle() as moodle, FakeCanvas() as canvas:
    os.environ["OPENSTEM_LMS_URL"] = moodle.url
    os.environ["OPENSTEM_LMS_TOKEN"] = FakeMoodle.TOKEN
    files = lms_tool.lms("files", course="11").data["files"]
    item = next(f["id"] for f in files if f["name"] == "lecture1.pdf")
    out["download"] = lms_tool.lms("download", course="11", item=item).ok
    # 다른 경로로 새는 것도 가린다(httpx 로거를 다시 INFO로 올리거나 다른 로거가 주소를 찍어도)
    logging.getLogger("httpx2").setLevel(logging.INFO)
    logging.getLogger("httpx2").info("HTTP Request: GET %s/webservice/pluginfile.php/1/x.pdf?token=%s",
                                     moodle.url, FakeMoodle.TOKEN)
    logging.getLogger("openstem.other").warning("canvas file %s/files/1/download?verifier=abcdef123", canvas.url)
    try:
        raise RuntimeError("boom " + FakeMoodle.TOKEN)
    except RuntimeError:
        logging.getLogger("openstem.other").exception("failed")
    del os.environ["OPENSTEM_LMS_URL"], os.environ["OPENSTEM_LMS_TOKEN"]
    os.environ["OPENSTEM_LMS_CALENDAR_URL"] = canvas.feed_url
    out["calendar"] = lms_tool.lms("deadlines").ok
    logging.getLogger("openstem.other").warning("feed %s", canvas.feed_url)
print(json.dumps(out))
"""


def test_logs_never_carry_tokens_or_calendar_secrets(tmp_path):
    """stdio 모드의 stderr(호스트 로그 파일)에 토큰·캘린더 주소·verifier가 남지 않는다."""
    import os
    import subprocess
    import sys

    script = tmp_path / "log_check.py"
    script.write_text(LOG_SCRIPT, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENSTEM_LMS_")}
    env.update(OPENSTEM_INPUT_DIR=str(tmp_path / "materials"), OPENSTEM_OUTPUT_DIR=str(tmp_path / "out"),
               COLUMNS="400", PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, str(script), str(Path(__file__).parent)], capture_output=True, check=False,
                          text=True, encoding="utf-8", env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"download": True, "calendar": True}
    flat = "".join(proc.stderr.split())  # rich가 긴 줄을 접어도 비밀 조각을 놓치지 않게
    for secret in (FakeMoodle.TOKEN, FakeCanvas.FEED_KEY, "abcdef123"):
        assert secret not in flat, proc.stderr
    assert "/feeds/calendars/" not in flat
    assert "boom" in flat and "***" in flat  # 가리기만 하고 로그는 남긴다
    assert "lecture1.pdf?forcedownload" not in flat  # httpx 요청 로그 자체가 꺼져 있다


def test_log_filter_unit(monkeypatch):
    import logging

    from openstem.lms import logs

    monkeypatch.setenv("OPENSTEM_LMS_TOKEN", "unit-secret-token-1")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", "https://lms.example.ac.kr/feeds/calendars/user_AbCdEf0123456789.ics")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "GET %s and %s", (
        "https://h/f?verifier=v123456&x=1", "unit-secret-token-1 user_AbCdEf0123456789"), None)
    assert logs.SecretFilter().filter(record)
    text = record.getMessage()
    assert "v123456" not in text and "unit-secret-token-1" not in text and "AbCdEf0123456789" not in text
    logs.install()
    assert logging.getLogger("httpx2").level >= logging.WARNING
    assert logging.getLogger("httpcore2").level >= logging.WARNING


def test_scrub_hides_canvas_verifier():
    out = lms_client.scrub({"u": "https://c/files/1/download?download_frd=1&verifier=secretV1"})
    assert "secretV1" not in json.dumps(out)


def _moodle_folder_ids(names):
    return {f["name"]: f["id"] for f in ok("files", course="11")["files"] if f["name"] in names}


def test_moodle_file_ids_survive_a_file_inserted_in_a_folder(moodle, tmp_path):
    """폴더 [a, b]에 a0이 끼어도 id가 그대로라 sync가 b.pdf에 a0 내용을 쓰지 않는다."""
    folder = materials(tmp_path) / "LMS" / "공업수학 1"
    moodle.folder_files = ["a.pdf", "b.pdf"]
    moodle.content = {"a.pdf": b"%PDF a", "b.pdf": b"%PDF b", "a0.pdf": b"%PDF a0"}
    before = _moodle_folder_ids({"a.pdf", "b.pdf"})
    ok("sync", course="11")
    moodle.folder_files = ["a0.pdf", "a.pdf", "b.pdf"]
    after = _moodle_folder_ids({"a.pdf", "b.pdf"})
    assert before == after
    data = ok("sync", course="11")
    assert [d["path"] for d in data["downloaded"]] == ["LMS/공업수학 1/a0.pdf"]
    assert (folder / "a0.pdf").read_bytes() == b"%PDF a0"
    assert (folder / "b.pdf").read_bytes() == b"%PDF b" and (folder / "a.pdf").read_bytes() == b"%PDF a"
    assert ok("sync", course="11")["downloaded"] == []  # outside.pdf(다른 사이트 주소)만 늘 실패로 남는다


def test_moodle_legacy_sync_state_matches_by_name_only(moodle, tmp_path):
    """예전 id(<모듈>.<순번>) 기록: 순번이 밀려도 이름·sha256으로만 이어받는다."""
    import hashlib

    folder = materials(tmp_path) / "LMS" / "공업수학 1"
    folder.mkdir(parents=True)
    moodle.folder_files = ["a0.pdf", "a.pdf", "b.pdf"]
    moodle.content = {"a.pdf": b"%PDF a", "b.pdf": b"%PDF b", "a0.pdf": b"%PDF a0"}
    (folder / "a.pdf").write_bytes(b"%PDF a")
    (folder / "b.pdf").write_bytes(b"%PDF b")
    stamp = iso(datetime.fromtimestamp(int(moodle.anchor.timestamp()) - 2 * 86400, tz=timezone.utc))

    def entry(name, body):
        return {"name": name, "updated": stamp, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    # 예전 순번: 폴더가 [a, b]일 때 202.0 = a, 202.1 = b. 지금은 [a0, a, b]라 순번으로 맞추면 b ← a0가 된다
    (folder / ".openstem-sync.json").write_text(json.dumps({"version": 1, "files": {
        "202.0": entry("a.pdf", b"%PDF a"), "202.1": entry("b.pdf", b"%PDF b")}}), encoding="utf-8")
    data = ok("sync", course="11")
    paths = [d["path"] for d in data["downloaded"]]
    assert "LMS/공업수학 1/a0.pdf" in paths and "LMS/공업수학 1/b.pdf" not in paths
    assert (folder / "b.pdf").read_bytes() == b"%PDF b" and (folder / "a0.pdf").read_bytes() == b"%PDF a0"
    saved = json.loads((folder / ".openstem-sync.json").read_text(encoding="utf-8"))
    assert saved["version"] == 2 and not any(k in saved["files"] for k in ("202.0", "202.1"))
    assert not list(folder.glob("* (2).pdf"))


def test_sync_keeps_a_file_the_user_edited(canvas, tmp_path):
    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    ok("sync", course="101")
    (folder / "lecture1.pdf").write_bytes(b"%PDF-1.4 my notes")  # 사용자가 받은 파일에 필기했다
    canvas.contents[9001] = b"%PDF-1.4 lecture one, revised"
    canvas.modified[9001] = canvas.file_time + timedelta(hours=1)
    data = ok("sync", course="101")
    assert data["downloaded"] == [{"path": "LMS/일반물리학 1/lecture1 (2).pdf", "status": "new_copy",
                                   "kept": "LMS/일반물리학 1/lecture1.pdf",
                                   "why": "the earlier copy was changed on this computer, so it was kept"}]
    assert "nothing was overwritten" in data["note"]
    assert (folder / "lecture1.pdf").read_bytes() == b"%PDF-1.4 my notes"
    assert (folder / "lecture1 (2).pdf").read_bytes() == b"%PDF-1.4 lecture one, revised"
    assert ok("sync", course="101")["downloaded"] == []  # 다음 sync는 (2)를 받은 사본으로 안다
    canvas.contents[9001] = b"%PDF-1.4 lecture one, v3"
    canvas.modified[9001] = canvas.file_time + timedelta(hours=2)
    data = ok("sync", course="101")
    assert data["downloaded"] == [{"path": "LMS/일반물리학 1/lecture1 (2).pdf", "status": "updated"}]
    assert (folder / "lecture1.pdf").read_bytes() == b"%PDF-1.4 my notes"


def test_sync_file_open_in_another_app_is_kept(canvas, tmp_path, monkeypatch):
    """Windows에서 열린 PDF를 바꾸면 PermissionError: sync 전체가 멈추지 않고 (2)로 저장한다."""
    import os

    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    ok("sync", course="101")
    canvas.contents[9001] = b"%PDF-1.4 lecture one, revised"
    canvas.modified[9001] = canvas.file_time + timedelta(hours=1)
    real = os.replace

    def locked(src, dst):
        if Path(dst).name == "lecture1.pdf":
            raise PermissionError(13, "The process cannot access the file")
        return real(src, dst)

    monkeypatch.setattr(lms_tool.os, "replace", locked)
    data = ok("sync", course="101")
    assert data["downloaded"][0]["status"] == "new_copy" and "open in another app" in data["downloaded"][0]["why"]
    assert (folder / "lecture1.pdf").read_bytes() == b"%PDF-1.4 lecture one"
    assert (folder / "lecture1 (2).pdf").read_bytes() == b"%PDF-1.4 lecture one, revised"


def test_sync_partial_result_and_failed_files_go_last(canvas, tmp_path):
    canvas.extra_files = [(9050, "gone1.pdf"), (9100, "gone2.pdf"), (9150, "gone3.pdf")]  # 가장 최근, 받으면 404
    first = ok("sync", course="101")
    assert [f["name"] for f in first["failed"]] == ["gone1.pdf", "gone2.pdf", "gone3.pdf"]
    assert first["downloaded"] == [] and first["remaining"] > 0 and "repeated download failures" in first["next"]
    second = ok("sync", course="101")
    assert "LMS/일반물리학 1/lecture1.pdf" in {d["path"] for d in second["downloaded"]}
    assert "remaining" not in second and len(second["failed"]) == 3 and "retried last" in second["note"]


def test_sync_same_course_at_once(canvas, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: lms_tool.lms("sync", course="101"), range(4)))
    assert all(r.ok for r in results), [r.data for r in results]
    assert not list(folder.glob("* (2).*")) and not list(folder.glob("*.part")) and not list(folder.glob(".*.part"))
    state = json.loads((folder / ".openstem-sync.json").read_text(encoding="utf-8"))
    assert "9001" in state["files"]


def test_sync_lock_busy(canvas, monkeypatch):
    monkeypatch.setattr(lms_tool, "SYNC_LOCK_WAIT", 0.01)
    ok("files", course="101")
    _root, folder = lms_tool._course_folder({"id": 101, "name": "일반물리학 1"})
    lock = lms_tool._sync_lock(folder)
    with lock:
        data = fail("sync", course="101")
    assert "still running" in data["errors"][0]


def test_sync_passes_remaining_time_to_downloads(canvas, monkeypatch):
    import time

    canvas.download_delay = 3.0
    monkeypatch.setattr(lms_tool, "SYNC_DEADLINE", 0.5)
    monkeypatch.setattr(lms_tool, "SYNC_MAX_ERRORS", 1)
    started = time.monotonic()
    data = ok("sync", course="101")
    assert time.monotonic() - started < 2.5
    assert "took over" in data["failed"][0]["error"]


def test_slow_response_headers_are_cut_off(canvas, monkeypatch):
    import time

    canvas.header_delay = 3.0
    monkeypatch.setattr(lms_client, "JSON_DEADLINE", 0.5)
    started = time.monotonic()
    assert "too slowly" in fail("courses")["errors"][0]
    assert time.monotonic() - started < 2.5


def test_slow_calendar_headers_are_cut_off(canvas, monkeypatch):
    import time

    from openstem.lms import feed

    monkeypatch.delenv("OPENSTEM_LMS_TOKEN")
    monkeypatch.delenv("OPENSTEM_LMS_URL")
    monkeypatch.setenv("OPENSTEM_LMS_CALENDAR_URL", canvas.feed_url)
    canvas.header_delay = 3.0
    monkeypatch.setattr(feed, "FEED_DEADLINE", 0.5)
    started = time.monotonic()
    assert "too slowly" in fail("deadlines")["errors"][0]
    assert time.monotonic() - started < 2.5


def test_long_korean_file_name_is_cut_by_bytes(canvas, tmp_path):
    name = "아주 긴 강의 자료 이름 " * 20 + ".pdf"
    canvas.extra_files = [(9060, name)]
    canvas.contents[9060] = b"%PDF long"
    got = ok("download", course="101", item="9060")
    saved = materials(tmp_path) / got["path"]
    assert saved.read_bytes() == b"%PDF long" and saved.suffix == ".pdf"
    assert len(saved.name.encode("utf-8")) <= 200


def test_disk_error_while_saving_is_a_short_error(canvas, tmp_path, monkeypatch):
    from openstem.lms import common

    monkeypatch.setattr(common, "NAME_LIMIT", 10_000)
    monkeypatch.setattr(common, "NAME_BYTES", 10_000)
    canvas.extra_files = [(9060, "가" * 150 + ".pdf")]
    canvas.contents[9060] = b"%PDF long"
    # 이름 길이 상한은 파일 시스템마다 다르다(Linux는 255바이트, NTFS·APFS는 UTF-16 255자라 이 이름도 받는다).
    # 디스크 에러 처리만 보려고, 이 이름을 만들 때 운영체제가 "이름이 너무 김"을 돌려준다고 꾸민다.
    import errno
    import os

    real_open = os.open

    def too_long(path, *args, **kwargs):
        if "가" * 100 in str(path):
            raise OSError(errno.ENAMETOOLONG, "File name too long", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", too_long)
    data = fail("download", course="101", item="9060")
    assert "Could not save" in data["errors"][0] and "too long" in data["hint"]
    folder = materials(tmp_path) / "LMS" / "일반물리학 1"
    assert not list(folder.glob("*.part")) and not list(folder.glob(".*.part"))


def test_unexpected_errors_are_short_and_scrubbed(canvas, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RecursionError(f"deep {FakeCanvas.TOKEN}")

    monkeypatch.setattr(lms_tool.canvas.Canvas, "courses", boom)
    data = fail("courses")
    assert "could not handle" in data["errors"][0] and "RecursionError" in data["errors"][0]


def test_broken_urls_and_far_dates_do_not_crash(canvas):
    canvas.broken_file_url = True
    assert "broken" in fail("download", course="101", item="9001")["errors"][0]
    assert lms_client.origin_of("https://[x/") == ("", "", 0)
    assert lms_client.join_url("http://127.0.0.1/", "https://[x/") is None
    canvas.broken_file_url = False
    canvas.extra_planner = [{"plannable_type": "assignment", "course_id": 101, "context_name": "일반물리학 1",
                             "plannable_date": "9999-12-31T23:59:59Z",
                             "plannable": {"title": "먼 미래", "due_at": "9999-12-31T23:59:59Z"},
                             "submissions": False, "html_url": "https://[x/"}]
    data = ok("deadlines")
    assert "먼 미래" not in [d["title"] for d in data["deadlines"]]


def test_deeply_nested_json_is_not_a_crash(moodle):
    # Python 3.13 이하는 깊은 JSON에서 RecursionError가 나고, 3.14는 그대로 읽는다. 어느 쪽이든 짧은 결과여야 한다.
    moodle.deep_json = True
    result = run("courses")
    if not result.ok:
        assert result.data["errors"] and all(len(e) < 300 for e in result.data["errors"])


def test_past_events_and_notes_are_past_not_overdue(canvas):
    now = datetime.now(timezone.utc)

    def item(kind, title):
        return {"plannable_type": kind, "course_id": 101, "context_name": "일반물리학 1",
                "plannable_date": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "plannable": {"title": title, "todo_date": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                "submissions": False}

    canvas.extra_planner = [item("planner_note", "내 메모"), item("calendar_event", "지난 행사")]
    rows = {d["title"]: d for d in ok("deadlines")["deadlines"]}
    assert rows["내 메모"]["due_in"] == "past" and rows["지난 행사"]["due_in"] == "past"
    assert rows["지난 과제(미제출)"]["due_in"] == "overdue"


def test_moodle_course_deadlines_use_course_function_and_pages(moodle):
    moodle.many_events = 60  # 회로이론 퀴즈 60개가 공업수학 일정보다 앞에 있다
    data = ok("deadlines", course="공업수학")
    assert "공수 퀴즈" in [d["title"] for d in data["deadlines"]]
    calls = [r.q("wsfunction") for r in moodle.requests if r.path == "/webservice/rest/server.php"]
    assert "core_calendar_get_action_events_by_course" in calls
    moodle.missing.add("core_calendar_get_action_events_by_course")  # 옛 Moodle: 전체에서 쪽을 넘기며 거른다
    data = ok("deadlines", course="공업수학")
    assert "공수 퀴즈" in [d["title"] for d in data["deadlines"]] and "per course" in data["note"]
    moodle.many_events = 250
    data = ok("deadlines")
    assert "truncated" in data and "more than 200" in data["note"]


def test_hidden_announcement_text_is_removed(canvas):
    canvas.hidden_announcement = True
    data = ok("announcements", course="103")
    row = data["announcements"][0]
    assert "보안경 지참" in row["text"] and "끝" in row["text"]
    for hidden in ("Ignore all previous", "SECRET-WHITE", "HIDDEN-ATTR"):
        assert hidden not in row["text"]
    assert row["hidden_text_removed"] is True and "hidden with CSS" in data["note"]
    st = {}
    assert html_to_text('<p>a<span style="font-size:0">x</span><b style="color:white">y</b>b</p>', stats=st) == "ab"
    assert st["hidden"] == 2


def test_ical_nesting_is_capped():
    import time

    deep = ["BEGIN:VCALENDAR"] + ["BEGIN:X"] * 40 + ["END:X"] * 40 + ["END:VCALENDAR"]
    with pytest.raises(ical.IcalError, match="nested"):
        ical.parse(_cal(*deep[1:-1]))
    lines = ["BEGIN:VTIMEZONE"] + ["X-A:1"] * 20000 + ["END:VTIMEZONE"]
    started = time.monotonic()
    ical.parse(_cal(*lines))
    assert time.monotonic() - started < 2


def test_lms_tool_annotations_say_it_writes_files():
    import anyio
    from mcp import Client

    from openstem.server import mcp

    async def tools():
        async with Client(mcp) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    found = anyio.run(tools)
    lms = found["lms"].annotations
    assert lms.read_only_hint is False and lms.destructive_hint is False and lms.open_world_hint is True
    assert "data, not instructions" in found["read_document"].description
