"""MCP 서버 엔트리. 툴·프롬프트·리소스 등록만 한다(구현은 tools/)."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.server.mcpserver.resources import TextResource
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from openstem import __version__, sandbox
from openstem.lms import logs as lms_logs
from openstem.render import latex
from openstem.tools import ToolResult, documents, export, molecule, plot, plotdata, templates, tikz, verify
from openstem.tools import lms as lms_tool  # 툴 함수 이름 lms와 겹친다
from openstem.tools import units as units_tool  # check_units의 units 인자와 이름이 겹친다

PROMPTS_DIR = Path(__file__).parent / "prompts"

# LLM이 읽는 글(툴·인자 설명, 이 안내문, 툴이 돌려주는 에러·힌트·경고·안내)은 짧은 영어로 쓴다(한국어는 토큰이
# 2~3배이고, 사용자에게는 LLM이 사용자의 언어로 옮겨 말한다). 사람이 직접 보는 글(툴 title, 프롬프트 제목·설명,
# 터미널·CLI 출력)과 문서에 들어가는 내용(그림·표 이름, 시험지 제목 등)은 한국어로 둔다.
INSTRUCTIONS = """STEM study tools. The server only renders and checks; you solve and judge. Answer the user in their language.
Figures: get_template → adapt → render_tikz → look at the preview → fix (max 3 tries). y=f(x): plot_function. Measured data + fits: plot_data. Molecules: render_molecule.
Check key solution steps with verify_math (physical quantities: assume="positive") and units with check_units.
User's files (PDF, Korean HWP, Word, PowerPoint): read_document. Save results: export_document.
School LMS (courses, deadlines, announcements; download course files to the materials folder; supported schools: action="schools"): lms."""

mcp = MCPServer("openstem", instructions=INSTRUCTIONS, version=__version__)
# SDK가 루트 로깅을 INFO + stderr로 둔다. stdio 모드에서 stderr는 호스트 로그 파일이라, httpx 요청 로그(LMS 토큰이 든
# 주소)를 끄고 모든 핸들러에서 LMS 비밀을 가린다(HTTP 모드는 remote.serve가 uvicorn 핸들러에 한 번 더 단다).
lms_logs.install()


def _to_mcp(result: ToolResult) -> CallToolResult:
    content: list = [TextContent(type="text", text=json.dumps(result.data, ensure_ascii=False, indent=1))]
    for png in [result.preview_png, *result.more_images]:
        if png:
            data = base64.b64encode(png).decode("ascii")
            content.append(ImageContent(type="image", data=data, mime_type="image/png"))
    return CallToolResult(content=content, is_error=not result.ok)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


# ── 툴 ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="render_tikz",
    title="TikZ 그림 렌더링",
    description=tikz.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
)
def render_tikz(
    code: Annotated[str, Field(description="tikzpicture/circuitikz code, or a full standalone document")],
    packages: Annotated[list[str], Field(description="Extra packages (usually auto-detected)")] = [],  # noqa: B006
    tikz_libraries: Annotated[list[str], Field(description="e.g. [\"arrows.meta\"] (usually auto-detected)")] = [],  # noqa: B006
    name: Annotated[str, Field(description="File name")] = "",
) -> CallToolResult:
    return _to_mcp(tikz.render_tikz(code, packages, tikz_libraries, name or None))


@mcp.tool(
    name="render_molecule",
    title="분자 구조식 그리기",
    description=molecule.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
)
def render_molecule(
    smiles: Annotated[str, Field(description="SMILES; reaction: reactants>>products")],
    kind: Literal["molecule", "reaction"] = "molecule",
    legend: Annotated[str, Field(description="Caption under the drawing")] = "",
    show_atom_numbers: bool = False,
    name: Annotated[str, Field(description="File name")] = "",
) -> CallToolResult:
    return _to_mcp(molecule.render_molecule(smiles, kind, legend or None, show_atom_numbers, name or None))


class PlotSeries(BaseModel):
    expr: str = Field(description="f(x), sympy syntax")
    label: str = ""


class PlotPoints(BaseModel):
    x: list[float]
    y: list[float]
    label: str = ""


@mcp.tool(
    name="plot_function",
    title="함수 그래프 그리기",
    description=plot.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
)
def plot_function(
    series: list[PlotSeries],
    x_range: Annotated[list[float | str], Field(description='[min, max]; numbers or e.g. "2*pi"')],
    y_range: Annotated[list[float | str], Field(description="[min, max]; empty = auto")] = [],  # noqa: B006
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    points: Annotated[list[PlotPoints], Field(description="Data points to overlay")] = [],  # noqa: B006
    name: Annotated[str, Field(description="File name")] = "",
) -> CallToolResult:
    labelled = [{**s.model_dump(), "label": s.label or None} for s in series]
    dots = [{**p.model_dump(), "label": p.label or None} for p in points]
    return _to_mcp(
        plot.plot_function(
            labelled, x_range, y_range or None, title or None, x_label or None, y_label or None, dots, name or None
        )
    )


FitKind = Literal["none", "linear", "poly2", "poly3", "exponential", "power", "log"]


class DataSeries(BaseModel):
    x: list[float]
    y: list[float]
    yerr: list[float] | float = Field(default=[], description="per point or one value")
    xerr: list[float] | float = Field(default=[], description="per point or one value")
    label: str = ""
    fit: FitKind | Literal[""] = Field(default="", description="override for this series")


@mcp.tool(
    name="plot_data",
    title="실험 데이터 그래프·맞춤",
    description=plotdata.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
)
def plot_data(
    series: Annotated[list[DataSeries], Field(description="Or use table")] = [],  # noqa: B006
    table: Annotated[str, Field(description="CSV/markdown table text with a header row")] = "",
    x_column: Annotated[str, Field(description="table: x column (default first)")] = "",
    y_columns: Annotated[list[str], Field(description="table: y columns (default the rest)")] = [],  # noqa: B006
    yerr_columns: Annotated[list[str], Field(description="table: y error columns")] = [],  # noqa: B006
    xerr_column: Annotated[str, Field(description="table: x error column")] = "",
    fit: FitKind = "none",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    log_x: bool = False,
    log_y: bool = False,
    name: Annotated[str, Field(description="File name")] = "",
) -> CallToolResult:
    data = [
        {
            **s.model_dump(),
            "yerr": s.yerr if s.yerr != [] else None,
            "xerr": s.xerr if s.xerr != [] else None,
            "label": s.label or None,
            "fit": s.fit or None,
        }
        for s in series
    ]
    return _to_mcp(
        plotdata.plot_data(
            data,
            table or None,
            x_column or None,
            y_columns or None,
            yerr_columns or None,
            xerr_column or None,
            fit,
            title or None,
            x_label or None,
            y_label or None,
            log_x,
            log_y,
            name or None,
        )
    )


@mcp.tool(
    name="check_units",
    title="단위·차원 검사",
    description=units_tool.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def check_units(
    expr: Annotated[str, Field(description="Formula, sympy syntax")] = "",
    units: Annotated[dict[str, str], Field(description="Unit per variable, optionally with value")] = {},  # noqa: B006
    expect: Annotated[str, Field(description="Expected unit")] = "",
    value: Annotated[str, Field(description='Value to convert, e.g. "72 km/h"')] = "",
    to: Annotated[str, Field(description="Target unit (empty = SI)")] = "",
) -> CallToolResult:
    return _to_mcp(units_tool.check_units(expr or None, units or None, expect or None, value or None, to or None))


@mcp.tool(
    name="verify_math",
    title="수식 검산",
    description=verify.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def verify_math(
    op: Literal[
        "equal", "simplify", "solve", "diff", "integrate", "limit", "evaluate",
        "ode", "matrix", "laplace", "inverse_laplace", "series", "sum", "probability",
    ],  # fmt: skip
    expr: Annotated[str, Field(description="Expression, equation or inequality (sympy syntax)")] = "",
    against: Annotated[str, Field(description="equal: the other expression / ode: your solution")] = "",
    var: Annotated[str, Field(description='Variable: "x, y" for systems, "t" for ode/laplace, "n" for sum')] = "",
    point: Annotated[str, Field(description='limit "0+", evaluate "x=2", series center')] = "",
    latex: bool = False,
    bounds: Annotated[list[str | float], Field(description='[0, "pi"], [1, "oo"]')] = [],
    order: Annotated[int, Field(description="diff order / series degree (0 = default)")] = 0,
    assume: Literal["real", "positive"] = "real",
    ics: Annotated[str, Field(description="ode initial conditions")] = "",
    matrix: Annotated[list[list[str | float]], Field(description='Rows: [["1", "2"], ["3", "4"]]')] = [],
    matrix_b: Annotated[list[list[str | float]], Field(description="B for A·B")] = [],
    dist: Annotated[str, Field(description='"Normal(0, 1)"')] = "",
) -> CallToolResult:
    return _to_mcp(
        verify.verify_math(
            op, expr, against or None, var or None, point or None, latex, bounds or None, order or None,
            assume, ics or None, matrix or None, matrix_b or None, dist or None,
        )  # fmt: skip
    )


@mcp.tool(
    name="export_document",
    title="문서로 내보내기",
    description=export.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
)
def export_document(
    markdown: str,
    format: Literal["pdf", "docx", "pptx", "hwpx"] = "pdf",
    title: str = "",
    layout: Literal["document", "cheatsheet", "exam", "flashcards"] = "document",
    name: Annotated[str, Field(description="File name (default: title)")] = "",
) -> CallToolResult:
    return _to_mcp(export.export_document(markdown, format, title or None, layout, name or None))


@mcp.tool(
    name="read_document",
    title="자료 파일 읽기",
    description=documents.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def read_document(
    path: Annotated[str, Field(description="File in the materials folder; empty or a folder = list files")] = "",
    pages: Annotated[str, Field(description='PDF pages, e.g. "1-3,7"')] = "",
    mode: Literal["text", "images"] = "text",
    part: int = 1,
) -> CallToolResult:
    return _to_mcp(documents.read_document(path or None, pages or None, mode, part))


@mcp.tool(
    name="lms",
    title="학교 LMS 읽기",
    description=lms_tool.DESCRIPTION,
    # LMS는 읽기만 하지만 download·sync는 자료 폴더에 파일을 쓴다. 사용자 파일을 지우거나 덮어쓰지 않으므로 destructive는 아니다.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True),
)
def lms(
    action: Literal["courses", "deadlines", "announcements", "files", "download", "sync", "schools"],
    course: Annotated[str, Field(description="Course id or part of its name; schools: school name to look up")] = "",
    item: Annotated[str, Field(description="download: file id from action=files")] = "",
    days: Annotated[int, Field(description="deadlines/announcements: window in days (1-120)")] = 14,
) -> CallToolResult:
    return _to_mcp(lms_tool.lms(action, course or None, item or None, days))


@mcp.tool(
    name="get_template",
    title="그림 예제 읽기",
    description=templates.DESCRIPTION,
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=False,
)
def get_template(
    name: Annotated[str, Field(description="category/name; empty = list")] = "",
) -> str:
    try:
        return templates.get_template(name)
    except templates.TemplateNotFound as exc:
        raise ToolError(str(exc)) from exc


def _drop_schema_titles(schema: Any) -> Any:
    """pydantic이 인자마다 붙이는 "title"(인자 이름을 다시 쓴 것)을 뺀다. 대화마다 실리는 문맥을 줄이려고.

    값이 문자열인 "title"만 지운다: 인자 이름이 title인 경우(properties 안)는 값이 스키마(dict)라 남는다.
    """
    if isinstance(schema, dict):
        return {k: _drop_schema_titles(v) for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(schema, list):
        return [_drop_schema_titles(v) for v in schema]
    return schema


for _tool in mcp._tool_manager.list_tools():
    _tool.parameters = _drop_schema_titles(_tool.parameters)


# ── 프롬프트 ────────────────────────────────────────────────────────


@mcp.prompt(
    name="figure",
    title="학술 그림 그리기",
    description="예제를 먼저 읽고 render_tikz로 그린 뒤, 미리보기를 보고 최대 3회 고친다.",
)
def figure(request: Annotated[str, Field(description="그릴 그림. 예: RC 직렬회로")]) -> str:
    return _load_prompt("figure").replace("{request}", request)


@mcp.prompt(
    name="solve",
    title="검산하며 풀기",
    description="풀이를 단계별로 쓰고, 핵심 단계마다 verify_math로 검산하고, 필요하면 그림을 넣어 최종 답을 낸다.",
)
def solve(problem: Annotated[str, Field(description="풀 문제. 예: 높이 20 m에서 떨어뜨린 공이 땅에 닿는 데 걸리는 시간")]) -> str:
    return _load_prompt("solve").replace("{problem}", problem)


@mcp.prompt(
    name="cheatsheet",
    title="A4 한 장 치트시트",
    description="자료를 시험용으로 골라 A4 한 장에 요약하고 export_document로 PDF를 만든다.",
)
def cheatsheet(material: Annotated[str, Field(description="요약할 자료나 범위. 예: 일반물리 1장 운동학")]) -> str:
    return _load_prompt("cheatsheet").replace("{material}", material)


@mcp.prompt(
    name="slides",
    title="발표 슬라이드 만들기",
    description="자료로 슬라이드 구성을 짜고, 그림을 render_tikz·plot_function·plot_data로 만들어 넣은 뒤 export_document로 pptx를 만든다.",
)
def slides(material: Annotated[str, Field(description="발표할 자료나 주제(시간·청중도 함께). 예: 열역학 제2법칙, 10분 발표")]) -> str:
    return _load_prompt("slides").replace("{material}", material)


@mcp.prompt(
    name="exam",
    title="예상 문제지 만들기",
    description="강의 자료나 기출 문제로 새 예상 문제를 만들고, 풀이를 verify_math·check_units로 검산한 뒤 정답과 해설이 붙은 시험지 PDF를 만든다.",
)
def exam(
    material: Annotated[str, Field(description="출제 범위 자료나 기출 문제, 문제 수·난이도. 예: 일반물리 1~3장, 5문제, 어려움")],
) -> str:
    return _load_prompt("exam").replace("{material}", material)


@mcp.prompt(
    name="flashcards",
    title="암기 카드 만들기",
    description="자료에서 개념·공식 카드를 뽑아, 잘라 쓰는 양면 인쇄용 카드 PDF와 카드 앱용 텍스트 파일을 만든다.",
)
def flashcards(material: Annotated[str, Field(description="카드로 만들 자료나 범위. 예: 일반화학 산과 염기 공식")]) -> str:
    return _load_prompt("flashcards").replace("{material}", material)


@mcp.prompt(
    name="lab_report",
    title="실험 결과 보고서",
    description="측정 데이터를 plot_data로 그리고 맞춘 뒤 check_units로 단위를 확인하고, 목적·이론·방법·결과·고찰·결론 보고서를 docx로 만든다.",
)
def lab_report(
    experiment: Annotated[str, Field(description="실험 내용과 측정 데이터. 예: 단진자 주기 측정, 길이 0.2~1.0 m에서 주기 5회씩")],
) -> str:
    return _load_prompt("lab_report").replace("{experiment}", experiment)


# ── 리소스 ──────────────────────────────────────────────────────────


for _t in templates.load_templates():
    mcp.add_resource(
        TextResource(
            uri=f"{templates.URI_PREFIX}{_t.id}",
            name=_t.id,
            title=_t.title,
            description=_t.description,
            mime_type="text/x-tex",
            text=_t.code,
        )
    )


@mcp.resource(
    "template://{category}/{name}",
    name="template",
    title="그림 예제",
    description="Verified TikZ examples under templates/",
    mime_type="text/x-tex",
)
def template_resource(category: str, name: str) -> str:
    try:
        return templates.find(f"{category}/{name}").code
    except templates.TemplateNotFound as exc:
        raise ResourceNotFoundError(str(exc)) from exc


# ── 실행 ────────────────────────────────────────────────────────────


WORKERS = ["openstem.workers.verify", "openstem.workers.plot", "openstem.workers.molecule", "openstem.workers.convert", "openstem.workers.documents", "openstem.workers.units", "openstem.workers.plotdata"]


def _warmup_sources() -> list[str]:
    return [tikz.warmup_source(), *export.warmup_sources()]


def _warmup_cli() -> int:
    print("계산 라이브러리를 준비하는 중입니다...", flush=True)
    sandbox.warm_workers(WORKERS)
    try:
        latex.ensure_tectonic(progress=lambda line: print(line, flush=True))  # 없으면 여기서 먼저 받는다
        print("TeX 패키지를 내려받는 중입니다. 처음이면 3~5분 걸립니다...", flush=True)
        result = latex.warmup(_warmup_sources())
    except latex.TectonicNotFound as exc:
        print(exc)
        return 1
    if result.ok:
        print("준비 완료. 이제 MCP 호스트에서 바로 쓸 수 있습니다.")
        return 0
    print("실패:", *result.errors, sep="\n")
    return 1


def _start_background_warmup() -> None:
    """stdio·HTTP 모드 공통: 캐시가 비어 있으면 TeX 번들과 파이썬 작업자를 백그라운드로 준비한다."""
    if not latex.is_warm():  # 처음 실행(또는 새 버전): 파이썬 작업자도 미리 import해 둔다(.pyc·글꼴 캐시)
        threading.Thread(target=sandbox.warm_workers, args=(WORKERS,), name="openstem-workers", daemon=True).start()
    latex.BACKGROUND_WARMUP.start(_warmup_sources)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("0~65535 사이 숫자여야 합니다(0이면 빈 포트를 고른다)")
    return port


def main() -> None:
    from openstem import remote  # HTTP 모드 전용(uvicorn은 그 안에서 필요할 때만 import한다)

    parser = argparse.ArgumentParser(
        prog="openstem-mcp",
        description="이공계 학습용 MCP 서버. 보통은 MCP 호스트가 stdio로 실행한다. "
        "--http는 ChatGPT 등 원격 MCP 클라이언트용이다.",
    )
    parser.add_argument("--warmup", action="store_true", help="TeX 패키지를 미리 내려받고 끝낸다(처음 한 번, 3~5분)")
    parser.add_argument("--version", action="version", version=f"openstem-mcp {__version__}")
    parser.add_argument(
        "--doctor", action="store_true", help="설치 상태를 점검하고 이슈에 붙일 요약을 출력한다(--json, --offline과 함께 쓸 수 있다)"
    )
    http = parser.add_argument_group("HTTP 모드(ChatGPT 등 원격 MCP 클라이언트용)")
    http.add_argument("--http", action="store_true", help="stdio 대신 streamable HTTP로 연다(붙을 주소를 시작할 때 출력)")
    http.add_argument("--port", type=_port, help=f"포트(기본 {remote.DEFAULT_PORT})")
    http.add_argument("--host", help=f"열 주소(기본 {remote.DEFAULT_HOST}: 이 PC 안에서만)")
    http.add_argument("--new-secret", action="store_true", help="주소에 들어가는 비밀 키를 새로 만든다(예전 주소는 404)")
    http.add_argument("--tunnel", action="store_true", help="설치된 cloudflared로 임시 HTTPS 주소를 열어 ChatGPT에 붙일 URL을 출력")
    if "--doctor" in sys.argv[1:]:  # --json·--offline은 진단 전용 인자라 doctor가 따로 읽는다
        from openstem import doctor

        sys.exit(doctor.main(sys.argv[1:], WORKERS))
    args = parser.parse_args()
    if not args.http and (args.port is not None or args.host is not None or args.new_secret or args.tunnel):
        parser.error("--port·--host·--new-secret·--tunnel은 --http와 함께 씁니다. 예: openstem-mcp --http --tunnel")
    if args.warmup:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        sys.exit(_warmup_cli())
    if args.http:
        sys.exit(
            remote.serve(
                mcp,
                host=args.host or remote.DEFAULT_HOST,
                port=remote.DEFAULT_PORT if args.port is None else args.port,
                new_secret=args.new_secret,
                tunnel=args.tunnel,
                before_serve=_start_background_warmup,  # 포트를 연 뒤에(열지 못하면 준비도 하지 않는다)
            )
        )
    _start_background_warmup()
    mcp.run()


if __name__ == "__main__":
    main()
