"""수식 문자열(sympy 문법) 검사와 파싱.

- `check()`: 글자 단위 검사. 허용한 이름·숫자·연산자만 통과시킨다. sympy를 부르지 않아 서버에서 써도 된다.
- `parse()`: sympy 파싱. sympy의 parse_expr는 내부에서 eval을 쓰므로 작업자(서브프로세스) 안에서만 부른다.
  check()를 통과한 토큰만 다시 이어 붙여 넘기므로, 속성 접근(.)·밑줄 이름·문자열은 eval까지 가지 못한다.

variables를 None으로 주면 변수를 식에서 찾는다(verify_math용). 변수 이름은 영문자로 시작하는 영숫자 10자 이하
(밑줄 없음)이고, 파이썬 예약어·sympy 내부 이름은 쓸 수 없다.
상수는 pi(원주율)와 소문자 e(자연상수)뿐이다. 대문자 E는 물리에서 에너지·전기장이라 변수로 읽는다.

verify_math의 확장 문법도 여기서 글자 단위로만 나눈다(sympy 없음, 서버·작업자 같이 씀).
- `ode_text()`: 미분방정식의 프라임(y', y'')을 자리표시 이름(yD1, yD2)으로 바꾼다. 따옴표는 check()까지 가지 못한다.
- `split_relation()`: 부등식·사건(x > 0, 0 < X <= 3)을 변과 관계 기호로 나눈다.
- `split_ics()`, `split_distribution()`: 초기조건 "y(0)=1, y'(0)=0", 분포 "Normal(0, 1)".
"""

from __future__ import annotations

import keyword
import math
import re
from dataclasses import dataclass
from typing import Any

MAX_LENGTH = 300
MAX_VARIABLES = 10
_VARIABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,9}$")
_RESERVED = {"Integer", "Float", "Rational", "Symbol"}  # parse_expr가 만드는 코드가 쓰는 이름
_UNEVALUATED_OPS = ("Add", "Mul", "Pow")  # evaluate=False일 때만 쓰는 이름(변수 이름과 겹치면 그 읽기만 포기)

# 이름 → sympy에서의 이름(또는 만드는 법). 값은 _sympy_names()에서 채운다.
FUNCTIONS = (
    "sin", "cos", "tan", "cot", "sec", "csc",
    "asin", "acos", "atan", "acot", "arcsin", "arccos", "arctan", "atan2",
    "sinh", "cosh", "tanh", "coth", "asinh", "acosh", "atanh",
    "exp", "log", "ln", "log10", "log2", "sqrt", "cbrt",
    "abs", "Abs", "sign", "floor", "ceil", "ceiling", "Heaviside", "min", "max", "Min", "Max", "factorial",
)  # fmt: skip
CONSTANTS = ("pi", "e")  # E는 변수(에너지·전기장)다

_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<num>(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"  # \d는 전각 숫자(３)까지 받아서 쓰지 않는다
    r"|(?P<name>[A-Za-z][A-Za-z0-9_]*)"
    r"|(?P<op>\*\*|[-+*/^(),])"
    r")"
)
_HINTS = {
    "|": "Use abs(x) for absolute value.",
    "=": "Write one expression, not an equation (drop 'y=', keep the right-hand side).",
    "[": "Use only ( ) for brackets.",
    "{": "Use only ( ) for brackets.",
    "\\": "Use sympy syntax, not LaTeX. E.g. sqrt(x), sin(x)**2, exp(-x)",
    "!": "Write factorial(n).",
    "π": "Write pi.",
    "×": "Use * for multiplication.",
    "÷": "Use / for division.",
    "−": "Use ASCII - for minus.",
    "²": "Write powers as x**2 (or x^2).",
    "'": "Primes (y', y'') go only right after the function name, in op=ode.",
    "<": "Inequalities only in op=solve (interval solution) or op=probability (event).",
    ">": "Inequalities only in op=solve (interval solution) or op=probability (event).",
}


@dataclass
class Token:
    kind: str  # num | name | op
    text: str
    pos: int  # 0부터


class ExprError(ValueError):
    pass


def tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    text = expr.rstrip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            ch = text[pos:].lstrip()[:1]
            at = len(text) - len(text[pos:].lstrip()) + 1
            hint = _HINTS.get(ch) or (
                "Use ASCII digits 0-9." if ch.isdigit() else "Allowed: numbers, variables, + - * / ** ^ ( ), function names"
            )
            raise ExprError(f"Character {at} '{ch}' is not allowed. {hint}")
        kind = m.lastgroup or ""
        tokens.append(Token(kind, m.group(kind), m.start(kind)))
        pos = m.end()
    return tokens


def detect_variables(tokens: list[Token]) -> tuple[str, ...]:
    """함수·상수가 아닌 이름을 나온 순서대로(중복 없이) 돌려준다."""
    known = set(FUNCTIONS) | set(CONSTANTS)
    return tuple(dict.fromkeys(t.text for t in tokens if t.kind == "name" and t.text not in known))


_GREEK = (
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "lam", "mu",
    "nu", "xi", "omicron", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
)  # fmt: skip
# 함수 이름 뒤에 붙은 인수: 글자 하나(앞뒤 숫자 허용, 2x·x2) 또는 그리스 문자 이름. "cost"는 cos(t)로 본다.
_GLUED_ARG = re.compile(rf"^(?:[0-9]*[A-Za-z][0-9]*|[0-9]*(?:{'|'.join(_GREEK)})[0-9]*|[0-9]+)$")
_PREFIXES = sorted([*FUNCTIONS, "pi"], key=len, reverse=True)


def glued_function_error(name: str) -> str | None:
    """sinx·lnx·sqrtx·cost처럼 함수 이름에 인수가 붙은 이름이면 고칠 방법을, 아니면 None을 돌려준다.

    이런 이름을 변수 하나로 읽으면 diff("sinx") = 1처럼 학생이 틀린 답을 맞다고 믿게 된다.
    """
    if name in FUNCTIONS or name in CONSTANTS:
        return None
    for prefix in _PREFIXES:
        rest = name[len(prefix) :]
        if name.startswith(prefix) and rest and _GLUED_ARG.match(rest):
            arg = re.sub(r"^([0-9]+)(?=[A-Za-z])", r"\1*", rest)
            meant = f"pi*{arg}" if prefix == "pi" else f"{prefix}({arg})"
            return (
                f"'{name}' looks like {meant} written without {'*' if prefix == 'pi' else 'parentheses'}. Did you mean {meant}? "
                "As a variable name, use one that does not start with a function name."
            )
    return None


def variable_name_error(name: str) -> str | None:
    if name in CONSTANTS:
        return f"'{name}' is a constant (e: Euler's number, pi) and cannot be assigned or used as a variable. For a variable use another name (e.g. E for energy)."
    if name in FUNCTIONS:
        return f"'{name}' is a function name and cannot be a variable. Use another name."
    if keyword.iskeyword(name):
        return f"'{name}' is a Python keyword and cannot be a variable name. Rename it (e.g. lam, val)."
    if name in _RESERVED or not _VARIABLE_RE.match(name):
        return f"Variable name '{name}' is not allowed. Use up to 10 letters/digits starting with a letter, no underscore. E.g. v0, theta, m1"
    return glued_function_error(name)


def _check_auto_variables(tokens: list[Token]) -> list[str]:
    errors = []
    variables = detect_variables(tokens)
    for name in variables:
        if err := variable_name_error(name):
            errors.append(err)
    if len(variables) > MAX_VARIABLES:
        errors.append(f"Too many variables ({len(variables)}, max {MAX_VARIABLES}).")
    # 'f(x)'처럼 늘 괄호 앞에만 나오는 이름은 정의 안 된 함수로 본다. x(x+1)처럼 따로도 나오면 곱하기다.
    followed = {name: [] for name in variables}
    for tok, nxt in zip(tokens, [*tokens[1:], None]):
        if tok.kind == "name" and tok.text in followed:
            followed[tok.text].append(nxt is not None and nxt.text == "(")
    for name, flags in followed.items():
        if all(flags):
            errors.append(f"'{name}(': {name} is not a function. For multiplication write {name}*(...); for a function {name}(x), write out its expression.")
    return errors


MAX_DIGITS = 1_000_000  # 숫자만으로 된 거듭제곱(10**10**10)의 결과 자릿수 한도
MAX_FACTORIAL = 100_000  # factorial(숫자)의 인수 한도


