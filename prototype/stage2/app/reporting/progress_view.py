from __future__ import annotations

import json
import textwrap
from typing import Any

from .decision_explainer import build_decision_explanation
from .models import ProgressCounter, ProgressEvent, ProgressSnapshot, coerce_progress_event, coerce_progress_snapshot


def render_progress_markdown(
    snapshot: ProgressSnapshot | dict[str, Any],
    *,
    recent_events: list[ProgressEvent | dict[str, Any]] | None = None,
    max_events: int = 20,
) -> str:
    normalized = coerce_progress_snapshot(snapshot)
    events = _resolve_events(normalized, recent_events, max_events)
    lines = [f"# Run Progress: {normalized.run_id}", ""]

    lines.append("## Current Status")
    lines.append("")
    current_status_pairs = [
        ("Status", normalized.status),
        ("Stage", normalized.stage),
        ("Step", normalized.step),
        ("Project", normalized.project_name),
        ("Template", normalized.template_name),
        ("Target Type", normalized.target_type),
        ("Target Name", normalized.target_name),
        ("Current Round", normalized.current_round),
        ("Discovery Round", normalized.discovery_round),
        ("Verification Round", normalized.verification_round),
        ("Attribution Round", normalized.attribution_round),
        ("Started At", normalized.started_at),
        ("Updated At", normalized.updated_at),
        ("Elapsed", _format_duration(normalized.elapsed_seconds)),
        ("Heartbeat", normalized.heartbeat_at),
        ("Blocked Reason", normalized.blocked_reason),
        ("Next Action", normalized.next_action),
    ]
    for label, value in current_status_pairs:
        if value is None:
            continue
        lines.append(f"- {label}: {_format_inline(value)}")
    if normalized.notes:
        lines.append("- Notes:")
        for note in normalized.notes:
            lines.append(f"  - {note}")
    lines.append("")

    lines.extend(_render_decision_section(normalized))
    lines.extend(_render_counter_section(normalized.counters))
    lines.extend(_render_event_section(events))
    return "\n".join(lines).rstrip() + "\n"


def render_progress_text(
    snapshot: ProgressSnapshot | dict[str, Any],
    *,
    recent_events: list[ProgressEvent | dict[str, Any]] | None = None,
    max_events: int = 20,
    width: int = 88,
) -> str:
    normalized = coerce_progress_snapshot(snapshot)
    events = _resolve_events(normalized, recent_events, max_events)
    inner_width = max(width, 60)

    lines = [
        "Run Progress",
        "=" * inner_width,
        f"Run: {normalized.run_id}",
    ]
    if normalized.project_name:
        lines.append(f"Project: {normalized.project_name}")
    if normalized.template_name:
        lines.append(f"Template: {normalized.template_name}")
    lines.append(f"Status: {normalized.status}")
    lines.append(f"Stage: {normalized.stage}")
    if normalized.step:
        lines.append(f"Step: {normalized.step}")
    if normalized.target_type or normalized.target_name:
        target = " / ".join(
            item
            for item in [normalized.target_type, normalized.target_name]
            if item
        )
        lines.append(f"Target: {target}")
    round_text = _format_rounds(normalized)
    if round_text:
        lines.append(f"Rounds: {round_text}")
    time_parts = []
    if normalized.started_at:
        time_parts.append(f"started {normalized.started_at}")
    if normalized.updated_at:
        time_parts.append(f"updated {normalized.updated_at}")
    if normalized.elapsed_seconds is not None:
        time_parts.append(f"elapsed {_format_duration(normalized.elapsed_seconds)}")
    if normalized.heartbeat_at:
        time_parts.append(f"heartbeat {normalized.heartbeat_at}")
    if time_parts:
        lines.append(f"Time: {' | '.join(time_parts)}")
    if normalized.blocked_reason:
        lines.append(f"Blocked: {normalized.blocked_reason}")
    if normalized.next_action:
        lines.append(f"Next: {normalized.next_action}")
    if normalized.notes:
        lines.append("Notes:")
        for note in normalized.notes:
            lines.extend(_wrap(f"- {note}", inner_width))

    lines.append("")
    lines.append("Decision View")
    lines.append("-" * inner_width)
    lines.extend(_render_decision_text(normalized, inner_width))

    lines.append("")
    lines.append("Progress Counters")
    lines.append("-" * inner_width)
    if normalized.counters:
        for counter in normalized.counters:
            lines.extend(_render_counter_text(counter, inner_width))
    else:
        lines.append("No counters recorded.")

    lines.append("")
    lines.append("Recent Events")
    lines.append("-" * inner_width)
    if events:
        for event in events:
            lines.extend(_render_event_text(event, inner_width))
    else:
        lines.append("No recent events recorded.")

    return "\n".join(lines).rstrip() + "\n"


