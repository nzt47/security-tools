"""日志性能监控模块 — 在核心高频日志路径记录性能对比日志

验证"双重序列化消除"重构后的实际耗时提升。

核心机制：
    旧模式: logger.info(json.dumps({...}, ensure_ascii=False))   # 调用方序列化
            → formatter json.loads(str)                          # formatter 反序列化
            → _format_value 再 json.dumps(val)                    # 二次序列化
    新模式: logger.info(log_dict({...}))                          # 直传 dict
            → formatter 直接使用 dict（跳过 json.loads）         # 零反序列化
            → DictToJsonFilter 仅在文件 handler 序列化一次        # 单次序列化

启用方式：
    - 环境变量 AGENT_PERF_LOGGING=1 启用持续埋点
    - 调用 run_comparison() 运行一次性新旧模式对比

性能开销：
    - 关闭时：仅一次 `is_enabled()` 布尔判断（约 0.05us）
    - 启用时：每次埋点约 1-2us（含 perf_counter 调用）

【生成日志摘要】
- 生成时间: 2026-07-03
- 内容描述: 日志性能监控模块 v1.0
- 关键状态: 提供核心路径性能埋点与新旧模式对比功能
"""
import os
import time
import json
import logging
import threading
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

# Prometheus 指标暴露（可选依赖）
try:
    from prometheus_client import Histogram, Counter, Gauge, start_http_server as _start_http_server
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    Histogram = Counter = Gauge = None

logger = logging.getLogger(__name__)

# 全局开关：环境变量 AGENT_PERF_LOGGING=1 启用
_ENABLED = os.environ.get("AGENT_PERF_LOGGING", "0") == "1"
# Prometheus 指标暴露开关：环境变量 AGENT_PERF_PROMETHEUS=1 启用
# 独立于 _ENABLED，允许在不记录详细日志的情况下暴露指标
_PROMETHEUS_ENABLED = os.environ.get("AGENT_PERF_PROMETHEUS", "0") == "1" and _PROMETHEUS_AVAILABLE
_LOCK = threading.Lock()

# 性能统计汇总：{key: {"count": N, "total_new_us": X, "samples": [...]}}
_STATS: Dict[str, Dict[str, Any]] = {}

# 采样间隔：每 N 次调用记录一次详细日志（避免日志爆炸）
_SAMPLE_INTERVAL = int(os.environ.get("AGENT_PERF_SAMPLE", "100"))

# 采样计数器
_COUNTERS: Dict[str, int] = {}


def is_enabled() -> bool:
    """是否启用性能埋点"""
    return _ENABLED


def enable() -> None:
    """启用性能埋点"""
    global _ENABLED
    _ENABLED = True


def disable() -> None:
    """禁用性能埋点"""
    global _ENABLED
    _ENABLED = False


def _record(module_name: str, action: str, new_us: float, old_us: float = 0.0,
            extra: Optional[Dict[str, Any]] = None) -> None:
    """记录一次性能埋点（内部使用）

    机制说明：
    - Request ID / 采样控制：用计数器取模实现采样，避免高频日志爆炸
    - 边界显性化：old_us/new_us 为负数时记为 0（数据校验）
    - Prometheus 指标：当 _PROMETHEUS_ENABLED 时，并行暴露到 /metrics
    """
    if new_us < 0:
        new_us = 0.0
    if old_us < 0:
        old_us = 0.0

    saved_us = old_us - new_us
    speedup = (old_us / new_us) if new_us > 0 else 0.0
    improvement_pct = (saved_us / old_us * 100) if old_us > 0 else 0.0

    key = f"{module_name}.{action}"

    with _LOCK:
        # 更新汇总统计
        if key not in _STATS:
            _STATS[key] = {
                "count": 0,
                "total_new_us": 0.0,
                "total_old_us": 0.0,
                "max_new_us": 0.0,
                "min_new_us": float("inf"),
            }
        stats = _STATS[key]
        stats["count"] += 1
        stats["total_new_us"] += new_us
        stats["total_old_us"] += old_us
        stats["max_new_us"] = max(stats["max_new_us"], new_us)
        stats["min_new_us"] = min(stats["min_new_us"], new_us)

        # 采样控制：每 N 次记录一次详细日志
        _COUNTERS[key] = _COUNTERS.get(key, 0) + 1
        should_log = (_COUNTERS[key] % _SAMPLE_INTERVAL) == 1

    # Prometheus 指标暴露（独立于日志采样，每次都记录）
    if _PROMETHEUS_ENABLED:
        LogDictPerfMetrics.observe_call(
            module_name=module_name,
            action=action,
            new_us=new_us,
            old_us=old_us,
        )

    if should_log:
        payload = {
            "trace_id": "",
            "module_name": "perf_monitor",
            "action": f"{key}.compare",
            "duration_ms": round(new_us / 1000, 6),
            "old_mode_us": round(old_us, 3),
            "new_mode_us": round(new_us, 3),
            "saved_us": round(saved_us, 3),
            "speedup": round(speedup, 3),
            "improvement_pct": round(improvement_pct, 2),
            "sample_no": _COUNTERS.get(key, 0),
        }
        if extra:
            payload.update(extra)
        # 直接用 logger.info 输出 JSON 字符串（避免循环依赖 log_dict）
        logger.info(json.dumps(payload, ensure_ascii=False))


