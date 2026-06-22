from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from .models import Fact, ReportItem, SectionBlock

_MANUAL_REVIEW_STATUSES = {"needs_review", "manual_review", "pending_review"}
_ALLOWED_STATUSES = {"allowed", "eligible", "selected", "routed", "ok"}
_BLOCKED_STATUSES = {"blocked", "incompatible", "denied"}
_DEGRADED_STATUSES = {"degraded", "fallback", "downgraded"}

_CAPABILITY_KEYS = (
    "capability_decision",
    "capability_preflight",
    "capability_gate_decision",
    "capability_preflight_decision",
)
_ROUTE_KEYS = (
    "routing_decision",
    "route_decision",
    "model_routing",
    "capability_routing",
)
_POLICY_KEYS = (
    "policy_decision",
    "run_policy_decision",
    "policy_gate_decision",
    "runtime_policy_decision",
)


@dataclass(slots=True)
class RoutingPayloadBundle:
    capability_decision: dict[str, Any] = field(default_factory=dict)
    route_decision: dict[str, Any] = field(default_factory=dict)
    policy_decision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RoutingExplanation:
    status: str = "unknown"
    headline: str | None = None
    summary: str | None = None
    model_name: str | None = None
    profile_name: str | None = None
    route_status: str | None = None
    route_reason: str | None = None
    capability_status: str | None = None
    capability_reason: str | None = None
    policy_status: str | None = None
    policy_reason: str | None = None
    requested_mode: str | None = None
    selected_mode: str | None = None
    assigned_role: str | None = None
    fallback_mode: str | None = None
    fallback_role: str | None = None
    browser_use_structured_available: bool | None = None
    browser_use_wrapper: str | None = None
    required_tags: list[str] = field(default_factory=list)
    missing_tags: list[str] = field(default_factory=list)
    capability_tags: dict[str, bool] = field(default_factory=dict)
    risk_level: str | None = None
    action_id: str | None = None
    action_name: str | None = None
    policy_source: str | None = None
    matched_allowlist: bool | None = None
    requires_allowlist: bool | None = None
    manual_review_required: bool = False
    degraded: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_routing_payloads(
    value: Any | None = None,
    *,
    capability_decision: Any | None = None,
    route_decision: Any | None = None,
    policy_decision: Any | None = None,
) -> RoutingPayloadBundle:
    containers = _candidate_containers(value)
    return RoutingPayloadBundle(
        capability_decision=_mapping_value(capability_decision) or _first_mapping(containers, _CAPABILITY_KEYS),
        route_decision=_mapping_value(route_decision) or _first_mapping(containers, _ROUTE_KEYS),
        policy_decision=_mapping_value(policy_decision) or _first_mapping(containers, _POLICY_KEYS),
    )


