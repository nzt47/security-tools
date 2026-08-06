# Release 自动发布工作流优化总结

> 阶段：**最终版（2026-08-06）**
> 范围：`.github/workflows/release-auto.yml`（自动发布）及其配套脚本
> （`scripts/update_changelog.py`、`scripts/create_gitee_release.ps1`）。
> 配套文档：[发布流程维护操作手册](release_workflow_manual.md)（§1-§11，含排障/FAQ/测试验证记录）。

---

## 1. 演进历程

| 阶段 | 变更 | 提交 |
|---|---|---|
| v1.0.1 起 | GitHub Actions 自动发布：guard 守卫 + 发布备注 + GitHub/Gitee Release | — |
| v1.0.2 | 迁移 GitLab CI（`.gitlab-ci.yml`），增 GitHub Release 重试语义 | — |
| v1.0.3 | 迁回 GitHub Actions，回填重试逻辑 | — |
| v1.0.5~1.0.8 | 退出码/超时/静默失败专项治理 + 本地模拟验证 | 见 §4 |

最终形态：**GitHub Actions** 单工作流 3 job（guard → auto-release → alert-on-failure）。

## 2. 最终架构

```
git tag vX.Y.Z + push
        │
        ▼
┌─────────────────┐   commit message 以 release(pypi) 开头（子包发布）?
│ guard (skip=?)  │─────────────── 是 → 跳过，不发布（不告警）
└────────┬────────┘
         │ skip=false
         ▼
┌──────────────────────┐
│ auto-release         │ ① update_changelog.py 生成发布备注（git log 分类）
│                      │ ② GitHub Release：curl + REST API，重试 3 次×10s
│                      │    · 409/422 幂等冲突不重试（提示改用 PATCH）
│                      │    · 网络层失败（超时/拒连）映射 HTTP 500 进入重试
│                      │    · --max-time 30 防 API 挂起
│                      │ ③ Gitee 同步：create_gitee_release.ps1，重试 3 次×10s
│                      │    · Invoke-Gitee TimeoutSec=30 防无限等待
│                      │    · if/else 内捕获 $?（else RC=$?）
└────────┬─────────────┘
         │ 任一失败（或 guard 失败）
         ▼
┌──────────────────────┐
│ alert-on-failure     │ needs: [guard, auto-release] + if: failure()
│                      │ gh issue create（GITHUB_TOKEN 零依赖）
└──────────────────────┘
```

**触发方式**：`git push origin vX.Y.Z`（push tag）或手动 workflow_dispatch 填版本号。

## 3. 优化点清单

### 3.1 可靠性（重试与超时）

| # | 优化 | 说明 |
|---|---|---|
| 1 | GitHub Release 重试 3 次×10s | `gh release create` 无内建重试，改用 curl + REST API 自建 while 循环 |
| 2 | 409/422 幂等冲突不重试 | tag 已存在 Release，重试无意义；直接失败并提示 `-Update`/PATCH |
| 3 | **curl 网络失败映射 500**（5d713f64） | `CODE=$(curl ...) \|\| CODE=500`：超时/拒连（exit 28/7）不再被 `set -e` 终止 step、跳过重试 |
| 4 | **`--max-time 30` 防挂起**（5d713f64） | API 挂起不会静默拖满 step 超时（timeout-minutes: 10） |
| 5 | 响应体容错读取 | `[ -s gh_resp.json ]`：网络失败时响应文件可能缺失/为空 |
| 6 | **Gitee 脚本 TimeoutSec=30**（5d713f64） | `Invoke-RestMethod` 默认无限等待；超时即抛异常 → exit 1 → 进重试 |
| 7 | Gitee 同步重试 3 次×10s | while 循环 + `else RC=$?` 正确捕获退出码 |

### 3.2 告警闭环

