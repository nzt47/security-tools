"""Orchestrator 工作流学习拦截层 + 自动学习钩子单元测试（自动闭环 v1）

覆盖：
- _extract_tool_calls_from_steps: steps → LearningRecord.tool_calls 转换
- _workflow_learning_layer_match: 拦截层命中/未命中/失败/异常/开关降级
- _learn_workflow_from_interaction: 自动学习钩子开关/无步骤/成功/异常
"""
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock, patch

from agent.orchestrator.orchestrator import Orchestrator


def _make_orchestrator():
    """构造不执行真实 __init__ 的 Orchestrator（被动组件，依赖宿主注入属性）"""
    orch = Orchestrator.__new__(Orchestrator)
    orch._session_id = "test-session"
    return orch


def _enabled_cfg():
    return {"enabled": True, "min_score": 0.25}


def _wf_result(**overrides):
    """构造 WorkflowExecutionResult 形状的 mock 结果"""
    base = dict(
        matched=True,
        success=True,
        workflow_id="wf_test",
        workflow_name="测试工作流",
        similarity=0.72,
        confidence=0.55,
        steps_executed=2,
        skipped_llm=True,
        output="执行完成",
        error="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestExtractToolCallsFromSteps:
    def test_成功配对_失败过滤(self):
        steps = [
            {"type": "tool_call", "tool": "search", "args": {"q": "天气"}, "status": "running"},
            {"type": "tool_result", "tool": "search", "status": "success", "summary": "晴天"},
            {"type": "tool_call", "tool": "translate", "args": {"text": "hello"}, "status": "running"},
            {"type": "tool_result", "tool": "translate", "status": "error", "summary": "失败"},
        ]
        calls = Orchestrator._extract_tool_calls_from_steps(steps)
        # search 成功被保留；translate 失败被过滤
        assert len(calls) == 1
        assert calls[0]["name"] == "search"
        assert calls[0]["params"] == {"q": "天气"}
        assert calls[0]["output"] == "晴天"
        assert calls[0]["success"] is True

    def test_未配对tool_call被丢弃(self):
        # 有 call 无 result → 不入库（未完成调用不可学）
        steps = [{"type": "tool_call", "tool": "search", "args": {}, "status": "running"}]
        assert Orchestrator._extract_tool_calls_from_steps(steps) == []

    def test_非dict与空steps_安全忽略(self):
        assert Orchestrator._extract_tool_calls_from_steps([None, "x", 1]) == []
        assert Orchestrator._extract_tool_calls_from_steps(None) == []
        assert Orchestrator._extract_tool_calls_from_steps([]) == []

    def test_同名工具多次调用分别保留(self):
        steps = [
            {"type": "tool_call", "tool": "search", "args": {"q": "a"}, "status": "running"},
            {"type": "tool_result", "tool": "search", "status": "success", "summary": "结果A"},
            {"type": "tool_call", "tool": "search", "args": {"q": "b"}, "status": "running"},
            {"type": "tool_result", "tool": "search", "status": "success", "summary": "结果B"},
        ]
        calls = Orchestrator._extract_tool_calls_from_steps(steps)
        assert len(calls) == 2
        assert calls[0]["params"] == {"q": "a"}
        assert calls[1]["params"] == {"q": "b"}


class TestWorkflowLearningLayerMatch:
    def test_开关关闭_返回None(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_load_workflow_learning_layer_config",
                            classmethod(lambda cls: {"enabled": False, "min_score": 0.25}))
        assert orch._workflow_learning_layer_match("测试输入") is None

    def test_服务未初始化_返回None(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_load_workflow_learning_layer_config",
                            classmethod(lambda cls: _enabled_cfg()))
        monkeypatch.setattr(Orchestrator, "_WFL_TOOL_EXECUTOR_INJECTED", True)
        with patch("agent.state_manager.get_workflow_learning_service",
                   return_value=None):
            assert orch._workflow_learning_layer_match("测试输入") is None

    def test_未命中_返回None(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_load_workflow_learning_layer_config",
                            classmethod(lambda cls: _enabled_cfg()))
        monkeypatch.setattr(Orchestrator, "_WFL_TOOL_EXECUTOR_INJECTED", True)
        svc = MagicMock()
        svc.try_execute.return_value = _wf_result(matched=False, success=False)
        with patch("agent.state_manager.get_workflow_learning_service",
                   return_value=svc):
            assert orch._workflow_learning_layer_match("测试输入") is None

    def test_执行失败_返回None(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_load_workflow_learning_layer_config",
                            classmethod(lambda cls: _enabled_cfg()))
        monkeypatch.setattr(Orchestrator, "_WFL_TOOL_EXECUTOR_INJECTED", True)
        svc = MagicMock()
        svc.try_execute.return_value = _wf_result(matched=True, success=False,
                                                  error="工具超时")
        with patch("agent.state_manager.get_workflow_learning_service",
                   return_value=svc):
            assert orch._workflow_learning_layer_match("测试输入") is None

    def test_命中_返回完整结果dict(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_load_workflow_learning_layer_config",
                            classmethod(lambda cls: _enabled_cfg()))
        monkeypatch.setattr(Orchestrator, "_WFL_TOOL_EXECUTOR_INJECTED", True)
        svc = MagicMock()
        svc.try_execute.return_value = _wf_result()
        with patch("agent.state_manager.get_workflow_learning_service",
                   return_value=svc):
            res = orch._workflow_learning_layer_match("测试输入", trace_id="tr-1")
        assert res is not None
        assert res["output"] == "执行完成"
        assert res["workflow_id"] == "wf_test"
        assert res["score"] == 0.72
        assert res["skipped_llm"] is True
        # min_score 透传
        svc.try_execute.assert_called_once()
        assert svc.try_execute.call_args.kwargs.get("min_score") == 0.25

    def test_异常降级_返回None(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_load_workflow_learning_layer_config",
                            classmethod(lambda cls: _enabled_cfg()))
        monkeypatch.setattr(Orchestrator, "_WFL_TOOL_EXECUTOR_INJECTED", True)
        with patch("agent.state_manager.get_workflow_learning_service",
                   side_effect=RuntimeError("boom")):
            assert orch._workflow_learning_layer_match("测试输入") is None


class TestLearnWorkflowFromInteraction:
    def test_开关关闭_返回False(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_wf_learn_enabled",
                            classmethod(lambda cls: False))
        assert orch._learn_workflow_from_interaction("用户输入") is False

    def test_无成功工具调用_返回False(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_wf_learn_enabled",
                            classmethod(lambda cls: True))
        orch._last_tool_steps = [
            {"type": "tool_call", "tool": "search", "args": {}, "status": "running"},
        ]  # 无配对 result → 无可学调用
        assert orch._learn_workflow_from_interaction("用户输入") is False

    def test_成功_返回True(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_wf_learn_enabled",
                            classmethod(lambda cls: True))
        orch._last_tool_steps = [
            {"type": "tool_call", "tool": "search", "args": {"q": "x"}, "status": "running"},
            {"type": "tool_result", "tool": "search", "status": "success", "summary": "结果"},
        ]
        svc = MagicMock()
        wf = SimpleNamespace(id="wf_1", steps=[{"name": "search"}], trigger_patterns=["测试"])
        svc.learn_from_interaction.return_value = wf
        with patch("agent.state_manager.get_workflow_learning_service",
                   return_value=svc):
            assert orch._learn_workflow_from_interaction("用户输入") is True
        assert svc.learn_from_interaction.called

    def test_服务未初始化_返回False(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_wf_learn_enabled",
                            classmethod(lambda cls: True))
        orch._last_tool_steps = [
            {"type": "tool_call", "tool": "search", "args": {}, "status": "running"},
            {"type": "tool_result", "tool": "search", "status": "success", "summary": "r"},
        ]
        with patch("agent.state_manager.get_workflow_learning_service",
                   return_value=None):
            assert orch._learn_workflow_from_interaction("用户输入") is False

    def test_异常_返回False不抛错(self, monkeypatch):
        orch = _make_orchestrator()
        monkeypatch.setattr(Orchestrator, "_wf_learn_enabled",
                            classmethod(lambda cls: True))
        orch._last_tool_steps = [
            {"type": "tool_call", "tool": "search", "args": {}, "status": "running"},
            {"type": "tool_result", "tool": "search", "status": "success", "summary": "r"},
        ]
        with patch("agent.state_manager.get_workflow_learning_service",
                   side_effect=RuntimeError("svc broken")):
            assert orch._learn_workflow_from_interaction("用户输入") is False
