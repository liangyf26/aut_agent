from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime import POLICY_ALLOWED, POLICY_BLOCKED, POLICY_NEEDS_REVIEW, PolicyGateDecision  # noqa: E402
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData  # noqa: E402
from tools.suyuan_submit_loop import TEMPLATE_BUNDLE, build_verified_new_application_registry  # noqa: E402


class _FakePage:
    pass


def test_template_actions_are_registered_in_verified_registry() -> None:
    registry = build_verified_new_application_registry()
    actions = [
        str(step.get("action") or "").strip()
        for step in TEMPLATE_BUNDLE.template.get("steps", [])
        if isinstance(step, dict) and str(step.get("action") or "").strip()
    ]

    missing: list[str] = []
    for action_name in actions:
        try:
            registry.get(action_name)
        except KeyError:
            missing.append(action_name)

    assert missing == []


def test_template_actions_match_expected_contract_sensitive_steps() -> None:
    expected_actions_by_step_id = {
        "run_apply_wizard": "run_apply_wizard",
        "select_initial_plant": "select_drawer_option",
        "select_initial_register_type": "select_drawer_option",
        "check_initial_promise": "ensure_drawer_checkbox",
        "submit_initial_form": "expand_cultivation_form",
        "fill_cultivation_template": "fill_success_template",
        "upload_required_files": "upload_drawer_required_files",
        "submit_cultivation_form": "expand_cultivation_form",
        "select_filing_dept": "select_submit_dialog_dept",
        "upload_apply_form": "upload_submit_dialog_apply_file",
        "submit_filing_dialog": "submit_filing_dialog",
    }

    actual_actions_by_step_id = {
        str(step.get("id") or ""): str(step.get("action") or "")
        for step in TEMPLATE_BUNDLE.template.get("steps", [])
        if isinstance(step, dict)
    }

    assert actual_actions_by_step_id == expected_actions_by_step_id


def test_template_action_args_keep_runtime_refs_stable() -> None:
    steps_by_id = {
        str(step.get("id") or ""): step
        for step in TEMPLATE_BUNDLE.template.get("steps", [])
        if isinstance(step, dict)
    }

    assert steps_by_id["select_initial_plant"]["args"] == {
        "label": "备案品种",
        "data_ref": "initial_form.plant_name",
    }
    assert steps_by_id["select_initial_register_type"]["args"] == {
        "label": "备案类型",
        "data_ref": "initial_form.register_type_text",
    }
    assert steps_by_id["check_initial_promise"]["args"] == {
        "label_text": "本人承诺",
    }
    assert steps_by_id["upload_required_files"]["args"] == {
        "files_ref": "generated_files",
    }
    assert steps_by_id["select_filing_dept"]["args"] == {
        "data_ref": "filing_submit.dept_label",
    }
    assert steps_by_id["upload_apply_form"]["args"] == {
        "file_ref": "generated_files.apply_file",
    }


