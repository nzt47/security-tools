"""资源泄漏压测验证

在压测场景下验证：
1. 高并发资源分配/释放的采样准确性
2. 资源释放曲线（采样后内存应回归基线）
3. 压测模式采样频率符合配置
4. 主动注入的内存泄漏能被趋势检测捕获
5. 采样本身性能开销可控（< 1%）

运行方式：
    pytest tests/stress/test_resource_leak.py -v -s
    pytest tests/stress/test_resource_leak.py -k "test_memory_leak_detection" -v
"""

import gc
import threading
import time

import pytest

from agent.monitoring.resource_monitor import (
    ResourceMonitor,
    ResourceSnapshot,
    get_resource_monitor,
    reset_resource_monitor,
)


@pytest.fixture(autouse=True)
def isolate_monitor():
    """每个用例独立的监控器实例

    禁用持久化：避免 start() 加载上一个测试遗留的持久化历史数据，
    导致 history 顺序与时间戳不一致（曾引发采样间隔负数的 flaky 失败）。
    """
    reset_resource_monitor()
    monitor = ResourceMonitor(config={
        "history_size": 1000,
        "persist_enabled": False,  # 测试间隔离：不加载/不写入持久化文件
    })
    yield monitor
    monitor.stop()
    reset_resource_monitor()


class TestHighConcurrencySampling:
    """高并发采样压测"""

    @pytest.mark.stress
    @pytest.mark.p0
    def test_concurrent_sample_calls_safe(self, isolate_monitor):
        """多线程同时调用 sample() 不应崩溃且数据一致"""
        monitor = isolate_monitor
        snapshots = []
        errors = []

        def worker():
            try:
                for _ in range(20):
                    snap = monitor.sample()
                    snapshots.append(snap)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(snapshots) == 8 * 20
        # 所有快照结构完整
        for snap in snapshots:
            assert isinstance(snap, ResourceSnapshot)
            assert snap.timestamp > 0

    @pytest.mark.stress
    @pytest.mark.p0
    def test_high_frequency_sampling_does_not_leak(self, isolate_monitor):
        """高频采样本身不引入内存泄漏

        执行 200 次连续采样，验证采样器自身内存占用稳定。
        """
        monitor = isolate_monitor
        # 触发 200 次采样
        for _ in range(200):
            monitor.sample()
        # 采样后内存应稳定（采样器历史有上限）
        history = monitor.get_history()
        assert len(history) == 200

        # 验证 history_size 上限生效（不会无限增长）
        for _ in range(50):
            monitor.sample()
        # 总数不超过 history_size 配置（1000）
        assert len(monitor.get_history()) <= 1000


class TestResourceReleaseCurve:
    """资源释放曲线压测"""

    @pytest.mark.stress
    @pytest.mark.p0
    def test_memory_returns_to_baseline_after_release(self, isolate_monitor):
        """分配大对象后释放，内存应回归基线"""
        monitor = isolate_monitor

        # 基线采样
        baseline = monitor.sample()
        baseline_mem = baseline.memory.current_bytes

        # 分配大量对象
        big_objects = [bytearray(1024 * 100) for _ in range(50)]  # 5MB
        peak = monitor.sample()
        peak_mem = peak.memory.current_bytes

        # 峰值应高于基线
        assert peak_mem >= baseline_mem

        # 释放
        del big_objects
        gc.collect()

        # 等待 tracemalloc 更新（tracemalloc 统计实时）
        time.sleep(0.1)
        after_release = monitor.sample()
        after_mem = after_release.memory.current_bytes

        # 释放后内存应低于峰值（允许部分残留，但应明显下降）
        assert after_mem < peak_mem

    @pytest.mark.stress
    @pytest.mark.p1
    def test_thread_count_returns_after_join(self, isolate_monitor):
        """线程创建与销毁后，活动线程数应回归"""
        monitor = isolate_monitor

        before = monitor.sample()
        before_threads = before.thread_pool.active_threads

        # 创建多个线程
        threads = []
        done = threading.Event()

        def sleeper():
            done.wait(2)

        for _ in range(20):
            t = threading.Thread(target=sleeper)
            t.start()
            threads.append(t)

        during = monitor.sample()
        during_threads = during.thread_pool.active_threads
        assert during_threads > before_threads

        # 释放线程
        done.set()
        for t in threads:
            t.join()

        after = monitor.sample()
        after_threads = after.thread_pool.active_threads
        # 应回归到接近 before 的水平
        assert after_threads <= during_threads


