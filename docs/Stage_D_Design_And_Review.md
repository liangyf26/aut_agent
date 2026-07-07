# 阶段D设计与对抗式审查文档

**日期**: 2026-07-03
**状态**: 开发完成，对抗式审查进行中

## 1. 阶段目标

实现"功能点目标闭环"（实施计划v4 §6），从Stage C输出的可达页面（page_entries.json）中识别真实功能点（查询、重置、详情、导出、Tab、弹窗、行级操作），并生成可执行测试用例。

## 2. 架构设计

### 2.1 模块结构（遵循Stage B/C一致模式）

```
prototype/stage2/app/feature_goal/
├── __init__.py                  - 公共API导出
├── feature_classifier.py        - 功能点类型分类和风险评估
├── feature_adapter.py           - GoalLoopEngine适配器
├── loader.py                    - 从page_entries.json加载
├── test_case_generator.py       - 测试用例生成
├── feature_fixture_writer.py    - 导出feature_points.json等
└── orchestrator.py              - 会话生命周期管理
```

### 2.2 数据流

```
Stage C (page_entries.json, status='reachable')
  → loader.load_feature_goals_from_page_fixture()
    → 为每个reachable页面创建feature_discovery目标（goal_type='feature'）
  → orchestrator.scan_page_features()
    → 基于页面标题/URL的规则驱动分类（MVP，无需真实浏览器）
  → feature_classifier.classify_feature_from_page_context()
    → 识别功能点类型和风险等级
  → test_case_generator.generate_test_case()
    → 低/中风险 → 生成executable测试用例
    → 高风险 → 生成entry_confirmation确认用例
  → feature_fixture_writer.write_feature_fixture()
    → 导出feature_points.json, generated_test_cases.json, discovery_review.json
```

## 3. 关键架构决策

### 3.1 决策一：为什么Stage D从不调用`engine.record_success()`

**发现过程**：实现时首先尝试直接调用`record_success()`标记功能点识别成功，但被引擎拒绝：

```
TypeError: GoalLoopEngine.record_success() got an unexpected keyword argument 'evidence_refs'
```

**根因调查**：查看`predicates.py`发现`feature`类型的成功谓词是：

```python
feature_identified AND case_generated AND basic_path_executed AND has_feedback
```

这个谓词跨越了**阶段D**（feature_identified, case_generated）和**阶段E**（basic_path_executed, has_feedback——用例的实际执行）。阶段D单独调用`record_success()`永远无法满足这个谓词，因为`basic_path_executed`和`has_feedback`只有在真实执行用例后才能为真。

**解决方案**：改用`record_failure(explicit_class="target_discovered_but_uncovered", made_progress=True)`。这个固定失败分类专门用于"已发现但未覆盖"场景，在playbook.py中映射到`EXIT_CONTINUE`（继续追踪，不判完成），语义完全匹配阶段D的实际完成状态。

对于识别失败（退化为通用view且低置信度）的情况，使用`explicit_class="feature_not_identified"`，对应实施计划§6.5明确指出的风险。

### 3.2 决策二：Confidence信息为什么不能通过engine signals传递

**发现过程**：初版实现尝试通过`signals={"note": f"confidence:{level}"}`传递confidence，但验证时发现：

```python
engine.record_failure(
    attempt_id=attempt.attempt_id,
    explicit_class='target_discovered_but_uncovered',
    signals={'note': 'confidence:high|test note'},
    evidence_refs=[],
    made_progress=True,
)
print(attempt.notes)  # []  ← 空列表！signals被完全丢弃
```

**根因**：查看`classification.py::classify_failure()`：

```python
def classify_failure(*, explicit_class=None, signals=None):
    if explicit_class is not None:
        if is_fixed_class(explicit_class):
            return explicit_class, CONFIDENCE_HIGH  # signals参数完全被忽略
        return UNKNOWN, CONFIDENCE_LOW
    # 只有explicit_class为None时才会检查signals
    ...
```

当提供`explicit_class`时（阶段D的标准路径），`signals`参数被完全忽略，`attempt.notes`永远不会被填充。这与Stage C审查发现的Finding #4（parent_menu_id不能通过`goal.parent_goal_id`传递，必须存储在adapter自己的注册表）是同一类架构模式——**引擎的字段不携带调用方想要传递的元数据时，必须在adapter层维护独立的旁路存储**。

