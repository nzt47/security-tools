# Git 操作安全指南 — 自动提交劫持识别与应对

> 适用范围：本仓库（security-tools）。编写日期：2026-08-05。
> 背景：2026-08-04 ~ 08-05 多次出现**后台进程自动 git 操作**，干扰正常提交/推送，甚至劫持提交消息、切换分支。

## 1. 风险概述

本仓库存在**周期性后台自动进程**（如 `verify_bom_hook_stability.py` 的调度实例、`simulate_workflow_closed_loop.py` 类巡检脚本），它们会：

| 行为 | 后果 |
|------|------|
| 自动 `git add` + `git commit` | 你的暂存内容被混入无关文件，提交消息被替换 |
| 自动 `git push` | 本地/远程频繁 non-fast-forward，推送被拒 |
| 自动 `git checkout -b` | 当前分支被切换，master 工作被转移 |
| 修改工作区文件（叠加 BOM） | pre-commit BOMFIX/ENCODING 段拦截提交 |
| 生成未跟踪文件 | `git add .` 时混入提交 |

## 2. 识别信号（出现任一即警惕）

1. **提交消息不是自己写的**——`git log --oneline -3` 出现 `test(ci)` / `chore(ci)` / `fix(ci)` 等非本人提交
2. **暂存区突然多出文件**——`git status` 显示未 add 过的 `A`/`M` 文件
3. **"nothing to commit" 但明明暂存过**——hook 运行期间暂存区被清
4. **推送被拒 non-fast-forward**——远程在你上次 fetch 后出现新提交
5. **当前分支变化**——`git status -sb` 第一行不再是预期分支（`## master` 变成其他分支）
6. **.ps1 文件被叠加 BOM**——`python scripts/check_ps1_encoding.py` 报 `叠加 BOM x2`
7. **工作区文件被还原**——已修改文件莫名回到 HEAD 状态
8. **出现可疑未跟踪文件**——`release-note-v1.1.10-20260804.md`、`safe_git_revert.py`、`simulate_pr_merge_guard.py`、`verify_bom_hook_stability.py` 等

## 3. 诊断命令（先确认，再行动）

```powershell
# 1) 检查是否有后台 python 干扰进程（脚本已内置识别列表）
powershell -File .\scripts\stop_agitator_processes.ps1          # 报告
powershell -File .\scripts\stop_agitator_processes.ps1 -Kill    # 终止（确认后）

# 2) 手动查进程命令行（确认匹配目标，绝不盲杀）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'verify_bom_hook_stability|simulate_workflow_closed_loop' } |
  Select-Object ProcessId, CommandLine

# 3) 查看提交/分支/同步状态
git log --oneline -5
git status -sb
git fetch origin master; git log --oneline origin/master -3   # 远程是否被推进

# 4) 确认暂存区内容（提交前必查）
git diff --cached --name-only
```

## 4. 应对流程

### 4.1 发现干扰进程 → 终止

1. 运行 `stop_agitator_processes.ps1`（DryRun）确认目标
2. 确认无误后 `-Kill`；必要时 `-KillTree` 连带终止 git/pwsh 残留子进程
3. 复查 `Get-Process python` 确认无残留

### 4.2 暂存区被塞文件 → 清理

```powershell
# 移出非目标文件（只动 index，不动工作区）
git restore --staged scripts/safe_git_revert.py scripts/simulate_pr_merge_guard.py ...
git diff --cached --name-only   # 确认只剩目标文件
```

### 4.3 .ps1 被叠加 BOM → 修复

```powershell
python scripts/fix_ps_bom.py --apply --repo-root .
python scripts/check_ps1_encoding.py --repo-root .   # 期望 BLOCK 0
```

### 4.4 提交被劫持/消息被替换 → 拆分修正

若目标文件已被混入后台提交（如 `a95e2dce` 含 Wiki + 后台脚本）：

```powershell
# 方案 A（推荐，接受现状）：内容已在远程则无需重写，避免与后台竞争
# 方案 B（拆分）：soft reset 到上一个可靠提交，重新单独提交目标文件
git reset --soft <上一可靠提交>      # 撤销混合提交，变更回暂存区
git restore --staged <后台脚本>      # 移出无关文件
git commit -m "<你自己的消息>"
```

> ⚠️ 若后台在 reset 后抢先推送，本地会 non-fast-forward；此时 **pull --rebase** 通常会自动丢弃"内容已上游"的重复提交。

### 4.5 推送被拒 non-fast-forward → rebase

```powershell
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"
git pull --rebase origin master
# 若报 untracked working tree files would be overwritten：
#   本地未跟踪文件与远程提交同名 → 删除该未跟踪文件（远程提交会恢复它）后重试
git push origin master
```

### 4.6 分支被切换 → 确认并切回

```powershell
git branch -a                       # 查看全部分支
git checkout master                 # 确认工作无误后切回
# 注意: 后台分支（fix/observability-ci-shard 等）的提交若未推送, 先确认是否需要保留
```

## 5. 已知 Git 陷阱汇总（本仓库实测）

| 陷阱 | 现象 | 规避 |
|------|------|------|
| `git commit -- <paths>` | hook 运行期间工作区被还原、暂存区被清 | 先 `git add` 再普通 `git commit`（不带 `--`） |
| `pull --rebase` | 未提交变更被还原（docker-compose.yml 曾丢失） | rebase 前确认工作区干净；有未跟踪同名文件先删 |
| PowerShell 花括号 | `stash@{0}` 报 "Too many revisions" | 用单引号 `'stash@{0}'` |
| stash pop 假失败 | 报错但部分文件已 apply、stash 已 drop | 双确认 `git stash list` + `git status` |
| 后台进程抢跑 | 提交消息被替换、暂存区被塞文件 | 提交前 `git diff --cached --name-only` + 提交后立即核对 `git log --oneline -1` |

