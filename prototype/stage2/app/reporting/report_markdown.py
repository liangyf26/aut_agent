from __future__ import annotations

import json
from typing import Any

from .models import (
    ArtifactRef,
    FailureCluster,
    Fact,
    ModelEvaluation,
    ProgressCounter,
    ReportItem,
    RunReport,
    RunSummary,
    SectionBlock,
    coerce_run_report,
)


def render_run_report_markdown(report: RunReport | dict[str, Any]) -> str:
    normalized = coerce_run_report(report)
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
    lines.extend(_render_extra_sections(normalized.extra_sections))

    if normalized.notes:
        lines.append("## Notes")
        lines.append("")
        for note in normalized.notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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
