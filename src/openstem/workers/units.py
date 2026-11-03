"""check_units 작업자: 식을 sympy로 읽고, 식 나무를 따라가며 pint 단위로 차원을 맞춰 본다.

- 식 검사(mode=expr): 변수마다 단위(값이 붙어 있으면 값도)를 받아 결과 단위·차원을 구하고 expect와 비교한다.
  덧셈 항끼리 차원이 다르거나, 함수(sin·exp·log 등)의 인수·지수에 차원이 있으면 problems로 알린다.
- 단위 변환(mode=convert): "72 km/h" → "m/s".
단위 문자열은 pint가 자기 토크나이저로 읽는다(eval 없음). 서버가 글자 검사를 먼저 했다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from openstem import mathexpr
from openstem.workers import WorkerError, serve

UREG: Any = None  # pint 단위 목록. _preload()가 준비 신호 전에 만든다(약 1초).

MAX_PROBLEMS = 6
UNIT_HINT = 'Write units as symbols. E.g. m, s, kg, N, J, W, Pa, V, ohm, Hz, K, degC, km/h, m/s^2, kg*m^2, J/(kg*K). Dimensionless is "1"'

# pint 기본 차원 → (기호, 이름). 차원 글은 이 순서로 쓴다.
BASE_DIMS = (
    ("[mass]", "M", "mass"),
    ("[length]", "L", "length"),
    ("[time]", "T", "time"),
    ("[current]", "I", "current"),
    ("[temperature]", "Θ", "temperature"),
    ("[substance]", "N", "amount of substance"),
    ("[luminosity]", "J", "luminous intensity"),
)
_SYMBOL = {key: sym for key, sym, _ in BASE_DIMS}
_NAME = {key: name for key, _, name in BASE_DIMS}

# 자주 쓰는 차원의 이름(그리고 SI 유도 단위 기호). 키는 (M, L, T, I, Θ, N, J) 지수.
NAMED: dict[tuple[int, ...], tuple[str, str | None]] = {
    (0, 0, 0, 0, 0, 0, 0): ("dimensionless", None),
    (1, 0, 0, 0, 0, 0, 0): ("mass", None),
    (0, 1, 0, 0, 0, 0, 0): ("length", None),
    (0, 0, 1, 0, 0, 0, 0): ("time", None),
    (0, 0, 0, 1, 0, 0, 0): ("current", None),
    (0, 0, 0, 0, 1, 0, 0): ("temperature", None),
    (0, 0, 0, 0, 0, 1, 0): ("amount of substance", None),
    (0, 2, 0, 0, 0, 0, 0): ("area", None),
    (0, 3, 0, 0, 0, 0, 0): ("volume", None),
    (0, 0, -1, 0, 0, 0, 0): ("frequency/angular velocity", "Hz"),
    (0, 1, -1, 0, 0, 0, 0): ("velocity", None),
    (0, 1, -2, 0, 0, 0, 0): ("acceleration", None),
    (1, 1, -1, 0, 0, 0, 0): ("momentum/impulse", None),
    (1, 1, -2, 0, 0, 0, 0): ("force", "N"),
    (1, 2, -2, 0, 0, 0, 0): ("energy/work/torque", "J"),
    (1, 2, -3, 0, 0, 0, 0): ("power", "W"),
    (1, -1, -2, 0, 0, 0, 0): ("pressure/stress", "Pa"),
    (1, -3, 0, 0, 0, 0, 0): ("density", None),
    (1, 2, -1, 0, 0, 0, 0): ("angular momentum", None),
    (1, 2, 0, 0, 0, 0, 0): ("moment of inertia", None),
    (1, 0, -2, 0, 0, 0, 0): ("spring constant/surface tension", None),
    (1, -1, -1, 0, 0, 0, 0): ("viscosity", None),
    (0, 3, -1, 0, 0, 0, 0): ("volume flow rate", None),
    (0, 0, 1, 1, 0, 0, 0): ("charge", "C"),
    (1, 2, -3, -1, 0, 0, 0): ("voltage (potential difference)", "V"),
    (1, 2, -3, -2, 0, 0, 0): ("resistance", "ohm"),
    (-1, -2, 4, 2, 0, 0, 0): ("capacitance", "F"),
    (1, 2, -2, -2, 0, 0, 0): ("inductance", "H"),
    (1, 0, -2, -1, 0, 0, 0): ("magnetic field (flux density)", "T"),
    (1, 2, -2, -1, 0, 0, 0): ("magnetic flux", "Wb"),
    (1, 1, -3, -1, 0, 0, 0): ("electric field", None),
    (1, 2, -2, 0, -1, 0, 0): ("heat capacity/entropy", None),
    (0, 2, -2, 0, -1, 0, 0): ("specific heat", None),
    (1, 2, -2, 0, -1, -1, 0): ("molar heat capacity (gas constant)", None),
    (0, -3, 0, 0, 0, 1, 0): ("concentration", None),
}

# 값이 붙은 단위 문자열: 숫자로 시작한다("20 m", "9.8 m/s^2"). 단 "1"과 "1/s"는 단위만 쓴 것으로 본다.
_STARTS_WITH_NUMBER = re.compile(r"^\s*[-+]?(?:[0-9]|\.[0-9])")
_UNIT_ONLY = re.compile(r"^\s*1\s*(?:/|$)")
_LEADING_NUMBER = re.compile(r"^\s*([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)\s*\*?\s*(.*)$")


# ── 차원 글 ─────────────────────────────────────────────────────────


def dims_of(unit: Any) -> dict[str, Fraction]:
    """pint 단위의 차원 → {"[length]": 1, ...}. 지수는 분수로 고른다(1/3을 세 번 곱한 0.999… 같은 오차 없애기)."""
    out = {}
    for key, exp in dict(UREG.Quantity(1, unit).dimensionality).items():
        frac = Fraction(exp).limit_denominator(1000)
        if frac:
            out[key] = frac
    return out


def _exp_text(exp: Fraction) -> str:
    if exp == 1:
        return ""
    if exp.denominator == 1:
        return f"^{exp.numerator}"
    return f"^({exp.numerator}/{exp.denominator})"


def _key(dims: dict[str, Fraction]) -> tuple[Fraction, ...] | None:
    if any(k not in _SYMBOL for k in dims):
        return None
    return tuple(dims.get(k, Fraction(0)) for k, _, _ in BASE_DIMS)


def dim_text(dims: dict[str, Fraction]) -> str:
    """{"[mass]": 1, "[length]": 1, "[time]": -2} → "M·L·T^-2 (force, N)" """
    if not dims:
        return "1 (dimensionless)"
    order = [k for k, _, _ in BASE_DIMS if k in dims] + sorted(k for k in dims if k not in _SYMBOL)
    text = "·".join(f"{_SYMBOL.get(k, k)}{_exp_text(dims[k])}" for k in order)
    key = _key(dims)
    if key is not None and all(v.denominator == 1 for v in key) and (named := NAMED.get(tuple(int(v) for v in key))):
        name, si = named
        text += f" ({name}, {si})" if si else f" ({name})"
    return text


def _divide(a: dict[str, Fraction], b: dict[str, Fraction]) -> dict[str, Fraction]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, Fraction(0)) - v
    return {k: v for k, v in out.items() if v}


def explain(got: dict[str, Fraction], want: dict[str, Fraction], expect: str) -> str:
    """차원이 다를 때 어느 기본 차원이 얼마나 다른지."""
    keys = [k for k, _, _ in BASE_DIMS] + sorted((set(got) | set(want)) - set(_SYMBOL))
    parts = []
    for k in keys:
        g, w = got.get(k, Fraction(0)), want.get(k, Fraction(0))
        if g != w:
            parts.append(f"{_NAME.get(k, k)} exponent {_frac(g)} (result) vs {_frac(w)} (expected)")
    ratio = dim_text(_divide(got, want))
    return (
        f"Dimensions differ. Result is {dim_text(got)}, expected '{expect}' is {dim_text(want)}. "
        f"{', '.join(parts)}. result ÷ expected = {ratio}: check for a missing or extra factor with this dimension."
    )


def _frac(v: Fraction) -> str:
    return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"


def unit_text(unit: Any) -> str:
    text = f"{unit:~C}"
    return text or "1"


def _num(v: float) -> str:
    text = f"{v:.6g}"
    return "0" if text == "-0" else text


def quantity_text(q: Any) -> str:
    unit = unit_text(q.units)
    return _num(q.magnitude) if unit == "1" else f"{_num(q.magnitude)} {unit}"


# ── 단위 문자열 읽기 ────────────────────────────────────────────────


def parse_quantity(where: str, text: str) -> tuple[Any, bool]:
    """단위 문자열 → (pint Quantity, 값이 붙어 있었는지). "1"·""은 무차원 단위만."""
    import pint

    s = (text or "").strip()
    if s in ("", "1", "dimensionless"):
        return UREG.Quantity(1, "dimensionless"), False
    try:
        try:
            q = UREG.parse_expression(s)
        except pint.OffsetUnitCalculusError:
            # pint 0.25(Python 3.11용 잠금 버전)는 "25 degC"를 25 * degC 곱셈으로 읽어 거절한다.
            # 0.26처럼 숫자와 단위를 나눠 Quantity(25, "degC")로 만든다.
            m = _LEADING_NUMBER.match(s)
            if not m or not m.group(2).strip():
                raise
            q = UREG.Quantity(float(m.group(1)), m.group(2).strip())
    except pint.UndefinedUnitError as exc:
        names = ", ".join(f"'{n}'" for n in getattr(exc, "unit_names", ()) or ()) or "?"
        raise WorkerError([f"{where} '{text}': unknown unit {names}."], UNIT_HINT) from None
    except pint.DimensionalityError as exc:
        raise WorkerError(
            [f"{where} '{text}': adds different dimensions inside the unit ({exc.dim1} + {exc.dim2})."], UNIT_HINT
        ) from None
    except Exception as exc:
        detail = str(exc).splitlines()[0][:80] if str(exc) else type(exc).__name__
        raise WorkerError([f"{where} '{text}': cannot read unit ({detail})."], UNIT_HINT) from None
    if not isinstance(q, UREG.Quantity):
        q = UREG.Quantity(q, "dimensionless")  # 숫자만 쓴 것: 무차원 값
    if isinstance(q.magnitude, complex) or not math.isfinite(float(q.magnitude)):
        raise WorkerError([f"{where} '{text}': value is not a finite real number."])
    has_value = bool(_STARTS_WITH_NUMBER.match(s)) and not _UNIT_ONLY.match(s)
    return q, has_value


# ── 회전수(rpm·rps·rev·cycle)와 Hz ─────────────────────────────────
# pint는 1 rev = 2π rad이고 rad를 1(무차원)로 다룬다. 그래서 60 rpm을 Hz로 바꾸면 6.28 Hz가 나온다(정답 1 Hz).
# Hz·1/s는 '초당 횟수(회전·주기)'로 읽고, rad가 들어간 단위(rpm·rad/s·deg/s)와 오갈 때 2π를 맞춘다.
REVOLUTION_UNITS = {"revolutions_per_minute", "revolutions_per_second", "turn"}
TWO_PI = 2 * math.pi
CYCLE_NOTE = "Hz (and 1/s) counts cycles per second: 1 Hz = 1 rev/s = 60 rpm = 2π rad/s ≈ 6.283 rad/s."


def _radian_power(q: Any) -> float:
    """뿌리 단위로 풀었을 때 rad의 지수(rpm·rad/s → 1, Hz·1/s → 0)."""
    return float(dict(UREG.Quantity(1, q.units).to_root_units().unit_items()).get("radian", 0))


def _is_revolution(q: Any) -> bool:
    return any(name in REVOLUTION_UNITS for name, _ in q.unit_items())


def as_cycles(q: Any) -> Any:
    """rad가 든 값을 '회전 수' 기준으로(1 rev = 1): 3000 rpm → 50 1/s, 1 rad/s → 0.159 1/s."""
    root = q.to_root_units()
    k = _radian_power(q)
    return root / UREG.Quantity(TWO_PI, "radian") ** k if k else root


def _is_offset(q: Any) -> bool:
    """degC·degF처럼 눈금(0점이 다른) 단위인가. 이런 단위는 곱셈이 정의되지 않는다."""
    import pint

    try:
        q * UREG.Quantity(1, "meter")
    except pint.OffsetUnitCalculusError:
        return True
    except Exception:
        return False
    return False


# ── 식 검사 ─────────────────────────────────────────────────────────


@dataclass
class Node:
    unit: Any  # pint Unit
    value: Any = None  # pint Quantity. 값을 모르면 None
    bad: bool = False  # 아래쪽에서 이미 차원 문제가 났다(같은 문제를 겹쳐 보고하지 않게)


# 인수가 무차원이어야 하고 결과도 무차원인 함수(각도 rad·deg는 무차원이다)
DIMENSIONLESS_FUNCS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "acot",
    "sinh", "cosh", "tanh", "coth", "asinh", "acosh", "atanh", "exp", "log",
}  # fmt: skip
SAME_UNIT_FUNCS = {"Abs", "floor", "ceiling"}  # 결과가 인수와 같은 단위
UNITLESS_RESULT_FUNCS = {"sign", "Heaviside"}  # 인수는 아무 단위나, 결과는 무차원
FUNC_NAMES = {"Abs": "abs", "ceiling": "ceil", "log": "log(ln)", "exp": "exp"}


class Checker:
    def __init__(self, env: dict[str, Node]) -> None:
        self.env = env
        self.problems: list[str] = []
        self.dimless = UREG.Unit("dimensionless")

    def problem(self, text: str) -> None:
        if text not in self.problems:
            self.problems.append(text)

    def is_dimless(self, node: Node) -> bool:
        return not dims_of(node.unit)

    def walk(self, e: Any) -> Node:
        import sympy

        if e.is_Symbol:
            return self.env[e.name]
        if e.is_number:  # 2, pi, sqrt(2), log(10)
            return Node(self.dimless, _number(e))
        if e.is_Add:
            return self._same_dims([self.walk(a) for a in e.args], e.args, "the terms of a sum/difference", sum_values)
        if e.is_Mul:
            return self._mul([self.walk(a) for a in e.args])
        if e.is_Pow:
            return self._pow(e)
        if isinstance(e, sympy.Function):
            return self._function(e)
        self.problem(f"'{e}': cannot check units of this form.")
        return Node(self.dimless, None, bad=True)

    def _same_dims(self, nodes: list[Node], exprs: Any, what: str, combine: Any) -> Node:
        """덧셈·Min·Max: 모든 항의 차원이 같아야 한다. 결과 단위는 첫 항의 단위."""
        first = nodes[0]
        groups: dict[str, list[str]] = {}
        for node, ex in zip(nodes, exprs, strict=True):
            if node.bad:
                continue
            groups.setdefault(dim_text(dims_of(node.unit)), []).append(f"'{ex}'")
        if len(groups) > 1:
            listing = "; ".join(f"{', '.join(terms[:3])} → {dim}" for dim, terms in groups.items())
            self.problem(f"Dimensions differ among {what}: {listing}")
            return Node(first.unit, None, bad=True)
        bad = any(n.bad for n in nodes)
        value = None
        if not bad and all(n.value is not None for n in nodes):
            value = _safe(lambda: combine([n.value.to(first.unit) for n in nodes], first.unit))
        return Node(first.unit, value, bad)

    def _mul(self, nodes: list[Node]) -> Node:
        unit = self.dimless
        for n in nodes:
            unit = unit * n.unit
        value = None
        if all(n.value is not None for n in nodes):

            def product() -> Any:
                out = UREG.Quantity(1, "dimensionless")
                for n in nodes:
                    out = out * n.value
                return out

            value = _safe(product)
        return Node(unit, value, any(n.bad for n in nodes))

    def _pow(self, e: Any) -> Node:
        base_expr, exp_expr = e.args
        base, exp = self.walk(base_expr), self.walk(exp_expr)
        if not exp.bad and not self.is_dimless(exp):
            self.problem(f"Exponent '{exp_expr}' must be dimensionless but is {dim_text(dims_of(exp.unit))} ('{e}').")
            return Node(base.unit, None, bad=True)
        if exp_expr.is_number:
            try:
                p = _sympy_float(exp_expr.evalf())
            except Exception:
                self.problem(f"Exponent '{exp_expr}' is not real ('{e}').")
                return Node(base.unit, None, bad=True)
            value = _safe(lambda: base.value**p) if base.value is not None else None
            return Node(base.unit**p, value, base.bad or exp.bad)
        if self.is_dimless(base) or base.bad:
            value = None
            if base.value is not None and exp.value is not None:
                value = _safe(
                    lambda: UREG.Quantity(
                        base.value.to("dimensionless").magnitude ** exp.value.to("dimensionless").magnitude,
                        "dimensionless",
                    )
                )
            return Node(self.dimless, value, base.bad or exp.bad)
        self.problem(
            f"'{e}': base '{base_expr}' has a dimension ({dim_text(dims_of(base.unit))}), so the exponent must be a number (now '{exp_expr}'). "
            "Use a numeric exponent or make the base a dimensionless ratio."
        )
        return Node(base.unit, None, bad=True)

    def _function(self, e: Any) -> Node:
        name = type(e).__name__
        shown = FUNC_NAMES.get(name, name)
        nodes = [self.walk(a) for a in e.args]
        bad = any(n.bad for n in nodes)
        if name in DIMENSIONLESS_FUNCS:
            arg, node = e.args[0], nodes[0]
            if not node.bad and not self.is_dimless(node):
                self.problem(
                    f"The argument '{arg}' of {shown} must be dimensionless but is {dim_text(dims_of(node.unit))} "
                    "(use a ratio of like units or an angle)."
                )
                return Node(self.dimless, None, bad=True)
            return Node(self.dimless, _apply(e.func, nodes, None), bad)
        if name in SAME_UNIT_FUNCS:
            return Node(nodes[0].unit, _apply(e.func, nodes, nodes[0].unit), bad)
        if name in UNITLESS_RESULT_FUNCS:
            node = nodes[0]
            value = None
            if node.value is not None:
                value = _safe(lambda: UREG.Quantity(_sympy_float(e.func(node.value.magnitude)), "dimensionless"))
            return Node(self.dimless, value, bad)
        if name in ("Min", "Max"):
            pick = min if name == "Min" else max
            return self._same_dims(nodes, e.args, f"the arguments of {name.lower()}", lambda qs, u: pick(qs, key=lambda q: q.magnitude))
        if name == "atan2":
            joined = self._same_dims(nodes, e.args, "the two arguments of atan2", lambda qs, u: qs)
            if joined.bad:
                return Node(self.dimless, None, bad=True)
            value = None
            if joined.value is not None:
                y, x = (q.magnitude for q in joined.value)
                value = _safe(lambda: UREG.Quantity(math.atan2(y, x), "dimensionless"))
            return Node(self.dimless, value)
        self.problem(f"Function '{shown}' is not supported in unit checks.")
        return Node(self.dimless, None, bad=True)


def sum_values(values: list[Any], unit: Any) -> Any:
    return UREG.Quantity(sum(q.magnitude for q in values), unit)


def _safe(fn: Any) -> Any:
    """값 계산: 실수가 아니거나(음수의 제곱근) 넘치면 None(값만 포기하고 단위 검사는 계속한다)."""
    try:
        q = fn()
    except Exception:
        return None
    mags = [x.magnitude for x in q] if isinstance(q, (list, tuple)) else [q.magnitude]
    for m in mags:
        if isinstance(m, complex) or not math.isfinite(float(m)):
            return None
    return q


def _sympy_float(v: Any) -> float:
    out = complex(v)
    if abs(out.imag) > 1e-12 * max(1.0, abs(out.real)):
        raise ValueError("not real")
    return out.real


def _number(e: Any) -> Any:
    try:
        return UREG.Quantity(_sympy_float(e.evalf()), "dimensionless")
    except Exception:
        return None


def _apply(func: Any, nodes: list[Node], unit: Any) -> Any:
    """sympy 함수를 값(무차원으로 바꾼 크기, unit이 있으면 그 단위의 크기)에 적용한다."""
    import sympy

    if any(n.value is None for n in nodes):
        return None

    def run() -> Any:
        if unit is None:
            mags = [n.value.to("dimensionless").magnitude for n in nodes]
        else:
            mags = [n.value.to(unit).magnitude for n in nodes]
        out = _sympy_float(func(*[sympy.Float(m) for m in mags]))
        return UREG.Quantity(out, unit if unit is not None else "dimensionless")

    return _safe(run)


def _revolution_value(name: str, text: str, q: Any, has_value: bool, has_pi: bool, warnings: list[str]) -> Any:
    """rpm처럼 회전 수로 준 값을 식에 맞게 읽는다.

    식에 pi가 있으면(P = 2*pi*n*T, omega = 2*pi*f) 2π를 식이 곱하므로 회전 수(1 rev = 1)로 읽고,
    없으면(P = omega*T) 각속도(1 rev = 2π rad)로 읽는다. 어느 쪽으로 읽었는지 늘 알린다.
    """
    cycles, angular = as_cycles(q), q.to_root_units()
    if has_pi:
        shown = f" = {quantity_text(cycles)}" if has_value else ""
        other = f" ({quantity_text(angular)})" if has_value else ""
        warnings.append(
            f"units[{name}]: read {text} as revolutions per time{shown} (1 rev = 1) because the formula has pi, as in "
            f"P = 2*pi*n*T or omega = 2*pi*f. If this variable is already an angular velocity, the formula should not multiply by 2*pi again{other}."
        )
        return cycles
    shown = f" = {quantity_text(angular)}" if has_value else ""
    other = f" ({quantity_text(cycles)})" if has_value else ""
    warnings.append(
        f"units[{name}]: read {text} as an angular quantity{shown} (1 rev = 2π rad), as in P = omega*T. "
        f"If the formula needs revolutions per time (n in P = 2*pi*n*T), add the 2*pi to the formula or give the value in Hz{other}."
    )
    return q


def check_expr(payload: dict[str, Any]) -> dict[str, Any]:
    import sympy

    expr_text = payload["expr"]
    try:
        expr = mathexpr.parse(expr_text, None)
    except mathexpr.ExprError as exc:
        raise WorkerError([f"expr '{expr_text}': {exc}"], "Expression syntax (sympy): x**2, sqrt(x), exp(-x), m*g*h") from None

    env: dict[str, Node] = {}
    warnings: list[str] = []
    errors: list[str] = []
    for name, text in (payload.get("units") or {}).items():
        try:
            q, has_value = parse_quantity(f"units[{name}]", text)
        except WorkerError as exc:
            errors += exc.errors
            continue
        if _is_offset(q):
            # 눈금 온도(°C·°F)는 곱셈이 정의되지 않아 K으로 바꿔 계산한다. 온도 차이라면 값이 틀려진다.
            q = q.to("kelvin")
            if has_value:
                warnings.append(
                    f"units[{name}]: read {text} as a temperature reading, i.e. the absolute temperature {quantity_text(q)}. "
                    f"If it is a temperature change (ΔT, as in Q = m*c*ΔT), write it as delta_degC (e.g. \"10 delta_degC\" = 10 K) or in K; "
                    "otherwise the result is wrong."
                )
        if "ton" in {n for n, _ in q.unit_items()}:
            warnings.append(f"units[{name}]: ton is the US short ton (907.185 kg); the metric tonne (1000 kg) is t.")
        if _is_revolution(q) and _radian_power(q):
            q = _revolution_value(name, text, q, has_value, expr.has(sympy.pi), warnings)
        env[name] = Node(q.units, q if has_value else None)
    if errors:
        raise WorkerError(errors, UNIT_HINT)

    checker = Checker(env)
    root = checker.walk(expr)
    result: dict[str, Any] = {"ok": True, "mode": "expr", "parsed": str(expr)}
    consistent = not checker.problems
    result["consistent"] = consistent
    if consistent:
        reduced = UREG.Quantity(1, root.unit).to_reduced_units().units
        got = dims_of(root.unit)
        result["unit"] = unit_text(reduced)
        result["si_unit"] = unit_text(UREG.Quantity(1, root.unit).to_base_units().units)
        result["dimension"] = dim_text(got)
    else:
        result["problems"] = checker.problems[:MAX_PROBLEMS]

    expect_text = payload.get("expect")
    target = None
    if expect_text is not None and expect_text.strip():
        q, has_value = parse_quantity("expect", expect_text)
        if has_value and q.magnitude != 1:
            raise WorkerError([f'expect \'{expect_text}\': give only a unit, no number. E.g. "s", "m/s^2"'])
        target = q.units
        want = dims_of(target)
        result["expect"] = expect_text
        result["expect_dimension"] = dim_text(want)
        if not consistent:
            result["matches"] = False
            result["explanation"] = "Dimensions are inconsistent within the expression (see problems). Fix that first."
        else:
            result["matches"] = got == want
            if got != want:
                result["explanation"] = explain(got, want, expect_text)

    if consistent and root.value is not None:
        value = root.value
        if target is not None and result.get("matches"):
            value = _safe(lambda: value.to(target))
        else:
            value = _safe(lambda: value.to_base_units())
            if value is not None and (si := _named_si(dims_of(value.units))):
                value = _safe(lambda: value.to(si)) or value
        if value is not None:
            result["value"] = quantity_text(value)
            result["magnitude"] = float(value.magnitude)
    elif consistent and any(n.value is not None for n in env.values()):
        missing = [k for k, n in env.items() if n.value is None]
        if missing:
            warnings.append(f'value not computed: no value for {", ".join(missing)} (attach a number, like "20 m")')
        else:
            warnings.append("value omitted: the result is not real or cannot be computed (square root of a negative, division by zero, etc.).")
    if warnings:
        result["warnings"] = warnings
    return result


def _named_si(dims: dict[str, Fraction]) -> str | None:
    key = _key(dims)
    if key is None or any(v.denominator != 1 for v in key):
        return None
    named = NAMED.get(tuple(int(v) for v in key))
    return named[1] if named and named[1] not in (None, "Hz") else None


# ── 단위 변환 ───────────────────────────────────────────────────────


def convert(payload: dict[str, Any]) -> dict[str, Any]:
    import pint

    text = payload["value"]
    q, _ = parse_quantity("value", text)
    to_text = payload.get("to")
    result: dict[str, Any] = {"ok": True, "mode": "convert", "input": text}
    notes: list[str] = []
    got = dims_of(q.units)
    result["dimension"] = dim_text(got)
    if to_text is not None and to_text.strip():
        target, has_value = parse_quantity("to", to_text)
        if has_value and target.magnitude != 1:
            raise WorkerError([f"to '{to_text}': give only a unit, no number. E.g. \"m/s\""])
        want = dims_of(target.units)
        if got != want:
            raise WorkerError(
                [f"Cannot convert: '{text}' is {dim_text(got)} but to '{to_text}' is {dim_text(want)}."],
                "Set to a unit of the same dimension.",
            )
        try:
            out = q.to(target.units)
        except pint.PintError as exc:
            raise WorkerError([f"Cannot convert: {str(exc)[:120]}"]) from None
        a, b = _radian_power(q), _radian_power(target)
        if a != b and _key(got) == (0, 0, -1, 0, 0, 0, 0):
            # rpm·rad/s ↔ Hz·1/s: pint는 rad = 1이라 2π가 빠진다. Hz 쪽을 초당 횟수로 보고 2π를 맞춘다.
            out = out * TWO_PI ** (b - a)
            notes.append(CYCLE_NOTE)
    else:
        try:
            out = q.to_base_units()
        except pint.PintError as exc:
            raise WorkerError([f"Cannot convert to SI base units: {str(exc)[:120]}"], "Give the target unit in to.") from None
        if si := _named_si(got):
            result["si_named"] = quantity_text(out.to(si))
    if isinstance(out.magnitude, complex) or not math.isfinite(float(out.magnitude)):
        raise WorkerError(["The converted value is not a finite real number."])
    result["result"] = quantity_text(out)
    result["magnitude"] = float(out.magnitude)
    result["unit"] = unit_text(out.units)
    names = {n for x in (q, out) for n, _ in x.unit_items()}
    if names & REVOLUTION_UNITS and CYCLE_NOTE not in notes:
        notes.append(CYCLE_NOTE)
    if "ton" in names:
        notes.append("ton is the US short ton (907.185 kg); the metric tonne (1000 kg) is t.")
    if _is_offset(q):
        notes.append(
            f"Converted {text} as a temperature reading. For a temperature change (ΔT) use delta_degC: "
            f"\"{_num(q.magnitude)} delta_degC\" = {_num(q.magnitude)} K."
        )
    if notes:
        result["note"] = " ".join(notes)
    return result


# ── 진입점 ──────────────────────────────────────────────────────────


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("mode") == "convert":
        return convert(payload)
    return check_expr(payload)


def _preload() -> None:
    """무거운 import(pint·sympy)와 단위 목록 읽기를 준비 신호 전에 끝낸다(sandbox는 그 뒤 시간만 센다)."""
    global UREG
    import pint
    import sympy  # noqa: F401

    UREG = pint.UnitRegistry()


if __name__ == "__main__":
    _preload()
    serve(handle)
