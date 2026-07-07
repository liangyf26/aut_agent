# 阶段C测试验证指南

## 🚀 快速验证（1分钟）

### 1. 运行集成测试

```bash
# 运行所有6个阶段C集成测试
python -m pytest prototype/stage2/tests/test_page_goal_integration.py -v

# 预期结果：6 passed
```

**测试覆盖**：
- ✅ 完整会话流程
- ✅ blank页面检测和重试逻辑
- ✅ permission_blocked处理
- ✅ CJK文本编码往返
- ✅ URL规范化和去重
- ✅ HTTP错误分类

### 2. 验证零回归

```bash
# 运行Stage A/B的96个测试确保零回归
python -m pytest prototype/stage2/tests/test_goal_loop_*.py \
                 prototype/stage2/tests/test_menu_goal_*.py \
                 prototype/stage2/tests/test_integration_menu_discovery_flow.py -v

# 预期结果：96 passed
```

---

## 🔍 详细验证（5分钟）

### 3. 查看测试详情

```bash
# 单独运行每个测试并查看详细输出
python -m pytest prototype/stage2/tests/test_page_goal_integration.py::test_page_discovery_session_basic -vv

# 查看测试代码理解验证内容
cat prototype/stage2/tests/test_page_goal_integration.py | head -100
```

### 4. 检查生成的fixture示例

集成测试会在临时目录生成fixture文件，查看测试代码中的断言：

```python
# test_page_discovery_session_basic验证的内容
assert "page_entries" in fixture
assert len(fixture["page_entries"]) >= 1

# 每个entry包含：
entry = fixture["page_entries"][0]
assert "page_id" in entry
assert "menu_path" in entry
assert "page_url" in entry
assert "page_title" in entry
assert "status" in entry  # reachable|failed|blocked|deduplicated|pending
assert "screenshot_path" in entry
assert "parent_menu_id" in entry
assert "http_status" in entry
assert "has_main_content" in entry
assert "is_blank" in entry
assert "metadata" in entry
```

### 5. CJK编码验证

```bash
# 运行CJK测试并查看详细输出
python -m pytest prototype/stage2/tests/test_page_goal_integration.py::test_cjk_page_titles_preserved -vv -s

# 验证内容：
# - CJK菜单路径：["溯源管理", "用户管理"]
# - CJK页面标题："溯源管理 | 用户管理"
# - UTF-8往返测试通过（export → re-import → 验证）
```

---

## 🛠️ 手动功能验证（10分钟）

### 6. 创建测试脚本

创建 `test_stage_c_manual.py`：

