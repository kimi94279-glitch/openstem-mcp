"""tests/cases/plot_function.yaml의 입력을 실제로 그려 기대 결과와 맞춰 본다(작업자 서브프로세스 포함)."""

import math
from pathlib import Path

import pytest
import yaml

from openstem.tools.plot import plot_function

CASES = yaml.safe_load((Path(__file__).parent / "cases" / "plot_function.yaml").read_text(encoding="utf-8"))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_pi_range_and_pdf_next_to_svg(output_dir):
    result = plot_function([{"expr": "tan(x)"}], [0, "2*pi"], ["-pi", "pi"])
    assert result.ok, result.data
    assert result.data["x_range"] == pytest.approx([0, 2 * math.pi])
    assert result.data["y_range"] == pytest.approx([-math.pi, math.pi], abs=1e-6)
    # export_document가 SVG 대신 쓰는 같은 이름의 PDF(축 밖 곡선이 잘려 있다)
    pdf = Path(result.data["pdf_path"])
    assert pdf.with_suffix(".svg") == Path(result.data["svg_path"])
    assert pdf.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_plot_function_case(case, output_dir):
    result = plot_function(**case["input"])
    expect = case["expect"]
    errors = " | ".join(result.data.get("errors", []))
    warnings = " | ".join(result.data.get("warnings", []))

    assert result.ok is expect["ok"], errors or result.data
    for fragment in expect.get("errors_contain", []):
        assert fragment in errors
    for fragment in expect.get("warnings_contain", []):
        assert fragment in warnings
    if expect.get("no_warnings"):
        assert "warnings" not in result.data, result.data["warnings"]
    if "parsed" in expect:
        assert [s["parsed"] for s in result.data["series"]] == expect["parsed"]

    if result.ok:
        for key in ("svg_path", "png_path", "pdf_path"):
            path = Path(result.data[key])
            assert path.parent == output_dir and path.stat().st_size > 0
        assert result.preview_png.startswith(PNG_SIGNATURE)
    else:
        assert result.preview_png is None
        assert len(result.data["errors"]) <= 10
