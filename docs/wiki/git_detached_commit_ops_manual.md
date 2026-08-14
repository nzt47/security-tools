# Git Detached Worktree 提交操作手册

> 最后更新：2026-08-14
> 维护者：团队共享
> 适用场景：并行会话活跃时用 `git worktree add --detach` 隔离提交的**标准操作流程**
> 事故复盘与根因：见 [git_detached_commit_fix_wiki.md](git_detached_commit_fix_wiki.md)

---

## 一、何时使用本手册

主工作区存在**多个并行 worktree**（`git worktree list` 输出 ≥2 行）时，共享 index
有被并发写清空/混入的风险。此时提交必须隔离：在 detached worktree 内 add + commit。

**风险点：** detached worktree 提交只写对象库，不移动任何分支指针；若提交后忘记
并入分支，`git worktree remove` 后该提交成为悬空提交，分支上不可见、最终被 `git gc`
回收——**没有任何报错信号**。

---

## 二、标准操作流程（Step-by-Step）

### Step 0：预检

```powershell
# 确认并行会话活跃（≥2 行 → 走隔离提交流程）
git worktree list

# 创建工作区（一次性；路径自选）
git worktree add --detach C:/Temp/<wt> HEAD
```

### Step 1：detached worktree 内提交（隔离 index）

```powershell
git -C C:/Temp/<wt> add <精确路径列表>
git -C C:/Temp/<wt> commit -F <message_file>
```

> ⚠️ 只 add 本次任务的精确文件路径，**禁止 `git add -A`**（防并行会话文件混入）。

### Step 2：【必做】提交后立即验证可达性

```powershell
git branch --contains <commit>
```

- 期望输出：`* develop`（或目标分支）→ 提交已在分支上，跳至 Step 5
- **输出为空 → 悬空！** 必须执行 Step 3 并入分支，**先不要删 worktree**

### Step 3：并入目标分支（纯前进，不改写历史）

```powershell
git -C C:/Users/Administrator/agent checkout develop
git merge --ff-only <commit>
```

#### 3a. merge 被"本地改动"拒绝时的处理

主工作区文件内容与提交内容一致时（提交源通常就是主工作区）：

```powershell
git diff --stat -- <文件列表>   # ① 确认改动 == 提交内容（无信息损失）
git checkout -- <文件列表>      # ② 恢复 HEAD（信息无损失，merge 会写回提交内容）
git merge --ff-only <commit>    # ③ 重试
```

> ⚠️ 若 `git diff` 显示主工作区内容已被并行会话**覆盖回旧版**（与提交内容不一致），
> **不要** checkout 恢复主工作区——应以隔离 worktree 内文件为唯一提交源。

### Step 4：复核

```powershell
git log --oneline develop -2
git branch --contains <commit>      # 期望：* develop
git show --stat --oneline HEAD      # 核对 staged 集合与行数完整
```

### Step 5：清理 worktree（最后一步）

```powershell
git worktree remove C:/Temp/<wt> --force
```

---

## 三、Checklist（提交后逐项打勾）

- [ ] `git branch --contains <commit>` 输出了目标分支（非空）
- [ ] `git merge --ff-only` 成功（未用 `reset --hard`）
- [ ] `git show --stat HEAD` 文件集合与预期一致（防并行会话混入）
- [ ] 关键标记已在**隔离 worktree 内**验证（主工作区验证会被实时覆盖）
- [ ] worktree 清理在并入分支**之后**执行

---

## 四、快速判定表

| 现象 | 判定 | 处置 |
|---|---|---|
| `git branch --contains <commit>` 有输出 | 正常 | 继续 Step 4/5 |
| 输出为空，但 `git status` 干净 | **悬空提交**（无报错信号） | 立即 Step 3 并入 |
| merge 拒绝"本地改动将被覆盖" | 主工作区与提交内容一致 | Step 3a ①→②→③ |
| `git diff` 显示主工作区内容已回滚 | 并行会话覆盖 | 以 worktree 内文件为提交源 |
| 悬空提交内容已被更全版本收录 | 无需人工处理 | 随 `git gc` 回收即可 |

---

## 五、守【不易】原则

1. 提交并入**只用 `git merge --ff-only`**，禁止 `reset --hard` 改写历史；
2. 确认目标分支未被并行会话推进，否则先 rebase 再并入；
3. 悬空提交对象仍在（reflog/gc 保护期），**不要急于 gc**，先判断内容是否已被收录。
