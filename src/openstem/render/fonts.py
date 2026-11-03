"""그림에 한글을 쓸 때 필요한 글꼴 찾기 (matplotlib·분자 범례용. TikZ는 tectonic 번들의 은글꼴을 쓴다)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")

_WINDOWS = ("malgun.ttf", "malgunsl.ttf", "gulim.ttc", "batang.ttc")
_MACOS = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/AppleGothic.ttf",
)
_LINUX_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts")
_LINUX_NAMES = (
    "NanumGothic.ttf",
    "NotoSansKR-Regular.otf",
    "NotoSansKR-Regular.ttf",
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKkr-Regular.otf",
    "UnDotum.ttf",
)


def has_hangul(*texts: str | None) -> bool:
    return any(t and HANGUL_RE.search(t) for t in texts)


def hangul_font(workdir: Path) -> Path:
    """한글 글리프가 있는 글꼴 파일. 시스템 글꼴이 없으면 pymupdf 내장 글꼴(Droid Sans Fallback)을 workdir에 풀어 쓴다."""
    if found := system_hangul_font():
        return found
    import pymupdf

    path = workdir / "hangul-fallback.ttf"
    if not path.exists():
        path.write_bytes(pymupdf.Font("korea").buffer)
    return path


def system_hangul_font() -> Path | None:
    if sys.platform == "win32":
        base = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = [base / name for name in _WINDOWS]
    elif sys.platform == "darwin":
        candidates = [Path(p) for p in _MACOS]
    else:
        candidates = []
        for d in _LINUX_DIRS:
            root = Path(d).expanduser()
            if root.is_dir():
                for name in _LINUX_NAMES:
                    candidates += sorted(root.rglob(name))
    return next((p for p in candidates if p.is_file()), None)
