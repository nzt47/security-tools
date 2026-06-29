# 云枢系统三层穿透式测试计划

> 生成时间：2026-06-25
> 版本：v1.0
> 适用范围：云枢(Yunshu)智能代理系统全模块

---

## 一、测试体系总览

### 1.1 设计理念

本测试计划遵循**"三层穿透式评估"**框架，旨在彻底验证AI生成的功能模块是否真正发挥业务价值，而非仅依赖编译通过或表面功能演示。

```
┌─────────────────────────────────────────────────────────────────┐
│                     三层穿透式评估体系                           │
├─────────────────────────────────────────────────────────────────┤
│  第三层：系统层 - 集成影响分析                                    │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  跨模块交互测试 | 非功能性影响监控 | 混沌工程容错验证     │  │
│  └─────────────────────────────────────────────────────────┘  │
│  第二层：业务层 - 业务指标关联验证                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  功能调用频次 | 错误率监控 | 性能耗时 | 转化率对比        │  │
│  └─────────────────────────────────────────────────────────┘  │
│  第一层：基础层 - 单元测试逻辑正确性                              │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  边界值测试 | 覆盖率≥80% | 异常路径覆盖 | 需求规格对齐     │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 质量门禁总表

| 层级 | 检查项 | 阈值 | CI策略 | 不达标后果 |
|------|--------|------|--------|-----------|
| 基础层 | 单元测试通过率 | ≥95% | 阻止合并 | 代码不得合并主干 |
| 基础层 | 核心模块覆盖率 | ≥80% | 阻止合并 | 代码不得合并主干 |
| 基础层 | P0测试通过率 | 100% | 阻止合并 | 代码不得合并主干 |
| 基础层 | 安全扫描高危漏洞 | 0个 | 阻止合并 | 代码不得合并主干 |
| 业务层 | 核心功能埋点覆盖率 | 100% | 警告 | 需补充埋点后方可上线 |
| 业务层 | 业务指标回归 | 无负向变化 | 警告 | 需分析根因并优化 |
| 系统层 | 集成测试通过率 | ≥90% | 阻止合并 | 代码不得合并主干 |
| 系统层 | 混沌测试关键路径 | 全部通过 | 警告 | 需评估风险后决策 |

---

## 二、第一层：基础层 - 单元测试验证逻辑正确性

### 2.1 测试目标

验证AI生成的代码是否**严格遵循需求规格**，而非仅实现表面功能。确保核心分支和异常路径得到充分覆盖，**单元测试覆盖率≥80%**。

### 2.2 模块覆盖率目标

| 模块 | 代码路径 | 当前状态 | 目标覆盖率 | 优先级 |
|------|---------|---------|-----------|--------|
| 熔断器 | `agent/circuit_breaker.py` | 已有测试 | 90% | P0 |
| 限流器 | `agent/rate_limiter.py` | 已有测试 | 90% | P0 |
| 优雅降级 | `agent/graceful_degrade.py` | 已有混沌测试 | 85% | P0 |
| 容灾恢复 | `agent/disaster_recovery.py` | 已有混沌测试 | 85% | P0 |
| 记忆路由 | `agent/memory/router.py` | 已有测试 | 85% | P0 |
| 记忆过滤 | `agent/memory/filter.py` | 已有测试 | 90% | P0 |
| 指标收集 | `agent/monitoring/metrics.py` | 已有测试 | 85% | P1 |
| 链路追踪 | `agent/monitoring/tracing.py` | 已有测试 | 80% | P1 |
| 业务指标 | `agent/monitoring/business_metrics.py` | 待加强 | 85% | P1 |
| 健康评估 | `agent/health/assessor.py` | 已有测试 | 85% | P1 |
| 健康评分 | `agent/health/health_score.py` | 已有测试 | 85% | P1 |
| 任务规划 | `agent/task_planner/planner.py` | 已有测试 | 80% | P1 |
| 模型路由 | `agent/model_router/router.py` | 已有测试 | 85% | P1 |
| 工具调用 | `agent/tool_calling.py` | 已有测试 | 80% | P1 |
| 权限系统 | `agent/permission_system.py` | 已有测试 | 90% | P0 |
| 安全工具 | `agent/security_utils.py` | 待加强 | 95% | P0 |
| 日志系统 | `agent/log_system/` | 已有测试 | 80% | P2 |
| 子代理 | `agent/subagent/` | 已有测试 | 80% | P2 |
| 数字生命 | `agent/digital_life*.py` | 已有测试 | 75% | P2 |
| 全局平均 | - | - | **≥80%** | - |

### 2.3 边界值测试矩阵

每个功能模块必须覆盖以下边界场景：

| 边界类型 | 测试场景示例 | 对应模块 |
|---------|-------------|---------|
| **空值输入** | 空字符串、None、空列表、空字典 | 所有输入处理模块 |
| **超长输入** | 超长文本(>10KB)、超大数组(>10000项) | 记忆存储、文本处理 |
| **临界数值** | 0、负数、最大值、浮点数精度 | 限流计数、熔断器阈值 |
| **非法格式** | 畸形JSON、无效URL、错误编码 | API处理、配置加载 |
| **并发冲突** | 多线程同时读写、竞态条件 | 状态管理、缓存层 |
| **资源耗尽** | 内存不足、磁盘满、连接池耗尽 | 存储模块、网络层 |
| **超时场景** | 网络超时、API超时、数据库超时 | 所有外部依赖调用 |
| **幂等性** | 重复提交、重复回调、重复消息 | 写操作、支付类接口 |

### 2.4 异常路径验证要求

**禁止仅测试happy path**，每个模块必须验证：

1. **异常抛出规范性**：
   - 所有业务异常必须携带明确的错误码（如 `ERROR_CODE_001`）
   - 禁止静默返回 `None` 或空对象掩盖错误
   - 异常消息必须包含足够的上下文信息（trace_id、参数摘要）

2. **错误处理完整性**：
   - `try/except` 块必须捕获具体异常类型，禁止裸 `except:`
   - 资源清理逻辑（文件句柄、网络连接）必须在 `finally` 中执行
   - 异步代码中异常必须正确传播，不得被吞掉

3. **日志可追溯性**：
   - 所有异常路径必须输出结构化日志（JSON格式）
   - 日志必须包含：`trace_id`、`module_name`、`action`、`duration_ms`
   - 错误日志必须包含完整堆栈信息

### 2.5 测试执行计划

#### 2.5.1 新增/补全测试用例清单

| 序号 | 测试文件 | 覆盖模块 | 预计用例数 | 优先级 |
|------|---------|---------|-----------|--------|
| 1 | `tests/unit/test_circuit_breaker_boundary.py` | 熔断器边界测试 | 15 | P0 |
| 2 | `tests/unit/test_rate_limiter_boundary.py` | 限流器边界测试 | 12 | P0 |
| 3 | `tests/unit/test_memory_filter_sensitive.py` | 敏感信息过滤 | 20 | P0 |
| 4 | `tests/unit/test_security_utils_comprehensive.py` | 安全工具全面测试 | 25 | P0 |
| 5 | `tests/unit/test_permission_edge_cases.py` | 权限系统边界 | 18 | P0 |
| 6 | `tests/unit/test_business_metrics_tracking.py` | 业务指标埋点验证 | 15 | P1 |
| 7 | `tests/unit/test_graceful_degrade_scenarios.py` | 降级场景全覆盖 | 12 | P1 |
| 8 | `tests/unit/test_disaster_recovery_scenarios.py` | 容灾场景全覆盖 | 12 | P1 |
| 9 | `tests/unit/test_health_assessor_edge.py` | 健康评估边界 | 10 | P1 |
| 10 | `tests/unit/test_tool_calling_race_condition.py` | 工具调用竞态 | 8 | P1 |

#### 2.5.2 执行频率与时机

| 测试类型 | 执行时机 | 执行范围 | 耗时目标 |
|---------|---------|---------|---------|
| 快速单元测试 | 每次代码提交 | P0 + P1模块 | < 2分钟 |
| 完整单元测试 | PR创建/更新 | 全部单元测试 | < 10分钟 |
| 覆盖率检查 | PR合并前 | 核心模块 | < 5分钟 |
| 全量回归 | 每日凌晨 | 全部测试 | < 30分钟 |

---

## 三、第二层：业务层 - 业务指标关联验证

### 3.1 测试目标

验证功能是否达成**业务目标**，而非仅技术实现。将功能模块与可量化业务指标绑定，通过埋点监控关键行为，对比上线前后数据判断AI生成逻辑是否偏离业务意图。

### 3.2 核心业务指标定义

基于 `agent/monitoring/business_metrics.py` 中的指标定义，建立以下业务验证维度：

#### 3.2.1 用户交互指标

| 指标名称 | 类型 | 标签 | 业务价值 | 验证方法 |
|---------|------|------|---------|---------|
| `yunshu_interaction_total` | Counter | interaction_type, model, success | 衡量用户活跃度 | 对比上线前后日活变化 |
| `yunshu_interaction_duration_seconds` | Histogram | interaction_type, model | 衡量响应速度 | P95延迟≤3s |
| `yunshu_message_type_distribution` | Counter | message_type, intent | 了解用户意图分布 | 复杂任务占比≥40% |
| `yunshu_tool_call_total` | Counter | tool_name, tool_category, success | 工具使用率 | 工具调用成功率≥95% |

#### 3.2.2 任务完成指标

| 指标名称 | 类型 | 标签 | 业务价值 | 验证方法 |
|---------|------|------|---------|---------|
| `yunshu_task_completion_rate` | Gauge | task_type, priority | 任务完成质量 | 完成率≥85% |
| `yunshu_task_duration_seconds` | Histogram | task_type, complexity | 任务效率 | 简单任务≤30s |
| `yunshu_async_task_success_rate` | Gauge | - | 异步任务可靠性 | 成功率≥99% |

#### 3.2.3 知识库指标

| 指标名称 | 类型 | 标签 | 业务价值 | 验证方法 |
|---------|------|------|---------|---------|
| `yunshu_memory_search_hit_rate` | Gauge | search_type | 记忆检索效果 | 命中率≥60% |
| `yunshu_vector_query_latency` | Histogram | index_type | 向量查询性能 | P95≤500ms |
| `yunshu_memory_access_frequency` | Counter | memory_type | 记忆使用频率 | 周环比增长 |

#### 3.2.4 系统稳定性指标

| 指标名称 | 类型 | 标签 | 业务价值 | 验证方法 |
|---------|------|------|---------|---------|
| `yunshu_circuit_breaker_state` | Gauge | breaker_name | 熔断保护有效性 | 熔断触发后错误率下降 |
| `yunshu_rate_limit_triggers` | Counter | limiter_name, level | 限流保护效果 | 限流后系统可用性≥99.9% |
| `yunshu_degrade_triggers_total` | Counter | degrade_type, module | 降级触发频次 | 降级后用户无感知率≥90% |

### 3.3 埋点植入规范

#### 3.3.1 强制埋点位置

所有新功能代码必须在以下位置植入业务埋点：

```python
# 【埋点规范】关键用户交互点必须预留 trackEvent 调用
# 示例：
from agent.monitoring.business_metrics import get_business_metrics_collector

