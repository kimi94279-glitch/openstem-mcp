# 함께 쓸 도구

openstem은 그림·수식 검산·단위·한국 자료(HWP·LMS) 읽기와 시험지·치트시트 같은 공부 결과물을 맡는다. 아래 일은 하지 않는다. 필요하면 다른 MCP 서버나 스킬을 함께 붙인다.

- 논문 검색, DOI·서지 정보 확인
- 이미 있는 PPT·XLSX·DOCX 파일 고치기, 디자인을 공들인 발표 자료
- HWP·HWPX 파일 **쓰기**(openstem은 읽기만 한다)
- 웹 페이지·음성 같은 자료 폴더 밖의 내용 읽기

이 목록은 추천이나 보증이 아니다. 각 도구는 만든 곳의 라이선스·약관·개인정보 정책을 따르고, openstem과 상관없이 바뀔 수 있다. 붙이기 전에 저장소와 약관을 직접 확인한다.

## 목록

확인 날짜는 2026-11-03(감사 조사)이고, 2026-11-04에 저장소 페이지와 라이선스 파일을 다시 열어 확인했다. 열지 못해 검색 결과로만 본 것은 "미확인"으로 적었다.

| 이름 | 하는 일 | 라이선스 | 무료/유료 | 설치 형태 | 확인 날짜 |
|---|---|---|---|---|---|
| [arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) | arXiv 논문 검색·내려받기, 논문 LaTeX 원문을 절 단위로 읽기, BibTeX 내보내기 | Apache-2.0 | 무료 | 로컬 MCP, `uvx arxiv-mcp-server`(같은 이름의 무관한 npm 패키지가 있어 npx는 쓰지 말라고 README가 경고) | 2026-11-03 |
| [paper-search-mcp](https://github.com/openags/paper-search-mcp) | arXiv·PubMed·Semantic Scholar·Crossref·OpenAlex 등 20여 곳에서 논문 검색·내려받기, Unpaywall DOI 조회 | MIT | 무료(IEEE 등 일부는 키 필요) | 로컬 MCP, `uvx paper-search-mcp` 또는 `pip install paper-search-mcp` | 2026-11-03 |
| [kordoc](https://github.com/chrisryugj/kordoc) | HWP·HWPX·PDF·오피스 파일 읽기, 마크다운 → HWPX 만들기, HWP → HWPX 변환, 양식 채우기(바이너리 HWP로는 쓰지 않음) | MIT | 무료 | Node 20+ 라이브러리·CLI·로컬 MCP, `npx -y kordoc setup` | 2026-11-03 |
| [Wolfram MCP](https://www.wolfram.com/artificial-intelligence/mcp/) | Wolfram\|Alpha 질의, Wolfram Language 계산 | 독점(Wolfram 서비스 약관) | 개인용 제한 무료라고 안내됨, 한도·키 필요 여부는 미확인 | 호스팅 원격 MCP(`https://agenttools.wolfram.com/mcp`, 미확인), 또는 Wolfram Engine이 있는 PC의 로컬 MCP | 2026-11-03(공식 페이지 미확인, 검색 결과로만) |
| [Anthropic 문서 스킬](https://github.com/anthropics/skills) docx·pptx·xlsx·pdf | 워드·파워포인트·엑셀·PDF 파일 만들기·고치기 | 소스 공개지만 오픈소스 아님(`skills/docx/LICENSE.txt`: Anthropic과의 약관을 따르고 복제·2차 저작·배포 금지). pptx·xlsx·pdf 폴더의 라이선스 문구가 같은지는 미확인 | Claude 이용 약관 안에서 | Claude 스킬(Claude 앱·Claude Code) | 2026-11-03 |
| [markitdown-mcp](https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp) | 여러 형식의 파일·주소를 마크다운으로 바꿔 읽기 | MIT(저장소 기준) | 무료 | 로컬 MCP, `pip install markitdown-mcp` 뒤 `markitdown-mcp`(HTTP 모드는 인증이 없어 이 PC 안에서만 쓴다) | 2026-11-03 |

## openstem과 겹치는 부분, 역할 나누기

- **arxiv-mcp-server**: 겹치지 않는다. 논문을 찾고 받는 일은 이쪽, 받은 PDF를 자료 폴더에 두고 요약·예상문제·치트시트로 만드는 일은 openstem(`read_document`, `export_document`).
- **paper-search-mcp**: 겹치지 않는다. 보고서의 참고문헌 DOI·서지 정보 확인은 이쪽, 보고서 본문의 그림·검산·내보내기는 openstem.
- **kordoc**: HWP·HWPX **읽기**가 겹친다(openstem `read_document`는 자료 폴더 안 파일만 읽는다). 학교에 HWPX로 내야 하거나 양식을 채워야 하면 kordoc, 그림·수식·검산은 openstem에서 만들어 넣는다.
- **Wolfram MCP**: 계산이 `verify_math`·`check_units`와 겹친다. openstem은 내 PC에서 sympy로 계산하고 아무것도 보내지 않는다. sympy가 못 푸는 식이나 물리 상수·자료 조회가 필요할 때 Wolfram을 쓰되, 입력이 Wolfram 서버로 간다는 점을 안다.
- **Anthropic 문서 스킬**: 파일 만들기가 `export_document`와 겹친다. 이미 있는 파일을 고치거나 레이아웃·디자인이 중요하면 이 스킬, 그림·수식·검산한 숫자는 openstem에서 만들어 넣는다([0010](decisions/0010-plugin-skills-as-guidance.md), `slides-and-documents` 스킬).
- **markitdown-mcp**: PDF·워드·파워포인트·엑셀 읽기가 `read_document`와 겹친다. 웹 페이지처럼 자료 폴더 밖의 내용은 markitdown, 자료 폴더의 강의 자료(특히 HWP, 스캔 PDF의 쪽 그림)는 openstem.

더 찾아볼 것(미확인): 인용 목록을 Crossref·OpenAlex와 대조하는 MCP로 mcp-refchecker(MIT, PyPI)가 검색되지만 저장소와 관리 상태는 열어 보지 못했다.

## 다른 호스트에서 openstem 스킬 쓰기

`claude-plugin/skills/`의 6개 스킬은 [Agent Skills 표준](https://agentskills.io/specification)(폴더 하나에 `SKILL.md`)을 따른다. Claude가 아닌 호스트도 이 폴더를 복사해 넣으면 읽는다. 다만 스킬은 사용 지침일 뿐이라 **openstem MCP 서버(`uvx openstem-mcp`)를 그 호스트에 따로 연결해야** 툴이 돈다. 서버가 없으면 스킬이 그렇게 말하고 설치 안내를 한다.

```bash
git clone https://github.com/kimi94279-glitch/openstem-mcp
cp -r openstem-mcp/claude-plugin/skills/* ~/.agents/skills/   # 아래 표의 폴더 중 호스트에 맞는 곳
```

| 호스트 | 스킬 폴더 | 공식 문서 | 확인 |
|---|---|---|---|
| Gemini CLI | 사용자 `~/.gemini/skills/` 또는 `~/.agents/skills/`, 작업 폴더 `.gemini/skills/` 또는 `.agents/skills/`(같은 단계에서는 `.agents`가 우선). `gemini skills install <git 주소나 폴더>`로도 넣는다 | [gemini-cli 저장소 docs/cli/skills.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/skills.md), [geminicli.com](https://geminicli.com/docs/cli/skills/) | 저장소 문서로 확인, 공식 사이트는 접속 못 함 |
| Codex CLI | 저장소 `.agents/skills/`, 사용자 `~/.agents/skills/` | [developers.openai.com/codex/skills](https://developers.openai.com/codex/skills) | 미확인(검색 결과로만) |
| ChatGPT | 스킬 → 만들기 → 컴퓨터에서 올리기(Business·Enterprise·Edu 등 워크스페이스) | [Skills in ChatGPT](https://help.openai.com/en/articles/20001066-skills-in-chatgpt) | 미확인(검색 결과로만). 서버 연결은 README의 "ChatGPT에서 쓰기" |
| VS Code(GitHub Copilot) | 작업 폴더 `.github/skills/`·`.claude/skills/`·`.agents/skills/`, 사용자 `~/.copilot/skills/`·`~/.claude/skills/`·`~/.agents/skills/` | [Agent Skills in VS Code](https://code.visualstudio.com/docs/agent-customization/agent-skills) | 미확인(검색 결과로만) |
| Cursor | 작업 폴더 `.cursor/skills/`(또는 `.agents/skills/`), 사용자 `~/.cursor/skills/` | [cursor.com/docs/skills](https://cursor.com/docs/skills) | 미확인(검색 결과로만) |

- 스킬 본문은 openstem 툴 이름(`verify_math` 등)을 그대로 쓴다. 호스트가 MCP 툴 이름에 서버 이름을 붙여도(예: `openstem__verify_math`) 보통 알아듣지만, 호스트마다 시험해 보지는 않았다.
- 스킬의 `description`에 한국어 요청 표현이 들어 있다. 호스트가 description 길이를 제한하면 잘릴 수 있다.
