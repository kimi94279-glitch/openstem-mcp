# 0004. TeX 코드는 컴파일 전에 토큰 단위로 검사한다

- 상태: 채택(2026-11 점검 뒤 단어 차단 방식에서 바꿈). 방어선 구성과 남은 한계는 [0012](0012-tex-defense-in-depth.md)가 갱신한다
- 시점: M1, 2026-11 보강

## 배경

tectonic `--untrusted`는 shell-escape만 끈다. TeX 코드는 여전히 절대 경로·상위 폴더의 파일을 읽고 쓸 수 있다. 처음에는 위험한 명령 이름을 단어로 막았는데, 2026-11 점검에서 `\csname`·`^^` 표기로 이름을 조립해 차단 목록을 우회하고 임의 파일을 덮어쓰고 읽는 것을 실제로 재현했다.

## 결정

`texcheck.py`가 컴파일 전에 코드를 TeX처럼 토큰으로 나눠 두 가지를 막는다.

1. **이름을 조립하는 수단**: `\csname`, `^^` 표기, `\scantokens`, catcode 바꾸기(`\makeatletter`, `\ExplSyntaxOn` 등), 허용 목록 밖의 `\begin{이름}`, `\UseName`·`\ExpandArgs`.
2. **파일·외부에 닿는 명령**: `\input`·`\openout`·`\write`·`\special`·`\font`·`\XeTeX*`·`\pgfimage`·`\includegraphics` 등, 그리고 pgfplots·TikZ의 파일 읽기 문법(`table|file|graphics {`).

3. **그림 데이터 소스**(2026-11-03 점검 뒤): `\addplot`·`\addplot3`(`+`, `+[...]`)와 TikZ `plot` 뒤, 중괄호가 중첩된 `[...]` 옵션을 짝 맞춰 건너뛴 자리의 데이터 소스는 `coordinates`, 식(`{...}`/`(...)`), pgfplots `expression`·`fill between`만 허용한다. `table`·`file`·`function`(gnuplot 호출)·`gnuplot`·`graphics`·`shell`과 매크로 인자는 거절한다. 원래 정규식(`table|file|graphics {`)은 옵션 안의 `]`나 중괄호 없는 매크로 인자로 우회되어, 임의 파일을 읽고(내용이 PGF 에러에 실려 돌아옴) gnuplot 파일을 썼다. 정규식은 추가 방어선으로 남겼다.
4. **TeX 쪽 방어선**: TikZ 경로 해석기는 매크로를 펼쳐 읽어서 `\def\a{p}\draw \a lot file …`처럼 낱말을 나눠 쓰면 토큰 검사로 다 잡을 수 없다(점검에서 재현). 그래서 render_tikz 문서는 `\begin{document}` 바로 앞에 `tikz.FILE_GUARD`를 넣어 pgf·pgfplots가 실제로 파일을 여는 내부 명령(`\pgfplotxyfile`, `\pgfplotgnuplot`, `\pgfplots@addplotimpl@table` 등)을 에러로 바꾼다. 사용자 코드는 `@` 이름을 쓸 수 없어 되돌리지 못한다.

1이 막혀 있으면 2의 명령은 이름을 그대로 써야만 쓸 수 있으므로 토큰 검사로 잡힌다. 명령 이름은 ASCII 글자만 이어 읽어 XeTeX보다 짧게 읽는다(덜 막지는 않고 더 막을 수만 있다). pgfplots `table {데이터}`는 파일 이름과 구분할 수 없어 막고 `coordinates`를 안내한다. TikZ 라이브러리 `external`은 금지한다. 작업은 매번 빈 임시 폴더에서 한다. export_document의 수식에도 같은 검사를 쓴다.

## 결과

- 점검에서 뚫렸던 공격은 `tests/test_texcheck.py`에 회귀 시험으로 남아 있다.
- export_document의 flashcards 머리말도 tikz를 불러오므로 수식 칸에서 같은 공격이 됐다. 지금은 토큰 검사로 막는다. 0.3.3에서 그 머리말에도 `FILE_GUARD`를 넣었다([0012](0012-tex-defense-in-depth.md)).
- **남은 한계**([0012](0012-tex-defense-in-depth.md)에서 갱신): 이 검사는 알려진 위험 문법을 막는 방식이라, 패키지 매크로가 파일을 여는 길을 빠짐없이 막았다고 보장하지 못한다. 운영체제 수준의 격리(파일 접근 제한, 쓰기 금지 토큰 등)는 아직 없다. 새 패키지·문법을 허용할 때는 그 패키지가 파일을 읽거나 쓰는 길이 있는지 먼저 본다.
