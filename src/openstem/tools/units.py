"""check_units: 식의 단위(차원)를 검사하고 값을 단위 변환한다. 단위 해석(pint)과 식 파싱(sympy)은 작업자(openstem.workers.units)에서 한다."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from openstem import mathexpr, sandbox
from openstem.tools import ToolResult

MAX_UNIT = 60
MAX_VALUE = 80
MAX_UNITS = 15
# 단위 문자열에 쓸 수 있는 글자. pint는 eval 없이 자기 토크나이저로 읽지만, 서버에서 먼저 좁혀 둔다.
UNIT_RE = re.compile(r"^[A-Za-z0-9 _.*/^()+\-%°µμΩÅ²³¹⁰⁴⁵⁶⁷⁸⁹⁻·]*$")
UNIT_CHARS = "letters, digits, space and * / ^ ( ) . - + _ % ° µ Ω Å ² ³ ·"

INPUT_HINT = 'Fix the input and call again. Units e.g. m, s, kg, N, J, km/h, m/s^2, kg*m^2, degC; dimensionless is "1"'
TIMEOUT_HINT = "Timed out ({timeout:.0f} s) while computing: the input is too heavy (huge powers or factorials). Simplify the expression or units and call again."
# 작업자가 준비 신호를 보내기 전(라이브러리 읽기)에 시간이 넘은 경우만 "다시 해 보라"고 한다. 그 뒤의 시간 초과는 입력이 무거운 것이다.
STARTUP_HINT = "On a first run loading the libraries can be slow; try once more."

DESCRIPTION = r"""Check the units (dimensions) of a physics/engineering formula, or convert a value.
Check: {"expr": "sqrt(2*h/g)", "units": {"h": "m", "g": "m/s^2"}, "expect": "s"} → matches; otherwise explanation says which dimensions differ. Units with values ("20 m") also give the numeric result.
Convert: {"value": "72 km/h", "to": "m/s"}
Use unit symbols (m, kg, N, J, km/h, m/s^2; dimensionless "1").
Rotation: Hz counts cycles (60 rpm = 1 Hz = 2π rad/s). Temperature changes: delta_degC. ton is the US short ton; metric tonne is t. Read note and warnings.
On failure: errors name the bad input; fix it and call again."""


def _check_unit(where: str, text: Any, limit: int = MAX_UNIT) -> list[str]:
    if not isinstance(text, str):
        return [f'{where} must be a string. E.g. "m/s^2"']
    if len(text) > limit:
        return [f"{where} too long ({len(text)} chars, max {limit})."]
    if not UNIT_RE.match(text):
        bad = next(ch for ch in text if not UNIT_RE.match(ch))
        return [
            f"{where} '{text}': character '{bad}' is not allowed. Allowed: {UNIT_CHARS}. Write units as symbols (e.g. 초→s, 미터→m)."
        ]
    return []


def validate(
    expr: str | None,
    units: dict[str, str] | None,
    expect: str | None,
    value: str | None,
    to: str | None,
) -> list[str]:
    has_expr, has_value = bool(expr and expr.strip()), bool(value and value.strip())
    if has_expr == has_value:
        return ['Give exactly one of expr (check a formula) or value (convert). E.g. {"value": "72 km/h", "to": "m/s"}']
    if has_value:
        errors = _check_unit("value", value, MAX_VALUE)
        if to is not None:
            errors += _check_unit("to", to)
        if expect is not None:
            errors += _check_unit("expect", expect)
        return errors

    errors = [f"expr: {e}" for e in mathexpr.check(expr, None)]
    if expect is not None:
        errors += _check_unit("expect", expect)
    units = units or {}
    if not isinstance(units, dict):
        return errors + ['units is {"variable": "unit"}. E.g. {"h": "m", "g": "m/s^2"}']
    if len(units) > MAX_UNITS:
        errors.append(f"At most {MAX_UNITS} units.")
    for name, text in list(units.items())[:MAX_UNITS]:
        if err := mathexpr.variable_name_error(name):
            errors.append(f"units '{name}': {err}")
        errors += _check_unit(f"units[{name}]", text)
    if errors:
        return errors
    names = mathexpr.detect_variables(mathexpr.tokenize(expr))
    if missing := [n for n in names if n not in units]:
        errors.append(f'No unit given in units for: {", ".join(missing)}. Give each variable a unit ("1" if dimensionless).')
    return errors


def check_units(
    expr: str | None = None,
    units: dict[str, str] | None = None,
    expect: str | None = None,
    value: str | None = None,
    to: str | None = None,
) -> ToolResult:
    if errors := validate(expr, units, expect, value, to):
        return ToolResult.failure(errors, INPUT_HINT)

    warnings = []
    if expr and expr.strip():
        names = set(mathexpr.detect_variables(mathexpr.tokenize(expr)))
        used = {k: v for k, v in (units or {}).items() if k in names}
        if extra := [k for k in units or {} if k not in names]:
            warnings.append(f"Ignored units for variables not in expr: {', '.join(extra)}")
        payload: dict[str, Any] = {"mode": "expr", "expr": expr, "units": used, "expect": expect}
    else:
        payload = {"mode": "convert", "value": value, "to": to if to is not None else expect}

    timeout = sandbox.default_timeout()
    with tempfile.TemporaryDirectory(prefix="openstem-units-") as tmp:
        try:
            result = sandbox.run_python("openstem.workers.units", payload, Path(tmp), timeout)
        except sandbox.WorkerFailed as exc:
            message = str(exc) if not exc.timed_out else (
                f"{exc} {STARTUP_HINT}" if str(exc).startswith("Worker startup") else TIMEOUT_HINT.format(timeout=timeout)
            )
            return ToolResult.failure([message])
    if not result.get("ok"):
        return ToolResult.failure(result.get("errors") or ["unknown failure"], result.get("hint"))
    if warnings:
        result["warnings"] = warnings + result.get("warnings", [])
    return ToolResult(ok=True, data=result)
