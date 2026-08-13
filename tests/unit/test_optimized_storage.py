"""agent.log_system.optimized_storage 单元测试

覆盖 BatchLogWriter（批量写入、后台线程、队列丢弃、异常回退、慢写入告警）、
ShardWriter / ShardedLogStorage（分片 JSONL 写入、分片缓存、过期清理）、
OptimizedLogStorage（SQLite 批量/直接写入、初始化幂等、统计、关闭）以及
模块级工厂函数与全局单例获取路径。
"""

import json
import logging
import os
import sqlite3
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from agent.log_system import optimized_storage as mod
from agent.log_system.models import LogCategory, LogLevel
from agent.log_system.optimized_storage import (
    BatchLogWriter,
    OptimizedLogStorage,
    ShardedLogStorage,
    ShardWriter,
    _trace_id,
)


@pytest.fixture
def writer_factory():
    """创建 BatchLogWriter 的工厂；自动收集每次 flush 的批次，并保证测试后 stop 后台线程。"""
    writers = []
    batches = []

    def _make(**kwargs):
        def _wf(batch):
            batches.append(list(batch))

        w = BatchLogWriter(write_func=_wf, **kwargs)
        writers.append(w)
        return w

    yield _make, batches
    for w in writers:
        w.stop(timeout=1.0)


@pytest.fixture
def storage(tmp_path):
    """构造并初始化真实的 OptimizedLogStorage（sqlite 与分片文件均在 tmp_path 下）。"""
    s = OptimizedLogStorage(
        db_path=str(tmp_path / "test.db"),
        raw_log_dir=str(tmp_path / "raw"),
    )
    s.initialize()
    yield s
    s.close()


# ── trace_id ────────────────────────────────────────────────────────────


def test_trace_id_returns_16_hex_chars():
    """_trace_id 应返回 16 位小写十六进制字符串。"""
    tid = _trace_id()
    assert isinstance(tid, str)
    assert len(tid) == 16
    assert all(c in "0123456789abcdef" for c in tid)


def test_trace_id_unique():
    """连续生成的 trace_id 不应重复。"""
    assert _trace_id() != _trace_id()


# ── BatchLogWriter ──────────────────────────────────────────────────────


def test_batch_writer_start_stops_thread(writer_factory):
    """start 后后台线程运行，stop 后线程结束且队列中数据被最终刷新。"""
    make, batches = writer_factory
    w = make(batch_size=100)
    w.write({"a": 1})
    w.start()
    assert w._flush_thread is not None
    assert w._flush_thread.is_alive()
    w.stop(timeout=2.0)
    assert not w._flush_thread.is_alive()
    # stop 时的最后一次 _flush 把队列数据写出
    assert len(batches) == 1
    assert batches[0] == [{"a": 1}]


def test_batch_writer_start_idempotent(writer_factory):
    """重复调用 start 不应启动第二个线程。"""
    make, _ = writer_factory
    w = make()
    w.start()
    first = w._flush_thread
    w.start()
    assert w._flush_thread is first


def test_batch_writer_stop_without_start(writer_factory):
    """未 start 直接 stop 不应抛异常（_flush_thread 为 None 分支）。"""
    make, batches = writer_factory
    w = make()
    w.write({"a": 1})
    w.stop(timeout=1.0)  # 仍会执行一次 _flush
    assert len(batches) == 1


def test_batch_writer_write_below_batch_size(writer_factory):
    """记录数未达 batch_size 时不触发 flush，仅入队。"""
    make, batches = writer_factory
    w = make(batch_size=10)
    assert w.write({"a": 1}) is True
    assert len(w._queue) == 1
    assert batches == []


def test_batch_writer_write_triggers_flush(writer_factory):
    """记录数达到 batch_size 时立即 flush 并更新统计。"""
    make, batches = writer_factory
    w = make(batch_size=2)
    w.write({"n": 1})
    w.write({"n": 2})
    assert batches == [[{"n": 1}, {"n": 2}]]
    stats = w.get_stats()
    assert stats["records_written"] == 2
    assert stats["batches_flushed"] == 1


