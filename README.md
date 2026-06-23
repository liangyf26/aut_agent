# aut_agent

AI Agent 软件自动化评测平台，面向第三方软件项目验收场景，辅助甲方或测评机构对乙方交付的软件系统进行可追溯、可复核的验收评测。

当前仓库已经包含一个零依赖 Node.js MVP：

- 运行中心首页，聚合当前项目阶段、推荐动作、阻塞项、第二阶段运行摘要、orchestration session 摘要和关键 artifacts
- 创建和管理评测项目
- 录入被测系统信息和需求资料
- 从需求资料中提取功能模块、功能点、验收项和测试场景
- 生成可审核的测试用例
- 模拟 Web UI 自动化执行并记录执行证据摘要
- 生成评测报告、缺陷清单和执行后沉淀的 Playwright 示例代码
- 通过 `GET /api/stage2/overview` 聚合第二阶段 run、验证矩阵、平台日报、模型对比、人工录制候选审阅摘要和基线冻结信息
- 支持 orchestration session 级持久化、session 详情展示，以及 `resume / mark resolved` 这类受控恢复动作

同时，仓库内已经落地第二阶段 Python 原型，用于验证：

- Web UI 真实页面发现与受控遍历
- 模板化功能点验证
- 失败簇归因与多轮调度
- 人工录制、人工接管与恢复续跑
- 结构化产物、运行报告、平台日报与模型对比
- orchestration session 级持久化与运行中心可操作化
- 验证层共享抽象接线、复用动作族抽取和泛化回归护栏

## 快速启动

```powershell
npm run dev
```

启动后打开：

```text
http://localhost:4173
```

当前首页默认展示“自动化测试运行中心”。项目级运行态和第二阶段聚合视图会在页面空闲时自动刷新。

## 常用命令

```powershell
npm run check
npm run dev
python -m prototype.stage2.main
python -m prototype.stage2.main --bootstrap-template --template demo_query_entry --page-url https://example.com/query --page-name "示例查询页" --scenario-kind query
python -m prototype.stage2.main --routing-summary --template demo_query_entry
python -m prototype.stage2.main --live-discovery --template demo_query_entry --model AI-tester --cdp-url http://localhost:9222
python -m prototype.stage2.main --capture-human-recording --template demo_query_entry --cdp-url http://localhost:9222
python -m prototype.stage2.main --template-revision-checklist --template demo_query_entry
python -m prototype.stage2.main --resume-human-takeover <run_dir> --cdp-url http://localhost:9222
python -m prototype.stage2.main --validate-connected-template <template_name> --cdp-url http://localhost:9222
python -m prototype.stage2.main --validate-template <template_name>
python -m prototype.stage2.main --validation-matrix --cdp-url http://localhost:9222
python -m prototype.stage2.main --run-sample --cdp-url http://localhost:9222
python -m prototype.stage2.main --platform-daily-report
.venv\Scripts\python.exe -m pytest prototype/stage2/tests/test_suyuan_shared_actions.py prototype/stage2/tests/test_suyuan_submit_dialog_actions.py prototype/stage2/tests/test_suyuan_registry_contract.py -q
.venv\Scripts\python.exe -m pytest prototype/stage2/tests/smoke_stage2_regressions.py prototype/stage2/tests/test_runtime_policy_gate.py -q
.venv\Scripts\python.exe -m pytest prototype/stage2/tests/test_g4_validation_matrix.py prototype/stage2/tests/test_generic_template_shared_verification.py -q
```

## 目录结构

```text
public/              Web 平台静态前端
src/server.js        Node.js 原生 HTTP API 和静态资源服务
src/storage.js       本地 JSON 数据存储
src/domain/          评测项目领域逻辑、Agent 编排和执行器抽象
src/stage2Dashboard.js 第二阶段 artifacts 聚合与运行中心概览 API
data/                运行时数据目录，首次启动自动创建
docs/                需求分析、头脑风暴和架构决策记录
prototype/stage2/    第二阶段 Python 执行子系统原型
tools/               原型脚本、探针和实战样本工具
artifacts/           运行产物、报告、截图和阶段性证据
```

## 团队测试手册

- [第二阶段新系统接入测试手册](docs/第二阶段新系统接入测试手册.md)

新系统首次接入建议按“两轮法”执行：

1. 先用 `--bootstrap-template` 生成最小模板骨架。
2. 再跑 `--live-discovery` 或 `--capture-human-recording` 收集页面入口、功能点和候选定位线索。
3. 再用 `--template-revision-checklist` 自动汇总建议回填项，生成按文件分组的修订清单。
4. 最后由测试人员按清单修订模板，再执行 `--validate-connected-template` 做深入连机验证。

## 技术路线

第一阶段遵循现有 ADR：

