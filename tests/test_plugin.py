"""Claude 플러그인(claude-plugin/)·MCP 레지스트리(server.json)가 패키지 버전과 디렉터리 요건에 맞는지 본다.

릴리스할 때 버전을 고칠 곳: src/openstem/__init__.py, claude-plugin/.claude-plugin/plugin.json,
claude-plugin/.mcp.json의 uvx 고정 버전, server.json(CI가 version·mcpb 주소·sha256을 다시 채운다).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openstem import __version__

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "claude-plugin"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_versions_match_the_package():
    assert _json(PLUGIN / ".claude-plugin" / "plugin.json")["version"] == __version__
    args = _json(PLUGIN / ".mcp.json")["mcpServers"]["openstem"]["args"]
    assert args == [f"openstem-mcp=={__version__}"]  # 디렉터리는 고정 버전 launcher만 받는다
    server = _json(ROOT / "server.json")
    assert server["version"] == __version__ and server["packages"][0]["version"] == __version__
    assert server["packages"][1]["identifier"].endswith(f"/v{__version__}/openstem-{__version__}.mcpb")


def test_registry_ownership_marker_in_readme():
    name = _json(ROOT / "server.json")["name"]
    assert f"<!-- mcp-name: {name} -->" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(_json(ROOT / "server.json")["description"]) <= 100


def test_plugin_env_uses_declared_user_config():
    declared = set(_json(PLUGIN / ".claude-plugin" / "plugin.json")["userConfig"])
    env = _json(PLUGIN / ".mcp.json")["mcpServers"]["openstem"]["env"]
    used = {m for v in env.values() for m in re.findall(r"\$\{user_config\.(\w+)\}", v)}
    assert used <= declared


def test_plugin_readme_license_privacy_and_skills():
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", readme, flags=re.S)
    assert len(prose.split()) >= 40
    assert "## Privacy Policy" in readme and (ROOT / "PRIVACY.md").is_file()
    assert (PLUGIN / "LICENSE").read_text(encoding="utf-8") == (ROOT / "LICENSE").read_text(encoding="utf-8")
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert len(skills) >= 5
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.S)
        assert front, skill
        assert re.search(rf"^name: {skill.parent.name}$", front.group(1), re.M), skill
        assert re.search(r"^description: \S", front.group(1), re.M), skill
    for path in PLUGIN.rglob("*"):  # 디렉터리 검사: 큰 파일·시스템 파일 없음
        assert path.name not in (".DS_Store", "Thumbs.db", "desktop.ini")
        assert not path.is_file() or path.stat().st_size < 256 * 1024


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code CLI가 없다")
def test_claude_plugin_validate():
    for target in (PLUGIN, ROOT):
        result = subprocess.run(["claude", "plugin", "validate", str(target)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        assert result.returncode == 0 and "Validation passed" in result.stdout, result.stdout + result.stderr
