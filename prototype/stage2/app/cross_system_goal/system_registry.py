"""
SystemProfile: the one genuinely new entity Stage F introduces.

Nothing upstream of Stage F carries an enforced "which real system is this
run against" identifier. ``config.run_policy_loader.RunPolicyResolution``
has ``project_name`` + ``template_name``, and ``v3_orchestrator.V3RunConfig``
has ``target_name`` — but none of these is a stable cross-run system key: a
project name can be reused across systems, and nothing stops two runs of the
same template from actually targeting two different real deployments.

``SystemProfile.system_id`` is that stable key, supplied by the caller
(typically once per real business system Stage F is validating against). It
does not replace ``template_name``/``project_name``; it labels which system
they were resolved for, so cross-system comparison has an explicit axis to
group by instead of overloading strings that already mean something else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


@dataclass(slots=True)
class SystemProfile:
    """Identifies the real business system one or more runs targeted."""

    system_id: str
    system_name: str
    template_name: str | None = None
    project_name: str | None = None
    notes: list[str] = field(default_factory=list)
    run_mode: str = "unspecified"  # unspecified | real_browser | replayed_fixture

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "system_id": self.system_id,
                "system_name": self.system_name,
                "template_name": self.template_name,
                "project_name": self.project_name,
                "notes": self.notes,
                "run_mode": self.run_mode,
            }
        )


__all__ = ["SystemProfile"]
