# 제3자 파일

openstem-mcp 저장소(MIT)에 함께 들어 있는, 다른 사람이 만든 파일과 그 라이선스다. 이 파일들은 MIT가 아니라 아래 라이선스를 따른다.

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
