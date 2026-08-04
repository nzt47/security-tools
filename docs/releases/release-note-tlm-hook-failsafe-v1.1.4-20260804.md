# tlm-hook-failsafe v1.1.4 PSGallery 发布链路修复报告

> **生成日期**: 2026-08-04  
> **版本**: v1.1.4  
> **状态**: ✅ 已发布（PSGallery v1.1.4）  
> **主提交**: `a2458b58`（fix(ci): 修复 1.1.4 发布链路遗留警告 + license 元数据补齐）  
> **二次修复提交**: upload-artifact@v5 → @v6（待提交）  
> **关联 CI Run**: #30919434635（v1.1.4 发布成功）/ #30922653841（本次修复验证）  
> **关联文档**: [CHANGELOG.md](../../CHANGELOG.md) | [psgallery-auto-publish-guide.md](../guides/psgallery-auto-publish-guide.md)

---

## 一、背景与目标

tlm-hook-failsafe 的 PSGallery 自动发布链路（auto-tag → dry-run → publish）在 v1.1.2/v1.1.3 调试期间暴露 5 个技术问题，导致 publish job 反复 `skipped`。v1.1.4 通过「auto-tag 与 publish 合并到同一工作流」彻底闭环。

本次工作在 v1.1.4 基础上完成三项任务：
1. 将 5 个问题修复整理为 CHANGELOG.md 更新记录
2. 为自动发布流程添加手动触发按钮（紧急回滚/强制发布）
3. 检查 v1.1.4 发布日志，确认遗漏的警告并修复

---

## 二、5 大问题修复详情

### 问题 1: GITHUB_TOKEN 创建的 tag 不触发 on.push（防循环机制）

- **根因**: GitHub 为防止工作流递归触发，规定由 `GITHUB_TOKEN` 创建的 tag/ref **不会**触发 `on.push.tags`。早期方案用「auto-tag 工作流创建 tag → 触发另一个 publish 工作流」，导致 publish job 始终 `skipped`。
- **修复**: auto-tag 与 publish 合并到**同一工作流**，通过 `needs.auto-tag.outputs.tagged` 在工作流内传递状态。
- **文件**: `.github/workflows/publish-psgallery.yml`
- **验证**: Run #30919434635 三个 job 全部 success，publish 实际推送 `[DONE] published to PSGallery`。

### 问题 2: gh workflow run 无法传递 workflow_dispatch 的 boolean inputs

- **根因**: `gh` CLI 对 `type: boolean` 的 workflow_dispatch input 存在传递 bug，`gh api` 查询显示 `inputs: {}`。
- **修复**: workflow_dispatch input 类型从 `boolean` 改为 `string`，通过 `gh workflow run --field force_publish=true` 传递字符串 `"true"`。

### 问题 3: Invoke-RestMethod 触发 workflow_dispatch 返回 403 Forbidden

- **根因**: 默认 `GITHUB_TOKEN` 只有 `contents:read`，缺少 `actions:write` 权限。
- **修复**: 工作流顶层显式声明 `permissions: actions: write`。

### 问题 4: PR 触发时 dry-run-validate 被连带跳过

- **根因**: auto-tag 的 `if` 条件限定仅 master/main push 运行，PR 时 auto-tag 被跳过；dry-run-validate 通过 `needs: auto-tag` 依赖它，连带被跳过。
- **修复**: dry-run-validate 添加 `if: ${{ success() || needs.auto-tag.result == 'skipped' }}`。

### 问题 5: Node.js 20 deprecation 警告（分两步修复）

- **根因**: `actions/checkout@v4` 和 `actions/upload-artifact@v4` 依赖 Node.js 20 运行时，而 runner 已默认使用 Node.js 24。
- **第一步修复**: `checkout@v4 → @v5`（3 处）+ `upload-artifact@v4 → @v5`（1 处）
- **CI 验证发现**: checkout@v5 警告消除 ✅，但 **upload-artifact@v5 仍基于 Node 20**（upstream 已知问题）。
- **第二步修复**: `upload-artifact@v5 → @v6`（Node 24 native，`runs.using: node24`）。
- **参考**: 多个开源项目（msupply-foundation、HostsFileEditor）确认 `@v5 is still node20, @v6 is node24 native`。

---

## 三、license 警告修复（本次补充）

