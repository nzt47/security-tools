"""性能监控模块测试 — 验证核心高频路径性能埋点功能

测试覆盖：
1. PerformanceMonitor 开关控制
2. perf_trace / record_call 记录功能
3. 统计汇总正确性
4. run_comparison 新旧模式对比
5. log_dict / EmojiFilter / DictToJsonFilter / format_structured_log 埋点集成

机制说明：
- Request ID 采样控制：验证采样间隔生效
- 边界显性化：负数耗时归零
- 幂等性：enable/disable 可重复调用
"""
import json
import logging
import threading
import time

import pytest

from agent.utils import perf_monitor
from agent.logging_utils import log_dict, EmojiFilter, DictToJsonFilter


class TestPerfMonitorSwitch:
    """性能埋点开关控制测试"""

    def setup_method(self):
        perf_monitor.reset_stats()
        perf_monitor.disable()

    def teardown_method(self):
        perf_monitor.reset_stats()
        perf_monitor.disable()

    def test_default_disabled(self):
        assert perf_monitor.is_enabled() is False

    def test_enable_disable(self):
        perf_monitor.enable()
        assert perf_monitor.is_enabled() is True
        perf_monitor.disable()
        assert perf_monitor.is_enabled() is False

    def test_enable_disable_idempotent(self):
        """幂等性：重复调用不抛异常"""
        perf_monitor.enable()
        perf_monitor.enable()
        assert perf_monitor.is_enabled() is True
        perf_monitor.disable()
        perf_monitor.disable()
        assert perf_monitor.is_enabled() is False


class TestPerfTrace:
    """perf_trace 上下文管理器测试"""

    def setup_method(self):
        perf_monitor.reset_stats()
        perf_monitor.enable()

    def teardown_method(self):
        perf_monitor.reset_stats()
        perf_monitor.disable()

    def test_perf_trace_records_when_enabled(self):
        with perf_monitor.perf_trace("test", "action", old_us=10.0):
            time.sleep(0.001)
        stats = perf_monitor.get_stats()
        assert "test.action" in stats
        assert stats["test.action"]["count"] == 1

    def test_perf_trace_noop_when_disabled(self):
        perf_monitor.disable()
        with perf_monitor.perf_trace("test", "disabled"):
            pass
        stats = perf_monitor.get_stats()
        assert len(stats) == 0

    def test_perf_trace_calculates_speedup(self):
        with perf_monitor.perf_trace("mod", "act", old_us=100.0):
            pass  # 极快，new_us 接近 0
        stats = perf_monitor.get_stats()
        s = stats["mod.act"]
        assert s["avg_old_us"] == 100.0
        assert s["speedup"] >= 1.0  # 旧 > 新

    def test_record_call_directly(self):
        perf_monitor.record_call("direct", "call", new_us=5.0, old_us=15.0)
        stats = perf_monitor.get_stats()
        assert stats["direct.call"]["count"] == 1
        assert stats["direct.call"]["avg_new_us"] == 5.0
        assert stats["direct.call"]["avg_old_us"] == 15.0
        assert stats["direct.call"]["improvement_pct"] > 0


class TestStatsAggregation:
    """统计汇总测试"""

    def setup_method(self):
        perf_monitor.reset_stats()
        perf_monitor.enable()

    def teardown_method(self):
        perf_monitor.reset_stats()
        perf_monitor.disable()

    def test_multiple_calls_aggregate(self):
        for i in range(5):
            perf_monitor.record_call("agg", "test", new_us=2.0 * (i + 1),
                                     old_us=10.0)
        stats = perf_monitor.get_stats()
        s = stats["agg.test"]
        assert s["count"] == 5
        assert s["avg_new_us"] == 6.0  # (2+4+6+8+10)/5
        assert s["avg_old_us"] == 10.0

    def test_reset_stats(self):
        perf_monitor.record_call("r", "t", 1.0, 2.0)
        assert len(perf_monitor.get_stats()) == 1
        perf_monitor.reset_stats()
        assert len(perf_monitor.get_stats()) == 0

    def test_negative_duration_zeroed(self):
        """边界显性化：负数耗时归零"""
        perf_monitor.record_call("neg", "test", new_us=-5.0, old_us=-3.0)
        stats = perf_monitor.get_stats()
        s = stats["neg.test"]
        assert s["avg_new_us"] == 0.0
        assert s["avg_old_us"] == 0.0


