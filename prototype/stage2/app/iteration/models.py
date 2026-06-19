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
class IterationSummary:
    run_id: str
    run_status: str
    outcome: str
    failure_cluster_count: int = 0
    retry_action_count: int = 0
    promotion_candidate_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class IterationArtifacts:
    summary: IterationSummary
    failure_clusters: list[FailureClusterRecord] = field(default_factory=list)
    retry_plan: RetryPlanRecord | None = None
    promotion_candidates: list[PromotionCandidateRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "failure_clusters": [cluster.to_dict() for cluster in self.failure_clusters],
            "retry_plan": self.retry_plan.to_dict() if self.retry_plan else None,
            "promotion_candidates": [
                candidate.to_dict() for candidate in self.promotion_candidates
            ],
        }
