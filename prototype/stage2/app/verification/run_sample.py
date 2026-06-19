from __future__ import annotations

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
    render_progress_markdown,
    render_run_report_markdown,
)
from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.verification.constants import ARTIFACT_ROOT, DEFAULT_CDP_URL, DEFAULT_ENV_FILES


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
        )
        report_payload["project_assets"].append(
            {
                "name": "Iteration Outputs",
                "status": "generated",
                "artifacts": [
                    {"label": "failure_clusters.json", "path": str(artifacts.run_dir / "failure_clusters.json")},
                    {"label": "retry_plan.json", "path": str(artifacts.run_dir / "retry_plan.json")},
                    {"label": "promotion_candidates.json", "path": str(artifacts.run_dir / "promotion_candidates.json")},
                ],
            }
        )
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
                runtime_data=run_data,
                progress=progress,
            )
        )
    return contexts
