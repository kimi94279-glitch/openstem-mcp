"""LMS HTTP 연결: 설정 읽기, 토큰을 설정한 주소(origin)에만 싣기, 크기·시간 상한.

- 설정 순서: OPENSTEM_LMS_URL(+TYPE)이 있으면 그것, 없으면 OPENSTEM_LMS_SCHOOL을 학교 목록(schools.py)에서 찾아
  주소·종류를 정한다. URL이 LearningX 앞단 포털 같은 목록의 "학생이 아는 주소"면 API 주소로 바꾼다.
- 토큰 없이 OPENSTEM_LMS_CALENDAR_URL(캘린더 구독 주소)만 있으면 캘린더 모드(마감만). 이 주소는 토큰과 같은
  비밀이라 결과·에러에 싣지 않고 scrub이 지운다.
- 주소는 https만(테스트용 127.0.0.1·localhost만 http 허용). 사용자 이름·비밀번호가 든 주소는 거절한다.
- 리디렉션은 httpx에 맡기지 않고 직접 따라간다. 다른 origin으로 넘어가는 요청에는 Authorization 헤더를
  싣지 않고, 주소에 토큰이 든 채로 다른 origin으로 가려 하면 거절한다.
- JSON 응답은 5 MB, 내려받기는 호출한 쪽이 정한 크기까지만 읽는다. 연결 10초·읽기 20초 타임아웃에 더해
  요청 하나에 전체 시간 상한을 둬서 조금씩 흘려 보내는 서버에도 멈추지 않는다. 응답 머리를 기다리는 동안(send)도
  벽시계 감시(send_before)로 묶는다: 시간이 다 되면 연결을 닫아 send를 끝낸다.
- 에러 글에 토큰이 들어가지 않게 httpx 예외 글을 그대로 쓰지 않는다(호스트 이름만).
- 이상한 주소(https://[x/ 처럼 urlsplit가 ValueError를 내는 것)는 도우미가 None·빈 origin으로 받는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

try:  # mcp 2.x는 httpx 대신 같은 API의 httpx2를 의존성으로 깐다
    import httpx2 as httpx
except ImportError:  # pragma: no cover — httpx만 있는 환경
    import httpx  # type: ignore[no-redef]

from openstem import __version__
from openstem.lms import schools
from openstem.lms.schools import School

URL_ENV = "OPENSTEM_LMS_URL"
TOKEN_ENV = "OPENSTEM_LMS_TOKEN"
TYPE_ENV = "OPENSTEM_LMS_TYPE"
SCHOOL_ENV = "OPENSTEM_LMS_SCHOOL"
CALENDAR_ENV = "OPENSTEM_LMS_CALENDAR_URL"
TYPES = ("canvas", "moodle")
MAX_CALENDAR_URL = 2048

TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
MAX_JSON_BYTES = 5 * 1024 * 1024
JSON_DEADLINE = 60.0  # JSON 요청 하나(리디렉션 포함)의 전체 시간 상한(초)
CALL_BUDGET = 90.0  # 툴 호출 한 번이 JSON 요청들에 쓰는 전체 시간(초). 넘으면 다음 요청을 보내지 않는다
DOWNLOAD_DEADLINE = 300.0
MAX_REDIRECTS = 5
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
USER_AGENT = f"openstem-mcp/{__version__} (read-only)"

# 사용자가 로그인 화면 주소를 붙여 넣는 경우가 많아서 흔한 화면 경로는 떼어 낸다.
_PAGE_SUFFIXES = (
    "/login/index.php", "/login/canvas", "/login/ldap", "/login/saml", "/login", "/my/courses.php",
    "/my/index.php", "/my", "/courses", "/dashboard", "/index.php",
)

# 사용자가 LMS 화면에서 메뉴를 찾을 수 있게 한국어 메뉴 이름을 따옴표로 둔다(LLM이 사용자의 말로 옮긴다).
TOKEN_STEPS = {
    "canvas": '"계정" → "설정" → "승인된 통합" → "+ 새 액세스 토큰" '
              "(Account → Settings → Approved Integrations → + New Access Token)",
    "moodle": '"환경설정" → "보안 키" (Preferences → Security keys; page /user/managetoken.php), '
              "copy the mobile web service key",
}
CALENDAR_STEPS = {
    "canvas": '"캘린더" → "캘린더 피드" (Calendar → Calendar Feed), copy the .ics link',
    "moodle": '"달력" → "일정 내보내기" (Calendar → Export calendar) → all events, recent and upcoming '
              "→ Get calendar URL",
    "blackboard": "Calendar → settings → external calendar (iCal) link, if the school allows it",
}
PLATFORM_NAME = {"canvas": "Canvas/LearningX", "moodle": "Moodle/Coursemos", "blackboard": "Blackboard"}
_RESTART = "Enter it in the OpenSTEM extension settings (or env vars) and restart the app."
HOW_TO_CONNECT = (
    f'Set {SCHOOL_ENV} to the school name (e.g. 성균관대; list: lms action="schools"). '
    "Then ask the user to create an access token on the LMS website (never ask for the password): "
    f"Canvas/LearningX: {TOKEN_STEPS['canvas']}. Moodle/Coursemos: {TOKEN_STEPS['moodle']}. "
    f"No token possible? Deadlines only: copy the calendar URL into {CALENDAR_ENV} "
    f"(Canvas: {CALENDAR_STEPS['canvas']}; Moodle: {CALENDAR_STEPS['moodle']}). {_RESTART}"
)


def how_to_connect(school: School | None = None, kind: str | None = None) -> str:
    """연결 방법(토큰 만들기 + 캘린더 주소 대안). 학교·종류를 알면 그 플랫폼 것만."""
    kind = kind or (school.kind if school else None)
    if kind not in TOKEN_STEPS:
        return HOW_TO_CONNECT
    where = school.lms if school else PLATFORM_NAME[kind]
    token = f"Ask the user to create an access token in {where} (never ask for the password): {TOKEN_STEPS[kind]}."
    calendar = f"No token possible? Deadlines only: {CALENDAR_STEPS[kind]}, into {CALENDAR_ENV}."
    if school and school.token == "unlikely":
        return f"{school.notes} Calendar: {CALENDAR_STEPS[kind]}, into {CALENDAR_ENV}. {token} {_RESTART}"
    note = f" {school.notes}" if school and school.token == "unknown" and school.notes else ""
    return f"{token} {calendar}{note} {_RESTART}"


class LmsError(Exception):
    """LLM에게 그대로 보여 줄 짧은 영어 에러. hint는 고치는 방법, status는 HTTP 상태."""

    def __init__(self, message: str, hint: str | None = None, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.hint = hint
        self.status = status
        self.code = code


def canvas_guess(host: str) -> str | None:
    """LearningX 포털 호스트에서 흔한 Canvas 주소를 짐작한다(lms1.knu.ac.kr → https://canvas.knu.ac.kr)."""
    host = _host_key(host)
    if not host or host in LOOPBACK or re.fullmatch(r"[0-9.]+", host) or ":" in host or "." not in host:
        return None
    labels = host.split(".")
    second = ("ac", "co", "or", "go", "re", "ne", "edu")
    n = 3 if len(labels) >= 3 and labels[-2] in second and len(labels[-1]) == 2 else 2
    return "https://canvas." + ".".join(labels[-n:])


def portal_error(url: str) -> LmsError:
    """HTTP 419(Laravel "Page Expired")를 준 주소: LearningX 앞단 포털일 가능성이 크다."""
    host = host_of(url, "the LMS")
    guess = canvas_guess(host)
    return LmsError(
        f"{host} answered HTTP 419 (Page Expired): this looks like a LearningX portal page, not the LMS API.",
        f"Set {SCHOOL_ENV} to the school name, or set {URL_ENV} to the Canvas API host"
        + (f" (often {guess})" if guess else "") + ".",
        status=419, code="portal",
    )


def not_api_error(url: str) -> LmsError:
    """API 주소가 JSON 대신 웹 쪽(HTML)을 준 경우: 대개 포털이나 로그인 화면 주소를 넣었다."""
    host = host_of(url, "the LMS")
    guess = canvas_guess(host)
    return LmsError(
        f"{host} answered with a web page, not LMS API data.",
        f"Set {SCHOOL_ENV} to the school name, or set {URL_ENV} to the LMS API host"
        + (f" (for LearningX often {guess})" if guess else "") + ".",
        code="not_api",
    )


def unsupported_error(school: School) -> LmsError:
    """학생 토큰 API가 없는 학교(ILOS·Blackboard·자체 시스템)."""
    still = []
    if school.calendar:
        still.append(f"deadlines via a calendar link in {CALENDAR_ENV} ({CALENDAR_STEPS[school.calendar]})")
    still.append("download course files from the LMS website into the materials folder and read them with read_document")
    note = f" {school.notes}" if school.notes else ""
    return LmsError(
        f"{school.name} uses {school.lms} ({school.label}), which the lms tool cannot read (no student API token).{note}",
        "What still works: " + "; ".join(still) + ".",
        code="unsupported",
    )


def unknown_school_error(name: str) -> LmsError:
    near = schools.close_matches(name)
    hint = (f"Close matches: {', '.join(s.name for s in near)}. " if near else "") + (
        f'Use a listed name (lms action="schools"), or set {URL_ENV} to the LMS address.'
    )
    return LmsError(f'Unknown school "{name[:40]}" in {SCHOOL_ENV}.', hint, code="unknown_school")


@dataclass(frozen=True)
class Config:
    base: str  # https://host[:port][/하위 경로], 끝 "/" 없음. 캘린더 모드에서는 "" 일 수 있다
    token: str  # 캘린더 모드에서는 ""
    kind: str | None  # canvas | moodle | None(자동 감지)
    calendar: str = ""  # 캘린더 구독 주소(비밀)
    school: School | None = None

    @property
    def mode(self) -> str:
        """api(토큰으로 API) | calendar(캘린더 주소로 마감만)."""
        return "api" if self.token and self.base else "calendar"

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.token, *calendar_secrets(self.calendar))

    @property
    def platform_kind(self) -> str | None:
        """안내에 쓸 종류: 설정·감지한 종류, 아니면 학교 목록의 종류."""
        return self.kind or (self.school.kind if self.school else None)

    @property
    def origin(self) -> tuple[str, str, int]:
        return origin_of(self.base)

    @property
    def loopback(self) -> bool:
        return self.origin[1] in LOOPBACK

    @property
    def host(self) -> str:
        return host_of(self.base)

    @property
    def cache_key(self) -> tuple[str, str]:
        return self.base, hashlib.sha256(self.token.encode("utf-8")).hexdigest()


