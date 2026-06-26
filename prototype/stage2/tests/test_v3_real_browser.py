from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.v3_orchestrator import V3RunConfig  # noqa: E402
from prototype.stage2.app.v3_real_browser import (  # noqa: E402
    TEST_ENV_FULL_ACCESS_POLICY,
    build_menu_discovery_artifacts,
    _dedupe_page_exploration,
    _infer_feature_type,
    _normalize_page_url,
    _plan_side_effect_actions,
    _resolve_playwright_target_page,
    _should_block_for_login_text,
    _snapshot_is_blank,
    _side_effect_execution_result,
)


class _FakeBodyLocator:
    def __init__(self, page: "_FakePlaywrightPage") -> None:
        self.page = page

    async def inner_text(self, timeout: int = 0) -> str:
        return self.page.body_text


class _FakePlaywrightPage:
    def __init__(self, url: str, body_text: str) -> None:
        self.url = url
        self.body_text = body_text
        self.goto_calls: list[str] = []

    async def bring_to_front(self) -> None:
        return None

    async def wait_for_load_state(self, state: str) -> None:
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        return None

    async def goto(self, url: str, wait_until: str = "") -> None:
        self.goto_calls.append(url)
        self.url = url

    def locator(self, selector: str) -> _FakeBodyLocator:
        assert selector == "body"
        return _FakeBodyLocator(self)


class _FakePlaywrightContext:
    def __init__(self, pages: list[_FakePlaywrightPage]) -> None:
        self.pages = pages


class _FakePlaywrightBrowser:
    def __init__(self, pages: list[_FakePlaywrightPage]) -> None:
        self.contexts = [_FakePlaywrightContext(pages)]


def test_low_risk_only_blocks_side_effect_candidates() -> None:
    plan = _plan_side_effect_actions(
        {
            "controls": [
                {"text": "删除", "tag": "button", "type": "", "candidate_index": 0},
                {"text": "保存", "tag": "button", "type": "", "candidate_index": 1},
            ]
        },
        V3RunConfig(),
    )

    assert plan["selected"] == []
    assert {item["action_type"] for item in plan["skipped"]} == {"delete", "save"}
    assert all(
        item["policy_decision"]["reason_code"] == "requires_test_env_full_access"
        for item in plan["skipped"]
    )


def test_full_access_selects_allowlisted_actions_with_caps() -> None:
    config = V3RunConfig(
        metadata={
            "safety_policy": TEST_ENV_FULL_ACCESS_POLICY,
            "allowed_side_effect_actions": ["delete", "save", "approve", "submit"],
            "max_side_effect_actions": 3,
            "max_side_effect_actions_per_type": 1,
        }
    )

    plan = _plan_side_effect_actions(
        {
            "controls": [
                {"text": "删除", "tag": "button", "candidate_index": 0},
                {"text": "再次删除", "tag": "button", "candidate_index": 1},
                {"text": "审批", "tag": "button", "candidate_index": 2},
                {"text": "保存", "tag": "button", "candidate_index": 3},
                {"text": "提交", "tag": "button", "candidate_index": 4},
                {"text": "新增", "tag": "button", "candidate_index": 5},
            ]
        },
        config,
    )

    assert [item["action_type"] for item in plan["selected"]] == ["delete", "approve", "save"]
    assert [item["execution_order"] for item in plan["selected"]] == [1, 2, 3]
    skipped_reasons = {item["policy_decision"]["reason_code"] for item in plan["skipped"]}
    assert "side_effect_per_type_limit_reached" in skipped_reasons
    assert "side_effect_total_limit_reached" in skipped_reasons
    assert "action_not_allowlisted" in skipped_reasons


def test_side_effect_result_contains_audit_contract_fields() -> None:
    action = {
        "action_id": "side_effect_001_delete",
        "action_type": "delete",
        "control_label": "删除",
        "risk_level": "high",
        "policy_decision": {
            "decision": "allowed",
            "reason_code": "test_env_full_access_allowlisted",
        },
    }

    result = _side_effect_execution_result(
        action,
        status="side_effect_executed",
        started_at="2026-06-25T00:00:00Z",
        before_ref="side_effect_001_before",
        after_ref="side_effect_001_after",
        before_state={"url": "https://example.test/list"},
        after_state={
            "url": "https://example.test/list",
            "visible_text_sample": "删除成功",
            "dialog_events": [{"type": "confirm", "handled": "accepted"}],
        },
        click_result={"dialog_events": []},
        failure_reason=None,
    )

    assert result["action_type"] == "delete"
    assert result["control_label"] == "删除"
    assert result["risk_level"] == "high"
    assert result["policy_decision"]["decision"] == "allowed"
    assert result["before_screenshot_ref"] == "side_effect_001_before"
    assert result["after_screenshot_ref"] == "side_effect_001_after"
    assert result["status"] == "side_effect_executed"
    assert result["failure_reason"] is None
    assert result["dialog_events"] == [{"type": "confirm", "handled": "accepted"}]


