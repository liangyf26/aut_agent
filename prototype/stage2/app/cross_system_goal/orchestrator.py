"""
CrossSystemGoalOrchestrator: session lifecycle manager for Stage F.

Owns one shared ``GoalLoopEngine`` and one ``CrossSystemAdapter`` per
registered system, drives cross-system comparison + promotion review once
each system's validation goals have concluded, and exports every 实施计划
§8.3 deliverable.

Whether each system's validation goals are driven by a real menu/page/feature
discovery pass (reusing menu_goal/page_goal/feature_goal adapters against the
SAME shared engine) or replayed from an already-frozen ``goal_registry.json``
(``add_system_from_output_dir``) is a caller decision — this orchestrator only
needs a :class:`CrossSystemRunRecord` per system to do its comparison/
promotion job, matching 实施计划 §2.6's fixture-first pattern (每个阶段边界都
产出一份冻结的 golden / fixture 产物，可独立验证).

IMPORTANT — one ``system_id`` may be recorded AT MOST ONCE across the
lifetime of one orchestrator instance, via EITHER ``capture_system_record``
OR ``add_system_from_output_dir``, never both and never twice. Every
downstream count (``MIN_SYSTEMS_FOR_PLATFORM`` in ``promotion_reviewer``,
``systems_observed``/``systems_recovered`` in ``comparison``) counts
``CrossSystemRunRecord`` entries, not distinct ``system_id`` values — a
second snapshot of the same real system (e.g. "capture, do more work,
capture again") would silently inflate a 1-system run into an apparent
2-system run. ``self._captured_system_ids`` enforces this at the
orchestrator boundary. It does NOT protect against two SEPARATE
orchestrators sharing one engine and both calling
``register_system(SystemProfile(system_id="same_id", ...))`` — that remains
a caller-discipline requirement (one orchestrator per shared engine), same
as ``execution_goal.orchestrator``'s "must not be shared with another goal
producer unless their frontiers are kept disjoint" precedent.
"""

from __future__ import annotations

from pathlib import Path

from ..goal_loop.state_machine import GoalLoopEngine
from .comparison import CrossSystemFailureComparison, compare_failure_classifications
from .cross_system_adapter import CrossSystemAdapter
from .fixture_writer import (
    write_cross_system_comparison,
    write_human_takeover_resolution,
    write_promotion_candidates,
    write_system_goal_loop_artifacts,
)
from .promotion_reviewer import PromotionReview, review_experience_updates
from .run_record import CrossSystemRunRecord
from .system_registry import SystemProfile


class CrossSystemGoalOrchestrator:
    """Orchestrates cross-system goal-loop validation for Stage F."""

    def __init__(
        self,
        engine: GoalLoopEngine | None = None,
        *,
        output_dir: str | Path = "output",
        run_id: str = "cross_system_run_001",
    ):
        self.engine = engine or GoalLoopEngine(run_id=run_id)
        self.run_id = run_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._adapters: dict[str, CrossSystemAdapter] = {}
        self._records: list[CrossSystemRunRecord] = []
        self._captured_system_ids: set[str] = set()

    def register_system(self, system: SystemProfile) -> CrossSystemAdapter:
        """Register a system for validation on the SHARED engine.

        Registering the SAME ``system_id`` twice is rejected: a system's
        validation session is meant to be built once and then read back via
        :meth:`capture_system_record`, not silently merged with a second,
        possibly-differently-configured session under the same id.
        """

        if system.system_id in self._adapters:
            raise ValueError(f"system already registered: {system.system_id!r}")
        adapter = CrossSystemAdapter(self.engine, system)
        self._adapters[system.system_id] = adapter
        return adapter

    def capture_system_record(self, system_id: str) -> CrossSystemRunRecord:
        """Snapshot one registered system's goals/attempts/classifications
        off the shared engine, AFTER its validation goals have concluded.

        Raises ``ValueError`` if ``system_id`` was already captured (via
        this method or :meth:`add_system_from_output_dir`) — see the module
        docstring for why a second snapshot of the same system must not
        silently double-count as a second system.
        """

        self._reject_duplicate_capture(system_id)
        adapter = self._adapters.get(system_id)
        if adapter is None:
            raise ValueError(f"unknown system_id: {system_id!r}")
        record = CrossSystemRunRecord.from_engine(self.engine, adapter.system)
        self._records.append(record)
        self._captured_system_ids.add(system_id)
        return record

    def add_system_from_output_dir(
        self, system: SystemProfile, output_dir: str | Path
    ) -> CrossSystemRunRecord:
        """Load an already-frozen system record from a prior GoalLoopWriter
        output directory, without needing it on the shared engine."""

        self._reject_duplicate_capture(system.system_id)
        record = CrossSystemRunRecord.from_output_dir(output_dir, system)
        self._records.append(record)
        self._captured_system_ids.add(system.system_id)
        return record

    def _reject_duplicate_capture(self, system_id: str) -> None:
        if system_id in self._captured_system_ids:
            raise ValueError(
                f"system {system_id!r} was already captured; capturing the same "
                "system twice would double-count it in cross-system comparisons"
            )

    # --- cross-system analysis -------------------------------------------

    def compare_failures(self) -> list[CrossSystemFailureComparison]:
        return compare_failure_classifications(self._records)

    def review_promotions(self) -> list[PromotionReview]:
        comparisons = self.compare_failures()
        return review_experience_updates(self._records, comparisons=comparisons)

    def get_summary(self) -> dict:
        reviews = self.review_promotions()
        return {
            "run_id": self.run_id,
            "system_count": len(self._records),
            "systems": [record.system.system_id for record in self._records],
            "promotion_review_count": len(reviews),
            "platform_eligible_count": sum(1 for r in reviews if r.eligible_for_platform),
        }

    # --- exports (实施计划 §8.3) -------------------------------------------

    def export_per_system_artifacts(self) -> dict[str, dict[str, Path]]:
        return {
            record.system.system_id: write_system_goal_loop_artifacts(record, self.output_dir)
            for record in self._records
        }

    def export_cross_system_comparison(
        self, filename: str = "cross_system_comparison.json"
    ) -> Path:
        return write_cross_system_comparison(
            self.compare_failures(),
            self.output_dir / filename,
            systems=[record.system for record in self._records],
        )

    def export_promotion_candidates(
        self, filename: str = "promotion_candidates.json"
    ) -> Path:
        return write_promotion_candidates(self.review_promotions(), self.output_dir / filename)

    def export_human_takeover_resolution(
        self,
        *,
        status: str,
        operator_id: str | None = None,
        note: str | None = None,
        ready_to_resume: bool = False,
        resolved_at: str,
        filename: str = "human_takeover_resolution.json",
    ) -> Path:
        return write_human_takeover_resolution(
            self.output_dir,
            status=status,
            operator_id=operator_id,
            note=note,
            ready_to_resume=ready_to_resume,
            resolved_at=resolved_at,
            filename=filename,
        )

    def export_all(self) -> dict[str, object]:
        """Write every 实施计划 §8.3 deliverable except
        ``human_takeover_resolution.json`` (only written when a real human
        takeover was resolved — see :meth:`export_human_takeover_resolution`
        and ``fixture_writer.write_human_takeover_resolution``'s docstring)."""

        return {
            "per_system": self.export_per_system_artifacts(),
            "cross_system_comparison": self.export_cross_system_comparison(),
            "promotion_candidates": self.export_promotion_candidates(),
        }


__all__ = ["CrossSystemGoalOrchestrator"]
