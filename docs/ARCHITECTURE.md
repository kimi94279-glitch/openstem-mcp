# 아키텍처

openstem-mcp가 어떻게 짜여 있는지, 왜 그렇게 짰는지 정리한 문서다. 툴별 입력·출력은 [tools.md](tools.md), 학교 LMS 연결은 [lms.md](lms.md), 배포와 설치 묶음은 [packaging.md](packaging.md), 보안 범위와 한계는 [../SECURITY.md](../SECURITY.md), 주요 결정의 배경은 [decisions/](decisions/)에 있다.

## 한눈에 보기

이미 Claude·ChatGPT 같은 AI를 쓰는 학생이 이공계 공부에 필요한 도구(학술 그림, 풀이 검산, 자료 읽기, 문서 내보내기)를 별도 구독 없이 쓰게 해 주는 MCP 서버다. 풀이와 판단은 사용자의 AI(MCP 호스트)가 하고, 이 서버는 그리기·계산·변환만 한다.

```
MCP 호스트 (Claude Desktop · Claude Code · ChatGPT 등)
   │  stdio(기본) 또는 streamable HTTP(remote.py, --http)
   ▼
server.py      툴·프롬프트·리소스 등록, ToolResult → CallToolResult 변환
   │
   ▼
tools/*.py     입력 검사(서버 프로세스 안), 결과 정리
   │                                   │
   │ sandbox.run()                      │ sandbox.run_python()
   ▼                                   ▼
tectonic · pandoc (외부 실행 파일)        workers/*.py (python -I -m, 별도 프로세스)
   │                                   │
   └───────────────┬───────────────────┘
                   ▼
결과물 폴더 (output.py)   <YYYYMMDD-HHMMSS>_<이름>.svg / .pdf / .png / .docx / ... + .json(재현 기록)·원본
```

## 설계 원칙

1. **서버는 LLM을 부르지 않는다.** 풀이·판단은 사용자의 LLM(호스트)이 하고, 서버는 렌더링·검증만 한다. 운영비가 0이어야 무료로 유지할 수 있다. → [결정 0001](decisions/0001-no-llm-calls.md)
2. **툴은 LLM이 스스로 고칠 수 있게 돌려준다.** 성공하면 파일 경로와 미리보기 PNG(이미지 콘텐츠)를, 실패하면 짧고 구체적인 에러(줄 번호 포함, 최대 10줄)를 돌려준다. 긴 로그를 통째로 돌려주지 않는다. → [결정 0002](decisions/0002-tool-result-contract.md)
3. **임의 코드를 실행하는 툴은 만들지 않는다.** 외부 입력(LaTeX, 수식 문자열, SMILES, 자료 파일)은 서버 프로세스가 아니라 `sandbox.py`가 띄우는 별도 프로세스에서 시간 제한(기본 20초)을 두고 처리한다. sympy 파싱도 안에서 eval을 쓰므로 예외가 없다. 이것은 프로세스 분리와 시간 제한이지 운영체제 수준의 샌드박스는 아니다(한계는 [SECURITY.md](../SECURITY.md)). → [결정 0003](decisions/0003-worker-processes.md)
4. **다른 회사 서비스의 이름·로고·UI·출력물은 가져오지 않는다.**

## 스택

- Python 3.11 이상, 패키지 관리 `uv`, 공식 MCP Python SDK(`mcp` 2.x, `MCPServer` 데코레이터 방식)
- 전송: stdio(사용자 PC에서 로컬 실행, 기본) + streamable HTTP(`--http`, ChatGPT 등 원격 MCP 클라이언트용)
- 렌더링: `tectonic`(LaTeX 엔진, 외부 실행 파일) → PDF → `pymupdf`로 SVG·PNG 변환
- 계산·그림: `sympy`, `numpy`, `matplotlib`, `rdkit`, `pint`
- 문서: `pypandoc_binary`(pandoc 3.9 포함), `olefile`(HWP 5.x)
- LMS: `httpx2`(mcp 2.x와 같은 httpx 갈래)
- 선택 의존성: `antlr4-python3-runtime==4.11.*`(extra `latex`, LaTeX 수식 입력)

## 폴더 구조

