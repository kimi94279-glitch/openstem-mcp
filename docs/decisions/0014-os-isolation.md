# 0014. tectonic과 파이썬 작업자를 운영체제 샌드박스 안에서 돌린다

- 상태: 채택([0003](0003-worker-processes.md)·[0012](0012-tex-defense-in-depth.md)의 "운영체제 수준 격리가 없다"는 한계를 Linux·macOS에서 줄임)
- 시점: 0.4.0(2026-11 감사 후속)

## 배경

- 2026-11 감사에서 TeX 코드로 PC의 파일을 읽는 우회가 재현됐다. 고친 뒤에도 방어선은 모두 "알려진 길을 막는" 방식이다: 입력 검사(`texcheck`·`mathexpr`), TeX 쪽 `FILE_GUARD`, 비밀 환경변수 제거, 메모리 상한.
- tectonic `--untrusted`는 shell-escape만 끈다. TeX의 `\input`·`\openin`은 절대 경로 파일도 읽는다. 검사를 빠져나가는 새 우회가 하나만 나와도, 작업자·tectonic은 사용자 권한으로 돌아 홈 폴더의 모든 파일에 닿는다.
- 필요한 것은 검사가 뚫려도 사용자 파일에 닿지 못하게 하는 **두 번째 벽**이다. 각 호출이 실제로 읽어야 하는 것은 정해져 있다: 시스템 라이브러리, 파이썬과 가상환경, tectonic 실행 파일과 번들 캐시, 작업 임시 폴더, 그 호출의 입력 파일 하나.

## 결정

- **설정** `OPENSTEM_ISOLATION=auto|off|require`(기본 `auto`). auto는 쓸 수 있으면 격리하고, 못 쓰면 지금처럼 돌며 서버 로그와 `openstem-mcp --doctor`에 한 줄을 남긴다. require는 격리를 못 쓰면 툴이 짧은 에러("OS isolation is required …")를 돌려준다. off는 끈다.
- **감지**: 처음 한 번, 실제 작업자와 같은 설정으로 작은 파이썬(`import openstem.workers`, 작업 폴더에 파일 쓰기)을 샌드박스 안에서 띄워 본다. 실행 파일이 있는지만 보지 않는 것은 Ubuntu 24.04의 AppArmor 비특권 사용자 네임스페이스 제한, 컨테이너 안, 이미 샌드박스에 든 앱처럼 "있지만 못 쓰는" 경우가 흔해서다. 실패하면 auto에서는 조용히(경고 한 줄) 격리 없이 돈다.
- **Linux: bubblewrap(`bwrap`)**. `--unshare-all --die-with-parent --new-session --cap-drop ALL`로 빈 루트를 만들고 필요한 것만 붙인다.
  - 읽기 전용: `/usr`, `/bin`·`/lib*`(링크), `/etc`의 일부(동적 링커, 이름 풀이, 인증서, 글꼴 설정, 시간대, `alternatives`, `texmf`, `passwd`·`group`), 인증서 환경변수(`SSL_CERT_FILE` 등)가 가리키는 파일, 작업자면 `python -I`가 보는 인터프리터·표준 라이브러리·site-packages(편집 설치 경로 포함, 홈 폴더 자체나 그 위는 넣지 않는다), tectonic이면 그 실행 파일.
  - 쓰기: 작업 폴더만. tectonic은 번들 캐시(`TECTONIC_CACHE_DIR`, 없으면 tectonic이 쓰는 Rust `directories` 크레이트의 위치: Linux `~/.cache/tectonic`, macOS `~/Library/Caches/TectonicProject.Tectonic`. 예전 tectonic의 `Tectonic` 폴더는 이미 있을 때만)도 쓴다. 홈 폴더에서 보이는 것은 이것뿐이다.
  - `/tmp`는 빈 tmpfs, `/proc`·`/dev`는 새로. 경로 중간의 심볼릭 링크는 링크로 다시 만들고 실제 경로를 붙인다.
  - 네트워크: tectonic은 TeX 번들을 받아야 해서 연다(`--share-net`). 파이썬 작업자는 끈다.
- **macOS: `sandbox-exec` 프로필**. 모든 파일 읽기를 막은 뒤 시스템(`/System`, `/usr`, `/Library`, `/opt/homebrew` 등)·파이썬·캐시·작업 폴더만 연다. 파일 정보(stat)는 어디서나 허용한다(내용은 못 읽는다). 쓰기는 작업 폴더(tectonic은 번들 캐시도)와 `/dev/null` 등만. 작업자는 `(deny network*)`.
- **예비 작업자**: 예비 작업자는 무엇을 처리할지 모르는 채로 미리 뜨므로 바인드할 입력 파일을 미리 알 수 없다. 그래서 작업자마다 전용 폴더(box)를 만들어 그 폴더만 쓰게 하고, 호출이 오면 서버가 입력 파일(payload의 `src`·`path`)을 `box/in`에 **복사**해 넘긴다(하드 링크는 원본을 고칠 수 있어 쓰지 않는다). 끝나면 `box/work`의 일반 파일만 호출한 작업 폴더로 옮긴다(심볼릭 링크·폴더는 버린다). 작업자의 `HOME`·`TMPDIR`·`MPLCONFIGDIR`는 box 안이고, matplotlib 글꼴 목록 캐시는 서버가 복사해 넣어 준다. 쓰고 난 box는 지운다.
- **tectonic**: `latex.compile_tex`가 `sandbox.run(..., isolate=isolation.tectonic_spec(...))`으로 부른다. `--version` 같은 진단 호출은 외부 입력이 없어 격리하지 않는다.
- **Windows**: 이번에는 하지 않는다. AppContainer나 제한 토큰은 작업자의 파이썬·가상환경 경로에 접근 권한(ACL)을 따로 주어야 하고, tectonic 캐시·프록시와의 관계도 따로 확인해야 한다. 그때까지 Windows는 auto에서 격리 없이 돌고(`--doctor`에 "못 씀"), require면 에러다.
- **격리 안에서 더 읽을 경로**: `OPENSTEM_ISOLATION_READ`(OS 경로 구분자로 여러 개). 기본 목록 밖에 설치된 TeX 배포판이나 파이썬(`/opt`, `/nix` 등)을 쓸 때만 넣는다.

