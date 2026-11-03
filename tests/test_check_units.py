"""tests/cases/check_units.yaml의 입력을 실제로 검사해(작업자 서브프로세스 포함) 기대 결과와 맞춰 본다."""

import json
from pathlib import Path

import pytest
import yaml
from mcp import Client

from openstem.server import mcp
from openstem.tools.units import check_units

CASES = yaml.safe_load((Path(__file__).parent / "cases" / "check_units.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_check_units_case(case):
    result = check_units(**case["input"])
    expect = case["expect"]
    data = result.data
    errors = " | ".join(data.get("errors", []))
    dumped = json.dumps(data, ensure_ascii=False)

    assert result.ok is expect["ok"], errors or dumped
    assert result.preview_png is None
    for fragment in expect.get("errors_contain", []):
        assert fragment in errors, errors
    for key in ("matches", "consistent"):
        if key in expect:
            assert data[key] is expect[key], dumped
    if "approx" in expect:
        assert data["magnitude"] == pytest.approx(expect["approx"], rel=1e-6), dumped
    for fragment in expect.get("contains", []):
        assert fragment in dumped, dumped
    for fragment in expect.get("excludes", []):
        assert fragment not in dumped, dumped
    if not result.ok:
        assert len(data["errors"]) <= 10


def test_unit_whitelist_and_length_checked_before_worker():
    long_unit = "m*" * 40 + "m"
    result = check_units(expr="x", units={"x": long_unit})
    assert not result.ok and "too long" in result.data["errors"][0]
    result = check_units(value="3 m; import os")
    assert not result.ok and "';'" in result.data["errors"][0]


@pytest.mark.anyio
async def test_server_lists_check_units_and_answers():
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "check_units" in tools
        assert '{"' in tools["check_units"].description and "On failure" in tools["check_units"].description

        args = {"expr": "sqrt(2*h/g)", "units": {"h": "m", "g": "m/s^2"}, "expect": "s"}
        result = await client.call_tool("check_units", args)
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert data["matches"] is True and len(result.content) == 1  # 그림 없음

        bad = await client.call_tool("check_units", {"value": "3 kg", "to": "m"})
        assert bad.is_error
        assert "Cannot convert" in json.loads(bad.content[0].text)["errors"][0]
