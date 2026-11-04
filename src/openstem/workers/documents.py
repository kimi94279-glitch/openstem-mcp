"""read_document 작업자: 자료 파일에서 글자(와 PDF 쪽 그림)를 뽑는다. sandbox.run_python으로만 돈다.

자료 파일은 믿을 수 없는 입력이라(망가진 PDF, 압축 폭탄 등) 서버가 아니라 여기서 시간 제한을 두고 연다.
- PDF: pymupdf로 쪽마다 글자, mode=images면 쪽 그림(PNG)
- HWP(한글 5.x): OLE 안의 BodyText/Section* 레코드에서 문단 글자(HWPTAG_PARA_TEXT)를 읽는다
- HWPX: zip 안 Contents/section*.xml의 <hp:t> 글자
- docx·pptx·xlsx·odt: pandoc(--sandbox)으로 마크다운. 워드 수식은 LaTeX로 나온다
- txt·md·csv: UTF-8, 안 되면 CP949(한국어 Windows)
- mode=problems: 위 글자를 문제 번호(1. 1) (1) [1] 【1】 문제 1 Q1 Problem 1 …)로 나눈다. PDF는 줄 좌표를 같이 모아
  crop(문제 번호 목록)이 오면 그 문제 영역(문제 시작~다음 문제 시작, 쪽·단마다)을 잘라 PNG로 만든다
"""

from __future__ import annotations

import re
import struct
import subprocess
import sys
import zipfile
import zlib
from collections.abc import Iterable, Sequence
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
CROP_DPI = 150
CROP_PAD = 4.0  # 잘라낼 영역 둘레 여백(pt)
MIN_CROP_HEIGHT = 30.0  # 이보다 낮은 영역(다음 문제가 쪽 맨 위에서 시작할 때 남는 머리말 띠)은 버린다
MAX_PREAMBLE = 1500

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


# ── 문제 단위 나누기 ────────────────────────────────────────────────

# (이름, 정규식) 우선순위 순. 줄 맨 앞(들여쓰기·마크다운 꾸밈을 뗀 뒤)에서만 찾는다.
# 원문자 ①②는 객관식 보기라 넣지 않고, (a)·(가) 같은 소문항은 숫자가 아니라 걸리지 않는다.
_MARKERS = [
    ("문제 N", re.compile(r"(?:문제|문항|문)\s*(\d{1,3})(?:\s*번)?(?=[\s.):]|$)")),
    ("Problem N", re.compile(r"(?:problem|question|exercise|q)\s*\.?\s*(\d{1,3})(?=[\s.):]|$)", re.IGNORECASE)),
    ("【N】", re.compile(r"【\s*(\d{1,3})\s*】")),
    ("[N]", re.compile(r"\[\s*(\d{1,3})\s*\](?!\()")),
    ("N번", re.compile(r"(\d{1,3})\s*번(?=[\s.):]|$)")),
    ("N.", re.compile(r"(\d{1,3})\.(?!\d)")),
    ("N)", re.compile(r"(\d{1,3})\)")),
    ("(N)", re.compile(r"\(\s*(\d{1,3})\s*\)")),
]
_DECOR = re.compile(r"^(?:#{1,6}\s+|>\s*|\*\*|__|\*(?=\S))+")
_ESCAPED = re.compile(r"\\([.()\[\]*#])")
_POINTS = re.compile(r"[(\[]\s*(\d+(?:\.\d+)?)\s*(?:점|pts?\.?|points?|marks?)\s*[)\]]", re.IGNORECASE)
# 문제 뒤의 정답·해설 부분. 마지막 문제에 붙지 않게 여기서 끊는다
_ANSWERS = re.compile(r"(?:#{1,6}\s*)?(?:\*\*)?(?:정답(?:과 해설| 및 해설)?|해설|답안|answers?(?: key)?|solutions?)(?:\*\*)?\s*", re.IGNORECASE)


