# Gitee API 创建 Release 失败排查指南

> **适用场景**: `scripts/create_gitee_release.ps1` 或任何 Gitee API v5 调用失败时的排查参考。
> **来源**: 2026-08-05 真实事故复盘（v1.0.0 Gitee Release 创建，经历 400/404/401 三连错后定位为 PowerShell 变量拼接 Bug）。
> **关联**: [create_gitee_release.ps1](../../scripts/create_gitee_release.ps1)、[test_create_gitee_release_script.py](../../tests/unit/test_create_gitee_release_script.py)

---

## 1. 快速定位（推荐：先跑诊断）

```powershell
$env:GITEE_TOKEN = "<令牌>"
pwsh -NoProfile -File scripts\create_gitee_release.ps1 -Diagnose
```

诊断按顺序执行三步，**任一步失败即退出**，输出直指问题层：

| 步骤 | 检查 | 失败含义 |
|---|---|---|
| 1 | `GET /user` | token 无效/未传入（401） |
| 2 | `GET /repos/{owner}/{repo}` | 仓库不可见：路径错 / token 权限不足 / 私有仓库 |
| 3 | `GET /repos/{owner}/{repo}/releases` | 环境正常；顺带确认是否已存在同 tag Release（避免 409/422） |

## 2. 状态码速查表

| 状态码 | 含义 | 排查点 |
|---|---|---|
| 400 | 参数校验失败 | **`target_commitish` 必填**（Gitee API 特有，分支名或 commit SHA） |
| 401 | `Access token does not exist` | token 无效/过期/环境变量未传入；先跑步骤 1 确认 |
| 403 | 权限不足 / 限流 | token 权限范围；IP 限制；企业安全策略 |
| **404** | 仓库不可见 | **最常见：token 缺 `projects` 权限**；owner/repo 拼写错误；仓库私有且 token 无权限。注意 Gitee 对无权限资源统一返回 404（不暴露存在性），需与 401 区分 |
| 409/422 | 资源冲突 | 同 tag 已存在 Release，先 `GET /releases` 确认 |
| 429 | 限流 | 等待后重试 |

## 3. 日志检查点

1. **HTTP 状态码**：`$_.Exception.Response.StatusCode` 是第一信号
2. **错误体格式**：
   - JSON `{"message": "..."}` → 业务错误，直接可读
   - **HTML 404 页面**（"你所访问的页面不存在"）→ 资源不可见（权限/路径），与业务 404 不同
3. **区分 401 vs 404**：先 `GET /user`（token 有效性）再 `GET /repos/{owner}/{repo}`（可见性）
4. **对照 `git push`**：push 成功但 API 404 → 凭据（SSH/HTTPS 凭据管理器）与 API token **不同源**，优先怀疑 token 权限而非仓库存在性
5. **验证 URL 拼接**：请求前打印完整 URL（本次事故根因——URL 被 PowerShell 变量解析破坏后返回 404，极易误判为权限问题）

## 4. 本次真实案例复盘（三连错 → 根因）

| 现象 | 初步判断 | 实际根因 |
|---|---|---|
| ① `400 target_commitish is missing` | 参数缺失 | Gitee API 必填 `target_commitish`，补上 |
| ② `404 Not Found Project` | 仓库不存在 / token 无权限 | **URL 实际为 `/repos/nzt47/=d428...`**（变量解析吞掉了 `security-tools?access_token=`） |
| ③ 诊断 `401 Access token does not exist` | token 无效 | 环境变量未传入当前终端（空 token），非 token 本身问题 |

**根因**：PowerShell 变量名允许包含 `?`。`"/repos/$Owner/$Repo?access_token=$token"` 中 `$Repo?access_token` 被整体解析为单个变量名（未定义 → 空字符串），URL 退化为 `/repos/nzt47/=d428...`。

**定位技巧**：同一 URL 直接调用 `Invoke-RestMethod` 成功、但经脚本失败 → 差异必在脚本内部的 URL 构造，打印 URL 即真相。

## 5. PowerShell 变量名陷阱专项

- PowerShell 变量名合法字符**包含 `?`**（`$env:?`、`$?` 等是内置变量），因此 `$var?xxx` 会尝试解析为 `$var?xxx` 整体
- **修复模式**：变量后紧跟非字母数字字符时用 `$()` 显式边界

```powershell
# 错误：$Repo?access_token 被当变量名 → 空
$old = "/repos/$Owner/$Repo?access_token=$token"

# 正确：$() 显式边界
$new = "/repos/$Owner/$($Repo)?access_token=$($token)"
```

- 回归测试：`tests/unit/test_create_gitee_release_script.py`（同时断言旧写法复现退化 URL、新写法产出完整 URL）

## 6. 预防措施

- [x] 脚本内置 `target_commitish = "master"` 兜底（400 不再发生）
- [x] `-Diagnose` 模式将 404/401 分层定位（不再盲猜）
- [x] 单元测试覆盖变量拼接 Bug，CI 每次提交自动回归
- [ ] 生成 token 时**务必勾选 `projects` 权限**（Release 读写必需）
- [ ] 调用 API 前打印请求 URL（尤其 URL 含变量拼接时）