def _render_counter_section(counters: list[ProgressCounter]) -> list[str]:
    lines = ["## Progress Counters", ""]
    if not counters:
        lines.append("- No counters recorded.")
        lines.append("")
        return lines
    for counter in counters:
        lines.append(f"- {_format_counter(counter)}")
    lines.append("")
    return lines


def _render_decision_section(snapshot: ProgressSnapshot) -> list[str]:
    lines = ["## Decision View", ""]
    extra = _snapshot_extra(snapshot)
    explanation = _build_snapshot_decision_explanation(snapshot)
    phase_label = _textish(extra.get("phase_label"))
    phase_summary = _phase_summary(snapshot)
    if phase_label:
        lines.append(f"- Platform Phase Label: {phase_label}")
    if phase_summary:
        lines.append(f"- Platform Phase Summary: {phase_summary}")

    if explanation:
        lines.append(f"- Decision Status: {explanation.status}")
    if explanation and explanation.headline:
        lines.append(f"- Decision Headline: {explanation.headline}")
    if explanation and explanation.summary:
        lines.append(f"- Decision Summary: {explanation.summary}")

    decision_reason = _decision_reason(snapshot)
    if decision_reason:
        lines.append(f"- Why This State: {decision_reason}")

    blocked_or_waiting = snapshot.blocked_reason or _textish(extra.get("waiting_reason"))
    if blocked_or_waiting:
        lines.append(f"- Blocked Or Waiting Reason: {blocked_or_waiting}")

    if snapshot.next_action:
        lines.append(f"- Next Action: {snapshot.next_action}")

    classification_category = (
        _textish(extra.get("classification_category"))
        or _textish(extra.get("failure_category"))
        or _textish(extra.get("failure_type"))
    )
    if classification_category:
        lines.append(f"- Structured Failure Category: `{classification_category}`")

    failure_categories = _collect_failure_categories(extra)
    if failure_categories:
        lines.append(f"- Structured Failure Categories: {', '.join(failure_categories)}")

    stop_payload = _mappingish(extra.get("stop_conditions"))
    next_round_payload = _mappingish(extra.get("next_round_decision"))
    execution_hints = _mappingish(
        extra.get("applied_execution_hints") or extra.get("execution_hints")
    )

    if stop_payload:
        lines.append(f"- Stop Decision Status: {_format_inline(stop_payload.get('status'))}")
        if "should_stop" in stop_payload:
            lines.append(f"- Should Stop: {_format_inline(stop_payload.get('should_stop'))}")
        primary_reason = _textish(stop_payload.get("primary_reason"))
        if primary_reason:
            lines.append(f"- Stop Condition Result: {primary_reason}")
        triggered = _string_listish(stop_payload.get("triggered_conditions"))
        if triggered:
            lines.append(f"- Triggered Stop Conditions: {', '.join(triggered)}")

    if next_round_payload:
        lines.append(f"- Next-Round Status: {_format_inline(next_round_payload.get('status'))}")
        if "should_start_next_round" in next_round_payload:
            lines.append(
                f"- Should Start Next Round: {_format_inline(next_round_payload.get('should_start_next_round'))}"
            )
        if next_round_payload.get("next_round") is not None:
            lines.append(f"- Planned Next Round: {_format_inline(next_round_payload.get('next_round'))}")
        if next_round_payload.get("target_stage"):
            lines.append(f"- Planned Target Stage: `{next_round_payload.get('target_stage')}`")
        primary_reason = _textish(next_round_payload.get("primary_reason"))
        if primary_reason:
            lines.append(f"- Next-Round Rationale: {primary_reason}")
        scheduled_ids = _string_listish(next_round_payload.get("scheduled_action_ids"))
        if scheduled_ids:
            lines.append(f"- Scheduled Action IDs: {', '.join(scheduled_ids)}")

    if explanation:
        if explanation.manual_review_required:
            lines.append("- Manual Review Required: true")
        if explanation.next_round is not None and next_round_payload.get("next_round") is None:
            lines.append(f"- Planned Next Round: {_format_inline(explanation.next_round)}")
        if explanation.target_stage and not next_round_payload.get("target_stage"):
            lines.append(f"- Planned Target Stage: `{explanation.target_stage}`")
        if explanation.scheduled_action_count:
            lines.append(f"- Planned Retry Actions: {explanation.scheduled_action_count}")
        if explanation.scheduled_cluster_count:
            lines.append(f"- Planned Retry Clusters: {explanation.scheduled_cluster_count}")
        if explanation.execution_hint_texts:
            lines.append(f"- Execution Focus: {'; '.join(explanation.execution_hint_texts[:3])}")

    if execution_hints:
        lines.append("- Applied Execution Hints:")
        for key, value in sorted(execution_hints.items()):
            lines.append(f"  - {key}: {_format_inline(value)}")

    if len(lines) == 2:
        lines.append("- No structured decision details recorded.")
    lines.append("")
    return lines


