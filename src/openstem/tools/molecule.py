"""render_molecule: SMILES(분자·반응)를 구조식 그림으로. RDKit 처리는 작업자(openstem.workers.molecule) 서브프로세스에서 한다."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from openstem import output, sandbox
from openstem.render import pages
from openstem.tools import ToolResult

KINDS = ("molecule", "reaction")
MAX_SMILES = 2000
MAX_LEGEND = 200

TIMEOUT_HINT = "Timed out ({timeout:.0f} s): the molecule is too large or complex. Split it into smaller parts and call again."
# 작업자가 준비 신호를 보내기 전(라이브러리 읽기)에 시간이 넘은 경우만 "다시 해 보라"고 한다. 그 뒤의 시간 초과는 입력이 무거운 것이다.
STARTUP_HINT = "On a first run loading the libraries can be slow; try once more."

DESCRIPTION = r"""Draw a molecule or reaction from SMILES (not a name): SVG/PDF/PNG, preview, formula, molecular weight. Reaction: reactants>>products.
Example: {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "legend": "aspirin"}
Check that formula and mol_weight match the intended molecule.
On failure: errors point at the atom (#index, ^); fix the SMILES there and call again."""


def validate(smiles: str, kind: str, legend: str | None) -> list[str]:
    errors = []
    if not smiles or not smiles.strip():
        errors.append("smiles is empty.")
    elif len(smiles) > MAX_SMILES:
        errors.append(f"smiles is too long ({len(smiles)} chars, max {MAX_SMILES}).")
    if kind not in KINDS:
        errors.append(f"kind must be molecule or reaction (got: {kind}).")
    if legend and len(legend) > MAX_LEGEND:
        errors.append(f"legend is too long (max {MAX_LEGEND} chars).")
    return errors


def render_molecule(
    smiles: str,
    kind: str = "molecule",
    legend: str | None = None,
    show_atom_numbers: bool = False,
    name: str | None = None,
) -> ToolResult:
    if errors := validate(smiles, kind, legend):
        return ToolResult.failure(errors, "Fix the input and call again.")

    payload = {"smiles": smiles, "kind": kind, "legend": legend, "show_atom_numbers": show_atom_numbers}
    timeout = sandbox.default_timeout()
    with tempfile.TemporaryDirectory(prefix="openstem-mol-") as tmp:
        try:
            result = sandbox.run_python("openstem.workers.molecule", payload, Path(tmp), timeout)
        except sandbox.WorkerFailed as exc:
            message = str(exc) if not exc.timed_out else (
                f"{exc} {STARTUP_HINT}" if str(exc).startswith("Worker startup") else TIMEOUT_HINT.format(timeout=timeout)
            )
            return ToolResult.failure([message])
        if not result.get("ok"):
            return ToolResult.failure(result.get("errors") or ["Unknown failure"], result.get("hint"))
        stem = output.new_stem(name or legend, default=result["kind"])
        pdf_path = stem.with_suffix(".pdf")
        shutil.copyfile(sandbox.worker_file(Path(tmp), result["pdf"]), pdf_path)

    try:
        converted = pages.figure(pdf_path, stem)
    except pages.ConvertError as exc:
        pdf_path.unlink(missing_ok=True)
        return ToolResult.failure(exc.errors, exc.hint)
    data: dict[str, Any] = {
        "ok": True,
        "svg_path": str(converted.svg_path),
        "pdf_path": str(pdf_path),
        "png_path": str(converted.png_path),
    }
    data.update({k: v for k, v in result.items() if k not in ("ok", "pdf", "warnings")})
    warnings = result.get("warnings", []) + converted.warnings
    if warnings:
        data["warnings"] = warnings
    data["check"] = "Check that formula, mol_weight and the preview match the intended molecule."
    return ToolResult(ok=True, data=data, preview_png=converted.preview_png)
