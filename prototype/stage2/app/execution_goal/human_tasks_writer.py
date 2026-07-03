"""
human_tasks.json / human_takeover.json writers for Stage E.

Both files are projections of the SAME source of truth: the goal loop's
paused execution goals (``status in PAUSED_STATUSES`` — waiting_human /
blocked_by_policy / blocked_by_executor, per ``goal_loop.models``). Two
files exist because two different consumers already read two different
shapes (方案 §2.6 兼容期条款):

- ``human_tasks.json`` mirrors the ``v3_orchestrator._build_human_tasks``
  shape (schema_version/tasks list) that the run center's human-task view
  already expects.
- ``human_takeover.json`` mirrors the packet shape
  ``orchestration.session_artifacts._load_run_session_record`` already
  reads (status/target_stage/waiting_reason/pending_actions/resume_command)
  — that reader exists today with NO current producer (confirmed by
  investigation), so this closes that gap for goal-loop-driven runs.

Only written when at least one execution goal is actually paused; an empty
run should not fabricate a takeover packet that implies a real block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_adapter import ExecutionAdapter

from ..goal_loop.models import PAUSED_STATUSES


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


def write_human_tasks(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    run_id: str,
    output_path: str | Path,
) -> Path:
    tasks: list[dict[str, Any]] = []
    for goal in _paused_execution_goals(engine):
        context = adapter.get_execution_context(goal.goal_id) or {}
        last_attempt = engine.last_attempt_for(goal.goal_id)
        failure_class = last_attempt.failure_class if last_attempt else None
        task_type = "login_handoff" if failure_class == "login_required" else "high_risk_authorization"
        tasks.append(
            {
                "task_id": f"human-task-{goal.goal_id}",
                "goal_id": goal.goal_id,
                "type": task_type,
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

    payload = {
        "schema_version": "stage2_execution_human_tasks.v1",
        "run_id": run_id,
        "open_task_count": len(tasks),
        "tasks": tasks,
        "notes": (
            ["No goal is currently paused for human review."]
            if not tasks
            else ["Resolve each task, then resume the run to continue the next round."]
        ),
    }
    return _safe_json_write(output_path, payload)


def write_human_takeover(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    run_id: str,
    run_dir: str | Path,
    output_path: str | Path,
) -> Path | None:
    """Write human_takeover.json, or return None if nothing is paused.

    Mirrors the packet shape ``session_artifacts._load_run_session_record``
    already reads: ``status``, ``target_stage``, ``waiting_reason``,
    ``pending_actions``, ``resume_command``, ``notes``.
    """

    paused_goals = _paused_execution_goals(engine)
    if not paused_goals:
        return None

    pending_actions: list[dict[str, Any]] = []
    for goal in paused_goals:
        context = adapter.get_execution_context(goal.goal_id) or {}
        pending_actions.append(
            {
                "goal_id": goal.goal_id,
                "test_case_id": context.get("test_case_id"),
                "feature_id": context.get("feature_id"),
                "stop_reason": goal.stop_reason,
                "risk_level": context.get("risk_level"),
            }
        )

    resume_command = (
        "python -m prototype.stage2.app.execution_goal.resume "
        f'--run-dir "{run_dir}"'
    )
    payload = {
        "schema_version": "stage2_execution_human_takeover.v1",
        "status": "waiting_human",
        "run_id": run_id,
        "target_stage": "execution",
        "waiting_reason": paused_goals[0].stop_reason,
        "pending_actions": pending_actions,
        "resume_command": resume_command,
        "notes": [
            "Complete the required human review or authorization for each pending action.",
            "Then resume the run to continue the goal loop from where it paused.",
        ],
    }
    return _safe_json_write(output_path, payload)


__all__ = ["write_human_tasks", "write_human_takeover"]
