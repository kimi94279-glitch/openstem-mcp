"""plot_function 작업자: sympy 파싱 → lambdify(numpy) → matplotlib.

특이점은 sympy(singularities, 정확한 값)와 수치 검사(격자 + 이분법)를 함께 써서 찾고,
발산·값이 뛰는 곳에서는 선을 끊는다(좌우 값이 같은 곳으로 모이는 한 점짜리 구멍은 이어 그린다).
실수 값이 없는 구간은 그리지 않고 경고로 알린다.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from openstem import mathexpr
from openstem.render import fonts
from openstem.workers import WorkerError, serve

GRID = 2001
REFINE_STEPS = 80
MAX_CANDIDATES = 200
SYMBOLIC_BUDGET = 4.0  # 모든 식의 sympy 특이점 계산에 쓰는 시간(초). 넘으면 수치 검사만 쓴다.
JUMP_RTOL = 1e-6  # 특이점 좌우 값이 이만큼(상대) 넘게 다르면 값이 뛴다(불연속)고 본다
MAX_LISTED = 5
PNG_DPI = 200
FIGSIZE = (6.4, 4.2)

SVG_NAME = "plot.svg"
PNG_NAME = "plot.png"
PDF_NAME = "plot.pdf"  # 문서 내보내기용. SVG의 clip-path를 MuPDF가 무시해 곡선이 축 밖으로 나가므로 PDF를 따로 둔다.
RANGE_HINT = 'Ranges are numbers or constant expression strings. E.g. [0, "2*pi"], ["-pi/2", "pi/2"]'


def nice(v: float) -> str:
    """사람이 읽기 좋은 수: 정수, π의 분수배(분모 12까지), 아니면 유효숫자 4자리."""
    if abs(v) < 1e-9:
        return "0"
    if abs(v - round(v)) < 1e-9 * max(1.0, abs(v)):
        return str(int(round(v)))
    k = v / np.pi * 12
    if abs(k - round(k)) < 1e-7 * max(1.0, abs(k)):
        frac = Fraction(int(round(k)), 12)
        num, den = frac.numerator, frac.denominator
        coef = {1: "", -1: "-"}.get(num, str(num))
        text = f"{coef}π" + (f"/{den}" if den != 1 else "")
        return f"{text}(≈{v:.4g})"
    return f"{v:.4g}"


def _listing(values: list[str]) -> str:
    shown = ", ".join(values[:MAX_LISTED])
    return shown + (f" and {len(values) - MAX_LISTED} more" if len(values) > MAX_LISTED else "")


def _num(v: float) -> str:
    text = f"{v:.4g}"
    return "0" if text == "-0" else text


def _jump_text(at: str, left: float, right: float) -> str:
    """'0(-1 → 1)': 뛰는 곳과 왼쪽·오른쪽 값."""
    return f"{at}({_num(left)} → {_num(right)})"


@dataclass
class Series:
    name: str  # 경고에 쓰는 이름(입력 식 그대로)
    label: str | None
    expr: Any
    func: Any
    xs: np.ndarray = field(default_factory=lambda: np.empty(0))
    ys: np.ndarray = field(default_factory=lambda: np.empty(0))
    breaks: list[float] = field(default_factory=list)  # 선을 끊을 x
    singular: list[str] = field(default_factory=list)
    jumps: list[str] = field(default_factory=list)
    holes: list[str] = field(default_factory=list)
    oscillations: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    overflow: list[str] = field(default_factory=list)

    def warnings(self) -> list[str]:
        out = []
        if self.singular:
            out.append(f"{self.name}: diverges at x = {_listing(self.singular)} (asymptote); line broken there.")
        if self.jumps:
            out.append(
                f"{self.name}: jumps at x = {_listing(self.jumps)} (discontinuity; parentheses show left → right values); line broken."
            )
        if self.oscillations:
            out.append(
                f"{self.name}: oscillates infinitely often near x = {_listing(self.oscillations)} and has no limit there "
                "(oscillating discontinuity); line broken, and the curve near that point only shows samples."
            )
        if self.holes:
            out.append(f"{self.name}: undefined at the single point x = {_listing(self.holes)} (curve drawn through).")
        if self.gaps:
            out.append(f"{self.name}: no real values for {_listing(self.gaps)}; not drawn.{_odd_root_hint(self.expr)}")
        if self.overflow:
            out.append(f"{self.name}: values too large for {_listing(self.overflow)}; not drawn.")
        return out


def _odd_root_hint(expr: Any) -> str:
    """x**(1/3)는 음수 x에서 복소수 주가지라 그려지지 않는다. 실수 세제곱근 쓰는 법을 알린다."""
    import sympy

    if not isinstance(expr, sympy.Basic):
        return ""
    for pw in expr.atoms(sympy.Pow):
        exp = pw.exp
        if exp.is_Rational and exp.q > 1 and exp.q % 2 == 1 and pw.base.is_nonnegative is not True:
            real = "cbrt(…)" if exp.q == 3 else f"sign(…)*abs(…)**({exp.p}/{exp.q})"
            return f" ({pw} is the principal complex root for a negative base; for the real root write {real}.)"
    return ""


# ── 계산 ────────────────────────────────────────────────────────────


def _evaluate(func: Any, xs: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        y = np.asarray(func(xs))
    if y.shape != xs.shape:
        y = np.broadcast_to(y, xs.shape)
    if np.iscomplexobj(y):
        real = y.real
        y = np.where(np.abs(y.imag) > 1e-9 * np.maximum(1.0, np.abs(real)), np.nan, real)
    return np.asarray(y, dtype=float)


def _at(func: Any, v: float) -> float:
    return float(_evaluate(func, np.array([v]))[0])


def _boundary(func: Any, good: float, bad: float) -> float:
    """good에서는 값이 있고(무한대 포함) bad에서는 실수 값이 없을 때, 그 경계를 이분법으로 찾는다."""
    for _ in range(REFINE_STEPS):
        mid = (good + bad) / 2
        if mid in (good, bad):
            break
        if not np.isnan(_at(func, mid)):
            good = mid
        else:
            bad = mid
    return (good + bad) / 2


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """True가 이어진 구간들의 (시작, 끝) 인덱스(끝 포함)."""
    idx = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return [(int(s), int(e) - 1) for s, e in zip(idx[::2], idx[1::2], strict=True)]


def _range_text(lo: float, hi: float) -> str:
    if abs(hi - lo) < 1e-9 * max(1.0, abs(lo)):
        return f"x = {nice(lo)} (single point)"
    return f"x = {nice(lo)} ~ {nice(hi)}"


def _symbolic_singularities(series: list[Series], a: float, b: float) -> dict[int, list[Any]]:
    """sympy.singularities로 (a, b) 안의 특이점을 정확히 구한다. 시간 안에 못 끝낸 식은 빠진다."""
    found: dict[int, list[Any]] = {}

    def work() -> None:
        import sympy

        x = sympy.Symbol("x", real=True)
        domain = sympy.Interval.open(sympy.Float(a), sympy.Float(b))
        for i, s in enumerate(series):
            try:
                points = sympy.singularities(s.expr, x, domain)
            except Exception:
                continue
            if isinstance(points, sympy.FiniteSet) and len(points) <= 200:
                found[i] = sorted(points, key=float)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(SYMBOLIC_BUDGET)
    return dict(found)


def _side_values(func: Any, s: float, scale: float) -> tuple[float, float] | None:
    """s 바로 왼쪽·오른쪽 값이 서로 다르면(값이 뜀) 그 두 값을, 같은 값으로 모이면 None을 돌려준다.

    두 거리에서 좌우 차이를 잰다: 이어지는 함수는 가까이 갈수록 차이가 줄고, 뛰는 함수는 그대로 남는다.
    (1e-8보다 더 가까이 가면 (x**2-1)/(x-1) 같은 식에서 자릿수 손실이 차이보다 커진다.)
    """
    diffs = []
    for d in (1e-5 * scale, 1e-8 * scale):
        left, right = _at(func, s - d), _at(func, s + d)
        if not (np.isfinite(left) and np.isfinite(right)):
            return None
        diffs.append(abs(right - left))
    if diffs[1] <= JUMP_RTOL * max(1.0, abs(left), abs(right)) or diffs[1] < 0.5 * diffs[0]:
        return None
    return left, right


def _grows_without_bound(func: Any, s: float, sign: int, scale: float) -> bool:
    """s로 다가갈수록 |f|가 줄지 않고 계속 커지는가(log|x|, |x|**-0.2처럼 느리게 발산하는 곳).

    100배 비교만으로는 log(abs(x))의 0(1e-4에서 9.2, 1e-10에서 23)을 놓친다. 거리를 100배씩 줄이며
    |f|가 늘 커지고, 늘어나는 폭이 줄어들지 않으면 발산으로 본다(sin(x)/x처럼 값이 모이는 곳은 폭이 0으로 준다).
    """
    values = [abs(_at(func, s + sign * d * scale)) for d in (1e-3, 1e-5, 1e-7, 1e-9)]
    if not all(np.isfinite(values)):
        return False
    steps = np.diff(values)
    return bool((steps > 0).all() and steps[-1] >= 0.5 * steps[0] and values[-1] > 1.5 * max(values[0], 1.0))


def _oscillates(func: Any, s: float, scale: float) -> bool:
    """s 가까이에서 값이 끝없이 오르내리며 모이지 않는가(sin(1/x)의 0). x*sin(1/x)처럼 폭이 0으로 줄면 아니다."""
    d = np.logspace(-9, -3, 240) * scale
    for sign in (-1, 1):
        ys = _evaluate(func, s + sign * d)
        if not np.isfinite(ys).all():
            return False
        turns = int(np.count_nonzero(np.diff(np.sign(np.diff(ys)))))
        near = ys[: len(ys) // 2]
        if turns < 20 or np.ptp(near) < 0.1 * max(1.0, float(np.max(np.abs(near)))):
            return False
    return True


def _classify_point(func: Any, s: float) -> tuple[str | None, tuple[float, float] | None]:
    """특이점 후보 s의 종류: diverge | oscillate | jump | hole | None(정의역 경계 등, 따로 보고됨).

    jump(abs(x)/x의 0)이면 (왼쪽 값, 오른쪽 값)도 돌려준다.
    hole(sin(x)/x의 0)은 좌우 값이 같은 곳으로 모이는데 그 한 점에서만 값이 없는 곳이다.
    """
    scale = max(1.0, abs(s))
    sides = []
    for sign in (-1, 1):
        far, near = _at(func, s + sign * 1e-4 * scale), _at(func, s + sign * 1e-10 * scale)
        if np.isnan(far) or np.isnan(near):
            sides.append("undefined")
        elif np.isinf(near) or abs(near) > 100 * max(abs(far), 1e-300):
            sides.append("diverge")
        elif not np.isfinite(_at(func, s)) and _grows_without_bound(func, s, sign, scale):
            sides.append("diverge")
        else:
            sides.append("bounded")
    if "diverge" in sides:
        return "diverge", None
    if sides != ["bounded", "bounded"]:
        return None, None
    if _oscillates(func, s, scale):
        return "oscillate", None
    if (values := _side_values(func, s, scale)) is not None:
        return "jump", values
    if not np.isfinite(_at(func, s)):
        return "hole", None
    return None, None


def _record(s: Series, kind: str, label: str, v: float, values: tuple[float, float] | None) -> None:
    """특이점 하나를 경고 목록에 넣는다. 발산·뛰는 곳은 선을 끊고, 구멍은 이어 그린다."""
    if kind == "diverge":
        s.singular.append(label)
        s.breaks.append(v)
    elif kind == "jump" and values is not None:
        s.jumps.append(_jump_text(label, *values))
        s.breaks.append(v)
    elif kind == "oscillate":
        s.oscillations.append(label)
        s.breaks.append(v)
    else:
        s.holes.append(label)


def analyze(s: Series, a: float, b: float, exact: list[Any] | None) -> None:
    xs = np.linspace(a, b, GRID)
    ys = _evaluate(s.func, xs)
    if not np.isfinite(ys).any():
        raise WorkerError(
            [f"{s.name}: no real values anywhere in x = {nice(a)} ~ {nice(b)}."],
            "Move x_range to where the expression is defined, or check the expression (e.g. sqrt(x) needs x ≥ 0).",
        )
    step = (b - a) / (GRID - 1)
    known: list[float] = []  # 이미 보고한 특이점 x (수치 검사가 같은 점을 또 보고하지 않게)

    # 1) sympy가 찾은 정확한 특이점
    for p in exact or []:
        v = float(p)
        kind, values = _classify_point(s.func, v)
        if kind is None:
            continue
        label = nice(v) if p.is_Rational else f"{p}(≈{v:.4g})".replace("*pi", "π").replace("pi", "π")
        _record(s, kind, label, v, values)
        known.append(v)

    def is_known(v: float) -> bool:
        return any(abs(v - k) <= 2 * step for k in known)

    finite = np.isfinite(ys)
    # 2) 격자 위에서 값이 무한대인 점(1/x의 x=0 등)과 넘침 구간(exp(1000) 등)
    for lo, hi in _runs(np.isinf(ys)):
        if hi - lo >= 2:
            s.overflow.append(_range_text(xs[lo], xs[hi]))
        elif not is_known(xs[lo]):
            s.singular.append(nice(xs[(lo + hi) // 2]))
            known.append(xs[(lo + hi) // 2])
    # 3) 실수 값이 없는 구간
    for lo, hi in _runs(np.isnan(ys)):
        if hi == lo and is_known(xs[lo]):
            continue
        if hi == lo and lo not in (0, GRID - 1):
            # 격자점 하나만 값이 없다: 구멍인지, 값이 뛰거나 발산하는 곳인지 좌우를 보고 가른다
            kind, values = _classify_point(s.func, xs[lo])
            _record(s, kind or "hole", nice(xs[lo]), xs[lo], values)
            known.append(xs[lo])
            continue
        # 구간 양옆 격자점은 값이 있다(유한하거나 무한대). 그 사이에서 경계를 좁힌다.
        left = _boundary(s.func, xs[lo - 1], xs[lo]) if lo > 0 else xs[lo]
        right = _boundary(s.func, xs[hi + 1], xs[hi]) if hi + 1 < GRID else xs[hi]
        s.gaps.append(_range_text(left, right))
    # 4) 이웃한 두 점 사이의 큰 값 차이: 이분법으로 좁혀 불연속인지 확인한다
    both = finite[:-1] & finite[1:]
    dy = np.where(both, np.abs(np.diff(np.where(finite, ys, 0.0))), 0.0)
    if both.any():
        q5, q95 = np.percentile(ys[finite], [5, 95])
        span = max(q95 - q5, 1e-12)
        typical = float(np.median(dy[both]))
        threshold = max(10 * typical, 0.05 * span)
        candidates = np.flatnonzero(dy > threshold)
        candidates = candidates[np.argsort(-dy[candidates])][:MAX_CANDIDATES]
        for i in sorted(candidates):
            kind, at, values = _refine_jump(s.func, xs[i], ys[i], xs[i + 1], ys[i + 1], max(abs(q5), abs(q95), 1.0))
            if kind is None or is_known(at):
                continue
            _record(s, kind, nice(at), at, values)
            known.append(at)
    s.xs, s.ys = xs, ys


def _refine_jump(
    func: Any, xl: float, yl: float, xr: float, yr: float, scale: float
) -> tuple[str | None, float, tuple[float, float]]:
    """이웃한 두 격자점 사이를 이분법으로 좁혀 (종류, 위치, (왼쪽 값, 오른쪽 값))을 돌려준다."""
    j0 = abs(yr - yl)
    for _ in range(REFINE_STEPS):
        xm = (xl + xr) / 2
        if xm in (xl, xr):
            break
        ym = _at(func, xm)
        if np.isinf(ym):
            return "diverge", xm, (yl, yr)
        if np.isnan(ym):
            return None, xm, (yl, yr)  # 정의역 문제는 격자 검사에서 따로 다룬다
        if abs(ym - yl) >= abs(yr - ym):
            xr, yr = xm, ym
        else:
            xl, yl = xm, ym
    if abs(yr - yl) < 0.5 * j0:
        return None, xl, (yl, yr)  # 좁힐수록 차이가 줄어든다 → 연속(가파를 뿐)
    kind = "diverge" if max(abs(yl), abs(yr)) > 1e6 * scale else "jump"
    return kind, (xl + xr) / 2, (yl, yr)


def _with_breaks(s: Series) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = s.xs.copy(), np.where(np.isfinite(s.ys), s.ys, np.nan)
    for v in sorted(set(s.breaks)):
        i = int(np.searchsorted(xs, v))
        if i < len(xs) and abs(xs[i] - v) < 1e-12 * max(1.0, abs(v)):
            ys[i] = np.nan
        else:
            xs, ys = np.insert(xs, i, v), np.insert(ys, i, np.nan)
    return xs, ys


# ── 그리기 ──────────────────────────────────────────────────────────


def _check_mathtext(texts: dict[str, str]) -> list[str]:
    from matplotlib.mathtext import MathTextParser

    parser = MathTextParser("path")
    errors = []
    for where, text in texts.items():
        if text.count("$") < 2:
            continue
        try:
            parser.parse(text)
        except Exception as exc:
            first = str(exc).strip().splitlines()[-1][:120] if str(exc).strip() else type(exc).__name__
            errors.append(f"{where}: cannot render math ($...$): {first}")
    return errors


def _auto_ylim(series: list[Series], points: list[dict[str, Any]]) -> tuple[float, float] | None:
    values = [s.ys[np.isfinite(s.ys)] for s in series]
    finite = np.concatenate(values) if values else np.empty(0)
    if finite.size == 0:
        return None
    q5, q95 = np.percentile(finite, [5, 95])
    span = q95 - q5 if q95 > q5 else max(abs(q95), 1.0)
    lo, hi = q5 - 0.5 * span, q95 + 0.5 * span
    full = finite.max() - finite.min()
    if full <= 4 * (hi - lo):
        return None
    for p in points:
        if p["y"]:
            lo, hi = min(lo, min(p["y"])), max(hi, max(p["y"]))
    from matplotlib.ticker import MaxNLocator

    ticks = MaxNLocator(nbins=8).tick_values(lo, hi)
    return float(ticks[0]), float(ticks[-1])


def _setup_fonts(texts: list[str]) -> None:
    import matplotlib
    from matplotlib import font_manager

    if not fonts.has_hangul(*texts):
        return
    path = fonts.hangul_font(Path.cwd())
    font_manager.fontManager.addfont(str(path))
    name = font_manager.FontProperties(fname=str(path)).get_name()
    # 한글 글꼴이 맨 앞이어야 한다: "변위 $y$"처럼 수식이 섞인 글은 글자별 대체 없이 첫 글꼴만 쓴다.
    matplotlib.rcParams["font.family"] = [name, "DejaVu Sans"]


def draw(payload: dict[str, Any], series: list[Series], warn: list[str]) -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["svg.hashsalt"] = "openstem"
    a, b = payload["x_range"]
    points = payload.get("points") or []
    texts = {
        "title": payload.get("title") or "",
        "x_label": payload.get("x_label") or "$x$",
        "y_label": payload.get("y_label") or "$y$",
    }
    texts.update({f"series[{i}].label": s.label for i, s in enumerate(series) if s.label})
    texts.update({f"points[{i}].label": p["label"] for i, p in enumerate(points) if p.get("label")})
    if errors := _check_mathtext(texts):
        raise WorkerError(errors, "Fix it to matplotlib mathtext (e.g. $\\sin x$, $e^{-x/5}$), or write plain text without $.")
    _setup_fonts(list(texts.values()))

    fig, ax = plt.subplots(figsize=FIGSIZE, layout="constrained")
    show_legend = len(series) + len(points) > 1 or any(s.label for s in series) or any(p.get("label") for p in points)
    for s in series:
        xs, ys = _with_breaks(s)
        ax.plot(xs, ys, linewidth=1.8, label=s.label or _auto_label(s.expr))
    for p in points:
        ax.plot(p["x"], p["y"], "o", markersize=5, zorder=3, label=p.get("label") or None)

    ax.set_xlim(a, b)
    if payload.get("y_range"):
        ax.set_ylim(*payload["y_range"])
    elif ylim := _auto_ylim(series, points):
        ax.set_ylim(*ylim)
        warn.append(f"y values grow too large; y-axis clipped to {ylim[0]:g} ~ {ylim[1]:g}. Set y_range for another range.")
    lo, hi = ax.get_ylim()
    if a < 0 < b:
        ax.axvline(0, color="0.25", linewidth=0.8, zorder=1)
    if lo < 0 < hi:
        ax.axhline(0, color="0.25", linewidth=0.8, zorder=1)
    ax.grid(True, alpha=0.3)
    ax.set_xlabel(texts["x_label"])
    ax.set_ylabel(texts["y_label"])
    if texts["title"]:
        ax.set_title(texts["title"])
    if show_legend:
        ax.legend()

    with warnings.catch_warnings(record=True) as caught, _mathtext_log() as logged:
        warnings.simplefilter("always")
        fig.savefig(SVG_NAME)
        fig.savefig(PNG_NAME, dpi=PNG_DPI)
        files = {"svg": SVG_NAME, "png": PNG_NAME}
        try:
            fig.savefig(PDF_NAME)
            files["pdf"] = PDF_NAME
        except Exception:  # PDF는 덤이다. 못 만들면 SVG·PNG만 돌려준다.
            Path(PDF_NAME).unlink(missing_ok=True)
    if missing := _missing_glyphs([str(w.message) for w in caught] + logged):
        warn.append(f"Characters missing from the font were drawn as boxes or ¤: {', '.join(missing[:5])}. Remove or replace them.")
    plt.close(fig)
    return {**files, "x_range": [a, b], "y_range": [round(lo, 6), round(hi, 6)]}


@contextmanager
def _mathtext_log() -> Iterator[list[str]]:
    """$...$가 섞인 글의 없는 글자는 warnings가 아니라 로그('... substituting with a dummy symbol')로만 알려져서 따로 모은다."""
    messages: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger, handler = logging.getLogger("matplotlib.mathtext"), Collect(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)


_GLYPH_RES = (
    (re.compile(r"Glyph (\d+) .*missing from font"), 10),
    (re.compile(r"does not have a glyph for .*\[U\+([0-9a-fA-F]+)\]"), 16),
)


def _missing_glyphs(messages: list[str]) -> list[str]:
    """matplotlib 경고 문장에서 글꼴에 없던 글자를 뽑는다. 예: ['🚀(U+1F680)']"""
    codes: dict[int, None] = {}
    for message in messages:
        for pattern, base in _GLYPH_RES:
            if m := pattern.search(message):
                codes[int(m.group(1), base)] = None
    return [f"{chr(c)}(U+{c:04X})" for c in codes]


def _auto_label(expr: Any) -> str:
    import sympy
    from matplotlib.mathtext import MathTextParser

    text = f"${sympy.latex(expr)}$"
    try:
        MathTextParser("path").parse(text)
        return text
    except Exception:
        return str(expr)


# ── 진입점 ──────────────────────────────────────────────────────────


def _convert_ranges(payload: dict[str, Any]) -> None:
    """x_range·y_range의 상수식 문자열("2*pi" 등)을 숫자로 바꾼다. 서버는 글자 검사(mathexpr.check)만 했다."""
    import sympy

    errors = []
    for key in ("x_range", "y_range"):
        if payload.get(key) is None:
            continue
        numbers = []
        for i, v in enumerate(payload[key]):
            if not isinstance(v, str):
                numbers.append(float(v))
                continue
            try:
                num = float(sympy.N(mathexpr.parse(v, ())))
            except mathexpr.ExprError as exc:
                errors.append(f"{key}[{i}] '{v}': {exc}")
                continue
            except (TypeError, ValueError, ArithmeticError):
                num = math.nan  # 복소수(sqrt(-1)), 무한대(1/0) 등
            if not math.isfinite(num):
                errors.append(f"{key}[{i}] '{v}': not a finite real number.")
                continue
            numbers.append(num)
        if len(numbers) == 2 and not numbers[0] < numbers[1]:
            errors.append(f"{key}: the first value must be less than the second (computed: [{numbers[0]:.6g}, {numbers[1]:.6g}]).")
        payload[key] = numbers
    if errors:
        raise WorkerError(errors, RANGE_HINT)


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    import sympy

    x = sympy.Symbol("x", real=True)
    _convert_ranges(payload)
    a, b = payload["x_range"]
    series: list[Series] = []
    errors = []
    for i, item in enumerate(payload.get("series") or []):
        expr_text = item["expr"]
        try:
            expr = mathexpr.parse(expr_text)
        except mathexpr.ExprError as exc:
            errors.append(f"series[{i}] '{expr_text}': {exc}")
            continue
        if expr.has(sympy.zoo, sympy.nan, sympy.oo, -sympy.oo):
            errors.append(f"series[{i}] '{expr_text}': the expression itself is undefined (division by zero, etc.).")
            continue
        func = sympy.lambdify(x, expr, modules="numpy")
        series.append(Series(name=expr_text, label=item.get("label"), expr=expr, func=func))
    if errors:
        raise WorkerError(errors, "Fix the expression and call again. Syntax: x**2, sqrt(x), exp(-x/5), sin(x)**2, abs(x), log(x)")

    exact = _symbolic_singularities(series, a, b)
    for i, s in enumerate(series):
        try:
            analyze(s, a, b, exact.get(i))
        except WorkerError:
            raise
        except Exception as exc:
            raise WorkerError([f"{s.name}: numerical evaluation failed ({type(exc).__name__}: {str(exc)[:100]})."]) from None

    warn: list[str] = []
    for s in series:
        warn += s.warnings()
    drawn = draw(payload, series, warn)  # svg·png·pdf(만들었으면) 파일 이름과 실제 축 범위
    result: dict[str, Any] = {
        "ok": True,
        "series": [{"expr": item["expr"], "parsed": str(s.expr)} for item, s in zip(payload["series"], series, strict=True)],
        **drawn,
        "versions": _versions(),
    }
    if warn:
        result["warnings"] = warn[:10]
    return result


def _versions() -> dict[str, str]:
    """재현 기록용 라이브러리 버전. 서버는 이 라이브러리들을 import하지 않으므로 작업자가 알려 준다."""
    import matplotlib
    import sympy

    return {"sympy": sympy.__version__, "numpy": np.__version__, "matplotlib": matplotlib.__version__}


def _preload() -> None:
    """무거운 import(처음 실행이면 matplotlib 글꼴 캐시 만들기까지)를 준비 신호 전에 끝낸다.

    sandbox는 준비 신호 뒤의 입력 처리 시간만 타임아웃으로 센다.
    """
    import matplotlib.pyplot  # noqa: F401
    import sympy  # noqa: F401


if __name__ == "__main__":
    _preload()
    serve(handle)