def _literal_chain(tokens: list[Token], i: int) -> tuple[list[float], int]:
    """tokens[i]부터 '숫자 ** 숫자 ** …'(^ 포함)를 읽어 (숫자들, 다음 인덱스)를 돌려준다."""
    values = [float(tokens[i].text)]
    j = i + 1
    while j + 1 < len(tokens) and tokens[j].text in ("**", "^") and tokens[j + 1].kind == "num":
        values.append(float(tokens[j + 1].text))
        j += 2
    return values, j


def _log10_of_chain(values: list[float]) -> float:
    """오른쪽부터 묶는 거듭제곱 a ** b ** c의 상용로그(자릿수). 넘치면 inf."""
    if values[0] <= 0:
        return 0.0
    exponent = values[-1]
    for v in reversed(values[1:-1]):
        try:
            exponent = v**exponent
        except OverflowError:
            return math.inf
    return math.log10(values[0]) * exponent if values[0] != 1 else 0.0


def heavy_errors(tokens: list[Token]) -> list[str]:
    """계산 전에 알 수 있는 거대한 수(10**10**10, factorial(10**8))를 거절한다. 작업자가 시간 초과로 죽기 전에."""
    errors = []
    for i, tok in enumerate(tokens):
        if tok.kind == "num" and (i == 0 or tokens[i - 1].text not in ("**", "^")):
            values, end = _literal_chain(tokens, i)
            text = "".join(t.text for t in tokens[i:end])
            digits = _log10_of_chain(values) if len(values) > 1 else 0.0
            if digits > MAX_DIGITS:
                size = "more digits than can be written down" if math.isinf(digits) else f"about {digits:.3g} digits"
                errors.append(f"'{text}' is far too large to compute ({size}). Compare such numbers through their logarithms or exponents.")
        if tok.text == "factorial" and i + 2 < len(tokens) and tokens[i + 1].text == "(" and tokens[i + 2].kind == "num":
            values, end = _literal_chain(tokens, i + 2)
            if end < len(tokens) and tokens[end].text == ")":
                digits = _log10_of_chain(values) if len(values) > 1 else math.log10(max(values[0], 1.0))
                if digits > math.log10(MAX_FACTORIAL):
                    arg = "".join(t.text for t in tokens[i + 2 : end])
                    errors.append(
                        f"factorial({arg}) is far too large to compute (max factorial({MAX_FACTORIAL})). Use Stirling's approximation or log-gamma by hand."
                    )
    return errors


def check(expr: str, variables: tuple[str, ...] | None = ("x",)) -> list[str]:
    """문제가 있으면 고칠 방법을 담은 짧은 에러 문장들을 돌려준다. 없으면 [].

    variables가 None이면 식에 나온 이름을 변수로 받아들인다.
    """
    if not expr or not expr.strip():
        return ["Expression is empty."]
    if len(expr) > MAX_LENGTH:
        return [f"Expression too long ({len(expr)} chars, max {MAX_LENGTH})."]
    try:
        tokens = tokenize(expr)
    except ExprError as exc:
        return [str(exc)]
    errors = heavy_errors(tokens)
    if variables is None:
        errors += _check_auto_variables(tokens)
        variables = detect_variables(tokens)
    allowed = set(FUNCTIONS) | set(CONSTANTS) | set(variables)
    for tok in tokens:
        if tok.kind == "name" and tok.text not in allowed:
            if len(tok.text) > 1 and all(c in variables for c in tok.text):
                errors.append(f"'{tok.text}': write multiplication with *, e.g. {'*'.join(tok.text)}.")
            elif len(tok.text) == 1:
                errors.append(
                    f"Variable '{tok.text}' is not allowed (allowed variables: {', '.join(variables) or 'none'}). "
                    "Replace constants with numbers."
                )
            elif glued := glued_function_error(tok.text):
                errors.append(glued)
            else:
                errors.append(f"Unknown name '{tok.text}'. Allowed functions: {', '.join(FUNCTIONS)}")
    depth = 0
    for tok in tokens:
        if tok.text == "(":
            depth += 1
        elif tok.text == ")":
            depth -= 1
            if depth < 0:
                errors.append(f"')' at character {tok.pos + 1} has no matching '('.")
                break
    if depth > 0:
        errors.append(f"{depth} '(' not closed.")
    return errors[:5]


