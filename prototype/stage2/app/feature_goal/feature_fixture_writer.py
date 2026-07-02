"""
Feature fixture writer for Stage D.

Exports feature_points.json and generated_test_cases.json from goal loop state.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .feature_adapter import FeatureAdapter

from ..goal_loop.models import (
    STATUS_SUCCEEDED,
    STATUS_FAILED_MAX_ROUNDS,
    STATUS_STOPPED_NO_PROGRESS,
    STATUS_SUPERSEDED,
    STATUS_BLOCKED_BY_POLICY,
    STATUS_BLOCKED_BY_EXECUTOR,
    STATUS_WAITING_HUMAN,
    STATUS_PLANNED,
    STATUS_RUNNING,
)
from ..goal_loop.classification import PERMISSION_BLOCKED, LOGIN_REQUIRED


def map_goal_status_to_feature_status(
    goal,
    adapter: "FeatureAdapter" | None = None,
) -> str:
    """
    Map GoalLoopEngine status to feature entry status.

    Status mapping:
    - succeeded → 'identified' (would only happen if Stage E later executes
      the feature and satisfies the full feature_goal_success predicate)
    - running + last failure_class == target_discovered_but_uncovered →
      'identified' — this is Stage D's actual terminal state: the feature
      was identified and a test case generated, but basic_path_executed /
      has_feedback (Stage E's job) are still pending. record_failure()
      with made_progress=True does NOT call evaluate_stop(), so goal.status
      never leaves STATUS_RUNNING on this path (see FeatureAdapter.
      record_feature_identified for why record_success() is never used here).
    - running + last failure_class == feature_not_identified → 'failed'
      (degraded to generic 'view' with low confidence — the exact risk
      called out in 实施计划 §6.5/6.6)
    - failed_max_rounds, stopped_no_progress → 'failed'
    - superseded → 'deduplicated'
    - blocked_by_policy, blocked_by_executor → 'blocked'
    - waiting_human (with blocked failure_class) → 'blocked'
    - waiting_human (other) → 'pending'
    - planned, running (no attempts yet) → 'pending'

    Args:
        goal: Goal object
        adapter: Optional adapter for failure class lookup

    Returns:
        Feature status string
    """
    status = goal.status

    if status == STATUS_SUCCEEDED:
        return "identified"

    if status in {STATUS_FAILED_MAX_ROUNDS, STATUS_STOPPED_NO_PROGRESS}:
        return "failed"

    if status == STATUS_SUPERSEDED:
        return "deduplicated"

    if status in {STATUS_BLOCKED_BY_POLICY, STATUS_BLOCKED_BY_EXECUTOR}:
        return "blocked"

    if status == STATUS_WAITING_HUMAN:
        # Check failure_class to distinguish blocked from pending
        if adapter:
            last_failure_class = None
            for attempt in adapter.engine.attempts:
                if attempt.goal_id == goal.goal_id:
                    last_failure_class = attempt.failure_class

            # Use the real fixed failure classes (not made-up strings) —
            # these are exactly the EXIT_HUMAN triggers in playbook.py that
            # drive a goal to STATUS_WAITING_HUMAN in the first place.
            if last_failure_class in {PERMISSION_BLOCKED, LOGIN_REQUIRED}:
                return "blocked"
        return "pending"

    if status == STATUS_RUNNING:
        # Stage D never calls evaluate_stop(), so a goal whose last attempt
        # recorded target_discovered_but_uncovered / feature_not_identified
        # stays STATUS_RUNNING forever from the engine's point of view.
        # Resolve Stage D's own terminal state from the failure_class.
        if adapter:
            last_failure_class = None
            for attempt in adapter.engine.attempts:
                if attempt.goal_id == goal.goal_id:
                    last_failure_class = attempt.failure_class

            if last_failure_class == "target_discovered_but_uncovered":
                return "identified"
            if last_failure_class == "feature_not_identified":
                return "failed"
        return "pending"

    if status == STATUS_PLANNED:
        return "pending"

    return "pending"


def write_feature_fixture(
    adapter: "FeatureAdapter",
    output_path: str | Path,
) -> None:
    """
    Write feature_points.json fixture from goal loop state.

    Collects all feature goals (not page_scan goals) and exports their metadata.

    Schema:
    [{
        feature_id: str,
        page_id: str,
        feature_type: str,
        risk_level: str,
        element_text: str | null,
        element_locator: str | null,
        confidence: str,
        status: 'identified' | 'failed' | 'blocked' | 'deduplicated' | 'pending',
        metadata: {
            goal_id: str,
            attempt_count: int,
            stop_reason: str | null,
            failure_class: str | null
        }
    }]

    Args:
        adapter: FeatureAdapter instance
        output_path: Path to write feature_points.json
    """
    entries = []

    for goal_id, goal in adapter.engine.goals.items():
        # Filter to feature goals only (exclude page_scan goals)
        if not goal.origin or not goal.origin.startswith("feature_entry::"):
            continue

        # Get feature context from adapter registry
        context = adapter.get_feature_context(goal_id)
        if not context:
            continue

        # Map status
        status = map_goal_status_to_feature_status(goal, adapter)

        # Confidence lives in the adapter's own registry (see
        # FeatureAdapter.record_feature_identified for why it cannot be
        # threaded through engine signals/notes when explicit_class is used).
        confidence = context.get("confidence", "medium")
        failure_class = None
        for attempt in adapter.engine.attempts:
            if attempt.goal_id == goal_id:
                failure_class = attempt.failure_class

        # Count attempts
        attempt_count = sum(1 for a in adapter.engine.attempts if a.goal_id == goal_id)

        entry = {
            "feature_id": context.get("feature_id"),
            "page_id": context.get("page_id"),
            "feature_type": context.get("feature_type"),
            "risk_level": context.get("risk_level"),
            "element_text": context.get("element_text"),
            "element_locator": context.get("element_locator"),
            "confidence": confidence,
            "status": status,
            "metadata": {
                "goal_id": goal_id,
                "attempt_count": attempt_count,
                # goal_status_raw is the raw engine status (always 'running'
                # for Stage D, since evaluate_stop() is never called).
                # stop_reason is the engine-owned Goal.stop_reason attribute
                # (only ever set by evaluate_stop) — kept distinct so a
                # consumer reading "stop_reason" doesn't get a status word.
                "goal_status_raw": goal.status,
                "stop_reason": goal.stop_reason,
                "failure_class": failure_class,
            },
        }

        entries.append(entry)

    # Sort by page_id, then feature_id
    entries.sort(key=lambda e: (e["page_id"], e["feature_id"]))

    # Write with UTF-8 encoding
    safe_json_write(output_path, entries)


def write_test_cases_fixture(
    test_cases: list[dict],
    output_path: str | Path,
) -> None:
    """
    Write generated_test_cases.json fixture.

    Args:
        test_cases: List of test case dicts from test_case_generator
        output_path: Path to write generated_test_cases.json
    """
    # Sort by test_case_id
    test_cases.sort(key=lambda tc: tc["test_case_id"])

    # Write with UTF-8 encoding
    safe_json_write(output_path, test_cases)


def write_discovery_review(
    adapter: "FeatureAdapter",
    output_path: str | Path,
) -> None:
    """
    Write discovery_review.json with summary of discovered features.

    Groups features by type and risk level for review.

    Args:
        adapter: FeatureAdapter instance
        output_path: Path to write discovery_review.json
    """
    from collections import defaultdict

    by_type = defaultdict(list)
    by_risk = defaultdict(list)

    for goal_id, goal in adapter.engine.goals.items():
        if not goal.origin or not goal.origin.startswith("feature_entry::"):
            continue

        context = adapter.get_feature_context(goal_id)
        if not context:
            continue

        feature_type = context.get("feature_type")
        risk_level = context.get("risk_level")
        status = map_goal_status_to_feature_status(goal, adapter)

        if status == "identified":
            by_type[feature_type].append(context.get("feature_id"))
            by_risk[risk_level].append(context.get("feature_id"))

    review = {
        "by_type": dict(by_type),
        "by_risk": dict(by_risk),
        "summary": {
            "total_features": sum(len(v) for v in by_type.values()),
            "feature_types": list(by_type.keys()),
            "risk_levels": list(by_risk.keys()),
        },
    }

    safe_json_write(output_path, review)


def safe_json_write(
    path: str | Path,
    data: any,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
) -> None:
    """
    Write JSON file with UTF-8 encoding (Windows cp1252 compatible).

    Args:
        path: Output file path
        data: Data to serialize
        encoding: File encoding (default: utf-8)
        ensure_ascii: Whether to escape non-ASCII (default: False for CJK)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding=encoding) as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=2)
