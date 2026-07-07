"""
Shared ``human_takeover_resolution.json`` writer for the goal_loop family.

This schema has an existing READER
(``orchestration.session_artifacts._load_run_session_record``) that predates
any producer. ``cross_system_goal`` (Stage F) was the first package to write
it (实施计划 §8.3); ``execution_goal`` (Stage E) is the second. Both packages
import from here rather than each defining their own copy, so the schema has
exactly one source of truth regardless of which stage's run_dir it lives in.

This is deliberately NOT a "resume the paused goal and continue" primitive —
``GoalLoopEngine`` has no serialization, so a CLI invocation that already
exited cannot reconstruct the in-memory engine that paused. This file only
records that a human looked at a takeover request and what they decided;
actually continuing the work means the operator re-invokes the relevant
``--run-*-goal`` command with fresh inputs (see
``main.resolve_goal_loop_takeover_entrypoint``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _safe_json_write(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


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
    """Write ``human_takeover_resolution.json`` recording how a human
    resolved a pending takeover request (``human_takeover.json``).

    Args:
        output_dir: the SAME run_dir the corresponding ``human_takeover.json``
            was written into.
        status: caller-defined resolution status (e.g. ``"resolved"``).
        operator_id: who resolved it, if known.
        note: free-text note from the operator.
        ready_to_resume: whether the operator considers the run ready for a
            follow-up round. Does NOT trigger anything automatically — see
            this module's docstring.
        resolved_at: ISO-8601 timestamp of the resolution.
        filename: output filename, overridable for tests.
    """

    payload = {
        "status": status,
        "operator_id": operator_id,
        "note": note,
        "ready_to_resume": ready_to_resume,
        "resolved_at": resolved_at,
    }
    return _safe_json_write(Path(output_dir) / filename, payload)


__all__ = ["write_human_takeover_resolution"]