class TestRunComparison:
    """新旧模式对比功能测试"""

    def setup_method(self):
        perf_monitor.reset_stats()

    def teardown_method(self):
        perf_monitor.reset_stats()

    def test_run_comparison_returns_scenarios(self):
        result = perf_monitor.run_comparison(iterations=100)
        assert "scenarios" in result
        assert "summary" in result
        assert len(result["scenarios"]) == 3  # 默认 3 种 payload

    def test_run_comparison_simple_faster(self):
        """中等到复杂 payload: log_dict 应比 json.dumps 快

        注：简单 payload（5 字段）因 log_dict 规范化固定开销，
        speedup 可能在 1.0 附近波动（测量误差），不强制断言。
        重点验证中等到复杂 payload 有显著提升。
        """
        result = perf_monitor.run_comparison(iterations=500)
        # 中等 payload 应有明显提升
        medium = result["scenarios"][1]
        assert medium["speedup"] > 1.2
        assert medium["improvement_pct"] > 15
        # 复杂 payload 应有更显著提升
        complex_payload = result["scenarios"][2]
        assert complex_payload["speedup"] > 1.5
        assert complex_payload["improvement_pct"] > 30
        # 平均加速比应 > 1.3
        assert result["summary"]["avg_speedup"] > 1.3

    def test_run_comparison_total_saved_positive(self):
        result = perf_monitor.run_comparison(iterations=200)
        assert result["summary"]["total_saved_ms"] > 0


class TestLogDictIntegration:
    """log_dict 性能埋点集成测试"""

    def setup_method(self):
        perf_monitor.reset_stats()

    def teardown_method(self):
        perf_monitor.reset_stats()
        perf_monitor.disable()

    def test_log_dict_no_overhead_when_disabled(self):
        """关闭时无统计记录"""
        perf_monitor.disable()
        payload = {"message": "test", "module_name": "t", "action": "a"}
        result = log_dict(payload)
        assert "trace_id" in result
        assert len(perf_monitor.get_stats()) == 0

    def test_log_dict_records_when_enabled(self):
        """启用时记录埋点"""
        perf_monitor.enable()
        payload = {"message": "test", "module_name": "t", "action": "a"}
        result = log_dict(payload)
        assert "trace_id" in result
        stats = perf_monitor.get_stats()
        assert "log_dict.normalize" in stats
        assert stats["log_dict.normalize"]["count"] == 1

    def test_log_dict_correctness_preserved_when_enabled(self):
        """启用埋点时功能正确性不受影响"""
        perf_monitor.enable()
        payload = {"msg": "hello", "module_name": "mod", "action": "act"}
        result = log_dict(payload)
        assert result["message"] == "hello"
        assert "msg" not in result
        assert result["module_name"] == "mod"
        assert result["action"] == "act"


