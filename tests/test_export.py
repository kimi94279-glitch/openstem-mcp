"""export_document: 마크다운 → docx/pdf. 그림은 결과물 폴더 안 파일만 들어간다."""

import json
import re
import zipfile
from pathlib import Path

import pytest

from openstem.tools.export import export_document
from openstem.tools.tikz import render_tikz

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

MARKDOWN = """## 자유낙하

높이 $h$에서 가만히 놓은 물체가 땅에 닿는 시간은

$$t = \\sqrt{\\frac{2h}{g}}$$

이다. $h = 20\\,\\mathrm{m}$이면 $t \\approx 2.02\\,\\mathrm{s}$.
"""


@pytest.fixture
def figure(output_dir, tectonic):
    result = render_tikz("\\draw[->] (0,0) -- (2,1) node[right] {힘 $F$};", name="force")
    assert result.ok, result.data
    return Path(result.data["svg_path"])


def test_pdf_with_math_hangul_and_figure(figure, output_dir):
    md = MARKDOWN + f"\n![힘의 방향]({figure})\n"
    result = export_document(md, "pdf", title="역학 정리")
    assert result.ok, result.data
    path = Path(result.data["path"])
    assert path.suffix == ".pdf" and path.parent == output_dir
    assert result.data["pages"] == 1 and result.data["images"] == 1
    assert "warnings" not in result.data, result.data["warnings"]
    assert result.preview_png.startswith(PNG_SIGNATURE)


def test_docx_embeds_png_and_word_math(figure, output_dir):
    md = MARKDOWN + f"\n![힘의 방향]({figure.name})\n"  # 파일 이름만 줘도 결과물 폴더에서 찾는다
    result = export_document(md, "docx", title="역학 정리")
    assert result.ok, result.data
    path = Path(result.data["path"])
    assert path.suffix == ".docx" and result.preview_png is None
    with zipfile.ZipFile(path) as docx:
        media = [n for n in docx.namelist() if n.startswith("word/media/")]
        assert media and all(n.endswith(".png") for n in media)
        assert "m:oMath" in docx.read("word/document.xml").decode("utf-8")


def test_cheatsheet_reports_pages(output_dir, tectonic):
    short = export_document("## 공식\n\n- $F = ma$\n- $E = mc^2$\n", "pdf", title="물리", layout="cheatsheet")
    assert short.ok and short.data["pages"] == 1

    long = "\n\n".join(f"## 주제 {i}\n\n" + "\n".join(f"- 공식 {j}: $a_{j} = b^{j}$" for j in range(25)) for i in range(12))
    result = export_document(long, "pdf", layout="cheatsheet")
    assert result.ok and result.data["pages"] > 1
    assert "fit one page" in " ".join(result.data["warnings"])


def test_bad_math_reports_tex_error(output_dir, tectonic):
    result = export_document("값은 $\\fracc{1}{2}$ 이다.", "pdf")
    assert not result.ok
    assert any(e.startswith("! Undefined control sequence") for e in result.data["errors"])


@pytest.mark.parametrize(
    ("make_markdown", "fragment"),
    [
        (lambda tmp: f"![x]({tmp / 'elsewhere.png'})", "output folder"),
        (lambda tmp: "![x](https://example.com/a.png)", "URLs"),
        (lambda tmp: "![x](없는파일.svg)", "not found"),
        (lambda tmp: "글\n\n$\\input{secret}$", "\\input"),
        (lambda tmp: "   ", "empty"),
        # 정규식이 아니라 pandoc 구조로 검사하므로, 어떤 그림 문법으로 써도 폴더 밖 그림은 막힌다
        (lambda tmp: f"![a][r]\n\n[r]: {(tmp / 'elsewhere.png').as_posix()}\n", "output folder"),
        (lambda tmp: f"![a [b] c]({(tmp / 'elsewhere.png').as_posix()})", "output folder"),
        (lambda tmp: f"![a]( {(tmp / 'elsewhere.png').as_posix()})", "output folder"),
        (lambda tmp: "![a][r]\n\n[r]: http://127.0.0.1:9/leak.png\n", "URLs"),
        (lambda tmp: "---\ntitle: $\\csname input\\endcsname{x}$\n---\n본문", "\\csname"),
        (lambda tmp: "값은 $\\frac{1}{2$ 이다.", "braces"),
    ],
)
@pytest.mark.parametrize("fmt", ["pdf", "pptx"])  # pptx도 같은 구조 검사를 거친다
def test_input_errors(tmp_path, output_dir, make_markdown, fragment, fmt):
    (tmp_path / "elsewhere.png").write_bytes(PNG_SIGNATURE)
    result = export_document(make_markdown(tmp_path), fmt)
    assert not result.ok
    assert fragment in " ".join(result.data["errors"])


