"""
human_tasks.json / human_takeover.json writers for Stage E.

Both files project the SAME two sources of truth: (1) the goal loop's paused
execution goals (``status in PAUSED_STATUSES`` — waiting_human /
blocked_by_policy / blocked_by_executor, per ``goal_loop.models``), and (2)
outcomes that PASSED their basic path but still carry
``requires_human_authorization=True`` (an ``entry_confirmation`` case: its
entry point was confirmed visible, but the real high-risk action itself was
deliberately never attempted — see ``execution_runner`` module docstring).
Both are real, open human-facing tasks; only scanning paused goals would
silently drop every high-risk-entry-confirmed case, since a "passed"
entry_confirmation goal is TERMINAL (STATUS_SUCCEEDED), never paused.

Two files exist because two different consumers already read two different
shapes (方案 §2.6 兼容期条款):

- ``human_tasks.json`` mirrors the ``v3_orchestrator._build_human_tasks``
  shape (schema_version/tasks list) that the run center's human-task view
  already expects.
- ``human_takeover.json`` mirrors the packet shape
  ``orchestration.session_artifacts._load_run_session_record`` already
  reads (status/target_stage/waiting_reason/pending_actions/resume_command)
  — that reader exists today with NO current producer (confirmed by
  investigation), so this closes that gap for goal-loop-driven runs.

Only written when there is at least one real open item (a paused goal, or a
passed-but-unauthorized entry_confirmation); an empty/all-clear run should
not fabricate a takeover packet that implies a real block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_adapter import ExecutionAdapter
    from .execution_runner import ExecutionOutcome

from ..goal_loop.models import PAUSED_STATUSES
from ..goal_loop.playbook import PLAYBOOK_TABLE

# Fixed failure_class -> human task type, derived from each class's OWN
# playbook action (goal_loop/playbook.py), not collapsed to one label. Only
# blocked_by_safety_policy's playbook actually says "raise a high-risk
# authorization task" — lumping assertion_failed / missing_prerequisite_data
# / permission_blocked under the same label would mislead an operator about
# what kind of action is actually being asked of them (adversarial review
# finding: task type collapse).
_TASK_TYPE_BY_FAILURE_CLASS: dict[str, str] = {
    "login_required": "login_handoff",
    "permission_blocked": "permission_grant",
    "assertion_failed": "defect_confirmation",
    "missing_prerequisite_data": "prerequisite_data_request",
    "blocked_by_safety_policy": "high_risk_authorization",
}
_DEFAULT_TASK_TYPE = "manual_review"

# The one real resume path that exists and is wired up (prototype/stage2/main.py
# --resume-human-takeover, dispatching to resume_human_takeover_entrypoint).
# The goal-loop side has no CLI wrapper of its own yet, so this packet points
# at the SAME command the existing iteration-pipeline packets already use
# (tools/suyuan_submit_loop.py's build_human_takeover_packet) rather than a
# module that was never created.
_RESUME_COMMAND_TEMPLATE = 'python -m prototype.stage2.main --resume-human-takeover "{run_dir}"'


def _safe_json_write(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _paused_execution_goals(engine: "GoalLoopEngine") -> list:
    return [
        goal
        for goal in engine.goals.values()
        if goal.origin
        and goal.origin.startswith("feature_execution::")
        and goal.status in PAUSED_STATUSES
    ]


def _task_type_for_failure_class(failure_class: str | None) -> str:
    if failure_class in _TASK_TYPE_BY_FAILURE_CLASS:
        return _TASK_TYPE_BY_FAILURE_CLASS[failure_class]
    # Fall back to the playbook's own exit if we ever see a class outside the
    # fixed map (should not happen since only HUMAN_REQUIRED_CLASSES pause a
    # goal), rather than silently mislabeling it as high_risk_authorization.
    spec = PLAYBOOK_TABLE.get(failure_class) if failure_class else None
    if spec is not None:
        return spec.playbook_id
    return _DEFAULT_TASK_TYPE


def write_human_tasks(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    outcomes: list["ExecutionOutcome"],
    run_id: str,
    output_path: str | Path,
) -> Path:
    tasks: list[dict[str, Any]] = []

    for goal in _paused_execution_goals(engine):
        context = adapter.get_execution_context(goal.goal_id) or {}
        last_attempt = engine.last_attempt_for(goal.goal_id)
        failure_class = last_attempt.failure_class if last_attempt else None
        tasks.append(
            {
                "task_id": f"human-task-{goal.goal_id}",
                "goal_id": goal.goal_id,
                "type": _task_type_for_failure_class(failure_class),
                "status": "open",
                "title": f"确认 {context.get('feature_id', goal.goal_id)} 的执行结果",
                "test_case_id": context.get("test_case_id"),
                "page_id": context.get("page_id"),
                "risk_level": context.get("risk_level"),
                "stop_reason": goal.stop_reason,
                "failure_class": failure_class,
                "blocks_next_round": True,
            }
        )

    for outcome in outcomes:
        if not outcome.requires_human_authorization:
            continue
        context = adapter.get_execution_context(outcome.goal_id) if outcome.goal_id else None
        context = context or {}
        tasks.append(
            {
                "task_id": f"human-task-authorize-{outcome.goal_id}",
                "goal_id": outcome.goal_id,
                "type": "high_risk_action_authorization",
                "status": "open",
                "title": f"授权执行 {outcome.feature_id} 的真实高风险动作",
                "test_case_id": outcome.test_case_id,
                "page_id": outcome.page_id,
                "risk_level": context.get("risk_level"),
                "stop_reason": None,
                "failure_class": None,
                "blocks_next_round": False,
            }
        )

    payload = {
        "schema_version": "stage2_execution_human_tasks.v1",
        "run_id": run_id,
        "open_task_count": len(tasks),
        "tasks": tasks,
        "notes": (
            ["No goal is currently paused for human review, and no high-risk action is pending authorization."]
            if not tasks
            else ["Resolve each task, then resume the run to continue the next round."]
        ),
    }
    return _safe_json_write(output_path, payload)


def write_human_takeover(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    outcomes: list["ExecutionOutcome"],
    run_id: str,
    run_dir: str | Path,
    output_path: str | Path,
) -> Path | None:
    """Write human_takeover.json, or return None if nothing is open.

    Mirrors the packet shape ``session_artifacts._load_run_session_record``
    already reads: ``status``, ``target_stage``, ``waiting_reason``,
    ``pending_actions``, ``resume_command``, ``notes``.
    """

    paused_goals = _paused_execution_goals(engine)
    pending_authorizations = [outcome for outcome in outcomes if outcome.requires_human_authorization]
    if not paused_goals and not pending_authorizations:
        return None

    pending_actions: list[dict[str, Any]] = []
    for goal in paused_goals:
        context = adapter.get_execution_context(goal.goal_id) or {}
        last_attempt = engine.last_attempt_for(goal.goal_id)
        failure_class = last_attempt.failure_class if last_attempt else None
        pending_actions.append(
            {
                "goal_id": goal.goal_id,
                "test_case_id": context.get("test_case_id"),
                "feature_id": context.get("feature_id"),
                "stop_reason": goal.stop_reason,
                "failure_class": failure_class,
                "risk_level": context.get("risk_level"),
                "action_kind": "resolve_pause",
            }
        )
    for outcome in pending_authorizations:
        context = adapter.get_execution_context(outcome.goal_id) if outcome.goal_id else None
        context = context or {}
        pending_actions.append(
            {
                "goal_id": outcome.goal_id,
                "test_case_id": outcome.test_case_id,
                "feature_id": outcome.feature_id,
                "stop_reason": None,
                "failure_class": None,
                "risk_level": context.get("risk_level"),
                "action_kind": "authorize_high_risk_action",
            }
        )

    # waiting_reason carries the actual failure_class of the first paused
    # goal when one exists (distinguishing a safety-policy block from a
    # routine login pause), falling back to the generic stop_reason only
    # when neither is available, and to the authorization action when the
    # only open items are pending high-risk authorizations.
    if paused_goals:
        first_action = pending_actions[0]
        waiting_reason = first_action.get("failure_class") or first_action.get("stop_reason") or "waiting_human"
    else:
        waiting_reason = "high_risk_action_authorization_pending"

    payload = {
        "schema_version": "stage2_execution_human_takeover.v1",
        "status": "waiting_human",
        "run_id": run_id,
        "target_stage": "execution",
        "waiting_reason": waiting_reason,
        "pending_actions": pending_actions,
        "resume_command": _RESUME_COMMAND_TEMPLATE.format(run_dir=run_dir),
        "notes": [
            "Complete the required human review or authorization for each pending action.",
            "Then resume the run to continue the goal loop from where it paused.",
        ],
    }
    return _safe_json_write(output_path, payload)


__all__ = ["write_human_tasks", "write_human_takeover"]