def test_side_effect_feature_type_inference_keeps_distinct_actions() -> None:
    assert _infer_feature_type("审批", "button", "") == "approve"
    assert _infer_feature_type("保存", "button", "") == "save"
    assert _infer_feature_type("提交", "button", "") == "submit"
    assert _infer_feature_type("删除", "button", "") == "delete"


def test_menu_discovery_artifacts_capture_success_failure_permission_and_evidence() -> None:
    bundle = build_menu_discovery_artifacts(
        start_url="https://example.test/index",
        menu_candidates=[
            {
                "discovery_id": "m1",
                "text": "业务办理",
                "level": 1,
                "expandable": True,
                "locator": "[data-menu='business']",
                "screenshot_id": "menu_initial",
            },
            {
                "discovery_id": "m2",
                "parent_id": "m1",
                "text": "线上备案申请",
                "level": 2,
                "href": "/online/apply",
                "locator": "[data-menu='online-apply']",
                "screenshot_id": "menu_m1_after_expand",
            },
            {
                "discovery_id": "m3",
                "text": "备案查询",
                "level": 1,
                "href": "/record-query",
                "locator": "[data-menu='query']",
                "screenshot_id": "menu_initial",
            },
            {
                "discovery_id": "m4",
                "text": "系统管理",
                "level": 1,
                "expandable": True,
                "disabled": True,
                "locator": "[data-menu='system']",
                "screenshot_id": "menu_initial",
            },
            {
                "discovery_id": "m5",
                "text": "报表中心",
                "level": 1,
                "expandable": True,
                "locator": "[data-menu='report']",
                "screenshot_id": "menu_initial",
            },
        ],
        traversal_events=[
            {
                "event": "expand",
                "menu_id": "m1",
                "status": "success",
                "screenshot_ref": "menu_m1_after_expand",
            },
            {
                "event": "expand",
                "menu_id": "m4",
                "status": "permission_blocked",
                "failure_reason": "permission_denied",
            },
            {
                "event": "expand",
                "menu_id": "m5",
                "status": "failed",
                "failure_reason": "no_child_menu_appeared",
                "screenshot_ref": "menu_m5_expand_failed",
            },
        ],
        screenshots=[
            {"screenshot_id": "menu_initial", "relative_path": "screenshots/menu_initial.png"},
            {
                "screenshot_id": "menu_m1_after_expand",
                "relative_path": "screenshots/menu_m1_after_expand.png",
            },
            {
                "screenshot_id": "menu_m5_expand_failed",
                "relative_path": "screenshots/menu_m5_expand_failed.png",
            },
        ],
    )

    entries = bundle["menu_entries"]
    assert bundle["menu_tree"]["root_count"] == 4
    assert bundle["menu_tree"]["status"] == "incomplete"
    assert [entry["text"] for entry in entries if entry["is_leaf"]] == [
        "线上备案申请",
        "备案查询",
    ]
    assert next(entry for entry in entries if entry["text"] == "业务办理")["status"] == "expanded"
    assert (
        next(entry for entry in entries if entry["text"] == "系统管理")["status"]
        == "permission_blocked"
    )
    assert next(entry for entry in entries if entry["text"] == "报表中心")[
        "failure_reason"
    ] == "no_child_menu_appeared"
    assert next(entry for entry in entries if entry["text"] == "线上备案申请")[
        "menu_path"
    ] == ["业务办理", "线上备案申请"]
    assert bundle["menu_traversal_log"][2]["screenshot_ref"] == "menu_m5_expand_failed"
    assert bundle["screenshots_index"]["screenshots"][1]["stage"] == "menu_discovery"


def test_menu_discovery_artifacts_tolerate_self_referential_expanded_parent() -> None:
    bundle = build_menu_discovery_artifacts(
        start_url="https://example.test/index",
        menu_candidates=[
            {
                "discovery_id": "menu_4",
                "text": "备案管理",
                "level": 1,
                "expandable": True,
                "locator": "[data-stage2-menu-id='menu_4']",
                "screenshot_id": "menu_initial",
            },
            {
                "discovery_id": "menu_4",
                "parent_id": "menu_4",
                "text": "备案管理 线上备案申请",
                "level": 2,
                "expandable": True,
                "locator": "[data-stage2-menu-id='menu_4']",
                "screenshot_id": "menu_4_after_expand",
            },
            {
                "discovery_id": "menu_12",
                "parent_id": "menu_4",
                "text": "线上备案申请",
                "level": 2,
                "href": "/online/apply",
                "locator": "[data-stage2-menu-id='menu_12']",
                "screenshot_id": "menu_4_after_expand",
            },
        ],
        traversal_events=[
            {
                "event": "expand",
                "menu_id": "menu_4",
                "status": "success",
                "screenshot_ref": "menu_4_after_expand",
            }
        ],
        screenshots=[
            {"screenshot_id": "menu_initial", "relative_path": "screenshots/menu_initial.png"},
            {
                "screenshot_id": "menu_4_after_expand",
                "relative_path": "screenshots/menu_4_after_expand.png",
            },
        ],
    )

    entries = bundle["menu_entries"]
    assert any(entry["text"] == "线上备案申请" for entry in entries)
    assert bundle["menu_tree"]["root_count"] == 1
    assert bundle["menu_tree"]["nodes"][0]["children"][0]["text"] == "线上备案申请"


