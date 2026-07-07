from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.reporting.decision_explainer import (
    build_decision_explanation,
    build_decision_section,
)
from prototype.stage2.app.reporting.daily_digest import build_platform_daily_report
from prototype.stage2.app.reporting.models import ArtifactRef, ReportItem, RunReport, RunSummary
from prototype.stage2.app.reporting.progress_view import render_progress_markdown
from prototype.stage2.app.reporting.report_markdown import render_run_report_markdown


def test_decision_explainer_summarizes_scheduled_retry_with_execution_focus() -> None:
    explanation = build_decision_explanation(
        next_round_decision={
            "status": "scheduled",
            "should_start_next_round": True,
            "next_round": 3,
            "target_stage": "verification",
            "primary_reason": "Open failure clusters were converted into next-round retry actions.",
            "remaining_attempt_budget": 2,
            "scheduled_cluster_ids": ["cluster-001", "cluster-002"],
            "scheduled_action_ids": ["retry-001", "retry-002"],
        },
        retry_plan={
            "status": "planned",
            "goal": "Resolve the open failure clusters and rerun the affected path.",
            "actions": [
                {
                    "action_id": "retry-001",
                    "cluster_id": "cluster-001",
                    "title": "Retry front_validation failure cluster",
                    "stage": "verification",
                    "owner": "agent",
                    "priority": "low",
                    "strategy": "inspect_validation_and_rerun",
                    "reason": "Visible front-end validation must be cleared before submit.",
                    "expected_outcome": "verification should complete without reopening cluster-001.",
                    "execution_hints": {
                        "validation_retry_mode": "inspect_visible_errors",
                        "focus_stage": "verification",
                    },
                }
            ],
        },
        round_input={
            "execution_hints": {
                "validation_retry_mode": "inspect_visible_errors",
                "focus_stage": "verification",
            }
        },
    )

    assert explanation.status == "scheduled"
    assert explanation.headline == "Next round 3 scheduled for verification"
    assert explanation.manual_review_required is False
    assert explanation.scheduled_action_count == 1
    assert explanation.scheduled_cluster_count == 2
    assert explanation.execution_hints["validation_retry_mode"] == "inspect_visible_errors"
    assert explanation.execution_hints["focus_stage"] == "verification"
    assert "inspect visible validation errors before retry" in explanation.summary
    assert "focus on verification" in explanation.summary

    section = build_decision_section(explanation)
    assert section.title == "Decision Explanation"
    assert section.summary == explanation.summary
    assert any(fact.label == "next_round_status" and fact.value == "scheduled" for fact in section.facts)
    assert any(item.item_id == "retry-001" for item in section.items)


def test_decision_explainer_marks_manual_review_and_stop_reasons() -> None:
    explanation = build_decision_explanation(
        stop_conditions={
            "status": "needs_review",
            "should_stop": None,
            "conditions": [
                {
                    "condition_type": "manual_takeover",
                    "status": "manual_review_needed",
                    "summary": "Structured failure clusters indicate that a human takeover or review is likely required.",
                }
            ],
            "notes": ["Stop decisions stay conservative and prefer structured iteration signals over free-text inference."],
        },
        next_round_decision={
            "status": "needs_review",
            "should_start_next_round": None,
            "next_round": 4,
            "primary_reason": "Stop decision requires manual review before scheduling the next round.",
            "scheduled_cluster_ids": ["cluster-009"],
            "scheduled_action_ids": ["retry-009"],
        },
        retry_plan={
            "status": "planned",
            "actions": [
                {
                    "action_id": "retry-009",
                    "cluster_id": "cluster-009",
                    "title": "Retry workflow_branch failure cluster",
                    "strategy": "resume_detected_branch",
                    "execution_hints": {
                        "workflow_retry_mode": "resume_detected_branch",
                        "requires_human_review": True,
                    },
                }
            ],
        },
    )

    assert explanation.status == "needs_review"
    assert explanation.manual_review_required is True
    assert explanation.headline == "Manual review needed before round 4"
    assert explanation.primary_reason == "Stop decision requires manual review before scheduling the next round."
    assert explanation.should_start_next_round is None
    assert explanation.execution_hints["workflow_retry_mode"] == "resume_detected_branch"
    assert explanation.execution_hints["requires_human_review"] is True
    assert any("should not auto-start before review" in note for note in explanation.notes)

    section = build_decision_section(explanation, title="Iteration Handoff")
    assert section.title == "Iteration Handoff"
    assert section.extra["manual_review_required"] is True
    assert section.extra["decision_status"] == "needs_review"
    assert any(fact.label == "manual_review_required" and fact.value is True for fact in section.facts)


