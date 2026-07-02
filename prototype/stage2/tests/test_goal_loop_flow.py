"""End-to-end walk of the goal loop, asserting 实施计划v4 §3.4 standards 1-8."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop import compat  # noqa: E402
from prototype.stage2.app.goal_loop.classification import FIXED_FAILURE_CLASSES  # noqa: E402
from prototype.stage2.app.goal_loop.ids import parent_of  # noqa: E402
from prototype.stage2.app.goal_loop.models import (  # noqa: E402
    STATUS_PLANNED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine  # noqa: E402


def _menu_success_signals() -> dict:
    return {"menu_text": "订单管理", "path": "首页/订单管理", "screenshot": "s1.png"}


def test_standard_1_planned_to_running() -> None:
    engine = GoalLoopEngine("run-1")
    goal = engine.register_goal("menu", "一级菜单发现")
    assert goal.status == STATUS_PLANNED
    assert goal.goal_id in engine.frontier

    activated = engine.activate_next()
    assert activated is not None
    assert activated.goal_id == goal.goal_id
    assert engine.goals[goal.goal_id].status == STATUS_RUNNING
    assert engine.active_goal_id == goal.goal_id
    # frontier drained on activation
    assert goal.goal_id not in engine.frontier


def test_single_active_goal_enforced() -> None:
    engine = GoalLoopEngine("run-1")
    engine.register_goal("menu", "菜单A")
    engine.register_goal("menu", "菜单B")
    engine.activate_next()
    with pytest.raises(ValueError):
        engine.activate_next()  # first goal still running


def test_standard_2_and_3_failure_classification_and_playbook() -> None:
    engine = GoalLoopEngine("run-2")
    engine.register_goal("menu", "菜单发现")
    engine.activate_next()
    attempt = engine.start_attempt()
    classification, action = engine.record_failure(
        attempt.attempt_id, explicit_class="menu_expand_failed"
    )
    # standard 2: classification is inside the fixed enum
    assert classification.failure_reason in FIXED_FAILURE_CLASSES
    assert classification.failure_reason == "menu_expand_failed"
    assert classification.reason_confidence == "high"
    # standard 3: playbook selected from the fixed table
    assert action.playbook_id == "pb_menu_expand_failed"
    assert action.trigger_reason == "menu_expand_failed"
    assert action.action_steps  # non-empty fixed steps
    # the classification also projects onto the emergent aggregator vocabulary
    assert classification.iteration_category == "ui"


def test_standard_8_evidence_chain_and_incomplete() -> None:
    engine = GoalLoopEngine("run-3")
    goal = engine.register_goal("page", "进入页面")
    engine.activate_next()
    attempt = engine.start_attempt()
    step = engine.add_step(attempt.attempt_id, "action", action="click")
    ev = engine.attach_evidence(step.step_id, "screenshot", uri="p.png")

    # every level traces to its parent, both via stored pointer and id shape
    assert ev.owner_step_id == step.step_id
    assert step.attempt_id == attempt.attempt_id
    assert attempt.goal_id == goal.goal_id
    assert parent_of(ev.evidence_id) == step.step_id
    assert parent_of(step.step_id) == attempt.attempt_id
    assert parent_of(attempt.attempt_id) == goal.goal_id
    assert parent_of(goal.goal_id) is None

    # a fully-evidenced attempt has no gaps
    assert engine.check_evidence_complete(attempt.attempt_id) == []

    # run-level evidence (unknown step) is rejected, not silently accepted
    with pytest.raises(ValueError):
        engine.attach_evidence("goal-999999-a99-s999", "screenshot")

    # an observed step without evidence is surfaced as a gap
    bare = engine.add_step(attempt.attempt_id, "assertion", observed=True)
    gaps = engine.check_evidence_complete(attempt.attempt_id)
    assert any(bare.step_id in gap for gap in gaps)


def test_standard_4_and_7_summary_and_goal_tree() -> None:
    engine = GoalLoopEngine("run-4")
    menu = engine.register_goal("menu", "菜单发现")
    engine.activate_next()

    # attempt 1 fails, attempt 2 succeeds
    a1 = engine.start_attempt()
    engine.record_failure(a1.attempt_id, explicit_class="menu_click_failed")
    a2 = engine.start_attempt()
    result, experience = engine.record_success(a2.attempt_id, signals=_menu_success_signals())
    assert result.value is True
    assert engine.goals[menu.goal_id].status == STATUS_SUCCEEDED
    assert experience.kind == "winning"

    # standard 4: readable summary
    summary = engine.build_summary(menu.goal_id)
    assert summary.succeeded is True
    assert summary.attempt_count == 2
    assert summary.primary_failure_class == "menu_click_failed"

    # standard 7: derive a child page goal — parent/child links + frontier
    page = engine.derive_child_goal(menu.goal_id, "page", "进入订单页", origin="menu_item::orders")
    assert page.parent_goal_id == menu.goal_id
    assert page.goal_id in engine.goals[menu.goal_id].child_goal_ids
    assert page.goal_id in engine.frontier

    # the succeeded menu is terminal, so the frontier can advance to the child
    nxt = engine.activate_next()
    assert nxt is not None and nxt.goal_id == page.goal_id


def test_standard_5_current_status_projection() -> None:
    engine = GoalLoopEngine("run-5")
    goal = engine.register_goal("menu", "菜单发现")
    engine.activate_next()
    a1 = engine.start_attempt()
    engine.record_success(a1.attempt_id, signals=_menu_success_signals())

    view = compat.goal_summary_to_current_status_view(
        engine.build_summary(goal.goal_id), run_id="run-5"
    )
    assert view["run_id"] == "run-5"
    # overall_status must be a token the run center already emits, not a foreign one
    assert view["overall_status"] == "completed"
    assert view["current_target"]["id"] == goal.goal_id
    assert view["current_target"]["kind"] == "menu"
    assert view["stats"]["attempt_count"] == 1
