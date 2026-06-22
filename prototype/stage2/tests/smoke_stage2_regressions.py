from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.iteration.writer import write_iteration_artifacts
from tools.suyuan_submit_loop import build_human_resume_decision, build_human_takeover_packet
from prototype.stage2.app.config.capability_preflight import CapabilityGateDecision
from prototype.stage2.app.config.capability_routing import build_capability_routing
from prototype.stage2.app.config.models import ModelProfile
from prototype.stage2.app.config.run_policy_loader import resolve_run_policy_payload
from prototype.stage2.app.reporting import build_routing_section


def test_should_auto_continue_only_for_scheduled_true() -> None:
    from tools.suyuan_submit_loop import should_auto_continue_next_round

    assert should_auto_continue_next_round(
        {
            "status": "scheduled",
            "should_start_next_round": True,
        }
    )
    assert not should_auto_continue_next_round(
        {
            "status": "scheduled",
            "should_start_next_round": False,
        }
    )
    assert not should_auto_continue_next_round(
        {
            "status": "needs_review",
            "should_start_next_round": True,
        }
    )
    assert not should_auto_continue_next_round(
        {
            "status": "stopped",
            "should_start_next_round": False,
        }
    )


def test_discovery_strategy_smoke_guardrail_preserves_skip_completed_as_hint_not_override() -> None:
    from prototype.stage2.app.config.capability_routing import CapabilityRoutingDecision, CapabilityStageRoute
    from prototype.stage2.app.discovery.strategy import select_discovery_strategy

    discovery_route = CapabilityStageRoute(
        stage="discovery",
        allowed=True,
        recommended_mode="browser_use_structured_candidate",
        reason_code="browser_use_structured_candidate",
        reason="Structured discovery may be possible.",
        routing_tags=["discovery_enabled", "structured_candidate"],
        capability_tags={"chat_completion": True, "json_schema_response_format": True},
    )
    routing = CapabilityRoutingDecision(
        profile_name="Qwen",
        model="Qwen",
        gate_status="ok",
        gate_reason_code="capability_probe_ok",
        gate_reason="Capability preflight passed.",
        capability_tags={"chat_completion": True, "json_schema_response_format": True},
        routing_tags=list(discovery_route.routing_tags),
        discovery=discovery_route,
        verification=CapabilityStageRoute(
            stage="verification",
            allowed=True,
            recommended_mode="playwright_deterministic_verification",
            reason_code="playwright_verification_ready",
            reason="Verification is allowed.",
            routing_tags=["verification_enabled"],
            capability_tags={"chat_completion": True},
        ),
        reporting=CapabilityStageRoute(
            stage="reporting",
            allowed=True,
            recommended_mode="llm_assisted_reporting",
            reason_code="llm_reporting_ready",
            reason="Reporting is allowed.",
            routing_tags=["reporting_enabled"],
            capability_tags={"chat_completion": True},
        ),
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={"skip_completed_discovery": True},
        has_completed_discovery=False,
        allow_live_enrichment=True,
    )

    assert decision.selected_strategy == "live_enrich"
    assert decision.reuse_completed_discovery is False
    assert decision.should_run_live_discovery is True
    assert "no completed discovery artifacts are available" in " ".join(decision.notes)