```python
"""
手动验证阶段C核心功能
"""
import tempfile
import json
from pathlib import Path
from prototype.stage2.app.page_goal import (
    PageGoalOrchestrator,
    PageAdapter,
)
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine

def test_manual_page_discovery():
    """手动验证页面发现流程"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        
        # 1. 创建orchestrator
        orch = PageGoalOrchestrator(
            output_dir=str(output_dir),
            run_id="manual_test"
        )
        print("✓ Orchestrator创建成功")
        
        # 2. 创建root goal
        root_id = orch.create_root_goal(description="Manual test root")
        print(f"✓ Root goal创建: {root_id}")
        
        # 3. 创建测试用的menu_entries.json
        menu_fixture = output_dir / "menu_entries.json"
        test_menus = {
            "run_id": "test",
            "timestamp": "2024-01-01T00:00:00Z",
            "menu_entries": [
                {
                    "menu_id": "menu_001",
                    "menu_path": ["系统管理"],
                    "menu_text": "系统管理",
                    "route_hint": "/system",
                    "status": "discovered",
                    "parent_id": None,
                    "metadata": {},
                }
            ]
        }
        with open(menu_fixture, "w", encoding="utf-8") as f:
            json.dump(test_menus, f, ensure_ascii=False, indent=2)
        print(f"✓ 测试menu fixture创建: {menu_fixture}")
        
        # 4. 加载菜单
        goal_ids = orch.load_menu_entries(str(menu_fixture))
        print(f"✓ 加载了 {len(goal_ids)} 个页面目标")
        assert len(goal_ids) == 1
        
        # 5. 模拟页面发现
        goal_id = goal_ids[0]
        goal = orch.engine.goals[goal_id]
        goal.status = "running"  # 模拟激活
        
        attempt_id = orch.adapter.record_page_attempt(goal_id)
        print(f"✓ 页面尝试创建: {attempt_id}")
        
        # 导航步骤
        step1 = orch.adapter.record_navigation_step(
            attempt_id,
            action="navigate_to_page",
            target="/system",
            observed=False
        )
        print(f"✓ 导航步骤记录: {step1}")
        
        step2 = orch.adapter.record_navigation_step(
            attempt_id,
            action="capture_state",
            observed=True
        )
        print(f"✓ 状态捕获步骤: {step2}")
        
        # 附加证据
        screenshot_path = output_dir / "screenshots" / "page_001.png"
        screenshot_path.parent.mkdir(exist_ok=True)
        screenshot_path.write_text("fake screenshot")
        
        ev1 = orch.adapter.attach_screenshot_evidence(
            step2,
            screenshot_path=str(screenshot_path)
        )
        print(f"✓ 截图证据附加: {ev1}")
        
        ev2 = orch.adapter.attach_page_metadata_evidence(
            step2,
            page_title="系统管理",
            page_url="https://example.com/system",
            http_status=200,
            dom_snapshot={"visible_text_len": 500, "dom_nodes": 100}
        )
        print(f"✓ 页面元数据附加: {ev2}")
        
        # 记录成功
        orch.adapter.record_page_success(
            attempt_id,
            page_url="https://example.com/system",
            page_title="系统管理",
            visible_text_len=500,
            dom_nodes=100,
            blank_screenshot_ratio=0.1,
            evidence_refs=[ev1, ev2]
        )
        print(f"✓ 页面发现成功记录")
        
        # 6. 导出fixture
        fixture_path = orch.export_fixture()
        print(f"✓ Fixture导出: {fixture_path}")
        
        # 验证fixture内容
        with open(fixture_path, "r", encoding="utf-8") as f:
            fixture = json.load(f)
        
        assert "page_entries" in fixture
        assert len(fixture["page_entries"]) == 1
        
        entry = fixture["page_entries"][0]
        assert entry["page_id"] == "menu_001"
        assert entry["menu_path"] == ["系统管理"]
        assert entry["page_title"] == "系统管理"
        assert entry["page_url"] == "https://example.com/system"
        assert entry["status"] == "reachable"
        assert entry["http_status"] == 200
        assert entry["has_main_content"] is True
        assert entry["is_blank"] is False
        
        print("✓ Fixture内容验证通过")
        
        # 7. 导出其他文件
        log_path = orch.export_exploration_log()
        print(f"✓ Exploration log导出: {log_path}")
        
        screenshots_index = orch.export_screenshots_index()
        print(f"✓ Screenshots index导出: {screenshots_index}")
        
        summary = orch.get_summary()
        print(f"✓ Summary获取成功")
        print(f"  - Total goals: {summary['total_goals']}")
        print(f"  - Reachable: {summary['reachable_count']}")
        print(f"  - Succeeded: {summary['succeeded']}")
        
        print("\n" + "="*60)
        print("✅ 阶段C手动验证全部通过！")
        print("="*60)

if __name__ == "__main__":
    test_manual_page_discovery()
```

运行验证：

```bash
python test_stage_c_manual.py
```

预期输出：
```
✓ Orchestrator创建成功
✓ Root goal创建: goal-000001
✓ 测试menu fixture创建: ...
✓ 加载了 1 个页面目标
✓ 页面尝试创建: attempt-000001
✓ 导航步骤记录: step-000001
✓ 状态捕获步骤: step-000002
✓ 截图证据附加: evidence-000001
✓ 页面元数据附加: evidence-000002
✓ 页面发现成功记录
✓ Fixture导出: .../page_entries.json
✓ Fixture内容验证通过
✓ Exploration log导出: ...
✓ Screenshots index导出: ...
✓ Summary获取成功
  - Total goals: 2
  - Reachable: 1
  - Succeeded: 1

============================================================
✅ 阶段C手动验证全部通过！
============================================================
```

---

## 📊 查看测试报告

### 7. 生成HTML测试报告（可选）

```bash
# 安装pytest-html（如果没有）
pip install pytest-html

# 生成HTML报告
python -m pytest prototype/stage2/tests/test_page_goal_integration.py \
    --html=test_report_stage_c.html --self-contained-html

# 打开报告
start test_report_stage_c.html  # Windows
# open test_report_stage_c.html  # macOS
# xdg-open test_report_stage_c.html  # Linux
```

### 8. 查看测试覆盖率（可选）