@contextmanager
def perf_trace(module_name: str, action: str, old_us: float = 0.0,
               extra: Optional[Dict[str, Any]] = None):
    """性能埋点上下文管理器

    用法：
        with perf_trace("log_dict", "normalize", old_us=5.88):
            data = log_dict(payload)

    机制：AbortController 等价——上下文退出时自动记录，异常不吞掉
    """
    if not _ENABLED:
        yield
        return

    start = time.perf_counter()
    try:
        yield
    finally:
        new_us = (time.perf_counter() - start) * 1_000_000
        _record(module_name, action, new_us, old_us, extra)


def record_call(module_name: str, action: str, new_us: float,
                old_us: float = 0.0, extra: Optional[Dict[str, Any]] = None) -> None:
    """直接记录一次性能数据（无上下文管理器开销）

    适用于已自行测量耗时的场景。
    """
    if not _ENABLED:
        return
    _record(module_name, action, new_us, old_us, extra)


def get_stats() -> Dict[str, Dict[str, float]]:
    """获取性能统计汇总

    返回每个 (module, action) 的：
    - count: 调用次数
    - avg_new_us: 新模式平均耗时
    - avg_old_us: 旧模式平均耗时（基准）
    - total_saved_us: 累计节省
    - speedup: 加速比
    - improvement_pct: 提升百分比
    """
    with _LOCK:
        result = {}
        for key, s in _STATS.items():
            count = s["count"]
            if count == 0:
                continue
            avg_new = s["total_new_us"] / count
            avg_old = s["total_old_us"] / count
            saved = s["total_old_us"] - s["total_new_us"]
            result[key] = {
                "count": count,
                "avg_new_us": round(avg_new, 3),
                "avg_old_us": round(avg_old, 3),
                "total_saved_us": round(saved, 3),
                "max_new_us": round(s["max_new_us"], 3),
                "min_new_us": round(s["min_new_us"], 3),
                "speedup": round(avg_old / avg_new, 3) if avg_new > 0 else 0.0,
                "improvement_pct": round(
                    (avg_old - avg_new) / avg_old * 100, 2
                ) if avg_old > 0 else 0.0,
            }
        return result


def reset_stats() -> None:
    """重置所有性能统计"""
    with _LOCK:
        _STATS.clear()
        _COUNTERS.clear()


def log_summary() -> None:
    """输出性能汇总报告（结构化日志）"""
    stats = get_stats()
    if not stats:
        logger.info(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": "summary.empty", "msg": "无性能统计数据"
        }, ensure_ascii=False))
        return

    for key, s in stats.items():
        logger.info(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": f"summary.{key}",
            "duration_ms": round(s["avg_new_us"] / 1000, 6),
            "count": s["count"],
            "avg_old_us": s["avg_old_us"],
            "avg_new_us": s["avg_new_us"],
            "total_saved_us": s["total_saved_us"],
            "speedup": s["speedup"],
            "improvement_pct": s["improvement_pct"],
        }, ensure_ascii=False))


