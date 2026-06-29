"""Emoji Filter 测试 — Windows GBK 兼容性"""
import logging
import pytest
from agent.log_system.emoji_map import EmojiFilter, _safe_log_message


class TestSafeLogMessage:
    """Emoji 替换功能测试"""

    def test_rocket_replaced(self):
        result = _safe_log_message("🚀 启动成功")
        assert "🚀" not in result
        assert "[ROCKET]" in result

    def test_ok_replaced(self):
        result = _safe_log_message("✅ 完成")
        assert "[OK]" in result

    def test_fail_replaced(self):
        result = _safe_log_message("❌ 失败")
        assert "[FAIL]" in result

    def test_multiple_emoji(self):
        result = _safe_log_message("🚀 ✅ 🔍")
        assert "[ROCKET]" in result
        assert "[OK]" in result
        assert "[SEARCH]" in result

    def test_no_emoji(self):
        result = _safe_log_message("普通文本无变化")
        assert result == "普通文本无变化"

    def test_empty_string(self):
        assert _safe_log_message("") == ""

    def test_non_string(self):
        assert _safe_log_message(42) == 42
        assert _safe_log_message(None) is None

    def test_unknown_emoji_preserved(self):
        """未知 emoji 应保持原样"""
        result = _safe_log_message("🌀 未知emoji")
        assert "🌀" in result


class TestEmojiFilter:
    """EmojiFilter 功能测试"""

    def setup_method(self):
        self.filter = EmojiFilter()

    def test_filter_record(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "🚀 任务完成", None, None)
        self.filter.filter(record)
        assert "🚀" not in record.msg
        assert "[ROCKET]" in record.msg

    def test_filter_record_no_emoji(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "正常消息", None, None)
        self.filter.filter(record)
        assert record.msg == "正常消息"
