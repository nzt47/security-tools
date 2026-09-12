"""TASK-S8-01 保留策略测试工具（**全部在临时目录内操作，绝不碰真实 data/**）。

【为什么不复用真实 data/】
    归档/还原类用例会写文件、会删文件。真实 `data/` 里是审计链（红线）与运营期
    唯一证据，任何用例误触都是不可逆事故。故本工具一律以 `tmp_path` 为"仓库根"，
    用显式路径参数驱动策略与归档器 —— 与任务书 §七.1 的要求一致。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from agent.observability.events import EventStore, reset_event_stores
from agent.retention.policy import (
    ARCHIVE_COLD_PACK,
    DELETE_GUARDED,
    DELETE_NONE,
    KIND_JSONL,
    KIND_SQLITE,
    KIND_TREE,
    RetentionClass,
    RetentionPolicy,
)

#: 测试用固定"今天"（注入时钟；跨午夜/时区不参与判定）
FIXED_TODAY = "2026-09-13"


def fixed_clock(now: Optional[str] = None):
    """注入时钟：返回固定 `datetime` 的 callable（任务书 §七.2 要求）。"""
    stamp = now or FIXED_TODAY

    def _clock() -> datetime:
        return datetime.strptime(stamp, "%Y-%m-%d")

    return _clock


def make_root(tmp_path) -> str:
    """建立临时"仓库根"骨架（data/ 下的既有落点）。"""
    root = os.path.abspath(str(tmp_path))
    for sub in ("data/events", "data/audit", "data/digestion/cases",
                "data/digestion/drafts", "data/digestion/shadow",
                "data/policies", "data/memory", "agent/data"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


# ── 事件流（用真实 EventStore 写，保证信封合法）────────────────


def write_events(root: str, day: str, *, count: int = 3,
                 cost_cents: float = 10.0, model: str = "m") -> List[str]:
    """往 `<root>/data/events/` 写 `count` 条合法 `cost` 事件（返回 ts 列表）。"""
    directory = os.path.join(root, "data", "events")
    os.makedirs(directory, exist_ok=True)
    store = EventStore(path=os.path.join(directory, "events.jsonl"),
                       enabled=True, audit_mirror=False, archive=False)
    stamps: List[str] = []
    for i in range(count):
        ts = f"{day}T0{i % 10}:00:00Z"
        envelope = store.emit(
            "cost",
            {"model": model, "cost_raw_cents": cost_cents,
             "cost_normalized_cents": cost_cents, "tokens_in": 100, "tokens_out": 50},
            actor="auto", correlation_id=f"c-{day}-{i}", ts=ts,
            idempotency_key=f"{day}-{i}",
        )
        assert envelope is not None, "测试事件写入失败（信封非法？）"
        stamps.append(ts)
    # 释放单写者登记（同一路径的第二个 writer 会抛 SingleWriterViolationError）
    store.close()
    return stamps


def write_shadow_ledger(root: str, *, runs: int = 2, sampled: int = 5,
                        day: str = "2026-01-01") -> str:
    """写灰度台账（`shadow_ledger.jsonl`）与人工复核队列。

    mtime 一律拨到 `day`（冷层按"归属日"选片；文件名无日期时退回 mtime）。
    """
    directory = os.path.join(root, "data", "digestion", "shadow")
    os.makedirs(directory, exist_ok=True)
    ledger = os.path.join(directory, "shadow_ledger.jsonl")
    with open(ledger, "a", encoding="utf-8") as fh:
        for i in range(runs):
            fh.write(json.dumps({
                "kind": "shadow_run", "capability_id": f"cap-{i}",
                "sampled": sampled, "passed": sampled - 1, "negative": 1,
                "ts": f"2026-01-0{i + 1}T00:00:00Z",
            }, ensure_ascii=False) + "\n")
    reviews = os.path.join(directory, "manual_reviews.jsonl")
    with open(reviews, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "kind": "queued", "capability_id": "cap-0", "case_id": "c1",
            "ts": "2026-01-01T00:00:00Z",
        }, ensure_ascii=False) + "\n")
    touch_old(ledger, day)
    touch_old(reviews, day)
    return ledger


def write_decisions(root: str, *, count: int = 2, day: str = "2026-01-01") -> str:
    """写策略决策日志（`decisions.jsonl`）。"""
    path = os.path.join(root, "data", "policies", "decisions.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(json.dumps({
                "ts": f"{day}T00:00:0{i}Z", "schema": "policy.decision.v1",
                "effect": "allow", "policy_id": "p1", "action": "read",
            }, ensure_ascii=False) + "\n")
    return path


def write_json_file(root: str, rel: str, data: Any) -> str:
    path = os.path.normpath(os.path.join(root, rel))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return path


def make_sqlite(root: str, rel: str, *, table: str = "unified_traces",
                rows: int = 3) -> str:
    """建一个最小的 SQLite 库（用于 SQLite 快照往返用例）。"""
    import sqlite3

    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" '
                     "(seq INTEGER PRIMARY KEY, name TEXT, n INTEGER)")
        for i in range(rows):
            conn.execute(f'INSERT INTO "{table}" (seq, name, n) VALUES (?, ?, ?)',
                         (i + 1, f"row-{i}", i * 10))
        conn.commit()
    finally:
        conn.close()
    return path


def touch_old(path: str, day: str) -> None:
    """把文件的 mtime 设为指定日期（让 `cold_files` 的 mtime 兜底路径可测）。"""
    stamp = datetime.strptime(day, "%Y-%m-%d").timestamp()
    os.utime(path, (stamp, stamp))


# ── 策略构造 ─────────────────────────────────────────────────


def retention_class(class_id: str, **kw: Any) -> RetentionClass:
    """构造一个测试用策略类（默认：JSONL、可删、无红线）。

    取名 `retention_class` 而非 `test_class`：pytest 的 `python_functions = test_*`
    会把导入的 `test_*` 名字当成用例收集（S8-01 实测踩过一次）。
    """
    base: Dict[str, Any] = dict(
        class_id=class_id, title=f"测试类 {class_id}", kind=KIND_JSONL,
        globs=(f"data/{class_id}/*.jsonl",), warm_days=None, cold_days=1,
        retention_days=30, archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_GUARDED, deletable=True, owner="test", basis="test",
    )
    base.update(kw)
    return RetentionClass(**base)


def make_policy(root: str, classes: Sequence[RetentionClass], **kw: Any
                ) -> RetentionPolicy:
    """构造测试策略（归档目录默认落在 `<root>/data/archive`）。"""
    archive_dir = kw.pop("archive_dir", "") or os.path.join(root, "data", "archive")
    return RetentionPolicy(list(classes), archive_dir=archive_dir, **kw)


def events_class(**kw: Any) -> RetentionClass:
    """与内置策略表同形的 `events` 类（温层可开、读端分片感知）。"""
    base: Dict[str, Any] = dict(
        class_id="events", title="事件流", kind=KIND_JSONL,
        globs=("data/events/events.jsonl", "data/events/events-*.jsonl"),
        warm_days=0, cold_days=1, retention_days=None,
        archive_mode="warm_daily", delete_mode=DELETE_NONE, deletable=False,
        reader_shard_aware=True, owner="test", basis="test",
    )
    base.update(kw)
    return RetentionClass(**base)


def drafts_class(**kw: Any) -> RetentionClass:
    """草稿类（全表唯一"可直接删"的类）。"""
    base: Dict[str, Any] = dict(
        class_id="digestion_drafts", title="消化草稿", kind=KIND_TREE,
        globs=("data/digestion/drafts/**/*.md",), warm_days=None, cold_days=1,
        retention_days=180, archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_GUARDED, deletable=True, owner="test", basis="test",
    )
    base.update(kw)
    return RetentionClass(**base)


def sqlite_class(**kw: Any) -> RetentionClass:
    base: Dict[str, Any] = dict(
        class_id="unified_traces", title="轨迹库", kind=KIND_SQLITE,
        globs=("agent/data/tool_trace.db",), warm_days=None, cold_days=0,
        retention_days=90, archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_NONE, deletable=False, sqlite_table="unified_traces",
        owner="test", basis="test",
    )
    base.update(kw)
    return RetentionClass(**base)


def cleanup_event_stores() -> None:
    """清掉事件层的进程内单写者登记（用例之间互不影响）。"""
    reset_event_stores()


__all__ = [
    "FIXED_TODAY", "fixed_clock", "make_root", "write_events", "write_shadow_ledger",
    "write_decisions", "write_json_file", "make_sqlite", "touch_old",
    "retention_class", "make_policy", "events_class", "drafts_class", "sqlite_class",
    "cleanup_event_stores", "KIND_TREE", "KIND_JSONL", "KIND_SQLITE",
]
