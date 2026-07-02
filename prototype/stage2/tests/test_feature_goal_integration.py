"""
Integration tests for Stage D: Feature goal loop.

Tests the complete feature discovery workflow from page_entries.json
to feature_points.json and generated_test_cases.json.
"""

import tempfile
import json
from pathlib import Path
import pytest

from prototype.stage2.app.feature_goal import (
    FeatureGoalOrchestrator,
    classify_feature_type,
    classify_feature_from_page_context,
    should_generate_executable_test,
)
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine


def test_feature_discovery_session_basic():
    """Test basic feature discovery session workflow."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        # Create orchestrator
        orch = FeatureGoalOrchestrator(output_dir=str(output_dir), run_id="test_session")

        # Create root goal
        root_id = orch.create_root_goal(description="Test feature discovery")
        assert root_id
        assert orch.engine.goals[root_id].origin == "root::feature_discovery"

        # Create test page_entries.json
        page_fixture = output_dir / "page_entries.json"
        test_pages = [
            {
                "page_id": "page_001",
                "menu_path": ["系统管理", "用户管理"],
                "page_url": "https://example.com/admin/users",
                "page_title": "用户管理",
                "route_hint": "/admin/users",
                "status": "reachable",
                "screenshot_path": "screenshots/page_001.png",
                "parent_menu_id": "menu_001",
                "http_status": 200,
                "has_main_content": True,
                "is_blank": False,
                "metadata": {"goal_id": "goal-000002"},
            }
        ]
        with open(page_fixture, "w", encoding="utf-8") as f:
            json.dump(test_pages, f, ensure_ascii=False, indent=2)

        # Load page entries
        page_goal_ids = orch.load_page_entries(str(page_fixture))
        assert len(page_goal_ids) == 1

        # Scan page for features
        feature_goal_ids = orch.scan_page_features(page_goal_ids[0])
        assert len(feature_goal_ids) > 0

        # Export fixtures
        feature_fixture = orch.export_fixture()
        test_cases_fixture = orch.export_test_cases()
        review_fixture = orch.export_discovery_review()

        # Verify feature_points.json
        assert feature_fixture.exists()
        with open(feature_fixture, "r", encoding="utf-8") as f:
            features = json.load(f)
        assert isinstance(features, list)
        assert len(features) > 0

        # Check feature structure — every entry has the expected shape
        for feature in features:
            assert "feature_id" in feature
            assert "page_id" in feature
            assert "feature_type" in feature
            assert "risk_level" in feature
            assert "confidence" in feature
            assert "status" in feature

        # "用户管理" matches the query-vocabulary ("管理"), so this page
        # produces real query/reset features beyond the baseline view.
        # The baseline 'view' entry itself is correctly reported as
        # 'failed' (feature_not_identified) — it carries no specific
        # feature type on its own, only the non-view entries represent
        # genuinely identified functionality (Stage D adversarial review
        # Finding #2 fix: a low-confidence view must NOT be reported as
        # identified just because sibling features on the same page were).
        query_feature = next(f for f in features if f["feature_type"] == "query")
        assert query_feature["status"] == "identified"

        # Verify generated_test_cases.json
        assert test_cases_fixture.exists()
        with open(test_cases_fixture, "r", encoding="utf-8") as f:
            test_cases = json.load(f)
        assert isinstance(test_cases, list)
        assert len(test_cases) > 0

        # Verify discovery_review.json
        assert review_fixture.exists()
        with open(review_fixture, "r", encoding="utf-8") as f:
            review = json.load(f)
        assert "by_type" in review
        assert "by_risk" in review
        assert "summary" in review


def test_feature_type_classification():
    """Test feature type classification from element text."""
    # Query feature
    result = classify_feature_type("查询")
    assert result.feature_type == "query"
    assert result.risk_level == "low"
    assert result.confidence == "high"

    # Reset feature
    result = classify_feature_type("重置")
    assert result.feature_type == "reset"
    assert result.risk_level == "low"

    # Detail feature
    result = classify_feature_type("查看详情")
    assert result.feature_type == "detail"
    assert result.risk_level == "low"

    # Delete (high risk)
    result = classify_feature_type("删除")
    assert result.feature_type == "row_action_delete"
    assert result.risk_level == "high"

    # Export (medium risk)
    result = classify_feature_type("导出")
    assert result.feature_type == "export"
    assert result.risk_level == "medium"

    # Generic text (fallback to view)
    result = classify_feature_type("用户列表")
    assert result.feature_type == "view"
    assert result.risk_level == "none"
    assert result.confidence == "low"


def test_keyword_tie_resolves_to_highest_risk():
    """
    Regression test for Stage D adversarial review Finding #1 (critical):
    a label matching both a low-risk and a high-risk keyword must resolve
    to the MORE dangerous interpretation, not whichever type happens to be
    inserted first in FEATURE_TYPE_DEFINITIONS.
    """
    # "查询并删除" matches "查询" (query/low) AND "删除" (row_action_delete/high)
    # with keyword_count=1 each — the tie must break toward risk="high".
    result = classify_feature_type("查询并删除")
    assert result.feature_type == "row_action_delete"
    assert result.risk_level == "high"

    # "保存查询" matches "查询" (query/low) AND "保存" (submit/high)
    result = classify_feature_type("保存查询")
    assert result.risk_level == "high"

    # "编辑或查看" matches "查看" (detail/low) AND "编辑" (row_action_edit/high)
    result = classify_feature_type("编辑或查看")
    assert result.feature_type == "row_action_edit"
    assert result.risk_level == "high"


def test_risk_level_assessment():
    """Test risk level assessment and executable test generation."""
    # Low risk → executable test
    assert should_generate_executable_test("low", "high") is True
    assert should_generate_executable_test("low", "medium") is True

    # Medium risk → executable test
    assert should_generate_executable_test("medium", "high") is True

    # High risk → no executable test (entry confirmation only)
    assert should_generate_executable_test("high", "high") is False

    # View (none risk) → no test
    assert should_generate_executable_test("none", "high") is False


def test_low_risk_test_case_generation():
    """Test executable test case generation for low-risk features."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = FeatureGoalOrchestrator(output_dir=str(output_dir))
        root_id = orch.create_root_goal()

        # Create page with query feature
        page_fixture = output_dir / "page_entries.json"
        test_pages = [
            {
                "page_id": "page_001",
                "menu_path": ["用户管理"],
                "page_url": "https://example.com/users",
                "page_title": "用户管理",
                "status": "reachable",
                "parent_menu_id": None,
                "http_status": 200,
                "has_main_content": True,
                "is_blank": False,
                "metadata": {},
            }
        ]
        with open(page_fixture, "w", encoding="utf-8") as f:
            json.dump(test_pages, f, ensure_ascii=False)

        page_goal_ids = orch.load_page_entries(str(page_fixture))
        feature_goal_ids = orch.scan_page_features(page_goal_ids[0])

        # Export and verify
        test_cases_fixture = orch.export_test_cases()
        with open(test_cases_fixture, "r", encoding="utf-8") as f:
            test_cases = json.load(f)

        # Should have executable test cases for low-risk features
        executable_cases = [tc for tc in test_cases if tc["type"] == "executable"]
        assert len(executable_cases) > 0

        # Verify test case structure
        query_case = next((tc for tc in executable_cases if "query" in tc["feature_id"]), None)
        if query_case:
            assert "steps" in query_case
            assert len(query_case["steps"]) > 0
            assert "expected_result" in query_case
            assert query_case["risk_level"] == "low"


