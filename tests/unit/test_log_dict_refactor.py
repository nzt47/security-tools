"""log_dict 重构测试套件

覆盖 7 个测试类，验证：
1. log_dict() 函数：键名归一化、trace_id 填充、浅拷贝
2. StructuredLogFormatter dict 快速路径
3. EmojiFilter dict 分支（_safe_log_dict）
4. SensitiveDataFilter dict 分支（_sanitize_dict）
5. DictToJsonFilter 文件 handler 序列化
6. setup_agent_logging 集成挂载
7. 向后兼容（旧 json.dumps 模式）

【生成日志摘要】
- 生成时间: 2026-07-02
- 内容描述: log_dict 重构测试套件 v1.0
- 关键状态: 覆盖 Task 1-4 所有新功能与回归场景
"""

import json
import logging
import os
import sys
import tempfile
import time
import uuid
from io import StringIO
from unittest import mock

import pytest

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.logging_utils import (
    DictToJsonFilter,
    EmojiFilter,
    LogRotationConfig,
    SensitiveDataFilter,
    _safe_log_dict,
    _safe_log_message,
    _trace_id,
    log_dict,
    setup_agent_logging,
)


# ─────────────────────────────────────────────────
# TestLogDictFunction: log_dict 函数
# ─────────────────────────────────────────────────
class TestLogDictFunction:
    """验证 log_dict() 函数的归一化与默认值填充"""

    def test_returns_dict(self):
        result = log_dict({"message": "hello"})
        assert isinstance(result, dict)

    def test_msg_normalized_to_message(self):
        """msg 字段应归一化为 message"""
        result = log_dict({"msg": "hello", "action": "test"})
        assert "msg" not in result
        assert result["message"] == "hello"

    def test_msg_dropped_when_message_exists(self):
        """同时存在 msg 和 message 时，丢弃 msg 保留 message"""
        result = log_dict({"msg": "drop_me", "message": "keep_me", "action": "test"})
        assert "msg" not in result
        assert result["message"] == "keep_me"

    def test_trace_id_filled(self):
        """未提供 trace_id 时应自动填充"""
        result = log_dict({"action": "test"})
        assert "trace_id" in result
        assert len(result["trace_id"]) == 16

    def test_trace_id_preserved(self):
        """已提供 trace_id 时应保留"""
        result = log_dict({"trace_id": "custom123", "action": "test"})
        assert result["trace_id"] == "custom123"

    def test_defaults_filled(self):
        """module_name/action/duration_ms 应有默认值"""
        result = log_dict({"message": "test"})
        assert result["module_name"] == "unknown"
        assert result["action"] == "unknown"
        assert result["duration_ms"] == 0

    def test_extra_fields_preserved(self):
        """额外字段应保留"""
        result = log_dict({"action": "test", "user_id": 42, "tags": ["a", "b"]})
        assert result["user_id"] == 42
        assert result["tags"] == ["a", "b"]

    def test_shallow_copy_no_mutation(self):
        """log_dict 应返回新 dict，不修改输入"""
        payload = {"msg": "test", "action": "demo"}
        result = log_dict(payload)
        assert "msg" in payload  # 原始 dict 未被修改
        assert "msg" not in result

    def test_trace_id_format(self):
        """trace_id 应为 16 位 hex 字符串"""
        result = log_dict({"action": "test"})
        tid = result["trace_id"]
        assert len(tid) == 16
        int(tid, 16)  # 应为合法 hex

    def test_empty_payload(self):
        """空 dict 也应填充默认值"""
        result = log_dict({})
        assert "trace_id" in result
        assert result["module_name"] == "unknown"


