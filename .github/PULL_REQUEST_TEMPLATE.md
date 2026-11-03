## 무엇을 바꿨나

<!-- 한두 문장. 관련 이슈가 있으면 #번호 -->

## 확인

- [ ] `uv run pytest` 통과(결과의 passed/failed/skipped 수: )
- [ ] 렌더링(TikZ·템플릿·내보내기)을 건드렸다면 tectonic이 있는 환경 또는 `OPENSTEM_REQUIRE_TEX=1`로 돌렸다(CI는 이 값으로 돈다)
- [ ] 툴을 바꿨다면 manifest 템플릿 `tools`, `docs/tools.md`, 관련 프롬프트·플러그인 스킬을 함께 봤다
- [ ] 설계 결정을 바꿨다면 `docs/decisions/`에 기록을 더했다
- [ ] 토큰·캘린더 주소·개인 정보가 코드·시험·로그에 없다

## 릴리스 PR이면

버전을 올리는 PR은 [docs/packaging.md의 "릴리스 전 점검(10분)"](https://github.com/kimi94279-glitch/openstem-mcp/blob/main/docs/packaging.md#릴리스-전-점검10분)을 새 PC나 새 사용자 계정에서 하고 결과를 적는다. 같은 결과를 릴리스 노트에도 붙인다.

- [ ] (1) .mcpb 설치 → 회로도 1장
- [ ] (2) Claude Code 플러그인 설치 → 검산 1번
- [ ] (3) 시험지 PDF 1개
- [ ] (4) 본인 학교 LMS `courses`·`deadlines`
- [ ] (5) 이 커밋의 CI 초록불
