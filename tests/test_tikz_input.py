"""render_tikz 입력 검사와 문서 생성. 컴파일 없이 돈다."""

import pytest

from openstem import output
from openstem.render.latex import extract_errors, extract_warnings
from openstem.tools.tikz import HANGUL_SETUP, InputError, build_document


def errors_of(code, packages=None, libraries=None):
    with pytest.raises(InputError) as info:
        build_document(code, packages, libraries)
    return " | ".join(info.value.errors)


def test_offset_points_at_first_code_line():
    code = "\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}"
    doc = build_document(code)
    lines = doc.source.split("\n")
    assert lines[doc.line_offset] == "\\begin{tikzpicture}"
    assert lines[doc.line_offset + 1] == "\\draw (0,0) -- (1,1);"
    assert doc.code_line_count == 3


def test_bare_commands_are_wrapped_in_tikzpicture():
    doc = build_document("\\draw (0,0) circle (1);")
    lines = doc.source.split("\n")
    assert lines[doc.line_offset - 1] == "\\begin{tikzpicture}"
    assert lines[doc.line_offset] == "\\draw (0,0) circle (1);"
    assert "\\end{tikzpicture}" in doc.source


@pytest.mark.parametrize(
    "code, package",
    [
        ("\\begin{circuitikz}\\draw (0,0) to[R] (2,0);\\end{circuitikz}", "circuitikz"),
        ("\\begin{tikzpicture}\\draw (0,0) to[R=$R_1$] (2,0);\\end{tikzpicture}", "circuitikz"),
        ("\\begin{tikzpicture}\\begin{axis}\\addplot {x^2};\\end{axis}\\end{tikzpicture}", "pgfplots"),
        ("\\chemfig{H-O-H}", "chemfig"),
        ("\\node {\\SI{3}{\\meter}};", "siunitx"),
    ],
)
def test_packages_are_detected_from_code(code, package):
    assert f"\\usepackage{{{package}}}" in build_document(code).source


def test_pgfplots_gets_compat_level():
    doc = build_document("\\begin{axis}\\addplot {x};\\end{axis}")
    assert "\\pgfplotsset{compat=" in doc.source


def test_chemfig_is_not_wrapped():
    assert "\\begin{tikzpicture}" not in build_document("\\chemfig{H-O-H}").source


def test_libraries_are_loaded():
    doc = build_document("\\draw (0,0) -- (1,0);", libraries=["arrows.meta", "calc"])
    assert "\\usetikzlibrary{arrows.meta,calc}" in doc.source


def test_hangul_turns_on_korean_fonts():
    assert HANGUL_SETUP in build_document("\\node {저항};").source
    assert HANGUL_SETUP not in build_document("\\node {R};").source


def test_disallowed_package_lists_allowlist():
    msg = errors_of("\\draw (0,0) -- (1,0);", packages=["minted"])
    assert "minted" in msg and "circuitikz" in msg


def test_unneeded_package_is_explained():
    assert "tikz already loads" in errors_of("\\draw (0,0) -- (1,0);", packages=["xcolor"])


def test_package_passed_as_library_is_explained():
    assert "Put it in packages" in errors_of("\\draw (0,0) -- (1,0);", libraries=["circuitikz"])


def test_usepackage_inside_snippet_is_rejected():
    assert "packages argument" in errors_of("\\usepackage{tikz}\n\\draw (0,0) -- (1,0);")


@pytest.mark.parametrize(
    "line",
    [
        "\\input{secret.txt}",
        "\\includegraphics{photo.png}",
        "\\immediate\\write18{calc}",
        "\\openin1=secret.txt",
        "\\addplot table {data.csv};",
        "\\addplot gnuplot {x^2};",
        "\\addplot file {data.dat};",
    ],
)
def test_file_access_is_blocked_with_line_number(line):
    msg = errors_of(f"\\begin{{tikzpicture}}\n{line}\n\\end{{tikzpicture}}")
    assert msg.startswith("l.2 ")


