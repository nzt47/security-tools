# Git Detached Worktree 提交悬空修复 Wiki

> 最后更新：2026-08-14
> 维护者：团队共享
> 关联提交：`63644492`（事故悬空提交）、`5721b304`（TASK-05 完整收录修复）、`47f5ab20`（日志/文档提交修复）

---

## 概述

并行会话活跃时，主工作区共享 index 有被并发写清空/混入的风险，因此采用 `git worktree add --detach` 隔离提交。但 **detached worktree 提交只写入对象库，不会移动任何分支指针**；worktree 删除后若无分支引用该提交，它将成为悬空提交（dangling commit），在 `git gc` 前不可达、在任何分支上都不存在。

本页记录该问题的完整现象、根因、标准修复流程与预防检查清单，避免团队重复踩坑。

| 项 | 值 |
| --- | --- |
| 事故提交 | `63644492`（detached 提交后未并入分支，`git branch --contains` 无输出） |
| 受影响文件 | `agent/skills_mgmt/feedback_agent.py`、`scripts/demo_feedback_agent.py`（实际未进入 develop） |
| 修复提交 | `5721b304`（13 文件完整收录 + ff 并入 develop）、`47f5ab20`（日志/文档，ff 并入） |
| 影响 | 若未发现，TASK-05 核心模块在 develop 缺失，依赖模块 import 失败 |

---

## 事故复现（2026-08-14）

1. 并行会话活跃（`git worktree list` 多个 worktree），共享 index 有并发写风险；
2. 按惯例 `git worktree add --detach C:/Temp/xxx HEAD` 隔离 index；
3. 在 worktree 内 `git add <精确路径> && git commit`（pre-commit 10 hooks 全 Passed）；
4. `git worktree remove C:/Temp/xxx --force` 删除 worktree；
5. **未将提交并入任何分支** —— 提交成为悬空提交；
6. 后续 `git branch --contains 63644492` **无任何输出**，develop 仍停留在提交前位置，新文件从未进入 develop。

### 根因

- detached worktree 的 HEAD 是 detached（无分支），commit 只写对象库；
- `git worktree remove` 只删工作树副本与索引，**不创建/移动分支引用**；
- 无引用 → 悬空 → 分支上不可见（`git log <branch> -- <file>` 为空），但对象仍在（reflog/gc 保护期）。

### 影响

- 代码"看似已提交"，实际不在任何分支：`git status` 干净、worktree 已清理，**没有任何报错信号**；
- 后续基于 develop 的代码若依赖该提交内容，构建/import 直接失败；
- 悬空提交最终被 `git gc` 回收，改动彻底丢失。

---

## 标准修复流程（detached 提交后必须执行）

```powershell
# 1. detached worktree 内提交（隔离 index，守并行会话安全）
git -C C:/Temp/<wt> add <精确路径列表>
git -C C:/Temp/<wt> commit -F <message_file>

# 2. 【必做】提交后立即验证可达性 —— 若此处无分支输出，立即并入
git branch --contains <commit>          # 期望：* develop（或目标分支）
# 若为空 → 悬空！必须并入分支：

# 3. 并入目标分支（纯前进 = fast-forward）
git -C C:/Users/Administrator/agent checkout develop
# 3a. 主工作区对应文件有本地改动时会拒绝 merge：
#     先 git diff 确认改动内容 == 提交内容（无信息损失）后 checkout 恢复，
#     merge 会把提交内容写回工作树
git checkout -- <本次提交的文件列表>
git merge --ff-only <commit>

# 4. 复核
git log --oneline develop -2
git branch --contains <commit>          # 期望：* develop
git show --stat --oneline HEAD          # 核对 staged 集合完整

# 5. 最后才清理 worktree
git worktree remove C:/Temp/<wt> --force
```

### merge 被"本地改动"拒绝时的处理

merge 会拒绝"将被覆盖的本地改动"。若主工作区文件内容与提交内容一致（通常如此，因为提交源就是主工作区），流程为：

```powershell
git diff --stat -- <文件列表>      # 确认改动 == 提交内容（78 insertions/4 deletions 一致等）
git checkout -- <文件列表>         # 恢复到 HEAD（信息无损失）
git merge --ff-only <commit>       # 成功，工作树自动更新为提交内容
```

---

## 预防检查清单（守【不易】）

1. **detached worktree 提交后，`git worktree remove` 之前**，必须 `git branch --contains <commit>` 验证可达；为空则先并入分支；
2. 提交并入采用 `git merge --ff-only`（纯前进），不用 `reset --hard` 改写历史；确认目标分支未被人为推进，否则先 rebase；
3. 提交后 `git show --stat` 核对文件集合与行数，防并行会话混入；
4. 主工作区被并行会话覆盖时（`git status` 显示 M 但内容已回滚），以隔离 worktree 内文件为提交源，提交前在 **worktree 内** 验证关键标记（主工作区验证会被实时覆盖）；
5. 悬空提交（`git branch --contains` 空）内容若已被更全版本覆盖收录，可随 `git gc` 回收，无需人工处理。

---

## 关联文档

- 变更说明：`docs/zh/智能体学习机制重构计划/变更说明/TASK-05_变更说明.md` §8 提交记录与 detached 悬空提交修复
- 项目记忆：2026-08-14「提交被并行会话混入事故」与「TASK-05 完整交付 ff 并入」