```
openstem-mcp/
├── README.md                 # 사용자용(한국어). 설치·사용법
├── CHANGELOG.md              # 변경 이력
├── CONTRIBUTING.md           # 개발 환경·테스트·기여 규칙
├── SECURITY.md               # 보안 범위·한계·취약점 알리는 법
├── PRIVACY.md                # 개인정보 처리방침
├── MANIFESTO.md              # 선언문(방향을 정할 때 따르는 가치)
├── CLAUDE.md                 # 코딩 에이전트용 짧은 안내(이 문서들을 가리킨다)
├── LICENSE                   # MIT
├── pyproject.toml            # 실행 명령: openstem-mcp = openstem.server:main
├── server.json               # 공식 MCP 레지스트리 등록 정보
├── docs/                     # 이 문서, tools.md, lms.md, packaging.md, decisions/
├── src/openstem/
│   ├── server.py             # MCP 엔트리. 툴·프롬프트·리소스 등록만
│   ├── tools/                # 툴 구현(MCP를 모르고 ToolResult만 돌려준다)
│   │   ├── tikz.py           # render_tikz
│   │   ├── molecule.py       # render_molecule
│   │   ├── plot.py           # plot_function
│   │   ├── plotdata.py       # plot_data
│   │   ├── verify.py         # verify_math
│   │   ├── units.py          # check_units
│   │   ├── documents.py      # read_document(자료 폴더만)
│   │   ├── templates.py      # get_template·리소스
│   │   ├── lms.py            # lms(읽기 전용)
│   │   └── export.py         # export_document
│   ├── lms/                  # LMS 연결: client.py, canvas.py, moodle.py, schools.py, feed.py, ical.py, common.py
│   ├── remote.py             # HTTP 모드(비밀 키 문지기, cloudflared 터널)
│   ├── render/
│   │   ├── latex.py          # tectonic 호출 + 로그에서 에러 줄 추출
│   │   ├── tectonic_setup.py # tectonic 자동 설치(버전·SHA-256 고정)
│   │   ├── convert.py        # PDF → SVG/PNG (pymupdf, 작업자 안에서만 쓴다)
│   │   ├── pages.py          # 변환을 작업자에게 맡기는 서버 쪽 함수
│   │   ├── tabular.lua       # 치트시트 표를 tabular로 바꾸는 pandoc 필터
│   │   ├── exam.lua          # 시험지 배치(풀이 공간, 해설 새 쪽)
│   │   ├── flashcards.lua    # 플래시카드 배치(양면 인쇄)
│   │   └── fonts.py          # 한글 글꼴 찾기(matplotlib·분자 범례용)
│   ├── workers/              # sandbox.run_python으로만 도는 작업자(서버에서 import하지 않는다)
│   │   ├── molecule.py       # RDKit 파싱·검사·그리기
│   │   ├── plot.py           # sympy 파싱 → numpy → matplotlib, 특이점 분석
│   │   ├── verify.py         # verify_math 계산·판정(시간 예산 안에서)
│   │   ├── units.py          # pint 차원 분석·단위 변환
│   │   ├── plotdata.py       # 측정 데이터 그래프·맞춤
│   │   ├── documents.py      # PDF·HWP·HWPX·docx 등에서 글자 뽑기
│   │   └── convert.py        # PDF·SVG 변환(시간 제한, 크기 제한)
│   ├── mathexpr.py           # 수식 문자열 글자 검사(서버) + sympy 파싱(작업자)
│   ├── texcheck.py           # TeX 코드 안전 검사(파일 접근·이름 조립 차단)
│   ├── sandbox.py            # 별도 프로세스 실행 + 시간 제한
│   ├── isolation.py          # 운영체제 격리(Linux bwrap, macOS sandbox-exec)
│   ├── output.py             # 저장 경로, 파일 이름 규칙
│   └── prompts/              # solve, figure, cheatsheet, exam, slides, flashcards, lab_report
├── templates/                # 검증된 TikZ 예제(circuits, physics, math, diagrams 각 10개)
├── claude-plugin/            # Claude 플러그인: plugin.json·.mcp.json·README·LICENSE·skills/
├── .claude-plugin/           # 이 저장소를 플러그인 마켓플레이스로(marketplace.json)
├── packaging/mcpb/           # Claude Desktop 확장(.mcpb) manifest 틀·launch.py·아이콘
├── scripts/                  # build_mcpb.py, register_claude_desktop.py
├── tests/                    # cases/(입력 → 기대 결과 yaml), fake_lms.py, test_*.py
└── examples/                 # README용 결과 이미지
```

