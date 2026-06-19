from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


class TemplateDataFactory:
    """Builds run-specific data from a template baseline and schema."""

    def __init__(self, run_id: str, now: datetime | None = None) -> None:
        self.run_id = run_id
        self.now = now or datetime.now()
        self.run_suffix = self.run_id[-8:]

    def build(self, baseline: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        run_data = deepcopy(baseline)
        field_rules = schema.get("field_rules", {})
        cultivation = run_data.get("cultivation_template", {})
        for field_name, rule in field_rules.items():
            if field_name not in cultivation:
                continue
            cultivation[field_name] = self._apply_rule(
                field_name=field_name,
                value=cultivation[field_name],
                rule=rule,
            )
        run_data.setdefault("run_meta", {})
        run_data["run_meta"].update(
            {
                "run_id": self.run_id,
                "generated_at": self.now.isoformat(),
                "strategy": "baseline_plus_safe_variation",
            }
        )
        return run_data

    def _apply_rule(self, field_name: str, value: Any, rule: dict[str, Any]) -> Any:
        strategy = rule.get("strategy", "constant")
        if strategy == "constant":
            return value
        if strategy == "suffix_text":
            prefix = rule.get("prefix", "")
            return f"{prefix}{value}-{self.run_suffix}"
        if strategy == "remark_with_run_suffix":
            return f"{value} #{self.run_suffix}"
        if strategy == "timestamp_text":
            fmt = rule.get("format", "%Y%m%d%H%M%S")
            return self.now.strftime(fmt)
        if strategy == "int_offset":
            offset = int(rule.get("offset", 0))
            return int(value) + offset
        if strategy == "iso_date_today":
            return self.now.strftime("%Y-%m-%d")
        return value
