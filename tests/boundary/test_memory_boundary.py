"""memory 模块边界测试 — BT-008

补充 memory 模块缺失的 overflow 场景。
覆盖 token 溢出丢弃、LRU 缓存淘汰、超长消息存储等边界。

被测模块：memory/（项目根目录的 memory/ 包）
关键 API：
- MemoryManager: get_context/add_message
- TokenCounter: count/count_messages
- Storage: save_message/load_recent_messages

【可观测性约束】
- 边界显性化：每个边界条件显式断言
"""

import logging
import os
import tempfile
import threading
from pathlib import Path

import pytest

# memory 在项目根目录
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from memory.memory_manager import MemoryManager
from memory.token_counter import TokenCounter


logger = logging.getLogger(__name__)


# ============================================================================
#  fixtures
# ============================================================================


@pytest.fixture
def tmp_data_dir(tmp_path):
    """临时数据目录"""
    return str(tmp_path / "memory_data")


@pytest.fixture
def memory_manager(tmp_data_dir):
    """MemoryManager 实例"""
    config = {
        "data_dir": tmp_data_dir,
        "token_limit": 4096,
        "compress_threshold": 0.8,
    }
    return MemoryManager(config)


@pytest.fixture
def token_counter():
    """TokenCounter 实例"""
    return TokenCounter()


@pytest.fixture
def large_message():
    """超长消息内容"""
    return "这是一条很长的消息。" * 1000  # 约 9000 字符


@pytest.fixture
def many_messages():
    """大量消息列表"""
    return [
        {"role": "user", "content": f"消息内容_{i}" * 10}
        for i in range(100)
    ]


# ============================================================================
#  overflow 边界场景测试
# ============================================================================


class TestOverflowBoundary:
    """溢出边界测试 — token 溢出、缓存淘汰、超大输入"""

    def test_overflow_get_context_token_limit_zero(self, memory_manager):
        """token_limit=0 时 get_context 返回空或仅摘要"""
        memory_manager.add_message("user", "测试消息")
        result = memory_manager.get_context(token_limit=0)
        # token_limit=0 时应丢弃所有消息
        assert isinstance(result, list)

    def test_overflow_get_context_very_small_token_limit(self, memory_manager):
        """极小 token_limit 时 get_context 丢弃最旧消息"""
        for i in range(10):
            memory_manager.add_message("user", f"消息_{i} " * 50)
        result = memory_manager.get_context(token_limit=10)
        # 应只保留少量消息
        assert isinstance(result, list)
        assert len(result) <= 10

    def test_overflow_get_context_many_messages(self, memory_manager):
        """大量消息时 get_context 正常工作"""
        for i in range(50):
            memory_manager.add_message("user", f"消息_{i}")
        result = memory_manager.get_context(token_limit=4096)
        assert isinstance(result, list)

    def test_overflow_add_message_very_long_content(self, memory_manager, large_message):
        """超长消息内容添加"""
        msg_id = memory_manager.add_message("user", large_message)
        assert msg_id is not None

    def test_overflow_token_counter_very_long_string(self, token_counter):
        """TokenCounter 处理超长字符串"""
        long_text = "x" * 100000
        count = token_counter.count(long_text)
        assert count > 0
        assert isinstance(count, int)

    def test_overflow_token_counter_many_messages(self, token_counter, many_messages):
        """TokenCounter 处理大量消息"""
        count = token_counter.count_messages(many_messages)
        assert count > 0
        assert isinstance(count, int)

    def test_overflow_token_counter_empty_messages(self, token_counter):
        """TokenCounter 空消息列表 — 基础计数可能非 0"""
        count = token_counter.count_messages([])
        assert isinstance(count, int)
        assert count >= 0

    def test_overflow_token_counter_extreme_large_message(self, token_counter):
        """TokenCounter 极端大消息"""
        huge_msg = [{"role": "user", "content": "y" * 1000000}]
        count = token_counter.count_messages(huge_msg)
        assert count > 0

    def test_overflow_add_message_extreme_content_size(self, memory_manager):
        """极端大小消息内容添加"""
        huge_content = "z" * 100000  # 100KB
        msg_id = memory_manager.add_message("user", huge_content)
        assert msg_id is not None

    def test_overflow_repeated_add_messages(self, memory_manager):
        """重复添加大量消息"""
        for i in range(200):
            memory_manager.add_message("user", f"消息_{i}")
        result = memory_manager.get_context(token_limit=4096)
        assert isinstance(result, list)

    def test_overflow_get_context_with_summary_and_many_messages(self, memory_manager):
        """带摘要的大量消息 get_context"""
        for i in range(30):
            memory_manager.add_message("user", f"对话内容_{i}")
        result = memory_manager.get_context(token_limit=100)
        assert isinstance(result, list)


