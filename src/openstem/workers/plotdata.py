"""plot_data 작업자: 실험 데이터(표 글 또는 숫자 목록)를 오차 막대와 함께 그리고 numpy 최소제곱으로 곡선을 맞춘다.

- 표 글: 머리글 줄 + CSV·탭·세미콜론·공백·마크다운 표. 칸의 "2.31 ± 0.05"는 값과 오차로 읽는다.
- 맞춤: np.polyfit(cov=True)로 매개변수와 표준오차를 구한다. exponential·power는 ln을 취해 직선으로 맞춘다.
  yerr가 있으면 1/σ² 가중(공분산은 잔차로 다시 맞춘 값). R²는 원래 y 기준.
- 3σ 밖 점: yerr가 있으면 |잔차|/σ, 없으면 그 점을 뺀 나머지 잔차의 표준편차로 잰다.
"""

from __future__ import annotations

import csv
import math
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from openstem.workers import WorkerError, serve

PNG_DPI = 200
FIGSIZE = (6.4, 4.2)
CURVE_POINTS = 400
MAX_ROWS = 5000
MAX_COLUMNS = 40
MAX_LISTED = 4
OUTLIER_SIGMA = 3.0

SVG_NAME = "data.svg"
PNG_NAME = "data.png"
PDF_NAME = "data.pdf"
OUTLIER_LABEL = "3σ 밖"


@dataclass(frozen=True)
class Model:
    params: tuple[str, ...]
    form: str  # 매개변수 이름이 들어간 식
    degree: int  # 변환한 좌표에서 맞추는 다항식 차수
    log_x: bool = False  # x 대신 ln x로 맞춘다
    log_y: bool = False  # y 대신 ln y로 맞춘다


MODELS = {
    "linear": Model(("a", "b"), "y = a·x + b", 1),
    "poly2": Model(("a", "b", "c"), "y = a·x² + b·x + c", 2),
    "poly3": Model(("a", "b", "c", "d"), "y = a·x³ + b·x² + c·x + d", 3),
    "exponential": Model(("A", "B"), "y = A·exp(B·x)", 1, log_y=True),
    "power": Model(("A", "B"), "y = A·x^B", 1, log_x=True, log_y=True),
    "log": Model(("a", "b"), "y = a·ln(x) + b", 1, log_x=True),
}


@dataclass
class Series:
    name: str  # 경고·에러에 쓰는 이름
    label: str | None
    x: np.ndarray
    y: np.ndarray
    yerr: np.ndarray | None = None
    xerr: np.ndarray | None = None
    fit: str = "none"
    fitted: dict[str, Any] | None = None
    curve: tuple[np.ndarray, np.ndarray] | None = None
    legend: str | None = None
    outliers: list[int] = field(default_factory=list)
    column: str | None = None  # 표에서 온 열 이름


def _num(v: float) -> str:
    text = f"{v:.4g}"
    return "0" if text == "-0" else text


def _listing(values: list[str]) -> str:
    shown = ", ".join(values[:MAX_LISTED])
    return shown + (f" and {len(values) - MAX_LISTED} more" if len(values) > MAX_LISTED else "")


# ── 표 읽기 ─────────────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_PM_RE = re.compile(r"^(.+?)\s*(?:±|\+/-|\+-)\s*(.+)$")
_MISSING = {"", "-", "—", "–", "na", "n/a", "nan", "null", "none"}
_MD_RULE_RE = re.compile(r"^:?-+:?$")
_UNIT_SUFFIX_RE = re.compile(r"\s*[(\[].*?[)\]]\s*$")


class CellError(ValueError):
    pass


def parse_cell(cell: str) -> tuple[float, float | None] | None:
    """칸 하나 → (값, 오차 또는 None). 빈 칸이면 None. 숫자가 아니면 CellError."""
    s = cell.strip().replace("−", "-").replace(" ", "").replace(" ", "")
    if s.casefold() in _MISSING:
        return None
    err = None
    if m := _PM_RE.match(s):
        s, err_text = m.group(1).strip(), m.group(2).strip()
        if not _NUMBER_RE.match(err_text):
            raise CellError(cell)
        err = abs(float(err_text))
    if not _NUMBER_RE.match(s):
        raise CellError(cell)
    value = float(s)
    if not math.isfinite(value) or (err is not None and not math.isfinite(err)):
        raise CellError(cell)
    return value, err


