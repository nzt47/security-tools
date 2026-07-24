# Release Notes — v1.3.1

## MCP 日志级别安全监控增强

> **版本**: v1.3.1
> **发布日期**: 2026-07-25
> **类型**: feat (新功能 — 可观测性增强)
> **提交**: `d7b427b3` (master) / `c8e71a80` (feature/tlm-l3)
> **影响范围**: MCP 协议工具执行器 + 监控部署栈

---

## 一、功能概述

针对 MCP 协议工具执行器（`agent/mcp_executor.py`）的日志级别控制，新增**安全防护 + 实时监控 + 告警闭环**三层能力，防止 `MCP_LOG_LEVEL=CRITICAL` 等恶意值抑制 ERROR 日志、掩盖错误痕迹。

### 核心价值

| 能力 | 说明 |
|------|------|
| 安全防护 | 模块加载时校验日志级别白名单，拒绝 CRITICAL 等非运维值并自动回退 INFO |
| 实时监控 | 轻量级指标导出器（仅依赖 stdlib，64MB 容器稳定运行）暴露 3 个 Prometheus 指标 |
| 告警闭环 | CRITICAL 回退 → 指标变更 → Prometheus 触发 → Alertmanager 接收，端到端 < 1 分钟 |
| 健康检查 | Docker healthcheck 探针自动检测回退状态，回退即 UNHEALTHY |

---

## 二、新增/变更文件

| 文件 | 类型 | 行数 | 说明 |
|------|------|------|------|
| `agent/mcp_executor.py` | 新增 | 579 | MCP 执行器 + 日志级别白名单校验 + `get_log_level_status()` 接口 |
| `scripts/check_mcp_log_level.py` | 新增 | 185 | 健康检查 + Prometheus 指标导出器（双模式） |
| `deploy/monitoring/docker-compose.yml` | 修改 | +85 | 新增 `mcp-log-monitor` 服务 + healthcheck 探针 |
| `deploy/monitoring/prometheus/alert_rules.yml` | 修改 | +150 | 新增 `Yunshu_mcp_log_level_alerts` 告警组（3 条规则） |
| `deploy/monitoring/prometheus/prometheus.yml` | 修改 | +14 | 新增 `mcp-log-monitor` 抓取任务（15s 间隔） |
| `docs/PROD_LOG_LEVEL_RUNBOOK.md` | 新增 | 369 | 生产环境日志级别切换操作手册 |
| `tests/unit/test_mcp_executor.py` | 新增 | 864 | 58 个单元测试（含 CRITICAL 回退场景） |

**合计**: 7 文件，+2245 行，-1 行

---

## 三、技术架构

### 3.1 日志级别优先级

```
优先级 1 (最高): CLI 参数 --verbose / -v        → DEBUG
优先级 2:        环境变量 MCP_LOG_LEVEL           → DEBUG/INFO/WARNING/ERROR
优先级 3 (最低): 默认 INFO                       → INFO
```

### 3.2 有效级别白名单

```
DEBUG  <  INFO  <  WARNING  <  ERROR
```

> ⚠️ **CRITICAL 不在白名单内**。虽是 Python logging 内置级别（50），但会抑制 ERROR（40）日志，视为恶意值/误配，模块加载时自动回退 INFO。

### 3.3 监控链路

```
mcp_executor.py (CRITICAL 回退, _LOG_LEVEL_FALLBACK=True)
        ↓
check_mcp_log_level.py --serve (端口 9102, 暴露 /metrics)
        ↓ 指标: mcp_log_level_fallback = 1
Prometheus (15s 抓取, 存入 TSDB)
        ↓ 触发: McpLogLevelFallback (expr: mcp_log_level_fallback == 1)
Alertmanager (severity=critical, status=active)
        ↓ 通知
运维团队
```

### 3.4 暴露的 Prometheus 指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `mcp_log_level_current` | gauge | 当前生效日志级别数值（10/20/30/40） |
| `mcp_log_level_fallback` | gauge | 是否发生无效值回退（0=正常, 1=回退） |
| `mcp_log_level_configured_value` | gauge | MCP_LOG_LEVEL 原始配置值数值（无效值=0） |

---

## 四、新增告警规则

