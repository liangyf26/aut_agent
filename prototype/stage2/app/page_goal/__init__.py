"""
Page goal loop module.

Stage C: Page discovery and state classification.
Consumes Stage B menu_entries.json, produces Stage D page_entries.json.
"""

from .page_adapter import PageAdapter
from .orchestrator import PageGoalOrchestrator
from .page_classifier import (
    classify_page_discovery_failure,
    classify_from_page_state,
    is_page_discovery_failure,
    should_retry_page_discovery,
)
from .loader import load_page_goals_from_menu_fixture, get_page_context_from_goal
from .page_fixture_writer import (
    write_page_fixture,
    collect_page_screenshots,
    map_goal_status_to_entry_status,
    safe_json_write,
)

__all__ = [
    # Adapter
    "PageAdapter",
    # Orchestrator
    "PageGoalOrchestrator",
    # Classifier
    "classify_page_discovery_failure",
    "classify_from_page_state",
    "is_page_discovery_failure",
    "should_retry_page_discovery",
    # Loader
    "load_page_goals_from_menu_fixture",
    "get_page_context_from_goal",
    # Fixture writer
    "write_page_fixture",
    "collect_page_screenshots",
    "map_goal_status_to_entry_status",
    "safe_json_write",
]
