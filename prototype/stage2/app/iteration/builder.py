from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from prototype.stage2.app.reporting import ReportItem, coerce_progress_snapshot, coerce_run_report

from .models import (
    ComparisonMetricRecord,
    FailureClusterChangeRecord,
    FailureClusterRecord,
    IterationArtifacts,
    IterationBuildInput,
    IterationComparisonRecord,
    IterationSummary,
    NextRoundDecisionRecord,
    PromotionCandidateRecord,
    RoundExecutionInputRecord,
    RetryAction,
    RetryPlanRecord,
    StopConditionRecord,
    StopDecisionRecord,
)


FAILURE_STATUSES = {"failed", "error", "blocked", "timeout"}
SUCCESS_STATUSES = {"completed", "success", "passed", "verified"}
MANUAL_REVIEW_STATUSES = {"unknown", "manual_review_needed"}


def build_iteration_outputs(
    run_report: Any = None,
    status_snapshot: Any = None,
    attempts: Sequence[Any] | None = None,
    previous_iteration: Any = None,
    max_attempts: int | None = None,
    round_input: Any = None,
) -> IterationArtifacts:
    payload = IterationBuildInput(
        run_report=run_report,
        status_snapshot=status_snapshot,
        attempts=list(attempts or []),
        previous_iteration=previous_iteration,
        max_attempts=max_attempts,
        round_input=round_input,
    )
    return _IterationBuilder(payload).build()


