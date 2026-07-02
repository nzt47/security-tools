# 迭代技术总结：边界治理 Phase 2 — 配置化 + 静态防护 + 硬编码扫描

> **迭代周期**：2026-07-02
> **分支**：`phase2-visibility-convergence`
> **核心提交**：
> - `bffb130d` feat(boundary): MAX_ANALYZE_DAYS 配置化为可配置项（Task 1）
> - `47eb0fde` feat(ci): 新增 timedelta 溢出静态分析脚本与 boundary-guard workflow（Task 2）
> - `df889add` docs(observability): 新增硬编码边界值扫描报告（Task 3）
> - *(pending)* feat(boundary): max_retries 配置化为可配置项（P1 改造续）
> **文档版本**：v1.0.0
> **生成时间**：2026-07-02

---

## 一、迭代概述

本次迭代是边界治理 Phase 2 的第一轮，聚焦于 **硬编码边界值配置化**、**CI 静态防护建设** 和 **全量硬编码扫描**，目标是将散落于多处的硬编码边界值收拢到统一的 `ObservabilityConfig` 声明式配置体系，并建立 CI 自动检测机制防止新增溢出风险。

### 核心成果

| 指标 | Phase 1 结束 | Phase 2 本轮 | 变化 |
|------|-------------|-------------|------|
| 配置化边界项 | 0 | **4**（time_window + 3 个 retry） | +4 |
| CI 静态分析规则 | 0 | **1**（timedelta 溢出扫描） | +1 |
| 硬编码扫描覆盖 | 仅 timedelta | **5 类模式 93 处** | +4 类 |
| 待配置化清单 | 未知 | **18 项（P1/P2/P3）** | 明确化 |
| 配置项总数 | 16 | **19**（+3 retry 相关） | +3 |

---

## 二、问题背景

### 2.1 Phase 1 遗留问题

Phase 1 完成了 4 个方法的 `timedelta(days=参数)` 溢出校验，但存在以下遗留：

1. **上限值硬编码**：`MAX_ANALYZE_DAYS = 36500` 散落于 3 个模块，修改需同步多处
2. **无 CI 防护**：新增的 `timedelta(days=参数)` 调用无法自动检测
3. **硬编码清单缺失**：除 timedelta 外，重试次数、超时值、容量限制等硬编码值分布未知

### 2.2 配置化改造需求

参照项目硬约束：

> All timeout and retry configuration must use the unified ValidationRule architecture in config.py

所有超时与重试配置必须使用统一的 `ValidationRule` 架构。Phase 1 仅完成了 `time_window.max_analyze_days`，重试次数仍为硬编码。

---

## 三、完成内容

### 3.1 Task 1: MAX_ANALYZE_DAYS 配置化

**目标**：将 `MAX_ANALYZE_DAYS = 36500` 从硬编码常量改为可配置项。

**改造范围**（3 个模块、4 个方法）：

| 文件 | 方法 | 改造内容 |
|------|------|----------|
| `observability_config.py` | — | 新增 `time_window.max_analyze_days` ValidationRule + `get_max_analyze_days()` 便捷函数 |
| `data_analytics.py` | `analyze_event_trends` | `MAX_ANALYZE_DAYS` → `_get_max_analyze_days()` 委托函数 |
| `replay_storage.py` | `cleanup_old_records` | 硬编码 36500 → `get_max_analyze_days()` |
| `defect_tracker.py` | `calculate_escape_rate` / `get_escape_rate_trend` | 硬编码 36500 → `get_max_analyze_days()` |

**关键设计**：
- 保留原 `MAX_ANALYZE_DAYS` 常量作为向后兼容别名，不破坏现有调用
- 支持热加载：`config.set("time_window.max_analyze_days", 100)` 立即生效
- 原子性变更：验证失败自动回滚

**验证**：160 个测试通过 + 配置热加载验证 5/5 OK

### 3.2 Task 2: CI 静态分析规则

**目标**：建立 CI 自动检测机制，防止新增 `timedelta(days=参数)` 溢出风险。

**交付物**：
- `scripts/check_timedelta_overflow.py` — 375 行 AST 静态分析器
- `.github/workflows/boundary-guard.yml` — CI workflow

**风险分级逻辑**：

| 风险等级 | 判定条件 | 处理方式 |
|----------|----------|----------|
| `high` | `days` 来自函数参数（用户可控） | 必须添加参数校验 |
| `medium` | `days` 为变量/表达式 | 需人工核实 |
| `low` | `days` 为字面量常量 | 无溢出风险 |

**扫描结果**（agent/ 308 个文件）：
- timedelta 调用总数：16
- 高风险 3 / 中风险 6 / 低风险 7
- 3 个高风险均已在 Task 1 中添加参数校验 ✅

**CI 策略**：基线 3 — `high_risk > 3` 阻断 CI，`≤ 3` 仅警告

