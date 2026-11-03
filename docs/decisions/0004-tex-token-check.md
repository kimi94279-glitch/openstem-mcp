# 0004. TeX 코드는 컴파일 전에 토큰 단위로 검사한다

- 상태: 채택(2026-11 점검 뒤 단어 차단 방식에서 바꿈)
- 시점: M1, 2026-11 보강

## 배경

tectonic `--untrusted`는 shell-escape만 끈다. TeX 코드는 여전히 절대 경로·상위 폴더의 파일을 읽고 쓸 수 있다. 처음에는 위험한 명령 이름을 단어로 막았는데, 2026-11 점검에서 `\csname`·`^^` 표기로 이름을 조립해 차단 목록을 우회하고 임의 파일을 덮어쓰고 읽는 것을 실제로 재현했다.

## 결정

`texcheck.py`가 컴파일 전에 코드를 TeX처럼 토큰으로 나눠 두 가지를 막는다.

1. **이름을 조립하는 수단**: `\csname`, `^^` 표기, `\scantokens`, catcode 바꾸기(`\makeatletter`, `\ExplSyntaxOn` 등), 허용 목록 밖의 `\begin{이름}`, `\UseName`·`\ExpandArgs`.
2. **파일·외부에 닿는 명령**: `\input`·`\openout`·`\write`·`\special`·`\font`·`\XeTeX*`·`\pgfimage`·`\includegraphics` 등, 그리고 pgfplots·TikZ의 파일 읽기 문법(`table|file|graphics {`).

1이 막혀 있으면 2의 명령은 이름을 그대로 써야만 쓸 수 있으므로 토큰 검사로 잡힌다. 명령 이름은 ASCII 글자만 이어 읽어 XeTeX보다 짧게 읽는다(덜 막지는 않고 더 막을 수만 있다). pgfplots `table {데이터}`는 파일 이름과 구분할 수 없어 막고 `coordinates`를 안내한다. TikZ 라이브러리 `external`은 금지한다. 작업은 매번 빈 임시 폴더에서 한다. export_document의 수식에도 같은 검사를 쓴다.

## 결과

- 점검에서 뚫렸던 공격은 `tests/test_texcheck.py`에 회귀 시험으로 남아 있다.
- **남은 한계**: 이 검사는 알려진 위험 문법을 막는 방식이라, 패키지 매크로가 파일을 여는 길을 빠짐없이 막았다고 보장하지 못한다. 운영체제 수준의 격리(파일 접근 제한, 쓰기 금지 토큰 등)는 아직 없다. 새 패키지·문법을 허용할 때는 그 패키지가 파일을 읽거나 쓰는 길이 있는지 먼저 본다.
