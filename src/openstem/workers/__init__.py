"""서브프로세스 작업자. `sandbox.run_python("openstem.workers.<이름>", payload, cwd)`로만 돈다.

서버 프로세스에서는 import하지 않는다(무거운 라이브러리, 외부 입력 처리).
규약: import를 마치면 stdout에 READY_LINE을 쓰고, stdin으로 payload JSON을 받아 stdout 마지막 줄에 결과 JSON을 쓴다.
결과는 {"ok": true, ...} 또는 {"ok": false, "errors": [...], "hint"?: ...}.
만든 파일은 현재 폴더(cwd)에 두고 결과에 파일 이름을 적는다.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any


class WorkerError(Exception):
    """입력 문제. LLM이 고칠 수 있게 짧은 errors와 hint를 돌려준다."""

    def __init__(self, errors: list[str], hint: str | None = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.hint = hint


READY_LINE = "OPENSTEM-READY"  # sandbox.READY_LINE과 같아야 한다

# 작업자 메모리 상한(주소 공간, MB). numpy·sympy·RDKit·pymupdf·matplotlib import와 보통 계산에는 넉넉하고,
# 2^2^2^2^2^2 같은 폭주 입력이 PC 메모리를 다 쓰기 전에 MemoryError로 끊긴다. 0이면 상한을 두지 않는다.
MEMORY_ENV = "OPENSTEM_WORKER_MEMORY_MB"
DEFAULT_MEMORY_MB = 3072
TOO_LARGE = "The input is too large to compute (worker memory limit). Use smaller numbers or a simpler input."


def memory_limit_mb() -> int:
    raw = os.environ.get(MEMORY_ENV, "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_MEMORY_MB
    except ValueError:
        return DEFAULT_MEMORY_MB


def limit_memory() -> int | None:
    """POSIX에서 이 프로세스의 주소 공간 상한(RLIMIT_AS)을 둔다. 둔 상한(MB)을 돌려준다.

    Windows에는 resource 모듈이 없어 상한이 없다(Job Object는 아직 쓰지 않는다). macOS는 RLIMIT_AS를
    잘 지키지 않아 사실상 상한이 없다. 둘 다 SECURITY.md의 알려진 한계에 적어 두었다.
    """
    mb = memory_limit_mb()
    if mb <= 0:
        return None
    try:
        import resource
    except ImportError:  # Windows
        return None
    limit = mb * 1024 * 1024
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ValueError, OSError):
        return None
    return limit // (1024 * 1024)


def serve(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    limit_memory()
    sys.stdout.write(READY_LINE + "\n")  # import가 끝났다: 여기서부터 입력 처리 타임아웃을 센다
    sys.stdout.flush()
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        return  # 미리 띄워 둔 작업자가 쓰이지 않고 서버가 끝났다
    payload = json.loads(raw.decode("utf-8"))
    os.chdir(payload.pop("_cwd", "."))  # 미리 띄운 작업자는 임시 폴더에서 시작하므로 일할 폴더로 옮긴다
    out = sys.stdout
    sys.stdout = sys.stderr  # 라이브러리가 찍는 글이 결과 JSON과 섞이지 않게
    try:
        result = handler(payload)
    except MemoryError:
        result = {"ok": False, "errors": [TOO_LARGE]}
    except WorkerError as exc:
        result = {"ok": False, "errors": exc.errors}
        if exc.hint:
            result["hint"] = exc.hint
    except Exception as exc:  # 예상 못 한 실패도 짧게 돌려준다
        result = {"ok": False, "errors": [f"Internal error: {type(exc).__name__}: {exc}"[:300]]}
    out.write(json.dumps(result, ensure_ascii=True) + "\n")
    out.flush()
