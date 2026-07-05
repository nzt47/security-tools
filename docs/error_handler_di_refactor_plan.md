# error_handler.py 依赖注入重构方案

> 文档版本：1.0
> 生成时间：2026-07-04
> 目标文件：[agent/error_handler.py](../agent/error_handler.py)
> 关联文档：[依赖注入重构与 Bug 修复报告](./di_refactor_and_bugfix_report.md)、[日志性能守护 CI 流水线文档](./ci_log_perf_guard.md)

## 一、背景与目标

### 1.1 当前问题

`error_handler.py` 是项目的统一错误处理和自动重试模块，提供 `with_retry` / `async_with_retry` 装饰器、`CircuitBreaker` 熔断器、`RetryPolicy` 重试策略。该模块被以下子系统依赖：

- `agent.monitoring.decorators`（在模块顶部 `from agent.error_handler import ...`）
- `agent.monitoring.alert_evaluator` / `error_reporter` / `prometheus` / `alert_notifier` / `self_healer`
- `agent.web.browser_agent`
- 业务层大量使用 `with_retry` 装饰器

而 `error_handler.py` 自身又在方法体内**延迟导入**了 `agent.monitoring.metrics.get_metrics_collector` 和 `agent.monitoring.observability_config.get_default_max_retries`，形成**真正的循环依赖**：

```
error_handler ──── (延迟导入) ───→ monitoring.metrics
      ↑                                    │
      │                                    ▼
      └── (模块级导入) ──── monitoring/__init__.py
                                           │
                                           ▼
                              monitoring/decorators.py:16
                          (from agent.error_handler import ...)
```

**根因**：`agent/monitoring/__init__.py:87` 在模块级执行 `from agent.monitoring.decorators import (...)`，而 `decorators.py:16` 在模块级执行 `from agent.error_handler import (get_error_handler, ErrorCategory, ErrorSeverity, YunshuError, RecoverableError, CriticalError)`。如果将 `error_handler` 中的 `get_metrics_collector` / `get_default_max_retries` 提到模块级，会触发 `monitoring/__init__.py` 加载链，最终 `decorators.py` 试图从尚未完全初始化的 `error_handler` 读取符号 → `ImportError` 或 `AttributeError`。

### 1.2 重构目标

- 消除 `error_handler.py` 中所有方法体内的延迟导入，统一改为依赖注入
- 保留 100% 向后兼容：所有现有调用方代码无需修改
- 提供可测试性：单元测试可注入 mock factory 完全隔离 `monitoring` 子系统
- 与 `perf_monitor.py` 的 DI 模式（`filter_chain_factory` / `log_dict_factory`）保持架构一致

### 1.3 安全性前提

经依赖图扫描确认：
- 模块级 `from agent.logging_utils import log_dict`（line 29）**无回环**，无需重构
- 仅有 `get_metrics_collector` 和 `get_default_max_retries` 两处延迟导入属于**真正的循环依赖规避**
- 其余模块级导入（stdlib）均安全

---

## 二、当前延迟导入清单

| 位置 | 行号 | 导入语句 | 用途 | 类型 |
|------|------|---------|------|------|
| `RetryPolicy.__init__` | 340-341 | `from agent.monitoring.observability_config import get_default_max_retries` | 读取默认重试次数 | **循环依赖** |
| `with_retry` 装饰器 | 652-653 | 同上 | 装饰器内每次调用读最新配置 | **循环依赖** |
| `async_with_retry` 装饰器 | 709-710 | 同上 | 同上（异步版） | **循环依赖** |
| `ErrorHandler.execute_with_retry` | 519-520 | `from agent.monitoring.metrics import get_metrics_collector` | 成功计数 | **循环依赖** |
| `ErrorHandler.execute_with_retry` | 552-553 | 同上 | 失败计数 | **循环依赖** |
| `async_with_retry` 装饰器 | 738-739 | 同上 | 成功计数 | **循环依赖** |
| `async_with_retry` 装饰器 | 767-768 | 同上 | 失败计数 | **循环依赖** |

合计 7 处延迟导入，对应 2 个目标符号。

---

