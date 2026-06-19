from __future__ import annotations

import json
from dataclasses import dataclass

from .models import CandidateTemplateDraft, HumanRecordingEvent, RecordingEventType, RecordingSessionConfig


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
    version: str = "0.1.0-draft"

    def build_draft(
        self,
        *,
        config: RecordingSessionConfig,
        events: list[HumanRecordingEvent],
    ) -> CandidateTemplateDraft:
        start_url = config.start_url or self._first_page_url(events)
        steps: list[dict[str, object]] = []
        for event in events:
            if event.event_type in {RecordingEventType.SESSION_STARTED, RecordingEventType.SESSION_ENDED}:
                continue
            steps.append(
                {
                    "id": f"recorded_step_{event.step_index:03d}",
                    "kind": "recorded_action",
                    "action": event.event_type.value,
                    "page_url": event.page_url,
                    "locator": event.locator,
                    "label": event.label,
                    "value": event.value,
                    "notes": event.notes,
                    "metadata": event.metadata,
                }
            )

        notes = [
            "This draft is generated from a human recording session.",
            "Recorded events may contain placeholders until browser event capture is connected.",
        ]
        if config.task_description:
            notes.append(f"task: {config.task_description}")

        return CandidateTemplateDraft(
            template_name=config.template_name,
            version=self.version,
            source_session_id=config.session_id,
            page_entry={
                "name": config.template_name,
                "url": start_url,
            },
            steps=steps,
            notes=notes,
            metadata={
                "operator_id": config.operator_id,
                "event_count": len(events),
                "placeholder_event_count": sum(
                    1 for event in events if event.event_type == RecordingEventType.PLACEHOLDER
                ),
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