## 툴 결과 규약

- mcp 2.x에서 FastMCP는 `mcp.server.mcpserver.MCPServer`로 이름이 바뀌었다(데코레이터 방식은 같다). 타입은 `mcp.types`에서 가져온다.
- 툴 구현(`tools/*.py`)은 MCP를 모르고 `tools.ToolResult`(ok, data, preview_png, more_images)만 돌려준다. `server.py`의 `_to_mcp`가 이것을 `CallToolResult`로 바꾼다: 텍스트 JSON 하나 + PNG 이미지들, 실패면 `is_error`.
- 에러는 `ToolResult.failure(errors, hint)`로 만들고 최대 10개만 싣는다.
- `more_images`는 결과에 그림 여러 장을 실을 때 쓴다(read_document의 쪽 그림).
- 대화마다 실리는 문맥을 줄이려고, pydantic이 인자마다 붙이는 스키마 `title`은 `server.py`가 지운다.
- 툴 설명은 호스트 LLM이 읽는 문서다. 언제 쓰는지, JSON 입력 예시 1개, `On failure:` 줄(실패하면 어떻게 고치는지)을 꼭 넣는다(`tests/test_server.py`가 확인). → [결정 0007](decisions/0007-llm-facing-text-in-english.md)

## 외부 입력 처리: 작업자 프로세스

외부 입력을 다루는 프로그램은 모두 `sandbox.py`를 거친다. → [결정 0003](decisions/0003-worker-processes.md)

- `sandbox.run(args, cwd, timeout)`: 외부 실행 파일(tectonic, pandoc)을 셸 없이 실행한다. 시간이 넘으면 죽이고 `timed_out`을 돌려준다. stdin은 막는다(MCP stdio 서버에서 자식이 프로토콜 스트림을 읽으면 안 된다).
- **자식 환경**: `run`과 작업자는 `sandbox.child_env()` 환경으로 돈다. `OPENSTEM_LMS_*`와 이름에 TOKEN·SECRET·PASSWORD·PASSWD·API_KEY·ACCESS_KEY·CREDENTIAL·PRIVATE_KEY가 든 변수를 빼고, 프록시·인증서·캐시 폴더 변수는 남긴다.
- **메모리 상한**: POSIX 작업자는 `workers.serve()`가 준비 신호 전에 `RLIMIT_AS`를 건다(기본 3072 MB, `OPENSTEM_WORKER_MEMORY_MB`). Windows는 `sandbox._spawn`이 작업자(예비 포함)를 띄운 직후 서버에 하나 있는 Job Object에 넣는다(ctypes, `JOB_OBJECT_LIMIT_PROCESS_MEMORY`로 프로세스별 커밋 메모리 상한 + `KILL_ON_JOB_CLOSE`로 서버가 끝나면 작업자도 끝남). job을 만들거나 넣지 못하면(구버전 Windows) 경고 로그 한 줄을 남기고 상한 없이 돈다. 어느 쪽이든 `MemoryError`는 "too large to compute" 에러가 된다. macOS는 RLIMIT_AS가 사실상 먹지 않는다([SECURITY.md](../SECURITY.md)).
- **운영체제 격리**(`isolation.py`, → [결정 0014](decisions/0014-os-isolation.md)): `OPENSTEM_ISOLATION=auto|off|require`(기본 auto). 처음 한 번 실제 작업자 설정으로 작은 파이썬을 띄워 쓸 수 있는지 보고(실행 파일 유무만 보지 않는다: AppArmor·중첩 샌드박스), 결과를 서버 로그에 한 줄 남긴다. 못 쓰면 auto는 지금처럼, require는 `isolation.Unavailable` → 툴의 짧은 에러.
  - Linux `bwrap`: 빈 루트에 `/usr`·`/lib*`·`/etc` 일부·인증서·파이썬(`python -I`의 sys.path, 홈 폴더 자체는 제외)·tectonic 실행 파일을 읽기 전용으로, 작업 폴더(tectonic은 번들 캐시도)만 쓰기로 붙인다. 홈 폴더는 보이지 않는다. `/tmp`는 빈 tmpfs. 작업자는 `--unshare-net`(`--unshare-all`), tectonic은 `--share-net`.
  - macOS `sandbox-exec`: 파일 읽기를 막고 시스템·파이썬·캐시·작업 폴더만 연다(stat은 허용), 쓰기는 작업 폴더만, 작업자는 `(deny network*)`.
  - tectonic은 `latex.compile_tex`가 `sandbox.run(..., isolate=isolation.tectonic_spec(exe, workdir))`로 부른다.
  - 작업자(예비 포함)는 띄울 때 전용 폴더 box(`openstem-box-*`)를 받고 그 폴더만 쓴다. `run_python`은 payload의 입력 파일(`sandbox.INPUT_KEYS` = `src`·`path`)을 `box/in`에 복사해 경로를 바꿔 넘기고, `box/work`에서 일하게 한 뒤 그 안의 일반 파일만(링크 제외) 호출한 `cwd`로 옮기고 box를 지운다. 작업자 환경의 `HOME`·`TMPDIR`·`MPLCONFIGDIR`는 box 안이다(matplotlib 글꼴 목록 캐시는 복사해 준다).
  - 기본 목록 밖 경로(다른 TeX 배포판 등)는 `OPENSTEM_ISOLATION_READ`로 더한다. Windows는 아직 격리가 없다.
