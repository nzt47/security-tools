# 脱敏过滤器故障排查手册

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026年6月3日 |
| 适用对象 | 运维团队 |
| 紧急联系 | 安全团队 |

---

## 目录

1. [常见错误速查](#常见错误速查)
2. [脱敏过滤器故障排查](#脱敏过滤器故障排查)
3. [加密解密故障排查](#加密解密故障排查)
4. [审计日志故障排查](#审计日志故障排查)
5. [性能问题排查](#性能问题排查)
6. [紧急恢复流程](#紧急恢复流程)
7. [日志分析指南](#日志分析指南)
8. [联系支持](#联系支持)

---

## 一、常见错误速查

| 错误现象 | 可能原因 | 快速修复 |
|----------|----------|----------|
| 日志中出现明文API Key | 脱敏过滤器未启用 | 检查日志配置 |
| 解密失败错误 | 密钥文件不匹配 | 验证密钥文件 |
| 服务启动失败 | 缺少依赖库 | 安装cryptography |
| 性能下降 | 正则表达式过慢 | 优化正则规则 |
| 日志不输出 | 日志级别配置错误 | 调整日志级别 |

---

## 二、脱敏过滤器故障排查

### 2.1 故障现象：敏感信息未被脱敏

**检查步骤：**

1. **确认脱敏过滤器已加载**
   ```bash
   python -c "from agent.logging_utils import SensitiveDataFilter; f = SensitiveDataFilter(); print(f._sanitize('password=secret123'))"
   # 预期输出: password="***"
   ```

2. **检查日志配置**
   ```python
   # 确认logging配置中已添加SensitiveDataFilter
   handler.addFilter(SensitiveDataFilter())
   ```

3. **检查日志级别**
   - 确保日志级别设置为DEBUG或更高级别
   - 检查过滤器是否被正确添加到handler

4. **验证正则表达式**
   ```bash
   python -c "import re; print(re.sub(r'(password)\s*=\s*[^ ]*', r'\1=\"***\"', 'password=secret'))"
   ```

**常见原因：**
- 过滤器未正确注册到日志handler
- 日志记录器级别设置过高
- 正则表达式不匹配特定格式

### 2.2 故障现象：误脱敏正常数据

**检查步骤：**

1. **识别被误脱敏的内容**
   - 检查日志中被错误替换为`***`的内容

2. **调整正则表达式**
   - 修改`agent/logging_utils.py`中的正则规则
   - 添加更精确的边界匹配

3. **测试新规则**
   ```bash
   python test_sanitize_logs.py
   ```

**常见原因：**
- 正则表达式过于宽泛
- 缺少边界约束

### 2.3 故障现象：特殊字符导致异常

**检查步骤：**

1. **查看错误日志**
   ```bash
   grep -E "脱敏.*错误|TypeError|re.error" logs/*.log
   ```

2. **测试边界输入**
   ```python
   from agent.logging_utils import SensitiveDataFilter
   f = SensitiveDataFilter()
   test_inputs = [None, "", 123, "normal text", "😊", "a"*10000]
   for inp in test_inputs:
       try:
           result = f._sanitize(inp)
           print(f"OK: {type(inp).__name__}")
       except Exception as e:
           print(f"FAIL: {type(inp).__name__} - {e}")
   ```

**常见原因：**
- 输入类型不是字符串
- 超长文本导致内存问题
- 二进制数据混入

---

## 三、加密解密故障排查

### 3.1 故障现象：解密失败

**错误日志示例：**
```
[ERROR] 解密失败：无效的认证标签（密钥不匹配或数据被篡改）
```

**检查步骤：**

1. **验证密钥文件**
   ```bash
   # 检查密钥文件存在且长度正确（32字节）
   ls -la .encryption_key
   wc -c .encryption_key  # 应输出32
   ```

2. **检查密钥文件权限**
   ```bash
   # 权限应为0o600
   ls -la .encryption_key
   ```

3. **验证加密配置文件**
   ```bash
   # 检查配置文件存在
   ls -la .secure_config.json
   ```

4. **测试加密解密**
   ```bash
   python -c "
   from config_secure import SecureConfigManager
   m = SecureConfigManager()
   encrypted = m.encrypt('test')
   print('Encrypted:', encrypted)
   decrypted = m.decrypt(encrypted)
   print('Decrypted:', decrypted)
   "
   ```

**常见原因：**
- 密钥文件丢失或损坏
- 密钥文件权限不正确
- 加密配置文件被篡改

### 3.2 故障现象：密钥文件权限错误

**错误日志示例：**
```
[WARNING] 密钥文件权限过宽，建议设置为0o600
```

**修复步骤：**
```bash
chmod 600 .encryption_key
chmod 600 .secure_config.json
```

### 3.3 故障现象：配置加载失败

**检查步骤：**

1. **检查环境变量**
   ```bash
   echo $LLM_API_KEY
   echo $AGENT_CONFIG_PATH
   ```

2. **检查配置加载顺序**
   ```python
   from config_secure import SecureConfigManager
   m = SecureConfigManager()
   # 环境变量 > 加密文件 > 默认值
   value = m.get_secure_value('llm_api_key', 'default')
   print(f"Loaded value: {value}")
   ```

---

## 四、审计日志故障排查

### 4.1 故障现象：审计日志未记录

**检查步骤：**

1. **检查日志目录**
   ```bash
   ls -la logs/
   ```

2. **检查日志配置**
   ```python
   from agent.logging_utils import get_audit_logger
   audit_logger = get_audit_logger()
   audit_logger.log_config_access('test_key', 'admin')
   ```

3. **验证日志文件**
   ```bash
   cat logs/audit.log
   ```

**常见原因：**
- 日志目录不存在或无写入权限
- 审计日志器未正确初始化

### 4.2 故障现象：审计日志中出现敏感信息

**检查步骤：**

1. **检查脱敏配置**
   ```python
   from agent.logging_utils import SensitiveDataFilter
   f = SensitiveDataFilter()
   test_data = {"api_key": "sk-secret", "user": "admin"}
   sanitized = f._sanitize_dict(test_data)
   print(sanitized)  # 应输出 {"api_key": "***", "user": "admin"}
   ```

2. **检查AuditLogger实现**
   - 确认`log_sensitive_operation`方法调用了`_sanitize_dict`

---

## 五、性能问题排查

### 5.1 故障现象：处理速度慢

**检查步骤：**

1. **运行性能测试**
   ```bash
   python test_performance.py
   ```

2. **监控资源使用**
   ```bash
   # 实时监控
   top -p $(pgrep -f python)
   ```

3. **分析正则表达式性能**
   ```python
   import time
   from agent.logging_utils import SensitiveDataFilter
   
   f = SensitiveDataFilter()
   test_text = "API Key: sk-proj-abc123 " * 1000
   
   start = time.time()
   for _ in range(10000):
       f._sanitize(test_text)
   elapsed = time.time() - start
   print(f"10000次处理耗时: {elapsed:.2f}秒")
   ```

**优化建议：**
- 减少正则表达式数量
- 使用更高效的正则模式
- 考虑使用编译后的正则表达式

### 5.2 故障现象：内存占用过高

**检查步骤：**

1. **监控内存使用**
   ```bash
   ps aux | grep python | grep -v grep
   ```

2. **检查日志消息大小**
   - 避免处理超大日志消息
   - 考虑对超长消息进行截断

---

## 六、紧急恢复流程

### 6.1 脱敏过滤器失效

**恢复步骤：**

1. **确认问题**
   ```bash
   grep -E "sk-proj-|password=|token=" logs/application.log | head -10
   ```

2. **临时解决方案**
   ```python
   # 在紧急情况下，可以临时禁用脱敏（不推荐）
   # 修改logging配置，移除SensitiveDataFilter
   ```

3. **根本修复**
   - 检查并修复`agent/logging_utils.py`
   - 重启服务

### 6.2 密钥丢失/损坏

**恢复步骤：**

1. **停止服务**
   ```bash
   systemctl stop agent.service
   ```

2. **从备份恢复密钥**
   ```bash
   cp /backup/encryption_key .encryption_key
   chmod 600 .encryption_key
   ```

3. **验证恢复**
   ```bash
   python -c "from config_secure import SecureConfigManager; m = SecureConfigManager(); print(m.load_secure_config())"
   ```

4. **重启服务**
   ```bash
   systemctl start agent.service
   ```

### 6.3 服务无法启动

**检查步骤：**

1. **查看启动日志**
   ```bash
   journalctl -u agent.service -f
   ```

2. **检查依赖**
   ```bash
   pip list | grep cryptography
   ```

3. **验证Python版本**
   ```bash
   python --version  # 需>=3.10
   ```

---

## 七、日志分析指南

### 7.1 查找脱敏失败记录

```bash
# 查找可能包含敏感信息的日志
grep -E "sk-[a-zA-Z0-9_-]{10,}|password=[^*]|token=[^*]" logs/*.log

# 统计脱敏错误
grep -c "脱敏.*错误" logs/*.log
```

### 7.2 性能监控

```bash
# 统计每秒日志数量
awk '{print $1, $2}' logs/application.log | uniq -c | head -20

# 查找慢日志
grep -E "\[ERROR\]|\[WARNING\]" logs/application.log
```

### 7.3 审计日志分析

```bash
# 统计认证失败次数
grep "AUTHENTICATION.*status=FAILED" logs/audit.log | wc -l

# 查找敏感操作
grep "SENSITIVE_OPERATION" logs/audit.log
```

---

## 八、联系支持

### 8.1 需要联系安全团队的情况

1. 密钥文件丢失且无备份
2. 检测到安全漏洞或数据泄露
3. 脱敏规则需要重大变更
4. 性能问题无法通过常规手段解决

### 8.2 准备信息

联系支持前，请准备：

1. **错误日志**
   ```bash
   tar -czvf logs.tar.gz logs/
   ```

2. **配置文件**
   - `.secure_config.json`（脱敏后）
   - `config.py`

3. **环境信息**
   ```bash
   python --version
   pip list | grep cryptography
   uname -a
   ```

4. **问题描述**
   - 发生时间
   - 影响范围
   - 已尝试的解决方案

---

## 附录：常用命令

### A.1 自动化诊断脚本

```bash
# 运行快速诊断（基础检查）
python diagnose.py

# 运行完整诊断（包含性能测试）
python diagnose.py --full
```

**诊断脚本功能：**
- ✅ Python版本检查
- ✅ 依赖库版本检查
- ✅ 加密密钥文件检查
- ✅ 加密配置文件检查
- ✅ 日志文件检查
- ✅ 脱敏过滤器测试
- ✅ 加密解密功能测试
- ✅ 审计日志功能测试
- ⚡ 性能测试（完整模式）

### A.2 手动验证命令

```bash
# 验证脱敏功能
python -c "from agent.logging_utils import SensitiveDataFilter; f = SensitiveDataFilter(); print(f._sanitize('password=secret123'))"

# 验证加密功能
python -c "from config_secure import SecureConfigManager; m = SecureConfigManager(); print(m.encrypt('test'))"

# 运行单元测试
python -m pytest tests/unit/test_config_secure.py -v

# 运行性能测试
python test_performance.py

# 查看日志级别
python -c "import logging; print(logging.getLogger().level)"

# 检查密钥文件权限
ls -la .encryption_key

# 查看审计日志
cat logs/audit.log

# 查找可能的敏感信息泄露
grep -E "sk-[a-zA-Z0-9_-]{10,}" logs/application.log
```

### A.3 诊断脚本输出示例

```
======================================================================
          安全配置自动诊断工具
======================================================================
诊断时间: 2026-06-03 10:38:16

📋 系统环境检查
----------------------------------------
✅ Python版本: 3.12.0

📦 依赖库检查
----------------------------------------
✅ 加密库: 48.0.0
✅ 系统监控库: 7.2.2

📁 文件检查
----------------------------------------
✅ 加密密钥文件正常
✅ 加密配置文件格式正确

🔧 功能测试
----------------------------------------
✅ 脱敏过滤器测试通过
✅ 加密解密测试通过
✅ 审计日志功能正常

======================================================================
📊 诊断结果汇总
======================================================================
通过: 6/6

🎉 所有检查通过！安全配置运行正常。
```

---

**最后更新**: 2026年6月3日
