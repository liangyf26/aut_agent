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

    def build_candidate_review(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        draft: CandidateTemplateDraft | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def write_draft(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        output_path: str,
    ) -> CandidateTemplateDraft:
        draft = self.build_draft(config=config, events=events)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(draft.to_dict(), fh, ensure_ascii=False, indent=2)
        return draft

    def write_candidate_review(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        output_path: str,
        draft: CandidateTemplateDraft | None = None,
    ) -> dict[str, Any]:
        review_payload = self.build_candidate_review(config=config, events=events, draft=draft)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(review_payload, fh, ensure_ascii=False, indent=2)
        return review_payload

    def write_artifacts(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        draft_output_path: str,
        candidate_review_output_path: str,
    ) -> CandidateTemplateDraft:
        draft = self.write_draft(
            config=config,
            events=events,
            output_path=draft_output_path,
        )
        self.write_candidate_review(
            config=config,
            events=events,
            output_path=candidate_review_output_path,
            draft=draft,
        )
        return draft


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
        field_catalog = self._build_field_catalog(config=config, events=filtered_events)
        feature_point = self._build_feature_point(config, filtered_events, field_catalog)
        candidate_data_schema = self._build_candidate_data_schema(field_catalog)
        project_field_candidates = self._project_field_candidates(config)
        project_field_aliases = self._project_field_aliases(config)

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
                "mapped_project_field_count": sum(1 for item in field_catalog.values() if item.project_field_key),
                "field_catalog": self._serialize_field_catalog(field_catalog),
                "candidate_data_schema": candidate_data_schema,
                "project_field_candidates": project_field_candidates,
                "project_field_aliases": project_field_aliases,
            },
        )

    def build_candidate_review(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
        draft: CandidateTemplateDraft | None = None,
    ) -> dict[str, Any]:
        candidate_draft = draft or self.build_draft(config=config, events=events)
        capture_summary = candidate_draft.metadata.get("capture_summary", {})
        field_catalog_payload = candidate_draft.metadata.get("field_catalog", {})
        project_field_candidates = candidate_draft.metadata.get("project_field_candidates", [])
        project_field_aliases = candidate_draft.metadata.get("project_field_aliases", {})

        field_mappings: list[dict[str, Any]] = []
        mapped_project_field_count = 0
        for field_key, payload in field_catalog_payload.items():
            if not isinstance(payload, dict):
                continue
            review_item = {
                "draft_field_key": field_key,
                "candidate_data_ref": payload.get("candidate_data_ref") or f"candidate_form.{field_key}",
                "project_field_key": payload.get("project_field_key"),
                "mapping_source": payload.get("mapping_source"),
                "review_status": payload.get("review_status") or "needs_project_mapping",
                "project_mapping_candidates": list(payload.get("project_mapping_candidates", []))[:5],
                "matched_aliases": list(payload.get("matched_aliases", [])),
                "observed_aliases": list(payload.get("observed_aliases", [])),
                "label": payload.get("label"),
                "sample_value": payload.get("sample_value"),
                "observed_labels": list(payload.get("observed_labels", [])),
                "observed_values": list(payload.get("observed_values", [])),
                "observed_event_types": list(payload.get("observed_event_types", [])),
                "locator_candidates": list(payload.get("locator_candidates", []))[:5],
                "value_schema_hint": payload.get("value_schema"),
            }
            if review_item["project_field_key"]:
                mapped_project_field_count += 1
            field_mappings.append(review_item)

        candidate_steps: list[dict[str, Any]] = []
        for step in candidate_draft.steps:
            if not isinstance(step, dict):
                continue
            condensed_step = {
                "id": step.get("id"),
                "kind": step.get("kind"),
                "action": step.get("action"),
                "label": step.get("label"),
                "page_url": step.get("page_url"),
                "locator": step.get("locator"),
                "args": step.get("args"),
                "candidate_locators": list(step.get("candidate_locators", []))[:5],
                "value_preview": step.get("value_preview"),
            }
            field_mapping = step.get("field_mapping")
            if isinstance(field_mapping, dict):
                condensed_step["field_mapping"] = {
                    "field_key": field_mapping.get("field_key"),
                    "candidate_data_ref": field_mapping.get("candidate_data_ref"),
                    "project_field_key": field_mapping.get("project_field_key"),
                    "mapping_source": field_mapping.get("mapping_source"),
                    "review_status": field_mapping.get("review_status"),
                    "project_mapping_candidates": list(field_mapping.get("project_mapping_candidates", []))[:3],
                }
            candidate_steps.append(condensed_step)

        needs_review_count = max(0, len(field_mappings) - mapped_project_field_count)
        notes = list(candidate_draft.notes)
        if needs_review_count:
            notes.append(f"{needs_review_count} field mappings still need project-level alias confirmation.")

        return {
            "schema_version": "human_recording_template_candidate_review.v1",
            "template_name": candidate_draft.template_name,
            "source_session_id": candidate_draft.source_session_id,
            "page_entry": candidate_draft.page_entry,
            "feature_point": candidate_draft.feature_point,
            "execution_path": candidate_draft.execution_path,
            "capture_summary": capture_summary,
            "project_field_context": {
                "candidate_count": len(project_field_candidates),
                "candidate_keys": list(project_field_candidates),
                "explicit_aliases": project_field_aliases,
            },
            "mapping_summary": {
                "candidate_field_count": len(field_mappings),
                "mapped_project_field_count": mapped_project_field_count,
                "needs_review_count": needs_review_count,
            },
            "field_mappings": field_mappings,
            "candidate_steps": candidate_steps,
            "notes": notes,
        }

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
            field_key = self._resolve_field_key(event, field_catalog)
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

            args = self._build_executable_args(event)
            if args:
                step["args"] = args

            if field_key:
                step["field_mapping"] = field_catalog[field_key].to_step_mapping()

            if event.event_type == RecordingEventType.PAGE_OPENED:
                step["kind"] = "navigation"
                step["action"] = "navigate_to_url"
            elif event.event_type == RecordingEventType.PLACEHOLDER:
                step["kind"] = "placeholder"
                step["action"] = "manual_review_required"
                step.pop("args", None)

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
            RecordingEventType.PAGE_OPENED: "navigate_to_url",
            RecordingEventType.INPUT: "fill_field_by_locator",
            RecordingEventType.SELECT: "select_option_by_locator",
            RecordingEventType.CLICK: "click_by_locator",
            RecordingEventType.PLACEHOLDER: "manual_review_required",
        }
        return mapping.get(event.event_type, event.event_type.value)

    def _build_field_catalog(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
    ) -> dict[str, "_FieldMappingCandidate"]:
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
        self._apply_project_field_mappings(config, field_catalog)
        return field_catalog

    def _apply_project_field_mappings(
        self,
        config: RecordingSessionConfig,
        field_catalog: dict[str, "_FieldMappingCandidate"],
    ) -> None:
        project_field_candidates = self._project_field_candidates(config)
        if not project_field_candidates:
            return
        project_field_aliases = self._project_field_aliases(config)
        alias_index, explicit_lookup_keys = _build_project_field_alias_index(
            project_field_candidates,
            project_field_aliases,
        )
        for candidate in field_catalog.values():
            candidate.project_mapping_candidates = _rank_project_field_mapping_candidates(
                candidate,
                project_field_candidates,
                project_field_aliases,
            )
            exact_top_candidates = [
                item for item in candidate.project_mapping_candidates if int(item.get("score", 0)) >= 95
            ]
            if len(exact_top_candidates) != 1:
                continue
            winner = exact_top_candidates[0]
            winner_lookup_key = _mapping_lookup_key(winner.get("matched_alias"))
            if not winner_lookup_key:
                continue
            canonical_matches = alias_index.get(winner_lookup_key, set())
            if len(canonical_matches) != 1:
                continue
            candidate.project_field_key = winner["project_field_key"]
            candidate.matched_aliases = list(winner.get("observed_aliases", []))
            if winner_lookup_key in explicit_lookup_keys:
                candidate.mapping_source = "project_alias"
            else:
                candidate.mapping_source = "project_field_key"

    def _project_field_candidates(self, config: RecordingSessionConfig) -> list[str]:
        metadata = config.metadata if isinstance(config.metadata, dict) else {}
        raw_values = metadata.get("project_field_candidates")
        if not isinstance(raw_values, list):
            return []
        seen: set[str] = set()
        candidates: list[str] = []
        for value in raw_values:
            text = _normalize_text(value)
            if not text or text in seen:
                continue
            seen.add(text)
            candidates.append(text)
        return candidates

    def _project_field_aliases(self, config: RecordingSessionConfig) -> dict[str, list[str]]:
        metadata = config.metadata if isinstance(config.metadata, dict) else {}
        payload = metadata.get("project_field_aliases") or metadata.get("field_aliases")
        if not isinstance(payload, dict):
            return {}
        aliases: dict[str, list[str]] = {}
        for canonical_key, values in payload.items():
            canonical_text = _normalize_text(canonical_key)
            if not canonical_text:
                continue
            raw_values = values if isinstance(values, list) else [values]
            normalized_values: list[str] = []
            seen: set[str] = set()
            for value in raw_values:
                text = _normalize_text(value)
                if not text or text in seen:
                    continue
                seen.add(text)
                normalized_values.append(text)
            if normalized_values:
                aliases[canonical_text] = normalized_values
        return aliases

    def _build_candidate_data_schema(self, field_catalog: dict[str, "_FieldMappingCandidate"]) -> dict[str, Any]:
        field_rules: dict[str, Any] = {}
        field_constraints: dict[str, Any] = {}
        field_samples: dict[str, Any] = {}
        for field_key, candidate in field_catalog.items():
            data_key = candidate.project_field_key or field_key
            field_rules[data_key] = candidate.value_schema.get("rule", {"strategy": "constant"})
            constraints = candidate.value_schema.get("constraints", {})
            if constraints:
                field_constraints[data_key] = constraints
            if candidate.sample_value not in (None, ""):
                field_samples[data_key] = candidate.sample_value

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

    def _build_executable_args(self, event: HumanRecordingEvent) -> dict[str, Any] | None:
        if event.event_type == RecordingEventType.PAGE_OPENED:
            if not event.page_url:
                return None
            return {"url": event.page_url}

        if event.event_type == RecordingEventType.CLICK:
            locator = self._best_locator(event)
            if not locator:
                return None
            return {"locator": locator}

        if event.event_type in {RecordingEventType.INPUT, RecordingEventType.SELECT}:
            locator = self._best_locator(event)
            if not locator:
                return None
            args: dict[str, Any] = {"locator": locator}
            if not bool((event.metadata or {}).get("value_masked")) and event.value is not None:
                args["value"] = event.value
            return args

        return None

    def _best_locator(self, event: HumanRecordingEvent) -> str | None:
        locator = _normalize_text(event.locator)
        if locator:
            return locator
        ranked = self._rank_locator_candidates(event)
        if not ranked:
            return None
        value = ranked[0].get("value")
        return _normalize_text(value)


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
    observed_aliases: list[str] = field(default_factory=list)
    locator_values: set[str] = field(default_factory=set)
    project_field_key: str | None = None
    mapping_source: str = "recording_inferred"
    matched_aliases: list[str] = field(default_factory=list)
    project_mapping_candidates: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, event: HumanRecordingEvent) -> None:
        if event.label:
            self._push_unique(self.observed_labels, event.label)
            if not self.label:
                self.label = event.label
        for alias in _collect_field_alias_candidates(event):
            self._push_unique(self.observed_aliases, alias)
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
            "observed_aliases": self.observed_aliases,
            "locator_candidates": self.locator_candidates,
            "value_schema": self.value_schema,
            "project_field_key": self.project_field_key,
            "mapping_source": self.mapping_source,
            "matched_aliases": self.matched_aliases,
            "project_mapping_candidates": self.project_mapping_candidates,
            "review_status": self.review_status,
            "candidate_data_ref": self.candidate_data_ref,
        }

    def to_step_mapping(self) -> dict[str, Any]:
        return {
            "field_key": self.field_key,
            "label": self.label,
            "source_event_type": self.source_event_type,
            "sample_value": self.sample_value,
            "candidate_data_ref": self.candidate_data_ref,
            "project_field_key": self.project_field_key,
            "mapping_source": self.mapping_source,
            "matched_aliases": self.matched_aliases,
            "observed_aliases": self.observed_aliases,
            "project_mapping_candidates": self.project_mapping_candidates[:3],
            "review_status": self.review_status,
            "locator_candidates": self.locator_candidates[:5],
            "value_schema_hint": self.value_schema,
        }

    @property
    def candidate_data_ref(self) -> str:
        return f"candidate_form.{self.project_field_key or self.field_key}"

    @property
    def review_status(self) -> str:
        return "mapped_to_project_field" if self.project_field_key else "needs_project_mapping"

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