def test_discovery_strategy_smoke_guardrail_keeps_template_seed_when_live_enrichment_is_disabled() -> None:
    from prototype.stage2.app.config.capability_routing import CapabilityRoutingDecision, CapabilityStageRoute
    from prototype.stage2.app.discovery.strategy import select_discovery_strategy

    discovery_route = CapabilityStageRoute(
        stage="discovery",
        allowed=True,
        recommended_mode="browser_use_structured_candidate",
        reason_code="browser_use_structured_candidate",
        reason="Structured discovery may be possible.",
        routing_tags=["discovery_enabled", "structured_candidate"],
        capability_tags={"chat_completion": True, "json_schema_response_format": True},
    )
    routing = CapabilityRoutingDecision(
        profile_name="Qwen",
        model="Qwen",
        gate_status="ok",
        gate_reason_code="capability_probe_ok",
        gate_reason="Capability preflight passed.",
        capability_tags={"chat_completion": True, "json_schema_response_format": True},
        routing_tags=list(discovery_route.routing_tags),
        discovery=discovery_route,
        verification=CapabilityStageRoute(
            stage="verification",
            allowed=True,
            recommended_mode="playwright_deterministic_verification",
            reason_code="playwright_verification_ready",
            reason="Verification is allowed.",
            routing_tags=["verification_enabled"],
            capability_tags={"chat_completion": True},
        ),
        reporting=CapabilityStageRoute(
            stage="reporting",
            allowed=True,
            recommended_mode="llm_assisted_reporting",
            reason_code="llm_reporting_ready",
            reason="Reporting is allowed.",
            routing_tags=["reporting_enabled"],
            capability_tags={"chat_completion": True},
        ),
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={"skip_completed_discovery": True},
        has_completed_discovery=False,
        allow_live_enrichment=False,
    )

    assert decision.selected_strategy == "template_seed_only"
    assert decision.reuse_completed_discovery is False
    assert decision.should_run_live_discovery is False
    assert decision.reason_code == "live_enrichment_disabled"


