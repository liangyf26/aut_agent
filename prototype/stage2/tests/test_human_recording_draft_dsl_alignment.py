from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.human_loop.drafts import MinimalCandidateTemplateDraftGenerator
from prototype.stage2.app.human_loop.models import HumanRecordingEvent, RecordingEventType, RecordingSessionConfig


def _build_config() -> RecordingSessionConfig:
    return RecordingSessionConfig(
        session_id="dsl_alignment_smoke",
        template_name="draft_alignment_demo",
        start_url="https://example.test/form",
        task_description="record a create flow",
    )


def _build_event(
    *,
    event_type: RecordingEventType,
    step_index: int,
    locator: str | None = None,
    label: str | None = None,
    value: object | None = None,
    target_name: str | None = None,
    target_type: str | None = None,
) -> HumanRecordingEvent:
    metadata = {
        "page": {
            "url": "https://example.test/form",
            "top_url": "https://example.test/form",
            "title": "Example Form",
        },
        "frame": {
            "url": "https://example.test/form",
            "is_top": True,
        },
        "interaction_source": "browser_trusted",
        "timestamp_source": "browser_event_iso",
        "source": "playwright.dom",
    }
    if target_name or target_type:
        metadata["target"] = {
            "tag": "input" if event_type != RecordingEventType.CLICK else "button",
            "type": target_type or "",
            "name": target_name or "",
            "placeholder": label or "",
        }
    if locator:
        metadata["locator_candidates"] = {
            "preferred": locator,
            "name": locator,
            "label_text": label or "",
        }
    return HumanRecordingEvent(
        event_type=event_type,
        timestamp=f"2026-06-21T10:00:0{step_index}+00:00",
        step_index=step_index,
        page_url="https://example.test/form",
        locator=locator,
        label=label,
        value=value,
        metadata=metadata,
    )


def test_page_opened_maps_to_navigate_to_url() -> None:
    generator = MinimalCandidateTemplateDraftGenerator()
    draft = generator.build_draft(
        config=_build_config(),
        events=[
            HumanRecordingEvent(
                event_type=RecordingEventType.PAGE_OPENED,
                timestamp="2026-06-21T10:00:00+00:00",
                step_index=1,
                page_url="https://example.test/form",
                label="Example Form",
                metadata={
                    "page": {
                        "url": "https://example.test/form",
                        "top_url": "https://example.test/form",
                        "title": "Example Form",
                    },
                    "frame": {
                        "url": "https://example.test/form",
                        "is_top": True,
                    },
                },
            )
        ],
    )

    step = draft.steps[0]
    assert step["action"] == "navigate_to_url"
    assert step["args"] == {"url": "https://example.test/form"}


def test_input_maps_to_fill_field_by_locator() -> None:
    generator = MinimalCandidateTemplateDraftGenerator()
    draft = generator.build_draft(
        config=_build_config(),
        events=[
            _build_event(
                event_type=RecordingEventType.INPUT,
                step_index=1,
                locator="input[name='remark']",
                label="remark",
                value="hello",
                target_name="remark",
                target_type="text",
            )
        ],
    )

    step = draft.steps[0]
    assert step["action"] == "fill_field_by_locator"
    assert step["args"] == {
        "locator": "input[name='remark']",
        "value": "hello",
    }
    assert step["field_mapping"]["candidate_data_ref"] == "candidate_form.remark"


def test_select_maps_to_select_option_by_locator() -> None:
    generator = MinimalCandidateTemplateDraftGenerator()
    draft = generator.build_draft(
        config=_build_config(),
        events=[
            _build_event(
                event_type=RecordingEventType.SELECT,
                step_index=1,
                locator="select[name='status']",
                label="status",
                value="active",
                target_name="status",
                target_type="select-one",
            )
        ],
    )

    step = draft.steps[0]
    assert step["action"] == "select_option_by_locator"
    assert step["args"] == {
        "locator": "select[name='status']",
        "value": "active",
    }
    assert step["field_mapping"]["candidate_data_ref"] == "candidate_form.status"


def test_click_maps_to_click_by_locator() -> None:
    generator = MinimalCandidateTemplateDraftGenerator()
    draft = generator.build_draft(
        config=_build_config(),
        events=[
            _build_event(
                event_type=RecordingEventType.CLICK,
                step_index=1,
                locator="button[type='submit']",
                label="submit",
            )
        ],
    )

    step = draft.steps[0]
    assert step["action"] == "click_by_locator"
    assert step["args"] == {"locator": "button[type='submit']"}


def test_candidate_review_payload_marks_unmapped_fields_for_review() -> None:
    generator = MinimalCandidateTemplateDraftGenerator()
    events = [
        _build_event(
            event_type=RecordingEventType.INPUT,
            step_index=1,
            locator="input[data-testid='temporary-note']",
            label="临时备注",
            value="hello",
            target_type="text",
        )
    ]

    review = generator.build_candidate_review(
        config=_build_config(),
        events=events,
    )

    assert review["schema_version"] == "human_recording_template_candidate_review.v1"
    assert review["mapping_summary"] == {
        "candidate_field_count": 1,
        "mapped_project_field_count": 0,
        "needs_review_count": 1,
    }
    assert review["candidate_steps"][0]["field_mapping"]["review_status"] == "needs_project_mapping"
    assert review["field_mappings"][0]["candidate_data_ref"] == "candidate_form.临时备注"
    assert any("field mappings still need project-level alias confirmation." in note for note in review["notes"])


def test_project_field_alias_maps_candidate_data_ref_to_template_field() -> None:
    generator = MinimalCandidateTemplateDraftGenerator()
    config = RecordingSessionConfig(
        session_id="dsl_alignment_alias",
        template_name="draft_alignment_demo",
        start_url="https://example.test/form",
        task_description="record a create flow",
        metadata={
            "project_field_candidates": ["cultivateDate", "remark"],
            "project_field_aliases": {
                "cultivateDate": ["栽培日期", "育苗日期"],
            },
        },
    )
    events = [
        _build_event(
            event_type=RecordingEventType.INPUT,
            step_index=1,
            locator="input[data-testid='cultivate-date']",
            label="栽培日期",
            value="2026-06-21",
            target_type="date",
        )
    ]

    draft = generator.build_draft(
        config=config,
        events=events,
    )

    step = draft.steps[0]
    assert step["field_mapping"]["field_key"] == "栽培日期"
    assert step["field_mapping"]["project_field_key"] == "cultivateDate"
    assert step["field_mapping"]["candidate_data_ref"] == "candidate_form.cultivateDate"
    assert step["field_mapping"]["mapping_source"] == "project_alias"

    review = generator.build_candidate_review(
        config=config,
        events=events,
        draft=draft,
    )

    assert review["mapping_summary"]["mapped_project_field_count"] == 1
    assert review["field_mappings"][0]["project_field_key"] == "cultivateDate"
    assert review["field_mappings"][0]["review_status"] == "mapped_to_project_field"
    assert review["field_mappings"][0]["project_mapping_candidates"][0]["project_field_key"] == "cultivateDate"
