"""
Integration test for Stage C page goal loop.

Demonstrates complete page discovery flow from menu_entries.json to page_entries.json.
Tests independently without browser using mocked page state signals.
"""

import json
import tempfile
from pathlib import Path

import pytest

from prototype.stage2.app.page_goal import (
    PageGoalOrchestrator,
    classify_page_discovery_failure,
    should_retry_page_discovery,
)


def test_page_discovery_session_basic():
    """
    Test basic page discovery session flow.

    Creates orchestrator, loads menu entries, registers page goals,
    records successful page discovery, exports fixtures.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        fixtures_dir = Path(tmpdir) / "fixtures"
        fixtures_dir.mkdir()

        # Create test menu_entries.json from Stage B
        menu_entries_path = fixtures_dir / "menu_entries.json"
        menu_entries = [
            {
                "menu_id": "menu_001",
                "menu_path": ["System Management"],
                "menu_text": "System Management",
                "route_hint": "/system",
                "status": "discovered",
                "parent_id": None,
            },
            {
                "menu_id": "menu_002",
                "menu_path": ["System Management", "User Management"],
                "menu_text": "User Management",
                "route_hint": "/system/users",
                "status": "discovered",
                "parent_id": "menu_001",
            },
            {
                "menu_id": "menu_003",
                "menu_path": ["Reports"],
                "menu_text": "Reports",
                "route_hint": None,
                "status": "failed",  # Should be skipped
                "parent_id": None,
            },
        ]

        with open(menu_entries_path, "w", encoding="utf-8") as f:
            json.dump(menu_entries, f, ensure_ascii=False)

        # Initialize orchestrator
        orch = PageGoalOrchestrator(output_dir=output_dir, run_id="test_run")
        adapter = orch.adapter

        # Create root goal
        root_id = orch.create_root_goal()
        assert root_id is not None
        root = orch.get_root_goal()
        assert root is not None
        assert root.goal_name == "Discover all reachable pages"

        # Load menu entries
        goal_ids = orch.load_menu_entries(menu_entries_path)
        assert len(goal_ids) == 2  # Only discovered entries loaded

        # Verify page goals registered
        for goal_id in goal_ids:
            goal = orch.engine.goals[goal_id]
            assert goal.goal_type == "page"
            assert goal.origin.startswith("page_entry::")
            assert goal.status == "planned"

            context = adapter.get_page_context(goal_id)
            assert context is not None
            assert context["page_id"] in ["menu_001", "menu_002"]
            assert context["menu_path"] is not None
            assert context["parent_menu_id"] in [None, "menu_001"]

        # Simulate page discovery for first goal
        goal_id_1 = goal_ids[0]

        # For fixture testing, manually set goal to running (simulates activation)
        goal_1 = orch.engine.goals[goal_id_1]
        goal_1.status = "running"

        # Start attempt
        attempt_id = adapter.record_page_attempt(goal_id=goal_id_1)

        # Record navigation steps (mitigation for Finding #3: observed=False for non-evidence steps)
        step1_id = adapter.record_navigation_step(
            attempt_id=attempt_id,
            action="navigate_to_page",
            target="/system",
            observed=False,  # No evidence attached
        )

        step2_id = adapter.record_navigation_step(
            attempt_id=attempt_id,
            action="wait_for_load",
            observed=False,  # No evidence attached
        )

        step3_id = adapter.record_navigation_step(
            attempt_id=attempt_id,
            action="capture_state",
            observed=True,  # Evidence will be attached
        )

        # Attach evidence
        screenshot_path = "screenshots/page_001.png"
        screenshot_ev = adapter.attach_screenshot_evidence(
            step_id=step3_id,
            screenshot_path=screenshot_path,
            metadata={"timestamp": "2026-07-02T10:00:00Z", "viewport": {"width": 1920, "height": 1080}},
        )

        page_meta_ev = adapter.attach_page_metadata_evidence(
            step_id=step3_id,
            page_title="System Management",
            page_url="https://example.com/system",
            http_status=200,
            dom_snapshot={"visible_text_len": 500, "dom_nodes": 100, "has_main_content": True},
        )

        # Record success (mitigation for Finding #10: explicit signals)
        adapter.record_page_success(
            attempt_id=attempt_id,
            page_url="https://example.com/system",
            page_title="System Management",
            visible_text_len=500,
            dom_nodes=100,
            blank_screenshot_ratio=0.1,
            evidence_refs=[screenshot_ev, page_meta_ev],
        )

        # Verify goal succeeded
        goal_1 = orch.engine.goals[goal_id_1]
        assert goal_1.status == "succeeded"

        # Export fixtures
        fixture_path = orch.export_fixture()
        assert fixture_path.exists()

        exploration_log_path = orch.export_exploration_log()
        assert exploration_log_path.exists()

        screenshots_index_path = orch.export_screenshots_index()
        assert screenshots_index_path.exists()

        goal_summary_path = orch.export_goal_summary()
        assert goal_summary_path.exists()

        # Verify page_entries.json content
        with open(fixture_path, "r", encoding="utf-8") as f:
            page_entries = json.load(f)

        assert len(page_entries) == 2

        # Find the succeeded entry
        succeeded_entry = next(e for e in page_entries if e["page_id"] == "menu_001")
        assert succeeded_entry["status"] == "reachable"  # Mitigation for Finding #1
        assert succeeded_entry["page_url"] == "https://example.com/system"
        assert succeeded_entry["page_title"] == "System Management"
        assert succeeded_entry["parent_menu_id"] is None  # Mitigation for Finding #4
        assert succeeded_entry["http_status"] == 200
        assert succeeded_entry["has_main_content"] is True
        assert succeeded_entry["is_blank"] is False

        # Pending entry
        pending_entry = next(e for e in page_entries if e["page_id"] == "menu_002")
        assert pending_entry["status"] == "pending"
        assert pending_entry["parent_menu_id"] == "menu_001"  # Mitigation for Finding #4

        # Verify goal summary
        summary = orch.get_summary()
        assert summary["run_id"] == "test_run"
        assert summary["domain"] == "page_discovery"
        assert summary["total_goals"] == 2
        assert summary["succeeded"] == 1  # Mitigation for Finding #1
        assert summary["pending"] == 1
        assert summary["failed"] == 0
        assert summary["blocked"] == 0  # Mitigation for Finding #6
        assert summary["reachable_count"] == 1


def test_page_discovery_with_blank_page():
    """
    Test page_blank classification and confidence-gated retry.

    Mitigation for Finding #8: confidence influences retry decision.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        orch = PageGoalOrchestrator(output_dir=output_dir)
        adapter = orch.adapter

        # Create root and page goal
        root_id = orch.create_root_goal()
        goal_id = adapter.register_page_goal(
            page_id="page_blank_test",
            menu_path=["Test", "Blank Page"],
            route_hint="/blank",
            parent_goal_id=root_id,
            parent_menu_id=None,
        )

        # Activate and attempt
        goal = orch.engine.goals[goal_id]
        goal.status = "running"  # Simulate activation
        attempt_id = adapter.record_page_attempt(goal_id=goal_id)

        step_id = adapter.record_navigation_step(
            attempt_id=attempt_id, action="capture_state", observed=True
        )

        # Attach evidence showing blank page
        adapter.attach_screenshot_evidence(step_id=step_id, screenshot_path="screenshots/blank.png")

        # Classify as blank with high confidence (multiple threshold violations)
        failure_class, confidence = classify_page_discovery_failure(
            page_signals={
                "visible_text_len": 5,  # < 20
                "dom_nodes": 2,  # < 5
                "blank_screenshot_ratio": 0.99,  # >= 0.98
                "has_main_content": False,
            }
        )

        assert failure_class == "page_blank"
        assert confidence == "high"

        # High confidence blank should not retry
        should_retry = should_retry_page_discovery(failure_class, attempt_count=1, confidence=confidence)
        assert should_retry is False

        # Record failure
        adapter.record_page_failure(
            attempt_id=attempt_id,
            failure_class=failure_class,
            confidence=confidence,
            evidence_refs=[],
            note="Blank page detected",
        )

        # Test low confidence blank (should retry)
        _, low_confidence = classify_page_discovery_failure(
            page_signals={"visible_text_len": 15, "dom_nodes": 4}  # Only 2 violations
        )
        assert low_confidence in ["medium", "low"]
        should_retry_low = should_retry_page_discovery("page_blank", 1, low_confidence)
        assert should_retry_low is True