def build_routing_explanation(
    value: Any | None = None,
    *,
    capability_decision: Any | None = None,
    route_decision: Any | None = None,
    policy_decision: Any | None = None,
) -> RoutingExplanation:
    payloads = extract_routing_payloads(
        value,
        capability_decision=capability_decision,
        route_decision=route_decision,
        policy_decision=policy_decision,
    )

    capability = payloads.capability_decision
    route = payloads.route_decision
    policy = payloads.policy_decision
    snapshot = _mapping_value(capability.get("snapshot"))

    capability_status = _status_value(capability.get("status"))
    route_status = _status_value(route.get("status") or route.get("routing_status"))
    policy_status = _status_value(policy.get("status"))

    requested_mode = _first_text(
        route.get("requested_mode"),
        route.get("target_mode"),
        route.get("mode"),
        capability.get("mode"),
    )
    selected_mode = _first_text(
        route.get("selected_mode"),
        route.get("assigned_mode"),
        route.get("resolved_mode"),
        requested_mode,
    )
    assigned_role = _first_text(
        route.get("assigned_role"),
        route.get("recommended_role"),
        route.get("route_role"),
    )
    fallback_mode = _first_text(
        route.get("fallback_mode"),
        route.get("degraded_to_mode"),
        route.get("fallback_target_mode"),
    )
    fallback_role = _first_text(
        route.get("fallback_role"),
        route.get("degraded_to_role"),
        route.get("fallback_target_role"),
    )

    capability_tags = {
        key: bool(value)
        for key, value in _mapping_value(capability.get("capability_tags")).items()
    }
    required_tags = _string_list(capability.get("required_tags"))
    missing_tags = _string_list(capability.get("missing_tags"))

    browser_use_wrapper = _detect_browser_use_wrapper(
        route=route,
        requested_mode=requested_mode,
        selected_mode=selected_mode,
        capability_tags=capability_tags,
    )
    browser_use_structured_available = _resolve_browser_use_availability(
        route=route,
        requested_mode=requested_mode,
        selected_mode=selected_mode,
        capability_status=capability_status,
        capability_tags=capability_tags,
        browser_use_wrapper=browser_use_wrapper,
    )

    degraded = _is_degraded(
        route_status=route_status,
        requested_mode=requested_mode,
        selected_mode=selected_mode,
        fallback_mode=fallback_mode,
        fallback_role=fallback_role,
    )
    manual_review_required = _needs_manual_review(route=route, policy=policy, route_status=route_status, policy_status=policy_status)
    status = _overall_status(
        capability_status=capability_status,
        route_status=route_status,
        policy_status=policy_status,
        degraded=degraded,
        manual_review_required=manual_review_required,
    )

    explanation = RoutingExplanation(
        status=status,
        model_name=_first_text(
            route.get("model_name"),
            route.get("model"),
            capability.get("model"),
            snapshot.get("model"),
        ),
        profile_name=_first_text(route.get("profile_name"), capability.get("profile_name")),
        route_status=route_status,
        route_reason=_first_text(route.get("reason"), route.get("summary"), route.get("route_reason")),
        capability_status=capability_status,
        capability_reason=_first_text(capability.get("reason"), capability.get("summary")),
        policy_status=policy_status,
        policy_reason=_first_text(policy.get("reason"), policy.get("summary")),
        requested_mode=requested_mode,
        selected_mode=selected_mode,
        assigned_role=assigned_role,
        fallback_mode=fallback_mode,
        fallback_role=fallback_role,
        browser_use_structured_available=browser_use_structured_available,
        browser_use_wrapper=browser_use_wrapper,
        required_tags=required_tags,
        missing_tags=missing_tags,
        capability_tags=capability_tags,
        risk_level=_text_value(policy.get("risk_level")),
        action_id=_first_text(policy.get("action_id"), policy.get("id")),
        action_name=_first_text(policy.get("action_name"), policy.get("name"), policy.get("title")),
        policy_source=_text_value(policy.get("policy_source")),
        matched_allowlist=_bool_value(policy.get("matched_allowlist")),
        requires_allowlist=_bool_value(policy.get("requires_allowlist")),
        manual_review_required=manual_review_required,
        degraded=degraded,
    )
    explanation.headline = _routing_headline(explanation)
    explanation.notes = _build_routing_notes(explanation, payloads)
    explanation.summary = _routing_summary(explanation)
    return explanation


def build_routing_facts(
    value: RoutingExplanation | Any,
    **kwargs: Any,
) -> list[Fact]:
    explanation = value if isinstance(value, RoutingExplanation) else build_routing_explanation(value, **kwargs)
    facts: list[Fact] = []
    _append_fact(facts, "routing_status", explanation.status)
    _append_fact(facts, "model_name", explanation.model_name)
    _append_fact(facts, "profile_name", explanation.profile_name)
    _append_fact(facts, "assigned_role", explanation.assigned_role)
    _append_fact(facts, "requested_mode", explanation.requested_mode)
    _append_fact(facts, "selected_mode", explanation.selected_mode)
    _append_fact(facts, "fallback_mode", explanation.fallback_mode)
    _append_fact(facts, "fallback_role", explanation.fallback_role)
    _append_fact(facts, "browser_use_structured_available", explanation.browser_use_structured_available)
    _append_fact(facts, "browser_use_wrapper", explanation.browser_use_wrapper)
    _append_fact(facts, "capability_status", explanation.capability_status)
    _append_fact(facts, "required_tags", explanation.required_tags)
    _append_fact(facts, "missing_tags", explanation.missing_tags)
    _append_fact(facts, "capability_tags", sorted(key for key, enabled in explanation.capability_tags.items() if enabled))
    _append_fact(facts, "policy_status", explanation.policy_status)
    _append_fact(facts, "risk_level", explanation.risk_level)
    _append_fact(facts, "requires_allowlist", explanation.requires_allowlist)
    _append_fact(facts, "matched_allowlist", explanation.matched_allowlist)
    _append_fact(facts, "policy_source", explanation.policy_source)
    _append_fact(facts, "manual_review_required", explanation.manual_review_required)
    _append_fact(facts, "degraded", explanation.degraded)
    return facts


