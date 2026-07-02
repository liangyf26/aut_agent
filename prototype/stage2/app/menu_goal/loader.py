"""Load menu goals from frozen menu_entries.json fixture (Finding 3 fix).

This module bridges existing v3 menu_entries.json artifacts to the goal loop,
enabling Stage C to run independently of live Stage B discovery (计划 §2.6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine


def load_menu_goals_from_fixture(
    engine: GoalLoopEngine,
    menu_entries_path: str | Path,
    *,
    parent_goal_id: str | None = None,
) -> list[str]:
    """Load menu goals from a frozen menu_entries.json fixture.

    Args:
        engine: Goal loop engine to register menu goals into
        menu_entries_path: Path to menu_entries.json artifact
        parent_goal_id: Optional parent goal (e.g., a discovery-session goal)

    Returns:
        List of registered goal_ids in traversal order

    The menu_entries.json schema (from v3_orchestrator.py) is:
        {
          "schema_version": "stage2_menu_entries.v1",
          "menu_entries": [
            {
              "menu_id": "m1",
              "text": "订单管理",
              "level": 1,
              "parent_id": null,
              "menu_path": ["订单管理"],
              "is_leaf": false,
              "route_hint": "/orders",
              "locator_candidates": [...],
              "status": "discovered" | "expanded" | "permission_blocked" | ...
            }
          ]
        }

    Each menu entry becomes one Goal with:
        - goal_type="menu"
        - goal_name=entry["text"]
        - origin=f"menu_entry::{entry['menu_id']}"
        - context stores menu_path, route_hint, locator_candidates
    """

    path = Path(menu_entries_path)
    if not path.exists():
        raise FileNotFoundError(f"menu_entries fixture not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("menu_entries") or payload.get("items") or []
    if not isinstance(entries, list):
        raise ValueError(f"menu_entries.json must have 'menu_entries' array, got: {type(entries)}")

    registered_ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        menu_id = entry.get("menu_id")
        if not menu_id:
            continue

        text = entry.get("text", menu_id)
        # Origin encodes the menu_entry provenance for traceback
        origin = f"menu_entry::{menu_id}"

        # Register as menu-type goal; the success predicate is menu_goal_success
        # from predicates.py: has_menu_text AND has_path AND has_screenshot
        goal = engine.register_goal(
            goal_type="menu",
            goal_name=text,
            parent_goal_id=parent_goal_id,
            origin=origin,
            # Menu goals typically need fewer rounds than page/feature
            max_rounds=3,
        )

        # Store menu context in goal.notes for reference (not used by predicates,
        # but available for reporting / human review)
        menu_path = entry.get("menu_path", [text])
        route_hint = entry.get("route_hint")
        status = entry.get("status", "discovered")
        goal.notes.append(f"menu_path={menu_path}")
        if route_hint:
            goal.notes.append(f"route_hint={route_hint}")
        goal.notes.append(f"fixture_status={status}")

        registered_ids.append(goal.goal_id)

    return registered_ids


__all__ = ["load_menu_goals_from_fixture"]
