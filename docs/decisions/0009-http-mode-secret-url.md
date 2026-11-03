# 0009. HTTP 모드는 주소 속 비밀 키 + stateless JSON 응답으로 연다

- 상태: 채택
- 시점: M6(0.3.0)

## 배경

ChatGPT는 인터넷의 HTTPS 주소로만 MCP 서버에 붙는다. 학생 PC의 서버를 그렇게 열려면 터널이 필요하고, 그 주소는 누구나 부를 수 있다. 터널은 SSE 같은 긴 연결을 잘 넘기지 못하고, 터널 Host 이름은 미리 알 수 없다.

## 결정

- 기본은 127.0.0.1에만 연다. 주소에 추측할 수 없는 비밀 키(`secrets.token_urlsafe(32)`)를 넣고(`/<키>/mcp`), 헤더 방식(`/mcp` + `Authorization: Bearer <키>`)도 받는다. 키가 틀린 요청은 모두 같은 404다.
- stateless·JSON 응답으로 연다. 세션 만료가 없고 SSE를 못 넘기는 터널도 괜찮다. GET은 405.
- SDK의 Host 검사는 끈다. 키 없는 요청은 SDK에 닿기 전에 404라 Host 검사가 더 막는 것이 없다.
- 접근 로그를 끄고 로그에서 키를 가린다. 키는 시작 줄에 한 번만 보인다.
- `--tunnel`은 사용자가 설치한 cloudflared만 쓰고 내려받지 않는다.
- 세부 구조는 [ARCHITECTURE.md](../ARCHITECTURE.md#http-모드).

## 결과

- OAuth 없이 붙을 수 있다(ChatGPT에서는 인증 "없음"으로 등록).
- 키가 새면 `--new-secret`으로 바꿔야 한다. 임시 터널 주소는 켤 때마다 바뀐다.
- 실제 ChatGPT·cloudflared로는 아직 확인하지 못했다.
