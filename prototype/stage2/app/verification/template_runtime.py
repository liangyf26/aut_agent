from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class TemplateRuntimeData:
    baseline: dict[str, Any]
    run_data: dict[str, Any]
    generated_files: dict[str, Path] | None = None
    locator_hints: dict[str, Any] | None = None

    def resolve_ref(self, ref: str | None) -> Any:
        if not ref:
            return None

        current: Any = {
            **self.run_data,
            "baseline": self.baseline,
            "run_data": self.run_data,
            "generated_files": self.generated_files or {},
            "locator_hints": self.locator_hints or {},
        }
        for part in ref.split("."):
            if isinstance(current, Mapping):
                current = current.get(part)
                continue
            return None
        return current

    def locator_hint(self, key: str | None) -> Any:
        if not key:
            return None
        return (self.locator_hints or {}).get(key)

    def has_locator_hint(self, key: str | None) -> bool:
        if not key:
            return False
        return key in (self.locator_hints or {})

    def generated_file(self, ref: str) -> Path | None:
        value = self.resolve_ref(ref)
        if isinstance(value, Path):
            return value
        if isinstance(value, str) and value:
            return Path(value)
        return None

    @property
    def initial_form(self) -> dict[str, Any]:
        return self.run_data.get("initial_form", {})

    @property
    def cultivation_template(self) -> dict[str, Any]:
        return self.run_data.get("cultivation_template", {})

    @property
    def filing_submit(self) -> dict[str, Any]:
        return self.run_data.get("filing_submit", {})