def process_user_chat(message, user_id):
    """处理用户聊天消息"""
    metrics = get_business_metrics_collector()
    
    # 功能调用开始 - 埋点
    metrics.increment_counter("yunshu_interaction_total", 
        labels={"interaction_type": "chat", "model": current_model, "success": "pending"})
    
    try:
        result = do_process(message)
        
        # 功能调用成功 - 埋点
        metrics.increment_counter("yunshu_interaction_total",
            labels={"interaction_type": "chat", "model": current_model, "success": "true"})
        metrics.record_latency("yunshu_interaction_duration_seconds",
            duration, labels={"interaction_type": "chat", "model": current_model})
        
        return result
    except Exception as e:
        # 功能调用失败 - 埋点
        metrics.increment_counter("yunshu_interaction_total",
            labels={"interaction_type": "chat", "model": current_model, "success": "false"})
        raise
```

#### 3.3.2 埋点验收标准

| 验收项 | 标准 | 检查方式 |
|--------|------|---------|
| 埋点覆盖率 | 核心功能100%覆盖 | 代码审查 + 静态扫描 |
| 指标命名规范 | 遵循 `yunshu_<模块>_<动作>` 格式 | 命名审核 |
| 标签完整性 | 必须包含成功/失败标签 | 代码审查 |
| 性能影响 | 埋点耗时≤1ms/次 | 性能测试验证 |
| 数据准确性 | 埋点计数与实际调用一致 | 对账测试 |

### 3.4 业务验证流程

#### 3.4.1 A/B测试框架

对于重大功能变更，必须通过A/B测试验证业务效果：

```
功能上线
    │
    ├─► 对照组（旧逻辑）───┐
    │                      ├─► 指标对比 ──► 业务效果评估
    └─► 实验组（新逻辑）───┘