| 告警名 | severity | 触发条件 | for | 说明 |
|--------|----------|----------|-----|------|
| `McpLogLevelFallback` | critical | `mcp_log_level_fallback == 1` | 0m | 日志级别异常回退，疑似恶意配置 |
| `McpLogLevelExporterDown` | warning | `absent(mcp_log_level_current) or up{job="mcp-log-monitor"} == 0` | 1m | 监控导出器宕机 |
| `McpLogLevelUnknown` | warning | `mcp_log_level_current == 0` | 2m | 日志级别未知，启动异常 |

---

## 五、验证结果

### 5.1 静态校验

| 校验项 | 工具 | 结果 |
|--------|------|------|
| 告警规则 PromQL 语法 | `promtool check rules` | ✅ SUCCESS: 19 rules found |
| Prometheus 主配置 | `promtool check config` | ✅ 2 rule files (19 + 5 rules) |
| YAML 结构完整性 | Python yaml + 结构校验 | ✅ 3 组 19 规则，均含 alert/expr/for/labels |

### 5.2 运行时验证（Docker Compose 端到端）

| 场景 | 验证点 | 结果 |
|------|--------|------|
| **正常（INFO）** | 容器健康状态 | ✅ healthy |
| | 指标 `mcp_log_level_fallback` | ✅ = 0 |
| | Prometheus target | ✅ UP（抓取成功） |
| | 3 指标入库 TSDB | ✅ current=20, fallback=0 |
| **CRITICAL 恶意值** | 容器健康状态 | ✅ unhealthy（36s 检测） |
| | 指标 `mcp_log_level_fallback` | ✅ = 1（original="CRITICAL"） |
| | Prometheus 告警 `McpLogLevelFallback` | ✅ state=**firing** |
| | Alertmanager 接收 | ✅ severity=critical, status=**active** |
| | 完整链路耗时 | ✅ < 1 分钟 |

### 5.3 单元测试

```
python -m pytest tests/unit/test_mcp_executor.py -q
============================= 58 passed in 3.84s ==============================
```

覆盖场景：
- `TestDynamicLogLevel` — 运行时 API 调整 + 无效值回退
- `TestVerboseCliFlag` — CLI `--verbose` 参数 + env 覆盖优先级 + CRITICAL 恶意值
- `TestMcpClientLoggerGracefulDegradation` — logger 配置缺失优雅降级

---

## 六、配置说明

### 6.1 环境变量

```env
# MCP 执行器日志级别（DEBUG/INFO/WARNING/ERROR）
MCP_LOG_LEVEL=INFO
```

### 6.2 部署

```bash
# 启动监控栈（含 mcp-log-monitor）
docker compose -f deploy/monitoring/docker-compose.yml up -d

# 验证健康状态
docker ps --filter "name=mcp-log-monitor"
# 期望: Up (healthy)

# 验证指标
curl -s http://localhost:9102/metrics | grep mcp_log_level_fallback
# 期望: mcp_log_level_fallback{...} 0
```

### 6.3 运行时切换日志级别（无需重启）

```python
from agent.mcp_executor import set_logger_level, get_logger_level

set_logger_level("DEBUG")  # 立即生效
set_logger_level("INFO")   # 排查完毕恢复
```

详见 [PROD_LOG_LEVEL_RUNBOOK.md](../PROD_LOG_LEVEL_RUNBOOK.md)。

---

## 七、回滚方案

```bash
# 方案 A: revert 提交（保留历史）
git revert d7b427b3

# 方案 B: 重启服务恢复 INFO（不回滚代码）
docker compose -f deploy/monitoring/docker-compose.yml restart mcp-log-monitor
# 默认 MCP_LOG_LEVEL=INFO，无需改配置
```

---

## 八、已知限制

1. `mcp-log-monitor` 指标导出器仅在 `deploy/monitoring/`（生产环境）部署，开发环境 `monitoring/` 未同步
2. CRITICAL 回退为模块加载时一次性校验，运行时通过 `set_logger_level()` 传入 CRITICAL 也会被拒绝（白名单校验）
3. `get_log_level_status()` 函数暂无专属单元测试，但已通过 healthcheck 与指标导出端到端验证

---

## 九、变更记录

| 日期 | 提交 | 内容 |
|------|------|------|
| 2026-07-25 | `c8e71a80` | 初始提交（feature/tlm-l3 分支） |
| 2026-07-25 | `d7b427b3` | cherry-pick 至 master |
