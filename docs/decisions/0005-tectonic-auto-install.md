# 0005. tectonic을 자동으로 설치한다 (M4 결정을 뒤집음)

- 상태: 채택. M4의 "자동으로 내려받지 않는다"를 대체한다
- 시점: M6(0.3.0)

## 배경

M4에서는 공급망 위험 때문에 tectonic을 자동으로 내려받지 않고, 없으면 설치 방법만 안내했다. 하지만 Claude Desktop 원클릭 설치(.mcpb)를 쓰는 학생에게 "터미널에서 tectonic을 설치하고 PATH에 넣어라"는 설치 실패와 같다.

## 결정

- tectonic 0.17.0 공식 GitHub 릴리스를 OS·CPU에 맞게 받는다. 버전·파일 이름·크기·SHA-256을 코드에 고정해서, 받는 파일이 바뀌면 해시가 달라 거절된다.
- 해시가 맞아야 압축을 열고, 실행 파일 하나만 꺼내 `--version`으로 실행되는지 본 뒤 원자적으로 캐시 폴더 `bin/`에 둔다.
- `OPENSTEM_TECTONIC`이나 PATH에 있으면 그것을 쓰고, `OPENSTEM_NO_DOWNLOAD=1`이면 받지 않는다.
- 세부 절차는 [packaging.md](../packaging.md#tectonic-자동-준비).

## 결과

- 설치 단계가 하나 줄었다. 처음 켤 때 tectonic(약 10~21 MB)과 TeX 번들(약 300 MB)을 받는다.
- 위험이 남는 곳: TeX 패키지 자체는 tectonic이 자기 번들 서버(fullyjustified.net)에서 받으며, 이 부분은 이 프로젝트가 해시를 고정하지 않는다. 번들 서버에 닿지 못하는 네트워크에서는 그림·PDF 기능이 동작하지 않는다.
- Linux x86_64는 2026-11-03에 자동 설치·해시 확인·실행을 확인했다. macOS와 Linux aarch64 바이너리는 아직 실제로 돌려 보지 못했다.
