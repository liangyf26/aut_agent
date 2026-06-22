from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .models import DiscoveryResult, FeaturePointRecord, PageEntryRecord, ScreenshotRecord

REVIEW_PATCH_FILENAME = "discovery_review_patch.json"


def load_discovery_review_patch(output_dir: str | Path) -> dict[str, Any]:
    path = Path(output_dir) / REVIEW_PATCH_FILENAME
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def apply_discovery_review_patch(
    result: DiscoveryResult,
    patch: Mapping[str, Any] | None,
) -> DiscoveryResult:
    if not patch:
        return result

    ignore_record_ids = {
        str(item).strip()
        for item in patch.get("ignore_record_ids", [])
        if str(item).strip()
    }
    rename_records = _normalize_text_mapping(patch.get("rename_records"))
    page_entry_updates = _normalize_update_mapping(patch.get("page_entry_updates"))
    feature_point_updates = _normalize_update_mapping(patch.get("feature_point_updates"))

    page_entries: list[PageEntryRecord] = []
    for item in result.page_entries:
        if item.page_entry_id in ignore_record_ids:
            continue
        updates = _collect_record_updates(
            record=item,
            rename_records=rename_records,
            explicit_updates=page_entry_updates.get(item.page_entry_id, {}),
        )
        page_entries.append(replace(item, **updates) if updates else item)

    valid_page_entry_ids = {item.page_entry_id for item in page_entries}

    feature_points: list[FeaturePointRecord] = []
    for item in result.feature_points:
        if item.feature_point_id in ignore_record_ids:
            continue
        if item.page_entry_id not in valid_page_entry_ids:
            continue
        source_page_entry_id = item.source_page_entry_id
        if source_page_entry_id and source_page_entry_id not in valid_page_entry_ids:
            source_page_entry_id = item.page_entry_id
        updates = _collect_record_updates(
            record=item,
            rename_records=rename_records,
            explicit_updates=feature_point_updates.get(item.feature_point_id, {}),
        )
        if source_page_entry_id != item.source_page_entry_id:
            updates["source_page_entry_id"] = source_page_entry_id
        feature_points.append(replace(item, **updates) if updates else item)

    valid_feature_point_ids = {item.feature_point_id for item in feature_points}

    screenshot_records: list[ScreenshotRecord] = []
    for item in result.screenshot_records:
        if item.page_entry_id not in valid_page_entry_ids:
            continue
        if item.feature_point_id and item.feature_point_id not in valid_feature_point_ids:
            continue
        screenshot_records.append(item)

    review_queue: list[dict[str, Any]] = []
    for entry in result.review_queue:
        if not isinstance(entry, dict):
            continue
        record_id = str(entry.get("record_id") or "").strip()
        record_type = str(entry.get("record_type") or "").strip()
        if not record_id or record_id in ignore_record_ids:
            continue
        if record_type == "page_entry" and record_id not in valid_page_entry_ids:
            continue
        if record_type == "feature_point" and record_id not in valid_feature_point_ids:
            continue
        fields_payload = dict(entry.get("fields") or {})
        renamed = rename_records.get(record_id)
        if renamed:
            fields_payload["name"] = renamed
        queue_item = dict(entry)
        queue_item["fields"] = fields_payload
        review_queue.append(queue_item)

    page_type_breakdown = Counter(item.page_type for item in page_entries)
    feature_scope_breakdown = Counter(item.feature_scope for item in feature_points)
    action_type_breakdown = Counter(item.action_type for item in feature_points)
    stats = dict(result.stats)
    stats.update(
        {
            "page_entry_count": len(page_entries),
            "feature_point_count": len(feature_points),
            "screenshot_record_count": len(screenshot_records),
            "review_queue_count": len(review_queue),
            "page_type_breakdown": dict(page_type_breakdown),
            "feature_scope_breakdown": dict(feature_scope_breakdown),
            "action_type_breakdown": dict(action_type_breakdown),
            "review_patch_applied": True,
            "review_patch_ignored_count": len(ignore_record_ids),
            "review_patch_renamed_count": len(rename_records),
        }
    )

    review_hints = dict(result.review_hints)
    review_hints["status"] = str(patch.get("status") or "reviewed_manual_patch")
    review_hints["review_patch_file"] = REVIEW_PATCH_FILENAME
    review_hints["applied_patch_summary"] = {
        "ignored_record_count": len(ignore_record_ids),
        "renamed_record_count": len(rename_records),
        "updated_page_entry_count": len(page_entry_updates),
        "updated_feature_point_count": len(feature_point_updates),
    }

    notes = list(result.notes)
    notes.append(
        "已应用 discovery_review_patch.json，后续 discovery / verification 将优先消费人工回填后的结果。"
    )
    patch_summary = str(patch.get("summary") or "").strip()
    if patch_summary:
        notes.append(f"review_patch_summary: {patch_summary}")

    return DiscoveryResult(
        template_name=result.template_name,
        generated_at=result.generated_at,
        strategy=result.strategy,
        page_entries=page_entries,
        feature_points=feature_points,
        screenshot_records=screenshot_records,
        review_queue=review_queue,
        review_hints=review_hints,
        stats=stats,
        notes=notes,
    )


def _normalize_text_mapping(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        normalized_key = str(key).strip()
        normalized_value = str(value).strip()
        if normalized_key and normalized_value:
            result[normalized_key] = normalized_value
    return result


def _normalize_update_mapping(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        normalized_key = str(key).strip()
        if not normalized_key or not isinstance(value, Mapping):
            continue
        result[normalized_key] = dict(value)
    return result


def _collect_record_updates(
    *,
    record: PageEntryRecord | FeaturePointRecord,
    rename_records: Mapping[str, str],
    explicit_updates: Mapping[str, Any],
) -> dict[str, Any]:
    allowed_fields = set(record.__dataclass_fields__.keys())
    updates: dict[str, Any] = {}
    record_id = getattr(record, "feature_point_id", None) or getattr(record, "page_entry_id", None) or ""
    renamed = rename_records.get(record_id)
    if renamed and "name" in allowed_fields:
        updates["name"] = renamed
    for key, value in explicit_updates.items():
        if key not in allowed_fields:
            continue
        updates[key] = value
    return updates
