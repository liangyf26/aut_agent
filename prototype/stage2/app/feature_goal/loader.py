"""
Feature goal loader for Stage D.

Loads reachable pages from Stage C page_entries.json and creates feature discovery goals.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .feature_adapter import FeatureAdapter


def load_feature_goals_from_page_fixture(
    engine: "GoalLoopEngine",
    adapter: "FeatureAdapter",
    page_entries_path: str | Path,
    parent_goal_id: str,
) -> list[str]:
    """
    Load feature discovery goals from page_entries.json.

    Only processes pages with status='reachable'. Creates one feature discovery goal
    per reachable page.

    Args:
        engine: GoalLoopEngine instance
        adapter: FeatureAdapter instance
        page_entries_path: Path to page_entries.json from Stage C
        parent_goal_id: Parent goal ID (typically root goal)

    Returns:
        List of created goal IDs

    Raises:
        FileNotFoundError: If page_entries.json not found
        ValueError: If page_entries.json has invalid format
    """
    path = Path(page_entries_path)
    if not path.exists():
        raise FileNotFoundError(f"Page entries file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        page_entries = json.load(f)

    # Validate format
    if not isinstance(page_entries, list):
        raise ValueError(f"Expected list in page_entries.json, got {type(page_entries)}")

    goal_ids = []

    for entry in page_entries:
        # Only process reachable pages
        if entry.get("status") != "reachable":
            continue

        page_id = entry.get("page_id")
        page_url = entry.get("page_url")
        page_title = entry.get("page_title")

        if not page_id:
            continue

        # Create feature discovery goal for this page
        goal = engine.register_goal(
            goal_type="feature",
            goal_name=f"Discover features on {page_title or page_id}",
            parent_goal_id=parent_goal_id,
            origin=f"page_features::{page_id}",
        )

        # Store page context in adapter (for later use during feature scan)
        # We store this as a special marker in the adapter's context registry
        adapter._feature_context[goal.goal_id] = {
            "feature_id": f"page_{page_id}_features",
            "page_id": page_id,
            "page_url": page_url,
            "page_title": page_title,
            "parent_menu_id": entry.get("parent_menu_id"),
            "menu_path": entry.get("menu_path", []),
            "feature_type": "page_scan",  # Special marker
            "risk_level": "none",
        }

        goal_ids.append(goal.goal_id)

    return goal_ids


def get_page_context_from_feature_goal(
    adapter: "FeatureAdapter",
    goal_id: str,
) -> dict | None:
    """
    Retrieve page context from a feature discovery goal.

    Args:
        adapter: FeatureAdapter instance
        goal_id: Feature discovery goal ID

    Returns:
        Page context dict or None if not found
    """
    context = adapter.get_feature_context(goal_id)
    if not context or context.get("feature_type") != "page_scan":
        return None

    return {
        "page_id": context.get("page_id"),
        "page_url": context.get("page_url"),
        "page_title": context.get("page_title"),
        "parent_menu_id": context.get("parent_menu_id"),
        "menu_path": context.get("menu_path"),
    }
