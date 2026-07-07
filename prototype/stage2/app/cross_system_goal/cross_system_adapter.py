"""
CrossSystemAdapter: registers a per-system validation root goal on a
GoalLoopEngine and defaults failure classification to ``scope="system"``.

Every other stage's adapter (DiscoveryAdapter, PageAdapter, FeatureAdapter,
ExecutionAdapter) wraps goal registration + attempt/evidence recording with a
domain-specific default. Stage F's domain is "did the goal loop generalize to
this system", so its one adapter-level default is the classification scope
that ``FailureClassification.scope`` already reserves for this
(goal_loop/models.py: ``scope: str = "goal"  # goal | run | system``).
``page_adapter.py`` already explicitly passes ``scope="goal"`` (the field's
own default, just spelled out); no caller before Stage F ever passed
``scope="system"``.

IMPORTANT — ``evaluate_stop()`` is called from INSIDE :meth:`record_failure`,
not left to a caller's loop. ``execution_goal.orchestrator.execute_all``
calls ``engine.evaluate_stop()`` itself after every failure, but that only
works there because it owns the whole batch loop. Stage F has no equivalent
mandatory driver loop (a system's validation goals may be driven by a real
discovery pass OR replayed from a frozen fixture — see
``orchestrator.py``'s module docstring), so if this adapter itself did not
call ``evaluate_stop()``, a ``HUMAN_REQUIRED_CLASSES`` failure
(``menu_not_found`` is not one, but ``permission_blocked``/
``login_required``/etc. are — 技术方案 §4.11, ``goal_loop/playbook.py``)
would never actually move the goal to ``waiting_human``/``blocked_by_*``.
The goal would stay ``STATUS_RUNNING``, so a careless caller could still
call :meth:`record_success` right after — silently laundering an
unresolved, safety-gated failure into a "recovered" success and, through
``promotion_reviewer``, into a platform-eligible recommendation with zero
human involvement. Calling ``evaluate_stop()`` here closes that gap
structurally: once it fires, ``engine.record_success`` refuses (goal.status
!= STATUS_RUNNING) until the goal is explicitly resumed via
``engine.resume_goal()`` — the same human-in-the-loop gate every other
stage's paused goal relies on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine

from ..goal_loop import predicates as pred
from ..goal_loop.models import PROMOTION_PROJECT
from .system_registry import SystemProfile


class CrossSystemAdapter:
    """Adapts one system's goal-loop validation session to goal loop primitives."""

    def __init__(self, engine: "GoalLoopEngine", system: SystemProfile):
        self.engine = engine
        self.system = system
        self._validation_context: dict[str, dict[str, Any]] = {}

    def register_validation_goal(
        self,
        *,
        goal_type: str,
        goal_name: str,
        parent_goal_id: str | None = None,
        max_rounds: int | None = None,
    ) -> str:
        """Register one menu/page/feature validation goal for this system.

        ``origin`` carries the system_id so downstream readers (and
        ``CrossSystemRunRecord`` when built ``from_engine``) can attribute a
        goal to its system without a new Goal field — the SAME pattern
        ``execution_goal`` uses (``origin=f"feature_execution::{feature_id}"``).
        """

        goal = self.engine.register_goal(
            goal_type=goal_type,
            goal_name=goal_name,
            parent_goal_id=parent_goal_id,
            origin=f"cross_system::{self.system.system_id}",
            max_rounds=max_rounds,
        )
        self._validation_context[goal.goal_id] = {"system_id": self.system.system_id}
        return goal.goal_id

    def record_failure(
        self,
        attempt_id: str,
        *,
        explicit_class: str | None = None,
        signals: Any = None,
        evidence_refs: list[str] | None = None,
        made_progress: bool = False,
    ) -> pred.StopEvaluation:
        """Record a failure with ``scope="system"`` by default, then
        immediately evaluate stop conditions on the goal.

        Returns the :class:`goal_loop.predicates.StopEvaluation` so the
        caller can see whether the goal just paused
        (``evaluation.target_status in PAUSED_STATUSES``) — the same signal
        ``execution_goal.orchestrator.execute_all`` checks to halt its batch
        loop. See the module docstring for why this call is mandatory here
        rather than left to an external loop.
        """

        classification, _ = self.engine.record_failure(
            attempt_id,
            explicit_class=explicit_class,
            signals=signals,
            evidence_refs=evidence_refs,
            scope="system",
            made_progress=made_progress,
        )
        return self.engine.evaluate_stop(classification.goal_id)

    def record_success(
        self,
        attempt_id: str,
        *,
        signals: Any = None,
        winning_pattern: str | None = None,
        promotion_level: str = PROMOTION_PROJECT,
    ):
        """Record a success at ``promotion_level="project"`` by default.

        A single system's success is, by definition, at most project-level
        evidence (技术方案 §2.3 平台级候选不能自动生效) — actual promotion to
        ``platform`` is decided later by ``promotion_reviewer`` once evidence
        from >= 2 systems is compared, never by a single adapter call.

        Refuses the same way ``engine.record_success`` always refuses if the
        goal is not ``STATUS_RUNNING`` — in particular, if :meth:`record_failure`
        just paused this goal for a ``HUMAN_REQUIRED_CLASSES`` reason, this
        call raises ``ValueError`` until a human resolves it via
        ``engine.resume_goal()``, rather than silently succeeding over an
        unresolved safety checkpoint.
        """

        return self.engine.record_success(
            attempt_id,
            signals=signals,
            winning_pattern=winning_pattern,
            promotion_level=promotion_level,
        )

    def get_validation_context(self, goal_id: str) -> dict[str, Any] | None:
        return self._validation_context.get(goal_id)


__all__ = ["CrossSystemAdapter"]

