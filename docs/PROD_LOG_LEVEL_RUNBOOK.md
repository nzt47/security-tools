# 生产环境日志级别切换操作手册

> **适用模块**: `agent/mcp_executor.py` (MCP 协议工具执行器)
> **版本**: v1.0 — 2026-07-24
> **维护者**: 云枢可观测性团队

---

## 1. 优先级总览

日志级别按以下优先级从高到低生效（前者覆盖后者）：

```
┌─────────────────────────────────────────────────────┐
│  优先级 1 (最高): CLI 参数 --verbose / -v           │
│                     ↓ 覆盖                          │
│  优先级 2:        环境变量 MCP_LOG_LEVEL             │
│                     ↓ 覆盖                          │
│  优先级 3 (最低): 默认 INFO                          │
└─────────────────────────────────────────────────────┘
```

| 来源 | 级别 | 生效时机 | 适用场景 |
|------|------|----------|----------|
| `--verbose` / `-v` | DEBUG | CLI 启动时立即生效 | 临时排查，无需改配置或重启服务 |
| `MCP_LOG_LEVEL=DEBUG` | DEBUG | 进程启动时（模块加载） | 计划内排查，需重启服务 |
| `MCP_LOG_LEVEL=WARNING` | WARNING | 进程启动时 | 生产环境降低日志噪音 |
| `MCP_LOG_LEVEL=ERROR` | ERROR | 进程启动时 | 仅记录错误，最低日志量 |
| 未设置 | INFO | 进程启动时 | **默认值**，生产推荐 |
| `set_logger_level()` API | 任意 | 运行时立即生效 | 不重启进程动态调整 |

### 有效日志级别白名单

```
DEBUG  <  INFO  <  WARNING  <  ERROR
```

> ⚠️ **CRITICAL 不在白名单内**。虽然 Python logging 内置 CRITICAL(50)，但它会抑制 ERROR(40) 日志，不属于运维白名单。模块加载时自动拒绝并回退到 INFO。

---

## 2. CLI 参数方式（优先级最高）

### 2.1 基本用法

```bash
# 默认 INFO 级别自检
python agent/mcp_executor.py

# --verbose 切换到 DEBUG（输出 initialize/list_tools 等详细协议日志）
python agent/mcp_executor.py --verbose

# -v 短标志（等价于 --verbose）
python agent/mcp_executor.py -v

# 指定工具和端点
python agent/mcp_executor.py -v --tool db_query --endpoint https://mcp.example.com/db
```

### 2.2 优先级覆盖示例

```bash
# 场景: 生产环境 MCP_LOG_LEVEL=WARNING（抑制 INFO 日志）
# 排查时 --verbose 强制切换 DEBUG，覆盖 WARNING

MCP_LOG_LEVEL=WARNING python agent/mcp_executor.py --verbose
# 输出: [CLI] --verbose 模式: 日志级别已切换为 DEBUG
# DEBUG 级别日志可见（initialize 成功日志出现）
```

```bash
# 对照: 无 --verbose 时 env 生效
MCP_LOG_LEVEL=WARNING python agent/mcp_executor.py
# 输出: [CLI] 默认模式: 日志级别=WARNING
# DEBUG 级别日志不可见（WARNING > DEBUG，被过滤）
```

### 2.3 Docker 容器内使用

```bash
# 一次性运行（不启动 visibility-exporter 常驻服务）
docker compose -f deploy/monitoring/docker-compose.yml run --rm --no-deps \
  visibility-exporter "python agent/mcp_executor.py --verbose"
```

---

## 3. 环境变量方式（优先级中）

### 3.1 Docker Compose 部署

`deploy/monitoring/docker-compose.yml` 已配置：

```yaml
visibility-exporter:
  image: python:3.11-slim
  environment:
    - MCP_LOG_LEVEL=${MCP_LOG_LEVEL:-INFO}  # 默认 INFO，可通过 .env 覆盖
    - PYTHONPATH=/app
  command:
    - |
      echo "[startup] MCP_LOG_LEVEL=${MCP_LOG_LEVEL:-INFO}" &&
      python scripts/visibility_report.py --serve-metrics --port 9101
```

#### 临时切换（不改配置文件）

```bash
# 启动时指定 DEBUG（一次性）
MCP_LOG_LEVEL=DEBUG docker compose -f deploy/monitoring/docker-compose.yml up -d visibility-exporter

# 恢复 INFO
docker compose -f deploy/monitoring/docker-compose.yml up -d visibility-exporter
```

#### 持久化配置（通过 .env 文件）

在 `deploy/monitoring/` 目录下创建或编辑 `.env` 文件：

```env
# MCP 执行器日志级别（DEBUG/INFO/WARNING/ERROR）
MCP_LOG_LEVEL=INFO

# Grafana 管理员密码（生产环境务必修改）
GF_SECURITY_ADMIN_PASSWORD=YourStrongPassword
```

