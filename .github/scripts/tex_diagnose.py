"""CI 진단: `openstem-mcp --warmup`이 실패했을 때만 돈다. 최소 문서와 warmup 문서를 하나씩 tectonic으로 컴파일해
stdout·stderr·로그 끝부분을 그대로 출력한다(--warmup은 요약된 에러만 보여 준다). 결과와 상관없이 종료 코드는 0이다."""

from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from openstem.render import latex
from openstem.server import _warmup_sources

MINIMAL = r"\documentclass{article}\begin{document}hello\end{document}"


def run(name: str, source: str, exe: str) -> None:
    print(f"\n===== {name} =====", flush=True)
    with tempfile.TemporaryDirectory(prefix="openstem-diag-") as tmp:
        tex = Path(tmp) / "figure.tex"
        tex.write_text(source, encoding="utf-8")
        res = subprocess.run(
            [exe, "--untrusted", "--keep-logs", "--chatter", "default", "--color", "never", tex.name],
            cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
        print(f"exit code: {res.returncode}")
        print("--- stdout (tail) ---\n" + res.stdout[-3000:])
        print("--- stderr (tail) ---\n" + res.stderr[-6000:])
        log = Path(tmp) / "figure.log"
        if log.exists():
            print("--- figure.log (tail) ---\n" + log.read_text(encoding="utf-8", errors="replace")[-4000:])
    sys.stdout.flush()


def main() -> None:
    exe = latex._locate_tectonic()
    print(f"platform: {platform.platform()} / python {sys.version.split()[0]}")
    print(f"tectonic: {exe}")
    if not exe:
        print("tectonic을 찾지 못했다.")
        return
    print(subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip())
    run("minimal", MINIMAL, exe)
    for i, source in enumerate(_warmup_sources()):
        first = next((line for line in source.splitlines() if "documentclass" in line), "")
        run(f"warmup source {i} ({first.strip()[:80]})", source, exe)


if __name__ == "__main__":
    main()
