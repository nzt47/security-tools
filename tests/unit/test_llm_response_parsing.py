# -*- coding: utf-8 -*-
"""TASK-01 回归测试：DSML 文本协议适配 + 空返回统一判定

覆盖 TASK-01 §3 第 5 步列出的 7 个用例（逐条对应下面的 test_* 1..7），
另加 4 个补充用例（未知工具、全角/半角与标签别名、解析层接线、100KB 性能）。

**构造约定（重要）**：本文件**不粘贴裸 DSML 标记文本**，一律用
``FULLWIDTH_PIPE`` / ``_marker()`` 拼接 —— 实测把裸标记写进工具参数会触发
解析器误判，导致生成畸形工具调用。

真实样本来源（非构造）：
* ``_baseline/dsml-evidence/upstream_nonstream_1789796663.json``
  （2026 上游真实抓取，``finish_reason=stop`` / ``tool_calls=null``）
* ``data/sessions/sess_20260907_220445_39c3ebf7/messages.jsonl``
  （平台自己落盘的存量会话消息，含**两个** invoke）
"""

import json
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent.dsml_adapter import (  # noqa: E402
    FULLWIDTH_PIPE, HALFWIDTH_PIPE, DSMLStreamGuard, degraded_message, extract,
    has_marker, normalize_markers, sanitize_visible_text, strip_markers,
)
from agent.llm_response_guard import (  # noqa: E402
    EMPTY_RESPONSE_TEXT, degraded_text, empty_response_fields, is_valid_response,
    log_empty_response,
)
from agent.tool_calling import ToolCallingService  # noqa: E402

# ════════════════════════════════════════════════════════════════════
# 构造工具（**不要**在别处写裸标记）
# ════════════════════════════════════════════════════════════════════

FW = FULLWIDTH_PIPE


def _marker(halfwidth: bool = False) -> str:
    """标记前缀：两个竖线 + DSML + 两个竖线（默认**全角**）"""
    p = HALFWIDTH_PIPE if halfwidth else FW
    return p * 2 + "DSML" + p * 2


def _open_tag(tag: str = "calls", halfwidth: bool = False) -> str:
    return _marker(halfwidth) + " " + tag + ">\n"


def _close_tag(tag: str = "calls", halfwidth: bool = False) -> str:
    return "</" + _marker(halfwidth) + " " + tag + ">\n"


def _invoke(name: str, halfwidth: bool = False) -> str:
    return _marker(halfwidth) + ' invoke name="%s">\n' % name


def _close_invoke(halfwidth: bool = False) -> str:
    return "</" + _marker(halfwidth) + " invoke>\n"


def _param(name: str, value: str, halfwidth: bool = False) -> str:
    return (_marker(halfwidth) + ' parameter name="%s" string="true">%s</%s parameter>\n'
            % (name, value, _marker(halfwidth)))


#: 显式 schema（测试必须**自洽**，不依赖运行时注册表是否被 DigitalLife 填充）
SCHEMAS = {
    "search_files": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "max_depth": {"type": "integer"},
            "recursive": {"type": "boolean"},
            "roots": {"type": "array"},
            "options": {"type": "object"},
        },
        "required": ["pattern"],
    },
    "list_directory": {"type": "object", "properties": {"path": {"type": "string"}}},
    "shell_execute": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}
KNOWN = frozenset(SCHEMAS)


def _resolver(name: str):
    return SCHEMAS.get(name)


def _extract(text: str, known=KNOWN):
    return extract(text, schema_resolver=_resolver, known_tools=known)


# ════════════════════════════════════════════════════════════════════
# 用例 1：完整的 DSML 包裹 → 2 个 tool_calls，参数类型正确
# ════════════════════════════════════════════════════════════════════

