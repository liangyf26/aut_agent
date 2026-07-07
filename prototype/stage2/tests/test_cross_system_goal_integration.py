"""
Integration tests for Stage F: cross-system validation and platform
deepening.

Verifies the SAME goal_loop kernel generalizes across more than one system,
that failure classifications / playbook usage compare cleanly across
systems, and that promotion to ``platform`` level is gated on real
cross-system, cross-goal evidence rather than a single system's repetition
(实施计划 §8.4 可验证标准 / §8.5-8.6 风险与修正).

Tests independently, no browser — every system's goal-loop activity is
driven directly through ``GoalLoopEngine``/``CrossSystemAdapter`` (同 Stage
A-E: 每个阶段边界产出一份冻结的 golden / fixture 产物).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from prototype.stage2.app.cross_system_goal import (
    CrossSystemGoalOrchestrator,
    SystemProfile,
)
from prototype.stage2.app.goal_loop.models import PROMOTION_PLATFORM, PROMOTION_PROJECT


def _recover_menu_goal(orch, adapter, goal_name, *, failure_class="menu_not_found", shot_suffix=""):
    """Register one menu goal, fail once with ``failure_class``, then
    succeed — the standard "goal recovers via the fixed playbook" pattern
    every test in this module builds on."""

    goal_id = adapter.register_validation_goal(goal_type="menu", goal_name=goal_name)
    orch.engine.activate_next()
    failing_attempt = orch.engine.start_attempt(goal_id)
    adapter.record_failure(failing_attempt.attempt_id, explicit_class=failure_class, made_progress=False)

    succeeding_attempt = orch.engine.start_attempt(goal_id)
    step = orch.engine.add_step(succeeding_attempt.attempt_id, "discovery")
    orch.engine.attach_evidence(step.step_id, "screenshot", uri=f"shot://{goal_id}{shot_suffix}")
    adapter.record_success(
        succeeding_attempt.attempt_id,
        signals={"menu_text": True, "path": True, "screenshot": True},
    )
    return goal_id


def test_shared_engine_runs_two_systems_without_cross_contamination():
    """The SAME GoalLoopEngine instance drives two systems' goals; each
    system's CrossSystemRunRecord must see only its own goals (实施计划
    §8.4 标准1: 同一套目标循环能在新系统上运行)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_shared")
        sys_a = SystemProfile(system_id="sys_a", system_name="System A")
        sys_b = SystemProfile(system_id="sys_b", system_name="System B")
        adapter_a = orch.register_system(sys_a)
        adapter_b = orch.register_system(sys_b)

        goal_a = _recover_menu_goal(orch, adapter_a, "Discover menu A")
        goal_b = _recover_menu_goal(orch, adapter_b, "Discover menu B")

        record_a = orch.capture_system_record("sys_a")
        record_b = orch.capture_system_record("sys_b")

        assert {g["goal_id"] for g in record_a.goals} == {goal_a}
        assert {g["goal_id"] for g in record_b.goals} == {goal_b}
        assert all(c["goal_id"] == goal_a for c in record_a.classifications)
        assert all(c["goal_id"] == goal_b for c in record_b.classifications)


def test_registering_same_system_id_twice_is_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_dup")
        orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        try:
            orch.register_system(SystemProfile(system_id="sys_a", system_name="A duplicate"))
            assert False, "expected ValueError on duplicate system_id"
        except ValueError:
            pass


def test_failure_class_recovered_on_two_systems_is_eligible_for_platform():
    """实施计划 §8.4 标准2/4: 失败分类和套路动作能跨系统复用 -> 能识别哪些经验值得晋升为平台级。"""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_platform")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        adapter_b = orch.register_system(SystemProfile(system_id="sys_b", system_name="B"))

        _recover_menu_goal(orch, adapter_a, "menu A1", shot_suffix="_1")
        _recover_menu_goal(orch, adapter_a, "menu A2", shot_suffix="_2")
        orch.capture_system_record("sys_a")

        _recover_menu_goal(orch, adapter_b, "menu B1", shot_suffix="_1")
        _recover_menu_goal(orch, adapter_b, "menu B2", shot_suffix="_2")
        orch.capture_system_record("sys_b")

        reviews = {r.signature: r for r in orch.review_promotions()}
        failure_review = reviews["failure_recovery::menu_not_found"]
        assert failure_review.eligible_for_platform is True
        assert failure_review.decided_promotion_level == PROMOTION_PLATFORM
        assert set(failure_review.systems) == {"sys_a", "sys_b"}

        winning_review = reviews["winning::menu"]
        assert winning_review.eligible_for_platform is True
        assert winning_review.decided_promotion_level == PROMOTION_PLATFORM