def test_unknown_format_is_rejected():
    result = export_document("글", "hwp")
    assert not result.ok and "docx" in result.data["errors"][0]


def test_cheatsheet_with_table_and_hangul_math(output_dir, tectonic):
    """치트시트(두 단)는 표를 longtable 대신 tabular로 바꿔야 컴파일된다. 수식 속 한글은 \\text{}로 감싼다."""
    md = "## 단위\n\n| 양 | 기호 | 단위 |\n|:---|:---:|---:|\n| 높이 | $h$ | m |\n| 마찰력 | $F_{마찰}$ | N |\n"
    result = export_document(md, "pdf", title="표 시험", layout="cheatsheet")
    assert result.ok, result.data
    assert result.data["pages"] == 1 and "warnings" not in result.data


def test_image_path_with_spaces(output_dir, tectonic):
    spaced = output_dir / "with space"
    spaced.mkdir(parents=True)
    figure = render_tikz("\\draw (0,0) -- (1,1);", name="spaced")
    assert figure.ok, figure.data
    target = spaced / "fig.svg"
    target.write_bytes(Path(figure.data["svg_path"]).read_bytes())
    result = export_document(f"![그림]({target})", "docx")
    assert result.ok, result.data
    assert result.data["images"] == 1


# ── 발표 슬라이드(pptx) ─────────────────────────────────────────────


def test_pptx_counts_slides_and_embeds_png_math_and_notes(figure, output_dir):
    md = (
        "# 1부\n\n## 운동학\n\n- 속도 $v = \\frac{dx}{dt}$\n- 가속도 $a = \\frac{dv}{dt}$\n\n"
        f"::: notes\n미분의 뜻을 설명한다.\n:::\n\n## 그림\n\n![힘의 방향]({figure})\n"
    )
    result = export_document(md, "pptx", title="역학 발표")
    assert result.ok, result.data
    path = Path(result.data["path"])
    assert path.suffix == ".pptx" and path.parent == output_dir
    assert result.data["slides"] == 4  # 표지 + 단원(# 1부) + ## 슬라이드 2장
    assert result.preview_png is None and "no preview" in result.data["check"]
    assert "warnings" not in result.data, result.data["warnings"]
    with zipfile.ZipFile(path) as pptx:
        names = pptx.namelist()
        media = [n for n in names if n.startswith("ppt/media/")]
        assert media and all(n.endswith(".png") for n in media)
        slides = "".join(pptx.read(n).decode("utf-8") for n in names if n.startswith("ppt/slides/slide"))
        assert "m:oMath" in slides  # 편집 가능한 수식
        notes = "".join(pptx.read(n).decode("utf-8") for n in names if n.startswith("ppt/notesSlides/notesSlide"))
        assert "미분의 뜻을 설명한다" in notes


def test_pptx_warns_about_crowded_slides_and_missing_headings(output_dir):
    crowded = "## 너무 많은 내용\n\n" + "\n".join(f"- 항목 {i}: 설명이 길게 붙은 글머리표 문장" for i in range(12))
    result = export_document(crowded, "pptx")
    assert result.ok and result.data["slides"] == 1
    assert "overflow" in " ".join(result.data["warnings"])

    flat = export_document("제목 없이 쓴 문단 하나.", "pptx")
    assert flat.ok and "No ## headings" in " ".join(flat.data["warnings"])


# ── 시험지(exam) ────────────────────────────────────────────────────

EXAM = """## 문제 1 (5점)

높이 $h = 20\\,\\mathrm{m}$에서 가만히 놓은 공이 땅에 닿는 시간을 구하라.

## 문제 2 (3점) {space=1}

다음 중 벡터량은? (1) 질량 (2) 속력 (3) 가속도

# 정답과 해설

## 문제 1

**정답** $t = \\sqrt{2h/g} \\approx 2.02\\,\\mathrm{s}$

## 문제 2

**정답** (3)
"""


