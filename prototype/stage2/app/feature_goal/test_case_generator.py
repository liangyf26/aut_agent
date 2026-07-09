"""
Test case generator for Stage D.

Generates executable test cases for low-risk features and
entry confirmation cases for high-risk features.
"""

from typing import Literal

from .feature_classifier import should_generate_executable_test

TestCaseType = Literal["executable", "entry_confirmation"]

# Feature types whose "low/medium risk" click still produces a real side
# effect (file write, server-side export job, audit-logged download) even
# though they are not row-mutation actions. These must require approval
# despite not being risk_level="high" — see Stage D adversarial review
# Finding: "export gets an executable, no-approval test that clicks a real
# download control".
_SIDE_EFFECTING_FEATURE_TYPES = frozenset({"export"})


def generate_test_case(
    feature_id: str,
    page_id: str,
    feature_type: str,
    risk_level: str,
    confidence: str,
    element_text: str | None = None,
    element_locator: str | None = None,
    locator_candidates: list[dict] | None = None,
    page_url: str | None = None,
    safety_policy: str = "low_risk_only",
) -> dict:
    """
    Generate a test case for a feature point.

    Low-risk features → executable test cases with steps
    High-risk features → entry confirmation cases requiring approval

    Args:
        feature_id: Feature identifier
        page_id: Page identifier
        feature_type: Feature type (query, reset, detail, etc.)
        risk_level: Risk level (low, medium, high, none)
        confidence: Classification confidence
        element_text: Element text
        element_locator: Element locator/selector
        locator_candidates: Ranked locator candidates (L2 pool, P0-1)
        page_url: Page URL

    Returns:
        Test case dict with type, steps, and metadata
    """
    test_case_id = f"tc_{feature_id}"

    # View-only features → no executable test needed
    if risk_level == "none":
        return {
            "test_case_id": test_case_id,
            "feature_id": feature_id,
            "page_id": page_id,
            "type": "view_only",
            "risk_level": risk_level,
            "requires_approval": False,
            "description": f"页面可见性验证",
            "metadata": {
                "feature_type": feature_type,
                "confidence": confidence,
                "locator_candidates": locator_candidates,
            },
        }

    # should_generate_executable_test() is the SINGLE source of truth for the
    # executable-vs-confirmation decision (risk_level=="high", OR low/medium
    # confidence — see feature_classifier.py). Previously this function had
    # its own, divergent risk_level=="high" check that ignored confidence
    # entirely, leaving should_generate_executable_test as unreachable dead
    # code (Stage D adversarial review finding). export is additionally
    # forced to entry_confirmation even at low/medium risk because clicking
    # it has a real, non-idempotent side effect (file write / server-side
    # export job), unlike a read-only query/reset/detail/tab action.
    needs_confirmation = (
        not should_generate_executable_test(risk_level, confidence)
        and safety_policy != "test_env_full_access"
        or (feature_type in _SIDE_EFFECTING_FEATURE_TYPES and safety_policy != "test_env_full_access")
    )

    if needs_confirmation:
        return {
            "test_case_id": test_case_id,
            "feature_id": feature_id,
            "page_id": page_id,
            "type": "entry_confirmation",
            "risk_level": risk_level,
            "requires_approval": True,
            "warning": f"高风险操作：{feature_type} - 需要人工确认"
            if risk_level == "high"
            else f"{feature_type} 可能产生真实副作用（如文件下载）- 需要人工确认",
            "description": f"确认 {element_text or feature_type} 功能入口可见",
            "metadata": {
                "feature_type": feature_type,
                "confidence": confidence,
                "element_text": element_text,
                "element_locator": element_locator,
                "locator_candidates": locator_candidates,
            },
        }

    # Low/medium risk, sufficient confidence, non-side-effecting → executable
    steps = _generate_test_steps(
        feature_type=feature_type,
        element_text=element_text,
        element_locator=element_locator,
        locator_candidates=locator_candidates,
        page_url=page_url,
    )

    return {
        "test_case_id": test_case_id,
        "feature_id": feature_id,
        "page_id": page_id,
        "type": "executable",
        "risk_level": risk_level,
        "requires_approval": False,
        "steps": steps,
        "expected_result": _get_expected_result(feature_type, element_text),
        "description": f"测试 {element_text or feature_type} 功能",
        "metadata": {
            "feature_type": feature_type,
            "confidence": confidence,
            "element_text": element_text,
            "element_locator": element_locator,
            "locator_candidates": locator_candidates,
        },
    }


