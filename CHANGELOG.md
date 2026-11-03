# 변경 이력

이 프로젝트의 눈에 띄는 변경을 적는다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르고, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따른다(1.0 전에는 마이너 버전에서도 툴 인자가 바뀔 수 있다).

## [Unreleased]

### 보안

- TeX 코드 검사를 강화했다. 그림 데이터를 표·파일·gnuplot에서 가져오는 문법을 낱말이 아니라 토큰 위치로 판정하고(`coordinates`와 식만 허용), render_tikz와 export_document(암기 카드 배치) 문서에 pgf·pgfplots의 파일 읽기 명령을 끄는 방어선을 넣었다. 이전 검사는 일부 문법에서 PC의 파일을 읽을 수 있었다.
- 작업자·tectonic·pandoc에 LMS 토큰 등 비밀 환경변수를 넘기지 않는다. POSIX에서 작업자 메모리 상한(기본 3 GB, `OPENSTEM_WORKER_MEMORY_MB`)을 둔다.
- stdio 모드에서 LMS 토큰·캘린더 주소가 서버 로그(호스트 로그 파일)에 남던 문제를 고쳤다. 모든 로그에서 비밀 값을 가린다.
- 릴리스 파이프라인: 시험이 통과해야 PyPI·.mcpb 배포가 돈다. Actions를 커밋 SHA로, mcpb CLI를 정확한 버전으로 고정하고, checkout 자격 증명을 남기지 않으며, mcp-publisher 해시를 확인한다. Dependabot을 켰다.

### 고침

- lms: Moodle 파일 id를 목록 순번 대신 파일 주소 기준 고정 값으로 바꿔, 폴더에 파일이 추가·삭제될 때 sync가 다른 파일 내용으로 덮어쓰던 문제를 고쳤다(`.openstem-sync.json` version 2, 예전 기록은 이름·sha256으로 이어받음).
- lms: sync가 사용자가 고친 파일이나 열려 있는 파일을 덮어쓰지 않고 새 버전을 `이름 (2)`로 저장한다. 긴 한글 파일 이름, 동시 호출, 실패한 파일 때문에 멈추는 문제, 지난 일정이 overdue로 보이는 문제, Canvas 제출 여부 판정, Moodle 마감 잘림 표시, 숨긴 공지 글, 느린 응답 시간 제한을 고쳤다.
- read_document: 목록이 100개에서 표시 없이 잘리던 것을 고쳤다(폴더별 목록, `truncated`). 결과에 "남이 쓴 데이터" 안내를 넣었다.
- verify_math: solve가 원식에 맞지 않는 근(로그 방정식의 음수 근 등)을 주던 문제, 실수 항등식을 기호 판정으로 "다르다"고 하던 문제, LaTeX 홀수 거듭제곱근을 복소수로 계산하던 문제, 함수 이름이 붙은 변수(`sinx`)를 받아들이던 문제를 고쳤다. 수치 비교 범위를 넓히고 결과에 범위를 적는다.
- check_units: rpm·rad/s와 Hz 사이 변환이 2π배 틀리던 문제를 고쳤다. ton·degC 안내를 더했다.
- plot_function: log 발산을 "구멍"으로 판정하던 문제, 진동 불연속을 점프로 보고하던 문제를 고쳤다.
- render_molecule: Linux에서 수소만으로 된 분자(`[H][H]` 등)와 그것이 든 반응식이 그려지지 않던 문제를 고쳤다.
- 시간 초과 안내가 무거운 입력에도 "첫 실행이면 다시 해 보라"고 하던 것을 고쳤다.
- Linux용 tectonic에서 한글 문서 컴파일이 줄바꿈 데이터 오류로 실패하던 문제를 피한다.

### 추가

- CI(Linux·Windows·macOS)와 `OPENSTEM_REQUIRE_TEX=1`(tectonic이 없으면 TeX 시험을 건너뛰지 않고 실패), 사람이 하는 릴리스 전 점검표, 이슈·PR 양식, `.python-version`(3.12).

### 바뀜

- README: LMS 표를 "연결 시험 대상"으로 바꾸고 실제 확인 학교가 아직 없다는 점, Canvas 토큰 정책, ChatGPT 가능 요금제, Claude Code 첫 실행 안내를 적었다.
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
