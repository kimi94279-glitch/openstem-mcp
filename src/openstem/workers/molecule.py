"""render_molecule 작업자: RDKit으로 SMILES를 파싱·검사하고 구조식을 그린다.

RDKit이 그린 SVG를 pymupdf로 PDF 한 쪽에 옮기고(여백을 잘라 내고), 범례 글자는 pymupdf가 쓴다.
RDKit의 글자 렌더러는 한글을 못 그리기 때문이다. SVG·PNG 변환은 서버가 render_tikz와 같은 방식으로 한다.
"""

from __future__ import annotations

import contextlib
import io
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdChemReactions, rdDepictor, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

from openstem.render import fonts
from openstem.workers import WorkerError, serve

PDF_NAME = "molecule.pdf"
MAX_HEAVY_ATOMS = 300
BOND_PX = 36  # 결합 하나의 길이(px). 1px = 0.75pt로 PDF에 옮긴다.
PX_TO_PT = 0.75
MARGIN_PT = 6
LEGEND_PT = 11

HINT = (
    "Fix the SMILES and call again. Pass SMILES, not a name "
    "(e.g. benzene c1ccccc1, ethanol CCO, aspirin CC(=O)Oc1ccccc1C(=O)O). Atom numbers follow the SMILES order (from 1)."
)

# SMILES에서 원자 하나에 해당하는 글자 묶음. 원자 번호(RDKit 인덱스)는 이 순서와 같다.
_ATOM_RE = re.compile(r"\[[^\]]*\]|Br|Cl|[BCNOSPFI]|[bcnosp]|\*")
_SKIP_RE = re.compile(r"%\d\d|.", re.DOTALL)
_PARSE_REASONS = {
    "syntax error": "SMILES syntax error",
    "unclosed ring": "unclosed ring bond (each ring digit must appear twice, e.g. C1CCCCC1)",
    "extra close parentheses": "unmatched ')'",
    "extra open parentheses": "unclosed '('",
}


def atom_positions(smiles: str) -> list[int]:
    """각 원자가 SMILES의 몇 번째 글자(0부터)에서 시작하는지."""
    positions, pos = [], 0
    while pos < len(smiles):
        if m := _ATOM_RE.match(smiles, pos):
            positions.append(pos)
        else:
            m = _SKIP_RE.match(smiles, pos)
        pos = m.end()
    return positions


def _caret(smiles: str, pos: int) -> list[str]:
    """SMILES와 문제 위치를 가리키는 ^ 두 줄. 길면 앞뒤를 자른다."""
    start = max(0, pos - 30)
    shown = ("…" if start else "") + smiles[start : pos + 30] + ("…" if pos + 30 < len(smiles) else "")
    offset = pos - start + (1 if start else 0)
    return [f"  {shown}", "  " + " " * offset + "^"]


def _parse_error(log: str, smiles: str, where: str) -> list[str]:
    lines = [re.sub(r"^\[[\d:]+\]\s*", "", ln).strip() for ln in log.splitlines()]
    lines = [ln.removeprefix("SMILES Parse Error:").strip() for ln in lines if ln]
    reason = next((ln for ln in lines if not ln.startswith(("check for mistakes", "Failed parsing"))), "")
    reason = re.split(r" (?:while parsing|for input)", reason)[0].strip()
    text = next((ko for en, ko in _PARSE_REASONS.items() if reason.startswith(en)), f"cannot parse SMILES: {reason}")
    out = [f"{where}: {text}"]
    if m := re.search(r"around position (\d+)", log):
        pos = int(m.group(1)) - 1
        out[0] += f" (near char {pos + 1})"
        out += _caret(smiles, min(pos, len(smiles) - 1))
    return out