def test_exam_puts_answers_on_a_new_page(output_dir, tectonic):
    pymupdf = pytest.importorskip("pymupdf")
    result = export_document(EXAM, "pdf", title="중간고사 대비", layout="exam")
    assert result.ok, result.data
    assert result.data["problems"] == 2 and "warnings" not in result.data, result.data
    assert result.data["pages"] == 2 and result.data["answer_page"] == 2
    assert "Problems: page 1; answers and solutions start on page 2" in result.data["check"]
    with pymupdf.open(result.data["path"]) as pdf:  # 한글 낱말 사이 공백은 글자로 뽑히지 않는다
        first, last = (pdf[i].get_text().replace(" ", "") for i in (0, -1))
    assert "문제2" in first and "학번" in first and "정답과해설" not in first
    assert "정답과해설" in last


def test_exam_docx_has_page_break_and_checks_answer_count(output_dir):
    md = EXAM.replace("## 문제 2\n\n**정답** (3)\n", "")  # 정답 하나를 빠뜨린다
    result = export_document(md, "docx", layout="exam")
    assert result.ok, result.data
    assert result.data["problems"] == 2
    assert "1 ## heading(s)" in " ".join(result.data["warnings"])
    with zipfile.ZipFile(result.data["path"]) as docx:
        body = docx.read("word/document.xml").decode("utf-8")
    assert 'w:type="page"' in body and "학번" in body


@pytest.mark.parametrize(
    ("markdown", "fmt", "fragment"),
    [
        ("## 문제 1\n\n본문\n\n## 문제 2\n\n본문\n", "pdf", "# 정답과 해설"),
        ("문제 없이 쓴 글\n\n# 정답과 해설\n\n답\n", "pdf", "## Problem 1"),
        ("## 문제 1 {space=99}\n\n본문\n\n# 정답과 해설\n\n## 문제 1\n\n답\n", "pdf", "space=99"),
        ("## 문제 1\n\n본문\n\n# 정답\n\n답\n\n# 해설\n\n풀이\n", "pdf", "Keep only one"),
        ("## Problem 1\n\nText\n\n# Answers\n\nA\n\n# Solutions\n\nS\n", "pdf", "Keep only one"),
        (EXAM, "pptx", "pdf or docx"),
    ],
)
def test_exam_rule_errors(output_dir, markdown, fmt, fragment):
    result = export_document(markdown, fmt, layout="exam")
    assert not result.ok
    assert fragment in " ".join(result.data["errors"])


def test_exam_accepts_english_answers_heading(output_dir):
    md = "## Problem 1 (5 pts)\n\nFind $t$.\n\n# Answers\n\n## Problem 1\n\n**Answer** $2\\,\\mathrm{s}$\n"
    result = export_document(md, "docx", layout="exam")
    assert result.ok, result.data
    assert result.data["problems"] == 1 and "warnings" not in result.data


# ── 암기 카드(flashcards) ──────────────────────────────────────────

CARDS = "## 역학\n\n| 앞면 | 뒷면 |\n|---|---|\n" + "\n".join(
    f"| 카드 {i} 앞면 | 카드 {i} 뒷면 $F_{{마찰}} = \\mu N_{i}$ |" for i in range(1, 10)
) + "\n| 부등호 | $a<b$ 그리고 x & y |\n"


def test_flashcards_make_mirrored_pdf_and_tsv(output_dir, tectonic):
    pymupdf = pytest.importorskip("pymupdf")
    result = export_document(CARDS, "pdf", title="물리 카드", layout="flashcards")
    assert result.ok, result.data
    assert result.data["cards"] == 10
    assert result.data["pages"] == 4  # (앞면 쪽 + 뒷면 쪽) x 2장(8장씩)
    assert "warnings" not in result.data, result.data["warnings"]
    assert result.preview_png.startswith(PNG_SIGNATURE)

    lines = Path(result.data["tsv_path"]).read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["#separator:tab", "#html:true"]
    assert len(lines) == 12
    assert lines[2] == "카드 1 앞면\t카드 1 뒷면 \\(F_{\\text{마찰}} = \\mu N_1\\)"
    assert lines[-1] == "부등호\t\\(a&lt;b\\) 그리고 x &amp; y"

    def word_x(page, word: str) -> float:
        """그 낱말이 처음 나오는 곳의 왼쪽 x(한글 낱말 사이 공백은 글자로 뽑히지 않을 때가 있어 낱말 단위로 찾는다)."""
        return next(w[0] for w in page.get_text("words") if w[4] == word)

    with pymupdf.open(result.data["path"]) as pdf:
        half = pdf[0].rect.width / 2
        assert word_x(pdf[0], "카드1") < half  # 앞면 쪽: 카드 1은 왼쪽 위
        assert word_x(pdf[1], "카드1") > half  # 뒷면 쪽은 좌우가 뒤집혀 오른쪽 위
        assert word_x(pdf[1], "카드2") < half
        assert word_x(pdf[2], "카드9") < half and word_x(pdf[3], "카드9") > half


