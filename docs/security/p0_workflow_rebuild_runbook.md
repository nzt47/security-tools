# P0 Workflow 重建脚本 — 验证报告与操作手册

> **文档编号**: P0-RUNBOOK-REBUILD-20260704-001
> **生成时间**: 2026-07-04 (UTC+8)
> **脚本路径**: `scripts/rebuild_p0_workflow.py`
> **单元测试**: `tests/unit/test_rebuild_p0_workflow.py`（32 个用例，全部通过）
> **文档定位**: 安全运维知识库 — 应急操作手册

---

## 一、文档概述

本文档合并了 P0 安全验证 workflow 重建脚本的**验证报告**和**操作手册**，作为安全运维知识库的归档文档。包含：

1. 脚本删除逻辑的风险评估
2. dry-run 模式零副作用的验证结论
3. 正式重建的完整操作手册（含权限要求、回滚步骤）
4. 已知风险与缓解措施

---

## 二、脚本删除逻辑风险评估

### 2.1 删除逻辑代码

```python
def remove_old_workflow(dry_run=False):
    if dry_run:
        print(f"  [dry-run] 将执行: git rm {OLD_WORKFLOW_PATH}")
        return
    run_git(["rm", OLD_WORKFLOW_PATH])  # 实际删除
```

底层 `run_git` 使用 `subprocess.run` 传递列表参数，`check=True` 默认开启。

### 2.2 风险矩阵

| 风险项 | 等级 | 评估 | 缓解状态 |
|--------|------|------|----------|
| **`--force` 强制删除** | ✅ 安全 | 未使用 `-f` 标志，`git rm` 会拒绝删除有未提交修改的文件 | 内置 git 安全机制 |
| **Shell 注入** | ✅ 安全 | `subprocess.run` 使用列表参数，无 shell 解释 | 代码层面已防护 |
| **工作目录逃逸** | ✅ 安全 | `cwd=str(REPO_ROOT)` 限定在仓库根目录 | 代码层面已防护 |
| **dry-run 副作用** | ✅ 安全 | dry-run 模式在调用 `run_git` 前返回 | 已验证零副作用 |
| **备份完整性未校验** | ⚠️ 中等 | 备份在删除前执行，但未校验备份文件内容与原文件一致 | 建议增加校验 |
| **路径模式未验证** | ⚠️ 中等 | `OLD_WORKFLOW_PATH` 为硬编码常量，但无运行时断言路径匹配 `.github/workflows/p0-security*.yml` | 建议增加断言 |
| **删除前无二次确认** | ⚠️ 低 | 仅 `main()` 入口处有初始确认，`git rm` 前无"最后机会"提示 | 建议增加 |
| **并发执行无保护** | ⚠️ 低 | 两人同时运行可能导致状态不一致 | 手动应急脚本，风险可接受 |

### 2.3 结论

删除逻辑**不包含强制删除的误操作风险**。`git rm` 不带 `--force` 标志，遇到未提交修改会自动拒绝并退出。主要改进建议：

1. 在 `remove_old_workflow()` 执行 `git rm` 前，增加备份文件完整性校验（读取并比对内容）
2. 增加路径模式断言：`assert "p0-security" in OLD_WORKFLOW_PATH`
3. 在 `git rm` 前增加最终确认提示（可选，取决于自动化需求）

---

## 三、dry-run 模式验证报告

### 3.1 验证方法

在 `python scripts/rebuild_p0_workflow.py --dry-run` 前后分别捕获 5 类 git 状态快照，比对差异。

| 捕获项 | 命令 | 覆盖范围 |
|--------|------|----------|
| 工作区+暂存区状态 | `git status --porcelain` | 所有文件的修改/暂存/未跟踪状态 |
| HEAD commit SHA | `git rev-parse HEAD` | 当前提交指针 |
| 暂存区差异 | `git diff --staged --stat` | 暂存区与 HEAD 的差异 |
| 工作区差异 | `git diff --stat` | 工作区与暂存区的差异 |
| 完整索引 | `git ls-files -s` | 全部 21182 个跟踪文件的 SHA+模式 |

