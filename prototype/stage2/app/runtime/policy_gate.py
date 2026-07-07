from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from collections.abc import Mapping, Sequence


POLICY_ALLOWED = "allowed"
POLICY_BLOCKED = "blocked"
POLICY_NEEDS_REVIEW = "needs_review"

SAFETY_POLICY_LOW_RISK_ONLY = "low_risk_only"
SAFETY_POLICY_TEST_ENV_FULL_ACCESS = "test_env_full_access"

RISK_SAFE_READ = "safe_read"
RISK_SAFE_INTERACT = "safe_interact"
RISK_RISKY_SUBMIT = "risky_submit"
RISK_FORBIDDEN_MUTATION = "forbidden_mutation"

_POLICY_CONTAINER_KEYS = (
    "run_policy",
    "policy",
    "runtime_policy",
    "risk_policy",
    "high_risk_actions",
)
_ALLOWLIST_KEYS = (
    "allowlist",
    "whitelist",
    "action_allowlist",
    "action_whitelist",
    "risk_allowlist",
    "risk_whitelist",
    "high_risk_action_allowlist",
    "high_risk_action_whitelist",
    "allowed_high_risk_actions",
)
_DEFAULT_RISKY_KEYS = (
    "risky_submit_default_decision",
    "high_risk_default_decision",
    "default_high_risk_decision",
)
_REVIEW_RISKY_KEYS = (
    "require_review_for_unlisted_risky_submit",
    "high_risk_requires_review",
    "manual_review_for_unlisted_risky_submit",
)
_ENTRY_COLLECTION_KEYS = ("rules", "items", "entries", "actions")


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


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        results: list[str] = []
        for item in value:
            text = _text(item)
            if text:
                results.append(text)
        return results
    text = _text(value)
    return [text] if text else []


