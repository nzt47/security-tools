"""P1 编排器回合步骤出口（_TURN_STEP_SINK）与流式复用接线守卫

覆盖：
  1. set / current / reset 语义（含 reset 幂等与非法输入）；
  2. 跨上下文隔离，且 copy_context 能带到池线程；
  3. _run_llm_bounded **把调用方上下文复制进池线程**（否则步骤出口在池线程里读不到）；
  4. plugins.chat._orch_step_to_event 对 tool_call/tool_result/text/中断 的映射与 id 关联。
"""
from __future__ import annotations

import contextvars

from agent.orchestrator.orchestrator import (
    Orchestrator,
    current_turn_step_sink,
    reset_turn_step_sink,
    set_turn_step_sink,
)
from plugins.chat import _orch_step_to_event


def test_sink_set_current_reset():
    assert current_turn_step_sink() is None
    got = []

    def sink(step):
        got.append(step)

    token = set_turn_step_sink(sink)
    assert current_turn_step_sink() is sink
    current_turn_step_sink()({"type": "text", "content": "x"})
    assert got == [{"type": "text", "content": "x"}]
    reset_turn_step_sink(token)
    assert current_turn_step_sink() is None
    # 幂等 / 非法输入不抛
    reset_turn_step_sink(token)
    reset_turn_step_sink(None)
    reset_turn_step_sink(object())


def test_sink_isolated_across_fresh_context():
    def sink(_s):
        pass

    token = set_turn_step_sink(sink)
    try:
        assert contextvars.copy_context().run(current_turn_step_sink) is sink
        # 全新上下文看不到调用方设置的出口（按执行上下文隔离）
        assert contextvars.Context().run(current_turn_step_sink) is None
    finally:
        reset_turn_step_sink(token)


def test_run_llm_bounded_propagates_context_to_pool():
    var = contextvars.ContextVar("t_p1_probe", default="unset")
    var.set("v1")
    sink = lambda _s: None  # noqa: E731
    token = set_turn_step_sink(sink)
    try:
        seen = Orchestrator._run_llm_bounded(
            object(), lambda: (var.get(), current_turn_step_sink() is sink))
    finally:
        reset_turn_step_sink(token)
    assert seen == ("v1", True), "池线程没看到调用方上下文 ⇒ 步骤出口形同虚设"


def test_mapper_tool_call_and_result_share_id():
    st = {}
    a = _orch_step_to_event({"type": "tool_call", "tool": "read_file",
                             "args": "{}", "status": "running"}, st)
    b = _orch_step_to_event({"type": "tool_result", "tool": "read_file",
                             "status": "success", "summary": "ok"}, st)
    assert a["id"] == b["id"]
    assert a["status"] == "running" and b["status"] == "done"
    err = _orch_step_to_event({"type": "tool_result", "tool": "read_file",
                               "status": "error", "summary": "boom"}, st)
    assert err["id"] == a["id"] and err["status"] == "error"


def test_mapper_text_and_interruptions():
    st = {}
    t = _orch_step_to_event({"type": "text", "content": "hello"}, st)
    assert t["status"] == "done" and "hello" in t["detail"]
    for kind, want in (("aborted", "编排已中止"), ("timed_out", "编排超时"),
                       ("tool_stuck", "工具卡住")):
        e = _orch_step_to_event({"type": kind, "summary": "s"}, st)
        assert e["status"] == "error" and e["title"] == want


def test_mapper_drops_unknown():
    assert _orch_step_to_event({"type": "mystery"}, {}) is None
    assert _orch_step_to_event(None, {}) is None
    assert _orch_step_to_event("not-a-dict", {}) is None
