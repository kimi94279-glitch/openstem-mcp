"""PDF → SVG/PNG 변환 (pymupdf). 입력은 우리가 컴파일한 PDF뿐이다."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

PNG_DPI = 200
PREVIEW_MAX_PX = 1600  # 미리보기 이미지 긴 변 상한
PNG_MAX_PX = 8000  # 저장 PNG 긴 변 상한 (거대한 그림이 메모리를 다 먹지 않게)


@dataclass
class Converted:
    svg_path: Path
    png_path: Path
    preview_png: bytes
    width_cm: float
    height_cm: float
    warnings: list[str] = field(default_factory=list)


def _dpi_for(rect: pymupdf.Rect, dpi: int, max_px: int) -> int:
    longest_in = max(rect.width, rect.height) / 72
    if longest_in * dpi <= max_px:
        return dpi
    return max(int(max_px / longest_in), 10)


def to_png(src: Path, dest: Path, dpi: int) -> None:
    """PDF·SVG의 첫 쪽을 PNG로. dpi를 파일에 적어 두어 워드가 원래 크기로 넣게 한다."""
    with pymupdf.open(src) as doc:
        page = doc[0]
        dpi = _dpi_for(page.rect, dpi, PNG_MAX_PX)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
    pix.set_dpi(dpi, dpi)
    pix.save(dest)


def to_pdf(src: Path, dest: Path) -> None:
    """SVG를 한 쪽짜리 PDF(벡터 그대로)로."""
    with pymupdf.open(src) as doc:
        dest.write_bytes(doc.convert_to_pdf())


def page_preview(pdf_path: Path, page: int = 0, dpi: int = 110) -> tuple[bytes, int]:
    """PDF 한 쪽의 미리보기 PNG와 전체 쪽수."""
    with pymupdf.open(pdf_path) as doc:
        target = doc[page]
        png = target.get_pixmap(dpi=_dpi_for(target.rect, dpi, PREVIEW_MAX_PX), alpha=False).tobytes("png")
        return png, doc.page_count


def pdf_to_svg_png(pdf_path: Path, stem: Path, dpi: int = PNG_DPI) -> Converted:
    """첫 쪽을 `<stem>.svg`, `<stem>.png`로 저장하고 미리보기 PNG 바이트를 돌려준다."""
    warnings: list[str] = []
    with pymupdf.open(pdf_path) as doc:
        if doc.page_count > 1:
            warnings.append(f"The PDF has {doc.page_count} pages; only the first was saved as SVG/PNG.")
        page = doc[0]
        rect = page.rect

        svg_path = stem.with_suffix(".svg")
        svg_path.write_text(page.get_svg_image(text_as_path=True), encoding="utf-8")

        png_dpi = _dpi_for(rect, dpi, PNG_MAX_PX)
        if png_dpi < dpi:
            warnings.append(f"Large figure: PNG saved at a reduced {png_dpi} dpi.")
        png_path = stem.with_suffix(".png")
        pix = page.get_pixmap(dpi=png_dpi, alpha=False)
        pix.save(png_path)

        preview_dpi = _dpi_for(rect, png_dpi, PREVIEW_MAX_PX)
        if preview_dpi == png_dpi:
            preview = pix.tobytes("png")
        else:
            preview = page.get_pixmap(dpi=preview_dpi, alpha=False).tobytes("png")

    return Converted(
        svg_path=svg_path,
        png_path=png_path,
        preview_png=preview,
        width_cm=round(rect.width / 72 * 2.54, 2),
        height_cm=round(rect.height / 72 * 2.54, 2),
        warnings=warnings,
    )
