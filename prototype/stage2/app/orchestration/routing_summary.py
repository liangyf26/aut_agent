from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from prototype.stage2.app.config.capability_preflight import CapabilityGateDecision
from prototype.stage2.app.config.capability_routing import CapabilityRoutingDecision, CapabilityStageRoute
from prototype.stage2.app.config.models import ModelProfile
from prototype.stage2.app.config.run_policy_loader import RunPolicyLoadResult


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != {}
    }


def _enabled_tags(capability_tags: dict[str, bool] | None) -> list[str]:
    if not capability_tags:
        return []
    return sorted(key for key, enabled in capability_tags.items() if enabled is True)


def _normalize_notes(*groups: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
    return result


@dataclass(frozen=True, slots=True)
class ModelRoutingPolicySummary:
    model: str
    profile_name: str
    requested_mode: str | None = None
    preflight_status: str = "unknown"
    preflight_reason_code: str | None = None
    preflight_reason: str | None = None
    discovery_mode: str | None = None
    discovery_allowed: bool | None = None
    discovery_reason_code: str | None = None
    verification_mode: str | None = None
    verification_allowed: bool | None = None
    verification_reason_code: str | None = None
    reporting_mode: str | None = None
    reporting_allowed: bool | None = None
    reporting_reason_code: str | None = None
    run_policy_load_status: str = "missing"
    run_policy_default_decision: str | None = None
    run_policy_default_source: str | None = None
    run_policy_path: str | None = None
    run_policy_rule_count: int = 0
    project_name: str | None = None
    template_name: str | None = None
    enabled_capability_tags: list[str] = field(default_factory=list)
    routing_tags: list[str] = field(default_factory=list)
    applied_policy_sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


def build_routing_summary(
    profile: ModelProfile,
    *,
    capability_gate: CapabilityGateDecision,
    capability_routing: CapabilityRoutingDecision | None = None,
    run_policy: RunPolicyLoadResult | None = None,
) -> ModelRoutingPolicySummary:
    routing = capability_routing or build_capability_routing(profile, gate=capability_gate)
    policy = run_policy or RunPolicyLoadResult(
        policy_path=Path("run_policy.json"),
        load_status="missing",
    )

    notes = _normalize_notes(
        _build_gate_notes(capability_gate, routing),
        _build_routing_notes(routing),
        _build_policy_notes(policy),
        capability_gate.notes,
        routing.notes,
        policy.notes,
    )

    return ModelRoutingPolicySummary(
        model=profile.model,
        profile_name=profile.name,
        requested_mode=capability_gate.mode,
        preflight_status=capability_gate.status,
        preflight_reason_code=capability_gate.reason_code,
        preflight_reason=capability_gate.reason,
        discovery_mode=_stage_mode(routing.discovery),
        discovery_allowed=_stage_allowed(routing.discovery),
        discovery_reason_code=_stage_reason_code(routing.discovery),
        verification_mode=_stage_mode(routing.verification),
        verification_allowed=_stage_allowed(routing.verification),
        verification_reason_code=_stage_reason_code(routing.verification),
        reporting_mode=_stage_mode(routing.reporting),
        reporting_allowed=_stage_allowed(routing.reporting),
        reporting_reason_code=_stage_reason_code(routing.reporting),
        run_policy_load_status=policy.load_status,
        run_policy_default_decision=policy.risky_submit_default_decision,
        run_policy_default_source=policy.resolved_default_source,
        run_policy_path=str(policy.policy_path),
        run_policy_rule_count=len(policy.allow_rules),
        project_name=policy.project_name,
        template_name=policy.template_name,
        enabled_capability_tags=_enabled_tags(routing.capability_tags or capability_gate.capability_tags),
        routing_tags=list(routing.routing_tags),
        applied_policy_sources=list(policy.applied_sources),
        notes=notes,
    )


def _build_gate_notes(
    gate: CapabilityGateDecision,
    routing: CapabilityRoutingDecision,
) -> list[str]:
    notes: list[str] = []
    if gate.reason_code in {"capability_probe_missing", "capability_probe_stale"}:
        notes.append("Capability preflight blocks discovery and verification until the probe is refreshed.")
    if gate.reason_code == "capability_probe_incompatible":
        discovery_mode = _stage_mode(routing.discovery)
        if gate.mode and discovery_mode and gate.mode != discovery_mode:
            notes.append(
                f"Requested strict mode {gate.mode} was downgraded to {discovery_mode} based on recorded capability tags."
            )
    return notes


def _build_routing_notes(routing: CapabilityRoutingDecision) -> list[str]:
    notes: list[str] = []
    discovery_mode = _stage_mode(routing.discovery)
    if discovery_mode == "browser_use_structured_candidate":
        notes.append("Structured discovery is only a candidate path until wrapper-specific Browser Use compatibility is proven.")
    if discovery_mode == "browser_use_chatopenai_structured":
        notes.append("OpenAI-style Browser Use structured discovery is available for this profile.")
    if discovery_mode == "browser_use_chatdeepseek_structured":
        notes.append("DeepSeek Browser Use structured discovery is available for this profile.")
    if discovery_mode == "template_seed_discovery":
        notes.append("Discovery stays on the conservative template-seeded path for this profile.")
    return notes


def _build_policy_notes(policy: RunPolicyLoadResult) -> list[str]:
    notes: list[str] = []
    if policy.load_status == "missing":
        notes.append("Run policy file is missing, so risky submit keeps the built-in blocked default.")
    elif policy.load_status == "loaded" and policy.applied_sources:
        notes.append("Run policy resolved from: " + ", ".join(policy.applied_sources))
    elif policy.load_status in {"empty", "invalid"}:
        notes.append("Run policy could not be fully loaded, so the built-in blocked default still applies.")
    return notes


def _stage_mode(route: CapabilityStageRoute | None) -> str | None:
    return route.recommended_mode if route else None


def _stage_allowed(route: CapabilityStageRoute | None) -> bool | None:
    return route.allowed if route else None


def _stage_reason_code(route: CapabilityStageRoute | None) -> str | None:
    return route.reason_code if route else None


def build_capability_routing(
    profile: ModelProfile,
    *,
    gate: CapabilityGateDecision,
) -> CapabilityRoutingDecision:
    from prototype.stage2.app.config.capability_routing import build_capability_routing as _build_capability_routing

    return _build_capability_routing(profile, gate=gate)


__all__ = [
    "ModelRoutingPolicySummary",
    "build_routing_summary",
]
