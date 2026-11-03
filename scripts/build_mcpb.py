"""Claude Desktop 확장 묶음(.mcpb)을 만든다. 사용자는 이 파일을 더블클릭해 설치한다.

    uv run python scripts/build_mcpb.py            # dist/openstem-<버전>.mcpb (공식 CLI가 있으면 그것으로 묶는다)
    uv run python scripts/build_mcpb.py --zip      # 공식 CLI(npx @anthropic-ai/mcpb) 없이 zipfile로 묶는다
    uv run python scripts/build_mcpb.py --icon     # packaging/mcpb/icon.png를 다시 그린다

MCPB manifest 0.4의 server.type "uv": 묶음에는 소스만 넣고, 의존성은 호스트가 pyproject.toml·uv.lock으로 설치한다
(`uv run --frozen --no-dev --extra latex`, dev 그룹은 설치하지 않는다). 묶음 안 배치는 저장소와 같다
(pyproject가 루트의 templates/를 wheel에 force-include하고, 편집 설치에서는 tools/templates.py가 루트 templates/를 찾는다).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging" / "mcpb"
MCPB_CLI = "@anthropic-ai/mcpb"

ROOT_FILES = ["pyproject.toml", "uv.lock", "README.md", "LICENSE"]
TREES = ["src/openstem", "templates"]
PACKAGING_FILES = ["launch.py", "icon.png", ".mcpbignore"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    path: Path
    method: str  # "mcpb-cli" | "zipfile"
    files: list[str]


def read_version(repo: Path = REPO) -> str:
    text = (repo / "src" / "openstem" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", text, re.MULTILINE)
    if not m:
        raise BuildError("src/openstem/__init__.py에서 __version__을 찾지 못했다.")
    return m.group(1)


def make_manifest(repo: Path = REPO) -> dict:
    """packaging/mcpb/manifest.template.json에 버전·저자·라이선스·주소를 채운다."""
    manifest = json.loads((PACKAGING / "manifest.template.json").read_text(encoding="utf-8"))
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    urls = project.get("urls", {})
    manifest["version"] = read_version(repo)
    authors = project.get("authors") or [{"name": project["name"]}]
    manifest["author"] = {k: v for k, v in authors[0].items() if k in ("name", "email")}
    if urls.get("Homepage"):
        manifest["homepage"] = urls["Homepage"]
        manifest["repository"] = {"type": "git", "url": urls["Homepage"]}
    if urls.get("Issues"):
        manifest["support"] = urls["Issues"]
    manifest["license"] = project["license"] if isinstance(project.get("license"), str) else "MIT"
    manifest["compatibility"]["runtimes"]["python"] = project["requires-python"]
    return manifest


def _wanted(path: Path) -> bool:
    return (
        not (set(path.parts) & SKIP_DIRS)
        and path.suffix not in SKIP_SUFFIXES
        and path.name not in SKIP_NAMES
    )


def stage(dest: Path, repo: Path = REPO) -> list[str]:
    """묶음에 넣을 파일을 dest에 저장소와 같은 배치로 모은다. 담은 파일(상대 경로, /)을 돌려준다."""
    files: list[str] = []

    def copy(src: Path, rel: str) -> None:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        files.append(rel)

    for name in ROOT_FILES:
        if not (repo / name).is_file():
            raise BuildError(f"{name}이(가) 없다.")
        copy(repo / name, name)
    for tree in TREES:
        root = repo / tree
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(repo)
            if path.is_file() and _wanted(rel):
                copy(path, rel.as_posix())
    for name in PACKAGING_FILES:
        if not (PACKAGING / name).is_file():
            raise BuildError(f"packaging/mcpb/{name}이(가) 없다." + (" --icon으로 만든다." if name == "icon.png" else ""))
        copy(PACKAGING / name, name)
    manifest = json.dumps(make_manifest(repo), ensure_ascii=False, indent=2) + "\n"
    (dest / "manifest.json").write_text(manifest, encoding="utf-8", newline="\n")
    files.append("manifest.json")
    return sorted(files)


def check_lock(repo: Path = REPO) -> None:
    """manifest가 `uv run --frozen`이라 uv.lock이 pyproject와 어긋나면 의존성이 빠진 채 설치된다. 미리 막는다."""
    uv = shutil.which("uv")
    if not uv:
        print("경고: uv가 없어 uv.lock이 최신인지 확인하지 못했다.", file=sys.stderr)
        return
    res = subprocess.run([uv, "lock", "--check"], check=False, cwd=repo, capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        raise BuildError("uv.lock이 pyproject.toml과 맞지 않는다. `uv lock`을 먼저 실행하라.\n" + res.stderr.strip()[-800:])


def _npx() -> str | None:
    return shutil.which("npx")


def pack_with_cli(stage_dir: Path, out: Path) -> None:
    npx = _npx()
    if not npx:
        raise BuildError("npx가 없다.")
    for args in (["validate", str(stage_dir / "manifest.json")], ["pack", str(stage_dir), str(out)]):
        res = subprocess.run(
            [npx, "-y", MCPB_CLI, *args], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600
        )
        if res.returncode != 0 or "ERROR" in res.stdout:
            raise BuildError(f"mcpb {args[0]} 실패:\n{(res.stdout + res.stderr).strip()[-1500:]}")
    if not out.is_file():
        raise BuildError("mcpb pack이 파일을 만들지 않았다.")


def pack_with_zipfile(stage_dir: Path, out: Path, files: list[str]) -> None:
    """공식 CLI와 같은 배치(루트에 manifest.json, / 구분자, .mcpbignore는 넣지 않음)로 묶는다."""
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            if rel != ".mcpbignore":
                zf.write(stage_dir / rel, rel)
    tmp.replace(out)


def build(out_dir: Path | None = None, use_cli: bool = True, lock_check: bool = True) -> BuildResult:
    version = read_version()
    out_dir = out_dir or REPO / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"openstem-{version}.mcpb"
    if lock_check:
        check_lock()
    with tempfile.TemporaryDirectory(prefix="openstem-mcpb-") as tmp:
        stage_dir = Path(tmp) / "openstem"
        files = stage(stage_dir)
        method = "zipfile"
        if use_cli and _npx():
            out.unlink(missing_ok=True)
            pack_with_cli(stage_dir, out)
            method = "mcpb-cli"
        else:
            pack_with_zipfile(stage_dir, out, files)
    with zipfile.ZipFile(out) as zf:
        packed = sorted(zf.namelist())
    expected = sorted(f for f in files if f != ".mcpbignore")
    if packed != expected:
        missing, extra = sorted(set(expected) - set(packed)), sorted(set(packed) - set(expected))
        raise BuildError(f"묶음 내용이 예상과 다르다. 빠짐: {missing[:10]} 더 있음: {extra[:10]}")
    return BuildResult(path=out, method=method, files=packed)


# ── 아이콘 ──────────────────────────────────────────────────────────


def make_icon(path: Path, size: int = 512) -> None:
    """512×512 아이콘: 둥근 사각형(남색→청록) 위에 좌표축, 감쇠 진동 곡선, 벤젠 고리. 이 프로젝트만의 단순한 그림."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import Circle, FancyBboxPatch, RegularPolygon

    fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    card = FancyBboxPatch((0.04, 0.04), 0.92, 0.92, boxstyle="round,pad=0,rounding_size=0.2", linewidth=0, facecolor="none")
    ax.add_patch(card)
    cmap = LinearSegmentedColormap.from_list("openstem", ["#17607a", "#163a63"])
    background = ax.imshow(np.linspace(0, 1, 256).reshape(-1, 1), extent=(0.04, 0.96, 0.04, 0.96), cmap=cmap, aspect="auto")
    background.set_clip_path(card)

    axis = {"color": "white", "alpha": 0.5, "lw": 7, "solid_capstyle": "round"}
    ax.plot([0.17, 0.85], [0.40, 0.40], **axis)
    ax.plot([0.21, 0.21], [0.17, 0.66], **axis)
    t = np.linspace(0, 1, 500)
    ax.plot(0.21 + 0.62 * t, 0.40 + 0.21 * np.exp(-1.7 * t) * np.sin(2 * np.pi * 2.25 * t),
            color="white", lw=15, solid_capstyle="round", solid_joinstyle="round")  # fmt: skip

    ring = {"fill": False, "edgecolor": "#ffcf5c", "lw": 9, "joinstyle": "round"}
    ax.add_patch(RegularPolygon((0.715, 0.73), numVertices=6, radius=0.13, orientation=0, **ring))
    ax.add_patch(Circle((0.715, 0.73), 0.068, fill=False, edgecolor="#ffcf5c", lw=6))

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100, transparent=True)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Desktop 확장 묶음(.mcpb)을 만든다.")
    parser.add_argument("--zip", action="store_true", help="공식 CLI 없이 zipfile로 묶는다")
    parser.add_argument("--icon", action="store_true", help="packaging/mcpb/icon.png를 다시 그린다")
    parser.add_argument("--no-lock-check", action="store_true", help="uv.lock 최신 여부 확인을 건너뛴다")
    parser.add_argument("--out", type=Path, help="출력 폴더(기본 dist/)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.icon or not (PACKAGING / "icon.png").is_file():
        make_icon(PACKAGING / "icon.png")
        print(f"아이콘을 그렸습니다: {PACKAGING / 'icon.png'}")
    try:
        result = build(args.out, use_cli=not args.zip, lock_check=not args.no_lock_check)
    except BuildError as exc:
        sys.exit(f"실패: {exc}")
    how = "공식 CLI(mcpb pack, manifest 검사 통과)" if result.method == "mcpb-cli" else "zipfile"
    size = result.path.stat().st_size
    print(f"만들었습니다: {result.path} ({size / 1024:.0f} KB, 파일 {len(result.files)}개, {how})")
    print("Claude Desktop에서 이 파일을 더블클릭하면 설치 화면이 열립니다.")


if __name__ == "__main__":
    main()
