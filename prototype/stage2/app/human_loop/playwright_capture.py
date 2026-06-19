from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Frame, Page, async_playwright

from prototype.stage2.app.runtime.artifacts import sanitize_name

from .drafts import MinimalCandidateTemplateDraftGenerator
from .models import HumanRecordingEvent, RecordingEventType, RecordingSessionConfig, utc_now_iso
from .recorder import HumanLoopRecorder


def _binding_name(session_id: str) -> str:
    return f"__stage2HumanLoopRecord_{sanitize_name(session_id)}"


@dataclass(frozen=True)
class PlaywrightCaptureResult:
    session_dir: str
    metadata_path: str
    events_path: str
    draft_path: str
    event_count: int


class PlaywrightHumanLoopCapture:
    def __init__(self, page: Page, recorder: HumanLoopRecorder) -> None:
        self.page = page
        self.recorder = recorder
        self.binding_name = _binding_name(recorder.config.session_id)
        self._step_index = 1
        self._frame_handler = None

    async def start(self) -> None:
        self.recorder.start_session()
        await self.page.expose_binding(self.binding_name, self._on_dom_event)
        await self.page.add_init_script(self._build_init_script())
        await self.page.evaluate(self._build_init_script())
        self._frame_handler = lambda frame: asyncio.create_task(self._record_frame_navigation(frame))
        self.page.on("framenavigated", self._frame_handler)
        await self._record_page_opened(self.page.url, label=await self.page.title())
        await self.capture_screenshot("session_started", "manual_capture_start.png")

    async def stop(self, note: str | None = None) -> PlaywrightCaptureResult:
        await self.capture_screenshot("session_finished", "manual_capture_end.png")
        self.recorder.end_session(note)
        if self._frame_handler is not None:
            self.page.remove_listener("framenavigated", self._frame_handler)
        events = self.recorder.load_events()
        MinimalCandidateTemplateDraftGenerator().write_draft(
            config=self.recorder.config,
            events=events,
            output_path=str(self.recorder.paths.draft_path),
        )
        return PlaywrightCaptureResult(
            session_dir=str(self.recorder.paths.session_dir),
            metadata_path=str(self.recorder.paths.metadata_path),
            events_path=str(self.recorder.paths.events_path),
            draft_path=str(self.recorder.paths.draft_path),
            event_count=len(events),
        )

    async def capture_screenshot(self, label: str, file_name: str) -> None:
        relative = Path("screenshots") / file_name
        target = self.recorder.paths.session_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(target), full_page=True)
        self.recorder.record_event(
            HumanRecordingEvent(
                event_type=RecordingEventType.SCREENSHOT,
                timestamp=utc_now_iso(),
                step_index=self._next_step(),
                page_url=self.page.url,
                label=label,
                screenshot_path=str(relative).replace("\\", "/"),
                metadata={"source": "playwright.page.screenshot"},
            )
        )

    async def _record_frame_navigation(self, frame: Frame) -> None:
        if frame != self.page.main_frame:
            return
        await self._record_page_opened(frame.url, label=await self.page.title())

    async def _record_page_opened(self, url: str, *, label: str | None = None) -> None:
        self.recorder.record_event(
            HumanRecordingEvent(
                event_type=RecordingEventType.PAGE_OPENED,
                timestamp=utc_now_iso(),
                step_index=self._next_step(),
                page_url=url,
                label=label or "page_opened",
                metadata={"source": "playwright.framenavigated"},
            )
        )

    async def _on_dom_event(self, _source: Any, payload: dict[str, Any]) -> None:
        event_type = _map_event_type(payload.get("event_type"))
        value = payload.get("value")
        self.recorder.record_event(
            HumanRecordingEvent(
                event_type=event_type,
                timestamp=utc_now_iso(),
                step_index=self._next_step(),
                page_url=payload.get("page_url") or self.page.url,
                locator=payload.get("locator"),
                value=value,
                label=payload.get("label"),
                notes=list(payload.get("notes", [])),
                metadata=dict(payload.get("metadata", {})),
            )
        )

    def _next_step(self) -> int:
        current = self._step_index
        self._step_index += 1
        return current

    def _build_init_script(self) -> str:
        binding_name = self.binding_name
        return f"""
        (() => {{
          const bindingName = {json.dumps(binding_name)};
          const captureKey = '__stage2HumanLoopCapture';
          function text(node) {{
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }}
          function cssPath(el) {{
            if (!el || !el.tagName) return '';
            if (el.id) return `#${{el.id}}`;
            const parts = [];
            let current = el;
            while (current && current.nodeType === 1 && parts.length < 5) {{
              let selector = current.tagName.toLowerCase();
              if (current.classList && current.classList.length) {{
                selector += '.' + Array.from(current.classList).slice(0, 2).join('.');
              }}
              const parent = current.parentElement;
              if (parent) {{
                const siblings = Array.from(parent.children).filter(x => x.tagName === current.tagName);
                if (siblings.length > 1) {{
                  selector += `:nth-of-type(${{siblings.indexOf(current) + 1}})`;
                }}
              }}
              parts.unshift(selector);
              current = parent;
            }}
            return parts.join(' > ');
          }}
          function emit(payload) {{
            const fn = window[bindingName];
            if (typeof fn === 'function') {{
              fn(payload);
            }}
          }}
          function install() {{
            if (window[captureKey]?.installed && window[captureKey]?.bindingName === bindingName) {{
              return;
            }}
            window[captureKey] = {{ installed: true, bindingName }};
            document.addEventListener('click', (event) => {{
              const target = event.target?.closest('button, a, [role="button"], [role="tab"], input, select, textarea, .el-button');
              if (!target) return;
              emit({{
                event_type: 'click',
                page_url: location.href,
                label: text(target),
                locator: cssPath(target),
                metadata: {{
                  tag: target.tagName.toLowerCase(),
                  role: target.getAttribute('role') || '',
                  type: target.getAttribute('type') || '',
                }},
              }});
            }}, true);
            document.addEventListener('change', (event) => {{
              const target = event.target;
              if (!target || !('value' in target)) return;
              const tag = target.tagName.toLowerCase();
              emit({{
                event_type: tag === 'select' ? 'select' : 'input',
                page_url: location.href,
                label: text(target.closest('label, .el-form-item, .form-group, td, div') || target),
                locator: cssPath(target),
                value: target.type === 'password' ? '[masked]' : target.value,
                metadata: {{
                  tag,
                  type: target.getAttribute('type') || '',
                  name: target.getAttribute('name') || '',
                }},
              }});
            }}, true);
          }}
          install();
        }})();
        """

async def record_human_loop_from_cdp(
    *,
    cdp_url: str,
    config: RecordingSessionConfig,
    duration_seconds: int,
) -> PlaywrightCaptureResult:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = await _resolve_target_page(browser, config.start_url or "")
            recorder = HumanLoopRecorder(config)
            capture = PlaywrightHumanLoopCapture(page, recorder)
            await capture.start()
            await page.wait_for_timeout(max(0, duration_seconds) * 1000)
            return await capture.stop("完成一轮人工录制采集")
        finally:
            await browser.close()


async def _resolve_target_page(browser: Browser, target_url: str) -> Page:
    pages: list[Page] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError("未发现可用页面，无法执行 human loop 录制")
    page = next((item for item in pages if target_url and target_url in item.url), pages[0])
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    if target_url and target_url not in page.url:
        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
    return page


def _map_event_type(value: str | None) -> RecordingEventType:
    mapping = {
        "page_opened": RecordingEventType.PAGE_OPENED,
        "click": RecordingEventType.CLICK,
        "input": RecordingEventType.INPUT,
        "select": RecordingEventType.SELECT,
    }
    return mapping.get((value or "").strip().lower(), RecordingEventType.NOTE)
