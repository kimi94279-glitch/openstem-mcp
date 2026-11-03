"""verify_math 작업자: sympy로 계산하고 판정한다. sandbox.run_python으로만 돈다.

기호 계산(simplify, solveset, integrate, dsolve 등)은 끝이 안 날 수 있어서 SYMBOLIC_BUDGET 안에서만 돌린다.
equal은 수치 비교(무작위 점)를 먼저 하고, 반례가 없을 때만 기호 판정을 한다.
변수는 실수로 본다. payload의 assume="positive"면 모두 양수로 본다(물리량).
대학 수학 op(ode, matrix, laplace, inverse_laplace, series, sum, probability, 부등식 solve)도 같은 규칙을 따른다:
입력은 mathexpr로만 읽고(프라임·부등호·분포 이름은 mathexpr가 글자 단위로 먼저 나눈다), 결과는 result·latex·approx로 짧게.
"""

from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import mpmath
import numpy as np
import sympy as sp
import sympy.stats as stats  # import에 시간이 걸려 준비 신호 전에 불러 둔다
from sympy.core.function import AppliedUndef
from sympy.functions.elementary.trigonometric import TrigonometricFunction
from sympy.matrices.exceptions import MatrixError
from sympy.printing.str import StrPrinter

from openstem import mathexpr
from openstem.workers import WorkerError, serve

SYMBOLIC_BUDGET = 12.0  # 기호 계산 전체에 쓰는 시간(초). 작업자 타임아웃(기본 20초)보다 짧게 둔다.
EXTRA_CAP = 2.0  # 판정이 끝난 뒤 덧붙이는 확인(상수 차이 등) 한 번에 쓰는 시간
POSITIVE_CAP = 3.0  # 양수 가정으로 다시 확인할 때 기호 계산 한 번에 쓰는 시간
NUMERIC_POINTS = 8
# 수치 비교 점의 크기(1/100 단위). 작은 값만 보면 Min(x, 3)과 x, acos(cos(x))와 abs(x)를 가르지 못한다.
SAMPLE_BANDS = ((10, 300), (300, 1000), (10, 300), (300, 1000), (1000, 5000), (10, 300), (300, 1000), (1000, 5000))
SAMPLE_LIMIT = SAMPLE_BANDS[4][1] // 100
NUMERIC_ATTEMPTS = 80
REL_TOL = 1e-8
ABS_TOL = 1e-150  # 0 근처 값만 절대 오차로 본다(그 밖은 상대 오차)
SCAN_RANGE = (-10.0, 10.0)
SCAN_STEPS = 4001
MAX_SOLUTIONS = 10
MAX_TEXT = 800

LATEX_HINT = "Set latex=false and resend in sympy syntax. E.g. sqrt(x), x**2, exp(-x), sin(x)**2"
LATEX_MIX_HINT = "With latex=true write both expr and against in LaTeX (e.g. \\sin(x), \\sqrt{x}, \\frac{a}{b}). For sympy syntax use latex=false."
TIMEOUT_HINT = "Check in smaller steps, or compare only the answer with equal."
POSITIVE_NOTE = 'Equal if all variables are positive (for physical quantities confirm with assume="positive").'
DEGREE_HINT = "Trig functions use radians. Write degrees like 30*pi/180."
INFINITY = {"oo": sp.oo, "inf": sp.oo, "infinity": sp.oo}
LATEX_CALCULUS = re.compile(r"\\(?:i+nt|oint|lim|partial|sum)(?![A-Za-z])")
NO_LATEX_HINT = "Use latex=false with sympy syntax. E.g. y'' + y = 0, x**2 - 4 > 0, X > 1.96"
PART_CAP = 4.0  # 결과 하나(행렬의 항목, 미분방정식 분류 등)에 쓰는 시간
SCAN_STEPS_INEQ = 2001
LATEX_BARE_FUNCTION = re.compile(r"(?<![\\A-Za-z{])(?:sin|cos|tan|log|ln|exp|sqrt|abs)(?![A-Za-z])")

POSITIVE = False  # handle()이 payload의 assume으로 정한다(작업자 하나가 요청 하나만 처리한다)


class Budget:
    """여러 기호 계산이 나눠 쓰는 시간. 넘으면 TimeoutError."""

    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def run(self, fn: Callable[..., Any], *args: Any, cap: float | None = None) -> Any:
        """fn(*args)를 남은 시간(cap이 있으면 그보다 짧게) 안에 돌린다."""
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = fn(*args)
            except BaseException as exc:  # 스레드 밖으로 넘겨 다시 던진다
                box["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        wait = max(self.left(), 0.0)
        thread.join(wait if cap is None else min(wait, cap))
        if thread.is_alive():
            raise TimeoutError
        if "error" in box:
            raise box["error"]
        return box["value"]


# ── 파싱 ────────────────────────────────────────────────────────────


def symbol(name: str) -> sp.Symbol:
    return sp.Symbol(name.strip(), positive=True) if POSITIVE else sp.Symbol(name.strip(), real=True)


def parse(text: str, latex: bool, where: str, allow_equation: bool = False, operand_of: str | None = None) -> sp.Basic:
    """operand_of: diff·integrate·limit이면 그 op 이름. LaTeX 안에 이미 미분·적분·극한 기호가 있으면 거절한다."""
    if latex:
        expr = _parse_latex(text, where, operand_of)
    else:
        try:
            expr = mathexpr.parse(text, None, positive=POSITIVE)
        except mathexpr.ExprError as exc:
            raise WorkerError([f"{where}: {exc}"]) from None
    if isinstance(expr, sp.Equality) and not allow_equation:
        raise WorkerError([f"{where}: equations (=) only in op=solve. Send a single expression."])
    return expr


CALCULUS_EXAMPLES = {
    "integrate": '\\int_0^1 x^2 dx → expr="x^2", bounds=[0, 1]',
    "diff": '\\frac{d}{dx} x^3 → expr="x^3"',
    "limit": '\\lim_{x\\to 0} \\frac{\\sin x}{x} → expr="\\frac{\\sin x}{x}", point="0"',
    "sum": '\\sum_{n=1}^\\infty \\frac{1}{n^2} → expr="\\frac{1}{n^2}", bounds=[1, "oo"]',
}


def _calculus_error(where: str, op: str) -> WorkerError:
    return WorkerError(
        [
            f"{where}: already contains an integral/derivative/limit/sum sign (\\int, \\frac{{d}}{{dx}}, \\lim, \\sum). "
            f"For op={op} send only the expression inside it."
        ],
        f"E.g. {CALCULUS_EXAMPLES[op]}. To evaluate the whole expression including the sign, use op=evaluate.",
    )


def _parse_latex(text: str, where: str, operand_of: str | None = None) -> sp.Basic:
    if operand_of and LATEX_CALCULUS.search(text):
        raise _calculus_error(where, operand_of)
    if m := LATEX_BARE_FUNCTION.search(text):  # sin(x)를 LaTeX로 읽으면 s*i*n(x)가 된다
        raise WorkerError([f"{where}: '{m.group(0)}' has no \\ before it (looks like sympy syntax sent as LaTeX)."], LATEX_MIX_HINT)
    try:
        from sympy.parsing.latex import parse_latex
    except ImportError:
        raise WorkerError(["LaTeX input (antlr4-python3-runtime) is not installed."], LATEX_HINT) from None
    try:
        expr = parse_latex(text)
    except ImportError:
        raise WorkerError(["LaTeX input (antlr4-python3-runtime) is not installed."], LATEX_HINT) from None
    except Exception as exc:
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:150]
        raise WorkerError([f"{where}: cannot parse LaTeX: {first}"], LATEX_HINT) from None
    # \sqrt[3]{-8}는 실수 세제곱근(-2)이다. parse_latex는 근호만 지수를 Rational(1, n)으로 남기므로
    # (x^{1/3}은 1*3**-1 꼴) 그것만 골라 홀수 차수를 real_root로 바꾼다. subs·doit이 지수를 합치기 전에 해야 한다.
    expr = expr.replace(
        lambda e: e.is_Pow and isinstance(e.exp, sp.Rational) and e.exp.p == 1 and e.exp.q % 2 == 1 and e.exp.q > 1,
        lambda e: sp.real_root(e.base, e.exp.q),
    )
    if operand_of and expr.has(sp.Integral, sp.Derivative, sp.Limit, sp.Sum):
        raise _calculus_error(where, operand_of)
    if undefined := sorted({str(f) for f in expr.atoms(AppliedUndef)}):
        names = ", ".join(undefined[:3])
        raise WorkerError([f"{where}: read as LaTeX it has undefined function {names} (sympy syntax seems mixed in)."], LATEX_MIX_HINT)
    # parse_latex는 e·\pi를 변수로 읽고 가정 없는 기호를 만든다: 상수로 바꾸고 변수는 실수(양수)로 맞춘다.
    expr = expr.subs({sp.Symbol("e"): sp.E, sp.Symbol("pi"): sp.pi})
    expr = expr.subs({s: symbol(s.name) for s in expr.free_symbols if isinstance(s, sp.Symbol)})
    return expr.doit()


def parse_equation(text: str, latex: bool, where: str) -> sp.Expr:
    """'좌변 = 우변'(또는 좌변만)을 좌변 - 우변 꼴로 읽는다."""
    if latex:
        expr = parse(text, True, where, allow_equation=True)
        return expr.lhs - expr.rhs if isinstance(expr, sp.Equality) else expr
    sides = text.replace("==", "=").split("=")
    if len(sides) > 2:
        raise WorkerError([f"{where}: use '=' only once per equation."])
    if len(sides) == 2:
        return parse(sides[0], False, f"{where} left side") - parse(sides[1], False, f"{where} right side")
    return parse(text, False, where)


def parse_point(text: str) -> tuple[sp.Basic, str | None]:
    """극한 위치. '0+'(우극한), '0-'(좌극한), 'oo', '-oo'."""
    text = text.strip()
    direction = None
    if len(text) > 1 and text[-1] in "+-":
        direction, text = text[-1], text[:-1].strip()
    sign = -1 if text.startswith("-") else 1
    bare = text.lstrip("+-").strip().lower()
    if bare in INFINITY:
        return sign * INFINITY[bare], None
    return parse(text, False, "point"), direction


