# 标准作业程序（SOP）：Git 合并、归档与清理

> 版本：1.0 ｜ 日期：2026-08-09 ｜ 依据：SingletonManager 迁移项目收尾实战（已验证）
> 适用范围：本地 develop 分支的分叉合并、文档归档推送、临时分支/stash/worktree 清理

---

## 一、目的

在多人协作 + 多工作线并行的仓库中，安全地完成「分叉合并 → 文档归档 → 临时对象清理」，确保：

1. 归档内容（commit）不丢失、完整落入远程历史。
2. 只删除已合并、无内容丢失风险的临时分支。
3. 不触碰并行工作线的未提交内容与活动分支。

## 二、前置检查

在任何写操作之前，先确认仓库现状：

```powershell
git status -sb            # 当前分支、与 upstream 的 ahead/behind、工作区状态
git stash list            # 是否有 stash 残留
git worktree list         # 是否有 linked worktree 占用分支
git branch -v             # 各分支 tip
git fetch --all --prune   # 拉取所有远程最新状态（含 gitee 等第二远程）
git remote -v             # 确认远程列表（多远程时勿遗漏）
```

**关键点**：先 fetch 再判断，避免基于过期 ref 做删除决策。

## 三、操作步骤

### A. 文档归档（commit + push）

1. 仅暂存本次归档文件，**禁用** `git add -A` / `git add .`（防混入并行工作线文件）：

   ```powershell
   git add docs/Your_Doc.md
   ```

2. 检查暂存内容：

   ```powershell
   git status -sb
   git diff --cached --stat
   ```

3. 提交（遵循项目 commit 惯例：`docs(singleton): ...`）：

   ```powershell
   git commit -m "docs(singleton): 归档..." 
   ```

4. 推送前复核本地与远程是否同步（并行环境随时可能被他人更新）：

   ```powershell
   git status -sb   # 确认 develop...origin/develop 无 ahead/behind
   git push origin develop
   ```

### B. 分叉合并（本地独有 commit + 远程新 commit）

1. 保护未提交的现场文件（rebase 需要干净工作区）：

   ```powershell
   git stash
   ```

2. 以 rebase 方式合并分叉（保留本地 commit、线性历史）：

   ```powershell
   git pull --rebase origin develop
   ```

3. 若出现冲突：解决后 `git add <file>` → `git rebase --continue`。

4. 推送：

   ```powershell
   git push origin develop
   ```

5. 恢复现场：

   ```powershell
   git stash pop
   ```

### C. 清理临时对象

1. **Stash**：`git stash list` 确认无残留；有残留且确认无用才 `git stash drop`（谨慎）。

2. **识别可删分支**（仅删已合并 + 临时性分支）：

   ```powershell
   git branch --merged develop   # 已合并 → 候选可删
   git branch --no-merged develop  # 未合并 → 保留
   ```

   删除用**安全模式** `git branch -d`（git 会再次验证 fully merged，未合并自动拒绝）。

3. **worktree 占用处理**：若删除被拒且提示 worktree 占用，先移除对应 worktree：

   ```powershell
   git worktree list                        # 找到占用分支的 worktree 路径
   git worktree remove <worktree路径>       # 一次只能移除一个；dirty 会拒绝
   git branch -d <分支>
   ```

   `worktree remove` 拒绝时，先确认 worktree 内是否有未提交内容，勿直接 `-f`。

### D. 最终验证

```powershell
git status -sb                  # develop == origin/develop
git branch -v                   # 剩余分支列表符合预期
git stash list                  # 空
git worktree list               # 仅保留必要的 worktree
git log --oneline -3            # 归档 commit 在历史中
```

## 四、安全检查清单（红线）

| 红线 | 处理 |
|------|------|
| 不用 `-D` 强制删除未合并分支 | `-d` 拒绝即保留，分支内容可能仍有价值（如并行工作线中间产物） |
| 不 `git add -A` 归档 | 并行环境工作区混杂多工作线文件，必须按文件名精确暂存 |
| 不触碰其他分支/远程 | 双远程（origin/gitee）不同步属团队策略，不擅自同步 |
| 不做超出任务范围的变更 | 并行工作线的 M/?? 文件一律不动 |
| worktree remove 不加 `-f` | 先排查 dirty 原因，确认无价值内容后再考虑强制 |

## 五、常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| `git branch -d` 报 not fully merged | 分支 tip 含未合入 develop 的 commit | 保留；确认为临时且无价值才 `-D`（须人工确认） |
| `cannot delete branch used by worktree` | 分支正被 linked worktree 检出 | `git worktree list` → `git worktree remove <path>` 后重试 |
| `git branch --merged` 与 `-d` 判定不一致 | 分支 ref 在检查间隙被并行进程移动 | 以 `git rev-parse` + `git log` 复核真实 tip 后再决定 |
| push 被拒（non-fast-forward） | 远程被他人更新 | `git pull --rebase` 后重推，勿 `--force` |
| 归档 commit 不在 tip | 并行工作线在其后新增 commit | 正常现象，commit 仍在历史中，无需处理 |
| 第二远程（gitee）分支落后 | 双远程不同步 | 报告状态即可，同步属团队决策 |

## 六、经验要点（本次实战沉淀）

1. **先 fetch 再判断**：所有删除/合并决策必须基于最新远程状态。
2. **stash 是 rebase 的保险**：`pull --rebase` 前必须 stash，成功后立即 pop，防止现场丢失。
3. **`git branch -d` 是免费的安全网**：git 内部会再验证一次 fully merged，比人工判断可靠。
4. **worktree 分支不可直接删**：先 remove worktree，且 remove 不接受 dirty 工作区。
5. **并行工作线是常态**：同一仓库可能同时有多个会话活动，所有写操作前复核 ref 状态。
