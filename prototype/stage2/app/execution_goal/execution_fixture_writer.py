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
    from .execution_adapter import ExecutionAdapter
    from .execution_runner import ExecutionOutcome


def _safe_json_write(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def write_execution_results(outcomes: list["ExecutionOutcome"], output_path: str | Path) -> Path:
    """Write execution_results.json: one record per executed test case.

    Includes a ``results`` key (identical to ``items``) as an alias for the
    older v3_orchestrator pipeline's execution_results.json shape
    (``{schema_version, results, items}``), which uses the same conventional
    filename with a different producer. The two pipelines are not currently
    wired to share a run directory, but should that change, a consumer keyed
    on either ``results`` or ``items`` still finds its data instead of
    hitting a KeyError against whichever producer wrote last.
    """

    items = [outcome.to_dict() for outcome in outcomes]
    payload = {
        "schema_version": "stage2_execution_results.v1",
        "count": len(outcomes),
        "items": items,
        "results": items,
    }
    return _safe_json_write(output_path, payload)


def write_action_log(
    engine: "GoalLoopEngine", adapter: "ExecutionAdapter", output_path: str | Path
) -> Path:
    """Write action_log.jsonl: one line per recorded action step.

    Each line carries the full goal -> attempt -> step -> evidence chain
    (方案 §5.7), so an action can always be traced back to the case and
    goal it belongs to without a separate index. ``test_case_id`` is read
    from the adapter's execution context (the same source
    execution_results.json uses), NOT parsed out of ``goal.origin`` — origin
    only ever encodes ``feature_id`` (see ``ExecutionAdapter.register_execution_goal``),
    so splitting it can never recover the real test_case_id.
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for attempt in engine.attempts:
            goal = engine.goals.get(attempt.goal_id)
            if not goal or not goal.origin or not goal.origin.startswith("feature_execution::"):
                continue
            context = adapter.get_execution_context(goal.goal_id) or {}
            test_case_id = context.get("test_case_id") or goal.origin.split("::", 1)[-1]
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
                        "test_case_id": test_case_id,
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

    ``capture_status`` reflects whether a REAL capture attempt was made
    (``outcome.execution_mode``), not whether any events happened to be
    captured — a real_browser outcome that legitimately observed zero
    network traffic must not be relabeled as "not applicable". Only when
    every outcome is fixture-simulated (no live browser was ever driven) is
    ``not_applicable_fixture_mode`` accurate.
    """

    items: list[dict[str, Any]] = []
    any_real_browser = False
    for outcome in outcomes:
        if outcome.execution_mode != "fixture_simulated":
            any_real_browser = True
        for event in outcome.network_events:
            # explicit precedence: outcome identity fields are added AFTER
            # the event payload so they can never be silently overwritten by
            # a same-named key inside event (dict-literal unpacking has no
            # such protection when identity fields come first).
            items.append({**event, "test_case_id": outcome.test_case_id, "goal_id": outcome.goal_id})

    payload = {
        "schema_version": "stage2_execution_network_events.v1",
        "capture_status": "captured" if any_real_browser else "not_applicable_fixture_mode",
        "count": len(items),
        "items": items,
    }
    return _safe_json_write(output_path, payload)


def write_screenshots_index(outcomes: list["ExecutionOutcome"], output_path: str | Path) -> Path:
    """Write screenshots_index.json.

    Only real screenshot references (attached via
    ``ExecutionAdapter.record_screenshot`` with a genuine file path) appear
    here. The "no screenshots fabricated" disclaimer is attached only when
    EVERY outcome was fixture-simulated — a real_browser run that legitimately
    took zero screenshots must not carry a note implying no real capture was
    attempted.
    """

    items: list[dict[str, Any]] = []
    any_real_browser = False
    for outcome in outcomes:
        if outcome.execution_mode != "fixture_simulated":
            any_real_browser = True
        for ref in outcome.screenshot_refs:
            items.append({**ref, "test_case_id": outcome.test_case_id, "goal_id": outcome.goal_id})

    payload = {
        "schema_version": "stage2_execution_screenshots_index.v1",
        "count": len(items),
        "items": items,
    }
    if not items and not any_real_browser:
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
