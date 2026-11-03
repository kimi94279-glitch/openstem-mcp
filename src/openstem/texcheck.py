"""TeX 코드 안전 검사. render_tikz(코드 전체)와 export_document(수식)가 컴파일 전에 쓴다.

tectonic --untrusted는 shell-escape만 끈다. TeX 코드는 여전히 절대 경로·상위 폴더의 파일을 읽고 쓸 수 있다.
그래서 컴파일 전에 코드를 TeX처럼 토큰으로 나눠 두 가지를 막는다.

1. 이름을 글자로 조립해 명령을 만드는 수단: \\csname, ^^ 표기, \\scantokens, catcode 바꾸기(\\makeatletter 등),
   허용 목록 밖의 \\begin{이름}(안에서 \\csname을 쓴다), LaTeX의 \\UseName·\\ExpandArgs.
2. 파일·외부에 닿는 명령: \\input, \\openout, \\write, \\special, \\XeTeX… 원시 명령, 그림·표 파일 읽기 등.
3. 그림 데이터를 파일·gnuplot에서 가져오는 문법: \\addplot(3)(+)[…] 와 TikZ plot[…] 뒤의 데이터 소스는
   coordinates·식만 허용한다(`check_plot_sources`). 옵션의 [ ]는 중괄호 짝을 맞춰 건너뛴다.

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
    # pgf·pgfplots에서 파일을 읽거나 쓰는 공개 명령(plot file·function·contour external이 안에서 쓴다)
    "pgfplotxyfile", "pgfplotxyzfile", "pgfplotgnuplot", "pgfplotscolormaptodatafile",
    "pgfplotsplothandlertofile",
}  # fmt: skip
_BANNED_PREFIXES = ("XeTeX", "pdf", "pgfplotstable")
# \begin{...}에 쓸 수 있는 환경
ENVIRONMENTS = {
    "tikzpicture", "circuitikz", "scope", "pgfonlayer", "axis", "semilogxaxis", "semilogyaxis", "loglogaxis",
    "polaraxis", "center", "flushleft", "flushright", "minipage", "tabular", "array", "itemize", "enumerate",
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "cases", "aligned",
    "alignedat", "gathered", "split", "align", "align*", "equation", "equation*", "gather", "gather*",
}  # fmt: skip
# 추가 방어선(정규식): \addplot table {파일}, file {파일}, graphics {파일}, plot file {파일}, gnuplot·외부 contour.
# 주된 검사는 토큰 기준의 check_plot_sources다.
_FILE_KEYWORD_RE = re.compile(
    r"\b(table|file|graphics|gnuplot|shell)\s*(?:\[[^\]]*\])?\s*\{|\b(contour\s+(?:external|gnuplot)|plot\s+to\s+file|raw\s+gnuplot)\b"
)
# \addplot·TikZ plot 뒤에 올 수 있는 데이터 소스. 이 밖의 낱말(table, file, function, gnuplot, graphics, shell …)은
# 파일을 읽거나 gnuplot 파일을 쓴다. 명령(매크로)이 오면 무엇으로 펼쳐질지 모르므로 거절한다.
_ADDPLOT_SOURCES = {"coordinates", "expression", "fill"}  # fill between: fillbetween 라이브러리, 파일 없음
_TIKZ_PLOT_SOURCES = {"coordinates"}
PLOT_SOURCE_HINT = "Give data inline with coordinates {(0,1) (1,2)} or as a formula, e.g. \\addplot {x^2}; or plot (\\x, {\\x^2})."
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


def _skip_space(tokens: list[Token], k: int) -> int:
    while k < len(tokens) and tokens[k].kind == "char" and tokens[k].text in " \t\n":
        k += 1
    return k


def _skip_brackets(tokens: list[Token], k: int) -> int | None:
    """tokens[k]가 [이면 짝이 맞는 ]까지 건너뛴다(중괄호 안의 ]는 세지 않는다). 짝이 없으면 None."""
    if k >= len(tokens) or tokens[k].kind != "char" or tokens[k].text != "[":
        return k
    depth = 0
    for j in range(k + 1, len(tokens)):
        tok = tokens[j]
        if tok.kind != "char":
            continue
        if tok.text == "{":
            depth += 1
        elif tok.text == "}":
            depth -= 1
            if depth < 0:
                return None
        elif tok.text == "]" and depth == 0:
            return j + 1
    return None


def _word_at(tokens: list[Token], k: int) -> str:
    """tokens[k]부터 이어지는 ASCII 글자 낱말(없으면 "")."""
    chars = []
    while k < len(tokens) and tokens[k].kind == "char" and tokens[k].text.isascii() and tokens[k].text.isalpha():
        chars.append(tokens[k].text)
        k += 1
    return "".join(chars)


def _source_error(tokens: list[Token], k: int, allowed: set[str], what: str) -> str | None:
    """데이터 소스 자리(tokens[k], 공백 건너뜀)를 본다. 허용하지 않는 것이면 에러 글."""
    k = _skip_space(tokens, k)
    if k >= len(tokens):
        return f"{what} has no data. {PLOT_SOURCE_HINT}"
    tok = tokens[k]
    if tok.kind == "cs":
        return f"{what} \\{tok.text}: data from a macro is not allowed (it may name a file). {PLOT_SOURCE_HINT}"
    if tok.text in "({":
        return None
    word = _word_at(tokens, k)
    if word in allowed:
        return None
    if word:
        return f"{what} {word}: reading files, tables or gnuplot is not allowed. {PLOT_SOURCE_HINT}"
    if what == "plot":
        return None  # TikZ는 c·f·( 밖의 글자에서 그냥 에러를 낸다. 파일에 닿지 않는다.
    return f"{what} {tok.text!r}: unsupported data source. {PLOT_SOURCE_HINT}"


def check_plot_sources(tokens: list[Token], where: str = "l.") -> list[str]:
    """\\addplot·\\addplot3(+)(옵션)(plot 옵션)과 TikZ plot(옵션) 뒤의 데이터 소스를 검사한다.

    pgfplots는 \\addplot[옵션] plot[옵션] 뒤의 첫 글자로 소스를 고르고(c·f·t·g·s·e·(·{), TikZ plot은 c·f·(를 본다.
    둘 다 다음 토큰을 펼치지 않고 보므로, 그 자리에 실제로 적힌 낱말이 허용 목록(coordinates·식)인지 보면 된다.
    낱말을 매크로로 나눠 쓰는 우회(TikZ 경로는 매크로를 펼친다)는 tools/tikz.py의 FILE_GUARD가 TeX 쪽에서 막는다.
    """
    errors: list[str] = []
    n = len(tokens)
    seen_plot: set[int] = set()  # \addplot … plot 의 plot(이미 본 것)
    for idx, tok in enumerate(tokens):
        if tok.kind == "cs" and tok.text == "addplot":
            k = _skip_space(tokens, idx + 1)
            if k < n and tokens[k].kind == "char" and tokens[k].text == "3":
                k = _skip_space(tokens, k + 1)
            if k < n and tokens[k].kind == "char" and tokens[k].text == "+":
                k = _skip_space(tokens, k + 1)
            k = _skip_brackets(tokens, k)
            if k is not None:
                k = _skip_space(tokens, k)
                if _word_at(tokens, k) == "plot":  # \addplot[…] plot[…] 소스
                    seen_plot.add(k)
                    k = _skip_brackets(tokens, _skip_space(tokens, k + 4))
            if k is None:
                err = "\\addplot: unbalanced [ ] options. " + PLOT_SOURCE_HINT
            else:
                err = _source_error(tokens, k, _ADDPLOT_SOURCES, "\\addplot")
            if err:
                errors.append(f"{where}{tok.line} {err}")
        elif tok.kind == "char" and tok.text == "p":
            # 글자 낱말 plot(앞뒤가 글자가 아닌 것). \addplot은 명령 토큰이라 여기 오지 않는다.
            if idx > 0 and tokens[idx - 1].kind == "char" and tokens[idx - 1].text.isascii() and tokens[idx - 1].text.isalpha():
                continue
            if idx in seen_plot or _word_at(tokens, idx) != "plot":
                continue
            k = _skip_brackets(tokens, _skip_space(tokens, idx + 4))
            if k is None:
                errors.append(f"{where}{tok.line} plot: unbalanced [ ] options. {PLOT_SOURCE_HINT}")
            elif err := _source_error(tokens, k, _TIKZ_PLOT_SOURCES, "plot"):
                errors.append(f"{where}{tok.line} {err}")
    if not errors:
        errors += _data_word_errors(tokens, where)
    return errors


# plot이 매크로로 나뉘어 안 보일 때(\a lot file {…})를 위한 덧붙임: 데이터 낱말 뒤([…]는 중괄호 짝을 맞춰 건너뜀)에
# {가 오면 거절한다. 뒤에 명령이 오는 경우는 글자("data table\\")와 구분할 수 없어 tools/tikz.py의 FILE_GUARD에 맡긴다.
_DATA_WORDS = {"table", "file", "graphics", "gnuplot", "shell", "function"}


def _data_word_errors(tokens: list[Token], where: str) -> list[str]:
    errors: list[str] = []
    for idx, tok in enumerate(tokens):
        if tok.kind != "char" or not (tok.text.isascii() and tok.text.isalpha()):
            continue
        if idx > 0 and tokens[idx - 1].kind == "char" and tokens[idx - 1].text.isascii() and tokens[idx - 1].text.isalpha():
            continue
        word = _word_at(tokens, idx)
        if word not in _DATA_WORDS:
            continue
        k = _skip_brackets(tokens, _skip_space(tokens, idx + len(word)))
        if k is None:
            continue
        k = _skip_space(tokens, k)
        if k < len(tokens) and tokens[k].kind == "char" and tokens[k].text == "{":
            errors.append(f"{where}{tok.line} {word}: syntax that reads or writes files or runs gnuplot is not allowed. {PLOT_SOURCE_HINT}")
    return errors


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
    plot_errors = check_plot_sources(tokens, where)
    errors += plot_errors
    if not plot_errors:  # 같은 자리를 두 번 알리지 않게, 토큰 검사가 조용할 때만 정규식 결과를 더한다
        for m in _FILE_KEYWORD_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            errors.append(
                f"{where}{line} {m.group(1) or m.group(2)}: syntax that reads or writes files or runs gnuplot is not allowed. "
                + PLOT_SOURCE_HINT
            )
    return errors[:MAX_ERRORS]
