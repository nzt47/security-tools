"""ReplayStorage 集成测试

覆盖用户行为回放存储的双存储（gzip 文件 + SQLite 元数据）功能，
包括存储/查询/列表/统计/清理全流程，以及参数校验和错误处理。

使用 tmp_path 隔离存储目录，避免测试间状态污染。
"""

import gzip
import base64
import datetime
import json
import os
import pytest

from agent.monitoring.replay_storage import (
    ReplayStorage,
    ReplayStorageError,
    REPLAY_ERR_INVALID_INPUT,
    REPLAY_ERR_STORAGE_FAILED,
    REPLAY_ERR_DB_FAILED,
    REPLAY_ERR_DECODE_FAILED,
    get_replay_storage,
    _reset_global_for_test,
    storage_health_check,
)


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def storage(tmp_path):
    """每个测试使用独立的临时存储目录"""
    return ReplayStorage(str(tmp_path / "replays"))


def _make_gzip_base64(data: str) -> str:
    """构造 gzip-base64 编码数据"""
    gz_bytes = gzip.compress(data.encode("utf-8"))
    return base64.b64encode(gz_bytes).decode("ascii")


# ═══════════════════════════════════════════════════════════════
#  初始化
# ═══════════════════════════════════════════════════════════════

class TestInitialization:
    """存储初始化"""

    def test_init_creates_directory(self, tmp_path):
        root = str(tmp_path / "new_replays")
        s = ReplayStorage(root)
        assert os.path.exists(root)
        assert os.path.exists(os.path.join(root, "replay_meta.db"))
        s.close()

    def test_init_creates_schema(self, storage):
        # 验证表已创建
        result = storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='replay'"
        ).fetchone()
        assert result is not None
        storage.close()

    def test_close(self, storage):
        storage.close()
        # 再次 close 不报错
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  存储功能
# ═══════════════════════════════════════════════════════════════

class TestStore:
    """回放存储功能"""

    def test_store_json_success(self, storage):
        result = storage.store(
            replay_id="replay-001",
            data='{"events": []}',
            trace_id="trace-001",
        )
        assert result["stored"] is True
        assert result["replay_id"] == "replay-001"
        assert result["size_bytes"] > 0
        storage.close()

    def test_store_with_all_params(self, storage):
        result = storage.store(
            replay_id="replay-002",
            data='{"events": [{"type": "click"}]}',
            trace_id="trace-002",
            user_session_id="session-002",
            error_id="error-002",
            duration_sec=30,
            event_count=5,
        )
        assert result["stored"] is True
        storage.close()

    def test_store_compressed_gzip_base64(self, storage):
        raw_data = '{"events": [{"type": "click"}, {"type": "scroll"}]}'
        encoded = _make_gzip_base64(raw_data)
        result = storage.store(
            replay_id="replay-gz1",
            data=encoded,
            compressed=True,
            encoding="gzip-base64",
        )
        assert result["stored"] is True
        storage.close()

    def test_store_compressed_gzip(self, storage):
        raw_data = '{"events": [{"type": "click"}]}'
        result = storage.store(
            replay_id="replay-gz2",
            data=raw_data,
            compressed=True,
        )
        assert result["stored"] is True
        storage.close()

    def test_store_invalid_replay_id_empty(self, storage):
        with pytest.raises(ReplayStorageError, match="replay_id 无效"):
            storage.store(replay_id="", data="{}")
        storage.close()

    def test_store_invalid_replay_id_short(self, storage):
        with pytest.raises(ReplayStorageError, match="replay_id 无效"):
            storage.store(replay_id="ab", data="{}")
        storage.close()

    def test_store_invalid_data_empty(self, storage):
        with pytest.raises(ReplayStorageError, match="data 为空"):
            storage.store(replay_id="replay-003", data="")
        storage.close()

    def test_store_invalid_data_type(self, storage):
        with pytest.raises(ReplayStorageError, match="data 为空"):
            storage.store(replay_id="replay-003", data=12345)
        storage.close()

    def test_store_invalid_timestamp(self, storage):
        with pytest.raises(ReplayStorageError, match="timestamp 格式无效"):
            storage.store(
                replay_id="replay-004",
                data="{}",
                timestamp="not-a-date",
            )
        storage.close()

    def test_store_invalid_base64(self, storage):
        with pytest.raises(ReplayStorageError, match="base64 解码失败"):
            storage.store(
                replay_id="replay-005",
                data="!!!invalid base64!!!",
                compressed=True,
                encoding="gzip-base64",
            )
        storage.close()

    def test_store_custom_timestamp(self, storage):
        ts = "2026-01-15T10:30:00"
        result = storage.store(
            replay_id="replay-006",
            data="{}",
            timestamp=ts,
        )
        assert result["stored"] is True
        meta = storage.get_by_id("replay-006")
        assert meta["timestamp"] == ts
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  查询功能
# ═══════════════════════════════════════════════════════════════

