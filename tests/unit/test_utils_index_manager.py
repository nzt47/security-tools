"""Index Manager 简化单元测试"""
import pytest

from agent.utils.index_manager import IndexManager


class TestIndexManagerInit:
    """测试索引管理器初始化"""

    def test_init_basic(self):
        """测试基本初始化"""
        manager = IndexManager()
        
        assert isinstance(manager.keyword_index, dict)
        assert isinstance(manager.time_index, dict)
        assert isinstance(manager.category_index, dict)
        assert isinstance(manager.id_to_item, dict)
        assert isinstance(manager.item_keywords, dict)

    def test_init_empty_indices(self):
        """测试初始状态为空"""
        manager = IndexManager()
        
        assert len(manager.keyword_index) == 0
        assert len(manager.time_index) == 0
        assert len(manager.category_index) == 0
        assert len(manager.id_to_item) == 0


class TestTokenization:
    """测试分词功能"""

    def test_tokenize_english(self):
        """测试英文分词"""
        manager = IndexManager()
        
        tokens = manager._tokenize("Hello World Test")
        
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_tokenize_chinese(self):
        """测试中文分词"""
        manager = IndexManager()
        
        tokens = manager._tokenize("你好世界测试")
        
        # 验证中文分词结果
        assert len(tokens) > 0

    def test_tokenize_mixed(self):
        """测试混合分词"""
        manager = IndexManager()
        
        tokens = manager._tokenize("Hello 你好 World 世界")
        
        assert "hello" in tokens
        assert "world" in tokens
        assert len(tokens) > 2


class TestItemManagement:
    """测试条目管理"""

    def test_add_and_get_item(self):
        """测试添加和获取条目"""
        manager = IndexManager()
        
        # 直接添加到 id_to_item
        item = {"id": "item1", "content": "Test content"}
        manager.id_to_item["item1"] = item
        
        result = manager.get_item("item1")
        
        assert result is not None
        assert result["id"] == "item1"

    def test_get_nonexistent_item(self):
        """测试获取不存在的条目"""
        manager = IndexManager()
        
        result = manager.get_item("nonexistent")
        
        assert result is None


class TestStats:
    """测试统计信息"""

    def test_get_stats(self):
        """测试获取统计信息"""
        manager = IndexManager()
        
        stats = manager.get_stats()
        
        assert isinstance(stats, dict)


class TestManagement:
    """测试管理功能"""

    def test_clear_all(self):
        """测试清空所有索引"""
        manager = IndexManager()
        
        manager.id_to_item["item1"] = {"id": "item1"}
        manager.keyword_index["test"].add("item1")
        
        manager.clear()
        
        assert len(manager.id_to_item) == 0
        assert len(manager.keyword_index) == 0


class TestIndexItem:
    """测试索引项功能"""

    def test_index_item_basic(self):
        """测试基本索引项"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Hello World Test",
            metadata={"category": "test"},
            timestamp="2024-01-01T00:00:00"
        )
        
        assert "item1" in manager.id_to_item
        assert manager.get_item("item1")["content"] == "Hello World Test"

    def test_index_item_multiple_keywords(self):
        """测试索引多个关键词"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Python programming language",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        # 检查关键词索引
        assert "python" in manager.keyword_index
        assert "programming" in manager.keyword_index
        assert "language" in manager.keyword_index

    def test_index_item_time_index(self):
        """测试时间索引"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Test content",
            metadata={},
            timestamp="2024-01-15T10:30:00"
        )
        
        assert "2024-01-15" in manager.time_index
        assert "item1" in manager.time_index["2024-01-15"]

    def test_index_item_category_index(self):
        """测试分类索引"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Test content",
            metadata={"category": "technology"},
            timestamp="2024-01-01T00:00:00"
        )
        
        assert "technology" in manager.category_index
        assert "item1" in manager.category_index["technology"]


class TestRemoveItem:
    """测试移除项"""

    def test_remove_item(self):
        """测试移除索引项"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Hello World",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        manager.remove_item("item1")
        
        assert "item1" not in manager.id_to_item
        assert manager.get_item("item1") is None

    def test_remove_nonexistent_item(self):
        """测试移除不存在的项"""
        manager = IndexManager()
        
        # 不应抛出异常
        manager.remove_item("nonexistent")


class TestSearchByKeywords:
    """测试关键词搜索"""

    def test_search_by_keywords_basic(self):
        """测试基本关键词搜索"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Python programming tutorial",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        manager.index_item(
            item_id="item2",
            content="JavaScript web development",
            metadata={},
            timestamp="2024-01-02T00:00:00"
        )
        
        results = manager.search_by_keywords("Python")
        
        assert "item1" in results
        assert "item2" not in results

    def test_search_by_keywords_multiple(self):
        """测试多关键词搜索"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Python programming tutorial",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        manager.index_item(
            item_id="item2",
            content="Python web development",
            metadata={},
            timestamp="2024-01-02T00:00:00"
        )
        
        results = manager.search_by_keywords("Python tutorial")
        
        # item1 匹配两个词，item2 只匹配一个
        assert results.index("item1") < results.index("item2")

    def test_search_by_keywords_none(self):
        """测试无匹配结果"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Python programming",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        results = manager.search_by_keywords("JavaScript")
        
        assert len(results) == 0


