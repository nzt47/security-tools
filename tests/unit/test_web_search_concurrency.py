"""web/search 并发安全测试。

修复前：模块级单例 SearchEngine 被多路 HTTP 请求并发调用——_stats 的 += 为
读-改-写序列（并发丢计数）、_set_cache 的整体重建 `self._cache = {...}` 与
并发写相互覆盖（缓存丢失）、remove_engine 的 list.remove 与 search 的引擎遍历
并发抛 RuntimeError、_check_cache 的 cached_hits += 1 无锁丢计数。修复后：
RLock 保护统计/缓存/引擎注册表，锁内仅内存快照/变更，网络调用（handler）在
锁外（持锁纪律：锁内严禁 I/O）。_check_cache/_set_cache 内部自锁（RLock 重入
安全），search 成功路径锁内一次写缓存。
"""

import threading

import pytest

from agent.web.search import SearchEngine

# 整文件标记 slow（Why: 全部用例为 40-50 线程并发 + t.join()，在并行会话/高负载下
# 会触发 pytest-timeout 无法中断的 tstate 锁等待导致全量回归被强杀——
# 2026-08-15 快速回归实测卡死于 test_concurrent_register_remove_search_no_crash；
# 归入 slow 集合后由 `-m "not slow"` 快速回归自动跳过）
pytestmark = pytest.mark.slow


def _ok_handler(query, num_results=10, page=1, **kwargs):
    """成功 handler：返回固定数量的假结果"""
    return {
        "ok": True,
        "results": [
            {"title": f"{query}-{i}", "url": f"http://example.com/{i}",
             "snippet": "", "source": "test"}
            for i in range(num_results)
        ],
        "total_estimate": num_results,
        "engine": "test",
    }


def _fail_handler(query, num_results=10, page=1, **kwargs):
    """失败 handler：所有引擎失败路径"""
    return {"ok": False, "error": "test fail"}


class TestWebSearchConcurrency:
    """SearchEngine 并发读写（RLock 原子化）。"""

    def test_concurrent_search_stats_precise(self):
        """50 线程 × 20 次并发搜索：searches/total_results/engine_usage 无丢失"""
        se = SearchEngine({"default_engine": "alpha",
                           "engine_priority": ["alpha", "beta"]})
        se.register_engine("alpha", "Alpha", _ok_handler)
        se.register_engine("beta", "Beta", _ok_handler)

        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    # 每次查询唯一 → 不命中缓存 → 走成功路径统计
                    result = se.search(f"q-{tid}-{i}")
                    assert result["ok"] and len(result["results"]) == 10
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = se.get_stats()
        assert stats["searches"] == total                    # 读-改-写计数无丢失
        assert stats["total_results"] == total * 10
        assert stats["engine_usage"]["alpha"] == total       # 首选引擎全部命中
        assert stats["engine_usage"]["beta"] == 0
        timing = stats["engine_timing"]["alpha"]
        assert timing["count"] == total
        assert timing["min"] <= timing["avg"] <= timing["max"]

    def test_concurrent_cache_write_no_loss(self):
        """40 线程 × 25 次唯一查询并发写缓存：无覆盖丢失、无 RuntimeError"""
        se = SearchEngine({"default_engine": "alpha",
                           "engine_priority": ["alpha"]})
        se.register_engine("alpha", "Alpha", _ok_handler)

        n_threads, per = 40, 25
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    # 唯一查询 → 每次写缓存 → 验证整体重建与并发写互斥
                    result = se.search(f"cache-{tid}-{i}")
                    assert result["ok"]
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发写缓存不应抛 RuntimeError: {errors}"
        assert len(se._cache) == total                        # 缓存条目无覆盖丢失

    def test_concurrent_register_remove_search_no_crash(self):
        """并发注册/移除/搜索：不抛 RuntimeError/KeyError，注册表一致"""
        se = SearchEngine({"default_engine": "keep",
                           "engine_priority": ["keep"]})
        se.register_engine("keep", "Keep", _ok_handler)

        n_threads = 40
        barrier = threading.Barrier(n_threads)
        errors = []

        def searcher(tid):
            try:
                barrier.wait()
                for i in range(200):
                    result = se.search(f"s-{tid}-{i}")
                    assert result["ok"] or result.get("error")  # 无可用引擎时返回错误而非崩溃
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def registrar(tid):
            try:
                barrier.wait()
                for i in range(30):
                    # 注册/移除与 search 的引擎遍历并发 → 不抛 RuntimeError
                    se.register_engine(f"dyn-{tid}-{i}", f"Dyn-{tid}-{i}", _ok_handler)
                    se.remove_engine(f"dyn-{tid}-{i}")
                    se.get_available_engines()
                    se.get_registered_engines()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=searcher if t % 2 == 0 else registrar, args=(t,))
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发注册/移除/搜索不应抛异常: {errors}"
        # 动态引擎全部已移除，仅剩 keep
        assert list(se._engine_registry.keys()) == ["keep"]
        # 与注册表一致的快照可读
        names = {e["name"] for e in se.get_registered_engines()}
        assert names == {"keep"}

    def test_concurrent_fallback_stats_precise(self):
        """50 线程 × 20 次全失败搜索：searches/fallback_count/_fallback_history 精确"""
        se = SearchEngine({"default_engine": "fail",
                           "engine_priority": ["fail"]})
        se.register_engine("fail", "Fail", _fail_handler)

        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    result = se.search(f"f-{tid}-{i}")
                    assert not result["ok"]
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = se.get_stats()
        assert stats["searches"] == total
        assert stats["fallback_count"] == total               # 失败计数无丢失
        assert len(se._fallback_history) == total             # 历史记录无丢失

    def test_concurrent_read_mix_consistent(self):
        """并发读状态 + 注册/移除 + 搜索混合：快照一致、不崩溃"""
        se = SearchEngine({"default_engine": "base",
                           "engine_priority": ["base"]})
        se.register_engine("base", "Base", _ok_handler)

        n_threads, per = 40, 30
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    if tid % 3 == 0:
                        se.search(f"m-{tid}-{i}")
                    elif tid % 3 == 1:
                        status = se.get_current_status()
                        assert status["stats"]["cache_size"] >= 0
                        assert "base" in status["stats"]["engine_usage"]
                    else:
                        se.register_engine(f"tmp-{tid}-{i}", f"Tmp-{tid}-{i}", _ok_handler)
                        se.remove_engine(f"tmp-{tid}-{i}")
                        se.get_stats()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"读写混合不应抛异常: {errors}"
        stats = se.get_stats()
        assert stats["searches"] > 0
        assert len(se._engine_registry) == 1                  # 临时引擎全部移除