```bash
docker compose -f deploy/monitoring/docker-compose.yml up -d
```

### 3.2 Kubernetes 部署

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mcp-log-config
data:
  MCP_LOG_LEVEL: "INFO"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: visibility-exporter
spec:
  template:
    spec:
      containers:
        - name: exporter
          image: yunshu/visibility-exporter:latest
          envFrom:
            - configMapRef:
                name: mcp-log-config
```

#### 运行时切换（不重启 Pod）

```bash
# 修改 ConfigMap
kubectl edit configmap mcp-log-config
# 将 MCP_LOG_LEVEL: "INFO" 改为 "DEBUG"

# 滚动重启使配置生效
kubectl rollout restart deployment/visibility-exporter
```

### 3.3 直接进程启动

```bash
# 默认 INFO
python scripts/visibility_report.py --serve-metrics

# 指定 DEBUG
MCP_LOG_LEVEL=DEBUG python scripts/visibility_report.py --serve-metrics

# 指定 ERROR（仅记录错误）
MCP_LOG_LEVEL=ERROR python scripts/visibility_report.py --serve-metrics
```

---

## 4. 运行时 API 方式（无需重启）

### 4.1 Python 代码调用

```python
from agent.mcp_executor import set_logger_level, get_logger_level

# 查询当前级别
print(get_logger_level())  # "INFO"

# 切换到 DEBUG（立即生效，无需重启进程）
set_logger_level("DEBUG")  # 返回 "DEBUG"

# 排查完毕恢复 INFO
set_logger_level("INFO")   # 返回 "INFO"
```

### 4.2 无效输入处理

```python
set_logger_level("VERBOSE")  # 无效值，回退 INFO，输出警告日志
# [MCP] 无效日志级别 'VERBOSE',回退到 INFO。有效值: ['DEBUG', 'ERROR', 'INFO', 'WARNING']
# 返回 "INFO"

set_logger_level("")         # 空字符串，回退 INFO
# 返回 "INFO"

set_logger_level("debug")    # 大小写不敏感
# 返回 "DEBUG"
```

### 4.3 适用场景

| 场景 | 推荐方式 | 原因 |
|------|----------|------|
| 临时排查（5分钟） | `set_logger_level("DEBUG")` | 无需重启，立即生效 |
| 计划内排查（1小时） | `MCP_LOG_LEVEL=DEBUG` + 重启 | 重启后持续 DEBUG |
| 快速验证 | `--verbose` CLI | 一次性运行，不污染运行中服务 |
| 生产常态 | 默认 INFO | 平衡可观测性与性能 |

---

## 5. 安全防护：恶意值自动回退

### 5.1 CRITICAL 恶意值防护

Python logging 内置级别 `CRITICAL(50)` 会抑制 `ERROR(40)` 日志，导致关键错误信息丢失。本模块在模块加载时自动拒绝 CRITICAL 并回退到 INFO：

```bash
$ MCP_LOG_LEVEL=CRITICAL python agent/mcp_executor.py
[MCP] 无效日志级别 'CRITICAL',回退到 INFO。有效值: ['DEBUG', 'ERROR', 'INFO', 'WARNING']
[ERROR] [MCP] 自检探针 | 级别=INFO | endpoint=https://mcp.example.com/test | tool=db_query
[CLI] 默认模式: 日志级别=INFO
```

### 5.2 防护机制说明

| 层级 | 机制 | 触发时机 |
|------|------|----------|
| 模块加载 | `_MCP_LOG_LEVEL not in _VALID_LOG_LEVELS` → 回退 INFO | 进程启动 |
| 运行时 API | `set_logger_level()` 内部白名单校验 | 代码调用 |
| CLI | `--verbose` 始终强制 DEBUG，不受 env 影响 | CLI 启动 |

### 5.3 ERROR 探针日志

`__main__` 块启动时输出一条 ERROR 级别探针日志，用于验证 ERROR 日志链路畅通：

```
[ERROR] [MCP] 自检探针 | 级别=INFO | endpoint=... | tool=db_query
```

- 若级别为 INFO → 探针可见（INFO < ERROR，ERROR 日志正常输出）
- 若级别被恶意设为 CRITICAL 且未修复 → 探针被抑制（CRITICAL > ERROR）
- 当前实现已修复此问题 → CRITICAL 自动回退 INFO → 探针始终可见

---

## 6. 日志输出说明

### 6.1 各级别输出的日志内容

| 级别 | 输出内容 | 日志量 |
|------|----------|--------|
| DEBUG | initialize 成功日志 + call_tool 进入/退出 + 所有 INFO 内容 | 高 |
| INFO | call_tool 进入/退出日志（含耗时、结果、异常类型） | 中 |
| WARNING | 无效日志级别警告 + 配置异常 | 低 |
| ERROR | 自检探针日志 + 严重错误 | 最低 |

### 6.2 日志格式

```
2026-07-24 01:17:56,437 [INFO] __main__: [MCP] call_tool 进入 | tool=db_query | endpoint=https://mcp.example.com/db | timeout=5.0s
2026-07-24 01:17:56,448 [INFO] __main__: [MCP] call_tool 退出 | tool=db_query | 耗时=10.5ms | 结果=成功 | 异常=无
```

### 6.3 日志输出通道

| 阶段 | 输出通道 | 说明 |
|------|----------|------|
| 模块加载时（CRITICAL 回退警告） | stderr（lastResort） | Python logging 兜底机制 |
| `__main__` 块运行时 | stderr（basicConfig StreamHandler） | 正常日志输出 |
| `print()` 语句 | stdout | CLI 状态信息（模式标记、自检结果） |

---

## 7. 故障排查

### 7.1 日志不输出

| 症状 | 可能原因 | 解决方案 |
|------|----------|----------|
| 无任何日志输出 | 级别设为 ERROR 或 WARNING | 检查 `MCP_LOG_LEVEL` 环境变量 |
| DEBUG 日志缺失 | 级别为 INFO（默认） | 使用 `--verbose` 或 `MCP_LOG_LEVEL=DEBUG` |
| 模块加载警告缺失 | logger 无 handler（配置缺失） | 检查 `logging.basicConfig()` 是否调用 |
| `--verbose` 无效 | 代码版本过旧 | 确认 `mcp_executor.py` 含 `__main__` 块 |

### 7.2 级别不生效

```bash
# 验证当前生效级别
python agent/mcp_executor.py
# 输出: [CLI] 默认模式: 日志级别=INFO