def test_high_risk_entry_confirmation():
    """Test entry confirmation generation for high-risk features."""
    from prototype.stage2.app.feature_goal.test_case_generator import generate_test_case

    # Generate test case for delete operation (high risk)
    test_case = generate_test_case(
        feature_id="feat_delete_001",
        page_id="page_001",
        feature_type="row_action_delete",
        risk_level="high",
        confidence="high",
        element_text="删除",
        element_locator="button.delete",
        page_url="https://example.com/users",
    )

    # Should be entry confirmation type
    assert test_case["type"] == "entry_confirmation"
    assert test_case["requires_approval"] is True
    assert "warning" in test_case
    assert "高风险" in test_case["warning"]
    assert "steps" not in test_case  # No executable steps


def test_english_titles_do_not_degrade_to_view_only():
    """
    Regression test for Stage D adversarial review Finding #3 (high): common
    English admin-page titles must not silently produce only the baseline
    'view' feature, which would violate 实施计划 §6.4's acceptance criterion
    ("至少能稳定识别若干低风险功能点").
    """
    for title in ["Users", "Settings", "Reports", "Roles", "Permissions"]:
        results = classify_feature_from_page_context(page_title=title, page_url=f"/{title.lower()}")
        feature_types = {r.feature_type for r in results}
        assert feature_types != {"view"}, f"'{title}' degraded to view-only"
        assert "query" in feature_types


