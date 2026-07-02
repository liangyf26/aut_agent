"""Goal loop engine: registry + tree + frontier + state machine.

The engine owns the whole runtime state for one run's goal loop:

- a registry of goals forming a tree (parent/child links),
- a frontier queue of goals waiting to be advanced,
- a single active goal at a time,
- attempts / steps / evidence bound by the four-level id chain,
- fixed failure classifications and the playbook actions they select,
- experience updates and a systematic-defect escalation counter.

It deliberately reuses the fixed classifier (:mod:`classification`), the fixed
playbook table (:mod:`playbook`) and the computable predicates
(:mod:`predicates`) so failure handling stays a finite, auditable state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import classification as fc
from . import playbook as pb
from . import predicates as pred
from .ids import IdAllocator
from .models import (
    ATTEMPT_FAILED,
    ATTEMPT_RUNNING,
    ATTEMPT_SUCCEEDED,
    GOAL_TYPES,
    PAUSED_STATUSES,
    PROMOTION_PLATFORM,
    PROMOTION_RUN,
    STATUS_PLANNED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_SUPERSEDED,
    TERMINAL_STATUSES,
    DefectEscalation,
    EvidenceRef,
    ExperienceUpdate,
    FailureClassification,
    Goal,
    GoalAttempt,
    GoalStep,
    GoalSummary,
    PlaybookAction,
    SuccessCriterion,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GoalLoopEngine:
    def __init__(self, run_id: str, *, thresholds: pred.Thresholds = pred.DEFAULT_THRESHOLDS) -> None:
        self.run_id = run_id
        self.thresholds = thresholds
        self.ids = IdAllocator()
        self.goals: dict[str, Goal] = {}
        self.frontier: list[str] = []
        self.active_goal_id: str | None = None
        self.attempts: list[GoalAttempt] = []
        self._attempt_index: dict[str, GoalAttempt] = {}
        self.steps: dict[str, GoalStep] = {}
        self.evidence: dict[str, EvidenceRef] = {}
        self.classifications: list[FailureClassification] = []
        self.playbook_action_records: list[PlaybookAction] = []
        self.experience_updates: list[ExperienceUpdate] = []
        self._defect_counter: dict[str, dict[str, float]] = {}
        self._fc_seq = 0
        self._pa_seq = 0
        self._exp_seq = 0

    # --- goal registration & tree -------------------------------------------

    def register_goal(
        self,
        goal_type: str,
        goal_name: str,
        *,
        parent_goal_id: str | None = None,
        origin: str | None = None,
        max_rounds: int | None = None,
        evidence_requirements: list[str] | None = None,
        allow_human_intervention: bool = True,
    ) -> Goal:
        if goal_type not in GOAL_TYPES:
            raise ValueError(f"unknown goal_type: {goal_type!r}")
        if parent_goal_id is not None and parent_goal_id not in self.goals:
            raise ValueError(f"unknown parent_goal_id: {parent_goal_id!r}")

        goal_id = self.ids.new_goal_id()
        now = _now_iso()
        criteria = [
            SuccessCriterion(
                predicate_name=item["predicate_name"],
                expression=item["expression"],
                params=item.get("params", {}),
            )
            for item in pred.success_criteria_for(goal_type, self.thresholds)
        ]
        goal = Goal(
            goal_id=goal_id,
            goal_type=goal_type,
            goal_name=goal_name,
            status=STATUS_PLANNED,
            parent_goal_id=parent_goal_id,
            origin=origin,
            success_criteria=criteria,
            evidence_requirements=list(evidence_requirements or []),
            allow_human_intervention=allow_human_intervention,
            max_rounds=int(max_rounds) if max_rounds is not None else self.thresholds.max_rounds_for(goal_type),
            created_at=now,
            updated_at=now,
        )
        self.goals[goal_id] = goal
        self.frontier.append(goal_id)
        if parent_goal_id is not None:
            self.goals[parent_goal_id].child_goal_ids.append(goal_id)
        return goal

    def derive_child_goal(
        self,
        parent_goal_id: str,
        goal_type: str,
        goal_name: str,
        *,
        origin: str | None = None,
        **kwargs: Any,
    ) -> Goal:
        return self.register_goal(
            goal_type, goal_name, parent_goal_id=parent_goal_id, origin=origin, **kwargs
        )

    def activate_next(self) -> Goal | None:
        """Advance to the next planned goal, enforcing single active goal.

        A paused active goal (waiting_human / blocked_*) is NOT resolved, so it
        blocks advancement — it must be resumed (:meth:`resume_goal`) or first
        driven to a terminal status. Only terminal statuses (succeeded / failed /
        stopped / superseded) let the frontier move on.
        """

        if self.active_goal_id is not None:
            active = self.goals[self.active_goal_id]
            if active.status not in TERMINAL_STATUSES:
                raise ValueError(
                    f"active goal {self.active_goal_id} is not resolved (status={active.status}); "
                    "resume or resolve it before advancing"
                )

        while self.frontier:
            goal_id = self.frontier.pop(0)
            goal = self.goals[goal_id]
            if goal.status != STATUS_PLANNED:
                continue
            goal.status = STATUS_RUNNING
            goal.updated_at = _now_iso()
            self.active_goal_id = goal_id
            return goal
        return None

    def resume_goal(self, goal_id: str | None = None) -> Goal:
        """Move a paused goal (waiting_human / blocked_*) back to running.

        Refuses if a *different* goal is still the live active goal, so resuming
        can never produce two RUNNING goals at once (single active goal).
        """

        goal_id = self._resolve_goal_id(goal_id)
        goal = self.goals[goal_id]
        if goal.status not in PAUSED_STATUSES:
            raise ValueError(f"goal {goal_id} is not paused (status={goal.status})")
        if (
            self.active_goal_id is not None
            and self.active_goal_id != goal_id
            and self.goals[self.active_goal_id].status not in TERMINAL_STATUSES
        ):
            raise ValueError(
                f"cannot resume {goal_id}: goal {self.active_goal_id} is still active "
                f"(status={self.goals[self.active_goal_id].status})"
            )
        goal.status = STATUS_RUNNING
        goal.stop_reason = None
        goal.is_active_conclusion = False
        goal.updated_at = _now_iso()
        self.active_goal_id = goal_id
        return goal

    def supersede_active(self, next_goal_id: str) -> None:
        """Conclude the active goal as superseded and make the named successor next."""

        if self.active_goal_id is None:
            return
        if next_goal_id not in self.goals:
            raise ValueError(f"unknown successor goal_id: {next_goal_id!r}")
        goal = self.goals[self.active_goal_id]
        goal.superseded = True
        goal.superseded_by = next_goal_id
        goal.status = STATUS_SUPERSEDED
        goal.is_active_conclusion = False
        goal.updated_at = _now_iso()
        # release the active slot: a superseded goal is concluded, so the
        # run-center view must not keep reporting it as the current goal.
        self.active_goal_id = None
        # honor the named successor: it must be the goal that runs next
        if next_goal_id in self.frontier:
            self.frontier.remove(next_goal_id)
            self.frontier.insert(0, next_goal_id)

    # --- attempts / steps / evidence ----------------------------------------

    def _resolve_goal_id(self, goal_id: str | None) -> str:
        goal_id = goal_id or self.active_goal_id
        if goal_id is None:
            raise ValueError("no active goal; call activate_next() first")
        if goal_id not in self.goals:
            raise ValueError(f"unknown goal_id: {goal_id!r}")
        return goal_id

    def start_attempt(self, goal_id: str | None = None) -> GoalAttempt:
        goal_id = self._resolve_goal_id(goal_id)
        goal = self.goals[goal_id]
        if goal.status != STATUS_RUNNING:
            raise ValueError(f"goal {goal_id} is not running (status={goal.status})")
        if any(a.goal_id == goal_id and a.status == ATTEMPT_RUNNING for a in self.attempts):
            raise ValueError(
                f"goal {goal_id} already has an attempt in flight; resolve it before starting another"
            )
        goal.attempt_count += 1
        goal.updated_at = _now_iso()
        attempt = GoalAttempt(
            attempt_id=self.ids.new_attempt_id(goal_id),
            goal_id=goal_id,
            index=goal.attempt_count,
            status=ATTEMPT_RUNNING,
            started_at=_now_iso(),
        )
        self.attempts.append(attempt)
        self._attempt_index[attempt.attempt_id] = attempt
        return attempt

    def add_step(
        self,
        attempt_id: str,
        kind: str,
        *,
        action: str | None = None,
        observed: bool = True,
        status: str = "recorded",
    ) -> GoalStep:
        attempt = self._attempt_index.get(attempt_id)
        if attempt is None:
            raise ValueError(f"unknown attempt_id: {attempt_id!r}")
        step = GoalStep(
            step_id=self.ids.new_step_id(attempt_id),
            attempt_id=attempt_id,
            index=len(attempt.steps) + 1,
            kind=kind,
            action=action,
            observed=observed,
            status=status,
        )
        attempt.steps.append(step)
        self.steps[step.step_id] = step
        return step

    def attach_evidence(
        self, step_id: str, kind: str, *, uri: str | None = None, note: str | None = None
    ) -> EvidenceRef:
        """Attach one atomic evidence to a step.

        Enforces the parent pointer: evidence for an unknown step is a chain
        break and is rejected (callers should record ``evidence_incomplete``
        instead of fabricating a run-level attachment).
        """

        if step_id not in self.steps:
            raise ValueError(
                f"cannot attach evidence to unknown step {step_id!r}; "
                "run-level evidence must not be bound as step evidence"
            )
        ev = EvidenceRef(
            evidence_id=self.ids.new_evidence_id(step_id),
            owner_step_id=step_id,
            kind=kind,
            uri=uri,
            note=note,
        )
        self.evidence[ev.evidence_id] = ev
        self.steps[step_id].evidence_ids.append(ev.evidence_id)
        return ev

    def check_evidence_complete(self, attempt_id: str) -> list[str]:
        """Return a list of evidence gaps for an attempt (empty == complete)."""

        attempt = self._attempt_index.get(attempt_id)
        if attempt is None:
            raise ValueError(f"unknown attempt_id: {attempt_id!r}")
        gaps: list[str] = []
        if not attempt.steps:
            gaps.append(f"attempt {attempt_id} has no recorded steps")
        for step in attempt.steps:
            if step.observed and not step.evidence_ids:
                gaps.append(f"step {step.step_id} observed but has no evidence")
        return gaps

    # --- failure classification + playbook ----------------------------------

    def last_attempt_for(self, goal_id: str) -> GoalAttempt | None:
        for attempt in reversed(self.attempts):
            if attempt.goal_id == goal_id:
                return attempt
        return None

    def _attempt_of_evidence(self, evidence_id: str) -> str | None:
        """Resolve which attempt an evidence id belongs to, via its owning step."""

        ev = self.evidence.get(evidence_id)
        if ev is None:
            return None
        step = self.steps.get(ev.owner_step_id)
        return step.attempt_id if step else None

    def _apply_failure(
        self,
        attempt: GoalAttempt,
        goal: Goal,
        failure_class: str,
        confidence: str,
        *,
        valid_refs: list[str],
        scope: str,
        made_progress: bool,
        note: str | None = None,
    ) -> tuple[FailureClassification, PlaybookAction]:
        spec = pb.select_playbook(failure_class)

        self._fc_seq += 1
        classification = FailureClassification(
            classification_id=f"fc-{self._fc_seq:04d}",
            goal_id=goal.goal_id,
            attempt_id=attempt.attempt_id,
            failure_reason=failure_class,
            reason_confidence=confidence,
            suggested_playbook=spec.playbook_id,
            scope=scope,
            iteration_category=fc.to_iteration_category(failure_class),
            is_overflow=(failure_class == fc.UNKNOWN),
            evidence_refs=list(valid_refs),
        )
        self.classifications.append(classification)

        self._pa_seq += 1
        action = PlaybookAction(
            playbook_action_id=f"pa-{self._pa_seq:04d}",
            goal_id=goal.goal_id,
            attempt_id=attempt.attempt_id,
            playbook_id=spec.playbook_id,
            trigger_reason=failure_class,
            action_steps=list(spec.action_steps),
            expected_effect=spec.expected_effect,
            exit=spec.exit,
            safety_constraints=list(spec.safety_constraints),
            output_evidence=list(valid_refs),
        )
        self.playbook_action_records.append(action)

        attempt.status = ATTEMPT_FAILED
        attempt.failure_class = failure_class
        attempt.playbook_id = spec.playbook_id
        attempt.ended_at = _now_iso()
        attempt.result = failure_class
        if note:
            attempt.notes.append(note)

        goal.no_improvement_streak = 0 if made_progress else goal.no_improvement_streak + 1
        goal.updated_at = _now_iso()

        counter = self._defect_counter.setdefault(
            failure_class, {"occurrences": 0, "resolved": 0, "no_gain_streak": 0}
        )
        counter["occurrences"] += 1
        counter["no_gain_streak"] += 1

        return classification, action

    def record_failure(
        self,
        attempt_id: str,
        *,
        explicit_class: str | None = None,
        signals: Any = None,
        evidence_refs: list[str] | None = None,
        scope: str = "goal",
        made_progress: bool = False,
    ) -> tuple[FailureClassification, PlaybookAction]:
        attempt = self._attempt_index.get(attempt_id)
        if attempt is None:
            raise ValueError(f"unknown attempt_id: {attempt_id!r}")
        if attempt.status != ATTEMPT_RUNNING:
            raise ValueError(f"attempt {attempt_id} is not running (status={attempt.status})")
        goal = self.goals[attempt.goal_id]

        # Only evidence bound to THIS attempt's chain may enter the audit trail.
        # First validate existence, then validate ownership (Finding 1 from review).
        refs = list(evidence_refs or [])
        missing_refs = [r for r in refs if r not in self.evidence]

        note: str | None = None
        if missing_refs:
            # Non-existent evidence refs are a chain break: reject with EVIDENCE_INCOMPLETE
            failure_class, confidence = fc.EVIDENCE_INCOMPLETE, fc.CONFIDENCE_HIGH
            note = f"evidence refs do not exist: {missing_refs}"
            valid_refs = []
        else:
            valid_refs = [r for r in refs if self._attempt_of_evidence(r) == attempt_id]
            invalid_refs = [r for r in refs if r not in set(valid_refs)]

            if invalid_refs:
                # cross-goal / cross-attempt refs are a chain break: surface as evidence_incomplete (§5.7).
                failure_class, confidence = fc.EVIDENCE_INCOMPLETE, fc.CONFIDENCE_HIGH
                note = f"dropped {len(invalid_refs)} evidence ref(s) not bound to this attempt: {invalid_refs}"
            else:
                failure_class, confidence = fc.classify_failure(
                    explicit_class=explicit_class, signals=signals
                )

        return self._apply_failure(
            attempt,
            goal,
            failure_class,
            confidence,
            valid_refs=valid_refs,
            scope=scope,
            made_progress=made_progress,
            note=note,
        )

    # --- success + experience -----------------------------------------------

    def evaluate_goal_success(self, goal_id: str | None = None, *, signals: Any = None) -> pred.PredicateResult:
        goal_id = self._resolve_goal_id(goal_id)
        goal = self.goals[goal_id]
        return pred.evaluate_success(
            goal_type=goal.goal_type, signals=signals or {}, thresholds=self.thresholds
        )

    def record_success(
        self,
        attempt_id: str,
        *,
        signals: Any = None,
        winning_pattern: str | None = None,
        promotion_level: str = PROMOTION_RUN,
    ) -> tuple[pred.PredicateResult, ExperienceUpdate]:
        attempt = self._attempt_index.get(attempt_id)
        if attempt is None:
            raise ValueError(f"unknown attempt_id: {attempt_id!r}")
        goal = self.goals[attempt.goal_id]

        # Success may only conclude a live attempt on a running goal — never
        # resurrect a goal already concluded by evaluate_stop, nor flip a failed
        # attempt to succeeded.
        if goal.status != STATUS_RUNNING:
            raise ValueError(
                f"cannot record success: goal {goal.goal_id} is not running (status={goal.status})"
            )
        if attempt.status != ATTEMPT_RUNNING:
            raise ValueError(
                f"cannot record success: attempt {attempt_id} is not running (status={attempt.status})"
            )

        # Evidence gate: an observed step with no evidence is a chain break, so
        # success is refused loudly (not silently passed, per standard #8 / §5.7).
        # The refusal is pure: it does NOT consume the attempt or touch the
        # no_improvement_streak / defect counter — a refused commit must not be
        # counted as a real failure. The caller may attach the missing evidence
        # and retry the SAME attempt, or record_failure(EVIDENCE_INCOMPLETE)
        # explicitly if the gap is genuine. A step-less attempt is tolerated (the
        # skeleton allows signal-only conclusions).

        # First validate all step evidence exists (Finding 1 from review)
        all_refs: set[str] = set()
        for step in self.steps.values():
            if step.attempt_id == attempt_id:
                all_refs.update(step.evidence_ids)
        missing_refs = [r for r in all_refs if r not in self.evidence]
        if missing_refs:
            raise ValueError(f"cannot record success: evidence refs do not exist: {missing_refs}")

        # Then check for gaps (steps without evidence)
        evidence_gaps = [g for g in self.check_evidence_complete(attempt_id) if "has no evidence" in g]
        if evidence_gaps:
            raise ValueError(f"cannot record success: evidence incomplete: {evidence_gaps}")

        result = pred.evaluate_success(
            goal_type=goal.goal_type, signals=signals or {}, thresholds=self.thresholds
        )
        if not result.value:
            raise ValueError(
                f"success predicate not satisfied for goal {goal.goal_id}: {result.expression}"
            )

        attempt.status = ATTEMPT_SUCCEEDED
        attempt.ended_at = _now_iso()
        attempt.result = "succeeded"

        goal.status = STATUS_SUCCEEDED
        goal.is_active_conclusion = True
        goal.stop_reason = None
        goal.no_improvement_streak = 0
        goal.updated_at = _now_iso()

        # Credit the previously-selected playbook (if the failing attempt used
        # one) so the defect counter's success rate reflects recoveries.
        for prior in reversed(self.attempts):
            if prior.goal_id == goal.goal_id and prior.failure_class:
                counter = self._defect_counter.get(prior.failure_class)
                if counter is not None:
                    counter["resolved"] += 1
                    counter["no_gain_streak"] = 0
                break

        self._exp_seq += 1
        experience = ExperienceUpdate(
            update_id=f"exp-{self._exp_seq:04d}",
            source_goal=goal.goal_id,
            kind="winning",
            promotion_level=promotion_level,
            confidence="high",
            review_status="needs_review" if promotion_level == PROMOTION_PLATFORM else "auto_recorded",
            winning_pattern=winning_pattern
            or f"{goal.goal_type} goal '{goal.goal_name}' reached success predicate {result.name}",
        )
        self.experience_updates.append(experience)
        return result, experience

    # --- stop evaluation -----------------------------------------------------

    def evaluate_stop(
        self,
        goal_id: str | None = None,
        *,
        policy_blocked: bool = False,
        executor_unavailable: bool = False,
        playwright_required: bool = True,
    ) -> pred.StopEvaluation:
        goal_id = self._resolve_goal_id(goal_id)
        goal = self.goals[goal_id]
        last = self.last_attempt_for(goal_id)
        active_failure_class = last.failure_class if last else None

        evaluation = pred.evaluate_stop_conditions(
            goal_type=goal.goal_type,
            attempt_count=goal.attempt_count,
            max_rounds=goal.max_rounds,
            no_improvement_streak=goal.no_improvement_streak,
            active_failure_class=active_failure_class,
            allow_human_intervention=goal.allow_human_intervention,
            policy_blocked=policy_blocked,
            executor_unavailable=executor_unavailable,
            playwright_required=playwright_required,
            thresholds=self.thresholds,
        )
        if evaluation.should_stop and goal.status not in TERMINAL_STATUSES:
            goal.status = evaluation.target_status or goal.status
            goal.stop_reason = evaluation.primary_reason
            goal.is_active_conclusion = True
            goal.updated_at = _now_iso()
        return evaluation

    # --- escalation counter (§7.4) ------------------------------------------

    def evaluate_escalations(self, *, scope: str = "run") -> list[DefectEscalation]:
        rows: list[DefectEscalation] = []
        for failure_class, counter in sorted(self._defect_counter.items()):
            occurrences = int(counter["occurrences"])
            resolved = int(counter["resolved"])
            success_rate = (resolved / occurrences) if occurrences else 0.0
            triggered = (
                occurrences >= self.thresholds.escalation_occurrence_threshold
                and success_rate <= self.thresholds.escalation_success_floor
            )
            rows.append(
                DefectEscalation(
                    failure_class=failure_class,
                    scope=scope,
                    occurrences=occurrences,
                    playbook_success_rate=round(success_rate, 4),
                    no_gain_streak=int(counter["no_gain_streak"]),
                    triggered=triggered,
                    recommendation=(
                        f"escalate {failure_class} to programming model: "
                        f"{occurrences} occurrences with success_rate {success_rate:.2f}"
                        if triggered
                        else None
                    ),
                )
            )
        return rows

    def record_escalation_experiences(self, *, scope: str = "run") -> list[ExperienceUpdate]:
        """Turn triggered escalations into failed/escalation experience updates."""

        emitted: list[ExperienceUpdate] = []
        for row in self.evaluate_escalations(scope=scope):
            if not row.triggered:
                continue
            self._exp_seq += 1
            update = ExperienceUpdate(
                update_id=f"exp-{self._exp_seq:04d}",
                source_goal=self.active_goal_id or "run",
                kind="escalation",
                promotion_level="platform",
                confidence="medium",
                review_status="needs_review",
                failed_pattern=f"{row.failure_class} recurring ({row.occurrences}x)",
                note=row.recommendation,
            )
            self.experience_updates.append(update)
            emitted.append(update)
        return emitted

    # --- summaries & snapshots ----------------------------------------------

    def _primary_failure_class(self, goal_id: str) -> str | None:
        """The obstacle a goal hit: the most recent attempt that carried a
        failure class, even if a later attempt then succeeded."""

        for attempt in reversed(self.attempts):
            if attempt.goal_id == goal_id and attempt.failure_class:
                return attempt.failure_class
        return None

    def build_summary(self, goal_id: str) -> GoalSummary:
        goal = self.goals[goal_id]
        experience_note = None
        for update in reversed(self.experience_updates):
            if update.source_goal == goal_id:
                experience_note = update.winning_pattern or update.failed_pattern
                break
        return GoalSummary(
            goal_id=goal_id,
            goal_type=goal.goal_type,
            goal_name=goal.goal_name,
            status=goal.status,
            succeeded=goal.status == STATUS_SUCCEEDED,
            attempt_count=goal.attempt_count,
            is_active_goal=goal_id == self.active_goal_id,
            is_active_conclusion=goal.is_active_conclusion,
            superseded=goal.superseded,
            superseded_by=goal.superseded_by,
            primary_failure_class=self._primary_failure_class(goal_id),
            stop_reason=goal.stop_reason,
            next_action=self._next_action_for(goal),
            experience_note=experience_note,
        )

    def _next_action_for(self, goal: Goal) -> str:
        if goal.status == STATUS_SUCCEEDED:
            return "advance frontier to next goal"
        if goal.status == STATUS_PLANNED:
            return "activate goal"
        if goal.status == STATUS_RUNNING:
            return "start or continue attempt"
        if goal.status == "waiting_human":
            return "resolve human task, then resume"
        if goal.status in {"blocked_by_policy", "blocked_by_executor"}:
            return "resolve blocker, then re-evaluate"
        if goal.status in {"failed_max_rounds", "stopped_no_progress"}:
            return "review failure clusters / consider escalation"
        return "review goal state"

    def registry_snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "active_goal_id": self.active_goal_id,
            "frontier": list(self.frontier),
            "goals": [self.goals[gid].to_dict() for gid in self.goals],
        }

    def state_snapshot(self, stop_evaluation: pred.StopEvaluation | None = None) -> dict[str, Any]:
        active = self.goals.get(self.active_goal_id) if self.active_goal_id else None
        return {
            "run_id": self.run_id,
            "active_goal_id": self.active_goal_id,
            "frontier": list(self.frontier),
            "thresholds": self.thresholds.to_dict(),
            "active_goal": active.to_dict() if active else None,
            "stop_evaluation": stop_evaluation.to_dict() if stop_evaluation else None,
        }


__all__ = ["GoalLoopEngine"]