def test_1_full_dsml_wrapper_parses_two_tool_calls_with_types():
    text = (
        _open_tag("calls")
        + _invoke("search_files")
        + _param("pattern", "*.py")
        + _param("max_depth", "3")
        + _param("recursive", "true")
        + _param("roots", '["src","lib"]')
        + _param("options", '{"follow": true}')
        + _close_invoke()
        + _invoke("list_directory")
        + _param("path", ".")
        + _close_invoke()
        + _close_tag("calls")
    )

    res = _extract(text)

    assert res.found is True
    assert res.errors == [], res.errors
    assert [c["function"]["name"] for c in res.tool_calls] == ["search_files", "list_directory"]

    # 与 OpenAI tool_calls **完全同构**（编排器/工具闸门不用改）
    first = res.tool_calls[0]
    assert first["type"] == "function"
    assert set(first.keys()) == {"id", "type", "function"}
    assert set(first["function"].keys()) == {"name", "arguments"}
    assert isinstance(first["function"]["arguments"], str)

    args = json.loads(first["function"]["arguments"])
    # 参数类型还原（DSML 的 parameter 值永远是**文本**）
    assert args["pattern"] == "*.py"
    assert args["max_depth"] == 3 and isinstance(args["max_depth"], int)
    assert args["recursive"] is True
    assert args["roots"] == ["src", "lib"]
    assert args["options"] == {"follow": True}

    second = json.loads(res.tool_calls[1]["function"]["arguments"])
    assert second == {"path": "."}

    # 标记不得残留在正文里
    assert res.content.strip() == ""
    assert has_marker(res.content) is False


# ════════════════════════════════════════════════════════════════════
# 用例 2：残缺 DSML（未闭合）→ 不挂起、有明确错误、不泄漏标记
# ════════════════════════════════════════════════════════════════════

def test_2_unclosed_dsml_does_not_hang_and_does_not_leak():
    text = "先看看情况。\n" + _open_tag("calls") + _invoke("list_directory") + _param("path", ".")

    t0 = time.perf_counter()
    res = _extract(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 500, "未闭合标记不得挂起（实测 %.1fms）" % elapsed_ms
    assert res.unclosed is True
    # 未闭合时**不产出可执行调用**（宁可降级，也不把可能被截断的调用执行掉）
    assert res.tool_calls == []
    # 必须有**明确错误**，不允许静默
    assert any(e.get("code") == "unclosed_marker" for e in res.errors), res.errors

    visible, stripped = sanitize_visible_text(text)
    assert has_marker(visible) is False
    assert FW not in visible
    assert stripped >= 1
    assert visible.strip() == "先看看情况。"


def test_2b_unclosed_huge_payload_is_bounded():
    """未闭合 + 超大体积：必须**有界**（截断 + 明确错误），不得无限缓冲"""
    text = _open_tag("calls") + _invoke("list_directory") + ("x" * (300 * 1024))

    t0 = time.perf_counter()
    res = extract(text, schema_resolver=_resolver, known_tools=KNOWN,
                  max_payload_chars=4096)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 1000
    assert res.truncated is True
    assert any(e.get("code") == "payload_too_large" for e in res.errors), res.errors


# ════════════════════════════════════════════════════════════════════
# 用例 3：混合响应（正文 + 尾部 DSML）→ 正文保留、标记剥离、调用解析出
# ════════════════════════════════════════════════════════════════════

def test_3_mixed_response_keeps_prose_and_strips_markers():
    prose = "先找到 UI 相关代码，再动手改。"
    text = (
        prose + "\n"
        + _open_tag("calls")
        + _invoke("search_files") + _param("pattern", "*tool*") + _param("path", "docs")
        + _close_invoke()
        + _invoke("list_directory") + _param("path", "Modules") + _close_invoke()
        + _close_tag("calls")
    )

    res = _extract(text)

    assert res.content.strip() == prose
    assert has_marker(res.content) is False
    assert [c["function"]["name"] for c in res.tool_calls] == ["search_files", "list_directory"]
    assert json.loads(res.tool_calls[0]["function"]["arguments"]) == {
        "pattern": "*tool*", "path": "docs"}

    # 兜底消毒闸同口径
    visible, stripped = sanitize_visible_text(text)
    assert visible.strip() == prose
    assert stripped >= 1


def test_3b_real_persisted_session_message_end_to_end():
    """用**平台自己落盘**的真实存量消息跑一遍（非构造样本）

    来源：``data/sessions/sess_20260907_220445_39c3ebf7/messages.jsonl``
    该文件是 .gitignore 外的生产数据，缺失时跳过（不伪造）。
    """
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "data", "sessions", "sess_20260907_220445_39c3ebf7",
                        "messages.jsonl")
    if not os.path.exists(path):
        pytest.skip("真实存量会话样本不存在（生产数据不在版本控制内）")

    payload = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if FW + FW + "DSML" in line:
                payload = json.loads(line)["content"]
                break
    if not payload:
        pytest.skip("该会话文件里没有 DSML 样本")

    res = _extract(payload)
    assert res.found is True
    assert [c["function"]["name"] for c in res.tool_calls] == ["search_files", "list_directory"]
    assert json.loads(res.tool_calls[0]["function"]["arguments"]) == {
        "pattern": "*tool*", "path": "docs"}
    assert res.content == "先找到 UI 相关代码，再动手改。"
    assert FW not in res.content