class TestQuery:
    """回放查询功能"""

    def test_get_by_id_found(self, storage):
        storage.store(
            replay_id="replay-q1",
            data='{"events": []}',
            trace_id="trace-q1",
            user_session_id="session-q1",
        )
        meta = storage.get_by_id("replay-q1")
        assert meta is not None
        assert meta["replay_id"] == "replay-q1"
        assert meta["trace_id"] == "trace-q1"
        storage.close()

    def test_get_by_id_not_found(self, storage):
        meta = storage.get_by_id("nonexistent")
        assert meta is None
        storage.close()

    def test_get_data_by_id_json(self, storage):
        raw = '{"events": [{"type": "click"}]}'
        storage.store(replay_id="replay-d1", data=raw)
        data = storage.get_data_by_id("replay-d1")
        assert data == raw
        storage.close()

    def test_get_data_by_id_compressed_gzip_base64(self, storage):
        raw = '{"events": [{"type": "click"}, {"type": "scroll"}]}'
        encoded = _make_gzip_base64(raw)
        storage.store(
            replay_id="replay-d2",
            data=encoded,
            compressed=True,
            encoding="gzip-base64",
        )
        data = storage.get_data_by_id("replay-d2")
        assert data == raw
        storage.close()

    def test_get_data_by_id_compressed_gzip(self, storage):
        raw = '{"events": [{"type": "click"}]}'
        storage.store(
            replay_id="replay-d3",
            data=raw,
            compressed=True,
        )
        data = storage.get_data_by_id("replay-d3")
        assert data == raw
        storage.close()

    def test_get_data_by_id_not_found(self, storage):
        data = storage.get_data_by_id("nonexistent")
        assert data is None
        storage.close()

    def test_get_data_by_id_file_missing(self, storage):
        storage.store(replay_id="replay-d4", data='{"events": []}')
        # 删除文件但保留 DB 记录
        meta = storage.get_by_id("replay-d4")
        os.remove(meta["file_path"])
        with pytest.raises(ReplayStorageError, match="回放文件不存在"):
            storage.get_data_by_id("replay-d4")
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  列表查询
# ═══════════════════════════════════════════════════════════════

class TestListQueries:
    """列表查询功能"""

    def _seed_multiple(self, storage):
        """填充多条测试数据"""
        ts_base = "2026-07-10T10:00:00"
        for i in range(5):
            storage.store(
                replay_id=f"replay-l{i}",
                data='{"events": []}',
                trace_id=f"trace-{i % 2}",
                user_session_id=f"session-{i % 3}",
                error_id=f"error-{i}" if i % 2 == 0 else None,
                timestamp=ts_base,
            )

    def test_list_by_trace_id(self, storage):
        self._seed_multiple(storage)
        results = storage.list_by_trace_id("trace-0")
        assert len(results) == 3  # i=0,2,4
        storage.close()

    def test_list_by_user_session(self, storage):
        self._seed_multiple(storage)
        results = storage.list_by_user_session("session-0")
        assert len(results) >= 1
        storage.close()

    def test_list_by_time_range(self, storage):
        self._seed_multiple(storage)
        results = storage.list_by_time_range(
            "2026-07-10T00:00:00",
            "2026-07-10T23:59:59",
        )
        assert len(results) == 5
        storage.close()

    def test_list_by_time_range_empty(self, storage):
        results = storage.list_by_time_range(
            "2020-01-01T00:00:00",
            "2020-12-31T23:59:59",
        )
        assert len(results) == 0
        storage.close()

    def test_list_recent_24h(self, storage):
        # 存储当前时间的数据
        storage.store(replay_id="replay-r1", data='{"events": []}')
        results = storage.list_recent_24h()
        assert len(results) >= 1
        storage.close()

    def test_list_recent_24h_empty(self, storage):
        results = storage.list_recent_24h()
        assert len(results) == 0
        storage.close()

    def test_list_with_limit(self, storage):
        self._seed_multiple(storage)
        results = storage.list_by_trace_id("trace-0", limit=2)
        assert len(results) <= 2
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  统计
# ═══════════════════════════════════════════════════════════════

