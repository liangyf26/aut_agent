from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.runtime.template_bootstrap import bootstrap_template_bundle
from prototype.stage2.app.reporting import (
    build_platform_daily_report,
    render_platform_daily_report_markdown,
)
TEMPLATE_ROOT = ROOT_DIR / "prototype" / "stage2" / "templates"
HUMAN_LOOP_ROOT = ROOT_DIR / "artifacts" / "stage2" / "human_loop"
DEFAULT_CDP_URL = "http://localhost:9222"
DEFAULT_G4_VALIDATION_GOAL = (
    "验证第二阶段原型是否已具备跨模板族/跨系统样本的统一执行、验证与汇总能力。"
)

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def _load_template_runtime_data_class():
    from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData

    return TemplateRuntimeData


def _ensure_validation_matrix_symbols() -> None:
    if "ValidationMatrixResult" in globals():
        return
    from prototype.stage2.app.verification.validation_matrix import (
        VALIDATION_MODE_CONNECTED as _VALIDATION_MODE_CONNECTED,
        VALIDATION_MODE_LOCAL as _VALIDATION_MODE_LOCAL,
        VALIDATION_STATUS_FAILED as _VALIDATION_STATUS_FAILED,
        VALIDATION_STATUS_SKIPPED as _VALIDATION_STATUS_SKIPPED,
        ValidationMatrixResult as _ValidationMatrixResult,
        ValidationMatrixTarget as _ValidationMatrixTarget,
        build_default_g4_validation_targets as _build_default_g4_validation_targets,
        build_validation_matrix_payload as _build_validation_matrix_payload,
        render_validation_matrix_markdown as _render_validation_matrix_markdown,
    )

    globals().update(
        {
            "VALIDATION_MODE_CONNECTED": _VALIDATION_MODE_CONNECTED,
            "VALIDATION_MODE_LOCAL": _VALIDATION_MODE_LOCAL,
            "VALIDATION_STATUS_FAILED": _VALIDATION_STATUS_FAILED,
            "VALIDATION_STATUS_SKIPPED": _VALIDATION_STATUS_SKIPPED,
            "ValidationMatrixResult": _ValidationMatrixResult,
            "ValidationMatrixTarget": _ValidationMatrixTarget,
            "build_default_g4_validation_targets": _build_default_g4_validation_targets,
            "build_validation_matrix_payload": _build_validation_matrix_payload,
            "render_validation_matrix_markdown": _render_validation_matrix_markdown,
        }
    )


def list_templates() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not TEMPLATE_ROOT.exists():
        return items
    for child in sorted(TEMPLATE_ROOT.iterdir()):
        if not child.is_dir():
            continue
        required = [
            child / "template.json",
            child / "baseline.json",
            child / "data_schema.json",
            child / "locator_hints.json",
        ]
        if not all(path.exists() for path in required):
            continue
        bundle = load_template_bundle(child)
        items.append(
            {
                "name": bundle.name,
                "path": str(child),
                "entry_point": bundle.template.get("page_entry", {}).get("name", ""),
                "feature_point": bundle.template.get("feature_point", {}).get("name", ""),
            }
        )
    return items


def bootstrap_template(
    template_name: str,
    *,
    page_url: str,
    page_name: str = "",
    feature_name: str = "",
    feature_type: str = "",
    scenario_kind: str = "navigation",
    overwrite: bool = False,
) -> dict[str, Any]:
    result = bootstrap_template_bundle(
        TEMPLATE_ROOT,
        template_name=template_name,
        page_url=page_url,
        page_name=page_name,
        feature_name=feature_name,
        feature_type=feature_type,
        scenario_kind=scenario_kind,
        overwrite=overwrite,
    )
    bundle = load_template_bundle(result.template_dir)
    return _build_bootstrap_template_payload(
        template_name=result.template_name,
        template_dir=result.template_dir,
        template_payload=bundle.template,
        scenario_kind=result.scenario_kind,
    )