def _collect_field_alias_candidates(event: HumanRecordingEvent) -> list[str]:
    metadata = event.metadata or {}
    target = metadata.get("target")
    locator_candidates = metadata.get("locator_candidates")
    candidates: list[str] = []
    seen: set[str] = set()

    def push(value: Any) -> None:
        text = _normalize_text(value)
        if not text or text in seen:
            return
        seen.add(text)
        candidates.append(text)

    if isinstance(target, dict):
        for value in (target.get("name"), target.get("id"), target.get("placeholder"), target.get("role")):
            push(value)
    if isinstance(locator_candidates, dict):
        for value in (
            locator_candidates.get("label_text"),
            locator_candidates.get("name"),
            locator_candidates.get("aria_label"),
            locator_candidates.get("placeholder"),
        ):
            push(value)
    for value in (event.label, metadata.get("text")):
        push(value)
    field_key = _infer_field_key(event)
    if field_key:
        push(field_key)
    return candidates


def _build_project_field_alias_index(
    project_field_candidates: list[str],
    project_field_aliases: dict[str, list[str]],
) -> tuple[dict[str, set[str]], set[str]]:
    alias_index: dict[str, set[str]] = {}
    explicit_lookup_keys: set[str] = set()
    for canonical_key in project_field_candidates:
        for alias in _derived_project_field_aliases(canonical_key):
            lookup_key = _mapping_lookup_key(alias)
            if not lookup_key:
                continue
            alias_index.setdefault(lookup_key, set()).add(canonical_key)
        for alias in project_field_aliases.get(canonical_key, []):
            lookup_key = _mapping_lookup_key(alias)
            if not lookup_key:
                continue
            alias_index.setdefault(lookup_key, set()).add(canonical_key)
            explicit_lookup_keys.add(lookup_key)
    return alias_index, explicit_lookup_keys


