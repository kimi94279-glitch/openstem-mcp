"""MCP 프로토콜로 서버를 호출해 본다(프로세스 안 연결)."""

import json
from pathlib import Path

import pytest
from mcp import Client

from openstem.server import mcp

pytestmark = pytest.mark.anyio

RC_CODE = (
    "\\begin{circuitikz}\n"
    "\\draw (0,0) to[battery1, l=$V_0$] (0,3) to[R, l=저항 $R$] (3,3) to[C, l=$C$] (3,0) -- (0,0);\n"
    "\\end{circuitikz}"
)


async def test_server_exposes_tools_prompt_and_resources():
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {"render_tikz", "get_template", "render_molecule", "plot_function", "verify_math", "export_document"} <= tools.keys()
        for tool in tools.values():
            assert '{"' in tool.description and "On failure" in tool.description  # 입력 예시(JSON)와 실패 대처

        assert {"figure", "solve", "cheatsheet"} <= {p.name for p in (await client.list_prompts()).prompts}
        uris = [str(r.uri) for r in (await client.list_resources()).resources]
        assert "template://circuits/rc_series" in uris
        patterns = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
        assert "template://{category}/{name}" in patterns


async def test_read_template_resource():
    async with Client(mcp) as client:
        result = await client.read_resource("template://circuits/opamp_inverting")
        assert "op amp" in result.contents[0].text


async def test_get_template_tool():
    async with Client(mcp) as client:
        catalog = await client.call_tool("get_template", {})
        assert "circuits/rc_series" in catalog.content[0].text

        one = await client.call_tool("get_template", {"name": "circuits/rc_series"})
        assert not one.is_error
        assert "\\begin{circuitikz}" in one.content[0].text

        missing = await client.call_tool("get_template", {"name": "circuits/nope"})
        assert missing.is_error


async def test_figure_prompt_includes_request():
    async with Client(mcp) as client:
        prompt = await client.get_prompt("figure", {"request": "RC 직렬회로"})
        text = prompt.messages[0].content.text
        assert "RC 직렬회로" in text and "get_template" in text and "3 times" in text


async def test_render_tikz_returns_paths_and_preview(tectonic):
    async with Client(mcp) as client:
        result = await client.call_tool("render_tikz", {"code": RC_CODE, "name": "rc"})
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert Path(data["svg_path"]).exists()
        image = result.content[1]
        assert image.type == "image" and image.mime_type == "image/png" and image.data


async def test_render_tikz_input_error_is_flagged():
    async with Client(mcp) as client:
        result = await client.call_tool("render_tikz", {"code": "\\draw (0,0);", "packages": ["minted"]})
        assert result.is_error
        data = json.loads(result.content[0].text)
        assert data["ok"] is False and "minted" in data["errors"][0]


async def test_render_molecule_returns_formula_and_preview():
    async with Client(mcp) as client:
        result = await client.call_tool("render_molecule", {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "legend": "아스피린"})
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert data["formula"] == "C9H8O4" and Path(data["svg_path"]).exists()
        image = result.content[1]
        assert image.type == "image" and image.mime_type == "image/png" and image.data


async def test_render_molecule_error_is_flagged():
    async with Client(mcp) as client:
        result = await client.call_tool("render_molecule", {"smiles": "CC(C)(C)(C)(C)C"})
        assert result.is_error
        assert "atom #2" in json.loads(result.content[0].text)["errors"][0]


async def test_plot_function_returns_warnings_and_preview():
    async with Client(mcp) as client:
        args = {"series": [{"expr": "1/x", "label": "반비례"}], "x_range": [-5, 5], "points": [{"x": [1], "y": [1]}]}
        result = await client.call_tool("plot_function", args)
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert Path(data["png_path"]).exists() and "diverge" in data["warnings"][0]
        image = result.content[1]
        assert image.type == "image" and image.mime_type == "image/png" and image.data


async def test_plot_function_input_error_is_flagged():
    async with Client(mcp) as client:
        result = await client.call_tool("plot_function", {"series": [{"expr": "|x|"}], "x_range": [-1, 1]})
        assert result.is_error
        assert "abs(x)" in json.loads(result.content[0].text)["errors"][0]


async def test_lms_schools_and_missing_config(monkeypatch):
    for name in ("OPENSTEM_LMS_URL", "OPENSTEM_LMS_TOKEN", "OPENSTEM_LMS_SCHOOL", "OPENSTEM_LMS_CALENDAR_URL", "OPENSTEM_LMS_TYPE"):
        monkeypatch.delenv(name, raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("lms", {"action": "schools", "course": "성균관대"})
        assert not result.is_error, result.content[0].text
        assert "canvas.skku.edu" in result.content[0].text
        result = await client.call_tool("lms", {"action": "deadlines"})
        assert result.is_error and "OPENSTEM_LMS_SCHOOL" in result.content[0].text


async def test_verify_math_judges_equality():
    async with Client(mcp) as client:
        result = await client.call_tool("verify_math", {"op": "equal", "expr": "(a+b)**2", "against": "a**2 + 2*a*b + b**2"})
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert data["matches"] is True and len(result.content) == 1  # 그림 없음


async def test_verify_math_input_error_is_flagged():
    async with Client(mcp) as client:
        result = await client.call_tool("verify_math", {"op": "simplify", "expr": "|x|"})
        assert result.is_error
        assert "abs(x)" in json.loads(result.content[0].text)["errors"][0]


async def test_solve_prompt_includes_problem():
    async with Client(mcp) as client:
        prompt = await client.get_prompt("solve", {"problem": "x**2 = 4를 풀어라"})
        text = prompt.messages[0].content.text
        assert "x**2 = 4를 풀어라" in text and "verify_math" in text


async def test_export_document_docx_over_mcp(output_dir):
    async with Client(mcp) as client:
        result = await client.call_tool("export_document", {"markdown": "# 정리\n\n$E = mc^2$", "format": "docx"})
        assert not result.is_error, result.content[0].text
        data = json.loads(result.content[0].text)
        assert Path(data["path"]).parent == output_dir and data["path"].endswith(".docx")


async def test_cheatsheet_prompt_includes_material():
    async with Client(mcp) as client:
        prompt = await client.get_prompt("cheatsheet", {"material": "일반물리 운동학"})
        text = prompt.messages[0].content.text
        assert "일반물리 운동학" in text and "export_document" in text and "cheatsheet" in text


def test_doctor_flag_runs_the_diagnosis(monkeypatch):
    """--doctor는 서버를 띄우지 않고 진단만 한다. --json·--offline은 진단으로 넘어간다."""
    import sys

    from openstem import doctor, server

    seen = {}

    def fake_main(argv, workers):
        seen.update(argv=list(argv), workers=list(workers))
        return 3

    monkeypatch.setattr(doctor, "main", fake_main)
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: pytest.fail("server must not start"))
    monkeypatch.setattr(sys, "argv", ["openstem-mcp", "--doctor", "--json", "--offline"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 3
    assert seen["argv"] == ["--doctor", "--json", "--offline"] and seen["workers"] == server.WORKERS
