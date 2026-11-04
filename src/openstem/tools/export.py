"""export_document: 마크다운(수식·그림 포함)을 pdf·docx·pptx·hwpx(한글)로 내보낸다.

1. pandoc으로 마크다운을 문서 구조(JSON)로 읽는다(raw TeX·HTML·YAML 머리말은 끈다).
2. 구조 안의 모든 그림·수식을 검사한다: 그림은 결과물 폴더(OPENSTEM_OUTPUT_DIR) 안 파일만, 수식은
   texcheck(파일·외부 접근, 이름 조립 금지). 참조식 그림(![a][r]) 같은 어떤 문법으로 써도 구조에서는 다 보인다.
   pptx도 같은 검사를 거친다.
3. 배치(layout)별 규칙을 구조에서 검사한다(시험지: `# 정답과 해설` 제목, 카드: 2칸 표). 배치는 Lua 필터
   (render/*.lua)가 만들고, 서버는 필터가 쓸 표시(클래스)만 붙인다.
4. 그림을 작업 폴더로 옮겨(형식 변환은 작업자에서) 구조의 경로를 바꾼 뒤, 그 구조를 docx·pptx나 LaTeX로 바꾼다.
5. PDF는 render_tikz와 같은 latex.compile_tex(tectonic --untrusted, 타임아웃, 에러 줄 추출)로 만든다.
6. HWPX는 render/hwpx.py가 구조를 OWPML로 직접 쓴다(pandoc에 HWPX 작성기가 없다). 수식은 한컴 수식 또는 수식 그림.

참고문헌(bibliography): 자료·결과물 폴더 안의 .bib·.json(CSL JSON)·.yaml 파일을 서버가 검사해 작업 폴더로 복사하고,
1단계 pandoc(--sandbox)이 --citeproc으로 인용과 참고문헌 목록을 구조 안에 넣는다. 그 뒤 2단계 검사(수식·그림)를
참고문헌에도 똑같이 거친다. 서식 파일(CSL)은 render/csl/에 넣어 둔 것만 쓴다(사용자 CSL은 받지 않는다).
docx·pptx 기본 서식은 render/reference.docx·reference.pptx(render/refdocs.py가 만든다)이고 template 인자로 바꾼다.
"""

from __future__ import annotations

import html
import json
import math
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from openstem import output, sandbox, texcheck
from openstem.render import hwpx, latex, pages
from openstem.tools import ToolResult, documents
from openstem.tools.tikz import FILE_GUARD, HANGUL_SETUP, hangul_to_text

FORMATS = ("pdf", "docx", "pptx", "hwpx")
LAYOUTS = ("document", "cheatsheet", "exam", "flashcards")
LAYOUT_FORMATS = {"exam": ("pdf", "docx", "hwpx"), "flashcards": ("pdf",)}  # 이 밖의 배치는 모든 형식
MAX_MARKDOWN = 100_000
MAX_TITLE = 200
MAX_IMAGES = 30
MAX_CARDS = 200
DOCX_DPI = 300  # pptx도 같은 PNG를 쓴다
EXPORT_TIMEOUT = 60.0  # 문서는 그림 하나보다 크고, 처음엔 TeX 패키지를 더 받는다
# raw TeX·HTML·raw 속성·YAML 머리말을 꺼서, 마크다운 안의 \명령은 글자로 나오고 LaTeX로는 수식만 간다.
MARKDOWN_FORMAT = "markdown-raw_tex-raw_html-raw_attribute-yaml_metadata_block"
IMAGE_EXTS = (".svg", ".pdf", ".png", ".jpg", ".jpeg")
RENDER_DIR = Path(__file__).resolve().parents[1] / "render"
TABULAR_FILTER = RENDER_DIR / "tabular.lua"
EXAM_FILTER = RENDER_DIR / "exam.lua"
FLASHCARDS_FILTER = RENDER_DIR / "flashcards.lua"

# 참고문헌·서식 파일. chicago는 pandoc에 든 기본 서식(Chicago author-date)이라 파일이 없다.
CSL_DIR = RENDER_DIR / "csl"
CITATION_STYLES = ("chicago", "apa", "ieee", "mla")
BIBLIOGRAPHY_EXTS = (".bib", ".json", ".yaml", ".yml")
MAX_BIBLIOGRAPHY_BYTES = 2_000_000
REFERENCE_DOCS = {"docx": RENDER_DIR / "reference.docx", "pptx": RENDER_DIR / "reference.pptx"}
TEMPLATE_PART = {"docx": "word/styles.xml", "pptx": "ppt/presentation.xml"}  # 진짜 Word·PowerPoint 파일인지 보는 부분
MAX_TEMPLATE_BYTES = 30_000_000
# 마크다운 끝에 사용자가 직접 참고문헌 제목을 썼으면 pandoc 제목을 또 넣지 않는다
_REFS_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(참고[ \t]*문헌|references|bibliography|works[ \t]+cited)[ \t]*(\{[^}\n]*\})?[ \t]*$", re.I | re.M)
_MISSING_CITE_RE = re.compile(r"Citeproc: citation (\S+) not found")
_BIB_META = ("bibliography", "csl", "lang", "reference-section-title", "references", "nocite", "citation-style")

_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?://|[^\\/])")  # http:, file:, data: 등(C:\ 같은 드라이브는 아님)
# ![설명](경로)에서 경로에 공백이 있으면(Windows 사용자 이름 등) <경로>로 감싼다. 길이를 묶어 느려지지 않게 한다.
_SPACED_IMAGE_RE = re.compile(r"!\[([^\]\n]{0,300})\]\(([^)<>\"\n]{1,1000}\s[^)<>\"\n]{0,1000})\)")
_SLIDE_RE = re.compile(r"ppt/slides/slide\d+\.xml")
# 시험지 정답 제목: "정답과 해설", "정답 및 해설", "해설", "풀이", "모범 답안" 등. 영어 문서용 "Answers", "Solutions"도 받는다
_ANSWER_TITLE_RE = re.compile(r"^\s*(정답|해설|풀이|모범\s*답안|(?i:answers?|solutions?)\b)")
# Lua 필터가 만든 LaTeX가 로그에 남기는 표시
_ANSWERS_LOG_RE = re.compile(r"OPENSTEM-ANSWERS=(\d+)")
_SHRUNK_LOG_RE = re.compile(r"OPENSTEM-SHRUNK=(\d+)")

