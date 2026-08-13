"""safety_guard 并发安全测试。

修复前：模块级 _alert_callbacks 的 register_alert_callback（任意线程 append）与
_record_alert（告警线程迭代）并发会 RuntimeError（list changed size during
iteration），且迭代中锁内执行外部回调（持锁纪律风险）；实例级 _blocked_count /
_warned_count 读-改-写并发丢计数。修复后：模块级回调列表 Lock 保护（锁内仅
变更/快照），实例级 RLock 保护计数与历史（锁内仅内存操作），回调锁外派发。
"""

import threading
import time

from agent import safety_guard as sg
from agent.safety_guard import SafetyGuard, register_alert_callback


class TestSafetyGuardConcurrency:
    """SafetyGuard 并发读写（RLock + 模块级回调锁）。"""

    def setup_method(self):
        # 模块级 _alert_callbacks 为全局共享，测试前后备份/恢复避免污染
        self._orig_callbacks = list(sg._alert_callbacks)
        sg._alert_callbacks.clear()

    def teardown_method(self):
        sg._alert_callbacks[:] = self._orig_callbacks

    @staticmethod
    def _make_guard():
        guard = SafetyGuard()
        guard.add_keyword("并发危险指令", "test", level="critical")
        return guard

    def test_concurrent_check_count_precise(self):
        """100 线程 × 50 次 check（critical 命中）：blocked 计数与告警历史精确"""
        guard = self._make_guard()
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    r = guard.check("执行 并发危险指令")
                    assert r["level"] == "critical"
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = guard.get_stats()
        assert stats["blocked_count"] == total             # 读-改-写计数无丢失
        assert stats["warned_count"] == 0
        # 告警历史按 _max_alerts 截断（200），非全量——验证截断不越界
        assert stats["total_alerts"] == min(total, guard._max_alerts)
        assert len(guard.get_alerts(limit=guard._max_alerts)) == min(total, guard._max_alerts)

    def test_concurrent_register_and_check_no_crash(self):
        """register_alert_callback + check 并发：不抛 RuntimeError（快照迭代）"""
        guard = self._make_guard()
        received = []
        sg.register_alert_callback(lambda alert: received.append(alert["level"]))
        stop = threading.Event()
        errors = []

        def registrar(_):
            try:
                for _ in range(200):
                    # 并发 append 扩容（与 check 触发 _record_alert 的快照迭代互斥）
                    sg.register_alert_callback(lambda a: None)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def checker(_):
            try:
                while not stop.is_set():
                    guard.check("执行 并发危险指令")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        registrars = [threading.Thread(target=registrar, args=(t,)) for t in range(4)]
        checkers = [threading.Thread(target=checker, args=(t,)) for t in range(4)]
        for t in registrars + checkers:
            t.start()
        for t in registrars:
            t.join()
        stop.set()
        for t in checkers:
            t.join()

        assert not errors, f"注册/检查并发不应抛 RuntimeError: {errors}"
        assert len(received) > 0                          # 回调确实被派发
        # 全局回调数 = 1（预置 lambda）+ 4×200（注册线程）
        assert len(sg._alert_callbacks) == 1 + 4 * 200

    def test_concurrent_register_callbacks_no_loss(self):
        """100 线程 × 10 次并发注册：回调注册无丢失"""
        n_threads, per = 100, 10
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    register_alert_callback(lambda a: None)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(sg._alert_callbacks) == total          # append 无丢失

    def test_callback_dispatched_outside_lock(self):
        """回调在锁外并发派发：慢回调不串行阻塞 check（持锁纪律验证）"""
        guard = self._make_guard()
        n_threads = 20
        elapsed_total = [0.0]

        def slow_cb(_alert):
            time.sleep(0.1)  # 模拟慢外部回调（日志/上报等 I/O）

        register_alert_callback(slow_cb)
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            guard.check("执行 并发危险指令")

        t0 = time.time()
        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        # 锁内串行派发 = 20 × 0.1 = 2.0s+；锁外并发 ≈ 0.15s。1.5s 阈值可区分。
        assert elapsed < 1.5, \
            f"回调应在锁外并发派发（elapsed={elapsed:.2f}s，锁内串行需 ≥2.0s）"
