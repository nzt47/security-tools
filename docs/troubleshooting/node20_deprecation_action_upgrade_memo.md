# Node.js 20 Deprecation 警告消除技术备忘录

> **创建日期**: 2026-08-04  
> **适用范围**: GitHub Actions 工作流中 `actions/*` 系列 action 的 Node 20 → Node 24 迁移  
> **关联版本**: tlm-hook-failsafe v1.1.4 → v1.1.9（v1.1.9 完成最后一步 action-gh-release@v3 升级，详见 §九）  
> **关联 CI Run**: #30919434635（修复前）/ #30922653841（v5 验证）/ #30925271334（v6 验证）/ v1.1.9（action-gh-release@v3 终态）  
> **关联文档**:
> - [license_migration_and_release_root_cause_memo.md](./license_migration_and_release_root_cause_memo.md) — License 迁移 + Release 修复根因
> - [v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md) — 综合复盘 + 全仓 33 个 workflow 审计 + v1.1.10 规划
> - [release-note-tlm-hook-failsafe-v1.1.4-20260804.md](../releases/release-note-tlm-hook-failsafe-v1.1.4-20260804.md)

---

## 一、背景

GitHub 在 2025-09-19 宣布 [Node.js 20 deprecation on GitHub Actions runners](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)：

- **2026-06-02 起**: Node.js 24 成为默认运行时
- **2026-09-16 起**: Node.js 20 将从 runner 中完全移除

基于 Node 20 的 action 会被强制跑在 Node 24 上，但每次运行都会打印 deprecation 警告。本备忘录记录 tlm-hook-failsafe 发布工作流的迁移经验。

---

## 二、问题现象

### 2.1 CI 日志中的警告

每个 job 的 `Complete job` step 末尾都会打印：

```
##[warning]Node.js 20 is deprecated. The following actions target Node.js 20
but are being forced to run on Node.js 24: actions/checkout@v4, actions/upload-artifact@v4.
For more information see: https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/
```

### 2.2 upload-artifact 内部 DeprecationWarning

`actions/upload-artifact@v4` 的 Node 进程内部还会触发：

```
(node:3536) [DEP0040] DeprecationWarning: The `punycode` module is deprecated.
Please use a userland alternative instead.
(Use `node --trace-deprecation ...` to show where the warning was created)

(node:3536) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized
and is prone to errors that have security implications. Use the WHATWG URL API instead.
CVEs are not issued for `url.parse()` vulnerabilities.
```

---

## 三、根因分析

| Action | v4 | v5 | v6 | 说明 |
|---------|-----|-----|-----|------|
| `actions/checkout` | Node 20 | **Node 24** ✅ | - | v5 已原生支持 Node 24 |
| `actions/upload-artifact` | Node 20 | **Node 20** ❌ | **Node 24** ✅ | **v5 仍基于 Node 20，必须升到 v6** |

**关键陷阱**: 直觉上会认为 `@v4 → @v5` 是大版本升级就能解决，但 `upload-artifact@v5` 仍然停留在 Node 20。这是本备忘录要重点提醒的**反直觉**点。

---

## 四、升级步骤

### 4.1 盘点所有 actions/* 使用点

```powershell
Select-String -Path ".github/workflows/*.yml" -Pattern "uses: actions/" |
  Select-Object LineNumber, Line, Path
```

### 4.2 按版本对应表升级

| 旧版本 | 新版本（Node 24 native） |
|--------|---------------------------|
| `actions/checkout@v3` 或 `@v4` | `actions/checkout@v5` |
| `actions/upload-artifact@v3` 或 `@v4` 或 `@v5` | `actions/upload-artifact@v6` |
| `actions/download-artifact@v4` 或 `@v5` | `actions/download-artifact@v7`（v6 仍 Node 20） |
| `actions/setup-node@v4` | `actions/setup-node@v5` |
| `actions/cache@v4` | `actions/cache@v5` |
| `actions/setup-python@v4` | `actions/setup-python@v6` |
| `actions/github-script@v7` | `actions/github-script@v8` |

