# 배포와 설치 묶음

openstem-mcp가 사용자에게 가는 길, 릴리스 절차, 버전을 올릴 때 함께 고칠 곳을 정리한다.

## 배포 경로

1. **PyPI** — `uvx openstem-mcp`. Claude Code(`claude mcp add openstem -- uvx openstem-mcp`)와 설정 파일 방식이 쓴다.
2. **GitHub 릴리스의 `.mcpb`** — Claude Desktop 원클릭 설치.
3. **공식 MCP 레지스트리** — `io.github.kimi94279-glitch/openstem-mcp`(`server.json`). PyPI 소유 확인은 README 첫머리의 `<!-- mcp-name: … -->` 주석으로 한다.
4. **Claude 플러그인** — `claude-plugin/`. 저장소 루트의 `.claude-plugin/marketplace.json`으로 `/plugin install openstem --marketplace kimi94279-glitch/openstem-mcp`. Anthropic 디렉터리에는 developer portal에 "Plugin bundle"로 제출한다(2026-11 기준 디렉터리는 로컬 서버 `.mcpb` 확장을 더는 받지 않고 플러그인으로만 받는다).

로컬 MCP 서버는 Claude Code와 내 PC에서 도는 Cowork에서만 뜨고, claude.ai 채팅에서는 플러그인의 스킬만 실린다(채팅은 `.mcpb`를 쓴다).

## 릴리스 절차

