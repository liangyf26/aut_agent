from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.template_bootstrap import bootstrap_template_bundle  # noqa: E402
from prototype.stage2.app.runtime.templates import load_template_bundle  # noqa: E402
from prototype.stage2.main import bootstrap_template, list_templates  # noqa: E402


def test_bootstrap_template_bundle_creates_minimal_scaffold() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        result = bootstrap_template_bundle(
            root,
            template_name="demo_query_entry",
            page_url="https://example.com/query",
            page_name="示例查询页",
            scenario_kind="query",
        )

        assert result.template_dir == root / "demo_query_entry"
        assert result.files["template"].exists()
        assert result.files["baseline"].exists()
        assert result.files["data_schema"].exists()
        assert result.files["locator_hints"].exists()

        bundle = load_template_bundle(result.template_dir)
        assert bundle.template["template_name"] == "demo_query_entry"
        assert bundle.template["page_entry"]["url"] == "https://example.com/query"
        assert bundle.template["feature_point"]["type"] == "查询"
        assert bundle.template["steps"][0]["action"] == "navigate_to_url"
        assert bundle.template["steps"][1]["action"] == "capture_named_screenshot"
        assert bundle.baseline["bootstrap"]["scenario_kind"] == "query"
        assert bundle.data_schema["strategy"] == "bootstrap_placeholder"
        assert "bootstrap_hints" in bundle.locator_hints


def test_bootstrap_template_bundle_rejects_existing_template_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        bootstrap_template_bundle(
            root,
            template_name="demo_existing",
            page_url="https://example.com/a",
        )

        try:
            bootstrap_template_bundle(
                root,
                template_name="demo_existing",
                page_url="https://example.com/b",
            )
        except FileExistsError as exc:
            assert "模板目录已存在" in str(exc)
        else:
            raise AssertionError("expected FileExistsError")


def test_main_bootstrap_template_and_list_templates_support_scaffolded_template(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        template_root = Path(tmpdir)
        import prototype.stage2.main as stage2_main

        monkeypatch.setattr(stage2_main, "TEMPLATE_ROOT", template_root)
        payload = bootstrap_template(
            "demo_detail_entry",
            page_url="https://example.com/detail",
            page_name="示例详情页",
            scenario_kind="detail",
        )

        assert payload["template"] == "demo_detail_entry"
        assert Path(payload["template_dir"]).exists()
        assert Path(payload["template_path"]).exists()
        assert payload["feature_type"] == "查看"

        templates = list_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "demo_detail_entry"
        assert templates[0]["entry_point"] == "示例详情页"
        assert templates[0]["feature_point"] == payload["feature_name"]

        persisted = json.loads(Path(payload["template_path"]).read_text(encoding="utf-8"))
        assert persisted["bootstrap"]["status"] == "draft_needs_discovery_review"
        assert persisted["execution_path"] == "bootstrap_detail"