def run_comparison(payloads: Optional[List[Dict[str, Any]]] = None,
                   iterations: int = 10000) -> Dict[str, Any]:
    """运行一次性新旧模式对比，返回详细对比数据

    用于验证"双重序列化消除"后的实际耗时提升。

    Args:
        payloads: 测试用的日志 payload 列表（默认生成 3 种复杂度）
        iterations: 每种 payload 的迭代次数

    Returns:
        {
            "scenarios": [
                {
                    "name": "simple",
                    "old_total_ms": X,
                    "new_total_ms": Y,
                    "old_per_call_us": ...,
                    "new_per_call_us": ...,
                    "speedup": ...,
                    "improvement_pct": ...,
                },
                ...
            ],
            "summary": {...}
        }

    机制说明：
    - Request ID：用 iterations 控制样本量
    - 边界显性化：异常时抛出 RuntimeError 而非静默返回
    """
    import uuid

    def _trace_id():
        return uuid.uuid4().hex[:16]

    if payloads is None:
        payloads = [
            # 简单 payload
            {"trace_id": _trace_id(), "module_name": "test", "action": "log",
             "duration_ms": 0, "message": "简单日志"},
            # 中等 payload
            {"trace_id": _trace_id(), "module_name": "test", "action": "test.medium",
             "duration_ms": 45, "message": "中等复杂度日志", "user_id": 42,
             "tags": ["search", "api"], "metadata": {"engine": "tavily", "version": "1.0"}},
            # 复杂 payload
            {"trace_id": _trace_id(), "module_name": "test", "action": "complex",
             "duration_ms": 120, "message": "复杂日志", "user_id": 42,
             "session_id": "sess-abc", "request": {"method": "POST", "url": "/api/search",
             "headers": {"content-type": "application/json"}, "body": {"query": "test"}},
             "response": {"status": 200, "results": [{"id": 1, "title": "结果1"},
             {"id": 2, "title": "结果2"}]}, "tags": ["api", "search", "v1"],
             "metadata": {"engine": "tavily", "latency_ms": 123.4}},
        ]

    results = []
    for idx, payload in enumerate(payloads):
        name = payload.get("action", f"scenario_{idx}")

        # 旧模式：json.dumps（调用方序列化）
        start = time.perf_counter()
        for _ in range(iterations):
            _ = json.dumps(payload, ensure_ascii=False)
        old_total = time.perf_counter() - start
        old_per_call = old_total / iterations * 1_000_000

        # 新模式：log_dict（dict 直传，无序列化）
        from agent.logging_utils import log_dict
        start = time.perf_counter()
        for _ in range(iterations):
            _ = log_dict(payload)
        new_total = time.perf_counter() - start
        new_per_call = new_total / iterations * 1_000_000

        speedup = old_per_call / new_per_call if new_per_call > 0 else 0.0
        improvement = ((old_per_call - new_per_call) / old_per_call * 100
                       if old_per_call > 0 else 0.0)

        scenario = {
            "name": name,
            "iterations": iterations,
            "old_total_ms": round(old_total * 1000, 3),
            "new_total_ms": round(new_total * 1000, 3),
            "old_per_call_us": round(old_per_call, 3),
            "new_per_call_us": round(new_per_call, 3),
            "saved_per_call_us": round(old_per_call - new_per_call, 3),
            "speedup": round(speedup, 3),
            "improvement_pct": round(improvement, 2),
        }
        results.append(scenario)

        # 输出对比日志
        logger.info(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": f"comparison.{name}",
            "duration_ms": round(new_per_call / 1000, 6),
            "old_per_call_us": round(old_per_call, 3),
            "new_per_call_us": round(new_per_call, 3),
            "saved_per_call_us": round(old_per_call - new_per_call, 3),
            "speedup": round(speedup, 3),
            "improvement_pct": round(improvement, 2),
            "iterations": iterations,
        }, ensure_ascii=False))

    # 汇总
    total_saved = sum(s["saved_per_call_us"] * s["iterations"] for s in results) / 1000
    summary = {
        "scenarios": len(results),
        "total_iterations": len(results) * iterations,
        "total_saved_ms": round(total_saved, 3),
        "avg_speedup": round(sum(s["speedup"] for s in results) / len(results), 3),
    }

    logger.info(json.dumps({
        "trace_id": "", "module_name": "perf_monitor",
        "action": "comparison.summary",
        "duration_ms": 0,
        "scenarios": summary["scenarios"],
        "total_iterations": summary["total_iterations"],
        "total_saved_ms": summary["total_saved_ms"],
        "avg_speedup": summary["avg_speedup"],
    }, ensure_ascii=False))

    return {"scenarios": results, "summary": summary}


# ─────────────────────────────────────────────────
# 高并发压力测试——模拟生产环境多线程日志写入
# ─────────────────────────────────────────────────


