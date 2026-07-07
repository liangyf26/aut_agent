from __future__ import annotations

import json
from pathlib import Path

from prototype.stage2.app.reporting import render_run_report_markdown
from prototype.stage2.app.reporting.models import (
    ArtifactRef,
    Fact,
    PromotionCandidateSummary,
    ReportItem,
    RunReport,
    RunSummary,
)


def _write_promotion_payload(root: Path, payload: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "promotion_candidates.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _iteration_asset(payload_path: Path) -> ReportItem:
    return ReportItem(
        name="iteration-artifacts",
        artifacts=[ArtifactRef(label="promotion_candidates.json", path=str(payload_path))],
    )


def test_run_report_prefers_report_summary_fields_but_merges_artifact_candidates_and_notes(
    tmp_path: Path,
) -> None:
    payload_path = _write_promotion_payload(
        tmp_path,
        {
            "candidates": [
                {
                    "candidate_id": "artifact-candidate-001",
                    "title": "Artifact candidate",
                    "status": "candidate",
                    "reason": "Iteration artifact candidate should replace report-level candidate list.",
                    "source": "iteration",
                    "promotion_level": "project",
                    "review_status": "ready_for_review",
                    "promotion_target": "project_baseline_freeze",
                    "promotion_recommendation": "freeze_project_baseline",
                    "needs_manual_review": True,
                    "evidence": [{"kind": "screenshot"}],
                    "evidence_requirements": ["candidate-level artifact evidence"],
                    "missing_evidence": ["artifact replay log"],
                }
            ],
            "promotion_candidate_summary": {
                "summary": "Artifact summary should lose to report payload.",
                "approval_notes": ["artifact approval note"],
                "evidence_requirements": ["artifact summary evidence"],
                "facts": [
                    {"label": "review_status", "value": "ready_for_review"},
                    {"label": "baseline_freeze_candidate_count", "value": 999},
                ],
                "notes": ["shared-note", "artifact-only-note"],
                "promotion_target_breakdown": {"project_baseline_freeze": 1},
                "promotion_recommendation_breakdown": {"freeze_project_baseline": 1},
            },
        },
    )

    report = RunReport(
        summary=RunSummary(run_id="run-priority", status="completed", template_name="sample-template"),
        promotion_candidates=[
            ReportItem(
                item_id="report-candidate-001",
                name="Report candidate should be replaced",
                status="candidate",
            )
        ],
        promotion_candidate_summary=PromotionCandidateSummary(
            summary="Report summary wins.",
            approval_notes=["report approval note"],
            evidence_requirements=["report summary evidence"],
            facts=[
                Fact(label="review_status", value="needs_review"),
                Fact(label="candidate_count", value=42),
            ],
            notes=["shared-note", "report-only-note"],
            extra={
                "promotion_target_breakdown": {"platform_rule": 2},
                "promotion_recommendation_breakdown": {"promote_platform_rule": 2},
            },
        ),
        project_assets=[_iteration_asset(payload_path)],
    )

    markdown = render_run_report_markdown(report)

    assert "## Promotion Candidates" in markdown
    assert "`artifact-candidate-001` Artifact candidate" in markdown
    assert "report-candidate-001" not in markdown

    assert "## Promotion Candidate Summary" in markdown
    assert "Report summary wins." in markdown
    assert "Artifact summary should lose to report payload." not in markdown
    assert "report approval note" in markdown
    assert "artifact approval note" not in markdown
    assert "report summary evidence" in markdown
    assert "artifact summary evidence" not in markdown
    assert "- candidate_count: 42" in markdown
    assert "- baseline_freeze_candidate_count: 999" not in markdown
    assert '- promotion_target_breakdown: {"platform_rule": 2}' in markdown
    assert '- promotion_recommendation_breakdown: {"promote_platform_rule": 2}' in markdown
    assert "artifact-only-note" in markdown
    assert "report-only-note" in markdown
    assert markdown.count("shared-note") == 1


def test_run_report_auto_derives_promotion_candidate_summary_when_payload_has_candidates_only(
    tmp_path: Path,
) -> None:
    payload_path = _write_promotion_payload(
        tmp_path,
        {
            "candidates": [
                {
                    "candidate_id": "candidate-ready-001",
                    "title": "Ready candidate",
                    "status": "candidate",
                    "reason": "Stable submit path can be reviewed for project baseline freeze.",
                    "review_status": "ready_for_review",
                    "promotion_target": "project_baseline_freeze",
                    "promotion_recommendation": "freeze_project_baseline",
                    "needs_manual_review": True,
                    "evidence": [{"kind": "screenshot"}, {"kind": "run_report"}],
                    "evidence_requirements": [
                        "successful verification evidence",
                        "repeatability across runs or models",
                    ],
                },
                {
                    "candidate_id": "candidate-followup-002",
                    "title": "Follow-up candidate",
                    "status": "candidate",
                    "reason": "Another flow still needs replay on a second environment.",
                    "review_status": "needs_followup_validation",
                    "promotion_target": "project_template",
                    "promotion_recommendation": "collect_more_evidence",
                    "needs_manual_review": False,
                    "evidence": [{"kind": "screenshot"}],
                    "evidence_requirements": ["cross-system replay"],
                    "missing_evidence": ["second-system replay"],
                },
            ]
        },
    )

    report = RunReport(
        summary=RunSummary(run_id="run-auto-derive", status="completed", template_name="sample-template"),
        project_assets=[_iteration_asset(payload_path)],
    )

    markdown = render_run_report_markdown(report)

    assert "## Promotion Candidate Summary" in markdown
    assert "Derived platform promotion candidate summary from promotion_candidates." in markdown
    assert "- candidate_count: 2" in markdown
    assert "- review_status: needs_review" in markdown
    assert "- manual_review_required: true" in markdown
    assert "- baseline_freeze_candidate_count: 1" in markdown
    assert "- ready_for_review_count: 1" in markdown
    assert "- deferred_candidate_count: 1" in markdown
    assert "successful verification evidence" in markdown
    assert "repeatability across runs or models" in markdown
    assert "cross-system replay" in markdown
    assert "This summary was auto-derived because no explicit promotion_candidate_summary payload was provided." in markdown


def test_run_report_markdown_stably_renders_candidate_summary_review_and_evidence_details() -> None:
    report = RunReport(
        summary=RunSummary(run_id="run-render", status="blocked", template_name="sample-template"),
        promotion_candidate_summary=PromotionCandidateSummary(
            summary="Manual review queue for baseline-freeze candidates.",
            candidates=[
                ReportItem(
                    item_id="candidate-001",
                    name="线上备案申请",
                    status="candidate",
                    summary="Needs one more replay before baseline freeze can be approved.",
                    facts=[
                        Fact(label="review_status", value="needs_followup_validation"),
                        Fact(label="promotion_target", value="project_baseline_freeze"),
                        Fact(label="promotion_recommendation", value="freeze_project_baseline"),
                        Fact(label="manual_review_required", value=True),
                    ],
                    notes=[
                        "missing evidence: second-system replay",
                        "missing evidence: baseline owner sign-off",
                    ],
                    extra={
                        "evidence_requirements": [
                            "successful verification evidence",
                            "cross-system replay",
                        ],
                        "missing_evidence": [
                            "second-system replay",
                            "baseline owner sign-off",
                        ],
                    },
                )
            ],
            approval_notes=["Need baseline owner review before freeze."],
            evidence_requirements=[
                "successful verification evidence",
                "cross-system replay",
            ],
            facts=[
                Fact(label="review_status", value="needs_review"),
                Fact(label="baseline_freeze_candidate_count", value=1),
            ],
            extra={
                "baseline_freeze_candidate_ids": ["candidate-001"],
                "review_status_breakdown": {"needs_followup_validation": 1},
            },
        ),
    )

    markdown = render_run_report_markdown(report)

    assert "## Promotion Candidate Summary" in markdown
    assert "Manual review queue for baseline-freeze candidates." in markdown
    assert "- review_status: needs_review" in markdown
    assert "- baseline_freeze_candidate_count: 1" in markdown
    assert "[candidate] `candidate-001` 线上备案申请" in markdown
    assert "review_status: needs_followup_validation" in markdown
    assert "promotion_target: project_baseline_freeze" in markdown
    assert "promotion_recommendation: freeze_project_baseline" in markdown
    assert "manual_review_required: true" in markdown
    assert "note: missing evidence: second-system replay" in markdown
    assert "note: missing evidence: baseline owner sign-off" in markdown
    assert "Need baseline owner review before freeze." in markdown
    assert "successful verification evidence" in markdown
    assert "cross-system replay" in markdown
    assert "- baseline_freeze_candidate_ids: candidate-001" in markdown
    assert '- review_status_breakdown: {"needs_followup_validation": 1}' in markdown
