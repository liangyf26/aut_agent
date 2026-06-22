from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from .models import Fact, ReportItem, SectionBlock

MANUAL_REVIEW_DECISION_STATUSES = {"needs_review", "manual_review", "pending_review"}

_EXECUTION_MODE_PHRASES = {
    "capture_and_retry": "capture network evidence and retry",
    "fix_precondition_then_rerun": "fix preconditions and rerun",
    "guard_and_retry": "add stability guards and retry",
    "inspect_backend_precondition": "inspect backend preconditions before retry",
    "inspect_visible_errors": "inspect visible validation errors before retry",
    "refresh_locator_and_rerun": "refresh UI locators and rerun",
    "repair_input_then_rerun": "repair input data and rerun",
    "resume_detected_branch": "resume the detected workflow branch",
    "tighten_assertion_then_rerun": "tighten verification assertions before retry",
}


@dataclass(slots=True)
class DecisionPayloadBundle:
    stop_conditions: dict[str, Any] = field(default_factory=dict)
    next_round_decision: dict[str, Any] = field(default_factory=dict)
    retry_plan: dict[str, Any] = field(default_factory=dict)
    round_input: dict[str, Any] = field(default_factory=dict)
    execution_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DecisionActionSummary:
    action_id: str | None = None
    cluster_id: str | None = None
    title: str | None = None
    stage: str | None = None
    owner: str | None = None
    priority: str | None = None
    strategy: str | None = None
    reason: str | None = None
    expected_outcome: str | None = None
    execution_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DecisionExplanation:
    status: str = "unknown"
    headline: str | None = None
    summary: str | None = None
    primary_reason: str | None = None
    stop_status: str | None = None
    should_stop: bool | None = None
    stop_reason: str | None = None
    next_round_status: str | None = None
    should_start_next_round: bool | None = None
    next_round: int | None = None
    target_stage: str | None = None
    remaining_attempt_budget: int | None = None
    retry_plan_status: str | None = None
    retry_goal: str | None = None
    manual_review_required: bool = False
    scheduled_action_count: int = 0
    scheduled_cluster_count: int = 0
    triggered_stop_conditions: list[str] = field(default_factory=list)
    execution_hints: dict[str, Any] = field(default_factory=dict)
    execution_hint_texts: list[str] = field(default_factory=list)
    actions: list[DecisionActionSummary] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["actions"] = [action.to_dict() for action in self.actions]
        return payload


def extract_decision_payloads(
    value: Any | None = None,
    *,
    stop_conditions: Any | None = None,
    next_round_decision: Any | None = None,
    retry_plan: Any | None = None,
    round_input: Any | None = None,
    execution_hints: Any | None = None,
) -> DecisionPayloadBundle:
    containers = _candidate_containers(value)

    extracted_stop = _first_mapping(containers, "stop_conditions")
    extracted_next = _first_mapping(containers, "next_round_decision")
    extracted_retry = _first_mapping(containers, "retry_plan")
    extracted_round_input = _first_mapping(containers, "round_input")

    explicit_hints = _mapping_value(execution_hints)
    extracted_hints = (
        explicit_hints
        or _first_mapping(containers, "applied_execution_hints")
        or _first_mapping(containers, "execution_hints")
        or _mapping_value(extracted_round_input.get("execution_hints"))
    )

    return DecisionPayloadBundle(
        stop_conditions=_mapping_value(stop_conditions) or extracted_stop,
        next_round_decision=_mapping_value(next_round_decision) or extracted_next,
        retry_plan=_mapping_value(retry_plan) or extracted_retry,
        round_input=_mapping_value(round_input) or extracted_round_input,
        execution_hints=extracted_hints,
    )