def _clean(value: str | None) -> str:
    """빈 값과 치환되지 않은 자리표시자("${user_config.lms_url}")는 설정 안 함으로 본다."""
    value = (value or "").strip().strip('"').strip("'").strip()
    if not value or "${" in value or re.fullmatch(r"%[A-Za-z_][A-Za-z0-9_]*%", value):
        return ""
    return value


def _host_key(host: str) -> str:
    host = host.strip("[]").rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return host


def split_url(url: str) -> Any:
    """urlsplit인데 이상한 주소(ValueError: 닫히지 않은 [ 등)면 None."""
    try:
        return urlsplit(url)
    except ValueError:
        return None


def host_of(url: str, default: str = "") -> str:
    parts = split_url(url)
    try:
        return (parts.hostname if parts else None) or default
    except ValueError:
        return default


def join_url(base: str, ref: str) -> str | None:
    """urljoin인데 이상한 주소면 None."""
    try:
        joined = urljoin(base, ref)
    except ValueError:
        return None
    return joined if split_url(joined) is not None else None


def origin_of(url: str) -> tuple[str, str, int]:
    parts = split_url(url)
    if parts is None:
        return "", "", 0  # 어느 origin과도 같지 않다
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    try:
        host = parts.hostname or ""
    except ValueError:
        return "", "", 0
    return scheme, _host_key(host), port or (443 if scheme == "https" else 80)


