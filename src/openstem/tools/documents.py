"""read_document: 자료 폴더의 PDF·한글(HWP/HWPX)·워드·파워포인트·엑셀·텍스트·그림 파일을 읽는다.

AI 앱은 채팅에 첨부한 파일만 읽을 수 있고, 한글 파일은 아예 열지 못한다. 이 툴은 사용자가 자료 폴더에
넣어 둔 파일을 AI가 이름으로 골라 읽게 한다. 아무 경로나 열게 두면 프롬프트 주입으로 개인 파일이 새어 나갈 수
있으므로, 자료 폴더(OPENSTEM_INPUT_DIR, 기본 결과물 폴더/자료)와 결과물 폴더 안 파일만 연다.
파일 해석은 작업자(openstem.workers.documents) 서브프로세스에서 한다.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from openstem import output, sandbox
from openstem.render import pages as page_images
from openstem.tools import ToolResult

INPUT_DIR_ENV = "OPENSTEM_INPUT_DIR"
KINDS = {
    ".pdf": "pdf",
    ".hwp": "hwp",
    ".hwpx": "hwpx",
    ".docx": "pandoc",
    ".pptx": "pandoc",
    ".xlsx": "pandoc",
    ".odt": "pandoc",
    ".txt": "text",
    ".md": "text",
    ".csv": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
}
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_CHARS = 30_000  # 한 번에 돌려주는 글자 수. 넘으면 part로 나눈다
MAX_LISTED = 100
MAX_PAGES_SPEC = 200
TIMEOUT = 60.0  # 큰 PDF·문서는 글자를 뽑는 데 시간이 든다

DESCRIPTION = r"""Read files the user put in the materials folder: PDF, Korean HWP/HWPX, docx, pptx, xlsx, txt/md/csv, png/jpg.
No path: list files and the folder location. PDF: pages ("1-3,7"); mode="images" returns page images (scans, formula-heavy pages). Long text: part=2, 3, …
Example: {"path": "physics/ch3.pdf", "pages": "1-5"}
On failure: outside the folder or unsupported → ask the user to put the file there or save it as PDF."""


def input_dir() -> Path:
    raw = output.env_value(INPUT_DIR_ENV)
    path = Path(raw).expanduser() if raw else output.output_dir() / "자료"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _allowed_roots() -> list[Path]:
    return [input_dir().resolve(), output.output_dir().resolve()]


def resolve(raw: str) -> Path:
    """자료 폴더 기준 경로(또는 자료·결과물 폴더 안의 절대 경로)를 실제 파일 경로로. 폴더 밖이면 ValueError."""
    candidate = Path(raw.strip().strip('"')).expanduser()
    if not candidate.is_absolute():
        candidate = input_dir() / candidate
    path = candidate.resolve()
    if not any(root == path or root in path.parents for root in _allowed_roots()):
        raise ValueError(f"Cannot open files outside the materials folder ({input_dir()}): {raw[:200]}")
    return path


def parse_pages(spec: str | None) -> list[int] | None:
    """"1-3,7" → [1, 2, 3, 7]. 비어 있으면 None(전체)."""
    if not spec or not spec.strip():
        return None
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f'Invalid pages: "{spec}". Example: "1-3,7"')
        start, end = int(m.group(1)), int(m.group(2) or m.group(1))
        if start < 1 or end < start:
            raise ValueError(f'Invalid page range: "{part}"')
        pages.extend(range(start, end + 1))
        if len(pages) > MAX_PAGES_SPEC:
            raise ValueError(f"At most {MAX_PAGES_SPEC} pages per call.")
    return list(dict.fromkeys(pages))


def catalog() -> ToolResult:
    root = input_dir()
    files = []
    for path in sorted(root.rglob("*")):
        if len(files) >= MAX_LISTED:
            break
        if path.is_file() and path.suffix.lower() in KINDS and len(path.relative_to(root).parts) <= 3:
            files.append({"path": path.relative_to(root).as_posix(), "kb": round(path.stat().st_size / 1024)})
    data: dict[str, Any] = {"ok": True, "folder": str(root), "files": files}
    if not files:
        data["note"] = f"The materials folder is empty. Ask the user to put the files to read in this folder: {root}"
    return ToolResult(ok=True, data=data)


def _chunk(text: str, part: int) -> tuple[str, int]:
    parts = max(1, -(-len(text) // MAX_CHARS))
    return text[(part - 1) * MAX_CHARS : part * MAX_CHARS], parts


def read_document(path: str | None = None, pages: str | None = None, mode: str = "text", part: int = 1) -> ToolResult:
    if not path or not path.strip():
        return catalog()
    try:
        file = resolve(path)
        page_list = parse_pages(pages)
    except ValueError as exc:
        return ToolResult.failure([str(exc)], "Call without path to list the materials folder and pick a file.")
    kind = KINDS.get(file.suffix.lower())
    if kind is None:
        return ToolResult.failure([f"Unsupported format: {file.suffix}. Supported: {', '.join(sorted(KINDS))}"])
    if not file.is_file():
        return ToolResult.failure([f"File not found: {path[:200]}"], "Call without path to list the files.")
    if file.stat().st_size > MAX_FILE_BYTES:
        return ToolResult.failure([f"File too large (max {MAX_FILE_BYTES // 1024 // 1024} MB)."])
    if mode not in ("text", "images"):
        return ToolResult.failure(['mode must be "text" or "images".'])
    if mode == "images" and kind not in ("pdf", "image"):
        return ToolResult.failure(['mode="images" works only for PDF and image files. Read this format as text.'])
    if part < 1:
        return ToolResult.failure(["part starts at 1."])

    shown = file.relative_to(input_dir().resolve()).as_posix() if input_dir().resolve() in file.parents else str(file)
    data: dict[str, Any] = {"ok": True, "path": shown, "type": file.suffix.lower().lstrip(".")}
    if kind == "image":
        try:
            png, _ = page_images.preview(file)
        except page_images.ConvertError as exc:
            return ToolResult.failure(exc.errors)
        data["note"] = "Read the image yourself."
        return ToolResult(ok=True, data=data, preview_png=png)

    payload: dict[str, Any] = {"path": str(file), "kind": kind, "pages": page_list, "mode": mode}
    if kind == "pandoc":
        from openstem.tools.export import pandoc_path

        payload["pandoc"] = pandoc_path()
    with tempfile.TemporaryDirectory(prefix="openstem-read-") as tmp:
        try:
            result = sandbox.run_python("openstem.workers.documents", payload, Path(tmp), TIMEOUT)
        except sandbox.WorkerFailed as exc:
            message = f"Reading timed out ({TIMEOUT:.0f} s). Call again with fewer pages." if exc.timed_out else str(exc)
            return ToolResult.failure([message])
        if not result.get("ok"):
            return ToolResult.failure(result.get("errors") or ["Could not read the file."], result.get("hint"))
        if mode == "images":
            images = [sandbox.worker_file(Path(tmp), name).read_bytes() for name in result.get("images", [])]
            data.update(pages=result["pages"], image_pages=result["image_pages"])
            data["note"] = "Read the page images yourself. At most 4 pages per call."
            return ToolResult(ok=True, data=data, preview_png=images[0] if images else None, more_images=images[1:])

    if kind == "pdf":
        text = "\n\n".join(f"[page {p}]\n{t}" for p, t in result["page_texts"])
        data["pages"] = result["pages"]
        if result.get("scanned_pages"):
            data["scanned_pages"] = result["scanned_pages"]
            data["note"] = 'scanned_pages have almost no text (maybe scans). Read them with mode="images".'
    else:
        text = result["text"]
    if not text.strip():
        data.setdefault("note", "No text extracted. The document may be images only (for PDF use mode=\"images\").")
    chunk, parts = _chunk(text, part)
    if part > parts:
        return ToolResult.failure([f"part must be 1-{parts}."])
    data.update(part=part, parts=parts, text=chunk)
    if parts > 1 and part < parts:
        data["next"] = f"To continue, call again with part={part + 1}."
    return ToolResult(ok=True, data=data)