- `sandbox.run_python("openstem.workers.<이름>", payload, cwd)`: sympy·RDKit·pymupdf·pint 처리. `python -I -m`(격리 모드: 현재 폴더·`PYTHON*` 환경변수·사용자 site-packages를 보지 않는다)로 빈 임시 폴더에서 돈다. payload는 stdin JSON, 결과는 stdout 마지막 줄 JSON이다(`workers.serve()`가 규약을 맞춘다). 작업자가 만든 파일은 서버가 `output.new_stem`으로 옮긴다.
- **준비 신호**: 작업자는 import를 마치면 stdout에 `OPENSTEM-READY`를 쓴다. `run_python`은 그 신호를 `IMPORT_TIMEOUT`(180초)까지 기다린 뒤 payload를 보내고, 그때부터 `timeout`(기본 20초)을 센다. 처음 설치한 환경은 .pyc 만들기·matplotlib 글꼴 캐시 때문에 import만 수십 초가 걸려서, 이것을 입력 처리 시간에 넣으면 첫 호출이 늘 시간 초과가 났다. 무거운 import는 신호 전에 끝내야 한다(plot 작업자는 `_preload()`, pint 준비 약 4초도 신호 전). 신호를 이미 읽은 예비 작업자는 `proc.openstem_ready`로 표시한다.
- **예비 작업자**: sympy import만 프로세스마다 약 5초라, 모듈마다 작업자를 하나씩 미리 띄워 둔다(import를 끝내고 stdin을 기다린다). 요청이 오면 그 작업자를 쓰고 바로 다음 것을 띄운다. 요청 하나에 프로세스 하나라는 분리는 그대로다. 미리 띄운 작업자는 시스템 임시 폴더에서 시작하므로 payload의 `_cwd`로 옮겨 가서 일한다. 서버가 끝나면 stdin이 닫혀 스스로 끝난다. 끄려면 `sandbox.SPARES_ENABLED = False`.
- **변환 작업자**: PDF·SVG → SVG/PNG/PDF 변환(pymupdf)은 `workers/convert.py`에서 하고, 서버는 `render/pages.py`로 맡기기만 한다(서버는 pymupdf를 import하지 않는다). 아주 촘촘한 패턴 그림은 렌더링에 수십 초가 걸리며 GIL을 쥐어 서버 전체가 멈췄다(점검에서 재현). 한 변이 200 cm를 넘는 그림은 변환 전에 거절한다.
- 작업자가 결과에 적은 파일 이름은 `sandbox.worker_file`로 작업 폴더 안인지 확인한 뒤 쓴다.
- 한 번 부를 때 3~5초(예비 작업자가 없으면 라이브러리 import 포함).

