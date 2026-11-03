"""tests/cases/render_tikz.yaml의 입력을 실제로 렌더링해 기대 결과와 맞춰 본다."""

from pathlib import Path

import pytest
import yaml

from openstem.tools.tikz import render_tikz

CASES = yaml.safe_load((Path(__file__).parent / "cases" / "render_tikz.yaml").read_text(encoding="utf-8"))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_render_tikz_case(case, tectonic, output_dir):
    result = render_tikz(**case["input"])
    expect = case["expect"]
    errors = " | ".join(result.data.get("errors", []))

    assert result.ok is expect["ok"], errors or result.data
    for fragment in expect.get("errors_contain", []):
        assert fragment in errors
    if expect.get("no_warnings"):
        assert "warnings" not in result.data, result.data["warnings"]

    if result.ok:
        for key in ("svg_path", "pdf_path", "png_path"):
            path = Path(result.data[key])
            assert path.parent == output_dir and path.stat().st_size > 0
        assert result.preview_png.startswith(PNG_SIGNATURE)
    else:
        assert result.preview_png is None
        assert len(result.data["errors"]) <= 10