def _render_event_section(events: list[ProgressEvent]) -> list[str]:
    lines = ["## Recent Events", ""]
    if not events:
        lines.append("- No recent events recorded.")
        lines.append("")
        return lines
    for event in events:
        headline = _format_event_headline(event)
        lines.append(f"- {headline}")
        if event.message:
            lines.append(f"  - message: {event.message}")
        if event.target_type or event.target_name:
            target = " / ".join(item for item in [event.target_type, event.target_name] if item)
            lines.append(f"  - target: {target}")
        round_text = _format_rounds(event)
        if round_text:
            lines.append(f"  - rounds: {round_text}")
        if event.next_action:
            lines.append(f"  - next: {event.next_action}")
        event_failure_categories = _collect_failure_categories(event.extra)
        if event_failure_categories:
            lines.append(f"  - failure categories: {', '.join(event_failure_categories)}")
        event_hints = _mappingish(
            event.extra.get("applied_execution_hints") or event.extra.get("execution_hints")
        )
        if event_hints:
            lines.append(f"  - execution hints: {_format_hint_summary(event_hints)}")
        for fact in event.facts:
            lines.append(f"  - {fact.label}: {_format_inline(fact.value)}")
        for key, value in sorted(event.extra.items()):
            if key in {
                "stop_conditions",
                "next_round_decision",
                "execution_hints",
                "applied_execution_hints",
                "failure_clusters",
                "failure_categories",
                "classification_category",
                "failure_category",
                "failure_type",
            }:
                continue
            lines.append(f"  - {key}: {_format_inline(value)}")
    lines.append("")
    return lines


def _render_counter_text(counter: ProgressCounter, width: int) -> list[str]:
    if counter.completed is not None and counter.total is not None:
        ratio = counter.ratio or 0.0
        label = f"{counter.label}: {counter.completed}/{counter.total}"
        bar = _progress_bar(ratio)
        text = f"{bar} {label}"
        if counter.note:
            text += f" ({counter.note})"
        return _wrap(text, width)
    text = _format_counter(counter)
    return _wrap(text, width)


def _render_event_text(event: ProgressEvent, width: int) -> list[str]:
    lines = _wrap(_format_event_headline(event), width)
    if event.message:
        lines.extend(_wrap(f"  {event.message}", width))
    if event.next_action:
        lines.extend(_wrap(f"  next: {event.next_action}", width))
    event_failure_categories = _collect_failure_categories(event.extra)
    if event_failure_categories:
        lines.extend(_wrap(f"  failure categories: {', '.join(event_failure_categories)}", width))
    event_hints = _mappingish(
        event.extra.get("applied_execution_hints") or event.extra.get("execution_hints")
    )
    if event_hints:
        lines.extend(_wrap(f"  execution hints: {_format_hint_summary(event_hints)}", width))
    return lines


