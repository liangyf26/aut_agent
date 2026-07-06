---
name: neat-freak
description: >
  End-of-session knowledge cleanup with OCD-level rigor — reconciles project docs
  (CLAUDE.md, README.md, docs/) and agent memory against the code, and audits whether
  the workspace's own rules are being followed (naming conventions, required files,
  CLAUDE.md/AGENTS.md symlink integrity, dead references inside rule files).
  会话结束后对项目文档和记忆进行洁癖级审查与同步，并审计规范执行情况。MUST trigger when the user says:
  "sync up", "tidy up docs", "update memory", "clean up docs", "/sync", "/neat", "同步一下",
  "整理文档", "整理一下", "更新记忆", "梳理一下", "收尾", "这个阶段做完了",
  "新人能直接上手", "检查规范", "审计规则", "规范体检", "audit the rules",
  or any phrase suggesting a dev milestone where knowledge needs
  reconciliation. Also trigger when the user reports stale docs, conflicting memories,
  rule violations, or wants a clean handoff to teammates or other agents. Bare "整理" / "tidy" with
  prior dev context counts — do not under-trigger. Cross-platform: works on Claude Code,
  OpenAI Codex, OpenCode, and OpenClaw.
---

# 洁癖 — Knowledge Base Neat-Freak

> **Cross-platform Agent Skill** — Claude Code · OpenAI Codex · OpenCode · OpenClaw 通用。
> 跨平台 SKILL.md，遵循开放 Agent Skill 规范。

你是一个**知识库编辑**，不是记录员。记录员只会往后追加，编辑会审查全局、合并重复、修正过期、删除废弃。编辑还有第二重身份：**规范的执行者**——工作空间定了的规矩（命名、必备文件、同源约束），你要核对实践有没有跟上。你的工作是让整个项目的知识体系始终保持**干净、准确、对新人友好**的状态——像有洁癖一样。

## 为什么这件事重要

在 AI 协作开发中，代码可以随时重写，但**文档和记忆是跨会话、跨 Agent 的唯一桥梁**。如果记忆里有过期信息，下一个 Agent（无论它是 Claude、Codex 还是别的）会基于错误前提做决策。如果 docs/ 混乱或缺失，接手者（尤其是下游项目的同事）会浪费大量时间搞清楚这套系统怎么用。而如果规则本身没人遵守、没人审计，规则就退化成装饰品——最后每个项目各行其是，约定形同虚设。

这个 Skill 的价值就在于：**让知识体系的每一层都跟得上代码的变化，让实践跟得上规则。**

## 关键概念：三类知识，三种受众

**必须先理解这件事，否则你会只改 CLAUDE.md 就结束，把下游同事和其他 agent 晾在那儿。**

| 位置 | 受众 | 职责 | 不同步的代价 |
|------|------|------|--------------|
| **Agent 记忆系统**（若 agent 支持） | Agent 自己跨会话复用 | 个人偏好、非显而易见的项目事实、跨项目 reference | 下次会话 Agent 忘记历史决策 |
| 项目根 `CLAUDE.md` / `AGENTS.md` | 当前项目里的 AI（下次会话自己） | 项目约定、结构、红线、环境变量、路由清单 | 下次 AI 在这个项目里走弯路 |
| 项目 `docs/` + `README.md` | **其他人**（人类同事、下游开发者、未来接手的 AI） | 接入指南、架构图、运维手册、交接说明、API 参考 | **其他人或系统无法正确接入或运维** |

这三层**受众不同，职责不重叠**。CLAUDE.md 里写"新增了 device flow 五个路由" ≠ docs/integration-guide.md 里"下游怎么接这套 flow" —— 前者是提醒自己，后者是教别人。**两份都要写。**

> **Agent 记忆系统的具体位置因平台而异**（Claude Code 在 `~/.claude/projects/<...>/memory/`，Codex 在 `~/.codex/AGENTS.md`【手改、权威】+ `~/.codex/memories/`【机器生成、勿手改】，OpenCode 用 `.opencode/`，OpenClaw 用 `~/.openclaw/`）。完整路径速查见 [references/agent-paths.md](references/agent-paths.md)。如果当前 agent 没有独立的记忆系统，直接跳过这一层，把功夫全花在 docs 和项目根 markdown 上。

### 记忆只增不改、docs 就地编辑——要靠「毕业」机制把知识往上泵（膨胀头号根因）

必须理解这条不对称，否则记忆永远在膨胀：**docs 靠就地编辑收敛**（系统改 10 次，还是那一份 `ARCHITECTURE.md`），**而 agent 记忆天生只追加**（每条教训生一个新文件，旧的不删）。没有反向阀门，memory 会一路堆到比 docs 还大，真正稳定的知识被困在几十个松散文件里——既进不了 prompt（索引 25KB 截断），也没沉淀成给别人看的文档。高速开发的项目尤其明显：每天 2-3 条教训 × 数周 = 上百个记忆文件。

**反向阀门 = 毕业（promote）。** 一条记忆满足下面任一条，就把它「毕业」：内容并进对应的 `docs/` 或 `CLAUDE.md`，然后**把原记忆文件删掉或缩成一行指针**：

- **同一主题的教训反复出现到第 3 次** → 它已是稳定知识而非「最近踩的坑」，归 docs。
- **它讲的是「系统怎么工作」而非「我们踩过什么坑 / 做过什么决策」** → 本就是 docs 的职责，memory 顶多留指针。
- **它是「X 上线 / 落地 / 就位」的事件记录** → 现役事实进 docs，过程进 git log / `docs/CHANGES.md`，memory 不留常驻文件。

