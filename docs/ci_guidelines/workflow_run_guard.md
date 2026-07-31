# workflow_run 守卫模式规范

> 适用于所有通过 `workflow_run` 触发的 GitHub Actions 工作流。
> 判定标准 + 改造模板 + 反模式定义，配套 `scripts/lint_workflow_guard.py` 自动扫描。

## TL;DR

- **不变量**：上游非 `success` 时不执行下游测试；但"跳过"**不得**标记为"失败"。
- **反模式**：守卫 job 用 `exit 1` 表达"跳过"——会污染整条流水线 conclusion，发噪音失败邮件。
- **正解**：集中守卫 job 用 `outputs.should_run` 串联下游；或单 job 直接 `if: conclusion == 'success'` 跳过。

## 1. 背景

2026-07-31，`daily_regression.yml` 的 `Nightly Build Guard` job 在上游 ci.yml 被**手动取消**（conclusion=`cancelled`）时执行 `exit 1`，导致：

1. 守卫 job 自身 conclusion=`failure`
2. 整个 workflow conclusion=`failure`，发出失败邮件
3. 但实际**没有任何回归测试运行或失败**——纯噪音（复发多次）

根因：守卫把"跳过"误标成"失败"。本规范防止此类问题复发。

## 2. 核心不变量（不易）

| 约束 | 说明 |
|------|------|
| 门禁语义 | `workflow_run` 触发时，上游非 `success` 则不执行下游测试 job |
| 跳过≠失败 | 上游 `cancelled`/`failure` 时下游应 `skipped`（静默），不得 `failed` |
| 告警归属 | 上游失败由上游 workflow 自身的通知机制负责；下游"因上游失败而跳过"不算下游失败 |
| 真失败才告警 | 仅下游测试 job 自身失败才发失败邮件 |

## 3. 三种 workflow_run 守卫模式

| 模式 | 形式 | 适用场景 | 上游 cancelled 行为 | 仓库实例 |
|------|------|----------|---------------------|----------|
| A. 集中守卫 job | `if: always()` 守卫 + `outputs.should_run`，下游 `needs` 判 output | 多个下游 job 共享同一门禁 | 静默跳过（should_run=false） | `daily_regression.yml` |
| B. 直接 if 跳过 | job 自身 `if: conclusion == 'success'` | 单 job 自守卫，下游用 `if: always()` 容忍 skipped | job=skipped | `extension-health-check.yml` |
| C. 失败通知器 | `if: conclusion == 'failure'` | 只在上游失败时发通知 | 不触发（cancelled≠failure） | `ci-failure-notify.yml` |

**模式 A vs B 的本质区别**：
- 模式 A 用一个**总是运行**的守卫 job 做集中判断，下游 `needs` 它并通过 `result`/`outputs` 决策——适用于多个下游需要统一门禁。此结构下守卫**不能 `exit 1`**（会自身 failed 污染流水线），必须用 `outputs` 串联。
- 模式 B 让 job **本身被 if 跳过**（skipped），下游用 `if: always()` + `needs.*.result` 天然容忍 skipped——无需 output 串联。

## 4. 判定标准（是否需要改造）

```
workflow_run 工作流是否命中反模式?
│
├─ 守卫 job 用 `if: always()` 总是运行
│  且 step 内对 conclusion != success 执行 `exit 1`
│  且下游 `needs` 该守卫并判 `result`
│  → ✅ 命中反模式,必须改造为模式 A（outputs 串联）
│
├─ job 直接用 `if: conclusion == 'success'` 让自身 skipped
│  → ⚪ 已是模式 B,正确,无需改造
│
└─ 只在 `conclusion == 'failure'` 时响应（通知器）
   → ⚪ 模式 C,语义不同,无需改造
```

**lint 命中条件**：同一 step 的 `run` 块内同时出现 `workflow_run.conclusion`（或其赋值的变量）与 `exit 1`，且 `exit 1` 位于 `!= success` 判定分支——即为反模式。

## 5. 改造模板（模式 A）

