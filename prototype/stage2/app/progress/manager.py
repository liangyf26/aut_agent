from __future__ import annotations

from pathlib import Path
from typing import Any

from .console import format_snapshot
from .models import (
    CurrentStatusSnapshot,
    JsonScalar,
    PhaseSummaryEntry,
    PhaseSummarySnapshot,
    ProgressEvent,
    RecentEvent,
    RoundInfo,
    StepInfo,
    TargetInfo,
    elapsed_ms,
    new_event_id,
    utc_now_iso,
)
from .writer import ProgressWriter


class ProgressManager:
    """Owns the current snapshot and persists events as the run advances."""

    def __init__(
        self,
        run_id: str,
        output_dir: str | Path,
        *,
        template_name: str | None = None,
        model_name: str | None = None,
        project_name: str | None = None,
        max_recent_events: int = 20,
    ) -> None:
        now = utc_now_iso()
        self.run_id = run_id
        self.writer = ProgressWriter(output_dir)
        self.max_recent_events = max_recent_events
        self.snapshot = CurrentStatusSnapshot(
            run_id=run_id,
            started_at=now,
            updated_at=now,
            template_name=template_name,
            model_name=model_name,
            project_name=project_name,
        )
        self.phase_summary = PhaseSummarySnapshot(run_id=run_id, updated_at=now)
        self._phase_started_at: dict[str, str] = {}
        self._step_started_at: dict[tuple[str, str], str] = {}
        self.writer.write_current_status(self.snapshot)
        self.writer.write_phase_summary(self.phase_summary)

    def start_phase(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        now = utc_now_iso()
        self._phase_started_at[phase] = now
        return self.emit_event(
            event_type="phase_started",
            status="running",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=now,
            started_at=now,
        )

    def complete_phase(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        ended_at = utc_now_iso()
        started_at = self._phase_started_at.pop(phase, None)
        return self.emit_event(
            event_type="phase_completed",
            status="completed",
            phase=phase,
            phase_label=phase_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=ended_at,
            started_at=started_at,
            ended_at=ended_at,
        )

    def fail_phase(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        ended_at = utc_now_iso()
        started_at = self._phase_started_at.pop(phase, None)
        return self.emit_event(
            event_type="phase_failed",
            status="failed",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=ended_at,
            started_at=started_at,
            ended_at=ended_at,
        )

    def start_step(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        now = utc_now_iso()
        self._step_started_at[self._step_key(phase, step_key, step_label)] = now
        return self.emit_event(
            event_type="step_started",
            status="running",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=now,
            started_at=now,
        )

    def complete_step(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        ended_at = utc_now_iso()
        started_at = self._step_started_at.pop(self._step_key(phase, step_key, step_label), None)
        return self.emit_event(
            event_type="step_completed",
            status="completed",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=ended_at,
            started_at=started_at,
            ended_at=ended_at,
        )

    def fail_step(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        ended_at = utc_now_iso()
        started_at = self._step_started_at.pop(self._step_key(phase, step_key, step_label), None)
        return self.emit_event(
            event_type="step_failed",
            status="failed",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=ended_at,
            started_at=started_at,
            ended_at=ended_at,
        )

    def skip_step(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        ended_at = utc_now_iso()
        started_at = self._step_started_at.pop(self._step_key(phase, step_key, step_label), None)
        return self.emit_event(
            event_type="step_skipped",
            status="skipped",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
            timestamp=ended_at,
            started_at=started_at,
            ended_at=ended_at,
        )

    def wait_for_human(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        reason: str,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        return self.emit_event(
            event_type="waiting_human",
            status="waiting_human",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=reason,
            next_action=next_action,
            stats=stats,
            details=details,
        )

    def block(
        self,
        phase: str,
        *,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        reason: str,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        return self.emit_event(
            event_type="blocked",
            status="blocked",
            phase=phase,
            phase_label=phase_label,
            round_kind=round_kind,
            round_index=round_index,
            round_label=round_label,
            step_key=step_key,
            step_label=step_label,
            target_kind=target_kind,
            target_id=target_id,
            target_label=target_label,
            message=reason,
            next_action=next_action,
            stats=stats,
            details=details,
        )

    def heartbeat(
        self,
        *,
        phase: str | None = None,
        phase_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        current_phase = phase or self.snapshot.current_phase or "unknown"
        current_status = self.snapshot.overall_status
        if current_status in {"pending", "completed", "failed", "skipped"}:
            current_status = "running"
        return self.emit_event(
            event_type="heartbeat",
            status=current_status,
            phase=current_phase,
            phase_label=phase_label or self.snapshot.current_phase_label,
            round_kind=round_kind or self.snapshot.current_round.kind,
            round_index=round_index if round_index is not None else self.snapshot.current_round.index,
            round_label=round_label or self.snapshot.current_round.label,
            step_key=step_key or self.snapshot.current_step.key,
            step_label=step_label or self.snapshot.current_step.label,
            target_kind=target_kind or self.snapshot.current_target.kind,
            target_id=target_id or self.snapshot.current_target.id,
            target_label=target_label or self.snapshot.current_target.label,
            message=message,
            next_action=next_action,
            stats=stats,
            details=details,
        )

    def emit_event(
        self,
        *,
        event_type: str,
        status: str,
        phase: str,
        phase_label: str | None = None,
        round_kind: str | None = None,
        round_index: int | None = None,
        round_label: str | None = None,
        step_key: str | None = None,
        step_label: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        message: str | None = None,
        next_action: str | None = None,
        stats: dict[str, JsonScalar] | None = None,
        details: dict[str, Any] | None = None,
        timestamp: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> ProgressEvent:
        event = ProgressEvent(
            event_id=new_event_id(),
            run_id=self.run_id,
            event_type=event_type,
            status=status,
            phase=phase,
            phase_label=phase_label,
            timestamp=timestamp or utc_now_iso(),
            started_at=started_at,
            ended_at=ended_at,
            elapsed_ms=elapsed_ms(started_at, ended_at),
            round=RoundInfo(kind=round_kind, index=round_index, label=round_label),
            step=StepInfo(key=step_key, label=step_label),
            target=TargetInfo(kind=target_kind, id=target_id, label=target_label),
            message=message,
            next_action=next_action,
            stats=dict(stats or {}),
            details=dict(details or {}),
        )
        self._apply_event(event)
        self.writer.append_event(event)
        self.writer.write_current_status(self.snapshot)
        self.writer.write_phase_summary(self.phase_summary)
        return event

    def render_console_summary(self, *, recent_limit: int = 5) -> str:
        return format_snapshot(self.snapshot, recent_limit=recent_limit)

    def _apply_event(self, event: ProgressEvent) -> None:
        previous_phase = self.snapshot.current_phase
        self.snapshot.overall_status = event.status
        self.snapshot.current_phase = event.phase
        self.snapshot.current_phase_label = event.phase_label
        if self._round_has_value(event.round):
            self.snapshot.current_round = event.round
        elif previous_phase == event.phase:
            self.snapshot.current_round = self.snapshot.current_round
        else:
            self.snapshot.current_round = event.round
        self.snapshot.current_step = event.step
        self.snapshot.current_target = event.target
        self.snapshot.updated_at = event.timestamp
        self.snapshot.last_heartbeat_at = event.timestamp
        self.snapshot.elapsed_ms = elapsed_ms(self.snapshot.started_at, event.timestamp) or 0
        self.snapshot.latest_message = event.message
        self.snapshot.next_action = event.next_action
        self.snapshot.phase_statuses[event.phase] = event.status
        if event.stats:
            self.snapshot.stats.update(event.stats)

        if event.status == "waiting_human":
            self.snapshot.waiting_reason = event.message
            self.snapshot.blocked_reason = None
        elif event.status == "blocked":
            self.snapshot.blocked_reason = event.message
            self.snapshot.waiting_reason = None
        else:
            self.snapshot.waiting_reason = None
            self.snapshot.blocked_reason = None

        self.snapshot.recent_events.append(
            RecentEvent(
                timestamp=event.timestamp,
                event_type=event.event_type,
                phase=event.phase,
                status=event.status,
                step_label=event.step.label or event.step.key,
                target_label=event.target.label or event.target.id,
                message=event.message,
            )
        )
        if len(self.snapshot.recent_events) > self.max_recent_events:
            self.snapshot.recent_events = self.snapshot.recent_events[-self.max_recent_events :]

        phase_entry = self.phase_summary.phases.get(event.phase)
        if phase_entry is None:
            phase_entry = PhaseSummaryEntry(phase=event.phase, phase_label=event.phase_label)
            self.phase_summary.phases[event.phase] = phase_entry
        self._apply_phase_summary(phase_entry, event)
        self.phase_summary.updated_at = event.timestamp

    def _apply_phase_summary(self, phase_entry: PhaseSummaryEntry, event: ProgressEvent) -> None:
        phase_entry.phase_label = event.phase_label or phase_entry.phase_label
        phase_entry.status = event.status
        phase_entry.updated_at = event.timestamp
        if self._round_has_value(event.round):
            phase_entry.current_round = event.round
        if self._step_has_value(event.step):
            phase_entry.last_step = event.step
        if self._target_has_value(event.target):
            phase_entry.last_target = event.target
        phase_entry.last_message = event.message
        phase_entry.next_action = event.next_action
        if event.stats:
            phase_entry.stats.update(event.stats)

        if event.event_type == "phase_started":
            phase_entry.phase_started_count += 1
            phase_entry.started_at = event.started_at or event.timestamp
        elif event.event_type == "phase_completed":
            phase_entry.phase_completed_count += 1
            phase_entry.ended_at = event.ended_at or event.timestamp
        elif event.event_type == "phase_failed":
            phase_entry.phase_failed_count += 1
            phase_entry.ended_at = event.ended_at or event.timestamp
        elif event.event_type == "step_started":
            phase_entry.step_started_count += 1
        elif event.event_type == "step_completed":
            phase_entry.step_completed_count += 1
        elif event.event_type == "step_failed":
            phase_entry.step_failed_count += 1
        elif event.event_type == "step_skipped":
            phase_entry.step_skipped_count += 1
        elif event.event_type == "waiting_human":
            phase_entry.waiting_count += 1
        elif event.event_type == "heartbeat":
            phase_entry.heartbeat_count += 1

    def _step_key(self, phase: str, step_key: str | None, step_label: str | None) -> tuple[str, str]:
        return (phase, step_key or step_label or "__phase__")

    def _round_has_value(self, round_info: RoundInfo) -> bool:
        return any(value is not None for value in (round_info.kind, round_info.index, round_info.label))

    def _step_has_value(self, step_info: StepInfo) -> bool:
        return any(value is not None for value in (step_info.key, step_info.label))

    def _target_has_value(self, target_info: TargetInfo) -> bool:
        return any(value is not None for value in (target_info.kind, target_info.id, target_info.label))
