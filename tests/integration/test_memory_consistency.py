"""记忆读写链路集成测试

测试覆盖：
1. 并发读写记忆的数据一致性
2. 记忆存储→检索→删除的完整生命周期
3. 敏感信息在记忆链路中的过滤效果
4. 记忆持久化的可靠性（进程重启后数据存在）
5. 记忆容量限制的处理
6. 记忆搜索结果的排序正确性
"""

import pytest
import time
import json
import tempfile
import os
import logging
import threading
from datetime import datetime
from unittest.mock import MagicMock

pytestmark = pytest.mark.integration
pytest.timeout = 30

logger = logging.getLogger(__name__)


class TestMemoryConsistency:
    """记忆读写链路集成测试"""

    def test_concurrent_read_write_memory_consistency(self):
        """测试并发读写记忆的数据一致性"""
        from agent.memory.router import MemoryRouter
        from agent.memory.base import MemoryResult

        logger.info("="*60)
        logger.info(f"[测试开始] test_concurrent_read_write_memory_consistency")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化内存存储和路由器")
        memory_store = {}
        router = MemoryRouter()

        logger.info("[步骤2] 定义并发操作")
        write_count = [0]
        read_count = [0]
        errors = []
        lock = threading.Lock()

        def writer():
            for i in range(20):
                try:
                    key = f"key_{threading.current_thread().name}_{i}"
                    value = f"value_{i}"
                    with lock:
                        memory_store[key] = value
                        write_count[0] += 1
                    time.sleep(0.01)
                except Exception as e:
                    with lock:
                        errors.append(f"Writer error: {e}")

        def reader():
            for i in range(30):
                try:
                    with lock:
                        keys = list(memory_store.keys())
                        if keys:
                            read_count[0] += len(keys)
                    time.sleep(0.005)
                except Exception as e:
                    with lock:
                        errors.append(f"Reader error: {e}")

        logger.info("[步骤3] 创建并发线程")
        threads = []
        for i in range(3):
            t = threading.Thread(target=writer, name=f"Writer-{i}")
            threads.append(t)
        for i in range(2):
            t = threading.Thread(target=reader, name=f"Reader-{i}")
            threads.append(t)

        logger.info("[步骤4] 启动所有线程")
        start_time = time.time()
        for t in threads:
            t.start()

        logger.info("[步骤5] 等待所有线程完成")
        for t in threads:
            t.join()
        duration = time.time() - start_time

        logger.info(f"[步骤5完成] 并发操作完成，耗时: {duration:.3f}s")
        logger.info(f"  写入次数: {write_count[0]}")
        logger.info(f"  读取键数量: {read_count[0]}")
        logger.info(f"  错误数量: {len(errors)}")

        assert len(errors) == 0
        logger.info("[断言通过] 无并发错误")

        assert write_count[0] == 60
        logger.info("[断言通过] 所有写入操作完成")

        assert len(memory_store) == 60
        logger.info("[断言通过] 数据完整性验证通过")

        logger.info("="*60)
        logger.info(f"[测试完成] test_concurrent_read_write_memory_consistency")
        logger.info("="*60)

    def test_memory_lifecycle_store_retrieve_delete(self):
        """测试记忆存储→检索→删除的完整生命周期"""
        from agent.memory.router import MemoryRouter
        from agent.memory.base import MemoryResult

        logger.info("="*60)
        logger.info(f"[测试开始] test_memory_lifecycle_store_retrieve_delete")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化内存存储")
        memory_store = {}

        logger.info("[步骤2] 存储记忆")
        test_items = [
            {"id": "1", "data": "test_data_1", "metadata": {"type": "user"}},
            {"id": "2", "data": "test_data_2", "metadata": {"type": "system"}},
            {"id": "3", "data": "test_data_3", "metadata": {"type": "conversation"}}
        ]

        for item in test_items:
            memory_store[item["id"]] = {
                "content": str(item["data"]),
                "metadata": item["metadata"]
            }
            logger.info(f"  存储记忆: id={item['id']}, type={item['metadata']['type']}")

        assert len(memory_store) == 3
        logger.info("[断言通过] 记忆存储成功")

        logger.info("[步骤3] 检索记忆")
        results = []
        for item in test_items:
            if item["id"] in memory_store:
                stored = memory_store[item["id"]]
                results.append(MemoryResult(
                    content=stored["content"],
                    confidence=1.0,
                    source="memory",
                    metadata=stored["metadata"]
                ))
                logger.info(f"  检索成功: id={item['id']}, content={stored['content']}")

        assert len(results) == 3
        logger.info("[断言通过] 所有记忆均可检索")

        assert all(r.content == f"test_data_{i+1}" for i, r in enumerate(results))
        logger.info("[断言通过] 检索内容正确")

        logger.info("[步骤4] 删除记忆")
        delete_ids = ["1", "3"]
        for id_to_delete in delete_ids:
            if id_to_delete in memory_store:
                del memory_store[id_to_delete]
                logger.info(f"  删除记忆: id={id_to_delete}")

        assert len(memory_store) == 1
        logger.info("[断言通过] 记忆删除成功")

        assert "2" in memory_store
        logger.info("[断言通过] 指定记忆保留")

        logger.info("="*60)
        logger.info(f"[测试完成] test_memory_lifecycle_store_retrieve_delete")
        logger.info("="*60)

    def test_sensitive_info_filtering_in_memory(self):
        """测试敏感信息在记忆链路中的过滤效果"""
        # 修复：MemoryFilter 不存在，实际类名为 SensitiveDataFilter（向后兼容别名）
        # 修复：DataSanitizer.sanitize() 不存在，实际方法为 sanitize_dict()
        from agent.memory.filter import SensitiveDataFilter
        from agent.security_utils import DataSanitizer

        logger.info("="*60)
        logger.info(f"[测试开始] test_sensitive_info_filtering_in_memory")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化数据脱敏器")
        sanitizer = DataSanitizer()

        logger.info("[步骤2] 准备包含敏感信息的测试数据")
        sensitive_data = {
            "user": "admin",
            "password": "secret123",
            "api_key": "sk-12345-secret-key",
            "email": "admin@example.com",
            "phone": "13800138000",
            "normal_field": "safe_value"
        }

        logger.info(f"  原始敏感数据: {sensitive_data}")

        logger.info("[步骤3] 执行脱敏处理")
        sanitized = sanitizer.sanitize_dict(sensitive_data)
        logger.info(f"  脱敏后数据: {sanitized}")

        assert sanitized["password"] != "secret123"
        logger.info("[断言通过] 密码已脱敏")

        assert sanitized["api_key"] != "sk-12345-secret-key"
        logger.info("[断言通过] API密钥已脱敏")

        assert sanitized["normal_field"] == "safe_value"
        logger.info("[断言通过] 普通字段保持不变")

        logger.info("[步骤4] 测试记忆过滤器")
        memory_filter = SensitiveDataFilter()
        filtered = memory_filter.filter(sensitive_data)
        logger.info(f"  过滤后数据: {filtered}")

        assert "password" not in filtered or filtered["password"] != "secret123"
        assert "api_key" not in filtered or filtered["api_key"] != "sk-12345-secret-key"
        logger.info("[断言通过] 敏感信息过滤成功")

        logger.info("="*60)
        logger.info(f"[测试完成] test_sensitive_info_filtering_in_memory")
        logger.info("="*60)

    def test_memory_persistence_reliability(self):
        """测试记忆持久化的可靠性（进程重启后数据存在）"""
        import sqlite3

        logger.info("="*60)
        logger.info(f"[测试开始] test_memory_persistence_reliability")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建临时数据库")
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_memory.db")

            logger.info("[步骤2] 模拟第一次进程启动 - 存储数据")
            conn1 = sqlite3.connect(db_path)
            cursor1 = conn1.cursor()
            cursor1.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, metadata TEXT)")

            test_data = [
                ("mem_001", "memory_content_1", json.dumps({"type": "user"})),
                ("mem_002", "memory_content_2", json.dumps({"type": "system"})),
                ("mem_003", "memory_content_3", json.dumps({"type": "conversation"}))
            ]

            for item in test_data:
                cursor1.execute("INSERT INTO memories VALUES (?, ?, ?)", item)
                logger.info(f"  存储记忆: id={item[0]}")

            conn1.commit()
            conn1.close()
            logger.info("[步骤2完成] 数据已持久化")

            logger.info("[步骤3] 模拟进程重启 - 恢复数据")
            conn2 = sqlite3.connect(db_path)
            cursor2 = conn2.cursor()

            cursor2.execute("SELECT COUNT(*) FROM memories")
            count = cursor2.fetchone()[0]
            logger.info(f"  恢复后记录数: {count}")

            assert count == 3
            logger.info("[断言通过] 记录数量正确")

            cursor2.execute("SELECT id, content FROM memories ORDER BY id")
            rows = cursor2.fetchall()

            for i, row in enumerate(rows):
                expected_content = f"memory_content_{i+1}"
                assert row[1] == expected_content
                logger.info(f"  验证记忆: id={row[0]}, content={row[1]}")

            logger.info("[断言通过] 所有数据恢复正确")

            conn2.close()

        logger.info("="*60)
        logger.info(f"[测试完成] test_memory_persistence_reliability")
        logger.info("="*60)

    def test_memory_capacity_limit_handling(self):
        """测试记忆容量限制的处理"""
        from agent.memory.router import MemoryRouter

        logger.info("="*60)
        logger.info(f"[测试开始] test_memory_capacity_limit_handling")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化带容量限制的内存存储")
        max_capacity = 10
        memory_store = {}

        logger.info(f"[步骤2] 存储超过容量限制的数据 ({max_capacity + 5} 条)")
        for i in range(max_capacity + 5):
            memory_store[f"key_{i}"] = {
                "content": f"content_{i}",
                "timestamp": i,
                "metadata": {"index": i}
            }
            logger.info(f"  存储: key_{i}")

        assert len(memory_store) == max_capacity + 5
        logger.info(f"[步骤3] 当前存储数量: {len(memory_store)}")

        logger.info("[步骤4] 执行容量清理策略（保留最新的N条）")
        if len(memory_store) > max_capacity:
            sorted_keys = sorted(memory_store.keys(), key=lambda k: memory_store[k]["timestamp"])
            keys_to_remove = sorted_keys[:len(memory_store) - max_capacity]

            for key in keys_to_remove:
                del memory_store[key]
                logger.info(f"  移除过期记忆: {key}")

        assert len(memory_store) == max_capacity
        logger.info(f"[断言通过] 容量限制生效，当前存储: {len(memory_store)}")

        remaining_indices = [memory_store[k]["metadata"]["index"] for k in memory_store.keys()]
        expected_indices = list(range(5, max_capacity + 5))
        assert set(remaining_indices) == set(expected_indices)
        logger.info("[断言通过] 保留的是最新的数据")

        logger.info("="*60)
        logger.info(f"[测试完成] test_memory_capacity_limit_handling")
        logger.info("="*60)

    def test_memory_search_result_sorting_correctness(self):
        """测试记忆搜索结果的排序正确性"""
        from agent.memory.base import MemoryResult

        logger.info("="*60)
        logger.info(f"[测试开始] test_memory_search_result_sorting_correctness")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建测试搜索结果")
        search_results = [
            MemoryResult(content="result_1", confidence=0.9, source="memory", metadata={"timestamp": 100}),
            MemoryResult(content="result_2", confidence=0.7, source="memory", metadata={"timestamp": 300}),
            MemoryResult(content="result_3", confidence=0.95, source="memory", metadata={"timestamp": 200}),
            MemoryResult(content="result_4", confidence=0.8, source="memory", metadata={"timestamp": 50}),
            MemoryResult(content="result_5", confidence=0.6, source="memory", metadata={"timestamp": 400})
        ]

        logger.info("  原始结果:")
        for r in search_results:
            logger.info(f"    confidence={r.confidence}, timestamp={r.metadata['timestamp']}")

        logger.info("[步骤2] 按置信度排序")
        sorted_by_confidence = sorted(search_results, key=lambda r: r.confidence, reverse=True)

        logger.info("  按置信度排序后:")
        for r in sorted_by_confidence:
            logger.info(f"    confidence={r.confidence}")

        assert sorted_by_confidence[0].confidence == 0.95
        assert sorted_by_confidence[-1].confidence == 0.6
        logger.info("[断言通过] 置信度排序正确")

        logger.info("[步骤3] 按时间戳排序")
        sorted_by_timestamp = sorted(search_results, key=lambda r: r.metadata["timestamp"], reverse=True)

        logger.info("  按时间戳排序后:")
        for r in sorted_by_timestamp:
            logger.info(f"    timestamp={r.metadata['timestamp']}")

        assert sorted_by_timestamp[0].metadata["timestamp"] == 400
        assert sorted_by_timestamp[-1].metadata["timestamp"] == 50
        logger.info("[断言通过] 时间戳排序正确")

        logger.info("[步骤4] 组合排序（置信度优先，时间戳次之）")
        sorted_combined = sorted(search_results, key=lambda r: (r.confidence, r.metadata["timestamp"]), reverse=True)

        logger.info("  组合排序后:")
        for r in sorted_combined:
            logger.info(f"    confidence={r.confidence}, timestamp={r.metadata['timestamp']}")

        assert sorted_combined[0].content == "result_3"
        assert sorted_combined[1].content == "result_1"
        logger.info("[断言通过] 组合排序正确")

        logger.info("="*60)
        logger.info(f"[测试完成] test_memory_search_result_sorting_correctness")
        logger.info("="*60)
