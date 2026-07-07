---
name: github-ops
description: GitHub 操作指南：git commit/push/pull/branch、gh CLI 管理 Issues/PR、triage 标签与认证配置
---

# GitHub 操作技能

本仓库 GitHub 操作指南，涵盖 git 命令、`gh` CLI、Issues/PR 管理与认证配置。

## 仓库信息

- **远程仓库：** `https://github.com/liangyf26/aut_agent.git`（origin）
- **默认分支：** `main`
- **GitHub 用户：** liangyf26
- **仓库：** `liangyf26/aut_agent`

## Git 基本操作

### 提交与推送

```bash
# 查看状态
git status

# 添加文件
git add <file>          # 单个文件
git add -A              # 所有变更

# 提交
git commit -m "消息"

# 推送到远程
git push origin main
```

### 分支操作

```bash
# 查看分支
git branch -a

# 创建并切换分支
git checkout -b <branch-name>

# 切换到已有分支
git checkout <branch-name>
```

### 合并与变基

```bash
# 合并远程最新到本地
git pull origin main

# 合并其他分支到当前分支
git merge <branch-name>
```

### 查看历史

```bash
git log --oneline -20
git diff                  # 未暂存的变更
git diff --staged         # 已暂存的变更
```

## GitHub Issues（通过 gh CLI）

### 创建 Issue

```bash
gh issue create --title "标题" --body "正文"
# 多行正文用 heredoc：
gh issue create --title "标题" --body "$(cat <<'EOF'
正文内容
可以多行
EOF
)"
```

### 查看 Issue

```bash
gh issue view <编号> --comments
```

### 列出 Issues

```bash
# 列出所有打开的 issue（带标签和评论）
gh issue list --state open --json number,title,body,labels,comments \
  --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'

# 按标签过滤
gh issue list --label "needs-triage" --state open
```

### 操作 Issue

```bash
# 评论
gh issue comment <编号> --body "评论内容"

# 添加/移除标签
gh issue edit <编号> --add-label "ready-for-agent"
gh issue edit <编号> --remove-label "needs-triage"

# 关闭
gh issue close <编号> --comment "关闭原因"
```

## GitHub PR（通过 gh CLI）

```bash
# 列出 PR
gh pr list --repo liangyf26/aut_agent

# 查看 PR
gh pr view <编号>

# 创建 PR
gh pr create --title "标题" --body "正文" --base main
```

> **注意：** PR 不作为常规 triage 请求面。Issue 和 PR 共用编号空间，`#42` 可能指向两者；先用 `gh pr view 42` 试，失败则 fallback 到 `gh issue view 42`。

## Triage 标签

| 标签 | 含义 |
|------|------|
| `needs-triage` | 维护者需评估 |
| `needs-info` | 等待报告者补充信息 |
| `ready-for-agent` | 已充分描述，可由 agent 处理 |
| `ready-for-human` | 需人工实现 |
| `wontfix` | 不予处理 |

## 认证

- 仓库已配置 `credential.helper store`
- `gh` CLI 已认证
- 如遇认证失败，检查 Token：`cat /opt/data/.github_pat`

## 安全注意事项

- 推送前务必确认分支正确（默认 main）
- 不要在提交中包含密钥/Token
- 高危操作（force push、删除远程分支）需人工确认后执行
- 对 Issues 的标签变更和关闭操作需先读懂上下文再执行