def test_degraded_page_reported_as_not_identified():
    """
    Regression test for Stage D adversarial review Finding #2 (high): a page
    that produces ONLY the baseline view (no substantive feature inferred)
    must be recorded as feature_not_identified, not silently reported as
    'identified' via target_discovered_but_uncovered.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = FeatureGoalOrchestrator(output_dir=str(output_dir))
        orch.create_root_goal()

        # "Dashboard" matches none of the query/detail/export vocabulary
        page_fixture = output_dir / "page_entries.json"
        test_pages = [
            {
                "page_id": "page_001",
                "menu_path": ["Dashboard"],
                "page_url": "https://example.com/dashboard",
                "page_title": "Dashboard",
                "status": "reachable",
                "parent_menu_id": None,
                "http_status": 200,
                "has_main_content": True,
                "is_blank": False,
                "metadata": {},
            }
        ]
        with open(page_fixture, "w", encoding="utf-8") as f:
            json.dump(test_pages, f, ensure_ascii=False)

        page_goal_ids = orch.load_page_entries(str(page_fixture))
        orch.scan_page_features(page_goal_ids[0])

        feature_fixture = orch.export_fixture()
        with open(feature_fixture, "r", encoding="utf-8") as f:
            features = json.load(f)

        # Only the baseline view feature exists for this page
        view_feature = next(f for f in features if f["feature_type"] == "view")
        assert view_feature["status"] == "failed"
        assert view_feature["metadata"]["failure_class"] == "feature_not_identified"


def test_feature_fixture_export():
    """Test feature fixture export with multiple feature types."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = FeatureGoalOrchestrator(output_dir=str(output_dir))
        root_id = orch.create_root_goal()

        # Create page with management context (should generate query/reset)
        page_fixture = output_dir / "page_entries.json"
        test_pages = [
            {
                "page_id": "page_001",
                "menu_path": ["系统管理", "角色管理"],
                "page_url": "https://example.com/admin/roles",
                "page_title": "角色管理",
                "status": "reachable",
                "parent_menu_id": "menu_001",
                "http_status": 200,
                "has_main_content": True,
                "is_blank": False,
                "metadata": {},
            }
        ]
        with open(page_fixture, "w", encoding="utf-8") as f:
            json.dump(test_pages, f, ensure_ascii=False)

        page_goal_ids = orch.load_page_entries(str(page_fixture))
        feature_goal_ids = orch.scan_page_features(page_goal_ids[0])

        # Export
        feature_fixture = orch.export_fixture()
        review_fixture = orch.export_discovery_review()

        # Verify features
        with open(feature_fixture, "r", encoding="utf-8") as f:
            features = json.load(f)

        # Should have multiple feature types (not all view)
        feature_types = {f["feature_type"] for f in features}
        assert "view" in feature_types  # Baseline
        assert len(feature_types) > 1  # Should have other types (query, reset, etc.)

        # Verify review
        with open(review_fixture, "r", encoding="utf-8") as f:
            review = json.load(f)

        assert review["summary"]["total_features"] > 0
        assert len(review["summary"]["feature_types"]) > 1  # Multiple types identified


def test_cjk_feature_text_preserved():
    """Test CJK text preservation in feature discovery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = FeatureGoalOrchestrator(output_dir=str(output_dir))
        root_id = orch.create_root_goal()

        # Create page with CJK text
        page_fixture = output_dir / "page_entries.json"
        test_pages = [
            {
                "page_id": "page_001",
                "menu_path": ["溯源管理", "用户管理"],
                "page_url": "https://example.com/traceability/users",
                "page_title": "溯源管理 | 用户管理",
                "status": "reachable",
                "parent_menu_id": "menu_001",
                "http_status": 200,
                "has_main_content": True,
                "is_blank": False,
                "metadata": {},
            }
        ]
        with open(page_fixture, "w", encoding="utf-8") as f:
            json.dump(test_pages, f, ensure_ascii=False)

        page_goal_ids = orch.load_page_entries(str(page_fixture))
        feature_goal_ids = orch.scan_page_features(page_goal_ids[0])

        # Export
        feature_fixture = orch.export_fixture()

        # Read back and verify CJK preservation ON DISK (raw file content).
        # Do NOT fall back to checking the json.loads()-parsed objects: any
        # \uXXXX-escaped file (an ensure_ascii=True regression) decodes back
        # to real CJK through json.loads() regardless of how it was written
        # on disk, so an "on raw content OR on parsed object" assertion
        # would pass even under that regression and never catch it.
        with open(feature_fixture, "r", encoding="utf-8") as f:
            content = f.read()

        assert "溯源管理" in content
        assert "用户管理" in content
        assert "\\u" not in content  # no escaped CJK anywhere in the file


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
