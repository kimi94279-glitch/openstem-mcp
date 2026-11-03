"""tests/cases/render_molecule.yaml의 입력을 실제로 그려 기대 결과와 맞춰 본다(작업자 서브프로세스 포함)."""

from pathlib import Path

import pytest
import yaml

from openstem.tools.molecule import render_molecule
from openstem.workers.molecule import atom_positions

CASES = yaml.safe_load((Path(__file__).parent / "cases" / "render_molecule.yaml").read_text(encoding="utf-8"))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_render_molecule_case(case, output_dir):
    result = render_molecule(**case["input"])
    expect = case["expect"]
    errors = " | ".join(result.data.get("errors", []))
    warnings = " | ".join(result.data.get("warnings", []))

    assert result.ok is expect["ok"], errors or result.data
    for fragment in expect.get("errors_contain", []):
        assert fragment in errors
    for fragment in expect.get("warnings_contain", []):
        assert fragment in warnings
    if expect.get("no_warnings"):
        assert "warnings" not in result.data, result.data["warnings"]
    if "hint_contains" in expect:
        assert expect["hint_contains"] in result.data.get("hint", "")
    if "formula" in expect:
        assert result.data["formula"] == expect["formula"]
    if "mol_weight" in expect:
        assert result.data["mol_weight"] == pytest.approx(expect["mol_weight"], abs=0.01)
    if "balanced" in expect:
        assert result.data["balanced"] is expect["balanced"]

    if result.ok:
        for key in ("svg_path", "pdf_path", "png_path"):
            path = Path(result.data[key])
            assert path.parent == output_dir and path.stat().st_size > 0
        assert result.preview_png.startswith(PNG_SIGNATURE)
    else:
        assert result.preview_png is None
        assert len(result.data["errors"]) <= 10


@pytest.mark.parametrize(
    ("smiles", "positions"),
    [
        ("CC(=O)[NH3+]Cl", [0, 1, 4, 6, 12]),
        ("c1ccccc1Br", [0, 2, 3, 4, 5, 6, 8]),
        ("C%12CCCCC%12", [0, 4, 5, 6, 7, 8]),
    ],
)
def test_atom_positions_follow_smiles_order(smiles, positions):
    assert atom_positions(smiles) == positions
