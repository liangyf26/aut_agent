from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Error as PlaywrightError, Frame, Page, async_playwright

from prototype.stage2.app.runtime.artifacts import sanitize_name

from .drafts import MinimalCandidateTemplateDraftGenerator
from .models import (
    build_recording_summary,
    HumanRecordingEvent,
    RecordingEventType,
    RecordingSessionConfig,
    utc_now_iso,
)
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
    summary_path: str | None = None
    screenshot_index_path: str | None = None
    capture_summary: dict[str, Any] | None = None


class PlaywrightHumanLoopCapture:
    def __init__(self, page: Page, recorder: HumanLoopRecorder) -> None:
        self.page = page
        self.recorder = recorder
        self.binding_name = _binding_name(recorder.config.session_id)
        self._step_index = 1
        self._interaction_sequence = 1
        self._frame_navigation_handler = None
        self._frame_attach_handler = None

    async def start(self) -> None:
        self.recorder.start_session()
        await self.page.expose_binding(self.binding_name, self._on_dom_event)
        script = self._build_init_script()
        await self.page.add_init_script(script)
        await self._install_capture_for_existing_frames(script)
        self._frame_navigation_handler = lambda frame: asyncio.create_task(self._handle_frame_navigated(frame))
        self._frame_attach_handler = lambda frame: asyncio.create_task(self._ensure_capture_installed(frame))
        self.page.on("framenavigated", self._frame_navigation_handler)
        self.page.on("frameattached", self._frame_attach_handler)
        await self._record_page_opened(self.page.url, label=await self._safe_page_title())
        await self.capture_screenshot("session_started", "manual_capture_start.png")

    async def stop(self, note: str | None = None) -> PlaywrightCaptureResult:
        await self._flush_pending_dom_events("capture_stop")
        await self.capture_screenshot("session_finished", "manual_capture_end.png")
        self.recorder.end_session(note)
        if self._frame_navigation_handler is not None:
            self.page.remove_listener("framenavigated", self._frame_navigation_handler)
        if self._frame_attach_handler is not None:
            self.page.remove_listener("frameattached", self._frame_attach_handler)
        events = self.recorder.load_events()
        MinimalCandidateTemplateDraftGenerator().write_draft(
            config=self.recorder.config,
            events=events,
            output_path=str(self.recorder.paths.draft_path),
        )
        capture_summary = self._load_capture_summary(events)
        return PlaywrightCaptureResult(
            session_dir=str(self.recorder.paths.session_dir),
            metadata_path=str(self.recorder.paths.metadata_path),
            events_path=str(self.recorder.paths.events_path),
            draft_path=str(self.recorder.paths.draft_path),
            summary_path=str(self.recorder.paths.summary_path),
            screenshot_index_path=str(self.recorder.paths.screenshot_index_path),
            event_count=len(events),
            capture_summary=capture_summary,
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
                metadata={
                    "source": "playwright.page.screenshot",
                    "interaction_source": "system",
                    "timestamp_source": "recorder_utc_iso",
                    "capture_kind": "manual_session_boundary",
                    "source_page_url": self.page.url,
                    "source_page_title": await self._safe_page_title(),
                    "page": {
                        "url": self.page.url,
                        "top_url": self.page.url,
                        "title": await self._safe_page_title(),
                    },
                    "frame": {
                        "name": "",
                        "url": self.page.url,
                        "is_top": True,
                    },
                },
            )
        )

    async def _handle_frame_navigated(self, frame: Frame) -> None:
        await self._ensure_capture_installed(frame)
        if frame != self.page.main_frame:
            return
        await self._record_page_opened(frame.url, label=await self._safe_page_title())

    async def _record_page_opened(self, url: str, *, label: str | None = None) -> None:
        self.recorder.record_event(
            HumanRecordingEvent(
                event_type=RecordingEventType.PAGE_OPENED,
                timestamp=utc_now_iso(),
                step_index=self._next_step(),
                page_url=url,
                label=label or "page_opened",
                metadata={
                    "source": "playwright.framenavigated",
                    "interaction_source": "system",
                    "timestamp_source": "recorder_utc_iso",
                    "source_page_url": url,
                    "source_page_title": label,
                    "page": {
                        "url": url,
                        "top_url": url,
                        "title": label,
                    },
                    "frame": {
                        "name": "",
                        "url": url,
                        "is_top": True,
                    },
                },
            )
        )

    async def _on_dom_event(self, _source: Any, payload: dict[str, Any]) -> None:
        normalized_payload = self._normalize_dom_payload(payload)
        event_type = _map_event_type(normalized_payload.get("event_type"))
        value = normalized_payload.get("value")
        step_index = self._next_step()
        metadata = dict(normalized_payload.get("metadata", {}))
        metadata["interaction_sequence_id"] = self._interaction_sequence
        screenshot_path: str | None = None
        if event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            screenshot_path = await self._capture_action_screenshot(step_index, event_type)
            if screenshot_path:
                metadata["latest_action_screenshot"] = screenshot_path
        event = HumanRecordingEvent(
            event_type=event_type,
            timestamp=str(normalized_payload.get("timestamp") or utc_now_iso()),
            step_index=step_index,
            page_url=normalized_payload.get("page_url") or self.page.url,
            locator=normalized_payload.get("locator"),
            value=value,
            label=normalized_payload.get("label"),
            screenshot_path=screenshot_path,
            notes=list(normalized_payload.get("notes", [])),
            metadata=metadata,
        )
        self.recorder.record_event(event)
        if screenshot_path and event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
            self.recorder.record_event(
                HumanRecordingEvent(
                    event_type=RecordingEventType.SCREENSHOT,
                    timestamp=utc_now_iso(),
                    step_index=self._next_step(),
                    page_url=event.page_url,
                    locator=event.locator,
                    label=f"{event.event_type.value}_context",
                    screenshot_path=screenshot_path,
                    metadata={
                        **metadata,
                        "source": "playwright.page.screenshot.action",
                        "capture_kind": "post_action",
                        "linked_step_index": event.step_index,
                        "linked_event_type": event.event_type.value,
                    },
                )
            )
        self._interaction_sequence += 1

    def _next_step(self) -> int:
        current = self._step_index
        self._step_index += 1
        return current

    async def _install_capture_for_existing_frames(self, script: str) -> None:
        for frame in self.page.frames:
            await self._ensure_capture_installed(frame, script=script)

    async def _ensure_capture_installed(self, frame: Frame, *, script: str | None = None) -> None:
        try:
            await frame.evaluate(script or self._build_init_script())
        except PlaywrightError:
            # Detached or rapidly reloading frames can fail injection transiently.
            # We keep the recorder schema ready and expose the uncertainty in metadata/session summary instead of
            # pretending every frame was definitely instrumented.
            return

    async def _safe_page_title(self) -> str | None:
        try:
            title = await self.page.title()
        except PlaywrightError:
            return None
        title = (title or "").strip()
        return title or None

    async def _flush_pending_dom_events(self, reason: str) -> None:
        script = """
            ({ captureKey, reason }) => {
              const state = window[captureKey];
              if (state && typeof state.flushPending === 'function') {
                state.flushPending(String(reason || 'manual_flush'));
                return true;
              }
              return false;
            }
            """
        for frame in list(self.page.frames):
            try:
                await frame.evaluate(
                    script,
                    {"captureKey": "__stage2HumanLoopCapture", "reason": reason},
                )
            except PlaywrightError:
                continue

    def _load_capture_summary(self, events: list[HumanRecordingEvent]) -> dict[str, Any]:
        try:
            payload = json.loads(self.recorder.paths.metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            payload = {}
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return summary
        return build_recording_summary(events)

    def _normalize_dom_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        normalized: dict[str, Any] = dict(payload or {})
        metadata = normalized.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        else:
            metadata = dict(metadata)

        page_url = str(normalized.get("page_url") or self.page.url or "").strip() or None
        label = normalized.get("label")
        if label is not None:
            label = str(label).strip() or None
        locator = normalized.get("locator")
        if locator is not None:
            locator = str(locator).strip() or None

        notes = normalized.get("notes")
        if isinstance(notes, list):
            normalized["notes"] = [str(item) for item in notes if str(item).strip()]
        else:
            normalized["notes"] = []

        locator_candidates = metadata.get("locator_candidates")
        if isinstance(locator_candidates, dict):
            locator_candidates = dict(locator_candidates)
        else:
            locator_candidates = {}

        preferred_locator = locator_candidates.get("preferred")
        if not locator and preferred_locator:
            locator = str(preferred_locator).strip() or None

        text_value = metadata.get("text")
        if text_value is not None:
            text_value = str(text_value).strip() or None
        if not label:
            label = text_value

        page_metadata = metadata.get("page")
        if not isinstance(page_metadata, dict):
            page_metadata = {}
        else:
            page_metadata = dict(page_metadata)

        frame_metadata = metadata.get("frame")
        if not isinstance(frame_metadata, dict):
            frame_metadata = {}
        else:
            frame_metadata = dict(frame_metadata)

        target_metadata = metadata.get("target")
        if not isinstance(target_metadata, dict):
            target_metadata = {}
        else:
            target_metadata = dict(target_metadata)

        metadata.setdefault("capture_version", "playwright_cdp_dom_capture.v2")
        metadata.setdefault("source", "playwright.dom")
        metadata.setdefault("interaction_source", "unknown")
        metadata.setdefault("timestamp_source", "recorder_utc_iso")
        page_metadata.setdefault("url", page_url)
        page_metadata.setdefault("top_url", self.page.url)
        page_metadata.setdefault("title", None)
        page_metadata.setdefault("referrer", "")
        frame_metadata.setdefault("name", "")
        frame_metadata.setdefault("url", page_url)
        frame_metadata.setdefault("is_top", True)
        target_metadata.setdefault("tag", "")
        target_metadata.setdefault("type", "")
        target_metadata.setdefault("name", "")
        target_metadata.setdefault("id", "")
        target_metadata.setdefault("role", "")
        target_metadata.setdefault("placeholder", "")
        metadata["text"] = text_value
        metadata["locator_candidates"] = locator_candidates
        metadata["page"] = page_metadata
        metadata["frame"] = frame_metadata
        metadata["target"] = target_metadata
        metadata["element_type"] = str(target_metadata.get("type") or "").strip() or None
        metadata["element_kind"] = self._derive_element_kind(target_metadata)
        metadata["element_text"] = text_value
        metadata["source_page_url"] = str(page_metadata.get("url") or page_url or "").strip() or None
        metadata["source_page_title"] = str(page_metadata.get("title") or "").strip() or None
        metadata["frame_name"] = str(frame_metadata.get("name") or "").strip() or None
        metadata["frame_url"] = str(frame_metadata.get("url") or "").strip() or None
        metadata["recorded_at_utc"] = utc_now_iso()

        normalized["page_url"] = page_url
        normalized["label"] = label
        normalized["locator"] = locator
        normalized["metadata"] = metadata
        return normalized

    async def _capture_action_screenshot(self, step_index: int, event_type: RecordingEventType) -> str | None:
        file_name = f"step_{step_index:03d}_{event_type.value}.png"
        relative = Path("screenshots") / "actions" / file_name
        target = self.recorder.paths.session_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self.page.screenshot(path=str(target), full_page=False)
        except PlaywrightError:
            return None
        return str(relative).replace("\\", "/")

    def _derive_element_kind(self, target_metadata: dict[str, Any]) -> str | None:
        tag = str(target_metadata.get("tag") or "").strip().lower()
        field_type = str(target_metadata.get("type") or "").strip().lower()
        role = str(target_metadata.get("role") or "").strip().lower()
        if tag == "select":
            return "select"
        if tag == "textarea":
            return "textarea"
        if tag == "a":
            return "link"
        if tag == "button" or role == "button":
            return "button"
        if field_type in {"checkbox", "radio"}:
            return field_type
        if tag == "input":
            return field_type or "input"
        if role:
            return role
        return tag or None

    def _build_init_script(self) -> str:
        binding_name = self.binding_name
        return f"""
        (() => {{
          const bindingName = {json.dumps(binding_name)};
          const captureKey = '__stage2HumanLoopCapture';
          const previousState = window[captureKey];
          if (previousState && Array.isArray(previousState.cleanup)) {{
            for (const cleanup of previousState.cleanup) {{
              try {{
                cleanup();
              }} catch (_err) {{
              }}
            }}
          }}

          const state = {{
            installed: false,
            bindingName,
            cleanup: [],
            nodeSeq: previousState?.nodeSeq || 0,
            pendingInputs: new Map(),
            lastCommitted: new Map(),
          }};
          window[captureKey] = state;

          function nowIso() {{
            return new Date().toISOString();
          }}

          function normalizeText(value) {{
            return String(value || '').replace(/\\s+/g, ' ').trim();
          }}

          function shortText(value, maxLength = 240) {{
            const normalized = normalizeText(value);
            if (normalized.length <= maxLength) {{
              return normalized;
            }}
            return normalized.slice(0, maxLength);
          }}

          function escapeAttrValue(value) {{
            return String(value || '').replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\\"');
          }}

          function text(node) {{
            if (!node) {{
              return '';
            }}
            return shortText(node.innerText || node.textContent || '');
          }}

          function domTargetFromEvent(event) {{
            const first = typeof event?.composedPath === 'function' ? event.composedPath()[0] : event?.target;
            return first && first.nodeType === 1 ? first : event?.target;
          }}

          function targetKey(target) {{
            if (!target) {{
              return '';
            }}
            if (!target.__stage2HumanLoopNodeId) {{
              state.nodeSeq += 1;
              target.__stage2HumanLoopNodeId = `node_${{state.nodeSeq}}`;
            }}
            return target.__stage2HumanLoopNodeId;
          }}

          function topUrl() {{
            try {{
              return window.top?.location?.href || location.href;
            }} catch (_err) {{
              return location.href;
            }}
          }}

          function frameName() {{
            if (window.self === window.top) {{
              return '';
            }}
            try {{
              return window.frameElement?.getAttribute('name') || window.frameElement?.id || '';
            }} catch (_err) {{
              return '';
            }}
          }}

          function cssPath(el) {{
            if (!el || !el.tagName) return '';
            if (el.id) return `#${{el.id}}`;
            const parts = [];
            let current = el;
            while (current && current.nodeType === 1 && parts.length < 6) {{
              let selector = current.tagName.toLowerCase();
              const name = current.getAttribute?.('name');
              const dataTestId = current.getAttribute?.('data-testid') || current.getAttribute?.('data-testid');
              if (dataTestId) {{
                selector += `[data-testid="${{escapeAttrValue(dataTestId)}}"]`;
                parts.unshift(selector);
                break;
              }}
              if (name) {{
                selector += `[name="${{escapeAttrValue(name)}}"]`;
              }} else if (current.classList && current.classList.length) {{
                selector += '.' + Array.from(current.classList).slice(0, 2).join('.');
              }}
              const parent = current.parentElement;
              if (parent) {{
                const siblings = Array.from(parent.children).filter((item) => item.tagName === current.tagName);
                if (siblings.length > 1) {{
                  selector += `:nth-of-type(${{siblings.indexOf(current) + 1}})`;
                }}
              }}
              parts.unshift(selector);
              current = parent;
            }}
            return parts.join(' > ');
          }}

          function associatedLabelText(target) {{
            if (!target) {{
              return '';
            }}
            if (Array.isArray(target.labels) && target.labels.length) {{
              return shortText(target.labels.map((item) => text(item)).filter(Boolean).join(' '));
            }}
            if (target.labels && target.labels.length) {{
              return shortText(Array.from(target.labels).map((item) => text(item)).filter(Boolean).join(' '));
            }}
            const labelledBy = target.getAttribute?.('aria-labelledby');
            if (labelledBy) {{
              const value = labelledBy
                .split(/\\s+/)
                .map((id) => document.getElementById(id))
                .filter(Boolean)
                .map((item) => text(item))
                .filter(Boolean)
                .join(' ');
              if (value) {{
                return shortText(value);
              }}
            }}
            const wrapper = target.closest?.('label, .el-form-item, .form-group, td, th');
            return shortText(text(wrapper) || '');
          }}

          function fieldType(target) {{
            if (!target || !target.tagName) {{
              return '';
            }}
            const tag = target.tagName.toLowerCase();
            if (tag === 'textarea') {{
              return 'textarea';
            }}
            if (target.isContentEditable) {{
              return 'contenteditable';
            }}
            return String(target.getAttribute?.('type') || '').toLowerCase();
          }}

          function isTextualField(target) {{
            if (!target || !target.tagName) {{
              return false;
            }}
            const tag = target.tagName.toLowerCase();
            if (tag === 'textarea' || target.isContentEditable) {{
              return true;
            }}
            if (tag !== 'input') {{
              return false;
            }}
            const type = fieldType(target);
            return !['button', 'submit', 'reset', 'checkbox', 'radio', 'range', 'color', 'file', 'image'].includes(type);
          }}

          function maskValue(target, rawValue) {{
            const type = fieldType(target);
            const name = String(target?.getAttribute?.('name') || '').toLowerCase();
            const id = String(target?.id || '').toLowerCase();
            const sensitiveHint = /password|passwd|captcha|token|secret/.test(`${{type}} ${{name}} ${{id}}`);
            if (type === 'password' || sensitiveHint) {{
              return {{ value: '[masked]', masked: true }};
            }}
            if (type === 'file') {{
              const files = Array.from(target?.files || []).map((file) => file?.name).filter(Boolean);
              return {{ value: files.length ? files : '[file-selected]', masked: true }};
            }}
            return {{ value: rawValue, masked: false }};
          }}

          function readValue(target) {{
            if (!target) {{
              return {{ value: null, masked: false }};
            }}
            if (target.tagName?.toLowerCase() === 'select' && target.multiple) {{
              const values = Array.from(target.selectedOptions || []).map((option) => option.value);
              return maskValue(target, values);
            }}
            if (target.isContentEditable) {{
              return maskValue(target, normalizeText(target.innerText || target.textContent || ''));
            }}
            if (Object.prototype.hasOwnProperty.call(target, 'value') || 'value' in target) {{
              return maskValue(target, target.value);
            }}
            return {{ value: null, masked: false }};
          }}

          function buildLocatorCandidates(target) {{
            const tag = target?.tagName?.toLowerCase?.() || '';
            const byTestId = target?.getAttribute?.('data-testid') || target?.getAttribute?.('data-testid') || '';
            const byId = target?.id || '';
            const byName = target?.getAttribute?.('name') || '';
            const byAriaLabel = target?.getAttribute?.('aria-label') || '';
            const byPlaceholder = target?.getAttribute?.('placeholder') || '';
            const byRole = target?.getAttribute?.('role') || '';
            const labelText = associatedLabelText(target);
            const css = cssPath(target);
            const candidates = {{
              test_id: byTestId ? `[data-testid="${{escapeAttrValue(byTestId)}}"]` : '',
              id: byId ? `#${{byId}}` : '',
              name: byName ? `${{tag || '*'}}[name="${{escapeAttrValue(byName)}}"]` : '',
              aria_label: byAriaLabel ? `${{tag || '*'}}[aria-label="${{escapeAttrValue(byAriaLabel)}}"]` : '',
              placeholder: byPlaceholder ? `${{tag || '*'}}[placeholder="${{escapeAttrValue(byPlaceholder)}}"]` : '',
              css_path: css,
              label_text: labelText,
              role: byRole,
            }};
            candidates.preferred =
              candidates.test_id ||
              candidates.id ||
              candidates.name ||
              candidates.aria_label ||
              candidates.placeholder ||
              candidates.css_path ||
              '';
            return candidates;
          }}

          function describeTarget(target) {{
            const explicitLabel = associatedLabelText(target);
            const directText = text(target);
            const placeholder = shortText(target?.getAttribute?.('placeholder') || '');
            const ariaLabel = shortText(target?.getAttribute?.('aria-label') || '');
            const name = shortText(target?.getAttribute?.('name') || '');
            const id = shortText(target?.id || '');
            return explicitLabel || directText || placeholder || ariaLabel || name || id || (target?.tagName?.toLowerCase?.() || 'element');
          }}

          function targetMetadata(target) {{
            const tag = target?.tagName?.toLowerCase?.() || '';
            const role = target?.getAttribute?.('role') || '';
            const type = fieldType(target);
            const locatorCandidates = buildLocatorCandidates(target);
            const label = describeTarget(target);
            const valueInfo = readValue(target);
            return {{
              label,
              text: shortText(text(target) || label || ''),
              locator: locatorCandidates.preferred || locatorCandidates.css_path || '',
              locatorCandidates,
              target: {{
                tag,
                type,
                name: target?.getAttribute?.('name') || '',
                id: target?.id || '',
                role,
                placeholder: target?.getAttribute?.('placeholder') || '',
                disabled: Boolean(target?.disabled),
                read_only: Boolean(target?.readOnly),
                checked: Boolean(target?.checked),
                multiple: Boolean(target?.multiple),
                is_content_editable: Boolean(target?.isContentEditable),
                class_list: Array.from(target?.classList || []).slice(0, 6),
              }},
              valueInfo,
            }};
          }}

          function pageMetadata() {{
            return {{
              url: location.href,
              top_url: topUrl(),
              title: shortText(document.title || '', 160),
              referrer: document.referrer || '',
            }};
          }}

          function frameMetadata() {{
            return {{
              name: frameName(),
              url: location.href,
              is_top: window.self === window.top,
            }};
          }}

          function interactionSource(domEvent) {{
            if (!domEvent) {{
              return 'browser_context';
            }}
            return domEvent.isTrusted ? 'browser_trusted' : 'scripted_untrusted';
          }}

          function eventMetadata(domEvent, actionType, commitReason) {{
            return {{
              action_type: actionType,
              dom_event_type: domEvent?.type || '',
              input_type: domEvent?.inputType || '',
              is_trusted: Boolean(domEvent?.isTrusted),
              detail: typeof domEvent?.detail === 'number' ? domEvent.detail : null,
              commit_reason: commitReason || '',
              dom_timestamp_ms: typeof domEvent?.timeStamp === 'number' ? Math.round(domEvent.timeStamp * 1000) / 1000 : null,
            }};
          }}

          function emit(payload) {{
            const fn = window[bindingName];
            if (typeof fn === 'function') {{
              fn(payload);
            }}
          }}

          function emitStructuredEvent(actionType, target, domEvent, options = {{}}) {{
            if (!target) {{
              return;
            }}
            const meta = targetMetadata(target);
            const valueInfo = options.valueInfo || meta.valueInfo;
            emit({{
              event_type: actionType,
              timestamp: nowIso(),
              page_url: location.href,
              locator: meta.locator,
              value: Object.prototype.hasOwnProperty.call(options, 'value') ? options.value : valueInfo.value,
              label: meta.label,
              notes: Array.isArray(options.notes) ? options.notes : [],
              metadata: {{
                capture_version: 'playwright_cdp_dom_capture.v2',
                source: options.source || `dom.${{domEvent?.type || actionType}}`,
                interaction_source: interactionSource(domEvent),
                timestamp_source: 'browser_event_iso',
                browser_timestamp: {{
                  iso: nowIso(),
                  epoch_ms: Date.now(),
                }},
                page: pageMetadata(),
                frame: frameMetadata(),
                target: meta.target,
                locator_candidates: meta.locatorCandidates,
                text: meta.text,
                value_masked: Boolean(valueInfo.masked),
                event: eventMetadata(domEvent, actionType, options.commitReason || ''),
              }},
            }});
          }}

          function resolveClickTarget(rawTarget) {{
            const target = rawTarget?.closest?.(
              'button, a, [role="button"], [role="tab"], [role="menuitem"], input, select, textarea, label, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"], .el-button, .el-select, .el-checkbox, .el-radio'
            );
            if (!target) {{
              return null;
            }}
            if (target.tagName?.toLowerCase?.() === 'label' && target.control) {{
              return target.control;
            }}
            return target;
          }}

          function resolveFieldTarget(rawTarget) {{
            if (!rawTarget) {{
              return null;
            }}
            if (rawTarget.matches?.('input, select, textarea')) {{
              return rawTarget;
            }}
            if (rawTarget.isContentEditable) {{
              return rawTarget;
            }}
            return rawTarget.closest?.('input, select, textarea, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]') || null;
          }}

          function shouldSkipCommit(actionType, target, value) {{
            const key = targetKey(target);
            if (!key) {{
              return false;
            }}
            const previous = state.lastCommitted.get(key);
            const valueKey = JSON.stringify(value);
            const now = Date.now();
            if (previous && previous.actionType === actionType && previous.valueKey === valueKey && now - previous.at < 800) {{
              return true;
            }}
            state.lastCommitted.set(key, {{
              actionType,
              valueKey,
              at: now,
            }});
            return false;
          }}

          function clearPendingEntry(key) {{
            const pending = state.pendingInputs.get(key);
            if (pending?.timer) {{
              clearTimeout(pending.timer);
            }}
            state.pendingInputs.delete(key);
          }}

          function commitFieldEvent(target, domEvent, commitReason, explicitValueInfo) {{
            if (!target) {{
              return;
            }}
            const actionType = target.tagName?.toLowerCase?.() === 'select' ? 'select' : 'input';
            const valueInfo = explicitValueInfo || readValue(target);
            if (shouldSkipCommit(actionType, target, valueInfo.value)) {{
              return;
            }}
            emitStructuredEvent(actionType, target, domEvent, {{
              source: actionType === 'select' ? 'dom.change.select' : `dom.${{domEvent?.type || 'input'}}.field`,
              commitReason,
              valueInfo,
            }});
          }}

          function queueInputCommit(target, domEvent) {{
            const key = targetKey(target);
            if (!key) {{
              return;
            }}
            clearPendingEntry(key);
            const valueInfo = readValue(target);
            const timer = window.setTimeout(() => {{
              state.pendingInputs.delete(key);
              commitFieldEvent(target, domEvent, 'debounced_input', valueInfo);
            }}, 350);
            state.pendingInputs.set(key, {{
              target,
              timer,
              domEvent,
              valueInfo,
            }});
          }}

          function flushPendingInputForTarget(target, commitReason, domEvent) {{
            const key = targetKey(target);
            if (!key) {{
              return;
            }}
            const pending = state.pendingInputs.get(key);
            if (!pending) {{
              return;
            }}
            clearPendingEntry(key);
            commitFieldEvent(pending.target || target, domEvent || pending.domEvent, commitReason, pending.valueInfo);
          }}

          function flushAllPendingInputs(commitReason, domEvent) {{
            for (const [key, pending] of Array.from(state.pendingInputs.entries())) {{
              clearPendingEntry(key);
              commitFieldEvent(pending.target, domEvent || pending.domEvent, commitReason, pending.valueInfo);
            }}
          }}

          function install() {{
            if (state.installed && state.bindingName === bindingName) {{
              return;
            }}
            state.installed = true;
            state.flushPending = (reason) => flushAllPendingInputs(reason || 'manual_flush');

            const onClick = (event) => {{
              flushAllPendingInputs('pre_click_flush', event);
              const target = resolveClickTarget(domTargetFromEvent(event));
              if (!target) {{
                return;
              }}
              emitStructuredEvent('click', target, event, {{
                source: 'dom.click',
                commitReason: 'direct_click',
                value: null,
              }});
            }};

            const onInput = (event) => {{
              const target = resolveFieldTarget(domTargetFromEvent(event));
              if (!target || !isTextualField(target)) {{
                return;
              }}
              queueInputCommit(target, event);
            }};

            const onChange = (event) => {{
              const target = resolveFieldTarget(domTargetFromEvent(event));
              if (!target) {{
                return;
              }}
              if (target.tagName?.toLowerCase?.() === 'select') {{
                flushPendingInputForTarget(target, 'select_change_flush', event);
                commitFieldEvent(target, event, 'select_change');
                return;
              }}
              if (!isTextualField(target)) {{
                return;
              }}
              flushPendingInputForTarget(target, 'change_flush', event);
              commitFieldEvent(target, event, 'change');
            }};

            const onBlur = (event) => {{
              const target = resolveFieldTarget(domTargetFromEvent(event));
              if (!target || !isTextualField(target)) {{
                return;
              }}
              flushPendingInputForTarget(target, 'blur');
            }};

            const onVisibilityChange = () => {{
              if (document.visibilityState === 'hidden') {{
                flushAllPendingInputs('visibility_hidden');
              }}
            }};

            const onPageHide = () => {{
              flushAllPendingInputs('pagehide');
            }};

            document.addEventListener('click', onClick, true);
            document.addEventListener('input', onInput, true);
            document.addEventListener('change', onChange, true);
            document.addEventListener('blur', onBlur, true);
            document.addEventListener('visibilitychange', onVisibilityChange, true);
            window.addEventListener('pagehide', onPageHide, true);

            state.cleanup.push(() => document.removeEventListener('click', onClick, true));
            state.cleanup.push(() => document.removeEventListener('input', onInput, true));
            state.cleanup.push(() => document.removeEventListener('change', onChange, true));
            state.cleanup.push(() => document.removeEventListener('blur', onBlur, true));
            state.cleanup.push(() => document.removeEventListener('visibilitychange', onVisibilityChange, true));
            state.cleanup.push(() => window.removeEventListener('pagehide', onPageHide, true));
            state.cleanup.push(() => flushAllPendingInputs('cleanup'));
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