class TestCorrelationStats:
    """三向关联统计"""

    def test_stats_empty(self, storage):
        stats = storage.get_correlation_stats()
        assert stats["total_replays"] == 0
        assert stats["with_trace_id"] == 0
        assert stats["fully_correlated"] == 0
        storage.close()

    def test_stats_with_data(self, storage):
        storage.store(
            replay_id="replay-s1",
            data='{"events": []}',
            trace_id="t1",
            user_session_id="u1",
            error_id="e1",
        )
        storage.store(
            replay_id="replay-s2",
            data='{"events": []}',
            trace_id="t2",
        )
        stats = storage.get_correlation_stats()
        assert stats["total_replays"] == 2
        assert stats["with_trace_id"] == 2
        assert stats["with_user_session_id"] == 1
        assert stats["with_error_id"] == 1
        assert stats["fully_correlated"] == 1
        storage.close()

    def test_stats_by_error_id(self, storage):
        storage.store(
            replay_id="replay-e1",
            data='{"events": []}',
            error_id="err-001",
        )
        storage.store(
            replay_id="replay-e2",
            data='{"events": []}',
            error_id="err-001",
        )
        stats = storage.get_correlation_stats()
        assert len(stats["by_error_id"]) >= 1
        assert stats["by_error_id"][0]["error_id"] == "err-001"
        assert stats["by_error_id"][0]["count"] == 2
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  清理
# ═══════════════════════════════════════════════════════════════

class TestCleanup:
    """旧记录清理"""

    def test_cleanup_old_records(self, storage):
        # 存储一条旧数据（40天前）
        old_ts = (datetime.datetime.now() - datetime.timedelta(days=40)).isoformat()
        storage.store(
            replay_id="replay-old",
            data='{"events": []}',
            timestamp=old_ts,
        )
        # 存储一条新数据
        storage.store(replay_id="replay-new", data='{"events": []}')

        deleted = storage.cleanup_old_records(days=30)
        assert deleted == 1
        assert storage.get_by_id("replay-old") is None
        assert storage.get_by_id("replay-new") is not None
        storage.close()

    def test_cleanup_no_old_records(self, storage):
        storage.store(replay_id="replay-c1", data='{"events": []}')
        deleted = storage.cleanup_old_records(days=30)
        assert deleted == 0
        storage.close()

    def test_cleanup_invalid_days_negative(self, storage):
        with pytest.raises(ValueError, match="非负整数"):
            storage.cleanup_old_records(days=-1)
        storage.close()

    def test_cleanup_invalid_days_too_large(self, storage):
        with pytest.raises(ValueError, match="超过上限"):
            storage.cleanup_old_records(days=99999)
        storage.close()

    def test_cleanup_zero_days(self, storage):
        """days=0 清理所有记录"""
        storage.store(replay_id="replay-c2", data='{"events": []}')
        deleted = storage.cleanup_old_records(days=0)
        assert deleted >= 1
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  辅助方法
# ═══════════════════════════════════════════════════════════════

class TestHelperMethods:
    """内部辅助方法"""

    def test_date_dir_with_valid_timestamp(self, storage):
        path = storage._date_dir("2026-07-10T10:00:00")
        assert "20260710" in path
        assert os.path.exists(path)
        storage.close()

    def test_date_dir_with_invalid_timestamp(self, storage):
        path = storage._date_dir("not-a-date")
        # 无效时间戳使用当前日期
        assert os.path.exists(path)
        storage.close()

    def test_date_dir_none(self, storage):
        path = storage._date_dir(None)
        assert os.path.exists(path)
        storage.close()

    def test_file_path_for_uncompressed(self, storage):
        path = storage._file_path_for("replay-001", None, False)
        assert path.endswith("replay-001.json")
        storage.close()

    def test_file_path_for_compressed(self, storage):
        path = storage._file_path_for("replay-001", None, True)
        assert path.endswith("replay-001.json.gz")
        storage.close()

    def test_file_path_sanitizes_id(self, storage):
        path = storage._file_path_for("replay/../bad", None, False)
        # 特殊字符被替换为 _
        assert ".." not in path
        storage.close()


# ═══════════════════════════════════════════════════════════════
#  全局单例与健康检查
# ═══════════════════════════════════════════════════════════════

class TestGlobalAndHealth:
    """全局单例与健康检查"""

    def test_get_replay_storage_singleton(self, tmp_path):
        _reset_global_for_test()
        s1 = get_replay_storage(str(tmp_path / "global_replays"))
        s2 = get_replay_storage()
        assert s1 is s2
        s1.close()
        _reset_global_for_test()

    def test_reset_global_for_test(self, tmp_path):
        _reset_global_for_test()
        s = get_replay_storage(str(tmp_path / "reset_replays"))
        _reset_global_for_test()
        # 重置后获取新实例
        s2 = get_replay_storage(str(tmp_path / "reset_replays2"))
        assert s is not s2
        s2.close()
        _reset_global_for_test()

    def test_storage_health_check(self, tmp_path):
        _reset_global_for_test()
        get_replay_storage(str(tmp_path / "health_replays"))
        health = storage_health_check()
        assert "storage_root" in health
        _reset_global_for_test()
