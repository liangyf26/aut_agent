from __future__ import annotations

from typing import Any

from prototype.stage2.app.progress.models import CurrentStatusSnapshot, RecentEvent
from prototype.stage2.app.reporting.models import ProgressCounter, ProgressEvent, ProgressSnapshot


def adapt_progress_snapshot(snapshot: CurrentStatusSnapshot) -> ProgressSnapshot:
    counters = [
        ProgressCounter(label=key, value=value)
        for key, value in sorted(snapshot.stats.items())
    ]
    recent_events = [adapt_recent_event(event) for event in snapshot.recent_events]
    return ProgressSnapshot(
        run_id=snapshot.run_id,
        status=snapshot.overall_status,
        stage=snapshot.current_phase or "unknown",
        step=snapshot.current_step.label or snapshot.current_step.key,
        project_name=snapshot.project_name,
        template_name=snapshot.template_name,
        target_type=snapshot.current_target.kind,
        target_name=snapshot.current_target.label or snapshot.current_target.id,
        started_at=snapshot.started_at,
        updated_at=snapshot.updated_at,
        elapsed_seconds=(snapshot.elapsed_ms / 1000.0) if snapshot.elapsed_ms else 0,
        heartbeat_at=snapshot.last_heartbeat_at,
        blocked_reason=snapshot.blocked_reason or snapshot.waiting_reason,
        next_action=snapshot.next_action,
        counters=counters,
        recent_events=recent_events,
        notes=[],
    )


def adapt_recent_event(event: RecentEvent | dict[str, Any]) -> ProgressEvent:
    if isinstance(event, RecentEvent):
        return ProgressEvent(
            occurred_at=event.timestamp,
            stage=event.phase,
            step=event.step_label,
            status=event.status,
            message=event.message,
        )
    return ProgressEvent.from_value(event)
