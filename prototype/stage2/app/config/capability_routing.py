from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .capability_preflight import CapabilityGateDecision, CapabilitySnapshot, required_capability_tags_for_mode
from .models import ModelProfile


_DISCOVERY_GENERIC_STRUCTURED_MODE = "browser_use_structured_candidate"
_DISCOVERY_TEMPLATE_SEED_MODE = "template_seed_discovery"
_VERIFICATION_PLAYWRIGHT_MODE = "playwright_deterministic_verification"
_REPORTING_LLM_MODE = "llm_assisted_reporting"
_REPORTING_ARTIFACT_ONLY_MODE = "artifact_only_reporting"
_REPORTING_PREFLIGHT_FAILURE_MODE = "preflight_failure_report"
_BLOCKED_MISSING_PROBE_MODE = "blocked_missing_capability_probe"
_BLOCKED_STALE_PROBE_MODE = "blocked_stale_capability_probe"
_BLOCKED_NO_CHAT_MODE = "blocked_missing_chat_completion"


def _normalize_tags(capability_tags: dict[str, bool] | None) -> dict[str, bool]:
    if not capability_tags:
        return {}
    return {str(key): bool(value) for key, value in capability_tags.items()}


def _required_tags_for_stage_mode(mode: str) -> list[str]:
    predefined_modes = {
        "stage2_run_sample",
        "resume_human_takeover",
        "template_init",
        "browser_use_chatopenai_structured",
        "browser_use_chatdeepseek_structured",
        "browser_use_anthropic",
    }
    if mode in predefined_modes:
        return required_capability_tags_for_mode(mode)
    if mode == _DISCOVERY_GENERIC_STRUCTURED_MODE:
        return ["chat_completion", "json_schema_response_format"]
    if mode in {
        _DISCOVERY_TEMPLATE_SEED_MODE,
        _VERIFICATION_PLAYWRIGHT_MODE,
        _REPORTING_LLM_MODE,
    }:
        return ["chat_completion"]
    return []


@dataclass(frozen=True)
class CapabilityStageRoute:
    stage: str
    allowed: bool
    recommended_mode: str
    reason_code: str
    reason: str
    required_tags: list[str] = field(default_factory=list)
    missing_tags: list[str] = field(default_factory=list)
    routing_tags: list[str] = field(default_factory=list)
    capability_tags: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityRoutingDecision:
    profile_name: str
    model: str
    gate_status: str
    gate_reason_code: str
    gate_reason: str
    capability_tags: dict[str, bool] = field(default_factory=dict)
    routing_tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discovery: CapabilityStageRoute | None = None
    verification: CapabilityStageRoute | None = None
    reporting: CapabilityStageRoute | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["discovery"] = self.discovery.to_dict() if self.discovery else None
        payload["verification"] = self.verification.to_dict() if self.verification else None
        payload["reporting"] = self.reporting.to_dict() if self.reporting else None
        return payload