def build_decision_explanation(
    value: Any | None = None,
    *,
    stop_conditions: Any | None = None,
    next_round_decision: Any | None = None,
    retry_plan: Any | None = None,
    round_input: Any | None = None,
    execution_hints: Any | None = None,
) -> DecisionExplanation:
    payloads = extract_decision_payloads(
        value,
        stop_conditions=stop_conditions,
        next_round_decision=next_round_decision,
        retry_plan=retry_plan,
        round_input=round_input,
        execution_hints=execution_hints,
    )

    action_payloads = _mapping_list(payloads.retry_plan.get("actions"))
    actions = [_coerce_action_summary(action) for action in action_payloads]
    merged_execution_hints = _collect_execution_hints(
        payloads.execution_hints,
        payloads.round_input,
        action_payloads,
    )
    execution_hint_texts = _unique_texts(
        _hint_phrase(key, value)
        for key, value in merged_execution_hints.items()
        if _hint_phrase(key, value)
    )

    stop_status = _text_value(payloads.stop_conditions.get("status"))
    should_stop = _bool_value(payloads.stop_conditions.get("should_stop"))
    stop_reason = _first_text(
        payloads.stop_conditions.get("primary_reason"),
        payloads.next_round_decision.get("stop_reason"),
    )
    next_status = _text_value(payloads.next_round_decision.get("status"))
    should_start_next_round = _bool_value(payloads.next_round_decision.get("should_start_next_round"))
    next_round = _int_value(payloads.next_round_decision.get("next_round"))
    target_stage = _first_text(
        payloads.next_round_decision.get("target_stage"),
        payloads.round_input.get("target_stage"),
        merged_execution_hints.get("focus_stage"),
    )
    primary_reason = _first_text(
        payloads.next_round_decision.get("primary_reason"),
        payloads.stop_conditions.get("primary_reason"),
    )
    triggered_stop_conditions = _unique_texts(
        _string_list(payloads.stop_conditions.get("triggered_conditions"))
        + _string_list(payloads.next_round_decision.get("triggered_stop_conditions"))
    )

    manual_review_required = _needs_manual_review(
        stop_status=stop_status,
        next_round_status=next_status,
        stop_conditions=payloads.stop_conditions,
        next_round_decision=payloads.next_round_decision,
        execution_hints=merged_execution_hints,
    )

    status = _overall_decision_status(
        should_stop=should_stop,
        next_round_status=next_status,
        stop_status=stop_status,
        should_start_next_round=should_start_next_round,
        manual_review_required=manual_review_required,
        retry_plan_status=_text_value(payloads.retry_plan.get("status")),
        action_count=len(actions),
    )
    headline = _decision_headline(
        status=status,
        next_round=next_round,
        target_stage=target_stage,
        stop_reason=stop_reason,
    )

    scheduled_cluster_count = len(
        _unique_texts(
            _string_list(payloads.next_round_decision.get("scheduled_cluster_ids"))
            + [action.cluster_id for action in actions if action.cluster_id]
        )
    )
    notes = _build_decision_notes(
        payloads=payloads,
        status=status,
        manual_review_required=manual_review_required,
        execution_hint_texts=execution_hint_texts,
        action_count=len(actions),
        triggered_stop_conditions=triggered_stop_conditions,
    )

    explanation = DecisionExplanation(
        status=status,
        headline=headline,
        primary_reason=primary_reason,
        stop_status=stop_status,
        should_stop=should_stop,
        stop_reason=stop_reason,
        next_round_status=next_status,
        should_start_next_round=should_start_next_round,
        next_round=next_round,
        target_stage=target_stage,
        remaining_attempt_budget=_int_value(payloads.next_round_decision.get("remaining_attempt_budget")),
        retry_plan_status=_text_value(payloads.retry_plan.get("status")),
        retry_goal=_text_value(payloads.retry_plan.get("goal")),
        manual_review_required=manual_review_required,
        scheduled_action_count=len(actions),
        scheduled_cluster_count=scheduled_cluster_count,
        triggered_stop_conditions=triggered_stop_conditions,
        execution_hints=merged_execution_hints,
        execution_hint_texts=execution_hint_texts,
        actions=actions,
        notes=notes,
    )
    explanation.summary = _decision_summary(explanation)
    return explanation