def test_stage_b_routing_and_policy_explanation_remains_structured() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_file = root / "demo.env"
        env_file.write_text("LOCAL_LLM_MODEL=AI-tester\n", encoding="utf-8")
        profile = ModelProfile(
            name="AI-tester",
            env_file=env_file,
            base_url="http://localhost:30000/v1",
            api_key="test",
            model="AI-tester",
        )
        gate = CapabilityGateDecision(
            status="allowed",
            reason_code="capability_probe_ok",
            reason="Capability probe for model AI-tester satisfies mode stage2_run_sample.",
            mode="stage2_run_sample",
            profile_name=profile.name,
            required_tags=["chat_completion"],
            capability_tags={"chat_completion": True},
            notes=["Current routing mode only requires the tags listed in required_tags."],
        )
        routing = build_capability_routing(profile, gate=gate)
        policy = resolve_run_policy_payload(
            {
                "run_policy": {
                    "risky_submit_default_decision": "blocked",
                    "projects": {
                        "AI Agent 软件自动化评测平台第二阶段原型": {
                            "templates": {
                                "suyuan_online_apply": {
                                    "whitelist": [
                                        {
                                            "action_id": "submit_online_apply_dialog",
                                            "decision": "allowed",
                                            "risk_level": "risky_submit",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            project_name="AI Agent 软件自动化评测平台第二阶段原型",
            template_name="suyuan_online_apply",
            policy_path=root / "run_policy.json",
            source_name="fixture",
        )
        section = build_routing_section(
            None,
            capability_decision=gate.to_dict(),
            route_decision={
                "status": "degraded",
                "model_name": profile.name,
                "requested_mode": "browser_use_chatopenai_structured",
                "selected_mode": routing.verification.recommended_mode if routing.verification else "stage2_run_sample",
                "assigned_role": "verification",
                "fallback_mode": routing.discovery.recommended_mode if routing.discovery else None,
                "reason": "Structured Browser Use is unavailable, so the profile falls back to deterministic verification.",
            },
            policy_decision={
                "status": "allowed",
                "action_id": "submit_online_apply_dialog",
                "action_name": "Submit online apply dialog",
                "risk_level": "risky_submit",
                "reason": "Action was explicitly resolved by the project allowlist.",
                "policy_source": policy.allow_rules[0].source if policy.allow_rules else "unknown",
                "matched_allowlist": True,
                "requires_allowlist": True,
            },
        )

        assert section.title == "Routing Explanation"
        assert section.extra["degraded"] is True
        assert any(fact.label == "policy_status" and fact.value == "allowed" for fact in section.facts)
        assert any(item.item_id == "capability-routing" for item in section.items)


def test_artifact_writer_avoids_same_second_name_collision() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        first = ArtifactWriter(root, "sample_run")
        second = ArtifactWriter(root, "sample_run")

        assert first.run_dir != second.run_dir
        assert first.run_dir.exists()
        assert second.run_dir.exists()
        assert second.run_dir.name.startswith(first.run_dir.name) or second.run_dir.name.endswith("_01")


def test_iteration_writer_persists_round_input() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_120000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        round_input = {
            "orchestration_stream_id": "tpl::modelA",
            "template_name": "tpl",
            "model_name": "modelA",
            "project_name": "proj",
            "round_index": 2,
            "previous_run_id": "20260620_115500_modelA",
            "scheduled_cluster_ids": ["cluster-001"],
            "scheduled_action_ids": ["retry-001"],
            "execution_hints": {"ui_retry_mode": "refresh_locator_and_rerun"},
        }
        write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 2,
                },
                "failure_items": [{"name": "locator failure", "status": "failed", "summary": "selector not found"}],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed"},
            attempts=[{"attempt_id": "a1", "status": "failed", "classification": "ui", "message": "selector not found"}],
            max_attempts=3,
            round_input=round_input,
        )
        persisted = json.loads((run_dir / "round_input.json").read_text(encoding="utf-8"))
        assert persisted["round_index"] == 2
        assert persisted["scheduled_cluster_ids"] == ["cluster-001"]
        assert persisted["execution_hints"]["ui_retry_mode"] == "refresh_locator_and_rerun"


def test_previous_iteration_lookup_requires_matching_stream() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        current = root / "20260620_120200_modelA"
        previous_match = root / "20260620_120100_modelA"
        previous_other = root / "20260620_120150_modelA"
        for path in (current, previous_match, previous_other):
            path.mkdir(parents=True, exist_ok=True)
        common_report = {
            "summary": {
                "status": "failed",
                "project_name": "proj",
                "template_name": "tpl",
            },
            "failure_items": [{"name": "runtime", "status": "failed", "summary": "boom"}],
        }
        write_iteration_artifacts(
            previous_match,
            run_report={**common_report, "summary": {**common_report["summary"], "run_id": previous_match.name}},
            status_snapshot={"run_id": previous_match.name, "overall_status": "failed"},
            attempts=[{"attempt_id": "a1", "status": "failed", "classification": "ui", "message": "boom"}],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 1,
            },
        )
        write_iteration_artifacts(
            previous_other,
            run_report={**common_report, "summary": {**common_report["summary"], "run_id": previous_other.name}},
            status_snapshot={"run_id": previous_other.name, "overall_status": "failed"},
            attempts=[{"attempt_id": "a1", "status": "failed", "classification": "ui", "message": "boom"}],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelB",
                "template_name": "tpl",
                "model_name": "modelB",
                "project_name": "proj",
                "round_index": 1,
            },
        )
        artifacts = write_iteration_artifacts(
            current,
            run_report={**common_report, "summary": {**common_report["summary"], "run_id": current.name}},
            status_snapshot={"run_id": current.name, "overall_status": "failed"},
            attempts=[{"attempt_id": "a2", "status": "failed", "classification": "ui", "message": "boom"}],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 2,
                "execution_hints": {
                    "focus_stage": "verification",
                    "scheduled_strategies": ["inspect_validation_and_rerun"],
                    "validation_retry_mode": "inspect_visible_errors",
                    "scheduled_clusters": [
                        {
                            "cluster_id": "cluster-001",
                            "category": "front_validation",
                            "stage": "verification",
                            "strategy": "inspect_validation_and_rerun",
                            "owner": "agent",
                            "action_level": "agent",
                        }
                    ],
                },
            },
        )
        assert artifacts.iteration_comparison is not None
        assert artifacts.iteration_comparison.previous_run_id == previous_match.name


def test_structured_permission_failure_hits_safety_boundary_stop() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_130000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 1,
                },
                "failure_items": [{"name": "account_policy_block", "status": "failed", "summary": "账号缺少机构信息"}],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed"},
            attempts=[
                {
                    "attempt_id": "a1",
                    "status": "failed",
                    "classification": {
                        "category": "account_policy_block",
                        "reason": "账号缺少新增备案所需机构信息，新增分支被后台拒绝",
                    },
                    "message": "账号缺少新增备案所需机构信息，新增分支被后台拒绝",
                }
            ],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 1,
            },
        )
        assert artifacts.stop_conditions is not None
        assert "safety_boundary" in artifacts.stop_conditions.triggered_conditions
        assert artifacts.next_round_decision is not None
        assert artifacts.next_round_decision.status == "stopped"


