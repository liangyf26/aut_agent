"""
Integration tests for Stage E: execution, evidence and retrospective loop.

Tests the complete flow from generated_test_cases.json (Stage D output) to
execution_results.json / action_log.jsonl / network_events.json /
screenshots_index.json / round_analysis.json / next_round_plan.json /
run_report.json / run_report.md / human_tasks.json / human_takeover.json.

Tests independently, no browser — the execution runner is fixture-simulated
(实施计划 §2.6: 每个阶段边界都产出一份冻结的 golden / fixture 产物).
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from prototype.stage2.app.execution_goal import ExecutionGoalOrchestrator
from prototype.stage2.app.goal_loop.models import PAUSED_STATUSES, TERMINAL_STATUSES


def _write_test_cases(path: Path, cases: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)


def _sample_cases() -> list[dict]:
    return [
        {
            "test_case_id": "tc_feat_001",
            "feature_id": "page_001_feat_001",
            "page_id": "page_001",
            "type": "executable",
            "risk_level": "low",
            "confidence": "high",
            "steps": [{"step": 1, "action": "click", "target": "button#query"}],
            "expected_result": "查询结果正确显示",
        },
        {
            "test_case_id": "tc_feat_002",
            "feature_id": "page_001_feat_002",
            "page_id": "page_001",
            "type": "view_only",
            "risk_level": "none",
            "confidence": "low",
        },
        {
            "test_case_id": "tc_feat_003",
            "feature_id": "page_001_feat_003",
            "page_id": "page_001",
            "type": "entry_confirmation",
            "risk_level": "high",
            "confidence": "high",
        },
    ]


def test_execution_session_basic_all_pass():
    """Full happy path: three cases, all conclude successfully, every
    Stage E artifact is written and internally consistent."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run")
        root_id = orch.create_root_goal()
        assert root_id not in orch.engine.frontier  # root must not stall activate_next()

        goal_ids = orch.load_test_cases(test_cases_path)
        assert len(goal_ids) == 3

        outcomes = orch.execute_all()
        assert len(outcomes) == 3
        assert all(outcome.status == "passed" for outcome in outcomes)
        assert orch.halted_early is False
        assert orch.engine.frontier == []

        # every execution goal must have actually succeeded via the real
        # feature-goal success predicate, not just "we didn't fail it"
        for goal_id in goal_ids:
            assert orch.engine.goals[goal_id].status == "succeeded"

        # each outcome's goal_id must match the goal actually driven for it
        # (execute_all processes goals in frontier/registration order, same
        # order load_test_cases returned goal_ids in).
        assert [outcome.goal_id for outcome in outcomes] == goal_ids

        results_path = orch.export_execution_results()
        results = json.loads(results_path.read_text(encoding="utf-8"))
        assert results["count"] == 3
        assert results["results"] == results["items"]  # v3_orchestrator schema-compat alias
        assert {item["test_case_id"] for item in results["items"]} == {
            "tc_feat_001",
            "tc_feat_002",
            "tc_feat_003",
        }
        # each item's goal_id must match the goal actually registered for
        # its test_case_id — catches a goal_id/outcome mix-up bug that a
        # mere set-of-test_case_ids comparison would miss.
        by_test_case_id = {item["test_case_id"]: item for item in results["items"]}
        for goal_id, test_case in zip(goal_ids, _sample_cases()):
            assert by_test_case_id[test_case["test_case_id"]]["goal_id"] == goal_id
        # entry_confirmation never attempts the real high-risk action itself
        entry_item = next(item for item in results["items"] if item["test_case_id"] == "tc_feat_003")
        assert entry_item["requires_human_authorization"] is True
        assert entry_item["status"] == "passed"

        action_log_path = orch.export_action_log()
        log_lines = action_log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(log_lines) == 3
        for line in log_lines:
            entry = json.loads(line)
            # every action log line must carry the full goal->attempt->step->evidence chain
            assert entry["goal_id"] and entry["attempt_id"] and entry["step_id"] and entry["evidence_id"]

        network_path = orch.export_network_events()
        network_payload = json.loads(network_path.read_text(encoding="utf-8"))
        assert network_payload["capture_status"] == "not_applicable_fixture_mode"
        assert network_payload["count"] == 0

        screenshots_path = orch.export_screenshots_index()
        screenshots_payload = json.loads(screenshots_path.read_text(encoding="utf-8"))
        assert screenshots_payload["count"] == 0
        assert "notes" in screenshots_payload  # honest about no real screenshots

        human_tasks_path = orch.export_human_tasks()
        human_tasks_payload = json.loads(human_tasks_path.read_text(encoding="utf-8"))
        # tc_feat_003 (entry_confirmation) passed its basic path but still
        # requires a SEPARATE human authorization before the real high-risk
        # action can be attempted — requires_human_authorization=True must
        # actually surface as an open task, not be inert metadata.
        assert human_tasks_payload["open_task_count"] == 1
        auth_task = human_tasks_payload["tasks"][0]
        assert auth_task["type"] == "high_risk_action_authorization"
        assert auth_task["test_case_id"] == "tc_feat_003"

        takeover_path = orch.export_human_takeover()
        assert takeover_path is not None  # a pending authorization is a real open item
        takeover_payload = json.loads(takeover_path.read_text(encoding="utf-8"))
        assert takeover_payload["pending_actions"][0]["action_kind"] == "authorize_high_risk_action"

        round_analysis_path = orch.export_round_analysis()
        round_analysis = json.loads(round_analysis_path.read_text(encoding="utf-8"))
        assert round_analysis["coverage"]["succeeded"] == 3
        assert round_analysis["coverage"]["failed"] == 0
        assert round_analysis["coverage"]["paused"] == 0
        assert round_analysis["failure_clusters"] == []

        next_round_path = orch.export_next_round_plan()
        next_round = json.loads(next_round_path.read_text(encoding="utf-8"))
        assert next_round["status"] == "no_retry_needed"
        assert next_round["should_start_next_round"] is False

        json_path, md_path = orch.export_run_report()
        report = json.loads(json_path.read_text(encoding="utf-8"))
        assert report["summary"]["status"] == "completed"
        assert len(report["success_items"]) == 3
        assert report["failure_items"] == []
        assert "run_report.md" in str(md_path)
        md_text = md_path.read_text(encoding="utf-8")
        assert "tc_feat_001" in md_text

        summary = orch.get_summary()
        assert summary["executed_case_count"] == 3
        assert summary["pending_human_authorization_count"] == 1
        assert summary["halted_early"] is False

        run_summary_path = orch.export_run_summary(extra={"rounds_run": 1})
        assert run_summary_path.exists()
        assert run_summary_path == orch.output_dir / "run_summary.json"
        persisted_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
        for key, value in summary.items():
            assert persisted_summary[key] == value
        assert persisted_summary["rounds_run"] == 1
        assert "generated_at" in persisted_summary


