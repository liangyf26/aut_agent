from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .builder import build_iteration_outputs
from .models import IterationArtifacts


def write_iteration_artifacts(
    output_dir: str | Path,
    *,
    run_report: Any = None,
    status_snapshot: Any = None,
    attempts: list[Any] | None = None,
) -> IterationArtifacts:
    artifacts = build_iteration_outputs(
        run_report=run_report,
        status_snapshot=status_snapshot,
        attempts=attempts,
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(
        root / "failure_clusters.json",
        {
            "summary": artifacts.summary.to_dict(),
            "clusters": [cluster.to_dict() for cluster in artifacts.failure_clusters],
        },
    )
    _write_json(
        root / "retry_plan.json",
        artifacts.retry_plan.to_dict() if artifacts.retry_plan else {},
    )
    _write_json(
        root / "promotion_candidates.json",
        {
            "summary": artifacts.summary.to_dict(),
            "candidates": [candidate.to_dict() for candidate in artifacts.promotion_candidates],
        },
    )
    return artifacts


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
