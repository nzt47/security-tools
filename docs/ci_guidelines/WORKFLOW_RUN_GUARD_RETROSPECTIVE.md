# workflow_run 守卫反模式修复 · 技术复盘

> 复盘时间: 2026-08-01
> 关联规范: [workflow_run_guard.md](./workflow_run_guard.md)
> 关联脚本: [lint_workflow_guard.py](../../scripts/lint_workflow_guard.py)
> 关联测试: [test_lint_workflow_guard.py](../../tests/unit/test_lint_workflow_guard.py)

---

## 1. 事件背景

收到的邮件告警：

```
[nzt47/security-tools] Daily Regression Tests: Some jobs were not successful
  Daily Regression Tests / Nightly Build Guard     Failed in 4 seconds
  Daily Regression Tests / Coverage Report         Skipped
  Daily Regression Tests / Test Summary            Succeeded
  Daily Regression Tests / Unit Tests              Skipped
  Daily Regression Tests / E2E Recovery Tests      Skipped
```

**异常信号**：守卫 job `Nightly Build Guard` failed，但所有测试 job 是 skipped（未运行），汇总 job 反而 succeeded。这说明测试本身没跑、没失败，失败的是"门禁"本身——一次**噪音告警**。

---

## 2. 根因分析

### 2.1 触发链

1. 上游 `ci.yml` 的一次 run 被手动取消（迭代调试时常见；`ci.yml` 无 `concurrency` 块、matrix `fail-fast: false`，多个 job 同时被取消可证）
2. `ci.yml` 取消后，`workflow_run` 事件带 `conclusion=cancelled` 触发 `daily_regression.yml`
3. 旧守卫 job 对**非 success 一律 `exit 1`** → 守卫自身 `failed`
4. 守卫 `failed` 污染整条流水线 conclusion → GitHub 发失败邮件
5. 但下游测试 job `needs` 守卫，守卫 failed 导致它们 skipped——**测试从未运行**

### 2.2 守卫反模式（三信号）

旧 `daily_regression.yml` 守卫 step 的 `run` 块同时命中三者：

| 信号 | 正则 | 旧代码片段 |
|---|---|---|
| `workflow_run.conclusion` 引用 | `workflow_run\.conclusion` | `CONCLUSION="${{ github.event.workflow_run.conclusion }}"` |
| `exit 1` | `\bexit\s+1\b` | `exit 1` |
| `!= success` 判断 | `!=\s*["']?success["']?` | `if [[ "${CONCLUSION}" != "success" ]]` |

**后果**：上游 `cancelled`/`failure` 时，守卫把"跳过"误标为"失败" → 噪音邮件，且为**复发模式**（前一次 run 同样卡在守卫）。

---

## 3. 修复方案

采用**模式 A：outputs 串联**（详见 [workflow_run_guard.md](./workflow_run_guard.md)）。

### 3.1 核心改动

```yaml
nightly-build-guard:
  if: always()
  outputs:
    should_run: ${{ steps.check.outputs.should_run }}   # 用 output 串联,取代 exit 1
  steps:
    - id: check
      run: |
        if [[ "${CONCLUSION}" == "success" ]]; then
          echo "should_run=true" >> "$GITHUB_OUTPUT"
        elif [[ "${CONCLUSION}" == "cancelled" ]]; then
          echo "should_run=false" >> "$GITHUB_OUTPUT"    # 静默跳过,不发失败邮件
        else
          echo "should_run=false" >> "$GITHUB_OUTPUT"
        fi
        # 守卫永不 exit 1

unit-tests:
  needs: [nightly-build-guard]
  if: always() && needs.nightly-build-guard.result == 'success'
       && needs.nightly-build-guard.outputs.should_run == 'true'
```

### 3.2 三种正解对比

| 模式 | 实现 | 适用场景 |
|---|---|---|
| A. outputs 串联 | `if: always()` 守卫 + `outputs.should_run` + 下游 `if` 判 output | 集中守卫 + 多下游 job |
| B. if 跳过 | job `if: conclusion == 'success'` 直接 skipped | 单 job 或下游用 `if: always()` 容忍 skipped |
| C. 失败通知器 | job `if: conclusion == 'failure'` 仅失败时触发 | 通知/告警类工作流 |

---

## 4. 防复发措施（三层防护）

### 4.1 规范文档
[docs/ci_guidelines/workflow_run_guard.md](./workflow_run_guard.md)：判定标准决策树 + 改造模板 + 反模式定义。

### 4.2 Lint 脚本
[scripts/lint_workflow_guard.py](../../scripts/lint_workflow_guard.py)：扫描三信号同时命中的反模式。
- 退出码：`0` 干净 / `1` 反模式（CI 阻塞）/ `2` 脚本错误
- 仅扫描 `workflow_run` 触发的工作流，非该触发器直接跳过

### 4.3 CI 集成
[.github/workflows/ci.yml](../../.github/workflows/ci.yml) 的 `code-quality` job 末尾接入 lint step（阻塞式，无 `|| true`）：

