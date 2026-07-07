from __future__ import annotations

import json
from pathlib import Path

from prototype.stage2.app.iteration.writer import write_iteration_artifacts
from prototype.stage2.app.reporting import build_platform_daily_report, render_run_report_markdown
from prototype.stage2.app.reporting.models import ArtifactRef, ReportItem, RunReport, RunSummary


def _write_promotion_iteration_artifacts(
    root: Path,
    *,
    run_id: str,
    run_status: str = "completed",
    include_failure: bool = False,
) -> Path:
    run_report = {
        "summary": {
            "run_id": run_id,
            "status": run_status,
            "template_name": "sample-template",
        },
        "success_items": [
            {
                "item_id": f"{run_id}-success-001",
                "name": "线上备案申请提交",
                "status": "completed",
                "summary": "Submit flow passed and can be considered for baseline freeze.",
                "artifacts": [
                    {
                        "label": "submit-success.png",
                        "path": str(root / f"{run_id}-submit-success.png"),
                    }
                ],
            }
        ],
        "failure_items": (
            [
                {
                    "item_id": f"{run_id}-failure-001",
                    "name": "前端校验仍有必填项缺失",
                    "status": "blocked",
                    "summary": "Visible validation errors still need to be cleared before retry.",
                }
            ]
            if include_failure
            else []
        ),
    }
    write_iteration_artifacts(
        root,
        run_report=run_report,
        status_snapshot={"run_id": run_id, "status": run_status},
        attempts=[],
    )
    return root / "promotion_candidates.json"


def test_iteration_writer_persists_structured_promotion_candidate_summary(tmp_path) -> None:
    payload_path = _write_promotion_iteration_artifacts(tmp_path, run_id="run-promote")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    summary = payload["promotion_candidate_summary"]
    candidate = payload["candidates"][0]

    assert summary["review_status"] == "needs_review"
    assert summary["manual_review_required"] is True
    assert summary["promotion_target_breakdown"]["project_baseline_freeze"] == 1
    assert summary["promotion_recommendation_breakdown"]["freeze_project_baseline"] == 1
    assert summary["baseline_freeze_candidate_ids"] == ["run-promote-success-001"]

    assert candidate["review_status"] == "ready_for_review"
    assert candidate["promotion_target"] == "project_baseline_freeze"
    assert candidate["promotion_recommendation"] == "freeze_project_baseline"
    assert candidate["needs_manual_review"] is True
    assert "successful verification evidence" in candidate["evidence_requirements"]


def test_run_report_markdown_uses_structured_promotion_candidate_artifact(tmp_path) -> None:
    payload_path = _write_promotion_iteration_artifacts(tmp_path, run_id="run-report")
    report = RunReport(
        summary=RunSummary(
            run_id="run-report",
            status="completed",
            template_name="sample-template",
        ),
        project_assets=[
            ReportItem(
                name="iteration-artifacts",
                artifacts=[
                    ArtifactRef(
                        label="promotion_candidates.json",
                        path=str(payload_path),
                    )
                ],
            )
        ],
    )

    markdown = render_run_report_markdown(report)

    assert "## Promotion Candidates" in markdown
    assert "promotion_target: project_baseline_freeze" in markdown
    assert "promotion_recommendation: freeze_project_baseline" in markdown
    assert "manual_review_required: true" in markdown
    assert "## Promotion Candidate Summary" in markdown
    assert "baseline_freeze_candidate_count: 1" in markdown


def test_platform_daily_report_aggregates_promotion_candidate_review_breakdowns(tmp_path) -> None:
    ready_payload = _write_promotion_iteration_artifacts(
        tmp_path / "ready",
        run_id="run-ready",
    )
    followup_payload = _write_promotion_iteration_artifacts(
        tmp_path / "followup",
        run_id="run-followup",
        run_status="blocked",
        include_failure=True,
    )
    reports = [
        RunReport(
            summary=RunSummary(
                run_id="run-ready",
                status="completed",
                template_name="sample-template",
            ),
            project_assets=[
                ReportItem(
                    name="iteration-artifacts",
                    artifacts=[ArtifactRef(label="promotion_candidates.json", path=str(ready_payload))],
                )
            ],
        ),
        RunReport(
            summary=RunSummary(
                run_id="run-followup",
                status="blocked",
                template_name="sample-template",
            ),
            project_assets=[
                ReportItem(
                    name="iteration-artifacts",
                    artifacts=[ArtifactRef(label="promotion_candidates.json", path=str(followup_payload))],
                )
            ],
        ),
    ]

    platform_report = build_platform_daily_report(reports)
    summary = platform_report.promotion_candidate_summary

    assert summary is not None
    facts = {fact.label: fact.value for fact in summary.facts}
    assert facts["candidate_count"] == 2
    assert facts["manual_review_required"] is True
    assert facts["baseline_freeze_candidate_count"] == 2
    assert summary.extra["review_status_breakdown"]["ready_for_review"] == 1
    assert summary.extra["review_status_breakdown"]["needs_followup_validation"] == 1
    assert summary.extra["promotion_recommendation_breakdown"]["freeze_project_baseline"] == 2
