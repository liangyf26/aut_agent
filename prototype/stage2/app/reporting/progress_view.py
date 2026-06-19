from __future__ import annotations

import json
import textwrap
from typing import Any

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
        for fact in event.facts:
            lines.append(f"  - {fact.label}: {_format_inline(fact.value)}")
        for key, value in sorted(event.extra.items()):
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
    return lines


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
