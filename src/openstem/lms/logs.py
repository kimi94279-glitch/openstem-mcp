"""로그에서 LMS 비밀 가리기: 토큰·캘린더 주소 조각·token=/authtoken=/wstoken= 값·Canvas verifier=.

stdio 모드에서는 서버의 stderr가 호스트의 로그 파일(예: Claude Desktop의 mcp-server-openstem.log)에 그대로 쌓인다.
MCP SDK가 루트 로깅을 INFO로 두고, httpx2는 요청마다 "HTTP Request: GET <전체 주소>"를 INFO로 찍는다. Moodle 파일
주소(token=)와 캘린더 구독 주소는 그 자체가 비밀이라 그대로 남으면 안 된다.

- install(): httpx·httpcore 로거를 WARNING으로 낮추고, 지금 있는 모든 로그 핸들러에 SecretFilter를 단다.
  서버가 시작할 때(stdio·HTTP 공통) 부르고, HTTP 모드는 uvicorn이 핸들러를 만든 뒤 한 번 더 부른다.
- 가리는 기준은 툴 결과의 client.scrub과 같다(client.strip_token). 비밀 값은 기록마다 환경변수에서 읽는다.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from openstem.lms.client import CALENDAR_ENV, TOKEN_ENV, LmsError, _clean, calendar_secrets, normalize_calendar, strip_token

QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")

_cache: tuple[tuple[str, str], tuple[str, ...]] | None = None


def current_secrets() -> tuple[str, ...]:
    """지금 설정된 토큰·캘린더 주소에서 가릴 값들(환경변수가 바뀌면 다시 계산한다)."""
    global _cache
    token = _clean(os.environ.get(TOKEN_ENV))
    calendar = _clean(os.environ.get(CALENDAR_ENV))
    key = (token, calendar)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    values = [token] if token else []
    if calendar:
        values += calendar_secrets(calendar)
        try:
            values += calendar_secrets(normalize_calendar(calendar))
        except LmsError:
            pass
    secrets = tuple(dict.fromkeys(v for v in values if v))
    _cache = (key, secrets)
    return secrets


def redact(text: str) -> str:
    return strip_token(text, *current_secrets())


class SecretFilter(logging.Filter):
    """로그 줄과 예외 글에서 LMS 비밀을 가린다. 기록은 늘 통과시킨다(가리기만 한다)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — 서식이 깨진 기록은 손대지 않는다
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
            record.__dict__.pop("color_message", None)  # uvicorn 서식이 원래 형식 문자열을 다시 쓰지 않게
        if record.exc_info and record.exc_info[1] is not None:
            # RichHandler는 exc_text가 아니라 exc_info로 traceback을 그리므로, 비밀이 있으면 가린 글로 바꿔 싣는다
            text = record.exc_text or logging.Formatter().formatException(record.exc_info)
            scrubbed = redact(text)
            if scrubbed != text:
                record.msg, record.args = f"{record.msg}\n{scrubbed}", None
                record.exc_info, record.exc_text = None, None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _all_handlers() -> list[logging.Handler]:
    loggers: list[Any] = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    out: list[logging.Handler] = []
    for lg in loggers:
        if isinstance(lg, logging.Logger):
            out.extend(h for h in lg.handlers if h not in out)
    if logging.lastResort is not None and logging.lastResort not in out:  # 핸들러가 없을 때 쓰는 stderr 핸들러
        out.append(logging.lastResort)
    return out


def install() -> None:
    """httpx·httpcore 요청 로그를 끄고(WARNING 이상만), 모든 로그 핸들러에 SecretFilter를 단다. 여러 번 불러도 된다."""
    for name in QUIET_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET or logger.level < logging.WARNING:
            logger.setLevel(logging.WARNING)
    for handler in _all_handlers():
        if not any(isinstance(f, SecretFilter) for f in handler.filters):
            handler.addFilter(SecretFilter())
