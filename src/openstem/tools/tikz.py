"""render_tikz: TikZ 계열 코드를 그림(SVG·PDF·PNG)으로 렌더링한다."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from openstem import output, sandbox, texcheck
from openstem.render import latex, pages
from openstem.tools import ToolResult

ALLOWED_PACKAGES = ("tikz", "circuitikz", "pgfplots", "chemfig", "siunitx", "amsmath", "amssymb")
_BASE_PACKAGES = ("amsmath", "amssymb", "tikz")
PGFPLOTS_COMPAT = r"\pgfplotsset{compat=1.18}"
# 한글 지원은 서버가 넣는다(허용목록과 별개). 글꼴은 tectonic 번들에 든 은글꼴이라 OS와 상관없다.
HANGUL_SETUP = (
    r"\usepackage{xetexko}\setmainhangulfont{UnBatang.ttf}[BoldFont=UnBatangBold.ttf]"
    r"\setsanshangulfont{UnDotum.ttf}[BoldFont=UnDotumBold.ttf]\setmonohangulfont{UnDotum.ttf}"
    # Linux용 tectonic 0.17.0(musl 정적 빌드)에는 ICU 줄바꿈 데이터가 없어, 줄바꿈 locale("ko")이 켜져 있으면
    # XeTeX가 "failed to create linebreak iterator"로 죽는다. 한글은 띄어쓰기에서 줄을 바꾸고 나머지는 xetexko가 맡으므로 끈다.
    r'\AtBeginDocument{\XeTeXlinebreaklocale ""}'
)
# 그림 데이터를 파일·gnuplot에서 가져오는 pgf·pgfplots 내부 명령을 TeX 쪽에서 끈다(texcheck의 두 번째 방어선).
# TikZ 경로는 매크로를 펼쳐 읽으므로 \def\a{p}\draw \a lot file{…}처럼 낱말을 나눠 쓰면 토큰 검사로는 다 못 잡는다.
# 그래서 실제로 파일을 여는 쪽(plot file → \pgfplotxyfile, plot function → \pgfplotgnuplot, \addplot table·file·
# gnuplot·graphics·shell·function, contour external)을 에러로 바꾼다. 사용자 코드는 @ 이름을 쓸 수 없어(catcode
# 바꾸기 금지) 되돌리지 못한다. 패키지를 다 불러온 뒤, \begin{document} 바로 앞(같은 줄)에 넣는다.
FILE_GUARD = (
    r"\makeatletter"
    r"\def\openstem@nofile{\PackageError{openstem}{Plot data from files, tables or gnuplot is disabled."
    r" Use coordinates {(0,1) (1,2)} or a formula}{}}"
    + "".join(
        rf"\def\{name}{{\openstem@nofile}}"
        for name in (
            "pgfplotxyfile", "pgfplotxyzfile", "pgfplotgnuplot", "pgfplotscolormaptodatafile",
            "pgfplotsplothandlertofile", "pgfplots@addplotimpl@table", "pgfplots@addplotimpl@file",
            "pgfplots@addplotimpl@gnuplot", "pgfplots@addplotimpl@graphics", "pgfplots@addplotimpl@shell",
            "pgfplots@addplotimpl@function",
        )
    )
    + r"\makeatother"
)
# 허용목록 밖이지만 LLM이 습관처럼 넣는 패키지: 왜 필요 없는지 알려준다.
_UNNEEDED = {
    "xcolor": "tikz already loads it",
    "graphicx": "tikz already loads it",
    "inputenc": "UTF-8 is supported by default",
    "fontenc": "not needed (XeTeX engine)",
    "kotex": "the server sets up Korean automatically",
    "xetexko": "the server sets up Korean automatically",
    "fontspec": "the server chooses the fonts",
}

COMPILE_HINT = "The `!` line is the cause; `l.<N>` is line N of your code (from 1). Fix that line and call again."
INPUT_HINT = "Fix the input and call again. Allowed packages: " + ", ".join(ALLOWED_PACKAGES)
WARMUP_WAIT = 40.0
HANGUL_EXTRA_TIMEOUT = 20.0

DESCRIPTION = r"""Compile TikZ/circuitikz/pgfplots/chemfig code into a figure (SVG, PDF, PNG) and return a preview image. For circuits, free-body diagrams, vectors, geometry, block diagrams. Adapt a similar example from get_template first.
Example: {"code": "\\begin{circuitikz}\\draw (0,0) to[R, l=$R$] (2,0);\\end{circuitikz}"}
Bare \\draw commands are wrapped; packages, libraries and Korean labels are mostly automatic. Check the preview for missing parts, wrong connections, overlaps, arrow directions.
Saves your code as source_path (.tex) and a record_path (.json); to edit the figure later, read source_path and call again.
On failure: errors give the TeX cause (`!` line) and `l.N` = line N of your code; fix it and call again."""

MAX_CODE = 20_000

_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_HANGUL_RUN_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]+(?:\s+[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]+)*")
_INLINE_MATH_RE = re.compile(r"(?<!\\)\$([^$]+?)(?<!\\)\$")
_PICTURE_RE = re.compile(r"\\begin\s*\{\s*(tikzpicture|circuitikz)\s*\}|\\chemfig\b|\\schemestart\b|\\tikz\b")
_DOCCLASS_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_USEPACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_BEGIN_DOCUMENT_RE = re.compile(r"\\begin\s*\{\s*document\s*\}")
_LIBRARY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]*$")

# 코드에 쓰인 문법을 보고 TikZ 라이브러리를 자동으로 넣는다(빠뜨려서 컴파일이 실패하는 일이 가장 흔하다).
_LIBRARY_DETECT = {
    "calc": re.compile(r"\(\s*\$"),
    "arrows.meta": re.compile(r"\b(?:Stealth|Latex|Triangle|Kite|Straight Barb|Arc Barb|Hooks|Turned Square)\b"),
    "positioning": re.compile(r"\b(?:above|below|left|right)(?: (?:left|right))?\s*=\s*[^,\]]*?\bof\b"),
    "angles": re.compile(r"\bpic\s*(?:\[[^\]]*\])?\s*\{\s*(?:right\s+)?angle\s*="),
    "quotes": re.compile(r"\bpic\s*\[\s*\""),
    "decorations.pathmorphing": re.compile(r"decoration\s*=\s*\{?\s*(?:coil|zigzag|snake|saw|bumps|random steps)\b"),
    "decorations.pathreplacing": re.compile(r"decoration\s*=\s*\{?\s*(?:brace|calligraphic brace)\b"),
    "decorations.markings": re.compile(r"decoration\s*=\s*\{?\s*markings\b|mark\s*=\s*at position"),
    "patterns": re.compile(r"\bpattern\s*="),
    "intersections": re.compile(r"\bname (?:path|intersections)\b"),
    "shapes.geometric": re.compile(r"\b(?:regular polygon|diamond|trapezium|isosceles triangle|cylinder)\s*[,\]]"),
    "through": re.compile(r"\bcircle through\b"),
    "backgrounds": re.compile(r"\bon background layer\b|\bbackground rectangle\b"),
}
# 라이브러리가 빠졌을 때 나는 에러 → 넣을 라이브러리
_LIBRARY_HINTS = [
    ("Unknown arrow tip kind", "arrows.meta"),
    ("coordinate calculation", "calc"),
    ("Unknown function `of'", "positioning"),
    ("decoration", "decorations.pathmorphing (springs, zigzags) or decorations.pathreplacing (braces)"),
    ("pattern", "patterns"),
]

_AUTO_DETECT = {
    "circuitikz": re.compile(
        r"\\begin\s*\{\s*circuitikz\s*\}|\\ctikzset\b|"
        r"to\s*\[\s*(?:short|open|R|vR|C|L|V|I|sV|cV|vsource|isource|battery1?|D|led|lamp|bulb|ammeter|"
        r"voltmeter|ohmmeter|rmeter|switch|nos|ncs|spst|cspst|ospst|pR|fuse|generic|european|american)\s*[,\]=]|"
        r"node\s*\[\s*(?:op amp|ground|rground|npn|pnp|nmos|pmos|and port|or port|not port)\b"
    ),
    "pgfplots": re.compile(
        r"\\begin\s*\{\s*(?:axis|semilogxaxis|semilogyaxis|loglogaxis|polaraxis)\s*\}|\\addplot\b|\\pgfplotsset\b"
    ),
    "chemfig": re.compile(r"\\chemfig\b|\\schemestart\b"),
    "siunitx": re.compile(r"\\(?:SI|si|qty|unit|num|ang|SIrange|qtyrange|numrange)\s*(?:\[[^\]]*\])?\s*\{"),
}


class InputError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Document:
    source: str
    line_offset: int  # 생성 문서에서 code 첫 줄 앞에 있는 줄 수
    code_line_count: int
    warnings: list[str] = field(default_factory=list)


def hangul_to_text(math: str) -> str:
    """수식 안의 한글을 \\text{}로 감싼다. 수식 글꼴에는 한글이 없어 그대로 두면 글자가 빠진다."""
    return _HANGUL_RUN_RE.sub(lambda h: rf"\text{{{h.group(0)}}}", math)


def wrap_hangul_in_math(code: str) -> str:
    """code의 $...$ 안 한글을 \\text{}로 감싼다. 줄 수는 그대로."""
    if not _HANGUL_RE.search(code):
        return code
    return _INLINE_MATH_RE.sub(lambda m: "$" + hangul_to_text(m.group(1)) + "$", code)


def _check_packages(names: list[str]) -> list[str]:
    errors = []
    for name in names:
        if name in ALLOWED_PACKAGES:
            continue
        if name in _UNNEEDED:
            errors.append(f"Remove package '{name}': {_UNNEEDED[name]}.")
        else:
            errors.append(f"Package '{name}' is not allowed. Allowed: {', '.join(ALLOWED_PACKAGES)}")
    return errors


def _check_libraries(libraries: list[str]) -> list[str]:
    errors = []
    for lib in libraries:
        if lib in ALLOWED_PACKAGES:
            errors.append(f"'{lib}' is a package, not a tikz library. Put it in packages.")
        elif lib in texcheck.BANNED_LIBRARIES:
            errors.append(f"tikz library '{lib}' is not allowed (it writes files and runs external commands).")
        elif not _LIBRARY_NAME_RE.match(lib):
            errors.append(f"Invalid tikz library name: '{lib}' (e.g. arrows.meta, calc, positioning)")
    return errors


def _full_document(code: str, stripped: str, packages: list[str], libraries: list[str]) -> Document:
    errors = []
    m = _DOCCLASS_RE.search(stripped)
    if not m or m.group(1).strip() != "standalone":
        found = m.group(1).strip() if m else "?"
        errors.append(f"A full document must use \\documentclass{{standalone}} (got: {found}).")
    used = [p.strip() for m in _USEPACKAGE_RE.finditer(stripped) for p in m.group(1).split(",") if p.strip()]
    errors += _check_packages(used)
    if not _BEGIN_DOCUMENT_RE.search(stripped):
        errors.append("A full document needs \\begin{document} ... \\end{document}.")
    if errors:
        raise InputError(errors)

    # 줄 번호가 바뀌지 않게 \begin{document}와 같은 줄 앞에 끼워 넣는다.
    inject = FILE_GUARD
    if "pgfplots" in used and "compat=" not in stripped:
        inject += PGFPLOTS_COMPAT
    if _HANGUL_RE.search(stripped):
        inject += HANGUL_SETUP
        if "amsmath" in used:
            code = wrap_hangul_in_math(code)
    source = _BEGIN_DOCUMENT_RE.sub(lambda mm: inject + mm.group(0), code, count=1)

    warnings = []
    if packages or libraries:
        warnings.append("Got a full document, so packages and tikz_libraries were ignored. Put them in its preamble.")
    return Document(source=source, line_offset=0, code_line_count=code.count("\n") + 1, warnings=warnings)


def build_document(code: str, packages: list[str] | None = None, libraries: list[str] | None = None) -> Document:
    """code를 검사하고 standalone 문서로 만든다. 문제가 있으면 InputError."""
    packages = [p.strip() for p in (packages or []) if p.strip()]
    libraries = [lib.strip() for lib in (libraries or []) if lib.strip()]
    code = code.replace("\r\n", "\n").strip("\n")
    if not code.strip():
        raise InputError(["code is empty."])
    if len(code) > MAX_CODE:
        raise InputError([f"code is too long ({len(code)} chars, max {MAX_CODE}). Split the figure."])
    stripped = texcheck.strip_comments(code)
    document = bool(_DOCCLASS_RE.search(stripped))

    errors = texcheck.check(code, document=document) + _check_packages(packages) + _check_libraries(libraries)
    if errors:
        raise InputError(errors[: texcheck.MAX_ERRORS])
    if document:
        return _full_document(code, stripped, packages, libraries)

    wanted = set(_BASE_PACKAGES) | set(packages)
    wanted |= {name for name, pattern in _AUTO_DETECT.items() if pattern.search(stripped)}
    libraries = list(dict.fromkeys(libraries + [lib for lib, p in _LIBRARY_DETECT.items() if p.search(stripped)]))
    header = [r"\documentclass[border=4pt]{standalone}", r"\usepackage{amsmath,amssymb}"]
    header += [rf"\usepackage{{{p}}}" for p in ALLOWED_PACKAGES if p in wanted and p not in ("amsmath", "amssymb")]
    if "pgfplots" in wanted:
        header.append(PGFPLOTS_COMPAT)
    if libraries:
        header.append(rf"\usetikzlibrary{{{','.join(libraries)}}}")
    if _HANGUL_RE.search(stripped):
        header.append(HANGUL_SETUP)
        code = wrap_hangul_in_math(code)
    header.append(FILE_GUARD + r"\begin{document}")

    wrap = not _PICTURE_RE.search(stripped)
    if wrap:
        header.append(r"\begin{tikzpicture}")
    footer = ([r"\end{tikzpicture}"] if wrap else []) + [r"\end{document}"]
    source = "\n".join(header + [code] + footer) + "\n"
    return Document(source=source, line_offset=len(header), code_line_count=code.count("\n") + 1)


def warmup_source() -> str:
    """허용 패키지와 한글을 모두 쓰는 문서. 첫 실행 때 TeX 번들을 미리 받는 데 쓴다."""
    code = "\n".join(
        [
            r"\begin{circuitikz}",
            r"\draw (0,0) to[battery1, l=$V_0$] (0,2) to[R, l=\SI{10}{\ohm}] (3,2) to[C, l=축전기] (3,0) -- (0,0);",
            r"\end{circuitikz}",
            r"\begin{tikzpicture}\begin{axis}[width=4cm]\addplot {x^2};\end{axis}\end{tikzpicture}",
            r"\chemfig{H-C(-[2]H)(-[6]H)-H}",
            r"\textsf{돋움}",
        ]
    )
    # 템플릿이 쓰는 라이브러리와 자동 감지하는 라이브러리를 모두 미리 받아 둔다(처음 쓸 때 받느라 시간 초과가 나지 않게).
    from openstem.tools import templates

    libraries = sorted(set(_LIBRARY_DETECT) | {lib for t in templates.load_templates() for lib in t.libraries})
    return build_document(code, list(ALLOWED_PACKAGES), libraries).source


def render_tikz(
    code: str,
    packages: list[str] | None = None,
    tikz_libraries: list[str] | None = None,
    name: str | None = None,
) -> ToolResult:
    try:
        doc = build_document(code, packages, tikz_libraries)
    except InputError as exc:
        return ToolResult.failure(exc.errors, INPUT_HINT)

    if not latex.BACKGROUND_WARMUP.wait(WARMUP_WAIT):
        return ToolResult.failure(
            ["First-run setup: downloading TeX packages (once, 3-5 min). Tell the user, then call again with the same input in about a minute."]
        )

    # 한글 글꼴(xetexko + 은글꼴)을 불러오는 데만 바쁜 PC에서 10초 가까이 든다. 그만큼만 더 준다.
    timeout = sandbox.default_timeout() + (HANGUL_EXTRA_TIMEOUT if HANGUL_SETUP in doc.source else 0)
    try:
        with tempfile.TemporaryDirectory(prefix="openstem-tikz-") as tmp:
            result = latex.compile_tex(
                doc.source,
                Path(tmp),
                line_offset=doc.line_offset,
                code_line_count=doc.code_line_count,
                timeout=timeout,
            )
            if not result.ok:
                hint = None if result.timed_out or result.download_failed else COMPILE_HINT
                joined = " ".join(result.errors)
                missing = [lib for marker, lib in _LIBRARY_HINTS if marker in joined]
                if hint and missing:
                    hint += f" A library seems to be missing: add {missing[0]} to tikz_libraries."
                return ToolResult.failure(result.errors, hint, warnings=result.warnings)
            stem = output.new_stem(name)
            pdf_path = stem.with_suffix(".pdf")
            shutil.copyfile(result.pdf_path, pdf_path)
    except latex.TectonicNotFound as exc:
        return ToolResult.failure([str(exc)], "Pass these instructions on to the user.")

    try:
        converted = pages.figure(pdf_path, stem)
    except pages.ConvertError as exc:
        pdf_path.unlink(missing_ok=True)
        return ToolResult.failure(exc.errors, exc.hint)
    data = {
        "ok": True,
        "svg_path": str(converted.svg_path),
        "pdf_path": str(pdf_path),
        "png_path": str(converted.png_path),
        "size_cm": f"{converted.width_cm} x {converted.height_cm}",
    }
    warnings = doc.warnings + result.warnings + converted.warnings
    if warnings:
        data["warnings"] = warnings
    output.save_record(
        data,
        stem,
        "render_tikz",
        inputs={"code": code, "packages": packages, "tikz_libraries": tikz_libraries, "name": name},
        results={"size_cm": data["size_cm"]},
        versions={"tectonic": output.tectonic_version()},
        # 사용자가 보낸 code가 원본이다. 서버가 감싸 실제로 컴파일한 문서는 .full.tex.
        sources={".tex": code.replace("\r\n", "\n").strip("\n") + "\n", ".full.tex": doc.source},
    )
    data["check"] = "Look at the preview: check for missing parts, connections, overlapping labels, directions."
    return ToolResult(ok=True, data=data, preview_png=converted.preview_png)
