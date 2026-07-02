from __future__ import annotations

"""
Runnable Stage A demo: walk one goal loop end-to-end (no browser / no SUT).

It exercises the whole kernel on fixture signals:
  menu goal  -> attempt 1 fails (menu_expand_failed) -> fixed playbook -> retry
             -> attempt 2 succeeds (with step-level evidence)
  page goal  (derived child) -> attempt 1 fails (page_blank) -> retry -> succeeds
  feature goal (derived child) -> succeeds
then prints a readable trace and writes the 7 goal-loop artifacts.

Run:
  python -m prototype.stage2.app.goal_loop.demo
  python -m prototype.stage2.app.goal_loop.demo --output-dir some/dir
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine
from prototype.stage2.app.goal_loop.writer import GoalLoopWriter


def _line(msg: str) -> None:
    print(msg)


def _menu_signals() -> dict:
    return {"menu_text": "订单管理", "path": "首页/订单管理", "screenshot": "menu.png"}


def _page_signals() -> dict:
    return {"http_ok": True, "has_main_content": True, "visible_text_len": 240, "dom_nodes": 52}


def _feature_signals() -> dict:
    return {
        "feature_identified": True,
        "case_generated": True,
        "basic_path_executed": True,
        "has_feedback": True,
    }


def run_demo(output_dir: Path) -> None:
    engine = GoalLoopEngine("stage-a-demo-run-001")

    # ---- menu goal --------------------------------------------------------
    _line("== 菜单目标 ==")
    menu = engine.register_goal("menu", "发现一级菜单：订单管理")
    engine.activate_next()
    _line(f"注册并激活: {menu.goal_id} status={engine.goals[menu.goal_id].status}")

    a1 = engine.start_attempt()
    cls, action = engine.record_failure(a1.attempt_id, explicit_class="menu_expand_failed")
    _line(f"尝试1 失败 -> 分类={cls.failure_reason}(置信度={cls.reason_confidence}) "
          f"-> 套路={action.playbook_id} 出口={action.exit}")
    _line(f"        套路动作: {action.action_steps}")

    a2 = engine.start_attempt()
    step = engine.add_step(a2.attempt_id, "action", action="normalize_menu_shell+click")
    ev = engine.attach_evidence(step.step_id, "screenshot", uri="menu_expanded.png")
    _line(f"尝试2 证据链: {menu.goal_id} -> {a2.attempt_id} -> {step.step_id} -> {ev.evidence_id}")
    result, exp = engine.record_success(a2.attempt_id, signals=_menu_signals())
    _line(f"尝试2 成功 -> 谓词 [{result.name}] 表达式: {result.expression} = {result.value}")
    _line(f"        经验沉淀: {exp.kind} / {exp.promotion_level} / review={exp.review_status}")

    # ---- derive page goal (frontier grows from the succeeded menu) --------
    _line("\n== 页面目标（由菜单目标派生）==")
    page = engine.derive_child_goal(menu.goal_id, "page", "进入订单列表页", origin="menu::orders")
    _line(f"派生子目标: {page.goal_id} parent={page.parent_goal_id} 进入 frontier={engine.frontier}")
    engine.activate_next()

    p1 = engine.start_attempt()
    cls, action = engine.record_failure(
        p1.attempt_id, signals="页面加载后主内容区 blank page 白屏", made_progress=True
    )
    _line(f"尝试1 失败 -> 分类={cls.failure_reason}(由关键词推断, 置信度={cls.reason_confidence}) "
          f"-> 套路={action.playbook_id}")

    p2 = engine.start_attempt()
    ps = engine.add_step(p2.attempt_id, "navigate", action="reenter+wait_stable")
    engine.attach_evidence(ps.step_id, "screenshot", uri="page_ok.png")
    engine.record_success(p2.attempt_id, signals=_page_signals())
    _line(f"尝试2 成功 -> {page.goal_id} status={engine.goals[page.goal_id].status}")

    # ---- derive feature goal ---------------------------------------------
    _line("\n== 功能点目标（由页面目标派生）==")
    feat = engine.derive_child_goal(page.goal_id, "feature", "验证查询功能", origin="page::query")
    engine.activate_next()
    f1 = engine.start_attempt()
    fs = engine.add_step(f1.attempt_id, "action", action="fill_query+submit")
    engine.attach_evidence(fs.step_id, "network", uri="query_response.json")
    engine.record_success(f1.attempt_id, signals=_feature_signals())
    _line(f"尝试1 成功 -> {feat.goal_id} status={engine.goals[feat.goal_id].status}")

    # ---- write artifacts + summary ---------------------------------------
    stop_eval = engine.evaluate_stop(feat.goal_id)
    writer = GoalLoopWriter(output_dir)
    paths = writer.write_all(engine, stop_evaluation=stop_eval, template_name="demo_orders")

    _line("\n== 目标树摘要 ==")
    for gid in engine.goals:
        s = engine.build_summary(gid)
        _line(f"  {s.goal_id} [{s.goal_type}] {s.goal_name} -> {s.status} "
              f"(尝试{s.attempt_count}次, 障碍={s.primary_failure_class or '无'})")

    escalations = engine.evaluate_escalations()
    _line("\n== 系统性缺陷升级计数器 (§7.4) ==")
    if escalations:
        for row in escalations:
            _line(f"  {row.failure_class}: 出现{row.occurrences}次, 套路成功率={row.playbook_success_rate}, "
                  f"触发升级={row.triggered}")
    else:
        _line("  （无失败分类累计）")

    _line("\n== 产物文件 ==")
    for name, path in paths.items():
        _line(f"  {name}: {path}")

    # show the run-center current-status projection (reuse of iteration/progress)
    current = json.loads(paths["goal_current_status"].read_text(encoding="utf-8"))
    _line("\n== 运行中心状态视图 (goal_current_status.json) ==")
    _line(f"  overall_status={current['overall_status']} "
          f"goal_status={current['goal_status']} next_action={current['next_action']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Stage A goal-loop demo.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(tempfile.gettempdir()) / "aut_agent_goal_loop_demo"),
        help="Directory for the demo's goal-loop artifacts.",
    )
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    run_demo(output_dir)


if __name__ == "__main__":
    main()
