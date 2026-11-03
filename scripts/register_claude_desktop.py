"""개발 중인 이 저장소의 openstem 서버를 Claude Desktop 설정에 등록한다(Windows).

Claude Desktop은 실행 중에 설정을 메모리에 들고 있다가 파일 전체를 다시 쓰므로,
앱이 켜져 있을 때 고친 내용은 사라진다. 그래서 앱을 완전히 종료한 뒤에 실행한다.

    python scripts/register_claude_desktop.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path(os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json"))


def desktop_running() -> bool:
    """Claude Desktop 앱 본체(WindowsApps 또는 AnthropicClaude 아래 Claude.exe)가 떠 있는지."""
    cmd = "Get-CimInstance Win32_Process -Filter \"Name='Claude.exe'\" | ForEach-Object { $_.ExecutablePath }"
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, errors="replace"
    ).stdout
    return any("WindowsApps" in line or "AnthropicClaude" in line for line in out.splitlines())


def server_entry() -> dict:
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv를 찾을 수 없습니다. https://docs.astral.sh/uv/ 에서 설치하세요.")
    entry = {"command": uv, "args": ["--directory", str(REPO), "run", "openstem-mcp"]}
    tectonic = shutil.which("tectonic")
    if tectonic:
        entry["env"] = {"OPENSTEM_TECTONIC": tectonic}
    return entry


def register(config: Path) -> dict:
    raw = config.read_text(encoding="utf-8") if config.exists() else "{}"
    data = json.loads(raw)
    if config.exists():
        shutil.copyfile(config, config.with_name(config.stem + ".backup-before-openstem.json"))
    entry = server_entry()
    data.setdefault("mcpServers", {})["openstem"] = entry
    out = json.dumps(data, ensure_ascii=False, indent=2) + ("\n" if raw.endswith("\n") else "")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(out, encoding="utf-8", newline="")
    return entry


def main() -> None:
    if desktop_running():
        sys.exit("Claude Desktop이 아직 켜져 있습니다. 트레이 아이콘 → 종료로 완전히 끈 뒤 다시 실행하세요.")
    entry = register(CONFIG)
    print(f"등록 완료: {CONFIG}")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print("이제 Claude Desktop을 켜고 채팅에서 'RC 직렬회로 그려줘'라고 해 보세요.")


if __name__ == "__main__":
    main()
