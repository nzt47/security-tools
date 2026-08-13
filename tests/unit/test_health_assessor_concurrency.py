"""health/assessor 并发安全测试。

修复前：HealthAssessor._history 的 append + pop(0) 截断为读-改-写序列，模块级
单例 health_assessor 被路由/采集/自愈多路并发调用时可能 IndexError（pop from
empty list）或历史错乱（自愈判定依据被污染）。修复后：assess /
assess_with_probes / get_history 统一 threading.Lock，锁内仅内存列表变更与纯
计算（持锁纪律：无 I/O）。
"""

import threading
from types import SimpleNamespace

from agent.health.assessor import HealthAssessor, DEFAULT_WEIGHTS


def _probe(score: float, available: bool = True, detail: str = "ok"):
    return SimpleNamespace(score=score, available=available, detail=detail)


class TestHealthAssessorConcurrency:
    """HealthAssessor 并发读写（Lock 原子化）。"""

    def test_concurrent_assess_truncation_precise(self):
        """100 线程 × 50 次并发 assess：无 IndexError、历史精确截断到 100"""
        assessor = HealthAssessor()
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    score = assessor.assess({"avg_response_ms": 200, "error_rate": 0.05})
                    assert 0.0 <= score.overall <= 1.0
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 assess 不应抛 IndexError: {errors}"
        assert len(assessor._history) == 100                # 截断到上限，不越界
        assert len(assessor.get_history(100)) == 100        # 读取快照一致
        # 全部为有效评分（无半写入/错乱条目）
        assert all(0.0 <= s.overall <= 1.0 for s in assessor._history)

    def test_concurrent_assess_high_contention(self):
        """放大竞争：100 线程 × 200 次 = 20000 次并发，截断恒成立"""
        assessor = HealthAssessor()
        n_threads, per = 100, 200
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    assessor.assess({"avg_response_ms": 8000, "error_rate": 0.15})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"高竞争并发不应抛异常: {errors}"
        assert len(assessor._history) == 100

    def test_concurrent_assess_with_probes_overall_precise(self):
        """100 线程 × 50 次并发探针评估：overall 与权重归一化公式完全一致"""
        assessor = HealthAssessor()
        n_threads, per = 100, 50
        probes = {
            "l1_process": _probe(0.9),
            "l2_dependency": _probe(0.8),
            "l3_llm_tool": _probe(0.7),
            "l4_business": _probe(0.6),
            "l5_semantic": _probe(0.5),
        }
        total_w = sum(DEFAULT_WEIGHTS.values())
        expected = sum(DEFAULT_WEIGHTS[l] * p.score for l, p in probes.items()) / total_w

        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    score = assessor.assess_with_probes(probes)
                    assert score.overall == expected
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(assessor._history) == 100
        assert len(assessor.get_history(50)) == 50

    def test_concurrent_read_write_mixed(self):
        """4 写（assess）+ 4 读（get_history）：不崩溃、最终历史一致"""
        assessor = HealthAssessor()
        stop = threading.Event()
        errors = []

        def writer(_):
            try:
                for _ in range(100):  # 有限次数（否则与 stop 竞争永远不退出）
                    assessor.assess({"avg_response_ms": 100, "error_rate": 0.01})
                    assessor.assess_with_probes({"l1_process": _probe(1.0)})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader(_):
            try:
                while not stop.is_set():
                    h = assessor.get_history(20)
                    assert len(h) <= 20
                    assert all(s.overall is None or 0.0 <= s.overall <= 1.0 for s in h)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        readers = [threading.Thread(target=reader, args=(t,)) for t in range(4)]
        for t in writers + readers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        for t in readers:
            t.join()

        assert not errors, f"读写并发不应抛异常: {errors}"
        assert len(assessor._history) == 100
