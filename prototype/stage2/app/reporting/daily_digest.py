from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .decision_explainer import build_decision_explanation
from .models import (
    DailySummary,
    Fact,
    PlatformDailyReport,
    PromotionCandidateSummary,
    ReportItem,
    RunReport,
    SectionBlock,
    SkillInventorySummary,
    coerce_run_report,
)


def build_platform_daily_report(
    reports: list[RunReport | dict[str, Any]] | tuple[RunReport | dict[str, Any], ...],
    *,
    report_date: str | None = None,
) -> PlatformDailyReport:
    normalized = [coerce_run_report(report) for report in reports]
    if not normalized:
        return PlatformDailyReport(
            report_date=report_date,
            summary="No runs were provided for the platform daily report.",
            notes=["Platform daily report is empty because no run report inputs were supplied."],
        )

    derived_runs = [_with_derived_sections(report) for report in normalized]
    signal_contexts = {
        id(report): _extract_run_signal_context(report)
        for report in derived_runs
    }
    chosen_date = report_date or _latest_report_date(derived_runs)
    run_summaries = [
        _build_run_summary_item(report, signal_contexts.get(id(report), {}))
        for report in derived_runs
    ]
    efficiency_facts = _aggregate_efficiency_facts(derived_runs)
    daily_summary = _aggregate_daily_summary(derived_runs, chosen_date, signal_contexts)
    model_comparison = _aggregate_model_comparison_summary(derived_runs, signal_contexts)
    skill_inventory = _aggregate_skill_inventory_summary(derived_runs)
    promotion_summary = _aggregate_promotion_candidate_summary(derived_runs)
    decision_sections = _aggregate_decision_sections(derived_runs, signal_contexts)

    facts = [
        Fact(label="run_count", value=len(derived_runs)),
        Fact(label="successful_runs", value=sum(1 for report in derived_runs if _is_success(report.summary.status))),
        Fact(label="failed_runs", value=sum(1 for report in derived_runs if _is_failure(report.summary.status))),
        Fact(label="models_covered", value=sorted(_collect_model_names(derived_runs))),
        Fact(label="stopped_runs", value=sum(1 for context in signal_contexts.values() if context.get("should_stop") is True)),
        Fact(
            label="runs_needing_review",
            value=sum(1 for context in signal_contexts.values() if context.get("manual_review_required")),
        ),
        Fact(
            label="next_round_scheduled_runs",
            value=sum(
                1
                for context in signal_contexts.values()
                if context.get("next_round_should_start") is True
            ),
        ),
        Fact(
            label="execution_hint_runs",
            value=sum(1 for context in signal_contexts.values() if context.get("execution_hints")),
        ),
    ]
    stop_reasons = _top_counts(
        context.get("stop_reason")
        for context in signal_contexts.values()
        if context.get("should_stop") is True
    )
    if stop_reasons:
        facts.append(Fact(label="top_stop_reasons", value=stop_reasons))
    target_stages = sorted(
        {
            context["next_round_target_stage"]
            for context in signal_contexts.values()
            if context.get("next_round_should_start") is True and context.get("next_round_target_stage")
        }
    )
    if target_stages:
        facts.append(Fact(label="scheduled_target_stages", value=target_stages))
    summary = (
        f"Aggregated {len(derived_runs)} run(s) into a platform daily report"
        + (f" for {chosen_date}." if chosen_date else ".")
    )
    notes = [
        "This platform daily report was aggregated from run-level reporting payloads.",
        "Sections fall back to derived summaries when a run did not provide explicit daily or skill summaries.",
    ]
    if stop_reasons:
        notes.append("Stopped runs were grouped by stop_reason for quick review.")
    if any(context.get("execution_hints") for context in signal_contexts.values()):
        notes.append("Execution hints were lifted from retry-plan actions and round-level handoff payloads when available.")
    return PlatformDailyReport(
        report_date=chosen_date,
        summary=summary,
        facts=facts,
        run_summaries=run_summaries,
        efficiency_observations=efficiency_facts,
        daily_summary=daily_summary,
        model_comparison_summary=model_comparison,
        skill_inventory_summary=skill_inventory,
        promotion_candidate_summary=promotion_summary,
        notes=notes,
        extra_sections=decision_sections,
    )


def _with_derived_sections(report: RunReport) -> RunReport:
    from .report_markdown import _with_derived_sections as derive_sections

    return derive_sections(report)


def _latest_report_date(reports: list[RunReport]) -> str | None:
    values = [
        report.summary.finished_at or report.summary.started_at
        for report in reports
        if report.summary.finished_at or report.summary.started_at
    ]
    if not values:
        return None
    return sorted(values)[-1]


