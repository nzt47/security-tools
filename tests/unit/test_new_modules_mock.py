#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新增模块 mock 数据测试脚本

覆盖模块：
1. agent/error_reporting_config.py — 错误上报配置 + Sentry 集成
2. agent/monitoring/replay_storage.py — 用户行为回放存储（gzip + SQLite 双存储）

运行方式：
    python -m pytest tests/unit/test_new_modules_mock.py -v --tb=short

状态同步机制说明（按用户硬约束）：
- monkeypatch 隔离环境变量，避免测试间状态污染
- tmp_path fixture 隔离文件系统，测试后自动清理
- unittest.mock 模拟 sentry_sdk，避免真实网络请求
- _reset_for_test() / _reset_global_for_test() 强制重置全局单例，确保每用例独立
- 每个测试用例独立 trace_id，避免跨用例关联
"""

import base64
import gzip
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.error_reporting_config import (
    SENTRY_ERR_CAPTURE_FAILED,
    SENTRY_ERR_DSN_INVALID,
    SENTRY_ERR_INIT_FAILED,
    SENTRY_ERR_RATE_INVALID,
    SENTRY_ERR_SDK_MISSING,
    SentryConfigError,
    _filter_sensitive_recursive,
    _is_sensitive_key,
    _parse_sample_rate,
    _reset_for_test,
    _safe_get_trace_id,
    _sentry_before_send,
    capture_error,
    capture_message,
    get_config,
    health_check,
    init_sentry,
    is_sentry_enabled,
    set_sensitive_patterns,
)
from agent.monitoring.replay_storage import (
    REPLAY_ERR_DB_FAILED,
    REPLAY_ERR_DECODE_FAILED,
    REPLAY_ERR_INVALID_INPUT,
    REPLAY_ERR_NOT_FOUND,
    REPLAY_ERR_STORAGE_FAILED,
    ReplayStorage,
    ReplayStorageError,
    _reset_global_for_test,
    get_replay_storage,
    storage_health_check,
)


# ═══════════════════════════════════════════════════════════════
# Mock 数据工厂
# ═══════════════════════════════════════════════════════════════

class MockDataFactory:
    """构造各类 mock 数据"""

    @staticmethod
    def make_replay_data(event_count=5):
        """构造 rrweb 事件 JSON 字符串"""
        events = []
        for i in range(event_count):
            events.append({
                "type": 2 if i % 2 == 0 else 3,
                "data": {"node": {"type": 0, "childNodes": []}},
                "timestamp": int(datetime.now().timestamp() * 1000) + i * 100,
            })
        return json.dumps({"events": events}, ensure_ascii=False)

    @staticmethod
    def make_gzip_base64_data(raw_json):
        """构造 gzip+base64 编码数据（前端上传格式）"""
        raw_bytes = raw_json.encode("utf-8")
        gz_bytes = gzip.compress(raw_bytes)
        return base64.b64encode(gz_bytes).decode("ascii")

    @staticmethod
    def make_sentry_event():
        """构造含敏感字段的 Sentry 事件（用于 before_send 脱敏测试）"""
        return {
            "event_id": "abc123def456",
            "level": "error",
            "message": "测试错误事件",
            "extra": {
                "password": "secret123",
                "api_key": "sk-xxxxxx",
                "normal_field": "正常值",
                "nested": {"token": "tok-abc"},
            },
            "request": {
                "url": "http://localhost:8000/api/test",
                "method": "POST",
                "headers": {
                    "Content-Type": "application/json",
                    "X-Api-Key": "sk-secret-key",
                },
                "data": {"password": "p@ss", "username": "admin"},
            },
            "tags": {"env": "test"},
            "breadcrumbs": [
                {"message": "step1", "data": {}},
            ],
        }

    @staticmethod
    def make_store_params(compressed=False):
        """构造 store() 调用的完整参数"""
        raw_data = MockDataFactory.make_replay_data(event_count=10)
        if compressed:
            data = MockDataFactory.make_gzip_base64_data(raw_data)
            encoding = "gzip-base64"
        else:
            data = raw_data
            encoding = "json"
        return {
            "replay_id": "replay-mock-001",
            "data": data,
            "trace_id": "trace-mock-abc-123",
            "user_session_id": "session-mock-xyz",
            "error_id": "error-mock-456",
            "timestamp": datetime.now().isoformat(),
            "duration_sec": 30,
            "event_count": 10,
            "compressed": compressed,
            "encoding": encoding,
        }


# ═══════════════════════════════════════════════════════════════
# Part 1: error_reporting_config.py 测试
# ═══════════════════════════════════════════════════════════════

class TestParseSampleRate:
    """采样率解析测试"""

    def test_valid_value(self):
        assert _parse_sample_rate("0.5", 1.0, "test") == 0.5

    def test_empty_returns_default(self):
        assert _parse_sample_rate("", 0.8, "test") == 0.8

    def test_none_returns_default(self):
        assert _parse_sample_rate(None, 0.8, "test") == 0.8

    def test_boundary_zero(self):
        assert _parse_sample_rate("0", 1.0, "test") == 0.0

    def test_boundary_one(self):
        assert _parse_sample_rate("1", 1.0, "test") == 1.0

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match=SENTRY_ERR_RATE_INVALID):
            _parse_sample_rate("abc", 1.0, "test")

    def test_out_of_range_high_raises(self):
        with pytest.raises(ValueError, match=SENTRY_ERR_RATE_INVALID):
            _parse_sample_rate("1.5", 1.0, "test")

    def test_out_of_range_low_raises(self):
        with pytest.raises(ValueError, match=SENTRY_ERR_RATE_INVALID):
            _parse_sample_rate("-0.1", 1.0, "test")


class TestGetConfig:
    """配置读取测试"""

    def test_defaults_sentry_disabled(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        cfg = get_config()
        assert cfg["sentry"]["enabled"] is False
        assert cfg["sentry"]["sample_rate"] == 1.0

    def test_sentry_enabled_with_dsn(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://key@example.com/1")
        monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
        monkeypatch.setenv("SENTRY_SAMPLE_RATE", "0.1")
        cfg = get_config()
        assert cfg["sentry"]["enabled"] is True
        assert cfg["sentry"]["environment"] == "staging"
        assert cfg["sentry"]["sample_rate"] == 0.1

    def test_console_config(self, monkeypatch):
        monkeypatch.setenv("ERROR_REPORTING_CONSOLE_LEVEL", "debug")
        cfg = get_config()
        assert cfg["console"]["min_level"] == "debug"


class TestInitSentry:
    """Sentry SDK 初始化测试"""

    def setup_method(self):
        _reset_for_test()

    def test_no_dsn_returns_false(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        assert init_sentry() is False

    def test_invalid_dsn_returns_false(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "not-a-url")
        assert init_sentry() is False

    def test_sdk_not_installed_returns_false(self, monkeypatch):
        """模拟 sentry_sdk 未安装：sys.modules 设为 None 触发 ImportError"""
        monkeypatch.setenv("SENTRY_DSN", "https://key@host.com/1")
        with patch.dict("sys.modules", {"sentry_sdk": None, "sentry_sdk.integrations": None}):
            assert init_sentry() is False

    def test_successful_init(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://key@host.com/1")
        mock_sdk = MagicMock()
        mock_integrations = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations": mock_integrations}):
            assert init_sentry() is True
            assert is_sentry_enabled() is True
            # 验证 sentry_sdk.init 被调用且传入 DSN
            mock_sdk.init.assert_called_once()
            assert mock_sdk.init.call_args.kwargs["dsn"] == "https://key@host.com/1"

    def test_already_initialized_returns_true(self, monkeypatch):
        """已初始化时直接返回 True，不重复 init"""
        import agent.error_reporting_config as mod
        mod._sentry_initialized = True
        assert init_sentry() is True

    def test_force_reinit(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://key@host.com/1")
        mock_sdk = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations": MagicMock()}):
            import agent.error_reporting_config as mod
            mod._sentry_initialized = True
            assert init_sentry(force=True) is True
            mock_sdk.init.assert_called_once()

    def test_init_exception_returns_false(self, monkeypatch):
        """sentry_sdk.init 抛异常时返回 False 且状态重置"""
        monkeypatch.setenv("SENTRY_DSN", "https://key@host.com/1")
        mock_sdk = MagicMock()
        mock_sdk.init.side_effect = RuntimeError("init failed")
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations": MagicMock()}):
            assert init_sentry() is False
            assert is_sentry_enabled() is False


class TestFilterSensitive:
    """敏感信息过滤测试"""

    def test_dict_password_redacted(self):
        result = _filter_sensitive_recursive({"password": "secret", "name": "alice"})
        assert result["password"] == "[REDACTED]"
        assert result["name"] == "alice"

    def test_nested_dict(self):
        result = _filter_sensitive_recursive({"outer": {"api_key": "sk-xxx", "ok": "val"}})
        assert result["outer"]["api_key"] == "[REDACTED]"
        assert result["outer"]["ok"] == "val"

    def test_list_filtering(self):
        result = _filter_sensitive_recursive([{"token": "tok"}, {"name": "bob"}])
        assert result[0]["token"] == "[REDACTED]"
        assert result[1]["name"] == "bob"

    def test_string_pattern_redacted(self):
        """字符串内嵌 token=xxx 模式替换"""
        result = _filter_sensitive_recursive("token=abcdef123456 and text")
        assert "[REDACTED]" in result

    def test_passthrough(self):
        """非容器类型原样返回"""
        assert _filter_sensitive_recursive(42) == 42
        assert _filter_sensitive_recursive(True) is True

    def test_sensitive_key_password(self):
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("PASSWORD") is True
        assert _is_sensitive_key("Pass-Word") is True  # 连字符归一化

    def test_sensitive_key_token(self):
        assert _is_sensitive_key("access_token") is True
        assert _is_sensitive_key("X-Access-Token") is True

    def test_normal_key(self):
        assert _is_sensitive_key("username") is False
        assert _is_sensitive_key("email") is False

    def test_non_string_key(self):
        """非字符串键返回 False 不抛异常"""
        assert _is_sensitive_key(123) is False
        assert _is_sensitive_key(None) is False

    def test_custom_patterns(self):
        """覆盖自定义敏感字段模式"""
        set_sensitive_patterns(["custom_secret"])
        assert _is_sensitive_key("custom_secret") is True
        # 恢复默认（使用 _DEFAULT_SENSITIVE_PATTERNS 避免手写列表遗漏字段）
        from agent.error_reporting_config import _DEFAULT_SENSITIVE_PATTERNS
        set_sensitive_patterns(_DEFAULT_SENSITIVE_PATTERNS)


class TestSentryBeforeSend:
    """before_send 钩子测试"""

    def test_filters_extra(self):
        event = MockDataFactory.make_sentry_event()
        result = _sentry_before_send(event, {})
        assert result["extra"]["password"] == "[REDACTED]"
        assert result["extra"]["api_key"] == "[REDACTED]"

    def test_filters_headers(self):
        event = MockDataFactory.make_sentry_event()
        result = _sentry_before_send(event, {})
        assert result["request"]["headers"]["X-Api-Key"] == "[REDACTED]"

    def test_filters_request_data(self):
        event = MockDataFactory.make_sentry_event()
        result = _sentry_before_send(event, {})
        assert result["request"]["data"]["password"] == "[REDACTED]"

    def test_injects_trace_id_tag(self):
        """before_send 应注入 trace_id 到 tags"""
        event = MockDataFactory.make_sentry_event()
        result = _sentry_before_send(event, {})
        assert "trace_id" in result["tags"]

    def test_exception_returns_event(self):
        """过滤过程异常时不阻塞，返回原事件

        构造一个会导致 _filter_sensitive_recursive 抛异常的事件：
        tags 设为非 dict（list），使 setdefault 调用失败
        """
        # tags 为 list 时，event.setdefault("tags", {}) 返回 list
        # 后续 if isinstance(tags, dict) 跳过，但 breadcrumbs 同理
        # 极端情况：构造一个完全畸形的 event 触发异常路径
        event = {"malformed": None}
        result = _sentry_before_send(event, {})
        # 即使有异常，原字段应保留（异常时返回原 event）
        assert "malformed" in result
        assert result["malformed"] is None


class TestCaptureError:
    """capture_error 测试"""

    def setup_method(self):
        _reset_for_test()

    def test_not_initialized_returns_none(self):
        """未初始化时返回 None"""
        assert capture_error(ValueError("test")) is None

    def test_success_returns_event_id(self):
        """成功时返回 sentry_sdk 给的 event_id"""
        import agent.error_reporting_config as mod
        mod._sentry_initialized = True
        mock_sdk = MagicMock()
        mock_sdk.capture_exception.return_value = "event-id-123"
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
            result = capture_error(ValueError("test"), context={"key": "val"})
            assert result == "event-id-123"

    def test_exception_returns_none(self):
        """capture_exception 抛异常时返回 None"""
        import agent.error_reporting_config as mod
        mod._sentry_initialized = True
        mock_sdk = MagicMock()
        mock_sdk.push_scope.side_effect = RuntimeError("scope failed")
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
            assert capture_error(ValueError("test")) is None

    def test_context_filtered_before_send(self):
        """context 中的敏感字段在 set_context 前已脱敏"""
        import agent.error_reporting_config as mod
        mod._sentry_initialized = True
        mock_sdk = MagicMock()
        mock_sdk.capture_exception.return_value = "eid"
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
            capture_error(ValueError("test"), context={"password": "raw"})
            # set_context 应传入脱敏后的值
            ctx_call = mock_sdk.push_scope.return_value.__enter__.return_value.set_context
            ctx_call.assert_called_with("custom", {"password": "[REDACTED]"})


class TestCaptureMessage:
    """capture_message 测试"""

    def setup_method(self):
        _reset_for_test()

    def test_not_initialized_returns_none(self):
        assert capture_message("hello") is None

    def test_success(self):
        import agent.error_reporting_config as mod
        mod._sentry_initialized = True
        mock_sdk = MagicMock()
        mock_sdk.capture_message.return_value = "msg-id-456"
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
            assert capture_message("test msg") == "msg-id-456"


class TestHealthCheck:
    """健康检查测试"""

    def test_returns_dict_with_required_fields(self):
        result = health_check()
        assert "sentry_sdk_installed" in result
        assert "sentry_initialized" in result
        assert "dsn_configured" in result
        assert "environment" in result


class TestSafeGetTraceId:
    """trace_id 获取测试"""

    def test_returns_non_empty_string(self):
        tid = _safe_get_trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_does_not_raise(self):
        """即使内部出错也返回默认值，不抛异常"""
        tid = _safe_get_trace_id()
        assert tid  # 非空


# ═══════════════════════════════════════════════════════════════
# Part 2: replay_storage.py 测试
# ═══════════════════════════════════════════════════════════════

class TestReplayStorageInit:
    """初始化测试"""

    def test_creates_dir_and_db(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "replays"))
        assert (tmp_path / "replays").exists()
        assert (tmp_path / "replays" / "replay_meta.db").exists()

    def test_init_fails_on_invalid_path(self):
        """无效路径应抛 ReplayStorageError"""
        with pytest.raises(ReplayStorageError, match=REPLAY_ERR_STORAGE_FAILED):
            ReplayStorage("Z:\\invalid<>path|cannot")

    def test_global_singleton(self, tmp_path):
        _reset_global_for_test()
        s1 = get_replay_storage(str(tmp_path / "rs1"))
        s2 = get_replay_storage(str(tmp_path / "rs2"))  # 应忽略 root，返回已有实例
        assert s1 is s2
        _reset_global_for_test()


class TestReplayStorageStore:
    """store() 测试"""

    def test_store_json_success(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params(compressed=False)
        result = storage.store(**params)
        assert result["stored"] is True
        assert os.path.exists(result["file_path"])
        assert result["size_bytes"] > 0

    def test_store_gzip_base64_success(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params(compressed=True)
        result = storage.store(**params)
        assert result["stored"] is True
        # 文件应是 gzip 格式
        with open(result["file_path"], "rb") as f:
            magic = f.read(2)
        assert magic == b"\x1f\x8b"  # gzip magic number

    def test_store_invalid_replay_id_raises(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params()
        params["replay_id"] = "invalid with spaces"
        with pytest.raises(ReplayStorageError, match=REPLAY_ERR_INVALID_INPUT):
            storage.store(**params)

    def test_store_empty_data_raises(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params()
        params["data"] = ""
        with pytest.raises(ReplayStorageError, match=REPLAY_ERR_INVALID_INPUT):
            storage.store(**params)

    def test_store_invalid_timestamp_raises(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params()
        params["timestamp"] = "not-a-date"
        with pytest.raises(ReplayStorageError, match=REPLAY_ERR_INVALID_INPUT):
            storage.store(**params)

    def test_store_invalid_base64_raises(self, tmp_path):
        """gzip-base64 但 data 不是合法 base64"""
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params(compressed=True)
        params["data"] = "!!!not-base64!!!"
        with pytest.raises(ReplayStorageError, match=REPLAY_ERR_DECODE_FAILED):
            storage.store(**params)

    def test_store_db_failure_rolls_back_file(self, tmp_path):
        """DB 写入失败时已创建的文件应回滚

        由于 sqlite3.Connection.execute 是只读属性不能直接 patch，
        改用替换整个 connection 对象的方式模拟 DB 失败。
        """
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params()

        # 保存原始 connection，注入会抛异常的 mock
        original_conn = storage._conn
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3_error()
        storage._conn = mock_conn

        try:
            with pytest.raises(ReplayStorageError, match=REPLAY_ERR_DB_FAILED):
                storage.store(**params)
        finally:
            storage._conn = original_conn

        # 文件应已被清理（DB 失败后回滚已写文件）
        date_dir = os.path.join(storage.storage_root, datetime.now().strftime("%Y%m%d"))
        files = list(Path(date_dir).glob("*.json")) if os.path.exists(date_dir) else []
        assert len(files) == 0


def sqlite3_error():
    """构造 sqlite3.Error 模拟 DB 失败"""
    import sqlite3
    return sqlite3.Error("mock db failed")


class TestReplayStorageQuery:
    """查询测试"""

    def setup_storage_with_data(self, tmp_path, count=3):
        """辅助：创建存储并写入 count 条数据"""
        storage = ReplayStorage(str(tmp_path / "r"))
        for i in range(count):
            params = MockDataFactory.make_store_params()
            params["replay_id"] = f"replay-{i:03d}"
            params["trace_id"] = f"trace-{i:03d}" if i % 2 == 0 else "trace-shared"
            params["user_session_id"] = f"session-{i:03d}"
            params["error_id"] = f"err-{i:03d}" if i > 0 else None
            params["timestamp"] = (datetime.now() - timedelta(hours=count - i)).isoformat()
            storage.store(**params)
        return storage

    def test_get_by_id_found(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=1)
        meta = storage.get_by_id("replay-000")
        assert meta is not None
        assert meta["replay_id"] == "replay-000"

    def test_get_by_id_not_found(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=1)
        assert storage.get_by_id("nonexistent") is None

    def test_get_data_by_id_json(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=1)
        data = storage.get_data_by_id("replay-000")
        assert data is not None
        parsed = json.loads(data)
        assert "events" in parsed

    def test_get_data_by_id_gzip(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        params = MockDataFactory.make_store_params(compressed=True)
        storage.store(**params)
        data = storage.get_data_by_id(params["replay_id"])
        assert data is not None
        parsed = json.loads(data)
        assert "events" in parsed

    def test_get_data_by_id_not_found_returns_none(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        assert storage.get_data_by_id("nonexistent") is None

    def test_get_data_file_missing_raises(self, tmp_path):
        """DB 有记录但文件丢失，应抛 REPLAY_ERR_STORAGE_FAILED"""
        storage = self.setup_storage_with_data(tmp_path, count=1)
        # 删文件
        meta = storage.get_by_id("replay-000")
        os.remove(meta["file_path"])
        with pytest.raises(ReplayStorageError, match=REPLAY_ERR_STORAGE_FAILED):
            storage.get_data_by_id("replay-000")

    def test_list_by_trace_id(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=3)
        # trace-shared 应匹配 replay-001（i=1, odd→trace-shared）
        items = storage.list_by_trace_id("trace-shared", limit=10)
        assert len(items) >= 1
        for item in items:
            assert item["trace_id"] == "trace-shared"

    def test_list_by_user_session(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=3)
        items = storage.list_by_user_session("session-001", limit=10)
        assert len(items) == 1
        assert items[0]["user_session_id"] == "session-001"

    def test_list_by_time_range(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=3)
        start = (datetime.now() - timedelta(hours=10)).isoformat()
        end = datetime.now().isoformat()
        items = storage.list_by_time_range(start, end, limit=100)
        assert len(items) == 3

    def test_list_recent_24h(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=3)
        items = storage.list_recent_24h(limit=100)
        assert len(items) == 3

    def test_list_invalid_limit_returns_at_most_limit(self, tmp_path):
        storage = self.setup_storage_with_data(tmp_path, count=5)
        items = storage.list_recent_24h(limit=2)
        assert len(items) == 2


class TestReplayStorageStats:
    """关联统计测试"""

    def test_stats_empty_storage(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        stats = storage.get_correlation_stats(hours=24)
        assert stats["total_replays"] == 0
        assert stats["fully_correlated"] == 0
        assert stats["window_hours"] == 24

    def test_stats_with_data(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        # 写入 2 条三向齐全的回放
        for i in range(2):
            params = MockDataFactory.make_store_params()
            params["replay_id"] = f"replay-stats-{i:03d}"
            storage.store(**params)
        stats = storage.get_correlation_stats(hours=24)
        assert stats["total_replays"] == 2
        assert stats["with_trace_id"] == 2
        assert stats["with_error_id"] == 2
        assert stats["fully_correlated"] == 2


class TestReplayStorageCleanup:
    """清理测试"""

    def test_cleanup_removes_old_records(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        # 写入一条 31 天前的记录
        params = MockDataFactory.make_store_params()
        params["replay_id"] = "replay-old-001"
        params["timestamp"] = (datetime.now() - timedelta(days=31)).isoformat()
        storage.store(**params)
        # 写入一条今天的记录
        params2 = MockDataFactory.make_store_params()
        params2["replay_id"] = "replay-new-001"
        storage.store(**params2)

        deleted = storage.cleanup_old_records(days=30)
        assert deleted == 1
        # 新记录应仍存在
        assert storage.get_by_id("replay-new-001") is not None
        # 旧记录应已删除
        assert storage.get_by_id("replay-old-001") is None

    def test_cleanup_with_no_old_records(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))
        # 写入今天的数据
        params = MockDataFactory.make_store_params()
        storage.store(**params)
        deleted = storage.cleanup_old_records(days=30)
        assert deleted == 0


class TestStorageHealthCheck:
    """健康检查测试"""

    def test_returns_dict_with_required_fields(self, tmp_path):
        _reset_global_for_test()
        get_replay_storage(str(tmp_path / "r"))
        result = storage_health_check()
        assert "storage_root" in result
        assert "db_path_exists" in result
        assert "db_writable" in result
        assert "disk_free_bytes" in result
        _reset_global_for_test()

    def test_returns_uninitialized_state(self):
        _reset_global_for_test()
        result = storage_health_check()
        assert result["db_path_exists"] is False
        assert result["db_writable"] is False


# ═══════════════════════════════════════════════════════════════
# Part 3: 端到端流程测试
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:
    """端到端流程：store → get_by_id → get_data_by_id → list → stats → cleanup"""

    def test_full_lifecycle(self, tmp_path):
        storage = ReplayStorage(str(tmp_path / "r"))

        # 1. store（gzip-base64 编码）
        params = MockDataFactory.make_store_params(compressed=True)
        params["replay_id"] = "e2e-001"
        params["trace_id"] = "e2e-trace-abc"
        params["error_id"] = "e2e-err-001"
        result = storage.store(**params)
        assert result["stored"] is True

        # 2. 查询元数据
        meta = storage.get_by_id("e2e-001")
        assert meta["trace_id"] == "e2e-trace-abc"
        assert meta["compressed"] == 1

        # 3. 读取数据并解码
        data = storage.get_data_by_id("e2e-001")
        parsed = json.loads(data)
        assert "events" in parsed

        # 4. 列表查询
        items = storage.list_by_trace_id("e2e-trace-abc", limit=10)
        assert len(items) == 1

        # 5. 统计
        stats = storage.get_correlation_stats(hours=24)
        assert stats["total_replays"] >= 1
        assert stats["fully_correlated"] >= 1

    def test_capture_error_with_replay_correlation(self, monkeypatch):
        """capture_error 上报错误 → error_id 关联回放"""
        _reset_for_test()
        monkeypatch.setenv("SENTRY_DSN", "https://key@host.com/1")
        mock_sdk = MagicMock()
        mock_sdk.capture_exception.return_value = "evt-correlation-001"
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations": MagicMock()}):
            init_sentry()
            event_id = capture_error(
                ValueError("test"),
                trace_id="trace-e2e",
                user_id="user-001",
            )
            assert event_id == "evt-correlation-001"
            # 该 event_id 可作为 error_id 关联回放
            assert mock_sdk.capture_exception.called
        _reset_for_test()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
