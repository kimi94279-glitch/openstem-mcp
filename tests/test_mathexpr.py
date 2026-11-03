"""수식 문자열 검사(check)와 파싱(parse). 파싱은 테스트 프로세스 안에서 직접 부른다(서버에서는 작업자 안에서만)."""

import math
import re

import pytest
import sympy

from openstem.mathexpr import (
    ExprError,
    check,
    matrix_parts,
    ode_text,
    parse,
    split_distribution,
    split_ics,
    split_relation,
    variable_name_error,
)
from openstem.workers.plot import nice

X = sympy.Symbol("x", real=True)


@pytest.mark.parametrize(
    "expr", ["sin(x)*exp(-x/5)", "x^2 + 2x + 1", "sqrt(abs(x))", "log(x, 2)", "1.5e-3*x", ".5x", "cbrt(x) - pi*e"]
)
def test_check_accepts(expr):
    assert check(expr) == []


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ("|x|", "abs(x)"),
        ("__import__('os')", "'_'"),
        ("x.real", "'.'"),
        ("lambda x", "Unknown name 'lambda'"),
        ("open", "Unknown name 'open'"),
        ("y + 1", "Variable 'y'"),
        ("xx", "x*x"),
        ("sinx", "Did you mean sin(x)?"),
        ("sqrt(x", "not closed"),
        ("x)", "matching '('"),
        ("y = x", "'='"),
        ("\\frac{1}{x}", "LaTeX"),
        ("π*x", "pi"),
        ("３*x", "0-9"),
        ("E*x", "Variable 'E'"),
        ("", "empty"),
        ("x+" * 200, "too long"),
    ],
)
def test_check_rejects_with_fix(expr, fragment):
    errors = check(expr)
    assert errors and fragment in " ".join(errors)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("2x^2", 2 * X**2),
        ("e^x", sympy.exp(X)),
        ("ln(x)", sympy.log(X)),
        ("x(x+1)", X * (X + 1)),
        ("arcsin(x)", sympy.asin(X)),
        ("max(x, 0)", sympy.Max(X, 0)),
    ],
)
def test_parse(expr, expected):
    assert sympy.simplify(parse(expr) - expected) == 0


def test_cbrt_is_real_for_negative_numbers():
    assert float(parse("cbrt(x)").subs(X, -8)) == pytest.approx(-2)


def test_parse_explains_function_power():
    with pytest.raises(ExprError, match=r"sin\(x\)\*\*2"):
        parse("sin^2(x)")


def test_parse_refuses_unchecked_input():
    with pytest.raises(ExprError):
        parse("__import__('os')")


@pytest.mark.parametrize(
    ("value", "text"),
    [(0.0, "0"), (2.0, "2"), (-3.0, "-3"), (math.pi / 2, "π/2(≈1.571)"), (-3 * math.pi / 2, "-3π/2(≈-4.712)"),
     (math.pi, "π(≈3.142)"), (0.123456, "0.1235")],
)  # fmt: skip
def test_nice_numbers(value, text):
    assert nice(value) == text


# ── 변수 자동 감지(variables=None, verify_math용) ─────────────────


def test_auto_variables_accept_physics_names():
    assert check("m*g*h + v0**2/2 - theta", None) == []
    assert parse("m*g*h", None).free_symbols == {sympy.Symbol(n, real=True) for n in "mgh"}


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ("lambda*x", "keyword"),
        ("v_0*t", "no underscore"),
        ("f(x) + 1", "is not a function"),
        ("Symbol*2", "not allowed"),
        ("averyverylongname*x", "up to 10"),
    ],
)
def test_auto_variables_reject_bad_names(expr, fragment):
    assert fragment in " ".join(check(expr, None))


def test_auto_variables_allow_multiplication_before_parenthesis():
    assert check("x(x+1)", None) == []
    assert sympy.expand(parse("x(x+1)", None)) == sympy.expand(parse("x**2 + x", None))


def test_capital_e_is_a_variable_and_small_e_is_euler():
    assert parse("E", None) == sympy.Symbol("E", real=True)
    assert parse("e", None) == sympy.E


def test_positive_variables():
    m, v = (sympy.Symbol(n, positive=True) for n in "mv")
    assert parse("sqrt(m**2*v**2)", None, positive=True) == m * v


@pytest.mark.parametrize(("name", "fragment"), [("e", "constant"), ("pi", "constant"), ("sin", "function name"), ("E", None)])
def test_variable_name_error_for_constants_and_functions(name, fragment):
    err = variable_name_error(name)
    assert (err is None) if fragment is None else (fragment in err)


# ── verify_math 확장 문법(글자 단위 나누기) ───────────────────────


