"""Data model for the goal loop kernel.

All records follow the existing stage2 convention: ``@dataclass(slots=True)`` with
a ``to_dict()`` that runs ``asdict`` through a local ``_compact_dict`` to drop
empty fields (mirrors ``iteration.models`` and ``config.run_policy_loader``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


# --- Goal types & statuses ---------------------------------------------------

GOAL_TYPE_MENU = "menu"
GOAL_TYPE_PAGE = "page"
GOAL_TYPE_FEATURE = "feature"
GOAL_TYPES: frozenset[str] = frozenset({GOAL_TYPE_MENU, GOAL_TYPE_PAGE, GOAL_TYPE_FEATURE})

# Goal statuses (需求 §5.4).
STATUS_PLANNED = "planned"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED_MAX_ROUNDS = "failed_max_rounds"
STATUS_WAITING_HUMAN = "waiting_human"
STATUS_BLOCKED_BY_POLICY = "blocked_by_policy"
STATUS_BLOCKED_BY_EXECUTOR = "blocked_by_executor"
STATUS_STOPPED_NO_PROGRESS = "stopped_no_progress"
STATUS_SUPERSEDED = "superseded"

GOAL_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_PLANNED,
        STATUS_RUNNING,
        STATUS_SUCCEEDED,
        STATUS_FAILED_MAX_ROUNDS,
        STATUS_WAITING_HUMAN,
        STATUS_BLOCKED_BY_POLICY,
        STATUS_BLOCKED_BY_EXECUTOR,
        STATUS_STOPPED_NO_PROGRESS,
        STATUS_SUPERSEDED,
    }
)

# A goal is "resolved" (concluded, safe for the frontier to advance past) only in
# these statuses. Paused statuses are deliberately NOT terminal: a paused goal is
# still the active conclusion and must be resumed, not silently abandoned.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_SUCCEEDED,
        STATUS_FAILED_MAX_ROUNDS,
        STATUS_STOPPED_NO_PROGRESS,
        STATUS_SUPERSEDED,
    }
)

# A goal is paused (needs a human decision or a blocker cleared, then resume). It
# blocks frontier advancement until resolved or resumed.
PAUSED_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_WAITING_HUMAN,
        STATUS_BLOCKED_BY_POLICY,
        STATUS_BLOCKED_BY_EXECUTOR,
    }
)

# Attempt statuses.
ATTEMPT_RUNNING = "running"
ATTEMPT_SUCCEEDED = "succeeded"
ATTEMPT_FAILED = "failed"

# Promotion layers (沉淀分层).
PROMOTION_RUN = "run"
PROMOTION_PROJECT = "project"
PROMOTION_PLATFORM = "platform"


@dataclass(slots=True)
class SuccessCriterion:
    """A success condition expressed as a computable predicate (技术方案 §6.6)."""

    predicate_name: str
    expression: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class EvidenceRef:
    """One atomic evidence, bound to its owning step (技术方案 §5.7).

    ``owner_step_id`` is mandatory; evidence without it is a chain break and must
    surface as ``evidence_incomplete`` rather than being silently accepted.
    """

    evidence_id: str
    owner_step_id: str
    kind: str  # screenshot | network | feedback | assertion | ...
    uri: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class GoalStep:
    step_id: str
    attempt_id: str
    index: int
    kind: str  # navigate | action | assertion | ...
    action: str | None = None
    status: str = "recorded"
    observed: bool = True
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class GoalAttempt:
    attempt_id: str
    goal_id: str
    index: int
    status: str = ATTEMPT_RUNNING
    started_at: str | None = None
    ended_at: str | None = None
    failure_class: str | None = None
    playbook_id: str | None = None
    result: str | None = None
    steps: list[GoalStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        return _compact_dict(payload)


@dataclass(slots=True)
class Goal:
    goal_id: str
    goal_type: str
    goal_name: str
    status: str = STATUS_PLANNED
    parent_goal_id: str | None = None
    origin: str | None = None
    success_criteria: list[SuccessCriterion] = field(default_factory=list)
    evidence_requirements: list[str] = field(default_factory=list)
    allow_human_intervention: bool = True
    max_rounds: int = 3
    attempt_count: int = 0
    no_improvement_streak: int = 0
    child_goal_ids: list[str] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    stop_reason: str | None = None
    superseded: bool = False
    superseded_by: str | None = None
    is_active_conclusion: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["success_criteria"] = [c.to_dict() for c in self.success_criteria]
        return _compact_dict(payload)


@dataclass(slots=True)
class FailureClassification:
    classification_id: str
    goal_id: str
    attempt_id: str
    failure_reason: str
    reason_confidence: str
    suggested_playbook: str
    scope: str = "goal"  # goal | run | system
    iteration_category: str | None = None
    is_overflow: bool = False
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class PlaybookAction:
    playbook_action_id: str
    goal_id: str
    attempt_id: str
    playbook_id: str
    trigger_reason: str
    action_steps: list[str]
    expected_effect: str
    exit: str
    safety_constraints: list[str] = field(default_factory=list)
    output_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class ExperienceUpdate:
    update_id: str
    source_goal: str
    kind: str  # winning | failed | escalation
    promotion_level: str = PROMOTION_RUN
    confidence: str = "medium"
    review_status: str = "needs_review"
    winning_pattern: str | None = None
    failed_pattern: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class DefectEscalation:
    """Systematic-defect escalation counter row (技术方案 §7.4)."""

    failure_class: str
    scope: str
    occurrences: int
    playbook_success_rate: float
    no_gain_streak: int
    triggered: bool
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class GoalSummary:
    goal_id: str
    goal_type: str
    goal_name: str
    status: str
    succeeded: bool
    attempt_count: int
    is_active_goal: bool = False
    is_active_conclusion: bool = False
    superseded: bool = False
    superseded_by: str | None = None
    primary_failure_class: str | None = None
    stop_reason: str | None = None
    next_action: str | None = None
    experience_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


__all__ = [
    "GOAL_TYPE_MENU",
    "GOAL_TYPE_PAGE",
    "GOAL_TYPE_FEATURE",
    "GOAL_TYPES",
    "STATUS_PLANNED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED_MAX_ROUNDS",
    "STATUS_WAITING_HUMAN",
    "STATUS_BLOCKED_BY_POLICY",
    "STATUS_BLOCKED_BY_EXECUTOR",
    "STATUS_STOPPED_NO_PROGRESS",
    "STATUS_SUPERSEDED",
    "GOAL_STATUSES",
    "TERMINAL_STATUSES",
    "PAUSED_STATUSES",
    "ATTEMPT_RUNNING",
    "ATTEMPT_SUCCEEDED",
    "ATTEMPT_FAILED",
    "PROMOTION_RUN",
    "PROMOTION_PROJECT",
    "PROMOTION_PLATFORM",
    "SuccessCriterion",
    "EvidenceRef",
    "GoalStep",
    "GoalAttempt",
    "Goal",
    "FailureClassification",
    "PlaybookAction",
    "ExperienceUpdate",
    "DefectEscalation",
    "GoalSummary",
]
