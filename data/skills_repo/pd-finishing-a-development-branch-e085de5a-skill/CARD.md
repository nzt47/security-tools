# finishing-a-development-branch

> LLM 整理，逐条经**原文引用校验**：verified 32 / 32（未通过的已丢弃）。

## 何时使用

- 实现完成、所有测试通过，且需要决定如何集成工作时使用。
- 通过提供合并、PR 或清理的结构化选项来引导开发收尾。

## 步骤

- 选项之一：在本地合并回基础分支。
- 选项之一：推送并创建 Pull Request。
- 选项之一：保持分支原样，稍后自行处理。
- 选项之一：丢弃这项工作。
- 选项之一：作为新分支推送并创建 Pull Request。
- 选项之一：保持原样，稍后自行处理。
- 选项之一：丢弃这项工作。
- 展示分支名称。
- 展示所有提交列表。
- 展示 worktree 路径。

## 参数

（原文没有这一节，或没有能通过引用校验的条目）

## 约束 / 反例

- 问题：合并了损坏的代码、创建了失败的 PR；修复：在提供选项前始终先验证测试。
- 修复方式：在提供选项前始终先验证测试。
- 问题：“接下来该做什么”含义模糊；修复：恰好提供 4 个结构化选项（detached HEAD 时为 3 个）。
- 修复：恰好提供 4 个结构化选项（detached HEAD 时为 3 个）。
- 问题：移除了用户进行 PR 迭代所需的 worktree；修复：仅对选项 1 和 4 执行清理。
- 修复：仅对选项 1 和 4 执行清理。
- 问题：git branch -d 因 worktree 仍引用该分支而失败；修复：先合并、移除 worktree，再删除分支。
- 修复：先合并、移除 worktree，再删除分支。
- 问题：当 CWD 位于待移除的 worktree 内时命令会静默失败；修复：执行 git worktree remove 前始终先 cd 到主仓库根目录。
- 修复：执行 git worktree remove 前始终先 cd 到主仓库根目录。
- 问题：移除 harness 创建的 worktree 会导致幻影状态；修复：只清理 .worktrees/、worktrees/ 或 ~/.config/superpowers/worktrees/ 下的 worktree。
- 修复：只清理 .worktrees/、worktrees/ 或 ~/.config/superpowers/worktrees/ 下的 worktree。
- 问题：意外删除工作；修复：要求输入“discard”进行确认。
- 修复：要求输入“discard”进行确认。
- 禁止：在测试失败的情况下继续。
- 禁止：未验证结果上的测试就合并。
- 禁止：未经确认就删除工作。
- 禁止：未经明确请求就强制推送。
- 禁止：在确认合并成功前移除 worktree。

## 示例

- 示例：步骤 1 为在本地合并回基础分支。