## 6. 预防措施

1. **提交前固定动作**：`git status --short` → `git diff --cached --name-only`（确认仅目标文件）→ 提交 → 核对 `git log -1`
2. **.gitignore 防护**：已配置后台产物忽略规则（见 .gitignore "后台干扰进程产物" 段）。⚠️ 注意：**已跟踪文件不受 ignore 影响**，若后台已提交，需 `git rm --cached <file>` 才能移出跟踪
3. **定期体检**：`powershell -File .\scripts\stop_agitator_processes.ps1`（可加入定时任务）
4. **关键提交前备份**：重要配置（docker-compose.yml 等）提交前比对当前文件与 HEAD：`git diff HEAD -- docker-compose.yml`

## 7. 本次事故时间线（2026-08-04 ~ 08-05）

| 时间 | 事件 |
|------|------|
| 08-04 | 多轮自动提交（BOM 修复链 d9530a77 → 117a7513 → 90728a6e → 3f975a99） |
| 08-05 06:47 | `verify_bom_hook_stability.py --iterations 5` 调度实例运行，反复 git add/reset/commit |
| 08-05 06:51 | 后台提交 `a95e2dce`（Wiki 页面 + verify 脚本混合），消息为 test(ci) |
| 08-05 | 4 个 .ps1 被叠加 BOM（BLOCK 级），修复后恢复 |
| 08-05 | 分支被切到 `fix/observability-ci-shard`，3 个后台文件被提交跟踪 |

## 8. 真相澄清（2026-08-05 复核，重要）

本章更正前述章节中的错误归因。经全仓排查（进程列表 / 计划任务 / 端口监听 / 提交时间线）确认：

| 结论 | 依据 |
|------|------|
| **不存在恶意守护进程** | 当前无 python/git 后台常驻进程、无自定义计划任务、无独立监听端口（33030 为 Trae 自身 SSE 端口） |
| **"干扰"主源 = 并发 AI 会话** | 08-05 白天 08:38~21:43 存在另一 Trae 会话持续提交（`fix(ci)`/`docs(ci)`/`chore(ci)` 数十个），其 git add/commit/rebase 与人工操作并发，表现为"提交被混入无关文件 / 工作区被还原 / 分支变化" |
| **06:47 的 `verify_bom_hook_stability` 运行 = 人工验证测试** | 该脚本设计即"循环 git add/reset/commit 触发 hook 拦截"（见脚本 docstring），06:47-06:49 的 5 轮运行是人工执行的 BOM 拦截稳定性验证，**非调度实例，不应被终止** |
| **GitHub 自动提交 = workflow 正常行为** | `ab4f3670` 等 `[skip ci]` 提交（+0000 时区、github-actions bot 身份）来自模块依赖图 / CI 健康度看板自动更新 workflow |

### 处置要点（守【不易】）

1. ⚠️ **不要执行 `stop_agitator_processes.ps1 -Kill` 去杀 `verify_bom_hook_stability` 进程**——该默认模式会匹配人工验证测试脚本本身，误杀合法验证。仅在确认存在「你自己运行的、不再需要的」同名调度时才可终止。
2. 若确认另一会话不再需要，由**人工手动关闭**该 Trae 对话即可；没有独立守护进程可杀。
3. §6 的提交前固定动作（`git status --short` → `git diff --cached --name-only` → 提交 → 核对 `git log -1`）依旧适用，用于规避并发会话的 git 竞争。

## 9. 排查清单（速查 · 手动验证/清理用）

按顺序执行，每步记录结果：

- [ ] 1. **确认无活跃干扰进程**
      `powershell -File scripts/stop_agitator_processes.ps1`（DryRun 仅报告，勿盲目 `-Kill`）
      `Get-Process | Where-Object { $_.ProcessName -match 'python|git' }` → 期望为空
- [ ] 2. **确认无自定义计划任务**
      `schtasks /query /fo CSV | ConvertFrom-Csv | Where-Object { $_.'Task To Run' -match 'agent|python|git' }` → 期望无自定义项
- [ ] 3. **核对提交时间线（区分来源）**
      `git log --since='今天' --format='%h %ci %s'`
      - `+0000` 且 `[skip ci]` → GitHub workflow 自动提交（正常行为，保留）
      - `+0800` 且非本人操作 → 并发会话提交（人工评估是否保留）
- [ ] 4. **检查工作区污染**
      `git status --porcelain`（未跟踪文件逐个确认来源）
      `git diff --cached --name-only`（暂存区应仅含目标文件）
- [ ] 5. **检查 BOM 污染**
      `python scripts/check_ps1_encoding.py --repo-root .` → 期望 BLOCK 0
- [ ] 6. **验证 hook 拦截能力（可选）**
      `python scripts/verify_bom_hook_stability.py --iterations 2` → 期望 2/2 PASS
- [ ] 7. **关键文件不变量**
      `python scripts/verify_core_invariants.py --repo-root .` → 期望 12/12 PASS
- [ ] 8. **清理误跟踪**（若后台已把干扰产物 `git add`）
      `git restore --staged <文件>`（只动 index，不动工作区）
- [ ] 9. **清理未跟踪日志/产物**（确认来源后删除）
      `docs/troubleshooting/*.log`、`docs/troubleshooting/_diag_*.py`、`docs/observability/*_report.md` 等逐个核对
- [ ] 10. **定期体检（预防）**
      计划任务挂 `stop_agitator_processes.ps1`（仅报告模式）或每周人工跑一次 §9 清单