class _IterationBuilder:
    def __init__(self, payload: IterationBuildInput) -> None:
        self.report = coerce_run_report(payload.run_report or {})
        self.snapshot = coerce_progress_snapshot(
            payload.status_snapshot or {"run_id": self.report.summary.run_id}
        )
        self.attempts = [_normalize_attempt(item) for item in payload.attempts]
        self.previous_iteration = _normalize_previous_iteration(payload.previous_iteration)
        self.max_attempts = payload.max_attempts
        self.round_input = _normalize_round_input(payload.round_input)

    def build(self) -> IterationArtifacts:
        failure_clusters = self._build_failure_clusters()
        retry_plan = self._build_retry_plan(failure_clusters)
        promotion_candidates = self._build_promotion_candidates(failure_clusters)
        comparison = self._build_iteration_comparison(
            failure_clusters=failure_clusters,
            retry_plan=retry_plan,
            promotion_candidates=promotion_candidates,
        )
        stop_conditions = self._build_stop_decision(
            failure_clusters=failure_clusters,
            comparison=comparison,
        )
        next_round_decision = self._build_next_round_decision(
            failure_clusters=failure_clusters,
            retry_plan=retry_plan,
            comparison=comparison,
            stop_conditions=stop_conditions,
        )
        summary = IterationSummary(
            run_id=self._run_id,
            run_status=self._run_status,
            outcome=self._derive_outcome(failure_clusters, stop_conditions),
            failure_cluster_count=len(failure_clusters),
            retry_action_count=len(retry_plan.actions),
            promotion_candidate_count=len(promotion_candidates),
            stop_status=stop_conditions.status,
            comparison_status=comparison.status,
            comparison_outcome=comparison.improvement_judgement,
            next_round_status=next_round_decision.status,
            next_round=next_round_decision.next_round,
            next_round_should_start=next_round_decision.should_start_next_round,
            triggered_stop_conditions=list(stop_conditions.triggered_conditions),
            notes=self._build_summary_notes(
                failure_clusters=failure_clusters,
                retry_plan=retry_plan,
                promotion_candidates=promotion_candidates,
                comparison=comparison,
                stop_conditions=stop_conditions,
                next_round_decision=next_round_decision,
            ),
        )
        return IterationArtifacts(
            summary=summary,
            round_input=self.round_input or None,
            failure_clusters=failure_clusters,
            retry_plan=retry_plan,
            promotion_candidates=promotion_candidates,
            stop_conditions=stop_conditions,
            iteration_comparison=comparison,
            next_round_decision=next_round_decision,
        )

    @property
    def _run_id(self) -> str:
        return self.report.summary.run_id or self.snapshot.run_id

    @property
    def _run_status(self) -> str:
        return self.report.summary.status or self.snapshot.status or "unknown"

    def _derive_outcome(
        self,
        failure_clusters: list[FailureClusterRecord],
        stop_conditions: StopDecisionRecord,
    ) -> str:
        if stop_conditions.should_stop and "goal_completed" in stop_conditions.triggered_conditions:
            return "goal_completed"
        if stop_conditions.should_stop and stop_conditions.triggered_conditions:
            return "stopped"
        if self._is_success_run() and not failure_clusters:
            return "success_only"
        if failure_clusters:
            return "needs_retry"
        return "no_signals"

    def _build_summary_notes(
        self,
        *,
        failure_clusters: list[FailureClusterRecord],
        retry_plan: RetryPlanRecord,
        promotion_candidates: list[PromotionCandidateRecord],
        comparison: IterationComparisonRecord,
        stop_conditions: StopDecisionRecord,
        next_round_decision: NextRoundDecisionRecord,
    ) -> list[str]:
        notes: list[str] = []
        if self._is_success_run():
            notes.append("Run completed without blocking signals in the iteration builder.")
        if failure_clusters:
            notes.append(f"Detected {len(failure_clusters)} failure clusters for next-round planning.")
        elif not self._is_success_run():
            notes.append("No explicit failure cluster was derived from the current inputs.")
        if promotion_candidates:
            notes.append(
                f"Selected {len(promotion_candidates)} promotion candidates from successful evidence."
            )
        if retry_plan.status == "no_retry_needed":
            notes.append("Retry plan intentionally left empty.")
        if comparison.status == "no_previous_iteration":
            notes.append("Iteration comparison skipped because no previous iteration baseline was available.")
        elif comparison.summary:
            notes.append(comparison.summary)
        if stop_conditions.triggered_conditions:
            notes.append(
                "Triggered stop conditions: " + ", ".join(stop_conditions.triggered_conditions)
            )
        elif stop_conditions.status == "needs_review":
            notes.append("Stop decision remains conservative because one or more conditions need manual review.")
        if next_round_decision.status == "scheduled":
            notes.append(
                f"Next round {next_round_decision.next_round} was scheduled for {next_round_decision.target_stage or 'retry'}."
            )
        elif next_round_decision.primary_reason:
            notes.append(next_round_decision.primary_reason)
        return notes

    def _build_failure_clusters(self) -> list[FailureClusterRecord]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for signal in self._collect_failure_signals():
            cluster_key = (
                signal["category"],
                signal.get("stage") or "unknown",
            )
            grouped[cluster_key].append(signal)

        clusters: list[FailureClusterRecord] = []
        for index, ((category, stage), signals) in enumerate(grouped.items(), start=1):
            related_attempts = _sorted_unique(
                signal.get("attempt_id") for signal in signals if signal.get("attempt_id")
            )
            related_items = _sorted_unique(
                signal.get("item_id") for signal in signals if signal.get("item_id")
            )
            root_cause_hint = _first_non_empty(
                signal.get("root_cause_hint") for signal in signals
            )
            clusters.append(
                FailureClusterRecord(
                    cluster_id=f"cluster-{index:03d}",
                    category=category,
                    status="open",
                    stage=stage if stage != "unknown" else None,
                    root_cause_hint=root_cause_hint if root_cause_hint != "unknown" else None,
                    summary=_summarize_cluster(category, stage, signals),
                    signal_count=len(signals),
                    related_attempts=related_attempts,
                    related_items=related_items,
                    recommendation=_recommendation_for_category(category, stage),
                    action_level=_action_level_for_category(category),
                    evidence=[_signal_evidence(signal) for signal in signals[:5]],
                )
            )
        clusters.sort(key=lambda item: (-item.signal_count, item.category, item.cluster_id))
        return clusters

    def _collect_failure_signals(self) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []

        for item in self.report.failure_items:
            signal = _failure_signal_from_report_item(item)
            if signal:
                signals.append(signal)

        for cluster in self.report.failure_clusters:
            signals.append(
                {
                    "source": "report_cluster",
                    "item_id": cluster.cluster_id,
                    "title": cluster.summary or cluster.category,
                    "category": _classify_failure(
                        cluster.category,
                        cluster.root_cause,
                        cluster.summary,
                    ),
                    "stage": cluster.action_level or self.snapshot.stage,
                    "root_cause_hint": _root_cause_hint(cluster.root_cause, cluster.summary),
                    "message": cluster.recommendation or cluster.summary,
                }
            )

        for attempt in self.attempts:
            if attempt["status"] in FAILURE_STATUSES:
                classification_payload = _to_mapping(attempt.get("classification_payload"))
                signals.append(
                    {
                        "source": "attempt",
                        "attempt_id": attempt["attempt_id"],
                        "item_id": attempt["attempt_id"],
                        "title": attempt["title"],
                        "category": _classify_failure(
                            classification_payload.get("category"),
                            attempt["classification"],
                            attempt["error_type"],
                            attempt["message"],
                        ),
                        "stage": attempt["stage"] or self.snapshot.stage,
                        "root_cause_hint": _root_cause_hint(
                            classification_payload.get("reason"),
                            attempt["error_type"],
                            attempt["message"],
                        ),
                        "message": attempt["message"],
                        "classification_payload": classification_payload,
                    }
                )

        if self.snapshot.status in FAILURE_STATUSES:
            signals.append(
                {
                    "source": "status_snapshot",
                    "title": self.snapshot.step or self.snapshot.stage or "run status",
                    "category": _classify_failure(
                        self.snapshot.stage,
                        self.snapshot.blocked_reason,
                        self.snapshot.next_action,
                    ),
                    "stage": self.snapshot.stage,
                    "root_cause_hint": _root_cause_hint(
                        self.snapshot.blocked_reason,
                        self.snapshot.next_action,
                    ),
                    "message": self.snapshot.blocked_reason or self.snapshot.next_action,
                }
            )
        return signals

    def _build_retry_plan(self, clusters: list[FailureClusterRecord]) -> RetryPlanRecord:
        if not clusters:
            return RetryPlanRecord(
                run_id=self._run_id,
                status="no_retry_needed",
                next_round=None,
                goal="Preserve the successful path and continue normal reporting.",
                stop_reason=self.report.summary.stop_reason,
                actions=[],
                notes=["No open failure cluster requires a retry action."],
            )

        current_round = self._current_round_index
        actions: list[RetryAction] = []
        for index, cluster in enumerate(clusters, start=1):
            actions.append(
                RetryAction(
                    action_id=f"retry-{index:03d}",
                    cluster_id=cluster.cluster_id,
                    title=_retry_title(cluster),
                    priority=_priority_for_cluster(cluster),
                    stage=cluster.stage,
                    owner=_retry_owner_for_cluster(cluster),
                    strategy=_strategy_for_cluster(cluster),
                    reason=cluster.summary or cluster.recommendation,
                    expected_outcome=_expected_outcome(cluster),
                    execution_hints=_execution_hints_for_cluster(cluster),
                )
            )

        return RetryPlanRecord(
            run_id=self._run_id,
            status="planned",
            next_round=current_round + 1,
            goal="Resolve the open failure clusters and rerun the affected path.",
            stop_reason=self.report.summary.stop_reason or self.snapshot.blocked_reason,
            actions=actions,
            notes=[
                "Retry plan was derived from the latest run report, status snapshot, and attempt outcomes.",
            ],
        )

    def _build_promotion_candidates(
        self,
        failure_clusters: list[FailureClusterRecord],
    ) -> list[PromotionCandidateRecord]:
        candidates: list[PromotionCandidateRecord] = []

        for item in self.report.success_items:
            evidence = _item_evidence(item)
            review_payload = self._promotion_review_payload(
                promotion_level="verified_success",
                evidence=evidence,
                has_open_failure_clusters=bool(failure_clusters),
            )
            candidates.append(
                PromotionCandidateRecord(
                    candidate_id=item.item_id or f"promotion-success-{len(candidates) + 1:03d}",
                    source="run_report.success_items",
                    title=item.name,
                    promotion_level="verified_success",
                    status="candidate",
                    reason=item.summary or "Successful item recorded in run report.",
                    review_status=review_payload["review_status"],
                    promotion_target=review_payload["promotion_target"],
                    promotion_recommendation=review_payload["promotion_recommendation"],
                    needs_manual_review=review_payload["needs_manual_review"],
                    evidence_requirements=review_payload["evidence_requirements"],
                    missing_evidence=review_payload["missing_evidence"],
                    evidence=evidence,
                )
            )

        for attempt in self.attempts:
            if attempt["status"] in SUCCESS_STATUSES:
                evidence = [
                    {
                        "attempt_id": attempt["attempt_id"],
                        "stage": attempt["stage"],
                        "status": attempt["status"],
                    }
                ]
                review_payload = self._promotion_review_payload(
                    promotion_level="retry_baseline",
                    evidence=evidence,
                    has_open_failure_clusters=bool(failure_clusters),
                )
                candidates.append(
                    PromotionCandidateRecord(
                        candidate_id=attempt["attempt_id"] or f"promotion-attempt-{len(candidates) + 1:03d}",
                        source="attempts",
                        title=attempt["title"],
                        promotion_level="retry_baseline",
                        status="candidate",
                        reason=attempt["message"] or "Attempt completed successfully.",
                        review_status=review_payload["review_status"],
                        promotion_target=review_payload["promotion_target"],
                        promotion_recommendation=review_payload["promotion_recommendation"],
                        needs_manual_review=review_payload["needs_manual_review"],
                        evidence_requirements=review_payload["evidence_requirements"],
                        missing_evidence=review_payload["missing_evidence"],
                        evidence=evidence,
                    )
                )

        if self._is_success_run() and not candidates:
            evidence = [{"run_id": self._run_id, "status": self._run_status}]
            review_payload = self._promotion_review_payload(
                promotion_level="run_summary",
                evidence=evidence,
                has_open_failure_clusters=bool(failure_clusters),
            )
            candidates.append(
                PromotionCandidateRecord(
                    candidate_id=f"{self._run_id}__promotion_run_summary",
                    source="run_status",
                    title="Successful run summary",
                    promotion_level="run_summary",
                    status="candidate",
                    reason="Run finished successfully and can be retained as a reference baseline.",
                    review_status=review_payload["review_status"],
                    promotion_target=review_payload["promotion_target"],
                    promotion_recommendation=review_payload["promotion_recommendation"],
                    needs_manual_review=review_payload["needs_manual_review"],
                    evidence_requirements=review_payload["evidence_requirements"],
                    missing_evidence=review_payload["missing_evidence"],
                    evidence=evidence,
                )
            )

        if failure_clusters:
            return candidates
        return candidates

    def _promotion_review_payload(
        self,
        *,
        promotion_level: str,
        evidence: list[dict[str, Any]],
        has_open_failure_clusters: bool,
    ) -> dict[str, Any]:
        promotion_target = _promotion_target_for_level(promotion_level)
        promotion_recommendation = _promotion_recommendation_for_level(promotion_level)
        evidence_requirements = _promotion_evidence_requirements(
            promotion_level=promotion_level,
            promotion_target=promotion_target,
        )
        missing_evidence = list(_missing_promotion_evidence(evidence, evidence_requirements))
        if has_open_failure_clusters:
            missing_evidence.append("rerun after open failure clusters are cleared")
        review_status = "ready_for_review"
        if not evidence:
            review_status = "needs_evidence"
        elif has_open_failure_clusters:
            review_status = "needs_followup_validation"
        return {
            "review_status": review_status,
            "promotion_target": promotion_target,
            "promotion_recommendation": promotion_recommendation,
            "needs_manual_review": True,
            "evidence_requirements": _sorted_unique(evidence_requirements),
            "missing_evidence": _sorted_unique(missing_evidence),
        }

    def _build_iteration_comparison(
        self,
        *,
        failure_clusters: list[FailureClusterRecord],
        retry_plan: RetryPlanRecord,
        promotion_candidates: list[PromotionCandidateRecord],
    ) -> IterationComparisonRecord:
        current_state = self._current_iteration_state(
            failure_clusters=failure_clusters,
            retry_plan=retry_plan,
            promotion_candidates=promotion_candidates,
        )
        previous_state = self.previous_iteration
        if not previous_state:
            return IterationComparisonRecord(
                current_run_id=self._run_id,
                previous_run_id=None,
                status="no_previous_iteration",
                improvement_judgement="unknown",
                summary="No previous iteration baseline was available.",
                notes=["Comparison intentionally skipped because no previous iteration artifacts were found."],
            )

        previous_run_id = _text(previous_state.get("run_id"))
        previous_metrics = previous_state.get("metrics", {})
        current_metrics = current_state["metrics"]
        metric_specs = [
            ("failure_cluster_count", "Failure Cluster Count"),
            ("failure_signal_count", "Failure Signal Count"),
            ("retry_action_count", "Retry Action Count"),
            ("promotion_candidate_count", "Promotion Candidate Count"),
            ("successful_attempt_count", "Successful Attempt Count"),
            ("failed_attempt_count", "Failed Attempt Count"),
            ("run_status", "Run Status"),
        ]
        metrics: list[ComparisonMetricRecord] = []
        for metric_id, label in metric_specs:
            current_value = current_metrics.get(metric_id)
            previous_value = previous_metrics.get(metric_id)
            delta = _metric_delta(current_value, previous_value)
            trend = _metric_trend(current_value, previous_value)
            metrics.append(
                ComparisonMetricRecord(
                    metric_id=metric_id,
                    label=label,
                    current_value=current_value,
                    previous_value=previous_value,
                    delta=delta,
                    trend=trend,
                )
            )

        cluster_changes = self._build_cluster_changes(
            current_clusters=failure_clusters,
            previous_clusters=previous_state.get("failure_clusters", []),
        )
        improvement = _judge_improvement(
            current_status=self._run_status,
            previous_status=_text(previous_metrics.get("run_status")) or "unknown",
            cluster_changes=cluster_changes,
            metrics=metrics,
        )
        streak_before = _coerce_int(previous_state.get("no_improvement_streak_after")) or 0
        streak_after = streak_before + 1 if improvement == "no_improvement" else 0
        summary = _comparison_summary(improvement, cluster_changes, metrics)
        return IterationComparisonRecord(
            current_run_id=self._run_id,
            previous_run_id=previous_run_id,
            status="compared",
            improvement_judgement=improvement,
            summary=summary,
            metrics=metrics,
            cluster_changes=cluster_changes,
            no_improvement_streak_before=streak_before,
            no_improvement_streak_after=streak_after,
            notes=[
                "Comparison uses conservative matching on category + stage.",
                "Unknown or partially missing metrics do not force an improvement judgement.",
            ],
        )

    def _current_iteration_state(
        self,
        *,
        failure_clusters: list[FailureClusterRecord],
        retry_plan: RetryPlanRecord,
        promotion_candidates: list[PromotionCandidateRecord],
    ) -> dict[str, Any]:
        return {
            "run_id": self._run_id,
            "metrics": {
                "failure_cluster_count": len(failure_clusters),
                "failure_signal_count": sum(cluster.signal_count for cluster in failure_clusters),
                "retry_action_count": len(retry_plan.actions),
                "promotion_candidate_count": len(promotion_candidates),
                "successful_attempt_count": sum(
                    1 for item in self.attempts if item["status"] in SUCCESS_STATUSES
                ),
                "failed_attempt_count": sum(
                    1 for item in self.attempts if item["status"] in FAILURE_STATUSES
                ),
                "run_status": self._run_status,
                "current_round": self._current_round_index,
                "attempt_count": len(self.attempts),
            },
        }

    def _build_cluster_changes(
        self,
        *,
        current_clusters: list[FailureClusterRecord],
        previous_clusters: list[dict[str, Any]],
    ) -> list[FailureClusterChangeRecord]:
        current_index = _index_clusters(current_clusters)
        previous_index = _index_clusters(previous_clusters)
        keys = sorted(set(current_index) | set(previous_index))
        results: list[FailureClusterChangeRecord] = []
        for key in keys:
            current_items = current_index.get(key, [])
            previous_items = previous_index.get(key, [])
            current_signal_count = sum(item.signal_count for item in current_items)
            previous_signal_count = sum(item.signal_count for item in previous_items)
            if previous_items and not current_items:
                status = "resolved"
            elif current_items and not previous_items:
                status = "new"
            elif current_signal_count > previous_signal_count:
                status = "regressed"
            elif current_signal_count < previous_signal_count:
                status = "improved"
            else:
                status = "unchanged"

            sample = current_items[0] if current_items else previous_items[0]
            results.append(
                FailureClusterChangeRecord(
                    cluster_key=key,
                    category=sample.category,
                    status=status,
                    stage=sample.stage,
                    previous_signal_count=previous_signal_count,
                    current_signal_count=current_signal_count,
                    signal_delta=current_signal_count - previous_signal_count,
                    previous_cluster_ids=[item.cluster_id for item in previous_items],
                    current_cluster_ids=[item.cluster_id for item in current_items],
                    summary=_cluster_change_summary(
                        key=key,
                        status=status,
                        previous_signal_count=previous_signal_count,
                        current_signal_count=current_signal_count,
                    ),
                )
            )
        return results

    def _build_stop_decision(
        self,
        *,
        failure_clusters: list[FailureClusterRecord],
        comparison: IterationComparisonRecord,
    ) -> StopDecisionRecord:
        conditions: list[StopConditionRecord] = []

        goal_completed = self._condition_goal_completed(failure_clusters)
        conditions.append(goal_completed)

        no_improvement = self._condition_no_improvement(comparison)
        conditions.append(no_improvement)

        safety_boundary = self._condition_safety_boundary(failure_clusters)
        conditions.append(safety_boundary)

        manual_takeover = self._condition_manual_takeover(failure_clusters)
        conditions.append(manual_takeover)

        resource_budget = self._condition_resource_budget()
        conditions.append(resource_budget)

        triggered = [
            item.condition_type for item in conditions if item.status == "hit" and item.stop is True
        ]
        has_review = any(item.status in MANUAL_REVIEW_STATUSES for item in conditions)
        should_stop: bool | None
        status: str
        if triggered:
            should_stop = True
            status = "stop"
        elif has_review:
            should_stop = None
            status = "needs_review"
        else:
            should_stop = False
            status = "continue"

        return StopDecisionRecord(
            run_id=self._run_id,
            status=status,
            should_stop=should_stop,
            primary_reason=triggered[0] if triggered else None,
            triggered_conditions=triggered,
            no_improvement_streak=comparison.no_improvement_streak_after,
            conditions=conditions,
            notes=[
                "Stop decisions stay conservative and prefer structured iteration signals over free-text inference.",
            ],
        )

    def _build_next_round_decision(
        self,
        *,
        failure_clusters: list[FailureClusterRecord],
        retry_plan: RetryPlanRecord,
        comparison: IterationComparisonRecord,
        stop_conditions: StopDecisionRecord,
    ) -> NextRoundDecisionRecord:
        current_round = self._current_round_index
        next_round = retry_plan.next_round if retry_plan else (current_round + 1 if current_round else None)
        cluster_changes = comparison.cluster_changes if comparison else []
        new_cluster_ids = _collect_cluster_ids(cluster_changes, {"new"})
        repeated_no_gain_cluster_ids = _collect_cluster_ids(cluster_changes, {"unchanged"})
        regressed_cluster_ids = _collect_cluster_ids(cluster_changes, {"regressed"})
        resolved_cluster_ids = _collect_cluster_ids(cluster_changes, {"resolved", "improved"})
        scheduled_actions = retry_plan.actions if retry_plan else []
        scheduled_action_ids = [action.action_id for action in scheduled_actions]
        scheduled_cluster_ids = [action.cluster_id for action in scheduled_actions]
        deferred_cluster_ids = [
            cluster.cluster_id
            for cluster in failure_clusters
            if cluster.cluster_id not in scheduled_cluster_ids
        ]

        remaining_budget = (
            self.max_attempts - current_round
            if self.max_attempts is not None and current_round is not None
            else None
        )
        budget_exhausted = remaining_budget is not None and remaining_budget <= 0
        no_actions = not scheduled_actions

        if stop_conditions.should_stop is True:
            status = "stopped"
            should_start_next_round = False
            primary_reason = _next_round_stop_reason(stop_conditions)
            target_stage = None
        elif stop_conditions.should_stop is None:
            status = "needs_review"
            should_start_next_round = None
            primary_reason = "Stop decision requires manual review before scheduling the next round."
            target_stage = None
        elif budget_exhausted:
            status = "budget_exhausted"
            should_start_next_round = False
            primary_reason = "No remaining attempt budget is available for another round."
            target_stage = None
        elif no_actions and failure_clusters:
            status = "needs_review"
            should_start_next_round = None
            primary_reason = "Open failure clusters remain, but no retry actions were produced."
            target_stage = None
        elif no_actions:
            status = "no_retry_needed"
            should_start_next_round = False
            primary_reason = "No open failure cluster requires another round."
            target_stage = None
        else:
            status = "scheduled"
            should_start_next_round = True
            primary_reason = "Open failure clusters were converted into next-round retry actions."
            target_stage = _target_stage_for_actions(scheduled_actions)

        notes = [
            "This decision is the main iteration handoff for orchestration to schedule the next round conservatively.",
        ]
        if comparison.status == "no_previous_iteration":
            notes.append("No previous iteration baseline was available, so new vs repeated failure classification is partial.")
        if regressed_cluster_ids:
            notes.append(f"Detected {len(regressed_cluster_ids)} regressed failure cluster(s).")
        if repeated_no_gain_cluster_ids:
            notes.append(f"Detected {len(repeated_no_gain_cluster_ids)} repeated no-gain failure cluster(s).")

        return NextRoundDecisionRecord(
            run_id=self._run_id,
            status=status,
            should_start_next_round=should_start_next_round,
            current_round=current_round,
            next_round=next_round,
            max_attempts=self.max_attempts,
            remaining_attempt_budget=remaining_budget,
            target_stage=target_stage,
            primary_reason=primary_reason,
            stop_reason=stop_conditions.primary_reason,
            improvement_judgement=comparison.improvement_judgement,
            new_failure_cluster_count=len(new_cluster_ids),
            repeated_no_gain_cluster_count=len(repeated_no_gain_cluster_ids),
            regressed_cluster_count=len(regressed_cluster_ids),
            resolved_cluster_count=len(resolved_cluster_ids),
            scheduled_cluster_ids=scheduled_cluster_ids,
            scheduled_action_ids=scheduled_action_ids,
            deferred_cluster_ids=deferred_cluster_ids,
            triggered_stop_conditions=list(stop_conditions.triggered_conditions),
            notes=notes,
        )

    def _condition_goal_completed(
        self,
        failure_clusters: list[FailureClusterRecord],
    ) -> StopConditionRecord:
        if not self._run_status:
            return StopConditionRecord(
                condition_id="stop-goal-completed",
                condition_type="goal_completed",
                status="unknown",
                summary="Run status is unavailable.",
                stop=None,
            )
        hit = self._is_success_run() and not failure_clusters
        return StopConditionRecord(
            condition_id="stop-goal-completed",
            condition_type="goal_completed",
            status="hit" if hit else "not_hit",
            summary=(
                "Current run completed successfully without open failure clusters."
                if hit
                else "Current run still has open failure signals or is not successful."
            ),
            stop=True if hit else False,
            evidence=[
                {
                    "run_status": self._run_status,
                    "failure_cluster_count": len(failure_clusters),
                }
            ],
        )

    def _condition_no_improvement(
        self,
        comparison: IterationComparisonRecord,
    ) -> StopConditionRecord:
        if comparison.status == "no_previous_iteration":
            return StopConditionRecord(
                condition_id="stop-no-improvement",
                condition_type="no_improvement",
                status="unknown",
                summary="No previous iteration baseline was available.",
                stop=None,
            )
        if comparison.improvement_judgement == "no_improvement":
            streak = comparison.no_improvement_streak_after
            if streak >= 2:
                return StopConditionRecord(
                    condition_id="stop-no-improvement",
                    condition_type="no_improvement",
                    status="hit",
                    summary="No improvement persisted across consecutive iterations.",
                    stop=True,
                    evidence=[{"no_improvement_streak": streak}],
                )
            return StopConditionRecord(
                condition_id="stop-no-improvement",
                condition_type="no_improvement",
                status="manual_review_needed",
                summary="No improvement detected, but the streak is still below the automatic stop threshold.",
                stop=None,
                evidence=[{"no_improvement_streak": streak}],
            )
        if comparison.improvement_judgement in {"improved", "regressed"}:
            return StopConditionRecord(
                condition_id="stop-no-improvement",
                condition_type="no_improvement",
                status="not_hit",
                summary="The latest comparison did not indicate a flat no-improvement outcome.",
                stop=False,
                evidence=[{"improvement_judgement": comparison.improvement_judgement}],
            )
        return StopConditionRecord(
            condition_id="stop-no-improvement",
            condition_type="no_improvement",
            status="unknown",
            summary="Comparison outcome was not decisive enough to determine no-improvement.",
            stop=None,
            evidence=[{"comparison_status": comparison.status}],
        )

    def _condition_resource_budget(self) -> StopConditionRecord:
        if self.max_attempts is not None:
            current_round = self._current_round_index
            remaining_budget = self.max_attempts - current_round
            if remaining_budget <= 0:
                return StopConditionRecord(
                    condition_id="stop-resource-budget",
                    condition_type="resource_budget_exhausted",
                    status="hit",
                    summary="The configured round or attempt budget has been exhausted.",
                    stop=True,
                    evidence=[
                        {
                            "max_attempts": self.max_attempts,
                            "current_round": current_round,
                            "remaining_attempt_budget": remaining_budget,
                        }
                    ],
                )
            return StopConditionRecord(
                condition_id="stop-resource-budget",
                condition_type="resource_budget_exhausted",
                status="not_hit",
                summary="Remaining round or attempt budget is still available.",
                stop=False,
                evidence=[
                    {
                        "max_attempts": self.max_attempts,
                        "current_round": current_round,
                        "remaining_attempt_budget": remaining_budget,
                    }
                ],
            )

        return self._condition_text_signal(
            condition_id="stop-resource-budget",
            condition_type="resource_budget_exhausted",
            keywords=(
                "budget exhausted",
                "quota exhausted",
                "token budget exhausted",
                "retry budget exhausted",
                "attempts exhausted",
                "max attempts reached",
                "round limit reached",
                "round_limit",
                "attempts_exhausted",
                "max_attempts",
                "达到最大尝试次数",
                "超过最大尝试次数",
                "预算耗尽",
                "配额耗尽",
                "轮次上限",
                "重试次数耗尽",
                "预算",
            ),
        )

    def _condition_safety_boundary(
        self,
        failure_clusters: list[FailureClusterRecord],
    ) -> StopConditionRecord:
        boundary_clusters = [
            cluster
            for cluster in failure_clusters
            if cluster.category in {"permission", "policy", "preflight"}
        ]
        if boundary_clusters:
            return StopConditionRecord(
                condition_id="stop-safety-boundary",
                condition_type="safety_boundary",
                status="hit",
                summary="A structured safety or permission boundary was detected in the current failure clusters.",
                stop=True,
                evidence=[
                    {
                        "cluster_ids": [cluster.cluster_id for cluster in boundary_clusters],
                        "categories": [cluster.category for cluster in boundary_clusters],
                    }
                ],
            )
        return self._condition_text_signal(
            condition_id="stop-safety-boundary",
            condition_type="safety_boundary",
            keywords=(
                "safety boundary",
                "security boundary",
                "policy blocked",
                "policy block",
                "forbidden by policy",
                "blocked by policy",
                "whitelist",
                "white list",
                "allowlist",
                "安全边界",
                "策略拦截",
                "白名单",
            ),
        )

    def _condition_manual_takeover(
        self,
        failure_clusters: list[FailureClusterRecord],
    ) -> StopConditionRecord:
        manual_clusters = [
            cluster
            for cluster in failure_clusters
            if cluster.category in {"workflow_branch", "backend_data"}
            or cluster.recommendation and "human" in cluster.recommendation.lower()
        ]
        if manual_clusters:
            return StopConditionRecord(
                condition_id="stop-manual-takeover",
                condition_type="manual_takeover",
                status="manual_review_needed",
                summary="Structured failure clusters indicate that a human takeover or review is likely required.",
                stop=None,
                evidence=[
                    {
                        "cluster_ids": [cluster.cluster_id for cluster in manual_clusters],
                        "categories": [cluster.category for cluster in manual_clusters],
                    }
                ],
            )
        return self._condition_text_signal(
            condition_id="stop-manual-takeover",
            condition_type="manual_takeover",
            keywords=(
                "manual takeover",
                "human takeover",
                "manual handoff",
                "handoff to human",
                "needs human review",
                "human review required",
                "takeover",
                "handoff",
                "人工接管",
                "人工介入",
                "人工处理",
                "人工审核",
                "转人工",
            ),
            default_status="not_hit",
        )

    def _condition_text_signal(
        self,
        *,
        condition_id: str,
        condition_type: str,
        keywords: Sequence[str],
        default_status: str = "unknown",
    ) -> StopConditionRecord:
        matches = _find_keyword_matches(self._condition_texts(), keywords)
        if matches:
            return StopConditionRecord(
                condition_id=condition_id,
                condition_type=condition_type,
                status="hit",
                summary=f"Explicit {condition_type} signal was found in current run artifacts.",
                stop=True,
                evidence=[{"matches": matches[:10]}],
            )
        return StopConditionRecord(
            condition_id=condition_id,
            condition_type=condition_type,
            status=default_status,
            summary=f"No explicit {condition_type} signal was found in current inputs.",
            stop=False if default_status == "not_hit" else None,
        )

    def _condition_texts(self) -> list[str]:
        values: list[str] = []
        values.extend(_flatten_text([self.report.summary.stop_reason, self.report.summary.next_action]))
        values.extend(_flatten_text([self.snapshot.blocked_reason, self.snapshot.next_action]))
        for item in self.report.failure_items:
            values.extend(_flatten_text([item.name, item.summary, item.notes, item.tags]))
        for attempt in self.attempts:
            values.extend(
                _flatten_text(
                    [
                        attempt["title"],
                        attempt["message"],
                        attempt["classification"],
                        attempt["error_type"],
                    ]
                )
            )
        return values

    def _is_success_run(self) -> bool:
        status = (self._run_status or "").lower()
        return status in SUCCESS_STATUSES

    @property
    def _current_round_index(self) -> int:
        return (
            _coerce_int(getattr(self.round_input, "round_index", None))
            or _coerce_int(self.report.summary.extra.get("orchestration_round"))
            or _coerce_int(self.report.summary.current_round)
            or _coerce_round_index(self.snapshot.current_round)
            or 0
        )


