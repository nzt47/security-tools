"""
Memory 模块单元测试
测试 VectorStore 和 KnowledgeBase 的完整功能
"""
import sys
import pytest
import tempfile
from unittest.mock import patch
from memory import VectorStore, MemoryItem, KnowledgeBase


@pytest.fixture(autouse=True)
def _disable_sqlite_vec_for_legacy_tests():
    """禁用 sqlite-vec 后端，让现有测试使用 JSON fallback。

    Why: sqlite-vec 后端需要加载 sentence_transformers 模型（55s+），
    会导致现有测试超时。这些测试原本就使用 JSON fallback（HAS_CHROMA=False），
    保持原有行为以避免回归。sqlite-vec 后端的测试在
    test_vector_store_sqlite_vec.py 中独立覆盖。
    """
    original = sys.modules.get('sqlite_vec')
    with patch.dict(sys.modules, {'sqlite_vec': None}):
        yield
    if original is not None:
        sys.modules['sqlite_vec'] = original


class TestMemoryItem:
    """测试 MemoryItem 数据类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_memory_item_creation(self):
        """测试 MemoryItem 创建"""
        item = MemoryItem(
            id="test_001",
            content="这是一条测试记忆",
            metadata={"type": "test", "tags": ["unit-test"]},
            timestamp="2026-05-31T12:00:00"
        )
        
        assert item.id == "test_001"
        assert item.content == "这是一条测试记忆"
        assert item.metadata == {"type": "test", "tags": ["unit-test"]}
        assert item.timestamp == "2026-05-31T12:00:00"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_memory_item_to_dict(self):
        """测试 MemoryItem to_dict 方法"""
        item = MemoryItem(
            id="test_002",
            content="测试内容",
            metadata={"key": "value"},
            timestamp="2026-05-31T12:00:00"
        )
        
        item_dict = item.to_dict()
        
        assert isinstance(item_dict, dict)
        assert item_dict["id"] == "test_002"
        assert item_dict["content"] == "测试内容"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_memory_item_from_dict(self):
        """测试 MemoryItem from_dict 方法"""
        item_dict = {
            "id": "test_003",
            "content": "反向测试内容",
            "metadata": {"test": True},
            "timestamp": "2026-05-31T12:00:00"
        }
        
        item = MemoryItem.from_dict(item_dict)
        
        assert item.id == "test_003"
        assert item.content == "反向测试内容"
        assert item.metadata == {"test": True}


class TestVectorStoreBasic:
    """测试 VectorStore 基本操作"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_initialization(self):
        """测试 VectorStore 初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_init",
                persist_dir=tmpdir
            )
            
            assert vs is not None
            assert vs.count == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_add(self):
        """测试添加记忆"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_add",
                persist_dir=tmpdir
            )
            
            item_id = vs.add("测试记忆内容", {"type": "test"})
            
            assert item_id is not None
            assert vs.count == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_multiple_adds(self):
        """测试添加多条记忆"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_multi_add",
                persist_dir=tmpdir
            )
            
            vs.add("记忆 #1", {"index": 1})
            vs.add("记忆 #2", {"index": 2})
            vs.add("记忆 #3", {"index": 3})
            
            assert vs.count == 3


class TestVectorStoreSearch:
    """测试 VectorStore 搜索功能"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_search_basic(self):
        """测试基本搜索功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_search",
                persist_dir=tmpdir
            )
            
            vs.add("Python 基础语法和数据类型", {"type": "note"})
            vs.add("Flask 快速入门指南", {"type": "tutorial"})
            vs.add("Django REST Framework 教程", {"type": "tutorial"})
            vs.add("Python 面向对象编程", {"type": "note"})
            vs.add("数据库设计和 SQL 基础", {"type": "note"})
            
            results = vs.search("Python", top_k=3)
            
            assert len(results) >= 2

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_search_no_results(self):
        """测试搜索无结果"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_search_empty",
                persist_dir=tmpdir
            )
            
            vs.add("一些内容", {"type": "test"})
            
            results = vs.search("完全不匹配的内容xyz", top_k=5)
            
            assert len(results) == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_search_with_metadata(self):
        """测试带元数据的搜索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_search_meta",
                persist_dir=tmpdir
            )
            
            vs.add("Python 教程", {"category": "programming", "level": "beginner"})
            vs.add("Flask 教程", {"category": "web", "level": "intermediate"})
            
            results = vs.search("教程", top_k=2)
            
            assert len(results) >= 1


class TestVectorStoreRecent:
    """测试获取最近记忆"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_recent_basic(self):
        """测试获取最近记忆基本功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_recent",
                persist_dir=tmpdir
            )
            
            for i in range(10):
                vs.add(f"记忆条目 #{i+1}", {"index": i})
            
            recent = vs.get_recent(limit=5)
            
            assert len(recent) == 5

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_recent_less_than_total(self):
        """测试获取的记忆少于总数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_recent_less",
                persist_dir=tmpdir
            )
            
            vs.add("记忆 #1", {})
            vs.add("记忆 #2", {})
            vs.add("记忆 #3", {})
            
            recent = vs.get_recent(limit=2)
            
            assert len(recent) == 2


