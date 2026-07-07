"""Persist goal-loop artifacts.

Writes the goal-entity products introduced by v4 plus a run-center consumable
current-status view projected via :mod:`compat`. JSON is written with
``indent=2, ensure_ascii=False`` and jsonl one object per line, matching
``runtime.ArtifactWriter`` / ``progress.ProgressWriter`` conventions.

``write_all`` is idempotent: it rewrites the full snapshot each call so a run
can be recovered from structured products alone (方案 §8.3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import compat
from .predicates import StopEvaluation
from .state_machine import GoalLoopEngine


class GoalLoopWriter:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.output_dir / "goal_registry.json"
        self.state_path = self.output_dir / "goal_state.json"
        self.attempts_path = self.output_dir / "goal_attempts.jsonl"
        self.classifications_path = self.output_dir / "failure_classifications.jsonl"
        self.playbook_actions_path = self.output_dir / "playbook_actions.jsonl"
        self.experience_updates_path = self.output_dir / "experience_updates.jsonl"
        self.summary_path = self.output_dir / "goal_summary.json"
        self.current_status_path = self.output_dir / "goal_current_status.json"

    def write_all(
        self,
        engine: GoalLoopEngine,
        *,
        stop_evaluation: StopEvaluation | None = None,
        template_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Path]:
        self._write_json(self.registry_path, engine.registry_snapshot())
        self._write_json(self.state_path, engine.state_snapshot(stop_evaluation))
        self._write_jsonl(self.attempts_path, [a.to_dict() for a in engine.attempts])
        self._write_jsonl(
            self.classifications_path, [c.to_dict() for c in engine.classifications]
        )
        self._write_jsonl(
            self.playbook_actions_path, [p.to_dict() for p in engine.playbook_action_records]
        )
        self._write_jsonl(
            self.experience_updates_path, [e.to_dict() for e in engine.experience_updates]
        )

        summaries = [engine.build_summary(gid).to_dict() for gid in engine.goals]
        escalations = [row.to_dict() for row in engine.evaluate_escalations()]
        summary_payload: dict[str, Any] = {
            "run_id": engine.run_id,
            "active_goal_id": engine.active_goal_id,
            "goal_count": len(engine.goals),
            "summaries": summaries,
            "escalations": escalations,
            "reuse_mapping": compat.MAPPING,
        }
        self._write_json(self.summary_path, summary_payload)

        if engine.active_goal_id is not None:
            current_view = compat.goal_summary_to_current_status_view(
                engine.build_summary(engine.active_goal_id),
                run_id=engine.run_id,
                template_name=template_name,
                model_name=model_name,
            )
        else:
            # no active goal yet: emit a placeholder so the advertised path always
            # references a real file (a run can be recovered from products alone).
            current_view = {
                "run_id": engine.run_id,
                "template_name": template_name,
                "model_name": model_name,
                "overall_status": "pending",
                "goal_status": None,
                "current_target": None,
                "latest_message": "no active goal",
                "waiting_reason": None,
                "blocked_reason": None,
                "next_action": "activate a goal",
                "stats": {},
            }
        self._write_json(self.current_status_path, current_view)

        return {
            "goal_registry": self.registry_path,
            "goal_state": self.state_path,
            "goal_attempts": self.attempts_path,
            "failure_classifications": self.classifications_path,
            "playbook_actions": self.playbook_actions_path,
            "experience_updates": self.experience_updates_path,
            "goal_summary": self.summary_path,
            "goal_current_status": self.current_status_path,
        }

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: list[Any]) -> None:
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


__all__ = ["GoalLoopWriter"]
