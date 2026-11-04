# 제3자 파일

openstem-mcp 저장소(MIT)에 함께 들어 있는, 다른 사람이 만든 파일과 그 라이선스, 그리고 구현할 때 참고만 한 자료다. 함께 들어 있는 파일은 MIT가 아니라 아래 라이선스를 따른다.

## 인용 서식(CSL)

`src/openstem/render/csl/`의 서식 파일은 [Citation Style Language styles 저장소](https://github.com/citation-style-language/styles)(커밋 `3220666a2b6918cccfeca3d86f6f7d9ea9661236`, 2026-11-04에 받음)에서 가져왔다. 내용은 바꾸지 않았고 파일 머리에 출처 주석만 더했다. 라이선스는 [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)이고, 지은이와 기여자는 각 파일의 `<info>`에 있다.

| 파일 | 원본 | 서식 |
|---|---|---|
| `apa.csl` | `apa.csl` | American Psychological Association 7th edition |
| `ieee.csl` | `ieee.csl` | IEEE Reference Guide |
| `mla.csl` | `modern-language-association.csl` | Modern Language Association 9th edition |

`citation_style="chicago"`(기본)는 파일이 없다. pandoc에 들어 있는 Chicago Manual of Style author-date 서식(역시 CSL 프로젝트, CC BY-SA 3.0)을 쓴다.

서식을 더할 때는 부모 서식을 내려받는 종속 서식(`rel="independent-parent"`)이 아닌 독립 서식만 넣는다(`tests/test_export.py`가 확인한다).

## docx·pptx 기본 서식

`src/openstem/render/reference.docx`·`reference.pptx`는 pandoc의 기본 데이터 파일(`pandoc --print-default-data-file reference.docx`)을 `src/openstem/render/refdocs.py`가 고쳐 만든 것이다. pandoc 데이터 파일은 pandoc과 같은 GPL-2.0-or-later로 배포되지만, pandoc 저작자들은 출력 서식용 데이터 파일(템플릿·reference 문서)을 이것으로 만든 문서에 제약 없이 쓸 수 있게 한다는 입장이다([pandoc 템플릿 저장소 안내](https://github.com/jgm/pandoc-templates) 참고: 템플릿은 BSD-3-Clause 또는 GPL 중 고를 수 있다).

## 한글(HWPX) 작성기 — `src/openstem/render/hwpx.py`(참고만 함, 들어 있는 남의 파일 없음)

| 자료 | 라이선스 | 어떻게 썼나 |
|---|---|---|
| 한글과컴퓨터 OWPML 공개 규격(KS X 6101, 개방형 워드프로세서 마크업 언어) | 공개 표준 문서 | 패키지 구조(`mimetype`, `META-INF/container.xml`, `Contents/content.hpf` 등), 요소·속성 이름, 이름공간 URI |
| [python-hwpx](https://pypi.org/project/python-hwpx/) 6.11.0 (airmang) | Apache-2.0 | 그림(`hp:pic`)·표(`hp:tbl`)·수식(`hp:equation`)·글자 모양(`hh:charPr`)의 자식 요소 순서와 기본 속성값, 빈 문서의 `header.xml`·`section0.xml` 짜임을 읽어 보고 관찰만 했다. 코드와 빈 문서 템플릿(`Skeleton.hwpx`)은 가져오지 않았고 의존성으로도 넣지 않았다. |

- 작성기는 파이썬 표준 라이브러리(`zipfile`, 문자열 XML)로 새로 썼다.
- 글꼴은 이름(함초롬바탕·함초롬돋움)만 적고 글꼴 파일은 넣지 않는다.
- "HWP"·"HWPX"·"한글"은 한글과컴퓨터의 문서 형식·제품 이름이다. 이 프로젝트는 한글과컴퓨터와 관계가 없고, 만든 파일을 한컴오피스에서 열어 확인하지 못했다([docs/tools.md](docs/tools.md#export_document)).
