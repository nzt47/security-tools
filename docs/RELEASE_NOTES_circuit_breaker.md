# Release Notes: 三级熔断器功能上线

> **发布版本**: circuit_breaker v2.0（三级熔断 + ConfigModel 集成）
> **发布日期**: 2026-07-16
> **涉及分支**: `master`（cherry-pick 自 `feature/tlm-step3-vectorstore-sqlite-vec`）
> **相关 Commit**:
> - `dac5f89e` feat(circuit_breaker): 熔断器增强 + 配置校验扩展（feature 分支）
> - `c19f0cf7` feat(circuit_breaker): ConfigModel 纳入 circuit_breaker + tool_trace 测试修复 + 技术文档（master，cherry-pick 自 feature 分支 `282d4db4`）

---

## 一、变更点总览

### 1.1 三级熔断器架构（CircuitScope: SESSION/USER/GLOBAL）

**文件**: [agent/circuit_breaker.py](file:///c:/Users/Administrator/agent/agent/circuit_breaker.py)

- 新增 `CircuitScope` 枚举（SESSION/USER/GLOBAL）
- 新增 `ThreeLevelBreakerConfig` dataclass（3 级独立配置）
- 新增 `ThreeLevelCircuitBreaker` 类：
  - **级联短路触发**：SESSION → USER（仅高危）→ GLOBAL
  - **双检锁模式**：breaker 注册表读路径无锁，创建路径加锁
  - **3 个独立注册表**：`_session_breakers` / `_user_breakers` / `_global_breakers`
- 修复 `CircuitBreakerError.message` 属性缺失 bug
- 放宽 `failure_threshold` 校验范围：`(0,1)` → `[0,1]`（含 1.0）

### 1.2 配置校验体系（14 项 ValidationRule）

**文件**: [agent/config_validation.py](file:///c:/Users/Administrator/agent/agent/config_validation.py)

- 新增 `CIRCUIT_BREAKER_VALIDATION_RULES`（12 项规则）：
  - SESSION 级 4 项：failure_threshold / min_requests / recovery_timeout / half_open_max_calls
  - USER 级 4 项：同上
  - GLOBAL 级 4 项：同上
- 与原有 `SEARCH_INSTANCE_VALIDATION_RULES`（2 项）合计 **14 项校验体系**

### 1.3 ConfigModel 集成（Pydantic 严格校验）

**文件**: [config.py](file:///c:/Users/Administrator/agent/config.py)

- 新增 `CircuitBreakerScopeConfig`（单级配置，4 字段 + 范围约束）
- 新增 `CircuitBreakerConfigSection`（三级配置，session/user/global_ + alias 兼容）
- `ConfigModel` 添加 `circuit_breaker` 字段
- `_basic_validation` required_sections 添加 `circuit_breaker`
- `validate_and_fix_config` 缺失时自动补全

### 1.4 tool_trace 集成（结构化日志 + 脱敏）

**文件**: [agent/observability/tool_trace.py](file:///c:/Users/Administrator/agent/agent/observability/tool_trace.py)

- `record_circuit_event`：熔断事件结构化日志（scope/session_id_hash/user_id_hash/tool_name/blocked）
- `hash_content`：SHA256[:16] 脱敏（json.dumps 统一序列化 + sort_keys）
- `record_tool_selection`：工具选择决策日志

### 1.5 ToolCallingService 重构（trace 包裹）

**文件**: [agent/tool_calling.py](file:///c:/Users/Administrator/agent/agent/tool_calling.py)

- `_execute_safe` 重构：抽离 `_execute_safe_core`，添加 tool_trace 包裹
- 分层职责：`_execute_safe`（trace + 异常归类）/ `_execute_safe_core`（tools.call + 重试）
- 支持测试 mock `_execute_safe_core` 替换核心调用

### 1.6 ToolRouter 增强（工具选择日志）

**文件**: [agent/tool_router.py](file:///c:/Users/Administrator/agent/agent/tool_router.py)

- `get_tools_for_input` 末尾调用 `record_tool_selection` 输出结构化日志

### 1.7 技术文档

**文件**: [docs/circuit_breaker_and_log_redaction.md](file:///c:/Users/Administrator/agent/docs/circuit_breaker_and_log_redaction.md)

- 9 章节技术文档：概述 / 架构设计 / 配置项矩阵 / 三层校验体系 / 日志脱敏逻辑 / tool_trace 事件接入 / 维护指南 / 相关提交 / 测试覆盖

---

## 二、配置项矩阵

| 字段 | 类型 | 范围 | SESSION | USER | GLOBAL | 说明 |
|------|------|------|---------|------|--------|------|
| failure_threshold | float | [0, 1] | 1.0 | 1.0 | 1.0 | 失败率阈值 |
| min_requests | int | [1, 10000] | 5 | 20 | 100 | 触发熔断最小请求数 |
| recovery_timeout | float | [0, 86400] | 60.0 | 300.0 | 600.0 | 冷却恢复时间（秒） |
| half_open_max_calls | int | [1, 100] | 1 | 2 | 3 | 半开状态最大探测请求数 |

---

## 三、测试覆盖率

### 3.1 全量测试统计

| 指标 | 数量 |
|------|------|
| 通过 (passed) | **8243** |
| 失败 (failed) | 14（全部预存在） |
| 跳过 (skipped) | 28 |
| 预期失败但通过 (xpassed) | 4 |
| 警告 (warnings) | 327 |
| **总计** | **8286** |
| 总耗时 | 32min 12s |

### 3.2 回归分析

**本次修改引入的回归：0**

14 个失败全部为预存在问题，与本次修改无关：
- `test_memory_optimized_deprecation.py` (4) — 其他会话修改 holographic_adapter.py
- `test_system_prompt_config_cache.py` (8) — deepcopy 既有 bug
- `test_observability_track_event.py` (1) — level 参数既有问题
- `test_tlm_memory_store.py` (1) — sqlite_vec 降级既有问题

### 3.3 专项测试覆盖

| 测试文件 | 用例数 | 覆盖范围 |
|----------|--------|----------|
| tests/unit/test_circuit_breaker_three_level.py | 29 | 级联触发、配置校验、trace 事件 |
| tests/unit/test_circuit_breaker_boundary.py | 多项 | failure_threshold 边界（含 1.0） |
| tests/unit/test_tool_trace.py | 58 | hash_content、采样、降级、trace 生命周期、集成测试 |

### 3.4 14 项校验体系验证

**29/29 验证全部通过**（7 个维度）：
1. ✓ ValidationRule 完整性（12 项 path 全部匹配）
2. ✓ Pydantic 模型字段对应
3. ✓ Pydantic 默认值加载（12 个默认值正确）
4. ✓ Pydantic alias 兼容性（`global` 键加载）
5. ✓ validate_dict_against_rules 实际校验（合法通过/非法拒绝）
6. ✓ _basic_validation required_sections
7. ✓ Pydantic 严格校验（超范围值拒绝）

---

## 四、校验报告链接

| 报告 | 位置 | 说明 |
|------|------|------|
| 14 项校验体系验证报告 | [_circuit_breaker_validation_report.md](file:///c:/Users/Administrator/agent/_circuit_breaker_validation_report.md) | 29 项验证详情 |
| 全量测试运行报告 | [_full_test_report.md](file:///c:/Users/Administrator/agent/_full_test_report.md) | 8243 passed / 14 failed（预存在） |
| 技术文档 | [docs/circuit_breaker_and_log_redaction.md](file:///c:/Users/Administrator/agent/docs/circuit_breaker_and_log_redaction.md) | 9 章节维护文档 |

---

## 五、上线检查清单

- [x] 三级熔断器实现完成（CircuitScope + ThreeLevelCircuitBreaker）
- [x] 14 项 ValidationRule 校验体系生效
- [x] Pydantic ConfigModel 集成完成
- [x] tool_trace 结构化日志接入
- [x] 日志脱敏（hash_content SHA256[:16]）
- [x] ToolCallingService _execute_safe trace 包裹
- [x] ToolRouter 工具选择日志
- [x] 技术文档完成
- [x] 全量测试零回归（8243 passed）
- [x] 14 项校验体系 29/29 验证通过

---

## 六、回滚方案

如需回滚，执行以下步骤：

1. **回退 commit**（master 分支）：
   ```bash
   git revert c19f0cf7
   ```

2. **手动回退**（如需彻底删除）：
   ```bash
   git reset --hard c19f0cf7^
   ```

3. **配置降级**：移除 `config.py` 中 `circuit_breaker` 字段，系统将退回单级熔断器模式

---

## 七、后续规划

- [ ] Pydantic V2 警告修复：`allow_population_by_field_name` → `validate_by_name`
- [ ] 考虑新增 TENANT（租户级）熔断（如有多租户需求）
- [ ] tool_trace 持久化到 SQLite 的熔断事件表（当前仅日志输出）

---

**发布人**: 云枢工程团队
**审核状态**: 待审核
