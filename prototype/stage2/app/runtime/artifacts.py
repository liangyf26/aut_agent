from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    screenshots_dir: Path
    generated_dir: Path
    reports_dir: Path
    attempts_path: Path
    network_path: Path
    progress_path: Path
    status_path: Path
    summary_path: Path


class ArtifactWriter:
    def __init__(self, artifact_root: Path, run_name: str) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = sanitize_name(run_name)
        base_run_dir = artifact_root / f"{timestamp}_{safe_name}"
        run_dir = base_run_dir
        suffix = 1
        while run_dir.exists():
            run_dir = artifact_root / f"{timestamp}_{safe_name}_{suffix:02d}"
            suffix += 1
        screenshots_dir = run_dir / "screenshots"
        generated_dir = run_dir / "generated"
        reports_dir = run_dir / "reports"
        for path in (run_dir, screenshots_dir, generated_dir, reports_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.paths = RunPaths(
            run_dir=run_dir,
            screenshots_dir=screenshots_dir,
            generated_dir=generated_dir,
            reports_dir=reports_dir,
            attempts_path=run_dir / "attempts.jsonl",
            network_path=run_dir / "network_events.jsonl",
            progress_path=run_dir / "progress_events.jsonl",
            status_path=run_dir / "current_status.json",
            summary_path=reports_dir / "final_report.md",
        )

    @property
    def run_dir(self) -> Path:
        return self.paths.run_dir

    @property
    def screenshots_dir(self) -> Path:
        return self.paths.screenshots_dir

    @property
    def generated_dir(self) -> Path:
        return self.paths.generated_dir

    @property
    def reports_dir(self) -> Path:
        return self.paths.reports_dir

    @property
    def summary_path(self) -> Path:
        return self.paths.summary_path

    def write_json(self, relative_path: str | Path, payload: Any) -> Path:
        path = self.run_dir / Path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def append_jsonl(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_attempt(self, payload: Any) -> None:
        self.append_jsonl(self.paths.attempts_path, payload)

    def append_network(self, payload: Any) -> None:
        self.append_jsonl(self.paths.network_path, payload)

    def write_text(self, relative_path: str | Path, text: str) -> Path:
        path = self.run_dir / Path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
