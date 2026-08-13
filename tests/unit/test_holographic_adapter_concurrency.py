"""HolographicAdapter 熔断计数并发安全测试

验证锁化修复后的高并发场景（2026-08-13 并发审计 #1：_vec_fail_count/_vec_available
读-改-写非原子，并发失败与探活重置交错丢计数、熔断判定不一致）：

1. 并发 _record_vec_failure（低于阈值）：_vec_fail_count 精确、未熔断
2. 并发失败达阈值：恰好第 5 次熔断（不早不晚）
3. 并发失败 + 探活重置交错：无异常且状态不变量成立
4. 重置后可重新计数（熔断 → 重置 → 再计数）
"""

import threading

from agent.memory.adapters.holographic_adapter import HolographicAdapter


class TestHolographicAdapterConcurrency:
    """熔断计数并发安全测试"""

    @staticmethod
    def _make_adapter(tmp_path):
        """构造自包含 adapter（sqlite-vec 不可用时自动降级，不抛异常）"""
        return HolographicAdapter(
            db_path=str(tmp_path / "holographic_concurrency_test.db"),
            enable_cache=False,
        )

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

    def test_concurrent_record_failure_below_threshold_exact(self, tmp_path):
        """并发失败（低于阈值 5）：_vec_fail_count 精确、未熔断"""
        adapter = self._make_adapter(tmp_path)
        adapter._vec_available = True
        n = 4

        errors = self._run_threads(lambda _: adapter._record_vec_failure(), list(range(n)))
        assert not errors, f"并发 _record_vec_failure 抛异常: {errors}"
        assert adapter._vec_fail_count == n, (
            f"_vec_fail_count 应为 {n}，实际 {adapter._vec_fail_count}"
        )
        assert adapter._vec_available is True, "低于阈值不应熔断"

    def test_concurrent_failure_trips_circuit_exactly(self, tmp_path):
        """并发失败恰好达阈值 5：第 5 次熔断（_vec_available 置 False）"""
        adapter = self._make_adapter(tmp_path)
        adapter._vec_available = True
        n = adapter._vec_fail_threshold  # 5

        errors = self._run_threads(lambda _: adapter._record_vec_failure(), list(range(n)))
        assert not errors, f"并发 _record_vec_failure 抛异常: {errors}"
        assert adapter._vec_fail_count == n, (
            f"_vec_fail_count 应为 {n}，实际 {adapter._vec_fail_count}"
        )
        assert adapter._vec_available is False, "达阈值后应已熔断"

    def test_concurrent_failure_and_reset_consistency(self, tmp_path):
        """并发失败 + 探活重置交错：无异常且状态不变量成立"""
        adapter = self._make_adapter(tmp_path)
        adapter._vec_available = True

        def fail_worker(_):
            for _ in range(30):
                adapter._record_vec_failure()

        def reset_worker(_):
            for _ in range(10):
                adapter._reset_vec_circuit()

        targets = [fail_worker] * 2 + [reset_worker] * 2
        errors = self._run_threads(
            lambda i: targets[i](i), list(range(len(targets)))
        )
        assert not errors, f"并发失败+重置抛异常: {errors}"
        # 状态不变量：_vec_available 为 True 时计数必然低于阈值（熔断后才置 False）
        if adapter._vec_available:
            assert adapter._vec_fail_count < adapter._vec_fail_threshold, (
                f"_vec_available=True 但 _vec_fail_count={adapter._vec_fail_count} 已达阈值"
            )
        else:
            assert adapter._vec_fail_count >= adapter._vec_fail_threshold, (
                f"_vec_available=False 但 _vec_fail_count={adapter._vec_fail_count} 未达阈值"
            )

    def test_reset_allows_recount(self, tmp_path):
        """熔断 → 重置 → 重新计数：重置后计数归零且可再次累计"""
        adapter = self._make_adapter(tmp_path)
        adapter._vec_available = True

        # 触发熔断
        errors = self._run_threads(
            lambda _: adapter._record_vec_failure(),
            list(range(adapter._vec_fail_threshold)),
        )
        assert not errors
        assert adapter._vec_available is False

        # 探活重置
        adapter._reset_vec_circuit()
        assert adapter._vec_fail_count == 0, "重置后 _vec_fail_count 应为 0"
        assert adapter._vec_available is True, "重置后应恢复可用"

        # 重置后重新计数（未达阈值不熔断）
        adapter._record_vec_failure()
        assert adapter._vec_fail_count == 1, "重置后首次失败计数应为 1"
        assert adapter._vec_available is True, "未达阈值不应再次熔断"