@pytest.mark.parametrize(
    ("markdown", "fmt", "fragment"),
    [
        ("## 카드\n\n표가 없는 글\n", "pdf", "No card table"),
        ("| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |\n", "pdf", "3 columns"),
        ("| 앞면 | 뒷면 |\n|---|---|\n| 질문 |  |\n", "pdf", "back is empty"),
        (CARDS, "docx", 'format="pdf"'),
        ("| 앞면 | 뒷면 |\n|---|---|\n| $\\input{x}$ | 답 |\n", "pdf", "\\input"),
        # 2026-11-03 점검: 카드 머리말이 tikz를 불러와서 수식 안의 plot file로 파일을 읽을 수 있었다
        ("| 앞면 | 뒷면 |\n|---|---|\n| $\\def\\f{C:/Users/me/secret.txt}\\tikz\\draw plot file \\f;$ | 답 |\n", "pdf", "plot file"),
    ],
)
def test_flashcards_rule_errors(output_dir, markdown, fmt, fragment):
    result = export_document(markdown, fmt, layout="flashcards")
    assert not result.ok
    assert fragment in " ".join(result.data["errors"])


# ── 프롬프트 ────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("prompt", "argument", "keywords"),
    [
        ("slides", "material", ["export_document", "pptx", "plot_data", "notes"]),
        ("exam", "material", ["verify_math", "check_units", "# 정답과 해설", 'layout="exam"']),
        ("flashcards", "material", ["| 앞면 | 뒷면 |", 'layout="flashcards"', "tsv_path"]),
        ("lab_report", "experiment", ["plot_data", "check_units", "## 고찰", 'format="docx"']),
    ],
)
async def test_study_prompts(prompt, argument, keywords):
    from mcp import Client

    from openstem.server import mcp

    async with Client(mcp) as client:
        listed = {p.name: p for p in (await client.list_prompts()).prompts}
        assert [a.name for a in listed[prompt].arguments] == [argument]
        text = (await client.get_prompt(prompt, {argument: "단진자 주기 측정"})).messages[0].content.text
    assert "단진자 주기 측정" in text
    for keyword in keywords:
        assert keyword in text, keyword


def test_flashcards_math_cannot_read_files_through_split_plot(output_dir, tectonic, tmp_path):
    """수식 칸에서 \\def로 plot 낱말을 나눠 쓰는 우회도 TeX 쪽 방어선(FILE_GUARD)이 막는다. 파일 내용이 에러에 실리면 안 된다."""
    secret = tmp_path / "secret.dat"
    secret.write_text("x y\n1 2\nTOKEN-should-not-leak 5\n", encoding="utf-8")
    path = secret.as_posix()
    cell = "$\\def\\a{p}\\def\\f{" + path + "}\\tikz\\draw \\a lot file\\f;$"
    markdown = "## 카드\n\n| 앞면 | 뒷면 |\n|---|---|\n| " + cell + " | 답 |\n"
    result = export_document(markdown, "pdf", layout="flashcards")
    assert not result.ok
    text = " ".join(result.data.get("errors", []))
    assert "TOKEN-should-not-leak" not in text
    assert "disabled" in text



# ---- 참고문헌(bibliography·citation_style)과 기본 서식(template) ----