def test_page_discovery_with_permission_blocked():
    """
    Test permission_blocked classification and blocked status mapping.

    Mitigation for Finding #6: blocked pages counted separately.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        orch = PageGoalOrchestrator(output_dir=output_dir)
        adapter = orch.adapter

        # Create page goal
        root_id = orch.create_root_goal()
        goal_id = adapter.register_page_goal(
            page_id="page_blocked_test",
            menu_path=["Admin", "Restricted"],
            route_hint="/admin/restricted",
            parent_goal_id=root_id,
            parent_menu_id=None,
        )

        # Classify HTTP 403
        failure_class, confidence = classify_page_discovery_failure(http_status=403)
        assert failure_class == "permission_blocked"
        assert confidence == "high"

        # Should not retry permission failures
        should_retry = should_retry_page_discovery(failure_class, 1, confidence)
        assert should_retry is False

        # Simulate failure recording that leads to waiting_human status
        # (In real scenario, evaluate_stop would set this via playbook EXIT_HUMAN)
        # For this test, manually set goal status to simulate the outcome
        goal = orch.engine.goals[goal_id]
        goal.status = "waiting_human"

        # Also create a fake attempt with failure_class
        # (In real scenario, this would be done via record_failure)
        from prototype.stage2.app.goal_loop.models import GoalAttempt
        fake_attempt = GoalAttempt(
            attempt_id="test_attempt",
            goal_id=goal_id,
            index=1,
            status="failed",
            failure_class="permission_blocked"
        )
        orch.engine.attempts.append(fake_attempt)

        # Export and verify blocked status
        fixture_path = orch.export_fixture()
        with open(fixture_path, "r", encoding="utf-8") as f:
            page_entries = json.load(f)

        # Verify blocked status
        blocked_entry = page_entries[0]
        assert blocked_entry["status"] == "blocked"  # Mitigation for Finding #6
        assert blocked_entry["metadata"]["failure_class"] == "permission_blocked"

        # Verify summary counts blocked separately
        summary = orch.get_summary()
        assert summary["blocked"] == 1
        assert summary["failed"] == 0
        assert summary["blocked_count"] == 1


def test_cjk_page_titles_preserved():
    """
    Test CJK text preservation through export/re-import cycle.

    Mitigation for Finding #7: UTF-8 encoding on cp1252 Windows.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        orch = PageGoalOrchestrator(output_dir=output_dir)
        adapter = orch.adapter

        # Create page goal with CJK title
        root_id = orch.create_root_goal()
        goal_id = adapter.register_page_goal(
            page_id="page_cjk_test",
            menu_path=["溯源管理", "用户管理"],  # CJK menu path
            route_hint="/suyuan/users",
            parent_goal_id=root_id,
            parent_menu_id=None,
        )

        # Simulate successful discovery with CJK title
        goal = orch.engine.goals[goal_id]
        goal.status = "running"  # Simulate activation
        attempt_id = adapter.record_page_attempt(goal_id=goal_id)

        step_id = adapter.record_navigation_step(
            attempt_id=attempt_id, action="capture_state", observed=True
        )

        adapter.attach_screenshot_evidence(step_id=step_id, screenshot_path="screenshots/cjk.png")

        adapter.record_page_success(
            attempt_id=attempt_id,
            page_url="https://example.com/suyuan/users",
            page_title="溯源管理 | 用户管理",  # CJK title with pipe
            visible_text_len=200,
            dom_nodes=50,
            blank_screenshot_ratio=0.05,
            evidence_refs=[],
        )

        # Export
        fixture_path = orch.export_fixture()

        # Re-read and verify CJK preserved
        with open(fixture_path, "r", encoding="utf-8") as f:
            page_entries = json.load(f)

        entry = page_entries[0]
        assert entry["page_title"] == "溯源管理 | 用户管理"
        assert entry["menu_path"] == ["溯源管理", "用户管理"]

        # Verify exploration log preserves CJK
        log_path = orch.export_exploration_log()
        with open(log_path, "r", encoding="utf-8") as f:
            log_lines = f.readlines()

        # At least one log entry should exist
        assert len(log_lines) > 0
        log_entry = json.loads(log_lines[0])
        assert log_entry["page_id"] == "page_cjk_test"


