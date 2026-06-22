from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.templates import load_template_bundle  # noqa: E402
from prototype.stage2.app.verification.generic_templates import build_generic_template_registry  # noqa: E402
from prototype.stage2.main import run_local_template_validation  # noqa: E402


TEMPLATE_ROOT = ROOT_DIR / "prototype" / "stage2" / "templates"


def test_local_template_validation_writes_shared_verification_result() -> None:
    payload = asyncio.run(run_local_template_validation("lab_navigation"))

    assert payload["success"] is True
    assert payload["status"] == "passed"
    assert payload["family"] == "lab"
    assert payload["system_id"] == "template_lab"
    assert payload["scenario_id"] == "lab_navigation"
    assert payload["mode"] == "local"
    assert "verification_result" in payload
    verification_result = payload["verification_result"]
    assert verification_result["status"] == "passed"
    assert verification_result["rule_evaluation"]["matched_success_rules"]
    assert verification_result["rule_evaluation"]["matched_success_rules"][0]["rule_source"] == "success_rules.ui_texts"

    run_dir = Path(payload["run_dir"])
    persisted = json.loads((run_dir / "verification_result.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "passed"
    assert persisted["rule_evaluation"]["passed"] is True
    assert persisted["evidence"]["final_url"].startswith("file:///")


def test_local_template_validation_supports_locator_hint_backed_query_template() -> None:
    payload = asyncio.run(run_local_template_validation("lab_query_filter"))

    assert payload["success"] is True
    assert payload["status"] == "passed"
    assert payload["feature_point_type"] == "查询"
    assert payload["verification_result"]["rule_evaluation"]["passed"] is True
    fill_step = next(step for step in payload["steps"] if step["step"] == "fill_query_keyword")
    assert fill_step["result"]["locator"] == "#queryInput"
    assert fill_step["result"]["locator_candidates"] == ["#queryInput"]


def test_local_template_validation_supports_locator_hint_backed_create_template() -> None:
    payload = asyncio.run(run_local_template_validation("lab_create_add"))

    assert payload["success"] is True
    assert payload["status"] == "passed"
    assert payload["feature_point_type"] == "新增"
    assert payload["verification_result"]["rule_evaluation"]["passed"] is True
    submit_step = next(step for step in payload["steps"] if step["step"] == "submit_create_form")
    assert submit_step["result"]["locator"] == "#createButton"
    assert submit_step["result"]["locator_candidates"] == ["#createButton"]


def test_real_suyuan_query_reset_template_uses_only_generic_shared_actions() -> None:
    bundle = load_template_bundle(TEMPLATE_ROOT / "suyuan_online_query_reset")
    registry = build_generic_template_registry()
    actions = [
        str(step.get("action") or "").strip()
        for step in bundle.template.get("steps", [])
        if isinstance(step, dict) and str(step.get("action") or "").strip()
    ]

    missing: list[str] = []
    for action_name in actions:
        try:
            registry.get(action_name)
        except KeyError:
            missing.append(action_name)

    assert missing == []
    assert bundle.template["feature_point"]["type"] == "查询"
    assert bundle.template["execution_path"] == "query_reset_online_records"


def test_real_suyuan_detail_view_template_uses_only_generic_shared_actions() -> None:
    bundle = load_template_bundle(TEMPLATE_ROOT / "suyuan_online_detail_view")
    registry = build_generic_template_registry()
    actions = [
        str(step.get("action") or "").strip()
        for step in bundle.template.get("steps", [])
        if isinstance(step, dict) and str(step.get("action") or "").strip()
    ]

    missing: list[str] = []
    for action_name in actions:
        try:
            registry.get(action_name)
        except KeyError:
            missing.append(action_name)

    assert missing == []
    assert bundle.template["feature_point"]["type"] == "查看"
    assert bundle.template["execution_path"] == "view_online_record_detail"