BIB = """@article{newton1687,
  author = {Newton, Isaac},
  title = {Philosophi{\\ae} Naturalis Principia Mathematica},
  journal = {Royal Society},
  year = {1687},
  doi = {10.1000/xyz}
}
@book{kim2020,
  author = {김철수},
  title = {일반물리학},
  publisher = {서울대출판부},
  year = {2020}
}
"""
CITED = "## 결과\n\n뉴턴 [@newton1687]에 따르면 낙하 시간은 같다. 같은 결론은 @kim2020 에도 있다.\n"


def _materials(output_dir: Path) -> Path:
    folder = output_dir / "자료"  # OPENSTEM_INPUT_DIR이 없으면 결과물 폴더 안 자료 폴더
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture
def bib(output_dir):
    path = _materials(output_dir) / "refs.bib"
    path.write_text(BIB, encoding="utf-8")
    return path


def _xml_text(path: Path, prefix: str) -> str:
    with zipfile.ZipFile(path) as z:
        return " ".join(z.read(n).decode("utf-8") for n in sorted(z.namelist()) if n.startswith(prefix) and n.endswith(".xml"))


def test_docx_citations_and_reference_list(bib, output_dir):
    result = export_document(CITED, "docx", title="보고서", bibliography="refs.bib", citation_style="apa")
    assert result.ok, result.data
    assert result.data["references"] == 2 and result.data["citations"] == 2
    assert "warnings" not in result.data, result.data["warnings"]
    xml = _xml_text(Path(result.data["path"]), "word/document.xml")
    assert "(Newton, 1687)" in xml and "김철수 (2020)" in xml  # APA 본문 인용
    assert "참고문헌" in xml and "principia" in xml and 'w:val="Bibliography"' in xml
    record = json.loads(Path(result.data["record_path"]).read_text(encoding="utf-8"))
    assert record["inputs"]["bibliography"] == "refs.bib" and record["results"]["references"] == 2


def test_pptx_reference_list_is_its_own_slide(bib, output_dir):
    result = export_document(CITED, "pptx", bibliography=str(bib), citation_style="ieee")
    assert result.ok, result.data
    assert result.data["slides"] == 2  # 결과 + 참고문헌(## 제목으로 바꿔 한 장)
    xml = _xml_text(Path(result.data["path"]), "ppt/slides/")
    assert "[1] I. Newton" in xml and "참고문헌" in xml and "[2] 김철수" in xml


def test_pdf_citations_and_english_heading(output_dir, tectonic):
    path = _materials(output_dir) / "refs.json"  # CSL JSON
    path.write_text(json.dumps([{"id": "doe2020", "type": "book", "title": "Mechanics", "author": [{"family": "Doe", "given": "Jane"}], "issued": {"date-parts": [[2020]]}}]), encoding="utf-8")
    result = export_document("## Summary\n\nFree fall is uniform [@doe2020, p. 3].\n", "pdf", bibliography="refs.json")
    assert result.ok, result.data
    import pymupdf

    text = pymupdf.open(result.data["path"])[0].get_text()
    assert "References" in text and "Mechanics" in text and "Doe" in text and "2020, 3" in text  # Chicago author-date


def test_missing_citation_key_is_a_short_warning(bib, output_dir):
    result = export_document("## A\n\nSee [@nobody; @kim2020].\n", "docx", bibliography="refs.bib")
    assert result.ok, result.data
    warning = " ".join(result.data["warnings"])
    assert "nobody" in warning and "not in the bibliography" in warning
    assert result.data["references"] == 1


def test_citations_without_bibliography_warn(output_dir):
    result = export_document("## A\n\nSee [@kim2020].\n", "docx")
    assert result.ok and "no bibliography" in " ".join(result.data["warnings"])


def test_user_written_references_heading_is_not_repeated(bib, output_dir):
    result = export_document(CITED + "\n# 참고문헌\n", "docx", bibliography="refs.bib")
    assert result.ok, result.data
    texts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", _xml_text(Path(result.data["path"]), "word/document.xml"))
    assert texts.count("참고문헌") == 1