def _generate_test_steps(
    feature_type: str,
    element_text: str | None,
    element_locator: str | None,
    locator_candidates: list[dict] | None,
    page_url: str | None,
) -> list[dict]:
    """
    Generate test steps based on feature type.

    Args:
        feature_type: Feature type
        element_text: Element text
        element_locator: Element locator
        locator_candidates: Ranked locator candidates (L2 pool, P0-1)
        page_url: Page URL

    Returns:
        List of test step dicts
    """
    locator = element_locator or _default_locator(feature_type, element_text)

    if feature_type == "query":
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "fill",
                "target": "input[type='text']:visible, input:not([type]):visible, textarea:visible",
                "value": "测试",
                "description": "填写查询条件",
            },
            {
                "step": 3,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击{element_text or '查询'}按钮",
            },
            {
                "step": 4,
                "action": "wait_for",
                "target": "table, .result-list, .data-grid",
                "description": "等待查询结果显示",
            },
        ]

    elif feature_type == "reset":
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "fill",
                "target": "input[type='text']:visible, input:not([type]):visible, textarea:visible",
                "value": "测试",
                "description": "填写查询条件",
            },
            {
                "step": 3,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击{element_text or '重置'}按钮",
            },
            {
                "step": 4,
                "action": "verify",
                "target": "input[type='text']:visible, input:not([type]):visible, textarea:visible",
                "expected": "",
                "description": "验证表单已清空",
            },
        ]

    elif feature_type == "detail":
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击{element_text or '详情'}按钮",
            },
            {
                "step": 3,
                "action": "wait_for",
                "target": ".detail-panel, .modal, [role='dialog']",
                "description": "等待详情页/弹窗显示",
            },
        ]

    elif feature_type == "export":
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击{element_text or '导出'}按钮",
            },
            {
                "step": 3,
                "action": "verify",
                "target": "download_started",
                "description": "验证下载已开始（需人工确认）",
            },
        ]

    elif feature_type == "tab":
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击Tab: {element_text}",
            },
            {
                "step": 3,
                "action": "verify",
                "target": "[aria-selected='true']",
                "description": "验证Tab已激活",
            },
        ]

    elif feature_type == "dialog":
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击{element_text or '弹窗'}按钮",
            },
            {
                "step": 3,
                "action": "wait_for",
                "target": "[role='dialog'], .modal",
                "description": "等待弹窗显示",
            },
        ]

    elif feature_type in {"form_field", "form_select", "number_input", "date_picker", "cascader"}:
        placeholder = {"form_field": "测试文本", "form_select": "1", "number_input": "100",
                       "date_picker": "2026-01-01", "cascader": "1"}.get(feature_type, "测试")
        return [
            {"step": 1, "action": "navigate", "target": page_url or "/", "description": "导航到页面"},
            {"step": 2, "action": "fill", "target": locator, "locator_candidates": locator_candidates,
             "value": placeholder, "description": f"填写{element_text or feature_type}"},
        ]

    elif feature_type == "submit":
        return [
            {"step": 1, "action": "navigate", "target": page_url or "/", "description": "导航到页面"},
            {"step": 2, "action": "click", "target": locator, "locator_candidates": locator_candidates,
             "description": f"点击{element_text or '提交'}按钮"},
            {"step": 3, "action": "verify", "target": "page_state_changed",
             "description": "验证提交后页面状态变化"},
        ]

    else:
        # Generic steps for unknown types
        return [
            {
                "step": 1,
                "action": "navigate",
                "target": page_url or "/",
                "description": "导航到页面",
            },
            {
                "step": 2,
                "action": "click",
                "target": locator,
                "locator_candidates": locator_candidates,
                "description": f"点击{element_text or feature_type}",
            },
            {
                "step": 3,
                "action": "verify",
                "target": "page_state_changed",
                "description": "验证页面状态变化",
            },
        ]


def _default_locator(feature_type: str, element_text: str | None) -> str:
    text = element_text or feature_type
    type_map = {
        "form_field": f"input[type='text']:visible, input:not([type]):visible, textarea:visible",
        "form_select": f"select:visible",
        "number_input": f"input[type='number']:visible",
        "date_picker": f"input[type='date']:visible, input[type='datetime-local']:visible",
        "cascader": f"input[placeholder*='{text}']:visible" if text else "input:visible",
        "file_upload": f"input[type='file']:visible",
        "submit": f"button:has-text('{text}'), input[type='submit'][value*='{text}']" if text else "button:visible",
    }
    return type_map.get(feature_type, f"button:has-text('{text}')") if text else type_map.get(feature_type, "button:visible")


def _get_expected_result(feature_type: str, element_text: str | None) -> str:
    """
    Get expected result description for a feature type.

    Args:
        feature_type: Feature type
        element_text: Element text

    Returns:
        Expected result description
    """
    expectations = {
        "query": "查询结果正确显示",
        "reset": "表单字段已清空",
        "detail": "详情信息正确显示",
        "export": "文件下载成功",
        "tab": "Tab内容正确切换",
        "dialog": "弹窗正确显示",
    }

    return expectations.get(feature_type, f"{element_text or feature_type}功能正常")