PDF_VARIABLES = {
    "document": ["papersize=a4", "geometry=margin=2.5cm", "colorlinks=true"],
    "cheatsheet": [
        "papersize=a4",
        "documentclass=extarticle",
        "fontsize=9pt",
        "classoption=twocolumn",
        "geometry=margin=1.2cm",
        "pagestyle=empty",
        "colorlinks=true",
    ],
    "exam": ["papersize=a4", "geometry=margin=2.2cm", "colorlinks=true"],
    "flashcards": ["papersize=a4", "geometry=margin=0cm", "pagestyle=empty"],
}

# 그림을 본문 그 자리에 둔다(떠다니다 다음 쪽으로 밀려 빈칸이 생기지 않게).
FIGURE_HERE = r"\usepackage{float}\floatplacement{figure}{H}"
KOREAN_NAMES = r"\renewcommand{\figurename}{그림}\renewcommand{\tablename}{표}"
# 치트시트: 제목 한 줄, 좁은 제목·목록 간격(한 쪽에 더 많이 담는다)
CHEATSHEET_COMPACT = r"""\usepackage[compact]{titlesec}
\titleformat*{\section}{\normalsize\bfseries}\titleformat*{\subsection}{\normalsize\bfseries}
\titlespacing*{\section}{0pt}{1ex}{0.4ex}\titlespacing*{\subsection}{0pt}{1ex}{0.4ex}
\usepackage{enumitem}\setlist{nosep,leftmargin=1.2em}
\makeatletter\renewcommand{\maketitle}{\twocolumn[\begin{center}{\large\bfseries\@title}\end{center}\vspace{0.5ex}]}\makeatother"""
# 시험지: 제목은 가운데, 문제(##)는 굵게, `# 정답과 해설`은 가운데 큰 제목(새 쪽은 exam.lua가 넣는다)
EXAM_HEADER = r"""\usepackage{needspace}
\usepackage[compact]{titlesec}
\titleformat*{\section}{\Large\bfseries\filcenter}\titleformat*{\subsection}{\large\bfseries}
\titlespacing*{\subsection}{0pt}{2ex}{1ex}
\makeatletter\renewcommand{\maketitle}{\begin{center}{\LARGE\bfseries\@title}\end{center}\vspace{0.5ex}}\makeatother"""
# 낱말 카드: 카드 안 글은 7.8cm 폭 상자에 넣고, 카드보다 높으면 줄인 뒤 로그에 카드 번호를 남긴다.
FLASHCARDS_HEADER = r"""\usepackage{tikz}
\renewcommand{\maketitle}{}
\setlength{\parindent}{0pt}
\newsavebox{\openstemcard}
\newcommand{\cardtext}[3]{%
  \sbox{\openstemcard}{\parbox{7.8cm}{\centering\sffamily #2 #3}}%
  \ifdim\dimexpr\ht\openstemcard+\dp\openstemcard\relax>5.4cm
    \typeout{OPENSTEM-SHRUNK=#1}\resizebox*{!}{5.4cm}{\usebox{\openstemcard}}%
  \else\usebox{\openstemcard}\fi}"""

# 배치별 (LaTeX 머리말, Lua 필터)
LAYOUT_SETUP: dict[str, tuple[str, Path | None]] = {
    "document": ("", None),
    "cheatsheet": (CHEATSHEET_COMPACT, TABULAR_FILTER),
    "exam": (EXAM_HEADER, EXAM_FILTER),
    "flashcards": (FLASHCARDS_HEADER, FLASHCARDS_FILTER),
}

EXAM_SPACE_DEFAULT = 4.0  # 문제 아래 풀이 공간(cm). exam.lua의 DEFAULT_SPACE와 같다
EXAM_SPACE_MAX = 20.0
SLIDE_MAX_LINES = 9  # 16:9 슬라이드 본문(24pt)에 들어가는 줄 수 어림
SLIDE_LINE_CHARS = 30  # 한 줄 글자 수 어림(한글·수식 섞임)
SLIDE_IMAGE_LINES = 6  # 그림 하나가 차지하는 줄 수 어림

INPUT_HINT = "Fix the input and call again. Insert figures as ![caption](path) with the svg_path/png_path returned by render_* tools."
COMPILE_HINT = "The `!` line is the cause. Find what it shows (usually math) in your markdown and fix it. l.<N> refers to the converted LaTeX; ignore it."
EXAM_EXAMPLE = "## Problem 1 (5 pts)\\n\\nProblem text\\n\\n## Problem 2 (5 pts) {space=1}\\n\\n…\\n\\n# 정답과 해설\\n\\n## Problem 1\\n\\n**Answer** …\\n\\nSolution …"
FLASHCARDS_EXAMPLE = "## Kinematics\\n\\n| Front | Back |\\n|---|---|\\n| Displacement at constant acceleration | $s = v_0 t + \\\\tfrac12 a t^2$ |"

DESCRIPTION = r"""Save markdown (math $...$, images ![caption](path from render_*/plot_*), tables) as pdf, docx, pptx or hwpx; pdf returns a first-page preview.
Example: {"markdown": "## Summary\n$t=\\sqrt{2h/g}$", "format": "pdf", "title": "Mechanics"}
hwpx: Korean Hangul (한글) file for school submissions, layout document or exam only; simple math becomes editable Hangul equations, the rest math images.
pptx: each ## heading is a slide. layout: cheatsheet (one A4 page, pdf), exam (## problems, then a `# 정답과 해설` or `# Answers` heading), flashcards (2-column table, pdf).
Citations: bibliography = a .bib, CSL .json or .yaml file in the materials folder; cite as [@key], [@key, p. 3] or @key. A reference list is added at the end. citation_style: chicago (default, author-date), apa, ieee, mla.
docx/pptx use a clean built-in style (Malgun Gothic, 16:9 slides); template = a .docx/.pptx in the materials folder to use its styles instead.
The markdown is saved as source_path (.md, image paths relative to the output folder) with a record_path (.json); to revise later, read source_path and export again.
On failure: follow errors; for TeX errors fix the math shown in the `!` line."""


class InputError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def pandoc_path() -> str:
    try:
        import pypandoc

        return pypandoc.get_pandoc_path()
    except (ImportError, OSError) as exc:
        raise InputError([f"pandoc not found ({exc}). The pypandoc_binary package is required."]) from None


