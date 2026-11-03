# 변경 이력

이 프로젝트의 눈에 띄는 변경을 적는다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르고, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따른다(1.0 전에는 마이너 버전에서도 툴 인자가 바뀔 수 있다).

## [Unreleased]

### 바뀜

- 설계 문서를 `CLAUDE.md` 한 파일에서 나눴다: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)(구조·설계 원칙), [docs/tools.md](docs/tools.md)(툴 명세), [docs/lms.md](docs/lms.md)(LMS 연결), [docs/packaging.md](docs/packaging.md)(배포), [docs/decisions/](docs/decisions/)(결정 기록), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), 이 파일. `CLAUDE.md`는 코딩 에이전트용 짧은 안내로 줄였다.

## [0.3.2] - 2026-11-03

### 추가

- Claude Code 플러그인: 서버와 스킬 6개(풀이 검산, 그림, 시험 대비, 실험 보고서, 발표·문서, 학교 LMS)를 한 줄로 설치(`/plugin install openstem --marketplace kimi94279-glitch/openstem-mcp`).
- 공식 MCP 레지스트리 등록: `io.github.kimi94279-glitch/openstem-mcp`.
- 개인정보 처리방침([PRIVACY.md](PRIVACY.md))과 선언문([MANIFESTO.md](MANIFESTO.md)).

## [0.3.1] - 2026-11-03

### 추가

- `lms` 툴의 `sync`: 과목의 읽을 수 있는 파일을 자료 폴더 `LMS/<과목>/`에 한 번에 받는다. 다음부터는 새로 올라오거나 바뀐 파일만 받고, 바뀐 파일은 덮어쓴다. 한 번에 40초·30개·500 MB까지 받고, 남으면 다시 부르라고 알린다.

## [0.3.0] - 2026-11-03

첫 공개 릴리스.

### 추가

- 툴 10개: `render_tikz`, `get_template`(검증된 예제 40개), `plot_function`, `plot_data`, `render_molecule`, `verify_math`, `check_units`, `read_document`, `export_document`, `lms`.
- 프롬프트 7개: `solve`, `figure`, `cheatsheet`, `exam`, `slides`, `flashcards`, `lab_report`.
- Claude Desktop 원클릭 설치 파일(`.mcpb`)과 tectonic 자동 설치(버전·SHA-256 고정).
- HTTP 모드(`--http`, `--tunnel`): ChatGPT 등 원격 MCP 클라이언트 연결.

### 알려진 한계

- Claude Desktop 더블클릭 설치, 실제 ChatGPT 연결, 실제 학교 LMS는 실사용으로 확인하지 못했다.
- macOS·Linux의 tectonic 자동 설치는 해시만 고정했고 실제로 돌려 보지 못했다.
- Windows ARM은 지원하지 않는다(pandoc 패키지 없음).

## 0.3.0 이전 개발 단계

공개 전에 다음 순서로 만들었다(M5의 0.2.0은 내부 버전이며 배포하지 않았다).

- **M1**: 서버 뼈대, `render_tikz`, `figure` 프롬프트, 회로 템플릿 5개. 완료 기준: Claude Desktop에서 "RC 직렬회로 그려줘" → SVG 저장 + 채팅에 미리보기.
- **M2**: `render_molecule`, `plot_function`, 템플릿 30개.
- **M3**: `verify_math`, `solve` 프롬프트, 시험 케이스 20개.
- **M4**: `export_document`, `cheatsheet`, 한국어 README, PyPI 배포 준비(`uvx openstem-mcp`), MIT 공개 준비.
- **M5(0.2.0)**: 대학 전공 계산(verify_math: 미분방정식·행렬·라플라스·급수·합·부등식·확률분포), `check_units`, `plot_data`, `read_document`(PDF·한글·워드·파워포인트), export_document pptx·시험지·플래시카드, `exam`·`slides`·`flashcards`·`lab_report` 프롬프트, 다이어그램 템플릿 10개.
- **M6(0.3.0)**: 원클릭 설치(.mcpb + tectonic 자동 준비), HTTP 모드(ChatGPT 연결), `lms` 툴(Canvas·Moodle 읽기 전용). 완료 기준: .mcpb를 새 폴더에 풀어 manifest의 명령 그대로 띄우면 모든 툴이 되고, HTTP 모드에 MCP 클라이언트로 붙어 툴을 부르고, 가짜 Canvas·Moodle 서버로 lms의 모든 action이 통과.

[Unreleased]: https://github.com/kimi94279-glitch/openstem-mcp/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/kimi94279-glitch/openstem-mcp/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/kimi94279-glitch/openstem-mcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/kimi94279-glitch/openstem-mcp/releases/tag/v0.3.0