def test_batch_writer_write_queue_full(writer_factory):
    """队列满时 write 应丢弃记录并返回 False。"""
    make, _ = writer_factory
    w = make(batch_size=100, max_queue_size=2)
    assert w.write({"n": 1}) is True
    assert w.write({"n": 2}) is True
    assert w.write({"n": 3}) is False
    stats = w.get_stats()
    assert stats["dropped_records"] == 1
    assert stats["queue_full_events"] == 1


def test_batch_writer_write_batch_normal(writer_factory):
    """write_batch 正常入队所有记录。"""
    make, batches = writer_factory
    w = make(batch_size=100, max_queue_size=100)
    records = [{"n": i} for i in range(3)]
    assert w.write_batch(records) is True
    assert w._queue == records


def test_batch_writer_write_batch_triggers_flush(writer_factory):
    """write_batch 记录数达到 batch_size 时立即 flush。"""
    make, batches = writer_factory
    w = make(batch_size=2, max_queue_size=100)
    assert w.write_batch([{"n": 1}, {"n": 2}]) is True
    assert batches == [[{"n": 1}, {"n": 2}]]


def test_batch_writer_write_batch_partial(writer_factory):
    """队列剩余空间不足时只容纳部分记录，其余计入丢弃。"""
    make, _ = writer_factory
    w = make(batch_size=100, max_queue_size=5)
    w.write({"n": 1})
    w.write({"n": 2})
    w.write({"n": 3})
    records = [{"n": i} for i in range(10, 15)]  # 5 条
    assert w.write_batch(records) is True
    assert len(w._queue) == 5  # 原 3 条 + 新容纳 2 条
    assert w._queue[-2:] == [{"n": 10}, {"n": 11}]
    assert w.get_stats()["dropped_records"] == 3


def test_batch_writer_write_batch_queue_full(writer_factory):
    """队列已满时 write_batch 全部丢弃并返回 False。"""
    make, _ = writer_factory
    w = make(batch_size=100, max_queue_size=2)
    w.write({"n": 1})
    w.write({"n": 2})
    assert w.write_batch([{"n": 3}, {"n": 4}]) is False
    stats = w.get_stats()
    assert stats["dropped_records"] == 2
    assert stats["queue_full_events"] == 1


def test_batch_writer_flush_empty(writer_factory):
    """队列为空时 _flush 直接返回，不调用 write_func。"""
    make, batches = writer_factory
    w = make()
    w._flush()
    assert batches == []


def test_batch_writer_flush_write_error_requeue(writer_factory):
    """write_func 抛异常时记录应放回队列（最多 10 条），统计不累计。"""
    def _boom(batch):
        raise RuntimeError("db down")

    w = BatchLogWriter(write_func=_boom, batch_size=2, max_queue_size=100)
    w.write({"a": 1})
    w.write({"a": 2})  # 触发 flush -> boom -> 放回队列
    assert len(w._queue) == 2
    assert w.get_stats()["records_written"] == 0
    assert w.get_stats()["batches_flushed"] == 0
    w.stop(timeout=1.0)  # 无线程；stop 内的 flush 再次失败但不抛异常


def test_batch_writer_flush_slow_warning(writer_factory, caplog):
    """单次写入超过 100ms 应记录 warning 告警。"""
    def _slow(batch):
        time.sleep(0.15)

    w = BatchLogWriter(write_func=_slow, batch_size=1)
    with caplog.at_level(logging.WARNING, logger="agent.log_system.optimized_storage"):
        w.write({"a": 1})  # 触发 flush，耗时 ~150ms
    assert "批量写入耗时较长" in caplog.text
    assert w.get_stats()["batches_flushed"] == 1