def build_routing_items(
    value: RoutingExplanation | Any,
    **kwargs: Any,
) -> list[ReportItem]:
    explanation = value if isinstance(value, RoutingExplanation) else build_routing_explanation(value, **kwargs)
    items: list[ReportItem] = []

    routing_facts: list[Fact] = []
    _append_fact(routing_facts, "requested_mode", explanation.requested_mode)
    _append_fact(routing_facts, "selected_mode", explanation.selected_mode)
    _append_fact(routing_facts, "assigned_role", explanation.assigned_role)
    _append_fact(routing_facts, "fallback_mode", explanation.fallback_mode)
    _append_fact(routing_facts, "fallback_role", explanation.fallback_role)
    _append_fact(routing_facts, "browser_use_structured_available", explanation.browser_use_structured_available)
    _append_fact(routing_facts, "browser_use_wrapper", explanation.browser_use_wrapper)
    _append_fact(routing_facts, "required_tags", explanation.required_tags)
    _append_fact(routing_facts, "missing_tags", explanation.missing_tags)
    _append_fact(
        routing_facts,
        "capability_tags",
        sorted(key for key, enabled in explanation.capability_tags.items() if enabled),
    )
    items.append(
        ReportItem(
            item_id="capability-routing",
            name=explanation.model_name or explanation.profile_name or "Capability routing",
            status=explanation.route_status or explanation.capability_status or explanation.status,
            summary=explanation.route_reason or explanation.capability_reason,
            facts=routing_facts,
            notes=_routing_item_notes(explanation),
            extra={
                "status": explanation.status,
                "capability_status": explanation.capability_status,
                "route_status": explanation.route_status,
                "browser_use_structured_available": explanation.browser_use_structured_available,
            },
        )
    )

    if explanation.policy_status or explanation.action_name or explanation.risk_level:
        policy_facts: list[Fact] = []
        _append_fact(policy_facts, "action_id", explanation.action_id)
        _append_fact(policy_facts, "risk_level", explanation.risk_level)
        _append_fact(policy_facts, "policy_source", explanation.policy_source)
        _append_fact(policy_facts, "requires_allowlist", explanation.requires_allowlist)
        _append_fact(policy_facts, "matched_allowlist", explanation.matched_allowlist)
        items.append(
            ReportItem(
                item_id=explanation.action_id or "run-policy",
                name=explanation.action_name or "Run policy",
                status=explanation.policy_status,
                summary=explanation.policy_reason,
                facts=policy_facts,
                notes=_policy_item_notes(explanation),
                extra={
                    "status": explanation.policy_status,
                    "manual_review_required": explanation.manual_review_required,
                },
            )
        )
    return items


def build_routing_section(
    value: RoutingExplanation | Any,
    *,
    title: str = "Routing Explanation",
    **kwargs: Any,
) -> SectionBlock:
    explanation = value if isinstance(value, RoutingExplanation) else build_routing_explanation(value, **kwargs)
    return SectionBlock(
        title=title,
        summary=explanation.summary or explanation.headline,
        facts=build_routing_facts(explanation),
        items=build_routing_items(explanation),
        notes=explanation.notes,
        extra={
            "routing_status": explanation.status,
            "manual_review_required": explanation.manual_review_required,
            "browser_use_structured_available": explanation.browser_use_structured_available,
            "browser_use_wrapper": explanation.browser_use_wrapper,
            "degraded": explanation.degraded,
        },
    )


def _routing_headline(explanation: RoutingExplanation) -> str:
    if explanation.status == "blocked":
        if explanation.policy_status == "blocked":
            return "Routing blocked by project policy"
        return "Routing blocked by capability gate"
    if explanation.status == "needs_review":
        return "Routing requires manual review"
    if explanation.degraded:
        if explanation.selected_mode and explanation.requested_mode and explanation.selected_mode != explanation.requested_mode:
            return f"Model downgraded from {explanation.requested_mode} to {explanation.selected_mode}"
        if explanation.fallback_mode:
            return f"Model downgraded to {explanation.fallback_mode}"
        return "Model routed to a fallback path"
    if explanation.browser_use_structured_available and explanation.browser_use_wrapper:
        return f"Browser Use structured path available via {explanation.browser_use_wrapper}"
    if explanation.assigned_role:
        return f"Model assigned to {explanation.assigned_role}"
    return "Routing context available"


