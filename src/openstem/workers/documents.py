"""read_document 작업자: 자료 파일에서 글자(와 PDF 쪽 그림)를 뽑는다. sandbox.run_python으로만 돈다.

자료 파일은 믿을 수 없는 입력이라(망가진 PDF, 압축 폭탄 등) 서버가 아니라 여기서 시간 제한을 두고 연다.
- PDF: pymupdf로 쪽마다 글자, mode=images면 쪽 그림(PNG)
- HWP(한글 5.x): OLE 안의 BodyText/Section* 레코드에서 문단 글자(HWPTAG_PARA_TEXT)를 읽는다
- HWPX: zip 안 Contents/section*.xml의 <hp:t> 글자
- docx·pptx·xlsx·odt: pandoc(--sandbox)으로 마크다운. 워드 수식은 LaTeX로 나온다
- txt·md·csv: UTF-8, 안 되면 CP949(한국어 Windows)
"""

from __future__ import annotations

import re
import struct
import subprocess
import sys
import zipfile
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import olefile
import pymupdf

from openstem.workers import WorkerError, serve

MAX_UNPACKED = 100 * 1024 * 1024  # 압축을 푼 크기 상한(압축 폭탄 방지). HWP는 섹션 하나, HWPX는 본문 전체
MAX_UNPACKED_TOTAL = 200 * 1024 * 1024  # HWP 섹션들을 다 합친 상한(섹션마다 100 MB씩 수십 개를 막는다)
MAX_IMAGE_PAGES = 4
IMAGE_DPI = 110
IMAGE_MAX_PX = 1600
PANDOC_TIMEOUT = 15.0
SCANNED_CHARS = 20  # 쪽에 글자가 이만큼도 없으면 스캔본일 수 있다

HWPTAG_PARA_TEXT = 67  # HWPTAG_BEGIN(16) + 51
# 문단 글자 안의 제어 문자: 0·10·13·24~31은 한 글자, 나머지(1~9, 11~12, 14~23)는 8글자(16바이트)를 차지한다.
_ONE_UNIT_CONTROLS = {0, 10, 13, *range(24, 32)}


# ── PDF ─────────────────────────────────────────────────────────────


def _pdf(path: Path, pages: list[int] | None, mode: str) -> dict[str, Any]:
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise WorkerError(["Cannot read a password-protected PDF."], "Ask the user to put an unlocked PDF in the materials folder.")
        count = doc.page_count
        wanted = [p for p in (pages or range(1, count + 1)) if 1 <= p <= count]
        if pages and not wanted:
            raise WorkerError([f"pages out of range: this PDF has pages 1-{count}."])
        if mode == "images":
            names = []
            for p in wanted[:MAX_IMAGE_PAGES]:
                page = doc[p - 1]
                longest_in = max(page.rect.width, page.rect.height) / 72
                dpi = min(IMAGE_DPI, int(IMAGE_MAX_PX / longest_in)) if longest_in else IMAGE_DPI
                name = f"page{p}.png"
                page.get_pixmap(dpi=max(dpi, 30), alpha=False).save(name)
                names.append(name)
            return {"ok": True, "pages": count, "images": names, "image_pages": wanted[:MAX_IMAGE_PAGES]}
        texts = [[p, doc[p - 1].get_text("text").strip()] for p in wanted]
        # 글자가 거의 없고 그림이 있는 쪽 = 스캔본일 가능성(짧은 표지 쪽은 그림이 없어서 빠진다)
        scanned = [p for p, t in texts if len(t) < SCANNED_CHARS and doc[p - 1].get_images()]
    return {"ok": True, "pages": count, "page_texts": texts, "scanned_pages": scanned[:20]}


# ── HWP(한글 5.x) ───────────────────────────────────────────────────


