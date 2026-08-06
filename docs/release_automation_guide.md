# Release 自动化指南（Gitee / GitHub）

> **定位**: 将 [create_gitee_release.ps1](../scripts/create_gitee_release.ps1)（诊断 + 创建/更新）与
> [update_changelog.py](../scripts/update_changelog.py)（CHANGELOG + 发布备注）整合为一条龙流程。
> **来源**: v1.0.0 发布实操沉淀（含 400/404/401 三连错 → PowerShell 变量拼接 Bug 事故复盘）。
> **配套**: [gitee_release_api_troubleshooting.md](./troubleshooting/gitee_release_api_troubleshooting.md)（排查细节）、
> [RELEASE_PROCESS_TEMPLATE.md](../RELEASE_PROCESS_TEMPLATE.md)（8 阶段模板）。

---

## 1. 工具链总览

| 阶段 | 工具 | 作用 |
|---|---|---|
| 0 环境准备 | 手动 | GITEE_TOKEN（勾选 `projects` 权限） |
| 1 环境诊断 | `create_gitee_release.ps1 -Diagnose` | token / 仓库 / 已有 releases 三步校验 |
| 2 发布备注 | `update_changelog.py` | git log → 分类 changelog + 发布备注 |
| 3 创建/更新 | `create_gitee_release.ps1`（POST / `-Update` PATCH） | 基于 tag 发布（已存在则更新） |
| 4 验证 | `release-readiness-action` / `verify_release_readiness.py` | 10 项就绪检查 + Release 事件 CI |

## 2. 步骤 0：环境准备

```powershell
# Gitee → 设置 → 私人令牌 → 生成新令牌（勾选 projects 权限）
$env:GITEE_TOKEN = "<令牌>"    # 会话级，需每次设置或写入 .env（勿提交）
```

## 3. 步骤 1：环境诊断

```powershell
pwsh -NoProfile -File scripts\create_gitee_release.ps1 -Diagnose
```

三步全绿（`exit 0`）才继续；任一步 FAIL 按状态码速查表定位（见 §7）。

## 4. 步骤 2：发布备注 + CHANGELOG

```bash
# 预览发布备注（stdout，不落盘）
python scripts/update_changelog.py --version v1.1.0 --prev-tag v1.0.0

# 写入 CHANGELOG.md 顶部 + 导出发布备注文件
python scripts/update_changelog.py --version v1.1.0 --write --out notes.md
```

生成的发布备注即 Release 正文来源（分类：新功能 / Bug 修复 / 性能优化 / 重构 / 文档 / 测试 / CI/CD）。

## 5. 步骤 3：创建 / 更新 Release

```powershell
# 创建（tag 须已推送；Gitee API 必填 target_commitish，脚本已兜底 master）
pwsh -NoProfile -File scripts\create_gitee_release.ps1 `
  -TagName v1.1.0 -Title "v1.1.0 发布说明" -BodyFile notes.md

# 更新（tag 已存在 Release 时用 -Update，PATCH 标题/正文；Gitee PATCH 也要求 tag_name）
pwsh -NoProfile -File scripts\create_gitee_release.ps1 `
  -TagName v1.1.0 -Title "v1.1.0 发布说明" -BodyFile notes.md -Update
```

## 6. 步骤 4：发布后验证

```bash
python scripts/verify_release_readiness.py --version v1.1.0 --remote origin,gitee
```

或由 `release-readiness-action`（CI 卡点）自动执行 10 项检查。

## 7. 状态码速查（失败快速定位）

| 状态码 | 含义 | 排查 |
|---|---|---|
| 400 | 参数校验失败 | `target_commitish` 必填（POST）；PATCH 需带 `tag_name` |
| 401 | `Access token does not exist` | token 无效/未传入 → 先确认 `$env:GITEE_TOKEN` |
| 403 | 权限不足 / 限流 | token 权限范围；IP 限制 |
| 404 | 仓库不可见 | **token 缺 `projects` 权限** / 路径错 / **URL 被变量拼接破坏**（打印 URL 验证） |
| 409/422 | 已存在 | 同 tag 已有 Release → 用 `-Update` 更新 |
| 429 | 限流 | 等待重试 |

**日志检查点（5 条）**：退出码 → `-Diagnose` 分层 → HTTP 状态码 + 错误体（JSON=业务 / HTML 404=资源不可见）→ 打印请求 URL（防变量陷阱）→ token 传入确认。

## 8. 真实案例复盘（v1.0.0，2026-08-05）

| 现象 | 根因 |
|---|---|
| `400 target_commitish is missing` | Gitee API 必填，脚本补 `master` 兜底 |
| `404 Not Found Project` | URL 退化为 `/repos/nzt47/=d428...`（PowerShell `?` 变量名陷阱吞掉 `security-tools?access_token=`） |
| 诊断 `401` | 环境变量未传入（空 token），非 token 本身问题 |
| PATCH `tag_name is missing` | Gitee 更新接口同样要求 `tag_name` |

修复均已固化：`$($Repo)`/`$($token)` 显式边界 + 单元测试 + 错误分类改状态码判断。

## 9. 检查清单（发布前）

- [ ] `-Diagnose` 全绿
- [ ] tag 已推送 origin + gitee（`git ls-remote --tags`）
- [ ] `update_changelog.py` 已生成发布备注
- [ ] Release 创建/更新成功（非 draft / 非 prerelease）
- [ ] `verify_release_readiness.py` PASS（或 CI 卡点通过）
