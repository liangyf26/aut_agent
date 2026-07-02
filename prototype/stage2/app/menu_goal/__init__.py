"""Menu goal loop: Stage B integration layer.

Bridges v3 menu discovery to goal loop architecture.
"""

from .discovery_adapter import DiscoveryAdapter
from .fixture_writer import write_menu_fixture
from .loader import load_menu_goals_from_fixture
from .menu_classifier import (
    classify_from_discovery_log,
    classify_menu_discovery_failure,
    is_menu_discovery_failure,
    should_retry_menu_discovery,
)
from .orchestrator import MenuGoalOrchestrator

__all__ = [
    "DiscoveryAdapter",
    "MenuGoalOrchestrator",
    "classify_from_discovery_log",
    "classify_menu_discovery_failure",
    "is_menu_discovery_failure",
    "load_menu_goals_from_fixture",
    "should_retry_menu_discovery",
    "write_menu_fixture",
]