## 결과

- 검사를 빠져나간 TeX·파이썬 코드가 있어도 홈 폴더의 파일을 읽거나, 작업 폴더 밖에 쓰거나, (작업자는) 네트워크로 보내지 못한다. `tests/test_isolation.py`가 격리 안에서 홈 폴더의 임시 비밀 파일 읽기(`\input` 포함), 작업 폴더 밖 쓰기, 작업자 네트워크가 실패하는 것과, 작업자·tectonic이 정상 동작(템플릿 컴파일, verify_math, read_document)하는 것을 확인한다. 같은 `\input`은 격리를 끄면 비밀 내용이 PDF에 그대로 실린다.
- **Ubuntu 23.10 이상(24.04 포함)**: AppArmor가 비특권 사용자 네임스페이스를 막아 bwrap이 `loopback: Failed RTM_NEWADDR: Operation not permitted`로 실패한다(CI ubuntu-latest에서 재현). auto는 이때 격리 없이 돈다. 켜려면 bwrap에만 `userns`를 허용하는 프로필을 넣는다(시스템 전체 제한을 푸는 sysctl보다 좁다). 관리자 권한으로 `/etc/apparmor.d/bwrap`에 아래를 쓰고 `sudo apparmor_parser -r /etc/apparmor.d/bwrap`:
  ```
  abi <abi/4.0>,
  include <tunables/global>
  profile bwrap /usr/bin/bwrap flags=(unconfined) {
    userns,
    include if exists <local/bwrap>
  }
  ```
- CI: ubuntu에는 bubblewrap과 위 프로필을 설치하고, GitHub Actions의 ubuntu·macos에서는 격리를 못 쓰면 격리 시험이 건너뛰지 않고 실패한다.
- 대가: 작업자 하나를 띄울 때 bwrap 설정 몇 ms, 입력 파일 복사(최대 파일 크기 안), 처리 결과 파일 복사가 든다. 사용자 폴더의 글꼴(`~/.fonts` 등)은 격리 안에서 보이지 않아 pymupdf 내장 글꼴로 대신한다. TeX Live 같은 다른 TeX를 `OPENSTEM_TECTONIC`으로 쓰면 그 배포판 경로(예: `/var/lib/texmf`)를 `OPENSTEM_ISOLATION_READ`에 넣어야 한다.
- **남은 한계**:
  - Windows에는 운영체제 격리가 없다.
  - tectonic은 네트워크가 열려 있다. 격리 안에서 읽을 수 있는 것(시스템 파일, 작업 폴더, 번들 캐시)은 밖으로 보낼 수 있다. 다만 사용자 파일은 보이지 않는다.
  - tectonic 번들 캐시는 쓸 수 있어서, 우회한 TeX 코드가 캐시를 오염시켜 다음 컴파일에 영향을 줄 수 있다.
  - pandoc(export_document의 변환)은 아직 격리하지 않는다. pandoc은 `--sandbox`(구조 읽기, 참고문헌 처리)와 작업 폴더 안의 복사본(그림, 서식 템플릿)만 다루지만, 운영체제가 막지는 않는다.
  - macOS 프로필은 파일 정보(stat: 이름·크기·시각)를 막지 않는다. 내용은 못 읽는다. `sandbox-exec`는 Apple이 사용 중단(deprecated)으로 표시한 도구라 앞으로 없어질 수 있다(그러면 auto에서 격리 없이 돈다).
  - auto에서 격리를 못 쓰는 PC(bwrap 미설치, 사용자 네임스페이스 금지)는 예전과 같은 방어선만 있다. 확인은 `openstem-mcp --doctor`의 "운영체제 격리" 줄.
- **새 PC 설치 점검에서 고친 것(2026-11-05)**: macOS의 tectonic 캐시 이름을 `~/Library/Caches/Tectonic`으로 잘못 붙여, 새 Mac에서 TeX 번들을 처음 받을 때 tectonic이 `Operation not permitted`로 멈췄다(회로도·PDF·HWPX 수식 그림이 모두 실패). CI는 `TECTONIC_CACHE_DIR`을 정하고 번들을 캐시에서 복원해서 이 길을 타지 않았다. 캐시 폴더를 tectonic과 같은 규칙으로 정하고, 깨끗한 가상 PC에서 격리를 켠 채 처음 받는 길을 `Install check` 워크플로가 시험한다.
- 보안 범위 전체는 [SECURITY.md](../../SECURITY.md).
