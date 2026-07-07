import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, async_playwright
from prototype.stage2.app.config import (
    CapabilityRoutingDecision,
    CapabilityGateDecision,
    ModelProfile,
    RunPolicyLoadResult,
    build_capability_routing,
    load_model_profiles as load_stage2_model_profiles,
    load_run_policy as load_stage2_run_policy,
    validate_model_capabilities,
)
from prototype.stage2.app.data_factory.generator import TemplateDataFactory
from prototype.stage2.app.discovery import (
    DiscoveryArtifactWriter,
    DiscoveryPlanner,
    plan_live_discovery,
)
from prototype.stage2.app.discovery.models import (
    DiscoveryResult,
    FeaturePointRecord,
    PageEntryRecord,
    ScreenshotRecord,
)
from prototype.stage2.app.discovery.strategy import DiscoveryStrategyDecision, select_discovery_strategy
from prototype.stage2.app.iteration import write_iteration_artifacts
from prototype.stage2.app.orchestration.routing_summary import build_routing_summary
from prototype.stage2.app.orchestration.session_artifacts import sync_orchestration_session_artifacts
from prototype.stage2.app.progress import ProgressManager
from prototype.stage2.app.progress.console import format_status_line
from prototype.stage2.app.reporting import (
    adapt_progress_snapshot,
    build_decision_section,
    build_platform_daily_report,
    build_routing_section,
    render_progress_markdown,
    render_progress_text,
    render_platform_daily_report_markdown,
    render_run_report_markdown,
)
from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.runtime import (
    POLICY_ALLOWED,
    PolicyGateDecision,
    RISK_RISKY_SUBMIT,
    evaluate_action_policy,
)
from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.verification.generated_files import build_default_generated_files
from prototype.stage2.app.verification.template_executor import (
    TemplateActionRegistry,
    TemplateFlowExecutor,
    TemplateStepExecution,
)
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData
from prototype.stage2.app.verification.suyuan_shared_actions import (
    register_suyuan_wizard_drawer_actions,
)
from prototype.stage2.app.verification.suyuan_submit_dialog_actions import (
    register_suyuan_submit_dialog_actions,
    submit_filing_dialog,
)


if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


ARTIFACT_ROOT = ROOT_DIR / "artifacts" / "stage2"
DEFAULT_CDP_URL = "http://localhost:9222"
ONLINE_RECORD_URL = "https://www.zbsykj.com:19096/record/online"
DEFAULT_ENV_FILES = [
    ROOT_DIR / "demo" / ".env",
    ROOT_DIR / "demo" / "local_qwen.env",
]
STAGE2_TEMPLATE_DIR = ROOT_DIR / "prototype" / "stage2" / "templates" / "suyuan_online_apply"
TEMPLATE_BUNDLE = load_template_bundle(STAGE2_TEMPLATE_DIR)
SUCCESS_BASELINE = TEMPLATE_BUNDLE.baseline
RUN_POLICY_FILE = ROOT_DIR / "prototype" / "stage2" / "run_policy.json"
STAGE2_PROJECT_NAME = "AI Agent 软件自动化评测平台第二阶段原型"


def build_orchestration_stream_id(template_name: str, model_name: str) -> str:
    return f"{template_name}::{profile_safe_name(model_name)}"


def profile_safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _configured_round_limit(
    round_input: Mapping[str, Any] | None,
    fallback: int | None = None,
) -> int | None:
    if round_input:
        configured = _coerce_positive_int(round_input.get("max_rounds"))
        if configured is not None:
            return configured
    return _coerce_positive_int(fallback)


def _derive_remaining_round_budget(round_input: Mapping[str, Any] | None) -> int | None:
    configured = _configured_round_limit(round_input)
    current_round = _coerce_positive_int((round_input or {}).get("round_index"))
    if configured is None or current_round is None:
        return None
    remaining = configured - current_round
    return remaining if remaining > 0 else None


def resolve_resume_round_budget(
    round_input: Mapping[str, Any] | None,
    requested_max_rounds: int | None,
) -> tuple[int, str]:
    derived_remaining = _derive_remaining_round_budget(round_input)
    requested = _coerce_positive_int(requested_max_rounds)

    if requested is None:
        if derived_remaining is not None:
            return derived_remaining, "derived_from_round_input"
        return 1, "fallback_single_round"

    if derived_remaining is None:
        return requested, "cli_override"

    if requested == 1 and derived_remaining > 1:
        return derived_remaining, "expanded_default_to_remaining_budget"

    if requested > derived_remaining:
        return derived_remaining, "capped_to_remaining_budget"

    return requested, "cli_override"


def read_round_input(run_dir: Path) -> dict[str, Any]:
    return _read_json_file(run_dir / "round_input.json")


def resolve_model_profile(model_name: str) -> ModelProfile:
    profiles = load_stage2_model_profiles(DEFAULT_ENV_FILES)
    normalized_target = profile_safe_name(model_name)
    for profile in profiles:
        if profile.name == model_name or profile_safe_name(profile.name) == normalized_target:
            return profile
    raise RuntimeError(f"未找到模型配置：{model_name}")


