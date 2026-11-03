# 0003. 외부 입력은 작업자 프로세스에서 시간 제한을 두고 처리한다

- 상태: 채택
- 시점: M1(외부 명령), M2(파이썬 작업자), 이후 보강(예비 작업자·준비 신호·변환 작업자)

## 배경

- sympy의 `parse_expr`는 안에서 eval을 쓴다.
- RDKit·pymupdf는 C/C++ 라이브러리라 망가진 입력에서 오래 걸리거나 죽을 수 있다.
- 아주 촘촘한 패턴 그림은 pymupdf 렌더링에 수십 초가 걸리고, 그동안 GIL을 쥐어 서버 전체가 멈췄다(점검에서 재현).
- sympy import만 프로세스마다 약 5초, 처음 설치한 환경은 .pyc·글꼴 캐시 때문에 수십 초가 걸린다.

## 결정

- 외부 실행 파일(tectonic, pandoc)은 `sandbox.run()`으로, 파이썬 처리는 `sandbox.run_python()`으로 `python -I -m openstem.workers.<이름>` 별도 프로세스에서 한다. 서버는 sympy·RDKit·pymupdf를 import하지 않는다.
- 요청 하나에 프로세스 하나. 기본 시간 제한은 20초(`OPENSTEM_TIMEOUT`).
- 작업자는 import를 마치면 `OPENSTEM-READY`를 쓰고, 시간 제한은 그 뒤부터 센다(import는 `IMPORT_TIMEOUT` 180초 따로).
- 모듈마다 import를 끝낸 예비 작업자를 하나씩 띄워 둔다.
- 수식 문자열은 eval 전에 `mathexpr.check()`가 허용한 토큰만 통과시킨다.

## 결과

- 무한 루프·폭주 입력이 서버를 멈추지 못한다. 첫 호출도 import 시간 때문에 시간 초과가 나지 않는다.
- 호출마다 3~5초(예비 작업자가 있으면 계산 시간만).
- **한계**: 이것은 프로세스 분리와 시간 제한이다. 작업자는 서버와 같은 사용자 권한으로 돌고, 파일 시스템·네트워크 접근이나 메모리 사용을 운영체제 수준에서 막지 않는다. 그래서 입력 검사(`mathexpr`, `texcheck`)가 1차 방어선이다. 자세한 범위는 [SECURITY.md](../../SECURITY.md).
