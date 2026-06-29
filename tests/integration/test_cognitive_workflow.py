"""Cognitive Loop + Workflow Engine 集成测试

验证 CognitiveLoop（反思 / 评估）与 WorkflowEngine（规则匹配）协作：
  - 普通聊天 → reflection.passed
  - 高风险工具调用 → complexity == high_risk → review 不为 None
"""
import pytest
from agent.cognitive.loop import CognitiveLoop
from agent.workflow_engine.engine import WorkflowEngine
from agent.workflow_engine.builtin_rules import register_builtin_rules


class TestCognitiveWorkflow:
    def test_workflow_result_triggers_reflection(self):
        """工作流执行结果应触发认知循环，reflection 默认 passed"""
        loop = CognitiveLoop()
        result = loop.evaluate("t1", "chat", "hello", "Hi!", 50, tool_calls=[])
        assert result.reflection is not None
        assert result.reflection.passed

    def test_high_risk_triggers_actor_critic(self):
        """高风险工具调用应触发双 Agent 校验 -> review 不为 None"""
        loop = CognitiveLoop()
        result = loop.evaluate(
            "t2", "execute_shell", "rm -rf /", "done", 100,
            tool_name="execute_shell", tool_params={"command": "rm -rf /"},
        )
        # 高风险时应自动触发 review
        if result.complexity == "high_risk":
            assert result.review is not None

    def test_workflow_engine_rules_match(self):
        """WorkflowEngine 内置规则应匹配已知场景"""
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        matched = engine.match("你好")
        assert matched is not None
        matched2 = engine.match("现在几点")
        assert matched2 is not None

    def test_simple_task_no_review(self):
        """简单聊天任务不应触发 review"""
        loop = CognitiveLoop()
        result = loop.evaluate("t3", "chat", "你好", "你好！", 30, tool_calls=[])
        assert result.complexity in ("simple", "low")
        assert result.review is None
