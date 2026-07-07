# 阶段E实现计划：执行、证据与复盘闭环

对应 `docs/第二阶段实施计划v4.md` §7。目标：把阶段D产出的功能点/用例，实际"跑"一遍（MVP：模拟执行器，非真实Playwright），产出 run 级+case级+step级证据链，并把结果投影进iteration层驱动复盘（round_analysis/next_round_plan/human_tasks）。

## 0. 关键确认点（需要你先拍板）

**默认按模拟执行器MVP推进**：executor.py 内部不接入真实 Playwright/CDP，而是消费阶段D的 test case steps，为每个 step 生成确定性的模拟观测结果（action_log条目 + 占位截图索引 + 网络事件占位），是否通过由该 step 的 `action` 类型和 `risk_level/policy_gate` 决定。这与阶段B/C/D一致的"安全占位优先，真实执行器可插拔替换"模式一脉相承（v3_orchestrator.py 的 `execution_mode` 分支就是先例：demo/safe 模式用占位，real_browser 模式才真正跑）。

如果你希望阶段E直接接入真实Playwright执行，请现在提出，我会调整方案（工作量会显著增加：需要真实浏览器会话管理、真实DOM定位、真实截图/网络监听落盘）。默认按模拟执行器MVP推进。

## 1. 七个架构决策（沿用既有调研结论，不改变）

1. 复用 `goal_loop/compat.py` 的既定投影路径（`failure_classification_to_cluster` / `playbook_action_to_retry_action` / `build_retry_plan` / `experience_update_to_promotion_candidate`），不新建平行的 iteration 结构。
2. 阶段E必须构造全新 `GoalLoopEngine(run_id=...)` 实例，绝不复用阶段D遗留的engine（避免frontier腐化）。
3. "证据门控"通过正确调用 `engine.attach_evidence()` + `engine.record_success()`（其内部 `check_evidence_complete` 机制）自然产生，不另造校验逻辑——这就是v4 §7.6"无法定位到step级证据就不能判稳定通过"的代码级落地。
4. 复用 `runtime/policy_gate.py::evaluate_action_policy()` 做已授权用例判定；阶段D已经在 test_case_generator 里判定过一次（`requires_approval`），阶段E执行前**再过一次** policy_gate 作为纵深防御（阶段D到阶段E之间用例可能被人工调整）。
5. `round_analysis.json` / `next_round_plan.json` / `human_tasks.json` 是 iteration 层数据（`FailureClusterRecord`/`RetryPlanRecord`/`NextRoundDecisionRecord`）的重命名投影或形状借鉴，不是第二套真相；字段形状参考 `v3_orchestrator.py::_build_round_analysis/_build_human_tasks/_build_next_round_plan`（仅借鉴形状，不做代码级import，因为v3_orchestrator是完全独立的执行栈）。
6. `human_tasks.json` 每条记录字段对齐 v3_orchestrator 的既有schema：`task_id/type/status/title/ui_action/case_ids/blocks_next_round`。
7. `human_takeover` 触发条件与 policy_gate 的 `needs_review` 决策对齐：任何 case 的 policy 决策为 `needs_review` 或 `blocked` 且该 case 是 `requires_approval` 类型，都会生成一条 human_task；若存在 `blocks_next_round=True` 的任务则整轮标记 `waiting_human`。

## 2. 模块设计（6个文件，目录 `prototype/stage2/app/execution/`）

沿用 `feature_goal/` 的六文件模式命名。阶段E不是新增第四种 goal_type（GOAL_TYPES 仍只有 menu/page/feature），所以不叫 `execution_goal`，而叫 `execution`（与 `runtime/` `iteration/` `progress/` 同级的功能模块目录）。

### 2.1 `execution/loader.py`
```python
def load_execution_input(fixtures_dir: Path) -> ExecutionInput:
    """读取阶段D产出 feature_points.json + generated_test_cases.json，
    校验每个 test case 都能反查到对应 feature_point（chain 完整性）。
    缺失时抛 ValueError，不静默跳过（阶段D的既有约定）。"""
```
- `ExecutionInput` dataclass：`feature_points: list[dict]`, `test_cases: list[dict]`, `run_id: str`, `page_index: dict[str, dict]`（page_id -> page dict，供executor取page_url）。

### 2.2 `execution/execution_adapter.py`
职责：把一个 test case 的执行，转换成 GoalLoopEngine 的 attempt/step/evidence 调用序列。
```python
def policy_check_case(case: dict, *, config=None) -> PolicyGateDecision:
    """对 test case 的动作重新跑一次 evaluate_action_policy，
    risk_level 取 case['risk_level']（阶段D已定），
    action 取 case['metadata']['feature_type']。"""

def run_case_as_attempt(
    engine: GoalLoopEngine, goal_id: str, case: dict, step_results: list[StepResult]
) -> tuple[GoalAttempt, PredicateResult | FailureClassification]:
    """start_attempt -> 对每个 step_result 调 add_step + attach_evidence
    -> 按 step_results 是否全部 observed=True 且无失败，
       调 record_success 或 record_failure。"""
```
- `StepResult` dataclass：`step_index, action, observed: bool, status: Literal["passed","failed","not_observed"], evidence_kind, evidence_uri, note`。

