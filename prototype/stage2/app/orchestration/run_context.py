from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prototype.stage2.app.config.models import ModelProfile
from prototype.stage2.app.iteration.models import IterationArtifacts
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
    iteration: IterationArtifacts | None = None
