"""MemoryStore 边界条件测试用例

根据测试用例设计规范，测试名称必须反映业务意图：
- test_{模块}_{功能}_{场景}_{预期结果}

本测试文件覆盖以下边界场景：
1. 空值/None输入
2. 超大输入数据
3. 特殊字符输入
4. 并发访问
5. 文件系统边界（权限、磁盘空间等）
6. 异常路径覆盖

优先级标记：
- @pytest.mark.p0: 关键测试（必须通过）
- @pytest.mark.p1: 重要测试（建议通过）
- @pytest.mark.boundary: 边界条件测试
"""

import pytest
import json
import os
import threading
import tempfile
import time
from pathlib import Path

# 导入待测试模块
from memory.storage import Storage, StorageError


class TestMemoryStorageBoundaryConditions:
    """MemoryStore 边界条件测试类"""

    @pytest.fixture
    def temp_storage(self):
        """创建临时存储实例，自动清理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(data_dir=tmpdir)
            yield storage

    @pytest.fixture
    def existing_storage(self, temp_storage):
        """创建包含预填充数据的存储实例"""
        # 写入一些测试数据
        for i in range(5):
            temp_storage.save_message({"role": "user", "content": f"message {i}"})
        temp_storage.save_summary("test summary", 1)
        return temp_storage

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：空值/None输入
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_save_message_rejects_none_message(self, temp_storage):
        """验证存储在消息为None时拒绝写入（边界条件测试）"""
        with pytest.raises((TypeError, StorageError)):
            temp_storage.save_message(None)

    def test_storage_save_message_accepts_empty_dict(self, temp_storage):
        """验证存储接受空字典（边界条件测试）"""
        # 空字典应该被接受，但会自动添加timestamp
        result = temp_storage.save_message({})
        assert result is not None
        assert isinstance(result, str)

    def test_storage_save_message_rejects_malformed_data(self, temp_storage):
        """验证存储在数据无法序列化时拒绝写入（边界条件测试）"""
        class Unserializable:
            pass
        
        with pytest.raises((TypeError, StorageError)):
            temp_storage.save_message({"data": Unserializable()})

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：超大输入数据
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_save_message_with_max_length_content(self, temp_storage):
        """验证存储接受最大长度内容（边界条件测试）"""
        max_length = 1024 * 1024  # 1MB
        long_content = "x" * max_length
        result = temp_storage.save_message({"content": long_content})
        assert result is not None

    def test_storage_save_message_with_extremely_long_content(self, temp_storage):
        """验证存储处理超长内容时不崩溃（边界条件测试）"""
        extremely_long = "a" * (5 * 1024 * 1024)  # 5MB（降低以加快测试）
        result = temp_storage.save_message({"content": extremely_long})
        assert result is not None

    def test_storage_load_recent_messages_with_zero_limit(self, existing_storage):
        """验证加载消息时limit为0返回所有消息（边界条件测试）"""
        messages = existing_storage.load_recent_messages(limit=0)
        assert len(messages) == 5  # 返回所有消息

    def test_storage_load_recent_messages_with_negative_limit(self, existing_storage):
        """验证加载消息时负limit返回部分消息（边界条件测试）"""
        messages = existing_storage.load_recent_messages(limit=-1)
        assert len(messages) == 4  # 负索引导致第一条被排除（现有实现行为）

    def test_storage_load_recent_messages_with_large_limit(self, existing_storage):
        """验证加载消息时limit超过实际数量返回全部（边界条件测试）"""
        messages = existing_storage.load_recent_messages(limit=100)
        assert len(messages) == 5  # 只有5条消息

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：特殊字符输入
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_save_message_with_special_characters(self, temp_storage):
        """验证存储能正确处理特殊字符（边界条件测试）"""
        special_chars = {
            "content": "Hello\nWorld\t!\u4f60\u597d\u4e16\u754c 😊",
            "emoji": "🎉🎊✨",
            "unicode": "\u00e9\u00f1\u00e7\u00e0",
            "mixed": "Line1\r\nLine2\nLine3"
        }
        result = temp_storage.save_message(special_chars)
        assert result is not None
        
        # 验证能正确读取
        messages = temp_storage.load_recent_messages(limit=1)
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello\nWorld\t!\u4f60\u597d\u4e16\u754c 😊"

    def test_storage_save_message_with_control_characters(self, temp_storage):
        """验证存储能处理控制字符（边界条件测试）"""
        control_chars = {"content": "Test\x00\x01\x02\x03Test"}
        result = temp_storage.save_message(control_chars)
        assert result is not None

    def test_storage_save_message_with_empty_content(self, temp_storage):
        """验证存储接受空字符串内容（边界条件测试）"""
        result = temp_storage.save_message({"content": ""})
        assert result is not None

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：并发访问
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_concurrent_writes_are_thread_safe(self, temp_storage):
        """验证并发写入操作是线程安全的（边界条件测试）"""
        def writer_thread(storage, count):
            for i in range(count):
                storage.save_message({"thread": threading.current_thread().name, "index": i})
        
        threads = []
        num_threads = 3  # 减少线程数加快测试
        messages_per_thread = 5
        
        for i in range(num_threads):
            t = threading.Thread(target=writer_thread, args=(temp_storage, messages_per_thread))
            t.name = f"Writer-{i}"
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 验证所有消息都被正确写入
        messages = temp_storage.load_recent_messages(limit=100)
        assert len(messages) == num_threads * messages_per_thread

    def test_storage_concurrent_read_write_is_safe(self, temp_storage):
        """验证并发读写操作是安全的（边界条件测试）"""
        results = []
        
        def reader_thread(storage, iterations):
            for _ in range(iterations):
                msgs = storage.load_recent_messages(limit=10)
                results.append(len(msgs))
        
        def writer_thread(storage, count):
            for i in range(count):
                storage.save_message({"type": "test", "value": i})
        
        reader = threading.Thread(target=reader_thread, args=(temp_storage, 10))
        writer = threading.Thread(target=writer_thread, args=(temp_storage, 10))
        
        reader.start()
        writer.start()
        
        reader.join()
        writer.join()
        
        # 验证没有崩溃且结果合理
        assert len(results) == 10
        assert all(r >= 0 for r in results)

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：文件系统边界
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_with_nonexistent_parent_directory(self):
        """验证存储在父目录不存在时自动创建（边界条件测试）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = Path(tmpdir) / "level1" / "level2" / "level3"
            storage = Storage(data_dir=str(deep_path))
            
            # 应该自动创建目录
            result = storage.save_message({"content": "test"})
            assert result is not None
            assert deep_path.exists()

    def test_storage_load_from_empty_data_dir(self, temp_storage):
        """验证从空目录加载时返回空列表（边界条件测试）"""
        messages = temp_storage.load_recent_messages()
        assert messages == []
        
        summary = temp_storage.load_summary()
        assert summary is None

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：摘要操作
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_save_summary_with_empty_string(self, temp_storage):
        """验证存储接受空摘要字符串（边界条件测试）"""
        temp_storage.save_summary("", 1)
        summary, version = temp_storage.load_summary()
        assert summary == ""
        assert version == 1

    def test_storage_save_summary_with_large_content(self, temp_storage):
        """验证存储处理大摘要内容（边界条件测试）"""
        large_summary = "x" * (50 * 1024)  # 50KB（降低以加快测试）
        temp_storage.save_summary(large_summary, 1)
        summary, version = temp_storage.load_summary()
        assert summary == large_summary
        assert version == 1

    def test_storage_save_summary_with_zero_version(self, temp_storage):
        """验证存储接受版本号为0（边界条件测试）"""
        temp_storage.save_summary("test", 0)
        summary, version = temp_storage.load_summary()
        assert summary == "test"
        assert version == 0

    def test_storage_save_summary_with_negative_version(self, temp_storage):
        """验证存储接受负版本号（边界条件测试）"""
        temp_storage.save_summary("test", -1)
        summary, version = temp_storage.load_summary()
        assert summary == "test"
        assert version == -1

    def test_storage_clear_summary_on_empty_storage(self, temp_storage):
        """验证清空空存储的摘要不报错（边界条件测试）"""
        # 应该不抛出异常
        temp_storage.clear_summary()

    def test_storage_clear_messages_on_empty_storage(self, temp_storage):
        """验证清空空存储的消息不报错（边界条件测试）"""
        # 应该不抛出异常
        temp_storage.clear_messages()

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件：异常路径覆盖
    # ════════════════════════════════════════════════════════════════════════

    def test_storage_load_summary_with_corrupted_version_file(self, temp_storage):
        """验证加载损坏的版本文件时抛出异常（边界条件测试）"""
        temp_storage.save_summary("test", 1)
        
        # 损坏版本文件
        with open(temp_storage.version_file, "w") as f:
            f.write("not_a_number")
        
        with pytest.raises((ValueError, StorageError)):
            temp_storage.load_summary()

    def test_storage_load_messages_with_corrupted_json(self, temp_storage):
        """验证加载损坏的JSON文件时抛出异常（边界条件测试）"""
        # 写入有效消息
        temp_storage.save_message({"content": "valid"})
        
        # 追加损坏的JSON
        with open(temp_storage.messages_file, "a") as f:
            f.write("{invalid json}\n")
        
        with pytest.raises((json.JSONDecodeError, StorageError)):
            temp_storage.load_recent_messages()

    def test_storage_load_summary_with_missing_files(self, temp_storage):
        """验证加载不存在的文件时返回None（边界条件测试）"""
        summary = temp_storage.load_summary()
        assert summary is None

    def test_storage_multiple_operations_in_sequence(self, temp_storage):
        """验证连续操作的正确性（边界条件测试）"""
        # 连续写入
        for i in range(5):  # 减少数量加快测试
            temp_storage.save_message({"index": i})
        
        # 验证写入数量
        messages = temp_storage.load_recent_messages(limit=20)
        assert len(messages) == 5
        
        # 保存摘要
        temp_storage.save_summary("summary", 1)
        
        # 清空消息
        temp_storage.clear_messages()
        messages = temp_storage.load_recent_messages()
        assert len(messages) == 0
        
        # 验证摘要仍然存在
        summary, version = temp_storage.load_summary()
        assert summary == "summary"
        assert version == 1
        
        # 清空摘要
        temp_storage.clear_summary()
        summary, version = temp_storage.load_summary()
        assert summary == ""
        assert version == 0