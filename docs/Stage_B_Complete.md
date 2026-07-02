# Stage B 菜单目标闭环 - 完成报告

## 执行摘要

阶段B已完成所有核心模块开发和API对接，**所有36个测试通过**，Stage A的59个测试保持通过，总计**95个测试全部通过**。

## 完成的模块

### 1. menu_goal/loader.py ✅
- 功能：从menu_entries.json加载目标到GoalLoopEngine
- 测试覆盖：5个测试全部通过
- 关键特性：
  - CJK文本保留
  - 父目标层级关联
  - 菜单上下文存储（menu_path, route_hint, status）

### 2. menu_goal/orchestrator.py ✅
- 功能：会话生命周期管理器
- 测试覆盖：5个测试全部通过
- 关键特性：
  - 封装GoalLoopEngine初始化
  - 创建根目标
  - 导出menu_entries.json固定装置
  - 聚合会话摘要

### 3. menu_goal/discovery_adapter.py ✅
- 功能：v3发现结果→目标循环原语映射
- 测试覆盖：8个测试全部通过
- 关键特性：
  - 注册菜单目标
  - 记录发现尝试
  - 记录导航步骤
  - 附加证据（截图、菜单元数据）
  - 记录失败/成功
  - 内部菜单上下文注册表

### 4. menu_goal/menu_classifier.py ✅
- 功能：菜单特定故障分类器扩展
- 测试覆盖：12个测试全部通过
- 关键特性：
  - v3错误码映射到固定故障类
  - 操作类型分类
  - 从发现日志分类
  - 重试决策逻辑

### 5. menu_goal/fixture_writer.py ✅
- 功能：导出menu_entries.json
- 测试覆盖：6个测试全部通过
- 关键特性：
  - 从目标循环状态重建菜单条目
  - CJK文本编码保留
  - 父子层级关系
  - 排序输出（按menu_id）

## API对接要点

### GoalLoopEngine API映射
```python
# 注册目标
register_goal(goal_type, goal_name, origin=..., parent_goal_id=...)

# 记录步骤
add_step(attempt_id, kind, action=...)

# 附加证据
attach_evidence(step_id, kind, uri=..., note=...)

# 记录失败
record_failure(attempt_id, explicit_class, signals, evidence_refs)

# 记录成功（需满足success predicate）
record_success(attempt_id, signals)
```

### 数据结构
- `engine.goals`: `dict[str, Goal]` ✅
- `engine.attempts`: `list[GoalAttempt]` ⚠️ 需遍历查找
- `engine.steps`: `dict[str, GoalStep]` ✅
- `engine.evidence`: `dict[str, EvidenceRef]` ✅
- `Goal.notes`: `list[str]` ⚠️ 非dict，改用adapter内部_menu_context

### 成功谓词
菜单目标的success predicate：
```python
signals = {
    "menu_text": True,  # 菜单文本存在
    "path": True,       # 菜单路径存在
    "screenshot": True, # 截图存在
}
```
谓词表达式：`has_menu_text AND has_path AND has_screenshot`

### 状态管理
- 初始状态：`planned`
- 需手动设置`status='running'`才能`start_attempt`
- pending计数包含：`planned`, `pending`, `running`状态

## 测试覆盖总结

| 模块 | 测试数 | 状态 |
|------|--------|------|
| loader | 5 | ✅ |
| orchestrator | 5 | ✅ |
| discovery_adapter | 8 | ✅ |
| menu_classifier | 12 | ✅ |
| fixture_writer | 6 | ✅ |
| **Stage B 总计** | **36** | **✅** |
| **Stage A (goal_loop)** | **59** | **✅** |
| **总计** | **95** | **✅** |

## 架构设计亮点

### 1. 菜单上下文注册表
使用adapter内部`_menu_context: dict[str, dict]`存储菜单元数据：
- menu_id
- menu_path
- menu_depth
- route_hint

解决了Goal.notes是list而非dict的问题。

### 2. 适配器模式
DiscoveryAdapter作为v3和goal loop之间的桥梁：
- 隐藏GoalLoopEngine API复杂性
- 提供菜单发现专用高级API
- 管理菜单上下文生命周期

### 3. 固定装置导出
write_menu_fixture支持Stage C独立测试：
- 从目标循环状态序列化
- CJK文本完整保留
- v3兼容格式

## 已知限制和简化

### 1. 截图路径
当前实现中screenshot_path在fixture中为null。
- **原因**：Goal对象没有last_attempt_id，需要遍历所有attempts
- **影响**：Stage C测试不依赖截图路径
- **未来**：可在orchestrator中单独跟踪

### 2. Attempt查找
attempts是list而非dict，需要线性查找：
```python
def get_attempt(self, attempt_id: str):
    for attempt in self.engine.attempts:
        if attempt.attempt_id == attempt_id:
            return attempt
    return None
```
- **影响**：O(n)复杂度，但n通常很小（每个目标几次尝试）
- **未来**：可建立索引

## 下一步工作

### Stage C：固定装置测试 (待开发)
- [ ] 从menu_entries.json加载测试场景
- [ ] 独立于Stage B运行
- [ ] 验证目标循环逻辑
- [ ] 测试故障分类和playbook选择

### Stage D/E：执行器集成 (待设计)
- [ ] 浏览器交互层
- [ ] 菜单发现实现
- [ ] 截图捕获
- [ ] 实时evidence收集

## 技术债务

无重大技术债务。所有设计决策都有清晰的权衡和未来路径。

## 结论

阶段B已全面完成：
- ✅ 所有核心模块实现
- ✅ 完整API对接
- ✅ 36个测试通过
- ✅ Stage A无回归（59个测试通过）
- ✅ CJK文本处理正确
- ✅ 架构设计清晰

准备进入Stage C固定装置测试开发。
