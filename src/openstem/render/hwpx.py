"""export_document의 한글(HWPX) 작성기: 검사를 마친 pandoc 문서 구조(JSON)를 OWPML zip으로 쓴다.

표준 라이브러리(zipfile, 문자열 XML)만 쓴다. 서버 프로세스에서 돌고 외부 입력을 해석하지 않는다:
수식·그림은 export.check_ast가 이미 검사했고, 그림은 export가 작업 폴더에 PNG로 옮겨 둔 것만 넣는다.

규격 참고(코드·빈 문서 템플릿을 가져오지 않고 직접 썼다. THIRD_PARTY.md):
- 한글과컴퓨터 OWPML 공개 규격(KS X 6101)의 패키지 구조·요소 이름
- python-hwpx(Apache-2.0, airmang)가 읽고 쓰는 HWPX의 요소 순서(그림·표·수식·글자 모양)를 관찰만 했다

수식은 두 가지로 넣는다.
- 단순한 수식(분수·지수·첨자·근호·그리스 문자·기본 연산자)은 한컴 수식 객체(hp:equation, 수식 스크립트)로 바꾼다.
  `latex_to_hwpeq`가 모르는 명령이 하나라도 있으면 None을 돌려주고, 그 수식은 그림이 된다.
- 나머지는 `render_math_images`가 latex.compile_tex(texcheck를 마친 수식 + HANGUL_SETUP + FILE_GUARD)로 한 PDF에
  한 쪽씩 컴파일하고, 이 모듈의 작업자 진입점(`python -m openstem.render.hwpx`, pymupdf)이 쪽마다 PNG로 바꾼다.
  서버는 pymupdf를 import하지 않는다(작업자 함수 안에서만 import한다).

한컴오피스로 열어 확인하지 못한 채 만들었다(개발 환경에 한컴오피스가 없다). 저장소의 HWPX 읽기와 XML 검사로만 시험했다.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import re
import struct
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openstem import __version__

MIMETYPE = "application/hwp+zip"

# ── 단위·쪽 설정 ────────────────────────────────────────────────────
# HWPUNIT: 1/7200 인치. 1pt = 100, 1mm ≈ 283.46, 1cm ≈ 2834.6
HWPUNIT_PER_INCH = 7200
HWPUNIT_PER_CM = HWPUNIT_PER_INCH / 2.54
PAGE_WIDTH, PAGE_HEIGHT = 59528, 84186  # A4
MARGIN_LEFT = MARGIN_RIGHT = 8504  # 30 mm(한글 기본)
MARGIN_TOP, MARGIN_BOTTOM, MARGIN_HEADER, MARGIN_FOOTER = 5668, 4252, 4252, 4252
TEXT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT  # 42520 = 150 mm
TEXT_HEIGHT = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM - MARGIN_HEADER - MARGIN_FOOTER
LIST_INDENT = 2000  # 목록 한 단계 들여쓰기
QUOTE_INDENT = 2000
DEFAULT_IMAGE_DPI = 96

# 한컴 기본 글꼴 이름(글꼴 파일을 넣지 않고 이름만 적는다. 없는 PC에서는 한글이 대체 글꼴을 쓴다)
FONT_BATANG = "함초롬바탕"
FONT_DOTUM = "함초롬돋움"
BODY_FONT, HEADING_FONT = 0, 1  # 아래 FONTS의 순서
FONTS = ((FONT_BATANG, "FCAT_MYUNGJO"), (FONT_DOTUM, "FCAT_GOTHIC"))
LANGS = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")

BODY_SIZE = 1000  # 10pt
TITLE_SIZE = 2000
HEADING_SIZES = {1: 1600, 2: 1400, 3: 1200, 4: 1100, 5: 1050, 6: 1000}

# OWPML 네임스페이스(규격의 이름공간 URI). 모든 XML 뿌리에 같은 목록을 둔다.
NAMESPACES = {
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf/",
    "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
    "hwpunitchar": "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar",
    "epub": "http://www.idpf.org/2007/ops",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
}
_XMLNS = " ".join(f'xmlns:{k}="{v}"' for k, v in NAMESPACES.items())
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'

# XML 1.0에 쓸 수 없는 글자(제어 문자, 짝 없는 서로게이트, U+FFFE·FFFF)
_XML_BAD_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def xml_text(text: str) -> str:
    """글자를 XML 본문용으로: 쓸 수 없는 제어 문자를 빼고 &, <, >를 이스케이프한다."""
    text = _XML_BAD_RE.sub("", text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def xml_attr(value: object) -> str:
    return xml_text(str(value)).replace('"', "&quot;")


def _attrs(pairs: dict[str, object]) -> str:
    return "".join(f' {k}="{xml_attr(v)}"' for k, v in pairs.items())


# ── 글자 모양·문단 모양 ────────────────────────────────────────────


@dataclass(frozen=True)
class CharStyle:
    height: int = BODY_SIZE
    font: int = BODY_FONT
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    sup: bool = False
    sub: bool = False
    color: str = "#000000"


@dataclass(frozen=True)
class ParaStyle:
    align: str = "JUSTIFY"  # JUSTIFY | LEFT | RIGHT | CENTER
    left: int = 0
    indent: int = 0
    prev: int = 0  # 문단 위 간격
    next: int = 0  # 문단 아래 간격
    heading: tuple[str, int, int] = ("NONE", 0, 0)  # (type, idRef, level)
    keep_next: bool = False
    line: int = 160  # 줄 간격(%)


PLAIN_CHAR = CharStyle()
PLAIN_PARA = ParaStyle()


@dataclass
class _Numbering:
    """번호 목록 하나(목록마다 따로 두어 번호가 1부터 다시 시작한다)."""

    start: int
    fmt: str  # DIGIT, LATIN_SMALL, ...
    text: str  # "^1." 같은 꼴


class Registry:
    """header.xml에 들어갈 글자·문단 모양과 번호 매기기를 모은다. 같은 모양은 같은 id를 쓴다."""

    OUTLINE_ID = 1  # 개요(제목) 번호: 글자를 비워 번호가 보이지 않게 한다
    BULLET_CHARS = ("•", "◦", "▪")

    def __init__(self) -> None:
        self.chars: dict[CharStyle, int] = {}
        self.paras: dict[ParaStyle, int] = {}
        self.numberings: list[_Numbering] = []
        self.char(CharStyle())  # id 0: 바탕글
        self.para(ParaStyle())
        # 제목 스타일(개요 1~6)의 모양을 먼저 등록해 둔다
        self.heading_ids = {level: (self.para(heading_para(level)), self.char(heading_char(level))) for level in HEADING_SIZES}

    def char(self, style: CharStyle) -> int:
        return self.chars.setdefault(style, len(self.chars))

    def para(self, style: ParaStyle) -> int:
        return self.paras.setdefault(style, len(self.paras))

    def numbering(self, start: int, fmt: str, text: str) -> int:
        self.numberings.append(_Numbering(start, fmt, text))
        return self.OUTLINE_ID + len(self.numberings)

    def bullet_id(self, depth: int) -> int:
        return 1 + depth % len(self.BULLET_CHARS)


def heading_para(level: int) -> ParaStyle:
    prev = {1: 1400, 2: 1000, 3: 800}.get(level, 600)
    return ParaStyle(align="LEFT", prev=prev, next=400, heading=("OUTLINE", Registry.OUTLINE_ID, level - 1), keep_next=True)


def heading_char(level: int) -> CharStyle:
    return CharStyle(height=HEADING_SIZES.get(level, BODY_SIZE), font=HEADING_FONT, bold=True)


def _lang_attrs(value: object) -> str:
    return _attrs({lang.lower(): value for lang in LANGS})


def _char_xml(style: CharStyle, cid: int) -> str:
    parts = [
        (
            f'<hh:charPr id="{cid}" height="{style.height}" textColor="{style.color}" shadeColor="none" useFontSpace="0" '
            'useKerning="0" symMark="NONE" borderFillIDRef="2">'
        ),
        f"<hh:fontRef{_lang_attrs(style.font)}/>",
        f"<hh:ratio{_lang_attrs(100)}/>",
        f"<hh:spacing{_lang_attrs(0)}/>",
        f"<hh:relSz{_lang_attrs(100)}/>",
        f"<hh:offset{_lang_attrs(0)}/>",
    ]
    if style.italic:
        parts.append("<hh:italic/>")
    if style.bold:
        parts.append("<hh:bold/>")
    parts.append(f'<hh:underline type="{"BOTTOM" if style.underline else "NONE"}" shape="SOLID" color="#000000"/>')
    parts.append(f'<hh:strikeout shape="{"SOLID" if style.strike else "NONE"}" color="#000000"/>')
    parts.append('<hh:outline type="NONE"/>')
    parts.append('<hh:shadow type="NONE" color="#C0C0C0" offsetX="10" offsetY="10"/>')
    if style.sup:
        parts.append("<hh:supscript/>")
    elif style.sub:
        parts.append("<hh:subscript/>")
    parts.append("</hh:charPr>")
    return "".join(parts)


def _para_xml(style: ParaStyle, pid: int) -> str:
    kind, ref, level = style.heading
    return (
        f'<hh:paraPr id="{pid}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" suppressLineNumbers="0" '
        'checked="0" textDir="LTR">'
        f'<hh:align horizontal="{style.align}" vertical="BASELINE"/>'
        f'<hh:heading type="{kind}" idRef="{ref}" level="{level}"/>'
        '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD" widowOrphan="0" '
        f'keepWithNext="{int(style.keep_next)}" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
        '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
        "<hh:margin>"
        f'<hc:intent value="{style.indent}" unit="HWPUNIT"/>'
        f'<hc:left value="{style.left}" unit="HWPUNIT"/>'
        '<hc:right value="0" unit="HWPUNIT"/>'
        f'<hc:prev value="{style.prev}" unit="HWPUNIT"/>'
        f'<hc:next value="{style.next}" unit="HWPUNIT"/>'
        "</hh:margin>"
        f'<hh:lineSpacing type="PERCENT" value="{style.line}" unit="HWPUNIT"/>'
        '<hh:border borderFillIDRef="2" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" connect="0" ignoreMargin="0"/>'
        "</hh:paraPr>"
    )


def _para_head(level: int, start: int, fmt: str, text: str) -> str:
    attrs = (
        f'start="{start}" level="{level}" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" '
        f'textOffsetType="PERCENT" textOffset="50" numFormat="{fmt}" charPrIDRef="4294967295" checkable="0"'
    )
    return f"<hh:paraHead {attrs}>{xml_text(text)}</hh:paraHead>" if text else f"<hh:paraHead {attrs}/>"


def _border(kind: str) -> str:
    width = "0.12 mm" if kind == "SOLID" else "0.1 mm"
    return "".join(f'<hh:{side}Border type="{kind}" width="{width}" color="#000000"/>' for side in ("left", "right", "top", "bottom"))


def _border_fill(bid: int, kind: str, brush: bool) -> str:
    fill = '<hc:fillBrush><hc:winBrush faceColor="none" hatchColor="#999999" alpha="0"/></hc:fillBrush>' if brush else ""
    return (
        f'<hh:borderFill id="{bid}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
        '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
        f'{_border(kind)}<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>{fill}</hh:borderFill>'
    )


TABLE_BORDER_FILL = 3


def header_xml(reg: Registry) -> str:
    fonts = "".join(
        f'<hh:fontface lang="{lang}" fontCnt="{len(FONTS)}">'
        + "".join(
            f'<hh:font id="{i}" face="{xml_attr(face)}" type="TTF" isEmbedded="0">'
            f'<hh:typeInfo familyType="{family}" weight="6" proportion="4" contrast="0" strokeVariation="1" armStyle="1" '
            'letterform="1" midline="1" xHeight="1"/></hh:font>'
            for i, (face, family) in enumerate(FONTS)
        )
        + "</hh:fontface>"
        for lang in LANGS
    )
    border_fills = _border_fill(1, "NONE", False) + _border_fill(2, "NONE", True) + _border_fill(TABLE_BORDER_FILL, "SOLID", True)
    chars = "".join(_char_xml(style, cid) for style, cid in reg.chars.items())
    paras = "".join(_para_xml(style, pid) for style, pid in reg.paras.items())
    outline = f'<hh:numbering id="{Registry.OUTLINE_ID}" start="0">' + "".join(_para_head(lv, 1, "DIGIT", "") for lv in range(1, 11)) + "</hh:numbering>"
    numberings = outline + "".join(
        f'<hh:numbering id="{Registry.OUTLINE_ID + 1 + i}" start="0">'
        + "".join(_para_head(lv, n.start if lv == 1 else 1, n.fmt if lv == 1 else "DIGIT", n.text if lv == 1 else "") for lv in range(1, 11))
        + "</hh:numbering>"
        for i, n in enumerate(reg.numberings)
    )
    bullets = "".join(
        f'<hh:bullet id="{i + 1}" char="{xml_attr(ch)}" useImage="0">'
        '<hh:paraHead level="0" align="LEFT" useInstWidth="0" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" '
        'textOffset="50" numFormat="DIGIT" charPrIDRef="4294967295" checkable="0"/></hh:bullet>'
        for i, ch in enumerate(Registry.BULLET_CHARS)
    )
    styles = ['<hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langID="1042" lockForm="0"/>']
    for level, (pid, cid) in reg.heading_ids.items():
        styles.append(
            f'<hh:style id="{level}" type="PARA" name="개요 {level}" engName="Outline {level}" paraPrIDRef="{pid}" '
            f'charPrIDRef="{cid}" nextStyleIDRef="0" langID="1042" lockForm="0"/>'
        )
    return (
        XML_DECL + f'<hh:head {_XMLNS} version="1.4" secCnt="1">'
        '<hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>'
        "<hh:refList>"
        f'<hh:fontfaces itemCnt="{len(LANGS)}">{fonts}</hh:fontfaces>'
        f'<hh:borderFills itemCnt="3">{border_fills}</hh:borderFills>'
        f'<hh:charProperties itemCnt="{len(reg.chars)}">{chars}</hh:charProperties>'
        '<hh:tabProperties itemCnt="1"><hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/></hh:tabProperties>'
        f'<hh:numberings itemCnt="{1 + len(reg.numberings)}">{numberings}</hh:numberings>'
        f'<hh:bullets itemCnt="{len(Registry.BULLET_CHARS)}">{bullets}</hh:bullets>'
        f'<hh:paraProperties itemCnt="{len(reg.paras)}">{paras}</hh:paraProperties>'
        f'<hh:styles itemCnt="{len(styles)}">{"".join(styles)}</hh:styles>'
        "</hh:refList>"
        '<hh:compatibleDocument targetProgram="HWP201X"><hh:layoutCompatibility/></hh:compatibleDocument>'
        '<hh:docOption><hh:linkinfo path="" pageInherit="0" footnoteInherit="0"/></hh:docOption>'
        "</hh:head>"
    )


# ── LaTeX → 한컴 수식 스크립트(단순한 경우만) ───────────────────────


@dataclass
class EqBox:
    """수식 상자 크기 어림(HWPUNIT, 10pt 기준). 한글이 수식을 다시 그리지만 처음 줄 배치에 쓴다."""

    width: float
    ascent: float
    descent: float

    @property
    def height(self) -> float:
        return self.ascent + self.descent


@dataclass
class Equation:
    script: str
    box: EqBox


_GREEK = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega"]
_GREEK_MAP = {g: g for g in _GREEK} | {"varepsilon": "epsilon", "varphi": "phi", "vartheta": "theta"}
_GREEK_UPPER = {g.capitalize(): g.upper() for g in (["gamma", "delta", "theta", "lambda", "xi", "pi", "sigma", "upsilon", "phi", "psi", "omega"])}
_SYMBOLS = {
    "times": "times", "cdot": "cdot", "pm": "+-", "mp": "-+", "le": "<=", "leq": "<=", "ge": ">=", "geq": ">=",
    "neq": "!=", "ne": "!=", "approx": "approx", "sim": "sim", "equiv": "equiv", "infty": "inf", "to": "->",
    "rightarrow": "->", "partial": "partial", "nabla": "nabla", "propto": "propto", "div": "div", "cdots": "cdots",
    "ldots": "ldots", "circ": "circ",
}  # fmt: skip
_FUNCTIONS = {"sin", "cos", "tan", "sec", "csc", "cot", "sinh", "cosh", "tanh", "log", "ln", "exp"}
_SPACES = {",": "`", ";": "~", ":": "~", " ": "~", "quad": "~~", "qquad": "~~~~", "!": ""}
_PLAIN_CHARS = set("+-=<>()[],./|!:")
_DELIMS = {"(": "(", ")": ")", "[": "[", "]": "]", "|": "|", ".": "", "\\{": "lbrace", "\\}": "rbrace"}
_TOKEN_RE = re.compile(r"\\[A-Za-z]+|\\.|[0-9]+(?:\.[0-9]+)?|\s+|.", re.DOTALL)
MAX_EQ_SCRIPT = 400  # 이보다 길면 그림으로(수식 상자 어림이 크게 틀리기 쉽다)


class _Unsupported(Exception):
    pass


# 글자 상자 크기(10pt): 높이는 한 줄 높이를 위·아래로 나눈 값
_ASC, _DESC = 720.0, 260.0
_SCRIPT_SCALE = 0.7


def _leaf(width: float) -> EqBox:
    return EqBox(width, _ASC, _DESC)


def _row(boxes: list[EqBox]) -> EqBox:
    if not boxes:
        return EqBox(0, _ASC, _DESC)
    return EqBox(sum(b.width for b in boxes), max(b.ascent for b in boxes), max(b.descent for b in boxes))


class _EqParser:
    """LaTeX 수식의 작은 부분집합을 한컴 수식 스크립트로 바꾼다. 모르는 것을 만나면 _Unsupported."""

    def __init__(self, tex: str) -> None:
        self.tokens = _TOKEN_RE.findall(tex)  # 빈칸도 남긴다(\\text{} 안에서는 글자다)
        self.i = 0

    def _skip_space(self) -> None:
        while self.i < len(self.tokens) and self.tokens[self.i].isspace():
            self.i += 1

    def peek(self) -> str | None:
        self._skip_space()
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, raw: bool = False) -> str:
        if not raw:
            self._skip_space()
        if self.i >= len(self.tokens):
            raise _Unsupported("unexpected end")
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def parse(self) -> tuple[str, EqBox]:
        script, box = self.row(stop=None)
        if self.peek() is not None:
            raise _Unsupported("trailing tokens")
        return script, box

    def row(self, stop: str | None) -> tuple[str, EqBox]:
        parts: list[str] = []
        boxes: list[EqBox] = []
        while (tok := self.peek()) is not None and tok != stop:
            if tok == "}" or tok == "\\right":
                break
            script, box = self.atom()
            script, box = self.scripts(script, box)
            parts.append(script)
            boxes.append(box)
        return " ".join(p for p in parts if p), _row(boxes)

    def group(self) -> tuple[str, EqBox]:
        """{...} 또는 글자 하나(x^2의 2)."""
        if self.peek() == "{":
            self.take()
            script, box = self.row(stop="}")
            if self.take() != "}":
                raise _Unsupported("unbalanced")
            return "{" + script + "}", box
        script, box = self.atom()
        return "{" + script + "}", box

    def scripts(self, base: str, box: EqBox) -> tuple[str, EqBox]:
        sub = sup = None
        while self.peek() in ("^", "_"):
            op = self.take()
            if (op == "^" and sup is not None) or (op == "_" and sub is not None):
                raise _Unsupported("double script")
            if op == "^":
                sup = self.group()
            else:
                sub = self.group()
        out, width, ascent, descent = base, box.width, box.ascent, box.descent
        extra = 0.0
        if sub is not None:
            out += " _" + sub[0]
            extra = max(extra, sub[1].width * _SCRIPT_SCALE)
            descent = max(descent, sub[1].height * _SCRIPT_SCALE)
        if sup is not None:
            out += " ^" + sup[0]
            extra = max(extra, sup[1].width * _SCRIPT_SCALE)
            ascent = max(ascent, box.ascent * 0.45 + sup[1].height * _SCRIPT_SCALE)
        return out, EqBox(width + extra + (40 if extra else 0), ascent, descent)

    def atom(self) -> tuple[str, EqBox]:
        tok = self.take()
        if tok == "{":
            script, box = self.row(stop="}")
            if self.take() != "}":
                raise _Unsupported("unbalanced")
            return "{" + script + "}", box
        if tok.isascii() and tok.isalpha():
            return tok, _leaf(560)  # 글자는 하나씩 띄어 쓴다(pi·in 같은 예약어로 읽히지 않게)
        if tok[0].isdigit():
            return tok, _leaf(500 * len(tok))
        if tok in _PLAIN_CHARS:
            width = 900 if tok in "+-=<>" else 400 if tok in "()[]|" else 300
            return tok, _leaf(width)
        if not tok.startswith("\\"):
            raise _Unsupported(f"character {tok!r}")
        name = tok[1:]
        if name in ("frac", "dfrac", "tfrac"):
            num, nbox = self.group()
            den, dbox = self.group()
            width = max(nbox.width, dbox.width) + 300
            return "{" + num + " over " + den + "}", EqBox(width, 330 + nbox.height, max(dbox.height - 170, _DESC))
        if name == "sqrt":
            if self.peek() == "[":
                self.take()
                index, ibox = self.row(stop="]")
                if self.take() != "]":
                    raise _Unsupported("sqrt index")
                body, bbox = self.group()
                return "{root {" + index + "} of " + body + "}", EqBox(bbox.width + 600 + ibox.width * 0.6, bbox.ascent + 200, bbox.descent)
            body, bbox = self.group()
            return "{sqrt " + body + "}", EqBox(bbox.width + 600, bbox.ascent + 200, bbox.descent)
        if name in _GREEK_MAP:
            return _GREEK_MAP[name], _leaf(600)
        if name in _GREEK_UPPER:
            return _GREEK_UPPER[name], _leaf(700)
        if name in _SYMBOLS:
            return _SYMBOLS[name], _leaf(950)
        if name in _FUNCTIONS:
            return name, _leaf(480 * len(name) + 150)
        if name in _SPACES:
            return _SPACES[name], _leaf(250 * len(_SPACES[name]))
        if name in ("mathrm", "text", "textrm", "operatorname"):
            return self.text_arg(roman=name != "text")
        if name in ("{", "}"):
            return ("lbrace" if name == "{" else "rbrace"), _leaf(450)
        if name == "left":
            left = self.delim()
            inner, ibox = self.row(stop="\\right")
            if self.take() != "\\right":
                raise _Unsupported("left without right")
            right = self.delim()
            box = EqBox(ibox.width + 800, ibox.ascent + 80, ibox.descent + 80)
            return f"left {left or '.'} {inner} right {right or '.'}", box
        raise _Unsupported(f"command {tok}")

    def delim(self) -> str:
        tok = self.take()
        if tok not in _DELIMS:
            raise _Unsupported(f"delimiter {tok}")
        return _DELIMS[tok]

    def text_arg(self, roman: bool) -> tuple[str, EqBox]:
        """\\mathrm{kg}·\\text{속도}: 중괄호 안 글자를 그대로(바로 세운 글자로) 쓴다."""
        if self.take() != "{":
            raise _Unsupported("text argument")
        chars: list[str] = []
        depth = 1
        while depth:
            tok = self.take(raw=True)
            if tok == "}":
                depth -= 1
            elif tok in ("\\text", "\\textrm", "\\mathrm") and self.take() == "{":
                depth += 1  # hangul_to_text가 \\text{속도}를 \\text{\\text{속도}}로 감싼다
            elif tok.startswith("\\") or tok in ("{", "^", "_", "&", "$", "#", "%"):
                raise _Unsupported("command inside text")
            else:
                chars.append(tok)
        text = "".join(chars).strip()
        if '"' in text or "`" in text or "~" in text:
            raise _Unsupported("quote in text")
        width = sum(1000 if ord(ch) > 0x2E80 else 520 for ch in text) or 300
        if roman and text.isascii() and text.replace(" ", "").isalnum():
            letters = " ".join(text.replace(" ", ""))
            return "{rm " + letters + "}", _leaf(width)
        return f'"{text}"', _leaf(width)


def latex_to_hwpeq(tex: str) -> Equation | None:
    """단순한 LaTeX 수식을 한컴 수식 스크립트로. 바꿀 수 없으면 None(그 수식은 그림으로 넣는다).

    다루는 것: 글자·숫자·+−=<>(), \\frac, ^, _, \\sqrt(와 [n]), 그리스 문자, \\times·\\cdot·\\pm·\\le 등 기본 기호,
    sin·log 같은 함수 이름, \\mathrm{}·\\text{}, \\left( \\right), 간격(\\, \\; \\quad).
    """
    tex = tex.strip()
    if not tex or len(tex) > MAX_EQ_SCRIPT:
        return None
    try:
        script, box = _EqParser(tex).parse()
    except (_Unsupported, RecursionError):
        return None
    if not script.strip():
        return None
    return Equation(script, box)


# ── 그림 크기 ───────────────────────────────────────────────────────


@dataclass
class Picture:
    """문서에 넣을 그림(PNG·JPEG). width·height는 원래 크기(HWPUNIT)."""

    path: Path
    width: int
    height: int


def image_size(path: Path) -> tuple[int, int, float, float]:
    """(가로 px, 세로 px, 가로 dpi, 세로 dpi). PNG(IHDR·pHYs)와 JPEG(SOF·JFIF)만."""
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        dpi_x = dpi_y = float(DEFAULT_IMAGE_DPI)
        pos = 8
        while pos + 8 <= len(data):
            (length,) = struct.unpack(">I", data[pos : pos + 4])
            kind = data[pos + 4 : pos + 8]
            if kind == b"pHYs" and length >= 9:
                px, py, unit = struct.unpack(">IIB", data[pos + 8 : pos + 17])
                if unit == 1 and px and py:
                    dpi_x, dpi_y = px * 0.0254, py * 0.0254
            if kind in (b"IDAT", b"IEND"):
                break
            pos += 12 + length
        return width, height, dpi_x, dpi_y
    if data.startswith(b"\xff\xd8"):
        dpi_x = dpi_y = float(DEFAULT_IMAGE_DPI)
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
            body = data[pos + 4 : pos + 2 + length]
            if marker == 0xE0 and body.startswith(b"JFIF\x00") and len(body) >= 12:
                unit, dx, dy = struct.unpack(">BHH", body[7:12])
                if unit == 1 and dx and dy:
                    dpi_x, dpi_y = float(dx), float(dy)
                elif unit == 2 and dx and dy:
                    dpi_x, dpi_y = dx * 2.54, dy * 2.54
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and len(body) >= 5:
                height, width = struct.unpack(">HH", body[1:5])
                return width, height, dpi_x, dpi_y
            pos += 2 + length
    raise ValueError(f"unsupported image: {path.name}")


def picture_from_file(path: Path) -> Picture:
    px_w, px_h, dpi_x, dpi_y = image_size(path)
    return Picture(path, round(px_w / dpi_x * HWPUNIT_PER_INCH), round(px_h / dpi_y * HWPUNIT_PER_INCH))


_LENGTH_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(%|cm|mm|in|px|pt)?\s*$")


def _length(value: str, full: int) -> int | None:
    """pandoc 그림 속성 width/height 값(50%, 6cm, 300px)을 HWPUNIT으로."""
    m = _LENGTH_RE.match(value)
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2) or "px"
    scale = {"%": full / 100, "cm": HWPUNIT_PER_CM, "mm": HWPUNIT_PER_CM / 10, "in": HWPUNIT_PER_INCH, "px": 75.0, "pt": 100.0}[unit]
    return round(number * scale) or None


# ── 본문(section0.xml) ──────────────────────────────────────────────


@dataclass
class _Run:
    char: int
    xml: str


@dataclass
class _State:
    """글자 모양을 바꾸는 인라인(굵게·기울임 등)을 따라 내려가는 상태."""

    char: CharStyle = field(default_factory=CharStyle)


@dataclass
class WriteInfo:
    equations: int = 0  # 한컴 수식 객체로 넣은 수식
    math_images: int = 0  # 그림으로 넣은 수식
    images: int = 0
    tables: int = 0
    text: list[str] = field(default_factory=list)  # 미리보기 글(PrvText.txt)
    warnings: list[str] = field(default_factory=list)


class SectionWriter:
    """pandoc 블록·인라인을 OWPML 문단(hp:p) 문자열로 바꾼다."""

    def __init__(
        self,
        reg: Registry,
        pictures: dict[str, Picture],
        math: Callable[[dict[str, Any]], Equation | Picture | None],
        bin_ids: dict[Path, str],
    ) -> None:
        self.reg = reg
        self.pictures = pictures
        self.math = math
        self.bin_ids = bin_ids
        self.object_id = 0
        self.notes: list[list[dict[str, Any]]] = []
        self.info = WriteInfo()
        self.page_break_next = False

    # 문단 ------------------------------------------------------------

    def paragraph(self, runs: list[_Run], para: ParaStyle = PLAIN_PARA, style_id: int = 0) -> str:
        pid = self.reg.para(para)
        page_break = int(self.page_break_next)
        self.page_break_next = False
        if not runs:
            runs = [_Run(self.reg.char(CharStyle()), "<hp:t/>")]
        body = "".join(f'<hp:run charPrIDRef="{r.char}">{r.xml}</hp:run>' for r in self._merge(runs))
        return f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="{style_id}" pageBreak="{page_break}" columnBreak="0" merged="0">{body}</hp:p>'

    @staticmethod
    def _merge(runs: list[_Run]) -> list[_Run]:
        """같은 글자 모양의 이웃한 글자 묶음을 한 run으로 합친다."""
        out: list[_Run] = []
        for run in runs:
            if out and out[-1].char == run.char:
                prev = out[-1].xml
                if prev.endswith("</hp:t>") and run.xml.startswith("<hp:t>") and not prev.endswith("<hp:lineBreak/></hp:t>"):
                    # 이웃한 글자는 hp:t 하나로(글자만 읽는 프로그램이 낱말 사이를 끊어 읽지 않게)
                    out[-1] = _Run(run.char, prev[: -len("</hp:t>")] + run.xml[len("<hp:t>") :])
                else:
                    out[-1] = _Run(run.char, prev + run.xml)
            else:
                out.append(run)
        return out

    def _next_id(self) -> int:
        self.object_id += 1
        return self.object_id

    # 인라인 ----------------------------------------------------------

    def inlines(self, nodes: list[dict[str, Any]], state: _State, text: list[str]) -> list[_Run]:
        runs: list[_Run] = []

        def put(s: str) -> None:
            if s:
                text.append(s)
                runs.append(_Run(self.reg.char(state.char), f"<hp:t>{xml_text(s)}</hp:t>"))

        def styled(children: list[dict[str, Any]], **change: Any) -> None:
            inner = _State(_replace(state.char, **change))
            runs.extend(self.inlines(children, inner, text))

        for node in nodes:
            kind, c = node["t"], node.get("c")
            if kind == "Str":
                put(c)
            elif kind in ("Space", "SoftBreak"):
                put(" ")
            elif kind == "LineBreak":
                # 줄 바꿈은 hp:t 안의 hp:lineBreak. 뒤 글자는 새 hp:t에 두어 글자만 읽는 프로그램도 놓치지 않게 한다
                runs.append(_Run(self.reg.char(state.char), "<hp:t><hp:lineBreak/></hp:t>"))
                text.append("\n")
            elif kind == "Strong":
                styled(c, bold=True)
            elif kind == "Emph":
                styled(c, italic=not state.char.italic)
            elif kind == "Underline":
                styled(c, underline=True)
            elif kind == "Strikeout":
                styled(c, strike=True)
            elif kind == "Superscript":
                styled(c, sup=True, sub=False)
            elif kind == "Subscript":
                styled(c, sub=True, sup=False)
            elif kind == "SmallCaps":
                styled(c)
            elif kind == "Code":
                code_state = _State(_replace(state.char, font=HEADING_FONT))
                runs.extend(self.inlines([{"t": "Str", "c": c[1]}], code_state, text))
            elif kind == "Quoted":
                left, right = ("“", "”") if c[0]["t"] == "DoubleQuote" else ("‘", "’")
                put(left)
                styled(c[1])
                put(right)
            elif kind in ("Span", "Cite"):
                styled(c[1])
            elif kind == "Link":
                styled(c[1], color="#0563C1", underline=True)
                url = c[2][0]
                if url and url != _plain(c[1]) and not url.startswith("#"):
                    put(f" <{url}>")
            elif kind == "Image":
                runs.append(self.picture_run(node, state))
                text.append(_plain(c[1]))
            elif kind == "Math":
                runs.extend(self.math_runs(node, state, text))
            elif kind == "Note":
                self.notes.append(c)
                styled([{"t": "Str", "c": f"{len(self.notes)})"}], sup=True, sub=False)
            # RawInline은 export가 이미 거절했다
        return runs

    def math_runs(self, node: dict[str, Any], state: _State, text: list[str]) -> list[_Run]:
        tex = node["c"][1]
        text.append(tex)
        item = self.math(node)
        cid = self.reg.char(state.char)
        if isinstance(item, Equation):
            self.info.equations += 1
            return [_Run(cid, self.equation_xml(item))]
        if isinstance(item, Picture):
            self.info.math_images += 1
            return [_Run(cid, self.pic_xml(item, item.width, item.height))]
        # 그림을 만들지 못한 수식(만들 수 없을 때만): 원문을 글자로 남긴다
        return [_Run(cid, f"<hp:t>{xml_text(tex)}</hp:t>")]

    def equation_xml(self, eq: Equation) -> str:
        oid = self._next_id()
        width, height = max(round(eq.box.width), 300), max(round(eq.box.height), 600)
        baseline = max(1, min(100, round(eq.box.ascent / eq.box.height * 100)))
        return (
            f'<hp:equation id="{oid}" zOrder="{oid}" numberingType="EQUATION" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            f'lock="0" dropcapstyle="None" version="Equation Version 60" baseLine="{baseline}" textColor="#000000" '
            'baseUnit="1000" lineMode="CHAR" font="HancomEQN">'
            f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
            'horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
            f"<hp:script>{xml_text(eq.script)}</hp:script></hp:equation>"
        )

    def picture_run(self, node: dict[str, Any], state: _State, max_width: int = TEXT_WIDTH) -> _Run:
        attr, _alt, (target, _title) = node["c"]
        pic = self.pictures[target]
        width, height = pic.width, pic.height
        keys = dict(attr[2])
        want_w = _length(keys["width"], max_width) if "width" in keys else None
        want_h = _length(keys["height"], TEXT_HEIGHT) if "height" in keys else None
        if want_w and want_h:
            width, height = want_w, want_h
        elif want_w:
            width, height = want_w, round(height * want_w / width)
        elif want_h:
            width, height = round(width * want_h / height), want_h
        # 글 폭·쪽 높이에 맞게 줄인다(비율 유지)
        scale = min(1.0, max_width / width, TEXT_HEIGHT * 0.9 / height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
        self.info.images += 1
        return _Run(self.reg.char(state.char), self.pic_xml(pic, width, height))

    def pic_xml(self, pic: Picture, width: int, height: int) -> str:
        oid = self._next_id()
        bin_id = self.bin_ids.setdefault(pic.path, f"image{len(self.bin_ids) + 1}")
        identity = 'e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"'
        return (
            f'<hp:pic id="{oid}" zOrder="{oid}" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
            f'dropcapstyle="None" href="" groupLevel="0" instid="{oid}" reverse="0">'
            '<hp:offset x="0" y="0"/>'
            f'<hp:orgSz width="{width}" height="{height}"/>'
            f'<hp:curSz width="{width}" height="{height}"/>'
            '<hp:flip horizontal="0" vertical="0"/>'
            f'<hp:rotationInfo angle="0" centerX="{width // 2}" centerY="{height // 2}" rotateimage="1"/>'
            f"<hp:renderingInfo><hc:transMatrix {identity}/><hc:scaMatrix {identity}/><hc:rotMatrix {identity}/></hp:renderingInfo>"
            f'<hc:img binaryItemIDRef="{bin_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
            f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{width}" y="0"/><hc:pt2 x="{width}" y="{height}"/>'
            f'<hc:pt3 x="0" y="{height}"/></hp:imgRect>'
            f'<hp:imgClip left="0" right="{pic.width}" top="0" bottom="{pic.height}"/>'
            '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:imgDim dimwidth="{pic.width}" dimheight="{pic.height}"/>'
            "<hp:effects/>"
            f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
            'horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            "</hp:pic>"
        )

    # 블록 ------------------------------------------------------------

    def blocks(self, blocks: list[dict[str, Any]], ctx: _Ctx) -> list[str]:
        out: list[str] = []
        for block in blocks:
            out.extend(self.block(block, ctx))
        return out

    def _text_para(self, inlines: list[dict[str, Any]], para: ParaStyle, char: CharStyle = PLAIN_CHAR, style_id: int = 0) -> str:
        text: list[str] = []
        runs = self.inlines(inlines, _State(char), text)
        self.info.text.append("".join(text))
        return self.paragraph(runs, para, style_id)

    def block(self, block: dict[str, Any], ctx: _Ctx) -> list[str]:
        kind, c = block["t"], block.get("c")
        base = ParaStyle(left=ctx.left, next=ctx.next)
        if kind in ("Para", "Plain"):
            if _is_page_break(c):
                self.page_break_next = True
                return []
            if kind == "Para" and len(c) == 1 and c[0]["t"] == "Math" and c[0]["c"][0]["t"] == "DisplayMath":
                return [self._text_para(c, _replace(base, align="CENTER"), ctx.char)]
            if kind == "Para" and len(c) == 1 and c[0]["t"] == "Image":
                return [self._text_para(c, _replace(base, align="CENTER"), ctx.char)]
            return [self._text_para(c, base, ctx.char)]
        if kind == "Header":
            level, attr, inlines = c
            level = min(level, max(HEADING_SIZES))
            out = []
            if "openstem-answers" in attr[1]:
                self.page_break_next = True
            para = heading_para(level)
            if ctx.exam and level == 1:
                para = _replace(para, align="CENTER")
            out.append(self._text_para(inlines, para, heading_char(level), style_id=level))
            return out
        if kind == "BulletList":
            return self.list_items(c, ctx, None)
        if kind == "OrderedList":
            (start, style, delim), items = c
            fmt = _NUMBER_FORMATS.get(style["t"], "DIGIT")
            text = {"OneParen": "^1)", "TwoParens": "(^1)"}.get(delim["t"], "^1.")
            nid = self.reg.numbering(start, fmt, text)
            return self.list_items(items, ctx, nid)
        if kind == "DefinitionList":
            out = []
            for term, definitions in c:
                out.append(self._text_para(term, base, _replace(ctx.char, bold=True)))
                for definition in definitions:
                    out.extend(self.blocks(definition, ctx.indented(LIST_INDENT)))
            return out
        if kind == "BlockQuote":
            return self.blocks(c, ctx.indented(QUOTE_INDENT))
        if kind == "Div":
            return self.blocks(c[1], ctx)
        if kind == "CodeBlock":
            code_char = _replace(ctx.char, font=HEADING_FONT, height=900)
            para = _replace(base, align="LEFT", line=130)
            lines = c[1].replace("\t", "    ").split("\n")
            return [self._text_para([{"t": "Str", "c": line}], para, code_char) for line in lines]
        if kind == "LineBlock":
            return [self._text_para(line, _replace(base, align="LEFT"), ctx.char) for line in c]
        if kind == "HorizontalRule":
            return [self._text_para([{"t": "Str", "c": "─" * 30}], _replace(base, align="CENTER"), ctx.char)]
        if kind == "Table":
            return [self.table(c, ctx)]
        if kind == "Figure":
            _attr, (_short, caption), body = c
            out = self.figure_body(body, ctx)
            caption_inlines = [i for b in caption for i in (b["c"] if b["t"] in ("Plain", "Para") else [])]
            if caption_inlines:
                out.append(self._text_para(caption_inlines, _replace(base, align="CENTER", next=600), ctx.char))
            return out
        return []  # RawBlock(이미 거절됨), Null 등

    def figure_body(self, body: list[dict[str, Any]], ctx: _Ctx) -> list[str]:
        out = []
        for block in body:
            if block["t"] in ("Plain", "Para"):
                out.append(self._text_para(block["c"], ParaStyle(align="CENTER", left=ctx.left), ctx.char))
            else:
                out.extend(self.block(block, ctx))
        return out

    def list_items(self, items: list[list[dict[str, Any]]], ctx: _Ctx, numbering: int | None) -> list[str]:
        out: list[str] = []
        depth = ctx.depth
        left = ctx.left + LIST_INDENT
        if numbering is None:
            heading = ("BULLET", self.reg.bullet_id(depth), 0)
        else:
            heading = ("NUMBER", numbering, 0)
        inner = ctx.list_child(left)
        for item in items:
            first, rest = (item[0], item[1:]) if item else (None, [])
            if first is not None and first["t"] in ("Plain", "Para"):
                para = ParaStyle(align="LEFT", left=left, heading=heading, next=ctx.next)
                out.append(self._text_para(first["c"], para, ctx.char))
            else:
                para = ParaStyle(align="LEFT", left=left, heading=heading)
                out.append(self.paragraph([], para))
                rest = item
            out.extend(self.blocks(rest, inner))
        return out

    def table(self, c: list[Any], ctx: _Ctx) -> str:
        _attr, (_short, caption), colspecs, head, bodies, foot = c
        ncols = len(colspecs)
        available = max(TEXT_WIDTH - ctx.left, 4000)
        widths = [spec[1]["c"] if spec[1]["t"] == "ColWidth" else 0.0 for spec in colspecs]
        if sum(widths) <= 0:
            widths = [1.0] * ncols
        else:
            missing = [w for w in widths if w <= 0]
            fill = (1 - sum(widths)) / len(missing) if missing and sum(widths) < 1 else 0.1
            widths = [w if w > 0 else fill for w in widths]
        total = sum(widths)
        col_w = [max(1500, round(available * w / total)) for w in widths]

        rows: list[tuple[bool, list[Any]]] = [(True, row) for row in head[1]]
        for body in bodies:
            rows += [(True, row) for row in body[2]] + [(False, row) for row in body[3]]
        rows += [(False, row) for row in foot[1]]

        occupied: set[tuple[int, int]] = set()
        row_xml: list[str] = []
        row_h = 1600
        for r, (is_head, row) in enumerate(rows):
            col = 0
            cells: list[str] = []
            for cell in row[1]:
                _cattr, align, rowspan, colspan, blocks = cell
                while (r, col) in occupied:
                    col += 1
                if col >= ncols:
                    break
                colspan = max(1, min(colspan, ncols - col))
                rowspan = max(1, min(rowspan, len(rows) - r))
                for dr in range(rowspan):
                    for dc in range(colspan):
                        occupied.add((r + dr, col + dc))
                width = sum(col_w[col : col + colspan])
                cell_ctx = _Ctx(char=_replace(ctx.char, bold=ctx.char.bold or is_head), cell_align=_ALIGNS.get(align["t"]))
                paras = self.cell_blocks(blocks, cell_ctx, width - 1020)
                cells.append(
                    f'<hp:tc name="" header="{int(is_head)}" hasMargin="0" protect="0" editable="0" dirty="0" '
                    f'borderFillIDRef="{TABLE_BORDER_FILL}">'
                    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" '
                    'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                    f"{''.join(paras)}</hp:subList>"
                    f'<hp:cellAddr colAddr="{col}" rowAddr="{r}"/>'
                    f'<hp:cellSpan colSpan="{colspan}" rowSpan="{rowspan}"/>'
                    f'<hp:cellSz width="{width}" height="{row_h * rowspan}"/>'
                    '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                    "</hp:tc>"
                )
                col += colspan
            row_xml.append("<hp:tr>" + "".join(cells) + "</hp:tr>")

        oid = self._next_id()
        width, height = sum(col_w), row_h * max(1, len(rows))
        self.info.tables += 1
        tbl = (
            f'<hp:tbl id="{oid}" zOrder="{oid}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
            f'dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="{len(rows)}" colCnt="{ncols}" cellSpacing="0" '
            f'borderFillIDRef="{TABLE_BORDER_FILL}" noAdjust="0">'
            f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
            'horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
            '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            f"{''.join(row_xml)}</hp:tbl>"
        )
        para = ParaStyle(align="CENTER", left=ctx.left, next=400)
        out = self.paragraph([_Run(self.reg.char(ctx.char), tbl)], para)
        caption_inlines = [i for b in caption for i in (b["c"] if b["t"] in ("Plain", "Para") else [])]
        if caption_inlines:  # 표 제목은 표 위에 둔다
            out = self._text_para(caption_inlines, ParaStyle(align="CENTER", left=ctx.left), ctx.char) + out
        return out

    def cell_blocks(self, blocks: list[dict[str, Any]], ctx: _Ctx, width: int) -> list[str]:
        if not blocks:
            return [self.paragraph([], ParaStyle(align=ctx.cell_align or "LEFT"))]
        out = []
        for block in blocks:
            if block["t"] in ("Plain", "Para"):
                if _is_page_break(block["c"]):
                    continue
                if len(block["c"]) == 1 and block["c"][0]["t"] == "Image":
                    text: list[str] = []
                    run = self.picture_run(block["c"][0], _State(ctx.char), max_width=max(width, 1000))
                    text.append(_plain(block["c"][0]["c"][1]))
                    self.info.text.append("".join(text))
                    out.append(self.paragraph([run], ParaStyle(align="CENTER")))
                    continue
                out.append(self._text_para(block["c"], ParaStyle(align=ctx.cell_align or "LEFT"), ctx.char))
            else:
                out.extend(self.block(block, ctx))
        return out or [self.paragraph([], ParaStyle())]

    def footnotes(self) -> list[str]:
        if not self.notes:
            return []
        out = [self._text_para([{"t": "Str", "c": "─" * 12}], ParaStyle(align="LEFT", prev=800))]
        small = CharStyle(height=900)
        for number, blocks in enumerate(self.notes, start=1):
            inlines: list[dict[str, Any]] = [{"t": "Str", "c": f"{number})"}, {"t": "Space"}]
            for k, block in enumerate(blocks):
                if block["t"] in ("Plain", "Para"):
                    inlines += ([{"t": "Space"}] if k else []) + block["c"]
            out.append(self._text_para(inlines, ParaStyle(align="LEFT"), small))
        return out


@dataclass
class _Ctx:
    """블록을 쓸 때 물려받는 들여쓰기·글자 모양."""

    left: int = 0
    depth: int = 0
    next: int = 0
    char: CharStyle = field(default_factory=CharStyle)
    cell_align: str | None = None
    exam: bool = False

    def indented(self, amount: int) -> _Ctx:
        return _replace(self, left=self.left + amount)

    def list_child(self, left: int) -> _Ctx:
        return _replace(self, left=left, depth=self.depth + 1)


_NUMBER_FORMATS = {
    "Decimal": "DIGIT", "DefaultStyle": "DIGIT", "Example": "DIGIT", "LowerAlpha": "LATIN_SMALL",
    "UpperAlpha": "LATIN_CAPITAL", "LowerRoman": "ROMAN_SMALL", "UpperRoman": "ROMAN_CAPITAL",
}  # fmt: skip
_ALIGNS = {"AlignLeft": "LEFT", "AlignRight": "RIGHT", "AlignCenter": "CENTER"}
_PAGE_BREAK_WORDS = ("\\newpage", "\\pagebreak", "\\clearpage")


def _replace(obj: Any, **change: Any) -> Any:
    return dataclasses.replace(obj, **change) if change else obj


def _plain(inlines: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for node in inlines:
        kind, c = node["t"], node.get("c")
        if kind == "Str":
            parts.append(c)
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            parts.append(" ")
        elif kind in ("Code", "Math"):
            parts.append(c[1])
        elif kind in ("Strong", "Emph", "Underline", "Strikeout", "Superscript", "Subscript", "SmallCaps"):
            parts.append(_plain(c))
        elif kind in ("Span", "Link", "Cite", "Quoted", "Image"):
            parts.append(_plain(c[1]))
    return "".join(parts)


def _is_page_break(inlines: list[dict[str, Any]]) -> bool:
    """`\\newpage`만 있는 문단은 쪽 나누기로 읽는다(raw TeX가 꺼져 있어 글자로 들어온다)."""
    return len(inlines) == 1 and inlines[0]["t"] == "Str" and inlines[0]["c"] in _PAGE_BREAK_WORDS


def _sec_pr() -> str:
    """첫 문단의 첫 run에 두는 구역 설정(A4 세로, 한글 기본 여백)과 단 설정."""
    note = (
        '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
        '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
        '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
        '<hp:numbering type="CONTINUOUS" newNum="1"/>'
    )
    border = "".join(
        f'<hp:pageBorderFill type="{kind}" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">'
        '<hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
        for kind in ("BOTH", "EVEN", "ODD")
    )
    return (
        '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" tabStopUnit="HWPUNIT" '
        f'outlineShapeIDRef="{Registry.OUTLINE_ID}" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0">'
        '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
        '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
        '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" fill="SHOW_ALL" '
        'hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
        '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
        f'<hp:pagePr landscape="WIDELY" width="{PAGE_WIDTH}" height="{PAGE_HEIGHT}" gutterType="LEFT_ONLY">'
        f'<hp:margin header="{MARGIN_HEADER}" footer="{MARGIN_FOOTER}" gutter="0" left="{MARGIN_LEFT}" right="{MARGIN_RIGHT}" '
        f'top="{MARGIN_TOP}" bottom="{MARGIN_BOTTOM}"/></hp:pagePr>'
        f'<hp:footNotePr>{note}<hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
        f'<hp:endNotePr>{note}<hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
        f"{border}</hp:secPr>"
        '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/></hp:ctrl>'
    )


# ── 시험지(exam) 배치 ───────────────────────────────────────────────

EXAM_DEFAULT_SPACE = 4.0  # cm, export.EXAM_SPACE_DEFAULT·exam.lua와 같다


def _exam_blocks(writer: SectionWriter, blocks: list[dict[str, Any]], ctx: _Ctx) -> list[str]:
    """exam.lua와 같은 배치: 맨 위 학번·이름 줄, 문제 뒤 풀이 공간, 정답과 해설은 새 쪽."""
    name_line = [{"t": "Str", "c": "학번: ______________    이름: ______________"}]
    out = [writer._text_para(name_line, ParaStyle(align="RIGHT", next=600))]
    pending: float | None = None

    def flush() -> None:
        nonlocal pending
        if pending:
            out.append(writer.paragraph([], ParaStyle(prev=round(pending * HWPUNIT_PER_CM))))
        pending = None

    for block in blocks:
        if block["t"] == "Header":
            classes, pairs = block["c"][1][1], dict(block["c"][1][2])
            if "openstem-problem" in classes:
                flush()
                try:
                    pending = float(pairs.get("space", EXAM_DEFAULT_SPACE))
                except ValueError:
                    pending = EXAM_DEFAULT_SPACE
            elif "openstem-answers" in classes or block["c"][0] == 1:
                flush()
        out.extend(writer.block(block, ctx))
    flush()
    return out


# ── 패키지 ──────────────────────────────────────────────────────────


def _content_hpf(title: str, bin_items: list[tuple[str, str, str]]) -> str:
    now = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = "".join(
        f'<opf:item id="{bid}" href="{href}" media-type="{media}" isEmbeded="1"/>' for bid, href, media in bin_items
    )
    return (
        XML_DECL + f'<opf:package {_XMLNS} version="" unique-identifier="" id="">'
        f"<opf:metadata><opf:title>{xml_text(title)}</opf:title><opf:language>ko</opf:language>"
        '<opf:meta name="creator" content="text">OpenSTEM</opf:meta>'
        f'<opf:meta name="CreatedDate" content="text">{now}</opf:meta>'
        f'<opf:meta name="ModifiedDate" content="text">{now}</opf:meta></opf:metadata>'
        "<opf:manifest>"
        '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
        f"{items}"
        '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        '<opf:item id="settings" href="settings.xml" media-type="application/xml"/>'
        "</opf:manifest>"
        '<opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine>'
        "</opf:package>"
    )


VERSION_XML = (
    XML_DECL + '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" tagetApplication="WORDPROCESSOR" '
    f'major="5" minor="1" micro="0" buildNumber="0" os="1" xmlVersion="1.4" application="OpenSTEM" appVersion="{__version__}"/>'
)
CONTAINER_XML = (
    XML_DECL + '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles>'
    '<ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
    '<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>'
    '<ocf:rootfile full-path="META-INF/container.rdf" media-type="application/rdf+xml"/>'
    "</ocf:rootfiles></ocf:container>"
)
MANIFEST_XML = XML_DECL + '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>'
_PKG = "http://www.hancom.co.kr/hwpml/2016/meta/pkg#"
CONTAINER_RDF = (
    XML_DECL + '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    + "".join(
        f'<rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="{_PKG}" rdf:resource="Contents/{part}.xml"/></rdf:Description>'
        f'<rdf:Description rdf:about="Contents/{part}.xml"><rdf:type rdf:resource="{_PKG}{kind}"/></rdf:Description>'
        for part, kind in (("header", "HeaderFile"), ("section0", "SectionFile"))
    )
    + f'<rdf:Description rdf:about=""><rdf:type rdf:resource="{_PKG}Document"/></rdf:Description></rdf:RDF>'
)
SETTINGS_XML = (
    XML_DECL + '<ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
    '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>'
)
PREVIEW_CHARS = 1024


def write_hwpx(
    ast: dict[str, Any],
    dest: Path,
    *,
    title: str | None,
    pictures: dict[str, Picture],
    math: Callable[[dict[str, Any]], Equation | Picture | None],
    exam: bool = False,
) -> WriteInfo:
    """pandoc 문서 구조를 HWPX 파일로 쓴다.

    pictures: 그림 요소의 경로(export가 바꿔 둔 작업 폴더 파일 이름) → Picture.
    math: Math 요소 → 한컴 수식(Equation), 수식 그림(Picture), 또는 None(원문을 글자로).
    """
    reg = Registry()
    bin_ids: dict[Path, str] = {}
    writer = SectionWriter(reg, pictures, math, bin_ids)
    ctx = _Ctx(exam=exam)
    paras: list[str] = []
    if title:
        paras.append(writer._text_para([{"t": "Str", "c": title}], ParaStyle(align="CENTER", next=800), CharStyle(height=TITLE_SIZE, font=HEADING_FONT, bold=True)))
    blocks = ast.get("blocks", [])
    paras += _exam_blocks(writer, blocks, ctx) if exam else writer.blocks(blocks, ctx)
    paras += writer.footnotes()
    if not paras:
        paras.append(writer.paragraph([], ParaStyle()))
    # 구역 설정은 첫 문단의 첫 run에 넣는다
    first = paras[0]
    cut = first.index(">") + 1
    paras[0] = first[:cut] + f'<hp:run charPrIDRef="0">{_sec_pr()}</hp:run>' + first[cut:]
    section = XML_DECL + f"<hs:sec {_XMLNS}>" + "".join(paras) + "</hs:sec>"

    bin_items = []
    for path, bid in bin_ids.items():
        ext = "jpg" if path.suffix.lower() in (".jpg", ".jpeg") else "png"
        bin_items.append((bid, f"BinData/{bid}.{ext}", "image/jpeg" if ext == "jpg" else "image/png"))
    preview = "\r\n".join(t for t in writer.info.text if t.strip())[:PREVIEW_CHARS]

    with zipfile.ZipFile(dest, "w") as zf:
        # mimetype은 첫 항목, 압축 없이(OCF 규약)
        zf.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)

        def put(name: str, data: str | bytes) -> None:
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)

        put("version.xml", VERSION_XML)
        put("Contents/header.xml", header_xml(reg))
        put("Contents/section0.xml", section)
        put("Preview/PrvText.txt", _XML_BAD_RE.sub("", preview))
        put("settings.xml", SETTINGS_XML)
        put("META-INF/container.rdf", CONTAINER_RDF)
        put("Contents/content.hpf", _content_hpf(title or "", bin_items))
        put("META-INF/container.xml", CONTAINER_XML)
        put("META-INF/manifest.xml", MANIFEST_XML)
        for (path, _bid), (_, href, _media) in zip(bin_ids.items(), bin_items, strict=True):
            zf.write(path, href, compress_type=zipfile.ZIP_STORED)
    return writer.info


# ── 수식 그림(그림으로 넣는 수식) ───────────────────────────────────

MATH_DPI = 300
MAX_MATH_IMAGES = 300
MATH_PAGE_ENV = "openstemmath"


def math_source(items: list[tuple[str, bool]], hangul_setup: str, file_guard: str) -> str:
    """수식마다 한 쪽인 standalone 문서. items = [(LaTeX, display 여부)]. 수식은 이미 texcheck를 거쳤다."""
    header = [
        r"\documentclass[border=1pt]{standalone}",
        r"\usepackage{amsmath,amssymb}",
        hangul_setup,
        rf"\newenvironment{{{MATH_PAGE_ENV}}}{{}}{{}}\standaloneenv{{{MATH_PAGE_ENV}}}",
        file_guard + r"\begin{document}",
    ]
    body = [
        rf"\begin{{{MATH_PAGE_ENV}}}$\displaystyle {tex}$\end{{{MATH_PAGE_ENV}}}" if display else rf"\begin{{{MATH_PAGE_ENV}}}${tex}$\end{{{MATH_PAGE_ENV}}}"
        for tex, display in items
    ]
    return "\n".join([*filter(None, header), *body, r"\end{document}"]) + "\n"


def pdf_pages_to_png(pdf: Path, dpi: int = MATH_DPI) -> list[dict[str, Any]]:
    """(작업자 프로세스에서만) PDF 쪽마다 PNG를 `math<N>.png`로 저장한다. 서버는 이 함수를 부르지 않는다."""
    import pymupdf  # 작업자에서만 import

    out = []
    with pymupdf.open(pdf) as doc:
        if doc.page_count > MAX_MATH_IMAGES:
            raise ValueError(f"too many math pages ({doc.page_count})")
        for index, page in enumerate(doc, start=1):
            rect = page.rect
            if max(rect.width, rect.height) > 72 * 40:  # 40인치보다 큰 수식은 없다
                raise ValueError("math image too large")
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            pix.set_dpi(dpi, dpi)
            name = f"math{index}.png"
            pix.save(name)
            out.append({"png": name, "width_pt": rect.width, "height_pt": rect.height})
    return out


def _worker_handle(payload: dict[str, Any]) -> dict[str, Any]:
    from openstem.workers import WorkerError

    try:
        pages = pdf_pages_to_png(Path(payload["src"]), int(payload.get("dpi", MATH_DPI)))
    except ValueError as exc:
        raise WorkerError([f"Could not convert math images: {exc}"]) from None
    return {"ok": True, "pages": pages}


WORKER = "openstem.render.hwpx"


def render_math_images(
    items: list[tuple[str, bool]], workdir: Path, *, hangul: bool, timeout: float
) -> tuple[list[Picture], list[str], list[str]]:
    """그림으로 넣을 수식들을 한 번에 컴파일해 PNG로 만든다. (그림들, errors, warnings).

    컴파일은 export와 같은 latex.compile_tex(tectonic --untrusted, 타임아웃)이고, 쪽 → PNG는 작업자에서 한다.
    """
    from openstem import sandbox
    from openstem.render import latex
    from openstem.tools.tikz import FILE_GUARD, HANGUL_SETUP

    if not items:
        return [], [], []
    if len(items) > MAX_MATH_IMAGES:
        return [], [f"Too many formulas to render as images ({len(items)}, max {MAX_MATH_IMAGES}). Export the document in parts."], []
    mathdir = Path(tempfile.mkdtemp(prefix="math-", dir=workdir))
    source = math_source(items, HANGUL_SETUP if hangul else "", FILE_GUARD)
    result = latex.compile_tex(source, mathdir, timeout=timeout, reruns=0)
    if not result.ok:
        return [], result.errors, result.warnings
    try:
        out = sandbox.run_python(WORKER, {"src": str(result.pdf_path)}, mathdir, sandbox.default_timeout())
    except sandbox.WorkerFailed as exc:
        return [], [f"Math image conversion failed: {exc}"], result.warnings
    if not out.get("ok"):
        return [], out.get("errors") or ["Math image conversion failed."], result.warnings
    pictures = []
    for page in out["pages"]:
        path = sandbox.worker_file(mathdir, page["png"])
        pictures.append(Picture(path, round(float(page["width_pt"]) * 100), round(float(page["height_pt"]) * 100)))
    if len(pictures) != len(items):
        return [], [f"Math images: expected {len(items)} pages, got {len(pictures)}."], result.warnings
    return pictures, [], result.warnings


if __name__ == "__main__":
    from openstem.workers import serve

    serve(_worker_handle)
