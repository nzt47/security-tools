# Git Hook 与 BOM 排查避坑指南

> **文档目的**：面向团队内部，沉淀 Git Hook 部署、UTF-8 BOM 编码、字节级调试
> 与 JSON 结构化日志的一线排查经验，让新同学 30 分钟内能独立定位/修复同类问题。
> **适用范围**：所有使用 `sync_precommit_hook.ps1` 部署 Hook 的仓库。
> **文档版本**：v1.0 | **更新日期**：2026-08-04

---

## 一、编码契约（先背结论）

| 文件类型 | 编码要求 | 原因 |
|----------|---------|------|
| `.git/hooks/pre-commit` / `pre-push`（bash） | **UTF-8 无 BOM** | bash 不识别 BOM，`#!/bin/bash` 前有 BOM 直接报错 |
| `.ps1` / `.psm1`（PowerShell） | **UTF-8 恰好 1 个 BOM** | PS 5.1 中文系统默认按 GBK 解码，无 BOM 中文乱码；≥2 个 BOM 破坏 `<#` 块注释 |
| `.py`（Python） | UTF-8 无 BOM | Python 源码约定 |

**一句话口诀**：PS 脚本「恰好 1 个 BOM」，bash hook / Python「无 BOM」。

### 为什么叠加 BOM 会炸块注释？

```
正常：EF BB BF  3C 23 0D 0A  .SYNOPSIS ...
      [BOM]    <#  CRLF      （BOM 后紧跟 <#，行首标记成立）
叠加：EF BB BF  EF BB BF  3C 23 0D 0A  .SYNOPSIS ...
      [BOM]    [BOM]     <#
```

PS 5.1 只把**第一个** BOM 当文件标记，后续 `EF BB BF` 被解码为 U+FEFF 字符，
导致 `<#` 不再位于行首 → 块注释解析失败 → 注释内容（如 `- 不易：…`）被当成代码，
报 `Missing expression after unary operator '-'`。

---

## 二、事故速查表（均已在生产踩过）

| # | 现象 | 根因 | 定位手段 |
|---|------|------|---------|
| 1 | 运行 `sync_precommit_hook.ps1` 直接报语法错误 | 脚本开头叠加 9 个 BOM | 读前 8 字节看 hex |
| 2 | 部署显示 `DONE` 但 hook 仍是**旧模板** | `hook_fail_safe.psm1` 叠加 BOM → `Import-Module` 静默失败 → 用旧内容生成 | `Get-HookContent` 后 grep 关键路径 |
| 3 | 提交成功但 post-commit 同步失败 | 包目录 5 个 PS 文件叠加 BOM | 批量扫描首字节 |

> **事故 2 最隐蔽**：磁盘文件看起来是新的，运行时却是旧的。必须同时验证
> 「磁盘内容」和「运行时行为」两个层面。

---

## 三、排查工具链（按使用频率排序）

### 3.1 一键扫描叠加 BOM（手动，快速初筛）

```powershell
Get-ChildItem scripts\dev,packages -Recurse -Include *.ps1,*.psm1 |
  Where-Object { $b = [System.IO.File]::ReadAllBytes($_.FullName); $b[0] -eq 239 -and $b[3] -eq 239 } |
  ForEach-Object { $_.FullName }
```

### 3.2 字节级调试开关 `-BomDiag`（拦截失败时看字节证据）

PS 5.1 的 `-File` 调用会把 `-Verbose` 当作保留参数名、不绑定到显式 switch，
因此用自定义开关 `-BomDiag` 实现（透传链路见第五节）。

```powershell
# 本地手动开启字节级诊断
& powershell -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 `
  -TargetRepo . -BomDiag
```

开启后，失效链接会额外输出：
- 链接原文 / 锚点剥离前后 / 解析全路径 / 文件存在性
- 宿主 Markdown 文件与目标脚本的 BOM 状态（`None/Single/Stacked`）+ 头部 hex

对应 `write-log --level debug` 的 `event=diag` 行（JSON 模式 `"level":"DEBUG"`）。

### 3.3 编码契约自动检查 `check_ps1_encoding.py`（CI/Commit 级）

```bash
# BLOCK 级问题（非法 UTF-8 / 关键契约文件缺 BOM）→ exit 1
python scripts/check_ps1_encoding.py --repo-root . --quiet
# 自动修复（补 BOM / 去叠加 BOM）
python scripts/check_ps1_encoding.py --repo-root . --fix
# 临时豁免（仅限明确场景）
SKIP_ENCODING_CHECK=1 git commit
```

分级策略：**BLOCK**（阻止提交）只针对关键契约文件；存量文件缺 BOM 仅 **WARN** 提示。

### 3.4 核心不变量校验 `verify_core_invariants.py`（防无痕回滚）

```bash
python scripts/verify_core_invariants.py --repo-root . --quiet   # 12 项静态校验
python scripts/verify_core_invariants.py --repo-root . --html report.html
```

破坏任一不变量 → BLOCK（exit 1）。背景：2026-08-04 曾发生修复被 git 还原、
历史无对应提交导致静默回归，本脚本把关键模式锁死为静态不变量。
临时豁免：`SKIP_INVARIANT=1`。

---

## 四、JSON 结构化日志（ELK/Filebeat 接入）

### 4.1 开启方式

```powershell
# 本地
& powershell -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 -TargetRepo . -JsonOutput

# CI（docs-precheck-tests job 已内置）
& powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 `
  -TargetRepo $env:GITHUB_WORKSPACE -BomDiag -JsonOutput
```

### 4.2 输出格式

每条日志**一行 JSON**，UTF-8 无 BOM 输出到 stdout：