@pytest.mark.parametrize(
    ("make_path", "fragment"),
    [
        (lambda tmp, mat: str(tmp / "outside.bib"), "inside the materials folder"),
        (lambda tmp, mat: "../../outside.bib", "inside the materials folder"),
        (lambda tmp, mat: "없음.bib", "not found"),
        (lambda tmp, mat: "notes.txt", ".bib/.json/.yaml"),
        (lambda tmp, mat: "link.bib", "inside the materials folder"),  # 폴더 밖을 가리키는 심볼릭 링크
    ],
)
def test_bibliography_outside_folder_or_wrong_type_is_rejected(tmp_path, output_dir, make_path, fragment):
    mat = _materials(output_dir)
    (tmp_path / "outside.bib").write_text(BIB, encoding="utf-8")
    (mat / "notes.txt").write_text("x", encoding="utf-8")
    try:
        (mat / "link.bib").symlink_to(tmp_path / "outside.bib")
    except OSError:
        pytest.skip("심볼릭 링크를 만들 수 없다")
    result = export_document(CITED, "docx", bibliography=make_path(tmp_path, mat))
    assert not result.ok
    assert fragment in " ".join(result.data["errors"]), result.data


def test_bibliography_content_goes_through_the_same_checks(output_dir):
    """참고문헌(.yaml은 마크다운)에서 온 그림·수식도 check_ast를 거치고, raw TeX는 글자가 된다."""
    mat = _materials(output_dir)
    (mat / "img.yaml").write_text(
        'references:\n- id: a\n  type: book\n  title: "![x](https://example.com/a.png)"\n  issued: {date-parts: [[2020]]}\n', encoding="utf-8"
    )
    result = export_document("## A\n\n[@a]\n", "docx", bibliography="img.yaml")
    assert not result.ok and "URLs" in " ".join(result.data["errors"])

    (mat / "math.bib").write_text("@book{b, title = {Danger $\\input{secret}$ and \\weird{x}}, year = {2020}}\n", encoding="utf-8")
    result = export_document("## A\n\n[@b]\n", "docx", bibliography="math.bib")
    assert not result.ok and "\\input" in " ".join(result.data["errors"])

    (mat / "raw.bib").write_text("@book{c, title = {Plain \\weird{x}}, year = {2020}}\n", encoding="utf-8")
    result = export_document("## A\n\n[@c]\n", "docx", bibliography="raw.bib")
    assert result.ok and "plain text" in " ".join(result.data["warnings"])


def test_citeproc_runs_in_the_sandboxed_parse_step(bib, output_dir, monkeypatch):
    """인용은 --sandbox 단계에서 작업 폴더에 복사한 파일로만 처리한다(네트워크·다른 파일에 닿지 않는다)."""
    from openstem.tools import export as export_module

    calls = []
    real = export_module._pandoc
    monkeypatch.setattr(export_module, "_pandoc", lambda args, workdir: (calls.append(args), real(args, workdir))[1])
    assert export_document(CITED, "docx", bibliography=str(bib), citation_style="apa").ok
    parse, write = calls
    assert "--sandbox" in parse and "--citeproc" in parse
    assert "--bibliography=refs.bib" in parse and "--csl=style.csl" in parse  # 상대 이름: 작업 폴더 안 복사본
    assert not any("citeproc" in a or "bibliography" in a for a in write)


def test_bundled_csl_styles_are_independent_and_credited():
    """CSL은 부모 서식을 내려받지 않는(독립) 서식만 넣고, 출처·라이선스를 적는다."""
    from openstem.tools.export import CITATION_STYLES, CSL_DIR

    root = Path(__file__).resolve().parents[1]
    third_party = (root / "THIRD_PARTY.md").read_text(encoding="utf-8")
    for style in CITATION_STYLES:
        if style == "chicago":
            continue  # pandoc에 든 기본 서식
        text = (CSL_DIR / f"{style}.csl").read_text(encoding="utf-8")
        assert 'rel="independent-parent"' not in text
        assert "Creative Commons Attribution-ShareAlike 3.0" in text[:600]  # 파일 머리 주석
        assert f"{style}.csl" in third_party


def test_citation_style_and_template_argument_errors(output_dir):
    assert "citation_style" in " ".join(export_document("x", "docx", citation_style="harvard").data["errors"])
    assert "flashcards" in " ".join(export_document("x", "pdf", layout="flashcards", bibliography="a.bib").data["errors"])
    assert "docx or pptx" in " ".join(export_document("x", "pdf", template="a.docx").data["errors"])


