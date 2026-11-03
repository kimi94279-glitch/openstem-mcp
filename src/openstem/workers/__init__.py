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


def serve(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
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
    except WorkerError as exc:
        result = {"ok": False, "errors": exc.errors}
        if exc.hint:
            result["hint"] = exc.hint
    except Exception as exc:  # 예상 못 한 실패도 짧게 돌려준다
        result = {"ok": False, "errors": [f"Internal error: {type(exc).__name__}: {exc}"[:300]]}
    out.write(json.dumps(result, ensure_ascii=True) + "\n")
    out.flush()
