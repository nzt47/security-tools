# 部署检查清单：三级熔断器功能上线

> **版本**: circuit_breaker v2.0（三级熔断 + ConfigModel 集成）
> **上线日期**: 2026-07-17
> **相关 Commit**: `c19f0cf7`（功能）/ `78d0e030`（文档）/ `c1e5a395`（hash 修正）
> **技术文档**: [circuit_breaker_and_log_redaction.md](circuit_breaker_and_log_redaction.md)
> **发布说明**: [RELEASE_NOTES_circuit_breaker.md](RELEASE_NOTES_circuit_breaker.md)

---

## 一、配置项检查（12 项 + Pydantic 模型）

### 1.1 Pydantic 模型完整性

- [ ] `config.py` 中存在 `CircuitBreakerScopeConfig` 类（4 字段）
- [ ] `config.py` 中存在 `CircuitBreakerConfigSection` 类（session/user/global_ 三字段）
- [ ] `ConfigModel` 包含 `circuit_breaker` 字段
- [ ] `_basic_validation` 的 `required_sections` 包含 `'circuit_breaker'`
- [ ] `validate_and_fix_config` 的 `required_sections` 包含 `'circuit_breaker': {}`

### 1.2 SESSION 级配置（单会话单工具冷却 60s）

| 配置项 | 默认值 | 校验范围 | 检查 |
|--------|--------|----------|------|
| `session.failure_threshold` | 1.0 | [0, 1] | [ ] |
| `session.min_requests` | 5 | [1, 10000] | [ ] |
| `session.recovery_timeout` | 60.0 | [0, 86400] | [ ] |
| `session.half_open_max_calls` | 1 | [1, 100] | [ ] |

### 1.3 USER 级配置（单用户高危工具冷却 300s）

| 配置项 | 默认值 | 校验范围 | 检查 |
|--------|--------|----------|------|
| `user.failure_threshold` | 1.0 | [0, 1] | [ ] |
| `user.min_requests` | 20 | [1, 10000] | [ ] |
| `user.recovery_timeout` | 300.0 | [0, 86400] | [ ] |
| `user.half_open_max_calls` | 2 | [1, 100] | [ ] |

### 1.4 GLOBAL 级配置（全局单工具冷却 600s）

| 配置项 | 默认值 | 校验范围 | 检查 |
|--------|--------|----------|------|
| `global.failure_threshold` | 1.0 | [0, 1] | [ ] |
| `global.min_requests` | 100 | [1, 10000] | [ ] |
| `global.recovery_timeout` | 600.0 | [0, 86400] | [ ] |
| `global.half_open_max_calls` | 3 | [1, 100] | [ ] |

### 1.5 Pydantic alias 兼容性

- [ ] `global_` 字段支持 `alias="global"` 加载 config dict 中的 `"global"` 键
- [ ] `allow_population_by_field_name = True` 已配置（允许字段名直接传参）

### 1.6 校验规则验证（14 项体系）

- [ ] `config_validation.py` 中 `CIRCUIT_BREAKER_VALIDATION_RULES` 包含 12 项规则
- [ ] `SEARCH_INSTANCE_VALIDATION_RULES` 包含 2 项规则
- [ ] 合法配置通过校验（0 errors）
- [ ] 非法配置被拒绝（如 `session_failure_threshold=1.5`）

---

## 二、日志脱敏验证

### 2.1 hash_content 脱敏函数

- [ ] `agent/observability/tool_trace.py` 中 `hash_content` 方法实现 SHA256[:16] 脱敏
- [ ] 脱敏流程：`json.dumps(ensure_ascii=False, default=str, sort_keys=True)` → `SHA256` → `[:16]`
- [ ] 异常兜底：序列化失败时 fallback 到 `str(data)`

### 2.2 脱敏字段清单（不存原文）

| 字段 | 脱敏方式 | 用途 | 检查 |
|------|----------|------|------|
| `input_hash` | hash_content(input_data) | 工具输入参数哈希 | [ ] |
| `output_hash` | hash_content(output_data) | 工具输出结果哈希 | [ ] |
| `user_input_hash` | hash_content(user_input) | 用户原始输入哈希 | [ ] |
| `session_id_hash` | hash_content(session_id) | 会话 ID 哈希 | [ ] |
| `user_id_hash` | hash_content(user_id) | 用户 ID 哈希 | [ ] |

### 2.3 脱敏验证用例

- [ ] 启动系统后触发一次工具调用，检查 `tool_trace` 日志中无原文输入/输出
- [ ] 验证 `input_hash` 长度为 16 字符（hex）
- [ ] 验证相同输入产生相同 hash（幂等性）
- [ ] 验证不同输入产生不同 hash（抗碰撞）

