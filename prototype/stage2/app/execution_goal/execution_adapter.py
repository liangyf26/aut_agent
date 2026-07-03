"""
Execution adapter for Stage E.

Bridges test-case execution to goal loop primitives (goals, attempts, steps,
evidence), following the same shape as ``FeatureAdapter`` / ``PageAdapter``
from Stages C/D. Concludes each execution goal via the SAME ``feature``
goal_type success predicate Stage D left open:
``feature_identified AND case_generated AND basic_path_executed AND has_feedback``
(see ``goal_loop.predicates``) — Stage D always supplied the first two as
True and left the last two for execution; Stage E supplies all four.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_runner import ExecutionOutcome


class ExecutionAdapter:
    """Adapter for test-case execution using GoalLoopEngine."""

    def __init__(self, engine: "GoalLoopEngine"):
        self.engine = engine
        # goal_id -> execution context (feature_id, page_id, test_case, ...)
        self._execution_context: dict[str, dict[str, Any]] = {}
        # attempt_id -> ordered list of evidence_ids attached during that attempt
        self._attempt_evidence: dict[str, list[str]] = {}

    # --- registration ---------------------------------------------------

    def register_execution_goal(
        self,
        *,
        feature_id: str,
        page_id: str | None,
        test_case: dict[str, Any],
        parent_goal_id: str,
    ) -> str:
        test_case_id = test_case.get("test_case_id") or f"exec_{feature_id}"
        # max_rounds=1: Stage E executes a case's basic path exactly once per
        # run (方案 §4.8). A failed basic path is not retried in the same
        # run — it surfaces as a fixed failure class for round_analysis /
        # next_round_plan to schedule a retry in a LATER round, same as any
        # other goal-loop failure (§13 exit=retry means "goal loop retries",
        # not "retry inside one execution pass").
        goal = self.engine.register_goal(
            goal_type="feature",
            goal_name=f"Execute {test_case.get('type', 'unknown')} case for {feature_id}",
            parent_goal_id=parent_goal_id,
            origin=f"feature_execution::{feature_id}",
            max_rounds=1,
        )
        self._execution_context[goal.goal_id] = {
            "feature_id": feature_id,
            "page_id": page_id,
            "test_case_id": test_case_id,
            "test_case": test_case,
            "risk_level": test_case.get("risk_level"),
        }
        return goal.goal_id

    def get_execution_context(self, goal_id: str) -> dict[str, Any] | None:
        return self._execution_context.get(goal_id)

    # --- attempts / evidence ---------------------------------------------

    def record_execution_attempt(self, *, goal_id: str) -> str:
        attempt = self.engine.start_attempt(goal_id=goal_id)
        self._attempt_evidence[attempt.attempt_id] = []
        return attempt.attempt_id

    def record_action(self, *, attempt_id: str, action_record: dict[str, Any]) -> str:
        """Record one executed action as a step with attached evidence.

        Every recorded step is ``observed=True`` with evidence attached in the
        same call, so a goal never accumulates an evidence gap for actions
        that genuinely happened (方案 §5.7 — a step-with-no-evidence is a
        chain break, not a normal state).
        """

        step = self.engine.add_step(
            attempt_id=attempt_id,
            kind="action",
            action=str(action_record.get("action") or "action"),
            observed=True,
        )
        evidence = self.engine.attach_evidence(
            step.step_id,
            "action",
            uri=None,
            note=json.dumps(action_record, ensure_ascii=False),
        )
        self._attempt_evidence.setdefault(attempt_id, []).append(evidence.evidence_id)
        return evidence.evidence_id

    def record_feedback(self, *, attempt_id: str, feedback: dict[str, Any]) -> str:
        step = self.engine.add_step(attempt_id=attempt_id, kind="feedback", action="observe_feedback", observed=True)
        evidence = self.engine.attach_evidence(
            step.step_id, "feedback", uri=None, note=json.dumps(feedback, ensure_ascii=False)
        )
        self._attempt_evidence.setdefault(attempt_id, []).append(evidence.evidence_id)
        return evidence.evidence_id

    def record_network_capture(
        self, *, attempt_id: str, network_events: list[dict[str, Any]], capture_status: str
    ) -> str:
        """Record the network-capture attempt for this action, honestly.

        ``capture_status`` must say plainly when capture was not applicable
        (e.g. fixture-simulated mode) rather than silently omitting the step —
        an omitted step would look like nobody checked, not like there was
        nothing to check.
        """

        step = self.engine.add_step(attempt_id=attempt_id, kind="network_capture", action=capture_status, observed=True)
        evidence = self.engine.attach_evidence(
            step.step_id,
            "network",
            uri=None,
            note=json.dumps({"capture_status": capture_status, "events": network_events}, ensure_ascii=False),
        )
        self._attempt_evidence.setdefault(attempt_id, []).append(evidence.evidence_id)
        return evidence.evidence_id

    def record_screenshot(self, *, attempt_id: str, screenshot_ref: dict[str, Any]) -> str:
        """Record a real screenshot reference. Only call this with a genuine
        file path — never call it to fabricate evidence for a run that took
        no screenshot (see execution_runner module docstring)."""

        step = self.engine.add_step(attempt_id=attempt_id, kind="screenshot", action="capture_screenshot", observed=True)
        evidence = self.engine.attach_evidence(
            step.step_id,
            "screenshot",
            uri=str(screenshot_ref.get("path") or ""),
            note=json.dumps(screenshot_ref, ensure_ascii=False),
        )
        self._attempt_evidence.setdefault(attempt_id, []).append(evidence.evidence_id)
        return evidence.evidence_id

    # --- conclusion --------------------------------------------------------

    def conclude_execution(self, *, attempt_id: str, outcome: "ExecutionOutcome") -> None:
        """Conclude the attempt via record_success or record_failure.

        A ``passed`` outcome (view_only / entry_confirmation / executable, all
        of which ran their respective basic path and produced feedback)
        satisfies all four feature-goal success signals. A ``failed`` outcome
        records the fixed failure class carried by the outcome (defaulting to
        ``assertion_failed`` if none was set, since a failed execution with
        no more specific class is exactly what that class means).
        """

        from .execution_runner import STATUS_FAILED, STATUS_PASSED

        evidence_refs = list(self._attempt_evidence.get(attempt_id, []))

        if outcome.status == STATUS_PASSED:
            self.engine.record_success(
                attempt_id,
                signals={
                    "feature_identified": True,
                    "case_generated": True,
                    "basic_path_executed": True,
                    "has_feedback": True,
                },
                winning_pattern=f"{outcome.test_case_id} executed via {outcome.execution_mode} and produced feedback",
            )
            return

        failure_class = outcome.failure_reason or "assertion_failed"
        self.engine.record_failure(
            attempt_id,
            explicit_class=failure_class,
            evidence_refs=evidence_refs,
            made_progress=False,
        )


__all__ = ["ExecutionAdapter"]
