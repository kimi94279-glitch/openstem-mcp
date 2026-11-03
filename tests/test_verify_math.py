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


@pytest.mark.parametrize(
    ("message", "fragment", "absent"),
    [
        ("Timed out (20 s)", "while computing", "try once more"),
        ("Worker startup (loading libraries) did not finish within 60 s.", "try once more", "while computing"),
    ],
)
def test_timeout_hint_depends_on_when_it_timed_out(monkeypatch, message, fragment, absent):
    """준비 신호 뒤의 시간 초과는 입력이 무거운 것이다. '다시 해 보라'는 라이브러리 읽기 중 시간 초과에만."""
    from openstem import sandbox
    from openstem.tools.plot import plot_function
    from openstem.tools.plotdata import plot_data
    from openstem.tools.units import check_units

    def fail(*args, **kwargs):
        raise sandbox.WorkerFailed(message, timed_out=True)

    monkeypatch.setattr(sandbox, "run_python", fail)
    results = [
        verify_math(op="evaluate", expr="2**100"),
        check_units(value="1 m", to="cm"),
        plot_function(series=[{"expr": "x"}], x_range=[0, 1]),
        plot_data(series=[{"x": [1, 2, 3], "y": [1, 2, 3]}]),
    ]
    for result in results:
        text = " ".join(result.data["errors"])
        assert not result.ok and fragment in text and absent not in text, text