### 3.3 Task 3: 硬编码边界值扫描

**目标**：全面扫描代码库中的硬编码边界值，为统一改造提供清单。

**扫描范围**（5 类模式 93 处命中）：

| 类别 | 扫描模式 | 命中数 | 需配置化 |
|------|----------|--------|----------|
| timedelta 溢出 | AST 分析 | 16 | 0（Phase 1 已修复） |
| 重试次数 | `max_retries = N` | 8 | 3 |
| 超时值 | `timeout = N` | 13 | 5 |
| 容量/并发限制 | `max_workers/pool_size = N` | 43 | 6（排除测试） |
| 模块级常量 | `MAX_*/MIN_*/LIMIT_*` | 13 | 4 |
| **合计** | — | **93** | **18** |

**改造清单**：P1（6 项，3.5h）/ P2（7 项，4.5h）/ P3（5 项，可延后）

### 3.4 max_retries 配置化（P1 续）

**目标**：将 P1 清单中的 `max_retries` 相关硬编码改为可配置项。

**改造范围**（3 个模块）：

| 文件 | 改造内容 | 配置路径 |
|------|----------|----------|
| `observability_config.py` | 新增 3 个 ValidationRule + 3 个便捷函数 | `retry.default_max_retries` / `cognitive.reflection_max_retries` / `http.max_retries` |
| `error_handler.py` | `RetryPolicy` / `with_retry` / `async_with_retry` 默认值改为从 Config 读取 | `retry.default_max_retries` |
| `reflection.py` | `MAX_RETRIES` 类属性改为 `_get_max_retries()` 方法 | `cognitive.reflection_max_retries` |
| `http_client.py` | `DEFAULT_MAX_RETRIES` fallback 改为从 Config 读取 | `http.max_retries` |

**关键设计**：
- `Optional[int] = None` 签名模式：未显式传参时从 Config 读取，支持热加载
- `with_retry` / `async_with_retry` 在 wrapper 内部解析，每次调用读取最新值
- 保留原 `MAX_RETRIES` / `DEFAULT_MAX_RETRIES` 常量作为向后兼容别名
- 测试中 `RetryPolicy(max_retries=N)` 显式传参不受影响

**验证**：6 项配置化功能验证全部通过

---

## 四、技术方案详解

### 4.1 配置化改造统一模式

本次迭代建立了标准的配置化改造模式，后续 P1/P2/P3 项均可参照：

```
硬编码常量 → ValidationRule 声明 → 便捷函数封装 → 业务模块从 Config 读取 → 保留原常量作为向后兼容别名
```

**ValidationRule 声明示例**：

```python
ValidationRule(
    path="retry.default_max_retries",
    validator=_range_validator(0, 20),
    default=3,
    error_message="retry.default_max_retries 必须在 0-20 之间（0 表示不重试）",
    description="默认最大重试次数，用于 error_handler.py 的 RetryPolicy/with_retry/async_with_retry",
),
```

**便捷函数示例**：

```python
def get_default_max_retries() -> int:
    try:
        return int(get_observability_config().get("retry.default_max_retries", default=3))
    except Exception:
        return 3
```

**业务模块使用示例（Optional[int] = None 模式）**：

```python
def with_retry(max_retries: Optional[int] = None, ...):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 配置化：未显式指定时从 Config 读取（支持热加载）
            _max_retries = max_retries
            if _max_retries is None:
                from agent.monitoring.observability_config import get_default_max_retries
                _max_retries = get_default_max_retries()
            ...
```

### 4.2 AST 静态分析架构

`scripts/check_timedelta_overflow.py` 基于 Python `ast` 模块的 `NodeVisitor` 模式：

- `TimedeltaOverflowVisitor` — 维护函数参数上下文栈，追踪局部变量赋值
- `_is_timedelta_call(node)` — 判断是否为 `timedelta(...)` 或 `datetime.timedelta(...)` 调用
- `_extract_days_arg(call_node)` — 提取 days 参数（位置参数或关键字参数）
- `_classify_risk(days_node)` — 基于 AST 节点类型进行风险分级

**CLI 接口**：

```bash
# 控制台报告
python scripts/check_timedelta_overflow.py --target agent/

# JSON 报告（CI 使用）
python scripts/check_timedelta_overflow.py --target agent/ --json --output report.json

# 高风险阻断模式（基线策略）
python scripts/check_timedelta_overflow.py --target agent/ --fail-on-high-risk
```

### 4.3 CI 基线策略

`.github/workflows/boundary-guard.yml` 采用基线策略：

- 当前基线：`high_risk = 3`（3 个已校验的高风险调用）
- `high_risk > 3`：`core.setFailed` 阻断 CI（新增未校验的高风险）
- `high_risk ≤ 3`：`core.warning` 仅警告（已校验的存量风险）
- 扫描报告作为 artifact 上传，保留 30 天

