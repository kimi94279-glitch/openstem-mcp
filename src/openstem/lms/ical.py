"""iCalendar(.ics) 읽기(표준 라이브러리만): LMS 캘린더 구독 주소로 받은 일정에서 마감에 쓸 것만 뽑는다.

- 줄 접기(RFC 5545 3.1)는 글자로 바꾸기 전에 바이트에서 푼다(75바이트에서 접으면 한글 한 글자가 둘로 갈린다).
- VEVENT·VTODO만 본다. RRULE(반복)은 첫 번째만, STATUS:CANCELLED는 뺀다.
- 시각: UTC "Z", TZID(zoneinfo. Windows에 tzdata가 없으면 Asia/Seoul은 +09:00, 그 밖에는 파일의 VTIMEZONE
  오프셋), 시간대 없는 시각은 이 PC 시간대, 종일(DATE) 일정은 그날 23:59(이 PC 시간대)로 본다.
- 상한: 파일 2 MB, 남기는 일정 500개(기간을 주면 기간 안의 것만 센다), 값 하나 20,000자.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 500
VALUE_LIMIT = 20_000
DETAILS_LIMIT = 200  # DESCRIPTION을 돌려줄 때 글자 수

SEOUL = timezone(timedelta(hours=9), "KST")
_SEOUL_NAMES = {"asia/seoul", "korea standard time", "kst", "rok", "korea", "seoul"}


class IcalError(ValueError):
    """캘린더 파일이 아니거나 너무 크다."""


@dataclass
class Event:
    uid: str = ""
    summary: str = ""
    start: datetime | date | None = None
    end: datetime | date | None = None
    due: datetime | date | None = None
    url: str = ""
    description: str = ""
    categories: list[str] = field(default_factory=list)
    component: str = "VEVENT"
    status: str = ""

    @property
    def all_day(self) -> bool:
        value = self.due or self.start or self.end
        return isinstance(value, date) and not isinstance(value, datetime)

    @property
    def when(self) -> datetime | None:
        """마감으로 볼 시각: DUE(VTODO) → DTSTART → DTEND. 종일 일정은 그날 23:59(이 PC 시간대)."""
        value = self.due or self.start or self.end
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return _local(datetime(value.year, value.month, value.day, 23, 59))

    @property
    def details(self) -> str:
        text = re.sub(r"\s+", " ", self.description).strip()
        return text if len(text) <= DETAILS_LIMIT else text[: DETAILS_LIMIT - 1].rstrip() + "…"


def _local(dt: datetime) -> datetime | None:
    """시간대 없는 시각을 이 PC 시간대로. Windows는 1970년 전·아주 먼 날짜에 OSError를 내서 None."""
    try:
        return dt.astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _load_zone(name: str) -> tzinfo:
    """시간대 이름 → tzinfo. 시험이 tzdata 없는 Windows를 흉내 내려고 바꿔 끼운다."""
    return ZoneInfo(name)


def unfold(data: bytes) -> list[str]:
    """줄 끝을 맞추고 접힌 줄(줄바꿈 뒤 공백·탭 한 글자)을 바이트에서 이은 뒤 UTF-8 글자 줄로."""
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    data = re.sub(rb"\n[ \t]", b"", data)
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    return text.split("\n")


def _split(line: str) -> tuple[str, dict[str, str], str] | None:
    """'NAME;P=V;Q="a:b":VALUE' → (NAME, {P: V, Q: a:b}, VALUE). 따옴표 안의 ':'·';'는 나누지 않는다."""
    quoted = False
    cuts: list[int] = []
    colon = -1
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == ";":
            cuts.append(i)
        elif not quoted and ch == ":":
            colon = i
            break
    if colon <= 0:
        return None
    head, value = line[:colon], line[colon + 1 :]
    bounds = [-1, *cuts, len(head)]
    pieces = [head[a + 1 : b] for a, b in zip(bounds, bounds[1:])]
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        k, _, v = piece.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return pieces[0].strip().upper(), params, value[:VALUE_LIMIT]


def unescape(value: str) -> str:
    """TEXT 값의 \\n \\, \\; \\\\ 를 푼다."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _offset(raw: str) -> tzinfo | None:
    m = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?", raw.strip())
    if not m:
        return None
    delta = timedelta(hours=int(m[2]), minutes=int(m[3]), seconds=int(m[4] or 0))
    if delta >= timedelta(hours=24):
        return None
    return timezone(-delta if m[1] == "-" else delta)


def _zone(tzid: str, offsets: dict[str, tzinfo]) -> tzinfo | None:
    name = tzid.strip().strip('"')
    tail = "/".join(name.split("/")[-2:])  # "/mozilla.org/20050126_1/Asia/Seoul" → "Asia/Seoul"
    for candidate in dict.fromkeys((name, tail)):
        try:
            return _load_zone(candidate)
        except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError(tzdata 없음)·이상한 이름(ValueError)
            continue
    low = name.lower()
    if low in _SEOUL_NAMES or low.endswith("asia/seoul"):
        return SEOUL
    return offsets.get(name)  # 파일의 VTIMEZONE 오프셋. 그것도 없으면 None → 이 PC 시간대


