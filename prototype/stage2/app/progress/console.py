from __future__ import annotations

from .models import CurrentStatusSnapshot, ProgressEvent, RecentEvent


def format_status_line(snapshot: CurrentStatusSnapshot) -> str:
    parts = [
        f"run={snapshot.run_id}",
        f"status={snapshot.overall_status}",
        f"phase={snapshot.current_phase or '-'}",
        f"step={snapshot.current_step.label or snapshot.current_step.key or '-'}",
    ]
    if snapshot.current_round.kind:
        round_label = snapshot.current_round.label or f"{snapshot.current_round.kind}#{snapshot.current_round.index}"
        parts.append(f"round={round_label}")
    if snapshot.current_target.label or snapshot.current_target.id:
        parts.append(f"target={snapshot.current_target.label or snapshot.current_target.id}")
    if snapshot.next_action:
        parts.append(f"next={snapshot.next_action}")
    return " | ".join(parts)


def format_event_brief(event: ProgressEvent | RecentEvent) -> str:
    parts = [event.timestamp, event.phase, event.status]
    step_label = getattr(event, "step", None)
    if step_label is not None:
        step_value = event.step.label or event.step.key
    else:
        step_value = event.step_label
    if step_value:
        parts.append(step_value)
    target_label = getattr(event, "target", None)
    if target_label is not None:
        target_value = event.target.label or event.target.id
    else:
        target_value = event.target_label
    if target_value:
        parts.append(target_value)
    if event.message:
        parts.append(event.message)
    return " | ".join(parts)


def format_snapshot(
    snapshot: CurrentStatusSnapshot,
    *,
    recent_limit: int = 5,
) -> str:
    lines = [
        "Stage-2 Progress Snapshot",
        "========================",
        format_status_line(snapshot),
        "",
        f"updated_at: {snapshot.updated_at}",
        f"started_at: {snapshot.started_at}",
        f"elapsed_ms: {snapshot.elapsed_ms}",
        f"heartbeat_at: {snapshot.last_heartbeat_at or '-'}",
        f"phase_label: {snapshot.current_phase_label or '-'}",
        f"round: {snapshot.current_round.label or _round_fallback(snapshot)}",
        f"step: {snapshot.current_step.label or snapshot.current_step.key or '-'}",
        f"target: {snapshot.current_target.label or snapshot.current_target.id or '-'}",
        f"message: {snapshot.latest_message or '-'}",
        f"waiting_reason: {snapshot.waiting_reason or '-'}",
        f"blocked_reason: {snapshot.blocked_reason or '-'}",
        f"next_action: {snapshot.next_action or '-'}",
        "",
        "stats:",
    ]

    if snapshot.stats:
        for key in sorted(snapshot.stats):
            lines.append(f"  - {key}: {snapshot.stats[key]}")
    else:
        lines.append("  - -")

    lines.append("")
    lines.append("phase_statuses:")
    if snapshot.phase_statuses:
        for key in sorted(snapshot.phase_statuses):
            lines.append(f"  - {key}: {snapshot.phase_statuses[key]}")
    else:
        lines.append("  - -")

    lines.append("")
    lines.append(f"recent_events (latest {recent_limit}):")
    if snapshot.recent_events:
        for recent in snapshot.recent_events[-recent_limit:]:
            lines.append(f"  - {format_event_brief(recent)}")
    else:
        lines.append("  - -")

    return "\n".join(lines)


def _round_fallback(snapshot: CurrentStatusSnapshot) -> str:
    if not snapshot.current_round.kind:
        return "-"
    if snapshot.current_round.index is None:
        return snapshot.current_round.kind
    return f"{snapshot.current_round.kind}#{snapshot.current_round.index}"