def validate(markdown: str, fmt: str, title: str | None, layout: str) -> list[str]:
    errors = []
    if fmt not in FORMATS:
        errors.append(f"format must be one of: {', '.join(FORMATS)}.")
    if layout not in LAYOUTS:
        errors.append(f"layout must be one of: {', '.join(LAYOUTS)}.")
    elif fmt == "hwpx" and layout not in HWPX_LAYOUTS:
        errors.append(f'format="hwpx" supports layout {" or ".join(HWPX_LAYOUTS)} only (use format="pdf" for {layout}).')
    elif fmt in FORMATS and fmt != "hwpx" and fmt not in LAYOUT_FORMATS.get(layout, FORMATS):
        if layout == "flashcards":
            errors.append('layout="flashcards" needs format="pdf" (it makes a card PDF plus a .txt for flashcard apps).')
        else:
            errors.append(f'layout="{layout}" works only with format {" or ".join(LAYOUT_FORMATS[layout])}.')
    if not markdown or not markdown.strip():
        errors.append("markdown is empty.")
    elif len(markdown) > MAX_MARKDOWN:
        errors.append(f"markdown is too long ({len(markdown)} chars, max {MAX_MARKDOWN}).")
    if title and len(title) > MAX_TITLE:
        errors.append(f"title is too long (max {MAX_TITLE} chars).")
    return errors


def _resolve_image(raw: str, base: Path) -> Path:
    raw = unquote(raw.strip())  # pandoc은 <경로> 속 공백을 %20으로 바꾼다. 풀어도 아래에서 폴더 밖은 막힌다.
    if _URL_RE.match(raw):
        raise InputError([f"Images from URLs or special addresses are not allowed: {raw[:80]}. Use a file saved by a render_* tool."])
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if base.resolve() not in path.parents:
        raise InputError([f"Images must be files inside the output folder ({base}): {raw[:200]}"])
    if path.suffix.lower() not in IMAGE_EXTS:
        raise InputError([f"Not an image format: {raw[:200]} (svg, pdf, png, jpg only)"])
    if not path.is_file():
        raise InputError([f"Image file not found: {raw[:200]}"])
    return path


def _walk(node: Any, visit: Callable[[dict[str, Any]], None]) -> None:
    """pandoc JSON 구조의 모든 요소(dict, "t" 키)를 돈다."""
    if isinstance(node, dict):
        if "t" in node:
            visit(node)
        for value in node.values():
            _walk(value, visit)
    elif isinstance(node, list):
        for value in node:
            _walk(value, visit)


def _text(node: Any) -> str:
    """요소 안의 글자를 이어 붙인다(수식·코드는 원문 그대로). 제목 비교와 분량 어림에 쓴다."""
    parts: list[str] = []

    def visit(n: dict[str, Any]) -> None:
        kind = n["t"]
        if kind == "Str":
            parts.append(n["c"])
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            parts.append(" ")
        elif kind in ("Math", "Code", "CodeBlock"):
            parts.append(n["c"][1])

    _walk(node, visit)
    return "".join(parts)


def _has(node: Any, kind: str) -> bool:
    found = []
    _walk(node, lambda n: found.append(n) if n["t"] == kind else None)
    return bool(found)


def _braces_balanced(tex: str) -> bool:
    depth = 0
    text = re.sub(r"\\[{}\\]", "", tex)
    for ch in text:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


def check_ast(ast: dict[str, Any], base: Path) -> tuple[list[str], list[tuple[dict[str, Any], Path]]]:
    """구조 안의 수식·그림을 검사한다. (errors, [(그림 요소, 원본 경로)]). 수식 안 한글은 \\text{}로 감싼다."""
    errors: list[str] = []
    images: list[tuple[dict[str, Any], Path]] = []

    def visit(node: dict[str, Any]) -> None:
        kind = node["t"]
        if kind in ("RawInline", "RawBlock"):
            errors.append("Raw TeX/HTML blocks are not allowed. Write math as $...$.")
        elif kind == "Math":
            tex = node["c"][1]
            short = tex if len(tex) <= 40 else tex[:40] + "…"
            errors.extend(f"Math ${short}$ — {e.split(' ', 1)[1]}" for e in texcheck.check(tex, where=""))
            if not _braces_balanced(tex):
                errors.append(f"Math ${short}$ has unbalanced braces {{ }}.")
            node["c"][1] = hangul_to_text(tex)
        elif kind == "Image":
            try:
                images.append((node, _resolve_image(node["c"][2][0], base)))
            except InputError as exc:
                errors.extend(exc.errors)

    _walk(ast, visit)
    if len(images) > MAX_IMAGES:
        errors.append(f"At most {MAX_IMAGES} images.")
    return errors[:10], images


# ── 배치(layout)별 검사 ─────────────────────────────────────────────
# 검사와 에러 메시지는 여기서 하고, Lua 필터가 알아볼 수 있게 제목에 클래스를 붙인다.


def _header(block: dict[str, Any], level: int) -> bool:
    return block["t"] == "Header" and block["c"][0] == level


def _add_class(block: dict[str, Any], name: str) -> None:
    block["c"][1][1].append(name)  # Header c = [level, [id, classes, [[key, value]]], inlines]