def _build_run_summary_item(report: RunReport, signal_context: Mapping[str, Any]) -> ReportItem:
    decision_status = _decision_item_status(report, signal_context)
    model_names = [model.model_name for model in report.model_evaluations if model.model_name]
    facts = [
        Fact(label="status", value=report.summary.status),
        Fact(label="decision_status", value=decision_status),
        Fact(label="template", value=report.summary.template_name),
        Fact(label="duration_seconds", value=report.summary.duration_seconds),
        Fact(label="failure_clusters", value=len(report.failure_clusters)),
        Fact(label="promotion_candidates", value=len(report.promotion_candidates)),
    ]
    if report.summary.current_round is not None:
        facts.append(Fact(label="current_round", value=report.summary.current_round))
    if signal_context.get("stop_status"):
        facts.append(Fact(label="stop_status", value=signal_context["stop_status"]))
    if signal_context.get("stop_reason"):
        facts.append(Fact(label="stop_reason", value=signal_context["stop_reason"]))
    if signal_context.get("triggered_stop_conditions"):
        facts.append(
            Fact(
                label="triggered_stop_conditions",
                value=signal_context["triggered_stop_conditions"],
            )
        )
    next_round_status = signal_context.get("next_round_status", report.summary.extra.get("next_round_status"))
    if next_round_status is not None:
        facts.append(Fact(label="next_round_status", value=next_round_status))
    next_round_should_start = signal_context.get(
        "next_round_should_start",
        report.summary.extra.get("next_round_should_start"),
    )
    if next_round_should_start is not None:
        facts.append(Fact(label="next_round_should_start", value=next_round_should_start))
    if signal_context.get("next_round") is not None:
        facts.append(Fact(label="next_round", value=signal_context["next_round"]))
    if signal_context.get("next_round_target_stage"):
        facts.append(
            Fact(
                label="next_round_target_stage",
                value=signal_context["next_round_target_stage"],
            )
        )
    if signal_context.get("planned_actions"):
        facts.append(Fact(label="planned_actions", value=signal_context["planned_actions"]))
    if signal_context.get("execution_hints"):
        facts.append(Fact(label="execution_hints", value=signal_context["execution_hints"]))
    if signal_context.get("manual_review_required"):
        facts.append(Fact(label="manual_review_required", value=True))
    if signal_context.get("watch_signals"):
        facts.append(Fact(label="watch_signals", value=signal_context["watch_signals"][:3]))
    if report.summary.next_action:
        facts.append(Fact(label="next_action", value=report.summary.next_action))
    if model_names:
        facts.append(Fact(label="models", value=model_names))
    return ReportItem(
        item_id=report.summary.run_id,
        name=report.summary.template_name or report.summary.run_id,
        status=decision_status,
        summary=_compose_run_summary(report, signal_context),
        facts=facts,
        notes=signal_context.get("decision_notes", []),
    )


def _aggregate_efficiency_facts(reports: list[RunReport]) -> list[Fact]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for report in reports:
        if report.summary.duration_seconds is not None:
            grouped["duration_seconds"].append(float(report.summary.duration_seconds))
        for fact in report.efficiency_observations:
            value = _number_value(fact.value)
            if value is not None:
                grouped[fact.label].append(value)
    results: list[Fact] = []
    for label, values in sorted(grouped.items()):
        if not values:
            continue
        average = sum(values) / len(values)
        results.append(
            Fact(
                label=f"avg_{label}",
                value=round(average, 2),
                note=f"{len(values)} sample(s)",
            )
        )
    return results


def _aggregate_daily_summary(
    reports: list[RunReport],
    report_date: str | None,
    signal_contexts: Mapping[int, Mapping[str, Any]],
) -> DailySummary:
    scheduled_runs = sum(
        1
        for context in signal_contexts.values()
        if context.get("next_round_should_start") is True
    )
    stopped_runs = sum(1 for context in signal_contexts.values() if context.get("should_stop") is True)
    review_runs = sum(
        1 for context in signal_contexts.values() if context.get("manual_review_required")
    )
    execution_hint_runs = sum(
        1 for context in signal_contexts.values() if context.get("execution_hints")
    )
    existing_watch_items = [
        item
        for report in reports
        for item in (report.daily_summary.watch_items if report.daily_summary else [])
    ]
    decision_watch_items = [
        _build_decision_watch_item(report, signal_contexts.get(id(report), {}))
        for report in reports
    ]
    stop_reasons = _top_counts(
        signal_contexts.get(id(report), {}).get("stop_reason")
        for report in reports
        if signal_contexts.get(id(report), {}).get("should_stop") is True
    )
    return DailySummary(
        date=report_date,
        summary=(
            f"Platform daily digest aggregated from {len(reports)} run(s): "
            f"{stopped_runs} stopped, {scheduled_runs} scheduled for another round, "
            f"{review_runs} requiring review."
        ),
        new_templates=_dedupe_items(
            item
            for report in reports
            for item in (report.daily_summary.new_templates if report.daily_summary else [])
        ),
        new_locator_strategies=_dedupe_items(
            item
            for report in reports
            for item in (report.daily_summary.new_locator_strategies if report.daily_summary else [])
        ),
        new_failure_fix_strategies=_dedupe_items(
            item
            for report in reports
            for item in (report.daily_summary.new_failure_fix_strategies if report.daily_summary else [])
        ),
        candidate_platform_skills=_dedupe_items(
            item
            for report in reports
            for item in (report.daily_summary.candidate_platform_skills if report.daily_summary else [])
        ),
        watch_items=_dedupe_items(
            item
            for item in [*existing_watch_items, *decision_watch_items]
            if item is not None
        ),
        facts=[
            Fact(label="run_count", value=len(reports)),
            Fact(label="templates_covered", value=sorted({report.summary.template_name for report in reports if report.summary.template_name})),
            Fact(label="stopped_runs", value=stopped_runs),
            Fact(label="next_round_scheduled_runs", value=scheduled_runs),
            Fact(label="runs_needing_review", value=review_runs),
            Fact(label="execution_hint_runs", value=execution_hint_runs),
        ],
        notes=[
            "Aggregated from run-level daily summaries.",
            *(
                ["Top stop reasons: " + ", ".join(stop_reasons)]
                if stop_reasons
                else []
            ),
        ],
    )


