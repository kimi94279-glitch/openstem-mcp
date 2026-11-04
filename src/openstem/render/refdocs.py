"""export_document의 docx·pptx 기본 서식(pandoc --reference-doc)을 만든다.

pandoc 기본 데이터 파일(`pandoc -o ref.docx --print-default-data-file reference.docx`)에서 출발해 XML만 고친다.
결과(render/reference.docx, render/reference.pptx)는 저장소에 넣는다. 서식을 바꾸면 이 파일을 고치고
`uv run python -m openstem.render.refdocs`로 다시 만든다(tests/test_export.py가 글꼴·크기를 확인한다).

- 글꼴: 본문·제목 모두 맑은 고딕(Malgun Gothic). 없는 PC에서는 Word·PowerPoint·LibreOffice가 시스템 글꼴로
  대신한다(fontTable에 한글 charset을 적어 한글 글꼴로 대체되게 한다). 수식(OMML, Cambria Math)과 코드(Consolas)는 그대로.
- docx: A4, 2.5cm 여백, 11pt 본문, 1.3줄 간격, 짙은 남색 굵은 제목(# 아래에 선), 위·아래·머리 행 선이 있는 표,
  참고문헌 내어쓰기.
- pptx: pandoc 기본이 이미 16:9다. 단순한 남색·파랑 색 테마, 왼쪽 정렬 굵은 슬라이드 제목.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RENDER_DIR = Path(__file__).resolve().parent
DOCX_PATH = RENDER_DIR / "reference.docx"
PPTX_PATH = RENDER_DIR / "reference.pptx"

FONT = "Malgun Gothic"
FONT_KO = "맑은 고딕"
NAVY = "1F3864"
ACCENT = "2F5597"
FIXED_DATE = (2026, 11, 1, 0, 0, 0)  # 다시 만들어도 같은 파일이 나오게 zip 시각을 고정한다

_RFONTS = f'<w:rFonts w:ascii="{FONT}" w:eastAsia="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}" />'


def _style(xml: str, style_id: str) -> re.Match[str]:
    m = re.search(rf'<w:style [^>]*w:styleId="{style_id}".*?</w:style>', xml, re.S)
    if not m:
        raise ValueError(f"style {style_id} not in reference.docx")
    return m


def _set_rpr(xml: str, style_id: str, rpr: str) -> str:
    """스타일의 <w:rPr>를 통째로 바꾼다(없으면 넣는다)."""
    m = _style(xml, style_id)
    block = m.group(0)
    if re.search(r"<w:rPr\s*/>|<w:rPr>.*?</w:rPr>", block, re.S):
        block = re.sub(r"<w:rPr\s*/>|<w:rPr>.*?</w:rPr>", f"<w:rPr>{rpr}</w:rPr>", block, count=1, flags=re.S)
    else:
        block = block.replace("</w:style>", f"<w:rPr>{rpr}</w:rPr></w:style>")
    return xml[: m.start()] + block + xml[m.end() :]


def _set_ppr(xml: str, style_id: str, ppr: str) -> str:
    m = _style(xml, style_id)
    block = m.group(0)
    if re.search(r"<w:pPr\s*/>|<w:pPr>.*?</w:pPr>", block, re.S):
        block = re.sub(r"<w:pPr\s*/>|<w:pPr>.*?</w:pPr>", f"<w:pPr>{ppr}</w:pPr>", block, count=1, flags=re.S)
    else:
        block = block.replace("<w:rPr>", f"<w:pPr>{ppr}</w:pPr><w:rPr>", 1)
    return xml[: m.start()] + block + xml[m.end() :]


def _heading_rpr(size: int) -> str:
    return f'{_RFONTS}<w:b /><w:bCs /><w:color w:val="{NAVY}" /><w:sz w:val="{size}" /><w:szCs w:val="{size}" />'


def docx_styles(xml: str) -> str:
    # 테마 글꼴(Aptos 등)을 쓰는 곳은 모두 맑은 고딕으로. 글꼴을 직접 적은 곳(코드의 Consolas)은 그대로 둔다.
    xml = re.sub(r"<w:rFonts\s[^>]*?Theme=\"[^\"]*\"[^>]*/>", _RFONTS, xml, flags=re.S)
    xml = re.sub(r'<w:sz w:val="24" />\s*<w:szCs w:val="24" />', '<w:sz w:val="22" /><w:szCs w:val="22" />', xml, count=1)
    xml = xml.replace('w:eastAsia="zh-CN"', 'w:eastAsia="ko-KR"', 1)
    xml = re.sub(
        r"<w:pPrDefault>.*?</w:pPrDefault>",
        '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="312" w:lineRule="auto" /></w:pPr></w:pPrDefault>',
        xml,
        count=1,
        flags=re.S,
    )
    xml = _set_ppr(xml, "BodyText", '<w:spacing w:before="0" w:after="120" />')
    xml = _set_ppr(xml, "Compact", '<w:spacing w:before="0" w:after="40" />')

    xml = _set_rpr(xml, "Title", _heading_rpr(40))
    xml = _set_rpr(xml, "TitleChar", _heading_rpr(40))
    xml = _set_ppr(xml, "Title", '<w:spacing w:before="0" w:after="240" w:line="240" w:lineRule="auto" /><w:contextualSpacing /><w:jc w:val="center" />')
    for level, size in ((1, 32), (2, 28), (3, 24)):
        xml = _set_rpr(xml, f"Heading{level}", _heading_rpr(size))
        xml = _set_rpr(xml, f"Heading{level}Char", _heading_rpr(size))
    xml = _set_ppr(
        xml,
        "Heading1",
        '<w:keepNext /><w:keepLines /><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" '
        f'w:color="{NAVY}" /></w:pBdr><w:spacing w:before="360" w:after="160" /><w:outlineLvl w:val="0" />',
    )
    for level in (2, 3):
        xml = _set_ppr(
            xml, f"Heading{level}", f'<w:keepNext /><w:keepLines /><w:spacing w:before="240" w:after="80" /><w:outlineLvl w:val="{level - 1}" />'
        )

    # 기울인 한글은 보기 흉해서 그림·표 설명은 작은 회색 글씨로 둔다.
    xml = _set_rpr(xml, "Caption", '<w:color w:val="595959" /><w:sz w:val="20" /><w:szCs w:val="20" />')
    xml = _set_ppr(xml, "Caption", '<w:spacing w:before="60" w:after="200" /><w:jc w:val="center" />')
    xml = _set_rpr(xml, "Hyperlink", f'<w:color w:val="{ACCENT}" />')
    xml = _set_ppr(xml, "Bibliography", '<w:spacing w:after="120" /><w:ind w:left="480" w:hanging="480" />')

    table = _style(xml, "Table")
    border = f'w:sz="8" w:space="0" w:color="{NAVY}"'
    new_table = (
        '<w:style w:type="table" w:default="1" w:styleId="Table"><w:name w:val="Table" /><w:basedOn w:val="TableNormal" />'
        "<w:semiHidden /><w:unhideWhenUsed /><w:qFormat />"
        '<w:pPr><w:spacing w:before="40" w:after="40" w:line="240" w:lineRule="auto" /></w:pPr>'
        '<w:tblPr><w:jc w:val="center" /><w:tblInd w:w="0" w:type="dxa" />'
        f'<w:tblBorders><w:top w:val="single" {border} /><w:bottom w:val="single" {border} />'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFBFBF" /></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="40" w:type="dxa" /><w:left w:w="108" w:type="dxa" />'
        '<w:bottom w:w="40" w:type="dxa" /><w:right w:w="108" w:type="dxa" /></w:tblCellMar></w:tblPr>'
        '<w:tblStylePr w:type="firstRow"><w:rPr><w:b /><w:bCs /></w:rPr>'
        f'<w:tcPr><w:tcBorders><w:bottom w:val="single" {border} /></w:tcBorders>'
        '<w:shd w:val="clear" w:color="auto" w:fill="DEE6F2" /><w:vAlign w:val="bottom" /></w:tcPr></w:tblStylePr>'
        "</w:style>"
    )
    return xml[: table.start()] + new_table + xml[table.end() :]


FONT_TABLE_ENTRY = (
    f'<w:font w:name="{FONT}"><w:altName w:val="{FONT_KO}" /><w:panose1 w:val="020B0503020000020004" />'
    '<w:charset w:val="81" /><w:family w:val="swiss" /><w:pitch w:val="variable" />'
    '<w:sig w:usb0="9000002F" w:usb1="29D77CFB" w:usb2="00000010" w:usb3="00000000" w:csb0="00080001" w:csb1="00000000" /></w:font>'
)
SECTION = (
    '<w:sectPr><w:footnotePr><w:numRestart w:val="eachSect" /></w:footnotePr>'
    '<w:pgSz w:w="11906" w:h="16838" /><w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
    'w:header="709" w:footer="709" w:gutter="0" /></w:sectPr>'
)


def docx_parts(parts: dict[str, bytes]) -> dict[str, bytes]:
    parts = dict(parts)
    parts["word/styles.xml"] = docx_styles(parts["word/styles.xml"].decode("utf-8")).encode("utf-8")
    fonts = parts["word/fontTable.xml"].decode("utf-8")
    parts["word/fontTable.xml"] = fonts.replace("</w:fonts>", FONT_TABLE_ENTRY + "</w:fonts>").encode("utf-8")
    document = parts["word/document.xml"].decode("utf-8")
    parts["word/document.xml"] = re.sub(r"<w:sectPr>.*?</w:sectPr>", SECTION, document, count=1, flags=re.S).encode("utf-8")
    return parts


PPTX_COLORS = (
    '<a:clrScheme name="openstem"><a:dk1><a:srgbClr val="1F2328"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    f'<a:dk2><a:srgbClr val="{NAVY}"/></a:dk2><a:lt2><a:srgbClr val="EEF2F7"/></a:lt2>'
    f'<a:accent1><a:srgbClr val="{ACCENT}"/></a:accent1><a:accent2><a:srgbClr val="2E9BD6"/></a:accent2>'
    '<a:accent3><a:srgbClr val="3BA272"/></a:accent3><a:accent4><a:srgbClr val="E8A33D"/></a:accent4>'
    '<a:accent5><a:srgbClr val="D9534F"/></a:accent5><a:accent6><a:srgbClr val="7E6BC4"/></a:accent6>'
    f'<a:hlink><a:srgbClr val="{ACCENT}"/></a:hlink><a:folHlink><a:srgbClr val="7E6BC4"/></a:folHlink></a:clrScheme>'
)


def pptx_theme(xml: str) -> str:
    xml = re.sub(r"<a:clrScheme .*?</a:clrScheme>", PPTX_COLORS, xml, count=1, flags=re.S)
    xml = xml.replace('<a:latin typeface="Calibri"/><a:ea typeface=""/>', f'<a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/>')
    return xml


def pptx_master(xml: str) -> str:
    # 슬라이드 제목: 가운데 → 왼쪽, 굵게, 남색(tx2 = dk2)
    title = re.search(r"<p:titleStyle>.*?</p:titleStyle>", xml, re.S)
    if not title:
        raise ValueError("titleStyle not in reference.pptx")
    block = title.group(0).replace('algn="ctr"', 'algn="l"', 1)
    block = block.replace('<a:defRPr sz="3300" kern="1200">', '<a:defRPr sz="3000" b="1" kern="1200">', 1)
    block = block.replace('<a:schemeClr val="tx1"/>', '<a:schemeClr val="tx2"/>', 1)
    return xml[: title.start()] + block + xml[title.end() :]


def pptx_parts(parts: dict[str, bytes]) -> dict[str, bytes]:
    parts = dict(parts)
    parts["ppt/theme/theme1.xml"] = pptx_theme(parts["ppt/theme/theme1.xml"].decode("utf-8")).encode("utf-8")
    master = "ppt/slideMasters/slideMaster1.xml"
    parts[master] = pptx_master(parts[master].decode("utf-8")).encode("utf-8")
    return parts


def _default_data_file(pandoc: str, name: str) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / name
        subprocess.run([pandoc, "-o", str(out), "--print-default-data-file", name], capture_output=True, check=True)
        with zipfile.ZipFile(out) as z:
            return {info.filename: z.read(info) for info in z.infolist()}


def _write(parts: dict[str, bytes], path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():  # [Content_Types].xml이 맨 앞(pandoc 기본 파일 순서 그대로)
            z.writestr(zipfile.ZipInfo(name, FIXED_DATE), data, compress_type=zipfile.ZIP_DEFLATED)


def build(pandoc: str | None = None) -> None:
    if pandoc is None:
        import pypandoc

        pandoc = pypandoc.get_pandoc_path()
    _write(docx_parts(_default_data_file(pandoc, "reference.docx")), DOCX_PATH)
    _write(pptx_parts(_default_data_file(pandoc, "reference.pptx")), PPTX_PATH)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"wrote {DOCX_PATH} and {PPTX_PATH}")
