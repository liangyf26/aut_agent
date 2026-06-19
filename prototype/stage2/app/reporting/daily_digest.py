from __future__ import annotations

from collections import defaultdict
from typing import Any

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
    chosen_date = report_date or _latest_report_date(derived_runs)
    run_summaries = [_build_run_summary_item(report) for report in derived_runs]
    efficiency_facts = _aggregate_efficiency_facts(derived_runs)
    daily_summary = _aggregate_daily_summary(derived_runs, chosen_date)
    model_comparison = _aggregate_model_comparison_summary(derived_runs)
    skill_inventory = _aggregate_skill_inventory_summary(derived_runs)
    promotion_summary = _aggregate_promotion_candidate_summary(derived_runs)

    facts = [
        Fact(label="run_count", value=len(derived_runs)),
        Fact(label="successful_runs", value=sum(1 for report in derived_runs if _is_success(report.summary.status))),
        Fact(label="failed_runs", value=sum(1 for report in derived_runs if _is_failure(report.summary.status))),
        Fact(label="models_covered", value=sorted(_collect_model_names(derived_runs))),
    ]
    summary = (
        f"Aggregated {len(derived_runs)} run(s) into a platform daily report"
        + (f" for {chosen_date}." if chosen_date else ".")
    )
    notes = [
        "This platform daily report was aggregated from run-level reporting payloads.",
        "Sections fall back to derived summaries when a run did not provide explicit daily or skill summaries.",
    ]
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


def _build_run_summary_item(report: RunReport) -> ReportItem:
    model_names = [model.model_name for model in report.model_evaluations if model.model_name]
    facts = [
        Fact(label="status", value=report.summary.status),
        Fact(label="template", value=report.summary.template_name),
        Fact(label="duration_seconds", value=report.summary.duration_seconds),
        Fact(label="failure_clusters", value=len(report.failure_clusters)),
        Fact(label="promotion_candidates", value=len(report.promotion_candidates)),
    ]
    if report.summary.current_round is not None:
        facts.append(Fact(label="current_round", value=report.summary.current_round))
    if report.summary.stop_reason:
        facts.append(Fact(label="stop_reason", value=report.summary.stop_reason))
    next_round_status = report.summary.extra.get("next_round_status")
    if next_round_status is not None:
        facts.append(Fact(label="next_round_status", value=next_round_status))
    next_round_should_start = report.summary.extra.get("next_round_should_start")
    if next_round_should_start is not None:
        facts.append(Fact(label="next_round_should_start", value=next_round_should_start))
    if model_names:
        facts.append(Fact(label="models", value=model_names))
    return ReportItem(
        item_id=report.summary.run_id,
        name=report.summary.template_name or report.summary.run_id,
        status=report.summary.status,
        summary=report.daily_summary.summary if report.daily_summary else None,
        facts=facts,
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


def _aggregate_daily_summary(reports: list[RunReport], report_date: str | None) -> DailySummary:
    return DailySummary(
        date=report_date,
        summary=f"Platform daily digest aggregated from {len(reports)} run(s).",
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
            for report in reports
            for item in (report.daily_summary.watch_items if report.daily_summary else [])
        ),
        facts=[
            Fact(label="run_count", value=len(reports)),
            Fact(label="templates_covered", value=sorted({report.summary.template_name for report in reports if report.summary.template_name})),
        ],
        notes=["Aggregated from run-level daily summaries."],
    )


def _aggregate_model_comparison_summary(reports: list[RunReport]) -> SectionBlock | None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        for model in report.model_evaluations:
            grouped[model.model_name].append(
                {
                    "model": model,
                    "run_id": report.summary.run_id,
                    "template": report.summary.template_name,
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
        notes = [
            f"Observed in {len(rows)} run(s).",
            f"Templates: {', '.join(sorted({row['template'] for row in rows if row['template']})) or 'unknown'}",
        ]
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
    return PromotionCandidateSummary(
        summary="Cross-run promotion candidate summary aggregated from run reports.",
        candidates=candidates,
        approval_notes=approval_notes,
        evidence_requirements=evidence_requirements,
        facts=[Fact(label="candidate_count", value=len(candidates))],
        notes=["Candidates were deduplicated across the supplied run reports."],
    )


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