```yaml
- name: workflow_run 守卫反模式检查
  run: |
    pip install pyyaml
    python scripts/lint_workflow_guard.py .github/workflows
```

新增 workflow 若写出反模式，CI 立即阻塞，防止合入。

---

## 5. 验证闭环

### 5.1 本地扫描
```
扫描 28 个文件 | 反模式 0 处 | 解析错误 0 处
EXIT=0
```

### 5.2 CI lint step 首次运行
- run [30679219077](https://github.com/nzt47/security-tools/actions/runs/30679219077)
- `code-quality` job：`success`
- `workflow_run 守卫反模式检查` step：`conclusion=success`（exit 0）
- 所有 12 个 step 全绿，无前序失败

### 5.3 单元测试
[tests/unit/test_lint_workflow_guard.py](../../tests/unit/test_lint_workflow_guard.py)，19 个用例全部通过：

| 类别 | 用例数 | 覆盖场景 |
|---|---|---|
| 0 反模式 | 9 | 模式 A/B/C 正解、非 workflow_run 触发、缺 exit 1、缺 conclusion 引用 |
| 1 反模式 | 4 | 三信号命中、`!=success` 三种写法、多反模式、unnamed step |
| 解析错误 | 2 | 非法 YAML、空文件 |
| 退出码 | 7 | 0/1/2 契约、解析错误优先、默认路径、不存在路径、混合目录 |

```
19 passed in 1.49s
```

### 5.4 失效链接插曲（已解决）
提交时 pre-commit hook 因 origin/master 已有的 3 个失效链接阻塞（`RERANKER_HOT_RELOAD_DEPLOYMENT_GUIDE.md` 引用了未提交的 INT8 文件），经授权 `--no-verify` 跳过。该失效链接后续被提交 `a2501d24`（补齐 INT8 文件）+ `81f01d4e`（修复 10 个失效链接）解决，当前 origin/master 已无该问题。

---

## 6. 全仓风险扫描（任务3结论）

对仓库内所有 `workflow_run` 触发的工作流逐一评估：

| 工作流 | 模式 | lint 结果 | 潜在风险 |
|---|---|---|---|
| [daily_regression.yml](../../.github/workflows/daily_regression.yml) | A（outputs 串联） | ✅ 干净 | 无——已修复的正解 |
| [extension-health-check.yml](../../.github/workflows/extension-health-check.yml) | B（if 跳过） | ✅ 干净 | 无——skipped 标 degraded 非 failed；`health-report` 用 `if: always()` + `needs.*.result` 正确容忍 |
| [ci-failure-notify.yml](../../.github/workflows/ci-failure-notify.yml) | C（失败通知器） | ✅ 干净 | 无——`if: conclusion == 'failure'`，cancelled 不触发通知（预期行为） |
| [ci.yml](../../.github/workflows/ci.yml) | 非 workflow_run 触发 | ✅ 不扫描 | 无——`on: push/pr/schedule`，"workflow_run" 仅出现在 lint step 名称里 |

**结论**：全仓 28 个 workflow，显式反模式 0 处，潜在反模式风险 0 处。lint 脚本覆盖的"三信号反模式"已无残留；人工评估的变体（`if: always()` + `exit 1` 依赖 `job.status` 等）均不构成守卫反模式。

### 6.1 lint 未覆盖的潜在变体（人工排查记录）
- `exit 2`/`exit 3` 等非 1 退出码：全仓无（且非反模式特征，exit 1 是 shell 守卫惯例）
- `|| true` 掩盖失败：全仓无此反模式用法
- workflow_run 触发但无守卫（直接跑测试）：`extension-health-check` 的 `extension-unit-tests`/`extension-config-check` job 无 conclusion 守卫，但它们不依赖上游结论（独立检查），上游 cancelled 时这些 job 仍跑属于设计意图，非风险

---

## 7. 经验教训

### 【不易】识别不变量
- 守卫的职责是**门禁**（放行/跳过），不是**告警**（上游失败由上游自身通知）
- "跳过" ≠ "失败"：cancelled/failure 时跳过回归是正确行为，不应发失败邮件

### 【变易】区分上游状态
- `success` → 放行跑回归
- `cancelled` → 静默跳过（不发邮件，避免噪音）
- `failure` → 跳过（上游失败不该再跑回归；上游自己会通知）

### 【简易】最小充分解
- 守卫 job 只做条件判断（无 I/O、无外部回调），用 `outputs` 串联下游
- 守卫**永不 `exit 1`**——失败由下游测试 job 自身的结果决定
- 防复发用 lint 脚本（三信号正则）而非复杂 AST 分析，初级工程师 30s 可读

### 工程实践
- 邮件告警需区分"测试失败"与"门禁失败"：前者要修代码，后者要修 CI 编排
- `workflow_run` 触发的工作流必须有明确的 conclusion 处理策略，不能对非 success 一律 exit 1
- pre-commit hook 的全仓链接检查会因**别人提交的失效链接**连累当前提交，需区分"本提交引入"与"pre-existing"
