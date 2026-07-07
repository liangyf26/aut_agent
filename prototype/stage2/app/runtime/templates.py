from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TemplateBundle:
    name: str
    template_dir: Path
    template: dict[str, Any]
    baseline: dict[str, Any]
    data_schema: dict[str, Any]
    locator_hints: dict[str, Any]


def read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_template_bundle(template_dir: Path) -> TemplateBundle:
    return TemplateBundle(
        name=template_dir.name,
        template_dir=template_dir,
        template=read_json_file(template_dir / "template.json"),
        baseline=read_json_file(template_dir / "baseline.json"),
        data_schema=read_json_file(template_dir / "data_schema.json"),
        locator_hints=read_json_file(template_dir / "locator_hints.json"),
    )
