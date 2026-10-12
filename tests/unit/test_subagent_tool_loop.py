"""子代理侧工具循环守卫（S5-3：agent/subagent/tool_loop.py + peer_local_handler 接线）

覆盖：
  1. 白名单内工具被执行并回填，最终文本返回；
  2. 白名单外 / 空白名单 ⇒ 拒绝执行（E_TOOL_NOT_ALLOWED，不触达后端）；
  3. LLM 不支持工具调用 ⇒ E_TOOL_LOOP_UNSUPPORTED（不静默退化）；
  4. LLM 调用异常 ⇒ E_TOOL_LOOP_LLM_FAILED；工具自身异常 ⇒ 记入 tool_calls 但循环继续；
  5. 轮次上限；审计每次工具一条；
  6. peer_local_handler.run 在带 tool_defs 且 LLM 支持工具时走工具循环。
"""
from __future__ import annotations

from agent.subagent import peer_local_handler as h
from agent.subagent.tool_loop import (
    E_TOOL_LOOP_LLM_FAILED,
    E_TOOL_LOOP_UNSUPPORTED,
    E_TOOL_NOT_ALLOWED,
    llm_supports_tools,
    run_tool_loop,
)


class _FakeToolLLM:
    provider = "fake"
    model = "fake-tools"

    def __init__(self, turns):
        # turns: [(text, [(tool_name, args_json), ...]), ...]；耗尽后返回空文本
        self._turns = list(turns)
        self.seen_tools = []

    def chat_stream(self, messages, system_prompt="", tools=None, on_tool_call=None):
        self.seen_tools.append(tools)
        text, calls = self._turns.pop(0) if self._turns else ("", [])
        for name, args_json in calls:
            if on_tool_call:
                on_tool_call(name, args_json, "")
        yield text


class _BoomToolLLM:
    provider = "fake"
    model = "boom"

    def chat_stream(self, messages, system_prompt="", tools=None, on_tool_call=None):
        raise RuntimeError("upstream down")
        yield ""  # pragma: no cover（生成器语义：永不产出）


class _ChatOnlyLLM:
    provider = "local"
    model = "no-tools"

    def chat(self, messages, system_prompt=""):
        return "ok"


def test_llm_supports_tools_shape():
    assert llm_supports_tools(_FakeToolLLM([])) is True
    assert llm_supports_tools(_ChatOnlyLLM()) is False
    assert llm_supports_tools(object()) is False


def test_白名单内执行并回填():
    seen = []

    def _exec(name, args):
        seen.append((name, args))
        return {"ok": True, "result": "hit"}

    llm = _FakeToolLLM([("thinking", [("memory.read", '{"query": "x"}')]),
                        ("final answer", [])])
    messages = [{"role": "user", "content": "go"}]
    out = run_tool_loop(llm, messages=messages, tool_defs=[{"x": 1}],
                        allowed=["memory.read"], execute=_exec, max_turns=4)
    assert out.ok is True and out.text == "final answer" and out.turns == 2
    assert seen == [("memory.read", {"query": "x"})]
    assert out.tool_calls == [{"name": "memory.read", "ok": True, "error_code": ""}]
    # 回填了 assistant(tool_calls) + tool 消息
    assert messages[-2]["role"] == "assistant" and messages[-2]["tool_calls"]
    assert messages[-1]["role"] == "tool" and "hit" in messages[-1]["content"]


def test_白名单外拒绝且不执行():
    called = []

    def _exec(name, args):
        called.append(name)
        return {"ok": True}

    llm = _FakeToolLLM([("", [("shell", "{}")]), ("done", [])])
    out = run_tool_loop(llm, messages=[], tool_defs=[{}],
                        allowed=["memory.read"], execute=_exec, max_turns=4)
    assert out.ok is True
    assert called == [], "白名单外工具被执行了"
    assert out.tool_calls[0]["error_code"] == E_TOOL_NOT_ALLOWED


def test_空白名单全拒():
    llm = _FakeToolLLM([("", [("memory.read", "{}")]), ("done", [])])
    out = run_tool_loop(llm, messages=[], tool_defs=[{}], allowed=[],
                        execute=lambda n, a: {"ok": True}, max_turns=4)
    assert out.tool_calls[0]["error_code"] == E_TOOL_NOT_ALLOWED


def test_不支持工具调用明确失败():
    out = run_tool_loop(_ChatOnlyLLM(), messages=[], tool_defs=[{}], allowed=["x"])
    assert out.ok is False and out.error_code == E_TOOL_LOOP_UNSUPPORTED


def test_LLM异常如实失败():
    out = run_tool_loop(_BoomToolLLM(), messages=[], tool_defs=[{}], allowed=["x"])
    assert out.ok is False and out.error_code == E_TOOL_LOOP_LLM_FAILED


def test_工具异常如实记录且循环继续():
    def _boom(name, args):
        raise ValueError("bad tool")

    llm = _FakeToolLLM([("", [("memory.write", "{}")]), ("recovered", [])])
    out = run_tool_loop(llm, messages=[], tool_defs=[{}],
                        allowed=["memory.write"], execute=_boom, max_turns=4)
    assert out.ok is True and out.text == "recovered"
    assert out.tool_calls[0]["ok"] is False
    assert out.tool_calls[0]["error_code"] == "ValueError"


def test_轮次上限():
    turns = [("", [("memory.read", "{}")]) for _ in range(5)]
    llm = _FakeToolLLM(turns)
    out = run_tool_loop(llm, messages=[], tool_defs=[{}],
                        allowed=["memory.read"], execute=lambda n, a: {"ok": True},
                        max_turns=2)
    assert out.turns == 2 and len(out.tool_calls) == 2


def test_审计每次工具一条():
    records = []
    llm = _FakeToolLLM([("", [("memory.read", "{}"), ("memory.write", "{}")]),
                        ("done", [])])
    run_tool_loop(llm, messages=[], tool_defs=[{}],
                  allowed=["memory.read", "memory.write"],
                  execute=lambda n, a: {"ok": True}, max_turns=4,
                  audit=lambda tool, ok, code, ms: records.append((tool, ok)))
    assert records == [("memory.read", True), ("memory.write", True)]


def test_peer_handler走工具循环(monkeypatch):
    from agent.subagent import tool_loop as tl
    # 注入执行器（默认走 agent.tools.call，"memory.read" 只是能力命名空间、非真实工具名）
    monkeypatch.setattr(tl, "_default_execute",
                        lambda name, args: {"ok": True, "result": "hit"})
    llm = _FakeToolLLM([("", [("memory.read", "{}")]),
                        ('{"status": "done", "summary": "real"}', [])])
    monkeypatch.setattr(h, "_resolve_llm", lambda: llm)
    rec = h.run({"goal": "g", "tool_defs": [{"name": "memory.read"}],
                 "allowed_tools": ["memory.read"]}, max_turns=4,
                output_format="json")
    assert rec["status"] == "done" and rec["summary"] == "real"
    assert rec["tool_calls"] == [{"name": "memory.read", "ok": True,
                                  "error_code": ""}]
    assert llm.seen_tools and llm.seen_tools[0], "工具定义没下发给模型"
