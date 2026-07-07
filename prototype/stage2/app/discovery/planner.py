from __future__ import annotations

from typing import Any

from .identity import build_feature_point_identity, build_page_entry_identity
from .models import (
    DiscoveryResult,
    FeaturePointRecord,
    PageEntryRecord,
    ScreenshotRecord,
    utc_now_iso,
)
from .summary import build_discovery_views


class DiscoveryPlanner:
    """Builds a conservative discovery result from a verified template."""

    def __init__(self, *, strategy_name: str = "template_seeded_minimum") -> None:
        self.strategy_name = strategy_name

    def plan(
        self,
        *,
        template_name: str,
        template: dict[str, Any],
        baseline: dict[str, Any] | None = None,
    ) -> DiscoveryResult:
        page_entry = template.get("page_entry") or {}
        feature_point = template.get("feature_point") or {}
        execution_path = template.get("execution_path")
        step_ids = self._step_ids(template.get("steps"))

        page_identity = build_page_entry_identity(
            template_name,
            name=page_entry.get("name", template_name),
            url=page_entry.get("url", ""),
        )
        page_entry_id = page_identity["record_id"]
        feature_identity = build_feature_point_identity(
            template_name,
            page_entry_key=page_identity["stable_key"],
            name=feature_point.get("name", page_entry.get("name", template_name)),
            feature_scope="page_action",
            action_type="verified_flow_entry",
            container_label=page_entry.get("name", template_name),
        )
        feature_point_id = feature_identity["record_id"]

        page_entries = [
            PageEntryRecord(
                page_entry_id=page_entry_id,
                name=page_entry.get("name", template_name),
                url=page_entry.get("url", ""),
                template_name=template_name,
                source="template.page_entry",
                confidence="verified_path_seed",
                stable_key=page_identity["stable_key"],
                dedupe_key=page_identity["dedupe_key"],
                dedupe_basis=page_identity["dedupe_basis"],
                discovery_depth=0,
                execution_path=execution_path,
                evidence={
                    "step_ids": step_ids,
                    "baseline_reference_record_id": (baseline or {}).get("reference_success_record_id"),
                },
            )
        ]
        feature_points = [
            FeaturePointRecord(
                feature_point_id=feature_point_id,
                page_entry_id=page_entry_id,
                name=feature_point.get("name", page_entry.get("name", template_name)),
                feature_type=feature_point.get("type", "unknown"),
                template_name=template_name,
                source="template.feature_point",
                confidence="verified_path_seed",
                feature_scope="page_action",
                action_type="verified_flow_entry",
                stable_key=feature_identity["stable_key"],
                dedupe_key=feature_identity["dedupe_key"],
                dedupe_basis=feature_identity["dedupe_basis"],
                discovery_depth=0,
                source_page_entry_id=page_entry_id,
                execution_path=execution_path,
                evidence={
                    "step_ids": step_ids,
                    "success_ui_text": ((baseline or {}).get("success_markers") or {}).get("ui_text"),
                },
            )
        ]
        screenshot_records = self._build_screenshot_records(
            page_entry_id=page_entry_id,
            feature_point_id=feature_point_id,
            page_entry=page_entry,
            feature_point=feature_point,
            execution_path=execution_path,
        )

        views = build_discovery_views(
            page_entries=page_entries,
            feature_points=feature_points,
            navigation_nodes=[
                {
                    "navigation_node_id": f"seed::{page_entry_id}",
                    "owner_page_entry_id": page_entry_id,
                    "parent_navigation_node_id": None,
                    "label": page_entry.get("name", template_name),
                    "node_kind": "seed_entry",
                    "menu_path_labels": [page_entry.get("name", template_name)],
                    "menu_level": 0,
                    "sibling_order": 0,
                    "locator": "",
                    "href": page_entry.get("url", ""),
                    "visible": True,
                    "expanded": None,
                    "target_page_entry_id": page_entry_id,
                    "status": "mapped_to_page_entry",
                    "status_reason": "template_seed",
                    "traversal_method": "template_seed",
                }
            ],
        )
        page_entries = views["page_entries"]

        return DiscoveryResult(
            template_name=template_name,
            generated_at=utc_now_iso(),
            strategy=self.strategy_name,
            page_entries=page_entries,
            feature_points=feature_points,
            screenshot_records=screenshot_records,
            navigation_tree=views["navigation_tree"],
            page_semantic_summary=views["page_semantic_summary"],
            navigation_nodes=[
                {
                    "navigation_node_id": f"seed::{page_entry_id}",
                    "owner_page_entry_id": page_entry_id,
                    "parent_navigation_node_id": None,
                    "label": page_entry.get("name", template_name),
                    "node_kind": "seed_entry",
                    "menu_path_labels": [page_entry.get("name", template_name)],
                    "menu_level": 0,
                    "sibling_order": 0,
                    "locator": "",
                    "href": page_entry.get("url", ""),
                    "visible": True,
                    "expanded": None,
                    "target_page_entry_id": page_entry_id,
                    "status": "mapped_to_page_entry",
                    "status_reason": "template_seed",
                    "traversal_method": "template_seed",
                }
            ],
            review_queue=[
                {
                    "record_type": "page_entry",
                    "record_id": page_entry_id,
                    "priority": "medium",
                    "reason": "template_seed_validation",
                    "fields": {
                        "name": page_entry.get("name", template_name),
                        "url": page_entry.get("url", ""),
                        "page_type": "page_entry",
                        "semantic_page_type": page_entries[0].semantic_page_type,
                        "semantic_page_type_confidence": page_entries[0].semantic_page_type_confidence,
                        "discovery_depth": 0,
                        "stable_key": page_identity["stable_key"],
                    },
                },
                {
                    "record_type": "feature_point",
                    "record_id": feature_point_id,
                    "priority": "medium",
                    "reason": "template_seed_validation",
                    "fields": {
                        "name": feature_point.get("name", page_entry.get("name", template_name)),
                        "page_entry_id": page_entry_id,
                        "feature_scope": "page_action",
                        "action_type": "verified_flow_entry",
                        "discovery_depth": 0,
                        "stable_key": feature_identity["stable_key"],
                    },
                },
            ],
            review_hints={
                "status": "pending_manual_review",
                "entry": {
                    "kind": "manual_review_placeholder",
                    "suggested_outputs": ["page_entries.json", "feature_points.json", "discovery_result.json"],
                    "review_queue_file": "discovery_review_queue.json",
                },
                "recommended_checks": [
                    "确认模板播种的页面入口是否仍然有效。",
                    "确认模板播种的功能点名称与当前页面文案是否一致。",
                ],
            },
            stats={
                "page_entry_count": len(page_entries),
                "feature_point_count": len(feature_points),
                "screenshot_record_count": len(screenshot_records),
                "review_queue_count": 2,
                "seed_step_count": len(step_ids),
                "page_type_breakdown": {"page_entry": len(page_entries)},
                "feature_scope_breakdown": {"page_action": len(feature_points)},
                "action_type_breakdown": {"verified_flow_entry": len(feature_points)},
                **views["stats"],
            },
            notes=[
                "当前发现结果来自已验证模板的保守播种，不包含自动页面遍历。",
                "截图记录先输出计划占位，后续可由浏览器执行器回填真实路径和采集状态。",
            ],
        )

    def _build_screenshot_records(
        self,
        *,
        page_entry_id: str,
        feature_point_id: str,
        page_entry: dict[str, Any],
        feature_point: dict[str, Any],
        execution_path: str | None,
    ) -> list[ScreenshotRecord]:
        slug = self._slug(page_entry.get("name") or page_entry_id)
        feature_slug = self._slug(feature_point.get("name") or feature_point_id)
        return [
            ScreenshotRecord(
                screenshot_id=f"{page_entry_id}__landing",
                page_entry_id=page_entry_id,
                feature_point_id=None,
                stage="page_entry_landing",
                purpose="capture entry page before traversal",
                status="planned",
                relative_path=f"screenshots/discovery/{slug}/landing.png",
                source="planner.placeholder",
                notes=[
                    f"entry_url={page_entry.get('url', '')}",
                    f"execution_path={execution_path or 'unknown'}",
                ],
            ),
            ScreenshotRecord(
                screenshot_id=f"{feature_point_id}__feature",
                page_entry_id=page_entry_id,
                feature_point_id=feature_point_id,
                stage="feature_point_focus",
                purpose="capture the confirmed feature entry point",
                status="planned",
                relative_path=f"screenshots/discovery/{slug}/{feature_slug}.png",
                source="planner.placeholder",
                notes=[
                    f"feature_type={feature_point.get('type', 'unknown')}",
                    "expected_to_be_replaced_by real capture after browser execution",
                ],
            ),
        ]

    def _step_ids(self, steps: Any) -> list[str]:
        if not isinstance(steps, list):
            return []
        return [step["id"] for step in steps if isinstance(step, dict) and isinstance(step.get("id"), str)]

    def _slug(self, value: str) -> str:
        normalized = []
        for ch in value.strip().lower():
            if ch.isalnum():
                normalized.append(ch)
            else:
                normalized.append("_")
        slug = "".join(normalized).strip("_")
        return slug or "item"
