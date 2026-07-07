from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.human_loop.models import (
    HumanRecordingEvent,
    RecordingEventType,
    RecordingSessionConfig,
)
from prototype.stage2.app.human_loop.recorder import HumanLoopRecorder


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _build_config(tmp_path: Path, *, session_id: str = "record-demo") -> RecordingSessionConfig:
    return RecordingSessionConfig(
        session_id=session_id,
        template_name="demo_template",
        operator_id="operator-1",
        start_url="https://example.test/home",
        task_description="capture demo flow",
        artifact_root=tmp_path / "human_loop",
        metadata={"env": "test"},
    )


def _build_action_event(
    *,
    event_type: RecordingEventType,
    step_index: int,
    timestamp: str,
    page_url: str = "https://example.test/form",
    locator: str,
    label: str,
    value: object | None = None,
    screenshot_path: str | None = None,
    element_kind: str,
) -> HumanRecordingEvent:
    return HumanRecordingEvent(
        event_type=event_type,
        timestamp=timestamp,
        step_index=step_index,
        page_url=page_url,
        locator=locator,
        label=label,
        value=value,
        screenshot_path=screenshot_path,
        metadata={
            "source": "playwright.dom",
            "interaction_source": "browser_trusted",
            "timestamp_source": "browser_event_iso",
            "frame": {
                "url": page_url,
                "is_top": True,
            },
            "element_kind": element_kind,
            "target": {
                "name": label,
            },
        },
    )


def test_start_session_creates_artifact_paths_and_initial_metadata(tmp_path: Path) -> None:
    config = _build_config(tmp_path, session_id="demo session/01")
    recorder = HumanLoopRecorder(config)

    paths = recorder.start_session()

    assert paths == recorder.paths
    assert re.match(r"^\d{8}_\d{6}_demo_session_01$", paths.session_dir.name)
    assert paths.session_dir.exists()
    assert paths.events_path.exists()
    assert paths.metadata_path.exists()
    assert paths.summary_path.exists()
    assert paths.screenshot_index_path.exists()
    assert paths.draft_path.name == "candidate_template_draft.json"
    assert paths.candidate_review_path.name == "candidate_template_review.json"

    metadata = _read_json(paths.metadata_path)
    summary = _read_json(paths.summary_path)
    screenshot_index = _read_json(paths.screenshot_index_path)
    events = _read_jsonl(paths.events_path)

    assert metadata["session"] == config.to_dict()
    assert metadata["started_at"]
    assert metadata["updated_at"]
    assert metadata["capture_readiness"]["status"] == "recording_session_started"
    assert summary == metadata["summary"]
    assert screenshot_index == metadata["key_screenshots"]
    assert summary["schema_version"] == "human_loop_summary.v3"
    assert summary["event_count"] == 1
    assert summary["action_event_count"] == 0
    assert summary["event_type_counts"] == {"session_started": 1}
    assert summary["page_urls"] == ["https://example.test/home"]
    assert summary["key_screenshot_count"] == 0
    assert screenshot_index["count"] == 0

    assert len(events) == 1
    assert events[0]["event_type"] == "session_started"
    assert events[0]["step_index"] == 0
    assert events[0]["page_url"] == "https://example.test/home"
    assert events[0]["metadata"]["source"] == "recorder.session"


