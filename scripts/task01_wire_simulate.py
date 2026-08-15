"""TASK-01 wire 接线本地模拟脚本（开发/排障用）

构造 4 个模拟场景，驱动真实 Orchestrator.process()（mock 前置层直达 wire 分支），
验证 wire_enabled=true 时规划引擎接入效果与回退铁律，日志含完整判定上下文：

  场景 A: wire_enabled=true + COMPLEX 任务 + 规划成功 → 走规划，跳过 LLM，routed_by=planning
  场景 B: wire_enabled=true + COMPLEX 任务 + 规划抛异常 → 回退 LLM 直答（WARNING 含原因）
  场景 C: wire_enabled=true + COMPLEX 任务 + 规划超时 → 回退 LLM 直答（链路耗时受控）
  场景 D: wire_enabled=false + COMPLEX 任务 → 分支 inert，LLM 直答（ingress 仅 DEBUG）

运行: python scripts/task01_wire_simulate.py
"""
import asyncio
import logging
import os
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock

# 从 scripts/ 目录运行时，项目根不在 sys.path，需显式注入（同 tests/conftest.py 模式）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.guardrails.input_guard import GuardAction, GuardResult  # noqa: E402
from agent.guardrails.output_guard import OutputResult
from agent.monitoring.prometheus import reset_intent_layer_counts
from agent.orchestrator.orchestrator import Orchestrator
from planning.core import ChatResult

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("wire_simulate")

COMPLEX_INPUT = "帮我构建一个分布式系统架构"  # 复杂词命中 → COMPLEX
SIMPLE_INPUT = "帮我完成一个多步骤任务"        # NORMAL < COMPLEX，不触发


def _build_orch(wire_cfg: dict, planner=None):
    """构造直落 wire 分支的 orchestrator（mock 前置层，同 test_planning_wire 模式）"""
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
        "_session_id": "sim-wire",
        "_guardrails_input_guard": MagicMock(check=lambda x: GuardResult(GuardAction.ALLOW)),
        "_guardrails_output_guard": MagicMock(check=lambda x: OutputResult(filtered=x)),
        "_workflow_engine": MagicMock(try_match=lambda x: None),
        "_memory": MagicMock(),
        "_behavior": behavior,
        "_build_body_status": MagicMock(return_value="Body status"),
        "_build_reject_response": MagicMock(return_value="Request rejected"),
        "_call_llm": MagicMock(return_value="【LLM 直答】这是普通对话链路生成的响应"),
        "_call_llm_v2": MagicMock(return_value="【LLM 直答】这是普通对话链路生成的响应"),
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
        "_planner": planner,
        "_planning_enabled": False,
        "_needs_planning": lambda x: False,
        "_config": {"planning": {"timeout_seconds": 30}},
        "_update_dst_after_route": MagicMock(),
        "_semantic_layer_match": MagicMock(return_value=None),
        "_should_reject": MagicMock(return_value=(False, "")),
        "_load_reject_config": MagicMock(return_value={"threshold": 0.3}),
        "check_health": MagicMock(return_value=[]),
        "_learn_workflow_from_interaction": MagicMock(),
        "_load_planning_wire_config": lambda: wire_cfg,
    }
    for k, v in defaults.items():
        setattr(orch, k, v)
    return orch


def _ok_planner():
    p = AsyncMock()
    p.chat.return_value = ChatResult(
        response="【规划引擎】1. 拆解模块: 接入层/核心层/存储层；2. 设计接口契约；3. 分步落地。",
        plan_summary="分 3 步完成分布式系统构建",
    )
    return p


def _crash_planner():
    p = AsyncMock()
    p.chat.side_effect = RuntimeError("模拟规划引擎崩溃")
    return p


def _hanging_planner():
    async def _hang(*a, **k):
        await asyncio.sleep(2)
        return None

    p = AsyncMock()
    p.chat.side_effect = _hang
    return p


def _run_scene(title: str, wire_cfg: dict, input_text: str, planner) -> None:
    reset_intent_layer_counts()
    logger.info("=" * 70)
    logger.info("场景: %s", title)
    logger.info("输入: %s", input_text)
    orch = _build_orch(wire_cfg, planner)
    start = time.monotonic()
    result = orch.process(input_text)
    elapsed = time.monotonic() - start
    logger.info("结果: success=%s | data=%s", result["success"], result["data"])
    logger.info("metadata: %s", result.get("metadata", {}))
    logger.info("耗时: %.2fs", elapsed)
    logger.info("=" * 70)
    print()


def main():
    wire_on = {"enabled": True, "min_complexity": "COMPLEX", "timeout_seconds": 0.5}
    wire_off = {"enabled": False, "min_complexity": "COMPLEX", "timeout_seconds": 0.5}

    # 场景 A：wire 开启 + 复杂任务 + 规划成功
    _run_scene("A. wire=true + COMPLEX + 规划成功 → 走规划引擎", wire_on, COMPLEX_INPUT, _ok_planner())

    # 场景 B：wire 开启 + 复杂任务 + 规划抛异常 → 回退 LLM
    _run_scene("B. wire=true + COMPLEX + 规划异常 → 回退 LLM 直答", wire_on, COMPLEX_INPUT, _crash_planner())

    # 场景 C：wire 开启 + 复杂任务 + 规划超时 → 回退 LLM（timeout=0.5s）
    _run_scene("C. wire=true + COMPLEX + 规划超时(0.5s) → 回退 LLM 直答", wire_on, COMPLEX_INPUT, _hanging_planner())

    # 场景 D：wire 关闭 + 复杂任务 → 分支 inert（ingress 仅 DEBUG，不输出）
    _run_scene("D. wire=false + COMPLEX → 分支 inert，LLM 直答", wire_off, COMPLEX_INPUT, _ok_planner())


if __name__ == "__main__":
    main()