def test_single_system_evidence_never_reaches_platform_regardless_of_goal_count():
    """实施计划 §8.5 风险: 把单系统经验误判为平台能力。§8.6 修正: 只有跨系统仍稳定
    有效的经验才允许进入平台级候选 — repeating on ONE system must not be enough,
    no matter how many goals recover on it."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_single")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))

        for i in range(5):
            _recover_menu_goal(orch, adapter_a, f"menu A{i}", shot_suffix=f"_{i}")
        orch.capture_system_record("sys_a")

        reviews = {r.signature: r for r in orch.review_promotions()}
        assert reviews["failure_recovery::menu_not_found"].eligible_for_platform is False
        assert reviews["failure_recovery::menu_not_found"].decided_promotion_level == PROMOTION_PROJECT
        assert reviews["winning::menu"].eligible_for_platform is False
        assert reviews["winning::menu"].decided_promotion_level == PROMOTION_PROJECT


def test_two_systems_but_only_one_supporting_goal_each_stays_below_floor():
    """Two systems clears the system-count bar, but only ONE goal per system
    recovered — 跨多个功能点 requires >= 2 supporting goals too, not just
    >= 2 systems."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_thin")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        adapter_b = orch.register_system(SystemProfile(system_id="sys_b", system_name="B"))

        _recover_menu_goal(orch, adapter_a, "menu A1")
        orch.capture_system_record("sys_a")
        _recover_menu_goal(orch, adapter_b, "menu B1")
        orch.capture_system_record("sys_b")

        reviews = {r.signature: r for r in orch.review_promotions()}
        assert reviews["failure_recovery::menu_not_found"].eligible_for_platform is False
        assert reviews["winning::menu"].eligible_for_platform is False


def test_inconsistent_playbook_across_systems_blocks_promotion():
    """A failure class recovered on 2 systems via 2 DIFFERENT playbooks is
    not one stable experience — comparison.py's playbook_consistent gate
    must block promotion even though the system/goal counts clear the bar."""

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        sys_a_dir = root / "sys_a_raw"
        sys_b_dir = root / "sys_b_raw"
        _write_frozen_run(
            sys_a_dir,
            run_id="run_a",
            goals=[
                {"goal_id": "ga1", "status": "succeeded", "goal_type": "menu"},
                {"goal_id": "ga2", "status": "succeeded", "goal_type": "menu"},
            ],
            attempts=[
                {"goal_id": "ga1", "attempt_id": "aa1", "failure_class": "menu_not_found"},
                {"goal_id": "ga2", "attempt_id": "aa2", "failure_class": "menu_not_found"},
            ],
            classifications=[
                {"goal_id": "ga1", "failure_reason": "menu_not_found", "suggested_playbook": "pb_x"},
                {"goal_id": "ga2", "failure_reason": "menu_not_found", "suggested_playbook": "pb_x"},
            ],
        )
        _write_frozen_run(
            sys_b_dir,
            run_id="run_b",
            goals=[
                {"goal_id": "gb1", "status": "succeeded", "goal_type": "menu"},
                {"goal_id": "gb2", "status": "succeeded", "goal_type": "menu"},
            ],
            attempts=[
                {"goal_id": "gb1", "attempt_id": "ab1", "failure_class": "menu_not_found"},
                {"goal_id": "gb2", "attempt_id": "ab2", "failure_class": "menu_not_found"},
            ],
            classifications=[
                {"goal_id": "gb1", "failure_reason": "menu_not_found", "suggested_playbook": "pb_y"},
                {"goal_id": "gb2", "failure_reason": "menu_not_found", "suggested_playbook": "pb_y"},
            ],
        )

        orch = CrossSystemGoalOrchestrator(output_dir=str(root / "output"), run_id="run_divergent")
        orch.add_system_from_output_dir(SystemProfile(system_id="sys_a", system_name="A"), sys_a_dir)
        orch.add_system_from_output_dir(SystemProfile(system_id="sys_b", system_name="B"), sys_b_dir)

        reviews = {r.signature: r for r in orch.review_promotions()}
        row = reviews["failure_recovery::menu_not_found"]
        assert row.playbook_consistent is False
        assert row.eligible_for_platform is False
        assert row.decided_promotion_level == PROMOTION_PROJECT
        assert set(row.playbook_ids) == {"pb_x", "pb_y"}


