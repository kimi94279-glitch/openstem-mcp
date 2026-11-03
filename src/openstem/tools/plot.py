"""plot_function: 수식 그래프를 그린다. 파싱·계산·그리기는 작업자(openstem.workers.plot) 서브프로세스에서 한다."""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from openstem import mathexpr, output, sandbox
from openstem.tools import ToolResult

MAX_SERIES = 8
MAX_POINT_SETS = 8
MAX_POINTS = 5000
MAX_TEXT = 200

INPUT_HINT = "Fix the input and call again. Syntax: x**2, sqrt(x), exp(-x/5), sin(x)**2, abs(x), log(x)"
TIMEOUT_HINT = "Timed out ({timeout:.0f} s). Simplify the expression or narrow x_range and call again. A first run can be slow; try once more."

DESCRIPTION = r"""Plot y = f(x) (SVG, PNG, PDF) with a preview; asymptotes, jumps and domain gaps come back as warnings. Measured data: use plot_data.
Example: {"series": [{"expr": "sin(x)*exp(-x/5)"}], "x_range": [0, "4*pi"]}
expr: sympy syntax in x (x**2, sqrt(x), exp(x), log(x)=ln, abs(x), pi, e). Check series[].parsed. Labels may use $...$.
On failure: errors name the series and character; fix it and call again."""


def _check_range_text(where: str, text: str) -> list[str]:
    """범위에 쓴 상수식 문자열("2*pi")의 글자 검사. 숫자로 바꾸는 일(sympy)은 작업자가 한다."""
    errors = mathexpr.check(text, ())
    if not errors:
        return []
    try:
        names = mathexpr.detect_variables(mathexpr.tokenize(text))
    except mathexpr.ExprError:
        names = ()
    if names:
        return [f"{where} '{text}': ranges cannot contain variables or unknown names ({', '.join(names)}). Use only numbers and the constants pi, e."]
    return [f"{where} '{text}': {e}" for e in errors]


def _check_range(name: str, value: list[float | str] | None, required: bool) -> list[str]:
    if value is None:
        return [f"{name} is required. E.g. [-5, 5]"] if required else []
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return [f"{name} must be [min, max]. E.g. [-5, 5], [0, \"2*pi\"]"]
    errors = []
    for i, v in enumerate(value):
        if isinstance(v, str):
            errors += _check_range_text(f"{name}[{i}]", v)
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{name}[{i}] must be a finite number or a constant expression string like \"2*pi\" (got {v!r}).")
    if not errors and not any(isinstance(v, str) for v in value) and not value[0] < value[1]:
        # 문자열이 섞였으면 값 비교는 작업자가 숫자로 바꾼 뒤에 한다
        errors.append(f"{name}: the first value must be less than the second (got {list(value)}).")
    return errors


def _check_points(points: list[dict[str, Any]]) -> list[str]:
    errors = []
    if len(points) > MAX_POINT_SETS:
        errors.append(f"At most {MAX_POINT_SETS} point sets.")
    for i, p in enumerate(points[:MAX_POINT_SETS]):
        xs, ys = p.get("x") or [], p.get("y") or []
        if len(xs) != len(ys):
            errors.append(f"points[{i}]: x and y lengths differ (x {len(xs)}, y {len(ys)}).")
        elif not xs:
            errors.append(f"points[{i}]: no points.")
        elif len(xs) > MAX_POINTS:
            errors.append(f"points[{i}]: at most {MAX_POINTS} points.")
        elif not all(isinstance(v, (int, float)) and math.isfinite(v) for v in [*xs, *ys]):
            errors.append(f"points[{i}]: x and y must be finite numbers.")
    return errors


def validate(
    series: list[dict[str, Any]],
    x_range: list[float | str] | None,
    y_range: list[float | str] | None,
    points: list[dict[str, Any]],
    texts: dict[str, str | None],
) -> list[str]:
    errors = []
    if not series and not points:
        errors.append("Give series (expressions to plot) or points (data points).")
    if len(series) > MAX_SERIES:
        errors.append(f"At most {MAX_SERIES} series.")
    for i, item in enumerate(series[:MAX_SERIES]):
        expr = item.get("expr") or ""
        errors += [f"series[{i}] '{expr}': {e}" for e in mathexpr.check(expr)]
    errors += _check_range("x_range", x_range, required=True)
    errors += _check_range("y_range", y_range, required=False)
    errors += _check_points(points)
    labels = {f"series[{i}].label": s.get("label") for i, s in enumerate(series)}
    labels |= {f"points[{i}].label": p.get("label") for i, p in enumerate(points)}
    for where, text in (texts | labels).items():
        if text and len(text) > MAX_TEXT:
            errors.append(f"{where} too long (max {MAX_TEXT} chars).")
    return errors


def plot_function(
    series: list[dict[str, Any]] | None,
    x_range: list[float | str] | None,
    y_range: list[float | str] | None = None,
    title: str | None = None,
    x_label: str | None = None,
    y_label: str | None = None,
    points: list[dict[str, Any]] | None = None,
    name: str | None = None,
) -> ToolResult:
    series, points = series or [], points or []
    texts = {"title": title, "x_label": x_label, "y_label": y_label}
    if errors := validate(series, x_range, y_range, points, texts):
        return ToolResult.failure(errors, INPUT_HINT)

    payload = {"series": series, "x_range": x_range, "y_range": y_range, "points": points, **texts}
    timeout = sandbox.default_timeout()
    with tempfile.TemporaryDirectory(prefix="openstem-plot-") as tmp:
        try:
            result = sandbox.run_python("openstem.workers.plot", payload, Path(tmp), timeout)
        except sandbox.WorkerFailed as exc:
            message = TIMEOUT_HINT.format(timeout=timeout) if exc.timed_out else str(exc)
            return ToolResult.failure([message])
        if not result.get("ok"):
            return ToolResult.failure(result.get("errors") or ["unknown failure"], result.get("hint"))
        stem = output.new_stem(name or title, default="plot")
        svg_path, png_path = stem.with_suffix(".svg"), stem.with_suffix(".png")
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["svg"]), svg_path)
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["png"]), png_path)
        # 같은 이름의 PDF: export_document가 SVG 대신 이것을 쓴다(MuPDF가 SVG clip-path를 무시해 곡선이 축 밖으로 나간다)
        pdf_path = None
        if result.get("pdf"):
            pdf_path = stem.with_suffix(".pdf")
            shutil.copyfile(sandbox.worker_file(Path(tmp), result["pdf"]), pdf_path)

    data: dict[str, Any] = {
        "ok": True,
        "svg_path": str(svg_path),
        "png_path": str(png_path),
        **({"pdf_path": str(pdf_path)} if pdf_path else {}),
        "series": result["series"],
        "x_range": result["x_range"],
        "y_range": result["y_range"],
    }
    if result.get("warnings"):
        data["warnings"] = result["warnings"]
    data["check"] = "Check the preview and series[].parsed: are expressions, ranges and labels as intended?"
    return ToolResult(ok=True, data=data, preview_png=png_path.read_bytes())
