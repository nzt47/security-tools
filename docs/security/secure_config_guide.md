# 安全配置使用说明

## 概述

本项目提供了完整的敏感信息安全保护机制，包括：
- **API Key 加密存储**：使用 AES-GCM 算法加密存储敏感配置
- **日志自动脱敏**：自动检测并脱敏日志中的敏感信息
- **审计日志记录**：记录所有安全相关操作

---

## 一、加密密钥管理

### 1.1 密钥生成

系统首次启动时会自动生成加密密钥，存储在 `.encryption_key` 文件中：

```python
from config_secure import SecureConfigManager

# 创建安全配置管理器（自动生成密钥）
manager = SecureConfigManager()

# 密钥文件路径（默认）
# Linux/macOS: ~/.encryption_key
# Windows: 当前目录/.encryption_key
```

### 1.2 密钥文件权限

密钥文件权限自动设置为 `0o600`（仅所有者可读），确保安全性：

```bash
# Linux/macOS 下查看权限
ls -la .encryption_key
# -rw------- 1 user group 32 Jun  1 10:00 .encryption_key
```

### 1.3 密钥备份与恢复

**备份密钥：**

```bash
# 创建密钥备份
cp .encryption_key .encryption_key.backup
```

**恢复密钥：**

```bash
# 从备份恢复
cp .encryption_key.backup .encryption_key
```

### 1.4 密钥文件位置

| 环境 | 默认路径 | 说明 |
|------|----------|------|
| 开发环境 | `./.encryption_key` | 当前目录 |
| 生产环境 | `/etc/灵犀/.encryption_key` | 建议配置 |

---

## 二、安全配置文件

### 2.1 配置文件格式

加密配置文件 `.secure_config.json` 格式：

```json
{
  "llm_api_key": "加密后的API Key",
  "db_password": "加密后的密码",
  "api_secret": "加密后的密钥"
}
```

### 2.2 保存安全配置

```python
from config_secure import SecureConfigManager

manager = SecureConfigManager()

# 保存敏感配置（自动加密）
manager.save_secure_config({
    "llm_api_key": "sk-your-api-key",
    "db_password": "your-database-password",
    "external_api_secret": "your-secret-key"
})
```

### 2.3 加载安全配置

```python
from config_secure import SecureConfigManager

manager = SecureConfigManager()

# 加载并自动解密配置
config = manager.load_secure_config()

# 获取单个配置值
api_key = manager.get_secure_value("llm_api_key")
```

---

## 三、配置加载优先级

系统支持多层配置加载，优先级从高到低：

1. **环境变量**（最高优先级）
2. **加密配置文件**（`.secure_config.json`）
3. **默认配置值**（最低优先级）

### 3.1 环境变量配置

```bash
# 设置环境变量（Linux/macOS）
export LLM_API_KEY="sk-your-api-key"
export LLM_PROVIDER="openai"

# 设置环境变量（Windows PowerShell）
$env:LLM_API_KEY = "sk-your-api-key"
```

### 3.2 使用示例

```python
from config_secure import SecureConfigManager

manager = SecureConfigManager()

# 获取配置值（自动按优先级查找）
api_key = manager.get_secure_value("llm_api_key", "default-value")
```

---

## 四、日志脱敏配置

### 4.1 自动脱敏规则

系统自动检测并脱敏以下敏感信息：

| 类型 | 匹配模式 | 示例 |
|------|----------|------|
| API Key | `sk-xxx`, `pk-xxx` | `sk-proj-abc123def456` |
| JWT Token | `eyxxx`（长Base64字符串） | `eyJhbGciOiJIUzI1NiIs...` |
| 密码字段 | `password=xxx`, `secret=xxx` | `password="secret123"` |
| 密钥字段 | `api_key=xxx`, `access_token=xxx` | `api_key="sk-123"` |
| URL参数 | `?api_key=xxx`, `&token=xxx` | `?api_key=sk-abc123` |

### 4.2 使用日志过滤器

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

### 4.3 日志格式配置

推荐的日志格式（包含时间戳和级别）：

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
```

---

## 五、审计日志

### 5.1 审计日志记录

```python
from agent.logging_utils import get_audit_logger

audit_logger = get_audit_logger()

# 记录配置访问
audit_logger.log_config_access("api_key", "admin")

# 记录配置修改
audit_logger.log_config_modification("llm_settings", "admin")

# 记录安全配置访问
audit_logger.log_secure_config_access("api_key", True, "user1")

# 记录认证尝试
audit_logger.log_authentication("admin", True, "192.168.1.100")

# 记录敏感操作（自动脱敏详情）
audit_logger.log_sensitive_operation(
    "export_data",
    {"api_key": "sk-sensitive", "user": "admin"}
)
```

### 5.2 审计日志格式

审计日志输出到 `logs/audit.log`：

```
2026-06-03 09:07:19 [INFO] CONFIG_ACCESS | user=admin | key=api_key
2026-06-03 09:07:19 [INFO] AUTHENTICATION | username=admin | status=SUCCESS | ip=192.168.1.100
2026-06-03 09:07:19 [INFO] SENSITIVE_OPERATION | user=system | operation=export_data | details={"api_key": "***", "user": "admin"}
```

---

## 六、错误处理

### 6.1 异常类型

| 异常 | 说明 |
|------|------|
| `SecureConfigError` | 安全配置异常基类 |
| `DecryptionError` | 解密失败 |
| `KeyFileError` | 密钥文件错误 |
| `ConfigFileError` | 配置文件错误 |

### 6.2 错误处理示例

```python
from config_secure import SecureConfigManager, KeyFileError, DecryptionError

try:
    manager = SecureConfigManager()
    config = manager.load_secure_config()
except KeyFileError as e:
    print(f"密钥文件错误: {e}")
except DecryptionError as e:
    print(f"解密失败: {e}")
except Exception as e:
    print(f"配置加载失败: {e}")
```

---

## 七、安全最佳实践

### 7.1 密钥管理
- ✅ 定期备份密钥文件
- ✅ 限制密钥文件权限为 `0o600`
- ✅ 不要将密钥文件提交到版本控制系统
- ✅ 在生产环境使用安全的密钥存储服务

### 7.2 日志安全
- ✅ 始终启用日志脱敏过滤器
- ✅ 避免在日志中直接打印敏感信息
- ✅ 定期清理审计日志

### 7.3 配置安全
- ✅ 使用环境变量管理敏感配置（生产环境）
- ✅ 加密配置文件权限设置为 `0o600`
- ✅ 定期轮换敏感配置

---

## 八、API 参考

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
| `log_encryption_key_access(success, user)` | 记录密钥访问 |
| `log_permission_change(action, resource, user)` | 记录权限变更 |
| `log_authentication(username, success, ip)` | 记录认证尝试 |
| `log_sensitive_operation(operation, details, user)` | 记录敏感操作 |

---

## 九、常见问题

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
**最后更新**: 2026-06-03  
**作者**: 安全团队