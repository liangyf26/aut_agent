from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prototype.stage2.app.config.models import ModelProfile
from prototype.stage2.app.progress import ProgressManager
from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.runtime.templates import TemplateBundle


@dataclass(frozen=True)
class Stage2RunContext:
    template_name: str
    template_dir: Path
    cdp_url: str
    max_attempts: int
    model_profile: ModelProfile
    artifacts: ArtifactWriter
    bundle: TemplateBundle
    runtime_data: dict[str, Any]
    progress: ProgressManager