def _problem_lines(mol: Chem.Mol, smiles: str, where: str) -> list[str]:
    positions = atom_positions(smiles)
    table = Chem.GetPeriodicTable()

    def atom(idx: int) -> str:
        sym = mol.GetAtomWithIdx(idx).GetSymbol()
        at = f", char {positions[idx] + 1}" if idx < len(positions) else ""
        return f"atom #{idx + 1} ({sym}{at})"

    out: list[str] = []
    for p in Chem.DetectChemistryProblems(mol):
        kind, msg = p.GetType(), p.Message()
        first: int | None = None
        if kind == "AtomValenceException":
            idx = p.GetAtomIdx()
            first = idx
            a = mol.GetAtomWithIdx(idx)
            allowed = max(table.GetValenceList(a.GetAtomicNum()))
            m = re.search(r", (\d+), is greater", msg)
            got = f" (has {m.group(1)})" if m else ""
            out.append(
                f"{where}: {atom(idx)} exceeds the valence {allowed} allowed for {a.GetSymbol()}{got}. "
                "Remove bonds or add a charge (e.g. [N+])."
            )
        elif kind == "KekulizeException":
            ids = list(p.GetAtomIndices())
            first = ids[0] if ids else None
            span = f"atoms #{ids[0] + 1}-#{ids[-1] + 1}" if ids else "aromatic ring"
            out.append(
                f"{where}: cannot kekulize the aromatic (lowercase) ring at {span}. "
                "Check the ring size and lowercase atoms; N in a 5-membered ring may need [nH] (e.g. c1cc[nH]c1)."
            )
        elif kind == "AtomKekulizeException":
            idx = p.GetAtomIdx()
            first = idx
            out.append(f"{where}: {atom(idx)} is not in a ring but written aromatic (lowercase). Use uppercase.")
        else:
            idx = p.GetAtomIdx() if hasattr(p, "GetAtomIdx") else None
            first = idx
            out.append(f"{where}: {atom(idx) + ': ' if idx is not None else ''}{kind}: {msg}")
        if first is not None and first < len(positions) and len(out) == 1:
            out += _caret(smiles, positions[first])
    return out


def parse_smiles(smiles: str, where: str) -> Chem.Mol:
    """SMILES 하나를 파싱·sanitize한다. 실패하면 어디서 깨졌는지 담은 WorkerError."""
    buf = io.StringIO()
    rdBase.LogToPythonStderr()
    with contextlib.redirect_stderr(buf):
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
        if mol is None:
            raise WorkerError(_parse_error(buf.getvalue(), smiles, where), HINT)
        if problems := _problem_lines(mol, smiles, where):
            raise WorkerError(problems, HINT)
        try:
            Chem.SanitizeMol(mol)
        except Exception as exc:
            raise WorkerError([f"{where}: structure check failed: {str(exc)[:150]}"], HINT) from None
    if mol.GetNumHeavyAtoms() > MAX_HEAVY_ATOMS:
        raise WorkerError([f"{where}: too many atoms ({mol.GetNumHeavyAtoms()}, max {MAX_HEAVY_ATOMS})."])
    return mol


def describe(mol: Chem.Mol) -> dict[str, Any]:
    info: dict[str, Any] = {
        "smiles": Chem.MolToSmiles(mol),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "mol_weight": round(Descriptors.MolWt(mol), 3),
    }
    if charge := Chem.GetFormalCharge(mol):
        info["charge"] = charge
    return info


