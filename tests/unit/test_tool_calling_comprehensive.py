"""ToolCallingService 综合单元测试

覆盖模块: agent/tool_calling.py
测试维度:
  - 辅助函数 (_clean_for_json, _trace_id, _summarize_tool_result)
  - ToolCallError 异常类
  - 静态方法 (_extract_tool_calls, _extract_text, _extract_reasoning,
            _get_last_assistant_text, _summarize_tool_steps,
            _extract_xml_tool_calls, _truncate_tool_content)
  - 初始化 (配置回退/参数注入)
  - 中止与超时 (abort, task_timeout)
  - chat_with_steps 全流程 (纯文本/工具调用/XML 工具/降级/失败重试)
  - _execute_safe (成功/失败/异常)
  - _call_llm_openai / _call_llm_anthropic
  - _anthropic_to_openai 格式转换
设计原则: AAA (Arrange-Act-Assert), Mock 外部依赖 (LLM 客户端/tools 模块)
"""

import datetime
import json
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from agent import tool_calling
from agent.tool_calling import (
    ToolCallError,
    ToolCallingService,
    _clean_for_json,
    _summarize_tool_result,
)


# ═══════════════════════════════════════════════════════════════
# 测试夹具
# ═══════════════════════════════════════════════════════════════


class FakeLLM:
    """模拟 LLM 服务"""

    def __init__(self, model="test-model", responses=None, raise_on_call=False,
                 is_openai=True, chat_text="模拟文本"):
        self.model = model
        self._responses = responses or []
        self._call_idx = 0
        self._raise_on_call = raise_on_call
        self._is_openai_flag = is_openai
        self._chat_text = chat_text
        self._client = SimpleNamespace()

    def _get_client(self):
        return self._client

    def _is_openai_compat(self):
        return self._is_openai_flag

    def chat(self, messages, system_prompt="", max_tokens=8192, temperature=0.7):
        if self._raise_on_call:
            raise RuntimeError("LLM chat 失败")
        return self._chat_text

    def next_response(self):
        """返回下一个预设响应"""
        if self._call_idx < len(self._responses):
            r = self._responses[self._call_idx]
            self._call_idx += 1
            return r
        if self._responses:
            return self._responses[-1]
        return SimpleNamespace(content="默认回复", tool_calls=None, reasoning_content=None)


@pytest.fixture
def mock_tools():
    """Mock agent.tools 模块"""
    with mock.patch("agent.tool_calling.tools") as m:
        m.get_tool_defs.return_value = []
        m.call.return_value = {"ok": True, "result": "执行成功"}
        m.ToolError = type("ToolError", (Exception,), {})
        yield m


@pytest.fixture
def service(mock_tools):
    """基础 ToolCallingService 实例"""
    llm = FakeLLM()
    return ToolCallingService(llm, max_rounds=3, tool_timeout=10, task_timeout=30)


# ═══════════════════════════════════════════════════════════════
# _clean_for_json 测试
# ═══════════════════════════════════════════════════════════════