def _aggregate_model_comparison_summary(
    reports: list[RunReport],
    signal_contexts: Mapping[int, Mapping[str, Any]],
) -> SectionBlock | None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        signal_context = signal_contexts.get(id(report), {})
        for model in report.model_evaluations:
            grouped[model.model_name].append(
                {
                    "model": model,
                    "run_id": report.summary.run_id,
                    "template": report.summary.template_name,
                    "signal_context": signal_context,
                }
            )
    if not grouped:
        return None

    items: list[ReportItem] = []
    for model_name in sorted(grouped):
        rows = grouped[model_name]
        completion_values = [
            _number_value(row["model"].completion_rate)
            for row in rows
            if _number_value(row["model"].completion_rate) is not None
        ]
        latency_values = [
            _number_value(row["model"].average_latency_ms)
            for row in rows
            if _number_value(row["model"].average_latency_ms) is not None
        ]
        stopped_runs = sum(
            1
            for row in rows
            if row["signal_context"].get("should_stop") is True
        )
        scheduled_runs = sum(
            1
            for row in rows
            if row["signal_context"].get("next_round_should_start") is True
        )
        review_runs = sum(
            1
            for row in rows
            if row["signal_context"].get("manual_review_required")
        )
        hint_runs = sum(
            1
            for row in rows
            if row["signal_context"].get("execution_hints")
        )
        precheck_tags = sorted(
            {
                tag
                for row in rows
                for tag in row["model"].precheck_tags
            }
        )
        recommended_roles = sorted(
            {
                row["model"].recommended_role
                for row in rows
                if row["model"].recommended_role
            }
        )
        response_stability = sorted(
            {
                row["model"].response_stability
                for row in rows
                if row["model"].response_stability
            }
        )
        structured_output = sorted(
            {
                row["model"].structured_output_stability
                for row in rows
                if row["model"].structured_output_stability
            }
        )
        joined_discovery = any(row["model"].joined_discovery is True for row in rows)
        joined_attribution = any(row["model"].joined_attribution is True for row in rows)
        next_round_targets = sorted(
            {
                row["signal_context"]["next_round_target_stage"]
                for row in rows
                if row["signal_context"].get("next_round_should_start") is True
                and row["signal_context"].get("next_round_target_stage")
            }
        )
        notes = [
            f"Observed in {len(rows)} run(s).",
            f"Templates: {', '.join(sorted({row['template'] for row in rows if row['template']})) or 'unknown'}",
        ]
        if next_round_targets:
            notes.append("Scheduled target stages: " + ", ".join(next_round_targets))
        items.append(
            ReportItem(
                name=model_name,
                status="aggregated",
                summary=_model_aggregate_summary(
                    completion_values=completion_values,
                    latency_values=latency_values,
                    recommended_roles=recommended_roles,
                ),
                facts=[
                    Fact(label="precheck_tags", value=precheck_tags),
                    Fact(label="joined_discovery", value=joined_discovery),
                    Fact(label="joined_attribution", value=joined_attribution),
                    Fact(
                        label="avg_completion_rate",
                        value=round(sum(completion_values) / len(completion_values), 2)
                        if completion_values
                        else None,
                    ),
                    Fact(
                        label="avg_latency_ms",
                        value=round(sum(latency_values) / len(latency_values), 2)
                        if latency_values
                        else None,
                    ),
                    Fact(label="response_stability", value=response_stability),
                    Fact(label="structured_output_stability", value=structured_output),
                    Fact(label="recommended_roles", value=recommended_roles),
                    Fact(label="stopped_runs", value=stopped_runs),
                    Fact(label="next_round_scheduled_runs", value=scheduled_runs),
                    Fact(label="runs_needing_review", value=review_runs),
                    Fact(label="execution_hint_runs", value=hint_runs),
                ],
                notes=notes,
            )
        )
    return SectionBlock(
        title="Model Comparison Summary",
        summary="Cross-run model comparison aggregated from model evaluations.",
        items=items,
        notes=["This summary is intended for AI-tester/Qwen-style routing decisions across runs."],
    )


def _aggregate_skill_inventory_summary(reports: list[RunReport]) -> SkillInventorySummary | None:
    runtime_skills = _dedupe_items(
        item
        for report in reports
        for item in (
            report.skill_inventory_summary.runtime_skills if report.skill_inventory_summary else []
        )
    )
    project_skills = _dedupe_items(
        item
        for report in reports
        for item in (
            report.skill_inventory_summary.project_skills if report.skill_inventory_summary else []
        )
    )
    platform_candidates = _dedupe_items(
        item
        for report in reports
        for item in (
            report.skill_inventory_summary.platform_candidates if report.skill_inventory_summary else []
        )
    )
    if not any((runtime_skills, project_skills, platform_candidates)):
        return None
    return SkillInventorySummary(
        summary="Cross-run skill inventory summary aggregated from run reports.",
        runtime_skills=runtime_skills,
        project_skills=project_skills,
        platform_candidates=platform_candidates,
        facts=[
            Fact(label="runtime_skill_count", value=len(runtime_skills)),
            Fact(label="project_skill_count", value=len(project_skills)),
            Fact(label="platform_candidate_count", value=len(platform_candidates)),
        ],
        notes=["Project and platform skill inventories were aggregated from per-run summaries."],
    )


