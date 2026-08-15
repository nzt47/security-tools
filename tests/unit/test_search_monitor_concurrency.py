"""搜索引擎性能监控模块并发安全测试

验证 SearchPerformanceMonitor 锁化修复后的高并发场景：
1. 并发 run_manual_check 计数精确、check_id 唯一（读-改-写原子）
2. 并发读写混合（check + status + history + summary）不抛 RuntimeError
3. 并发 start 只启动一个监控线程（TOCTOU 防重复启动）
4. 并发 set_interval + get_status 最终状态一致
"""

import os
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from agent.monitoring.search import SearchPerformanceMonitor


class TestSearchPerformanceMonitorConcurrency:
    """并发安全测试（mock 网络请求，隔离性能数据文件）"""

    N_THREADS = 24

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="search_monitor_test_")
        self.data_file = os.path.join(self.temp_dir, "search_performance.json")
        self._patcher = patch("agent.monitoring.search.PERFORMANCE_DATA_FILE",
                              self.data_file)
        self._patcher.start()
        self.monitor = SearchPerformanceMonitor(base_url="http://localhost:5678")

    def teardown_method(self):
        try:
            self.monitor.stop()
        except Exception:
            pass
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _run_threads(target, args_list):
        """Barrier 同步起跑，放大竞争窗口"""
        barrier = threading.Barrier(len(args_list))
        results = []
        errors = []

        def worker(arg):
            barrier.wait()
            try:
                results.append(target(arg))
            except Exception as e:  # noqa: BLE001 - 收集所有异常统一断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_concurrent_manual_check_count_exact(self):
        """并发 run_manual_check：check_count 精确、check_id 唯一、history 完整"""
        with patch("requests.post") as mock_post, \
             patch("requests.get") as mock_get:
            mock_post.return_value.json.return_value = {"ok": True}
            mock_get.return_value.json.return_value = {
                "ok": True, "results": [],
            }

            results, errors = self._run_threads(
                lambda i: self.monitor.run_manual_check(),
                list(range(self.N_THREADS)),
            )

        assert not errors, f"并发 run_manual_check 抛异常: {errors}"
        assert self.monitor._check_count == self.N_THREADS, (
            f"check_count 应为 {self.N_THREADS}，实际 {self.monitor._check_count}"
        )

        status = self.monitor.get_status()
        assert status["check_count"] == self.N_THREADS
        assert status["history_count"] == self.N_THREADS

        history = self.monitor.get_recent_history(self.N_THREADS)
        check_ids = [r["check_id"] for r in history]
        assert len(set(check_ids)) == self.N_THREADS, (
            f"check_id 应唯一，实际出现重复: {len(check_ids)} != {len(set(check_ids))}"
        )

    def test_concurrent_read_write_mix_no_runtime_error(self):
        """并发 check + 快照读取混合，不抛 RuntimeError"""
        with patch("requests.post") as mock_post, \
             patch("requests.get") as mock_get:
            mock_post.return_value.json.return_value = {"ok": True}
            mock_get.return_value.json.return_value = {
                "ok": True, "results": [],
            }

            def writer(i):
                self.monitor.run_manual_check()

            def reader(i):
                self.monitor.get_status()
                self.monitor.get_recent_history(5)
                self.monitor.get_performance_summary()

            barrier = threading.Barrier(self.N_THREADS)
            errors = []

            def worker(fn, arg):
                barrier.wait()
                try:
                    fn(arg)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = []
            for i in range(self.N_THREADS):
                fn = writer if i % 2 == 0 else reader
                threads.append(threading.Thread(target=worker, args=(fn, i)))
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"并发读写混合抛异常: {errors}"
        assert self.monitor._check_count == self.N_THREADS // 2

    def test_concurrent_start_single_thread(self):
        """并发 start 只启动一个监控线程（TOCTOU 防重复启动）"""
        # 实例属性遮蔽 _monitor_loop：模拟运行中的监控循环（避免真实网络）
        monitor = SearchPerformanceMonitor(base_url="http://localhost:5678")
        monitor._monitor_loop = lambda: time.sleep(0.3)

        results, errors = self._run_threads(
            lambda i: monitor.start(),
            list(range(8)),
        )
        try:
            assert not errors, f"并发 start 抛异常: {errors}"
            assert monitor._running is True
            assert monitor._thread is not None
            # 锁保证仅第一个 start 创建线程并注册，其余直接 return
            assert len(monitor._registered_threads) == 1, (
                f"应仅启动 1 个监控线程，实际 {len(monitor._registered_threads)}"
            )
            assert monitor._thread.is_alive()
        finally:
            monitor.stop()

    def test_concurrent_set_interval_status_consistent(self):
        """并发 set_interval + get_status：读取到的 interval 必为写入值之一"""
        def setter(i):
            self.monitor.set_interval(60 + i)

        def reader(i):
            return self.monitor.get_status()["interval"]

        barrier = threading.Barrier(self.N_THREADS)
        errors = []
        read_intervals = []

        def worker(fn, arg):
            barrier.wait()
            try:
                result = fn(arg)
                if result is not None:
                    read_intervals.append(result)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = []
        for i in range(self.N_THREADS):
            fn = setter if i % 2 == 0 else reader
            threads.append(threading.Thread(target=worker, args=(fn, i)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 set_interval/get_status 抛异常: {errors}"
        # 读到的值必为已写入值之一：setter 写入值或初始值 300（reader 可能在首个 setter 前执行）
        valid = {300} | {60 + i for i in range(0, self.N_THREADS, 2)}
        assert all(iv in valid for iv in read_intervals), (
            f"读到非法 interval: {read_intervals}"
        )
        assert self.monitor._interval in valid
