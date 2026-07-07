from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prototype.stage2.app.discovery import DiscoveryArtifactWriter
from prototype.stage2.app.runtime.templates import TemplateBundle, load_template_bundle


@dataclass(frozen=True)
class TemplateRevisionChecklistResult:
    template_name: str
    output_dir: Path
    checklist_path: Path
    markdown_path: Path
    payload: dict[str, Any]


def build_template_revision_checklist(
    template_dir: Path,
    *,
    discovery_dir: Path | None = None,
    candidate_review_path: Path | None = None,
    output_dir: Path | None = None,
) -> TemplateRevisionChecklistResult:
    bundle = load_template_bundle(template_dir)
    discovery_payload = _load_discovery_payload(discovery_dir)
    candidate_review = _load_json(candidate_review_path) if candidate_review_path else {}
    payload = _build_checklist_payload(
        bundle=bundle,
        discovery_payload=discovery_payload,
        candidate_review=candidate_review,
    )

    target_dir = output_dir or (template_dir / "_revision_checklist")
    target_dir.mkdir(parents=True, exist_ok=True)
    checklist_path = target_dir / "template_revision_checklist.json"
    markdown_path = target_dir / "template_revision_checklist.md"
    checklist_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_template_revision_checklist_markdown(payload), encoding="utf-8")
    return TemplateRevisionChecklistResult(
        template_name=bundle.name,
        output_dir=target_dir,
        checklist_path=checklist_path,
        markdown_path=markdown_path,
        payload=payload,
    )


