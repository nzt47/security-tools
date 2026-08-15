"""cognitive/reflection 并发安全测试。

修复前：ReflectionEngine._retry_counts 的 evaluate「读 current → 判断 → 写/删」
为读-改-写序列，并发对同 task_id 会读到同一旧值后相互覆盖（重试计数丢失 →
永远达不到重试上限）。修复后：读-改-写整体 threading.Lock，配置读取
_get_max_retries 与 logger 在锁外（持锁纪律：锁内严禁 I/O）。
"""

import threading

from agent.cognitive.reflection import ReflectionEngine


class TestReflectionEngineConcurrency:
    """ReflectionEngine 并发读写（Lock 原子化）。"""

    def test_concurrent_retry_count_precise(self):
        """100 线程 × 50 次并发 evaluate 同 task_id（重试路径）：计数无丢失"""
        engine = ReflectionEngine()
        # 放大上限（> 总次数 5000），确保每次 evaluate 都走重试路径，验证读-改-写计数
        engine._get_max_retries = lambda: 10000
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    # 空输出 → score=0.5 → passed=False → 触发重试 +1
                    result = engine.evaluate("task-1", "输入文本", "", 100)
                    assert result.should_retry
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert engine.get_retry_count("task-1") == total   # 读-改-写计数无丢失

    def test_concurrent_evaluate_and_reset_no_crash(self):
        """并发 evaluate（重试）+ reset_retry：不抛异常、计数为非负整数"""
        engine = ReflectionEngine()
        engine._get_max_retries = lambda: 1000
        n_threads, per = 50, 100
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for _ in range(per):
                    engine.evaluate("task-x", "输入文本", "", 100)
                    if tid % 2 == 0:
                        engine.reset_retry("task-x")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"evaluate + reset 并发不应抛异常: {errors}"
        assert 0 <= engine.get_retry_count("task-x")

    def test_concurrent_cleanup_and_retry_consistent(self):
        """并发 passed（清理 del）+ retry（+1）混合：无 KeyError、状态一致"""
        engine = ReflectionEngine()
        engine._get_max_retries = lambda: 1000
        stop = threading.Event()
        errors = []

        def passer(_):
            try:
                for _ in range(200):
                    # 正常输出 → score=1.0 → passed → 触发清理 del
                    engine.evaluate("task-y", "输入文本", "正常输出内容", 100)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def retrier(_):
            try:
                while not stop.is_set():
                    # 空输出 → score=0.5 → 触发重试 +1
                    engine.evaluate("task-y", "输入文本", "", 100)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        passers = [threading.Thread(target=passer, args=(t,)) for t in range(4)]
        retriers = [threading.Thread(target=retrier, args=(t,)) for t in range(4)]
        for t in passers + retriers:
            t.start()
        for t in passers:
            t.join()
        stop.set()
        for t in retriers:
            t.join()

        assert not errors, f"清理与重试并发不应抛 KeyError: {errors}"
        assert 0 <= engine.get_retry_count("task-y")