def para_text(raw: bytes) -> str:
    """HWPTAG_PARA_TEXT 레코드 본문(UTF-16LE)에서 글자만 뽑는다."""
    units = struct.unpack(f"<{len(raw) // 2}H", raw[: len(raw) // 2 * 2])
    kept: list[int] = []
    k = 0
    while k < len(units):
        code = units[k]
        if code >= 32:
            kept.append(code)
            k += 1
        elif code in _ONE_UNIT_CONTROLS:
            if code == 10:
                kept.append(10)
            k += 1
        else:
            if code == 9:
                kept.append(9)  # 탭
            k += 8
    return struct.pack(f"<{len(kept)}H", *kept).decode("utf-16-le", errors="replace")


def hwp_records(data: bytes) -> list[str]:
    """BodyText 섹션 스트림(압축 푼 것)의 레코드를 돌며 문단 글자를 모은다."""
    texts, i = [], 0
    while i + 4 <= len(data):
        (header,) = struct.unpack_from("<I", data, i)
        i += 4
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        if size == 0xFFF:
            if i + 4 > len(data):
                break
            (size,) = struct.unpack_from("<I", data, i)
            i += 4
        if tag == HWPTAG_PARA_TEXT:
            text = para_text(data[i : i + size]).strip()
            if text:
                texts.append(text)
        i += size
    return texts


TOO_LARGE_TOTAL = f"The HWP file is too large when unpacked (over {MAX_UNPACKED_TOTAL // 1024 // 1024} MB in total)."


def _inflate(data: bytes, limit: int = MAX_UNPACKED) -> bytes:
    inflater = zlib.decompressobj(-15)
    out = inflater.decompress(data, limit)
    if inflater.unconsumed_tail:
        raise WorkerError([TOO_LARGE_TOTAL if limit < MAX_UNPACKED else "The HWP file is too large when unpacked."])
    return out


def hwp_body(streams: Iterable[bytes], compressed: bool) -> list[str]:
    """BodyText 섹션 스트림들의 문단 글자. 푼 크기는 섹션마다 MAX_UNPACKED, 합쳐서 MAX_UNPACKED_TOTAL까지."""
    paragraphs: list[str] = []
    total = 0
    for data in streams:
        left = MAX_UNPACKED_TOTAL - total
        if left <= 0 and data:  # zlib은 길이 0을 '제한 없음'으로 읽는다
            raise WorkerError([TOO_LARGE_TOTAL])
        if compressed:
            data = _inflate(data, min(MAX_UNPACKED, left))
        elif len(data) > left:
            raise WorkerError([TOO_LARGE_TOTAL])
        total += len(data)
        paragraphs += hwp_records(data)
    return paragraphs


def _hwp(path: Path) -> str:
    if not olefile.isOleFile(str(path)):
        raise WorkerError(["Not an HWP 5.x file. If it is an old HWP 3.0 file, ask the user to re-save it in Hangul (the word processor)."])
    with olefile.OleFileIO(str(path)) as ole:
        header = ole.openstream("FileHeader").read()
        (flags,) = struct.unpack_from("<I", header, 36)
        if flags & 0x2:
            raise WorkerError(["Cannot read a password-protected HWP file."])
        if flags & 0x4:
            raise WorkerError(
                ["Cannot extract text from a distribution (read-only) HWP document."],
                "Ask the user to save it as PDF in Hangul and put it in the materials folder.",
            )
        sections = [e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")]
        sections.sort(key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))
        paragraphs = hwp_body((ole.openstream(entry).read() for entry in sections), bool(flags & 0x1))
    return "\n".join(paragraphs)


# ── HWPX ────────────────────────────────────────────────────────────


def _hwpx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)]
        names.sort(key=lambda n: int(re.sub(r"\D", "", n)))
        if not names:
            raise WorkerError(["No body (Contents/section*.xml) found in the HWPX file."])
        if sum(zf.getinfo(n).file_size for n in names) > MAX_UNPACKED:
            raise WorkerError(["HWPX body is too large."])
        paragraphs = []
        for name in names:
            root = ElementTree.fromstring(zf.read(name))
            for para in root.iter():
                if para.tag.endswith("}p"):
                    text = "".join(t.text or "" for t in para.iter() if t.tag.endswith("}t")).strip()
                    if text:
                        paragraphs.append(text)
    return "\n".join(paragraphs)


# ── 그 밖 ───────────────────────────────────────────────────────────


def _pandoc(path: Path, pandoc: str) -> str:
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        res = subprocess.run(
            [pandoc, str(path), "-t", "markdown-raw_html-raw_attribute", "--wrap=none", "--sandbox"],
            capture_output=True,
            timeout=PANDOC_TIMEOUT,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise WorkerError([f"Document conversion timed out ({PANDOC_TIMEOUT:.0f} s)."]) from None
    if res.returncode != 0:
        detail = res.stderr.decode("utf-8", errors="replace").strip().splitlines()[:2]
        raise WorkerError(["Could not read the document: " + " / ".join(detail)[:200]])
    return res.stdout.decode("utf-8", errors="replace")


def _text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    path, kind = Path(payload["path"]), payload["kind"]
    if kind == "pdf":
        return _pdf(path, payload.get("pages"), payload.get("mode", "text"))
    if kind == "hwp":
        return {"ok": True, "text": _hwp(path)}
    if kind == "hwpx":
        return {"ok": True, "text": _hwpx(path)}
    if kind == "pandoc":
        return {"ok": True, "text": _pandoc(path, payload["pandoc"])}
    if kind == "text":
        return {"ok": True, "text": _text(path)}
    raise WorkerError([f"Unknown format: {kind}"])


if __name__ == "__main__":
    serve(handle)
