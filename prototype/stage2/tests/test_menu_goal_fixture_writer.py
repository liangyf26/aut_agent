"""Tests for MenuFixtureWriter."""

import json
import tempfile
from pathlib import Path

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine
from prototype.stage2.app.menu_goal import DiscoveryAdapter, write_menu_fixture


def test_write_menu_fixture_basic():
    """Test fixture writer exports basic menu entries."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    # Register menu goals
    goal1_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )
    goal2_id = adapter.register_menu_goal(
        menu_id="menu_002",
        menu_path=["系统管理", "用户管理"],
        parent_goal_id=goal1_id,
    )

    # Add route hints via menu context
    adapter._menu_context[goal1_id]["route_hint"] = "/system"
    adapter._menu_context[goal2_id]["route_hint"] = "/system/user"

    # Mark first succeeded, second failed
    engine.goals[goal1_id].status = "succeeded"
    engine.goals[goal2_id].status = "failed"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "menu_entries.json"
        write_menu_fixture(adapter, output_path)

        assert output_path.exists()

        with output_path.open(encoding="utf-8") as f:
            entries = json.load(f)

        assert len(entries) == 2

        # Check first entry
        entry1 = entries[0]
        assert entry1["menu_id"] == "menu_001"
        assert entry1["menu_path"] == ["系统管理"]
        assert entry1["menu_text"] == "系统管理"
        assert entry1["route_hint"] == "/system"
        assert entry1["status"] == "discovered"
        assert entry1["parent_menu_id"] is None

        # Check second entry
        entry2 = entries[1]
        assert entry2["menu_id"] == "menu_002"
        assert entry2["menu_path"] == ["系统管理", "用户管理"]
        assert entry2["menu_text"] == "用户管理"
        assert entry2["route_hint"] == "/system/user"
        assert entry2["status"] == "failed"
        assert entry2["parent_menu_id"] == "menu_001"


def test_write_menu_fixture_with_screenshots():
    """Test fixture writer includes screenshot paths."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    # Register menu goal
    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )

    # Record attempt with screenshot
    attempt_id = adapter.record_discovery_attempt(goal_id=goal_id)
    step_id = adapter.record_navigation_step(
        attempt_id=attempt_id,
        action="click_menu",
    )
    adapter.attach_screenshot_evidence(
        step_id=step_id,
        screenshot_path="/screenshots/menu_001.png",
    )

    # Mark as succeeded
    engine.goals[goal_id].status = "succeeded"
    goal = engine.goals[goal_id]
    goal.last_attempt_id = attempt_id

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "menu_entries.json"
        write_menu_fixture(adapter, output_path)

        with output_path.open(encoding="utf-8") as f:
            entries = json.load(f)

        assert len(entries) == 1
        assert entries[0]["screenshot_path"] == "/screenshots/menu_001.png"


def test_write_menu_fixture_with_metadata():
    """Test fixture writer includes goal metadata."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    # Register menu goal
    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )

    # Record multiple attempts
    attempt1 = adapter.record_discovery_attempt(goal_id=goal_id)
    adapter.record_discovery_failure(
        attempt_id=attempt1,
        failure_class="menu_expand_failed",
        confidence="high",
    )

    attempt2 = adapter.record_discovery_attempt(goal_id=goal_id)
    step_id = adapter.record_navigation_step(
        attempt_id=attempt2,
        action="click_menu",
    )
    evidence_id = adapter.attach_screenshot_evidence(
        step_id=step_id,
        screenshot_path="/tmp/success.png",
    )
    adapter.record_discovery_success(
        attempt_id=attempt2,
        evidence_refs=[evidence_id],
    )

    # Add conclusion
    goal = engine.goals[goal_id]
    goal.conclusion = "Menu discovered after retry"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "menu_entries.json"
        write_menu_fixture(adapter, output_path)

        with output_path.open(encoding="utf-8") as f:
            entries = json.load(f)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["metadata"]["goal_id"] == goal_id
        assert entry["metadata"]["attempts"] == 2
        assert entry["metadata"]["conclusion"] == "Menu discovered after retry"


def test_write_menu_fixture_excludes_non_menu_goals():
    """Test fixture writer only exports menu entry goals."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    # Register menu goal via adapter
    menu_goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )

    # Register non-menu goal directly
    other_goal = engine.register_goal(
        goal_type="page",
        goal_name="Other goal",
        origin="other::task_001",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "menu_entries.json"
        write_menu_fixture(adapter, output_path)

        with output_path.open(encoding="utf-8") as f:
            entries = json.load(f)

        # Only menu goal should be exported
        assert len(entries) == 1
        assert entries[0]["menu_id"] == "menu_001"


def test_write_menu_fixture_cjk_encoding():
    """Test fixture writer preserves CJK characters."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    # Register menu with Chinese text
    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理", "用户管理", "权限设置"],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "menu_entries.json"
        write_menu_fixture(adapter, output_path)

        # Read back and verify CJK preserved
        with output_path.open(encoding="utf-8") as f:
            content = f.read()
            entries = json.loads(content)

        assert "系统管理" in content
        assert "用户管理" in content
        assert "权限设置" in content

        assert entries[0]["menu_path"] == ["系统管理", "用户管理", "权限设置"]
        assert entries[0]["menu_text"] == "权限设置"


def test_write_menu_fixture_sorted_output():
    """Test fixture writer sorts entries by menu_id."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    # Register menus in random order
    adapter.register_menu_goal(menu_id="menu_003", menu_path=["C"])
    adapter.register_menu_goal(menu_id="menu_001", menu_path=["A"])
    adapter.register_menu_goal(menu_id="menu_002", menu_path=["B"])

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "menu_entries.json"
        write_menu_fixture(adapter, output_path)

        with output_path.open(encoding="utf-8") as f:
            entries = json.load(f)

        # Should be sorted by menu_id
        assert entries[0]["menu_id"] == "menu_001"
        assert entries[1]["menu_id"] == "menu_002"
        assert entries[2]["menu_id"] == "menu_003"