def _render_decision_text(snapshot: ProgressSnapshot, width: int) -> list[str]:
    lines: list[str] = []
    extra = _snapshot_extra(snapshot)
    explanation = _build_snapshot_decision_explanation(snapshot)
    phase_label = _textish(extra.get("phase_label"))
    phase_summary = _phase_summary(snapshot)
    if phase_label:
        lines.extend(_wrap(f"Phase label: {phase_label}", width))
    if phase_summary:
        lines.extend(_wrap(f"Phase summary: {phase_summary}", width))

    if explanation:
        lines.extend(_wrap(f"Decision status: {explanation.status}", width))
    if explanation and explanation.headline:
        lines.extend(_wrap(f"Decision headline: {explanation.headline}", width))
    if explanation and explanation.summary:
        lines.extend(_wrap(f"Decision summary: {explanation.summary}", width))

    decision_reason = _decision_reason(snapshot)
    if decision_reason:
        lines.extend(_wrap(f"Why this state: {decision_reason}", width))

    blocked_or_waiting = snapshot.blocked_reason or _textish(extra.get("waiting_reason"))
    if blocked_or_waiting:
        lines.extend(_wrap(f"Blocked/waiting: {blocked_or_waiting}", width))

    if snapshot.next_action:
        lines.extend(_wrap(f"Next action: {snapshot.next_action}", width))

    classification_category = (
        _textish(extra.get("classification_category"))
        or _textish(extra.get("failure_category"))
        or _textish(extra.get("failure_type"))
    )
    if classification_category:
        lines.extend(_wrap(f"Structured failure category: {classification_category}", width))

    failure_categories = _collect_failure_categories(extra)
    if failure_categories:
        lines.extend(_wrap(f"Failure categories: {', '.join(failure_categories)}", width))

    stop_payload = _mappingish(extra.get("stop_conditions"))
    if stop_payload:
        lines.extend(_wrap(f"Stop decision: status={_format_inline(stop_payload.get('status'))}", width))
        if "should_stop" in stop_payload:
            lines.extend(_wrap(f"  should stop: {_format_inline(stop_payload.get('should_stop'))}", width))
        primary_reason = _textish(stop_payload.get("primary_reason"))
        if primary_reason:
            lines.extend(_wrap(f"  reason: {primary_reason}", width))

    next_round_payload = _mappingish(extra.get("next_round_decision"))
    if next_round_payload:
        lines.extend(
            _wrap(f"Next-round decision: status={_format_inline(next_round_payload.get('status'))}", width)
        )
        if "should_start_next_round" in next_round_payload:
            lines.extend(
                _wrap(
                    f"  should start next round: {_format_inline(next_round_payload.get('should_start_next_round'))}",
                    width,
                )
            )
        if next_round_payload.get("next_round") is not None:
            lines.extend(
                _wrap(f"  planned next round: {_format_inline(next_round_payload.get('next_round'))}", width)
            )
        primary_reason = _textish(next_round_payload.get("primary_reason"))
        if primary_reason:
            lines.extend(_wrap(f"  rationale: {primary_reason}", width))

    execution_hints = _mappingish(
        extra.get("applied_execution_hints") or extra.get("execution_hints")
    )
    if explanation and explanation.manual_review_required:
        lines.extend(_wrap("Manual review required: true", width))
    if explanation and explanation.scheduled_action_count:
        lines.extend(_wrap(f"Planned retry actions: {explanation.scheduled_action_count}", width))
    if explanation and explanation.scheduled_cluster_count:
        lines.extend(_wrap(f"Planned retry clusters: {explanation.scheduled_cluster_count}", width))
    if explanation and explanation.execution_hint_texts:
        lines.extend(_wrap(f"Execution focus: {'; '.join(explanation.execution_hint_texts[:3])}", width))
    if execution_hints:
        lines.extend(_wrap(f"Applied execution hints: {_format_hint_summary(execution_hints)}", width))

    if not lines:
        lines.append("No structured decision details recorded.")
    return lines


def _build_snapshot_decision_explanation(snapshot: ProgressSnapshot):
    extra = _snapshot_extra(snapshot)
    stop_payload = _mappingish(extra.get("stop_conditions"))
    next_round_payload = _mappingish(extra.get("next_round_decision"))
    retry_plan_payload = _mappingish(extra.get("retry_plan"))
    round_input_payload = _mappingish(extra.get("round_input"))
    execution_hints = _mappingish(
        extra.get("applied_execution_hints") or extra.get("execution_hints")
    )
    if not any((stop_payload, next_round_payload, retry_plan_payload, round_input_payload, execution_hints)):
        return None
    return build_decision_explanation(
        stop_conditions=stop_payload,
        next_round_decision=next_round_payload,
        retry_plan=retry_plan_payload,
        round_input=round_input_payload,
        execution_hints=execution_hints,
    )