class TestSearchByTimeRange:
    """测试时间范围搜索"""

    def test_search_by_time_range(self):
        """测试时间范围搜索"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Test 1",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        manager.index_item(
            item_id="item2",
            content="Test 2",
            metadata={},
            timestamp="2024-01-15T00:00:00"
        )
        manager.index_item(
            item_id="item3",
            content="Test 3",
            metadata={},
            timestamp="2024-02-01T00:00:00"
        )
        
        results = manager.search_by_time_range("2024-01-01", "2024-01-31")
        
        assert "item1" in results
        assert "item2" in results
        assert "item3" not in results

    def test_search_by_time_range_no_match(self):
        """测试时间范围无匹配"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Test 1",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        results = manager.search_by_time_range("2024-06-01", "2024-06-30")
        
        assert len(results) == 0


class TestSearchByCategory:
    """测试分类搜索"""

    def test_search_by_category(self):
        """测试分类搜索"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="Tech article",
            metadata={"category": "technology"},
            timestamp="2024-01-01T00:00:00"
        )
        manager.index_item(
            item_id="item2",
            content="News article",
            metadata={"category": "news"},
            timestamp="2024-01-02T00:00:00"
        )
        
        results = manager.search_by_category("technology")
        
        assert "item1" in results
        assert "item2" not in results

    def test_search_by_category_none(self):
        """测试分类无结果"""
        manager = IndexManager()
        
        results = manager.search_by_category("nonexistent")
        
        assert len(results) == 0


class TestGetGlobalIndex:
    """测试全局索引"""

    def test_get_global_index(self):
        """测试获取全局索引实例"""
        from agent.utils.index_manager import get_global_index
        
        index1 = get_global_index()
        index2 = get_global_index()
        
        # 应该是单例
        assert index1 is index2


class TestIndexItemTypeMetadata:
    """测试索引项的 type 元数据"""

    def test_index_item_with_type_metadata(self):
        """测试包含 type 元数据的索引项"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item_with_type",
            content="测试内容",
            metadata={"type": "test_type"},
            timestamp="2024-01-01T00:00:00"
        )
        
        # 应该可以通过 type 搜索
        result = manager.search_by_category("test_type")
        assert "item_with_type" in result


class TestRemoveItemWithMetadata:
    """测试移除包含 category 和 type 元数据的项"""

    def test_remove_item_with_category_and_type(self):
        """测试移除包含 category 和 type 元数据的项"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item_to_remove",
            content="测试内容",
            metadata={"category": "test_category", "type": "test_type"},
            timestamp="2024-01-01T00:00:00"
        )
        
        # 确认存在
        assert "item_to_remove" in manager.id_to_item
        assert "item_to_remove" in manager.search_by_category("test_category")
        assert "item_to_remove" in manager.search_by_category("test_type")
        
        # 移除
        manager.remove_item("item_to_remove")
        
        # 确认已移除
        assert "item_to_remove" not in manager.id_to_item


class TestSearchEdgeCases:
    """测试搜索的边界情况"""

    def test_search_by_keywords_empty_query(self):
        """测试空关键词搜索"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="测试内容",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        # 空查询应返回空列表
        result = manager.search_by_keywords("")
        assert result == []
        
        # 只有空格的查询也应返回空
        result = manager.search_by_keywords("   ")
        assert result == []

    def test_search_by_time_range_invalid_date_format(self):
        """测试无效日期格式的搜索"""
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="测试内容",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        # 无效日期格式应返回空而不是崩溃
        result = manager.search_by_time_range("invalid_date", "2024-12-31")
        # 只要不崩溃就算通过
        assert isinstance(result, list)
    
    def test_search_by_time_range_datetime_import_error(self):
        """测试日期处理时的异常处理"""
        import unittest.mock
        manager = IndexManager()
        
        manager.index_item(
            item_id="item1",
            content="测试内容",
            metadata={},
            timestamp="2024-01-01T00:00:00"
        )
        
        # 模拟datetime.strptime抛出异常
        with unittest.mock.patch('agent.utils.index_manager.datetime') as mock_datetime:
            mock_datetime.strptime.side_effect = Exception("Test exception")
            
            # 这应该触发异常分支
            result = manager.search_by_time_range("2024-01-01", "2024-12-31")
            assert isinstance(result, list)
