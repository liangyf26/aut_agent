# aut_agent Agent Guide

本仓库同时维护两条主线：

- 第一阶段：Node.js MVP，用于需求驱动的评测平台演示闭环
- 第二阶段：`prototype/stage2/` 下的 Python 执行子系统原型，用于验证 Web UI 发现、验证、循环归因、人工接管和报告沉淀

## 当前工作重心

当前真实开发重心是第二阶段 Python 原型。需要先读以下文档，再动代码：

1. `docs/需求分析第二阶段.md`
2. `docs/技术方案第二阶段.md`
3. `docs/第二阶段原型开发计划.md`
4. `CONTEXT.md`

当前围绕“泛化闭环”的专项执行计划采用四步：

- G1：把“定义了但没接上”的抽象层接通（已完成）
- G2：把复杂真实流程从“项目专用流”抽成“复用动作族”（已完成）
- G3：补泛化回归测试护栏（已完成）
- G4：跨模板族/跨系统样本验证矩阵已起步，下一步接入新的真实业务系统

执行节奏约束：

- G3 已完成，当前优先继续拆剩余项目耦合点，并把 G4 从“样本矩阵”推进到“新真实业务系统接入”
- 不要把 `fill_success_template` 误当成高于 G4 的独立优先级任务

## 关键目录

- `prototype/stage2/app/`: 第二阶段平台原型模块
- `prototype/stage2/templates/`: 项目级执行模板、基线、schema、locator hints
- `prototype/stage2/app/verification/suyuan_shared_actions.py`: 溯源样本的 wizard / drawer 共享动作族
- `prototype/stage2/app/verification/suyuan_submit_dialog_actions.py`: 溯源样本的 upload / submit dialog 共享动作族
- `src/stage2Dashboard.js`: Node.js 运行中心对 `artifacts/stage2/` 的聚合入口
- `tools/suyuan_submit_loop.py`: 溯源系统样本闭环与迭代编排脚本
- `prototype/stage2/tests/`: 第二阶段 smoke / regression 测试
- `artifacts/stage2/`: 第二阶段运行产物，属于证据层，不是源码层

## 第二阶段入口

统一 CLI 入口：

```powershell
python -m prototype.stage2.main
```

常用命令：

```powershell
python -m prototype.stage2.main --run-sample --cdp-url http://localhost:9222
python -m prototype.stage2.main --routing-summary --template suyuan_online_apply
python -m prototype.stage2.main --live-discovery --template suyuan_online_apply --model AI-tester --cdp-url http://localhost:9222
python -m prototype.stage2.main --capture-human-recording --template suyuan_online_apply --cdp-url http://localhost:9222
python -m prototype.stage2.main --platform-daily-report
python -m prototype.stage2.main --resume-human-takeover <run_dir> --cdp-url http://localhost:9222
python -m prototype.stage2.main --validation-matrix --cdp-url http://localhost:9222
```

## 事实约束

- 发现阶段允许受控 Browser Use / 页面理解；验证阶段默认由 Playwright 确定性执行
- discovery 现在分成两层判断：先做 capability routing，再做 discovery strategy；不要把“模型支持 Browser Use 结构化输出”误解为“主流程一定会跑 Browser Use discovery”
- 当前 live discovery 的真实执行边界仍是“模板播种 + Playwright 受控 enrich”；Browser Use readiness 目前主要作为路由提示，而不是 discovery 主执行器
- 高风险真实提交默认禁止，除非项目级白名单显式允许
- 运行态必须持续落盘结构化产物，至少包含进度事件、当前状态、页面入口、功能点、执行结果、失败簇、报告
- 初始化/运行阶段现在还会落盘 `routing_summary.json` 与 `discovery_strategy.json`，用于说明模型路由与本轮发现策略
- 人工录制会话当前除候选草稿外还会落盘 `candidate_template_review.json`；运行中心已消费其摘要和 artifact 链接
- 当前验证层已经形成三层结构：通用模板动作、项目级复用动作族、少量项目胶水；后续抽象优先继续拆剩余项目耦合点，并在 G4 骨架上接入新的真实业务系统
- 当前验证层已补 G3 护栏，并新增 G4 validation matrix 骨架：`lab_*` 本地模板族与 `suyuan_*` 样本可走进同一套统一汇总链路
- Node.js 主平台首页现在默认是运行中心优先布局；涉及平台 UI 时，应从 `public/index.html`、`public/app.js`、`src/stage2Dashboard.js` 理解当前外壳，而不是假设仍是表单优先首页。当前 overview 还会聚合 `latest_baseline_freeze_manifest.json` 与 run 级 `promotion_candidate_summary`
- 项目级沉淀可以自动落盘；平台级基线沉淀必须人工审核后晋升
- 生成的 `artifacts/`、日报、报告是证据，不是设计真相；设计真相以 `docs/` 和 `CONTEXT.md` 为准

## /neat 维护规则

- 更新文档时优先改现有条目，不要在顶部追加会话流水账
- 若第二阶段 CLI、产物名、里程碑状态变化，需同步更新 `README.md`、`docs/第二阶段原型开发计划.md`、`docs/技术方案第二阶段.md`
- 若新增长期稳定术语，补到 `CONTEXT.md`