def _normalize_round_input(value: Any) -> RoundExecutionInputRecord | None:
    data = _to_mapping(value)
    if not data:
        return None
    return RoundExecutionInputRecord(
        orchestration_stream_id=_text(data.get("orchestration_stream_id")),
        template_name=_text(data.get("template_name") or data.get("template")),
        model_name=_text(data.get("model_name") or data.get("model")),
        project_name=_text(data.get("project_name") or data.get("project")),
        round_index=_coerce_int(data.get("round_index") or data.get("round")),
        max_rounds=_coerce_int(data.get("max_rounds")),
        previous_run_id=_text(data.get("previous_run_id")),
        previous_run_dir=_text(data.get("previous_run_dir")),
        retry_run_dir=_text(data.get("retry_run_dir")),
        target_stage=_text(data.get("target_stage")),
        goal=_text(data.get("goal")),
        source_decision_status=_text(data.get("source_decision_status")),
        source_decision_reason=_text(data.get("source_decision_reason")),
        scheduled_cluster_ids=_string_list(data.get("scheduled_cluster_ids")),
        scheduled_action_ids=_string_list(data.get("scheduled_action_ids")),
        execution_hints=_to_mapping(data.get("execution_hints")),
        notes=_string_list(data.get("notes")),
    )


def _normalize_previous_iteration(value: Any) -> dict[str, Any]:
    data = _to_mapping(value)
    if not data:
        return {}

    comparison = _to_mapping(data.get("iteration_comparison"))
    comparison_streak = _coerce_int(comparison.get("no_improvement_streak_after"))
    comparison_metrics = _metrics_from_comparison(comparison.get("metrics"))

    failure_clusters = data.get("failure_clusters")
    if not failure_clusters and isinstance(data.get("clusters"), list):
        failure_clusters = data.get("clusters")

    retry_plan = _to_mapping(data.get("retry_plan"))
    promotion_candidates = data.get("promotion_candidates")
    if not promotion_candidates and isinstance(data.get("candidates"), list):
        promotion_candidates = data.get("candidates")

    summary = _to_mapping(data.get("summary"))
    metrics = {
        "failure_cluster_count": comparison_metrics.get("failure_cluster_count")
        or (_coerce_int(summary.get("failure_cluster_count")) if summary else None),
        "failure_signal_count": comparison_metrics.get("failure_signal_count"),
        "retry_action_count": comparison_metrics.get("retry_action_count"),
        "promotion_candidate_count": comparison_metrics.get("promotion_candidate_count"),
        "successful_attempt_count": comparison_metrics.get("successful_attempt_count")
        or _count_from_summary(summary, "successful_attempt_count"),
        "failed_attempt_count": comparison_metrics.get("failed_attempt_count")
        or _count_from_summary(summary, "failed_attempt_count"),
        "current_round": comparison_metrics.get("current_round")
        or _coerce_int(summary.get("next_round")),
        "attempt_count": comparison_metrics.get("attempt_count"),
        "run_status": comparison_metrics.get("run_status")
        or _text(summary.get("run_status") or summary.get("status")),
    }

    normalized_clusters = _normalize_cluster_records(failure_clusters)
    metrics["failure_cluster_count"] = metrics["failure_cluster_count"] or len(normalized_clusters)
    metrics["failure_signal_count"] = sum(item.signal_count for item in normalized_clusters)

    actions = retry_plan.get("actions")
    metrics["retry_action_count"] = len(actions) if isinstance(actions, list) else _count_from_summary(summary, "retry_action_count") or 0

    normalized_candidates = _normalize_promotion_candidates(promotion_candidates)
    metrics["promotion_candidate_count"] = (
        _count_from_summary(summary, "promotion_candidate_count") or len(normalized_candidates)
    )

    return {
        "run_id": _text(summary.get("run_id")) or _text(data.get("run_id")),
        "orchestration_stream_id": _text(data.get("orchestration_stream_id")),
        "template_name": _text(data.get("template_name") or summary.get("template_name")),
        "model_name": _text(data.get("model_name")),
        "project_name": _text(data.get("project_name") or summary.get("project_name")),
        "metrics": metrics,
        "failure_clusters": normalized_clusters,
        "no_improvement_streak_after": comparison_streak or 0,
    }


