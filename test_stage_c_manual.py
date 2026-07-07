"""
阶段C手动验证脚本

运行此脚本验证页面目标闭环的核心功能。
"""
import tempfile
import json
from pathlib import Path
from prototype.stage2.app.page_goal import (
    PageGoalOrchestrator,
)

def test_manual_page_discovery():
    """手动验证页面发现流程"""

    print("\n" + "="*70)
    print("阶段C手动验证 - Page Goal Loop")
    print("="*70 + "\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()

        # 1. 创建orchestrator
        print("步骤 1: 创建PageGoalOrchestrator")
        orch = PageGoalOrchestrator(
            output_dir=str(output_dir),
            run_id="manual_test"
        )
        print("  ✓ Orchestrator创建成功")

        # 2. 创建root goal
        print("\n步骤 2: 创建root goal")
        root_id = orch.create_root_goal(description="Manual test root")
        print(f"  ✓ Root goal ID: {root_id}")

        # 3. 创建测试用的menu_entries.json
        print("\n步骤 3: 创建测试menu fixture")
        menu_fixture = output_dir / "menu_entries.json"
        # loader.py expects a list, not a dict with menu_entries key
        test_menus = [
            {
                "menu_id": "menu_001",
                "menu_path": ["系统管理"],
                "menu_text": "系统管理",
                "route_hint": "/system",
                "status": "discovered",
                "parent_id": None,
                "metadata": {},
            },
            {
                "menu_id": "menu_002",
                "menu_path": ["系统管理", "用户管理"],
                "menu_text": "用户管理",
                "route_hint": "/system/users",
                "status": "discovered",
                "parent_id": "menu_001",
                "metadata": {},
            }
        ]
        with open(menu_fixture, "w", encoding="utf-8") as f:
            json.dump(test_menus, f, ensure_ascii=False, indent=2)
        print(f"  ✓ Menu fixture创建: {menu_fixture}")
        print(f"  ✓ 包含 {len(test_menus)} 个菜单项")

        # 4. 加载菜单
        print("\n步骤 4: 加载菜单并创建页面目标")
        goal_ids = orch.load_menu_entries(str(menu_fixture))
        print(f"  ✓ 成功加载 {len(goal_ids)} 个页面目标")
        for i, goal_id in enumerate(goal_ids, 1):
            goal = orch.engine.goals[goal_id]
            context = orch.adapter._page_context.get(goal_id, {})
            print(f"    {i}. {goal_id}: {context.get('menu_path', [])} ({goal.status})")

        # 5. 模拟第一个页面发现（成功）
        print("\n步骤 5: 模拟页面发现 - 成功案例")
        goal_id_1 = goal_ids[0]
        goal_1 = orch.engine.goals[goal_id_1]
        goal_1.status = "running"  # 模拟激活

        attempt_id_1 = orch.adapter.record_page_attempt(goal_id=goal_id_1)
        print(f"  ✓ 页面尝试创建: {attempt_id_1}")

        # 导航步骤
        step1 = orch.adapter.record_navigation_step(
            attempt_id_1,
            action="navigate_to_page",
            target="/system",
            observed=False
        )
        print(f"  ✓ 导航步骤: {step1}")

        step2 = orch.adapter.record_navigation_step(
            attempt_id_1,
            action="capture_state",
            observed=True
        )
        print(f"  ✓ 状态捕获: {step2}")

        # 附加证据
        screenshot_path = output_dir / "screenshots" / "page_001.png"
        screenshot_path.parent.mkdir(exist_ok=True)
        screenshot_path.write_text("fake screenshot content")

        ev1 = orch.adapter.attach_screenshot_evidence(
            step2,
            screenshot_path=str(screenshot_path)
        )
        print(f"  ✓ 截图证据: {ev1}")

        ev2 = orch.adapter.attach_page_metadata_evidence(
            step2,
            page_title="系统管理",
            page_url="https://example.com/system",
            http_status=200,
            dom_snapshot={"visible_text_len": 500, "dom_nodes": 100}
        )
        print(f"  ✓ 页面元数据: {ev2}")

        # 记录成功
        orch.adapter.record_page_success(
            attempt_id_1,
            page_url="https://example.com/system",
            page_title="系统管理",
            visible_text_len=500,
            dom_nodes=100,
            blank_screenshot_ratio=0.1,
            evidence_refs=[ev1, ev2]
        )
        print(f"  ✓ 页面发现成功！Status: {goal_1.status}")

        # 6. 模拟第二个页面发现（blank页面）
        print("\n步骤 6: 模拟页面发现 - Blank页面案例")
        goal_id_2 = goal_ids[1]
        goal_2 = orch.engine.goals[goal_id_2]
        goal_2.status = "running"

        attempt_id_2 = orch.adapter.record_page_attempt(goal_id=goal_id_2)
        print(f"  ✓ 页面尝试创建: {attempt_id_2}")

        step3 = orch.adapter.record_navigation_step(
            attempt_id_2,
            action="navigate_to_page",
            target="/system/users",
            observed=False
        )
        step4 = orch.adapter.record_navigation_step(
            attempt_id_2,
            action="capture_state",
            observed=True
        )

        screenshot_path_2 = output_dir / "screenshots" / "page_002.png"
        screenshot_path_2.write_text("blank screenshot")

        ev3 = orch.adapter.attach_screenshot_evidence(step4, str(screenshot_path_2))
        ev4 = orch.adapter.attach_page_metadata_evidence(
            step4,
            page_title="用户管理",
            page_url="https://example.com/system/users",
            http_status=200,
            dom_snapshot={"visible_text_len": 5, "dom_nodes": 2}
        )

        # 分类blank失败
        from prototype.stage2.app.page_goal.page_classifier import classify_page_discovery_failure
        failure_class, confidence = classify_page_discovery_failure(
            page_signals={
                "visible_text_len": 5,
                "dom_nodes": 2,
                "blank_screenshot_ratio": 0.99,
            }
        )
        print(f"  ✓ 失败分类: {failure_class} (confidence: {confidence})")

        orch.adapter.record_page_failure(
            attempt_id_2,
            failure_class=failure_class,
            confidence=confidence,
            evidence_refs=[ev3, ev4],
            note="Blank page detected"
        )

        # 设置为failed状态
        goal_2.status = "failed_max_rounds"
        print(f"  ✓ 页面失败记录！Status: {goal_2.status}, Failure: {failure_class}")

        # 7. 导出所有产物
        print("\n步骤 7: 导出产物")
        fixture_path = orch.export_fixture()
        print(f"  ✓ page_entries.json: {fixture_path}")

        log_path = orch.export_exploration_log()
        print(f"  ✓ exploration_log.jsonl: {log_path}")

        screenshots_index = orch.export_screenshots_index()
        print(f"  ✓ screenshots_index.json: {screenshots_index}")

        summary_path = orch.export_goal_summary()
        print(f"  ✓ goal_summary.json: {summary_path}")

        # 8. 验证fixture内容
        print("\n步骤 8: 验证fixture内容")
        with open(fixture_path, "r", encoding="utf-8") as f:
            fixture = json.load(f)

        assert "page_entries" in fixture, "Missing page_entries"
        assert len(fixture["page_entries"]) == 2, f"Expected 2 entries, got {len(fixture['page_entries'])}"

        # 验证成功的页面
        entry1 = fixture["page_entries"][0]
        assert entry1["page_id"] == "menu_001"
        assert entry1["menu_path"] == ["系统管理"]
        assert entry1["page_title"] == "系统管理"
        assert entry1["status"] == "reachable"
        assert entry1["http_status"] == 200
        assert entry1["is_blank"] is False
        print(f"  ✓ Entry 1: {entry1['menu_path']} - {entry1['status']}")

        # 验证失败的页面
        entry2 = fixture["page_entries"][1]
        assert entry2["page_id"] == "menu_002"
        assert entry2["menu_path"] == ["系统管理", "用户管理"]
        assert entry2["status"] == "failed"
        assert entry2["metadata"]["failure_class"] == "page_blank"
        print(f"  ✓ Entry 2: {entry2['menu_path']} - {entry2['status']} ({entry2['metadata']['failure_class']})")

        # 9. 验证summary
        print("\n步骤 9: 验证运行级统计")
        summary = orch.get_summary()
        print(f"  ✓ Total goals: {summary['total_goals']}")
        print(f"  ✓ Succeeded: {summary['succeeded']}")
        print(f"  ✓ Failed: {summary['failed']}")
        print(f"  ✓ Reachable pages: {summary['reachable_count']}")
        print(f"  ✓ Blank pages: {summary['blank_count']}")

        assert summary['total_goals'] == 3  # root + 2 page goals
        assert summary['succeeded'] == 1
        assert summary['failed'] == 1
        assert summary['reachable_count'] == 1
        assert summary['blank_count'] == 1

        # 10. 显示生成的文件内容（前几行）
        print("\n步骤 10: 查看生成的文件示例")
        print("\n--- page_entries.json (前20行) ---")
        with open(fixture_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines[:20]:
                print(line.rstrip())

        print("\n--- exploration_log.jsonl (前3行) ---")
        with open(log_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 3:
                    break
                log_entry = json.loads(line)
                print(f"  {log_entry.get('action', 'N/A')}: goal={log_entry.get('goal_id', 'N/A')}, status={log_entry.get('status', 'N/A')}")

        print("\n" + "="*70)
        print("✅ 阶段C手动验证全部通过！")
        print("="*70)
        print("\n验证项目：")
        print("  ✓ Orchestrator初始化")
        print("  ✓ Root goal创建")
        print("  ✓ Menu加载和页面目标注册")
        print("  ✓ 页面尝试和步骤记录")
        print("  ✓ Evidence附加（screenshot + metadata）")
        print("  ✓ 成功页面记录")
        print("  ✓ 失败页面分类（page_blank）")
        print("  ✓ Fixture导出（page_entries.json）")
        print("  ✓ Exploration log导出")
        print("  ✓ Screenshots index导出")
        print("  ✓ Summary统计")
        print("  ✓ CJK文本编码保留")
        print("\n下一步：运行完整测试套件")
        print("  python -m pytest prototype/stage2/tests/test_page_goal_integration.py -v")

if __name__ == "__main__":
    try:
        test_manual_page_discovery()
    except Exception as e:
        print(f"\n❌ 验证失败: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
