#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户行为回放存储层单元测试

覆盖维度：
- 存储初始化（自动建目录、SQLite 表结构）
- store 正常存储（gzip 文件 + SQLite 元数据）
- store 参数校验（replay_id 空、data 空、timestamp 非法）
- 查询接口（get_by_id / get_data_by_id / list_by_trace_id / list_by_time_range）
- 关联统计（get_correlation_stats）
- 清理过期数据（cleanup_old_records）
- 单次回放压缩后 < 500KB
- 边界场景（不存在的 replay_id、空数据库查询）

测试目标：覆盖率 ≥ 80%，所有失败路径均有断言。
"""

import base64
import gzip
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.monitoring.replay_storage import (
    ReplayStorage,
    ReplayStorageError,
    get_replay_storage,
    REPLAY_ERR_INVALID_INPUT,
    REPLAY_ERR_STORAGE_FAILED,
    REPLAY_ERR_DB_FAILED,
    REPLAY_ERR_NOT_FOUND,
    REPLAY_ERR_DECODE_FAILED,
)


# ─── 测试夹具 ─────────────────────────────────────────────────────────

@pytest.fixture
def temp_storage(tmp_path):
    """临时回放存储（每个测试独立目录）"""
    storage = ReplayStorage(str(tmp_path / "replays"))
    yield storage
    storage.close()


@pytest.fixture
def sample_events():
    """构造一份示例 rrweb 事件数据"""
    now_ms = int(datetime.now().timestamp() * 1000)
    return [
        {"type": 4, "data": {}, "timestamp": now_ms},
        {"type": 2, "data": {"node": {"type": 0, "childNodes": []}}, "timestamp": now_ms + 10},
        {"type": 5, "data": {"x": 100, "y": 200, "id": 1}, "timestamp": now_ms + 20},
    ]


# ─── 初始化测试 ──────────────────────────────────────────────────────

class TestReplayStorageInit:
    """回放存储初始化测试"""

    def test_init_creates_storage_root(self, tmp_path):
        """初始化应自动创建存储根目录"""
        root = tmp_path / "replays"
        assert not root.exists()
        storage = ReplayStorage(str(root))
        assert root.exists()
        storage.close()

    def test_init_creates_db_file(self, tmp_path):
        """初始化应创建 SQLite 数据库文件"""
        storage = ReplayStorage(str(tmp_path / "replays"))
        assert (tmp_path / "replays" / "replay_meta.db").exists()
        storage.close()

    def test_init_creates_schema(self, tmp_path):
        """初始化应创建 replay 表与索引"""
        import sqlite3
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 查询 sqlite_master 验证表存在
        conn = sqlite3.connect(str(tmp_path / "replays" / "replay_meta.db"))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='replay'"
        )
        assert cursor.fetchone() is not None
        # 验证索引存在
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_replay_trace_id'"
        )
        assert cursor.fetchone() is not None
        conn.close()
        storage.close()

    def test_get_global_singleton(self, tmp_path, monkeypatch):
        """get_replay_storage 应返回全局单例"""
        # 重置全局单例
        import agent.monitoring.replay_storage as mod
        monkeypatch.setattr(mod, "_global_storage", None)
        storage1 = get_replay_storage(str(tmp_path / "replays"))
        storage2 = get_replay_storage(str(tmp_path / "replays2"))
        assert storage1 is storage2
        storage1.close()


# ─── store 存储测试 ──────────────────────────────────────────────────

class TestReplayStore:
    """store 方法测试"""

    def test_store_basic(self, temp_storage, sample_events):
        """正常存储应返回元数据字典"""
        data_str = json.dumps(sample_events)
        meta = temp_storage.store(
            replay_id="r-001",
            data=data_str,
            trace_id="trace-abc",
            user_session_id="user-xyz",
            error_id="err-001",
            duration_sec=30,
            event_count=len(sample_events),
        )
        # store 返回字段：replay_id / file_path / size_bytes / stored
        assert meta["replay_id"] == "r-001"
        assert meta["size_bytes"] > 0
        assert meta["stored"] is True
        # 文件应实际存在
        assert Path(meta["file_path"]).exists()
        # 验证元数据已写入 DB
        record = temp_storage.get_by_id("r-001")
        assert record is not None
        assert record["trace_id"] == "trace-abc"
        assert record["user_session_id"] == "user-xyz"
        assert record["error_id"] == "err-001"

    def test_store_creates_date_dir(self, temp_storage, sample_events):
        """存储应按日期分目录（YYYYMMDD）"""
        ts = "2026-06-26T10:00:00"
        meta = temp_storage.store(
            replay_id="r-date",
            data=json.dumps(sample_events),
            timestamp=ts,
        )
        # 文件路径应包含 20260626
        assert "20260626" in meta["file_path"]

    def test_store_compressed_data(self, temp_storage, sample_events):
        """压缩数据（gzip-base64）应正确解码后重新存储"""
        raw_bytes = json.dumps(sample_events).encode("utf-8")
        gz_bytes = gzip.compress(raw_bytes)
        b64_str = base64.b64encode(gz_bytes).decode("ascii")
        meta = temp_storage.store(
            replay_id="r-gz",
            data=b64_str,
            compressed=True,
            encoding="gzip-base64",
        )
        # 验证文件可解压回原始 JSON
        with gzip.open(meta["file_path"], "rb") as f:
            content = f.read().decode("utf-8")
        assert json.loads(content) == sample_events

    def test_store_size_under_500kb(self, temp_storage):
        """单次回放压缩后应 < 500KB（典型场景）"""
        # 构造 1000 个事件（模拟 30 秒回放）
        now_ms = int(datetime.now().timestamp() * 1000)
        events = [
            {"type": 5, "data": {"x": i, "y": i * 2}, "timestamp": now_ms + i * 30}
            for i in range(1000)
        ]
        meta = temp_storage.store(
            replay_id="r-size",
            data=json.dumps(events),
            event_count=1000,
        )
        # gzip 压缩后应远小于 500KB
        assert meta["size_bytes"] < 500 * 1024, (
            f"压缩后大小 {meta['size_bytes']} 超过 500KB"
        )

    def test_store_empty_replay_id_raises(self, temp_storage, sample_events):
        """replay_id 为空应抛出 ReplayStorageError"""
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.store(
                replay_id="",
                data=json.dumps(sample_events),
            )
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_none_replay_id_raises(self, temp_storage, sample_events):
        """replay_id 为 None 应抛出 ReplayStorageError"""
        with pytest.raises(ReplayStorageError):
            temp_storage.store(
                replay_id=None,  # type: ignore
                data=json.dumps(sample_events),
            )

    def test_store_empty_data_raises(self, temp_storage):
        """data 为空应抛出 ReplayStorageError"""
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.store(replay_id="r-empty", data="")
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_invalid_timestamp_raises(self, temp_storage, sample_events):
        """timestamp 格式非法应抛出 ReplayStorageError"""
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.store(
                replay_id="r-bad-ts",
                data=json.dumps(sample_events),
                timestamp="not-a-date",
            )
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_replaces_existing(self, temp_storage, sample_events):
        """重复 replay_id 应覆盖（INSERT OR REPLACE）"""
        data1 = json.dumps(sample_events)
        data2 = json.dumps(sample_events + [{"type": 99}])
        temp_storage.store(replay_id="r-dup", data=data1)
        meta2 = temp_storage.store(replay_id="r-dup", data=data2)
        # 数据库中应只有 1 条记录
        records = temp_storage.list_by_trace_id("", limit=10)
        # list_by_trace_id 在 trace_id 为空时返回空，用 list_recent_24h 验证
        records = temp_storage.list_recent_24h()
        dup_records = [r for r in records if r["replay_id"] == "r-dup"]
        assert len(dup_records) == 1
        assert dup_records[0]["replay_id"] == "r-dup"


# ─── 查询测试 ────────────────────────────────────────────────────────

class TestReplayQuery:
    """查询接口测试"""

    def _seed(self, storage, count=3):
        """插入 count 条测试数据"""
        for i in range(count):
            storage.store(
                replay_id=f"r-{i:03d}",
                data=json.dumps([{"type": 4, "timestamp": i}]),
                trace_id=f"trace-{i}",
                user_session_id=f"user-{i}",
                error_id=f"err-{i}" if i % 2 == 0 else None,
                timestamp=(datetime.now() - timedelta(minutes=i)).isoformat(),
            )

    def test_get_by_id_exists(self, temp_storage):
        """get_by_id 应返回存在的记录"""
        self._seed(temp_storage, count=1)
        meta = temp_storage.get_by_id("r-000")
        assert meta is not None
        assert meta["replay_id"] == "r-000"
        assert meta["trace_id"] == "trace-0"

    def test_get_by_id_not_exists(self, temp_storage):
        """get_by_id 不存在时返回 None"""
        self._seed(temp_storage, count=1)
        assert temp_storage.get_by_id("nonexistent") is None

    def test_get_data_by_id_decodes_gzip(self, temp_storage, sample_events):
        """get_data_by_id 应解码 gzip 返回 JSON 字符串"""
        temp_storage.store(
            replay_id="r-data",
            data=json.dumps(sample_events),
        )
        data_str = temp_storage.get_data_by_id("r-data")
        assert data_str is not None
        decoded = json.loads(data_str)
        assert decoded == sample_events

    def test_get_data_by_id_not_exists(self, temp_storage):
        """get_data_by_id 不存在时返回 None"""
        assert temp_storage.get_data_by_id("nonexistent") is None

    def test_get_data_by_id_file_missing_raises(self, temp_storage, sample_events):
        """文件丢失时应抛出 ReplayStorageError（REPLAY_ERR_STORAGE_FAILED）"""
        meta = temp_storage.store(
            replay_id="r-missing",
            data=json.dumps(sample_events),
        )
        # 删除文件
        Path(meta["file_path"]).unlink()
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_data_by_id("r-missing")
        # 源码使用 REPLAY_ERR_STORAGE_FAILED（文件丢失属于存储层失败）
        assert exc_info.value.code == REPLAY_ERR_STORAGE_FAILED

    def test_list_by_trace_id(self, temp_storage):
        """list_by_trace_id 应按 trace_id 过滤"""
        self._seed(temp_storage, count=3)
        records = temp_storage.list_by_trace_id("trace-1")
        assert len(records) == 1
        assert records[0]["trace_id"] == "trace-1"

    def test_list_by_trace_id_empty_returns_empty(self, temp_storage):
        """list_by_trace_id 传入空字符串应返回空列表"""
        self._seed(temp_storage, count=2)
        assert temp_storage.list_by_trace_id("") == []

    def test_list_by_trace_id_limit(self, temp_storage):
        """list_by_trace_id 应受 limit 限制"""
        # 同一 trace_id 插入 5 条
        for i in range(5):
            temp_storage.store(
                replay_id=f"r-lim-{i}",
                data=json.dumps([{"type": 4}]),
                trace_id="same-trace",
            )
        records = temp_storage.list_by_trace_id("same-trace", limit=3)
        assert len(records) == 3

    def test_list_by_time_range(self, temp_storage):
        """list_by_time_range 应按时间过滤"""
        # 插入 1 小时前和 5 小时前各一条
        old_ts = (datetime.now() - timedelta(hours=5)).isoformat()
        new_ts = (datetime.now() - timedelta(hours=1)).isoformat()
        temp_storage.store(replay_id="r-old", data="[]", timestamp=old_ts)
        temp_storage.store(replay_id="r-new", data="[]", timestamp=new_ts)
        # 查询最近 3 小时
        cutoff = (datetime.now() - timedelta(hours=3)).isoformat()
        now = datetime.now().isoformat()
        records = temp_storage.list_by_time_range(cutoff, now)
        ids = [r["replay_id"] for r in records]
        assert "r-new" in ids
        assert "r-old" not in ids

    def test_list_by_user_session(self, temp_storage):
        """list_by_user_session 应按 user_session_id 过滤"""
        self._seed(temp_storage, count=3)
        records = temp_storage.list_by_user_session("user-2")
        assert len(records) == 1
        assert records[0]["user_session_id"] == "user-2"

    def test_list_recent_24h(self, temp_storage):
        """list_recent_24h 应只返回最近 24 小时的记录"""
        # 插入 1 小时前和 25 小时前各一条
        recent_ts = (datetime.now() - timedelta(hours=1)).isoformat()
        old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        temp_storage.store(replay_id="r-recent", data="[]", timestamp=recent_ts)
        temp_storage.store(replay_id="r-old24", data="[]", timestamp=old_ts)
        records = temp_storage.list_recent_24h()
        ids = [r["replay_id"] for r in records]
        assert "r-recent" in ids
        assert "r-old24" not in ids


# ─── 关联统计测试 ────────────────────────────────────────────────────

class TestCorrelationStats:
    """关联统计测试"""

    def test_empty_stats(self, temp_storage):
        """空数据库统计应全部为 0"""
        stats = temp_storage.get_correlation_stats(hours=24)
        assert stats["total_replays"] == 0
        assert stats["with_trace_id"] == 0
        assert stats["with_user_session_id"] == 0
        assert stats["with_error_id"] == 0
        assert stats["fully_correlated"] == 0
        assert stats["by_error_id"] == []
        assert stats["window_hours"] == 24

    def test_stats_counts(self, temp_storage):
        """统计应正确计数"""
        # 3 条完整关联
        for i in range(3):
            temp_storage.store(
                replay_id=f"r-full-{i}",
                data=json.dumps([{"type": 4}]),
                trace_id=f"trace-{i}",
                user_session_id=f"user-{i}",
                error_id=f"err-{i}",
            )
        # 1 条仅 trace_id（无 error_id）
        temp_storage.store(
            replay_id="r-partial",
            data=json.dumps([{"type": 4}]),
            trace_id="trace-partial",
            user_session_id="user-partial",
        )
        stats = temp_storage.get_correlation_stats(hours=24)
        assert stats["total_replays"] == 4
        assert stats["with_trace_id"] == 4
        assert stats["with_user_session_id"] == 4
        assert stats["with_error_id"] == 3
        assert stats["fully_correlated"] == 3

    def test_stats_by_error_id(self, temp_storage):
        """by_error_id 应按 error_id 分组并按数量倒序"""
        # err-A 出现 2 次，err-B 出现 1 次
        for i in range(2):
            temp_storage.store(
                replay_id=f"r-A-{i}",
                data=json.dumps([{"type": 4}]),
                error_id="err-A",
            )
        temp_storage.store(
            replay_id="r-B-0",
            data=json.dumps([{"type": 4}]),
            error_id="err-B",
        )
        stats = temp_storage.get_correlation_stats(hours=24)
        by_error = stats["by_error_id"]
        assert len(by_error) == 2
        # err-A 应排在前面
        assert by_error[0]["error_id"] == "err-A"
        assert by_error[0]["count"] == 2
        assert by_error[1]["error_id"] == "err-B"
        assert by_error[1]["count"] == 1

    def test_stats_excludes_old_records(self, temp_storage):
        """统计应排除超过时间窗口的记录"""
        # 25 小时前的记录
        old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        temp_storage.store(
            replay_id="r-old-stats",
            data=json.dumps([{"type": 4}]),
            trace_id="trace-old",
            timestamp=old_ts,
        )
        stats = temp_storage.get_correlation_stats(hours=24)
        assert stats["total_replays"] == 0


# ─── 清理测试 ────────────────────────────────────────────────────────

class TestCleanup:
    """清理过期数据测试"""

    def test_cleanup_removes_old_records(self, temp_storage, sample_events):
        """cleanup_old_records 应删除超过指定天数的记录"""
        old_ts = (datetime.now() - timedelta(days=40)).isoformat()
        new_ts = (datetime.now() - timedelta(days=1)).isoformat()
        old_meta = temp_storage.store(
            replay_id="r-old-clean",
            data=json.dumps(sample_events),
            timestamp=old_ts,
        )
        new_meta = temp_storage.store(
            replay_id="r-new-clean",
            data=json.dumps(sample_events),
            timestamp=new_ts,
        )
        cleaned = temp_storage.cleanup_old_records(days=30)
        assert cleaned == 1
        # 旧文件应被删除
        assert not Path(old_meta["file_path"]).exists()
        # 新文件应保留
        assert Path(new_meta["file_path"]).exists()
        # 数据库中也应只剩 1 条
        assert temp_storage.get_by_id("r-old-clean") is None
        assert temp_storage.get_by_id("r-new-clean") is not None

    def test_cleanup_with_default_days(self, temp_storage, sample_events):
        """默认保留 30 天"""
        recent_ts = (datetime.now() - timedelta(days=10)).isoformat()
        temp_storage.store(
            replay_id="r-recent-clean",
            data=json.dumps(sample_events),
            timestamp=recent_ts,
        )
        cleaned = temp_storage.cleanup_old_records()
        assert cleaned == 0
        assert temp_storage.get_by_id("r-recent-clean") is not None


# ─── 边界场景测试 ────────────────────────────────────────────────────

class TestEdgeCases:
    """边界场景测试"""

    def test_store_failure_rolls_back_file(self, temp_storage, monkeypatch, sample_events):
        """数据库写入失败时应清理已写入的文件"""
        import sqlite3 as _sqlite3

        # 先正常存储一条，确保文件写入逻辑可走通
        temp_storage.store(
            replay_id="r-normal",
            data=json.dumps(sample_events),
        )

        # 用 mock 替换 _conn，使其 execute 在写入元数据时抛 sqlite3.Error
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = _sqlite3.Error("DB error")
        monkeypatch.setattr(temp_storage, "_conn", mock_conn)

        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.store(
                replay_id="r-rollback",
                data=json.dumps(sample_events),
            )
        assert exc_info.value.code == REPLAY_ERR_DB_FAILED
        # 文件应已被清理（store 中 DB 失败时回滚已写文件）
        # 注意：源码属性 storage_root 是字符串，需用 Path 包装后 rglob
        files = list(Path(temp_storage.storage_root).rglob("r-rollback*"))
        assert len(files) == 0

    def test_list_empty_storage(self, temp_storage):
        """空数据库的查询接口都应返回空列表"""
        assert temp_storage.list_by_trace_id("any") == []
        assert temp_storage.list_by_user_session("any") == []
        assert temp_storage.list_recent_24h() == []
        cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
        now = datetime.now().isoformat()
        assert temp_storage.list_by_time_range(cutoff, now) == []

    def test_list_by_user_session_with_empty_id(self, temp_storage):
        """user_session_id 为空时应直接返回空列表（边界保护）"""
        assert temp_storage.list_by_user_session("") == []
        assert temp_storage.list_by_user_session(None) == []

    def test_get_by_id_db_failure_raises(self, temp_storage, monkeypatch, sample_events):
        """数据库查询失败时 get_by_id 应抛出 ReplayStorageError（边界显性化）"""
        meta = temp_storage.store(
            replay_id="r-db-fail",
            data=json.dumps(sample_events),
        )
        # 替换 _conn 为抛出 sqlite3.Error 的 mock
        import sqlite3 as _sqlite3
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = _sqlite3.Error("DB error")
        monkeypatch.setattr(temp_storage, "_conn", mock_conn)
        # 源码在 DB 失败时抛出 ReplayStorageError（不静默返回 None）
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_by_id("r-db-fail")
        assert exc_info.value.code == REPLAY_ERR_DB_FAILED

    def test_get_correlation_stats_db_failure_raises(self, temp_storage, monkeypatch):
        """关联统计查询失败时抛出 ReplayStorageError（边界显性化）"""
        import sqlite3 as _sqlite3
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = _sqlite3.Error("DB error")
        monkeypatch.setattr(temp_storage, "_conn", mock_conn)
        # 源码在 DB 失败时抛出异常，不返回降级空统计
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_correlation_stats(hours=24)
        assert exc_info.value.code == REPLAY_ERR_DB_FAILED

    def test_close_is_idempotent(self, temp_storage):
        """close() 可被多次调用，不抛异常"""
        temp_storage.close()
        # 再次调用不应抛异常
        temp_storage.close()


# ═══════════════════════════════════════════════════════════════
# P0 高风险分支测试
# 覆盖行号：431-433（gzip 解压失败）、582-590（三向关联统计）、615（DB 异常）
# 状态同步机制：tmp_path 隔离文件系统 + monkeypatch 隔离 mock
# ═══════════════════════════════════════════════════════════════


class TestGzipDecodeFailure:
    """P0-4: 覆盖 get_data_by_id 中 gzip 解压失败分支（行 425-437）

    场景：DB 中记录为 gzip 压缩，但磁盘上的文件已损坏/被篡改。
    预期：抛出 ReplayStorageError(REPLAY_ERR_DECODE_FAILED)。
    """

    def test_corrupt_gzip_raises_decode_error(self, temp_storage, sample_events):
        """损坏的 gzip 数据应抛出 REPLAY_ERR_DECODE_FAILED"""
        import gzip as gzip_module

        # 1. 正常存储一条 gzip-base64 记录
        raw_bytes = json.dumps(sample_events).encode("utf-8")
        gz_bytes = gzip_module.compress(raw_bytes)
        b64_str = base64.b64encode(gz_bytes).decode("ascii")
        meta = temp_storage.store(
            replay_id="r-corrupt-gzip",
            data=b64_str,
            compressed=True,
            encoding="gzip-base64",
        )
        file_path = meta["file_path"]

        # 2. 用损坏数据覆盖文件
        with open(file_path, "wb") as f:
            f.write(b"this-is-not-valid-gzip-data!!!")

        # 3. 读取应抛出 REPLAY_ERR_DECODE_FAILED
        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_data_by_id("r-corrupt-gzip")
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED

    def test_truncated_gzip_raises_decode_error(self, temp_storage, sample_events):
        """截断的 gzip 数据应抛出 REPLAY_ERR_DECODE_FAILED"""
        import gzip as gzip_module

        raw_bytes = json.dumps(sample_events).encode("utf-8")
        gz_bytes = gzip_module.compress(raw_bytes)
        b64_str = base64.b64encode(gz_bytes).decode("ascii")
        meta = temp_storage.store(
            replay_id="r-truncated",
            data=b64_str,
            compressed=True,
            encoding="gzip-base64",
        )
        file_path = meta["file_path"]

        # 截断 gzip 数据（保留前半部分）
        with open(file_path, "rb") as f:
            original = f.read()
        with open(file_path, "wb") as f:
            f.write(original[:len(original) // 2])

        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_data_by_id("r-truncated")
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED

    def test_empty_gzip_file_raises_decode_error(self, temp_storage, sample_events):
        """不完整的 gzip 头应抛出 REPLAY_ERR_DECODE_FAILED

        注意：gzip.decompress(b"") 在 Python 3.12 返回 b"" 不抛异常，
        因此用不完整的 gzip magic + 少量字节来触发 EOFError。
        """
        import gzip as gzip_module

        raw_bytes = json.dumps(sample_events).encode("utf-8")
        gz_bytes = gzip_module.compress(raw_bytes)
        b64_str = base64.b64encode(gz_bytes).decode("ascii")
        meta = temp_storage.store(
            replay_id="r-empty-gz",
            data=b64_str,
            compressed=True,
            encoding="gzip-base64",
        )
        file_path = meta["file_path"]

        # 写入不完整的 gzip 数据（仅 magic + 少量字节，触发 EOFError）
        with open(file_path, "wb") as f:
            f.write(b"\x1f\x8b\x08\x00corrupt-data")

        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_data_by_id("r-empty-gz")
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED

    def test_random_bytes_gzip_raises(self, temp_storage, sample_events):
        """随机字节作为 gzip 数据应抛出解压错误"""
        import gzip as gzip_module
        import os as _os

        raw_bytes = json.dumps(sample_events).encode("utf-8")
        gz_bytes = gzip_module.compress(raw_bytes)
        b64_str = base64.b64encode(gz_bytes).decode("ascii")
        meta = temp_storage.store(
            replay_id="r-random-bytes",
            data=b64_str,
            compressed=True,
            encoding="gzip-base64",
        )
        file_path = meta["file_path"]

        # 写入随机字节
        with open(file_path, "wb") as f:
            f.write(_os.urandom(256))

        with pytest.raises(ReplayStorageError) as exc_info:
            temp_storage.get_data_by_id("r-random-bytes")
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED


class TestComplexCorrelationStats:
    """P0-5: 覆盖 get_correlation_stats 三向关联统计（行 580-597）

    构造包含复杂关联关系的 Mock 数据集：
    - 完整三向关联（trace_id + user_session_id + error_id 齐全）
    - 缺失 error_id（仅 trace_id + user_session_id）
    - 缺失 user_session_id（仅 trace_id + error_id）
    - 缺失 trace_id（仅 user_session_id + error_id）
    - 仅 error_id
    - 空字段（全部为 None/空字符串）
    - 多条同 error_id（验证 by_error_id 分组排序）
    - 超出时间窗口的记录（验证时间过滤）
    """

    @pytest.fixture
    def complex_storage(self, temp_storage):
        """构造复杂关联数据集"""
        now = datetime.now()

        # ── 时间窗口内的记录（hours=24）──
        records_in_window = [
            # 1. 完整三向关联（3 条）
            {"replay_id": "r-full-001", "trace_id": "trace-001", "user_session_id": "sess-001",
             "error_id": "err-A", "timestamp": (now - timedelta(minutes=5)).isoformat()},
            {"replay_id": "r-full-002", "trace_id": "trace-002", "user_session_id": "sess-002",
             "error_id": "err-B", "timestamp": (now - timedelta(minutes=10)).isoformat()},
            {"replay_id": "r-full-003", "trace_id": "trace-003", "user_session_id": "sess-003",
             "error_id": "err-A", "timestamp": (now - timedelta(minutes=15)).isoformat()},

            # 2. 缺失 error_id（trace + session 有，error 无）
            {"replay_id": "r-no-err-001", "trace_id": "trace-101", "user_session_id": "sess-101",
             "error_id": None, "timestamp": (now - timedelta(minutes=20)).isoformat()},
            {"replay_id": "r-no-err-002", "trace_id": "trace-102", "user_session_id": "sess-102",
             "error_id": "", "timestamp": (now - timedelta(minutes=25)).isoformat()},

            # 3. 缺失 user_session_id（trace + error 有，session 无）
            {"replay_id": "r-no-sess-001", "trace_id": "trace-201", "user_session_id": None,
             "error_id": "err-C", "timestamp": (now - timedelta(minutes=30)).isoformat()},

            # 4. 缺失 trace_id（session + error 有，trace 无）
            {"replay_id": "r-no-trace-001", "trace_id": None, "user_session_id": "sess-301",
             "error_id": "err-D", "timestamp": (now - timedelta(minutes=35)).isoformat()},

            # 5. 仅 error_id（trace 和 session 都无）
            {"replay_id": "r-only-err-001", "trace_id": None, "user_session_id": None,
             "error_id": "err-A", "timestamp": (now - timedelta(minutes=40)).isoformat()},

            # 6. 完全空字段
            {"replay_id": "r-empty-fields-001", "trace_id": "", "user_session_id": "",
             "error_id": "", "timestamp": (now - timedelta(minutes=45)).isoformat()},
        ]

        for rec in records_in_window:
            temp_storage.store(
                replay_id=rec["replay_id"],
                data=json.dumps([{"type": 4, "timestamp": 0}]),
                trace_id=rec["trace_id"],
                user_session_id=rec["user_session_id"],
                error_id=rec["error_id"],
                timestamp=rec["timestamp"],
            )

        # ── 超出时间窗口的记录（25 小时前）──
        old_records = [
            {"replay_id": "r-old-001", "trace_id": "trace-old", "user_session_id": "sess-old",
             "error_id": "err-old-A"},
            {"replay_id": "r-old-002", "trace_id": "trace-old-2", "user_session_id": "sess-old-2",
             "error_id": "err-old-B"},
        ]
        old_ts = (now - timedelta(hours=25)).isoformat()
        for rec in old_records:
            temp_storage.store(
                replay_id=rec["replay_id"],
                data=json.dumps([{"type": 4, "timestamp": 0}]),
                trace_id=rec["trace_id"],
                user_session_id=rec["user_session_id"],
                error_id=rec["error_id"],
                timestamp=old_ts,
            )

        return temp_storage

    def test_total_replays_excludes_old(self, complex_storage):
        """总数应排除超出时间窗口的记录"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # 窗口内 9 条，窗口外 2 条
        assert stats["total_replays"] == 9

    def test_with_trace_id_count(self, complex_storage):
        """with_trace_id 应统计所有 trace_id 非空的记录"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # r-full-001/002/003 + r-no-err-001/002 + r-no-sess-001 = 6 条
        assert stats["with_trace_id"] == 6

    def test_with_user_session_id_count(self, complex_storage):
        """with_user_session_id 应统计所有 user_session_id 非空的记录"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # r-full-001/002/003 + r-no-err-001/002 + r-no-trace-001 = 6 条
        assert stats["with_user_session_id"] == 6

    def test_with_error_id_count(self, complex_storage):
        """with_error_id 应统计所有 error_id 非空且非空字符串的记录"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # 源码 SQL: error_id IS NOT NULL AND error_id != ''
        # r-full-001/002/003(3) + r-no-sess-001(1) + r-no-trace-001(1) + r-only-err-001(1) = 6 条
        # 排除: r-no-err-001(None)、r-no-err-002("")、r-empty-fields-001("")
        assert stats["with_error_id"] == 6

    def test_fully_correlated_count(self, complex_storage):
        """fully_correlated 应统计三向齐全的记录"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # 仅 r-full-001/002/003 三条三向齐全
        assert stats["fully_correlated"] == 3

    def test_by_error_id_grouping_and_sorting(self, complex_storage):
        """by_error_id 应按 error_id 分组并按数量倒序排列"""
        stats = complex_storage.get_correlation_stats(hours=24)
        by_error = stats["by_error_id"]

        # err-A 出现 3 次（r-full-001, r-full-003, r-only-err-001）
        # err-B 出现 1 次（r-full-002）
        # err-C 出现 1 次（r-no-sess-001）
        # err-D 出现 1 次（r-no-trace-001）
        # 排除 None/空字符串的 error_id
        assert len(by_error) == 4

        # err-A 应排第一（count=3）
        assert by_error[0]["error_id"] == "err-A"
        assert by_error[0]["count"] == 3

        # 其余 count=1，顺序不限
        error_ids = {item["error_id"] for item in by_error}
        assert error_ids == {"err-A", "err-B", "err-C", "err-D"}

    def test_window_hours_field(self, complex_storage):
        """window_hours 应反映传入参数"""
        stats = complex_storage.get_correlation_stats(hours=12)
        assert stats["window_hours"] == 12

    def test_old_records_excluded_from_stats(self, complex_storage):
        """25 小时前的记录不应出现在统计中"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # err-old-A 和 err-old-B 不应出现
        error_ids = {item["error_id"] for item in stats["by_error_id"]}
        assert "err-old-A" not in error_ids
        assert "err-old-B" not in error_ids

    def test_empty_string_treated_as_missing(self, complex_storage):
        """空字符串应被视为缺失（不计入 with_trace_id 等）"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # r-empty-fields-001 的所有字段为空字符串，不计入任何统计
        # r-no-err-002 的 error_id 为空字符串
        # 验证 with_trace_id 不包含空字符串
        # total=9, with_trace=6, with_session=6, with_error=7, fully=3
        # 不含空字符串的 3 条（r-no-trace-001, r-only-err-001, r-empty-fields-001）
        assert stats["with_trace_id"] + 3 == stats["total_replays"]

    def test_partial_correlation_not_in_fully(self, complex_storage):
        """部分关联的记录不计入 fully_correlated"""
        stats = complex_storage.get_correlation_stats(hours=24)
        # 缺失任一字段的记录不应计入 fully_correlated
        # fully = 3（仅 r-full-001/002/003）
        # with_trace = 6, with_session = 6, with_error = 7
        # fully < min(with_trace, with_session, with_error)
        assert stats["fully_correlated"] < stats["with_trace_id"]
        assert stats["fully_correlated"] < stats["with_error_id"]

    def test_comprehensive_stats_summary(self, complex_storage):
        """综合统计值一次性验证"""
        stats = complex_storage.get_correlation_stats(hours=24)
        assert stats == {
            "total_replays": 9,
            "with_trace_id": 6,
            "with_user_session_id": 6,
            "with_error_id": 6,
            "fully_correlated": 3,
            "by_error_id": [
                {"error_id": "err-A", "count": 3},
                {"error_id": "err-B", "count": 1},
                {"error_id": "err-C", "count": 1},
                {"error_id": "err-D", "count": 1},
            ],
            "window_hours": 24,
        }