def problem_marker(line: str) -> tuple[str, int] | None:
    """줄이 문제 시작 표지로 시작하면 (표지 이름, 번호)."""
    expanded = line.replace("\t", "    ")
    if len(expanded) - len(expanded.lstrip(" ")) >= 4:  # 들여 쓴 줄(마크다운 중첩 목록)은 소문항으로 본다
        return None
    text = _ESCAPED.sub(r"\1", _DECOR.sub("", expanded.strip()))
    for name, pattern in _MARKERS:
        m = pattern.match(text)
        if m:
            return name, int(m.group(1))
    return None


def _chains(marks: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """(줄 번호, 문제 번호)를 1씩 늘어나는 사슬로 묶는다. 번호가 다시 1로 가면 새 사슬(소문항·지시문)."""
    chains: list[list[tuple[int, int]]] = []
    for mark in marks:
        for chain in reversed(chains):
            if chain[-1][1] == mark[1] - 1:
                chain.append(mark)
                break
        else:
            chains.append([mark])
    return chains


def _nested(inner: list[list[tuple[int, int]]], outer: list[tuple[int, int]]) -> bool:
    """inner 표지의 사슬이 둘 이상이고, 하나하나가 outer 문제 한 칸 안에 들어 있으면 소문항으로 본다."""
    if len(outer) < 2 or len(inner) < 2:
        return False
    starts = [i for i, _ in outer]

    def slot(i: int) -> int:
        return sum(1 for s in starts if s <= i)

    slots = [{slot(i) for i, _ in chain} for chain in inner]
    return all(len(s) == 1 for s in slots) and any(s != {0} for s in slots)


def split_problems(lines: Sequence[str]) -> dict[str, Any]:
    """줄 목록을 문제로 나눈다. {"numbering", "marks": [(줄, 번호)], "end": 문제 부분 끝 줄, "notes"}."""
    end = len(lines)
    notes: list[str] = []
    found: dict[str, list[tuple[int, int]]] = {}
    for i, line in enumerate(lines):
        if found and _ANSWERS.fullmatch(line.strip()):
            end = i
            notes.append("Stopped at the answer section; it is not part of any problem.")
            break
        hit = problem_marker(line)
        if hit:
            found.setdefault(hit[0], []).append((i, hit[1]))
    if not found:
        return {"numbering": None, "marks": [], "end": end, "notes": notes}
    order = [name for name, _ in _MARKERS]
    chains = {name: _chains(marks) for name, marks in found.items()}
    best = {name: max(c, key=lambda ch: (len(ch), ch[0][0])) for name, c in chains.items()}
    kept = [a for a in found if not any(b != a and _nested(chains[a], best[b]) for b in found)] or list(found)
    kept.sort(key=lambda name: (-len(best[name]), order.index(name)))
    name, marks = kept[0], best[kept[0]]
    if len(marks) == 1:
        notes.append("Only one problem number was found; the split may be wrong.")
    others = [n for n in kept[1:] if len(best[n]) >= 2]
    if others:
        notes.append(f'Other numbering ("{others[0]}") was also found and kept inside problems; if the split looks wrong, read with mode="text".')
    restarts = [c for c in chains[name] if c is not marks and len(c) >= 2]
    if restarts:
        notes.append(f"Numbering restarts in the document (instructions or sections); used the longest run {marks[0][1]}-{marks[-1][1]}.")
    return {"numbering": name, "marks": marks, "end": end, "notes": notes}


def points_of(text: str) -> float | int | None:
    m = _POINTS.search(text[:300])
    if not m:
        return None
    value = float(m.group(1))
    return int(value) if value.is_integer() else value


def problems_from_lines(lines: Sequence[str], line_pages: Sequence[int] | None = None) -> dict[str, Any]:
    """split_problems 결과로 문제 목록(번호, 배점, 글자, 쪽)과 머리말을 만든다."""
    split = split_problems(lines)
    marks, end = split["marks"], split["end"]
    problems = []
    for k, (start, number) in enumerate(marks):
        stop = marks[k + 1][0] if k + 1 < len(marks) else end
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines[start:stop]).strip())
        item: dict[str, Any] = {"number": number}
        points = points_of(text)
        if points is not None:
            item["points"] = points
        if line_pages is not None:
            item["pages"] = sorted(set(line_pages[start:stop]))
        item["text"] = text
        problems.append(item)
    preamble = "\n".join(lines[: marks[0][0]]).strip() if marks else ""
    return {
        "ok": True,
        "numbering": split["numbering"],
        "problems": problems,
        "preamble": preamble[:MAX_PREAMBLE],
        "notes": split["notes"],
        "marks": [start for start, _ in marks],
        "end": end,
    }