def parse_time(value: str, params: dict[str, str], offsets: dict[str, tzinfo]) -> datetime | date | None:
    """DATE(YYYYMMDD) → date, DATE-TIME → 시간대가 붙은 datetime. 못 읽으면 None."""
    value = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
            m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value[:8])
            return date(int(m[1]), int(m[2]), int(m[3])) if m else None
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?(Z)?", value, re.IGNORECASE)
        if not m:
            return None
        dt = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), min(int(m[6] or 0), 59))
    except ValueError:  # 13월 같은 값
        return None
    if m[7]:
        return dt.replace(tzinfo=timezone.utc)
    zone = _zone(params["TZID"], offsets) if params.get("TZID") else None
    return dt.replace(tzinfo=zone) if zone else _local(dt)  # 시간대 없는 시각은 이 PC 시간대


def _categories(value: str) -> list[str]:
    return [c for c in (unescape(part).strip() for part in re.split(r"(?<!\\),", value)) if c]


def parse(data: bytes, window: tuple[datetime, datetime] | None = None) -> tuple[list[Event], bool]:
    """(일정 목록, 잘렸는지). window를 주면 when이 그 안에 드는 일정만 남긴다(취소된 일정은 늘 뺀다)."""
    if len(data) > MAX_BYTES:
        raise IcalError(f"The calendar is too large (over {MAX_BYTES // 1024 // 1024} MB).")
    lines = unfold(data)
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if first.upper() != "BEGIN:VCALENDAR":
        raise IcalError("This is not an iCalendar file.")

    events: list[Event] = []
    truncated = False
    stack: list[str] = []
    offsets: dict[str, tzinfo] = {}
    pending: list[tuple[str, dict[str, str], str]] = []  # 시간 값은 VTIMEZONE을 다 읽은 뒤 해석한다
    current: Event | None = None
    tz_name = ""
    raw_events: list[tuple[Event, list[tuple[str, dict[str, str], str]]]] = []

    for line in lines:
        parsed = _split(line.rstrip())
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN":
            kind = value.strip().upper()
            stack.append(kind)
            if kind in ("VEVENT", "VTODO") and current is None:
                current, pending = Event(component=kind), []
            elif kind == "VTIMEZONE":
                tz_name = ""
            continue
        if name == "END":
            kind = value.strip().upper()
            if stack and stack[-1] == kind:
                stack.pop()
            if kind in ("VEVENT", "VTODO") and current is not None:
                raw_events.append((current, pending))
                current = None
            continue
        inside = stack[-1] if stack else ""
        if current is not None and inside == current.component:
            if name in ("DTSTART", "DTEND", "DUE"):
                pending.append((name, params, value))
            elif name == "SUMMARY":
                current.summary = unescape(value).strip()
            elif name == "UID":
                current.uid = value.strip()
            elif name == "URL":
                current.url = value.strip()
            elif name == "DESCRIPTION":
                current.description = unescape(value)
            elif name == "CATEGORIES":
                current.categories.extend(_categories(value))
            elif name == "STATUS":
                current.status = value.strip().upper()
        elif "VTIMEZONE" in stack:
            if name == "TZID" and inside == "VTIMEZONE":
                tz_name = value.strip()
            elif name == "TZOFFSETTO" and inside in ("STANDARD", "DAYLIGHT") and tz_name:
                zone = _offset(value)
                if zone and (inside == "STANDARD" or tz_name not in offsets):
                    offsets[tz_name] = zone

    for event, times in raw_events:
        for name, params, value in times:
            setattr(event, {"DTSTART": "start", "DTEND": "end", "DUE": "due"}[name], parse_time(value, params, offsets))
        if event.status == "CANCELLED" or event.when is None:
            continue
        if window and not (window[0] <= event.when <= window[1]):
            continue
        if len(events) >= MAX_EVENTS:
            truncated = True
            break
        events.append(event)
    return events, truncated


def summary_course(event: Event) -> tuple[str, str | None]:
    """(제목, 과목). Moodle은 CATEGORIES에 과목을, Canvas는 SUMMARY 끝 대괄호에 과목을 적는다("과제 [과목]")."""
    title = event.summary or "(untitled)"
    if event.categories:
        return title, event.categories[0]
    m = re.fullmatch(r"(.*\S)\s*\[([^\[\]]{1,200})\]\s*", title, re.DOTALL)
    if m:
        return m[1].strip(), m[2].strip()
    return title, None


def kind_of(event: Event) -> str:
    """UID·URL 모양으로 종류를 짐작한다(Canvas "event-assignment-12", Moodle "/mod/quiz/")."""
    text = f"{event.uid} {event.url}".lower()
    m = re.search(r"/mod/([a-z]+)/", text)
    if m:
        return m[1]
    for word in ("assignment", "quiz", "discussion"):
        if word in text:
            return word
    return "todo" if event.component == "VTODO" else "event"
