# 发布流程治理实录：从静默失败到全链路可验证

> 团队分享 / 新成员培训草稿
> 作者：Release 工作流治理小组　日期：2026-08-06
> 配套资料：[操作手册](release_workflow_manual.md)｜[优化总结](release_workflow_summary.md)｜[检查清单](release_checklist.md)｜[可复用模板](release_workflow_template.md)

---

## 1. 背景：一个"看起来正常"的发布流程

我们的自动发布流程经历了 GitHub Actions → GitLab CI → 迁回 GitHub Actions 的完整闭环，
最终形态是 3 个 job：`guard`（子包守卫）→ `auto-release`（发布备注 + GitHub/Gitee Release）→
`alert-on-failure`（失败建告警 Issue）。

但"能跑通"和"可靠"是两回事。治理过程中我们抓出了 **3 类静默失败**——它们都不报错，
却让发布悄悄失败或悄悄没人管。

## 2. 静默失败三兄弟

### 2.1 兄弟一：curl 网络失败跳过重试

**现象**：创建 GitHub Release 用了 `CODE=$(curl ...)`，重试逻辑写在 while 循环里。
本地/正常网络下一切正常，一旦 curl 网络层失败（超时 `exit 28` / 连接拒绝 `exit 7`），
`set -e` 直接终止整个 step——**while 重试循环一次都没跑就 abort 了**。
网络瞬时抖动本该重试 3 次，结果第一次就失败并误触发告警。

**根因**：`set -e` 的语义是"命令失败即退出"，命令替换失败也算。而 HTTP 状态码
（`-w '%{http_code}'` 输出 500/503）**不是退出码**——curl 照样返回 0。
所以"HTTP 5xx 走重试"设计好了，但"网络层失败"这条路径没人覆盖。

**修复**：
```bash
CODE=$(curl -s -o gh_resp.json -w '%{http_code}' --max-time 30 -X POST ...) || CODE=500
```
把网络失败映射为 HTTP 500 进重试；`--max-time 30` 防 API 挂起；读响应体前 `[ -s gh_resp.json ]` 容错。

**教训**：**HTTP 状态码和进程退出码是两回事**。处理外部调用要同时覆盖两条失败路径。

### 2.2 兄弟二：bash if 吞退出码

**现象**：Gitee 同步用 `if pwsh ...; then ...; fi`，后面再取 `$?` 判断失败原因。
结果 `$?` 恒为 0，重试循环永远以为"刚才是成功的"。

**根因**：`if cmd; then ...; fi` 中 cmd 失败且无 `else` 时，**if 构造整体返回 0**
（bash 约定条件为假返回 0）。退出码被 if 构造吞了。

**修复**：
```bash
if pwsh ...; then
  echo "成功"
else
  RC=$?   # $? 必须在 else 分支内捕获
fi
```

**教训**：`$?` 是"上一条命令的退出码"，不是"你想关心的命令的退出码"。
任何必然成功的命令（echo、赋值）都会覆盖它。全仓库排查 21 处 `$?` 捕获点，
又揪出一个同型问题（log-perf-guard.yml：`if/elif/else` 分支里最后一条命令是
`echo scan_mode=...`，fi 后取 `$?` 恒 0，PR 评论永远显示 ✅）。

### 2.3 兄弟三：guard 失败无告警（依赖缺失）

**现象**：`alert-on-failure` 只 `needs: auto-release`。一旦 guard 失败，
auto-release 因 needs 失败被**跳过**（skipped），而 `if: failure()` 对 skipped 返回 **false**——
结果 guard 失败 → 发布静默中断 → 没有任何告警。

**修复**：`needs: [guard, auto-release]`。guard 失败 → `failure()` 为 true → 正常告警；
子包拦截（skip=true，guard 成功 + auto-release 被跳）仍不告警，语义不破坏。

