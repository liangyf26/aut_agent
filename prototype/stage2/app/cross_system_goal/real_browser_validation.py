"""
Stage F 的真实浏览器验证目标:仅做菜单发现,纯只读,直接复用
v3_real_browser._discover_menu_with_playwright(与 menu_goal.real_browser_discovery
调用的是同一个底层函数),翻译成 CrossSystemAdapter 的调用序列——因为
CrossSystemAdapter 与 DiscoveryAdapter 接口不兼容,不能直接套用现成的
discover_menus_with_playwright。

安全边界:本模块只做菜单扫描(读 + Stage B 已证明安全的受限展开点击),
不做任何 feature 级交互(不 fill/click/submit 业务控件)。未经专门模板
证明特定 locator 安全之前,不要把本模块扩展成调用
feature_goal.real_browser_classifier 或 execution_goal.real_browser_runner。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

    from ..goal_loop.state_machine import GoalLoopEngine
    from .cross_system_adapter import CrossSystemAdapter

RUN_MODE_REAL_BROWSER = "real_browser"

# 复用 goal_loop.playbook 固定的失败分类词表(与 menu_goal.real_browser_discovery
# 用的是同一张映射,不重新发明)。
_STATUS_TO_FAILURE_CLASS = {
    "permission_blocked": "permission_blocked",
    "expansion_failed": "menu_expand_failed",
}


async def run_menu_validation_goal(
    page: "Page",
    adapter: "CrossSystemAdapter",
    engine: "GoalLoopEngine",
    *,
    screenshots_dir: Path,
    parent_goal_id: str | None = None,
    max_pages: int = 5,
) -> tuple[list[str], list[dict[str, Any]]]:
    """对一个系统跑一次真实的、只读的菜单发现验证,每个菜单条目注册一个
    CrossSystemAdapter 目标。

    直接手动驱动引擎(engine.activate_next() -> engine.start_attempt() ->
    adapter.record_success()/record_failure()),与
    test_cross_system_goal_integration.py 自己的手驱动模式一致,而不是
    像 DiscoveryAdapter 那样封装了专门的 record_discovery_* 方法。

    返回 (goal_ids, raw_entries) —— raw_entries 是扫描器的完整
    menu_entries 列表。
    """

    from ..v3_orchestrator import V3RunConfig
    from ..v3_real_browser import _discover_menu_with_playwright

    config = V3RunConfig(max_pages=max_pages)
    artifacts = await _discover_menu_with_playwright(page, screenshots_dir, config)
    entries: list[dict[str, Any]] = artifacts.get("menu_entries") or []

    goal_ids: list[str] = []
    for entry in entries:
        menu_id = entry.get("menu_id") or "menu"
        menu_text = entry.get("text") or menu_id

        # max_rounds=1: one real scan is a definitive verdict for this menu
        # entry, not a retryable action — without this, a retry-exit
        # failure (e.g. menu_expand_failed/evidence_incomplete) leaves the
        # goal RUNNING (unresolved), and the next entry's activate_next()
        # would raise because the previous goal was never concluded.
        goal_id = adapter.register_validation_goal(
            goal_type="menu",
            goal_name=f"Discover menu: {menu_text}",
            parent_goal_id=parent_goal_id,
            max_rounds=1,
        )
        goal_ids.append(goal_id)

        engine.activate_next()
        attempt = engine.start_attempt(goal_id)

        status = entry.get("status")
        if status in _STATUS_TO_FAILURE_CLASS:
            stop_eval = adapter.record_failure(
                attempt.attempt_id,
                explicit_class=_STATUS_TO_FAILURE_CLASS[status],
                made_progress=False,
            )
            # HUMAN_REQUIRED_CLASSES(如 permission_blocked)会把目标暂停,
            # 留在暂停状态,不在这里尝试恢复。暂停后必须停止本次扫描的
            # 剩余条目——engine 的单活跃目标约束下，下一次
            # activate_next() 会因为这个目标未解决而抛异常，与
            # execution_goal.orchestrator.execute_all() 遇到暂停就停止整批
            # 处理的先例一致（人工介入优先于继续扫描）。
            if stop_eval.target_status in {"waiting_human", "blocked_by_policy", "blocked_by_executor"}:
                break
            continue

        step = engine.add_step(attempt.attempt_id, "discovery", action="playwright_menu_scan")
        screenshot_refs = entry.get("screenshot_refs") or []
        for ref in screenshot_refs:
            path = ref.get("path") if isinstance(ref, dict) else ref
            if path:
                engine.attach_evidence(step.step_id, "screenshot", uri=str(path))

        try:
            adapter.record_success(
                attempt.attempt_id,
                signals={
                    "menu_text": bool(menu_text),
                    "path": True,
                    "screenshot": bool(screenshot_refs),
                },
            )
        except ValueError:
            # 真实扫描没有产出截图证据,menu_goal_success 谓词不满足——
            # 这是一次真实的失败,不是要绕过的异常。
            adapter.record_failure(
                attempt.attempt_id,
                explicit_class="evidence_incomplete",
                made_progress=False,
            )

    return goal_ids, entries


__all__ = ["RUN_MODE_REAL_BROWSER", "run_menu_validation_goal"]