```bash
# 安装pytest-cov（如果没有）
pip install pytest-cov

# 生成覆盖率报告
python -m pytest prototype/stage2/tests/test_page_goal_integration.py \
    --cov=prototype.stage2.app.page_goal \
    --cov-report=html \
    --cov-report=term

# 查看HTML覆盖率报告
start htmlcov/index.html  # Windows
```

---

## 🔬 深度验证（可选）

### 9. 验证特定功能

#### 9.1 URL规范化

```python
from prototype.stage2.app.page_goal.page_adapter import PageAdapter

# 测试URL规范化
test_cases = [
    ("https://EXAMPLE.COM/Path/", "https://example.com/path"),
    ("https://example.com/path?b=2&a=1", "https://example.com/path?a=1&b=2"),
    ("https://example.com/path#hash", "https://example.com/path"),
]

for input_url, expected in test_cases:
    result = PageAdapter._normalize_url(input_url)
    assert result == expected, f"Failed: {input_url} -> {result} != {expected}"
    print(f"✓ {input_url} -> {result}")
```

#### 9.2 页面分类器

```python
from prototype.stage2.app.page_goal.page_classifier import classify_page_discovery_failure

# 测试blank检测
failure_class, confidence = classify_page_discovery_failure(
    page_signals={
        "visible_text_len": 5,
        "dom_nodes": 2,
        "blank_screenshot_ratio": 0.99,
    }
)
assert failure_class == "page_blank"
assert confidence == "high"
print(f"✓ Blank检测: {failure_class} ({confidence})")

# 测试HTTP错误分类
failure_class, confidence = classify_page_discovery_failure(http_status=403)
assert failure_class == "permission_blocked"
print(f"✓ HTTP 403: {failure_class}")

failure_class, confidence = classify_page_discovery_failure(http_status=500)
assert failure_class == "unknown"
print(f"✓ HTTP 500: {failure_class}")
```

#### 9.3 状态映射

```python
from prototype.stage2.app.page_goal.page_fixture_writer import map_goal_status_to_entry_status
from prototype.stage2.app.goal_loop.models import Goal

# 测试状态映射
test_statuses = [
    ("succeeded", "reachable"),
    ("failed_max_rounds", "failed"),
    ("blocked_by_policy", "blocked"),
    ("superseded", "deduplicated"),
    ("planned", "pending"),
]

for goal_status, expected_entry_status in test_statuses:
    goal = Goal(
        goal_id="test",
        goal_type="page",
        goal_name="test",
        status=goal_status
    )
    result = map_goal_status_to_entry_status(goal)
    assert result == expected_entry_status
    print(f"✓ {goal_status} -> {result}")
```

---

## ✅ 验证清单

完成以下检查确认阶段C正常工作：

- [ ] ✅ 6个集成测试全部通过
- [ ] ✅ 96个回归测试全部通过（零回归）
- [ ] ✅ 手动验证脚本运行成功
- [ ] ✅ URL规范化测试通过
- [ ] ✅ 页面分类器测试通过
- [ ] ✅ 状态映射测试通过
- [ ] ✅ CJK编码测试通过
- [ ] ✅ 生成的fixture schema正确

---

## 🐛 常见问题

### Q1: 测试失败 "ModuleNotFoundError: No module named 'dotenv'"

**原因**: 缺少依赖  
**解决**: `pip install python-dotenv`

### Q2: 测试失败 "encoding error"

**原因**: Windows默认cp1252编码  
**解决**: 测试已经使用UTF-8，如果仍有问题检查Python版本（需要3.12+）

### Q3: 如何调试单个测试？

```bash
# 运行单个测试并显示print输出
python -m pytest prototype/stage2/tests/test_page_goal_integration.py::test_page_discovery_session_basic -vv -s

# 进入调试器
python -m pytest prototype/stage2/tests/test_page_goal_integration.py::test_page_discovery_session_basic --pdb
```

---

## 📝 下一步

验证通过后，你可以：

1. **查看生成的文档**：
   - `docs/Stage_C_Design_And_Review.md` - 对抗式审查
   - `docs/Stage_C_Completion.md` - 完成报告
   - `prototype/stage2/app/page_goal/README.md` - 使用指南

2. **开始阶段D**：
   - Feature目标闭环设计
   - 消费page_entries.json
   - 功能点识别和验证

3. **真实浏览器集成**（可选）：
   - 连接Playwright
   - 真实DOM提取
   - 实际页面发现测试

---

**完成本指南后，你将完整验证阶段C的所有功能！** 🎉