class TestCleanForJson:
    """_clean_for_json 各种类型转换测试"""

    def test_bytes_to_str(self):
        result = _clean_for_json(b"hello")
        assert result == "hello"

    def test_bytes_invalid_utf8_replace(self):
        result = _clean_for_json(b"\xff\xfe")
        assert isinstance(result, str)

    def test_dict_recursive(self):
        result = _clean_for_json({"a": b"x", "b": 1})
        assert result == {"a": "x", "b": 1}

    def test_dict_none_key(self):
        result = _clean_for_json({None: "v"})
        assert result == {None: "v"}

    def test_list_recursive(self):
        result = _clean_for_json([b"a", b"b"])
        assert result == ["a", "b"]

    def test_tuple_to_list(self):
        result = _clean_for_json((b"a", 1))
        assert result == ["a", 1]
        assert isinstance(result, list)

    def test_set_to_list(self):
        result = _clean_for_json({1, 2, 3})
        assert set(result) == {1, 2, 3}
        assert isinstance(result, list)

    def test_frozenset_to_list(self):
        result = _clean_for_json(frozenset(["a", "b"]))
        assert set(result) == {"a", "b"}

    def test_int_passthrough(self):
        assert _clean_for_json(42) == 42

    def test_float_passthrough(self):
        assert _clean_for_json(3.14) == 3.14

    def test_str_passthrough(self):
        assert _clean_for_json("text") == "text"

    def test_bool_passthrough(self):
        assert _clean_for_json(True) is True

    def test_none_passthrough(self):
        assert _clean_for_json(None) is None

    def test_datetime_isoformat(self):
        dt = datetime.datetime(2026, 1, 1, 12, 0, 0)
        result = _clean_for_json(dt)
        assert result == dt.isoformat()

    def test_date_isoformat(self):
        d = datetime.date(2026, 1, 1)
        result = _clean_for_json(d)
        assert result == d.isoformat()

    def test_object_with_str(self):
        class Custom:
            def __str__(self):
                return "custom-instance"
        assert _clean_for_json(Custom()) == "custom-instance"

    def test_circular_reference(self):
        a = {}
        a["self"] = a
        result = _clean_for_json(a)
        assert result["self"] == "<循环引用>"

    def test_nested_circular(self):
        a = {"x": 1}
        b = {"a": a}
        a["b"] = b
        result = _clean_for_json(a)
        # 嵌套循环引用应该被检测到
        assert "<循环引用>" in str(result)

    def test_empty_dict(self):
        assert _clean_for_json({}) == {}

    def test_empty_list(self):
        assert _clean_for_json([]) == []

    def test_complex_nested(self):
        data = {
            "list": [b"a", {"inner": b"b"}],
            "tuple": (1, 2, b"c"),
            "nested": {"deep": {"bytes": b"d"}}
        }
        result = _clean_for_json(data)
        assert result == {
            "list": ["a", {"inner": "b"}],
            "tuple": [1, 2, "c"],
            "nested": {"deep": {"bytes": "d"}}
        }


# ═══════════════════════════════════════════════════════════════
# _summarize_tool_result 测试
# ═══════════════════════════════════════════════════════════════


class TestSummarizeToolResult:
    """_summarize_tool_result 工具结果摘要测试"""

    def test_non_dict_input(self):
        assert _summarize_tool_result("tool", "string result") == "string result"

    def test_non_dict_truncated(self):
        long_str = "x" * 300
        result = _summarize_tool_result("tool", long_str)
        assert len(result) <= 200

    def test_failure_with_error(self):
        result = _summarize_tool_result("tool", {"ok": False, "error": "网络错误"})
        assert "执行失败" in result
        assert "网络错误" in result

    def test_failure_with_message(self):
        result = _summarize_tool_result("tool", {"ok": False, "message": "校验失败"})
        assert "校验失败" in result

    def test_failure_with_exit_code(self):
        result = _summarize_tool_result("tool", {"ok": False, "exit_code": 127})
        assert "127" in result

    def test_failure_unknown_error(self):
        result = _summarize_tool_result("tool", {"ok": False})
        assert "未知错误" in result

    def test_web_search_with_results(self):
        result = _summarize_tool_result("web_search", {
            "ok": True,
            "results": [
                {"title": "标题1", "snippet": "片段1"},
                {"title": "标题2", "snippet": "片段2"},
            ]
        })
        assert "2 条结果" in result
        assert "标题1" in result

    def test_web_search_empty_results(self):
        result = _summarize_tool_result("web_search", {"ok": True, "results": []})
        assert "未找到" in result

    def test_web_get_with_title(self):
        result = _summarize_tool_result("web_get", {
            "ok": True, "title": "页面标题", "text": "x" * 100
        })
        assert "页面标题" in result

    def test_web_get_without_title(self):
        result = _summarize_tool_result("web_get", {"ok": True, "text": "x" * 100})
        assert "100 字符" in result

    def test_read_file(self):
        result = _summarize_tool_result("read_file", {"ok": True, "content": "x" * 50})
        assert "50 字符" in result

    def test_read_file_with_result_key(self):
        result = _summarize_tool_result("read_file", {"ok": True, "result": "y" * 80})
        assert "80 字符" in result

    def test_generic_success_with_result(self):
        result = _summarize_tool_result("any_tool", {"ok": True, "result": "完成"})
        assert "完成" in result

    def test_generic_success_truncated(self):
        long_result = "z" * 200
        result = _summarize_tool_result("any_tool", {"ok": True, "result": long_result})
        assert len(result) <= 80  # 60 + "执行成功" 前缀 (无前缀时仅 60)

    def test_generic_success_no_result(self):
        result = _summarize_tool_result("any_tool", {"ok": True})
        assert result == "执行成功"


