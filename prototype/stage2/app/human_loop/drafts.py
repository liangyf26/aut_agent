from __future__ import annotations

import json
from dataclasses import dataclass, field
import re
from typing import Any

from .models import (
    build_recording_summary,
    CandidateTemplateDraft,
    HumanRecordingEvent,
    RecordingEventType,
    RecordingSessionConfig,
)


class CandidateTemplateDraftGenerator:
    def build_draft(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
    ) -> CandidateTemplateDraft:
        raise NotImplementedError


@dataclass
class MinimalCandidateTemplateDraftGenerator(CandidateTemplateDraftGenerator):
    version: str = "0.2.0-draft"

    def build_draft(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
    ) -> CandidateTemplateDraft:
        start_url = config.start_url or self._first_page_url(events)
        summary = build_recording_summary(events)
        filtered_events = self._filter_primary_events(events)
        field_catalog = self._build_field_catalog(filtered_events)
        feature_point = self._build_feature_point(config, filtered_events, field_catalog)
        candidate_data_schema = self._build_candidate_data_schema(field_catalog)

        page_entry = {
            "name": config.template_name,
            "url": start_url,
            "source": "human_recording",
        }
        if summary.get("page_urls"):
            page_entry["observed_urls"] = summary["page_urls"]

        steps = self._build_candidate_steps(filtered_events, field_catalog)
        notes = self._build_notes(config, summary, filtered_events)

        return CandidateTemplateDraft(
            template_name=config.template_name,
            version=self.version,
            source_session_id=config.session_id,
            page_entry=page_entry,
            feature_point=feature_point,
            execution_path=self._guess_execution_path(config, feature_point),
            steps=steps,
            notes=notes,
            metadata={
                "operator_id": config.operator_id,
                "event_count": len(events),
                "recorded_action_count": len(filtered_events),
                "placeholder_event_count": sum(
                    1 for event in events if event.event_type == RecordingEventType.PLACEHOLDER
                ),
                "capture_summary": summary,
                "page_urls": summary.get("page_urls", []),
                "frame_urls": summary.get("frame_urls", []),
                "key_screenshot_count": summary.get("key_screenshot_count", 0),
                "timestamp_source_counts": summary.get("timestamp_source_counts", {}),
                "interaction_source_counts": summary.get("interaction_source_counts", {}),
                "element_kind_counts": summary.get("element_kind_counts", {}),
                "quality": summary.get("quality", {}),
                "candidate_locator_count": sum(len(item.locator_candidates) for item in field_catalog.values()),
                "candidate_field_mapping_count": len(field_catalog),
                "field_catalog": self._serialize_field_catalog(field_catalog),
                "candidate_data_schema": candidate_data_schema,
            },
        )

    def write_draft(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        output_path: str,
    ) -> None:
        draft = self.build_draft(config=config, events=events)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(draft.to_dict(), fh, ensure_ascii=False, indent=2)

    def _first_page_url(self, events: list[HumanRecordingEvent]) -> str | None:
        for event in events:
            if event.page_url:
                return event.page_url
        return None

    def _filter_primary_events(self, events: list[HumanRecordingEvent]) -> list[HumanRecordingEvent]:
        primary_events: list[HumanRecordingEvent] = []
        for event in events:
            if event.event_type in {
                RecordingEventType.SESSION_STARTED,
                RecordingEventType.SESSION_ENDED,
                RecordingEventType.SCREENSHOT,
                RecordingEventType.NOTE,
            }:
                continue
            if event.event_type in {RecordingEventType.CLICK, RecordingEventType.INPUT, RecordingEventType.SELECT}:
                if _looks_like_noise(event):
                    continue
            primary_events.append(event)
        return primary_events

    def _build_feature_point(
        self,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        field_catalog: dict[str, "_FieldMappingCandidate"],
    ) -> dict[str, Any]:
        name = None
        if isinstance(config.metadata, dict):
            name = _normalize_text(config.metadata.get("feature_point_name"))
        if not name:
            name = _first_nonempty(
                _normalize_text(config.task_description),
                _normalize_text(config.template_name),
            ) or "待人工命名功能点"

        feature_type = self._infer_feature_type(events)
        confidence = "medium"
        if any(event.event_type == RecordingEventType.INPUT for event in events) and field_catalog:
            confidence = "high"
        elif events:
            confidence = "low"

        return {
            "name": name,
            "type": feature_type,
            "source": "human_recording",
            "confidence": confidence,
        }

    def _infer_feature_type(self, events: list[HumanRecordingEvent]) -> str:
        fragments = [_normalize_text(event.label) for event in events] + [
            _normalize_text(event.locator) for event in events
        ]
        text_blob = " ".join(fragment for fragment in fragments if fragment).lower()
        if any(token in text_blob for token in ("新增", "申请", "提交", "备案", "create", "add", "apply")):
            return "新增"
        if any(token in text_blob for token in ("查询", "搜索", "筛选", "filter", "search", "query")):
            return "查询"
        if any(token in text_blob for token in ("编辑", "修改", "update", "edit")):
            return "编辑"
        return "待确认"

    def _guess_execution_path(self, config: RecordingSessionConfig, feature_point: dict[str, Any]) -> str:
        if isinstance(config.metadata, dict):
            explicit = _normalize_key(config.metadata.get("execution_path"))
            if explicit:
                return explicit
        name = _normalize_key(feature_point.get("name"))
        if name:
            return name
        return _normalize_key(config.template_name) or "recorded_path"

    def _build_candidate_steps(
        self,
        events: list[HumanRecordingEvent],
        field_catalog: dict[str, "_FieldMappingCandidate"],
    ) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for event in events:
            step: dict[str, Any] = {
                "id": f"recorded_step_{event.step_index:03d}",
                "kind": self._step_kind(event),
                "action": self._step_action(event),
                "timestamp": event.timestamp,
                "page_url": event.page_url,
                "label": event.label,
                "locator": event.locator,
                "notes": list(event.notes),
                "candidate_locators": self._rank_locator_candidates(event),
                "draft_hints": self._build_step_hints(event),
            }
            if event.value not in (None, ""):
                step["value_preview"] = event.value

            field_key = self._resolve_field_key(event, field_catalog)
            if field_key:
                step["args"] = {
                    "field_key": field_key,
                    "data_ref": f"candidate_form.{field_key}",
                }
                step["field_mapping"] = field_catalog[field_key].to_step_mapping()

            if event.event_type == RecordingEventType.PAGE_OPENED:
                step["kind"] = "navigation"
                step["action"] = "open_page"
                step["args"] = {"url": event.page_url}
            elif event.event_type == RecordingEventType.PLACEHOLDER:
                step["kind"] = "placeholder"
                step["action"] = "manual_review_required"

            steps.append(step)
        return steps

    def _step_kind(self, event: HumanRecordingEvent) -> str:
        if event.event_type == RecordingEventType.PAGE_OPENED:
            return "navigation"
        if event.event_type == RecordingEventType.INPUT:
            return "field_input"
        if event.event_type == RecordingEventType.SELECT:
            return "field_select"
        if event.event_type == RecordingEventType.CLICK:
            return "action"
        if event.event_type == RecordingEventType.PLACEHOLDER:
            return "placeholder"
        return "recorded_action"

    def _step_action(self, event: HumanRecordingEvent) -> str:
        mapping = {
            RecordingEventType.PAGE_OPENED: "open_page",
            RecordingEventType.INPUT: "fill_field",
            RecordingEventType.SELECT: "select_option",
            RecordingEventType.CLICK: "click_element",
            RecordingEventType.PLACEHOLDER: "manual_review_required",
        }
        return mapping.get(event.event_type, event.event_type.value)

    def _build_field_catalog(self, events: list[HumanRecordingEvent]) -> dict[str, "_FieldMappingCandidate"]:
        field_catalog: dict[str, _FieldMappingCandidate] = {}
        for event in events:
            if event.event_type not in {RecordingEventType.INPUT, RecordingEventType.SELECT}:
                continue

            field_key = _infer_field_key(event)
            if not field_key:
                continue

            candidate = field_catalog.get(field_key)
            if candidate is None:
                candidate = _FieldMappingCandidate(
                    field_key=field_key,
                    label=event.label,
                    source_event_type=event.event_type.value,
                    sample_value=event.value,
                    value_schema=_infer_value_schema(field_key, event),
                )
                field_catalog[field_key] = candidate

            candidate.observe(event)
        return field_catalog

    def _build_candidate_data_schema(self, field_catalog: dict[str, "_FieldMappingCandidate"]) -> dict[str, Any]:
        field_rules: dict[str, Any] = {}
        field_constraints: dict[str, Any] = {}
        field_samples: dict[str, Any] = {}
        for field_key, candidate in field_catalog.items():
            field_rules[field_key] = candidate.value_schema.get("rule", {"strategy": "constant"})
            constraints = candidate.value_schema.get("constraints", {})
            if constraints:
                field_constraints[field_key] = constraints
            if candidate.sample_value not in (None, ""):
                field_samples[field_key] = candidate.sample_value

        return {
            "schema_version": "human_recording_candidate_schema.v1",
            "strategy": "baseline_plus_safe_variation",
            "field_rules": field_rules,
            "field_constraints": field_constraints,
            "field_samples": field_samples,
        }

    def _build_notes(
        self,
        config: RecordingSessionConfig,
        summary: dict[str, Any],
        events: list[HumanRecordingEvent],
    ) -> list[str]:
        notes = [
            "This draft is generated from a human recording session.",
            "The draft tries to collapse noisy DOM events into candidate executable steps, but it still requires project-level review before promotion.",
        ]
        if config.task_description:
            notes.append(f"task: {config.task_description}")
        notes.extend(summary.get("warnings", []))
        if not any(event.event_type in {RecordingEventType.INPUT, RecordingEventType.SELECT} for event in events):
            notes.append("No input/select events were captured, so candidate field mapping is currently weak.")
        return notes

    def _build_step_hints(self, event: HumanRecordingEvent) -> dict[str, Any]:
        metadata = event.metadata or {}
        target = metadata.get("target")
        locator_candidates = metadata.get("locator_candidates")
        page = metadata.get("page")
        frame = metadata.get("frame")
        hints: dict[str, Any] = {
            "interaction_source": metadata.get("interaction_source"),
            "source": metadata.get("source"),
            "timestamp_source": metadata.get("timestamp_source"),
            "element_kind": metadata.get("element_kind"),
            "element_type": metadata.get("element_type"),
            "element_text": metadata.get("element_text") or metadata.get("text"),
            "source_page_url": metadata.get("source_page_url"),
            "source_page_title": metadata.get("source_page_title"),
            "interaction_sequence_id": metadata.get("interaction_sequence_id"),
            "value_masked": metadata.get("value_masked"),
            "screenshot_path": event.screenshot_path or metadata.get("latest_action_screenshot"),
        }
        if isinstance(target, dict):
            hints["target"] = {
                "tag": target.get("tag"),
                "type": target.get("type"),
                "name": target.get("name"),
                "role": target.get("role"),
                "placeholder": target.get("placeholder"),
                "disabled": target.get("disabled"),
                "read_only": target.get("read_only"),
            }
        if isinstance(locator_candidates, dict):
            hints["locator_candidates"] = locator_candidates
        if isinstance(page, dict):
            hints["page"] = {
                "url": page.get("url"),
                "top_url": page.get("top_url"),
                "title": page.get("title"),
            }
        if isinstance(frame, dict):
            hints["frame"] = {
                "name": frame.get("name"),
                "url": frame.get("url"),
                "is_top": frame.get("is_top"),
            }
        return hints

    def _rank_locator_candidates(self, event: HumanRecordingEvent) -> list[dict[str, Any]]:
        return _rank_locator_candidates_from_payload(
            payload=event.metadata.get("locator_candidates") if isinstance(event.metadata, dict) else None,
            event_locator=event.locator,
        )

    def _resolve_field_key(
        self,
        event: HumanRecordingEvent,
        field_catalog: dict[str, "_FieldMappingCandidate"],
    ) -> str | None:
        field_key = _infer_field_key(event)
        if field_key and field_key in field_catalog:
            return field_key
        for key, candidate in field_catalog.items():
            if event.locator and event.locator in candidate.locator_values:
                return key
        return None

    def _serialize_field_catalog(
        self,
        field_catalog: dict[str, "_FieldMappingCandidate"],
    ) -> dict[str, Any]:
        return {field_key: candidate.to_dict() for field_key, candidate in field_catalog.items()}


