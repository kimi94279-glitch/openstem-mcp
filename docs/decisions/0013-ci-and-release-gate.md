# 0013. 시험을 통과해야 배포하고, 설치 경로는 사람이 확인한다

- 상태: 채택
- 시점: 0.3.3(2026-11-03 감사 후속)

## 배경

0.3.2까지 `publish.yml`은 GitHub 릴리스를 만들면 바로 PyPI 업로드와 `.mcpb` 묶기를 했다. 시험을 돌리는 CI가 없었고, Action은 `@v4` 같은 움직이는 태그로 불렀다. 그 결과 0.3.1·0.3.2는 시험이 실패한 상태로 배포됐다.

시험이 있어도 tectonic이 없는 환경에서는 렌더링 시험이 "건너뛰기"로 끝나 초록불처럼 보였다. 또 시험은 코드만 보고, 사용자가 실제로 설치하는 길(새 PC의 `.mcpb`, 플러그인 설치, 첫 실행 다운로드)은 보지 않는다.

## 결정

- **CI**(`.github/workflows/ci.yml`): push·pull_request마다 ubuntu·windows·macos × Python 3.11·3.14에서 `uv sync --locked --all-extras` → `openstem-mcp --warmup` → `pytest`.
- **TeX 필수 모드**: CI는 `OPENSTEM_REQUIRE_TEX=1`로 돈다. tectonic이 없으면 TeX 시험이 건너뛰지 않고 실패한다(`tests/conftest.py`의 `require_tectonic`). 로컬에서는 이 변수가 없으면 건너뛰기를 허용하되 `skipped` 수를 확인한다.
- **배포 관문**: `publish.yml`은 먼저 `ci.yml`을 다시 불러(`test` 잡) 통과해야 `publish`(PyPI)와 `bundle`(.mcpb)이 돌고, 둘이 끝나야 `registry`(MCP 레지스트리)가 돈다.
- **공급망 고정**: 서드파티 Action은 커밋 SHA로 고정하고 주석에 태그를 적는다. checkout은 `persist-credentials: false`. mcpb CLI는 정확한 버전, `mcp-publisher`는 워크플로에 고정한 sha256과 릴리스 checksums 파일로 확인한다. 워크플로 기본 권한은 `contents: read`. Dependabot이 Action과 `uv.lock` 갱신 PR을 연다.
- **사람이 하는 점검표**: 릴리스마다 새 PC나 새 사용자 계정에서 [릴리스 전 점검(10분)](../packaging.md#릴리스-전-점검10분) 다섯 항목(.mcpb 설치 → 회로도, 플러그인 → 검산, 시험지 PDF, 본인 학교 LMS, CI 초록불)을 하고 결과를 릴리스 노트에 적는다. 릴리스 PR 양식에도 같은 항목이 있다.

## 결과

- 시험이 실패한 커밋은 배포되지 않는다. TeX가 없는 러너에서 렌더링 시험이 조용히 빠지는 일도 없다.
- 대가: 릴리스가 3개 OS 시험 시간만큼 늦어진다. TeX 번들은 `actions/cache`로 다시 써서 줄인다.
- 대가: SHA 고정은 갱신을 사람이 봐야 한다(Dependabot PR 검토).
- **남은 한계**: 점검표는 사람이 해야 하고 CI가 강제하지 못한다. 실제 학교 LMS 연결은 아직 확인한 학교가 없어 점검 4번은 각자 학교로만 확인된다. Windows ARM처럼 CI에 없는 환경은 시험하지 않는다.
- 세부 절차는 [packaging.md](../packaging.md), [CONTRIBUTING.md](../../CONTRIBUTING.md).