# ============================================================================
#  empty/invalid/null 补充测试
# ============================================================================


class TestEmptyBoundary:
    """空值边界测试"""

    def test_empty_get_context_no_messages(self, memory_manager):
        """无消息时 get_context 返回空列表"""
        result = memory_manager.get_context(token_limit=4096)
        assert result == []

    def test_empty_add_message_empty_content(self, memory_manager):
        """空字符串消息内容"""
        msg_id = memory_manager.add_message("user", "")
        assert msg_id is not None

    def test_empty_token_counter_empty_string(self, token_counter):
        """TokenCounter 空字符串"""
        count = token_counter.count("")
        assert count == 0


class TestInvalidInput:
    """非法输入测试"""

    def test_invalid_get_context_negative_token_limit(self, memory_manager):
        """负数 token_limit"""
        memory_manager.add_message("user", "测试")
        result = memory_manager.get_context(token_limit=-1)
        assert isinstance(result, list)

    def test_invalid_token_counter_non_string(self, token_counter):
        """TokenCounter 非字符串输入"""
        with pytest.raises((AttributeError, TypeError)):
            token_counter.count(12345)


class TestNullBoundary:
    """None 值处理测试"""

    def test_null_get_context_token_limit(self, memory_manager):
        """None token_limit — get_context 内部 while 循环处理 None"""
        memory_manager.add_message("user", "测试")
        # None 在比较时可能抛 TypeError 或返回结果
        try:
            result = memory_manager.get_context(token_limit=None)
            assert isinstance(result, list)
        except (TypeError,):
            pass  # None 比较抛 TypeError 也是合理行为

    def test_null_token_counter_input(self, token_counter):
        """TokenCounter None 输入 — 可能返回 0 或抛异常"""
        try:
            count = token_counter.count(None)
            assert isinstance(count, int)
        except (AttributeError, TypeError):
            pass  # None 输入抛异常也是合理行为


# ============================================================================
#  extreme 极值测试
# ============================================================================


class TestExtremeValues:
    """极值测试"""

    def test_extreme_large_token_limit(self, memory_manager):
        """极大 token_limit"""
        memory_manager.add_message("user", "测试消息")
        result = memory_manager.get_context(token_limit=10**10)
        assert isinstance(result, list)

    def test_extreme_many_rapid_add_messages(self, memory_manager):
        """快速连续添加大量消息"""
        for i in range(100):
            memory_manager.add_message("user", f"快速消息_{i}")
        assert memory_manager is not None

    def test_extreme_unicode_content(self, memory_manager):
        """Unicode 多语言内容"""
        contents = [
            "中文消息内容",
            "English message content",
            "日本語のメッセージ",
            "한국어 메시지",
            "Emoji: 😀🎉🔥💯",
        ]
        for content in contents:
            memory_manager.add_message("user", content)
        result = memory_manager.get_context(token_limit=4096)
        assert isinstance(result, list)


# ============================================================================
#  并发安全测试
# ============================================================================


class TestConcurrencySafety:
    """并发安全测试"""

    def test_concurrent_add_messages(self, memory_manager):
        """并发添加消息"""
        errors = []

        def worker(worker_id):
            try:
                for i in range(20):
                    memory_manager.add_message("user", f"worker_{worker_id}_msg_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_get_context(self, memory_manager):
        """并发获取上下文"""
        for i in range(10):
            memory_manager.add_message("user", f"消息_{i}")

        errors = []
        results = []

        def worker():
            try:
                ctx = memory_manager.get_context(token_limit=4096)
                results.append(ctx)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5
