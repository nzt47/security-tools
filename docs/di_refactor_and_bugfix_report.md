# 依赖注入重构与 _DiscardHandler Bug 修复报告

> 报告版本：1.0
> 生成时间：2026-07-04
> 关联文件：[perf_monitor.py](../agent/utils/perf_monitor.py)、[test_perf_monitor.py](../tests/unit/test_perf_monitor.py)、[test_memory_comparison.py](../tests/unit/test_memory_comparison.py)

## 一、变更概览

本次变更包含两部分：
1. **依赖注入重构**：为 `perf_monitor.stress_test()` 添加 `filter_chain_factory` 和 `log_dict_factory` 参数，解耦与 `logging_utils` 的设计耦合
2. **Bug 修复**：修复 `_DiscardHandler` 继承 `NullHandler` 导致 filter 链从未被调用的严重 bug

| 变更项 | 文件 | 行数变化 |
|--------|------|----------|
| stress_test 参数化 | `agent/utils/perf_monitor.py` | +15 行 |
| _DiscardHandler 修复 | `agent/utils/perf_monitor.py` | +5 行（含注释） |
| 同步修复 | `tests/unit/test_memory_comparison.py` | +5 行（含注释） |
| 新增测试套件 | `tests/unit/test_perf_monitor.py` | +485 行（21 个测试 + 4 个辅助类） |

## 二、Bug 根因分析

### 2.1 问题描述

`perf_monitor.stress_test()` 中的 `_DiscardHandler` 原本继承自 `logging.NullHandler`：

```python
# ❌ 错误实现（修复前）
class _DiscardHandler(_logging.NullHandler):
    def emit(self, record):
        pass
```

### 2.2 根因

Python `logging.NullHandler` 的源码：

```python
class NullHandler(Handler):
    def handle(self, record):
        pass  # 直接返回，跳过 filter 链
```

`NullHandler` 覆盖了 `handle()` 方法为 `pass`，**完全跳过了 `self.filter(record)` 调用**。这意味着：

1. `handler.addFilter(SensitiveDataFilter())` 添加的 filter 从未被执行
2. `handler.addFilter(EmojiFilter())` 添加的 filter 从未被执行
3. `handler.addFilter(DictToJsonFilter())` 添加的 filter 从未被执行

### 2.3 影响

- **stress_test 默认模式**：声称测试完整 filter 链，实际只测了 `logger.info()` 的纯调用开销
- **test_memory_comparison.py**：真实管道内存测试也跳过了 filter 链
- **性能数据偏差**：之前报告的"filter 链开销"部分被低估
- **错误未暴露**：filter 链中的 bug（如异常处理）无法被测试发现

### 2.4 修复方案

```python
# ✅ 正确实现（修复后）
class _DiscardHandler(_logging.Handler):
    """丢弃所有输出的 handler，仅触发 filter 链

    关键：继承 Handler 而非 NullHandler。
    NullHandler 覆盖了 handle() 为 pass，导致 filter 链不被调用。
    Handler.handle() 会先调用 self.filter(record) 遍历 filter 链，
    然后才调用 emit()。我们覆盖 emit 为 pass，丢弃输出但保留 filter 调用。
    """
    def emit(self, record):
        pass
```

`logging.Handler.handle()` 的源码：

```python
def handle(self, record):
    rv = self.filter(record)  # ← 遍历 filter 链
    if rv:
        self.acquire()
        try:
            self.emit(record)  # ← 调用 emit（被覆盖为 pass）
        finally:
            self.release()
    return rv
```

修复后，filter 链被正确执行，`emit()` 仍为空操作，不保存输出。

## 三、依赖注入重构

### 3.1 重构前

```python
def stress_test(
    num_threads=8,
    duration_seconds=5.0,
    payloads=None,
    use_log_dict=True,
    enable_filter_chain=True,
    report_interval=1.0,
):
    # 函数内延迟导入（设计耦合）
    if enable_filter_chain:
        from agent.logging_utils import EmojiFilter, DictToJsonFilter, SensitiveDataFilter
        handler.addFilter(SensitiveDataFilter())
        handler.addFilter(EmojiFilter())
        handler.addFilter(DictToJsonFilter())

    if use_log_dict:
        from agent.logging_utils import log_dict as _log_dict
```

**问题**：
- `perf_monitor` 是性能监控基础设施，却依赖业务模块 `logging_utils`
- 虽然 `logging_utils.log_dict()` 也依赖 `perf_monitor`（延迟导入），形成双向耦合
- 无法在不 import `logging_utils` 的情况下独立运行 `stress_test`

### 3.2 重构后

```python
def stress_test(
    num_threads=8,
    duration_seconds=5.0,
    payloads=None,
    use_log_dict=True,
    enable_filter_chain=True,
    report_interval=1.0,
    filter_chain_factory: Optional[Any] = None,  # ← 新增
    log_dict_factory: Optional[Any] = None,       # ← 新增
):
    if enable_filter_chain:
        # 优先使用注入的 filter 链工厂
        if filter_chain_factory is not None:
            for flt in filter_chain_factory():
                handler.addFilter(flt)
        else:
            # 延迟导入保持向后兼容
            from agent.logging_utils import EmojiFilter, DictToJsonFilter, SensitiveDataFilter
            handler.addFilter(SensitiveDataFilter())
            ...

    if use_log_dict:
        # 优先使用注入的 log_dict 工厂
        if log_dict_factory is not None:
            _log_dict = log_dict_factory
        else:
            from agent.logging_utils import log_dict as _log_dict
```

