# PR #634 合并冲突解决记录（2026-08-14）

> 场景：develop → master 的 PR #634，`mergeable=CONFLICTING`。分叉点 `0cf53264`，develop 独有 95 提交。
> 最终：merge commit `c9026686` 推送成功，PR 恢复 `MERGEABLE`。
> 配套脚本：`scripts/resolve_pr634_conflict.ps1`

---

## 一、冲突文件与解决策略（4 个）

merge 方向：`merge origin/master INTO develop`（本侧 ours=develop，对侧 theirs=master）。

| 冲突文件 | 策略 | 依据 |
|---|---|---|
| `tests/unit/test_planning_defect_d16.py` | `checkout --ours`（取 develop） | 我方任务 3/4 相关改动优先 |
| `tests/integration/test_orchestrator三层路由_e2e.py` | `checkout --ours`（取 develop） | 测试须与实现配套；取 master 版会因 `_interaction_lock` 与 develop 实现不匹配而 9 failed |
| `tests/conftest.py` | 手动合并：**保留 develop 强制复位**，删 master 快照/恢复 | 见下"场景推演" |
| `.pre-commit-config.yaml` | 手动合并：**两侧 hook 全保留** | 两侧 CI 防线都有效 |

### conftest.py 决策依据（logging.disable 泄漏治理）

master（+7 行）：快照 `_saved_manager_disable` 并在 yield 后恢复原值；develop（+35/-1）：同区域"强制复位 `manager.disable = NOTSET`"。

场景推演（快照恢复 vs 强制复位）：

| 场景 | master 快照/恢复 | develop 强制 NOTSET |
|---|---|---|
| A. 测试内 disable 且恢复 | ✅ | ✅ |
| B. 测试内 disable 未恢复（泄漏） | ✅（快照进入时值） | ✅ |
| **C. 前序测试已泄漏（进入 fixture 时 disable=50）** | ❌ 快照到 50 并恢复 50，**泄漏永远无法自愈** | ✅ 无条件清除 |

**结论**：快照方案存在场景 C 自愈缺失，保留 develop 强制复位（覆盖 A/B/C）。

---

## 二、执行流程（6 步）

1. **前置检查**：`git status --short` 核对未提交改动（并行会话改动不属本任务，保持不动）
2. **并行会话阻塞检测**：merge 前检查 `learning_budget.py / learning_metrics* / config.yaml / TASK-03_*` 是否 dirty（2026-08-14 实测 TASK-03 staged 改动阻断 merge，报 "local changes would be overwritten"）——**禁止 stash/checkout 这些文件**，等待并行会话提交收口
3. **同步 + 合并**：`git fetch origin master develop` → `git merge origin/master --no-commit`
   - merge 还会被 untracked CI 产物阻断（`knowledge_health_2026*.html/md`、`quality_gate_report.json`）：移开备份（勿删，可恢复）
4. **解决冲突**：2 个取侧 + 2 个手动（见上表）
5. **回归验证**：P0 清单通过后提交
6. **提交 + 推送**：`git push origin develop` → PR mergeable 自动更新

---

## 三、本场景特有障碍与处置

### 1. 共享 index 被并行会话实时竞争（最高风险）

- 症状：`git add` 后 index 混入并行会话文件（实测 73 个）；`git diff --cached` 随时可能被并发清空/污染
- 处置：**放弃在主工作区 commit**，改用 detached worktree 隔离提交：
  ```powershell
  git worktree add --detach "$env:TEMP\pr634_wt" HEAD
  # 在 tmp 内重新 merge → 复制主工作区已解决的 4 文件覆盖 → add + commit
  git merge --quit            # 清主工作区 MERGE_HEAD（不动工作区/index）
  git reset --soft <mergecommit>   # 快进 develop HEAD 到 merge commit
  git worktree remove --force "$env:TEMP\pr634_wt"
  ```
- 注意：worktree 间 index 虽独立（`$GIT_COMMON_DIR/index` 共享的是主 index；worktree 自带 index），但 **worktree 检出/checkout 类操作仍可能触及共享 refs**，隔离提交仍是首选

### 2. pre-commit 的 stash/restore 被并行会话 unstaged 改动阻断

- 症状：`error: tests/contract/contracts/*.json: patch does not apply`
- 根因：pre-commit 对 unstaged 改动做 patch 缓存（stash/restore），并行会话正在修改的文件无法打 patch
- 处置：并行会话活跃时不在主工作区 commit（转 worktree）；等待其提交收口后 unstaged 变干净

### 3. check-index-isolation hook 对 merge 场景误报

- 症状：merge commit 时 hook 报 "index 含运行时/敏感文件 `.env.corrupt_backup_20260810`"
- 根因：merge index 天然含从 master 历史带入的文件（`git ls-tree origin/master` 证实该文件在 master），hook 无法区分"merge 合法引入"与"并行会话 git add . 混入"
- 处置：**hook 官方逃生通道** `SKIP=check-index-isolation` 合法使用（其余 hook 照跑），commit message 注明原因

### 4. e2e 测试失败 ≠ merge 回归（环境噪音判定）

- 症状：`test_orchestrator三层路由_e2e.py` 9 failed，`AttributeError: 'Orchestrator' object has no attribute '_interaction_lock'`
- 根因：并行会话半成品——工作区 `orchestrator.py` 被改成 `_interaction_count`（unstaged），与 HEAD 版 e2e 不匹配
- 判定法：`checkout --ours` 后仍失败 = 环境不一致（并行会话半成品），非 merge 引入；P0/P1 其他清单全绿佐证

---

## 四、关键教训（防重演）

1. **merge 前先检测并行会话阻塞文件**（dirty 即等待收口），禁止 stash/checkout 并行会话工作（守不易）
2. **共享 index 竞争环境**（`git worktree list` 多 worktree）禁止主工作区 commit，detached worktree 隔离提交为最可靠方案
3. **untracked CI 产物阻断 merge**：移开备份而非删除；执行后必须核实状态（PowerShell 报错≠失败）
4. **测试与实现必须配套取侧**：e2e 等实现配套文件取本侧（develop），不能机械"取 master"
5. **hook 逃生通道按场景合法使用**：merge 场景的 index 语义与常规提交不同，误报时 SKIP 并注明原因
6. **判定回归失败须排除并行会话半成品**：`checkout --ours` 后仍失败 + 工作区文件与 HEAD 不一致 → 环境噪音，非产品缺陷

---

## 五、回归清单（冲突解决后）

| 优先级 | 测试 | 依据 |
|---|---|---|
| P0 | `test_planning_defect_d16.py` + `test_response_workflows.py` + `test_performance_monitor*.py` | 冲突文件 + conftest 泄漏治理域（实测 52/0） |
| P1 | `test_state_manager_comprehensive.py` + `test_singleton_manager.py` | reset_global_singletons 影响面（实测 89/0） |
| P1 | `test_orchestrator三层路由_e2e.py` | 冲突文件（取 develop；并行会话收口后须复验） |
| P2 | planning 单元全量 + pre-commit 自检 | 回归门禁 R1 |
