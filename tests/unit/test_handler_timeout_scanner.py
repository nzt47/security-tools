"""超时上界静态检查器的单测（TASK-08 子工作流 D / E1j 的"检测手段"）

【为什么检查器本身也要有测试】
    这类静态检查器最危险的失败模式不是"报错"，而是**恒返回通过**
    （假绿）：它照样退出码 0，于是谁也不会注意到它其实什么都没查。
    故本文件对检查器的**每一个判定**都配一条**反例**：喂一段"有缺陷"的
    合成源码，确认检查器真的会命中。合成源码用 `ast.parse` 直接构造，
    不落盘、不碰仓库源码。

【覆盖的判据】
    · 分发层裸调检测（E1j 路径 2 的缺陷指纹）
    · handler 自身超时证据检测（B1）
    · schema 标量超时声明检测（B2）
    · 注册装饰器的识别
    · 退出码语义（0 通过 / 1 发现缺陷 / 2 检查器自身错误）
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_scanner():
    """按路径加载 `scripts/check_handler_timeouts.py`（scripts/ 不是包）

    与既有的 `tests/unit/test_settings_registry.py::_load_scanner` 同款做法：
    **先注册进 `sys.modules` 再 exec**，否则 Python 3.12 下 dataclass 装饰会因
    `sys.modules[cls.__module__] is None` 而报 AttributeError。
    """
    path = REPO_ROOT / "scripts" / "check_handler_timeouts.py"
    spec = importlib.util.spec_from_file_location("cp_check_handler_timeouts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def scanner():
    return _load_scanner()


# ════════════════════════════════════════════════════════════
#  反例：裸调检测器（必须能命中缺陷）
# ════════════════════════════════════════════════════════════

class TestBareHandlerCallDetector:
    """E1j 路径 2 的缺陷指纹：`result = tool["handler"](**params)`"""

    def test_detects_bare_call(self, scanner):
        """★反例：裸调必须被命中（改动前的缺陷形态）"""
        tree = ast.parse('def call():\n'
                         '    result = tool["handler"](**params)\n')
        assert scanner.find_bare_handler_calls(tree)

    def test_detects_bare_call_with_positional(self, scanner):
        tree = ast.parse('tool["handler"](1, 2)\n')
        assert scanner.find_bare_handler_calls(tree)

    def test_ignores_bounded_call(self, scanner):
        """正例：走有界包装的调用**不得**误报"""
        tree = ast.parse('def call():\n'
                         '    ok, out = call_with_timeout(tool["handler"], 30, kwargs=params)\n')
        assert scanner.find_bare_handler_calls(tree) == []

    def test_ignores_other_subscript_keys(self, scanner):
        """`tool["schema"]` 之类的其它键不得误报"""
        tree = ast.parse('x = tool["schema"]()\n')
        assert scanner.find_bare_handler_calls(tree) == []


# ════════════════════════════════════════════════════════════
#  handler 自身超时证据（B1）
# ════════════════════════════════════════════════════════════

class TestInternalTimeoutDetector:
    def test_detects_subprocess_timeout(self, scanner):
        tree = ast.parse('def h():\n'
                         '    return subprocess.run(["x"], timeout=30)\n')
        fn = tree.body[0]
        ok, evidence = scanner._has_internal_timeout(fn)
        assert ok and any("timeout" in e for e in evidence)

    def test_detects_join_timeout(self, scanner):
        tree = ast.parse('def h():\n    p.join(timeout=5)\n')
        assert scanner._has_internal_timeout(tree.body[0])[0] is True

    def test_detects_wait_for(self, scanner):
        tree = ast.parse('async def h():\n    await asyncio.wait_for(coro, 3)\n')
        assert scanner._has_internal_timeout(tree.body[0])[0] is True

    def test_flags_handler_without_any_timeout(self, scanner):
        """★反例：完全无超时的 handler 必须被判为"无证据" """
        tree = ast.parse('def h(**kw):\n    return do_work(kw)\n')
        ok, evidence = scanner._has_internal_timeout(tree.body[0])
        assert ok is False
        assert evidence == []


class TestDeclaredScalarTimeoutDetector:
    def test_detects_schema_timeout_param(self, scanner):
        tree = ast.parse(
            '@_tools.register("t", "d", schema={"properties": {"timeout": {}}})\n'
            'def h(**kw):\n    return 1\n')
        dec = tree.body[0].decorator_list[0]
        assert scanner._declares_scalar_timeout(dec) is True

    def test_detects_timeout_sec_param(self, scanner):
        tree = ast.parse(
            '@_tools.register("t", "d", schema={"properties": {"timeout_sec": {}}})\n'
            'def h(**kw):\n    return 1\n')
        assert scanner._declares_scalar_timeout(tree.body[0].decorator_list[0]) is True

    def test_ignores_unrelated_params(self, scanner):
        """★反例：schema 里没有标量超时 ⇒ 不得误判为有"""
        tree = ast.parse(
            '@_tools.register("t", "d", schema={"properties": {"query": {}}})\n'
            'def h(**kw):\n    return 1\n')
        assert scanner._declares_scalar_timeout(tree.body[0].decorator_list[0]) is False


class TestRegisterDecoratorDiscovery:
    def test_finds_register_decorated_handlers(self, scanner):
        tree = ast.parse(
            '@_tools.register("my_tool", "desc")\n'
            'def my_handler(**kw):\n    return 1\n')
        found = list(scanner._iter_register_decorators(tree))
        assert len(found) == 1
        name, _dec, func = found[0]
        assert name == "my_tool" and func.name == "my_handler"

    def test_finds_register_dynamic(self, scanner):
        tree = ast.parse(
            '@_tools.register_dynamic("dyn", "d")\n'
            'def h(**kw):\n    return 1\n')
        assert len(list(scanner._iter_register_decorators(tree))) == 1

    def test_ignores_non_register_decorators(self, scanner):
        tree = ast.parse('@pytest.fixture\ndef h():\n    return 1\n')
        assert list(scanner._iter_register_decorators(tree)) == []

    def test_ignores_non_literal_tool_name(self, scanner):
        """工具名不是字面量（动态拼接）时跳过，不得崩"""
        tree = ast.parse('@_tools.register(SOME_CONST)\ndef h():\n    return 1\n')
        assert list(scanner._iter_register_decorators(tree)) == []


# ════════════════════════════════════════════════════════════
#  真实仓库判定与退出码语义
# ════════════════════════════════════════════════════════════

class TestCurrentRepoInvariants:
    def test_dispatch_invariant_holds_now(self, scanner):
        """当前仓库：分发层有界、无裸调、默认上界已启用"""
        result = scanner.check_dispatch_invariant()
        assert result["ok"] is True, result["problems"]
        assert result["details"]["bare_handler_call_lines"] == []
        assert result["details"]["bounded_call_sites"] >= 1
        assert result["details"]["tool_handler_timeout_default"] > 0

    def test_scan_finds_real_handlers(self, scanner):
        """确实扫到了真实 handler（规模下限，防"扫了个空"假绿）"""
        handlers = scanner.scan_handlers()
        assert len(handlers) >= 40, f"只扫到 {len(handlers)} 个，疑似扫描失效"
        kinds = {h["bound_kind"] for h in handlers}
        assert kinds <= {"internal_bound", "declared_scalar_timeout",
                         "dispatch_bound_only"}
        assert "internal_bound" in kinds, "应至少有工具自带超时（如 shell/git）"

    def test_main_exit_code_zero_on_healthy_repo(self, scanner, capsys):
        assert scanner.main([]) == 0
        capsys.readouterr()

    def test_main_strict_exit_code_one(self, scanner, capsys):
        """★退出码语义：`--strict` 下"仅靠外层兜底"也算失败 ⇒ 非零退出

        这证明检查器**能失败**，而不是恒 0 的假绿。
        """
        rc = scanner.main(["--strict"])
        capsys.readouterr()
        assert rc == 1

    def test_main_json_mode_is_serializable(self, scanner, capsys):
        import json
        rc = scanner.main(["--json"])
        out = capsys.readouterr().out
        payload = json.loads(out)          # 不抛即通过
        assert payload["handlers_total"] >= 40
        assert "dispatch" in payload
        assert payload["ok"] is True