def test_human_required_failure_pauses_batch_and_writes_takeover_packet():
    """A permission_blocked failure must pause its goal (not fail it outright)
    and halt the batch, leaving remaining cases queued in the frontier —
    never silently skipped or force-run out of order (方案 §4.11)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_paused")
        orch.create_root_goal()
        goal_ids = orch.load_test_cases(test_cases_path)

        outcomes = orch.execute_all(injected_failures={"tc_feat_001": "permission_blocked"})

        assert len(outcomes) == 1  # batch stopped after the first case
        assert outcomes[0].status == "failed"
        assert orch.halted_early is True
        assert len(orch.engine.frontier) == 2  # remaining two cases still queued, not dropped

        # verify WHICH two cases remain (not just a count) — a loader bug
        # that drops/duplicates a case would still leave len(frontier)==2.
        remaining_test_case_ids = {
            orch.adapter.get_execution_context(gid)["test_case_id"] for gid in orch.engine.frontier
        }
        assert remaining_test_case_ids == {"tc_feat_002", "tc_feat_003"}
        # and neither remaining goal was touched by an attempt.
        assert not any(a.goal_id in orch.engine.frontier for a in orch.engine.attempts)

        first_goal = orch.engine.goals[goal_ids[0]]
        assert first_goal.status in PAUSED_STATUSES  # paused, NOT a terminal failure

        human_tasks_path = orch.export_human_tasks()
        human_tasks = json.loads(human_tasks_path.read_text(encoding="utf-8"))
        assert human_tasks["open_task_count"] == 1
        assert human_tasks["tasks"][0]["failure_class"] == "permission_blocked"
        # permission_blocked has its own task type, distinct from the
        # generic label previously collapsed across all 5 human-required
        # classes — see human_tasks_writer._TASK_TYPE_BY_FAILURE_CLASS.
        assert human_tasks["tasks"][0]["type"] == "permission_grant"

        takeover_path = orch.export_human_takeover()
        assert takeover_path is not None
        takeover = json.loads(takeover_path.read_text(encoding="utf-8"))
        assert takeover["status"] == "waiting_human"
        assert len(takeover["pending_actions"]) == 1
        assert takeover["pending_actions"][0]["test_case_id"] == "tc_feat_001"
        # waiting_reason must carry the ACTUAL failure class (permission_blocked),
        # not the generic stop-condition name "waiting_human" — a safety-policy
        # block must be distinguishable from a routine login pause.
        assert takeover["waiting_reason"] == "permission_blocked"
        # resume_command must reference a command that actually exists in
        # this codebase and actually works against a goal_loop run_dir.
        # --resume-human-takeover looks like the obvious choice but is a
        # trap: it unconditionally dispatches to
        # tools/suyuan_submit_loop.py's resume_profile_from_human_takeover,
        # which requires a round_input.json (with model_name) that goal_loop
        # runs never write — it would raise immediately. Only
        # --resolve-goal-loop-takeover actually understands this run_dir's
        # human_takeover.json shape (found + fixed 2026-07-04).
        assert takeover["resume_command"] == (
            f'python -m prototype.stage2.main --resolve-goal-loop-takeover "{output_dir}"'
        )

        next_round_path = orch.export_next_round_plan()
        next_round = json.loads(next_round_path.read_text(encoding="utf-8"))
        assert next_round["status"] == "needs_review"
        # should_start_next_round=None is dropped by _compact_dict (same
        # convention as iteration.models) rather than serialized as null.
        assert next_round.get("should_start_next_round") is None
        assert "permission_blocked" in next_round["primary_reason"]

        json_path, _ = orch.export_run_report()
        report = json.loads(json_path.read_text(encoding="utf-8"))
        assert report["summary"]["status"] == "needs_review"


def test_login_required_failure_produces_login_handoff_task():
    """login_required must map to a distinct human-task type from a generic
    high-risk authorization block, so a human knows which action to take."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_login")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)

        orch.execute_all(injected_failures={"tc_feat_001": "login_required"})

        human_tasks = json.loads(orch.export_human_tasks().read_text(encoding="utf-8"))
        assert human_tasks["tasks"][0]["type"] == "login_handoff"