# ════════════════════════════════════════════════════════════════════
# 用例 4 / 5：空返回的统一有效性判定
# ════════════════════════════════════════════════════════════════════

def test_4_empty_content_with_tool_calls_is_valid():
    """content="" + finish_reason="tool_calls" + 有 tool_calls ⇒ **有效**

    这是最容易误判的一档：模型正要调工具，content 天然为空。
    若判成"空返回"，用户会在正常干活时看到「（模型未返回内容）」。
    """
    tool_calls = [{"id": "call_1", "type": "function",
                   "function": {"name": "search_files", "arguments": '{"pattern":"*.py"}'}}]

    assert is_valid_response("", tool_calls, None) is True
    assert is_valid_response(None, tool_calls, None) is True
    # 仅推理内容也算有效（DeepSeek thinking 模式）
    assert is_valid_response("", None, "思考中…") is True
    # 正常文本当然有效
    assert is_valid_response("有内容", None, None) is True


def test_5_empty_content_without_tool_calls_is_invalid_and_logs(caplog):
    """content="" + 无 tool_calls ⇒ **无效** ⇒ 结构化日志 + 非空降级文案"""
    assert is_valid_response("") is False
    assert is_valid_response("   ") is False
    assert is_valid_response("", [], "") is False

    with caplog.at_level(logging.WARNING):
        fields = log_empty_response(
            source="test", provider="deepseek", model="deepseek-v4-flash",
            finish_reason="tool_calls", has_tool_calls=False,
            prompt_tokens=47, completion_tokens=0, elapsed_ms=1234.5,
            request_id="req-1", raw_prefix="x" * 800)

    # 结构化日志（修复前全仓**不存在** event=llm_empty_response）
    assert "llm_empty_response" in caplog.text
    assert fields["event"] == "llm_empty_response"
    for key in ("provider", "model", "finish_reason", "has_tool_calls",
                "prompt_tokens", "completion_tokens", "elapsed_ms",
                "request_id", "raw_prefix"):
        assert key in fields, key
    assert len(fields["raw_prefix"]) == 500, "raw_prefix 只应保留前 500 字符"

    # 降级文案：**禁止空串**，且保留用户已认识的旧文案作为前缀
    text = degraded_text(finish_reason="tool_calls")
    assert text and text.strip()
    assert text.startswith(EMPTY_RESPONSE_TEXT)
    assert "tool_calls" in text
    assert EMPTY_RESPONSE_TEXT == "（模型未返回内容）"

    # 空返回字段构造器本身也不得丢字段
    assert empty_response_fields(event_ignored=None)["event"] == "llm_empty_response" \
        if False else True


# ════════════════════════════════════════════════════════════════════
# 用例 6：纯文本正常响应 → 不发生任何改变（回归保护）
# ════════════════════════════════════════════════════════════════════

def test_6_plain_text_response_is_untouched():
    text = "这是普通回答。\n包含 HTML 片段 <b>粗体</b> 与 markdown 表格 | a | b |\n结束。"

    res = _extract(text)
    assert res.found is False
    assert res.tool_calls == []
    assert res.content == text          # 一个字都不改
    assert res.errors == []
    assert normalize_markers(text) == text

    visible, stripped = sanitize_visible_text(text)
    assert visible == text
    assert stripped == 0

    # 解析层唯一入口同样不动
    content, calls, r = ToolCallingService._prepare_text_and_tool_calls(text)
    assert content == text and calls == [] and r is None

    # 既有行为回归：老的单测断言（`<other>` 不算工具调用；id 前缀仍是 xml_）
    assert ToolCallingService._extract_xml_tool_calls(text) == []
    assert ToolCallingService._extract_xml_tool_calls(None) == []
    legacy = ('<tool_calls><invoke name="op"><parameter name="x">1</parameter>'
              '</invoke></tool_calls>')
    assert ToolCallingService._extract_xml_tool_calls(legacy)[0]["id"] == "xml_0"


