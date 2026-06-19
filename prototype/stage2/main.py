from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prototype.stage2.app.human_loop import (
    HumanLoopRecorder,
    MinimalCandidateTemplateDraftGenerator,
    RecordingSessionConfig,
)
from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.verification.run_sample import build_run_contexts


ROOT_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT_DIR / "prototype" / "stage2" / "templates"
HUMAN_LOOP_ROOT = ROOT_DIR / "artifacts" / "stage2" / "human_loop"

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


def bootstrap_human_recording(
    template_name: str,
    *,
    session_id: str,
    operator_id: str | None = None,
    start_url: str | None = None,
    task_description: str | None = None,
) -> dict[str, str | int]:
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

    print(json.dumps(list_templates(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