@dataclass
class _FieldMappingCandidate:
    field_key: str
    label: str | None
    source_event_type: str
    sample_value: Any
    value_schema: dict[str, Any]
    locator_candidates: list[dict[str, Any]] = field(default_factory=list)
    observed_labels: list[str] = field(default_factory=list)
    observed_values: list[Any] = field(default_factory=list)
    observed_event_types: list[str] = field(default_factory=list)
    locator_values: set[str] = field(default_factory=set)

    def observe(self, event: HumanRecordingEvent) -> None:
        if event.label:
            self._push_unique(self.observed_labels, event.label)
            if not self.label:
                self.label = event.label
        if event.value not in (None, ""):
            self._push_unique(self.observed_values, event.value)
            if self.sample_value in (None, ""):
                self.sample_value = event.value
        self._push_unique(self.observed_event_types, event.event_type.value)
        for item in _rank_locator_candidates_from_payload(
            payload=event.metadata.get("locator_candidates") if isinstance(event.metadata, dict) else None,
            event_locator=event.locator,
        ):
            candidate_key = f"{item['strategy']}::{item['value']}"
            if candidate_key in self.locator_values:
                continue
            self.locator_values.add(candidate_key)
            self.locator_candidates.append(item)
        if event.locator:
            self.locator_values.add(event.locator)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_key": self.field_key,
            "label": self.label,
            "source_event_type": self.source_event_type,
            "sample_value": self.sample_value,
            "observed_labels": self.observed_labels,
            "observed_values": self.observed_values,
            "observed_event_types": self.observed_event_types,
            "locator_candidates": self.locator_candidates,
            "value_schema": self.value_schema,
        }

    def to_step_mapping(self) -> dict[str, Any]:
        return {
            "field_key": self.field_key,
            "label": self.label,
            "source_event_type": self.source_event_type,
            "sample_value": self.sample_value,
            "locator_candidates": self.locator_candidates[:5],
            "value_schema_hint": self.value_schema,
        }

    def _push_unique(self, values: list[Any], value: Any) -> None:
        if value not in values:
            values.append(value)