def build_decision_facts(
    value: DecisionExplanation | Any,
    **kwargs: Any,
) -> list[Fact]:
    explanation = value if isinstance(value, DecisionExplanation) else build_decision_explanation(value, **kwargs)
    facts: list[Fact] = []
    _append_fact(facts, "decision_status", explanation.status)
    _append_fact(facts, "stop_status", explanation.stop_status)
    _append_fact(facts, "should_stop", explanation.should_stop)
    _append_fact(facts, "stop_reason", explanation.stop_reason)
    _append_fact(facts, "next_round_status", explanation.next_round_status)
    _append_fact(facts, "should_start_next_round", explanation.should_start_next_round)
    _append_fact(facts, "next_round", explanation.next_round)
    _append_fact(facts, "target_stage", explanation.target_stage)
    _append_fact(facts, "remaining_attempt_budget", explanation.remaining_attempt_budget)
    _append_fact(facts, "retry_plan_status", explanation.retry_plan_status)
    _append_fact(facts, "scheduled_action_count", explanation.scheduled_action_count)
    _append_fact(facts, "scheduled_cluster_count", explanation.scheduled_cluster_count)
    _append_fact(facts, "manual_review_required", explanation.manual_review_required)
    _append_fact(facts, "triggered_stop_conditions", explanation.triggered_stop_conditions)
    _append_fact(facts, "execution_hints", explanation.execution_hints)
    return facts


def build_decision_items(
    value: DecisionExplanation | Any,
    *,
    limit: int | None = None,
    **kwargs: Any,
) -> list[ReportItem]:
    explanation = value if isinstance(value, DecisionExplanation) else build_decision_explanation(value, **kwargs)
    items: list[ReportItem] = []
    action_summaries = explanation.actions[:limit] if limit is not None else explanation.actions
    for action in action_summaries:
        facts: list[Fact] = []
        _append_fact(facts, "cluster_id", action.cluster_id)
        _append_fact(facts, "stage", action.stage)
        _append_fact(facts, "priority", action.priority)
        _append_fact(facts, "expected_outcome", action.expected_outcome)
        _append_fact(facts, "execution_hints", action.execution_hints)
        items.append(
            ReportItem(
                item_id=action.action_id,
                name=action.title or action.action_id or "planned action",
                status=action.strategy or explanation.status,
                summary=action.reason or action.expected_outcome,
                owner=action.owner,
                facts=facts,
                notes=_unique_texts(
                    [
                        _hint_phrase(key, value)
                        for key, value in action.execution_hints.items()
                        if _hint_phrase(key, value)
                    ]
                ),
                extra={"execution_hints": action.execution_hints},
            )
        )
    return items


def build_decision_section(
    value: DecisionExplanation | Any,
    *,
    title: str = "Decision Explanation",
    item_limit: int | None = 5,
    **kwargs: Any,
) -> SectionBlock:
    explanation = value if isinstance(value, DecisionExplanation) else build_decision_explanation(value, **kwargs)
    return SectionBlock(
        title=title,
        summary=explanation.summary or explanation.headline,
        facts=build_decision_facts(explanation),
        items=build_decision_items(explanation, limit=item_limit),
        notes=explanation.notes,
        extra={
            "headline": explanation.headline,
            "decision_status": explanation.status,
            "primary_reason": explanation.primary_reason,
            "manual_review_required": explanation.manual_review_required,
            "triggered_stop_conditions": explanation.triggered_stop_conditions,
            "execution_hints": explanation.execution_hints,
        },
    )


def _coerce_action_summary(value: Any) -> DecisionActionSummary:
    data = _mapping_value(value)
    return DecisionActionSummary(
        action_id=_text_value(data.get("action_id")),
        cluster_id=_text_value(data.get("cluster_id")),
        title=_text_value(data.get("title")),
        stage=_text_value(data.get("stage")),
        owner=_text_value(data.get("owner")),
        priority=_text_value(data.get("priority")),
        strategy=_text_value(data.get("strategy")),
        reason=_text_value(data.get("reason")),
        expected_outcome=_text_value(data.get("expected_outcome")),
        execution_hints=_mapping_value(data.get("execution_hints")),
    )


