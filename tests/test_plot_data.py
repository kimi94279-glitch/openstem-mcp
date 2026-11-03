"""tests/cases/plot_data.yaml의 입력을 실제로 그려(작업자 서브프로세스 포함) 기대 결과와 맞춰 본다."""

import json
from pathlib import Path

import pytest
import yaml
from mcp import Client

from openstem.server import mcp
from openstem.tools.plotdata import plot_data
from openstem.workers.plotdata import parse_cell, pm_text, split_table

CASES = yaml.safe_load((Path(__file__).parent / "cases" / "plot_data.yaml").read_text(encoding="utf-8"))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_plot_data_case(case, output_dir):
    result = plot_data(**case["input"])
    expect = case["expect"]
    data = result.data
    errors = " | ".join(data.get("errors", []))
    warnings = " | ".join(data.get("warnings", []))
    dumped = json.dumps(data, ensure_ascii=False)

    assert result.ok is expect["ok"], errors or dumped
    for fragment in expect.get("errors_contain", []):
        assert fragment in errors, errors
    for fragment in expect.get("warnings_contain", []):
        assert fragment in warnings, warnings
    if expect.get("no_warnings"):
        assert "warnings" not in data, data["warnings"]
    for fragment in expect.get("contains", []):
        assert fragment in dumped, dumped
    for fragment in expect.get("excludes", []):
        assert fragment not in dumped, dumped
    if "n_series" in expect:
        assert len(data["series"]) == expect["n_series"]
    for series, want in zip(data.get("series", []), expect.get("fits", []), strict=False):
        fit = series["fit"]
        assert fit["model"] == want["model"]
        for name, (value, tol) in want.get("params", {}).items():
            assert fit["params"][name]["value"] == pytest.approx(value, abs=tol), dumped
        if "r2_min" in want:
            assert fit["r2"] >= want["r2_min"], dumped

    if result.ok:
        for key in ("svg_path", "png_path", "pdf_path"):
            path = Path(data[key])
            assert path.parent == output_dir and path.stat().st_size > 0
        assert result.preview_png.startswith(PNG_SIGNATURE)
    else:
        assert result.preview_png is None
        assert len(data["errors"]) <= 10


@pytest.mark.parametrize(
    ("value", "error", "text"),
    [
        (2.0312, 0.0478, "2.031 ± 0.048"),
        (-0.1807, 0.194, "-0.18 ± 0.19"),
        (1523.0, 124.0, "1520 ± 120"),
        (1.2e-5, 3e-7, "(1.200 ± 0.030)×10^-5"),
        (2.5, None, "2.5"),
    ],
)
def test_value_plus_minus_text(value, error, text):
    assert pm_text(value, error) == text


def test_table_cells_and_delimiters():
    assert parse_cell(" 2.31 ± 0.05 ") == (2.31, 0.05)
    assert parse_cell("−1.5e-3") == (-1.5e-3, None)
    assert parse_cell("n/a") is None
    header, rows = split_table('"t (s)";"v (m/s)"\n0;1,5\n')
    assert header == ["t (s)", "v (m/s)"] and rows == [["0", "1,5"]]
    header, rows = split_table("| a | b |\n|:--|--:|\n| 1 | 2 |\n")
    assert header == ["a", "b"] and rows == [["1", "2"]]


@pytest.mark.anyio
async def test_server_lists_plot_data_and_returns_preview(output_dir):
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "plot_data" in tools
        assert '{"' in tools["plot_data"].description and "On failure" in tools["plot_data"].description

        args = {"series": [{"x": [1, 2, 3, 4, 5], "y": [2.1, 3.9, 6.1, 8.0, 9.9], "yerr": 0.2}], "fit": "linear"}
        result = await client.call_tool("plot_data", args)
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert Path(data["png_path"]).parent == output_dir
        assert data["series"][0]["fit"]["params"]["a"]["value"] == pytest.approx(1.97, abs=0.05)
        image = result.content[1]
        assert image.type == "image" and image.mime_type == "image/png" and image.data

        bad = await client.call_tool("plot_data", {"series": [{"x": [1, 2], "y": [-1, 2]}], "fit": "exponential"})
        assert bad.is_error
        assert "y > 0" in json.loads(bad.content[0].text)["errors"][0]
