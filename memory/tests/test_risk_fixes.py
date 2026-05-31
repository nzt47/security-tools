"""R001-R004 风险修复验证测试

R001: API Key 验证缺失 - 验证 API Key 验证逻辑
R004: Storage 并发写入无保护 - 验证并发写入安全性
R006: BlackBox 并发写入无保护 - 验证黑匣子并发安全性

运行方式:
    pytest memory/tests/test_risk_fixes.py -v
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory.llm_service import LLMService, LLMServiceError
from memory.storage import Storage, StorageError
from memory.black_box import BlackBox


class TestR001_APIKeyValidation:
    """R001: API Key 验证缺失 - 测试用例"""

    def test_empty_api_key_should_raise_error(self):
        """测试空 API Key 应该抛出异常"""
        with pytest.raises(LLMServiceError) as exc_info:
            LLMService(provider="openai", api_key="")
        assert "API Key" in str(exc_info.value) or "api_key" in str(exc_info.value).lower()

    def test_none_api_key_should_raise_error(self):
        """测试 None API Key 应该抛出异常"""
        with pytest.raises(LLMServiceError) as exc_info:
            LLMService(provider="openai", api_key=None)
        assert "API Key" in str(exc_info.value) or "api_key" in str(exc_info.value).lower()

    def test_short_api_key_should_raise_error(self):
        """测试过短的 API Key 应该抛出异常"""
        with pytest.raises(LLMServiceError) as exc_info:
            LLMService(provider="openai", api_key="short")
        assert "格式" in str(exc_info.value) or "format" in str(exc_info.value).lower()

    def test_valid_api_key_should_not_raise(self):
        """测试有效 API Key 不应抛出异常"""
        # 使用 mock 避免实际 API 调用
        llm = LLMService(provider="openai", api_key="sk-test-valid-key-12345")
        assert llm.api_key == "sk-test-valid-key-12345"
        assert llm.provider == "openai"

    def test_whitespace_only_api_key_should_raise_error(self):
        """测试仅包含空白的 API Key 应该抛出异常"""
        with pytest.raises(LLMServiceError) as exc_info:
            LLMService(provider="openai", api_key="   ")
        assert "API Key" in str(exc_info.value)

    def test_api_key_with_only_newlines_should_raise_error(self):
        """测试仅包含换行符的 API Key 应该抛出异常"""
        with pytest.raises(LLMServiceError) as exc_info:
            LLMService(provider="openai", api_key="\n\t  ")
        assert "API Key" in str(exc_info.value)


class TestR004_StorageConcurrency:
    """R004: Storage 并发写入无保护 - 测试用例"""

    @pytest.fixture
    def storage(self, tmp_path):
        """创建临时 Storage 实例"""
        return Storage(data_dir=str(tmp_path / "test_storage"))

    def test_sequential_writes_should_succeed(self, storage):
        """测试顺序写入应该成功"""
        for i in range(10):
            msg_id = storage.save_message({
                "role": "user",
                "content": f"消息 {i}"
            })
            assert msg_id

        messages = storage.load_recent_messages(limit=100)
        assert len(messages) == 10

    def test_concurrent_writes_should_not_corrupt_data(self, storage):
        """测试并发写入不应损坏数据"""
        num_threads = 20
        messages_per_thread = 5
        expected_total = num_threads * messages_per_thread

        def write_messages(thread_id):
            for i in range(messages_per_thread):
                storage.save_message({
                    "role": "user",
                    "content": f"线程 {thread_id} 消息 {i}"
                })

        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=write_messages, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        messages = storage.load_recent_messages(limit=1000)
        assert len(messages) == expected_total, \
            f"期望 {expected_total} 条消息，实际 {len(messages)} 条"

    def test_concurrent_writes_with_threadpool(self, storage):
        """使用 ThreadPoolExecutor 测试并发写入"""
        num_tasks = 30
        messages_per_task = 3
        expected_total = num_tasks * messages_per_task

        def write_messages(task_id):
            for i in range(messages_per_task):
                storage.save_message({
                    "role": "assistant",
                    "content": f"任务 {task_id} 消息 {i}"
                })

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_messages, i) for i in range(num_tasks)]
            for future in as_completed(futures):
                future.result()

        messages = storage.load_recent_messages(limit=1000)
        assert len(messages) == expected_total, \
            f"并发写入失败：期望 {expected_total}，实际 {len(messages)}"

    def test_concurrent_writes_jsonl_integrity(self, storage):
        """测试并发写入后 JSONL 格式完整性"""
        num_threads = 10
        messages_per_thread = 10

        def write_messages(thread_id):
            for i in range(messages_per_thread):
                storage.save_message({
                    "role": "user",
                    "content": f"消息内容 {thread_id}-{i}"
                })

        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=write_messages, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        messages = storage.load_recent_messages(limit=1000)
        assert len(messages) == num_threads * messages_per_thread

        for msg in messages:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ["user", "assistant", "system"]

    def test_concurrent_read_write(self, storage):
        """测试并发读写操作"""
        storage.save_message({"role": "user", "content": "初始消息"})

        results = {"reads": [], "writes": 0}

        def read_messages():
            for _ in range(20):
                msgs = storage.load_recent_messages(limit=100)
                results["reads"].append(len(msgs))
                time.sleep(0.001)

        def write_messages():
            for i in range(20):
                storage.save_message({"role": "user", "content": f"写入 {i}"})
                results["writes"] += 1
                time.sleep(0.001)

        threads = [
            threading.Thread(target=read_messages),
            threading.Thread(target=read_messages),
            threading.Thread(target=write_messages),
            threading.Thread(target=write_messages),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results["reads"]) == 40, f"读取次数应为 40，实际 {len(results['reads'])}"
        assert results["writes"] == 40, f"写入次数应为 40，实际 {results['writes']}"

    def test_high_concurrency_stress(self, storage):
        """高并发压力测试"""
        num_threads = 50
        messages_per_thread = 20
        expected_total = num_threads * messages_per_thread

        errors = []

        def write_messages(thread_id):
            try:
                for i in range(messages_per_thread):
                    storage.save_message({
                        "role": "user",
                        "content": f"压力测试 线程{thread_id} 消息{i}"
                    })
            except Exception as e:
                errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=25) as executor:
            futures = [executor.submit(write_messages, i) for i in range(num_threads)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(("executor", e))

        messages = storage.load_recent_messages(limit=10000)

        assert len(errors) == 0, f"发生 {len(errors)} 个错误: {errors[:5]}"
        assert len(messages) == expected_total, \
            f"数据丢失：期望 {expected_total}，实际 {len(messages)}"


class TestR006_BlackBoxConcurrency:
    """R006: BlackBox 并发写入无保护 - 测试用例

    注意：由于加密模块依赖问题，这些测试需要在完整的加密环境配置下运行。
    """

    @pytest.fixture
    def blackbox(self, tmp_path):
        """创建临时 BlackBox 实例（禁用加密以避免依赖问题）"""
        return BlackBox(
            log_dir=str(tmp_path / "test_blackbox"),
            max_size_bytes=1024 * 1024,
            max_files=5,
            encryption_enabled=False
        )

    def test_sequential_logs_should_succeed(self, blackbox):
        """测试顺序日志记录应该成功"""
        for i in range(10):
            event_id = blackbox.log("test_event", {"index": i})
            assert event_id

        results = blackbox.query(limit=100)
        assert len(results) == 10

    def test_concurrent_logs_should_not_lose_data(self, blackbox):
        """测试并发日志记录不应丢失数据"""
        num_threads = 20
        logs_per_thread = 10
        expected_total = num_threads * logs_per_thread

        def log_events(thread_id):
            for i in range(logs_per_thread):
                blackbox.log("concurrent_event", {
                    "thread": thread_id,
                    "index": i,
                    "timestamp": time.time()
                })

        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=log_events, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        results = blackbox.query(event_type="concurrent_event", limit=10000)
        assert len(results) == expected_total, \
            f"日志丢失：期望 {expected_total} 条，实际 {len(results)} 条"

    def test_concurrent_logs_json_integrity(self, blackbox):
        """测试并发日志后 JSON 格式完整性"""
        num_threads = 15
        logs_per_thread = 20

        def log_events(thread_id):
            for i in range(logs_per_thread):
                blackbox.log("integrity_test", {
                    "thread": thread_id,
                    "message": f"测试消息 {i}" * 10
                })

        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=log_events, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        results = blackbox.query(event_type="integrity_test", limit=10000)
        assert len(results) == num_threads * logs_per_thread

        for result in results:
            assert "id" in result
            assert "timestamp" in result
            assert "event_type" in result
            assert "data" in result
            assert isinstance(result["data"], dict)

    def test_high_concurrency_stress_blackbox(self, blackbox):
        """高并发压力测试（BlackBox）"""
        num_threads = 30
        logs_per_thread = 15
        expected_total = num_threads * logs_per_thread

        errors = []

        def log_events(thread_id):
            try:
                for i in range(logs_per_thread):
                    blackbox.log("stress_test", {
                        "thread": thread_id,
                        "iteration": i,
                        "data": "x" * 100
                    })
            except Exception as e:
                errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(log_events, i) for i in range(num_threads)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(("executor", e))

        results = blackbox.query(event_type="stress_test", limit=10000)

        assert len(errors) == 0, f"发生 {len(errors)} 个错误: {errors[:5]}"
        assert len(results) == expected_total, \
            f"日志丢失：期望 {expected_total}，实际 {len(results)}"


class TestR001R004Integration:
    """R001 + R004 集成测试"""

    @pytest.fixture
    def storage(self, tmp_path):
        """创建临时 Storage 实例"""
        return Storage(data_dir=str(tmp_path / "test_integration"))

    def test_storage_with_valid_llm_config(self, storage):
        """测试 Storage 与有效的 LLM 配置协同工作"""
        llm = LLMService(
            provider="openai",
            api_key="sk-valid-test-key-12345",
            model="gpt-4"
        )
        assert llm.api_key == "sk-valid-test-key-12345"

        storage.save_message({"role": "user", "content": "测试消息"})
        messages = storage.load_recent_messages(limit=10)
        assert len(messages) == 1

    def test_config_validation_before_storage_ops(self, tmp_path):
        """测试配置验证在存储操作之前"""
        with pytest.raises(LLMServiceError):
            LLMService(provider="openai", api_key="")

        storage = Storage(data_dir=str(tmp_path / "test_storage"))
        storage.save_message({"role": "user", "content": "这个消息应该成功"})

        messages = storage.load_recent_messages(limit=10)
        assert len(messages) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
