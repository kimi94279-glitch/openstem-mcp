"""export_document(format="hwpx"): 한글(HWPX) 작성기. 만든 파일을 저장소의 HWPX 읽기(workers/documents.py)로 다시 읽는다.

한컴오피스로 열어 보는 시험은 없다(개발 환경에 없다). zip 구조, XML 형식, 글자·표·그림·수식이 들어갔는지를 본다.
"""

import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
import pytest

from openstem.render import hwpx
from openstem.tools.export import export_document
from openstem.tools.tikz import render_tikz
from openstem.workers.documents import _hwpx as read_hwpx

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
REQUIRED = (
    "mimetype",
    "version.xml",
    "settings.xml",
    "META-INF/container.xml",
    "META-INF/manifest.xml",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
    "Preview/PrvText.txt",
)

MARKDOWN = """## 자유낙하 & <특수> "문자"

높이 $h$에서 **굵게** *기울임* 떨어진 물체: $t = \\sqrt{\\frac{2h}{g}}$, $\\alpha_i^2 \\le \\pi$.

- 첫째 항목
- 둘째 항목
  1. 하위 번호 하나
  2. 하위 번호 둘

### 표

| 양 | 단위 |
|---|---|
| 길이 | $\\mathrm{m}$ |
| 시간 | 초 |

끝 문단 A&B <c>.
"""


def make_png(path: Path, width: int = 120, height: int = 60, dpi: int = 96) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(200)
    pix.set_dpi(dpi, dpi)
    pix.save(path)
    return path


def export(markdown: str, **kwargs):
    result = export_document(markdown, "hwpx", **kwargs)
    assert result.ok, result.data
    return result, Path(result.data["path"])


def section(path: Path) -> ElementTree.Element:
    with zipfile.ZipFile(path) as zf:
        return ElementTree.fromstring(zf.read("Contents/section0.xml"))


def scripts(path: Path) -> list[str]:
    return [e.text or "" for e in section(path).iter(f"{HP}script")]


# ── zip 구조·XML ────────────────────────────────────────────────────


def test_package_structure_and_wellformed_xml(output_dir):
    _result, path = export(MARKDOWN, title="역학 <정리>")
    assert path.suffix == ".hwpx" and path.parent == output_dir
    with zipfile.ZipFile(path) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/hwp+zip"
        names = zf.namelist()
        assert all(name in names for name in REQUIRED), names
        for name in names:
            if name.endswith((".xml", ".hpf", ".rdf")):
                ElementTree.fromstring(zf.read(name))  # 잘 형성된 XML
        container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
        assert "Contents/content.hpf" in [e.get("full-path") for e in container.iter() if e.get("full-path")]
        hpf = zf.read("Contents/content.hpf").decode("utf-8")
        assert "Contents/section0.xml" in hpf and "Contents/header.xml" in hpf
        assert "역학 &lt;정리&gt;" in hpf
        header = zf.read("Contents/header.xml").decode("utf-8")
        assert "함초롬바탕" in header and "함초롬돋움" in header
        assert "자유낙하" in zf.read("Preview/PrvText.txt").decode("utf-8")


def test_text_reads_back_with_hangul_and_escapes(output_dir):
    _result, path = export(MARKDOWN, title="역학 <정리>")
    text = read_hwpx(path)
    for expected in ("역학 <정리>", "자유낙하 & <특수> “문자”", "굵게", "기울임", "첫째 항목", "하위 번호 둘", "길이", "초", "끝 문단 A&B <c>."):
        assert expected in text, (expected, text)
    raw = zipfile.ZipFile(path).read("Contents/section0.xml").decode("utf-8")
    assert "A&amp;B &lt;c&gt;" in raw