def parse_assignments(text: str, expr: sp.Basic) -> tuple[dict[sp.Symbol, sp.Basic], list[str]]:
    """evaluate의 point: 'x=2, y=3' 또는 변수가 하나면 '2'. 식에 없는 이름은 따로 돌려준다."""
    by_name = {s.name: s for s in expr.free_symbols}
    subs: dict[sp.Symbol, sp.Basic] = {}
    unused: list[str] = []
    for part in [p for p in text.split(",") if p.strip()]:
        if "=" in part:
            name, value = (t.strip() for t in part.split("=", 1))
            if err := mathexpr.variable_name_error(name):
                raise WorkerError([f"point '{part.strip()}': {err}"])
            parsed = parse(value, False, f"point '{part.strip()}'")
            if name in by_name:
                subs[by_name[name]] = parsed
            else:
                unused.append(name)
        else:
            free = sorted(expr.free_symbols, key=lambda s: s.name)
            if len(free) != 1:
                names = ", ".join(s.name for s in free) or "none"
                raise WorkerError([f"point '{part.strip()}': variables are {names}, so it is unclear which one this value is for. Write \"x=2, y=3\"."])
            subs[free[0]] = parse(part, False, "point")
    return subs, unused


def pick_var(exprs: list[sp.Basic], var: str | None, action: str, default: str = "x") -> sp.Symbol:
    """default: 변수가 여럿일 때 이 이름이 있으면 그것(라플라스는 t, 급수의 합은 n)."""
    free = sorted(set().union(*(e.free_symbols for e in exprs)), key=lambda s: s.name)
    if var and var.strip():
        return next((s for s in free if s.name == var.strip()), symbol(var))
    if len(free) == 1:
        return free[0]
    if not free or default in [s.name for s in free]:
        return next((s for s in free if s.name == default), symbol(default))
    names = ", ".join(s.name for s in free)
    raise WorkerError([f"Several variables ({names}); unclear which to {action}. Set var (e.g. var=\"{free[0].name}\")."])


def exact(expr: sp.Basic) -> sp.Basic:
    """소수를 분수로(9.8 → 49/5). 1.2*x + 0.6*x와 1.8*x가 소수 오차로 달라지지 않게 한다."""
    return sp.nsimplify(expr, rational=True) if expr.has(sp.Float) else expr


def as_positive(expr: sp.Basic) -> sp.Basic:
    return expr.xreplace({s: sp.Symbol(s.name, positive=True) for s in expr.free_symbols})


# ── 결과 꼴 ─────────────────────────────────────────────────────────


class _Printer(StrPrinter):
    """자연상수를 E 대신 e로 쓴다(입력에서 E는 변수다). 미분방정식의 y(x)는 y, 도함수는 y''로 쓴다."""

    def _print_Exp1(self, expr: sp.Basic) -> str:
        return "e"

    def _print_AppliedUndef(self, expr: sp.Basic) -> str:
        return expr.func.__name__

    def _print_Derivative(self, expr: sp.Basic) -> str:
        if isinstance(expr.expr, AppliedUndef) and len(expr.variables) == expr.derivative_count:
            return expr.expr.func.__name__ + "'" * expr.derivative_count
        return super()._print_Derivative(expr)


def clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " …(truncated)"


def sstr(expr: sp.Basic, ascending: bool = False) -> str:
    """9.80000000000000 대신 9.8처럼 쓴다. ascending이면 낮은 차수부터(급수)."""
    settings: dict[str, Any] = {"full_prec": False}
    if ascending:
        settings["order"] = "rev-lex"
    return _Printer(settings).doprint(expr)


def approx(expr: sp.Basic) -> float | str | None:
    if not getattr(expr, "is_number", False) or expr.is_Integer or expr.has(sp.oo, -sp.oo, sp.zoo, sp.nan):
        return None
    value = sp.N(expr, 15)
    re_part, im_part = (float(v) for v in value.as_real_imag())
    if abs(im_part) <= 1e-12 * max(1.0, abs(re_part)):
        return float(f"{re_part:.12g}")
    return f"{re_part:.10g}{'+' if im_part >= 0 else '-'}{abs(im_part):.10g}i"


def show(expr: sp.Basic) -> dict[str, Any]:
    out: dict[str, Any] = {"result": clip(sstr(expr)), "latex": clip(sp.latex(expr))}
    if (value := approx(expr)) is not None:
        out["approx"] = value
    return out


def odd_root_note(*exprs: sp.Basic) -> str | None:
    """음수일 수 있는 밑의 홀수 분수 거듭제곱((-8)**(1/3), x**(1/3))은 sympy가 복소수 주가지로 계산한다고 알린다."""
    for expr in exprs:
        for pw in expr.atoms(sp.Pow):
            q = pw.exp.q if isinstance(pw.exp, sp.Rational) else 0
            if q > 1 and q % 2 == 1 and pw.base.is_nonnegative is not True:
                base, n, k = sstr(pw.base), q, pw.exp.p
                if n == 3:
                    real = f"cbrt({base})" + (f"**{k}" if k != 1 else "")
                else:
                    real = ("" if k % 2 == 0 else f"sign({base})*") + f"abs({base})**({k}/{n})"
                return (
                    f"{sstr(pw)} uses the principal complex root when the base is negative (e.g. (-8)**(1/3) = 1 + 1.732i, not -2). "
                    f"For the real root write {real}."
                )
    return None


def degree_hint(*exprs: sp.Basic) -> str | None:
    """sin(30)처럼 pi 없는 숫자를 넣은 삼각함수가 있으면 라디안 안내를 돌려준다."""
    for expr in exprs:
        for f in expr.atoms(TrigonometricFunction):
            if f.args[0].is_number and not f.args[0].has(sp.pi):
                return DEGREE_HINT
    return None


# ── 수치 비교 ───────────────────────────────────────────────────────


def _float(part: sp.Basic) -> float:
    # 끝까지 상쇄돼 유효숫자가 없는 값(sympy가 0.e-165처럼 낸다)은 0이다.
    return 0.0 if part.is_Float and part._prec < 10 else float(part)


def real_value(expr: sp.Basic, point: dict[sp.Symbol, sp.Basic]) -> float | None:
    """그 점에서의 실수 값. 정의되지 않거나(0으로 나눔 등) 실수가 아니면 None."""
    try:
        value = sp.N(expr.subs(point), 30)
    except (ZeroDivisionError, ValueError, TypeError):
        return None
    if not value.is_number or value.has(sp.oo, -sp.oo, sp.zoo, sp.nan):
        return None
    re_part, im_part = (_float(v) for v in value.as_real_imag())
    if abs(im_part) > 1e-12 * abs(re_part) + ABS_TOL:
        return None
    return re_part


def close(a: float, b: float) -> bool:
    """상대 오차로 비교한다(6.674e-11과 6.675e-11은 다르다). 0 근처만 ABS_TOL로 본다."""
    return abs(a - b) <= max(REL_TOL * max(abs(a), abs(b)), ABS_TOL)


def sample_point(rng: random.Random, symbols: list[sp.Symbol], k: int) -> dict[sp.Symbol, sp.Basic]:
    """k번째 비교 점. 크기는 SAMPLE_BANDS를 돌아가며(0.1~3, 3~10, 10~50), 부호는 무작위(양수 변수는 양수)."""
    lo, hi = SAMPLE_BANDS[k % len(SAMPLE_BANDS)]
    return {s: sp.Rational((1 if s.is_positive else rng.choice([-1, 1])) * rng.randint(lo, hi), 100) for s in symbols}


def sampled_range(points: list[dict[str, float]]) -> str:
    values = [v for p in points for v in p.values()]
    return f"[{min(values):g}, {max(values):g}]"


def numeric_compare(a: sp.Basic, b: sp.Basic, symbols: list[sp.Symbol]) -> dict[str, Any]:
    """무작위 점(변수마다 -50~50, 0 근처 제외. 양수 변수는 양수)에서 두 식의 실수 값을 비교한다. 시드는 고정(재현성)."""
    rng = random.Random(20261102)
    checked: list[dict[str, float]] = []
    one_sided = 0
    for _ in range(NUMERIC_ATTEMPTS if symbols else 1):
        if len(checked) >= NUMERIC_POINTS:
            break
        point = sample_point(rng, symbols, len(checked))
        va, vb = real_value(a, point), real_value(b, point)
        if va is None or vb is None:
            one_sided += (va is None) != (vb is None)
            continue
        at = {s.name: float(v) for s, v in point.items()}
        if not close(va, vb):
            return {"checked": len(checked) + 1, "agree": False, "counterexample": {"at": at, "expr": va, "against": vb}}
        checked.append(at)
    result: dict[str, Any] = {"checked": len(checked), "agree": True}
    if checked and symbols:
        result["points"] = checked
    if one_sided:
        result["domain_note"] = f"At {one_sided} point(s) only one expression had a real value (the expressions have different domains)."
    return result


def positive_equal(a: sp.Basic, b: sp.Basic, budget: Budget) -> bool:
    """변수를 모두 양수로 두면 같다고 증명되는가(sqrt(m**2*v**2) = m*v, log(a*b) = log(a) + log(b))."""
    if budget.left() < 1.0:
        return False
    pa, pb = as_positive(a), as_positive(b)
    symbols = sorted(pa.free_symbols | pb.free_symbols, key=lambda s: s.name)
    if not numeric_compare(pa, pb, symbols)["agree"]:
        return False
    try:
        return budget.run(sp.simplify, pa - pb, cap=POSITIVE_CAP) == 0 or budget.run(pa.equals, pb, cap=POSITIVE_CAP) is True
    except Exception:  # 시간 초과 등: 증명 못 함
        return False


