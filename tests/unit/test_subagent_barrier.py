"""TASK-S4-04 §4.2 并发屏障（并发上限 + 回压）与 §5.9 第三方默认隔离 单元测试

覆盖：
- 并发上限不变量：任一时刻 ``in_flight ≤ max_concurrency``；峰值可断言
- 回压：指数退避等待；``queue_timeout`` 到期抛 ``BackpressureTimeout``（显式失败）
- 纯回压模式（``queue_timeout=None``）：等待而不失败
- ``slot()`` 异常路径归还槽位；多还抛 ``ValueError``（不静默抬高上限）
- §5.9：第三方执行默认**无宿主网络 / 无 SSH agent / 无 $HOME**，且宿主云凭据不继承
"""

from __future__ import annotations

import threading
import time

import pytest

from agent.subagent.barrier import (
    BackpressureTimeout,
    ConcurrencyBarrier,
    ConcurrencyBarrierStats,
)
from agent.subagent.sandbox import (
    ISOLATION_ENV_BLOCKLIST_PREFIXES,
    ISOLATION_POLICY_ID,
    THIRD_PARTY_DENIED_CAPABILITIES,
    apply_isolation_env,
    isolation_env_overrides,
    isolation_policy_report,
)


# ════════════════════════════════════════════════════════════
#  并发屏障（§4.2）
# ════════════════════════════════════════════════════════════


class TestConcurrencyBarrierBasics:
    def test_rejects_non_positive_max(self):
        with pytest.raises(ValueError):
            ConcurrencyBarrier(max_concurrency=0)
        with pytest.raises(ValueError):
            ConcurrencyBarrier(max_concurrency=-1)

    def test_rejects_non_positive_queue_timeout(self):
        with pytest.raises(ValueError):
            ConcurrencyBarrier(max_concurrency=1, queue_timeout=0)
        with pytest.raises(ValueError):
            ConcurrencyBarrier(max_concurrency=1, queue_timeout=-1)

    def test_initial_state(self):
        gate = ConcurrencyBarrier(max_concurrency=3, name="t")
        assert gate.in_flight == 0
        assert gate.peak_in_flight == 0
        assert gate.saturated is False
        assert gate.name == "t"

    def test_single_slot_acquire_release(self):
        gate = ConcurrencyBarrier(max_concurrency=2)
        gate.acquire()
        assert gate.in_flight == 1
        gate.release()
        assert gate.in_flight == 0

    def test_slot_context_manager(self):
        gate = ConcurrencyBarrier(max_concurrency=1)
        with gate.slot():
            assert gate.in_flight == 1
        assert gate.in_flight == 0

    def test_slot_releases_on_exception(self):
        gate = ConcurrencyBarrier(max_concurrency=1)
        with pytest.raises(RuntimeError):
            with gate.slot():
                raise RuntimeError("boom")
        assert gate.in_flight == 0
        # 槽位确实归还：还能再取
        with gate.slot():
            assert gate.in_flight == 1

    def test_extra_release_raises(self):
        """BoundedSemaphore 语义：多还抛错，而不是悄悄抬高上限"""
        gate = ConcurrencyBarrier(max_concurrency=1)
        gate.acquire()
        gate.release()
        with pytest.raises(ValueError):
            gate.release()

    def test_saturated_property(self):
        gate = ConcurrencyBarrier(max_concurrency=1)
        gate.acquire()
        assert gate.saturated is True
        gate.release()
        assert gate.saturated is False