def test_blocked_command_in_comment_is_ignored():
    build_document("% \\input{x}\n\\draw (0,0) -- (1,0); % 50\\% 지점")


def test_table_is_rejected_with_coordinates_hint():
    """table {...}은 안의 내용이 파일 이름인지 데이터인지 가릴 수 없어 막고, coordinates를 안내한다."""
    assert "coordinates" in errors_of("\\begin{axis}\\addplot table {x y \\\\ 1 2 \\\\};\\end{axis}")


def test_libraries_are_detected_from_code():
    code = "\\draw[-{Stealth}] ($(0,0)!0.5!(1,1)$) -- (2,0) node[right=of a] {x};\n\\draw[decorate, decoration={coil}] (0,0) -- (1,0);"
    source = build_document(code).source
    for lib in ("arrows.meta", "calc", "positioning", "decorations.pathmorphing"):
        assert lib in source


def test_hangul_inside_math_is_wrapped_in_text():
    source = build_document("\\node {$F_{마찰}$};").source
    assert "$F_{\\text{마찰}}$" in source


def test_code_length_is_capped():
    assert "too long" in errors_of("\\draw (0,0);" * 3000)


def test_full_document_must_be_standalone():
    code = "\\documentclass{article}\n\\usepackage{tikz}\n\\begin{document}\nx\n\\end{document}"
    assert "standalone" in errors_of(code)


def test_full_document_package_allowlist():
    code = "\\documentclass{standalone}\n\\usepackage{minted}\n\\begin{document}\nx\n\\end{document}"
    assert "minted" in errors_of(code)


def test_full_document_keeps_line_numbers_when_injecting():
    code = "\\documentclass{standalone}\n\\usepackage{tikz}\n\\begin{document}\n\\tikz \\node {힘};\n\\end{document}"
    doc = build_document(code)
    assert doc.line_offset == 0
    assert doc.source.count("\n") == code.count("\n")
    assert HANGUL_SETUP in doc.source


def test_full_document_ignores_packages_argument_with_warning():
    code = "\\documentclass{standalone}\n\\usepackage{tikz}\n\\begin{document}\nx\n\\end{document}"
    assert build_document(code, packages=["pgfplots"]).warnings


def test_empty_code_is_rejected():
    assert "empty" in errors_of("  \n ")


# ── 로그에서 에러 뽑기 ─────────────────────────────────────────────

LOG = """\
This is XeTeX
! Undefined control sequence.
l.12 \\drwa
           (0,0) -- (0,1);
Here is how much of TeX's memory you used:
"""


def test_error_line_is_mapped_to_code_line():
    assert extract_errors(LOG, line_offset=9, code_line_count=5) == ["! Undefined control sequence.", "l.3 \\drwa"]


def test_error_in_header_and_after_code_are_labelled():
    assert extract_errors(LOG, line_offset=20, code_line_count=5)[1].startswith("(preamble)")
    assert extract_errors(LOG, line_offset=0, code_line_count=5)[1].startswith("(after the end of code")


def test_input_line_inside_message_is_mapped():
    log = "! LaTeX Error: \\begin{tikzpicture} on input line 10 ended by \\end{document}.\n"
    assert "on input line 2 " in extract_errors(log, line_offset=8, code_line_count=3)[0]


def test_wrapped_error_line_is_joined():
    long = "! Package pgfkeys Error: I do not know the key '/tikz/label/align', to which yo"
    assert len(long) == 79
    errors = extract_errors(long + "\nu passed 'straight'.\n")
    assert errors == [long + "u passed 'straight'."]


def test_errors_are_capped_at_ten():
    log = "\n".join(f"! error {i}" for i in range(30))
    assert len(extract_errors(log)) == 10


def test_missing_character_becomes_warning():
    log = "Missing character: There is no ☃ (U+2603) in font [lmroman10-regular]:mapping=tex-text;!\n"
    assert "☃" in extract_warnings(log)[0]