def test_url_normalization_and_deduplication():
    """
    Test URL normalization and deduplication logic.

    Mitigation for Finding #5: normalize + supersede duplicates.
    """
    from prototype.stage2.app.page_goal import PageAdapter
    from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine

    engine = GoalLoopEngine(run_id="test")
    adapter = PageAdapter(engine)

    # Test URL normalization
    assert adapter.normalize_page_url("https://example.com/path/") == "https://example.com/path"
    assert adapter.normalize_page_url("https://example.com/path?b=2&a=1") == "https://example.com/path?a=1&b=2"
    assert adapter.normalize_page_url("https://EXAMPLE.COM/Path") == "https://example.com/path"
    assert adapter.normalize_page_url("https://example.com/path#hash") == "https://example.com/path"
    assert adapter.normalize_page_url("https://example.com/") == "https://example.com/"  # Root preserved

    # Test deduplication (would require more complex setup with actual page discoveries)
    # For now, just verify the function exists and is callable
    adapter.deduplicate_pages()  # Should not raise


def test_http_error_maps_to_unknown():
    """
    Test HTTP 4xx/5xx maps to 'unknown' not 'locator_unstable'.

    Mitigation for Finding #9: prevent semantic pollution.
    """
    # HTTP 500 should map to unknown
    failure_class, confidence = classify_page_discovery_failure(http_status=500)
    assert failure_class == "unknown"
    assert confidence == "medium"

    # HTTP 404 should map to unknown
    failure_class, confidence = classify_page_discovery_failure(http_status=404)
    assert failure_class == "unknown"

    # HTTP 403 should still map to permission_blocked
    failure_class, confidence = classify_page_discovery_failure(http_status=403)
    assert failure_class == "permission_blocked"


if __name__ == "__main__":
    # Run tests manually for development
    test_page_discovery_session_basic()
    print("[OK] test_page_discovery_session_basic")

    test_page_discovery_with_blank_page()
    print("[OK] test_page_discovery_with_blank_page")

    test_page_discovery_with_permission_blocked()
    print("[OK] test_page_discovery_with_permission_blocked")

    test_cjk_page_titles_preserved()
    print("[OK] test_cjk_page_titles_preserved")

    test_url_normalization_and_deduplication()
    print("[OK] test_url_normalization_and_deduplication")

    test_http_error_maps_to_unknown()
    print("[OK] test_http_error_maps_to_unknown")

    print("\n=== All integration tests passed ===")