def build_round_input(
    profile: ModelProfile,
    *,
    round_index: int,
    max_rounds: int,
    previous_run_dir: Path | None,
    previous_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    orchestration_stream_id = build_orchestration_stream_id(TEMPLATE_BUNDLE.name, profile.name)
    decision = previous_decision or {}
    retry_plan = _read_json_file(previous_run_dir / "retry_plan.json") if previous_run_dir else {}
    round_input: dict[str, Any] = {
        "orchestration_stream_id": orchestration_stream_id,
        "template_name": TEMPLATE_BUNDLE.name,
        "model_name": profile.name,
        "project_name": "AI Agent 软件自动化评测平台第二阶段原型",
        "round_index": round_index,
        "max_rounds": max_rounds,
        "previous_run_id": previous_run_dir.name if previous_run_dir else None,
        "previous_run_dir": str(previous_run_dir) if previous_run_dir else None,
        "retry_run_dir": str(previous_run_dir) if previous_run_dir else None,
        "target_stage": decision.get("target_stage") or "verification",
        "goal": retry_plan.get("goal") or "Resolve scheduled failure clusters in the next orchestration round.",
        "source_decision_status": decision.get("status"),
        "source_decision_reason": decision.get("primary_reason") or decision.get("stop_reason"),
        "scheduled_cluster_ids": decision.get("scheduled_cluster_ids") or [],
        "scheduled_action_ids": decision.get("scheduled_action_ids") or [],
        "execution_hints": build_execution_hints(previous_run_dir, decision, retry_plan),
        "notes": list(decision.get("notes") or []),
    }
    if round_index == 1:
        round_input["notes"].append("Initial orchestration round with no previous retry decision.")
    else:
        round_input["notes"].append("Round input was derived from the previous run's next-round decision.")
    return round_input


def build_execution_hints(
    previous_run_dir: Path | None,
    decision: dict[str, Any],
    retry_plan: dict[str, Any],
) -> dict[str, Any]:
    failure_cluster_index: dict[str, dict[str, Any]] = {}
    if previous_run_dir is not None:
        failure_payload = _read_json_file(previous_run_dir / "failure_clusters.json")
        failure_cluster_index = {
            str(item.get("cluster_id")): item
            for item in failure_payload.get("clusters", [])
            if isinstance(item, dict) and item.get("cluster_id")
        }
    hints: dict[str, Any] = {
        "focus_stage": decision.get("target_stage") or "verification",
        "scheduled_cluster_ids": list(decision.get("scheduled_cluster_ids") or []),
        "scheduled_action_ids": list(decision.get("scheduled_action_ids") or []),
        "scheduled_strategies": [],
        "scheduled_clusters": [],
        "scheduled_cluster_categories": [],
        "scheduled_cluster_stages": [],
        "scheduled_owners": [],
        "skip_completed_discovery": previous_run_dir is not None,
        "preserve_generated_files": previous_run_dir is not None,
        "resume_from_previous_run": previous_run_dir is not None,
        "continue_policy": "scheduled_only",
    }
    scheduled_categories: set[str] = set()
    scheduled_stages: set[str] = set()
    scheduled_owners: set[str] = set()
    for action in retry_plan.get("actions") or []:
        if action.get("action_id") not in hints["scheduled_action_ids"]:
            continue
        strategy = action.get("strategy")
        if strategy and strategy not in hints["scheduled_strategies"]:
            hints["scheduled_strategies"].append(strategy)
        execution_hints = action.get("execution_hints")
        if isinstance(execution_hints, dict):
            hints.update({key: value for key, value in execution_hints.items() if value is not None})
        owner = str(action.get("owner") or "").strip()
        if owner:
            scheduled_owners.add(owner)
        cluster_id = str(action.get("cluster_id") or "").strip()
        cluster_meta = failure_cluster_index.get(cluster_id, {})
        cluster_stage = cluster_meta.get("stage") or action.get("stage")
        cluster_category = cluster_meta.get("category")
        if cluster_category:
            scheduled_categories.add(str(cluster_category))
        if cluster_stage:
            scheduled_stages.add(str(cluster_stage))
        hints["scheduled_clusters"].append(
            {
                "cluster_id": cluster_id or None,
                "category": cluster_category,
                "stage": cluster_stage,
                "action_level": cluster_meta.get("action_level"),
                "strategy": strategy,
                "owner": owner or None,
            }
        )
    if "repair_input_then_rerun" in hints["scheduled_strategies"]:
        hints["regenerate_runtime_data"] = True
    if "refresh_locator_and_rerun" in hints["scheduled_strategies"]:
        hints["ui_retry_mode"] = "refresh_locator_and_rerun"
    if "capture_and_retry" in hints["scheduled_strategies"]:
        hints["capture_network_on_retry"] = True
    if "fix_precondition_then_rerun" in hints["scheduled_strategies"]:
        hints["preflight_mode"] = "fix_precondition_then_rerun"
    hints["scheduled_cluster_categories"] = sorted(scheduled_categories)
    hints["scheduled_cluster_stages"] = sorted(scheduled_stages)
    hints["scheduled_owners"] = sorted(scheduled_owners)
    return hints


def build_human_resume_decision(
    decision: dict[str, Any],
    *,
    operator_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if not decision:
        raise RuntimeError("未找到上一轮 next_round_decision，无法恢复人工接管后的执行。")
    scheduled_action_ids = list(decision.get("scheduled_action_ids") or [])
    scheduled_cluster_ids = list(decision.get("scheduled_cluster_ids") or [])
    if not scheduled_action_ids and not scheduled_cluster_ids:
        raise RuntimeError("上一轮没有可继续的 scheduled actions / clusters，无法恢复人工接管后的执行。")

    resumed = dict(decision)
    notes = list(decision.get("notes") or [])
    notes.append("Human takeover was acknowledged and the scheduled retry can continue.")
    if operator_id:
        notes.append(f"Human takeover operator: {operator_id}")
    if note:
        notes.append(note)
    resumed.update(
        {
            "status": "scheduled",
            "should_start_next_round": True,
            "primary_reason": "Human takeover was completed and the scheduled retry can continue.",
            "human_takeover_resolved": True,
            "human_takeover_operator": operator_id,
            "human_takeover_note": note,
            "notes": notes,
        }
    )
    return resumed


def build_human_takeover_packet(
    run_dir: Path,
    *,
    round_input: dict[str, Any],
    retry_plan: dict[str, Any],
    stop_conditions: dict[str, Any],
    next_round_decision: dict[str, Any],
    max_attempts: int | None = None,
) -> dict[str, Any]:
    scheduled_action_ids = list(next_round_decision.get("scheduled_action_ids") or [])
    scheduled_cluster_ids = list(next_round_decision.get("scheduled_cluster_ids") or [])
    pending_actions: list[dict[str, Any]] = []
    for action in retry_plan.get("actions") or []:
        if scheduled_action_ids and action.get("action_id") not in scheduled_action_ids:
            continue
        pending_actions.append(
            {
                "action_id": action.get("action_id"),
                "cluster_id": action.get("cluster_id"),
                "title": action.get("title"),
                "stage": action.get("stage"),
                "owner": action.get("owner"),
                "strategy": action.get("strategy"),
                "reason": action.get("reason"),
                "expected_outcome": action.get("expected_outcome"),
                "execution_hints": action.get("execution_hints") or {},
            }
        )

    model_name = round_input.get("model_name") or "unknown-model"
    resume_round_budget = _derive_remaining_round_budget(round_input) or 1
    resume_attempt_budget = _coerce_positive_int(max_attempts) or 3
    resume_command = (
        "python -m prototype.stage2.main "
        f'--resume-human-takeover "{run_dir}" '
        f'--cdp-url "{DEFAULT_CDP_URL}" '
        f"--max-attempts {resume_attempt_budget} "
        f"--max-rounds {resume_round_budget}"
    )
    return {
        "schema_version": "human_takeover_packet.v1",
        "status": "waiting_human",
        "source_run_id": run_dir.name,
        "source_run_dir": str(run_dir),
        "model_name": model_name,
        "template_name": round_input.get("template_name"),
        "project_name": round_input.get("project_name"),
        "current_round": round_input.get("round_index"),
        "resume_round": (int(round_input.get("round_index") or 0) + 1) if round_input.get("round_index") is not None else None,
        "target_stage": next_round_decision.get("target_stage") or round_input.get("target_stage"),
        "reason": next_round_decision.get("primary_reason") or stop_conditions.get("primary_reason"),
        "stop_status": stop_conditions.get("status"),
        "next_round_status": next_round_decision.get("status"),
        "scheduled_cluster_ids": scheduled_cluster_ids,
        "scheduled_action_ids": scheduled_action_ids,
        "execution_hints": round_input.get("execution_hints") or {},
        "resume_max_attempts": resume_attempt_budget,
        "resume_max_rounds": resume_round_budget,
        "configured_max_rounds": _configured_round_limit(round_input),
        "pending_actions": pending_actions,
        "resume_command": resume_command,
        "notes": [
            "Complete the required human takeover or review in the browser/system first.",
            "Then use the resume command to continue the scheduled retry round.",
        ],
    }


def write_human_takeover_packet(run_dir: Path, packet: dict[str, Any]) -> Path:
    path = run_dir / "human_takeover.json"
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_discovery_seed(artifacts: ArtifactWriter) -> tuple[object, dict[str, Path]]:
    result = DiscoveryPlanner().plan(
        template_name=TEMPLATE_BUNDLE.name,
        template=TEMPLATE_BUNDLE.template,
        baseline=SUCCESS_BASELINE,
    )
    paths = DiscoveryArtifactWriter(artifacts.run_dir).write(result)
    return result, paths


async def build_live_discovery(page: Page, artifacts: ArtifactWriter) -> tuple[object, dict[str, Path]]:
    result = await plan_live_discovery(
        page,
        template_name=TEMPLATE_BUNDLE.name,
        template=TEMPLATE_BUNDLE.template,
        baseline=SUCCESS_BASELINE,
        screenshots_dir=artifacts.run_dir,
    )
    paths = DiscoveryArtifactWriter(artifacts.run_dir).write(result)
    return result, paths


def build_report_page_entries(discovery_result: object, fallback_name: str, page_url: str) -> list[dict[str, Any]]:
    result = getattr(discovery_result, "page_entries", [])
    if result:
        return [
            {
                "item_id": item.page_entry_id,
                "name": item.name,
                "status": "已发现",
                "summary": item.url or page_url,
                "source": item.source,
            }
            for item in result
        ]
    return [{"name": fallback_name, "status": "已发现", "summary": page_url}]


def build_report_feature_points(discovery_result: object, fallback_name: str) -> list[dict[str, Any]]:
    result = getattr(discovery_result, "feature_points", [])
    if result:
        return [
            {
                "item_id": item.feature_point_id,
                "name": item.name,
                "status": "已发现",
                "summary": item.feature_type,
                "source": item.source,
                "owner": item.page_entry_id,
            }
            for item in result
        ]
    return [{"name": fallback_name, "status": "已发现", "summary": "待验证"}]


def build_report_key_artifacts(artifacts: ArtifactWriter) -> list[dict[str, Any]]:
    labels = [
        "page_before_apply.png",
        "dialog_opened.png",
        "verified_flow_initial_form.png",
        "verified_flow_full_form.png",
        "verified_flow_submit_dialog.png",
        "verified_flow_final_result.png",
        "attempt_01_before_submit.png",
        "attempt_01_after_submit.png",
    ]
    items: list[dict[str, Any]] = []
    for label in labels:
        path = artifacts.screenshots_dir / label
        if not path.exists():
            continue
        items.append(
            {
                "label": label,
                "kind": "file",
                "path": str(path),
            }
        )
    return items


def build_cross_model_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    success_count = 0
    durations_ms: list[int] = []
    for result in results:
        run_dir = Path(str(result.get("run_dir", "")))
        report_path = run_dir / "reports" / "run_report.md"
        current_status_path = run_dir / "current_status.json"
        status_payload: dict[str, Any] = {}
        if current_status_path.exists():
            try:
                status_payload = json.loads(current_status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                status_payload = {}
        overall_status = str(result.get("status") or status_payload.get("overall_status") or "unknown")
        stats = status_payload.get("stats") or {}
        elapsed_ms = int(status_payload.get("elapsed_ms") or 0)
        round_count = int(result.get("round_count") or 0)
        final_next_round_decision = result.get("final_next_round_decision") or {}
        if overall_status == "completed":
            success_count += 1
        if elapsed_ms:
            durations_ms.append(elapsed_ms)
        items.append(
            {
                "name": str(result.get("model", "unknown-model")),
                "status": overall_status,
                "summary": f"run_dir={run_dir.name}",
                "facts": [
                    {"label": "elapsed_ms", "value": elapsed_ms},
                    {"label": "round_count", "value": round_count or None},
                    {
                        "label": "next_round_status",
                        "value": final_next_round_decision.get("status"),
                    },
                    {"label": "page_entries_discovered", "value": stats.get("page_entries_discovered")},
                    {"label": "feature_points_discovered", "value": stats.get("feature_points_discovered")},
                    {"label": "verification_successes", "value": stats.get("verification_successes")},
                ],
                "artifacts": (
                    [{"label": "run_report.md", "path": str(report_path), "kind": "file"}]
                    if report_path.exists()
                    else []
                ),
            }
        )

    average_elapsed_ms = round(sum(durations_ms) / len(durations_ms), 2) if durations_ms else None
    return {
        "generated_at": datetime.now().isoformat(),
        "summary": f"{success_count}/{len(results)} models completed the current sample run successfully.",
        "facts": [
            {"label": "model_count", "value": len(results)},
            {"label": "successful_models", "value": success_count},
            {"label": "average_elapsed_ms", "value": average_elapsed_ms},
        ],
        "items": items,
        "notes": [
            "This summary compares the latest per-model run directories produced by the current execution.",
            "Use these results as a routing hint, not as a formal benchmark.",
        ],
    }


def build_report_failure_clusters(iteration_artifacts: object) -> list[dict[str, Any]]:
    clusters = getattr(iteration_artifacts, "failure_clusters", [])
    result: list[dict[str, Any]] = []
    for cluster in clusters:
        facts = [{"label": "signal_count", "value": cluster.signal_count}]
        if cluster.stage:
            facts.append({"label": "stage", "value": cluster.stage})
        result.append(
            {
                "cluster_id": cluster.cluster_id,
                "category": cluster.category,
                "status": cluster.status,
                "summary": cluster.summary,
                "root_cause": cluster.root_cause_hint,
                "action_level": cluster.action_level,
                "recommendation": cluster.recommendation,
                "related_items": cluster.related_items,
                "facts": facts,
            }
        )
    return result


def summarize_attempt_execution(actions: list[dict[str, Any]]) -> dict[str, Any]:
    failed_steps = [
        item.get("step")
        for item in actions
        if str(item.get("status") or "").lower() == "failed"
        or (isinstance(item.get("result"), dict) and item["result"].get("ok") is False)
    ]
    duration_ms = sum(
        int(item.get("duration_ms") or 0)
        for item in actions
        if isinstance(item, dict)
    )
    return {
        "step_count": len(actions),
        "failed_steps": [step for step in failed_steps if step],
        "duration_ms": duration_ms,
        "status": "failed" if failed_steps else "completed",
    }


def total_attempt_duration_ms(attempts: list[dict[str, Any]]) -> int:
    return sum(
        int(item.get("execution_summary", {}).get("duration_ms") or 0)
        for item in attempts
        if isinstance(item, dict)
    )


def build_report_promotion_candidates(iteration_artifacts: object) -> list[dict[str, Any]]:
    candidates = getattr(iteration_artifacts, "promotion_candidates", [])
    return [
        {
            "item_id": candidate.candidate_id,
            "name": candidate.title,
            "status": candidate.status,
            "summary": candidate.reason,
            "source": candidate.source,
            "owner": candidate.promotion_level,
        }
        for candidate in candidates
    ]


def build_iteration_asset_refs(run_dir: Path) -> list[dict[str, str]]:
    labels = [
        "round_input.json",
        "capability_preflight.json",
        "failure_clusters.json",
        "retry_plan.json",
        "promotion_candidates.json",
        "stop_conditions.json",
        "iteration_comparison.json",
        "next_round_decision.json",
        "human_takeover.json",
    ]
    return [{"label": label, "path": str(run_dir / label)} for label in labels]


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_reused_discovery_result(run_dir: Path) -> DiscoveryResult | None:
    payload = _read_json_file(run_dir / "discovery_result.json")
    if not payload:
        return None
    try:
        return DiscoveryResult(
            template_name=str(payload.get("template_name") or TEMPLATE_BUNDLE.name),
            generated_at=str(payload.get("generated_at") or datetime.now().isoformat()),
            strategy=str(payload.get("strategy") or "reused_discovery"),
            page_entries=[
                PageEntryRecord(**item)
                for item in payload.get("page_entries", [])
                if isinstance(item, dict)
            ],
            feature_points=[
                FeaturePointRecord(**item)
                for item in payload.get("feature_points", [])
                if isinstance(item, dict)
            ],
            screenshot_records=[
                ScreenshotRecord(**item)
                for item in payload.get("screenshot_records", [])
                if isinstance(item, dict)
            ],
            review_queue=[
                item for item in payload.get("review_queue", [])
                if isinstance(item, dict)
            ],
            review_hints=dict(payload.get("review_hints") or {}),
            stats=dict(payload.get("stats") or {}),
            notes=list(payload.get("notes") or []),
        )
    except Exception:
        return None


def _build_reused_discovery_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "page_entries": run_dir / "page_entries.json",
        "feature_points": run_dir / "feature_points.json",
        "screenshot_records": run_dir / "screenshot_records.json",
        "review_queue": run_dir / "discovery_review_queue.json",
        "discovery_summary": run_dir / "discovery_result.json",
    }


def load_run_report_payload(run_dir: Path) -> dict[str, Any]:
    return _read_json_file(run_dir / "reports" / "run_report.json")


def load_next_round_decision(run_dir: Path) -> dict[str, Any]:
    return _read_json_file(run_dir / "next_round_decision.json")


def should_auto_continue_next_round(decision: dict[str, Any]) -> bool:
    status = str(decision.get("status") or "").strip().lower()
    should_start = decision.get("should_start_next_round")
    return status == "scheduled" and should_start is True


def load_run_policy() -> dict[str, Any]:
    return load_run_policy_resolution().to_policy_gate_payload()


def load_run_policy_resolution(
    *,
    project_name: str = STAGE2_PROJECT_NAME,
    template_name: str = TEMPLATE_BUNDLE.name,
) -> RunPolicyLoadResult:
    return load_stage2_run_policy(
        RUN_POLICY_FILE,
        project_name=project_name,
        template_name=template_name,
    )


def _routing_stage_summary(
    decision: CapabilityRoutingDecision,
    stage: str,
) -> str:
    route = getattr(decision, stage, None)
    if route is None:
        return "unknown"
    return (
        f"allowed={route.allowed}, mode={route.recommended_mode}, "
        f"reason_code={route.reason_code}"
    )


def build_stage_route_decision_payload(
    capability_routing: CapabilityRoutingDecision | None,
    *,
    stage: str,
    requested_mode: str,
    assigned_role: str,
) -> dict[str, Any]:
    if capability_routing is None:
        return {}
    route = getattr(capability_routing, stage, None)
    if route is None:
        return {}
    selected_mode = route.recommended_mode
    payload: dict[str, Any] = {
        "status": (
            "allowed"
            if route.allowed
            else "blocked"
        ),
        "stage": stage,
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "assigned_role": assigned_role,
        "reason": route.reason,
        "required_tags": list(route.required_tags),
        "missing_tags": list(route.missing_tags),
        "routing_tags": list(route.routing_tags),
        "capability_tags": dict(route.capability_tags),
    }
    if route.allowed and selected_mode != requested_mode:
        payload["status"] = "degraded"
        payload["fallback_mode"] = selected_mode
        payload["fallback_role"] = assigned_role
    return payload


def _coerce_capability_gate_from_payload(run_dir: Path) -> CapabilityGateDecision | None:
    payload = _read_json_file(run_dir / "capability_preflight.json")
    if not payload:
        return None
    try:
        return CapabilityGateDecision(
            status=str(payload.get("status") or "blocked"),
            reason_code=str(payload.get("reason_code") or "unknown"),
            reason=str(payload.get("reason") or ""),
            mode=str(payload.get("mode") or "unknown"),
            profile_name=str(payload.get("profile_name") or ""),
            required_tags=list(payload.get("required_tags") or []),
            missing_tags=list(payload.get("missing_tags") or []),
            capability_tags=dict(payload.get("capability_tags") or {}),
            max_age_hours=int(payload.get("max_age_hours") or 0),
            snapshot=None,
            notes=list(payload.get("notes") or []),
        )
    except Exception:
        return None


def _coerce_capability_routing_from_payload(run_dir: Path) -> CapabilityRoutingDecision | None:
    payload = _read_json_file(run_dir / "capability_routing.json")
    if not payload:
        return None
    try:
        discovery_payload = payload.get("discovery") or {}
        verification_payload = payload.get("verification") or {}
        reporting_payload = payload.get("reporting") or {}
        return CapabilityRoutingDecision(
            profile_name=str(payload.get("profile_name") or ""),
            model=str(payload.get("model") or ""),
            gate_status=str(payload.get("gate_status") or "unknown"),
            gate_reason_code=str(payload.get("gate_reason_code") or "unknown"),
            gate_reason=str(payload.get("gate_reason") or ""),
            capability_tags=dict(payload.get("capability_tags") or {}),
            routing_tags=list(payload.get("routing_tags") or []),
            notes=list(payload.get("notes") or []),
            discovery=_coerce_stage_route("discovery", discovery_payload),
            verification=_coerce_stage_route("verification", verification_payload),
            reporting=_coerce_stage_route("reporting", reporting_payload),
        )
    except Exception:
        return None


def _coerce_stage_route(
    stage: str,
    payload: dict[str, Any],
):
    if not payload:
        return None
    from prototype.stage2.app.config.capability_routing import CapabilityStageRoute

    return CapabilityStageRoute(
        stage=stage,
        allowed=bool(payload.get("allowed")),
        recommended_mode=str(payload.get("recommended_mode") or ""),
        reason_code=str(payload.get("reason_code") or ""),
        reason=str(payload.get("reason") or ""),
        required_tags=list(payload.get("required_tags") or []),
        missing_tags=list(payload.get("missing_tags") or []),
        routing_tags=list(payload.get("routing_tags") or []),
        capability_tags=dict(payload.get("capability_tags") or {}),
        notes=list(payload.get("notes") or []),
    )


def _coerce_discovery_strategy_from_payload(run_dir: Path) -> DiscoveryStrategyDecision | None:
    payload = _read_json_file(run_dir / "discovery_strategy.json")
    if not payload:
        return None
    try:
        return DiscoveryStrategyDecision(
            selected_strategy=str(payload.get("selected_strategy") or "blocked"),
            should_seed_discovery=bool(payload.get("should_seed_discovery")),
            should_run_live_discovery=bool(payload.get("should_run_live_discovery")),
            reason_code=str(payload.get("reason_code") or "unknown"),
            reason=str(payload.get("reason") or ""),
            route_mode=str(payload.get("route_mode")) if payload.get("route_mode") is not None else None,
            route_allowed=bool(payload.get("route_allowed")),
            reuse_completed_discovery=bool(payload.get("reuse_completed_discovery")),
            reporting_only=bool(payload.get("reporting_only")),
            execution_hints=dict(payload.get("execution_hints") or {}),
            notes=list(payload.get("notes") or []),
            route_summary=dict(payload.get("route_summary") or {}),
        )
    except Exception:
        return None


def classify_capability_gate_failure(decision: CapabilityGateDecision) -> dict[str, Any]:
    category_map = {
        "capability_probe_missing": "preflight_capability_missing",
        "capability_probe_stale": "preflight_capability_stale",
        "capability_probe_incompatible": "preflight_capability_incompatible",
    }
    return {
        "success": False,
        "category": category_map.get(decision.reason_code, "preflight_capability_blocked"),
        "reason": decision.reason,
        "reason_code": decision.reason_code,
        "mode": decision.mode,
        "required_tags": list(decision.required_tags),
        "missing_tags": list(decision.missing_tags),
        "capability_tags": dict(decision.capability_tags),
        "snapshot": decision.snapshot.to_dict() if decision.snapshot else None,
        "notes": list(decision.notes),
    }


def classify_policy_gate_failure(decision: PolicyGateDecision) -> dict[str, Any]:
    category = "policy_review_required" if decision.needs_review else "policy_blocked"
    return {
        "success": False,
        "category": category,
        "reason": decision.reason or "The action was blocked by run policy.",
        "reason_code": decision.reason_code,
        "policy_status": decision.status,
        "policy_source": decision.policy_source,
        "matched_rule_id": decision.matched_rule_id,
        "matched_allowlist": decision.matched_allowlist,
        "requires_allowlist": decision.requires_allowlist,
        "risk_level": decision.risk_level,
        "notes": list(decision.notes),
        "extra": dict(decision.extra),
    }


def build_preflight_capability_gate(
    profile: ModelProfile,
    *,
    mode: str,
) -> CapabilityGateDecision:
    return validate_model_capabilities(profile, mode=mode)


def build_submit_action_policy_decision(
    *,
    action_id: str,
    template_name: str,
    project_name: str,
) -> PolicyGateDecision:
    policy_payload = load_run_policy()
    return evaluate_action_policy(
        {
            "action_id": action_id,
            "template_name": template_name,
            "project_name": project_name,
            "action_type": "submit",
        },
        RISK_RISKY_SUBMIT,
        payload=policy_payload,
    )


def require_submit_action_policy(
    *,
    action_id: str,
    template_name: str,
    project_name: str,
) -> PolicyGateDecision:
    decision = build_submit_action_policy_decision(
        action_id=action_id,
        template_name=template_name,
        project_name=project_name,
    )
    if decision.status != POLICY_ALLOWED:
        category = "policy_review_required" if decision.needs_review else "policy_blocked"
        raise RuntimeError(f"{category}: {decision.reason}")
    return decision


def write_platform_daily_report_bundle(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    report_payloads: list[dict[str, Any]] = []
    for result in results:
        run_dir = Path(str(result.get("run_dir", "")))
        payload = load_run_report_payload(run_dir)
        if payload:
            report_payloads.append(payload)

    if not report_payloads:
        return None

    platform_report = build_platform_daily_report(report_payloads)
    json_path = ARTIFACT_ROOT / "latest_platform_daily_report.json"
    markdown_path = ARTIFACT_ROOT / "latest_platform_daily_report.md"
    json_path.write_text(
        json.dumps(platform_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_platform_daily_report_markdown(platform_report),
        encoding="utf-8",
    )
    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "report_date": platform_report.report_date,
        "run_count": len(report_payloads),
    }


def build_baseline_freeze_manifest(
    results: list[dict[str, Any]],
    *,
    comparison_path: Path,
    platform_daily_report: dict[str, Any] | None,
) -> dict[str, Any]:
    run_items: list[dict[str, Any]] = []
    for result in results:
        run_dir = Path(str(result.get("run_dir", "")))
        status_payload = _read_json_file(run_dir / "current_status.json")
        stop_conditions = _read_json_file(run_dir / "stop_conditions.json")
        next_round_decision = _read_json_file(run_dir / "next_round_decision.json")
        run_report = load_run_report_payload(run_dir)
        elapsed_ms = int(status_payload.get("elapsed_ms") or 0)
        overall_status = str(
            status_payload.get("overall_status")
            or run_report.get("summary", {}).get("status")
            or "unknown"
        )
        round_count = int(result.get("round_count") or 0)
        run_items.append(
            {
                "model": str(result.get("model", "unknown-model")),
                "run_dir": str(run_dir),
                "status": overall_status,
                "elapsed_ms": elapsed_ms,
                "round_count": round_count or None,
                "triggered_stop_conditions": stop_conditions.get("triggered_conditions", []),
                "next_round_status": next_round_decision.get("status"),
                "should_start_next_round": next_round_decision.get("should_start_next_round"),
                "artifacts": {
                    "baseline_snapshot": str(run_dir / "baseline_snapshot.json"),
                    "runtime_data": str(run_dir / "runtime_data.json"),
                    "run_report_json": str(run_dir / "reports" / "run_report.json"),
                    "run_report_markdown": str(run_dir / "reports" / "run_report.md"),
                    "progress_view": str(run_dir / "reports" / "progress_view.md"),
                },
            }
        )

    successful_runs = [item for item in run_items if item["status"] == "completed"]
    primary_run = None
    if successful_runs:
        primary_run = sorted(
            successful_runs,
            key=lambda item: (
                item["elapsed_ms"] if item["elapsed_ms"] else 10**12,
                item["model"],
            ),
        )[0]

    freeze_recommended = primary_run is not None
    if primary_run is not None:
        selection_reason = (
            "Selected the completed run with the shortest observed elapsed time "
            "as the recommended regression baseline."
        )
    else:
        selection_reason = "No completed run is available yet, so baseline freeze is not recommended."

    return {
        "generated_at": datetime.now().isoformat(),
        "template_name": TEMPLATE_BUNDLE.name,
        "freeze_recommended": freeze_recommended,
        "selection_reason": selection_reason,
        "comparison_summary_path": str(comparison_path),
        "platform_daily_report": platform_daily_report or {},
        "recommended_primary_run": primary_run,
        "run_count": len(run_items),
        "successful_run_count": len(successful_runs),
        "runs": run_items,
        "notes": [
            "This manifest is a platform-level W7 artifact used to declare which run is recommended as the current frozen baseline.",
            "Project-level and platform-level promotions still require human review.",
        ],
    }


def write_baseline_freeze_manifest(
    results: list[dict[str, Any]],
    *,
    comparison_path: Path,
    platform_daily_report: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = build_baseline_freeze_manifest(
        results,
        comparison_path=comparison_path,
        platform_daily_report=platform_daily_report,
    )
    manifest_path = ARTIFACT_ROOT / "latest_baseline_freeze_manifest.json"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "path": str(manifest_path),
        "freeze_recommended": payload.get("freeze_recommended"),
        "recommended_primary_run": payload.get("recommended_primary_run"),
    }


def _summarize_round_input(round_input: object) -> str:
    round_index = getattr(round_input, "round_index", None)
    target_stage = getattr(round_input, "target_stage", None) or "verification"
    scheduled_clusters = len(getattr(round_input, "scheduled_cluster_ids", []) or [])
    scheduled_actions = len(getattr(round_input, "scheduled_action_ids", []) or [])
    if round_index is None:
        return "Round input was prepared for the current orchestration cycle."
    return (
        f"Round {round_index} will focus on {target_stage} with "
        f"{scheduled_clusters} scheduled cluster(s) and {scheduled_actions} scheduled action(s)."
    )


def summarize_execution_hints(execution_hints: dict[str, Any]) -> str:
    scheduled_strategies = execution_hints.get("scheduled_strategies") or []
    focus_stage = execution_hints.get("focus_stage") or "verification"
    if scheduled_strategies:
        return (
            f"Execution will focus on {focus_stage} and prefer retry strategies: "
            + ", ".join(str(item) for item in scheduled_strategies)
            + "."
        )
    if execution_hints.get("requires_human_review"):
        return "Execution hints indicate that human review is required before continuing."
    return f"Execution will focus on {focus_stage} using the carried orchestration hints."


def summarize_stop_conditions(stop_conditions: object) -> str:
    triggered = list(getattr(stop_conditions, "triggered_conditions", []) or [])
    if getattr(stop_conditions, "should_stop", None) is True:
        if triggered:
            return "Run will stop because these stop conditions were triggered: " + ", ".join(triggered) + "."
        return "Run will stop because one or more stop conditions were triggered."
    if getattr(stop_conditions, "should_stop", None) is None:
        return "Run cannot safely decide whether to stop or continue; manual review is required."
    return "No stop condition blocked the current run, so the orchestration loop may continue conservatively."


def summarize_next_round_decision(next_round: object) -> str:
    status = str(getattr(next_round, "status", "") or "").strip().lower()
    next_round_index = getattr(next_round, "next_round", None)
    target_stage = getattr(next_round, "target_stage", None) or "verification"
    primary_reason = getattr(next_round, "primary_reason", None)
    if status == "scheduled":
        prefix = f"Next round {next_round_index} is scheduled for {target_stage}."
        if primary_reason:
            return f"{prefix} {primary_reason}"
        return prefix
    if status == "stopped":
        return primary_reason or "Next-round scheduling stopped because a stop condition was triggered."
    if status == "needs_review":
        return primary_reason or "Next-round scheduling requires manual review before continuing."
    if status == "budget_exhausted":
        return primary_reason or "Next-round scheduling stopped because the attempt budget was exhausted."
    if status == "no_retry_needed":
        return primary_reason or "No next round is needed because no open failure cluster remains."
    return primary_reason or "Next-round decision was recorded, but no stronger explanation was available."


def build_retry_plan_section(iteration_artifacts: object) -> list[dict[str, Any]]:
    retry_plan = getattr(iteration_artifacts, "retry_plan", None)
    if retry_plan is None:
        return []
    facts: list[dict[str, Any]] = [{"label": "status", "value": retry_plan.status}]
    if retry_plan.next_round is not None:
        facts.append({"label": "next_round", "value": retry_plan.next_round})
    if retry_plan.goal:
        facts.append({"label": "goal", "value": retry_plan.goal})
    if retry_plan.stop_reason:
        facts.append({"label": "stop_reason", "value": retry_plan.stop_reason})
    items = [
        {
            "item_id": action.action_id,
            "name": action.title,
            "status": action.priority,
            "summary": action.reason,
            "owner": action.owner,
            "source": action.stage,
            "facts": [
                {"label": "strategy", "value": action.strategy},
                {"label": "expected_outcome", "value": action.expected_outcome},
            ]
            + [
                {"label": key, "value": value}
                for key, value in sorted((action.execution_hints or {}).items())
            ],
        }
        for action in retry_plan.actions
    ]
    return [
        {
            "title": "Retry Plan",
            "summary": (
                f"Prepared {len(retry_plan.actions)} retry action(s) for the next orchestration handoff."
                if retry_plan.actions
                else "No retry action was required for this run."
            ),
            "facts": facts,
            "items": items,
            "notes": retry_plan.notes,
        }
    ]


def build_iteration_comparison_section(iteration_artifacts: object) -> dict[str, Any] | None:
    comparison = getattr(iteration_artifacts, "iteration_comparison", None)
    if comparison is None:
        return None

    facts: list[dict[str, Any]] = [
        {"label": "status", "value": comparison.status},
        {"label": "improvement_judgement", "value": comparison.improvement_judgement},
    ]
    if comparison.previous_run_id:
        facts.append({"label": "previous_run_id", "value": comparison.previous_run_id})
    if comparison.no_improvement_streak_after is not None:
        facts.append(
            {
                "label": "no_improvement_streak_after",
                "value": comparison.no_improvement_streak_after,
            }
        )

    items: list[dict[str, Any]] = []
    for metric in getattr(comparison, "metrics", []):
        items.append(
            {
                "item_id": metric.metric_id,
                "name": metric.label,
                "status": metric.trend,
                "summary": metric.note or f"{metric.previous_value} -> {metric.current_value}",
                "facts": [
                    {"label": "previous_value", "value": metric.previous_value},
                    {"label": "current_value", "value": metric.current_value},
                    {"label": "delta", "value": metric.delta},
                ],
            }
        )
    for change in getattr(comparison, "cluster_changes", []):
        items.append(
            {
                "item_id": change.cluster_key,
                "name": f"{change.category} / {change.stage or 'unknown'}",
                "status": change.status,
                "summary": change.summary,
                "facts": [
                    {"label": "previous_signal_count", "value": change.previous_signal_count},
                    {"label": "current_signal_count", "value": change.current_signal_count},
                    {"label": "signal_delta", "value": change.signal_delta},
                ],
            }
        )

    return {
        "title": "Iteration Comparison",
        "summary": comparison.summary,
        "facts": facts,
        "items": items,
        "notes": comparison.notes,
    }


def build_round_input_section(iteration_artifacts: object) -> dict[str, Any] | None:
    round_input = getattr(iteration_artifacts, "round_input", None)
    if round_input is None:
        return None

    facts: list[dict[str, Any]] = [
        {"label": "orchestration_stream_id", "value": round_input.orchestration_stream_id},
        {"label": "template_name", "value": round_input.template_name},
        {"label": "model_name", "value": round_input.model_name},
        {"label": "project_name", "value": round_input.project_name},
        {"label": "round_index", "value": round_input.round_index},
        {"label": "max_rounds", "value": round_input.max_rounds},
        {"label": "target_stage", "value": round_input.target_stage},
        {"label": "goal", "value": round_input.goal},
        {"label": "source_decision_status", "value": round_input.source_decision_status},
        {"label": "source_decision_reason", "value": round_input.source_decision_reason},
        {"label": "previous_run_id", "value": round_input.previous_run_id},
        {"label": "scheduled_cluster_count", "value": len(round_input.scheduled_cluster_ids)},
        {"label": "scheduled_action_count", "value": len(round_input.scheduled_action_ids)},
    ]
    items: list[dict[str, Any]] = []
    for cluster in round_input.scheduled_cluster_ids:
        items.append(
            {
                "item_id": cluster,
                "name": cluster,
                "status": "scheduled",
                "summary": "Failure cluster carried into this round input.",
            }
        )
    for action_id in round_input.scheduled_action_ids:
        items.append(
            {
                "item_id": action_id,
                "name": action_id,
                "status": "scheduled",
                "summary": "Retry action carried into this round input.",
            }
        )
    return {
        "title": "Round Input",
        "summary": _summarize_round_input(round_input),
        "facts": [item for item in facts if item["value"] not in (None, [], {})],
        "items": items,
        "notes": list(getattr(round_input, "notes", []) or []),
    }


def build_execution_hints_section(iteration_artifacts: object) -> dict[str, Any] | None:
    round_input = getattr(iteration_artifacts, "round_input", None)
    if round_input is None:
        return None
    execution_hints = getattr(round_input, "execution_hints", None) or {}
    if not execution_hints:
        return None

    items: list[dict[str, Any]] = []
    for cluster in execution_hints.get("scheduled_clusters") or []:
        if not isinstance(cluster, dict):
            continue
        items.append(
            {
                "item_id": cluster.get("cluster_id"),
                "name": cluster.get("category") or cluster.get("cluster_id") or "scheduled_cluster",
                "status": "scheduled",
                "summary": "Execution hints retained this cluster for the next round.",
                "owner": cluster.get("owner"),
                "source": cluster.get("stage"),
                "facts": [
                    {"label": "strategy", "value": cluster.get("strategy")},
                    {"label": "action_level", "value": cluster.get("action_level")},
                ],
            }
        )

    facts = [
        {"label": key, "value": value}
        for key, value in sorted(execution_hints.items())
        if key != "scheduled_clusters"
    ]
    return {
        "title": "Execution Hints",
        "summary": summarize_execution_hints(execution_hints),
        "facts": [item for item in facts if item["value"] not in (None, [], {})],
        "items": items,
        "notes": [
            "Execution hints are advisory orchestration inputs passed into the next round.",
        ],
    }


def build_stop_conditions_section(iteration_artifacts: object) -> dict[str, Any] | None:
    stop_conditions = getattr(iteration_artifacts, "stop_conditions", None)
    if stop_conditions is None:
        return None

    facts: list[dict[str, Any]] = [
        {"label": "status", "value": stop_conditions.status},
        {"label": "should_stop", "value": stop_conditions.should_stop},
        {"label": "primary_reason", "value": stop_conditions.primary_reason},
        {"label": "no_improvement_streak", "value": stop_conditions.no_improvement_streak},
    ]
    items = [
        {
            "item_id": condition.condition_id,
            "name": condition.condition_type,
            "status": condition.status,
            "summary": condition.summary,
            "facts": [
                {"label": "stop", "value": condition.stop},
                {"label": "evidence_count", "value": len(condition.evidence)},
            ],
            "notes": condition.notes,
        }
        for condition in getattr(stop_conditions, "conditions", [])
    ]
    return {
        "title": "Stop Conditions",
        "summary": summarize_stop_conditions(stop_conditions),
        "facts": facts,
        "items": items,
        "notes": stop_conditions.notes,
        "triggered_conditions": list(getattr(stop_conditions, "triggered_conditions", [])),
    }


def build_next_round_decision_section(iteration_artifacts: object) -> dict[str, Any] | None:
    next_round = getattr(iteration_artifacts, "next_round_decision", None)
    if next_round is None:
        return None

    facts: list[dict[str, Any]] = [
        {"label": "status", "value": next_round.status},
        {"label": "should_start_next_round", "value": next_round.should_start_next_round},
        {"label": "current_round", "value": next_round.current_round},
        {"label": "next_round", "value": next_round.next_round},
        {"label": "target_stage", "value": next_round.target_stage},
        {"label": "primary_reason", "value": next_round.primary_reason},
        {"label": "remaining_attempt_budget", "value": next_round.remaining_attempt_budget},
    ]
    items = [
        {
            "item_id": cluster_id,
            "name": cluster_id,
            "status": "scheduled",
            "summary": "Failure cluster scheduled for the next round.",
        }
        for cluster_id in getattr(next_round, "scheduled_cluster_ids", [])
    ]
    items.extend(
        {
            "item_id": action_id,
            "name": action_id,
            "status": "scheduled",
            "summary": "Retry action scheduled for the next round.",
        }
        for action_id in getattr(next_round, "scheduled_action_ids", [])
    )
    items.extend(
        {
            "item_id": cluster_id,
            "name": cluster_id,
            "status": "deferred",
            "summary": "Failure cluster remained open but was not scheduled for this next round.",
        }
        for cluster_id in getattr(next_round, "deferred_cluster_ids", [])
    )
    return {
        "title": "Next Round Decision",
        "summary": summarize_next_round_decision(next_round),
        "facts": facts,
        "items": items,
        "notes": getattr(next_round, "notes", []),
        "triggered_stop_conditions": list(getattr(next_round, "triggered_stop_conditions", [])),
    }


async def reset_online_apply_page(page: Page) -> dict[str, Any]:
    await page.goto(ONLINE_RECORD_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], .el-button'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el))
            .filter(Boolean)
            .slice(0, 80);
          return {
            url: location.href,
            title: document.title,
            buttons,
            body: text(document.body).slice(0, 2500),
          };
        }
        """
    )


async def find_open_panel(page: Page) -> Locator | None:
    dialog = page.locator(".el-dialog:visible")
    if await dialog.count():
        return dialog.last
    drawer = page.locator(".el-drawer__wrapper:visible, .el-drawer:visible")
    if await drawer.count():
        return drawer.last
    return None


async def close_visible_panel(page: Page) -> dict[str, Any]:
    panel = await find_open_panel(page)
    if panel is None:
        return {"ok": True, "closed": False}
    close_button = page.locator(
        ".el-dialog__headerbtn:visible, .el-drawer__close-btn:visible, [aria-label*='close']:visible"
    ).last
    if await close_button.count():
        await close_button.click(force=True)
        await page.wait_for_timeout(800)
        return {"ok": True, "closed": True, "method": "close_button"}
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    return {"ok": True, "closed": True, "method": "escape"}


async def get_apply_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          if (!apply) {
            return { ok: false, reason: 'apply-not-found' };
          }
          return {
            ok: true,
            title: apply.title,
            isEdit: !!apply.isEdit,
            isModified: !!apply.isModified,
            isPendingEditMode: !!apply.isPendingEditMode,
            isPendingPayment: !!apply.isPendingPayment,
            isShowEditButton: !!apply.isShowEditButton,
            isShowSaveButtons: !!apply.isShowSaveButtons,
            submitButtonText: apply.submitButtonText,
            currentId: apply.currentId,
            detailId: apply.detailId,
            auditStatus: apply.auditStatus,
            committed: !!apply.committed,
            typeFlags: {
              isCultivationType: !!apply.isCultivationType,
              isHardeningType: !!apply.isHardeningType,
              isPlantingType: !!apply.isPlantingType,
              isHarvestType: !!apply.isHarvestType,
              isPenjingType: !!apply.isPenjingType,
            },
            form: {
              registerType: apply.form?.registerType,
              type: apply.form?.type,
              institutionId: apply.form?.institutionId,
              institutionUserId: apply.form?.institutionUserId,
              plantId: apply.form?.plantId,
              batchNo: apply.form?.batchNo,
              cityRegionId: apply.form?.cityRegionId,
              cityRegionName: apply.form?.cityRegionName,
              rangeStr: apply.form?.rangeStr,
              deptId: apply.form?.deptId,
              acceptanceDeptId: apply.form?.acceptanceDeptId,
              acceptancePerson: apply.form?.acceptancePerson,
              acceptanceDate: apply.form?.acceptanceDate,
            },
          };
        }
        """
    )


async def snapshot_dialog_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const dialog = Array.from(document.querySelectorAll('.el-dialog')).find(el => el.offsetParent !== null);
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const errors = Array.from(document.querySelectorAll('.el-form-item__error'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const required = ['备案品种', '育苗开始日期', '育苗地点', '验收日期', '育苗人员信息表', '验收文件'];
          const requiredPresent = Object.fromEntries(required.map(label => [label, text(dialog).includes(label)]));
          const fields = Array.from((dialog || document).querySelectorAll('input, textarea, select'))
            .map((el) => ({
              tag: el.tagName,
              type: el.type || '',
              name: el.name || '',
              placeholder: el.placeholder || '',
              value: el.value || '',
              disabled: !!el.disabled,
              visible: el.offsetParent !== null,
              className: typeof el.className === 'string' ? el.className : '',
              parentText: text(el.parentElement).slice(0, 200),
            }));
          const fileInputs = Array.from((dialog || document).querySelectorAll('input[type=file]'))
            .map((el, idx) => ({
              idx,
              accept: el.accept || '',
              multiple: !!el.multiple,
              visible: el.offsetParent !== null,
              parentText: text(el.parentElement).slice(0, 200),
              grandText: text(el.parentElement?.parentElement).slice(0, 280),
            }));
          return {
            url: location.href,
            title: document.title,
            dialogText: text(dialog).slice(0, 2500),
            errors,
            requiredPresent,
            fields,
            fileInputs
          };
        }
        """
    )


async def snapshot_drawer_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const drawer = Array.from(document.querySelectorAll('.el-drawer, .el-drawer__wrapper'))
            .find(el => el.offsetParent !== null) || document;
          const errors = Array.from(drawer.querySelectorAll('.el-form-item__error, .el-alert__content'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const toast = Array.from(document.querySelectorAll('.el-message, .el-notification'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const requiredLabels = Array.from(drawer.querySelectorAll('.el-form-item.is-required .el-form-item__label'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const inputs = Array.from(drawer.querySelectorAll('input, textarea, .el-input__inner'))
            .filter(el => el.offsetParent !== null)
            .map(el => ({
              tag: el.tagName,
              value: el.value || '',
              placeholder: el.placeholder || '',
              className: typeof el.className === 'string' ? el.className : '',
            }))
            .slice(0, 120);
          return {
            title: document.title,
            body: text(drawer).slice(0, 9000),
            errors,
            toast,
            requiredLabels,
            inputs,
          };
        }
        """
    )


async def snapshot_submit_dialog(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const dialog = Array.from(document.querySelectorAll('.el-dialog, .el-message-box__wrapper'))
            .find(el => el.offsetParent !== null);
          const messages = Array.from(document.querySelectorAll('.el-message, .el-notification, .el-alert__content'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const fileInputs = dialog ? Array.from(dialog.querySelectorAll('input[type=file]')).map((el, idx) => ({
            idx,
            accept: el.accept || '',
            multiple: !!el.multiple,
            visible: el.offsetParent !== null,
          })) : [];
          return {
            dialogText: text(dialog).slice(0, 3000),
            messages,
            fileInputs,
            body: text(document.body).slice(0, 12000),
          };
        }
        """
    )


async def fill_select_by_label(page: Page, label: str, choice_text: str) -> dict[str, Any]:
    js = """
    async ({ label, choiceText }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      function findLabelNode(targetLabel) {
        const labels = Array.from(document.querySelectorAll('.el-form-item__label'));
        return labels.find(node => text(node).includes(targetLabel));
      }
      const labelNode = findLabelNode(label);
      if (!labelNode) return { ok: false, reason: 'label-not-found' };
      const formItem = labelNode.closest('.el-form-item');
      if (!formItem) return { ok: false, reason: 'form-item-not-found' };
      const trigger = formItem.querySelector('.el-select .el-input__inner, .el-cascader .el-input__inner');
      if (!trigger) return { ok: false, reason: 'trigger-not-found' };
      trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      trigger.click();
      await new Promise(resolve => setTimeout(resolve, 600));
      const options = Array.from(document.querySelectorAll('.el-select-dropdown__item, .el-cascader-node'))
        .filter(node => node.offsetParent !== null);
      const target = options.find(node => text(node).includes(choiceText));
      if (!target) {
        return {
          ok: false,
          reason: 'option-not-found',
          visibleOptions: options.map(node => text(node)).slice(0, 20),
        };
      }
      target.click();
      return { ok: true, chosen: text(target) };
    }
    """
    return await page.evaluate(js, {"label": label, "choiceText": choice_text})


async def set_plant_by_component(page: Page, plant_name: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        async ({ plantName }) => {
          const input = document.querySelector('.el-form-item input[placeholder="请输入备案品种"]');
          const plantComp = input?.parentElement?.parentElement?.parentElement?.__vue__;
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          if (!plantComp || !apply) return { ok: false, reason: 'plant-component-not-found' };
          await plantComp.getList(plantName);
          const target = (plantComp.list || []).find(item => (item.plantName || '').includes(plantName));
          if (!target) {
            return {
              ok: false,
              reason: 'plant-not-found',
              loaded: (plantComp.list || []).map(item => item.plantName || item.label).slice(0, 20),
            };
          }
          apply.plantChange(target);
          const field = apply.$refs?.form?.fields?.find(f => f.prop === 'plantId');
          if (field) {
            field.validateState = 'success';
            field.validateMessage = '';
            field.onFieldChange && field.onFieldChange();
          }
          return {
            ok: true,
            plantId: apply.form.plantId,
            plantName: apply.form.plantName,
            target,
          };
        }
        """,
        {"plantName": plant_name},
    )


async def fill_date_by_label(page: Page, label: str, value: str) -> dict[str, Any]:
    js = """
    ({ label, value }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      const labels = Array.from(document.querySelectorAll('.el-form-item__label'));
      const labelNode = labels.find(node => text(node).includes(label));
      if (!labelNode) return { ok: false, reason: 'label-not-found' };
      const formItem = labelNode.closest('.el-form-item');
      if (!formItem) return { ok: false, reason: 'form-item-not-found' };
      const input = formItem.querySelector('input');
      if (!input) return { ok: false, reason: 'input-not-found' };
      input.focus();
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
      return { ok: true, value: input.value };
    }
    """
    return await page.evaluate(js, {"label": label, "value": value})


async def set_city_by_component(page: Page, city_code: str, city_label: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ cityCode, cityLabel }) => {
          const input = document.querySelector('.el-form-item input[placeholder="请选择"]');
          const cityComp = input?.parentElement?.parentElement?.parentElement?.__vue__;
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const cultivation = apply?.$refs?.formCultivation;
          if (!cityComp || !apply || !cultivation) return { ok: false, reason: 'city-component-not-found' };
          cityComp.value = cityCode;
          cityComp.valueStr = cityCode;
          cultivation.handleAddressChange({ value: cityCode, label: cityLabel });
          const field = apply.$refs?.form?.fields?.find(f => f.prop === 'cityRegionId');
          if (field) {
            field.validateState = 'success';
            field.validateMessage = '';
            field.onFieldChange && field.onFieldChange();
          }
          const inputEl = input;
          if (inputEl) {
            inputEl.removeAttribute('readonly');
            inputEl.value = cityLabel;
            inputEl.dispatchEvent(new Event('input', { bubbles: true }));
            inputEl.dispatchEvent(new Event('change', { bubbles: true }));
            inputEl.setAttribute('readonly', 'readonly');
          }
          return {
            ok: true,
            cityRegionId: apply.form.cityRegionId,
            cityRegionName: apply.form.cityRegionName,
          };
        }
        """,
        {"cityCode": city_code, "cityLabel": city_label},
    )


async def fill_text_by_label(page: Page, label: str, value: str) -> dict[str, Any]:
    js = """
    ({ label, value }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      const labels = Array.from(document.querySelectorAll('.el-form-item__label'));
      const labelNode = labels.find(node => text(node).includes(label));
      if (!labelNode) return { ok: false, reason: 'label-not-found' };
      const formItem = labelNode.closest('.el-form-item');
      if (!formItem) return { ok: false, reason: 'form-item-not-found' };
      const input = formItem.querySelector('input, textarea');
      if (!input) return { ok: false, reason: 'input-not-found' };
      input.focus();
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
      return { ok: true, value: input.value };
    }
    """
    return await page.evaluate(js, {"label": label, "value": value})


async def set_date_and_validate(page: Page, label: str, value: str, prop: str) -> dict[str, Any]:
    result = await fill_date_by_label(page, label, value)
    validate = await page.evaluate(
        """
        ({ prop }) => {
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const field = apply?.$refs?.form?.fields?.find(f => f.prop === prop);
          if (!field) return { ok: false, reason: 'field-not-found' };
          field.validateState = 'success';
          field.validateMessage = '';
          field.onFieldChange && field.onFieldChange();
          return { ok: true, validateState: field.validateState, validateMessage: field.validateMessage };
        }
        """,
        {"prop": prop},
    )
    return {"input": result, "validate": validate}


async def ensure_checkbox(page: Page, label_text: str) -> dict[str, Any]:
    js = """
    ({ labelText }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      const labels = Array.from(document.querySelectorAll('label, span, div'));
      const target = labels.find(node => node.offsetParent !== null && text(node).includes(labelText));
      if (!target) return { ok: false, reason: 'label-not-found' };
      const checkbox = target.closest('label')?.querySelector('input[type=checkbox]') || target.parentElement?.querySelector('input[type=checkbox]');
      if (!checkbox) return { ok: false, reason: 'checkbox-not-found' };
      if (!checkbox.checked) {
        checkbox.click();
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return { ok: true, checked: checkbox.checked };
    }
    """
    return await page.evaluate(js, {"labelText": label_text})


async def upload_by_label(page: Page, label: str, file_path: Path, expected_prop: str) -> dict[str, Any]:
    file_path = file_path.resolve()
    form_item = page.locator(".el-form-item", has=page.locator(".el-form-item__label", has_text=label)).first
    input = form_item.locator("input[type=file]").first
    await input.set_input_files(str(file_path))
    await page.wait_for_timeout(2500)
    state = await page.evaluate(
        """
        ({ prop }) => {
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const field = apply?.$refs?.form?.fields?.find(f => f.prop === prop);
          const value = apply?.form?.[prop];
          return {
            value,
            fieldState: field ? { validateState: field.validateState, validateMessage: field.validateMessage } : null,
          };
        }
        """,
        {"prop": expected_prop},
    )
    return {"ok": True, "label": label, "file": str(file_path), "state": state}


async def set_attachment_validation_success(page: Page, prop: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ prop }) => {
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const field = apply?.$refs?.form?.fields?.find(f => f.prop === prop);
          if (!field) return { ok: false, reason: 'field-not-found' };
          field.validateState = 'success';
          field.validateMessage = '';
          field.onFieldChange && field.onFieldChange();
          return { ok: true, validateState: field.validateState, validateMessage: field.validateMessage, value: apply.form[prop] };
        }
        """,
        {"prop": prop},
    )


async def fill_success_template(page: Page, template: dict[str, Any]) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ template }) => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          if (!apply) return { ok: false, reason: 'apply-not-found' };
          Object.assign(apply.form, template);
          if (apply.$refs?.formCultivation?.handleAddressChange) {
            apply.$refs.formCultivation.handleAddressChange({
              value: template.cityRegionId,
              label: template.cityRegionName,
            });
          }
          if (apply.$refs?.formCultivation?.handleDeptChange) {
            apply.$refs.formCultivation.handleDeptChange(template.acceptanceDeptId);
          }
          for (const field of (apply.$refs?.form?.fields || [])) {
            if (!field?.prop) continue;
            const value = apply.form[field.prop];
            if (value !== '' && value !== null && value !== undefined) {
              field.validateState = '';
              field.validateMessage = '';
            }
          }
          return {
            ok: true,
            form: {
              deptId: apply.form.deptId,
              cityRegionId: apply.form.cityRegionId,
              cityRegionName: apply.form.cityRegionName,
              rangeStr: apply.form.rangeStr,
              seedlingSource: apply.form.seedlingSource,
              cultivateType: apply.form.cultivateType,
              cultivateDate: apply.form.cultivateDate,
              cultivateNum: apply.form.cultivateNum,
              cultivateArea: apply.form.cultivateArea,
              acceptanceNum: apply.form.acceptanceNum,
              cultivatePurpose: apply.form.cultivatePurpose,
              acceptanceDeptId: apply.form.acceptanceDeptId,
              acceptancePerson: apply.form.acceptancePerson,
              acceptanceDate: apply.form.acceptanceDate,
              batchNo: apply.form.batchNo,
            },
          };
        }
        """,
        {"template": template},
    )


def build_verified_new_application_registry() -> TemplateActionRegistry:
    registry = TemplateActionRegistry()
    register_suyuan_wizard_drawer_actions(registry)

    async def handle_fill_success_template(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        ref = step.get("args", {}).get("data_ref", "")
        data = runtime.resolve_ref(ref) if ref else {}
        if data is None:
            template_data: dict[str, Any] = {}
        elif isinstance(data, Mapping):
            template_data = dict(data)
        else:
            raise RuntimeError(
                f"fill_success_template_invalid_data: data_ref={ref or '<empty>'} expected mapping, got {type(data).__name__}"
            )
        return await fill_success_template(page, template_data)

    async def handle_submit_filing_dialog(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        decision = build_submit_action_policy_decision(
            action_id=str(step.get("id") or "submit_filing_dialog"),
            template_name=TEMPLATE_BUNDLE.name,
            project_name=STAGE2_PROJECT_NAME,
        )
        if decision.status != POLICY_ALLOWED:
            category = "policy_review_required" if decision.needs_review else "policy_blocked"
            raise RuntimeError(f"{category}: {decision.reason}")
        result = await submit_filing_dialog(page)
        result["policy_decision"] = decision.to_dict()
        return result

    registry.register("fill_success_template", handle_fill_success_template)
    register_suyuan_submit_dialog_actions(
        registry,
        submit_filing_dialog_handler=handle_submit_filing_dialog,
    )
    return registry


async def execute_verified_new_application_flow(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[TemplateStepExecution]]:
    executor = TemplateFlowExecutor(build_verified_new_application_registry())
    executions = await executor.execute(
        page=page,
        artifacts=artifacts,
        runtime=runtime,
        template=TEMPLATE_BUNDLE.template,
    )
    actions: list[dict[str, Any]] = []
    for execution in executions:
        actions.append(execution.to_attempt_action())
        if execution.step_id == "check_initial_promise":
            await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_initial_form.png"), full_page=True)
        elif execution.step_id == "upload_required_files":
            await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_full_form.png"), full_page=True)
        elif execution.step_id == "upload_apply_form":
            await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_submit_dialog.png"), full_page=True)

    await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_final_result.png"), full_page=True)
    final_state = await snapshot_submit_dialog(page)
    return actions, final_state, executions


async def dismiss_overlays(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const visible = Array.from(document.querySelectorAll('body > div'))
            .filter(node => node.offsetParent !== null)
            .map(node => ({
              cls: typeof node.className === 'string' ? node.className : '',
              text: (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
            }));
          document.body.click();
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
          return { ok: true, visible };
        }
        """
    )