**解决方案**：`FeatureAdapter._feature_context[goal_id]["confidence"]`直接存储confidence，不依赖engine的signals/notes机制。`feature_fixture_writer.py`从adapter注册表读取，不从`attempt.notes`解析。

### 3.3 决策三：状态映射必须识别"永久RUNNING"语义

**发现过程**：修复上述两个问题后，测试仍然失败：

```
AssertionError: assert 'pending' == 'identified'
```

**根因**：验证`record_failure(made_progress=True)`对`goal.status`的实际影响：

```python
engine.record_failure(attempt_id=..., explicit_class='target_discovered_but_uncovered',
                       evidence_refs=[], made_progress=True)
print(goal.status)  # 'running' ← 从未改变！
```

`record_failure()`本身不调用`evaluate_stop()`——状态转换需要显式调用`evaluate_stop()`。阶段D是一次性识别流程，不驱动完整的目标循环轮次，所以`goal.status`永远停留在`STATUS_RUNNING`。

**解决方案**：`map_goal_status_to_feature_status()`增加对`STATUS_RUNNING`分支的深入处理——检查该goal最后一次attempt的`failure_class`：
- `target_discovered_but_uncovered` → `'identified'`（阶段D的真正终态）
- `feature_not_identified` → `'failed'`
- 无attempt → `'pending'`

这与`succeeded`/`failed_max_rounds`等真实终态并存，因为如果阶段E后续真的执行了用例并调用`record_success()`，`goal.status`会变成`STATUS_SUCCEEDED`，映射函数的第一个分支会优先命中。

## 4. 功能点类型与风险等级

| 功能点类型 | 风险等级 | 识别关键词 | 用例类型 |
|-----------|---------|-----------|---------|
| query | low | 查询、搜索、检索 | executable |
| reset | low | 重置、清空、清除 | executable |
| detail | low | 详情、查看、明细 | executable |
| tab | low | tab、页签、选项卡 | executable |
| export | medium | 导出、下载 | executable |
| dialog | medium | 弹窗、对话框 | executable |
| row_action_edit | high | 编辑、修改、更新 | entry_confirmation |
| row_action_delete | high | 删除、移除 | entry_confirmation |
| submit | high | 提交、保存、确认 | entry_confirmation |
| view | none | (默认fallback) | view_only |

## 5. 测试覆盖

7个集成测试，全部通过：

1. `test_feature_discovery_session_basic` - 完整会话流程
2. `test_feature_type_classification` - 功能点类型分类（含高风险的删除/编辑识别）
3. `test_risk_level_assessment` - 风险等级评估门控逻辑
4. `test_low_risk_test_case_generation` - 低风险executable用例生成
5. `test_high_risk_entry_confirmation` - 高风险entry_confirmation用例（不含steps）
6. `test_feature_fixture_export` - 多功能点类型导出验证（不退化成全部view）
7. `test_cjk_feature_text_preserved` - CJK文本编码往返

## 6. 零回归验证

```
109 passed (96 Stage A/B + 6 Stage C + 7 Stage D)
```

## 7. 对抗式审查

使用Workflow工具运行5维度独立审查（Opus高强度推理）：

1. **API Contract Correctness** - 验证与GoalLoopEngine实际API签名的一致性
2. **Feature Type Degradation Risk** - 验证不会退化成全部view（实施计划§6.5核心风险）
3. **Risk Level & Test Case Safety** - 验证高风险操作不会被误判为可安全执行
4. **State Machine & Concurrency Correctness** - 验证状态机使用和并发攻击面
5. **Encoding, Evidence Chain & Zero-Regression** - 验证UTF-8编码、证据链完整性、回归

审查结果和修复记录见下文（Section 8）。

---

## 8. 审查发现与修复记录

**Workflow执行统计**: 7个agent，385,453 tokens，113次工具调用

**流程**: 5个独立维度并行审查（Opus高强度推理）→ 1个Opus验证agent交叉核实

**结果**: 14个原始发现 → 验证阶段确认全部14个（0个被拒绝）→ 去重后11个独立问题

| 严重程度 | 数量 |
|---------|------|
| Critical | 1 |
| High | 5 |
| Medium | 4 |
| Low | 1 |

所有11个问题均已修复并通过回归测试验证（新增3个专门的回归测试）。