@pytest.mark.parametrize(
    ("expr", "var", "text", "func", "variable", "order"),
    [
        ("y'' + 3*y' + 2*y = sin(x)", None, "yD2 + 3*yD1 + 2*y = sin(x)", "y", "x", 2),
        ("y''(t) + y(t) = 0", None, "yD2 + y = 0", "y", "t", 2),
        ("x'' + 4*x = 0", None, "xD2 + 4*x = 0", "x", "t", 2),
        ("2y' = y*1e5", None, "2yD1 = y*1e5", "y", "x", 1),
        ("i' + R*i/L = V/L", "t", "iD1 + R*i/L = V/L", "i", "t", 1),
        ("y′′ + y = 0", None, "yD2 + y = 0", "y", "x", 2),
    ],
)
def test_ode_text_replaces_primes(expr, var, text, func, variable, order):
    ode = ode_text(expr, var)
    assert (ode.text, ode.func, ode.var, ode.order) == (text, func, variable, order)
    assert "'" not in ode.text and check(ode.text.split("=")[0], None) == []


@pytest.mark.parametrize(
    ("expr", "var", "fragment"),
    [
        ("y = 2*x", None, "No derivative"),
        ("y' = z'", None, "Several names"),
        ("y'' + y(0) = 0", None, "in ics"),
        ("y'(s) = y", "t", "var=\"t\""),
        ("x' = x", "x", "both"),
        ("sin' = 1", None, "function name"),
        ("y''''''' = 0", None, "max order 6"),
    ],
)
def test_ode_text_rejects_with_fix(expr, var, fragment):
    with pytest.raises(ExprError, match=re.escape(fragment)):
        ode_text(expr, var)


def test_ode_text_restores_prime_in_errors():
    ode = ode_text("y'' + y = 0")
    assert ode.restore("Variable 'yD2' error") == "Variable 'y''' error"


def test_prime_outside_ode_points_to_ode():
    assert "op=ode" in " ".join(check("y' + y", None))


def test_split_ics():
    assert split_ics("y(0)=1, y'(0) = -2; y''(pi/2)=0", "y") == [(0, "0", "1"), (1, "0", "-2"), (2, "pi/2", "0")]
    with pytest.raises(ExprError, match="function is y"):
        split_ics("z(0)=1", "y")
    with pytest.raises(ExprError, match="Cannot read"):
        split_ics("y=1", "y")


@pytest.mark.parametrize(
    ("text", "sides", "ops"),
    [
        ("x**2 - 4 > 0", ["x**2 - 4 ", " 0"], [">"]),
        ("0 < x =< 3", ["0 ", " x ", " 3"], ["<", "<="]),
        ("x ≥ 1", ["x ", " 1"], [">="]),
        ("x == 2", ["x ", " 2"], ["="]),
        ("x", ["x"], []),
    ],
)
def test_split_relation(text, sides, ops):
    assert split_relation(text) == (sides, ops)


@pytest.mark.parametrize(("text", "fragment"), [("x != 1", "≠"), ("x = 1 < 2", "do not mix"), ("0 < x < 1 < 2", "At most two")])
def test_split_relation_rejects(text, fragment):
    with pytest.raises(ExprError, match=fragment):
        split_relation(text)


def test_split_distribution():
    assert split_distribution("normal(0, 1)") == ("Normal", ["0", "1"])
    assert split_distribution("Binomial(10, 0.3)") == ("Binomial", ["10", "0.3"])
    for text, fragment in [("N(0, 4)", "Normal(μ, σ)"), ("Normal(0)", "2 argument"), ("Gamma(1, 2)", "Unknown distribution"), ("Poisson(lam=2)", "by position")]:
        with pytest.raises(ExprError, match=re.escape(fragment)):
            split_distribution(text)


def test_matrix_parts():
    assert matrix_parts(None, False) == ["det", "rank", "inverse", "eigen", "rref"]
    assert matrix_parts("", True) == ["product"]
    assert matrix_parts("eig, inv, det", False) == ["eigen", "inverse", "det"]
    with pytest.raises(ExprError, match="matrix_b"):
        matrix_parts("product", False)


def test_factorial_is_allowed():
    assert check("x**n/factorial(n)", None) == []


@pytest.mark.parametrize(
    ("name", "meant"),
    [
        ("sinx", "sin(x)"),
        ("cosx", "cos(x)"),
        ("lnx", "ln(x)"),
        ("sqrtx", "sqrt(x)"),
        ("tanx", "tan(x)"),
        ("expx", "exp(x)"),
        ("sin2x", "sin(2*x)"),
        ("costheta", "cos(theta)"),
        ("log10x", "log10(x)"),
        ("coshx", "cosh(x)"),
        ("pix", "pi*x"),
    ],
)
def test_function_name_glued_to_argument_is_rejected(name, meant):
    """diff("sinx")가 sinx를 변수 하나로 읽어 1을 내던 오답(감사 2026-11-03)."""
    for errors in (check(name, None), check(f"{name} + 1", ("x",))):
        assert any(f"Did you mean {meant}?" in e for e in errors), errors
    assert f"Did you mean {meant}?" in (variable_name_error(name) or "")


@pytest.mark.parametrize("name", ["signal", "expect", "Emax", "theta", "pitch", "v0", "omega2", "lambda1"])
def test_names_merely_starting_like_a_function_are_variables(name):
    assert check(f"{name} + 1", None) == []