def numeric_roots(expr: sp.Expr, v: sp.Symbol) -> list[float]:
    """[-10, 10]을 훑어 부호가 바뀌는 곳을 이분법으로 좁힌다. 극(발산)은 버린다."""
    fn = sp.lambdify(v, expr, "numpy")

    def f(x: float) -> float:
        with np.errstate(all="ignore"):
            y = complex(fn(np.float64(x)))
        return y.real if abs(y.imag) < 1e-12 and np.isfinite(y.real) else float("nan")

    xs = np.linspace(*SCAN_RANGE, SCAN_STEPS)
    ys = [f(x) for x in xs]
    roots: list[float] = []
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if not (np.isfinite(y0) and np.isfinite(y1)):
            continue
        if y0 == 0:
            roots.append(float(x0))
            continue
        if y0 * y1 > 0:
            continue
        lo, hi, flo = float(x0), float(x1), y0
        for _ in range(60):
            mid = (lo + hi) / 2
            fm = f(mid)
            if not np.isfinite(fm):
                break
            if flo * fm <= 0:
                hi = mid
            else:
                lo, flo = mid, fm
        root = (lo + hi) / 2
        value = f(root)
        if np.isfinite(value) and abs(value) < 1e-6 * (1 + max(abs(y0), abs(y1))):
            roots.append(root)
    unique: list[float] = []
    for r in roots:
        if not unique or abs(r - unique[-1]) > 1e-7:
            unique.append(float(f"{r:.10g}"))
    return unique[:MAX_SOLUTIONS]


# ── op ──────────────────────────────────────────────────────────────


