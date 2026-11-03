"""PDF·SVG 변환을 작업자(openstem.workers.convert)에게 맡기는 서버 쪽 함수.

pymupdf 렌더링은 복잡한 그림에서 오래 걸리고 그동안 GIL을 쥐어 서버 전체를 멈추게 하므로,
서버 프로세스에서는 하지 않는다(pymupdf를 import하지도 않는다: 서버 시작도 그만큼 빠르다).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openstem import sandbox

WORKER = "openstem.workers.convert"
TIMEOUT_HINT = "Figure conversion timed out ({timeout:.0f} s): too many patterns, dots or repeats. Widen the spacing or shrink the figure and call again."


class ConvertError(RuntimeError):
    def __init__(self, errors: list[str], hint: str | None = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.hint = hint


@dataclass
class Figure:
    svg_path: Path
    png_path: Path
    preview_png: bytes
    width_cm: float
    height_cm: float
    warnings: list[str] = field(default_factory=list)


def _call(payload: dict[str, Any], workdir: Path) -> dict[str, Any]:
    timeout = sandbox.default_timeout()
    try:
        result = sandbox.run_python(WORKER, payload, workdir, timeout)
    except sandbox.WorkerFailed as exc:
        raise ConvertError([TIMEOUT_HINT.format(timeout=timeout) if exc.timed_out else str(exc)]) from None
    if not result.get("ok"):
        raise ConvertError(result.get("errors") or ["Figure conversion failed."], result.get("hint"))
    return result


def figure(pdf_path: Path, stem: Path) -> Figure:
    """PDF 첫 쪽을 `<stem>.svg`, `<stem>.png`로 저장하고 미리보기 PNG를 돌려준다."""
    with tempfile.TemporaryDirectory(prefix="openstem-convert-") as tmp:
        work = Path(tmp)
        result = _call({"op": "figure", "src": str(pdf_path)}, work)
        svg_path, png_path = stem.with_suffix(".svg"), stem.with_suffix(".png")
        shutil.copyfile(sandbox.worker_file(work, result["svg"]), svg_path)
        shutil.copyfile(sandbox.worker_file(work, result["png"]), png_path)
        preview = sandbox.worker_file(work, result["preview"]).read_bytes()
    return Figure(svg_path, png_path, preview, result["width_cm"], result["height_cm"], result.get("warnings", []))


def to_png(src: Path, dest: Path, dpi: int) -> None:
    with tempfile.TemporaryDirectory(prefix="openstem-convert-") as tmp:
        result = _call({"op": "to_png", "src": str(src), "dpi": dpi}, Path(tmp))
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["out"]), dest)


def to_pdf(src: Path, dest: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="openstem-convert-") as tmp:
        result = _call({"op": "to_pdf", "src": str(src)}, Path(tmp))
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["out"]), dest)


def preview(pdf_path: Path) -> tuple[bytes, int]:
    """PDF 첫 쪽 미리보기 PNG와 전체 쪽수."""
    with tempfile.TemporaryDirectory(prefix="openstem-convert-") as tmp:
        result = _call({"op": "preview", "src": str(pdf_path)}, Path(tmp))
        return sandbox.worker_file(Path(tmp), result["preview"]).read_bytes(), int(result["pages"])