def _aggregate_promotion_candidate_summary(reports: list[RunReport]) -> PromotionCandidateSummary | None:
    candidates = _dedupe_items(
        item
        for report in reports
        for item in (
            report.promotion_candidate_summary.candidates
            if report.promotion_candidate_summary and report.promotion_candidate_summary.candidates
            else report.promotion_candidates
        )
    )
    if not candidates:
        return None

    approval_notes = sorted(
        {
            note
            for report in reports
            for note in (
                report.promotion_candidate_summary.approval_notes
                if report.promotion_candidate_summary
                else []
            )
        }
    )
    evidence_requirements = sorted(
        {
            note
            for report in reports
            for note in (
                report.promotion_candidate_summary.evidence_requirements
                if report.promotion_candidate_summary
                else []
            )
        }
    )
    if not approval_notes:
        approval_notes = ["Platform promotion still requires evidence review and repeatability checks."]
    if not evidence_requirements:
        evidence_requirements = [
            "successful verification evidence",
            "repeatability across runs or models",
        ]

    review_status_breakdown: Counter[str] = Counter()
    promotion_target_breakdown: Counter[str] = Counter()
    recommendation_breakdown: Counter[str] = Counter()
    baseline_freeze_candidate_ids: list[str] = []
    ready_candidate_ids: list[str] = []
    deferred_candidate_ids: list[str] = []
    manual_review_required = False

    for item in candidates:
        review_status = _promotion_candidate_fact_value(item, "review_status") or "needs_review"
        review_status_breakdown[review_status] += 1

        promotion_target = _promotion_candidate_fact_value(item, "promotion_target") or "unspecified"
        promotion_target_breakdown[promotion_target] += 1
        if "baseline_freeze" in promotion_target and item.item_id:
            baseline_freeze_candidate_ids.append(item.item_id)

        recommendation = _promotion_candidate_fact_value(item, "promotion_recommendation") or "review_candidate"
        recommendation_breakdown[recommendation] += 1

        if _promotion_candidate_fact_value(item, "manual_review_required") is True:
            manual_review_required = True
        if review_status == "ready_for_review" and item.item_id:
            ready_candidate_ids.append(item.item_id)
        elif item.item_id:
            deferred_candidate_ids.append(item.item_id)

    return PromotionCandidateSummary(
        summary="Cross-run promotion candidate summary aggregated from run reports.",
        candidates=candidates,
        approval_notes=approval_notes,
        evidence_requirements=evidence_requirements,
        facts=[
            Fact(label="candidate_count", value=len(candidates)),
            Fact(label="review_status", value="needs_review" if manual_review_required else "ready_for_review"),
            Fact(label="manual_review_required", value=manual_review_required),
            Fact(label="baseline_freeze_candidate_count", value=len(set(baseline_freeze_candidate_ids))),
            Fact(label="ready_for_review_count", value=len(set(ready_candidate_ids))),
            Fact(label="deferred_candidate_count", value=len(set(deferred_candidate_ids))),
        ],
        notes=["Candidates were deduplicated across the supplied run reports."],
        extra={
            "review_status_breakdown": dict(review_status_breakdown),
            "promotion_target_breakdown": dict(promotion_target_breakdown),
            "promotion_recommendation_breakdown": dict(recommendation_breakdown),
            "baseline_freeze_candidate_ids": sorted(set(baseline_freeze_candidate_ids)),
            "ready_candidate_ids": sorted(set(ready_candidate_ids)),
            "deferred_candidate_ids": sorted(set(deferred_candidate_ids)),
        },
    )


def _aggregate_decision_sections(
    reports: list[RunReport],
    signal_contexts: Mapping[int, Mapping[str, Any]],
) -> list[SectionBlock]:
    items = [
        _build_decision_section_item(report, signal_contexts.get(id(report), {}))
        for report in reports
    ]
    items = [item for item in items if item is not None]
    if not items:
        return []

    stopped_runs = sum(1 for item in items if item.status == "stopped")
    scheduled_runs = sum(1 for item in items if item.status == "scheduled")
    review_runs = sum(1 for item in items if item.status == "needs_review")
    hint_runs = sum(
        1
        for context in signal_contexts.values()
        if context.get("execution_hints")
    )
    return [
        SectionBlock(
            title="Stop and Next-Round Digest",
            summary=(
                f"{stopped_runs} run(s) stopped, {scheduled_runs} scheduled follow-up work, "
                f"{review_runs} require manual review."
            ),
            facts=[
                Fact(label="stopped_runs", value=stopped_runs),
                Fact(label="next_round_scheduled_runs", value=scheduled_runs),
                Fact(label="runs_needing_review", value=review_runs),
                Fact(label="execution_hint_runs", value=hint_runs),
            ],
            items=items,
            notes=[
                "This section summarizes stop outcomes, next-round planning, and execution hints from each run.",
            ],
        )
    ]


def _dedupe_items(items: Any) -> list[ReportItem]:
    deduped: dict[tuple[str, str, str], ReportItem] = {}
    for item in items:
        normalized = item if isinstance(item, ReportItem) else ReportItem.from_value(item)
        key = (
            normalized.item_id or "",
            normalized.name,
            normalized.summary or "",
        )
        deduped.setdefault(key, normalized)
    return list(deduped.values())


