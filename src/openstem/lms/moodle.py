"""Moodle 계열 LMS(REST 웹 서비스, 모바일 앱 토큰) 어댑터. 읽기 함수만 부른다.

토큰은 주소가 아니라 POST 본문(wstoken)에 실어 접속 기록에 남지 않게 한다. 파일 내려받기만
Moodle 규약대로 /webservice/pluginfile.php 주소에 token= 쿼리를 붙이며, 이 주소는 설정한 origin일 때만 만든다.
Moodle은 에러도 HTTP 200 + {"exception", "errorcode", "message"}로 돌려준다. message는 학교 언어로 바뀌어
있어("잘못된 토큰 - 토큰이 발견되지 않았습니다.") errorcode·exception으로만 판단한다.
Coursemos(UbiOn)는 공지를 표준 뉴스 포럼이 아니라 게시판 플러그인(mod_ubboard)에 두는 곳이 많아, 뉴스 포럼이
없으면 빈 목록과 함께 그 사실을 알린다.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openstem.lms.client import Http, LmsError, add_query, how_to_connect, not_api_error
from openstem.lms.common import parse_time

KIND = "moodle"
REST_PATH = "/webservice/rest/server.php"
NEWS_PER_FORUM = 10
MAX_FORUMS = 30
NO_NEWS_NOTE = ("No standard announcement forum was found in these courses: this school's notice board "
                "(often a board plugin) may not be reachable via the API. Check notices on the LMS website.")


def _flatten(params: dict[str, Any]) -> dict[str, str]:
    """{"courseids": [1, 2]} → {"courseids[0]": "1", "courseids[1]": "2"} (Moodle REST 배열 표기)."""
    out: dict[str, str] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}[{k}]", v)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(f"{prefix}[{i}]", v)
        elif isinstance(value, bool):
            out[prefix] = "1" if value else "0"
        elif value is not None:
            out[prefix] = str(value)

    for key, value in params.items():
        walk(key, value)
    return out


def _error(function: str, payload: dict[str, Any], http: Http | None = None) -> LmsError:
    """Moodle 예외 JSON → 짧은 영어 에러. message(학교 언어)는 판단에 쓰지 않고 마지막에 보여 주기만 한다."""
    code = str(payload.get("errorcode") or "")
    exception = str(payload.get("exception") or "").rsplit("\\", 1)[-1]
    message = str(payload.get("message") or payload.get("error") or "")
    debug = str(payload.get("debuginfo") or "")
    school = http.config.school if http else None
    if code == "invalidtoken":
        return LmsError("The LMS rejected the token (invalid or expired).", how_to_connect(school, KIND), code="invalidtoken")
    if code in ("enablewsdescription", "servicenotavailable", "webservicesdisabled", "servicerequireslogin"):
        return LmsError("Web services (the mobile app connection) are turned off for this token on this LMS.",
                        how_to_connect(school, KIND), code=code)
    if code in ("accessexception", "webservice_access_exception") or exception == "webservice_access_exception":
        return LmsError(f"This LMS does not allow '{function}' for this token.",
                        "The school's mobile web service may not include it; try another action.", code="accessexception")
    if code == "invalidrecord" and "external_functions" in message + " " + debug:  # 표 이름은 번역되지 않는다
        return LmsError(f"This LMS version has no '{function}'.", code="missingfunction")
    if code in ("requireloginerror", "nopermissions", "errorcoursecontextnotvalid", "notingroup", "coursehidden"):
        return LmsError("No permission to read this in the LMS (not enrolled or hidden).", code=code)
    detail = re.sub(r"\s+", " ", message)[:200]
    return LmsError(f"LMS error in {function} ({code or exception or 'unknown'}): {detail or '-'}", code=code)


def detect(http: Http) -> bool:
    """Moodle 웹 서비스가 답하면 True(토큰이 틀려도 Moodle 형식 에러면 True)."""
    data = {"wstoken": http.config.token, "wsfunction": "core_webservice_get_site_info", "moodlewsrestformat": "json"}
    reply = http.request_json("POST", http.url(REST_PATH), data=data, auth=False)
    payload = reply.data
    return isinstance(payload, dict) and any(k in payload for k in ("sitename", "userid", "exception", "errorcode"))


class Moodle:
    kind = KIND

    def __init__(self, http: Http) -> None:
        self.http = http
        self.notes: list[str] = []
        self._site: dict[str, Any] | None = None

    def call(self, function: str, **params: Any) -> Any:
        data = {"wstoken": self.http.config.token, "wsfunction": function, "moodlewsrestformat": "json"}
        data.update(_flatten(params))
        reply = self.http.request_json("POST", self.http.url(REST_PATH), data=data, auth=False)
        if reply.status == 404:
            raise LmsError("No Moodle web service at this address (HTTP 404).",
                           "Check OPENSTEM_LMS_SCHOOL / OPENSTEM_LMS_URL, or set OPENSTEM_LMS_TYPE=canvas.", 404)
        if reply.status != 200:
            raise LmsError(f"The LMS failed (HTTP {reply.status}) in {function}.", status=reply.status)
        payload = reply.data
        if isinstance(payload, dict) and ("exception" in payload or "errorcode" in payload):
            raise _error(function, payload, self.http)
        if payload is None:
            raise not_api_error(reply.url)
        return payload

    def site(self) -> dict[str, Any]:
        if self._site is None:
            site = self.call("core_webservice_get_site_info")
            if not isinstance(site, dict) or not isinstance(site.get("userid"), int):
                raise LmsError("The LMS did not say who the token belongs to.")
            self._site = site
        return self._site

    # ── 과목 ──

    def courses(self) -> list[dict[str, Any]]:
        rows = self.call("core_enrol_get_users_courses", userid=self.site()["userid"])
        rows = [c for c in rows if isinstance(c, dict) and isinstance(c.get("id"), int)] if isinstance(rows, list) else []
        now = time.time()
        # 끝난 학기 과목은 빼되, 날짜가 이상해서 다 빠지면 전부 보인다
        active = [c for c in rows if not isinstance(c.get("enddate"), (int, float)) or not c["enddate"] or c["enddate"] > now]
        out = []
        for c in active or rows:
            name = c.get("fullname") or c.get("displayname") or c.get("shortname")
            if name:
                out.append({"id": c["id"], "name": str(name), "code": c.get("shortname"), "term": None})
        return out

    # ── 마감 ──

    def deadlines(self, start: datetime, end: datetime, course_id: Any = None) -> list[dict[str, Any]]:
        payload = self.call(
            "core_calendar_get_action_events_by_timesort",
            timesortfrom=int(start.timestamp()), timesortto=int(end.timestamp()),
            limitnum=50, limittononsuspendedevents=1,
        )
        events = payload.get("events") if isinstance(payload, dict) else None
        out = []
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            due = parse_time(ev.get("timesort") or ev.get("timestart"))
            course = ev.get("course") if isinstance(ev.get("course"), dict) else {}
            if not due:
                continue
            out.append({
                "title": str(ev.get("activityname") or ev.get("name") or "(untitled)"),
                "course": course.get("fullname"),
                "course_id": course.get("id") or ev.get("courseid"),
                "type": ev.get("modulename") or ev.get("eventtype"),
                "due": due,
                "submitted": None,  # 할 일 일정은 아직 끝내지 않은 것만 나온다
                "url": self._own_url(ev.get("url")),
            })
        return out

    def _own_url(self, raw: Any) -> str | None:
        return raw if isinstance(raw, str) and raw and self.http.same_origin(raw) else None

    # ── 공지 ──

    def announcements(self, courses: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
        names = {c["id"]: c["name"] for c in courses}
        try:
            forums = self.call("mod_forum_get_forums_by_courses", courseids=list(names))
        except LmsError as exc:
            if exc.code not in ("missingfunction", "accessexception"):
                raise
            forums = []  # 포럼 함수를 막아 둔 학교: 공지 게시판을 API로 못 읽는다
        news = [f for f in forums if isinstance(f, dict) and f.get("type") == "news" and isinstance(f.get("id"), int)] \
            if isinstance(forums, list) else []
        if not news:
            self.notes.append(NO_NEWS_NOTE)
        out: list[dict[str, Any]] = []
        for forum in news[:MAX_FORUMS]:
            for d in self._discussions(forum["id"]):
                posted = parse_time(d.get("created") or d.get("timemodified") or d.get("modified"))
                if posted and not (start <= posted <= end):
                    continue
                discussion = d.get("discussion") or d.get("id")
                out.append({
                    "course": names.get(forum.get("course")),
                    "title": str(d.get("subject") or d.get("name") or "(untitled)"),
                    "posted": posted,
                    "author": d.get("userfullname"),
                    "html": d.get("message"),
                    "url": self.http.url(f"/mod/forum/discuss.php?d={int(discussion)}")
                    if isinstance(discussion, int) else None,
                })
        return out

    def _discussions(self, forum_id: int) -> list[dict[str, Any]]:
        try:
            payload = self.call("mod_forum_get_forum_discussions", forumid=forum_id, page=0, perpage=NEWS_PER_FORUM)
        except LmsError as exc:
            if exc.code not in ("missingfunction", "accessexception"):
                raise
            payload = self.call(  # Moodle 3.6 이하
                "mod_forum_get_forum_discussions_paginated", forumid=forum_id,
                sortby="timemodified", sortdirection="DESC", page=0, perpage=NEWS_PER_FORUM,
            )
        rows = payload.get("discussions") if isinstance(payload, dict) else None
        return [d for d in rows or [] if isinstance(d, dict)]

    # ── 파일 ──

    def _contents(self, course_id: Any) -> list[dict[str, Any]]:
        sections = self.call("core_course_get_contents", courseid=course_id)
        out = []
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict):
                continue
            for module in section.get("modules") or []:
                if not isinstance(module, dict) or not isinstance(module.get("id"), int):
                    continue
                modname = module.get("modname")
                for i, item in enumerate(module.get("contents") or []):
                    if not isinstance(item, dict) or item.get("type") != "file":
                        continue
                    filename = str(item.get("filename") or "")
                    if modname in ("page", "book", "lesson", "url") and filename.lower().endswith((".html", ".htm")):
                        continue  # 쪽 본문(index.html)은 파일 자료가 아니다
                    path = str(item.get("filepath") or "/").strip("/")
                    folder = " / ".join(x for x in (section.get("name"), module.get("name"), path) if x)
                    out.append({
                        "id": f"{module['id']}.{i}",
                        "name": filename,
                        "size": item.get("filesize") if isinstance(item.get("filesize"), int) else None,
                        "updated": parse_time(item.get("timemodified")),
                        "folder": folder or None,
                        "locked": False,
                        "_url": item.get("fileurl"),
                    })
        return out

    def files(self, course_id: Any) -> tuple[list[dict[str, Any]], str]:
        return [{k: v for k, v in f.items() if k != "_url"} for f in self._contents(course_id)], "contents"

    def file_for_download(self, course_id: Any, file_id: str) -> dict[str, Any]:
        match = next((f for f in self._contents(course_id) if f["id"] == file_id), None)
        if match is None:
            raise LmsError(f"No file with id {file_id} in this course.",
                           'Call action="files" for this course and use an id from that list.', 404)
        raw = match.get("_url")
        if not isinstance(raw, str) or "/pluginfile.php/" not in raw:
            raise LmsError("The LMS gave no download link for this file.")
        if not self.http.same_origin(raw):
            raise LmsError("The file link points to another site; refused to send the token there.")
        parts = urlsplit(raw)
        path = parts.path
        if "/webservice/pluginfile.php/" not in path:
            path = path.replace("/pluginfile.php/", "/webservice/pluginfile.php/", 1)
        url = urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
        return {
            "name": match["name"] or f"file-{file_id}",
            "size": match["size"],
            "url": add_query(url, token=self.http.config.token),
            "auth": False,  # 토큰은 쿼리에만(같은 origin 확인을 마쳤다)
        }