- 使用 Node.js 全栈路线
- 保留可配置 LLM Provider 抽象
- 以 Web UI 自动化评测为核心
- 在执行完成后沉淀自动化测试代码

当前 MVP 暂不依赖外部 npm 包，便于先跑通产品闭环。后续可逐步替换为 React/Vue 前端、NestJS/Fastify API、PostgreSQL、BullMQ、Playwright 和真实 LLM Provider。

## 第一版进度检查

检查日期：2026-06-15

结论：第一版已经完成到“可本地运行、可演示核心流程”的 MVP 状态；如果按正式验收评测平台的交付标准看，仍有真实自动化执行、证据文件、报告导出和人工审核编辑等能力需要补齐。

### 已完成

- Web 工作台已实现：可新建评测项目、录入甲方/乙方、被测系统、访问地址、测试账号、评测范围和需求资料。
- 本地数据存储已实现：使用 `data/store.json` 保存评测项目，首次运行自动创建数据目录。
- 后端 API 已实现：包含项目列表、项目创建、项目详情、项目删除，以及解析需求、生成用例、执行评测、生成报告四个流程动作。
- 需求解析已实现 MVP：基于本地启发式规则从需求文本中提取功能模块、功能点、验收项和测试场景。
- 测试用例生成已实现 MVP：可按功能点生成正常流程、异常流程、优先级、操作步骤、预期结果和验收项追踪关系。
- 执行结果已实现 MVP：可生成测试结论、执行摘要、证据摘要、缺陷清单和整改建议。
- 评测报告已实现 MVP：可展示结论、覆盖率、通过率、缺陷数量、人工确认项和风险说明。
- 自动化测试代码沉淀已实现 MVP：对明确通过的用例生成 Playwright 示例回归测试代码。
- 技术路线文档已补充：已有 `CONTEXT.md`、需求分析文档和 ADR，明确第一阶段以 Web UI 自动化评测为核心。

### 尚未完成

- 真实浏览器自动化尚未接入：当前 `src/domain/executors.js` 是模拟执行器，没有实际调用 Playwright 打开页面、定位元素、截图或生成 trace。
- 真实证据文件尚未生成：执行结果里有截图和 trace 路径摘要，但还没有实际落盘的图片、trace 包、页面快照或网络日志。
- 文档上传尚未实现：当前只能粘贴需求文本，还不支持 Word、PDF、Excel 等文件上传和解析。
- 用例人工审核能力不完整：前端可以查看用例，但还不能编辑、启用/禁用、调整优先级或修改步骤。
- 报告导出尚未实现：当前报告只在页面展示，尚不能导出 HTML、PDF、Word、Excel 或 JUnit XML。
- LLM Provider 仍是本地启发式实现：已预留抽象，但还没有接入真实大模型或本地模型服务。
- 凭据安全能力尚未实现：测试账号说明按普通文本存储，尚未做加密、脱敏或密钥托管。
- 自动化回归测试尚未建立：目前只有 `npm run check` 语法检查，没有 API、领域逻辑或端到端测试。

### 验证结果

已执行：

```powershell
npm run check
```

结果：通过。当前所有后端 JavaScript 文件语法检查通过。

### 下一步建议

1. 接入 Playwright，把模拟执行器替换为真实 Web UI 自动化执行器。
2. 增加实际证据产物落盘：截图、trace、页面快照和操作日志。
3. 补齐用例审核编辑能力，让测试人员能调整 AI 生成结果。
4. 增加报告导出能力，优先支持 HTML 或 Word/PDF。
5. 为项目创建、需求解析、用例生成和执行流程补充自动化测试。

## 第二阶段进度

检查日期：2026-06-22

阶段目标：在独立 Python 原型中完成“模型能力预检 + 两段式执行 + 失败归因循环 + 人工接管 + 报告沉淀”的最小平台闭环，并持续验证 Browser Use、Playwright 与本地/公网模型的真实兼容边界。

相关计划文档：

- [第二阶段原型开发计划](docs/第二阶段原型开发计划.md)

### 已完成

- 已新增第二阶段需求分析、技术方案与 ADR。
- 已实现模型能力预检脚本：`tools/probe_llm_capabilities.py`。
- 已建立第二阶段统一入口：`python -m prototype.stage2.main`。
- 已完成模板化原型骨架：`prototype/stage2/app/`、`prototype/stage2/templates/`、`artifacts/stage2/`。
- 已实现按模型输出的路由/策略摘要：`python -m prototype.stage2.main --routing-summary`，并在运行目录落盘 `routing_summary.json`、`discovery_strategy.json`。
- 已接通验证层共享抽象：`locator_hints` 解析链、共享规则评估器、共享验证结果路径和录制草稿 DSL 对齐已经纳入 stage2 执行主线。
- 已落地 6 个模板样本：
  - `suyuan_online_apply`
  - `suyuan_online_query_reset`
  - `suyuan_online_detail_view`
  - `lab_navigation`
  - `lab_query_filter`
  - `lab_create_add`
