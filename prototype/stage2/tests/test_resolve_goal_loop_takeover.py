"""
Integration tests for the goal_loop-family human-takeover resolution flow
(``--resolve-goal-loop-takeover``).

Guards two things found broken during 2026-07-04 verification:
1. execution_goal's human_takeover.json's resume_command pointed at
   --resume-human-takeover, which crashes against a goal_loop run_dir (it
   requires round_input.json/model_name, which goal_loop never writes) —
   see test_execution_goal_integration.py's assertion on the corrected
   template.
2. There was no working entrypoint at all for a human to record having
   reviewed a goal_loop takeover request. This module exercises the one
   that was added: resolve_goal_loop_takeover_entrypoint.
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

from prototype.stage2.app.execution_goal import ExecutionGoalOrchestrator  # noqa: E402
from prototype.stage2.main import resolve_goal_loop_takeover_entrypoint  # noqa: E402


def _write_test_cases(path: Path, cases: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)


def _paused_run_dir(tmpdir: str) -> Path:
    """Build a real execution_goal run that pauses on a human-required
    failure, matching test_execution_goal_integration.py's own setup, and
    export its real human_takeover.json."""

    output_dir = Path(tmpdir) / "output"
    output_dir.mkdir()
    test_cases_path = output_dir / "generated_test_cases.json"
    _write_test_cases(
        test_cases_path,
        [{"test_case_id": "tc_x", "feature_id": "feat_x", "page_id": "page_x", "type": "view_only", "risk_level": "none"}],
    )

    orch = ExecutionGoalOrchestrator(output_dir=str(output_dir), run_id="test_run_resolve")
    orch.create_root_goal()
    orch.load_test_cases(test_cases_path)
    orch.execute_all(injected_failures={"tc_x": "permission_blocked"})
    orch.export_human_takeover()

    return output_dir


def test_resolve_writes_resolution_for_a_real_paused_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = _paused_run_dir(tmpdir)
        assert (output_dir / "human_takeover.json").exists()

        result = resolve_goal_loop_takeover_entrypoint(
            str(output_dir),
            operator_id="alice",
            note="确认权限已开通",
            ready_to_resume=True,
        )

        resolution_path = Path(result["human_takeover_resolution_path"])
        assert resolution_path.exists()
        resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
        assert resolution["status"] == "resolved"
        assert resolution["operator_id"] == "alice"
        assert resolution["note"] == "确认权限已开通"
        assert resolution["ready_to_resume"] is True
        assert resolution["resolved_at"]

        assert result["waiting_reason"] == "permission_blocked"
        assert result["pending_action_count"] == 1
        assert "重新执行" in result["next_step"]


def test_resolve_refuses_missing_takeover_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_dir = Path(tmpdir) / "empty_run"
        empty_dir.mkdir()

        with pytest.raises(RuntimeError, match="不存在"):
            resolve_goal_loop_takeover_entrypoint(str(empty_dir))


def test_resolve_refuses_already_resolved_takeover():
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = _paused_run_dir(tmpdir)

        # First resolution succeeds and is the ONLY legitimate one for this
        # takeover request.
        resolve_goal_loop_takeover_entrypoint(str(output_dir), ready_to_resume=True)

        # Manually flip the takeover packet's status the way a real
        # resume/rerun flow would (not something this entrypoint itself
        # does — it never mutates human_takeover.json), to prove the
        # status guard actually blocks a stale/already-handled packet.
        takeover_path = output_dir / "human_takeover.json"
        takeover = json.loads(takeover_path.read_text(encoding="utf-8"))
        takeover["status"] = "resolved"
        takeover_path.write_text(json.dumps(takeover, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(RuntimeError, match="waiting_human"):
            resolve_goal_loop_takeover_entrypoint(str(output_dir))
