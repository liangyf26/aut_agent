"""
Cross-system failure classification + playbook comparison (实施计划 §8.2:
"比较不同系统的失败分类与套路动作").

Failure classes and playbooks are already a fixed, closed vocabulary
(``goal_loop.classification.FIXED_FAILURE_CLASSES`` / ``goal_loop.playbook``)
shared by every system — Stage F does not invent a new comparison axis, it
just checks whether the SAME fixed class, on a SECOND system, was resolved by
the SAME fixed playbook. Divergence here (a class that needed a different
playbook on a different system) is itself the signal that stops
over-generalizing a single system's experience (实施计划 §8.5 风险条款).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .run_record import CrossSystemRunRecord


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


@dataclass(slots=True)
class CrossSystemFailureComparison:
    """One failure class's cross-system recurrence + playbook-consistency row."""

    failure_class: str
    systems_observed: list[str] = field(default_factory=list)
    systems_recovered: list[str] = field(default_factory=list)
    playbook_ids: list[str] = field(default_factory=list)
    playbook_consistent: bool = True
    recovering_goal_ids: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "failure_class": self.failure_class,
                "systems_observed": self.systems_observed,
                "systems_recovered": self.systems_recovered,
                "playbook_ids": self.playbook_ids,
                "playbook_consistent": self.playbook_consistent,
                "recovering_goal_ids": self.recovering_goal_ids,
            }
        )


def compare_failure_classifications(
    records: list[CrossSystemRunRecord],
) -> list[CrossSystemFailureComparison]:
    """Build one comparison row per failure class observed on any system.

    ``systems_observed`` counts every system that hit the class at all
    (per ``failure_classifications.jsonl`` / ``seen_failure_classes``).
    ``systems_recovered`` (the strict subset that matters for promotion,
    见 promotion_reviewer.py) counts only systems where a goal that hit the
    class went on to actually succeed, per
    ``CrossSystemRunRecord.recovered_failure_classes`` — which is derived
    from a DIFFERENT source file (``goal_attempts.jsonl``).

    A system is counted toward ``systems_recovered`` ONLY IF it is ALSO in
    ``systems_observed`` for the same class: ``recovered_failure_classes``
    and ``seen_failure_classes`` read two independently-producible files
    (attempts vs. classifications), and a partial/inconsistent producer
    could in principle report a recovery for a class with no matching
    classification row at all. Enforcing ``systems_recovered ⊆
    systems_observed`` here keeps that invariant true BY CONSTRUCTION rather
    than by hoping the two files always agree.

    ``playbook_ids``/``playbook_consistent`` are scoped to ``systems_recovered``
    ONLY, not every system in ``systems_observed``. The eligibility rule this
    feeds (promotion_reviewer.py) is literally "a SINGLE playbook resolved it
    on every system that RECOVERED" — a third system that merely observed the
    same class but never recovered from it (e.g. it's still failing, or hit
    max_rounds) contributes no "resolved by playbook X" evidence at all, and
    must not be able to veto two OTHER systems that recovered via the exact
    same, consistent playbook.
    """

    all_classes: set[str] = set()
    for record in records:
        all_classes |= record.seen_failure_classes()

    rows: list[CrossSystemFailureComparison] = []
    for failure_class in sorted(all_classes):
        systems_observed: list[str] = []
        systems_recovered: list[str] = []
        recovered_playbook_ids: set[str] = set()
        recovering_goal_ids: dict[str, list[str]] = {}

        for record in records:
            observed_here = failure_class in record.seen_failure_classes()
            if observed_here:
                systems_observed.append(record.system.system_id)

            recovered = record.recovered_failure_classes().get(failure_class)
            if recovered and observed_here:
                systems_recovered.append(record.system.system_id)
                recovering_goal_ids[record.system.system_id] = recovered
                recovered_playbook_ids |= record.playbook_by_failure_class().get(
                    failure_class, set()
                )

        # Recovery evidence with no playbook evidence backing it (empty
        # playbook_ids) is NOT "consistent" — it is missing data, and must
        # not vacuously satisfy promotion_reviewer's "single stable
        # playbook" gate (len(set()) <= 1 is trivially true otherwise).
        playbook_consistent = (
            len(recovered_playbook_ids) == 1 if systems_recovered else len(recovered_playbook_ids) <= 1
        )

        rows.append(
            CrossSystemFailureComparison(
                failure_class=failure_class,
                systems_observed=systems_observed,
                systems_recovered=systems_recovered,
                playbook_ids=sorted(recovered_playbook_ids),
                playbook_consistent=playbook_consistent,
                recovering_goal_ids=recovering_goal_ids,
            )
        )
    return rows


__all__ = ["CrossSystemFailureComparison", "compare_failure_classifications"]