def _resolve_events(
    snapshot: ProgressSnapshot,
    recent_events: list[ProgressEvent | dict[str, Any]] | None,
    max_events: int,
) -> list[ProgressEvent]:
    source = recent_events if recent_events is not None else snapshot.recent_events
    normalized = [coerce_progress_event(item) for item in source]
    if max_events <= 0:
        return normalized
    return normalized[-max_events:]


def _format_rounds(value: ProgressSnapshot | ProgressEvent) -> str | None:
    parts = []
    if value.current_round is not None:
        parts.append(f"current={value.current_round}")
    if value.discovery_round is not None:
        parts.append(f"discovery={value.discovery_round}")
    if value.verification_round is not None:
        parts.append(f"verification={value.verification_round}")
    if value.attribution_round is not None:
        parts.append(f"attribution={value.attribution_round}")
    if not parts:
        return None
    return ", ".join(parts)


def _format_event_headline(event: ProgressEvent) -> str:
    segments = []
    if event.occurred_at:
        segments.append(event.occurred_at)
    segments.append(f"[{event.status}]")
    segments.append(event.stage)
    if event.step:
        segments.append(f"/ {event.step}")
    if event.event_id:
        segments.append(f"({event.event_id})")
    return " ".join(segments)


def _format_counter(counter: ProgressCounter) -> str:
    if counter.completed is not None and counter.total is not None:
        ratio = counter.ratio
        suffix = f" ({ratio:.0%})" if ratio is not None else ""
        text = f"{counter.label}: {counter.completed}/{counter.total}{suffix}"
    elif counter.value is not None:
        text = f"{counter.label}: {_format_inline(counter.value)}"
    else:
        text = counter.label
    if counter.unit and counter.value is not None and counter.completed is None:
        text += f" {counter.unit}"
    if counter.note:
        text += f" ({counter.note})"
    return text


def _progress_bar(ratio: float, width: int = 18) -> str:
    ratio = min(max(ratio, 0.0), 1.0)
    filled = int(round(width * ratio))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_inline(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_format_inline(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def _format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width) or [text]


def _mappingish(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _textish(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_listish(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            text = _textish(item)
            if text:
                values.append(text)
        return values
    text = _textish(value)
    return [text] if text else []


def _collect_failure_categories(extra: dict[str, Any]) -> list[str]:
    categories: set[str] = set()
    direct_keys = ("classification_category", "failure_category", "failure_type")
    for key in direct_keys:
        text = _textish(extra.get(key))
        if text:
            categories.add(text)

    for item in _string_listish(extra.get("failure_categories")):
        categories.add(item)

    clusters = extra.get("failure_clusters")
    if isinstance(clusters, list):
        for cluster in clusters:
            if isinstance(cluster, dict):
                text = _textish(cluster.get("category") or cluster.get("failure_category"))
                if text:
                    categories.add(text)

    if len(categories) > 1:
        direct_category = _textish(extra.get("classification_category"))
        if direct_category and direct_category.startswith("unknown_") and "unknown" in categories:
            categories.discard("unknown")

    return sorted(categories)


def _phase_summary(snapshot: ProgressSnapshot) -> str | None:
    stage = _textish(snapshot.stage)
    status = _textish(snapshot.status)
    if not stage and not status:
        return None
    target = " / ".join(part for part in [snapshot.target_type, snapshot.target_name] if part)
    parts = []
    if status:
        parts.append(status)
    if stage:
        parts.append(stage)
    if snapshot.step:
        parts.append(snapshot.step)
    if target:
        parts.append(target)
    return " -> ".join(parts)


def _decision_reason(snapshot: ProgressSnapshot) -> str | None:
    extra = _snapshot_extra(snapshot)
    next_round_payload = _mappingish(extra.get("next_round_decision"))
    stop_payload = _mappingish(extra.get("stop_conditions"))
    for value in (
        next_round_payload.get("primary_reason"),
        stop_payload.get("primary_reason"),
        snapshot.blocked_reason,
        snapshot.next_action,
    ):
        text = _textish(value)
        if text:
            return text
    return None


def _snapshot_extra(snapshot: ProgressSnapshot) -> dict[str, Any]:
    extra = dict(snapshot.extra)
    nested = _mappingish(extra.get("extra"))
    if nested:
        merged = dict(extra)
        merged.update(nested)
        return merged
    return extra


def _format_hint_summary(hints: dict[str, Any]) -> str:
    return "; ".join(f"{key}={_format_inline(value)}" for key, value in sorted(hints.items()))
