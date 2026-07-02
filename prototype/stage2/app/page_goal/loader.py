"""
Page goal loader from menu_entries.json fixture.

Loads Stage B output (menu_entries.json) as input for Stage C page discovery.
Creates page-type goals for each discovered menu entry.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from ..goal_loop.models import Goal
    from .page_adapter import PageAdapter


def load_page_goals_from_menu_fixture(
    engine: "GoalLoopEngine",
    adapter: "PageAdapter",
    menu_entries_path: str | Path,
    *,
    parent_goal_id: str | None = None,
) -> list[str]:
    """
    Load page goals from frozen menu_entries.json fixture.

    Filters entries with status='discovered' and registers goal_type='page' goals.
    Returns list of registered goal_ids. Each page goal origin is 'page_entry::{page_id}'
    derived from menu_id. Stores menu context in adapter registry (menu_path, route_hint,
    parent_menu_id). Only creates goals for discovered menus (status='discovered')
    to avoid processing unavailable menu entries.

    Args:
        engine: GoalLoopEngine instance
        adapter: PageAdapter instance for page context tracking
        menu_entries_path: Path to menu_entries.json from Stage B
        parent_goal_id: Parent goal ID (typically root goal)

    Returns:
        List of registered goal IDs

    Raises:
        FileNotFoundError: If menu_entries_path doesn't exist
        json.JSONDecodeError: If menu_entries.json is invalid
    """
    path = Path(menu_entries_path)
    if not path.exists():
        raise FileNotFoundError(f"Menu entries fixture not found: {path}")

    # Read with UTF-8 encoding for CJK preservation
    with open(path, "r", encoding="utf-8") as f:
        menu_entries = json.load(f)

    if not isinstance(menu_entries, list):
        raise ValueError(f"Expected list in menu_entries.json, got {type(menu_entries)}")

    registered_goal_ids = []

    for entry in menu_entries:
        # Only process discovered menu entries
        status = entry.get("status")
        if status != "discovered":
            continue

        menu_id = entry.get("menu_id")
        if not menu_id:
            continue

        # Derive page_id from menu_id
        page_id = menu_id

        # Extract menu context
        menu_path = entry.get("menu_path", [])
        route_hint = entry.get("route_hint")
        parent_menu_id = entry.get("parent_id")  # From menu hierarchy

        # Register page goal via adapter
        goal_id = adapter.register_page_goal(
            page_id=page_id,
            menu_path=menu_path,
            route_hint=route_hint,
            parent_goal_id=parent_goal_id,
            parent_menu_id=parent_menu_id,  # Mitigation for Finding #4
        )

        registered_goal_ids.append(goal_id)

    return registered_goal_ids


def get_page_context_from_goal(goal: "Goal") -> dict | None:
    """
    Extract page context from goal.

    Note: This is a helper for legacy compatibility. Prefer using
    PageAdapter.get_page_context(goal_id) which reads from the internal
    registry.

    Args:
        goal: Goal instance

    Returns:
        Dict with page context or None if goal is not a page goal
    """
    # Check if this is a page goal
    if not goal.origin or not goal.origin.startswith("page_entry::"):
        return None

    # Extract page_id from origin
    page_id = goal.origin.replace("page_entry::", "")

    # Basic context from goal attributes
    return {
        "page_id": page_id,
        "goal_name": goal.goal_name,
        "origin": goal.origin,
    }