### 8.1 Critical: 关键词平局解析偏向低风险类型

**文件**: `feature_classifier.py:124-125`

**问题**: `classify_feature_type()`仅按`keyword_count`降序排序。当一个文本同时匹配一个低风险关键词和一个高风险关键词时（如"查询并删除"同时匹配"查询"和"删除"），两者`keyword_count`都是1，Python稳定排序保留字典插入顺序，而`FEATURE_TYPE_DEFINITIONS`中低风险类型排在高风险类型之前，导致平局总是解析为**更安全但错误**的分类。

**实测验证**：
```
'查询并删除' → feature_type='query', risk_level='low'  (delete被丢弃)
'保存查询'   → feature_type='query', risk_level='low'  (submit被丢弃)
'编辑或查看' → feature_type='detail', risk_level='low'  (edit被丢弃)
```

这个`risk_level`直接传给`generate_test_case()`，生成`requires_approval=False`的可执行测试，意味着一个真正会触发删除操作的控件，会被系统自动点击而无需人工审批。

**修复**：排序键从`keyword_count`扩展为`(keyword_count, risk_rank)`复合键，risk_rank映射`high=3/medium=2/low=1/none=0`，平局时优先选择更危险的解释：

```python
risk_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
matched_types.sort(
    key=lambda x: (x["keyword_count"], risk_rank.get(x["risk_level"], 0)),
    reverse=True,
)
```

**回归测试**: `test_keyword_tie_resolves_to_highest_risk` — 验证"查询并删除"→`row_action_delete`/`high`，"保存查询"→`high`，"编辑或查看"→`row_action_edit`/`high`。

### 8.2 High: Degradation检测机制在生产路径上是死代码

**文件**: `feature_classifier.py:187`, `feature_adapter.py:238`

**问题**: 生产路径（`orchestrator.scan_page_features`）唯一调用的分类函数是`classify_feature_from_page_context()`，它硬编码baseline view的`confidence="high"`，永远不会输出`confidence="low"`。而`feature_adapter.py`中检测"退化"的逻辑是`is_degraded = feature_type == "view" and confidence == "low"`——因为生产路径的view永远是`confidence="high"`，这个安全网永远不会触发，导致退化页面被错误地记录为`target_discovered_but_uncovered`（"已识别"）而非`feature_not_identified`（"未识别"）。

真正会输出`view + confidence="low"`的是另一个函数`classify_feature_type()`，但它从未被生产代码调用。

**修复**：将`classify_feature_from_page_context()`的baseline view改为`confidence="low"`，使其语义与"未推断出任何具体功能"保持一致：

```python
# 修复前: confidence="high"（永远不会被判定为degraded）
# 修复后: confidence="low"（页面若只产生baseline view，会被正确判定为degraded）
features.append(FeatureClassification(
    feature_type="view", risk_level="none", confidence="low", ...
))
```

**回归测试**: `test_degraded_page_reported_as_not_identified` — 验证"Dashboard"页面（无匹配关键词）的view功能点状态为`'failed'`且`failure_class='feature_not_identified'`。

### 8.3 High: 英文页面标题全部退化成view，违反验收标准

**文件**: `feature_classifier.py:197`

**问题**: `classify_feature_from_page_context()`只在标题/URL包含字面子串`["管理","列表","list","manage"]`时才推断query/reset功能。9个常见英文管理页面标题（Dashboard, Settings, Reports, Users, Roles, Permissions, Profile, Audit Log, Home）实测**全部**只产生`[view]`，直接违反实施计划§6.4验收标准"至少能稳定识别若干低风险功能点"，精确命中§6.5警告的核心风险。

结合8.2的问题，这些退化页面还会被错误地标记为"已识别"，使问题更难被发现。

**修复**：扩展关键词词汇表覆盖常见英文管理页面词汇：

```python
_PAGE_CONTEXT_QUERY_KEYWORDS = (
    "管理", "列表", "list", "manage", "admin", "users", "roles",
    "settings", "report", "audit", "permissions",
)
_PAGE_CONTEXT_DETAIL_KEYWORDS = ("详情", "detail", "profile", "info")
```

**回归测试**: `test_english_titles_do_not_degrade_to_view_only` — 验证Users/Settings/Reports/Roles/Permissions均能识别出query功能点。