def _derived_project_field_aliases(canonical_key: str) -> list[str]:
    values = [
        canonical_key,
        re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", canonical_key),
        re.sub(r"_+", " ", canonical_key),
    ]
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        text = _normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        aliases.append(text)
    return aliases


def _rank_project_field_mapping_candidates(
    candidate: _FieldMappingCandidate,
    project_field_candidates: list[str],
    project_field_aliases: dict[str, list[str]],
) -> list[dict[str, Any]]:
    observed_aliases = [candidate.field_key, *candidate.observed_aliases]
    rankings: list[dict[str, Any]] = []
    for project_field_key in project_field_candidates:
        best_score = 0
        matched_alias = ""
        matched_observed: list[str] = []
        explicit_aliases = project_field_aliases.get(project_field_key, [])
        for observed_alias in observed_aliases:
            observed_lookup = _mapping_lookup_key(observed_alias)
            if not observed_lookup:
                continue
            for project_alias in [project_field_key, *_derived_project_field_aliases(project_field_key), *explicit_aliases]:
                project_lookup = _mapping_lookup_key(project_alias)
                if not project_lookup:
                    continue
                score = 0
                if observed_lookup == project_lookup:
                    score = 100 if project_alias in explicit_aliases else 95
                elif len(observed_lookup) >= 4 and len(project_lookup) >= 4:
                    if observed_lookup in project_lookup or project_lookup in observed_lookup:
                        score = 70 if project_alias in explicit_aliases else 60
                if score < best_score:
                    continue
                if score > best_score:
                    matched_observed = []
                best_score = score
                matched_alias = project_alias
                if observed_alias not in matched_observed:
                    matched_observed.append(observed_alias)
        if best_score:
            rankings.append(
                {
                    "project_field_key": project_field_key,
                    "score": best_score,
                    "matched_alias": matched_alias,
                    "observed_aliases": matched_observed,
                }
            )
    rankings.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("project_field_key", ""))))
    return rankings


def _mapping_lookup_key(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = text.lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text or None


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
