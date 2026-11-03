"""verify_math: 수식을 sympy로 계산·검산한다. 파싱·계산은 작업자(openstem.workers.verify) 서브프로세스에서 한다.

여기(서버)서는 글자 단위 검사만 한다(mathexpr.check와 확장 문법 나누기). sympy는 부르지 않는다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from openstem import mathexpr, sandbox
from openstem.tools import ToolResult

OPS = (
    "equal", "simplify", "solve", "diff", "integrate", "limit", "evaluate",
    "ode", "matrix", "laplace", "inverse_laplace", "series", "sum", "probability",
)  # fmt: skip
ASSUMES = ("real", "positive")
LATEX_OPS = ("equal", "simplify", "solve", "diff", "integrate", "limit", "evaluate", "laplace", "inverse_laplace", "series", "sum")
MAX_LATEX = 500
MAX_POINT = 100
MAX_EQUATIONS = 5
MAX_SERIES_ORDER = 20
INFINITY_WORDS = ("oo", "inf", "infinity")

INPUT_HINT = "Fix the input and call again. Syntax (sympy): x**2, sqrt(x), exp(-x), log(x), sin(x)**2, abs(x), pi, e"
TIMEOUT_HINT = "Timed out ({timeout:.0f} s) while computing: the input is too heavy for sympy (huge powers, factorials, long expansions). Check in smaller steps, or compare only the answer with equal."
# 작업자가 준비 신호를 보내기 전(라이브러리 읽기)에 시간이 넘은 경우만 "다시 해 보라"고 한다. 그 뒤의 시간 초과는 입력이 무거운 것이다.
STARTUP_HINT = "On a first run loading the libraries can be slow; try once more."

DESCRIPTION = r"""Check math with sympy: a solution step, or whether an answer equals a reference in another form. You solve; this only computes.
ops: equal (expr vs against → matches true/false/"unknown", plus a numeric check at 8 seeded random points in [-50, 50]), simplify, solve (equation "x**2 = 4", inequality "x**2 > 4", system "x + y = 3; x - y = 1" with var="x, y"; var also isolates a formula; roots are substituted back and extraneous ones dropped with a note), diff (order), integrate (bounds → definite), limit (point "0", "oo", "0+", "0-"), evaluate (point "x=2, y=3"), ode (expr "y'' + 3*y' + 2*y = sin(x)", ics "y(0)=1, y'(0)=0"; against = your solution's right-hand side), matrix (matrix rows → det, rank, inverse, eigen, rref; expr "det, eigen" picks; matrix_b → A·B), laplace (in t), inverse_laplace (in s), series (point, order), sum (bounds [1, "oo"], index n), probability (dist Normal(mean, std) | Binomial(n, p) | Poisson(λ) | Exponential(rate) | Uniform(a, b); expr is an event "X > 1.96" or a quantity "X**2" for its expectation).
Example: {"op": "equal", "expr": "sin(x)**2", "against": "(1 - cos(2*x))/2"}
Syntax: x**2, sqrt, exp, log (= ln), log10, abs, factorial; constants are pi and e only (E is a variable); radians. Physical quantities: assume="positive". Decimals become fractions; numeric comparison is relative, so 9.8 ≠ 9.81. latex=true reads expr and against as LaTeX (not for ode, matrix, probability, inequalities; send the integrand without \int).
Check parsed and read note. matches=false: numeric.counterexample shows where it breaks. "unknown": judge from numeric and say so.
On failure: errors name the input and character; fix and call again. Timeout: split into smaller steps."""


def _check_text(where: str, text: str | None, latex: bool, equation: bool = False) -> list[str]:
    if text is None or not text.strip():
        return [f"{where} is empty."]
    if latex:
        return [f"{where} too long ({len(text)} chars, max {MAX_LATEX})."] if len(text) > MAX_LATEX else []
    if not equation:
        return [f"{where}: {e}" for e in mathexpr.check(text, None)]
    pieces = [p for p in text.split(";") if p.strip()]
    if len(pieces) > MAX_EQUATIONS:
        return [f"{where}: at most {MAX_EQUATIONS} equations in a system."]
    errors = []
    kinds = set()
    for i, piece in enumerate(pieces, start=1):
        label = f"{where} (part {i})" if len(pieces) > 1 else where
        try:
            sides, ops = mathexpr.split_relation(piece)
        except mathexpr.ExprError as exc:
            errors.append(f"{label}: {exc}")
            continue
        kinds.add("inequality" if set(ops) & set(mathexpr.INEQUALITIES) else "equation")
        for side in sides:
            errors += [f"{label} '{side.strip()}': {e}" for e in mathexpr.check(side, None)]
    if len(kinds) > 1:
        errors.append(f"{where}: equations and inequalities are not solved together. Send them in separate op=solve calls.")
    return errors


def _check_point(op: str, point: str | None) -> list[str]:
    if point is None or not point.strip():
        return ['op=limit needs point. E.g. "0", "oo", "0+"'] if op == "limit" else []
    if len(point) > MAX_POINT:
        return [f"point too long (max {MAX_POINT} chars)."]
    if op in ("limit", "series"):
        text = point.strip()
        if op == "limit" and len(text) > 1 and text[-1] in "+-":
            text = text[:-1]
        if text.strip().lstrip("+-").strip().lower() in INFINITY_WORDS:
            return []
        return [f"point: {e}" for e in mathexpr.check(text, None)]
    errors = []
    for part in [p for p in point.split(",") if p.strip()]:
        name, _, value = part.rpartition("=")
        if name and (err := mathexpr.variable_name_error(name.strip())):
            errors.append(f"point '{part.strip()}': {err}")
        errors += [f"point '{part.strip()}': {e}" for e in mathexpr.check(value, None)]
    return errors


def _check_bounds(op: str, bounds: list[str] | None) -> list[str]:
    if bounds is None:
        return ['op=sum needs bounds for the index. E.g. [1, "oo"]'] if op == "sum" else []
    errors = []
    if len(bounds) != 2:
        errors.append('bounds is [lower, upper]. E.g. ["0", "pi"], [1, "oo"]')
    for text in bounds[:2]:
        if text.lstrip("+-").lower() not in INFINITY_WORDS:
            errors += [f"bounds '{text}': {e}" for e in mathexpr.check(text, None)]
    return errors


def _check_ode(expr: str, var: str | None, ics: str | None, against: str | None) -> list[str]:
    if not expr or not expr.strip():
        return ["expr is empty. E.g. \"y'' + 3*y' + 2*y = sin(x)\""]
    try:
        ode = mathexpr.ode_text(expr, var)
    except mathexpr.ExprError as exc:
        return [f"expr: {exc}"]
    sides = ode.text.replace("==", "=").split("=")
    if len(sides) > 2:
        return ["expr: use '=' only once."]
    errors = [f"expr '{side.strip()}': {ode.restore(e)}" for side in sides for e in mathexpr.check(side, None)]
    if ics:
        try:
            for k, point, value in mathexpr.split_ics(ics, ode.func):
                label = f"ics '{ode.func}{chr(39) * k}({point})'"
                errors += [f"{label}: {e}" for text in (point, value) for e in mathexpr.check(text, None)]
        except mathexpr.ExprError as exc:
            errors.append(f"ics: {exc}")
    if against:
        errors += [f"against: {e}" for e in mathexpr.check(against, None)]
    return errors


def _matrix_rows(matrix: list[list[str | float]] | None, where: str) -> tuple[list[list[str]], list[str]]:
    """성분을 문자열로 맞추고(1.0 → "1") 크기·글자를 검사한다."""
    if not matrix or not isinstance(matrix, list) or not all(isinstance(row, list) and row for row in matrix):
        return [], [f'{where} is a list of rows. E.g. [["1", "2"], ["3", "4"]]']
    rows = [[_bound_text(v) for v in row] for row in matrix]
    size = mathexpr.MATRIX_MAX
    if len(rows) > size or any(len(row) > size for row in rows):
        return rows, [f"{where} too large (max {size}x{size})."]
    if len({len(row) for row in rows}) > 1:
        return rows, [f"{where}: every row needs the same number of entries (now {', '.join(str(len(r)) for r in rows)})."]
    errors = [
        f"{where}[{i + 1}][{j + 1}] '{text}': {e}"
        for i, row in enumerate(rows)
        for j, text in enumerate(row)
        for e in mathexpr.check(text, None)
    ]
    return rows, errors


def _check_matrix(
    expr: str | None, matrix: list[list[str | float]] | None, matrix_b: list[list[str | float]] | None
) -> tuple[list[list[str]], list[list[str]] | None, list[str]]:
    a, errors = _matrix_rows(matrix, "matrix")
    b = None
    if matrix_b is not None:
        b, b_errors = _matrix_rows(matrix_b, "matrix_b")
        errors += b_errors
        if not errors and len(a[0]) != len(b):
            errors.append(f"Product A·B: A has {len(a[0])} columns but B has {len(b)} rows, so they cannot be multiplied.")
    try:
        mathexpr.matrix_parts(expr, matrix_b is not None)
    except mathexpr.ExprError as exc:
        errors.append(f"expr: {exc}")
    return a, b, errors


def _check_probability(expr: str | None, dist: str | None) -> list[str]:
    errors = []
    try:
        _, args = mathexpr.split_distribution(dist)
        for i, arg in enumerate(args, start=1):
            errors += [f"dist argument {i} '{arg}': {e}" for e in mathexpr.check(arg, None)]
    except mathexpr.ExprError as exc:
        errors.append(f"dist: {exc}")
    if expr and expr.strip():
        try:
            sides, _ = mathexpr.split_relation(expr)
            errors += [f"expr '{side.strip()}': {e}" for side in sides for e in mathexpr.check(side, None)]
        except mathexpr.ExprError as exc:
            errors.append(f"expr: {exc}")
    return errors


def _bound_text(bound: str | float) -> str:
    """bounds·행렬 성분은 숫자로도 받는다: 1.0은 "1"로(정수는 정확한 값으로 계산되게)."""
    if isinstance(bound, float) and bound.is_integer():
        return str(int(bound))
    return str(bound).strip()


def validate(
    op: str,
    expr: str,
    against: str | None,
    var: str | None,
    point: str | None,
    latex: bool,
    bounds: list[str] | None,
    order: int | None,
    assume: str = "real",
    ics: str | None = None,
    dist: str | None = None,
) -> list[str]:
    """행렬(op=matrix)은 _check_matrix가 따로 본다."""
    if op not in OPS:
        return [f"Unknown op '{op}'. Available ops: {', '.join(OPS)}"]
    if assume not in ASSUMES:
        return [f"Unknown assume '{assume}'. Use \"real\" (default) or \"positive\" (all variables positive, for physical quantities)."]
    inequality = op == "solve" and bool(expr) and any(c in expr for c in "<>≤≥")
    if latex and (op not in LATEX_OPS or inequality):
        what = "inequalities" if inequality else f"op={op}"
        return [f"latex is not supported for {what}. Use latex=false with sympy syntax."]
    if op == "ode":
        errors = _check_ode(expr, var, ics, against)
    elif op == "probability":
        errors = _check_probability(expr, dist)
    elif op == "matrix":
        errors = []
    else:
        errors = _check_text("expr", expr, latex, equation=op == "solve")
    if op == "equal":
        errors += _check_text("against", against, latex)
    names = [n.strip() for n in (var or "").split(",") if n.strip()]
    if len(names) > 1 and op != "solve":
        errors.append("Give only one var (several only for systems in op=solve).")
    errors += [f"var: {err}" for n in names if (err := mathexpr.variable_name_error(n))]
    if op in ("limit", "evaluate", "series"):
        errors += _check_point(op, point)
    if op in ("integrate", "sum"):
        errors += _check_bounds(op, bounds)
    if op == "diff" and order is not None and not 1 <= order <= 10:
        errors.append("order must be 1-10.")
    if op == "series" and order is not None and not 1 <= order <= MAX_SERIES_ORDER:
        errors.append(f"series order (highest power) must be 1-{MAX_SERIES_ORDER}.")
    return errors


def verify_math(
    op: str,
    expr: str = "",
    against: str | None = None,
    var: str | None = None,
    point: str | None = None,
    latex: bool = False,
    bounds: list[str | float] | None = None,
    order: int | None = None,
    assume: str = "real",
    ics: str | None = None,
    matrix: list[list[str | float]] | None = None,
    matrix_b: list[list[str | float]] | None = None,
    dist: str | None = None,
) -> ToolResult:
    expr = expr or ""
    texts = [_bound_text(b) for b in bounds] if bounds is not None else None
    errors = validate(op, expr, against, var, point, latex, texts, order, assume, ics, dist)
    rows_a = rows_b = None
    if op == "matrix" and not errors:
        rows_a, rows_b, errors = _check_matrix(expr, matrix, matrix_b)
    if errors:
        return ToolResult.failure(errors, INPUT_HINT)

    payload: dict[str, Any] = {
        "op": op,
        "expr": expr,
        "against": against,
        "var": var,
        "point": point,
        "latex": latex,
        "bounds": texts or None,
        "order": order,
        "assume": assume,
        "ics": ics,
        "matrix": rows_a,
        "matrix_b": rows_b,
        "dist": dist,
    }
    timeout = sandbox.default_timeout()
    with tempfile.TemporaryDirectory(prefix="openstem-verify-") as tmp:
        try:
            result = sandbox.run_python("openstem.workers.verify", payload, Path(tmp), timeout)
        except sandbox.WorkerFailed as exc:
            message = str(exc) if not exc.timed_out else (
                f"{exc} {STARTUP_HINT}" if str(exc).startswith("Worker startup") else TIMEOUT_HINT.format(timeout=timeout)
            )
            return ToolResult.failure([message])
    if not result.get("ok"):
        return ToolResult.failure(result.get("errors") or ["unknown failure"], result.get("hint"))
    return ToolResult(ok=True, data=result)
