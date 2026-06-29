# 云枢业务指标定义文档

## 文档概述

本文档详细定义了云枢智能代理系统的核心业务指标，用于衡量业务价值和系统健康状况。

**版本**: 1.0  
**创建时间**: 2026-06-24  
**模块路径**: `agent/monitoring/business_metrics.py`

---

## 指标分类

业务指标分为以下四大类：

1. **用户交互指标** - 衡量用户活跃度和系统使用频率
2. **任务完成指标** - 衡量任务执行成功率，识别失败模式
3. **知识库指标** - 衡量记忆检索效率，优化记忆策略
4. **扩展使用指标** - 衡量扩展获取频率，识别热门扩展

---

## 一、用户交互指标

### 1.1 yunshu_interaction_total

**指标名称**: `yunshu_interaction_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量用户活跃度和系统使用频率

**标签**:
- `interaction_type`: 交互类型（chat/tool_call/planning等）
- `model`: 使用的模型名称（gpt-4/gpt-3.5等）
- `success`: 是否成功（true/false）

**埋点位置**: `agent/orchestrator/orchestrator.py` - process() 方法

**使用示例**:
```python
from agent.monitoring.business_metrics import record_interaction

record_interaction(
    interaction_type="chat",
    model="gpt-4",
    success=True,
    duration=1.5,
)
```

---

### 1.2 yunshu_interaction_duration_seconds

**指标名称**: `yunshu_interaction_duration_seconds`  
**指标类型**: Histogram（直方图）  
**单位**: 秒  
**业务价值**: 衡量响应速度和用户体验

**标签**:
- `interaction_type`: 交互类型
- `model`: 使用的模型名称

**统计维度**:
- count: 样本数
- sum: 总和
- avg: 平均值
- min: 最小值
- max: 最大值
- p50: 50分位数
- p95: 95分位数
- p99: 99分位数

---

### 1.3 yunshu_message_type_distribution

**指标名称**: `yunshu_message_type_distribution`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 了解用户意图分布，优化对话策略

**标签**:
- `message_type`: 消息类型（simple_query/complex_task/follow_up等）
- `intent`: 意图（greeting/request/question等）

**埋点位置**: `agent/orchestrator/orchestrator.py` - 意图路由部分

---

### 1.4 yunshu_tool_call_total

**指标名称**: `yunshu_tool_call_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量工具使用频率，识别高频工具

**标签**:
- `tool_name`: 工具名称（read_file/web_search等）
- `tool_category`: 工具分类（file/web/system/extension/memory）
- `success`: 是否成功（true/false）

**埋点位置**: `agent/tools/__init__.py` - call() 方法

**使用示例**:
```python
from agent.monitoring.business_metrics import record_tool_call

record_tool_call(
    tool_name="read_file",
    tool_category="file",
    success=True,
    duration=0.3,
)
```

---

### 1.5 yunshu_tool_call_duration_seconds

**指标名称**: `yunshu_tool_call_duration_seconds`  
**指标类型**: Histogram（直方图）  
**单位**: 秒  
**业务价值**: 识别慢工具，优化工具性能

**标签**:
- `tool_name`: 工具名称
- `tool_category`: 工具分类

---

## 二、任务完成指标

### 2.1 yunshu_task_completion_rate

**指标名称**: `yunshu_task_completion_rate`  
**指标类型**: Gauge（仪表）  
**单位**: %  
**业务价值**: 衡量任务执行成功率，识别失败模式

**标签**:
- `task_type`: 任务类型（direct/planning/async等）
- `complexity`: 复杂度（simple/medium/complex）

**计算公式**: 成功任务数 / 总任务数 × 100

---

### 2.2 yunshu_task_total

**指标名称**: `yunshu_task_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 统计任务执行量，分析任务分布

**标签**:
- `task_type`: 任务类型
- `complexity`: 复杂度
- `status`: 状态（success/failed/pending）

**埋点位置**: `agent/orchestrator/orchestrator.py` - process() 方法

**使用示例**:
```python
from agent.monitoring.business_metrics import record_task

record_task(
    task_type="planning",
    complexity="complex",
    status="success",
    duration=10.0,
)
```

---

### 2.3 yunshu_task_duration_seconds

**指标名称**: `yunshu_task_duration_seconds`  
**指标类型**: Histogram（直方图）  
**单位**: 秒  
**业务价值**: 识别耗时任务，优化任务调度

**标签**:
- `task_type`: 任务类型
- `complexity`: 复杂度

---

### 2.4 yunshu_planning_task_success

**指标名称**: `yunshu_planning_task_success`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量规划引擎成功率

**标签**:
- `planner_type`: 规划器类型
- `steps_count`: 步骤数量

---

### 2.5 yunshu_async_task_success

**指标名称**: `yunshu_async_task_success`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量异步任务执行成功率

**标签**:
- `async_type`: 异步任务类型
- `queue_name`: 队列名称

---

## 三、知识库指标

### 3.1 yunshu_memory_search_hit_rate

**指标名称**: `yunshu_memory_search_hit_rate`  
**指标类型**: Gauge（仪表）  
**单位**: %  
**业务价值**: 衡量记忆检索效率，优化记忆策略

**标签**:
- `memory_type`: 记忆类型（long_term/short_term）
- `search_method`: 搜索方法（keyword/vector）

**计算公式**: 命中次数 / 搜索次数 × 100

---

### 3.2 yunshu_memory_search_total

**指标名称**: `yunshu_memory_search_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 统计记忆检索频率