# ═══════════════════════════════════════════════════════════════
# ToolCallError 异常测试
# ═══════════════════════════════════════════════════════════════


class TestToolCallError:
    """ToolCallError 异常类测试"""

    def test_is_exception(self):
        err = ToolCallError("测试")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        err = ToolCallError("错误消息")
        assert "错误消息" in str(err)

    def test_raise_and_catch(self):
        with pytest.raises(ToolCallError) as exc_info:
            raise ToolCallError("抛出错误")
        assert "抛出错误" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════
# 静态方法测试
# ═══════════════════════════════════════════════════════════════


class TestExtractToolCalls:
    """_extract_tool_calls 测试 (实例方法)"""

    def test_with_tool_calls_attribute(self, service):
        fn = SimpleNamespace(name="search", arguments='{"q":"test"}')
        tc = SimpleNamespace(id="tc1", type="function", function=fn)
        resp = SimpleNamespace(tool_calls=[tc])
        result = service._extract_tool_calls(resp)
        assert len(result) == 1
        assert result[0]["id"] == "tc1"
        assert result[0]["function"]["name"] == "search"

    def test_with_none_tool_calls(self, service):
        resp = SimpleNamespace(tool_calls=None)
        assert service._extract_tool_calls(resp) == []

    def test_without_tool_calls_attribute(self, service):
        resp = SimpleNamespace(content="text")
        assert service._extract_tool_calls(resp) == []

    def test_dict_response_with_tool_calls(self, service):
        resp = {"tool_calls": [{"id": "x", "function": {"name": "f", "arguments": "{}"}}]}
        result = service._extract_tool_calls(resp)
        assert len(result) == 1

    def test_dict_response_without_tool_calls(self, service):
        resp = {"content": "text"}
        assert service._extract_tool_calls(resp) == []


class TestExtractText:
    """_extract_text 测试 (实例方法)"""

    def test_with_content_attribute(self, service):
        resp = SimpleNamespace(content="hello")
        assert service._extract_text(resp) == "hello"

    def test_with_none_content(self, service):
        resp = SimpleNamespace(content=None)
        assert service._extract_text(resp) == ""

    def test_dict_with_content(self, service):
        resp = {"content": "world"}
        assert service._extract_text(resp) == "world"

    def test_dict_with_text_key(self, service):
        resp = {"text": "fallback"}
        assert service._extract_text(resp) == "fallback"

    def test_other_type_str(self, service):
        result = service._extract_text(42)
        assert "42" in result


class TestExtractReasoning:
    """_extract_reasoning 测试 (实例方法)"""

    def test_with_reasoning_content(self, service):
        resp = SimpleNamespace(reasoning_content="thinking...")
        assert service._extract_reasoning(resp) == "thinking..."

    def test_with_none_reasoning_content(self, service):
        resp = SimpleNamespace(reasoning_content=None)
        assert service._extract_reasoning(resp) is None

    def test_without_reasoning_content(self, service):
        resp = SimpleNamespace(content="x")
        assert service._extract_reasoning(resp) is None

    def test_dict_with_reasoning_content(self, service):
        resp = {"reasoning_content": "rationale"}
        assert service._extract_reasoning(resp) == "rationale"

    def test_dict_with_reasoning_key(self, service):
        resp = {"reasoning": "alt"}
        assert service._extract_reasoning(resp) == "alt"


class TestGetLastAssistantText:
    """_get_last_assistant_text 测试 (实例方法)"""

    def test_with_assistant_message(self, service):
        msgs = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        assert service._get_last_assistant_text(msgs) == "回答"

    def test_multiple_assistants_returns_last(self, service):
        msgs = [
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "second"},
        ]
        assert service._get_last_assistant_text(msgs) == "second"

    def test_no_assistant(self, service):
        msgs = [{"role": "user", "content": "q"}]
        assert service._get_last_assistant_text(msgs) == ""

    def test_assistant_empty_content(self, service):
        msgs = [{"role": "assistant", "content": ""}]
        assert service._get_last_assistant_text(msgs) == ""

    def test_assistant_none_content(self, service):
        msgs = [{"role": "assistant", "content": None}]
        assert service._get_last_assistant_text(msgs) == ""

    def test_empty_list(self, service):
        assert service._get_last_assistant_text([]) == ""


