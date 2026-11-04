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


# ---- 스킬·프롬프트 정합성: 글에 나오는 툴 이름·인자·Literal 값이 서버 스키마에 있는지 ----

GUIDE_TEXTS = sorted((PLUGIN / "skills").glob("*/SKILL.md")) + sorted((ROOT / "src" / "openstem" / "prompts").glob("*.md"))
# `name=` 꼴이지만 툴 인자가 아닌 것: 시험지 제목 속성 {space=N}(export_document exam 배치), verify_math 결과 필드
NOT_ARGUMENTS = {"space", "matches"}
TOOL_LIKE = re.compile(r"`((?:render|plot|verify|check|export|read|get|list|draw|make)_[a-z0-9_]+)`")
ARGUMENT = re.compile(r"\b([A-Za-z_]\w*)=(?:\"([^\"]*)\"|(\S))")


def _tool_schemas() -> dict[str, dict[str, dict]]:
    """{툴 이름: {인자 이름: 스키마}}. 중첩 모델($defs, 예: plot_data series의 fit)의 속성도 넣는다."""
    import anyio

    from openstem.server import mcp

    tools = anyio.run(mcp.list_tools)
    out: dict[str, dict[str, dict]] = {}
    for tool in tools:
        schema = tool.input_schema
        props = dict(schema.get("properties", {}))
        for sub in schema.get("$defs", {}).values():
            for key, value in sub.get("properties", {}).items():
                props.setdefault(key, value)
        out[tool.name] = props
    return out


def _enum(prop: dict) -> set[str] | None:
    """인자의 Literal 값(enum/const, anyOf 안 포함). Literal이 아니면 None."""
    values: set[str] = set(prop.get("enum", []))
    if "const" in prop:
        values.add(prop["const"])
    for alt in prop.get("anyOf", []):
        values |= _enum(alt) or set()
    return values or None


@pytest.fixture(scope="module")
def schemas():
    return _tool_schemas()


def _guide_problems(text: str, schemas: dict[str, dict[str, dict]]) -> list[str]:
    problems = []
    for name in set(TOOL_LIKE.findall(text)):
        if name not in schemas:
            problems.append(f"없는 툴 `{name}`")
    for number, line in enumerate(text.splitlines(), 1):
        for m in ARGUMENT.finditer(line):
            arg, quoted = m.group(1), m.group(2)
            if arg in NOT_ARGUMENTS:
                continue
            # 같은 줄에서 인자 앞에 툴 이름이 나오면 그 툴의 인자여야 한다. 없으면 어느 툴이든(문단 앞에서 툴을 밝힌 경우)
            named = [t for t in schemas if re.search(rf"`{t}`|\b{t}\(", line[: m.start()])]
            candidates = named or list(schemas)
            owners = [t for t in candidates if arg in schemas[t]]
            if not owners:
                problems.append(f"{number}행: 인자 `{arg}=`가 {', '.join(candidates) if named else '어느 툴'}에도 없다")
                continue
            if quoted:
                enums = [e for e in (_enum(schemas[t][arg]) for t in owners) if e is not None]
                if enums and not any(quoted in e for e in enums):
                    problems.append(f"{number}행: `{arg}=\"{quoted}\"`는 허용 값이 아니다({sorted(set().union(*enums))})")
    return problems


@pytest.mark.parametrize("path", GUIDE_TEXTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_guide_mentions_real_tools_and_arguments(path, schemas):
    problems = _guide_problems(path.read_text(encoding="utf-8"), schemas)
    assert not problems, f"{path.relative_to(ROOT)}:\n" + "\n".join(problems)


def test_guide_checker_catches_mistakes(schemas):
    """검사 자체가 틀린 툴 이름·인자·Literal 값을 잡는지 본다."""
    text = 'Use `verify_maths`.\n`verify_math` with `op="equals"`.\n`lms` with `mode="text"`.\n'
    message = "\n".join(_guide_problems(text, schemas))
    assert "verify_maths" in message and "equals" in message and "mode=" in message


def test_skills_handle_missing_server():
    """claude.ai 채팅처럼 스킬만 실리고 서버가 없을 때 그렇게 말하고 설치 방법을 안내하는지."""
    for skill in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        assert "tools are not available in this session" in text, skill
        assert "github.com/kimi94279-glitch/openstem-mcp" in text, skill