def test_headings_lists_and_table_structure(output_dir):
    result, path = export(MARKDOWN)
    root = section(path)
    tables = list(root.iter(f"{HP}tbl"))
    assert len(tables) == 1 and tables[0].get("rowCnt") == "3" and tables[0].get("colCnt") == "2"
    assert len(list(tables[0].iter(f"{HP}tc"))) == 6
    assert all(tc.get("header") == "1" for tc in next(tables[0].iter(f"{HP}tr")).iter(f"{HP}tc"))

    with zipfile.ZipFile(path) as zf:
        header = ElementTree.fromstring(zf.read("Contents/header.xml"))
    para_prs = {p.get("id"): p.find(f"{HH}heading") for p in header.iter(f"{HH}paraPr")}
    used = [para_prs[p.get("paraPrIDRef")] for p in root.iter(f"{HP}p")]
    kinds = [(h.get("type"), h.get("level")) for h in used]
    assert ("BULLET", "0") in kinds and ("NUMBER", "0") in kinds
    # 제목(##, ###)은 스타일 "개요 2·3"이지만 개요 번호를 걸지 않는다: 읽는 프로그램이 "1. 1. 목적"처럼 번호를 붙이지 않게
    assert "OUTLINE" not in {kind for kind, _ in kinds}
    heading_paras = [p for p in root.iter(f"{HP}p") if p.get("styleIDRef") in ("2", "3")]
    assert {p.get("styleIDRef") for p in heading_paras} == {"2", "3"}
    assert all(para_prs[p.get("paraPrIDRef")].get("type") == "NONE" for p in heading_paras)
    styles = {s.get("id"): s.get("name") for s in header.iter(f"{HH}style")}
    assert styles["2"] == "개요 2"
    # 글자 모양: 굵게·기울임이 header에 있다
    assert header.find(f".//{HH}charPr/{HH}bold") is not None and header.find(f".//{HH}charPr/{HH}italic") is not None
    # 모든 IDRef가 header에 있는 모양을 가리킨다
    char_ids = {c.get("id") for c in header.iter(f"{HH}charPr")}
    assert {r.get("charPrIDRef") for r in root.iter(f"{HP}run")} <= char_ids
    assert result.data["equations"] == 4 and result.data["math_images"] == 0
    assert "4 formula(s) became editable Hangul equations" in result.data["warnings"][0]
    assert "Hangul" in result.data["note"]


def test_simple_math_becomes_hangul_equations(output_dir):
    _result, path = export("$x^2 + y_1 = \\frac{a}{b}$ 그리고 $\\sqrt[3]{x}$, $\\Delta \\theta \\times 2$, $v = \\text{평균 속도}$")
    assert scripts(path) == ["x ^{2} + y _{1} = {{a} over {b}}", "{root {3} of {x}}", "DELTA theta times 2", 'v = "평균 속도"']
    eq = next(section(path).iter(f"{HP}equation"))
    assert eq.find(f"{HP}sz") is not None and eq.find(f"{HP}pos").get("treatAsChar") == "1"


@pytest.mark.parametrize(
    ("tex", "script"),
    [
        ("\\frac{1}{2}", "{{1} over {2}}"),
        ("e^{-x^2}", "e ^{- x ^{2}}"),
        ("a_{n+1}", "a _{n + 1}"),
        ("\\sqrt{2}", "{sqrt {2}}"),
        ("\\alpha + \\beta", "alpha + beta"),
        ("\\Omega", "OMEGA"),
        ("pi", "p i"),  # 글자 p·i는 원주율 예약어(pi)가 아니다
        ("9.8\\,\\mathrm{m/s}", '9.8 ` "m/s"'),  # 글자만이 아닌 단위는 따옴표 글자(바로 선 글자)로
        ("9.8\\,\\mathrm{kg}", "9.8 ` {rm k g}"),
        ("\\left( x \\right)", "left ( x right )"),
        ("F = ma", "F = m a"),
        ("\\sin x \\le 1", "sin x <= 1"),
    ],
)
def test_latex_to_hwpeq(tex, script):
    eq = hwpx.latex_to_hwpeq(tex)
    if script is None:
        assert eq is None
    else:
        assert eq is not None and eq.script == script
        assert eq.box.width > 0 and eq.box.height > 0


@pytest.mark.parametrize(
    "tex",
    ["\\sum_{i=1}^n i", "\\int_0^1 x\\,dx", "\\begin{pmatrix}1\\end{pmatrix}", "\\vec{F}", "x'", "a \\\\ b", "\\frac{1}", "x^2^3", "9.8\\,\\mathrm{m/s^2}", "×"],
)
def test_complex_math_falls_back_to_image(tex):
    assert hwpx.latex_to_hwpeq(tex) is None