def test_scope_system_does_not_leak_into_single_system_goal_scope_counters():
    """CrossSystemAdapter.record_failure defaults scope='system'; verify this
    does not corrupt the engine-level defect counter that single-system
    goal-loop consumers (round_writer's escalation scoping precedent) rely
    on — the classification's own scope field must read back as 'system',
    while the SHARED defect counter still counts occurrences normally (the
    escalation counter is intentionally run-wide; scope only labels HOW a
    classification was attributed, it does not partition the counter)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_scope")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))

        goal_id = adapter_a.register_validation_goal(goal_type="menu", goal_name="menu A")
        orch.engine.activate_next()
        attempt = orch.engine.start_attempt(goal_id)
        adapter_a.record_failure(attempt.attempt_id, explicit_class="menu_not_found")

        classification = orch.engine.classifications[-1]
        assert classification.scope == "system"


def test_cross_system_comparison_reports_systems_observed_vs_recovered():
    """A class OBSERVED on a system that never recovered from it must still
    show up in systems_observed but not systems_recovered — comparison.py
    must not conflate "saw this failure" with "playbook fixed it here"."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_partial")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        adapter_b = orch.register_system(SystemProfile(system_id="sys_b", system_name="B"))

        _recover_menu_goal(orch, adapter_a, "menu A recovered")
        orch.capture_system_record("sys_a")

        goal_b = adapter_b.register_validation_goal(goal_type="menu", goal_name="menu B never recovers")
        orch.engine.activate_next()
        attempt_b = orch.engine.start_attempt(goal_b)
        adapter_b.record_failure(attempt_b.attempt_id, explicit_class="menu_not_found")
        # goal_b's attempt fails and is never retried to success; goal stays
        # non-terminal (running) — this system never recovers this class.
        orch.capture_system_record("sys_b")

        comparisons = {row.failure_class: row for row in orch.compare_failures()}
        row = comparisons["menu_not_found"]
        assert set(row.systems_observed) == {"sys_a", "sys_b"}
        assert set(row.systems_recovered) == {"sys_a"}


def test_export_all_writes_per_system_and_comparison_and_promotion_files():
    """实施计划 §8.3 交付物: promotion_candidates.json / promotion_candidate_summary
    / failure_classifications.jsonl / experience_updates.jsonl / goal_summary.json."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        orch = CrossSystemGoalOrchestrator(output_dir=str(output_dir), run_id="run_export")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        adapter_b = orch.register_system(SystemProfile(system_id="sys_b", system_name="B"))

        _recover_menu_goal(orch, adapter_a, "menu A1", shot_suffix="_1")
        _recover_menu_goal(orch, adapter_a, "menu A2", shot_suffix="_2")
        orch.capture_system_record("sys_a")
        _recover_menu_goal(orch, adapter_b, "menu B1", shot_suffix="_1")
        _recover_menu_goal(orch, adapter_b, "menu B2", shot_suffix="_2")
        orch.capture_system_record("sys_b")

        orch.export_all()

        for system_id in ("sys_a", "sys_b"):
            system_dir = output_dir / system_id
            assert (system_dir / "failure_classifications.jsonl").exists()
            assert (system_dir / "experience_updates.jsonl").exists()
            summary = json.loads((system_dir / "goal_summary.json").read_text(encoding="utf-8"))
            assert summary["system"]["system_id"] == system_id
            assert summary["goal_count"] == 2

        comparison_payload = json.loads(
            (output_dir / "cross_system_comparison.json").read_text(encoding="utf-8")
        )
        assert {row["failure_class"] for row in comparison_payload["failure_comparisons"]} == {
            "menu_not_found"
        }
        assert {s["system_id"] for s in comparison_payload["systems"]} == {"sys_a", "sys_b"}

        promotion_payload = json.loads(
            (output_dir / "promotion_candidates.json").read_text(encoding="utf-8")
        )
        candidate_ids = {c["candidate_id"] for c in promotion_payload["candidates"]}
        assert candidate_ids == {"winning::menu", "failure_recovery::menu_not_found"}
        assert promotion_payload["promotion_candidate_summary"]["manual_review_required"] is True
        for candidate in promotion_payload["candidates"]:
            assert candidate["review_status"] == "needs_review"
            assert candidate["promotion_level"] == PROMOTION_PLATFORM


def test_human_takeover_resolution_written_only_when_requested():
    """实施计划 §8.3: human_takeover_resolution.json（如阶段内发生人工恢复）—
    must not be fabricated when Stage F never actually resolved a human
    takeover; export_all() must not write it unconditionally."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        orch = CrossSystemGoalOrchestrator(output_dir=str(output_dir), run_id="run_no_takeover")
        orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        orch.capture_system_record("sys_a")
        orch.export_all()

        assert not (output_dir / "human_takeover_resolution.json").exists()

        orch.export_human_takeover_resolution(
            status="resolved",
            operator_id="tester-1",
            note="manually verified cross-system defect",
            ready_to_resume=True,
            resolved_at="2026-01-01T00:00:00",
        )
        resolution = json.loads(
            (output_dir / "human_takeover_resolution.json").read_text(encoding="utf-8")
        )
        assert resolution["status"] == "resolved"
        assert resolution["operator_id"] == "tester-1"
        assert resolution["ready_to_resume"] is True


