"""ReplayStorage 综合单元测试

覆盖模块: agent/monitoring/replay_storage.py
测试维度: 初始化 / 存储 / 查询 / 列表 / 关联统计 / 清理 / 健康检查 / 全局单例
设计原则: AAA (Arrange-Act-Assert), 隔离文件系统 (tmp_path), 边界显性化
"""

import base64
import datetime
import gzip
import json
import os
import sqlite3
import threading
from unittest import mock

import pytest

from agent.monitoring.replay_storage import (
    REPLAY_ERR_INVALID_INPUT,
    REPLAY_ERR_STORAGE_FAILED,
    REPLAY_ERR_DB_FAILED,
    REPLAY_ERR_NOT_FOUND,
    REPLAY_ERR_DECODE_FAILED,
    ReplayStorageError,
    ReplayStorage,
    SCHEMA_SQL,
    _emit_log,
    _ms,
    get_replay_storage,
    _reset_global_for_test,
    storage_health_check,
)


# ═══════════════════════════════════════════════════════════════
# 常量与错误码测试
# ═══════════════════════════════════════════════════════════════


class TestErrorCodes:
    """错误码常量完整性测试"""

    def test_error_codes_distinct(self):
        codes = [
            REPLAY_ERR_INVALID_INPUT,
            REPLAY_ERR_STORAGE_FAILED,
            REPLAY_ERR_DB_FAILED,
            REPLAY_ERR_NOT_FOUND,
            REPLAY_ERR_DECODE_FAILED,
        ]
        assert len(set(codes)) == len(codes), "错误码必须互不相同"

    def test_error_code_prefix(self):
        for code in [
            REPLAY_ERR_INVALID_INPUT,
            REPLAY_ERR_STORAGE_FAILED,
            REPLAY_ERR_DB_FAILED,
            REPLAY_ERR_NOT_FOUND,
            REPLAY_ERR_DECODE_FAILED,
        ]:
            assert code.startswith("REPLAY_ERR_"), f"错误码缺少前缀: {code}"

    def test_schema_sql_contains_replay_table(self):
        assert "CREATE TABLE IF NOT EXISTS replay" in SCHEMA_SQL
        assert "CREATE INDEX" in SCHEMA_SQL


# ═══════════════════════════════════════════════════════════════
# ReplayStorageError 异常类
# ═══════════════════════════════════════════════════════════════


class TestReplayStorageError:
    """异常类行为测试"""

    def test_error_message_format(self):
        err = ReplayStorageError("REPLAY_ERR_001", "参数无效")
        assert err.code == "REPLAY_ERR_001"
        assert err.message == "参数无效"
        assert "[REPLAY_ERR_001]" in str(err)
        assert "参数无效" in str(err)

    def test_error_is_exception(self):
        err = ReplayStorageError("CODE", "msg")
        assert isinstance(err, Exception)

    def test_raise_and_catch(self):
        with pytest.raises(ReplayStorageError) as exc_info:
            raise ReplayStorageError("REPLAY_ERR_002", "存储失败")
        assert exc_info.value.code == "REPLAY_ERR_002"


# ═══════════════════════════════════════════════════════════════
# 辅助函数测试
# ═══════════════════════════════════════════════════════════════


