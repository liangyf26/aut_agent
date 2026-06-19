from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
import re
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


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
    summary_path: Path
    screenshot_index_path: Path


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
            metadata={
                "source": "placeholder.seed",
                "interaction_source": "placeholder",
                "timestamp_source": "recorder_utc_iso",
            },
        )


@dataclass(frozen=True)
class CandidateTemplateDraft:
    template_name: str
    version: str
    source_session_id: str
    page_entry: dict[str, Any]
    steps: list[dict[str, Any]]
    feature_point: dict[str, Any] | None = None
    execution_path: str | None = None
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "template_name": self.template_name,
            "version": self.version,
            "source_session_id": self.source_session_id,
            "page_entry": self.page_entry,
            "steps": self.steps,
            "notes": self.notes,
            "metadata": self.metadata,
        }
        if self.feature_point is not None:
            payload["feature_point"] = self.feature_point
        if self.execution_path is not None:
            payload["execution_path"] = self.execution_path
        return payload


def build_recording_summary(events: list[HumanRecordingEvent]) -> dict[str, Any]:
    event_type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    interaction_source_counts: dict[str, int] = {}
    timestamp_source_counts: dict[str, int] = {}
    element_kind_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}
    page_urls: list[str] = []
    frame_urls: list[str] = []
    seen_page_urls: set[str] = set()
    seen_frame_urls: set[str] = set()
    action_event_count = 0
    locator_event_count = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    key_screenshot_count = 0
    action_preview: list[dict[str, Any]] = []
    masked_value_event_count = 0
    missing_locator_count = 0
    missing_frame_url_count = 0
    missing_page_url_count = 0
    missing_interaction_source_count = 0
    missing_timestamp_source_count = 0
    missing_element_kind_count = 0

    for event in events:
        event_key = event.event_type.value
        event_type_counts[event_key] = event_type_counts.get(event_key, 0) + 1
        if event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            action_event_count += 1
            if event.locator:
                locator_event_count += 1
            else:
                missing_locator_count += 1

        source = str(event.metadata.get("source", "")).strip()
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1

        interaction_source = str(event.metadata.get("interaction_source", "")).strip()
        if interaction_source:
            interaction_source_counts[interaction_source] = interaction_source_counts.get(interaction_source, 0) + 1
        elif event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            missing_interaction_source_count += 1

        timestamp_source = str(event.metadata.get("timestamp_source", "")).strip()
        if timestamp_source:
            timestamp_source_counts[timestamp_source] = timestamp_source_counts.get(timestamp_source, 0) + 1
        elif event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            missing_timestamp_source_count += 1

        if event.page_url:
            if event.page_url not in seen_page_urls:
                seen_page_urls.add(event.page_url)
                page_urls.append(event.page_url)
        elif event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            missing_page_url_count += 1

        frame_url = _metadata_url(event.metadata.get("frame"))
        if frame_url:
            if frame_url not in seen_frame_urls:
                seen_frame_urls.add(frame_url)
                frame_urls.append(frame_url)
        elif event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            missing_frame_url_count += 1

        element_kind = _metadata_text(event.metadata.get("element_kind")) or _metadata_text(event.metadata.get("element_type"))
        if element_kind:
            element_kind_counts[element_kind] = element_kind_counts.get(element_kind, 0) + 1
        elif event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            missing_element_kind_count += 1

        if event.screenshot_path:
            key_screenshot_count += 1
        if event.metadata.get("value_masked"):
            masked_value_event_count += 1

        target_key = _summary_target_key(event)
        if target_key:
            target_counts[target_key] = target_counts.get(target_key, 0) + 1

        if first_timestamp is None:
            first_timestamp = event.timestamp
        last_timestamp = event.timestamp

        if (
            event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}
            and len(action_preview) < 20
        ):
            action_preview.append(
                {
                    "step_index": event.step_index,
                    "event_type": event.event_type.value,
                    "label": event.label,
                    "locator": event.locator,
                    "page_url": event.page_url,
                    "element_kind": element_kind,
                    "interaction_source": interaction_source or None,
                    "screenshot_path": event.screenshot_path,
                }
            )

    warnings: list[str] = []
    if event_type_counts.get(RecordingEventType.PLACEHOLDER.value, 0):
        warnings.append("Contains placeholder events; manual review is still required before template solidification.")
    if interaction_source_counts.get("scripted_untrusted", 0):
        warnings.append("Contains scripted_untrusted DOM events; review whether they came from page scripts or test helpers.")
    if interaction_source_counts.get("browser_trusted", 0):
        warnings.append(
            "browser_trusted means the browser marked the event as trusted; it can still include CDP/browser automation input, not only physical-human interaction."
        )
    if missing_locator_count:
        warnings.append("Some action events are missing locators; locator ranking or manual review is still required.")
    if missing_frame_url_count:
        warnings.append("Some action events are missing frame URL metadata; nested frame replay may need manual correction.")

    duration_ms = _duration_ms(first_timestamp, last_timestamp)

    return {
        "schema_version": "human_loop_summary.v3",
        "event_count": len(events),
        "action_event_count": action_event_count,
        "unique_target_count": len(target_counts),
        "locator_event_count": locator_event_count,
        "masked_value_event_count": masked_value_event_count,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "duration_ms": duration_ms,
        "event_type_counts": event_type_counts,
        "source_counts": source_counts,
        "interaction_source_counts": interaction_source_counts,
        "timestamp_source_counts": timestamp_source_counts,
        "element_kind_counts": element_kind_counts,
        "page_urls": page_urls,
        "frame_urls": frame_urls,
        "key_screenshot_count": key_screenshot_count,
        "action_preview": action_preview,
        "quality": {
            "action_events_missing_locator": missing_locator_count,
            "action_events_missing_page_url": missing_page_url_count,
            "action_events_missing_frame_url": missing_frame_url_count,
            "action_events_missing_interaction_source": missing_interaction_source_count,
            "action_events_missing_timestamp_source": missing_timestamp_source_count,
            "action_events_missing_element_kind": missing_element_kind_count,
        },
        "warnings": warnings,
    }