### 3.2 验证结果

| 指标 | before | after | 结论 |
|------|--------|-------|------|
| 文件大小 | 2,173,891 bytes | 2,173,891 bytes | 一致 |
| 行数 | 21,182 | 21,182 | 一致 |
| SHA256 | `FF72507B...F387AA5` | `FF72507B...F387AA5` | **完全一致** |
| `Compare-Object` 差异 | — | — | 无输出（零差异） |

### 3.3 额外验证

旧 bug 会创建的两个文件均不存在：
- `.github/workflows/p0-security-v2.yml` → 未找到
- `docs/security/archive/p0-security.yml.backup_*` → 未找到

### 3.4 验证结论

dry-run 模式仅执行 2 条只读 git 命令（`rev-parse` 和 `status --porcelain`），对**暂存区、工作区、HEAD、索引、跟踪文件内容**均无任何修改。dry-run 模式是安全的。

### 3.5 单元测试覆盖

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|----------|
| TestBackupOldWorkflow | 6 | 备份创建、内容一致、时间戳、自动建目录、dry-run 无文件、多次不覆盖 |
| TestCreateNewWorkflow | 4 | 创建新文件、路径不同、dry-run 无文件、Unix 换行符 |
| TestRemoveOldWorkflow | 2 | dry-run 不调用 git、实际模式调用 git rm |
| TestCommitAndPush | 5 | dry-run 不调用 git、暂存/commit/push 序列、返回 SHA、失败退出 |
| TestCheckPrerequisites | 3 | 条件满足通过、分支错误退出、文件缺失退出 |
| TestAnalyzeResult | 7 | 全成功、Set up job 失败、非 Set up job 失败、P0 成功其他失败、None/空 jobs |
| TestDryRunIntegration | 3 | 无文件副作用、不修改旧文件、完整流程无 git 调用 |
| TestActualFlowIntegration | 2 | 备份后保留旧文件、新文件内容匹配 |
| **合计** | **32** | **全部通过** |

---

## 四、正式重建操作手册

### 4.1 前置条件

#### 权限要求

| 权限项 | 要求 | 验证方法 |
|--------|------|----------|
| Git 仓库写权限 | 对 `nzt47/security-tools` 仓库有 push 权限 | `git push --dry-run origin phase2-visibility-convergence` |
| GitHub Token | `~/.git-credentials` 中有有效 token（`gho_` 前缀） | `git ls-remote https://github.com/nzt47/security-tools.git` |
| GitHub Actions 查看权限 | 能访问仓库的 Actions 页面 | 浏览器打开 `https://github.com/nzt47/security-tools/actions` |
| 本地分支 | 当前在 `phase2-visibility-convergence` 分支 | `git rev-parse --abbrev-ref HEAD` |
| 工作目录状态 | workflow 文件无未提交变更 | `git status --porcelain .github/workflows/p0-security.yml` |

#### 触发条件

仅当以下**全部**条件满足时才执行正式重建：

1. P0 回归测试 Job 持续因 "Set up job" 失败（非代码问题）
2. 已尝试 `rerun-failed-jobs` API 重跑 — 无效
3. 已尝试 `workflow_dispatch` 触发新运行 — 无效
4. 已尝试修改 Job 名称 — 无效
5. 已尝试修改 job_id — 无效
6. 距离首次失败已超过 24 小时，平台仍未恢复

### 4.2 操作步骤

#### Step 0：环境准备

```bash
# 确认当前分支
git rev-parse --abbrev-ref HEAD
# 输出应为: phase2-visibility-convergence

# 确认工作目录干净（workflow 文件无修改）
git status --porcelain .github/workflows/p0-security.yml
# 应无输出

# 拉取最新代码
git pull origin phase2-visibility-convergence
```

#### Step 1：dry-run 模拟（必执行）

```bash
python scripts/rebuild_p0_workflow.py --dry-run
```

**验证要点**：
- 输出应包含 `[dry-run]` 标记的所有 4 个步骤
- 最后一行应为 `[dry-run] 模拟完成，未实际执行任何操作`
- 执行后 `git status` 应无变化

