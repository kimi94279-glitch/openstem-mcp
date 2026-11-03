"""Canvas 계열 LMS(REST API v1, Bearer 토큰) 어댑터. 읽기(GET)만 한다.

에러 글은 학교마다 한국어로 바뀌어 있어("인증되지 않음", "사용자 인증 필요", "권한이 없음") 글자로 판단하지 않고
상태 코드·JSON 모양·WWW-Authenticate 헤더로 판단한다. 401은 두 가지다: 토큰이 틀렸거나 만료됨(Canvas는
WWW-Authenticate를 붙인다) / 토큰은 맞는데 그 과목·메뉴에 권한이 없음. 헤더가 없으면 /api/v1/users/self로
토큰이 살아 있는지 한 번 확인해 가른다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from openstem.lms.client import Http, LmsError, Reply, how_to_connect
from openstem.lms.common import parse_time

KIND = "canvas"
MODULE_FILE_LOOKUPS = 200  # 모듈 대체 경로에서 파일 정보를 하나씩 물어보는 최대 개수
MODULE_LOOKUP_BUDGET = 25.0  # 그 조회에 쓰는 시간(초). 넘으면 이름만 돌려준다


def _zulu(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _message(reply: Reply) -> str:
    """에러 본문의 첫 메시지(학교 언어 그대로, 판단에는 쓰지 않고 보여 주기만 한다)."""
    data = reply.data
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("message") or "")[:200]
        if isinstance(errors, dict):
            return str(next(iter(errors.values()), ""))[:200]
        return str(data.get("message") or data.get("status") or "")[:200]
    return ""


def canvas_error_body(reply: Reply) -> bool:
    """Canvas 에러 JSON 모양: {"errors": [...]} 또는 {"errors": {...}}."""
    return isinstance(reply.data, dict) and isinstance(reply.data.get("errors"), (list, dict))


_CANVAS_HEADERS = ("x-canvas-meta", "x-request-context-id", "x-rate-limit-remaining")


def _canvas_shaped(reply: Reply) -> bool:
    """Canvas 응답 모양: 사용자(dict, id), 과목 목록(course_code가 있거나 Canvas 헤더), 에러 JSON(4xx)."""
    data = reply.data
    if reply.status == 200:
        if isinstance(data, dict):
            return "id" in data
        if isinstance(data, list):
            courses = bool(data) and all(isinstance(c, dict) and "course_code" in c for c in data)
            return courses or any(reply.header(h) for h in _CANVAS_HEADERS)
        return False
    return 400 <= reply.status < 500 and canvas_error_body(reply)


def detect(http: Http) -> bool:
    """Canvas면 True. 토큰이 틀려도 Canvas 에러 JSON(401·404)이면 True.

    LearningX는 인증 안 된 /api/v1/users/self에 401이 아니라 404 JSON을 준다. 그 응답도 Canvas 모양이면 받고,
    애매하면 /api/v1/courses로 한 번 더 본다.
    """
    reply = http.request_json("GET", http.url("/api/v1/users/self"))
    if _canvas_shaped(reply):
        return True
    reply = http.request_json("GET", http.url("/api/v1/courses"), params=[("per_page", "1")])
    return _canvas_shaped(reply)


class Canvas:
    kind = KIND

    def __init__(self, http: Http) -> None:
        self.http = http
        self.notes: list[str] = []
        self._token_ok: bool | None = None

    # ── 401 가르기 ──

    def token_works(self) -> bool:
        """토큰이 살아 있는지(/api/v1/users/self가 200). 호출 한 번에 한 번만 묻는다."""
        if self._token_ok is None:
            reply = self.http.request_json("GET", self.http.url("/api/v1/users/self"))
            self._token_ok = reply.status == 200 and isinstance(reply.data, dict) and "id" in reply.data
        return self._token_ok

    def token_rejected(self, reply: Reply) -> bool:
        """401이 "토큰이 틀림·만료"인지(권한 없음이 아니라). 글자가 아니라 헤더와 토큰 확인으로 가른다."""
        if reply.status != 401:
            return False
        if reply.header("www-authenticate"):
            return True
        return not self.token_works()

    def _fail(self, reply: Reply, what: str) -> LmsError:
        config = self.http.config
        if self.token_rejected(reply):
            return LmsError("The LMS rejected the token (invalid, expired or deleted).",
                            how_to_connect(config.school, KIND), 401, "invalidtoken")
        if reply.status in (401, 403):
            return LmsError(f"No permission to read {what} in the LMS (the token works; this part is closed).",
                            status=reply.status)
        if reply.status == 404:
            return LmsError(f"The LMS has no {what} here (HTTP 404).", status=404)
        detail = _message(reply)
        return LmsError(f"The LMS failed to return {what} (HTTP {reply.status}){': ' + detail if detail else ''}.",
                        status=reply.status)

    def _soft(self, reply: Reply) -> bool:
        """대체 경로로 넘어가도 되는 실패(권한 없음·메뉴 숨김·없는 API). 잘못된 토큰은 아니다."""
        return reply.status in (401, 403, 404) and not self.token_rejected(reply)

    def _get(self, path: str, what: str, params: list[tuple[str, str]] | None = None) -> Any:
        reply = self.http.request_json("GET", self.http.url(path), params=params)
        if reply.status != 200:
            raise self._fail(reply, what)
        return reply.data

    def _list(self, path: str, what: str, params: list[tuple[str, str]], max_pages: int = 10) -> list[Any]:
        items, reply = self.http.get_pages(path, params, max_pages)
        if reply.status != 200:
            raise self._fail(reply, what)
        return [it for it in items if isinstance(it, dict)]

    # ── 과목 ──

    def courses(self) -> list[dict[str, Any]]:
        params = [("enrollment_state", "active"), ("per_page", "50"), ("include[]", "term")]
        out = []
        for c in self._list("/api/v1/courses", "courses", params, max_pages=5):
            if c.get("id") is None or not c.get("name") or c.get("access_restricted_by_date"):
                continue
            term = c.get("term") if isinstance(c.get("term"), dict) else {}
            out.append({"id": c["id"], "name": str(c["name"]), "code": c.get("course_code"), "term": term.get("name")})
        return out

    # ── 마감 ──

    def deadlines(self, start: datetime, end: datetime, course_id: Any = None) -> list[dict[str, Any]]:
        params = [("start_date", _zulu(start)), ("end_date", _zulu(end)), ("per_page", "100")]
        if course_id is not None:
            params.append(("context_codes[]", f"course_{course_id}"))
        items, reply = self.http.get_pages("/api/v1/planner/items", params, max_pages=3)
        if reply.status == 200:
            return [d for it in items if isinstance(it, dict) and (d := self._planner_item(it))]
        if not self._soft(reply):
            raise self._fail(reply, "deadlines")
        # 플래너가 꺼진 학교: 다가오는 일정(기간 지정 없음, 대개 1~2주)
        events = self._list("/api/v1/users/self/upcoming_events", "upcoming events", [], max_pages=1)
        return [d for ev in events if (d := self._event_item(ev))]

    def _abs_url(self, raw: Any) -> str | None:
        if not isinstance(raw, str) or not raw:
            return None
        url = urljoin(self.http.config.base + "/", raw)
        return url if self.http.same_origin(url) else None

    def _planner_item(self, it: dict[str, Any]) -> dict[str, Any] | None:
        kind = it.get("plannable_type")
        if kind in ("announcement", "attendance"):
            return None
        p = it.get("plannable") if isinstance(it.get("plannable"), dict) else {}
        due = parse_time(p.get("due_at") or p.get("todo_date") or it.get("plannable_date"))
        if not due:
            return None
        subs = it.get("submissions")
        submitted = bool(subs.get("submitted") or subs.get("excused")) if isinstance(subs, dict) else None
        return {
            "title": str(p.get("title") or p.get("name") or "(untitled)"),
            "course": it.get("context_name"),
            "course_id": it.get("course_id"),
            "type": {"planner_note": "note", "discussion_topic": "discussion"}.get(kind, kind),
            "due": due,
            "submitted": submitted,
            "url": self._abs_url(it.get("html_url")),
        }

    def _event_item(self, ev: dict[str, Any]) -> dict[str, Any] | None:
        assignment = ev.get("assignment") if isinstance(ev.get("assignment"), dict) else {}
        due = parse_time(assignment.get("due_at") or ev.get("start_at"))
        if not due:
            return None
        code = str(ev.get("context_code") or "")
        course_id = assignment.get("course_id") or (code[len("course_"):] if code.startswith("course_") else None)
        submitted = assignment.get("has_submitted_submissions") if assignment else None
        return {
            "title": str(ev.get("title") or assignment.get("name") or "(untitled)"),
            "course": ev.get("context_name"),
            "course_id": course_id,
            "type": "assignment" if assignment else str(ev.get("type") or "event"),
            "due": due,
            "submitted": submitted if isinstance(submitted, bool) else None,
            "url": self._abs_url(ev.get("html_url")),
        }

    # ── 공지 ──

    def announcements(self, courses: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
        names = {f"course_{c['id']}": c["name"] for c in courses}
        out: list[dict[str, Any]] = []
        codes = list(names)
        for i in range(0, len(codes), 20):  # 주소가 너무 길어지지 않게 20과목씩
            params = [("context_codes[]", code) for code in codes[i : i + 20]]
            params += [("start_date", _zulu(start)), ("end_date", _zulu(end)), ("per_page", "50")]
            for it in self._list("/api/v1/announcements", "announcements", params, max_pages=2):
                author = it.get("author") if isinstance(it.get("author"), dict) else {}
                out.append({
                    "course": names.get(str(it.get("context_code"))),
                    "title": str(it.get("title") or "(untitled)"),
                    "posted": parse_time(it.get("posted_at") or it.get("created_at")),
                    "author": author.get("display_name") or it.get("user_name"),
                    "html": it.get("message"),
                    "url": self._abs_url(it.get("html_url")),
                })
        return out

    # ── 파일 ──

    def files(self, course_id: Any) -> tuple[list[dict[str, Any]], str]:
        """(파일 목록, 출처 "files" | "modules"). 파일 메뉴를 숨긴 과목은 모듈에 걸린 파일로 대신한다."""
        items, reply = self.http.get_pages(f"/api/v1/courses/{course_id}/files", [("per_page", "100")], 10)
        if reply.status == 200:
            folders = self._folders(course_id)
            return [self._file(it, folders.get(it.get("folder_id"))) for it in items if isinstance(it, dict)], "files"
        if not self._soft(reply):
            raise self._fail(reply, "course files")
        return self._files_from_modules(course_id), "modules"

    def _folders(self, course_id: Any) -> dict[Any, str | None]:
        items, reply = self.http.get_pages(f"/api/v1/courses/{course_id}/folders", [("per_page", "100")], 3)
        if reply.status != 200:
            return {}
        out: dict[Any, str | None] = {}
        for f in items:
            if isinstance(f, dict) and f.get("id") is not None:
                name = str(f.get("full_name") or f.get("name") or "")
                if name.startswith("course files"):
                    name = name[len("course files") :].lstrip("/")
                out[f["id"]] = name or None
        return out

    @staticmethod
    def _file(it: dict[str, Any], folder: str | None) -> dict[str, Any]:
        return {
            "id": it.get("id"),
            "name": str(it.get("display_name") or it.get("filename") or ""),
            "size": it.get("size") if isinstance(it.get("size"), int) else None,
            "updated": parse_time(it.get("updated_at") or it.get("modified_at") or it.get("created_at")),
            "folder": folder,
            "locked": bool(it.get("locked_for_user") or it.get("hidden_for_user")),
        }

    def _files_from_modules(self, course_id: Any) -> list[dict[str, Any]]:
        params = [("include[]", "items"), ("per_page", "50")]
        found: dict[Any, tuple[str, str | None]] = {}
        modules, reply = self.http.get_pages(f"/api/v1/courses/{course_id}/modules", params, 5)
        if reply.status != 200:
            if self._soft(reply):
                return []  # 파일 메뉴도 모듈도 안 보이는 과목
            raise self._fail(reply, "course modules")
        for module in (m for m in modules if isinstance(m, dict)):
            items = module.get("items")
            if not isinstance(items, list) and isinstance(module.get("id"), int):
                items, reply = self.http.get_pages(
                    f"/api/v1/courses/{course_id}/modules/{module['id']}/items", [("per_page", "100")], 3
                )
                if reply.status != 200:
                    items = []
            for item in items or []:
                if isinstance(item, dict) and item.get("type") == "File" and isinstance(item.get("content_id"), int):
                    found.setdefault(item["content_id"], (str(item.get("title") or ""), module.get("name")))
        out = []
        deadline = time.monotonic() + MODULE_LOOKUP_BUDGET
        for n, (file_id, (title, module_name)) in enumerate(found.items()):
            info = None
            if n < MODULE_FILE_LOOKUPS and time.monotonic() < deadline:
                reply = self.http.request_json("GET", self.http.url(f"/api/v1/files/{file_id}"))
                if reply.status == 200 and isinstance(reply.data, dict):
                    info = self._file(reply.data, module_name)
                elif self.token_rejected(reply):
                    raise self._fail(reply, "files")
            out.append(info or {"id": file_id, "name": title, "size": None, "updated": None,
                                "folder": module_name, "locked": False})
        return out

    def file_for_download(self, course_id: Any, file_id: str) -> dict[str, Any]:
        reply = self.http.request_json("GET", self.http.url(f"/api/v1/courses/{course_id}/files/{file_id}"))
        if reply.status in (401, 403) and not self.token_rejected(reply):
            # 파일 메뉴를 숨긴 과목: 모듈에 걸린 파일은 파일 id로 직접 읽을 수 있다(404면 이 과목 파일이 아니다)
            reply = self.http.request_json("GET", self.http.url(f"/api/v1/files/{file_id}"))
        if reply.status != 200 or not isinstance(reply.data, dict):
            if reply.status == 404:
                raise LmsError(f"No file with id {file_id} in this course.",
                               'Call action="files" for this course and use an id from that list.', 404)
            raise self._fail(reply, "this file")
        info = reply.data
        if info.get("locked_for_user") or not info.get("url"):
            raise LmsError("This file is locked in the LMS (not available yet, or the teacher hid it).")
        return {
            "name": str(info.get("display_name") or info.get("filename") or f"file-{file_id}"),
            "size": info.get("size") if isinstance(info.get("size"), int) else None,
            "url": urljoin(self.http.config.base + "/", str(info["url"])),
            "auth": True,  # 같은 origin일 때만 Bearer 헤더(Http.download가 판단)
        }
