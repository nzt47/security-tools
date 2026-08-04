"""reranker_utils 并发闸门单元测试

验证 concurrency_slot 的核心语义：
1. 信号量满时 acquire(timeout) 超时返回 False（不阻塞、不抛错）
2. 成功路径自动 release（释放后额度恢复）
3. 并发上限可配（env > 默认 5）+ 热重载重建
4. 非法 env 回退默认

模块级信号量为全局状态，每个测试前重置（隔离跨测试污染）。
"""

import os
import threading
import time

import pytest

from agent.skills_mgmt import reranker_utils as ru


@pytest.fixture(autouse=True)
def _reset_gate(monkeypatch):
    """重置模块级信号量（容量默认 5），隔离全局状态"""
    monkeypatch.delenv(ru._MAX_CONCURRENCY_ENV, raising=False)
    ru._sem = None
    ru._sem_capacity = -1
    yield
    ru._sem = None
    ru._sem_capacity = -1


# ═══════════════════════════════════════════════════════════
# 信号量满时 acquire 超时 → False（核心场景）
# ═══════════════════════════════════════════════════════════

class TestConcurrencySlot:
    def test_默认并发上限_5(self):
        assert ru.get_max_concurrency() == 5

    def test_env覆盖并发上限(self, monkeypatch):
        monkeypatch.setenv(ru._MAX_CONCURRENCY_ENV, "3")
        assert ru.get_max_concurrency() == 3

    def test_非法env回退默认(self, monkeypatch):
        monkeypatch.setenv(ru._MAX_CONCURRENCY_ENV, "abc")
        assert ru.get_max_concurrency() == 5

    def test_并发上限下限为1(self, monkeypatch):
        monkeypatch.setenv(ru._MAX_CONCURRENCY_ENV, "0")
        assert ru.get_max_concurrency() == 1

    def test_信号量满时acquire超时返回False(self):
        """核心场景：容量 1 被占用时，第二个 acquire(0.2) 超时 → False 且不抛错"""
        ru.set_max_concurrency(1)
        acquired = []

        def holder():
            with ru.concurrency_slot(10) as ok:
                assert ok is True  # 第一个成功获得额度
                acquired.append(ok)
                time.sleep(0.5)  # 持有期间模拟推理

        t = threading.Thread(target=holder)
        t.start()
        time.sleep(0.1)  # 确保 holder 已获得额度

        # 主线程尝试获取 → 信号量满 → 应在 0.2s 内返回 False（不阻塞 0.5s+）
        start = time.perf_counter()
        with ru.concurrency_slot(0.2) as ok:
            elapsed = time.perf_counter() - start
            assert ok is False  # 核心断言：超时返回 False
        assert elapsed < 0.4  # 及时返回（明显短于 holder 的 0.5s 持有期）
        t.join()

    def test_成功路径自动release(self):
        """释放后额度恢复：先占先放，后续 acquire 应立即成功"""
        ru.set_max_concurrency(1)
        # 第一次成功并释放
        with ru.concurrency_slot(1) as ok:
            assert ok is True
        # 第二次（此时额度已恢复）应立刻成功
        with ru.concurrency_slot(0.1) as ok:
            assert ok is True

    def test_容量2_并发3_恰好2成功(self):
        """并发上限边界：容量 2 下 3 个并发请求 → 恰好 2 个成功、1 个超时

        Barrier 同步 3 线程同时竞争；持有期(0.6s) > acquire 窗口(0.2s)，
        保证第 3 个线程在窗口内必然无人释放 → 超时 False。
        """
        ru.set_max_concurrency(2)
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def worker():
            barrier.wait()  # 3 个线程同时开始竞争
            with ru.concurrency_slot(0.2) as ok:
                with lock:
                    results.append(ok)
                if ok:
                    time.sleep(0.6)  # 持有期 > 超时窗口，确保第 3 个必超时

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 2
        assert results.count(False) == 1

    def test_热重载_容量变化生效(self):
        """set_max_concurrency 重建信号量：1 → 3 后并发 3 全部成功"""
        ru.set_max_concurrency(1)
        # 触发 _ensure_semaphore 重建（容量 1）
        with ru.concurrency_slot(1):
            pass
        ru.set_max_concurrency(3)
        results = []
        lock = threading.Lock()

        def worker():
            with ru.concurrency_slot(1.0) as ok:
                with lock:
                    results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == [True, True, True]

    def test_重置为env默认(self, monkeypatch):
        """set_max_concurrency(0) 清除 env → 回到默认 5"""
        ru.set_max_concurrency(0)
        assert os.environ.get(ru._MAX_CONCURRENCY_ENV) is None
        assert ru.get_max_concurrency() == 5