def test_raster_image_is_embedded(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    make_png(output_dir / "photo.png", 1200, 600, dpi=96)  # 원래 크기 12.5 인치 → 글 폭(150 mm)으로 줄인다
    _result, path = export("사진:\n\n![실험 장치](photo.png){width=50%}\n\n뒤 문단\n")
    with zipfile.ZipFile(path) as zf:
        assert "BinData/image1.png" in zf.namelist()
        assert zf.read("BinData/image1.png").startswith(b"\x89PNG")
        hpf = zf.read("Contents/content.hpf").decode("utf-8")
        assert 'href="BinData/image1.png"' in hpf and 'id="image1"' in hpf
    pics = list(section(path).iter(f"{HP}pic"))
    assert len(pics) == 1
    assert pics[0].find(f"{HC}img").get("binaryItemIDRef") == "image1"
    assert int(pics[0].find(f"{HP}sz").get("width")) == hwpx.TEXT_WIDTH // 2
    assert "실험 장치" in read_hwpx(path)
    # 그림 문단은 설명 문단과 같은 쪽에 둔다(keepWithNext)
    with zipfile.ZipFile(path) as zf:
        header = ElementTree.fromstring(zf.read("Contents/header.xml"))
    keep = {p.get("id"): p.find(f"{HH}breakSetting").get("keepWithNext") for p in header.iter(f"{HH}paraPr")}
    pic_para = next(p for p in section(path).iter(f"{HP}p") if list(p.iter(f"{HP}pic")))
    assert keep[pic_para.get("paraPrIDRef")] == "1"


def test_page_break_and_footnote(output_dir):
    _result, path = export("앞 쪽\n\n\\newpage\n\n뒤 쪽[^n]\n\n[^n]: 각주 글.\n")
    paras = list(section(path).iter(f"{HP}p"))
    breaks = [p for p in paras if p.get("pageBreak") == "1"]
    assert len(breaks) == 1 and "뒤 쪽" in "".join(t.text or "" for t in breaks[0].iter(f"{HP}t"))
    text = read_hwpx(path)
    assert "\\newpage" not in text and "1) 각주 글." in text


def test_citations_and_reference_list(output_dir):
    materials = output_dir / "자료"  # OPENSTEM_INPUT_DIR이 없으면 결과물 폴더 안 자료 폴더
    materials.mkdir(parents=True, exist_ok=True)
    (materials / "refs.bib").write_text(
        "@article{kim2020,\n  author = {Kim, Minsu and Lee, Jiwon},\n  title = {Damped oscillation in student labs},\n"
        "  journal = {Journal of Physics Education},\n  volume = {12},\n  pages = {1--10},\n  year = {2020}\n}\n",
        encoding="utf-8",
    )
    result, path = export("## 결과\n\n감쇠 진동은 선행 연구와 같다 [@kim2020].\n", title="보고서", bibliography="refs.bib")
    assert result.data["references"] == 1 and result.data["citations"] == 1
    text = read_hwpx(path)
    assert "(Kim and Lee 2020)" in text  # 본문 인용
    assert "참고문헌" in text and "Journal of Physics Education" in text  # 참고문헌 목록 제목과 항목
    italic = [r for r in section(path).iter(f"{HP}run") if "Journal of Physics Education" in "".join(t.text or "" for t in r.iter(f"{HP}t"))]
    assert italic and italic[0].get("charPrIDRef") != "0"  # 학술지 이름은 기울임(본문과 다른 글자 모양)


def test_xml_text_strips_control_characters():
    assert hwpx.xml_text("a\x01b\x0bc & <d>\t\n") == "abc &amp; &lt;d&gt;\t\n"
    assert hwpx.xml_attr('"q"') == "&quot;q&quot;"


def test_image_size_png_and_jpeg(tmp_path):
    png = make_png(tmp_path / "a.png", 300, 150, dpi=300)
    assert hwpx.image_size(png) == (300, 150, pytest.approx(300, abs=1), pytest.approx(300, abs=1))
    pic = hwpx.picture_from_file(png)
    assert pic.width == pytest.approx(7200, abs=30) and pic.height == pytest.approx(3600, abs=30)
    jpg = tmp_path / "b.jpg"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pix.clear_with(128)
    jpg.write_bytes(pix.tobytes("jpeg"))
    width, height, *_ = hwpx.image_size(jpg)
    assert (width, height) == (40, 20)


@pytest.mark.parametrize(("layout", "fragment"), [("cheatsheet", "document or exam"), ("flashcards", "document or exam")])
def test_unsupported_layouts_are_refused(layout, fragment):
    result = export_document("## 공식\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", "hwpx", layout=layout)
    assert not result.ok and fragment in " ".join(result.data["errors"])


def test_exam_layout(output_dir):
    md = "## 문제 1 (5점) {space=2}\n\n$x^2$을 미분하라.\n\n## 문제 2 (5점)\n\n적분하라.\n\n# 정답과 해설\n\n## 문제 1\n\n$2x$\n\n## 문제 2\n\n풀이\n"
    result, path = export(md, title="중간고사", layout="exam")
    assert result.data["layout"] == "exam" and result.data["problems"] == 2
    paras = list(section(path).iter(f"{HP}p"))
    text = read_hwpx(path)
    assert "학번:" in text and "이름:" in text
    answers = [p for p in paras if p.get("pageBreak") == "1"]
    assert len(answers) == 1 and "정답과 해설" in "".join(t.text or "" for t in answers[0].iter(f"{HP}t"))
    # 풀이 공간: 문단 위 간격을 준 빈 문단. 한글이 저장하는 꼴(글자 없는 run)이어야 다른 프로그램도 건너뛰지 않는다
    with zipfile.ZipFile(path) as zf:
        header = ElementTree.fromstring(zf.read("Contents/header.xml"))
    prev = {p.get("id"): int(p.find(f"{HH}margin/{HC}prev").get("value")) for p in header.iter(f"{HH}paraPr")}
    spacers = [p for p in paras if not "".join(t.text or "" for t in p.iter(f"{HP}t")) and prev[p.get("paraPrIDRef")] > 0]
    assert [round(prev[p.get("paraPrIDRef")] / hwpx.HWPUNIT_PER_CM, 1) for p in spacers] == [2.0, 4.0]
    assert all(len(p) == 1 and len(p[0]) == 0 for p in spacers)  # <hp:run charPrIDRef="…"/>


def test_empty_paragraphs_have_no_empty_text_element(output_dir):
    """빈 <hp:t/>가 든 문단은 LibreOffice(H2Orestart)가 건너뛴다. 빈 문단·빈 표 칸은 글자 없는 run으로 쓴다."""
    _result, path = export("앞\n\n| a | |\n|---|---|\n| | b |\n\n- \n\n뒤\n")
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")
    assert "<hp:t/>" not in xml and "<hp:t></hp:t>" not in xml
    cells = list(section(path).iter(f"{HP}tc"))
    assert len(cells) == 4 and all(list(c.iter(f"{HP}p")) for c in cells)  # 빈 칸에도 문단이 있다


# ── TeX이 필요한 시험: 그림으로 넣는 수식, 벡터 그림 ─────────────────


def test_complex_math_and_vector_figure(output_dir, tectonic):
    figure = render_tikz("\\draw[->] (0,0) -- (2,1) node[right] {힘 $F$};", name="force")
    assert figure.ok, figure.data
    md = (
        "## 급수\n\n"
        "$$\\sum_{n=1}^\\infty \\frac{1}{n^2} = \\frac{\\pi^2}{6}$$\n\n"
        "간단한 식 $E = mc^2$와 적분 $\\int_0^1 x\\,dx$, 한글 수식 $\\sum_k \\text{점수}_k$.\n\n"
        f"![힘의 방향]({figure.data['svg_path']})\n"
    )
    result, path = export(md, title="그림과 수식")
    assert result.data["math_images"] == 3 and result.data["equations"] == 1
    assert result.data["images"] == 1
    assert "3 were inserted as images" in result.data["warnings"][0]
    with zipfile.ZipFile(path) as zf:
        bins = [n for n in zf.namelist() if n.startswith("BinData/")]
        assert len(bins) == 4 and all(zf.read(n).startswith(b"\x89PNG") for n in bins)
    pics = list(section(path).iter(f"{HP}pic"))
    assert len(pics) == 4
    assert scripts(path) == ["E = m c ^{2}"]
    assert "힘의 방향" in read_hwpx(path)


def test_bad_complex_math_reports_tex_error(output_dir, tectonic):
    result = export_document("값은 $\\sum \\fracc{1}{2}$ 이다.", "hwpx")
    assert not result.ok
    assert any(e.startswith("! Undefined control sequence") for e in result.data["errors"])