def _normalize_attempt(value: Any) -> dict[str, Any]:
    data = _to_mapping(value)
    if not data and isinstance(value, str):
        data = {"message": value}

    attempt_id = _text(data.get("attempt_id") or data.get("id") or data.get("key")) or "attempt"
    status = (_text(data.get("status") or data.get("result") or data.get("state")) or "unknown").lower()
    title = _text(data.get("title") or data.get("name") or data.get("step")) or attempt_id
    classification_payload = _to_mapping(data.get("classification"))
    classification = (
        _text(classification_payload.get("category"))
        or _text(data.get("classification") or data.get("category") or data.get("failure_type"))
    )
    error_type = (
        _text(classification_payload.get("reason"))
        or _text(data.get("error_type") or data.get("reason") or data.get("blocked_reason"))
    )
    stage = _text(data.get("stage") or data.get("phase"))
    message = (
        _text(classification_payload.get("reason"))
        or _text(data.get("message") or data.get("summary") or data.get("description"))
    )

    return {
        "attempt_id": attempt_id,
        "status": status,
        "title": title,
        "classification": classification,
        "classification_payload": classification_payload,
        "error_type": error_type,
        "stage": stage,
        "message": message,
    }


def _normalize_cluster_records(value: Any) -> list[FailureClusterRecord]:
    if not isinstance(value, list):
        return []
    results: list[FailureClusterRecord] = []
    for item in value:
        data = _to_mapping(item)
        if not data:
            continue
        results.append(
            FailureClusterRecord(
                cluster_id=_text(data.get("cluster_id") or data.get("id")) or "cluster",
                category=_text(data.get("category")) or "unknown",
                status=_text(data.get("status")) or "open",
                stage=_text(data.get("stage")),
                root_cause_hint=_text(data.get("root_cause_hint") or data.get("root_cause")),
                summary=_text(data.get("summary")),
                signal_count=_coerce_int(data.get("signal_count")) or 0,
                related_attempts=_string_list(data.get("related_attempts")),
                related_items=_string_list(data.get("related_items")),
                recommendation=_text(data.get("recommendation")),
                action_level=_text(data.get("action_level")),
                evidence=_coerce_evidence(data.get("evidence")),
            )
        )
    return results