class TestSummarizeToolSteps:
    """_summarize_tool_steps 测试 (@staticmethod)"""

    def test_empty_steps(self):
        assert ToolCallingService._summarize_tool_steps([]) == ""

    def test_no_tool_results(self):
        steps = [{"type": "text", "content": "x"}]
        assert ToolCallingService._summarize_tool_steps(steps) == ""

    def test_with_successful_tool_results(self):
        steps = [
            {"type": "tool_result", "status": "success", "tool": "search", "summary": "找到结果"},
            {"type": "tool_result", "status": "error", "tool": "read", "summary": "失败"},
        ]
        result = ToolCallingService._summarize_tool_steps(steps)
        assert "以下是执行结果" in result
        assert "search" in result
        assert "找到结果" in result
        assert "read" not in result

    def test_with_empty_summary(self):
        steps = [{"type": "tool_result", "status": "success", "tool": "t", "summary": ""}]
        result = ToolCallingService._summarize_tool_steps(steps)
        assert "t" not in result


class TestExtractXmlToolCalls:
    """_extract_xml_tool_calls 测试 (@staticmethod)"""

    def test_empty_text(self):
        assert ToolCallingService._extract_xml_tool_calls("") == []

    def test_none_text(self):
        assert ToolCallingService._extract_xml_tool_calls(None) == []

    def test_no_tool_calls_tag(self):
        text = "<other>content</other>"
        assert ToolCallingService._extract_xml_tool_calls(text) == []

    def test_simple_xml_tool_call(self):
        text = """<tool_calls>
<invoke name="search">
<parameter name="q">测试</parameter>
</invoke>
</tool_calls>"""
        result = ToolCallingService._extract_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "search"
        args = json.loads(result[0]["function"]["arguments"])
        assert args["q"] == "测试"

    def test_multiple_xml_tool_calls(self):
        text = """<tool_calls>
<invoke name="search"><parameter name="q">a</parameter></invoke>
<invoke name="read"><parameter name="path">/tmp</parameter></invoke>
</tool_calls>"""
        result = ToolCallingService._extract_xml_tool_calls(text)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "search"
        assert result[1]["function"]["name"] == "read"

    def test_namespaced_xml(self):
        text = """<dsml:tool_calls>
<dsml:invoke name="calc">
<dsml:parameter name="x">1</dsml:parameter>
</dsml:invoke>
</dsml:tool_calls>"""
        result = ToolCallingService._extract_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "calc"

    def test_xml_id_format(self):
        text = """<tool_calls>
<invoke name="op"><parameter name="x">1</parameter></invoke>
</tool_calls>"""
        result = ToolCallingService._extract_xml_tool_calls(text)
        assert result[0]["id"] == "xml_0"


class TestTruncateToolContent:
    """_truncate_tool_content 测试 (@staticmethod)"""

    def test_short_content_unchanged(self):
        assert ToolCallingService._truncate_tool_content("short") == "short"

    def test_exact_limit(self):
        content = "x" * 3000
        result = ToolCallingService._truncate_tool_content(content, max_chars=3000)
        assert result == content

    def test_over_limit_truncated(self):
        content = "x" * 4000
        result = ToolCallingService._truncate_tool_content(content, max_chars=3000)
        assert len(result) < len(content)
        assert "已截断" in result

    def test_custom_max_chars(self):
        content = "x" * 200
        result = ToolCallingService._truncate_tool_content(content, max_chars=100)
        assert "已截断" in result
        assert len(result) < 200


# ═══════════════════════════════════════════════════════════════
# ToolCallingService 初始化测试
# ═══════════════════════════════════════════════════════════════


class TestInit:
    """ToolCallingService 初始化测试"""

    def test_init_with_explicit_params(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=5, tool_timeout=30, task_timeout=120)
        assert svc._max_rounds == 5
        assert svc._tool_timeout == 30
        assert svc._task_timeout == 120
        assert svc._primary_llm is llm
        assert svc._upgrade_llm is None
        assert svc._model_upgraded is False
        assert svc.last_steps == []

    def test_init_defaults_when_config_unavailable(self, mock_tools):
        # 当 Config() 抛异常时应使用默认值
        llm = FakeLLM()
        with mock.patch("config.Config", side_effect=Exception("config unavailable")):
            svc = ToolCallingService(llm)
        assert svc._max_rounds == 20
        assert svc._tool_timeout == 120
        assert svc._task_timeout == 600

    def test_current_llm_property(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=1, tool_timeout=1, task_timeout=1)
        assert svc._current_llm is llm
        # 模拟升级后
        new_llm = FakeLLM(model="upgraded")
        svc._upgrade_llm = new_llm
        assert svc._current_llm is new_llm

    def test_abort_sets_event(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=1, tool_timeout=1, task_timeout=1)
        assert not svc._abort_event.is_set()
        svc.abort()
        assert svc._abort_event.is_set()


