"""TASK-01 测试：规划引擎 wire 接线（LLM 调用前分支 + 回退铁律）

评估标准对应（TASK-01_规划引擎接入主链路.md §6）：
1. wire_enabled=false（默认）→ process() 不调用 _planner.chat()（灰度 inert）；
2. wire_enabled=true + COMPLEX 任务 → 调用 _planner.chat() 且响应为规划结果
   （metadata 标注 routed_by: planning，供 TASK-03 埋点）；
3. wire_enabled=true + chat() 抛异常/超时 → 回退原 LLM 路径，响应正常（不中断）；
4. wire_enabled=true + 简单任务 → 不触发规划。

设计说明（【简易】复用 test_planning_stage5_e2e 的 _make_test_orch 模式：
mock 掉 InputGuard/Workflow/语义/拒识等前置层，让 process() 直落 wire 分支 →
LLM 段，只验证 TASK-01 接线行为本身，不重复验证四层路由）。
"""
import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.guardrails.input_guard import GuardAction, GuardResult
from agent.guardrails.output_guard import OutputResult
from agent.monitoring.prometheus import (
    reset_intent_layer_counts,
    _intent_layer_counts,
)
from agent.orchestrator.orchestrator import Orchestrator
from planning.core import ChatResult


@pytest.fixture(autouse=True)
def _reset_counts():
    """每个测试前重置模块级 intent layer 计数，隔离测试间状态"""
    reset_intent_layer_counts()
    yield
    reset_intent_layer_counts()


def _wire_orch(**overrides):
    """构造直落 wire→LLM 段的 orchestrator（wire 配置由测试注入，隔离 config.yaml）

    【不易】_load_planning_wire_config 是类方法（读真实 config.yaml），
           测试用实例属性遮蔽为 lambda，仅注入 wire_* 三配置，不依赖真实文件。
    """
    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")
    behavior.profile.enable_reflection = False

    orch = Orchestrator.__new__(Orchestrator)
    defaults = {
        "_running": True,
        "_interaction_count": 1,
        "_interaction_lock": threading.Lock(),
        "_last_context_warning": None,
        "_last_was_template": False,
        "_session_id": "test-wire",
        "_guardrails_input_guard": MagicMock(check=lambda x: GuardResult(GuardAction.ALLOW)),
        # _output_guard 是只读 property（懒加载 _guardrails_output_guard），直接设底层属性
        "_guardrails_output_guard": MagicMock(check=lambda x: OutputResult(filtered=x)),
        "_workflow_engine": MagicMock(try_match=lambda x: None),
        "_memory": MagicMock(),
        "_behavior": behavior,
        "_build_body_status": MagicMock(return_value="Body status"),
        "_build_reject_response": MagicMock(return_value="Request rejected"),
        "_call_llm": MagicMock(return_value="LLM 直答响应"),
        "_call_llm_v2": MagicMock(return_value="LLM 直答响应"),
        "_set_thinking_mode": MagicMock(),
        "_check_context_usage": MagicMock(return_value=None),
        "_v2_lifetrace": False,
        "_v2_distillation": False,
        "_v2_persona": False,
        "_vector_memory": None,
        "_trace_recorder": None,
        "_error_reporter": None,
        "_current_mode": MagicMock(value="test_mode"),
        "_persona_injector": None,
        "_persona_extractor": None,
        "_is_skill_enabled": lambda x: False,
        # wire 分支依赖
        "_planner": None,
        "_planning_enabled": False,  # 旧"第四步半"规划段关闭，聚焦验证 wire 分支
        "_needs_planning": lambda x: False,
        "_config": {"planning": {"timeout_seconds": 30}},
        # 直落 LLM 步骤所需的关键路由桩（避免真实语义/拒识/模板逻辑干扰）
        "_update_dst_after_route": MagicMock(),
        "_semantic_layer_match": MagicMock(return_value=None),
        "_should_reject": MagicMock(return_value=(False, "")),
        "_load_reject_config": MagicMock(return_value={"threshold": 0.3}),
        "check_health": MagicMock(return_value=[]),
        "_learn_workflow_from_interaction": MagicMock(),
        # wire 配置加载：实例属性遮蔽类方法，测试注入（默认 false = 灰度关闭）
        "_load_planning_wire_config": lambda: {
            "enabled": False,
            "min_complexity": "COMPLEX",
            "timeout_seconds": 30,
        },
    }
    for k, v in defaults.items():
        setattr(orch, k, v)
    for k, v in overrides.items():
        setattr(orch, k, v)
    return orch


