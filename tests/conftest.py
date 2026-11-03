import pytest

from openstem.render.latex import _locate_tectonic

HAS_TECTONIC = bool(_locate_tectonic())  # PATH·OPENSTEM_TECTONIC·자동 설치한 캐시 폴더


@pytest.fixture(autouse=True)
def output_dir(tmp_path, monkeypatch):
    """테스트 결과물이 ~/OpenSTEM에 쌓이지 않게 임시 폴더로 돌린다."""
    out = tmp_path / "out"
    monkeypatch.setenv("OPENSTEM_OUTPUT_DIR", str(out))
    return out


@pytest.fixture
def tectonic():
    """컴파일이 필요한 테스트. tectonic이 없으면 건너뛴다."""
    if not HAS_TECTONIC:
        pytest.skip("tectonic이 설치되어 있지 않다")


@pytest.fixture
def anyio_backend():
    return "asyncio"
