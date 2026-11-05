"""새 PC 설치 점검: 사용자가 설치하는 길 그대로 서버를 띄우고 툴을 불러 본다(docs/packaging.md "릴리스 전 점검" 1~3).

    python scripts/install_check.py --mcpb dist/openstem-0.3.4.mcpb   # Claude Desktop 확장: manifest의 명령 그대로
    python scripts/install_check.py --uvx openstem-mcp==0.3.4         # Claude Code 플러그인(.mcp.json): uvx로
    python scripts/install_check.py --uvx "--from . openstem-mcp"     # 이 저장소 코드로(uvx 인자를 그대로 넘긴다)

표준 라이브러리만 쓴다(MCP stdio는 줄마다 JSON-RPC 한 개). 결과물·자료 폴더는 새 임시 폴더이고, tectonic과 TeX 번들은
처음 쓰는 PC처럼 내려받는다(이미 받아 둔 캐시가 있으면 그것을 쓴다). 단계별 결과를 JSON으로 출력하고,
GITHUB_STEP_SUMMARY가 있으면 표를 쓴다. 한 단계라도 실패하면 종료 코드 1.

Claude Desktop·Claude Code의 화면(더블클릭 설치, 권한 창)은 이 스크립트가 확인하지 못한다. 사람이 하는 점검은 그대로 필요하다.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

FIRST_RUN = "First-run setup"  # 툴이 TeX 준비 중이라고 알리는 글(export·tikz)
START_TIMEOUT = 900.0  # 처음엔 가상환경을 만들고 의존성을 설치한다
CALL_TIMEOUT = 300.0
WARMUP_LIMIT = 900.0  # TeX 첫 준비를 기다리는 최대 시간

RC_CIRCUIT = r"""\begin{circuitikz}
  \draw (0,0) to[battery1, l=$\mathcal{E}$, invert] (0,3)
        to[nos, l=$S$] (3,3)
        to[R, l=$R$, i>_=$I$] (6,3)
        to[C, l=$C$] (6,0)
        -- (0,0);
  \node at (3,1.5) {RC 직렬회로};
\end{circuitikz}"""

EXAM = r"""## 문제 1 (5점) {space=3}

함수 $f(x) = x^3 - 3x$의 극값을 구하라.

## 문제 2 (5점)

$\int_0^1 x e^{x} \, dx$를 계산하라.

# 정답과 해설

## 문제 1

$f'(x) = 3x^2 - 3 = 0$에서 $x = \pm 1$.

## 문제 2

부분적분으로 $1$.
"""

REPORT = """## 결과