def test_record_event_and_placeholder_refresh_summary_and_screenshot_index(tmp_path: Path) -> None:
    recorder = HumanLoopRecorder(_build_config(tmp_path))
    paths = recorder.start_session()

    recorder.record_event(
        _build_action_event(
            event_type=RecordingEventType.CLICK,
            step_index=1,
            timestamp="2026-06-21T10:00:01+00:00",
            locator="button[type='submit']",
            label="Submit",
            screenshot_path="screenshots/submit.png",
            element_kind="button",
        )
    )
    placeholder = recorder.record_placeholder_event(
        2,
        page_url="https://example.test/form",
        label="capture gap marker",
    )

    events = _read_jsonl(paths.events_path)
    summary = _read_json(paths.summary_path)
    metadata = _read_json(paths.metadata_path)
    screenshot_index = _read_json(paths.screenshot_index_path)

    assert len(events) == 3
    assert events[1]["event_type"] == "click"
    assert events[1]["screenshot_path"] == "screenshots/submit.png"
    assert events[2]["event_type"] == "placeholder"
    assert events[2]["label"] == "capture gap marker"
    assert placeholder.event_type == RecordingEventType.PLACEHOLDER
    assert placeholder.label == "capture gap marker"
    assert placeholder.notes == ["placeholder event for future browser/CDP/Playwright capture"]
    assert placeholder.metadata["source"] == "placeholder.seed"

    assert summary == metadata["summary"]
    assert screenshot_index == metadata["key_screenshots"]
    assert summary["event_count"] == 3
    assert summary["action_event_count"] == 1
    assert summary["locator_event_count"] == 1
    assert summary["key_screenshot_count"] == 1
    assert summary["event_type_counts"]["click"] == 1
    assert summary["event_type_counts"]["placeholder"] == 1
    assert summary["page_urls"] == [
        "https://example.test/home",
        "https://example.test/form",
    ]
    assert summary["frame_urls"] == ["https://example.test/form"]
    assert summary["quality"] == {
        "action_events_missing_locator": 0,
        "action_events_missing_page_url": 0,
        "action_events_missing_frame_url": 0,
        "action_events_missing_interaction_source": 0,
        "action_events_missing_timestamp_source": 0,
        "action_events_missing_element_kind": 0,
    }
    assert any("Contains placeholder events" in warning for warning in summary["warnings"])
    assert any("browser marked the event as trusted" in warning for warning in summary["warnings"])

    assert screenshot_index["count"] == 1
    assert screenshot_index["items"] == [
        {
            "step_index": 1,
            "event_type": "click",
            "timestamp": "2026-06-21T10:00:01+00:00",
            "label": "Submit",
            "page_url": "https://example.test/form",
            "locator": "button[type='submit']",
            "screenshot_path": "screenshots/submit.png",
            "interaction_source": "browser_trusted",
            "source": "playwright.dom",
            "element_kind": "button",
            "capture_kind": None,
            "linked_step_index": None,
        }
    ]


def test_end_session_records_terminal_event_and_updates_summary_counts(tmp_path: Path) -> None:
    recorder = HumanLoopRecorder(_build_config(tmp_path))
    paths = recorder.start_session()
    recorder.record_event(
        _build_action_event(
            event_type=RecordingEventType.INPUT,
            step_index=4,
            timestamp="2026-06-21T10:00:05+00:00",
            locator="input[name='title']",
            label="Title",
            value="Hello",
            element_kind="input",
        )
    )

    recorder.end_session("manual review completed")

    events = recorder.load_events()
    summary = _read_json(paths.summary_path)
    metadata = _read_json(paths.metadata_path)
    screenshot_index = _read_json(paths.screenshot_index_path)
    last_event = events[-1]

    assert last_event.event_type == RecordingEventType.SESSION_ENDED
    assert last_event.step_index == 5
    assert last_event.notes == ["manual review completed"]
    assert last_event.metadata == {
        "source": "recorder.session",
        "interaction_source": "system",
        "timestamp_source": "recorder_utc_iso",
    }

    assert metadata["started_at"]
    assert metadata["updated_at"]
    assert summary == metadata["summary"]
    assert summary["event_count"] == 3
    assert summary["action_event_count"] == 1
    assert summary["event_type_counts"] == {
        "session_started": 1,
        "input": 1,
        "session_ended": 1,
    }
    assert summary["action_preview"] == [
        {
            "step_index": 4,
            "event_type": "input",
            "label": "Title",
            "locator": "input[name='title']",
            "page_url": "https://example.test/form",
            "element_kind": "input",
            "interaction_source": "browser_trusted",
            "screenshot_path": None,
        }
    ]
    assert summary["last_timestamp"] == last_event.timestamp
    assert screenshot_index["count"] == 0