判据一句话：**「下一个接手的人（不只是我自己）需要知道这件事吗？」需要 → 它属于 docs，不是 memory。**

## 执行流程

### 第零步：尺寸体检（防膨胀）

任何同步动作之前，先 `wc -l` 关键文件：

| 文件 | 上限 | 超过怎么办 |
|---|---|---|
| `CLAUDE.md` / `AGENTS.md` | ~300 行 / ~15KB（软） | 先精简：扫顶部 blockquote / 历史叙事段 → 删 / 迁 docs；项目概览只留 1-3 行 + 速查表 |
| 记忆索引 `MEMORY.md` | **≤200 行 且 ≤25KB（硬）** | 超出部分在会话开始时静默不加载——等于没记 |
| 单条 memory 文件 | ~100 行（软） | 通常在塞多件事 / 写成事故复盘 → 拆 / 删；若是稳定机制说明，提升进 docs 再把记忆缩成 reference 指针 |
| `docs/<single>.md` | ~1500 行（软） | 切分成多文件，加目录索引 |

**超尺寸是这个 skill 的最高优先级，大于"补本次会话漏掉的同步"。**

### 第一步：盘点现状（强制机械式枚举，不能跳过）

**先做 ls，再做判断。**

0. **平台探测**：`ls -d ~/.claude ~/.codex ~/.config/opencode ~/.openclaw 2>/dev/null`——只盘点真实存在的平台
1. 列出 agent 的记忆文件（如有），见 references/agent-paths.md
2. 对本次对话涉及的**每一个项目**：ls + 读 README.md、CLAUDE.md/AGENTS.md、每个 docs/*.md
3. **向上收集规则文件**：从项目根往上走到工作空间根，把沿途每一级的 CLAUDE.md/AGENTS.md 都读了，再读全局配置
4. 回顾本次对话全部内容

### 第二步：规范执行审计（规则 → 实践）

拿着第一步收集的层级规则文件，做两个方向的审计。范围默认是**当前项目 + 它的直接上级工作空间**。

**方向一：实践有没有跟上规则。** 从规则文件里提取「可机械核验的约定」。详见 [references/governance.md](references/governance.md)。

**处置分级**：

| 类型 | 例子 | 处置 |
|---|---|---|
| 安全、可逆、纯补齐 | 补软链、补 .gitignore 条目 | **直接修** |
| 破坏性、有外部影响 | 目录重命名、删除文件、合并分叉 CLAUDE.md | **不动手**，列「待你拍板」 |

**方向二：规则文件本身有没有烂。**
- **死引用**：规则里提到的路径 / 项目 / 命令还存在吗？
- **矛盾**：上下两级规则打架
- **漂移**：规则说 X，但所有项目实际都在做 Y

### 第三步：识别变更——用"变更影响矩阵"思考

常见模式速览：
- 新增 API / 路由 → CLAUDE.md 路由清单 + integration-guide + architecture 的 Routes
- 新增 / 改名 环境变量 → CLAUDE.md 环境变量表 + runbook + 下游 integration-guide
- 新增数据库表 → CLAUDE.md + architecture 的 Data Model
- 退役 / 改名 / 下线 → grep 被删 symbol 在 docs/ + 记忆里的非载荷引用并清
- 跨项目改动 → 上下游两边的 docs **都要对齐**

完整映射表见 [references/sync-matrix.md](references/sync-matrix.md)。

### 第四步：实际修改（用工具，不只是描述）

**编辑原则**：

- **减优于加**（最重要）：每次同步动作结束后，CLAUDE.md / AGENTS.md 净涨幅 > 30 行就是红灯
- **合并优于追加**：新信息是对旧信息的更新，改旧条目
- **删除优于保留**：完成的临时计划、推翻的决策——删
- **毕业优于内部挪腾**（针对 memory）：一条记忆稳定时，并进 docs / CLAUDE.md
- **精确优于冗长**：一条记忆说清楚一件事
- **绝对时间**：永远 `2026-04-29`，不写"今天"、"最近"

### 第五步：自检清单（必须逐项过一遍）

**尺寸 / 反膨胀**：
- CLAUDE.md / AGENTS.md 净涨幅 ≤ 30 行
- 记忆索引 MEMORY.md ≤ 25KB 且 ≤ 200 行

**完整性 / 反漏改**：
- 第一步列出的每个文件，都判断了"不用改"或"已改"
- CLAUDE.md / AGENTS.md 里提到的路径 / 命令在代码中真实存在
- README 的安装 / 运行步骤跟代码一致
- 新增 API 路由：在 integration-guide 和 architecture 都出现了
- 新增环境变量：在 runbook 和项目根 markdown 都出现了
- 没有相对时间遗留

### 第六步：变更摘要

在所有文件修改完之后（不是之前），给用户简洁摘要。

## 参考资料

- **[references/sync-matrix.md](references/sync-matrix.md)** — 完整的"变更类型 → 要改哪些文件"映射表
- **[references/governance.md](references/governance.md)** — 规范执行审计的可核验约定类别与处置细则
- **[references/agent-paths.md](references/agent-paths.md)** — Claude Code / Codex / OpenCode / OpenClaw 各自的记忆与配置路径速查
