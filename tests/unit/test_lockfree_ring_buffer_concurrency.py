"""LockFreeRingBuffer 并发安全测试

验证 RLock 锁化修复后的高并发场景（2026-08-13 并发审计发现的
多生产者/多消费者竞态：_head/_tail/_count 读-改-写非原子导致丢元素/丢计数）：

1. 并发 push：_push_count/_count 精确、元素无丢失无重复
2. 并发 drain（多消费者）：汇总元素不重复不丢失
3. 满容量并发 push：成功数 + overflow 数 == 总尝试数
4. 混合并发（push + drain）：最终计数一致性
5. BatchProcessor.submit 并发：所有成功提交均被消费
"""

import threading

from agent.monitoring.performance_optimization import (
    LockFreeRingBuffer,
    BatchProcessor,
    OptimizationConfig,
)


class TestLockFreeRingBufferConcurrency:
    """LockFreeRingBuffer 并发安全测试"""

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

    def test_concurrent_push_count_exact(self):
        """并发 push：计数精确、元素无丢失无重复"""
        buf = LockFreeRingBuffer(capacity=4096)
        n_threads, per_thread = 16, 200
        submitted = set()
        submitted_lock = threading.Lock()

        def worker(tid):
            for i in range(per_thread):
                item = (tid, i)
                assert buf.push(item), "容量充足时 push 不应失败"
                with submitted_lock:
                    submitted.add(item)

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"并发 push 抛异常: {errors}"

        total = n_threads * per_thread
        assert buf._push_count == total, f"_push_count 应为 {total}，实际 {buf._push_count}"
        assert buf._count == total, f"_count 应为 {total}，实际 {buf._count}"

        drained = buf.drain()
        assert len(drained) == total, f"drain 应取回 {total} 个元素，实际 {len(drained)}"
        assert set(drained) == submitted, "drain 元素与提交集合不一致（丢失或重复）"
        assert buf._count == 0, f"drain 后 _count 应为 0，实际 {buf._count}"

    def test_concurrent_drain_no_loss(self):
        """多消费者并发 drain：汇总元素不重复不丢失"""
        buf = LockFreeRingBuffer(capacity=4096)
        total = 2000
        for i in range(total):
            assert buf.push(("item", i))

        n_consumers = 8
        collected = []
        collected_lock = threading.Lock()

        def worker(_):
            items = buf.drain()
            with collected_lock:
                collected.extend(items)

        errors = self._run_threads(worker, list(range(n_consumers)))
        assert not errors, f"并发 drain 抛异常: {errors}"

        assert len(collected) == total, f"应取回 {total} 个元素，实际 {len(collected)}"
        assert len(set(collected)) == total, "元素存在重复（同一元素被多个消费者取走）"
        assert buf._count == 0, f"drain 后 _count 应为 0，实际 {buf._count}"

    def test_concurrent_push_overflow_exact(self):
        """满容量并发 push：成功数 + overflow 数 == 总尝试数"""
        buf = LockFreeRingBuffer(capacity=64)
        n_threads, per_thread = 8, 200
        ok = 0
        overflow = 0
        counter_lock = threading.Lock()

        def worker(tid):
            nonlocal ok, overflow
            for i in range(per_thread):
                if buf.push((tid, i)):
                    with counter_lock:
                        ok += 1
                else:
                    with counter_lock:
                        overflow += 1

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"满容量并发 push 抛异常: {errors}"

        total = n_threads * per_thread
        assert ok + overflow == total, f"ok+overflow 应为 {total}，实际 {ok + overflow}"
        assert ok == buf._push_count, f"_push_count 应为 {ok}，实际 {buf._push_count}"
        assert overflow == buf._overflow_count, f"_overflow_count 应为 {overflow}，实际 {buf._overflow_count}"
        assert ok <= buf._capacity, f"成功数 {ok} 不应超过容量 {buf._capacity}"
        assert buf._count == ok, f"队列剩余应等于成功数 {ok}，实际 {buf._count}"

    def test_mixed_push_drain_consistency(self):
        """生产/消费混合并发：最终计数一致性"""
        buf = LockFreeRingBuffer(capacity=512)
        stop = threading.Event()
        drained_total = 0
        stats_lock = threading.Lock()
        errors = []

        def producer(pid):
            for i in range(500):
                try:
                    buf.push((pid, i))
                except Exception as e:  # noqa: BLE001
                    with stats_lock:
                        errors.append(e)

        def consumer(_):
            nonlocal drained_total
            while not (stop.is_set() and buf.is_empty()):
                try:
                    items = buf.drain()
                    with stats_lock:
                        drained_total += len(items)
                except Exception as e:  # noqa: BLE001
                    with stats_lock:
                        errors.append(e)

        producers = [threading.Thread(target=producer, args=(i,)) for i in range(2)]
        consumers = [threading.Thread(target=consumer, args=(i,)) for i in range(2)]
        for t in consumers + producers:
            t.start()
        for t in producers:
            t.join()
        stop.set()
        for t in consumers:
            t.join()

        assert not errors, f"混合并发抛异常: {errors}"
        remaining = buf.drain()  # 消费者退出后应已清空
        assert drained_total + len(remaining) == 1000, (
            f"消费总数应为 1000，实际 drain={drained_total} 剩余={len(remaining)}"
        )
        assert buf._push_count == 1000, f"_push_count 应为 1000，实际 {buf._push_count}"
        assert buf._pop_count == 1000, f"_pop_count 应为 1000，实际 {buf._pop_count}"
        assert buf._count == 0, f"最终 _count 应为 0，实际 {buf._count}"

    def test_batch_processor_concurrent_submit_no_loss(self):
        """BatchProcessor.submit 并发：所有成功提交均被消费（无丢失）"""
        config = OptimizationConfig(
            batch_size=1000,
            flush_interval_ms=600000,  # 长间隔，禁用后台定时 flush 干扰
            max_queue_size=4096,
        )
        consumed = []
        consumed_lock = threading.Lock()

        def process_func(batch):
            with consumed_lock:
                consumed.extend(batch)

        processor = BatchProcessor(process_func, config)
        processor.start()

        n_threads, per_thread = 16, 100
        submitted_ok = []
        ok_lock = threading.Lock()

        def worker(tid):
            for i in range(per_thread):
                item = (tid, i)
                assert processor.submit(item), "容量充足时 submit 不应失败"
                with ok_lock:
                    submitted_ok.append(item)

        errors = self._run_threads(worker, list(range(n_threads)))
        processor.stop()

        assert not errors, f"并发 submit 抛异常: {errors}"
        assert len(submitted_ok) == n_threads * per_thread, (
            f"应有 {n_threads * per_thread} 个提交成功，实际 {len(submitted_ok)}"
        )
        assert len(consumed) == len(submitted_ok), (
            f"消费数 {len(consumed)} 应等于提交成功数 {len(submitted_ok)}（元素丢失）"
        )
        assert set(consumed) == set(submitted_ok), "消费元素与提交集合不一致"
