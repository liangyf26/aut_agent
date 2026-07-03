"""
Execution goal loader for Stage E.

Loads generated_test_cases.json (Stage D output) plus feature_points.json
(for feature_id -> page_id / goal lineage) and registers one execution goal
per test case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_adapter import ExecutionAdapter


def load_execution_goals_from_test_cases(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    test_cases_path: str | Path,
    *,
    parent_goal_id: str,
) -> list[str]:
    """Register one execution goal per entry in generated_test_cases.json.

    Args:
        engine: GoalLoopEngine instance.
        adapter: ExecutionAdapter instance.
        test_cases_path: path to Stage D's ``generated_test_cases.json``.
        parent_goal_id: parent goal (typically the Stage E root goal).

    Returns:
        List of created execution goal IDs, in file order.

    Raises:
        FileNotFoundError: if ``test_cases_path`` does not exist.
        ValueError: if the fixture is not a JSON list.
    """

    path = Path(test_cases_path)
    if not path.exists():
        raise FileNotFoundError(f"Test cases file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        test_cases = json.load(f)

    if not isinstance(test_cases, list):
        raise ValueError(f"Expected list in generated_test_cases.json, got {type(test_cases)}")

    goal_ids: list[str] = []
    for test_case in test_cases:
        if not isinstance(test_case, dict):
            continue
        feature_id = test_case.get("feature_id")
        if not feature_id:
            continue
        goal_id = adapter.register_execution_goal(
            feature_id=feature_id,
            page_id=test_case.get("page_id"),
            test_case=test_case,
            parent_goal_id=parent_goal_id,
        )
        goal_ids.append(goal_id)

    return goal_ids


__all__ = ["load_execution_goals_from_test_cases"]