**教训**：GitHub Actions 的 job 状态有 4 种（success/failure/skipped/cancelled），
`failure()` 只对 **failure** 为真。梳理告警链路时必须画出"每个 job 在每种状态下的下游行为"。

## 3. 重试与幂等：哪些该重试，哪些不该

| 失败类型 | 是否重试 | 理由 |
|---|---|---|
| 网络超时 / 5xx / 403 限流 / 401 / 404 | ✅ 3 次×10s | 瞬时故障，重试有收益 |
| 409 / 422（tag 已存在 Release） | ❌ | 幂等冲突，payload 没变重试也没用 |

要点：**重试不是万能药**。先判断"重试是否改变结果"。409/422 的正确动作是改
`-Update` 更新模式或 PATCH 编辑接口，而不是无脑重试。

## 4. 方法论：不碰真实环境的本地模拟验证

治理的核心难点是**不敢在真实环境测试失败路径**（总不能真把 Gitee 搞挂）。
我们建立了本地模拟方法论：

1. **mock API**：本地 python http.server 模拟 GitHub/Gitee API，按调用次数返回
   指定状态码（500/503/403/201 均可编排）
2. **stub 脚本**：ps1/bash 桩脚本模拟外部命令的失败（`exit 1` + 打印 `HTTP 503`）
3. **测试 tag**：打 `v1.0.X` 测试 tag 跑全流程，验证后删除
4. **关键细节**：Windows 本地模拟有 3 个 WSL 坑——
   - Linux curl 连不上 Windows 宿主的 mock → 用 `/mnt/c/Windows/System32/curl.exe`
   - `python`/`pwsh` 找不到 → 用 `python.exe`/`pwsh.exe`
   - Windows 程序解析不了 WSL 路径 → 传参前 `wslpath -w` 转 `C:\...`

已完成的验证矩阵：

| tag | 场景 | 结果 |
|---|---|---|
| v1.0.5 | GitHub 500×2→201；Gitee 401→成功 | 重试推进正常 |
| v1.0.6 | Gitee 超时（exit 28）×2→成功 | 超时走重试 |
| v1.0.7 | Gitee 503×2→成功 | 5xx 走重试 |
| v1.0.8 | GitHub 403：恢复场景成功 / 耗尽场景告警 | 双路径验证 |

**这验证的不只是重试逻辑**，还包括退出码传播、时间戳间隔、告警触发条件。

## 5. 经验教训清单（可直接用于培训）

1. **HTTP 状态码 ≠ 进程退出码**：外部调用要同时覆盖"网络层失败"和"HTTP 层失败"两条路径
2. **`$?` 不是你想的那条命令的退出码**：捕获必须紧贴命令，if/else 里放 else 分支
3. **GitHub Actions 的 skipped ≠ failure**：画 job 状态机，别只看 `if: failure()`
4. **重试前先问"重试有收益吗"**：幂等冲突不重试
5. **外部调用必须设超时**：curl 用 `--max-time`，PowerShell 用 `TimeoutSec`，否则"挂起"会静默拖满 step 超时
6. **可观测性先行**：`set -x` + 时间戳 + token 长度 + 响应体摘要，让每次失败都有据可查
7. **测试失败路径要本地模拟**：mock + stub + 测试 tag，不碰真实环境
8. **文档是流程的一部分**：操作手册（排障）+ 检查清单（发布前）+ 模板（复用）+ 本总结（传承）

## 6. 结语

发布流程的价值在"失败时"才体现：能不能快速定位、能不能自动告警、能不能自动恢复。
这次治理把三类静默失败变成显式失败，把失败路径全部用本地模拟验证过，
并沉淀成 4 份文档 + 1 份可复用模板。下次发布，按[检查清单](release_checklist.md)走即可。

> 附：最终提交链 `e104e391 → 84800618 → 1094a1c6 → a73896c9 → cfde3721 → 5d713f64 → 49714b6a → 9ba84769 → 21b3a071 → f8d634a8`（详见优化总结 §5）。