# ═══════════════════════════════════════════════════════════════
# _anthropic_to_openai 测试
# ═══════════════════════════════════════════════════════════════


class TestAnthropicToOpenai:
    """_anthropic_to_openai 格式转换测试"""

    def test_text_only(self, service):
        text_block = SimpleNamespace(type="text", text="hello")
        resp = SimpleNamespace(content=[text_block])
        result = service._anthropic_to_openai(resp)
        assert result.content == "hello"
        assert result.tool_calls is None
        assert result.role == "assistant"

    def test_tool_use_only(self, service):
        tool_block = SimpleNamespace(
            type="tool_use", id="tc1", name="search", input={"q": "x"}
        )
        resp = SimpleNamespace(content=[tool_block])
        result = service._anthropic_to_openai(resp)
        assert result.content == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "tc1"
        assert result.tool_calls[0].function.name == "search"
        assert json.loads(result.tool_calls[0].function.arguments) == {"q": "x"}

    def test_mixed_blocks(self, service):
        text_block = SimpleNamespace(type="text", text="思考中...")
        tool_block = SimpleNamespace(
            type="tool_use", id="tc1", name="op", input={"x": 1}
        )
        resp = SimpleNamespace(content=[text_block, tool_block])
        result = service._anthropic_to_openai(resp)
        assert result.content == "思考中..."
        assert len(result.tool_calls) == 1

    def test_empty_content(self, service):
        resp = SimpleNamespace(content=[])
        result = service._anthropic_to_openai(resp)
        assert result.content == ""
        assert result.tool_calls is None

    def test_no_content_attribute(self, service):
        resp = SimpleNamespace()
        result = service._anthropic_to_openai(resp)
        assert result.content == ""


# ═══════════════════════════════════════════════════════════════
# chat_with_steps 全流程测试
# ═══════════════════════════════════════════════════════════════