def build_capability_routing(
    profile: ModelProfile,
    *,
    gate: CapabilityGateDecision | None = None,
    snapshot: CapabilitySnapshot | None = None,
) -> CapabilityRoutingDecision:
    resolved_snapshot = snapshot or (gate.snapshot if gate else None)
    capability_tags = _normalize_tags(
        resolved_snapshot.capability_tags if resolved_snapshot else (gate.capability_tags if gate else None)
    )

    if gate and gate.reason_code == "capability_probe_missing":
        return _build_preflight_blocked_routing(
            profile,
            gate=gate,
            capability_tags=capability_tags,
            blocked_mode=_BLOCKED_MISSING_PROBE_MODE,
            routing_tags=["probe_missing", "reporting_only"],
        )

    if gate and gate.reason_code == "capability_probe_stale":
        return _build_preflight_blocked_routing(
            profile,
            gate=gate,
            capability_tags=capability_tags,
            blocked_mode=_BLOCKED_STALE_PROBE_MODE,
            routing_tags=["probe_stale", "reporting_only"],
        )

    if gate is None and resolved_snapshot is None:
        synthetic_gate = CapabilityGateDecision(
            status="blocked",
            reason_code="capability_probe_missing",
            reason=(
                f"No capability probe context was provided for model {profile.model}. "
                "Run capability preflight before routing stage-2 work."
            ),
            mode="routing_only",
            profile_name=profile.name,
        )
        return _build_preflight_blocked_routing(
            profile,
            gate=synthetic_gate,
            capability_tags={},
            blocked_mode=_BLOCKED_MISSING_PROBE_MODE,
            routing_tags=["probe_missing", "reporting_only"],
        )

    notes: list[str] = []
    if gate is None and resolved_snapshot is not None:
        notes.append("Routing was derived from a capability snapshot only; freshness was not revalidated by preflight.")
    if gate and gate.reason_code == "capability_probe_incompatible" and gate.missing_tags:
        notes.append(
            "The provided gate was incompatible for at least one strict mode, so routing falls back to the strongest "
            "stage plan supported by the recorded capability tags."
        )

    discovery = _build_discovery_route(capability_tags)
    verification = _build_verification_route(capability_tags)
    reporting = _build_reporting_route(capability_tags)

    routing_tags = _build_routing_tags(capability_tags, discovery=discovery, verification=verification, reporting=reporting)
    if gate and gate.reason_code == "capability_probe_incompatible":
        routing_tags.append("strict_mode_downgraded")

    return CapabilityRoutingDecision(
        profile_name=profile.name,
        model=profile.model,
        gate_status=gate.status if gate else "snapshot_only",
        gate_reason_code=gate.reason_code if gate else "routing_snapshot_only",
        gate_reason=gate.reason if gate else "Routing used the supplied capability snapshot without rerunning preflight.",
        capability_tags=capability_tags,
        routing_tags=routing_tags,
        notes=notes,
        discovery=discovery,
        verification=verification,
        reporting=reporting,
    )


def _build_preflight_blocked_routing(
    profile: ModelProfile,
    *,
    gate: CapabilityGateDecision,
    capability_tags: dict[str, bool],
    blocked_mode: str,
    routing_tags: list[str],
) -> CapabilityRoutingDecision:
    blocked_reason = gate.reason
    discovery = CapabilityStageRoute(
        stage="discovery",
        allowed=False,
        recommended_mode=blocked_mode,
        reason_code=gate.reason_code,
        reason=blocked_reason,
        required_tags=["chat_completion"],
        missing_tags=["capability_probe"],
        routing_tags=list(routing_tags),
        capability_tags=dict(capability_tags),
        notes=["Refresh or create a capability probe before discovery joins the run."],
    )
    verification = CapabilityStageRoute(
        stage="verification",
        allowed=False,
        recommended_mode=blocked_mode,
        reason_code=gate.reason_code,
        reason=blocked_reason,
        required_tags=required_capability_tags_for_mode("stage2_run_sample"),
        missing_tags=["capability_probe"],
        routing_tags=list(routing_tags),
        capability_tags=dict(capability_tags),
        notes=["Keep verification blocked until capability preflight is fresh again."],
    )
    reporting = CapabilityStageRoute(
        stage="reporting",
        allowed=True,
        recommended_mode=_REPORTING_PREFLIGHT_FAILURE_MODE,
        reason_code="preflight_failure_report_only",
        reason="Reporting may still summarize the blocked run and capture the preflight failure details.",
        required_tags=[],
        missing_tags=[],
        routing_tags=list(routing_tags),
        capability_tags=dict(capability_tags),
        notes=["Persist the block reason so this run is traceable instead of disappearing as a generic failure."],
    )
    return CapabilityRoutingDecision(
        profile_name=profile.name,
        model=profile.model,
        gate_status=gate.status,
        gate_reason_code=gate.reason_code,
        gate_reason=gate.reason,
        capability_tags=dict(capability_tags),
        routing_tags=list(routing_tags),
        notes=["Capability preflight remains a hard gate for discovery and verification when the probe is missing or stale."],
        discovery=discovery,
        verification=verification,
        reporting=reporting,
    )


