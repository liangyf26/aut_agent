from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime import POLICY_ALLOWED, PolicyGateDecision  # noqa: E402
from prototype.stage2.app.verification.suyuan_shared_actions import register_suyuan_wizard_drawer_actions  # noqa: E402
from prototype.stage2.app.verification.template_executor import TemplateActionRegistry, TemplateFlowExecutor  # noqa: E402
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData  # noqa: E402
from tools.suyuan_submit_loop import build_verified_new_application_registry  # noqa: E402


class _FakePage:
    pass


def test_run_apply_wizard_substep_failure_marks_execution_failed(monkeypatch) -> None:
    registry = TemplateActionRegistry()
    register_suyuan_wizard_drawer_actions(registry)
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

    async def fake_run_apply_wizard(page: object) -> list[dict[str, object]]:
        assert isinstance(page, _FakePage)
        return [
            {"step": "click_apply_button", "result": {"ok": True}},
            {"step": "click_intro_confirm", "result": {"ok": False, "reason": "missing-button"}},
        ]

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_shared_actions.run_apply_wizard",
        fake_run_apply_wizard,
    )

    executions = asyncio.run(
        TemplateFlowExecutor(registry).execute(
            page=_FakePage(),
            artifacts=object(),
            runtime=runtime,
            template={
                "steps": [
                    {
                        "id": "run_apply_wizard",
                        "action": "run_apply_wizard",
                    }
                ]
            },
        )
    )

    assert len(executions) == 1
    assert executions[0].status == "failed"
    assert executions[0].substeps == [
        {"step": "click_apply_button", "result": {"ok": True}},
        {"step": "click_intro_confirm", "result": {"ok": False, "reason": "missing-button"}},
    ]
    assert executions[0].to_attempt_action()["status"] == "failed"


def test_fill_success_template_handler_reads_runtime_ref_and_passes_mapping(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("fill_success_template")
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={
            "cultivation_template": {
                "rangeStr": "桂A-TEST-001",
                "batchNo": "BATCH-20260621",
                "acceptancePerson": "张三",
            }
        },
        generated_files={},
    )

    captured: list[dict[str, object]] = []

    async def fake_fill_success_template(page: object, template: dict[str, object]) -> dict[str, object]:
        assert isinstance(page, _FakePage)
        captured.append(template)
        return {"ok": True, "form": template}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.fill_success_template",
        fake_fill_success_template,
    )

    result = asyncio.run(
        handler(
            _FakePage(),
            object(),
            runtime,
            {"args": {"data_ref": "cultivation_template"}},
        )
    )

    assert captured == [
        {
            "rangeStr": "桂A-TEST-001",
            "batchNo": "BATCH-20260621",
            "acceptancePerson": "张三",
        }
    ]
    assert result == {
        "ok": True,
        "form": {
            "rangeStr": "桂A-TEST-001",
            "batchNo": "BATCH-20260621",
            "acceptancePerson": "张三",
        },
    }


def test_submit_handler_passes_step_identity_into_policy_bridge(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("submit_filing_dialog")
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

    captured: list[dict[str, object]] = []

    def fake_build_submit_action_policy_decision(**kwargs: object) -> PolicyGateDecision:
        captured.append(dict(kwargs))
        return PolicyGateDecision(
            status=POLICY_ALLOWED,
            risk_level="risky_submit",
            action_id=str(kwargs.get("action_id") or ""),
            template_name=str(kwargs.get("template_name") or ""),
            project_name=str(kwargs.get("project_name") or ""),
            reason="allowed by test fixture",
            reason_code="risky_submit_allowed",
            matched_allowlist=True,
        )

    async def fake_submit_filing_dialog(page: object) -> dict[str, object]:
        assert isinstance(page, _FakePage)
        return {"ok": True, "method": "role"}

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.build_submit_action_policy_decision",
        fake_build_submit_action_policy_decision,
    )
    monkeypatch.setattr(
        "tools.suyuan_submit_loop.submit_filing_dialog",
        fake_submit_filing_dialog,
    )

    result = asyncio.run(
        handler(
            _FakePage(),
            object(),
            runtime,
            {"id": "submit_online_apply_dialog"},
        )
    )

    assert captured == [
        {
            "action_id": "submit_online_apply_dialog",
            "template_name": "suyuan_online_apply",
            "project_name": "AI Agent 软件自动化评测平台第二阶段原型",
        }
    ]
    assert result["ok"] is True
    assert result["policy_decision"]["action_id"] == "submit_online_apply_dialog"
    assert result["policy_decision"]["matched_allowlist"] is True
