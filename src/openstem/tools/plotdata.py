"""plot_data: 실험 데이터 그래프(오차 막대 + 최소제곱 맞춤). 표 읽기·맞춤·그리기는 작업자(openstem.workers.plotdata)에서 한다."""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from openstem import output, sandbox
from openstem.tools import ToolResult

FITS = ("none", "linear", "poly2", "poly3", "exponential", "power", "log")
MAX_SERIES = 8
MAX_POINTS = 5000
MAX_TABLE = 200_000
MAX_TEXT = 200
MAX_COLUMN_NAME = 100

INPUT_HINT = 'Fix the input and call again. E.g. {"series": [{"x": [1, 2, 3], "y": [2.1, 3.9, 6.2], "yerr": 0.2}], "fit": "linear"}'
TIMEOUT_HINT = "Timed out ({timeout:.0f} s). Use fewer points and call again. A first run can be slow; try once more."

DESCRIPTION = r"""Plot measured data with error bars and fit a curve (equation with ± standard errors, R²). For lab reports.
Example: {"series": [{"x": [0.1, 0.2, 0.3], "y": [0.52, 0.98, 1.55], "yerr": 0.05}], "fit": "linear", "x_label": "m (kg)"}
Or paste CSV/markdown table text in `table` and pick columns by header name; "2.3 ± 0.1" is value ± error. fit: linear, poly2, poly3, exponential, power, log.
On failure: errors name the series/column/row; fix it and call again."""


def _finite_list(values: Any) -> bool:
    return isinstance(values, (list, tuple)) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
    )


def _check_err(where: str, err: Any, n: int) -> list[str]:
    if err is None:
        return []
    if isinstance(err, (int, float)) and not isinstance(err, bool):
        values = [err]
    elif _finite_list(err):
        values = list(err)
        if len(values) != n:
            return [f"{where} must have one value per point ({n}) or be a single number (got {len(values)})."]
    else:
        return [f"{where} must be a list of numbers or a single number."]
    if not all(math.isfinite(v) and v >= 0 for v in values):
        return [f"{where} must be finite and ≥ 0 (errors are magnitudes)."]
    return []


def _check_series(series: list[dict[str, Any]]) -> list[str]:
    errors = []
    if len(series) > MAX_SERIES:
        errors.append(f"At most {MAX_SERIES} series.")
    for i, s in enumerate(series[:MAX_SERIES]):
        xs, ys = s.get("x"), s.get("y")
        if not _finite_list(xs) or not _finite_list(ys):
            errors.append(f"series[{i}]: x and y must be lists of finite numbers.")
            continue
        if len(xs) != len(ys):
            errors.append(f"series[{i}]: x and y lengths differ (x {len(xs)}, y {len(ys)}).")
            continue
        if not xs:
            errors.append(f"series[{i}]: no points.")
        elif len(xs) > MAX_POINTS:
            errors.append(f"series[{i}]: at most {MAX_POINTS} points.")
        errors += _check_err(f"series[{i}].yerr", s.get("yerr"), len(xs))
        errors += _check_err(f"series[{i}].xerr", s.get("xerr"), len(xs))
        if s.get("fit") is not None and s["fit"] not in FITS:
            errors.append(f"series[{i}].fit '{s['fit']}' is unknown. Available: {', '.join(FITS)}")
    return errors


def _check_columns(columns: dict[str, Any]) -> list[str]:
    errors = []
    y_columns, yerr_columns = columns.get("y_columns") or [], columns.get("yerr_columns")
    if len(y_columns) > MAX_SERIES:
        errors.append(f"At most {MAX_SERIES} y_columns.")
    if yerr_columns is not None and len(yerr_columns) != len(y_columns):
        errors.append(
            f"yerr_columns must have as many entries as y_columns (paired in order). Now {len(yerr_columns)} vs {len(y_columns)}."
        )
    names = [columns.get("x_column"), columns.get("xerr_column"), *y_columns, *(yerr_columns or [])]
    for name in names:
        if name is not None and len(str(name)) > MAX_COLUMN_NAME:
            errors.append(f"Column name too long (max {MAX_COLUMN_NAME} chars).")
            break
    return errors


def validate(
    series: list[dict[str, Any]],
    table: str | None,
    columns: dict[str, Any],
    fit: str,
    texts: dict[str, str | None],
) -> list[str]:
    errors = []
    has_table = bool(table and table.strip())
    if bool(series) == has_table:
        return ["Give exactly one of series (number lists) or table (table text)."]
    if fit not in FITS:
        errors.append(f"fit '{fit}' is unknown. Available: {', '.join(FITS)}")
    if has_table:
        if len(table) > MAX_TABLE:
            errors.append(f"table too long ({len(table)} chars, max {MAX_TABLE}).")
        errors += _check_columns(columns)
    else:
        if any(v for v in columns.values()):
            errors.append("x_column, y_columns, yerr_columns and xerr_column are only used with table.")
        errors += _check_series(series)
    labels = {f"series[{i}].label": s.get("label") for i, s in enumerate(series)}
    for where, text in (texts | labels).items():
        if text and len(text) > MAX_TEXT:
            errors.append(f"{where} too long (max {MAX_TEXT} chars).")
    return errors


def plot_data(
    series: list[dict[str, Any]] | None = None,
    table: str | None = None,
    x_column: str | None = None,
    y_columns: list[str] | None = None,
    yerr_columns: list[str] | None = None,
    xerr_column: str | None = None,
    fit: str = "none",
    title: str | None = None,
    x_label: str | None = None,
    y_label: str | None = None,
    log_x: bool = False,
    log_y: bool = False,
    name: str | None = None,
) -> ToolResult:
    series = series or []
    columns = {"x_column": x_column, "y_columns": y_columns, "yerr_columns": yerr_columns, "xerr_column": xerr_column}
    texts = {"title": title, "x_label": x_label, "y_label": y_label}
    if errors := validate(series, table, columns, fit, texts):
        return ToolResult.failure(errors, INPUT_HINT)

    payload = {
        "series": series,
        "table": table if table and table.strip() else None,
        "columns": columns,
        "fit": fit,
        "log_x": bool(log_x),
        "log_y": bool(log_y),
        **texts,
    }
    timeout = sandbox.default_timeout()
    with tempfile.TemporaryDirectory(prefix="openstem-data-") as tmp:
        try:
            result = sandbox.run_python("openstem.workers.plotdata", payload, Path(tmp), timeout)
        except sandbox.WorkerFailed as exc:
            message = TIMEOUT_HINT.format(timeout=timeout) if exc.timed_out else str(exc)
            return ToolResult.failure([message])
        if not result.get("ok"):
            return ToolResult.failure(result.get("errors") or ["unknown failure"], result.get("hint"))
        stem = output.new_stem(name or title, default="data")
        svg_path, png_path = stem.with_suffix(".svg"), stem.with_suffix(".png")
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["svg"]), svg_path)
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["png"]), png_path)
        # 같은 이름의 PDF: export_document가 SVG 대신 이것을 쓴다(plot_function과 같은 규칙)
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
    }
    if result.get("warnings"):
        data["warnings"] = result["warnings"]
    data["check"] = "Check the preview: do points, error bars and the fit curve match the data, and are the equation values plausible?"
    return ToolResult(ok=True, data=data, preview_png=png_path.read_bytes())
