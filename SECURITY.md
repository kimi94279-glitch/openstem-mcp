# 보안 정책

openstem-mcp는 사용자 PC에서 돌며, AI(MCP 호스트)가 보낸 입력과 사용자의 자료 파일, 학교 LMS에서 받은 데이터를 다룬다. 이 문서는 무엇을 지키려 하는지, 어떤 장치가 있는지, 무엇이 아직 막혀 있지 않은지, 취약점을 어떻게 알리는지를 적는다.

## 지원하는 버전

보안 수정은 가장 최근 릴리스(현재 0.3.x)에만 한다. 이전 버전을 쓰고 있다면 먼저 최신 버전으로 올린다.

## 취약점 알리기

- GitHub 저장소의 **Security → Report a vulnerability**(비공개 보고)로 알려 주세요.
- 고쳐지기 전에는 공개 이슈·토론에 재현 방법을 올리지 말아 주세요.
- 보고에는 버전, 운영체제, MCP 호스트(Claude Desktop, Claude Code, ChatGPT 등), 재현 입력, 기대한 동작과 실제 동작을 적어 주세요. LMS 토큰·캘린더 주소·HTTP 모드 비밀 키 같은 실제 비밀 값은 넣지 마세요.

## 지키려는 것

- 사용자 PC의 파일(자료 폴더·결과물 폴더 밖의 개인 파일)
- LMS 액세스 토큰과 캘린더 구독 주소
- HTTP 모드 비밀 키
- 서버 프로세스의 가용성(입력 하나가 서버를 멈추지 못하게)

## 믿지 않는 입력

- **AI가 보내는 툴 인자**: AI는 자료 파일·LMS 공지·웹 페이지에 숨은 지시(프롬프트 주입)를 따라 위험한 입력을 만들 수 있다. 그래서 TeX 코드·수식·경로는 사람이 쓴 것이라도 검사한다.
- **자료 파일**: PDF·HWP·HWPX·Office 파일은 망가졌거나 악의적일 수 있다.
- **LMS 데이터**: 제목·공지·파일 이름·파일 내용은 남이 쓴 데이터다.

## 있는 장치

- 임의 코드를 실행하는 툴이 없다. 서버는 LLM을 부르지 않는다.
- 외부 입력은 별도 프로세스에서 시간 제한(기본 20초)을 두고 처리한다([결정 0003](docs/decisions/0003-worker-processes.md)).
- 작업자·tectonic·pandoc에는 서버의 비밀 환경변수를 물려주지 않는다: `OPENSTEM_LMS_*`와 이름에 TOKEN·SECRET·PASSWORD·PASSWD·API_KEY·ACCESS_KEY·CREDENTIAL·PRIVATE_KEY가 든 변수를 뺀다(`sandbox.child_env`). 프록시·인증서·캐시 폴더 변수는 남긴다.
- Linux에서는 파이썬 작업자의 메모리(주소 공간)에 상한을 둔다(기본 3072 MB, `OPENSTEM_WORKER_MEMORY_MB`, 0이면 끔). 넘으면 "too large to compute" 에러로 돌아온다.
- 수식 문자열은 허용한 토큰만 sympy에 넘긴다(속성 접근·밑줄 이름·문자열은 eval까지 가지 못한다).
- TeX 코드(render_tikz, export_document의 수식)는 컴파일 전에 토큰 단위로 검사해 파일·외부에 닿는 명령과 이름을 조립하는 수단을 막는다. `\addplot`·TikZ `plot` 뒤의 데이터 소스는 `coordinates`와 식만 허용한다(table·file·function·gnuplot·graphics·shell, 매크로 인자는 거절). render_tikz 문서에는 그 밖에 pgf·pgfplots가 파일을 읽고 쓰는 내부 명령을 에러로 바꾸는 줄(`tikz.FILE_GUARD`)이 들어간다. tectonic은 `--untrusted`로 shell-escape를 끄고, 매번 빈 임시 폴더에서 컴파일한다([결정 0004](docs/decisions/0004-tex-token-check.md)).
- read_document는 자료 폴더와 결과물 폴더 안 파일만 연다(심볼릭 링크·정션은 실제 경로로 다시 검사). 압축을 푼 크기에 상한을 둔다.
- export_document는 결과물 폴더 안 그림만 넣고, URL·`data:` 그림과 raw TeX·HTML을 거절한다([결정 0006](docs/decisions/0006-export-via-pandoc-ast.md)).
- LMS 토큰은 설정한 LMS 주소(https)로만 보내고 다른 origin으로 가는 리디렉션에는 싣지 않는다. 툴 결과와 서버 로그(호스트가 남기는 로그 파일 포함)에서 토큰과 캘린더 주소를 지운다. 비밀번호는 받지 않는다([결정 0008](docs/decisions/0008-lms-tokens-not-passwords.md)).
- tectonic 자동 설치는 버전·크기·SHA-256을 코드에 고정한 공식 릴리스만 받는다([결정 0005](docs/decisions/0005-tectonic-auto-install.md)).
- HTTP 모드는 기본으로 127.0.0.1에만 열고, 비밀 키가 틀린 요청은 모두 404다([결정 0009](docs/decisions/0009-http-mode-secret-url.md)).

