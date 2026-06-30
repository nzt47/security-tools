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

## 关联 Issue

<!-- 列出关联的 issue 号，如 Closes #123 -->