def _build_discovery_route(capability_tags: dict[str, bool]) -> CapabilityStageRoute:
    chat_completion_ready = capability_tags.get("chat_completion") is True
    json_schema_ready = capability_tags.get("json_schema_response_format") is True
    tool_calling_ready = capability_tags.get("tool_calling") is True
    openai_structured_ready = capability_tags.get("browser_use_chatopenai_structured") is True
    deepseek_structured_ready = capability_tags.get("browser_use_chatdeepseek_structured") is True
    anthropic_ready = capability_tags.get("browser_use_compatible") is True

    if anthropic_ready and chat_completion_ready and tool_calling_ready:
        mode = "browser_use_anthropic"
        return CapabilityStageRoute(
            stage="discovery",
            allowed=True,
            recommended_mode=mode,
            reason_code="browser_use_anthropic_ready",
            reason="This Anthropic model passed chat + tool-use checks and can join live discovery.",
            required_tags=_required_tags_for_stage_mode(mode),
            routing_tags=["discovery_enabled", "browser_use_compatible", "anthropic_wrapper"],
            capability_tags=dict(capability_tags),
            notes=["Use the Anthropic ChatAnthropic wrapper for browser_use exploration."],
        )

    if openai_structured_ready and chat_completion_ready and json_schema_ready:
        mode = "browser_use_chatopenai_structured"
        return CapabilityStageRoute(
            stage="discovery",
            allowed=True,
            recommended_mode=mode,
            reason_code="browser_use_openai_structured_ready",
            reason="This model passed the OpenAI-style Browser Use structured wrapper checks and can join live discovery.",
            required_tags=_required_tags_for_stage_mode(mode),
            routing_tags=["discovery_enabled", "browser_use_structured", "openai_wrapper"],
            capability_tags=dict(capability_tags),
            notes=["Use the strict Browser Use structured path for exploration when project policy allows it."],
        )

    if deepseek_structured_ready and chat_completion_ready:
        mode = "browser_use_chatdeepseek_structured"
        return CapabilityStageRoute(
            stage="discovery",
            allowed=True,
            recommended_mode=mode,
            reason_code="browser_use_deepseek_structured_ready",
            reason="This model passed the DeepSeek Browser Use wrapper checks and can join live discovery.",
            required_tags=_required_tags_for_stage_mode(mode),
            routing_tags=["discovery_enabled", "browser_use_structured", "deepseek_wrapper"],
            capability_tags=dict(capability_tags),
            notes=["Prefer the DeepSeek-specific wrapper instead of assuming OpenAI structured compatibility."],
        )

    if chat_completion_ready and json_schema_ready:
        return CapabilityStageRoute(
            stage="discovery",
            allowed=True,
            recommended_mode=_DISCOVERY_GENERIC_STRUCTURED_MODE,
            reason_code="browser_use_structured_candidate",
            reason="The model supports chat completion plus json_schema, but wrapper-specific Browser Use compatibility is not yet proven.",
            required_tags=_required_tags_for_stage_mode(_DISCOVERY_GENERIC_STRUCTURED_MODE),
            routing_tags=["discovery_enabled", "structured_candidate"],
            capability_tags=dict(capability_tags),
            notes=["Keep the route generic until a wrapper-specific probe confirms the exact Browser Use path."],
        )

    if chat_completion_ready:
        return CapabilityStageRoute(
            stage="discovery",
            allowed=True,
            recommended_mode=_DISCOVERY_TEMPLATE_SEED_MODE,
            reason_code="template_seed_discovery_only",
            reason="The model can support seeded or reviewed discovery, but not Browser Use structured routing.",
            required_tags=_required_tags_for_stage_mode(_DISCOVERY_TEMPLATE_SEED_MODE),
            routing_tags=["discovery_enabled", "template_seed_only"],
            capability_tags=dict(capability_tags),
            notes=["Stay on conservative discovery modes such as template seeding or human-reviewed page mapping."],
        )

    return CapabilityStageRoute(
        stage="discovery",
        allowed=False,
        recommended_mode=_BLOCKED_NO_CHAT_MODE,
        reason_code="chat_completion_missing",
        reason="Discovery needs at least basic chat completion support before the model can participate safely.",
        required_tags=["chat_completion"],
        missing_tags=["chat_completion"],
        routing_tags=["discovery_blocked", "reporting_only"],
        capability_tags=dict(capability_tags),
        notes=["Use another model for discovery or keep this profile out of interactive stage work."],
    )


