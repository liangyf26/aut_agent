"""
run_report.json / run_report.md writer for Stage E.

Builds an actual ``reporting.RunReport`` dataclass instance (rather than a
raw dict, unlike ``tools/suyuan_submit_loop.py``'s older pipeline) and
renders it with the SAME ``render_run_report_markdown`` function every other
stage2 report consumer already reads — no new report renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from .execution_runner import ExecutionOutcome

from ..goal_loop.models import PAUSED_STATUSES, STATUS_SUCCEEDED, TERMINAL_STATUSES
from ..reporting import (
    ArtifactRef,
    Fact,
    FailureCluster,
    ReportItem,
    RunReport,
    RunSummary,
    render_run_report_markdown,
)
from .execution_runner import STATUS_PASSED


def _execution_goals(engine: "GoalLoopEngine") -> list:
    return [
        goal
        for goal in engine.goals.values()
        if goal.origin and goal.origin.startswith("feature_execution::")
    ]


def _build_success_items(outcomes: list["ExecutionOutcome"]) -> list[ReportItem]:
    items = []
    for outcome in outcomes:
        if outcome.status != STATUS_PASSED:
            continue
        items.append(
            ReportItem(
                item_id=outcome.test_case_id,
                name=outcome.test_case_id,
                status="passed",
                summary=outcome.page_feedback.get("summary"),
                source=outcome.page_id,
                owner=outcome.feature_id,
                facts=[
                    Fact(label="case_kind", value=outcome.case_kind),
                    Fact(label="execution_mode", value=outcome.execution_mode),
                    Fact(
                        label="requires_human_authorization",
                        value=outcome.requires_human_authorization,
                    ),
                ],
                notes=outcome.notes,
            )
        )
    return items


def _build_failure_items(outcomes: list["ExecutionOutcome"]) -> list[ReportItem]:
    items = []
    for outcome in outcomes:
        if outcome.status == STATUS_PASSED:
            continue
        items.append(
            ReportItem(
                item_id=outcome.test_case_id,
                name=outcome.test_case_id,
                status="failed",
                summary=outcome.page_feedback.get("summary") or outcome.failure_reason,
                source=outcome.page_id,
                owner=outcome.feature_id,
                facts=[
                    Fact(label="case_kind", value=outcome.case_kind),
                    Fact(label="failure_reason", value=outcome.failure_reason),
                ],
                notes=outcome.notes,
            )
        )
    return items


def _build_failure_clusters(engine: "GoalLoopEngine") -> list[FailureCluster]:
    from ..goal_loop import compat

    execution_goal_ids = {g.goal_id for g in _execution_goals(engine)}
    clusters = [
        compat.failure_classification_to_cluster(record, stage="execution")
        for record in engine.classifications
        if record.goal_id in execution_goal_ids
    ]
    return [FailureCluster.from_value(cluster.to_dict()) for cluster in clusters]


def _build_key_artifacts(output_dir: Path) -> list[ArtifactRef]:
    refs = []
    for filename in (
        "execution_results.json",
        "action_log.jsonl",
        "network_events.json",
        "screenshots_index.json",
        "round_analysis.json",
        "next_round_plan.json",
    ):
        path = output_dir / filename
        if path.exists():
            refs.append(ArtifactRef(label=filename, path=str(path)))
    return refs


def build_run_report(
    engine: "GoalLoopEngine",
    outcomes: list["ExecutionOutcome"],
    run_id: str,
    output_dir: Path,
) -> RunReport:
    goals = _execution_goals(engine)
    paused_count = sum(1 for g in goals if g.status in PAUSED_STATUSES)
    succeeded_count = sum(1 for g in goals if g.status == STATUS_SUCCEEDED)
    failed_count = sum(1 for g in goals if g.status in TERMINAL_STATUSES and g.status != STATUS_SUCCEEDED)

    if paused_count:
        status = "needs_review"
        next_action = "resolve pending human tasks, then resume the run"
    elif failed_count:
        status = "completed_with_failures"
        next_action = "review failed execution goals and schedule a retry round"
    else:
        status = "completed"
        next_action = "advance to next goal in the frontier"

    summary = RunSummary(
        run_id=run_id,
        status=status,
        next_action=next_action,
        counts=[],
        facts=[
            Fact(label="total_execution_goals", value=len(goals)),
            Fact(label="succeeded", value=succeeded_count),
            Fact(label="failed", value=failed_count),
            Fact(label="paused_for_human", value=paused_count),
            Fact(label="executed_case_count", value=len(outcomes)),
        ],
    )

    any_real_browser = any(o.execution_mode != "fixture_simulated" for o in outcomes)
    notes = (
        [
            "This run executed at least one case via a real-browser runner "
            "(execution_mode on the corresponding outcome is not "
            "'fixture_simulated'); see each success/failure item's "
            "execution_mode for which cases were real vs. simulated.",
        ]
        if any_real_browser
        else [
            "Stage E executes each case's basic path via a fixture-simulated runner "
            "(no live browser); execution_mode on every outcome makes this explicit.",
        ]
    )

    return RunReport(
        summary=summary,
        success_items=_build_success_items(outcomes),
        failure_items=_build_failure_items(outcomes),
        failure_clusters=_build_failure_clusters(engine),
        key_artifacts=_build_key_artifacts(output_dir),
        notes=notes,
    )


def write_run_report(
    engine: "GoalLoopEngine",
    outcomes: list["ExecutionOutcome"],
    run_id: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write run_report.json/.md under output_dir/reports/, matching the
    EXISTING convention (runtime.artifacts.RunPaths.reports_dir,
    verification/run_sample.py) that orchestration.session_artifacts's
    _load_run_session_record() already reads from
    (``run_dir / "reports" / "run_report.json"``) — writing flat at the top
    level would make a genuine Stage E run invisible to that reader.
    """

    report = build_run_report(engine, outcomes, run_id, output_dir)

    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    import json

    json_path = reports_dir / "run_report.json"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_path = reports_dir / "run_report.md"
    md_path.write_text(render_run_report_markdown(report), encoding="utf-8")

    return json_path, md_path


__all__ = ["build_run_report", "write_run_report"]
