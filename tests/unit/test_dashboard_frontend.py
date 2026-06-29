#!/usr/bin/env python3
"""
仪表盘前端单元测试

测试覆盖：
1. HTML 结构验证
2. JavaScript 功能测试
3. API 调用模拟测试
4. 响应式设计验证
"""

import pytest
import re
from pathlib import Path


class TestDashboardHtmlStructure:
    """Dashboard HTML 结构测试"""
    
    def setup_method(self):
        self.html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        assert self.html_path.exists(), f"仪表盘文件不存在: {self.html_path}"
        with open(self.html_path, "r", encoding="utf-8") as f:
            self.html_content = f.read()
    
    def test_html_structure_has_doctype(self):
        """测试 HTML 文档有正确的 DOCTYPE"""
        assert self.html_content.startswith("<!DOCTYPE html>")
    
    def test_html_has_required_meta_tags(self):
        """测试 HTML 包含必需的 meta 标签"""
        assert '<meta charset="UTF-8">' in self.html_content
        assert '<meta name="viewport"' in self.html_content
    
    def test_html_has_main_container(self):
        """测试 HTML 包含主容器"""
        assert 'class="dashboard-container"' in self.html_content
    
    def test_html_has_all_tabs(self):
        """测试 HTML 包含所有标签页"""
        assert 'switchTab(' in self.html_content
        assert 'quality' in self.html_content
        assert 'traces' in self.html_content
        assert 'memory' in self.html_content
    
    def test_html_has_chart_containers(self):
        """测试 HTML 包含所有图表容器"""
        charts = [
            "schemaTrendChart",
            "criticTrendChart", 
            "failurePieChart",
            "traceFlowChart",
            "longTermTrendChart",
            "memoryPieChart",
            "hitRateTrendChart"
        ]
        for chart in charts:
            assert f'id="{chart}"' in self.html_content
    
    def test_html_has_echarts_script(self):
        """测试 HTML 引入了 ECharts 库"""
        assert 'echarts.min.js' in self.html_content
    
    def test_html_has_time_range_selector(self):
        """测试 HTML 包含时间范围选择器"""
        time_ranges = ["today", "week", "month"]
        for range_name in time_ranges:
            assert f'changeTimeRange(\'{range_name}\')' in self.html_content
    
    def test_html_has_search_boxes(self):
        """测试 HTML 包含搜索框"""
        assert 'id="traceSearch"' in self.html_content
        assert 'id="memorySearch"' in self.html_content


class TestDashboardJavaScript:
    """Dashboard JavaScript 功能测试"""
    
    def setup_method(self):
        self.html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        with open(self.html_path, "r", encoding="utf-8") as f:
            self.html_content = f.read()
    
    def test_js_has_init_charts_function(self):
        """测试 JavaScript 包含图表初始化函数"""
        assert 'function initCharts()' in self.html_content
    
    def test_js_has_tab_switch_function(self):
        """测试 JavaScript 包含标签切换函数"""
        assert 'function switchTab(tab)' in self.html_content
    
    def test_js_has_data_loading_functions(self):
        """测试 JavaScript 包含数据加载函数"""
        functions = [
            'loadQualityData',
            'loadTraces',
            'loadMemoryData',
            'loadTraceDetail'
        ]
        for func in functions:
            assert f'function {func}' in self.html_content or f'async function {func}' in self.html_content
    
    def test_js_has_chart_update_functions(self):
        """测试 JavaScript 包含图表更新函数"""
        functions = [
            'updateSchemaTrendChart',
            'updateCriticTrendChart', 
            'updateFailurePieChart',
            'updateTraceFlowChart',
            'updateLongTermTrendChart',
            'updateMemoryPieChart',
            'updateHitRateTrendChart'
        ]
        for func in functions:
            assert f'function {func}' in self.html_content
    
    def test_js_has_format_time_function(self):
        """测试 JavaScript 包含时间格式化函数"""
        assert 'function formatTime(timestamp)' in self.html_content
    
    def test_js_has_refresh_function(self):
        """测试 JavaScript 包含刷新函数"""
        assert 'function refreshAllData()' in self.html_content
    
    def test_js_has_error_handling(self):
        """测试 JavaScript 包含错误处理"""
        assert 'try {' in self.html_content
        assert 'catch (error)' in self.html_content
        assert 'console.error' in self.html_content
    
    def test_js_has_event_listeners(self):
        """测试 JavaScript 包含事件监听器"""
        assert 'addEventListener' in self.html_content
        assert 'DOMContentLoaded' in self.html_content
        assert 'resize' in self.html_content


class TestDashboardApiEndpoints:
    """Dashboard API 端点测试"""
    
    def test_api_endpoints_are_called(self):
        """测试 JavaScript 调用正确的 API 端点"""
        html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        endpoints = [
            '/api/dashboard/quality',
            '/api/dashboard/traces',
            '/api/dashboard/memory'
        ]
        for endpoint in endpoints:
            assert endpoint in content
    
    def test_api_calls_have_proper_headers(self):
        """测试 API 调用包含正确的请求头"""
        html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert 'Authorization' in content
        assert 'Bearer' in content


class TestDashboardResponsiveDesign:
    """响应式设计测试"""
    
    def test_responsive_media_queries(self):
        """测试包含响应式媒体查询"""
        html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 检查媒体查询
        assert '@media' in content
        assert 'max-width: 768px' in content
        assert 'max-width: 480px' in content
    
    def test_responsive_grid_classes(self):
        """测试包含响应式网格类"""
        html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert 'grid-template-columns' in content
        assert 'auto-fit' in content
        assert 'minmax' in content


class TestDashboardDataValidation:
    """数据验证测试"""
    
    def test_format_time_function_output(self):
        """测试时间格式化函数输出格式"""
        # 这是一个简化的测试，验证 JavaScript 代码中的格式化逻辑
        html_path = Path(__file__).parent.parent.parent / "templates" / "dashboard.html"
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 检查函数是否正确使用 toLocaleString
        assert 'toLocaleString' in content
        assert 'zh-CN' in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