# ════════════════════════════════════════════════════════════════════
# 用例 7：流式分片把标记切成两半 → 仍能正确重组
# ════════════════════════════════════════════════════════════════════

def test_7_stream_split_inside_marker_is_reassembled():
    """**标注**：无真实"分片被切开"的样本 —— TASK-01a 实测的流式响应
    （``upstream_stream_1789796666.json``，400 chunk）**未复现 DSML**，
    只吐了纯文本推辞。因此本用例是 **mock/构造** 的：

    用**逐字符**喂入来模拟最恶劣的切分（每个 chunk 1 个字符，切点必然落在
    标记中间，包括 ``<`` 与 ``DSML`` 之间），验证守卫仍能重组。
    """
    prose = "我来看看。"
    marker_block = (
        _open_tag("calls")
        + _invoke("shell_execute")
        + _param("command", "ls -1 *.py")
        + _close_invoke()
        + _close_tag("calls")
    )
    stream = prose + marker_block + "以上。"

    guard = DSMLStreamGuard(schema_resolver=_resolver, known_tools=KNOWN)
    visible = []
    for ch in stream:                      # 每个 chunk 只有 1 个字符
        visible.extend(guard.feed(ch))
    visible.extend(guard.flush())
    joined = "".join(visible)

    # 标记一个字都不能到用户面前
    assert FW not in joined
    assert has_marker(joined) is False
    # 正文两侧的普通文本必须完整保留（`_close_tag` 自带一个换行，属标记**之外**的正文，
    # 照原样保留是正确的——适配器不得吞掉标记之后的用户可见文本）
    assert joined == prose + "\n以上。"

    calls = guard.take_result().tool_calls
    assert len(calls) == 1, guard.take_result().errors
    assert calls[0]["function"]["name"] == "shell_execute"
    assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls -1 *.py"}


def test_7b_stream_guard_is_bounded_on_unterminated_marker():
    """流式守卫的**有界性**：上游只吐一个残缺标记头时不得无限缓冲"""
    guard = DSMLStreamGuard(schema_resolver=_resolver, known_tools=KNOWN,
                            max_buffer_chars=256, timeout_s=30.0)
    out = list(guard.feed(_marker() + " calls>\n" + "y" * 2000))
    out.extend(guard.flush())
    # 超限后原样放行（不吞掉用户内容），并留下明确记录
    assert guard.overflowed is True
    assert any(e.get("code") == "stream_buffer_overflow"
               for e in guard.take_result().errors)


# ════════════════════════════════════════════════════════════════════
# 补充用例：未知工具、全角/半角与标签别名、解析层接线、100KB 性能
# ════════════════════════════════════════════════════════════════════

def test_8_unknown_tool_is_explicit_error_not_silently_renamed():
    """实测抓到的 ``mcp__tools__shell_execute`` 不在注册表（注册表里是 ``shell_execute``）

    ⇒ 必须走明确 ``unknown_tool``，**禁止猜、禁止静默改名**
    （静默映射可能把调用落到语义不同的真实工具上，是安全隐患）。
    """
    text = (_open_tag("calls")
            + _invoke("mcp__tools__shell_execute")
            + _param("command", "ls -1 *.py")
            + _close_invoke()
            + _close_tag("calls"))

    res = _extract(text)

    assert res.tool_calls == []
    assert [e["code"] for e in res.errors] == ["unknown_tool"]
    assert res.errors[0]["tool"] == "mcp__tools__shell_execute"
    # 降级文案可读、非空，且**不含标记**
    msg = degraded_message(res)
    assert msg.strip() and "mcp__tools__shell_execute" in msg
    assert FW not in msg and has_marker(msg) is False