## 三、重构 API 设计

### 3.1 三个可注入的工厂函数

| 工厂名 | 签名 | 返回 | 用途 |
|--------|------|------|------|
| `max_retries_factory` | `Callable[[], int]` | 默认重试次数 | 替代 `get_default_max_retries()` |
| `metrics_collector_factory` | `Callable[[], Optional[MetricsCollector]]` | 指标收集器实例（可能为 None） | 替代 `get_metrics_collector()` |
| `log_dict_factory` | `Callable[[Dict[str, Any]], Dict[str, Any]]` | 结构化日志字典 | 已知安全但保持架构一致（可选） |

> **注意**：`log_dict_factory` 为可选项。当前模块级 `from agent.logging_utils import log_dict` 无回环，可保持原状；但为与 `perf_monitor.py` 的 DI 模式统一，建议同样提供 factory 参数。**本方案默认不重构 `log_dict`**，仅作为可选项在文末说明。

### 3.2 `ErrorHandler.__init__` 签名扩展

```python
class ErrorHandler:
    def __init__(
        self,
        *,
        max_retries_factory: Optional[Callable[[], int]] = None,
        metrics_collector_factory: Optional[Callable[[], Any]] = None,
    ):
        self._metrics: Dict[str, ErrorMetrics] = defaultdict(ErrorMetrics)
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        # 依赖注入：优先使用注入的工厂，否则延迟导入（向后兼容）
        self._max_retries_factory = max_retries_factory
        self._metrics_collector_factory = metrics_collector_factory
        logger.info(log_dict({...}))
```

### 3.3 `RetryPolicy.__init__` 签名扩展

```python
class RetryPolicy:
    def __init__(
        self,
        max_retries: Optional[int] = None,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
        jitter_factor: float = 0.1,
        strategy: str = "exponential",
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        retryable_status_codes: Optional[List[int]] = None,
        custom_retry_condition: Optional[Callable[[Exception], bool]] = None,
        max_retries_factory: Optional[Callable[[], int]] = None,  # ← 新增
    ):
        if max_retries is None:
            if max_retries_factory is not None:
                max_retries = max_retries_factory()
            else:
                # 延迟导入保持向后兼容
                from agent.monitoring.observability_config import get_default_max_retries
                max_retries = get_default_max_retries()
        self.max_retries = max_retries
        self._max_retries_factory = max_retries_factory
        # ... 其余不变
```

### 3.4 `execute_with_retry` 内部使用注入的 factory

```python
def execute_with_retry(
    self,
    func: Callable[P, R],
    retry_policy: Optional[RetryPolicy] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    error_counter: Optional[str] = None,
    func_args: Optional[Tuple] = None,
    func_kwargs: Optional[Dict] = None,
) -> R:
    # ...
    for attempt in range(policy.max_retries + 1):
        try:
            result = ...
            if error_counter and attempt == 0:
                collector = self._get_metrics_collector()  # ← 改为内部方法
                if collector:
                    collector.increment_counter(f"{error_counter}.success")
            return result
        except Exception as e:
            # ...
            if error_counter:
                collector = self._get_metrics_collector()
                if collector:
                    collector.increment_counter(f"{error_counter}.failure")
            raise self.record_error(e)

def _get_metrics_collector(self):
    """获取指标收集器（依赖注入 + 延迟导入兜底）"""
    if self._metrics_collector_factory is not None:
        return self._metrics_collector_factory()
    # 延迟导入保持向后兼容
    try:
        from agent.monitoring.metrics import get_metrics_collector
        return get_metrics_collector()
    except Exception:
        return None
```

### 3.5 装饰器 `with_retry` / `async_with_retry` 签名扩展

```python
def with_retry(
    max_retries: Optional[int] = None,
    initial_delay: float = 1.0,
    # ... 现有参数
    error_counter: Optional[str] = None,
    max_retries_factory: Optional[Callable[[], int]] = None,  # ← 新增
    metrics_collector_factory: Optional[Callable[[], Any]] = None,  # ← 新增
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            _max_retries = max_retries
            if _max_retries is None:
                if max_retries_factory is not None:
                    _max_retries = max_retries_factory()
                else:
                    # 延迟导入兜底
                    from agent.monitoring.observability_config import get_default_max_retries
                    _max_retries = get_default_max_retries()
            # ...
            handler = get_error_handler()
            # 如果传入了 metrics_collector_factory，覆盖 handler 的工厂
            if metrics_collector_factory is not None:
                handler._metrics_collector_factory = metrics_collector_factory
            # ...

    return decorator
```

