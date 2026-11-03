# 기여 안내

버그 보고, 학교 LMS 확인 결과, 그림 예제, 코드 기여를 모두 환영한다. 먼저 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)에서 구조와 설계 원칙을 읽어 주세요. 보안 문제는 공개 이슈가 아니라 [SECURITY.md](SECURITY.md)의 방법으로 알려 주세요.

## 개발 환경

필요한 것: [uv](https://docs.astral.sh/uv/), 인터넷 연결(첫 실행 때 tectonic과 TeX 패키지를 받는다).

```bash
uv sync --all-extras
uv run openstem-mcp --warmup
uv run pytest
```

`--warmup`은 tectonic이 없으면 공식 릴리스를 받아 설치하고 TeX 패키지(약 300 MB)를 미리 받는다(처음 한 번, 3~5분). MCP Inspector로 직접 불러 볼 수 있다.

```bash
npx @modelcontextprotocol/inspector uv run openstem-mcp
```

## 테스트

- 툴을 만들거나 바꿀 때마다 `tests/`에 케이스를 추가한다. 입력 → 기대 결과는 `tests/cases/*.yaml`에 둔다. `uv run pytest`가 통과해야 한다.
- **tectonic이 없으면 그림·템플릿·문서 내보내기 시험이 실패가 아니라 건너뛰기로 끝난다.** 결과 요약의 `skipped` 수를 확인하고, 렌더링을 건드렸다면 `--warmup`을 한 환경에서 다시 돌린다. `OPENSTEM_REQUIRE_TEX=1`을 두면 tectonic이 없을 때 건너뛰지 않고 실패한다(`tests/conftest.py`의 `tectonic` fixture·`require_tectonic()`). tectonic이 필요한 새 시험은 이 fixture를 받는다.

  ```bash
  OPENSTEM_REQUIRE_TEX=1 uv run pytest        # CI와 같은 조건
  ```
- 저장소의 `.python-version`(3.12)은 `.mcpb`에 들어가 호스트의 Python을 고정한다. 로컬 `uv run`도 이 버전을 쓴다. 다른 버전으로 시험하려면 `UV_PYTHON=3.11 uv run pytest`.
- `templates/`의 모든 예제는 컴파일 시험을 돈다(`tests/test_templates.py`).
- 느린 시험: `OPENSTEM_SLOW_TESTS=1`(.mcpb를 새 폴더에 풀어 manifest 명령 그대로 띄우기). 네트워크 시험: `OPENSTEM_NETWORK_TESTS=1`(실제 tectonic 다운로드).
- LMS는 가짜 서버(`tests/fake_lms.py`)로 시험한다. 실제 학교 LMS에서 확인한 결과(학교 이름, 되는 action, 안 되는 action, 에러 메시지)는 토큰을 빼고 이슈로 알려 주면 큰 도움이 된다.

## CI

`.github/workflows/ci.yml`이 push·pull_request마다 ubuntu·windows·macos × Python 3.11·3.14에서 잠금 파일 그대로(`uv sync --locked --all-extras`) `openstem-mcp --warmup` → `pytest`를 돌린다. `OPENSTEM_REQUIRE_TEX=1`이라 렌더링 시험이 건너뛰어지면 실패한다. tectonic과 TeX 번들은 캐시 폴더(Linux `~/.cache/openstem`·`~/.cache/Tectonic`, macOS `~/Library/Caches/…`, Windows `%LOCALAPPDATA%\openstem`·`%LOCALAPPDATA%\TectonicProject`)를 `actions/cache`로 다시 쓴다. 릴리스(`publish.yml`)도 이 워크플로를 먼저 돌려 통과해야 PyPI·`.mcpb`를 올린다. `uv.lock`을 바꿨다면 `uv lock --check`가 통과하는지 확인한다(`--locked`라 어긋나면 CI가 실패한다).

PR을 열면 `.github/PULL_REQUEST_TEMPLATE.md`의 확인 목록이 채워진다. 버그와 학교 LMS 확인 결과는 이슈 양식(버그 보고, 학교 LMS 확인 보고)으로 받는다. 토큰·캘린더 주소는 넣지 않는다.

## 툴을 추가하거나 바꿀 때

- 툴 구현은 MCP를 모르고 `ToolResult`만 돌려준다. 등록은 `src/openstem/server.py`에서 한다([결정 0002](docs/decisions/0002-tool-result-contract.md)).
- 외부 입력을 해석하는 무거운 처리(sympy, RDKit, pymupdf, pint)는 `src/openstem/workers/`의 작업자에서 한다. 서버 프로세스에서 이 라이브러리를 import하지 않는다([결정 0003](docs/decisions/0003-worker-processes.md)).
- 툴 설명(`DESCRIPTION`)은 호스트 LLM이 읽는 문서다. 언제 쓰는지, JSON 입력 예시 1개(`{"`로 시작), `On failure:` 줄을 꼭 넣는다(`tests/test_server.py`가 확인).
- 함께 고칠 곳: `packaging/mcpb/manifest.template.json`의 `tools`(`tests/test_mcpb.py`가 확인), [docs/tools.md](docs/tools.md), 그 툴을 쓰는 프롬프트(`src/openstem/prompts/`)와 플러그인 스킬(`claude-plugin/skills/`).

## 글의 언어

LLM이 읽는 글(툴·인자 설명, 에러·힌트·경고, 프롬프트·스킬 본문)은 짧은 영어로, 사람이 직접 보는 글(코드 주석, 메뉴 제목, CLI 출력, 설치 화면, 학생이 받는 문서 안의 글)은 한국어로 쓴다. 자세한 구분은 [결정 0007](docs/decisions/0007-llm-facing-text-in-english.md). 문서(이 파일, README, docs/)는 한국어로 쓴다.

## 설계를 바꿀 때

설계 원칙에 닿는 결정(새 외부 다운로드, 새 파일 접근 경로, 툴 결과 형식, 보안 장치)을 바꾸면 [docs/decisions/](docs/decisions/)에 결정 기록을 하나 더한다. 이미 있는 결정을 뒤집을 때도 원래 기록은 지우지 않고 새 기록에 무엇을 대체하는지 적는다.

## 버전 올리기와 배포

[docs/packaging.md](docs/packaging.md#버전-올리기)의 목록대로 버전을 함께 올리고 [CHANGELOG.md](CHANGELOG.md)에 항목을 더한다. GitHub 릴리스를 만들기 전에 [릴리스 전 점검(10분)](docs/packaging.md#릴리스-전-점검10분)을 하고 결과를 릴리스 노트에 적는다. 릴리스를 만들면 CI 시험이 다시 돌고, 통과하면 PyPI, `.mcpb`, MCP 레지스트리 배포가 자동으로 돈다.

## 개발 환경에서 자주 걸리는 것

- **Windows에서 `uv sync`·`uv run` 실패**: Claude Desktop·Claude Code가 `uv run openstem-mcp`로 서버를 띄워 두면 `.venv\Scripts\openstem-mcp.exe`가 잠겨 프로젝트를 다시 설치하려다 실패한다. 그동안은 `uv run --no-sync pytest`를 쓰고, 의존성만 바꿀 때는 `uv sync --inexact --no-install-project`를 쓴다. 서버를 끄면 평소대로 된다.
- **Claude Desktop에 개발 버전 붙이기**: 실행 중인 Claude Desktop은 `claude_desktop_config.json`을 메모리 사본으로 통째로 다시 써서, 앱이 켜져 있을 때 고친 내용은 사라진다. 앱을 완전히 끈 뒤(트레이 아이콘 → 종료) `python scripts/register_claude_desktop.py`를 실행하고 다시 켠다(앱이 켜져 있으면 스크립트가 거절한다). Claude Code 세션은 앱의 자식 프로세스라 앱을 끄면 같이 꺼지므로, 이 단계는 별도 터미널에서 한다.
- **첫 호출이 느리다**: 작업자 import와 TeX 패키지 받기 때문이다. `--warmup`을 한 번 한다.

## AI 도구로 만든 기여

이 프로젝트는 개발에 AI 코딩 도구(Claude Code)를 쓴다. 저장소 루트의 [CLAUDE.md](CLAUDE.md)는 그런 도구가 이 저장소에서 지킬 규칙을 적은 짧은 안내이고, 설계 문서는 이 파일과 `docs/`에 있다. AI 도구로 만든 기여도 사람이 쓴 기여와 같은 기준을 거친다: 시험이 통과해야 하고, 기여자가 변경 내용을 이해하고 설명할 수 있어야 한다.
