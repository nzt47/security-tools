# Skills Check Branch Protection 配置指南

本文档说明如何配置 GitHub Branch Protection Rules，强制要求 `skills-check` workflow 通过后才允许合并 PR 到 `master`/`main` 分支。

## 前置条件

- 仓库已启用 GitHub Actions
- `.github/workflows/skills-check.yml` 已提交到默认分支
- 你对仓库有 **Admin** 权限（只有 Admin 才能配置 Branch Protection）

## 配置步骤

### 1. 进入 Branch Protection 设置页

1. 打开 GitHub 仓库页面
2. 点击 **Settings** 标签
3. 左侧菜单选择 **Branches**
4. 找到 **Branch protection rules** 区域
5. 点击 **Add branch protection rule** 按钮

### 2. 配置规则基本信息

| 字段 | 值 |
|---|---|
| **Branch name pattern** | `master` （或 `main`，根据你的主分支名） |

> 如果仓库同时有 `master` 和 `main`，需创建两条规则分别配置。

### 3. 勾选 Required Checks（核心配置）

在 **Protect matching branches** 页面找到 **Require status checks to pass before merging** 选项：

1. **勾选** `Require status checks to pass before merging`
2. 在搜索框中输入以下 required check 名称（逐个添加）：

**推荐方案 A：只设汇总门禁（推荐）**

只添加 `Skills Gate (汇总门禁)`，简化配置：

```
Skills Gate (汇总门禁)
```

> 这个 job 是 `skills-check.yml` 中的 `skills-gate` job，它会汇总 `skills-consistency` 和 `dynamic-load-gate` 的结果。只需设这一个 required check 即可覆盖所有场景。

**推荐方案 B：分别设每个 Job**

如果需要更细粒度的控制，分别添加：

```
新旧格式元数据一致性
动态加载风险阻断 (HIGH)
```

> **注意**：`nightly-full-scan` 是定时任务，**不要**设为 required check（它只在 schedule/manual 触发时运行，PR 不会触发，会导致 PR 永远无法合并）。
> **注意**：`skills-gate` 是汇总 job，设它即可覆盖 A/B 方案中的所有子 job，无需重复设置。

### 4. 配置其他保护选项（建议）

以下选项与 skills-check 配合使用，提升整体代码质量：

| 选项 | 建议值 | 说明 |
|---|---|---|
| **Require a pull request before merging** | ✅ 勾选 | 强制走 PR 流程，不能直接 push |
| **Required approving reviews** | `1` 或 `2` | 至少需要几人 approve |
| **Dismiss stale pull request approvals when new commits are pushed** | ✅ 勾选 | 新提交会撤销旧 approve，强制重新审查 |
| **Require status checks to pass before merging** | ✅ 勾选 | 核心配置 |
| **Require branches to be up to date before merging** | ✅ 勾选 | 合并前必须 rebase 到最新主分支 |
| **Do not allow bypassing the above settings** | ✅ 勾选 | 禁止 Admin 绕过（严格模式） |

### 5. 保存规则

点击页面底部的 **Create** 或 **Save changes** 按钮。

## 验证配置是否生效

### 方法 1：创建测试 PR

1. 创建一个新分支：`git checkout -b test/skills-check-gate`
2. 做一个微小改动（如修改注释）
3. 推送并创建 PR 到 `master`
4. 在 PR 页面底部 **Checks** 区域应看到：

```
✅ Skills Check / 新旧格式元数据一致性   (成功)
✅ Skills Check / 动态加载风险阻断 (HIGH) (跳过 - 非 push 事件)
✅ Skills Check / Skills Gate (汇总门禁) (成功)
```

5. 尝试点击 **Merge** 按钮——如果所有 required checks 通过，合并按钮应可点击（绿色）

### 方法 2：故意触发失败

1. 修改 `data/skills.json` 中某个技能的 `name` 字段（不更新 `skill.md`）
2. 提交并创建 PR
3. 预期结果：

```
❌ Skills Check / 新旧格式元数据一致性   (失败)
   - compare 检测到 name 不一致
   - 退出码 1

❌ Skills Check / Skills Gate (汇总门禁) (失败)
   - 依赖 job 失败导致汇总失败
```

4. **Merge 按钮应变为不可点击**（红色，显示 "Required checks failed"）

## skills-check workflow 的 Job 触发矩阵

理解每个 Job 的触发时机，避免配置错误的 required check：