#### Step 2：正式执行（交互模式）

```bash
python scripts/rebuild_p0_workflow.py
```

脚本将依次执行：
1. 前置条件检查（分支、文件存在性）
2. 备份旧 workflow 到 `docs/security/archive/p0-security.yml.backup_<timestamp>`
3. 创建新文件 `.github/workflows/p0-security-v2.yml`
4. 删除旧文件（`git rm .github/workflows/p0-security.yml`）
5. 提交并推送到 `phase2-visibility-convergence`
6. 等待新 workflow 出现在 GitHub Actions 中
7. 等待首次 CI 运行触发
8. 轮询运行结果，自动判断 P0 回归测试是否通过

**或使用 `--yes` 跳过确认**（自动化场景）：

```bash
python scripts/rebuild_p0_workflow.py --yes
```

#### Step 3：人工确认

脚本完成后，人工确认以下事项：

| 确认项 | 方法 | 预期结果 |
|--------|------|----------|
| 新 workflow 已创建 | GitHub Actions 页面 | 出现 "P0 安全验证" workflow（路径为 `p0-security-v2.yml`） |
| 旧 workflow 已删除 | GitHub Actions 页面 | 旧 workflow 不再出现在列表中 |
| 备份文件存在 | `ls docs/security/archive/` | 存在 `p0-security.yml.backup_*` 文件 |
| 首次运行已触发 | GitHub Actions 运行列表 | 有新的运行记录 |
| P0 回归测试 Job 通过 | 运行详情页 | "P0 Security Regression Test" Job 显示 ✅ |

### 4.3 回滚步骤

如果重建后出现问题（如新 workflow 仍失败、新文件内容有误等），按以下步骤回滚。

#### 场景 A：重建后 P0 回归测试仍失败（平台故障未恢复）

此场景下无需回滚文件，只需记录失败并联系 GitHub 支持：

```bash
# 查看新运行结果
python scripts/rebuild_p0_workflow.py --dry-run  # 确认脚本状态

# 手动查看 CI 运行
# 浏览器打开: https://github.com/nzt47/security-tools/actions
# 查看新 workflow 的运行详情
```

#### 场景 B：需要恢复旧 workflow 文件

如果新 workflow 有问题，需要恢复旧文件：

```bash
# 方法 1：从备份恢复
cp docs/security/archive/p0-security.yml.backup_<timestamp> .github/workflows/p0-security.yml

# 方法 2：从 git 历史恢复（推荐，确保内容与提交前一致）
git show <重建前的 commit>:.github/workflows/p0-security.yml > .github/workflows/p0-security.yml

# 删除新 workflow 文件
rm .github/workflows/p0-security-v2.yml

# 提交回滚
git add .github/workflows/p0-security.yml
git rm .github/workflows/p0-security-v2.yml
git commit -m "revert: 回滚 P0 workflow 重建，恢复旧 workflow 文件"
git push origin phase2-visibility-convergence
```

#### 场景 C：需要回退到重建前的 commit

如果整个重建操作需要完全撤销：

```bash
# 查看重建前的 commit
git log --oneline -5
# 找到重建 commit 的前一个 commit SHA

# 创建回退 commit（推荐，保留历史）
git revert <重建 commit SHA>
git push origin phase2-visibility-convergence

# 或硬回退（谨慎！会丢失重建后的所有提交）
# git reset --hard <重建前的 commit SHA>
# git push --force origin phase2-visibility-convergence
# ⚠️ 警告：force push 会重写远程历史，仅在确认无其他人的提交依赖此分支时使用
```

#### 场景 D：备份文件丢失且 git 历史不可用

极端情况下（备份丢失 + git 历史被重写），可从 P0 最终验证报告中手工重建 workflow 文件：

```bash
# 参考文档中的完整 workflow 内容
# docs/security/p0_final_verification_report.md
# 或从 GitHub Actions UI 的历史运行中查看旧 workflow 的 YAML 内容
```

### 4.4 回滚决策树

