"""Writer produces the goal-loop artifacts; compat proves reuse of iteration."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop import compat  # noqa: E402
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine  # noqa: E402
from prototype.stage2.app.goal_loop.writer import GoalLoopWriter  # noqa: E402
from prototype.stage2.app.iteration.models import (  # noqa: E402
    FailureClusterRecord,
    PromotionCandidateRecord,
    RetryAction,
    RetryPlanRecord,
)


def _run_a_goal() -> GoalLoopEngine:
    engine = GoalLoopEngine("run-writer")
    engine.register_goal("menu", "菜单发现")
    engine.activate_next()
    a1 = engine.start_attempt()
    step = engine.add_step(a1.attempt_id, "action", action="expand")
    engine.attach_evidence(step.step_id, "screenshot", uri="s.png")
    engine.record_failure(a1.attempt_id, explicit_class="menu_expand_failed")
    a2 = engine.start_attempt()
    engine.record_success(
        a2.attempt_id, signals={"menu_text": "x", "path": "p", "screenshot": "s.png"}
    )
    return engine


def test_writer_emits_all_goal_loop_artifacts() -> None:
    engine = _run_a_goal()
    with tempfile.TemporaryDirectory() as tmp:
        writer = GoalLoopWriter(tmp)
        paths = writer.write_all(engine)
        for key in (
            "goal_registry",
            "goal_state",
            "goal_attempts",
            "failure_classifications",
            "playbook_actions",
            "experience_updates",
            "goal_summary",
            "goal_current_status",
        ):
            assert paths[key].exists(), f"missing artifact {key}"

        registry = json.loads(paths["goal_registry"].read_text(encoding="utf-8"))
        assert registry["run_id"] == "run-writer"
        assert registry["goals"][0]["goal_type"] == "menu"

        summary = json.loads(paths["goal_summary"].read_text(encoding="utf-8"))
        assert summary["summaries"][0]["succeeded"] is True
        assert "reuse_mapping" in summary  # documents projection onto iteration

        # jsonl: one classification, one playbook action recorded
        classifications = paths["failure_classifications"].read_text(encoding="utf-8").strip().splitlines()
        actions = paths["playbook_actions"].read_text(encoding="utf-8").strip().splitlines()
        assert len(classifications) == 1
        assert len(actions) == 1


def test_writer_is_idempotent() -> None:
    engine = _run_a_goal()
    with tempfile.TemporaryDirectory() as tmp:
        writer = GoalLoopWriter(tmp)
        writer.write_all(engine)
        writer.write_all(engine)  # second call must not append/duplicate
        actions = writer.playbook_actions_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(actions) == 1


def test_compat_projects_onto_iteration_structures() -> None:
    engine = _run_a_goal()

    cluster = compat.failure_classification_to_cluster(engine.classifications[0], stage="menu")
    assert isinstance(cluster, FailureClusterRecord)
    assert cluster.cluster_id == "goalloop::menu_expand_failed"
    assert cluster.category == "ui"

    retry = compat.playbook_action_to_retry_action(engine.playbook_action_records[0])
    assert isinstance(retry, RetryAction)
    assert retry.cluster_id == "goalloop::menu_expand_failed"
    assert retry.execution_hints["exit"] == "retry"

    plan = compat.build_retry_plan("run-writer", engine.playbook_action_records)
    assert isinstance(plan, RetryPlanRecord)
    assert plan.status == "scheduled"

    candidate = compat.experience_update_to_promotion_candidate(engine.experience_updates[0])
    assert isinstance(candidate, PromotionCandidateRecord)
    assert candidate.source.startswith("goal_loop:")


def test_mapping_table_documents_reuse() -> None:
    # the in-code mapping must name the existing iteration structures reused
    assert compat.MAPPING["failure_classifications"] == "iteration.FailureClusterRecord"
    assert compat.MAPPING["playbook_actions"] == "iteration.RetryAction/RetryPlanRecord"
    assert compat.MAPPING["experience_updates"] == "iteration.PromotionCandidateRecord"