def _wire_enabled_cfg(min_complexity: str = "COMPLEX", timeout_seconds: float = 30):
    """wire 开启时的配置注入（用例 2/3/4 复用）"""
    return lambda: {
        "enabled": True,
        "min_complexity": min_complexity,
        "timeout_seconds": timeout_seconds,
    }


_COMPLEX_INPUT = "帮我构建一个分布式系统架构"  # 复杂词命中（架构/系统/分布式/帮我构建）→ COMPLEX
# 简单任务：意图 unknown（模板不命中，直落 LLM，stage5 已验证）+ 复杂度 NORMAL < COMPLEX
_SIMPLE_INPUT = "帮我完成一个多步骤任务"


@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestWireEnabledOff:
    """wire_enabled=false（默认）→ 规划分支整体 inert"""

    def test_wire_disabled_keeps_llm_behavior(self):
        """开关关闭时（即使任务复杂）也不调用 _planner.chat()，保持 LLM 直答"""
        planner = AsyncMock()
        orch = _wire_orch(_planner=planner)  # _load_planning_wire_config 默认 enabled=False
        result = orch.process(_COMPLEX_INPUT)

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应", "开关关闭时响应应与原 LLM 路径一致"
        planner.chat.assert_not_called()
        assert _intent_layer_counts.get("planning") is None
        assert result["metadata"].get("routed_by") is None


@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestWireEnabledOn:
    """wire_enabled=true → COMPLEX 任务走规划，简单任务不受影响"""

    def test_complex_task_uses_planning(self):
        """wire 开启 + COMPLEX 任务 → 调用 _planner.chat()，响应为规划结果"""
        planner = AsyncMock()
        planner.chat.return_value = ChatResult(response="规划引擎响应")
        orch = _wire_orch(_planner=planner, _load_planning_wire_config=_wire_enabled_cfg())
        result = orch.process(_COMPLEX_INPUT)

        assert result["success"] is True
        assert result["data"] == "规划引擎响应", "复杂任务响应应来自规划引擎"
        assert result["metadata"].get("routed_by") == "planning", "响应应标注 routed_by=planning"
        planner.chat.assert_called_once()
        assert _intent_layer_counts.get("planning") == 1

    def test_simple_task_skips_planning(self):
        """wire 开启 + 简单任务（复杂度 < COMPLEX）→ 不触发规划，走 LLM 直答"""
        planner = AsyncMock()
        orch = _wire_orch(_planner=planner, _load_planning_wire_config=_wire_enabled_cfg())
        result = orch.process(_SIMPLE_INPUT)

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应"
        planner.chat.assert_not_called()
        assert _intent_layer_counts.get("planning") is None


@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestWireFallback:
    """回退铁律：规划异常/超时 → 静默降级原 LLM 路径（不中断用户请求）"""

    def test_planning_exception_falls_back_to_llm(self, caplog):
        """chat() 抛异常 → 回退 LLM 直答，日志含降级 WARNING"""
        planner = AsyncMock()
        planner.chat.side_effect = RuntimeError("planning engine crash")
        orch = _wire_orch(_planner=planner, _load_planning_wire_config=_wire_enabled_cfg())
        with caplog.at_level(logging.WARNING, logger="agent.orchestrator.orchestrator"):
            result = orch.process(_COMPLEX_INPUT)

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应", "规划异常应回退 LLM 直答"
        planner.chat.assert_called_once()
        assert _intent_layer_counts.get("planning") is None
        assert any("回退 LLM 直答" in r.getMessage() for r in caplog.records), \
            "降级应有 WARNING 日志记录原因"

    def test_planning_timeout_falls_back_to_llm(self):
        """chat() 超时（timeout_seconds=0.1，挂起 2s）→ wait_for 中断回退直答，链路耗时受控"""
        async def _hanging(*a, **k):
            await asyncio.sleep(2)
            return None

        planner = AsyncMock()
        planner.chat.side_effect = _hanging
        orch = _wire_orch(_planner=planner,
                          _load_planning_wire_config=_wire_enabled_cfg(timeout_seconds=0.1))

        start = time.monotonic()
        result = orch.process(_COMPLEX_INPUT)
        elapsed = time.monotonic() - start

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应", "规划超时应回退 LLM 直答"
        assert elapsed < 1.5, f"超时中断应生效（耗时 {elapsed:.2f}s，不应等满挂起 2s）"
