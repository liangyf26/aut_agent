"""
CrossSystemRunRecord: one system's already-completed goal-loop run, loaded
either straight from a live engine or from the artifacts
``goal_loop.writer.GoalLoopWriter`` already writes (goal_registry.json,
goal_attempts.jsonl, failure_classifications.jsonl, experience_updates.jsonl).

No new file format: Stage F only reads the EXISTING goal-loop product shapes
(技术方案 §2.6) so a system's run stays independently producible/verifiable by
the stages that already generate it (B/C/D/E); Stage F's own job starts
strictly at comparing across already-frozen runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine

from .system_registry import SystemProfile


@dataclass(slots=True)
class CrossSystemRunRecord:
    """One system's goal-loop run, reduced to what cross-system comparison needs."""

    system: SystemProfile
    run_id: str
    goals: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    classifications: list[dict[str, Any]] = field(default_factory=list)
    experience_updates: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_engine(cls, engine: "GoalLoopEngine", system: SystemProfile) -> "CrossSystemRunRecord":
        """Build from a live engine, scoped to THIS system's goals only.

        Filters by the ``origin`` prefix ``CrossSystemAdapter`` stamps
        (``cross_system::{system_id}``) — the same origin-prefix scoping
        ``execution_goal.round_writer``/``run_report_writer`` already use to
        isolate one stage's goals on a shared engine. This is what lets
        Stage F run MULTIPLE systems' ``CrossSystemAdapter``s against ONE
        shared ``GoalLoopEngine`` (proving the SAME engine instance
        generalizes, not just the same class) while still producing an
        honestly per-system view: without this filter, every system built
        from the same shared engine would see every OTHER system's goals
        too, silently inflating cross-system recurrence counts.
        """

        prefix = f"cross_system::{system.system_id}"
        goal_ids = {
            goal.goal_id for goal in engine.goals.values() if goal.origin == prefix
        }
        return cls(
            system=system,
            run_id=engine.run_id,
            goals=[
                goal.to_dict() for goal in engine.goals.values() if goal.goal_id in goal_ids
            ],
            attempts=[
                attempt.to_dict() for attempt in engine.attempts if attempt.goal_id in goal_ids
            ],
            classifications=[
                c.to_dict() for c in engine.classifications if c.goal_id in goal_ids
            ],
            experience_updates=[
                e.to_dict() for e in engine.experience_updates if e.source_goal in goal_ids
            ],
        )

    @classmethod
    def from_output_dir(cls, output_dir: str | Path, system: SystemProfile) -> "CrossSystemRunRecord":
        """Load from an existing goal-loop artifact directory.

        Reads ``goal_attempts.jsonl`` / ``failure_classifications.jsonl`` /
        ``experience_updates.jsonl`` exactly as
        :class:`goal_loop.writer.GoalLoopWriter` writes them — the same
        fixture files any of Stage B/C/D/E's runs already produce, so a
        second real system needs no new export format to be compared.

        For the ``goals`` list specifically, this checks TWO possible
        sources, in order, because two different writers in this codebase
        use two different filenames/shapes for goal rows:

        1. ``goal_registry.json`` (``GoalLoopWriter``'s own file —
           ``GoalLoopEngine.registry_snapshot()``'s shape: ``{run_id, goals}``).
        2. ``goal_summary.json`` written by THIS package's own
           ``fixture_writer.write_system_goal_loop_artifacts`` (shape:
           ``{run_id, system, goal_count, goals}``) — checked as a fallback
           so Stage F's own per-system export directory is itself a valid
           input back into this loader (e.g. for a second comparison pass
           over already-exported systems), not just genuine upstream
           GoalLoopWriter output.

        If neither file is present, ``goals`` is silently empty (matching
        the pre-existing lenient-missing-file behavior of ``_read_json``) —
        this loader does not raise on a partially-populated directory.
        """

        root = Path(output_dir)
        registry = _read_json(root / "goal_registry.json")
        if "goals" not in registry:
            registry = _read_json(root / "goal_summary.json")
        run_id = str(registry.get("run_id") or system.system_id)
        return cls(
            system=system,
            run_id=run_id,
            goals=list(registry.get("goals") or []),
            attempts=_read_jsonl(root / "goal_attempts.jsonl"),
            classifications=_read_jsonl(root / "failure_classifications.jsonl"),
            experience_updates=_read_jsonl(root / "experience_updates.jsonl"),
        )

    # --- derived views ---------------------------------------------------

    def goal_status(self, goal_id: str) -> str | None:
        for goal in self.goals:
            if goal.get("goal_id") == goal_id:
                return goal.get("status")
        return None

    def recovered_failure_classes(self) -> dict[str, list[str]]:
        """failure_class -> goal_ids that hit it, then went on to succeed.

        A goal "recovers" from ``failure_class`` if at least one of its
        attempts carried that class and the goal's FINAL status is
        succeeded — i.e. the fixed playbook bound to that class
        (``goal_loop.playbook``) actually let the goal reach its success
        predicate on THIS system, not merely that the class occurred.
        """

        by_goal: dict[str, set[str]] = {}
        for attempt in self.attempts:
            failure_class = attempt.get("failure_class")
            goal_id = attempt.get("goal_id")
            if not failure_class or not goal_id:
                continue
            by_goal.setdefault(goal_id, set()).add(failure_class)

        recovered: dict[str, list[str]] = {}
        for goal_id, classes in by_goal.items():
            if self.goal_status(goal_id) != "succeeded":
                continue
            for failure_class in classes:
                recovered.setdefault(failure_class, []).append(goal_id)
        return recovered

    def playbook_by_failure_class(self) -> dict[str, set[str]]:
        """failure_class -> set of suggested_playbook values actually recorded.

        Should always be a singleton set (the playbook table is fixed and
        global — 技术方案 §13); kept as a set so ``comparison.py`` can flag a
        divergence as a real bug rather than silently picking one.
        """

        table: dict[str, set[str]] = {}
        for row in self.classifications:
            failure_class = row.get("failure_reason")
            playbook_id = row.get("suggested_playbook")
            if not failure_class or not playbook_id:
                continue
            table.setdefault(failure_class, set()).add(playbook_id)
        return table

    def seen_failure_classes(self) -> set[str]:
        return {row.get("failure_reason") for row in self.classifications if row.get("failure_reason")}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


__all__ = ["CrossSystemRunRecord"]
