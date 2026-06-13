# cognitive/test_cognitive/test_templates.py
import pytest
from cognitive.templates import TemplateManager


class TestTemplateManager:
    def setup_method(self):
        self.mgr = TemplateManager()

    def test_render_default_template(self):
        """默认模板应正确注入 body_status"""
        result = self.mgr.render("default", body_status="体温正常，能量充足。", task_guidance="状态良好。")
        assert "体温正常" in result
        assert "云枢" in result
        assert "状态良好" in result

    def test_render_reject_template(self):
        """拒绝模板应正确注入原因"""
        result = self.mgr.render("reject", reason="CPU 温度过高", body_status="我感觉发烧了")
        assert "CPU 温度过高" in result
        assert "我感觉发烧了" in result

    def test_render_unknown_template_raises(self):
        """未知模板名应抛出 ValueError"""
        with pytest.raises(ValueError):
            self.mgr.render("nonexistent")

    def test_register_template(self):
        """注册新模板后应能使用"""
        self.mgr.register_template("custom", "自定义模板: {msg}")
        result = self.mgr.render("custom", msg="你好")
        assert result == "自定义模板: 你好"

    def test_custom_templates_override_defaults(self):
        """构造函数传入的自定义模板应覆盖默认模板"""
        custom = {"default": "自定义默认: {body_status}"}
        mgr = TemplateManager(custom_templates=custom)
        result = mgr.render("default", body_status="测试状态")
        assert result == "自定义默认: 测试状态"
