"""LMS 어댑터가 같이 쓰는 도우미: HTML→글자, 시각 표기, 저장할 파일·폴더 이름 정리."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

BODY_LIMIT = 1500  # 공지 본문 한 건에 돌려주는 글자 수
NAME_LIMIT = 120  # 저장할 파일·폴더 이름 길이 상한(글자)

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "hr", "section", "article", "header", "footer",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "head", "title", "svg", "iframe", "object"}


class _TextParser(HTMLParser):
    """HTML에서 보이는 글자만 모은다. script·style 등은 통째로 버린다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n- " if tag == "li" else "\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: Any, limit: int = BODY_LIMIT) -> str:
    """LMS 글 본문(HTML)을 짧은 평문으로. 길면 limit 글자에서 자르고 …를 붙인다."""
    if not isinstance(html, str) or not html:
        return ""
    parser = _TextParser()
    try:
        parser.feed(html[: limit * 40])  # 아주 긴 본문은 앞부분만 해석한다
        parser.close()
    except Exception:  # noqa: BLE001 — 깨진 HTML이어도 모은 데까지 쓴다
        pass
    text = "".join(parser.parts).replace("\xa0", " ").replace("\r", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def parse_time(value: Any) -> datetime | None:
    """ISO 8601 문자열(Canvas)이나 유닉스 초(Moodle)를 UTC datetime으로. 못 읽으면 None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return parse_time(int(raw))
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def iso(dt: datetime | None) -> str | None:
    """이 PC의 시간대로 분 단위 ISO 8601(예: 2026-11-05T23:59+09:00)."""
    return dt.astimezone().isoformat(timespec="minutes") if dt else None


def due_in(dt: datetime, now: datetime) -> str:
    """남은 시간을 "3d 4h" / "5h 10m" / "12m" / "overdue"로."""
    seconds = int((dt - now).total_seconds())
    if seconds < 0:
        return "overdue"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in [*"123456789", "¹", "²", "³"]
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def safe_name(raw: Any, fallback: str = "file", basename: bool = True) -> str:
    """LMS가 준 이름을 이 PC에 저장해도 안전한 한 칸짜리 이름으로.

    basename=True(파일 이름)면 경로 구분자 앞을 버리고 마지막 조각만 쓴다("../../x.pdf" → "x.pdf").
    폴더 이름(과목명)은 구분자를 "_"로 바꾼다("물리 I/II" → "물리 I_II").
    제어 문자·Windows 금지 글자, "..", 끝의 점·공백, 예약 이름(CON, NUL, COM1 …)을 막고 길이를 줄인다.
    """
    name = unicodedata.normalize("NFC", str(raw or ""))
    if basename:
        name = re.split(r"[/\\]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = _BAD_CHARS.sub("_", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    stem = name.split(".")[0].strip().upper()
    if stem in _WINDOWS_RESERVED:
        name = "_" + name
    if len(name) > NAME_LIMIT:
        dot = name.rfind(".")
        ext = name[dot:] if 0 < dot and len(name) - dot <= 10 else ""
        name = name[: NAME_LIMIT - len(ext)].rstrip(" .") + ext
    return name or fallback
