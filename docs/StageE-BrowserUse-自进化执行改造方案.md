# Stage E Browser Use 自进化执行 — 改造方案

> 版本：v1
> 日期：2026-07-06
> 基于：gap 分析报告 + 头脑风暴六大方向 + 小红书质效团队实践经验 + v4 需求/技术方案

---

## 目录

1. [当前状态与设计差距](#1-当前状态与设计差距)
2. [架构约束与红线](#2-架构约束与红线)
3. [四层探测金字塔](#3-四层探测金字塔)
4. [Browser Use 跨阶段执行器](#4-browser-use-跨阶段执行器)
5. [LLM 测试模型（本地大模型适配）](#5-llm-测试模型本地大模型适配)
6. [经验沉淀 L1→L2→L3 飞轮](#6-经验沉淀-l1l2l3-飞轮)
7. [AI 决策链可视化](#7-ai-决策链可视化)
8. [文件清单与改动计划](#8-文件清单与改动计划)
9. [实施路线图](#9-实施路线图)

---

## 1. 当前状态与设计差距

### 1.1 当前 Stage E 实际架构

```
main.py run_execution_goal_entrypoint()
    ├── fixture_simulated → simulate_test_case_execution()  纯模拟
    └── real_browser       → execute_test_case_with_playwright()  纯 Playwright
```

**关键发现**：
- 零 LLM 集成：失败分类用硬编码关键词匹配（`classification.py` 的 `_KEYWORD_RULES` 元组）
- 零 Browser Use：`real_browser_runner.py` 346 行，仅 `playwright.async_api` 调用
- 零 LLM 归因：`round_writer.py` 的下一轮建议用 `if/elif/else` 模板字符串
- 套路动作声明了但未实现：`semantic_takeover_click`、`semantic_fallback_locate` 等在 `playbook.py` 中仅为字符串

### 1.2 与 v4 设计文档的差距

| v4 要求 | 实际 | 差距 |
|--------|------|------|
| 测试大模型负责 Browser Use 驱动、失败归因 | 零 LLM 集成 | **完全缺失** |
| Playwright 不足时 Browser Use 语义接管 | 纯 Playwright，无回退 | **完全缺失** |
| 套路动作如 `semantic_takeover_click` 落实到代码 | 仅 `playbook.py` 中声明 | **声明未接线** |
| Preflight 决定是否允许 Browser Use | 存在但仅连 discovery 管线 | **存在未贯通** |
| 三层沉淀 L1→L2→L3 自动晋升 | L1 已有（goal_attempts），L2/L3 无自动机制 | **L2/L3 缺失** |

---

## 2. 架构约束与红线

### 2.1 不碰的模块

| 模块/文件 | 原因 |
|-----------|------|
| `goal_loop/state_machine.py` | 目标状态机核心，13 个下游模块依赖 |
| `goal_loop/models.py` | 数据模型定义，30+ 文件 import |
| `v3_real_browser.py` 发现管线 | 已验证稳定，不改已有逻辑 |
| `stage2TestCenter.js` E2E 流程 | UI 外壳，不堆业务逻辑 |
| `feature_goal/test_case_generator.py` | 已有 342 行，双路径消费 |

### 2.2 复用（import，不复制）

| 源文件 | 复用内容 | 消费方 |
|--------|---------|--------|
| `v3_real_browser._run_browser_use_target_handover()` | Browser Use Agent 创建 + 执行模式 | `browser_use_executor.py` |
| `capability_preflight.py` | 模型能力标签检测 | `execution_goal/orchestrator.py` |
| `capability_routing.py` | routing_summary 落盘 | 同上 |
| `goal_loop/playbook.py` PLAYBOOK_TABLE | 失败类→套路→出口 | `failure_adviser.py` |
| `goal_loop/classification.py` | 固定失败枚举常量 | 同上 |
| `goal_loop/predicates.py` | 停止条件谓词 | 同上 |
| `v3_real_browser._dom_collection_expression()` | cssPath/classes/id 收集 | `locator_candidates.py` |
| `stage2GoalLoopRunCenter.js` | 现有渲染框架 | 新增 AI 决策链面板 |

### 2.3 修改（增量，不破旧路径）

| 文件 | 改动 | 风险 |
|------|------|------|
| `execution_goal/orchestrator.py` | `run()` 增加 L2/L3/L4 分派逻辑 | 低 |
| `execution_goal/real_browser_runner.py` | `_run_executable_steps()` 改为遍历 L2 候选池 | 低 |
| `feature_goal/real_browser_classifier.py` | `_build_stable_locator()` 扩展为产候选池数组 | 中 |
| `feature_goal/feature_adapter.py` | `register_feature_goal()` 新增 `locator_candidates` 可选字段 | 低 |
| `feature_goal/test_case_generator.py` | step 模板中 locator 可接收数组 | 低 |
| `main.py` | `run_execution_goal_entrypoint()` 加 preflight 调用 | 低 |
| `stage2TestCenter.js` | `evaluateGoalLoopStepResult` + 步骤结果结构 | 低 |
| `public/app.js` | 详情渲染增加 AI 决策链折叠区 | 低 |

---

## 3. 四层探测金字塔

```
         ┌──────────────────────────────┐
         │ L4: Browser Use 语义接管      │ ← LLM 驱动，最贵（~5s）
         ├──────────────────────────────┤
         │ L3: ARIA 语义匹配             │ ← page.getByRole() / accessibility.snapshot()
         ├──────────────────────────────┤
         │ L2: 定位器候选池排序尝试        │ ← 多策略定位器按置信度逐一尝试
         ├──────────────────────────────┤
         │ L1: 静态快照匹配              │ ← 当前 _build_stable_locator() 产物（已有）
         └──────────────────────────────┘
```

### 3.1 L1：静态快照（已有，不改）

当前 `_build_stable_locator()` 产出：`#id > button:has-text("...") > tag[role="..."] > CSS path`。

### 3.2 L2：定位器候选池排序（P0-1）

阶段 D 不再只产一个 `element_locator` 字符串，改为产出 `locator_candidates` 数组：

```json
{
  "feature_id": "feature_001",
  "locator_candidates": [
    {"selector": "#search-btn", "confidence": 0.95, "strategy": "id"},
    {"selector": "button:has-text('查询')", "confidence": 0.85, "strategy": "text"},
    {"selector": "button[role='button']:nth-of-type(3)", "confidence": 0.60, "strategy": "role"},
    {"selector": "div.toolbar > button:nth-of-type(2)", "confidence": 0.40, "strategy": "css_path"}
  ]
}
```

Stage E 按置信度降序尝试，第一个命中即停，成功置信度 +0.05，失败降权。

### 3.3 L3：ARIA 语义匹配（P0-5）

L2 候选全部失败时，消费 Playwright 原生 `page.getByRole()` / `page.accessibility.snapshot()` 做语义级定位。纯 Playwright，不引入 LLM。

### 3.4 L4：Browser Use 语义接管（P0-4/P0-5）

触发条件：
- L2 候选池全部尝试失败
- L3 ARIA 未匹配
- capability preflight 确认 Browser Use 可用
- 高风险操作（`risk_level=="high"`）永远不进 L4

**不是等 max_rounds 耗尽**，而是在首次探测判断 Playwright 定位器不适合当前页面时立即触发。

---

## 4. Browser Use 跨阶段执行器

### 4.1 各阶段介入点

| 阶段 | 介入时机 | 角色 | 安全约束 |
|------|---------|------|---------|
| **B 菜单发现** | 菜单壳无法展开/文本无法识别 | 语义理解 + 展开 | `write_allowed=False, max_steps=3` |
| **C 页面发现** | 页面白屏/不可达/登录误判 | 页面状态判断 | `write_allowed=False, max_steps=5` |
| **D 功能点识别** | 非标准控件/复合控件识别 | 控件类型判断 | `write_allowed=False, max_steps=5` |
| **E 执行验证** | L2 候选池全部失败 | 元素定位 + 点击 | `write_allowed=True, max_steps=8`（排除高风险） |

### 4.2 统一接口

```python
# 新文件: app/browser_use_executor.py
@dataclass
class BrowserUseSafety:
    write_allowed: bool
    max_steps: int = 8
    timeout_ms: int = 15000

class BrowserUseResult:
    ok: bool
    actions: list[dict]
    screenshots: list[dict]
    evidence_id: str

async def execute_with_browser_use(
    page,           # Playwright Page
    instruction,    # Agent 指令
    context,        # dict: 阶段、目标、已尝试策略
    safety,         # BrowserUseSafety
) -> BrowserUseResult:
```

### 4.3 安全约束

- Browser Use 仅用于**定位和点击**，不用于填表/提交
- 单次调用最大步数由 `safety.max_steps` 限制
- 每次调用产 `execution_mode="browser_use_fallback"` 标记
- 必须附带执行前后截图 + Agent 决策理由
- 结果写入 `ai_decisions.jsonl`

---

## 5. LLM 测试模型（本地大模型适配）

### 5.1 模型特性与任务适配

本地大模型特点：
- Token 成本低，支持高频调用
- 内存小，上下文短（4K-8K）
- 长流程思考能力弱

**不做**：长篇失败总结报告、完整测试计划生成、开放式代码修正建议

**只做**：选择题（候选评分）、判断题（二选一）、填空题（短字段 ≤30 字）

### 5.2 Prompt 设计原则

- 每次最多 3 个问题，合并一次调用
- 强制输出 JSON（`response_format: json_object`）
- 输入精简：只给必要的上下文（页面标题 + 可见文本前 200 字 + 候选定位器列表）
- 输出模板化：预设字段名和类型，LLM 只填值

```json
// 示例：定位器候选评分
{"candidates": [
    {"selector": "#search-btn", "label": "id: search-btn"},
    {"selector": "button:has-text('查询')", "label": "text: 查询"},
    {"selector": "div.toolbar > button:nth-of-type(2)", "label": "css: toolbar button"}
]}

// LLM 返回
{"ranking": ["id: search-btn", "text: 查询", "css: toolbar button"], "reason": "ID 最稳定"}
```

### 5.3 调用频率控制

| 规则 | 说明 |
|------|------|
| 每个目标最多 1 次 | 合并所有问题到一次性 prompt |
| 仅 `unknown` / `feature_not_identified` 时调用 | 不浪费在高命中率场景 |
| 冷启动阶段才开 | 热数据（有 L2 沉淀）命中时跳过 |
| 异步生成 | 不阻塞执行，事后写回 |
| 超时 3s | 超时视为"模型不可用"，降级为规则判断 |

---

## 6. 经验沉淀 L1→L2→L3 飞轮

### 6.1 L1 战术沉淀（已有，不改）

`goal_attempts.jsonl` + `goal_state.json` 记录每次尝试。

### 6.2 L2 项目沉淀（P1-2 新增）

**定位器置信度自动管理**：

| 事件 | 置信度变化 |
|------|-----------|
| 首次尝试命中 | +0.05 |
| 后续尝试命中 | +0.02 |
| 页面变化未命中 | -0.15 |
| 超时/不存在未命中 | -0.10 |

衰减规则：
- 置信度 < 0.2 → 标记 `degraded`，下次跳过
- 连续 3 次命中 → 置信度回正到 0.6
- 30 天未使用 → 每天 -0.01

落盘格式（`locator_hints.json` 增量字段）：

```json
{
  "locator_performance": {
    "#search-btn": {"confidence": 0.92, "last_used": "2026-07-06", "hit_count": 12, "miss_count": 1},
    "button:has-text('查询')": {"confidence": 0.78, "last_used": "2026-07-06", "hit_count": 8, "miss_count": 3}
  }
}
```

### 6.3 L3 平台沉淀（已有机制，补齐触发）

当前 `promotion_candidates.json` + 人工审核流程已存在。补齐自动触发条件：

| 触发条件 | 阈值 |
|---------|------|
| 跨 ≥2 系统出现同一失败模式 | 自动生成 candidate |
| 定位器置信度 = 1.0 且使用 ≥10 次 | 标记为 `platform_candidate` |
| LLM 归因建议标记 `promotion_candidate` | 进入人工审核队列 |

---

## 7. AI 决策链可视化

### 7.1 展示位置

v4 测试中心 E2E 详情面板中，各阶段详情底部增加 `[AI 决策链]` 折叠区域。

### 7.2 展示内容

```
▶ AI 决策链（2 次参与）
  ┌─────────────────────────────────┐
  │ Step 2: 定位器选择（LLM）         │
  │ 模型: deepseek-v4-flash          │
  │ 输入: 3 个候选定位器 + 页面摘要   │
  │ 决策: 推荐 '#search-btn'         │
  │ 置信度: 0.92  耗时: 340ms        │
  │ [展开完整 prompt/response]       │
  └─────────────────────────────────┘
  ┌─────────────────────────────────┐
  │ Step 4: Browser Use 接管（L4）   │
  │ 触发: L2 候选池全部超时           │
  │ Agent: 3 步完成                  │
  │ Step 1: navigate ✓               │
  │ Step 2: locate "查询" ✓          │
  │ Step 3: click ✓                  │
  │ [查看前后截图对比]               │
  └─────────────────────────────────┘
```

### 7.3 数据源

每次 LLM/Browser Use 参与写入 `ai_decisions.jsonl`（每行一条 JSON）：

```json
{"type": "llm_ranking", "step": "locator_selection", "model": "deepseek-v4-flash",
 "input": {"candidates": [...], "page_title": "..."},
 "output": {"ranking": [...], "reason": "..."}, "confidence": 0.92, "duration_ms": 340}

{"type": "browser_use_fallback", "step": "semantic_takeover", "trigger": "l2_all_timeout",
 "agent_steps": [...], "screenshots": ["before.png", "after.png"]}
```

---

## 8. 文件清单与改动计划

### 8.1 新文件

| 文件 | 功能 | 行数估 |
|------|------|--------|
| `app/browser_use_executor.py` | 统一 Browser Use 执行接口（阶段 B/C/D/E 共用） | ~150 |
| `app/execution_goal/locator_candidates.py` | `_build_locator_candidates()` + 置信度模型 | ~120 |
| `app/execution_goal/locator_trier.py` | `_try_locator_candidates()` 按置信度尝试 + 记录 | ~100 |
| `app/execution_goal/failure_adviser.py` | LLM 归因建议（prompt 模板 + 调用入口） | ~80 |
| `app/execution_goal/ai_decision_writer.py` | `ai_decisions.jsonl` 写入 | ~60 |
| `public/aiDecisionChain.js` | 前端 AI 决策链渲染组件 | ~100 |

### 8.2 修改文件

| 文件 | 改动内容 | 风险 |
|------|---------|------|
| `execution_goal/orchestrator.py` | `run()` 增加 L2/L3/L4 分派；`run_until_stable()` 增加 preflight 查询 | 低 |
| `execution_goal/real_browser_runner.py` | `_run_executable_steps()` 改为遍历 L2 候选池而非单次点击 | 低 |
| `feature_goal/real_browser_classifier.py` | `_build_stable_locator()` 扩展为 `_build_locator_candidates()`，保留旧函数 | 中 |
| `feature_goal/feature_adapter.py` | `register_feature_goal()` 新增 `locator_candidates` 可选字段 | 低 |
| `feature_goal/test_case_generator.py` | step 模板中 locator 字段可接受数组 | 低 |
| `main.py` | `run_execution_goal_entrypoint()` 加 preflight 调用 | 低 |
| `stage2TestCenter.js` | 步骤结果增加 `aiDecisions` 数据字段 | 低 |
| `public/app.js` | 详情渲染增加 AI 决策链折叠区 | 低 |

### 8.3 仅读不改的文件（复用已有函数）

| 文件 | 复用函数 |
|------|---------|
| `v3_real_browser.py` | `_run_browser_use_target_handover()`、`_dom_collection_expression()` |
| `capability_preflight.py` | `check_model_capability()` |
| `capability_routing.py` | `build_routing_summary()` |
| `goal_loop/playbook.py` | `PLAYBOOK_TABLE`、`select_playbook()` |
| `goal_loop/classification.py` | `FIXED_FAILURE_CLASSES`、`classify_failure()` |
| `goal_loop/predicates.py` | `evaluate_stop_conditions()`、`Thresholds` |
| `stage2GoalLoopRunCenter.js` | 现有 goal_loop 渲染框架 |

---

## 9. 实施路线图

```
P0-1: L2 候选池（locator_candidates.py + real_browser_classifier 扩展）
  产出：_build_locator_candidates() 替代单一定位器
  验证：test_feature_goal_integration.py 全绿

P0-2: L2 尝试引擎（locator_trier.py + real_browser_runner 改造）
  产出：_try_locator_candidates() 按置信度尝试
  验证：test_execution_goal_integration.py 全绿

P0-3: Preflight 贯通（orchestrator.py + main.py）
  产出：Stage E 启动时查 capability 决定可用层
  验证：人工运行 E2E，查看 routing_summary.json

P0-4: Browser Use 执行器（browser_use_executor.py）
  产出：统一接口，安全约束层，阶段适配
  验证：本地 smoke test（headless 静态 HTML 页面）

P0-5: L3+L4 接入 Stage E（orchestrator.py + real_browser_runner.py 分支）
  产出：L2 失败 → L3 ARIA → L4 Browser Use 完整链路
  验证：E2E real_browser 模式，查看 ai_decisions.jsonl

P1-1: LLM 归因顾问（failure_adviser.py）
  产出：选择题型 prompt + 异步建议
  验证：round_analysis.json 中增加 llm_advice 字段

P1-2: AI 决策链可视化（前端）
  产出：E2E 详情底部 AI 决策链折叠区域
  验证：E2E 运行后前端展开查看 LLM/Browser Use 参与记录

P1-3: L2 置信度管理 + 回归测试
  产出：locator_hints.json 自动更新 + 全链路回归通过
  验证：pytest + node --test 全部通过
```

---

## 修订记录

| 日期 | 修订内容 |
|------|---------|
| 2026-07-06 | 初稿：基于 gap 分析 + 头脑风暴 + v4 需求/技术方案对标 |
