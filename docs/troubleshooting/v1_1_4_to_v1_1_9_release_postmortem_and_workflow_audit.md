# tlm-hook-failsafe v1.1.4 → v1.1.9 完整修复链复盘与全仓 action 审计

> **创建日期**: 2026-08-05  
> **适用范围**: tlm-hook-failsafe PSGallery 发布链路 + 全仓 GitHub Actions Node 20 → Node 24 迁移状态  
> **关联版本**: v1.1.4 → v1.1.9（6 个版本迭代）  
> **本文定位**: 入口汇总文档，串联两份子文档并补充 v1.1.9 终态、全仓审计、v1.1.10 规划  
> **关联子文档**:
> - [node20_deprecation_action_upgrade_memo.md](./node20_deprecation_action_upgrade_memo.md) — v1.1.4 时点 checkout/upload-artifact 升级
> - [license_migration_and_release_root_cause_memo.md](./license_migration_and_release_root_cause_memo.md) — v1.1.5-v1.1.8 License 迁移 + Release 修复根因

---

## 一、修复链总览

### 1.1 v1.1.4 → v1.1.9 六个版本迭代

| 版本 | 主题 | 关键修改 | 子文档章节 |
|------|------|----------|------------|
| v1.1.4 | Node 20 警告初治 | `actions/checkout@v4→v5` × 3 处 + `actions/upload-artifact@v4→v6` × 1 处（**v5 仍 Node 20，必须直接跳到 v6**） | node20 §四、§七 |
| v1.1.5 | License 启用 | `.psd1` 启用 `LicenseUri` + LICENSE 文件入包 + sync 脚本双重 BOM 修复 | license §2.2 阶段 1 |
| v1.1.6 | nuspec 添加 licenseUrl | .nuspec 模板加 `<licenseUrl>` + XML 转义防 ReleaseNotes 尖括号破坏结构 | license §2.2 阶段 2 |
| v1.1.7 | License 迁移到 SPDX 表达式 + Release if 扩展 | `<license type="expression">MIT</license>` + Release step if 覆盖三种触发路径 | license §2.2 阶段 3、§3.2 根因 1 |
| v1.1.8 | Release 修复（needs 显式声明） | publish job 显式 `needs: [auto-tag, dry-run-validate]`（**传递依赖 outputs 不可访问**） | license §3.2 根因 2 |
| **v1.1.9** | **Node 20 警告终治** | `softprops/action-gh-release@v2→v3`（**v3 = Node 24 native，消除最后一条警告**） | **本文 §二** |

### 1.2 v1.1.9 终态验证（CI Run）

license 文档 §5.3 明确指出 v1.1.8 残留"`action-gh-release@v2` Node 20 警告，待下个版本验证"。v1.1.9 升级到 `@v3` 后：

```
publish-to-psgallery job 所有 step 均为 success ✅
- 检出代码 (actions/checkout@v5)                          success
- 发布到 PSGallery                                         success
- 创建 GitHub Release (softprops/action-gh-release@v3)    success  ← v1.1.8 残留警告消除
- Complete job                                            success  ← 无 Node 20 deprecation 警告
```

CI 日志中以下三类警告**全部消失**：

| 警告来源 | v1.1.8 状态 | v1.1.9 状态 |
|----------|-------------|-------------|
| `##[warning]Node.js 20 is deprecated ... actions/checkout@v4, actions/upload-artifact@v4` | ❌ 残留（publish-psgallery.yml 自身已修，但其他 workflow 仍触发） | ✅ publish-psgallery.yml 范围内已消除 |
| `##[warning]Node.js 20 is deprecated ... softprops/action-gh-release@v2` | ❌ v1.1.8 残留 | ✅ 升级 @v3 后消除 |
| `[DEP0040] punycode DeprecationWarning` + `[DEP0169] url.parse() DeprecationWarning` | ❌ upload-artifact@v4 内部触发 | ✅ upload-artifact@v6 已消除 |

### 1.3 三义校验

