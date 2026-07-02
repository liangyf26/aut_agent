"""Tests for MenuGoalOrchestrator."""

import json
import tempfile
from pathlib import Path

from prototype.stage2.app.menu_goal import MenuGoalOrchestrator


def test_orchestrator_initializes_with_output_dir():
    """Test orchestrator creates output directory on initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "menu_output"
        orch = MenuGoalOrchestrator(output_dir=output_dir)

        assert output_dir.exists()
        assert orch.output_dir == output_dir
        assert orch.run_id is not None
        assert orch.run_id == orch.engine.run_id


def test_orchestrator_creates_root_goal():
    """Test orchestrator can create root menu discovery goal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orch = MenuGoalOrchestrator(output_dir=tmpdir)

        root_id = orch.create_root_goal(description="Discover all L1 menus")

        assert root_id in orch.engine.goals
        root_goal = orch.engine.goals[root_id]
        assert root_goal.goal_name == "Discover all L1 menus"
        assert root_goal.origin == "menu_discovery::root"
        assert orch.get_root_goal() == root_goal


def test_orchestrator_export_fixture():
    """Test orchestrator exports menu_entries.json fixture."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orch = MenuGoalOrchestrator(output_dir=tmpdir)

        # Register a menu goal using adapter
        goal_id = orch.adapter.register_menu_goal(
            menu_id="menu_001",
            menu_path=["系统管理"],
        )
        menu_context = orch.adapter.get_menu_context(goal_id)
        menu_context["route_hint"] = "/system"

        # Export fixture
        fixture_path = orch.export_fixture()

        assert fixture_path.exists()
        assert fixture_path == orch.output_dir / "menu_entries.json"

        # Verify fixture content
        with fixture_path.open(encoding="utf-8") as f:
            entries = json.load(f)

        assert len(entries) == 1
        assert entries[0]["menu_id"] == "menu_001"
        assert entries[0]["menu_path"] == ["系统管理"]
        assert entries[0]["route_hint"] == "/system"


def test_orchestrator_get_summary():
    """Test orchestrator aggregates session summary."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orch = MenuGoalOrchestrator(output_dir=tmpdir)

        # Create root goal
        root_id = orch.create_root_goal()

        # Register some menu goals using adapter with parent
        goal1_id = orch.adapter.register_menu_goal(
            menu_id="menu_001",
            menu_path=["Menu 1"],
            parent_goal_id=root_id,
        )
        goal2_id = orch.adapter.register_menu_goal(
            menu_id="menu_002",
            menu_path=["Menu 2"],
            parent_goal_id=root_id,
        )

        # Mark one succeeded, one failed
        goal1 = orch.engine.goals[goal1_id]
        goal2 = orch.engine.goals[goal2_id]
        goal1.status = "succeeded"
        goal2.status = "failed"

        # Root goal remains pending (planned status)
        root_goal = orch.engine.goals[root_id]
        root_goal.stop_reason = "2 menus discovered, 1 succeeded"

        # Get summary
        summary = orch.get_summary()

        assert summary["run_id"] == orch.run_id
        assert summary["domain"] == "menu_discovery"
        assert summary["total_goals"] == 3  # root + 2 menus
        assert summary["succeeded"] == 1
        assert summary["failed"] == 1
        # Root goal is in planned state (counts as pending)
        assert summary["pending"] == 1  # root goal (planned status)
        assert summary["root_goal_id"] == root_id
        assert summary["root_conclusion"] == "2 menus discovered, 1 succeeded"


def test_orchestrator_custom_run_id():
    """Test orchestrator accepts custom session ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        custom_id = "test_session_123"
        orch = MenuGoalOrchestrator(
            output_dir=tmpdir,
            run_id=custom_id,
        )

        assert orch.run_id == custom_id
        assert orch.engine.run_id == custom_id