def bootstrap_system_exploration_template(
    *,
    target_name: str,
    start_url: str,
    template_name: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    normalized_target_name = str(target_name or "").strip() or "新系统页面"
    normalized_start_url = str(start_url or "").strip()
    if not normalized_start_url:
        raise ValueError("start_url 不能为空。")
    resolved_template_name = str(template_name or "").strip() or _default_system_map_template_name(
        normalized_target_name
    )
    try:
        payload = bootstrap_template(
            resolved_template_name,
            page_url=normalized_start_url,
            page_name=f"{normalized_target_name}系统入口",
            feature_name=f"{normalized_target_name}系统地图探索",
            feature_type="导航",
            scenario_kind="navigation",
            overwrite=overwrite,
        )
    except FileExistsError:
        existing_bundle = _load_existing_system_map_template_bundle(resolved_template_name)
        existing_url = str(
            existing_bundle.template.get("page_entry", {}).get("url") or ""
        ).strip()
        if existing_url and existing_url != normalized_start_url:
            raise FileExistsError(
                "系统地图模板目录已存在，且绑定的页面入口 URL 与当前探索目标不同："
                f"{existing_bundle.template_dir}。当前模板 URL={existing_url}，"
                f"本次请求 URL={normalized_start_url}。如需覆盖，请使用 bootstrap_overwrite=True。"
            )
        payload = _build_bootstrap_template_payload(
            template_name=existing_bundle.name,
            template_dir=existing_bundle.template_dir,
            template_payload=existing_bundle.template,
            scenario_kind="navigation",
            reused_existing_template=True,
        )
    payload["target_name"] = normalized_target_name
    payload["mode"] = "system_map_bootstrap"
    payload["recommended_next_steps"] = [
        "python -m prototype.stage2.main --routing-summary --template <template_name>",
        "python -m prototype.stage2.main --explore-system-map --template <template_name> --cdp-url http://localhost:9222",
        "python -m prototype.stage2.main --template-revision-checklist --template <template_name>",
    ]
    return payload


def generate_template_revision_checklist(
    template_name: str,
    *,
    discovery_dir: str = "",
    candidate_review_path: str = "",
    output_dir: str = "",
) -> dict[str, Any]:
    from prototype.stage2.app.runtime.template_revision_checklist import build_template_revision_checklist

    template_dir = TEMPLATE_ROOT / template_name
    resolved_discovery_dir = Path(discovery_dir) if discovery_dir else ROOT_DIR / "artifacts" / "stage2" / f"live_discovery_{template_name}"
    resolved_candidate_review_path = (
        Path(candidate_review_path) if candidate_review_path else _find_latest_candidate_review_for_template(template_name)
    )
    result = build_template_revision_checklist(
        template_dir,
        discovery_dir=resolved_discovery_dir if resolved_discovery_dir.exists() else None,
        candidate_review_path=resolved_candidate_review_path if resolved_candidate_review_path and resolved_candidate_review_path.exists() else None,
        output_dir=Path(output_dir) if output_dir else None,
    )
    return {
        "template": result.template_name,
        "output_dir": str(result.output_dir),
        "checklist_path": str(result.checklist_path),
        "markdown_path": str(result.markdown_path),
        "discovery_dir": str(resolved_discovery_dir) if resolved_discovery_dir else "",
        "candidate_review_path": str(resolved_candidate_review_path) if resolved_candidate_review_path else "",
        "summary": result.payload.get("summary", {}),
    }


def _find_latest_candidate_review_for_template(template_name: str) -> Path | None:
    if not HUMAN_LOOP_ROOT.exists():
        return None
    candidates: list[Path] = []
    for path in HUMAN_LOOP_ROOT.glob("*/candidate_template_review.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(payload.get("template_name") or "").strip() != template_name:
            continue
        candidates.append(path)
    if not candidates:
        return None
    return sorted(candidates, reverse=True)[0]


def _build_bootstrap_template_payload(
    *,
    template_name: str,
    template_dir: Path,
    template_payload: dict[str, Any],
    scenario_kind: str,
    reused_existing_template: bool = False,
) -> dict[str, Any]:
    page_entry = template_payload.get("page_entry", {}) if isinstance(template_payload, dict) else {}
    feature_point = template_payload.get("feature_point", {}) if isinstance(template_payload, dict) else {}
    bootstrap_meta = template_payload.get("bootstrap", {}) if isinstance(template_payload, dict) else {}
    resolved_scenario_kind = str(
        bootstrap_meta.get("scenario_kind") or scenario_kind or DEFAULT_SCENARIO_KIND
    )
    return {
        "template": template_name,
        "template_dir": str(template_dir),
        "page_name": str(page_entry.get("name") or ""),
        "feature_name": str(feature_point.get("name") or ""),
        "feature_type": str(feature_point.get("type") or ""),
        "scenario_kind": resolved_scenario_kind,
        "template_path": str(template_dir / "template.json"),
        "baseline_path": str(template_dir / "baseline.json"),
        "data_schema_path": str(template_dir / "data_schema.json"),
        "locator_hints_path": str(template_dir / "locator_hints.json"),
        "reused_existing_template": reused_existing_template,
        "next_steps": [
            "python -m prototype.stage2.main --routing-summary --template <template_name>",
            "python -m prototype.stage2.main --live-discovery --template <template_name> --model AI-tester --cdp-url http://localhost:9222",
            "python -m prototype.stage2.main --capture-human-recording --template <template_name> --cdp-url http://localhost:9222",
            "python -m prototype.stage2.main --validate-connected-template <template_name> --cdp-url http://localhost:9222",
        ],
    }


def _load_existing_system_map_template_bundle(template_name: str):
    template_dir = TEMPLATE_ROOT / template_name
    required = [
        template_dir / "template.json",
        template_dir / "baseline.json",
        template_dir / "data_schema.json",
        template_dir / "locator_hints.json",
    ]
    if not all(path.exists() for path in required):
        raise FileExistsError(
            f"模板目录已存在但缺少系统地图模板所需文件：{template_dir}。如需覆盖，请使用 bootstrap_overwrite=True。"
        )
    return load_template_bundle(template_dir)


def _default_system_map_template_name(target_name: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(target_name or "").strip())
    normalized = normalized.strip("_")
    return f"{normalized or 'new_system'}_system_map"


def initialize_runs(template_name: str) -> list[dict[str, str]]:
    from prototype.stage2.app.verification.run_sample import build_run_contexts

    contexts = build_run_contexts(template_name=template_name)
    results: list[dict[str, str]] = []
    for context in contexts:
        results.append(
            {
                "model": context.model_profile.name,
                "template": context.template_name,
                "run_dir": str(context.artifacts.run_dir),
                "progress_file": str(context.artifacts.paths.status_path),
                "progress_view": str(context.artifacts.run_dir / "reports" / "progress_view.md"),
                "page_entries": str(context.artifacts.run_dir / "page_entries.json"),
                "feature_points": str(context.artifacts.run_dir / "feature_points.json"),
                "retry_plan": str(context.artifacts.run_dir / "retry_plan.json"),
                "discovery_strategy": str(context.artifacts.run_dir / "discovery_strategy.json"),
                "routing_summary": str(context.artifacts.run_dir / "routing_summary.json"),
            }
        )
    return results


def build_routing_summaries(template_name: str) -> list[dict[str, Any]]:
    from prototype.stage2.app.config import (
        build_capability_routing,
        load_model_profiles,
        load_run_policy,
        validate_model_capabilities,
    )
    from prototype.stage2.app.discovery.strategy import select_discovery_strategy
    from prototype.stage2.app.orchestration.routing_summary import build_routing_summary
    from prototype.stage2.app.verification.constants import DEFAULT_ENV_FILES

    profiles = load_model_profiles(DEFAULT_ENV_FILES)
    run_policy_path = ROOT_DIR / "prototype" / "stage2" / "run_policy.json"
    results: list[dict[str, Any]] = []
    for profile in profiles:
        capability_gate = validate_model_capabilities(profile, mode="template_init")
        capability_routing = build_capability_routing(profile, gate=capability_gate)
        run_policy = load_run_policy(
            run_policy_path,
            project_name="AI Agent 软件自动化评测平台第二阶段原型",
            template_name=template_name,
        )
        discovery_strategy = select_discovery_strategy(
            capability_routing=capability_routing,
            execution_hints={},
            has_completed_discovery=False,
            allow_live_enrichment=False,
        )
        routing_summary = build_routing_summary(
            profile,
            capability_gate=capability_gate,
            capability_routing=capability_routing,
            run_policy=run_policy,
        )
        results.append(
            {
                "model": profile.name,
                "template": template_name,
                "routing_summary": routing_summary.to_dict(),
                "discovery_strategy": discovery_strategy.to_dict(),
            }
        )
    return results


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_completed_discovery(output_dir: Path) -> tuple[Any | None, dict[str, Path]]:
    from prototype.stage2.app.discovery import (
        DiscoveryArtifactWriter,
        apply_discovery_review_patch,
        load_discovery_review_patch,
    )

    paths = DiscoveryArtifactWriter.load_paths(output_dir)
    result = DiscoveryArtifactWriter.load(paths["discovery_summary"])
    if result is None:
        return None, paths
    if not result.page_entries and not result.feature_points:
        return None, paths
    review_patch = load_discovery_review_patch(output_dir)
    if review_patch:
        result = apply_discovery_review_patch(result, review_patch)
    return result, paths


def load_run_report_payload(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    return _read_json_file(root / "reports" / "run_report.json")


def load_validation_result_payload(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    return _read_json_file(root / "validation_result.json")


def _resolve_cli_sample_max_rounds(value: int) -> int:
    return value if value > 0 else 1


def _resolve_cli_resume_max_rounds(value: int) -> int | None:
    return value if value > 0 else None


def _build_human_recording_template_metadata(bundle: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    project_field_candidates = _collect_human_recording_project_field_candidates(bundle)
    if project_field_candidates:
        metadata["project_field_candidates"] = project_field_candidates
    project_field_aliases = _collect_human_recording_field_aliases(bundle)
    if project_field_aliases:
        metadata["project_field_aliases"] = project_field_aliases
    return metadata


def _collect_human_recording_project_field_candidates(bundle: Any) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []

    def push(value: Any) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        candidates.append(text)

    sections: list[dict[str, Any]] = []
    for payload in (getattr(bundle, "data_schema", None), getattr(bundle, "baseline", None)):
        if isinstance(payload, dict):
            sections.append(payload)
            human_recording = payload.get("human_recording")
            if isinstance(human_recording, dict):
                sections.append(human_recording)

    for section in sections:
        for key in ("field_rules", "field_constraints", "field_samples"):
            nested = section.get(key)
            if isinstance(nested, dict):
                for field_key in nested.keys():
                    push(field_key)
        raw_candidates = section.get("project_field_candidates")
        if isinstance(raw_candidates, list):
            for field_key in raw_candidates:
                push(field_key)

    return candidates


def _collect_human_recording_field_aliases(bundle: Any) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for payload in (
        getattr(bundle, "template", None),
        getattr(bundle, "baseline", None),
        getattr(bundle, "data_schema", None),
        getattr(bundle, "locator_hints", None),
    ):
        if not isinstance(payload, dict):
            continue
        sections = [payload]
        human_recording = payload.get("human_recording")
        if isinstance(human_recording, dict):
            sections.append(human_recording)
        for section in sections:
            for key in (
                "project_field_aliases",
                "field_aliases",
                "candidate_field_aliases",
                "human_recording_field_aliases",
            ):
                raw_aliases = section.get(key)
                if not isinstance(raw_aliases, dict):
                    continue
                for canonical_key, values in raw_aliases.items():
                    canonical_text = str(canonical_key or "").strip()
                    if not canonical_text:
                        continue
                    bucket = aliases.setdefault(canonical_text, [])
                    raw_values = values if isinstance(values, list) else [values]
                    for value in raw_values:
                        alias_text = str(value or "").strip()
                        if not alias_text or alias_text in bucket:
                            continue
                        bucket.append(alias_text)
    return aliases


def load_latest_run_reports(limit: int = 20) -> list[dict[str, Any]]:
    artifact_root = ROOT_DIR / "artifacts" / "stage2"
    if not artifact_root.exists():
        return []

    payloads: list[dict[str, Any]] = []
    for child in sorted(artifact_root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        if child.name in {"human_loop"} or child.name.startswith("live_discovery_"):
            continue
        report_payload = load_run_report_payload(child)
        if report_payload:
            payloads.append(report_payload)
        if len(payloads) >= limit:
            break
    return payloads


def load_latest_validation_results(limit: int = 20) -> list[dict[str, Any]]:
    artifact_root = ROOT_DIR / "artifacts" / "stage2" / "template_validation"
    if not artifact_root.exists():
        return []

    payloads: list[dict[str, Any]] = []
    for child in sorted(artifact_root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        payload = load_validation_result_payload(child)
        if payload:
            payloads.append(payload)
        if len(payloads) >= limit:
            break
    return payloads


def _build_template_validation_metadata(
    template_name: str,
    template: dict[str, Any],
    *,
    mode: str,
    model_name: str = "",
) -> dict[str, Any]:
    if template_name.startswith("lab_"):
        family = "lab"
        system_id = "template_lab"
        system_name = "Template Lab"
    elif template_name.startswith("suyuan_"):
        family = "suyuan"
        system_id = "suyuan_online_record"
        system_name = "Suyuan Online Record"
    else:
        family = "generic"
        system_id = "generic_template"
        system_name = "Generic Template"

    page_entry = template.get("page_entry", {}) if isinstance(template, dict) else {}
    feature_point = template.get("feature_point", {}) if isinstance(template, dict) else {}
    return {
        "source_kind": "validation_result",
        "template_name": template_name,
        "family": family,
        "system_id": system_id,
        "system_name": system_name,
        "scenario_id": template_name,
        "mode": mode,
        "model_name": model_name,
        "page_entry_name": str(page_entry.get("name") or ""),
        "page_entry_url": str(page_entry.get("url") or ""),
        "feature_point_name": str(feature_point.get("name") or ""),
        "feature_point_type": str(feature_point.get("type") or ""),
        "notes": list(template.get("notes", [])) if isinstance(template.get("notes"), list) else [],
    }


def _build_template_validation_payload(
    *,
    bundle: Any,
    artifacts: Any,
    verification: Any,
    mode: str,
    model_name: str = "",
) -> dict[str, Any]:
    verification_payload = verification.to_dict()
    steps = [item for item in verification_payload.get("steps", []) if isinstance(item, dict)]
    verification_result = verification_payload.get("verification_result", {})
    rule_evaluation = verification_result.get("rule_evaluation", {})
    validation_path = artifacts.run_dir / "validation_result.json"
    payload = {
        **_build_template_validation_metadata(
            bundle.name,
            bundle.template,
            mode=mode,
            model_name=model_name,
        ),
        "template": bundle.name,
        "run_dir": str(artifacts.run_dir),
        "report_path": str(validation_path),
        "duration_ms": sum(int(item.get("duration_ms") or 0) for item in steps),
        "failed_step_ids": [str(item.get("step") or "") for item in steps if item.get("status") != "completed"],
        "verification_status": str(verification_result.get("status") or ""),
        "rule_summary": str(rule_evaluation.get("summary") or ""),
        **verification_payload,
    }
    payload["artifacts"] = [
        {"label": "validation_result.json", "path": str(validation_path)},
        {"label": "verification_result.json", "path": str(artifacts.run_dir / "verification_result.json")},
        {"label": "network_events.json", "path": str(artifacts.run_dir / "network_events.json")},
    ]
    return payload


async def run_local_template_validation(template_name: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from prototype.stage2.app.data_factory.generator import TemplateDataFactory
    from prototype.stage2.app.runtime.artifacts import ArtifactWriter
    from prototype.stage2.app.verification import execute_generic_template_with_shared_result

    TemplateRuntimeData = _load_template_runtime_data_class()
    _ensure_validation_matrix_symbols()
    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    artifact_root = ROOT_DIR / "artifacts" / "stage2" / "template_validation"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifacts = ArtifactWriter(artifact_root, template_name)
    run_data = TemplateDataFactory(artifacts.run_dir.name).build(
        baseline=bundle.baseline,
        schema=bundle.data_schema,
    )
    runtime = TemplateRuntimeData(
        baseline=bundle.baseline,
        run_data=run_data,
        generated_files={},
        locator_hints=bundle.locator_hints,
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            verification = await execute_generic_template_with_shared_result(
                page=page,
                artifacts=artifacts,
                runtime=runtime,
                template=bundle.template,
            )
            payload = _build_template_validation_payload(
                bundle=bundle,
                artifacts=artifacts,
                verification=verification,
                mode=VALIDATION_MODE_LOCAL,
            )
            artifacts.write_json("validation_result.json", payload)
            return payload
        finally:
            await browser.close()


async def run_connected_template_validation(
    template_name: str,
    *,
    cdp_url: str,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from prototype.stage2.app.data_factory.generator import TemplateDataFactory
    from prototype.stage2.app.runtime.artifacts import ArtifactWriter
    from prototype.stage2.app.verification import execute_generic_template_with_shared_result

    TemplateRuntimeData = _load_template_runtime_data_class()
    _ensure_validation_matrix_symbols()
    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    artifact_root = ROOT_DIR / "artifacts" / "stage2" / "template_validation"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifacts = ArtifactWriter(artifact_root, template_name)
    run_data = TemplateDataFactory(artifacts.run_dir.name).build(
        baseline=bundle.baseline,
        schema=bundle.data_schema,
    )
    runtime = TemplateRuntimeData(
        baseline=bundle.baseline,
        run_data=run_data,
        generated_files={},
        locator_hints=bundle.locator_hints,
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            pages = []
            for context in browser.contexts:
                pages.extend(context.pages)
            if not pages:
                raise RuntimeError("未发现可用页面，无法执行 connected template validation")
            target_url = str(bundle.template.get("page_entry", {}).get("url") or "")
            page = next((item for item in pages if target_url and target_url in item.url), pages[0])
            await page.bring_to_front()
            if target_url and target_url not in page.url:
                await page.goto(target_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
            verification = await execute_generic_template_with_shared_result(
                page=page,
                artifacts=artifacts,
                runtime=runtime,
                template=bundle.template,
            )
            payload = _build_template_validation_payload(
                bundle=bundle,
                artifacts=artifacts,
                verification=verification,
                mode=VALIDATION_MODE_CONNECTED,
            )
            artifacts.write_json("validation_result.json", payload)
            return payload
        finally:
            await browser.close()


async def run_execution_goal_entrypoint(
    test_cases_path: str,
    *,
    mode: str = "fixture_simulated",
    cdp_url: str = DEFAULT_CDP_URL,
    run_id: str | None = None,
    max_rounds: int = 1,
) -> dict[str, Any]:
    """Run Stage E's execution_goal orchestrator over a generated_test_cases.json fixture.

    mode="fixture_simulated" (default) never touches a browser — same as
    calling ``ExecutionGoalOrchestrator.execute_all()`` directly.
    mode="real_browser" connects to an already-logged-in Chrome session via
    ``cdp_url`` (same connect/resolve-page pattern as
    ``run_connected_template_validation``) and drives every case for real via
    ``real_browser_runner.execute_test_case_with_playwright``. The caller is
    responsible for having already navigated that Chrome session to the
    target page before invoking this in real_browser mode — execution_goal's
    test cases span whatever page(s) they were generated for, so there is no
    single template page_entry URL to auto-navigate to here.

    max_rounds: forwarded to ``ExecutionGoalOrchestrator.run_until_stable`` —
    auto-advances through retryable failures (方案 §13 exit=retry) within
    this one process/call, up to ``max_rounds`` rounds. Default 1 preserves
    the exact previous single-round behavior. Only takes effect for
    mode="fixture_simulated"; mode="real_browser" always stops after round 1
    regardless of this value (see ``run_until_stable``'s docstring).
    """

    from prototype.stage2.app.execution_goal import ExecutionGoalOrchestrator

    run_id = run_id or f"execution_goal_{mode}_run"
    output_dir = ROOT_DIR / "artifacts" / "stage2" / "execution_goal_runs" / run_id

    test_cases = json.loads(Path(test_cases_path).read_text(encoding="utf-8"))
    if not isinstance(test_cases, list):
        raise ValueError(f"Expected list in {test_cases_path}, got {type(test_cases)}")

    orchestrator = ExecutionGoalOrchestrator(output_dir=output_dir, run_id=run_id)
    orchestrator.create_root_goal()

    async def _run_and_export(**run_kwargs: Any) -> list[dict[str, Any]]:
        rounds = await orchestrator.run_until_stable(
            test_cases, mode=mode, max_rounds=max_rounds, **run_kwargs
        )

        orchestrator.export_execution_results()
        orchestrator.export_action_log()
        orchestrator.export_network_events()
        orchestrator.export_screenshots_index()
        orchestrator.export_human_tasks()
        orchestrator.export_human_takeover()
        orchestrator.export_round_analysis()
        orchestrator.export_next_round_plan()
        orchestrator.export_run_report()
        return rounds

    if mode == "fixture_simulated":
        rounds = await _run_and_export()
    else:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            try:
                pages = []
                for context in browser.contexts:
                    pages.extend(context.pages)
                if not pages:
                    raise RuntimeError("未发现可用页面，无法执行 execution_goal real_browser 模式")
                page = pages[0]
                screenshots_dir = output_dir / "screenshots"
                rounds = await _run_and_export(page=page, screenshots_dir=screenshots_dir)
            finally:
                await browser.close()

    summary = orchestrator.get_summary()
    summary["output_dir"] = str(output_dir)
    summary["rounds_run"] = len(rounds)
    summary["round_history"] = rounds
    summary["stopped_reason"] = rounds[-1]["stopped_reason"] if rounds else None
    run_summary_path = orchestrator.export_run_summary(
        extra={
            "rounds_run": summary["rounds_run"],
            "round_history": summary["round_history"],
            "stopped_reason": summary["stopped_reason"],
        }
    )
    summary["run_summary_path"] = str(run_summary_path)
    return summary


async def _resolve_connected_page(cdp_url: str, browser: Any) -> Any:
    pages = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError("未发现可用页面，无法执行 real_browser goal-loop 发现")
    page = pages[0]
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    return page


async def run_menu_goal_entrypoint(
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    run_id: str | None = None,
    max_pages: int = 5,
) -> dict[str, Any]:
    """Run Stage B's menu_goal real-browser discovery and write menu_entries.json.

    Connects to an already-logged-in Chrome session via ``cdp_url`` (same
    connect/resolve-page pattern as ``run_connected_template_validation``),
    scans real menus via ``menu_goal.real_browser_discovery``, and writes
    TWO artifacts:

    - ``menu_entries.json``: the SAME reduced schema the fixture path
      (``MenuGoalOrchestrator.export_fixture``) already produces, for
      consumers of that established shape.
    - ``menu_entries_raw.json``: the underlying scanner's full entry list
      (``is_leaf``/``expandable``/``locator_candidates`` included) —
      ``run_page_goal_entrypoint`` chains off THIS file, not the reduced one,
      because ``page_goal.real_browser_discovery`` needs ``is_leaf`` to know
      which entries are navigable pages.
    """

    import json as _json

    from playwright.async_api import async_playwright

    from prototype.stage2.app.menu_goal import MenuGoalOrchestrator
    from prototype.stage2.app.menu_goal.real_browser_discovery import discover_menus_with_playwright

    run_id = run_id or "menu_goal_real_browser_run"
    output_dir = ROOT_DIR / "artifacts" / "stage2" / "menu_goal_runs" / run_id

    orchestrator = MenuGoalOrchestrator(output_dir=output_dir, run_id=run_id)
    root_id = orchestrator.create_root_goal()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = await _resolve_connected_page(cdp_url, browser)
            _goal_ids, raw_entries = await discover_menus_with_playwright(
                page,
                orchestrator.adapter,
                screenshots_dir=output_dir / "screenshots",
                parent_goal_id=root_id,
                max_pages=max_pages,
            )
        finally:
            await browser.close()

    fixture_path = orchestrator.export_fixture()
    raw_entries_path = output_dir / "menu_entries_raw.json"
    raw_entries_path.write_text(_json.dumps(raw_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = orchestrator.get_summary()
    summary["output_dir"] = str(output_dir)
    summary["menu_entries_path"] = str(fixture_path)
    summary["menu_entries_raw_path"] = str(raw_entries_path)
    run_summary_path = orchestrator.export_goal_summary()
    summary["run_summary_path"] = str(run_summary_path)
    return summary


async def run_page_goal_entrypoint(
    menu_entries_path: str,
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    run_id: str | None = None,
    max_pages: int = 5,
    max_features_per_page: int = 6,
) -> dict[str, Any]:
    """Run Stage C's page_goal real-browser discovery and write page_entries.json.

    ``menu_entries_path`` must be the ``menu_entries_raw.json`` written by
    ``run_menu_goal_entrypoint`` (NOT its ``menu_entries.json``) — this
    driver's underlying ``page_goal.real_browser_discovery`` filters on
    ``is_leaf``, which only the raw scanner output carries; the reduced
    fixture schema drops it (实施计划 §2.6 stage boundary).
    """

    import json

    from playwright.async_api import async_playwright

    from prototype.stage2.app.page_goal import PageGoalOrchestrator
    from prototype.stage2.app.page_goal.real_browser_discovery import discover_pages_with_playwright

    menu_entries = json.loads(Path(menu_entries_path).read_text(encoding="utf-8"))
    if not isinstance(menu_entries, list):
        raise ValueError(f"Expected a list in {menu_entries_path}, got {type(menu_entries)}")

    run_id = run_id or "page_goal_real_browser_run"
    output_dir = ROOT_DIR / "artifacts" / "stage2" / "page_goal_runs" / run_id

    orchestrator = PageGoalOrchestrator(output_dir=output_dir, run_id=run_id)
    root_id = orchestrator.create_root_goal()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = await _resolve_connected_page(cdp_url, browser)
            await discover_pages_with_playwright(
                page,
                orchestrator.adapter,
                menu_entries,
                screenshots_dir=output_dir / "screenshots",
                parent_goal_id=root_id,
                max_pages=max_pages,
                max_features_per_page=max_features_per_page,
            )
        finally:
            await browser.close()

    fixture_path = orchestrator.export_fixture()
    summary = orchestrator.get_summary()
    summary["output_dir"] = str(output_dir)
    summary["page_entries_path"] = str(fixture_path)
    run_summary_path = orchestrator.export_goal_summary()
    summary["run_summary_path"] = str(run_summary_path)
    return summary


async def run_feature_goal_entrypoint(
    page_entries_path: str,
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    run_id: str | None = None,
    max_features_per_page: int = 6,
) -> dict[str, Any]:
    """Run Stage D's feature_goal real-browser classification and write
    feature_points.json / generated_test_cases.json.

    ``page_entries_path`` is typically the ``page_entries.json`` just written
    by ``run_page_goal_entrypoint``, filtered to ``status == "reachable"``
    (same filter ``feature_goal.loader.load_feature_goals_from_page_fixture``
    applies). For each reachable page this navigates the connected Chrome
    session to that page's real URL before classifying its real DOM — unlike
    the fixture path, which never re-navigates because it only has the page's
    recorded title/URL, not a live page to visit.
    """

    import json

    from playwright.async_api import async_playwright

    from prototype.stage2.app.feature_goal import FeatureGoalOrchestrator
    from prototype.stage2.app.feature_goal.feature_fixture_writer import (
        write_discovery_review,
        write_feature_fixture,
        write_test_cases_fixture,
    )
    from prototype.stage2.app.feature_goal.real_browser_classifier import classify_features_with_playwright

    page_entries = json.loads(Path(page_entries_path).read_text(encoding="utf-8"))
    if not isinstance(page_entries, list):
        raise ValueError(f"Expected a list in {page_entries_path}, got {type(page_entries)}")
    reachable_entries = [entry for entry in page_entries if entry.get("status") == "reachable"]

    run_id = run_id or "feature_goal_real_browser_run"
    output_dir = ROOT_DIR / "artifacts" / "stage2" / "feature_goal_runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Independent engine per FeatureGoalOrchestrator's own documented
    # requirement (see feature_goal/orchestrator.py's module docstring and
    # real_browser_classifier.py's — this stage's goal.status="running"
    # convention is incompatible with a shared, activate_next()-driven engine).
    # Using the orchestrator itself (rather than hand-rolling engine/adapter)
    # so export_goal_summary()/get_summary() see the same goal tree the
    # dashboard's run_summary.json read side expects.
    orchestrator = FeatureGoalOrchestrator(output_dir=output_dir, run_id=run_id)
    adapter = orchestrator.adapter
    engine = orchestrator.engine
    root_goal_id = orchestrator.create_root_goal()

    all_test_cases: list[dict[str, Any]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = await _resolve_connected_page(cdp_url, browser)
            for entry in reachable_entries:
                page_id = entry["page_id"]
                page_url = entry.get("page_url")
                page_goal = engine.register_goal(
                    goal_type="feature",
                    goal_name=f"Discover features on {entry.get('page_title') or page_id}",
                    parent_goal_id=root_goal_id,
                    origin=f"page_features::{page_id}",
                )
                if page_url:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=8000)
                    # 1500ms, matching run_connected_template_validation's own
                    # post-navigation wait (main.py above) — 300ms was too
                    # short to observe this codebase's real target systems'
                    # Vue/Element-UI controls render after navigation (found
                    # during 2026-07-04 real suyuan-system verification: a
                    # 300ms wait produced 0 controls, 1500ms produced 6).
                    await page.wait_for_timeout(1500)
                _feature_goal_ids, test_cases = await classify_features_with_playwright(
                    page,
                    adapter,
                    page_goal.goal_id,
                    page_id=page_id,
                    screenshots_dir=output_dir / "screenshots",
                    max_features_per_page=max_features_per_page,
                )
                all_test_cases.extend(test_cases)
        finally:
            await browser.close()

    feature_points_path = output_dir / "feature_points.json"
    test_cases_path = output_dir / "generated_test_cases.json"
    discovery_review_path = output_dir / "discovery_review.json"
    write_feature_fixture(adapter, feature_points_path)
    write_test_cases_fixture(all_test_cases, test_cases_path)
    write_discovery_review(adapter, discovery_review_path)

    # get_summary()'s test_case_types breakdown reads orchestrator._test_cases
    # — classify_features_with_playwright returns cases directly rather than
    # appending to it (that append only happens in the fixture-driven
    # scan_page_features path), so backfill it here before summarizing.
    orchestrator._test_cases = all_test_cases
    run_summary_path = orchestrator.export_goal_summary()

    return {
        "run_id": run_id,
        "output_dir": str(output_dir),
        "pages_scanned": len(reachable_entries),
        "feature_count": sum(
            1 for gid, goal in engine.goals.items() if goal.origin and goal.origin.startswith("feature_entry::")
        ),
        "test_cases_generated": len(all_test_cases),
        "feature_points_path": str(feature_points_path),
        "generated_test_cases_path": str(test_cases_path),
        "discovery_review_path": str(discovery_review_path),
        "run_summary_path": str(run_summary_path),
    }


def _coerce_validation_matrix_result(
    target: ValidationMatrixTarget,
    payload: dict[str, Any],
) -> ValidationMatrixResult:
    _ensure_validation_matrix_symbols()
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"passed", "failed", "skipped"}:
        status = "passed" if payload.get("success") else "failed"
    reason = str(
        payload.get("reason")
        or payload.get("rule_summary")
        or payload.get("verification_result", {}).get("rule_evaluation", {}).get("summary")
        or ""
    )
    return ValidationMatrixResult(
        target=target,
        status=status,
        success=bool(payload.get("success")) and status == "passed",
        run_dir=str(payload.get("run_dir") or ""),
        reason=reason,
        payload=payload,
    )


def _failed_validation_matrix_result(
    target: ValidationMatrixTarget,
    *,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> ValidationMatrixResult:
    _ensure_validation_matrix_symbols()
    return ValidationMatrixResult(
        target=target,
        status=VALIDATION_STATUS_FAILED,
        success=False,
        reason=reason,
        run_dir=str((payload or {}).get("run_dir") or ""),
        payload=payload or {},
    )


def _skipped_validation_matrix_result(target: ValidationMatrixTarget, *, reason: str) -> ValidationMatrixResult:
    _ensure_validation_matrix_symbols()
    return ValidationMatrixResult(
        target=target,
        status=VALIDATION_STATUS_SKIPPED,
        success=False,
        reason=reason,
        payload={},
    )


async def run_g4_validation_matrix(
    *,
    cdp_url: str,
    targets: list[ValidationMatrixTarget] | None = None,
    output_root: Path | None = None,
    local_runner: Any = None,
    connected_runner: Any = None,
) -> dict[str, Any]:
    _ensure_validation_matrix_symbols()
    selected_targets = list(targets or build_default_g4_validation_targets())
    local_runner = local_runner or run_local_template_validation
    connected_runner = connected_runner or run_connected_template_validation
    results: list[ValidationMatrixResult] = []

    for target in selected_targets:
        try:
            if target.mode == VALIDATION_MODE_LOCAL:
                payload = await local_runner(target.template_name)
                results.append(_coerce_validation_matrix_result(target, payload))
                continue

            if target.mode == VALIDATION_MODE_CONNECTED:
                if not cdp_url:
                    results.append(_skipped_validation_matrix_result(target, reason="cdp_url_missing"))
                    continue
                payload = await connected_runner(target.template_name, cdp_url=cdp_url)
                results.append(_coerce_validation_matrix_result(target, payload))
                continue

            results.append(_skipped_validation_matrix_result(target, reason=f"unsupported_mode:{target.mode}"))
        except Exception as exc:
            results.append(
                _failed_validation_matrix_result(
                    target,
                    reason=f"{type(exc).__name__}: {exc}",
                    payload={"template_name": target.template_name, "mode": target.mode},
                )
            )

    payload = build_validation_matrix_payload(
        goal=DEFAULT_G4_VALIDATION_GOAL,
        results=results,
    )
    matrix_root = (output_root or (ROOT_DIR / "artifacts" / "stage2")) / "validation_matrix"
    history_root = matrix_root / "history"
    matrix_root.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_json_path = matrix_root / "latest_validation_matrix.json"
    latest_markdown_path = matrix_root / "latest_validation_matrix.md"
    history_json_path = history_root / f"{timestamp}_validation_matrix.json"
    history_markdown_path = history_root / f"{timestamp}_validation_matrix.md"

    payload["json_path"] = str(latest_json_path)
    payload["markdown_path"] = str(latest_markdown_path)
    payload["history_json_path"] = str(history_json_path)
    payload["history_markdown_path"] = str(history_markdown_path)

    latest_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_markdown_path.write_text(render_validation_matrix_markdown(payload), encoding="utf-8")
    history_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    history_markdown_path.write_text(render_validation_matrix_markdown(payload), encoding="utf-8")
    return payload


def generate_platform_daily_report(
    *,
    report_date: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    payloads = load_latest_run_reports(limit=limit)
    platform_report = build_platform_daily_report(payloads, report_date=report_date)
    json_path = ROOT_DIR / "artifacts" / "stage2" / "latest_platform_daily_report.json"
    markdown_path = ROOT_DIR / "artifacts" / "stage2" / "latest_platform_daily_report.md"
    json_path.write_text(
        json.dumps(platform_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_platform_daily_report_markdown(platform_report),
        encoding="utf-8",
    )
    return {
        "report_date": platform_report.report_date,
        "run_count": len(payloads),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "summary": platform_report.summary,
    }


def bootstrap_human_recording(
    template_name: str,
    *,
    session_id: str,
    operator_id: str | None = None,
    start_url: str | None = None,
    task_description: str | None = None,
) -> dict[str, str | int]:
    from prototype.stage2.app.human_loop import (
        HumanLoopRecorder,
        MinimalCandidateTemplateDraftGenerator,
        RecordingSessionConfig,
    )

    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    config = RecordingSessionConfig(
        session_id=session_id,
        template_name=template_name,
        operator_id=operator_id,
        start_url=start_url or bundle.template.get("page_entry", {}).get("url"),
        task_description=task_description or "人工演示首条成功路径并生成候选模板草稿",
        artifact_root=HUMAN_LOOP_ROOT,
        metadata={
            "bootstrap_mode": "placeholder_seed",
            **_build_human_recording_template_metadata(bundle),
        },
    )
    recorder = HumanLoopRecorder(config)
    paths = recorder.start_session()
    recorder.record_placeholder_event(
        1,
        page_url=config.start_url,
        label="等待人工在浏览器中演示首条路径",
    )
    recorder.end_session("初始化人工录制入口占位会话")
    events = recorder.load_events()
    MinimalCandidateTemplateDraftGenerator().write_artifacts(
        config=config,
        events=events,
        draft_output_path=str(paths.draft_path),
        candidate_review_output_path=str(paths.candidate_review_path),
    )
    return {
        "template": template_name,
        "session_id": session_id,
        "session_dir": str(paths.session_dir),
        "metadata_path": str(paths.metadata_path),
        "events_path": str(paths.events_path),
        "draft_path": str(paths.draft_path),
        "candidate_review_path": str(paths.candidate_review_path),
        "summary_path": str(paths.summary_path),
        "screenshot_index_path": str(paths.screenshot_index_path),
        "event_count": len(events),
    }


async def run_live_discovery(
    template_name: str,
    *,
    cdp_url: str,
    model_name: str | None = None,
    reuse_completed_discovery: bool = False,
) -> dict[str, Any]:
    from prototype.stage2.app.config import (
        build_capability_routing,
        load_model_profiles,
        validate_model_capabilities,
    )
    from prototype.stage2.app.discovery import DiscoveryArtifactWriter, run_live_discovery_session
    from prototype.stage2.app.discovery.strategy import select_discovery_strategy
    from prototype.stage2.app.orchestration.routing_summary import build_routing_summary
    from prototype.stage2.app.verification.constants import DEFAULT_ENV_FILES

    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    profiles = load_model_profiles(DEFAULT_ENV_FILES)
    if not profiles:
        raise RuntimeError("未从 demo 目录加载到模型配置，无法判断 live discovery 路由。")
    profile = profiles[0]
    if model_name:
        normalized_target = "".join(ch.lower() if ch.isalnum() else "_" for ch in model_name)
        matched = next(
            (
                item
                for item in profiles
                if item.name == model_name
                or "".join(ch.lower() if ch.isalnum() else "_" for ch in item.name) == normalized_target
            ),
            None,
        )
        if matched is None:
            raise RuntimeError(f"未找到模型配置：{model_name}")
        profile = matched
    capability_gate = validate_model_capabilities(profile, mode="stage2_run_sample")
    capability_routing = build_capability_routing(profile, gate=capability_gate)
    run_policy_summary = build_routing_summary(
        profile,
        capability_gate=capability_gate,
        capability_routing=capability_routing,
        run_policy=None,
    )
    output_dir = ROOT_DIR / "artifacts" / "stage2" / f"live_discovery_{template_name}"
    output_dir.mkdir(parents=True, exist_ok=True)
    completed_result, completed_paths = _load_completed_discovery(output_dir)
    has_completed_discovery = completed_result is not None
    discovery_strategy = select_discovery_strategy(
        capability_routing=capability_routing,
        execution_hints={"skip_completed_discovery": reuse_completed_discovery},
        has_completed_discovery=has_completed_discovery,
        allow_live_enrichment=True,
    )
    if not discovery_strategy.should_run_live_discovery:
        payload = {
            "template": template_name,
            "model": profile.name,
            "status": "reused" if discovery_strategy.reuse_completed_discovery else "blocked",
            "reason": discovery_strategy.reason,
            "selected_strategy": discovery_strategy.selected_strategy,
            "routing_summary": run_policy_summary.to_dict(),
            "discovery_strategy": discovery_strategy.to_dict(),
            "output_dir": str(output_dir),
            "requested_reuse_completed_discovery": reuse_completed_discovery,
            "reused_completed_discovery": discovery_strategy.reuse_completed_discovery,
            "has_completed_discovery": has_completed_discovery,
        }
        if completed_result is not None:
            payload.update(
                {
                    "strategy": completed_result.strategy,
                    "page_entry_count": len(completed_result.page_entries),
                    "feature_point_count": len(completed_result.feature_points),
                    "screenshot_record_count": len(completed_result.screenshot_records),
                    "page_entries_path": str(completed_paths["page_entries"]),
                    "feature_points_path": str(completed_paths["feature_points"]),
                    "screenshot_records_path": str(completed_paths["screenshot_records"]),
                    "navigation_nodes_path": str(completed_paths["navigation_nodes"]),
                    "navigation_tree_path": str(completed_paths["navigation_tree"]),
                    "page_semantic_summary_path": str(completed_paths["page_semantic_summary"]),
                    "discovery_result_path": str(completed_paths["discovery_summary"]),
                    "navigation_node_count": len(completed_result.navigation_nodes),
                    "page_semantic_count": len(completed_result.page_semantic_summary),
                    "semantic_page_type_breakdown": completed_result.stats.get("semantic_page_type_breakdown", {}),
                }
            )
        (output_dir / "routing_summary.json").write_text(
            json.dumps(run_policy_summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "discovery_strategy.json").write_text(
            json.dumps(discovery_strategy.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "live_discovery_blocked.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload
    result = await run_live_discovery_session(
        cdp_url=cdp_url,
        template_name=template_name,
        template=bundle.template,
        baseline=bundle.baseline,
        output_dir=output_dir,
    )

    paths = DiscoveryArtifactWriter(output_dir).write(result)
    (output_dir / "routing_summary.json").write_text(
        json.dumps(run_policy_summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "discovery_strategy.json").write_text(
        json.dumps(discovery_strategy.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "template": template_name,
        "model": profile.name,
        "strategy": result.strategy,
        "selected_strategy": discovery_strategy.selected_strategy,
        "output_dir": str(output_dir),
        "page_entry_count": len(result.page_entries),
        "feature_point_count": len(result.feature_points),
        "screenshot_record_count": len(result.screenshot_records),
        "navigation_node_count": len(result.navigation_nodes),
        "page_semantic_count": len(result.page_semantic_summary),
        "semantic_page_type_breakdown": result.stats.get("semantic_page_type_breakdown", {}),
        "page_entries_path": str(paths["page_entries"]),
        "feature_points_path": str(paths["feature_points"]),
        "screenshot_records_path": str(paths["screenshot_records"]),
        "navigation_nodes_path": str(paths["navigation_nodes"]),
        "navigation_tree_path": str(paths["navigation_tree"]),
        "page_semantic_summary_path": str(paths["page_semantic_summary"]),
        "discovery_result_path": str(paths["discovery_summary"]),
    }


async def explore_system_map(
    *,
    target_name: str,
    start_url: str,
    cdp_url: str,
    model_name: str | None = None,
    template_name: str = "",
    overwrite_template: bool = False,
) -> dict[str, Any]:
    bootstrap_payload = bootstrap_system_exploration_template(
        target_name=target_name,
        start_url=start_url,
        template_name=template_name,
        overwrite=overwrite_template,
    )
    discovery_payload = await run_live_discovery(
        bootstrap_payload["template"],
        cdp_url=cdp_url,
        model_name=model_name,
    )
    return {
        "target_name": target_name,
        "template": bootstrap_payload["template"],
        "template_dir": bootstrap_payload["template_dir"],
        "mode": "system_map_exploration",
        "bootstrap": bootstrap_payload,
        "discovery": discovery_payload,
        "recommended_next_steps": [
            "检查 navigation_tree.json 和 page_semantic_summary.json，确认系统地图和页面类型初分是否合理。",
            "如需更精细线索，再执行 --capture-human-recording。",
            "确认首批要验证的页面后，再执行 --template-revision-checklist 或补具体模板。",
        ],
    }


async def capture_human_recording(
    template_name: str,
    *,
    session_id: str,
    operator_id: str | None,
    start_url: str | None,
    task_description: str,
    cdp_url: str,
    duration_seconds: int,
) -> dict[str, str | int]:
    from prototype.stage2.app.human_loop import RecordingSessionConfig, record_human_loop_from_cdp

    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    config = RecordingSessionConfig(
        session_id=session_id,
        template_name=template_name,
        operator_id=operator_id,
        start_url=start_url or bundle.template.get("page_entry", {}).get("url"),
        task_description=task_description,
        artifact_root=HUMAN_LOOP_ROOT,
        metadata={
            "capture_mode": "playwright_cdp",
            **_build_human_recording_template_metadata(bundle),
        },
    )
    result = await record_human_loop_from_cdp(
        cdp_url=cdp_url,
        config=config,
        duration_seconds=duration_seconds,
    )
    return {
        "template": template_name,
        "session_id": session_id,
        "session_dir": result.session_dir,
        "metadata_path": result.metadata_path,
        "events_path": result.events_path,
        "draft_path": result.draft_path,
        "candidate_review_path": result.candidate_review_path,
        "summary_path": result.summary_path,
        "screenshot_index_path": result.screenshot_index_path,
        "capture_summary": result.capture_summary,
        "event_count": result.event_count,
        "duration_seconds": duration_seconds,
    }


async def run_stage2_sample_entrypoint(
    *,
    cdp_url: str,
    max_attempts: int,
    max_rounds: int,
) -> dict[str, Any]:
    from tools.suyuan_submit_loop import run_stage2_sample

    return await run_stage2_sample(
        cdp_url=cdp_url,
        max_attempts=max_attempts,
        max_rounds=_resolve_cli_sample_max_rounds(max_rounds),
    )


async def run_v3_assessment_entrypoint(
    *,
    target_name: str,
    start_url: str,
    cdp_url: str,
    model_name: str | None,
    model_profile_ids: list[str] | None,
    run_id: str,
    round_id: str,
    round_stage: str,
    artifact_root: str,
    use_live_discovery: bool,
    execution_mode: str,
    safety_policy: str,
    scope: str,
    prioritized_targets: list[str] | None,
    waived_targets: list[str] | None,
    allowed_side_effect_actions: list[str] | None,
    reuse_run_dir: bool,
    max_pages: int,
    max_features_per_page: int,
    template_name: str,
) -> dict[str, Any]:
    from prototype.stage2.app.v3_orchestrator import V3RunConfig, run_v3_assessment

    artifact_root_path = (
        Path(artifact_root)
        if artifact_root
        else ROOT_DIR / "artifacts" / "stage2" / "v3_runs"
    )
    selected_model_profile_ids = list(model_profile_ids or ())
    effective_model_name = model_name or (selected_model_profile_ids[0] if selected_model_profile_ids else None)
    config = V3RunConfig(
        target_name=target_name or "第二阶段 v3 演示系统",
        start_url=start_url,
        cdp_url=cdp_url,
        execution_mode=execution_mode,
        safety_policy=safety_policy,
        allowed_side_effect_actions=tuple(allowed_side_effect_actions or ()),
        reuse_run_dir=reuse_run_dir,
        model_name=effective_model_name,
        run_id=run_id,
        round_id=round_id,
        round_stage=round_stage,
        artifact_root=artifact_root_path,
        use_live_discovery=bool(use_live_discovery and start_url),
        max_pages=max_pages,
        max_features_per_page=max_features_per_page,
        metadata={
            "entrypoint": "prototype.stage2.main --run-v3",
            "template_name": template_name,
            "round_id": round_id,
            "round_stage": round_stage,
            "scope": scope,
            "prioritized_targets": list(prioritized_targets or ()),
            "waived_targets": list(waived_targets or ()),
            "selected_model_profile_ids": selected_model_profile_ids,
            "safety_policy": safety_policy,
            "allowed_side_effect_actions": list(allowed_side_effect_actions or ()),
        },
    )

    async def discovery_provider() -> dict[str, Any]:
        return await explore_system_map(
            target_name=config.target_name,
            start_url=config.start_url,
            cdp_url=config.cdp_url,
            model_name=config.model_name,
            template_name=template_name,
        )

    return await run_v3_assessment(
        config,
        discovery_provider=discovery_provider if config.use_live_discovery else None,
    )


async def resume_human_takeover_entrypoint(
    *,
    run_dir: str,
    cdp_url: str,
    max_attempts: int,
    max_rounds: int,
    operator_id: str | None,
    note: str | None,
) -> dict[str, Any]:
    from tools.suyuan_submit_loop import resume_profile_from_human_takeover

    return await resume_profile_from_human_takeover(
        Path(run_dir),
        cdp_url=cdp_url,
        max_attempts=max_attempts,
        max_rounds=_resolve_cli_resume_max_rounds(max_rounds),
        operator_id=operator_id,
        note=note,
    )


def resolve_goal_loop_takeover_entrypoint(
    run_dir: str,
    *,
    operator_id: str | None = None,
    note: str | None = None,
    ready_to_resume: bool = False,
) -> dict[str, Any]:
    """Record that a human reviewed a goal_loop-family run's pending takeover.

    This is NOT --resume-human-takeover's counterpart in the sense of
    reconstructing and continuing a paused run — ``GoalLoopEngine`` has no
    serialization, so the in-memory engine that paused is gone the moment
    its CLI process exited. This only writes ``human_takeover_resolution.json``
    (via ``goal_loop.resolution_writer.write_human_takeover_resolution``, the
    same schema ``cross_system_goal`` already produces) confirming a human
    looked at ``human_takeover.json`` and what they decided.

    Requires ``run_dir/human_takeover.json`` to actually exist with
    ``status == "waiting_human"`` — refuses to fabricate a resolution record
    against a run_dir that never raised a real takeover request.

    To actually continue the work after this, re-invoke the relevant
    ``--run-execution-goal``/``--run-menu-goal``/``--run-page-goal``/
    ``--run-feature-goal`` command with fresh inputs; ``ready_to_resume`` is
    a recorded signal for a human reader, not a trigger.
    """

    from datetime import datetime, timezone

    from prototype.stage2.app.goal_loop.resolution_writer import write_human_takeover_resolution

    run_dir_path = Path(run_dir)
    takeover_path = run_dir_path / "human_takeover.json"
    if not takeover_path.exists():
        raise RuntimeError(
            f"{takeover_path} 不存在，无法确认这是一个真实的目标循环人工接管请求；"
            "请确认 run_dir 是否正确，或该次运行是否真的触发了人工接管。"
        )
    takeover = json.loads(takeover_path.read_text(encoding="utf-8"))
    if takeover.get("status") != "waiting_human":
        raise RuntimeError(
            f"{takeover_path} 的 status 是 {takeover.get('status')!r}，不是 'waiting_human'；"
            "该接管请求可能已经被处理过，或这份文件不是目标循环家族写出的格式。"
        )

    resolved_at = datetime.now(timezone.utc).isoformat()
    resolution_path = write_human_takeover_resolution(
        run_dir_path,
        status="resolved",
        operator_id=operator_id,
        note=note,
        ready_to_resume=ready_to_resume,
        resolved_at=resolved_at,
    )

    next_step = (
        "人工接管已记录为可继续；请重新执行对应的 --run-execution-goal / --run-menu-goal / "
        "--run-page-goal / --run-feature-goal 命令，用新的输入开始下一轮。"
        if ready_to_resume
        else "人工接管已记录，但尚未标记为可继续（未传 --resolve-ready-to-resume）。"
    )

    return {
        "run_dir": str(run_dir_path),
        "human_takeover_resolution_path": str(resolution_path),
        "waiting_reason": takeover.get("waiting_reason"),
        "pending_action_count": len(takeover.get("pending_actions") or []),
        "ready_to_resume": ready_to_resume,
        "next_step": next_step,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 prototype entrypoint.")
    parser.add_argument(
        "--bootstrap-template",
        action="store_true",
        help="Create a minimal template scaffold so discovery and human recording can start without hand-writing all four template files.",
    )
    parser.add_argument(
        "--explore-system-map",
        action="store_true",
        help="Bootstrap a minimal system-map template, then run live discovery to generate navigation tree and page semantic summary for a new system.",
    )
    parser.add_argument(
        "--template-revision-checklist",
        action="store_true",
        help="Build a semi-automatic revision checklist from discovery and human recording artifacts for the given template.",
    )
    parser.add_argument(
        "--init-run",
        action="store_true",
        help="Initialize run directories, progress artifacts, and runtime snapshots for the template.",
    )
    parser.add_argument(
        "--template",
        default="suyuan_online_apply",
        help="Template name to inspect or initialize.",
    )
    parser.add_argument(
        "--page-url",
        default="",
        help="Start URL used by --bootstrap-template.",
    )
    parser.add_argument(
        "--target-name",
        default="",
        help="Human-readable system name used by --explore-system-map.",
    )
    parser.add_argument(
        "--page-name",
        default="",
        help="Optional page entry name used by --bootstrap-template.",
    )
    parser.add_argument(
        "--feature-name",
        default="",
        help="Optional feature point name used by --bootstrap-template.",
    )
    parser.add_argument(
        "--feature-type",
        default="",
        help="Optional feature point type override used by --bootstrap-template.",
    )
    parser.add_argument(
        "--scenario-kind",
        default="navigation",
        help="Bootstrap scenario kind: navigation, query, detail, create, edit, or generic.",
    )
    parser.add_argument(
        "--bootstrap-overwrite",
        action="store_true",
        help="Allow --bootstrap-template to overwrite an existing template directory.",
    )
    parser.add_argument(
        "--discovery-dir",
        default="",
        help="Optional explicit discovery artifact directory used by --template-revision-checklist.",
    )
    parser.add_argument(
        "--candidate-review",
        default="",
        help="Optional explicit candidate_template_review.json path used by --template-revision-checklist.",
    )
    parser.add_argument(
        "--checklist-output-dir",
        default="",
        help="Optional explicit output directory used by --template-revision-checklist.",
    )
    parser.add_argument(
        "--init-human-recording",
        action="store_true",
        help="Bootstrap a minimal human-loop recording session and candidate draft.",
    )
    parser.add_argument(
        "--recording-session",
        default="stage2_manual_recording",
        help="Session id for the human recording bootstrap.",
    )
    parser.add_argument(
        "--recording-operator",
        default="manual-reviewer",
        help="Operator id recorded in the human recording bootstrap.",
    )
    parser.add_argument(
        "--recording-url",
        default="",
        help="Optional start URL override for the human recording bootstrap.",
    )
    parser.add_argument(
        "--recording-task",
        default="人工演示首条成功路径并生成候选模板草稿",
        help="Task description stored in the human recording bootstrap.",
    )
    parser.add_argument(
        "--live-discovery",
        action="store_true",
        help="Run controlled live discovery against the currently connected Chrome CDP session.",
    )
    parser.add_argument(
        "--reuse-completed-discovery",
        action="store_true",
        help="When running live discovery, reuse an existing completed discovery_result.json if one is already present for the template output directory.",
    )
    parser.add_argument(
        "--routing-summary",
        action="store_true",
        help="Show per-model routing and discovery-strategy summaries for the current template.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional model/profile name used by live discovery or for filtering routing summaries.",
    )
    parser.add_argument(
        "--capture-human-recording",
        action="store_true",
        help="Capture real DOM events from the currently connected Chrome CDP session.",
    )
    parser.add_argument(
        "--cdp-url",
        default=DEFAULT_CDP_URL,
        help="Chrome CDP endpoint used by live discovery or human recording capture.",
    )
    parser.add_argument(
        "--capture-seconds",
        type=int,
        default=20,
        help="Duration in seconds for real human recording capture.",
    )
    parser.add_argument(
        "--run-sample",
        action="store_true",
        help="Run the full stage-2 sample pipeline for the current template via the unified platform entrypoint.",
    )
    parser.add_argument(
        "--run-v3",
        action="store_true",
        help="Run the stage-2 v3 run-centered minimum loop and write v3 contract artifacts.",
    )
    parser.add_argument(
        "--v3-run-id",
        default="",
        help="Optional explicit run id for --run-v3.",
    )
    parser.add_argument(
        "--v3-artifact-root",
        default="",
        help="Optional artifact root for --run-v3. Defaults to artifacts/stage2/v3_runs.",
    )
    parser.add_argument(
        "--v3-round-id",
        default="round_001",
        help="For --run-v3, current run round id, e.g. round_001 or round_002.",
    )
    parser.add_argument(
        "--v3-round-stage",
        choices=("menu_discovery", "page_feature_verification", "full_assessment"),
        default="full_assessment",
        help="For --run-v3, select which stage this round should execute.",
    )
    parser.add_argument(
        "--v3-use-live-discovery",
        action="store_true",
        help="For --run-v3, call the existing system-map/live-discovery flow before generating cases.",
    )
    parser.add_argument(
        "--v3-execution-mode",
        choices=("contract_only", "real_browser"),
        default="contract_only",
        help="For --run-v3, choose contract_only or real_browser execution.",
    )
    parser.add_argument(
        "--v3-safety-policy",
        choices=("low_risk_only", "test_env_full_access"),
        default="low_risk_only",
        help="For --run-v3, choose low_risk_only or test_env_full_access.",
    )
    parser.add_argument(
        "--v3-scope",
        default="",
        help="For --run-v3, user-provided exploration scope or priority target text.",
    )
    parser.add_argument(
        "--v3-prioritized-target",
        action="append",
        default=[],
        help="For --run-v3, prioritized target page/menu text. May be repeated.",
    )
    parser.add_argument(
        "--v3-waived-target",
        action="append",
        default=[],
        help="For --run-v3, user-waived target page/menu text. May be repeated.",
    )
    parser.add_argument(
        "--v3-allow-side-effect-action",
        action="append",
        default=[],
        help=(
            "For --run-v3, allow a side-effect action type or id in test_env_full_access. "
            "May be repeated, e.g. --v3-allow-side-effect-action submit --v3-allow-side-effect-action delete."
        ),
    )
    parser.add_argument(
        "--v3-model-profile",
        action="append",
        default=[],
        help="For --run-v3, selected model profile id. May be repeated.",
    )
    parser.add_argument(
        "--v3-reuse-run-dir",
        action="store_true",
        help="For --run-v3, write into an existing run directory instead of creating a suffixed directory.",
    )
    parser.add_argument(
        "--v3-max-pages",
        type=int,
        default=5,
        help="Maximum page entries retained in a v3 run.",
    )
    parser.add_argument(
        "--v3-max-features-per-page",
        type=int,
        default=6,
        help="Maximum feature points retained per page in a v3 run.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum attempts used by the stage-2 sample pipeline.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help="Maximum orchestration rounds. On human-takeover resume, the requested value is capped by the remaining round budget from round_input.json.",
    )
    parser.add_argument(
        "--platform-daily-report",
        action="store_true",
        help="Aggregate the latest run reports into a platform daily report bundle.",
    )
    parser.add_argument(
        "--resume-human-takeover",
        default="",
        help="Resume a previously blocked run directory after human takeover or manual review is completed.",
    )
    parser.add_argument(
        "--resume-operator",
        default="",
        help="Optional operator id recorded when resuming a human takeover.",
    )
    parser.add_argument(
        "--resume-note",
        default="",
        help="Optional note recorded when resuming a human takeover.",
    )
    parser.add_argument(
        "--resolve-goal-loop-takeover",
        default="",
        help="Record that a human reviewed a goal_loop-family run's human_takeover.json "
        "(writes human_takeover_resolution.json). Does NOT reconstruct or continue the "
        "paused run — re-invoke the relevant --run-*-goal command afterward. Use this, "
        "NOT --resume-human-takeover, for menu/page/feature/execution_goal run directories.",
    )
    parser.add_argument(
        "--resolve-ready-to-resume",
        action="store_true",
        help="For --resolve-goal-loop-takeover, mark the takeover as ready for a follow-up "
        "round (recorded only — does not itself start one).",
    )
    parser.add_argument(
        "--validate-template",
        default="",
        help="Run a local generic-template validation for the given template name.",
    )
    parser.add_argument(
        "--validate-connected-template",
        default="",
        help="Run a generic-template validation against the currently connected Chrome CDP session.",
    )
    parser.add_argument(
        "--validation-matrix",
        action="store_true",
        help="Run the G4 cross-system validation matrix and write aggregated json/markdown artifacts.",
    )
    parser.add_argument(
        "--run-execution-goal",
        action="store_true",
        help="Run Stage E's execution_goal orchestrator over a generated_test_cases.json fixture.",
    )
    parser.add_argument(
        "--execution-goal-test-cases",
        default="",
        help="Path to generated_test_cases.json for --run-execution-goal (required).",
    )
    parser.add_argument(
        "--execution-goal-mode",
        choices=("fixture_simulated", "real_browser"),
        default="fixture_simulated",
        help="For --run-execution-goal, choose fixture_simulated (default, no browser) or real_browser "
        "(connects via --cdp-url to an already-logged-in Chrome session).",
    )
    parser.add_argument(
        "--execution-goal-run-id",
        default="",
        help="Optional explicit run id for --run-execution-goal.",
    )
    parser.add_argument(
        "--execution-goal-max-rounds",
        type=int,
        default=1,
        help="For --run-execution-goal, how many rounds to auto-advance through retryable "
        "failures within this single process (default 1 = no auto-advance, current behavior). "
        "Only applies to mode=fixture_simulated; mode=real_browser always stops after round 1 "
        "regardless of this value, since retrying a live production action unattended is not safe.",
    )
    parser.add_argument(
        "--run-menu-goal",
        action="store_true",
        help="Run Stage B's menu_goal real-browser discovery against the currently connected "
        "Chrome CDP session and write menu_entries.json.",
    )
    parser.add_argument(
        "--run-page-goal",
        action="store_true",
        help="Run Stage C's page_goal real-browser discovery over the menu entries just "
        "discovered by --run-menu-goal (or --goal-chain-menu-entries) and write page_entries.json.",
    )
    parser.add_argument(
        "--run-feature-goal",
        action="store_true",
        help="Run Stage D's feature_goal real-browser classification over the reachable pages "
        "just discovered by --run-page-goal (or --goal-chain-page-entries) and write "
        "feature_points.json / generated_test_cases.json.",
    )
    parser.add_argument(
        "--goal-chain-menu-entries",
        default="",
        help="Path to the menu_entries_raw.json written by --run-menu-goal (NOT its "
        "menu_entries.json — the raw file carries is_leaf, which --run-page-goal requires). "
        "Required for --run-page-goal since each CLI invocation is a separate process.",
    )
    parser.add_argument(
        "--goal-chain-page-entries",
        default="",
        help="Optional explicit page_entries.json path for --run-feature-goal, when not chaining "
        "directly after --run-page-goal in the same invocation.",
    )
    parser.add_argument(
        "--goal-chain-run-id",
        default="",
        help="Optional explicit run id shared by --run-menu-goal/--run-page-goal/--run-feature-goal.",
    )
    parser.add_argument(
        "--goal-chain-max-pages",
        type=int,
        default=5,
        help="Maximum pages/menu-expansion budget for the real-browser menu/page goal drivers.",
    )
    parser.add_argument(
        "--goal-chain-max-features-per-page",
        type=int,
        default=6,
        help="Maximum features-per-page budget for the real-browser page/feature goal drivers.",
    )
    parser.add_argument(
        "--report-date",
        default="",
        help="Optional explicit date string for the generated platform daily report.",
    )
    parser.add_argument(
        "--report-limit",
        type=int,
        default=20,
        help="Maximum number of recent run reports to include when aggregating the platform daily report.",
    )
    args = parser.parse_args()

    if args.bootstrap_template:
        print(
            json.dumps(
                bootstrap_template(
                    args.template,
                    page_url=args.page_url,
                    page_name=args.page_name,
                    feature_name=args.feature_name,
                    feature_type=args.feature_type,
                    scenario_kind=args.scenario_kind,
                    overwrite=args.bootstrap_overwrite,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.explore_system_map:
        print(
            json.dumps(
                asyncio.run(
                    explore_system_map(
                        target_name=args.target_name or args.template or "新系统",
                        start_url=args.page_url,
                        cdp_url=args.cdp_url,
                        model_name=args.model or None,
                        template_name=args.template,
                        overwrite_template=args.bootstrap_overwrite,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.template_revision_checklist:
        print(
            json.dumps(
                generate_template_revision_checklist(
                    args.template,
                    discovery_dir=args.discovery_dir,
                    candidate_review_path=args.candidate_review,
                    output_dir=args.checklist_output_dir,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.init_run:
        print(json.dumps(initialize_runs(template_name=args.template), ensure_ascii=False, indent=2))
        return

    if args.init_human_recording:
        print(
            json.dumps(
                bootstrap_human_recording(
                    args.template,
                    session_id=args.recording_session,
                    operator_id=args.recording_operator,
                    start_url=args.recording_url or None,
                    task_description=args.recording_task,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.live_discovery:
        print(
            json.dumps(
                asyncio.run(
                    run_live_discovery(
                        args.template,
                        cdp_url=args.cdp_url,
                        model_name=args.model or None,
                        reuse_completed_discovery=args.reuse_completed_discovery,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.routing_summary:
        summaries = build_routing_summaries(template_name=args.template)
        if args.model:
            normalized_target = "".join(ch.lower() if ch.isalnum() else "_" for ch in args.model)
            summaries = [
                item
                for item in summaries
                if item.get("model") == args.model
                or "".join(ch.lower() if ch.isalnum() else "_" for ch in str(item.get("model", ""))) == normalized_target
            ]
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return

    if args.capture_human_recording:
        print(
            json.dumps(
                asyncio.run(
                    capture_human_recording(
                        args.template,
                        session_id=args.recording_session,
                        operator_id=args.recording_operator,
                        start_url=args.recording_url or None,
                        task_description=args.recording_task,
                        cdp_url=args.cdp_url,
                        duration_seconds=args.capture_seconds,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.run_sample:
        print(
            json.dumps(
                asyncio.run(
                    run_stage2_sample_entrypoint(
                        cdp_url=args.cdp_url,
                        max_attempts=args.max_attempts,
                        max_rounds=args.max_rounds,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.run_v3:
        print(
            json.dumps(
                asyncio.run(
                    run_v3_assessment_entrypoint(
                        target_name=args.target_name or args.template or "第二阶段 v3 演示系统",
                        start_url=args.page_url,
                        cdp_url=args.cdp_url,
                        model_name=args.model or None,
                        model_profile_ids=args.v3_model_profile,
                        run_id=args.v3_run_id,
                        round_id=args.v3_round_id,
                        round_stage=args.v3_round_stage,
                        artifact_root=args.v3_artifact_root,
                        use_live_discovery=args.v3_use_live_discovery,
                        execution_mode=args.v3_execution_mode,
                        safety_policy=args.v3_safety_policy,
                        scope=args.v3_scope,
                        prioritized_targets=args.v3_prioritized_target,
                        waived_targets=args.v3_waived_target,
                        allowed_side_effect_actions=args.v3_allow_side_effect_action,
                        reuse_run_dir=args.v3_reuse_run_dir,
                        max_pages=args.v3_max_pages,
                        max_features_per_page=args.v3_max_features_per_page,
                        template_name=args.template
                        if args.template != "suyuan_online_apply"
                        else "",
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.platform_daily_report:
        print(
            json.dumps(
                generate_platform_daily_report(
                    report_date=args.report_date or None,
                    limit=args.report_limit,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.resume_human_takeover:
        print(
            json.dumps(
                asyncio.run(
                    resume_human_takeover_entrypoint(
                        run_dir=args.resume_human_takeover,
                        cdp_url=args.cdp_url,
                        max_attempts=args.max_attempts,
                        max_rounds=args.max_rounds,
                        operator_id=args.resume_operator or None,
                        note=args.resume_note or None,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.resolve_goal_loop_takeover:
        print(
            json.dumps(
                resolve_goal_loop_takeover_entrypoint(
                    args.resolve_goal_loop_takeover,
                    operator_id=args.resume_operator or None,
                    note=args.resume_note or None,
                    ready_to_resume=args.resolve_ready_to_resume,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.validate_template:
        print(
            json.dumps(
                asyncio.run(run_local_template_validation(args.validate_template)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.validate_connected_template:
        print(
            json.dumps(
                asyncio.run(
                    run_connected_template_validation(
                        args.validate_connected_template,
                        cdp_url=args.cdp_url,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.validation_matrix:
        print(
            json.dumps(
                asyncio.run(
                    run_g4_validation_matrix(
                        cdp_url=args.cdp_url,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.run_execution_goal:
        if not args.execution_goal_test_cases:
            raise SystemExit("--run-execution-goal requires --execution-goal-test-cases <path>")
        print(
            json.dumps(
                asyncio.run(
                    run_execution_goal_entrypoint(
                        args.execution_goal_test_cases,
                        mode=args.execution_goal_mode,
                        cdp_url=args.cdp_url,
                        run_id=args.execution_goal_run_id or None,
                        max_rounds=args.execution_goal_max_rounds,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.run_menu_goal:
        print(
            json.dumps(
                asyncio.run(
                    run_menu_goal_entrypoint(
                        cdp_url=args.cdp_url,
                        run_id=args.goal_chain_run_id or None,
                        max_pages=args.goal_chain_max_pages,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.run_page_goal:
        if not args.goal_chain_menu_entries:
            raise SystemExit("--run-page-goal requires --goal-chain-menu-entries <path>")
        print(
            json.dumps(
                asyncio.run(
                    run_page_goal_entrypoint(
                        args.goal_chain_menu_entries,
                        cdp_url=args.cdp_url,
                        run_id=args.goal_chain_run_id or None,
                        max_pages=args.goal_chain_max_pages,
                        max_features_per_page=args.goal_chain_max_features_per_page,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.run_feature_goal:
        if not args.goal_chain_page_entries:
            raise SystemExit("--run-feature-goal requires --goal-chain-page-entries <path>")
        print(
            json.dumps(
                asyncio.run(
                    run_feature_goal_entrypoint(
                        args.goal_chain_page_entries,
                        cdp_url=args.cdp_url,
                        run_id=args.goal_chain_run_id or None,
                        max_features_per_page=args.goal_chain_max_features_per_page,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(json.dumps(list_templates(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