- **【不易】** PSGallery API key 仅从 Secrets 读取、同版本不可重发、版本号是唯一真相源 —— 6 个版本迭代中此三不变量始终未被破坏
- **【变易】** 修复按"先治表（Node 20 警告）→ 再治里（License 元数据）→ 后治隐（needs 传递依赖）→ 终治本（action-gh-release Node 24）"分步演进，每步可独立验证回滚
- **【简易】** 每个修复点最小变更（1-3 行），不引入新工具链；publish-psgallery.yml 当前 action 组合 `checkout@v5 + upload-artifact@v6 + action-gh-release@v3` 即为 v1.1.10+ 可直接复用的稳定基线

---

## 二、v1.1.9 action-gh-release 升级详情

### 2.1 升级原因

v1.1.8 修复 `needs` 显式声明后，Release step 首次真正运行 —— 才发现 `softprops/action-gh-release@v2` 仍基于 Node 20，触发新的 deprecation 警告。这是"修复 A 暴露 B"的典型链式发现。

### 2.2 关键代码变更

```yaml
# publish-psgallery.yml 第 349 行
uses: softprops/action-gh-release@v3   # v2 → v3 (Node 24 native)
with:
  tag_name: v${{ steps.version.outputs.version }}
  name: tlm-hook-failsafe v${{ steps.version.outputs.version }}
  body: |
    ## tlm-hook-failsafe v${{ steps.version.outputs.version }}
    ...
```

### 2.3 验证维度

- **静态**: `Select-String -Pattern "action-gh-release@v[12]"` 全仓无匹配
- **运行时**: v1.1.9 CI 的 Complete job step 无 Node 20 deprecation 警告
- **业务**: GitHub Release v1.1.9 创建成功，body 内容正确，tag_name 显式构造不依赖 `github.ref_name`

---

## 三、全仓 GitHub Actions 兼容性审计（2026-08-05）

### 3.1 审计方法

```powershell
# 扫描所有 workflow 的 action 引用并按版本聚合
Get-ChildItem .github/workflows/*.yml |
  Select-String -Pattern "uses:\s*([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)@v([0-9]+)" -AllMatches |
  ForEach-Object { $_.Matches } |
  ForEach-Object { "$($_.Groups[1].Value)@$($_.Groups[2].Value)" } |
  Group-Object | Sort-Object Count -Descending
```

### 3.2 全量 action 使用矩阵（33 个 workflow × 18 类 action）

| Action | 当前主版本 | 使用次数 | Node 24 native 最新版本 | 当前是否合规 | 升级目标 |
|--------|-----------|----------|------------------------|--------------|----------|
| actions/checkout | v4 | **99** | v6.0.2（v5 已合规） | ❌ 96 处违规 | v4 → v5（最低）/ v6（最新） |
| actions/setup-python | v5 | **76** | v6 | ❌ 全部违规 | v5 → v6 |
| actions/upload-artifact | v4 | **74** | v7.0.1（v6 已合规） | ❌ 73 处违规 | v4 → v6（最低）/ v7（最新） |
| actions/github-script | v7 | 18 | 待查证（可能 v7 已合规） | ⚠️ 需查证 | 确认 v7 是否 Node 24 |
| actions/download-artifact | v4 | 12 | v8（v7 仍 Node 20） | ❌ 全部违规 | v4 → v8（跳过 v6/v7） |
| actions/cache | v4 | 12 | v5（Node 24）/ v6（最新） | ❌ 全部违规 | v4 → v5（最低） |
| actions/setup-node | v4 | 3 | v5 | ❌ 全部违规 | v4 → v5 |
| slackapi/slack-github-action | v1 | 3 | 需查证 | ⚠️ 需查证 | 确认 v1.x 是否 Node 24 |
| docker/setup-buildx-action | v3 | 3 | v3 已是 Node 20+，需查证 | ⚠️ 需查证 | 确认最新版本 |
| actions/upload-pages-artifact | v3 | 2 | v4 | ❌ 全部违规 | v3 → v4 |
| docker/build-push-action | v5 | 2 | v5/v6 待查证 | ⚠️ 需查证 | 确认最新版本 |
| actions/deploy-pages | v4 | 2 | v4 待查证 | ⚠️ 需查证 | 确认 v4 是否 Node 24 |
| actions/configure-pages | v5 | 1 | v5 已合规 | ✅ | — |
| actions/checkout | v5 | **3** | v6.0.2 | ✅ 已合规 | — |
| actions/upload-artifact | v6 | **1** | v7.0.1 | ✅ 已合规 | — |
| codecov/codecov-action | v4 | 1 | v5 | ⚠️ 需查证 | 确认 v5 是否 Node 24 |
| dawidd6/action-send-mail | v3 | 1 | 需查证 | ⚠️ 需查证 | 确认最新版本 |
| docker/login-action | v3 | 1 | v3 待查证 | ⚠️ 需查证 | 确认最新版本 |
| softprops/action-gh-release | v3 | 1 | v3（Node 24 native） | ✅ 已合规 | — |