def prepare_exam(ast: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    """시험지 규칙을 검사하고 문제·정답 제목에 표시를 붙인다. (errors, warnings, 결과에 넣을 값)."""
    blocks = ast["blocks"]
    answer_at = [i for i, b in enumerate(blocks) if _header(b, 1) and _ANSWER_TITLE_RE.match(_text(b["c"][2]))]
    if not answer_at:
        return [
            "The exam layout needs a `# 정답과 해설` heading (single #; `# Answers` also works) after all problems, "
            f"with answers and solutions below it in the same order under `## Problem 1`-style headings. Example: {EXAM_EXAMPLE}"
        ], [], {}
    if len(answer_at) > 1:
        return [f"Found {len(answer_at)} answer headings like `# 정답과 해설`. Keep only one and put all answers and solutions under it."], [], {}
    split = answer_at[0]
    problems = [b for b in blocks[:split] if _header(b, 2)]
    answers = [b for b in blocks[split + 1 :] if _header(b, 2)]
    if not problems:
        return [f"No problems. Before `# 정답과 해설`, start each problem with a ## heading like `## Problem 1 (5 pts)`. Example: {EXAM_EXAMPLE}"], [], {}

    errors: list[str] = []
    for number, block in enumerate(problems, start=1):
        _add_class(block, "openstem-problem")
        pairs = block["c"][1][2]
        for pair in pairs:
            if pair[0] != "space":
                continue
            try:
                space = float(pair[1])
            except ValueError:
                space = -1.0
            if not 0 <= space <= EXAM_SPACE_MAX:
                errors.append(f"Problem {number} space={pair[1]}: solution space must be a number from 0 to {EXAM_SPACE_MAX:.0f} (cm). Example: {{space=6}}")
            pair[1] = str(space)
    _add_class(blocks[split], "openstem-answers")

    warnings = []
    if len(answers) != len(problems):
        warnings.append(
            f"{len(problems)} problems, but {len(answers)} ## heading(s) under `# 정답과 해설`. "
            "Give each problem one `## Problem N` heading there, in the same order, with its answer and solution."
        )
    return errors, warnings, {"problems": len(problems)}


def _inlines_html(inlines: list[dict[str, Any]]) -> str:
    """카드 한 면(pandoc 인라인)을 카드 앱이 읽는 HTML로. 수식은 \\(...\\)·\\[...\\]로 둔다."""
    tags = {"Strong": "b", "Emph": "i", "Underline": "u", "Strikeout": "s", "Superscript": "sup", "Subscript": "sub"}
    out = []
    for node in inlines:
        kind, c = node["t"], node.get("c")
        if kind == "Str":
            out.append(html.escape(c, quote=False))
        elif kind in ("Space", "SoftBreak"):
            out.append(" ")
        elif kind == "LineBreak":
            out.append("<br>")
        elif kind == "Math":
            tex = html.escape(c[1], quote=False)
            out.append(rf"\({tex}\)" if c[0]["t"] == "InlineMath" else rf"\[{tex}\]")
        elif kind == "Code":
            out.append(f"<code>{html.escape(c[1], quote=False)}</code>")
        elif kind in tags:
            out.append(f"<{tags[kind]}>{_inlines_html(c)}</{tags[kind]}>")
        elif kind == "SmallCaps":
            out.append(_inlines_html(c))
        elif kind in ("Span", "Link", "Cite"):  # c = [속성·인용, 인라인, ...]
            out.append(_inlines_html(c[1]))
        elif kind == "Quoted":
            left, right = ("“", "”") if c[0]["t"] == "DoubleQuote" else ("‘", "’")
            out.append(left + _inlines_html(c[1]) + right)
        # Image(카드 앱용 파일에는 넣지 않는다), Note 등은 버린다
    return "".join(out)


def _blocks_html(blocks: list[dict[str, Any]]) -> str:
    parts = []
    for block in blocks:
        kind = block["t"]
        if kind in ("Plain", "Para"):
            parts.append(_inlines_html(block["c"]))
        elif kind in ("BulletList", "OrderedList"):
            items = block["c"] if kind == "BulletList" else block["c"][1]
            parts.extend("• " + _blocks_html(item) for item in items)
        else:
            parts.append(html.escape(_text(block), quote=False))
    return "<br>".join(p for p in parts if p).replace("\t", " ").replace("\n", " ")


def prepare_flashcards(ast: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    """카드 표를 검사하고 카드 앱용 탭 구분 텍스트를 만든다. (errors, warnings, {"cards", "tsv"})."""
    errors: list[str] = []
    cards: list[tuple[str, str]] = []
    tables = dropped = with_images = 0
    for block in ast["blocks"]:
        if block["t"] == "Header":
            continue
        if block["t"] != "Table":
            dropped += 1
            continue
        tables += 1
        _, _, colspecs, _, bodies, _ = block["c"]
        if len(colspecs) != 2:
            errors.append(f"Table {tables} has {len(colspecs)} columns. A card table needs exactly 2: | Front | Back |.")
            continue
        for body in bodies:
            for row in body[3]:  # body = [attr, 행 머리 칸 수, 중간 머리 행들, 본문 행들]; row = [attr, cells]
                front, back = (cell[4] for cell in row[1])  # cell = [attr, 정렬, 행 병합, 열 병합, blocks]
                if not front and not back:
                    continue
                number = len(cards) + 1
                if not front or not back:
                    errors.append(f"Card {number}: the {'front' if not front else 'back'} is empty.")
                with_images += _has([front, back], "Image")
                cards.append((_blocks_html(front), _blocks_html(back)))
    if not tables:
        errors.append(f"No card table. Each row of a 2-column table is one card (the first row is the header). Example: {FLASHCARDS_EXAMPLE}")
    elif not cards and not errors:
        errors.append("The table has no card rows. Below the header row (| Front | Back |) and separator row (|---|---|), write one card per row.")
    if len(cards) > MAX_CARDS:
        errors.append(f"Too many cards ({len(cards)}, max {MAX_CARDS}). Export them in parts.")
    warnings = []
    if dropped:
        warnings.append(f"Text outside tables ({dropped} paragraphs/lists) was left out. Write card content as table rows.")
    if with_images:
        warnings.append(f"{with_images} card(s) have images: images are only in the PDF, not in the flashcard-app .txt.")
    tsv = "#separator:tab\n#html:true\n" + "".join(f"{front}\t{back}\n" for front, back in cards)
    return errors[:10], warnings, {"cards": len(cards), "tsv": tsv}


def prepare_layout(ast: dict[str, Any], layout: str) -> tuple[list[str], list[str], dict[str, Any]]:
    if layout == "exam":
        return prepare_exam(ast)
    if layout == "flashcards":
        return prepare_flashcards(ast)
    return [], [], {}


def _block_lines(block: dict[str, Any]) -> int:
    """슬라이드에서 이 블록이 차지할 줄 수를 어림한다."""
    kind = block["t"]
    if kind in ("BulletList", "OrderedList"):
        items = block["c"] if kind == "BulletList" else block["c"][1]
        return sum(_block_lines(b) for item in items for b in item)
    if kind in ("Div", "BlockQuote"):
        if kind == "Div" and "notes" in block["c"][0][1]:
            return 0  # 발표자 노트는 슬라이드에 나오지 않는다
        return sum(_block_lines(b) for b in (block["c"][1] if kind == "Div" else block["c"]))
    if kind == "Figure" or _has(block, "Image"):
        return SLIDE_IMAGE_LINES
    if kind == "Table":
        _, _, _, head, bodies, _ = block["c"]
        return len(head[1]) + sum(len(body[3]) for body in bodies)
    if kind == "CodeBlock":
        return block["c"][1].count("\n") + 1
    return max(1, math.ceil(len(_text(block)) / SLIDE_LINE_CHARS))


def slide_warnings(ast: dict[str, Any]) -> list[str]:
    """미리보기가 없는 pptx 대신, 넘칠 것 같은 슬라이드와 ## 제목이 없는 문서를 알려준다."""
    slides: list[tuple[str, int]] = []
    title, lines, has_slide_heading = "", 0, False
    for block in ast["blocks"]:
        if block["t"] == "Header" and block["c"][0] <= 2:
            if title or lines:
                slides.append((title, lines))
            title, lines = _text(block["c"][2]), 0
            has_slide_heading |= block["c"][0] == 2
        else:
            lines += _block_lines(block)
    slides.append((title, lines))
    warnings = []
    if not has_slide_heading:
        warnings.append("No ## headings, so the slides were not split. Start each slide with a `## Title`.")
    crowded = [f"'{t[:30] or '(untitled)'}' (~{n} lines)" for t, n in slides if n > SLIDE_MAX_LINES]
    if crowded:
        warnings.append(
            f"Slides likely to overflow: {', '.join(crowded[:5])}. "
            f"Keep about {SLIDE_MAX_LINES} lines (5-6 bullets) per slide and move the rest to the next slide."
        )
    return warnings


def count_slides(path: Path) -> int:
    with zipfile.ZipFile(path) as pptx:
        return sum(1 for name in pptx.namelist() if _SLIDE_RE.fullmatch(name))


# ── 변환 ────────────────────────────────────────────────────────────


def _sibling(path: Path, suffix: str) -> Path | None:
    candidate = path.with_suffix(suffix)
    return candidate if candidate.is_file() else None


def _prepare_image(src: Path, fmt: str, workdir: Path, index: int) -> str:
    """그림을 작업 폴더로 옮기며 형식을 맞추고, 문서에 쓸 파일 이름을 돌려준다.

    같은 이름의 PDF가 있으면(render_tikz·plot_function 결과) 그것을 원본으로 쓴다: 벡터라서 가장 깨끗하다.
    """
    vector = _sibling(src, ".pdf") or (src if src.suffix.lower() == ".svg" else None) or _sibling(src, ".svg")
    if fmt in ("docx", "pptx"):
        name = f"img{index}.png"
        if vector is not None:
            pages.to_png(vector, workdir / name, DOCX_DPI)
        else:
            name = f"img{index}{src.suffix.lower()}"
            shutil.copyfile(src, workdir / name)
        return name
    name = f"img{index}.pdf"
    if vector is not None and vector.suffix.lower() == ".pdf":
        shutil.copyfile(vector, workdir / name)
    elif vector is not None:
        pages.to_pdf(vector, workdir / name)
    else:
        name = f"img{index}{src.suffix.lower()}"
        shutil.copyfile(src, workdir / name)
    return name


def _pandoc(args: list[str], workdir: Path) -> tuple[list[str], list[str]]:
    """pandoc을 돌린다. (errors, warnings)."""
    res = sandbox.run([pandoc_path(), *args], cwd=workdir, timeout=EXPORT_TIMEOUT)
    lines = [ln.strip() for ln in res.stderr.splitlines() if ln.strip()]
    warnings = [ln.removeprefix("[WARNING] ") for ln in lines if ln.startswith("[WARNING]")][:5]
    if res.timed_out:
        return [f"pandoc conversion timed out ({EXPORT_TIMEOUT:.0f} s). Export the document in parts."], warnings
    if res.returncode != 0:
        return [ln for ln in lines if not ln.startswith("[WARNING]")][:10] or ["pandoc conversion failed."], warnings
    return [], warnings


def parse_markdown(markdown: str, workdir: Path, citeproc: list[str] | None = None) -> dict[str, Any]:
    """마크다운을 pandoc 문서 구조(JSON)로 읽는다. 이 단계의 pandoc은 --sandbox로 파일을 더 읽지 않는다."""
    return _parse(markdown, workdir, citeproc)[0]


def _parse(markdown: str, workdir: Path, citeproc: list[str] | None) -> tuple[dict[str, Any], list[str]]:
    """parse_markdown + pandoc 경고. citeproc 인자가 있으면 같은 --sandbox 단계에서 인용을 처리한다
    (--sandbox는 명령줄에 준 파일만 읽게 하고 네트워크를 막는다. 참고문헌·CSL은 작업 폴더에 복사해 둔 것이다)."""
    fixed = _SPACED_IMAGE_RE.sub(lambda m: f"![{m.group(1)}](<{m.group(2).strip()}>)", markdown)
    (workdir / "input.md").write_text(fixed, encoding="utf-8")
    args = ["input.md", "-f", MARKDOWN_FORMAT, "-t", "json", "-o", "ast.json", "--sandbox", *(citeproc or [])]
    errors, warnings = _pandoc(args, workdir)
    if errors:
        raise InputError(errors)
    return json.loads((workdir / "ast.json").read_text(encoding="utf-8")), warnings


# ── 참고문헌·서식 ───────────────────────────────────────────────────


def _materials_file(raw: str, what: str, exts: tuple[str, ...], max_bytes: int) -> Path:
    """자료·결과물 폴더 안의 파일(read_document와 같은 제한). 확장자·크기도 본다."""
    try:
        path = documents.resolve(raw)
    except ValueError:
        raise InputError([f"{what} must be a file inside the materials folder ({documents.input_dir()}): {raw[:200]}"]) from None
    if path.suffix.lower() not in exts:
        raise InputError([f"{what} must be a {'/'.join(exts)} file: {raw[:200]}"])
    if not path.is_file():
        raise InputError([f"{what} file not found: {raw[:200]}. Check the name with read_document (no path lists the folder)."])
    if path.stat().st_size > max_bytes:
        raise InputError([f"{what} file is too large (max {max_bytes // 1_000_000} MB): {raw[:200]}"])
    return path


def validate_extras(fmt: str, layout: str, bibliography: str | None, citation_style: str, template: str | None) -> list[str]:
    errors = []
    if citation_style not in CITATION_STYLES:
        errors.append(f"citation_style must be one of: {', '.join(CITATION_STYLES)}.")
    if bibliography and layout == "flashcards":
        errors.append("bibliography does not work with layout=flashcards (cards have no reference list).")
    if template and fmt not in REFERENCE_DOCS:
        errors.append("template works only with format docx or pptx (pdf has a fixed layout).")
    return errors


def citeproc_args(bibliography: str, citation_style: str, markdown: str, hangul: bool, workdir: Path) -> list[str]:
    """참고문헌 파일을 검사해 작업 폴더로 복사하고 1단계 pandoc에 줄 인자를 돌려준다."""
    src = _materials_file(bibliography, "bibliography", BIBLIOGRAPHY_EXTS, MAX_BIBLIOGRAPHY_BYTES)
    ext = ".yaml" if src.suffix.lower() == ".yml" else src.suffix.lower()
    shutil.copyfile(src, workdir / f"refs{ext}")
    args = ["--citeproc", f"--bibliography=refs{ext}"]
    if citation_style != "chicago":
        shutil.copyfile(CSL_DIR / f"{citation_style}.csl", workdir / "style.csl")
        args.append("--csl=style.csl")
    # lang=ko-KR은 넣지 않는다: CSL 한국어 낱말이 영어 문헌에도 "Lee 와/과 Park 2019 년"처럼 붙어 어색하다.
    # 한국어 문서에서는 목록 제목만 "참고문헌"으로 둔다.
    if not _REFS_HEADING_RE.search(markdown):
        args.append(f"--metadata=reference-section-title:{'참고문헌' if hangul else 'References'}")
    return args


def _plain(node: dict[str, Any]) -> int:
    """참고문헌·인용 안의 raw TeX/HTML(.bib의 모르는 \\명령 등)을 글자로 바꾼다. 바꾼 수."""
    count = 0

    def visit(n: dict[str, Any]) -> None:
        nonlocal count
        if n["t"] == "RawInline":
            n["t"], n["c"] = "Str", n["c"][1]
            count += 1
        elif n["t"] == "RawBlock":
            n["t"], n["c"] = "Plain", [{"t": "Str", "c": n["c"][1]}]
            count += 1

    _walk(node, visit)
    return count


def finish_citations(ast: dict[str, Any], fmt: str, cited: bool, pandoc_warnings: list[str]) -> tuple[list[str], dict[str, Any]]:
    """citeproc이 만든 구조를 정리한다. (warnings, {"references", "citations"})."""
    for key in _BIB_META:
        ast.get("meta", {}).pop(key, None)  # 2단계(LaTeX의 lang → polyglossia 등)에 넘기지 않는다
    cites: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    def visit(n: dict[str, Any]) -> None:
        if n["t"] == "Cite":
            cites.append(n)
        elif n["t"] == "Div" and n["c"][0][0] == "refs":
            refs.append(n)

    _walk(ast, visit)
    warnings: list[str] = []
    if not cited:
        if cites:
            warnings.append("Found citations like [@key] but no bibliography, so they are printed as typed. Pass bibliography=<.bib/.json/.yaml in the materials folder>.")
        return warnings, {}
    raw = sum(_plain(n) for n in [*cites, *refs])
    if fmt == "pptx":
        for block in ast["blocks"]:
            if block["t"] == "Header" and block["c"][1][0] == "bibliography":
                block["c"][0] = 2  # pandoc은 # 제목을 넣는다. pptx에서는 ## 제목이어야 슬라이드 한 장이 된다
    missing = list(dict.fromkeys(m.group(1) for w in pandoc_warnings if (m := _MISSING_CITE_RE.search(w))))
    if missing:
        warnings.append(
            f"Citation keys not in the bibliography: {', '.join(missing[:10])}. They show as bold 'key?' in the document; "
            "use keys from the bibliography file (read it with read_document) and export again."
        )
    if not cites:
        warnings.append("bibliography was given but the markdown has no citations, so no reference list was added. Cite as [@key].")
    if raw:
        warnings.append(f"{raw} unsupported TeX/HTML command(s) in the bibliography were printed as plain text.")
    entries = sum(len(r["c"][1]) for r in refs)
    return warnings, {"references": entries, "citations": len(cites)}


def template_args(fmt: str, template: str | None, workdir: Path) -> list[str]:
    """docx·pptx 서식 파일(사용자 template 또는 기본)을 작업 폴더에 두고 pandoc 인자를 돌려준다."""
    name = f"ref.{fmt}"
    if template:
        src = _materials_file(template, "template", (f".{fmt}",), MAX_TEMPLATE_BYTES)
        try:
            with zipfile.ZipFile(src) as z:
                z.getinfo(TEMPLATE_PART[fmt])
        except (zipfile.BadZipFile, KeyError, OSError):
            raise InputError([f"template is not a valid {fmt} file: {template[:200]}. Save it again from Word/PowerPoint."]) from None
    else:
        src = REFERENCE_DOCS[fmt]
    shutil.copyfile(src, workdir / name)
    return [f"--reference-doc={name}"]


def _write_ast(ast: dict[str, Any], workdir: Path) -> list[str]:
    (workdir / "checked.json").write_text(json.dumps(ast, ensure_ascii=False), encoding="utf-8")
    return ["checked.json", "-f", "json", "--resource-path=."]


def _title_args(title: str | None) -> list[str]:
    return ["--metadata", f"title={title}"] if title else []


def _filter_args(layout: str, workdir: Path) -> list[str]:
    """배치의 Lua 필터를 작업 폴더에 두고 pandoc 인자를 돌려준다."""
    lua = LAYOUT_SETUP[layout][1]
    if lua is None:
        return []
    shutil.copyfile(lua, workdir / lua.name)
    return [f"--lua-filter={lua.name}"]


def to_latex(ast: dict[str, Any], workdir: Path, title: str | None, layout: str, hangul: bool) -> tuple[list[str], list[str], str]:
    """검사를 마친 구조를 standalone LaTeX로 바꾼다. (errors, warnings, source)."""
    header = [FIGURE_HERE]
    if hangul:
        header += [HANGUL_SETUP, KOREAN_NAMES]
    if LAYOUT_SETUP[layout][0]:
        header.append(LAYOUT_SETUP[layout][0])
    # 카드 배치는 tikz를 불러오므로 수식 칸에서도 그림 데이터를 파일에서 읽을 수 있다. render_tikz와 같은 방어선을 둔다.
    header.append(FILE_GUARD)
    (workdir / "header.tex").write_text("\n".join(header) + "\n", encoding="utf-8")
    variables = [arg for v in PDF_VARIABLES[layout] for arg in ("-V", v)]
    args = [*_write_ast(ast, workdir), *_title_args(title), "-t", "latex", "-s", "-o", "doc.tex"]
    args += ["--include-in-header=header.tex", *_filter_args(layout, workdir), *variables]
    errors, warnings = _pandoc(args, workdir)
    source = "" if errors else (workdir / "doc.tex").read_text(encoding="utf-8")
    return errors, warnings, source


WARMUP_MARKDOWN = {
    "document": "## 준비\n\n한글과 수식 $x^2$, $$\\int_0^1 x\\,dx$$\n\n| 양 | 단위 |\n|---|---|\n| 길이 | m |\n",
    "exam": "## 문제 1 (5점)\n\n$x^2$을 미분하라.\n\n# 정답과 해설\n\n## 문제 1\n\n$2x$\n",
    "flashcards": "## 준비\n\n| 앞면 | 뒷면 |\n|---|---|\n| 뉴턴 제2법칙 | $F = ma$ |\n",
}


def warmup_sources() -> list[str]:
    """배치마다 쓰는 TeX 패키지를 미리 받아 두려고 컴파일할 문서들."""
    sources = []
    for layout in LAYOUTS:
        with tempfile.TemporaryDirectory(prefix="openstem-export-warmup-") as tmp:
            try:
                ast = parse_markdown(WARMUP_MARKDOWN.get(layout, WARMUP_MARKDOWN["document"]), Path(tmp))
            except InputError:
                continue
            prepare_layout(ast, layout)
            errors, _, source = to_latex(ast, Path(tmp), "준비", layout, hangul=True)
        if not errors:
            sources.append(source)
    return sources


def _read_log(workdir: Path) -> str:
    log = workdir / "figure.log"  # compile_tex는 --keep-logs로 로그를 남긴다
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


# ── HWPX(한글) ──────────────────────────────────────────────────────

HWPX_LAYOUTS = ("document", "exam")
HWPX_NOTE = "Made without opening it in Hancom Office (Hangul). Ask the user to open the .hwpx in Hangul and check the layout before submitting."


def export_hwpx(
    ast: dict[str, Any], workdir: Path, title: str | None, layout: str, dest: Path
) -> tuple[list[str], list[str], dict[str, Any]]:
    """검사를 마친 구조(그림은 작업 폴더의 PNG)를 HWPX로 쓴다. (errors, warnings, 결과 값).

    수식은 단순하면 한컴 수식 객체로, 아니면 render_tikz와 같은 컴파일 경로(texcheck를 마친 수식만)로 PNG를 만들어 넣는다.
    """
    maths: list[dict[str, Any]] = []
    _walk(ast, lambda n: maths.append(n) if n["t"] == "Math" else None)
    plan: dict[int, hwpx.Equation | hwpx.Picture | None] = {}
    need_images = []
    for node in maths:
        equation = hwpx.latex_to_hwpeq(node["c"][1])
        if equation is not None:
            plan[id(node)] = equation
        else:
            need_images.append(node)
    warnings: list[str] = []
    if need_images:
        if not latex.BACKGROUND_WARMUP.wait(40.0):
            return ["First-run setup: downloading TeX packages (once, 3-5 min). Tell the user, then call again in about a minute."], [], {}
        items = [(n["c"][1], n["c"][0]["t"] == "DisplayMath") for n in need_images]
        hangul = bool(_HANGUL_RE.search("".join(tex for tex, _ in items)))
        pictures, errors, compile_warnings = hwpx.render_math_images(items, workdir, hangul=hangul, timeout=EXPORT_TIMEOUT)
        if errors:
            return errors, compile_warnings, {"compile_failed": True}
        warnings += compile_warnings
        plan.update({id(n): pic for n, pic in zip(need_images, pictures, strict=True)})

    pictures_by_name: dict[str, hwpx.Picture] = {}
    images: list[dict[str, Any]] = []
    _walk(ast, lambda n: images.append(n) if n["t"] == "Image" else None)
    for node in images:
        name = node["c"][2][0]
        if name not in pictures_by_name:
            try:
                pictures_by_name[name] = hwpx.picture_from_file(workdir / name)
            except (OSError, ValueError):
                return [f"Could not read image {name} for hwpx (png and jpg only)."], [], {}

    info = hwpx.write_hwpx(ast, dest, title=title, pictures=pictures_by_name, math=lambda n: plan.get(id(n)), exam=layout == "exam")
    if info.equations or info.math_images:
        warnings.append(
            f"Math: {info.equations} formula(s) became editable Hangul equations, {info.math_images} were inserted as images "
            "(not editable in Hangul; complex math such as sums, integrals, matrices or accents is always an image)."
        )
    return [], warnings, {"equations": info.equations, "math_images": info.math_images}


def export_document(
    markdown: str,
    format: str = "pdf",
    title: str | None = None,
    layout: str = "document",
    name: str | None = None,
    bibliography: str | None = None,
    citation_style: str = "chicago",
    template: str | None = None,
) -> ToolResult:
    if errors := validate(markdown, format, title, layout) + validate_extras(format, layout, bibliography, citation_style, template):
        return ToolResult.failure(errors, INPUT_HINT)

    warnings: list[str] = []
    if layout == "cheatsheet" and format != "pdf":
        warnings.append(f"The cheatsheet layout is pdf-only; made a normal {format} document instead.")
        layout = "document"
    data: dict[str, Any] = {"ok": True, "format": format}
    if layout != "document":
        data["layout"] = layout
    preview: bytes | None = None
    hangul = layout in ("exam", "flashcards") or bool(_HANGUL_RE.search(markdown + (title or "")))
    with tempfile.TemporaryDirectory(prefix="openstem-export-") as tmp:
        workdir = Path(tmp)
        try:
            citeproc = citeproc_args(bibliography, citation_style, markdown, hangul, workdir) if bibliography else None
            ast, parse_warnings = _parse(markdown, workdir, citeproc)
            # 참고문헌에서 온 글(수식·그림·링크)도 아래 check_ast를 똑같이 거친다
            cite_warnings, cite_info = finish_citations(ast, format, bool(citeproc), parse_warnings)
            warnings += cite_warnings
            data.update(cite_info)
            errors, images = check_ast(ast, output.output_dir())
            if errors:
                return ToolResult.failure(errors, INPUT_HINT)
            errors, layout_warnings, info = prepare_layout(ast, layout)
            if errors:
                return ToolResult.failure(errors, INPUT_HINT)
            warnings += layout_warnings
            if format == "pptx":
                warnings += slide_warnings(ast)
            for i, (node, src) in enumerate(images, start=1):
                node["c"][2][0] = _prepare_image(src, "docx" if format == "hwpx" else format, workdir, i)
            stem = output.new_stem(name or title, default="cards" if layout == "flashcards" else "document")
            if format in ("docx", "pptx"):
                args = [*_write_ast(ast, workdir), *_title_args(title), "-t", format, "-o", f"out.{format}"]
                if format == "pptx":
                    args.append("--slide-level=2")
                args += template_args(format, template, workdir)
                args += _filter_args(layout, workdir)  # exam(docx)
                errors, pandoc_warnings = _pandoc(args, workdir)
                if errors:
                    return ToolResult.failure(errors, INPUT_HINT)
                path = stem.with_suffix(f".{format}")
                shutil.copyfile(workdir / f"out.{format}", path)
                if format == "pptx":
                    data["slides"] = count_slides(path)
            elif format == "hwpx":
                path = stem.with_suffix(".hwpx")
                errors, pandoc_warnings, hwpx_info = export_hwpx(ast, workdir, title, layout, path)
                if errors:
                    return ToolResult.failure(errors, COMPILE_HINT if hwpx_info.get("compile_failed") else INPUT_HINT)
                data.update(hwpx_info)
                data["note"] = HWPX_NOTE
            else:
                errors, pandoc_warnings, source = to_latex(ast, workdir, title, layout, hangul)
                if errors:
                    return ToolResult.failure(errors, INPUT_HINT)
                if not latex.BACKGROUND_WARMUP.wait(40.0):
                    return ToolResult.failure(
                        ["First-run setup: downloading TeX packages (once, 3-5 min). Tell the user, then call again in about a minute."]
                    )
                # pandoc 문서에는 상호 참조가 없어 한 번만 컴파일하면 된다(재컴파일하면 두 배 가까이 느리다).
                result = latex.compile_tex(source, workdir, timeout=EXPORT_TIMEOUT, reruns=0)
                if not result.ok:
                    hint = None if result.timed_out or result.download_failed else COMPILE_HINT
                    return ToolResult.failure(result.errors, hint, warnings=result.warnings)
                warnings += result.warnings
                log = _read_log(workdir)
                path = stem.with_suffix(".pdf")
                shutil.copyfile(result.pdf_path, path)
                preview, page_count = pages.preview(path)
                data["pages"] = page_count
                if layout == "cheatsheet" and page_count > 1:
                    warnings.append(f"The cheatsheet came out as {page_count} pages. Cut content to fit one page and call again.")
                if layout == "exam" and (m := _ANSWERS_LOG_RE.search(log)):
                    data["answer_page"] = int(m.group(1))
                if layout == "flashcards":
                    tsv_path = stem.with_suffix(".txt")
                    tsv_path.write_text(info["tsv"], encoding="utf-8", newline="\n")
                    data["tsv_path"] = str(tsv_path)
                    if shrunk := sorted({int(n) for n in _SHRUNK_LOG_RE.findall(log)}):
                        listed = ", ".join(map(str, shrunk[:15]))
                        warnings.append(f"Cards {listed}: text too long, so it was shrunk. Cut each side to one or two lines or split the card.")
        except latex.TectonicNotFound as exc:
            return ToolResult.failure([str(exc)], "Pass these instructions on to the user.")
        except (InputError, pages.ConvertError) as exc:
            return ToolResult.failure(exc.errors, getattr(exc, "hint", None))

    data["path"] = str(path)
    data["images"] = len(images)
    if layout == "exam":
        data["problems"] = info["problems"]
    if layout == "flashcards":
        data["cards"] = info["cards"]
    warnings += pandoc_warnings
    if warnings:
        data["warnings"] = warnings[:10]
    result_keys = ("format", "layout", "pages", "slides", "answer_page", "problems", "cards", "references", "citations", "equations", "math_images")
    output.save_record(
        data,
        stem,
        "export_document",
        inputs={
            "markdown": markdown, "format": format, "title": title, "layout": layout, "name": name,
            **({"bibliography": bibliography, "citation_style": citation_style} if bibliography else {}),
            **({"template": template} if template else {}),
        },
        results={**{k: data[k] for k in result_keys if k in data}, "images": [str(src) for _node, src in images]},
        versions={"pandoc": output.pandoc_version(), **({"tectonic": output.tectonic_version()} if format == "pdf" or data.get("math_images") else {})},
        # 이미지 경로는 결과물 폴더 기준 상대 경로로 바꿔 둔다(그대로 export_document에 다시 넘길 수 있다).
        sources={".md": output.portable(markdown)},
    )
    data["check"] = _check_text(format, layout, data)
    if "references" in data:
        data["check"] += f" The reference list at the end has {data['references']} entries; citations missing from it are listed in warnings."
    return ToolResult(ok=True, data=data, preview_png=preview)


def _check_text(fmt: str, layout: str, data: dict[str, Any]) -> str:
    """LLM이 결과를 확인할 방법."""
    if fmt == "pptx":
        return (
            "pptx has no preview. Check that slides matches the intended count (## headings + title slide + # sections) "
            "and that warnings list no overflowing slides."
        )
    if fmt == "docx":
        return "docx has no preview. To check the layout, also export the same markdown with format=pdf."
    if fmt == "hwpx":
        return "hwpx has no preview. To check the layout, also export the same markdown with format=pdf; tell the user to open the .hwpx in Hangul."
    if layout == "exam":
        page = data.get("answer_page")
        sheet = "page 1" if page == 2 else f"pages 1-{page - 1}" if page else ""
        split = f"Problems: {sheet}; answers and solutions start on page {page} (to print only the problems, print {sheet}). " if page else ""
        return f"{split}Check the preview (first page): problem numbers, math, solution space."
    if layout == "flashcards":
        return (
            "The preview is the first page (fronts). Check that card text and math fit in the boxes. Tell the user to print "
            "double-sided (flip on long edge) and cut along the dashed lines, and that tsv_path can be imported into a flashcard app."
        )
    return "Check the preview (first page): math, images and text (incl. Korean) render correctly."