class TestChatWithSteps:
    """chat_with_steps 各种场景测试

    通过 mock _call_llm_with_tools 方法精确控制 LLM 响应,
    避免 FakeLLM._client 缺少 chat.completions.create 导致的降级路径。
    """

    def test_pure_text_response(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=3, tool_timeout=5, task_timeout=10)
        resp = SimpleNamespace(content="你好", tool_calls=None, reasoning_content=None)
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        result = svc.chat_with_steps([{"role": "user", "content": "hi"}])
        assert result["text"] == "你好"

    def test_with_reasoning(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        resp = SimpleNamespace(
            content="回答", tool_calls=None, reasoning_content="推理过程"
        )
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        result = svc.chat_with_steps([{"role": "user", "content": "q"}])
        assert result["text"] == "回答"
        assert result["reasoning"] == "推理过程"

    def test_tool_call_then_text(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=3, tool_timeout=5, task_timeout=10)
        fn = SimpleNamespace(name="search", arguments='{"q":"test"}')
        tc = SimpleNamespace(id="tc1", type="function", function=fn)
        resp1 = SimpleNamespace(content=None, tool_calls=[tc], reasoning_content=None)
        resp2 = SimpleNamespace(content="最终回答", tool_calls=None, reasoning_content=None)
        svc._call_llm_with_tools = mock.MagicMock(side_effect=[resp1, resp2])
        mock_tools.call.return_value = {"ok": True, "result": "找到结果"}
        result = svc.chat_with_steps([{"role": "user", "content": "搜索 test"}])
        assert result["text"] == "最终回答"
        tool_calls = [s for s in result["steps"] if s["type"] == "tool_call"]
        tool_results = [s for s in result["steps"] if s["type"] == "tool_result"]
        assert len(tool_calls) == 1
        assert len(tool_results) == 1
        assert tool_results[0]["status"] == "success"

    def test_xml_tool_call_detection(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        xml_text = """<tool_calls>
<invoke name="calc"><parameter name="x">1</parameter></invoke>
</tool_calls>"""
        resp = SimpleNamespace(content=xml_text, tool_calls=None, reasoning_content=None)
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        mock_tools.call.return_value = {"ok": True, "result": 42}
        result = svc.chat_with_steps([{"role": "user", "content": "计算"}])
        assert "已执行操作" in result["text"]
        tool_calls = [s for s in result["steps"] if s["type"] == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool"] == "calc"

    def test_abort_signal_via_callback(self, mock_tools):
        # 通过 on_step 回调在循环内触发 abort
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=3, tool_timeout=5, task_timeout=10)
        fn = SimpleNamespace(name="op", arguments='{}')
        tc = SimpleNamespace(id="tc1", type="function", function=fn)
        resp = SimpleNamespace(content=None, tool_calls=[tc])
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        mock_tools.call.return_value = {"ok": True, "result": "ok"}

        def trigger_abort(step):
            if step.get("type") == "tool_call":
                svc.abort()
        result = svc.chat_with_steps(
            [{"role": "user", "content": "q"}],
            on_step=trigger_abort,
        )
        aborted = [s for s in result["steps"] if s["type"] == "aborted"]
        assert len(aborted) == 1

    def test_first_round_fallback_to_chat(self, mock_tools):
        llm = FakeLLM(chat_text="降级回复")
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)

        def fail_call(*args, **kwargs):
            raise RuntimeError("API error")
        svc._call_llm_with_tools = fail_call
        result = svc.chat_with_steps([{"role": "user", "content": "q"}])
        assert result["text"] == "降级回复"

    def test_first_round_fallback_failure_raises(self, mock_tools):
        llm = FakeLLM(raise_on_call=True)
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)

        def fail_call(*args, **kwargs):
            raise RuntimeError("API error")
        svc._call_llm_with_tools = fail_call
        with pytest.raises(ToolCallError):
            svc.chat_with_steps([{"role": "user", "content": "q"}])

    def test_max_rounds_reached(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        fn = SimpleNamespace(name="op", arguments='{}')
        tc = SimpleNamespace(id="tc1", type="function", function=fn)
        resp = SimpleNamespace(content=None, tool_calls=[tc])
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        mock_tools.call.return_value = {"ok": True, "result": "ok"}
        result = svc.chat_with_steps([{"role": "user", "content": "q"}])
        max_steps = [s for s in result["steps"] if "最大" in s.get("content", "")]
        assert len(max_steps) >= 1

    def test_on_step_callback(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        resp = SimpleNamespace(content="hi", tool_calls=None)
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        recorded = []
        result = svc.chat_with_steps(
            [{"role": "user", "content": "q"}],
            on_step=lambda s: recorded.append(s),
        )
        assert result["text"] == "hi"

    def test_chat_returns_text_only(self, mock_tools):
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        resp = SimpleNamespace(content="回复", tool_calls=None)
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        text = svc.chat([{"role": "user", "content": "q"}])
        assert text == "回复"

    def test_empty_text_falls_back_to_history(self, mock_tools):
        # 模型返回空文本,应回退到历史助手消息
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        resp = SimpleNamespace(content="", tool_calls=None)
        svc._call_llm_with_tools = mock.MagicMock(return_value=resp)
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "历史回答"},
        ]
        result = svc.chat_with_steps(messages)
        assert result["text"] == "历史回答"

    def test_empty_text_no_history_falls_back_to_summary(self, mock_tools):
        # 模型返回空文本且无历史,应回退到工具步骤摘要
        llm = FakeLLM()
        svc = ToolCallingService(llm, max_rounds=2, tool_timeout=5, task_timeout=10)
        # 第一轮返回工具调用,第二轮返回空文本
        fn = SimpleNamespace(name="op", arguments='{}')
        tc = SimpleNamespace(id="tc1", type="function", function=fn)
        resp1 = SimpleNamespace(content=None, tool_calls=[tc])
        resp2 = SimpleNamespace(content="", tool_calls=None)
        svc._call_llm_with_tools = mock.MagicMock(side_effect=[resp1, resp2])
        mock_tools.call.return_value = {"ok": True, "result": "ok"}
        result = svc.chat_with_steps([{"role": "user", "content": "q"}])
        # 应使用工具步骤摘要作为最终文本
        assert result["text"]  # 非空


