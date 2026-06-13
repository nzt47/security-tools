"""
索引管理器测试
"""

import pytest

from agent.utils.index_manager import (
    IndexManager,
    get_global_index,
)


class TestIndexManager:
    """测试索引管理器类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_index_manager_init(self):
        """测试索引管理器初始化"""
        manager = IndexManager()
        assert manager is not None
        assert len(manager.id_to_item) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_index_item(self):
        """测试索引项目"""
        manager = IndexManager()
        
        item_id = "test_item_001"
        content = "这是测试内容，包含中文和English words"
        metadata = {"category": "test", "type": "document"}
        timestamp = "2024-01-15 10:30:00"
        
        manager.index_item(item_id, content, metadata, timestamp)
        
        assert item_id in manager.id_to_item
        assert item_id in manager.item_keywords
        assert len(manager.item_keywords[item_id]) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_remove_item(self):
        """测试移除索引项目"""
        manager = IndexManager()
        
        item_id = "test_item_001"
        content = "测试内容"
        metadata = {"category": "test"}
        timestamp = "2024-01-15 10:30:00"
        
        manager.index_item(item_id, content, metadata, timestamp)
        assert item_id in manager.id_to_item
        
        manager.remove_item(item_id)
        assert item_id not in manager.id_to_item
        assert item_id not in manager.item_keywords

    @pytest.mark.unit
    @pytest.mark.p0
    def test_remove_nonexistent_item(self):
        """测试移除不存在的项目"""
        manager = IndexManager()
        # 应该不抛出异常
        manager.remove_item("nonexistent_item")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_by_keywords(self):
        """测试关键词搜索"""
        manager = IndexManager()
        
        manager.index_item("item1", "机器学习入门教程", {}, "2024-01-15 10:00:00")
        manager.index_item("item2", "深度学习实战", {}, "2024-01-16 11:00:00")
        manager.index_item("item3", "Python编程基础", {}, "2024-01-17 12:00:00")
        
        results = manager.search_by_keywords("学习")
        assert isinstance(results, list)
        assert "item1" in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_by_keywords_empty_query(self):
        """测试空查询搜索"""
        manager = IndexManager()
        results = manager.search_by_keywords("")
        assert results == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_by_time_range(self):
        """测试时间范围搜索"""
        manager = IndexManager()
        
        manager.index_item("item1", "内容1", {}, "2024-01-15 10:00:00")
        manager.index_item("item2", "内容2", {}, "2024-01-16 11:00:00")
        manager.index_item("item3", "内容3", {}, "2024-01-17 12:00:00")
        
        results = manager.search_by_time_range("2024-01-15", "2024-01-16")
        assert isinstance(results, list)
        assert "item1" in results
        assert "item2" in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_by_category(self):
        """测试分类搜索"""
        manager = IndexManager()
        
        manager.index_item("item1", "内容1", {"category": "tech"}, "2024-01-15 10:00:00")
        manager.index_item("item2", "内容2", {"category": "tech"}, "2024-01-16 11:00:00")
        manager.index_item("item3", "内容3", {"category": "news"}, "2024-01-17 12:00:00")
        
        results = manager.search_by_category("tech")
        assert isinstance(results, list)
        assert "item1" in results
        assert "item2" in results
        assert "item3" not in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_by_type(self):
        """测试类型搜索"""
        manager = IndexManager()
        
        manager.index_item("item1", "内容1", {"type": "document"}, "2024-01-15 10:00:00")
        manager.index_item("item2", "内容2", {"type": "document"}, "2024-01-16 11:00:00")
        
        results = manager.search_by_category("document")
        assert isinstance(results, list)
        assert "item1" in results
        assert "item2" in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_item(self):
        """测试获取项目"""
        manager = IndexManager()
        
        manager.index_item("item1", "内容1", {"category": "test"}, "2024-01-15 10:00:00")
        
        item = manager.get_item("item1")
        assert item is not None
        assert item["id"] == "item1"
        assert item["content"] == "内容1"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_nonexistent_item(self):
        """测试获取不存在的项目"""
        manager = IndexManager()
        item = manager.get_item("nonexistent")
        assert item is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_clear(self):
        """测试清空索引"""
        manager = IndexManager()
        
        manager.index_item("item1", "内容1", {}, "2024-01-15 10:00:00")
        manager.index_item("item2", "内容2", {}, "2024-01-16 11:00:00")
        
        assert len(manager.id_to_item) == 2
        
        manager.clear()
        
        assert len(manager.id_to_item) == 0
        assert len(manager.keyword_index) == 0
        assert len(manager.time_index) == 0
        assert len(manager.category_index) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats(self):
        """测试获取统计信息"""
        manager = IndexManager()
        
        manager.index_item("item1", "内容1", {"category": "test"}, "2024-01-15 10:00:00")
        manager.index_item("item2", "内容2", {"category": "test"}, "2024-01-16 11:00:00")
        
        stats = manager.get_stats()
        assert isinstance(stats, dict)
        assert stats["total_items"] == 2
        assert stats["keyword_count"] > 0
        assert stats["date_count"] == 2
        assert stats["category_count"] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tokenize_chinese(self):
        """测试中文分词"""
        manager = IndexManager()
        tokens = manager._tokenize("这是中文测试")
        assert isinstance(tokens, set)
        assert len(tokens) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tokenize_english(self):
        """测试英文分词"""
        manager = IndexManager()
        tokens = manager._tokenize("This is an English test")
        assert isinstance(tokens, set)
        assert "english" in tokens
        assert "test" in tokens

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tokenize_mixed(self):
        """测试混合分词"""
        manager = IndexManager()
        tokens = manager._tokenize("中文English混合测试")
        assert isinstance(tokens, set)
        assert "english" in tokens


class TestGlobalIndex:
    """测试全局索引实例"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_global_index(self):
        """测试获取全局索引"""
        index = get_global_index()
        assert isinstance(index, IndexManager)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_global_index_singleton(self):
        """测试全局索引单例"""
        index1 = get_global_index()
        index2 = get_global_index()
        assert index1 is index2