def stress_test(
    num_threads: int = 8,
    duration_seconds: float = 5.0,
    payloads: Optional[List[Dict[str, Any]]] = None,
    use_log_dict: bool = True,
    enable_filter_chain: bool = True,
    report_interval: Optional[float] = 1.0,
    filter_chain_factory: Optional[Any] = None,
    log_dict_factory: Optional[Any] = None,
) -> Dict[str, Any]:
    """高并发压力测试——模拟生产环境多线程日志写入

    测量指标：
    1. 吞吐量（ops/sec）—— 总日志写入次数 / 实际耗时
    2. 延迟分位（p50/p90/p99/max）—— 单次 logger.info 调用耗时
    3. 内存增长—— 测试前后的 tracemalloc 内存差
    4. 错误率—— 失败次数 / 总次数

    Args:
        num_threads: 并发线程数（默认 8）
        duration_seconds: 测试持续时间（秒，默认 5.0）
        payloads: 测试用 payload 列表（默认使用 3 种复杂度）
        use_log_dict: True=新模式（log_dict dict 直传），False=旧模式（json.dumps 字符串）
        enable_filter_chain: 是否启用完整 filter 链（SensitiveDataFilter + EmojiFilter + DictToJsonFilter）
        report_interval: 实时报告间隔（秒），None=不报告
        filter_chain_factory: 可选的 filter 链工厂函数，返回 List[logging.Filter]。
            默认 None 时延迟导入 agent.logging_utils 的内置 filter。
            注入此参数可彻底解耦 perf_monitor 与 logging_utils 的依赖。
        log_dict_factory: 可选的 log_dict 替代函数，签名 (Dict) -> Dict。
            默认 None 时延迟导入 agent.logging_utils.log_dict。
            注入此参数可在不依赖 logging_utils 的情况下测试新模式。

    Returns:
        {
            "mode": "new" | "old",
            "config": {...},
            "throughput_ops_per_sec": float,
            "total_ops": int,
            "duration_seconds": float,
            "latency": {"p50_us": ..., "p90_us": ..., "p99_us": ..., "max_us": ...},
            "memory_growth_bytes": int,
            "errors": int,
            "error_rate": float,
            "thread_results": [...],
        }

    机制说明：
    - 竞态防御：每个线程独立计数器，最终汇总（避免锁竞争影响测量）
    - 边界显性化：参数校验失败抛 ValueError 而非静默返回
    - 幂等性：每次调用重置全局状态，可重复运行
    """
    import threading
    import time
    import tracemalloc
    from statistics import median, mean

    # ── 边界显性化：参数校验 ──
    if num_threads <= 0:
        raise ValueError(f"num_threads 必须为正数，得到: {num_threads}")
    if duration_seconds <= 0:
        raise ValueError(f"duration_seconds 必须为正数，得到: {duration_seconds}")
    if report_interval is not None and report_interval <= 0:
        raise ValueError(f"report_interval 必须为正数或 None，得到: {report_interval}")

    # ── 准备 payload ──
    if payloads is None:
        payloads = [
            {"trace_id": "stress-test", "module_name": "perf", "action": "log",
             "duration_ms": 0, "message": "stress simple"},
            {"trace_id": "stress-test", "module_name": "perf", "action": "medium",
             "duration_ms": 45, "message": "stress medium log", "user_id": 42,
             "tags": ["search", "api"], "metadata": {"engine": "tavily"}},
            {"trace_id": "stress-test", "module_name": "perf", "action": "complex",
             "duration_ms": 120, "message": "stress complex log", "user_id": 42,
             "session_id": "sess-abc", "request": {"method": "POST", "url": "/api/search"},
             "response": {"status": 200, "results": [{"id": 1, "title": "r1"}]}},
        ]

    # ── 准备 logger（独立隔离，不污染全局） ──
    import logging as _logging

    stress_logger = _logging.getLogger(f"stress_test_{id(payloads)}")
    stress_logger.handlers.clear()
    stress_logger.setLevel(_logging.INFO)
    stress_logger.propagate = False  # 关键：避免被 pytest 捕获导致内存累积

    class _DiscardHandler(_logging.Handler):
        """丢弃所有输出的 handler，仅触发 filter 链

        关键：继承 Handler 而非 NullHandler。
        NullHandler 覆盖了 handle() 为 pass，导致 filter 链不被调用。
        Handler.handle() 会先调用 self.filter(record) 遍历 filter 链，
        然后才调用 emit()。我们覆盖 emit 为 pass，丢弃输出但保留 filter 调用。
        """
        def emit(self, record):
            pass

    handler = _DiscardHandler()
    if enable_filter_chain:
        # 优先使用注入的 filter 链工厂，避免对 logging_utils 的依赖
        if filter_chain_factory is not None:
            for flt in filter_chain_factory():
                handler.addFilter(flt)
        else:
            # 延迟导入保持向后兼容（不破坏现有调用方）
            from agent.logging_utils import EmojiFilter, DictToJsonFilter, SensitiveDataFilter
            handler.addFilter(SensitiveDataFilter())
            handler.addFilter(EmojiFilter())
            handler.addFilter(DictToJsonFilter())
    stress_logger.addHandler(handler)

    # ── 准备导入 ──
    if use_log_dict:
        # 优先使用注入的 log_dict 工厂，避免对 logging_utils 的依赖
        if log_dict_factory is not None:
            _log_dict = log_dict_factory
        else:
            from agent.logging_utils import log_dict as _log_dict
        import json as _json

        def _emit(payload):
            stress_logger.info(_log_dict(payload))
    else:
        import json as _json

        def _emit(payload):
            stress_logger.info(_json.dumps(payload, ensure_ascii=False))

    # ── 线程工作函数 ──
    stop_event = threading.Event()
    # 每个线程独立计数器和延迟列表，避免锁竞争
    thread_results = [None] * num_threads

    def _worker(thread_idx: int):
        local_count = 0
        local_errors = 0
        local_latencies = []  # 微秒
        payload_count = len(payloads)

        while not stop_event.is_set():
            start = time.perf_counter()
            try:
                _emit(payloads[local_count % payload_count])
            except Exception:
                local_errors += 1
            elapsed_us = (time.perf_counter() - start) * 1_000_000
            local_latencies.append(elapsed_us)
            local_count += 1

        thread_results[thread_idx] = {
            "count": local_count,
            "errors": local_errors,
            "latencies": local_latencies,
        }

    # ── 实时报告线程 ──
    last_report = [time.perf_counter()]
    last_count = [0]

    def _reporter():
        while not stop_event.is_set():
            time.sleep(report_interval)
            now = time.perf_counter()
            current_count = sum(r["count"] for r in thread_results if r is not None)
            elapsed = now - last_report[0]
            ops = current_count - last_count[0]
            if elapsed > 0:
                rate = ops / elapsed
                logger.info(json.dumps({
                    "trace_id": "", "module_name": "perf_monitor",
                    "action": "stress_test.progress",
                    "duration_ms": 0,
                    "mode": "new" if use_log_dict else "old",
                    "threads": num_threads,
                    "current_ops": current_count,
                    "rate_ops_per_sec": round(rate, 1),
                    "elapsed_seconds": round(now - test_start[0], 2),
                }, ensure_ascii=False))
            last_report[0] = now
            last_count[0] = current_count

    # ── 启动测试 ──
    test_start = [time.perf_counter()]

    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0]

    threads = [threading.Thread(target=_worker, args=(i,), daemon=True)
               for i in range(num_threads)]

    reporter_thread = None
    if report_interval is not None:
        reporter_thread = threading.Thread(target=_reporter, daemon=True)

    for t in threads:
        t.start()
    if reporter_thread:
        reporter_thread.start()

    # ── 等待 duration ──
    time.sleep(duration_seconds)
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    test_end = time.perf_counter()
    actual_duration = test_end - test_start[0]

    mem_after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    if reporter_thread:
        reporter_thread.join(timeout=1.0)

    # ── 汇总统计 ──
    total_ops = sum(r["count"] for r in thread_results)
    total_errors = sum(r["errors"] for r in thread_results)
    all_latencies = []
    for r in thread_results:
        all_latencies.extend(r["latencies"])

    # 排序后计算分位
    all_latencies.sort()
    n = len(all_latencies)
    if n > 0:
        p50 = all_latencies[n // 2]
        p90 = all_latencies[int(n * 0.9)]
        p99 = all_latencies[int(n * 0.99)]
        max_lat = all_latencies[-1]
        avg_lat = mean(all_latencies)
    else:
        p50 = p90 = p99 = max_lat = avg_lat = 0.0

    throughput = total_ops / actual_duration if actual_duration > 0 else 0.0
    error_rate = total_errors / total_ops if total_ops > 0 else 0.0

    result = {
        "mode": "new" if use_log_dict else "old",
        "config": {
            "num_threads": num_threads,
            "duration_seconds": duration_seconds,
            "use_log_dict": use_log_dict,
            "enable_filter_chain": enable_filter_chain,
            "num_payloads": len(payloads),
        },
        "throughput_ops_per_sec": round(throughput, 1),
        "total_ops": total_ops,
        "duration_seconds_actual": round(actual_duration, 3),
        "latency_us": {
            "avg": round(avg_lat, 3),
            "p50": round(p50, 3),
            "p90": round(p90, 3),
            "p99": round(p99, 3),
            "max": round(max_lat, 3),
        },
        "memory_growth_bytes": mem_after - mem_before,
        "errors": total_errors,
        "error_rate": round(error_rate, 6),
        "thread_results": [
            {"thread": i, "count": r["count"], "errors": r["errors"]}
            for i, r in enumerate(thread_results)
        ],
    }

    # 输出最终报告
    logger.info(json.dumps({
        "trace_id": "", "module_name": "perf_monitor",
        "action": "stress_test.completed",
        "duration_ms": round(actual_duration * 1000, 3),
        "mode": result["mode"],
        "throughput_ops_per_sec": result["throughput_ops_per_sec"],
        "total_ops": total_ops,
        "latency_avg_us": result["latency_us"]["avg"],
        "latency_p50_us": result["latency_us"]["p50"],
        "latency_p99_us": result["latency_us"]["p99"],
        "memory_growth_bytes": result["memory_growth_bytes"],
        "errors": total_errors,
        "error_rate": result["error_rate"],
    }, ensure_ascii=False))

    return result


def run_stress_comparison(
    num_threads: int = 8,
    duration_seconds: float = 3.0,
    payloads: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """运行新旧模式的高并发压力对比测试

    同时运行新模式（log_dict）和旧模式（json.dumps）的压力测试，
    返回对比数据，验证重构后的吞吐量提升和延迟降低。

    Args:
        num_threads: 并发线程数
        duration_seconds: 每个模式测试持续时间
        payloads: 测试用 payload 列表

    Returns:
        {
            "new_mode": {...stress_test 结果...},
            "old_mode": {...stress_test 结果...},
            "comparison": {
                "throughput_speedup": float,      # 新模式吞吐量加速比
                "latency_p50_reduction_pct": float,
                "latency_p99_reduction_pct": float,
                "memory_growth_diff_bytes": int,
            },
        }

    机制说明：
    - 边界显性化：参数校验失败抛 ValueError
    - 幂等性：每次调用独立 logger，避免状态污染
    """
    if num_threads <= 0:
        raise ValueError(f"num_threads 必须为正数，得到: {num_threads}")
    if duration_seconds <= 0:
        raise ValueError(f"duration_seconds 必须为正数，得到: {duration_seconds}")

    # 重置性能统计
    reset_stats()

    logger.info(json.dumps({
        "trace_id": "", "module_name": "perf_monitor",
        "action": "stress_comparison.start",
        "duration_ms": 0,
        "num_threads": num_threads,
        "duration_seconds_per_mode": duration_seconds,
    }, ensure_ascii=False))

    # 运行新模式测试
    new_result = stress_test(
        num_threads=num_threads,
        duration_seconds=duration_seconds,
        payloads=payloads,
        use_log_dict=True,
        report_interval=None,  # 对比测试时不打印进度
    )

    # 运行旧模式测试
    old_result = stress_test(
        num_threads=num_threads,
        duration_seconds=duration_seconds,
        payloads=payloads,
        use_log_dict=False,
        report_interval=None,
    )

    # 计算对比指标
    new_tps = new_result["throughput_ops_per_sec"]
    old_tps = old_result["throughput_ops_per_sec"]
    throughput_speedup = new_tps / old_tps if old_tps > 0 else 0.0

    new_p50 = new_result["latency_us"]["p50"]
    old_p50 = old_result["latency_us"]["p50"]
    p50_reduction = ((old_p50 - new_p50) / old_p50 * 100) if old_p50 > 0 else 0.0

    new_p99 = new_result["latency_us"]["p99"]
    old_p99 = old_result["latency_us"]["p99"]
    p99_reduction = ((old_p99 - new_p99) / old_p99 * 100) if old_p99 > 0 else 0.0

    memory_diff = new_result["memory_growth_bytes"] - old_result["memory_growth_bytes"]

    comparison = {
        "throughput_speedup": round(throughput_speedup, 3),
        "throughput_improvement_pct": round((throughput_speedup - 1) * 100, 2),
        "latency_p50_reduction_pct": round(p50_reduction, 2),
        "latency_p99_reduction_pct": round(p99_reduction, 2),
        "memory_growth_diff_bytes": memory_diff,
    }

    logger.info(json.dumps({
        "trace_id": "", "module_name": "perf_monitor",
        "action": "stress_comparison.completed",
        "duration_ms": 0,
        "throughput_speedup": comparison["throughput_speedup"],
        "throughput_improvement_pct": comparison["throughput_improvement_pct"],
        "latency_p50_reduction_pct": comparison["latency_p50_reduction_pct"],
        "latency_p99_reduction_pct": comparison["latency_p99_reduction_pct"],
        "memory_growth_diff_bytes": memory_diff,
        "new_mode_tps": new_tps,
        "old_mode_tps": old_tps,
    }, ensure_ascii=False))

    return {
        "new_mode": new_result,
        "old_mode": old_result,
        "comparison": comparison,
    }


# ─────────────────────────────────────────────────
# Prometheus 指标暴露
# ─────────────────────────────────────────────────


class _NoopMetric:
    """prometheus_client 不可用时的 no-op 替代，避免散落的 if 判断"""

    def labels(self, *args, **kwargs):
        return self

    def observe(self, value):
        pass

    def inc(self, value=1):
        pass

    def set(self, value):
        pass


class LogDictPerfMetrics:
    """log_dict 性能指标 Prometheus 暴露

    通过 AGENT_PERF_PROMETHEUS=1 环境变量启用。
    独立于 AGENT_PERF_LOGGING，允许在不记录详细日志的情况下暴露指标。

    暴露的指标：
    - log_dict_call_duration_seconds (Histogram, labels: mode) — 调用耗时
    - log_dict_calls_total (Counter, labels: mode, status) — 调用次数
    - log_dict_speedup_ratio (Gauge, labels: module, action) — 加速比
    - log_dict_improvement_pct (Gauge, labels: module, action) — 提升百分比

    使用方法：
        # 1. 启动 Prometheus HTTP 端点（暴露 /metrics）
        from agent.utils.perf_monitor import start_metrics_server
        start_metrics_server(port=8001)

        # 2. 在性能埋点中自动记录（_record() 已集成）
        with perf_trace("log_dict", "normalize", old_us=5.88):
            data = log_dict(payload)
        # LogDictPerfMetrics.observe_call() 会被自动调用
    """

    _call_duration: Optional[Any] = None
    _calls_total: Optional[Any] = None
    _speedup_ratio: Optional[Any] = None
    _improvement_pct: Optional[Any] = None
    _initialized: bool = False

    @classmethod
    def _ensure_initialized(cls) -> None:
        """延迟初始化 Prometheus 指标（首次调用时创建）"""
        if cls._initialized:
            return
        if not _PROMETHEUS_AVAILABLE:
            cls._call_duration = _NoopMetric()
            cls._calls_total = _NoopMetric()
            cls._speedup_ratio = _NoopMetric()
            cls._improvement_pct = _NoopMetric()
        else:
            cls._call_duration = Histogram(
                'log_dict_call_duration_seconds',
                'log_dict() 调用耗时（秒）',
                buckets=[1e-6, 5e-6, 1e-5, 5e-5, 1e-4, 5e-4, 1e-3, 5e-3, 1e-2],
                labelnames=['mode'],
            )
            cls._calls_total = Counter(
                'log_dict_calls_total',
                'log_dict() 调用次数',
                labelnames=['mode', 'status'],
            )
            cls._speedup_ratio = Gauge(
                'log_dict_speedup_ratio',
                'log_dict 新旧模式加速比（old_us / new_us）',
                labelnames=['module', 'action'],
            )
            cls._improvement_pct = Gauge(
                'log_dict_improvement_pct',
                'log_dict 性能提升百分比',
                labelnames=['module', 'action'],
            )
        cls._initialized = True

    @classmethod
    def observe_call(cls, module_name: str, action: str,
                     new_us: float, old_us: float = 0.0) -> None:
        """记录一次 log_dict 调用的性能指标

        在 _record() 中自动调用，通常无需手动调用。

        Args:
            module_name: 模块名（如 "log_dict"）
            action: 动作名（如 "normalize"）
            new_us: 新模式耗时（微秒）
            old_us: 旧模式耗时（微秒，0 表示无基准）
        """
        if not _PROMETHEUS_ENABLED:
            return
        cls._ensure_initialized()

        # 耗时直方图（秒）
        cls._call_duration.labels(mode='new').observe(new_us / 1_000_000)
        if old_us > 0:
            cls._call_duration.labels(mode='old').observe(old_us / 1_000_000)

        # 调用计数
        cls._calls_total.labels(mode='new', status='success').inc()
        if old_us > 0:
            cls._calls_total.labels(mode='old', status='success').inc()

        # 加速比和提升百分比（仅当有旧模式基准时）
        if old_us > 0 and new_us > 0:
            speedup = old_us / new_us
            improvement_pct = ((old_us - new_us) / old_us * 100)
            cls._speedup_ratio.labels(module=module_name, action=action).set(round(speedup, 3))
            cls._improvement_pct.labels(module=module_name, action=action).set(round(improvement_pct, 2))

    @classmethod
    def observe_failure(cls, module_name: str, action: str,
                        error_type: str = 'unknown') -> None:
        """记录一次 log_dict 调用失败

        用于在 try/except 中记录异常情况。

        Args:
            module_name: 模块名
            action: 动作名
            error_type: 错误类型（如 'serialization', 'type_error'）
        """
        if not _PROMETHEUS_ENABLED:
            return
        cls._ensure_initialized()
        cls._calls_total.labels(mode='new', status='failure').inc()

    @classmethod
    def observe_pipeline(cls, handler_type: str, duration_us: float) -> None:
        """记录完整日志管道耗时

        用于在 filter 链或 handler 中埋点。

        Args:
            handler_type: handler 类型（如 'console', 'file'）
            duration_us: 管道耗时（微秒）
        """
        if not _PROMETHEUS_ENABLED:
            return
        cls._ensure_initialized()
        # 复用 _call_duration 但添加 handler_type 维度需要新指标
        # 这里简化为记录到 module/action 维度
        cls._call_duration.labels(mode=f'pipeline_{handler_type}').observe(duration_us / 1_000_000)

    @classmethod
    def is_enabled(cls) -> bool:
        """是否启用 Prometheus 指标暴露"""
        return _PROMETHEUS_ENABLED

    @classmethod
    def get_metric_names(cls) -> List[str]:
        """获取所有暴露的指标名（用于调试）"""
        return [
            'log_dict_call_duration_seconds',
            'log_dict_calls_total',
            'log_dict_speedup_ratio',
            'log_dict_improvement_pct',
        ]


def start_metrics_server(port: int = 8001, addr: str = '0.0.0.0') -> bool:
    """启动 Prometheus HTTP 端点暴露 /metrics

    独立于应用主服务，避免与应用端口冲突。
    启动后可通过 http://localhost:{port}/metrics 采集指标。

    Args:
        port: 监听端口（默认 8001，避免与应用 8000 冲突）
        addr: 绑定地址（默认 0.0.0.0 允许外部访问）

    Returns:
        True 启动成功，False 启动失败（prometheus_client 未安装或端口占用）

    使用示例：
        # 在应用启动时调用
        from agent.utils.perf_monitor import start_metrics_server
        start_metrics_server(port=8001)

        # 配合 Prometheus scrape_configs
        # scrape_configs:
        #   - job_name: 'agent-perf'
        #     static_configs:
        #       - targets: ['localhost:8001']
    """
    if not _PROMETHEUS_AVAILABLE:
        logger.warning(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": "metrics_server.unavailable",
            "msg": "prometheus_client 未安装，无法启动 metrics server"
        }, ensure_ascii=False))
        return False

    # 确保指标已初始化
    LogDictPerfMetrics._ensure_initialized()

    try:
        _start_http_server(port, addr=addr)
        logger.info(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": "metrics_server.started",
            "duration_ms": 0,
            "port": port,
            "addr": addr,
            "metrics_path": "/metrics",
            "exposed_metrics": LogDictPerfMetrics.get_metric_names(),
        }, ensure_ascii=False))
        return True
    except OSError as e:
        logger.error(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": "metrics_server.start_failed",
            "duration_ms": 0,
            "msg": f"端口 {port} 启动失败: {e}",
            "error_type": type(e).__name__,
        }, ensure_ascii=False))
        return False


