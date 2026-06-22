from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.human_loop.models import RecordingEventType, RecordingSessionConfig
from prototype.stage2.app.human_loop.playwright_capture import (
    PlaywrightHumanLoopCapture,
    record_human_loop_from_cdp,
)
from prototype.stage2.app.human_loop.recorder import HumanLoopRecorder


class FakeFrame:
    def __init__(self, url: str) -> None:
        self.url = url
        self.evaluate_calls: list[tuple[object, object | None]] = []

    async def evaluate(self, script: object, arg: object | None = None) -> bool:
        self.evaluate_calls.append((script, arg))
        return True


class FakePage:
    def __init__(self, url: str, *, title: str = "Example Form") -> None:
        self.url = url
        self._title = title
        self.main_frame = FakeFrame(url)
        self.frames = [self.main_frame]
        self.exposed_bindings: dict[str, object] = {}
        self.init_scripts: list[str] = []
        self.listeners: dict[str, list[object]] = {}
        self.screenshot_calls: list[dict[str, object]] = []
        self.timeout_calls: list[int] = []
        self.load_state_calls: list[str] = []
        self.goto_calls: list[tuple[str, str]] = []
        self.brought_to_front = False

    async def expose_binding(self, name: str, callback: object) -> None:
        self.exposed_bindings[name] = callback

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def on(self, event: str, handler: object) -> None:
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: object) -> None:
        handlers = self.listeners.get(event, [])
        self.listeners[event] = [item for item in handlers if item is not handler]

    async def screenshot(self, *, path: str, full_page: bool) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-image")
        self.screenshot_calls.append({"path": path, "full_page": full_page})

    async def title(self) -> str:
        return self._title

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        self.timeout_calls.append(timeout_ms)

    async def bring_to_front(self) -> None:
        self.brought_to_front = True

    async def wait_for_load_state(self, state: str) -> None:
        self.load_state_calls.append(state)

    async def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_calls.append((url, wait_until))
        self.url = url
        self.main_frame.url = url


class FakeBrowserContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.contexts = [FakeBrowserContext(page)]
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.cdp_urls: list[str] = []

    async def connect_over_cdp(self, cdp_url: str) -> FakeBrowser:
        self.cdp_urls.append(cdp_url)
        return self.browser


class FakeAsyncPlaywrightContext:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)

    async def __aenter__(self) -> "FakeAsyncPlaywrightContext":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _build_config(tmp_path: Path, *, session_id: str) -> RecordingSessionConfig:
    return RecordingSessionConfig(
        session_id=session_id,
        template_name="playwright_capture_demo",
        start_url="https://example.test/form",
        task_description="record form submission",
        artifact_root=tmp_path / "artifacts",
        metadata={
            "project_field_candidates": ["remark", "cultivateDate"],
            "project_field_aliases": {
                "cultivateDate": ["栽培日期"],
            },
        },
    )


def test_record_human_loop_from_cdp_returns_candidate_review_and_capture_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage("https://example.test/form", title="Example Form")
    browser = FakeBrowser(page)
    playwright_context = FakeAsyncPlaywrightContext(browser)

    def _fake_async_playwright() -> FakeAsyncPlaywrightContext:
        return playwright_context

    monkeypatch.setattr(
        "prototype.stage2.app.human_loop.playwright_capture.async_playwright",
        _fake_async_playwright,
    )

    result = asyncio.run(
        record_human_loop_from_cdp(
            cdp_url="http://localhost:9222",
            config=_build_config(tmp_path, session_id="run_result_case"),
            duration_seconds=0,
        )
    )

    candidate_review_path = Path(result.candidate_review_path)
    summary_path = Path(result.summary_path or "")
    metadata_path = Path(result.metadata_path)

    assert candidate_review_path.name == "candidate_template_review.json"
    assert candidate_review_path.exists()
    assert summary_path.exists()
    assert metadata_path.exists()
    assert result.capture_summary is not None
    assert result.capture_summary["event_count"] == result.event_count
    assert result.capture_summary["event_type_counts"]["page_opened"] == 1
    assert result.capture_summary["event_type_counts"]["screenshot"] == 2

    candidate_review = json.loads(candidate_review_path.read_text(encoding="utf-8"))
    assert candidate_review["schema_version"] == "human_recording_template_candidate_review.v1"
    assert candidate_review["capture_summary"]["event_count"] == result.event_count

    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary_payload == result.capture_summary

    assert playwright_context.chromium.cdp_urls == ["http://localhost:9222"]
    assert page.brought_to_front is True
    assert page.load_state_calls == ["domcontentloaded"]
    assert page.timeout_calls == [0]
    assert browser.closed is True