def test_batch_writer_flush_loop_timer(writer_factory):
    """后台线程应按 flush_interval_ms 定时刷新队列。"""
    make, batches = writer_factory
    w = make(batch_size=100, flush_interval_ms=1)
    w.write({"tick": 1})
    w.start()
    time.sleep(0.25)  # 等待后台定时 flush
    w.stop(timeout=2.0)
    assert batches, "后台线程未定时触发 flush"


def test_batch_writer_flush_loop_exception():
    """_flush_loop 中 sleep 抛异常时进入 except 分支并继续（此处直接抛出让异常逃逸）。"""
    w = BatchLogWriter(write_func=lambda b: None)
    w._running = True
    with mock.patch.object(mod.time, "sleep", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            w._flush_loop()
    w._running = False


def test_batch_writer_get_stats_returns_copy(writer_factory):
    """get_stats 返回独立副本，外部修改不影响内部统计。"""
    make, _ = writer_factory
    w = make()
    stats = w.get_stats()
    stats["records_written"] = 999
    assert w.get_stats()["records_written"] == 0


# ── ShardWriter ─────────────────────────────────────────────────────────


def test_shard_writer_write_and_close(tmp_path):
    """ShardWriter.write 应把记录以 JSON 行写入 .jsonl 文件。"""
    w = ShardWriter(str(tmp_path / "2026" / "08" / "11" / "01"))
    w.write({"a": 1})
    w.write({"b": 2})
    w.close()
    lines = (tmp_path / "2026" / "08" / "11" / "01.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"b": 2}]


def test_shard_writer_ensure_open_idempotent(tmp_path):
    """文件句柄已打开时不重复打开。"""
    w = ShardWriter(str(tmp_path / "sub"))
    w._ensure_open()
    handle = w._file_handle
    w._ensure_open()
    assert w._file_handle is handle
    w.close()


def test_shard_writer_close_idempotent(tmp_path):
    """重复 close 无副作用（第二次时 _file_handle 已为 None）。"""
    w = ShardWriter(str(tmp_path / "sub"))
    w.write({"a": 1})
    w.close()
    assert w._file_handle is None
    w.close()  # 不应抛异常


def test_shard_writer_close_flush_error(tmp_path):
    """flush 抛异常时 close 应吞掉异常并将句柄置空。"""
    w = ShardWriter(str(tmp_path / "sub"))
    w.write({"a": 1})
    real_handle = w._file_handle
    fake = mock.Mock()
    fake.flush.side_effect = OSError("io error")
    w._file_handle = fake
    w.close()  # 不抛异常
    assert w._file_handle is None
    real_handle.close()  # 释放真实文件句柄


def test_shard_writer_last_access(tmp_path):
    """last_access 属性返回最后访问时间。"""
    w = ShardWriter(str(tmp_path / "sub"))
    before = time.time()
    assert w.last_access >= before - 1
    w.close()


# ── ShardedLogStorage ───────────────────────────────────────────────────


def test_sharded_get_shard_path(tmp_path):
    """分片路径格式应为 base_dir/年/月/日/小时分片。"""
    import datetime

    dt = datetime.datetime(2026, 8, 11, 10, 30)
    ts = dt.timestamp()  # 与 datetime.fromtimestamp 使用同一本地时区，roundtrip 一致
    s = ShardedLogStorage(str(tmp_path), shard_hours=6)
    # strftime 内部使用 '/' 分隔，os.path.join 仅拼接 base_dir，故此处混用分隔符
    assert s._get_shard_path(ts) == os.path.join(
        str(tmp_path), "2026/08/11/01"  # 10 // 6 == 1
    )


def test_sharded_get_shard_writer_cache(tmp_path):
    """同一分片路径应复用同一个 ShardWriter。"""
    s = ShardedLogStorage(str(tmp_path), shard_hours=24)
    ts = time.time()
    w1 = s._get_shard_writer(ts)
    w2 = s._get_shard_writer(ts)
    assert w1 is w2
    s.close()


def test_sharded_write_uses_default_timestamp(tmp_path):
    """record 缺少 timestamp 时使用当前时间对应的分片。"""
    s = ShardedLogStorage(str(tmp_path), shard_hours=24)
    s.write({"msg": "no-ts"})
    # 今天日期对应的分片文件应存在
    now = time.time()
    shard = s._get_shard_path(now)
    assert os.path.exists(shard + ".jsonl")
    s.close()


def test_sharded_write_with_timestamp(tmp_path):
    """record 带 timestamp 时写入对应分片文件。"""
    s = ShardedLogStorage(str(tmp_path), shard_hours=24)
    ts = time.time()
    s.write({"msg": "with-ts", "timestamp": ts})
    shard = s._get_shard_path(ts)
    # 写入缓冲 64KB，需 flush 后再读文件
    s._shard_cache[shard]._file_handle.flush()
    assert os.path.exists(shard + ".jsonl")
    data = json.loads(open(shard + ".jsonl", encoding="utf-8").read())
    assert data["msg"] == "with-ts"
    s.close()


def test_sharded_cleanup_stale_shards(tmp_path):
    """过期的分片写入器应被清理并关闭。"""
    s = ShardedLogStorage(str(tmp_path), shard_hours=1)
    old_ts = time.time() - 24 * 3600  # 昨天的分片
    new_ts = time.time()
    w_old = s._get_shard_writer(old_ts)
    w_new = s._get_shard_writer(new_ts)
    w_old._last_access = time.time() - 10000  # 手动把访问时间改为过期
    s._get_shard_writer(new_ts)  # 触发 _cleanup_stale_shards
    assert w_old._shard_path not in s._shard_cache
    assert w_old._file_handle is None  # 已被 close
    assert w_new._shard_path in s._shard_cache
    s.close()


def test_sharded_close_closes_all_writers(tmp_path):
    """close 应关闭并清空所有分片写入器。"""
    s = ShardedLogStorage(str(tmp_path), shard_hours=24)
    s.write({"a": 1})
    s.write({"b": 2})
    assert len(s._shard_cache) >= 1
    s.close()
    assert s._shard_cache == {}


# ── OptimizedLogStorage ─────────────────────────────────────────────────


def test_optimized_storage_init_defaults(monkeypatch, tmp_path):
    """未传 db_path/raw_log_dir 时使用 storage 模块的默认常量。"""
    db = str(tmp_path / "d.db")
    raw = str(tmp_path / "raw")
    monkeypatch.setattr("agent.log_system.storage.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("agent.log_system.storage.DEFAULT_RAW_DIR", raw)
    s = OptimizedLogStorage()
    assert s.db_path == db
    assert s.raw_log_dir == raw
    s.close()


def test_optimized_get_conn_creates_and_caches(storage):
    """_get_conn 首次创建连接（PRAGMA 生效），同线程后续复用同一连接。"""
    conn = storage._get_conn()
    assert isinstance(conn, sqlite3.Connection)
    assert conn.row_factory is sqlite3.Row
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert storage._get_conn() is conn


def test_optimized_initialize_creates_schema(storage):
    """initialize 应建表、置 _initialized 并启动批量写入线程。"""
    assert storage._initialized is True
    conn = storage._get_conn()
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "logs_operation" in tables
    assert "logs_performance" in tables
    assert "logs_error" in tables
    assert storage._batch_writer._flush_thread is not None
    assert storage._batch_writer._flush_thread.is_alive()


def test_optimized_initialize_idempotent(storage):
    """_initialized 后再次 initialize 直接返回，不重复建表/重启线程。"""
    thread = storage._batch_writer._flush_thread
    storage.initialize()
    assert storage._batch_writer._flush_thread is thread


def test_optimized_bulk_write_empty(storage):
    """空 records 列表时 _bulk_write_to_db 直接返回。"""
    storage._bulk_write_to_db([])
    assert storage._stats["batch_writes"] == 0


def test_optimized_bulk_write_normal(storage):
    """批量写入应真实落库并累计 batch_writes 统计。"""
    records = [
        {
            "table": "logs_operation",
            "columns": ["timestamp", "operation", "message"],
            "values": [1.0, "op-1", "hello-1"],
        },
        {
            "table": "logs_operation",
            "columns": ["timestamp", "operation", "message"],
            "values": [2.0, "op-2", "hello-2"],
        },
    ]
    storage._bulk_write_to_db(records)
    assert storage._stats["batch_writes"] == 2
    rows = storage._get_conn().execute(
        "SELECT message FROM logs_operation ORDER BY timestamp"
    ).fetchall()
    assert [r[0] for r in rows] == ["hello-1", "hello-2"]


def test_optimized_bulk_write_skip_empty_columns(storage):
    """columns/values 为空的记录应被跳过，但不影响统计计数。"""
    records = [
        {"table": "logs_operation", "columns": [], "values": []},
        {
            "table": "logs_operation",
            "columns": ["timestamp", "operation"],
            "values": [3.0, "op-3"],
        },
    ]
    storage._bulk_write_to_db(records)
    assert storage._stats["batch_writes"] == 2
    rows = storage._get_conn().execute(
        "SELECT timestamp FROM logs_operation"
    ).fetchall()
    assert [r[0] for r in rows] == [3.0]


def test_optimized_bulk_write_single_error(storage):
    """单条绑定参数数量不匹配时记录失败但循环继续，其余记录正常落库。"""
    records = [
        {"table": "logs_operation", "columns": ["timestamp"], "values": [1.0, 2.0]},
        {
            "table": "logs_operation",
            "columns": ["timestamp", "operation"],
            "values": [4.0, "op-4"],
        },
    ]
    storage._bulk_write_to_db(records)
    assert storage._stats["batch_writes"] == 2
    assert storage._stats["errors"] == 0  # 单条失败不累计整体错误
    rows = storage._get_conn().execute(
        "SELECT timestamp FROM logs_operation"
    ).fetchall()
    assert [r[0] for r in rows] == [4.0]


def test_optimized_bulk_write_commit_error(storage):
    """commit 抛异常时回滚并累计 errors。"""
    mock_conn = mock.Mock()
    mock_cursor = mock_conn.cursor.return_value
    mock_conn.commit.side_effect = sqlite3.OperationalError("disk full")
    records = [
        {"table": "logs_operation", "columns": ["timestamp"], "values": [1.0]}
    ]
    with mock.patch.object(storage, "_get_conn", return_value=mock_conn):
        storage._bulk_write_to_db(records)
    assert storage._stats["errors"] == 1
    mock_conn.rollback.assert_called_once()
    mock_cursor.close.assert_called_once()


def test_optimized_write_entry(storage):
    """write_entry_optimized 构造完整 record 入队（枚举 level/category 取 value，message 截断 200）。"""
    entry = SimpleNamespace(
        timestamp=1.0,
        level=LogLevel.INFO,
        category=LogCategory.OPERATION,
        message="x" * 300,
        source="src",
        user_id="u1",
        trace_id="tid",
        duration_ms=12.5,
        tags=["t1"],
        metadata={"k": "v"},
    )
    assert storage.write_entry_optimized(entry) is True
    record = storage._batch_writer._queue[0]
    assert record["table"] == "logs_operation"
    assert record["values"][1] == "info"
    assert record["values"][2] == "operation"
    assert record["values"][3] == "x" * 200  # 截断
    assert record["values"][11] == "x" * 300  # 完整消息
    assert json.loads(record["values"][9]) == ["t1"]
    assert json.loads(record["values"][10]) == {"k": "v"}


def test_optimized_write_entry_plain_level_category(storage):
    """level/category 无 .value 属性时原样使用（非枚举分支）。"""
    entry = SimpleNamespace(
        timestamp=1.0,
        level="warn",
        category="custom",
        message="msg",
        source="src",
        user_id="u1",
        trace_id="tid",
        duration_ms=0.0,
        tags=[],
        metadata={},
    )
    assert storage.write_entry_optimized(entry) is True
    record = storage._batch_writer._queue[0]
    assert record["values"][1] == "warn"
    assert record["values"][2] == "custom"


def test_optimized_write_performance(storage):
    """write_performance_optimized 构造性能记录入队。"""
    record = SimpleNamespace(
        timestamp=1.0,
        metric_name="latency",
        value=150.5,
        unit="ms",
        source="test",
        tags={"node": "n1"},
    )
    assert storage.write_performance_optimized(record) is True
    r = storage._batch_writer._queue[0]
    assert r["table"] == "logs_performance"
    assert r["values"][1] == "latency"
    assert json.loads(r["values"][5]) == {"node": "n1"}


def test_optimized_write_error_with_traceback(storage):
    """write_error_optimized 对非空 traceback 截断到 5000 字符。"""
    record = SimpleNamespace(
        timestamp=1.0,
        severity="error",
        message="boom" * 300,
        source="src",
        exception_type="ValueError",
        traceback="t" * 6000,
        context={"a": 1},
        resolved=True,
    )
    assert storage.write_error_optimized(record) is True
    r = storage._batch_writer._queue[0]
    assert r["table"] == "logs_error"
    assert r["values"][2] == "boom" * 250  # message[:1000] 截断（300*4=1200 -> 1000）
    assert r["values"][5] == "t" * 5000
    assert r["values"][7] == 1


def test_optimized_write_error_without_traceback(storage):
    """traceback 为空时写入空字符串，resolved=False 写入 0。"""
    record = SimpleNamespace(
        timestamp=1.0,
        severity="warning",
        message="m",
        source="src",
        exception_type="",
        traceback=None,
        context={},
        resolved=False,
    )
    assert storage.write_error_optimized(record) is True
    r = storage._batch_writer._queue[0]
    assert r["values"][5] == ""
    assert r["values"][7] == 0


def test_optimized_write_raw(storage):
    """write_raw_optimized 写入分片 JSONL 并累计 raw_writes。"""
    storage.write_raw_optimized("chat", {"msg": "hi", "timestamp": time.time()})
    assert storage._stats["raw_writes"] == 1
    # 分片文件存在；写入缓冲 64KB，先 flush 再读
    assert len(storage._shard_storage._shard_cache) == 1
    shard_path = next(iter(storage._shard_storage._shard_cache))
    storage._shard_storage._shard_cache[shard_path]._file_handle.flush()
    data = json.loads(open(shard_path + ".jsonl", encoding="utf-8").read())
    assert data["category"] == "chat"
    assert data["msg"] == "hi"


def test_optimized_write_direct_success(storage):
    """write_direct 同步写入成功并累计 direct_writes。"""
    ok = storage.write_direct(
        "logs_operation",
        ["timestamp", "operation", "message"],
        (5.0, "direct-op", "direct"),
    )
    assert ok is True
    assert storage._stats["direct_writes"] == 1
    rows = storage._get_conn().execute(
        "SELECT message FROM logs_operation WHERE message='direct'"
    ).fetchall()
    assert len(rows) == 1


def test_optimized_write_direct_failure(storage):
    """目标表不存在时 write_direct 返回 False 并累计 errors。"""
    ok = storage.write_direct("logs_nonexist", ["timestamp"], (1.0,))
    assert ok is False
    assert storage._stats["errors"] == 1
    assert storage._stats["direct_writes"] == 0


def test_optimized_get_stats(storage):
    """get_stats 返回存储统计与批量写入器统计。"""
    storage.write_raw_optimized("cat", {"timestamp": time.time()})
    stats = storage.get_stats()
    assert stats["storage"]["raw_writes"] == 1
    assert "batch_writer" in stats
    assert "records_written" in stats["batch_writer"]


def test_optimized_close_closes_conn(storage):
    """close 后线程本地连接被置空。"""
    storage._get_conn()
    assert storage._local.conn is not None
    storage.close()
    assert storage._local.conn is None


def test_optimized_close_conn_close_error(storage):
    """close 时 conn.close 抛异常应被吞掉且连接仍置空。"""
    storage._get_conn()
    fake = mock.Mock()
    fake.close.side_effect = sqlite3.OperationalError("already closed")
    storage._local.conn = fake
    storage.close()  # 不抛异常
    assert storage._local.conn is None


def test_optimized_close_without_conn(tmp_path):
    """从未获取连接时 close 不应抛异常（hasattr/None 分支）。"""
    s = OptimizedLogStorage(
        db_path=str(tmp_path / "d.db"), raw_log_dir=str(tmp_path / "raw")
    )
    s.close()
    s.close()  # 幂等


# ── 模块级函数 ──────────────────────────────────────────────────────────


def test_create_optimized_storage(monkeypatch, tmp_path):
    """_create_optimized_storage 工厂返回 OptimizedLogStorage 实例。"""
    monkeypatch.setattr("agent.log_system.storage.DEFAULT_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setattr("agent.log_system.storage.DEFAULT_RAW_DIR", str(tmp_path / "raw"))
    inst = mod._create_optimized_storage(config={"x": 1})
    assert isinstance(inst, OptimizedLogStorage)
    inst.close()


def test_get_optimized_storage_singleton_path(monkeypatch, tmp_path):
    """_SINGLETON_AVAILABLE=True 时走 SingletonManager，重复获取同一实例。"""
    from agent.utils.singleton_manager import reset_singleton

    # 单例工厂在模块导入时已注册，故把默认路径指向 tmp_path 以隔离副作用
    monkeypatch.setattr("agent.log_system.storage.DEFAULT_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setattr("agent.log_system.storage.DEFAULT_RAW_DIR", str(tmp_path / "raw"))
    reset_singleton("optimized_storage")  # 清理可能的残留实例
    try:
        a = mod.get_optimized_storage()
        b = mod.get_optimized_storage()
        assert a is b
        assert isinstance(a, OptimizedLogStorage)
        a.close()
    finally:
        reset_singleton("optimized_storage")


def test_get_optimized_storage_fallback_path(monkeypatch):
    """_SINGLETON_AVAILABLE=False 时走模块级全局缓存，重复获取同一实例。"""
    monkeypatch.setattr(mod, "_SINGLETON_AVAILABLE", False)
    monkeypatch.setattr(mod, "_global_optimized_storage", None)
    fake = mock.Mock()
    monkeypatch.setattr(mod, "_create_optimized_storage", lambda config=None: fake)
    assert mod.get_optimized_storage() is fake
    assert mod.get_optimized_storage() is fake  # 第二次命中缓存


def test_module_import_fallback_without_singleton_manager(monkeypatch):
    """模拟 singleton_manager 不可导入：模块应 fallback 到 _SINGLETON_AVAILABLE=False。"""
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "agent.utils.singleton_manager", None)
    importlib.reload(mod)
    try:
        assert mod._SINGLETON_AVAILABLE is False
        assert mod.register_singleton is None
        assert mod.get_singleton is None
        assert _trace_id()  # 其余功能不受影响
    finally:
        # 恢复 singleton_manager 可导入状态并重新加载模块
        sys.modules.pop("agent.utils.singleton_manager", None)
        importlib.reload(mod)
        assert mod._SINGLETON_AVAILABLE is True
        assert mod.register_singleton is not None
        from agent.utils.singleton_manager import reset_singleton

        reset_singleton("optimized_storage")  # 清理 reload 期间注册的残留实例
