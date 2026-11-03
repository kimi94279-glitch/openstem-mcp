"""결과물 저장 경로와 파일명 규칙.

저장 폴더: 환경변수 OPENSTEM_OUTPUT_DIR, 없으면 ~/OpenSTEM/
파일명: <YYYYMMDD-HHMMSS>_<이름>.<확장자>  (같은 그림의 svg/pdf/png는 이름이 같다)
"""

from __future__ import annotations

import glob
import os
import re
import threading
from datetime import datetime
from pathlib import Path

OUTPUT_DIR_ENV = "OPENSTEM_OUTPUT_DIR"
# 툴은 스레드 여러 개에서 동시에 돈다. 같은 이름이 같은 초에 오면 파일이 생기기 전에 같은 stem을 고를 수 있어,
# 이 프로세스에서 나눠 준 stem을 기억해 두고 겹치지 않게 한다.
_lock = threading.Lock()
_handed_out: set[str] = set()


def env_value(name: str) -> str | None:
    """환경변수 값(앞뒤 공백 제거). 없거나 비었거나 채워지지 않은 자리표시자면 None.

    Claude Desktop 확장(.mcpb)은 사용자가 비워 둔 설정을 빈 문자열이나 "${user_config.x}" 글자 그대로
    넘길 수 있어서, 그런 값은 설정하지 않은 것으로 본다.
    """
    value = (os.environ.get(name) or "").strip()
    if not value or value.startswith("${"):
        return None
    return value


def output_dir() -> Path:
    raw = env_value(OUTPUT_DIR_ENV)
    path = Path(raw).expanduser() if raw else Path.home() / "OpenSTEM"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(name: str | None, default: str = "figure") -> str:
    """파일명에 쓸 수 있게 다듬는다. 한글은 그대로 둔다."""
    text = re.sub(r"[^\w\-]+", "-", (name or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-_")[:40].strip("-_")
    return text or default


def new_stem(name: str | None, default: str = "figure") -> Path:
    """확장자 없는 저장 경로. 같은 이름이 이미 있으면 -2, -3을 붙인다."""
    base = output_dir()
    stem = f"{datetime.now():%Y%m%d-%H%M%S}_{slugify(name, default)}"
    with _lock:
        candidate, n = base / stem, 2
        while str(candidate) in _handed_out or glob.glob(glob.escape(str(candidate)) + ".*"):
            candidate, n = base / f"{stem}-{n}", n + 1
        _handed_out.add(str(candidate))
    return candidate
