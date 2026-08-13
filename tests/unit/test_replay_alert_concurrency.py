#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_storage + alert_evaluator 高并发稳定性验证

覆盖 2026-08-13 并发审计修复：
- replay_storage.store：文件 I/O 移出 RLock（慢磁盘不再阻塞查询路径）
- alert_evaluator.evaluate：logger/回调/自愈移出锁外（慢回调不再冻结评估器）

验证维度：
1. ReplayStorage 并发 store 不同 id：全部成功 + DB 记录数精确 + 文件齐全
2. ReplayStorage 并发 store + 并发查询：无异常（读路径不被写阻塞）
3. ReplayStorage SQLite 失败：文件回滚删除 + 抛 REPLAY_ERR_DB_FAILED
4. AlertEvaluator 并发 evaluate：total_evaluations 计数精确（锁内原子性）
5. AlertEvaluator 慢状态变化回调：不阻塞 add_rule（回调确在锁外）
"""
import json
import os
import threading
import time

import pytest

from agent.monitoring.replay_storage import (
    ReplayStorage,
    ReplayStorageError,
    REPLAY_ERR_DB_FAILED,
)
from agent.monitoring.alert_evaluator import AlertEvaluator, AlertRule


# ═══════════════════════════════════════════════════════════════
# ReplayStorage 高并发验证
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def temp_storage(tmp_path):
    """临时回放存储（每个测试独立目录）"""
    storage = ReplayStorage(str(tmp_path / "replays"))
    yield storage
    storage.close()


class TestReplayStorageConcurrency:
    """ReplayStorage 并发稳定性"""

    def test_concurrent_store_distinct_ids(self, temp_storage):
        """并发 store 不同 replay_id：全部成功、DB 记录精确、文件齐全"""
        n_threads, per_thread = 8, 10
        barrier = threading.Barrier(n_threads)
        errors = []

        def _worker(tid):
            try:
                barrier.wait()
                for i in range(per_thread):
                    rid = f"rid-{tid}-{i}"
                    temp_storage.store(replay_id=rid, data=json.dumps({"tid": tid, "i": i}))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 store 出现异常: {errors}"
        count = temp_storage._conn.execute(
            "SELECT COUNT(*) FROM replay"
        ).fetchone()[0]
        assert count == n_threads * per_thread, \
            f"DB 记录数: 期望 {n_threads * per_thread}，实际 {count}"
        for tid in range(n_threads):
            for i in range(per_thread):
                rid = f"rid-{tid}-{i}"
                fp = temp_storage._file_path_for(rid, None, False)
                assert os.path.exists(fp), f"replay 文件缺失: {fp}"

    def test_concurrent_store_and_query(self, temp_storage):
        """store 与查询并发执行：读路径不被文件写阻塞（无异常）"""
        for i in range(20):
            temp_storage.store(replay_id=f"seed-{i}", data=json.dumps({"seed": i}))
        existing_ids = [f"seed-{i}" for i in range(20)]

        n_threads = 6
        barrier = threading.Barrier(n_threads)
        errors = []

        def _store_worker(tid):
            try:
                barrier.wait()
                for i in range(15):
                    temp_storage.store(replay_id=f"new-{tid}-{i}", data="{}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def _query_worker(tid):
            try:
                barrier.wait()
                for _ in range(30):
                    rid = existing_ids[(tid * 7) % len(existing_ids)]
                    meta = temp_storage.get_by_id(rid)
                    assert meta is not None
                    temp_storage.get_data_by_id(rid)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = []
        for t in range(n_threads // 2):
            threads.append(threading.Thread(target=_store_worker, args=(t,)))
            threads.append(threading.Thread(target=_query_worker, args=(t,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 store+query 出现异常: {errors}"

    def test_store_db_failure_rolls_back_file(self, temp_storage, monkeypatch):
        """SQLite 写入失败 → 已写文件回滚删除 + 抛 REPLAY_ERR_DB_FAILED"""
        import sqlite3

        class _FakeConn:
            """execute 必失败的假连接（sqlite3.Connection.execute 不可 monkeypatch）"""
            def execute(self, *args, **kwargs):
                raise sqlite3.Error("mock db failure")
            def close(self):
                pass

        monkeypatch.setattr(temp_storage, "_conn", _FakeConn())
        file_path = temp_storage._file_path_for("rollback-test", None, False)
        with pytest.raises(ReplayStorageError) as ei:
            temp_storage.store(replay_id="rollback-test", data="{}")
        assert ei.value.code == REPLAY_ERR_DB_FAILED
        assert not os.path.exists(file_path), \
            "SQLite 失败后已写文件应被回滚删除"


# ═══════════════════════════════════════════════════════════════
# AlertEvaluator 高并发验证
# ═══════════════════════════════════════════════════════════════

class TestAlertEvaluatorConcurrency:
    """AlertEvaluator 并发稳定性"""

    def test_concurrent_evaluate_stats_accurate(self, monkeypatch):
        """并发 evaluate：total_evaluations 计数精确（锁内原子性）"""
        evaluator = AlertEvaluator()
        evaluator.add_rule(AlertRule(name="r", expr="x>0", threshold=0.5, comparison="gt"))
        monkeypatch.setattr(evaluator, "_evaluate_rule", lambda rule: None)

        n_threads, per_thread = 8, 20
        barrier = threading.Barrier(n_threads)
        errors = []

        def _worker():
            try:
                barrier.wait()
                for _ in range(per_thread):
                    evaluator.evaluate()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 evaluate 出现异常: {errors}"
        assert evaluator.get_stats()["total_evaluations"] == n_threads * per_thread, \
            f"total_evaluations: 期望 {n_threads * per_thread}，实际 {evaluator.get_stats()['total_evaluations']}"

    def test_slow_callback_does_not_block_evaluator(self, monkeypatch):
        """慢状态变化回调在锁外：触发回调期间 add_rule 不被阻塞"""
        evaluator = AlertEvaluator(pending_duration=999.0)
        evaluator.add_rule(AlertRule(name="r1", expr="x>0", threshold=0.5, comparison="gt"))
        monkeypatch.setattr(evaluator, "_evaluate_rule", lambda rule: 1.0)

        callback_started = threading.Event()
        callback_done = threading.Event()

        def slow_callback(alert, prev, new):
            # 模拟慢外部通知（网络请求等）
            callback_started.set()
            time.sleep(0.3)
            callback_done.set()

        evaluator.set_on_state_change(slow_callback)
        results = []

        def _run_evaluate():
            results.append(evaluator.evaluate())

        t_eval = threading.Thread(target=_run_evaluate)
        t_eval.start()
        assert callback_started.wait(timeout=2.0), "状态变化回调未触发"
        # 回调已进入锁外 sleep——此时锁必须已释放，add_rule 应立即可用
        t0 = time.time()
        evaluator.add_rule(AlertRule(name="r2", expr="y>0"))
        elapsed = time.time() - t0
        assert callback_done.wait(timeout=2.0)
        t_eval.join(timeout=5)

        assert not t_eval.is_alive(), "evaluate 线程未正常结束"
        assert elapsed < 0.2, \
            f"add_rule 被慢回调阻塞（回调疑似仍在锁内）: {elapsed:.3f}s"
        assert results, "evaluate 应正常返回（r1 已进入 PENDING，非 FIRING）"
        assert evaluator.get_alerts(), "评估器应保持可用"
