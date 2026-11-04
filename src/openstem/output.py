"""결과물 저장 경로와 파일명 규칙.

저장 폴더: 환경변수 OPENSTEM_OUTPUT_DIR, 없으면 ~/OpenSTEM/
파일명: <YYYYMMDD-HHMMSS>_<이름>.<확장자>  (같은 그림의 svg/pdf/png는 이름이 같다)
재현 기록: <stem>.json과 원본(<stem>.tex·.md·.csv). save_record()와 OPENSTEM_RECORD 참고.
"""

from __future__ import annotations

import glob
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

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


# ── 재현 기록(사이드카) ─────────────────────────────────────────────
# 결과물 stem 옆에 <stem>.json(어떤 툴이 어떤 입력·버전으로 무엇을 만들었나)과 원본(<stem>.tex·.md·.csv)을 둔다.
# 다른 대화에서 그림을 고치거나 보고서의 분석을 다시 할 수 있게 하려는 것이다. OPENSTEM_RECORD=0이면 쓰지 않는다.
# 저장이 실패해도 결과물은 이미 만들어졌으므로 툴은 성공으로 두고 warnings에 한 줄만 더한다.
RECORD_ENV = "OPENSTEM_RECORD"
RECORD_SCHEMA = 1
_OFF = ("0", "false", "no", "off")
_tectonic_versions: dict[str, str | None] = {}


def record_enabled() -> bool:
    value = env_value(RECORD_ENV)
    return value is None or value.lower() not in _OFF


def tectonic_version() -> str | None:
    """`tectonic --version` 첫 줄(실행 파일마다 한 번만 묻는다). 알 수 없으면 None."""
    from openstem import sandbox  # 순환 import를 피한다(render.latex가 output을 쓴다)
    from openstem.render import latex

    try:
        exe = latex.find_tectonic()
    except latex.TectonicNotFound:
        return None
    if exe not in _tectonic_versions:
        version = None
        try:
            res = sandbox.run([exe, "--version"], cwd=Path(exe).parent, timeout=15)
            if not res.timed_out and res.returncode == 0 and res.stdout.strip():
                version = res.stdout.strip().splitlines()[0][:100]
        except OSError:
            pass
        _tectonic_versions[exe] = version
    return _tectonic_versions[exe]


def pandoc_version() -> str | None:
    try:
        import pypandoc

        return str(pypandoc.get_pandoc_version())
    except (ImportError, OSError, RuntimeError):
        return None


def portable(value: Any) -> Any:
    """기록에 남길 값에서 이 PC의 경로를 지운다: 결과물 폴더 안 경로는 그 폴더 기준 상대 경로, 홈 폴더는 ~."""
    if isinstance(value, str):
        out_dir = str(output_dir())
        home = str(Path.home())
        for prefix, repl in ((out_dir + os.sep, ""), (out_dir + "/", ""), (home, "~")):
            if prefix and prefix not in ("/", os.sep) and prefix in value:
                value = value.replace(prefix, repl)
        return value
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable(v) for v in value]
    return value


def save_record(
    data: dict[str, Any],
    stem: Path,
    tool: str,
    inputs: dict[str, Any],
    results: dict[str, Any] | None = None,
    versions: dict[str, str | None] | None = None,
    sources: dict[str, str | bytes] | None = None,
) -> None:
    """원본 파일(sources: 확장자 → 내용, 첫 항목이 source_path)과 <stem>.json을 쓰고 data에 경로를 싣는다.

    inputs는 툴 인자 전체(비밀은 없어야 한다). 이 PC의 경로는 portable()로 지운다.
    실패하면 data["warnings"]에 한 줄을 더할 뿐 예외를 내지 않는다.
    """
    if not record_enabled():
        return
    from openstem import __version__

    try:
        source_path = None
        for suffix, content in (sources or {}).items():
            path = stem.with_name(stem.name + suffix)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8", newline="\n")
            source_path = source_path or path
        record_path = stem.with_name(stem.name + ".json")
        outputs = sorted({Path(p).name for p in glob.glob(glob.escape(str(stem)) + ".*")} | {record_path.name})
        record: dict[str, Any] = {
            "schema": RECORD_SCHEMA,
            "tool": tool,
            "openstem_version": __version__,
            "versions": {k: v for k, v in (versions or {}).items() if v},
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "inputs": portable(inputs),
            "outputs": outputs,
            "results": portable(results or {}),
        }
        if source_path:
            record["source"] = source_path.name
        if data.get("warnings"):
            record["warnings"] = data["warnings"]
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, TypeError, ValueError) as exc:
        data.setdefault("warnings", []).append(f"Could not save the reproduction record ({type(exc).__name__}: {str(exc)[:120]}).")
        return
    if source_path:
        data["source_path"] = str(source_path)
    data["record_path"] = str(record_path)
