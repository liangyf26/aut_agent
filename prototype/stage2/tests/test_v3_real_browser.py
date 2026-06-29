from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.v3_orchestrator import V3RunConfig  # noqa: E402
from prototype.stage2.app.v3_real_browser import (  # noqa: E402
    TEST_ENV_FULL_ACCESS_POLICY,
    build_menu_discovery_artifacts,
    _ensure_online_apply_upload_samples,
    _dedupe_page_exploration,
    _browser_use_handover_task,
    _infer_feature_type,
    _merge_browser_use_handover,
    _normalize_page_url,
    _plan_side_effect_actions,
    _resolve_playwright_target_page,
    _target_handover_reasons,
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
    assert _infer_feature_type("我要申请备案", "button", "") == "create"
    assert _infer_feature_type("申请备案", "button", "") == "create"
    assert _infer_feature_type("线上备案申请", "a", "") == "navigation"


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


def test_prioritized_target_triggers_browser_use_handover_when_uncovered() -> None:
    config = V3RunConfig(metadata={"prioritized_targets": ["线上备案申请"]})

    reasons = _target_handover_reasons(
        config,
        menu_entries=[{
            "menu_id": "menu_online_apply",
            "text": "线上备案申请",
            "menu_path": ["备案管理", "线上备案申请"],
            "is_leaf": True,
            "status": "discovered",
        }],
        pages=[{
            "page_id": "menu_page_004",
            "name": "线上备案申请",
            "status": "unreachable",
            "failure_reason": "blank_page_after_navigation",
        }],
        features=[],
    )

    assert reasons == [{
        "target": "线上备案申请",
        "reason": "target_page_uncovered",
        "matched_menu_entry_ids": ["menu_online_apply"],
        "matched_page_ids": ["menu_page_004"],
        "matched_feature_ids": [],
    }]


def test_visible_target_control_does_not_suppress_browser_use_handover() -> None:
    config = V3RunConfig(metadata={"prioritized_targets": ["线上备案申请"]})

    reasons = _target_handover_reasons(
        config,
        menu_entries=[{
            "menu_id": "menu_online_apply",
            "text": "线上备案申请",
            "menu_path": ["备案管理", "线上备案申请"],
            "is_leaf": True,
            "status": "discovered",
        }],
        pages=[{
            "page_id": "menu_page_002",
            "name": "线上备案申请",
            "status": "reachable",
            "url": "https://example.test/record/online",
        }],
        features=[{
            "feature_id": "feature_online_apply_label",
            "page_id": "menu_page_002",
            "name": "线上备案申请",
            "feature_type": "navigation",
            "verification_strategy": "playwright_visible_control",
        }, {
            "feature_id": "feature_apply_button",
            "page_id": "menu_page_002",
            "name": "我要申请备案",
            "feature_type": "view",
            "verification_strategy": "playwright_visible_control",
        }],
    )

    assert reasons == [{
        "target": "线上备案申请",
        "reason": "target_page_uncovered",
        "matched_menu_entry_ids": ["menu_online_apply"],
        "matched_page_ids": ["menu_page_002"],
        "matched_feature_ids": ["feature_online_apply_label"],
    }]


def test_side_effect_policy_gate_target_feature_does_not_suppress_browser_use_handover() -> None:
    config = V3RunConfig(metadata={"prioritized_targets": ["线上备案申请"]})

    reasons = _target_handover_reasons(
        config,
        menu_entries=[{
            "menu_id": "menu_online_apply",
            "text": "线上备案申请",
            "menu_path": ["备案管理", "线上备案申请"],
            "is_leaf": True,
            "status": "discovered",
        }],
        pages=[{
            "page_id": "menu_page_002",
            "name": "线上备案申请",
            "status": "reachable",
            "url": "https://example.test/record/online",
        }],
        features=[{
            "feature_id": "feature_online_apply_label",
            "page_id": "menu_page_002",
            "name": "线上备案申请",
            "feature_type": "create",
            "verification_strategy": "side_effect_policy_gate",
            "source": "playwright.light_interaction",
        }, {
            "feature_id": "feature_apply_button",
            "page_id": "menu_page_002",
            "name": "我要申请备案",
            "feature_type": "create",
            "verification_strategy": "side_effect_policy_gate",
            "source": "playwright.light_interaction",
        }],
    )

    assert reasons == [{
        "target": "线上备案申请",
        "reason": "target_page_uncovered",
        "matched_menu_entry_ids": ["menu_online_apply"],
        "matched_page_ids": ["menu_page_002"],
        "matched_feature_ids": ["feature_online_apply_label"],
    }]


def test_browser_use_handover_task_instructs_full_online_apply_submission() -> None:
    config = V3RunConfig(
        start_url="https://www.zbsykj.com:19096/index",
        safety_policy=TEST_ENV_FULL_ACCESS_POLICY,
        allowed_side_effect_actions=["create", "submit", "save"],
    )

    task = _browser_use_handover_task(
        config,
        ["线上备案申请"],
        [{"target": "线上备案申请", "reason": "target_page_uncovered"}],
    )

    assert "script_prefill_form" in task
    assert "script_upload_sample_files" in task
    assert "script_repair_required_fields" in task
    assert "script_select_required_dropdowns" in task
    assert "script_submit_form" in task
    assert "最终提交一次备案申请" in task


def test_browser_use_handover_task_avoids_seed_propagation_license_branch() -> None:
    task = _browser_use_handover_task(
        V3RunConfig(
            start_url="https://www.zbsykj.com:19096/index",
            safety_policy=TEST_ENV_FULL_ACCESS_POLICY,
            allowed_side_effect_actions=["create", "submit", "save"],
        ),
        ["线上备案申请"],
        [{"target": "线上备案申请", "reason": "target_page_uncovered"}],
    )

    assert "育苗方式" in task
    assert "育苗地点" in task
    assert "三级 cascader" in task
    assert "不要选择“种子繁殖”" in task
    assert "种子采集许可证号" in task
    assert "不要用 evaluate/JS 直接给下拉输入框赋值" in task
    assert "分蘗繁殖" in task or "炼苗" in task or "其他" in task


def test_browser_use_handover_task_tells_agent_not_to_repeat_manual_cascader_clicks() -> None:
    task = _browser_use_handover_task(
        V3RunConfig(
            start_url="https://www.zbsykj.com:19096/index",
            safety_policy=TEST_ENV_FULL_ACCESS_POLICY,
            allowed_side_effect_actions=["create", "submit", "save"],
        ),
        ["线上备案申请"],
        [{"target": "线上备案申请", "reason": "target_page_uncovered"}],
    )

    assert "不要反复手工点击 cascader" in task
    assert "script_prefill_form" in task


def test_prefill_script_clears_element_plus_message_boxes_before_dropdown_clicks() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    prefill_block = source[source.index("async def script_prefill_form") : source.index("async def script_select_required_dropdowns")]

    assert "clear_blocking_overlays" in prefill_block
    assert ".el-message-box__wrapper" in source


def test_prefill_dropdown_selection_prefers_active_form_and_enabled_widgets() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    dropdown_block = source[source.index("async def select_required_online_apply_dropdowns") : source.index("async def prefill_visible_online_apply_fields")]

    assert "active_form_root" in dropdown_block
    assert "item_has_enabled_widget" in dropdown_block
    assert ".el-dialog:not([style*='display: none']), .el-drawer__wrapper:not([style*='display: none'])" in source


def test_online_apply_upload_samples_are_real_named_files() -> None:
    tmp_dir = ROOT_DIR / "prototype" / "stage2" / "tests" / "_tmp_upload_samples"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        samples = _ensure_online_apply_upload_samples(tmp_dir)

        assert set(samples) == {"personnel", "image", "attachment", "acceptance", "application"}
        assert samples["personnel"].name == "人员信息表1.xls"
        assert samples["image"].name == "备案图片01.jpg"
        assert samples["attachment"].name == "附件11.doc"
        assert samples["acceptance"].name == "验收文件00.pdf"
        assert samples["application"].name == "备案申请表.pdf"
        assert samples["personnel"].read_bytes().startswith(b"PK")
        assert samples["image"].read_bytes().startswith(b"\xff\xd8")
        assert samples["attachment"].read_bytes().startswith(b"PK")
        assert samples["acceptance"].read_bytes().startswith(b"%PDF")
        assert samples["application"].read_bytes().startswith(b"%PDF")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_browser_use_handover_defines_deterministic_upload_tool() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")

    assert "async def script_upload_sample_files" in source
    assert "set_input_files" in source
    assert "input[type=file]" in source
    assert "label_text_for_file_input" in source
    assert "育苗人员信息表|人员信息表|人员" in source
    assert "备案图片|图片|照片" in source
    assert "附件|doc|docx|word" in source
    assert "验收文件|验收文件00|验收" in source
    assert '("attachment", samples["attachment"])' in source
    assert "already_uploaded_file_name" in source
    assert '{"personnel", "image", "attachment", "acceptance"}' in source
    assert "input_count" in source


def test_browser_use_handover_defines_required_field_repair_tool() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    task = _browser_use_handover_task(
        V3RunConfig(
            start_url="https://www.zbsykj.com:19096/index",
            safety_policy=TEST_ENV_FULL_ACCESS_POLICY,
            allowed_side_effect_actions=["create", "submit", "save"],
        ),
        ["线上备案申请"],
        [{"target": "线上备案申请", "reason": "target_page_uncovered"}],
    )

    assert "async def script_repair_required_fields" in source
    assert "repair_online_apply_required_fields" in source
    assert "育苗开始日期" in source
    assert "验收日期" in source
    assert "验收监管单位" in source
    assert "验收文件" in source
    assert "不要再自行编写 evaluate/JS" in task


def test_online_apply_dropdown_prefill_skips_already_selected_controls() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    dropdown_block = source[source.index("async def select_required_online_apply_dropdowns") : source.index("async def prefill_visible_online_apply_fields")]

    assert "already_has_selected_value" in dropdown_block
    assert "already_selected" in dropdown_block


def test_online_apply_required_field_repair_targets_acceptance_unit_not_record_unit() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    repair_block = source[source.index("async def repair_online_apply_required_fields") : source.index("async def prefill_visible_online_apply_fields")]

    assert "/备案监管单位/" not in repair_block
    assert "setItemInput([/验收监管单位/]" not in repair_block
    assert '"acceptanceUnit": await choose_dropdown(' in source
    assert 'label_texts=["验收监管单位"]' in source
    assert 'label_texts: list[str] | None = None' in source


def test_online_apply_prefill_avoids_text_filling_for_dropdown_date_and_cascader_fields() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    prefill_block = source[source.index("async def prefill_visible_online_apply_fields") : source.index("@tools.action(description=\"[脚本内部工具] 预填写表单")]

    assert "return '测试监管单位'" not in prefill_block
    assert "return '2026-06-01'" not in prefill_block
    assert "生产地点|地址|详细地址" not in prefill_block
    assert "const isDateInput = Boolean(el.closest('.el-date-editor'))" in prefill_block
    assert "const isWidgetInput = Boolean(el.closest('.el-select,.el-cascader'))" in prefill_block


def test_online_apply_prefill_avoids_unknown_text_for_labeled_dropdown_controls() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    prefill_block = source[source.index("async def prefill_visible_online_apply_fields") : source.index("@tools.action(description=\"[脚本内部工具] 预填写表单")]

    assert "const looksLikeChoiceField" in prefill_block
    assert "验收监管单位" in prefill_block
    assert "备案品种" in prefill_block
    assert "if (el.disabled || !el.offsetParent || isWidgetInput || isDateInput || isChoiceField || el.readOnly)" in prefill_block


def test_online_apply_date_repair_uses_picker_selection_instead_of_text_fill() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    repair_block = source[source.index("async def repair_online_apply_required_fields") : source.index("async def prefill_visible_online_apply_fields")]

    assert "async def repair_required_dates" in repair_block
    assert "await input_locator.fill(value" not in repair_block
    assert "await input_locator.type(value" not in repair_block
    assert "setItemInput([/育苗开始日期/, /开始日期/], '2026-06-01', 'dates');" not in repair_block
    assert "setItemInput([/验收日期/], '2026-06-15', 'dates');" not in repair_block
    assert ".el-picker-panel" in repair_block
    assert "await day_cell.click(timeout=2500)" in repair_block


def test_online_apply_date_repair_skips_dates_that_already_have_values() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    repair_block = source[source.index("async def repair_online_apply_required_fields") : source.index("async def prefill_visible_online_apply_fields")]

    assert "existing_value = _text(await input_locator.input_value(timeout=800))" in repair_block
    assert '"skipped": "already_selected"' in repair_block
    assert "if existing_value:" in repair_block


def test_online_apply_repair_block_defines_its_own_visible_text_helper() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    repair_block = source[source.index("async def repair_online_apply_required_fields") : source.index("async def prefill_visible_online_apply_fields")]

    assert "async def visible_text(locator: Any) -> str:" in repair_block
    assert "return _text(await locator.inner_text(timeout=800))" in repair_block


def test_online_apply_seedling_address_uses_exact_cascader_selection() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    dropdown_block = source[source.index("async def select_required_online_apply_dropdowns") : source.index("async def repair_online_apply_required_fields")]

    assert "label_texts: list[str] | None = None" in dropdown_block
    assert 'label_texts=["育苗地址", "育苗地点"]' in dropdown_block
    assert 're.compile(r"育苗地点|育苗区域|育苗地址|种植地点|育苗.*地区|location|area", re.I)' not in dropdown_block
    assert "max_depth: int = 3" in dropdown_block
    assert ".el-cascader-panel .el-cascader-menu" in dropdown_block
    assert "if depth < max_depth - 1 and not await next_menu.count():" in dropdown_block
    assert '"reason": "terminal_level_not_reached"' in dropdown_block


def test_online_apply_acceptance_unit_uses_exact_dropdown_label_and_select_triggers() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    dropdown_block = source[source.index("async def select_required_online_apply_dropdowns") : source.index("async def repair_online_apply_required_fields")]

    assert 'label_texts=["验收监管单位"]' in dropdown_block
    assert "input[placeholder*='请选择']" in dropdown_block
    assert '.el-select .el-input__wrapper' in dropdown_block


def test_browser_use_handover_mentions_four_upload_slots_with_matching_files() -> None:
    task = _browser_use_handover_task(
        V3RunConfig(
            start_url="https://www.zbsykj.com:19096/index",
            safety_policy=TEST_ENV_FULL_ACCESS_POLICY,
            allowed_side_effect_actions=["create", "submit", "save"],
        ),
        ["线上备案申请"],
        [{"target": "线上备案申请", "reason": "target_page_uncovered"}],
    )

    assert "4 处上传" in task
    assert "人员信息表 xls" in task
    assert "备案图片 jpg" in task
    assert "附件 doc" in task
    assert "验收文件 pdf" in task


def test_browser_use_handover_defines_final_record_dialog_tool_without_native_file_picker() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    task = _browser_use_handover_task(
        V3RunConfig(
            start_url="https://www.zbsykj.com:19096/index",
            safety_policy=TEST_ENV_FULL_ACCESS_POLICY,
            allowed_side_effect_actions=["create", "submit", "save"],
        ),
        ["线上备案申请"],
        [{"target": "线上备案申请", "reason": "target_page_uncovered"}],
    )

    assert "async def script_complete_final_record_dialog" in source
    assert "complete_online_apply_final_record_dialog" in source
    assert "备案申请表.pdf" in source
    assert "set_input_files" in source
    assert "不要点击弹窗里的“上传文件”按钮" in task
    assert "script_complete_final_record_dialog" in task


def test_online_apply_upload_mapping_prefers_pdf_for_acceptance_and_doc_for_attachment() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    upload_block = source[source.index("async def upload_online_apply_sample_files") : source.index("@tools.action(description=\"[脚本内部工具] 上传线上备案申请 4 处所需样本文件")]

    assert 'return "attachment", samples["attachment"]' in upload_block
    assert 'return "acceptance", samples["acceptance"]' in upload_block
    assert 're.search(r"附件|doc|docx|word", hint, re.I)' in upload_block
    assert 're.search(r"验收文件|验收文件00|验收", hint, re.I)' in upload_block
    assert '("attachment", samples["attachment"])' in upload_block
    assert '("acceptance", samples["acceptance"])' in upload_block


def test_online_apply_upload_mapping_checks_acceptance_before_generic_attachment_tokens() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    upload_block = source[source.index("def sample_for(") : source.index("uploaded: list[dict[str, Any]] = []")]

    acceptance_pos = upload_block.index('if re.search(r"验收文件|验收文件00|验收", hint, re.I):')
    attachment_pos = upload_block.index('if re.search(r"附件|doc|docx|word", hint, re.I):')
    personnel_pos = upload_block.index('if re.search(r"育苗人员信息表|人员信息表|人员|xls|xlsx|excel|表格", hint, re.I):')
    assert acceptance_pos < attachment_pos
    assert attachment_pos < personnel_pos


def test_online_apply_upload_label_extraction_prefers_field_label_over_helper_text() -> None:
    source = Path("prototype/stage2/app/v3_real_browser.py").read_text(encoding="utf-8")
    upload_block = source[source.index("async def upload_online_apply_sample_files") : source.index("@tools.action(description=\"[脚本内部工具] 上传线上备案申请 4 处所需样本文件")]

    assert "querySelector('.el-form-item__label')" in upload_block
    assert "querySelector('.title')" in upload_block
    assert "querySelector('.el-upload__text')" not in upload_block


def test_browser_use_handover_payload_merges_into_unified_artifacts() -> None:
    page_bundle = {
        "pages": [],
        "features": [],
        "page_exploration_log": [],
        "screenshots_index": {"schema_version": "stage2_v3_run.v1", "screenshots": [], "items": []},
    }
    handover = {
        "status": "completed",
        "pages": [{"page_id": "browser_use_target_001", "name": "线上备案申请"}],
        "features": [{
            "feature_id": "browser_use_target_001_flow",
            "page_id": "browser_use_target_001",
            "name": "线上备案申请目标接管流程",
        }],
        "case_execution_results": [{
            "case_id": "browser_use_target_001_flow_case",
            "feature_id": "browser_use_target_001_flow",
            "status": "passed",
        }],
        "page_exploration_log": [{"event": "browser_use_handover", "status": "completed"}],
        "screenshots_index": {
            "schema_version": "stage2_v3_run.v1",
            "screenshots": [{"screenshot_id": "browser_use_target_001"}],
            "items": [{"screenshot_id": "browser_use_target_001"}],
        },
    }

    merged = _merge_browser_use_handover(page_bundle, handover)

    assert [page["page_id"] for page in merged["pages"]] == ["browser_use_target_001"]
    assert [feature["feature_id"] for feature in merged["features"]] == [
        "browser_use_target_001_flow"
    ]
    assert merged["case_execution_results"][0]["case_id"] == "browser_use_target_001_flow_case"
    assert merged["page_exploration_log"][0]["event"] == "browser_use_handover"
    assert merged["screenshots_index"]["items"][0]["screenshot_id"] == "browser_use_target_001"


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
