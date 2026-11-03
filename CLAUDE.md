# CLAUDE.md

Claude Code 같은 코딩 에이전트가 이 저장소에서 일할 때 읽는 짧은 안내다. 설계와 규칙의 원문은 아래 문서에 있고, 이 파일은 그 문서를 가리키기만 한다. 내용이 겹치면 아래 문서가 맞다.

## 먼저 읽을 문서

| 알고 싶은 것 | 문서 |
|---|---|
| 구조, 설계 원칙, 작업자 프로세스, 렌더링·내보내기 흐름, 환경변수 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 툴·프롬프트·리소스 명세와 툴별 규칙 | [docs/tools.md](docs/tools.md) |
| 학교 LMS 연결(lms 툴) | [docs/lms.md](docs/lms.md) |
| 배포 경로, 릴리스, 버전 올리기, .mcpb, tectonic 자동 설치 | [docs/packaging.md](docs/packaging.md) |
| 왜 그렇게 정했는가 | [docs/decisions/](docs/decisions/) |
| 보안 범위와 알려진 한계 | [SECURITY.md](SECURITY.md) |
| 개발 환경, 시험, 기여 규칙 | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 변경 이력 | [CHANGELOG.md](CHANGELOG.md) |

## 깨면 안 되는 것

1. 서버는 LLM을 부르지 않는다([0001](docs/decisions/0001-no-llm-calls.md)).
2. 툴은 MCP를 모르고 `ToolResult`를 돌려준다. 성공은 경로 + 미리보기, 실패는 짧은 에러(최대 10줄)([0002](docs/decisions/0002-tool-result-contract.md)).
3. 임의 코드를 실행하는 툴을 만들지 않는다. 외부 입력은 `sandbox.py`를 거쳐 작업자 프로세스에서 시간 제한을 두고 처리하고, 서버는 sympy·RDKit·pymupdf를 import하지 않는다([0003](docs/decisions/0003-worker-processes.md)).
4. TeX 코드와 수식은 컴파일·파싱 전에 `texcheck`·`mathexpr`로 검사한다. 허용 패키지·문법을 늘릴 때는 그것이 파일을 읽거나 쓰는 길이 있는지 먼저 본다([0004](docs/decisions/0004-tex-token-check.md)).
5. 다른 회사 서비스의 이름·로고·UI·출력물을 가져오지 않는다.

## 일하는 방식

- 툴 설명은 호스트 LLM이 읽는 문서다. 언제 쓰는지, JSON 입력 예시 1개, `On failure:` 줄을 넣는다. LLM이 읽는 글은 영어, 사람이 보는 글은 한국어([0007](docs/decisions/0007-llm-facing-text-in-english.md)).
- 툴을 만들거나 바꾸면 `tests/`에 케이스를 더하고 `uv run pytest`를 통과시킨다. tectonic이 없으면 렌더링 시험이 건너뛰기로 끝나므로 `skipped` 수를 확인한다.
- 툴을 바꾸면 manifest 템플릿 `tools`, `docs/tools.md`, 관련 프롬프트와 플러그인 스킬도 함께 본다.
- 설계 결정을 바꾸면 `docs/decisions/`에 기록을 더한다. 버전을 올릴 때는 [docs/packaging.md](docs/packaging.md#버전-올리기)의 목록을 따르고 `CHANGELOG.md`를 고친다.
- 프로젝트 이름을 바꾸게 되면 `pyproject.toml`, README, `server.json`, `claude-plugin/`을 함께 고친다.
- 사용자는 코드를 직접 읽지 않는다. 단계가 끝날 때마다 무엇이 됐고 어떻게 확인하는지를 한국어 3줄로 요약한다.

## 자주 걸리는 것

- Windows에서 Claude Desktop·Claude Code가 서버를 띄워 둔 동안에는 `uv sync`가 실패한다. `uv run --no-sync pytest`를 쓴다.
- Claude Desktop 설정 파일은 앱이 켜져 있으면 덮어써진다. 개발 버전 등록은 앱을 끈 뒤 사용자가 터미널에서 `python scripts/register_claude_desktop.py`로 한다.
