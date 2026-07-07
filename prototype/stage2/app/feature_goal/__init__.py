"""
Feature goal loop module.

Stage D: Feature point discovery and test case generation.
Consumes Stage C page_entries.json, produces feature_points.json and generated_test_cases.json.
"""

from .feature_adapter import FeatureAdapter
from .orchestrator import FeatureGoalOrchestrator
from .feature_classifier import (
    classify_feature_type,
    classify_feature_from_page_context,
    should_generate_executable_test,
    get_feature_type_description,
    FeatureClassification,
)
from .loader import (
    load_feature_goals_from_page_fixture,
    get_page_context_from_feature_goal,
)
from .test_case_generator import generate_test_case
from .feature_fixture_writer import (
    write_feature_fixture,
    write_test_cases_fixture,
    write_discovery_review,
    map_goal_status_to_feature_status,
    safe_json_write,
)

__all__ = [
    # Adapter
    "FeatureAdapter",
    # Orchestrator
    "FeatureGoalOrchestrator",
    # Classifier
    "classify_feature_type",
    "classify_feature_from_page_context",
    "should_generate_executable_test",
    "get_feature_type_description",
    "FeatureClassification",
    # Loader
    "load_feature_goals_from_page_fixture",
    "get_page_context_from_feature_goal",
    # Test case generator
    "generate_test_case",
    # Fixture writer
    "write_feature_fixture",
    "write_test_cases_fixture",
    "write_discovery_review",
    "map_goal_status_to_feature_status",
    "safe_json_write",
]