def test_submit_handler_keeps_project_policy_gate(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("submit_filing_dialog")
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.build_submit_action_policy_decision",
        lambda **_: PolicyGateDecision(
            status=POLICY_BLOCKED,
            risk_level="risky_submit",
            reason="blocked for review",
            reason_code="risky_submit_unlisted_blocked",
        ),
    )

    try:
        asyncio.run(
            handler(
                _FakePage(),
                object(),
                runtime,
                {"id": "submit_filing_dialog"},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "policy_blocked: blocked for review"
    else:
        raise AssertionError("expected policy gate to block submit_filing_dialog")


def test_submit_handler_attaches_allowed_policy_decision(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("submit_filing_dialog")
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.build_submit_action_policy_decision",
        lambda **_: PolicyGateDecision(
            status=POLICY_ALLOWED,
            risk_level="risky_submit",
            action_id="submit_filing_dialog",
            reason="allowed by whitelist",
            reason_code="risky_submit_allowed",
            matched_allowlist=True,
        ),
    )

    async def fake_submit_filing_dialog(page: object) -> dict[str, object]:
        assert isinstance(page, _FakePage)
        return {"ok": True, "method": "role"}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.submit_filing_dialog",
        fake_submit_filing_dialog,
    )

    result = asyncio.run(
        handler(
            _FakePage(),
            object(),
            runtime,
            {"id": "submit_filing_dialog"},
        )
    )

    assert result["ok"] is True
    assert result["method"] == "role"
    assert result["policy_decision"]["status"] == POLICY_ALLOWED
    assert result["policy_decision"]["matched_allowlist"] is True


def test_submit_handler_raises_review_error_and_skips_real_submit_when_policy_needs_review(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("submit_filing_dialog")
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})
    decision_calls: list[dict[str, object]] = []
    submit_calls: list[object] = []

    def fake_build_submit_action_policy_decision(**kwargs: object) -> PolicyGateDecision:
        decision_calls.append(dict(kwargs))
        return PolicyGateDecision(
            status=POLICY_NEEDS_REVIEW,
            risk_level="risky_submit",
            action_id=str(kwargs.get("action_id") or ""),
            reason="needs manual approval",
            reason_code="risky_submit_needs_review",
        )

    async def fake_submit_filing_dialog(page: object) -> dict[str, object]:
        submit_calls.append(page)
        return {"ok": True, "method": "role"}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.build_submit_action_policy_decision",
        fake_build_submit_action_policy_decision,
    )
    monkeypatch.setattr(
        "tools.suyuan_submit_loop.submit_filing_dialog",
        fake_submit_filing_dialog,
    )

    try:
        asyncio.run(
            handler(
                _FakePage(),
                object(),
                runtime,
                {"id": "submit_filing_dialog"},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "policy_review_required: needs manual approval"
    else:
        raise AssertionError("expected policy gate to require review for submit_filing_dialog")

    assert decision_calls == [
        {
            "action_id": "submit_filing_dialog",
            "template_name": TEMPLATE_BUNDLE.name,
            "project_name": "AI Agent 软件自动化评测平台第二阶段原型",
        }
    ]
    assert submit_calls == []


def test_fill_success_template_handler_resolves_runtime_ref_to_plain_dict(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("fill_success_template")
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={"cultivation_template": {"crop_name": "番茄", "base_area": "10亩"}},
        generated_files={},
    )

    received_templates: list[dict[str, object]] = []

    async def fake_fill_success_template(page: object, template: dict[str, object]) -> dict[str, object]:
        received_templates.append(template)
        return {"ok": True, "field_count": len(template)}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.fill_success_template",
        fake_fill_success_template,
    )

    result = asyncio.run(
        handler(
            _FakePage(),
            object(),
            runtime,
            {"id": "fill_cultivation_template", "args": {"data_ref": "cultivation_template"}},
        )
    )

    assert received_templates == [{"crop_name": "番茄", "base_area": "10亩"}]
    assert result == {"ok": True, "field_count": 2}


def test_fill_success_template_handler_uses_empty_mapping_when_runtime_ref_is_missing(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("fill_success_template")
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})
    received_templates: list[dict[str, object]] = []

    async def fake_fill_success_template(page: object, template: dict[str, object]) -> dict[str, object]:
        received_templates.append(template)
        return {"ok": True, "field_count": len(template)}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.fill_success_template",
        fake_fill_success_template,
    )

    result = asyncio.run(
        handler(
            _FakePage(),
            object(),
            runtime,
            {"id": "fill_cultivation_template", "args": {"data_ref": "cultivation_template"}},
        )
    )

    assert received_templates == [{}]
    assert result == {"ok": True, "field_count": 0}


def test_fill_success_template_handler_rejects_non_mapping_runtime_data(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("fill_success_template")
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={"cultivation_template": ["not", "a", "mapping"]},
        generated_files={},
    )
    submit_calls: list[object] = []

    async def fake_fill_success_template(page: object, template: dict[str, object]) -> dict[str, object]:
        submit_calls.append(template)
        return {"ok": True}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.fill_success_template",
        fake_fill_success_template,
    )

    try:
        asyncio.run(
            handler(
                _FakePage(),
                object(),
                runtime,
                {"id": "fill_cultivation_template", "args": {"data_ref": "cultivation_template"}},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == (
            "fill_success_template_invalid_data: data_ref=cultivation_template expected mapping, got list"
        )
    else:
        raise AssertionError("expected non-mapping runtime data to be rejected")

    assert submit_calls == []