def normalize_url(raw: str) -> str:
    """사용자가 넣은 LMS 주소를 https://host[:port][/경로]로. 이상하면 LmsError."""
    raw = raw.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = split_url(raw)
    if parts is None:
        raise LmsError(f"OPENSTEM_LMS_URL is not a web address: {raw[:100]}", HOW_TO_CONNECT)
    scheme = parts.scheme.lower()
    if parts.username or parts.password:
        raise LmsError("OPENSTEM_LMS_URL must not contain a user name or password.", HOW_TO_CONNECT)
    host = parts.hostname or ""
    if not host or any(ch.isspace() for ch in host):
        raise LmsError(f"OPENSTEM_LMS_URL is not a web address: {raw[:100]}", HOW_TO_CONNECT)
    if scheme == "http" and _host_key(host) not in LOOPBACK:
        raise LmsError("OPENSTEM_LMS_URL must start with https:// (the token is never sent over plain http).")
    if scheme not in ("http", "https"):
        raise LmsError("OPENSTEM_LMS_URL must start with https://.")
    try:
        _ = parts.port  # 포트가 숫자가 아니면 여기서 ValueError
    except ValueError as exc:
        raise LmsError("OPENSTEM_LMS_URL has an invalid port.") from exc
    path = parts.path.rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _PAGE_SUFFIXES:
            if path.lower().endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((scheme, netloc.lower() if netloc.isascii() else netloc, path, "", ""))


