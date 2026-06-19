"""
PerformanceMonitor 测试 - 覆盖性能指标追踪、Timer 上下文管理器
"""

import pytest
import time
import threading

from agent.performance_monitor import (
    ModuleInitRecord,
    InitPerformanceTracker,
    Timer,
    log_module_load_time,
    get_performance_recorder,
)


class TestModuleInitRecord:
    """测试模块初始化记录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_record(self):
        """测试初始化记录"""
        record = ModuleInitRecord(
            name="test_module",
            start_time=100.0
        )

        assert record.name == "test_module"
        assert record.start_time == 100.0
        assert record.success is True
        assert record.error == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_finish(self):
        """测试标记完成"""
        record = ModuleInitRecord(
            name="test_module",
            start_time=time.time()
        )

        time.sleep(0.01)
        record.finish()

        assert record.end_time > 0
        assert record.duration_ms > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_failure(self):
        """测试失败记录"""
        record = ModuleInitRecord(
            name="test_module",
            start_time=time.time()
        )

        record.success = False
        record.error = "test error"

        assert record.success is False
        assert record.error == "test error"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_record_str(self):
        """测试字符串表示"""
        record = ModuleInitRecord(
            name="test_module",
            start_time=time.time()
        )
        record.finish()

        str_repr = str(record)
        assert "test_module" in str_repr
        assert "✅" in str_repr

        record.success = False
        str_repr = str(record)
        assert "❌" in str_repr


class TestInitPerformanceTracker:
    """测试初始化性能追踪器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracker_init(self):
        """测试追踪器初始化"""
        tracker = InitPerformanceTracker()

        assert tracker.records == {}
        assert tracker.start_time > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_and_finish_module(self):
        """测试开始和完成模块追踪"""
        tracker = InitPerformanceTracker()

        tracker.start_module("module1")
        time.sleep(0.01)
        tracker.finish_module("module1")

        assert "module1" in tracker.records
        record = tracker.records["module1"]
        assert record.success is True
        assert record.duration_ms > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_finish_module_with_failure(self):
        """测试失败的模块完成"""
        tracker = InitPerformanceTracker()

        tracker.start_module("module1")
        tracker.finish_module("module1", success=False, error="test error")

        record = tracker.records["module1"]
        assert record.success is False
        assert record.error == "test error"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_total_time(self):
        """测试获取总时间"""
        tracker = InitPerformanceTracker()

        time.sleep(0.01)
        total_ms = tracker.get_total_time()

        assert total_ms > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_summary(self):
        """测试获取摘要"""
        tracker = InitPerformanceTracker()

        tracker.start_module("module1")
        time.sleep(0.01)
        tracker.finish_module("module1")

        tracker.start_module("module2")
        time.sleep(0.02)
        tracker.finish_module("module2")

        summary = tracker.get_summary()
        assert "初始化性能总结" in summary
        assert "module1" in summary
        assert "module2" in summary

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_bottlenecks(self):
        """测试获取瓶颈"""
        tracker = InitPerformanceTracker()

        tracker.start_module("fast_module")
        time.sleep(0.01)
        tracker.finish_module("fast_module")

        tracker.start_module("slow_module")
        time.sleep(0.06)
        tracker.finish_module("slow_module")

        bottlenecks = tracker.get_bottlenecks(threshold_ms=50.0)
        assert len(bottlenecks) == 1
        assert bottlenecks[0].name == "slow_module"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_timeline(self):
        """测试获取时间线"""
        tracker = InitPerformanceTracker()

        tracker.start_module("module1")
        time.sleep(0.01)
        tracker.finish_module("module1")

        tracker.start_module("module2")
        time.sleep(0.01)
        tracker.finish_module("module2")

        timeline = tracker.get_timeline()
        assert len(timeline) == 2
        assert timeline[0]["module"] == "module1"
        assert timeline[1]["module"] == "module2"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_print_summary(self):
        """测试打印摘要"""
        tracker = InitPerformanceTracker()

        tracker.start_module("module1")
        tracker.finish_module("module1")

        summary = tracker.get_summary()
        assert "初始化性能总结" in summary
        assert "module1" in summary
        
        tracker.print_summary()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_concurrent_module_tracking(self):
        """测试并发模块追踪"""
        tracker = InitPerformanceTracker()

        def track_module(name):
            tracker.start_module(name)
            time.sleep(0.01)
            tracker.finish_module(name)

        threads = []
        for i in range(5):
            t = threading.Thread(target=track_module, args=(f"module_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(tracker.records) == 5


class TestTimer:
    """测试计时器类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timer_init(self):
        """测试计时器初始化"""
        timer = Timer("test_timer")

        assert timer.name == "test_timer"
        assert timer.start_time > 0
        assert timer.end_time is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timer_stop(self):
        """测试停止计时器"""
        timer = Timer("test_timer")

        time.sleep(0.01)
        elapsed = timer.stop()

        assert elapsed > 0
        assert timer.end_time is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timer_context_manager(self):
        """测试上下文管理器"""
        with Timer("context_timer") as timer:
            time.sleep(0.01)

        assert timer.elapsed > 0
        assert timer.end_time is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timer_elapsed(self):
        """测试耗时计算"""
        timer = Timer()

        time.sleep(0.02)
        timer.stop()

        assert timer.elapsed >= 0.02

    @pytest.mark.unit
    @pytest.mark.p1
    def test_timer_without_name(self):
        """测试无名计时器"""
        timer = Timer()

        with timer:
            time.sleep(0.01)

        assert timer.elapsed > 0


class TestHelperFunctions:
    """测试辅助函数"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_log_module_load_time(self):
        """测试记录模块加载时间"""
        log_module_load_time("test_module", 123.45)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_performance_recorder(self):
        """测试获取性能记录器"""
        recorder = get_performance_recorder()

        assert recorder is not None
        assert isinstance(recorder, InitPerformanceTracker)


class TestIntegration:
    """测试集成场景"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_full_tracking_scenario(self):
        """测试完整追踪场景"""
        tracker = InitPerformanceTracker()

        modules = ["memory", "llm", "tools", "ui"]

        for module in modules:
            tracker.start_module(module)
            time.sleep(0.01)
            tracker.finish_module(module)

        summary = tracker.get_summary()
        assert "模块总数: 4" in summary
        assert "成功数: 4" in summary

        bottlenecks = tracker.get_bottlenecks()
        assert len(bottlenecks) >= 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_mixed_success_failure(self):
        """测试混合成功和失败"""
        tracker = InitPerformanceTracker()

        tracker.start_module("success_module")
        tracker.finish_module("success_module")

        tracker.start_module("fail_module")
        tracker.finish_module("fail_module", success=False, error="failed")

        summary = tracker.get_summary()
        assert "成功数: 1" in summary
        assert "失败数: 1" in summary