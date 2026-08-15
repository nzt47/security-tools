# PR #634 并行冲突根因与解决复盘

> 背景：PR #634（develop → master）显示 `mergeable=CONFLICTING` / `mergeState=DIRTY`。
> 本文档记录冲突定位、根因分析、解决过程与经验教训（2026-08-15）。

---

## 1. 冲突事实

| 项 | 值 |
|---|---|
| PR #634 | base=`master`（846c241d）→ head=`develop`（ae78f42b） |
| changedFiles | 409（仅 3 个文件冲突，其余均可自动合并） |
| merge-base | 250ef566（08-13 05:19） |

### 冲突文件清单

| 文件 | 冲突类型 | 两侧改动规模 |
|---|---|---|
| `planning/react.py` | content | master +130 / develop +285/-2 |
| `planning/reflector.py` | content | master +244 / develop +272/-3 |
| `tests/unit/test_planning_failure_reflect.py` | add/add | 两侧各新建 70 行同名文件 |

## 2. 根因：任务4/阶段5 功能被两侧平行实现

merge-base 之后，**master 与 develop 各自提交了一整套「任务4 失败反思闭环」功能**，功能点重叠但实现代码不同，git 无法自动判定以哪侧为准：

| 功能点 | master 链 | develop 链 |
|---|---|---|
| 失败路径反思闭环 | 147af2b7（**08-14 12:45:24**） | d34bc708（**08-14 12:45:24，与 master 秒级同刻**） |
| 追踪/分步/链路日志 | bdc7a35a / 88203ebd / bc461302 | ff37f84d / 4c575d82 / b8aaa002 |
| 反思路径统计 + 兜底细分 | 2e85634a / 846c241d | a354fe1b / f712e1c4 |
| 记账 `_bill_llm` | 5c323879（hasattr 防御式） | 完整实现 + budget_manager |

关键证据：
- 两侧起点提交**时间戳完全相同（08-14 12:45:24，秒级一致）**，哈希不同 → 平行会话/移植产生的重复实现。
- 同名函数：react.py 的 `_failure_reflect` / `_failure_history` / `_project_context_summary`；
  reflector.py 的 `failure_reflect` / `_run_failure_llm` / `_rule_based_failure_reflect` / `_persist_failure_lesson`。

## 3. 超集判定：develop 为功能完整侧

| 文件 | 差异结论 |
|---|---|
| reflector.py | develop 完整实现 `_bill_llm(prompt, response, budget_manager=None)`；master 仅 `getattr(self, "_bill_llm", None)` 防御占位（提交 5c323879 自述"master 无 budget_manager 基础"） |
| react.py | develop 在 master 基础上多 162 行统计逻辑 |
| 测试文件 | develop = master + 2 用例（`test_same_root_cause_reflection_limited_by_retries`、`test_second_round_prompt_contains_failure_history`） |

### `_bill_llm` 的 budget_manager=None 防御检查（合并前确认）

develop 侧实现：

```python
def _bill_llm(self, prompt: Any, response: Any, budget_manager=None) -> None:
    """TD-4：LLM 反思成本记账（调用方显式实例 > 默认实例；均未注入则跳过）"""
    bm = budget_manager or self.budget_manager
    if bm is None:
        return
    bm.record_text(prompt)
    bm.record_text(response)
```

- 3 个调用点（`step_reflect` / `plan_reflect` 显式传参、`failure_reflect` 隐式走 `self.budget_manager`）均安全。
- master 的 hasattr 防御（防"方法不存在"）由 develop 的"方法恒存在 + bm None 早退"覆盖，无需保留 master 版本。

## 4. 解决过程（隔离 worktree，未污染主仓库）

1. **非破坏性定位**：`git merge-tree --write-tree origin/master origin/develop` 输出 3 个冲突文件及其 stage blob。
2. **超集验证**：blob 间 `git diff` 对比两侧新增 def / 测试差异，确认 develop 为超集。
3. **worktree 合并**：
   ```bash
   git worktree add C:\Windows\Temp\pr634_merge origin/develop -b merge-634-conflict
   cd C:\Windows\Temp\pr634_merge && git merge origin/master
   git checkout --ours planning/react.py planning/reflector.py tests/unit/test_planning_failure_reflect.py
   git add . && git commit -m "merge: origin/master 合入 develop，解决任务4失败反思平行实现冲突"
   ```
   `--ours` = develop（HEAD 侧）版本。pre-commit 全钩子通过。
4. **测试验证**：
   - planning 4 文件：**61 passed / 1 skipped**（skip 为 `--runslow` 慢速标记，非失败）
   - 守卫 `tests/regression/test_pr634_ci_fixes.py`：**28 passed**（L3 修复未受影响）
5. **同步最新 develop 并推送**：origin/develop 前进 1 个 docs 提交（2052ba34）期间，`git checkout -b final origin/develop && git merge merge-634-conflict`（干净合并）→ `git push origin final:develop` → develop 更新至 3c62b029。
6. **PR 状态**：`mergeable=CONFLICTING → MERGEABLE`，`mergeStateStatus=UNSTABLE`（剩余为 CI 检查状态，冲突已清除）。

## 5. 经验教训

1. **功能唯一来源 = develop**：master 侧规划功能均为从 develop 移植的重复实现，合并时直接以 develop 为准；此类"移植"应记录在协调清单，避免重复开发。
2. **同刻提交是平行实现的强信号**：两侧起点提交时间戳完全一致（秒级）时，优先怀疑重复实现而非内容分歧。
3. **`git merge-tree --write-tree` 是非破坏性冲突分析的利器**：不触碰工作区即可拿到冲突文件清单与 stage blob，可进一步做 blob 级 diff 判定超集。
4. **记账类调用统一"None 早退"防御**：`budget_manager or self.budget_manager; if bm is None: return` 比 `getattr` 防御更完整（同时覆盖"对象缺失"与"参数缺失"两类场景）。
