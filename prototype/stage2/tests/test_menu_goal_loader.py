"""Test menu_entries.json fixture loader (Finding 3 from adversarial review)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop.models import GOAL_TYPE_MENU, STATUS_PLANNED  # noqa: E402
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine  # noqa: E402
from prototype.stage2.app.menu_goal.loader import load_menu_goals_from_fixture  # noqa: E402


def test_load_menu_goals_from_fixture_registers_goals() -> None:
    """Verify loader creates one goal per menu_entry with correct origin."""

    fixture = {
        "schema_version": "stage2_menu_entries.v1",
        "menu_entries": [
            {
                "menu_id": "menu_001",
                "text": "订单管理",
                "level": 1,
                "parent_id": None,
                "menu_path": ["订单管理"],
                "is_leaf": False,
                "route_hint": "/orders",
                "status": "discovered",
            },
            {
                "menu_id": "menu_002",
                "text": "系统管理",
                "level": 1,
                "parent_id": None,
                "menu_path": ["系统管理"],
                "is_leaf": False,
                "route_hint": "/system",
                "status": "expanded",
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
        fixture_path = f.name

    try:
        engine = GoalLoopEngine("run-loader-test")
        goal_ids = load_menu_goals_from_fixture(engine, fixture_path)

        assert len(goal_ids) == 2
        assert all(gid in engine.goals for gid in goal_ids)

        # First goal
        goal1 = engine.goals[goal_ids[0]]
        assert goal1.goal_type == GOAL_TYPE_MENU
        assert goal1.goal_name == "订单管理"
        assert goal1.status == STATUS_PLANNED
        assert goal1.origin == "menu_entry::menu_001"
        assert goal1.goal_id in engine.frontier
        assert any("menu_path=" in note for note in goal1.notes)
        assert any("route_hint=/orders" in note for note in goal1.notes)

        # Second goal
        goal2 = engine.goals[goal_ids[1]]
        assert goal2.goal_name == "系统管理"
        assert goal2.origin == "menu_entry::menu_002"
    finally:
        Path(fixture_path).unlink(missing_ok=True)


def test_load_menu_goals_with_parent_goal() -> None:
    """Verify loader can attach menu goals to a parent goal."""

    fixture = {
        "menu_entries": [
            {
                "menu_id": "menu_001",
                "text": "订单管理",
                "menu_path": ["订单管理"],
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
        fixture_path = f.name

    try:
        engine = GoalLoopEngine("run-parent-test")
        parent = engine.register_goal("menu", "菜单发现会话")
        goal_ids = load_menu_goals_from_fixture(engine, fixture_path, parent_goal_id=parent.goal_id)

        menu_goal = engine.goals[goal_ids[0]]
        assert menu_goal.parent_goal_id == parent.goal_id
        assert menu_goal.goal_id in engine.goals[parent.goal_id].child_goal_ids
    finally:
        Path(fixture_path).unlink(missing_ok=True)


def test_load_menu_goals_missing_file_raises() -> None:
    """Verify loader raises FileNotFoundError for missing fixture."""

    engine = GoalLoopEngine("run-missing")
    with pytest.raises(FileNotFoundError):
        load_menu_goals_from_fixture(engine, "/nonexistent/menu_entries.json")


def test_load_menu_goals_invalid_schema_raises() -> None:
    """Verify loader raises ValueError for invalid schema."""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"menu_entries": "not_a_list"}, f)
        fixture_path = f.name

    try:
        engine = GoalLoopEngine("run-invalid")
        with pytest.raises(ValueError):
            load_menu_goals_from_fixture(engine, fixture_path)
    finally:
        Path(fixture_path).unlink(missing_ok=True)


def test_loader_handles_cjk_menu_text() -> None:
    """Verify loader preserves CJK menu text correctly (memory: CJK deployment)."""

    fixture = {
        "menu_entries": [
            {
                "menu_id": "menu_cjk",
                "text": "系统管理",
                "menu_path": ["系统管理", "用户管理"],
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
        fixture_path = f.name

    try:
        engine = GoalLoopEngine("run-cjk")
        goal_ids = load_menu_goals_from_fixture(engine, fixture_path)
        goal = engine.goals[goal_ids[0]]
        assert goal.goal_name == "系统管理"
        assert any("系统管理" in note and "用户管理" in note for note in goal.notes)
    finally:
        Path(fixture_path).unlink(missing_ok=True)