def test_retryable_failure_does_not_pause_batch():
    """A failure class outside HUMAN_REQUIRED_CLASSES (e.g. locator_unstable)
    hits failed_max_rounds (terminal, not paused) since max_rounds=1 for
    execution goals; the batch must continue to the remaining cases."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_retry")
        orch.create_root_goal()
        goal_ids = orch.load_test_cases(test_cases_path)

        outcomes = orch.execute_all(injected_failures={"tc_feat_001": "locator_unstable"})

        assert len(outcomes) == 3  # batch did NOT stop
        assert orch.halted_early is False
        assert outcomes[0].status == "failed"
        assert outcomes[1].status == "passed"
        assert outcomes[2].status == "passed"

        first_goal = orch.engine.goals[goal_ids[0]]
        assert first_goal.status in TERMINAL_STATUSES
        assert first_goal.status not in PAUSED_STATUSES

        round_analysis = json.loads(orch.export_round_analysis().read_text(encoding="utf-8"))
        assert round_analysis["coverage"]["failed"] == 1
        assert round_analysis["coverage"]["succeeded"] == 2
        assert round_analysis["failure_clusters"]
        assert round_analysis["failure_clusters"][0]["cluster_id"] == "goalloop::locator_unstable"

        next_round = json.loads(orch.export_next_round_plan().read_text(encoding="utf-8"))
        assert next_round["status"] == "scheduled"
        assert next_round["should_start_next_round"] is True
        assert goal_ids[0] in next_round["target_ids"]


def test_evidence_chain_traces_goal_to_evidence():
    """Every evidence entry recorded during execution must be traceable back
    goal -> attempt -> step -> evidence, per 方案 §5.7's four-level chain."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, [_sample_cases()[0]])

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_evidence")
        orch.create_root_goal()
        goal_ids = orch.load_test_cases(test_cases_path)
        orch.execute_all()

        goal_id = goal_ids[0]
        attempts = [a for a in orch.engine.attempts if a.goal_id == goal_id]
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.steps  # at least the action step + network_capture step
        # the action step specifically must be present — the primary
        # evidence of what was actually done, not just any step kind.
        assert any(step.kind == "action" for step in attempt.steps)
        for step in attempt.steps:
            assert step.attempt_id == attempt.attempt_id
            for evidence_id in step.evidence_ids:
                evidence = orch.engine.evidence[evidence_id]
                assert evidence.owner_step_id == step.step_id

        # check_evidence_complete must report no gaps: every observed step in
        # this run carried evidence attached in the same call.
        gaps = orch.engine.check_evidence_complete(attempt.attempt_id)
        assert gaps == []