def render_template_revision_checklist_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    template_patch = payload.get("template_json_patch", {})
    locator_patch = payload.get("locator_hints_patch", {})
    baseline_patch = payload.get("baseline_patch", {})
    schema_patch = payload.get("data_schema_patch", {})
    file_actions = payload.get("file_actions", [])
    review_items = payload.get("review_items", [])

    lines = [
        "# Template Revision Checklist",
        "",
        f"- template: {payload.get('template_name', '')}",
        f"- generated_at: {payload.get('generated_at', '')}",
        f"- candidate_feature_count: {summary.get('candidate_feature_count', 0)}",
        f"- candidate_step_count: {summary.get('candidate_step_count', 0)}",
        f"- recommended_locator_count: {summary.get('recommended_locator_count', 0)}",
        f"- mapped_field_count: {summary.get('mapped_field_count', 0)}",
        f"- needs_review_field_count: {summary.get('needs_review_field_count', 0)}",
        "",
        "## File Actions",
        "",
    ]
    for item in file_actions:
        lines.append(f"- {item.get('file')}: {item.get('action')}")
        if item.get("reason"):
            lines.append(f"  reason: {item.get('reason')}")

    lines.extend(
        [
            "",
            "## Suggested Template Patch",
            "",
            "```json",
            json.dumps(template_patch, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Suggested Locator Hints Patch",
            "",
            "```json",
            json.dumps(locator_patch, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Suggested Baseline Patch",
            "",
            "```json",
            json.dumps(baseline_patch, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Suggested Data Schema Patch",
            "",
            "```json",
            json.dumps(schema_patch, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Review Items",
            "",
        ]
    )
    for item in review_items:
        status = item.get("status") or "needs_review"
        lines.append(f"- [{ 'x' if status == 'ready' else ' ' }] {item.get('title')}")
        if item.get("detail"):
            lines.append(f"  detail: {item.get('detail')}")
        if item.get("target_file"):
            lines.append(f"  target_file: {item.get('target_file')}")
    lines.append("")
    return "\n".join(lines)


def _build_checklist_payload(
    *,
    bundle: TemplateBundle,
    discovery_payload: dict[str, Any],
    candidate_review: dict[str, Any],
) -> dict[str, Any]:
    page_entry_patch = _build_page_entry_patch(bundle, discovery_payload, candidate_review)
    feature_point_patch = _build_feature_point_patch(bundle, discovery_payload, candidate_review)
    step_patch = _build_step_patch(candidate_review)
    locator_patch = _build_locator_patch(discovery_payload, candidate_review)
    baseline_patch = _build_baseline_patch(bundle, page_entry_patch, candidate_review)
    data_schema_patch = _build_data_schema_patch(candidate_review)
    review_items = _build_review_items(
        bundle=bundle,
        discovery_payload=discovery_payload,
        candidate_review=candidate_review,
        page_entry_patch=page_entry_patch,
        feature_point_patch=feature_point_patch,
        step_patch=step_patch,
        locator_patch=locator_patch,
        data_schema_patch=data_schema_patch,
    )

    candidate_steps = _as_list(candidate_review.get("candidate_steps"))
    field_mappings = _as_list(candidate_review.get("field_mappings"))
    recommended_locators = locator_patch.get("recommended_locators", {})
    return {
        "schema_version": "template_revision_checklist.v1",
        "template_name": bundle.name,
        "generated_at": _utc_now_iso(),
        "sources": {
            "template_dir": str(bundle.template_dir),
            "discovery_dir": str(discovery_payload.get("_source_dir") or ""),
            "candidate_review_path": str(candidate_review.get("_source_path") or ""),
        },
        "summary": {
            "candidate_feature_count": len(_as_list(discovery_payload.get("feature_points"))),
            "candidate_step_count": len(candidate_steps),
            "recommended_locator_count": len(recommended_locators),
            "mapped_field_count": sum(1 for item in field_mappings if item.get("project_field_key")),
            "needs_review_field_count": sum(
                1 for item in field_mappings if item.get("review_status") != "mapped_to_project_field"
            ),
        },
        "file_actions": [
            {
                "file": "template.json",
                "action": "replace page_entry / feature_point / steps from suggested patch if they are better than current scaffold",
                "reason": "Combines current template, discovery candidates, and recording candidates into one revision target.",
            },
            {
                "file": "locator_hints.json",
                "action": "copy recommended locators and candidate texts, then trim unstable CSS selectors manually",
                "reason": "Discovery and recording already captured the first batch of candidate locators.",
            },
            {
                "file": "baseline.json",
                "action": "keep page context and optional observed URLs; add only the minimum defaults actually needed",
                "reason": "Avoid overfilling baseline values before the feature flow is stable.",
            },
            {
                "file": "data_schema.json",
                "action": "adopt mapped field rules first; leave unresolved fields for manual confirmation",
                "reason": "Candidate review can already provide usable field_rules and field_constraints fragments.",
            },
        ],
        "template_json_patch": {
            "page_entry": page_entry_patch,
            "feature_point": feature_point_patch,
            "steps": step_patch,
            "notes_append": _build_template_notes_append(discovery_payload, candidate_review),
        },
        "locator_hints_patch": locator_patch,
        "baseline_patch": baseline_patch,
        "data_schema_patch": data_schema_patch,
        "review_items": review_items,
    }


def _build_page_entry_patch(
    bundle: TemplateBundle,
    discovery_payload: dict[str, Any],
    candidate_review: dict[str, Any],
) -> dict[str, Any]:
    current_page_entry = bundle.template.get("page_entry", {}) if isinstance(bundle.template, dict) else {}
    discovery_page_entries = _as_list(discovery_payload.get("page_entries"))
    first_entry = discovery_page_entries[0] if discovery_page_entries else {}
    candidate_page_entry = candidate_review.get("page_entry", {}) if isinstance(candidate_review, dict) else {}
    return {
        "name": _first_nonempty(
            _text(first_entry.get("name")),
            _text(candidate_page_entry.get("name")),
            _text(current_page_entry.get("name")),
        ),
        "url": _first_nonempty(
            _text(first_entry.get("url")),
            _text(candidate_page_entry.get("url")),
            _text(current_page_entry.get("url")),
        ),
        "observed_urls": _as_list(candidate_page_entry.get("observed_urls")),
        "evidence": {
            "discovery_title": _text((first_entry.get("evidence") or {}).get("title")),
            "discovery_source": _text(first_entry.get("source")),
            "current_template_url": _text(current_page_entry.get("url")),
            "semantic_page_type": _text(first_entry.get("semantic_page_type")),
            "semantic_page_type_confidence": _text(first_entry.get("semantic_page_type_confidence")),
            "entry_role": _text(first_entry.get("entry_role")),
        },
    }


def _build_feature_point_patch(
    bundle: TemplateBundle,
    discovery_payload: dict[str, Any],
    candidate_review: dict[str, Any],
) -> dict[str, Any]:
    current_feature = bundle.template.get("feature_point", {}) if isinstance(bundle.template, dict) else {}
    candidate_feature = candidate_review.get("feature_point", {}) if isinstance(candidate_review, dict) else {}
    discovery_features = _as_list(discovery_payload.get("feature_points"))
    preferred_feature = _pick_preferred_feature(discovery_features, current_feature)
    alternatives = [
        {
            "name": _text(item.get("name")),
            "feature_type": _text(item.get("feature_type")),
            "occurrence_count": int(((item.get("evidence") or {}).get("occurrence_count") or 0)),
        }
        for item in discovery_features[:8]
        if _text(item.get("name"))
    ]
    return {
        "name": _first_nonempty(
            _text(preferred_feature.get("name")),
            _text(candidate_feature.get("name")),
            _text(current_feature.get("name")),
        ),
        "type": _first_nonempty(
            _text(preferred_feature.get("feature_type")),
            _text(candidate_feature.get("type")),
            _text(current_feature.get("type")),
        ),
        "alternatives": alternatives,
        "selection_reason": _text(preferred_feature.get("selection_reason")) or "prefer discovery-visible action",
    }


def _build_step_patch(candidate_review: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_steps = _as_list(candidate_review.get("candidate_steps"))
    if not candidate_steps:
        return []
    steps: list[dict[str, Any]] = []
    for raw_step in candidate_steps:
        if not isinstance(raw_step, dict):
            continue
        step_id = _text(raw_step.get("id"))
        action = _text(raw_step.get("action"))
        if not step_id or not action or action == "manual_review_required":
            continue
        step: dict[str, Any] = {
            "id": step_id,
            "kind": _text(raw_step.get("kind")) or "recorded_action",
            "action": action,
        }
        args = raw_step.get("args")
        if isinstance(args, dict) and args:
            step["args"] = args
        field_mapping = raw_step.get("field_mapping")
        if isinstance(field_mapping, dict):
            candidate_data_ref = _text(field_mapping.get("candidate_data_ref"))
            if candidate_data_ref and isinstance(step.get("args"), dict):
                step["args"] = dict(step["args"])
                step["args"]["data_ref"] = candidate_data_ref
        locator = _text(raw_step.get("locator"))
        if locator and isinstance(step.get("args"), dict) and "locator" not in step["args"]:
            step["args"]["locator"] = locator
        steps.append(step)
    return steps


def _build_locator_patch(discovery_payload: dict[str, Any], candidate_review: dict[str, Any]) -> dict[str, Any]:
    recommended_locators: dict[str, Any] = {}
    for item in _as_list(discovery_payload.get("feature_points")):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        locator = _text(evidence.get("locator"))
        if not name or not locator:
            continue
        hint_key = _normalize_hint_key(name)
        recommended_locators.setdefault(hint_key, locator)

    candidate_field_labels: list[str] = []
    candidate_button_texts: list[str] = []
    for mapping in _as_list(candidate_review.get("field_mappings")):
        if not isinstance(mapping, dict):
            continue
        label = _text(mapping.get("label"))
        if label and label not in candidate_field_labels:
            candidate_field_labels.append(label)
    for step in _as_list(candidate_review.get("candidate_steps")):
        if not isinstance(step, dict):
            continue
        if _text(step.get("action")) != "click_by_locator":
            continue
        label = _text(step.get("label"))
        if label and label not in candidate_button_texts:
            candidate_button_texts.append(label)

    return {
        "recommended_locators": recommended_locators,
        "bootstrap_hints": {
            "candidate_button_texts": candidate_button_texts,
            "candidate_field_labels": candidate_field_labels,
            "candidate_dialog_titles": _extract_candidate_dialog_titles(discovery_payload, candidate_review),
        },
    }


def _build_baseline_patch(
    bundle: TemplateBundle,
    page_entry_patch: dict[str, Any],
    candidate_review: dict[str, Any],
) -> dict[str, Any]:
    baseline = bundle.baseline if isinstance(bundle.baseline, dict) else {}
    page_entry = baseline.get("page_entry", {}) if isinstance(baseline.get("page_entry"), dict) else {}
    observed_urls = _as_list((candidate_review.get("page_entry") or {}).get("observed_urls"))
    return {
        "page_entry": {
            "name": _first_nonempty(_text(page_entry_patch.get("name")), _text(page_entry.get("name"))),
            "url": _first_nonempty(_text(page_entry_patch.get("url")), _text(page_entry.get("url"))),
            "observed_urls": observed_urls,
        }
    }


def _build_data_schema_patch(candidate_review: dict[str, Any]) -> dict[str, Any]:
    field_mappings = _as_list(candidate_review.get("field_mappings"))
    field_rules: dict[str, Any] = {}
    field_constraints: dict[str, Any] = {}
    field_samples: dict[str, Any] = {}
    candidate_form_samples: dict[str, Any] = {}
    for item in field_mappings:
        if not isinstance(item, dict):
            continue
        project_field_key = _text(item.get("project_field_key"))
        if not project_field_key:
            continue
        candidate_data_ref = _text(item.get("candidate_data_ref")) or f"candidate_form.{project_field_key}"
        schema_hint = item.get("value_schema_hint") if isinstance(item.get("value_schema_hint"), dict) else {}
        rule = schema_hint.get("rule") if isinstance(schema_hint.get("rule"), dict) else {}
        constraints = schema_hint.get("constraints") if isinstance(schema_hint.get("constraints"), dict) else {}
        field_rules[project_field_key] = {
            **(rule or {"strategy": "constant"}),
            "path": candidate_data_ref,
        }
        if constraints:
            field_constraints[project_field_key] = constraints
        sample_value = item.get("sample_value")
        if sample_value not in (None, ""):
            field_samples[project_field_key] = sample_value
            candidate_form_samples[project_field_key] = sample_value
    return {
        "schema_version": "human_recording_candidate_schema.v1",
        "strategy": "baseline_plus_safe_variation",
        "field_rules": field_rules,
        "field_constraints": field_constraints,
        "field_samples": field_samples,
        "baseline_candidate_form_patch": candidate_form_samples,
    }


def _build_template_notes_append(discovery_payload: dict[str, Any], candidate_review: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    capture_summary = candidate_review.get("capture_summary") if isinstance(candidate_review, dict) else {}
    if isinstance(capture_summary, dict):
        warnings = _as_list(capture_summary.get("warnings"))
        for value in warnings[:5]:
            text = _text(value)
            if text:
                notes.append(f"human_recording_warning: {text}")
    for value in _as_list(discovery_payload.get("notes"))[:5]:
        text = _text(value)
        if text:
            notes.append(f"discovery_note: {text}")
    return notes


def _build_review_items(
    *,
    bundle: TemplateBundle,
    discovery_payload: dict[str, Any],
    candidate_review: dict[str, Any],
    page_entry_patch: dict[str, Any],
    feature_point_patch: dict[str, Any],
    step_patch: list[dict[str, Any]],
    locator_patch: dict[str, Any],
    data_schema_patch: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_page_name = _text((bundle.template.get("page_entry") or {}).get("name"))
    suggested_page_name = _text(page_entry_patch.get("name"))
    if suggested_page_name and suggested_page_name != current_page_name:
        items.append(
            {
                "title": "更新 page_entry.name",
                "detail": f"当前值为 {current_page_name or '空'}，建议改为 {suggested_page_name}。",
                "target_file": "template.json",
                "status": "needs_review",
            }
        )
    semantic_page_type = _text((page_entry_patch.get("evidence") or {}).get("semantic_page_type"))
    semantic_confidence = _text((page_entry_patch.get("evidence") or {}).get("semantic_page_type_confidence"))
    if semantic_page_type:
        items.append(
            {
                "title": "确认页面语义初分",
                "detail": f"discovery 当前把该页面初分为 {semantic_page_type}（confidence={semantic_confidence or 'unknown'}），请确认模板是否应该收敛在这个页面类型上。",
                "target_file": "template.json",
                "status": "needs_review" if semantic_confidence != "high" else "ready",
            }
        )
    current_feature_name = _text((bundle.template.get("feature_point") or {}).get("name"))
    suggested_feature_name = _text(feature_point_patch.get("name"))
    if suggested_feature_name and suggested_feature_name != current_feature_name:
        items.append(
            {
                "title": "确认 feature_point.name",
                "detail": f"建议优先使用 {suggested_feature_name}，同时参考 alternatives 列表。",
                "target_file": "template.json",
                "status": "needs_review",
            }
        )
    if not step_patch:
        items.append(
            {
                "title": "补充真实交互 steps",
                "detail": "当前 recording 还没有可直接回放的交互步骤，至少需要一轮更完整的人工作业或补充 discovery 结果。",
                "target_file": "template.json",
                "status": "needs_review",
            }
        )
    else:
        items.append(
            {
                "title": "替换 bootstrap steps",
                "detail": f"可从 candidate_steps 中采用 {len(step_patch)} 条建议步骤替换当前 bootstrap 占位步骤。",
                "target_file": "template.json",
                "status": "ready",
            }
        )

    unresolved_fields = [
        item for item in _as_list(candidate_review.get("field_mappings")) if item.get("review_status") != "mapped_to_project_field"
    ]
    if unresolved_fields:
        items.append(
            {
                "title": "确认未映射字段",
                "detail": f"仍有 {len(unresolved_fields)} 个字段未映射到项目字段名，先确认 project_field_key 再补 data_schema.json。",
                "target_file": "data_schema.json",
                "status": "needs_review",
            }
        )
    elif data_schema_patch.get("field_rules"):
        items.append(
            {
                "title": "回填 data_schema field_rules",
                "detail": f"已有 {len(data_schema_patch.get('field_rules', {}))} 个字段具备可复制的规则建议。",
                "target_file": "data_schema.json",
                "status": "ready",
            }
        )

    recommended_locators = locator_patch.get("recommended_locators", {})
    if recommended_locators:
        items.append(
            {
                "title": "整理 locator hints",
                "detail": f"discovery 已提供 {len(recommended_locators)} 条候选 locator，建议优先保留稳定文本或语义定位，谨慎直接采用长 CSS 路径。",
                "target_file": "locator_hints.json",
                "status": "needs_review",
            }
        )

    feature_candidates = _as_list(discovery_payload.get("feature_points"))
    if len(feature_candidates) > 1:
        items.append(
            {
                "title": "确认本模板只锁定一个功能点",
                "detail": f"discovery 当前发现了 {len(feature_candidates)} 个候选功能点，需要明确本模板到底收敛到哪一个。",
                "target_file": "template.json",
                "status": "needs_review",
            }
        )

    return items


def _load_discovery_payload(discovery_dir: Path | None) -> dict[str, Any]:
    if not discovery_dir:
        return {}
    result = DiscoveryArtifactWriter.load(discovery_dir)
    if result is None:
        return {}
    payload = result.to_dict()
    payload["_source_dir"] = str(discovery_dir)
    return payload


def _pick_preferred_feature(
    discovery_features: list[dict[str, Any]],
    current_feature: dict[str, Any],
) -> dict[str, Any]:
    current_name = _text(current_feature.get("name"))
    if current_name:
        for item in discovery_features:
            if _text(item.get("name")) == current_name:
                selected = dict(item)
                selected["selection_reason"] = "matched current feature_point.name"
                return selected
    ranked = sorted(
        discovery_features,
        key=lambda item: (
            -_feature_type_priority(_text(item.get("feature_type"))),
            -int(((item.get("evidence") or {}).get("occurrence_count") or 0)),
            _text(item.get("name")) or "",
        ),
    )
    if ranked:
        selected = dict(ranked[0])
        selected["selection_reason"] = "best discovery candidate by feature type and occurrence"
        return selected
    return {}


def _feature_type_priority(value: str | None) -> int:
    mapping = {
        "查询": 100,
        "查看": 95,
        "导航": 90,
        "新增": 85,
        "编辑": 80,
        "操作": 70,
    }
    return mapping.get(_text(value) or "", 0)


def _extract_candidate_dialog_titles(discovery_payload: dict[str, Any], candidate_review: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for step in _as_list(candidate_review.get("candidate_steps")):
        if not isinstance(step, dict):
            continue
        label = _text(step.get("label"))
        if label and any(token in label for token in ("详情", "弹窗", "抽屉", "对话框")) and label not in titles:
            titles.append(label)
    for item in _as_list(discovery_payload.get("feature_points")):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if name and any(token in name for token in ("详情", "关闭", "修改")) and name not in titles:
            titles.append(name)
    return titles


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        payload["_source_path"] = str(path)
        return payload
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_hint_key(value: str) -> str:
    text = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
    text = "_".join(part for part in text.split("_") if part)
    return text or "candidate_locator"


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
