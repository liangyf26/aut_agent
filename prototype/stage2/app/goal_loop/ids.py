"""Four-level identity scheme for the goal loop kernel.

The goal loop binds every piece of evidence into one traceable chain:

    goal_id -> attempt_id -> step_id -> evidence_id

Each level carries an explicit parent pointer (see ``models``), and the id
strings are also self-describing so a parent can be recovered by ``rsplit`` if a
pointer is ever missing. Ids are allocated by a stateful :class:`IdAllocator`
held by the engine, which keeps them deterministic for a given call order (so
tests are stable) while remaining unique within a run.

This module is intentionally dependency-free so every other goal_loop module can
import it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field


GOAL_PREFIX = "goal"
ATTEMPT_MARKER = "a"
STEP_MARKER = "s"
EVIDENCE_MARKER = "e"


def parent_of(identifier: str) -> str | None:
    """Recover the parent id from a self-describing child id.

    ``goal-000001-a01-s001-e002`` -> ``goal-000001-a01-s001`` and so on. Returns
    ``None`` for a bare goal id (which has no parent in the id space).
    """

    text = (identifier or "").strip()
    if not text:
        return None
    # goal ids look like "goal-000001" (single dash after the prefix); anything
    # deeper is "<parent>-<marker><n>".
    if text.startswith(f"{GOAL_PREFIX}-") and text.count("-") == 1:
        return None
    head, _, _tail = text.rpartition("-")
    return head or None


@dataclass(slots=True)
class IdAllocator:
    """Allocates hierarchical ids and validates parent linkage.

    The allocator is deliberately stateful: the engine owns one instance for the
    whole run so that goal/attempt/step/evidence counters advance monotonically.
    """

    _goal_seq: int = 0
    _attempt_seq: dict[str, int] = field(default_factory=dict)
    _step_seq: dict[str, int] = field(default_factory=dict)
    _evidence_seq: dict[str, int] = field(default_factory=dict)

    def new_goal_id(self) -> str:
        self._goal_seq += 1
        return f"{GOAL_PREFIX}-{self._goal_seq:06d}"

    def new_attempt_id(self, goal_id: str) -> str:
        if not goal_id:
            raise ValueError("attempt id requires a goal_id parent")
        nxt = self._attempt_seq.get(goal_id, 0) + 1
        self._attempt_seq[goal_id] = nxt
        return f"{goal_id}-{ATTEMPT_MARKER}{nxt:02d}"

    def new_step_id(self, attempt_id: str) -> str:
        if not attempt_id:
            raise ValueError("step id requires an attempt_id parent")
        nxt = self._step_seq.get(attempt_id, 0) + 1
        self._step_seq[attempt_id] = nxt
        return f"{attempt_id}-{STEP_MARKER}{nxt:03d}"

    def new_evidence_id(self, step_id: str) -> str:
        if not step_id:
            raise ValueError("evidence id requires an owner step_id parent")
        nxt = self._evidence_seq.get(step_id, 0) + 1
        self._evidence_seq[step_id] = nxt
        return f"{step_id}-{EVIDENCE_MARKER}{nxt:03d}"


__all__ = ["IdAllocator", "parent_of", "GOAL_PREFIX"]