async def submit_dialog(page: Page) -> dict[str, Any]:
    decision = build_submit_action_policy_decision(
        action_id="submit_online_apply_dialog",
        template_name=TEMPLATE_BUNDLE.name,
        project_name=STAGE2_PROJECT_NAME,
    )
    if decision.status != POLICY_ALLOWED:
        category = "policy_review_required" if decision.needs_review else "policy_blocked"
        raise RuntimeError(f"{category}: {decision.reason}")
    found = await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const buttons = Array.from(document.querySelectorAll('button')).filter(btn => btn.offsetParent !== null);
          const target = buttons.find(btn => text(btn).includes('信息纳入溯源系统'));
          if (!target) {
            return { ok: false, visibleButtons: buttons.map(text) };
          }
          target.click();
          return { ok: true, text: text(target) };
        }
        """
    )
    if not found.get("ok"):
        raise RuntimeError(f"未找到“信息纳入溯源系统”提交按钮: {found.get('visibleButtons', [])}")
    await page.wait_for_timeout(2500)
    result = await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const messages = Array.from(document.querySelectorAll('.el-message, .el-notification, .el-message-box'))
            .filter(node => node.offsetParent !== null)
            .map(node => text(node))
            .filter(Boolean);
          const errors = Array.from(document.querySelectorAll('.el-form-item__error'))
            .filter(node => node.offsetParent !== null)
            .map(node => text(node))
            .filter(Boolean);
          return {
            messages,
            errors,
            bodySnippet: document.body.innerText.slice(0, 2000)
          };
        }
        """
    )
    result["policy_decision"] = decision.to_dict()
    return result


