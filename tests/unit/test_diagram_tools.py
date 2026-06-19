import pytest
import os
from agent import diagram_tools


class TestDiagramTools:
    """图表工具测试"""

    def test_generate_architecture_diagram_empty_title(self, tmp_path):
        """测试空标题应报错"""
        components = [{"name": "Test", "type": "frontend"}]
        output_path = str(tmp_path / "test.html")
        
        result = diagram_tools.generate_architecture_diagram("", components, output_path)
        assert result["ok"] is False
        assert "标题不能为空" in result["error"]

    def test_generate_architecture_diagram_empty_components(self, tmp_path):
        """测试空组件列表应报错"""
        output_path = str(tmp_path / "test.html")
        
        result = diagram_tools.generate_architecture_diagram("Test", [], output_path)
        assert result["ok"] is False
        assert "组件列表不能为空" in result["error"]

    def test_generate_architecture_diagram_empty_output_path(self):
        """测试空输出路径应报错"""
        components = [{"name": "Test", "type": "frontend"}]
        
        result = diagram_tools.generate_architecture_diagram("Test", components, "")
        assert result["ok"] is False
        assert "输出路径不能为空" in result["error"]

    def test_generate_architecture_diagram_success(self, tmp_path):
        """测试成功生成架构图"""
        components = [
            {"name": "Frontend", "type": "frontend", "description": "Web UI"},
            {"name": "Backend", "type": "backend", "description": "API Service"},
            {"name": "Database", "type": "database", "description": "PostgreSQL"},
        ]
        output_path = str(tmp_path / "architecture.html")
        
        result = diagram_tools.generate_architecture_diagram("Test System", components, output_path)
        assert result["ok"] is True
        assert result["path"] == output_path
        assert result["component_count"] == 3
        assert os.path.exists(output_path)

    def test_generate_architecture_diagram_with_all_types(self, tmp_path):
        """测试使用所有组件类型"""
        components = [
            {"name": "Frontend", "type": "frontend"},
            {"name": "Backend", "type": "backend"},
            {"name": "Database", "type": "database"},
            {"name": "Cloud", "type": "cloud"},
            {"name": "Security", "type": "security"},
            {"name": "External", "type": "external"},
        ]
        output_path = str(tmp_path / "all_types.html")
        
        result = diagram_tools.generate_architecture_diagram("All Types", components, output_path)
        assert result["ok"] is True
        assert os.path.exists(output_path)

    def test_get_color_default(self):
        """测试获取默认颜色配置"""
        colors = diagram_tools._get_color("unknown_type")
        assert colors == diagram_tools.TYPE_COLORS["external"]

    def test_get_color_by_type(self):
        """测试按类型获取颜色配置"""
        frontend_colors = diagram_tools._get_color("frontend")
        assert frontend_colors["stroke"] == "#22d3ee"
        
        backend_colors = diagram_tools._get_color("backend")
        assert backend_colors["stroke"] == "#34d399"
        
        database_colors = diagram_tools._get_color("database")
        assert database_colors["stroke"] == "#a78bfa"

    def test_escape_xml(self):
        """测试 XML 转义"""
        text = 'Test & "quoted" <tag> &amp;'
        escaped = diagram_tools._escape_xml(text)
        
        assert "&amp;" in escaped
        assert "&quot;" in escaped
        assert "&lt;" in escaped
        assert "&gt;" in escaped

    def test_render_svg_components(self):
        """测试 SVG 组件渲染"""
        components = [
            {"name": "Test", "type": "frontend", "description": "Desc"}
        ]
        svg = diagram_tools._render_svg_components(components)
        
        assert "<rect" in svg
        assert "<text" in svg
        assert "Test" in svg

    def test_render_legend(self):
        """测试图例渲染"""
        legend = diagram_tools._render_legend()
        
        assert "<text" in legend
        assert "<rect" in legend
        assert "Legend" in legend

    def test_generate_with_long_names(self, tmp_path):
        """测试长名称组件"""
        components = [
            {"name": "A very long component name that should be truncated", "type": "frontend"},
        ]
        output_path = str(tmp_path / "long_name.html")
        
        result = diagram_tools.generate_architecture_diagram("Long Names", components, output_path)
        assert result["ok"] is True
        assert os.path.exists(output_path)

    def test_generate_with_special_characters(self, tmp_path):
        """测试特殊字符处理"""
        components = [
            {"name": "Component & Special <Chars>", "type": "frontend", "description": 'Description with "quotes"'},
        ]
        output_path = str(tmp_path / "special_chars.html")
        
        result = diagram_tools.generate_architecture_diagram("Special Chars", components, output_path)
        assert result["ok"] is True
        assert os.path.exists(output_path)