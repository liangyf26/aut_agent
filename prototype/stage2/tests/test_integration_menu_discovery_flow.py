"""Stage B集成测试：完整菜单发现流程演示"""

import tempfile
from pathlib import Path

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine
from prototype.stage2.app.menu_goal import (
    DiscoveryAdapter,
    MenuGoalOrchestrator,
    classify_menu_discovery_failure,
    write_menu_fixture,
)


def test_complete_menu_discovery_flow():
    """演示完整的菜单发现流程：注册→尝试→成功/失败→导出"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 初始化orchestrator
        orch = MenuGoalOrchestrator(output_dir=tmpdir, run_id="demo_run")
        adapter = orch.adapter

        # 2. 创建根目标
        root_id = orch.create_root_goal(description="Discover L1 menus")
        print(f"[OK] Created root goal: {root_id}")

        # 3. 注册菜单目标
        menu1_id = adapter.register_menu_goal(
            menu_id="menu_sys",
            menu_path=["系统管理"],
            parent_goal_id=root_id,
        )
        menu2_id = adapter.register_menu_goal(
            menu_id="menu_user",
            menu_path=["系统管理", "用户管理"],
            parent_goal_id=menu1_id,
        )
        print(f"[OK] Registered 2 menu goals")

        # 4. 模拟第一个菜单发现 - 成功
        attempt1 = adapter.record_discovery_attempt(
            goal_id=menu1_id, route_hint="/system"
        )
        step1 = adapter.record_navigation_step(
            attempt_id=attempt1, action="click_menu", target=".menu-系统管理"
        )
        screenshot1 = adapter.attach_screenshot_evidence(
            step_id=step1, screenshot_path="/tmp/sys_menu.png"
        )
        metadata1 = adapter.attach_menu_metadata_evidence(
            step_id=step1, menu_text="系统管理"
        )
        adapter.record_discovery_success(
            attempt_id=attempt1, evidence_refs=[screenshot1, metadata1]
        )
        print(f"[OK] Menu 1 discovered successfully")

        # 5. 模拟第二个菜单发现 - 首次失败，重试成功
        attempt2a = adapter.record_discovery_attempt(goal_id=menu2_id)
        step2a = adapter.record_navigation_step(
            attempt_id=attempt2a, action="expand_submenu"
        )
        screenshot2a = adapter.attach_screenshot_evidence(
            step_id=step2a, screenshot_path="/tmp/expand_fail.png"
        )

        # 使用分类器
        failure_class, confidence = classify_menu_discovery_failure(
            error_code="EXPAND_TIMEOUT", operation="expand_submenu"
        )
        print(f"[OK] Classified failure: {failure_class} ({confidence} confidence)")

        adapter.record_discovery_failure(
            attempt_id=attempt2a,
            failure_class=failure_class,
            confidence=confidence,
            evidence_refs=[screenshot2a],
            note="Submenu expand timeout",
        )

        # 重试
        attempt2b = adapter.record_discovery_attempt(goal_id=menu2_id)
        step2b = adapter.record_navigation_step(
            attempt_id=attempt2b, action="click_menu", target=".submenu-用户管理"
        )
        screenshot2b = adapter.attach_screenshot_evidence(
            step_id=step2b, screenshot_path="/tmp/user_menu.png"
        )
        metadata2b = adapter.attach_menu_metadata_evidence(
            step_id=step2b, menu_text="用户管理"
        )
        adapter.record_discovery_success(
            attempt_id=attempt2b, evidence_refs=[screenshot2b, metadata2b]
        )
        print(f"[OK] Menu 2 discovered after retry")

        # 6. 导出固定装置
        fixture_path = orch.export_fixture()
        print(f"[OK] Exported fixture to: {fixture_path}")

        # 7. 获取摘要
        summary = orch.get_summary()
        print("\n=== Session Summary ===")
        print(f"Run ID: {summary['run_id']}")
        print(f"Total goals: {summary['total_goals']}")
        print(f"Succeeded: {summary['succeeded']}")
        print(f"Failed: {summary['failed']}")
        print(f"Pending: {summary['pending']}")

        # 8. 验证
        assert summary["total_goals"] == 3  # root + 2 menus
        assert summary["succeeded"] == 2  # 2 menu goals succeeded
        assert fixture_path.exists()

        print("\n[SUCCESS] Complete flow test passed!")


if __name__ == "__main__":
    test_complete_menu_discovery_flow()