# 验证 --verbose 覆盖
MCP_LOG_LEVEL=WARNING python agent/mcp_executor.py --verbose
# 输出: [CLI] --verbose 模式: 日志级别已切换为 DEBUG
```

### 7.3 Docker 容器日志不输出

```bash
# 查看容器启动日志
docker logs yunshu-prod-visibility-exporter 2>&1 | head -5
# 应看到: [startup] MCP_LOG_LEVEL=INFO (mcp_executor 动态日志级别)

# 验证环境变量是否注入
docker exec yunshu-prod-visibility-exporter env | grep MCP_LOG_LEVEL
# 应看到: MCP_LOG_LEVEL=INFO

# 容器内手动验证
docker compose -f deploy/monitoring/docker-compose.yml run --rm --no-deps \
  visibility-exporter "python agent/mcp_executor.py --verbose"
```

### 7.4 CRITICAL 恶意值排查

```bash
# 模拟容器内环境变量被恶意修改
docker compose -f deploy/monitoring/docker-compose.yml run --rm --no-deps \
  -e MCP_LOG_LEVEL=CRITICAL visibility-exporter "python agent/mcp_executor.py"

# 预期输出:
# [MCP] 无效日志级别 'CRITICAL',回退到 INFO。有效值: ['DEBUG', 'ERROR', 'INFO', 'WARNING']
# [ERROR] [MCP] 自检探针 | 级别=INFO | ...
# [CLI] 默认模式: 日志级别=INFO
```

如果未看到回退警告，说明模块加载校验逻辑缺失，需检查 `mcp_executor.py` 第 36-46 行。

---

## 8. 单元测试覆盖

| 测试类 | 测试场景 | 验证点 |
|--------|----------|--------|
| `TestDynamicLogLevel` | 运行时 API 调整 | set/get_logger_level 功能 + 无效值回退 |
| `TestVerboseCliFlag` | CLI --verbose 参数 | DEBUG 切换 + 短标志 + env 覆盖优先级 |
| `TestVerboseCliFlag` | CRITICAL 恶意值 | 程序正常启动 + 回退 INFO + ERROR 探针可见 |
| `TestMcpClientLoggerGracefulDegradation` | logger 配置缺失 | 优雅降级 + lastResort 兜底 + 对照实验 |

运行测试：

```bash
python -m pytest tests/unit/test_mcp_executor.py -v
# 预期: 58 passed
```

---

## 9. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|----------|----------|
| 2026-07-24 | 新增模块加载时 MCP_LOG_LEVEL 校验（CRITICAL 回退 INFO） | `mcp_executor.py` L36-46 |
| 2026-07-24 | 新增 ERROR 探针日志（__main__ 块） | `mcp_executor.py` L517-520 |
| 2026-07-24 | 新增 test_env_critical_falls_back_to_info 测试 | `test_mcp_executor.py` |
| 2026-07-24 | Docker Compose 集成 MCP_LOG_LEVEL 环境变量 | `docker-compose.yml` visibility-exporter |
