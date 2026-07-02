"""MenuFixtureWriter: Serializes menu_entries.json for Stage C independent testing.

Exports goal loop state to v3-compatible menu_entries.json format:
[
  {
    "menu_id": "menu_001",
    "menu_path": ["系统管理", "用户管理"],
    "menu_text": "用户管理",
    "route_hint": "/system/user",
    "status": "discovered",
    "screenshot_path": "screenshots/menu_001.png",
    "parent_menu_id": null,
    "metadata": {...}
  },
  ...
]

Design:
- Reads goal loop state and reconstructs menu entries
- Preserves CJK text encoding
- Includes only goals with origin="menu_entry::*"
- Maps goal status to entry status (succeeded→discovered, failed→failed, pending→pending)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .discovery_adapter import DiscoveryAdapter


def write_menu_fixture(
    adapter: DiscoveryAdapter,
    output_path: str | Path,
) -> None:
    """Write menu_entries.json fixture from goal loop state.

    Args:
        adapter: Discovery adapter with menu context registry
        output_path: Path to write menu_entries.json
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    engine = adapter.engine

    # Collect all menu goals (origin starts with "menu_entry::")
    menu_entries = []

    for goal in engine.goals.values():
        if not goal.origin.startswith("menu_entry::"):
            continue

        # Extract menu_id from origin
        menu_id = goal.origin.split("::", 1)[1]

        # Get menu context from adapter
        menu_context = adapter.get_menu_context(goal.goal_id)
        if not menu_context:
            continue

        menu_path = menu_context.get("menu_path", [])
        menu_text = menu_path[-1] if menu_path else ""
        route_hint = menu_context.get("route_hint")
        parent_menu_id = None

        # Map goal status to entry status
        status_map = {
            "succeeded": "discovered",
            "failed": "failed",
            "pending": "pending",
        }
        status = status_map.get(goal.status, "pending")

        # Find screenshot evidence if available
        screenshot_path = None
        if goal.last_attempt_id:
            attempt = engine.attempts.get(goal.last_attempt_id)
            if attempt:
                for step_id in attempt.step_ids:
                    step = engine.steps.get(step_id)
                    if step:
                        for ev_id in step.evidence_ids:
                            evidence = engine.evidence.get(ev_id)
                            if evidence and evidence.evidence_type == "screenshot":
                                screenshot_path = evidence.content
                                break
                    if screenshot_path:
                        break

        # Find parent goal if hierarchical
        if goal.parent_goal_id:
            parent_goal = engine.goals.get(goal.parent_goal_id)
            if parent_goal and parent_goal.origin.startswith("menu_entry::"):
                parent_menu_id = parent_goal.origin.split("::", 1)[1]

        entry = {
            "menu_id": menu_id,
            "menu_path": menu_path,
            "menu_text": menu_text,
            "route_hint": route_hint,
            "status": status,
            "screenshot_path": screenshot_path,
            "parent_menu_id": parent_menu_id,
            "metadata": {
                "goal_id": goal.goal_id,
                "attempts": goal.attempt_count,
                "conclusion": goal.conclusion,
            },
        }

        menu_entries.append(entry)

    # Sort by menu_id for deterministic output
    menu_entries.sort(key=lambda e: e["menu_id"])

    # Write JSON with CJK support
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(menu_entries, f, ensure_ascii=False, indent=2)
