"""캘린더 구독 주소(.ics)로 마감을 읽는다. 토큰을 만들 수 없는 학교(모바일 서비스가 꺼진 Moodle 등)의 길이다.

- Canvas·LearningX: /feeds/calendars/user_<해시>.ics, Moodle·Coursemos: /calendar/export_execute.php?…&authtoken=…
- 주소 자체가 비밀(토큰과 같다)이라 에러에는 호스트 이름만 쓰고, 결과는 tools.lms가 scrub으로 한 번 더 지운다.
- Authorization 헤더는 싣지 않는다. 리디렉션은 같은 origin만(다른 곳으로 가면 대개 로그인 화면이다).
- 상한: 2 MB, 연결 10초·읽기 20초, 전체 30초.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from openstem.lms import ical
from openstem.lms.client import CALENDAR_ENV, LOOPBACK, MAX_REDIRECTS, TIMEOUT, USER_AGENT, LmsError, httpx, origin_of

FEED_DEADLINE = 30.0
_COPY_AGAIN = f"Ask the user to copy the calendar URL again from the LMS calendar into {CALENDAR_ENV}."


def fetch(url: str) -> bytes:
    """캘린더 파일을 받는다(2 MB, 30초). 에러 글에 주소를 싣지 않는다."""
    origin = origin_of(url)
    host = urlsplit(url).hostname or "the LMS"
    deadline = time.monotonic() + FEED_DEADLINE
    headers = {"User-Agent": USER_AGENT, "Accept": "text/calendar, */*;q=0.5"}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=origin[1] not in LOOPBACK,
                      headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = client.send(client.build_request("GET", url), stream=True)
            except httpx.TimeoutException:
                raise LmsError(f"{host} did not send the calendar in time. Try again later.") from None
            except httpx.HTTPError:
                raise LmsError(f"Could not connect to {host} for the calendar feed. Check the network.") from None
            try:
                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                    target = urljoin(str(response.url), response.headers["location"])
                    if origin_of(target) != origin:
                        raise LmsError("The calendar link redirects to another site (maybe a login page).", _COPY_AGAIN)
                    url = target
                    continue
                if response.status_code in (400, 401, 403, 404, 410):
                    raise LmsError(f"The LMS refused the calendar link (HTTP {response.status_code}): "
                                   "it is wrong, expired or was reset.", _COPY_AGAIN, status=response.status_code)
                if response.status_code != 200:
                    raise LmsError(f"The calendar feed failed (HTTP {response.status_code}). Try again later.",
                                   status=response.status_code)
                return _read(response, host, deadline)
            finally:
                response.close()
    raise LmsError("The calendar link redirected too many times.", _COPY_AGAIN)


def _read(response: Any, host: str, deadline: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > ical.MAX_BYTES:
                raise LmsError(f"The calendar is too large (over {ical.MAX_BYTES // 1024 // 1024} MB).")
            if time.monotonic() > deadline:
                raise LmsError(f"{host} is sending the calendar too slowly. Try again later.")
            chunks.append(chunk)
    except httpx.HTTPError:
        raise LmsError(f"The connection to {host} broke while reading the calendar. Try again.") from None
    return b"".join(chunks)


def _web_url(raw: str, feed_url: str) -> str | None:
    """일정의 URL 값: http(s)만(테스트용 loopback 밖에서는 https만), 500자까지."""
    raw = raw.strip()
    parts = urlsplit(raw)
    if parts.scheme == "https" and parts.hostname:
        return raw[:500]
    if parts.scheme == "http" and origin_of(raw)[1] in LOOPBACK and origin_of(feed_url)[1] in LOOPBACK:
        return raw[:500]
    return None


def deadlines(url: str, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], bool]:
    """(마감 목록, 잘렸는지). 항목 모양은 어댑터의 deadlines와 같고 submitted는 늘 None(피드는 제출 여부를 모른다)."""
    body = fetch(url)
    try:
        events, truncated = ical.parse(body, window=(start, end))
    except ical.IcalError as exc:
        hint = None if "large" in str(exc) else _COPY_AGAIN
        raise LmsError(f"The calendar link did not return a calendar ({exc}) - maybe expired or a login page.", hint) \
            from None
    out = []
    for ev in events:
        title, course = ical.summary_course(ev)
        out.append({
            "title": title[:300],
            "course": course[:200] if course else None,
            "course_id": None,
            "type": ical.kind_of(ev),
            "due": ev.when,
            "all_day": ev.all_day,
            "submitted": None,
            "url": _web_url(ev.url, url) if ev.url else None,
            "details": ev.details or None,
            "source": "calendar",
        })
    return out, truncated