```

#### 3.4.2 上线后数据回滚准则

| 指标变化 | 判定 | 动作 |
|---------|------|------|
| 核心指标改善≥5% | ✅ 有效 | 全量推广 |
| 核心指标波动±2% | ⚠️ 待观察 | 延长观察期 |
| 核心指标恶化≥5% | ❌ 无效 | 立即回滚，分析根因 |
| 错误率上升≥1% | ❌ 质量下降 | 立即回滚 |

---

## 四、第三层：系统层 - 集成影响分析

### 4.1 测试目标

验证模块是否引发**隐性系统风险**（如资源泄漏、数据不一致）。检查跨模块交互的正确性，监控非功能性影响，通过混沌工程验证容错能力。

### 4.2 跨模块交互测试

#### 4.2.1 关键集成链路

| 集成链路 | 涉及模块 | 测试重点 | 优先级 |
|---------|---------|---------|--------|
| 对话处理全链路 | 输入防护→模型路由→工具调用→记忆存储→响应生成 | 事务一致性、错误传播 | P0 |
| 熔断降级联动 | 熔断器→优雅降级→健康评估→告警通知 | 状态传递及时性、降级策略正确性 | P0 |
| 记忆读写链路 | 记忆路由→向量存储→敏感过滤→持久化 | 数据一致性、并发安全 | P0 |
| 限流防护链路 | 限流器→API网关→业务处理 | 限流准确性、分级策略 | P1 |
| 监控告警链路 | 指标收集→告警评估→通知发送→自愈触发 | 告警准确率、通知时效性 | P1 |
| 容灾恢复链路 | 故障检测→状态备份→自动恢复→数据修复 | RTO≤30s、RPO=0 | P1 |

#### 4.2.2 集成测试用例清单

| 序号 | 测试文件 | 测试场景 | 优先级 |
|------|---------|---------|--------|
| 1 | `tests/integration/test_circuit_breaker_degrade_flow.py` | 熔断触发→自动降级→恢复全流程 | P0 |
| 2 | `tests/integration/test_memory_consistency.py` | 并发读写记忆的数据一致性验证 | P0 |
| 3 | `tests/integration/test_rate_limit_api_gateway.py` | API网关限流全链路验证 | P0 |
| 4 | `tests/integration/test_disaster_recovery_e2e.py` | 模拟故障→自动恢复→数据验证 | P0 |
| 5 | `tests/integration/test_monitoring_alert_flow.py` | 指标异常→告警触发→通知发送 | P1 |
| 6 | `tests/integration/test_tool_call_memory_flow.py` | 工具调用→结果存储→记忆检索 | P1 |
| 7 | `tests/integration/test_multi_module_failure.py` | 多模块同时故障时的系统韧性 | P1 |

### 4.3 非功能性影响监控

#### 4.3.1 资源使用基线

| 资源类型 | 基线值 | 告警阈值 | 熔断阈值 | 测量方式 |
|---------|--------|---------|---------|---------|
| 内存占用 | ≤512MB | ≥768MB | ≥1GB | 进程RSS监控 |
| CPU使用率 | ≤30% | ≥60% | ≥90% | 1分钟平均 |
| 句柄数 | ≤1000 | ≥2000 | ≥5000 | 文件描述符计数 |
| 线程数 | ≤50 | ≥100 | ≥200 | 活跃线程计数 |
| Goroutine/协程 | ≤100 | ≥300 | ≥500 | 协程计数 |

#### 4.3.2 依赖服务调用监控

| 依赖类型 | 调用频率基线 | 告警阈值 | 验证方式 |
|---------|------------|---------|---------|
| 向量数据库查询 | ≤10次/对话 | ≥50次/对话 | 检查是否存在冗余查询 |
| 外部API调用 | ≤5次/对话 | ≥20次/对话 | 检查是否存在重复调用 |
| 文件IO操作 | ≤20次/对话 | ≥100次/对话 | 检查是否存在频繁读写 |
| 数据库读写 | ≤15次/对话 | ≥50次/对话 | 检查N+1查询问题 |

### 4.4 混沌工程验证

基于 `agent/monitoring/chaos_injector.py` 和 `tests/chaos/` 现有体系，扩展混沌测试场景。

#### 4.4.1 混沌测试场景矩阵

| 故障类型 | 注入点 | 验证目标 | 现有测试 | 需新增 |
|---------|-------|---------|---------|-------|
| **网络延迟** | HTTP客户端 | 超时重试是否生效 | ✅ 已有 | 扩展场景 |
| **网络错误** | 外部API | 熔断是否触发 | ✅ 已有 | 扩展场景 |
| **内存压力** | 内存分配 | OOM时的优雅降级 | ❌ 待建 | 新增 |
| **磁盘IO延迟** | 文件存储 | 数据持久化可靠性 | ❌ 待建 | 新增 |
| **CPU满载** | 计算密集型 | 服务降级策略 | ❌ 待建 | 新增 |
| **数据库连接耗尽** | 数据库层 | 连接池耗尽处理 | ❌ 待建 | 新增 |
| **消息丢失** | 事件总线 | 消息可靠性保障 | ❌ 待建 | 新增 |
| **时钟偏移** | 时间依赖 | 时间敏感逻辑正确性 | ❌ 待建 | 新增 |

#### 4.4.2 混沌测试执行计划

| 序号 | 测试文件 | 故障场景 | 优先级 |
|------|---------|---------|--------|
| 1 | `tests/chaos/test_network_latency_chaos.py` | 50ms/200ms/1s网络延迟注入 | P0 |
| 2 | `tests/chaos/test_memory_pressure_chaos.py` | 内存占用飙升场景 | P1 |
| 3 | `tests/chaos/test_disk_io_chaos.py` | 磁盘IO延迟注入 | P1 |
| 4 | `tests/chaos/test_connection_pool_chaos.py` | 数据库连接耗尽 | P1 |
| 5 | `tests/chaos/test_cpu_stress_chaos.py` | CPU满载场景 | P2 |
| 6 | `tests/chaos/test_message_loss_chaos.py` | 消息队列丢包模拟 | P2 |

### 4.5 依赖分析与架构守护

#### 4.5.1 模块依赖规则

| 规则 | 说明 | 检查方式 |
|------|------|---------|
| 单向依赖 | 上层模块依赖下层，禁止反向依赖 | 架构测试 |
| 循环依赖 | 禁止模块间循环依赖 | 静态分析 |
| 层级隔离 | 业务逻辑不直接依赖基础设施 | 架构测试 |
| 接口隔离 | 模块间通过接口通信，不直接依赖实现 | 代码审查 |

#### 4.5.2 架构测试用例

```python
# tests/architecture/test_module_dependencies.py
# 使用 importlib 分析模块依赖关系，验证架构规则
```

---

## 五、CI/CD 质量卡点配置

### 5.1 流水线阶段配置

基于 `.github/workflows/ci.yml` 现有配置，优化质量门禁：

```
代码提交
    │
    ▼
