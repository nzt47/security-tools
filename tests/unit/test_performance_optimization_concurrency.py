"""DynamicOptimizer 采样率统计并发安全测试

验证锁化修复后的高并发场景（2026-08-13 并发审计 #2：_sample_count/_request_count
锁外自增丢计数，导致自适应采样率 actual_ratio 失真）：

1. 并发 should_sample（ratio=1.0）：_request_count/_sample_count 精确 == N
2. 并发 should_sample（ratio=0.0）：_sample_count == 0、_request_count == N
3. 调整周期触发重置后继续累计正确（重置与自增互斥无丢失）
"""

import threading
import time

from agent.monitoring.performance_optimization import (
    AdaptiveSampler,
    OptimizationConfig,
)


class TestPerformanceOptimizationConcurrency:
    """采样率统计并发安全测试"""

    @staticmethod
    def _make_optimizer(ratio: float = 1.0) -> AdaptiveSampler:
        opt = AdaptiveSampler(OptimizationConfig(default_sampling_ratio=ratio))
        # 禁用周期调整：隔离测试期间的自增计数（调整间隔设为极长）
        opt._adjustment_interval = 10**9
        return opt

    @staticmethod
    def _run_threads(target, args_list):
        """Barrier 同步起跑，放大竞争窗口"""
        barrier = threading.Barrier(len(args_list))
        errors = []

        def worker(arg):
            barrier.wait()
            try:
                target(arg)
            except Exception as e:  # noqa: BLE001 - 收集所有异常统一断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return errors

    def test_concurrent_should_sample_all_sampled_exact(self):
        """并发 should_sample（ratio=1.0 全采样）：两个计数均精确"""
        opt = self._make_optimizer(ratio=1.0)
        n_threads, per_thread = 16, 100

        errors = self._run_threads(
            lambda _: [opt.should_sample("t") for _ in range(per_thread)],
            list(range(n_threads)),
        )
        assert not errors, f"并发 should_sample 抛异常: {errors}"

        total = n_threads * per_thread
        assert opt._request_count == total, f"_request_count 应为 {total}，实际 {opt._request_count}"
        assert opt._sample_count == total, f"_sample_count 应为 {total}，实际 {opt._sample_count}"

    def test_concurrent_should_sample_none_sampled_exact(self):
        """并发 should_sample（ratio=0.0 全拒绝）：sample 恒 0、request 精确"""
        opt = self._make_optimizer(ratio=0.0)
        n_threads, per_thread = 8, 100

        errors = self._run_threads(
            lambda _: [opt.should_sample("t") for _ in range(per_thread)],
            list(range(n_threads)),
        )
        assert not errors, f"并发 should_sample 抛异常: {errors}"

        total = n_threads * per_thread
        assert opt._request_count == total, f"_request_count 应为 {total}，实际 {opt._request_count}"
        assert opt._sample_count == 0, f"_sample_count 应为 0，实际 {opt._sample_count}"

    def test_adjust_reset_then_recount_exact(self):
        """调整周期重置后继续累计：重置与自增互斥，计数不丢失"""
        opt = self._make_optimizer(ratio=1.0)
        opt._adjustment_interval = 1  # 覆盖 _make_optimizer 的禁用值，允许本次调整触发
        # 模拟上一周期已有计数，且已超过调整间隔
        opt._request_count = 100
        opt._sample_count = 100
        opt._last_adjustment = time.time() - 10

        opt._maybe_adjust()
        assert opt._request_count == 0, "调整后 _request_count 应被重置为 0"
        assert opt._sample_count == 0, "调整后 _sample_count 应被重置为 0"

        # 调整过程会降低采样比率（target_ratio=0.2），重置为 1.0 使后续全采样，
        # 仅验证「重置与自增互斥、计数不丢失」这一修复目标
        opt._sampler.update_ratio(1.0)

        # 重置后并发继续计数（与 _maybe_adjust 的重置互斥）
        n_threads = 8
        errors = self._run_threads(
            lambda _: opt.should_sample("t"),
            list(range(n_threads)),
        )
        assert not errors
        assert opt._request_count == n_threads, (
            f"重置后 _request_count 应为 {n_threads}，实际 {opt._request_count}"
        )
        assert opt._sample_count == n_threads, (
            f"重置后 _sample_count 应为 {n_threads}，实际 {opt._sample_count}"
        )
