"""`openstem-mcp --doctor`: 설치 상태 진단.

학생이 "그림이 안 나와요" 같은 문제를 알릴 때, 무엇이 빠졌는지 스스로 확인하고 이슈에 붙일 요약을 얻게 한다.
사람이 터미널에서 보는 글이라 한국어로 쓴다.

지키는 것
- 비밀 값(LMS 토큰·캘린더 주소, HTTP 비밀 키 파일 내용)은 읽어서 출력하지 않는다. "설정됨/안 됨"·"있음/없음"만.
- 끝의 "이슈에 붙여 넣을 요약"과 `--json` 출력은 홈 폴더를 `~`로, 사용자 이름을 `<사용자>`로 바꾸고, 비밀로 보이는
  환경변수 값이 우연히 섞여 들어가도(작업자 stderr 등) 가린다.
- 네트워크는 tectonic 번들 서버에 짧은 HEAD 요청 하나만 보낸다(`--offline`이면 보내지 않는다).
- 아무것도 설치하거나 내려받지 않는다. 결과물·자료 폴더가 없으면 서버처럼 만들고, 쓰기 시험 파일은 바로 지운다.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import platform
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openstem import __version__, output, sandbox

BUNDLE_URL = "https://relay.fullyjustified.net/default_bundle_v33.tar"  # tectonic 0.17 기본 번들
NETWORK_TIMEOUT = 5.0
TECTONIC_TIMEOUT = 30.0

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
MARK = {OK: "[정상]", WARN: "[주의]", FAIL: "[문제]", SKIP: "[건너뜀]"}

ISSUE_URL = "https://github.com/kimi94279-glitch/openstem-mcp/issues"


@dataclass
class Check:
    id: str
    label: str
    status: str
    detail: str
    hint: str = ""


# ── 점검 ────────────────────────────────────────────────────────────


def check_versions() -> Check:
    py = platform.python_version()
    os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
    status = OK if sys.version_info >= (3, 11) else FAIL
    return Check("versions", "버전", status, f"openstem {__version__} · Python {py} · {os_name}")


def check_tectonic() -> Check:
    from openstem.render import latex, tectonic_setup

    found = latex._locate_tectonic()
    if not found:
        configured = output.env_value(latex.TECTONIC_ENV)
        if configured:
            return Check("tectonic", "tectonic", FAIL, f"{latex.TECTONIC_ENV}에 적힌 파일이 없습니다: {configured}",
                         "경로를 고치거나 환경변수를 지우세요.")
        if tectonic_setup.can_download():
            return Check("tectonic", "tectonic", WARN, "아직 설치되지 않았습니다(처음 그림을 그릴 때 자동으로 받습니다).",
                         "터미널에서 `uvx openstem-mcp --warmup`을 한 번 실행하면 미리 받습니다.")
        return Check("tectonic", "tectonic", FAIL, "찾지 못했고 자동 설치도 할 수 없습니다(꺼져 있거나 이 OS·CPU용 릴리스가 없음).",
                     "README의 설치 안내대로 tectonic을 직접 설치하세요.")
    try:
        res = sandbox.run([found, "--version"], cwd=Path(found).parent, timeout=TECTONIC_TIMEOUT)
    except OSError as exc:
        return Check("tectonic", "tectonic", FAIL, f"{found} 실행 실패: {exc}", "백신이 막았는지, 파일이 깨졌는지 확인하세요.")
    first = (res.stdout.strip().splitlines() or [""])[0]
    if res.timed_out or res.returncode != 0 or "tectonic" not in first.lower():
        why = "시간 초과" if res.timed_out else f"종료 코드 {res.returncode}"
        return Check("tectonic", "tectonic", FAIL, f"{found} 실행 실패({why})", "백신이 막았는지, 파일이 깨졌는지 확인하세요.")
    return Check("tectonic", "tectonic", OK, f"{first} · {found}")


def check_tex_bundle() -> Check:
    from openstem.render import latex

    cache = latex._cache_dir()
    marker = cache / latex.WARMUP_MARKER
    if marker.exists():
        return Check("tex_bundle", "TeX 번들 준비", OK, f"준비됨 ({marker})")
    others = sorted(p.name for p in cache.glob("warm-*")) if cache.is_dir() else []
    detail = f"준비 표시 파일이 없습니다 ({marker})"
    if others:
        detail += f" · 예전 버전 표시: {', '.join(others)}"
    return Check("tex_bundle", "TeX 번들 준비", WARN, detail,
                 "`uvx openstem-mcp --warmup`을 한 번 실행하세요(처음 3~5분). 서버가 켜져 있으면 백그라운드로 준비 중일 수 있습니다.")


def check_bundle_server(offline: bool, opener: Callable[..., Any] | None = None) -> Check:
    if offline:
        return Check("bundle_server", "TeX 번들 서버 연결", SKIP, "--offline")
    from openstem.render import tectonic_setup

    host = urllib.request.urlparse(BUNDLE_URL).hostname
    request = urllib.request.Request(BUNDLE_URL, method="HEAD", headers={"User-Agent": f"openstem-mcp/{__version__} (doctor)"})
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=tectonic_setup._ssl_context())).open
    started = time.monotonic()
    try:
        with opener(request, timeout=NETWORK_TIMEOUT) as response:
            code = response.status
    except urllib.error.HTTPError as exc:  # 서버까지는 닿았다
        code = exc.code
    except (OSError, ValueError) as exc:
        reason = str(getattr(exc, "reason", None) or exc).splitlines()[0][:200] or type(exc).__name__
        return Check("bundle_server", "TeX 번들 서버 연결", FAIL, f"{host}에 닿지 못했습니다: {reason}",
                     "학교·회사 네트워크나 방화벽이 막는지 확인하세요. 이미 준비된 TeX 패키지만 쓰는 그림은 그려집니다.")
    ms = (time.monotonic() - started) * 1000
    status = OK if code < 400 else WARN
    return Check("bundle_server", "TeX 번들 서버 연결", status, f"{host} 응답 HTTP {code} ({ms:.0f} ms)")


def _writable(path: Path) -> str | None:
    """쓰기 시험. 문제가 없으면 None, 있으면 이유."""
    try:
        with tempfile.NamedTemporaryFile(prefix=".openstem-doctor-", dir=path):
            pass
    except OSError as exc:
        return str(exc).splitlines()[0][:200]
    return None


def check_folder(id: str, label: str, get: Callable[[], Path], env: str) -> Check:
    note = f" ({env}로 지정)" if output.env_value(env) else ""
    try:
        path = get()
    except OSError as exc:
        return Check(id, label, FAIL, f"폴더를 만들 수 없습니다: {exc}", f"{env}로 다른 폴더를 지정해 보세요.")
    problem = _writable(path)
    if problem:
        return Check(id, label, FAIL, f"{path}{note}: 쓸 수 없습니다({problem})",
                     "OneDrive·백신의 '제어된 폴더 액세스'가 막는지 확인하거나 다른 폴더를 지정하세요.")
    return Check(id, label, OK, f"{path}{note} · 쓰기 가능")


def check_output_folder() -> Check:
    return check_folder("output_dir", "결과물 폴더", output.output_dir, output.OUTPUT_DIR_ENV)


def check_materials_folder() -> Check:
    from openstem.tools import documents

    return check_folder("materials_dir", "자료 폴더", documents.input_dir, documents.INPUT_DIR_ENV)


def check_workers(modules: Sequence[str], timeout: float | None = None) -> Check:
    """작업자를 하나씩 띄워 import가 끝날 때까지(준비 신호) 걸린 시간을 잰다. 입력은 주지 않고 바로 끝낸다."""
    timeout = sandbox.IMPORT_TIMEOUT if timeout is None else timeout
    times: list[str] = []
    failed: list[str] = []
    slowest = 0.0
    for module in modules:
        short = module.rsplit(".", 1)[-1]
        started = time.monotonic()
        try:
            proc = sandbox._spawn(module)
        except OSError as exc:
            failed.append(f"{short}: {exc}")
            continue
        ready = sandbox._wait_ready(proc, timeout)
        took = time.monotonic() - started
        proc.kill()
        _out, err = proc.communicate()
        if ready:
            slowest = max(slowest, took)
            times.append(f"{short} {took:.1f}s")
            continue
        tail = [ln for ln in sandbox._decode(err).strip().splitlines() if ln.strip()][-1:]
        failed.append(f"{short}: " + ("시간 초과" if ready is None else (tail[0][:200] if tail else "준비 신호 없이 끝남")))
    detail = " · ".join(times)
    detail += f" · 메모리 상한 {_memory_limit_text()}"
    if failed:
        return Check("workers", "계산 작업자 import", FAIL, detail + "\n실패: " + " / ".join(failed),
                     "`uv cache clean` 뒤 다시 설치하거나, 이 요약을 이슈에 붙여 주세요.")
    status = WARN if slowest > 30 else OK
    hint = "처음 실행이면 .pyc·글꼴 캐시를 만드느라 느립니다. 한 번 더 실행해 보세요." if status == WARN else ""
    return Check("workers", "계산 작업자 import", status, detail, hint)


def _memory_limit_text() -> str:
    from openstem.workers import MEMORY_ENV, memory_limit_mb

    mb = memory_limit_mb()
    if mb <= 0:
        return f"꺼짐({MEMORY_ENV}=0)"
    if sys.platform == "win32":
        how = "Windows Job Object" if sandbox._job else "Windows Job Object 실패 → 없음"
    elif sys.platform == "darwin":
        how = "RLIMIT_AS, macOS에서는 거의 효과 없음"
    else:
        how = "RLIMIT_AS"
    return f"{mb} MB({how})"


def check_isolation() -> Check:
    """운영체제 격리(OPENSTEM_ISOLATION) 상태 한 줄."""
    from openstem import isolation

    m, st = isolation.mode(), isolation.status()
    if m == "off":
        return Check("isolation", "운영체제 격리", SKIP, isolation.describe(), "격리를 켜려면 OPENSTEM_ISOLATION을 지우거나 auto로 두세요.")
    if st.active:
        return Check("isolation", "운영체제 격리", OK, isolation.describe())
    hint = "Linux는 bubblewrap(bwrap)을 설치하세요. Windows는 아직 지원하지 않습니다(SECURITY.md)."
    return Check("isolation", "운영체제 격리", FAIL if m == "require" else WARN, isolation.describe(), hint)


def check_lms() -> Check:
    from openstem.lms import client

    names = {
        client.SCHOOL_ENV: "학교",
        client.URL_ENV: "주소",
        client.TYPE_ENV: "종류",
        client.TOKEN_ENV: "토큰",
        client.CALENDAR_ENV: "캘린더 주소",
    }
    parts = [f"{label} {'설정됨' if output.env_value(env) else '안 됨'}" for env, label in names.items()]
    connected = output.env_value(client.TOKEN_ENV) or output.env_value(client.CALENDAR_ENV)
    return Check("lms", "학교 LMS 설정", OK if connected else SKIP, " · ".join(parts),
                 "" if connected else "LMS를 쓰지 않으면 괜찮습니다. 쓰려면 docs/lms.md를 보세요.")


def check_http_secret() -> Check:
    from openstem import remote

    path = remote.secret_path()
    exists = path.is_file()
    return Check("http_secret", "HTTP 모드 비밀 키 파일", OK if exists else SKIP,
                 f"{'있음' if exists else '없음'} ({path})",
                 "" if exists else "`--http`(ChatGPT 등)를 쓰지 않으면 없어도 됩니다.")


def check_env() -> Check:
    names = sorted(k for k in os.environ if k.upper().startswith("OPENSTEM_") and output.env_value(k))
    return Check("env", "OPENSTEM_ 환경변수", OK, ", ".join(names) if names else "없음(기본값)")


def run_checks(workers: Sequence[str], *, offline: bool = False) -> list[Check]:
    checks: list[Callable[[], Check]] = [
        check_versions,
        check_tectonic,
        check_tex_bundle,
        lambda: check_bundle_server(offline),
        check_output_folder,
        check_materials_folder,
        lambda: check_workers(workers),
        check_isolation,
        check_lms,
        check_http_secret,
        check_env,
    ]
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 — 진단 도구는 한 항목이 터져도 나머지를 보여 준다
            name = getattr(fn, "__name__", "check")
            results.append(Check(name, name, FAIL, f"점검 중 오류: {type(exc).__name__}: {str(exc)[:200]}"))
    return results


# ── 가리기 ───────────────────────────────────────────────────────────


def _secret_values() -> list[str]:
    values = [v for k, v in os.environ.items() if sandbox._is_secret(k) and len(v.strip()) >= 6]
    with contextlib.suppress(Exception):  # 가리기 준비가 실패해도 진단은 계속한다(환경변수 값은 위에서 이미 모았다)
        from openstem.lms.logs import current_secrets

        values += list(current_secrets())
    return sorted({v.strip() for v in values if v.strip()}, key=len, reverse=True)


def redact(text: str, *, home: str | None = None, user: str | None = None, secrets: Sequence[str] | None = None) -> str:
    """이슈에 붙여도 되게: 비밀 값 → <비밀>, 홈 폴더 → ~, 사용자 이름 → <사용자>."""
    for secret in _secret_values() if secrets is None else secrets:
        text = text.replace(secret, "<비밀>")
    home = str(Path.home()) if home is None else home
    if home and home not in ("/", "\\"):
        variants = {home, home.replace("\\", "/"), home.replace("/", "\\")}
        for variant in sorted(variants, key=len, reverse=True):
            text = re.sub(re.escape(variant), "~", text, flags=re.IGNORECASE if sys.platform == "win32" else 0)
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 — 사용자 이름을 못 얻는 환경(컨테이너 등)
            user = ""
    if user and len(user) >= 2:
        text = re.sub(rf"(?<![\w]){re.escape(user)}(?![\w])", "<사용자>", text, flags=re.IGNORECASE)
    return text


# ── 출력 ────────────────────────────────────────────────────────────


def format_report(checks: Sequence[Check]) -> str:
    lines = [f"OpenSTEM 진단 (openstem-mcp {__version__})", ""]
    for c in checks:
        first, *rest = c.detail.splitlines() or [""]
        lines.append(f"{MARK[c.status]} {c.label}: {first}")
        lines += [f"        {ln}" for ln in rest]
        if c.hint and c.status in (WARN, FAIL, SKIP):
            lines.append(f"        → {c.hint}")
    problems = sum(c.status == FAIL for c in checks)
    warnings = sum(c.status == WARN for c in checks)
    lines += ["", f"문제 {problems}개, 주의 {warnings}개." if problems or warnings else "문제가 없습니다."]
    return "\n".join(lines)


def format_summary(checks: Sequence[Check]) -> str:
    """이슈에 붙여 넣을 요약(가린 것). 마크다운 코드 블록 하나."""
    body = [f"openstem-mcp {__version__} --doctor"]
    for c in checks:
        body.append(f"{c.status.upper():4} {c.id}: {' | '.join(c.detail.splitlines())}")
    return "```\n" + redact("\n".join(body)) + "\n```"


def to_json(checks: Sequence[Check]) -> str:
    data = {
        "version": __version__,
        "ok": not any(c.status == FAIL for c in checks),
        "checks": [{k: redact(v) if isinstance(v, str) else v for k, v in asdict(c).items()} for c in checks],
    }
    return json.dumps(data, ensure_ascii=False, indent=1)


def main(argv: Sequence[str], workers: Sequence[str]) -> int:
    """`openstem-mcp --doctor [--json] [--offline]`. 문제가 하나라도 있으면 1."""
    parser = argparse.ArgumentParser(prog="openstem-mcp --doctor", description="설치 상태를 진단하고 이슈에 붙일 요약을 출력한다.")
    parser.add_argument("--doctor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="JSON으로 출력(경로·비밀은 가린다)")
    parser.add_argument("--offline", action="store_true", help="네트워크 점검(번들 서버 연결)을 건너뛴다")
    args = parser.parse_args(list(argv))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # 한국어 Windows 콘솔(cp949)에서 못 쓰는 글자로 죽지 않게
    if not args.json:
        print("점검하는 중입니다(계산 작업자 import에 몇 초~수십 초 걸립니다)...", flush=True)
    checks = run_checks(workers, offline=args.offline)
    if args.json:
        print(to_json(checks))
    else:
        print()
        print(format_report(checks))
        print()
        print(f"── 이슈에 붙여 넣을 요약 ({ISSUE_URL}) ──")
        print("홈 폴더·사용자 이름·비밀 값은 가렸습니다. 붙이기 전에 한 번 더 확인하세요.")
        print(format_summary(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0
