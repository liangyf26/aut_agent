from __future__ import annotations

"""
Prototype question:
Can one minimal progress module express platform-level stages, rounds, steps,
targets, status, stats, and next action without depending on the later stage-2
framework modules?

Run:
  python prototype/stage2/app/progress/example.py
"""

import argparse
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from progress import ProgressManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the stage-2 progress prototype.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(tempfile.gettempdir()) / "aut_agent_stage2_progress_prototype"),
        help="Directory used for prototype progress artifacts.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    manager = ProgressManager(run_id="stage2-demo-run-001", output_dir=output_dir)

    manager.start_phase(
        "preflight",
        phase_label="预检",
        step_key="probe_models",
        step_label="模型能力预检",
        message="初始化 run 并检查模型配置",
        next_action="进入登录接管",
        stats={"models_planned": 2},
    )
    manager.start_step(
        "preflight",
        phase_label="预检",
        step_key="probe_models",
        step_label="模型能力预检",
        message="开始检查 AI-tester 与 Qwen",
    )
    manager.complete_step(
        "preflight",
        phase_label="预检",
        step_key="probe_models",
        step_label="模型能力预检",
        message="模型预检通过",
        stats={"models_checked": 2, "models_ready": 2},
        next_action="切换到登录接管阶段",
    )
    manager.complete_phase(
        "preflight",
        phase_label="预检",
        message="预检阶段完成",
        next_action="登录接管",
    )

    manager.start_phase(
        "login_handoff",
        phase_label="登录接管",
        step_key="reuse_logged_in_session",
        step_label="复用已登录会话",
        message="尝试连接现有浏览器会话",
        next_action="若失败则等待人工扫码",
    )
    manager.start_step(
        "login_handoff",
        phase_label="登录接管",
        step_key="reuse_logged_in_session",
        step_label="复用已登录会话",
        message="检测到需要人工扫码",
    )
    manager.wait_for_human(
        "login_handoff",
        phase_label="登录接管",
        step_key="reuse_logged_in_session",
        step_label="复用已登录会话",
        reason="等待人工完成扫码登录",
        next_action="登录成功后继续发现阶段",
    )
    manager.heartbeat(
        phase="login_handoff",
        phase_label="登录接管",
        message="人工介入中，最近心跳已刷新",
        stats={"human_takeovers": 1},
    )
    manager.complete_step(
        "login_handoff",
        phase_label="登录接管",
        step_key="reuse_logged_in_session",
        step_label="复用已登录会话",
        message="已接管可复用会话",
        next_action="开始发现页面入口",
    )
    manager.complete_phase(
        "login_handoff",
        phase_label="登录接管",
        message="登录接管完成",
        next_action="发现阶段",
    )

    manager.start_phase(
        "discovery",
        phase_label="发现",
        round_kind="discovery",
        round_index=1,
        round_label="发现第 1 轮",
        step_key="scan_page_entries",
        step_label="自动遍历页面入口",
        message="从系统首页开始构建入口树",
        next_action="识别功能点",
    )
    manager.start_step(
        "discovery",
        phase_label="发现",
        round_kind="discovery",
        round_index=1,
        round_label="发现第 1 轮",
        step_key="scan_page_entries",
        step_label="自动遍历页面入口",
        target_kind="page_entry",
        target_id="record-online",
        target_label="线上备案申请",
        message="采集页面入口与首屏截图",
    )
    manager.complete_step(
        "discovery",
        phase_label="发现",
        round_kind="discovery",
        round_index=1,
        round_label="发现第 1 轮",
        step_key="scan_page_entries",
        step_label="自动遍历页面入口",
        target_kind="page_entry",
        target_id="record-online",
        target_label="线上备案申请",
        message="入口扫描完成",
        stats={"page_entries_found": 6, "feature_points_found": 3},
        next_action="进入验证阶段",
    )
    manager.complete_phase(
        "discovery",
        phase_label="发现",
        message="发现阶段完成",
        next_action="验证阶段第 1 轮",
    )

    manager.start_phase(
        "verification",
        phase_label="验证",
        round_kind="verification",
        round_index=1,
        round_label="验证第 1 轮",
        step_key="execute_template",
        step_label="执行模板回放",
        message="装载模板与本轮生成数据",
        next_action="判定执行结果",
    )
    manager.start_step(
        "verification",
        phase_label="验证",
        round_kind="verification",
        round_index=1,
        round_label="验证第 1 轮",
        step_key="execute_template",
        step_label="执行模板回放",
        target_kind="feature_point",
        target_id="seedling_apply",
        target_label="育苗备案申请",
        message="执行新增类模板",
    )
    manager.fail_step(
        "verification",
        phase_label="验证",
        round_kind="verification",
        round_index=1,
        round_label="验证第 1 轮",
        step_key="execute_template",
        step_label="执行模板回放",
        target_kind="feature_point",
        target_id="seedling_apply",
        target_label="育苗备案申请",
        message="提交被业务校验拦截",
        stats={"verifications_completed": 1, "verification_failures": 1},
        next_action="进入失败归因",
    )
    manager.complete_phase(
        "verification",
        phase_label="验证",
        message="验证阶段已结束，等待归因结论",
        next_action="失败归因第 1 轮",
    )

    manager.start_phase(
        "failure_analysis",
        phase_label="失败归因",
        round_kind="analysis",
        round_index=1,
        round_label="归因第 1 轮",
        step_key="cluster_failures",
        step_label="聚合失败簇",
        message="开始归并验证失败项",
        next_action="输出修正建议",
    )
    manager.start_step(
        "failure_analysis",
        phase_label="失败归因",
        round_kind="analysis",
        round_index=1,
        round_label="归因第 1 轮",
        step_key="cluster_failures",
        step_label="聚合失败簇",
        target_kind="failure_cluster",
        target_id="business-validation",
        target_label="业务校验拦截",
        message="归类为业务校验型失败",
    )
    manager.complete_step(
        "failure_analysis",
        phase_label="失败归因",
        round_kind="analysis",
        round_index=1,
        round_label="归因第 1 轮",
        step_key="cluster_failures",
        step_label="聚合失败簇",
        target_kind="failure_cluster",
        target_id="business-validation",
        target_label="业务校验拦截",
        message="已形成下一轮修正建议",
        stats={"failure_clusters": 1},
        next_action="沉淀到项目级模板",
    )
    manager.complete_phase(
        "failure_analysis",
        phase_label="失败归因",
        message="失败归因完成",
        next_action="输出阶段总结",
    )

    print(manager.render_console_summary(recent_limit=8))
    print("")
    print(f"Artifacts written to: {output_dir}")
    print(f"  - {manager.writer.events_path}")
    print(f"  - {manager.writer.current_status_path}")
    print(f"  - {manager.writer.phase_summary_path}")


if __name__ == "__main__":
    main()
