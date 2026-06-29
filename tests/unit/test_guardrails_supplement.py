"""Guardrails 安全护栏补充测试"""
import pytest


class TestGuardrailsImport:
    """Guardrails 模块导入测试"""

    def test_import_input_guard(self):
        from agent.guardrails.input_guard import InputGuard
        assert InputGuard is not None

    def test_import_output_guard(self):
        from agent.guardrails.output_guard import OutputGuard
        assert OutputGuard is not None

    def test_input_guard_basic(self):
        from agent.guardrails.input_guard import InputGuard, GuardAction
        guard = InputGuard()
        result = guard.check("正常的用户输入")
        assert result.action == GuardAction.ALLOW

    def test_output_guard_basic(self):
        from agent.guardrails.output_guard import OutputGuard
        guard = OutputGuard()
        result = guard.check("正常的回答内容")
        assert result.modified is False