```json
{"ts":"2026-08-04T04:12:33.1234567Z","level":"ERROR","event":"broken_link","msg":"  [BROKEN] guide.md: ../x.md","data":{"file":"guide.md","link":"../x.md","host":"C:\\repo\\docs\\guide.md"}}
```

| 字段 | 说明 |
|------|------|
| `ts` | UTC ISO-8601（`o` 格式） |
| `level` | `INFO` / `WARN` / `ERROR` / `OK` / `DEBUG` |
| `event` | 事件名，供 ELK 过滤：`broken_link` / `block` / `pass` / `summary` / `diag` / `bom` / `header` 等 |
| `msg` | 人类可读正文，**保留 `[BROKEN]`/`[BLOCK]`/`[OK]` 标记**，回归测试断言与 GitHub Actions 阅读均不受影响 |
| `data` | 可选附加字段（BOM hex、文件路径、计数等） |

### 4.3 ELK 采集要点

- **单行 JSON**：每条日志一个 `\n`，Filebeat 按行切分即可，无需多行合并配置。
- **空行已剔除**：JSON 模式下排版空行不输出，避免污染采集。
- **DEBUG 级默认不输出**：仅 `-BomDiag` 时出现，默认场景日志量可控。
- **Filebeat 建议配置**：`json.keys_under_root: true` + `json.add_error_key: true`，
  非 JSON 行（如 pytest 原始输出）作为普通 message 保留，不丢弃。

---

## 五、`-BomDiag` 透传链路（CI 与本地同源）

```
CI:  .github/workflows/ci.yml docs-precheck-tests job
      └─ git_precommit_check.ps1 -BomDiag -JsonOutput
本地: TLM_HOOK_VERBOSE=1 → hook bash VERBOSE_ARG="-BomDiag"
      └─ git_precommit_check.ps1 -BomDiag
          └─ precheck_docs.ps1 -BomDiag [-JsonOutput]
```

- `-BomDiag`：`git_precommit_check.ps1` 与 `precheck_docs.ps1` 均消费；
  开启后 `$VerbosePreference='Continue'`，`DEBUG` 级诊断日志生效。
- `-JsonOutput`：`git_precommit_check.ps1` 透传给下游 `precheck_docs.ps1`；
  本地 hook 保持人类可读（不传 `-JsonOutput`），JSON 仅供 CI/ELK。
- **CI 在 PR 阶段即触发**（`docs-precheck-tests` job，`on: pull_request`），
  与本地 hook 使用同一判定链，BOM 边缘问题在 PR 页面直接可见。

---

## 六、常见坑与规避（血的教训）

1. **编辑 .psm1 后必须复查 BOM**
   `hook_fail_safe.psm1` 必须 UTF-8 **带 BOM**（PS 5.1 中文系统按 GBK 解码无 BOM 文件解析失败）。
   用 IDE 保存后，用 3.1 的扫描命令复查，或跑一次 `check_ps1_encoding.py`。

2. **「无 BOM 文件」被 TrimStart 会误加 BOM**
   修复叠加 BOM 前先确认原状态（无/单/叠加），只处理叠加 BOM 的文件。

3. **hook 旧模板的静默失败**
   部署显示 `DONE` ≠ 模板已更新。验证三连：
   ```powershell
   Import-Module hook_fail_safe.psm1 -Force; $Error.Count   # 期望 0
   (Get-HookContent -SourceRepo '<repo>') | Select-String 'git_precommit_check'  # 期望命中
   ```

4. **`git commit -- <paths>` 会还原工作区**
   本仓库用 `git commit -- <files>` 形式提交时，pre-commit 运行期间工作区修改会被还原。
   **规避：先 `git add <files>` 再普通 `git commit`（不带 `-- <paths>`）。**

5. **PowerShell 花括号陷阱**
   `stash@{0}` 的 `{0}` 会被 PS 当子表达式解析。**规避：单引号包裹 `'stash@{0}'`。**

6. **stash pop 冲突假失败**
   stash pop 因冲突 abort 时，非冲突文件可能已 apply 且 stash 已被 drop。
   不能只看报错，必须 `git stash list` + `git status` 双确认。

7. **WSL bash 不继承 Windows 环境变量**
   本地跑 bash 脚本调试时，`TLM_HOOK_SOURCE_REPO` 可能取不到。git for windows 自带的
   sh（真实 git commit/push 场景）会继承，与 WSL 行为不同，测试 hook 用 git 原生触发。

---

## 七、部署与回滚速查

```powershell
# 部署/同步/状态
.\scripts\dev\sync_precommit_hook.ps1                    # 安装到当前仓库
.\scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code   # 批量同步
.\scripts\dev\sync_precommit_hook.ps1 -Status            # 查看各仓库状态
.\scripts\dev\sync_precommit_hook.ps1 -DryRun            # 预览不落盘

# 回滚预案
# 1. 每个仓库 hooks/ 下保留 pre-commit.bak.<时间戳>，手动还原即可
# 2. 紧急跳过 hook：git commit --no-verify / git push --no-verify
# 3. 关键脚本被破坏时：verify_core_invariants.py 报告 + 恢复清单见
#    docs/observability/rollback_recovery_report.md
```

## 八、变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-04 | v1.0 | 首次成文：编码契约 + 事故速查 + 调试工具链 + JSON/ELK 接入 + 透传链路 + 避坑清单 |

---

## 参考文档

- 事故复盘细节：`docs/ci_guidelines/precommit_hook_bom_incident_report.md`
- Hook 复用指南：`docs/ci_guidelines/precommit_hook_reuse_guide.md`
- 编码 BOM 排查清单：`docs/observability/ps1_encoding_bom_troubleshooting.md`
- 回滚恢复报告：`docs/observability/rollback_recovery_report.md`