### 2.3 `execution/executor.py`（模拟执行器 MVP）
```python
def execute_case(case: dict, page_url: str | None) -> CaseExecutionRecord:
    """遍历 case['steps']（阶段D schema：step/action/target/description/[value/expected]），
    为每个 step 生成一条 action_log 记录 + 一条模拟证据（screenshot占位路径 + dom_snapshot占位）。
    MVP: 占位模式下不伪造失败，全部observed step判定passed，但证据链必须完整
    （entry_confirmation 类用例 requires_approval=True 永不进入此函数，上游拦截）。"""

def execute_entry_confirmation(case: dict) -> CaseExecutionRecord:
    """requires_approval=True 的用例：只记录一个 'awaiting_human_confirmation' 状态的 step，
    不产生 pass/fail 判定，直接进入 human_tasks。"""
```
- `CaseExecutionRecord` dataclass：`case_id, feature_id, status, verdict, execution_mode="simulated_placeholder", steps: list[StepResult], started_at, finished_at, message`。
- `action_log.jsonl`：逐行写，每行 `{ts, run_id, case_id, step_index, action, target, status}`。
- `network_events.json`：模拟模式下为空事件列表占位，字段存在但诚实标注 `execution_mode="simulated_placeholder"`，不伪造网络流量。
- `screenshots_index.json`：每个 observed step 一条 `{screenshot_id, case_id, step_index, path, is_placeholder: true}`。

### 2.4 `execution/round_analysis.py`
```python
def build_iteration_artifacts(
    run_id: str, engine: GoalLoopEngine, case_records: list[CaseExecutionRecord]
) -> IterationArtifacts:
    """用 compat.py 把 engine.classifications / engine.playbook_action_records /
    engine.experience_updates 投影成 FailureClusterRecord / RetryPlanRecord / PromotionCandidateRecord，
    再调用 engine.evaluate_stop() 结果映射成 StopDecisionRecord，
    组装成 IterationArtifacts（iteration/models.py 现有dataclass，不新建）。"""

def build_round_analysis_view(artifacts, case_records) -> dict:
    """形状对齐 v3_orchestrator._build_round_analysis 返回结构，数据来源换成 iteration artifacts。"""

def build_human_tasks_view(case_records, policy_decisions) -> dict:
    """requires_approval 或 policy_gate needs_review/blocked 的case -> 一条human_task，
    形状对齐 v3_orchestrator._build_human_tasks。"""

def build_next_round_plan_view(round_analysis_view, human_tasks_view, next_round_decision) -> dict:
    """should_start_next_round 直接取 NextRoundDecisionRecord.should_start_next_round，
    形状对齐 v3_orchestrator._build_next_round_plan。"""
```

### 2.5 `execution/execution_fixture_writer.py`
```python
def write_execution_artifacts(output_dir: Path, *, execution_results, action_log_entries,
                                network_events, screenshots_index, round_analysis,
                                next_round_plan, run_report_md, run_report_json,
                                human_tasks, human_takeover=None) -> dict[str, Path]:
    """落盘 v4 §7 清单要求的全部文件：
    execution_results.json, action_log.jsonl, network_events.json, screenshots_index.json,
    round_analysis.json, next_round_plan.json, run_report.md, run_report.json, human_tasks.json,
    human_takeover.json（仅当存在需要人工接管的阻塞项才写出，否则不生成——
    与阶段C/D"文件存在即代表触发"的既有约定一致）。"""
```

### 2.6 `execution/orchestrator.py`
```python
def run_execution_round(fixtures_dir: Path, output_dir: Path, *, run_id: str | None = None) -> ExecutionRoundResult:
    """1. loader.load_execution_input()
       2. 全新 GoalLoopEngine(run_id) + register_goal(goal_type="feature", ...)
       3. activate_next() + 对每个case: policy_check_case -> executor.execute_case/execute_entry_confirmation
          -> execution_adapter.run_case_as_attempt (attach evidence, record_success/record_failure)
       4. engine.evaluate_stop() 判定本轮是否停止
       5. round_analysis.build_iteration_artifacts + 三个view + run_report
       6. execution_fixture_writer.write_execution_artifacts
       返回 ExecutionRoundResult(run_id, output_paths, summary)。"""
```

### 2.7 `execution/__init__.py`
仅导出 `run_execution_round` 与关键dataclass，不做逻辑。

## 3. 数据流