> **注意**：装饰器版本通过设置全局 `ErrorHandler` 实例的 `_metrics_collector_factory` 属性实现注入。这种"配置式注入"虽然不如直接传参优雅，但保持了装饰器签名的简洁性。更纯净的方案是引入 `configure_error_handler(metrics_collector_factory=...)` 模块级 setter，见 §6.2。

---

## 四、变更范围

### 4.1 需修改的文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| [agent/error_handler.py](../agent/error_handler.py) | 改动 | 添加 DI 参数、内部辅助方法 `_get_metrics_collector` / `_get_max_retries` |
| [tests/unit/test_error_handler.py](../tests/unit/test_error_handler.py) | 改动 | 新增 `TestErrorHandlerDependencyInjection` 测试类 |
| [tests/unit/test_error_handler_comprehensive.py](../tests/unit/test_error_handler_comprehensive.py) | 改动 | 补充 DI 边界测试 |
| [docs/di_refactor_and_bugfix_report.md](./di_refactor_and_bugfix_report.md) | 改动 | 在"后续建议"章节标记本方案已落地 |

### 4.2 不需要修改的文件

- `agent/monitoring/decorators.py` — 维持现有模块级导入，因为 `error_handler` 重构后仍然会在不传 factory 时延迟导入 `monitoring.metrics`，循环依赖在原调用路径下依然由"延迟导入"打破
- 所有 `with_retry` / `async_with_retry` 装饰器的现有调用方 — 向后兼容
- `agent/orchestrator/lifecycle_manager.py` — 不依赖 `error_handler` 的延迟导入路径

---

## 五、测试计划

### 5.1 测试矩阵

| 维度 | 测试数 | 用例 |
|------|--------|------|
| `max_retries_factory` 调用验证 | 4 | factory 被调用、返回值被使用、factory 抛异常时回落到延迟导入、factory 返回 None 时的边界 |
| `metrics_collector_factory` 调用验证 | 5 | factory 被调用、返回的 collector 接收 increment_counter 调用、factory 返回 None 时跳过计数、success 和 failure 两个分支都被覆盖、factory 抛异常时不影响主流程 |
| 组合场景 | 2 | 同时注入两个 factory、注入模式与默认模式行为一致 |
| 边界处理 | 3 | factory 返回非标准对象（duck typing 验证）、factory 抛 RuntimeError、factory 返回 None |
| 完全解耦验证 | 3 | 用 mock 拦截 `agent.monitoring.metrics` 导入、用 mock 拦截 `agent.monitoring.observability_config` 导入、注入模式下不触发任何 `agent.monitoring` 导入 |
| 装饰器版本验证 | 3 | `@with_retry(max_retries_factory=...)` 工作正常、`@async_with_retry(metrics_collector_factory=...)` 工作正常、装饰器版本与直接调用 `execute_with_retry` 行为一致 |

**合计约 20 个测试**，参照 `tests/unit/test_perf_monitor.py::TestStressTestDependencyInjection` 的结构组织。

### 5.2 关键测试样例

