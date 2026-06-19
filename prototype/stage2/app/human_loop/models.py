from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RecordingEventType(StrEnum):
    SESSION_STARTED = "session_started"
    PAGE_OPENED = "page_opened"
    CLICK = "click"
    INPUT = "input"
    SELECT = "select"
    SCREENSHOT = "screenshot"
    NOTE = "note"
    CHECKPOINT = "checkpoint"
    SESSION_ENDED = "session_ended"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True)
class RecordingSessionConfig:
    session_id: str
    template_name: str
    operator_id: str | None = None
    start_url: str | None = None
    task_description: str | None = None
    artifact_root: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "template_name": self.template_name,
            "operator_id": self.operator_id,
            "start_url": self.start_url,
            "task_description": self.task_description,
            "artifact_root": str(self.artifact_root) if self.artifact_root else None,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class RecordingArtifactPaths:
    session_dir: Path
    events_path: Path
    draft_path: Path
    metadata_path: Path


@dataclass(frozen=True)
class HumanRecordingEvent:
    event_type: RecordingEventType
    timestamp: str
    step_index: int
    page_url: str | None = None
    locator: str | None = None
    value: Any = None
    label: str | None = None
    screenshot_path: str | None = None
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "step_index": self.step_index,
            "page_url": self.page_url,
            "locator": self.locator,
            "value": self.value,
            "label": self.label,
            "screenshot_path": self.screenshot_path,
            "notes": self.notes,
            "metadata": self.metadata,
        }

    @classmethod
    def placeholder(
        cls,
        step_index: int,
        *,
        page_url: str | None = None,
        note: str = "placeholder event for future browser/CDP/Playwright capture",
    ) -> "HumanRecordingEvent":
        return cls(
            event_type=RecordingEventType.PLACEHOLDER,
            timestamp=utc_now_iso(),
            step_index=step_index,
            page_url=page_url,
            notes=[note],
        )


@dataclass(frozen=True)
class CandidateTemplateDraft:
    template_name: str
    version: str
    source_session_id: str
    page_entry: dict[str, Any]
    steps: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "version": self.version,
            "source_session_id": self.source_session_id,
            "page_entry": self.page_entry,
            "steps": self.steps,
            "notes": self.notes,
            "metadata": self.metadata,
        }