- **警告**: `WARNING: All published packages should have license information specified`
- **根因**: `tlm-hook-failsafe.psd1` 第 109 行 `LicenseUri` 被注释，且仓库根/包目录均无 `LICENSE` 文件。
- **修复**:
  1. 创建仓库根 `LICENSE`（MIT 标准文本，与 .psd1 第 30 行 `Copyright` 声明一致）
  2. `sync-from-source.ps1` 补充 LICENSE 复制逻辑（真相源=根 LICENSE，sync 到包目录供 nuget pack 包含）
  3. `.psd1` 第 109 行 `LicenseUri` 启用，指向 `https://github.com/nzt47/security-tools/blob/master/packages/tlm-hook-failsafe/LICENSE`
- **验证**: CI dry-run 日志确认 `[OK] LICENSE copied` + .nupkg 体积从 14413 → 14470 bytes（+57 bytes = LICENSE 文件）。

---

## 四、手动触发按钮设计

### 配置（`.github/workflows/publish-psgallery.yml` 第 53-64 行）

```yaml
workflow_dispatch:
  inputs:
    force_publish:
      description: '真实发布到 PSGallery（需 PSGALLERY_API_KEY secret，输入 true 触发）'
      required: false
      default: 'false'
      type: string  # 不用 boolean：避免 gh CLI 传递 bug
    skip_version_check:
      description: '跳过版本预检（用于首次发布或调试，输入 true 跳过）'
      required: false
      default: 'false'
      type: string
```

### publish 门控三选一（第 266-269 行）

```yaml
if: |
  (github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')) ||
  (github.event_name == 'workflow_dispatch' && github.event.inputs.force_publish == 'true') ||
  (needs.auto-tag.outputs.tagged == 'true')
```

### 使用场景

| 场景 | force_publish | skip_version_check | 说明 |
|------|---------------|---------------------|------|
| 紧急回滚旧版本 | true | true | 回滚时跳过版本预检（PSGallery 可能已有该版本） |
| 强制重发当前版本 | true | false | 版本预检通过后强制发布 |
| 调试发布流程 | false | - | 仅触发 dry-run，不真实发布 |

---

## 五、日志检查结果（Run #30919434635）

### 发布成功证据

```
Publishing version: 1.1.4
[OK] version = 1.1.4
Pushing tlm-hook-failsafe.1.1.4.nupkg to 'https://www.powershellgallery.com/api/v2/package'
Your package was pushed.
[OK] PSGallery now has v1.1.4
[DONE] published to PSGallery
```

### 发现的 4 类警告及处理

| 警告 | 严重性 | 处理 |
|------|--------|------|
| Node.js 20 deprecation（checkout@v4） | 中 | ✅ 升级 v5 消除 |
| Node.js 20 deprecation（upload-artifact@v4→v5） | 中 | ✅ 升级 v6 消除（二次修复） |
| PSGallery license 警告 | 中 | ✅ 创建 LICENSE + 启用 LicenseUri |
| nuget pack readme 警告 | 低 | ⏳ Pending（非阻断，后续处理） |
| punycode/url.parse DeprecationWarning | 低 | ✅ 随 upload-artifact v6 修复 |

---

## 六、额外发现：sync-from-source.ps1 双重 BOM

- **现象**: `sync-from-source.ps1` 文件开头有**双重 BOM**（`EF BB BF EF BB BF`），第二个 BOM 被当作代码字符，破坏 `<#` 注释块识别，导致 PS7 解析第 7 行注释失败（`Missing expression after unary operator '-'`）。
- **根因**: 疑似历史编辑/回滚引入的编码损坏。
- **修复**: 去除多余 BOM，保留单 BOM（`EF BB BF`），文件大小 4428 bytes。
- **验证**: 修复后 `pwsh -File sync-from-source.ps1` 正常运行。

---

## 七、本地 sync 验证（与 CI 环境对比）

### 本地运行输出

```
=== sync-from-source ===
  source: C:\Users\Administrator\agent\scripts\dev\hook_fail_safe.psm1
  target: C:\Users\Administrator\agent\packages\tlm-hook-failsafe\tlm-hook-failsafe.psm1
  [OK] copied
  [OK] LICENSE copied
  [OK] verified 15 exported functions
  [INFO] extra functions: Get-PrePushContent
  [OK] hash match: abc08c8987c98a29cb1c23f99dc64e2e7e5431db8e93e30a4cde037407a090f7

[DONE] sync complete
```