### 3.3 风险评估

**当前日期 2026-08-05，距 Node 20 完全移除（2026-09-16）仅剩 42 天**。

#### 风险分级

| 风险等级 | 项目 | 说明 | 影响 |
|----------|------|------|------|
| 🔴 **高危** | `actions/checkout@v4` × 96 处 | 32 个 workflow 仍在用，每次运行触发警告 | 9 月 16 日后 workflow 直接失败 |
| 🔴 **高危** | `actions/upload-artifact@v4` × 73 处 | 同上，且 v5 仍 Node 20（**反直觉陷阱**） | 同上 |
| 🟡 **中危** | `actions/setup-python@v5` × 76 处 | v5 仍是 Node 20，需升到 v6 | 同上 |
| 🟡 **中危** | `actions/cache@v4` × 12 处 | 需升到 v5+ | 同上 |
| 🟡 **中危** | `actions/download-artifact@v4` × 12 处 | 需直接跳到 v8（v6/v7 仍 Node 20） | 同上 |
| 🟢 **低危** | `actions/github-script@v7` 等"待查证"项 | 需逐项确认是否已 Node 24 | 待确认后定级 |

#### 高危项目分布

仅 `publish-psgallery.yml`（1/33）已完成全量升级：

```
已升级 workflow: publish-psgallery.yml (3 处 checkout@v5 + 1 处 upload-artifact@v6 + 1 处 action-gh-release@v3)
未升级 workflow: 其余 32 个文件
```

### 3.4 升级优先级建议

按"业务影响 × 修复成本"排序：

| 优先级 | 范围 | 行动 |
|--------|------|------|
| **P0**（立即） | `publish-psgallery.yml` | ✅ 已完成 |
| **P1**（本周） | `ci.yml`、`ci-cd.yml`、`observability-ci.yml`、`test.yml` | 使用次数最多、影响最广，先升 |
| **P2**（本月） | 其余 28 个 workflow | 批量替换 `checkout@v4→v5` + `upload-artifact@v4→v6` |
| **P3**（9 月 16 日前） | setup-python、cache、download-artifact | 升级到 Node 24 native 版本 |
| **P4**（查证后） | github-script、slack、docker 系列、codecov | 逐个查证最新版本是否 Node 24 |

### 3.5 批量升级 PowerShell 脚本（参考）

```powershell
# 一次性升级 checkout + upload-artifact（最低合规版本）
$workflows = Get-ChildItem .github/workflows/*.yml
foreach ($wf in $workflows) {
    $content = Get-Content $wf.FullName -Raw
    $original = $content
    $content = $content -replace 'actions/checkout@v4', 'actions/checkout@v5'
    $content = $content -replace 'actions/upload-artifact@v4', 'actions/upload-artifact@v6'
    # 注意：不要无脑替换 v5（upload-artifact v5 仍是 Node 20）
    # 注意：download-artifact 需直接跳到 v8，不是 v6
    if ($content -ne $original) {
        Set-Content -Path $wf.FullName -Value $content -NoNewline
        Write-Host "[OK] updated $($wf.Name)" -ForegroundColor Green
    }
}
```

