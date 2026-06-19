from __future__ import annotations

import json
import os
from pathlib import Path

from prototype.stage2.app.config.models import load_model_profiles
from prototype.stage2.app.data_factory.generator import TemplateDataFactory
from prototype.stage2.app.discovery import DiscoveryArtifactWriter, DiscoveryPlanner
from prototype.stage2.app.iteration import write_iteration_artifacts
from prototype.stage2.app.orchestration.run_context import Stage2RunContext
from prototype.stage2.app.progress import ProgressManager
from prototype.stage2.app.reporting import (
    adapt_progress_snapshot,
    build_platform_daily_report,
    render_progress_markdown,
    render_platform_daily_report_markdown,
    render_run_report_markdown,
)
from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.verification.constants import ARTIFACT_ROOT, DEFAULT_CDP_URL, DEFAULT_ENV_FILES
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData


def _map_discovery_page_entries(discovery_result: object) -> list[dict[str, str]]:
    result = getattr(discovery_result, "page_entries", [])
    return [
        {
            "item_id": item.page_entry_id,
            "name": item.name,
            "status": "已发现",
            "summary": item.url,
            "source": item.source,
        }
        for item in result
    ]


def _map_discovery_feature_points(discovery_result: object) -> list[dict[str, str]]:
    result = getattr(discovery_result, "feature_points", [])
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


def _build_iteration_asset_refs(run_dir: Path) -> list[dict[str, str]]:
    labels = [
        "failure_clusters.json",
        "retry_plan.json",
        "promotion_candidates.json",
        "stop_conditions.json",
        "iteration_comparison.json",
        "next_round_decision.json",
    ]
    return [{"label": label, "path": str(run_dir / label)} for label in labels]


