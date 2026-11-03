"""texcheck: TeX 코드 안전 검사. 보안 점검에서 실제로 뚫렸던 공격들과, 막으면 안 되는 보통 코드."""

import pytest

from openstem.texcheck import check, strip_comments

S = "C:/Users/me/secret.txt"


@pytest.mark.parametrize(
    "code",
    [
        r"\csname input\endcsname{" + S + "}",
        r"\immedi^^61te\openo^^75t\fh=" + S,
        r"\makeatletter\@@input{" + S + "}",
        r"\begin{input}{" + S + r"}\end{input}",
        r"\def\e{input}\begin\e{" + S + "}",
        r"\begin{filecontents}{x.tex}hi\end{filecontents}",
        r"\pgfimage{" + S + "}",
        r'\XeTeXpicfile "' + S + '"',
        "\\begin{axis}\\addplot table\n{" + S + "};\\end{axis}",
        r"\draw plot file {" + S + "};",
        r"\addplot graphics {" + S + "};",
        r"\edef\x{\string\in put}\scantokens\expandafter{\x}",
        r"\ExplSyntaxOn \tex_input:D x \ExplSyntaxOff",
        r'\font\x="[' + S + r']" \x a',
        r"\special{pdf:image (" + S + ")}",
        r"\UseName{input}{" + S + "}",
        r"\usetikzlibrary{../../x}",
        r"\catcode`\@=11",
        r"\filedump offset 0 length 100 {" + S + "}",
    ],
)
def test_attacks_are_rejected(code):
    assert check(code), code


def test_full_document_allows_preamble_but_not_input():
    doc = (
        "\\documentclass{standalone}\n\\usepackage{tikz}\n\\usetikzlibrary{calc}\n"
        "\\begin{document}\n\\tikz \\draw (0,0);\n\\end{document}"
    )
    assert check(doc, document=True) == []
    assert check(doc.replace("\\tikz", "\\input{x}\\tikz"), document=True)
    external = "\\documentclass{standalone}\n\\usetikzlibrary{external}\n\\begin{document}\\end{document}"
    assert check(external, document=True)


@pytest.mark.parametrize(
    "code",
    [
        r"\begin{tikzpicture}\draw[->, thick] (0,0) -- (1,1) node[above] {$\vec{F}$};\end{tikzpicture}",
        r"\begin{circuitikz}\draw (0,0) to[R, l=$R_1$] (2,0);\end{circuitikz}",
        r"\foreach \i in {1,...,5} {\fill (\i,0) circle (2pt);}",
        r"\newcommand\vf[1]{\vec{#1}}\node {$\vf{F}$};",
        r"\begin{axis}[xlabel=$x$]\addplot[domain=0:1] {x^2};\addplot coordinates {(0,1) (1,2)};\end{axis}",
        r"\node {$\begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}$};",
        r"\node {저항 $R$ 50\% 지점}; % 주석 속 \input{x}는 괜찮다",
        r"\pgfmathsetmacro\a{2*3}\draw (0,0) -- (\a,0);",
    ],
)
def test_ordinary_code_passes(code):
    assert check(code) == []


def test_errors_have_line_numbers():
    errors = check("\\draw (0,0);\n\n\\input{x}")
    assert errors and errors[0].startswith("l.3 \\input")


def test_strip_comments_keeps_escaped_percent_and_line_count():
    code = "a 50\\% b % comment\n\\\\% after linebreak\nc"
    stripped = strip_comments(code)
    assert stripped.count("\n") == code.count("\n")
    assert "50\\%" in stripped and "comment" not in stripped and "after" not in stripped
