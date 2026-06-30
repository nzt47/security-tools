"""
工具调用模块重构单元测试
覆盖 tool_calling.py、tools/__init__.py 的核心功能
"""
import json
import pytest
import logging
from unittest.mock import MagicMock, patch
from datetime import datetime, date

logger = logging.getLogger(__name__)


# ============================================================================
#  一、_clean_for_json 函数测试
# ============================================================================


class TestCleanForJson:
    """测试 _clean_for_json 函数 - 各种数据类型的 JSON 序列化清理"""

    def test_clean_for_json_bytes_utf8(self):
        """测试 bytes 类型（UTF-8 编码）转换为字符串"""
        from agent.tool_calling import _clean_for_json
        result = _clean_for_json(b"hello world")
        assert result == "hello world"
        assert isinstance(result, str)

    def test_clean_for_json_bytes_non_utf8(self):
        """测试 bytes 类型（含非 UTF-8 字符）的容错处理"""
        from agent.tool_calling import _clean_for_json
        result = _clean_for_json(b"hello \xff\xfe world")
        assert isinstance(result, str)
        assert "hello" in result

    def test_clean_for_json_dict_basic(self):
        """测试 dict 类型的递归清理"""
        from agent.tool_calling import _clean_for_json
        data = {"key1": b"value1", "key2": "value2"}
        result = _clean_for_json(data)
        assert result["key1"] == "value1"
        assert result["key2"] == "value2"

    def test_clean_for_json_list(self):
        """测试 list 类型的递归清理"""
        from agent.tool_calling import _clean_for_json
        result = _clean_for_json([b"a", b"b", "c"])
        assert result == ["a", "b", "c"]

    def test_clean_for_json_tuple(self):
        """测试 tuple 类型转换为 list"""
        from agent.tool_calling import _clean_for_json
        result = _clean_for_json((b"a", b"b"))
        assert isinstance(result, list)
        assert result == ["a", "b"]

    def test_clean_for_json_set(self):
        """测试 set 类型转换为 list"""
        from agent.tool_calling import _clean_for_json
        result = _clean_for_json({1, 2, 3})
        assert isinstance(result, list)
        assert len(result) == 3

    def test_clean_for_json_frozenset(self):
        """测试 frozenset 类型转换为 list"""
        from agent.tool_calling import _clean_for_json
        result = _clean_for_json(frozenset([1, 2, 3]))
        assert isinstance(result, list)
        assert len(result) == 3

    def test_clean_for_json_basic_types(self):
        """测试基本类型（int/float/str/bool/None）直接返回"""
        from agent.tool_calling import _clean_for_json
        assert _clean_for_json(42) == 42
        assert _clean_for_json(3.14) == 3.14
        assert _clean_for_json("hello") == "hello"
        assert _clean_for_json(True) is True
        assert _clean_for_json(False) is False
        assert _clean_for_json(None) is None

    def test_clean_for_json_datetime(self):
        """测试 datetime 类型通过 isoformat 转换"""
        from agent.tool_calling import _clean_for_json
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = _clean_for_json(dt)
        assert result == "2024-01-15T10:30:00"

    def test_clean_for_json_date(self):
        """测试 date 类型通过 isoformat 转换"""
        from agent.tool_calling import _clean_for_json
        d = date(2024, 1, 15)
        result = _clean_for_json(d)
        assert result == "2024-01-15"

    def test_clean_for_json_nested_structure(self):
        """测试嵌套结构的递归清理"""
        from agent.tool_calling import _clean_for_json
        data = {
            "level1": {
                "level2": [
                    b"bytes_item",
                    {"deep_key": b"deep_value"}
                ]
            }
        }
        result = _clean_for_json(data)
        assert result["level1"]["level2"][0] == "bytes_item"
        assert result["level1"]["level2"][1]["deep_key"] == "deep_value"

    def test_clean_for_json_circular_reference(self):
        """测试循环引用检测与处理"""
        from agent.tool_calling import _clean_for_json
        data = {"a": 1}
        data["self"] = data
        result = _clean_for_json(data)
        assert result["a"] == 1
        assert result["self"] == "<循环引用>"

    def test_clean_for_json_output_is_json_serializable(self):
        """测试清理后的结果可以被 json.dumps 序列化"""
        from agent.tool_calling import _clean_for_json
        data = {
            "bytes": b"hello",
            "list": [b"a", 1, True],
            "nested": {"key": b"value"},
            "set": {1, 2, 3},
        }
        cleaned = _clean_for_json(data)
        json_str = json.dumps(cleaned, ensure_ascii=False)
        assert isinstance(json_str, str)


