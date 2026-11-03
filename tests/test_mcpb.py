"""Claude Desktop 확장 묶음(.mcpb, scripts/build_mcpb.py)과 환경변수 자리표시자 처리.

묶음을 새 폴더에 풀어 manifest의 명령 그대로(uv run ...) 띄우는 시험은 오래 걸려(가상환경을 새로 만든다)
OPENSTEM_SLOW_TESTS=1일 때만 돈다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

import anyio
import pytest

from openstem import __version__, output
from openstem.tools import documents

REPO = Path(__file__).resolve().parents[1]
SLOW = pytest.mark.skipif(os.environ.get("OPENSTEM_SLOW_TESTS") != "1", reason="OPENSTEM_SLOW_TESTS=1일 때만(수 분)")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_mcpb", REPO / "scripts" / "build_mcpb.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass가 모듈을 sys.modules에서 찾는다
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    builder = _load_builder()
    return builder.build(out_dir=tmp_path_factory.mktemp("dist"), use_cli=False, lock_check=False)


@pytest.fixture(scope="module")
def manifest(bundle):
    with zipfile.ZipFile(bundle.path) as zf:
        return json.loads(zf.read("manifest.json").decode("utf-8"))


def test_bundle_file_list(bundle):
    assert bundle.path.name == f"openstem-{__version__}.mcpb"
    with zipfile.ZipFile(bundle.path) as zf:
        names = set(zf.namelist())
    required = {
        "manifest.json", "launch.py", "icon.png", "pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE",
        "src/openstem/__init__.py", "src/openstem/server.py", "src/openstem/render/tectonic_setup.py",
        "src/openstem/render/exam.lua", "src/openstem/workers/verify.py", "src/openstem/prompts/solve.md",
    }  # fmt: skip
    assert required <= names
    templates = {p.relative_to(REPO).as_posix() for p in (REPO / "templates").rglob("*.tex")}
    assert templates and templates <= names  # 저장소와 같은 배치(루트 templates/)
    sources = {
        p.relative_to(REPO).as_posix()
        for p in (REPO / "src" / "openstem").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    assert sources <= names
    for name in names:
        assert "\\" not in name and not name.startswith("/")
        assert not name.startswith(("tests/", ".venv/", "examples/", "dist/", "scripts/", "packaging/", ".github/"))
        assert "__pycache__" not in name and not name.endswith((".pyc", ".pyo"))
    assert ".mcpbignore" not in names  # 공식 CLI도 넣지 않는다


def test_bundle_pins_python_version(bundle):
    """호스트의 uv가 아무 Python이나 고르지 않게 .python-version을 넣는다. requires-python을 만족하고 lock에 wheel이 있어야 한다."""
    import tomllib

    with zipfile.ZipFile(bundle.path) as zf:
        pinned = zf.read(".python-version").decode("utf-8").strip()
    assert pinned == (REPO / ".python-version").read_text(encoding="utf-8").strip()
    major, minor = (int(x) for x in pinned.split(".")[:2])
    lower = re.search(r">=\s*3\.(\d+)", tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"])
    assert major == 3 and minor >= int(lower.group(1))
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    for name in ("rdkit", "pymupdf"):
        wheels = [w["url"] for p in lock["package"] if p["name"] == name for w in p.get("wheels", [])]
        assert any(f"-cp3{minor}-" in w or "-abi3-" in w for w in wheels), f"{name}: Python {pinned}용 wheel이 lock에 없다"


def test_mcpb_cli_is_pinned():
    builder = _load_builder()
    assert re.fullmatch(r"@anthropic-ai/mcpb@\d+\.\d+\.\d+", builder.MCPB_CLI)


def test_manifest_fields(manifest):
    import tomllib

    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "openstem" and manifest["display_name"] == "OpenSTEM"
    assert manifest["version"] == __version__
    assert re.search(r"[가-힣]", manifest["description"])  # 설치 화면에 보이는 설명은 한국어
    assert manifest["author"]["name"] == project["authors"][0]["name"]
    assert manifest["license"] == project["license"]
    assert manifest["homepage"] == project["urls"]["Homepage"]
    assert manifest["icon"] == "icon.png"
    assert manifest["compatibility"] == {
        "platforms": ["darwin", "win32", "linux"],
        "runtimes": {"python": project["requires-python"]},
    }
    assert "(" not in json.dumps({k: manifest[k] for k in ("version", "author", "license", "compatibility")})

    server = manifest["server"]
    assert server["type"] == "uv" and server["entry_point"] == "launch.py"
    config = server["mcp_config"]
    assert config["command"] == "uv"
    args = config["args"]
    assert args[:3] == ["run", "--directory", "${__dirname}"] and args[-1] == "launch.py"
    assert "--no-dev" in args and "--frozen" in args
    assert config["env"] == {
        "OPENSTEM_OUTPUT_DIR": "${user_config.output_dir}",
        "OPENSTEM_INPUT_DIR": "${user_config.materials_dir}",
        "OPENSTEM_LMS_SCHOOL": "${user_config.lms_school}",
        "OPENSTEM_LMS_TOKEN": "${user_config.lms_token}",
        "OPENSTEM_LMS_CALENDAR_URL": "${user_config.lms_calendar_url}",
        "OPENSTEM_LMS_URL": "${user_config.lms_url}",
    }

    user = manifest["user_config"]
    assert list(user) == ["output_dir", "materials_dir", "lms_school", "lms_token", "lms_calendar_url", "lms_url"]
    assert user["output_dir"]["type"] == user["materials_dir"]["type"] == "directory"
    assert user["output_dir"]["default"] == "${DOCUMENTS}/OpenSTEM"
    for name in ("lms_school", "lms_token", "lms_calendar_url", "lms_url"):
        assert user[name]["type"] == "string"
    assert user["lms_school"]["title"] == "학교 이름 (예: 성균관대)"
    assert user["lms_calendar_url"]["title"] == "LMS 캘린더 주소(토큰이 안 될 때)"
    # 토큰과 캘린더 주소는 비밀 값(운영체제 비밀 저장소), 학교 이름·주소는 아니다
    assert user["lms_token"]["sensitive"] is True and user["lms_calendar_url"]["sensitive"] is True
    assert "sensitive" not in user["lms_school"] and "sensitive" not in user["lms_url"]
    for option in user.values():
        assert option["required"] is False
        assert re.search(r"[가-힣]", option["title"]) and option["description"]


def test_manifest_lists_every_server_tool(manifest):
    from openstem.server import mcp

    server_tools = {t.name for t in mcp._tool_manager.list_tools()}
    listed = {t["name"] for t in manifest["tools"]}
    assert server_tools <= listed, f"manifest tools에 빠진 툴: {server_tools - listed}"
    assert listed - server_tools <= {"lms"}  # lms(M6)가 server.py에 등록되면 이 차이는 저절로 없어진다
    assert all(t["description"].strip() for t in manifest["tools"])


def test_icon_is_512_png(bundle):
    with zipfile.ZipFile(bundle.path) as zf:
        data = zf.read("icon.png")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (512, 512)


def test_launch_py_starts_server():
    text = (REPO / "packaging" / "mcpb" / "launch.py").read_text(encoding="utf-8")
    assert "from openstem.server import main" in text and "main()" in text


# ── 환경변수: 비었거나 채워지지 않은 자리표시자는 설정하지 않은 것으로 ─────────


@pytest.mark.parametrize("value", ["", "   ", "${user_config.output_dir}", " ${user_config.output_dir} "])
def test_output_dir_ignores_empty_and_placeholder(monkeypatch, tmp_path, value):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv(output.OUTPUT_DIR_ENV, value)
    assert output.env_value(output.OUTPUT_DIR_ENV) is None
    assert output.output_dir() == tmp_path / "home" / "OpenSTEM"


def test_output_dir_uses_set_value(monkeypatch, tmp_path):
    monkeypatch.setenv(output.OUTPUT_DIR_ENV, f"  {tmp_path / 'chosen'}  ")
    assert output.output_dir() == tmp_path / "chosen"


@pytest.mark.parametrize("value", ["", "  ", "${user_config.materials_dir}"])
def test_input_dir_ignores_empty_and_placeholder(output_dir, monkeypatch, value):
    monkeypatch.setenv(documents.INPUT_DIR_ENV, value)
    assert documents.input_dir() == output_dir / "자료"


def test_input_dir_uses_set_value(monkeypatch, tmp_path):
    monkeypatch.setenv(documents.INPUT_DIR_ENV, str(tmp_path / "materials"))
    assert documents.input_dir() == tmp_path / "materials"


# ── 묶음을 새 폴더에 풀어 manifest의 명령 그대로 띄우기 ─────────────────


@SLOW
@pytest.mark.anyio
async def test_unpacked_bundle_runs_with_manifest_command(bundle, manifest, tmp_path):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv가 없다")
    root = tmp_path / "extension"
    with zipfile.ZipFile(bundle.path) as zf:
        zf.extractall(root)
    out = tmp_path / "out"
    # 호스트가 하는 치환: __dirname과 사용자가 고른 결과물 폴더. 비워 둔 설정은 빈 값이나 자리표시자 그대로 온다.
    values = {"${__dirname}": str(root), "${user_config.output_dir}": str(out), "${user_config.materials_dir}": ""}
    config = manifest["server"]["mcp_config"]
    args = [values.get(a, a) for a in config["args"]]
    env = {k: values.get(v, v) for k, v in config["env"].items()}
    env["OPENSTEM_NO_DOWNLOAD"] = "1"  # 시험 중에는 tectonic을 받지 않는다
    params = StdioServerParameters(command=uv, args=args, env=env)

    with anyio.fail_after(900):  # 처음엔 가상환경을 만들고 의존성을 설치한다
        async with Client(params) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {t["name"] for t in manifest["tools"]} - {"lms"} <= names

            check = await client.call_tool("verify_math", {"op": "solve", "expr": "x**2 = 9"})
            assert not check.is_error, check.content[0].text
            assert [s["result"] for s in json.loads(check.content[0].text)["solutions"]] == ["-3", "3"]

            listing = await client.call_tool("read_document", {})
            assert not listing.is_error, listing.content[0].text
            assert Path(json.loads(listing.content[0].text)["folder"]) == out / "자료"  # 빈 자료 폴더 설정 → 기본값

            parsed = await client.call_tool("verify_math", {"op": "simplify", "expr": r"\frac{x^2-1}{x-1}", "latex": True})
            assert not parsed.is_error, parsed.content[0].text  # --extra latex로 LaTeX 입력이 된다

    venv = root / ".venv"
    assert venv.is_dir()
    installed = {p.name.split("-")[0].lower() for p in venv.rglob("*.dist-info")}
    assert "pytest" not in installed  # dev 그룹은 설치하지 않는다
    assert "antlr4_python3_runtime" in installed
