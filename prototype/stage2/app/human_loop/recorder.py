from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from prototype.stage2.app.runtime.artifacts import sanitize_name

from .models import (
    build_recording_summary,
    build_key_screenshot_index,
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
        self._session_started_at: str | None = None

    def start_session(self) -> RecordingArtifactPaths:
        self.paths.session_dir.mkdir(parents=True, exist_ok=True)
        self._session_started_at = utc_now_iso()
        self.paths.metadata_path.write_text(
            json.dumps(
                {
                    "session": self.config.to_dict(),
                    "started_at": self._session_started_at,
                    "capture_readiness": {
                        "status": "recording_session_started",
                        "notes": [
                            "Event capture metadata schema is ready for click/input/select stability work.",
                            "Some browser-level distinctions still depend on real-session validation against the target app.",
                        ],
                    },
                    "summary": {
                        "schema_version": "human_loop_summary.v3",
                        "event_count": 0,
                        "action_event_count": 0,
                        "first_timestamp": None,
                        "last_timestamp": None,
                        "duration_ms": None,
                        "event_type_counts": {},
                        "source_counts": {},
                        "interaction_source_counts": {},
                        "timestamp_source_counts": {},
                        "element_kind_counts": {},
                        "page_urls": [],
                        "frame_urls": [],
                        "key_screenshot_count": 0,
                        "action_preview": [],
                        "quality": {
                            "action_events_missing_locator": 0,
                            "action_events_missing_page_url": 0,
                            "action_events_missing_frame_url": 0,
                            "action_events_missing_interaction_source": 0,
                            "action_events_missing_timestamp_source": 0,
                            "action_events_missing_element_kind": 0,
                        },
                        "warnings": [],
                    },
                    "key_screenshots": {
                        "schema_version": "human_loop_key_screenshots.v1",
                        "count": 0,
                        "items": [],
                    },
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
                metadata={
                    "template_name": self.config.template_name,
                    "source": "recorder.session",
                    "interaction_source": "system",
                    "timestamp_source": "recorder_utc_iso",
                },
            )
        )
        return self.paths

    def record_event(self, event: HumanRecordingEvent) -> None:
        self.paths.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._refresh_session_metadata()

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
                metadata={
                    "source": "recorder.session",
                    "interaction_source": "system",
                    "timestamp_source": "recorder_utc_iso",
                },
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
            summary_path=session_dir / "recording_summary.json",
            screenshot_index_path=session_dir / "key_screenshots.json",
        )

    def _refresh_session_metadata(self) -> None:
        payload = self._read_session_metadata()
        payload["session"] = self.config.to_dict()
        payload["started_at"] = payload.get("started_at") or self._session_started_at or utc_now_iso()
        payload["updated_at"] = utc_now_iso()
        events = self.load_events()
        summary = build_recording_summary(events)
        key_screenshots = build_key_screenshot_index(events)
        payload["summary"] = summary
        payload["key_screenshots"] = key_screenshots
        self.paths.metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.paths.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.paths.screenshot_index_path.write_text(
            json.dumps(key_screenshots, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_session_metadata(self) -> dict[str, object]:
        if not self.paths.metadata_path.exists():
            return {}
        try:
            return json.loads(self.paths.metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
