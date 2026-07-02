"""Reuse bridge: project goal-loop records onto the existing iteration layer.

This module is the code-level enforcement of 技术方案 §2.6: goal-loop products are
*projections / renames* of the structures already implemented in
``prototype/stage2/app/iteration``, never a parallel second source of truth.

- ``FailureClassification`` -> ``iteration.FailureClusterRecord``
- ``PlaybookAction``        -> ``iteration.RetryAction`` / ``RetryPlanRecord``
- ``ExperienceUpdate``      -> ``iteration.PromotionCandidateRecord``
- ``GoalSummary``           -> a run-center current_status view

``MAPPING`` documents the same table in-code so it can be asserted by tests.
"""

from __future__ import annotations

from typing import Any

from prototype.stage2.app.iteration.builder import _action_level_for_category
from prototype.stage2.app.iteration.models import (
    FailureClusterRecord,
    PromotionCandidateRecord,
    RetryAction,
    RetryPlanRecord,
)

from .models import (
    STATUS_BLOCKED_BY_EXECUTOR,
    STATUS_BLOCKED_BY_POLICY,
    STATUS_FAILED_MAX_ROUNDS,
    STATUS_PLANNED,
    STATUS_RUNNING,
    STATUS_STOPPED_NO_PROGRESS,
    STATUS_SUCCEEDED,
    STATUS_SUPERSEDED,
    STATUS_WAITING_HUMAN,
    ExperienceUpdate,
    FailureClassification,
    GoalSummary,
    PlaybookAction,
    PROMOTION_PLATFORM,
    PROMOTION_PROJECT,
)


# new goal-loop product -> existing iteration structure it reuses.
MAPPING: dict[str, str] = {
    "failure_classifications": "iteration.FailureClusterRecord",
    "playbook_actions": "iteration.RetryAction/RetryPlanRecord",
    "experience_updates": "iteration.PromotionCandidateRecord",
    "goal_summary.current_status_view": "progress.CurrentStatusSnapshot",
}


def _cluster_id(failure_class: str) -> str:
    return f"goalloop::{failure_class}"


def failure_classification_to_cluster(
    record: FailureClassification, *, stage: str | None = None
) -> FailureClusterRecord:
    """Fold a fixed classification into the emergent aggregator's cluster shape."""

    category = record.iteration_category or "runtime"
    return FailureClusterRecord(
        cluster_id=_cluster_id(record.failure_reason),
        category=category,
        status="open",
        stage=stage,
        root_cause_hint=record.failure_reason,
        summary=f"{record.failure_reason} (confidence={record.reason_confidence})",
        signal_count=1,
        related_attempts=[record.attempt_id],
        recommendation=record.suggested_playbook,
        # reuse the iteration layer's own category->action_level derivation
        # instead of inventing a token from a different vocabulary.
        action_level=_action_level_for_category(category),
        # one dict per evidence ref, preserving the goal->attempt->evidence
        # parent pointer, matching the shape other cluster producers emit.
        evidence=[
            {"evidence_id": ref, "attempt_id": record.attempt_id}
            for ref in record.evidence_refs
        ],
    )


def playbook_action_to_retry_action(action: PlaybookAction) -> RetryAction:
    return RetryAction(
        action_id=action.playbook_action_id,
        cluster_id=_cluster_id(action.trigger_reason),
        title=action.playbook_id,
        priority="high" if action.exit in {"human", "stop", "escalate"} else "medium",
        owner="agent",
        strategy=" | ".join(action.action_steps),
        reason=action.trigger_reason,
        expected_outcome=action.expected_effect,
        execution_hints={
            "exit": action.exit,
            "action_steps": list(action.action_steps),
            "safety_constraints": list(action.safety_constraints),
        },
    )


def build_retry_plan(
    run_id: str, actions: list[PlaybookAction], *, goal: str | None = None
) -> RetryPlanRecord:
    return RetryPlanRecord(
        run_id=run_id,
        status="scheduled" if actions else "no_retry_needed",
        goal=goal or "Apply fixed playbooks and re-attempt the active goal.",
        actions=[playbook_action_to_retry_action(a) for a in actions],
    )