def element_counts(mols: list[Chem.Mol]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for mol in mols:
        for a in mol.GetAtoms():
            counts[a.GetSymbol()] += 1
            counts["H"] += a.GetTotalNumHs()
        counts["charge"] += Chem.GetFormalCharge(mol)
    return counts


# ── 그리기 ──────────────────────────────────────────────────────────


def _number_atoms(mol: Chem.Mol) -> Chem.Mol:
    mol = Chem.Mol(mol)
    for a in mol.GetAtoms():
        a.SetProp("atomNote", str(a.GetIdx() + 1))
    return mol


def _coords_finite(mol: Chem.Mol) -> bool:
    return mol.GetNumAtoms() == 0 or bool(np.isfinite(mol.GetConformer().GetPositions()).all())


def ensure_coords(mols: list[Chem.Mol]) -> None:
    """2D 좌표를 만든다. CoordGen이 NaN 좌표를 내면(RDKit 2026.09의 [H][H]·[H+] 등 수소만 있는 분자)
    이 요청 동안 RDKit 기본 좌표 생성기로 바꿔 모두 다시 계산한다(DrawReaction도 같은 설정을 쓴다)."""
    for mol in mols:
        rdDepictor.Compute2DCoords(mol)
    if all(_coords_finite(m) for m in mols):
        return
    rdDepictor.SetPreferCoordGen(False)
    for mol in mols:
        rdDepictor.Compute2DCoords(mol)


def _extent(mols: list[Chem.Mol]) -> tuple[float, float]:
    """2D 좌표의 가로·세로 크기를 결합 길이 단위로 더한 값(가로는 합, 세로는 최대)."""
    width = height = 0.0
    ensure_coords(mols)
    for mol in mols:
        if mol.GetNumAtoms() == 0:
            continue
        pos = mol.GetConformer().GetPositions()
        width += (pos[:, 0].max() - pos[:, 0].min()) / 1.5 + 2
        height = max(height, (pos[:, 1].max() - pos[:, 1].min()) / 1.5 + 2)
    return width, height


def _svg(draw: Any) -> str:
    draw.FinishDrawing()
    return draw.GetDrawingText()


def _new_drawer(width_bonds: float, height_bonds: float) -> Any:
    # 넉넉한 캔버스에 고정 결합 길이로 그리고, 나중에 여백을 잘라 낸다.
    w = int(min(4000, max(300, (width_bonds + 3) * BOND_PX * 1.3)))
    h = int(min(3000, max(240, (height_bonds + 3) * BOND_PX * 1.3)))
    draw = rdMolDraw2D.MolDraw2DSVG(w, h)
    opts = draw.drawOptions()
    opts.fixedBondLength = BOND_PX
    opts.clearBackground = False
    opts.annotationFontScale = 0.6
    return draw


def draw_molecule(mol: Chem.Mol, numbers: bool) -> str:
    ensure_coords([mol])
    target = _number_atoms(mol) if numbers else Chem.Mol(mol)
    draw = _new_drawer(*_extent([target]))
    draw.DrawMolecule(target)
    return _svg(draw)


def draw_reaction(reactants: list[Chem.Mol], agents: list[Chem.Mol], products: list[Chem.Mol], numbers: bool) -> str:
    # 좌표 생성기만 먼저 고른다. 템플릿에는 좌표를 넣지 않는다(좌표가 있는 [H][H] 템플릿은 DrawReaction이 NaN으로 그린다).
    ensure_coords([Chem.Mol(m) for m in (*reactants, *agents, *products)])
    prep = (lambda m: _number_atoms(m)) if numbers else (lambda m: Chem.Mol(m))
    rxn = rdChemReactions.ChemicalReaction()
    for m in reactants:
        rxn.AddReactantTemplate(prep(m))
    for m in agents:
        rxn.AddAgentTemplate(prep(m))
    for m in products:
        rxn.AddProductTemplate(prep(m))
    width, height = _extent([*reactants, *products])
    agent_w, agent_h = _extent(agents) if agents else (0.0, 0.0)
    count = len(reactants) + len(products)
    draw = _new_drawer(width + count * 1.5 + max(4.0, agent_w), max(height, height / 2 + agent_h + 1))
    draw.DrawReaction(rxn)
    return _svg(draw)


def compose_pdf(svg: str, legend: str | None, path: Path) -> list[str]:
    """RDKit SVG를 PDF 한 쪽으로 옮기고 여백을 잘라 낸다. 범례는 아래에 쓴다. 경고를 돌려준다."""
    warn: list[str] = []
    src = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg")
    drawing = pymupdf.open("pdf", src.convert_to_pdf())
    page = drawing[0]
    boxes = [pymupdf.Rect(r) for _, r in page.get_bboxlog() if not pymupdf.Rect(r).is_empty]
    if not boxes:
        raise WorkerError(["Nothing to draw (empty structure)."], HINT)
    content = boxes[0]
    for r in boxes[1:]:
        content |= r
    content = (content + (-4, -4, 4, 4)) & page.rect

    width, height = content.width * PX_TO_PT, content.height * PX_TO_PT
    font = None
    if legend:
        font = pymupdf.Font(fontfile=str(fonts.hangul_font(Path.cwd())))
        if missing := sorted({c for c in legend if not c.isspace() and not font.has_glyph(ord(c))}):
            warn.append(f"Legend characters missing from the font were dropped: {' '.join(missing[:10])}")
        width = max(width, font.text_length(legend, fontsize=LEGEND_PT))
    page_h = height + 2 * MARGIN_PT + (LEGEND_PT * 1.8 if legend else 0)
    out = pymupdf.open()
    target = out.new_page(width=width + 2 * MARGIN_PT, height=page_h)
    x0 = (target.rect.width - content.width * PX_TO_PT) / 2
    target.show_pdf_page(
        pymupdf.Rect(x0, MARGIN_PT, x0 + content.width * PX_TO_PT, MARGIN_PT + height), drawing, 0, clip=content
    )
    if legend and font is not None:
        target.insert_font(fontname="legend", fontbuffer=font.buffer)
        text_w = font.text_length(legend, fontsize=LEGEND_PT)
        baseline = MARGIN_PT + height + LEGEND_PT * 1.4
        target.insert_text(((target.rect.width - text_w) / 2, baseline), legend, fontname="legend", fontsize=LEGEND_PT)
    out.save(path)
    return warn


# ── 진입점 ──────────────────────────────────────────────────────────


def _split_reaction(smiles: str) -> tuple[list[str], list[str], list[str]]:
    parts = smiles.split(">")
    if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
        raise WorkerError(
            ["Reaction SMILES must be 'reactants>>products' or 'reactants>agents>products' (e.g. CCO.CC(=O)O>>CCOC(C)=O.O)."],
            HINT,
        )
    return tuple([m for m in part.split(".") if m] for part in parts)  # type: ignore[return-value]


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    rdDepictor.SetPreferCoordGen(True)
    smiles = payload["smiles"].strip().split()[0]  # CXSMILES 꼬리(공백 뒤)는 버린다
    numbers = bool(payload.get("show_atom_numbers"))
    legend = (payload.get("legend") or "").strip() or None
    kind = payload.get("kind") or "molecule"
    warn: list[str] = []
    if kind == "molecule" and ">" in smiles:
        kind = "reaction"
        warn.append("Found '>', so drew it as a reaction SMILES (kind=reaction).")
    elif kind == "reaction" and ">" not in smiles:
        raise WorkerError(["kind=reaction but no '>>'. For one molecule use kind=molecule; for a reaction write reactants>>products."])

    result: dict[str, Any] = {"ok": True, "pdf": PDF_NAME, "kind": kind}
    if kind == "molecule":
        mol = parse_smiles(smiles, "SMILES")
        result.update(describe(mol))
        result["canonical_smiles"] = result.pop("smiles")
        svg = draw_molecule(mol, numbers)
    else:
        groups = _split_reaction(smiles)
        names = ("reactant", "agent", "product")
        parsed: list[list[Chem.Mol]] = []
        errors: list[str] = []
        for name, group in zip(names, groups, strict=True):
            mols = []
            for i, s in enumerate(group, start=1):
                try:
                    mols.append(parse_smiles(s, f"{name} {i} '{s}'"))
                except WorkerError as exc:
                    errors += exc.errors
            parsed.append(mols)
        if errors:
            raise WorkerError(errors[:10], HINT)
        reactants, agents, products = parsed
        result["reactants"] = [describe(m) for m in reactants]
        if agents:
            result["agents"] = [describe(m) for m in agents]
        result["products"] = [describe(m) for m in products]
        diff = element_counts(reactants)
        diff.subtract(element_counts(products))
        unbalanced = {k: v for k, v in diff.items() if v}
        result["balanced"] = not unbalanced
        if unbalanced:
            detail = ", ".join(f"{k} {v:+d}" for k, v in sorted(unbalanced.items()))
            warn.append(
                f"Atoms are not balanced (reactants - products: {detail}). Repeat molecules for coefficients "
                "(e.g. 2H2 -> [H][H].[H][H]) or check for a missing product. Ignore if omitted on purpose."
            )
        svg = draw_reaction(reactants, agents, products, numbers)

    warn += compose_pdf(svg, legend, Path(PDF_NAME))
    if warn:
        result["warnings"] = warn
    return result


if __name__ == "__main__":
    serve(handle)