```python
class TestErrorHandlerDependencyInjection:
    """error_handler 依赖注入测试套件"""

    def test_max_retries_factory_is_called(self):
        """验证 max_retries_factory 在 RetryPolicy 初始化时被调用"""
        call_count = 0
        def factory():
            nonlocal call_count
            call_count += 1
            return 7
        policy = RetryPolicy(max_retries_factory=factory)
        assert policy.max_retries == 7
        assert call_count == 1

    def test_metrics_collector_factory_receives_increment(self):
        """验证注入的 metrics_collector_factory 返回的对象接收计数调用"""
        class _CountingCollector:
            def __init__(self):
                self.counts = {}
            def increment_counter(self, key):
                self.counts[key] = self.counts.get(key, 0) + 1

        collector = _CountingCollector()
        handler = ErrorHandler(metrics_collector_factory=lambda: collector)
        # ... 触发 execute_with_retry
        assert "test_op.success" in collector.counts

    def test_complete_decoupling_from_monitoring(self):
        """验证注入模式下完全不导入 agent.monitoring.*"""
        import builtins
        real_import = builtins.__import__
        monitored_imports = []
        def _tracking_import(name, *args, **kwargs):
            if name.startswith("agent.monitoring"):
                monitored_imports.append(name)
            return real_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=_tracking_import):
            handler = ErrorHandler(
                max_retries_factory=lambda: 3,
                metrics_collector_factory=lambda: None,
            )
            policy = RetryPolicy(max_retries_factory=lambda: 3)
            # 触发完整执行路径
            handler.execute_with_retry(lambda: 42, retry_policy=policy)
        # 验证完全没有 agent.monitoring 的导入
        assert monitored_imports == [], f"检测到意外导入: {monitored_imports}"
```

### 5.3 性能验证

注入 factory 不应引入额外开销（与现有延迟导入相当）。新增基准测试：

```python
def test_di_no_performance_regression(self):
    """验证 DI 模式与延迟导入模式性能相当（< 5% 差异）"""
    import timeit
    # DI 模式
    di_time = timeit.timeit(lambda: RetryPolicy(max_retries_factory=lambda: 3).max_retries, number=10000)
    # 延迟导入模式
    fallback_time = timeit.timeit(lambda: RetryPolicy().max_retries, number=10000)
    # 容许 5% 差异（CI 抖动）
    assert di_time < fallback_time * 1.05
```

---

## 六、风险与缓解

### 6.1 风险一：装饰器版本注入路径不够纯净

**问题**：`@with_retry(metrics_collector_factory=...)` 通过修改全局 `_global_error_handler` 的属性实现注入，在多线程并发场景下可能存在竞态。

**缓解**：
- 在测试场景下，每个测试通过 `get_error_handler()` 获取独立实例并直接设置属性
- 在生产场景下，工厂函数应为纯函数（返回固定实例），避免共享可变状态
- 文档明确说明：装饰器级别的 `metrics_collector_factory` 仅用于初始化阶段，不应在运行期动态切换

### 6.2 风险二：循环依赖在原路径下依然存在

**问题**：即使添加了 DI 支持，未注入 factory 时仍然会触发延迟导入，`monitoring/__init__.py` 加载链没有改变。

**缓解**：这是设计预期——保持 100% 向后兼容的代价。未来可推动以下任一长期方案：

1. **将 `monitoring/decorators.py` 的 `from agent.error_handler import` 改为延迟导入**：彻底消除 `monitoring/__init__.py` 触发的回环
2. **引入 `agent.bootstrap` 模块级 setter**：`configure_error_handler(max_retries_fn=..., metrics_fn=...)` 在系统启动时统一注入，业务代码完全无需感知

### 6.3 风险三：测试 mock 过度

**问题**：`test_complete_decoupling_from_monitoring` 通过 mock `builtins.__import__` 来验证不触发导入。这可能与 pytest 插件、第三方库冲突。

**缓解**：
- 仅在该测试中使用 mock，且 mock 作用域精确到 `agent.monitoring.*`
- 测试结束后立即 `mock.patch.stopall()`
- 添加 `pytestmark = pytest.mark.isolated` 标记，便于必要时单独运行

---

## 七、实施步骤

### 7.1 阶段 1：核心 API 改造（预估 1 小时）

1. 在 `ErrorHandler.__init__` 中添加 `max_retries_factory` 和 `metrics_collector_factory` 参数
2. 添加内部辅助方法 `_get_metrics_collector()` 和 `_get_max_retries()`
3. 在 `execute_with_retry` 中将 4 处 `from agent.monitoring.metrics import get_metrics_collector` 替换为 `self._get_metrics_collector()`
4. 在 `RetryPolicy.__init__` 中添加 `max_retries_factory` 参数
5. 在 `with_retry` / `async_with_retry` 装饰器签名中添加两个 factory 参数

