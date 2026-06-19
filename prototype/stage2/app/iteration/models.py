from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


@dataclass(slots=True)
class IterationBuildInput:
    run_report: Any = None
    status_snapshot: Any = None
    attempts: list[Any] = field(default_factory=list)
    previous_iteration: Any = None
    max_attempts: int | None = None


@dataclass(slots=True)
class FailureClusterRecord:
    cluster_id: str
    category: str
    status: str
    stage: str | None = None
    root_cause_hint: str | None = None
    summary: str | None = None
    signal_count: int = 0
    related_attempts: list[str] = field(default_factory=list)
    related_items: list[str] = field(default_factory=list)
    recommendation: str | None = None
    action_level: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class RetryAction:
    action_id: str
    cluster_id: str
    title: str
    priority: str = "medium"
    stage: str | None = None
    owner: str = "agent"
    strategy: str | None = None
    reason: str | None = None
    expected_outcome: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class RetryPlanRecord:
    run_id: str
    status: str
    next_round: int | None = None
    goal: str | None = None
    stop_reason: str | None = None
    actions: list[RetryAction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["actions"] = [action.to_dict() for action in self.actions]
        return _compact_dict(payload)


@dataclass(slots=True)
class PromotionCandidateRecord:
    candidate_id: str
    source: str
    title: str
    promotion_level: str
    status: str
    reason: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class ComparisonMetricRecord:
    metric_id: str
    label: str
    current_value: Any = None
    previous_value: Any = None
    delta: Any = None
    trend: str = "unknown"
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class FailureClusterChangeRecord:
    cluster_key: str
    category: str
    status: str
    stage: str | None = None
    previous_signal_count: int = 0
    current_signal_count: int = 0
    signal_delta: int = 0
    previous_cluster_ids: list[str] = field(default_factory=list)
    current_cluster_ids: list[str] = field(default_factory=list)
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class IterationComparisonRecord:
    current_run_id: str
    status: str
    previous_run_id: str | None = None
    improvement_judgement: str = "unknown"
    summary: str | None = None
    metrics: list[ComparisonMetricRecord] = field(default_factory=list)
    cluster_changes: list[FailureClusterChangeRecord] = field(default_factory=list)
    no_improvement_streak_before: int = 0
    no_improvement_streak_after: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metrics"] = [metric.to_dict() for metric in self.metrics]
        payload["cluster_changes"] = [change.to_dict() for change in self.cluster_changes]
        return _compact_dict(payload)


@dataclass(slots=True)
class StopConditionRecord:
    condition_id: str
    condition_type: str
    status: str
    summary: str | None = None
    stop: bool | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class StopDecisionRecord:
    run_id: str
    status: str
    should_stop: bool | None = None
    primary_reason: str | None = None
    triggered_conditions: list[str] = field(default_factory=list)
    no_improvement_streak: int = 0
    conditions: list[StopConditionRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["conditions"] = [condition.to_dict() for condition in self.conditions]
        return _compact_dict(payload)


@dataclass(slots=True)
class NextRoundDecisionRecord:
    run_id: str
    status: str
    should_start_next_round: bool | None = None
    current_round: int | None = None
    next_round: int | None = None
    max_attempts: int | None = None
    remaining_attempt_budget: int | None = None
    target_stage: str | None = None
    primary_reason: str | None = None
    stop_reason: str | None = None
    improvement_judgement: str | None = None
    new_failure_cluster_count: int = 0
    repeated_no_gain_cluster_count: int = 0
    regressed_cluster_count: int = 0
    resolved_cluster_count: int = 0
    scheduled_cluster_ids: list[str] = field(default_factory=list)
    scheduled_action_ids: list[str] = field(default_factory=list)
    deferred_cluster_ids: list[str] = field(default_factory=list)
    triggered_stop_conditions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class IterationSummary:
    run_id: str
    run_status: str
    outcome: str
    failure_cluster_count: int = 0
    retry_action_count: int = 0
    promotion_candidate_count: int = 0
    stop_status: str | None = None
    comparison_status: str | None = None
    comparison_outcome: str | None = None
    next_round_status: str | None = None
    next_round: int | None = None
    next_round_should_start: bool | None = None
    triggered_stop_conditions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class IterationArtifacts:
    summary: IterationSummary
    failure_clusters: list[FailureClusterRecord] = field(default_factory=list)
    retry_plan: RetryPlanRecord | None = None
    promotion_candidates: list[PromotionCandidateRecord] = field(default_factory=list)
    stop_conditions: StopDecisionRecord | None = None
    iteration_comparison: IterationComparisonRecord | None = None
    next_round_decision: NextRoundDecisionRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "failure_clusters": [cluster.to_dict() for cluster in self.failure_clusters],
            "retry_plan": self.retry_plan.to_dict() if self.retry_plan else None,
            "promotion_candidates": [
                candidate.to_dict() for candidate in self.promotion_candidates
            ],
            "stop_conditions": self.stop_conditions.to_dict() if self.stop_conditions else None,
            "iteration_comparison": (
                self.iteration_comparison.to_dict()
                if self.iteration_comparison
                else None
            ),
            "next_round_decision": (
                self.next_round_decision.to_dict()
                if self.next_round_decision
                else None
            ),
        }