def _routing_summary(explanation: RoutingExplanation) -> str | None:
    parts: list[str] = []
    if explanation.headline:
        parts.append(_ensure_period(explanation.headline))
    if explanation.route_reason:
        parts.append(_ensure_period(explanation.route_reason))
    elif explanation.capability_reason and explanation.status in {"blocked", "degraded"}:
        parts.append(_ensure_period(explanation.capability_reason))
    if explanation.policy_reason:
        parts.append(_ensure_period(explanation.policy_reason))

    browser_use_note = _browser_use_note(explanation)
    if browser_use_note:
        parts.append(_ensure_period(browser_use_note))
    return " ".join(parts) or None


def _build_routing_notes(
    explanation: RoutingExplanation,
    payloads: RoutingPayloadBundle,
) -> list[str]:
    notes: list[str] = []
    browser_use_note = _browser_use_note(explanation)
    if browser_use_note:
        notes.append(browser_use_note)

    if explanation.missing_tags:
        notes.append("Missing capability tags: " + ", ".join(explanation.missing_tags))
    if explanation.degraded:
        if explanation.requested_mode and explanation.selected_mode and explanation.requested_mode != explanation.selected_mode:
            notes.append(
                f"Routing degraded from {explanation.requested_mode} to {explanation.selected_mode}."
            )
        elif explanation.fallback_mode:
            notes.append(f"Routing degraded to {explanation.fallback_mode}.")
    if explanation.policy_status == "blocked" and explanation.requires_allowlist:
        notes.append("Risky action remains blocked until a project allowlist resolves it.")
    if explanation.policy_status == "needs_review":
        notes.append("Manual review is required before the risky action can run.")
    if explanation.policy_status == "allowed" and explanation.matched_allowlist:
        notes.append("Project policy explicitly allowlisted this action.")

    notes.extend(_string_list(payloads.capability_decision.get("notes"))[:2])
    notes.extend(_string_list(payloads.route_decision.get("notes"))[:2])
    notes.extend(_string_list(payloads.policy_decision.get("notes"))[:2])
    return _unique_texts(note for note in notes if note)


def _routing_item_notes(explanation: RoutingExplanation) -> list[str]:
    notes: list[str] = []
    browser_use_note = _browser_use_note(explanation)
    if browser_use_note:
        notes.append(browser_use_note)
    if explanation.capability_reason and explanation.capability_reason != explanation.route_reason:
        notes.append(explanation.capability_reason)
    if explanation.missing_tags:
        notes.append("Missing tags: " + ", ".join(explanation.missing_tags))
    if explanation.degraded and explanation.fallback_mode:
        notes.append(f"Fallback mode: {explanation.fallback_mode}")
    return _unique_texts(notes)


def _policy_item_notes(explanation: RoutingExplanation) -> list[str]:
    notes: list[str] = []
    if explanation.policy_status == "blocked" and explanation.requires_allowlist:
        notes.append("A project allowlist entry is required before this action can run.")
    if explanation.policy_status == "needs_review":
        notes.append("Review is required before auto-continuing.")
    if explanation.policy_status == "allowed" and explanation.matched_allowlist:
        notes.append("This action matched a project allowlist rule.")
    return _unique_texts(notes)


def _browser_use_note(explanation: RoutingExplanation) -> str | None:
    if not explanation.browser_use_wrapper:
        return None
    if explanation.browser_use_structured_available is True:
        return f"Browser Use structured path is available via {explanation.browser_use_wrapper}"
    if explanation.browser_use_structured_available is False:
        return f"Browser Use structured path is unavailable for {explanation.browser_use_wrapper}"
    return None


def _needs_manual_review(
    *,
    route: Mapping[str, Any],
    policy: Mapping[str, Any],
    route_status: str | None,
    policy_status: str | None,
) -> bool:
    if route_status in _MANUAL_REVIEW_STATUSES or policy_status in _MANUAL_REVIEW_STATUSES:
        return True
    if _bool_value(route.get("requires_human_review")) is True:
        return True
    if _bool_value(policy.get("requires_human_review")) is True:
        return True
    return False


