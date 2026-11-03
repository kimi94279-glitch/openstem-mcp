"""templates/ 아래 검증된 TikZ 예제. MCP 리소스(template://{category}/{name})와 get_template 툴로 노출한다.

템플릿 파일 머리에는 메타데이터 주석을 둔다:
    % title: RC 직렬회로
    % description: 한 줄 설명
    % packages: circuitikz
    % libraries: arrows.meta, calc
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

URI_PREFIX = "template://"
_META_RE = re.compile(r"^%\s*(title|description|packages|libraries)\s*:\s*(.*)$")

DESCRIPTION = r"""Read verified figure examples (circuits, physics, math, diagrams) to adapt before render_tikz. No name: list them.
Example: {"name": "circuits/rc_series"}
On failure: pick an exact name from the list."""


@dataclass
class Template:
    id: str  # "circuits/rc_series"
    category: str
    name: str
    title: str
    description: str
    packages: list[str]
    libraries: list[str]
    code: str


class TemplateNotFound(LookupError):
    pass


def templates_root() -> Path:
    here = Path(__file__).resolve()
    packaged = here.parents[1] / "templates"  # wheel 설치: openstem/templates
    if packaged.is_dir():
        return packaged
    return here.parents[3] / "templates"  # 저장소에서 개발할 때


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse(path: Path, root: Path) -> Template:
    code = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    for line in code.splitlines():
        if not line.startswith("%"):
            break
        if m := _META_RE.match(line):
            meta[m.group(1)] = m.group(2).strip()
    rel = path.relative_to(root).with_suffix("")
    category, name = rel.parts[0], "/".join(rel.parts[1:])
    return Template(
        id=f"{category}/{name}",
        category=category,
        name=name,
        title=meta.get("title", name),
        description=meta.get("description", ""),
        packages=_split(meta.get("packages", "")),
        libraries=_split(meta.get("libraries", "")),
        code=code,
    )


def load_templates() -> list[Template]:
    root = templates_root()
    if not root.is_dir():
        return []
    return [_parse(p, root) for p in sorted(root.glob("*/*.tex"))]


def find(template_id: str) -> Template:
    template_id = template_id.strip().removeprefix(URI_PREFIX).removesuffix(".tex")
    for t in load_templates():
        if t.id == template_id:
            return t
    raise TemplateNotFound(f"No template '{template_id}'. Call get_template without name to list them.")


def catalog() -> str:
    templates = load_templates()
    if not templates:
        return "No templates installed."
    lines = [f"{len(templates)} templates. Read one with get_template(name=\"<id>\").", ""]
    category = None
    for t in templates:
        if t.category != category:
            category = t.category
            lines.append(f"[{category}]")
        lines.append(f"- {t.id}: {t.title} — {t.description}")
    return "\n".join(lines)


def describe(t: Template) -> str:
    return "\n".join(
        [
            f"# {t.id}: {t.title}",
            t.description,
            f"render_tikz args: packages={t.packages}, tikz_libraries={t.libraries}",
            "Adapt the code below to the request (change coordinates, labels, parts; keep the structure) and pass it as render_tikz code.",
            "",
            t.code,
        ]
    )


def get_template(name: str | None = None) -> str:
    if not name or not name.strip():
        return catalog()
    return describe(find(name))
