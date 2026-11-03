"""MCP 툴 구현. 각 모듈은 MCP를 모르고 ToolResult만 돌려준다(MCP 변환은 server.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_ERRORS = 10


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any]
    preview_png: bytes | None = None
    more_images: list[bytes] = field(default_factory=list)  # 미리보기 뒤에 붙는 PNG들(read_document의 쪽 그림)

    @classmethod
    def failure(cls, errors: list[str], hint: str | None = None, **extra: Any) -> ToolResult:
        data: dict[str, Any] = {"ok": False, "errors": errors[:MAX_ERRORS]}
        if hint:
            data["hint"] = hint
        data.update({k: v for k, v in extra.items() if v})
        return cls(ok=False, data=data)
