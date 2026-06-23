from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.discovery.models import FeaturePointRecord, PageEntryRecord  # noqa: E402
from prototype.stage2.app.discovery.summary import build_discovery_views  # noqa: E402


def test_build_discovery_views_generates_navigation_tree_and_semantic_summary() -> None:
    root_page = PageEntryRecord(
        page_entry_id="page_root",
        name="业务查询中心",
        url="https://example.com/query",
        template_name="demo_query",
        source="playwright.live_page",
        confidence="live_page_loaded",
        page_type="page_entry",
        evidence={"title": "业务查询中心"},
    )
    detail_page = PageEntryRecord(
        page_entry_id="page_detail",
        name="业务详情",
        url="https://example.com/detail",
        template_name="demo_query",
        source="playwright.same_origin_link",
        confidence="live_traversed",
        page_type="linked_page_entry",
        discovery_depth=1,
        source_page_entry_id="page_root",
        parent_page_entry_id="page_root",
        source_action_type="navigate",
        entry_role="linked_entry",
        evidence={"title": "业务详情"},
    )
    feature_points = [
        FeaturePointRecord(
            feature_point_id="fp_query",
            page_entry_id="page_root",
            name="查询",
            feature_type="查询",
            template_name="demo_query",
            source="playwright.visible_action",
            confidence="live_visible",
            feature_scope="page_action",
            action_type="filter",
            evidence={"container_type": "page", "occurrence_count": 1},
        ),
        FeaturePointRecord(
            feature_point_id="fp_reset",
            page_entry_id="page_root",
            name="重置",
            feature_type="查询",
            template_name="demo_query",
            source="playwright.visible_action",
            confidence="live_visible",
            feature_scope="page_action",
            action_type="filter",
            evidence={"container_type": "page", "occurrence_count": 1},
        ),
        FeaturePointRecord(
            feature_point_id="fp_row_detail",
            page_entry_id="page_root",
            name="详情",
            feature_type="查看",
            template_name="demo_query",
            source="playwright.visible_action",
            confidence="live_visible",
            feature_scope="row_action",
            action_type="inspect",
            evidence={"container_type": "table_row", "occurrence_count": 5},
        ),
        FeaturePointRecord(
            feature_point_id="fp_detail_close",
            page_entry_id="page_detail",
            name="关闭",
            feature_type="操作",
            template_name="demo_query",
            source="playwright.visible_action",
            confidence="live_visible",
            feature_scope="modal_action",
            action_type="trigger",
            evidence={"container_type": "modal", "occurrence_count": 1},
        ),
    ]
    navigation_nodes = [
        {
            "navigation_node_id": "nav_root",
            "owner_page_entry_id": "page_root",
            "label": "业务查询",
            "node_kind": "menu_item",
            "menu_path_labels": ["业务管理", "业务查询"],
            "menu_level": 1,
            "sibling_order": 0,
            "locator": "nav > a.query",
            "href": "https://example.com/query",
            "visible": True,
            "expanded": True,
            "target_page_entry_id": "page_root",
            "status": "mapped_to_page_entry",
            "status_reason": "dom_scan",
            "traversal_method": "playwright_probe",
        },
        {
            "navigation_node_id": "nav_detail",
            "owner_page_entry_id": "page_root",
            "label": "业务详情",
            "node_kind": "menu_item",
            "menu_path_labels": ["业务管理", "业务查询", "业务详情"],
            "menu_level": 2,
            "sibling_order": 1,
            "locator": "table .detail",
            "href": "https://example.com/detail",
            "visible": True,
            "expanded": None,
            "target_page_entry_id": "page_detail",
            "status": "mapped_to_page_entry",
            "status_reason": "playwright_probe",
            "traversal_method": "playwright_probe",
        },
    ]

    views = build_discovery_views(
        page_entries=[root_page, detail_page],
        feature_points=feature_points,
        navigation_nodes=navigation_nodes,
    )

    page_entries = views["page_entries"]
    root_entry = next(item for item in page_entries if item.page_entry_id == "page_root")
    detail_entry = next(item for item in page_entries if item.page_entry_id == "page_detail")
    assert root_entry.semantic_page_type == "查询列表页"
    assert detail_entry.entry_role == "linked_entry"
    assert any(item["semantic_page_type"] == "查询列表页" for item in views["page_semantic_summary"])
    assert views["stats"]["navigation_node_count"] == 2
    assert views["stats"]["semantic_page_type_breakdown"]["查询列表页"] >= 1

    tree = views["navigation_tree"]
    assert tree
    root_children = tree[0]["children"]
    assert root_children
    assert root_children[0]["kind"] == "menu_group"
    assert root_children[0]["children"][0]["label"] == "业务查询"