def test_menu_discovery_prefers_routed_duplicate_over_unrouted_clone() -> None:
    bundle = build_menu_discovery_artifacts(
        start_url="https://example.test/index",
        menu_candidates=[
            {
                "discovery_id": "menu_1",
                "text": "饮片生产",
                "level": 1,
                "expandable": True,
                "locator": "[data-stage2-menu-id='menu_1']",
            },
            {
                "discovery_id": "menu_2",
                "parent_id": "menu_1",
                "text": "线上备案申请",
                "level": 2,
                "href": "/record/online",
                "locator": "[data-stage2-menu-id='menu_2']",
                "screenshot_id": "menu_1_after_expand",
            },
            {
                "discovery_id": "menu_3",
                "parent_id": "menu_1",
                "text": "线上备案申请",
                "level": 2,
                "href": "",
                "locator": "[data-stage2-menu-id='menu_3']",
                "screenshot_id": "menu_1_after_expand",
            },
        ],
        traversal_events=[
            {
                "event": "expand",
                "menu_id": "menu_1",
                "status": "success",
                "screenshot_ref": "menu_1_after_expand",
            }
        ],
        screenshots=[],
    )

    online_entries = [
        entry for entry in bundle["menu_entries"] if entry["text"] == "线上备案申请"
    ]
    assert len(online_entries) == 1
    assert online_entries[0]["route_hint"] == "/record/online"


def test_page_exploration_dedupes_home_aliases_and_keeps_duplicate_features() -> None:
    pages, features = _dedupe_page_exploration(
        [
            {
                "page_id": "menu_page_001",
                "name": "追本溯源管理平台",
                "url": "https://www.zbsykj.com:19096/",
                "status": "reachable",
                "screenshot_refs": ["logo_home"],
            },
            {
                "page_id": "menu_page_002",
                "name": "首页",
                "url": "https://www.zbsykj.com:19096/index",
                "status": "reachable",
                "screenshot_refs": ["home"],
            },
            {
                "page_id": "menu_page_003",
                "name": "线上备案申请",
                "url": "https://www.zbsykj.com:19096/record/online",
                "status": "reachable",
                "screenshot_refs": ["online"],
            },
        ],
        [
            {"feature_id": "feature_001", "page_id": "menu_page_001"},
            {"feature_id": "feature_002", "page_id": "menu_page_002"},
            {"feature_id": "feature_003", "page_id": "menu_page_003"},
        ],
    )

    assert _normalize_page_url("https://www.zbsykj.com:19096/") == (
        "https://www.zbsykj.com:19096/index"
    )
    assert [page["name"] for page in pages] == ["首页", "线上备案申请"]
    assert [feature["feature_id"] for feature in features] == [
        "feature_001",
        "feature_002",
        "feature_003",
    ]
    assert [feature["page_id"] for feature in features] == [
        "menu_page_002",
        "menu_page_002",
        "menu_page_003",
    ]


def test_blank_snapshot_is_not_treated_as_visible_page() -> None:
    assert _snapshot_is_blank({"title": "", "links": [], "controls": [], "visibleTextSample": ""})
    assert not _snapshot_is_blank(
        {"title": "", "links": [], "controls": [], "visibleTextSample": "欢迎进入首页"}
    )


def test_resolve_target_page_reloads_matching_url_when_body_is_blank() -> None:
    page = _FakePlaywrightPage("https://www.zbsykj.com:19096/index", "")
    resolved = asyncio.run(
        _resolve_playwright_target_page(
            _FakePlaywrightBrowser([page]),
            "https://www.zbsykj.com:19096/index",
        )
    )

    assert resolved is page
    assert page.goto_calls == ["https://www.zbsykj.com:19096/index"]


def test_login_residual_text_does_not_block_when_menu_evidence_exists() -> None:
    menu_bundle = {
        "menu_entries": [
            {
                "menu_id": "menu_1",
                "text": "溯源管理",
                "is_leaf": False,
                "status": "expanded",
            },
            {
                "menu_id": "menu_2",
                "text": "线上备案申请",
                "is_leaf": True,
                "status": "discovered",
                "route_hint": "/record/online",
            },
        ]
    }

    assert _should_block_for_login_text("登录 密码 大写锁定已打开", menu_bundle) is False