# ── 파일명 ─────────────────────────────────────────────────────────


def test_slugify_keeps_hangul():
    assert output.slugify("RC 회로/1번") == "RC-회로-1번"
    assert output.slugify("   ") == "figure"


def test_new_stem_does_not_overwrite(output_dir):
    first = output.new_stem("same")
    first.with_suffix(".svg").write_text("x")
    second = output.new_stem("same")
    assert second != first and second.parent == output_dir


# ── 파일 접근 막기: TeX 쪽 두 번째 방어선(FILE_GUARD) ─────────────────────
# texcheck를 거치지 않은 코드를 FILE_GUARD가 든 문서에 넣고 컴파일한다. 읽힐 파일은 시험이 만든 임시 파일이다.

SECRET = "OPENSTEM-SECRET-LINE"


def _compile_bypassing_check(code, workdir, pgfplots=False):
    from openstem.render import latex

    base = "\\begin{axis}\\addplot {x};\\end{axis}" if pgfplots else "\\draw (0,0) -- (1,1);"
    doc = build_document(base)
    assert base in doc.source
    result = latex.compile_tex(doc.source.replace(base, code), workdir)
    log = (workdir / "figure.log").read_text(encoding="utf-8", errors="replace") if (workdir / "figure.log").exists() else ""
    return result, log


@pytest.mark.parametrize(
    "code, pgfplots",
    [
        ("\\draw plot file {SECRET};", False),
        ("\\begin{axis}\\addplot table {SECRET};\\end{axis}", True),
        ("\\begin{axis}\\addplot file {SECRET};\\end{axis}", True),
    ],
)
def test_file_guard_stops_plot_file_reads(tmp_path, tectonic, code, pgfplots):
    secret = tmp_path / "secret.txt"
    secret.write_text(f"{SECRET} 1\n", encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()
    result, log = _compile_bypassing_check(code.replace("SECRET", secret.as_posix()), workdir, pgfplots)
    assert not result.ok
    assert SECRET not in " ".join(result.errors) and SECRET not in log
    assert "disabled" in " ".join(result.errors)


@pytest.mark.parametrize(
    "code, pgfplots",
    [
        ("\\draw[domain=0:1] plot[id=leak] function {x};", False),
        ("\\begin{axis}\\addplot gnuplot[id=leak] {x^2};\\end{axis}", True),
    ],
)
def test_file_guard_stops_gnuplot_writes(tmp_path, tectonic, code, pgfplots):
    result, _ = _compile_bypassing_check(code, tmp_path, pgfplots)
    assert not result.ok
    assert not list(tmp_path.glob("*.gnuplot")) and not list(tmp_path.glob("*.table"))


def test_plot_word_split_by_macro_is_caught_by_guard(tmp_path, tectonic):
    """TikZ 경로는 매크로를 펼쳐 읽어 \\a lot function 처럼 plot을 나눠 쓸 수 있다. texcheck가 못 보아도 TeX에서 막힌다."""
    from openstem.render import latex

    doc = build_document("\\def\\x{x}\\def\\a{p}\\draw[domain=0:1] \\a lot function\\x;")
    result = latex.compile_tex(doc.source, tmp_path)
    assert not result.ok and not list(tmp_path.glob("*.gnuplot"))


def test_file_guard_is_in_every_document():
    from openstem.tools.tikz import FILE_GUARD

    assert FILE_GUARD in build_document("\\draw (0,0);").source
    full = "\\documentclass{standalone}\n\\usepackage{pgfplots}\n\\begin{document}\n\\tikz \\draw (0,0);\n\\end{document}"
    doc = build_document(full)
    assert FILE_GUARD in doc.source and doc.source.index(FILE_GUARD) < doc.source.index("\\begin{document}")
    assert doc.source.count("\n") == full.count("\n")  # 줄 번호가 바뀌지 않는다