def test_default_docx_template_fonts_and_table_style(output_dir):
    result = export_document("# 제목\n\n본문\n\n| 가 | 나 |\n|---|---|\n| 1 | 2 |\n", "docx")
    assert result.ok, result.data
    with zipfile.ZipFile(result.data["path"]) as z:
        styles = z.read("word/styles.xml").decode("utf-8")
        assert 'w:eastAsia="Malgun Gothic"' in styles and 'w:ascii="Malgun Gothic"' in styles
        assert "Aptos" not in styles and "majorHAnsi" not in styles  # 테마 글꼴이 남지 않았다
        assert 'w:eastAsia="ko-KR"' in styles and 'w:fill="DEE6F2"' in styles  # 표 머리 행
        assert re.search(r'<w:pgSz (w:h="16838" w:w="11906"|w:w="11906" w:h="16838")', z.read("word/document.xml").decode("utf-8"))  # A4
        assert "m:oMath" not in styles  # 수식 서식은 건드리지 않는다


def test_default_pptx_template_is_16_9_with_theme(output_dir):
    result = export_document("## 슬라이드\n\n- 내용\n", "pptx")
    assert result.ok, result.data
    with zipfile.ZipFile(result.data["path"]) as z:
        assert 'type="screen16x9"' in z.read("ppt/presentation.xml").decode("utf-8")
        theme = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("ppt/theme/"))
        assert '<a:latin typeface="Malgun Gothic"/>' in theme and "1F3864" in theme


def test_reference_docs_match_the_generator():
    """render/reference.docx·pptx는 refdocs.py로 다시 만들 수 있어야 한다(손으로 고친 파일이 아님)."""
    pypandoc = pytest.importorskip("pypandoc")
    from openstem.render import refdocs

    for path, patch, name in ((refdocs.DOCX_PATH, refdocs.docx_parts, "reference.docx"), (refdocs.PPTX_PATH, refdocs.pptx_parts, "reference.pptx")):
        expected = patch(refdocs._default_data_file(pypandoc.get_pandoc_path(), name))
        with zipfile.ZipFile(path) as z:
            actual = {n: z.read(n) for n in z.namelist()}
        assert actual == expected, f"{name}: run `uv run python -m openstem.render.refdocs`"


def test_reference_docs_generator_ignores_line_endings():
    """Windows의 pandoc 기본 파일은 XML 줄 끝이 CRLF다. 어느 OS에서 만들어도 같은 파일이 나와야 한다."""
    pypandoc = pytest.importorskip("pypandoc")
    from openstem.render import refdocs

    for path, patch, name in ((refdocs.DOCX_PATH, refdocs.docx_parts, "reference.docx"), (refdocs.PPTX_PATH, refdocs.pptx_parts, "reference.pptx")):
        parts = refdocs._default_data_file(pypandoc.get_pandoc_path(), name)
        crlf = {n: refdocs._lf(n, d.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")) for n, d in parts.items()}
        with zipfile.ZipFile(path) as z:
            assert patch(crlf) == {n: z.read(n) for n in z.namelist()}, name
    with pytest.raises(ValueError, match="theme fonts"):
        refdocs.pptx_theme('<a:clrScheme name="x"></a:clrScheme><a:latin typeface="Aptos"/>')


def test_user_template_replaces_default_styles(output_dir):
    """사용자 template(자료 폴더 안)을 쓰면 그 서식이 들어가고, 폴더 밖·가짜 파일은 거절한다."""
    import subprocess

    import pypandoc

    mat = _materials(output_dir)
    mine = mat / "학교양식.docx"
    subprocess.run([pypandoc.get_pandoc_path(), "-o", str(mine), "--print-default-data-file", "reference.docx"], check=True)
    result = export_document("# 제목\n\n본문\n", "docx", template="학교양식.docx")
    assert result.ok, result.data
    with zipfile.ZipFile(result.data["path"]) as z:
        assert "Malgun Gothic" not in z.read("word/styles.xml").decode("utf-8")  # pandoc 기본 양식 그대로

    (mat / "fake.docx").write_text("not a zip", encoding="utf-8")
    assert "not a valid docx" in " ".join(export_document("# A\n", "docx", template="fake.docx").data["errors"])
    assert "inside the materials folder" in " ".join(export_document("# A\n", "docx", template="/etc/passwd.docx").data["errors"])
    assert ".pptx" in " ".join(export_document("## A\n", "pptx", template="학교양식.docx").data["errors"])