GitHub에서 릴리스를 만들기 전에 아래 [릴리스 전 점검(10분)](#릴리스-전-점검10분)을 한다. 릴리스를 만들면 `.github/workflows/publish.yml`이 다음 순서로 돈다.

0. **test**: `ci.yml`을 그대로 불러 ubuntu·windows·macos × Python 3.11·3.14에서 `uv sync --locked --all-extras` → `openstem-mcp --warmup` → `pytest`(`OPENSTEM_REQUIRE_TEX=1`, tectonic이 없으면 건너뛰지 않고 실패). 하나라도 실패하면 아래 작업은 돌지 않는다.
1. **publish**: `uv build` → `uv publish --trusted-publishing always`(PyPI 신뢰할 수 있는 게시, 토큰 없음). PyPI 쪽 설정(pending publisher)은 관리자가 pypi.org에서 한 번 한다: 프로젝트 `openstem-mcp`, 워크플로 `publish.yml`, 환경 `pypi`.
2. **bundle**: `scripts/build_mcpb.py`로 `.mcpb`를 묶어 릴리스에 붙인다. 공식 CLI는 `MCPB_CLI`에 정확한 버전(`@anthropic-ai/mcpb@2.1.2`)으로 고정했다.
3. **registry**: `mcp-publisher`를 받아 워크플로에 고정한 sha256과 릴리스의 `registry_<버전>_checksums.txt`로 확인한 뒤, 릴리스에 붙은 `.mcpb`의 sha256을 `server.json`에 채우고, PyPI가 새 버전을 내줄 때까지 기다린 뒤 `mcp-publisher login github-oidc` → `publish`.

워크플로 공급망: 서드파티 Action은 커밋 SHA로 고정하고(주석에 태그, `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag>`로 확인), 모든 checkout은 `persist-credentials: false`다. `.github/dependabot.yml`이 Action과 `uv.lock` 갱신 PR을 매주 연다. `MCP_PUBLISHER_VERSION`을 올리면 `MCP_PUBLISHER_SHA256`도 같은 릴리스의 checksums 파일 값으로 고친다.

wheel에 templates·prompts·workers가 다 들어가는지, `uvx --from <wheel> openstem-mcp`로 새 환경에서 띄워 툴이 모두 되는지 확인한다.

## 릴리스 전 점검(10분)

CI는 코드가 시험을 통과하는지만 본다. 사용자가 실제로 설치해 쓰는 길은 사람이 확인한다. 0.3.1·0.3.2는 시험이 실패한 채 배포됐다. 릴리스마다 **새 PC나 새 사용자 계정**(개발 환경의 캐시·PATH·설정이 없는 곳)에서 다음을 하고, 결과를 릴리스 노트에 적는다.

1. **.mcpb 설치 → 회로도 1장**: 이 커밋으로 만든 `.mcpb`를 Claude Desktop에서 더블클릭해 설치하고 "RC 직렬회로 그려 줘"로 그림 1장이 나오는지.
2. **Claude Code 플러그인 설치 → 검산 1번**: `/plugin install openstem --marketplace kimi94279-glitch/openstem-mcp` 뒤 검산 1번(예: "(x+1)^2 = x^2+2x+1 맞아?").
3. **시험지 PDF 1개**: 시험지(정답·해설 새 쪽) PDF가 만들어지고 열리는지.
4. **본인 학교 LMS**: `lms` 툴의 `courses`와 `deadlines`가 되는지(안 되면 에러 글을 토큰 없이 적는다).
5. **CI 초록불**: 릴리스할 커밋의 CI(3개 OS 모두)가 통과했는지 GitHub Actions에서 확인.

릴리스 노트에 붙일 형식:

```markdown
### 릴리스 전 점검
- 환경: Windows 11 새 사용자 계정 / Claude Desktop x.y.z / Claude Code x.y.z
- (1) .mcpb 설치 → 회로도: 통과
- (2) 플러그인 → 검산: 통과
- (3) 시험지 PDF: 통과
- (4) LMS(○○대) courses·deadlines: 통과 / 실패(이유)
- (5) CI: 통과 (Actions 실행 링크)
```

버전을 올리는 PR은 `.github/PULL_REQUEST_TEMPLATE.md`의 "릴리스 PR이면" 체크리스트에 같은 항목이 있다.

## 버전 올리기

버전의 원본은 `src/openstem/__init__.py`의 `__version__` 하나다. 다음을 함께 고친다(`tests/test_plugin.py`가 확인한다).

- `src/openstem/__init__.py`
- `claude-plugin/.claude-plugin/plugin.json`의 `version`
- `claude-plugin/.mcp.json`의 `openstem-mcp==<버전>`(디렉터리는 고정 버전 launcher만 받는다)
- `server.json`의 `version`·`packages`
- `CHANGELOG.md`에 새 버전 항목

## 원클릭 설치 묶음(.mcpb)

- `scripts/build_mcpb.py` → `dist/openstem-<버전>.mcpb`.
- `packaging/mcpb/manifest.template.json`에 버전(`__init__.py`)·저자·라이선스·주소·requires-python(pyproject)을 채우고, 저장소와 같은 배치(pyproject·uv.lock·README·LICENSE·src/openstem·templates + launch.py·icon.png)로 모아 공식 CLI(`npx -y @anthropic-ai/mcpb validate`·`pack`)로 묶는다. `--zip`이면 공식 CLI 없이 zipfile로 같은 배치를 만든다.
- 묶음 루트에 저장소의 `.python-version`(3.12)을 넣는다. 호스트의 uv가 Python을 고르게 두면 rdkit·pymupdf wheel이 아직 없는 새 Python을 골라 설치가 실패한 사례가 있었다. 올릴 때는 `uv.lock`에 그 버전용 rdkit·pymupdf wheel이 있는지 본다(`tests/test_mcpb.py`가 확인한다). 저장소에서 `uv run`도 이 버전을 쓰고, CI는 `UV_PYTHON`으로 덮어써 3.11·3.14를 돈다.
- manifest 0.4의 `server.type: "uv"`(의존성은 pyproject로 호스트가 설치하고, 묶음에는 소스만 넣는다). 명령은 `uv run --directory ${__dirname} --frozen --no-dev --extra latex launch.py`(묶은 uv.lock 그대로, dev 그룹 제외, LaTeX 수식 입력 켬). `--frozen`이라 빌드 전에 `uv lock --check`로 lock이 최신인지 막는다.
- uv가 확장 폴더에 `.venv`를 만들고 프로젝트를 편집 설치하므로 템플릿은 루트 `templates/`에서 찾는다.
- user_config: 결과물 폴더(기본 `${DOCUMENTS}/OpenSTEM`), 자료 폴더, 학교 이름, LMS 토큰·캘린더 주소(둘 다 sensitive), LMS 주소 직접 지정. 각각 env `OPENSTEM_OUTPUT_DIR`·`INPUT_DIR`·`LMS_SCHOOL`·`LMS_TOKEN`·`LMS_CALENDAR_URL`·`LMS_URL`로 간다. 비워 둔 설정은 빈 값이나 `${user_config.x}` 글자 그대로 올 수 있어 `output.env_value()`가 둘 다 "설정 안 함"으로 본다.
- 아이콘은 `build_mcpb.py --icon`이 matplotlib으로 그린다(512×512).
- 툴을 추가하면 manifest 템플릿 `tools`에도 넣는다(`tests/test_mcpb.py`가 확인).
- 묶음을 새 폴더에 풀어 manifest 명령 그대로 띄우는 시험은 `OPENSTEM_SLOW_TESTS=1`일 때 돈다.
- 첫 실행 때 서버가 tectonic과 TeX 번들을 백그라운드로 준비한다.

## tectonic 자동 준비

배경은 [결정 0005](decisions/0005-tectonic-auto-install.md). 코드는 `render/tectonic_setup.py`.

- 찾는 순서: `OPENSTEM_TECTONIC` → PATH → 캐시 폴더 `bin/`(`latex.tectonic_bin_dir()`).
- 없으면 tectonic 0.17.0 공식 릴리스를 OS·CPU에 맞게 받는다: Windows x86_64 msvc zip, macOS arm64·x86_64, Linux x86_64·aarch64 musl tar.gz. 버전·파일 이름·크기·SHA-256을 코드에 고정했다. 해시는 GitHub 릴리스 API의 digest다(버전을 올릴 때 `gh api …/releases/tags/tectonic@<버전>`으로 다시 받는다).
- 캐시 폴더 안 임시 파일로 받는다: 60 MB 상한, https 리디렉션만, 전체 5분.
- 해시가 맞아야 압축을 연다. 절대 경로·드라이브 문자·`..` 이름이 하나라도 있으면 거절하고, 실행 파일 하나(일반 파일)만 꺼내 `--version`으로 실행되는지 본 뒤 원자적으로 이름을 바꾼다.
- 스레드 잠금 + 파일 잠금(msvcrt/fcntl)으로 서버 여러 개·`--warmup`이 동시에 받지 않는다.
- 잠금을 잡은 뒤 1시간 넘은 설치 찌꺼기(받다 끊긴 `.download-*.part`, 풀다 끊긴 `.tectonic-*`)를 지운다(`remove_leftovers`). 받기·풀기가 실패하거나 Ctrl+C로 끊겨도 그 자리에서 임시 파일을 지운다.
- 설치 상태는 `openstem-mcp --doctor`로 확인한다(tectonic 위치·`--version`, TeX 준비 표시 파일, 번들 서버 연결 등).
- `OPENSTEM_NO_DOWNLOAD=1`이거나 릴리스가 없는 OS·CPU(Windows ARM 등)면 받지 않고 설치 안내를 돌려준다.
- 서버 시작 때 `BACKGROUND_WARMUP`이 tectonic → TeX 번들 순서로 준비한다(그동안 툴은 "준비 중"). `--warmup`도 먼저 받는다. 자동 설치가 실패하면(네트워크가 늦게 붙는 경우 등) 30초 뒤 렌더링 요청이 올 때 다시 건다.
- 진행 글은 stderr에 UTF-8로 쓴다(한국어 Windows의 파이프는 cp949라 호스트 로그가 깨졌다).
- Windows msvc 빌드는 CRT 정적 링크라 VC 런타임이 필요 없다(가져오는 DLL을 확인했다).
- 실제 다운로드 시험은 `OPENSTEM_NETWORK_TESTS=1`일 때 돈다.
- Linux용 0.17.0(musl 정적 빌드)에는 ICU 줄바꿈 데이터가 없어 `\XeTeXlinebreaklocale "ko"`(xetexko가 켠다)가 있으면 XeTeX가 "failed to create linebreak iterator"로 죽는다. 그래서 한글 머리말(`tools/tikz.py`의 `HANGUL_SETUP`)이 문서 시작 때 locale을 끈다. CI(ubuntu)가 시험지 배치 warmup에서 처음 잡았다.

## HTTP 모드(ChatGPT 등)

`openstem-mcp --http [--port 8765]`는 127.0.0.1에서 streamable HTTP로 연다. 주소에 추측할 수 없는 비밀 키가 들어가고(설정 폴더에 저장, `--new-secret`으로 교체) 키가 틀리면 404다. `--tunnel`은 설치된 `cloudflared`로 임시 HTTPS 주소를 열어 ChatGPT에 붙일 전체 URL을 출력한다(cloudflared는 내려받지 않고 설치 방법만 안내한다). 구조는 [ARCHITECTURE.md](ARCHITECTURE.md#http-모드).

## 아직 확인하지 못한 것

- 실제 Claude Desktop에서 `.mcpb` 더블클릭 설치(호스트가 uv를 주는지, 첫 설치 약 200 MB가 서버 시작 시간 제한 안에 끝나는지)
- Linux aarch64에서 자동 설치한 tectonic 실행(Linux x86_64·Windows x86_64·macOS arm64는 2026-11-03 CI에서 자동 설치 → TeX 번들 warmup → 렌더링 시험까지 확인했다)
- 실제 ChatGPT·cloudflared 연결