**警告**: 此脚本不处理 `actions/setup-python@v5→v6`、`actions/cache@v4→v5`、`actions/download-artifact@v4→v8` 等需要查证的 action，需逐项确认后再升级。

---

## 四、v1.1.10 发布规划

### 4.1 CI 配置可复用性确认

**结论**: publish-psgallery.yml 当前配置可直接复用，无需任何修改即可发布 v1.1.10。

| 检查项 | 状态 | 说明 |
|--------|------|------|
| checkout@v5 × 3 处 | ✅ | Node 24 合规 |
| upload-artifact@v6 × 1 处 | ✅ | Node 24 合规 |
| action-gh-release@v3 × 1 处 | ✅ | Node 24 native |
| publish job `needs: [auto-tag, dry-run-validate]` | ✅ | 传递依赖 outputs 已修复 |
| Release step if 三路径覆盖 | ✅ | tag push / auto-tag / workflow_dispatch 均覆盖 |
| `tag_name: v${{ steps.version.outputs.version }}` | ✅ | 显式构造，不依赖 github.ref_name |
| `permissions: contents: write` + `actions: write` | ✅ | 可创建 tag 和触发 workflow_dispatch |
| `<license type="expression">MIT</license>` | ✅ | NuGet 4.9.2+ 推荐，无 NU5125 警告 |
| sync-from-source.ps1 期望 16 函数 | ✅ | 与源 .psm1 一致 |
| .psd1 ReleaseNotes 模板 | ✅ | 已累积 v1.1.0-v1.1.9 历史 |

### 4.2 v1.1.10 bump 流程

#### 4.2.1 必须修改的文件（最小变更）

仅 1 个文件需修改：

**`packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1`**

```powershell
# 第 15 行
ModuleVersion = '1.1.10'    # 1.1.9 → 1.1.10

# 第 118 行 ReleaseNotes 头部追加 v1.1.10 条目
ReleaseNotes = 'v1.1.10: <本次变更说明>. v1.1.9: upgrade softprops/action-gh-release@v2 to @v3 ...'
```

#### 4.2.2 可选修改（如本次有功能变更）

若 v1.1.10 包含源码变更，还需修改：

- `scripts/dev/hook_fail_safe.psm1` — 真相源
- `packages/tlm-hook-failsafe/sync-from-source.ps1` — 若新增/删除导出函数（更新 `$expected` 数组）

#### 4.2.3 触发流程

```powershell
# 1. 修改 .psd1 版本号和 ReleaseNotes
# 2. 若源码变更，先 sync 验证
& .\packages\tlm-hook-failsafe\sync-from-source.ps1
# 期望输出: [OK] verified 16 exported functions

# 3. 本地 dry-run 验证 .nupkg 可生成
& .\packages\tlm-hook-failsafe\publish-to-psgallery.ps1 -NuGetApiKey 'dummy' -DryRun -SkipVersionCheck

# 4. 提交（注意：使用 git add 后普通 commit，不要用 git commit -- <paths>）
git add packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1
git commit -m "chore(tlm-hook-failsafe): bump to v1.1.10"

# 5. 推送（触发 auto-tag → dry-run → publish 全链路）
git push origin master
```

#### 4.2.4 触发后的工作流自动行为

| 阶段 | Job | 触发条件 | 验证方式 |
|------|-----|----------|----------|
| 1 | auto-tag | master push + 版本变化 | 检查 `v1.1.10` tag 是否创建 |
| 2 | dry-run-validate | auto-tag 完成或 PR | 检查 .nupkg 生成 + 16 函数验证 |
| 3 | publish-to-psgallery | auto-tag.outputs.tagged == 'true' | PSGallery 出现 v1.1.10 |
| 4 | GitHub Release | publish 成功 + if 三路径满足 | GitHub Release v1.1.10 创建 |

### 4.3 验证清单

#### 4.3.1 发布前本地验证

