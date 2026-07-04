"""
round_analysis.json / next_round_plan.json writers for Stage E.

Per 技术方案 §2.6 these are NOT a new parallel schema: ``round_analysis.json``
projects the goal loop's own failure classifications (via
``goal_loop.compat.failure_classification_to_cluster``, the same bridge
Stage A already uses onto ``iteration.FailureClusterRecord``) plus the
systematic-defect escalation counter (§7.4); ``next_round_plan.json``
directly instantiates the EXISTING ``iteration.NextRoundDecisionRecord``
dataclass and only adds the ``next_goal`` / ``target_ids`` fields the
mapping table calls for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_adapter import ExecutionAdapter

from ..goal_loop import compat, playbook as pb
from ..goal_loop.models import PAUSED_STATUSES, STATUS_SUCCEEDED, TERMINAL_STATUSES
from prototype.stage2.app.iteration.models import NextRoundDecisionRecord


def _safe_json_write(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _execution_goals(engine: "GoalLoopEngine") -> list:
    return [
        goal
        for goal in engine.goals.values()
        if goal.origin and goal.origin.startswith("feature_execution::")
    ]


def _evidence_quality(engine: "GoalLoopEngine", goals: list) -> dict[str, Any]:
    complete = 0
    with_gaps = 0
    gap_examples: list[str] = []
    for goal in goals:
        for attempt in engine.attempts:
            if attempt.goal_id != goal.goal_id:
                continue
            gaps = engine.check_evidence_complete(attempt.attempt_id)
            if gaps:
                with_gaps += 1
                gap_examples.extend(gaps[:1])
            else:
                complete += 1
    return {
        "attempts_with_complete_evidence": complete,
        "attempts_with_evidence_gaps": with_gaps,
        "evidence_gap_examples": gap_examples[:5],
    }


def _scoped_escalations(
    engine: "GoalLoopEngine", execution_goal_ids: set[str], thresholds
) -> list[dict[str, Any]]:
    """Recompute escalation counters restricted to THIS run's execution
    goals only.

    ``engine.evaluate_escalations()`` reads ``engine._defect_counter``, which
    is a single run-wide dict keyed only by ``failure_class`` with no
    goal-origin scoping (adversarial review finding: on a shared engine,
    another stage's failures of the same class silently inflate Stage E's
    occurrence/streak counts). Recomputing from ``engine.classifications``
    filtered to ``execution_goal_ids`` — the same filter already applied to
    ``failure_clusters`` in this module — keeps the escalation view honestly
    scoped to what Stage E itself actually observed.
    """

    occurrences: dict[str, int] = {}
    resolved: dict[str, int] = {}
    for record in engine.classifications:
        if record.goal_id not in execution_goal_ids:
            continue
        occurrences[record.failure_reason] = occurrences.get(record.failure_reason, 0) + 1

    for goal_id in execution_goal_ids:
        goal = engine.goals.get(goal_id)
        if goal is None or goal.status != STATUS_SUCCEEDED:
            continue
        for attempt in reversed(engine.attempts):
            if attempt.goal_id == goal_id and attempt.failure_class:
                resolved[attempt.failure_class] = resolved.get(attempt.failure_class, 0) + 1
                break

    rows: list[dict[str, Any]] = []
    for failure_class, count in sorted(occurrences.items()):
        resolved_count = resolved.get(failure_class, 0)
        success_rate = (resolved_count / count) if count else 0.0
        triggered = (
            count >= thresholds.escalation_occurrence_threshold
            and success_rate <= thresholds.escalation_success_floor
        )
        rows.append(
            {
                "failure_class": failure_class,
                "scope": "execution_goal",
                "occurrences": count,
                "playbook_success_rate": round(success_rate, 4),
                "triggered": triggered,
                "recommendation": (
                    f"escalate {failure_class} to programming model: "
                    f"{count} occurrences with success_rate {success_rate:.2f}"
                    if triggered
                    else None
                ),
            }
        )
    return [row for row in rows if row["triggered"]]


def resolve_retryable_test_cases(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    *,
    goal_ids: "set[str] | None" = None,
) -> dict[str, Any]:
    """Decide which failed execution goals are safe to auto-retry next round.

    Only goals whose last attempt's failure_class maps to a playbook with
    ``exit == EXIT_RETRY`` are retryable (方案 §13: exit=retry means the goal
    loop itself schedules a fresh attempt, distinct from exit=human/stop/
    escalate/degrade, which must NOT be silently retried without a human
    clearing the blocker first). Paused goals (``PAUSED_STATUSES``) are never
    included here for the same reason, even though some paused stop_reasons
    look superficially similar to a retryable failure_class.

    Args:
        goal_ids: restrict the scan to THESE execution goals only (typically
            "the goals registered in the round that just ran"). Without
            this, a multi-round caller re-scanning the whole engine would
            keep re-surfacing round 1's already-superseded failed goal
            alongside round 2's fresh outcome for the SAME test_case — the
            engine never deletes/supersedes a prior round's terminal goal,
            it just accumulates a new one alongside it (round_index-suffixed
            origin, see ``execution_adapter.register_execution_goal``).
            Pass ``None`` only for a single-round/whole-engine snapshot use
            (e.g. ad-hoc inspection), never from a multi-round loop.

    Returns:
        ``{"retryable": [{"test_case": ..., "failure_class": ..., "playbook_id": ...,
        "source_goal_id": ...}, ...], "blocked_reasons": [str, ...]}`` — the
        retryable list feeds directly into the next round's
        ``load_test_cases_from_list``, no goal_id/cluster_id indirection.
    """

    goals = _execution_goals(engine)
    if goal_ids is not None:
        goals = [g for g in goals if g.goal_id in goal_ids]
    failed_goals = [g for g in goals if g.status in TERMINAL_STATUSES and g.status != STATUS_SUCCEEDED]
    paused_goals = [g for g in goals if g.status in PAUSED_STATUSES]

    retryable: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []

    for goal in failed_goals:
        last_attempt = engine.last_attempt_for(goal.goal_id)
        failure_class = last_attempt.failure_class if last_attempt else None
        context = adapter.get_execution_context(goal.goal_id)
        if context is None:
            continue
        spec = pb.select_playbook(failure_class or "")
        if spec.exit == pb.EXIT_RETRY:
            retryable.append(
                {
                    "test_case": context["test_case"],
                    "failure_class": failure_class,
                    "playbook_id": spec.playbook_id,
                    "source_goal_id": goal.goal_id,
                }
            )
        else:
            blocked_reasons.append(
                f"goal {goal.goal_id} (test_case={context.get('test_case_id')}) failure_class="
                f"{failure_class!r} has playbook exit={spec.exit!r}, not auto-retryable."
            )

    for goal in paused_goals:
        context = adapter.get_execution_context(goal.goal_id)
        test_case_id = context.get("test_case_id") if context else None
        blocked_reasons.append(
            f"goal {goal.goal_id} (test_case={test_case_id}) is paused (status={goal.status!r}); "
            "requires human takeover resolution before any retry."
        )

    return {"retryable": retryable, "blocked_reasons": blocked_reasons}


def write_round_analysis(
    engine: "GoalLoopEngine",
    run_id: str,
    output_path: str | Path,
) -> Path:
    goals = _execution_goals(engine)

    coverage = {
        "total_execution_goals": len(goals),
        "succeeded": sum(1 for g in goals if g.status == STATUS_SUCCEEDED),
        "failed": sum(1 for g in goals if g.status in TERMINAL_STATUSES and g.status != STATUS_SUCCEEDED),
        "paused": sum(1 for g in goals if g.status in PAUSED_STATUSES),
        "pending": sum(
            1
            for g in goals
            if g.status not in TERMINAL_STATUSES and g.status not in PAUSED_STATUSES
        ),
    }

    execution_goal_ids = {g.goal_id for g in goals}
    failure_clusters = [
        compat.failure_classification_to_cluster(record, stage="execution").to_dict()
        for record in engine.classifications
        if record.goal_id in execution_goal_ids
    ]
    escalations = _scoped_escalations(engine, execution_goal_ids, engine.thresholds)

    if any(g.status in PAUSED_STATUSES for g in goals):
        suggestion = "存在暂停中的执行目标，需人工处理后才能继续下一轮。"
    elif coverage["failed"] > 0:
        suggestion = "存在失败的执行目标，建议在下一轮重试或升级评审。"
    else:
        suggestion = "本轮执行目标已全部完成，可继续推进下一阶段目标。"

    payload = {
        "schema_version": "stage2_execution_round_analysis.v1",
        "run_id": run_id,
        "coverage": coverage,
        "evidence_quality": _evidence_quality(engine, goals),
        "failure_clusters": failure_clusters,
        "escalations": escalations,
        "next_round_suggestion": suggestion,
    }
    return _safe_json_write(output_path, payload)


def write_next_round_plan(
    engine: "GoalLoopEngine",
    run_id: str,
    output_path: str | Path,
    *,
    decision_alias_path: str | Path | None = None,
) -> Path:
    """Write next_round_plan.json (方案 §2.6's mandated artifact name).

    Also writes an identical copy to ``decision_alias_path`` when given, so
    the EXISTING run-center reader (orchestration.session_artifacts, which
    reads ``run_dir / "next_round_decision.json"`` — a different filename
    that predates this module) sees the same decision without requiring a
    second, divergent schema. This is a filename alias, not a new source of
    truth: both paths get byte-identical content from the same
    NextRoundDecisionRecord.
    """

    goals = _execution_goals(engine)
    paused_goals = [g for g in goals if g.status in PAUSED_STATUSES]
    failed_goals = [g for g in goals if g.status in TERMINAL_STATUSES and g.status != STATUS_SUCCEEDED]

    scheduled_action_ids = [
        action.playbook_action_id
        for action in engine.playbook_action_records
        if action.goal_id in {g.goal_id for g in failed_goals}
    ]
    failed_goal_ids = {g.goal_id for g in failed_goals}
    scheduled_cluster_ids = [
        compat.failure_classification_to_cluster(record).cluster_id
        for record in engine.classifications
        if record.goal_id in failed_goal_ids
    ]

    if paused_goals:
        status = "needs_review"
        should_start_next_round = None
        last_attempt = engine.last_attempt_for(paused_goals[0].goal_id)
        blocking_reason = (last_attempt.failure_class if last_attempt else None) or paused_goals[0].stop_reason or "waiting_human"
        primary_reason = f"存在暂停中的执行目标，需人工处理：{blocking_reason}。"
        target_stage = "execution"
        target_ids = [g.goal_id for g in paused_goals]
    elif failed_goals:
        status = "scheduled"
        should_start_next_round = True
        primary_reason = "存在未通过基础路径验证的执行目标，需要在下一轮重试。"
        target_stage = "execution"
        target_ids = [g.goal_id for g in failed_goals]
    else:
        status = "no_retry_needed"
        should_start_next_round = False
        primary_reason = "本轮所有执行目标均已成功完成，无需重试。"
        target_stage = None
        target_ids = []

    record = NextRoundDecisionRecord(
        run_id=run_id,
        status=status,
        should_start_next_round=should_start_next_round,
        target_stage=target_stage,
        primary_reason=primary_reason,
        scheduled_cluster_ids=sorted(set(scheduled_cluster_ids)),
        scheduled_action_ids=scheduled_action_ids,
        deferred_cluster_ids=[],
        notes=[
            "Derived directly from goal_loop execution-goal state (技术方案 §2.6): "
            "no separate iteration re-classification pass was needed because "
            "the goal loop's fixed classifier already produced stable labels.",
        ],
    )
    payload = record.to_dict()
    payload["next_goal"] = target_ids[0] if target_ids else None
    payload["target_ids"] = target_ids
    written_path = _safe_json_write(output_path, payload)
    if decision_alias_path is not None:
        _safe_json_write(decision_alias_path, payload)
    return written_path


__all__ = ["resolve_retryable_test_cases", "write_round_analysis", "write_next_round_plan"]