---

## 五、验证结果

### 5.1 配置化功能验证

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | 3 个配置项默认值均为 3 | ✅ |
| 2 | RetryPolicy 不传参时从 Config 读取 | ✅ |
| 3 | RetryPolicy 显式传参不受默认值影响 | ✅ |
| 4 | ReflectionEngine 配置化 + 向后兼容常量保留 | ✅ |
| 5 | HTTP max_retries 配置化 | ✅ |
| 6 | 恢复默认值后行为恢复 | ✅ |

### 5.2 静态分析脚本验证

```
扫描文件数: 308
timedelta 调用总数: 16
高风险: 3（均已校验）
中风险: 6（实际安全）
低风险: 7（无溢出风险）
```

### 5.3 测试回归验证

运行 `test_error_handler*.py` + `test_cognitive_loop.py` + `test_http_client.py`，确认无回归。

### 5.4 配置系统完整性

- 配置项总数：19（原 16 + 新增 3 个 retry 相关）
- 启动时自动验证并修复：✅
- 热加载：✅
- 原子性变更（验证失败回滚）：✅

---

## 六、Git 提交记录

```
df889add docs(observability): 新增硬编码边界值扫描报告（Task 3）
47eb0fde feat(ci): 新增 timedelta 溢出静态分析脚本与 boundary-guard workflow（Task 2）
bffb130d feat(boundary): MAX_ANALYZE_DAYS 配置化为可配置项（Task 1）
*(pending)* feat(boundary): max_retries 配置化为可配置项（P1 改造续）
```

---

## 七、风险评估

### 7.1 向后兼容性

| 改造项 | 向后兼容措施 | 风险等级 |
|--------|-------------|----------|
| MAX_ANALYZE_DAYS | 保留常量作为别名 | 低 |
| RetryPolicy 默认值 | Optional[int] = None + 内部解析 | 低 |
| with_retry / async_with_retry | Optional[int] = None + wrapper 内部解析 | 低 |
| ReflectionEngine.MAX_RETRIES | 保留类属性 + 新增 _get_max_retries() 方法 | 低 |
| DEFAULT_MAX_RETRIES | 保留常量作为别名 | 低 |

### 7.2 性能影响

- 便捷函数每次调用读取 Config（受 RLock 保护），单次耗时 < 0.1ms
- `with_retry` / `async_with_retry` 在 wrapper 内部解析，仅在被装饰函数调用时执行一次
- 对主流程性能影响可忽略

### 7.3 已知限制

1. **基线策略需定期更新**：当新增已校验的高风险 timedelta 调用时，需同步更新基线值
2. **AST 分析仅覆盖 timedelta**：重试次数、超时值等硬编码尚未有 CI 自动检测
3. **配置热加载无审计日志**：配置变更仅记录到 `_change_log`，未推送至外部审计系统

---

## 八、后续建议

### 8.1 短期（下一迭代）

1. **完成 P1 剩余项**：`http.timeout_sec` / `http.connect_timeout_sec` / `http.pool_size`（预计 1.5h）
2. **扩展静态分析**：在 `check_timedelta_overflow.py` 基础上扩展重试次数/超时值的检测
3. **混沌测试**：注入配置异常值（如 max_retries=0、timeout=0），验证降级行为

### 8.2 中期

1. **P2 配置化**：缓存容量、调度器常量、监控超时等 7 项（预计 4.5h）
2. **配置变更审计**：将 `_change_log` 推送至 Loki/Prometheus，实现配置变更可观测
3. **配置 A/B 测试**：支持按租户/灰度级别应用不同配置值

### 8.3 长期

1. **P3 配置化**：模块级边界常量（可延后）
2. **配置漂移检测**：对比运行时配置与配置文件，发现未授权变更
3. **配置推荐系统**：基于历史负载数据自动推荐最优配置值

---

## 九、附录

### 9.1 相关文档

- [边界值溢出修复迭代技术总结（Phase 1）](iteration_summary_boundary_overflow_fix.md)
- [硬编码边界值扫描报告](hardcoded_boundary_scan_report.md)
- [边界治理 Phase 2 迭代行动计划](next_iteration_plan.md)

### 9.2 配置项速查表

| 配置路径 | 默认值 | 范围 | 用途 |
|----------|--------|------|------|
| `time_window.max_analyze_days` | 36500 | 1-36500 | timedelta(days=) 上限 |
| `retry.default_max_retries` | 3 | 0-20 | RetryPolicy/with_retry/async_with_retry 默认重试次数 |
| `cognitive.reflection_max_retries` | 3 | 1-10 | 反思引擎最大重试次数 |
| `http.max_retries` | 3 | 0-10 | HTTP 客户端默认重试次数 |
