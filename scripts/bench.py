"""정확도 벤치마크 실행기.

benchmarks/math/cases/*.yaml의 사례를 툴 함수(openstem.tools.*)로 돌리고, 사례마다 적힌 판정 조건으로
correct / partial / wrong / error를 매긴다. 결과는 Markdown(표준 출력 또는 --markdown 파일)과 JSON(--json)으로 낸다.

    uv run python scripts/bench.py                      # 전체(약 3분, 작업자 8개)
    uv run python scripts/bench.py --tool check_units   # 툴 하나
    uv run python scripts/bench.py --only v_equal/      # id 앞부분(쉼표로 여러 개)
    uv run python scripts/bench.py --json out.json --markdown out.md
    uv run python scripts/bench.py --source audit       # 툴을 돌리지 않고 raw 파일의 0.3.2 결과를 같은 기준으로 채점
    uv run python scripts/bench.py --save-baseline      # 지금 결과를 benchmarks/math/baseline.json에 기록(회귀 시험 기준)

판정 조건의 문법은 benchmarks/math/README.md에 있다. 결과물(그림 파일)은 임시 폴더에 쓰고(OPENSTEM_OUTPUT_DIR),
재현 기록은 남기지 않는다(OPENSTEM_RECORD=0).

이 스크립트는 서버가 아니라 개발 도구라서 sympy를 직접 import한다(툴 결과의 수식을 비교하려고). 비교 대상은
툴이 낸 문자열과 사례 파일의 문자열뿐이다.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import itertools
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "benchmarks" / "math" / "cases"
BASELINE_PATH = ROOT / "benchmarks" / "math" / "baseline.json"
RAW_PATH = ROOT / "benchmarks" / "raw" / "audit-2026-11-03.json"

TOOLS = ("verify_math", "check_units", "plot_function", "render_molecule", "plot_data")
GRADES = ("correct", "partial", "wrong", "error")
RANK = {"error": 0, "wrong": 0, "partial": 1, "correct": 2}  # 회귀 판단용(error와 wrong은 같은 단계)


# ---------------------------------------------------------------- 사례 읽기


def load_cases(tool: str | None = None, only: list[str] | None = None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for case in doc["cases"]:
            case.setdefault("tool", doc["tool"])
            cases.append(case)
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"사례 id가 겹친다: {dup}")
    if tool:
        cases = [c for c in cases if c["tool"] == tool]
    if only:
        cases = [c for c in cases if any(c["id"] == o or c["id"].startswith(o) for o in only)]
    return cases


# ---------------------------------------------------------------- 툴 실행


def _tool_functions() -> dict[str, Any]:
    from openstem.tools import molecule, plot, plotdata, units, verify

    return {
        "verify_math": verify.verify_math,
        "check_units": units.check_units,
        "plot_function": plot.plot_function,
        "plot_data": plotdata.plot_data,
        "render_molecule": molecule.render_molecule,
    }


def prepare_env(out_dir: str | None = None) -> str:
    """결과물은 임시 폴더로, 재현 기록은 끈다. 툴을 import하기 전에 부른다."""
    out = out_dir or tempfile.mkdtemp(prefix="openstem-bench-")
    os.environ["OPENSTEM_OUTPUT_DIR"] = out
    os.environ["OPENSTEM_RECORD"] = "0"
    return out


def run_case(case: dict[str, Any], funcs: dict[str, Any]) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    try:
        res = funcs[case["tool"]](**case["input"])
        data = {"ok": res.ok, **res.data}
    except Exception as exc:  # noqa: BLE001 - 벤치마크는 툴 예외도 결과로 남긴다
        data = {"ok": False, "exception": f"{type(exc).__name__}: {exc}"}
    return data, time.perf_counter() - start


def run_cases(cases: list[dict[str, Any]], jobs: int = 8) -> list[dict[str, Any]]:
    """사례를 돌려 채점한 결과 목록(사례 순서 그대로)."""
    funcs = _tool_functions()

    def one(case: dict[str, Any]) -> dict[str, Any]:
        data, seconds = run_case(case, funcs)
        return grade_case(case, data) | {"seconds": round(seconds, 3)}

    # 툴마다 작업자 프로세스를 따로 띄우므로 스레드로 겹쳐 돌려도 서로 간섭하지 않는다.
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(one, cases))


def audit_results(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """raw 감사 파일의 0.3.2 결과를 같은 판정 조건으로 채점한다(툴을 돌리지 않는다)."""
    raw = {x["id"]: x for x in json.loads(RAW_PATH.read_text(encoding="utf-8"))}
    out = []
    for case in cases:
        data = raw[case["id"]]["audit_result_0_3_2"]
        out.append(grade_case(case, data) | {"seconds": None})
    return out


# ---------------------------------------------------------------- 수식 비교(sympy)

_SYMPY: dict[str, Any] = {}


def _sympy():
    if not _SYMPY:
        import sympy
        from sympy.parsing.sympy_parser import parse_expr, standard_transformations

        _SYMPY.update(sympy=sympy, parse_expr=parse_expr, transformations=standard_transformations)
    return _SYMPY


def to_expr(text: Any):
    """툴 출력·사례 파일의 수식 문자열을 sympy 식으로. 'e'는 자연상수, 'E'는 그냥 문자(툴 출력 규칙과 같다)."""
    sp = _sympy()
    sympy = sp["sympy"]
    if isinstance(text, bool):
        raise TypeError("bool is not an expression")
    if isinstance(text, (int, float)):
        return sympy.Float(text) if isinstance(text, float) else sympy.Integer(text)
    s = str(text).split("≈")[0].strip()
    s = s.replace("−", "-").replace("^", "**")
    if re.fullmatch(r"[-+]?[0-9.]+[-+][0-9.]+i", s):  # 툴의 복소수 근삿값 표기 "1+1.732i"
        s = s[:-1] + "*I"
    local = {
        "e": sympy.E,
        "E": sympy.Symbol("E"),
        "I": sympy.I,
        "oo": sympy.oo,
        "zoo": sympy.zoo,
        "nan": sympy.nan,
        "pi": sympy.pi,
        "lambda": sympy.Symbol("lambda"),
        "lam": sympy.Symbol("lambda"),
        "C": sympy.Symbol("C"),
        "N": sympy.Symbol("N"),
        "S": sympy.Symbol("S"),
        "Q": sympy.Symbol("Q"),
        "beta": sympy.Symbol("beta"),
        "gamma": sympy.Symbol("gamma"),
    }
    return sp["parse_expr"](s, local_dict=local, transformations=sp["transformations"], evaluate=True)


_SAMPLES = (0.37, 1.61, 0.83, 2.29, 1.17, 0.59)


def _close(a: complex, b: complex, rel: float = 1e-7, abs_: float = 1e-9) -> bool:
    return abs(a - b) <= max(abs_, rel * max(1.0, abs(b)))


def _num(expr) -> complex | None:
    sympy = _sympy()["sympy"]
    try:
        v = complex(sympy.N(expr, 30))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if not (math.isfinite(v.real) and math.isfinite(v.imag)):
        return None
    return v


def same_value(a, b, consts: list[str] | None = None, rel: float = 1e-7) -> bool:
    """두 sympy 식이 같은가. 자유 문자가 있으면 여러 점에 넣어 비교한다. consts의 이름은 서로 바꿔도 같다고 본다."""
    sympy = _sympy()["sympy"]
    if a == b:
        return True
    special = (sympy.oo, -sympy.oo, sympy.zoo, sympy.nan)
    if a in special or b in special:
        return a == b
    names = sorted({s.name for s in (a.free_symbols | b.free_symbols)})
    perms = [None]
    if consts:
        present = [c for c in consts if c in names]
        perms = [dict(zip(present, p, strict=True)) for p in itertools.permutations(present)] or [None]
    for perm in perms:
        aa = a
        if perm:
            aa = a.xreplace({sympy.Symbol(k): sympy.Symbol("__tmp_" + v) for k, v in perm.items()})
            aa = aa.xreplace({sympy.Symbol("__tmp_" + v): sympy.Symbol(v) for v in perm.values()})
        with contextlib.suppress(Exception):  # simplify가 못 하면 아래 수치 비교로
            if sympy.simplify(aa - b) == 0:
                return True
        if _sample_equal(aa, b, names, rel):
            return True
    return False


def _sample_equal(a, b, names: list[str], rel: float) -> bool:
    sympy = _sympy()["sympy"]
    if not names:
        va, vb = _num(a), _num(b)
        return va is not None and vb is not None and _close(va, vb, rel)
    good = 0
    for k in range(len(_SAMPLES)):
        subs = {sympy.Symbol(n): sympy.Float(_SAMPLES[(k + i) % len(_SAMPLES)] + 0.11 * i) for i, n in enumerate(names)}
        va, vb = _num(a.subs(subs)), _num(b.subs(subs))
        if va is None or vb is None:
            continue
        if not _close(va, vb, rel):
            return False
        good += 1
    return good >= 2


# ---------------------------------------------------------------- 판정 조건

MISSING = object()


def get_path(data: Any, path: str) -> Any:
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return MISSING
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return MISSING
        else:
            return MISSING
    return cur


def _text(value: Any) -> str:
    if value is MISSING or value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _equal_values(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected or actual == expected and type(actual) is type(expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return _norm(actual) == _norm(expected)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return _close(actual, expected, 1e-9, 0)
    return actual == expected


def _as_number(value: Any) -> complex | None:
    if value is MISSING or value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return complex(value)
    try:
        return _num(to_expr(value))
    except Exception:  # noqa: BLE001
        return None


def _item_expr(item: Any):
    """solutions 항목 하나를 sympy 값으로. {"result","approx"} 형태면 result를 먼저, 안 되면 approx."""
    if isinstance(item, dict) and ("result" in item or "approx" in item):
        if "result" in item:
            with contextlib.suppress(Exception):  # CRootOf 등 읽지 못하면 approx로
                return to_expr(item["result"])
        return to_expr(item["approx"])
    return to_expr(item)


def _same_item(actual: Any, expected: Any, consts: list[str] | None) -> bool:
    try:
        if isinstance(expected, dict):  # 연립방정식: 변수마다 비교
            if not isinstance(actual, dict) or set(expected) != {k for k in actual if k in expected} or set(actual) != set(expected):
                return False
            return all(same_value(_item_expr(actual[k]), to_expr(v), consts) for k, v in expected.items())
        if isinstance(actual, dict) and "result" not in actual and "approx" not in actual:
            return False
        return same_value(_item_expr(actual), to_expr(expected), consts)
    except Exception:  # noqa: BLE001
        return False


def _list_at(data: Any, path: str) -> list[Any] | None:
    value = get_path(data, path)
    return value if isinstance(value, list) else None


def check(name: str, arg: Any, data: dict[str, Any]) -> str | None:
    """조건 하나를 본다. 통과하면 None, 아니면 짧은 이유."""
    if name == "ok":
        return None if data.get("ok") is arg else f"ok={data.get('ok')} (기대 {arg})"
    if name == "error":  # ok=false, 에러 글에 낱말이 모두 있다
        if data.get("ok") is not False:
            return "에러가 아니다"
        text = _text(data.get("errors")) + " " + _text(data.get("hint")) + " " + _text(data.get("exception"))
        miss = [w for w in (arg or []) if w.lower() not in text.lower()]
        return f"에러 글에 없음: {miss}" if miss else None
    if name in ("eq", "in"):
        for path, expected in arg.items():
            actual = get_path(data, path)
            options = expected if name == "in" else [expected]
            if not any(_equal_values(actual, e) for e in options):
                return f"{path}={_short(actual)} (기대 {'중 하나: ' if name == 'in' else ''}{_short(expected)})"
        return None
    if name == "missing":  # 경로가 없거나 null
        bad = [p for p in arg if get_path(data, p) not in (MISSING, None)]
        return f"있으면 안 됨: {bad}" if bad else None
    if name == "num":
        actual = _as_number(get_path(data, arg.get("path", "result")))
        if actual is None:
            return f"{arg.get('path', 'result')}: 수가 아님"
        expected = complex(arg["value"])
        if not _close(actual, expected, arg.get("rel", 1e-6), arg.get("abs", 0.0)):
            return f"{arg.get('path', 'result')}={_short(actual)} (기대 {arg['value']})"
        return None
    if name == "expr":
        path = arg.get("path", "result")
        raw = get_path(data, path)
        if raw is MISSING or raw is None:
            return f"{path}: 없음"
        try:
            ok = same_value(to_expr(raw), to_expr(arg["equals"]), arg.get("consts"), arg.get("rel", 1e-7))
        except Exception as exc:  # noqa: BLE001
            return f"{path}: 수식으로 읽지 못함({type(exc).__name__})"
        return None if ok else f"{path}={_short(raw)} (기대 {arg['equals']})"
    if name == "antiderivative":  # d/dx(result) == of
        sympy = _sympy()["sympy"]
        raw = get_path(data, arg.get("path", "result"))
        try:
            x = sympy.Symbol(arg.get("var", "x"))
            deriv = sympy.diff(to_expr(raw), x)
            target = to_expr(arg["of"])
        except Exception:  # noqa: BLE001
            return "원시함수를 읽지 못함"
        good = 0
        for xv in arg.get("at", (0.3, 0.7, 1.1, -0.4, 2.0)):
            va, vb = _num(deriv.subs(x, xv)), _num(target.subs(x, xv))
            if va is None or vb is None:
                continue
            if not _close(va, vb, 1e-7):
                return f"미분하면 {arg['of']}가 아님(x={xv})"
            good += 1
        return None if good >= 2 else "원시함수를 확인할 점이 모자람"
    if name in ("solutions", "solutions_include", "solutions_exclude", "set", "set_include"):
        if name.startswith("solutions"):
            path, items, consts = "solutions", arg, None
            if isinstance(arg, dict):
                path, items, consts = arg.get("path", "solutions"), arg["items"], arg.get("consts")
        else:
            path, items, consts = arg["path"], arg["items"], arg.get("consts")
        actual = _list_at(data, path)
        if actual is None:
            return f"{path}: 목록이 없음"
        if name == "solutions_exclude":
            hit = [e for e in items if any(_same_item(a, e, consts) for a in actual)]
            return f"{path}에 있으면 안 됨: {hit}" if hit else None
        miss = [e for e in items if not any(_same_item(a, e, consts) for a in actual)]
        if miss:
            return f"{path}에 없음: {miss} (실제 {_short(actual)})"
        if name in ("solutions", "set"):
            extra = [a for a in actual if not any(_same_item(a, e, consts) for e in items)]
            if extra:
                return f"{path}에 남는 것: {_short(extra)}"
        return None
    if name == "len":
        for path, n in arg.items():
            value = get_path(data, path)
            size = len(value) if isinstance(value, (list, dict, str)) else (0 if value in (MISSING, None) else -1)
            if size != n:
                return f"len({path})={size} (기대 {n})"
        return None
    if name in ("has", "lacks"):
        if isinstance(arg, dict):
            paths = arg.get("path", "")
            paths = paths if isinstance(paths, list) else [paths]
            words = arg["words"]
        else:
            paths, words = [""], arg
        text = " ".join(_text(data if not p else get_path(data, p)) for p in paths).lower()
        if name == "has":
            miss = [w for w in words if w.lower() not in text]
            return f"{'/'.join(p or '*' for p in paths)}에 없음: {miss}" if miss else None
        hit = [w for w in words if w.lower() in text]
        return f"{'/'.join(p or '*' for p in paths)}에 있으면 안 됨: {hit}" if hit else None
    if name == "any":
        reasons = []
        for group in arg:
            r = check_group(group, data)
            if r is None:
                return None
            reasons.append(r)
        return "어느 쪽도 아님: " + " | ".join(reasons)
    if name == "not":
        return "조건에 맞으면 안 됨" if check_group(arg, data) is None else None
    raise ValueError(f"모르는 판정 조건: {name}")


def check_group(group: list[dict[str, Any]], data: dict[str, Any]) -> str | None:
    """조건 목록이 모두 통과하면 None, 아니면 처음 실패한 이유."""
    for item in group or []:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError(f"판정 조건은 키가 하나인 매핑이어야 한다: {item!r}")
        (name, arg), = item.items()
        reason = check(name, arg, data)
        if reason is not None:
            return reason
    return None


def _short(value: Any, limit: int = 120) -> str:
    s = _text(value) if not isinstance(value, complex) else (f"{value.real:.6g}" if value.imag == 0 else str(value))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def grade_case(case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """correct 조건이 모두 맞으면 correct, 아니면 partial 조건, 아니면 툴이 실패했으면 error, 나머지는 wrong."""
    reason = check_group(case["correct"], data)
    severe = False
    if reason is None:
        grade = "correct"
    elif case.get("partial") and check_group(case["partial"], data) is None:
        grade = "partial"
    elif data.get("ok") is False:
        grade = "error"
    else:
        grade = "wrong"
        severe = bool(case.get("severe")) and check_group(case["severe"], data) is None
    return {
        "id": case["id"],
        "tool": case["tool"],
        "grade": grade,
        "severe": severe,
        "reason": reason,
        "expect": case.get("expect"),
        "known_issue": case.get("known_issue"),
        "output": _summary(data),
    }


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    """보고서에 남길 출력 요약(파일 경로·긴 안내 빼고)."""
    drop = {"svg_path", "png_path", "pdf_path", "check", "latex", "hint", "rref", "explanation"}
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k in drop:
            continue
        out[k] = v
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) > 1500:
        keys = ("ok", "result", "matches", "solutions", "errors", "warnings", "note", "magnitude", "unit", "formula", "mol_weight", "det", "rank")
        out = {k: out[k] for k in keys if k in out} | {"truncated": True}
    return out


# ---------------------------------------------------------------- 보고서


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    tools: dict[str, dict[str, Any]] = {}
    for r in results:
        t = tools.setdefault(r["tool"], {"n": 0, **{g: 0 for g in GRADES}, "severe": 0, "seconds": 0.0})
        t["n"] += 1
        t[r["grade"]] += 1
        t["severe"] += int(r["severe"])
        t["seconds"] += r["seconds"] or 0.0
    total = {"n": len(results), **{g: sum(t[g] for t in tools.values()) for g in GRADES}}
    total["severe"] = sum(t["severe"] for t in tools.values())
    total["seconds"] = round(sum(t["seconds"] for t in tools.values()), 1)
    for t in tools.values():
        t["seconds"] = round(t["seconds"], 1)
    ordered = {k: tools[k] for k in TOOLS if k in tools}
    return {"tools": ordered, "total": total}


def environment(source: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "date": _dt.datetime.now().astimezone().date().isoformat(),
        "source": source,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
    }
    if source == "run":
        import openstem

        info["openstem"] = openstem.__version__
        for mod in ("sympy", "pint", "rdkit", "numpy", "matplotlib"):
            with contextlib.suppress(Exception):
                info[mod] = __import__(mod).__version__
    else:
        info["openstem"] = "0.3.2 (audit 2026-11-03)"
    return info


def _pct(a: int, n: int) -> str:
    return f"{100 * a / n:.0f}%" if n else "-"


def markdown(report: dict[str, Any]) -> str:
    env, summ, results = report["environment"], report["summary"], report["results"]
    lines = [
        f"## openstem-mcp {env['openstem']} 정확도 벤치마크 ({env['date']})",
        "",
        (
            f"Python {env['python']}, {env['platform']}, sympy {env.get('sympy')}, pint {env.get('pint')}, RDKit {env.get('rdkit')}"
            if env["source"] == "run"
            else "툴을 돌리지 않고 raw 감사 파일의 0.3.2 결과를 같은 판정 조건으로 채점했다(시간 없음)."
        ),
        "",
        "| 툴 | 사례 | correct | partial | wrong | error | 심각 | 시간(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, t in [*summ["tools"].items(), ("**합계**", summ["total"])]:
        lines.append(
            f"| {name} | {t['n']} | {t['correct']} ({_pct(t['correct'], t['n'])}) | {t['partial']} | {t['wrong']} | {t['error']} | {t['severe']} | {t['seconds'] if env['source'] == 'run' else '-'} |"
        )
    bad = [r for r in results if r["grade"] != "correct"]
    if bad:
        lines += ["", f"### correct가 아닌 사례 {len(bad)}개", "", "| id | 판정 | 기대 | 이유 |", "|---|---|---|---|"]
        for r in sorted(bad, key=lambda r: (-GRADES.index(r["grade"]), r["id"])):  # error, wrong, partial 순
            why = r.get("known_issue") or r["reason"] or ""
            mark = " (심각)" if r["severe"] else ""
            lines.append(f"| `{r['id']}` | {r['grade']}{mark} | {_cell(r['expect'])} | {_cell(why)} |")
    if report.get("regressions"):
        lines += ["", "### 기준선보다 나빠진 사례", ""]
        lines += [f"- `{i}`: {a} → {b}" for i, a, b in report["regressions"]]
    if report.get("improvements"):
        lines += ["", "### 기준선보다 좋아진 사례", ""]
        lines += [f"- `{i}`: {a} → {b}" for i, a, b in report["improvements"]]
    return "\n".join(lines) + "\n"


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def compare_baseline(results: list[dict[str, Any]], baseline: dict[str, str]) -> tuple[list, list]:
    worse, better = [], []
    for r in results:
        old = baseline.get(r["id"])
        if old is None:
            continue
        if RANK[r["grade"]] < RANK[old]:
            worse.append((r["id"], old, r["grade"]))
        elif RANK[r["grade"]] > RANK[old]:
            better.append((r["id"], old, r["grade"]))
    return worse, better


def load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.exists():
        return {"grades": {}}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="openstem-mcp 정확도 벤치마크")
    ap.add_argument("--tool", choices=TOOLS, help="이 툴의 사례만")
    ap.add_argument("--only", help="id(또는 id 앞부분)를 쉼표로 구분")
    ap.add_argument("--json", dest="json_out", help="결과 JSON을 쓸 파일")
    ap.add_argument("--markdown", help="Markdown 보고서를 쓸 파일(없으면 표준 출력)")
    ap.add_argument("--jobs", type=int, default=8, help="동시에 돌릴 사례 수(기본 8)")
    ap.add_argument("--source", choices=("run", "audit"), default="run", help="run: 지금 코드로 실행, audit: raw의 0.3.2 결과 채점")
    ap.add_argument("--save-baseline", action="store_true", help="결과를 benchmarks/math/baseline.json에 기록")
    ap.add_argument("--fail-on-regression", action="store_true", help="기준선보다 나빠진 사례가 있으면 종료 코드 1")
    args = ap.parse_args(argv)

    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    cases = load_cases(args.tool, only)
    if not cases:
        print("고른 사례가 없다.", file=sys.stderr)
        return 2
    if args.source == "run":
        prepare_env()
        print(f"사례 {len(cases)}개 실행 중(동시 {args.jobs})…", file=sys.stderr)
        results = run_cases(cases, args.jobs)
    else:
        results = audit_results(cases)

    report: dict[str, Any] = {"environment": environment(args.source), "summary": summarize(results), "results": results}
    baseline = load_baseline()
    if args.source == "run" and baseline.get("grades"):
        worse, better = compare_baseline(results, baseline["grades"])
        report["regressions"], report["improvements"] = worse, better

    md = markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    if args.save_baseline:
        if args.source != "run" or args.tool or only:
            print("--save-baseline은 전체 사례를 지금 코드로 돌릴 때만 쓴다.", file=sys.stderr)
            return 2
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "environment": report["environment"],
                    "summary": report["summary"]["total"],
                    "grades": {r["id"]: r["grade"] for r in results},
                },
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"기준선을 썼다: {BASELINE_PATH.relative_to(ROOT)}", file=sys.stderr)
    if args.fail_on_regression and report.get("regressions"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
