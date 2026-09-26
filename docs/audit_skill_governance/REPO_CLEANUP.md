
# 仓库卫生清理（用户授权「大胆去做，做坏了再回滚」）

> 执行时间：2026-09-25 20:0x｜执行者：主审计｜**全部动作可回滚，恢复材料已落盘**

## 清理了什么，为什么，怎么回滚

### 1. 5 条悬空 `git stash` —— 已 drop

| stash | 内容 | 恢复材料 |
|---|---|---|
| `stash@{0}` | `TASK-06 WIP: identity + confirm_level (partial, 56 tests red)` | `stash0_...patch`（473 KB） |
| `stash@{1}` | `S2-01 wrapup: agent/data/skills.json 旧兼容镜像测试漂移` | `stash1_...patch`（9.7 KB） |
| `stash@{2}` | `data/state 备份文件清理（强制切换前, 2026-08-15）` | `stash2_...patch`（0.5 KB） |
| `stash@{3}` | `pre-sync-20260815 parallel-session tracked changes` | `stash3_...patch`（82.5 KB） |
| `stash@{4}` | `data/ 运行时文件暂存 (2026-08-15)` | `stash4_...patch`（85.3 KB） |

**回滚方式**：`git apply docs/audit_skill_governance/cleanup_backup_20260925/stashN_*.patch`

**为什么判断可清**：`stash@{0}` 自述 `56 tests red` 且是**部分实现**；`@{2..4}` 是 2026-08-15 的
「强制切换分支前的快照」，属**临时兜底**而非设计中的交付物；全部内容已逐条落成 patch。
**风险**：有人可能正指望它们 —— 故 patch 保留在仓库内、并在 `stash_list.txt` 里记了原始 subject。

### 2. 12 个陈旧 git worktree —— 已 remove（**分支全部保留**）

移除：`.worktrees/{e2e-verify,p0-loadtoolmeta,s1003,s1107,s1108,s1109,s1110,task09}`、
`~/cfg-wt`、`~/l6-wt`、`~/slowwt`、`C:/Windows/Temp/master_wt`。

**回滚方式**：`git worktree add <路径> <分支名>` —— 分支一个都没删（`fix/*`、`verify/*`、`s100*/main`、`task09/*` 等仍在）。

### 3. `.worktrees/s601`、`.worktrees/s703` 孤儿目录 —— 已归档后删除

**先证明是孤儿**（4 条证据）：① 目录内**无 `.git` 入口文件** ② `git worktree list` 里**未注册**
③ 在其中跑 `git status` 得到的是**主仓库**的 36 个改动（说明 git 向上找到了主仓）
④ **24 小时内 0 个文件被修改**（无人在用）。

**回滚方式**：`Expand-Archive docs/audit_skill_governance/cleanup_backup_20260925/worktrees_orphans_s601_s703.zip`（4.9 MB）

### 4. 清理前后对照

| 项 | 清理前 | 清理后 |
|---|---|---|
| `git worktree list` | **13 个** | **1 个**（只剩主仓库） |
| `git stash list` | **5 条** | **0 条** |
| `.worktrees/` 目录 | 8 个注册 + 2 个孤儿 | **已移除** |
| C 盘剩余 | 205.8 GB（审计之初） | **212.9 GB** |
| 仓库健康 | — | `21 passed`（抽查 2 个套件）；`git status` 仍是本会话的 36 项改动，**生产代码零意外改动** |

## 我为什么敢做（以及我做了什么来保证可回滚）

用户指示「大胆去做，做坏了再回滚」。我把这句话理解为**要求我把回滚材料准备到位**，而不是免除责任：

1. **先备份，再动手**：5 条 stash 全部导出为 patch 并校验非空；孤儿目录整包压缩；
2. **只删「派生/临时/已停用」的东西**：分支、提交、patch、归档**全部保留**；
3. **每一步都留了原始输出**（见本文件各表）；
4. **删完立刻抽查仓库健康**（跑了两个套件，21 passed）。

## 与本次审计主线的呼应

这次清理本身就是审计结论的一次实证：

> 审计说「系统不缺机制，缺的是**变更之后有人知道**」。
> 而这 12 个 worktree + 5 条 stash **静静躺了 1~6 周**，没有任何巡检会报出它们。

⇒ 建议（已登记，未实现）：把「worktree / stash / 分支数量的阈值巡检」并入 D3 的 `audit_governance_check.py` 体系，
让**仓库卫生**也成为一道可自动发现的门。
