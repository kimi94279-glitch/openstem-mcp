"""templates/의 모든 예제: 메타데이터가 있고, 경고 없이 컴파일된다."""

from collections import Counter

import pytest

from openstem.tools import templates
from openstem.tools.tikz import ALLOWED_PACKAGES, render_tikz

ALL = templates.load_templates()
MINIMUM = {"circuits": 10, "physics": 10, "math": 10, "diagrams": 10}  # 마일스톤마다 올린다


def test_minimum_template_counts():
    counts = Counter(t.category for t in ALL)
    for category, minimum in MINIMUM.items():
        assert counts[category] >= minimum, f"{category}: {counts[category]}개 < {minimum}개"


@pytest.mark.parametrize("template", ALL, ids=[t.id for t in ALL])
def test_template_metadata(template):
    assert template.title != template.name, "% title: 이 없다"
    assert template.description, "% description: 이 없다"
    assert set(template.packages) <= set(ALLOWED_PACKAGES)


@pytest.mark.parametrize("template", ALL, ids=[t.id for t in ALL])
def test_template_compiles_cleanly(template, tectonic):
    result = render_tikz(template.code, template.packages, template.libraries, name=template.name)
    assert result.ok, result.data.get("errors")
    assert "warnings" not in result.data, result.data["warnings"]


def test_catalog_lists_every_template():
    catalog = templates.catalog()
    assert all(t.id in catalog for t in ALL)


def test_find_accepts_uri_and_rejects_unknown():
    assert templates.find("template://circuits/rc_series").id == "circuits/rc_series"
    with pytest.raises(templates.TemplateNotFound):
        templates.find("circuits/nope")