def _normalize_promotion_candidates(value: Any) -> list[PromotionCandidateRecord]:
    if not isinstance(value, list):
        return []
    results: list[PromotionCandidateRecord] = []
    for item in value:
        data = _to_mapping(item)
        if not data:
            continue
        results.append(
            PromotionCandidateRecord(
                candidate_id=_text(data.get("candidate_id") or data.get("id")) or "candidate",
                source=_text(data.get("source")) or "unknown",
                title=_text(data.get("title") or data.get("name")) or "candidate",
                promotion_level=_text(data.get("promotion_level") or data.get("owner")) or "unknown",
                status=_text(data.get("status")) or "candidate",
                reason=_text(data.get("reason") or data.get("summary")),
                review_status=_text(data.get("review_status")),
                promotion_target=_text(data.get("promotion_target")),
                promotion_recommendation=_text(data.get("promotion_recommendation")),
                needs_manual_review=_coerce_bool(data.get("needs_manual_review")),
                evidence_requirements=_string_list(data.get("evidence_requirements")),
                missing_evidence=_string_list(data.get("missing_evidence")),
                evidence=_coerce_evidence(data.get("evidence")),
            )
        )
    return results


def _failure_signal_from_report_item(item: ReportItem) -> dict[str, Any] | None:
    status = (item.status or "").lower()
    if status and status not in FAILURE_STATUSES:
        return None
    category = _classify_failure(item.name, item.summary, item.tags)
    return {
        "source": "report_item",
        "item_id": item.item_id or item.name,
        "title": item.name,
        "category": category,
        "stage": item.owner or item.source,
        "root_cause_hint": _root_cause_hint(item.summary, item.notes),
        "message": item.summary,
    }