def test_policy_gate_failure_hits_safety_boundary_stop() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_130500_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 1,
                },
                "failure_items": [{"name": "policy_blocked", "status": "failed", "summary": "高风险真实提交未在白名单中显式允许"}],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed"},
            attempts=[
                {
                    "attempt_id": "a1",
                    "status": "failed",
                    "classification": {
                        "category": "policy_blocked",
                        "reason": "高风险真实提交未在项目级白名单中显式允许，执行层已阻断提交动作",
                    },
                    "message": "高风险真实提交未在项目级白名单中显式允许，执行层已阻断提交动作",
                }
            ],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 1,
            },
        )
        assert artifacts.stop_conditions is not None
        assert "safety_boundary" in artifacts.stop_conditions.triggered_conditions
        assert artifacts.next_round_decision is not None
        assert artifacts.next_round_decision.status == "stopped"


def test_structured_workflow_branch_requests_manual_review() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_131000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 2,
                },
                "failure_items": [{"name": "pending_payment_modify_mode", "status": "failed", "summary": "需走待支付记录分支"}],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed"},
            attempts=[
                {
                    "attempt_id": "a2",
                    "status": "failed",
                    "classification": {
                        "category": "pending_payment_modify_mode",
                        "reason": "已进入待支付记录的修改态，需走提交申请/支付分支而非 update",
                    },
                    "message": "已进入待支付记录的修改态，需走提交申请/支付分支而非 update",
                }
            ],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 2,
                "execution_hints": {
                    "focus_stage": "verification",
                    "scheduled_strategies": ["inspect_validation_and_rerun"],
                    "validation_retry_mode": "inspect_visible_errors",
                    "scheduled_clusters": [
                        {
                            "cluster_id": "cluster-001",
                            "category": "front_validation",
                            "stage": "verification",
                            "strategy": "inspect_validation_and_rerun",
                            "owner": "agent",
                            "action_level": "agent",
                        }
                    ],
                },
            },
        )
        assert artifacts.stop_conditions is not None
        condition_types = {item.condition_type: item for item in artifacts.stop_conditions.conditions}
        assert "manual_takeover" in condition_types
        assert condition_types["manual_takeover"].status == "manual_review_needed"
        assert artifacts.next_round_decision is not None
        assert artifacts.next_round_decision.status == "needs_review"


def test_front_validation_retry_action_exposes_validation_execution_hints() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_132000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 2,
                },
                "failure_items": [
                    {
                        "name": "front_validation_missing_commitment",
                        "status": "failed",
                        "summary": "前端校验提示未勾选承诺书，需先补齐可见必填项",
                    }
                ],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed", "stage": "verification"},
            attempts=[
                {
                    "attempt_id": "a3",
                    "status": "failed",
                    "classification": {
                        "category": "front_validation_missing_commitment",
                        "reason": "页面显示必填校验，需补齐承诺书后再重试",
                    },
                    "stage": "verification",
                    "message": "页面显示必填校验，需补齐承诺书后再重试",
                }
            ],
            max_attempts=4,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 2,
                "execution_hints": {
                    "focus_stage": "verification",
                    "scheduled_strategies": ["inspect_validation_and_rerun"],
                    "validation_retry_mode": "inspect_visible_errors",
                    "scheduled_clusters": [
                        {
                            "cluster_id": "cluster-001",
                            "category": "front_validation",
                            "stage": "verification",
                            "strategy": "inspect_validation_and_rerun",
                            "owner": "agent",
                            "action_level": "agent",
                        }
                    ],
                },
            },
        )
        assert artifacts.retry_plan is not None
        assert artifacts.retry_plan.actions
        assert any(
            action.strategy == "inspect_validation_and_rerun" for action in artifacts.retry_plan.actions
        )
        stage_action = next(
            action for action in artifacts.retry_plan.actions if action.stage == "verification"
        )
        assert stage_action.execution_hints["validation_retry_mode"] == "inspect_visible_errors"
        assert stage_action.execution_hints["focus_stage"] == "verification"
        persisted_retry_plan = json.loads((run_dir / "retry_plan.json").read_text(encoding="utf-8"))
        persisted_stage_action = next(
            action for action in persisted_retry_plan["actions"] if action.get("stage") == "verification"
        )
        assert persisted_stage_action["execution_hints"]["validation_retry_mode"] == "inspect_visible_errors"
        assert persisted_stage_action["execution_hints"]["focus_stage"] == "verification"


