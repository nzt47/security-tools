# 云枢 API 契约文档

> **版本**：v1.0.0  
> **生成时间**：2026-06-26  
> **契约规范**：Pact Specification v2.0.0  
> **维护方式**：本地 JSON 文件存储（`tests/contract/contracts/`）

---

## 文档导航

本文档以交互式方式呈现 3 个核心 API 的契约定义，包含：
- 请求字段（类型/必填/枚举/约束）
- 响应字段（类型/必填/状态码/错误码）
- 请求/响应示例
- 字段说明与业务约束

| API | 方法 | 路径 | 描述 |
| --- | --- | --- | --- |
| [对话接口](#1-对话接口-apichat) | POST | `/api/chat` | 处理用户消息并返回响应 |
| [健康检查](#2-健康检查-apihealth) | GET | `/api/health` | 返回身体状态读数数组 |
| [仪表盘](#3-仪表盘-apidashboard) | GET | `/api/dashboard/*` | 质量监控与追踪数据 |

---

## 1. 对话接口 `/api/chat`

**Consumer**：`yunshu_frontend`  
**Provider**：`yunshu_backend`  
**契约文件**：[`tests/contract/contracts/chat_api_contract.json`](file:///c:/Users/Administrator/agent/tests/contract/contracts/chat_api_contract.json)

### 交互 1.1：正常对话请求

#### 请求

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `message` | string | ✅ | min_length=1, max_length=10000 | 用户输入消息 |
| `voice` | boolean | ❌ | — | 是否启用语音合成 |
| `session` | string | ❌ | — | 会话 ID（可选） |

**请求示例**：

```json
{
  "message": "你好，云枢",
  "voice": false
}
```

#### 响应（200 OK）

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `response` | string | ✅ | — | 对话响应文本 |
| `mode` | string | ✅ | enum: `normal`, `focus`, `creative`, `study`, `rest` | 行为模式 |
| `mode_label` | string | ✅ | — | 模式标签（中文） |
| `logs` | array[string] | ✅ | — | 处理日志数组 |
| `timing` | object | ✅ | — | 耗时统计 |
| `timing.total` | number | ✅ | — | 总耗时(ms) |
| `timing.safety_check` | number | ✅ | — | 安全检查耗时(ms) |
| `timing.chat_processing` | number | ✅ | — | 对话处理耗时(ms) |
| `health` | array[object] | ❌ | — | 身体状态读数 |
| `health[].sensor_name` | string | ❌ | — | 传感器名称 |
| `health[].severity` | string | ❌ | enum: `normal`, `warning`, `critical` | 严重程度 |
| `llm_state` | object | ❌ | — | LLM 配置状态 |
| `llm_state.configured` | boolean | ❌ | — | 是否已配置 |
| `llm_state.provider` | string | ❌ | — | 提供商 |
| `llm_state.api_key_set` | boolean | ❌ | — | API Key 是否已设置 |

**响应示例**：

```json
{
  "response": "您好，我是云枢。",
  "mode": "normal",
  "mode_label": "正常模式",
  "logs": ["[START] 收到对话请求"],
  "timing": {
    "total": 123.45,
    "safety_check": 1.2,
    "chat_processing": 100.0
  },
  "health": [],
  "llm_state": {
    "configured": true,
    "provider": "openai",
    "api_key_set": true
  }
}
```

### 交互 1.2：空消息请求

#### 请求

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `message` | string | ✅ | 空字符串（触发 400） |

#### 响应（400 Bad Request）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `error` | string | ✅ | 错误信息 |

**响应示例**：

```json
{
  "error": "消息不能为空"
}
```

### 交互 1.3：安全拦截请求

#### 请求

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `message` | string | ✅ | min_length=1 | 含危险操作的消息 |

#### 响应（403 Forbidden）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `response` | string | ✅ | 拦截提示文本 |
| `blocked` | boolean | ✅ | 是否阻断（固定为 true） |
| `mode` | string | ✅ | 行为模式（enum 同交互 1.1） |
| `safety` | object | ✅ | 安全检查结果 |
| `safety.level` | string | ✅ | enum: `safe`, `warning`, `critical` |
| `safety.safe` | boolean | ✅ | 是否安全 |

**响应示例**：

```json
{
  "response": "⚠️ 安全警告：检测到危险操作！",
  "blocked": true,
  "mode": "normal",
  "safety": {
    "level": "critical",
    "safe": false
  }
}
```

### 错误码表

| 状态码 | 错误码 | 场景 | 处置建议 |
| --- | --- | --- | --- |
| 400 | `MESSAGE_EMPTY` | message 为空字符串 | 前端校验输入非空 |
| 403 | `SAFETY_BLOCKED` | 安全守护拦截 | 提示用户操作被拦截，需确认风险 |
| 500 | `CHAT_INTERNAL_ERROR` | 对话处理异常 | 查看 `logs` 中的 `[ERROR]` 与 `[STACK TRACE]` |

---

## 2. 健康检查 `/api/health`

**Consumer**：`yunshu_frontend`  
**Provider**：`yunshu_backend`  
**契约文件**：[`tests/contract/contracts/health_api_contract.json`](file:///c:/Users/Administrator/agent/tests/contract/contracts/health_api_contract.json)

### 交互 2.1：获取身体状态读数

#### 请求

- **方法**：GET
- **路径**：`/api/health`
- **查询参数**：无
- **请求体**：无

#### 响应（200 OK）

响应为**根级数组**，每个元素为一个身体状态读数对象：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `sensor_name` | string | ✅ | — | 传感器名称 |
| `description` | string | ❌ | — | 读数描述 |
| `severity` | string | ✅ | enum: `normal`, `warning`, `critical` | 严重程度 |
| `value` | number | ❌ | — | 读数值 |
| `unit` | string | ❌ | — | 单位 |
| `timestamp` | string | ❌ | — | 时间戳（ISO 8601） |

**响应示例**：

```json
[
  {
    "sensor_name": "heart_rate",
    "description": "心率",
    "severity": "normal",
    "value": 72,
    "unit": "bpm",
    "timestamp": "2026-06-26T10:00:00"
  },
  {
    "sensor_name": "blood_pressure",
    "description": "血压",
    "severity": "warning",
    "value": 140,
    "unit": "mmHg",
    "timestamp": "2026-06-26T10:00:00"
  }
]
```

### 错误码表

| 状态码 | 错误码 | 场景 | 处置建议 |
| --- | --- | --- | --- |
| 200 | — | 正常返回读数数组 | — |
| 500 | `HEALTH_COLLECT_ERROR` | 传感器数据采集异常 | 检查 `agent/health/assessor.py` |

---

## 3. 仪表盘 `/api/dashboard`

**Consumer**：`yunshu_frontend`  
**Provider**：`yunshu_backend`  
**契约文件**：[`tests/contract/contracts/dashboard_api_contract.json`](file:///c:/Users/Administrator/agent/tests/contract/contracts/dashboard_api_contract.json)

### 交互 3.1：获取质量监控数据

#### 请求

- **方法**：GET
- **路径**：`/api/dashboard/quality`
- **查询参数**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `time_range` | string | `today` | 时间范围：`today` / `week` / `month` / 自定义 |

#### 响应（200 OK）

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `total_requests` | integer | ✅ | minimum=0 | 总请求数 |
| `success_count` | integer | ✅ | minimum=0 | 成功数 |
| `error_count` | integer | ✅ | minimum=0 | 错误数 |
| `success_rate` | number | ✅ | minimum=0, maximum=100 | 成功率(%) |
| `avg_response_time` | number | ❌ | minimum=0 | 平均响应时间(ms) |
| `p95_response_time` | number | ❌ | minimum=0 | P95 响应时间(ms) |
| `p99_response_time` | number | ❌ | minimum=0 | P99 响应时间(ms) |
| `time_range` | object | ✅ | — | 时间范围 |
| `time_range.start` | number | ✅ | — | 开始时间戳 |
| `time_range.end` | number | ✅ | — | 结束时间戳 |
| `error_breakdown` | object | ❌ | — | 错误分类统计 |

**响应示例**：

```json
{
  "total_requests": 1000,
  "success_count": 950,
  "error_count": 50,
  "success_rate": 95.0,
  "avg_response_time": 123.45,
  "p95_response_time": 300.0,
  "p99_response_time": 500.0,
  "time_range": {
    "start": 1719360000.0,
    "end": 1719446400.0
  },
  "error_breakdown": {
    "timeout": 30,
    "validation": 20
  }
}
```

### 交互 3.2：获取追踪数据列表

#### 请求

- **方法**：GET
- **路径**：`/api/dashboard/traces`
- **查询参数**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `limit` | integer | 20 | 返回条数上限 |

#### 响应（200 OK）

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `total` | integer | ✅ | minimum=0 | 追踪总数 |
| `traces` | array[object] | ✅ | — | 追踪记录数组 |
| `traces[].trace_id` | string | ✅ | — | 追踪 ID |
| `traces[].service` | string | ✅ | — | 服务名 |
| `traces[].operation` | string | ✅ | — | 操作名 |
| `traces[].duration_ms` | number | ✅ | minimum=0 | 耗时(ms) |
| `traces[].status` | string | ✅ | enum: `success`, `error` | 状态 |

**响应示例**：

```json
{
  "total": 1,
  "traces": [
    {
      "trace_id": "abc123",
      "service": "chat",
      "operation": "api.chat",
      "duration_ms": 123.45,
      "status": "success"
    }
  ]
}
```

### 错误码表

| 状态码 | 错误码 | 场景 | 处置建议 |
| --- | --- | --- | --- |
| 200 | — | 正常返回数据 | — |
| 400 | `INVALID_TIME_RANGE` | time_range 参数非法 | 使用合法枚举值 |
| 500 | `DASHBOARD_QUERY_ERROR` | 数据查询异常 | 检查 `agent/monitoring/` 模块 |

---

## 契约验证流程

### 本地验证

```bash
# 1. 启动云枢服务
python app_server.py

# 2. 运行 Provider 验证
python tests/contract/verify_provider.py --base-url http://localhost:5678
```

### CI 验证

契约验证已集成到 `.github/workflows/observability-ci.yml` 的 `Contract Test` job：
1. 启动 Flask 服务
2. 运行 `verify_provider.py`
3. 上传验证报告 artifact
4. 验证失败则阻断合并

### 验证报告

验证结果输出到 `docs/observability/contract_verification/<contract_name>_verification.json`，包含：
- 每个交互的通过/失败状态
- 失败原因与错误码
- 实际响应状态码
- 验证耗时

---

## 契约变更流程

1. **Consumer 提议**：前端在 `contract_definitions.py` 中修改契约定义
2. **本地验证**：运行 `pytest tests/contract/test_contracts.py` 确保契约定义合法
3. **Provider 验证**：启动服务运行 `verify_provider.py` 确保 Provider 满足新契约
4. **PR 评审**：契约变更需在 PR 中明确说明影响范围
5. **文档更新**：同步更新本文档

---

## 字段类型说明

| 类型 | 说明 |
| --- | --- |
| `string` | 字符串 |
| `integer` | 整数（不接受布尔值） |
| `number` | 数值（整数或浮点数，不接受布尔值） |
| `boolean` | 布尔值 |
| `array` | 数组，元素类型由 `items` 定义 |
| `object` | 对象，属性由 `properties` 定义 |
| `null` | 空值 |

---

## 维护信息

- **契约源文件**：[`tests/contract/contract_definitions.py`](file:///c:/Users/Administrator/agent/tests/contract/contract_definitions.py)
- **框架代码**：[`tests/contract/contract_framework.py`](file:///c:/Users/Administrator/agent/tests/contract/contract_framework.py)
- **验证脚本**：[`tests/contract/verify_provider.py`](file:///c:/Users/Administrator/agent/tests/contract/verify_provider.py)
- **Pact JSON 文件**：[`tests/contract/contracts/`](file:///c:/Users/Administrator/agent/tests/contract/contracts)

---

_本文档由契约定义自动同步，如有疑问请联系平台研发组。_
