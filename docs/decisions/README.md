# 결정 기록

설계 원칙을 정하거나 바꾼 결정을 한 파일에 하나씩 남긴다. 형식: 상태, 시점, 배경, 결정, 결과(대가와 남은 한계 포함). 이미 내린 결정을 뒤집을 때는 원래 파일을 지우지 않고 새 파일을 만들어 무엇을 대체하는지 적는다([0005](0005-tectonic-auto-install.md)처럼).

| 번호 | 결정 | 상태 |
|---|---|---|
| [0001](0001-no-llm-calls.md) | 서버는 LLM을 부르지 않는다 | 채택 |
| [0002](0002-tool-result-contract.md) | 툴은 MCP를 모르고, LLM이 고칠 수 있는 결과를 돌려준다 | 채택 |
| [0003](0003-worker-processes.md) | 외부 입력은 작업자 프로세스에서 시간 제한을 두고 처리한다 | 채택 |
| [0004](0004-tex-token-check.md) | TeX 코드는 컴파일 전에 토큰 단위로 검사한다 | 채택 |
| [0005](0005-tectonic-auto-install.md) | tectonic을 자동으로 설치한다(M4 결정을 뒤집음) | 채택 |
| [0006](0006-export-via-pandoc-ast.md) | export_document는 pandoc 문서 구조로 검사한다 | 채택 |
| [0007](0007-llm-facing-text-in-english.md) | LLM이 읽는 글은 영어, 사람이 보는 글은 한국어 | 채택 |
| [0008](0008-lms-tokens-not-passwords.md) | LMS는 토큰·캘린더 주소만 받고 읽기만 한다 | 채택 |
| [0009](0009-http-mode-secret-url.md) | HTTP 모드는 주소 속 비밀 키 + stateless JSON | 채택 |
| [0010](0010-plugin-skills-as-guidance.md) | 플러그인 스킬은 사용 지침만 담는다 | 채택 |