def _layout(page: pymupdf.Page, boxes: list[tuple[float, float, float, float]]) -> tuple[bool, float]:
    """쪽이 두 단인지(왼쪽·오른쪽 반에만 있는 줄이 많고 걸친 줄이 거의 없음)와 가운데 x."""
    rect = page.rect
    mid, slack = rect.x0 + rect.width / 2, rect.width * 0.02
    left = sum(1 for b in boxes if b[2] <= mid + slack)
    right = sum(1 for b in boxes if b[0] >= mid - slack)
    wide = len(boxes) - left - right
    return left >= 3 and right >= 3 and wide <= 0.1 * len(boxes), mid


def _column(box: Sequence[float], two: bool, mid: float, width: float) -> str:
    if not two:
        return "F"
    if box[2] <= mid + width * 0.02:
        return "L"
    if box[0] >= mid - width * 0.02:
        return "R"
    return "F"


def _content(page: pymupdf.Page, texts: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """쪽의 글자 줄·그림·선 그림 상자. 쪽 전체를 두르는 테두리는 뺀다."""
    rect = page.rect
    boxes = list(texts)
    boxes += [tuple(info["bbox"]) for info in page.get_image_info()]
    for path in page.get_drawings():
        r = path["rect"]
        if r.width > rect.width * 0.9 and r.height > rect.height * 0.9:
            continue
        boxes.append((r.x0, r.y0, r.x1, r.y1))
    return boxes


def crop_problems(doc: pymupdf.Document, boxes: list[tuple], split: dict[str, Any], numbers: list[int]) -> dict[str, Any]:
    """고른 문제의 영역(문제 시작 줄~다음 문제 시작 줄, 쪽·단마다)을 PNG로 자른다. 최대 MAX_IMAGE_PAGES장."""
    starts, end = split["marks"], split["end"]
    by_number = {p["number"]: k for k, p in enumerate(split["problems"])}
    page_lines: dict[int, list[tuple[float, float, float, float]]] = {}
    for b in boxes:
        page_lines.setdefault(b[0], []).append(tuple(b[1:]))
    layouts: dict[int, tuple[bool, float]] = {}
    contents: dict[int, list[tuple[float, float, float, float]]] = {}
    names, regions, total = [], [], 0
    for number in numbers:
        k = by_number.get(number)
        if k is None:
            continue
        first = starts[k]
        stop = starts[k + 1] if k + 1 < len(starts) else end
        groups: dict[tuple[int, str], list[int]] = {}
        for i in range(first, stop):
            p = boxes[i][0]
            if p not in layouts:
                layouts[p] = _layout(doc[p - 1], page_lines[p])
            two, mid = layouts[p]
            groups.setdefault((p, _column(boxes[i][1:], two, mid, doc[p - 1].rect.width)), []).append(i)
        for (p, col), members in groups.items():
            page = doc[p - 1]
            rect = page.rect
            two, mid = layouts[p]
            x0, x1 = {"L": (rect.x0, mid), "R": (mid, rect.x1)}.get(col, (rect.x0, rect.x1))
            if p not in contents:
                contents[p] = _content(page, page_lines[p])
            inside = [b for b in contents[p] if col == "F" or x0 <= (b[0] + b[2]) / 2 <= x1]
            top = boxes[first][2] if first in members else min([b[1] for b in inside] + [boxes[members[0]][2]])
            nxt = boxes[stop] if stop < len(boxes) else None  # 다음 문제(또는 정답 부분)의 첫 줄
            limit = rect.y1
            if nxt is not None and nxt[0] == p and _column(nxt[1:], two, mid, rect.width) == col and nxt[2] > top:
                limit = nxt[2]  # 다음 문제가 같은 쪽·단에서 시작하면 거기까지
            below = [b[3] for b in inside if b[1] >= top - 1 and b[3] <= limit + 1]
            bottom = max(below + [boxes[i][4] for i in members])
            if bottom - top < MIN_CROP_HEIGHT and first not in members:
                continue
            total += 1
            if len(names) >= MAX_IMAGE_PAGES:
                continue
            clip = pymupdf.Rect(x0, top - CROP_PAD, x1, min(bottom + CROP_PAD, limit - 0.5)) & rect
            longest_in = max(clip.width, clip.height) / 72
            dpi = max(30, min(CROP_DPI, int(IMAGE_MAX_PX / longest_in))) if longest_in else CROP_DPI
            name = f"problem{number}_p{p}{col if col != 'F' else ''}.png"
            page.get_pixmap(dpi=dpi, clip=clip, alpha=False).save(name)
            names.append(name)
            regions.append({"number": number, "page": p})
    return {"images": names, "image_regions": regions, "regions_total": total}


_SCANNED_ERROR = (
    ["The PDF pages have no text layer (scans or photos), so problems cannot be split by number."],
    'Read the pages with mode="images" and find the problem yourself.',
)


def _pdf_problems(path: Path, pages: list[int] | None, crop: list[int] | None) -> dict[str, Any]:
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise WorkerError(["Cannot read a password-protected PDF."], "Ask the user to put an unlocked PDF in the materials folder.")
        count = doc.page_count
        wanted = [p for p in (pages or range(1, count + 1)) if 1 <= p <= count]
        if pages and not wanted:
            raise WorkerError([f"pages out of range: this PDF has pages 1-{count}."])
        lines: list[str] = []
        boxes: list[tuple] = []
        scanned = []
        for p in wanted:
            page = doc[p - 1]
            before = len(lines)
            for block in page.get_text("dict", flags=flags)["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    if text:
                        lines.append(text)
                        boxes.append((p, *line["bbox"]))
            if sum(len(t) for t in lines[before:]) < SCANNED_CHARS and page.get_images():
                scanned.append(p)
        if scanned and sum(len(t) for t in lines) < SCANNED_CHARS * len(wanted):
            raise WorkerError(*_SCANNED_ERROR)
        result = problems_from_lines(lines, [b[0] for b in boxes])
        result.update(pages=count, scanned_pages=scanned[:20])
        if crop and result["problems"]:
            result.update(crop_problems(doc, boxes, result, crop))
    return result


def _text_problems(text: str) -> dict[str, Any]:
    return problems_from_lines(text.splitlines())


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


def _read_text(path: Path, kind: str, payload: dict[str, Any]) -> str:
    if kind == "hwp":
        return _hwp(path)
    if kind == "hwpx":
        return _hwpx(path)
    if kind == "pandoc":
        return _pandoc(path, payload["pandoc"])
    if kind == "text":
        return _text(path)
    raise WorkerError([f"Unknown format: {kind}"])


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    path, kind = Path(payload["path"]), payload["kind"]
    if payload.get("mode") == "problems":
        if kind == "pdf":
            return _pdf_problems(path, payload.get("pages"), payload.get("crop"))
        return _text_problems(_read_text(path, kind, payload))
    if kind == "pdf":
        return _pdf(path, payload.get("pages"), payload.get("mode", "text"))
    return {"ok": True, "text": _read_text(path, kind, payload)}


if __name__ == "__main__":
    serve(handle)
