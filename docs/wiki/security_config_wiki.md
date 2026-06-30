# 安全配置 Wiki

本文档是云枢项目安全配置的官方指南，涵盖敏感信息加密、日志脱敏、审计日志等安全功能的使用方法。

---

## 目录

1. [敏感信息加密](#敏感信息加密)
2. [配置方法](#配置方法)
3. [日志脱敏](#日志脱敏)
4. [审计日志](#审计日志)
5. [安全最佳实践](#安全最佳实践)
6. [API 参考](#api-参考)
7. [常见问题](#常见问题)

---

## 敏感信息加密

云枢使用 **AES-GCM** 算法加密存储敏感配置，确保 API Key、密码等敏感信息不会以明文形式存储。

### 配置加载优先级

系统支持多层配置加载，优先级从高到低：

1. **环境变量**（最高优先级）
2. **加密配置文件**（`.secure_config.json`）
3. **默认值**（最低优先级）

### 加密配置文件格式

```json
{
  "llm_api_key": "加密后的API Key",
  "db_password": "加密后的密码",
  "api_secret": "加密后的密钥"
}
```

---

## 配置方法

### 方式一：环境变量（推荐用于生产环境）

**Windows PowerShell：**
```powershell
$env:LLM_API_KEY = "sk-your-api-key"
$env:LLM_PROVIDER = "openai"
```

**Linux/macOS：**
```bash
export LLM_API_KEY="sk-your-api-key"
export LLM_PROVIDER="openai"
```

### 方式二：加密配置文件

系统首次启动时会自动生成加密密钥文件 `.encryption_key`（权限 0o600）。

**保存敏感配置：**
```python
from config_secure import SecureConfigManager

manager = SecureConfigManager()
manager.save_secure_config({
    "llm_api_key": "sk-your-api-key",
    "db_password": "your-database-password"
})
```

**加载敏感配置：**
```python
from config_secure import SecureConfigManager

manager = SecureConfigManager()
config = manager.load_secure_config()
api_key = manager.get_secure_value("llm_api_key")
```

---

## 日志脱敏

系统自动检测并脱敏日志中的敏感信息，防止敏感数据泄露。

### 支持的脱敏类型

| 类型 | 匹配模式 | 示例 | 脱敏结果 |
|------|----------|------|----------|
| API Key | `sk-xxx`, `pk-xxx` | `sk-proj-abc123def456` | `***` |
| JWT Token | 长Base64字符串 | `eyJhbGciOiJIUzI1NiIs...` | `***` |
| 密码字段 | `password=xxx`, `secret=xxx` | `password="secret123"` | `password="***"` |
| 密钥字段 | `api_key=xxx`, `access_token=xxx` | `api_key="sk-123"` | `api_key="***"` |
| 手机号（大陆） | 11位数字，1开头 | `13812345678` | `138****5678` |
| 手机号（带区号） | `+86`或`86`前缀 | `+8613900001111` | `+86139****1111` |
| 手机号（香港） | 8位数字 | `98765432` | `9876****` |
| 身份证号（18位） | 前6位+生日+顺序码+校验码 | `110101199003071234` | `110101********1234` |
| 身份证号（含X） | 最后一位为X | `44030119851212001X` | `440301********001X` |
| 身份证号（15位） | 旧版格式 | `110101900307123` | `110101******123` |
| URL参数 | `?api_key=xxx` | `?api_key=sk-abc123` | `?api_key=***` |

### 使用脱敏过滤器

```python
import logging
from agent.logging_utils import SensitiveDataFilter

# 创建日志记录器
logger = logging.getLogger("my_app")
logger.setLevel(logging.DEBUG)

# 添加脱敏过滤器
handler = logging.StreamHandler()
handler.addFilter(SensitiveDataFilter())

logger.addHandler(handler)

# 日志输出会自动脱敏
logger.info(f"API Key: sk-proj-abc123def456")
# 输出: API Key: ***
```

### P0 安全修复记录（2026-06-28）

> **本节为团队知识库同步条目，记录 error_reporting_config.py 中两处 P0 级安全缺陷的修复细节，供团队成员学习参考，避免类似问题再次发生。**

#### P0-SEC-001：Bearer Token 脱敏失败

**缺陷位置：** [agent/error_reporting_config.py 行 366-384](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py)

**问题根因：**
`_filter_sensitive_recursive` 函数中，字符串内嵌 Bearer Token 的替换逻辑使用 `m.group(0).split("=")[0] + "=[REDACTED]"`。该逻辑假设匹配格式为 `key=value`，但 Bearer Token 格式为 `Bearer <token>`，`split("=")` 会将 token 值保留在 `split("=")[0]` 中，导致敏感 token 泄露。

**修复前（有缺陷）：**
```python
redacted = pat.sub(
    lambda m: m.group(0).split("=")[0] + "=[REDACTED]"
    if "=" in m.group(0) else m.group(0).split(":")[0] + ": [REDACTED]",
    redacted,
)
```

**修复后：** 新增 `_redact_token_match` 函数，Bearer 模式独立判断
```python
def _redact_token_match(m):
    matched = m.group(0)
    if matched.lower().startswith("bearer"):
        return "Bearer [REDACTED]"  # Bearer 模式：整段替换
    if "=" in matched:
        return matched.split("=")[0] + "=[REDACTED]"
    if ":" in matched:
        return matched.split(":")[0] + ": [REDACTED]"
    return "[REDACTED]"
```

**经验教训：** 正则替换 lambda 中不应假设所有匹配都遵循同一格式，需针对不同模式（Bearer vs key=value）分支处理。

---

#### P0-SEC-002：贪婪正则吞噬相邻 URL 参数

**缺陷位置：** [agent/error_reporting_config.py 行 360](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py)

**问题根因：**
敏感 token 正则 `\S+` 为贪婪匹配，会消耗所有非空白字符。当敏感值后紧跟 `&page=1` 等 URL 参数（无空格分隔）时，这些参数会被一并替换为 `[REDACTED]`，导致非敏感数据丢失。

**修复前（有缺陷）：**
```python
re.compile(r"(?i)(token|api[_-]?key|secret|password)\s*[=:]\s*\S+")
```

**修复后：** 改用 `[^&\s]+`，遇 `&` 或空白字符停止
```python
re.compile(r"(?i)(token|api[_-]?key|secret|password)\s*[=:]\s*[^&\s]+")
```

**经验教训：** URL 查询参数场景下，`\S+` 会吞噬 `&` 分隔的相邻参数，应使用 `[^&\s]+` 限定匹配边界。

---

#### 防复发回归测试

专项回归测试文件：[tests/regression/test_p0_security_fix.py](file:///c:/Users/Administrator/agent/tests/regression/test_p0_security_fix.py)

包含 41 个参数化测试用例，覆盖：
- Bearer Token 各变体（JWT/Base64/超长/特殊字符）完全脱敏验证
- `&` 分隔和空格分隔的 URL 参数保留验证
- `_sentry_before_send` 钩子集成场景
- 边界场景（空字符串/Unicode/多分隔符混合）

**运行命令：**
```bash
python -m pytest tests/regression/test_p0_security_fix.py -v
```

**修复验证结果：** 192 passed, 0 failed（151 原有 + 41 新增回归）

---

## 审计日志

所有安全相关操作记录到 `logs/audit.log`，便于安全审计和问题追踪。

### 记录的事件类型

| 事件类型 | 说明 | 示例 |
|----------|------|------|
| `CONFIG_ACCESS` | 配置访问 | 用户读取配置 |
| `CONFIG_MODIFY` | 配置修改 | 用户修改配置 |
| `SECURE_CONFIG_ACCESS` | 安全配置访问 | 访问加密配置 |
| `AUTHENTICATION` | 认证尝试 | 用户登录 |
| `SENSITIVE_OPERATION` | 敏感操作 | 数据导出 |

### 使用审计日志

```python
from agent.logging_utils import get_audit_logger

audit_logger = get_audit_logger()

# 记录配置访问
audit_logger.log_config_access("api_key", "admin")

# 记录认证尝试
audit_logger.log_authentication("admin", True, "192.168.1.100")

# 记录敏感操作（自动脱敏详情）
audit_logger.log_sensitive_operation(
    "export_data",
    {"api_key": "sk-sensitive", "user": "admin"}
)
```

### 审计日志格式

```
2026-06-03 09:07:19 [INFO] CONFIG_ACCESS | user=admin | key=api_key
2026-06-03 09:07:19 [INFO] AUTHENTICATION | username=admin | status=SUCCESS | ip=192.168.1.100
2026-06-03 09:07:19 [INFO] SENSITIVE_OPERATION | user=system | operation=export_data | details={"api_key": "***", "user": "admin"}
```

---

## 安全最佳实践

### 密钥管理
- ✅ 定期备份密钥文件（`.encryption_key`）
- ✅ 限制密钥文件权限为 `0o600`（仅所有者可读）
- ✅ 不要将密钥文件提交到版本控制系统
- ✅ 在生产环境使用安全的密钥存储服务

### 日志安全
- ✅ 始终启用日志脱敏过滤器
- ✅ 避免在日志中直接打印敏感信息
- ✅ 定期清理审计日志

### 配置安全
- ✅ 使用环境变量管理敏感配置（生产环境）
- ✅ 加密配置文件权限设置为 `0o600`
- ✅ 定期轮换敏感配置

---

## API 参考

### SecureConfigManager

| 方法 | 说明 | 参数 |
|------|------|------|
| `encrypt(text)` | 加密字符串 | `text`: 明文字符串 |
| `decrypt(encrypted)` | 解密密文字符串 | `encrypted`: Base64密文 |
| `save_secure_config(config)` | 保存加密配置 | `config`: 配置字典 |
| `load_secure_config()` | 加载解密配置 | 无 |
| `get_secure_value(key, default)` | 获取配置值 | `key`: 配置键, `default`: 默认值 |

### SensitiveDataFilter

| 方法 | 说明 |
|------|------|
| `filter(record)` | 过滤日志记录 |
| `_sanitize(text)` | 脱敏文本 |
| `_sanitize_dict(data)` | 递归脱敏字典 |

### AuditLogger

| 方法 | 说明 |
|------|------|
| `log_config_access(key, user)` | 记录配置访问 |
| `log_config_modification(key, user)` | 记录配置修改 |
| `log_secure_config_access(key, success, user)` | 记录安全配置访问 |
| `log_authentication(username, success, ip)` | 记录认证尝试 |
| `log_sensitive_operation(operation, details, user)` | 记录敏感操作 |

---

## 常见问题

### Q1: 密钥文件丢失怎么办？

**A**: 如果密钥文件丢失，无法解密已加密的配置。请：
1. 从备份恢复密钥
2. 重新配置敏感信息并重新加密

### Q2: 解密失败如何处理？

**A**: 解密失败通常是密钥不匹配或数据损坏：
1. 检查密钥文件是否正确
2. 确认配置文件未被篡改
3. 查看日志获取详细错误信息

### Q3: 如何在容器环境中使用？

**A**: 在容器中推荐使用环境变量传递敏感配置：

```dockerfile
ENV LLM_API_KEY=sk-your-api-key
ENV LLM_PROVIDER=openai
```

---

**版本**: 1.0  
**最后更新**: 2026年6月3日  
**作者**: 安全团队

---

## 相关文档

- [安全配置使用说明](../security/secure_config_guide.md)
- [安全测试报告](../test_reports/security_test_report.md)
- [项目 README](../../README.md)