def test_action_log_test_case_id_matches_execution_results():
    """action_log.jsonl's test_case_id must be the REAL test_case_id, not the
    feature_id — the two are different strings in a real Stage D run
    (test_case_id = f'tc_{feature_id}'), so parsing it out of goal.origin
    (which only ever encodes feature_id) silently breaks any join between
    action_log.jsonl and execution_results.json."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(
            test_cases_path,
            [
                {
                    "test_case_id": "tc_page_001_feat_001",
                    "feature_id": "page_001_feat_001",
                    "page_id": "page_001",
                    "type": "executable",
                    "risk_level": "low",
                    "confidence": "high",
                    "steps": [{"step": 1, "action": "click"}],
                }
            ],
        )

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_actionlog")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)
        orch.execute_all()

        results = json.loads(orch.export_execution_results().read_text(encoding="utf-8"))
        action_log_lines = orch.export_action_log().read_text(encoding="utf-8").strip().splitlines()

        assert results["items"][0]["test_case_id"] == "tc_page_001_feat_001"
        action_entry = json.loads(action_log_lines[0])
        assert action_entry["test_case_id"] == "tc_page_001_feat_001"
        # explicitly NOT the feature_id (the bug this test guards against)
        assert action_entry["test_case_id"] != "page_001_feat_001"


def test_executable_case_with_high_risk_level_is_refused_not_executed():
    """Defense-in-depth: an 'executable' case that (via a stale fixture or a
    future classifier bug) declares risk_level='high' must be refused, not
    executed as if it were a genuine low-risk case — Stage E must not trust
    generated_test_cases.json blindly for the type<->risk_level invariant
    Stage D's classifier is supposed to enforce."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(
            test_cases_path,
            [
                {
                    "test_case_id": "tc_evil",
                    "feature_id": "feat_evil",
                    "page_id": "page_x",
                    "type": "executable",
                    "risk_level": "high",
                    "confidence": "high",
                    "steps": [{"step": 1, "action": "submit_delete_all"}],
                }
            ],
        )

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_defense")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)
        outcomes = orch.execute_all()

        assert len(outcomes) == 1
        assert outcomes[0].status == "failed"
        assert outcomes[0].failure_reason == "blocked_by_safety_policy"
        # the dangerous action must never be reported as "completed"
        assert not any(action.get("status") == "completed" for action in outcomes[0].actions)


