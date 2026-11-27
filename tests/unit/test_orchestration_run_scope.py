"""E4-1b：编排 run-scope（整 run 一次性授权）的窄旁路。

边界（见 agent/tool_gate.py 的 run-scope 小节）：
  ① 只放行 L2，L3 一律不认；
  ② 单次消费（命中一次即摘掉，防 fan_out 子代理沿同一线程继承而免审批）；
  ③ 只由 agent/orchestration/runtime 设置，不跨线程继承。
"""
from __future__ import annotations

import shutil
import threading

import pytest

import agent.tool_gate as G
from agent.orchestration import runtime


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    """审批边界全开（conftest 的会话基线是 0，这里显式打开才测得到确认层）"""
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    monkeypatch.delenv(G.CONFIRM_LEVEL_ENFORCE_ENV, raising=False)
    monkeypatch.delenv(G.CONFIRM_LEVEL_SHADOW_ENV, raising=False)
    monkeypatch.setattr(G, "_RUN_SCOPE_VAR", G.contextvars.ContextVar(
        "cp_run_scope_test", default=(frozenset(), "")))
    G._reset_cache()
    yield
    G._reset_cache()


def _node_available() -> bool:
    return shutil.which(runtime.node_binary()) is not None


class TestRunScopeGate:
    def test_无作用域时_L2_仍被拦(self):
        result = G.check_tool_call("fan_out", {})
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"

    def test_作用域内_L2_放行(self):
        token = G.set_run_scope(["fan_out"], run_id="run-t")
        try:
            assert G.check_tool_call("fan_out", {}) is None
        finally:
            G.reset_run_scope(token)

    def test_单次消费_第二次又被拦(self):
        token = G.set_run_scope(["fan_out"], run_id="run-t")
        try:
            assert G.check_tool_call("fan_out", {}) is None
            again = G.check_tool_call("fan_out", {})
            assert again is not None and again["error_code"] == "APPROVAL_REQUIRED"
            assert G.current_run_scope()["tools"] == ()
        finally:
            G.reset_run_scope(token)

    def test_L3_不认作用域(self):
        token = G.set_run_scope(["terminal"], run_id="run-t")
        try:
            result = G.check_tool_call("terminal", {})
            assert result is not None, "L3 不得被 run-scope 放行"
        finally:
            G.reset_run_scope(token)

    def test_作用域里的别的工具不放行(self):
        token = G.set_run_scope(["read_file"], run_id="run-t")
        try:
            result = G.check_tool_call("fan_out", {})
            assert result is not None and result["error_code"] == "APPROVAL_REQUIRED"
        finally:
            G.reset_run_scope(token)

    def test_不跨线程继承(self):
        token = G.set_run_scope(["fan_out"], run_id="run-t")
        seen = {}

        def _worker():
            seen["scope"] = G.current_run_scope()["tools"]
            seen["result"] = G.check_tool_call("fan_out", {})

        try:
            th = threading.Thread(target=_worker)
            th.start()
            th.join()
        finally:
            G.reset_run_scope(token)
        assert seen["scope"] == ()
        assert seen["result"] is not None, "子线程不得继承发起方的 run-scope"

    def test_放行时落审计(self, monkeypatch):
        decisions = []
        monkeypatch.setattr(G, "_audit_confirm_decision",
                            lambda **kw: decisions.append(kw.get("decision")))
        token = G.set_run_scope(["fan_out"], run_id="run-t")
        try:
            assert G.check_tool_call("fan_out", {}) is None
        finally:
            G.reset_run_scope(token)
        assert "run_scope" in decisions


class TestOrchestrationWiring:
    def test_call_tool_在执行线程里设作用域并复位(self, monkeypatch):
        seen = {}

        def _fake_call(name, **params):
            seen["scope"] = G.current_run_scope()["tools"]
            return {"ok": True, "value": 1}

        monkeypatch.setattr("agent.tools.call", _fake_call)
        ok, envelope = runtime._call_tool(
            "read_file", {"path": "x"}, run_id="run-t", result_chars=8000,
            scope_tools=("read_file",))
        assert ok is True
        assert "read_file" in seen["scope"]
        assert G.current_run_scope()["tools"] == (), "调用后必须复位（不外溢）"

    @pytest.mark.skipif(not _node_available(), reason="本机没有 node")
    def test_spawn_通过_run_scope_而不是_审批挂单(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runtime, "RUN_ROOT", tmp_path / "orch")
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        runtime.reset_probe_cache()
        result = runtime.run('const r = await spawn([{goal: "x"}]); return r;',
                             allow_spawn=True, timeout_sec=30)
        # 门已过：拿到的是"工具不存在/执行失败"，而**不是** needs_approval
        assert result["denied"] == 0, result
        assert result["result"]["code"] != "needs_approval", result