**标签**:
- `memory_type`: 记忆类型
- `search_method`: 搜索方法
- `hit`: 是否命中（true/false）

**埋点位置**: `agent/memory/long_term_memory.py` - search() 方法

**使用示例**:
```python
from agent.monitoring.business_metrics import record_memory_search

record_memory_search(
    memory_type="long_term",
    search_method="keyword",
    hit=True,
)
```

---

### 3.3 yunshu_memory_access_count

**指标名称**: `yunshu_memory_access_count`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 识别高频访问记忆，优化记忆缓存

**标签**:
- `memory_key`: 记忆键
- `importance`: 重要性评分（1-5）

**埋点位置**: `agent/memory/long_term_memory.py` - get() 方法

---

### 3.4 yunshu_memory_storage_total

**指标名称**: `yunshu_memory_storage_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 统计记忆写入频率

**标签**:
- `memory_type`: 记忆类型
- `importance`: 重要性评分

**埋点位置**: 
- `agent/orchestrator/orchestrator.py` - 记忆保存部分
- `agent/memory/long_term_memory.py` - save() 方法

---

### 3.5 yunshu_vector_query_hit_rate

**指标名称**: `yunshu_vector_query_hit_rate`  
**指标类型**: Gauge（仪表）  
**单位**: %  
**业务价值**: 衡量向量检索效率

**标签**:
- `vector_store`: 向量存储类型
- `query_type`: 查询类型

---

### 3.6 yunshu_memory_compression_total

**指标名称**: `yunshu_memory_compression_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 统计记忆压缩频率，优化压缩策略

**标签**:
- `compression_type`: 压缩类型
- `success`: 是否成功

---

## 四、扩展使用指标

### 4.1 yunshu_extension_install_total

**指标名称**: `yunshu_extension_install_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量扩展获取频率，识别热门扩展

**标签**:
- `extension_type`: 扩展类型（skill/mcp/channel/plugin）
- `source`: 来源（builtin/github/npm/pip/url/local）
- `success`: 是否成功

**埋点位置**: `agent/tools/ext_tools.py` - ext_install 工具

**使用示例**:
```python
from agent.monitoring.business_metrics import record_extension_install

record_extension_install(
    extension_type="skill",
    source="github",
    success=True,
)
```

---

### 4.2 yunshu_extension_uninstall_total

**指标名称**: `yunshu_extension_uninstall_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 统计扩展移除频率，识别不常用扩展

**标签**:
- `extension_type`: 扩展类型
- `extension_id`: 扩展ID

---

### 4.3 yunshu_extension_enabled_count

**指标名称**: `yunshu_extension_enabled_count`  
**指标类型**: Gauge（仪表）  
**单位**: 个  
**业务价值**: 衡量扩展活跃度

**标签**:
- `extension_type`: 扩展类型

---

### 4.4 yunshu_mcp_connection_total

**指标名称**: `yunshu_mcp_connection_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量 MCP 服务使用频率

**标签**:
- `transport_type`: 传输类型（stdio/http）
- `service_id`: 服务ID
- `success`: 是否成功

**埋点位置**: `agent/tools/mcp_connector.py` - connect_stdio/connect_http 方法

**使用示例**:
```python
from agent.monitoring.business_metrics import record_mcp_connection

record_mcp_connection(
    transport_type="stdio",
    service_id="filesystem",
    success=True,
)
```

---

### 4.5 yunshu_mcp_active_connections

**指标名称**: `yunshu_mcp_active_connections`  
**指标类型**: Gauge（仪表）  
**单位**: 个  
**业务价值**: 衡量 MCP 服务活跃度

**标签**:
- `transport_type`: 传输类型

---

### 4.6 yunshu_skill_usage_total

**指标名称**: `yunshu_skill_usage_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量技能使用频率，识别热门技能

**标签**:
- `skill_id`: 技能ID
- `skill_category`: 技能分类
- `success`: 是否成功

---

### 4.7 yunshu_market_search_total

**指标名称**: `yunshu_market_search_total`  
**指标类型**: Counter（计数器）  
**单位**: 次  
**业务价值**: 衡量市场使用频率，识别热门搜索

**标签**:
- `query_category`: 查询分类
- `result_count`: 结果数量

---

## 五、API 端点

业务仪表盘提供以下 API 端点：

### 5.1 GET /api/business/health

**功能**: 业务指标健康检查

**响应示例**:
```json
{
  "status": "healthy",
  "health_score": 100,
  "checks": {
    "interaction_active": true,
    "task_success_rate": 95.0,
    "task_health": true,
    "memory_hit_rate": 75.0,
    "memory_health": true,
    "extension_active": true
  },
  "timestamp": 1719234567.89
}
```