```
feature_points.json + generated_test_cases.json  (阶段D产物, 只读输入)
        |  loader.load_execution_input
        v
ExecutionInput
        |  orchestrator: 新GoalLoopEngine + register_goal(feature)
        v
for each case:
   policy_check_case (policy_gate 纵深防御)
        |
        +-- blocked/needs_review + requires_approval -> execute_entry_confirmation -> human_task
        +-- allowed -> execute_case -> CaseExecutionRecord(steps, evidence占位)
        |  execution_adapter.run_case_as_attempt
        v
engine.attempts / classifications / playbook_action_records / experience_updates
        |  round_analysis.build_iteration_artifacts (走 compat.py 投影桥)
        v
IterationArtifacts (iteration/models.py 现有结构)
        |  三个 view 构建函数
        v
round_analysis.json / next_round_plan.json / human_tasks.json (+ human_takeover.json 条件性)
        v
execution_fixture_writer.write_execution_artifacts -> v4 §7 全部交付物落盘
```

## 4. 与 v4 §7.4 验收标准对照

| §7.4 验收项 | 落地方式 |
|---|---|
| run级+case级+step级证据 | `add_step`+`attach_evidence` 逐step绑定，`check_evidence_complete` 强制校验 |
| 无法定位到step级证据不能判稳定通过 | `record_success` 内建evidence gate，缺证据抛`ValueError`，orchestrator捕获后转为`evidence_incomplete`分类 |
| 复用iteration层结构 | 全程通过`compat.py`投影，无新增parallel dataclass |
| 8类固定失败分类 | `classification.py`既有分类表直接复用 |
| human_tasks可操作 | 字段对齐v3_orchestrator既有UI消费schema |

## 5. 集成测试计划（10个，`prototype/stage2/tests/test_execution_*.py`）

1. `test_loader_reads_stage_d_fixtures` — loader正确读取阶段D的feature_points/generated_test_cases
2. `test_loader_rejects_orphan_case` — test case引用不存在的feature_id时loader抛错
3. `test_executor_executable_case_produces_step_evidence` — executable类型case每个observed step都有evidence
4. `test_executor_entry_confirmation_case_no_verdict` — requires_approval case不产生pass/fail，只产生awaiting_human
5. `test_policy_gate_second_pass_can_downgrade_case` — 阶段D标记auto_allowed但policy_gate复核后risky_submit未列入allowlist时转人工任务
6. `test_engine_record_success_requires_evidence` — 故意漏加evidence，验证`record_success`拒绝（回归验证阶段A证据门控没被破坏）
7. `test_round_analysis_projects_via_compat` — 断言`round_analysis.py`产出的cluster/action确实来自`compat.py`的函数而非重新实现
8. `test_human_tasks_blocks_next_round_when_approval_pending` — 存在待批准case时`next_round_plan.should_start_next_round=False`
9. `test_write_execution_artifacts_full_set` — 断言v4§7全部9个文件都被创建（human_takeover除外，按条件）
10. `test_orchestrator_end_to_end_smoke` — 用阶段D demo fixtures跑完整`run_execution_round`，断言summary.status和文件都存在，engine是全新实例（跑两次run_id不同，frontier不互相污染）

## 6. 对抗式审查计划（Workflow多agent，5维度，沿用阶段B/C/D模式）

1. **证据链完整性**：是否存在"声称通过但没有step级证据"的路径？evidence gate是否可被绕过？
2. **iteration层复用合规性**：是否有任何新建的、与FailureClusterRecord/RetryPlanRecord语义重复的结构？
3. **policy_gate纵深防御**：阶段D的auto_allowed判定和阶段E的二次policy_check是否可能矛盾（比如阶段D允许但policy_gate拒绝时，是否正确转人工而不是静默跳过）？
4. **engine实例隔离**：是否真的每次`run_execution_round`都是全新engine，还是不小心复用了模块级单例？
5. **产物schema对v4§7的字段级符合度**：9个交付物文件的字段是否覆盖v4文档要求，命名是否与v3_orchestrator形状一致但不引入外来字段。

每个维度一个agent，输出问题列表；再用一个综合agent交叉验证去重，最后修复。

## 7. 实现顺序（12步）

1. 创建 `execution/` 目录 + `__init__.py`
2. `loader.py` + 单测#1,#2
3. `executor.py`（CaseExecutionRecord + execute_case + execute_entry_confirmation）+ 单测#3,#4
4. `execution_adapter.py`（policy_check_case + run_case_as_attempt）+ 单测#5,#6
5. `round_analysis.py` 三个view函数 + build_iteration_artifacts + 单测#7,#8
6. `execution_fixture_writer.py` + 单测#9
7. `orchestrator.py::run_execution_round` 串联 + 单测#10（端到端smoke）
8. 全量跑 `pytest prototype/stage2/tests/test_execution_*.py -q`
9. Workflow对抗式审查（5维度并行 + 交叉验证）
10. 按审查发现修复问题
11. 复审确认无遗留问题
12. 生成 `Stage_E_Design_And_Review.md` + `Stage_E_Completion.md`

---

以上是完整阶段E计划。默认按模拟执行器MVP推进（§0已说明），如需真实Playwright接入请现在提出，否则将直接开始步骤1。
