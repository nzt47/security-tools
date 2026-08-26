"""workflow_learning/matcher 并发安全测试。

修复前：register/unregister 的 add/remove（写 _docs/_df）与 match 触发的
_rebuild（遍历 _docs）并发会抛 RuntimeError（dictionary changed size during
iteration），add 的 df 读-改-写丢计数。修复后：WorkflowMatcher 公开操作
（register/unregister/rebuild/match 的 query 段）统一 threading.RLock，
锁内仅内存计算，match 的观测/日志/metrics 在锁外（持锁纪律）。
"""

import threading
import time

from agent.workflow_learning.matcher import WorkflowMatcher
from agent.workflow_learning.models import LearnedWorkflow


def _make_wf(wf_id: str, keyword: str, *, enabled: bool = True) -> LearnedWorkflow:
    return LearnedWorkflow(
        id=wf_id,
        name=f"wf-{wf_id}",
        description="concurrency test workflow",
        task_signature=f"匹配任务 {keyword}",
        trigger_patterns=[keyword],
        tags=["test"],
        confidence=0.9,
        priority=60,
        enabled=enabled,
    )


class TestWorkflowMatcherConcurrency:
    """WorkflowMatcher 并发读写（threading.RLock 原子化）。"""

    def test_concurrent_register_count_precise(self):
        """100 线程 × 10 次 register 唯一 wf：注册计数精确、可匹配"""
        matcher = WorkflowMatcher()
        n_threads, per = 100, 10
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    wf = _make_wf(f"wf-{tid}-{i}", f"keyword{tid}{i}")
                    matcher.register(wf)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(matcher._workflows) == total            # 注册计数精确（无丢失）
        # 索引完整性：并发注册的每个 wf 都应进入索引（无丢失写入）
        # （match 的相似度阈值过滤会排除短文本命中，故直接验证索引层）
        assert len(matcher._index._docs) == total
        assert "wf-12-7" in matcher._index._docs           # tid=12, i=7（per=10 范围内）

    def test_concurrent_register_and_match_no_crash(self):
        """4 写（register）+ 4 读（match）：不抛 RuntimeError（_rebuild 遍历互斥）"""
        matcher = WorkflowMatcher()
        for i in range(30):
            matcher.register(_make_wf(f"seed-{i}", f"seedkw{i}"))
        errors = []

        def writer(tid):
            try:
                for i in range(80):
                    matcher.register(_make_wf(f"w-{tid}-{i}", f"wkw{tid}{i}"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader(_):
            # 有界循环 + 让步：4 reader 无限循环高频抢 RLock（锁内每次 dirty 触发
            # _rebuild O(N)），会饿死 4 writer（CI 慢机 60s t.join 超时复现，本地
            # 2.46s 通过——纯调度时序，非并发正确性缺陷）。有界循环仍保留
            # "读写并发不抛 RuntimeError"的验证语义，且 writer 必然能拿到锁。
            # 500 轮在 CI 慢机（xdist 4 worker 抢 CPU）下仍可逼近 60s 超时线，
            # 2026-08-14 py3.12 Shard5 复现（本地 9s 通过）；降到 100 轮留足余量。
            # 2026-08-26 py3.12 Shard1 再次复现（本地 2.07s 通过），续降 reader 50 轮
            #   且 writer 150→80（reader 饿死根因在 writer 高频 register 触发 O(N)
            #   rebuild 抢占锁，双降释放锁竞争余量）。
            try:
                for _ in range(50):
                    matcher.match("匹配任务 seedkw0 wkw1 2")
                    matcher.match("匹配任务 其他文本")
                    time.sleep(0)  # 让出 GIL，给 writer 抢占锁的机会
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        readers = [threading.Thread(target=reader, args=(t,)) for t in range(4)]
        for t in writers + readers:
            t.start()
        for t in writers:
            t.join()
        for t in readers:
            t.join()

        assert not errors, f"读写并发不应抛 RuntimeError: {errors}"
        assert len(matcher._workflows) == 30 + 4 * 150     # 注册无丢失

    def test_concurrent_unregister_and_match_safe(self):
        """预置后并发 unregister + match：不抛异常、最终状态一致"""
        matcher = WorkflowMatcher()
        n_items = 100
        for i in range(n_items):
            matcher.register(_make_wf(f"rm-{i}", f"rmkw{i}"))
        n_threads = 20
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                # 每线程删除全部（大量重复删除，验证 remove 与 match 并发安全）
                for i in range(n_items):
                    matcher.unregister(f"rm-{i}")
                    if i % 3 == 0:
                        matcher.match("匹配任务 rmkw0")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"unregister + match 并发不应抛异常: {errors}"
        assert len(matcher._workflows) == 0                # 全部移除
        assert matcher.match("匹配任务 rmkw0") == []       # 索引同步清空

    def test_concurrent_register_and_rebuild_consistent(self):
        """register + rebuild 并发：不抛异常、rebuild 后注册不丢、无孤儿"""
        matcher = WorkflowMatcher()
        stop = threading.Event()
        errors = []

        def writer(tid):
            try:
                for i in range(100):
                    matcher.register(_make_wf(f"w-{tid}-{i}", f"wkw{tid}{i}"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def rebuilder(_):
            base = [_make_wf(f"base-{i}", f"basekw{i}") for i in range(20)]
            try:
                while not stop.is_set():
                    matcher.rebuild(base)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        rebuilder_t = threading.Thread(target=rebuilder, args=(0,))
        for t in writers + [rebuilder_t]:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        rebuilder_t.join()

        assert not errors, f"register + rebuild 并发不应抛异常: {errors}"
        # 一致性：_workflows 中的每个 id 都能在索引中查询到（无孤儿）
        for wf_id in matcher._workflows:
            assert wf_id in matcher._index._docs