## 알려진 한계

- **운영체제 수준의 격리가 없다.** 작업자 프로세스와 tectonic·pandoc은 서버와 같은 사용자 권한으로 돈다(비밀 환경변수만 뺀다). 파일 시스템·네트워크 접근을 운영체제가 막지 않는다.
- **메모리 상한은 Linux에서만 확실하다.** 상한은 `RLIMIT_AS`로 건다. macOS는 이 값을 사실상 지키지 않아 상한이 없는 것과 같고, Windows는 `resource` 모듈이 없어 상한이 없다(Job Object는 아직 쓰지 않는다). 이 두 OS에서는 시간 제한(기본 20초)이 끝날 때까지 작업자가 메모리를 키울 수 있다. 상한은 파이썬 작업자에만 걸고 tectonic·pandoc에는 걸지 않는다. 상한 안이라도 20초 동안 1 GB 넘게 키우는 입력(예: `2^2^2^2^2^2`)은 시간 제한으로 끊긴다.
- **TeX 검사는 알려진 위험 문법을 막는 방식이다.** tectonic `--untrusted`는 TeX의 파일 읽기·쓰기(절대 경로 읽기, `../` 쓰기 포함)를 막지 않으므로, 패키지 매크로가 파일을 여는 길을 검사가 빠짐없이 막았다고 보장하지 못한다. TikZ 경로는 매크로를 펼쳐 읽어서 `plot` 같은 낱말을 매크로로 나눠 쓸 수 있고, 이것은 토큰 검사가 아니라 `FILE_GUARD`가 막는다. export_document의 flashcards 머리말에는 아직 `FILE_GUARD`가 없어 수식 칸은 토큰 검사에만 기댄다.
- **툴 결과는 AI에게 간다.** 자료 파일과 LMS 글에 들어 있는 지시를 AI가 따를 위험은 호스트 쪽 방어에 달려 있다.
- 실제 학교 LMS, 실제 ChatGPT 연결, macOS에서의 자동 설치는 아직 실사용으로 확인하지 못했다.

## 사용자에게 권하는 설정

- 결과물 폴더(`OPENSTEM_OUTPUT_DIR`)와 자료 폴더(`OPENSTEM_INPUT_DIR`)를 홈 폴더 전체나 문서 폴더 전체로 두지 않는다. read_document는 이 두 폴더 안의 모든 파일을 열 수 있다.
- LMS 토큰은 만료 기간을 짧게 만들고, 쓰지 않으면 LMS 설정에서 폐기한다. 캘린더 주소는 비밀번호처럼 다룬다.
- HTTP 모드의 비밀 키가 담긴 주소를 남에게 보이지 않는다. 새었으면 `openstem-mcp --http --new-secret`으로 바꾼다.
- 버그를 알릴 때 호스트 로그 파일을 붙이기 전에 토큰·주소 같은 비밀 값이 들어 있지 않은지 확인한다.
