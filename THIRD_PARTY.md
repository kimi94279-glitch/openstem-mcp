# 제3자 자료

이 저장소에 남의 코드나 데이터 파일을 그대로 넣은 것은 없다. 아래는 구현할 때 참고만 한 자료와 그 라이선스다.

## 한글(HWPX) 작성기 — `src/openstem/render/hwpx.py`

| 자료 | 라이선스 | 어떻게 썼나 |
|---|---|---|
| 한글과컴퓨터 OWPML 공개 규격(KS X 6101, 개방형 워드프로세서 마크업 언어) | 공개 표준 문서 | 패키지 구조(`mimetype`, `META-INF/container.xml`, `Contents/content.hpf` 등), 요소·속성 이름, 이름공간 URI |
| [python-hwpx](https://pypi.org/project/python-hwpx/) 6.11.0 (airmang) | Apache-2.0 | 그림(`hp:pic`)·표(`hp:tbl`)·수식(`hp:equation`)·글자 모양(`hh:charPr`)의 자식 요소 순서와 기본 속성값, 빈 문서의 `header.xml`·`section0.xml` 짜임을 읽어 보고 관찰만 했다. 코드와 빈 문서 템플릿(`Skeleton.hwpx`)은 가져오지 않았고 의존성으로도 넣지 않았다. |

- 작성기는 파이썬 표준 라이브러리(`zipfile`, 문자열 XML)로 새로 썼다.
- 글꼴은 이름(함초롬바탕·함초롬돋움)만 적고 글꼴 파일은 넣지 않는다.
- "HWP"·"HWPX"·"한글"은 한글과컴퓨터의 문서 형식·제품 이름이다. 이 프로젝트는 한글과컴퓨터와 관계가 없고, 만든 파일을 한컴오피스에서 열어 확인하지 못했다([docs/tools.md](docs/tools.md#export_document)).
