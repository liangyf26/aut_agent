from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

from prototype.stage2.app.runtime.policy_gate import (
    POLICY_ALLOWED,
    POLICY_BLOCKED,
    POLICY_NEEDS_REVIEW,
    RISK_RISKY_SUBMIT,
)


RUN_POLICY_SCHEMA_VERSION = 1

_POLICY_ROOT_KEYS = ("run_policy",)
_PROJECTS_KEY = "projects"
_TEMPLATES_KEY = "templates"
_ALLOWLIST_KEYS = ("allowlist", "whitelist")
_DEFAULT_DECISION_KEYS = (
    "risky_submit_default_decision",
    "high_risk_default_decision",
    "default_high_risk_decision",
)
_REVIEW_DEFAULT_KEYS = (
    "require_review_for_unlisted_risky_submit",
    "manual_review_for_unlisted_risky_submit",
)


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


def _to_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_identifier(value: Any) -> str:
    text = _text(value) or ""
    if not text:
        return ""
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in text)


def _normalize_decision(value: Any, *, default: str) -> str:
    normalized = (_normalize_identifier(value) or _normalize_identifier(default)).strip("_")
    if normalized in {POLICY_ALLOWED, POLICY_BLOCKED, POLICY_NEEDS_REVIEW}:
        return normalized
    aliases = {
        "allow": POLICY_ALLOWED,
        "allowed": POLICY_ALLOWED,
        "permit": POLICY_ALLOWED,
        "permitted": POLICY_ALLOWED,
        "block": POLICY_BLOCKED,
        "blocked": POLICY_BLOCKED,
        "deny": POLICY_BLOCKED,
        "denied": POLICY_BLOCKED,
        "forbid": POLICY_BLOCKED,
        "forbidden": POLICY_BLOCKED,
        "review": POLICY_NEEDS_REVIEW,
        "needs_review": POLICY_NEEDS_REVIEW,
        "manual_review": POLICY_NEEDS_REVIEW,
        "needsreview": POLICY_NEEDS_REVIEW,
    }
    return aliases.get(normalized, default)


def _normalize_risk_level(value: Any) -> str:
    normalized = (_normalize_identifier(value) or RISK_RISKY_SUBMIT).strip("_")
    aliases = {
        "submit": RISK_RISKY_SUBMIT,
        "high_risk_submit": RISK_RISKY_SUBMIT,
        "real_submit": RISK_RISKY_SUBMIT,
        "risky_submit": RISK_RISKY_SUBMIT,
    }
    return aliases.get(normalized, normalized or RISK_RISKY_SUBMIT)


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _read_json_file(path: Path) -> tuple[str, dict[str, Any], list[str]]:
    if not path.exists():
        return "missing", {}, [f"Run policy file does not exist: {path}"]

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return "invalid", {}, [f"Run policy file could not be read: {exc}"]

    if not text.strip():
        return "empty", {}, [f"Run policy file is empty: {path}"]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return "invalid", {}, [f"Run policy JSON is invalid: {exc.msg}"]

    mapping = _to_mapping(payload)
    if not mapping:
        return "empty", {}, [f"Run policy root must be a JSON object: {path}"]
    return "loaded", mapping, []


def _unwrap_policy_root(payload: Mapping[str, Any], *, source_name: str) -> tuple[dict[str, Any], str]:
    root = dict(payload)
    for key in _POLICY_ROOT_KEYS:
        nested = _to_mapping(root.get(key))
        if nested:
            return nested, f"{source_name}.{key}"
    return root, source_name


def _find_named_section(mapping: Mapping[str, Any], lookup: str | None) -> tuple[str | None, dict[str, Any]]:
    sections = _to_mapping(mapping)
    if not sections or not lookup:
        return None, {}

    normalized_lookup = _normalize_identifier(lookup)
    for key, value in sections.items():
        if _normalize_identifier(key) == normalized_lookup:
            return _text(key), _to_mapping(value)
    return None, {}


def _pick_default_decision(mapping: Mapping[str, Any], *, current: str) -> str | None:
    for key in _DEFAULT_DECISION_KEYS:
        if key in mapping:
            return _normalize_decision(mapping.get(key), default=current)
    for key in _REVIEW_DEFAULT_KEYS:
        review = _bool(mapping.get(key))
        if review is True:
            return POLICY_NEEDS_REVIEW
        if review is False:
            return POLICY_BLOCKED
    return None


