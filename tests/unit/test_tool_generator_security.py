"""测试 tool_generator 超时保护——防止用户代码无限循环阻塞注册流程

两层防御：
1. AST 静态校验：拒绝顶层 while/for/if/裸表达式等危险语句
2. 子进程超时动态校验：5 秒内未完成则 terminate

使用 mock_sandbox_spawn 避免 CI Linux multiprocessing.spawn pickle 错误。
"""
import pytest

from agent.tools.tool_generator import (
    _validate_tool_code_safety,
    _exec_with_timeout,
    ToolGenEngine,
)


class TestValidateToolCodeSafety:
    """AST 静态校验：顶层仅允许函数/类定义、导入、赋值、文档字符串"""

    def test_while_loop_rejected(self):
        safe, msg = _validate_tool_code_safety("while True: pass")
        assert not safe
        assert "While" in msg

    def test_for_loop_rejected(self):
        safe, msg = _validate_tool_code_safety("for i in range(10): pass")
        assert not safe
        assert "For" in msg

    def test_if_statement_rejected(self):
        safe, msg = _validate_tool_code_safety("if True: x = 1")
        assert not safe
        assert "If" in msg

    def test_bare_expression_rejected(self):
        safe, msg = _validate_tool_code_safety('print("hello")')
        assert not safe
        assert "Expr" in msg

    def test_with_statement_rejected(self):
        safe, msg = _validate_tool_code_safety("with open('x') as f: pass")
        assert not safe

    def test_try_statement_rejected(self):
        safe, msg = _validate_tool_code_safety("try: pass\nexcept: pass")
        assert not safe

    def test_function_def_accepted(self):
        safe, _ = _validate_tool_code_safety("def foo(): return 1")
        assert safe

    def test_async_function_def_accepted(self):
        safe, _ = _validate_tool_code_safety("async def foo(): return 1")
        assert safe

    def test_class_def_accepted(self):
        safe, _ = _validate_tool_code_safety("class Bar: pass")
        assert safe

    def test_import_accepted(self):
        safe, _ = _validate_tool_code_safety("import os\nimport json")
        assert safe

    def test_from_import_accepted(self):
        safe, _ = _validate_tool_code_safety("from os import path")
        assert safe

    def test_assignment_accepted(self):
        safe, _ = _validate_tool_code_safety("x = 42")
        assert safe

    def test_annotated_assignment_accepted(self):
        safe, _ = _validate_tool_code_safety("x: int = 42")
        assert safe

    def test_docstring_accepted(self):
        safe, _ = _validate_tool_code_safety('"""模块文档"""')
        assert safe

    def test_mixed_safe_top_level_accepted(self):
        code = '''"""工具模块"""
import os
from typing import Any

DEFAULT = 42

def my_tool(**kw):
    """工具函数"""
    return {"ok": True}
'''
        safe, _ = _validate_tool_code_safety(code)
        assert safe

    def test_syntax_error_raises(self):
        with pytest.raises(SyntaxError):
            _validate_tool_code_safety("def broken(:")


class TestExecWithTimeout:
    """子进程超时动态校验"""

    @pytest.fixture(autouse=True)
    def _mock_spawn(self, mock_sandbox_spawn):
        self._spawn = mock_sandbox_spawn

    def test_normal_code_succeeds(self):
        code = "def foo(): return 42"
        ok, err = _exec_with_timeout(code, timeout_sec=5.0)
        assert ok
        assert err == ""

    def test_code_with_import_succeeds(self):
        code = "import json\ndef parse(s): return json.loads(s)"
        ok, err = _exec_with_timeout(code, timeout_sec=5.0)
        assert ok

    def test_blocking_code_times_out(self):
        self._spawn.force_timeout = True
        ok, err = _exec_with_timeout("while True: pass", timeout_sec=1.0)
        assert not ok
        assert "超时" in err

    def test_runtime_error_returns_failure(self):
        ok, err = _exec_with_timeout("x = 1 / 0", timeout_sec=5.0)
        assert not ok
        assert "ZeroDivisionError" in err

    def test_syntax_error_returns_failure(self):
        ok, err = _exec_with_timeout("def broken(:", timeout_sec=5.0)
        assert not ok
        assert "SyntaxError" in err


class TestGenerateSimpleWithTimeout:
    """generate_simple 集成测试：超时保护生效后仍保持正常功能"""

    @pytest.fixture(autouse=True)
    def _mock_spawn(self, mock_sandbox_spawn):
        self._spawn = mock_sandbox_spawn

    def test_normal_tool_registration(self):
        from agent import tools

        code = '''def greet(**kw):
    name = kw.get("name", "world")
    return {"ok": True, "message": f"Hello, {name}!"}
'''
        ok = ToolGenEngine().generate_simple("greet", "打招呼", code)
        assert ok
        try:
            result = tools.call("greet", name="测试")
            assert result["ok"]
            assert result["message"] == "Hello, 测试!"
        finally:
            tools.unregister("greet")

    def test_while_loop_rejected_before_exec(self):
        """while True: pass 应被 AST 校验拦截，不进入子进程"""
        ok = ToolGenEngine().generate_simple("bad", "死循环", "while True: pass")
        assert not ok

    def test_blocking_code_rejected_by_timeout(self):
        """AST 通过但子进程超时的代码应被拒绝"""
        self._spawn.force_timeout = True
        ok = ToolGenEngine().generate_simple("blocker", "阻塞工具", "x = 42")
        assert not ok

    def test_safe_top_level_imports_accepted(self):
        from agent import tools

        code = '''import json

def parse_json(**kw):
    """解析 JSON 字符串"""
    s = kw.get("text", "{}")
    return {"ok": True, "data": json.loads(s)}
'''
        ok = ToolGenEngine().generate_simple("parse_json", "JSON 解析", code)
        assert ok
        try:
            result = tools.call("parse_json", text='{"key": "value"}')
            assert result["ok"]
            assert result["data"]["key"] == "value"
        finally:
            tools.unregister("parse_json")

    def test_no_function_still_rejected(self):
        """无函数定义的代码仍被拒绝（保持原有行为）"""
        ok = ToolGenEngine().generate_simple("nope", "无函数", "x = 42")
        assert not ok

    def test_syntax_error_still_rejected(self):
        ok = ToolGenEngine().generate_simple("bad", "语法错误", "def broken(:")
        assert not ok