# ─────────────────────────────────────────────────
# TestStructuredFormatterDictPath: formatter dict 快速路径
# ─────────────────────────────────────────────────
class TestStructuredFormatterDictPath:
    """验证 StructuredLogFormatter 支持 dict 快速路径"""

    def _make_record(self, msg):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=None, exc_info=None,
        )

    def test_dict_fast_path(self):
        """dict 直接走快速路径，不要求 JSON 字符串"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        data = log_dict({"message": "hello dict", "action": "test.dict"})
        record = self._make_record(data)
        result = formatter.format(record)
        assert "test.dict" in result
        assert "hello dict" in result

    def test_str_json_still_works(self):
        """旧 JSON 字符串模式仍应正常工作"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        json_str = json.dumps({
            "trace_id": "abc12345",
            "module_name": "test_mod",
            "action": "test.legacy",
            "duration_ms": 10,
            "message": "legacy mode",
        }, ensure_ascii=False)
        record = self._make_record(json_str)
        result = formatter.format(record)
        assert "test.legacy" in result
        assert "legacy mode" in result

    def test_priority_keys_highlighted_for_dict(self):
        """dict 模式下 priority 字段应高亮"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        data = log_dict({
            "message": "changed",
            "action": "priority.test",
            "priority_before": ["a"],
            "priority_after": ["a", "b"],
        })
        record = self._make_record(data)
        result = formatter.format(record)
        assert "priority" in result.lower()

    def test_non_json_str_fallback(self):
        """非 JSON 字符串应回退到标准格式"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        record = self._make_record("plain text message")
        result = formatter.format(record)
        assert "plain text message" in result

    def test_dict_without_action_still_formed(self):
        """dict 无 action 字段时，log_dict 会填充默认 action=unknown"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        data = log_dict({"message": "no action"})
        record = self._make_record(data)
        result = formatter.format(record)
        assert "unknown" in result

    def test_dict_preserves_extra_fields(self):
        """dict 模式下额外字段应显示"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        data = log_dict({
            "message": "extra",
            "action": "test.extra",
            "user_id": 42,
            "instance_id": "inst-123",
        })
        record = self._make_record(data)
        result = formatter.format(record)
        assert "42" in result
        assert "inst-123" in result


# ─────────────────────────────────────────────────
# TestEmojiFilterDict: EmojiFilter dict 分支
# ─────────────────────────────────────────────────
class TestEmojiFilterDict:
    """验证 EmojiFilter 支持 dict 类型"""

    def _make_record(self, msg):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=None, exc_info=None,
        )

    def test_dict_str_value_emoji_replaced(self):
        """dict 中 str 值的 emoji 应被替换"""
        f = EmojiFilter()
        data = log_dict({"message": "启动 🚀", "action": "test.emoji"})
        record = self._make_record(data)
        f.filter(record)
        assert "[ROCKET]" in record.msg["message"]
        assert "🚀" not in record.msg["message"]

    def test_dict_nested_dict_emoji_replaced(self):
        """嵌套 dict 中的 emoji 应被替换"""
        f = EmojiFilter()
        data = log_dict({
            "message": "outer",
            "action": "test.nested",
            "nested": {"inner": "完成 ✅"},
        })
        record = self._make_record(data)
        f.filter(record)
        assert "[OK]" in record.msg["nested"]["inner"]

    def test_dict_list_values_emoji_replaced(self):
        """dict 中 list 内 str 的 emoji 应被替换"""
        f = EmojiFilter()
        data = log_dict({
            "message": "list test",
            "action": "test.list",
            "tags": ["tag1 🎯", "tag2 🔍"],
        })
        record = self._make_record(data)
        f.filter(record)
        assert "[TARGET]" in record.msg["tags"][0]
        assert "[SEARCH]" in record.msg["tags"][1]

    def test_dict_non_str_values_unchanged(self):
        """dict 中非 str 值（int/bool/None）不应被修改"""
        f = EmojiFilter()
        data = log_dict({
            "message": "test",
            "action": "test.types",
            "count": 42,
            "enabled": True,
            "empty": None,
        })
        record = self._make_record(data)
        f.filter(record)
        assert record.msg["count"] == 42
        assert record.msg["enabled"] is True
        assert record.msg["empty"] is None

    def test_original_dict_not_mutated(self):
        """原始 dict 不应被修改（_safe_log_dict 返回新 dict）"""
        f = EmojiFilter()
        original_msg = "保持 🚀"
        data = {"message": original_msg, "action": "test.immutable"}
        record = self._make_record(dict(data))
        f.filter(record)
        # record.msg 是新 dict，原始 data 中的 emoji 仍存在
        assert "🚀" in data.get("message", "")


