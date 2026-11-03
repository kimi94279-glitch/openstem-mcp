"""tectonic 자동 설치(render/tectonic_setup.py)와 latex.py의 찾기·준비 순서.

네트워크는 쓰지 않는다: 가짜 압축 파일을 로컬 http 서버로 내려준다. 실제 GitHub 릴리스를 받는 시험은
OPENSTEM_NETWORK_TESTS=1일 때만 돈다.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from openstem.render import latex, tectonic_setup
from openstem.render.tectonic_setup import Asset, SetupError

EXE = tectonic_setup.exe_name()
FAKE_EXE = b"#!/bin/sh\necho fake tectonic\n" + b"\0" * 100


# ── 가짜 압축 파일·로컬 서버 ───────────────────────────────────────────


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_targz(members: dict[str, bytes], symlinks: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    return buf.getvalue()


def asset_of(name: str, data: bytes) -> Asset:
    return Asset(name, hashlib.sha256(data).hexdigest(), len(data))


class Server:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits.append(self.path)
                no_length = self.path.startswith("/nolength/")
                data = outer.files.get(self.path.removeprefix("/nolength/").lstrip("/"))
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                if not no_length:  # HTTP/1.0이라 길이가 없으면 연결을 닫아 끝을 알린다
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def put(self, name: str, data: bytes) -> str:
        self.files[name] = data
        return f"{self.base}/{name}"


@pytest.fixture
def server(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    srv = Server()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def leftovers(bin_dir: Path) -> list[str]:
    """설치 폴더에 남은 파일(잠금 파일 제외)."""
    if not bin_dir.exists():
        return []
    return sorted(p.name for p in bin_dir.iterdir() if p.name != tectonic_setup.LOCK_NAME)


# ── OS·CPU → 릴리스 파일 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("win32", "AMD64", "tectonic-0.17.0-x86_64-pc-windows-msvc.zip"),
        ("darwin", "arm64", "tectonic-0.17.0-aarch64-apple-darwin.tar.gz"),
        ("darwin", "x86_64", "tectonic-0.17.0-x86_64-apple-darwin.tar.gz"),
        ("linux", "x86_64", "tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz"),
        ("linux", "aarch64", "tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz"),
        ("linux", "arm64", "tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz"),
        ("win32", "ARM64", None),  # Windows ARM: 공식 빌드 없음 → 설치 안내
        ("linux", "armv7l", None),
        ("linux", "i686", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_platform_asset_mapping(system, machine, expected):
    asset = tectonic_setup.asset_for(system, machine)
    assert (asset.name if asset else None) == expected


def test_pinned_assets_are_complete():
    assert tectonic_setup.exe_name("win32") == "tectonic.exe"
    assert tectonic_setup.exe_name("darwin") == tectonic_setup.exe_name("linux") == "tectonic"
    for asset in tectonic_setup.ASSETS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
        assert 5_000_000 < asset.size < tectonic_setup.MAX_DOWNLOAD_BYTES
        assert asset.url == (
            "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/" + asset.name
        )
        assert asset.name.startswith(f"tectonic-{tectonic_setup.VERSION}-")


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("tectonic", True),
        ("./tectonic", True),
        ("dir/tectonic.exe", True),
        ("../tectonic", False),
        ("a/../../tectonic", False),
        ("..\\tectonic.exe", False),
        ("/usr/local/bin/tectonic", False),
        ("\\tectonic.exe", False),
        ("C:/Windows/tectonic.exe", False),
        ("C:tectonic.exe", False),
        ("", False),
    ],
)
def test_member_name_safety(name, safe):
    assert tectonic_setup._safe_name(name) is safe


# ── 설치 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_install_extracts_only_the_executable(server, tmp_path, kind):
    members = {f"tectonic-0.17.0/{EXE}" if kind == "zip" else f"./{EXE}": FAKE_EXE, "README.md": b"other file"}
    data = make_zip(members) if kind == "zip" else make_targz(members)
    name = f"fake-tectonic.{kind}"
    url = server.put(name, data)
    bin_dir = tmp_path / "cache" / "bin"
    messages: list[str] = []

    path = tectonic_setup.install(bin_dir, asset=asset_of(name, data), url=url, progress=messages.append, probe=False)

    assert path == bin_dir / EXE
    assert path.read_bytes() == FAKE_EXE
    assert leftovers(bin_dir) == [EXE]  # 압축 파일·임시 파일·README는 남지 않는다
    if sys.platform != "win32":
        assert os.stat(path).st_mode & 0o111
    assert any("내려받는 중" in m for m in messages) and any("설치 완료" in m for m in messages)

    # 이미 있으면 다시 받지 않는다
    server.hits.clear()
    assert tectonic_setup.install(bin_dir, asset=asset_of(name, data), url=url, probe=False) == path
    assert server.hits == []


def test_hash_mismatch_installs_nothing(server, tmp_path):
    data = make_zip({EXE: FAKE_EXE})
    url = server.put("t.zip", data)
    wrong = Asset("t.zip", hashlib.sha256(b"something else").hexdigest(), len(data))
    bin_dir = tmp_path / "bin"
    with pytest.raises(SetupError, match="SHA-256"):
        tectonic_setup.install(bin_dir, asset=wrong, url=url, probe=False)
    assert leftovers(bin_dir) == []


@pytest.mark.parametrize(
    ("kind", "evil"),
    [("zip", "../evil.txt"), ("zip", "C:/evil.txt"), ("tar.gz", "../../evil.txt"), ("tar.gz", "/tmp/evil.txt")],
)
def test_traversal_member_rejects_archive(server, tmp_path, kind, evil):
    members = {EXE: FAKE_EXE, evil: b"pwned"}
    data = make_zip(members) if kind == "zip" else make_targz(members)
    name = f"t.{kind}"
    url = server.put(name, data)
    bin_dir = tmp_path / "a" / "b" / "bin"
    with pytest.raises(SetupError, match="unsafe file name"):
        tectonic_setup.install(bin_dir, asset=asset_of(name, data), url=url, probe=False)
    assert leftovers(bin_dir) == []
    assert not list(tmp_path.rglob("evil.txt"))


def test_archive_without_single_regular_executable_is_rejected(server, tmp_path):
    bin_dir = tmp_path / "bin"
    cases = {
        "none.zip": make_zip({"README.md": b"x"}),
        "two.zip": make_zip({EXE: FAKE_EXE, f"sub/{EXE}": FAKE_EXE}),
        "link.tar.gz": make_targz({}, symlinks={EXE: "/bin/sh"}),
    }
    for name, data in cases.items():
        url = server.put(name, data)
        with pytest.raises(SetupError, match="exactly one"):
            tectonic_setup.install(bin_dir, asset=asset_of(name, data), url=url, probe=False)
        assert leftovers(bin_dir) == []


@pytest.mark.parametrize("prefix", ["", "nolength/"])
def test_download_size_cap(server, tmp_path, prefix):
    data = make_zip({EXE: os.urandom(5000)})
    server.put("big.zip", data)
    bin_dir = tmp_path / "bin"
    with pytest.raises(SetupError, match="limit"):
        tectonic_setup.install(
            bin_dir, asset=asset_of("big.zip", data), url=f"{server.base}/{prefix}big.zip", max_bytes=1000, probe=False
        )
    assert leftovers(bin_dir) == []


def test_http_error_is_short(server, tmp_path):
    with pytest.raises(SetupError, match="HTTP 404"):
        tectonic_setup.install(tmp_path / "bin", asset=asset_of("x.zip", b""), url=f"{server.base}/missing.zip", probe=False)


def test_concurrent_installs_download_once(server, tmp_path):
    data = make_zip({EXE: FAKE_EXE})
    url = server.put("t.zip", data)
    bin_dir = tmp_path / "bin"
    results: list[Path] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(tectonic_setup.install(bin_dir, asset=asset_of("t.zip", data), url=url, probe=False))
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == [bin_dir / EXE] * 4
    assert server.hits == ["/t.zip"]


def test_probe_failure_installs_nothing(server, tmp_path):
    """받은 파일이 이 PC에서 실행되지 않으면(--version 실패) 설치하지 않는다."""
    data = make_zip({EXE: b"not a real program"})
    url = server.put("t.zip", data)
    bin_dir = tmp_path / "bin"
    with pytest.raises(SetupError, match="cannot start"):
        tectonic_setup.install(bin_dir, asset=asset_of("t.zip", data), url=url)
    assert leftovers(bin_dir) == []


# ── latex.py: 찾는 순서·자동 설치 끄기·준비 순서 ─────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """캐시 폴더·PATH를 비우고 자동 설치 상태를 초기화한다."""
    cache = tmp_path / "cache"
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setattr(latex, "_cache_dir", lambda: cache)
    monkeypatch.setattr(latex, "_setup_error", None)
    monkeypatch.setattr(latex, "BACKGROUND_WARMUP", latex.BackgroundWarmup())
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")  # Windows: 현재 폴더를 찾지 않게
    monkeypatch.delenv(latex.TECTONIC_ENV, raising=False)
    monkeypatch.delenv(tectonic_setup.NO_DOWNLOAD_ENV, raising=False)
    return cache


@pytest.fixture
def downloadable(monkeypatch):
    """이 PC의 OS·CPU와 상관없이 자동 설치를 시도할 수 있는 것으로 둔다(실제로 받지는 않는다)."""
    monkeypatch.setattr(tectonic_setup, "asset_for", lambda *a, **k: Asset("fake.zip", "0" * 64, 1))


def _fake_program(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE_EXE)
    path.chmod(0o755)
    return path


def test_find_order(isolated, tmp_path, monkeypatch):
    with pytest.raises(latex.TectonicNotFound):
        latex.find_tectonic()

    cached = _fake_program(isolated / "bin" / EXE)
    assert latex.find_tectonic() == str(cached)

    on_path = _fake_program(tmp_path / "on-path" / EXE)
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert Path(latex.find_tectonic()) == on_path  # PATH가 캐시보다 먼저

    configured = _fake_program(tmp_path / "configured" / EXE)
    monkeypatch.setenv(latex.TECTONIC_ENV, str(configured))
    assert latex.find_tectonic() == str(configured)  # 환경변수가 가장 먼저

    for placeholder in ("", "   ", "${user_config.tectonic}"):  # 비었거나 채워지지 않은 값은 없는 것으로
        monkeypatch.setenv(latex.TECTONIC_ENV, placeholder)
        assert Path(latex.find_tectonic()) == on_path

    monkeypatch.setenv(latex.TECTONIC_ENV, str(tmp_path / "nope.exe"))
    with pytest.raises(latex.TectonicNotFound, match=latex.TECTONIC_ENV):  # 틀린 경로는 PATH로 넘어가지 않는다
        latex.find_tectonic()


def _forbid_install(*args, **kwargs):
    raise AssertionError("자동 설치를 하면 안 된다")


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_no_download_env_is_honored(isolated, monkeypatch, value):
    monkeypatch.setenv(tectonic_setup.NO_DOWNLOAD_ENV, value)
    monkeypatch.setattr(tectonic_setup, "install", _forbid_install)
    assert not tectonic_setup.can_download()
    with pytest.raises(latex.TectonicNotFound) as info:
        latex.ensure_tectonic()
    assert str(info.value) == latex.INSTALL_HINT
    with pytest.raises(latex.TectonicNotFound):
        latex.warmup(["\\documentclass{article}"])
    assert latex.BackgroundWarmup().start(list) is False  # 백그라운드 준비도 시작하지 않는다


@pytest.mark.parametrize("value", ["0", "false", "", "${user_config.no_download}"])
def test_no_download_off_values(monkeypatch, value):
    monkeypatch.setenv(tectonic_setup.NO_DOWNLOAD_ENV, value)
    assert not tectonic_setup.download_disabled()


def test_configured_path_is_never_replaced_by_download(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv(latex.TECTONIC_ENV, str(tmp_path / "missing.exe"))
    monkeypatch.setattr(tectonic_setup, "install", _forbid_install)
    with pytest.raises(latex.TectonicNotFound, match=latex.TECTONIC_ENV):
        latex.ensure_tectonic()


def test_ensure_tectonic_installs_into_cache_bin(isolated, downloadable, monkeypatch):
    calls: list[Path] = []

    def fake_install(bin_dir, progress=None, **kwargs):
        calls.append(bin_dir)
        return _fake_program(bin_dir / EXE)

    monkeypatch.setattr(tectonic_setup, "install", fake_install)
    path = latex.ensure_tectonic()
    assert calls == [isolated / "bin"]
    assert path == str(isolated / "bin" / EXE) == latex.find_tectonic()
    assert latex.ensure_tectonic() == path and len(calls) == 1  # 한 번 설치하면 다시 받지 않는다


def test_failed_install_explains_and_falls_back_to_hint(isolated, downloadable, monkeypatch):
    def failing(*args, **kwargs):
        raise SetupError("Could not download tectonic (timed out).")

    monkeypatch.setattr(tectonic_setup, "install", failing)
    with pytest.raises(latex.TectonicNotFound) as info:
        latex.ensure_tectonic()
    message = str(info.value)
    assert "timed out" in message and latex.INSTALL_HINT in message
    # 렌더링 때(서버의 백그라운드 준비가 없는 경우)도 같은 이유를 알린다
    with pytest.raises(latex.TectonicNotFound, match="Could not install tectonic automatically"):
        latex.find_tectonic()


def test_background_warmup_installs_tectonic_before_tex_bundle(isolated, downloadable, monkeypatch):
    order: list[str] = []
    release = threading.Event()

    def fake_ensure(progress=None):
        order.append("tectonic")
        release.wait(10)  # 내려받는 중
        return "tectonic"

    def fake_warmup(sources, timeout=0, progress=None):
        order.append(f"bundle:{len(sources)}")
        return latex.CompileResult(ok=True)

    monkeypatch.setattr(latex, "ensure_tectonic", fake_ensure)
    monkeypatch.setattr(latex, "warmup", fake_warmup)
    bg = latex.BackgroundWarmup()
    assert bg.start(lambda: ["doc"]) is True
    assert bg.wait(0.3) is False  # 렌더링은 "준비 중, 잠시 후 다시"로 돌아간다
    release.set()
    assert bg.wait(10) is True
    assert order == ["tectonic", "bundle:1"]
    assert bg.error is None


def test_background_warmup_retries_after_failure(isolated, downloadable, monkeypatch):
    attempts: list[int] = []

    def failing_ensure(progress=None):
        attempts.append(1)
        raise latex.TectonicNotFound("Could not install tectonic automatically: offline")

    monkeypatch.setattr(latex, "ensure_tectonic", failing_ensure)
    bg = latex.BackgroundWarmup()
    assert bg.start(list) is True and bg.wait(10)
    assert "offline" in (bg.error or "")
    assert bg.retry() is False  # 방금 실패했으면 바로 다시 하지 않는다
    bg.RETRY_AFTER = 0.0
    assert bg.retry() is True and bg.wait(10)
    assert len(attempts) == 2
    assert latex.BackgroundWarmup().retry() is False  # 서버가 아니면(시작한 적 없음) 다시 걸 것이 없다


def test_missing_message_restarts_server_setup(isolated, downloadable, monkeypatch):
    """서버에서 자동 설치가 실패한 뒤 렌더링 요청이 오면 다시 받기 시작하고 "준비 중"이라고 답한다."""
    bg = latex.BackgroundWarmup()
    started: list[bool] = []
    monkeypatch.setattr(latex, "BACKGROUND_WARMUP", bg)
    monkeypatch.setattr(bg, "_make_sources", list)
    monkeypatch.setattr(bg, "start", lambda make_sources: started.append(True) or True)
    with pytest.raises(latex.TectonicNotFound) as info:
        latex.find_tectonic()
    assert str(info.value) == latex.PREPARING_TECTONIC and started == [True]


# ── 실제 GitHub 릴리스(네트워크) ───────────────────────────────────────


@pytest.mark.skipif(os.environ.get("OPENSTEM_NETWORK_TESTS") != "1", reason="OPENSTEM_NETWORK_TESTS=1일 때만(약 10~22 MB 다운로드)")
def test_real_download_runs(tmp_path):
    if tectonic_setup.asset_for() is None:
        pytest.skip("이 OS·CPU용 공식 빌드가 없다")
    path = tectonic_setup.install(tmp_path / "bin")  # 해시 확인 + --version 실행 확인까지 한다
    res = latex.sandbox.run([str(path), "--version"], cwd=tmp_path, timeout=60)
    assert res.returncode == 0 and tectonic_setup.VERSION in res.stdout
    assert leftovers(tmp_path / "bin") == [EXE]