def _promotion_candidate_fact_value(item: ReportItem, label: str) -> Any:
    for fact in item.facts:
        if fact.label == label and fact.value not in (None, "", [], {}):
            return fact.value
    extra_value = item.extra.get(label)
    if extra_value not in (None, "", [], {}):
        return extra_value
    return None


def _collect_model_names(reports: list[RunReport]) -> set[str]:
    return {
        model.model_name
        for report in reports
        for model in report.model_evaluations
        if model.model_name
    }


def _is_success(status: str | None) -> bool:
    return (status or "").lower() in {"completed", "success", "passed", "verified"}


def _is_failure(status: str | None) -> bool:
    return (status or "").lower() in {"failed", "error", "blocked", "timeout"}


def _number_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().rstrip("%")
        try:
            number = float(stripped)
        except ValueError:
            return None
        if value.strip().endswith("%"):
            return number / 100.0
        return number
    return None


def _model_aggregate_summary(
    *,
    completion_values: list[float],
    latency_values: list[float],
    recommended_roles: list[str],
) -> str:
    parts: list[str] = []
    if completion_values:
        parts.append(f"avg completion {sum(completion_values) / len(completion_values):.2f}")
    if latency_values:
        parts.append(f"avg latency {sum(latency_values) / len(latency_values):.0f} ms")
    if recommended_roles:
        parts.append("roles: " + ", ".join(recommended_roles))
    return "; ".join(parts) or "Cross-run model summary."


def _extract_run_signal_context(report: RunReport) -> dict[str, Any]:
    from .report_markdown import _load_iteration_artifact_payloads

    iteration_artifacts = _load_iteration_artifact_payloads(report)
    stop_conditions = _mapping_dict(_lookup_extra_value(report, "stop_conditions")) or _mapping_dict(
        iteration_artifacts.get("stop_conditions")
    )
    next_round_decision = _mapping_dict(_lookup_extra_value(report, "next_round_decision")) or _mapping_dict(
        iteration_artifacts.get("next_round_decision")
    )
    retry_plan = _mapping_dict(_lookup_extra_value(report, "retry_plan")) or _mapping_dict(
        iteration_artifacts.get("retry_plan")
    )
    round_input = _mapping_dict(_lookup_extra_value(report, "round_input")) or _mapping_dict(
        iteration_artifacts.get("round_input")
    )

    stop_status = _text_value(stop_conditions.get("status")) or _text_value(
        _lookup_value(report, "stop_status")
    )
    should_stop = _bool_value(stop_conditions.get("should_stop"))
    stop_reason = _first_text(
        report.summary.stop_reason,
        stop_conditions.get("primary_reason"),
        next_round_decision.get("stop_reason"),
        _lookup_value(report, "stop_reason"),
    )
    triggered_stop_conditions = _unique_texts(
        _string_list(stop_conditions.get("triggered_conditions"))
        + _string_list(next_round_decision.get("triggered_stop_conditions"))
        + _string_list(_lookup_value(report, "triggered_stop_conditions"))
    )

    next_round_status = _text_value(next_round_decision.get("status")) or _text_value(
        _lookup_value(report, "next_round_status")
    )
    next_round_should_start = _bool_value(next_round_decision.get("should_start_next_round"))
    if next_round_should_start is None:
        next_round_should_start = _bool_value(_lookup_value(report, "next_round_should_start"))
    next_round = _int_value(next_round_decision.get("next_round"))
    if next_round is None:
        next_round = _int_value(_lookup_value(report, "next_round"))
    next_round_target_stage = _first_text(
        next_round_decision.get("target_stage"),
        round_input.get("target_stage"),
        _lookup_value(report, "next_round_target_stage", "target_stage"),
    )
    next_round_primary_reason = _first_text(
        next_round_decision.get("primary_reason"),
        round_input.get("source_decision_reason"),
        _lookup_value(report, "next_round_primary_reason"),
    )
    explanation = build_decision_explanation(
        stop_conditions=stop_conditions,
        next_round_decision=next_round_decision,
        retry_plan=retry_plan,
        round_input=round_input,
        execution_hints=_mapping_dict(_lookup_extra_value(report, "execution_hints")),
    )

    actions = _mapping_list(retry_plan.get("actions"))
    planned_actions = _unique_texts(
        _action_label(action)
        for action in actions
        if _action_label(action)
    )
    action_summaries = _unique_texts(
        _action_summary(action)
        for action in actions
        if _action_summary(action)
    )
    execution_hints = _collect_execution_hints(report, actions, round_input)
    if not planned_actions:
        planned_actions = _collect_section_planned_actions(report)
    if not action_summaries:
        action_summaries = _collect_section_action_summaries(report)
    if not execution_hints:
        execution_hints = _collect_section_execution_hints(report)
    watch_signals = _collect_watch_signals(report)
    manual_review_signals = _collect_manual_review_signals(
        report,
        stop_conditions,
        next_round_decision,
        watch_signals,
    )
    manual_review_required = explanation.manual_review_required or bool(manual_review_signals)
    decision_notes = _unique_texts(
        [explanation.summary, explanation.headline]
        + explanation.notes
        + manual_review_signals
        + action_summaries[:3]
        + execution_hints[:2]
    )

    return {
        "decision_status": explanation.status,
        "decision_headline": explanation.headline,
        "decision_summary": explanation.summary,
        "decision_primary_reason": explanation.primary_reason,
        "stop_status": explanation.stop_status or stop_status,
        "should_stop": explanation.should_stop if explanation.should_stop is not None else should_stop,
        "stop_reason": explanation.stop_reason or stop_reason,
        "triggered_stop_conditions": explanation.triggered_stop_conditions or triggered_stop_conditions,
        "next_round_status": explanation.next_round_status or next_round_status,
        "next_round_should_start": (
            explanation.should_start_next_round
            if explanation.should_start_next_round is not None
            else next_round_should_start
        ),
        "next_round": explanation.next_round if explanation.next_round is not None else next_round,
        "next_round_target_stage": explanation.target_stage or next_round_target_stage,
        "next_round_primary_reason": explanation.primary_reason or next_round_primary_reason,
        "planned_actions": planned_actions[:5],
        "action_summaries": action_summaries[:3],
        "execution_hints": execution_hints[:5],
        "watch_signals": watch_signals[:5],
        "manual_review_required": manual_review_required,
        "manual_review_signals": manual_review_signals[:5],
        "decision_notes": decision_notes[:5],
    }


