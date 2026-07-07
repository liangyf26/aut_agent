from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
import re
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
        field_constraints = schema.get("field_constraints", {})
        generation_log: list[dict[str, Any]] = []

        for field_name, raw_rule in field_rules.items():
            rule = self._normalize_mapping(raw_rule)
            container, target_key, resolved_path = self._resolve_target(run_data, field_name, rule)
            if container is None or target_key not in container:
                generation_log.append(
                    {
                        "field": field_name,
                        "status": "skipped_missing_target",
                        "resolved_path": resolved_path,
                        "rule": rule,
                    }
                )
                continue

            original_value = container[target_key]
            generated_value = self._apply_rule(
                field_name=field_name,
                value=original_value,
                rule=rule,
            )
            constrained_value, guard_info = self._apply_constraints(
                value=generated_value,
                baseline_value=original_value,
                rule=rule,
                schema_constraints=self._normalize_mapping(field_constraints.get(field_name)),
            )
            container[target_key] = constrained_value

            generation_log.append(
                {
                    "field": field_name,
                    "path": resolved_path,
                    "strategy": rule.get("strategy", "constant"),
                    "original_value": original_value,
                    "generated_value": generated_value,
                    "final_value": constrained_value,
                    "status": "changed" if constrained_value != original_value else "unchanged",
                    "guard_info": guard_info,
                    "used_unique_token": self._contains_unique_token(original_value, constrained_value),
                }
            )

        run_data.setdefault("run_meta", {})
        run_data["run_meta"].update(
            {
                "run_id": self.run_id,
                "generated_at": self.now.isoformat(),
                "strategy": schema.get("strategy", "baseline_plus_safe_variation"),
                "data_generation": {
                    "field_generation_count": len(generation_log),
                    "changed_field_count": sum(1 for item in generation_log if item.get("status") == "changed"),
                    "guarded_field_count": sum(
                        1 for item in generation_log if item.get("guard_info", {}).get("fallback_applied")
                    ),
                    "generation_log": generation_log,
                },
            }
        )
        return run_data

    def _apply_rule(self, field_name: str, value: Any, rule: dict[str, Any]) -> Any:
        strategy = rule.get("strategy", "constant")
        if strategy == "constant":
            return value
        if strategy == "suffix_text":
            prefix = self._expand_rule_text(rule.get("prefix", ""), field_name)
            return f"{prefix}{value}-{self.run_suffix}"
        if strategy == "unique_text":
            return self._build_unique_text(value, field_name, rule)
        if strategy == "remark_with_run_suffix":
            return self._build_remark(value, field_name, rule)
        if strategy == "pick_variant":
            return self._pick_variant(value, field_name, rule)
        if strategy == "timestamp_text":
            fmt = str(rule.get("format", "%Y%m%d%H%M%S"))
            return self.now.strftime(fmt)
        if strategy == "int_offset":
            return self._offset_integer(value, field_name, rule)
        if strategy == "numeric_string_offset":
            return self._offset_numeric_string(value, field_name, rule)
        if strategy == "iso_date_today":
            fmt = str(rule.get("format", "%Y-%m-%d"))
            return self.now.strftime(fmt)
        if strategy == "iso_date_offset_days":
            return self._offset_iso_date(value, field_name, rule)
        if strategy == "sequence_number":
            return self._sequence_number(field_name, rule)
        if strategy == "append_tokens":
            return self._append_tokens(value, field_name, rule)
        return value

    def _normalize_mapping(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, str):
            return {"strategy": value}
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    def _resolve_target(
        self,
        run_data: dict[str, Any],
        field_name: str,
        rule: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, str]:
        path = str(rule.get("path") or field_name)
        cultivation = run_data.get("cultivation_template")
        if isinstance(cultivation, dict) and path in cultivation:
            return cultivation, path, f"cultivation_template.{path}"
        if path in run_data:
            return run_data, path, path
        if "." in path:
            container, key = self._resolve_path(run_data, path)
            if container is not None:
                return container, key, path
        if isinstance(cultivation, dict) and field_name in cultivation:
            return cultivation, field_name, f"cultivation_template.{field_name}"
        return None, field_name, path

    def _resolve_path(
        self,
        root: dict[str, Any],
        path: str,
    ) -> tuple[dict[str, Any] | None, str]:
        parts = [part for part in path.split(".") if part]
        if not parts:
            return None, path
        cursor: Any = root
        for part in parts[:-1]:
            if not isinstance(cursor, dict):
                return None, path
            cursor = cursor.get(part)
            if not isinstance(cursor, dict):
                return None, path
        if not isinstance(cursor, dict):
            return None, path
        return cursor, parts[-1]

    def _build_unique_text(self, value: Any, field_name: str, rule: dict[str, Any]) -> str:
        base_text = "" if value is None else str(value)
        if rule.get("strip"):
            base_text = base_text.strip()
        prefix = self._expand_rule_text(rule.get("prefix", ""), field_name)
        separator = str(rule.get("separator", "-"))
        token = self._expand_rule_text(rule.get("token") or self._token_for_field(field_name), field_name)
        position = str(rule.get("position", "suffix")).lower()
        enriched = f"{prefix}{base_text}"
        if not enriched:
            return token
        if position == "prefix":
            return f"{token}{separator}{enriched}"
        return f"{enriched}{separator}{token}"

    def _build_remark(self, value: Any, field_name: str, rule: dict[str, Any]) -> str:
        base_text = "" if value is None else str(value).rstrip()
        parts = [base_text] if base_text else []
        variant = self._select_variant(rule.get("variants"), field_name)
        if variant:
            parts.append(self._expand_rule_text(str(variant), field_name))
        if rule.get("include_run_suffix", True):
            marker_prefix = str(rule.get("marker_prefix", "#"))
            parts.append(f"{marker_prefix}{self.run_suffix}")
        extra_notes = rule.get("extra_notes")
        if isinstance(extra_notes, list):
            parts.extend(self._expand_rule_text(str(note), field_name) for note in extra_notes if note not in (None, ""))
        return " ".join(part for part in parts if part).strip()

    def _pick_variant(self, value: Any, field_name: str, rule: dict[str, Any]) -> Any:
        variant = self._select_variant(rule.get("variants") or rule.get("choices"), field_name)
        if variant is None:
            return value
        variant = self._expand_rule_text(str(variant), field_name)
        if rule.get("append_to_base") and value not in (None, ""):
            separator = str(rule.get("separator", " "))
            return f"{value}{separator}{variant}"
        return variant

    def _offset_integer(self, value: Any, field_name: str, rule: dict[str, Any]) -> Any:
        number = self._coerce_int(value)
        if number is None:
            return value
        offset = self._coerce_int(rule.get("offset"), default=0)
        jitter = self._bounded_jitter(field_name, rule.get("jitter"))
        return number + offset + jitter

    def _offset_numeric_string(self, value: Any, field_name: str, rule: dict[str, Any]) -> Any:
        base_text = "" if value is None else str(value).strip()
        if not base_text:
            return base_text
        digits = "".join(character for character in base_text if character.isdigit())
        if not digits:
            return self._build_unique_text(value, field_name, rule)

        width = self._coerce_int(rule.get("width"), default=len(digits))
        offset = self._coerce_int(rule.get("offset"), default=0)
        jitter = self._bounded_jitter(field_name, rule.get("jitter"))
        candidate = int(digits) + offset + jitter
        if width > 0:
            modulus = 10**width
            candidate %= modulus
            numeric_part = f"{candidate:0{width}d}"
        else:
            numeric_part = str(candidate)

        prefix = rule.get("prefix")
        suffix = self._expand_rule_text(rule.get("suffix", ""), field_name)
        if prefix is None and base_text.isdigit() and not suffix:
            return numeric_part

        if prefix is None:
            match = re.match(r"^\D*", base_text)
            prefix = match.group(0) if match else ""
        prefix_text = self._expand_rule_text(prefix, field_name)
        return f"{prefix_text}{numeric_part}{suffix}"

    def _offset_iso_date(self, value: Any, field_name: str, rule: dict[str, Any]) -> Any:
        fmt = str(rule.get("format", "%Y-%m-%d"))
        parsed = self._parse_date(value, fmt) or self.now
        offset_days = self._coerce_int(rule.get("offset_days"), default=None)
        if offset_days is None:
            offset_days = self._coerce_int(rule.get("offset"), default=0)
        jitter_days = self._bounded_jitter(field_name, rule.get("jitter_days"))
        return (parsed + timedelta(days=offset_days + jitter_days)).strftime(fmt)

    def _sequence_number(self, field_name: str, rule: dict[str, Any]) -> str | int:
        start = self._coerce_int(rule.get("start"), default=1) or 1
        width = self._coerce_int(rule.get("width"), default=0) or 0
        prefix = self._expand_rule_text(rule.get("prefix", ""), field_name)
        suffix = self._expand_rule_text(rule.get("suffix", ""), field_name)
        candidate = start + self._stable_index(field_name, 10000)
        if width > 0:
            return f"{prefix}{candidate:0{width}d}{suffix}"
        if prefix or suffix:
            return f"{prefix}{candidate}{suffix}"
        return candidate

    def _append_tokens(self, value: Any, field_name: str, rule: dict[str, Any]) -> str:
        base_text = "" if value is None else str(value).strip()
        separator = str(rule.get("separator", " "))
        tokens: list[str] = []
        for token in rule.get("tokens", []):
            expanded = self._expand_rule_text(token, field_name)
            if expanded:
                tokens.append(expanded)
        if base_text:
            return separator.join([base_text, *tokens]).strip()
        return separator.join(tokens).strip()

    def _apply_constraints(
        self,
        value: Any,
        baseline_value: Any,
        rule: dict[str, Any],
        schema_constraints: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        constraints = dict(schema_constraints)
        constraints.update(self._normalize_mapping(rule.get("constraints")))
        for key in (
            "allowed_values",
            "choices",
            "fallback",
            "max",
            "max_length",
            "max_value",
            "min",
            "min_length",
            "min_value",
            "non_empty",
            "regex",
            "strip",
            "type",
            "date_format",
        ):
            if key in rule:
                constraints[key] = rule[key]

        guard_info = {
            "constraints_applied": sorted(constraints.keys()),
            "fallback_applied": False,
            "fallback_reason": None,
        }

        if not constraints:
            return value, guard_info

        constrained = value
        if constraints.get("strip") and isinstance(constrained, str):
            constrained = constrained.strip()

        target_type = constraints.get("type")
        if target_type == "int":
            parsed_int = self._coerce_int(constrained)
            if parsed_int is None:
                return self._fallback_with_reason(constraints, baseline_value, guard_info, "int_parse_failed")
            constrained = parsed_int
        elif target_type == "float":
            parsed_float = self._coerce_float(constrained)
            if parsed_float is None:
                return self._fallback_with_reason(constraints, baseline_value, guard_info, "float_parse_failed")
            constrained = parsed_float
        elif target_type == "str" and constrained is not None:
            constrained = str(constrained)
        elif target_type == "iso_date":
            date_format = str(constraints.get("date_format", "%Y-%m-%d"))
            parsed_date = self._parse_date(constrained, date_format)
            if parsed_date is None:
                return self._fallback_with_reason(constraints, baseline_value, guard_info, "date_parse_failed")
            constrained = parsed_date.strftime(date_format)

        if isinstance(constrained, (int, float)) and not isinstance(constrained, bool):
            minimum = self._coerce_float(
                constraints["min_value"] if "min_value" in constraints else constraints.get("min"),
                default=None,
            )
            maximum = self._coerce_float(
                constraints["max_value"] if "max_value" in constraints else constraints.get("max"),
                default=None,
            )
            if minimum is not None and constrained < minimum:
                constrained = int(minimum) if isinstance(constrained, int) else minimum
                guard_info["fallback_reason"] = "clamped_to_min"
            if maximum is not None and constrained > maximum:
                constrained = int(maximum) if isinstance(constrained, int) else maximum
                guard_info["fallback_reason"] = "clamped_to_max"

        if isinstance(constrained, str):
            max_length = self._coerce_int(constraints.get("max_length"), default=None)
            if max_length is not None and max_length >= 0 and len(constrained) > max_length:
                constrained = constrained[:max_length].rstrip()
                guard_info["fallback_reason"] = "trimmed_to_max_length"

            min_length = self._coerce_int(constraints.get("min_length"), default=None)
            if min_length is not None and len(constrained) < min_length:
                return self._fallback_with_reason(constraints, baseline_value, guard_info, "min_length_failed")

            pattern = constraints.get("regex")
            if pattern and not re.fullmatch(str(pattern), constrained):
                return self._fallback_with_reason(constraints, baseline_value, guard_info, "regex_failed")

            if constraints.get("non_empty") and not constrained.strip():
                return self._fallback_with_reason(constraints, baseline_value, guard_info, "non_empty_failed")

        allowed_values = constraints.get("allowed_values")
        if allowed_values is None:
            allowed_values = constraints.get("choices")
        if allowed_values is not None:
            allowed = list(allowed_values) if isinstance(allowed_values, (list, tuple, set)) else [allowed_values]
            if constrained not in allowed:
                fallback = self._fallback_value(constraints, baseline_value)
                guard_info["fallback_applied"] = True
                guard_info["fallback_reason"] = "allowed_values_failed"
                if fallback in allowed:
                    return fallback, guard_info
                return baseline_value, guard_info

        return constrained, guard_info

    def _fallback_value(self, constraints: dict[str, Any], baseline_value: Any) -> Any:
        fallback = constraints.get("fallback", baseline_value)
        if fallback == "__baseline__":
            return baseline_value
        return fallback

    def _fallback_with_reason(
        self,
        constraints: dict[str, Any],
        baseline_value: Any,
        guard_info: dict[str, Any],
        reason: str,
    ) -> tuple[Any, dict[str, Any]]:
        guard_info["fallback_applied"] = True
        guard_info["fallback_reason"] = reason
        return self._fallback_value(constraints, baseline_value), guard_info

    def _select_variant(self, variants: Any, field_name: str) -> Any:
        if isinstance(variants, str):
            return variants
        if not isinstance(variants, list | tuple):
            return None
        options = [option for option in variants if option not in (None, "")]
        if not options:
            return None
        return options[self._stable_index(field_name, len(options))]

    def _stable_index(self, field_name: str, size: int) -> int:
        if size <= 0:
            return 0
        return self._stable_seed(field_name) % size

    def _bounded_jitter(self, field_name: str, raw_limit: Any) -> int:
        limit = self._coerce_int(raw_limit, default=0)
        if limit <= 0:
            return 0
        span = (limit * 2) + 1
        return self._stable_index(field_name, span) - limit

    def _stable_seed(self, field_name: str) -> int:
        token = f"{self.run_id}:{field_name}"
        return sum((index + 1) * ord(character) for index, character in enumerate(token))

    def _token_for_field(self, field_name: str) -> str:
        field_digits = "".join(character for character in field_name if character.isdigit())
        if field_digits:
            return f"{self.run_suffix}{field_digits[-2:]}"
        return self.run_suffix

    def _expand_rule_text(self, value: Any, field_name: str) -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""
        replacements = {
            "{run_id}": self.run_id,
            "{run_suffix}": self.run_suffix,
            "{date}": self.now.strftime("%Y-%m-%d"),
            "{datetime}": self.now.strftime("%Y%m%d%H%M%S"),
            "{field}": field_name,
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return text

    def _contains_unique_token(self, original_value: Any, final_value: Any) -> bool:
        if not isinstance(final_value, str):
            return False
        if self.run_suffix in final_value or self.run_id in final_value:
            return True
        return isinstance(original_value, str) and final_value != original_value and any(
            marker in final_value for marker in ("#", "-", "_")
        )

    def _parse_date(self, value: Any, fmt: str) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            return None

    def _coerce_int(self, value: Any, default: int | None = None) -> int | None:
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _coerce_float(self, value: Any, default: float | None = None) -> float | None:
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