def _overall_status(
    *,
    capability_status: str | None,
    route_status: str | None,
    policy_status: str | None,
    degraded: bool,
    manual_review_required: bool,
) -> str:
    if policy_status == "blocked":
        return "blocked"
    if manual_review_required:
        return "needs_review"
    if route_status == "blocked":
        return "blocked"
    if degraded:
        return "degraded"
    if route_status in _ALLOWED_STATUSES:
        return "allowed"
    if capability_status == "blocked":
        return "blocked"
    if policy_status == "allowed":
        return "allowed"
    if capability_status == "allowed":
        return "allowed"
    return route_status or policy_status or capability_status or "unknown"


def _is_degraded(
    *,
    route_status: str | None,
    requested_mode: str | None,
    selected_mode: str | None,
    fallback_mode: str | None,
    fallback_role: str | None,
) -> bool:
    if route_status in _DEGRADED_STATUSES:
        return True
    if fallback_mode or fallback_role:
        return True
    return bool(requested_mode and selected_mode and requested_mode != selected_mode)


def _resolve_browser_use_availability(
    *,
    route: Mapping[str, Any],
    requested_mode: str | None,
    selected_mode: str | None,
    capability_status: str | None,
    capability_tags: Mapping[str, bool],
    browser_use_wrapper: str | None,
) -> bool | None:
    explicit = _bool_value(route.get("browser_use_structured_eligible"))
    if explicit is not None:
        return explicit

    wrapper = browser_use_wrapper or _first_text(requested_mode, selected_mode)
    openai_eligible = capability_tags.get("browser_use_chatopenai_structured") is True
    deepseek_eligible = capability_tags.get("browser_use_chatdeepseek_structured") is True
    if wrapper == "browser_use_chatopenai_structured":
        return openai_eligible
    if wrapper == "browser_use_chatdeepseek_structured":
        return deepseek_eligible
    if wrapper and "browser_use" in wrapper:
        return openai_eligible or deepseek_eligible or capability_status == "allowed"
    return None


def _detect_browser_use_wrapper(
    *,
    route: Mapping[str, Any],
    requested_mode: str | None,
    selected_mode: str | None,
    capability_tags: Mapping[str, bool],
) -> str | None:
    explicit = _first_text(
        route.get("browser_use_wrapper"),
        route.get("structured_wrapper"),
        route.get("wrapper"),
    )
    if explicit:
        return explicit
    for value in (selected_mode, requested_mode):
        text = _text_value(value)
        if text and "browser_use" in text:
            return text
    if capability_tags.get("browser_use_chatopenai_structured") is True:
        return "browser_use_chatopenai_structured"
    if capability_tags.get("browser_use_chatdeepseek_structured") is True:
        return "browser_use_chatdeepseek_structured"
    return None


def _append_fact(facts: list[Fact], label: str, value: Any) -> None:
    if value is None or value == "":
        return
    if isinstance(value, (list, dict)) and not value:
        return
    facts.append(Fact(label=label, value=value))


def _candidate_containers(value: Any | None) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    root = _mapping_value(value)
    if root:
        containers.append(root)
        summary = _mapping_value(root.get("summary"))
        if summary:
            containers.append(summary)
            summary_extra = _mapping_value(summary.get("extra"))
            if summary_extra:
                containers.append(summary_extra)
        extra = _mapping_value(root.get("extra"))
        if extra:
            containers.append(extra)
    return containers


def _first_mapping(containers: list[dict[str, Any]], keys: Sequence[str]) -> dict[str, Any]:
    for container in containers:
        for key in keys:
            value = _mapping_value(container.get(key))
            if value:
                return value
    return {}


def _mapping_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _status_value(value: Any) -> str | None:
    text = _text_value(value)
    if not text:
        return None
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def _bool_value(value: Any) -> bool | None:
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
    if isinstance(value, Mapping) or is_dataclass(value):
        text = _text_value(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        items: list[str] = []
        for item in value:
            text = _text_value(item)
            if text:
                items.append(text)
        return items
    text = _text_value(value)
    return [text] if text else []


def _unique_texts(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _text_value(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text_value(value)
        if text:
            return text
    return None


def _ensure_period(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."

