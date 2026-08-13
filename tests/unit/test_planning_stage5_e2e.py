"""阶段 5 E2E 验收测试：规划引擎接入聊天主链路（D7）+ 灰度回滚（PLANNING_ENABLED）

评估标准对应（阶段5_运行时接入与重复能力收口.md）：
1. 复杂任务走规划链路：响应来自规划引擎（response 覆盖直答、_record_intent_layer("planning")）；
2. 逃生通道：规划失败/超时/空响应 → 回退原 LLM 直答响应；
3. planning.enabled=true 且 PLANNING_ENABLED=false 时可回退为旧行为；
4. 链路耗时 ≤ 配置 timeout_seconds（超时中断生效）。

设计说明（【简易】复用 test_llm_error_path_recorded 的 _make_test_orch 模式：
mock 掉 InputGuard/Workflow/语义/拒识等前置层，让 process() 直落 LLM → D7 规划段 →
OutputGuard → 反思/记忆，只验证 D7 接入行为本身，不重复验证四层路由）。
"""
import asyncio
import os
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.guardrails.input_guard import GuardAction, GuardResult
from agent.guardrails.output_guard import OutputResult
from agent.monitoring.prometheus import (
    record_intent_layer,
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


def _make_test_orch(**overrides):
    """复用 test_llm_error_path_recorded 模式，直落 LLM 段；补 D7 规划段所需属性"""
    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")
    behavior.profile.enable_reflection = False

    orch = Orchestrator.__new__(Orchestrator)
    defaults = {
        "_running": True,
        "_interaction_count": 1,
        "_interaction_lock": threading.Lock(),  # process() 内 _interaction_count 递增所需
        "_last_context_warning": None,
        "_last_was_template": False,
        "_session_id": "test-stage5",
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
        # D7 规划段依赖（默认关闭 = 旧行为；测试覆盖时开启）
        "_planning_enabled": False,
        "_planner": None,
        "_needs_planning": lambda x: False,
        "_config": {"planning": {"timeout_seconds": 30}},
        # 直落 LLM 步骤所需的关键路由桩（避免真实语义/拒识/模板逻辑干扰）
        "_update_dst_after_route": MagicMock(),
        "_semantic_layer_match": MagicMock(return_value=None),
        "_should_reject": MagicMock(return_value=(False, "")),
        "_load_reject_config": MagicMock(return_value={"threshold": 0.3}),
        "check_health": MagicMock(return_value=[]),
        "_learn_workflow_from_interaction": MagicMock(),
    }
    for k, v in defaults.items():
        setattr(orch, k, v)
    for k, v in overrides.items():
        setattr(orch, k, v)
    return orch


def _planning_orch(**overrides):
    """构造启用规划且复杂任务判定的 orchestrator（D7 主路径）"""
    planner = AsyncMock()
    planner.chat.return_value = ChatResult(response="规划引擎响应")
    base = {
        "_planning_enabled": True,
        "_planner": planner,
        "_needs_planning": lambda x: True,
        "_config": {"planning": {"timeout_seconds": 30}},
    }
    base.update(overrides)
    return _make_test_orch(**base)


# ─────────────────────── 评估标准 1：复杂任务走规划链路 ───────────────────────

@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestPlanningPath:
    """复杂任务（或 planning_mode=True）→ 规划引擎处理，响应覆盖 LLM 直答"""

    def test_complex_task_uses_planning(self):
        """语义判定复杂任务 → 规划引擎响应覆盖直答；planning layer 计数 +1"""
        orch = _planning_orch()
        result = orch.process("帮我完成一个多步骤任务")

        assert result["success"] is True
        assert result["data"] == "规划引擎响应", "复杂任务响应应来自规划引擎"
        assert result["metadata"].get("used_planning") is True, "响应应标识 used_planning"
        orch._planner.chat.assert_called_once()
        assert _intent_layer_counts.get("planning") == 1

    def test_explicit_planning_mode_forces_planning(self):
        """显式 planning_mode=True 时即使非复杂任务也走规划"""
        orch = _planning_orch(_needs_planning=lambda x: False)
        result = orch.process("你好", planning_mode=True)

        assert result["data"] == "规划引擎响应"
        assert result["metadata"].get("used_planning") is True
        orch._planner.chat.assert_called_once()
        assert _intent_layer_counts.get("planning") == 1

    def test_disabled_planning_keeps_old_behavior(self):
        """planning.enabled=false（默认）→ 不调用规划引擎，保持 LLM 直答"""
        orch = _make_test_orch()  # _planning_enabled=False, _planner=None
        result = orch.process("帮我完成一个多步骤任务")

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应"
        assert _intent_layer_counts.get("planning") is None


# ─────────────────────── 评估标准 2/4：逃生通道（失败/空响应/超时） ───────────────────────

@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestPlanningFallback:
    """规划失败/超时/空响应 → 回退原 LLM 直答响应"""

    def test_planning_exception_falls_back(self):
        """规划调用抛异常 → 回退直答，不记 planning layer"""
        orch = _planning_orch()
        orch._planner.chat.side_effect = RuntimeError("planning engine crash")
        result = orch.process("帮我完成一个多步骤任务")

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应", "规划异常应回退 LLM 直答"
        assert result["metadata"].get("used_planning") is None, "回退路径不标 used_planning"
        assert _intent_layer_counts.get("planning") is None

    def test_planning_empty_response_falls_back(self):
        """规划返回空响应/None → 回退直答"""
        orch = _planning_orch()
        orch._planner.chat.return_value = None
        result = orch.process("帮我完成一个多步骤任务")

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应"

    def test_planning_timeout_falls_back_within_budget(self):
        """规划超时（timeout_seconds=0.1）→ wait_for 中断回退直答，链路耗时受控"""
        async def _hanging(*a, **k):
            await asyncio.sleep(2)
            return None

        orch = _planning_orch(_config={"planning": {"timeout_seconds": 0.1}})
        orch._planner.chat.side_effect = _hanging

        start = time.monotonic()
        result = orch.process("帮我完成一个多步骤任务")
        elapsed = time.monotonic() - start

        assert result["success"] is True
        assert result["data"] == "LLM 直答响应", "规划超时应回退直答"
        assert elapsed < 1.5, f"超时中断应生效（耗时 {elapsed:.2f}s，不应等满挂起 2s）"


# ─────────────────────── 评估标准 3：PLANNING_ENABLED 灰度回滚 ───────────────────────

class TestPlanningEnvRollback:
    """PLANNING_ENABLED 环境变量覆盖 config（false 一键回退旧行为）"""

    def _run_initialize(self, planning_available, env_value, cfg_enabled):
        """执行 _initialize_planning_engine 并返回 (planning_enabled, complexity_threshold)"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, "__init__", lambda self, config=None: None):
            mgr = LifecycleManager()
            mgr._config = {"planning": {"enabled": cfg_enabled, "complexity_threshold": 2.0}}
            mgr._llm = MagicMock()
            mgr._memory = MagicMock()
            with patch.object(mgr, "_register_planning_tools"), \
                 patch("agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE", planning_available), \
                 patch("agent.orchestrator.lifecycle_manager.PlanningCore"), \
                 patch("agent.orchestrator.lifecycle_manager.ReActLoop"), \
                 patch.dict("os.environ", {}, clear=False):
                # env_value=None 表示"未设置环境变量"：os.environ 不接受 None 值，需显式移除
                if env_value is None:
                    os.environ.pop("PLANNING_ENABLED", None)
                else:
                    os.environ["PLANNING_ENABLED"] = env_value
                mgr._initialize_planning_engine()
        return mgr._planning_enabled, mgr._complexity_threshold

    def test_env_false_overrides_cfg_true(self):
        """planning.enabled=true + PLANNING_ENABLED=false → 回退为禁用（旧行为）"""
        enabled, _ = self._run_initialize(True, "false", True)
        assert enabled is False

    def test_env_true_overrides_cfg_false(self):
        """planning.enabled=false + PLANNING_ENABLED=true → 环境变量优先启用"""
        enabled, _ = self._run_initialize(True, "true", False)
        assert enabled is True

    def test_no_env_uses_cfg(self):
        """未设置环境变量 → 用 config.enabled"""
        enabled, threshold = self._run_initialize(True, None, True)
        assert enabled is True
        assert threshold == 2.0  # complexity_threshold 参数化下发

    def test_planning_unavailable_disabled(self):
        """规划模块不可用 → 强制禁用 + 阈值默认 1.0"""
        enabled, threshold = self._run_initialize(False, "true", True)
        assert enabled is False
        assert threshold == 1.0
