"""ModelRouter 测试"""
import os
import threading
from datetime import date

from agent.model_router.router import ModelRouter
from agent.model_router.cost_tracker import CostTracker

class TestModelRouter:
    def setup_method(self):
        self.router = ModelRouter()

    def test_simple_uses_small_model(self):
        m = self.router.route("chat", "hello", 0)
        assert m in ("gpt-3.5-turbo", "gpt-4o-mini")

    def test_complex_uses_large_model(self):
        m = self.router.route("chat", "帮我分析这段代码的性能", 0)
        assert m == "gpt-4"

class TestCostTracker:
    def test_record_and_summary(self):
        t = CostTracker(log_path="./test_cost_log.jsonl")
        t.record("gpt-4", 100, 50, 500, "test")
        s = t.get_summary()
        assert s["total_calls"] >= 1


class TestCostTrackerConcurrency:
    """CostTracker 并发安全（threading.Lock 原子化）。

    修复前：record 的「if-in 初始化 → += 累加」为 TOCTOU + 读-改-写序列，
    多线程并发丢费用/调用计数（关键业务数据）。修复后：setdefault 原子初始化
    + 锁内累加，文件写入在锁外（持锁纪律）。
    """

    LOG_PATH = "./test_cost_concurrency.jsonl"

    def setup_method(self):
        if os.path.exists(self.LOG_PATH):
            os.remove(self.LOG_PATH)

    def teardown_method(self):
        if os.path.exists(self.LOG_PATH):
            os.remove(self.LOG_PATH)

    def test_concurrent_record_precise(self):
        """100 线程 × 50 次并发 record：calls/费用/token 精确无丢失"""
        t = CostTracker(log_path=self.LOG_PATH)
        n_threads, per = 100, 50
        total = n_threads * per
        # gpt-4: in=1000, out=1000 → cost = 1*0.03 + 1*0.06 = 0.09
        barrier = threading.Barrier(n_threads)  # 同步起跑，放大读-改-写竞争
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    t.record("gpt-4", 1000, 1000, 10.0, "test")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors
        s = t.get_summary()
        assert s["total_calls"] == total                       # 调用计数无丢失
        assert abs(s["total_cost_usd"] - total * 0.09) < 1e-3   # 费用精确（round 4 位）
        today = date.today().isoformat()
        assert len(s["daily"]) == 1 and today in s["daily"]     # 只有今天一个 entry
        daily = s["daily"][today]
        assert abs(daily["total_cost"] - total * 0.09) < 1e-6
        assert daily["total_tokens"] == total * 2000
        assert daily["calls"] == total

    def test_concurrent_first_record_single_init(self):
        """100 线程同时首次 record 同一天：daily 只初始化一次（TOCTOU 修复）"""
        t = CostTracker(log_path=self.LOG_PATH)
        n_threads = 100
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                t.record("gpt-4", 100, 100, 5.0, "test")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors
        s = t.get_summary()
        assert s["total_calls"] == n_threads
        assert len(s["daily"]) == 1
        assert s["daily"][date.today().isoformat()]["calls"] == n_threads

    def test_concurrent_mixed_models_cost_precise(self):
        """并发 record 不同模型：各模型费用按单价精确累加"""
        t = CostTracker(log_path=self.LOG_PATH)
        models = ["gpt-4", "gpt-3.5-turbo", "gpt-4o-mini"]
        per_model_threads = 7
        n_threads = per_model_threads * len(models)
        per = 20
        total = n_threads * per
        # gpt-4: 0.03+0.06=0.09；gpt-3.5-turbo: 0.0015+0.002=0.0035；gpt-4o-mini: 0.00015+0.0006=0.00075
        expected_cost = per_model_threads * per * (0.09 + 0.0035 + 0.00075)
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            model = models[tid % len(models)]
            try:
                barrier.wait()
                for _ in range(per):
                    t.record(model, 1000, 1000, 10.0, "test")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors
        s = t.get_summary()
        assert s["total_calls"] == total
        assert abs(s["total_cost_usd"] - expected_cost) < 1e-3

    def test_concurrent_readers_and_writers_no_crash(self):
        """get_summary 与 record 并发：不抛异常、计数守恒"""
        t = CostTracker(log_path=self.LOG_PATH)
        n_threads, per = 20, 50
        total = n_threads * per
        stop = threading.Event()
        errors = []

        def writer():
            try:
                for _ in range(per):
                    t.record("gpt-4", 1000, 1000, 10.0, "test")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    s = t.get_summary()
                    assert isinstance(s["total_calls"], int)
                    assert isinstance(s["daily"], dict)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=writer) for _ in range(n_threads)]
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for th in writers + readers:
            th.start()
        for th in writers:
            th.join()
        stop.set()
        for th in readers:
            th.join()

        assert not errors
        s = t.get_summary()
        assert s["total_calls"] == total
        assert abs(s["total_cost_usd"] - total * 0.09) < 1e-3