def success_from_submission(result: dict[str, Any]) -> bool:
    joined = " ".join(result.get("messages", []))
    body = result.get("bodySnippet", "")
    error_text = " ".join(result.get("errors", []))

    if any(
        token in " ".join([joined, body, error_text])
        for token in [
            "操作失败",
            "无法新增备案信息",
            "primaryValues array can not be null or empty",
            "请先勾选",
            "失败",
            "异常",
        ]
    ):
        return False

    if any(key in joined for key in ["成功", "已提交", "提交成功", "保存成功"]):
        return True
    if "提交完成，待备案登记/监管单位登记备案" in body:
        return True
    if result.get("errors"):
        return False
    return False


def classify_submission_result(
    submit_result: dict[str, Any],
    network_events: list[dict[str, Any]],
    apply_state: dict[str, Any],
) -> dict[str, Any]:
    joined = " ".join(submit_result.get("messages", []))
    body = submit_result.get("bodySnippet", "")
    all_text = " ".join([joined, body, " ".join(submit_result.get("errors", []))])

    if any(token in all_text for token in ["policy_blocked", "risky_submit_unlisted_blocked"]):
        return {
            "success": False,
            "category": "policy_blocked",
            "reason": "高风险真实提交未在项目级白名单中显式允许，执行层已阻断提交动作",
            "latest_request": None,
            "latest_response": None,
        }

    if any(token in all_text for token in ["policy_review_required", "risky_submit_unlisted_review"]):
        return {
            "success": False,
            "category": "policy_review_required",
            "reason": "高风险真实提交需要人工审核后才能继续，执行层未自动提交",
            "latest_request": None,
            "latest_response": None,
        }

    registration_events = [
        item for item in network_events if "/prod-api/zwsy/registration/apply/" in item.get("url", "")
    ]
    response_events = [item for item in registration_events if item.get("type") == "response"]
    request_events = [item for item in registration_events if item.get("type") == "request"]

    latest_apply_response = response_events[-1] if response_events else None
    latest_apply_request = request_events[-1] if request_events else None
    submit_request = None
    submit_response = None
    for request in reversed(request_events):
        if request.get("method") == "POST" and any(
            request.get("url", "").endswith(suffix) for suffix in ["/save", "/update", "/dept"]
        ):
            submit_request = request
            break
    if submit_request:
        target_url = submit_request.get("url")
        for response in reversed(response_events):
            if response.get("url") == target_url:
                submit_response = response
                break
    submit_response_body = (submit_response or {}).get("body", "")

    if any(token in " ".join([all_text, submit_response_body]) for token in ["当前用户无机构信息，无法新增备案信息", "无法新增备案信息"]):
        return {
            "success": False,
            "category": "account_policy_block",
            "reason": "账号缺少新增备案所需机构信息，新增分支被后台拒绝",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if any(token in " ".join([all_text, submit_response_body]) for token in ["primaryValues array can not be null or empty"]):
        return {
            "success": False,
            "category": "backend_update_primary_key_error",
            "reason": "编辑更新分支触发后台主键依赖异常",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if any(token in all_text for token in ['请先勾选"本人承诺', "请先勾选"]):
        return {
            "success": False,
            "category": "front_validation_missing_commitment",
            "reason": "提交前未满足承诺勾选条件",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if submit_response:
        body_text = submit_response.get("body", "")
        if any(token in body_text for token in ["\"code\":200", "\"msg\":\"操作成功\"", "\"msg\":\"提交成功\""]):
            category = "network_success"
            reason = "后台接口响应成功"
            if submit_request and submit_request.get("url", "").endswith("/dept"):
                category = "network_success_final_filing"
                reason = "最终备案提交接口响应成功"
            return {
                "success": True,
                "category": category,
                "reason": reason,
                "latest_request": submit_request,
                "latest_response": submit_response,
            }

    if success_from_submission(submit_result):
        return {
            "success": True,
            "category": "ui_success",
            "reason": "页面成功提示命中",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if apply_state.get("ok") and apply_state.get("isPendingPayment") and apply_state.get("isShowSaveButtons"):
        return {
            "success": False,
            "category": "pending_payment_modify_mode",
            "reason": "已进入待支付记录的修改态，需走提交申请/支付分支而非 update",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    return {
        "success": False,
        "category": "unknown_failure",
        "reason": "未命中已知成功或失败模式",
        "latest_request": submit_request or latest_apply_request,
        "latest_response": submit_response or latest_apply_response,
    }


def write_structured_stage2_report(
    artifacts: ArtifactWriter,
    profile: ModelProfile,
    progress: ProgressManager,
    *,
    page_url: str,
    success: bool,
    classification: dict[str, Any],
    attempts: list[dict[str, Any]],
    generated_files: dict[str, Path],
    discovery_result: object | None = None,
    discovery_paths: dict[str, Path] | None = None,
    notes: list[str] | None = None,
    max_attempts: int | None = None,
    round_input: dict[str, Any] | None = None,
    capability_gate: CapabilityGateDecision | None = None,
    capability_routing: CapabilityRoutingDecision | None = None,
    run_policy_resolution: RunPolicyLoadResult | None = None,
    discovery_strategy: DiscoveryStrategyDecision | None = None,
    policy_decision: PolicyGateDecision | None = None,
) -> None:
    progress_snapshot = adapt_progress_snapshot(progress.snapshot)
    total_duration_ms = total_attempt_duration_ms(attempts)
    resolved_round_input = round_input or {}
    iteration_budget = _configured_round_limit(resolved_round_input, max_attempts)
    resolved_capability_gate = capability_gate or _coerce_capability_gate_from_payload(artifacts.run_dir)
    resolved_capability_routing = capability_routing or _coerce_capability_routing_from_payload(artifacts.run_dir)
    resolved_run_policy = run_policy_resolution or load_run_policy_resolution()
    resolved_discovery_strategy = discovery_strategy or _coerce_discovery_strategy_from_payload(artifacts.run_dir)
    selected_discovery_mode = (
        resolved_capability_routing.discovery.recommended_mode
        if resolved_capability_routing and resolved_capability_routing.discovery
        else None
    )
    selected_verification_mode = (
        resolved_capability_routing.verification.recommended_mode
        if resolved_capability_routing and resolved_capability_routing.verification
        else None
    )
    route_stages = [
        stage
        for stage in ("discovery", "verification", "reporting")
        if getattr(resolved_capability_routing, stage, None) is not None
        and getattr(getattr(resolved_capability_routing, stage), "allowed", False)
    ]
    report_payload = {
        "summary": {
            "run_id": artifacts.run_dir.name,
            "status": "completed" if success else "failed",
            "project_name": "AI Agent 软件自动化评测平台第二阶段原型",
            "template_name": TEMPLATE_BUNDLE.name,
            "started_at": progress.snapshot.started_at,
            "finished_at": progress.snapshot.updated_at,
            "duration_seconds": round(total_duration_ms / 1000, 3) if total_duration_ms else None,
            "current_round": resolved_round_input.get("round_index") or progress_snapshot.current_round,
            "stop_reason": classification.get("reason"),
            "next_action": progress.snapshot.next_action,
            "verification_max_attempts": max_attempts,
            "orchestration_max_rounds": iteration_budget,
            "counts": [
                {"label": "attempts", "value": len(attempts)},
                {"label": "template_steps", "value": len(TEMPLATE_BUNDLE.template.get("steps", []))},
            ],
            "facts": [
                {"label": "page_url", "value": page_url},
                {"label": "model_name", "value": profile.name},
                {"label": "classification_category", "value": classification.get("category")},
                {"label": "orchestration_stream_id", "value": resolved_round_input.get("orchestration_stream_id")},
                {"label": "orchestration_round", "value": resolved_round_input.get("round_index")},
                {"label": "capability_preflight_status", "value": resolved_capability_gate.status if resolved_capability_gate else None},
                {"label": "capability_preflight_reason_code", "value": resolved_capability_gate.reason_code if resolved_capability_gate else None},
                {"label": "discovery_route_mode", "value": selected_discovery_mode},
                {"label": "discovery_strategy", "value": resolved_discovery_strategy.selected_strategy if resolved_discovery_strategy else None},
                {"label": "verification_route_mode", "value": selected_verification_mode},
                {"label": "run_policy_load_status", "value": resolved_run_policy.load_status},
                {"label": "run_policy_default_decision", "value": resolved_run_policy.risky_submit_default_decision},
            ],
        },
        "page_entries": build_report_page_entries(
            discovery_result,
            TEMPLATE_BUNDLE.template.get("page_entry", {}).get("name", "页面入口"),
            page_url,
        ),
        "feature_points": build_report_feature_points(
            discovery_result,
            TEMPLATE_BUNDLE.template.get("feature_point", {}).get("name", "功能点"),
        ),
        "success_items": [
            {
                "name": "线上申请备案模板样本",
                "status": "passed",
                "summary": classification.get("reason"),
            },
            {
                "item_id": TEMPLATE_BUNDLE.template.get("feature_point", {}).get("name", "online_apply"),
                "name": TEMPLATE_BUNDLE.template.get("feature_point", {}).get("name", "线上申请备案"),
                "status": "passed",
                "summary": classification.get("reason"),
                "facts": [
                    {"label": "template_execution_path", "value": TEMPLATE_BUNDLE.template.get("execution_path")},
                    {"label": "attempt_count", "value": len(attempts)},
                ],
            },
        ]
        if success
        else [],
        "failure_items": []
        if success
        else [
            {
                "item_id": classification.get("category"),
                "name": "线上申请备案模板样本",
                "status": "failed",
                "summary": classification.get("reason"),
                "tags": [classification.get("category")] if classification.get("category") else [],
            }
        ],
        "key_artifacts": build_report_key_artifacts(artifacts),
        "project_assets": [
            {
                "name": "Template Snapshot",
                "status": "generated",
                "artifacts": [
                    {"label": "template_snapshot.json", "path": str(artifacts.run_dir / "template_snapshot.json")}
                ],
            },
            {
                "name": "Runtime Data",
                "status": "generated",
                "artifacts": [
                    {"label": "runtime_data.json", "path": str(artifacts.run_dir / "runtime_data.json")}
                ],
            },
            {
                "name": "Generated Attachments",
                "status": "generated",
                "artifacts": [
                    {"label": key, "path": str(path)} for key, path in generated_files.items()
                ],
            },
            {
                "name": "Key Screenshots",
                "status": "generated",
                "artifacts": build_report_key_artifacts(artifacts),
            },
        ],
        "network_highlights": [
            {
                "name": f"Attempt {item.get('attempt')}",
                "status": item.get("classification", {}).get("category", "unknown"),
                "summary": item.get("classification", {}).get("reason", ""),
                "facts": [
                    {"label": "attempt_status", "value": item.get("status")},
                    {"label": "step_count", "value": item.get("execution_summary", {}).get("step_count")},
                    {"label": "duration_ms", "value": item.get("execution_summary", {}).get("duration_ms")},
                ],
            }
            for item in attempts
        ],
        "efficiency_observations": [
            {"label": "attempt_count", "value": len(attempts)},
            {"label": "platform_status", "value": progress.snapshot.overall_status},
            {
                "label": "template_execution_duration_ms",
                "value": total_duration_ms,
            },
        ],
        "model_evaluations": [
            {
                "model_name": profile.name,
                "summary": classification.get("reason"),
                "precheck_tags": (
                    sorted(
                        key
                        for key, enabled in (resolved_capability_routing.capability_tags if resolved_capability_routing else {}).items()
                        if enabled
                    )
                ),
                "participated_stages": route_stages or ["verification"],
                "joined_discovery": bool(
                    resolved_capability_routing
                    and resolved_capability_routing.discovery
                    and resolved_capability_routing.discovery.allowed
                ),
                "joined_attribution": True,
                "comparison_summary": (
                    "Single-run observation; cross-model comparison requires multiple completed runs."
                    if not resolved_capability_routing
                    else (
                        f"Discovery routed via {selected_discovery_mode or 'unknown'}; "
                        f"verification routed via {selected_verification_mode or 'unknown'}."
                    )
                ),
                "structured_output_stability": (
                    "wrapper_ready"
                    if resolved_capability_routing
                    and resolved_capability_routing.discovery
                    and "browser_use" in resolved_capability_routing.discovery.recommended_mode
                    else (
                        "not_required_for_current_verification_path"
                        if resolved_capability_routing
                        and resolved_capability_routing.verification
                        and resolved_capability_routing.verification.allowed
                        else None
                    )
                ),
                "recommended_role": (
                    "controlled_discovery"
                    if resolved_capability_routing
                    and resolved_capability_routing.discovery
                    and resolved_capability_routing.discovery.allowed
                    else (
                        "verification"
                        if resolved_capability_routing
                        and resolved_capability_routing.verification
                        and resolved_capability_routing.verification.allowed
                        else None
                    )
                ),
                "facts": [
                    {"label": "attempt_count", "value": len(attempts)},
                    {"label": "final_category", "value": classification.get("category")},
                    {"label": "capability_gate_reason_code", "value": resolved_capability_gate.reason_code if resolved_capability_gate else None},
                    {"label": "discovery_route_summary", "value": _routing_stage_summary(resolved_capability_routing, "discovery") if resolved_capability_routing else None},
                    {"label": "verification_route_summary", "value": _routing_stage_summary(resolved_capability_routing, "verification") if resolved_capability_routing else None},
                ],
            }
        ],
        "notes": (notes or []) + [
            f"当前平台级状态：{progress.snapshot.overall_status} / {progress.snapshot.current_phase}",
            format_status_line(progress.snapshot),
        ],
    }
    if resolved_round_input:
        report_payload["round_input"] = resolved_round_input
        report_payload["summary"]["orchestration_stream_id"] = resolved_round_input.get("orchestration_stream_id")
        report_payload["summary"]["orchestration_round"] = resolved_round_input.get("round_index")
        report_payload["summary"]["previous_run_id"] = resolved_round_input.get("previous_run_id")
        report_payload["summary"]["target_stage"] = resolved_round_input.get("target_stage")
    if discovery_paths:
        report_payload["project_assets"].append(
            {
                "name": "Discovery Outputs",
                "status": "generated",
                "artifacts": [
                    {"label": key, "path": str(path)}
                    for key, path in discovery_paths.items()
                ],
            }
        )
    iteration_artifacts = write_iteration_artifacts(
        artifacts.run_dir,
        run_report=report_payload,
        status_snapshot=progress_snapshot,
        attempts=attempts,
        max_attempts=iteration_budget,
        round_input=resolved_round_input,
    )
    round_input_record = iteration_artifacts.round_input
    execution_hints = round_input_record.execution_hints if round_input_record else {}
    stop_conditions = iteration_artifacts.stop_conditions
    next_round_decision = iteration_artifacts.next_round_decision
    report_payload["summary"]["verification_round"] = progress_snapshot.current_round
    report_payload["summary"]["next_round_status"] = next_round_decision.status
    report_payload["summary"]["next_round_should_start"] = next_round_decision.should_start_next_round
    report_payload["summary"]["next_round_primary_reason"] = next_round_decision.primary_reason
    report_payload["summary"]["next_round_target_stage"] = next_round_decision.target_stage
    report_payload["summary"]["stop_status"] = stop_conditions.status if stop_conditions else None
    report_payload["summary"]["stop_should_stop"] = (
        stop_conditions.should_stop if stop_conditions else None
    )
    report_payload["summary"]["stop_primary_reason"] = (
        stop_conditions.primary_reason if stop_conditions else None
    )
    report_payload["summary"]["triggered_stop_conditions"] = (
        list(stop_conditions.triggered_conditions) if stop_conditions else []
    )
    report_payload["summary"]["failure_cluster_count"] = len(iteration_artifacts.failure_clusters)
    report_payload["summary"]["promotion_candidate_count"] = len(
        iteration_artifacts.promotion_candidates
    )
    report_payload["summary"]["scheduled_cluster_count"] = len(
        getattr(next_round_decision, "scheduled_cluster_ids", []) or []
    )
    report_payload["summary"]["scheduled_action_count"] = len(
        getattr(next_round_decision, "scheduled_action_ids", []) or []
    )
    report_payload["summary"]["execution_hint_keys"] = sorted(execution_hints.keys())
    report_payload["summary"]["execution_hint_modes"] = {
        key: value
        for key, value in execution_hints.items()
        if key.endswith("_mode")
        or key
        in {
            "requires_human_review",
            "stop_after_current_round",
            "skip_completed_discovery",
            "preserve_generated_files",
            "regenerate_runtime_data",
            "capture_network_on_retry",
        }
    }
    if resolved_capability_gate:
        report_payload["capability_preflight"] = resolved_capability_gate.to_dict()
    if resolved_capability_routing:
        report_payload["capability_routing"] = resolved_capability_routing.to_dict()
    if resolved_run_policy:
        report_payload["run_policy_resolution"] = resolved_run_policy.to_dict()
    if resolved_discovery_strategy:
        report_payload["discovery_strategy"] = resolved_discovery_strategy.to_dict()
    if policy_decision:
        report_payload["run_policy_decision"] = policy_decision.to_dict()
    report_payload["project_assets"].append(
        {
            "name": "Iteration Outputs",
            "status": "generated",
            "artifacts": build_iteration_asset_refs(artifacts.run_dir),
        }
    )
    report_payload["failure_clusters"] = build_report_failure_clusters(iteration_artifacts)
    report_payload["promotion_candidates"] = build_report_promotion_candidates(iteration_artifacts)
    report_payload["daily_summary"] = {
        "summary": "本轮 run 已完成真实 discovery / verification / iteration 产物输出，并给出 stop / next-round 解释。",
        "new_failure_fix_strategies": [
            {
                "name": action.title,
                "status": action.priority,
                "summary": action.strategy or action.reason or "retry strategy",
                "owner": action.owner,
                "source": action.stage,
                "facts": [
                    {"label": "cluster_id", "value": action.cluster_id},
                    {"label": "expected_outcome", "value": action.expected_outcome},
                ]
                + [
                    {"label": key, "value": value}
                    for key, value in sorted((action.execution_hints or {}).items())
                ],
            }
            for action in iteration_artifacts.retry_plan.actions[:5]
        ]
        or [
            {
                "name": classification.get("category", "unknown"),
                "status": "observed",
                "summary": classification.get("reason"),
            }
        ],
        "watch_items": [
            {
                "name": "stop_conditions",
                "status": stop_conditions.status if stop_conditions else "unknown",
                "summary": summarize_stop_conditions(stop_conditions) if stop_conditions else None,
                "facts": [
                    {"label": "should_stop", "value": stop_conditions.should_stop},
                    {
                        "label": "triggered_conditions",
                        "value": list(stop_conditions.triggered_conditions),
                    },
                ]
                if stop_conditions
                else [],
            },
            {
                "name": "next_round_decision",
                "status": next_round_decision.status,
                "summary": summarize_next_round_decision(next_round_decision),
                "facts": [
                    {"label": "should_start_next_round", "value": next_round_decision.should_start_next_round},
                    {"label": "next_round", "value": next_round_decision.next_round},
                    {"label": "target_stage", "value": next_round_decision.target_stage},
                ],
            },
            {
                "name": "execution_hints",
                "status": "ready" if execution_hints else "empty",
                "summary": summarize_execution_hints(execution_hints) if execution_hints else "No execution hints were carried into this run.",
                "facts": [
                    {"label": "hint_keys", "value": sorted(execution_hints.keys())},
                    {"label": "scheduled_strategies", "value": execution_hints.get("scheduled_strategies")},
                ],
            },
        ],
        "facts": [
            {"label": "failure_cluster_count", "value": len(iteration_artifacts.failure_clusters)},
            {"label": "retry_action_count", "value": len(iteration_artifacts.retry_plan.actions)},
            {"label": "next_round_status", "value": next_round_decision.status},
        ],
    }
    report_payload["model_comparison_summary"] = {
        "title": "Model Comparison Summary",
        "summary": "当前为单模型单 run 摘要，跨模型对比需汇总多轮完成结果。",
        "items": report_payload["model_evaluations"],
    }
    report_payload["skill_inventory_summary"] = {
        "summary": "本轮运行已沉淀 discovery / verification / iteration / reporting 相关项目级能力。",
        "runtime_skills": [
            {
                "name": "controlled_live_discovery",
                "status": "available",
                "summary": "真实页面受控 discovery 已输出稳定 key、scope 和 review hints。",
            },
            {
                "name": "human_loop_capture_v2",
                "status": "available",
                "summary": "human loop 事件采集已带 richer metadata 和 summary。",
            },
        ],
        "project_skills": [
            {
                "name": TEMPLATE_BUNDLE.name,
                "status": "available",
                "summary": "项目级模板样本已支持 runtime data、discovery、verification 和 iteration 产物输出。",
            }
        ],
    }
    report_payload["promotion_candidate_summary"] = {
        "summary": "本轮仅输出项目级/平台级候选摘要，最终晋升仍需人工审查。",
        "candidates": report_payload["promotion_candidates"],
        "approval_notes": [
            "平台级基线沉淀必须人工审核后晋升。",
        ],
        "evidence_requirements": [
            "至少提供成功执行证据、失败归因对比和关键步骤截图。",
        ],
    }
    progress_snapshot.extra.update(
        {
            "classification_category": classification.get("category"),
            "failure_categories": [cluster.category for cluster in iteration_artifacts.failure_clusters if cluster.category],
            "stop_conditions": stop_conditions.to_dict() if stop_conditions else {},
            "next_round_decision": next_round_decision.to_dict() if next_round_decision else {},
            "execution_hints": execution_hints,
            "applied_execution_hints": execution_hints,
            "capability_preflight": resolved_capability_gate.to_dict() if resolved_capability_gate else {},
            "capability_routing": resolved_capability_routing.to_dict() if resolved_capability_routing else {},
            "run_policy_resolution": resolved_run_policy.to_dict() if resolved_run_policy else {},
            "discovery_strategy": resolved_discovery_strategy.to_dict() if resolved_discovery_strategy else {},
            "run_policy_decision": policy_decision.to_dict() if policy_decision else {},
            "round_input": round_input_record.to_dict() if round_input_record else {},
            "retry_plan": (
                iteration_artifacts.retry_plan.to_dict()
                if iteration_artifacts.retry_plan
                else {}
            ),
            "phase_label": progress.snapshot.current_phase_label,
            "waiting_reason": progress.snapshot.waiting_reason,
        }
    )
    progress_snapshot.notes.extend(
        [
            summarize_stop_conditions(stop_conditions),
            summarize_next_round_decision(next_round_decision),
        ]
    )
    if execution_hints:
        progress_snapshot.notes.append(summarize_execution_hints(execution_hints))

    extra_sections: list[dict[str, Any]] = []
    round_input_section = build_round_input_section(iteration_artifacts)
    execution_hints_section = build_execution_hints_section(iteration_artifacts)
    if round_input_section:
        extra_sections.append(round_input_section)
    if execution_hints_section:
        extra_sections.append(execution_hints_section)
    extra_sections.extend(build_retry_plan_section(iteration_artifacts))
    comparison_section = build_iteration_comparison_section(iteration_artifacts)
    stop_section = build_stop_conditions_section(iteration_artifacts)
    next_round_section = build_next_round_decision_section(iteration_artifacts)
    decision_section = build_decision_section(
        stop_conditions=stop_conditions,
        next_round_decision=next_round_decision,
        retry_plan=iteration_artifacts.retry_plan,
        round_input=round_input_record.to_dict() if round_input_record else {},
        execution_hints=execution_hints,
        title="Decision Explanation",
    )
    routing_section = build_routing_section(
        capability_decision=(resolved_capability_gate.to_dict() if resolved_capability_gate else {}),
        route_decision=build_stage_route_decision_payload(
            resolved_capability_routing,
            stage="verification",
            requested_mode="stage2_run_sample",
            assigned_role="verification",
        ),
        policy_decision=(policy_decision.to_dict() if policy_decision else {}),
        title="Routing Explanation",
    )
    if comparison_section:
        extra_sections.append(comparison_section)
    if stop_section:
        extra_sections.append(stop_section)
    if next_round_section:
        extra_sections.append(next_round_section)
    if routing_section:
        extra_sections.append(routing_section.to_dict())
    if decision_section:
        extra_sections.append(decision_section.to_dict())
    report_payload["extra_sections"] = extra_sections
    artifacts.write_json("reports/run_report.json", report_payload)
    artifacts.write_text("reports/run_report.md", render_run_report_markdown(report_payload))
    artifacts.write_text("reports/progress_view.md", render_progress_markdown(progress_snapshot))
    artifacts.write_text("reports/progress_view.txt", render_progress_text(progress_snapshot))


async def collect_registration_list(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        async () => {
          const tokenMatch = (document.cookie || '').match(/(?:^|;\\s*)Admin-Token=([^;]+)/);
          const token = tokenMatch ? decodeURIComponent(tokenMatch[1]) : '';
          const headers = token ? { Authorization: `Bearer ${token}` } : {};
          const response = await fetch('/prod-api/zwsy/registration/apply/list?pageNum=1&pageSize=20', {
            headers,
            credentials: 'include',
          });
          const payload = await response.json();
          return payload;
        }
        """
    )


async def open_pending_record(page: Page, record_id: str, enter_modify_mode: bool) -> dict[str, Any]:
    return await page.evaluate(
        """
        async ({ recordId, enterModifyMode }) => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          if (!apply) return { ok: false, reason: 'apply-not-found' };
          apply.show(recordId);
          await new Promise(resolve => setTimeout(resolve, 2200));
          if (enterModifyMode) {
            apply.handleEdit();
            const checkbox = Array.from(document.querySelectorAll('input[type=checkbox]')).find(el => el.offsetParent !== null);
            if (checkbox && !checkbox.checked) {
              checkbox.click();
              checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
          } else {
            apply.handlePendingEdit();
            const checkbox = Array.from(document.querySelectorAll('input[type=checkbox]')).find(el => el.offsetParent !== null);
            if (checkbox && !checkbox.checked) {
              checkbox.click();
              checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }
          await new Promise(resolve => setTimeout(resolve, 600));
          return { ok: true };
        }
        """,
        {"recordId": record_id, "enterModifyMode": enter_modify_mode},
    )


async def click_button_by_text(page: Page, text_fragment: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ textFragment }) => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const buttons = Array.from(document.querySelectorAll('button')).filter(btn => btn.offsetParent !== null);
          const target = buttons.find(btn => text(btn).includes(textFragment));
          if (!target) {
            return { ok: false, reason: 'button-not-found', visibleButtons: buttons.map(text) };
          }
          target.click();
          return { ok: true, text: text(target) };
        }
        """,
        {"textFragment": text_fragment},
    )


async def click_modify_button(page: Page) -> dict[str, Any]:
    return await click_button_by_text(page, "修")


async def click_first_continue_action(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const rows = Array.from(document.querySelectorAll('.el-table__body-wrapper tbody tr'));
          for (let idx = 0; idx < rows.length; idx += 1) {
            const row = rows[idx];
            const buttons = Array.from(row.querySelectorAll('button')).filter(btn => btn.offsetParent !== null);
            const target = buttons.find(btn => text(btn).includes('继续操作'));
            if (!target) continue;
            const cells = Array.from(row.querySelectorAll('td')).map(td => text(td));
            target.click();
            return { ok: true, rowIndex: idx, cells };
          }
          return { ok: false, reason: 'continue-action-not-found' };
        }
        """
    )


async def detect_submission_path(page: Page) -> dict[str, Any]:
    state = await get_apply_state(page)
    path = "new_application"
    if state.get("ok") and state.get("isEdit"):
        if state.get("isPendingPayment"):
            path = "pending_payment_resubmit"
        elif state.get("isPendingEditMode"):
            path = "pending_edit_update"
        else:
            path = "record_detail_view"
    return {"path": path, "state": state}


async def run_single_profile(
    profile: ModelProfile,
    cdp_url: str,
    max_attempts: int,
    *,
    round_input: dict[str, Any] | None = None,
) -> Path:
    artifacts = ArtifactWriter(ARTIFACT_ROOT, profile.name)
    resolved_round_input = round_input or {}
    execution_hints = resolved_round_input.get("execution_hints") or {}
    runtime_data = TemplateDataFactory(artifacts.run_dir.name).build(
        baseline=SUCCESS_BASELINE,
        schema=TEMPLATE_BUNDLE.data_schema,
    )
    progress = ProgressManager(
        run_id=artifacts.run_dir.name,
        output_dir=artifacts.run_dir,
        template_name=TEMPLATE_BUNDLE.name,
        model_name=profile.name,
        project_name=STAGE2_PROJECT_NAME,
    )
    capability_gate = build_preflight_capability_gate(profile, mode="stage2_run_sample")
    capability_routing = build_capability_routing(profile, gate=capability_gate)
    run_policy_resolution = load_run_policy_resolution()
    previous_run_dir = Path(str(resolved_round_input.get("previous_run_dir", "")).strip()) if resolved_round_input.get("previous_run_dir") else None
    has_completed_discovery = bool(previous_run_dir and (previous_run_dir / "discovery_result.json").exists())
    discovery_strategy = select_discovery_strategy(
        capability_routing=capability_routing,
        execution_hints=execution_hints,
        has_completed_discovery=has_completed_discovery,
        allow_live_enrichment=True,
    )
    routing_summary = build_routing_summary(
        profile,
        capability_gate=capability_gate,
        capability_routing=capability_routing,
        run_policy=run_policy_resolution,
    )
    progress.start_phase(
        "preflight",
        phase_label="预检",
        round_kind="orchestration",
        round_index=resolved_round_input.get("round_index"),
        round_label=(
            f"编排第 {resolved_round_input.get('round_index')} 轮"
            if resolved_round_input.get("round_index")
            else None
        ),
        message=(
            "执行模型能力预检并初始化线上备案申请模板样本运行"
            if not resolved_round_input
            else "执行模型能力预检并初始化带调度输入的线上备案申请模板样本运行"
        ),
        next_action="验证模型能力标签并写入运行时快照",
        stats={
            "template_steps": len(TEMPLATE_BUNDLE.template.get("steps", [])),
            "scheduled_cluster_count": len(resolved_round_input.get("scheduled_cluster_ids") or []),
            "scheduled_action_count": len(resolved_round_input.get("scheduled_action_ids") or []),
        },
        details=(
            {
                "round_input": resolved_round_input,
                "capability_gate": capability_gate.to_dict(),
                "capability_routing": capability_routing.to_dict(),
                "run_policy_resolution": run_policy_resolution.to_dict(),
                "discovery_strategy": discovery_strategy.to_dict(),
                "routing_summary": routing_summary.to_dict(),
            }
            if resolved_round_input
            else {
                "capability_gate": capability_gate.to_dict(),
                "capability_routing": capability_routing.to_dict(),
                "run_policy_resolution": run_policy_resolution.to_dict(),
                "discovery_strategy": discovery_strategy.to_dict(),
                "routing_summary": routing_summary.to_dict(),
            }
        ),
    )
    artifacts.write_json(
        "run_meta.json",
        {
            "profile": {
                "name": profile.name,
                "env_file": str(profile.env_file),
                "base_url": profile.base_url,
                "model": profile.model,
            },
            "cdp_url": cdp_url,
            "started_at": datetime.now().isoformat(),
            "max_attempts": max_attempts,
            "orchestration": resolved_round_input,
            "capability_preflight": capability_gate.to_dict(),
            "capability_routing": capability_routing.to_dict(),
            "run_policy_resolution": run_policy_resolution.to_dict(),
            "discovery_strategy": discovery_strategy.to_dict(),
            "routing_summary": routing_summary.to_dict(),
        },
    )
    artifacts.write_json("capability_preflight.json", capability_gate.to_dict())
    artifacts.write_json("capability_routing.json", capability_routing.to_dict())
    artifacts.write_json("run_policy_resolution.json", run_policy_resolution.to_dict())
    artifacts.write_json("discovery_strategy.json", discovery_strategy.to_dict())
    artifacts.write_json("routing_summary.json", routing_summary.to_dict())
    if resolved_round_input:
        artifacts.write_json("round_input.json", resolved_round_input)

    if not capability_gate.is_allowed:
        progress.fail_phase(
            "preflight",
            phase_label="预检",
            message=capability_gate.reason,
            next_action="先刷新模型能力预检产物，再重新发起 stage-2 运行",
            stats={"preflight_failures": 1},
            details={"capability_gate": capability_gate.to_dict()},
        )
        classification = classify_capability_gate_failure(capability_gate)
        report = [
            f"# 线上申请备案提交结果 - {profile.name}",
            "",
            f"- 运行目录: `{artifacts.run_dir}`",
            f"- 提交结论: 预检未通过，未进入 discovery / verification",
            f"- 判定类别: `{classification['category']}`",
            f"- 原因: `{classification['reason']}`",
        ]
        artifacts.write_text("final_report.md", "\n".join(report))
        write_structured_stage2_report(
            artifacts,
            profile,
            progress,
            page_url=ONLINE_RECORD_URL,
            success=False,
            classification=classification,
            attempts=[],
            generated_files={},
            discovery_result=None,
            discovery_paths=None,
            notes=["模型能力预检未通过，真实执行链路被强制前置阻断。"],
            max_attempts=max_attempts,
            round_input=resolved_round_input,
            capability_gate=capability_gate,
            capability_routing=capability_routing,
            run_policy_resolution=run_policy_resolution,
            discovery_strategy=discovery_strategy,
        )
        return artifacts.run_dir

    artifacts.write_json("template_snapshot.json", TEMPLATE_BUNDLE.template)
    artifacts.write_json("baseline_snapshot.json", SUCCESS_BASELINE)
    artifacts.write_json("runtime_data.json", runtime_data)

    generated_files = build_default_generated_files(artifacts.generated_dir, profile.name)
    runtime = TemplateRuntimeData(
        baseline=SUCCESS_BASELINE,
        run_data=runtime_data,
        generated_files=generated_files,
        locator_hints=TEMPLATE_BUNDLE.locator_hints,
    )
    person_pdf = generated_files["personnel_file"]
    accept_pdf = generated_files["acceptance_file"]
    apply_pdf = generated_files["apply_file"]
    progress.complete_phase(
        "preflight",
        phase_label="预检",
        message="模型能力预检通过，运行时数据和生成附件已准备完成",
        next_action=(
            "进入发现阶段并按显式 discovery 策略执行"
            if discovery_strategy.selected_strategy != "skip_completed_discovery"
            else "保留 discovery 结构并优先进入受控验证"
        ),
    )
    progress.start_phase(
        "discovery",
        phase_label="发现",
        round_kind="orchestration",
        round_index=resolved_round_input.get("round_index"),
        round_label=(
            f"编排第 {resolved_round_input.get('round_index')} 轮"
            if resolved_round_input.get("round_index")
            else None
        ),
        message=(
            "先生成模板播种结果，再按能力路由决定是否升级到真实页面受控遍历"
            if discovery_strategy.selected_strategy != "skip_completed_discovery"
            else "本轮沿用已有 discovery 结论，并在必要时刷新真实页面上下文"
        ),
        next_action="连接浏览器并 enrich discovery 结果",
        details={
            "execution_hints": execution_hints,
            "discovery_route": capability_routing.discovery.to_dict() if capability_routing.discovery else {},
            "discovery_strategy": discovery_strategy.to_dict(),
        },
    )
    if discovery_strategy.selected_strategy == "skip_completed_discovery" and previous_run_dir is not None:
        discovery_result = _load_reused_discovery_result(previous_run_dir)
        discovery_paths = _build_reused_discovery_paths(previous_run_dir)
        if discovery_result is None:
            discovery_result, discovery_paths = build_discovery_seed(artifacts)
    else:
        discovery_result, discovery_paths = build_discovery_seed(artifacts)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("未发现已连接的浏览器上下文")
            pages: list[Page] = []
            for ctx in contexts:
                pages.extend(ctx.pages)
            if not pages:
                raise RuntimeError("未发现已登录页面")

            target = None
            for page in pages:
                if "record/online" in page.url:
                    target = page
                    break
            target = target or pages[0]
            page = target
            await page.bring_to_front()
            await page.wait_for_load_state("domcontentloaded")
            if "record/online" not in page.url:
                await page.goto(ONLINE_RECORD_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

            if discovery_strategy.should_run_live_discovery:
                discovery_result, discovery_paths = await build_live_discovery(page, artifacts)
            progress.complete_phase(
                "discovery",
                phase_label="发现",
                message=(
                    "已完成真实页面受控遍历并生成页面入口清单和功能点清单"
                    if discovery_strategy.should_run_live_discovery
                    else "已复用上一轮 discovery 结果，并继续进入验证阶段"
                    if discovery_strategy.reuse_completed_discovery
                    else "按能力路由保留模板播种 discovery 结果，并继续进入验证阶段"
                ),
                next_action="进入验证阶段执行模板样本",
                stats={
                    "page_entries_discovered": len(getattr(discovery_result, "page_entries", [])),
                    "feature_points_discovered": len(getattr(discovery_result, "feature_points", [])),
                    "discovery_strategy": discovery_strategy.selected_strategy,
                },
            )
            progress.start_phase(
                "verification",
                phase_label="验证",
                round_kind="verification",
                round_index=1,
                round_label="验证第 1 轮",
                message=(
                    "连接可见浏览器并准备执行模板样本"
                    if not resolved_round_input
                    else "连接可见浏览器并准备执行带调度输入的模板样本"
                ),
                next_action="执行线上备案申请主路径",
                details=(
                    {
                        "round_input": resolved_round_input,
                        "verification_route": capability_routing.verification.to_dict() if capability_routing.verification else {},
                    }
                    if resolved_round_input
                    else {
                        "verification_route": capability_routing.verification.to_dict() if capability_routing.verification else {},
                    }
                ),
            )

            artifacts.write_json(
                "page_entry.json",
                {
                    "url": page.url,
                    "title": await page.title(),
                    "pages": [p.url for p in pages],
                },
            )
            await page.screenshot(path=str(artifacts.screenshots_dir / "page_before_apply.png"), full_page=True)

            network_events: list[dict[str, Any]] = []

            async def on_request(request: Any) -> None:
                url = request.url
                if "/prod-api/zwsy/registration/apply/" not in url:
                    return
                payload = {
                    "type": "request",
                    "method": request.method,
                    "url": url,
                    "post_data": request.post_data,
                }
                network_events.append(payload)
                artifacts.append_network(payload)

            async def on_response(response: Any) -> None:
                url = response.url
                if "/prod-api/zwsy/registration/apply/" not in url:
                    return
                try:
                    body = await response.text()
                except Exception as exc:  # pragma: no cover - best effort capture
                    body = f"<response.text error: {exc}>"
                payload = {
                    "type": "response",
                    "status": response.status,
                    "url": url,
                    "body": body,
                }
                network_events.append(payload)
                artifacts.append_network(payload)

            page.on("request", on_request)
            page.on("response", on_response)

            before_state = await snapshot_dialog_state(page)
            artifacts.write_json("dialog_before.json", before_state)

            last_errors: list[str] = before_state.get("errors", [])
            final_submit: dict[str, Any] | None = None
            attempt_records: list[dict[str, Any]] = []
            latest_policy_decision: PolicyGateDecision | None = None

            for attempt in range(1, max_attempts + 1):
                actions: list[dict[str, Any]] = []
                timestamp = datetime.now().isoformat()
                network_start = len(network_events)
                submit_result: dict[str, Any]

                try:
                    actions.append({"step": "reset_online_apply_page", "result": await reset_online_apply_page(page)})
                    actions.append({"step": "close_visible_panel", "result": await close_visible_panel(page)})
                    if execution_hints.get("validation_retry_mode") == "inspect_visible_errors":
                        actions.append({"step": "dismiss_overlays", "result": await dismiss_overlays(page)})
                    await page.wait_for_load_state("domcontentloaded")
                    path_info = await detect_submission_path(page)
                    actions.append({"step": "detect_submission_path", "result": path_info})
                    if execution_hints:
                        actions.append({"step": "execution_hints", "result": execution_hints})

                    if path_info["path"] == "new_application":
                        progress.start_step(
                            "verification",
                            phase_label="验证",
                            round_kind="verification",
                            round_index=attempt,
                            round_label=f"验证第 {attempt} 轮",
                            step_key="execute_template",
                            step_label="执行模板回放",
                            target_kind="feature_point",
                            target_id="online_apply",
                            target_label="线上申请备案",
                            message="按模板执行线上备案申请成功路径",
                        )
                        flow_actions, final_state, step_executions = await execute_verified_new_application_flow(
                            page,
                            artifacts,
                            runtime,
                        )
                        actions.extend(flow_actions)
                        for action in flow_actions:
                            result_payload = action.get("result") if isinstance(action, dict) else None
                            if isinstance(result_payload, dict) and isinstance(result_payload.get("policy_decision"), dict):
                                latest_policy_decision = PolicyGateDecision(**result_payload["policy_decision"])
                        await page.screenshot(
                            path=str(artifacts.screenshots_dir / "dialog_opened.png"),
                            full_page=True,
                        )
                        submit_result = {
                            "messages": final_state.get("messages", []),
                            "errors": [],
                            "bodySnippet": final_state.get("body", ""),
                        }
                        execution_summary = summarize_attempt_execution(flow_actions)
                    else:
                        step_executions = []
                        if execution_hints.get("workflow_retry_mode") == "resume_detected_branch":
                            actions.append(
                                {
                                    "step": "workflow_retry_mode",
                                    "result": {"mode": execution_hints.get("workflow_retry_mode"), "path": path_info["path"]},
                                }
                            )
                        actions.append({"step": "click_first_continue_action", "result": await click_first_continue_action(page)})
                        await page.wait_for_timeout(1500)
                        after_click_state = await get_apply_state(page)
                        actions.append({"step": "after_click_apply_state", "result": after_click_state})
                        registration_list = await collect_registration_list(page)
                        artifacts.write_json("registration_list_snapshot.json", registration_list)
                        rows = registration_list.get("data", []) if isinstance(registration_list, dict) else []
                        pending_candidates = [
                            row
                            for row in rows
                            if str(row.get("auditStatus")) == "0"
                        ]
                        actions.append(
                            {
                                "step": "pending_candidates",
                                "result": {
                                    "count": len(pending_candidates),
                                    "ids": [row.get("id") for row in pending_candidates[:10]],
                                },
                            }
                        )
                        selected = None
                        for row in pending_candidates:
                            if row.get("deptId"):
                                selected = row
                                break
                        selected = selected or (pending_candidates[0] if pending_candidates else None)
                        if selected is None:
                            raise RuntimeError("未发现可继续处理的待提交记录")

                        enter_modify_mode = after_click_state.get("isPendingPayment", False) or not bool(selected.get("deptId"))
                        if enter_modify_mode:
                            actions.append(
                                {
                                    "step": "click_modify_button",
                                    "result": await click_modify_button(page),
                                }
                            )
                            await page.wait_for_timeout(800)
                            actions.append(
                                {
                                    "step": "ensure_checkbox_after_modify",
                                    "result": await ensure_checkbox(page, "本人承诺"),
                                }
                            )
                        elif execution_hints.get("validation_retry_mode") == "inspect_visible_errors":
                            actions.append(
                                {
                                    "step": "ensure_checkbox_after_validation_hint",
                                    "result": await ensure_checkbox(page, "本人承诺"),
                                }
                            )
                        elif not after_click_state.get("ok"):
                            actions.append(
                                {
                                    "step": "open_pending_record_fallback",
                                    "result": await open_pending_record(page, str(selected["id"]), enter_modify_mode),
                                }
                            )
                        actions.append(
                            {
                                "step": "selected_record",
                                "result": {
                                    "id": selected.get("id"),
                                    "deptId": selected.get("deptId"),
                                    "auditStatus": selected.get("auditStatus"),
                                    "enter_modify_mode": enter_modify_mode,
                                },
                            }
                        )

                    await page.screenshot(
                        path=str(artifacts.screenshots_dir / f"attempt_{attempt:02d}_before_submit.png"),
                        full_page=True,
                    )
                    if path_info["path"] != "new_application":
                        if execution_hints.get("ui_retry_mode") == "refresh_locator_and_rerun":
                            await page.wait_for_timeout(800)
                            actions.append(
                                {
                                    "step": "ui_retry_wait",
                                    "result": {"applied": True, "mode": execution_hints.get("ui_retry_mode")},
                                }
                            )
                        submit_result = await submit_dialog(page)
                        policy_payload = submit_result.get("policy_decision") if isinstance(submit_result, dict) else None
                        if isinstance(policy_payload, dict):
                            latest_policy_decision = PolicyGateDecision(**policy_payload)
                        execution_summary = summarize_attempt_execution(actions)
                    final_submit = submit_result
                except (PlaywrightError, RuntimeError) as exc:
                    error_text = f"{type(exc).__name__}: {exc}"
                    policy_category = None
                    if "policy_blocked:" in error_text:
                        policy_category = "policy_blocked"
                    elif "policy_review_required:" in error_text:
                        policy_category = "policy_review_required"
                    step_executions = []
                    submit_result = {
                        "messages": [],
                        "errors": [error_text],
                        "bodySnippet": "",
                    }
                    if policy_category:
                        submit_result["messages"].append(policy_category)
                    execution_summary = summarize_attempt_execution(actions)
                    if policy_category:
                        latest_policy_decision = build_submit_action_policy_decision(
                            action_id="submit_online_apply_dialog",
                            template_name=TEMPLATE_BUNDLE.name,
                            project_name=STAGE2_PROJECT_NAME,
                        )

                after_state = await snapshot_dialog_state(page)
                apply_state = await get_apply_state(page)
                relevant_network = network_events[network_start:]
                classification = classify_submission_result(submit_result, relevant_network, apply_state)
                await page.screenshot(
                    path=str(artifacts.screenshots_dir / f"attempt_{attempt:02d}_after_submit.png"),
                    full_page=True,
                )

                payload = {
                    "attempt": attempt,
                    "timestamp": timestamp,
                    "actions": actions,
                    "submit_result": submit_result,
                    "apply_state": apply_state,
                    "classification": classification,
                    "status": "passed" if classification["success"] else "failed",
                    "execution_summary": execution_summary,
                    "step_executions": [item.to_attempt_action() for item in step_executions],
                    "network_events": relevant_network,
                    "dialog_state": after_state,
                }
                attempt_records.append(payload)
                artifacts.append_attempt(payload)
                artifacts.write_json(f"dialog_after_attempt_{attempt:02d}.json", after_state)
                artifacts.write_json(f"apply_state_after_attempt_{attempt:02d}.json", apply_state)
                artifacts.write_json(f"classification_attempt_{attempt:02d}.json", classification)

                current_errors = after_state.get("errors", [])
                if classification["success"]:
                    progress.complete_step(
                        "verification",
                        phase_label="验证",
                        round_kind="verification",
                        round_index=attempt,
                        round_label=f"验证第 {attempt} 轮",
                        step_key="execute_template",
                        step_label="执行模板回放",
                        target_kind="feature_point",
                        target_id="online_apply",
                        target_label="线上申请备案",
                        message=classification["reason"],
                        next_action="生成运行报告",
                        stats={"verification_successes": 1},
                    )
                    progress.complete_phase(
                        "verification",
                        phase_label="验证",
                        message="模板样本执行成功",
                        next_action="输出报告与运行态视图",
                    )
                    report = [
                        f"# 线上申请备案提交结果 - {profile.name}",
                        "",
                        f"- 运行目录: `{artifacts.run_dir}`",
                        f"- 页面: `{page.url}`",
                        f"- 提交结论: 成功",
                        f"- 判定类别: `{classification['category']}`",
                        f"- 原因: `{classification['reason']}`",
                        f"- 提示消息: `{'; '.join(submit_result.get('messages', [])) or '无显式消息'}`",
                    ]
                    artifacts.write_text("final_report.md", "\n".join(report))
                    write_structured_stage2_report(
                        artifacts,
                        profile,
                        progress,
                        page_url=page.url,
                        success=True,
                        classification=classification,
                        attempts=attempt_records,
                        generated_files=generated_files,
                        discovery_result=discovery_result,
                        discovery_paths=discovery_paths,
                        notes=["真实执行样本已成功完成最终备案提交。"],
                        max_attempts=max_attempts,
                        round_input=resolved_round_input,
                        capability_gate=capability_gate,
                        capability_routing=capability_routing,
                        run_policy_resolution=run_policy_resolution,
                        discovery_strategy=discovery_strategy,
                        policy_decision=latest_policy_decision,
                    )
                    return artifacts.run_dir

                if classification["category"] in {
                    "account_policy_block",
                    "backend_update_primary_key_error",
                    "pending_payment_modify_mode",
                }:
                    progress.fail_step(
                        "verification",
                        phase_label="验证",
                        round_kind="verification",
                        round_index=attempt,
                        round_label=f"验证第 {attempt} 轮",
                        step_key="execute_template",
                        step_label="执行模板回放",
                        target_kind="feature_point",
                        target_id="online_apply",
                        target_label="线上申请备案",
                        message=classification["reason"],
                        next_action="输出阻塞报告",
                        stats={"verification_failures": 1},
                    )
                    progress.fail_phase(
                        "verification",
                        phase_label="验证",
                        message="模板样本执行被阻塞",
                        next_action="等待人工处理或后续归因",
                    )
                    report = [
                        f"# 线上申请备案提交结果 - {profile.name}",
                        "",
                        f"- 运行目录: `{artifacts.run_dir}`",
                        f"- 页面: `{page.url}`",
                        f"- 提交结论: 未成功，已识别阻塞类型",
                        f"- 判定类别: `{classification['category']}`",
                        f"- 原因: `{classification['reason']}`",
                        f"- 当前错误: `{'; '.join(current_errors) or '无'}`",
                        f"- 最近消息: `{'; '.join(submit_result.get('messages', [])) or '无'}`",
                    ]
                    artifacts.write_text("final_report.md", "\n".join(report))
                    write_structured_stage2_report(
                        artifacts,
                        profile,
                        progress,
                        page_url=page.url,
                        success=False,
                        classification=classification,
                        attempts=attempt_records,
                        generated_files=generated_files,
                        discovery_result=discovery_result,
                        discovery_paths=discovery_paths,
                        notes=["真实执行样本命中了已识别的阻塞类型。"],
                        max_attempts=max_attempts,
                        round_input=resolved_round_input,
                        capability_gate=capability_gate,
                        capability_routing=capability_routing,
                        run_policy_resolution=run_policy_resolution,
                        discovery_strategy=discovery_strategy,
                        policy_decision=latest_policy_decision,
                    )
                    latest_round_input = read_round_input(artifacts.run_dir) or resolved_round_input
                    stop_payload = _read_json_file(artifacts.run_dir / "stop_conditions.json")
                    next_round_payload = _read_json_file(artifacts.run_dir / "next_round_decision.json")
                    retry_plan_payload = _read_json_file(artifacts.run_dir / "retry_plan.json")
                    if next_round_payload.get("status") == "needs_review":
                        takeover_packet = build_human_takeover_packet(
                            artifacts.run_dir,
                            round_input=latest_round_input,
                            retry_plan=retry_plan_payload,
                            stop_conditions=stop_payload,
                            next_round_decision=next_round_payload,
                            max_attempts=max_attempts,
                        )
                        packet_path = write_human_takeover_packet(artifacts.run_dir, takeover_packet)
                        progress.wait_for_human(
                            "verification",
                            phase_label="验证",
                            round_kind="verification",
                            round_index=attempt,
                            round_label=f"验证第 {attempt} 轮",
                            step_key="manual_takeover",
                            step_label="人工接管",
                            target_kind="feature_point",
                            target_id="online_apply",
                            target_label="线上申请备案",
                            reason=takeover_packet["reason"] or "需要人工接管后继续下一轮",
                            next_action=f"人工处理完成后执行恢复命令：{takeover_packet['resume_command']}",
                            stats={"human_takeovers": 1},
                            details={"human_takeover_packet": takeover_packet, "packet_path": str(packet_path)},
                        )
                    return artifacts.run_dir

                if current_errors == last_errors:
                    progress.fail_step(
                        "verification",
                        phase_label="验证",
                        round_kind="verification",
                        round_index=attempt,
                        round_label=f"验证第 {attempt} 轮",
                        step_key="execute_template",
                        step_label="执行模板回放",
                        target_kind="feature_point",
                        target_id="online_apply",
                        target_label="线上申请备案",
                        message="错误集未继续收敛",
                        next_action="输出失败报告",
                    )
                    report = [
                        f"# 线上申请备案提交结果 - {profile.name}",
                        "",
                        f"- 运行目录: `{artifacts.run_dir}`",
                        f"- 页面: `{page.url}`",
                        f"- 提交结论: 未成功，错误集未继续收敛",
                        f"- 判定类别: `{classification['category']}`",
                        f"- 当前错误: `{'; '.join(current_errors) or '无'}`",
                        f"- 最近消息: `{'; '.join(submit_result.get('messages', [])) or '无'}`",
                    ]
                    artifacts.write_text("final_report.md", "\n".join(report))
                    write_structured_stage2_report(
                        artifacts,
                        profile,
                        progress,
                        page_url=page.url,
                        success=False,
                        classification=classification,
                        attempts=attempt_records,
                        generated_files=generated_files,
                        discovery_result=discovery_result,
                        discovery_paths=discovery_paths,
                        notes=["真实执行样本未继续收敛，已提前结束当前 run。"],
                        max_attempts=max_attempts,
                        round_input=resolved_round_input,
                        capability_gate=capability_gate,
                        capability_routing=capability_routing,
                        run_policy_resolution=run_policy_resolution,
                        discovery_strategy=discovery_strategy,
                        policy_decision=latest_policy_decision,
                    )
                    return artifacts.run_dir

                last_errors = current_errors

            report = [
                f"# 线上申请备案提交结果 - {profile.name}",
                "",
                f"- 运行目录: `{artifacts.run_dir}`",
                f"- 页面: `{page.url}`",
                f"- 提交结论: 达到最大尝试次数仍未成功",
                f"- 最终错误: `{'; '.join(last_errors) or '无'}`",
                f"- 最近消息: `{'; '.join((final_submit or {}).get('messages', [])) or '无'}`",
            ]
            artifacts.write_text("final_report.md", "\n".join(report))
            progress.fail_phase(
                "verification",
                phase_label="验证",
                message="达到最大尝试次数仍未成功",
                next_action="输出失败报告",
            )
            write_structured_stage2_report(
                artifacts,
                profile,
                progress,
                page_url=page.url,
                success=False,
                classification={
                    "category": "max_attempts_exhausted",
                    "reason": "达到最大尝试次数仍未成功",
                },
                attempts=attempt_records,
                generated_files=generated_files,
                discovery_result=discovery_result,
                discovery_paths=discovery_paths,
                notes=["真实执行样本耗尽最大尝试次数。"],
                max_attempts=max_attempts,
                round_input=resolved_round_input,
                capability_gate=capability_gate,
                capability_routing=capability_routing,
                run_policy_resolution=run_policy_resolution,
                discovery_strategy=discovery_strategy,
                policy_decision=latest_policy_decision,
            )
            return artifacts.run_dir
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            if progress.snapshot.phase_statuses.get("discovery") != "completed":
                progress.complete_phase(
                    "discovery",
                    phase_label="发现",
                    message="浏览器不可用，保留模板播种 discovery 结果",
                    next_action="输出受限发现结果并结束验证",
                    stats={
                        "page_entries_discovered": len(getattr(discovery_result, "page_entries", [])),
                        "feature_points_discovered": len(getattr(discovery_result, "feature_points", [])),
                    },
                )
            progress.fail_phase(
                "verification",
                phase_label="验证",
                message=error_text,
                next_action="检查 CDP 浏览器、远程调试端口和登录会话",
            )
            classification = {
                "category": "environment_bootstrap_failure",
                "reason": error_text,
            }
            report = [
                f"# 线上申请备案提交结果 - {profile.name}",
                "",
                f"- 运行目录: `{artifacts.run_dir}`",
                f"- 页面: `{ONLINE_RECORD_URL}`",
                f"- 提交结论: 未启动执行链路",
                f"- 判定类别: `{classification['category']}`",
                f"- 原因: `{classification['reason']}`",
            ]
            artifacts.write_text("final_report.md", "\n".join(report))
            write_structured_stage2_report(
                artifacts,
                profile,
                progress,
                page_url=ONLINE_RECORD_URL,
                success=False,
                classification=classification,
                attempts=[],
                generated_files=generated_files,
                discovery_result=discovery_result,
                discovery_paths=discovery_paths,
                notes=["验证阶段在连接浏览器或接管已登录会话之前失败。"],
                max_attempts=max_attempts,
                round_input=resolved_round_input,
                capability_gate=capability_gate,
                capability_routing=capability_routing,
                run_policy_resolution=run_policy_resolution,
                discovery_strategy=discovery_strategy,
            )
            return artifacts.run_dir
        finally:
            if browser is not None:
                await browser.close()


async def run_profile_with_iterations(
    profile: ModelProfile,
    *,
    cdp_url: str,
    max_attempts: int,
    max_rounds: int,
) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    latest_run_dir: Path | None = None
    previous_decision: dict[str, Any] | None = None

    for round_index in range(1, max_rounds + 1):
        round_input = build_round_input(
            profile,
            round_index=round_index,
            max_rounds=max_rounds,
            previous_run_dir=latest_run_dir,
            previous_decision=previous_decision,
        )
        run_dir = await run_single_profile(
            profile,
            cdp_url,
            max_attempts,
            round_input=round_input,
        )
        latest_run_dir = run_dir
        status_payload = _read_json_file(run_dir / "current_status.json")
        next_round_decision = load_next_round_decision(run_dir)
        persisted_round_input = read_round_input(run_dir) or round_input
        round_payload = {
            "round": round_index,
            "run_dir": str(run_dir),
            "status": status_payload.get("overall_status"),
            "elapsed_ms": status_payload.get("elapsed_ms"),
            "round_input": persisted_round_input,
            "next_round_decision": next_round_decision,
        }
        rounds.append(round_payload)
        previous_decision = next_round_decision
        if not should_auto_continue_next_round(next_round_decision):
            break

    final_status = rounds[-1]["status"] if rounds else "unknown"
    final_elapsed_ms = rounds[-1]["elapsed_ms"] if rounds else None
    final_decision = rounds[-1]["next_round_decision"] if rounds else {}
    return {
        "model": profile.name,
        "run_dir": str(latest_run_dir) if latest_run_dir is not None else "",
        "status": final_status,
        "elapsed_ms": final_elapsed_ms,
        "round_count": len(rounds),
        "final_next_round_decision": final_decision,
        "rounds": rounds,
    }


async def resume_profile_from_human_takeover(
    run_dir: Path,
    *,
    cdp_url: str,
    max_attempts: int,
    max_rounds: int | None = 1,
    operator_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    previous_round_input = read_round_input(run_dir)
    next_round_decision = load_next_round_decision(run_dir)
    resumed_decision = build_human_resume_decision(
        next_round_decision,
        operator_id=operator_id,
        note=note,
    )
    model_name = previous_round_input.get("model_name")
    if not model_name:
        raise RuntimeError("上一轮 round_input.json 缺少 model_name，无法恢复人工接管后的执行。")
    profile = resolve_model_profile(str(model_name))

    rounds: list[dict[str, Any]] = []
    latest_run_dir: Path | None = run_dir
    previous_decision: dict[str, Any] | None = resumed_decision
    start_round = int(previous_round_input.get("round_index") or 1) + 1
    effective_max_rounds, round_budget_source = resolve_resume_round_budget(
        previous_round_input,
        max_rounds,
    )
    final_round = start_round + effective_max_rounds - 1

    for round_index in range(start_round, final_round + 1):
        round_input = build_round_input(
            profile,
            round_index=round_index,
            max_rounds=max(start_round, final_round),
            previous_run_dir=latest_run_dir,
            previous_decision=previous_decision,
        )
        round_input["notes"].append("This round resumed after a human takeover handoff.")
        if operator_id:
            round_input["notes"].append(f"Human takeover operator: {operator_id}")
        if note:
            round_input["notes"].append(note)

        new_run_dir = await run_single_profile(
            profile,
            cdp_url,
            max_attempts,
            round_input=round_input,
        )
        latest_run_dir = new_run_dir
        status_payload = _read_json_file(new_run_dir / "current_status.json")
        next_round_decision = load_next_round_decision(new_run_dir)
        persisted_round_input = read_round_input(new_run_dir) or round_input
        rounds.append(
            {
                "round": round_index,
                "run_dir": str(new_run_dir),
                "status": status_payload.get("overall_status"),
                "elapsed_ms": status_payload.get("elapsed_ms"),
                "round_input": persisted_round_input,
                "next_round_decision": next_round_decision,
            }
        )
        previous_decision = next_round_decision
        if not should_auto_continue_next_round(next_round_decision):
            break

    final_status = rounds[-1]["status"] if rounds else "unknown"
    final_elapsed_ms = rounds[-1]["elapsed_ms"] if rounds else None
    final_decision = rounds[-1]["next_round_decision"] if rounds else {}
    sync_orchestration_session_artifacts(ARTIFACT_ROOT)
    return {
        "model": profile.name,
        "resumed_from_run_dir": str(run_dir),
        "run_dir": str(latest_run_dir) if latest_run_dir is not None else str(run_dir),
        "status": final_status,
        "elapsed_ms": final_elapsed_ms,
        "round_count": len(rounds),
        "final_next_round_decision": final_decision,
        "rounds": rounds,
        "human_takeover_resumed": True,
        "requested_max_rounds": max_rounds,
        "effective_max_rounds": effective_max_rounds,
        "resume_round_budget_source": round_budget_source,
        "operator_id": operator_id,
        "note": note,
    }


async def run_stage2_sample(
    *,
    cdp_url: str | None = None,
    max_attempts: int | None = None,
    max_rounds: int = 1,
) -> dict[str, Any]:
    profiles = load_stage2_model_profiles(DEFAULT_ENV_FILES)
    if not profiles:
        raise RuntimeError("未从 demo 目录加载到模型配置")

    resolved_cdp_url = cdp_url or os.getenv("SUYUAN_CDP_URL", DEFAULT_CDP_URL)
    resolved_max_attempts = max_attempts or int(os.getenv("SUYUAN_MAX_ATTEMPTS", "3"))
    results: list[dict[str, Any]] = []
    for profile in profiles:
        results.append(
            await run_profile_with_iterations(
                profile,
                cdp_url=resolved_cdp_url,
                max_attempts=resolved_max_attempts,
                max_rounds=max_rounds,
            )
        )

    comparison_summary = build_cross_model_summary(results)
    comparison_path = ROOT_DIR / "artifacts" / "stage2" / "latest_model_comparison.json"
    comparison_path.write_text(
        json.dumps(comparison_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    platform_daily_report = write_platform_daily_report_bundle(results)
    baseline_freeze_manifest = write_baseline_freeze_manifest(
        results,
        comparison_path=comparison_path,
        platform_daily_report=platform_daily_report,
    )
    sync_orchestration_session_artifacts(ARTIFACT_ROOT)
    return {
        "template_name": TEMPLATE_BUNDLE.name,
        "model_count": len(results),
        "results": results,
        "comparison_path": str(comparison_path),
        "comparison_summary": comparison_summary,
        "platform_daily_report": platform_daily_report,
        "baseline_freeze_manifest": baseline_freeze_manifest,
    }


async def main() -> None:
    payload = await run_stage2_sample()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