def test_workflow_branch_retry_plan_persists_resume_branch_hints_and_manual_review_explanation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_133000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 2,
                },
                "failure_items": [
                    {
                        "name": "pending_payment_modify_mode",
                        "status": "failed",
                        "summary": "已进入待支付记录修改态，需改走已有分支",
                    }
                ],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed", "stage": "verification"},
            attempts=[
                {
                    "attempt_id": "a4",
                    "status": "failed",
                    "classification": {
                        "category": "pending_payment_modify_mode",
                        "reason": "已进入待支付记录的修改态，需走提交申请/支付分支而非默认新增流",
                    },
                    "stage": "verification",
                    "message": "已进入待支付记录的修改态，需走提交申请/支付分支而非默认新增流",
                }
            ],
            max_attempts=4,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 2,
                "execution_hints": {
                    "focus_stage": "verification",
                    "scheduled_strategies": ["inspect_validation_and_rerun"],
                    "validation_retry_mode": "inspect_visible_errors",
                    "scheduled_clusters": [
                        {
                            "cluster_id": "cluster-001",
                            "category": "front_validation",
                            "stage": "verification",
                            "strategy": "inspect_validation_and_rerun",
                            "owner": "agent",
                            "action_level": "agent",
                        }
                    ],
                },
            },
        )
        assert artifacts.retry_plan is not None
        stage_action = next(
            action for action in artifacts.retry_plan.actions if action.stage == "verification"
        )
        assert stage_action.execution_hints["workflow_retry_mode"] == "resume_detected_branch"
        assert stage_action.execution_hints["focus_stage"] == "verification"
        assert artifacts.stop_conditions is not None
        condition_types = {item.condition_type: item for item in artifacts.stop_conditions.conditions}
        assert condition_types["manual_takeover"].status == "manual_review_needed"
        assert artifacts.next_round_decision is not None
        assert artifacts.next_round_decision.status == "needs_review"
        assert artifacts.next_round_decision.should_start_next_round is None
        persisted_retry_plan = json.loads((run_dir / "retry_plan.json").read_text(encoding="utf-8"))
        persisted_stage_action = next(
            action for action in persisted_retry_plan["actions"] if action.get("stage") == "verification"
        )
        assert persisted_stage_action["execution_hints"]["workflow_retry_mode"] == "resume_detected_branch"
        next_round_payload = json.loads((run_dir / "next_round_decision.json").read_text(encoding="utf-8"))
        assert next_round_payload["primary_reason"] == (
            "Stop decision requires manual review before scheduling the next round."
        )
        assert next_round_payload["scheduled_cluster_ids"]
        assert next_round_payload["scheduled_action_ids"]


def test_safety_boundary_artifacts_persist_explanations_and_stop_reason() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_134000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 1,
                },
                "failure_items": [
                    {
                        "name": "account_policy_block",
                        "status": "failed",
                        "summary": "账号权限不足，被业务策略拦截",
                    }
                ],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed"},
            attempts=[
                {
                    "attempt_id": "a5",
                    "status": "failed",
                    "classification": {
                        "category": "account_policy_block",
                        "reason": "账号权限不足，被业务策略拦截",
                    },
                    "message": "账号权限不足，被业务策略拦截",
                }
            ],
            max_attempts=3,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 1,
            },
        )
        assert artifacts.stop_conditions is not None
        assert artifacts.next_round_decision is not None
        stop_payload = json.loads((run_dir / "stop_conditions.json").read_text(encoding="utf-8"))
        next_round_payload = json.loads((run_dir / "next_round_decision.json").read_text(encoding="utf-8"))
        assert stop_payload["primary_reason"] == "safety_boundary"
        assert "safety_boundary" in stop_payload["triggered_conditions"]
        assert next_round_payload["status"] == "stopped"
        assert next_round_payload["should_start_next_round"] is False
        assert next_round_payload["stop_reason"] == "safety_boundary"
        assert "safety_boundary" in next_round_payload["triggered_stop_conditions"]
        assert "Next round scheduling stopped because safety_boundary was triggered." in next_round_payload["primary_reason"]