| # | 优化 | 说明 |
|---|---|---|
| 8 | alert-on-failure 建告警 Issue | `if: failure()` + `gh issue create`，GITHUB_TOKEN 自动注入，零额外凭据 |
| 9 | **needs 含 guard**（21b3a071） | 修复 guard 失败 → auto-release 被跳过 → `failure()` 对 skipped 返回 false → **无告警静默中断**；guard 失败现也会触发告警，skip=true 拦截仍不告警 |
| 10 | 告警内容自含排查指引 | 版本/运行链接/触发方式/失败时间 + 401/404/409/422 状态码排查 |

### 3.3 守卫与退出码

| # | 优化 | 说明 |
|---|---|---|
| 11 | 子包 tag 守卫精确匹配（e104e391） | commit message 前缀 `^release\(pypi\)` 才跳过；禁用宽泛关键词（曾误拦 v1.0.1-test） |
| 12 | bash if 吞退出码修复（1094a1c6） | `$?` 必须在 else 分支内捕获；`if cmd; then; fi` 无 else 时 if 构造返回 0 |
| 13 | 全仓库 `$?` 排查（a73896c9） | 21 处捕获点核对，修复 log-perf-guard.yml:169 同型陷阱 |

### 3.4 可观测性（日志）

| # | 优化 | 说明 |
|---|---|---|
| 14 | 关键 step `set -x` | 完整命令展开，便于定位失败命令 |
| 15 | 时间戳 `[HH:MM:SS]` | 每次重试尝试带 UTC 时间戳，核对 10s 间隔 |
| 16 | 上下文打印 | token 长度、API 地址、版本号、响应体（前 200-300 字符）、失败退出码 |

### 3.5 文档

| # | 优化 | 说明 |
|---|---|---|
| 17 | 操作手册（84800618 起，持续维护） | §1-§5 架构/守卫/重试/告警/密钥；§6 排障；§7-§8 迁移对照；§9 退出码经验；§10 FAQ；§11 测试验证记录 |
| 18 | 本地模拟验证方法论 | WSL 环境坑（`python.exe`/`pwsh.exe`/Windows curl/`wslpath -w`）+ 测试 tag + mock API + stub 脚本 |

## 4. 验证记录（本地模拟，均不碰真实 API）

| 验证 tag | 场景 | 结果 |
|---|---|---|
| v1.0.5 | GitHub 500×2→201；Gitee 401→第 2 次成功 | 重试推进正常，else 内 RC 正确 |
| v1.0.6 | Gitee 网络超时（exit 28）×2 → 第 3 次成功 | 超时走 else 捕获 → 重试 |
| v1.0.7 | Gitee 503×2 → 第 3 次成功 | 5xx 瞬时故障走重试，恢复后成功 |
| v1.0.8 | GitHub 403 限流：A) 403×2→201；B) 403×3→exit 1 | 403 走重试；瞬时恢复成功，持续耗尽触发告警 |

**重试行为速查**：

| 失败类型 | 行为 |
|---|---|
| 网络超时（exit 28）/ 5xx（500/503）/ 403 / 401 / 404 | 走重试 3 次×10s |
| 409 / 422（tag 已存在 Release） | 幂等冲突，不重试 |
| 重试 3 次均失败 | `exit 1` → 自动触发告警 Issue |

## 5. 最终提交链

```
e104e391 guard 正则精确匹配
84800618 操作手册创建
1094a1c6 Gitee else 捕获 $? + 手册
a73896c9 log-perf-guard.yml 退出码修复
cfde3721 v1.0.5 模拟验证
5d713f64 curl 网络失败→500 + Gitee TimeoutSec + 手册 §10
49714b6a 手册 §6.1（网络超时与静默失败修复）
9ba84769 手册 §11（测试验证记录 v1.0.5~v1.0.8）
21b3a071 alert-on-failure needs 含 guard（本次最终修复）
```

## 6. 遗留建议（非本次范围）

- 告警渠道扩展：Slack/DingTalk webhook（可复用 `scripts/slack_notify.py`）
- Gitee 幂等增强：409/422 时自动探测已有 release id 切换 `-Update` 更新模式
- 首次发布（无上一 tag）支持：`update_changelog.py` 目前缺 prev-tag 会显式报错，可支持"从首个提交起"模式