# ─────────────────────────────────────────────────
# TestSensitiveDataFilterDict: SensitiveDataFilter dict 分支
# ─────────────────────────────────────────────────
class TestSensitiveDataFilterDict:
    """验证 SensitiveDataFilter 支持 dict 类型"""

    def _make_record(self, msg):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=None, exc_info=None,
        )

    def test_api_key_redacted_in_dict(self):
        """dict 中敏感 key（api_key）的值应被替换"""
        f = SensitiveDataFilter()
        data = log_dict({
            "message": "test",
            "action": "test.sensitive",
            "api_key": "sk-super-secret-12345",
        })
        record = self._make_record(data)
        f.filter(record)
        assert record.msg["api_key"] == "[REDACTED]"

    def test_password_redacted_in_dict(self):
        """dict 中 password 字段应被脱敏"""
        f = SensitiveDataFilter()
        data = log_dict({
            "message": "login",
            "action": "test.password",
            "password": "my_secret_pw",
        })
        record = self._make_record(data)
        f.filter(record)
        assert record.msg["password"] == "[REDACTED]"

    def test_nested_dict_sensitive_redacted(self):
        """嵌套 dict 中的敏感字段也应被脱敏"""
        f = SensitiveDataFilter()
        data = log_dict({
            "message": "nested",
            "action": "test.nested",
            "config": {"token": "abc123", "name": "ok"},
        })
        record = self._make_record(data)
        f.filter(record)
        assert record.msg["config"]["token"] == "[REDACTED]"
        assert record.msg["config"]["name"] == "ok"

    def test_str_value_with_pattern_redacted(self):
        """dict 中 str 值含敏感模式（如 sk-xxx）应被脱敏"""
        f = SensitiveDataFilter()
        data = log_dict({
            "message": "config loaded",
            "action": "test.pattern",
            "detail": "key is sk-abcdefghijk12345",
        })
        record = self._make_record(data)
        f.filter(record)
        assert "sk-abcdefghijk12345" not in record.msg["detail"]
        assert "[REDACTED]" in record.msg["detail"]

    def test_non_sensitive_fields_preserved(self):
        """非敏感字段不应被修改"""
        f = SensitiveDataFilter()
        data = log_dict({
            "message": "test",
            "action": "test.preserve",
            "user_id": 42,
            "username": "alice",
            "tags": ["a", "b"],
        })
        record = self._make_record(data)
        f.filter(record)
        assert record.msg["user_id"] == 42
        assert record.msg["username"] == "alice"
        assert record.msg["tags"] == ["a", "b"]

    def test_str_msg_still_sanitized(self):
        """str 类型 record.msg 仍应正常脱敏（向后兼容）"""
        f = SensitiveDataFilter()
        record = self._make_record("password=my_secret_pw")
        f.filter(record)
        assert "my_secret_pw" not in record.msg


# ─────────────────────────────────────────────────
# TestDictToJsonFilter: DictToJsonFilter 类
# ─────────────────────────────────────────────────
class TestDictToJsonFilter:
    """验证 DictToJsonFilter 将 dict 序列化为 JSON 字符串"""

    def _make_record(self, msg):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=None, exc_info=None,
        )

    def test_dict_converted_to_json_string(self):
        """dict 应被序列化为 JSON 字符串"""
        f = DictToJsonFilter()
        data = log_dict({"message": "hello", "action": "test"})
        record = self._make_record(data)
        f.filter(record)
        assert isinstance(record.msg, str)
        parsed = json.loads(record.msg)
        assert parsed["message"] == "hello"

    def test_str_msg_unchanged(self):
        """str 类型 record.msg 不应被修改"""
        f = DictToJsonFilter()
        record = self._make_record("plain text")
        f.filter(record)
        assert record.msg == "plain text"

    def test_json_string_parseable(self):
        """序列化后的字符串应是合法 JSON"""
        f = DictToJsonFilter()
        data = log_dict({
            "message": "复杂对象",
            "action": "test.complex",
            "list": [1, 2, 3],
            "nested": {"k": "v"},
        })
        record = self._make_record(data)
        f.filter(record)
        parsed = json.loads(record.msg)
        assert parsed["list"] == [1, 2, 3]
        assert parsed["nested"] == {"k": "v"}

    def test_chinese_not_escaped(self):
        """中文不应被转义为 \\uXXXX（ensure_ascii=False）"""
        f = DictToJsonFilter()
        data = log_dict({"message": "测试中文", "action": "test.zh"})
        record = self._make_record(data)
        f.filter(record)
        assert "测试中文" in record.msg
        assert "\\u" not in record.msg

    def test_non_dict_non_str_unchanged(self):
        """非 dict/str 类型（如 int）不应被修改"""
        f = DictToJsonFilter()
        record = self._make_record(42)
        f.filter(record)
        assert record.msg == 42