---

### 5.2 GET /api/business/dashboard

**功能**: 业务仪表盘总览数据

**查询参数**:
- `time_range`: 时间范围（hour/today/week/month）
- `time_range_seconds`: 时间范围秒数（可选）

**响应示例**:
```json
{
  "generated_at": "2026-06-24T10:30:00Z",
  "time_range_seconds": 86400,
  "interaction": {...},
  "task": {...},
  "knowledge": {...},
  "extension": {...},
  "summary": {
    "total_interactions": 150,
    "total_tool_calls": 85,
    "task_success_rate": 92.5,
    "memory_hit_rate": 73.5,
    "active_extensions": 15
  }
}
```

---

### 5.3 GET /api/business/metrics/<metric_name>

**功能**: 单个指标详情

**路径参数**:
- `metric_name`: 指标名称

**响应示例**:
```json
{
  "definition": {
    "name": "yunshu_interaction_total",
    "description": "用户交互总次数",
    "metric_type": "counter",
    "labels": ["interaction_type", "model", "success"],
    "unit": "次",
    "category": "interaction",
    "business_value": "衡量用户活跃度和系统使用频率"
  },
  "data": {
    "interaction_type=chat,model=gpt-4,success=true": 120,
    "interaction_type=chat,model=gpt-4,success=false": 5
  }
}
```

---

### 5.4 GET /api/business/prometheus

**功能**: Prometheus 格式导出

**响应格式**: text/plain

**响应示例**:
```
# HELP yunshu_interaction_total 用户交互总次数
# TYPE yunshu_interaction_total counter
yunshu_interaction_total{interaction_type="chat",model="gpt-4",success="true"} 120
yunshu_interaction_total{interaction_type="chat",model="gpt-4",success="false"} 5
```

---

### 5.5 GET /api/business/definitions

**功能**: 指标定义列表

**响应示例**:
```json
{
  "definitions": [...],
  "total": 20,
  "categories": {
    "interaction": 5,
    "task": 5,
    "knowledge": 6,
    "extension": 4
  },
  "timestamp": 1719234567.89
}
```

---

## 六、使用指南

### 6.1 在业务代码中添加埋点

**步骤 1**: 导入业务指标模块
```python
from agent.monitoring.business_metrics import (
    record_interaction,
    record_tool_call,
    record_task,
    record_memory_search,
    record_extension_install,
)
```

**步骤 2**: 在关键业务流程中添加埋点
```python
# 工具调用埋点
try:
    result = tool_handler(**params)
    duration = time.time() - start_time
    
    record_tool_call(
        tool_name="read_file",
        tool_category="file",
        success=True,
        duration=duration,
    )
except Exception as e:
    record_tool_call(
        tool_name="read_file",
        tool_category="file",
        success=False,
    )
```

---

### 6.2 查询业务仪表盘数据

**方法 1**: 使用全局快捷函数
```python
from agent.monitoring.business_metrics import get_dashboard_data

dashboard = get_dashboard_data(time_range_seconds=3600)
print(json.dumps(dashboard, indent=2))
```

**方法 2**: 使用 API 端点
```bash
curl http://localhost:8080/api/business/dashboard?time_range=today
```

---

### 6.3 导出 Prometheus 格式

**方法 1**: 使用 Python API
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

collector = get_business_metrics_collector()
prometheus_text = collector.export_prometheus()
print(prometheus_text)
```

**方法 2**: 使用 API 端点
```bash
curl http://localhost:8080/api/business/prometheus
```

---

## 七、测试验证

运行测试脚本验证业务指标功能：

```bash
python tests/test_business_dashboard.py
```

测试内容包括：
- 业务指标收集器基本功能测试
- 业务指标记录测试（交互、工具调用、任务、记忆、扩展）
- 业务仪表盘数据查询测试
- Prometheus 导出测试
- API 端点测试

---

## 八、注意事项

### 8.1 性能考虑

- 业务指标收集器使用内存存储，适合短期数据
- 长期数据存储建议集成 Prometheus 或其他监控系统
- 指标埋点应避免在高频循环中调用，以免影响性能

### 8.2 数据保留

- 默认数据保留 30 天（可通过 `retention_days` 配置）
- Histogram 数据默认保留 7 天
- 建议定期清理历史数据，避免内存占用过大

### 8.3 标签使用

- 标签值应避免动态生成（如用户ID、时间戳等）
- 标签值应保持有限数量，避免标签爆炸
- 建议使用预定义的标签值集合

---

## 九、未来扩展

### 9.1 持久化存储

计划支持以下持久化存储：
- SQLite 本地存储
- Prometheus 远程写入
- Elasticsearch 日志存储

### 9.2 可视化仪表盘

计划集成以下可视化工具：
- Grafana 仪表盘模板
- 自定义 Web UI 仪表盘
- 移动端仪表盘应用

### 9.3 告警机制

计划支持以下告警机制：
- 任务成功率低于阈值告警
- 记忆命中率低于阈值告警
- 工具调用失败率高于阈值告警

---

**文档结束**