def _compose_run_summary(report: RunReport, signal_context: Mapping[str, Any]) -> str | None:
    parts: list[str] = []
    base_summary = report.daily_summary.summary if report.daily_summary else None
    if base_summary and not _is_generic_derived_daily_summary(base_summary):
        parts.append(base_summary)

    decision_summary = _decision_summary(signal_context)
    if decision_summary and decision_summary not in parts:
        parts.append(decision_summary)

    next_actions = signal_context.get("action_summaries") or signal_context.get("planned_actions") or []
    if next_actions:
        parts.append("Planned work: " + "; ".join(next_actions[:2]))
    elif report.summary.next_action and report.summary.next_action not in parts:
        parts.append("Next action: " + report.summary.next_action)

    summary = " ".join(part.strip() for part in parts if part)
    return summary or None


def _decision_summary(signal_context: Mapping[str, Any]) -> str | None:
    direct_summary = _first_text(
        signal_context.get("decision_summary"),
        signal_context.get("decision_headline"),
    )
    if direct_summary:
        return direct_summary

    if signal_context.get("next_round_should_start") is True:
        round_index = signal_context.get("next_round")
        target_stage = signal_context.get("next_round_target_stage")
        primary_reason = signal_context.get("next_round_primary_reason")
        message = f"Next round {round_index} scheduled" if round_index is not None else "Next round scheduled"
        if target_stage:
            message += f" for {target_stage}"
        message += "."
        if primary_reason:
            message += f" {primary_reason}"
        return message

    if signal_context.get("should_stop") is True:
        reason = signal_context.get("stop_reason")
        if reason:
            return f"Stopped because {reason}."
        triggered = signal_context.get("triggered_stop_conditions") or []
        if triggered:
            return "Stopped after triggering: " + ", ".join(triggered) + "."
        return "Stopped after a stop condition was triggered."

    if signal_context.get("manual_review_required"):
        reason = _first_text(
            *(signal_context.get("manual_review_signals") or []),
            signal_context.get("next_round_primary_reason"),
            signal_context.get("stop_reason"),
        )
        if reason:
            return f"Needs manual review: {reason}"
        return "Needs manual review before the next round can be scheduled."

    if signal_context.get("next_round_status") == "no_retry_needed":
        return "No next round was scheduled."
    return None


def _build_decision_watch_item(
    report: RunReport,
    signal_context: Mapping[str, Any],
) -> ReportItem | None:
    if not _has_decision_signal(signal_context):
        return None
    return ReportItem(
        item_id=report.summary.run_id,
        name=report.summary.template_name or report.summary.run_id,
        status=_decision_item_status(report, signal_context),
        summary=_compose_run_summary(report, signal_context),
        facts=_decision_item_facts(report, signal_context),
        notes=signal_context.get("manual_review_signals", [])[:3],
    )


def _build_decision_section_item(
    report: RunReport,
    signal_context: Mapping[str, Any],
) -> ReportItem | None:
    if not _has_decision_signal(signal_context):
        return None
    notes = _unique_texts(
        (signal_context.get("manual_review_signals") or [])
        + (signal_context.get("execution_hints") or [])
        + (signal_context.get("watch_signals") or [])
    )
    return ReportItem(
        item_id=report.summary.run_id,
        name=report.summary.template_name or report.summary.run_id,
        status=_decision_item_status(report, signal_context),
        summary=_compose_run_summary(report, signal_context),
        source=report.summary.run_id,
        facts=_decision_item_facts(report, signal_context),
        notes=notes[:5],
    )


def _decision_item_facts(
    report: RunReport,
    signal_context: Mapping[str, Any],
) -> list[Fact]:
    facts = [
        Fact(label="run_id", value=report.summary.run_id),
        Fact(label="template", value=report.summary.template_name),
    ]
    _append_fact(facts, "stop_reason", signal_context.get("stop_reason"))
    _append_fact(facts, "triggered_stop_conditions", signal_context.get("triggered_stop_conditions"))
    _append_fact(facts, "next_round_status", signal_context.get("next_round_status"))
    _append_fact(facts, "next_round", signal_context.get("next_round"))
    _append_fact(facts, "next_round_target_stage", signal_context.get("next_round_target_stage"))
    _append_fact(facts, "planned_actions", signal_context.get("planned_actions"))
    _append_fact(facts, "execution_hints", signal_context.get("execution_hints"))
    if signal_context.get("manual_review_required"):
        facts.append(Fact(label="manual_review_required", value=True))
    return facts


