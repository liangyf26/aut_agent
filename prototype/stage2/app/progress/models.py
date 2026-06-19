from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count
from typing import Any


JsonScalar = str | int | float | bool | None

_EVENT_COUNTER = count(1)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return f"evt-{next(_EVENT_COUNTER):06d}"


def elapsed_ms(started_at: str | None, ended_at: str | None) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


@dataclass
class RoundInfo:
    kind: str | None = None
    index: int | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepInfo:
    key: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TargetInfo:
    kind: str | None = None
    id: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecentEvent:
    timestamp: str
    event_type: str
    phase: str
    status: str
    step_label: str | None = None
    target_label: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProgressEvent:
    event_id: str
    run_id: str
    event_type: str
    status: str
    phase: str
    phase_label: str | None
    timestamp: str
    started_at: str | None = None
    ended_at: str | None = None
    elapsed_ms: int | None = None
    round: RoundInfo = field(default_factory=RoundInfo)
    step: StepInfo = field(default_factory=StepInfo)
    target: TargetInfo = field(default_factory=TargetInfo)
    message: str | None = None
    next_action: str | None = None
    stats: dict[str, JsonScalar] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "status": self.status,
            "phase": self.phase,
            "phase_label": self.phase_label,
            "timestamp": self.timestamp,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_ms": self.elapsed_ms,
            "round": self.round.to_dict(),
            "step": self.step.to_dict(),
            "target": self.target.to_dict(),
            "message": self.message,
            "next_action": self.next_action,
            "stats": self.stats,
            "details": self.details,
        }


@dataclass
class CurrentStatusSnapshot:
    run_id: str
    started_at: str
    updated_at: str
    template_name: str | None = None
    model_name: str | None = None
    project_name: str | None = None
    overall_status: str = "pending"
    current_phase: str | None = None
    current_phase_label: str | None = None
    current_round: RoundInfo = field(default_factory=RoundInfo)
    current_step: StepInfo = field(default_factory=StepInfo)
    current_target: TargetInfo = field(default_factory=TargetInfo)
    elapsed_ms: int = 0
    last_heartbeat_at: str | None = None
    latest_message: str | None = None
    waiting_reason: str | None = None
    blocked_reason: str | None = None
    next_action: str | None = None
    stats: dict[str, JsonScalar] = field(default_factory=dict)
    phase_statuses: dict[str, str] = field(default_factory=dict)
    recent_events: list[RecentEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "template_name": self.template_name,
            "model_name": self.model_name,
            "project_name": self.project_name,
            "overall_status": self.overall_status,
            "current_phase": self.current_phase,
            "current_phase_label": self.current_phase_label,
            "current_round": self.current_round.to_dict(),
            "current_step": self.current_step.to_dict(),
            "current_target": self.current_target.to_dict(),
            "elapsed_ms": self.elapsed_ms,
            "last_heartbeat_at": self.last_heartbeat_at,
            "latest_message": self.latest_message,
            "waiting_reason": self.waiting_reason,
            "blocked_reason": self.blocked_reason,
            "next_action": self.next_action,
            "stats": self.stats,
            "phase_statuses": self.phase_statuses,
            "recent_events": [event.to_dict() for event in self.recent_events],
        }


@dataclass
class PhaseSummaryEntry:
    phase: str
    phase_label: str | None = None
    status: str = "pending"
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    current_round: RoundInfo = field(default_factory=RoundInfo)
    last_step: StepInfo = field(default_factory=StepInfo)
    last_target: TargetInfo = field(default_factory=TargetInfo)
    last_message: str | None = None
    next_action: str | None = None
    stats: dict[str, JsonScalar] = field(default_factory=dict)
    phase_started_count: int = 0
    phase_completed_count: int = 0
    phase_failed_count: int = 0
    step_started_count: int = 0
    step_completed_count: int = 0
    step_failed_count: int = 0
    step_skipped_count: int = 0
    waiting_count: int = 0
    heartbeat_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "phase_label": self.phase_label,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "updated_at": self.updated_at,
            "current_round": self.current_round.to_dict(),
            "last_step": self.last_step.to_dict(),
            "last_target": self.last_target.to_dict(),
            "last_message": self.last_message,
            "next_action": self.next_action,
            "stats": self.stats,
            "phase_started_count": self.phase_started_count,
            "phase_completed_count": self.phase_completed_count,
            "phase_failed_count": self.phase_failed_count,
            "step_started_count": self.step_started_count,
            "step_completed_count": self.step_completed_count,
            "step_failed_count": self.step_failed_count,
            "step_skipped_count": self.step_skipped_count,
            "waiting_count": self.waiting_count,
            "heartbeat_count": self.heartbeat_count,
        }


@dataclass
class PhaseSummarySnapshot:
    run_id: str
    updated_at: str
    phases: dict[str, PhaseSummaryEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "updated_at": self.updated_at,
            "phases": {name: entry.to_dict() for name, entry in self.phases.items()},
        }