def test_escalation_counter_does_not_leak_across_goal_origins():
    """round_analysis.json's escalations must be scoped to THIS run's
    execution goals — failures recorded against a differently-originated
    goal on a shared engine must not inflate Stage E's own escalation
    counters."""

    from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        shared_engine = GoalLoopEngine(run_id="shared")
        foreign_goal = shared_engine.register_goal(goal_type="menu", goal_name="unrelated menu goal")
        shared_engine.activate_next()
        for _ in range(3):
            attempt = shared_engine.start_attempt(foreign_goal.goal_id)
            shared_engine.record_failure(attempt.attempt_id, explicit_class="locator_unstable")
        # resolve the foreign goal to a terminal status so it releases
        # active_goal_id — otherwise activate_next() (used by Stage E below)
        # would correctly refuse to advance past it, which is a different
        # invariant than the one this test is about.
        shared_engine.evaluate_stop(foreign_goal.goal_id)
        assert foreign_goal.status in TERMINAL_STATUSES

        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(
            test_cases_path,
            [_sample_cases()[0]],
        )

        orch = ExecutionGoalOrchestrator(engine=shared_engine, output_dir=str(output_dir), run_id="shared")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)
        orch.execute_all(injected_failures={"tc_feat_001": "locator_unstable"})

        round_analysis = json.loads(orch.export_round_analysis().read_text(encoding="utf-8"))
        assert round_analysis["coverage"]["failed"] == 1
        # the escalation view must reflect only Stage E's 1 occurrence, not
        # the foreign goal's 3 + Stage E's 1 = 4.
        escalation = next(
            (row for row in round_analysis["escalations"] if row["failure_class"] == "locator_unstable"),
            None,
        )
        assert escalation is None or escalation["occurrences"] == 1


def test_execute_all_raises_on_foreign_goal_and_preserves_prior_outcomes():
    """If engine.frontier ever contains a goal this orchestrator did not
    register (e.g. a shared engine with another goal producer), execute_all
    must raise loudly rather than silently stranding the foreign goal at
    STATUS_RUNNING — and outcomes already recorded earlier in the SAME call
    must not be discarded when the error is raised."""

    from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        shared_engine = GoalLoopEngine(run_id="shared_foreign")
        orch = ExecutionGoalOrchestrator(engine=shared_engine, output_dir=str(output_dir), run_id="shared_foreign")
        root_id = orch.create_root_goal()

        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, [_sample_cases()[0]])
        goal_ids = orch.load_test_cases(test_cases_path)

        # insert a foreign goal into the frontier AFTER the one real execution goal
        foreign_goal = shared_engine.register_goal(goal_type="menu", goal_name="foreign", parent_goal_id=root_id)
        assert shared_engine.frontier == [goal_ids[0], foreign_goal.goal_id]

        with pytest.raises(RuntimeError):
            orch.execute_all()

        # the real goal (executed before the foreign one was reached) must
        # have its outcome preserved despite the later crash.
        assert len(orch._outcomes) == 1
        assert orch.engine.goals[goal_ids[0]].status == "succeeded"
        results = json.loads(orch.export_execution_results().read_text(encoding="utf-8"))
        assert results["count"] == 1


def test_unrecognized_case_type_fails_with_evidence_incomplete():
    """A test case with an unknown/missing type cannot execute a basic path;
    it must fail with the fixed evidence_incomplete class rather than being
    silently treated as a pass."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(
            test_cases_path,
            [{"test_case_id": "tc_unknown", "feature_id": "feat_x", "page_id": "page_x", "type": "mystery"}],
        )

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_unknown")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)
        outcomes = orch.execute_all()

        assert len(outcomes) == 1
        assert outcomes[0].status == "failed"
        assert outcomes[0].failure_reason == "evidence_incomplete"


def test_run_dispatches_fixture_mode_by_default():
    """run() with the default mode delegates to execute_all() unchanged —
    same outcomes, same artifacts, no real_browser_runner import triggered."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_dispatch")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)

        outcomes = asyncio.run(orch.run(mode="fixture_simulated"))

        assert len(outcomes) == 3
        assert all(outcome.status == "passed" for outcome in outcomes)
        assert all(outcome.execution_mode == "fixture_simulated" for outcome in outcomes)