def _looks_like_noise(event: HumanRecordingEvent) -> bool:
    metadata = event.metadata or {}
    locator = _normalize_text(event.locator)
    label = _normalize_text(event.label)
    source = _normalize_text(metadata.get("source")) or ""
    if "binding_probe" in source:
        return True
    if locator and any(token in locator.lower() for token in ("debug.locator", "__stage2", "manual_binding_probe")):
        return True
    if label and "manual_binding_probe" in label.lower():
        return True
    return False


def _infer_field_key(event: HumanRecordingEvent) -> str | None:
    metadata = event.metadata or {}
    target = metadata.get("target")
    if isinstance(target, dict):
        for candidate in (target.get("name"), target.get("id"), target.get("placeholder")):
            key = _normalize_key(candidate)
            if key:
                return key
    locator_candidates = metadata.get("locator_candidates")
    if isinstance(locator_candidates, dict):
        key = _normalize_key(locator_candidates.get("label_text"))
        if key:
            return key
    for candidate in (event.label, metadata.get("text"), event.locator):
        key = _normalize_key(candidate)
        if key:
            return key
    return None


def _infer_value_schema(field_key: str, event: HumanRecordingEvent) -> dict[str, Any]:
    value = event.value
    target = event.metadata.get("target") if isinstance(event.metadata, dict) else None
    target_type = ""
    if isinstance(target, dict):
        target_type = _normalize_text(target.get("type")) or ""

    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
            return {
                "kind": "date",
                "rule": {"strategy": "iso_date_offset_days", "offset_days": 0, "jitter_days": 2},
                "constraints": {"type": "iso_date", "date_format": "%Y-%m-%d"},
            }
        if re.fullmatch(r"\d{8,}", stripped):
            return {
                "kind": "identifier",
                "rule": {"strategy": "numeric_string_offset", "offset": 1, "jitter": 3},
                "constraints": {"type": "str", "regex": r".*\\d.*", "non_empty": True},
            }
        if any(token in field_key for token in ("remark", "note", "desc", "memo")):
            return {
                "kind": "remark",
                "rule": {
                    "strategy": "remark_with_run_suffix",
                    "variants": ["自动生成", "录制映射", "回归样本"],
                    "include_run_suffix": True,
                },
                "constraints": {"type": "str", "max_length": 200, "non_empty": True},
            }
        if any(token in field_key for token in ("code", "num", "serial", "batch", "order", "no")):
            return {
                "kind": "number_like_text",
                "rule": {"strategy": "unique_text", "separator": "-", "token": "{run_suffix}"},
                "constraints": {"type": "str", "non_empty": True},
            }
        if target_type == "date":
            return {
                "kind": "date",
                "rule": {"strategy": "iso_date_today"},
                "constraints": {"type": "iso_date", "date_format": "%Y-%m-%d"},
            }
        return {
            "kind": "text",
            "rule": {"strategy": "unique_text", "separator": "-", "token": "{run_suffix}"},
            "constraints": {"type": "str", "non_empty": True},
        }

    if isinstance(value, int) and not isinstance(value, bool):
        return {
            "kind": "integer",
            "rule": {"strategy": "int_offset", "offset": 0, "jitter": 2},
            "constraints": {"type": "int"},
        }

    return {
        "kind": "constant",
        "rule": {"strategy": "constant"},
        "constraints": {},
    }


def _rank_locator_candidates_from_payload(
    *,
    payload: Any,
    event_locator: str | None,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for name, score in (
            ("preferred", 100),
            ("test_id", 95),
            ("id", 92),
            ("name", 88),
            ("aria_label", 82),
            ("placeholder", 75),
            ("label_text", 68),
            ("css_path", 60),
            ("role", 52),
        ):
            value = payload.get(name)
            if value in (None, ""):
                continue
            ranked.append(
                {
                    "strategy": name,
                    "value": value,
                    "score": score,
                }
            )
    if not ranked and event_locator:
        ranked.append({"strategy": "event_locator", "value": event_locator, "score": 50})
    return ranked


def _normalize_key(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    text = text.lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return None
    if re.search(r"[\u4e00-\u9fff]", text):
        return _compact_chinese_key(text)
    return text


def _compact_chinese_key(text: str) -> str:
    tokens = [token for token in re.split(r"_+", text) if token]
    normalized: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            normalized.append(token[:6])
        else:
            normalized.append(token)
    return "_".join(normalized)


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _first_nonempty(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None