def _collect_execution_hints(
    extracted_hints: Mapping[str, Any],
    round_input: Mapping[str, Any],
    action_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    sources = [
        _mapping_value(round_input.get("execution_hints")),
        _mapping_value(extracted_hints),
    ]
    sources.extend(_mapping_value(action.get("execution_hints")) for action in action_payloads)
    for hints in sources:
        _merge_mapping(merged, hints)
    return merged


def _needs_manual_review(
    *,
    stop_status: str | None,
    next_round_status: str | None,
    stop_conditions: Mapping[str, Any],
    next_round_decision: Mapping[str, Any],
    execution_hints: Mapping[str, Any],
) -> bool:
    if stop_status in MANUAL_REVIEW_DECISION_STATUSES or next_round_status in MANUAL_REVIEW_DECISION_STATUSES:
        return True
    if _bool_value(execution_hints.get("requires_human_review")) is True:
        return True
    for condition in _mapping_list(stop_conditions.get("conditions")):
        condition_type = _text_value(condition.get("condition_type"))
        condition_status = _text_value(condition.get("status"))
        if condition_status == "manual_review_needed":
            return True
        if condition_type == "manual_takeover" and condition_status in {"hit", "manual_review_needed"}:
            return True
    if _text_value(next_round_decision.get("status")) in MANUAL_REVIEW_DECISION_STATUSES:
        return True
    return False


def _overall_decision_status(
    *,
    should_stop: bool | None,
    next_round_status: str | None,
    stop_status: str | None,
    should_start_next_round: bool | None,
    manual_review_required: bool,
    retry_plan_status: str | None,
    action_count: int,
) -> str:
    if should_stop is True or next_round_status == "stopped":
        return "stopped"
    if manual_review_required:
        return "needs_review"
    if next_round_status:
        return next_round_status
    if should_start_next_round is True:
        return "scheduled"
    if should_start_next_round is False and retry_plan_status == "no_retry_needed":
        return "no_retry_needed"
    if stop_status == "continue" and action_count:
        return "planned"
    if stop_status:
        return stop_status
    if retry_plan_status:
        return retry_plan_status
    return "unknown"


def _decision_headline(
    *,
    status: str,
    next_round: int | None,
    target_stage: str | None,
    stop_reason: str | None,
) -> str:
    if status == "scheduled":
        if next_round is not None and target_stage:
            return f"Next round {next_round} scheduled for {target_stage}"
        if next_round is not None:
            return f"Next round {next_round} scheduled"
        if target_stage:
            return f"Next round scheduled for {target_stage}"
        return "Next round scheduled"
    if status == "stopped":
        if stop_reason:
            return f"Stopped by {stop_reason}"
        return "Stopped after structured review"
    if status == "needs_review":
        if next_round is not None:
            return f"Manual review needed before round {next_round}"
        return "Manual review needed before continuing"
    if status == "budget_exhausted":
        return "Attempt budget exhausted"
    if status == "no_retry_needed":
        return "No additional retry round needed"
    if status == "planned":
        return "Retry plan drafted"
    return "Decision context available"


def _build_decision_notes(
    *,
    payloads: DecisionPayloadBundle,
    status: str,
    manual_review_required: bool,
    execution_hint_texts: list[str],
    action_count: int,
    triggered_stop_conditions: list[str],
) -> list[str]:
    notes: list[str] = []
    if triggered_stop_conditions:
        notes.append("Triggered stop conditions: " + ", ".join(triggered_stop_conditions))
    if manual_review_required and action_count:
        notes.append("Retry actions were prepared, but they should not auto-start before review.")
    if status == "scheduled" and action_count:
        notes.append(f"{action_count} retry action(s) are ready for the next round.")
    retry_notes = _string_list(payloads.retry_plan.get("notes"))
    if retry_notes:
        notes.extend(retry_notes[:2])
    stop_notes = _string_list(payloads.stop_conditions.get("notes"))
    if stop_notes:
        notes.extend(stop_notes[:2])
    if execution_hint_texts:
        notes.append("Execution focus: " + "; ".join(execution_hint_texts[:3]))
    return _unique_texts(note for note in notes if note)


def _decision_summary(explanation: DecisionExplanation) -> str | None:
    parts: list[str] = []
    if explanation.headline:
        parts.append(_ensure_period(explanation.headline))
    if explanation.primary_reason:
        parts.append(_ensure_period(explanation.primary_reason))
    elif explanation.status == "stopped" and explanation.stop_reason:
        parts.append(_ensure_period(f"Stop reason: {explanation.stop_reason}"))

    if explanation.status == "scheduled" and explanation.scheduled_action_count:
        parts.append(_ensure_period(f"{explanation.scheduled_action_count} retry action(s) prepared"))
    elif explanation.status == "needs_review" and explanation.scheduled_action_count:
        parts.append(
            _ensure_period(
                f"{explanation.scheduled_action_count} draft retry action(s) are available for review"
            )
        )

    if explanation.execution_hint_texts:
        parts.append(_ensure_period("Execution focus: " + "; ".join(explanation.execution_hint_texts[:2])))
    return " ".join(part for part in parts if part) or None


def _hint_phrase(key: str, value: Any) -> str | None:
    text_value = _text_value(value)
    if key == "focus_stage" and text_value:
        return f"focus on {text_value}"
    if key == "requires_human_review" and _bool_value(value) is True:
        return "requires human review before retry"
    if key == "stop_after_current_round" and _bool_value(value) is True:
        return "stop after the current round"
    if key == "related_items":
        items = _string_list(value)
        if items:
            return "related items: " + ", ".join(items[:3])
        return None
    if text_value and text_value in _EXECUTION_MODE_PHRASES:
        return _EXECUTION_MODE_PHRASES[text_value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = _string_list(value)
        if items:
            return f"{_humanize_key(key)}: {', '.join(items[:3])}"
        return None
    if _bool_value(value) is True:
        return _humanize_key(key)
    if text_value:
        return f"{_humanize_key(key)}: {text_value}"
    return None


def _append_fact(facts: list[Fact], label: str, value: Any) -> None:
    if value is None:
        return
    if value == "":
        return
    if isinstance(value, (list, dict)) and not value:
        return
    facts.append(Fact(label=label, value=value))


def _merge_mapping(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if value is None:
            continue
        if key not in target:
            target[key] = value
            continue
        target[key] = _merge_value(target[key], value)


def _merge_value(current: Any, incoming: Any) -> Any:
    if current == incoming or incoming is None:
        return current
    if isinstance(current, Mapping) and isinstance(incoming, Mapping):
        merged = dict(current)
        for key, value in incoming.items():
            if key not in merged:
                merged[key] = value
            else:
                merged[key] = _merge_value(merged[key], value)
        return merged
    if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
        current_items = list(current)
        if isinstance(incoming, Sequence) and not isinstance(incoming, (str, bytes, bytearray)):
            return _unique_texts([*current_items, *list(incoming)])
        return _unique_texts([*current_items, incoming])
    if isinstance(incoming, Sequence) and not isinstance(incoming, (str, bytes, bytearray)):
        return _unique_texts([current, *list(incoming)])
    return _unique_texts([current, incoming])


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


def _first_mapping(containers: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for container in containers:
        value = _mapping_value(container.get(key))
        if value:
            return value
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping) or is_dataclass(value):
        mapped = _mapping_value(value)
        return [mapped] if mapped else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[dict[str, Any]] = []
        for item in value:
            mapped = _mapping_value(item)
            if mapped:
                result.append(mapped)
        return result
    mapped = _mapping_value(value)
    return [mapped] if mapped else []


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


def _int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else None
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


def _humanize_key(key: str) -> str:
    return key.replace("_", " ")


def _ensure_period(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."