def test_run_report_extra_sections_preserve_iteration_explanation_fields() -> None:
    from prototype.stage2.app.reporting.models import coerce_run_report

    report = coerce_run_report(
        {
            "summary": {
                "run_id": "20260620_135000_modelA",
                "status": "needs_review",
                "project_name": "proj",
                "template_name": "tpl",
                "current_round": 2,
            },
            "extra_sections": [
                {
                    "title": "Iteration Handoff",
                    "summary": "Conservative stop and retry explanation for the next round handoff.",
                    "facts": [
                        {"label": "next_round_status", "value": "needs_review"},
                        {"label": "scheduled_cluster_count", "value": 2},
                    ],
                    "notes": ["manual review required before scheduling another round"],
                    "stop_reason": "safety_boundary",
                    "target_stage": "verification",
                }
            ],
        }
    )

    assert report.extra_sections
    section = report.extra_sections[0]
    assert section.title == "Iteration Handoff"
    assert section.summary == "Conservative stop and retry explanation for the next round handoff."
    assert [(fact.label, fact.value) for fact in section.facts] == [
        ("next_round_status", "needs_review"),
        ("scheduled_cluster_count", 2),
    ]
    assert section.notes == ["manual review required before scheduling another round"]
    assert section.extra["stop_reason"] == "safety_boundary"
    assert section.extra["target_stage"] == "verification"