def _rule_bucket(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    mapping = _to_mapping(value)
    return [mapping] if mapping else [value]


@dataclass(frozen=True, slots=True)
class RunPolicyRule:
    rule_id: str | None = None
    action_key: str | None = None
    action_id: str | None = None
    action_name: str | None = None
    action_type: str | None = None
    risk_level: str = RISK_RISKY_SUBMIT
    decision: str = POLICY_ALLOWED
    enabled: bool = True
    note: str | None = None
    project_name: str | None = None
    template_name: str | None = None
    source: str | None = None

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        project_name: str | None = None,
        template_name: str | None = None,
        source: str | None = None,
    ) -> "RunPolicyRule":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(
                action_key=_text(value),
                project_name=_text(project_name),
                template_name=_text(template_name),
                source=source,
            )

        data = _to_mapping(value)
        if not data:
            return cls(
                project_name=_text(project_name),
                template_name=_text(template_name),
                source=source,
            )

        return cls(
            rule_id=_text(data.get("rule_id") or data.get("id") or data.get("key")),
            action_key=_text(data.get("action_key") or data.get("action") or data.get("step") or data.get("value")),
            action_id=_text(data.get("action_id") or data.get("step_id")),
            action_name=_text(data.get("action_name") or data.get("name") or data.get("title")),
            action_type=_text(data.get("action_type") or data.get("type")),
            risk_level=_normalize_risk_level(data.get("risk_level") or data.get("risk")),
            decision=_normalize_decision(data.get("decision") or data.get("status"), default=POLICY_ALLOWED),
            enabled=_bool(data.get("enabled")) is not False,
            note=_text(data.get("note") or data.get("reason") or data.get("summary")),
            project_name=_text(data.get("project_name") or project_name),
            template_name=_text(data.get("template_name") or template_name),
            source=source,
        )

    def is_matchable(self) -> bool:
        return any((self.action_key, self.action_id, self.action_name, self.action_type))

    def identity(self) -> tuple[Any, ...]:
        return (
            self.rule_id,
            self.action_key,
            self.action_id,
            self.action_name,
            self.action_type,
            self.risk_level,
            self.decision,
            self.enabled,
            self.note,
            self.project_name,
            self.template_name,
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(frozen=True, slots=True)
class RunPolicyLoadResult:
    policy_path: Path
    load_status: str
    schema_version: int = RUN_POLICY_SCHEMA_VERSION
    project_name: str | None = None
    template_name: str | None = None
    risky_submit_default_decision: str = POLICY_BLOCKED
    resolved_default_source: str = "built_in_default"
    allow_rules: list[RunPolicyRule] = field(default_factory=list)
    applied_sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.load_status != "missing"

    def to_policy_gate_payload(self) -> dict[str, Any]:
        return {
            "run_policy": {
                "schema_version": self.schema_version,
                "risky_submit_default_decision": self.risky_submit_default_decision,
                "whitelist": [rule.to_dict() for rule in self.allow_rules],
                "source_resolution": {
                    "load_status": self.load_status,
                    "document_source": str(self.policy_path),
                    "applied_sources": list(self.applied_sources),
                    "resolved_default_source": self.resolved_default_source,
                    "project_name": self.project_name,
                    "template_name": self.template_name,
                },
            }
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["policy_path"] = str(self.policy_path)
        payload["allow_rules"] = [rule.to_dict() for rule in self.allow_rules]
        return _compact_dict(payload)


def resolve_run_policy_payload(
    payload: Mapping[str, Any] | None,
    *,
    project_name: str | None = None,
    template_name: str | None = None,
    policy_path: Path | None = None,
    source_name: str = "inline",
    load_status: str = "loaded",
    notes: list[str] | None = None,
) -> RunPolicyLoadResult:
    root_payload = _to_mapping(payload)
    unwrapped, root_source = _unwrap_policy_root(root_payload, source_name=source_name)
    schema_version = _coerce_schema_version(unwrapped.get("schema_version") or unwrapped.get("version"))

    applied_sections: list[tuple[str, dict[str, Any], str | None, str | None]] = []
    if unwrapped:
        applied_sections.append((root_source, unwrapped, None, None))

    matched_project_name, project_section = _find_named_section(unwrapped.get(_PROJECTS_KEY), project_name)
    if project_section:
        applied_sections.append(
            (
                f"{root_source}.{_PROJECTS_KEY}.{_normalize_identifier(matched_project_name)}",
                project_section,
                matched_project_name,
                None,
            )
        )

    matched_template_name: str | None = None
    template_section: dict[str, Any] = {}
    if project_section and template_name:
        matched_template_name, template_section = _find_named_section(project_section.get(_TEMPLATES_KEY), template_name)
        if template_section:
            applied_sections.append(
                (
                    f"{root_source}.{_PROJECTS_KEY}.{_normalize_identifier(matched_project_name)}.{_TEMPLATES_KEY}.{_normalize_identifier(matched_template_name)}",
                    template_section,
                    matched_project_name,
                    matched_template_name,
                )
            )

    effective_project_name = matched_project_name or _text(project_name)
    effective_template_name = matched_template_name or _text(template_name)

    default_decision = POLICY_BLOCKED
    resolved_default_source = "built_in_default"
    for section_source, section, _, _ in applied_sections:
        picked = _pick_default_decision(section, current=default_decision)
        if picked is None:
            continue
        default_decision = picked
        resolved_default_source = section_source

    rules: list[RunPolicyRule] = []
    seen_rule_ids: set[tuple[Any, ...]] = set()
    for section_source, section, scoped_project_name, scoped_template_name in reversed(applied_sections):
        scoped_rules = _collect_rules(
            section,
            source_prefix=section_source,
            project_name=scoped_project_name or effective_project_name,
            template_name=scoped_template_name or effective_template_name,
        )
        for rule in scoped_rules:
            identity = rule.identity()
            if identity in seen_rule_ids:
                continue
            seen_rule_ids.add(identity)
            rules.append(rule)

    result_notes = list(notes or [])
    if project_name and not project_section:
        result_notes.append(f"Project policy section not found: {project_name}")
    if template_name and project_section and not template_section:
        result_notes.append(f"Template policy section not found under project {effective_project_name}: {template_name}")

    return RunPolicyLoadResult(
        policy_path=policy_path or Path("run_policy.json"),
        load_status=load_status if root_payload or load_status != "loaded" else "empty",
        schema_version=schema_version,
        project_name=effective_project_name,
        template_name=effective_template_name,
        risky_submit_default_decision=default_decision,
        resolved_default_source=resolved_default_source,
        allow_rules=rules,
        applied_sources=[section_source for section_source, _, _, _ in applied_sections],
        notes=result_notes,
        raw_payload=root_payload,
    )


def load_run_policy(
    path: str | Path,
    *,
    project_name: str | None = None,
    template_name: str | None = None,
) -> RunPolicyLoadResult:
    policy_path = Path(path)
    load_status, payload, notes = _read_json_file(policy_path)
    if load_status != "loaded":
        return RunPolicyLoadResult(
            policy_path=policy_path,
            load_status=load_status,
            project_name=_text(project_name),
            template_name=_text(template_name),
            notes=notes,
        )

    return resolve_run_policy_payload(
        payload,
        project_name=project_name,
        template_name=template_name,
        policy_path=policy_path,
        source_name="file",
        load_status=load_status,
        notes=notes,
    )


def _coerce_schema_version(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return RUN_POLICY_SCHEMA_VERSION
    return parsed if parsed > 0 else RUN_POLICY_SCHEMA_VERSION


def _collect_rules(
    section: Mapping[str, Any],
    *,
    source_prefix: str,
    project_name: str | None,
    template_name: str | None,
) -> list[RunPolicyRule]:
    rules: list[RunPolicyRule] = []
    for key in _ALLOWLIST_KEYS:
        if key not in section:
            continue
        bucket_source = f"{source_prefix}.{key}"
        for item in _rule_bucket(section.get(key)):
            rule = RunPolicyRule.from_value(
                item,
                project_name=project_name,
                template_name=template_name,
                source=bucket_source,
            )
            if rule.enabled and rule.is_matchable():
                rules.append(rule)
    return rules


__all__ = [
    "RUN_POLICY_SCHEMA_VERSION",
    "RunPolicyLoadResult",
    "RunPolicyRule",
    "load_run_policy",
    "resolve_run_policy_payload",
]
