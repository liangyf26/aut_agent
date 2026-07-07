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


def load_execution_goals_from_test_case_list(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    test_cases: list[dict],
    *,
    parent_goal_id: str,
    round_index: int = 1,
) -> list[str]:
    """Register one execution goal per already-in-memory test case dict.

    Same registration logic as :func:`load_execution_goals_from_test_cases`,
    factored out so callers that build cases directly in Python (e.g. a
    one-off real-browser verification driver) don't need to round-trip
    through a JSON file on disk.

    Args:
        engine: GoalLoopEngine instance.
        adapter: ExecutionAdapter instance.
        test_cases: list of test case dicts, same shape as
            ``generated_test_cases.json`` entries.
        parent_goal_id: parent goal (typically the Stage E root goal).
        round_index: forwarded to :meth:`ExecutionAdapter.register_execution_goal`
            — 1 for the first pass, >1 when re-registering a retried
            test_case in an auto-advanced round.

    Returns:
        List of created execution goal IDs, in list order.

    Raises:
        ValueError: if ``test_cases`` is not a list.
    """

    if not isinstance(test_cases, list):
        raise ValueError(f"Expected a list of test cases, got {type(test_cases)}")

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
            round_index=round_index,
        )
        goal_ids.append(goal_id)

    return goal_ids


def load_execution_goals_from_test_cases(
    engine: "GoalLoopEngine",
    adapter: "ExecutionAdapter",
    test_cases_path: str | Path,
    *,
    parent_goal_id: str,
    round_index: int = 1,
) -> list[str]:
    """Register one execution goal per entry in generated_test_cases.json.

    Args:
        engine: GoalLoopEngine instance.
        adapter: ExecutionAdapter instance.
        test_cases_path: path to Stage D's ``generated_test_cases.json``.
        parent_goal_id: parent goal (typically the Stage E root goal).
        round_index: forwarded to :func:`load_execution_goals_from_test_case_list`.

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

    return load_execution_goals_from_test_case_list(
        engine, adapter, test_cases, parent_goal_id=parent_goal_id, round_index=round_index
    )


__all__ = [
    "load_execution_goals_from_test_cases",
    "load_execution_goals_from_test_case_list",
]