### 2.4 危险命令检测（不脱敏，直接标记）

- [ ] `_is_dangerous` 方法检测 critical 模式（如 `rm -rf`, `sudo` 等）
- [ ] 匹配到危险命令时 `is_dangerous=True`，但**不**存原文（仅存 hash）

---

## 三、熔断器状态验证

### 3.1 三级熔断器注册表

- [ ] `agent/circuit_breaker.py` 中存在 `CircuitScope` 枚举（SESSION/USER/GLOBAL）
- [ ] `ThreeLevelCircuitBreaker` 类管理 3 个独立 `CircuitBreaker` 注册表
- [ ] 级联短路触发顺序：SESSION → USER（仅高危）→ GLOBAL

### 3.2 三状态转换

- [ ] CLOSED → OPEN：错误率达阈值 + 最小请求数达标
- [ ] OPEN → HALF_OPEN：冷却期 `recovery_timeout` 到期
- [ ] HALF_OPEN → CLOSED：探测成功数达 `half_open_max_calls`
- [ ] HALF_OPEN → OPEN：探测失败立即重新熔断

### 3.3 tool_trace 事件接入

- [ ] `record_circuit_event` 方法记录熔断状态转换事件
- [ ] `record_tool_selection` 方法记录工具选择决策
- [ ] `_execute_safe` 包裹 `start_trace`/`finish_trace`（异常时传 `exception=e`）
- [ ] tool_trace 为结构化日志，**不**持久化到 SQLite

---

## 四、测试覆盖验证

### 4.1 全量测试结果

- [ ] 全量测试通过率：8243 passed / 14 failed（预存在）/ 0 回归
- [ ] 14 项失败用例与本次修改无关（已分析确认）
- [ ] 测试报告归档：`docs/reports/full_test_report_20260716.md`

### 4.2 校验体系验证

- [ ] 14 项校验体系验证：29/29 全部通过
- [ ] 校验报告归档：`docs/reports/circuit_breaker_validation_report_20260716.md`

### 4.3 关键测试用例

- [ ] `test_full_tool_call_produces_one_trace`：工具调用产生 trace 记录
- [ ] `test_failed_tool_call_produces_trace`：失败工具调用产生 trace 记录
- [ ] 三级熔断器状态转换测试通过
- [ ] 采样策略验证：高频 10% / 低频 100% / 危险操作 100%

---

## 五、回滚方案

### 5.1 Git 回滚（保留历史）

```bash
# 回退功能 commit（保留 Release Notes 和文档）
git revert c19f0cf7
```

### 5.2 Git 回滚（彻底删除）

```bash
# 回退到功能 commit 之前（会删除所有相关变更）
git reset --hard c19f0cf7^
```

### 5.3 配置降级（最小回滚）

- [ ] 移除 `config.py` 中 `circuit_breaker` 字段，系统退回单级熔断器模式
- [ ] 保留 `agent/circuit_breaker.py` 的 `ThreeLevelCircuitBreaker`（向下兼容）
- [ ] 保留 `config_validation.py` 的 12 项 `CIRCUIT_BREAKER_VALIDATION_RULES`（向下兼容）

### 5.4 回滚验证

- [ ] 回滚后执行全量测试，确认无新增失败
- [ ] 回滚后验证工具调用正常（无熔断器误触发）
- [ ] 回滚后验证日志脱敏仍生效（hash_content 不依赖 circuit_breaker）

---

## 六、上线后监控

### 6.1 熔断器指标

- [ ] 监控 `circuit_state_changed` 事件频率（正常应低）
- [ ] 监控 `circuit_blocked` 事件（熔断触发时）
- [ ] 关注 GLOBAL 级熔断（影响全局，需立即处理）

### 6.2 tool_trace 指标

- [ ] 监控 tool_trace 日志量（采样策略生效）
- [ ] 验证无原文泄露（所有字段均为 hash）
- [ ] 关注 `is_dangerous=True` 的事件（安全审计）

### 6.3 配置漂移检测

- [ ] 定期检查 `circuit_breaker` 配置项是否被修改
- [ ] Pydantic 严格校验对非法配置拒绝（超范围值抛 ValidationError）

---

## 七、签字确认

| 角色 | 确认项 | 签字 | 日期 |
|------|--------|------|------|
| 开发 | 配置项 12 项 + Pydantic 模型完整 | | |
| 测试 | 全量测试 0 回归 + 校验 29/29 通过 | | |
| 安全 | 日志脱敏 5 字段无原文泄露 | | |
| 运维 | 回滚方案验证通过 + 监控指标就绪 | | |

---

**检查清单版本**: v1.0
**生成时间**: 2026-07-17
**维护者**: circuit_breaker 模块负责人