def _sympy_names() -> dict[str, Any]:
    import sympy

    names: dict[str, Any] = {
        name: getattr(sympy, name)
        for name in FUNCTIONS
        if hasattr(sympy, name) and name not in ("abs", "min", "max")
    }
    names.update(
        arcsin=sympy.asin,
        arccos=sympy.acos,
        arctan=sympy.atan,
        ln=sympy.log,
        log10=lambda a, **_: sympy.log(a, 10),
        log2=lambda a, **_: sympy.log(a, 2),
        cbrt=lambda a, **_: sympy.real_root(a, 3),  # evaluate=False로 읽을 때 evaluate 인자가 붙어 온다
        abs=sympy.Abs,
        ceil=sympy.ceiling,
        min=sympy.Min,
        max=sympy.Max,
        pi=sympy.pi,
        e=sympy.E,
    )
    return names


def parse(expr: str, variables: tuple[str, ...] | None = ("x",), positive: bool = False, evaluate: bool = True) -> Any:
    """check()를 통과한 식을 sympy 식으로 바꾼다(변수는 실수, positive면 양수). 작업자 안에서만 부른다.

    variables가 None이면 식에 나온 이름을 변수로 쓴다.
    evaluate=False면 x/x, 1/(x-1) - 1/(x-1)처럼 읽는 순간 지워지는 부분을 그대로 남긴다(정의역 조건을 모을 때).
    """
    errors = check(expr, variables)
    if errors:
        raise ExprError(" ".join(errors))
    if variables is None:
        variables = detect_variables(tokenize(expr))

    import sympy
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication,
        parse_expr,
        standard_transformations,
    )

    local = _sympy_names()
    assumption = {"positive": True} if positive else {"real": True}
    local.update({v: sympy.Symbol(v, **assumption) for v in variables})
    global_dict = {
        "__builtins__": {},
        "Integer": sympy.Integer,
        "Float": sympy.Float,
        "Rational": sympy.Rational,
        "Symbol": sympy.Symbol,
    }
    if not evaluate:  # 읽는 코드가 연산을 Add(…, evaluate=False) 꼴로 부른다
        if clash := sorted(set(variables) & set(_UNEVALUATED_OPS)):
            raise ExprError(f"Variable name '{clash[0]}' cannot be read unevaluated.")
        global_dict.update({name: getattr(sympy, name) for name in _UNEVALUATED_OPS})
    text = " ".join(tok.text for tok in tokenize(expr))
    try:
        return parse_expr(
            text,
            local_dict=local,
            global_dict=global_dict,
            transformations=(*standard_transformations, convert_xor, implicit_multiplication),
            evaluate=evaluate,
        )
    except (SyntaxError, TypeError, ValueError) as exc:
        detail = str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__
        raise ExprError(
            f"Cannot parse expression: {detail}. Call functions with parentheses like sin(x); write sin^2(x) as sin(x)**2."
        ) from None


# ── verify_math 확장 문법(글자 단위로만 나눈다. sympy 없음) ─────────

ODE_MAX_ORDER = 6
MAX_FUNC_NAME = 7  # 자리표시 이름(yD2)이 변수 이름 규칙(10자 이하)을 지키게
_PRIMES = str.maketrans({"′": "'", "’": "'", "″": "''"})
# 숫자를 통째로 먹어야 1e5의 e를 이름으로 잘못 보지 않는다(토큰 규칙은 tokenize와 같다).
_ODE_SCAN = re.compile(
    r"(?P<num>(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
    r"|(?P<name>[A-Za-z][A-Za-z0-9]*)(?P<primes>'*)"
    r"(?:(?P<call>\s*\(\s*(?P<arg>[A-Za-z][A-Za-z0-9]*)\s*\))|(?P<open>\s*\())?"
)


def derivative_name(func: str, order: int) -> str:
    """y의 k계 도함수 자리표시 이름(yD2). check·parse에는 보통 변수로 보인다."""
    return f"{func}D{order}"


