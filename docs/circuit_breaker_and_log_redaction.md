# 三级熔断器配置与日志脱敏技术文档

> **维护对象**：`agent/circuit_breaker.py`、`config.py`、`agent/config_validation.py`、`agent/observability/tool_trace.py`
> **设计原则**：不易（契约不可变）/ 变易（按需演进）/ 简易（最小充分解）

---

## 1. 概述

系统提供 **三级熔断器**（ThreeLevelCircuitBreaker）用于工具调用的故障隔离：

| 作用域 | 语义 | 默认冷却 | 触发条件 |
|--------|------|----------|----------|
| SESSION | 单会话单工具死循环隔离 | 60s | 连续失败 5 次 |
| USER | 单用户跨会话累积（仅高危工具） | 300s | 连续失败 20 次 |
| GLOBAL | 全局单工具过载保护 | 600s | 连续失败 100 次 |

**级联触发顺序**：`SESSION → USER（仅高危）→ GLOBAL`，任一触发即短路返回，不检查后续级别。

---

## 2. 架构设计

### 2.1 组合优于继承

`ThreeLevelCircuitBreaker` 内部持有 **3 个独立的 CircuitBreaker 注册表**（按 scope 隔离），而非继承单一 CircuitBreaker。

```
ThreeLevelCircuitBreaker
├── _session_breakers: dict[(session_id, tool_name), CircuitBreaker]
├── _user_breakers:    dict[(user_id, tool_name),    CircuitBreaker]
└── _global_breakers:  dict[tool_name,               CircuitBreaker]
```

**[不易]**：每级独立阈值与冷却策略，互不影响。
**[变易]**：新增熔断级别只需追加一个注册表 + 一个 `_get_xxx_breaker` 方法。
**[简易]**：双检锁模式（breaker 注册表读路径无锁，创建路径加锁）。

### 2.2 关键代码位置

