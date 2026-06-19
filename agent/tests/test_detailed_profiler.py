"""DetailedProfiler 单元测试"""
import pytest
from agent.detailed_profiler import (
    PerformanceTracker,
    profile,
    log_module_load,
    PerformanceContext,
    StageTimer,
    get_tracker,
    get_performance_report,
    print_performance_report
)


def test_tracker_initial_state():
    """追踪器初始状态"""
    tracker = PerformanceTracker()
    assert len(tracker.events) == 0
    assert tracker.metrics['total_modules'] == 0


def test_record_load_start():
    """记录加载开始"""
    tracker = PerformanceTracker()
    tracker.record_load_start("test_module")
    
    assert len(tracker.events) == 1
    assert tracker.events[0].event_type == 'start'
    assert tracker.events[0].module_name == "test_module"


def test_record_load_end():
    """记录加载结束"""
    tracker = PerformanceTracker()
    tracker.record_load_start("test_module")
    tracker.record_load_end("test_module", 100.5)
    
    assert len(tracker.events) == 2
    assert tracker.events[1].event_type == 'end'
    assert tracker.events[1].elapsed_ms == 100.5


def test_record_load_error():
    """记录加载错误"""
    tracker = PerformanceTracker()
    tracker.record_load_error("test_module", ValueError("failed"))
    
    assert len(tracker.events) == 1
    assert tracker.events[0].event_type == 'error'
    assert tracker.events[0].error == "failed"


def test_start_stage():
    """开始阶段"""
    tracker = PerformanceTracker()
    tracker.start_stage("test_stage")
    
    assert "test_stage" in tracker.stage_timings


def test_end_stage():
    """结束阶段"""
    tracker = PerformanceTracker()
    tracker.start_stage("test_stage")
    tracker.end_stage("test_stage")
    
    timing = tracker.stage_timings["test_stage"]
    assert timing.end_time is not None
    assert timing.elapsed_ms > 0


def test_end_stage_not_exists():
    """结束不存在的阶段"""
    tracker = PerformanceTracker()
    tracker.end_stage("nonexistent")  # 不应抛出异常


def test_get_report():
    """获取性能报告"""
    tracker = PerformanceTracker()
    tracker.record_load_start("mod1")
    tracker.record_load_end("mod1", 100.0)
    tracker.record_load_start("mod2")
    tracker.record_load_end("mod2", 200.0)
    
    report = tracker.get_report()
    
    assert report["summary"]["total_load_time_ms"] == 300.0
    assert report["summary"]["total_modules"] == 2
    assert "mod1" in report["module_stats"]


def test_reset():
    """重置追踪器"""
    tracker = PerformanceTracker()
    tracker.record_load_start("mod1")
    tracker.record_load_end("mod1", 100.0)
    
    tracker.reset()
    
    assert len(tracker.events) == 0
    assert tracker.metrics['total_modules'] == 0


def test_profile_decorator():
    """性能分析装饰器"""
    tracker = get_tracker()
    tracker.reset()
    
    @profile("test_func")
    def test_function():
        return "result"
    
    result = test_function()
    
    assert result == "result"
    assert len(tracker.events) >= 2  # start and end


def test_log_module_load_decorator():
    """模块加载日志装饰器"""
    tracker = get_tracker()
    tracker.reset()
    
    @log_module_load("test_module")
    def load_module():
        return {"loaded": True}
    
    result = load_module()
    
    assert result["loaded"] is True
    assert len(tracker.events) >= 2


def test_log_module_load_decorator_with_error():
    """模块加载日志装饰器（带错误）"""
    tracker = get_tracker()
    tracker.reset()
    
    @log_module_load("failing_module")
    def load_failing_module():
        raise ValueError("load failed")
    
    with pytest.raises(ValueError):
        load_failing_module()
    
    assert len(tracker.events) >= 1
    assert any(e.event_type == 'error' for e in tracker.events)


def test_performance_context():
    """性能上下文管理器"""
    tracker = get_tracker()
    tracker.reset()
    
    with PerformanceContext("test_context") as ctx:
        pass
    
    assert ctx.elapsed_ms >= 0
    assert len(tracker.events) >= 2


def test_performance_context_with_exception():
    """性能上下文管理器（带异常）"""
    tracker = get_tracker()
    tracker.reset()
    
    try:
        with PerformanceContext("error_context"):
            raise ValueError("test error")
    except ValueError:
        pass
    
    assert len(tracker.events) >= 2
    assert any(e.event_type == 'error' for e in tracker.events)


def test_stage_timer():
    """阶段计时器"""
    tracker = get_tracker()
    tracker.reset()
    
    with StageTimer("test_stage"):
        pass
    
    assert "test_stage" in tracker.stage_timings


def test_global_tracker():
    """全局追踪器实例"""
    tracker1 = get_tracker()
    tracker2 = get_tracker()
    
    assert tracker1 is tracker2


def test_get_performance_report():
    """获取性能报告（快捷函数）"""
    report = get_performance_report()
    assert isinstance(report, dict)


def test_metrics_update():
    """指标更新"""
    tracker = PerformanceTracker()
    
    tracker.record_load_end("slow_mod", 500.0)
    tracker.record_load_end("fast_mod", 50.0)
    
    assert tracker.metrics["slowest_module"] == "slow_mod"
    assert tracker.metrics["slowest_time_ms"] == 500.0
    assert tracker.metrics["fastest_module"] == "fast_mod"
    assert tracker.metrics["fastest_time_ms"] == 50.0


def test_module_stats():
    """模块统计"""
    tracker = PerformanceTracker()
    
    tracker.record_load_end("mod1", 100.0)
    tracker.record_load_end("mod1", 150.0)
    tracker.record_load_end("mod2", 200.0)
    
    report = tracker.get_report()
    
    assert report["module_stats"]["mod1"]["count"] == 2
    assert report["module_stats"]["mod1"]["avg_ms"] == 125.0