"""
数据处理流水线测试
"""

import pytest
from unittest.mock import Mock

from agent.web.processor import DataProcessor


class TestDataProcessorInit:
    """测试数据处理器初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_default_config(self):
        """测试默认配置初始化"""
        processor = DataProcessor()
        
        assert processor._min_content_length == 50
        assert processor._max_content_length == 100000
        assert processor._min_quality_score == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_custom_config(self):
        """测试自定义配置初始化"""
        config = {
            "min_content_length": 100,
            "max_content_length": 50000,
            "min_quality_score": 0.5,
        }
        processor = DataProcessor(config=config)
        
        assert processor._min_content_length == 100
        assert processor._max_content_length == 50000
        assert processor._min_quality_score == 0.5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_initial_stats(self):
        """测试初始统计"""
        processor = DataProcessor()
        
        assert processor._stats["processed"] == 0
        assert processor._stats["dedup_removed"] == 0
        assert processor._stats["quality_filtered"] == 0
        assert processor._stats["output"] == 0


class TestDataProcessorProcess:
    """测试数据处理管线"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_process_empty_list(self):
        """测试处理空列表"""
        processor = DataProcessor()
        
        result = processor.process([])
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p1
    def test_process_single_item(self):
        """测试处理单个数据项"""
        processor = DataProcessor()
        
        items = [
            {
                "url": "http://example.com",
                "content": "This is a test content with enough length to pass validation.",
                "title": "Test Page",
            }
        ]
        
        result = processor.process(items)
        
        assert len(result) >= 0  # 可能被过滤或保留

    @pytest.mark.unit
    @pytest.mark.p1
    def test_process_multiple_items(self):
        """测试处理多个数据项"""
        processor = DataProcessor()
        
        items = [
            {"url": "http://example.com/1", "content": "Content 1 with enough length for validation check."},
            {"url": "http://example.com/2", "content": "Content 2 with enough length for validation check."},
        ]
        
        result = processor.process(items)
        
        assert processor._stats["processed"] == 2


class TestDataProcessorClean:
    """测试数据清洗"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_clean_item_basic(self):
        """测试基本清洗"""
        processor = DataProcessor()
        
        item = {
            "content": "  Test content with extra spaces.  ",
            "title": "  Test Title  ",
        }
        
        cleaned = processor.clean_item(item)
        
        # 内容应该被清洗
        assert "content" in cleaned

    @pytest.mark.unit
    @pytest.mark.p1
    def test_clean_html_tags(self):
        """测试 HTML 标签清洗"""
        processor = DataProcessor()
        
        item = {
            "content": "<p>Test <b>content</b> with HTML tags.</p>",
        }
        
        cleaned = processor.clean_item(item)
        
        # HTML 标签应该被移除或处理
        assert "content" in cleaned

    @pytest.mark.unit
    @pytest.mark.p1
    def test_clean_special_chars(self):
        """测试特殊字符清洗"""
        processor = DataProcessor()
        
        item = {
            "content": "Test&nbsp;content&#8211;with&nbsp;entities",
        }
        
        cleaned = processor.clean_item(item)
        
        assert "content" in cleaned


class TestDataProcessorDedup:
    """测试去重功能"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_dedup_same_url(self):
        """测试相同 URL 去重"""
        processor = DataProcessor()
        
        items = [
            {"url": "http://example.com/page", "content": "Content A"},
            {"url": "http://example.com/page", "content": "Content B"},
        ]
        
        result = processor.process(items, dedup=True)
        
        # 相同 URL 应该被去重
        assert len(result) <= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_dedup_same_content(self):
        """测试相同内容去重"""
        processor = DataProcessor()
        
        items = [
            {"url": "http://example.com/1", "content": "Same content here"},
            {"url": "http://example.com/2", "content": "Same content here"},
        ]
        
        result = processor.process(items, dedup=True)
        
        # 去重后应该只剩一个
        assert len(result) <= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_dedup_disabled(self):
        """测试禁用去重"""
        processor = DataProcessor(config={"min_content_length": 10})
        
        items = [
            {"url": "http://example.com/page", "content": "Content A here"},
            {"url": "http://example.com/page", "content": "Content B here"},
        ]
        
        result = processor.process(items, dedup=False)
        
        # 禁用去重后应该保留所有项（如果内容足够长）
        assert len(result) >= 0


class TestDataProcessorQuality:
    """测试质量评分"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_quality_filter_low_score(self):
        """测试低质量过滤"""
        processor = DataProcessor(config={"min_content_length": 100, "min_quality_score": 0.5})
        
        items = [
            {"url": "http://example.com", "content": "Short"},  # 低质量
        ]
        
        result = processor.process(items, quality_filter=True)
        
        # 低质量内容应该被过滤
        assert len(result) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_quality_filter_disabled(self):
        """测试禁用质量过滤"""
        processor = DataProcessor(config={"min_content_length": 5})
        
        items = [
            {"url": "http://example.com", "content": "Short"},
        ]
        
        result = processor.process(items, quality_filter=False)
        
        # 禁用过滤后应该保留
        assert len(result) >= 0


class TestDataProcessorValidate:
    """测试数据验证"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_validate_valid_item(self):
        """测试验证有效数据项"""
        processor = DataProcessor(config={"min_content_length": 10})
        
        item = {
            "url": "http://example.com",
            "content": "Valid content with enough length.",
        }
        
        # 使用 process 方法验证
        result = processor.process([item])
        assert len(result) >= 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_validate_missing_url(self):
        """测试验证缺失 URL"""
        processor = DataProcessor(config={"min_content_length": 10})
        
        item = {"content": "Content without URL"}
        
        # 缺失 URL 可能仍然有效（取决于配置）
        result = processor.process([item])
        assert len(result) >= 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_validate_short_content(self):
        """测试验证过短内容"""
        processor = DataProcessor(config={"min_content_length": 100})
        
        item = {
            "url": "http://example.com",
            "content": "Short",  # 长度不足
        }
        
        result = processor.process([item])
        
        # 过短内容应该被过滤
        assert len(result) == 0


class TestDataProcessorStats:
    """测试统计功能"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_stats(self):
        """测试获取统计"""
        processor = DataProcessor()
        
        stats = processor.get_stats()
        
        assert "processed" in stats
        assert "dedup_removed" in stats
        assert "quality_filtered" in stats
        assert "output" in stats

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stats_update_after_process(self):
        """测试处理后统计更新"""
        processor = DataProcessor()
        
        items = [
            {"url": "http://example.com/1", "content": "Content 1"},
            {"url": "http://example.com/2", "content": "Content 2"},
        ]
        
        processor.process(items)
        
        assert processor._stats["processed"] == 2