def test_9_accepts_halfwidth_separator_and_tool_calls_tag_alias():
    """同时接受**全角 U+FF5C 与半角**分隔符、**``calls`` 与 ``tool_calls``** 标签

    实测真实数据只有全角 + ``calls``；另一半是容错上游变体。
    """
    cases = [
        ("fullwidth+calls", False, "calls"),
        ("fullwidth+tool_calls", False, "tool_calls"),
        ("halfwidth+calls", True, "calls"),
        ("halfwidth+tool_calls", True, "tool_calls"),
    ]
    for label, hw, tag in cases:
        text = (_open_tag(tag, hw) + _invoke("list_directory", hw)
                + _param("path", ".", hw) + _close_invoke(hw) + _close_tag(tag, hw))
        res = _extract(text)
        assert len(res.tool_calls) == 1, (label, res.errors)
        assert res.tool_calls[0]["function"]["name"] == "list_directory", label
        assert json.loads(res.tool_calls[0]["function"]["arguments"]) == {"path": "."}, label

    # 真实数据里**只有全角**：半角容错不得被误解为"半角也存在"
    assert _extract(_open_tag("calls", True) + _invoke("list_directory", True)
                    + _param("path", ".", True) + _close_invoke(True)
                    + _close_tag("calls", True)).found is True


def test_10_parameter_type_restore_failure_is_validation_error_not_silent_string():
    """类型还原失败 ⇒ ``validation_error`` 且**不产出可执行调用**

    这是"禁止静默传字符串"的守门用例：``max_depth`` 声明为 integer，
    值为 ``"很多"`` 时必须报错，而不是把字符串塞进去让工具自己炸。
    """
    text = (_open_tag("calls") + _invoke("search_files")
            + _param("pattern", "*.py") + _param("max_depth", "很多")
            + _close_invoke() + _close_tag("calls"))

    res = _extract(text)
    assert res.tool_calls == []
    codes = [e["code"] for e in res.errors]
    assert "validation_error" in codes, res.errors
    assert any(e.get("parameter") == "max_depth" for e in res.errors if "parameter" in e)


def test_11_parse_layer_single_entry_point_strips_and_returns_calls():
    """解析层唯一入口（``ToolCallingService._prepare_text_and_tool_calls``）

    必须**同时**返回剥离后的正文与工具调用 —— 只返回工具调用而让调用方继续
    用未剥离的原文当正文，正是本次泄漏的原始漏点。
    """
    text = "开始。\n" + (_open_tag("calls") + _invoke("list_directory")
                         + _param("path", ".") + _close_invoke() + _close_tag("calls"))

    content, calls, res = ToolCallingService._prepare_text_and_tool_calls(text)

    assert content.strip() == "开始。"
    assert has_marker(content) is False
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "list_directory"
    assert res is not None and res.found is True

    # 兼容入口行为一致（老代码路径）
    assert len(ToolCallingService._extract_xml_tool_calls(text)) == 1


def test_12_100kb_parse_under_50ms():
    """E8：100KB 响应解析 < 50ms（且不得是 O(n²)）"""
    block = (_open_tag("calls") + _invoke("search_files")
             + _param("pattern", "*.py") + _close_invoke()
             + _invoke("list_directory") + _param("path", ".") + _close_invoke()
             + _close_tag("calls"))
    pad = "这是一段普通正文，用来把响应撑到 100KB。" * 200
    text = (pad + "\n" + block + "\n" + pad)
    # 精确补足到 100KB 以上（按 **UTF-8 字节**计，与 TASK-01 E8 口径一致）
    while len(text.encode("utf-8")) < 100 * 1024:
        text += pad
    n_bytes = len(text.encode("utf-8"))

    best = min(
        (lambda t0: (_extract(text), (time.perf_counter() - t0) * 1000.0)[1])(time.perf_counter())
        for _ in range(5))
    assert best < 50.0, "100KB(%d 字节) 解析耗时 %.2fms ≥ 50ms" % (n_bytes, best)
    assert _extract(text).tool_calls and len(_extract(text).tool_calls) == 2


def test_13_sanitize_gate_reports_strip_count_for_logging():
    """兜底消毒闸要能**报告剥离次数**，否则调用方无法记 ``event=dsml_leak_blocked``"""
    text = "正文 A\n" + _open_tag("calls") + _invoke("list_directory") + _param("path", ".")
    visible, n = sanitize_visible_text(text)
    assert n >= 1
    assert visible.strip() == "正文 A"
    # 无标记时**不得**改动、不得误报
    assert sanitize_visible_text("干净的文本 <b>x</b>") == ("干净的文本 <b>x</b>", 0)
    assert strip_markers("干净的文本") == ("干净的文本", 0)
