## 变更说明

<!-- 简要描述本 PR 的变更内容和目的 -->

## 变更类型

<!-- 勾选适用的类型 -->

- [ ] 新功能（feature）
- [ ] Bug 修复（bugfix）
- [ ] 重构（refactor）
- [ ] 文档更新（docs）
- [ ] 性能优化（performance）
- [ ] 安全修复（security）

## 安全审查清单

> 如果本 PR 涉及安全敏感模块（日志脱敏、认证、加密、敏感数据处理），请勾选以下检查项。

- [ ] 本 PR 不涉及安全敏感模块
- [ ] 正则表达式已检查贪婪匹配问题（`\S+` → `[^&\s]+`）
- [ ] Bearer Token / API Key 脱敏逻辑已通过 `tests/regression/test_p0_security_fix.py`
- [ ] 未引入硬编码的密钥、密码或 token
- [ ] 日志输出不包含敏感字段（password、token、api_key 等）
- [ ] 新增的正则表达式已添加注释说明匹配边界

## 测试验证

- [ ] 单元测试通过：`python -m pytest tests/unit/ -v --tb=short`
- [ ] 安全回归测试通过：`python -m pytest tests/regression/test_p0_security_fix.py -v`
- [ ] 覆盖率未下降：`python -m pytest --cov=agent --cov-report=term-missing`

## 策略变更（P7.2-19：仅当本 PR 触及 `data/policies/**` 时填写）

> 策略即代码：策略变更走「模拟报告 + 人工合入」，**不得运行时静默改策略**。
> 门禁脚本：`scripts/check_policy_change_gate.py`（CI：`.github/workflows/policy-change-gate.yml`）。

- [ ] 本 PR 不涉及策略文件变更
- [ ] 已附模拟报告：`reports/policy_simulation.json`（或 `<report: 路径>` 指定）
- [ ] 模拟报告与本次改动一致（候选策略 id/version 出现在改动后的策略文件里）

<!--
  高危确认：只要模拟报告的 high_risk_hits 非空，就必须逐条勾选并写明理由。
  格式必须是 `- [x] <policy_id> <capability_id> <理由>`（门禁按勾选数核对）。
  无高危变更时，本节保留并写「（无高危变更）」即可。
-->

## 高危确认

- [ ] （无高危变更时勾选本行；有高危变更时**删除本行**并逐条列出，格式 `- [x] <policy_id> <capability_id> <理由>`）

## 无样本声明

<!--
  仅当模拟报告的 totals.total == 0（无历史决策样本）时需要勾选。
  门禁要求此时本段存在**已勾选项**——「零变更」在无样本时不构成安全证据，
  必须由人工显式承担未经模拟的风险。有样本时可留空或整段删除。
-->

- [ ] 本次变更无历史决策样本，未经模拟，风险由人工承担

## 关联 Issue

<!-- 列出关联的 issue 号，如 Closes #123 -->
