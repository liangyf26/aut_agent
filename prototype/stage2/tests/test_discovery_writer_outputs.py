from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.discovery.models import DiscoveryResult, PageEntryRecord  # noqa: E402
from prototype.stage2.app.discovery.writer import DiscoveryArtifactWriter  # noqa: E402


def test_discovery_writer_persists_navigation_and_semantic_artifacts() -> None:
    result = DiscoveryResult(
        template_name="demo_system_map",
        generated_at="2026-06-23T10:00:00+00:00",
        strategy="playwright_controlled_live",
        page_entries=[
            PageEntryRecord(
                page_entry_id="page_1",
                name="系统首页",
                url="https://example.com/home",
                template_name="demo_system_map",
                source="playwright.live_page",
                confidence="live_page_loaded",
                page_type="page_entry",
                entry_role="seed_entry",
                semantic_page_type="导航页",
                semantic_page_type_confidence="medium",
            )
        ],
        feature_points=[],
        screenshot_records=[],
        navigation_tree=[{"node_id": "page::page_1", "label": "系统首页", "children": []}],
        page_semantic_summary=[
            {
                "page_entry_id": "page_1",
                "semantic_page_type": "导航页",
                "confidence": "medium",
                "signals": {},
            }
        ],
        navigation_nodes=[
            {
                "navigation_node_id": "nav_1",
                "owner_page_entry_id": "page_1",
                "label": "首页",
                "node_kind": "seed_entry",
                "status": "mapped_to_page_entry",
            }
        ],
        stats={"semantic_page_type_breakdown": {"导航页": 1}},
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        paths = DiscoveryArtifactWriter(output_dir).write(result)
        assert paths["navigation_tree"].exists()
        assert paths["page_semantic_summary"].exists()
        assert paths["navigation_nodes"].exists()

        navigation_tree_payload = json.loads(paths["navigation_tree"].read_text(encoding="utf-8"))
        semantic_payload = json.loads(paths["page_semantic_summary"].read_text(encoding="utf-8"))
        node_payload = json.loads(paths["navigation_nodes"].read_text(encoding="utf-8"))
        assert navigation_tree_payload["items"][0]["label"] == "系统首页"
        assert semantic_payload["items"][0]["semantic_page_type"] == "导航页"
        assert node_payload["items"][0]["label"] == "首页"