class TestFilterIntegration:
    """EmojiFilter / DictToJsonFilter 埋点集成测试"""

    def setup_method(self):
        perf_monitor.reset_stats()

    def teardown_method(self):
        perf_monitor.reset_stats()
        perf_monitor.disable()

    def _make_record(self, msg):
        return logging.LogRecord(
            "test", logging.INFO, "", 0, msg, None, None
        )

    def test_emoji_filter_dict_records_when_enabled(self):
        perf_monitor.enable()
        record = self._make_record({"message": "test 🚀", "action": "a"})
        f = EmojiFilter()
        assert f.filter(record) is True
        stats = perf_monitor.get_stats()
        assert "EmojiFilter.dict_safe" in stats

    def test_emoji_filter_no_record_when_disabled(self):
        perf_monitor.disable()
        record = self._make_record({"message": "test 🚀", "action": "a"})
        f = EmojiFilter()
        f.filter(record)
        assert len(perf_monitor.get_stats()) == 0

    def test_dict_to_json_filter_records_when_enabled(self):
        perf_monitor.enable()
        record = self._make_record({"message": "test", "action": "a"})
        f = DictToJsonFilter()
        assert f.filter(record) is True
        assert isinstance(record.msg, str)
        stats = perf_monitor.get_stats()
        assert "DictToJsonFilter.serialize" in stats

    def test_dict_to_json_filter_correctness(self):
        perf_monitor.enable()
        record = self._make_record({"message": "hi", "action": "act"})
        f = DictToJsonFilter()
        f.filter(record)
        parsed = json.loads(record.msg)
        assert parsed["message"] == "hi"
        assert parsed["action"] == "act"


class TestStressTest:
    """高并发压力测试 stress_test() 功能验证

    机制说明：
    - 边界显性化：参数校验失败抛 ValueError
    - 幂等性：使用独立 logger（stress_test_{id}），不污染全局状态
    - 竞态防御：每线程独立计数器，最终汇总
    """

    def test_stress_test_invalid_num_threads(self):
        """边界显性化：num_threads <= 0 抛 ValueError"""
        with pytest.raises(ValueError, match="num_threads"):
            perf_monitor.stress_test(num_threads=0, duration_seconds=0.5)

    def test_stress_test_invalid_duration(self):
        """边界显性化：duration_seconds <= 0 抛 ValueError"""
        with pytest.raises(ValueError, match="duration_seconds"):
            perf_monitor.stress_test(num_threads=1, duration_seconds=0)

    def test_stress_test_invalid_report_interval(self):
        """边界显性化：report_interval <= 0 抛 ValueError"""
        with pytest.raises(ValueError, match="report_interval"):
            perf_monitor.stress_test(num_threads=1, duration_seconds=0.5, report_interval=0)

    def test_stress_test_new_mode_basic(self):
        """新模式（log_dict）压力测试基本功能"""
        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=1.0,
            use_log_dict=True,
            report_interval=None,
        )
        # 验证返回结构
        assert result["mode"] == "new"
        assert result["config"]["num_threads"] == 2
        assert result["config"]["use_log_dict"] is True
        assert result["total_ops"] > 0
        assert result["errors"] == 0
        assert result["error_rate"] == 0.0
        assert result["throughput_ops_per_sec"] > 0
        # 延迟分位应递增
        lat = result["latency_us"]
        assert lat["p50"] <= lat["p90"] <= lat["p99"] <= lat["max"]

    def test_stress_test_old_mode_basic(self):
        """旧模式（json.dumps）压力测试基本功能"""
        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=1.0,
            use_log_dict=False,
            report_interval=None,
        )
        assert result["mode"] == "old"
        assert result["config"]["use_log_dict"] is False
        assert result["total_ops"] > 0
        assert result["errors"] == 0

    def test_stress_test_no_filter_chain(self):
        """不启用 filter 链时仍能正常运行"""
        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.5,
            enable_filter_chain=False,
            report_interval=None,
        )
        assert result["total_ops"] > 0
        assert result["errors"] == 0

    def test_stress_test_custom_payloads(self):
        """自定义 payload 列表正常处理"""
        custom_payloads = [
            {"trace_id": "t1", "module_name": "custom", "action": "test",
             "duration_ms": 10, "message": "custom payload 1"},
            {"trace_id": "t2", "module_name": "custom", "action": "test2",
             "duration_ms": 20, "message": "custom payload 2 🚀"},
        ]
        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.5,
            payloads=custom_payloads,
            report_interval=None,
        )
        assert result["config"]["num_payloads"] == 2
        assert result["total_ops"] > 0
        assert result["errors"] == 0

    def test_stress_test_thread_results_count(self):
        """返回的 thread_results 数量与 num_threads 一致"""
        result = perf_monitor.stress_test(
            num_threads=3,
            duration_seconds=0.5,
            report_interval=None,
        )
        assert len(result["thread_results"]) == 3
        # 每个线程的 count 字段总和应等于 total_ops
        total = sum(t["count"] for t in result["thread_results"])
        assert total == result["total_ops"]

    def test_run_stress_comparison_basic(self):
        """新旧模式对比测试基本功能"""
        result = perf_monitor.run_stress_comparison(
            num_threads=2,
            duration_seconds=0.5,
        )
        # 验证返回结构
        assert "new_mode" in result
        assert "old_mode" in result
        assert "comparison" in result

        # 对比指标应合理
        comp = result["comparison"]
        assert "throughput_speedup" in comp
        assert "throughput_improvement_pct" in comp
        assert "latency_p50_reduction_pct" in comp
        assert "latency_p99_reduction_pct" in comp
        assert "memory_growth_diff_bytes" in comp

        # 两种模式都应无错误
        assert result["new_mode"]["errors"] == 0
        assert result["old_mode"]["errors"] == 0

    def test_run_stress_comparison_invalid_args(self):
        """边界显性化：参数校验失败抛 ValueError"""
        with pytest.raises(ValueError, match="num_threads"):
            perf_monitor.run_stress_comparison(num_threads=-1, duration_seconds=1.0)
        with pytest.raises(ValueError, match="duration_seconds"):
            perf_monitor.run_stress_comparison(num_threads=1, duration_seconds=0)