def _build_verification_route(capability_tags: dict[str, bool]) -> CapabilityStageRoute:
    chat_completion_ready = capability_tags.get("chat_completion") is True
    if chat_completion_ready:
        return CapabilityStageRoute(
            stage="verification",
            allowed=True,
            recommended_mode=_VERIFICATION_PLAYWRIGHT_MODE,
            reason_code="playwright_verification_ready",
            reason="Current run-sample style verification only requires the minimal chat-completion gate plus deterministic browser execution.",
            required_tags=_required_tags_for_stage_mode(_VERIFICATION_PLAYWRIGHT_MODE),
            routing_tags=["verification_enabled", "minimal_chat_gate"],
            capability_tags=dict(capability_tags),
            notes=["Do not over-tighten this path to require json_schema unless the runtime actually switches to structured Browser Use execution."],
        )

    return CapabilityStageRoute(
        stage="verification",
        allowed=False,
        recommended_mode=_BLOCKED_NO_CHAT_MODE,
        reason_code="chat_completion_missing",
        reason="Verification is blocked because the prototype still expects at least a basic chat-completion-capable profile.",
        required_tags=_required_tags_for_stage_mode("stage2_run_sample"),
        missing_tags=["chat_completion"],
        routing_tags=["verification_blocked", "reporting_only"],
        capability_tags=dict(capability_tags),
        notes=["Route verification to another profile or stop at artifact-only reporting."],
    )


def _build_reporting_route(capability_tags: dict[str, bool]) -> CapabilityStageRoute:
    chat_completion_ready = capability_tags.get("chat_completion") is True
    if chat_completion_ready:
        return CapabilityStageRoute(
            stage="reporting",
            allowed=True,
            recommended_mode=_REPORTING_LLM_MODE,
            reason_code="llm_reporting_ready",
            reason="The model can assist with report synthesis, summaries, and classification for stage-2 artifacts.",
            required_tags=_required_tags_for_stage_mode(_REPORTING_LLM_MODE),
            routing_tags=["reporting_enabled", "llm_reporting"],
            capability_tags=dict(capability_tags),
            notes=["This is the lowest-risk stage for weaker local models after deterministic execution finishes."],
        )

    return CapabilityStageRoute(
        stage="reporting",
        allowed=True,
        recommended_mode=_REPORTING_ARTIFACT_ONLY_MODE,
        reason_code="artifact_reporting_only",
        reason="Reporting can still run in artifact-only mode even when the model is not suitable for interactive stage work.",
        required_tags=[],
        missing_tags=[],
        routing_tags=["reporting_enabled", "artifact_only"],
        capability_tags=dict(capability_tags),
        notes=["Use deterministic report assembly and leave subjective interpretation to human review."],
    )


def _build_routing_tags(
    capability_tags: dict[str, bool],
    *,
    discovery: CapabilityStageRoute,
    verification: CapabilityStageRoute,
    reporting: CapabilityStageRoute,
) -> list[str]:
    result: list[str] = []
    if capability_tags.get("chat_completion") is True:
        result.append("chat_completion_ready")
    else:
        result.append("chat_completion_missing")
    if capability_tags.get("json_schema_response_format") is True:
        result.append("json_schema_ready")
    if capability_tags.get("browser_use_chatopenai_structured") is True:
        result.append("browser_use_chatopenai_structured")
    if capability_tags.get("browser_use_chatdeepseek_structured") is True:
        result.append("browser_use_chatdeepseek_structured")
    if discovery.allowed:
        result.append("discovery_allowed")
    if verification.allowed:
        result.append("verification_allowed")
    if reporting.allowed:
        result.append("reporting_allowed")
    for route in (discovery, verification, reporting):
        for tag in route.routing_tags:
            if tag not in result:
                result.append(tag)
    return result


__all__ = [
    "CapabilityRoutingDecision",
    "CapabilityStageRoute",
    "build_capability_routing",
]
