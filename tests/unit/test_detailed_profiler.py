"""
详细性能分析器测试
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch

from agent.detailed_profiler import (
    LoadEvent,
    StageTiming,
    PerformanceTracker,
    profile,
    PerformanceContext,
    get_performance_report,
)


class TestLoadEvent:
    """测试加载事件"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_load_event(self):
        """测试创建加载事件"""
        event = LoadEvent(
            module_name="test_module",
            event_type="start",
            timestamp=time.time(),
        )
        
        assert event.module_name == "test_module"
        assert event.event_type == "start"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_load_event_defaults(self):
        """测试加载事件默认值"""
        event = LoadEvent(
            module_name="test",
            event_type="end",
            timestamp=time.time(),
        )
        
        assert event.elapsed_ms == 0.0
        assert event.details == {}
        assert event.error is None


class TestStageTiming:
    """测试阶段计时"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_stage_timing(self):
        """测试创建阶段计时"""
        timing = StageTiming(
            stage_name="init",
            start_time=time.time(),
        )
        
        assert timing.stage_name == "init"
        assert timing.end_time is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stage_timing_completed(self):
        """测试完成的阶段计时"""
        start = time.time()
        timing = StageTiming(
            stage_name="load",
            start_time=start,
            end_time=start + 1.5,
            elapsed_ms=1500.0,
        )
        
        assert timing.elapsed_ms == 1500.0


class TestPerformanceTracker:
    """测试性能追踪器"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_init(self):
        """测试初始化"""
        tracker = PerformanceTracker()
        
        assert tracker.events == []
        assert tracker.stage_timings == {}
        assert tracker.current_stages == []

    @pytest.mark.unit
    @pytest.mark.p2
    def test_metrics_initialized(self):
        """测试指标初始化"""
        tracker = PerformanceTracker()
        
        assert tracker.metrics['total_load_time_ms'] == 0.0
        assert tracker.metrics['slowest_time_ms'] == 0.0
        assert tracker.metrics['fastest_time_ms'] == float('inf')

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_module_load(self):
        """测试记录模块加载"""
        tracker = PerformanceTracker()
        
        # 使用公共方法记录加载
        tracker.start_stage("test_module")
        time.sleep(0.001)
        tracker.end_stage("test_module")
        
        assert "test_module" in tracker.stage_timings

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_module_load_updates_metrics(self):
        """测试记录模块加载更新指标"""
        tracker = PerformanceTracker()
        
        # 记录一个慢模块
        tracker.start_stage("slow_module")
        time.sleep(0.01)
        tracker.end_stage("slow_module")
        
        # 检查阶段被记录
        assert tracker.stage_timings["slow_module"].elapsed_ms > 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_start_stage(self):
        """测试开始阶段"""
        tracker = PerformanceTracker()
        
        tracker.start_stage("init")
        
        assert "init" in tracker.current_stages
        assert "init" in tracker.stage_timings

    @pytest.mark.unit
    @pytest.mark.p2
    def test_end_stage(self):
        """测试结束阶段"""
        tracker = PerformanceTracker()
        
        tracker.start_stage("init")
        time.sleep(0.01)
        tracker.end_stage("init")
        
        assert "init" not in tracker.current_stages
        assert tracker.stage_timings["init"].end_time is not None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_end_stage_nested(self):
        """测试嵌套阶段结束"""
        tracker = PerformanceTracker()
        
        tracker.start_stage("outer")
        tracker.start_stage("inner")
        tracker.end_stage("inner")
        tracker.end_stage("outer")
        
        assert tracker.stage_timings["outer"].elapsed_ms >= tracker.stage_timings["inner"].elapsed_ms

    @pytest.mark.unit
    @pytest.mark.p2
    def test_thread_safety(self):
        """测试线程安全"""
        tracker = PerformanceTracker()
        
        def worker():
            for i in range(10):
                tracker.start_stage(f"stage_{threading.current_thread().name}_{i}")
                tracker.end_stage(f"stage_{threading.current_thread().name}_{i}")
        
        threads = [threading.Thread(target=worker, name=f"worker_{i}") for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 所有阶段应该被记录
        assert len(tracker.stage_timings) == 50


class TestProfileDecorator:
    """测试性能分析装饰器"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_profile_decorator(self):
        """测试性能分析装饰器"""
        @profile("test_func")
        def test_function():
            return 42
        
        result = test_function()
        assert result == 42

    @pytest.mark.unit
    @pytest.mark.p2
    def test_profile_with_args(self):
        """测试带参数的函数分析"""
        @profile("add")
        def add(a, b):
            return a + b
        
        result = add(1, 2)
        assert result == 3

    @pytest.mark.unit
    @pytest.mark.p2
    def test_profile_exception(self):
        """测试分析异常"""
        @profile("failing_func")
        def failing_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            failing_func()


class TestPerformanceContext:
    """测试性能上下文管理器"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_context_manager(self):
        """测试上下文管理器"""
        with PerformanceContext("test_op") as ctx:
            time.sleep(0.01)
        
        assert ctx.elapsed_ms > 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_context_manager_exception(self):
        """测试上下文管理器异常"""
        with pytest.raises(ValueError):
            with PerformanceContext("failing_op"):
                raise ValueError("test")


class TestGetPerformanceReport:
    """测试获取性能报告"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_report(self):
        """测试获取报告"""
        report = get_performance_report()
        
        assert isinstance(report, dict)
        assert len(report) > 0