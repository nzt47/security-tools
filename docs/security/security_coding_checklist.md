# 安全编码规范检查清单

> **文档来源：** P0-SEC-001 / P0-SEC-002 缺陷修复经验教训
> **适用范围：** 所有涉及敏感数据处理、日志脱敏、正则表达式、认证授权的代码
> **强制级别：** 代码审查必检项

---

## 一、正则表达式安全规范

### 1.1 贪婪匹配检查

**规则：** 涉及敏感值替换的正则，禁止使用 `\S+` 或 `.*` 等贪婪量词匹配值部分。

**原因：** `\S+` 会消耗到下一个空白字符前的所有内容，导致 `&` 分隔的 URL 参数被吞噬。

| 场景 | 禁止 | 推荐 |
|------|------|------|
| URL 参数值 | `token=xxx\S+` | `token=xxx[^&\s]+` |
| 引号包裹值 | `password=["']?([^"']*)` | `password=["']?([^"'\&\s]*)` |
| 通用分隔 | `key=.*` | `key=[^&;\s]+` |

**检查方法：**
```bash
# 搜索所有 \S+ 用法，逐一确认是否涉及敏感值
grep -rn '\\S+' agent/ --include="*.py" | grep -i 'token\|password\|secret\|key'
```

### 1.2 Bearer Token 处理

**规则：** Bearer Token 脱敏必须独立处理，不得复用 `key=value` 的 `split('=')` 逻辑。

**原因：** Bearer Token 格式为 `Bearer <token>`，`split('=')` 会将 token 值保留在结果中。

**正确实现：**
```python
def _redact_token_match(m):
    matched = m.group(0)
    if matched.lower().startswith("bearer"):
        return "Bearer [REDACTED]"  # 整段替换，不保留 token 值
    if "=" in matched:
        return matched.split("=")[0] + "=[REDACTED]"
    return "[REDACTED]"
```

### 1.3 正则边界注释

**规则：** 所有涉及敏感数据匹配的正则，必须注释说明匹配边界（遇什么字符停止）。

```python
# 正确：注释说明边界
re.compile(r"(?i)(token|api_key)\s*[=:]\s*[^&\s]+")  # 遇 & 或空白停止，保留相邻 URL 参数
```

---

## 二、敏感数据脱敏规范

### 2.1 脱敏覆盖范围

| 数据类型 | 必须脱敏的字段名 | 脱敏方式 |
|---------|----------------|---------|
| 密码 | password, passwd, pwd, secret | 值替换 `[REDACTED]` |
| API 密钥 | api_key, apikey, api-key, access_token | 值替换 `[REDACTED]` |
| 认证头 | Authorization, Bearer token | 整段替换 `Bearer [REDACTED]` |
| 会话 | session_id, session_token | 值替换 `[REDACTED]` |
| 个人信息 | phone, mobile, id_card, bank_card | 部分脱敏（保留首尾） |

### 2.2 递归脱敏要求

- dict 的值必须递归检查（嵌套结构）
- list / tuple 的元素必须递归检查
- str 内嵌的 `key=value` 模式必须用正则替换
- 非容器类型（int/bool/None）原样返回

### 2.3 测试断言要求

- **禁止**仅用 `assert "[REDACTED]" in result` 宽松断言
- **必须**用 `assert result == "Bearer [REDACTED]"` 精确断言
- **必须**断言 `assert "secret_value" not in result`（原值不残留）

---

## 三、代码审查检查清单

> 以下为 PR 审查时必须逐项确认的检查项。

### 3.1 正则表达式

- [ ] 新增/修改的正则未使用 `\S+` 或 `.*` 匹配敏感值
- [ ] 正则有明确的边界限定（`[^&\s]`、`[^"']` 等）
- [ ] Bearer Token 处理未复用 `split('=')` 逻辑
- [ ] 正则已添加注释说明匹配边界

### 3.2 敏感数据处理

- [ ] 新增字段名不在敏感关键词列表中（或已加入脱敏）
- [ ] 日志输出不包含明文密码/token/api_key
- [ ] 异常堆栈不泄露敏感值（如 `ValueError(f"invalid token: {token}")` 应改为 `ValueError("invalid token")`）
- [ ] 测试数据中的 token/password 为虚拟值（非真实凭据）

### 3.3 测试覆盖

- [ ] 脱敏逻辑有对应的单元测试
- [ ] 测试使用精确断言（`==`）而非宽松断言（`in`）
- [ ] 包含 `&` 分隔和空格分隔两种场景
- [ ] 包含空字符串、None、非字符串类型的边界测试

### 3.4 CI 流水线

- [ ] 修改 `error_reporting_config.py` 或 `sensitive_data_filter.py` 时，`tests/regression/test_p0_security_fix.py` 通过
- [ ] 新增正则的模块已加入安全回归测试覆盖

---

## 四、已知风险模块

以下模块涉及敏感数据处理，修改时必须通过安全回归测试：

| 模块路径 | 风险等级 | 涉及缺陷 |
|---------|---------|---------|
| [agent/error_reporting_config.py](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py) | 高 | P0-SEC-001, P0-SEC-002 |
| [agent/utils/sensitive_data_filter.py](file:///c:/Users/Administrator/agent/agent/utils/sensitive_data_filter.py) | 高 | P0-SEC-002 同步修复 |
| [agent/monitoring/sensitive_data_filter.py](file:///c:/Users/Administrator/agent/agent/monitoring/sensitive_data_filter.py) | 中 | 向后兼容层 |
| [agent/logging_utils.py](file:///c:/Users/Administrator/agent/agent/logging_utils.py) | 中 | 日志过滤器 |
| [agent/server_auth.py](file:///c:/Users/Administrator/agent/agent/server_auth.py) | 高 | 认证逻辑 |
| [agent/security_utils.py](file:///c:/Users/Administrator/agent/agent/security_utils.py) | 高 | 安全工具 |

---

## 五、自动化检测命令

```bash
# 1. 运行 P0 防复发回归测试
python -m pytest tests/regression/test_p0_security_fix.py -v

# 2. 搜索潜在贪婪正则风险
grep -rn '\\S+' agent/ --include="*.py" | grep -iE 'token|password|secret|api_key'

# 3. 搜索 Bearer 相关逻辑
grep -rn 'Bearer' agent/ --include="*.py"

# 4. 搜索 split('=') 脱敏逻辑（潜在 P0-SEC-001 复发）
grep -rn "split.*=.*REDACTED" agent/ --include="*.py"

# 5. 运行 Bandit 安全扫描
bandit -r agent/ -f screen
```

---

## 六、缺陷历史参考

| 缺陷ID | 描述 | 修复日期 | 经验教训 |
|--------|------|---------|---------|
| P0-SEC-001 | Bearer Token 脱敏失败 | 2026-06-28 | 不同格式的 token（Bearer vs key=value）必须分支处理 |
| P0-SEC-002 | 贪婪正则吞噬 URL 参数 | 2026-06-28 | URL 参数场景必须用 `[^&\s]+` 限定边界 |