# ═══════════════════════════════════════════════════════════════
# _execute_safe 测试
# ═══════════════════════════════════════════════════════════════


class TestExecuteSafe:
    """_execute_safe 测试"""

    def test_success_with_dict(self, service, mock_tools):
        mock_tools.call.return_value = {"ok": True, "result": "成功"}
        result = service._execute_safe("search", {"q": "test"})
        assert result["ok"] is True

    def test_success_with_non_dict(self, service, mock_tools):
        mock_tools.call.return_value = "string result"
        result = service._execute_safe("op", {})
        assert result["ok"] is True
        assert result["result"] == "string result"

    def test_dict_without_ok_key(self, service, mock_tools):
        mock_tools.call.return_value = {"data": "x"}
        result = service._execute_safe("op", {})
        assert result["ok"] is True

    def test_tool_error(self, service, mock_tools):
        err = mock_tools.ToolError("工具错误")
        mock_tools.call.side_effect = err
        result = service._execute_safe("op", {})
        assert result["ok"] is False
        assert "工具错误" in result["error"]

    def test_generic_exception(self, service, mock_tools):
        mock_tools.call.side_effect = ValueError("值错误")
        result = service._execute_safe("op", {})
        assert result["ok"] is False
        assert "工具执行异常" in result["error"]


# ═══════════════════════════════════════════════════════════════
# _call_llm_openai / _call_llm_anthropic 测试
# ═══════════════════════════════════════════════════════════════


class TestCallLlmOpenai:
    """_call_llm_openai 测试"""

    def test_basic_call(self, service, mock_tools):
        msg = SimpleNamespace(content="hi", tool_calls=None)
        choice = SimpleNamespace(message=msg)
        resp = SimpleNamespace(choices=[choice])
        client = SimpleNamespace()
        client.chat = SimpleNamespace()
        client.chat.completions = SimpleNamespace()
        client.chat.completions.create = mock.MagicMock(return_value=resp)

        result = service._call_llm_openai(
            client, [{"role": "user", "content": "q"}],
            "system", 100, 0.5, None
        )
        assert result.content == "hi"
        client.chat.completions.create.assert_called_once()

    def test_with_system_prompt(self, service, mock_tools):
        msg = SimpleNamespace(content="ok")
        choice = SimpleNamespace(message=msg)
        resp = SimpleNamespace(choices=[choice])
        client = SimpleNamespace()
        client.chat = SimpleNamespace()
        client.chat.completions = SimpleNamespace()
        client.chat.completions.create = mock.MagicMock(return_value=resp)

        service._call_llm_openai(
            client, [{"role": "user", "content": "q"}],
            "你是助手", 100, 0.5, None
        )
        call_kwargs = client.chat.completions.create.call_args.kwargs
        # 第一条消息应为 system
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][0]["content"] == "你是助手"

    def test_with_tool_defs(self, service, mock_tools):
        msg = SimpleNamespace(content="ok")
        choice = SimpleNamespace(message=msg)
        resp = SimpleNamespace(choices=[choice])
        client = SimpleNamespace()
        client.chat = SimpleNamespace()
        client.chat.completions = SimpleNamespace()
        client.chat.completions.create = mock.MagicMock(return_value=resp)
        tool_defs = [{"function": {"name": "op"}}]

        service._call_llm_openai(
            client, [{"role": "user", "content": "q"}],
            "", 100, 0.5, tool_defs
        )
        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["tools"] == tool_defs