# ============================================================================
#  二、ToolCallingService 初始化测试
# ============================================================================


class TestToolCallingServiceInit:
    """测试 ToolCallingService 初始化"""

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_init_default_params(self, mock_rate_limiter, mock_circuit_breaker):
        """测试使用默认参数初始化"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "test-model"

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(llm_service=mock_llm)

        assert service._primary_llm is mock_llm
        assert service._upgrade_llm is None
        assert service._model_upgraded is False
        assert service._max_rounds == 5
        assert service._tool_timeout == 120
        assert service._task_timeout == 600
        assert service.last_steps == []
        assert service._abort_event is not None
        assert service._timeout_event is not None

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_init_custom_params(self, mock_rate_limiter, mock_circuit_breaker):
        """测试使用自定义参数初始化"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "test-model"

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(
            llm_service=mock_llm,
            max_rounds=10,
            tool_timeout=60,
            task_timeout=300,
        )

        assert service._max_rounds == 10
        assert service._tool_timeout == 60
        assert service._task_timeout == 300

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_init_with_model_router(self, mock_rate_limiter, mock_circuit_breaker):
        """测试传入 model_router 初始化"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_router = MagicMock()

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(
            llm_service=mock_llm,
            model_router=mock_router,
        )

        assert service._model_router is mock_router

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_current_llm_property_primary(self, mock_rate_limiter, mock_circuit_breaker):
        """测试 _current_llm 属性 - 未升级时返回主 LLM"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "primary-model"

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(llm_service=mock_llm)

        assert service._current_llm is mock_llm

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_current_llm_property_upgraded(self, mock_rate_limiter, mock_circuit_breaker):
        """测试 _current_llm 属性 - 升级后返回升级 LLM"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "primary-model"
        mock_upgraded = MagicMock()
        mock_upgraded.model = "upgraded-model"

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(llm_service=mock_llm)
        service._upgrade_llm = mock_upgraded
        service._model_upgraded = True

        assert service._current_llm is mock_upgraded


# ============================================================================
#  三、abort 方法测试
# ============================================================================


class TestAbortMethod:
    """测试 abort 中止方法"""

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_abort_sets_event(self, mock_rate_limiter, mock_circuit_breaker):
        """测试 abort 方法设置中止事件标志"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "test-model"

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(llm_service=mock_llm)

        assert service._abort_event.is_set() is False
        service.abort()
        assert service._abort_event.is_set() is True

    @patch('agent.tool_calling.get_circuit_breaker')
    @patch('agent.tool_calling.get_rate_limiter')
    def test_abort_idempotent(self, mock_rate_limiter, mock_circuit_breaker):
        """测试多次调用 abort 不会出错（幂等性）"""
        mock_circuit_breaker.return_value = MagicMock()
        mock_rate_limiter.return_value = MagicMock()
        mock_llm = MagicMock()
        mock_llm.model = "test-model"

        from agent.tool_calling import ToolCallingService
        service = ToolCallingService(llm_service=mock_llm)

        service.abort()
        service.abort()
        service.abort()
        assert service._abort_event.is_set() is True


# ============================================================================
#  四、tools/__init__.py - 注册与注销测试
# ============================================================================