def test_capture_screenshot_records_relative_path_and_system_metadata(tmp_path: Path) -> None:
    page = FakePage("https://example.test/form", title="Example Form")
    recorder = HumanLoopRecorder(_build_config(tmp_path, session_id="manual_boundary_case"))
    recorder.start_session()
    capture = PlaywrightHumanLoopCapture(page, recorder)

    asyncio.run(capture.capture_screenshot("session_started", "manual_capture_start.png"))

    screenshot_events = [
        event for event in recorder.load_events() if event.event_type == RecordingEventType.SCREENSHOT
    ]
    assert len(screenshot_events) == 1

    event = screenshot_events[0]
    assert event.screenshot_path == "screenshots/manual_capture_start.png"
    assert "\\" not in event.screenshot_path
    assert not Path(event.screenshot_path).is_absolute()
    assert event.metadata["source"] == "playwright.page.screenshot"
    assert event.metadata["interaction_source"] == "system"
    assert event.metadata["timestamp_source"] == "recorder_utc_iso"
    assert event.metadata["capture_kind"] == "manual_session_boundary"
    assert event.metadata["source_page_url"] == "https://example.test/form"
    assert event.metadata["source_page_title"] == "Example Form"
    assert event.metadata["frame"]["is_top"] is True
    assert (recorder.paths.session_dir / event.screenshot_path).exists()


def test_dom_event_records_action_and_context_screenshot_metadata(tmp_path: Path) -> None:
    page = FakePage("https://example.test/form", title="Example Form")
    recorder = HumanLoopRecorder(_build_config(tmp_path, session_id="dom_event_case"))
    recorder.start_session()
    capture = PlaywrightHumanLoopCapture(page, recorder)

    payload = {
        "event_type": "input",
        "timestamp": "2026-06-22T10:00:00+00:00",
        "page_url": "https://example.test/form",
        "locator": "input[name='remark']",
        "value": "hello world",
        "label": "备注",
        "metadata": {
            "source": "dom.input.field",
            "interaction_source": "browser_trusted",
            "timestamp_source": "browser_event_iso",
            "page": {
                "url": "https://example.test/form",
                "top_url": "https://example.test/form",
                "title": "Example Form",
            },
            "frame": {
                "name": "",
                "url": "https://example.test/form",
                "is_top": True,
            },
            "target": {
                "tag": "input",
                "type": "text",
                "name": "remark",
                "id": "",
                "role": "",
                "placeholder": "请输入备注",
            },
            "locator_candidates": {
                "preferred": "input[name='remark']",
                "name": "input[name='remark']",
                "placeholder": "input[placeholder='请输入备注']",
                "label_text": "备注",
            },
            "text": "备注",
        },
    }

    asyncio.run(capture._on_dom_event(None, payload))

    events = recorder.load_events()
    action_events = [event for event in events if event.event_type == RecordingEventType.INPUT]
    screenshot_events = [event for event in events if event.event_type == RecordingEventType.SCREENSHOT]

    assert len(action_events) == 1
    assert len(screenshot_events) == 1

    action_event = action_events[0]
    screenshot_event = screenshot_events[0]

    assert action_event.screenshot_path == "screenshots/actions/step_001_input.png"
    assert action_event.metadata["latest_action_screenshot"] == action_event.screenshot_path
    assert action_event.metadata["source"] == "dom.input.field"
    assert action_event.metadata["interaction_source"] == "browser_trusted"
    assert action_event.metadata["timestamp_source"] == "browser_event_iso"
    assert action_event.metadata["source_page_url"] == "https://example.test/form"
    assert action_event.metadata["source_page_title"] == "Example Form"
    assert action_event.metadata["frame_url"] == "https://example.test/form"
    assert action_event.metadata["element_type"] == "text"
    assert action_event.metadata["element_kind"] == "text"

    assert screenshot_event.screenshot_path == "screenshots/actions/step_001_input.png"
    assert screenshot_event.metadata["source"] == "playwright.page.screenshot.action"
    assert screenshot_event.metadata["interaction_source"] == "browser_trusted"
    assert screenshot_event.metadata["timestamp_source"] == "browser_event_iso"
    assert screenshot_event.metadata["capture_kind"] == "post_action"
    assert screenshot_event.metadata["linked_step_index"] == action_event.step_index
    assert screenshot_event.metadata["linked_event_type"] == "input"
    assert (recorder.paths.session_dir / screenshot_event.screenshot_path).exists()
