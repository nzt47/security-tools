"""D7 缺陷测试：规划引擎接入主链路（"已建未用"已修复，TASK-01）

原缺陷（P1）：orchestrator 聊天主链路从未调用 _planner.chat()（规划引擎"已建未用"）。
TASK-01 修复：planning.wire_enabled 灰度开关 + process() LLM 调用前接线分支。

本测试验证（从"复现缺陷"转为"验证修复"，断言转正）：
1. 生产配置 planning.enabled=true（引擎已启用）；
2. 灰度开关 planning.wire_enabled 默认 false（正式环境未开启前行为与现状等价）；
3. wire_enabled=true + 复杂任务 → orchestrator 确实调用 _planner.chat()（实断言）。
"""
import os
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from agent.guardrails.input_guard import GuardAction, GuardResult
from agent.guardrails.output_guard import OutputResult
from agent.orchestrator.orchestrator import Orchestrator
from planning.core import ChatResult


def _repo_root() -> str:
    """仓库根目录（config.yaml 所在）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _wire_orch(**overrides):
    """直落 wire→LLM 段的测试 orchestrator（与 test_planning_wire 同构）

    【简易】复用 test_planning_stage5_e2e._make_test_orch 模式，mock 前置层；
           默认 _planning_enabled=False 关闭旧"第四步半"段，聚焦验证 wire 分支。
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
        "_session_id": "test-d7",
        "_guardrails_input_guard": MagicMock(check=lambda x: GuardResult(GuardAction.ALLOW)),
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
        "_planner": None,
        "_planning_enabled": False,
        "_needs_planning": lambda x: False,
        "_config": {"planning": {"timeout_seconds": 30}},
        "_update_dst_after_route": MagicMock(),
        "_semantic_layer_match": MagicMock(return_value=None),
        "_should_reject": MagicMock(return_value=(False, "")),
        "_load_reject_config": MagicMock(return_value={"threshold": 0.3}),
        "check_health": MagicMock(return_value=[]),
        "_learn_workflow_from_interaction": MagicMock(),
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


class TestDefectD7:
    """D7：规划引擎应接入主链路（TASK-01 修复后转正）"""

    def test_planning_enabled_in_production_config(self):
        """生产配置 planning.enabled=true（引擎已启用，非"已建未用"）"""
        with open(os.path.join(_repo_root(), "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        assert cfg.get("planning", {}).get("enabled") is True

    def test_wire_default_off_in_production_config(self):
        """灰度开关 planning.wire_enabled 默认 false（未开启前行为与现状等价）"""
        with open(os.path.join(_repo_root(), "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        assert cfg.get("planning", {}).get("wire_enabled") is False

    @patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
    def test_wire_enabled_calls_planner_chat(self):
        """wire_enabled=true + 复杂任务 → orchestrator 调用 _planner.chat()（D7 修复实断言）"""
        planner = AsyncMock()
        planner.chat.return_value = ChatResult(response="规划引擎响应")
        orch = _wire_orch(
            _planner=planner,
            _load_planning_wire_config=lambda: {
                "enabled": True,
                "min_complexity": "COMPLEX",
                "timeout_seconds": 30,
            },
        )
        result = orch.process("帮我构建一个分布式系统架构")

        planner.chat.assert_called_once()
        assert result["success"] is True
        assert result["data"] == "规划引擎响应", "复杂任务响应应来自规划引擎"
        assert result["metadata"].get("routed_by") == "planning"

    @patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
    def test_wire_disabled_does_not_call_planner_chat(self):
        """wire_enabled=false（默认灰度）→ orchestrator 不调用 _planner.chat()"""
        planner = AsyncMock()
        orch = _wire_orch(_planner=planner)  # _load_planning_wire_config 默认 enabled=False
        result = orch.process("帮我构建一个分布式系统架构")

        planner.chat.assert_not_called()
        assert result["success"] is True
        assert result["data"] == "LLM 直答响应"