┌─────────────┐
│ 代码质量检查 │  ──► flake8 / black / mypy
└─────────────┘
    │
    ▼
┌─────────────┐
│ 安全扫描     │  ──► bandit / safety
└─────────────┘
    │
    ▼
┌─────────────┐
│ 单元测试     │  ──► pytest + 覆盖率≥80% ──► 不达标则阻断
└─────────────┘
    │
    ▼
┌─────────────┐
│ 集成测试     │  ──► 核心链路验证 ──► 不达标则阻断
└─────────────┘
    │
    ▼
┌─────────────┐
│ 混沌测试     │  ──► 关键容错场景（仅release分支）
└─────────────┘
    │
    ▼
┌─────────────┐
│ 业务指标验证 │  ──► 埋点完整性检查
└─────────────┘
    │
    ▼
  合并放行
```

### 5.2 覆盖率门禁升级

| 模块 | 当前阈值 | 目标阈值 | 升级时间节点 |
|------|---------|---------|-------------|
| 全局 | 40% | 70% | 3个月内 |
| 核心模块 | 60% | 80% | 2个月内 |
| 安全/权限 | 70% | 90% | 1个月内 |

### 5.3 质量报告模板

每次PR必须生成以下报告：

- ✅ 单元测试通过率
- ✅ 覆盖率变化趋势（+/- %）
- ✅ 安全漏洞数（高危/中危/低危）
- ✅ 新增代码覆盖率
- ✅ 集成测试结果
- ⚠️ 业务埋点覆盖检查
- ⚠️ 混沌测试摘要（仅重大变更）

---

## 六、闭环优化机制

### 6.1 缺陷逃逸率追踪

**目标：缺陷逃逸率≤5%**（传统开发通常10%-15%）

```
缺陷逃逸率 = 上线后发现的缺陷数 / 总缺陷数 × 100%
```

| 指标 | 目标 | 统计周期 |
|------|------|---------|
| 缺陷逃逸率 | ≤5% | 每月 |
| 严重缺陷逃逸 | 0 | 每月 |
| 回归缺陷率 | ≤2% | 每次发布 |

### 6.2 测试用例反馈闭环

```
线上故障发现
    │
    ▼
