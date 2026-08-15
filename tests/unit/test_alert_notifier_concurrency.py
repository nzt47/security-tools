"""AlertNotifier 并发安全测试

验证锁外发送修复（2026-08-13 并发审计 A/C：sender.send 网络 I/O 移出锁外，
锁内仅取 sender 快照）：

1. 并发 send_notification：结果数精确、history 完整、stats 计数精确
2. 并发 send_critical：结果数 == critical 渠道数、无异常
3. 慢渠道不阻塞其他线程（send 锁外的核心验证——串行/并行计时差异）
4. 并发发送 history 截断一致（超过 max_history 后截断）
"""

import threading
import time

from agent.monitoring.alert_notifier import (
    AlertNotifier,
    AlertNotification,
    NotificationResult,
)


class MockSender:
    """测试用通知渠道（可模拟延迟）"""

    def __init__(self, delay: float = 0.0, channel: str = "mock"):
        self.calls = 0
        self.delay = delay
        self.channel = channel
        self._lock = threading.Lock()

    def send(self, notification):
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.calls += 1
        return NotificationResult(True, self.channel, "ok")


def make_notification() -> AlertNotification:
    return AlertNotification(
        alert_name="test",
        state="firing",
        severity="warning",
        message="test",
        value=1.0,
        threshold=2.0,
    )


class TestAlertNotifierConcurrency:
    """AlertNotifier 并发安全测试"""

    @staticmethod
    def _make_notifier(channels=None) -> AlertNotifier:
        notifier = AlertNotifier({"default_receiver": "default-notifications"})
        for name in channels or ["default-notifications"]:
            notifier._senders[name] = MockSender(channel=name)
        return notifier

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

    def test_concurrent_send_notification_results_exact(self):
        """并发 send_notification：结果数、history、stats 均精确（无丢失）"""
        notifier = self._make_notifier()
        # 发送总量 80 < _max_history(100)：验证 history 完整记录不丢失（截断语义由
        # test_history_truncation_consistent 单独覆盖）
        n_threads, per_thread = 8, 10
        total_ok = [0]
        ok_lock = threading.Lock()

        def worker(_):
            for _ in range(per_thread):
                results = notifier.send(make_notification())
                assert len(results) == 1, f"应返回 1 个结果，实际 {len(results)}"
                assert results[0].success, "发送应成功"
                with ok_lock:
                    total_ok[0] += 1

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"并发 send_notification 抛异常: {errors}"

        total = n_threads * per_thread
        assert total_ok[0] == total, f"成功发送应 {total} 次，实际 {total_ok[0]}"
        assert len(notifier._history) == total, (
            f"history 应记录 {total} 条，实际 {len(notifier._history)}"
        )
        stats = notifier.get_stats()
        assert stats["total"] == total, f"stats.total 应为 {total}，实际 {stats['total']}"
        assert stats["success"] == total, f"stats.success 应为 {total}，实际 {stats['success']}"

    def test_concurrent_send_critical_no_error(self):
        """并发 send_critical：结果数 == critical 渠道数、无异常"""
        notifier = self._make_notifier(
            channels=["critical-alerts", "critical-alerts-2", "info-alerts"]
        )
        n_threads = 8

        def worker(_):
            results = notifier.send_critical(make_notification())
            assert len(results) == 2, f"critical 渠道应 2 个，实际 {len(results)}"
            assert all(r.success for r in results)

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"并发 send_critical 抛异常: {errors}"
        # 每个 critical 渠道都被调用 n_threads 次
        for name in ("critical-alerts", "critical-alerts-2"):
            assert notifier._senders[name].calls == n_threads, (
                f"{name} 应被调用 {n_threads} 次，实际 {notifier._senders[name].calls}"
            )

    def test_slow_sender_does_not_block_others(self):
        """核心验证 A：send 出锁后慢渠道不阻塞并发发送

        4 线程同时发 slow 渠道（delay=0.2s）：若 send 持锁则串行约 0.8s，
        锁外则并行约 0.2s。阈值 0.6s 区分两者。
        """
        notifier = self._make_notifier(channels=["slow"])
        notifier._senders["slow"] = MockSender(delay=0.2, channel="slow")
        n_threads = 4
        barrier = threading.Barrier(n_threads)
        errors = []
        start = time.time()

        def worker(_):
            barrier.wait()
            try:
                notifier.send(make_notification(), receivers=["slow"])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start

        assert not errors, f"慢渠道发送抛异常: {errors}"
        assert elapsed < 0.2 * n_threads * 0.75, (
            f"send 应并行执行（锁外）：串行约 {0.2 * n_threads}s，实际 {elapsed:.3f}s"
        )
        assert notifier._senders["slow"].calls == n_threads, (
            f"slow 渠道应被调用 {n_threads} 次，实际 {notifier._senders['slow'].calls}"
        )

    def test_history_truncation_consistent(self):
        """并发发送 history 截断一致：超过 max_history 后精确截断"""
        notifier = self._make_notifier()
        notifier._max_history = 20
        n_threads, per_thread = 4, 20  # 共 80 次发送 > max_history

        def worker(_):
            for _ in range(per_thread):
                notifier.send(make_notification())

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"并发发送抛异常: {errors}"
        assert len(notifier._history) == notifier._max_history, (
            f"history 应截断为 {notifier._max_history} 条，实际 {len(notifier._history)}"
        )
        stats = notifier.get_stats()
        assert stats["total"] == notifier._max_history, (
            f"stats.total 应截断为 {notifier._max_history}，实际 {stats['total']}"
        )