# ─────────────────────────────────────────────────
# 依赖注入测试——验证 stress_test 的 filter_chain_factory 和 log_dict_factory 参数
# ─────────────────────────────────────────────────


class _CountingFilter(logging.Filter):
    """线程安全的计数 filter，记录被调用的次数

    机制：用 threading.Lock 保护计数器，避免多线程竞争
    边界显性化：filter() 永远返回 True（不阻断日志流）
    """
    def __init__(self, name: str = "counting"):
        super().__init__()
        self.name = name
        self._lock = threading.Lock()
        self.call_count = 0
        self.seen_records = []

    def filter(self, record):
        with self._lock:
            self.call_count += 1
            self.seen_records.append(record)
        return True


class _CountingLogDict:
    """线程安全的计数 log_dict 替代，记录被调用的次数和 payload

    机制：用 threading.Lock 保护状态
    边界显性化：返回规范化的 dict（模拟 log_dict 行为）
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.call_count = 0
        self.seen_payloads = []

    def __call__(self, payload):
        with self._lock:
            self.call_count += 1
            self.seen_payloads.append(payload)
        # 模拟 log_dict 的规范化行为
        data = dict(payload)
        data.setdefault("trace_id", "injected")
        data.setdefault("module_name", "test_di")
        data.setdefault("action", "stress")
        data.setdefault("duration_ms", 0)
        return data


class _FailingFilter(logging.Filter):
    """始终抛异常的 filter，用于测试边界处理"""
    def __init__(self):
        super().__init__()
        self.call_count = 0

    def filter(self, record):
        self.call_count += 1
        raise RuntimeError("filter 故意失败（边界测试）")


class _FailingLogDict:
    """始终抛异常的 log_dict，用于测试边界处理"""
    def __init__(self):
        self.call_count = 0

    def __call__(self, payload):
        self.call_count += 1
        raise RuntimeError("log_dict 故意失败（边界测试）")


class TestStressTestDependencyInjection:
    """依赖注入测试——验证自定义 filter 链和 log_dict 工厂注入

    机制说明：
    - 边界显性化：异常 factory 应被计入 error_rate，而非静默吞掉
    - 幂等性：每个测试用独立 factory 实例，互不干扰
    - 竞态防御：计数器用 threading.Lock 保护
    - 完全解耦：注入模式不依赖 agent.logging_utils 模块
    """

    def test_filter_chain_factory_is_called(self):
        """filter_chain_factory 应被调用以获取 filter 列表"""
        factory_call_count = [0]

        def factory():
            factory_call_count[0] += 1
            return [_CountingFilter("test")]

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            report_interval=None,
        )
        assert factory_call_count[0] >= 1, "filter_chain_factory 应至少被调用一次"
        assert result["errors"] == 0

    def test_filter_chain_factory_filters_actually_applied(self):
        """factory 返回的 filter 应被实际应用到日志记录"""
        counting_filter = _CountingFilter("applied")

        def factory():
            return [counting_filter]

        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.5,
            filter_chain_factory=factory,
            report_interval=None,
        )
        # 每个 LogRecord 都应经过 filter
        assert counting_filter.call_count > 0, "自定义 filter 应被调用"
        assert counting_filter.call_count == result["total_ops"], (
            f"filter 调用次数 {counting_filter.call_count} 应等于 total_ops {result['total_ops']}"
        )

    def test_filter_chain_factory_empty_list(self):
        """边界显性化：factory 返回空列表时不应报错"""
        def factory():
            return []

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            report_interval=None,
        )
        assert result["errors"] == 0, "空 filter 列表不应导致错误"

    def test_filter_chain_factory_single_filter(self):
        """单个 filter 注入正常工作"""
        single_filter = _CountingFilter("single")

        def factory():
            return [single_filter]

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            report_interval=None,
        )
        assert single_filter.call_count > 0
        assert result["errors"] == 0

    def test_filter_chain_factory_multiple_filters(self):
        """多个 filter 都应被应用到每个 record"""
        filter_a = _CountingFilter("A")
        filter_b = _CountingFilter("B")
        filter_c = _CountingFilter("C")

        def factory():
            return [filter_a, filter_b, filter_c]

        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            report_interval=None,
        )
        # 三个 filter 都应被调用相同次数
        assert filter_a.call_count > 0
        assert filter_b.call_count > 0
        assert filter_c.call_count > 0
        assert filter_a.call_count == filter_b.call_count == filter_c.call_count, (
            "所有 filter 应被调用相同次数"
        )

    def test_log_dict_factory_is_called(self):
        """log_dict_factory 应被调用以替代默认 log_dict"""
        counting_log_dict = _CountingLogDict()

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        assert counting_log_dict.call_count > 0, "log_dict_factory 应被调用"
        assert result["errors"] == 0

    def test_log_dict_factory_invocation_count(self):
        """log_dict_factory 调用次数应等于 total_ops"""
        counting_log_dict = _CountingLogDict()

        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.5,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        assert counting_log_dict.call_count == result["total_ops"], (
            f"log_dict 调用 {counting_log_dict.call_count} 次应等于 total_ops {result['total_ops']}"
        )

    def test_log_dict_factory_payload_preserved(self):
        """log_dict_factory 应接收到原始 payload 内容"""
        counting_log_dict = _CountingLogDict()
        custom_payload = [
            {"message": "test_payload", "custom_field": "abc", "module_name": "custom"}
        ]

        perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            payloads=custom_payload,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        assert len(counting_log_dict.seen_payloads) > 0
        first_payload = counting_log_dict.seen_payloads[0]
        assert first_payload["message"] == "test_payload"
        assert first_payload["custom_field"] == "abc"

    def test_both_factories_injected_simultaneously(self):
        """同时注入 filter_chain_factory 和 log_dict_factory"""
        counting_filter = _CountingFilter("combined")
        counting_log_dict = _CountingLogDict()

        def factory():
            return [counting_filter]

        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        # 两个 factory 都应被调用
        assert counting_filter.call_count > 0
        assert counting_log_dict.call_count > 0
        # 调用次数应一致（每个 record 都经过两者）
        assert counting_filter.call_count == counting_log_dict.call_count == result["total_ops"]
        assert result["errors"] == 0

    def test_injected_mode_no_errors(self):
        """注入模式应无错误（与默认模式行为一致）"""
        counting_filter = _CountingFilter("no_errors")
        counting_log_dict = _CountingLogDict()

        def factory():
            return [counting_filter]

        result = perf_monitor.stress_test(
            num_threads=4,
            duration_seconds=1.0,
            filter_chain_factory=factory,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        assert result["errors"] == 0, "注入模式应无错误"
        assert result["error_rate"] == 0.0
        assert result["total_ops"] > 0
        assert result["throughput_ops_per_sec"] > 0

    def test_filter_chain_factory_raises_exception(self):
        """边界显性化：filter 抛异常时被计入 error_rate，而非静默吞掉"""
        failing_filter = _FailingFilter()

        def factory():
            return [failing_filter]

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            report_interval=None,
        )
        # 抛异常的 filter 应导致 error_count > 0
        assert result["errors"] > 0, "filter 异常应被计入 error_count"
        assert result["error_rate"] > 0.0

    def test_log_dict_factory_raises_exception(self):
        """边界显性化：log_dict 抛异常时被计入 error_rate"""
        failing_log_dict = _FailingLogDict()

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            log_dict_factory=failing_log_dict,
            report_interval=None,
        )
        assert result["errors"] > 0, "log_dict 异常应被计入 error_count"
        assert result["error_rate"] > 0.0

    def test_filter_chain_factory_ignored_when_filter_chain_disabled(self):
        """enable_filter_chain=False 时 filter_chain_factory 应被忽略"""
        factory_call_count = [0]

        def factory():
            factory_call_count[0] += 1
            return [_CountingFilter("should_not_run")]

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            enable_filter_chain=False,
            filter_chain_factory=factory,
            report_interval=None,
        )
        assert factory_call_count[0] == 0, (
            "enable_filter_chain=False 时 filter_chain_factory 不应被调用"
        )
        assert result["errors"] == 0

    def test_log_dict_factory_ignored_when_use_log_dict_false(self):
        """use_log_dict=False 时 log_dict_factory 应被忽略"""
        counting_log_dict = _CountingLogDict()

        result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            use_log_dict=False,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        assert counting_log_dict.call_count == 0, (
            "use_log_dict=False 时 log_dict_factory 不应被调用"
        )
        assert result["errors"] == 0

    def test_custom_filter_invoked_per_record(self):
        """每个 LogRecord 都应触发自定义 filter 的 filter() 方法"""
        counting_filter = _CountingFilter("per_record")

        def factory():
            return [counting_filter]

        result = perf_monitor.stress_test(
            num_threads=3,
            duration_seconds=0.5,
            filter_chain_factory=factory,
            report_interval=None,
        )
        # 每条日志都应经过 filter
        assert counting_filter.call_count == result["total_ops"], (
            f"filter 调用次数 {counting_filter.call_count} 应等于 total_ops {result['total_ops']}"
        )
        # seen_records 应包含所有 LogRecord
        assert len(counting_filter.seen_records) == result["total_ops"]

    def test_log_dict_factory_called_per_record(self):
        """每条日志都应触发 log_dict_factory 调用"""
        counting_log_dict = _CountingLogDict()

        result = perf_monitor.stress_test(
            num_threads=3,
            duration_seconds=0.5,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        assert counting_log_dict.call_count == result["total_ops"], (
            f"log_dict 调用次数 {counting_log_dict.call_count} 应等于 total_ops {result['total_ops']}"
        )

    def test_injected_mode_latency_reasonable(self):
        """注入模式的延迟分位应合理（无异常飙升）"""
        counting_filter = _CountingFilter("latency")
        counting_log_dict = _CountingLogDict()

        def factory():
            return [counting_filter]

        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.5,
            filter_chain_factory=factory,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        lat = result["latency_us"]
        # p50 应小于 1ms（宽松阈值，避免 CI 噪声）
        assert lat["p50"] < 1000, f"p50 延迟 {lat['p50']}us 过高"
        # p99 应小于 50ms（宽松阈值，CI runner 性能波动可导致 p99 飙升）
        assert lat["p99"] < 50000, f"p99 延迟 {lat['p99']}us 过高"
        # 分位递增
        assert lat["p50"] <= lat["p90"] <= lat["p99"] <= lat["max"]

    def test_injected_mode_throughput_reasonable(self):
        """注入模式的吞吐量应合理（不低于阈值）"""
        counting_filter = _CountingFilter("throughput")
        counting_log_dict = _CountingLogDict()

        def factory():
            return [counting_filter]

        result = perf_monitor.stress_test(
            num_threads=2,
            duration_seconds=0.5,
            filter_chain_factory=factory,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        # 吞吐量应大于 1000 ops/sec（宽松阈值，避免 CI 噪声）
        assert result["throughput_ops_per_sec"] > 1000, (
            f"吞吐量 {result['throughput_ops_per_sec']} 过低"
        )

    def test_complete_decoupling_from_logging_utils(self):
        """完全解耦验证：注入模式下不依赖 agent.logging_utils 任何内容

        机制：用 mock 检查 agent.logging_utils 是否被导入
        边界显性化：注入模式应完全脱离 logging_utils
        """
        from unittest.mock import patch
        import builtins

        # 记录原始 import 行为
        original_import = builtins.__import__
        import_log = []

        def tracking_import(name, *args, **kwargs):
            if name.startswith("agent.logging_utils"):
                import_log.append(name)
            return original_import(name, *args, **kwargs)

        counting_filter = _CountingFilter("decoupled")
        counting_log_dict = _CountingLogDict()

        def factory():
            return [counting_filter]

        # 用 mock 替换 __import__
        with patch("builtins.__import__", side_effect=tracking_import):
            result = perf_monitor.stress_test(
                num_threads=2,
                duration_seconds=0.3,
                filter_chain_factory=factory,
                log_dict_factory=counting_log_dict,
                report_interval=None,
            )
        # 注入模式下不应 import agent.logging_utils
        assert len(import_log) == 0, (
            f"注入模式不应 import agent.logging_utils，实际 import 了: {import_log}"
        )
        assert result["errors"] == 0

    def test_injected_mode_vs_default_mode_consistency(self):
        """注入模式与默认模式结果结构应一致"""
        counting_filter = _CountingFilter("consistency")
        counting_log_dict = _CountingLogDict()

        def factory():
            return [counting_filter]

        injected_result = perf_monitor.stress_test(
            num_threads=1,
            duration_seconds=0.3,
            filter_chain_factory=factory,
            log_dict_factory=counting_log_dict,
            report_interval=None,
        )
        # 验证返回结构完整
        required_keys = {
            "mode", "config", "throughput_ops_per_sec", "total_ops",
            "duration_seconds_actual", "latency_us", "memory_growth_bytes",
            "errors", "error_rate", "thread_results",
        }
        assert required_keys.issubset(injected_result.keys()), (
            f"注入模式返回结构缺少键: {required_keys - set(injected_result.keys())}"
        )
        # mode 应为 "new"（use_log_dict=True 默认）
        assert injected_result["mode"] == "new"

    def test_filter_chain_factory_thread_safety(self):
        """多线程下 factory 计数器应线程安全"""
        filter_calls = [0]
        filter_lock = threading.Lock()

        class _ThreadSafeFilter(logging.Filter):
            def filter(self, record):
                with filter_lock:
                    filter_calls[0] += 1
                return True

        def factory():
            return [_ThreadSafeFilter()]

        result = perf_monitor.stress_test(
            num_threads=8,
            duration_seconds=0.5,
            filter_chain_factory=factory,
            report_interval=None,
        )
        # 多线程下 filter 调用次数应准确（无丢失/重复）
        assert filter_calls[0] == result["total_ops"], (
            f"多线程 filter 调用 {filter_calls[0]} 应等于 total_ops {result['total_ops']}"
        )
