"""결과물 재현 기록: <stem>.json 사이드카와 원본(.tex·.md·.csv)이 생기고, OPENSTEM_RECORD=0이면 안 생긴다."""

import csv
import io
import json
from pathlib import Path

import pytest

from openstem import __version__, output
from openstem.tools.export import export_document
from openstem.tools.molecule import render_molecule
from openstem.tools.plot import plot_function
from openstem.tools.plotdata import plot_data
from openstem.tools.tikz import render_tikz

TIKZ_CODE = r"\draw[->] (0,0) -- (1,1) node[right] {힘 $F$};"


def _record(data: dict) -> dict:
    path = Path(data["record_path"])
    assert path.is_file()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["openstem_version"] == __version__
    assert record["created"][:4].isdigit() and "T" in record["created"]
    assert path.name in record["outputs"]
    stem = path.name.removesuffix(".json")
    for name in record["outputs"]:  # 같은 stem의 파일만, 실제로 있는 것만
        assert name.startswith(stem + ".")
        assert (path.parent / name).is_file()
    return record


def _no_local_paths(record: dict, output_dir: Path) -> None:
    dumped = json.dumps(record, ensure_ascii=False)
    assert str(output_dir) not in dumped
    assert str(Path.home()) not in dumped


def test_render_tikz_saves_code_and_record(tectonic, output_dir):
    result = render_tikz(TIKZ_CODE, tikz_libraries=["calc"], name="force")
    assert result.ok, result.data
    data = result.data
    source = Path(data["source_path"])
    assert source.suffix == ".tex" and source.read_text(encoding="utf-8") == TIKZ_CODE + "\n"
    full = source.with_name(source.name.removesuffix(".tex") + ".full.tex")
    assert r"\documentclass" in full.read_text(encoding="utf-8")
    record = _record(data)
    assert record["tool"] == "render_tikz"
    assert record["inputs"] == {"code": TIKZ_CODE, "packages": None, "tikz_libraries": ["calc"], "name": "force"}
    assert record["source"] == source.name
    assert "Tectonic" in record["versions"]["tectonic"]
    assert record["results"]["size_cm"] == data["size_cm"]
    names = set(record["outputs"])
    assert {Path(data[k]).name for k in ("svg_path", "pdf_path", "png_path")} <= names
    assert {source.name, full.name} <= names
    _no_local_paths(record, output_dir)


def test_export_saves_markdown_with_relative_image_paths(tectonic, output_dir):
    fig = render_tikz(r"\draw (0,0) circle (1);", name="circle")
    assert fig.ok, fig.data
    markdown = f"## 원\n\n$A = \\pi r^2$\n\n![원]({fig.data['svg_path']})\n"
    result = export_document(markdown, format="pdf", title="정리")
    assert result.ok, result.data
    source = Path(result.data["source_path"])
    assert source.suffix == ".md"
    saved = source.read_text(encoding="utf-8")
    image = Path(fig.data["svg_path"]).name
    assert f"![원]({image})" in saved and str(output_dir) not in saved
    record = _record(result.data)
    assert record["tool"] == "export_document"
    assert record["inputs"]["format"] == "pdf" and record["inputs"]["title"] == "정리"
    assert record["results"]["pages"] == 1 and record["results"]["images"] == [image]
    assert record["versions"]["pandoc"] and record["versions"]["tectonic"]
    _no_local_paths(record, output_dir)
    # 저장된 .md를 그대로 다시 내보낼 수 있다(상대 경로 그림은 결과물 폴더 기준).
    again = export_document(saved, format="docx")
    assert again.ok, again.data


def test_export_exam_records_problems(output_dir):
    markdown = "## 문제 1 (5점)\n\n$1+1$은?\n\n## 문제 2 (5점)\n\n$2+2$는?\n\n# 정답과 해설\n\n## 문제 1\n\n**정답** 2\n\n## 문제 2\n\n**정답** 4\n"
    result = export_document(markdown, format="docx", layout="exam")
    assert result.ok, result.data
    record = _record(result.data)
    assert record["results"]["problems"] == result.data["problems"]
    assert record["results"]["layout"] == "exam"
    assert "tectonic" not in record["versions"]


def test_plot_function_record_has_parsed_series_and_warnings(output_dir):
    result = plot_function([{"expr": "1/x"}], [-2, 2], name="hyperbola")
    assert result.ok, result.data
    data = result.data
    assert "source_path" not in data  # 원본은 inputs(식) 자체다
    record = _record(data)
    assert record["tool"] == "plot_function"
    assert record["inputs"]["series"] == [{"expr": "1/x"}] and record["inputs"]["x_range"] == [-2, 2]
    assert record["results"]["series"][0]["parsed"] == "1/x"
    assert record["warnings"] == data["warnings"] and any("x = 0" in w for w in record["warnings"])
    assert {"sympy", "numpy", "matplotlib"} <= set(record["versions"])


