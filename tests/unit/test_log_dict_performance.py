"""log_dict 重构性能对比测试

对比两种日志路径的耗时：
1. 旧模式（双重序列化）：logger.info(json.dumps({...}, ensure_ascii=False))
   调用方 json.dumps(dict) → formatter json.loads(str) → _format_value 再 json.dumps(val)
2. 新模式（单次序列化）：logger.info(log_dict({...}))
   log_dict 返回 dict → formatter 直接使用 dict → 文件 handler 单次 json.dumps

【生成日志摘要】
- 生成时间: 2026-07-03
- 内容描述: log_dict 性能对比测试 v1.0
- 关键状态: 验证双重序列化消除后的实际耗时提升
"""

import json
import logging
import os
import sys
import tempfile
import time
import statistics
from io import StringIO
from typing import List

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.logging_utils import log_dict, DictToJsonFilter, EmojiFilter, SensitiveDataFilter


# ─────────────────────────────────────────────────
# 测试用的日志 payload 模板
# ─────────────────────────────────────────────────

# 简单日志（5 个字段）
SIMPLE_PAYLOAD = {
    "trace_id": "abc12345",
    "module_name": "test_module",
    "action": "test.simple",
    "duration_ms": 10,
    "message": "简单日志消息",
}

# 中等复杂度日志（10 个字段，含嵌套）
MEDIUM_PAYLOAD = {
    "trace_id": "abc12345",
    "module_name": "test_module",
    "action": "test.medium",
    "duration_ms": 45,
    "message": "中等复杂度日志",
    "user_id": 42,
    "instance_id": "inst-550e8400",
    "tags": ["search", "api"],
    "metadata": {"engine": "tavily", "version": "1.0"},
    "priority": 1,
}

# 高复杂度日志（15+ 字段，含深度嵌套和长列表）
COMPLEX_PAYLOAD = {
    "trace_id": "abc12345def67890",
    "module_name": "orchestrator",
    "action": "orchestrator.execute_task.complete",
    "duration_ms": 1250,
    "message": "任务执行完成，包含大量上下文信息用于性能测试",
    "task_id": "task-550e8400-e29b-41d4-a716-446655440000",
    "user_id": 42,
    "session_id": "sess-abc123def456",
    "steps": [
        {"step": "plan", "duration_ms": 120, "status": "ok"},
        {"step": "execute", "duration_ms": 980, "status": "ok"},
        {"step": "verify", "duration_ms": 150, "status": "ok"},
    ],
    "context": {
        "input": {"query": "复杂查询语句，包含多个关键词和参数",
                  "params": {"limit": 10, "offset": 0, "filter": "active"}},
        "output": {"result_count": 8, "items": list(range(8))},
    },
    "metrics": {"cpu": 45.2, "memory_mb": 128.5, "io_ops": 1024},
    "priority_before": ["uuid-aaa", "uuid-bbb"],
    "priority_after": ["uuid-aaa", "uuid-bbb", "uuid-ccc"],
}


def _make_record(msg):
    return logging.LogRecord(
        name="perf_test", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=None, exc_info=None,
    )


def _measure(callable_, iterations: int = 1000, warmup: int = 100) -> List[float]:
    """测量 callable 的耗时（微秒），含 warmup 轮预热"""
    # 预热
    for _ in range(warmup):
        callable_()

    # 正式测量
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        callable_()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1_000_000)  # 转换为微秒
    return times


def _stats(times: List[float]) -> dict:
    """计算统计指标"""
    return {
        "count": len(times),
        "mean_us": round(statistics.mean(times), 2),
        "median_us": round(statistics.median(times), 2),
        "p95_us": round(statistics.quantiles(times, n=20)[18], 2) if len(times) >= 20 else round(max(times), 2),
        "p99_us": round(statistics.quantiles(times, n=100)[98], 2) if len(times) >= 100 else round(max(times), 2),
        "min_us": round(min(times), 2),
        "max_us": round(max(times), 2),
        "stdev_us": round(statistics.stdev(times), 2) if len(times) > 1 else 0,
    }


