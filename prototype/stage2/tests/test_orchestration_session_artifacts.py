from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.orchestration.session_artifacts import (
    sync_orchestration_session_artifacts,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class OrchestrationSessionArtifactsTests(unittest.TestCase):
    def test_sync_orchestration_session_artifacts_groups_runs_and_persists_index(self) -> None:
        with TemporaryDirectory() as tmpdir:
            stage2_root = Path(tmpdir) / "stage2"
            run1 = stage2_root / "20260623_120000_modelA"
            run2 = stage2_root / "20260623_121500_modelA"
            run1.mkdir(parents=True, exist_ok=True)
            run2.mkdir(parents=True, exist_ok=True)

            common_stream = "suyuan_online_apply::AI_tester"
            _write_json(
                run1 / "current_status.json",
                {
                    "run_id": run1.name,
                    "overall_status": "failed",
                    "template_name": "suyuan_online_apply",
                    "model_name": "AI-tester",
                    "project_name": "proj",
                    "current_phase": "verification",
                    "current_phase_label": "验证",
                    "latest_message": "first failure",
                    "updated_at": "2026-06-23T12:00:00",
                },
            )
            _write_json(
                run1 / "round_input.json",
                {
                    "orchestration_stream_id": common_stream,
                    "template_name": "suyuan_online_apply",
                    "model_name": "AI-tester",
                    "project_name": "proj",
                    "round_index": 1,
                },
            )
            _write_json(
                run1 / "next_round_decision.json",
                {
                    "status": "needs_review",
                    "primary_reason": "manual review required",
                    "target_stage": "verification",
                    "scheduled_action_ids": ["retry-001"],
                    "should_start_next_round": None,
                },
            )
            _write_json(
                run1 / "human_takeover.json",
                {
                    "status": "waiting_human",
                    "target_stage": "verification",
                    "reason": "manual review required",
                    "resume_command": "python -m prototype.stage2.main --resume-human-takeover x",
                    "pending_actions": [{"action_id": "retry-001", "title": "resume"}],
                },
            )
            _write_json(
                run1 / "reports" / "run_report.json",
                {
                    "summary": {
                        "run_id": run1.name,
                        "status": "failed",
                        "project_name": "proj",
                        "template_name": "suyuan_online_apply",
                        "orchestration_stream_id": common_stream,
                        "promotion_candidate_count": 1,
                    },
                    "notes": ["round-1 note"],
                },
            )

            _write_json(
                run2 / "current_status.json",
                {
                    "run_id": run2.name,
                    "overall_status": "completed",
                    "template_name": "suyuan_online_apply",
                    "model_name": "AI-tester",
                    "project_name": "proj",
                    "current_phase": "reporting",
                    "current_phase_label": "报告",
                    "latest_message": "second round completed",
                    "updated_at": "2026-06-23T12:15:00",
                    "elapsed_ms": 1200,
                },
            )
            _write_json(
                run2 / "round_input.json",
                {
                    "orchestration_stream_id": common_stream,
                    "template_name": "suyuan_online_apply",
                    "model_name": "AI-tester",
                    "project_name": "proj",
                    "round_index": 2,
                    "previous_run_id": run1.name,
                },
            )
            _write_json(
                run2 / "next_round_decision.json",
                {
                    "status": "no_retry_needed",
                    "primary_reason": "done",
                    "target_stage": None,
                    "scheduled_action_ids": [],
                    "should_start_next_round": False,
                },
            )
            _write_json(
                run2 / "human_takeover_resolution.json",
                {
                    "status": "resolved",
                    "operator_id": "tester-1",
                    "note": "manually verified",
                    "ready_to_resume": False,
                    "resolved_at": "2026-06-23T12:10:00",
                },
            )
            _write_json(
                run2 / "reports" / "run_report.json",
                {
                    "summary": {
                        "run_id": run2.name,
                        "status": "completed",
                        "project_name": "proj",
                        "template_name": "suyuan_online_apply",
                        "orchestration_stream_id": common_stream,
                        "promotion_candidate_count": 2,
                    },
                    "notes": ["round-2 note"],
                },
            )

            payload = sync_orchestration_session_artifacts(stage2_root)

            self.assertEqual(len(payload), 1)
            session = payload[0]
            self.assertEqual(session["sessionId"], common_stream)
            self.assertEqual(session["runCount"], 2)
            self.assertEqual(session["latestRunId"], run2.name)
            self.assertTrue(session["waitingHuman"])
            self.assertEqual(session["unresolvedHumanRunId"], run1.name)
            self.assertTrue(session["latestResumeCommand"])
            self.assertEqual(session["stats"]["promotionCandidateTotal"], 3)
            self.assertEqual(session["timeline"][0]["runId"], run2.name)
            self.assertEqual(session["timeline"][1]["runId"], run1.name)

            sessions_root = stage2_root / "sessions"
            index_payload = json.loads((sessions_root / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index_payload["session_count"], 1)
            self.assertEqual(index_payload["sessions"][0]["session_id"], common_stream)

            session_dir = sessions_root / session["directoryName"]
            summary_payload = json.loads((session_dir / "session_summary.json").read_text(encoding="utf-8"))
            timeline_payload = json.loads((session_dir / "session_timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["latest_run_id"], run2.name)
            self.assertEqual(summary_payload["unresolved_human_run_id"], run1.name)
            self.assertEqual(len(timeline_payload["runs"]), 2)


if __name__ == "__main__":
    unittest.main()