def build_run_contexts(template_name: str = "suyuan_online_apply") -> list[Stage2RunContext]:
    template_dir = Path(__file__).resolve().parents[2] / "templates" / template_name
    bundle = load_template_bundle(template_dir)
    profiles = load_model_profiles(DEFAULT_ENV_FILES)
    if not profiles:
        raise RuntimeError("未从 demo 目录加载到模型配置")

    cdp_url = os.getenv("SUYUAN_CDP_URL", DEFAULT_CDP_URL)
    max_attempts = int(os.getenv("SUYUAN_MAX_ATTEMPTS", "3"))
    contexts: list[Stage2RunContext] = []
    for profile in profiles:
        artifacts = ArtifactWriter(ARTIFACT_ROOT, profile.name)
        run_data = TemplateDataFactory(artifacts.run_dir.name).build(
            baseline=bundle.baseline,
            schema=bundle.data_schema,
        )
        runtime = TemplateRuntimeData(
            baseline=bundle.baseline,
            run_data=run_data,
            generated_files={},
        )
        progress = ProgressManager(
            run_id=artifacts.run_dir.name,
            output_dir=artifacts.run_dir,
            template_name=template_name,
            model_name=profile.name,
            project_name="AI Agent 软件自动化评测平台第二阶段原型",
        )
        progress.start_phase(
            "preflight",
            phase_label="预检",
            message="初始化运行上下文并装载模板数据",
            next_action="生成运行时数据并准备验证样本",
            stats={"profiles_loaded": len(profiles)},
        )
        artifacts.write_json("template_snapshot.json", bundle.template)
        artifacts.write_json("baseline_snapshot.json", bundle.baseline)
        artifacts.write_json("data_schema_snapshot.json", bundle.data_schema)
        artifacts.write_json("runtime_data.json", run_data)
        progress.complete_phase(
            "preflight",
            phase_label="预检",
            message="模板快照与运行时数据已落盘",
            next_action="进入发现阶段并播种页面入口",
            stats={"template_steps": len(bundle.template.get("steps", []))},
        )
        progress.start_phase(
            "discovery",
            phase_label="发现",
            message="基于已验证模板播种页面入口和功能点",
            next_action="落盘 discovery 最小闭环产物",
        )
        discovery_result = DiscoveryPlanner().plan(
            template_name=template_name,
            template=bundle.template,
            baseline=bundle.baseline,
        )
        discovery_paths = DiscoveryArtifactWriter(artifacts.run_dir).write(discovery_result)
        progress.complete_phase(
            "discovery",
            phase_label="发现",
            message="已生成页面入口清单和功能点清单",
            next_action="写入初始化报告与循环占位产物",
            stats={
                "page_entries_discovered": len(discovery_result.page_entries),
                "feature_points_discovered": len(discovery_result.feature_points),
            },
        )
        progress_snapshot = adapt_progress_snapshot(progress.snapshot)
        report_payload = {
            "summary": {
                "run_id": artifacts.run_dir.name,
                "status": "initialized",
                "project_name": "AI Agent 软件自动化评测平台第二阶段原型",
                "template_name": template_name,
                "started_at": progress.snapshot.started_at,
                "finished_at": progress.snapshot.updated_at,
                "stop_reason": "初始化完成，等待验证执行器接入",
                "next_action": "执行线上备案申请模板样本",
                "counts": [
                    {"label": "profiles_loaded", "value": len(profiles)},
                    {"label": "template_steps", "value": len(bundle.template.get("steps", []))},
                    {"label": "page_entries_discovered", "value": len(discovery_result.page_entries)},
                    {"label": "feature_points_discovered", "value": len(discovery_result.feature_points)},
                ],
            },
            "page_entries": _map_discovery_page_entries(discovery_result),
            "feature_points": _map_discovery_feature_points(discovery_result),
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
                    "name": "Discovery Outputs",
                    "status": "generated",
                    "artifacts": [
                        {"label": key, "path": str(path)}
                        for key, path in discovery_paths.items()
                    ],
                },
            ],
            "notes": [
                "这是第二阶段原型首批任务的初始化报告，尚未真正执行浏览器验证。",
                "发现阶段已接入模板播种式最小闭环。",
                f"当前平台级状态：{progress.snapshot.overall_status} / {progress.snapshot.current_phase}",
            ],
        }
        write_iteration_artifacts(
            artifacts.run_dir,
            run_report=report_payload,
            status_snapshot=progress_snapshot,
            attempts=[],
            max_attempts=max_attempts,
        )
        report_payload["project_assets"].append(
            {
                "name": "Iteration Outputs",
                "status": "generated",
                "artifacts": _build_iteration_asset_refs(artifacts.run_dir),
            }
        )
        report_payload["daily_summary"] = {
            "summary": "初始化 run 已创建 discovery 和 iteration 占位产物，等待真实浏览器验证接入。",
            "new_templates": [
                {
                    "name": template_name,
                    "status": "initialized",
                    "summary": "模板快照、运行时数据和发现产物已落盘。",
                }
            ],
            "watch_items": [
                {
                    "name": "verification_executor_pending",
                    "status": "pending",
                    "summary": "初始化报告阶段尚未真正执行浏览器验证。",
                }
            ],
        }
        report_payload["model_comparison_summary"] = {
            "title": "Model Comparison Summary",
            "summary": "当前仅完成初始化阶段，模型对比需等待真实验证执行结果后再补齐。",
            "items": [
                {
                    "name": profile.name,
                    "status": "initialized",
                    "summary": "已装载模板与 runtime data，待进入真实验证阶段。",
                }
            ],
        }
        report_payload["skill_inventory_summary"] = {
            "summary": "当前 run 仅完成模板播种和初始化沉淀。",
            "runtime_skills": [
                {
                    "name": "template_seed_discovery",
                    "status": "available",
                    "summary": "模板播种式 discovery 已可输出页面入口与功能点清单。",
                }
            ],
            "project_skills": [
                {
                    "name": "suyuan_online_apply",
                    "status": "available",
                    "summary": "首个项目模板样本已完成初始化。",
                }
            ],
        }
        report_payload["promotion_candidate_summary"] = {
            "summary": "初始化阶段不自动晋升平台级候选，等待真实验证与人工审查。",
            "approval_notes": [
                "平台级晋升必须在真实验证 run 之后结合证据审查。",
            ],
        }
        artifacts.write_json("reports/run_report.json", report_payload)
        artifacts.write_text("reports/progress_view.md", render_progress_markdown(progress_snapshot))
        artifacts.write_text(
            "reports/run_report.md",
            render_run_report_markdown(report_payload),
        )
        contexts.append(
            Stage2RunContext(
                template_name=template_name,
                template_dir=template_dir,
                cdp_url=cdp_url,
                max_attempts=max_attempts,
                model_profile=profile,
                artifacts=artifacts,
                bundle=bundle,
                runtime=runtime,
                progress=progress,
            )
        )
    return contexts


def build_platform_daily_report_from_contexts(
    contexts: list[Stage2RunContext],
) -> dict[str, str | int | None]:
    report_payloads = []
    for context in contexts:
        report_path = context.artifacts.run_dir / "reports" / "run_report.json"
        if not report_path.exists():
            continue
        try:
            report_payloads.append(json.loads(report_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
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
        "report_date": platform_report.report_date,
        "run_count": len(report_payloads),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }
