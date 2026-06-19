from __future__ import annotations

import json
from typing import Any

from .models import (
    ArtifactRef,
    DailySummary,
    FailureCluster,
    Fact,
    ModelEvaluation,
    PlatformDailyReport,
    PromotionCandidateSummary,
    ProgressCounter,
    ReportItem,
    RunReport,
    RunSummary,
    SectionBlock,
    SkillInventorySummary,
    coerce_run_report,
)


def render_run_report_markdown(report: RunReport | dict[str, Any]) -> str:
    normalized = coerce_run_report(report)
    normalized = _with_derived_sections(normalized)
    lines = [f"# Run Report: {normalized.summary.run_id}", ""]

    lines.extend(_render_summary(normalized.summary))
    lines.extend(_render_item_section("Page Entries", normalized.page_entries, "No page entries recorded."))
    lines.extend(_render_item_section("Feature Points", normalized.feature_points, "No feature points recorded."))
    lines.extend(_render_item_section("Success Items", normalized.success_items, "No successful items recorded."))
    lines.extend(_render_item_section("Failure Items", normalized.failure_items, "No failed items recorded."))
    lines.extend(_render_failure_clusters(normalized.failure_clusters))
    lines.extend(_render_artifact_section("Key Artifacts", normalized.key_artifacts, "No key artifacts recorded."))
    lines.extend(_render_item_section("Network Highlights", normalized.network_highlights, "No network highlights recorded."))
    lines.extend(_render_fact_section("Data Observations", normalized.data_observations, "No data observations recorded."))
    lines.extend(
        _render_fact_section(
            "Execution Efficiency Observations",
            normalized.efficiency_observations,
            "No efficiency observations recorded.",
        )
    )
    lines.extend(_render_item_section("Project Assets", normalized.project_assets, "No project assets recorded."))
    lines.extend(
        _render_item_section(
            "Promotion Candidates",
            normalized.promotion_candidates,
            "No promotion candidates recorded.",
        )
    )
    lines.extend(_render_models(normalized.model_evaluations))
    lines.extend(_render_daily_summary(normalized.daily_summary))
    lines.extend(_render_section_block(normalized.model_comparison_summary, default_title="Model Comparison Summary"))
    lines.extend(_render_skill_inventory_summary(normalized.skill_inventory_summary))
    lines.extend(_render_promotion_candidate_summary(normalized.promotion_candidate_summary))
    lines.extend(_render_extra_sections(normalized.extra_sections))

    if normalized.notes:
        lines.append("## Notes")
        lines.append("")
        for note in normalized.notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_platform_daily_report_markdown(
    report: PlatformDailyReport | dict[str, Any],
) -> str:
    normalized = PlatformDailyReport.from_value(report)
    lines = [f"# Platform Daily Report: {normalized.report_date or 'unknown-date'}", ""]
    if normalized.summary:
        lines.append(normalized.summary)
        lines.append("")
    if normalized.facts:
        lines.extend(
            _render_fact_section(
                "Platform Facts",
                normalized.facts,
                "No platform facts recorded.",
            )
        )
    lines.extend(
        _render_item_section(
            "Run Summaries",
            normalized.run_summaries,
            "No run summaries recorded.",
        )
    )
    lines.extend(
        _render_fact_section(
            "Execution Efficiency Observations",
            normalized.efficiency_observations,
            "No aggregated efficiency observations recorded.",
        )
    )
    lines.extend(_render_daily_summary(normalized.daily_summary))
    lines.extend(
        _render_section_block(
            normalized.model_comparison_summary,
            default_title="Model Comparison Summary",
        )
    )
    lines.extend(_render_skill_inventory_summary(normalized.skill_inventory_summary))
    lines.extend(_render_promotion_candidate_summary(normalized.promotion_candidate_summary))
    lines.extend(_render_extra_sections(normalized.extra_sections))
    if normalized.notes:
        lines.append("## Notes")
        lines.append("")
        for note in normalized.notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _with_derived_sections(report: RunReport) -> RunReport:
    daily_summary = report.daily_summary or _derive_daily_summary(report)
    model_comparison_summary = (
        report.model_comparison_summary or _derive_model_comparison_summary(report)
    )
    skill_inventory_summary = (
        report.skill_inventory_summary or _derive_skill_inventory_summary(report)
    )
    promotion_candidate_summary = (
        report.promotion_candidate_summary or _derive_promotion_candidate_summary(report)
    )
    if (
        daily_summary is report.daily_summary
        and model_comparison_summary is report.model_comparison_summary
        and skill_inventory_summary is report.skill_inventory_summary
        and promotion_candidate_summary is report.promotion_candidate_summary
    ):
        return report
    return RunReport(
        summary=report.summary,
        page_entries=report.page_entries,
        feature_points=report.feature_points,
        success_items=report.success_items,
        failure_items=report.failure_items,
        failure_clusters=report.failure_clusters,
        key_artifacts=report.key_artifacts,
        network_highlights=report.network_highlights,
        data_observations=report.data_observations,
        efficiency_observations=report.efficiency_observations,
        project_assets=report.project_assets,
        promotion_candidates=report.promotion_candidates,
        model_evaluations=report.model_evaluations,
        daily_summary=daily_summary,
        model_comparison_summary=model_comparison_summary,
        skill_inventory_summary=skill_inventory_summary,
        promotion_candidate_summary=promotion_candidate_summary,
        extra_sections=report.extra_sections,
        notes=report.notes,
        extra=report.extra,
    )