def normalize_calendar(raw: str) -> str:
    """캘린더 구독 주소를 https://로 정리한다. 비밀이라 에러에 값을 싣지 않는다."""
    raw = raw.strip()
    if raw.lower().startswith("webcal://"):
        raw = "https://" + raw[len("webcal://") :]
    if "://" not in raw:
        raw = "https://" + raw
    if len(raw) > MAX_CALENDAR_URL:
        raise LmsError(f"{CALENDAR_ENV} is too long to be a calendar link. Copy it again.")
    parts = split_url(raw)
    if parts is None:
        raise LmsError(f"{CALENDAR_ENV} is not a valid calendar link. Copy it again from the LMS calendar.")
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if parts.username or parts.password or not host or any(ch.isspace() for ch in raw):
        raise LmsError(f"{CALENDAR_ENV} is not a valid calendar link. Copy it again from the LMS calendar.")
    if not (scheme == "https" or (scheme == "http" and _host_key(host) in LOOPBACK)):
        raise LmsError(f"{CALENDAR_ENV} must start with https:// (it is a private link).")
    try:
        _ = parts.port
    except ValueError as exc:
        raise LmsError(f"{CALENDAR_ENV} has an invalid port.") from exc
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def calendar_secrets(url: str) -> list[str]:
    """캘린더 주소에서 지울 비밀 조각: 주소 전체, 긴 마지막 경로 조각(user_<해시>.ics), 긴 쿼리 값(authtoken)."""
    if not url:
        return []
    out = [url]
    parts = split_url(url)
    if parts is None:
        return [x for x in out if len(x) >= 8]
    last = parts.path.rsplit("/", 1)[-1]
    if len(last) >= 12:
        out += [last, re.sub(r"\.ics$", "", last, flags=re.IGNORECASE)]
    out += [v for _, v in parse_qsl(parts.query, keep_blank_values=True) if len(v) >= 8]
    return [x for x in out if len(x) >= 8]


def _school_from_url(base: str, kind: str | None) -> tuple[str, School | None, str | None]:
    """학교 목록에 있는 호스트면 (API 주소, 학교, 종류). 포털이면 API 주소로 바꾼다. 미지원 학교면 LmsError."""
    hit = schools.by_host(base)
    if hit is None:
        return base, None, kind
    school, where = hit
    if where == "site":
        if kind is None:  # 종류를 직접 정했으면 사용자의 판단을 따른다
            raise unsupported_error(school)
        return base, school, kind
    if where == "portal":
        base = normalize_url(school.api)
    return base, school, kind or school.kind


def load_config() -> Config:
    """환경변수에서 설정을 읽는다. 없거나 자리표시자뿐이면 연결 방법을 담은 LmsError.

    순서: OPENSTEM_LMS_URL(+TYPE)이 이긴다 → 없으면 OPENSTEM_LMS_SCHOOL의 주소·종류.
    토큰이 없고 캘린더 주소만 있으면 캘린더 모드(Config.mode == "calendar").
    """
    url = _clean(os.environ.get(URL_ENV))
    token = _clean(os.environ.get(TOKEN_ENV))
    kind = _clean(os.environ.get(TYPE_ENV)).lower() or None
    school_name = _clean(os.environ.get(SCHOOL_ENV))
    calendar_raw = _clean(os.environ.get(CALENDAR_ENV))
    if kind not in (None, *TYPES):
        raise LmsError(f'{TYPE_ENV} must be "canvas", "moodle" or empty (auto-detect).')
    school: School | None = None
    if school_name:
        school = schools.find(school_name)
        if school is None:
            raise unknown_school_error(school_name)
    calendar = normalize_calendar(calendar_raw) if calendar_raw else ""
    if token and (any(ch.isspace() for ch in token) or len(token) > 512):
        raise LmsError(f"{TOKEN_ENV} looks wrong (spaces or too long). Copy the token again.", how_to_connect(school, kind))

    base = ""
    if url and (token or not calendar):
        base, found, kind = _school_from_url(normalize_url(url), kind)
        school = school or found
    elif school and not url:
        if school.supported:
            base, kind = normalize_url(school.api), kind or school.kind
        elif not calendar:
            raise unsupported_error(school)

    if not token and not calendar:
        what = f"for {school.name} " if school else ""
        missing = "no access token or calendar URL" if (school or url) else "no school, token or calendar URL set"
        raise LmsError(f"LMS is not connected {what}({missing}).", how_to_connect(school, kind), code="not_configured")
    if token and not base and not calendar:
        raise LmsError("LMS is not connected (the LMS address is unknown).",
                       f"Set {SCHOOL_ENV} to the school name or {URL_ENV} to the LMS address.", code="not_configured")
    return Config(base=base, token=token if base else "", kind=kind, calendar=calendar, school=school)


