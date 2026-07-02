"""Computable predicates + named thresholds (standard 6: every decision traces
to a predicate expression and a named threshold)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop import predicates as pred  # noqa: E402


def test_every_stop_condition_carries_expression_and_params() -> None:
    ev = pred.evaluate_stop_conditions(
        goal_type="menu", attempt_count=0, max_rounds=3, no_improvement_streak=0
    )
    assert {c.name for c in ev.conditions} == {
        "blocked_by_policy",
        "blocked_by_executor",
        "waiting_human",
        "no_progress_repeated",
        "max_rounds_reached",
    }
    for cond in ev.conditions:
        assert cond.expression  # non-empty computable expression
        assert isinstance(cond.params, dict)
    assert ev.should_stop is False


def test_max_rounds_predicate_and_named_threshold() -> None:
    ev = pred.evaluate_stop_conditions(
        goal_type="menu", attempt_count=3, max_rounds=3, no_improvement_streak=0
    )
    assert ev.should_stop is True
    assert ev.primary_reason == "max_rounds_reached"
    assert ev.target_status == "failed_max_rounds"
    cond = next(c for c in ev.conditions if c.name == "max_rounds_reached")
    assert cond.expression == "attempt_count >= max_rounds"
    assert cond.params == {"attempt_count": 3, "max_rounds": 3}


def test_no_progress_threshold_is_named_and_configurable() -> None:
    tight = pred.Thresholds(no_progress_threshold=2)
    ev = pred.evaluate_stop_conditions(
        goal_type="menu",
        attempt_count=1,
        max_rounds=5,
        no_improvement_streak=2,
        thresholds=tight,
    )
    assert ev.primary_reason == "no_progress_repeated"
    cond = next(c for c in ev.conditions if c.name == "no_progress_repeated")
    assert cond.params["no_progress_threshold"] == 2


def test_stop_priority_orders_policy_over_max_rounds() -> None:
    ev = pred.evaluate_stop_conditions(
        goal_type="page",
        attempt_count=9,
        max_rounds=3,
        no_improvement_streak=9,
        policy_blocked=True,
    )
    # several conditions fire; policy wins per priority
    assert ev.primary_reason == "blocked_by_policy"
    assert ev.target_status == "blocked_by_policy"
    assert len(ev.triggered) >= 3


def test_waiting_human_uses_human_required_classes() -> None:
    ev = pred.evaluate_stop_conditions(
        goal_type="feature",
        attempt_count=0,
        max_rounds=3,
        no_improvement_streak=0,
        active_failure_class="login_required",
    )
    assert ev.primary_reason == "waiting_human"
    assert ev.target_status == "waiting_human"


def test_page_success_predicate_blank_detection() -> None:
    good = pred.evaluate_success(
        goal_type="page",
        signals={"http_ok": True, "has_main_content": True, "visible_text_len": 200, "dom_nodes": 40},
    )
    assert good.value is True
    assert good.expression == "http_ok AND has_main_content AND NOT is_blank"

    blank = pred.evaluate_success(
        goal_type="page",
        signals={"http_ok": True, "has_main_content": True, "visible_text_len": 2, "dom_nodes": 1},
    )
    assert blank.value is False
    assert blank.params["is_blank"] is True
    assert blank.params["min_visible_text_len"] == pred.DEFAULT_THRESHOLDS.min_visible_text_len


def test_feature_success_requires_all_conditions() -> None:
    partial = pred.evaluate_success(
        goal_type="feature",
        signals={"feature_identified": True, "case_generated": True, "basic_path_executed": True},
    )
    assert partial.value is False  # missing has_feedback


def test_declared_success_criteria_records_thresholds() -> None:
    criteria = pred.success_criteria_for("page")
    assert len(criteria) == 1
    assert criteria[0]["expression"] == "http_ok AND has_main_content AND NOT is_blank"
    assert "min_visible_text_len" in criteria[0]["params"]