단진자의 주기는 $T = 2\\pi\\sqrt{L/g}$이고, 감쇠는 선행 연구와 같다 [@kim2020].
"""

BIB = """@article{kim2020,
  author = {Kim, Minsu and Lee, Jiwon},
  title = {Damped oscillation in student labs},
  journal = {Journal of Physics Education},
  volume = {12}, pages = {1--10}, year = {2020}
}
"""


class Server:
    """MCP 서버 하나를 stdio로 띄워 JSON-RPC로 부른다."""

    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0
        )
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        self.next_id = 0

    def _read_out(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read_err(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace").rstrip())
            del self.stderr[:-40]

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.next_id += 1
        rid = self.next_id
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{method}: no answer in {timeout:.0f}s")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(f"server exited (code {self.proc.poll()}): " + " | ".join(self.stderr[-8:]))
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                raise RuntimeError(f"not JSON on stdout: {line[:200]!r}") from None
            if message.get("id") == rid:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]
            # 알림(log 등)은 넘긴다

    def call(self, tool: str, arguments: dict[str, Any], timeout: float = CALL_TIMEOUT) -> tuple[bool, dict[str, Any]]:
        result = self.request("tools/call", {"name": tool, "arguments": arguments}, timeout)
        text = next((c["text"] for c in result.get("content", []) if c.get("type") == "text"), "{}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"text": text[:500]}
        return not result.get("isError"), data

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def mcpb_command(bundle: Path, work: Path, out: Path) -> tuple[list[str], dict[str, str], list[str]]:
    """묶음을 새 폴더에 풀고 manifest의 명령·환경을 호스트처럼 치환한다. (서버 명령, 환경, doctor 명령)."""
    root = work / "extension"
    with zipfile.ZipFile(bundle) as zf:
        zf.extractall(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["server"]["mcp_config"]
    values = {"${__dirname}": str(root), "${user_config.output_dir}": str(out)}
    args = [values.get(a, a) for a in config["args"]]
    env = {k: values.get(v, "") for k, v in config["env"].items()}  # 비워 둔 설정은 빈 값으로 온다
    uv = shutil.which(config["command"]) or config["command"]
    command = [uv, *args]
    return command, env, [*command, "--doctor", "--json"]


def uvx_command(spec: str, out: Path) -> tuple[list[str], dict[str, str], list[str]]:
    uvx = shutil.which("uvx") or "uvx"
    command = [uvx, *shlex.split(spec)]
    env = {"OPENSTEM_OUTPUT_DIR": str(out), "OPENSTEM_INPUT_DIR": "", "OPENSTEM_LMS_SCHOOL": "", "OPENSTEM_LMS_TOKEN": ""}
    return command, env, [*command, "--doctor", "--json"]


def step(results: list[dict[str, Any]], name: str, fn) -> Any:
    started = time.monotonic()
    try:
        ok, detail, value = fn()
    except Exception as exc:  # 한 단계가 실패해도 다음 단계를 본다
        ok, detail, value = False, f"{type(exc).__name__}: {exc}"[:600], None
    results.append({"step": name, "ok": ok, "seconds": round(time.monotonic() - started, 1), "detail": detail})
    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    return value


def with_warmup(server: Server, tool: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any], int]:
    """처음 쓰는 PC에서는 툴이 "First-run setup … call again"을 돌려준다. 사용자가 다시 부르듯 기다렸다가 다시 부른다."""
    deadline = time.monotonic() + WARMUP_LIMIT
    tries = 0
    while True:
        tries += 1
        ok, data = server.call(tool, arguments)
        waiting = not ok and any(FIRST_RUN in e for e in data.get("errors", []))
        if not waiting or time.monotonic() > deadline:
            return ok, data, tries
        time.sleep(30)


# 사용자 PC에는 없는 값: CI의 토큰, 이 스크립트가 출력하려고 켜는 UTF-8 모드(서버의 Windows 인코딩 문제를 가릴 수 있다)
NOT_ON_USER_PC = ("GH_TOKEN", "GITHUB_TOKEN", "PYTHONUTF8", "PYTHONIOENCODING")


def run(command: list[str], env_extra: dict[str, str], doctor: list[str], out: Path) -> list[dict[str, Any]]:
    env = {k: v for k, v in os.environ.items() if k not in NOT_ON_USER_PC} | env_extra
    materials = out / "자료"
    materials.mkdir(parents=True, exist_ok=True)
    (materials / "refs.bib").write_text(BIB, encoding="utf-8")
    results: list[dict[str, Any]] = []
    features: dict[str, bool] = {}
    server = Server(command, env)
    try:

        def start():
            info = server.request(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "install-check", "version": "1"}},
                START_TIMEOUT,
            )
            server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = {t["name"]: t for t in server.request("tools/list", {}, CALL_TIMEOUT)["tools"]}
            names = sorted(tools)
            ok = {"render_tikz", "verify_math", "export_document", "read_document"} <= set(names)
            export_schema = tools.get("export_document", {}).get("inputSchema", {})
            features["hwpx"] = "hwpx" in json.dumps(export_schema.get("properties", {}).get("format", {}))
            features["bibliography"] = "bibliography" in export_schema.get("properties", {})
            return ok, f"{info.get('serverInfo', {}).get('name')} {info.get('serverInfo', {}).get('version')}: {len(names)} tools", None

        step(results, "start + tools/list", start)
        if not results[-1]["ok"]:
            return results

        def circuit():
            ok, data, tries = with_warmup(server, "render_tikz", {"code": RC_CIRCUIT, "packages": ["circuitikz"]})
            png = Path(data.get("png_path", ""))
            return ok and png.is_file(), f"tries={tries} " + (png.name if ok else json.dumps(data, ensure_ascii=False)[:400]), None

        step(results, "render_tikz RC circuit (TeX first run)", circuit)

        def check():
            ok, data = server.call("verify_math", {"op": "equal", "expr": "(x+1)^2", "against": "x^2+2*x+1"})
            return ok and data.get("matches") is True, f"matches={data.get('matches')}", None

        step(results, "verify_math (x+1)^2 = x^2+2x+1", check)

        def exam_pdf():
            ok, data, tries = with_warmup(server, "export_document", {"markdown": EXAM, "format": "pdf", "title": "중간고사 대비", "layout": "exam"})
            good = ok and data.get("problems") == 2 and bool(data.get("answer_page")) and Path(data.get("path", "")).is_file()
            return good, f"tries={tries} pages={data.get('pages')} answer_page={data.get('answer_page')}" + ("" if ok else f" {data.get('errors')}"), None

        step(results, "export_document exam pdf", exam_pdf)

        def exam_hwpx():
            if not features.get("hwpx"):
                return None, "format=hwpx is not in this version", None
            ok, data = server.call("export_document", {"markdown": EXAM, "format": "hwpx", "title": "중간고사 대비", "layout": "exam"})
            path = Path(data.get("path", ""))
            valid = path.is_file() and zipfile.is_zipfile(path) and "Contents/section0.xml" in zipfile.ZipFile(path).namelist()
            return ok and valid, f"equations={data.get('equations')} math_images={data.get('math_images')}" + ("" if ok else f" {data.get('errors')}"), None

        step(results, "export_document exam hwpx", exam_hwpx)

        def report_docx():
            arguments = {"markdown": REPORT, "format": "docx", "title": "실험 보고서", "name": "보고서"}
            if not features.get("bibliography"):  # 참고문헌 인자가 없는 이전 버전: 인용 없이 docx만
                ok, data = server.call("export_document", arguments)
                return ok, "plain docx (bibliography is not in this version)" + ("" if ok else f" {data.get('errors')}"), data
            ok, data = server.call("export_document", {**arguments, "bibliography": "refs.bib"})
            good = ok and data.get("references") == 1 and data.get("citations") == 1
            return good, f"references={data.get('references')} citations={data.get('citations')}" + ("" if ok else f" {data.get('errors')}"), data

        docx = step(results, "export_document report docx + citation", report_docx)

        def read_back():
            if not docx or not docx.get("path"):
                return False, "no docx to read", None
            shutil.copyfile(docx["path"], materials / "보고서.docx")
            ok, data = server.call("read_document", {"path": "보고서.docx"})
            text = json.dumps(data, ensure_ascii=False)
            return ok and "단진자" in text, "read 보고서.docx" + ("" if ok else f" {data.get('errors')}"), None

        step(results, "read_document docx", read_back)
    finally:
        server.close()

    def doctor_run():
        proc = subprocess.run(doctor, env=env, capture_output=True, timeout=600)
        try:
            report = json.loads(proc.stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return False, f"exit {proc.returncode}: {proc.stdout[:300]!r} {proc.stderr[-300:]!r}", None
        checks = [c for c in report.get("checks", []) if isinstance(c, dict)]
        names = ", ".join(f"{c.get('id')}={c.get('status')}" for c in checks)
        return bool(report.get("ok")), f"openstem {report.get('version')}: {names}"[:700], None

    step(results, "--doctor --json", doctor_run)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--mcpb", type=Path, help=".mcpb 파일")
    target.add_argument("--uvx", help="uvx에 넘길 인자(예: openstem-mcp==0.3.4)")
    parser.add_argument("--label", default="", help="요약 표 제목")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # Windows 콘솔에서도 한글 결과를 출력한다

    with tempfile.TemporaryDirectory(prefix="openstem-install-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        out = work / "OpenSTEM"  # 사용자 문서 폴더의 OpenSTEM 대신
        if args.mcpb:
            command, env, doctor = mcpb_command(args.mcpb.resolve(), work, out)
            label = args.label or f".mcpb {args.mcpb.name}"
        else:
            command, env, doctor = uvx_command(args.uvx, out)
            label = args.label or f"uvx {args.uvx}"
        print(json.dumps({"label": label, "command": command}, ensure_ascii=False), flush=True)
        results = run(command, env, doctor, out)

    passed = all(r["ok"] is not False for r in results)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        mark = {True: "통과", False: "실패", None: "해당 없음"}
        rows = "\n".join(f"| {r['step']} | {mark[r['ok']]} | {r['seconds']} | {str(r['detail']).replace('|', '/')} |" for r in results)
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {label} ({sys.platform})\n\n| 단계 | 결과 | 초 | 내용 |\n|---|---|---:|---|\n{rows}\n\n")
    print(json.dumps({"label": label, "platform": sys.platform, "passed": passed}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
