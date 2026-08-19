"""LearningMetrics SQLite 持久化测试（flush 批量 / 事务性 / 降级 / 重启恢复 / 默认关闭）

覆盖（对应持久化方案设计）：
1. 默认关闭不落盘；启用后 record < 阈值不落库、达阈值自动批量落库
2. 事务性：一次 flush = 单事务写多行（commit 恰 1 次）
3. 降级-初始化：DB 不可用（connect 异常）→ 构造不抛、自动降级为内存聚合
4. 降级-落库失败：flush 异常被吞、持久化关闭、内存数据完整
5. 重启恢复：写 + flush → 新实例同路径加载 → get_snapshot 与写前一致
"""

import os
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from agent.learning_metrics import LearningMetrics


def _mk_persistence(path, batch=5, retention=90) -> dict:
    return {
        "enabled": True,
        "path": str(path),
        "flush_batch_size": batch,
        "retention_days": retention,
    }


def _db_rows(path) -> list:
    """读取持久化表的全部行（未落库/未创建返回空）"""
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT day, kind, key, val, cnt FROM lm_daily_agg ORDER BY day, kind, key"
        ).fetchall()
    finally:
        conn.close()


def _seed(lm: LearningMetrics, n: int = 1, day_offset: int = 0) -> None:
    """构造 n 次混合记录（交互/workflow/任务结果；ts 取近期并可按天偏移）

    day_offset 用于产生不同 (day,kind,key) 组合：pending 以该三元组为键合并，
    同一 day 重复 seed 不会增加行数（阈值判定按事件行数）。
    """
    base = time.time() - 3600 + day_offset * 86400
    for i in range(n):
        ts = base + i * 100
        lm.record_interaction(ts=ts)
        lm.record_workflow_match(hit=(i % 2 == 0), saved_tokens=100, ts=ts)
        lm.record_task_result("qa", success=(i % 2 == 0))


def test_default_off_no_db_file_and_batch_flush(tmp_path):
    """默认不持久化（无 db 文件）；启用后达阈值才批量落库（阈值为事件行数）"""
    # 默认（不传 persistence）：行为与现状一致，无 db 文件、无持久化
    lm0 = LearningMetrics(enabled=True)
    _seed(lm0)
    lm0.flush()  # 无持久化时 no-op
    assert lm0._persistence is None

    # 启用持久化：1 次交互产生 5 个事件行（含 token_saved）< 阈值 6 → 未落库
    db = tmp_path / "lm.db"
    lm = LearningMetrics(persistence=_mk_persistence(db, batch=6))
    assert lm._persistence is not None
    _seed(lm, n=1, day_offset=0)
    assert _db_rows(db) == []  # 未达阈值，pending 在内存

    # 次日再 1 次交互（跨天产生新 (day,kind,key)，累计 10 事件行 ≥ 6）→ 自动批量落库
    _seed(lm, n=1, day_offset=1)
    rows = _db_rows(db)
    assert len(rows) > 0
    # 批量数据应含 interaction / workflow_query / workflow_hit / task_total 等 kind
    kinds = {r[1] for r in rows}
    assert {"interaction", "workflow_query", "task_total"} <= kinds


def test_flush_single_transaction_multiple_rows(tmp_path):
    """事务性：一次 flush 用单事务写全部 pending 行（commit 恰 1 次、execute 行数=批次）"""
    db = tmp_path / "lm.db"
    lm = LearningMetrics(persistence=_mk_persistence(db, batch=100))
    _seed(lm, n=3)  # 交互 3 次 → 事件行 12 个（含 token_saved），< 100 不自动 flush

    calls = {"commit": 0, "execute": 0}
    real_connect = sqlite3.connect

    def _counting_connect(*a, **kw):
        conn = real_connect(*a, **kw)
        m = MagicMock(wraps=conn)  # wraps 保留真实行为，side_effect 拦截计数
        orig_commit, orig_execute = conn.commit, conn.execute

        def _c():
            calls["commit"] += 1
            return orig_commit()

        def _e(sql, params=()):
            if isinstance(sql, str) and sql.lstrip().upper().startswith("INSERT"):
                calls["execute"] += 1
            return orig_execute(sql, params)
        m.commit.side_effect = _c
        m.execute.side_effect = _e
        return m

    with patch("agent.learning_metrics.sqlite3.connect", side_effect=_counting_connect):
        lm.flush()

    assert calls["commit"] == 1          # 单事务
    assert calls["execute"] >= 5         # 批量多行（12 事件行全部一次写入）
    # 落库后 pending 清空，再次 flush 无副作用
    lm.flush()
    assert calls["commit"] == 1


def test_degradation_when_db_unavailable_at_init(tmp_path):
    """降级-初始化：DB 连接异常 → 构造不抛、自动降级为内存聚合、埋点仍正常"""
    with patch("agent.learning_metrics.sqlite3.connect",
               side_effect=sqlite3.OperationalError("cannot open db")):
        lm = LearningMetrics(persistence=_mk_persistence(tmp_path / "x.db"))
    assert lm._persistence is None  # 已降级
    _seed(lm, n=3)
    snap = lm.get_snapshot()
    assert snap["kpis"]["workflow_hit_rate"]["interactions"] == 3
    assert snap["kpis"]["failure_rate_by_task_type"]["qa"]["total"] == 3


def test_degradation_when_flush_fails(tmp_path):
    """降级-落库失败：flush 异常被吞、持久化关闭、内存数据完整"""
    db = tmp_path / "lm.db"
    lm = LearningMetrics(persistence=_mk_persistence(db, batch=100))
    _seed(lm, n=3)

    def _boom_connect(*a, **kw):
        conn = sqlite3.connect(*a, **kw)
        orig_execute = conn.execute

        def _e(sql, params=()):
            if sql.lstrip().upper().startswith("INSERT"):
                raise sqlite3.OperationalError("disk I/O error")
            return orig_execute(sql, params)
        conn.execute = _e
        return conn

    with patch("agent.learning_metrics.sqlite3.connect", side_effect=_boom_connect):
        lm.flush()  # 不抛异常（内部吞掉）
    assert lm._persistence is None  # 降级关闭
    # 内存聚合不受影响
    snap = lm.get_snapshot()
    assert snap["kpis"]["workflow_hit_rate"]["interactions"] == 3
    assert snap["kpis"]["token_reuse_rate"]["saved_tokens"] == 200  # 2 次命中 × 100


def test_restart_restores_snapshot_from_db(tmp_path):
    """重启恢复：写 + flush → 新实例同路径加载 → get_snapshot 与写前一致"""
    db = tmp_path / "lm.db"
    lm_a = LearningMetrics(persistence=_mk_persistence(db, batch=100))
    _seed(lm_a, n=3)
    lm_a.flush()
    snap_a = lm_a.get_snapshot()

    # 模拟进程重启：同一 db 路径构造新实例（自动 load_from_db）
    lm_b = LearningMetrics(persistence=_mk_persistence(db, batch=100))
    snap_b = lm_b.get_snapshot()

    k_a, k_b = snap_a["kpis"], snap_b["kpis"]
    assert k_b["workflow_hit_rate"] == k_a["workflow_hit_rate"]          # 3/3、命中 2
    assert k_b["token_reuse_rate"] == k_a["token_reuse_rate"]            # saved 200 / total 200
    assert k_b["failure_rate_by_task_type"] == k_a["failure_rate_by_task_type"]  # qa 2 成功 1 失败
    assert k_b["artifact_delta"] == k_a["artifact_delta"]
    assert k_b["evolution_adoption_rate"] == k_a["evolution_adoption_rate"]
