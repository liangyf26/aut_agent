from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime import POLICY_ALLOWED, PolicyGateDecision  # noqa: E402
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData  # noqa: E402
from tools.suyuan_submit_loop import build_verified_new_application_registry  # noqa: E402


class _FakePage:
    pass


def test_fill_success_template_handler_uses_runtime_cultivation_template_ref(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("fill_success_template")
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={
            "cultivation_template": {
                "deptId": "dept-001",
                "cityRegionId": "3301",
                "batchNo": "BATCH-20260621",
            }
        },
        generated_files={},
    )
    captured: list[dict[str, object]] = []

    async def fake_fill_success_template(page: object, template: dict[str, object]) -> dict[str, object]:
        captured.append(template)
        return {"ok": True, "batchNo": template.get("batchNo")}

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

    assert captured == [runtime.cultivation_template]
    assert result == {"ok": True, "batchNo": "BATCH-20260621"}


def test_submit_handler_uses_step_id_for_policy_bridge_lookup(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("submit_filing_dialog")
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})
    policy_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "tools.suyuan_submit_loop.build_submit_action_policy_decision",
        lambda **kwargs: policy_calls.append(kwargs)
        or PolicyGateDecision(
            status=POLICY_ALLOWED,
            risk_level="risky_submit",
            action_id=str(kwargs.get("action_id") or ""),
            reason="allowed by whitelist",
            reason_code="risky_submit_allowed",
            matched_allowlist=True,
        ),
    )

    async def fake_submit_filing_dialog(page: object) -> dict[str, object]:
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
            {"id": "submit_custom_branch"},
        )
    )

    assert len(policy_calls) == 1
    assert policy_calls[0]["action_id"] == "submit_custom_branch"
    assert policy_calls[0]["template_name"] == "suyuan_online_apply"
    assert policy_calls[0]["project_name"] == "AI Agent 软件自动化评测平台第二阶段原型"
    assert result["policy_decision"]["action_id"] == "submit_custom_branch"
    assert result["policy_decision"]["matched_allowlist"] is True


def test_fill_success_template_handler_defaults_to_empty_mapping_when_step_omits_data_ref(monkeypatch) -> None:
    registry = build_verified_new_application_registry()
    handler = registry.get("fill_success_template")
    runtime = TemplateRuntimeData(baseline={}, run_data={"cultivation_template": {"batchNo": "ignored"}}, generated_files={})
    captured: list[dict[str, object]] = []

    async def fake_fill_success_template(page: object, template: dict[str, object]) -> dict[str, object]:
        captured.append(template)
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
            {"id": "fill_cultivation_template"},
        )
    )

    assert captured == [{}]
    assert result == {"ok": True, "field_count": 0}
