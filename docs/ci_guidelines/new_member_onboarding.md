# 新成员快速上手指南：本地环境 + Git Hook 配置

> **文档目的**：30 分钟内完成本地环境配置，让 Git Hook 在提交时自动拦截
> 失效链接 / BOM 编码错误 / 不变量破坏。
> **适用范围**：本仓库及其所有使用 `sync_precommit_hook.ps1` 部署 Hook 的仓库。
> **文档版本**：v1.0 | **更新日期**：2026-08-04
> **关联文档**：[Git Hook 与 BOM 排查避坑指南](git_hook_bom_guide.md) | [Git Hook 与 CI 升级总结](git_hook_ci_upgrade_summary.md)

---

## 一、环境要求（先核对）

| 依赖 | 版本 | 用途 | 验证命令 |
|------|------|------|---------|
| Windows | 10/11 | Hook 脚本运行平台 | `winver` |
| PowerShell | 5.1（系统内建） | 执行 `.ps1` 检查脚本 | `$PSVersionTable.PSVersion` |
| Git for Windows | 任意较新版本 | 触发 hook、push | `git --version` |
| Python | 3.10+ | 锚点链接回归测试 | `python --version` |

> ⚠️ **不要**手动安装新 PowerShell（pwsh）代替 5.1：本地 hook 明确用
> `powershell.exe`（PS 5.1）调用，以验证中文 BOM 编码契约（PS 7 行为不同）。

---

## 二、一步部署 Git Hook

```powershell
# 在仓库根目录执行（会自动安装 pre-commit 与 pre-push 两个 hook）
.\scripts\dev\sync_precommit_hook.ps1 -Install .
```

- 脚本自动从 `scripts/dev/` 生成 hook 模板到 `.git/hooks/`，并保留 `.bak.<时间戳>` 备份。
- 多仓库批量同步：`.\scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code`
- 只查看不落盘：`.\scripts\dev\sync_precommit_hook.ps1 -DryRun`
- 查看各仓库部署状态：`.\scripts\dev\sync_precommit_hook.ps1 -Status`

---

## 三、验证部署（30 秒）

```powershell
# 1. 手动跑一次完整预检（模拟 hook 行为）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 -TargetRepo .

# 期望输出：预检通过 / exit 0 / 锚点回归 4/4
```

```powershell
# 2. 验证 hook 确实拦截失败（故意制造一个坏提交）
#    —— 在 docs/ 下新建含失效链接的 .md 文件，git add 后 git commit，
#       应看到 [BROKEN] 输出且提交被 [BLOCK] 阻止
```

```powershell
# 3. 验证核心不变量静态校验（防无痕回滚）
python scripts/verify_core_invariants.py --repo-root . --quiet   # 期望 12/12 通过
```

---

## 四、日常使用

### 4.1 提交时自动检查什么

| 检查项 | 触发 | 失败行为 |
|--------|------|---------|
| Markdown 链接预检 | 每次 `git commit` | `[BLOCK]` → exit 1，提交被阻止 |
| 锚点链接回归测试 | 每次 `git commit` | `[FAIL]` → exit 1，提交被阻止 |
| PS 脚本编码契约（BOM） | 每次 `git commit` | BLOCK（关键文件）或 WARN（存量文件） |
| 核心不变量（12 项） | `git commit` 与 `git push` | BLOCK → exit 1，阻止提交/推送 |
| CI 守卫判定链 | `git commit`（CI 目录场景） | 失败 exit 1（可用 `SKIP_CI_GUARD=1` 豁免） |

### 4.2 提交被拦截时怎么办

```powershell
# 1. 先看 [BROKEN]/[BLOCK]/[FAIL] 定位具体文件与原因
# 2. 失效链接 → 修复链接；BOM 问题 → 见第五节
# 3. 修复后重新 git add + git commit 即可
```

### 4.3 临时豁免开关（仅限明确场景，用完恢复）

```bash
SKIP_ENCODING_CHECK=1 git commit      # 跳过编码契约检查
SKIP_CI_GUARD=1 git commit            # 跳过 CI 守卫判定链
SKIP_INVARIANT=1 git commit           # 跳过核心不变量校验
git commit --no-verify                # 完全跳过所有本地 hook（最不推荐）
```

> ⚠️ 豁免只影响**本地** hook。CI 的 `docs-precheck-tests` job 在 PR 阶段
> 会用同一判定链复查，坏改动依然会在 PR 页面暴露。

---

## 五、BOM 排障速查（踩坑高发区）

### 5.1 编码契约一句话口诀

> PS 脚本（`.ps1`/`.psm1`）**恰好 1 个 BOM**；bash hook / Python **无 BOM**。

### 5.2 一键扫描叠加 BOM

```powershell
Get-ChildItem scripts\dev,packages -Recurse -Include *.ps1,*.psm1 |
  Where-Object { $b = [System.IO.File]::ReadAllBytes($_.FullName); $b[0] -eq 239 -and $b[3] -eq 239 } |
  ForEach-Object { $_.FullName }
```

### 5.3 自动检查与修复

```bash
python scripts/check_ps1_encoding.py --repo-root . --quiet   # 检查（BLOCK/WARN 分级）
python scripts/check_ps1_encoding.py --repo-root . --fix      # 自动修复（补/去 BOM）
```

### 5.4 拦截失败时看字节证据（-BomDiag）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 -TargetRepo . -BomDiag
```

输出 BOM 头部 hex（如 `EF BB BF EF BB BF 23 20 E9` = 叠加 BOM x2），
判定：`None`（无 BOM）/ `Single`（契约态）/ `Stacked`（破坏态）。
详细解读见 [Git Hook 与 BOM 排查避坑指南](git_hook_bom_guide.md) 第三节。

---

## 六、常见问题（FAQ）

| 现象 | 原因与处理 |
|------|-----------|
| 部署显示 `DONE` 但提交行为没变化 | 模板更新失败（叠加 BOM → `Import-Module` 静默失败）。验证：`Import-Module scripts/dev/hook_fail_safe.psm1 -Force; $Error.Count` 应为 0 |
| `Import-Module` 报解析错误 | `hook_fail_safe.psm1` 缺 BOM（PS 5.1 中文系统按 GBK 解码失败）。用 5.2 扫描 + 5.3 修复 |
| 提交时提示 `Missing expression after unary operator` | 目标 `.ps1` 叠加 BOM 破坏 `<#` 块注释。5.3 `--fix` 修复 |
| `git commit -- <files>` 后文件被还原 | 本仓库已知陷阱：pre-commit 运行期间工作区修改会被还原。**规避：先 `git add` 再普通 `git commit`（不带 `--`）** |
| push 被 `INVARIANT` 段阻止 | 核心不变量被破坏（疑似无痕回滚）。跑 `verify_core_invariants.py` 定位，恢复清单见 `../observability/rollback_recovery_report.md`（主指南第七节引用） |

---

## 七、参考文档

- [Git Hook 与 BOM 排查避坑指南](git_hook_bom_guide.md) — 编码契约 / 事故速查 / 调试工具链 / ELK 接入
- [Git Hook 与 CI 升级总结](git_hook_ci_upgrade_summary.md) — 本次升级变更点 / ELK 配置细节 / `-BomDiag` 用法
- [Hook 复用指南](precommit_hook_reuse_guide.md) — 多仓库部署 / 同步 / 回滚
- [BOM 事故复盘](precommit_hook_bom_incident_report.md) — 事故时间线与根因
- 演示：`docs/ci_guidelines/assets/bomdiag_pr_demo.gif`（README 内嵌）