### 4.3 替换示例（PowerShell）

```powershell
# checkout: v4 → v5（3 处）
(Get-Content .github/workflows/publish-psgallery.yml) -replace 'actions/checkout@v4', 'actions/checkout@v5' |
  Set-Content .github/workflows/publish-psgallery.yml

# upload-artifact: v4 → v6（跳过 v5！）
(Get-Content .github/workflows/publish-psgallery.yml) -replace 'actions/upload-artifact@v4', 'actions/upload-artifact@v6' |
  Set-Content .github/workflows/publish-psgallery.yml
```

### 4.4 验证升级后的版本

```powershell
Select-String -Path ".github/workflows/publish-psgallery.yml" -Pattern "actions/"
```

期望输出（无 `@v4` 残留）：

```
LineNumber Line
        98 uses: actions/checkout@v5
       190 uses: actions/checkout@v5
       232 uses: actions/upload-artifact@v6
       274 uses: actions/checkout@v5
```

---

## 五、验证清单

### 5.1 本地静态验证

- [ ] `Select-String` 确认无 `@v3` / `@v4` / `@v5`（upload-artifact）残留
- [ ] yml 语法校验（可用 `yq` 或 `actionlint`）

### 5.2 CI 运行时验证

推送后观察 CI run 的 `Complete job` step，确认以下三类日志全部消失：

| 检查项 | 期望 |
|--------|------|
| `##[warning]Node.js 20 is deprecated` | **不再出现** |
| `[DEP0040] DeprecationWarning: punycode` | **不再出现** |
| `[DEP0169] DeprecationWarning: url.parse()` | **不再出现** |

### 5.3 查询命令

```powershell
# 提取 dry-run job 日志中的 Node 20 警告
$runId = "30925271334"
$dryRunJobId = (gh run view $runId --json jobs --jq '.jobs[] | select(.name | contains("Dry-run")) | .databaseId').Trim()
gh run view $runId --log --job=$dryRunJobId |
  Select-String -Pattern "Node 20|deprecated|DEP0040|DEP0169"
```

期望输出为空（无任何匹配）。

---

## 六、常见陷阱

### 6.1 陷阱一：以为 v4 → v5 就够了