# ─────────────────────────────────────────────────
# TestPerformanceComparison: 旧模式 vs 新模式性能对比
# ─────────────────────────────────────────────────
class TestPerformanceComparison:
    """对比旧模式（json.dumps）和新模式（log_dict）的耗时"""

    ITERATIONS = 2000

    def _print_comparison(self, label: str, old_stats: dict, new_stats: dict):
        """打印对比结果"""
        speedup = old_stats["mean_us"] / new_stats["mean_us"] if new_stats["mean_us"] > 0 else float("inf")
        saved_us = old_stats["mean_us"] - new_stats["mean_us"]
        print(f"\n{'=' * 70}")
        print(f"性能对比: {label}")
        print(f"{'=' * 70}")
        print(f"{'指标':<12} {'旧模式(us)':<15} {'新模式(us)':<15} {'提升':<15}")
        print(f"{'-' * 57}")
        print(f"{'mean':<12} {old_stats['mean_us']:<15.2f} {new_stats['mean_us']:<15.2f} {saved_us:<+15.2f}")
        print(f"{'median':<12} {old_stats['median_us']:<15.2f} {new_stats['median_us']:<15.2f} {old_stats['median_us']-new_stats['median_us']:<+15.2f}")
        print(f"{'p95':<12} {old_stats['p95_us']:<15.2f} {new_stats['p95_us']:<15.2f} {old_stats['p95_us']-new_stats['p95_us']:<+15.2f}")
        print(f"{'p99':<12} {old_stats['p99_us']:<15.2f} {new_stats['p99_us']:<15.2f} {old_stats['p99_us']-new_stats['p99_us']:<+15.2f}")
        print(f"{'min':<12} {old_stats['min_us']:<15.2f} {new_stats['min_us']:<15.2f}")
        print(f"{'max':<12} {old_stats['max_us']:<15.2f} {new_stats['max_us']:<15.2f}")
        print(f"{'stdev':<12} {old_stats['stdev_us']:<15.2f} {new_stats['stdev_us']:<15.2f}")
        print(f"{'-' * 57}")
        print(f"加速比: {speedup:.2f}x")
        print(f"平均节省: {saved_us:.2f} us ({saved_us/old_stats['mean_us']*100:.1f}%)")
        print(f"{'=' * 70}\n")

    def test_simple_payload_log_dict_faster(self):
        """简单 payload: log_dict 应比 json.dumps 更快"""
        payload = dict(SIMPLE_PAYLOAD)

        # 旧模式：json.dumps
        def old_way():
            msg = json.dumps(payload, ensure_ascii=False)
            return msg

        # 新模式：log_dict
        def new_way():
            return log_dict(payload)

        old_times = _measure(old_way, self.ITERATIONS)
        new_times = _measure(new_way, self.ITERATIONS)

        old_stats = _stats(old_times)
        new_stats = _stats(new_times)
        self._print_comparison("简单 payload (5 字段)", old_stats, new_stats)

        # log_dict 应该比 json.dumps 更快（dict 操作 vs 字符串序列化）
        assert new_stats["mean_us"] <= old_stats["mean_us"], (
            f"log_dict ({new_stats['mean_us']}us) 应比 json.dumps ({old_stats['mean_us']}us) 更快"
        )

    def test_medium_payload_log_dict_faster(self):
        """中等 payload: log_dict 应比 json.dumps 更快"""
        payload = dict(MEDIUM_PAYLOAD)

        def old_way():
            return json.dumps(payload, ensure_ascii=False)

        def new_way():
            return log_dict(payload)

        old_times = _measure(old_way, self.ITERATIONS)
        new_times = _measure(new_way, self.ITERATIONS)

        old_stats = _stats(old_times)
        new_stats = _stats(new_times)
        self._print_comparison("中等 payload (10 字段，含嵌套)", old_stats, new_stats)

        assert new_stats["mean_us"] <= old_stats["mean_us"]

    def test_complex_payload_log_dict_faster(self):
        """复杂 payload: log_dict 应比 json.dumps 更快"""
        payload = dict(COMPLEX_PAYLOAD)

        def old_way():
            return json.dumps(payload, ensure_ascii=False)

        def new_way():
            return log_dict(payload)

        old_times = _measure(old_way, self.ITERATIONS)
        new_times = _measure(new_way, self.ITERATIONS)

        old_stats = _stats(old_times)
        new_stats = _stats(new_times)
        self._print_comparison("复杂 payload (15+ 字段，深度嵌套)", old_stats, new_stats)

        assert new_stats["mean_us"] <= old_stats["mean_us"]

    def test_full_pipeline_old_vs_new(self):
        """完整管道对比：包含 logger + filter + formatter 的完整流程

        旧模式: json.dumps(dict) → EmojiFilter → SensitiveDataFilter → Formatter(json.loads + 再处理)
        新模式: log_dict(dict) → EmojiFilter → SensitiveDataFilter → Formatter(直接用 dict)

        注意：完整管道的性能取决于 filter 对 dict 的处理效率。
        本测试用于诊断瓶颈，不强制要求新模式必须更快。
        """
        from scripts.struct_log_formatter import StructuredLogFormatter

        formatter = StructuredLogFormatter()
        emoji_filter = EmojiFilter()
        sensitive_filter = SensitiveDataFilter()

        payload = dict(MEDIUM_PAYLOAD)

        def old_full_pipeline():
            # 1. 调用方 json.dumps
            msg = json.dumps(payload, ensure_ascii=False)
            record = _make_record(msg)
            # 2. filter 链
            sensitive_filter.filter(record)
            emoji_filter.filter(record)
            # 3. formatter json.loads（模拟双重序列化）
            formatter.format(record)
            return record

        def new_full_pipeline():
            # 1. log_dict 返回 dict
            data = log_dict(payload)
            record = _make_record(data)
            # 2. filter 链
            sensitive_filter.filter(record)
            emoji_filter.filter(record)
            # 3. formatter 直接用 dict（快速路径）
            formatter.format(record)
            return record

        old_times = _measure(old_full_pipeline, self.ITERATIONS)
        new_times = _measure(new_full_pipeline, self.ITERATIONS)

        old_stats = _stats(old_times)
        new_stats = _stats(new_times)
        self._print_comparison("完整管道 (log_dict + filter + formatter)", old_stats, new_stats)

        speedup = old_stats["mean_us"] / new_stats["mean_us"] if new_stats["mean_us"] > 0 else 0
        print(f"完整管道加速比: {speedup:.2f}x")
        # 不强制断言完整管道更快 - 用于诊断瓶颈
        if new_stats["mean_us"] > old_stats["mean_us"]:
            print(f"[诊断] 完整管道新模式更慢，需优化 dict filter 处理")

    def test_pipeline_bottleneck_analysis(self):
        """管道瓶颈分析：分别测量每个步骤的耗时"""
        from scripts.struct_log_formatter import StructuredLogFormatter

        formatter = StructuredLogFormatter()
        emoji_filter = EmojiFilter()
        sensitive_filter = SensitiveDataFilter()
        payload = dict(MEDIUM_PAYLOAD)

        # 旧模式各步骤（每步生成自己的输入）
        def old_serialize():
            return json.dumps(payload, ensure_ascii=False)

        def old_sensitive():
            msg = json.dumps(payload, ensure_ascii=False)
            record = _make_record(msg)
            sensitive_filter.filter(record)
            return record.msg

        def old_emoji():
            msg = json.dumps(payload, ensure_ascii=False)
            record = _make_record(msg)
            emoji_filter.filter(record)
            return record.msg

        def old_format():
            msg = json.dumps(payload, ensure_ascii=False)
            record = _make_record(msg)
            return formatter.format(record)

        # 新模式各步骤
        def new_serialize():
            return log_dict(payload)

        def new_sensitive():
            data = log_dict(payload)
            record = _make_record(data)
            sensitive_filter.filter(record)
            return record.msg

        def new_emoji():
            data = log_dict(payload)
            record = _make_record(data)
            emoji_filter.filter(record)
            return record.msg

        def new_format():
            data = log_dict(payload)
            record = _make_record(data)
            return formatter.format(record)

        steps = [
            ("序列化 (serialize)", old_serialize, new_serialize),
            ("SensitiveDataFilter", old_sensitive, new_sensitive),
            ("EmojiFilter", old_emoji, new_emoji),
            ("Formatter", old_format, new_format),
        ]

        print(f"\n{'=' * 80}")
        print(f"管道瓶颈分析 (中等 payload, {self.ITERATIONS} 次迭代)")
        print(f"{'=' * 80}")
        print(f"{'步骤':<25} {'旧模式(us)':<15} {'新模式(us)':<15} {'差异(us)':<15} {'加速比':<10}")
        print(f"{'-' * 80}")

        for name, old_fn, new_fn in steps:
            old_times = _measure(old_fn, self.ITERATIONS)
            new_times = _measure(new_fn, self.ITERATIONS)
            old_s = _stats(old_times)
            new_s = _stats(new_times)
            diff = new_s["mean_us"] - old_s["mean_us"]
            speedup = old_s["mean_us"] / new_s["mean_us"] if new_s["mean_us"] > 0 else 0
            flag = "← 瓶颈" if diff > 0.5 else "✓ 提升" if diff < -0.5 else ""
            print(f"{name:<25} {old_s['mean_us']:<15.2f} {new_s['mean_us']:<15.2f} {diff:<+15.2f} {speedup:<10.2f}x {flag}")

        print(f"{'-' * 80}")
        print(f"{'=' * 80}\n")

    def test_file_handler_pipeline(self):
        """文件 handler 管道对比

        旧模式: json.dumps(dict) → EmojiFilter → SensitiveDataFilter → Formatter → 写文件
        新模式: log_dict(dict) → EmojiFilter → SensitiveDataFilter → DictToJsonFilter(json.dumps) → 写文件
        """
        payload = dict(MEDIUM_PAYLOAD)
        emoji_filter = EmojiFilter()
        sensitive_filter = SensitiveDataFilter()
        dict_to_json_filter = DictToJsonFilter()

        def old_file_pipeline():
            msg = json.dumps(payload, ensure_ascii=False)
            record = _make_record(msg)
            sensitive_filter.filter(record)
            emoji_filter.filter(record)
            # 文件 handler 直接写 JSON 字符串
            return record.msg

        def new_file_pipeline():
            data = log_dict(payload)
            record = _make_record(data)
            sensitive_filter.filter(record)
            emoji_filter.filter(record)
            dict_to_json_filter.filter(record)  # dict → JSON 字符串
            return record.msg

        old_times = _measure(old_file_pipeline, self.ITERATIONS)
        new_times = _measure(new_file_pipeline, self.ITERATIONS)

        old_stats = _stats(old_times)
        new_stats = _stats(new_times)
        self._print_comparison("文件 handler 管道 (含 DictToJsonFilter)", old_stats, new_stats)

        # 文件 handler 管道：新模式增加了 DictToJsonFilter 的 json.dumps，
        # 但消除了调用方的 json.dumps，总体应持平或略快
        # 关键收益在于 formatter 不再需要 json.loads
        print(f"文件 handler 管道对比 - 旧: {old_stats['mean_us']:.2f}us, 新: {new_stats['mean_us']:.2f}us")

    def test_high_frequency_scenario(self):
        """高频场景模拟：10000 次连续日志调用"""
        payload = dict(MEDIUM_PAYLOAD)
        iterations = 10000

        # 旧模式
        def old_way():
            json.dumps(payload, ensure_ascii=False)
        t0 = time.perf_counter()
        for _ in range(iterations):
            old_way()
        old_total = time.perf_counter() - t0

        # 新模式
        def new_way():
            log_dict(payload)
        t0 = time.perf_counter()
        for _ in range(iterations):
            new_way()
        new_total = time.perf_counter() - t0

        speedup = old_total / new_total if new_total > 0 else 0
        saved_ms = (old_total - new_total) * 1000
        per_call_saved_us = (old_total - new_total) / iterations * 1_000_000

        print(f"\n{'=' * 70}")
        print(f"高频场景模拟: {iterations} 次连续调用")
        print(f"{'=' * 70}")
        print(f"旧模式总耗时: {old_total*1000:.2f} ms ({old_total/iterations*1_000_000:.2f} us/call)")
        print(f"新模式总耗时: {new_total*1000:.2f} ms ({new_total/iterations*1_000_000:.2f} us/call)")
        print(f"总节省: {saved_ms:.2f} ms")
        print(f"单次节省: {per_call_saved_us:.2f} us")
        print(f"加速比: {speedup:.2f}x")
        print(f"{'=' * 70}\n")

        assert new_total <= old_total, (
            f"新模式 ({new_total*1000:.2f}ms) 应比旧模式 ({old_total*1000:.2f}ms) 更快"
        )