def _classify_failure(*parts: Any) -> str:
    text = " ".join(_flatten_text(parts)).lower()
    explicit = {
        "account_policy_block": "permission",
        "policy_blocked": "permission",
        "policy_review_required": "policy",
        "backend_update_primary_key_error": "backend_data",
        "front_validation_missing_commitment": "front_validation",
        "pending_payment_modify_mode": "workflow_branch",
        "preflight_capability_missing": "preflight",
        "preflight_capability_stale": "preflight",
        "preflight_capability_incompatible": "preflight",
        "environment_bootstrap_failure": "environment",
        "max_attempts_exhausted": "stability",
        "unknown_failure": "runtime",
    }
    for key, mapped in explicit.items():
        if key in text:
            return mapped
    if any(token in text for token in ("network", "http", "api", "socket", "dns")):
        return "network"
    if any(token in text for token in ("selector", "locator", "ui", "dom", "render")):
        return "ui"
    if any(token in text for token in ("assert", "expect", "mismatch", "diff", "verification")):
        return "verification"
    if any(token in text for token in ("schema", "field", "validation", "parse", "format")):
        return "data"
    if any(token in text for token in ("permission", "env", "browser", "cdp", "setup", "config")):
        return "environment"
    if any(token in text for token in ("timeout", "retry", "flaky", "unstable")):
        return "stability"
    if any(token in text for token in ("exception", "traceback", "crash", "error", "failed", "blocked")):
        return "runtime"
    return "unknown"