class TestToolsRegister:
    """测试工具注册与注销功能"""

    def setup_method(self):
        """每个测试前保存原始注册表状态"""
        from agent.tools import clear, _registry
        self._original_registry = dict(_registry)
        clear()

    def teardown_method(self):
        """每个测试后恢复原始注册表状态"""
        from agent.tools import clear, _registry
        clear()
        _registry.update(self._original_registry)

    def test_register_decorator_mode(self):
        """测试 register 作为装饰器使用模式"""
        from agent.tools import register, call, _registry, clear

        clear()

        @register("decorator_tool", category="test")
        def my_tool(**params):
            return {"result": "decorated"}

        assert "decorator_tool" in _registry
        tool_info = _registry["decorator_tool"]
        assert tool_info["name"] == "decorator_tool"

        result = call("decorator_tool")
        assert result["result"] == "decorated"

        clear()

    def test_unregister_existing_tool(self):
        """测试注销已存在的工具"""
        from agent.tools import register, unregister, _registry, clear

        clear()

        @register("to_remove", category="test")
        def mock_tool(**params):
            return {"ok": True}

        assert "to_remove" in _registry
        unregister("to_remove")
        assert "to_remove" not in _registry

        clear()

    def test_unregister_nonexistent_tool(self):
        """测试注销不存在的工具不会报错"""
        from agent.tools import unregister, _registry, clear

        clear()
        count_before = len(_registry)
        unregister("nonexistent_tool_xyz")
        assert len(_registry) == count_before

    def test_clear_all_tools(self):
        """测试 clear 函数清空所有工具"""
        from agent.tools import register, clear, _registry

        clear()

        @register("tool_a", category="test")
        def tool_a(**params): return {}

        @register("tool_b", category="test")
        def tool_b(**params): return {}

        assert len(_registry) >= 2
        clear()
        assert len(_registry) == 0


# ============================================================================
#  五、tools/__init__.py - 基础设施测试
# ============================================================================


class TestToolsInfra:
    """测试工具基础设施：健康状态、工具列表等"""

    def setup_method(self):
        """每个测试前清空工具注册表"""
        from agent.tools import clear
        clear()

    def teardown_method(self):
        """每个测试后恢复"""
        from agent.tools import clear
        clear()

    def test_health_status_tracking(self):
        """测试工具健康状态跟踪功能"""
        from agent.tools import register, call, _tool_health, clear

        clear()

        @register("health_test_tool", category="test")
        def health_test_tool(**params):
            return {"ok": True}

        call("health_test_tool")

        assert "health_test_tool" in _tool_health
        health_info = _tool_health["health_test_tool"]
        assert "call_count" in health_info
        assert "last_ok" in health_info

        clear()

    def test_list_tools_function(self):
        """测试 list_tools 函数列出工具"""
        from agent.tools import register, list_tools, clear
        clear()

        @register("list_tool_1", category="category1")
        def tool_a(**params): return {}

        @register("list_tool_2", category="category2")
        def tool_b(**params): return {}

        all_tools = list_tools()
        assert len(all_tools) >= 2

        clear()

    def test_get_tool_defs_function(self):
        """测试 get_tool_defs 函数获取工具定义"""
        from agent.tools import register, get_tool_defs, clear
        clear()

        @register("defs_tool", category="test", schema={"type": "object"})
        def defs_tool(**params): return {}

        tool_defs = get_tool_defs()
        assert len(tool_defs) >= 1
        # 工具定义可能使用 name 或 function 等字段
        found = False
        for t in tool_defs:
            if isinstance(t, dict):
                if t.get("name") == "defs_tool":
                    found = True
                    break
                if t.get("function", {}).get("name") == "defs_tool":
                    found = True
                    break
        assert found, f"未找到 defs_tool，工具列表: {tool_defs[:2]}"

        clear()

    def test_tool_error_exception(self):
        """测试 ToolError 异常类"""
        from agent.tools import ToolError

        error = ToolError("test error message")
        assert str(error) == "test error message"
        assert isinstance(error, Exception)


# ============================================================================
#  六、ToolCallError 异常测试
# ============================================================================


class TestToolCallError:
    """测试 ToolCallError 异常类"""

    def test_tool_call_error_inheritance(self):
        """测试 ToolCallError 继承自 Exception"""
        from agent.tool_calling import ToolCallError

        error = ToolCallError("test error")
        assert isinstance(error, Exception)
        assert str(error) == "test error"

    def test_tool_call_error_with_context(self):
        """测试 ToolCallError 携带上下文信息"""
        from agent.tool_calling import ToolCallError

        error = ToolCallError("tool failed: test_tool")
        assert isinstance(error, Exception)
        assert "tool failed" in str(error)