| 组件 | 文件 | 行号 |
|------|------|------|
| CircuitScope 枚举 | [agent/circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py#L726-L734) | 726-734 |
| ThreeLevelBreakerConfig | [agent/circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py#L737-L756) | 737-756 |
| ThreeLevelCircuitBreaker | [agent/circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py#L759) | 759+ |
| allow_request（级联短路） | [agent/circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py#L826-L884) | 826-884 |
| _emit_trace_event | [agent/circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py#L1046-L1070) | 1046-1070 |

---

## 3. 配置项矩阵

每级熔断器包含 **4 个配置字段**，三级共 **12 项**，对应 12 项 ValidationRule。

| 字段 | 类型 | 范围 | SESSION 默认 | USER 默认 | GLOBAL 默认 | 说明 |
|------|------|------|--------------|-----------|-------------|------|
| failure_threshold | float | [0, 1] | 1.0 | 1.0 | 1.0 | 失败率阈值（1.0 = 100% 失败才触发） |
| min_requests | int | [1, 10000] | 5 | 20 | 100 | 触发熔断的最小请求数 |
| recovery_timeout | float | [0, 86400] | 60.0 | 300.0 | 600.0 | 冷却恢复时间（秒） |
| half_open_max_calls | int | [1, 100] | 1 | 2 | 3 | 半开状态最大探测请求数 |

> **注意**：`failure_threshold` 范围是 `[0, 1]`（含 1.0），早期版本是 `(0, 1)` 不含 1.0，已在 commit `4cca94ce` 修正。

---

## 4. 配置校验体系（三层防护）

### 4.1 第一层：Pydantic ConfigModel（严格校验）

**文件**：[config.py](file:///c:/Users/Administrator/agent/config.py#L219-L268)

```python
class CircuitBreakerScopeConfig(BaseModel):
    """单级熔断器配置（SESSION/USER/GLOBAL 通用）"""
    failure_threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    min_requests: int = Field(default=5, ge=1, le=10000)
    recovery_timeout: float = Field(default=60.0, ge=0.0, le=86400)
    half_open_max_calls: int = Field(default=1, ge=1, le=100)


class CircuitBreakerConfigSection(BaseModel):
    """三级熔断器配置"""
    session: CircuitBreakerScopeConfig = Field(default_factory=...)
    user: CircuitBreakerScopeConfig = Field(default_factory=...)
    global_: CircuitBreakerScopeConfig = Field(..., alias="global")  # 关键字避让

    class Config:
        allow_population_by_field_name = True  # 同时支持 global_ 和 global 两种键名


class ConfigModel(BaseModel):
    """完整配置模型"""
    # ... 原有 12 个字段 ...
    circuit_breaker: CircuitBreakerConfigSection = Field(default_factory=CircuitBreakerConfigSection)
```

**关键设计**：
- `global` 是 Python 关键字，用 `global_` 字段名 + `alias="global"` 兼容配置文件中的 `global` 键
- `allow_population_by_field_name = True` 允许同时用字段名和 alias 加载

### 4.2 第二层：ValidationRule 声明式校验（12 项）

**文件**：[agent/config_validation.py](file:///c:/Users/Administrator/agent/agent/config_validation.py#L106-L199)

```python
CIRCUIT_BREAKER_VALIDATION_RULES = [
    # SESSION 级（4 项）
    ValidationRule(path="session_failure_threshold",     validator=_range_validator(0, 1),       default=1.0,  required=True),
    ValidationRule(path="session_min_requests",          validator=_range_validator(1, 10000),  default=5,    required=True),
    ValidationRule(path="session_recovery_timeout",      validator=_range_validator(0, 86400),  default=60.0, required=True),
    ValidationRule(path="session_half_open_max_calls",   validator=_range_validator(1, 100),    default=1,    required=True),
    # USER 级（4 项）
    ValidationRule(path="user_failure_threshold",        validator=_range_validator(0, 1),       default=1.0,   required=True),
    ValidationRule(path="user_min_requests",             validator=_range_validator(1, 10000),  default=20,    required=True),
    ValidationRule(path="user_recovery_timeout",         validator=_range_validator(0, 86400),  default=300.0, required=True),
    ValidationRule(path="user_half_open_max_calls",      validator=_range_validator(1, 100),    default=2,     required=True),
    # GLOBAL 级（4 项）
    ValidationRule(path="global_failure_threshold",      validator=_range_validator(0, 1),       default=1.0,   required=True),
    ValidationRule(path="global_min_requests",           validator=_range_validator(1, 10000),  default=100,   required=True),
    ValidationRule(path="global_recovery_timeout",       validator=_range_validator(0, 86400),  default=600.0, required=True),
    ValidationRule(path="global_half_open_max_calls",    validator=_range_validator(1, 100),    default=3,     required=True),
]
```

### 4.3 第三层：_basic_validation required_sections

**文件**：[config.py](file:///c:/Users/Administrator/agent/config.py#L355-L364)

```python
required_sections = {
    'sensor': '...',
    'cognitive': '...',
    'memory': '...',
    'behavior': '...',
    'permission': '...',
    'security': '...',
    'circuit_breaker': '三级熔断器配置（SESSION/USER/GLOBAL 独立阈值与冷却策略）',
}
```

**[变易]** `validate_and_fix_config` 中的 `required_sections` 也包含 `'circuit_breaker': {}`，缺失时自动补全为空 dict，由 Pydantic 填充默认值。

---

## 5. 日志脱敏逻辑

### 5.1 hash_content 实现

**文件**：[agent/observability/tool_trace.py](file:///c:/Users/Administrator/agent/agent/observability/tool_trace.py#L475-L488)

```python
def hash_content(self, data: Any) -> str:
    """计算内容的 SHA256 哈希（前 16 位），不存原文"""
    try:
        content = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        content = str(data)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
```

**关键设计要点**：

1. **统一序列化**：先用 `json.dumps(..., sort_keys=True)` 序列化为 JSON 字符串，再 SHA256
2. **`sort_keys=True`**：确保字典键顺序不影响哈希结果（`{"a":1,"b":2}` 和 `{"b":2,"a":1}` 哈希相同）
3. **`default=str`**：遇到不可序列化对象（如 datetime、自定义类）降级为 `str()`
4. **截断 16 位**：16 位 hex = 64 bit，碰撞概率足够低，且日志可读
5. **不存原文**：仅记录哈希前缀，满足脱敏合规要求

### 5.2 字符串哈希的陷阱

**重要**：`json.dumps("sess-001")` 产出 `"sess-001"`（**含双引号**），因此哈希与直接 `hashlib.sha256("sess-001".encode())` **不同**。

```python
# 错误计算方式（与系统输出不匹配）
hashlib.sha256("sess-001".encode()).hexdigest()[:16]
# → 687a53027806debd

# 正确计算方式（与 hash_content 输出一致）
hashlib.sha256('"sess-001"'.encode()).hexdigest()[:16]
# → 93902c09d59c46cd  ✓
```

**[简易]**：这是设计行为，统一序列化确保类型一致性（字符串、字典、列表走相同路径），调试时需注意。

### 5.3 tool_trace 中的脱敏字段

**熔断事件**（[record_circuit_event](file:///c:/Users/Administrator/agent/agent/observability/tool_trace.py#L440-L471)）：

```json
{
  "module_name": "tool_trace",
  "action": "circuit_event",
  "scope": "session",
  "session_id_hash": "93902c09d59c46cd",
  "user_id_hash": "a1b2c3d4e5f67890",
  "tool_name": "web_search",
  "blocked": true
}
```

| 字段 | 是否脱敏 | 说明 |
|------|----------|------|
| scope | 否 | 作用域枚举值（session/user/global） |
| session_id_hash | **是** | session_id 的 SHA256[:16] |
| user_id_hash | **是** | user_id 的 SHA256[:16] |
| tool_name | 否 | 工具名称（业务标识，非敏感） |
| blocked | 否 | 是否阻断 |

**工具选择事件**（[record_tool_selection](file:///c:/Users/Administrator/agent/agent/observability/tool_trace.py#L415-L438)）：

```json
{
  "module_name": "tool_trace",
  "action": "tool_selection",
  "user_input_hash": "...",
  "categories": ["core", "file"],
  "tools_count": 9,
  "tools_preview": ["read_file", "write_file", ...]
}
```

---

## 6. tool_trace 事件接入

### 6.1 熔断事件写入流程

```
ThreeLevelCircuitBreaker.allow_request()
    │
    ├─ SESSION 熔断 → _emit_trace_event(CircuitScope.SESSION, ...)
    ├─ USER 熔断    → _emit_trace_event(CircuitScope.USER, ...)
    └─ GLOBAL 熔断 → _emit_trace_event(CircuitScope.GLOBAL, ...)
                          │
                          ▼
                  ToolTraceRecorder.record_circuit_event()
                          │
                          ▼
                  logger.info(json.dumps({...}))  ← 结构化日志
                          │
                          ▼
                  （不持久化到 SQLite，仅日志输出）
```

**[简易]**：熔断事件是轻量事件，用结构化日志记录即可；SQLite 只持久化工具执行 trace（ToolTraceRecord）。

### 6.2 ToolCallingService._execute_safe 的 trace 包裹

**文件**：[agent/tool_calling.py](file:///c:/Users/Administrator/agent/agent/tool_calling.py#L720-L758)

```
_execute_safe(func_name, args)
    │
    ├─ recorder.start_trace(func_name, args)  ← 开始 trace
    │
    ├─ result = self._execute_safe_core(func_name, args)  ← 核心调用
    │
    ├─ 成功 → recorder.finish_trace(ctx, result, None)
    │        （finish_trace 内部用 result["ok"] 推断 success）
    │
    └─ 异常 → recorder.finish_trace(ctx, {...}, e)
             （error_type 自动从 exception 提取）
```

**分层职责**（[不易] 契约）：
- `_execute_safe`：负责 trace 包裹 + 异常归类
- `_execute_safe_core`：负责 `tools.call` + ErrorRecovery 重试 + 结果后处理

测试可通过 mock `_execute_safe_core` 替换核心调用，无需注册真实工具。

---

## 7. 维护指南

### 7.1 修改默认值

**场景**：将 SESSION 级冷却时间从 60s 改为 90s。

需同步修改 **3 处**（保持一致）：

1. **Pydantic 模型**：[config.py](file:///c:/Users/Administrator/agent/config.py#L234) `CircuitBreakerConfigSection.session`
2. **ValidationRule 默认值**：[config_validation.py](file:///c:/Users/Administrator/agent/agent/config_validation.py#L124-L131) `session_recovery_timeout` 的 `default=60.0`
3. **运行时 dataclass**：[circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py#L745-L748) `ThreeLevelBreakerConfig.session` 的 `reset_timeout=60.0`

> **[不易] 警告**：三处必须同步，否则配置加载与运行时行为不一致。

### 7.2 添加新的熔断级别

**场景**：新增 TENANT（租户级）熔断。

1. **CircuitScope 枚举**追加 `TENANT = "tenant"`
2. **ThreeLevelBreakerConfig** 追加 `tenant: CircuitBreakerConfig` 字段
3. **ThreeLevelCircuitBreaker** 追加 `_tenant_breakers` 注册表 + `_get_tenant_breaker` 方法
4. **allow_request** 在合适位置插入 TENANT 检查（建议在 USER 和 GLOBAL 之间）
5. **Pydantic 模型**追加 `tenant: CircuitBreakerScopeConfig` 字段
6. **ValidationRule** 追加 4 项 tenant_xxx 规则
7. **测试**：在 `tests/unit/test_circuit_breaker_three_level.py` 追加测试用例

### 7.3 调试熔断事件

**查看熔断日志**：

```bash
# 过滤熔断事件日志
grep '"action": "circuit_event"' logs/*.log

# 查看 SESSION 级熔断
grep '"scope": "session"' logs/*.log
```

**验证哈希计算**：

```python
import json, hashlib
data = "sess-001"
content = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
# content == '"sess-001"'（注意双引号）
h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
print(h)  # 93902c09d59c46cd
```

**运行测试验证**：

```bash
# 三级熔断器专项测试（29 用例）
python -m pytest tests/unit/test_circuit_breaker_three_level.py -v

# 边界测试
python -m pytest tests/unit/test_circuit_breaker_boundary.py -v

# tool_trace 测试（含熔断事件接入）
python -m pytest tests/unit/test_tool_trace.py -v
```

---

## 8. 相关提交

| Commit | 内容 |
|--------|------|
| `4cca94ce` | ThreeLevelCircuitBreaker 实现 + CIRCUIT_BREAKER_VALIDATION_RULES 12 项 + 边界测试更新 |
| （本次） | ConfigModel 纳入 circuit_breaker + _basic_validation 更新 + _execute_safe trace 包裹 + test_tool 注册 + tool_selection 日志 |

---

## 9. 测试覆盖

| 测试文件 | 用例数 | 覆盖范围 |
|----------|--------|----------|
| [tests/unit/test_circuit_breaker_three_level.py](file:///c:/Users/Administrator/agent/tests/unit/test_circuit_breaker_three_level.py) | 29 | 级联触发、配置校验、trace 事件 |
| [tests/unit/test_circuit_breaker_boundary.py](file:///c:/Users/Administrator/agent/tests/unit/test_circuit_breaker_boundary.py) | 多项 | failure_threshold 边界（含 1.0） |
| [tests/unit/test_tool_trace.py](file:///c:/Users/Administrator/agent/tests/unit/test_tool_trace.py) | 58 | hash_content、采样、降级、trace 生命周期、集成测试 |

---

**文档版本**：1.0  |  **最后更新**：2026-07-16  |  **维护者**：云枢工程团队