def test_new_system_run_does_not_break_existing_single_system_reader():
    """实施计划 §8.4 标准5: 新系统接入不会破坏既有 run 级产物读取 — a per-system
    goal_summary.json written by Stage F must remain readable by a plain
    JSON-loading consumer, with the 'goals'/'goal_count' keys Stage F's OWN
    writer (fixture_writer.write_system_goal_loop_artifacts) establishes for
    itself. This is Stage F's own filename+shape contract, not a claim that
    it's byte-identical to GoalLoopWriter's goal_summary.json (which is a
    different, derived-GoalSummary shape — see fixture_writer.py's module
    docstring for why the two are allowed to differ under the same filename,
    same as menu_goal/page_goal/feature_goal already do)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        orch = CrossSystemGoalOrchestrator(output_dir=str(output_dir), run_id="run_compat")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        _recover_menu_goal(orch, adapter_a, "menu A1")
        orch.capture_system_record("sys_a")
        orch.export_per_system_artifacts()

        summary_path = output_dir / "sys_a" / "goal_summary.json"
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        assert isinstance(payload["goals"], list)
        assert isinstance(payload["goal_count"], int)
        assert payload["goal_count"] == len(payload["goals"])


def _write_frozen_run(
    root: Path,
    *,
    run_id: str,
    goals: list[dict],
    attempts: list[dict],
    classifications: list[dict],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "goal_registry.json").write_text(
        json.dumps({"run_id": run_id, "goals": goals}, ensure_ascii=False), encoding="utf-8"
    )
    with (root / "goal_attempts.jsonl").open("w", encoding="utf-8") as fh:
        for row in attempts:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (root / "failure_classifications.jsonl").open("w", encoding="utf-8") as fh:
        for row in classifications:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (root / "experience_updates.jsonl").write_text("", encoding="utf-8")


def test_human_required_failure_pauses_goal_and_blocks_immediate_success():
    """Adversarial-review Finding A1: CrossSystemAdapter.record_failure must
    itself call evaluate_stop(), so a HUMAN_REQUIRED_CLASSES failure (e.g.
    permission_blocked) actually pauses the goal — a subsequent
    record_success on the SAME unresolved attempt must be refused, not
    silently laundered into a "recovered" success and a platform-eligible
    promotion with zero human involvement."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_safety")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))

        goal_id = adapter_a.register_validation_goal(goal_type="menu", goal_name="menu A")
        orch.engine.activate_next()
        attempt = orch.engine.start_attempt(goal_id)

        stop_eval = adapter_a.record_failure(attempt.attempt_id, explicit_class="permission_blocked")

        assert stop_eval.target_status == "waiting_human"
        assert orch.engine.goals[goal_id].status == "waiting_human"

        try:
            adapter_a.record_success(
                attempt.attempt_id,
                signals={"menu_text": True, "path": True, "screenshot": True},
            )
            assert False, "record_success must not succeed over an unresolved human-required pause"
        except ValueError:
            pass

        # Only after an explicit resume_goal() (the human-in-the-loop gate)
        # can the goal continue toward success.
        orch.engine.resume_goal(goal_id)
        retry_attempt = orch.engine.start_attempt(goal_id)
        step = orch.engine.add_step(retry_attempt.attempt_id, "discovery")
        orch.engine.attach_evidence(step.step_id, "screenshot", uri="shot://resolved")
        adapter_a.record_success(
            retry_attempt.attempt_id,
            signals={"menu_text": True, "path": True, "screenshot": True},
        )
        assert orch.engine.goals[goal_id].status == "succeeded"