def test_run_real_browser_mode_requires_page_and_screenshots_dir():
    """run(mode='real_browser') must refuse to proceed without a page/dir
    rather than crash deep inside the runner closure with a confusing error."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_missing_page")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)

        with pytest.raises(ValueError, match="requires both page and screenshots_dir"):
            asyncio.run(orch.run(mode="real_browser"))


def test_run_rejects_unrecognized_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        test_cases_path = output_dir / "generated_test_cases.json"
        _write_test_cases(test_cases_path, _sample_cases())

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_bad_mode")
        orch.create_root_goal()
        orch.load_test_cases(test_cases_path)

        with pytest.raises(ValueError, match="unrecognized execution mode"):
            asyncio.run(orch.run(mode="not_a_real_mode"))


def test_run_until_stable_auto_retries_locator_unstable_and_converges():
    """A LOCATOR_UNSTABLE (playbook exit=retry) failure on round 1 must be
    auto-retried in round 2 within the SAME process/engine, and — since
    injected_failures only applies to round 1 — round 2 must pass, causing
    the loop to converge and stop (not burn through max_rounds)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_until_stable_retry")
        orch.create_root_goal()

        test_cases = [
            {
                "test_case_id": "tc_unstable",
                "feature_id": "feat_unstable",
                "page_id": "page_unstable",
                "type": "view_only",
                "risk_level": "none",
            }
        ]

        rounds = asyncio.run(
            orch.run_until_stable(
                test_cases,
                max_rounds=3,
                injected_failures={"tc_unstable": "locator_unstable"},
            )
        )

        # converged at round 2, never reached round 3
        assert [r["round_index"] for r in rounds] == [1, 2]
        assert rounds[0]["retryable_count"] == 1
        assert rounds[1]["retryable_count"] == 0
        assert rounds[1]["stopped_reason"] == "converged: no retryable failures remain."
        assert "stopped_reason" not in rounds[0]

        assert len(orch._outcomes) == 2
        assert orch._outcomes[0].status == "failed"
        assert orch._outcomes[1].status == "passed"

        origins = sorted(
            g.origin for g in orch.engine.goals.values() if g.origin.startswith("feature_execution::")
        )
        assert origins == ["feature_execution::feat_unstable", "feature_execution::feat_unstable#round2"]


def test_run_until_stable_does_not_auto_retry_human_required_failure():
    """A permission_blocked (playbook exit=human) failure must NEVER be
    auto-retried, even with max_rounds>1 — it pauses the goal instead, and
    run_until_stable must hard-stop there (an unresolved active goal makes
    any further activate_next() raise), surfacing the block via
    blocked_reasons/stopped_reason rather than silently looping."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_until_stable_human")
        orch.create_root_goal()

        test_cases = [
            {
                "test_case_id": "tc_blocked",
                "feature_id": "feat_blocked",
                "page_id": "page_blocked",
                "type": "view_only",
                "risk_level": "none",
            }
        ]

        rounds = asyncio.run(
            orch.run_until_stable(
                test_cases,
                max_rounds=3,
                injected_failures={"tc_blocked": "permission_blocked"},
            )
        )

        assert [r["round_index"] for r in rounds] == [1]
        assert rounds[0]["retryable_count"] == 0
        assert len(rounds[0]["blocked_reasons"]) == 1
        assert "paused" in rounds[0]["blocked_reasons"][0]
        assert "paused_on_human_takeover" in rounds[0]["stopped_reason"]
        assert orch.halted_early is True

        # only round 1's goal was ever registered — no #round2 retry attempt
        origins = [g.origin for g in orch.engine.goals.values() if g.origin.startswith("feature_execution::")]
        assert origins == ["feature_execution::feat_blocked"]


def test_run_until_stable_real_browser_mode_always_stops_after_one_round():
    """Even with max_rounds>1 and a retryable failure present, real_browser
    mode must stop after exactly one round — auto-retrying a live production
    action unattended is the explicit safety boundary this feature must not
    cross (confirmed with the user before implementation)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_until_stable_real_browser")
        orch.create_root_goal()

        test_cases = [
            {
                "test_case_id": "tc_real",
                "feature_id": "feat_real",
                "page_id": "page_real",
                "type": "view_only",
                "risk_level": "none",
            }
        ]

        class _FakePage:
            pass

        async def _fake_runner(test_case, *, goal_id, injected_failure, **_kwargs):
            from prototype.stage2.app.execution_goal.execution_runner import simulate_test_case_execution

            return simulate_test_case_execution(test_case, goal_id=goal_id, injected_failure=injected_failure)

        import prototype.stage2.app.execution_goal.orchestrator as orchestrator_module

        original_run = orchestrator_module.ExecutionGoalOrchestrator.run

        async def _patched_run(self, *, mode, page=None, screenshots_dir=None, injected_failures=None, safety_policy="low_risk_only"):
            if mode != "real_browser":
                return await original_run(
                    self, mode=mode, page=page, screenshots_dir=screenshots_dir, injected_failures=injected_failures, safety_policy=safety_policy
                )
            return await self.execute_all_async(runner=_fake_runner, injected_failures=injected_failures)

        orchestrator_module.ExecutionGoalOrchestrator.run = _patched_run
        try:
            rounds = asyncio.run(
                orch.run_until_stable(
                    test_cases,
                    mode="real_browser",
                    max_rounds=5,
                    page=_FakePage(),
                    screenshots_dir=output_dir / "screenshots",
                    injected_failures={"tc_real": "locator_unstable"},
                )
            )
        finally:
            orchestrator_module.ExecutionGoalOrchestrator.run = original_run

        assert [r["round_index"] for r in rounds] == [1]
        assert rounds[0]["retryable_count"] == 1
        assert "real_browser_round_limit" in rounds[0]["stopped_reason"]