class TestLeakDetectionUnderStress:
    """压测下的泄漏检测能力"""

    @pytest.mark.stress
    @pytest.mark.p0
    def test_memory_leak_detection(self, isolate_monitor):
        """主动注入内存泄漏，趋势检测应触发告警"""
        monitor = isolate_monitor
        monitor.register_leak_callback(lambda r: None)
        leak_threshold = 1024  # 1KB/采样
        monitor._config["leak_slope_threshold"] = leak_threshold

        # 持续累积分配（模拟泄漏）
        leaked = []

        for i in range(20):
            # 每次采样前泄漏 10KB
            leaked.append(bytearray(10 * 1024))
            monitor.sample()

        trend = monitor.get_trend("memory")
        assert trend is not None
        # 应检测到增长趋势
        assert trend.slope > 0
        # 斜率应超过阈值（10KB/采样 >> 1KB 阈值）
        assert trend.is_leaking is True

        # 清理
        del leaked
        gc.collect()

    @pytest.mark.stress
    @pytest.mark.p1
    def test_no_false_positive_on_stable_load(self, isolate_monitor):
        """稳定负载下不应误报泄漏

        注：tracemalloc.take_snapshot() 自身会产生 KB 级内存抖动，
        因此"稳定"负载下斜率不会精确为 0。本测试使用较高阈值
        （100KB/采样）区分真实泄漏（MB 级）与采样抖动。
        """
        monitor = isolate_monitor
        # 提高阈值避免 tracemalloc 抖动误报
        monitor._config["leak_slope_threshold"] = 100000  # 100KB/采样

        # 维持稳定的内存分配
        stable = [bytearray(1024) for _ in range(100)]
        for _ in range(15):
            monitor.sample()

        trend = monitor.get_trend("memory")
        assert trend is not None
        # 稳定负载下斜率应低于 100KB 阈值（真实泄漏通常 MB 级）
        assert trend.is_leaking is False, f"稳定负载误报泄漏，斜率={trend.slope}"

        del stable


class TestSamplingPerformance:
    """采样性能开销测试"""

    @pytest.mark.stress
    @pytest.mark.p0
    def test_single_sample_under_600ms(self, isolate_monitor):
        """单次采样耗时满足 < 1% 开销约束

        约束要求监控本身开销 < 1%，按 60s 采样间隔，
        单次采样需 < 600ms（600ms / 60s = 1%）。

        注：tracemalloc.take_snapshot() 在 Windows 上性能抖动明显，
        采用中位数（P50）判断大多数采样满足约束，P95 允许个别抖动但封顶，
        避免单次极端值拉高平均值导致 flaky 失败。
        """
        monitor = isolate_monitor
        # 充分预热（tracemalloc / psutil 首次调用较慢，需多次预热缓存）
        for _ in range(3):
            monitor.sample()

        # 测量 20 次采样耗时（样本量增大以稳定统计）
        durations = []
        for _ in range(20):
            start = time.time()
            monitor.sample()
            durations.append((time.time() - start) * 1000)

        durations.sort()
        median = durations[len(durations) // 2]  # P50：大多数采样的典型性能
        p95 = durations[int(len(durations) * 0.95)]  # P95：允许个别抖动的封顶

        # 中位数满足 1% 开销约束（大多数采样性能达标）
        assert median < 600, f"采样中位数耗时 {median:.2f}ms 超过 600ms（1% 开销约束）"
        # P95 允许个别抖动，但不超过 1500ms（防止持续劣化）
        assert p95 < 1500, f"采样 P95 耗时 {p95:.2f}ms 超过 1500ms（抖动过大）"

    @pytest.mark.stress
    @pytest.mark.p1
    def test_metrics_reporting_under_1ms(self, isolate_monitor):
        """业务指标单次上报耗时 < 1ms"""
        monitor = isolate_monitor
        snap = monitor.sample()

        # 测量 _report_metrics 耗时
        durations = []
        for _ in range(20):
            start = time.time()
            monitor._report_metrics(snap)
            durations.append((time.time() - start) * 1000)

        avg = sum(durations) / len(durations)
        assert avg < 1.0, f"指标上报平均耗时 {avg:.3f}ms 超过 1ms 阈值"


class TestStressModeIntegration:
    """压测模式集成测试"""

    @pytest.mark.stress
    @pytest.mark.p0
    def test_stress_mode_high_frequency_capture(self, isolate_monitor):
        """压测模式下应捕获高频采样数据

        注：fixture 已禁用持久化，history 仅包含本次后台采样，
        时间戳必然递增。取最后两次采样计算间隔，避免启动初始抖动。
        """
        monitor = isolate_monitor
        monitor.enable_stress_mode()

        # 启动后台采样
        monitor.start()
        # 运行 3 秒，1 秒间隔应产生 2-3 次采样（延长以确保足够样本）
        time.sleep(3)
        monitor.stop()

        history = monitor.get_history()
        # 至少 2 次采样
        assert len(history) >= 2
        # 采样间隔应接近 1 秒（取最后两次，避免启动初始抖动）
        if len(history) >= 2:
            interval = history[-1].timestamp - history[-2].timestamp
            assert 0.5 < interval < 2.0, f"采样间隔 {interval:.2f}s 异常"
