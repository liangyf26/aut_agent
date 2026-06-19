from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from prototype.stage2.app.reporting import (
    ReportItem,
    RunReport,
    coerce_progress_snapshot,
    coerce_run_report,
)

from .models import (
    FailureClusterRecord,
    IterationArtifacts,
    IterationBuildInput,
    IterationSummary,
    PromotionCandidateRecord,
    RetryAction,
    RetryPlanRecord,
)


FAILURE_STATUSES = {"failed", "error", "blocked", "timeout"}
SUCCESS_STATUSES = {"completed", "success", "passed", "verified"}


def build_iteration_outputs(
    run_report: Any = None,
    status_snapshot: Any = None,
    attempts: Sequence[Any] | None = None,
) -> IterationArtifacts:
    payload = IterationBuildInput(
        run_report=run_report,
        status_snapshot=status_snapshot,
        attempts=list(attempts or []),
    )
    return _IterationBuilder(payload).build()


class _IterationBuilder:
    def __init__(self, payload: IterationBuildInput) -> None:
        self.report = coerce_run_report(payload.run_report or {})
        self.snapshot = coerce_progress_snapshot(
            payload.status_snapshot or {"run_id": self.report.summary.run_id}
        )
        self.attempts = [_normalize_attempt(item) for item in payload.attempts]

    def build(self) -> IterationArtifacts:
        failure_clusters = self._build_failure_clusters()
        retry_plan = self._build_retry_plan(failure_clusters)
        promotion_candidates = self._build_promotion_candidates(failure_clusters)
        summary = IterationSummary(
            run_id=self._run_id,
            run_status=self._run_status,
            outcome=self._derive_outcome(failure_clusters),
            failure_cluster_count=len(failure_clusters),
            retry_action_count=len(retry_plan.actions),
            promotion_candidate_count=len(promotion_candidates),
            notes=self._build_summary_notes(failure_clusters, retry_plan, promotion_candidates),
        )
        return IterationArtifacts(
            summary=summary,
            failure_clusters=failure_clusters,
            retry_plan=retry_plan,
            promotion_candidates=promotion_candidates,
        )

    @property
    def _run_id(self) -> str:
        return self.report.summary.run_id or self.snapshot.run_id

    @property
    def _run_status(self) -> str:
        return self.report.summary.status or self.snapshot.status or "unknown"

    def _derive_outcome(self, failure_clusters: list[FailureClusterRecord]) -> str:
        if self._is_success_run() and not failure_clusters:
            return "success_only"
        if failure_clusters:
            return "needs_retry"
        return "no_signals"

    def _build_summary_notes(
        self,
        failure_clusters: list[FailureClusterRecord],
        retry_plan: RetryPlanRecord,
        promotion_candidates: list[PromotionCandidateRecord],
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
                signals.append(
                    {
                        "source": "attempt",
                        "attempt_id": attempt["attempt_id"],
                        "item_id": attempt["attempt_id"],
                        "title": attempt["title"],
                        "category": _classify_failure(
                            attempt["classification"],
                            attempt["error_type"],
                            attempt["message"],
                        ),
                        "stage": attempt["stage"] or self.snapshot.stage,
                        "root_cause_hint": _root_cause_hint(
                            attempt["error_type"],
                            attempt["message"],
                        ),
                        "message": attempt["message"],
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
                next_round=_coerce_int(self.report.summary.current_round) or _coerce_int(self.snapshot.current_round),
                goal="Preserve the successful path and continue normal reporting.",
                stop_reason=self.report.summary.stop_reason,
                actions=[],
                notes=["No open failure cluster requires a retry action."],
            )

        current_round = _coerce_int(self.report.summary.current_round) or _coerce_int(self.snapshot.current_round) or 0
        actions: list[RetryAction] = []
        for index, cluster in enumerate(clusters, start=1):
            actions.append(
                RetryAction(
                    action_id=f"retry-{index:03d}",
                    cluster_id=cluster.cluster_id,
                    title=_retry_title(cluster),
                    priority=_priority_for_cluster(cluster),
                    stage=cluster.stage,
                    strategy=_strategy_for_cluster(cluster),
                    reason=cluster.summary or cluster.recommendation,
                    expected_outcome=_expected_outcome(cluster),
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
            candidates.append(
                PromotionCandidateRecord(
                    candidate_id=f"promotion-success-{len(candidates) + 1:03d}",
                    source="run_report.success_items",
                    title=item.name,
                    promotion_level="verified_success",
                    status="candidate",
                    reason=item.summary or "Successful item recorded in run report.",
                    evidence=_item_evidence(item),
                )
            )

        for attempt in self.attempts:
            if attempt["status"] in SUCCESS_STATUSES:
                candidates.append(
                    PromotionCandidateRecord(
                        candidate_id=f"promotion-attempt-{len(candidates) + 1:03d}",
                        source="attempts",
                        title=attempt["title"],
                        promotion_level="retry_baseline",
                        status="candidate",
                        reason=attempt["message"] or "Attempt completed successfully.",
                        evidence=[
                            {
                                "attempt_id": attempt["attempt_id"],
                                "stage": attempt["stage"],
                                "status": attempt["status"],
                            }
                        ],
                    )
                )

        if self._is_success_run() and not candidates:
            candidates.append(
                PromotionCandidateRecord(
                    candidate_id="promotion-run-001",
                    source="run_status",
                    title="Successful run summary",
                    promotion_level="run_summary",
                    status="candidate",
                    reason="Run finished successfully and can be retained as a reference baseline.",
                    evidence=[{"run_id": self._run_id, "status": self._run_status}],
                )
            )

        if failure_clusters:
            return candidates
        return candidates

    def _is_success_run(self) -> bool:
        status = (self._run_status or "").lower()
        return status in SUCCESS_STATUSES


def _normalize_attempt(value: Any) -> dict[str, Any]:
    data = _to_mapping(value)
    if not data and isinstance(value, str):
        data = {"message": value}

    attempt_id = _text(
        data.get("attempt_id") or data.get("id") or data.get("key")
    ) or "attempt"
    status = (_text(data.get("status") or data.get("result") or data.get("state")) or "unknown").lower()
    title = _text(data.get("title") or data.get("name") or data.get("step")) or attempt_id
    classification = _text(data.get("classification") or data.get("category") or data.get("failure_type"))
    error_type = _text(data.get("error_type") or data.get("reason") or data.get("blocked_reason"))
    stage = _text(data.get("stage") or data.get("phase"))
    message = _text(data.get("message") or data.get("summary") or data.get("description"))

    return {
        "attempt_id": attempt_id,
        "status": status,
        "title": title,
        "classification": classification,
        "error_type": error_type,
        "stage": stage,
        "message": message,
    }


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


def _first_non_empty(values: Any) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


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