| Job | pull_request | push (master) | schedule | workflow_dispatch |
|---|---|---|---|---|
| `skills-consistency` | ✅ 运行 | ✅ 运行 | ❌ 跳过 | ❌ 跳过 |
| `dynamic-load-gate` | ❌ 跳过 | ✅ 运行 | ❌ 跳过 | ❌ 跳过 |
| `nightly-full-scan` | ❌ 跳过 | ❌ 跳过 | ✅ 运行 | ✅ 运行 |
| `skills-gate` | ✅ 运行 | ✅ 运行 | ❌ 跳过 | ❌ 跳过 |

**配置原则**：
- 设为 required check 的 Job 必须在 PR 场景下运行
- `nightly-full-scan` 只在 schedule/manual 时运行，**不可** 设为 required
- `skills-gate` 是汇总门禁，设它一个即可覆盖所有场景

## 常见问题

### Q1: PR 上看不到 `dynamic-load-gate` 检查

**原因**：`dynamic-load-gate` 配置了 `if: github.event_name == 'push'`，PR 场景（pull_request 事件）不会触发它。

**解决**：这是预期行为。HIGH 风险阻断只在 PR 合并后（push 到 master）时运行。若需在 PR 阶段也阻断，修改 `skills-check.yml` 第 80 行：

```yaml
# 原：只在 push 时运行
if: github.event_name == 'push' && (github.ref == 'refs/heads/master' || github.ref == 'refs/heads/main')

# 改为：PR + push 都运行
if: github.event_name != 'schedule'
```

### Q2: required check 搜不到对应的 Job 名称

**原因**：GitHub Actions 的 required check 名称用的是 Job 的 `name` 字段（不是 job ID）。

**对照表**：

| job ID (yaml) | Job name (required check 搜索用) |
|---|---|
| `skills-consistency` | `新旧格式元数据一致性` |
| `dynamic-load-gate` | `动态加载风险阻断 (HIGH)` |
| `nightly-full-scan` | `定期全量扫描` |
| `skills-gate` | `Skills Gate (汇总门禁)` |

> **搜索技巧**：如果搜不到中文名称，可能是历史记录未生成。先让 workflow 跑一次成功，GitHub 才会记录 check 名称供搜索。

### Q3: Admin 仍能强制合并（红色 Merge 按钮）

**原因**：未勾选 `Do not allow bypassing the above settings`。

**解决**：编辑 Branch Protection Rule，勾选该选项。注意这会影响所有 Admin，紧急回滚时需临时关闭规则。

### Q4: PR 显示 "Expected — Waiting for status to be reported"

**原因**：GitHub 尚未收到该 check 的任何运行记录（workflow 从未成功跑过）。

**解决**：
1. 确认 `.github/workflows/skills-check.yml` 已合并到 `master`
2. 手动触发一次 `workflow_dispatch` 让 workflow 产生运行记录
3. 回到 Branch Protection 设置页，重新搜索 required check

### Q5: 修改了 `skills-check.yml` 但 PR 不触发

**原因**：`on.pull_request.paths` 配置了路径过滤，只在特定文件变更时触发。

**检查**：修改的文件是否在 `paths` 列表中（`data/skills.json` / `data/skills_repo/**` / 三个 scripts / conftest.py / test_verify_migrated_skills.py）。如果修改的是无关文件，workflow 不触发是预期行为。

## 紧急回滚

如果 skills-check 误阻断紧急修复：

### 方法 1：临时关闭规则（推荐）

1. Settings → Branches → 找到对应规则
2. 点击 **Edit** → 取消勾选 `Require status checks to pass before merging`
3. 合并紧急 PR
4. **立即重新勾选**，恢复保护

### 方法 2：Gold PR（需提前配置）

在 Branch Protection 中添加 `Allow specified actors to bypass required pull request requests`，指定紧急回滚负责人。

> 不推荐方法 2，因为会永久削弱保护强度。

## 与现有 Branch Protection 共存

如果仓库已有其他 required checks（如 `ci` / `lint` / `test`），skills-check 会**叠加**到现有规则中，所有 required checks 都需通过才能合并。

无需修改现有 workflow，skills-check 是独立 workflow，互不干扰。

## 参考链接

- [GitHub Docs: Managing a branch protection rule](https://docs.github.com/en/repositories/configuring-tools-and-add-ons/github-actions/enabling-required-status-checks)
- [GitHub Docs: About required status checks](https://docs.github.com/en/repositories/configuring-tools-and-add-ons/github-actions/about-required-status-checks)