def _root_cause_hint(*parts: Any) -> str | None:
    for part in _flatten_text(parts):
        lowered = part.lower()
        if len(lowered) <= 96:
            return lowered
        return lowered[:93] + "..."
    return None


def _summarize_cluster(category: str, stage: str, signals: list[dict[str, Any]]) -> str:
    stage_label = stage if stage != "unknown" else "unknown stage"
    primary = signals[0].get("message") or signals[0].get("title") or category
    return f"{len(signals)} signal(s) around {category} in {stage_label}: {primary}"


def _recommendation_for_category(category: str, stage: str) -> str:
    if category == "permission":
        return "Stop automatic retry and request account, role, or organization confirmation before another round."
    if category == "policy":
        return "Do not auto-submit again until a human reviews the high-risk action policy for this project."
    if category == "preflight":
        return "Refresh the model capability probe and capability routing inputs before another run."
    if category == "front_validation":
        return "Inspect the visible validation errors and fill the missing required inputs before retrying."
    if category == "workflow_branch":
        return "Resume from the detected workflow branch instead of replaying the default new-application path."
    if category == "backend_data":
        return "Avoid blind retry; inspect server-side data prerequisites or modify-mode assumptions first."
    if category == "network":
        return "Re-run the affected step with network capture and confirm whether the dependency is transient."
    if category == "ui":
        return "Refresh selectors or UI readiness checks before the next verification attempt."
    if category == "verification":
        return "Inspect the assertion delta and tighten the expected output for the next round."
    if category == "data":
        return "Repair the structured input or parsing logic before retrying the flow."
    if category == "environment":
        return "Validate runtime configuration and browser connectivity before retrying."
    if category == "stability":
        return "Add a focused retry with stronger readiness or polling guards."
    if stage and stage != "unknown":
        return f"Retry the {stage} stage with additional diagnostics."
    return "Retry the affected path with one focused diagnostic action."


def _action_level_for_category(category: str) -> str:
    if category in {"permission", "workflow_branch"}:
        return "workflow"
    if category == "policy":
        return "policy"
    if category == "preflight":
        return "runtime"
    if category == "front_validation":
        return "logic"
    if category == "backend_data":
        return "runtime"
    if category in {"data", "verification"}:
        return "logic"
    if category in {"environment", "network"}:
        return "runtime"
    if category == "ui":
        return "workflow"
    if category == "stability":
        return "policy"
    return "unknown"


def _retry_title(cluster: FailureClusterRecord) -> str:
    if cluster.stage:
        return f"Retry {cluster.stage} for {cluster.category}"
    return f"Retry {cluster.category} failure cluster"


def _priority_for_cluster(cluster: FailureClusterRecord) -> str:
    if cluster.category in {"environment", "runtime", "network"}:
        return "high"
    if cluster.signal_count >= 3:
        return "high"
    if cluster.category in {"verification", "data"}:
        return "medium"
    return "low"


def _strategy_for_cluster(cluster: FailureClusterRecord) -> str:
    if cluster.category == "permission":
        return "human_review_required"
    if cluster.category == "policy":
        return "human_review_required"
    if cluster.category == "preflight":
        return "fix_precondition_then_rerun"
    if cluster.category == "front_validation":
        return "inspect_validation_and_rerun"
    if cluster.category == "workflow_branch":
        return "resume_detected_branch"
    if cluster.category == "backend_data":
        return "inspect_backend_precondition"
    if cluster.category == "environment":
        return "fix_precondition_then_rerun"
    if cluster.category == "network":
        return "capture_and_retry"
    if cluster.category == "ui":
        return "refresh_locator_and_rerun"
    if cluster.category == "verification":
        return "tighten_assertion_then_rerun"
    if cluster.category == "data":
        return "repair_input_then_rerun"
    if cluster.category == "stability":
        return "guard_and_retry"
    return "diagnose_then_rerun"


def _expected_outcome(cluster: FailureClusterRecord) -> str:
    if cluster.stage:
        return f"{cluster.stage} should complete without reopening {cluster.cluster_id}."
    return f"The next round should retire {cluster.cluster_id}."


def _retry_owner_for_cluster(cluster: FailureClusterRecord) -> str:
    if cluster.action_level == "runtime":
        return "runtime"
    if cluster.action_level in {"logic", "workflow"}:
        return "agent"
    if cluster.category in {"environment", "network"}:
        return "runtime"
    return "agent"


def _execution_hints_for_cluster(cluster: FailureClusterRecord) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    if cluster.category == "permission":
        hints["requires_human_review"] = True
        hints["stop_after_current_round"] = True
    elif cluster.category == "policy":
        hints["requires_human_review"] = True
        hints["stop_after_current_round"] = True
        hints["policy_retry_mode"] = "review_project_allowlist"
    elif cluster.category == "preflight":
        hints["preflight_mode"] = "refresh_capability_probe"
        hints["stop_after_current_round"] = True
    elif cluster.category == "front_validation":
        hints["validation_retry_mode"] = "inspect_visible_errors"
    elif cluster.category == "workflow_branch":
        hints["workflow_retry_mode"] = "resume_detected_branch"
    elif cluster.category == "backend_data":
        hints["backend_retry_mode"] = "inspect_backend_precondition"
    if cluster.category == "ui":
        hints["ui_retry_mode"] = "refresh_locator_and_rerun"
    elif cluster.category == "verification":
        hints["verification_mode"] = "tighten_assertion_then_rerun"
    elif cluster.category == "data":
        hints["data_retry_mode"] = "repair_input_then_rerun"
    elif cluster.category == "environment":
        hints["preflight_mode"] = "fix_precondition_then_rerun"
    elif cluster.category == "network":
        hints["network_mode"] = "capture_and_retry"
    elif cluster.category == "stability":
        hints["stability_mode"] = "guard_and_retry"
    if cluster.stage:
        hints["focus_stage"] = cluster.stage
    if cluster.related_items:
        hints["related_items"] = list(cluster.related_items)
    return hints


def _signal_evidence(signal: Mapping[str, Any]) -> dict[str, Any]:
    evidence = {
        "source": signal.get("source"),
        "title": signal.get("title"),
        "attempt_id": signal.get("attempt_id"),
        "item_id": signal.get("item_id"),
        "message": signal.get("message"),
    }
    return {key: value for key, value in evidence.items() if value is not None}


