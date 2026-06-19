from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from prototype.stage2.app.runtime.artifacts import sanitize_name

from .models import (
    HumanRecordingEvent,
    RecordingArtifactPaths,
    RecordingEventType,
    RecordingSessionConfig,
    utc_now_iso,
)


class HumanLoopRecorder:
    def __init__(self, config: RecordingSessionConfig) -> None:
        self.config = config
        self.paths = self._build_paths(config)

    def start_session(self) -> RecordingArtifactPaths:
        self.paths.session_dir.mkdir(parents=True, exist_ok=True)
        self.paths.metadata_path.write_text(
            json.dumps(
                {
                    "session": self.config.to_dict(),
                    "started_at": utc_now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.record_event(
            HumanRecordingEvent(
                event_type=RecordingEventType.SESSION_STARTED,
                timestamp=utc_now_iso(),
                step_index=0,
                page_url=self.config.start_url,
                label="human recording session started",
                metadata={"template_name": self.config.template_name},
            )
        )
        return self.paths

    def record_event(self, event: HumanRecordingEvent) -> None:
        self.paths.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def record_placeholder_event(
        self,
        step_index: int,
        *,
        page_url: str | None = None,
        label: str | None = None,
    ) -> HumanRecordingEvent:
        event = HumanRecordingEvent.placeholder(step_index=step_index, page_url=page_url)
        if label:
            event = replace(event, label=label)
        self.record_event(event)
        return event

    def record_events(self, events: Iterable[HumanRecordingEvent]) -> None:
        for event in events:
            self.record_event(event)

    def end_session(self, note: str | None = None) -> None:
        notes = [note] if note else []
        self.record_event(
            HumanRecordingEvent(
                event_type=RecordingEventType.SESSION_ENDED,
                timestamp=utc_now_iso(),
                step_index=self._next_step_index(),
                page_url=self.config.start_url,
                notes=notes,
            )
        )

    def load_events(self) -> list[HumanRecordingEvent]:
        if not self.paths.events_path.exists():
            return []
        events: list[HumanRecordingEvent] = []
        for line in self.paths.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            events.append(
                HumanRecordingEvent(
                    event_type=RecordingEventType(payload["event_type"]),
                    timestamp=payload["timestamp"],
                    step_index=int(payload["step_index"]),
                    page_url=payload.get("page_url"),
                    locator=payload.get("locator"),
                    value=payload.get("value"),
                    label=payload.get("label"),
                    screenshot_path=payload.get("screenshot_path"),
                    notes=list(payload.get("notes", [])),
                    metadata=dict(payload.get("metadata", {})),
                )
            )
        return events

    def _next_step_index(self) -> int:
        events = self.load_events()
        if not events:
            return 0
        return max(event.step_index for event in events) + 1

    def _build_paths(self, config: RecordingSessionConfig) -> RecordingArtifactPaths:
        root = config.artifact_root or (Path("artifacts") / "stage2" / "human_loop")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = root / f"{timestamp}_{sanitize_name(config.session_id)}"
        return RecordingArtifactPaths(
            session_dir=session_dir,
            events_path=session_dir / "recording_events.jsonl",
            draft_path=session_dir / "candidate_template_draft.json",
            metadata_path=session_dir / "session.json",
        )