适用：命中反模式的集中守卫 job。三步最小改动：

### 步骤 1：守卫 job 声明 outputs

```yaml
nightly-build-guard:
  name: Nightly Build Guard
  runs-on: ubuntu-latest
  if: always()
  outputs:
    should_run: ${{ steps.check.outputs.should_run }}   # 新增
  steps:
    - name: Check upstream conclusion
      id: check                                          # 新增
      run: |
        ...
```

### 步骤 2：守卫 step 三分支设 should_run，永不 exit 1

```yaml
      run: |
        EVENT="${{ github.event_name }}"
        CONCLUSION="${{ github.event.workflow_run.conclusion }}"
        if [[ "${EVENT}" == "workflow_run" ]]; then
          if [[ "${CONCLUSION}" == "success" ]]; then
            echo "should_run=true" >> "$GITHUB_OUTPUT"
            echo "✅ 上游构建成功,继续"
          elif [[ "${CONCLUSION}" == "cancelled" ]]; then
            echo "should_run=false" >> "$GITHUB_OUTPUT"
            echo "⏭️ 上游被取消,静默跳过(不发失败通知)"
          else
            echo "should_run=false" >> "$GITHUB_OUTPUT"
            echo "❌ 上游失败(conclusion=${CONCLUSION}),跳过"
          fi
        else
          # schedule / PR / dispatch 路径直接放行
          echo "should_run=true" >> "$GITHUB_OUTPUT"
        fi
```

### 步骤 3：下游 job 的 if 追加 should_run 判定

```yaml
unit-tests:
  needs: [nightly-build-guard]
  if: |
    always() && needs.nightly-build-guard.result == 'success'
    && needs.nightly-build-guard.outputs.should_run == 'true'   # 新增
    && github.event.inputs.test_suite != 'e2e'
```

### 效果

| 上游 conclusion | 守卫 job | 下游测试 job | workflow conclusion | 失败邮件 |
|-----------------|----------|--------------|---------------------|----------|
| success | success | 运行 | 取决于测试结果 | 仅测试真失败时 |
| cancelled | success | skipped | success | 无 |
| failure | success | skipped | success | 无（上游自己通知） |

## 6. 反模式（禁止）

```yaml
# ❌ 禁止：守卫 job 用 exit 1 表达"跳过"
- name: Check upstream conclusion
  run: |
    if [[ "${{ github.event.workflow_run.conclusion }}" != "success" ]]; then
      echo "跳过回归测试"
      exit 1          # 污染 workflow conclusion = failure → 噪音邮件
    fi
```

**为什么禁止**：`exit 1` 让守卫 job 自身 `failed`，进而让整个 workflow conclusion=`failure`，触发失败邮件——但下游测试并未运行，属于"跳过"被误标"失败"。上游被 `cancelled` 时尤甚（人为取消不应告警）。

## 7. Lint 集成

配套脚本：[`scripts/lint_workflow_guard.py`](../../scripts/lint_workflow_guard.py)

```bash
# 本地运行
python scripts/lint_workflow_guard.py .github/workflows

# CI 集成（在代码质量检查 job 中）
- name: Lint workflow_run guards
  run: python scripts/lint_workflow_guard.py .github/workflows
```

- 退出码 `0`：未发现反模式
- 退出码 `1`：发现反模式（CI 应阻塞）
- 退出码 `2`：脚本自身错误（如 YAML 解析失败）

## 8. 决策记录

| 日期 | 事件 |
|------|------|
| 2026-07-31 | `daily_regression.yml` 守卫修复：`exit 1` → `outputs.should_run` 串联。上游 ci.yml 被 cancel 时不再发噪音邮件。提交 `2216d74d`。 |
| 2026-07-31 | 全仓审计确认：仅 `daily_regression.yml` 命中反模式；`extension-health-check.yml`（模式 B）与 `ci-failure-notify.yml`（模式 C）无需改造。 |
| 2026-08-01 | 本规范 + lint 脚本落地，防止未来新增 workflow 复发。 |
