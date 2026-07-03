"""
Stage F fixture writer: promotion_candidates.json, goal_summary.json,
failure_classifications.jsonl, experience_updates.jsonl,
human_takeover_resolution.json (实施计划 §8.3 交付物清单).

``failure_classifications.jsonl``/``experience_updates.jsonl`` are
BYTE-IDENTICAL in shape to ``goal_loop.writer.GoalLoopWriter``'s own files
(one row per ``FailureClassification``/``ExperienceUpdate``, scoped to one
system via a ``CrossSystemAdapter``-filtered engine view) — no new schema.

``goal_summary.json`` reuses the FILENAME 实施计划 §8.3 mandates, but its
content is Stage F's own per-system goal-registry rollup (raw ``Goal.to_dict()``
rows), not a byte-identical copy of ``GoalLoopWriter``'s ``goal_summary.json``
(which is a list of derived ``GoalSummary`` rows). This is consistent with
the rest of this codebase's existing (imperfect) convention: menu_goal /
page_goal / feature_goal / goal_loop.writer each ALREADY write a mutually
different shape under this same filename — Stage F is one more per-stage
producer under a shared filename, not a claim of one canonical schema.

``promotion_candidates.json``/``promotion_candidate_summary`` DO genuinely
reuse ``iteration.PromotionCandidateRecord`` + the SAME summary-building
function ``iteration.writer._build_promotion_candidate_summary_payload``
already uses, so existing readers of that summary SHAPE (not existing
readers of Stage F's specific candidates, since nothing outside this
package's own tests currently reads Stage F's ``promotion_candidates.json``
file path) parse it without a new code path. Stage F's only new content is
WHICH ``promotion_level`` each candidate carries, decided by
``promotion_reviewer``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prototype.stage2.app.iteration.models import PromotionCandidateRecord
from prototype.stage2.app.iteration.writer import _build_promotion_candidate_summary_payload

from ..goal_loop import compat
from ..goal_loop.models import PROMOTION_PLATFORM, PROMOTION_PROJECT
from .promotion_reviewer import PromotionReview
from .run_record import CrossSystemRunRecord


def _safe_json_write(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def promotion_review_to_candidate(review: PromotionReview) -> PromotionCandidateRecord:
    """Project one cross-system :class:`PromotionReview` onto the EXISTING
    ``iteration.PromotionCandidateRecord`` shape.

    ``promotion_target`` is read from
    ``goal_loop.compat._PROMOTION_TARGET_BY_LEVEL`` — the SAME lookup table
    ``compat.experience_update_to_promotion_candidate`` uses — rather than a
    locally re-derived literal, so a platform-level candidate always carries
    the identical ``"project_baseline_freeze"`` token whether it came from a
    single-system ``ExperienceUpdate`` (via ``compat``) or a cross-system
    ``PromotionReview`` (here). Inventing a second literal
    (e.g. a Stage-F-only ``"platform_baseline_freeze"``) would fork the
    vocabulary ``iteration/writer.py``'s ``promotion_target_breakdown``
    counts by, silently splitting what should be one bucket into two.

    ``needs_manual_review`` is ALWAYS true, independent of
    ``eligible_for_platform`` — a platform-eligible candidate still needs a
    human to approve the actual promotion (技术方案 §2.3), it is just
    recommended for approval rather than deferred.
    """

    return PromotionCandidateRecord(
        candidate_id=review.signature,
        source=f"cross_system_goal:{review.kind}",
        title=review.signature,
        promotion_level=review.decided_promotion_level,
        status="candidate",
        reason=review.rationale,
        review_status="needs_review",
        promotion_target=compat._PROMOTION_TARGET_BY_LEVEL.get(review.decided_promotion_level),
        promotion_recommendation=(
            "manual_review_recommended_approve"
            if review.eligible_for_platform
            else "review_candidate_insufficient_cross_system_evidence"
        ),
        needs_manual_review=True,
        evidence_requirements=[
            f"cross_system_systems:{','.join(review.systems)}",
            f"cross_system_goals:{','.join(review.supporting_goal_ids)}",
        ],
        evidence=[
            {
                "signature": review.signature,
                "systems": review.systems,
                "supporting_goal_ids": review.supporting_goal_ids,
                "playbook_ids": review.playbook_ids,
                "playbook_consistent": review.playbook_consistent,
                "source_update_ids": review.source_update_ids,
            }
        ],
    )


def write_promotion_candidates(
    reviews: list[PromotionReview],
    output_path: str | Path,
) -> Path:
    candidates = [promotion_review_to_candidate(r) for r in reviews]
    payload = {
        "schema_version": "stage2_cross_system_promotion_candidates.v1",
        "promotion_candidate_summary": _build_promotion_candidate_summary_payload(candidates),
        "candidates": [c.to_dict() for c in candidates],
    }
    return _safe_json_write(output_path, payload)


def write_cross_system_comparison(
    comparisons: list[Any],
    output_path: str | Path,
    *,
    systems: list[Any],
) -> Path:
    payload = {
        "schema_version": "stage2_cross_system_failure_comparison.v1",
        "systems": [system.to_dict() for system in systems],
        "failure_comparisons": [row.to_dict() for row in comparisons],
    }
    return _safe_json_write(output_path, payload)


def write_system_goal_loop_artifacts(
    record: CrossSystemRunRecord,
    output_dir: str | Path,
) -> dict[str, Path]:
    """Write ONE system's ``failure_classifications.jsonl`` /
    ``experience_updates.jsonl`` / ``goal_summary.json`` under
    ``output_dir / record.system.system_id`` — the SAME per-run file names
    ``GoalLoopWriter`` uses, just isolated per system directory so two
    systems on a shared engine never overwrite each other's product.

    ``experience_updates.jsonl`` rows carrying ``promotion_level="platform"``
    are demoted to ``"project"`` before being written here. A single
    system's engine (``GoalLoopEngine.record_escalation_experiences``, §7.4)
    tags a recurring defect ``platform`` on its own — that claim has ZERO
    cross-system evidence behind it, since one engine only ever sees one
    system. Writing it through unchanged would let a single-system
    escalation's unvetted ``platform`` claim reach disk BEFORE
    ``promotion_reviewer`` (which is the actual §8 cross-system re-decision
    gate, and only re-decides ``kind == "winning"``/failure-recovery rows —
    see its module docstring) ever sees it. Demoting here is what makes that
    docstring's claim ("RE-DECIDE platform claims... and demote anything
    that does not clear the bar") actually hold for escalation-kind updates
    too, not just the two kinds ``promotion_reviewer`` re-derives from
    scratch.
    """

    root = Path(output_dir) / record.system.system_id
    root.mkdir(parents=True, exist_ok=True)

    classifications_path = root / "failure_classifications.jsonl"
    with classifications_path.open("w", encoding="utf-8") as fh:
        for row in record.classifications:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    experience_path = root / "experience_updates.jsonl"
    with experience_path.open("w", encoding="utf-8") as fh:
        for row in record.experience_updates:
            fh.write(json.dumps(_demote_unvetted_platform_claim(row), ensure_ascii=False) + "\n")

    summary_payload = {
        "run_id": record.run_id,
        "system": record.system.to_dict(),
        "goal_count": len(record.goals),
        "goals": record.goals,
    }
    summary_path = _safe_json_write(root / "goal_summary.json", summary_payload)

    return {
        "failure_classifications": classifications_path,
        "experience_updates": experience_path,
        "goal_summary": summary_path,
    }


def _demote_unvetted_platform_claim(update_row: dict[str, Any]) -> dict[str, Any]:
    if update_row.get("promotion_level") != PROMOTION_PLATFORM:
        return update_row
    demoted = dict(update_row)
    demoted["promotion_level"] = PROMOTION_PROJECT
    return demoted


def write_human_takeover_resolution(
    output_dir: str | Path,
    *,
    status: str,
    operator_id: str | None,
    note: str | None,
    ready_to_resume: bool,
    resolved_at: str,
    filename: str = "human_takeover_resolution.json",
) -> Path:
    """Write ``human_takeover_resolution.json`` if a human takeover was
    actually resolved during Stage F (实施计划 §8.3: "如阶段内发生人工恢复").

    This schema already has a READER
    (``orchestration.session_artifacts._load_run_session_record``,
    confirmed by investigation to have no producer before Stage F) — this
    function reuses that exact field set rather than inventing a new one, so
    Stage F is the first real producer for an existing consumer, not a
    parallel schema.
    """

    payload = {
        "status": status,
        "operator_id": operator_id,
        "note": note,
        "ready_to_resume": ready_to_resume,
        "resolved_at": resolved_at,
    }
    return _safe_json_write(Path(output_dir) / filename, payload)


__all__ = [
    "promotion_review_to_candidate",
    "write_promotion_candidates",
    "write_cross_system_comparison",
    "write_system_goal_loop_artifacts",
    "write_human_takeover_resolution",
]
