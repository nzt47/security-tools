"""MemoryManager 单元测试 - 覆盖记忆存储和检索逻辑"""
import pytest
import logging
from unittest.mock import MagicMock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_memory_manager")

class MockMemoryItem:
    def __init__(self, id_val, content, metadata=None):
        self.id = id_val
        self.content = content
        self.metadata = metadata or {}
    
    def __repr__(self):
        return f"MockMemoryItem(id={self.id}, content={self.content[:20]}...)"

class MockVectorStore:
    def __init__(self):
        self.memories = {}
    
    def add_memory(self, memory, embedding=None):
        self.memories[memory.id] = memory
    
    def get_memory(self, memory_id):
        return self.memories.get(memory_id)
    
    def search_memories(self, query, top_k=5):
        return list(self.memories.values())[:top_k]
    
    def delete_memory(self, memory_id):
        if memory_id in self.memories:
            del self.memories[memory_id]
            return True
        return False

class MemoryManager:
    def __init__(self, vector_store=None):
        self.vector_store = vector_store or MockVectorStore()
    
    def store_memory(self, content, metadata=None):
        memory_id = f"mem_{hash(content) % 1000000}"
        memory = MockMemoryItem(memory_id, content, metadata)
        self.vector_store.add_memory(memory)
        return memory_id
    
    def retrieve_memory(self, memory_id):
        memory = self.vector_store.get_memory(memory_id)
        return memory.content if memory else None
    
    def search(self, query, limit=5):
        results = self.vector_store.search_memories(query, limit)
        return [{"id": m.id, "content": m.content, "metadata": m.metadata} for m in results]
    
    def delete(self, memory_id):
        return self.vector_store.delete_memory(memory_id)

def test_store_and_retrieve_memory():
    """存储并检索记忆"""
    logger.info("测试: 存储并检索记忆")
    manager = MemoryManager()
    memory_id = manager.store_memory("测试记忆内容")
    retrieved = manager.retrieve_memory(memory_id)
    assert retrieved == "测试记忆内容"

def test_store_memory_with_metadata():
    """存储带元数据的记忆"""
    logger.info("测试: 存储带元数据的记忆")
    manager = MemoryManager()
    metadata = {"category": "test", "source": "unit_test"}
    memory_id = manager.store_memory("带元数据的记忆", metadata)
    results = manager.search("test")
    assert len(results) > 0
    assert results[0]["metadata"]["category"] == "test"

def test_retrieve_nonexistent_memory():
    """检索不存在的记忆"""
    logger.info("测试: 检索不存在的记忆")
    manager = MemoryManager()
    result = manager.retrieve_memory("nonexistent_id")
    assert result is None

def test_search_memories():
    """搜索记忆功能"""
    logger.info("测试: 搜索记忆功能")
    manager = MemoryManager()
    manager.store_memory("学习 Python", {"category": "learning"})
    manager.store_memory("学习 ML", {"category": "learning"})
    manager.store_memory("购买物品", {"category": "shopping"})
    results = manager.search("学习", limit=2)
    assert len(results) == 2

def test_delete_memory():
    """删除记忆"""
    logger.info("测试: 删除记忆")
    manager = MemoryManager()
    memory_id = manager.store_memory("待删除")
    success = manager.delete(memory_id)
    assert success is True
    assert manager.retrieve_memory(memory_id) is None

def test_memory_lifecycle():
    """记忆完整生命周期"""
    logger.info("测试: 记忆完整生命周期")
    manager = MemoryManager()
    memory_id = manager.store_memory("生命周期测试")
    assert manager.retrieve_memory(memory_id) == "生命周期测试"
    manager.delete(memory_id)
    assert manager.retrieve_memory(memory_id) is None