def _item_evidence(item: ReportItem) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    if item.item_id:
        evidence.append({"item_id": item.item_id, "status": item.status})
    for artifact in item.artifacts:
        evidence.append({"artifact": artifact.label, "path": artifact.path})
    if item.summary:
        evidence.append({"summary": item.summary})
    return evidence


def _promotion_target_for_level(promotion_level: str) -> str:
    if promotion_level == "run_summary":
        return "project_reference_baseline"
    return "project_baseline_freeze"


def _promotion_recommendation_for_level(promotion_level: str) -> str:
    if promotion_level == "retry_baseline":
        return "freeze_retry_baseline"
    if promotion_level == "run_summary":
        return "retain_reference_run"
    return "freeze_project_baseline"


def _promotion_evidence_requirements(
    *,
    promotion_level: str,
    promotion_target: str,
) -> list[str]:
    requirements = ["successful verification evidence"]
    if promotion_target == "project_baseline_freeze":
        requirements.append("key artifacts or screenshots")
    if promotion_target == "project_reference_baseline":
        requirements.append("final run summary or key iteration artifacts")
    if promotion_level == "verified_success":
        requirements.append("manual reviewer confirmation before promotion")
    return requirements


def _missing_promotion_evidence(
    evidence: list[dict[str, Any]],
    evidence_requirements: list[str],
) -> list[str]:
    if evidence:
        return []
    if not evidence_requirements:
        return ["successful verification evidence"]
    return list(evidence_requirements)


def _metric_delta(current_value: Any, previous_value: Any) -> Any:
    if isinstance(current_value, (int, float)) and isinstance(previous_value, (int, float)):
        return current_value - previous_value
    if current_value == previous_value:
        return 0
    return None


def _metric_trend(current_value: Any, previous_value: Any) -> str:
    if previous_value is None or current_value is None:
        return "unknown"
    if current_value == previous_value:
        return "unchanged"
    if isinstance(current_value, (int, float)) and isinstance(previous_value, (int, float)):
        return "up" if current_value > previous_value else "down"
    return "changed"


def _judge_improvement(
    *,
    current_status: str,
    previous_status: str,
    cluster_changes: list[FailureClusterChangeRecord],
    metrics: list[ComparisonMetricRecord],
) -> str:
    if current_status.lower() in SUCCESS_STATUSES and previous_status.lower() not in SUCCESS_STATUSES:
        return "improved"
    if current_status.lower() not in SUCCESS_STATUSES and previous_status.lower() in SUCCESS_STATUSES:
        return "regressed"

    improved = 0
    regressed = 0
    for change in cluster_changes:
        if change.status in {"resolved", "improved"}:
            improved += 1
        elif change.status in {"new", "regressed"}:
            regressed += 1

    if improved and not regressed:
        return "improved"
    if regressed and not improved:
        return "regressed"

    if not improved and not regressed:
        relevant = {
            item.metric_id: item for item in metrics if item.metric_id != "promotion_candidate_count"
        }
        changed = any(item.trend not in {"unchanged", "unknown"} for item in relevant.values())
        if not changed:
            return "no_improvement"
    return "unknown"


def _comparison_summary(
    improvement: str,
    cluster_changes: list[FailureClusterChangeRecord],
    metrics: list[ComparisonMetricRecord],
) -> str:
    if improvement == "improved":
        return "The latest iteration shows conservative signs of improvement over the previous baseline."
    if improvement == "regressed":
        return "The latest iteration regressed against the previous baseline."
    if improvement == "no_improvement":
        return "The latest iteration did not show a measurable improvement over the previous baseline."
    if cluster_changes:
        return "The latest iteration comparison produced mixed or incomplete signals."
    changed_metrics = [item.metric_id for item in metrics if item.trend not in {"unchanged", "unknown"}]
    if changed_metrics:
        return "The latest iteration changed some metrics, but not enough for a decisive judgement."
    return "The latest iteration comparison was inconclusive."


def _collect_cluster_ids(
    cluster_changes: Sequence[FailureClusterChangeRecord],
    statuses: set[str],
) -> list[str]:
    cluster_ids: list[str] = []
    for change in cluster_changes:
        if change.status not in statuses:
            continue
        cluster_ids.extend(change.current_cluster_ids or change.previous_cluster_ids)
    return _sorted_unique(cluster_ids)


def _target_stage_for_actions(actions: Sequence[RetryAction]) -> str | None:
    if not actions:
        return None
    stage_counts: dict[str, int] = defaultdict(int)
    for action in actions:
        bucket = action.stage or action.owner or "verification"
        stage_counts[bucket] += 1
    if stage_counts:
        return sorted(stage_counts, key=lambda key: (-stage_counts[key], key))[0]
    return "verification"


def _next_round_stop_reason(stop_conditions: StopDecisionRecord) -> str:
    if stop_conditions.primary_reason:
        return f"Next round scheduling stopped because {stop_conditions.primary_reason} was triggered."
    return "Next round scheduling stopped because one or more stop conditions were triggered."


def _cluster_change_summary(
    *,
    key: str,
    status: str,
    previous_signal_count: int,
    current_signal_count: int,
) -> str:
    return (
        f"{key} => {status}; signals {previous_signal_count} -> {current_signal_count}"
    )


def _index_clusters(value: Sequence[Any]) -> dict[str, list[FailureClusterRecord]]:
    index: dict[str, list[FailureClusterRecord]] = defaultdict(list)
    for item in value:
        cluster = item if isinstance(item, FailureClusterRecord) else _normalize_cluster_records([item])[0]
        key = f"{cluster.category}::{cluster.stage or 'unknown'}"
        index[key].append(cluster)
    return index


def _find_keyword_matches(values: Sequence[str], keywords: Sequence[str]) -> list[str]:
    matches: list[str] = []
    lowered_keywords = [item.lower() for item in keywords]
    for value in values:
        lowered = value.lower()
        for keyword in lowered_keywords:
            if keyword in lowered:
                matches.append(value)
                break
    return matches


def _flatten_text(parts: Any) -> list[str]:
    values: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, str):
            text = part.strip()
            if text:
                values.append(text)
            continue
        if isinstance(part, Mapping):
            values.extend(_flatten_text(part.values()))
            continue
        if isinstance(part, Sequence) and not isinstance(part, (str, bytes)):
            values.extend(_flatten_text(list(part)))
            continue
        text = _text(part)
        if text:
            values.append(text)
    return values


def _sorted_unique(values: Any) -> list[str]:
    items = sorted({value for value in values if value})
    return [str(item) for item in items]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _coerce_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    results: list[dict[str, Any]] = []
    for item in value:
        data = _to_mapping(item)
        if data:
            results.append(data)
    return results


def _first_non_empty(values: Any) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


def _count_from_summary(summary: Mapping[str, Any], label: str) -> int | None:
    counts = summary.get("counts")
    if not isinstance(counts, list):
        return None
    for item in counts:
        data = _to_mapping(item)
        if _text(data.get("label")) == label:
            return _coerce_int(data.get("value"))
    return None


def _metrics_from_comparison(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    metrics: dict[str, Any] = {}
    for item in value:
        data = _to_mapping(item)
        metric_id = _text(data.get("metric_id"))
        if not metric_id:
            continue
        metrics[metric_id] = data.get("current_value")
    return metrics


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _coerce_round_index(value: Any) -> int | None:
    data = _to_mapping(value)
    if data:
        return _coerce_int(data.get("index"))
    return _coerce_int(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}
