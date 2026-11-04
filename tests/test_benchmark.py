"""정확도 벤치마크 회귀 시험(benchmarks/math, scripts/bench.py).

기본은 `quick: true`로 표시한 사례만 돈다(작업자를 띄우므로 수십 초). `OPENSTEM_BENCH=1`이면 414개 전체.
기준선(benchmarks/math/baseline.json)보다 판정이 나빠지면 실패하고(correct → partial/wrong/error 등),
기준선에서도 correct가 아닌 사례는 known_issue를 이유로 xfail로 남긴다. 좋아진 사례는 통과한다
(그때는 `scripts/bench.py --save-baseline`으로 기준선을 다시 쓴다).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("openstem_bench", ROOT / "scripts" / "bench.py")
bench = importlib.util.module_from_spec(_spec)
sys.modules["openstem_bench"] = bench
_spec.loader.exec_module(bench)

FULL = os.environ.get("OPENSTEM_BENCH") == "1"
CASES = bench.load_cases()
BASELINE = bench.load_baseline()["grades"]
SELECTED = [c for c in CASES if FULL or c.get("quick")]


# ---------------------------------------------------------------- 사례 파일과 채점기(툴을 돌리지 않는다)


def test_cases_match_raw_audit():
    raw = json.loads(bench.RAW_PATH.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in CASES}
    assert len(CASES) == len(raw) == 414
    for item in raw:
        case = by_id[item["id"]]
        assert case["tool"] == item["tool"]
        assert case["input"] == item["input"], item["id"]
        assert case["expect"] == item["expect"], item["id"]


def test_every_case_has_valid_checks():
    for case in CASES:
        assert case.get("correct"), case["id"]
        for key in ("correct", "partial", "severe"):
            # 모르는 조건 이름이면 ValueError. 빈 결과에 대해 돌려서 문법만 확인한다.
            try:
                bench.check_group(case.get(key) or [], {"ok": True})
            except ValueError as exc:
                pytest.fail(f"{case['id']}.{key}: {exc}")
        unknown = set(case) - {"id", "tool", "input", "expect", "correct", "partial", "severe", "note", "known_issue", "quick"}
        assert not unknown, (case["id"], unknown)


def test_baseline_covers_all_cases():
    assert set(BASELINE) == {c["id"] for c in CASES}
    assert set(BASELINE.values()) <= set(bench.GRADES)


def test_quick_subset_covers_every_tool():
    quick = {c["tool"] for c in CASES if c.get("quick")}
    assert quick == set(bench.TOOLS)


def test_grader_examples():
    """판정 조건의 대표 동작(툴 출력 모양 그대로의 가짜 결과)."""
    case = {
        "id": "x",
        "tool": "verify_math",
        "correct": [{"ok": True}, {"solutions": ["3"]}],
        "partial": [{"ok": True}, {"solutions_include": ["3"]}],
        "severe": [{"solutions_include": ["-2"]}],
    }
    good = {"ok": True, "solutions": [{"result": "3"}]}
    extra = {"ok": True, "solutions": [{"result": "-2"}, {"result": "3"}]}
    bad = {"ok": True, "solutions": [{"result": "-2"}]}
    assert bench.grade_case(case, good)["grade"] == "correct"
    assert bench.grade_case(case, extra)["grade"] == "partial"
    graded = bench.grade_case(case, bad)
    assert (graded["grade"], graded["severe"]) == ("wrong", True)
    assert bench.grade_case(case, {"ok": False, "errors": ["boom"]})["grade"] == "error"

    ode = [{"expr": {"equals": "C1*sin(2*x) + C2*cos(2*x) + x*sin(2*x)/4", "consts": ["C1", "C2"]}}]
    assert bench.check_group(ode, {"result": "C2*cos(2*x) + (C1 + x/4)*sin(2*x)"}) is None
    assert bench.check_group(ode, {"result": "C1*cos(2*x) + C2*sin(2*x)"}) is not None
    assert bench.check_group([{"num": {"path": "variance", "value": 2.1, "rel": 1e-9}}], {"variance": "21/10 ≈ 2.1"}) is None
    assert bench.check_group([{"antiderivative": {"of": "sec(x)"}}], {"result": "-log(sin(x) - 1)/2 + log(sin(x) + 1)/2"}) is None
    assert bench.check_group([{"expr": {"equals": "e"}}], {"result": "e"}) is None
    assert bench.check_group([{"expr": {"equals": "exp(2)"}}], {"result": "E**2"}) is not None  # E는 문자
    sys_case = [{"solutions": [{"x": "2", "y": "1"}]}]
    assert bench.check_group(sys_case, {"solutions": [{"x": {"result": "2"}, "y": {"result": "1"}}]}) is None
    assert bench.check_group([{"has": {"path": "warnings", "words": ["x = 0"]}}], {"warnings": ["diverges at x = 0"]}) is None
    assert bench.check_group([{"lacks": ["temperature"]}], {"note": "a temperature reading"}) is not None
    either = [{"any": [[{"eq": {"matches": True}}], [{"eq": {"matches": False}}, {"has": ["cbrt"]}]]}]
    assert bench.check_group(either, {"matches": False, "note": "write cbrt(x)"}) is None
    assert bench.check_group(either, {"matches": False}) is not None


# ---------------------------------------------------------------- 실제 툴 실행


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPENSTEM_OUTPUT_DIR", str(tmp_path_factory.mktemp("bench-out")))
        mp.setenv("OPENSTEM_RECORD", "0")
        graded = bench.run_cases(SELECTED, jobs=8)
    return {r["id"]: r for r in graded}


@pytest.mark.parametrize("case_id", [c["id"] for c in SELECTED])
def test_benchmark_case(case_id, results):
    r = results[case_id]
    base = BASELINE.get(case_id, "correct")
    if bench.RANK[r["grade"]] < bench.RANK[base]:
        pytest.fail(
            f"{case_id}: 기준선 {base} → 지금 {r['grade']} ({r['reason']})\n"
            f"출력: {json.dumps(r['output'], ensure_ascii=False)[:600]}",
            pytrace=False,
        )
    if r["grade"] != "correct":
        pytest.xfail(f"{r['grade']}: {r['known_issue'] or r['reason']}")
