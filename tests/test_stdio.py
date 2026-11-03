"""실제 MCP 호스트처럼 서버를 별도 프로세스(stdio)로 띄우고 툴을 불러 본다."""

import json
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.anyio


async def test_stdio_server_draws_molecule_and_plot(output_dir):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "openstem.server"], env={"OPENSTEM_OUTPUT_DIR": str(output_dir), "OPENSTEM_NO_DOWNLOAD": "1"}
    )
    async with Client(params) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"render_tikz", "get_template", "render_molecule", "plot_function", "verify_math"} <= names

        mol = await client.call_tool("render_molecule", {"smiles": "c1ccccc1O", "legend": "페놀"})
        plot = await client.call_tool("plot_function", {"series": [{"expr": "x^2"}], "x_range": [-2, 2]})
        for result in (mol, plot):
            assert not result.is_error, result.content[0].text
            data = json.loads(result.content[0].text)
            assert Path(data["png_path"]).parent == output_dir
            assert result.content[1].type == "image" and result.content[1].data
        assert json.loads(mol.content[0].text)["formula"] == "C6H6O"

        check = await client.call_tool("verify_math", {"op": "solve", "expr": "x**2 = 9"})
        assert not check.is_error, check.content[0].text
        assert [s["result"] for s in json.loads(check.content[0].text)["solutions"]] == ["-3", "3"]
