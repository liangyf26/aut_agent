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

## 关键目录

- `prototype/stage2/app/`: 第二阶段平台原型模块
- `prototype/stage2/templates/`: 项目级执行模板、基线、schema、locator hints
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
python -m prototype.stage2.main --live-discovery --template suyuan_online_apply --cdp-url http://localhost:9222
python -m prototype.stage2.main --capture-human-recording --template suyuan_online_apply --cdp-url http://localhost:9222
python -m prototype.stage2.main --platform-daily-report
python -m prototype.stage2.main --resume-human-takeover <run_dir> --cdp-url http://localhost:9222
```

## 事实约束

- 发现阶段允许受控 Browser Use / 页面理解；验证阶段默认由 Playwright 确定性执行
- 高风险真实提交默认禁止，除非项目级白名单显式允许
- 运行态必须持续落盘结构化产物，至少包含进度事件、当前状态、页面入口、功能点、执行结果、失败簇、报告
- 项目级沉淀可以自动落盘；平台级基线沉淀必须人工审核后晋升
- 生成的 `artifacts/`、日报、报告是证据，不是设计真相；设计真相以 `docs/` 和 `CONTEXT.md` 为准

## /neat 维护规则

- 更新文档时优先改现有条目，不要在顶部追加会话流水账
- 若第二阶段 CLI、产物名、里程碑状态变化，需同步更新 `README.md`、`docs/第二阶段原型开发计划.md`、`docs/技术方案第二阶段.md`
- 若新增长期稳定术语，补到 `CONTEXT.md`
