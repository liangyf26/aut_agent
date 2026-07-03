"""
Promotion gate: run -> project -> platform (技术方案 §2.3 分层沉淀 /
实施计划 §8.5-8.6 阶段风险与修正).

The single-engine ``GoalLoopEngine`` already tags every ``ExperienceUpdate``
with a ``promotion_level`` (goal_loop/state_machine.py: "winning" successes
default to ``run``; ``record_escalation_experiences`` proposes ``platform``
for recurring defects). Neither of those levels has cross-system evidence
behind it yet — a single engine only ever sees ONE system. Stage F's job is
to RE-DECIDE ``platform`` claims against real cross-system evidence and
demote anything that does not clear the bar back to ``project``.

The promotion gate (实施计划 §8.6): "只有跨多个功能点、跨多次 run 仍稳定有效的经验，
才允许进入平台级候选" is read as a compound, PER-SYSTEM requirement — an
aggregate goal count is not enough, because "one goal each on two systems"
would trivially clear an aggregate-of-2 floor while still being exactly the
thin, single-feature-point evidence the clause warns against:

- a system only counts toward the cross-system requirement if it
  independently shows >= ``MIN_SUPPORTING_GOALS_PER_SYSTEM`` supporting
  goals for the signature (跨多个功能点 evaluated WITHIN that system, not
  spread thin across systems)
- >= ``MIN_SYSTEMS_FOR_PLATFORM`` systems must each independently qualify
  that way (跨多次 run / 跨系统)
- for failure-class experience specifically, a SINGLE playbook resolved it on
  every system that recovered (an inconsistent playbook means the "experience"
  is not actually one stable thing)

Even when the gate passes, ``review_status`` stays ``needs_review`` —
platform-level candidates never auto-activate (技术方案 §2.3: "平台级候选不能自动
生效"). Passing the gate only changes the RECOMMENDATION a human reviewer
sees, never the review requirement itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..goal_loop.models import PROMOTION_PLATFORM, PROMOTION_PROJECT
from .comparison import CrossSystemFailureComparison, compare_failure_classifications
from .run_record import CrossSystemRunRecord

MIN_SYSTEMS_FOR_PLATFORM = 2
MIN_SUPPORTING_GOALS_PER_SYSTEM = 2

KIND_WINNING = "winning"
KIND_FAILURE_RECOVERY = "failure_recovery"
VALID_KINDS: frozenset[str] = frozenset({KIND_WINNING, KIND_FAILURE_RECOVERY})


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


@dataclass(slots=True)
class PromotionReview:
    """One promotion decision for a cross-system-comparable experience signature."""

    signature: str
    kind: str  # "winning" (goal_type success) | "failure_recovery" (failure_class)
    systems: list[str] = field(default_factory=list)
    supporting_goal_ids: list[str] = field(default_factory=list)
    playbook_ids: list[str] = field(default_factory=list)
    playbook_consistent: bool | None = None
    decided_promotion_level: str = PROMOTION_PROJECT
    eligible_for_platform: bool = False
    rationale: str = ""
    source_update_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "signature": self.signature,
                "kind": self.kind,
                "systems": self.systems,
                "supporting_goal_ids": self.supporting_goal_ids,
                "playbook_ids": self.playbook_ids,
                "playbook_consistent": self.playbook_consistent,
                "decided_promotion_level": self.decided_promotion_level,
                "eligible_for_platform": self.eligible_for_platform,
                "rationale": self.rationale,
                "source_update_ids": self.source_update_ids,
            }
        )


def _goal_type_for(record: CrossSystemRunRecord, goal_id: str | None) -> str | None:
    if not goal_id:
        return None
    for goal in record.goals:
        if goal.get("goal_id") == goal_id:
            return goal.get("goal_type")
    return None


def _winning_reviews(records: list[CrossSystemRunRecord]) -> list[PromotionReview]:
    """Group ``kind == "winning"`` updates by goal_type (structural join via
    ``source_goal`` -> the goal's own ``goal_type`` field, never by parsing
    ``winning_pattern`` free text, which is per-goal and not comparable
    across systems)."""

    by_goal_type: dict[str, dict[str, Any]] = {}
    for record in records:
        for update in record.experience_updates:
            if update.get("kind") != KIND_WINNING:
                continue
            goal_type = _goal_type_for(record, update.get("source_goal"))
            if goal_type is None:
                continue
            bucket = by_goal_type.setdefault(goal_type, {"goal_ids_by_system": {}, "update_ids": []})
            system_goals = bucket["goal_ids_by_system"].setdefault(record.system.system_id, set())
            system_goals.add(update.get("source_goal"))
            if update.get("update_id"):
                bucket["update_ids"].append(update["update_id"])

    reviews: list[PromotionReview] = []
    for goal_type, bucket in sorted(by_goal_type.items()):
        goal_ids_by_system: dict[str, set[str]] = bucket["goal_ids_by_system"]
        qualifying_systems = sorted(
            system_id
            for system_id, goal_ids in goal_ids_by_system.items()
            if len(goal_ids) >= MIN_SUPPORTING_GOALS_PER_SYSTEM
        )
        # Evidence must stay internally consistent: supporting_goal_ids only
        # includes goals from QUALIFYING systems, matching `systems` — a
        # non-qualifying system's goal count must not pad the reported goal
        # total behind a "cross_system_systems:sys_a,sys_b" evidence claim
        # that only names 2 systems.
        qualifying_goal_ids = sorted(
            {
                gid
                for system_id in qualifying_systems
                for gid in goal_ids_by_system[system_id]
            }
        )
        total_goal_count = sum(len(goals) for goals in goal_ids_by_system.values())
        eligible = len(qualifying_systems) >= MIN_SYSTEMS_FOR_PLATFORM
        reviews.append(
            PromotionReview(
                signature=f"winning::{goal_type}",
                kind=KIND_WINNING,
                systems=qualifying_systems,
                supporting_goal_ids=qualifying_goal_ids,
                decided_promotion_level=(
                    PROMOTION_PLATFORM if eligible else PROMOTION_PROJECT
                ),
                eligible_for_platform=eligible,
                rationale=(
                    f"{goal_type} success predicate held on {len(qualifying_systems)} system(s) "
                    f"with >= {MIN_SUPPORTING_GOALS_PER_SYSTEM} supporting goal(s) each "
                    f"(out of {len(goal_ids_by_system)} system(s) observed, {total_goal_count} goal(s) total)."
                    + ("" if eligible else " Below per-system cross-goal promotion floor.")
                ),
                source_update_ids=sorted(set(bucket["update_ids"])),
            )
        )
    return reviews


def _failure_recovery_reviews(
    comparisons: list[CrossSystemFailureComparison],
) -> list[PromotionReview]:
    """Turn each failure-class comparison row into a promotion decision.

    Driven ENTIRELY by the structural ``CrossSystemRunRecord`` join
    ``comparison.py`` already computed (systems_recovered, playbook
    consistency) — no ``ExperienceUpdate.failed_pattern`` text parsing is
    load-bearing for this decision.
    """

    reviews: list[PromotionReview] = []
    for row in comparisons:
        if not row.systems_recovered:
            continue  # never recovered anywhere: nothing to promote yet.

        qualifying_systems = sorted(
            system_id
            for system_id, goal_ids in row.recovering_goal_ids.items()
            if len(goal_ids) >= MIN_SUPPORTING_GOALS_PER_SYSTEM
        )
        # Same consistency rule as _winning_reviews: only qualifying
        # systems' goals back the evidence claim.
        supporting_goal_ids = sorted(
            {
                gid
                for system_id in qualifying_systems
                for gid in row.recovering_goal_ids[system_id]
            }
        )
        eligible = (
            len(qualifying_systems) >= MIN_SYSTEMS_FOR_PLATFORM
            and row.playbook_consistent
        )
        if eligible:
            rationale = (
                f"{row.failure_class} recovered via a consistent playbook on "
                f"{len(qualifying_systems)} system(s), each with >= "
                f"{MIN_SUPPORTING_GOALS_PER_SYSTEM} supporting goal(s)."
            )
        elif not row.playbook_consistent:
            rationale = (
                f"{row.failure_class} was resolved by DIFFERENT playbooks across systems "
                f"({row.playbook_ids}); not stable enough to promote."
            )
        else:
            rationale = (
                f"{row.failure_class} recovered on only {len(qualifying_systems)} system(s) with "
                f">= {MIN_SUPPORTING_GOALS_PER_SYSTEM} supporting goal(s) each "
                f"(out of {len(row.systems_recovered)} system(s) that recovered at all); "
                "below per-system cross-goal promotion floor."
            )

        reviews.append(
            PromotionReview(
                signature=f"failure_recovery::{row.failure_class}",
                kind=KIND_FAILURE_RECOVERY,
                systems=qualifying_systems,
                supporting_goal_ids=supporting_goal_ids,
                playbook_ids=row.playbook_ids,
                playbook_consistent=row.playbook_consistent,
                decided_promotion_level=(
                    PROMOTION_PLATFORM if eligible else PROMOTION_PROJECT
                ),
                eligible_for_platform=eligible,
                rationale=rationale,
            )
        )
    return reviews


def review_experience_updates(
    records: list[CrossSystemRunRecord],
    *,
    comparisons: list[CrossSystemFailureComparison] | None = None,
) -> list[PromotionReview]:
    """Re-decide promotion levels for every cross-system-comparable signature.

    ``review_status`` on the ``ExperienceUpdate``/``PromotionCandidateRecord``
    side is NOT touched here and always stays ``needs_review`` regardless of
    ``eligible_for_platform`` — see module docstring. Callers project
    ``PromotionReview`` rows onto ``PromotionCandidateRecord`` via
    ``fixture_writer.promotion_review_to_candidate``.
    """

    comparisons = (
        comparisons if comparisons is not None else compare_failure_classifications(records)
    )
    return _winning_reviews(records) + _failure_recovery_reviews(comparisons)


__all__ = [
    "MIN_SYSTEMS_FOR_PLATFORM",
    "MIN_SUPPORTING_GOALS_PER_SYSTEM",
    "PromotionReview",
    "review_experience_updates",
]
