from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TemplateRuntimeData:
    baseline: dict[str, Any]
    run_data: dict[str, Any]

    @property
    def initial_form(self) -> dict[str, Any]:
        return self.run_data.get("initial_form", {})

    @property
    def cultivation_template(self) -> dict[str, Any]:
        return self.run_data.get("cultivation_template", {})

    @property
    def filing_submit(self) -> dict[str, Any]:
        return self.run_data.get("filing_submit", {})
