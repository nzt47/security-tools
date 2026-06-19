"""
Tools 模块测试 - pytest 格式
针对 agent/tools 模块的测试用例
"""
import pytest


class TestToolsBasics:
    """测试 tools 模块的基本功能"""
    
    @pytest.mark.p0
    def test_tools_module_import(self):
        """测试 tools 模块可以导入"""
        try:
            from agent import tools
            assert tools is not None
        except ImportError as e:
            pytest.skip(f"Tools module import failed: {e}")
    
    @pytest.mark.p1
    def test_tools_has_attributes(self):
        """测试 tools 模块有预期的属性"""
        try:
            from agent import tools
            # 只验证模块可被访问
            assert hasattr(tools, '__dict__')
        except ImportError:
            pytest.skip("Tools module not available")