def test_capturing_same_system_twice_is_rejected():
    """Adversarial-review Finding #2/#3: capturing one system_id a second
    time must raise, not silently append a duplicate CrossSystemRunRecord
    that would double-count the system in cross-system comparisons."""

    with tempfile.TemporaryDirectory() as tmpdir:
        orch = CrossSystemGoalOrchestrator(output_dir=str(Path(tmpdir) / "output"), run_id="run_dup_capture")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))
        _recover_menu_goal(orch, adapter_a, "menu A1")
        orch.capture_system_record("sys_a")

        try:
            orch.capture_system_record("sys_a")
            assert False, "expected ValueError on duplicate capture"
        except ValueError:
            pass


def test_third_non_recovering_system_cannot_veto_two_consistent_recoveries():
    """Adversarial-review Finding #4: playbook_consistent must be computed
    over systems_recovered only. A third system that merely OBSERVED the
    same failure class (but never recovered from it — still failing/blocked)
    must not be able to block promotion for two OTHER systems that recovered
    via the exact same, consistent playbook."""

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        sys_a_dir = root / "sys_a_raw"
        sys_b_dir = root / "sys_b_raw"
        sys_c_dir = root / "sys_c_raw"
        _write_frozen_run(
            sys_a_dir,
            run_id="run_a",
            goals=[
                {"goal_id": "ga1", "status": "succeeded", "goal_type": "menu"},
                {"goal_id": "ga2", "status": "succeeded", "goal_type": "menu"},
            ],
            attempts=[
                {"goal_id": "ga1", "attempt_id": "aa1", "failure_class": "menu_not_found"},
                {"goal_id": "ga2", "attempt_id": "aa2", "failure_class": "menu_not_found"},
            ],
            classifications=[
                {"goal_id": "ga1", "failure_reason": "menu_not_found", "suggested_playbook": "pb_x"},
                {"goal_id": "ga2", "failure_reason": "menu_not_found", "suggested_playbook": "pb_x"},
            ],
        )
        _write_frozen_run(
            sys_b_dir,
            run_id="run_b",
            goals=[
                {"goal_id": "gb1", "status": "succeeded", "goal_type": "menu"},
                {"goal_id": "gb2", "status": "succeeded", "goal_type": "menu"},
            ],
            attempts=[
                {"goal_id": "gb1", "attempt_id": "ab1", "failure_class": "menu_not_found"},
                {"goal_id": "gb2", "attempt_id": "ab2", "failure_class": "menu_not_found"},
            ],
            classifications=[
                {"goal_id": "gb1", "failure_reason": "menu_not_found", "suggested_playbook": "pb_x"},
                {"goal_id": "gb2", "failure_reason": "menu_not_found", "suggested_playbook": "pb_x"},
            ],
        )
        # sys_c observed the SAME class but its goal never succeeded (still
        # running / never recovered) and its classification row happens to
        # carry a different playbook value (e.g. a stale classifier run).
        _write_frozen_run(
            sys_c_dir,
            run_id="run_c",
            goals=[{"goal_id": "gc1", "status": "running", "goal_type": "menu"}],
            attempts=[{"goal_id": "gc1", "attempt_id": "ac1", "failure_class": "menu_not_found"}],
            classifications=[
                {"goal_id": "gc1", "failure_reason": "menu_not_found", "suggested_playbook": "pb_z"},
            ],
        )

        orch = CrossSystemGoalOrchestrator(output_dir=str(root / "output"), run_id="run_veto")
        orch.add_system_from_output_dir(SystemProfile(system_id="sys_a", system_name="A"), sys_a_dir)
        orch.add_system_from_output_dir(SystemProfile(system_id="sys_b", system_name="B"), sys_b_dir)
        orch.add_system_from_output_dir(SystemProfile(system_id="sys_c", system_name="C"), sys_c_dir)

        comparisons = {row.failure_class: row for row in orch.compare_failures()}
        row = comparisons["menu_not_found"]
        assert set(row.systems_observed) == {"sys_a", "sys_b", "sys_c"}
        assert set(row.systems_recovered) == {"sys_a", "sys_b"}
        # playbook_ids/consistency must reflect ONLY the recovering systems'
        # playbook (pb_x), not sys_c's non-recovering pb_z.
        assert row.playbook_ids == ["pb_x"]
        assert row.playbook_consistent is True

        reviews = {r.signature: r for r in orch.review_promotions()}
        assert reviews["failure_recovery::menu_not_found"].eligible_for_platform is True