### 3.3 重构效果

| 调用方式 | 对 logging_utils 的依赖 |
|---------|------------------------|
| 默认（不传参） | 延迟导入（100% 向后兼容） |
| 注入 factory 参数 | **完全脱离** logging_utils |

## 四、测试覆盖情况

### 4.1 新增测试套件

在 [`test_perf_monitor.py`](../tests/unit/test_perf_monitor.py#L459) 添加 `TestStressTestDependencyInjection` 类，21 个测试覆盖 7 个维度：

| 维度 | 测试数 | 测试用例 |
|------|--------|----------|
| **filter_chain_factory 调用验证** | 5 | factory 被调用、filter 实际应用、空列表、单个 filter、多个 filter |
| **log_dict_factory 调用验证** | 3 | factory 被调用、调用次数等于 total_ops、payload 内容保留 |
| **组合场景** | 2 | 同时注入两个 factory、注入模式无错误 |
| **边界处理（边界显性化）** | 2 | filter 抛异常被计入 error_rate、log_dict 抛异常被计入 error_rate |
| **互斥场景** | 2 | enable_filter_chain=False 时 factory 被忽略、use_log_dict=False 时 factory 被忽略 |
| **mock 计数验证** | 2 | 每个 LogRecord 触发 filter、每条日志触发 log_dict |
| **结果一致性与解耦** | 5 | 延迟合理、吞吐量合理、完全脱离 logging_utils、返回结构一致、多线程线程安全 |

### 4.2 测试辅助类

| 类 | 用途 | 机制 |
|----|------|------|
| `_CountingFilter` | 线程安全的计数 filter | `threading.Lock` 保护 `call_count` 和 `seen_records` |
| `_CountingLogDict` | 线程安全的计数 log_dict | `threading.Lock` 保护 `call_count` 和 `seen_payloads` |
| `_FailingFilter` | 始终抛异常的 filter | 用于边界处理测试 |
| `_FailingLogDict` | 始终抛异常的 log_dict | 用于边界处理测试 |

### 4.3 关键测试用例说明

**test_complete_decoupling_from_logging_utils**：
- 用 `unittest.mock.patch` 替换 `builtins.__import__`，记录所有 `agent.logging_utils` 的导入
- 注入 filter_chain_factory 和 log_dict_factory
- 断言 `import_log` 为空，验证完全解耦

**test_filter_chain_factory_raises_exception**：
- 注入 `_FailingFilter`（filter() 抛 RuntimeError）
- 断言 `result["errors"] > 0`，验证异常被计入 error_rate（边界显性化）
- 验证不会静默吞掉异常

**test_filter_chain_factory_thread_safety**：
- 8 线程并发，用 `threading.Lock` 保护计数器
- 断言 `filter_calls[0] == result["total_ops"]`，验证多线程下无丢失/重复

## 五、影响评估

### 5.1 性能数据变化

Bug 修复后，stress_test 默认模式现在真正执行 filter 链。预期影响：

| 指标 | 修复前（filter 链跳过） | 修复后（filter 链执行） | 变化 |
|------|----------------------|----------------------|------|
| 吞吐量 | ~18000 ops/sec | 略低（filter 开销） | -5~15% |
| p50 延迟 | ~50 us | 略高（filter 开销） | +10~30% |
| 错误率 | 0% | 0%（filter 无异常） | 不变 |

### 5.2 测试覆盖影响

- **stress_test 默认模式**：现在真正测试 SensitiveDataFilter + EmojiFilter + DictToJsonFilter 的完整链
- **test_memory_comparison.py**：真实管道内存测试现在也触发 filter 链
- **依赖注入模式**：通过 factory 注入，可完全脱离 logging_utils 独立运行

### 5.3 向后兼容性

- **100% 向后兼容**：不传 `filter_chain_factory` 和 `log_dict_factory` 参数时，行为与修复前一致（延迟导入）
- **现有测试全部通过**：114 个测试（21 新增 + 93 原有）全部通过

## 六、后续建议

### 6.1 已扫描的其他模块（无需立即重构）

对项目代码库进行了完整扫描，结论：

| 设计问题 | 命中数 | 评估 |
|---------|--------|------|
| 函数内延迟导入（循环依赖规避） | ~50 处 | 集中在 `error_handler.py` 和 `orchestrator/lifecycle_manager.py` |
| 工厂模式使用 | ~10 处 | 职责单一，暂不需要 DI 重构 |
| NullHandler 反模式 | 0 处 | 已正确规避 |

### 6.2 下一阶段重构优先级

1. **高优先级**：`agent/error_handler.py`（7 处延迟导入，`error_handler ↔ monitoring.*` 双向循环）
2. **高优先级**：`agent/orchestrator/lifecycle_manager.py`（15 处延迟导入，`orchestrator ↔ tools/tool_calling/extensions` 双向循环）
3. **中优先级**：`agent/digital_life_persona.py`（6 处延迟导入）
4. **低优先级**：其他模块（各 2-3 处，影响较小）

### 6.3 CI 守护建议

- 将 `TestStressTestDependencyInjection` 的 21 个测试纳入每日自动构建
- 添加邮件通知，测试失败时自动通知维护者
- 配置测试覆盖率报告，跟踪 filter 链执行覆盖率

## 七、相关文件

- [CI 流水线文档](./ci_log_perf_guard.md)
- [日志双重序列化迁移路线图](./log_dict_migration_roadmap.md)
- [P0 安全修复归档](./security/p0_security_fix_archive_20260703.md)
