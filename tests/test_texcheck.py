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
        # 2026-11-03 점검: 정규식(_FILE_KEYWORD_RE)을 피해 \addplot·plot으로 파일을 읽고 gnuplot 파일을 쓰던 입력
        r"\begin{axis}\addplot table[comment chars={]}]{" + S + r"};\end{axis}",
        "\\def\\f{" + S + "}\n\\begin{axis}\\addplot table \\f;\\end{axis}",
        "\\def\\f{" + S + "}\n\\draw plot file \\f;",
        r"\begin{axis}\addplot gnuplot[id={]}]{x^2};\end{axis}",
        "\\def\\g{x^2}\n\\begin{axis}\\addplot gnuplot \\g;\\end{axis}",
        r"$\def\f{" + S + r"}\tikz\draw plot file \f;$",
        r"\draw plot function {x^2};",
        r"\draw[domain=0:1] plot[id={../x}] function {x};",
        r"\begin{axis}\addplot3+[a={]}] plot[b] file {" + S + r"};\end{axis}",
        r"\begin{axis}\addplot+ [red]  shell {echo};\end{axis}",
        r"\begin{axis}\addplot graphics[xmin=0] {" + S + r"};\end{axis}",
        r"\begin{axis}\addplot function {x};\end{axis}",
        r"\def\p{\addplot}\begin{axis}\p table {" + S + r"};\end{axis}",
        r"\let\p\addplot\begin{axis}\p table {" + S + r"};\end{axis}",
        r"\def\a{plot fi}\draw \a le{" + S + "};",
        r"\begin{axis}\addplot3[contour gnuplot] coordinates {(0,0,0)};\end{axis}",
        r"\pgfplotxyfile{" + S + "}",
        r"\pgfplotscolormaptodatafile{viridis}{../x.dat}",
    ],
)
def test_attacks_are_rejected(code):
    assert check(code), code


@pytest.mark.parametrize(
    "code, fragment",
    [
        (r"\begin{axis}\addplot table[x=a]{d.csv};\end{axis}", "\\addplot table"),
        ("\\def\\f{d}\n\\draw plot file \\f;", "plot file"),
        (r"\begin{axis}\addplot \data;\end{axis}", "\\addplot \\data"),
    ],
)
def test_plot_source_errors_point_to_coordinates(code, fragment):
    message = " ".join(check(code))
    assert fragment in message and "coordinates {(0,1) (1,2)}" in message


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
        r"\begin{axis}\addplot {x^2};\addplot+[red] coordinates {(0,1)};\addplot3[surf] {x*y};\end{axis}",
        "\\begin{axis}\\addplot[thick,\n  domain={-1:1}]\n  {f(x)};\\addplot expression {x};\\addplot (x, x^2);\\end{axis}",
        r"\begin{axis}\addplot[fill=blue] fill between[of=A and B];\addplot plot coordinates {(0,0)};\end{axis}",
        r"\draw[domain=0:1, samples=40, smooth] plot (\x, {\x^2}); \draw plot[mark=*] coordinates {(0,0) (1,1)};",
        r"\node {table, file, gnuplot, a plot};",
        r"\node[align=center] {data table\\ boxplot of x};",
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