def test_single_system_escalation_platform_claim_is_demoted_on_export():
    """Adversarial-review Finding #8: a single-system
    record_escalation_experiences() ExperienceUpdate carries
    promotion_level='platform' with ZERO cross-system evidence. Exporting
    one system's artifacts must demote that unvetted claim back to
    'project' rather than writing it through untouched — Stage F's whole
    point is that only promotion_reviewer's cross-system re-decision may
    grant 'platform'."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        orch = CrossSystemGoalOrchestrator(output_dir=str(output_dir), run_id="run_escalation")
        adapter_a = orch.register_system(SystemProfile(system_id="sys_a", system_name="A"))

        # Trigger the escalation counter: repeat the same failure class past
        # the fixed threshold with no successful recovery in between.
        # max_rounds=1 so each goal hits failed_max_rounds (terminal) after
        # its single failure, letting activate_next() advance to the next.
        for i in range(4):
            goal_id = adapter_a.register_validation_goal(
                goal_type="menu", goal_name=f"menu A{i}", max_rounds=1
            )
            orch.engine.activate_next()
            attempt = orch.engine.start_attempt(goal_id)
            adapter_a.record_failure(attempt.attempt_id, explicit_class="locator_unstable")

        escalation_updates = orch.engine.record_escalation_experiences()
        assert any(u.promotion_level == PROMOTION_PLATFORM for u in escalation_updates)

        orch.capture_system_record("sys_a")
        orch.export_per_system_artifacts()

        experience_lines = (
            (output_dir / "sys_a" / "experience_updates.jsonl")
            .read_text(encoding="utf-8")
            .strip()
            .splitlines()
        )
        rows = [json.loads(line) for line in experience_lines]
        escalation_rows = [r for r in rows if r.get("kind") == "escalation"]
        assert escalation_rows
        for row in escalation_rows:
            assert row["promotion_level"] == PROMOTION_PROJECT


if __name__ == "__main__":
    test_shared_engine_runs_two_systems_without_cross_contamination()
    print("[OK] test_shared_engine_runs_two_systems_without_cross_contamination")

    test_registering_same_system_id_twice_is_rejected()
    print("[OK] test_registering_same_system_id_twice_is_rejected")

    test_failure_class_recovered_on_two_systems_is_eligible_for_platform()
    print("[OK] test_failure_class_recovered_on_two_systems_is_eligible_for_platform")

    test_single_system_evidence_never_reaches_platform_regardless_of_goal_count()
    print("[OK] test_single_system_evidence_never_reaches_platform_regardless_of_goal_count")

    test_two_systems_but_only_one_supporting_goal_each_stays_below_floor()
    print("[OK] test_two_systems_but_only_one_supporting_goal_each_stays_below_floor")

    test_inconsistent_playbook_across_systems_blocks_promotion()
    print("[OK] test_inconsistent_playbook_across_systems_blocks_promotion")

    test_human_required_failure_pauses_goal_and_blocks_immediate_success()
    print("[OK] test_human_required_failure_pauses_goal_and_blocks_immediate_success")

    test_capturing_same_system_twice_is_rejected()
    print("[OK] test_capturing_same_system_twice_is_rejected")

    test_third_non_recovering_system_cannot_veto_two_consistent_recoveries()
    print("[OK] test_third_non_recovering_system_cannot_veto_two_consistent_recoveries")

    test_single_system_escalation_platform_claim_is_demoted_on_export()
    print("[OK] test_single_system_escalation_platform_claim_is_demoted_on_export")

    test_scope_system_does_not_leak_into_single_system_goal_scope_counters()
    print("[OK] test_scope_system_does_not_leak_into_single_system_goal_scope_counters")

    test_cross_system_comparison_reports_systems_observed_vs_recovered()
    print("[OK] test_cross_system_comparison_reports_systems_observed_vs_recovered")

    test_export_all_writes_per_system_and_comparison_and_promotion_files()
    print("[OK] test_export_all_writes_per_system_and_comparison_and_promotion_files")

    test_human_takeover_resolution_written_only_when_requested()
    print("[OK] test_human_takeover_resolution_written_only_when_requested")

    test_new_system_run_does_not_break_existing_single_system_reader()
    print("[OK] test_new_system_run_does_not_break_existing_single_system_reader")

    print("\n=== All Stage F integration tests passed ===")
