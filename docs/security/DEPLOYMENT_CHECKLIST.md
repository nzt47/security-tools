# 安全配置部署检查清单

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026年6月3日 |
| 适用环境 | 生产环境部署 |
| 审核状态 | ✅ 待审核 |

---

## 目录

1. [部署前检查](#部署前检查)
2. [代码部署检查](#代码部署检查)
3. [配置验证](#配置验证)
4. [安全性检查](#安全性检查)
5. [性能测试](#性能测试)
6. [监控告警配置](#监控告警配置)
7. [回滚计划](#回滚计划)

---

## 一、部署前检查

| 序号 | 检查项 | 检查方法 | 状态 |
|------|--------|----------|------|
| 1 | Python版本 >= 3.10 | `python --version` | |
| 2 | cryptography库 >= 42.0.0 | `pip show cryptography` | |
| 3 | 项目依赖已安装 | `pip install -r requirements.txt` | |
| 4 | 密钥文件备份完成 | 确认备份存在 | |
| 5 | 部署窗口已审批 | 确认运维工单 | |

---

## 二、代码部署检查

### 2.1 核心文件

| 序号 | 文件路径 | 检查内容 | 状态 |
|------|----------|----------|------|
| 1 | `agent/logging_utils.py` | 脱敏过滤器已包含异常处理 | |
| 2 | `config_secure.py` | AES-GCM加密模块已更新 | |
| 3 | `config.py` | 安全配置管理器已集成 | |

### 2.2 脱敏规则版本

| 脱敏类型 | 版本 | 状态 |
|----------|------|------|
| API Key | v1.3 | ✅ |
| JWT Token | v1.3 | ✅ |
| 密码字段 | v1.3 | ✅ |
| 手机号（大陆） | v1.2 | ✅ |
| 手机号（香港） | v1.2 | ✅ |
| 身份证号（18位） | v1.2 | ✅ |
| 身份证号（15位） | v1.2 | ✅ |
| URL参数 | v1.3 | ✅ |

### 2.3 异常处理增强

| 检查项 | 说明 | 状态 |
|--------|------|------|
| 正则表达式异常捕获 | `re.error` | ✅ |
| 类型错误捕获 | `TypeError` | ✅ |
| 通用异常捕获 | `Exception` | ✅ |
| 边界输入处理 | None、空字符串、数字等 | ✅ |

---

## 三、配置验证

### 3.1 配置加载优先级

| 优先级 | 来源 | 说明 | 测试方法 |
|--------|------|------|----------|
| 1 | 环境变量 | 最高优先级 | `echo $LLM_API_KEY` |
| 2 | 加密配置文件 | `.secure_config.json` | 检查文件存在 |
| 3 | 默认值 | 最低优先级 | 测试回退逻辑 |

### 3.2 测试命令

```bash
# 验证加密配置加载
python -c "from config_secure import SecureConfigManager; m = SecureConfigManager(); print('API Key:', m.get_secure_value('llm_api_key', 'not_found'))"

# 验证环境变量优先级
export TEST_KEY=env_value
python -c "from config_secure import SecureConfigManager; m = SecureConfigManager(); print('TEST_KEY:', m.get_secure_value('test_key'))"

# 验证脱敏过滤器
python -c "from agent.logging_utils import SensitiveDataFilter; f = SensitiveDataFilter(); print(f._sanitize('password=secret123'))"
```

---

## 四、安全性检查

### 4.1 文件权限

| 文件 | 要求权限 | 检查命令 | 状态 |
|------|----------|----------|------|
| `.encryption_key` | 0o600 | `ls -la .encryption_key` | |
| `.secure_config.json` | 0o600 | `ls -la .secure_config.json` | |
| `logs/` | 0o755 | `ls -la logs/` | |

### 4.2 密钥管理

| 检查项 | 说明 | 状态 |
|--------|------|------|
| 密钥长度 | 32字节（256位） | ✅ |
| 密钥备份 | 异地备份完成 | |
| 密钥轮换 | 轮换策略已制定 | |

### 4.3 敏感信息保护

| 检查项 | 验证方法 | 状态 |
|--------|----------|------|
| 日志无明文API Key | 检查日志输出 | |
| 日志无明文密码 | 检查日志输出 | |
| 审计日志已脱敏 | 检查`logs/audit.log` | |

---

## 五、性能测试

### 5.1 基准测试命令

```bash
# 运行并发测试
python test_concurrent_sanitize.py

# 运行单元测试
python -m pytest tests/unit/test_config_secure.py -v

# 性能压力测试
python test_performance.py
```

### 5.2 性能指标要求

| 指标 | 要求 |
|------|------|
| 并发线程数 | ≥100 |
| 处理速度 | ≥10000条/秒 |
| 错误率 | =0 |
| 内存占用 | ≤100MB |

---

## 六、监控告警配置

### 6.1 日志监控

| 监控项 | 告警阈值 | 告警方式 |
|--------|----------|----------|
| 脱敏失败次数 | >0 | 日志告警 |
| 解密失败次数 | >0 | 即时告警 |
| 密钥文件访问异常 | >0 | 即时告警 |

### 6.2 性能监控

| 监控项 | 告警阈值 | 告警方式 |
|--------|----------|----------|
| 处理延迟 | >100ms | 警告 |
| 错误率 | >0.1% | 严重告警 |

---

## 七、回滚计划

### 7.1 回滚步骤

1. **停止服务**
   ```bash
   systemctl stop agent.service
   ```

2. **备份当前版本**
   ```bash
   tar -czvf backup_$(date +%Y%m%d).tar.gz agent/ config_secure.py
   ```

3. **恢复上一版本**
   ```bash
   git checkout HEAD~1
   ```

4. **验证恢复**
   ```bash
   python -m pytest tests/unit/test_config_secure.py -v
   ```

5. **重启服务**
   ```bash
   systemctl start agent.service
   ```

### 7.2 回滚验证

| 检查项 | 验证方法 |
|--------|----------|
| 服务状态 | `systemctl status agent.service` |
| 日志输出 | 检查无脱敏错误 |
| 功能测试 | 运行测试用例 |

---

## 部署签名

| 角色 | 签名 | 日期 |
|------|------|------|
| 开发负责人 | | |
| 运维负责人 | | |
| 安全负责人 | | |

---

**最后更新**: 2026年6月3日
