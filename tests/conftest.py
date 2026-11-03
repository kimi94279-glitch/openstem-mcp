import os

import pytest

from openstem.render.latex import _locate_tectonic

HAS_TECTONIC = bool(_locate_tectonic())  # PATH·OPENSTEM_TECTONIC·자동 설치한 캐시 폴더
# CI는 OPENSTEM_REQUIRE_TEX=1로 돈다. tectonic이 없어 렌더링 시험이 조용히 건너뛰어지는 대신 실패하게 한다.
REQUIRE_TEX = os.environ.get("OPENSTEM_REQUIRE_TEX") == "1"


def require_tectonic() -> None:
    """tectonic이 필요한 시험의 공통 조건. 없으면 건너뛰고, OPENSTEM_REQUIRE_TEX=1이면 실패한다."""
    if HAS_TECTONIC:
        return
    if REQUIRE_TEX:
        pytest.fail(
            "tectonic이 없다(OPENSTEM_REQUIRE_TEX=1이라 건너뛰지 않고 실패). `uv run openstem-mcp --warmup`으로 준비한다.",
            pytrace=False,
        )
    pytest.skip("tectonic이 설치되어 있지 않다(OPENSTEM_REQUIRE_TEX=1이면 실패로 바뀐다)")


@pytest.fixture(autouse=True)
def output_dir(tmp_path, monkeypatch):
    """테스트 결과물이 ~/OpenSTEM에 쌓이지 않게 임시 폴더로 돌린다."""
    out = tmp_path / "out"
    monkeypatch.setenv("OPENSTEM_OUTPUT_DIR", str(out))
    return out


@pytest.fixture
def tectonic():
    """컴파일이 필요한 테스트. tectonic이 없으면 건너뛴다(OPENSTEM_REQUIRE_TEX=1이면 실패)."""
    require_tectonic()


@pytest.fixture
def anyio_backend():
    return "asyncio"