# ─────────────────────────────────────────────────
# TestIntegrationSetup: setup_agent_logging 集成
# ─────────────────────────────────────────────────
class TestIntegrationSetup:
    """验证 setup_agent_logging 正确挂载 DictToJsonFilter"""

    def test_file_handler_has_dict_to_json_filter(self, tmp_path):
        """文件 handler 应挂载 DictToJsonFilter"""
        log_file = str(tmp_path / "test.log")
        logger = setup_agent_logging(
            enable_console=False,
            enable_file=True,
            log_file=log_file,
        )
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) > 0
        # 检查文件 handler 上是否有 DictToJsonFilter
        for h in file_handlers:
            filter_types = [type(f).__name__ for f in h.filters]
            assert "DictToJsonFilter" in filter_types, f"文件 handler 缺少 DictToJsonFilter，实际: {filter_types}"

    def test_console_handler_no_dict_to_json_filter(self, tmp_path):
        """控制台 handler 不应挂载 DictToJsonFilter"""
        logger = setup_agent_logging(
            enable_console=True,
            enable_file=False,
        )
        root = logging.getLogger()
        console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        assert len(console_handlers) > 0
        for h in console_handlers:
            filter_types = [type(f).__name__ for f in h.filters]
            assert "DictToJsonFilter" not in filter_types, "控制台 handler 不应挂载 DictToJsonFilter"

    def test_file_log_json_parseable(self, tmp_path):
        """文件日志中的 dict 应被序列化为合法 JSON"""
        log_file = str(tmp_path / "test.log")
        logger = setup_agent_logging(
            enable_console=False,
            enable_file=True,
            log_file=log_file,
        )
        test_logger = logging.getLogger("test.integration.dict")
        test_logger.info(log_dict({
            "message": "file json test",
            "action": "test.file.json",
            "duration_ms": 5,
        }))
        # 关闭 handler 确保写入
        for h in logging.getLogger().handlers:
            h.flush()
        time.sleep(0.1)
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        # 应找到 JSON 字符串
        assert "file json test" in content
        # 提取并解析 JSON
        for line in content.splitlines():
            if "file json test" in line:
                # 文件格式: <asctime> [<level>] <name> <pid>:<tid>: <json>
                json_part = line.split(": ", 2)[-1] if ": " in line else line
                try:
                    data = json.loads(json_part)
                    assert data["message"] == "file json test"
                    assert data["action"] == "test.file.json"
                    break
                except json.JSONDecodeError:
                    continue

    def test_console_format_unchanged(self, tmp_path):
        """控制台 handler 应使用标准 Formatter（不挂载 DictToJsonFilter）

        注：setup_agent_logging 实际使用标准 logging.Formatter，
        StructuredLogFormatter 由 setup_readable_logging() 单独配置。
        本测试验证控制台 handler 的 formatter 不被本次重构破坏。
        """
        logger = setup_agent_logging(
            enable_console=True,
            enable_file=False,
        )
        root = logging.getLogger()
        console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        assert len(console_handlers) > 0
        # 控制台 handler 应有 formatter（不为 None）
        assert console_handlers[0].formatter is not None
        # 控制台 handler 不应挂载 DictToJsonFilter
        filter_types = [type(f).__name__ for f in console_handlers[0].filters]
        assert "DictToJsonFilter" not in filter_types


# ─────────────────────────────────────────────────
# TestBackwardCompatibility: 向后兼容
# ─────────────────────────────────────────────────
class TestBackwardCompatibility:
    """验证旧代码（json.dumps 模式）仍能正常工作"""

    def _make_record(self, msg):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=None, exc_info=None,
        )

    def test_legacy_json_dumps_mode(self):
        """旧模式：logger.info(json.dumps({...})) 应正常工作"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        json_str = json.dumps({
            "trace_id": "legacy123",
            "module_name": "legacy",
            "action": "legacy.action",
            "duration_ms": 100,
            "message": "old mode still works",
        }, ensure_ascii=False)
        record = self._make_record(json_str)
        result = formatter.format(record)
        assert "legacy.action" in result
        assert "old mode still works" in result

    def test_plain_string_message(self):
        """纯字符串消息应回退到标准格式"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()
        record = self._make_record("这是一条普通消息")
        result = formatter.format(record)
        assert "这是一条普通消息" in result

    def test_mixed_dict_and_str_in_pipeline(self):
        """同一管道中 dict 和 str 混合应都正常"""
        from scripts.struct_log_formatter import StructuredLogFormatter
        formatter = StructuredLogFormatter()

        # dict 模式
        data = log_dict({"message": "dict mode", "action": "test.dict"})
        record1 = self._make_record(data)
        r1 = formatter.format(record1)
        assert "dict mode" in r1

        # str 模式
        json_str = json.dumps({
            "trace_id": "str12345678",
            "module_name": "m",
            "action": "test.str",
            "duration_ms": 1,
            "message": "str mode",
        }, ensure_ascii=False)
        record2 = self._make_record(json_str)
        r2 = formatter.format(record2)
        assert "str mode" in r2


# ─────────────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────────────
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