class TestHelpers:
    """_emit_log / _ms 辅助函数测试"""

    def test_ms_returns_float(self):
        result = _ms(0)
        assert isinstance(result, float)
        assert result >= 0

    def test_emit_log_info_no_exception(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            _emit_log("test_action", "info", "trace123", result="ok")
        # 应至少输出一条日志
        assert any("test_action" in r.message for r in caplog.records)

    def test_emit_log_with_extra_fields(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG):
            _emit_log("test_action", "debug", None, custom_field="value", num=42)
        # None trace_id 应自动生成 uuid
        assert any("test_action" in r.message for r in caplog.records)

    def test_emit_log_invalid_level_fallback(self, caplog):
        # 无效的 log_level 应回退到 info
        _emit_log("action", "invalid_level", "tid")
        assert True  # 不抛异常即可


# ═══════════════════════════════════════════════════════════════
# ReplayStorage 初始化
# ═══════════════════════════════════════════════════════════════


class TestReplayStorageInit:
    """ReplayStorage 初始化测试"""

    def test_init_creates_directory(self, tmp_path):
        storage_root = tmp_path / "replays"
        storage = ReplayStorage(str(storage_root))
        assert storage_root.exists()
        assert storage.storage_root == os.path.abspath(str(storage_root))

    def test_init_creates_db_file(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        assert os.path.exists(storage.db_path)
        assert storage.db_path.endswith("replay_meta.db")

    def test_init_creates_schema(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 查询 replay 表是否存在
        cur = storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='replay'"
        )
        assert cur.fetchone() is not None

    def test_init_creates_indexes(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        cur = storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        names = [row[0] for row in cur.fetchall()]
        assert "idx_replay_trace_id" in names
        assert "idx_replay_user_session_id" in names
        assert "idx_replay_error_id" in names
        assert "idx_replay_timestamp" in names

    def test_init_idempotent(self, tmp_path):
        # 重复初始化相同目录不应报错
        root = str(tmp_path / "replays")
        s1 = ReplayStorage(root)
        s2 = ReplayStorage(root)
        assert s1.db_path == s2.db_path

    def test_init_failure_raises_error(self, tmp_path):
        # 模拟 makedirs 失败
        with mock.patch("os.makedirs", side_effect=OSError("permission denied")):
            with pytest.raises(ReplayStorageError) as exc_info:
                ReplayStorage(str(tmp_path / "forbidden"))
            assert exc_info.value.code == REPLAY_ERR_STORAGE_FAILED


# ═══════════════════════════════════════════════════════════════
# store 方法测试
# ═══════════════════════════════════════════════════════════════


class TestReplayStorageStore:
    """store() 方法测试"""

    def test_store_basic(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        result = storage.store(
            replay_id="replay-001",
            data='{"events": []}',
            trace_id="trace-001",
        )
        assert result["stored"] is True
        assert result["replay_id"] == "replay-001"
        assert result["size_bytes"] > 0
        assert os.path.exists(result["file_path"])

    def test_store_with_all_fields(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        result = storage.store(
            replay_id="replay-full",
            data='{"events": [{"type": "click"}]}',
            trace_id="trace-full",
            user_session_id="session-001",
            error_id="err-001",
            timestamp="2026-01-01T10:00:00",
            duration_sec=60,
            event_count=10,
            compressed=False,
            encoding="json",
        )
        assert result["stored"] is True
        # 验证 DB 记录
        meta = storage.get_by_id("replay-full")
        assert meta["trace_id"] == "trace-full"
        assert meta["user_session_id"] == "session-001"
        assert meta["error_id"] == "err-001"
        assert meta["duration_sec"] == 60
        assert meta["event_count"] == 10

    def test_store_invalid_replay_id_empty(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.store(replay_id="", data="{}")
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_invalid_replay_id_format(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.store(replay_id="!!@#", data="{}")
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_invalid_data_empty(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.store(replay_id="replay-x", data="")
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_invalid_data_type(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.store(replay_id="replay-x", data=12345)
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_invalid_timestamp(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.store(
                replay_id="replay-ts",
                data='{"x":1}',
                timestamp="not-a-timestamp",
            )
        assert exc_info.value.code == REPLAY_ERR_INVALID_INPUT

    def test_store_compressed_gzip_base64(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        raw_data = '{"events": [{"type": "click"}]}' * 100
        gz_bytes = gzip.compress(raw_data.encode("utf-8"))
        b64_data = base64.b64encode(gz_bytes).decode("ascii")
        result = storage.store(
            replay_id="replay-gz",
            data=b64_data,
            compressed=True,
            encoding="gzip-base64",
        )
        assert result["stored"] is True
        # 读取并验证解压
        data_back = storage.get_data_by_id("replay-gz")
        assert data_back == raw_data

    def test_store_compressed_gzip_str(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        raw_data = '{"events": []}'
        result = storage.store(
            replay_id="replay-gz2",
            data=raw_data,
            compressed=True,
            encoding="gzip",
        )
        assert result["stored"] is True
        data_back = storage.get_data_by_id("replay-gz2")
        assert data_back == raw_data

    def test_store_replace_existing(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="rep-1", data='{"v": 1}')
        storage.store(replay_id="rep-1", data='{"v": 2}')
        meta = storage.get_by_id("rep-1")
        data_back = storage.get_data_by_id("rep-1")
        assert '{"v": 2}' in data_back

    def test_store_invalid_base64(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.store(
                replay_id="rep-bad",
                data="!!!not-base64!!!",
                compressed=True,
                encoding="gzip-base64",
            )
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED

    def test_store_db_failure_rolls_back_file(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 用 mock 连接替换真实连接，模拟 DB 写入失败
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("DB locked")
        mock_conn.row_factory = sqlite3.Row
        original_conn = storage._conn
        storage._conn = mock_conn
        try:
            with pytest.raises(ReplayStorageError) as exc_info:
                storage.store(replay_id="rep-rollback", data='{"x":1}')
            assert exc_info.value.code == REPLAY_ERR_DB_FAILED
        finally:
            storage._conn = original_conn
        # 文件应该被回滚删除
        files = list((tmp_path / "replays").rglob("*.json"))
        assert all("rep-rollback" not in f.name for f in files)


# ═══════════════════════════════════════════════════════════════
# 查询方法测试
# ═══════════════════════════════════════════════════════════════


class TestReplayStorageQuery:
    """get_by_id / get_data_by_id 查询测试"""

    def test_get_by_id_found(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(
            replay_id="rep-q1",
            data='{"x":1}',
            trace_id="t1",
            user_session_id="u1",
        )
        meta = storage.get_by_id("rep-q1")
        assert meta is not None
        assert meta["replay_id"] == "rep-q1"
        assert meta["trace_id"] == "t1"

    def test_get_by_id_not_found(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        assert storage.get_by_id("nonexistent") is None

    def test_get_data_by_id_found(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="rep-d1", data='{"data": "hello"}')
        data = storage.get_data_by_id("rep-d1")
        assert data == '{"data": "hello"}'

    def test_get_data_by_id_not_found(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        assert storage.get_data_by_id("nonexistent") is None

    def test_get_data_by_id_file_missing(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="rep-m1", data='{"x":1}')
        # 删除文件
        meta = storage.get_by_id("rep-m1")
        os.remove(meta["file_path"])
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.get_data_by_id("rep-m1")
        assert exc_info.value.code == REPLAY_ERR_STORAGE_FAILED

    def test_get_data_by_id_corrupt_gzip(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 先用有效的 gzip-base64 数据存储
        raw_data = '{"events": []}'
        gz_bytes = gzip.compress(raw_data.encode("utf-8"))
        b64_data = base64.b64encode(gz_bytes).decode("ascii")
        storage.store(
            replay_id="rep-c1",
            data=b64_data,
            compressed=True,
            encoding="gzip-base64",
        )
        # 覆盖文件为无效 gzip 数据
        meta = storage.get_by_id("rep-c1")
        with open(meta["file_path"], "wb") as f:
            f.write(b"not-a-valid-gzip")
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.get_data_by_id("rep-c1")
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED

    def test_get_by_id_db_failure(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 替换为失败的 mock 连接（sqlite3.Connection.execute 是只读的）
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("DB error")
        original = storage._conn
        storage._conn = mock_conn
        try:
            with pytest.raises(ReplayStorageError) as exc_info:
                storage.get_by_id("any")
            assert exc_info.value.code == REPLAY_ERR_DB_FAILED
        finally:
            storage._conn = original


# ═══════════════════════════════════════════════════════════════
# 列表查询测试
# ═══════════════════════════════════════════════════════════════


class TestReplayStorageList:
    """list_by_* 方法测试"""

    def _setup_data(self, storage):
        """准备测试数据"""
        storage.store(replay_id="rep-001", data="{}", trace_id="t1", user_session_id="u1",
                      error_id="e1", timestamp="2026-01-01T10:00:00")
        storage.store(replay_id="rep-002", data="{}", trace_id="t1", user_session_id="u2",
                      timestamp="2026-01-02T10:00:00")
        storage.store(replay_id="rep-003", data="{}", trace_id="t2", user_session_id="u1",
                      error_id="e2", timestamp="2026-01-03T10:00:00")

    def test_list_by_trace_id(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        self._setup_data(storage)
        results = storage.list_by_trace_id("t1")
        assert len(results) == 2
        assert all(r["trace_id"] == "t1" for r in results)

    def test_list_by_trace_id_empty(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        self._setup_data(storage)
        assert storage.list_by_trace_id("nonexistent") == []

    def test_list_by_user_session(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        self._setup_data(storage)
        results = storage.list_by_user_session("u1")
        assert len(results) == 2

    def test_list_by_time_range(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        self._setup_data(storage)
        results = storage.list_by_time_range(
            "2026-01-02T00:00:00", "2026-01-03T23:59:59"
        )
        assert len(results) == 2

    def test_list_by_time_range_empty(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        self._setup_data(storage)
        results = storage.list_by_time_range(
            "2025-01-01T00:00:00", "2025-12-31T23:59:59"
        )
        assert results == []

    def test_list_recent_24h(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 插入一条当前时间的记录
        storage.store(replay_id="r-now", data="{}", trace_id="t-now")
        results = storage.list_recent_24h()
        assert len(results) >= 1
        assert any(r["replay_id"] == "r-now" for r in results)

    def test_list_by_field_db_failure(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("DB error")
        original = storage._conn
        storage._conn = mock_conn
        try:
            with pytest.raises(ReplayStorageError):
                storage.list_by_trace_id("t1")
        finally:
            storage._conn = original


# ═══════════════════════════════════════════════════════════════
# 关联统计测试
# ═══════════════════════════════════════════════════════════════


class TestCorrelationStats:
    """get_correlation_stats 测试"""

    def test_correlation_stats_empty(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        stats = storage.get_correlation_stats()
        assert stats["total_replays"] == 0
        assert stats["with_trace_id"] == 0
        assert stats["fully_correlated"] == 0
        assert stats["window_hours"] == 24

    def test_correlation_stats_with_data(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="rep-001", data="{}", trace_id="t1",
                      user_session_id="u1", error_id="e1")
        storage.store(replay_id="rep-002", data="{}", trace_id="t2")
        stats = storage.get_correlation_stats()
        assert stats["total_replays"] == 2
        assert stats["with_trace_id"] == 2
        assert stats["with_user_session_id"] == 1
        assert stats["with_error_id"] == 1
        assert stats["fully_correlated"] == 1

    def test_correlation_stats_by_error_id(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="rep-001", data="{}", error_id="e1")
        storage.store(replay_id="rep-002", data="{}", error_id="e1")
        storage.store(replay_id="rep-003", data="{}", error_id="e2")
        stats = storage.get_correlation_stats()
        by_error = {e["error_id"]: e["count"] for e in stats["by_error_id"]}
        assert by_error["e1"] == 2
        assert by_error["e2"] == 1

    def test_correlation_stats_custom_window(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        stats = storage.get_correlation_stats(hours=48)
        assert stats["window_hours"] == 48

    def test_correlation_stats_db_failure(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("DB error")
        original = storage._conn
        storage._conn = mock_conn
        try:
            with pytest.raises(ReplayStorageError):
                storage.get_correlation_stats()
        finally:
            storage._conn = original


# ═══════════════════════════════════════════════════════════════
# 清理测试
# ═══════════════════════════════════════════════════════════════


class TestCleanupOldRecords:
    """cleanup_old_records 测试"""

    def test_cleanup_removes_old_records(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 插入老记录
        storage.store(replay_id="r-old", data="{}",
                      timestamp="2020-01-01T00:00:00")
        # 插入新记录
        storage.store(replay_id="r-new", data="{}")
        deleted = storage.cleanup_old_records(days=365)
        assert deleted == 1
        # 老记录应不存在
        assert storage.get_by_id("r-old") is None
        # 新记录应保留
        assert storage.get_by_id("r-new") is not None

    def test_cleanup_removes_files(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="r-old", data="{}",
                      timestamp="2020-01-01T00:00:00")
        meta = storage.get_by_id("r-old")
        file_path = meta["file_path"]
        assert os.path.exists(file_path)
        storage.cleanup_old_records(days=365)
        assert not os.path.exists(file_path)

    def test_cleanup_invalid_days_negative(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ValueError):
            storage.cleanup_old_records(days=-1)

    def test_cleanup_invalid_days_too_large(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        with pytest.raises(ValueError):
            storage.cleanup_old_records(days=999999)

    def test_cleanup_zero_days_removes_all(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 插入老记录（一年前）
        old_ts = (datetime.datetime.now() - datetime.timedelta(days=365)).isoformat()
        storage.store(replay_id="r-old", data="{}", timestamp=old_ts)
        deleted = storage.cleanup_old_records(days=0)
        assert deleted == 1

    def test_cleanup_db_failure(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("DB error")
        original = storage._conn
        storage._conn = mock_conn
        try:
            with pytest.raises(ReplayStorageError):
                storage.cleanup_old_records(days=30)
        finally:
            storage._conn = original


# ═══════════════════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════════════════


class TestStorageHealthCheck:
    """storage_health_check 测试"""

    def test_health_check_not_initialized(self):
        _reset_global_for_test()
        result = storage_health_check()
        assert result["db_path_exists"] is False
        assert result["db_writable"] is False

    def test_health_check_initialized(self, tmp_path):
        _reset_global_for_test()
        storage = get_replay_storage(str(tmp_path / "replays"))
        result = storage_health_check()
        assert result["db_path_exists"] is True
        assert result["db_writable"] is True
        _reset_global_for_test()

    def test_health_check_db_closed(self, tmp_path):
        _reset_global_for_test()
        storage = get_replay_storage(str(tmp_path / "replays"))
        storage._conn.close()
        result = storage_health_check()
        assert result["db_writable"] is False
        _reset_global_for_test()


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════


class TestGlobalSingleton:
    """get_replay_storage / _reset_global_for_test 测试"""

    def setup_method(self, method):
        _reset_global_for_test()

    def teardown_method(self, method):
        _reset_global_for_test()

    def test_get_singleton_returns_same_instance(self, tmp_path):
        s1 = get_replay_storage(str(tmp_path / "r1"))
        s2 = get_replay_storage(str(tmp_path / "r2"))
        assert s1 is s2

    def test_reset_global(self, tmp_path):
        s1 = get_replay_storage(str(tmp_path / "r1"))
        _reset_global_for_test()
        s2 = get_replay_storage(str(tmp_path / "r2"))
        assert s1 is not s2

    def test_get_singleton_uses_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPLAY_STORAGE_ROOT", str(tmp_path / "env_replays"))
        s = get_replay_storage()
        assert "env_replays" in s.storage_root
        _reset_global_for_test()


# ═══════════════════════════════════════════════════════════════
# close 方法
# ═══════════════════════════════════════════════════════════════


class TestClose:
    """close() 测试"""

    def test_close_no_exception(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.close()
        # 重复 close 不应抛异常
        storage.close()

    def test_close_makes_db_unavailable(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.close()
        with pytest.raises(sqlite3.Error):
            storage._conn.execute("SELECT 1")


# ═══════════════════════════════════════════════════════════════
# 线程安全
# ═══════════════════════════════════════════════════════════════


class TestThreadSafety:
    """线程安全测试"""

    def test_concurrent_store(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        errors = []

        def worker(idx):
            try:
                storage.store(
                    replay_id=f"concurrent-{idx}",
                    data='{"idx": %d}' % idx,
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        # 验证所有记录都已写入
        for i in range(10):
            assert storage.get_by_id(f"concurrent-{i}") is not None

    def test_concurrent_read_write(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        storage.store(replay_id="r-init", data="{}")
        errors = []

        def reader():
            try:
                for _ in range(20):
                    storage.list_recent_24h()
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(20):
                    storage.store(replay_id=f"r-{i}", data="{}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════
# _record_metrics 埋点
# ═══════════════════════════════════════════════════════════════


class TestRecordMetrics:
    """埋点预留方法测试"""

    def test_record_metrics_no_exception(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        # 不应抛异常
        storage._record_metrics(True, 100.5)
        storage._record_metrics(False, 0.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
