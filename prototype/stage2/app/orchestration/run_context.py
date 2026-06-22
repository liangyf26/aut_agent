from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prototype.stage2.app.config import (
    CapabilityGateDecision,
    CapabilityRoutingDecision,
    ModelProfile,
    RunPolicyLoadResult,
)
from prototype.stage2.app.discovery import DiscoveryResult
from prototype.stage2.app.discovery.strategy import DiscoveryStrategyDecision
from prototype.stage2.app.iteration.models import IterationArtifacts
from prototype.stage2.app.orchestration.routing_summary import ModelRoutingPolicySummary
from prototype.stage2.app.progress import ProgressManager
from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.runtime.templates import TemplateBundle
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData


@dataclass(frozen=True)
class Stage2RunContext:
    template_name: str
    template_dir: Path
    cdp_url: str
    max_attempts: int
    model_profile: ModelProfile
    artifacts: ArtifactWriter
    bundle: TemplateBundle
    runtime: TemplateRuntimeData
    progress: ProgressManager
    capability_gate: CapabilityGateDecision | None = None
    capability_routing: CapabilityRoutingDecision | None = None
    run_policy_resolution: RunPolicyLoadResult | None = None
    discovery_strategy: DiscoveryStrategyDecision | None = None
    routing_summary: ModelRoutingPolicySummary | None = None
    discovery_result: DiscoveryResult | None = None
    discovery_paths: dict[str, Path] | None = None
    iteration: IterationArtifacts | None = None
