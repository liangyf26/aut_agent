from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.v3_orchestrator import V3RunConfig  # noqa: E402
from prototype.stage2.app.v3_real_browser import (  # noqa: E402
    TEST_ENV_FULL_ACCESS_POLICY,
    build_menu_discovery_artifacts,
    _infer_feature_type,
    _plan_side_effect_actions,
    _side_effect_execution_result,
)


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