class TestConcurrencyInvariant:
    @pytest.mark.timeout(60)
    def test_peak_never_exceeds_limit(self):
        gate = ConcurrencyBarrier(max_concurrency=3)
        observed_peak = [0]
        lock = threading.Lock()

        def worker():
            with gate.slot():
                with lock:
                    observed_peak[0] = max(observed_peak[0], gate.in_flight)
                time.sleep(0.005)

        threads = [threading.Thread(target=worker) for _ in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert gate.peak_in_flight <= 3
        assert observed_peak[0] <= 3
        assert gate.in_flight == 0
        assert gate.stats().total_admitted == 24

    @pytest.mark.timeout(60)
    def test_serial_when_limit_is_one(self):
        gate = ConcurrencyBarrier(max_concurrency=1)
        concurrent = [0]
        peak = [0]
        lock = threading.Lock()

        def worker():
            with gate.slot():
                with lock:
                    concurrent[0] += 1
                    peak[0] = max(peak[0], concurrent[0])
                time.sleep(0.002)
                with lock:
                    concurrent[0] -= 1

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert peak[0] == 1


class TestBackpressure:
    @pytest.mark.timeout(60)
    def test_queue_timeout_raises_explicit_failure(self):
        gate = ConcurrencyBarrier(max_concurrency=1, queue_timeout=0.05)
        gate.acquire()
        try:
            start = time.time()
            with pytest.raises(BackpressureTimeout) as excinfo:
                gate.acquire()
            elapsed = time.time() - start
            assert excinfo.value.code == "E_BACKPRESSURE_TIMEOUT"
            assert excinfo.value.max_concurrency == 1
            assert elapsed >= 0.04
            assert gate.stats().total_rejected == 1
        finally:
            gate.release()

    @pytest.mark.timeout(60)
    def test_blocked_acquire_succeeds_after_release(self):
        gate = ConcurrencyBarrier(max_concurrency=1, queue_timeout=5.0)
        gate.acquire()
        holder = threading.Timer(0.05, gate.release)
        holder.start()
        try:
            waited_ms = gate.acquire()
            assert waited_ms >= 0
            assert gate.stats().total_waited == 1
        finally:
            holder.cancel()
            if gate.in_flight:
                gate.release()

    @pytest.mark.timeout(60)
    def test_pure_backpressure_mode_never_rejects(self):
        """``queue_timeout=None``：等待而不失败（并发上限 + 回压，不丢任务）"""
        gate = ConcurrencyBarrier(max_concurrency=1, queue_timeout=None)
        release_holder = threading.Event()
        holder_ready = threading.Event()

        def holder():
            gate.acquire()
            holder_ready.set()
            release_holder.wait(5)
            gate.release()

        thread = threading.Thread(target=holder)
        thread.start()
        assert holder_ready.wait(5)
        assert gate.in_flight == 1

        # 主线程在满载下 acquire：应**等待**而不是失败
        waiter_done = threading.Event()

        def waiter():
            gate.acquire()
            waiter_done.set()
            gate.release()

        wait_thread = threading.Thread(target=waiter)
        wait_thread.start()
        time.sleep(0.1)
        assert waiter_done.is_set() is False       # 确实在等（回压生效）
        release_holder.set()
        thread.join(5)
        wait_thread.join(5)
        assert waiter_done.is_set() is True
        assert gate.stats().total_rejected == 0
        assert gate.in_flight == 0

    def test_acquire_timeout_argument_overrides_default(self):
        gate = ConcurrencyBarrier(max_concurrency=1, queue_timeout=60.0)
        gate.acquire()
        try:
            with pytest.raises(BackpressureTimeout):
                gate.acquire(timeout=0.02)
        finally:
            gate.release()

    @pytest.mark.timeout(60)
    def test_waited_counter_counts_only_contended_admissions(self):
        gate = ConcurrencyBarrier(max_concurrency=2)
        gate.acquire()          # 无等待
        gate.acquire()          # 无等待
        gate.release()
        assert gate.stats().total_waited == 0
        assert gate.stats().total_admitted == 2


class TestBarrierStats:
    def test_stats_snapshot_fields(self):
        gate = ConcurrencyBarrier(max_concurrency=2, queue_timeout=1.0, name="d")
        with gate.slot():
            pass
        payload = gate.stats().to_dict()
        assert payload["name"] == "d"
        assert payload["max_concurrency"] == 2
        assert payload["total_admitted"] == 1
        assert payload["total_released"] == 1
        assert payload["in_flight"] == 0
        assert payload["peak_in_flight"] == 1
        assert payload["queue_timeout_seconds"] == 1.0
        assert payload["saturated"] is False

    def test_reset_stats_keeps_in_flight(self):
        gate = ConcurrencyBarrier(max_concurrency=2)
        gate.acquire()
        gate.reset_stats()
        assert gate.stats().total_admitted == 0
        assert gate.in_flight == 1
        gate.release()

    def test_stats_dataclass_defaults(self):
        assert ConcurrencyBarrierStats().to_dict()["total_admitted"] == 0

    def test_repr_mentions_capacity(self):
        assert "in_flight=0/2" in repr(ConcurrencyBarrier(max_concurrency=2))