`upload-artifact@v5` 仍然基于 Node 20。多个开源项目（[msupply-foundation#12489](https://github.com/msupply-foundation/open-msupply/issues/12489)、[HostsFileEditor#134](https://github.com/scottlerch/HostsFileEditor/pull/134)）都踩过这个坑：

> `actions/upload-artifact@v3`/`@v4` → `@v6`（note: `@v5` is still node20）

**对策**: 升级前查 [action 的 README](https://github.com/actions/upload-artifact) 确认 `runs.using: node24`。

### 6.2 陷阱二：download-artifact 同样有坑

`actions/download-artifact@v6` 仍基于 Node 20，必须升到 `@v7`。

### 6.3 陷阱三：自托管 runner 版本

Node 24 action 要求 Actions Runner **v2.327.1 或更新**。自托管 runner 需先升级 runner 版本，否则 action 加载失败。

查询当前 runner 版本（CI 日志开头）：

```
Current runner version: '2.336.0'  ← 需 ≥ 2.327.1
```

---

## 七、本仓库迁移记录

### 7.1 涉及文件

- `.github/workflows/publish-psgallery.yml`
  - `actions/checkout@v4 → @v5`（3 处：第 98/190/274 行）
  - `actions/upload-artifact@v4 → @v5 → @v6`（1 处：第 232 行）

### 7.2 两步迁移过程

| 步骤 | 修改 | 验证 Run | 结果 |
|------|------|----------|------|
| 第一步 | checkout@v4→v5 + upload-artifact@v4→v5 | #30922653841 | checkout 警告消除 ✅，upload-artifact 警告仍在 ❌ |
| 第二步 | upload-artifact@v5→v6 | #30925271334 | 待验证 |

### 7.3 关键提交

- `a2458b58`：第一步（checkout@v5 + upload-artifact@v5 + LICENSE + 报告）
- `9e340a18`：第二步（upload-artifact@v6 + CHANGELOG 修正 + 备忘录提交预告）

---

## 八、参考链接

- [GitHub 官方弃用公告](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
- [actions/upload-artifact 仓库](https://github.com/actions/upload-artifact)（README 标注 `runs.using: node24`）
- [actions/checkout 仓库](https://github.com/actions/checkout)
- [msupply-foundation Node 24 迁移 issue](https://github.com/msupply-foundation/open-msupply/issues/12489)
- [HostsFileEditor Node 24 bump PR](https://github.com/scottlerch/HostsFileEditor/pull/134)
- [v1.1.4 发布报告](../releases/release-note-tlm-hook-failsafe-v1.1.4-20260804.md)

---

## 九、v1.1.9 终态（action-gh-release@v3 升级）

### 9.1 残留警告发现

v1.1.4 完成 checkout@v5 + upload-artifact@v6 升级后，publish-psgallery.yml 范围内 Node 20 警告基本消除。但 v1.1.8 修复 GitHub Release（详见 [license 文档 §3.2](./license_migration_and_release_root_cause_memo.md)）后，Release step 首次真正运行 —— 才发现 `softprops/action-gh-release@v2` 仍基于 Node 20，触发新的 deprecation 警告。

**这是"修复 A 暴露 B"的典型链式发现**：v1.1.4-v1.1.7 期间 Release step 一直被 skipped，所以 action-gh-release 的 Node 20 警告从未暴露，直到 v1.1.8 修复 Release 创建后才显现。

### 9.2 v1.1.9 升级

```yaml
# publish-psgallery.yml 第 349 行
uses: softprops/action-gh-release@v3   # v2 → v3 (Node 24 native)
```

### 9.3 终态验证

v1.1.9 CI 中 publish-to-psgallery job 所有 step 均 success，**publish-psgallery.yml 范围内 Node 20 deprecation 警告全部消除**。

---

## 十、全仓 GitHub Actions 兼容性审计（2026-08-05）

### 10.1 审计范围

本备忘录原聚焦 publish-psgallery.yml 单文件迁移，但 Node 20 弃用是全仓性问题。2026-08-05 完成全仓 33 个 workflow × 18 类 action 的扫描审计，详见 [v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md §三](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md)。

### 10.2 关键发现

| 项目 | 状态 |
|------|------|
| 已升级 workflow | 仅 `publish-psgallery.yml`（1/33） |
| 仍用 checkout@v4 的 workflow | 32 个，共 96 处违规 |
| 仍用 upload-artifact@v4 的 workflow | 32 个，共 73 处违规（**注意 v5 仍 Node 20**） |
| 仍用 setup-python@v5 的 workflow | 多数 workflow，共 76 处违规（v5 仍 Node 20） |
| 距 9 月 16 日 Node 20 完全移除 | 仅剩 42 天 |

### 10.3 升级优先级与批量升级脚本

详见 [汇总文档 §3.4 升级优先级建议](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md#34-升级优先级建议) 与 [§3.5 批量升级 PowerShell 脚本](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md#35-批量升级-powershell-脚本参考)。

### 10.4 后续行动

- P1 优先级（本周）: ci.yml、ci-cd.yml、observability-ci.yml、test.yml 升级
- P2 优先级（本月）: 其余 28 个 workflow 批量升级 checkout/upload-artifact
- P3 优先级（9 月 16 日前）: setup-python、cache、download-artifact 升级
- v1.1.10 发布规划: 见 [汇总文档 §四](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md#四v1110-发布规划)