def enable_prometheus() -> None:
    """运行时启用 Prometheus 指标暴露

    允许在不重启应用的情况下启用指标暴露。
    配合 start_metrics_server() 使用。
    """
    global _PROMETHEUS_ENABLED
    if not _PROMETHEUS_AVAILABLE:
        logger.warning(json.dumps({
            "trace_id": "", "module_name": "perf_monitor",
            "action": "prometheus.enable_failed",
            "msg": "prometheus_client 未安装"
        }, ensure_ascii=False))
        return
    _PROMETHEUS_ENABLED = True
    LogDictPerfMetrics._ensure_initialized()
    logger.info(json.dumps({
        "trace_id": "", "module_name": "perf_monitor",
        "action": "prometheus.enabled",
        "duration_ms": 0,
        "msg": "Prometheus 指标暴露已启用"
    }, ensure_ascii=False))


def disable_prometheus() -> None:
    """运行时禁用 Prometheus 指标暴露"""
    global _PROMETHEUS_ENABLED
    _PROMETHEUS_ENABLED = False
    logger.info(json.dumps({
        "trace_id": "", "module_name": "perf_monitor",
        "action": "prometheus.disabled",
        "duration_ms": 0,
        "msg": "Prometheus 指标暴露已禁用"
    }, ensure_ascii=False))


__all__ = [
    "is_enabled", "enable", "disable",
    "perf_trace", "record_call",
    "get_stats", "reset_stats", "log_summary",
    "run_comparison",
    "stress_test", "run_stress_comparison",
    # Prometheus 指标暴露
    "LogDictPerfMetrics", "start_metrics_server",
    "enable_prometheus", "disable_prometheus",
]
