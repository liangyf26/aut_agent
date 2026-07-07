"""Regression tests pinning the fixes from the adversarial review.

Each test maps to a confirmed finding so a regression re-surfaces the exact bug.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop import classification as fc  # noqa: E402
from prototype.stage2.app.goal_loop import compat  # noqa: E402
from prototype.stage2.app.goal_loop import predicates as pred  # noqa: E402
from prototype.stage2.app.goal_loop.models import (  # noqa: E402
    PAUSED_STATUSES,
    STATUS_RUNNING,
    STATUS_SUPERSEDED,
    STATUS_WAITING_HUMAN,
    TERMINAL_STATUSES,
)
from prototype.stage2.app.goal_loop.predicates import Thresholds  # noqa: E402
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine  # noqa: E402
from prototype.stage2.app.goal_loop.writer import GoalLoopWriter  # noqa: E402


def _menu_ok() -> dict:
    return {"menu_text": "x", "path": "p", "screenshot": "s.png"}


# --- finding 1 & 12: supersede -----------------------------------------------

def test_supersede_active_conclude_and_route_successor() -> None:
    engine = GoalLoopEngine("r")
    a = engine.register_goal("menu", "A")
    b = engine.register_goal("menu", "B")
    c = engine.register_goal("menu", "C")
    engine.activate_next()  # A running
    engine.supersede_active(c.goal_id)
    # replaced goal is concluded (not still running) and blocks nothing
    assert engine.goals[a.goal_id].status == STATUS_SUPERSEDED
    assert STATUS_SUPERSEDED in TERMINAL_STATUSES
    assert engine.goals[a.goal_id].superseded_by == c.goal_id
    # the NAMED successor runs next, not the first-registered frontier goal
    nxt = engine.activate_next()
    assert nxt.goal_id == c.goal_id


def test_superseded_goal_cannot_take_attempts() -> None:
    engine = GoalLoopEngine("r")
    a = engine.register_goal("menu", "A")
    b = engine.register_goal("menu", "B")
    engine.activate_next()
    engine.supersede_active(b.goal_id)
    with pytest.raises(ValueError):
        engine.start_attempt(a.goal_id)


def test_supersede_unknown_successor_rejected() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("menu", "A")
    engine.activate_next()
    with pytest.raises(ValueError):
        engine.supersede_active("goal-does-not-exist")


# --- finding 4: paused statuses are not terminal; resume works ---------------

def test_waiting_human_is_not_terminal_and_blocks_activation() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("page", "P")
    engine.register_goal("page", "Q")
    engine.activate_next()
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="login_required")
    ev = engine.evaluate_stop()
    assert ev.target_status == STATUS_WAITING_HUMAN
    assert STATUS_WAITING_HUMAN in PAUSED_STATUSES
    assert STATUS_WAITING_HUMAN not in TERMINAL_STATUSES
    # a paused active goal must NOT be silently abandoned
    with pytest.raises(ValueError):
        engine.activate_next()


def test_resume_goal_returns_to_running() -> None:
    engine = GoalLoopEngine("r")
    goal = engine.register_goal("page", "P")
    engine.activate_next()
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="login_required")
    engine.evaluate_stop()
    resumed = engine.resume_goal(goal.goal_id)
    assert resumed.status == STATUS_RUNNING
    assert engine.goals[goal.goal_id].stop_reason is None
    # can continue after resume
    engine.start_attempt()


def test_resume_goal_refuses_when_another_goal_still_running() -> None:
    # NB3 regression: resuming a paused NON-active goal while A is still running
    # must not produce two RUNNING goals.
    engine = GoalLoopEngine("r")
    a = engine.register_goal("menu", "A")
    b = engine.register_goal("menu", "B")
    engine.activate_next()  # A running, active=A
    engine.evaluate_stop(goal_id=b.goal_id, policy_blocked=True)  # park planned B
    with pytest.raises(ValueError):
        engine.resume_goal(b.goal_id)
    running = [g for g, go in engine.goals.items() if go.status == STATUS_RUNNING]
    assert running == [a.goal_id]


def test_supersede_releases_active_slot() -> None:
    # NB4 regression: after supersede, the run-center view must not report the
    # concluded goal as current.
    engine = GoalLoopEngine("r")
    engine.register_goal("menu", "A")
    b = engine.register_goal("menu", "B")
    engine.activate_next()
    engine.supersede_active(b.goal_id)
    assert engine.active_goal_id is None
    with tempfile.TemporaryDirectory() as tmp:
        paths = GoalLoopWriter(tmp).write_all(engine)
        payload = json.loads(paths["goal_current_status"].read_text(encoding="utf-8"))
        assert payload["overall_status"] == "pending"  # placeholder, not the superseded goal


# --- finding 2: record_success state guard -----------------------------------

def test_record_success_cannot_resurrect_terminal_goal() -> None:
    # goal terminal but attempt STILL RUNNING, so only the goal-status guard can
    # fire (the attempt-status guard cannot mask its removal).
    engine = GoalLoopEngine("r", thresholds=Thresholds(default_max_rounds=1, no_progress_threshold=9))
    goal = engine.register_goal("menu", "A", max_rounds=1)
    engine.activate_next()
    a1 = engine.start_attempt()  # attempt_count=1, a1 still RUNNING
    ev = engine.evaluate_stop()  # attempt_count(1) >= max_rounds(1) -> failed_max_rounds
    assert ev.primary_reason == "max_rounds_reached"
    assert engine.goals[goal.goal_id].status == "failed_max_rounds"
    assert a1.status == "running"  # attempt was never concluded
    with pytest.raises(ValueError) as exc:
        engine.record_success(a1.attempt_id, signals=_menu_ok())
    assert "not running" in str(exc.value)  # references the GOAL status guard


# --- finding 3: record_failure evidence validation ---------------------------

def test_record_failure_rejects_cross_attempt_evidence() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("page", "P1")
    p2 = engine.register_goal("page", "P2")
    # P1: real evidence on attempt 1, then conclude P1 so the frontier can advance
    engine.activate_next()
    a1 = engine.start_attempt()
    s1 = engine.add_step(a1.attempt_id, "action")
    real_ev = engine.attach_evidence(s1.step_id, "screenshot")
    engine.record_success(
        a1.attempt_id,
        signals={"http_ok": True, "has_main_content": True, "visible_text_len": 200, "dom_nodes": 40},
    )
    # P2 attempt cites P1's evidence (cross-attempt) plus a fabricated id
    engine.activate_next()
    assert engine.active_goal_id == p2.goal_id
    a2 = engine.start_attempt()
    cls, action = engine.record_failure(
        a2.attempt_id,
        explicit_class="assertion_failed",
        evidence_refs=[real_ev.evidence_id, "goal-999999-a99-s999-e001"],
    )
    # cross-attempt + fabricated refs are a chain break -> evidence_incomplete
    assert cls.failure_reason == fc.EVIDENCE_INCOMPLETE
    assert cls.evidence_refs == []  # nothing bound to this attempt survived
    assert action.playbook_id == "pb_evidence_incomplete"


# --- finding 13: success is evidence-gated -----------------------------------

def test_record_success_refuses_when_observed_step_has_no_evidence() -> None:
    engine = GoalLoopEngine("r", thresholds=Thresholds(no_progress_threshold=2))
    goal = engine.register_goal("menu", "A")
    engine.activate_next()
    a = engine.start_attempt()
    engine.add_step(a.attempt_id, "assertion", observed=True)  # no evidence attached
    with pytest.raises(ValueError):
        engine.record_success(a.attempt_id, signals=_menu_ok())
    # the refusal is loud but PURE: it must not be counted as a real failure —
    # attempt stays running, streak untouched, no defect-counter pollution, so
    # the caller can attach evidence and retry the same attempt.
    assert a.status == "running"
    assert engine.goals[goal.goal_id].no_improvement_streak == 0
    assert "evidence_incomplete" not in engine._defect_counter
    assert engine.classifications == []
    # attaching the missing evidence then retrying the SAME attempt succeeds
    step = engine.steps[a.steps[0].step_id]
    engine.attach_evidence(step.step_id, "screenshot")
    result, _ = engine.record_success(a.attempt_id, signals=_menu_ok())
    assert result.value is True


# --- finding 19: no overlapping attempts -------------------------------------

def test_start_attempt_rejects_overlapping_attempt() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("menu", "A")
    engine.activate_next()
    engine.start_attempt()
    with pytest.raises(ValueError):
        engine.start_attempt()


# --- finding 8: allow_human_intervention gates waiting_human ------------------

def test_allow_human_intervention_false_falls_through() -> None:
    engine = GoalLoopEngine("r", thresholds=Thresholds(no_progress_threshold=1))
    engine.register_goal("page", "restricted", allow_human_intervention=False, max_rounds=5)
    engine.activate_next()
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="login_required")
    ev = engine.evaluate_stop()
    # human intervention disallowed -> NOT parked in waiting_human
    assert ev.primary_reason != "waiting_human"
    assert engine.goals[engine.active_goal_id].status != STATUS_WAITING_HUMAN


def test_allow_human_intervention_true_parks_waiting() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("page", "normal", allow_human_intervention=True, max_rounds=5)
    engine.activate_next()
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="login_required")
    ev = engine.evaluate_stop()
    assert ev.primary_reason == "waiting_human"


# --- finding 6 & 17: blank_screenshot_ratio + OR semantics -------------------

def test_blank_ratio_alone_marks_page_blank() -> None:
    # populated DOM shell but visually blank screenshot
    r = pred.evaluate_success(
        goal_type="page",
        signals={"http_ok": True, "has_main_content": True, "visible_text_len": 200,
                 "dom_nodes": 40, "blank_screenshot_ratio": 0.99},
    )
    assert r.params["is_blank"] is True
    assert r.value is False


def test_blank_or_semantics_single_signal_below_threshold() -> None:
    # only dom_nodes below threshold
    r = pred.evaluate_success(
        goal_type="page",
        signals={"http_ok": True, "has_main_content": True, "visible_text_len": 200, "dom_nodes": 1},
    )
    assert r.params["is_blank"] is True


def test_blank_boundary_values_not_blank() -> None:
    r = pred.evaluate_success(
        goal_type="page",
        signals={"http_ok": True, "has_main_content": True, "visible_text_len": 20, "dom_nodes": 5},
    )
    assert r.params["is_blank"] is False
    assert r.value is True


# --- finding 5 & 9 & 7 & 16 & 20: classifier robustness ----------------------

def test_number_containing_403_is_not_permission_blocked() -> None:
    cls, _ = fc.classify_failure(signals="page load took 1403ms, timed out")
    assert cls == "page_load_timeout"


def test_ascii_keyword_adjacent_to_cjk_still_matches() -> None:
    # NB1 regression guard: Unicode \w treats CJK as word chars, so a naive
    # word-boundary would fail here; re.ASCII boundaries must still match.
    assert fc.classify_failure(signals="请求timeout了")[0] == "page_load_timeout"
    assert fc.classify_failure(signals="浏览器browser use unavailable")[0] == "browser_use_unavailable"


def test_unexpected_is_not_assertion_failed() -> None:
    # real word-boundary guard: flips to assertion_failed under substring matching
    cls, _ = fc.classify_failure(signals="unexpected error occurred")
    assert cls == fc.UNKNOWN


def test_dict_signals_use_values_not_keys() -> None:
    cls, _ = fc.classify_failure(signals={"message": "menu not found", "detail": "no menu"})
    assert cls == "menu_not_found"


def test_locator_beats_blank_when_selector_mentioned() -> None:
    # both current keyword forms present, so ORDERING alone decides the winner;
    # isolates the locator-before-page_blank ordering fix.
    cls, _ = fc.classify_failure(signals="selector failed on a blank page")
    assert cls == "locator_unstable"


# --- finding 10 & 11: compat vocabularies ------------------------------------

def test_current_status_view_uses_run_center_vocabulary() -> None:
    run_center_statuses = {"pending", "running", "completed", "failed", "skipped", "waiting_human", "blocked"}
    m = compat._OVERALL_STATUS_BY_GOAL_STATUS
    # membership sweep: no foreign tokens
    for value in m.values():
        assert value in run_center_statuses
    # plus specific load-bearing mappings so a wrong-but-valid mapping is caught
    assert m["succeeded"] == "completed"
    assert m["superseded"] == "skipped"
    assert m["failed_max_rounds"] == "failed"
    assert m["stopped_no_progress"] == "failed"
    assert m["blocked_by_policy"] == "blocked"
    assert m["blocked_by_executor"] == "blocked"
    assert m["waiting_human"] == "waiting_human"


def test_promotion_candidate_target_is_iteration_vocabulary() -> None:
    from prototype.stage2.app.goal_loop.models import ExperienceUpdate

    platform = ExperienceUpdate(update_id="e1", source_goal="g", kind="escalation", promotion_level="platform")
    cand = compat.experience_update_to_promotion_candidate(platform)
    assert cand.promotion_target == "project_baseline_freeze"
    assert cand.needs_manual_review is True
    assert cand.promotion_recommendation == "manual_review"

    run_local = ExperienceUpdate(update_id="e2", source_goal="g", kind="winning", promotion_level="run")
    cand2 = compat.experience_update_to_promotion_candidate(run_local)
    assert cand2.promotion_target is None  # -> summarizer records 'unspecified'


def test_cluster_action_level_reuses_iteration_mapping() -> None:
    from prototype.stage2.app.goal_loop.models import FailureClassification

    record = FailureClassification(
        classification_id="fc-1", goal_id="g", attempt_id="g-a01",
        failure_reason="menu_expand_failed", reason_confidence="high",
        suggested_playbook="pb_menu_expand_failed", iteration_category="ui",
        evidence_refs=["g-a01-s001-e001"],
    )
    cluster = compat.failure_classification_to_cluster(record)
    assert cluster.action_level == "workflow"  # ui -> workflow, not 'agent'
    # evidence emitted one dict per ref with the attempt parent pointer
    assert cluster.evidence == [{"evidence_id": "g-a01-s001-e001", "attempt_id": "g-a01"}]


# --- finding 14: escalation occurrence-threshold term ------------------------

def test_escalation_occurrence_term_is_independent() -> None:
    # non-default threshold (2) so the test proves the configured parameter is
    # actually read, not a hard-coded 3.
    engine = GoalLoopEngine("r", thresholds=Thresholds(escalation_occurrence_threshold=2, default_max_rounds=10))
    engine.register_goal("menu", "A", max_rounds=10)
    engine.activate_next()
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="locator_unstable")
    row1 = next(r for r in engine.evaluate_escalations() if r.failure_class == "locator_unstable")
    assert row1.occurrences == 1 and row1.triggered is False  # below configured threshold
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="locator_unstable")
    row2 = next(r for r in engine.evaluate_escalations() if r.failure_class == "locator_unstable")
    assert row2.occurrences == 2 and row2.triggered is True  # flips at configured 2, not default 3


# --- finding 15: engine-level blocked/waiting status + compat mapping --------

def test_engine_evaluate_stop_sets_blocked_status_and_view() -> None:
    engine = GoalLoopEngine("r")
    goal = engine.register_goal("page", "P", max_rounds=9)
    engine.activate_next()
    a = engine.start_attempt()
    engine.record_failure(a.attempt_id, explicit_class="page_blank")
    ev = engine.evaluate_stop(policy_blocked=True)
    assert ev.target_status == "blocked_by_policy"
    assert engine.goals[goal.goal_id].status == "blocked_by_policy"
    view = compat.goal_summary_to_current_status_view(engine.build_summary(goal.goal_id), run_id="r")
    assert view["overall_status"] == "blocked"
    assert view["blocked_reason"] == "blocked_by_policy"


# --- finding 23: writer always emits current-status file ---------------------

def test_writer_emits_current_status_even_without_active_goal() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("menu", "A")  # registered but never activated
    with tempfile.TemporaryDirectory() as tmp:
        paths = GoalLoopWriter(tmp).write_all(engine)
        assert paths["goal_current_status"].exists()
        payload = json.loads(paths["goal_current_status"].read_text(encoding="utf-8"))
        assert payload["overall_status"] == "pending"


# --- finding 24: idempotency across all artifacts ----------------------------

def test_writer_idempotent_across_all_artifacts() -> None:
    engine = GoalLoopEngine("r")
    engine.register_goal("menu", "A")
    engine.activate_next()
    a1 = engine.start_attempt()
    engine.record_failure(a1.attempt_id, explicit_class="menu_not_found")
    a2 = engine.start_attempt()
    engine.record_success(a2.attempt_id, signals=_menu_ok())
    with tempfile.TemporaryDirectory() as tmp:
        writer = GoalLoopWriter(tmp)
        first = {k: v.read_text(encoding="utf-8") for k, v in writer.write_all(engine).items()}
        second = {k: v.read_text(encoding="utf-8") for k, v in writer.write_all(engine).items()}
        assert first == second  # byte-identical across a second write