def test_execution_hints_enrich_with_scheduled_cluster_context() -> None:
    from tools.suyuan_submit_loop import build_execution_hints

    with tempfile.TemporaryDirectory() as tmpdir:
        previous_run_dir = Path(tmpdir) / "20260620_140000_modelA"
        previous_run_dir.mkdir(parents=True, exist_ok=True)
        (previous_run_dir / "failure_clusters.json").write_text(
            json.dumps(
                {
                    "summary": {"run_id": previous_run_dir.name},
                    "clusters": [
                        {
                            "cluster_id": "cluster-001",
                            "category": "front_validation",
                            "stage": "verification",
                            "action_level": "agent",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        hints = build_execution_hints(
            previous_run_dir,
            {
                "target_stage": "verification",
                "scheduled_cluster_ids": ["cluster-001"],
                "scheduled_action_ids": ["retry-001"],
            },
            {
                "actions": [
                    {
                        "action_id": "retry-001",
                        "cluster_id": "cluster-001",
                        "stage": "verification",
                        "owner": "agent",
                        "strategy": "inspect_validation_and_rerun",
                        "execution_hints": {"validation_retry_mode": "inspect_visible_errors"},
                    }
                ]
            },
        )

        assert hints["resume_from_previous_run"] is True
        assert hints["continue_policy"] == "scheduled_only"
        assert hints["scheduled_cluster_categories"] == ["front_validation"]
        assert hints["scheduled_cluster_stages"] == ["verification"]
        assert hints["scheduled_owners"] == ["agent"]
        assert hints["scheduled_clusters"][0]["action_level"] == "agent"


def test_iteration_sections_include_round_hint_stop_and_next_round_explanations() -> None:
    from tools.suyuan_submit_loop import (
        build_execution_hints_section,
        build_next_round_decision_section,
        build_round_input_section,
        build_stop_conditions_section,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_141000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = write_iteration_artifacts(
            run_dir,
            run_report={
                "summary": {
                    "run_id": run_dir.name,
                    "status": "failed",
                    "project_name": "proj",
                    "template_name": "tpl",
                    "current_round": 2,
                },
                "failure_items": [
                    {
                        "name": "front_validation_missing_commitment",
                        "status": "failed",
                        "summary": "前端校验未通过，需补齐可见字段",
                    }
                ],
            },
            status_snapshot={"run_id": run_dir.name, "overall_status": "failed", "stage": "verification"},
            attempts=[
                {
                    "attempt_id": "a7",
                    "status": "failed",
                    "classification": {
                        "category": "front_validation_missing_commitment",
                        "reason": "前端校验未通过，需补齐可见字段",
                    },
                    "stage": "verification",
                    "message": "前端校验未通过，需补齐可见字段",
                }
            ],
            max_attempts=5,
            round_input={
                "orchestration_stream_id": "tpl::modelA",
                "template_name": "tpl",
                "model_name": "modelA",
                "project_name": "proj",
                "round_index": 2,
                "execution_hints": {
                    "focus_stage": "verification",
                    "scheduled_strategies": ["inspect_validation_and_rerun"],
                    "validation_retry_mode": "inspect_visible_errors",
                    "scheduled_clusters": [
                        {
                            "cluster_id": "cluster-001",
                            "category": "front_validation",
                            "stage": "verification",
                            "strategy": "inspect_validation_and_rerun",
                            "owner": "agent",
                            "action_level": "agent",
                        }
                    ],
                },
            },
        )

        round_section = build_round_input_section(artifacts)
        execution_section = build_execution_hints_section(artifacts)
        stop_section = build_stop_conditions_section(artifacts)
        next_round_section = build_next_round_decision_section(artifacts)

        assert round_section is not None
        assert "Round 2 will focus on verification" in str(round_section["summary"])
        assert execution_section is not None
        assert "retry strategies" in str(execution_section["summary"])
        assert any(fact["label"] == "validation_retry_mode" for fact in execution_section["facts"])
        assert stop_section is not None
        assert "manual review is required" in str(stop_section["summary"])
        assert next_round_section is not None
        assert "manual review before scheduling the next round" in str(next_round_section["summary"])
        assert any(item["status"] == "scheduled" for item in next_round_section["items"])


def test_human_takeover_packet_contains_resume_command_and_pending_actions() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "20260620_150000_modelA"
        run_dir.mkdir(parents=True, exist_ok=True)
        packet = build_human_takeover_packet(
            run_dir,
            round_input={
                "template_name": "tpl",
                "project_name": "proj",
                "model_name": "modelA",
                "round_index": 2,
                "target_stage": "verification",
                "execution_hints": {"workflow_retry_mode": "resume_detected_branch"},
            },
            retry_plan={
                "actions": [
                    {
                        "action_id": "retry-001",
                        "cluster_id": "cluster-001",
                        "title": "Resume branch",
                        "stage": "verification",
                        "owner": "agent",
                        "strategy": "resume_detected_branch",
                        "reason": "Detected pending branch",
                        "expected_outcome": "Resume existing workflow",
                        "execution_hints": {"workflow_retry_mode": "resume_detected_branch"},
                    }
                ]
            },
            stop_conditions={"status": "needs_review", "primary_reason": "manual_takeover"},
            next_round_decision={
                "status": "needs_review",
                "primary_reason": "Stop decision requires manual review before scheduling the next round.",
                "target_stage": "verification",
                "scheduled_cluster_ids": ["cluster-001"],
                "scheduled_action_ids": ["retry-001"],
            },
        )
        assert packet["status"] == "waiting_human"
        assert packet["scheduled_action_ids"] == ["retry-001"]
        assert packet["pending_actions"][0]["execution_hints"]["workflow_retry_mode"] == "resume_detected_branch"
        assert "--resume-human-takeover" in packet["resume_command"]
        assert packet["target_stage"] == "verification"


def test_human_resume_decision_converts_review_to_scheduled() -> None:
    resumed = build_human_resume_decision(
        {
            "status": "needs_review",
            "should_start_next_round": None,
            "primary_reason": "Stop decision requires manual review before scheduling the next round.",
            "target_stage": "verification",
            "scheduled_cluster_ids": ["cluster-001"],
            "scheduled_action_ids": ["retry-001"],
            "notes": ["manual review required"],
        },
        operator_id="tester-1",
        note="human fixed prerequisite",
    )
    assert resumed["status"] == "scheduled"
    assert resumed["should_start_next_round"] is True
    assert resumed["human_takeover_resolved"] is True
    assert resumed["human_takeover_operator"] == "tester-1"
    assert "human fixed prerequisite" in resumed["notes"]