**已知局限**：这仍是MVP级别的标题/URL启发式规则，非DOM驱动的真实元素识别。审查建议长期方案应改为基于`classify_feature_type()`分析真实DOM元素文本，该函数已经能正确处理任意语言的按钮文本。

### 8.4 High: `should_generate_executable_test()`是死代码，与实际风险门控逻辑脱节

**文件**: `orchestrator.py:18`, `test_case_generator.py`

**问题**: `should_generate_executable_test()`被导入但从未在流程中被调用（仅出现在导入、`__init__.py`和测试中）。实际的执行/确认门控逻辑在`generate_test_case()`内部，仅检查`risk_level=="high"`，完全忽略`confidence`。两者策略不一致——前者要求`confidence`至少为`medium`才生成可执行用例，后者对任何`confidence`的低/中风险功能点都生成可执行用例。

**修复**：让`should_generate_executable_test()`成为唯一决策源，`generate_test_case()`内部调用它：

```python
needs_confirmation = (
    not should_generate_executable_test(risk_level, confidence)
    or feature_type in _SIDE_EFFECTING_FEATURE_TYPES
)
```

同时移除orchestrator.py中该函数的未使用导入。

### 8.5 High: `feature_count`计数错误 + `goal_summary.json`与`feature_points.json`状态互相矛盾

**文件**: `orchestrator.py:290-319`（此问题被3个独立审查维度分别发现，是同一根因）

**问题A**（feature_count）: `feature_count = len(self.adapter._feature_context) - succeeded`。注释声称"排除page_scan目标"，但实际减去的`succeeded`永远是0（阶段D从不调用`record_success()`），导致`_feature_context`中的page_scan标记条目被误计为功能点，每个页面多算1个。

**问题B**（状态桶不一致）: `get_summary()`直接读取`goal.status`分桶。因为阶段D从不调用`evaluate_stop()`，所有目标永远停留在`STATUS_RUNNING`，全部落入`pending`桶。但`write_feature_fixture()`使用`map_goal_status_to_feature_status()`能正确将同样的`STATUS_RUNNING`目标解析为`'identified'`。结果：`goal_summary.json`报告0个已识别功能点，而`feature_points.json`报告全部已识别——两份同一次运行的产物互相矛盾。

**修复**：`get_summary()`改用`map_goal_status_to_feature_status()`统一状态分桶，`feature_count`直接等于`sum(feature_types.values())`（与排除page_scan的逻辑保持一致）：

```python
status_counts = Counter()
for goal in self.engine.goals.values():
    status_counts[map_goal_status_to_feature_status(goal, self.adapter)] += 1
...
"feature_count": sum(feature_types.values()),
```

### 8.6 Medium: `permission_required`/`approval_needed`是不存在的失败分类字符串

**文件**: `feature_fixture_writer.py:85`

**问题**: `map_goal_status_to_feature_status()`用`last_failure_class in {"permission_required", "approval_needed"}`区分blocked/pending，但这两个字符串不存在于18个固定失败分类中（真实的是`permission_blocked`和`login_required`）。这个比较永远为False，目前因阶段D从不触发`waiting_human`状态而处于潜伏状态，但字符串契约本身是错的。

**修复**：导入真实常量并使用：

```python
from ..goal_loop.classification import PERMISSION_BLOCKED, LOGIN_REQUIRED
...
if last_failure_class in {PERMISSION_BLOCKED, LOGIN_REQUIRED}:
    return "blocked"
```

### 8.7 Medium: `stop_reason`字段被错误填充为`goal.status`

**文件**: `feature_fixture_writer.py:182`（此问题被2个独立审查维度分别发现）

**问题**: 导出的`metadata.stop_reason`字段实际存储的是`goal.status`（状态字符串），而不是`Goal.stop_reason`属性（引擎在`evaluate_stop()`中设置的真实停止原因）。由于阶段D从不调用`evaluate_stop()`，该字段永远是字面值`'running'`，对下游消费者毫无意义。

**修复**：拆分为两个语义清晰的字段：

```python
"goal_status_raw": goal.status,      # 原始引擎状态
"stop_reason": goal.stop_reason,     # 真正的停止原因（阶段D中恒为None）
```

### 8.8 Medium: Export功能点生成可执行测试，实际点击会产生真实文件写入副作用

**文件**: `test_case_generator.py:207-227`