def _normalize_token(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text.strip().lower()


def _normalize_risk_level(value: Any) -> str:
    normalized = (_normalize_token(value) or RISK_SAFE_READ).replace("-", "_").replace(" ", "_")
    aliases = {
        "read": RISK_SAFE_READ,
        "read_only": RISK_SAFE_READ,
        "safe_read": RISK_SAFE_READ,
        "safe_interact": RISK_SAFE_INTERACT,
        "interact": RISK_SAFE_INTERACT,
        "interaction": RISK_SAFE_INTERACT,
        "fill": RISK_SAFE_INTERACT,
        "risky_submit": RISK_RISKY_SUBMIT,
        "submit": RISK_RISKY_SUBMIT,
        "high_risk_submit": RISK_RISKY_SUBMIT,
        "real_submit": RISK_RISKY_SUBMIT,
        "forbidden_mutation": RISK_FORBIDDEN_MUTATION,
        "destructive_mutation": RISK_FORBIDDEN_MUTATION,
        "forbidden": RISK_FORBIDDEN_MUTATION,
        "delete": RISK_FORBIDDEN_MUTATION,
        "approve": RISK_FORBIDDEN_MUTATION,
        "payment": RISK_FORBIDDEN_MUTATION,
        "mutation": RISK_FORBIDDEN_MUTATION,
    }
    return aliases.get(normalized, normalized)


def _normalize_decision(value: Any, *, default: str) -> str:
    normalized = (_normalize_token(value) or default).replace("-", "_").replace(" ", "_")
    if normalized in {POLICY_ALLOWED, POLICY_BLOCKED, POLICY_NEEDS_REVIEW}:
        return normalized
    if normalized in {"allow", "permitted", "permit"}:
        return POLICY_ALLOWED
    if normalized in {"block", "deny", "denied", "forbid", "forbidden"}:
        return POLICY_BLOCKED
    if normalized in {"review", "manual_review", "needsreview"}:
        return POLICY_NEEDS_REVIEW
    return default


@dataclass(frozen=True, slots=True)
class PolicyAction:
    action_id: str | None = None
    action_name: str | None = None
    action_type: str | None = None
    risk_level: str = RISK_SAFE_READ
    template_name: str | None = None
    project_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        risk_level: str | None = None,
        template_name: str | None = None,
        project_name: str | None = None,
    ) -> "PolicyAction":
        if isinstance(value, cls):
            if risk_level or template_name or project_name:
                return cls(
                    action_id=value.action_id,
                    action_name=value.action_name,
                    action_type=value.action_type,
                    risk_level=_normalize_risk_level(risk_level or value.risk_level),
                    template_name=_text(template_name) or value.template_name,
                    project_name=_text(project_name) or value.project_name,
                    extra=value.extra,
                )
            return value
        if isinstance(value, str):
            return cls(
                action_id=_text(value),
                action_name=_text(value),
                risk_level=_normalize_risk_level(risk_level),
                template_name=_text(template_name),
                project_name=_text(project_name),
            )
        data = _to_mapping(value)
        if not data:
            return cls(
                risk_level=_normalize_risk_level(risk_level),
                template_name=_text(template_name),
                project_name=_text(project_name),
            )
        known_keys = {
            "action_id",
            "id",
            "step_id",
            "action_name",
            "name",
            "title",
            "label",
            "action_type",
            "action",
            "type",
            "risk_level",
            "risk",
            "template_name",
            "template",
            "project_name",
            "project",
        }
        return cls(
            action_id=_text(data.get("action_id") or data.get("id") or data.get("step_id")),
            action_name=_text(
                data.get("action_name")
                or data.get("name")
                or data.get("title")
                or data.get("label")
                or data.get("action")
            ),
            action_type=_text(data.get("action_type") or data.get("type") or data.get("action")),
            risk_level=_normalize_risk_level(risk_level or data.get("risk_level") or data.get("risk")),
            template_name=_text(template_name or data.get("template_name") or data.get("template")),
            project_name=_text(project_name or data.get("project_name") or data.get("project")),
            extra={key: item for key, item in data.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(frozen=True, slots=True)
class PolicyAllowRule:
    rule_id: str | None = None
    action_key: str | None = None
    action_id: str | None = None
    action_name: str | None = None
    action_type: str | None = None
    risk_level: str | None = None
    template_name: str | None = None
    project_name: str | None = None
    decision: str = POLICY_ALLOWED
    enabled: bool = True
    note: str | None = None
    source: str | None = None

    @classmethod
    def from_value(cls, value: Any, *, source: str | None = None) -> "PolicyAllowRule":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(action_key=_text(value), source=source)
        data = _to_mapping(value)
        if not data:
            return cls(source=source)
        action_key = _text(data.get("action") or data.get("step") or data.get("value"))
        return cls(
            rule_id=_text(data.get("rule_id") or data.get("id") or data.get("key")),
            action_key=action_key,
            action_id=_text(data.get("action_id") or data.get("step_id")),
            action_name=_text(data.get("action_name") or data.get("name") or data.get("title")),
            action_type=_text(data.get("action_type") or data.get("type")),
            risk_level=_normalize_risk_level(data.get("risk_level") or data.get("risk"))
            if data.get("risk_level") is not None or data.get("risk") is not None
            else None,
            template_name=_text(data.get("template_name") or data.get("template")),
            project_name=_text(data.get("project_name") or data.get("project")),
            decision=_normalize_decision(data.get("decision") or data.get("status"), default=POLICY_ALLOWED),
            enabled=_bool(data.get("enabled")) is not False,
            note=_text(data.get("note") or data.get("reason") or data.get("summary")),
            source=source,
        )

    def matches(self, action: PolicyAction) -> bool:
        if not self.enabled:
            return False
        if self.risk_level and self.risk_level != action.risk_level:
            return False
        if self.template_name and _normalize_token(self.template_name) != _normalize_token(action.template_name):
            return False
        if self.project_name and _normalize_token(self.project_name) != _normalize_token(action.project_name):
            return False
        if self.action_id and _normalize_token(self.action_id) != _normalize_token(action.action_id):
            return False
        if self.action_name and _normalize_token(self.action_name) != _normalize_token(action.action_name):
            return False
        if self.action_type and _normalize_token(self.action_type) != _normalize_token(action.action_type):
            return False
        if self.action_key:
            tokens = {
                token
                for token in (
                    _normalize_token(action.action_id),
                    _normalize_token(action.action_name),
                    _normalize_token(action.action_type),
                )
                if token
            }
            if _normalize_token(self.action_key) not in tokens:
                return False
        return any(
            (
                self.action_key,
                self.action_id,
                self.action_name,
                self.action_type,
                self.risk_level,
                self.template_name,
                self.project_name,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(frozen=True, slots=True)
class PolicyGateConfig:
    risky_submit_default_decision: str = POLICY_BLOCKED
    unknown_risk_default_decision: str = POLICY_NEEDS_REVIEW
    safety_policy: str = SAFETY_POLICY_LOW_RISK_ONLY
    allowed_side_effect_actions: list[str] = field(default_factory=list)
    allow_rules: list[PolicyAllowRule] = field(default_factory=list)
    sources_checked: list[str] = field(default_factory=list)

    @classmethod
    def from_sources(cls, *, config: Any = None, payload: Any = None) -> "PolicyGateConfig":
        risky_submit_default = POLICY_BLOCKED
        safety_policy = SAFETY_POLICY_LOW_RISK_ONLY
        allowed_side_effect_actions: list[str] = []
        allow_rules: list[PolicyAllowRule] = []
        sources_checked: list[str] = []
        for label, source in (("config", config), ("payload", payload)):
            for source_name, mapping in _iter_policy_mappings(source, label):
                sources_checked.append(source_name)
                risky_submit_default = _pick_risky_submit_default(mapping, risky_submit_default)
                safety_policy = _pick_safety_policy(mapping, safety_policy)
                allowed_side_effect_actions.extend(
                    _extract_allowed_side_effect_actions(mapping)
                )
                allow_rules.extend(_extract_allow_rules(mapping, source_name))
        return cls(
            risky_submit_default_decision=risky_submit_default,
            unknown_risk_default_decision=POLICY_NEEDS_REVIEW,
            safety_policy=safety_policy,
            allowed_side_effect_actions=_dedupe_preserve_order(allowed_side_effect_actions),
            allow_rules=allow_rules,
            sources_checked=sources_checked,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allow_rules"] = [rule.to_dict() for rule in self.allow_rules]
        return _compact_dict(payload)


@dataclass(frozen=True, slots=True)
class PolicyGateDecision:
    status: str
    risk_level: str
    action_id: str | None = None
    action_name: str | None = None
    action_type: str | None = None
    template_name: str | None = None
    project_name: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    policy_source: str | None = None
    matched_rule_id: str | None = None
    matched_allowlist: bool = False
    requires_allowlist: bool = False
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        return self.status == POLICY_ALLOWED

    @property
    def is_blocked(self) -> bool:
        return self.status == POLICY_BLOCKED

    @property
    def needs_review(self) -> bool:
        return self.status == POLICY_NEEDS_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


def build_policy_gate_config(*, config: Any = None, payload: Any = None) -> PolicyGateConfig:
    return PolicyGateConfig.from_sources(config=config, payload=payload)


def evaluate_action_policy(
    action: PolicyAction | Mapping[str, Any] | str,
    risk_level: str | None = None,
    *,
    config: Any = None,
    payload: Any = None,
    template_name: str | None = None,
    project_name: str | None = None,
) -> PolicyGateDecision:
    policy_action = PolicyAction.from_value(
        action,
        risk_level=risk_level,
        template_name=template_name,
        project_name=project_name,
    )
    gate_config = build_policy_gate_config(config=config, payload=payload)
    matched_rule = _find_matching_rule(policy_action, gate_config.allow_rules)
    matched_side_effect_action = _matches_allowed_side_effect_action(
        policy_action,
        gate_config.allowed_side_effect_actions,
    )
    matched_rule_id = matched_rule.rule_id if matched_rule else None
    matched_source = matched_rule.source if matched_rule else None
    notes = [note for note in (_text(matched_rule.note) if matched_rule else None,) if note]

    if policy_action.risk_level == RISK_SAFE_READ:
        return _decision_from_action(
            policy_action,
            status=POLICY_ALLOWED,
            reason_code="safe_read_default",
            reason="safe_read actions are allowed by default in stage 2.",
            policy_source=matched_source or "default",
            matched_rule_id=matched_rule_id,
            matched_allowlist=matched_rule is not None,
            notes=notes,
        )

    if policy_action.risk_level == RISK_SAFE_INTERACT:
        return _decision_from_action(
            policy_action,
            status=POLICY_ALLOWED,
            reason_code="safe_interact_default",
            reason="safe_interact actions are allowed by default in stage 2.",
            policy_source=matched_source or "default",
            matched_rule_id=matched_rule_id,
            matched_allowlist=matched_rule is not None,
            notes=notes,
        )

    if policy_action.risk_level == RISK_FORBIDDEN_MUTATION:
        if matched_rule and matched_rule.note and matched_rule.note not in notes:
            notes.append(matched_rule.note)
        if _is_test_env_full_access(gate_config) and (matched_rule or matched_side_effect_action):
            notes.append("测试环境全权限模式已开启，且该副作用动作在本轮 allowlist 内。")
            return _decision_from_action(
                policy_action,
                status=POLICY_ALLOWED,
                reason_code="forbidden_mutation_test_env_allowed",
                reason=(
                    "forbidden_mutation action is allowed only because "
                    "test_env_full_access is enabled and the action is allowlisted."
                ),
                policy_source=matched_source or "allowed_side_effect_actions",
                matched_rule_id=matched_rule_id,
                matched_allowlist=True,
                requires_allowlist=True,
                notes=notes,
                extra={
                    "safety_policy": gate_config.safety_policy,
                    "allowed_side_effect_actions": gate_config.allowed_side_effect_actions,
                    "sources_checked": gate_config.sources_checked,
                    "test_environment_authorization": True,
                },
            )
        return _decision_from_action(
            policy_action,
            status=POLICY_BLOCKED,
            reason_code="forbidden_mutation_blocked",
            reason=(
                "forbidden_mutation actions stay blocked unless test_env_full_access "
                "is enabled and the action is allowlisted."
            ),
            policy_source=matched_source or "policy",
            matched_rule_id=matched_rule_id,
            matched_allowlist=matched_rule is not None,
            requires_allowlist=True,
            notes=notes,
            extra={
                "safety_policy": gate_config.safety_policy,
                "allowed_side_effect_actions": gate_config.allowed_side_effect_actions,
                "sources_checked": gate_config.sources_checked,
            },
        )

    if policy_action.risk_level == RISK_RISKY_SUBMIT:
        if _is_test_env_full_access(gate_config) and matched_side_effect_action and not matched_rule:
            notes.append("测试环境全权限模式已开启，且该副作用动作在本轮 allowlist 内。")
            return _decision_from_action(
                policy_action,
                status=POLICY_ALLOWED,
                reason_code="risky_submit_test_env_allowed",
                reason=(
                    "risky_submit action is allowed because test_env_full_access is "
                    "enabled and the action is allowlisted."
                ),
                policy_source="allowed_side_effect_actions",
                matched_allowlist=True,
                requires_allowlist=True,
                notes=notes,
                extra={
                    "safety_policy": gate_config.safety_policy,
                    "allowed_side_effect_actions": gate_config.allowed_side_effect_actions,
                    "sources_checked": gate_config.sources_checked,
                    "test_environment_authorization": True,
                },
            )
        if matched_rule:
            reason = "risky_submit action was explicitly resolved by project policy allowlist."
            if matched_rule.decision == POLICY_NEEDS_REVIEW:
                reason = "risky_submit action is allowlisted but still requires manual review."
            elif matched_rule.decision == POLICY_BLOCKED:
                reason = "risky_submit action is explicitly blocked by project policy."
            return _decision_from_action(
                policy_action,
                status=matched_rule.decision,
                reason_code=f"risky_submit_{matched_rule.decision}",
                reason=reason,
                policy_source=matched_source or "allowlist",
                matched_rule_id=matched_rule_id,
                matched_allowlist=True,
                requires_allowlist=True,
                notes=notes,
                extra={
                    "safety_policy": gate_config.safety_policy,
                    "allowed_side_effect_actions": gate_config.allowed_side_effect_actions,
                    "sources_checked": gate_config.sources_checked,
                    "test_environment_authorization": (
                        gate_config.safety_policy == SAFETY_POLICY_TEST_ENV_FULL_ACCESS
                        and matched_rule.decision == POLICY_ALLOWED
                    ),
                },
            )
        default_decision = gate_config.risky_submit_default_decision
        if default_decision == POLICY_NEEDS_REVIEW:
            return _decision_from_action(
                policy_action,
                status=POLICY_NEEDS_REVIEW,
                reason_code="risky_submit_unlisted_review",
                reason="risky_submit actions need manual review unless a project whitelist explicitly allows them.",
                policy_source="default",
                requires_allowlist=True,
                extra={"sources_checked": gate_config.sources_checked},
            )
        return _decision_from_action(
            policy_action,
            status=POLICY_BLOCKED,
            reason_code="risky_submit_unlisted_blocked",
            reason="risky_submit actions are blocked by default unless a project whitelist explicitly allows them.",
            policy_source="default",
            requires_allowlist=True,
            extra={"sources_checked": gate_config.sources_checked},
        )

    return _decision_from_action(
        policy_action,
        status=gate_config.unknown_risk_default_decision,
        reason_code="unknown_risk_level",
        reason="Unknown action risk level requires manual review before execution.",
        policy_source="default",
        requires_allowlist=True,
        extra={"sources_checked": gate_config.sources_checked},
    )


def _decision_from_action(
    action: PolicyAction,
    *,
    status: str,
    reason_code: str,
    reason: str,
    policy_source: str | None,
    matched_rule_id: str | None = None,
    matched_allowlist: bool = False,
    requires_allowlist: bool = False,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> PolicyGateDecision:
    return PolicyGateDecision(
        status=status,
        risk_level=action.risk_level,
        action_id=action.action_id,
        action_name=action.action_name,
        action_type=action.action_type,
        template_name=action.template_name,
        project_name=action.project_name,
        reason_code=reason_code,
        reason=reason,
        policy_source=policy_source,
        matched_rule_id=matched_rule_id,
        matched_allowlist=matched_allowlist,
        requires_allowlist=requires_allowlist,
        notes=notes or [],
        extra=extra or {},
    )


def _iter_policy_mappings(value: Any, label: str):
    root = _to_mapping(value)
    if not root:
        return
    yielded: set[str] = set()
    stack: list[tuple[str, dict[str, Any]]] = [(label, root)]
    while stack:
        source_name, mapping = stack.pop(0)
        if source_name in yielded:
            continue
        yielded.add(source_name)
        yield source_name, mapping
        for key in _POLICY_CONTAINER_KEYS:
            nested = _to_mapping(mapping.get(key))
            if nested:
                stack.append((f"{source_name}.{key}", nested))


def _pick_risky_submit_default(mapping: Mapping[str, Any], current: str) -> str:
    for key in _DEFAULT_RISKY_KEYS:
        if key in mapping:
            return _normalize_decision(mapping.get(key), default=current)
    for key in _REVIEW_RISKY_KEYS:
        review = _bool(mapping.get(key))
        if review is True:
            return POLICY_NEEDS_REVIEW
        if review is False:
            return POLICY_BLOCKED
    return current


def _pick_safety_policy(mapping: Mapping[str, Any], current: str) -> str:
    raw_value = mapping.get("safety_policy") or mapping.get("v3_safety_policy")
    normalized = (_normalize_token(raw_value) or current).replace("-", "_").replace(" ", "_")
    if normalized in {"test_env_full_access", "full_access", "test_full_access"}:
        return SAFETY_POLICY_TEST_ENV_FULL_ACCESS
    return SAFETY_POLICY_LOW_RISK_ONLY


def _extract_allowed_side_effect_actions(mapping: Mapping[str, Any]) -> list[str]:
    actions: list[str] = []
    for key in (
        "allowed_side_effect_actions",
        "side_effect_action_allowlist",
        "side_effect_action_whitelist",
    ):
        actions.extend(_string_list(mapping.get(key)))
    return actions


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        normalized = _normalize_token(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(value)
    return results


def _is_test_env_full_access(gate_config: PolicyGateConfig) -> bool:
    return gate_config.safety_policy == SAFETY_POLICY_TEST_ENV_FULL_ACCESS


def _matches_allowed_side_effect_action(
    action: PolicyAction,
    allowed_actions: list[str],
) -> bool:
    tokens = {
        token
        for token in (
            _normalize_token(action.action_id),
            _normalize_token(action.action_name),
            _normalize_token(action.action_type),
        )
        if token
    }
    return any(_normalize_token(item) in tokens for item in allowed_actions)


def _extract_allow_rules(mapping: Mapping[str, Any], source_name: str) -> list[PolicyAllowRule]:
    rules: list[PolicyAllowRule] = []
    for key in _ALLOWLIST_KEYS:
        if key not in mapping:
            continue
        source = f"{source_name}.{key}"
        for item in _iter_rule_values(mapping.get(key)):
            rule = PolicyAllowRule.from_value(item, source=source)
            if rule.matches(PolicyAction()):
                continue
            if any(
                (
                    rule.action_key,
                    rule.action_id,
                    rule.action_name,
                    rule.action_type,
                    rule.risk_level,
                    rule.template_name,
                    rule.project_name,
                )
            ):
                rules.append(rule)
    return rules


def _iter_rule_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    mapping = _to_mapping(value)
    if not mapping:
        return [value]
    collected: list[Any] = []
    for key in _ENTRY_COLLECTION_KEYS:
        nested = mapping.get(key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            collected.extend(list(nested))
    if collected:
        return collected
    return [mapping]


def _find_matching_rule(action: PolicyAction, rules: list[PolicyAllowRule]) -> PolicyAllowRule | None:
    for rule in rules:
        if rule.matches(action):
            return rule
    return None


__all__ = [
    "POLICY_ALLOWED",
    "POLICY_BLOCKED",
    "POLICY_NEEDS_REVIEW",
    "SAFETY_POLICY_LOW_RISK_ONLY",
    "SAFETY_POLICY_TEST_ENV_FULL_ACCESS",
    "RISK_SAFE_READ",
    "RISK_SAFE_INTERACT",
    "RISK_RISKY_SUBMIT",
    "RISK_FORBIDDEN_MUTATION",
    "PolicyAction",
    "PolicyAllowRule",
    "PolicyGateConfig",
    "PolicyGateDecision",
    "build_policy_gate_config",
    "evaluate_action_policy",
]