- [ ] `.psd1` ModuleVersion 改为 `1.1.10`
- [ ] `.psd1` ReleaseNotes 头部追加 v1.1.10 条目
- [ ] `sync-from-source.ps1` 输出 `[OK] verified 16 exported functions`
- [ ] `publish-to-psgallery.ps1 -DryRun` 生成 `tlm-hook-failsafe.1.1.10.nupkg`
- [ ] pre-commit hook 通过（编码检查 / 不变量校验 / CI 守卫）
- [ ] pre-push hook 通过（核心不变量校验）

#### 4.3.2 发布后 CI 验证

- [ ] auto-tag job 创建 `v1.1.10` tag
- [ ] dry-run-validate job 中 `.nupkg` 生成成功
- [ ] publish-to-psgallery job 各 step 均 success
- [ ] GitHub Release v1.1.10 创建，body 包含 PSGallery 链接
- [ ] PSGallery `Find-Module tlm-hook-failsafe` 返回 Version=1.1.10
- [ ] CI 日志中无 Node 20 deprecation 警告

### 4.4 v1.1.10 内容建议

由于 v1.1.9 已完成 Node 20 警告终治，v1.1.10 候选内容：

| 候选 | 描述 | 推荐度 |
|------|------|--------|
| **A. 纯验证版** | 仅 bump 版本号，验证 CI 链路稳定性 | ⭐⭐ 推荐作为 dry-run 验证 |
| **B. sync 脚本同步升级** | 若有未提交的源 .psm1 改动需同步 | ⭐⭐⭐ 推荐若源码有变更 |
| **C. 全仓 action 批量升级** | 不建议塞入 v1.1.10，应作为独立 chore PR | ⭐ 不推荐，scope 过大 |

**建议**: 若源码无变更，选 A（纯验证版）；若有源码变更，选 B。

---

## 五、待办事项与建议

### 5.1 短期待办（v1.1.10 前）

- [ ] 确认 v1.1.10 内容定位（验证版 / 源码同步版）
- [ ] 执行 4.2.3 的 bump 流程

### 5.2 中期待办（9 月 16 日前）

- [ ] P1 优先级 4 个 workflow 升级（ci.yml、ci-cd.yml、observability-ci.yml、test.yml）
- [ ] P2 优先级 28 个 workflow 批量升级 checkout/upload-artifact
- [ ] P3 优先级 setup-python、cache、download-artifact 升级
- [ ] P4 优先级查证 github-script、slack、docker、codecov 等最新版本

### 5.3 长期待办

- [ ] 建立 Dependabot 自动监控 actions/* 版本（参考 license 文档未提及的 .github/dependabot.yml）
- [ ] 考虑将 action 版本统一到 v6/v7（最新而非最低合规版本）

### 5.4 风险提示

- **9 月 16 日硬截止**: Node 20 从 runner 完全移除，未升级的 workflow 将直接失败
- **GitHub Pages 内置 workflow**: `pages-build-deployment` 由 GitHub 管理无法手动升级，但其警告不影响业务（参见 [GitHub 社区讨论 #193381](https://github.com/orgs/community/discussions/193381)）
- **临时强制 Node 24**: 若 9 月 16 日前无法完成升级，可设置 `env: FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true` 临时缓解，但非长久之计

---

## 六、参考链接

- [GitHub 官方弃用公告（2025-09-19，2026-05-19 更新迁移日期为 6 月 16 日）](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
- [Node 20 deprecation 备忘录（v1.1.4 时点）](./node20_deprecation_action_upgrade_memo.md)
- [License 迁移与 Release 根因分析（v1.1.5-v1.1.8）](./license_migration_and_release_root_cause_memo.md)
- [actions/checkout 仓库](https://github.com/actions/checkout)
- [actions/upload-artifact 仓库](https://github.com/actions/upload-artifact)
- [actions/cache 仓库](https://github.com/actions/cache)（v5 = Node 24）
- [actions/setup-python 仓库](https://github.com/actions/setup-python)（v6 = Node 24）
- [softprops/action-gh-release 仓库](https://github.com/softprops/action-gh-release)（v3 = Node 24）
- [GitHub 社区讨论 #193381（pages-build-deployment 内置 workflow 警告）](https://github.com/orgs/community/discussions/193381)