# goal-loop promotion layer -> iteration promotion_target vocabulary. The two
# are distinct axes: promotion_level is our run/project/platform layering, while
# the iteration summarizer buckets promotion_target on its own value set.
_PROMOTION_TARGET_BY_LEVEL: dict[str, str] = {
    PROMOTION_PLATFORM: "project_baseline_freeze",
    PROMOTION_PROJECT: "project_reference_baseline",
}


def experience_update_to_promotion_candidate(update: ExperienceUpdate) -> PromotionCandidateRecord:
    title = update.winning_pattern or update.failed_pattern or update.kind
    needs_manual = update.promotion_level == PROMOTION_PLATFORM
    return PromotionCandidateRecord(
        candidate_id=update.update_id,
        source=f"goal_loop:{update.source_goal}",
        title=title,
        promotion_level=update.promotion_level,
        status="candidate",
        reason=update.note or title,
        review_status=update.review_status,
        # map onto the iteration target vocabulary (None for run-local so the
        # summarizer records it as 'unspecified' rather than a foreign token).
        promotion_target=_PROMOTION_TARGET_BY_LEVEL.get(update.promotion_level),
        promotion_recommendation="manual_review" if needs_manual else "auto_record_candidate",
        needs_manual_review=needs_manual,
    )


# Map goal statuses onto the run center's ACTUAL overall_status vocabulary
# (from progress ProgressEvent.status): pending / running / completed / failed /
# skipped / waiting_human / blocked. No foreign tokens.
_OVERALL_STATUS_BY_GOAL_STATUS: dict[str, str] = {
    STATUS_PLANNED: "pending",
    STATUS_RUNNING: "running",
    STATUS_SUCCEEDED: "completed",
    STATUS_WAITING_HUMAN: "waiting_human",
    STATUS_BLOCKED_BY_POLICY: "blocked",
    STATUS_BLOCKED_BY_EXECUTOR: "blocked",
    STATUS_FAILED_MAX_ROUNDS: "failed",
    STATUS_STOPPED_NO_PROGRESS: "failed",
    STATUS_SUPERSEDED: "skipped",
}


def goal_summary_to_current_status_view(
    summary: GoalSummary,
    *,
    run_id: str,
    template_name: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Project a goal summary into a run-center consumable current_status view.

    Shaped to line up with ``progress.CurrentStatusSnapshot`` so the run center
    can display goal-level state without a new consumer.
    """

    overall_status = _OVERALL_STATUS_BY_GOAL_STATUS.get(summary.status, "pending")
    waiting_reason = summary.stop_reason if summary.status == STATUS_WAITING_HUMAN else None
    blocked_reason = (
        summary.stop_reason
        if summary.status in {STATUS_BLOCKED_BY_POLICY, STATUS_BLOCKED_BY_EXECUTOR}
        else None
    )
    return {
        "run_id": run_id,
        "template_name": template_name,
        "model_name": model_name,
        "overall_status": overall_status,
        "goal_status": summary.status,
        "current_target": {
            "kind": summary.goal_type,
            "id": summary.goal_id,
            "label": summary.goal_name,
        },
        "latest_message": summary.experience_note
        or (f"{summary.goal_name}: {summary.status}"),
        "waiting_reason": waiting_reason,
        "blocked_reason": blocked_reason,
        "next_action": summary.next_action,
        "stats": {
            "attempt_count": summary.attempt_count,
            "succeeded": summary.succeeded,
            "primary_failure_class": summary.primary_failure_class,
            "is_active_conclusion": summary.is_active_conclusion,
            "superseded": summary.superseded,
        },
    }


__all__ = [
    "MAPPING",
    "failure_classification_to_cluster",
    "playbook_action_to_retry_action",
    "build_retry_plan",
    "experience_update_to_promotion_candidate",
    "goal_summary_to_current_status_view",
]
