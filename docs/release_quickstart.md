# Release 流程快速上手指南（新成员版）

> 面向第一次接触本仓库发布流程的成员。5 分钟读完即可独立完成一次发布。
> 完整细节见[操作手册](release_workflow_manual.md)；发布前必读[检查清单](release_checklist.md)。

---

## 1. 一句话理解

> **推一个 `vX.Y.Z` 的 git tag，GitHub Actions 自动生成发布备注、创建 GitHub/Gitee Release，
> 失败自动重试 3 次×10s，重试耗尽自动建告警 Issue。**

## 2. 三个 job 在干什么

| job | 职责 | 失败后果 |
|---|---|---|
| `guard` | 检查 tag 的 commit message 是否以 `release(pypi)` 开头（子包发布）→ 是则跳过 | 跳过发布（非失败），但 guard 自身出错会触发告警 |
| `auto-release` | ① 生成发布备注 ② 创建 GitHub Release ③ 同步 Gitee Release | 任一失败重试 3 次×10s，耗尽则失败 |
| `alert-on-failure` | 任一步失败自动建告警 Issue（标题「发布失败告警: {version}」） | — |

## 3. 首次发布五步走

```bash
# ① 确认变更已合并到 master 且 CI 通过
git pull --rebase

# ② 发布前自动检查（Actions → 发布前检查 → Run workflow → 填 version）
#    任一 ❌ 阻断项先修复；⚠️ 警告项手动确认

# ③ 打 annotated tag（注意：commit message 不要以 release(pypi) 开头）
git tag -a v1.1.0 -m "ci(release): 发布 v1.1.0"

# ④ 推送触发工作流
git push origin v1.1.0

# ⑤ 盯日志：Actions → 自动发布 → guard=skip false → GitHub 201 → Gitee 成功
#    验证：GitHub/Gitee Release 页面存在、无新告警 Issue
```

## 4. 发布前检查（Checklist）怎么用

| 工具 | 覆盖范围 | 何时用 |
|---|---|---|
| [release-precheck.yml](../.github/workflows/release-precheck.yml) 工作流 | 自动检查 8 项：版本格式/远端同步/tag 唯一/上一版本/CHANGELOG/GITEE_TOKEN/guard 判定/CI 状态 | 每次发布前手动触发（打 tag 前） |
| [release_checklist.md](release_checklist.md) 手动清单 | 7 段完整清单（含发布后验证、测试发布清理） | 首次发布 / 重要版本逐项勾选 |

**注意**：自动检查只覆盖"可自动化"的部分，D 段（发布过程监控）和 E 段（发布后验证）
必须在运行日志和 Release 页面上人工确认。

## 5. 新项目接入（Template）怎么用

1. 打开 [release_workflow_template.md](release_workflow_template.md)，复制 §2 guard + §3 alert-on-failure 模板
2. 按 **§5 兼容性检查清单 10 项**逐项适配：
   - 子包正则换成自己项目的约定（无子包则删 guard，alert `needs` 只留 auto-release）
   - 顶层 `permissions: contents: write + issues: write`
   - 两处 `checkout` 必须 `fetch-depth: 0`
   - curl 调用带三件套（`|| CODE=500` / `--max-time 30` / `[ -s ]` 容错）
   - Gitee 脚本 `TimeoutSec = 30`；无 Gitee 需求整段删除
3. 跑一次本地模拟验证（见 §7），再上线

## 6. 文档导航（什么时候看哪份）

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| [release_quickstart.md](release_quickstart.md)（本文） | 新手上手指南 | 第一次接触流程 |
| [release_checklist.md](release_checklist.md) | 发布前检查清单 | 每次发布前 |
| [release_workflow_manual.md](release_workflow_manual.md) | 排障/FAQ/退出码经验/验证记录 | 发布失败排查 |
| [release_workflow_template.md](release_workflow_template.md) | 可复用模板 + 兼容性检查 | 新项目接入 |
| [release_workflow_summary.md](release_workflow_summary.md) | 优化总结/演进历程 | 了解背景 |
| [release_workflow_retrospective.md](release_workflow_retrospective.md) | 技术分享文章 | 培训/复盘 |

## 7. 常见问题速查

- **tag 被 guard 跳过（skip=true）？** 该 tag 的 commit message 以 `release(pypi)` 开头——这是子包发布约定，主项目发布换个 message。
- **GitHub/Gitee 报 409/422？** tag 已存在 Release（幂等冲突不重试）。已发布就改 `-Update` 更新模式；误发就先删 tag。
- **Gitee step 显示 Skipped？** `GITEE_TOKEN` 未配置——安全跳过，不是故障（确认是预期即可）。
- **重试耗尽自动告警了？** 打开告警 Issue 里的运行链接，看失败 step 的响应体 `message` 字段（401=token、404=仓库、5xx=服务端），对照手册 §6。
- **本地想模拟发布流程？** 用测试 tag + mock API，注意 Windows/WSL 三坑（用 `python.exe`/`pwsh.exe`/Windows curl + `wslpath -w`），详见手册 §10 Q2。

## 8. 名词表

| 术语 | 含义 |
|---|---|
| annotated tag | 带 message 的 git tag（`git tag -a`），guard 读取其 commit message |
| 子包发布 | l2-p99-monitor 等子包走 PyPI，其 tag 以 `release(pypi)` 开头 |
| 幂等冲突 | 同一 tag 重复创建 Release 返回 409/422，重试无意义 |
| Step Summary | 工作流运行页的 Markdown 检查报告区（precheck 结果展示） |
