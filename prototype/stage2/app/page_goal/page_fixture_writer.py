"""
Page fixture writer for Stage D independence.

Serializes page_entries.json from goal loop state for Stage D testing.
Preserves CJK encoding and page discovery results.

Mitigation for Finding #1: Explicit status mapping for all TERMINAL/PAUSED statuses.
Mitigation for Finding #7: UTF-8 encoding for all exports.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.models import Goal
    from .page_adapter import PageAdapter

# Import status constants
from ..goal_loop.models import (
    STATUS_SUCCEEDED,
    STATUS_FAILED_MAX_ROUNDS,
    STATUS_STOPPED_NO_PROGRESS,
    STATUS_WAITING_HUMAN,
    STATUS_BLOCKED_BY_POLICY,
    STATUS_BLOCKED_BY_EXECUTOR,
    STATUS_SUPERSEDED,
    STATUS_PLANNED,
    STATUS_RUNNING,
    TERMINAL_STATUSES,
    PAUSED_STATUSES,
)


def map_goal_status_to_entry_status(goal: "Goal", adapter: "PageAdapter" = None) -> str:
    """
    Map goal status to page entry status.

    Mitigation for Finding #1: Handles all TERMINAL_STATUSES and PAUSED_STATUSES explicitly.
    The engine never sets goal.status to plain 'failed', so we map the actual statuses:

    - succeeded → 'reachable'
    - failed_max_rounds, stopped_no_progress → 'failed'
    - blocked_by_policy, blocked_by_executor → 'blocked'
    - waiting_human (with permission_blocked/login_required) → 'blocked'
    - waiting_human (other) → 'pending'
    - superseded → 'deduplicated'
    - planned, running → 'pending'

    Args:
        goal: Goal instance
        adapter: Optional PageAdapter to retrieve failure classification

    Returns:
        Entry status string: 'reachable', 'failed', 'blocked', 'deduplicated', 'pending'
    """
    status = goal.status

    # Terminal statuses
    if status == STATUS_SUCCEEDED:
        return "reachable"

    if status in {STATUS_FAILED_MAX_ROUNDS, STATUS_STOPPED_NO_PROGRESS}:
        return "failed"

    if status == STATUS_SUPERSEDED:
        return "deduplicated"

    # Paused statuses - Mitigation for Finding #6
    if status in {STATUS_BLOCKED_BY_POLICY, STATUS_BLOCKED_BY_EXECUTOR}:
        return "blocked"

    if status == STATUS_WAITING_HUMAN:
        # Check failure_class from last attempt to distinguish blocked from other paused
        if adapter:
            # Find last attempt for this goal
            last_failure_class = None
            for attempt in adapter.engine.attempts:
                if attempt.goal_id == goal.goal_id:
                    last_failure_class = attempt.failure_class

            if last_failure_class in {"permission_blocked", "login_required"}:
                return "blocked"
        # Other waiting_human reasons (e.g., manual review) → pending
        return "pending"

    # Active statuses
    if status in {STATUS_PLANNED, STATUS_RUNNING}:
        return "pending"

    # Fallback (should not reach here if all statuses covered)
    return "pending"


def write_page_fixture(adapter: "PageAdapter", output_path: str | Path) -> None:
    """
    Write page_entries.json fixture from goal loop state via adapter.

    Collects all goals with origin='page_entry::*'. Maps goal status to entry status.
    Extracts page context from adapter registry (page_id, menu_path, page_url,
    page_title, route_hint, parent_menu_id). Includes metadata: goal_id, attempt_count,
    stop_reason, failure_class.

    Mitigation for Finding #7: Uses safe_json_write with encoding='utf-8',
    ensure_ascii=False for CJK preservation.

    Schema:
    [{
        page_id: str,
        menu_path: list[str],
        page_url: str | null,
        page_title: str | null,
        route_hint: str | null,
        status: 'reachable' | 'failed' | 'blocked' | 'deduplicated' | 'pending',
        screenshot_path: str | null,
        parent_menu_id: str | null,
        http_status: int | null,
        has_main_content: bool,
        is_blank: bool,
        metadata: {
            goal_id: str,
            attempt_count: int,
            stop_reason: str | null,
            failure_class: str | null
        }
    }]

    Args:
        adapter: PageAdapter instance with page context registry
        output_path: Path to write page_entries.json
    """
    entries = []

    for goal_id, goal in adapter.engine.goals.items():
        # Filter to page goals only
        # Mitigation for Finding #12: guard against None origin
        if not goal.origin or not goal.origin.startswith("page_entry::"):
            continue

        # Get page context from adapter registry
        context = adapter.get_page_context(goal_id)
        if not context:
            continue

        # Map goal status to entry status
        entry_status = map_goal_status_to_entry_status(goal, adapter)

        # Extract page context fields
        page_id = context.get("page_id")
        menu_path = context.get("menu_path", [])
        page_url = context.get("page_url")
        page_title = context.get("page_title")
        route_hint = context.get("route_hint")
        parent_menu_id = context.get("parent_menu_id")  # Mitigation for Finding #4

        # Find screenshot path from evidence (if any)
        screenshot_path = None
        if goal.attempt_count > 0:
            # Look for screenshot evidence in attempts
            for attempt in adapter.engine.attempts:
                if attempt.goal_id == goal_id:
                    for step in attempt.steps:
                        for evidence_id in step.evidence_ids:
                            # engine.evidence is a dict, not a list
                            ev = adapter.engine.evidence.get(evidence_id)
                            if ev and ev.kind == "screenshot":
                                screenshot_path = ev.uri
                                break
                        if screenshot_path:
                            break
                    if screenshot_path:
                        break

        # Extract HTTP status and page state from attempts
        http_status = None
        has_main_content = False
        is_blank = False

        # Find last attempt with page_metadata evidence
        for attempt in reversed(adapter.engine.attempts):
            if attempt.goal_id == goal_id:
                for step in attempt.steps:
                    for evidence_id in step.evidence_ids:
                        ev = adapter.engine.evidence.get(evidence_id)
                        if ev and ev.kind == "page_metadata" and ev.note:
                            # Parse JSON-encoded metadata
                            try:
                                metadata = json.loads(ev.note)
                                http_status = metadata.get("http_status")
                                has_main_content = metadata.get("has_main_content", False)
                                visible_text_len = metadata.get("visible_text_len", 0)
                                dom_nodes = metadata.get("dom_nodes", 0)
                                # Compute is_blank from thresholds
                                is_blank = visible_text_len < 20 or dom_nodes < 5
                            except (json.JSONDecodeError, KeyError):
                                pass
                        if http_status is not None:
                            break
                    if http_status is not None:
                        break
                if http_status is not None:
                    break

        # Find last failure_class from attempts
        last_failure_class = None
        for attempt in reversed(adapter.engine.attempts):
            if attempt.goal_id == goal_id and attempt.failure_class:
                last_failure_class = attempt.failure_class
                break

        # Build entry
        entry = {
            "page_id": page_id,
            "menu_path": menu_path,
            "page_url": page_url,
            "page_title": page_title,
            "route_hint": route_hint,
            "status": entry_status,
            "screenshot_path": screenshot_path,
            "parent_menu_id": parent_menu_id,
            "http_status": http_status,
            "has_main_content": has_main_content,
            "is_blank": is_blank,
            "metadata": {
                "goal_id": goal_id,
                "attempt_count": goal.attempt_count,
                "stop_reason": goal.stop_reason,
                "failure_class": last_failure_class,
            },
        }

        entries.append(entry)

    # Write with UTF-8 encoding and CJK preservation
    safe_json_write(output_path, entries)


def collect_page_screenshots(adapter: "PageAdapter") -> dict[str, str]:
    """
    Collect mapping of page_id -> screenshot_path.

    Iterates through page goals, finds last successful attempt, extracts
    screenshot evidence URIs. Returns dict for screenshots_index.json export.

    Args:
        adapter: PageAdapter instance

    Returns:
        Dict mapping page_id to screenshot_path
    """
    screenshot_index = {}

    for goal_id, goal in adapter.engine.goals.items():
        # Filter to page goals
        if not goal.origin or not goal.origin.startswith("page_entry::"):
            continue

        context = adapter.get_page_context(goal_id)
        if not context:
            continue

        page_id = context.get("page_id")
        if not page_id:
            continue

        # Find screenshot from evidence
        screenshot_path = None
        for attempt in adapter.engine.attempts:
            if attempt.goal_id == goal_id:
                for step in attempt.steps:
                    for evidence_id in step.evidence_ids:
                        # engine.evidence is a dict
                        ev = adapter.engine.evidence.get(evidence_id)
                        if ev and ev.kind == "screenshot":
                            screenshot_path = ev.uri
                            break
                    if screenshot_path:
                        break
                if screenshot_path:
                    break

        if screenshot_path:
            screenshot_index[page_id] = screenshot_path

    return screenshot_index


def safe_json_write(
    path: str | Path,
    data: Any,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
) -> None:
    """
    Safely write JSON with UTF-8 encoding and CJK preservation.

    Mitigation for Finding #7: Encapsulates open(encoding='utf-8') +
    json.dump(ensure_ascii=False) to prevent cp1252 regressions on Windows.

    Args:
        path: Output file path
        data: Data to serialize
        encoding: File encoding (default: 'utf-8')
        ensure_ascii: Whether to escape non-ASCII (default: False for CJK)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding=encoding) as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=2)