def test_resolve_retryable_test_cases_only_returns_retry_exit_goals():
    """Direct unit test of round_writer.resolve_retryable_test_cases: given
    goals with different failure classes, only the one whose playbook exit
    is 'retry' (locator_unstable) should come back retryable; the
    exit='human' one (permission_blocked) must land in blocked_reasons
    instead, with its original test_case content untouched."""

    from prototype.stage2.app.execution_goal.round_writer import resolve_retryable_test_cases

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_resolve_retryable")
        orch.create_root_goal()

        test_cases = [
            {
                "test_case_id": "tc_retry_me",
                "feature_id": "feat_retry_me",
                "page_id": "page_x",
                "type": "view_only",
                "risk_level": "none",
            },
            {
                "test_case_id": "tc_human_me",
                "feature_id": "feat_human_me",
                "page_id": "page_x",
                "type": "view_only",
                "risk_level": "none",
            },
        ]
        orch.load_test_cases_from_list(test_cases)
        orch.execute_all(
            injected_failures={"tc_retry_me": "locator_unstable", "tc_human_me": "permission_blocked"}
        )

        decision = resolve_retryable_test_cases(orch.engine, orch.adapter)

        assert len(decision["retryable"]) == 1
        assert decision["retryable"][0]["test_case"]["test_case_id"] == "tc_retry_me"
        assert decision["retryable"][0]["failure_class"] == "locator_unstable"

        assert len(decision["blocked_reasons"]) == 1
        assert "tc_human_me" in decision["blocked_reasons"][0]


if __name__ == "__main__":
    test_execution_session_basic_all_pass()
    print("[OK] test_execution_session_basic_all_pass")

    test_human_required_failure_pauses_batch_and_writes_takeover_packet()
    print("[OK] test_human_required_failure_pauses_batch_and_writes_takeover_packet")

    test_login_required_failure_produces_login_handoff_task()
    print("[OK] test_login_required_failure_produces_login_handoff_task")

    test_retryable_failure_does_not_pause_batch()
    print("[OK] test_retryable_failure_does_not_pause_batch")

    test_evidence_chain_traces_goal_to_evidence()
    print("[OK] test_evidence_chain_traces_goal_to_evidence")

    test_action_log_test_case_id_matches_execution_results()
    print("[OK] test_action_log_test_case_id_matches_execution_results")

    test_executable_case_with_high_risk_level_is_refused_not_executed()
    print("[OK] test_executable_case_with_high_risk_level_is_refused_not_executed")

    test_escalation_counter_does_not_leak_across_goal_origins()
    print("[OK] test_escalation_counter_does_not_leak_across_goal_origins")

    test_execute_all_raises_on_foreign_goal_and_preserves_prior_outcomes()
    print("[OK] test_execute_all_raises_on_foreign_goal_and_preserves_prior_outcomes")

    test_unrecognized_case_type_fails_with_evidence_incomplete()
    print("[OK] test_unrecognized_case_type_fails_with_evidence_incomplete")

    print("\n=== All Stage E integration tests passed ===")