def build_key_screenshot_index(events: list[HumanRecordingEvent]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for event in events:
        if not event.screenshot_path:
            continue
        items.append(
            {
                "step_index": event.step_index,
                "event_type": event.event_type.value,
                "timestamp": event.timestamp,
                "label": event.label,
                "page_url": event.page_url,
                "locator": event.locator,
                "screenshot_path": event.screenshot_path,
                "interaction_source": _metadata_text(event.metadata.get("interaction_source")),
                "source": _metadata_text(event.metadata.get("source")),
                "element_kind": _metadata_text(event.metadata.get("element_kind"))
                or _metadata_text(event.metadata.get("element_type")),
                "capture_kind": _metadata_text(event.metadata.get("capture_kind")),
                "linked_step_index": event.metadata.get("linked_step_index"),
            }
        )
    return {
        "schema_version": "human_loop_key_screenshots.v1",
        "count": len(items),
        "items": items,
    }


def _metadata_url(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("url")
    if not value:
        return None
    text = str(value).strip()
    return text or None


def _metadata_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _summary_target_key(event: HumanRecordingEvent) -> str | None:
    target = event.metadata.get("target")
    if isinstance(target, dict):
        for value in (target.get("name"), target.get("id"), target.get("placeholder")):
            text = _summary_text(value)
            if text:
                return text
    for value in (event.locator, event.label, event.metadata.get("text")):
        text = _summary_text(value)
        if text:
            return text
    return None


def _summary_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _duration_ms(first_timestamp: str | None, last_timestamp: str | None) -> int | None:
    if not first_timestamp or not last_timestamp:
        return None
    try:
        first_value = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
        last_value = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((last_value - first_value).total_seconds() * 1000))