_TOKEN_PARAM = re.compile(r"((?:[?&]|&amp;)(?:(?:ws|access_|auth)?token|verifier)=)[^&#\s\"'<>]*", re.IGNORECASE)


def strip_token(text: str, *secrets: str | None) -> str:
    """글·주소에서 토큰·비밀을 지운다(Moodle 파일 주소의 token=, 캘린더 주소의 authtoken=, Canvas verifier= 포함)."""
    for secret in sorted((x for x in secrets if x and len(x) >= 4), key=len, reverse=True):
        text = text.replace(secret, "***")
    return _TOKEN_PARAM.sub(r"\1***", text)


def scrub(value: Any, *secrets: str | None) -> Any:
    """툴 결과 전체에서 토큰·캘린더 주소를 지운다(마지막 안전장치)."""
    if isinstance(value, str):
        return strip_token(value, *secrets)
    if isinstance(value, dict):
        return {k: scrub(v, *secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, *secrets) for v in value]
    return value


class TooSlow(Exception):
    """send_before가 시간이 다 되어 연결을 닫았다."""


def send_before(client: Any, request: Any, deadline: float) -> Any:
    """client.send(stream=True)를 벽시계 deadline까지만 기다린다.

    httpx의 읽기 타임아웃은 읽기 한 번마다라서 응답 머리를 한 글자씩 흘려 보내는 서버에는 끝없이 묶인다.
    send는 작업 스레드에서 하고, 시간이 다 되면 기다리기를 그만두고 TooSlow를 낸다. 늦게 온 응답은 그 스레드가 닫는다
    (그 스레드도 읽기 타임아웃 20초나 Http가 client를 닫을 때 끝난다).
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TooSlow
    box: dict[str, Any] = {}
    done = threading.Event()
    guard = threading.Lock()

    def work() -> None:
        try:
            response = client.send(request, stream=True)
        except BaseException as exc:  # noqa: BLE001 — 부른 쪽 스레드에서 다시 낸다
            box["error"] = exc
            done.set()
            return
        with guard:
            if box.get("abandoned"):
                response.close()
                return
            box["response"] = response
        done.set()

    threading.Thread(target=work, name="openstem-lms-send", daemon=True).start()
    if not done.wait(remaining):
        with guard:
            if "response" not in box:
                box["abandoned"] = True
        if box.get("abandoned"):
            raise TooSlow
    if "error" in box:
        raise box["error"]
    return box["response"]


@dataclass
class Reply:
    status: int
    data: Any  # JSON을 읽은 값. JSON이 아니면 None
    headers: dict[str, str]
    url: str
    html: bool = False  # HTML 쪽(로그인 화면·포털 등)

    def header(self, name: str) -> str:
        name = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == name), "")


@dataclass
class Download:
    path: Path  # 저장 폴더 안의 임시 파일
    size: int
    sha256: str
    content_type: str


class Http:
    """설정한 LMS 하나에만 말을 거는 HTTP 도우미. with 문으로 쓴다."""

    def __init__(self, config: Config, budget: float | None = None) -> None:
        self.config = config
        self.expires = time.monotonic() + (CALL_BUDGET if budget is None else budget)
        # 테스트(127.0.0.1)에서는 사용자의 프록시 환경변수를 타지 않게 한다.
        self.client = httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=not config.loopback,
            headers={"User-Agent": USER_AGENT},
        )

    def __enter__(self) -> Http:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.client.close()

    # ── 주소 검사 ──

    def url(self, path: str) -> str:
        return self.config.base + path

    def same_origin(self, url: str) -> bool:
        return origin_of(url) == self.config.origin

    def allowed_target(self, url: str) -> bool:
        scheme, host, _ = origin_of(url)
        if scheme == "https":
            return bool(host)
        return scheme == "http" and host in LOOPBACK and self.config.loopback

    def _headers(self, url: str, auth: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"} if extra is None else dict(extra)
        if auth and self.same_origin(url):
            headers["Authorization"] = f"Bearer {self.config.token}"
        return headers

    def _network_error(self, exc: Exception, url: str) -> LmsError:
        host = host_of(url, self.config.host)
        if isinstance(exc, httpx.TimeoutException):
            return LmsError(f"{host} did not answer in time (connect 10 s, read 20 s). Try again later.")
        if isinstance(exc, httpx.ConnectError):
            return LmsError(f"Could not connect to {host}. Check OPENSTEM_LMS_URL and the network.")
        return LmsError(f"Network error talking to {host} ({type(exc).__name__}). Try again later.")

    # ── JSON API ──

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: list[tuple[str, str]] | None = None,
        data: dict[str, str] | None = None,
        auth: bool = True,
    ) -> Reply:
        """JSON API 요청. 같은 origin 리디렉션만 따라간다(GET만). 상태 코드는 호출한 쪽이 판단한다."""
        deadline = min(time.monotonic() + JSON_DEADLINE, self.expires)
        for _ in range(MAX_REDIRECTS + 1):
            if time.monotonic() > self.expires:
                raise LmsError(f"{self.config.host} is too slow right now (over {CALL_BUDGET:.0f} s for this call).",
                               "Try again later, or pass course to ask for less.")
            if not self.same_origin(url):
                raise LmsError(
                    f"The LMS sent a request to another site ({host_of(url, 'unknown')}); refused to send the token there.",
                    f"If the school moved its LMS, set {URL_ENV} to the new address.",
                )
            request = self.client.build_request(
                method, url, params=params, data=data, headers=self._headers(url, auth)
            )
            try:
                response = send_before(self.client, request, deadline)
            except TooSlow:
                raise LmsError(f"{self.config.host} is answering too slowly. Try again later.") from None
            except httpx.HTTPError as exc:
                raise self._network_error(exc, url) from None
            try:
                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                    target = join_url(str(response.url), response.headers["location"]) or ""
                    if method != "GET" or not self.same_origin(target):
                        raise LmsError(
                            f"The LMS address redirects to {host_of(target, 'another page')} "
                            "(maybe a login page). The token was not sent there.",
                            f"Check {URL_ENV}: it should be the LMS home address the user logs in to.",
                            status=response.status_code,
                        )
                    url, params = target, None
                    continue
                if response.status_code == 419:  # Laravel CSRF "Page Expired": LearningX 앞단 포털
                    raise portal_error(url)
                body = self._read_capped(response, MAX_JSON_BYTES, deadline)
                kind = response.headers.get("content-type", "").lower()
                head = body[:300].lstrip().lower()
                html = "html" in kind or head.startswith((b"<!doctype html", b"<html"))
                return Reply(response.status_code, _parse_json(body), dict(response.headers), str(response.url), html)
            finally:
                response.close()
        raise LmsError("The LMS redirected too many times.")

    def _read_capped(self, response: Any, limit: int, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    raise LmsError(f"The LMS answer is too large (over {limit // 1024 // 1024} MB).")
                if time.monotonic() > deadline:
                    raise LmsError(f"{self.config.host} is answering too slowly. Try again later.")
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise self._network_error(exc, str(response.url)) from None
        return b"".join(chunks)

    def get_pages(self, path: str, params: list[tuple[str, str]], max_pages: int = 10) -> tuple[list[Any], Reply]:
        """Canvas 목록 API: Link rel="next"를 같은 origin일 때만 따라가며 모은다(최대 max_pages쪽).

        첫 쪽이 200이 아니면 그 Reply를 그대로 돌려준다(호출한 쪽이 상태를 본다).
        """
        items: list[Any] = []
        url, page_params = self.url(path), params
        first: Reply | None = None
        for _ in range(max_pages):
            reply = self.request_json("GET", url, params=page_params)
            if first is None:
                first = reply
                if reply.status == 200 and reply.data is None:
                    raise not_api_error(reply.url)  # 목록 대신 웹 쪽: 빈 목록으로 보이지 않게
            if reply.status != 200:
                return items, reply if first is reply else first
            if isinstance(reply.data, list):
                items.extend(reply.data)
            elif isinstance(reply.data, dict) and first is reply:
                return items, reply  # 목록이 아닌 응답(에러 등)
            next_url = _next_link(reply.header("link"))
            if not next_url:
                break
            next_url = join_url(reply.url, next_url)
            if not next_url or not self.same_origin(next_url):
                break  # 다른 호스트로 가는 다음 쪽에는 토큰을 싣지 않는다
            url, page_params = next_url, None
        assert first is not None
        return items, first

    # ── 내려받기 ──

    def download(self, url: str, folder: Path, max_bytes: int, *, auth: bool = True,
                 seconds: float | None = None) -> Download:
        """파일을 folder 안 임시 파일로 받는다. 리디렉션은 직접 따라가며 다른 origin에는 토큰을 싣지 않는다.

        seconds를 주면 DOWNLOAD_DEADLINE보다 짧게 끊는다(sync가 남은 시간을 넘긴다). 응답 머리도 그 안에서 기다린다.
        """
        limit = DOWNLOAD_DEADLINE if seconds is None else max(1.0, min(DOWNLOAD_DEADLINE, seconds))
        deadline = time.monotonic() + limit
        for _ in range(MAX_REDIRECTS + 1):
            if not self.allowed_target(url):
                raise LmsError("The file link is not https; refused to download it.")
            if not self.same_origin(url) and self.config.token in url:
                raise LmsError("The file link would carry the token to another site; refused.")
            headers = self._headers(url, auth, extra={"Accept": "*/*"})
            try:
                response = send_before(self.client, self.client.build_request("GET", url, headers=headers), deadline)
            except TooSlow:
                raise LmsError(f"The download took over {limit:.0f} s and was stopped.") from None
            except httpx.HTTPError as exc:
                raise self._network_error(exc, url) from None
            try:
                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                    target = join_url(str(response.url), response.headers["location"])
                    if target is None:
                        raise LmsError("The LMS gave a broken download link.")
                    url = target
                    continue
                if response.status_code in (401, 403):
                    raise LmsError("The LMS refused the download (no permission or the file is locked).",
                                   status=response.status_code)
                if response.status_code == 404:
                    raise LmsError("The file was not found on the LMS (deleted or moved).", status=404)
                if response.status_code != 200:
                    raise LmsError(f"Download failed (HTTP {response.status_code}).", status=response.status_code)
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > max_bytes:
                    raise LmsError(f"The file is too large (over {max_bytes // 1024 // 1024} MB).")
                return self._save_stream(response, folder, max_bytes, deadline, limit)
            finally:
                response.close()
        raise LmsError("The download redirected too many times.")

    def _save_stream(self, response: Any, folder: Path, max_bytes: int, deadline: float, limit: float) -> Download:
        digest = hashlib.sha256()
        total = 0
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=".lms-", suffix=".part", dir=folder)
        except OSError as exc:
            raise LmsError(f"Could not write in the materials folder ({type(exc).__name__}).") from None
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise LmsError(f"The file is too large (over {max_bytes // 1024 // 1024} MB).")
                    if time.monotonic() > deadline:
                        raise LmsError(f"The download took over {limit:.0f} s and was stopped.")
                    digest.update(chunk)
                    out.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise self._network_error(exc, str(response.url)) from None
        except OSError as exc:  # 디스크가 꽉 참 등
            tmp.unlink(missing_ok=True)
            raise LmsError(f"Could not save the file ({type(exc).__name__}: disk full or not writable).") from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return Download(tmp, total, digest.hexdigest(), response.headers.get("content-type", ""))


def _parse_json(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace").lstrip("﻿").strip()
    if text.startswith("while(1);"):  # Canvas가 붙이는 JSON 하이재킹 방지 머리
        text = text[len("while(1);") :]
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):  # 아주 깊게 중첩된 JSON도 못 읽은 것으로 본다
        return None


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;(.*)', part)
        if match and re.search(r'rel\s*=\s*"?next"?', match.group(2)):
            return match.group(1).strip()
    return None


def add_query(url: str, **params: str) -> str:
    """주소의 쿼리에 값을 더한다(같은 이름이 있으면 바꾼다)."""
    parts = split_url(url)
    if parts is None:
        raise LmsError("The LMS gave a broken link.")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in params]
    query.extend(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