class TestCallLlmAnthropic:
    """_call_llm_anthropic 测试"""

    def test_basic_call(self, service, mock_tools):
        text_block = SimpleNamespace(type="text", text="hi")
        resp = SimpleNamespace(content=[text_block])
        client = SimpleNamespace()
        client.messages = SimpleNamespace()
        client.messages.create = mock.MagicMock(return_value=resp)

        result = service._call_llm_anthropic(
            client, [{"role": "user", "content": "q"}],
            "", 100, 0.5, None
        )
        assert result.content == "hi"

    def test_system_prompt_passed(self, service, mock_tools):
        text_block = SimpleNamespace(type="text", text="ok")
        resp = SimpleNamespace(content=[text_block])
        client = SimpleNamespace()
        client.messages = SimpleNamespace()
        client.messages.create = mock.MagicMock(return_value=resp)

        service._call_llm_anthropic(
            client, [{"role": "user", "content": "q"}],
            "你是助手", 100, 0.5, None
        )
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "你是助手"

    def test_tool_role_conversion(self, service, mock_tools):
        text_block = SimpleNamespace(type="text", text="ok")
        resp = SimpleNamespace(content=[text_block])
        client = SimpleNamespace()
        client.messages = SimpleNamespace()
        client.messages.create = mock.MagicMock(return_value=resp)

        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "op", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "result"},
        ]
        service._call_llm_anthropic(
            client, messages, "", 100, 0.5, None
        )
        call_kwargs = client.messages.create.call_args.kwargs
        # tool 角色应转为 user + tool_result
        anthropic_msgs = call_kwargs["messages"]
        # 应包含 tool_result 块
        tool_result_found = False
        for m in anthropic_msgs:
            if m["role"] == "user":
                for c in m.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        tool_result_found = True
        assert tool_result_found

    def test_system_role_merged(self, service, mock_tools):
        text_block = SimpleNamespace(type="text", text="ok")
        resp = SimpleNamespace(content=[text_block])
        client = SimpleNamespace()
        client.messages = SimpleNamespace()
        client.messages.create = mock.MagicMock(return_value=resp)

        messages = [
            {"role": "system", "content": "额外系统消息"},
            {"role": "user", "content": "q"},
        ]
        service._call_llm_anthropic(
            client, messages, "原始 system", 100, 0.5, None
        )
        call_kwargs = client.messages.create.call_args.kwargs
        # 应合并 system prompt
        assert "原始 system" in call_kwargs["system"]
        assert "额外系统消息" in call_kwargs["system"]

    def test_tool_defs_conversion(self, service, mock_tools):
        text_block = SimpleNamespace(type="text", text="ok")
        resp = SimpleNamespace(content=[text_block])
        client = SimpleNamespace()
        client.messages = SimpleNamespace()
        client.messages.create = mock.MagicMock(return_value=resp)
        tool_defs = [{"function": {"name": "op", "description": "操作", "parameters": {}}}]

        service._call_llm_anthropic(
            client, [{"role": "user", "content": "q"}],
            "", 100, 0.5, tool_defs
        )
        call_kwargs = client.messages.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "op"

    def test_max_tokens_minimum(self, service, mock_tools):
        text_block = SimpleNamespace(type="text", text="ok")
        resp = SimpleNamespace(content=[text_block])
        client = SimpleNamespace()
        client.messages = SimpleNamespace()
        client.messages.create = mock.MagicMock(return_value=resp)

        service._call_llm_anthropic(
            client, [{"role": "user", "content": "q"}],
            "", 100, 0.5, None
        )
        call_kwargs = client.messages.create.call_args.kwargs
        # max_tokens 至少为 2048
        assert call_kwargs["max_tokens"] >= 2048


# ═══════════════════════════════════════════════════════════════
# _try_upgrade_model 测试
# ═══════════════════════════════════════════════════════════════


class TestTryUpgradeModel:
    """_try_upgrade_model 测试"""

    def test_skip_when_already_upgraded(self, service):
        service._model_upgraded = True
        assert service._try_upgrade_model([]) is False

    def test_skip_when_no_router(self, service):
        service._model_router = None
        assert service._try_upgrade_model([{"type": "tool_result", "status": "success"}]) is False

    def test_skip_when_no_successful_tools(self, service):
        router = mock.MagicMock()
        service._model_router = router
        steps = [{"type": "tool_result", "status": "error"}]
        assert service._try_upgrade_model(steps) is False

    def test_skip_when_model_not_in_router(self, service):
        router = mock.MagicMock()
        router._models = {"other": SimpleNamespace(model="other-model")}
        service._model_router = router
        # primary_llm.model 不匹配
        steps = [{"type": "tool_result", "status": "success"}]
        assert service._try_upgrade_model(steps) is False

    def test_skip_when_no_upgrade_target(self, service):
        router = mock.MagicMock()
        router._models = {"base": SimpleNamespace(model="test-model")}
        router.get_upgrade.return_value = None
        service._model_router = router
        steps = [{"type": "tool_result", "status": "success"}]
        assert service._try_upgrade_model(steps) is False