def test_plot_data_saves_csv_and_fit(output_dir):
    table = "| m (kg) | a |\n|---|---|\n| 0.1 | 0.52 ± 0.05 |\n| 0.2 | 0.98 ± 0.05 |\n| 0.3 | 1.55 ± 0.05 |\n| 0.4 | 2.01 ± 0.05 |\n"
    result = plot_data(table=table, x_column="m (kg)", y_columns=["a"], fit="linear")
    assert result.ok, result.data
    source = Path(result.data["source_path"])
    assert source.suffix == ".csv"
    rows = list(csv.DictReader(io.StringIO(source.read_text(encoding="utf-8"))))
    assert [float(r["x"]) for r in rows] == [0.1, 0.2, 0.3, 0.4]
    assert [float(r["y"]) for r in rows] == [0.52, 0.98, 1.55, 2.01]
    assert {float(r["yerr"]) for r in rows} == {0.05}
    record = _record(result.data)
    assert record["tool"] == "plot_data" and record["inputs"]["table"] == table
    fit = record["results"]["series"][0]["fit"]
    assert fit == result.data["series"][0]["fit"]
    assert set(fit["params"]) and all("stderr" in p for p in fit["params"].values()) and fit["r2"] > 0.99
    assert {"numpy", "matplotlib"} <= set(record["versions"])


def test_plot_data_lists_csv_has_all_series(output_dir):
    series = [{"x": [1, 2], "y": [3, 4], "label": "A"}, {"x": [1, 2, 3], "y": [5, 6, 7], "label": "B", "xerr": 0.1}]
    result = plot_data(series=series)
    assert result.ok, result.data
    rows = list(csv.DictReader(io.StringIO(Path(result.data["source_path"]).read_text(encoding="utf-8"))))
    assert [r["series"] for r in rows] == ["A", "A", "B", "B", "B"]
    assert rows[0]["xerr"] == "" and rows[2]["xerr"] == "0.1"
    assert _record(result.data)["inputs"]["series"] == series


def test_render_molecule_record_has_formula(output_dir):
    result = render_molecule("CC(=O)Oc1ccccc1C(=O)O", legend="aspirin")
    assert result.ok, result.data
    assert "versions" not in result.data
    record = _record(result.data)
    assert record["tool"] == "render_molecule"
    assert record["inputs"]["smiles"] == "CC(=O)Oc1ccccc1C(=O)O"
    assert record["results"]["formula"] == result.data["formula"] == "C9H8O4"
    assert record["results"]["mol_weight"] == result.data["mol_weight"]
    assert {"rdkit", "pymupdf"} <= set(record["versions"])


@pytest.mark.parametrize("value", ["0", "off", "false"])
def test_record_off(value, monkeypatch, output_dir):
    monkeypatch.setenv(output.RECORD_ENV, value)
    result = plot_data(series=[{"x": [1, 2, 3], "y": [2, 4, 6]}], fit="linear")
    assert result.ok, result.data
    assert "record_path" not in result.data and "source_path" not in result.data
    assert not list(output_dir.glob("*.json")) and not list(output_dir.glob("*.csv"))
    molecule = render_molecule("CCO")
    assert molecule.ok and "record_path" not in molecule.data
    assert not list(output_dir.glob("*.json"))


def test_record_off_tikz(tectonic, monkeypatch, output_dir):
    monkeypatch.setenv(output.RECORD_ENV, "0")
    result = render_tikz(r"\draw (0,0) -- (1,0);")
    assert result.ok, result.data
    assert "record_path" not in result.data
    assert not list(output_dir.glob("*.tex")) and not list(output_dir.glob("*.json"))


def test_save_failure_is_a_warning(monkeypatch, output_dir):
    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", broken)
    result = render_molecule("CCO")
    assert result.ok, result.data
    assert "record_path" not in result.data
    assert any("reproduction record" in w and "disk full" in w for w in result.data["warnings"])


def test_portable_strips_local_paths(output_dir):
    out = output.output_dir()
    home = Path.home()
    assert output.portable({"a": [f"![x]({out / 'f.svg'})"]}) == {"a": ["![x](f.svg)"]}
    assert output.portable(str(home / "Documents" / "x.png")) == str(Path("~") / "Documents" / "x.png")
    assert output.portable(3) == 3