def op_equal(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    a = parse(p["expr"], p["latex"], "expr")
    b = parse(p["against"], p["latex"], "against")
    symbols = sorted(a.free_symbols | b.free_symbols, key=lambda s: s.name)
    out: dict[str, Any] = {
        "parsed": {"expr": clip(sstr(a)), "against": clip(sstr(b))},
        "variables": [s.name for s in symbols],
    }
    ea, eb = exact(a), exact(b)
    numeric = numeric_compare(ea, eb, symbols)
    out["numeric"] = numeric
    notes: list[str] = []
    recheck = bool(symbols) and not POSITIVE  # 실수로는 다를 때 양수 가정으로 다시 본다
    if not numeric["agree"]:
        out.update(matches=False, method="numeric")
        ce = numeric["counterexample"]
        va, vb = ce["expr"], ce["against"]
        if not symbols:
            out["values"] = {"expr": va, "against": vb}
            del numeric["counterexample"]
            notes.append("The two values (no variables) differ.")
        elif (rel := abs(va - vb) / max(abs(va), abs(vb))) < 1e-3:
            notes.append(f"Relative difference at the counterexample is small ({rel:.1e}); a rounded constant would explain it.")
        if recheck and any(v < 0 for v in ce["at"].values()) and positive_equal(ea, eb, budget):
            notes.append(POSITIVE_NOTE)
        elif symbols:
            try:
                diff = budget.run(sp.simplify, ea - eb, cap=EXTRA_CAP)
                if diff.is_number and diff != 0:
                    notes.append(f"The expressions differ by the constant {sstr(diff)} (for antiderivatives this may be the integration constant).")
            except Exception:  # 시간 초과 등: 덧붙이는 말이라 생략
                pass
        if hint := degree_hint(a, b):
            notes.append(hint)
        if notes:
            out["note"] = " ".join(notes)
        return out
    try:
        if budget.run(sp.simplify, ea - eb) == 0:
            out.update(matches=True, method="symbolic")
            return out
        verdict = budget.run(ea.equals, eb)
    except TimeoutError:
        verdict = None
        notes.append("Symbolic check did not finish in time.")
    if verdict is True:
        out.update(matches=True, method="symbolic")
        return out
    if verdict is False and symbols and (not numeric["checked"] or not numeric.get("domain_note")):
        # sympy의 equals()는 복소수 점에서도 비교한다. 실수 점에서 반례가 없으면 '다르다'고 단정하지 않는다
        # (abs(x - 1) + abs(x + 1)과 2*Max(1, abs(x)), atan(x) + atan(1/x)과 pi/2*sign(x) 등).
        verdict = None
        if numeric["checked"]:
            notes.append(
                f"sympy's symbolic test reports a difference, but it also tests complex values; over the reals they are "
                f"numerically equal at all {numeric['checked']} points sampled in {sampled_range(numeric['points'])} (not a proof)."
            )
        else:
            notes.append("Found no real points where both have a value, so they could not be compared over the reals.")
            notes += _log_abs_note(ea, eb, symbols)
        out.update(matches="unknown", method="numeric")
    elif verdict is False:
        out.update(matches=False, method="symbolic")
    else:
        out.update(matches="unknown", method="numeric")
        notes.append("Could not prove equality symbolically.")
        if not numeric["checked"]:
            notes.append("Found no points where both could be compared numerically.")
        elif symbols:
            notes.append(f"Numerically equal at all {numeric['checked']} points sampled in {sampled_range(numeric['points'])} (not a proof).")
        else:
            notes.append("The two values agree numerically (not a proof).")
    if recheck and positive_equal(ea, eb, budget):
        notes.append(POSITIVE_NOTE)
    out["note"] = " ".join(notes)
    return out


def _log_abs_note(a: sp.Basic, b: sp.Basic, symbols: list[sp.Symbol]) -> list[str]:
    """log(…)를 ln|…|로 읽으면(부정적분의 관례) 같은지. -log(sin(x) - 1)/2 + …는 실수 x에서 값이 없다."""
    if not (a.has(sp.log) or b.has(sp.log)):
        return []
    to_abs = {sp.log: lambda *args: sp.log(sp.Abs(args[0]), *args[1:])}
    try:
        again = numeric_compare(a.replace(sp.log, to_abs[sp.log]), b.replace(sp.log, to_abs[sp.log]), symbols)
    except Exception:  # 덧붙이는 확인이라 실패하면 생략
        return []
    if again["agree"] and again["checked"]:
        return [f"Reading every log(…) as ln|…| (as for antiderivatives), they agree at all {again['checked']} points sampled in {sampled_range(again['points'])}."]
    return []


def op_simplify(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr")
    result = budget.run(sp.simplify, expr)
    out = {"parsed": clip(sstr(expr)), **show(result)}
    forms = {}
    try:
        factored = budget.run(sp.factor, result)
        expanded = budget.run(sp.expand, result)
    except Exception:  # 시간 초과나 인수분해할 수 없는 꼴이면 생략
        factored = expanded = result
    if factored != result:
        forms["factor"] = clip(sstr(factored))
    if expanded != result and expanded != factored:
        forms["expand"] = clip(sstr(expanded))
    if forms:
        out["forms"] = forms
    return out


def _periodic(sol: sp.Set) -> bool:
    """정수 n으로 이어지는 해(ImageSet over Integers, 예: 2*n*pi + pi/6)가 들어 있는가."""
    return any(sp.S.Integers in s.base_sets for s in sol.atoms(sp.ImageSet))


def _finite_part(sol: sp.Set) -> list[sp.Basic] | None:
    """Intersection({…}, Reals)·Complement({…}, {0})처럼 조건이 붙은 유한 해 집합에서 해를 꺼낸다."""
    if isinstance(sol, sp.FiniteSet):
        return list(sol)
    if isinstance(sol, sp.Intersection):
        return next((list(s) for s in sol.args if isinstance(s, sp.FiniteSet)), None)
    if isinstance(sol, sp.Complement):
        return _finite_part(sol.args[0])
    if isinstance(sol, sp.Union):
        parts = [_finite_part(s) for s in sol.args]
        return None if any(part is None for part in parts) else [s for part in parts for s in part]
    return None


def _undefined_part(expr: sp.Basic, point: dict[sp.Symbol, sp.Basic]) -> sp.Basic | None:
    """그 점에서 실수 값이 없는 부분식(log(-3), sqrt(-1), 1/0 등). 모두 정의되면 None.

    좌변 - 우변 전체만 보면 log(-2) + log(-5) - log(10) = 2πi처럼 허수가 남거나, 허수끼리 지워져 가짜 해가 통과한다.
    Piecewise는 고른 갈래만 의미가 있으므로 안을 들여다보지 않는다.
    """
    if not isinstance(expr, sp.Expr) or expr.is_Atom or not (expr.free_symbols & set(point)):
        return None
    if not isinstance(expr, sp.Piecewise):
        for arg in expr.args:
            if (bad := _undefined_part(arg, point)) is not None:
                return bad
    return expr if real_value(expr, point) is None else None


def check_root(eq: sp.Expr, point: dict[sp.Symbol, sp.Basic]) -> str | None:
    """sympy가 낸 해를 원래 식(좌변 - 우변)에 다시 넣어 본다. 실수 해가 아니면 이유를, 맞으면 None을 돌려준다.

    값이 숫자로 정해지지 않는 해(다른 문자가 남는 공식)는 판정하지 않는다(None).
    """
    if any(not sp.sympify(v).is_number for v in point.values()):
        return None
    if (bad := _undefined_part(eq, point)) is not None:
        return f"{sstr(bad)} has no real value there"
    value = real_value(eq, point)
    terms = [real_value(t, point) for t in sp.Add.make_args(eq)]
    if value is None or None in terms:
        return "the equation has no real value there"
    if abs(value) > 1e-9 * max(sum(abs(t) for t in terms), 1.0):
        return "the two sides are not equal there"
    return None


def _drop_false_roots(eq: sp.Expr, v: sp.Symbol, roots: list[sp.Basic], notes: list[str]) -> list[sp.Basic]:
    kept, dropped = [], []
    for r in roots:
        reason = check_root(eq, {v: r})
        (dropped if reason else kept).append((r, reason))
    if dropped:
        notes.append(
            "Substituted back and dropped "
            + "; ".join(f"{v.name} = {sstr(r)} ({reason})" for r, reason in dropped)
            + ". Not a real solution (extraneous root)."
        )
    return [r for r, _ in kept]


def _solve_single(eq: sp.Expr, v: sp.Symbol, budget: Budget) -> dict[str, Any]:
    out: dict[str, Any] = {"parsed": f"{clip(sstr(eq))} = 0", "variable": v.name}
    others = ", ".join(sorted(s.name for s in eq.free_symbols - {v}))
    notes: list[str] = []
    try:
        sol = budget.run(sp.solveset, eq, v, sp.S.Reals)
    except (TimeoutError, NotImplementedError):
        sol = None
    if isinstance(sol, sp.ConditionSet) and v not in sol.condition.free_symbols:  # 다른 문자에 조건이 붙은 해
        notes.append(f"Condition: {sstr(sol.condition)}.")
        sol = sol.base_set
    if sol == sp.S.EmptySet:
        out["solutions"] = []
        out["note"] = "No real solutions."
        vc = sp.Symbol(v.name)
        try:
            csol = budget.run(sp.solveset, eq.subs(v, vc), vc, sp.S.Complexes)
            if isinstance(csol, sp.FiniteSet) and csol:
                out["complex_solutions"] = [show(s) for s in list(csol)[:MAX_SOLUTIONS]]
        except Exception:
            pass
        return out
    if sol == sp.S.Reals:
        out["note"] = "Every real number is a solution (identity)."
        return out
    if sol is not None and _periodic(sol):
        out["general"] = show(sol)
        notes.insert(0, "Infinitely many solutions; n in general is an integer.")
        window, label = (sp.Interval.Lopen(0, 2 * sp.pi), "(0, 2π]") if v.is_positive else (sp.Interval(-2 * sp.pi, 2 * sp.pi), "[-2π, 2π]")
        try:
            near = budget.run(sol.intersect, window, cap=EXTRA_CAP)
            if isinstance(near, sp.FiniteSet) and near:
                near = _drop_false_roots(eq, v, sorted(near, key=sp.default_sort_key), notes)
                out["solutions"] = [show(s) for s in near[:MAX_SOLUTIONS]]
                notes.append(f"solutions lists those in {label}.")
        except Exception:
            pass
        out["note"] = " ".join(notes)
        return out

    found = _finite_part(sol) if sol is not None else None
    if found is None and others:  # 문자가 섞인 공식은 solve가 더 잘 푼다(T = 2*pi*sqrt(L/g) → L)
        try:
            found = budget.run(sp.solve, eq, v) or None
        except (TimeoutError, NotImplementedError, ValueError, TypeError):
            found = None
    if found is not None and others and not isinstance(sol, sp.FiniteSet) and not POSITIVE:
        notes.append(f'Solutions valid when real, depending on the other letters ({others}). For physical quantities solve with assume="positive".')
    if found is not None:
        if found:
            found = _drop_false_roots(eq, v, list(found), notes)
            if not found:
                out["solutions"] = []
                out["note"] = " ".join([*notes, "No real solutions."])
                return out
        if v.is_positive and (kept := [s for s in found if s.is_positive is not False]) != found:
            found = kept
            notes.append("Dropped non-positive solutions (assume=positive).")
        out["solutions"] = [show(s) for s in sorted(found, key=sp.default_sort_key)[:MAX_SOLUTIONS]]
        if notes:
            out["note"] = " ".join(notes)
        return out
    if others:
        out["note"] = (
            f"Could not solve symbolically (this does not mean there is no solution). Other letters ({others}) remain, "
            f"so roots cannot be found numerically either. Substitute values for {others} and solve again."
        )
        return out
    roots = numeric_roots(eq, v)
    if v.is_positive:
        roots = [r for r in roots if r > 0]
    out["solutions"] = [{"approx": r} for r in roots]
    out["note"] = f"Could not solve symbolically; found roots numerically in [{SCAN_RANGE[0]:g}, {SCAN_RANGE[1]:g}] (roots outside may be missing)."
    return out


def op_solve(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    texts = [t for t in p["expr"].split(";") if t.strip()]
    if any(INEQUALITY_SIGN.search(t) for t in texts):
        return solve_inequalities(texts, p, budget)
    eqs =[parse_equation(t, p["latex"], f"equation {i + 1}") for i, t in enumerate(texts)]
    names = [n.strip() for n in (p.get("var") or "").split(",") if n.strip()]
    free = sorted(set().union(*(e.free_symbols for e in eqs)), key=lambda s: s.name)
    by_name = {s.name: s for s in free}
    if names:
        if missing := [n for n in names if n not in by_name]:
            raise WorkerError([f"var {', '.join(missing)} not in the equations. Variables present: {', '.join(s.name for s in free)}"])
        unknowns = [by_name[n] for n in names]
    elif len(eqs) == 1:
        unknowns = [pick_var(eqs, None, "solve for")]
    elif len(free) == len(eqs):
        unknowns = free
    else:
        raise WorkerError([f"Set the unknowns with var (e.g. var=\"x, y\"). Variables present: {', '.join(s.name for s in free)}"])

    if len(eqs) == 1 and len(unknowns) == 1:
        return _solve_single(eqs[0], unknowns[0], budget)
    solutions = budget.run(lambda: sp.solve(eqs, unknowns, dict=True))
    dropped = []
    for sol in list(solutions):
        reasons = [r for e in eqs if (r := check_root(e, sol))]
        if reasons:
            solutions.remove(sol)
            dropped.append(", ".join(f"{k.name} = {sstr(val)}" for k, val in sol.items()) + f" ({reasons[0]})")
    out: dict[str, Any] = {
        "parsed": [f"{clip(sstr(e))} = 0" for e in eqs],
        "variables": [u.name for u in unknowns],
        "solutions": [{k.name: show(v) for k, v in s.items()} for s in solutions[:MAX_SOLUTIONS]],
    }
    notes = []
    if dropped:
        notes.append(f"Substituted back and dropped {'; '.join(dropped)}. Not a real solution (extraneous root).")
    if not solutions:
        notes.append("No solution (or sympy could not find one).")
    if notes:
        out["note"] = " ".join(notes)
    return out


def op_diff(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr", operand_of="diff")
    v = pick_var([expr], p.get("var"), "differentiate by")
    order = int(p.get("order") or 1)
    derivative = sp.diff(expr, v, order)
    try:
        derivative = budget.run(sp.simplify, derivative)
    except TimeoutError:
        pass
    return {"parsed": clip(sstr(expr)), "variable": v.name, "order": order, **show(derivative)}


def op_integrate(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr", operand_of="integrate")
    v = pick_var([expr], p.get("var"), "integrate over")
    out: dict[str, Any] = {"parsed": clip(sstr(expr)), "variable": v.name}
    bounds = p.get("bounds")
    if not bounds:
        try:
            result = budget.run(sp.integrate, expr, v)
        except TimeoutError:
            result = sp.Integral(expr, v)
        if result.has(sp.Integral):
            raise WorkerError(
                ["Could not find the antiderivative symbolically."],
                "For an interval give bounds to get the definite value, or retry after a substitution.",
            )
        out.update(show(result))
        out["note"] = "The integration constant C is omitted."
        if not POSITIVE and result.atoms(sp.log) - expr.atoms(sp.log):  # ∫1/x dx = log(x)
            out["note"] += " Over the reals read log(…) as ln|…| (e.g. ∫1/x dx = ln|x|)."
        if root := odd_root_note(expr):
            out["note"] += " " + root
        return out

    lo, hi = (parse_point(str(b))[0] for b in bounds)
    out["bounds"] = [sstr(lo), sstr(hi)]
    try:
        result = budget.run(sp.integrate, expr, (v, lo, hi))
    except TimeoutError:
        result = sp.Integral(expr, (v, lo, hi))
    if result in (sp.oo, -sp.oo):
        out.update(result=sstr(result), note=f"The definite integral diverges to {'+' if result == sp.oo else '-'}∞.")
        return out
    if result.has(sp.AccumBounds):  # ∫_0^∞ sin(x) dx: 부분 적분값이 0과 2 사이를 오간다
        bounds_text = ", ".join(f"{sstr(b.min)} … {sstr(b.max)}" for b in result.atoms(sp.AccumBounds))
        out.update(
            result="diverges (oscillates)",
            note=f"The definite integral does not converge: the partial integrals keep oscillating (between {bounds_text}) and approach no single value.",
        )
        return out
    if result.has(sp.nan, sp.zoo):
        out.update(result="diverges", note="The definite integral diverges and has no value (the function goes to infinity inside the interval).")
        return out
    if result.has(sp.Integral):
        try:
            value = budget.run(lambda: sp.N(sp.Integral(expr, (v, lo, hi)), 15))
        except TimeoutError:
            raise
        except Exception:  # 구간 안의 발산점에서 수치 적분이 깨진다
            value = sp.nan
        out["result"] = "not computed symbolically"
        if (out_approx := approx(value)) is None:
            out["note"] = "Could not compute numerically either (it diverges inside the interval, or other letters remain). Split the interval and check."
            return out
        out["approx"] = out_approx
        out["note"] = "approx is a numerical integral."
        return out
    out.update(show(result))
    if (root := odd_root_note(expr)) and not (isinstance(lo, sp.Basic) and lo.is_nonnegative):
        out["note"] = root
    return out


def op_limit(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr", operand_of="limit")
    v = pick_var([expr], p.get("var"), "take the limit in")
    target, direction = parse_point(p["point"])
    out: dict[str, Any] = {"parsed": clip(sstr(expr)), "variable": v.name, "point": sstr(target)}
    if direction or target in (sp.oo, -sp.oo):
        if direction:
            out["direction"] = "right-hand" if direction == "+" else "left-hand"
        result = budget.run(sp.limit, expr, v, target, direction or "+")
    else:
        try:
            result = budget.run(sp.limit, expr, v, target, "+-")
        except ValueError:
            result = sp.zoo
        if result == sp.zoo:  # 양쪽이 다르거나 복소 무한대: 실수 극한으로 좌우를 따로 본다
            left = budget.run(sp.limit, expr, v, target, "-")
            right = budget.run(sp.limit, expr, v, target, "+")
            if left != right:
                out.update(result="does not exist", left=show(left), right=show(right))
                out["note"] = "The left and right limits differ, so the limit does not exist."
                return out
            result = left
    out.update(show(result))
    if isinstance(result, sp.AccumBounds):
        out["note"] = "The value oscillates and does not converge (only its range is shown)."
    return out


def op_evaluate(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr")
    subs, unused = parse_assignments(p["point"], expr) if p.get("point") else ({}, [])
    value = expr.subs(subs)
    try:
        value = budget.run(sp.simplify, value)
    except TimeoutError:
        pass
    undefined = value.has(sp.zoo, sp.nan)
    if value.is_number and value.has(sp.Float) and not undefined:  # 0.6389*sqrt(10)처럼 섞이면 소수 하나로
        value = sp.N(value, 15)
    out: dict[str, Any] = {"parsed": clip(sstr(expr)), **show(value)}
    if subs:
        out["at"] = {k.name: sstr(v) for k, v in subs.items()}
    notes: list[str] = []
    if value.has(sp.nan):
        notes.append("Indeterminate form like 0/0. Use op=limit for the value approaching that point.")
    elif undefined:
        notes.append("Undefined (division by zero, log(0), etc.). Use op=limit for the value approaching that point.")
    if value.free_symbols:
        names = ", ".join(sorted(s.name for s in value.free_symbols))
        notes.append(f"No value for {names}, so the result stays symbolic. Give values in point (e.g. \"x=2\").")
    if unused:
        notes.append(f"Ignored {', '.join(unused)} in point (not in the expression).")
    if value.is_number and not undefined and value.is_real is not True and (root := odd_root_note(expr) or odd_root_note(expr.subs(subs))):
        notes.append(root)
    if hint := degree_hint(expr):
        notes.append(hint)
    if notes:
        out["note"] = " ".join(notes)
    return out


# ── 0인지 판정(대입 확인용) ─────────────────────────────────────────


def vanishes(expr: sp.Basic, budget: Budget) -> tuple[bool | str, sp.Basic]:
    """expr가 0인가: True(기호로 증명) | False(0이 아닌 점이 있음) | "unknown". 간단히 한 식도 돌려준다.

    잔차는 0 근처라 상대 오차를 쓸 수 없다. 점마다 항들의 크기 합에 견줘 본다.
    """
    try:
        expr = budget.run(sp.simplify, expr, cap=PART_CAP)
    except Exception:  # 시간 초과 등: 수치로만 본다
        pass
    if expr == 0:
        return True, expr
    symbols = sorted(expr.free_symbols, key=lambda s: s.name)
    rng = random.Random(20261103)
    checked = 0
    for _ in range(NUMERIC_ATTEMPTS if symbols else 1):
        if checked >= NUMERIC_POINTS:
            break
        point = {s: sp.Rational((1 if s.is_positive else rng.choice([-1, 1])) * rng.randint(10, 300), 100) for s in symbols}
        value = real_value(expr, point)
        terms = [real_value(t, point) for t in sp.Add.make_args(expr)]
        if value is None or None in terms:
            continue
        checked += 1
        if abs(value) > 1e-9 * max(sum(abs(t) for t in terms), 1e-300):
            return False, expr
    return "unknown", expr


def combine(*results: bool | str) -> bool | str:
    if any(r is False for r in results):
        return False
    return True if all(r is True for r in results) else "unknown"


# ── 부등식(op=solve) ────────────────────────────────────────────────

INEQUALITY_SIGN = re.compile(r"[<>≤≥]")
RELATIONS = {"<": sp.Lt, "<=": sp.Le, ">": sp.Gt, ">=": sp.Ge, "=": sp.Eq}
COMPARE = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal}
FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "=="}
NUMERIC_MARK = "\x00numeric"  # notes 안에서 수치로 푼 부등식이 있었다는 표시(밖으로 나가지 않는다)


def parse_relations(text: str, where: str) -> tuple[list[sp.Basic], list[str], str]:
    """'0 < x <= 3' → 변 [0, x, 3], 기호 ['<', '<='], 읽은 꼴 '0 < x <= 3'."""
    try:
        sides, ops = mathexpr.split_relation(text)
    except mathexpr.ExprError as exc:
        raise WorkerError([f"{where}: {exc}"]) from None
    exprs = [parse(s, False, where) for s in sides]
    shown = sstr(exprs[0]) + "".join(f" {op} {sstr(e)}" for op, e in zip(ops, exprs[1:]))
    return [exact(e) for e in exprs], ops, clip(shown)


def _set_key(s: sp.Basic) -> tuple[float, float]:
    """구간·값을 왼쪽부터 늘어놓는 열쇠(복소수는 실수부, 허수부 순)."""
    try:
        z = complex(sp.N(s.inf if isinstance(s, sp.Set) else s))
        return (z.real, z.imag)
    except (TypeError, ValueError, AttributeError):
        return (0.0, 0.0)


def set_text(s: sp.Set) -> str:
    """구간을 (-oo, -2) ∪ (2, oo)처럼 쓴다."""
    if s == sp.S.EmptySet:
        return "∅"
    if isinstance(s, sp.Interval):
        return f"{'(' if s.left_open else '['}{sstr(s.start)}, {sstr(s.end)}{')' if s.right_open else ']'}"
    if isinstance(s, sp.FiniteSet):
        return "{" + ", ".join(sstr(a) for a in sorted(s.args, key=_set_key)) + "}"
    if isinstance(s, sp.Union):
        return " ∪ ".join(set_text(a) for a in sorted(s.args, key=_set_key))
    return clip(sstr(s))


def approx_roots(expr: sp.Basic) -> sp.Basic:
    """거듭제곱근으로 안 나오는 근(CRootOf)을 7자리 소수로 바꾼다."""
    roots = expr.atoms(sp.CRootOf)
    return expr.xreplace({r: sp.Float(f"{complex(sp.N(r)).real:.7g}") for r in roots}) if roots else expr


def _polynomial_set(rel: sp.Basic, v: sp.Symbol) -> sp.Set:
    """다항 부등식: 실근으로 나눈 구간마다 가운데 점에서 성립하는지 본다(3차 이상도 정확한 근으로)."""
    g = sp.expand(rel.lhs - rel.rhs)
    cuts = sorted(set(sp.real_roots(sp.Poly(g, v))), key=_set_key)
    edges = [sp.S.NegativeInfinity, *cuts, sp.S.Infinity]
    found: sp.Set = sp.S.EmptySet
    for a, b in zip(edges, edges[1:]):
        mid = sp.Integer(0) if (a, b) == (-sp.oo, sp.oo) else b - 1 if a == -sp.oo else a + 1 if b == sp.oo else (a + b) / 2
        if COMPARE[rel.rel_op](float(sp.N(g.subs(v, mid), 30)), 0):
            found = found | sp.Interval.open(a, b)
    if rel.rel_op in ("<=", ">="):
        found = found | sp.FiniteSet(*cuts)
    return found


def _numeric_set(rel: sp.Basic, v: sp.Symbol) -> sp.Set:
    """기호로 못 풀 때: SCAN_RANGE(양수면 0부터)에서 성립하는 구간을 찾고, 경계는 이분법으로 좁힌다."""
    fn = sp.lambdify(v, rel.lhs - rel.rhs, "numpy")
    compare = COMPARE[rel.rel_op]

    def holds(x: float) -> bool:
        with np.errstate(all="ignore"):
            try:
                y = complex(fn(np.float64(x)))
            except (ZeroDivisionError, ValueError, OverflowError, TypeError):
                return False
        return abs(y.imag) < 1e-12 and bool(np.isfinite(y.real)) and bool(compare(y.real, 0))

    def short(x: float) -> sp.Number:
        value = float(f"{x:.7g}")
        return sp.Integer(round(value)) if abs(value) < 1e-9 or value.is_integer() else sp.Float(value)

    def edge(a: float, b: float) -> sp.Number:
        fa = holds(a)
        for _ in range(50):
            mid = (a + b) / 2
            a, b = (mid, b) if holds(mid) == fa else (a, mid)
        return short((a + b) / 2)

    xs = np.linspace(0.0 if v.is_positive else SCAN_RANGE[0], SCAN_RANGE[1], SCAN_STEPS_INEQ)
    flags = [holds(x) for x in xs]
    found: sp.Set = sp.S.EmptySet
    start = short(xs[0]) if flags[0] else None
    for i in range(1, len(xs)):
        if flags[i] and not flags[i - 1]:
            start = edge(xs[i - 1], xs[i])
        elif flags[i - 1] and not flags[i] and start is not None:
            found = found | sp.Interval.open(start, edge(xs[i - 1], xs[i]))
            start = None
    if start is not None:
        found = found | sp.Interval.Lopen(start, short(xs[-1]))
    return found


def _inequality_set(rel: sp.Basic, v: sp.Symbol, domain: sp.Set, budget: Budget, notes: list[str]) -> sp.Set:
    if (rel.lhs - rel.rhs).is_polynomial(v):
        try:
            return budget.run(_polynomial_set, rel, v, cap=PART_CAP)
        except (TimeoutError, NotImplementedError, ValueError, TypeError, sp.PolynomialError):
            pass
    try:
        sol = budget.run(sp.solveset, rel, v, domain, cap=PART_CAP * 1.5)
    except (TimeoutError, NotImplementedError, ValueError, TypeError):
        sol = None
    if sol is not None and not sol.has(sp.ConditionSet):
        return sol
    lo = 0 if v.is_positive else SCAN_RANGE[0]
    notes.append(f"'{sstr(rel)}' could not be solved symbolically; searched numerically in [{lo:g}, {SCAN_RANGE[1]:g}] (bounds are approximate; outside not checked).")
    notes.append(NUMERIC_MARK)
    return _numeric_set(rel, v)


def solve_inequalities(texts: list[str], p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """부등식(여러 개면 ;로 나눈 것의 교집합)을 실수 구간으로 푼다."""
    if p["latex"]:
        raise WorkerError(["Inequalities do not accept latex."], NO_LATEX_HINT)
    relations: list[sp.Basic] = []
    parsed: list[str] = []
    for i, text in enumerate(texts):
        where = f"expr part {i + 1}" if len(texts) > 1 else "expr"
        exprs, ops, shown = parse_relations(text, where)
        if not ops or "=" in ops:
            raise WorkerError([f"{where}: equations and inequalities are not solved together. Solve the equation in a separate op=solve call."])
        parsed.append(shown)
        relations += [RELATIONS[op](a, b) for a, op, b in zip(exprs, ops, exprs[1:])]
    v = pick_var(relations, p.get("var"), "solve for")
    if others := sorted(s.name for s in set().union(*(r.free_symbols for r in relations)) - {v}):
        raise WorkerError([f"Inequalities are solved in one variable only ({v.name}). Substitute values for the other letters ({', '.join(others)})."])
    domain = sp.Interval.open(0, sp.oo) if v.is_positive else sp.S.Reals
    notes: list[str] = []
    found: sp.Set = domain
    for rel in relations:
        if rel == sp.false:
            found = sp.S.EmptySet
        elif rel != sp.true:
            found = sp.Intersection(found, _inequality_set(rel, v, domain, budget, notes))
    if found.atoms(sp.CRootOf):
        found = approx_roots(found)
        notes.append("Bounds not expressible in radicals are decimal approximations.")
    out: dict[str, Any] = {"parsed": parsed, "variable": v.name, "result": set_text(found), "latex": clip(sp.latex(found))}
    if found == sp.S.EmptySet:
        notes.insert(0, "No solution.")
    elif found == domain:
        notes.insert(0, "Holds for all positive numbers." if v.is_positive else "Holds for all real numbers.")
    if v.is_positive:
        notes.append(f"Solved within {v.name} > 0 (assume=positive).")
    if NUMERIC_MARK in notes:
        notes.remove(NUMERIC_MARK)
    elif any(f.has(v) for r in relations for f in r.atoms(TrigonometricFunction)):
        notes.append("Trig inequalities may show intervals within one period only. For the general solution add the period (2*pi*n).")
    if notes:
        out["note"] = " ".join(notes)
    return out


# ── 상미분방정식 ────────────────────────────────────────────────────

ODE_TYPES = {  # classify_ode 힌트 → 이름. 이 순서로 처음 맞는 것을 type으로 쓴다.
    "separable": "separable",
    "1st_linear": "first-order linear (integrating factor)",
    "Bernoulli": "Bernoulli equation",
    "nth_linear_constant_coeff_homogeneous": "linear, constant coefficients, homogeneous (characteristic equation)",
    "nth_linear_constant_coeff_undetermined_coefficients": "linear, constant coefficients, nonhomogeneous (undetermined coefficients)",
    "nth_linear_constant_coeff_variation_of_parameters": "linear, constant coefficients, nonhomogeneous (variation of parameters)",
    "nth_linear_euler_eq_homogeneous": "Cauchy-Euler equation",
    "nth_linear_euler_eq_nonhomogeneous_undetermined_coefficients": "Cauchy-Euler equation (nonhomogeneous)",
    "nth_linear_euler_eq_nonhomogeneous_variation_of_parameters": "Cauchy-Euler equation (nonhomogeneous)",
    "1st_exact": "exact equation",
    "1st_homogeneous_coeff_best": "homogeneous type (substitute y = u*x)",
    "1st_rational_riccati": "Riccati equation",
    "Riccati_special_minus2": "Riccati equation",
    "nth_order_reducible": "order reduction",
    "2nd_power_series_ordinary": "power series solution",
}
CONSTANT_NAME = re.compile(r"C\d+$")


@dataclass
class Ode:
    eq: sp.Expr  # 좌변 - 우변
    y: sp.Expr  # y(x)
    x: sp.Symbol
    order: int
    text: mathexpr.OdeText
    parsed: str


def parse_ode(p: dict[str, Any]) -> Ode:
    if p["latex"]:
        raise WorkerError(["op=ode does not accept latex."], NO_LATEX_HINT)
    try:
        text = mathexpr.ode_text(p["expr"], p.get("var"))
    except mathexpr.ExprError as exc:
        raise WorkerError([f"expr: {exc}"]) from None
    sides = text.text.replace("==", "=").split("=")
    if len(sides) > 2:
        raise WorkerError(["expr: use '=' only once."])
    x = symbol(text.var)
    y = sp.Function(text.func)(x)
    exprs: list[sp.Expr] = []
    for side, where in zip(sides, ("expr left side", "expr right side") if len(sides) == 2 else ("expr",)):
        try:
            expr = mathexpr.parse(side, None, positive=POSITIVE)
        except mathexpr.ExprError as exc:
            raise WorkerError([f"{where}: {text.restore(str(exc))}"]) from None
        mapping = {s: y.diff(x, text.names[s.name]) if text.names[s.name] else y for s in expr.free_symbols if s.name in text.names}
        exprs.append(exact(expr.xreplace(mapping)))
    if len(exprs) == 1:
        exprs.append(sp.Integer(0))
    eq = exprs[0] - exprs[1]
    if not eq.has(sp.Derivative):
        raise WorkerError(["expr: no derivative remains after simplifying (not a differential equation)."])
    return Ode(eq, y, x, sp.ode_order(eq, y), text, clip(f"{sstr(exprs[0])} = {sstr(exprs[1])}"))


def parse_ics(text: str, ode: Ode) -> list[tuple[int, sp.Basic, sp.Basic]]:
    """초기조건 → [(미분 횟수, 위치, 값)]."""
    try:
        items = mathexpr.split_ics(text, ode.text.func)
    except mathexpr.ExprError as exc:
        raise WorkerError([f"ics: {exc}"]) from None
    out = []
    for k, point, value in items:
        label = f"ics '{ode.text.func}{chr(39) * k}({point})'"
        if k >= ode.order:
            top = ode.text.func + chr(39) * (ode.order - 1)
            raise WorkerError([f"{label}: an order-{ode.order} equation takes initial conditions up to {top}."])
        out.append((k, exact(parse(point, False, label)), exact(parse(value, False, label))))
    if len(out) > ode.order:
        raise WorkerError([f"ics: {len(out)} initial conditions given; an order-{ode.order} equation takes at most {ode.order}."])
    return out


def ode_against(p: dict[str, Any], ode: Ode, ics: list[tuple[int, sp.Basic, sp.Basic]], budget: Budget) -> dict[str, Any]:
    """내 해(against, 우변 식)를 방정식·초기조건에 대입해 본다."""
    claimed = exact(parse(p["against"], False, "against"))
    if {s.name for s in claimed.free_symbols} & set(ode.text.names):
        raise WorkerError([f"against: drop '{ode.text.func} =' and give only the right-hand side (e.g. against=\"C1*exp(-{ode.text.var})\")."])
    derivatives = {ode.y.diff(ode.x, k): claimed.diff(ode.x, k) for k in range(1, ode.order + 1)}
    on_ode, residual = vanishes(ode.eq.xreplace({**derivatives, ode.y: claimed}), budget)
    check: dict[str, Any] = {"satisfies_ode": on_ode}
    if on_ode is False:
        check["residual"] = clip(sstr(residual))
    if ics:
        results = [vanishes(claimed.diff(ode.x, k).subs(ode.x, x0) - value, budget)[0] for k, x0, value in ics]
        check["satisfies_ics"] = combine(*results)
    out: dict[str, Any] = {"against": check, "matches": combine(*(v for k, v in check.items() if k != "residual"))}
    constants = sorted(s.name for s in claimed.free_symbols - ode.eq.free_symbols - {ode.x})
    if not ics and len(constants) < ode.order:
        out["against"]["note"] = (
            f"{len(constants)} arbitrary constant(s); a general solution of order {ode.order} needs {ode.order} (this may be a particular solution)."
        )
    return out


def ode_solution(sol: sp.Basic, ode: Ode, budget: Budget) -> dict[str, Any]:
    if sol.lhs == ode.y:
        item: dict[str, Any] = {"result": clip(sstr(sol.rhs)), "latex": clip(sp.latex(sol))}
    else:
        item = {"result": clip(f"{sstr(sol.lhs)} = {sstr(sol.rhs)}"), "latex": clip(sp.latex(sol)), "implicit": True}
    try:
        ok, residual = budget.run(sp.checkodesol, ode.eq, sol, ode.y, cap=PART_CAP)
        item["checked"] = True if ok else vanishes(residual, budget)[0]
    except Exception:  # 시간 초과 등
        item["checked"] = "unknown"
    return item


def op_ode(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    ode = parse_ode(p)
    items = parse_ics(p["ics"], ode) if p.get("ics") else []
    ics = {(ode.y.subs(ode.x, x0) if k == 0 else ode.y.diff(ode.x, k).subs(ode.x, x0)): v for k, x0, v in items}
    out: dict[str, Any] = {"parsed": ode.parsed, "function": f"{ode.text.func}({ode.text.var})", "order": ode.order}
    if ics:
        out["ics"] = [f"{ode.text.func}{chr(39) * k}({sstr(x0)}) = {sstr(v)}" for k, x0, v in items]
    notes: list[str] = []
    if p.get("against"):
        out.update(ode_against(p, ode, items, budget))
    try:
        hints = budget.run(sp.classify_ode, ode.eq, ode.y, cap=PART_CAP)
        if kind := next((name for hint, name in ODE_TYPES.items() if hint in hints), None):
            out["type"] = kind
    except Exception:  # 분류는 덧붙이는 정보라 생략
        pass

    def run_dsolve(with_ics: bool) -> Any:
        return budget.run(lambda: sp.dsolve(ode.eq, ode.y, ics=ics if with_ics and ics else None), cap=max(budget.left() - 1.5, 0.5))

    try:
        try:
            solution = run_dsolve(True)
        except (ValueError, NotImplementedError) as exc:
            if not ics or "initial" not in str(exc):
                raise
            solution = run_dsolve(False)
            notes.append("Could not find constants satisfying the initial conditions; showing the general solution.")
    except (TimeoutError, NotImplementedError, ValueError) as exc:
        reason = "could not solve it in time" if isinstance(exc, TimeoutError) else "cannot solve this equation"
        if "against" not in out:
            raise WorkerError(
                [f"sympy {reason}."],
                "Check whether it is linear or separable. If you have a solution, pass it as against to check by substitution.",
            ) from None
        out["note"] = f"sympy {reason} (against was checked by substitution)."
        return out
    sols = solution if isinstance(solution, list) else [solution]
    shown = [ode_solution(s, ode, budget) for s in sols[:MAX_SOLUTIONS]]
    if len(shown) == 1:
        out.update(shown[0])
    else:
        out["solutions"] = shown
    params = {s.name for s in ode.eq.free_symbols}
    if constants := sorted({s.name for sol in sols for s in sol.free_symbols if CONSTANT_NAME.match(s.name)} - params):
        notes.append(f"Arbitrary constants: {', '.join(constants)}.")
    if not POSITIVE and any(s.has(sp.Abs) for s in sols) and not ode.eq.has(sp.Abs):
        notes.append('Abs appears because the signs of the letters are unknown. For physical constants solve again with assume="positive".')
    if notes:
        out["note"] = " ".join(notes)
    return out


# ── 행렬 ────────────────────────────────────────────────────────────

MATRIX_LABELS = {"det": "determinant", "rank": "rank", "inverse": "inverse", "eigen": "eigenvalues", "rref": "rref", "product": "product A·B"}
MAX_ENTRY = 120


def parse_matrix(rows: list[list[str]], where: str) -> sp.Matrix:
    return sp.Matrix(
        [[exact(parse(str(v), False, f"{where}[{i + 1}][{j + 1}]")) for j, v in enumerate(row)] for i, row in enumerate(rows)]
    )


def show_matrix(m: sp.Matrix) -> dict[str, Any]:
    rows = [[sstr(v) if len(sstr(v)) <= MAX_ENTRY else sstr(v)[:MAX_ENTRY] + "…" for v in row] for row in m.tolist()]
    return {"result": rows, "latex": clip(sp.latex(m))}


def number_text(expr: sp.Basic) -> str:
    """짧은 값 표기: 21/10, sqrt(210)/10 ≈ 1.449137675."""
    value = approx(expr)
    return clip(sstr(expr)) + (f" ≈ {value}" if value is not None and sstr(expr) != str(value) else "")


def _num(z: complex, digits: int = 10) -> float | str:
    if abs(z.imag) <= 1e-12 * max(1.0, abs(z.real)):
        return float(f"{z.real:.{digits}g}")
    return f"{z.real:.{digits}g}{'+' if z.imag >= 0 else '-'}{abs(z.imag):.{digits}g}i"


def _det(a: sp.Matrix, b: sp.Matrix | None, budget: Budget, notes: list[str]) -> dict[str, Any]:
    return show(budget.run(lambda: sp.factor(a.det()) if a.free_symbols else a.det(), cap=PART_CAP))


def _rank(a: sp.Matrix, b: sp.Matrix | None, budget: Budget, notes: list[str]) -> int:
    if a.free_symbols:
        notes.append("rank and rref of a symbolic matrix assume the letters take no special values (e.g. zero denominators).")
    return int(budget.run(a.rank, cap=PART_CAP))


def _inverse(a: sp.Matrix, b: sp.Matrix | None, budget: Budget, notes: list[str]) -> dict[str, Any] | None:
    det = budget.run(a.det, cap=PART_CAP)
    if det == 0:
        notes.append("det = 0, so there is no inverse.")
        return None
    inverse = budget.run(lambda: a.inv().applyfunc(sp.factor) if a.free_symbols else a.inv(), cap=PART_CAP)
    if a.free_symbols:
        notes.append(f"The inverse exists only when det = {sstr(sp.factor(det))} ≠ 0.")
    return show_matrix(inverse)


def _rref(a: sp.Matrix, b: sp.Matrix | None, budget: Budget, notes: list[str]) -> dict[str, Any]:
    reduced, pivots = budget.run(a.rref, cap=PART_CAP)
    return {**show_matrix(reduced), "pivots": [c + 1 for c in pivots]}


def _product(a: sp.Matrix, b: sp.Matrix | None, budget: Budget, notes: list[str]) -> dict[str, Any]:
    assert b is not None
    if a.cols != b.rows:
        raise WorkerError([f"Product A·B: A has {a.cols} columns but B has {b.rows} rows, so they cannot be multiplied."])
    return {"size": f"{a.rows}x{b.cols}", **show_matrix(budget.run(lambda: a * b, cap=PART_CAP))}


def _eigen(a: sp.Matrix, b: sp.Matrix | None, budget: Budget, notes: list[str]) -> dict[str, Any]:
    lam = sp.Symbol("lambda")
    poly = budget.run(lambda: a.charpoly(lam).as_expr(), cap=PART_CAP)
    try:
        poly = budget.run(sp.factor, poly, cap=EXTRA_CAP)
    except Exception:  # 인수분해는 덤이다
        pass
    out: dict[str, Any] = {"charpoly": clip(sstr(poly))}
    if a.rows % 2:
        notes.append("charpoly is det(λI - A) (odd size, so its sign is opposite to det(A - λI)).")
    try:
        vects = budget.run(a.eigenvects, cap=PART_CAP * 1.5)
    except (TimeoutError, NotImplementedError, MatrixError):
        vects = None
    if vects is not None and not any(v.has(sp.CRootOf) or len(sstr(v)) > 100 for v, _, _ in vects):
        out["values"] = [
            {**show(v), "multiplicity": m, "vectors": [[clip(sstr(c)) for c in vec] for vec in vs]}
            for v, m, vs in sorted(vects, key=lambda t: _set_key(t[0]))
        ]
        out["diagonalizable"] = all(len(vs) == m for _, m, vs in vects)
        if any(v.has(sp.I) for v, _, _ in vects):
            notes.append("I is the imaginary unit (complex eigenvalues).")
        return out
    if a.free_symbols:
        notes.append("Could not find eigenvalues symbolically. Solve charpoly = 0 with op=solve.")
        return out
    w, vecs = np.linalg.eig(np.array(a.evalf().tolist(), dtype=complex))
    out["values"] = [{"approx": _num(w[i]), "vectors": [[_num(c, 6) for c in vecs[:, i]]]} for i in np.lexsort((w.imag, w.real))]
    notes.append("Eigenvalues are not expressible in radicals, so they were computed numerically (eigenvectors have length 1).")
    return out


MATRIX_OPS: dict[str, Callable[..., Any]] = {
    "det": _det, "rank": _rank, "inverse": _inverse, "eigen": _eigen, "rref": _rref, "product": _product,
}  # fmt: skip


def op_matrix(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    if p["latex"]:
        raise WorkerError(["op=matrix does not accept latex."], 'Send entries as numbers or sympy syntax, like matrix=[["1", "2"], ["3", "4"]].')
    a = parse_matrix(p["matrix"], "matrix")
    b = parse_matrix(p["matrix_b"], "matrix_b") if p.get("matrix_b") else None
    try:
        parts = mathexpr.matrix_parts(p.get("expr"), b is not None)
    except mathexpr.ExprError as exc:
        raise WorkerError([f"expr: {exc}"]) from None
    out: dict[str, Any] = {"size": f"{a.rows}x{a.cols}"}
    notes: list[str] = []
    if not a.is_square and (square_only := [MATRIX_LABELS[q] for q in parts if q in ("det", "inverse", "eigen")]):
        notes.append(f"Not a square matrix ({a.rows}x{a.cols}), so no {', '.join(square_only)}.")
        parts = [q for q in parts if q not in ("det", "inverse", "eigen")]
    for part in parts:
        try:
            out[part] = MATRIX_OPS[part](a, b, budget, notes)
        except TimeoutError:
            notes.append(f"{MATRIX_LABELS[part]}: not computed in time.")
    if notes:
        out["note"] = " ".join(dict.fromkeys(notes))
    return out


# ── 라플라스 변환 ───────────────────────────────────────────────────


def _partial_fractions(F: sp.Basic, s: sp.Symbol, out: dict[str, Any], budget: Budget) -> None:
    if not F.is_rational_function(s):
        return
    try:
        pf = budget.run(sp.apart, F, s, cap=EXTRA_CAP)
    except Exception:  # 부분분수는 덤이다
        return
    if pf != F and len(sp.Add.make_args(pf)) > 1:
        out["partial_fractions"] = clip(sstr(pf))


def op_laplace(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr")
    t = pick_var([expr], p.get("var"), "transform in", default="t")
    if "s" in {v.name for v in expr.free_symbols - {t}}:
        raise WorkerError(["expr: s is the variable of the result F(s) and cannot appear in the expression. Use another letter."])
    real_t = sp.Symbol(t.name, real=True)  # 양수 기호면 DiracDelta(t)가 0이 된다
    s = sp.Symbol("s")
    F = budget.run(lambda: sp.laplace_transform(expr.xreplace({t: real_t}), real_t, s, noconds=True))
    if F.has(sp.LaplaceTransform):
        raise WorkerError(
            ["sympy could not find the Laplace transform of this expression."],
            "Transform term by term, or compare with a table formula using op=equal.",
        )
    if F.has(sp.I) and not expr.has(sp.I):  # t*sin(t) 꼴은 허수 단위가 낀 꼴로 나온다: 실수 꼴로 묶는다
        try:
            F = budget.run(sp.simplify, F, cap=PART_CAP)
        except Exception:  # 시간 초과면 그대로
            pass
    out: dict[str, Any] = {"parsed": clip(sstr(expr)), "variable": t.name, **show(F)}
    _partial_fractions(F, s, out, budget)
    return out


def op_inverse_laplace(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr")
    s = pick_var([expr], p.get("var"), "inverse-transform in", default="s")
    if "t" in {v.name for v in expr.free_symbols - {s}}:
        raise WorkerError(["expr: t is the variable of the result f(t) and cannot appear in the expression. Use another letter."])
    t = sp.Symbol("t", real=True)
    f = budget.run(sp.inverse_laplace_transform, expr, s, t)
    if f.has(sp.InverseLaplaceTransform):
        raise WorkerError(
            ["sympy could not find the inverse Laplace transform of this expression."],
            "Inverse-transform each partial-fraction term, or compare with a table formula using op=equal.",
        )
    step = f.has(sp.Heaviside(t))
    f = f.subs(sp.Heaviside(t), 1)
    out: dict[str, Any] = {"parsed": clip(sstr(expr)), "variable": s.name, **show(f)}
    _partial_fractions(expr, s, out, budget)
    if step:
        out["note"] = "Valid for t > 0 (the factor u(t) is omitted)."
    return out


# ── 급수 ────────────────────────────────────────────────────────────


def _ascending(poly: sp.Basic, v: sp.Symbol, center: sp.Basic) -> dict[str, str]:
    """급수를 낮은 차수부터 쓴다. 중심이 a면 (x - a)의 거듭제곱 꼴을 그대로 둔다."""
    if not center.is_finite:
        return {"result": clip(sstr(poly, ascending=True)), "latex": clip(sp.latex(poly, order="rev-lex"))}
    h = sp.Dummy("h")
    parts = sp.collect(sp.expand(poly.subs(v, h + center)), h, evaluate=False)
    terms = [c * (v - center) ** k.as_coeff_exponent(h)[1] for k, c in sorted(parts.items(), key=lambda kc: kc[0].as_coeff_exponent(h)[1])]

    def join(texts: list[str]) -> str:
        return texts[0] + "".join(f" - {t[1:].lstrip()}" if t.startswith("-") else f" + {t}" for t in texts[1:])

    return {"result": clip(join([sstr(t) for t in terms])), "latex": clip(join([sp.latex(t) for t in terms]))}


def op_series(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr")
    v = pick_var([expr], p.get("var"), "expand in")
    center = parse_point(p.get("point") or "0")[0]
    order = int(p.get("order") or 5)
    expansion = budget.run(sp.series, expr, v, center, order + 1)
    poly = expansion.removeO()
    out: dict[str, Any] = {
        "parsed": clip(sstr(expr)),
        "variable": v.name,
        "point": sstr(center),
        "order": order,
        **_ascending(poly, v, center),
    }
    if (big_o := expansion.getO()) is not None:
        out["remainder"] = sstr(big_o)
    if center.is_finite:
        h = sp.Dummy("h")
        try:
            powers = [t.as_coeff_exponent(h)[1] for t in sp.Add.make_args(sp.expand(poly.subs(v, h + center)))]
            if any(e.is_negative for e in powers):
                out["note"] = f"Has negative powers: diverges at {v.name} = {sstr(center)}, so this is a Laurent series, not a Taylor series."
        except Exception:  # 덧붙이는 말이라 생략
            pass
    return out


def op_sum(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    expr = parse(p["expr"], p["latex"], "expr", operand_of="sum")
    n = pick_var([expr], p.get("var"), "sum over", default="n")
    k = sp.Symbol(n.name, integer=True)
    expr = expr.xreplace({n: k})
    lo, hi = (parse_point(str(b))[0] for b in p["bounds"])
    out: dict[str, Any] = {"parsed": clip(sstr(expr)), "variable": n.name, "bounds": [sstr(lo), sstr(hi)]}
    series = sp.Sum(expr, (k, lo, hi))
    result = budget.run(sp.summation, expr, (k, lo, hi))
    if isinstance(result, sp.Piecewise) and not result.args[0][0].has(sp.Sum):
        value, condition = result.args[0]
        out.update(show(value))
        out["note"] = f"Value when {sstr(condition)} (diverges otherwise)."
        return out
    if result in (sp.oo, -sp.oo):
        out.update(result=sstr(result), note=f"Diverges to {'+' if result == sp.oo else '-'}∞.")
        return out
    if not result.has(sp.Sum, sp.Piecewise, sp.nan, sp.zoo):
        out.update(show(result))
        return out
    try:
        convergent = budget.run(series.is_convergent, cap=PART_CAP) if hi.is_infinite or lo.is_infinite else True
        convergent = bool(convergent) if convergent in (True, False) else None  # sympy는 S.true/S.false로 준다
    except Exception:  # 판정 못 함
        convergent = None
    if convergent is False:
        out.update(result="diverges", note="The series does not converge (it diverges or oscillates).")
        return out
    out["result"] = "not computed symbolically"
    if expr.free_symbols - {k} or not hi.is_infinite and not hi.is_number:
        out["note"] = "Could not find the sum symbolically. Substitute values for the other letters to get a numerical sum."
        return out
    if convergent is not True:  # 진동하는 급수에도 수치 합(외삽)은 값을 내므로 믿지 않는다
        out["note"] = "Could not find the sum symbolically or decide convergence. Check with the term limit, comparison or ratio test."
        return out
    fn = sp.lambdify(k, expr, "mpmath")
    ends = [mpmath.inf if e == sp.oo else -mpmath.inf if e == -sp.oo else int(e) for e in (lo, hi)]
    try:
        value = budget.run(mpmath.nsum, fn, ends, cap=PART_CAP)
        out["approx"] = float(f"{float(mpmath.re(value)):.12g}")
        out["note"] = "approx is a numerical sum (convergence confirmed)."
    except Exception:  # 수치 합도 실패
        out["note"] = "Could not compute the sum numerically either."
    return out


# ── 확률분포 ────────────────────────────────────────────────────────

CONTINUOUS = ("Normal", "Exponential", "Uniform")


def _random_symbol(exprs: list[sp.Basic], params: set[sp.Basic], var: str | None) -> sp.Symbol:
    names = {s.name: s for e in exprs for s in e.free_symbols if s not in params}
    if var and var.strip():
        return names.get(var.strip(), symbol(var))
    if "X" in names:
        return names["X"]
    if len(names) == 1:
        return next(iter(names.values()))
    if not names:
        return symbol("X")
    raise WorkerError([f"expr has several letters ({', '.join(sorted(names))}); unclear which is the random variable. Set var (e.g. var=\"X\")."])


def _probability(X: sp.Basic, name: str, relations: list[sp.Basic], budget: Budget, notes: list[str]) -> sp.Basic:
    """연속분포에서 X와 수의 비교뿐이면 누적분포함수로 바로 계산한다(P보다 훨씬 빠르다)."""
    if name in CONTINUOUS:
        lo, hi = sp.S.NegativeInfinity, sp.S.Infinity
        simple = True
        for rel in relations:
            if not isinstance(rel, sp.Rel):
                simple = False
                break
            if rel.lhs == X and rel.rhs.is_number:
                bound, side = rel.rhs, rel.rel_op
            elif rel.rhs == X and rel.lhs.is_number:
                bound, side = rel.lhs, FLIP[rel.rel_op]
            else:
                simple = False
                break
            if side == "==":
                notes.append("Continuous distribution: the probability of a single point is 0.")
                return sp.Integer(0)
            lo, hi = (lo, sp.Min(hi, bound)) if side in ("<", "<=") else (sp.Max(lo, bound), hi)
        if simple:
            if (lo >= hi) is sp.true:
                return sp.Integer(0)
            F = stats.cdf(X)
            return sp.sympify((1 if hi == sp.oo else F(hi)) - (0 if lo == -sp.oo else F(lo)))
    try:
        return budget.run(stats.P, sp.And(*relations), cap=PART_CAP * 1.5)
    except (ValueError, TypeError, NotImplementedError):
        raise WorkerError(
            ["expr: could not compute the probability of this event."],
            "Write abs(X) < a as -a < X < a; write events like X > 1.96, 1 <= X <= 3, X = 2."
        ) from None


def _tidy(expr: sp.Basic, budget: Budget) -> sp.Basic:
    try:
        simpler = budget.run(sp.simplify, expr, cap=EXTRA_CAP)
    except Exception:  # 시간 초과 등: 그대로 쓴다
        return expr
    return simpler if sp.count_ops(simpler) <= sp.count_ops(expr) else expr


def op_probability(p: dict[str, Any], budget: Budget) -> dict[str, Any]:
    if p["latex"]:
        raise WorkerError(["op=probability does not accept latex."], NO_LATEX_HINT)
    try:
        name, arg_texts = mathexpr.split_distribution(p.get("dist"))
    except mathexpr.ExprError as exc:
        raise WorkerError([f"dist: {exc}"]) from None
    labels = mathexpr.DISTRIBUTIONS[name.lower()][1]
    args = [exact(parse(a, False, f"dist {name} argument {i + 1}")) for i, a in enumerate(arg_texts)]
    params = set().union(*(a.free_symbols for a in args))
    text = (p.get("expr") or "").strip()
    exprs, ops, shown = parse_relations(text, "expr") if text else ([], [], "")
    rv = _random_symbol(exprs, params, p.get("var"))
    try:
        X = getattr(stats, name)(rv.name, *args)
    except (ValueError, TypeError) as exc:
        raise WorkerError([f"dist {name}({', '.join(arg_texts)}): invalid arguments ({', '.join(labels)}). {str(exc)[:100]}"]) from None
    out: dict[str, Any] = {"distribution": f"{name}({', '.join(sstr(a) for a in args)})", "variable": rv.name}
    for key, fn in (("mean", stats.E), ("variance", stats.variance), ("std", stats.std)):
        try:
            out[key] = number_text(_tidy(budget.run(fn, X, cap=PART_CAP), budget))
        except TimeoutError:
            pass
    if not text:
        return out
    notes: list[str] = []
    sub = {rv: X}
    if ops:
        relations = [RELATIONS[op](a.xreplace(sub), b.xreplace(sub)) for a, op, b in zip(exprs, ops, exprs[1:])]
        value = _probability(X, name, relations, budget, notes)
        out["event"] = shown
    else:
        value = budget.run(stats.E, exprs[0].xreplace(sub), cap=PART_CAP * 1.5)
        out["expectation"] = f"E[{sstr(exprs[0])}]"
    out.update(show(_tidy(value, budget)))
    if notes:
        out["note"] = " ".join(notes)
    return out


OPS = {
    "equal": op_equal,
    "simplify": op_simplify,
    "solve": op_solve,
    "diff": op_diff,
    "integrate": op_integrate,
    "limit": op_limit,
    "evaluate": op_evaluate,
    "ode": op_ode,
    "matrix": op_matrix,
    "laplace": op_laplace,
    "inverse_laplace": op_inverse_laplace,
    "series": op_series,
    "sum": op_sum,
    "probability": op_probability,
}


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    global POSITIVE
    POSITIVE = payload.get("assume") == "positive"
    op = payload["op"]
    budget = Budget(SYMBOLIC_BUDGET)
    try:
        out = OPS[op](payload, budget)
    except TimeoutError:
        raise WorkerError([f"sympy did not finish within {SYMBOLIC_BUDGET:.0f} s."], TIMEOUT_HINT) from None
    except NotImplementedError as exc:
        raise WorkerError([f"sympy cannot solve this: {str(exc)[:150]}"], TIMEOUT_HINT) from None
    return {"ok": True, "op": op, **out}


if __name__ == "__main__":
    serve(handle)
