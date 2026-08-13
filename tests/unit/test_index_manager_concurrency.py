"""utils/index_manager 并发安全测试。

修复前：index_item/remove_item 的「检查→变更」为 TOCTOU（并发删除导致 KeyError），
search 遍历 set/dict 时与并发写冲突抛 RuntimeError（set/dict changed size during
iteration）。修复后：全部索引操作统一 threading.RLock，锁内仅内存 dict/set 变更
（_tokenize 为纯字符串处理，锁外计算），遵守持锁纪律。
"""

import threading

from agent.utils.index_manager import IndexManager


class TestIndexManagerConcurrency:
    """IndexManager 并发读写（threading.RLock 原子化）。"""

    def test_concurrent_index_count_precise(self):
        """100 线程 × 10 次 index_item 唯一 id：计数精确无丢失"""
        mgr = IndexManager()
        n_threads, per = 100, 10
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    mgr.index_item(
                        item_id=f"mem_{tid}_{i}",
                        content=f"hello world memory item {tid} {i}",
                        metadata={"category": "test", "type": "memory"},
                        timestamp="2026-08-13T00:00:00",
                    )
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = mgr.get_stats()
        assert stats["total_items"] == total          # id_to_item 计数精确
        # 每个 item 应可被关键词检索到（索引完整性）；limit 需覆盖全部结果
        assert len(mgr.search_by_keywords("memory", limit=total + 1)) == total
        assert len(mgr.search_by_keywords("hello", limit=total + 1)) == total

    def test_concurrent_index_and_search_no_crash(self):
        """4 写 + 4 读（search_by_keywords）：不抛 set changed size"""
        mgr = IndexManager()
        for i in range(50):
            mgr.index_item(
                item_id=f"seed_{i}", content=f"seed document {i}",
                metadata={}, timestamp="2026-08-13T00:00:00",
            )
        stop = threading.Event()
        errors = []

        def writer(tid):
            try:
                for i in range(200):
                    mgr.index_item(
                        item_id=f"w_{tid}_{i}", content=f"writer doc {tid} {i}",
                        metadata={"category": "writer"}, timestamp="2026-08-13T00:00:00",
                    )
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader(_):
            try:
                while not stop.is_set():
                    mgr.search_by_keywords("seed writer doc")
                    mgr.search_by_category("writer")
                    mgr.search_by_time_range("2026-08-13", "2026-08-13")
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
        assert mgr.get_stats()["total_items"] == 50 + 4 * 200  # 写入无丢失

    def test_concurrent_remove_no_keyerror(self):
        """预置后并发 remove（含重复删除）：不抛 KeyError、最终为空"""
        mgr = IndexManager()
        n_items = 200
        for i in range(n_items):
            mgr.index_item(
                item_id=f"rm_{i}", content=f"remove me {i}",
                metadata={"category": "rm"}, timestamp="2026-08-13T00:00:00",
            )
        n_threads = 100
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                # 每线程删除全部 id（大量重复删除，验证 TOCTOU 修复）
                for i in range(n_items):
                    mgr.remove_item(f"rm_{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 remove 不应抛 KeyError: {errors}"
        assert mgr.get_stats()["total_items"] == 0       # 全部移除
        assert mgr.search_by_keywords("remove me") == []  # 索引同步清空

    def test_concurrent_index_and_remove_consistency(self):
        """index + remove 混合：最终状态一致（无半索引残留）"""
        mgr = IndexManager()
        n_threads, per = 20, 100
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    item_id = f"mix_{tid}_{i}"
                    mgr.index_item(
                        item_id=item_id, content=f"mixed doc {tid} {i}",
                        metadata={"category": "mix"}, timestamp="2026-08-13T00:00:00",
                    )
                    if i % 2 == 0:
                        mgr.remove_item(item_id)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = mgr.get_stats()
        # 每线程 100 个 item，50 个保留（i 为奇数时未删除）
        assert stats["total_items"] == n_threads * per // 2
        # 一致性：保留的 item 可在 id_to_item 中获取，且可被关键词搜索命中
        kept = mgr.search_by_keywords("mixed doc", limit=n_threads * per)
        assert len(kept) == stats["total_items"]