def _render_summary(summary: RunSummary) -> list[str]:
    lines = ["## Run Summary", ""]
    summary_pairs = [
        ("Run ID", summary.run_id),
        ("Status", summary.status),
        ("Project", summary.project_name),
        ("Template", summary.template_name),
        ("Started At", summary.started_at),
        ("Finished At", summary.finished_at),
        ("Duration", _format_duration(summary.duration_seconds)),
        ("Current Round", summary.current_round),
        ("Discovery Round", summary.discovery_round),
        ("Verification Round", summary.verification_round),
        ("Attribution Round", summary.attribution_round),
        ("Stop Reason", summary.stop_reason),
        ("Next Action", summary.next_action),
    ]
    for label, value in summary_pairs:
        if value is None:
            continue
        lines.append(f"- {label}: {_format_inline(value)}")
    if summary.counts:
        lines.append("- Counters:")
        for counter in summary.counts:
            lines.append(f"  - {_format_counter(counter)}")
    if summary.facts:
        lines.append("- Facts:")
        for fact in summary.facts:
            lines.append(f"  - {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
    if summary.notes:
        lines.append("- Notes:")
        for note in summary.notes:
            lines.append(f"  - {note}")
    lines.append("")
    return lines


def _derive_daily_summary(report: RunReport) -> DailySummary | None:
    facts: list[Fact] = []
    if report.summary.template_name:
        facts.append(Fact(label="template", value=report.summary.template_name))
    if report.summary.status:
        facts.append(Fact(label="run_status", value=report.summary.status))
    if report.summary.duration_seconds is not None:
        facts.append(Fact(label="duration_seconds", value=report.summary.duration_seconds))
    if report.efficiency_observations:
        facts.extend(report.efficiency_observations[:3])

    new_templates: list[ReportItem] = []
    if report.summary.template_name:
        new_templates.append(
            ReportItem(
                name=report.summary.template_name,
                status=report.summary.status,
                summary="Template participated in the latest run.",
            )
        )

    new_locator_strategies = [
        item for item in report.project_assets if _matches_skill_hint(item, ("locator", "selector"))
    ]
    new_failure_fix_strategies = [
        item for item in report.failure_items if _matches_skill_hint(item, ("fix", "retry", "repair"))
    ]
    watch_items = list(report.failure_items[:5] or [])
    if report.failure_clusters:
        watch_items.extend(
            ReportItem(
                item_id=cluster.cluster_id,
                name=cluster.category,
                status=cluster.status,
                summary=cluster.summary,
            )
            for cluster in report.failure_clusters[:3]
        )
    candidate_platform_skills = [
        item
        for item in (report.promotion_candidates + report.project_assets)
        if _matches_skill_hint(item, ("skill", "strategy", "template", "rule"))
    ][:5]

    if not any(
        (
            facts,
            new_templates,
            new_locator_strategies,
            new_failure_fix_strategies,
            candidate_platform_skills,
            watch_items,
        )
    ):
        return None
    return DailySummary(
        date=report.summary.finished_at or report.summary.started_at,
        summary="Derived daily summary from the current run report.",
        new_templates=new_templates,
        new_locator_strategies=new_locator_strategies,
        new_failure_fix_strategies=new_failure_fix_strategies,
        candidate_platform_skills=candidate_platform_skills,
        watch_items=watch_items[:5],
        facts=facts,
        notes=["This daily summary was auto-derived because no explicit daily_summary payload was provided."],
    )


def _derive_model_comparison_summary(report: RunReport) -> SectionBlock | None:
    if not report.model_evaluations:
        return None
    items: list[ReportItem] = []
    for model in report.model_evaluations:
        summary_parts: list[str] = []
        if model.comparison_summary:
            summary_parts.append(model.comparison_summary)
        if model.joined_discovery is not None:
            summary_parts.append(
                "joined discovery" if model.joined_discovery else "did not join discovery"
            )
        if model.joined_attribution is not None:
            summary_parts.append(
                "joined attribution" if model.joined_attribution else "did not join attribution"
            )
        if model.completion_rate is not None:
            summary_parts.append(f"completion { _format_inline(model.completion_rate) }")
        if model.response_stability:
            summary_parts.append(f"stability {model.response_stability}")
        if model.average_latency_ms is not None:
            summary_parts.append(f"latency {_format_inline(model.average_latency_ms)} ms")
        if model.structured_output_stability:
            summary_parts.append(f"structured output {model.structured_output_stability}")
        if model.recommended_role:
            summary_parts.append(f"recommended role: {model.recommended_role}")
        items.append(
            ReportItem(
                name=model.model_name,
                status="reviewed",
                summary="; ".join(summary_parts) or model.summary,
                facts=[
                    Fact(label="precheck_tags", value=model.precheck_tags),
                    Fact(label="joined_discovery", value=model.joined_discovery),
                    Fact(label="joined_attribution", value=model.joined_attribution),
                    Fact(label="completion_rate", value=model.completion_rate),
                    Fact(label="response_stability", value=model.response_stability),
                    Fact(label="average_latency_ms", value=model.average_latency_ms),
                    Fact(
                        label="structured_output_stability",
                        value=model.structured_output_stability,
                    ),
                ],
            )
        )
    return SectionBlock(
        title="Model Comparison Summary",
        summary="Derived model comparison summary from model evaluations.",
        items=items,
        notes=["This section was auto-derived because no explicit model_comparison_summary payload was provided."],
    )


def _derive_skill_inventory_summary(report: RunReport) -> SkillInventorySummary | None:
    runtime_skills = [
        item for item in report.project_assets if _matches_skill_hint(item, ("runtime", "executor", "strategy"))
    ]
    project_skills = [
        item for item in report.project_assets if _matches_skill_hint(item, ("template", "project", "rule"))
    ]
    platform_candidates = list(report.promotion_candidates[:5])
    if not any((runtime_skills, project_skills, platform_candidates)):
        return None
    return SkillInventorySummary(
        summary="Derived skill inventory summary from project assets and promotion candidates.",
        runtime_skills=runtime_skills[:5],
        project_skills=project_skills[:5],
        platform_candidates=platform_candidates,
        notes=["This summary was auto-derived because no explicit skill_inventory_summary payload was provided."],
    )


def _derive_promotion_candidate_summary(report: RunReport) -> PromotionCandidateSummary | None:
    candidates = list(report.promotion_candidates[:8])
    if not candidates:
        return None
    return PromotionCandidateSummary(
        summary="Derived platform promotion candidate summary from promotion_candidates.",
        candidates=candidates,
        approval_notes=[
            "Auto-derived candidates still require evidence review before platform-level promotion."
        ],
        evidence_requirements=[
            "successful verification evidence",
            "repeatability across runs or models",
        ],
        notes=["This summary was auto-derived because no explicit promotion_candidate_summary payload was provided."],
    )


def _render_item_section(title: str, items: list[ReportItem], empty_message: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines.append(f"- {empty_message}")
        lines.append("")
        return lines
    for item in items:
        lines.extend(_render_report_item(item))
    lines.append("")
    return lines


def _render_report_item(item: ReportItem) -> list[str]:
    headline_parts: list[str] = []
    if item.status:
        headline_parts.append(f"[{item.status}]")
    if item.item_id:
        headline_parts.append(f"`{item.item_id}`")
    headline_parts.append(item.name)
    lines = [f"- {' '.join(headline_parts)}"]
    if item.summary:
        lines.append(f"  - summary: {item.summary}")
    if item.source:
        lines.append(f"  - source: `{item.source}`")
    if item.owner:
        lines.append(f"  - owner: `{item.owner}`")
    if item.tags:
        lines.append(f"  - tags: {', '.join(item.tags)}")
    for fact in item.facts:
        lines.append(f"  - {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
    if item.artifacts:
        for artifact in item.artifacts:
            lines.append(f"  - artifact: {_format_artifact(artifact)}")
    for note in item.notes:
        lines.append(f"  - note: {note}")
    for key, value in sorted(item.extra.items()):
        lines.append(f"  - {key}: {_format_inline(value)}")
    return lines


def _render_failure_clusters(clusters: list[FailureCluster]) -> list[str]:
    lines = ["## Failure Clusters", ""]
    if not clusters:
        lines.append("- No failure clusters recorded.")
        lines.append("")
        return lines
    for cluster in clusters:
        headline_parts: list[str] = []
        if cluster.status:
            headline_parts.append(f"[{cluster.status}]")
        if cluster.cluster_id:
            headline_parts.append(f"`{cluster.cluster_id}`")
        headline_parts.append(cluster.category)
        lines.append(f"- {' '.join(headline_parts)}")
        if cluster.summary:
            lines.append(f"  - summary: {cluster.summary}")
        if cluster.root_cause:
            lines.append(f"  - root cause: {cluster.root_cause}")
        if cluster.action_level:
            lines.append(f"  - action level: `{cluster.action_level}`")
        if cluster.recommendation:
            lines.append(f"  - recommendation: {cluster.recommendation}")
        if cluster.related_items:
            lines.append(f"  - related items: {', '.join(cluster.related_items)}")
        for fact in cluster.facts:
            lines.append(f"  - {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
        for artifact in cluster.artifacts:
            lines.append(f"  - artifact: {_format_artifact(artifact)}")
        for note in cluster.notes:
            lines.append(f"  - note: {note}")
        for key, value in sorted(cluster.extra.items()):
            lines.append(f"  - {key}: {_format_inline(value)}")
    lines.append("")
    return lines


def _render_artifact_section(title: str, artifacts: list[ArtifactRef], empty_message: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not artifacts:
        lines.append(f"- {empty_message}")
        lines.append("")
        return lines
    for artifact in artifacts:
        lines.append(f"- {_format_artifact(artifact)}")
    lines.append("")
    return lines


def _render_fact_section(title: str, facts: list[Fact], empty_message: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not facts:
        lines.append(f"- {empty_message}")
        lines.append("")
        return lines
    for fact in facts:
        lines.append(f"- {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
    lines.append("")
    return lines


def _render_models(models: list[ModelEvaluation]) -> list[str]:
    lines = ["## Model Evaluations", ""]
    if not models:
        lines.append("- No model evaluations recorded.")
        lines.append("")
        return lines
    for model in models:
        lines.append(f"- `{model.model_name}`")
        if model.summary:
            lines.append(f"  - summary: {model.summary}")
        if model.precheck_tags:
            lines.append(f"  - precheck tags: {', '.join(model.precheck_tags)}")
        if model.participated_stages:
            lines.append(f"  - participated stages: {', '.join(model.participated_stages)}")
        if model.joined_discovery is not None:
            lines.append(f"  - joined discovery: {_format_inline(model.joined_discovery)}")
        if model.joined_attribution is not None:
            lines.append(f"  - joined attribution: {_format_inline(model.joined_attribution)}")
        if model.comparison_summary:
            lines.append(f"  - comparison summary: {model.comparison_summary}")
        if model.completion_rate is not None:
            lines.append(f"  - completion rate: {_format_inline(model.completion_rate)}")
        if model.response_stability:
            lines.append(f"  - response stability: {model.response_stability}")
        if model.average_latency_ms is not None:
            lines.append(f"  - average latency ms: {_format_inline(model.average_latency_ms)}")
        if model.structured_output_stability:
            lines.append(f"  - structured output stability: {model.structured_output_stability}")
        if model.recommended_role:
            lines.append(f"  - recommended role: {model.recommended_role}")
        for fact in model.facts:
            lines.append(f"  - {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
        for note in model.notes:
            lines.append(f"  - note: {note}")
        for key, value in sorted(model.extra.items()):
            lines.append(f"  - {key}: {_format_inline(value)}")
    lines.append("")
    return lines


def _render_daily_summary(summary: DailySummary | None) -> list[str]:
    if summary is None:
        return []

    lines = ["## Daily Summary", ""]
    if summary.date:
        lines.append(f"- Date: {summary.date}")
    if summary.summary:
        lines.append(f"- Summary: {summary.summary}")
    for fact in summary.facts:
        lines.append(f"- {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
    lines.extend(
        _render_nested_item_group(
            "New Templates",
            summary.new_templates,
            "No new templates recorded.",
        )
    )
    lines.extend(
        _render_nested_item_group(
            "New Locator Strategies",
            summary.new_locator_strategies,
            "No new locator strategies recorded.",
        )
    )
    lines.extend(
        _render_nested_item_group(
            "New Failure Fix Strategies",
            summary.new_failure_fix_strategies,
            "No new failure fix strategies recorded.",
        )
    )
    lines.extend(
        _render_nested_item_group(
            "Candidate Platform Skills",
            summary.candidate_platform_skills,
            "No candidate platform skills recorded.",
        )
    )
    lines.extend(
        _render_nested_item_group(
            "Watch Items",
            summary.watch_items,
            "No watch items recorded.",
        )
    )
    for note in summary.notes:
        lines.append(f"- note: {note}")
    for key, value in sorted(summary.extra.items()):
        lines.append(f"- {key}: {_format_inline(value)}")
    lines.append("")
    return lines


def _render_skill_inventory_summary(summary: SkillInventorySummary | None) -> list[str]:
    if summary is None:
        return []

    lines = ["## Skill Inventory Summary", ""]
    if summary.summary:
        lines.append(summary.summary)
        lines.append("")
    for fact in summary.facts:
        lines.append(f"- {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
    lines.extend(
        _render_nested_item_group(
            "Runtime Skills",
            summary.runtime_skills,
            "No runtime skills recorded.",
        )
    )
    lines.extend(
        _render_nested_item_group(
            "Project Skills",
            summary.project_skills,
            "No project skills recorded.",
        )
    )
    lines.extend(
        _render_nested_item_group(
            "Platform Candidates",
            summary.platform_candidates,
            "No platform candidates recorded.",
        )
    )
    for note in summary.notes:
        lines.append(f"- note: {note}")
    for key, value in sorted(summary.extra.items()):
        lines.append(f"- {key}: {_format_inline(value)}")
    lines.append("")
    return lines


def _render_promotion_candidate_summary(summary: PromotionCandidateSummary | None) -> list[str]:
    if summary is None:
        return []

    lines = ["## Promotion Candidate Summary", ""]
    if summary.summary:
        lines.append(summary.summary)
        lines.append("")
    for fact in summary.facts:
        lines.append(f"- {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
    lines.extend(
        _render_nested_item_group(
            "Candidates",
            summary.candidates,
            "No promotion candidate summary items recorded.",
        )
    )
    if summary.approval_notes:
        lines.append("- Approval Notes:")
        for note in summary.approval_notes:
            lines.append(f"  - {note}")
    if summary.evidence_requirements:
        lines.append("- Evidence Requirements:")
        for requirement in summary.evidence_requirements:
            lines.append(f"  - {requirement}")
    for note in summary.notes:
        lines.append(f"- note: {note}")
    for key, value in sorted(summary.extra.items()):
        lines.append(f"- {key}: {_format_inline(value)}")
    lines.append("")
    return lines


def _render_section_block(section: SectionBlock | None, default_title: str | None = None) -> list[str]:
    if section is None:
        return []
    if default_title and section.title == "Additional Section":
        section = SectionBlock(
            title=default_title,
            summary=section.summary,
            facts=section.facts,
            items=section.items,
            notes=section.notes,
            markdown=section.markdown,
            extra=section.extra,
        )
    return _render_extra_sections([section])


def _render_extra_sections(sections: list[SectionBlock]) -> list[str]:
    lines: list[str] = []
    for section in sections:
        lines.append(f"## {section.title}")
        lines.append("")
        if section.summary:
            lines.append(section.summary)
            lines.append("")
        if section.facts:
            for fact in section.facts:
                lines.append(f"- {fact.label}: {_format_inline(fact.value)}{_suffix_note(fact.note)}")
        if section.items:
            for item in section.items:
                lines.extend(_render_report_item(item))
        if section.notes:
            for note in section.notes:
                lines.append(f"- note: {note}")
        if section.markdown:
            lines.append(section.markdown.rstrip())
        for key, value in sorted(section.extra.items()):
            lines.append(f"- {key}: {_format_inline(value)}")
        lines.append("")
    return lines


def _render_nested_item_group(title: str, items: list[ReportItem], empty_message: str) -> list[str]:
    lines = [f"- {title}:"]
    if not items:
        lines.append(f"  - {empty_message}")
        return lines
    for item in items:
        rendered = _render_report_item(item)
        if not rendered:
            continue
        lines.append(f"  {rendered[0]}")
        for line in rendered[1:]:
            lines.append(f"  {line}")
    return lines


def _matches_skill_hint(item: ReportItem, keywords: tuple[str, ...]) -> bool:
    haystack = " ".join(
        part
        for part in (
            item.name,
            item.summary,
            item.source,
            item.owner,
            " ".join(item.tags),
        )
        if part
    ).lower()
    return any(keyword in haystack for keyword in keywords)


def _format_artifact(artifact: ArtifactRef) -> str:
    formatted = f"`{artifact.label}` ({artifact.kind})"
    if artifact.path:
        formatted += f": `{artifact.path}`"
    if artifact.note:
        formatted += f" - {artifact.note}"
    return formatted


def _format_counter(counter: ProgressCounter) -> str:
    if counter.completed is not None and counter.total is not None:
        ratio = counter.ratio
        suffix = f" ({ratio:.0%})" if ratio is not None else ""
        text = f"{counter.label}: {counter.completed}/{counter.total}{suffix}"
    elif counter.value is not None:
        text = f"{counter.label}: {_format_inline(counter.value)}"
    else:
        text = counter.label
    if counter.unit and counter.value is not None and counter.completed is None:
        text += f" {counter.unit}"
    if counter.note:
        text += f" - {counter.note}"
    return text


def _format_inline(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_format_inline(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def _suffix_note(note: str | None) -> str:
    if not note:
        return ""
    return f" ({note})"


def _format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
