from __future__ import annotations

from typing import Any

from .models import (
    DiscoveryResult,
    FeaturePointRecord,
    PageEntryRecord,
    ScreenshotRecord,
    utc_now_iso,
)


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

        page_entry_id = self._page_entry_id(template_name, page_entry)
        feature_point_id = self._feature_point_id(template_name, feature_point)

        page_entries = [
            PageEntryRecord(
                page_entry_id=page_entry_id,
                name=page_entry.get("name", template_name),
                url=page_entry.get("url", ""),
                template_name=template_name,
                source="template.page_entry",
                confidence="verified_path_seed",
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

        return DiscoveryResult(
            template_name=template_name,
            generated_at=utc_now_iso(),
            strategy=self.strategy_name,
            page_entries=page_entries,
            feature_points=feature_points,
            screenshot_records=screenshot_records,
            stats={
                "page_entry_count": len(page_entries),
                "feature_point_count": len(feature_points),
                "screenshot_record_count": len(screenshot_records),
                "seed_step_count": len(step_ids),
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

    def _page_entry_id(self, template_name: str, page_entry: dict[str, Any]) -> str:
        return f"{template_name}__page_entry__{self._slug(page_entry.get('name') or 'entry')}"

    def _feature_point_id(self, template_name: str, feature_point: dict[str, Any]) -> str:
        return f"{template_name}__feature_point__{self._slug(feature_point.get('name') or 'feature')}"

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
