"""
execution_results.json / action_log.jsonl / network_events.json /
screenshots_index.json writers for Stage E.

Every writer is a straight projection of engine state (attempts / steps /
evidence) or of the outcomes list the orchestrator already produced — no
new parallel truth is invented, per 技术方案 §2.6.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_runner import ExecutionOutcome


def _safe_json_write(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def write_execution_results(outcomes: list["ExecutionOutcome"], output_path: str | Path) -> Path:
    """Write execution_results.json: one record per executed test case."""

    payload = {
        "schema_version": "stage2_execution_results.v1",
        "count": len(outcomes),
        "items": [outcome.to_dict() for outcome in outcomes],
    }
    return _safe_json_write(output_path, payload)


def write_action_log(engine: "GoalLoopEngine", output_path: str | Path) -> Path:
    """Write action_log.jsonl: one line per recorded action step.

    Each line carries the full goal -> attempt -> step -> evidence chain
    (方案 §5.7), so an action can always be traced back to the case and
    goal it belongs to without a separate index.
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for attempt in engine.attempts:
            goal = engine.goals.get(attempt.goal_id)
            if not goal or not goal.origin or not goal.origin.startswith("feature_execution::"):
                continue
            for step in attempt.steps:
                if step.kind != "action":
                    continue
                for evidence_id in step.evidence_ids:
                    evidence = engine.evidence.get(evidence_id)
                    action_record: dict[str, Any] = {}
                    if evidence and evidence.note:
                        try:
                            action_record = json.loads(evidence.note)
                        except json.JSONDecodeError:
                            action_record = {}
                    entry = {
                        "goal_id": goal.goal_id,
                        "attempt_id": attempt.attempt_id,
                        "step_id": step.step_id,
                        "evidence_id": evidence_id,
                        "test_case_id": goal.origin.split("::", 1)[-1],
                        "action": action_record.get("action") or step.action,
                        "status": action_record.get("status") or step.status,
                        "duration_ms": action_record.get("duration_ms"),
                        "result": action_record.get("result"),
                    }
                    json.dump(entry, f, ensure_ascii=False)
                    f.write("\n")

    return output_path


def write_network_events(outcomes: list["ExecutionOutcome"], output_path: str | Path) -> Path:
    """Write network_events.json.

    In fixture-simulated mode this honestly reports that capture was not
    applicable rather than fabricating request/response pairs — see
    execution_runner module docstring.
    """

    items: list[dict[str, Any]] = []
    any_captured = False
    for outcome in outcomes:
        if outcome.network_events:
            any_captured = True
        for event in outcome.network_events:
            items.append({"test_case_id": outcome.test_case_id, "goal_id": outcome.goal_id, **event})

    payload = {
        "schema_version": "stage2_execution_network_events.v1",
        "capture_status": "captured" if any_captured else "not_applicable_fixture_mode",
        "count": len(items),
        "items": items,
    }
    return _safe_json_write(output_path, payload)


def write_screenshots_index(outcomes: list["ExecutionOutcome"], output_path: str | Path) -> Path:
    """Write screenshots_index.json.

    Only real screenshot references (attached via
    ``ExecutionAdapter.record_screenshot`` with a genuine file path) appear
    here. Fixture-simulated outcomes carry no screenshot_refs, so this is
    honestly empty rather than pointing at files that don't exist.
    """

    items: list[dict[str, Any]] = []
    for outcome in outcomes:
        for ref in outcome.screenshot_refs:
            items.append({"test_case_id": outcome.test_case_id, "goal_id": outcome.goal_id, **ref})

    payload = {
        "schema_version": "stage2_execution_screenshots_index.v1",
        "count": len(items),
        "items": items,
    }
    if not items:
        payload["notes"] = [
            "No screenshots were captured. Executions in this run used execution_mode="
            "'fixture_simulated', which does not fabricate screenshot files.",
        ]
    return _safe_json_write(output_path, payload)


__all__ = [
    "write_execution_results",
    "write_action_log",
    "write_network_events",
    "write_screenshots_index",
]