def split_table(text: str) -> tuple[list[str], list[list[str]]]:
    """표 글 → (머리글, 데이터 줄들). 구분자는 머리글 줄에서 고른다: 마크다운 | > 탭 > ; > , > 공백."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise WorkerError(["table needs a header row and data rows."], "Put column names on the first line and numbers from the next line.")
    header_line = lines[0]
    if header_line.strip().startswith("|"):
        rows = []
        for ln in lines:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(_MD_RULE_RE.match(c) for c in cells if c) and any(cells):
                continue  # |---|---| 구분 줄
            rows.append(cells)
    else:
        delimiter = next((d for d in ("\t", ";", ",") if d in header_line), None)
        if delimiter is None:
            rows = [ln.split() for ln in lines]
        else:
            rows = [[c.strip() for c in row] for row in csv.reader(lines, delimiter=delimiter)]
    header, data = [h.strip() for h in rows[0]], rows[1:]
    if len(header) > MAX_COLUMNS:
        raise WorkerError([f"table has too many columns ({len(header)}, max {MAX_COLUMNS})."])
    if len(data) > MAX_ROWS:
        raise WorkerError([f"table has too many rows ({len(data)}, max {MAX_ROWS})."])
    if all(_is_number_like(h) for h in header):
        raise WorkerError(
            ["The first line of table is all numbers. The first line must be column names (header)."],
            "Add a header line at the top. E.g. t (s), d (m)",
        )
    for i, row in enumerate(data, start=1):
        if len(row) > len(header):
            raise WorkerError(
                [f"table data row {i} has {len(row)} cells but the header has {len(header)}."],
                "Use one delimiter (comma, tab or space) throughout, and no commas (1,234) or spaces inside numbers.",
            )
        row.extend([""] * (len(header) - len(row)))
    return header, data


def _is_number_like(text: str) -> bool:
    try:
        return parse_cell(text) is not None
    except CellError:
        return False


def find_column(header: list[str], name: str, where: str) -> int:
    """열 이름으로 찾는다: 그대로 → 대소문자 무시 → 끝의 (단위)·[단위]를 뺀 이름."""
    for key in (lambda s: s, lambda s: s.strip().casefold(), lambda s: _UNIT_SUFFIX_RE.sub("", s).strip().casefold()):
        hits = [i for i, h in enumerate(header) if key(h) == key(name)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise WorkerError([f"{where} '{name}': several columns have this name. Make the headers distinct."])
    raise WorkerError([f"{where} '{name}': no such column. Columns: {', '.join(header)}"])


def read_column(header: list[str], data: list[list[str]], col: int) -> tuple[np.ndarray, np.ndarray | None]:
    """열 → (값 배열(빈 칸은 nan), ± 오차 배열 또는 None)."""
    values, errs = [], []
    for i, row in enumerate(data, start=1):
        try:
            parsed = parse_cell(row[col])
        except CellError:
            raise WorkerError(
                [f"table data row {i}, column '{header[col]}': '{row[col]}' is not a number."],
                "Numbers only (1.5, -2e-3, 2.31 ± 0.05). Units go in the header; use a dot (.) as decimal point.",
            ) from None
        values.append(math.nan if parsed is None else parsed[0])
        errs.append(math.nan if parsed is None or parsed[1] is None else parsed[1])
    err_array = np.array(errs, dtype=float)
    return np.array(values, dtype=float), (err_array if np.isfinite(err_array).any() else None)


def _numeric_column(data: list[list[str]], col: int) -> bool:
    try:
        return any(parse_cell(row[col]) is not None for row in data)
    except CellError:
        return False


def series_from_table(payload: dict[str, Any], warn: list[str]) -> tuple[list[Series], dict[str, str]]:
    """표 글과 열 선택 → Series 목록, 기본 축 이름."""
    header, data = split_table(payload["table"])
    cols = payload.get("columns") or {}
    xi = find_column(header, cols["x_column"], "x_column") if cols.get("x_column") else 0
    xerr_i = find_column(header, cols["xerr_column"], "xerr_column") if cols.get("xerr_column") else None
    yerr_idx = [find_column(header, n, "yerr_columns") for n in cols.get("yerr_columns") or []]
    if cols.get("y_columns"):
        y_idx = [find_column(header, n, "y_columns") for n in cols["y_columns"]]
    else:
        used = {xi, *yerr_idx, *([xerr_i] if xerr_i is not None else [])}
        y_idx, skipped = [], []
        for i in range(len(header)):
            if i in used:
                continue
            (y_idx if _numeric_column(data, i) else skipped).append(i)
        if skipped:
            warn.append(f"Skipped non-numeric columns: {', '.join(header[i] for i in skipped)}")
        if not y_idx:
            raise WorkerError(["table has no numeric column to use as y."], "Pick columns by name with y_columns.")
    if len(y_idx) > 8:
        raise WorkerError([f"Too many y columns ({len(y_idx)}, max 8). Pick some with y_columns."])

    x, x_pm = read_column(header, data, xi)
    xerr = read_column(header, data, xerr_i)[0] if xerr_i is not None else x_pm
    out = []
    for k, yi in enumerate(y_idx):
        y, y_pm = read_column(header, data, yi)
        yerr = read_column(header, data, yerr_idx[k])[0] if yerr_idx else y_pm
        keep = np.isfinite(x) & np.isfinite(y)
        if not keep.any():
            raise WorkerError([f"Column '{header[yi]}': no row has both x and y."])
        if dropped := int((~keep).sum()):
            warn.append(f"'{header[yi]}': skipped {dropped} row(s) with empty cells.")
        s = Series(
            name=f"column '{header[yi]}'",
            column=header[yi],
            label=header[yi] if len(y_idx) > 1 else None,  # 열이 하나면 이름은 y축에 쓴다
            x=x[keep],
            y=y[keep],
            yerr=_clean_err(yerr, keep),
            xerr=_clean_err(xerr, keep),
            fit=payload.get("fit") or "none",
        )
        out.append(s)
    labels = {"x_label": header[xi], "y_label": header[y_idx[0]] if len(y_idx) == 1 else "$y$"}
    return out, labels


def _clean_err(err: np.ndarray | None, keep: np.ndarray) -> np.ndarray | None:
    if err is None:
        return None
    err = np.abs(err[keep])
    return np.where(np.isfinite(err), err, 0.0)


def _err_array(value: Any, shape: tuple[int, ...]) -> np.ndarray | None:
    """오차: 점마다 목록이거나 숫자 하나(모든 점에 같게)."""
    if value is None:
        return None
    return np.abs(np.broadcast_to(np.asarray(value, dtype=float), shape).copy())


def series_from_lists(payload: dict[str, Any]) -> list[Series]:
    out = []
    for i, item in enumerate(payload["series"]):
        x, y = np.asarray(item["x"], dtype=float), np.asarray(item["y"], dtype=float)
        out.append(
            Series(
                name=f"series[{i}]" + (f"('{item['label']}')" if item.get("label") else ""),
                label=item.get("label"),
                x=x,
                y=y,
                yerr=_err_array(item.get("yerr"), x.shape),
                xerr=_err_array(item.get("xerr"), x.shape),
                fit=item.get("fit") or payload.get("fit") or "none",
            )
        )
    return out


# ── 맞춤 ────────────────────────────────────────────────────────────


def pm_text(v: float, e: float | None) -> str:
    """값 ± 오차를 오차 유효숫자 2자리에 맞춰 쓴다. 아주 크거나 작으면 (m ± s)×10^k."""
    if e is None or not math.isfinite(e) or e <= 0:
        return f"{v:.6g}"
    scale = max(abs(v), e)
    big = math.floor(math.log10(scale))
    err_exp = math.floor(math.log10(e))
    if big >= 5 or big <= -4:
        f = 10.0**big
        d = max(0, 1 - (err_exp - big))
        return f"({v / f:.{d}f} ± {e / f:.{d}f})×10^{big}"
    d = 1 - err_exp
    if d > 9:  # 오차가 값보다 아주 작다(거의 정확히 맞음)
        return f"{v:.8g} ± {e:.2g}"
    if d < 0:
        step = 10.0 ** (-d)
        return f"{round(v / step) * step:.0f} ± {round(e / step) * step:.0f}"
    return f"{v:.{d}f} ± {e:.{d}f}"


def _tex_num(v: float) -> str:
    text = f"{v:.4g}"
    if "e" in text:
        mant, exp = text.split("e")
        return f"{mant}\\times10^{{{int(exp)}}}"
    return "0" if text == "-0" else text


def _poly_tex(coefs: list[float], negligible: list[bool]) -> str:
    """[a, b, c] → 'a x^2 + b x + c' (빼기 부호 정리). 기여가 무시할 만한 항(2e-15 같은 계산 찌꺼기)은 뺀다."""
    deg = len(coefs) - 1
    out = ""
    for i, c in enumerate(coefs):
        if negligible[i]:
            continue
        power = deg - i
        var = "" if power == 0 else ("x" if power == 1 else f"x^{power}")
        term = f"{_tex_num(abs(c))}{var}"
        if not out:
            out = ("-" if c < 0 else "") + term
        else:
            out += (" - " if c < 0 else " + ") + term
    return out or "0"


def _legend_equation(model: str, values: list[float], x: np.ndarray, y: np.ndarray) -> str:
    if model in ("linear", "poly2", "poly3"):
        deg = len(values) - 1
        x_max, y_max = float(np.max(np.abs(x))) or 1.0, float(np.max(np.abs(y))) or 1.0
        negligible = [abs(c) * x_max ** (deg - i) < 1e-9 * y_max for i, c in enumerate(values)]
        return f"$y = {_poly_tex(values, negligible)}$"
    if model == "exponential":
        return f"$y = {_tex_num(values[0])}\\,e^{{{_tex_num(values[1])}x}}$"
    if model == "power":
        return f"$y = {_tex_num(values[0])}\\,x^{{{_tex_num(values[1])}}}$"
    a, b = values
    return f"$y = {_tex_num(a)}\\ln x {'-' if b < 0 else '+'} {_tex_num(abs(b))}$"


def predict(model: str, values: list[float], x: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        if model in ("linear", "poly2", "poly3"):
            return np.polyval(values, x)
        if model == "exponential":
            return values[0] * np.exp(values[1] * x)
        if model == "power":
            return values[0] * np.power(x, values[1])
        return values[0] * np.log(x) + values[1]


def fit_series(s: Series, warn: list[str]) -> None:
    model = MODELS[s.fit]
    k, n = len(model.params), len(s.x)
    if model.log_y and (bad := s.y <= 0).any():
        raise WorkerError(
            [
                (
                    f"{s.name}: the {s.fit} fit needs all y > 0 (it fits a line to ln y). "
                    f"{int(bad.sum())} point(s) have y ≤ 0 (e.g. x = {_num(s.x[bad][0])}, y = {_num(s.y[bad][0])})."
                )
            ],
            "Subtract the background or flip the sign to make y positive, drop points ≤ 0, or use another fit (e.g. poly2).",
        )
    if model.log_x and (bad := s.x <= 0).any():
        raise WorkerError(
            [
                (
                    f"{s.name}: the {s.fit} fit needs all x > 0 (it uses ln x). "
                    f"{int(bad.sum())} point(s) have x ≤ 0 (e.g. x = {_num(s.x[bad][0])})."
                )
            ],
            "Drop points with x ≤ 0 or use another fit.",
        )
    u = np.log(s.x) if model.log_x else s.x
    v = np.log(s.y) if model.log_y else s.y
    if (distinct := len(np.unique(u))) < model.degree + 1:
        raise WorkerError(
            [f"{s.name}: the {s.fit} fit needs at least {model.degree + 1} distinct x values (now {distinct})."]
        )
    if n < k:
        raise WorkerError([f"{s.name}: the {s.fit} fit needs at least {k} points (now {n})."])

    # 가중치: yerr가 모두 양수일 때만. 변환한 좌표(ln y)에서는 σ_ln y = σ_y / y.
    sigma = None
    if s.yerr is not None:
        if (s.yerr > 0).all():
            sigma = s.yerr / s.y if model.log_y else s.yerr
        else:
            warn.append(f"{s.name}: yerr contains 0, so the fit is unweighted.")
    w = 1.0 / sigma if sigma is not None else None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if n > k:
            coefs, cov = np.polyfit(u, v, model.degree, w=w, cov=True)
            errs = np.sqrt(np.clip(np.diag(cov), 0, None))
        else:
            coefs, errs = np.polyfit(u, v, model.degree, w=w), None
            warn.append(f"{s.name}: {n} points for {k} parameters, so the curve passes through every point; standard errors cannot be computed.")
    if any("Rank" in type(c.message).__name__ for c in caught):
        warn.append(f"{s.name}: the fit is unstable (x values too clustered or degree too high). Try a lower degree.")
    if n > k and n - k < 3:
        warn.append(f"{s.name}: only {n} points for {k} parameters; standard errors are unreliable.")

    coefs = [float(c) for c in coefs]
    errs = [float(e) for e in errs] if errs is not None else [None] * len(coefs)
    if model.log_y:  # ln y = c1 + c0·u → A = e^c1, B = c0
        a_val = math.exp(coefs[1])
        values = [a_val, coefs[0]]
        stderr = [a_val * errs[1] if errs[1] is not None else None, errs[0]]
    else:
        values, stderr = coefs, errs

    fitted_y = predict(s.fit, values, s.x)
    ss_res = float(np.sum((s.y - fitted_y) ** 2))
    ss_tot = float(np.sum((s.y - s.y.mean()) ** 2))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else (1.0 if ss_res == 0 else None)

    info: dict[str, Any] = {
        "model": s.fit,
        "form": model.form,
        "equation": _equation(model, values, stderr),
        "params": {p: {"value": val, "stderr": err} for p, val, err in zip(model.params, values, stderr, strict=True)},
        "r2": round(r2, 6) if r2 is not None else None,
        "n": n,
        "weighted": sigma is not None,
    }
    resid = v - (np.polyval(coefs, u))
    if sigma is not None and n > k:
        info["chi2_red"] = round(float(np.sum((resid / sigma) ** 2) / (n - k)), 4)
    s.fitted = info
    s.outliers = _outliers(resid, sigma, k, v)
    s.legend = f"{s.label} 맞춤: " if s.label else "맞춤: "
    s.legend += _legend_equation(s.fit, values, s.x, s.y) + (f", $R^2$ = {_r2_text(r2)}" if r2 is not None else "")
    s.curve = _curve(s, values)


def _r2_text(r2: float) -> str:
    """0.99996처럼 1에 아주 가까우면 자리를 늘려 1.0000으로 보이지 않게 한다."""
    text = f"{r2:.4f}"
    return f"{r2:.6f}" if text == "1.0000" and r2 < 1 else text


def _equation(model: Model, values: list[float], stderr: list[float | None]) -> str:
    texts = {p: pm_text(val, err) for p, val, err in zip(model.params, values, stderr, strict=True)}
    return re.sub(r"\b([abcdAB])\b", lambda m: f"({texts[m.group(1)]})" if m.group(1) in texts else m.group(1), model.form)


def _outliers(resid: np.ndarray, sigma: np.ndarray | None, k: int, v: np.ndarray) -> list[int]:
    """맞춤에서 3σ 넘게 벗어난 점의 번호. σ: yerr가 있으면 그 값, 없으면 그 점을 뺀 나머지 잔차의 표준편차."""
    n = len(resid)
    if sigma is not None:
        return [int(i) for i in np.flatnonzero(np.abs(resid) > OUTLIER_SIGMA * sigma)]
    dof = n - 1 - k
    if dof < 2:
        return []
    tiny = 1e-9 * max(float(np.ptp(v)), float(np.max(np.abs(v))), 1e-300)
    total = float(np.sum(resid**2))
    out = []
    for i, r in enumerate(resid):
        others = math.sqrt(max(total - r * r, 0.0) / dof)
        if abs(r) > tiny and abs(r) > OUTLIER_SIGMA * others:
            out.append(i)
    return out


def _curve(s: Series, values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = float(s.x.min()), float(s.x.max())
    if lo > 0 and (s.fit in ("power", "log") or hi / lo > 20):
        xs = np.geomspace(lo, hi, CURVE_POINTS)
    else:
        xs = np.linspace(lo, hi, CURVE_POINTS)
    return xs, predict(s.fit, values, xs)


# ── 그리기 ──────────────────────────────────────────────────────────


def draw(payload: dict[str, Any], series: list[Series], labels: dict[str, str], warn: list[str]) -> dict[str, str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from openstem.workers.plot import _check_mathtext, _mathtext_log, _missing_glyphs, _setup_fonts

    matplotlib.rcParams["svg.hashsalt"] = "openstem"
    texts = {
        "title": payload.get("title") or "",
        "x_label": payload.get("x_label") or labels.get("x_label") or "$x$",
        "y_label": payload.get("y_label") or labels.get("y_label") or "$y$",
    }
    texts.update({f"{s.name}.label": s.label for s in series if s.label})
    if errors := _check_mathtext(texts):
        raise WorkerError(errors, "Fix it to matplotlib mathtext (e.g. $t^2$, $\\Delta x$), or write plain text without $.")
    any_outlier = any(s.outliers for s in series)
    _setup_fonts([*texts.values(), *(s.legend or "" for s in series), OUTLIER_LABEL if any_outlier else ""])

    log_x, log_y = payload.get("log_x"), payload.get("log_y")
    if log_x or log_y:
        # 로그 눈금은 \mathdefault 수식으로 그려지는데, 한글 글꼴에서는 유니코드 빼기(−)가 빠질 수 있다
        matplotlib.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=FIGSIZE, layout="constrained")
    handles: list[Any] = []  # 범례 순서: 계열마다 데이터 → 맞춤, 맨 끝에 3σ 밖
    outlier_handle = None
    for s in series:
        container = ax.errorbar(
            s.x,
            s.y,
            yerr=s.yerr,
            xerr=s.xerr,
            fmt="o",
            markersize=5,
            capsize=3,
            elinewidth=1,
            zorder=3,
            label=s.label,
        )
        if s.label:
            handles.append(container)
        color = container[0].get_color()
        if s.curve is not None:
            (line,) = ax.plot(*s.curve, "-", color=color, linewidth=1.6, zorder=2, label=s.legend)
            handles.append(line)
        if s.outliers:
            idx = np.array(s.outliers)
            (ring,) = ax.plot(
                s.x[idx], s.y[idx], "o", markersize=13, mfc="none", mec="red", mew=1.4, zorder=4, label=OUTLIER_LABEL
            )
            outlier_handle = outlier_handle or ring
        _warn_log_axes(s, log_x, log_y, warn)

    if log_x:
        ax.set_xscale("log")
        _plain_log_ticks(ax.xaxis, np.concatenate([s.x for s in series]))
    if log_y:
        ax.set_yscale("log")
        _plain_log_ticks(ax.yaxis, np.concatenate([s.y for s in series]))
    ax.grid(True, which="both" if (log_x or log_y) else "major", alpha=0.3)
    ax.set_xlabel(texts["x_label"])
    ax.set_ylabel(texts["y_label"])
    if texts["title"]:
        ax.set_title(texts["title"])
    if outlier_handle is not None:
        handles.append(outlier_handle)
    if handles:
        ax.legend(handles=handles, fontsize=8.5)

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
    return files


def _plain_log_ticks(axis: Any, values: np.ndarray) -> None:
    """데이터가 0.001~100000 안이면 로그 눈금을 2×10⁻¹ 대신 0.2처럼 쓴다(10의 거듭제곱 꼴은 범위가 넓을 때만)."""
    from matplotlib import ticker

    class Plain(ticker.LogFormatter):
        """어느 눈금에 글을 붙일지는 LogFormatter에 맡기고, 숫자만 0.2·30처럼 쓴다."""

        def _num_to_string(self, x: float, vmin: float, vmax: float) -> str:
            return f"{x:g}"

    positive = values[values > 0]
    if positive.size and positive.min() >= 1e-3 and positive.max() <= 1e5:
        axis.set_major_formatter(Plain())
        axis.set_minor_formatter(Plain(labelOnlyBase=False))


def _warn_log_axes(s: Series, log_x: bool, log_y: bool, warn: list[str]) -> None:
    for on, values, axis in ((log_x, s.x, "x"), (log_y, s.y, "y")):
        if on and (bad := int((values <= 0).sum())):
            warn.append(f"{s.name}: log axis, so {bad} point(s) with {axis} ≤ 0 are not shown.")


# ── 진입점 ──────────────────────────────────────────────────────────


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    warn: list[str] = []
    labels: dict[str, str] = {}
    if payload.get("table"):
        series, labels = series_from_table(payload, warn)
    else:
        series = series_from_lists(payload)

    for s in series:
        if s.fit != "none":
            fit_series(s, warn)
        if s.outliers:
            points = [f"x = {_num(s.x[i])}(y = {_num(s.y[i])})" for i in s.outliers]
            warn.append(
                f"{s.name}: point(s) at {_listing(points)} lie more than 3σ from the fit (red circles in the plot). Check for measurement mistakes."
            )

    files = draw(payload, series, labels, warn)
    summary = []
    for s in series:
        item: dict[str, Any] = {"label": s.label or s.column, "n": len(s.x)}
        if s.yerr is not None:
            item["yerr"] = True
        if s.fitted:
            item["fit"] = s.fitted
        summary.append(item)
    result: dict[str, Any] = {"ok": True, **files, "series": summary}
    if warn:
        result["warnings"] = warn[:10]
    return result


def _preload() -> None:
    """무거운 import(처음 실행이면 matplotlib 글꼴 캐시 만들기까지)를 준비 신호 전에 끝낸다.

    sandbox는 준비 신호 뒤의 입력 처리 시간만 타임아웃으로 센다.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot

    import openstem.workers.plot  # noqa: F401  (수식 라벨 검사·글꼴 도우미)


if __name__ == "__main__":
    _preload()
    serve(handle)
