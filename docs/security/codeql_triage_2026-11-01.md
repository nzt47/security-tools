# CodeQL 分诊与批次计划（2026-11-01）

> 起因：查 dependabot 时顺带发现 **code scanning 641 条 open**（远超"CodeQL 噪音"的量级）。
> 本文件固定口径与批次，避免下一轮从 0 开始数。

## 1. 盘点（实测）

`gh api "repos/nzt47/security-tools/code-scanning/alerts?state=open&per_page=100&page=N"` 逐页拉全：

| 维度 | 值 |
|---|---|
| 总数 | **641** |
| 严重度 | **3 critical / 181 high / 457 medium** |
| 所在 ref | **全部 `refs/heads/master`**（无分支重复计数） |
| 工具 | **全部 CodeQL**，仓库内**没有**自定义 CodeQL workflow ⇒ 走 default setup 的 `Analyze (python/actions/javascript-typescript)`；**不是双跑重复** |

### 按规则（前 12）

| 条数 | 规则 | 集中文件（前 5） |
|---|---|---|
| **337** | `py/stack-trace-exposure` | 75 routes_skills_mgmt.py · 33 plugins/admin.py · 26 routes_subagent.py · 24 plugins/system_tools.py · 20 routes_workflow_learning.py |
| **101** | `actions/missing-workflow-permissions` | 13 ci.yml · 11 observability-ci.yml · 9 ci-cd.yml · 7 daily_regression.yml · 6 test.yml |
| **76** | `py/path-injection` | 22 file_tools.py · 8 routes_assets.py · 6 replay_storage.py · 6 ui_panels/data.py · 5 lines/registry.py |
| **63** | `py/clear-text-logging-sensitive-data` | 15 file_tools.py · 7 tests/test_api_gateway_auth_flow.py · 5 network_config.py |
| 12 | `py/incomplete-url-substring-sanitization` | 3 notify_arch_violation.py · 2 tests · 1 docs/archive |
| 11 | `js/bad-code-sanitization` | 5 workbench_acceptance_smoke.mjs · 3 login_flow_smoke.mjs · 2 kb_workbench_smoke.mjs |
| 10 | `py/weak-sensitive-data-hashing` | identity.py · feature_flags.py · ab_testing.py |
| 7 | `py/overly-large-range` | 全部 text_tools.py |
| 7 | `py/clear-text-storage-sensitive-data` | setup_llm.py 等脚本 |
| 6 | `py/polynomial-redos` | loki.py · executor.py · processor.py |
| 3 | `js/xss-through-dom` | scripts/*.html（smoke/render 检查页） |
| 2 | `js/clear-text-storage-of-sensitive-data` | yunshu-ui apiToken.ts / Login |
| 3 | **critical** | `ci-failure-notify.yml` ×2（actions/code-injection）、`process_tools.py` ×1（py/command-line-injection） |

## 2. 本轮（B0）已修：3 条 critical

| 告警 | 位置 | 改法 |
|---|---|---|
| actions/code-injection #1 | `ci-failure-notify.yml:64`（prep step 把 `workflow_run.head_branch` 内联进 `run:`） | 全部改经 `env:` 传入、shell 用 `"${VAR:-默认}"` 引用；step outputs 也不再内联进 `github-script`（改 `process.env`） |
| actions/code-injection #2 | 同文件 `:222`（docker-scan 恢复通知的 `--branch "${{ ... }}"`） | 同上（env + 引号引用） |
| py/command-line-injection #598 | `agent/tools/process_tools.py:104` | 用户串**只用于比较**，进 `Popen` 的 argv[0] 必须是**白名单元素**（`_resolve_whitelisted_program`）；参数只做形状校验（字符串列表/上界/无 NUL）；新增 12 条回归用例 |

**为什么这样改能过 CodeQL**：这两类告警的根因都是「用户可控字符串参与命令/脚本构造」。
- actions 侧：GitHub 官方缓解就是「表达式进 env、脚本里用变量」，值不参与解析；
- python 侧：按 §8 陷阱 14 的口径——**让用户输入不进命令**（只做等值比较），而不是加正则白名单。

## 3. 批次计划（按性价比排序）

| 批次 | 范围 | 条数 | 判据 / 坑 |
|---|---|---|---|
| B0 ✅ | 3 critical | 3 | 见 §2 |
| **B1** | `py/path-injection` | 76 | 与 §8 陷阱 14 同源：**用户输入不进路径**（glob 候选 + stem 等值比较）。先 `file_tools.py`（22 条）→ `routes_assets.py`（8） |
| **B2** | `py/stack-trace-exposure` | 337 | 与 §8 陷阱 15 同源：响应体里不能出现异常文本。**5 个文件占 178 条**（routes_skills_mgmt 75 / plugins/admin 33 / routes_subagent 26 / plugins/system_tools 24 / routes_workflow_learning 20），先做这 5 个 + 收敛到统一错误封装 |
| **B3** | `actions/missing-workflow-permissions` | 101 | 机械但**有风险**：加 `permissions: contents: read` 会让"隐式 write"的 job 挂。须逐文件看是否需要 job 级 `permissions:`（提 PR / 发版 / 推 tag 的那些）。建议一次 5–6 个文件 |
| **B4** | `py/clear-text-logging-sensitive-data` | 63 | 先 `file_tools.py`（15），再 `network_config.py`（5）；tests 里的 16 条可判 dismiss |
| **B5** | 小规则合计 | 57 | 多数在 `scripts/dev/*.mjs`、`scripts/*.html`、`tests/`——**优先 dismiss（not_used / used_in_tests）**，别为测试脚本改生产代码 |

## 4. 处置纪律

1. **先修后 dismiss**：能改的一律改；只有「测试/开发脚本、非生产路径」才 dismiss，且 comment 写清判据。
2. **不要用路径排除糊掉生产代码**：default setup 下也没有自定义 config 可挂；迁移到 advanced setup 属于另一件事，另议。
3. **每批都带回归用例**：CodeQL 只认"数据流断了"，不认注释/白名单（§8 陷阱 14）。
4. **别一次动 100 个文件**：PR 越大越难判断是"修好了"还是"扫不到"。按批次走，每批单独 PR + 部署复核。
5. 每批完成后用 `gh api .../code-scanning/alerts?state=open` 复核条数**真的下降**（不会立刻掉，要等 CodeQL 在 master 上重扫）。