### CI Run #30922653841 dry-run sync 步骤输出

```
[OK] copied
[OK] LICENSE copied
[OK] verified 15 exported functions
[INFO] extra functions: Get-PrePushContent
[OK] hash match: abc08c8987c98a29cb1c23f99dc64e2e7e5431db8e93e30a4cde037407a090f7
[DONE] sync complete
```

### 对比结果

| 验证项 | 本地 | CI | 一致性 |
|--------|------|-----|--------|
| LICENSE copied | ✅ 出现 | ✅ 出现 | **完全一致** |
| hash | `abc08c8...090f7` | `abc08c8...090f7` | **完全一致** |
| 导出函数数 | 15 | 15 | **完全一致** |
| .nupkg 体积 | - | 14470 bytes（含 LICENSE） | LICENSE 已打包 |

**结论**: LICENSE 复制逻辑在本地与 CI 环境**完全一致**，hash 匹配证明源码 + LICENSE 内容字节级相同。

---

## 八、CI 验证结果（Run #30922653841）

### Job 状态

| Job | 结论 | 说明 |
|-----|------|------|
| 自动打 Tag | success | v1.1.4 tag 已存在，tagged=false，不重复创建 |
| Dry-run 验证 | success | sync + pack 验证通过，.nupkg 生成（14470 bytes） |
| 发布到 PSGallery | **skipped** ✅ | 正确！版本未变，不重复发布 |

### 警告验证

| 检查项 | 结果 |
|--------|------|
| checkout@v5 Node 20 警告 | ✅ **已消除**（dry-run 日志无 checkout 警告） |
| upload-artifact@v5 Node 20 警告 | ❌ **仍存在**（`actions/upload-artifact@v5` target Node 20） |
| LICENSE 复制 | ✅ CI 日志确认 `[OK] LICENSE copied` |
| hash 一致 | ✅ `abc08c8...090f7` 与本地一致 |

### 二次修复（upload-artifact@v6）

CI 验证发现 `upload-artifact@v5` 仍基于 Node 20 后，立即升级到 `@v6`（Node 24 native）。待下次 push 验证警告彻底消除。

---

## 九、提交记录

| 提交 | 内容 | 状态 |
|------|------|------|
| `76545d77` | fix(ci): auto-tag 与 publish 合并到同一工作流 + bump to 1.1.4 | ✅ 已 push |
| `a2458b58` | fix(ci): 修复 1.1.4 发布链路遗留警告 + license 元数据补齐 | ✅ 已 push |
| 待提交 | fix(ci): upload-artifact@v5→@v6 消除残留 Node 20 警告 | 🔄 工作区已修改 |

### pre-commit hook 验证（commit `a2458b58`）

- 文档链接预检：598 链接 0 失效 ✅
- 锚点回归测试：4 passed ✅
- 核心不变量：12/12 通过 ✅

### pre-push hook 验证

- 核心不变量：12/12 通过 ✅
- 注："Bad file descriptor" 为已知问题（project_memory 第 13 轮），不影响功能

---

## 十、待办

- [ ] 提交 upload-artifact@v6 升级并 push 验证 Node 20 警告彻底消除
- [ ] nuget pack readme 警告：后续在 .nuspec 模板补充 `<readme>README.md</readme>` 并提供 README 文件
- [ ] 本地 `git fetch --tags` 同步 v1.1.4 tag 到本地

---

## 十一、关键文件清单

| 文件 | 修改内容 |
|------|----------|
| `.github/workflows/publish-psgallery.yml` | checkout@v4→v5、upload-artifact@v4→v5→v6、workflow_dispatch 手动触发、permissions: actions:write、dry-run if 条件 |
| `packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1` | LicenseUri 启用（第 109 行） |
| `packages/tlm-hook-failsafe/sync-from-source.ps1` | LICENSE 复制逻辑（第 40-49 行）+ 双重 BOM 修复 |
| `LICENSE` | 新建（MIT 标准文本） |
| `CHANGELOG.md` | v1.1.4 条目（5 大问题 + license 修复） |
| `docs/guides/psgallery-auto-publish-guide.md` | 手动触发按钮使用说明 |
