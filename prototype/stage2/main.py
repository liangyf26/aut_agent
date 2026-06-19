from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.reporting import (
    build_platform_daily_report,
    render_platform_daily_report_markdown,
)
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData

TEMPLATE_ROOT = ROOT_DIR / "prototype" / "stage2" / "templates"
HUMAN_LOOP_ROOT = ROOT_DIR / "artifacts" / "stage2" / "human_loop"
DEFAULT_CDP_URL = "http://localhost:9222"

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


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


def load_run_report_payload(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    return _read_json_file(root / "reports" / "run_report.json")


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


async def run_local_template_validation(template_name: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from prototype.stage2.app.data_factory.generator import TemplateDataFactory
    from prototype.stage2.app.runtime.artifacts import ArtifactWriter
    from prototype.stage2.app.verification import execute_generic_template

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
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            executions = await execute_generic_template(
                page=page,
                artifacts=artifacts,
                runtime=runtime,
                template=bundle.template,
            )
            success = all(item.status == "completed" for item in executions)
            payload = {
                "template": template_name,
                "run_dir": str(artifacts.run_dir),
                "success": success,
                "step_count": len(executions),
                "steps": [item.to_attempt_action() for item in executions],
                "final_url": page.url,
                "final_title": await page.title(),
            }
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
    from prototype.stage2.app.verification import execute_generic_template

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
            executions = await execute_generic_template(
                page=page,
                artifacts=artifacts,
                runtime=runtime,
                template=bundle.template,
            )
            success = all(item.status == "completed" for item in executions)
            payload = {
                "template": template_name,
                "run_dir": str(artifacts.run_dir),
                "success": success,
                "step_count": len(executions),
                "steps": [item.to_attempt_action() for item in executions],
                "final_url": page.url,
                "final_title": await page.title(),
                "mode": "connected_cdp",
            }
            artifacts.write_json("validation_result.json", payload)
            return payload
        finally:
            await browser.close()


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
        metadata={"bootstrap_mode": "placeholder_seed"},
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
    MinimalCandidateTemplateDraftGenerator().write_draft(
        config=config,
        events=events,
        output_path=str(paths.draft_path),
    )
    return {
        "template": template_name,
        "session_id": session_id,
        "session_dir": str(paths.session_dir),
        "metadata_path": str(paths.metadata_path),
        "events_path": str(paths.events_path),
        "draft_path": str(paths.draft_path),
        "event_count": len(events),
    }


async def run_live_discovery(template_name: str, *, cdp_url: str) -> dict[str, str | int]:
    from prototype.stage2.app.discovery import DiscoveryArtifactWriter, run_live_discovery_session

    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    output_dir = ROOT_DIR / "artifacts" / "stage2" / f"live_discovery_{template_name}"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = await run_live_discovery_session(
        cdp_url=cdp_url,
        template_name=template_name,
        template=bundle.template,
        baseline=bundle.baseline,
        output_dir=output_dir,
    )

    paths = DiscoveryArtifactWriter(output_dir).write(result)
    return {
        "template": template_name,
        "strategy": result.strategy,
        "output_dir": str(output_dir),
        "page_entry_count": len(result.page_entries),
        "feature_point_count": len(result.feature_points),
        "screenshot_record_count": len(result.screenshot_records),
        "page_entries_path": str(paths["page_entries"]),
        "feature_points_path": str(paths["feature_points"]),
        "screenshot_records_path": str(paths["screenshot_records"]),
        "discovery_result_path": str(paths["discovery_summary"]),
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
        metadata={"capture_mode": "playwright_cdp"},
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
        max_rounds=max_rounds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 prototype entrypoint.")
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
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum attempts used by the stage-2 sample pipeline.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help="Maximum orchestration rounds used by the stage-2 sample pipeline.",
    )
    parser.add_argument(
        "--platform-daily-report",
        action="store_true",
        help="Aggregate the latest run reports into a platform daily report bundle.",
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
                asyncio.run(run_live_discovery(args.template, cdp_url=args.cdp_url)),
                ensure_ascii=False,
                indent=2,
            )
        )
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

    print(json.dumps(list_templates(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