def test_decision_explainer_summarizes_policy_review_gate() -> None:
    explanation = build_decision_explanation(
        stop_conditions={
            "status": "needs_review",
            "should_stop": None,
            "conditions": [
                {
                    "condition_type": "manual_takeover",
                    "status": "manual_review_needed",
                    "summary": "高风险提交在执行层被要求先人工审核。",
                }
            ],
        },
        next_round_decision={
            "status": "needs_review",
            "should_start_next_round": None,
            "next_round": 2,
            "primary_reason": "高风险提交需要人工审核后才能继续，当前未自动启动下一轮。",
            "scheduled_cluster_ids": ["cluster-001"],
            "scheduled_action_ids": ["retry-001"],
        },
        retry_plan={
            "status": "planned",
            "actions": [
                {
                    "action_id": "retry-001",
                    "cluster_id": "cluster-001",
                    "title": "Review risky submit policy",
                    "strategy": "human_review_required",
                    "execution_hints": {
                        "requires_human_review": True,
                        "stop_after_current_round": True,
                    },
                }
            ],
        },
    )

    assert explanation.status == "needs_review"
    assert explanation.manual_review_required is True
    assert "requires human review before retry" in explanation.summary


def test_reporting_consumers_reuse_scheduled_decision_explanation(tmp_path) -> None:
    stop_path = tmp_path / "stop_conditions.json"
    next_path = tmp_path / "next_round_decision.json"
    retry_path = tmp_path / "retry_plan.json"
    round_input_path = tmp_path / "round_input.json"

    stop_path.write_text('{"status":"continue","should_stop":false}', encoding="utf-8")
    next_path.write_text(
        """
        {
          "status": "scheduled",
          "should_start_next_round": true,
          "next_round": 3,
          "target_stage": "verification",
          "primary_reason": "Open failure clusters were converted into next-round retry actions.",
          "scheduled_cluster_ids": ["cluster-001"],
          "scheduled_action_ids": ["retry-001"]
        }
        """.strip(),
        encoding="utf-8",
    )
    retry_path.write_text(
        """
        {
          "status": "planned",
          "goal": "Resolve the open failure clusters and rerun the affected path.",
          "actions": [
            {
              "action_id": "retry-001",
              "cluster_id": "cluster-001",
              "title": "Retry front_validation failure cluster",
              "stage": "verification",
              "reason": "Visible front-end validation must be cleared before submit.",
              "execution_hints": {
                "validation_retry_mode": "inspect_visible_errors",
                "focus_stage": "verification"
              }
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )
    round_input_path.write_text(
        """
        {
          "execution_hints": {
            "validation_retry_mode": "inspect_visible_errors",
            "focus_stage": "verification"
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    report = RunReport(
        summary=RunSummary(run_id="run-scheduled", status="running", template_name="sample-template"),
        project_assets=[
            ReportItem(
                name="iteration-artifacts",
                artifacts=[
                    ArtifactRef(label="stop_conditions.json", path=str(stop_path)),
                    ArtifactRef(label="next_round_decision.json", path=str(next_path)),
                    ArtifactRef(label="retry_plan.json", path=str(retry_path)),
                    ArtifactRef(label="round_input.json", path=str(round_input_path)),
                ],
            )
        ],
    )

    progress_markdown = render_progress_markdown(
        {
            "run_id": "run-scheduled",
            "status": "running",
            "stage": "verification",
            "extra": {
                "stop_conditions": {"status": "continue", "should_stop": False},
                "next_round_decision": {
                    "status": "scheduled",
                    "should_start_next_round": True,
                    "next_round": 3,
                    "target_stage": "verification",
                    "primary_reason": "Open failure clusters were converted into next-round retry actions.",
                    "scheduled_cluster_ids": ["cluster-001"],
                    "scheduled_action_ids": ["retry-001"],
                },
                "retry_plan": {
                    "status": "planned",
                    "actions": [
                        {
                            "action_id": "retry-001",
                            "cluster_id": "cluster-001",
                            "title": "Retry front_validation failure cluster",
                            "stage": "verification",
                            "reason": "Visible front-end validation must be cleared before submit.",
                            "execution_hints": {
                                "validation_retry_mode": "inspect_visible_errors",
                                "focus_stage": "verification",
                            },
                        }
                    ],
                },
                "round_input": {
                    "execution_hints": {
                        "validation_retry_mode": "inspect_visible_errors",
                        "focus_stage": "verification",
                    }
                },
            },
        }
    )
    assert "Decision Headline: Next round 3 scheduled for verification" in progress_markdown
    assert "Decision Summary: Next round 3 scheduled for verification." in progress_markdown
    assert "Planned Retry Actions: 1" in progress_markdown
    assert "Execution Focus: inspect visible validation errors before retry; focus on verification" in progress_markdown

    report_markdown = render_run_report_markdown(report)
    assert "Decision Headline: Next round 3 scheduled for verification" in report_markdown
    assert "Decision Summary: Next round 3 scheduled for verification." in report_markdown
    assert "Planned Retry Actions: 1" in report_markdown
    assert "Execution Focus: inspect visible validation errors before retry; focus on verification" in report_markdown

    platform_report = build_platform_daily_report([report])
    run_summary = platform_report.run_summaries[0]
    assert run_summary.status == "scheduled"
    assert run_summary.summary is not None
    assert "Next round 3 scheduled for verification." in run_summary.summary


def test_reporting_consumers_keep_manual_review_gate_consistent(tmp_path) -> None:
    progress_markdown = render_progress_markdown(
        {
            "run_id": "run-review",
            "status": "blocked",
            "stage": "verification",
            "extra": {
                "stop_conditions": {
                    "status": "needs_review",
                    "conditions": [
                        {
                            "condition_type": "manual_takeover",
                            "status": "manual_review_needed",
                            "summary": "Structured failure clusters indicate that a human takeover or review is likely required.",
                        }
                    ],
                },
                "next_round_decision": {
                    "status": "scheduled",
                    "should_start_next_round": True,
                    "next_round": 4,
                    "target_stage": "verification",
                    "primary_reason": "A retry draft exists, but the manual review gate must be cleared first.",
                },
                "retry_plan": {
                    "status": "planned",
                    "actions": [
                        {
                            "action_id": "retry-009",
                            "cluster_id": "cluster-009",
                            "title": "Retry workflow_branch failure cluster",
                            "execution_hints": {
                                "workflow_retry_mode": "resume_detected_branch",
                                "requires_human_review": True,
                            },
                        }
                    ],
                },
            },
        }
    )

    assert "Decision Headline: Manual review needed before round 4" in progress_markdown
    assert "Manual Review Required: true" in progress_markdown
    assert "Decision Summary: Manual review needed before round 4." in progress_markdown

    stop_path = tmp_path / "stop_conditions.json"
    next_path = tmp_path / "next_round_decision.json"
    retry_path = tmp_path / "retry_plan.json"
    stop_path.write_text(
        """
        {
          "status": "needs_review",
          "conditions": [
            {
              "condition_type": "manual_takeover",
              "status": "manual_review_needed",
              "summary": "Structured failure clusters indicate that a human takeover or review is likely required."
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )
    next_path.write_text(
        """
        {
          "status": "scheduled",
          "should_start_next_round": true,
          "next_round": 4,
          "target_stage": "verification",
          "primary_reason": "A retry draft exists, but the manual review gate must be cleared first."
        }
        """.strip(),
        encoding="utf-8",
    )
    retry_path.write_text(
        """
        {
          "status": "planned",
          "actions": [
            {
              "action_id": "retry-009",
              "cluster_id": "cluster-009",
              "title": "Retry workflow_branch failure cluster",
              "execution_hints": {
                "workflow_retry_mode": "resume_detected_branch",
                "requires_human_review": true
              }
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    report = RunReport(
        summary=RunSummary(
            run_id="run-review",
            status="blocked",
            template_name="sample-template",
            extra={
                "stop_conditions": {
                    "status": "needs_review",
                    "conditions": [
                        {
                            "condition_type": "manual_takeover",
                            "status": "manual_review_needed",
                            "summary": "Structured failure clusters indicate that a human takeover or review is likely required.",
                        }
                    ],
                },
                "next_round_decision": {
                    "status": "scheduled",
                    "should_start_next_round": True,
                    "next_round": 4,
                    "target_stage": "verification",
                    "primary_reason": "A retry draft exists, but the manual review gate must be cleared first.",
                },
                "retry_plan": {
                    "status": "planned",
                    "actions": [
                        {
                            "action_id": "retry-009",
                            "cluster_id": "cluster-009",
                            "title": "Retry workflow_branch failure cluster",
                            "execution_hints": {
                                "workflow_retry_mode": "resume_detected_branch",
                                "requires_human_review": True,
                            },
                        }
                    ],
                },
            },
        ),
        project_assets=[
            ReportItem(
                name="iteration-artifacts",
                artifacts=[
                    ArtifactRef(label="stop_conditions.json", path=str(stop_path)),
                    ArtifactRef(label="next_round_decision.json", path=str(next_path)),
                    ArtifactRef(label="retry_plan.json", path=str(retry_path)),
                ],
            )
        ],
    )
    report_markdown = render_run_report_markdown(report)
    assert "Decision Status: needs_review" in report_markdown
    assert "Decision Headline: Manual review needed before round 4" in report_markdown
    assert "Manual Review Required: true" in report_markdown

    platform_report = build_platform_daily_report([report])
    run_summary = platform_report.run_summaries[0]
    assert run_summary.status == "needs_review"
    assert run_summary.summary is not None
    assert "Manual review needed before round 4." in run_summary.summary


def test_reporting_consumers_keep_stopped_decision_consistent(tmp_path) -> None:
    progress_markdown = render_progress_markdown(
        {
            "run_id": "run-stopped",
            "status": "completed",
            "stage": "attribution",
            "extra": {
                "stop_conditions": {
                    "status": "stop",
                    "should_stop": True,
                    "primary_reason": "no_improvement",
                    "triggered_conditions": ["no_improvement", "attempt_budget"],
                },
                "next_round_decision": {
                    "status": "stopped",
                    "should_start_next_round": False,
                    "stop_reason": "no_improvement",
                },
            },
        }
    )

    assert "Decision Status: stopped" in progress_markdown
    assert "Decision Headline: Stopped by no_improvement" in progress_markdown
    assert "Decision Summary: Stopped by no_improvement." in progress_markdown

    stop_path = tmp_path / "stop_conditions.json"
    next_path = tmp_path / "next_round_decision.json"
    stop_path.write_text(
        """
        {
          "status": "stop",
          "should_stop": true,
          "primary_reason": "no_improvement",
          "triggered_conditions": ["no_improvement", "attempt_budget"]
        }
        """.strip(),
        encoding="utf-8",
    )
    next_path.write_text(
        """
        {
          "status": "stopped",
          "should_start_next_round": false,
          "stop_reason": "no_improvement"
        }
        """.strip(),
        encoding="utf-8",
    )

    report = RunReport(
        summary=RunSummary(
            run_id="run-stopped",
            status="completed",
            template_name="sample-template",
        ),
        project_assets=[
            ReportItem(
                name="iteration-artifacts",
                artifacts=[
                    ArtifactRef(label="stop_conditions.json", path=str(stop_path)),
                    ArtifactRef(label="next_round_decision.json", path=str(next_path)),
                ],
            )
        ],
    )
    report_markdown = render_run_report_markdown(report)
    assert "Decision Status: stopped" in report_markdown
    assert "Decision Headline: Stopped by no_improvement" in report_markdown
    assert "Triggered Stop Conditions: no_improvement, attempt_budget" in report_markdown

    platform_report = build_platform_daily_report([report])
    run_summary = platform_report.run_summaries[0]
    assert run_summary.status == "stopped"
    assert run_summary.summary is not None
    assert "Stopped by no_improvement." in run_summary.summary
