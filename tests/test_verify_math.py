"""tests/cases/verify_math.yaml의 입력을 실제로 계산해(작업자 서브프로세스 포함) 기대 결과와 맞춰 본다."""

import json
from pathlib import Path

import pytest
import yaml

from openstem.tools.verify import verify_math

CASES = yaml.safe_load((Path(__file__).parent / "cases" / "verify_math.yaml").read_text(encoding="utf-8"))


def approximations(data):
    values = [data.get("approx")] + [s.get("approx") for s in data.get("solutions", [])]
    return [v for v in values if isinstance(v, float)]


def field(data, path):
    """"det.result"처럼 점으로 이은 경로의 값(op=matrix·ode의 중첩 결과)."""
    for key in path.split("."):
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_verify_math_case(case):
    result = verify_math(**case["input"])
    expect = case["expect"]
    data = result.data
    errors = " | ".join(data.get("errors", []))
    dumped = json.dumps(data, ensure_ascii=False)

    assert result.ok is expect["ok"], errors or dumped
    assert result.preview_png is None
    for fragment in expect.get("errors_contain", []):
        assert fragment in errors, errors
    if "matches" in expect:
        assert data["matches"] == expect["matches"], dumped
    if "result" in expect:
        assert data["result"] == expect["result"], dumped
    if "solutions" in expect:
        assert [s["result"] for s in data["solutions"]] == expect["solutions"], dumped
    if "system" in expect:
        got = [{k: v["result"] for k, v in s.items()} for s in data["solutions"]]
        assert got == expect["system"], dumped
    if "approx" in expect:
        assert any(abs(v - expect["approx"]) < 1e-4 for v in approximations(data)), dumped
    for path, value in expect.get("fields", {}).items():
        assert field(data, path) == value, dumped
    for fragment in expect.get("contains", []):
        assert fragment in dumped, dumped
    for fragment in expect.get("excludes", []):
        assert fragment not in dumped, dumped
    if not result.ok:
        assert len(data["errors"]) <= 10