- 已把 `suyuan_online_apply` 的复杂真实流程拆成两组项目级复用动作族：
  - `prototype/stage2/app/verification/suyuan_shared_actions.py`
  - `prototype/stage2/app/verification/suyuan_submit_dialog_actions.py`
- 已补泛化回归护栏，覆盖：
  - 模板动作名到 registry 的 contract
  - 共享动作 handler 的 runtime 参数解析
  - 高风险提交的 policy bridge 不回退
  - 复杂 flow 的失败传播
  - `fill_success_template` 的输入契约与异常边界
- 已实现真实页面受控 discovery，可输出 `page_entries.json`、`feature_points.json`、`discovery_review_queue.json`、页面截图与进度事件。
- 已实现模板 bootstrap 入口：`--bootstrap-template`，可为新系统快速生成 `template.json`、`baseline.json`、`data_schema.json`、`locator_hints.json` 四件套的最小草稿，供第一轮 discovery / human recording 直接使用。
- 已补 discovery 审核回填最小闭环：支持在已完成 discovery 上加载 `discovery_review_patch.json`，对页面入口和功能点执行忽略、重命名与字段修正，并让后续 discovery / verification 优先消费人工回填后的结果。
- 已把 discovery 阶段显式化为策略决策：当前会在 `blocked`、`template_seed_only`、`live_enrich`、`skip_completed_discovery` 之间选择，并把决策纳入 run 产物与报告。
- 已实现验证阶段统一执行器，可输出执行日志、关键截图、失败簇、重试计划、运行报告、平台日报和模型对比结果。
- 已实现运行态进度视图产物：`progress_events.jsonl`、`current_status.json`、`phase_summary.json`。
- 已把 Node.js 主平台首页调整为运行中心视图：当前可通过 `GET /api/stage2/overview` 聚合 stage2 run、验证矩阵、平台日报、模型对比、人工录制候选审阅摘要、基线冻结清单，以及 `artifacts/stage2/sessions/` 下的 orchestration session 摘要，并展示选中 run 的阶段时间线、判停说明、人工接管摘要、沉淀候选审阅摘要和关键 artifacts 动作区；页面空闲时默认每 15 秒刷新。
- 已实现人工录制入口：`--capture-human-recording`，可输出 `recording_summary.json`、`key_screenshots.json`、`candidate_template_draft.json` 和 `candidate_template_review.json`；审阅包包含项目字段候选、alias 草案和待确认字段映射。
- 已实现人工接管恢复续跑入口：`--resume-human-takeover`，并在需要人工审核/接管时输出 `human_takeover.json`。
- 已补人工处理记录产物：运行中心或人工流程记录“已处理 / 可继续”时，会额外落盘 `human_takeover_resolution.json`；它只表示人工处理状态，不等价于自动判定问题已解决。
- 已实现 G4 骨架入口：`--validation-matrix`，可将 `lab_*` 本地模板族与 `suyuan_*` 样本放进同一套验证矩阵并输出 json / markdown 聚合结果；默认目标已包含 `suyuan_online_query_reset` 与 `suyuan_online_detail_view` 两个 connected 样本。
- 已完成多组对照验证：
  - 2026-06-16 的探针结果显示：`demo/.env` 指向的本地 `AI-tester` 对 `/chat/completions` 连通性不稳定，基础 chat、`json_object`、`json_schema`、tool calling 全部超时。
  - 2026-06-16 的探针结果显示：`demo/local_qwen.env` 指向的本地 `Qwen3.6-35B-A3B-UD-Q5_K_M-MTP` 对 `/chat/completions` 连通性不稳定，基础 chat、`json_object`、`json_schema`、forced tool calling、auto tool calling、Browser Use 封装路径全部超时或连接失败。
  - `demo/deepseek.env` 指向的公网 `deepseek-v4-flash-260425`，基础 chat 可用；raw HTTP 直接验证 `response_format.type=json_schema` 被服务端明确拒绝；原始 tool calling 两次探测结果不一致，需要继续观察。
  - `demo/deepseek-v4-flash.env` 指向的 DeepSeek 原厂 `deepseek-v4-flash`，基础 chat、`response_format.type=json_object`、自动 tool calling 可用；`response_format.type=json_schema` 被服务端明确拒绝；强制 `tool_choice` 被 thinking mode 拒绝。
  - 2026-06-19 的统一样本联调结果显示：在当时的本地环境下，`AI-tester` 已能完成 `suyuan_online_apply` 当前样本 run；同批次 `Qwen` 仍失败，说明“模型探针能力”与“真实样本任务完成率”需要分别记录，不能混为一个结论。
