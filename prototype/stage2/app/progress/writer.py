from __future__ import annotations

import json
import time
from pathlib import Path

from .models import CurrentStatusSnapshot, PhaseSummarySnapshot, ProgressEvent


class ProgressWriter:
    """Persist platform-level progress artifacts for a single run."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.output_dir / "progress_events.jsonl"
        self.current_status_path = self.output_dir / "current_status.json"
        self.phase_summary_path = self.output_dir / "phase_summary.json"

    def append_event(self, event: ProgressEvent) -> Path:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return self.events_path

    def write_current_status(self, snapshot: CurrentStatusSnapshot) -> Path:
        self._write_json(self.current_status_path, snapshot.to_dict())
        return self.current_status_path

    def write_phase_summary(self, summary: PhaseSummarySnapshot) -> Path:
        self._write_json(self.phase_summary_path, summary.to_dict())
        return self.phase_summary_path

    def _write_json(self, path: Path, payload: object) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path.write_text(text, encoding="utf-8")
        last_error: PermissionError | None = None
        for _ in range(5):
            try:
                temp_path.replace(path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05)
        if last_error is not None:
            raise last_error