# ─────────────────────────────────────────────────
# TestCorrectness: 确保两种模式输出等价
# ─────────────────────────────────────────────────
class TestCorrectness:
    """确保新模式输出与旧模式语义等价"""

    def test_dict_contains_same_fields(self):
        """log_dict 输出应包含与 json.dumps 相同的字段"""
        old_data = dict(MEDIUM_PAYLOAD)
        new_data = log_dict(MEDIUM_PAYLOAD)

        # 新模式应包含所有旧字段（trace_id/duration_ms 由 log_dict 填充）
        for key in old_data:
            if key in ("trace_id", "duration_ms"):
                continue  # 这两个字段 log_dict 会自动填充
            assert key in new_data, f"新模式缺少字段: {key}"
            assert new_data[key] == old_data[key], f"字段 {key} 值不匹配"

    def test_file_output_json_parseable(self, tmp_path):
        """文件 handler 输出应是合法 JSON"""
        log_file = str(tmp_path / "perf.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.addFilter(EmojiFilter())
        file_handler.addFilter(DictToJsonFilter())
        file_handler.setFormatter(logging.Formatter("%(message)s"))

        test_logger = logging.getLogger("perf_test_file")
        test_logger.handlers = [file_handler]
        test_logger.setLevel(logging.INFO)

        test_logger.info(log_dict(MEDIUM_PAYLOAD))
        file_handler.flush()
        file_handler.close()

        with open(log_file, "r", encoding="utf-8") as f:
            line = f.read().strip()

        # 应是合法 JSON
        data = json.loads(line)
        assert data["module_name"] == MEDIUM_PAYLOAD["module_name"]
        assert data["action"] == MEDIUM_PAYLOAD["action"]

    def test_unicode_preserved(self):
        """中文等 Unicode 字符应正确保留"""
        data = log_dict({"message": "测试中文消息 🚀"})
        assert "测试中文消息" in data["message"]


# ─────────────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────────────
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