## 수식 입력

`mathexpr.check()`가 허용한 이름(함수 목록·`pi`·소문자 `e`·변수)·숫자(반각 0-9만)·연산자만 통과시키고, 통과한 토큰만 이어 붙여 `parse_expr`에 넘긴다(`__builtins__`는 비운다). 그래서 `_`, `.`, 문자열, 속성 접근은 eval까지 가지 못한다. `^`는 거듭제곱, `2x`는 곱하기로 읽는다. 대문자 `E`는 상수가 아니라 변수다(에너지·전기장). plot_function·verify_math·check_units가 이 모듈을 쓴다. 파싱은 작업자 안에서만 한다.

## LaTeX 렌더링

- **tectonic 찾는 순서**: `OPENSTEM_TECTONIC` → PATH → 캐시 폴더 `bin/`(`latex.tectonic_bin_dir()`). 없으면 공식 릴리스를 받아 설치한다(→ [packaging.md](packaging.md#tectonic-자동-준비), [결정 0005](decisions/0005-tectonic-auto-install.md)).
- **컴파일**: `latex.compile_tex`가 빈 임시 폴더에 `figure.tex`를 쓰고 `tectonic --untrusted`로 컴파일한다(쓸 수 있으면 운영체제 격리 안에서, 위 "외부 입력 처리"). `--untrusted`는 shell-escape만 끄므로, 파일에 닿는 TeX 명령은 컴파일 전에 `texcheck.py`가 막고, render_tikz 문서에는 pgf·pgfplots의 파일 읽기·gnuplot 내부 명령을 끄는 `tikz.FILE_GUARD`가 `\begin{document}` 앞에 들어간다(→ [결정 0004](decisions/0004-tex-token-check.md), [SECURITY.md](../SECURITY.md)).
- **첫 실행**: TeX 번들을 받느라 약 4분 걸린다(캐시 후 3~6초). 서버 시작 때 준비 표시 파일이 없으면 `BACKGROUND_WARMUP`이 tectonic → TeX 번들 순서로 백그라운드에서 준비하고, 그동안 render_tikz는 최대 40초 기다린 뒤 "준비 중, 잠시 후 다시" 에러를 돌려준다. `openstem-mcp --warmup`으로 미리 받을 수 있다.
- **준비 표시 파일**: `warm-<버전>`. 위치는 `%LOCALAPPDATA%\openstem`(macOS `~/Library/Caches/openstem`, Linux `~/.cache/openstem`). 이름에 버전이 들어가서, 새 버전을 처음 띄우면 TeX 준비(tikz 문서 + export 배치별 문서)와 파이썬 작업자 준비(`sandbox.warm_workers`)를 백그라운드로 다시 한다. `--warmup`도 둘 다 한다.
- **에러 줄**: 로그에서 `!` 줄과 `l.<번호>` 줄만 뽑는다. `l.<번호>`와 메시지 속 `input line N`은 생성 문서가 아니라 사용자가 보낸 code 기준으로 바꾸고, 머리말·꼬리말에서 난 에러는 그렇게 표시한다.
- **패키지·라이브러리 자동 감지**: circuitikz·pgfplots·chemfig·siunitx는 코드에서 쓰이면 자동으로 넣는다. TikZ 라이브러리도 문법을 보고 넣는다(`($`→calc, `Stealth`→arrows.meta, `right=of`→positioning, `coil`→decorations.pathmorphing 등, `tikz._LIBRARY_DETECT`). 라이브러리를 한꺼번에 다 넣으면 그림마다 1.5~2.5초가 늘어 감지 방식을 쓴다. 환경 없이 `\draw`만 오면 tikzpicture로 감싼다.
- **타임아웃**: 기본 20초(`OPENSTEM_TIMEOUT`). 한글이 들어간 그림은 xetexko와 은글꼴을 불러오는 데만 바쁜 PC에서 10초 가까이 들어(같은 그림이 한글 없으면 6초, 있으면 12~14초) `HANGUL_EXTRA_TIMEOUT`(20초)을 더한다. 끝나지 않는 입력을 끊는 장치는 그대로다.

## 한글 처리

- **TeX**: code에 한글이 있으면 서버가 `xetexko` + 은글꼴(UnBatang/UnDotum, tectonic 번들에 들어 있어 OS와 상관없다)을 넣는다. 패키지 허용 목록과 별개다. `$...$` 안의 한글은 `\text{}`로 감싼다(수식 글꼴에 한글이 없어 빠진다).
- **matplotlib·분자 범례**: 시스템 글꼴(맑은 고딕 등)을 먼저 찾고, 없으면 pymupdf에 들어 있는 Droid Sans Fallback을 쓴다. matplotlib에서는 한글 글꼴을 `font.family` 맨 앞에 둬야 한다(`변위 $y$`처럼 수식이 섞인 글은 글자별 대체 없이 첫 글꼴만 쓴다).
- **RDKit**: RDKit의 글자 렌더러는 한글을 못 그려서, 분자는 RDKit SVG → pymupdf로 PDF 한 쪽에 옮기고(여백을 잘라 낸다) 범례는 pymupdf가 쓴 뒤, render_tikz와 같은 변환 작업자로 SVG·PNG를 만든다.

## 문서 내보내기

export_document의 처리 순서다. 왜 정규식이 아니라 문서 구조를 쓰는지는 [결정 0006](decisions/0006-export-via-pandoc-ast.md).

1. pandoc(pypandoc_binary, 3.9)으로 마크다운을 문서 구조(JSON)로 읽는다(`--sandbox`). 입력 형식은 `markdown-raw_tex-raw_html-raw_attribute-yaml_metadata_block`이라 마크다운 속 `\명령`은 글자로 나오고, YAML 머리말로 제목에 수식을 넣는 길도 막힌다.
2. 구조 안의 모든 `Math`(texcheck + 중괄호 짝 + 한글 `\text{}` 감싸기)와 `Image`(결과물 폴더 안 파일만, URL·`data:` 등 거절, `%20` 풀어 읽기)를 검사·교체한다. 경로에 공백이 있는 그림(Windows 사용자 이름)은 `<경로>`로 감싸 준다.
3. 배치(layout)별 규칙을 구조에서 검사한다(시험지: 정답 제목, 카드: 2열 표). 위반은 Lua 필터를 돌리기 전에 파이썬이 짧은 에러로 돌려준다. 배치는 Lua 필터(`render/exam.lua`, `render/flashcards.lua`, `render/tabular.lua`)가 만들고, 서버는 필터가 알아볼 클래스만 붙인다.
4. 그림을 작업 폴더에 `imgN`으로 옮긴다. docx·pptx는 같은 이름 PDF > SVG > 원본 순으로 300dpi PNG(dpi 정보 기록), pdf는 같은 이름 PDF(벡터) > SVG→PDF. MuPDF는 SVG `clip-path`를 무시해서 SVG를 변환하면 곡선이 축 밖으로 튀어나오므로, 같은 이름 PDF를 먼저 쓴다.
5. 구조를 docx·pptx나 standalone LaTeX로 바꾸고, PDF는 `latex.compile_tex(..., reruns=0)`로 한 번만 컴파일한다(상호 참조가 없어 한 번이면 되고, 두 번 돌리면 두 배 느리다).
6. 첫 쪽 미리보기도 변환 작업자(`pages.preview`)로 만든다.

머리말: `float`로 그림을 그 자리에 고정하고, 한글이 있으면 `HANGUL_SETUP`(굵은 은글꼴 포함)과 그림·표 이름 한글화를 넣는다. 치트시트는 extarticle 9pt 두 단 + titlesec·enumitem으로 간격을 줄이고 제목을 두 단에 걸쳐 한 줄로 둔다. pandoc은 표를 longtable로 쓰는데 두 단에서는 컴파일이 실패해서, 치트시트는 `render/tabular.lua`로 표를 tabular로 바꾼다.

## HTTP 모드

`remote.py`. 왜 이 방식인지는 [결정 0009](decisions/0009-http-mode-secret-url.md).

- `MCPServer.streamable_http_app(stateless_http=True, json_response=True)`를 `SecretGate`(ASGI)로 감싸, 직접 연 소켓으로 uvicorn(mcp 의존성)에 띄운다. Windows는 `SO_EXCLUSIVEADDRUSE`, `--port 0`은 빈 포트를 고른다.
- 키는 `secrets.token_urlsafe(32)`. 설정 폴더(`%APPDATA%\openstem`, macOS `~/Library/Application Support/openstem`, Linux `$XDG_CONFIG_HOME/openstem`, `OPENSTEM_CONFIG_DIR`로 바꿈)의 `http-secret`에 둔다(POSIX에서는 0600). `--new-secret`으로 교체한다.
- `/<키>/mcp` 또는 `/mcp` + `Authorization: Bearer <키>`만 통과하고(`hmac.compare_digest`), 나머지는 모두 같은 404다. GET은 405(보낼 SSE가 없고, stateless에서 SDK에 넘기면 빈 연결이 남는다).
- stateless·JSON 응답이라 세션 만료가 없고 SSE를 못 넘기는 터널도 괜찮다.
- SDK의 Host 검사(DNS 리바인딩 방어)는 끈다: 터널 Host는 미리 알 수 없고, 키 없는 요청은 SDK에 닿기 전에 404라 Host 검사가 더 막는 것이 없다.
- 접근 로그는 끄고 모든 로그 핸들러에 키 가리기 필터(traceback 포함)를 단다. 키는 시작 줄에 한 번만 출력한다.
- 동기 툴은 SDK가 `anyio.to_thread.run_sync`로 돌려 동시 호출이 서로 막지 않는다.
- uvicorn은 정리 후 받은 신호를 다시 일으켜 SIGTERM·Ctrl+Break면 finally 없이 죽으므로, `capture_signals`를 덮어 터널을 끄고 다시 일으키지 않는다(비공개 `_captured_signals`, uvicorn 0.54 기준).
- `--tunnel`은 `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<포트>`를 자식으로 띄워 출력에서 `https://<임의>.trycloudflare.com`(api. 제외)을 30초 안에 찾는다. 없거나 실패하면 설치 안내 후 로컬로만 연다. cloudflared는 내려받지 않는다(`OPENSTEM_CLOUDFLARED`로 경로 지정).
- 툴은 stdio와 같다. 결과 파일은 서버를 띄운 PC에 저장된다.

## 결과물 저장

- 저장 폴더: `OPENSTEM_OUTPUT_DIR`, 없으면 `~/OpenSTEM/`.
- 파일 이름: `<YYYYMMDD-HHMMSS>_<이름>.<확장자>`. 같은 그림의 svg·pdf·png는 이름이 같다. `output.new_stem`이 프로세스 안에서 나눠 준 stem을 기억해, 같은 초·같은 이름의 동시 호출이 서로 덮어쓰지 않게 한다.
- **재현 기록**: 결과물을 만드는 툴은 `output.save_record()`로 같은 stem 옆에 `<stem>.json`과 원본을 남긴다. 다른 대화에서 그림을 고치거나 보고서의 분석을 다시 하기 위한 것이다([MANIFESTO](../MANIFESTO.md)의 재현 가능한 연구).
  - 원본: render_tikz는 사용자가 보낸 `code`를 `<stem>.tex`(서버가 감싼 전체 문서는 `<stem>.full.tex`), export_document는 입력 마크다운을 `<stem>.md`, plot_data는 실제로 그린 데이터를 `<stem>.csv`로. plot_function·render_molecule은 식·SMILES가 기록의 `inputs`에 있어 원본 파일이 따로 없다.
  - `<stem>.json`: `schema`, `tool`, `openstem_version`, `versions`(그 툴이 쓴 것만: tectonic `--version` 첫 줄, pandoc, sympy·numpy·matplotlib·rdkit·pymupdf), `created`(ISO 시각), `inputs`(툴 인자 전체), `outputs`(같은 stem의 파일 이름), `results`(맞춤 계수·표준오차·R², 분자식·분자량, `parsed` 식·축 범위, 시험지 `problems` 등), `warnings`(특이점 경고 등), `source`.
  - 작업자에서만 import하는 라이브러리(sympy·numpy·matplotlib·RDKit·pymupdf)의 버전은 작업자가 결과의 `versions`에 실어 보낸다. 서버는 이 라이브러리를 import하지 않는다. tectonic 버전은 실행 파일마다 한 번만 묻는다.
  - 기록에는 이 PC의 경로를 남기지 않는다(`output.portable()`: 결과물 폴더 안 경로는 상대 경로, 홈 폴더는 `~`). 이 툴들은 비밀을 인자로 받지 않는다.
  - 툴 결과에 `source_path`(원본이 있을 때)와 `record_path`를 싣는다. `OPENSTEM_RECORD=0`이면 쓰지 않는다. 저장이 실패해도 툴은 성공이고 `warnings`에 한 줄을 더한다.
- `output.env_value()`는 빈 값과 채워지지 않은 자리표시자(`${user_config.x}`)를 둘 다 "설정 안 함"으로 본다(Claude Desktop 확장·플러그인이 비워 둔 설정을 그렇게 넘길 수 있다).

## 설정(환경변수)

| 변수 | 뜻 | 기본값 |
|---|---|---|
| `OPENSTEM_OUTPUT_DIR` | 결과물 폴더 | `~/OpenSTEM` |
| `OPENSTEM_INPUT_DIR` | 자료 폴더(read_document가 여는 곳) | `<결과물 폴더>/자료` |
| `OPENSTEM_READ_CACHE` | `0`이면 read_document 추출 결과 캐시(캐시 폴더 `read-cache/`, 200 MB LRU)를 끈다 | 켜짐 |
| `OPENSTEM_TIMEOUT` | 외부 입력 처리 시간 제한(초) | 20 |
| `OPENSTEM_RECORD` | `0`이면 재현 기록(`<stem>.json`·원본 파일)을 남기지 않는다 | 켬 |
| `OPENSTEM_WORKER_MEMORY_MB` | 파이썬 작업자 메모리 상한(MB, POSIX RLIMIT_AS·Windows Job Object). 0이면 끔 | 3072 |
| `OPENSTEM_ISOLATION` | 운영체제 격리: `auto`(쓸 수 있으면 격리), `off`, `require`(못 쓰면 툴 에러). Linux bwrap, macOS sandbox-exec | auto |
| `OPENSTEM_ISOLATION_READ` | 격리 안에서 더 읽게 할 경로(OS 경로 구분자로 여러 개). 기본 목록 밖 TeX·파이썬용 | 없음 |
| `OPENSTEM_TECTONIC` | tectonic 실행 파일 경로 | PATH → 캐시 폴더 |
| `OPENSTEM_NO_DOWNLOAD` | `1`이면 tectonic 자동 설치를 하지 않는다 | 꺼짐 |
| `OPENSTEM_CONFIG_DIR` | HTTP 모드 비밀 키를 둘 설정 폴더 | OS별 설정 폴더 |
| `OPENSTEM_CLOUDFLARED` | cloudflared 실행 파일 경로 | PATH |
| `OPENSTEM_LMS_SCHOOL` | 학교 이름 | 없음 |
| `OPENSTEM_LMS_TOKEN` | LMS 액세스 토큰(비밀) | 없음 |
| `OPENSTEM_LMS_CALENDAR_URL` | LMS 캘린더 구독 주소(비밀) | 없음 |
| `OPENSTEM_LMS_URL`, `OPENSTEM_LMS_TYPE` | LMS 주소·종류 직접 지정(canvas, moodle) | 없음 |
| `OPENSTEM_SLOW_TESTS`, `OPENSTEM_NETWORK_TESTS` | 느린 시험·네트워크 시험 켜기(개발용) | 꺼짐 |

## 의존성 메모

- pymupdf는 `>=1.24,<2`: 2.0은 인텔 맥 wheel이 없고 onnxruntime 등을 끌고 온다. 쓰는 기능은 1.24부터 있다.
- mcp는 `<3`.
- 버전은 `src/openstem/__init__.py`의 `__version__` 한 곳에만 쓴다(hatch dynamic version). 배포 파일들의 버전은 [packaging.md](packaging.md#버전-올리기)의 목록대로 함께 고친다.
- 저장소 루트 `templates/`는 wheel 안 `openstem/templates`로 들어간다(pyproject `force-include`).
