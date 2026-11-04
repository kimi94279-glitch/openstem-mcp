# 0012. TeX 방어선을 이중으로 둔다: 토큰 검사 + TeX 쪽 파일 명령 끄기 + 자식 프로세스 제한

- 상태: 채택([0004](0004-tex-token-check.md)의 "남은 한계"를 갱신)
- 시점: 0.3.3(2026-11-03 감사 후속)

## 배경

[0004](0004-tex-token-check.md)는 TeX 코드를 컴파일 전에 토큰으로 나눠 위험한 명령과 이름 조립 수단을 막는다. 2026-11-03 감사에서 이 한 겹만으로는 부족하다는 것이 재현됐다.

- pgfplots·TikZ의 파일 읽기 문법은 정규식(`table|file|graphics {`)으로 막았는데, 옵션 안의 `]`나 중괄호 없는 매크로 인자로 우회되어 임의 파일 내용이 PGF 에러에 실려 돌아왔다.
- TikZ 경로 해석기는 매크로를 펼쳐 읽는다. 그래서 **낱말을 매크로로 나눠 쓰는 우회**는 토큰 검사가 원리적으로 다 잡을 수 없다.
- export_document의 암기 카드 배치도 tikz를 불러와 수식 칸에서 같은 길이 열렸고, 0004 당시에는 그 머리말에 TeX 쪽 방어선이 없었다.
- 작업자·tectonic·pandoc이 서버 환경변수를 그대로 물려받아, TeX나 수식 처리에서 파일을 읽는 길이 하나라도 남으면 LMS 토큰 같은 비밀 값까지 닿을 수 있었다.
- 큰 입력 하나로 작업자가 메모리를 다 써서 PC를 느리게 만들 수 있었다.

## 결정

방어선 하나가 뚫려도 다음 방어선이 막도록 네 겹을 둔다.

1. **토큰 위치 검사**(`texcheck.py`): `\addplot`·`\addplot3`·TikZ `plot` 뒤, 중괄호가 중첩된 `[...]` 옵션을 짝 맞춰 건너뛴 자리의 데이터 소스를 `coordinates`, 식, pgfplots `expression`·`fill between`만 허용한다. `table`·`file`·`function`·`gnuplot`·`graphics`·`shell`과 매크로 인자는 거절한다. 정규식은 추가 방어선으로 남긴다. 나머지 규칙은 0004와 같다.
2. **TeX 쪽 파일 명령 끄기**(`tools/tikz.py`의 `FILE_GUARD`): 패키지를 다 불러온 뒤 `\begin{document}` 바로 앞에서, pgf·pgfplots가 실제로 파일을 열거나 gnuplot을 부르는 내부 명령(`\pgfplotxyfile`, `\pgfplotgnuplot`, `\pgfplots@addplotimpl@table` 등)을 에러를 내는 명령으로 바꾼다. 사용자 코드는 catcode를 바꿀 수 없어 `@` 이름을 다시 정의하지 못한다. render_tikz 문서와 export_document 머리말(`tools/export.py`의 `to_latex`, 모든 배치) 두 곳에 넣는다.
3. **비밀 환경변수 제거**(`sandbox.child_env`): 작업자·tectonic·pandoc에는 `OPENSTEM_LMS_*`와 이름에 `TOKEN`·`SECRET`·`PASSWORD`·`API_KEY`·`ACCESS_KEY`·`CREDENTIAL`·`PRIVATE_KEY`가 든 변수를 빼고 넘긴다. 프록시·인증서·캐시 폴더 변수는 tectonic이 TeX 번들을 받아야 해서 남긴다.
4. **메모리 상한**(`workers.limit_memory`): POSIX에서 작업자 프로세스에 주소 공간 상한(`RLIMIT_AS`, 기본 3 GB, `OPENSTEM_WORKER_MEMORY_MB`, 0이면 끔)을 둔다.

허용 패키지·문법을 늘릴 때는 0004대로 그것이 파일을 읽거나 쓰는 길이 있는지 먼저 보고, 있으면 1과 2 양쪽에 막는 규칙을 더한다.

## 결과

- 감사에서 재현한 우회는 `tests/test_texcheck.py`·`tests/test_tikz_input.py`·`tests/test_export.py`에 회귀 시험으로 남겼다(공격 문자열은 시험 코드에만 둔다).
- 0004의 "export 머리말에는 아직 `FILE_GUARD`가 없다"는 한계는 없어졌다.
- 대가: pgfplots `table {데이터}`처럼 정당한 문법도 막혀 `coordinates`로 바꿔 써야 한다. `FILE_GUARD`가 막는 내부 명령 이름은 pgf·pgfplots 버전에 묶여 있어, tectonic 번들이 바뀌면 다시 확인해야 한다.
- **남은 한계**:
  - 두 방어선 모두 알려진 길을 막는 방식이다. 패키지 매크로가 파일을 여는 길을 빠짐없이 막았다고 보장하지 못한다.
  - 운영체제 수준의 파일 접근 격리(읽기 제한, 쓰기 금지 토큰, 컨테이너)는 아직 없다. 작업자는 사용자 권한으로 돈다.
  - Windows는 이후 Job Object로 같은 상한을 건다(0.3.3 다음, `sandbox.py`). macOS는 `RLIMIT_AS`를 잘 지키지 않아 사실상 상한이 없고 시간 제한만 있다.
  - 이름 규칙에 안 걸리는 비밀 환경변수는 자식 프로세스로 넘어간다.
- 보안 범위 전체는 [SECURITY.md](../../SECURITY.md).
