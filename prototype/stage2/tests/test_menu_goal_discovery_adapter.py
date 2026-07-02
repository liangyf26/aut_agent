"""Tests for DiscoveryAdapter."""

import tempfile

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine
from prototype.stage2.app.menu_goal import DiscoveryAdapter


def test_adapter_registers_menu_goal():
    """Test adapter registers menu goal with context."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理", "用户管理"],
    )

    assert goal_id in engine.goals
    goal = engine.goals[goal_id]
    assert goal.goal_name == "Discover menu: 系统管理 > 用户管理"
    assert goal.origin == "menu_entry::menu_001"

    # Check menu context in adapter
    menu_context = adapter.get_menu_context(goal_id)
    assert menu_context["menu_id"] == "menu_001"
    assert menu_context["menu_path"] == ["系统管理", "用户管理"]
    assert menu_context["menu_depth"] == 2


def test_adapter_registers_hierarchical_goal():
    """Test adapter supports parent goal attachment."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    parent_id = adapter.register_menu_goal(
        menu_id="menu_parent",
        menu_path=["系统管理"],
    )

    child_id = adapter.register_menu_goal(
        menu_id="menu_child",
        menu_path=["系统管理", "用户管理"],
        parent_goal_id=parent_id,
    )

    child_goal = engine.goals[child_id]
    assert child_goal.parent_goal_id == parent_id


def test_adapter_records_discovery_attempt():
    """Test adapter records attempt with route hint."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )

    attempt_id = adapter.record_discovery_attempt(
        goal_id=goal_id,
        route_hint="/system",
    )

    assert attempt_id in engine.attempts
    attempt = engine.attempts[attempt_id]
    assert attempt.goal_id == goal_id

    # Check route hint in menu context
    menu_context = adapter.get_menu_context(goal_id)
    assert menu_context["route_hint"] == "/system"


def test_adapter_records_navigation_step():
    """Test adapter records navigation step with action metadata."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )
    attempt_id = adapter.record_discovery_attempt(goal_id=goal_id)

    step_id = adapter.record_navigation_step(
        attempt_id=attempt_id,
        action="click_menu",
        target=".nav-menu-item",
    )

    assert step_id in engine.steps
    step = engine.steps[step_id]
    assert step.attempt_id == attempt_id
    assert "click_menu" in step.description
    assert ".nav-menu-item" in step.description


def test_adapter_attaches_screenshot_evidence():
    """Test adapter attaches screenshot evidence with metadata."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )
    attempt_id = adapter.record_discovery_attempt(goal_id=goal_id)
    step_id = adapter.record_navigation_step(
        attempt_id=attempt_id,
        action="click_menu",
    )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        screenshot_path = f.name

    evidence_id = adapter.attach_screenshot_evidence(
        step_id=step_id,
        screenshot_path=screenshot_path,
        metadata={"timestamp": "2026-07-02T10:00:00Z"},
    )

    assert evidence_id in engine.evidence
    evidence = engine.evidence[evidence_id]
    assert evidence.evidence_type == "screenshot"
    assert evidence.content == screenshot_path
    assert evidence.metadata["timestamp"] == "2026-07-02T10:00:00Z"

    step = engine.steps[step_id]
    assert evidence_id in step.evidence_ids


def test_adapter_attaches_menu_metadata_evidence():
    """Test adapter attaches menu metadata evidence."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )
    attempt_id = adapter.record_discovery_attempt(goal_id=goal_id)
    step_id = adapter.record_navigation_step(
        attempt_id=attempt_id,
        action="extract_menu",
    )

    evidence_id = adapter.attach_menu_metadata_evidence(
        step_id=step_id,
        menu_text="系统管理",
        menu_html='<div class="menu">系统管理</div>',
        bounding_box={"x": 100, "y": 200, "width": 120, "height": 40},
    )

    assert evidence_id in engine.evidence
    evidence = engine.evidence[evidence_id]
    assert evidence.evidence_type == "menu_metadata"
    assert evidence.content == "系统管理"
    assert evidence.metadata["menu_text"] == "系统管理"
    assert "menu_html" in evidence.metadata
    assert evidence.metadata["bounding_box"]["x"] == 100


def test_adapter_records_discovery_failure():
    """Test adapter records failure with classification."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )
    attempt_id = adapter.record_discovery_attempt(goal_id=goal_id)
    step_id = adapter.record_navigation_step(
        attempt_id=attempt_id,
        action="click_menu",
    )
    evidence_id = adapter.attach_screenshot_evidence(
        step_id=step_id,
        screenshot_path="/tmp/failure.png",
    )

    adapter.record_discovery_failure(
        attempt_id=attempt_id,
        failure_class="menu_not_found",
        confidence="high",
        evidence_refs=[evidence_id],
        note="Menu element not present in DOM",
    )

    attempt = engine.attempts[attempt_id]
    assert attempt.status == "failed"
    assert attempt.failure_class == "menu_not_found"
    assert attempt.confidence == "high"
    assert evidence_id in attempt.evidence_refs
    assert "not present in DOM" in attempt.note


def test_adapter_records_discovery_success():
    """Test adapter records successful discovery."""
    engine = GoalLoopEngine(run_id="test_run")
    adapter = DiscoveryAdapter(engine)

    goal_id = adapter.register_menu_goal(
        menu_id="menu_001",
        menu_path=["系统管理"],
    )
    attempt_id = adapter.record_discovery_attempt(goal_id=goal_id)
    step_id = adapter.record_navigation_step(
        attempt_id=attempt_id,
        action="click_menu",
    )
    screenshot_ev = adapter.attach_screenshot_evidence(
        step_id=step_id,
        screenshot_path="/tmp/success.png",
    )
    metadata_ev = adapter.attach_menu_metadata_evidence(
        step_id=step_id,
        menu_text="系统管理",
    )

    adapter.record_discovery_success(
        attempt_id=attempt_id,
        evidence_refs=[screenshot_ev, metadata_ev],
        note="Menu discovered successfully",
    )

    attempt = engine.attempts[attempt_id]
    assert attempt.status == "succeeded"
    assert screenshot_ev in attempt.evidence_refs
    assert metadata_ev in attempt.evidence_refs
    assert "successfully" in attempt.note
