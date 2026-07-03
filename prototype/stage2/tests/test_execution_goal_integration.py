"""
Integration tests for Stage E: execution, evidence and retrospective loop.

Tests the complete flow from generated_test_cases.json (Stage D output) to
execution_results.json / action_log.jsonl / network_events.json /
screenshots_index.json / round_analysis.json / next_round_plan.json /
run_report.json / run_report.md / human_tasks.json / human_takeover.json.

Tests independently, no browser — the execution runner is fixture-simulated
(实施计划 §2.6: 每个阶段边界都产出一份冻结的 golden / fixture 产物).
"""

import json
import tempfile
from pathlib import Path

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

        results_path = orch.export_execution_results()
        results = json.loads(results_path.read_text(encoding="utf-8"))
        assert results["count"] == 3
        assert {item["test_case_id"] for item in results["items"]} == {
            "tc_feat_001",
            "tc_feat_002",
            "tc_feat_003",
        }
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
        assert human_tasks_payload["open_task_count"] == 0

        assert orch.export_human_takeover() is None  # nothing paused -> no packet

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

        first_goal = orch.engine.goals[goal_ids[0]]
        assert first_goal.status in PAUSED_STATUSES  # paused, NOT a terminal failure

        human_tasks_path = orch.export_human_tasks()
        human_tasks = json.loads(human_tasks_path.read_text(encoding="utf-8"))
        assert human_tasks["open_task_count"] == 1
        assert human_tasks["tasks"][0]["failure_class"] == "permission_blocked"
        assert human_tasks["tasks"][0]["type"] == "high_risk_authorization"

        takeover_path = orch.export_human_takeover()
        assert takeover_path is not None
        takeover = json.loads(takeover_path.read_text(encoding="utf-8"))
        assert takeover["status"] == "waiting_human"
        assert len(takeover["pending_actions"]) == 1
        assert takeover["pending_actions"][0]["test_case_id"] == "tc_feat_001"
        assert "resume_command" in takeover and takeover["resume_command"]

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
        for step in attempt.steps:
            assert step.attempt_id == attempt.attempt_id
            for evidence_id in step.evidence_ids:
                evidence = orch.engine.evidence[evidence_id]
                assert evidence.owner_step_id == step.step_id

        # check_evidence_complete must report no gaps: every observed step in
        # this run carried evidence attached in the same call.
        gaps = orch.engine.check_evidence_complete(attempt.attempt_id)
        assert gaps == []


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

    test_unrecognized_case_type_fails_with_evidence_incomplete()
    print("[OK] test_unrecognized_case_type_fails_with_evidence_incomplete")

    print("\n=== All Stage E integration tests passed ===")
