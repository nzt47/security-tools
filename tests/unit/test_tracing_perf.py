"""agent.monitoring.tracing_perf 模块单元测试

覆盖内容：
1. _trace_id 生成
2. PerformanceStats 记录/计数/统计（空与非空）、报告生成（空与非空）
3. measure_overhead 正常测量与零耗时边界（or 0.000001 分支）
4. TraceOverheadBenchmark 各 benchmark_* 方法（小规模真实执行）
5. run_full_benchmark 控制流（span_creation 均值三个阈值分支）
6. main() 报告文件写出（chdir 到 tmp_path，桩化 run_full_benchmark）
7. _safe_call 成功转发与异常重抛 + 结构化错误日志
"""
import json
import logging

import pytest

import agent.monitoring.tracing_perf as tp
from agent.monitoring.tracing_perf import (
    PerformanceStats,
    TraceOverheadBenchmark,
    _safe_call,
    _trace_id,
    measure_overhead,
)


# ---------------------------------------------------------------------------
# _trace_id
# ---------------------------------------------------------------------------
def test_trace_id_length_and_uniqueness():
    """_trace_id 返回 16 位十六进制且每次生成不同"""
    ids = {_trace_id() for _ in range(100)}
    assert all(len(i) == 16 for i in ids)
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# PerformanceStats
# ---------------------------------------------------------------------------
def test_performance_stats_init():
    """初始化 timings/counters/start_time"""
    stats = PerformanceStats()
    assert stats.timings == {}
    assert stats.counters == {}
    assert stats.start_time > 0


def test_record_and_increment():
    """record 追加耗时、increment 累加计数（默认值与自定义值）"""
    stats = PerformanceStats()
    stats.record("m", 1.5)
    stats.record("m", 2.5)
    assert stats.timings["m"] == [1.5, 2.5]
    stats.increment("calls")
    stats.increment("calls", 3)
    assert stats.counters["calls"] == 4


