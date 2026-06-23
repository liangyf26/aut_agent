from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.discovery.models import DiscoveryResult, FeaturePointRecord, PageEntryRecord, ScreenshotRecord  # noqa: E402
from prototype.stage2.app.discovery.writer import DiscoveryArtifactWriter  # noqa: E402
from prototype.stage2.app.runtime.template_bootstrap import bootstrap_template_bundle  # noqa: E402
from prototype.stage2.app.runtime.template_revision_checklist import build_template_revision_checklist  # noqa: E402
from prototype.stage2.main import generate_template_revision_checklist  # noqa: E402


def test_build_template_revision_checklist_combines_discovery_and_candidate_review() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        template_root = root / "templates"
        result = bootstrap_template_bundle(
            template_root,
            template_name="demo_query",
            page_url="https://example.com/query",
            page_name="示例查询页",
            scenario_kind="query",
        )

        discovery_dir = root / "live_discovery_demo_query"
        discovery = DiscoveryResult(
            template_name="demo_query",
            generated_at="2026-06-23T10:00:00+00:00",
            strategy="playwright_controlled_live",
            page_entries=[
                PageEntryRecord(
                    page_entry_id="page_1",
                    name="示例查询页面",
                    url="https://example.com/query",
                    template_name="demo_query",
                    source="playwright.live_page",
                    confidence="live_page_loaded",
                    semantic_page_type="查询列表页",
                    semantic_page_type_confidence="medium",
                    evidence={"title": "查询中心"},
                )
            ],
            feature_points=[
                FeaturePointRecord(
                    feature_point_id="fp_1",
                    page_entry_id="page_1",
                    name="重置",
                    feature_type="查询",
                    template_name="demo_query",
                    source="playwright.visible_action",
                    confidence="live_visible",
                    evidence={"locator": "button:has-text('重置')", "occurrence_count": 1},
                )
            ],
            screenshot_records=[
                ScreenshotRecord(
                    screenshot_id="shot_1",
                    page_entry_id="page_1",
                    feature_point_id=None,
                    stage="page_entry_landing",
                    purpose="landing",
                    status="captured",
                    relative_path="screenshots/discovery/demo_query/landing.png",
                    source="playwright.live_page",
                )
            ],
        )
        DiscoveryArtifactWriter(discovery_dir).write(discovery)

        candidate_review_path = root / "candidate_template_review.json"
        candidate_review_path.write_text(
            json.dumps(
                {
                    "template_name": "demo_query",
                    "page_entry": {
                        "name": "示例查询页",
                        "url": "https://example.com/query",
                        "observed_urls": ["https://example.com/query"],
                    },
                    "feature_point": {
                        "name": "查询条件重置",
                        "type": "查询",
                    },
                    "candidate_steps": [
                        {
                            "id": "step_fill_keyword",
                            "kind": "field_input",
                            "action": "fill_field_by_locator",
                            "label": "关键字",
                            "locator": "#keyword",
                            "args": {"locator": "#keyword", "value": "演示值"},
                            "field_mapping": {
                                "candidate_data_ref": "candidate_form.keyword",
                                "project_field_key": "keyword",
                            },
                        },
                        {
                            "id": "step_click_reset",
                            "kind": "action",
                            "action": "click_by_locator",
                            "label": "重置",
                            "locator": "button:has-text('重置')",
                            "args": {"locator": "button:has-text('重置')"},
                        },
                    ],
                    "field_mappings": [
                        {
                            "label": "关键字",
                            "project_field_key": "keyword",
                            "candidate_data_ref": "candidate_form.keyword",
                            "review_status": "mapped_to_project_field",
                            "sample_value": "演示值",
                            "value_schema_hint": {
                                "rule": {"strategy": "unique_text", "separator": "-", "token": "{run_suffix}"},
                                "constraints": {"type": "str", "non_empty": True},
                            },
                        }
                    ],
                    "capture_summary": {"warnings": []},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        checklist = build_template_revision_checklist(
            result.template_dir,
            discovery_dir=discovery_dir,
            candidate_review_path=candidate_review_path,
        )

        assert checklist.checklist_path.exists()
        assert checklist.markdown_path.exists()
        payload = checklist.payload
        assert payload["template_json_patch"]["feature_point"]["name"] == "重置"
        assert payload["template_json_patch"]["page_entry"]["evidence"]["semantic_page_type"] == "查询列表页"
        assert payload["template_json_patch"]["steps"][0]["action"] == "fill_field_by_locator"
        assert payload["locator_hints_patch"]["recommended_locators"]["重置"] == "button:has-text('重置')"
        assert payload["data_schema_patch"]["field_rules"]["keyword"]["path"] == "candidate_form.keyword"
        assert any(item["title"] == "替换 bootstrap steps" for item in payload["review_items"])
        assert any(item["title"] == "确认页面语义初分" for item in payload["review_items"])


def test_generate_template_revision_checklist_works_with_template_only(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        template_root = root / "templates"
        bootstrap_template_bundle(
            template_root,
            template_name="demo_nav",
            page_url="https://example.com/nav",
            page_name="导航页",
            scenario_kind="navigation",
        )

        import prototype.stage2.main as stage2_main

        monkeypatch.setattr(stage2_main, "TEMPLATE_ROOT", template_root)
        monkeypatch.setattr(stage2_main, "HUMAN_LOOP_ROOT", root / "human_loop")

        payload = generate_template_revision_checklist(
            "demo_nav",
            discovery_dir="",
            candidate_review_path="",
            output_dir=str(root / "output"),
        )

        assert Path(payload["checklist_path"]).exists()
        assert Path(payload["markdown_path"]).exists()
        assert payload["summary"]["candidate_step_count"] == 0
        persisted = json.loads(Path(payload["checklist_path"]).read_text(encoding="utf-8"))
        assert persisted["template_name"] == "demo_nav"
        assert persisted["template_json_patch"]["steps"] == []