class TestVectorStorePersistence:
    """测试 VectorStore 持久化功能"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_persistence(self):
        """测试 VectorStore 持久化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs1 = VectorStore(
                collection_name="test_persist",
                persist_dir=tmpdir
            )
            
            vs1.add("持久化测试记忆 #1", {"test": "persist"})
            vs1.add("持久化测试记忆 #2", {"test": "persist"})
            vs1.add("持久化测试记忆 #3", {"test": "persist"})
            
            # 重新创建实例验证持久化
            vs2 = VectorStore(
                collection_name="test_persist",
                persist_dir=tmpdir
            )
            
            assert vs2.count == 3

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_persistence_search(self):
        """测试持久化后的搜索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs1 = VectorStore(
                collection_name="test_persist_search",
                persist_dir=tmpdir
            )
            
            vs1.add("持久化搜索测试", {"test": "persist_search"})
            
            vs2 = VectorStore(
                collection_name="test_persist_search",
                persist_dir=tmpdir
            )
            
            results = vs2.search("持久化搜索测试")
            
            assert len(results) == 1


class TestVectorStoreClear:
    """测试清空记忆"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_clear(self):
        """测试清空记忆"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_clear",
                persist_dir=tmpdir
            )
            
            vs.add("记忆 #1", {"test": "clear"})
            vs.add("记忆 #2", {"test": "clear"})
            vs.add("记忆 #3", {"test": "clear"})
            
            assert vs.count == 3
            
            vs.clear()
            
            assert vs.count == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_clear_then_add(self):
        """测试清空后添加新记忆"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_clear_add",
                persist_dir=tmpdir
            )
            
            vs.add("旧记忆", {})
            vs.clear()
            vs.add("新记忆", {})
            
            assert vs.count == 1


class TestVectorStoreStats:
    """测试统计信息"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_vector_store_get_stats(self):
        """测试获取统计信息"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_stats",
                persist_dir=tmpdir
            )
            
            vs.add("统计测试 #1", {"test": "stats"})
            vs.add("统计测试 #2", {"test": "stats"})
            
            stats = vs.get_stats()
            
            assert isinstance(stats, dict)
            assert "count" in stats
            assert stats["count"] == 2


class TestKnowledgeBase:
    """测试 KnowledgeBase"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_knowledge_base_initialization(self):
        """测试 KnowledgeBase 初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            kb_store = VectorStore(
                collection_name="test_kb_init",
                persist_dir=tmpdir
            )
            kb = KnowledgeBase(store=kb_store)
            
            assert kb is not None
            assert kb.store is not None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_knowledge_base_add_document(self):
        """测试添加文档"""
        with tempfile.TemporaryDirectory() as tmpdir:
            kb_store = VectorStore(
                collection_name="test_kb_add",
                persist_dir=tmpdir
            )
            kb = KnowledgeBase(store=kb_store)
            
            kb.add_document(
                "Python 是一种高级编程语言",
                "Python官网",
                ["编程语言", "Python"]
            )
            
            assert kb.store.count == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_knowledge_base_query(self):
        """测试知识库查询"""
        with tempfile.TemporaryDirectory() as tmpdir:
            kb_store = VectorStore(
                collection_name="test_kb_query",
                persist_dir=tmpdir
            )
            kb = KnowledgeBase(store=kb_store)
            
            kb.add_document(
                "Python 是一种高级编程语言",
                "Python官网",
                ["编程语言", "Python"]
            )
            kb.add_document(
                "Flask 是一个轻量级的 Web 框架",
                "Flask文档",
                ["Web开发", "Flask"]
            )
            
            result = kb.query("Python")
            
            assert result is not None
            assert len(result) > 0


class TestVectorStoreEmptyHandling:
    """测试空输入处理"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_add_empty_content(self):
        """测试添加空内容"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_empty_add",
                persist_dir=tmpdir
            )
            
            item_id = vs.add("")
            
            assert item_id is not None
            assert vs.count == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_search_empty_string(self):
        """测试搜索空字符串"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vs = VectorStore(
                collection_name="test_empty_search",
                persist_dir=tmpdir
            )
            
            vs.add("一些内容", {})
            
            results = vs.search("")
            
            # 搜索空字符串可能返回所有结果或无结果
            assert isinstance(results, list)
