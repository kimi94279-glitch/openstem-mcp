"""TeX 코드 안전 검사. render_tikz(코드 전체)와 export_document(수식)가 컴파일 전에 쓴다.

tectonic --untrusted는 shell-escape만 끈다. TeX 코드는 여전히 절대 경로·상위 폴더의 파일을 읽고 쓸 수 있다.
그래서 컴파일 전에 코드를 TeX처럼 토큰으로 나눠 두 가지를 막는다.

1. 이름을 글자로 조립해 명령을 만드는 수단: \\csname, ^^ 표기, \\scantokens, catcode 바꾸기(\\makeatletter 등),
   허용 목록 밖의 \\begin{이름}(안에서 \\csname을 쓴다), LaTeX의 \\UseName·\\ExpandArgs.
2. 파일·외부에 닿는 명령: \\input, \\openout, \\write, \\special, \\XeTeX… 원시 명령, 그림·표 파일 읽기 등.

1이 막혀 있으면 2의 명령은 이름을 그대로 써야만 쓸 수 있으므로, 토큰 검사로 빠짐없이 잡힌다.
명령 이름은 ASCII 글자만 이어 읽는다. XeTeX은 한글 같은 유니코드 글자도 이어 읽으므로 우리가 읽는 이름은
XeTeX이 읽는 이름의 앞부분이다: 위험한 이름(전부 ASCII)을 놓치는 일은 없고, 지나치게 막을 수는 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ERRORS = 10

# 이름을 조립하거나 catcode를 바꾸는 명령
_ASSEMBLY = {
    "csname", "ifcsname", "endcsname", "scantokens", "catcode", "lccode", "uccode", "makeatletter",
    "ExplSyntaxOn", "UseName", "ExpandArgs", "NewCommandCopy", "Ucharcat", "Uchar",
}  # fmt: skip
# 파일·외부에 닿는 명령
_FILE_IO = {
    "input", "include", "includeonly", "InputIfFileExists", "IfFileExists", "LoadClass", "endinput",
    "openin", "openout", "closein", "closeout", "read", "readline", "write", "immediate", "newwrite", "newread",
    "special", "font", "filedump", "filesize", "filemoddate", "mdfivesum", "directlua", "ShellEscape",
    "includegraphics", "pgfimage", "pgfdeclareimage", "pgfdeclaremask", "verbatiminput", "VerbatimInput",
    "lstinputlisting", "tikzexternalize", "usepackage", "RequirePackage", "documentclass",
    "usetikzlibrary", "usepgfplotslibrary",
}  # fmt: skip
_BANNED_PREFIXES = ("XeTeX", "pdf", "pgfplotstable")
# \begin{...}에 쓸 수 있는 환경
ENVIRONMENTS = {
    "tikzpicture", "circuitikz", "scope", "pgfonlayer", "axis", "semilogxaxis", "semilogyaxis", "loglogaxis",
    "polaraxis", "center", "flushleft", "flushright", "minipage", "tabular", "array", "itemize", "enumerate",
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "cases", "aligned",
    "alignedat", "gathered", "split", "align", "align*", "equation", "equation*", "gather", "gather*",
}  # fmt: skip
# pgfplots·TikZ에서 파일을 읽는 문법: \addplot table {파일}, file {파일}, graphics {파일}, plot file {파일}
_FILE_KEYWORD_RE = re.compile(r"\b(table|file|graphics|gnuplot|shell)\s*(?:\[[^\]]*\])?\s*\{")
# 문서 전체 모드에서 머리말에 쓸 수 있는 명령(인자는 따로 검사한다)
PREAMBLE_COMMANDS = {"documentclass", "usepackage", "usetikzlibrary", "usepgfplotslibrary"}
_LIBRARY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]*$")
BANNED_LIBRARIES = {"external"}  # 파일을 쓰고 외부 명령을 부른다


@dataclass
class Token:
    kind: str  # cs(명령) | char
    text: str  # 명령이면 백슬래시 뺀 이름
    line: int  # 1부터
    pos: int  # code 안 위치


class TexCheckError(ValueError):
    pass


def strip_comments(code: str) -> str:
    """% 주석을 지운다(\\%는 글자). 줄 수는 그대로."""
    out, i = [], 0
    while i < len(code):
        ch = code[i]
        if ch == "\\" and i + 1 < len(code):
            out.append(code[i : i + 2])
            i += 2
        elif ch == "%":
            while i < len(code) and code[i] != "\n":
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokenize(code: str) -> list[Token]:
    """주석을 지운 code를 명령·글자 토큰으로 나눈다."""
    tokens: list[Token] = []
    i, line = 0, 1
    while i < len(code):
        ch = code[i]
        if ch == "\\" and i + 1 < len(code):
            j = i + 1
            if code[j].isascii() and code[j].isalpha():
                while j < len(code) and code[j].isascii() and code[j].isalpha():
                    j += 1
            else:
                j += 1
            tokens.append(Token("cs", code[i + 1 : j], line, i))
            i = j
            continue
        if ch == "\n":
            line += 1
        tokens.append(Token("char", ch, line, i))
        i += 1
    return tokens


def _group_text(tokens: list[Token], start: int) -> tuple[str | None, int]:
    """tokens[start]부터 공백을 건너 {글자들}을 읽는다. 명령이 섞이거나 중괄호가 없으면 None."""
    k = start
    while k < len(tokens) and tokens[k].kind == "char" and tokens[k].text in " \t\n":
        k += 1
    if k >= len(tokens) or tokens[k].text != "{" or tokens[k].kind != "char":
        return None, k
    k += 1
    chars = []
    while k < len(tokens) and not (tokens[k].kind == "char" and tokens[k].text == "}"):
        if tokens[k].kind == "cs":
            return None, k
        chars.append(tokens[k].text)
        k += 1
    return "".join(chars).strip(), k + 1


def _name_error(name: str) -> str | None:
    if name in _ASSEMBLY:
        return "commands that build command names or change catcodes are not allowed."
    if name in _FILE_IO or name.startswith(_BANNED_PREFIXES):
        return "commands that touch files or the outside are not allowed. Write the content directly."
    return None


def check(code: str, *, document: bool = False, where: str = "l.") -> list[str]:
    """위험한 것이 있으면 고칠 방법을 담은 에러들(최대 10개)을 돌려준다. 없으면 [].

    document=True(문서 전체 모드)면 머리말의 \\documentclass·\\usepackage·라이브러리 불러오기와
    \\begin{document}를 허용한다. 패키지 이름 허용목록은 호출하는 쪽이 따로 검사한다.
    where는 위치 표시 앞에 붙는다("l." → "l.3").
    """
    errors: list[str] = []
    text = strip_comments(code)
    if (pos := text.find("^^")) >= 0:
        line = text.count("\n", 0, pos) + 1
        errors.append(f"{where}{line} ^^: ^^ character notation is not allowed. Write the character itself.")
    tokens = tokenize(text)
    in_preamble = document
    for idx, tok in enumerate(tokens):
        if tok.kind != "cs":
            continue
        name = tok.text
        at = f"{where}{tok.line} \\{name}"
        if name in ("begin", "end"):
            env, _ = _group_text(tokens, idx + 1)
            allowed = ENVIRONMENTS | ({"document"} if document else set())
            if env is None:
                errors.append(f"{at}: write the environment name literally, e.g. \\{name}{{tikzpicture}}.")
            elif env not in allowed:
                errors.append(f"{at}{{{env}}}: environment '{env}' is not allowed. Allowed: {', '.join(sorted(allowed))}")
            elif name == "begin" and env == "document":
                in_preamble = False
            continue
        if in_preamble and name in PREAMBLE_COMMANDS:
            if name in ("usetikzlibrary", "usepgfplotslibrary"):
                libs, _ = _group_text(tokens, idx + 1)
                bad = [lib for lib in (libs or "").split(",") if not _LIBRARY_NAME_RE.match(lib.strip()) or lib.strip() in BANNED_LIBRARIES]
                if libs is None or bad:
                    errors.append(f"{at}: invalid or disallowed library name: {', '.join(bad) or '?'}")
            continue
        if name in PREAMBLE_COMMANDS and name in ("usetikzlibrary", "usepgfplotslibrary"):
            errors.append(f"{at}: do not load libraries inside code; pass them in tikz_libraries.")
            continue
        if name in PREAMBLE_COMMANDS:
            errors.append(f"{at}: do not put this in code; pass packages in the packages argument.")
            continue
        if reason := _name_error(name):
            errors.append(f"{at}: {reason}")
    for m in _FILE_KEYWORD_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        errors.append(
            f"{where}{line} {m.group(1)} {{: syntax that reads files or external data is not allowed. "
            "Give data inline with coordinates {(0,1) (1,2)} or use a formula."
        )
    return errors[:MAX_ERRORS]