- 已确认 Browser Use 的 `ChatOpenAI` 结构化路径会强制使用 `response_format.type=json_schema`，在上述公网模型上会失败。
- 已确认 Browser Use 的 `ChatDeepSeek` 兼容路径在一次探测中可工作，但它走的是另一套协议，不应与 `ChatOpenAI` 的结构化输出能力混为一谈，也不应直接等同于稳定的原始 tool calling 能力。

### 当前结论

- 第二阶段原型启动前，模型能力预检必须是强制步骤。
- 任务路由不能只看“模型能否聊天”，必须显式区分：
  - 普通 chat 能力
  - tool calling 能力
  - `json_object` 能力
  - `json_schema` 能力
  - Browser Use 封装层兼容性
- 当前平台还要进一步区分“能力路由”和“发现策略”：
  - 能力路由决定 discovery / verification / reporting 三个阶段允许采用的模式
  - 发现策略决定本轮是阻断、仅模板播种、模板播种后 live enrich，还是复用上一轮 discovery 结果
- 对于当前探测到的 `deepseek-v4-flash-260425`，它不应进入依赖 `response_format.type=json_schema` 的 Browser Use 结构化输出路径。
- 对于当前探测到的原厂 `deepseek-v4-flash`，它也不应进入依赖 `response_format.type=json_schema` 或强制 `tool_choice` 的路径；可优先用于普通 chat、`json_object` 结构化输出和自动 tool calling 路径。
- 本地模型结论必须按日期区分：2026-06-16 的原始能力探针显示 `AI-tester` 与 Qwen 都不稳定；2026-06-19 的真实样本联调则表明 `AI-tester` 已能完成当前样本，而 Qwen 仍未稳定通过。因此，现阶段两者都还不能被当成“无条件稳定”的唯一主路径，但 `AI-tester` 已具备继续作为当前样本主模型打磨的价值。
- `--live-discovery` 现在会先遵守能力路由再决定是否执行 live enrich；即使路由可推荐 Browser Use 结构化 discovery，当前主线仍是“模板播种 + Playwright 受控 enrich”，不会把 Browser Use 伪装成 discovery 主执行器。
- 第二阶段平台闭环已经成立，但当前更像“可演示的原型平台”，还不是可广泛复用的正式平台。
- 自动续跑现在只会在 `next_round_decision.status=scheduled` 且 `should_start_next_round=true` 时继续；命中 `needs_review` 时会转入人工接管恢复路径，而不是盲目自动重试。
- 当前围绕“泛化闭环”的专项状态是：
  - G1 已完成：把定义了但没接上的共享抽象接通
  - G2 已完成：把复杂真实流程抽成复用动作族
  - G3 已完成：泛化回归测试护栏已落地并通过回归
  - G4 已启动：已建立跨模板族/跨系统样本的统一验证矩阵骨架，并补入同一真实系统内的第三个模板样本 `suyuan_online_detail_view` 作为接线与回归对象
- `fill_success_template` 仍是剩余项目级耦合点，但当前已经补上输入契约护栏；下一步应继续拆剩余耦合，再接入新的真实业务系统。
- `suyuan_online_detail_view` 当前状态应理解为“已接线并纳入回归与矩阵目标”，而不是“已完成新的 connected live 实证”；该样本的连机验证证据仍需在可用 CDP 会话下补齐。

### 验证产物

- 探测脚本输出目录：`artifacts/model_capability_probe/`
- 第二阶段统一产物目录：`artifacts/stage2/`
- 最新平台聚合产物：
  - `artifacts/stage2/latest_platform_daily_report.json`
  - `artifacts/stage2/latest_platform_daily_report.md`
  - `artifacts/stage2/latest_model_comparison.json`
  - `artifacts/stage2/latest_baseline_freeze_manifest.json`
  - `artifacts/stage2/validation_matrix/latest_validation_matrix.json`
  - `artifacts/stage2/validation_matrix/latest_validation_matrix.md`
- 人工录制候选审阅产物目录：
  - `artifacts/stage2/human_loop/<session_id>/candidate_template_draft.json`
  - `artifacts/stage2/human_loop/<session_id>/candidate_template_review.json`
- 公网模型探测结果：
  - `artifacts/model_capability_probe/20260616_185018_deepseek-v4-flash-260425.json`
  - `artifacts/model_capability_probe/20260616_185142_deepseek-v4-flash-260425.json`
  - `artifacts/model_capability_probe/20260616_203747_deepseek-v4-flash.json`
  - `artifacts/model_capability_probe/20260616_203831_deepseek-v4-flash.json`
- 本地模型探测结果：
  - `artifacts/model_capability_probe/20260616_184923_AI-tester.json`
  - `artifacts/model_capability_probe/20260616_213322_Qwen3.6-35B-A3B-UD-Q5_K_M-MTP.json`
