# Agent 记忆与配置路径速查

不同 agent 平台的记忆系统和项目配置文件位置不一样。

## Claude Code

| 用途 | 路径 |
|---|---|
| 跨会话记忆(全局) | `~/.claude/projects/<encoded-project-path>/memory/` |
| 记忆索引文件 | `~/.claude/projects/<...>/memory/MEMORY.md` |
| 全局指令 | `~/.claude/CLAUDE.md` |
| 项目级指令 | 项目根 `CLAUDE.md`(可层级嵌套) |
| Skills 目录 | `~/.claude/skills/<name>/SKILL.md` |

记忆文件用 YAML frontmatter:`name`、`description`、`type`(user / feedback / project / reference)。

## OpenAI Codex

| 用途 | 路径 |
|---|---|
| 跨会话指令(全局，手改、权威) | `~/.codex/AGENTS.md` 或 `$CODEX_HOME/AGENTS.md` |
| 项目级指令 | 项目根 `AGENTS.md`(可层级嵌套；常软链到 `CLAUDE.md`) |
| 项目级 override | `AGENTS.override.md`(若存在,覆盖同目录 AGENTS.md) |
| 自动记忆库(机器生成) | `~/.codex/memories/`(git 仓)：`MEMORY.md` 索引 + `memory_summary.md` + `raw_memories.md` + `rollout_summaries/` |
| 全局 Skills | `~/.codex/skills/<name>/SKILL.md` |
| 项目内 Skills | 项目内 `.codex/skills/<name>/` |

**注意**：`~/.codex/memories/` 里的 rollout 派生文件（MEMORY.md / raw_memories.md / memory_summary.md）**不要手改**——它们是「某次会话里做过 X」的历史记录，会按 rollout 重新生成。把功夫花在 AGENTS.md。

## OpenClaw

| 用途 | 路径 |
|---|---|
| 用户级 skills | `~/.openclaw/skills/<name>/SKILL.md` |
| 项目级 skills | `.openclaw/skills/<name>/SKILL.md` |
| Workspace skills | 当前 workspace 的 `skills/` 目录 |

OpenClaw 没有独立的"记忆文件 + 索引"机制，跨会话信息可放在项目根的 markdown 里。

## OpenCode

| 用途 | 路径 |
|---|---|
| 全局配置 | `~/.config/opencode/` |
| 项目配置 | `.opencode/` |
| Skills 目录(项目) | `.opencode/skills/`、`.claude/skills/`、`.codex/skills/` 都会被扫描 |
| Skills 目录(全局) | `~/.config/opencode/skills/`、`~/.claude/skills/`、`~/.codex/skills/` |

OpenCode 同时读取 Claude Code 和 Codex 的目录,所以同一个 skill 装在 `~/.claude/skills/` 下的话三家都能识别。

## 如果当前 agent 没有独立记忆系统

跳过"记忆"那一层,把功夫全花在:
- 项目根 markdown(CLAUDE.md / AGENTS.md / 本平台等价文件)
- README.md
- docs/

## 跨平台共存策略

- **`CLAUDE.md` 是真身,`AGENTS.md` 是指向它的软链**,永远只编辑 CLAUDE.md
- **绝不允许两份独立维护**——发现两份内容不一致的独立文件,按处置分级走
- docs/ 和 README 是平台中立的,不需要分两份