```
重建后 P0 回归测试结果？
├── ✅ 通过 → 重建成功，无需回滚
├── ❌ Set up job 失败 → 平台故障未恢复
│   ├── 记录失败详情
│   ├── 联系 GitHub 支持
│   └── 保留新 workflow（等待平台恢复）
└── ❌ 其他步骤失败 → 新 workflow 可能有配置问题
    ├── 执行场景 B（恢复旧 workflow 文件）
    └── 检查新文件内容是否与旧文件一致
```

---

## 五、已知风险与缓解措施

| 风险项 | 影响 | 缓解措施 |
|--------|------|----------|
| 备份完整性未校验 | 备份文件可能损坏但未发现 | 操作前手动 `diff` 比对备份与原文件 |
| 路径硬编码 | 修改常量可能导致删除错误文件 | 常量定义在文件顶部，修改前 code review |
| 无并发锁 | 同时执行可能导致状态不一致 | 仅授权一人执行，操作前通知团队 |
| GitHub API 限流 | 轮询可能触发限流 | 脚本内置 10-30s 间隔，单次最多轮询 20 次 |
| 网络中断 | 推送后轮询失败 | 手动在 GitHub Actions UI 查看结果 |
| 旧 workflow ID 仍缓存在 GitHub | 极端情况下旧 ID 可能仍有效 | 观察 24h，如旧 workflow 仍触发运行联系 GitHub 支持 |

---

## 六、附录

### 6.1 脚本关键常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `REPO` | `nzt47/security-tools` | GitHub 仓库 |
| `BRANCH` | `phase2-visibility-convergence` | 操作分支 |
| `OLD_WORKFLOW_PATH` | `.github/workflows/p0-security.yml` | 旧 workflow 文件路径 |
| `NEW_WORKFLOW_PATH` | `.github/workflows/p0-security-v2.yml` | 新 workflow 文件路径 |
| `ARCHIVE_DIR` | `docs/security/archive` | 备份归档目录 |

### 6.2 相关文档

| 文档 | 路径 | 说明 |
|------|------|------|
| P0 最终验证报告 | `docs/security/p0_final_verification_report.md` | 完整诊断过程和 17 次运行记录 |
| P0 安全修复归档 | `docs/security/p0_security_fix_archive_20260703.md` | P0 修复完整日志 |
| Release Notes | `docs/security/RELEASE_NOTES_P0_SECURITY_20260703.md` | P0 发布说明 |
| 安全变更日志 | `docs/security/CHANGELOG.md` | 安全变更记录 |
| 部署检查清单 | `docs/security/DEPLOYMENT_CHECKLIST.md` | 通用部署检查清单 |
| 故障排查指南 | `docs/security/TROUBLESHOOTING.md` | 通用故障排查 |

### 6.3 Git 提交历史

| Commit | 说明 | 日期 |
|--------|------|------|
| `0b9fddd4` | 新增重建脚本单元测试 + 修复 dry-run 副作用 + README 文档 | 2026-07-04 |
| `457177eb` | 更新 P0 验证报告完整运行记录 + 新增 workflow 重建脚本 | 2026-07-04 |
| `cb68d605` | 修改 P0 回归测试 job_id 绕过平台故障 | 2026-07-04 |
| `f1572c4b` | 修改 P0 回归测试 Job 名称绕过平台故障 | 2026-07-04 |
| `52a9aa91` | 更新 P0 最终验证报告 - 平台故障深度诊断 | 2026-07-04 |

---

## 七、签收信息

| 项目 | 内容 |
|------|------|
| 文档生成人 | AI 助手 |
| 文档生成时间 | 2026-07-04 (UTC+8) |
| 文档类型 | 验证报告 + 操作手册（合并归档） |
| 验证范围 | dry-run 零副作用验证 + 删除逻辑风险评估 + 32 个单元测试 |
| 操作手册范围 | 正式重建流程 + 权限要求 + 4 种回滚场景 |
| 用户审阅状态 | ⏳ 待审阅 |

---

**本文档为 P0 workflow 重建脚本的验证报告与操作手册合并归档，存放于安全运维知识库。正式执行重建前请完整阅读第四章操作手册。**
