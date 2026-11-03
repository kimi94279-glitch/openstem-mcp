"""PDF·SVG 변환 작업자(pymupdf). sandbox.run_python으로만 돈다.

복잡한 그림(아주 촘촘한 패턴 등)은 렌더링에 수십 초가 걸리고, 그동안 파이썬 전역 잠금(GIL)을 쥔다.
서버 프로세스에서 하면 서버 전체가 멈추므로 여기서 시간 제한을 두고 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from openstem.render import convert
from openstem.workers import WorkerError, serve

MAX_SIDE_CM = 200


def _check_size(src: Path) -> None:
    with pymupdf.open(src) as doc:
        rect = doc[0].rect
    width, height = rect.width / 72 * 2.54, rect.height / 72 * 2.54
    if max(width, height) > MAX_SIDE_CM:
        raise WorkerError(
            [f"Figure too large ({width:.0f} x {height:.0f} cm, max {MAX_SIDE_CM} cm per side)."],
            "Shrink the coordinate range or use a smaller scale, then call again.",
        )


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    op, src = payload["op"], Path(payload["src"])
    if op == "figure":
        _check_size(src)
        result = convert.pdf_to_svg_png(src, Path("figure"))
        Path("preview.png").write_bytes(result.preview_png)
        return {
            "ok": True,
            "svg": result.svg_path.name,
            "png": result.png_path.name,
            "preview": "preview.png",
            "width_cm": result.width_cm,
            "height_cm": result.height_cm,
            "warnings": result.warnings,
        }
    if op == "to_png":
        _check_size(src)
        convert.to_png(src, Path("out.png"), int(payload["dpi"]))
        return {"ok": True, "out": "out.png"}
    if op == "to_pdf":
        _check_size(src)
        convert.to_pdf(src, Path("out.pdf"))
        return {"ok": True, "out": "out.pdf"}
    if op == "preview":
        png, pages = convert.page_preview(src)
        Path("preview.png").write_bytes(png)
        return {"ok": True, "preview": "preview.png", "pages": pages}
    raise WorkerError([f"Unknown conversion: {op}"])


if __name__ == "__main__":
    serve(handle)