根因分析 ──► 是否测试遗漏？
    │
    ├─► 是 ──► 补充测试用例 ──► 回归测试
    │
    └─► 否 ──► 升级测试策略 ──► 更新测试计划
```

### 6.3 AI生成质量评估

每次AI生成代码后，自动评估以下维度：

| 评估维度 | 评分标准 | 权重 |
|---------|---------|------|
| 测试通过率 | 单元测试是否全部通过 | 30% |
| 覆盖率贡献 | 是否提升整体覆盖率 | 20% |
| 边界覆盖 | 是否覆盖关键边界条件 | 20% |
| 业务埋点 | 是否植入正确的业务指标 | 15% |
| 异常处理 | 异常路径是否完整 | 15% |

---

## 七、实施路线图

### 阶段一：基础层加固（第1-2周）

- [ ] 补齐P0模块边界测试用例
- [ ] 提升核心模块覆盖率至80%
- [ ] 完善异常路径测试
- [ ] CI覆盖率门禁升级至60%

### 阶段二：业务层建设（第3-4周）

- [ ] 核心功能100%埋点覆盖
- [ ] 建立业务指标基线
- [ ] A/B测试框架落地
- [ ] 上线后数据对比机制

### 阶段三：系统层深化（第5-6周）

- [ ] 扩展集成测试覆盖关键链路
- [ ] 新增混沌测试场景（6个以上）
- [ ] 架构守护测试落地
- [ ] 非功能性监控基线建立

### 阶段四：闭环优化（第7-8周）

- [ ] 缺陷逃逸率追踪机制
- [ ] 测试用例反馈闭环
- [ ] AI生成质量评估体系
- [ ] 覆盖率全局达标80%

---

## 八、附录

### 8.1 参考文件

- 测试框架配置：[pytest.ini](file:///c:/Users/Administrator/agent/pytest.ini)
- CI工作流：[ci.yml](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml)
- 覆盖率检查：[coverage_checker.py](file:///c:/Users/Administrator/agent/tests/coverage_checker.py)
- 业务指标定义：[business_metrics.py](file:///c:/Users/Administrator/agent/agent/monitoring/business_metrics.py)
- 混沌注入器：[chaos_injector.py](file:///c:/Users/Administrator/agent/agent/monitoring/chaos_injector.py)
- 熔断器：[circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py)

### 8.2 测试命令速查

```bash
# 运行单元测试
pytest tests/unit/ -v

# 运行带覆盖率的单元测试
pytest tests/unit/ --cov=agent --cov-report=html --cov-report=term-missing

# 运行集成测试
pytest tests/integration/ -v

# 运行混沌测试
pytest tests/chaos/ -v

# 运行特定标记的测试
pytest -m "p0" -v          # P0优先级
pytest -m "not slow" -v    # 排除慢速测试

# 覆盖率检查
python tests/coverage_checker.py
```

---

**文档版本**: v1.0  
**最后更新**: 2026-06-25  
**维护者**: 测试架构组
