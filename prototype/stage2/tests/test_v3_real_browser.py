from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.v3_orchestrator import V3RunConfig  # noqa: E402
from prototype.stage2.app.v3_real_browser import (  # noqa: E402
    TEST_ENV_FULL_ACCESS_POLICY,
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
