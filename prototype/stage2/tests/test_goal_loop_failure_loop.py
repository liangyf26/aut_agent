"""Repeated-failure loop: stop-status mutation + systematic-defect escalation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop.predicates import Thresholds  # noqa: E402
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine  # noqa: E402


def test_max_rounds_drives_goal_to_failed_status() -> None:
    # keep no_progress_threshold above the round budget so max_rounds fires alone
    engine = GoalLoopEngine(
        "run-loop", thresholds=Thresholds(default_max_rounds=2, no_progress_threshold=5)
    )
    goal = engine.register_goal("menu", "菜单发现", max_rounds=2)
    engine.activate_next()
    for _ in range(2):
        attempt = engine.start_attempt()
        # made_progress=True each round: the goal advances but still runs out of budget
        engine.record_failure(
            attempt.attempt_id, explicit_class="menu_not_found", made_progress=True
        )
    ev = engine.evaluate_stop()
    assert ev.should_stop is True
    assert ev.primary_reason == "max_rounds_reached"
    assert engine.goals[goal.goal_id].status == "failed_max_rounds"
    assert engine.goals[goal.goal_id].stop_reason == "max_rounds_reached"


def test_no_progress_streak_stops_before_max_rounds() -> None:
    engine = GoalLoopEngine("run-loop2", thresholds=Thresholds(no_progress_threshold=2))
    engine.register_goal("page", "进入页面", max_rounds=10)
    engine.activate_next()
    for _ in range(2):
        attempt = engine.start_attempt()
        engine.record_failure(
            attempt.attempt_id, explicit_class="page_blank", made_progress=False
        )
    ev = engine.evaluate_stop()
    assert ev.primary_reason == "no_progress_repeated"


def test_made_progress_resets_streak() -> None:
    engine = GoalLoopEngine("run-loop3", thresholds=Thresholds(no_progress_threshold=2))
    goal = engine.register_goal("page", "进入页面", max_rounds=10)
    engine.activate_next()
    a1 = engine.start_attempt()
    engine.record_failure(a1.attempt_id, explicit_class="page_blank", made_progress=False)
    a2 = engine.start_attempt()
    engine.record_failure(a2.attempt_id, explicit_class="page_blank", made_progress=True)
    assert engine.goals[goal.goal_id].no_improvement_streak == 0


def test_escalation_counter_triggers_after_threshold() -> None:
    engine = GoalLoopEngine(
        "run-esc",
        thresholds=Thresholds(
            escalation_occurrence_threshold=3, escalation_success_floor=0.0, default_max_rounds=10
        ),
    )
    engine.register_goal("menu", "菜单发现", max_rounds=10)
    engine.activate_next()
    for _ in range(3):
        attempt = engine.start_attempt()
        engine.record_failure(attempt.attempt_id, explicit_class="locator_unstable")

    rows = engine.evaluate_escalations()
    row = next(r for r in rows if r.failure_class == "locator_unstable")
    assert row.occurrences == 3
    assert row.playbook_success_rate == 0.0
    assert row.triggered is True

    emitted = engine.record_escalation_experiences()
    assert any(u.kind == "escalation" for u in emitted)


def test_recovery_credits_playbook_success_rate() -> None:
    engine = GoalLoopEngine("run-esc2", thresholds=Thresholds(escalation_occurrence_threshold=1))
    engine.register_goal("menu", "菜单发现", max_rounds=10)
    engine.activate_next()
    a1 = engine.start_attempt()
    engine.record_failure(a1.attempt_id, explicit_class="menu_click_failed")
    a2 = engine.start_attempt()
    engine.record_success(
        a2.attempt_id, signals={"menu_text": "x", "path": "p", "screenshot": "s.png"}
    )
    row = next(r for r in engine.evaluate_escalations() if r.failure_class == "menu_click_failed")
    # one occurrence, one recovery -> success_rate 1.0, not escalated
    assert row.playbook_success_rate == 1.0
    assert row.triggered is False