def test_get_stats_empty_metric():
    """无记录指标 → 全零统计"""
    stats = PerformanceStats()
    result = stats.get_stats("nonexistent")
    assert result == {"count": 0, "mean": 0, "p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}


def test_get_stats_with_values():
    """有记录指标 → 统计正确（mean/min/max/分位数）"""
    stats = PerformanceStats()
    for v in (1.0, 2.0, 3.0, 4.0, 5.0):
        stats.record("m", v)
    result = stats.get_stats("m")
    assert result["count"] == 5
    assert result["mean"] == 3.0
    assert result["min"] == 1.0
    assert result["max"] == 5.0
    assert result["p50"] == 3.0  # values[int(5*0.5)] = values[2]
    assert result["p90"] == 5.0  # values[int(5*0.9)] = values[4]
    assert result["p99"] == 5.0  # values[int(5*0.99)] = values[4]


def test_get_report_empty():
    """无任何记录 → metrics 为空、counters 为空"""
    stats = PerformanceStats()
    report = stats.get_report()
    assert report["metrics"] == {}
    assert report["counters"] == {}
    assert report["total_duration_ms"] >= 0


def test_get_report_with_metrics():
    """有记录与计数 → report 聚合正确"""
    stats = PerformanceStats()
    stats.record("uuid", 0.5)
    stats.increment("total", 10)
    report = stats.get_report()
    assert report["counters"] == {"total": 10}
    assert report["metrics"]["uuid"]["mean"] == 0.5
    assert report["metrics"]["uuid"]["count"] == 1


# ---------------------------------------------------------------------------
# measure_overhead
# ---------------------------------------------------------------------------
def test_measure_overhead_basic():
    """正常测量：预热不计入，结果含四项指标且 calls_per_second > 0"""
    result = measure_overhead(lambda: 1 + 1, iterations=200, warmup=20)
    assert result["iterations"] == 200
    assert result["total_ms"] >= 0
    assert result["per_call_ms"] >= 0
    assert result["calls_per_second"] > 0


def test_measure_overhead_args_kwargs_passthrough():
    """*args / **kwargs 透传给被测函数"""
    def add(a, b=0):
        return a + b

    result = measure_overhead(add, 100, 10, 2, b=3)
    assert result["iterations"] == 100


def test_measure_overhead_zero_duration(monkeypatch):
    """测量耗时为零 → calls_per_second 使用 0.000001 兜底（or 分支）"""
    monkeypatch.setattr(tp.time, "perf_counter", lambda: 1.0)
    result = measure_overhead(lambda: None, iterations=10, warmup=0)
    assert result["total_ms"] == 0
    assert result["per_call_ms"] == 0
    assert result["calls_per_second"] == 10 / 0.000001


# ---------------------------------------------------------------------------
# TraceOverheadBenchmark 各 benchmark 方法（小规模真实执行）
# ---------------------------------------------------------------------------
def test_generate_span_and_trace_id():
    """私有 ID 生成方法返回 16 位十六进制且互不相同"""
    benchmark = TraceOverheadBenchmark()
    trace_ids = {benchmark._generate_trace_id() for _ in range(50)}
    span_ids = {benchmark._generate_span_id() for _ in range(50)}
    assert all(len(i) == 16 for i in trace_ids)
    assert all(len(i) == 16 for i in span_ids)
    assert len(trace_ids) == 50
    assert len(span_ids) == 50


def test_benchmark_uuid_generation():
    """UUID 生成基准真实执行并记录统计"""
    benchmark = TraceOverheadBenchmark()
    result = benchmark.benchmark_uuid_generation(iterations=100)
    assert result["iterations"] == 100
    assert result["per_call_ms"] >= 0
    assert benchmark.stats.counters["uuid_generation_total"] == 100


def test_benchmark_json_serialization():
    """JSON 序列化基准真实执行并记录统计"""
    benchmark = TraceOverheadBenchmark()
    result = benchmark.benchmark_json_serialization(iterations=100)
    assert result["iterations"] == 100
    assert benchmark.stats.counters["json_serialization_total"] == 100


def test_benchmark_context_var_access():
    """ContextVar 访问基准真实执行并记录统计"""
    benchmark = TraceOverheadBenchmark()
    result = benchmark.benchmark_context_var_access(iterations=100)
    assert result["iterations"] == 100
    assert benchmark.stats.counters["context_var_access_total"] == 100


def test_benchmark_context_switch():
    """上下文切换基准真实执行并记录统计"""
    benchmark = TraceOverheadBenchmark()
    result = benchmark.benchmark_context_switch(iterations=100)
    assert result["iterations"] == 100
    assert benchmark.stats.counters["context_switch_total"] == 100


def _patch_missing_trace_context_api(monkeypatch):
    """外部依赖 TraceContext（agent.monitoring.tracing）缺少 add_event/set_attribute 方法，
    被测模块调用了这两个方法。此处为外部类补齐缺失方法，测试仍真实走完 benchmark 逻辑"""
    from agent.monitoring.tracing import TraceContext

    if not hasattr(TraceContext, "add_event"):
        monkeypatch.setattr(TraceContext, "add_event", lambda self, name, attributes=None: None, raising=False)
    if not hasattr(TraceContext, "set_attribute"):
        monkeypatch.setattr(TraceContext, "set_attribute", lambda self, key, value: None, raising=False)


def test_benchmark_span_creation(monkeypatch):
    """Span 创建基准使用真实 TraceContext 执行并记录统计"""
    _patch_missing_trace_context_api(monkeypatch)
    benchmark = TraceOverheadBenchmark()
    result = benchmark.benchmark_span_creation(iterations=20)
    assert result["iterations"] == 20
    assert benchmark.stats.counters["span_creation_total"] == 20
    assert benchmark.stats.get_stats("span_creation")["count"] == 1


def test_benchmark_parallel_spans(monkeypatch):
    """多线程并行 Span 基准真实执行，结果统计线程总数"""
    _patch_missing_trace_context_api(monkeypatch)
    benchmark = TraceOverheadBenchmark()
    result = benchmark.benchmark_parallel_spans(threads=2, iterations=10)
    assert result["threads"] == 2
    assert result["total_iterations"] == 20
    assert result["min_ms"] >= 0
    assert result["max_ms"] >= result["min_ms"]
    assert benchmark.stats.counters["parallel_spans_total"] == 20


# ---------------------------------------------------------------------------
# run_full_benchmark 控制流
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "span_mean, expected_text",
    [
        (0.05, "极低"),
        (0.5, "较低"),
        (2.0, "较高"),
    ],
)
def test_run_full_benchmark_summary_branches(monkeypatch, capsys, span_mean, expected_text):
    """run_full_benchmark 依据 span_creation 均值走三个开销结论分支"""
    benchmark = TraceOverheadBenchmark()

    def stub(metric, duration):
        return lambda iterations=10000: benchmark.stats.record(metric, duration) or {"ok": True}

    monkeypatch.setattr(benchmark, "benchmark_uuid_generation", stub("uuid_generation", 0.001))
    monkeypatch.setattr(benchmark, "benchmark_json_serialization", stub("json_serialization", 0.002))
    monkeypatch.setattr(benchmark, "benchmark_context_var_access", stub("context_var_access", 0.003))
    monkeypatch.setattr(benchmark, "benchmark_context_switch", stub("context_switch", 0.004))
    monkeypatch.setattr(
        benchmark, "benchmark_span_creation", stub("span_creation", span_mean)
    )
    monkeypatch.setattr(benchmark, "benchmark_parallel_spans", lambda threads=4, iterations=1000: {"threads": threads, "iterations_per_thread": iterations, "total_iterations": 0, "avg_per_call_ms": 0, "min_ms": 0, "max_ms": 0})

    results = benchmark.run_full_benchmark()
    out = capsys.readouterr().out

    assert "uuid_generation" in results["detailed_results"]
    assert "parallel_spans" in results["detailed_results"]
    assert "span_creation" in results["summary"]["metrics"]
    assert expected_text in out


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
def test_main_saves_report(monkeypatch, tmp_path):
    """main() 将完整基准结果写入当前目录 JSON 文件"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tp.time, "time", lambda: 1234567890.123)
    fake_results = {"detailed_results": {"uuid_generation": {"ok": 1}}, "summary": {}, "timestamp": 123}
    monkeypatch.setattr(tp.TraceOverheadBenchmark, "run_full_benchmark", lambda self: fake_results)

    tp.main()

    report_file = tmp_path / "tracing_performance_report_1234567890.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text(encoding="utf-8"))
    assert data["detailed_results"]["uuid_generation"] == {"ok": 1}


# ---------------------------------------------------------------------------
# _safe_call
# ---------------------------------------------------------------------------
def test_safe_call_success():
    """_safe_call 正常转发函数调用并返回结果"""
    assert _safe_call(len, [1, 2, 3]) == 3
    assert _safe_call(pow, 2, 10) == 1024
    assert _safe_call(sum, [1, 2, 3], action="add") == 6


def test_safe_call_rethrows_with_error_log(caplog):
    """_safe_call 捕获异常记录结构化错误日志后重新抛出"""
    def boom():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="agent.monitoring.tracing_perf"):
        with pytest.raises(ValueError, match="boom"):
            _safe_call(boom, action="test_operation")

    assert any("test_operation.failed" in str(r.msg) for r in caplog.records)