def _decision_item_status(report: RunReport, signal_context: Mapping[str, Any]) -> str:
    decision_status = _text_value(signal_context.get("decision_status"))
    if decision_status in {"stopped", "scheduled", "needs_review", "no_retry_needed"}:
        return decision_status
    if signal_context.get("should_stop") is True or signal_context.get("next_round_status") == "stopped":
        return "stopped"
    if signal_context.get("next_round_should_start") is True:
        return "scheduled"
    if signal_context.get("manual_review_required"):
        return "needs_review"
    return report.summary.status or "observed"


def _has_decision_signal(signal_context: Mapping[str, Any]) -> bool:
    return any(
        (
            signal_context.get("decision_summary"),
            signal_context.get("decision_headline"),
            signal_context.get("should_stop") is True,
            signal_context.get("manual_review_required"),
            signal_context.get("next_round_should_start") is True,
            signal_context.get("execution_hints"),
            signal_context.get("planned_actions"),
            signal_context.get("watch_signals"),
        )
    )


def _append_fact(facts: list[Fact], label: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    facts.append(Fact(label=label, value=value))


def _lookup_value(report: RunReport, *keys: str) -> Any:
    extra_value = _lookup_extra_value(report, *keys)
    if extra_value not in (None, "", [], {}):
        return extra_value
    return _lookup_fact_value(report, *keys)


def _lookup_extra_value(report: RunReport, *keys: str) -> Any:
    for payload in _iter_extra_payloads(report):
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _lookup_fact_value(report: RunReport, *labels: str) -> Any:
    wanted = {label.lower() for label in labels}
    for fact in _iter_fact_payloads(report):
        if fact.label.lower() in wanted and fact.value not in (None, "", [], {}):
            return fact.value
    return None


def _iter_extra_payloads(report: RunReport):
    if report.summary.extra:
        yield report.summary.extra
    if report.extra:
        yield report.extra
    if report.daily_summary and report.daily_summary.extra:
        yield report.daily_summary.extra
    if report.promotion_candidate_summary and report.promotion_candidate_summary.extra:
        yield report.promotion_candidate_summary.extra
    for item in report.promotion_candidates:
        if item.extra:
            yield item.extra
    for section in report.extra_sections:
        if section.extra:
            yield section.extra
        for item in section.items:
            if item.extra:
                yield item.extra
    if report.daily_summary:
        for item in (
            report.daily_summary.new_templates
            + report.daily_summary.new_locator_strategies
            + report.daily_summary.new_failure_fix_strategies
            + report.daily_summary.candidate_platform_skills
            + report.daily_summary.watch_items
        ):
            if item.extra:
                yield item.extra


def _iter_fact_payloads(report: RunReport):
    yield from report.summary.facts
    if report.daily_summary:
        yield from report.daily_summary.facts
        for item in (
            report.daily_summary.new_templates
            + report.daily_summary.new_locator_strategies
            + report.daily_summary.new_failure_fix_strategies
            + report.daily_summary.candidate_platform_skills
            + report.daily_summary.watch_items
        ):
            yield from item.facts
    if report.promotion_candidate_summary:
        yield from report.promotion_candidate_summary.facts
        for item in report.promotion_candidate_summary.candidates:
            yield from item.facts
    for item in report.promotion_candidates:
        yield from item.facts
    for section in report.extra_sections:
        yield from section.facts
        for item in section.items:
            yield from item.facts


def _collect_execution_hints(
    report: RunReport,
    actions: list[dict[str, Any]],
    round_input: Mapping[str, Any],
) -> list[str]:
    hints: list[str] = []
    for action in actions:
        hint_text = _hint_summary(_mapping_dict(action.get("execution_hints")))
        if not hint_text:
            continue
        label = _action_label(action)
        hints.append(f"{label}: {hint_text}" if label else hint_text)

    round_hint_text = _hint_summary(
        _mapping_dict(round_input.get("execution_hints"))
        or _mapping_dict(_lookup_extra_value(report, "execution_hints"))
    )
    if round_hint_text:
        hints.append(f"round_input: {round_hint_text}")
    return _unique_texts(hints)


def _collect_watch_signals(report: RunReport) -> list[str]:
    signals: list[str] = []
    if report.daily_summary:
        for item in report.daily_summary.watch_items:
            text = _first_text(item.summary, item.name)
            if text:
                signals.append(text)
            signals.extend(item.notes)
    for section in report.extra_sections:
        if not _matches_section_keywords(section, ("watch", "review", "stop", "next round", "next-round", "retry")):
            continue
        section_text = _first_text(section.summary, *section.notes)
        if section_text:
            signals.append(section_text)
        for item in section.items:
            item_text = _first_text(item.summary, item.name)
            if item_text:
                signals.append(item_text)
            signals.extend(item.notes)
    return _unique_texts(signals)


def _collect_section_planned_actions(report: RunReport) -> list[str]:
    actions: list[str] = []
    for section in report.extra_sections:
        if not _matches_section_keywords(section, ("next round", "next-round", "retry", "plan", "handoff")):
            continue
        for item in section.items:
            label = _first_text(item.name, item.summary)
            if label:
                actions.append(label)
    return _unique_texts(actions)


def _collect_section_action_summaries(report: RunReport) -> list[str]:
    summaries: list[str] = []
    for section in report.extra_sections:
        if not _matches_section_keywords(section, ("next round", "next-round", "retry", "plan", "execution")):
            continue
        for item in section.items:
            summary = _first_text(item.summary, item.name)
            if summary:
                summaries.append(summary)
    return _unique_texts(summaries)


def _collect_section_execution_hints(report: RunReport) -> list[str]:
    hints: list[str] = []
    for section in report.extra_sections:
        if not _matches_section_keywords(section, ("execution", "hint", "retry", "next round", "next-round")):
            continue
        for fact in section.facts:
            if _matches_hint_label(fact.label):
                hints.append(f"{fact.label}={_compact_value(fact.value)}")
        for item in section.items:
            for fact in item.facts:
                if _matches_hint_label(fact.label):
                    hints.append(f"{item.name}: {fact.label}={_compact_value(fact.value)}")
            for note in item.notes:
                if _contains_hint_language(note):
                    hints.append(note)
    return _unique_texts(hints)


def _collect_manual_review_signals(
    report: RunReport,
    stop_conditions: Mapping[str, Any],
    next_round_decision: Mapping[str, Any],
    watch_signals: list[str],
) -> list[str]:
    signals: list[str] = []
    if _text_value(stop_conditions.get("status")) == "needs_review":
        signals.append(
            _first_text(
                stop_conditions.get("primary_reason"),
                "Stop conditions require manual review.",
            )
        )
    for condition in _mapping_list(stop_conditions.get("conditions")):
        if _is_review_status(condition.get("status")):
            summary = _first_text(condition.get("summary"), condition.get("condition_type"))
            if summary:
                signals.append(summary)
    if _text_value(next_round_decision.get("status")) == "needs_review":
        signals.append(
            _first_text(
                next_round_decision.get("primary_reason"),
                "Next-round scheduling requires manual review.",
            )
        )
    for signal in watch_signals:
        if _contains_review_language(signal):
            signals.append(signal)
    return _unique_texts(signals)


def _action_label(action: Mapping[str, Any]) -> str | None:
    title = _first_text(action.get("title"), action.get("name"), action.get("action_id"))
    stage = _text_value(action.get("stage"))
    if title and stage:
        return f"{title} [{stage}]"
    return title


def _action_summary(action: Mapping[str, Any]) -> str | None:
    label = _action_label(action)
    reason = _text_value(action.get("reason"))
    expected_outcome = _text_value(action.get("expected_outcome"))
    if label and reason:
        return f"{label}: {reason}"
    if label and expected_outcome:
        return f"{label}: {expected_outcome}"
    return label


def _hint_summary(payload: Mapping[str, Any]) -> str | None:
    if not payload:
        return None
    parts: list[str] = []
    for key, value in sorted(payload.items()):
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={_compact_value(value)}")
        if len(parts) >= 4:
            break
    return "; ".join(parts) or None


def _compact_value(value: Any) -> str:
    if isinstance(value, Mapping):
        pairs = [
            f"{key}:{_compact_value(item)}"
            for key, item in sorted(value.items())
            if item not in (None, "", [], {})
        ]
        return "{" + ", ".join(pairs[:3]) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "[" + ", ".join(_compact_value(item) for item in list(value)[:3]) + "]"
    return str(value)


def _mapping_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_value(value: Any) -> bool | None:
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


def _int_value(value: Any) -> int | None:
    number = _number_value(value)
    if number is None:
        return None
    return int(number)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        results: list[str] = []
        for item in value:
            text = _text_value(item)
            if text:
                results.append(text)
        return results
    text = _text_value(value)
    return [text] if text else []


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                text = _text_value(item)
                if text:
                    return text
            continue
        text = _text_value(value)
        if text:
            return text
    return None


def _unique_texts(values: Any) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        text = _text_value(value)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results


def _top_counts(values: Any, *, limit: int = 3) -> list[str]:
    counter = Counter(
        text
        for text in (_text_value(value) for value in values)
        if text
    )
    return [f"{value} ({count})" for value, count in counter.most_common(limit)]


def _contains_review_language(text: str | None) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(
        keyword in normalized
        for keyword in (
            "manual review",
            "needs review",
            "needs_review",
            "takeover",
            "approval",
            "evidence review",
            "人工",
            "审查",
            "审核",
            "接管",
        )
    )


def _is_review_status(value: Any) -> bool:
    normalized = (_text_value(value) or "").lower()
    return normalized in {"needs_review", "manual_review", "pending_review"}


def _matches_section_keywords(section: SectionBlock, keywords: tuple[str, ...]) -> bool:
    haystack = " ".join(
        part
        for part in (
            section.title,
            section.summary,
            " ".join(section.notes),
        )
        if part
    ).lower()
    return any(keyword in haystack for keyword in keywords)


def _matches_hint_label(label: str | None) -> bool:
    normalized = (label or "").strip().lower()
    return normalized in {"execution_hints", "execution_hint", "hint", "hints", "planned_actions", "next_action"}


def _contains_hint_language(text: str | None) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(
        keyword in normalized
        for keyword in ("hint", "locator", "selector", "network", "execution", "下一轮")
    )


def _is_generic_derived_daily_summary(text: str | None) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith("derived daily summary from the current run report")