@dataclass
class OdeText:
    text: str  # 프라임을 자리표시 이름으로 바꾸고 y(x)를 y로 줄인 식
    func: str  # 미지 함수 이름(y)
    var: str  # 독립변수(x)
    order: int  # 가장 높은 미분 횟수
    names: dict[str, int]  # 이름 → 미분 횟수(0은 함수 자신)

    def restore(self, message: str) -> str:
        """에러 문장 속 자리표시 이름을 y'' 꼴로 되돌린다."""
        for name, k in self.names.items():
            if k:
                message = re.sub(rf"\b{name}\b", self.func + "'" * k, message)
        return message


def ode_text(expr: str, var: str | None = None) -> OdeText:
    """미분방정식 "y'' + 3*y' + 2*y = sin(x)"의 프라임을 자리표시 이름으로 바꾼다. 따옴표는 이 단계에서 모두 없어진다.

    var가 없으면 독립변수는 x(함수가 x이거나, 식에 t만 있으면 t)다.
    """
    text = expr.translate(_PRIMES)
    found = [m for m in _ODE_SCAN.finditer(text) if m.group("name")]
    primed: dict[str, int] = {}
    for m in found:
        if m.group("primes"):
            primed[m.group("name")] = max(primed.get(m.group("name"), 0), len(m.group("primes")))
    if not primed:
        raise ExprError("No derivative found. Put primes (') right after the function name, e.g. y'' + y = 0 (dy/dx is not supported).")
    if len(primed) > 1:
        raise ExprError(f"Several names have primes ({', '.join(primed)}). Only one unknown function is allowed.")
    func, order = next(iter(primed.items()))
    if err := variable_name_error(func):
        raise ExprError(err)
    if len(func) > MAX_FUNC_NAME:
        raise ExprError(f"Function name '{func}' too long (max {MAX_FUNC_NAME} chars).")
    if order > ODE_MAX_ORDER:
        raise ExprError(f"Order-{order} derivatives are not supported (max order {ODE_MAX_ORDER}).")
    seen = {m.group("name") for m in found} | {m.group("arg") for m in found if m.group("arg")}
    if not var or not var.strip():
        var = "t" if func == "x" or ("t" in seen and "x" not in seen) else "x"
    var = var.strip()
    if err := variable_name_error(var):
        raise ExprError(f"var: {err}")
    if var == func:
        raise ExprError(f"The independent variable (var) and the function are both '{var}'. Give var another name (e.g. var=\"t\").")
    names = {func: 0} | {derivative_name(func, k): k for k in range(1, order + 1)}
    if clash := sorted(n for n in seen if n in names and n != func):
        raise ExprError(f"Variable name '{clash[0]}' clashes with the derivative notation. Use another name.")

    def replace(m: re.Match[str]) -> str:
        name, primes = m.group("name"), m.group("primes") or ""
        if name != func:
            return m.group(0)
        if m.group("open") or (m.group("call") and m.group("arg") != var):
            raise ExprError(
                f"'{func}{primes}(…)': in the equation write only {func}{primes}({var}) or {func}{primes} (independent variable var=\"{var}\"). "
                f"Give values like {func}(0)=1 in ics."
            )
        return derivative_name(func, len(primes)) if primes else func

    return OdeText(_ODE_SCAN.sub(lambda m: m.group(0) if m.group("num") else replace(m), text), func, var, order, names)