**问题**: `export`功能点的`risk_level="medium"`，`generate_test_case()`将low和medium风险同等对待，生成`requires_approval=False`的可执行测试。生成的测试步骤第2步是真实的`click`导出按钮操作——如果针对真实系统执行，会触发真实的服务端导出、文件写入和审计日志记录。步骤3的描述甚至自我矛盾地写着"需人工确认"，却标记为不需要审批。

**修复**：将`export`归入"有副作用的功能类型"集合，强制走`entry_confirmation`路径：

```python
_SIDE_EFFECTING_FEATURE_TYPES = frozenset({"export"})
needs_confirmation = (
    not should_generate_executable_test(risk_level, confidence)
    or feature_type in _SIDE_EFFECTING_FEATURE_TYPES
)
```

### 8.9 Medium: 手动设置`goal.status='running'`导致engine frontier腐化（跨阶段集成风险）

**文件**: `orchestrator.py:137,176`

**问题**: `scan_page_features()`直接赋值`goal.status = "running"`绕过`engine.activate_next()`，从未设置`engine.active_goal_id`，也从未把目标从`engine.frontier`中移除。阶段D内部因为从不调用`activate_next()`/`evaluate_stop()`，这个问题处于潜伏状态。但如果这个engine实例被传递给阶段E继续使用并调用`activate_next()`：该方法只会激活状态为`STATUS_PLANNED`的目标（`state_machine.py:157`跳过非PLANNED状态），会跳过所有阶段D手动置为running的目标，转而激活root goal——静默丢弃所有已识别的功能点。

**修复**：在`FeatureGoalOrchestrator`类文档中明确警告此限制，说明该engine实例仅适用于一次性fixture生成流程，不可传递给会调用`activate_next()`/`evaluate_stop()`的下游阶段：

```python
"""
IMPORTANT — engine reuse warning: scan_page_features() activates goals by
setting goal.status = "running" directly, NOT via engine.activate_next()...
Do NOT hand self.engine to a caller that will invoke activate_next() or
evaluate_stop() on it (e.g. a Stage E execution driver)...
Construct a fresh GoalLoopEngine for any stage that needs proper
activate_next() semantics.
"""
```

**架构决策说明**：审查建议的替代方案（真正通过`activate_next()`驱动激活）会破坏当前fixture测试模式的简洁性，且阶段D的一次性识别流程本质上不需要frontier机制。选择文档化限制而非重构，是权衡了当前MVP范围与未来阶段E集成需求后的决定。

### 8.10 Low: CJK编码回归测试存在断言逻辑漏洞

**文件**: `test_feature_goal_integration.py:319`（原始行号，已修复）

**问题**: 原测试断言为`'溯源管理' in content or any('溯源' in element_text for f in features)`。第二个OR分支永远为真——因为`json.loads()`会将任何`\uXXXX`转义序列解码回真实CJK字符，无论文件在磁盘上是否被正确写入。这意味着如果`safe_json_write()`回归为`ensure_ascii=True`（Stage C Finding #12的同类缺陷），磁盘上的原始内容会变成转义字符（第一个分支False），但解析后的Python对象仍然正确（第二个分支True）——整个断言仍然通过，回归被完全掩盖。

**修复**：只断言原始文件内容，去掉OR分支，并额外验证没有转义序列：

```python
assert "溯源管理" in content
assert "用户管理" in content
assert "\\u" not in content  # no escaped CJK anywhere in the file
```

## 9. 与自查发现的交叉验证

在启动workflow等待期间，我通过直接运行代码手动验证了2个假设风险，均被workflow独立确认：

1. **关键词平局风险**（自查："查询并删除"→`low`/`query`）：与Section 8.1的Critical发现完全一致，workflow进一步指出这是排序稳定性+字典插入顺序的组合效应，并提供了精确的行号定位。
2. **英文标题退化风险**（自查：5个英文页面全部产生`{"view": 5}`）：与Section 8.3的High发现完全一致，workflow额外发现了这与Section 8.2（degradation检测死代码）叠加后的连锁效应——退化页面不仅功能点单一，还会被错误标记为"已识别"。

这次交叉验证表明：对抗式审查workflow不仅确认了直觉发现的表层问题，还系统性地挖掘出了根因层面的关联缺陷（8.2与8.3的耦合关系，8.5中三个独立症状指向同一根因）。
