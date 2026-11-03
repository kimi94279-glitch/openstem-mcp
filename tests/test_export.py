"""export_document: 마크다운 → docx/pdf. 그림은 결과물 폴더 안 파일만 들어간다."""

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