_IC_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)('*)\s*\((.+)\)\s*=(.+)$")


def split_ics(text: str, func: str) -> list[tuple[int, str, str]]:
    """초기조건 "y(0)=1, y'(0)=0" → [(0, "0", "1"), (1, "0", "0")]. ;로 나눠도 된다."""
    out = []
    for part in [p for p in re.split(r"[,;]", text.translate(_PRIMES)) if p.strip()]:
        m = _IC_RE.match(part)
        if not m:
            raise ExprError(f"Cannot read initial condition '{part.strip()}'. Write e.g. {func}(0)=1, {func}'(0)=0.")
        name, primes, point, value = m.groups()
        if name != func:
            raise ExprError(f"Initial condition '{part.strip()}': the equation's function is {func}.")
        out.append((len(primes), point.strip(), value.strip()))
    if not out:
        raise ExprError("ics is empty.")
    return out


INEQUALITIES = ("<", "<=", ">", ">=")
_RELATION_RE = re.compile(r"(<=|>=|=<|=>|==|!=|≤|≥|≠|<|>|=)")
_RELATIONS = {"<": "<", ">": ">", "<=": "<=", "=<": "<=", "≤": "<=", ">=": ">=", "=>": ">=", "≥": ">=", "=": "=", "==": "="}


def split_relation(text: str) -> tuple[list[str], list[str]]:
    """'0 < x <= 3' → (['0 ', ' x ', ' 3'], ['<', '<=']). 관계 기호가 없으면 ([text], [])."""
    parts = _RELATION_RE.split(text)
    sides, ops = parts[0::2], parts[1::2]
    if any(op in ("!=", "≠") for op in ops):
        raise ExprError("'≠' (!=) is not allowed. Write inequalities with <, <=, >, >=.")
    ops = [_RELATIONS[op] for op in ops]
    if "=" in ops and len(ops) > 1:
        raise ExprError("Use '=' at most once per expression and do not mix it with inequality signs.")
    if len(ops) > 2:
        raise ExprError("At most two inequality signs per expression (e.g. 0 < x <= 3). Split more with ;.")
    return sides, ops


DISTRIBUTIONS = {  # 소문자 이름 → (sympy.stats 이름, 인자 뜻)
    "normal": ("Normal", ("mean", "standard deviation")),
    "binomial": ("Binomial", ("number of trials n", "success probability p")),
    "poisson": ("Poisson", ("mean λ",)),
    "exponential": ("Exponential", ("rate λ (mean 1/λ)",)),
    "uniform": ("Uniform", ("lower bound a", "upper bound b")),
}
DISTRIBUTION_USAGE = "Normal(mean, std), Binomial(n, p), Poisson(λ), Exponential(λ), Uniform(a, b)"
_CALL_RE = re.compile(r"^\s*([A-Za-z]+)\s*\((.*)\)\s*$")


def split_distribution(text: str | None) -> tuple[str, list[str]]:
    """분포 "Normal(0, 1)" → ("Normal", ["0", "1"]). 인자는 따로 check()한다."""
    m = _CALL_RE.match(text or "")
    if not m:
        raise ExprError(f"Cannot read the distribution. Allowed forms: {DISTRIBUTION_USAGE}")
    name, inner = m.groups()
    if name.lower() not in DISTRIBUTIONS:
        normal = " For N(μ, σ²) notation write Normal(μ, σ) (the second value is the standard deviation)." if name.lower() == "n" else ""
        raise ExprError(f"Unknown distribution '{name}'. Allowed forms: {DISTRIBUTION_USAGE}.{normal}")
    canonical, labels = DISTRIBUTIONS[name.lower()]
    args = [a.strip() for a in inner.split(",")] if inner.strip() else []
    if len(args) != len(labels):
        raise ExprError(f"{canonical} takes {len(labels)} argument(s) ({', '.join(labels)}).")
    if any("=" in a for a in args):
        raise ExprError(f"Give arguments by position, without names: {canonical}({', '.join(labels)}).")
    return canonical, args


MATRIX_MAX = 8  # 행·열 최대 크기
MATRIX_PARTS = ("det", "rank", "inverse", "eigen", "rref", "product")
_MATRIX_DEFAULT = ["det", "rank", "inverse", "eigen", "rref"]
_MATRIX_ALIASES = {
    "determinant": "det", "inv": "inverse", "eig": "eigen", "eigenvalues": "eigen", "eigenvectors": "eigen",
    "charpoly": "eigen", "mul": "product", "matmul": "product", "multiply": "product",
}  # fmt: skip


def matrix_parts(text: str | None, has_b: bool) -> list[str]:
    """op=matrix의 expr("det, eigen")를 계산할 항목 목록으로. 비우거나 all이면 전부(matrix_b만 있으면 곱만)."""
    words = [w for w in re.split(r"[\s,;]+", (text or "").strip().lower()) if w]
    if not words or words == ["all"]:
        return ["product"] if has_b and not words else [*_MATRIX_DEFAULT, *(["product"] if has_b else [])]
    parts: list[str] = []
    for word in words:
        part = _MATRIX_ALIASES.get(word, word)
        if part not in MATRIX_PARTS:
            raise ExprError(f"op=matrix has no expr '{word}'. Choose from: {', '.join(MATRIX_PARTS)} (empty = all)")
        if part == "product" and not has_b:
            raise ExprError("product (A·B) needs matrix_b.")
        if part not in parts:
            parts.append(part)
    return parts
