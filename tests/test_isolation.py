"""운영체제 수준 격리(isolation.py): 작업자·tectonic을 샌드박스 안에서 돌린다.

격리를 실제로 쓸 수 있는 곳(Linux bwrap, macOS sandbox-exec)에서는 홈 폴더의 비밀 파일 읽기와 작업 폴더 밖 쓰기가
실패하는지 직접 확인한다. GitHub Actions의 ubuntu·macos에서는 격리를 못 쓰면 건너뛰지 않고 실패한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from openstem import isolation, sandbox
from openstem.render import latex

IN_CI = os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform in ("linux", "darwin")
PAYLOAD = {"smiles": "CCO", "kind": "molecule", "legend": None, "show_atom_numbers": False}
SECRET = "openstem-secret-" + uuid.uuid4().hex


@pytest.fixture
def fresh(monkeypatch):
    """설정을 바꾼 시험이 다른 시험의 감지 결과·예비 작업자를 쓰지 않게 한다."""
    sandbox._stop_spares()
    isolation.reset()
    yield monkeypatch
    sandbox._stop_spares()
    isolation.reset()


@pytest.fixture
def active(monkeypatch):
    """실제 격리가 켜져야 하는 시험. 못 쓰면 건너뛴다(CI의 ubuntu·macos에서는 실패)."""
    monkeypatch.delenv(isolation.MODE_ENV, raising=False)
    isolation.reset()
    st = isolation.status()
    if not st.active:
        if IN_CI:
            pytest.fail(f"CI({sys.platform})에서 운영체제 격리를 쓰지 못했다: {st.reason}", pytrace=False)
        pytest.skip(f"운영체제 격리를 쓸 수 없다: {st.reason}")
    return st


@pytest.fixture
def home_secret():
    """홈 폴더에 임시 비밀 파일을 둔다(격리 안에서는 보이지 않아야 한다)."""
    path = Path.home() / f".openstem-test-{uuid.uuid4().hex}.tex"
    path.write_text(SECRET + "\n", encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


def run_isolated(code: str, workdir: Path, *, network: bool = False) -> subprocess.CompletedProcess[str]:
    """입력 검사를 빠져나간 코드가 작업자 안에서 돈다고 가정하고, 같은 격리 설정으로 파이썬 코드를 돌린다."""
    argv = isolation.wrap([sys.executable, "-I", "-c", code], isolation.Spec(write=(workdir,), python=True, network=network))
    assert argv[0] != sys.executable, "격리되지 않았다"
    return subprocess.run(argv, cwd=workdir, capture_output=True, text=True, timeout=60, env=sandbox.child_env(), check=False)


# ── 설정 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "expected"), [("", "auto"), ("off", "off"), ("REQUIRE", "require"), (" auto ", "auto"), ("nope", "auto")])
def test_mode_setting(monkeypatch, raw, expected):
    monkeypatch.setenv(isolation.MODE_ENV, raw)
    assert isolation.mode() == expected


def test_off_never_wraps(fresh):
    fresh.setenv(isolation.MODE_ENV, "off")
    assert isolation.wrap(["tectonic", "x.tex"], isolation.Spec()) == ["tectonic", "x.tex"]
    assert "꺼짐" in isolation.describe()


def _no_backend(monkeypatch):
    """bwrap이 없는 PC처럼(macOS면 sandbox-exec가 없는 PC처럼) 만든다."""
    monkeypatch.setattr(isolation.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(isolation, "_SANDBOX_EXEC", "/nonexistent/sandbox-exec")


def test_auto_without_backend_runs_as_before(fresh, tmp_path):
    fresh.setenv(isolation.MODE_ENV, "auto")
    _no_backend(fresh)
    st = isolation.status()
    assert not st.active and st.reason
    assert isolation.wrap(["a"], isolation.Spec()) == ["a"]
    assert sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path)["ok"] is True
    assert "격리 없이 동작" in isolation.describe()


def test_require_without_backend_is_a_short_tool_error(fresh, tmp_path):
    from openstem.tools.verify import verify_math

    fresh.setenv(isolation.MODE_ENV, "require")
    _no_backend(fresh)
    with pytest.raises(sandbox.WorkerFailed, match="OS isolation is required"):
        sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path)
    result = verify_math("simplify", "x + x")
    assert not result.ok
    errors = result.data["errors"]
    assert len(errors) <= 10 and any("OS isolation is required" in e for e in errors)
    sandbox.warm_workers(["openstem.workers.molecule"])  # 서버 시작 때 준비가 죽지 않는다
    assert "openstem.workers.molecule" not in sandbox._spares


def test_require_without_backend_fails_tex_compile(fresh, tmp_path):
    fresh.setenv(isolation.MODE_ENV, "require")
    _no_backend(fresh)
    fresh.setattr(latex, "find_tectonic", lambda: sys.executable)
    result = latex.compile_tex(r"\documentclass{article}\begin{document}x\end{document}", tmp_path)
    assert not result.ok and "OS isolation is required" in result.errors[0]


def test_failing_backend_falls_back_quietly_in_auto(fresh, tmp_path, caplog):
    """bwrap은 있지만 사용자 네임스페이스가 막힌 경우(Ubuntu 24.04 AppArmor 등): 감지에서 걸러 지금처럼 돈다."""
    fresh.setenv(isolation.MODE_ENV, "auto")
    fresh.setattr(isolation, "_build", lambda backend, argv, spec: [sys.executable, "-c", "raise SystemExit('setting up uid map: Permission denied')"])
    fresh.setattr(isolation.shutil, "which", lambda name, *a, **k: "/usr/bin/bwrap")
    fresh.setattr(isolation.Path, "is_file", lambda self: True)
    with caplog.at_level("WARNING", logger="openstem.isolation"):
        st = isolation.status()
    fresh.undo()
    if sys.platform not in ("linux", "darwin"):
        assert not st.active
        return
    assert not st.active and "Permission denied" in st.reason
    assert sum("unavailable" in r.message for r in caplog.records) == 1


def test_python_paths_never_expose_the_home_folder():
    home = os.path.realpath(Path.home())
    for p in isolation.python_paths():
        real = os.path.realpath(p)
        assert not isolation._under(home, real), p
    assert any(os.path.realpath(sys.executable).startswith(os.path.realpath(p)) for p in isolation.python_paths())


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap·sandbox-exec 인자는 POSIX 경로를 다룬다")
def test_bwrap_mounts_follow_symlinks_and_skip_covered_paths(tmp_path):
    real = tmp_path / "real"
    (real / "lib").mkdir(parents=True)
    (real / "lib" / "f.txt").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real)
    m = isolation._Mounts()
    m.add(str(link / "lib" / "f.txt"), "ro")
    m.add(str(real), "rw")
    args = m.args()
    assert ["--symlink", str(real), str(link)] == args[args.index("--symlink"):args.index("--symlink") + 3]
    assert ["--bind", str(real), str(real)] == args[args.index("--bind"):args.index("--bind") + 3]
    assert "--ro-bind" not in args  # 쓰기로 붙인 폴더 안이라 다시 붙이지 않는다


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap·sandbox-exec 인자는 POSIX 경로를 다룬다")
def test_bwrap_mounts_keep_intermediate_links(tmp_path):
    """venv/bin/python → uv/cpython-3.14-…/bin/python3.14 이고 그 폴더가 다시 cpython-3.14.x-…를 가리키는 경우(CI에서 재현)."""
    real = tmp_path / "cpython-3.14.0" / "bin"
    real.mkdir(parents=True)
    (real / "python3.14").write_text("x")
    minor = tmp_path / "cpython-3.14"
    minor.symlink_to(tmp_path / "cpython-3.14.0")
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(minor / "bin" / "python3.14")
    m = isolation._Mounts()
    m.add(str(tmp_path / "venv"), "ro")
    m.add(str(venv / "python"), "ro")
    args = m.args()
    triples = [args[i:i + 3] for i, a in enumerate(args) if a in ("--symlink", "--ro-bind")]
    assert ["--symlink", str(tmp_path / "cpython-3.14.0"), str(minor)] in triples
    assert ["--ro-bind", str(real / "python3.14"), str(real / "python3.14")] in triples


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap·sandbox-exec 인자는 POSIX 경로를 다룬다")
def test_bwrap_network_only_for_tectonic(tmp_path):
    worker = isolation._bwrap_args(["python"], isolation.Spec(write=(tmp_path,), python=True))
    tex = isolation._bwrap_args(["tectonic"], isolation.tectonic_spec(sys.executable, tmp_path))
    assert "--unshare-all" in worker and "--share-net" not in worker
    assert "--share-net" in tex
    home = str(Path.home())
    binds = [worker[i + 1] for i, a in enumerate(worker) if a in ("--bind", "--ro-bind")]
    assert home not in binds and "/" not in binds and "/home" not in binds


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap·sandbox-exec 인자는 POSIX 경로를 다룬다")
def test_macos_profile_denies_reads_writes_and_worker_network(tmp_path):
    worker = isolation.macos_profile(isolation.Spec(write=(tmp_path,), python=True))
    tex = isolation.macos_profile(isolation.tectonic_spec(sys.executable, tmp_path))
    assert '(deny file-read* (subpath "/"))' in worker and '(deny file-write* (subpath "/"))' in worker
    assert os.path.realpath(tmp_path) in worker
    assert "(deny network*)" in worker and "(deny network*)" not in tex
    assert f'(subpath "{os.path.realpath(Path.home())}")' not in worker


def test_collect_box_skips_symlinks(tmp_path, home_secret):
    """작업자가 box/work에 홈 폴더 파일로 가는 링크를 두어도 서버가 따라가 복사하지 않는다."""
    box, out = tmp_path / "box", tmp_path / "out"
    (box / "work").mkdir(parents=True)
    out.mkdir()
    (box / "work" / "ok.txt").write_text("fine")
    (box / "work" / "leak.txt").symlink_to(home_secret)
    sandbox._collect_box(box, out)
    assert (out / "ok.txt").read_text() == "fine" and not (out / "leak.txt").exists()


def test_doctor_shows_one_isolation_line():
    from openstem import doctor

    check = doctor.check_isolation()
    assert check.id == "isolation" and "\n" not in check.detail


# ── 격리 안에서 실제로 (Linux bwrap, macOS sandbox-exec) ─────────────


def test_isolated_code_cannot_read_a_secret_in_home(active, tmp_path, home_secret):
    code = f"print(open({str(home_secret)!r}).read())"
    res = run_isolated(code, tmp_path)
    assert res.returncode != 0 and SECRET not in res.stdout
    listing = run_isolated(f"import os; print(os.listdir({str(Path.home())!r}))", tmp_path)
    assert home_secret.name not in listing.stdout


def test_isolated_code_cannot_write_outside_the_work_folder(active, tmp_path):
    work, outside = tmp_path / "work", tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    targets = [outside / "x.txt", Path.home() / f".openstem-test-{uuid.uuid4().hex}"]
    code = "import sys\nfor p in sys.argv[1:]:\n    try:\n        open(p, 'w').write('x')\n    except OSError as e:\n        print('denied', e)\nopen('inside.txt', 'w').write('ok')"
    argv = isolation.wrap(
        [sys.executable, "-I", "-c", code, *map(str, targets)], isolation.Spec(write=(work,), python=True)
    )
    res = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=60, env=sandbox.child_env(), check=False)
    try:
        assert res.returncode == 0, res.stderr
        assert (work / "inside.txt").read_text() == "ok"
        assert not any(t.exists() for t in targets), res.stdout
    finally:
        for t in targets:
            t.unlink(missing_ok=True)


def test_isolated_worker_has_no_network(active, tmp_path):
    code = "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=3)\n    print('connected')\nexcept OSError as e:\n    print('blocked', e)"
    assert "blocked" in run_isolated(code, tmp_path).stdout


def test_workers_run_inside_the_sandbox(active, tmp_path, monkeypatch):
    """예비 작업자도 격리 안에서 뜨고, 일이 끝나면 전용 폴더(box)를 지운다."""
    sandbox._stop_spares()
    proc = sandbox._spawn("openstem.workers.molecule")
    box = sandbox._box_of(proc)
    try:
        assert box is not None and proc.args[0] != sys.executable
    finally:
        proc.kill()
        proc.communicate()
        sandbox._drop_box(box)
    result = sandbox.run_python("openstem.workers.molecule", PAYLOAD, tmp_path)
    assert result["ok"] and (tmp_path / result["pdf"]).stat().st_size > 0
    with sandbox._boxes_lock:
        live = {sandbox._box_of(p) for p in sandbox._spares.values()}
        assert sandbox._boxes <= live  # 쓰고 난 box는 남지 않는다(예비 작업자 것만)


def test_verify_math_inside_the_sandbox(active):
    from openstem.tools.verify import verify_math

    result = verify_math("equal", "sin(x)^2 + cos(x)^2", against="1")
    assert result.ok and result.data["matches"] is True, result.data


def test_read_document_inside_the_sandbox(active, tmp_path, monkeypatch):
    """입력 파일은 작업자 폴더로 복사해 넘긴다(자료 폴더 자체는 작업자에게 보이지 않는다)."""
    import pymupdf

    from openstem.tools import documents
    from openstem.tools.documents import read_document

    folder = tmp_path / "자료"
    folder.mkdir()
    monkeypatch.setenv("OPENSTEM_INPUT_DIR", str(folder))
    monkeypatch.setenv("OPENSTEM_READ_CACHE", "0")
    monkeypatch.setattr(documents, "_cache_root", lambda: tmp_path / "cache")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "isolated reading works")
    doc.save(folder / "note.pdf")
    (folder / "plain.txt").write_text("plain text file", encoding="utf-8")
    result = read_document("note.pdf")
    assert result.ok and "isolated reading works" in json.dumps(result.data), result.data
    images = read_document("note.pdf", mode="images")
    assert images.ok and images.preview_png


def test_tex_cannot_read_a_secret_in_home(active, tectonic, tmp_path, home_secret):
    """TeX 검사를 빠져나간 \\input이 있다고 가정(검사를 거치지 않고 바로 컴파일): 홈 폴더 파일이 보이지 않는다."""
    source = "\\documentclass{article}\\begin{document}\\input{" + home_secret.as_posix() + "}\\end{document}\n"
    result = latex.compile_tex(source, tmp_path)
    log = (tmp_path / "figure.log").read_text(errors="replace") if (tmp_path / "figure.log").exists() else ""
    assert not result.ok and SECRET not in log and SECRET not in " ".join(result.errors)


@pytest.mark.parametrize("template_id", ["circuits/rc_series", "math/unit_circle", "physics/incline_forces", "diagrams/flowchart_decision"])
def test_templates_compile_inside_the_sandbox(active, tectonic, template_id):
    from openstem.tools import templates
    from openstem.tools.tikz import render_tikz

    folder = template_id.split("/")[0]
    found = [t for t in templates.load_templates() if t.id == template_id] or [t for t in templates.load_templates() if t.id.startswith(folder + "/")]
    template = found[0]
    result = render_tikz(template.code, template.packages, template.libraries, name=template.name)
    assert result.ok, result.data.get("errors")


def test_compile_failure_without_log_shows_tectonic_stderr_tail(monkeypatch):
    """로그도 error: 줄도 없이 tectonic이 끝나면(샌드박스 거부 등) 마지막 줄을 에러에 싣고 홈 폴더는 ~로 가린다."""
    from openstem.render import latex

    home = str(Path.home())
    stderr = f"note: something\nthread 'main' panicked at {home}/.cargo/x.rs:1:2:\nOperation not permitted (os error 1)\n"
    assert latex._stderr_errors(stderr) == [
        "tectonic: thread 'main' panicked at ~/.cargo/x.rs:1:2:",
        "tectonic: Operation not permitted (os error 1)",
    ]
    assert latex._stderr_errors("error: first\nerror: second\nother") == ["error: first", "error: second"]
    assert latex._stderr_errors("") == []