### 7.2 阶段 2：测试编写（预估 1.5 小时）

1. 创建 `tests/unit/test_error_handler_di.py`（或合并到 `test_error_handler.py`）
2. 实现 6 个维度的 20 个测试
3. 运行 `pytest tests/unit/test_error_handler.py -v` 验证

### 7.3 阶段 3：CI 集成（预估 0.5 小时）

1. 在 `.github/workflows/log-perf-guard.yml` 的 `di-unit-tests` job 中追加 `tests/unit/test_error_handler.py::TestErrorHandlerDependencyInjection` 到测试列表
2. 更新 `docs/ci_log_perf_guard.md` 文档

### 7.4 阶段 4：回归验证（预估 0.5 小时）

1. 运行全量测试套件（114 + 20 = 134 个测试）
2. 检查覆盖率，目标：`error_handler.py` 行覆盖率 ≥ 90%
3. 输出完成报告

---

## 八、验收标准

- [ ] 所有现有测试通过（114 个，无回归）
- [ ] 新增 20 个 DI 测试通过
- [ ] `test_complete_decoupling_from_monitoring` 通过：注入模式下完全不导入 `agent.monitoring.*`
- [ ] `test_di_no_performance_regression` 通过：DI 模式与延迟导入性能相当
- [ ] `error_handler.py` 行覆盖率 ≥ 90%
- [ ] 装饰器版本（`@with_retry(max_retries_factory=...)`）工作正常
- [ ] 文档更新：[di_refactor_and_bugfix_report.md](./di_refactor_and_bugfix_report.md) 中"后续建议"章节更新

---

## 九、附录：与 perf_monitor.py DI 模式对比

| 维度 | perf_monitor.py | error_handler.py |
|------|----------------|------------------|
| 工厂数量 | 2（filter_chain_factory, log_dict_factory） | 2（max_retries_factory, metrics_collector_factory） |
| 注入点 | `stress_test()` 函数 | `ErrorHandler.__init__`, `RetryPolicy.__init__`, `with_retry`, `async_with_retry` |
| 兜底机制 | 延迟导入 `logging_utils` | 延迟导入 `monitoring.metrics` / `monitoring.observability_config` |
| 完全解耦验证 | `test_complete_decoupling_from_logging_utils` | `test_complete_decoupling_from_monitoring` |
| 修复的循环依赖 | `perf_monitor ↔ logging_utils`（设计耦合，非真正循环） | `error_handler ↔ monitoring`（**真正循环依赖**） |
| Bug 修复 | `_DiscardHandler` 继承 NullHandler | 无（仅 DI 重构） |

> **关键差异**：`perf_monitor` 的"循环依赖"是设计耦合（perf_monitor 直接 import `logging_utils` 中的具体 Filter 类），并非真正的 ImportError；`error_handler` 的循环依赖是真正的 ImportError（由 `monitoring/__init__.py` 触发 `decorators.py` 模块级导入造成），重构收益更大。

---

## 十、待用户确认事项

在开始实施前，请确认以下决策：

1. **`log_dict` 是否一并迁移**？
   - 选项 A：保持模块级 `from agent.logging_utils import log_dict`（无回环，无需重构）
   - 选项 B：为架构一致性也添加 `log_dict_factory` 参数（推荐，但增加 3-4 个测试点）

2. **装饰器版本的注入路径**？
   - 选项 A：通过修改全局 `_global_error_handler` 实例属性（当前方案，简单但有竞态风险）
   - 选项 B：引入 `configure_error_handler(...)` 模块级 setter（更纯净，但 API 表面更大）

3. **测试文件组织**？
   - 选项 A：合并到 `tests/unit/test_error_handler.py::TestErrorHandlerDependencyInjection`
   - 选项 B：新建独立文件 `tests/unit/test_error_handler_di.py`

4. **是否同步更新 CI 配置**？
   - 选项 A：是，将新增 20 个 DI 测试纳入 `di-unit-tests` job
   - 选项 B：否，先验证再下个迭代纳入
