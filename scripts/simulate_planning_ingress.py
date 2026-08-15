"""模拟复杂任务输入，本地验证规划引擎接入（D7）的路由判定与降级逃生通道

用途：
- 构造复杂/简单任务输入，走 Orchestrator.process() 真实链路（复用测试 mock 前置层），
  观察路由判定（_needs_planning 真实逻辑）与降级路径是否符合预期。
- 验证 orchestrator.py 规划入口/成功/回退路径的详细日志（stdout 可见）。

场景覆盖（阶段5 评估标准）：
  S1 复杂任务 + 规划成功          → 响应来自规划引擎，used_planning=True
  S2 复杂任务 + 规划抛异常         → 逃生通道降级 LLM 直答
  S3 复杂任务 + 规划超时(0.1s)     → 逃生通道降级直答，耗时受控
  S4 简单任务                     → 路由判定跳过规划（LLM 直答）
  S5 灰度关闭(_planning_enabled=False) + 复杂任务 → 真实 _needs_planning 返回 False，走旧行为
  S6 显式 planning_mode=True + 简单输入 → 强制进入规划引擎

用法: python scripts/simulate_planning_ingress.py
"""
import asyncio
import logging
import os
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from agent.digital_life import DigitalLife  # noqa: E402  (DigitalLife 含 TaskDispatcher._needs_planning 真实实现)
from agent.guardrails.input_guard import GuardAction, GuardResult  # noqa: E402
from agent.guardrails.output_guard import OutputResult  # noqa: E402
from agent.monitoring.prometheus import reset_intent_layer_counts  # noqa: E402
from planning.core import ChatResult  # noqa: E402

COMPLEX_INPUT = "帮我分析整个系统的流程：第一步检查配置文件，第二步分析数据库结构，然后生成监控报告"
SIMPLE_INPUT = "你好，最近怎么样"


def _make_orch(**overrides):
    """复用测试 _make_test_orch 模式，但【不】setattr _needs_planning——
    让它解析到 TaskDispatcher 真实实现（DigitalLife 继承链）"""
    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")
    behavior.profile.enable_reflection = False

    orch = DigitalLife.__new__(DigitalLife)
    defaults = {
        "_running": True,
        "_interaction_count": 1,
        "_interaction_lock": threading.Lock(),
        "_last_context_warning": None,
        "_last_was_template": False,
        "_session_id": "sim-planning",
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
        "_current_mode": MagicMock(value="sim_mode"),
        "_persona_injector": None,
        "_persona_extractor": None,
        "_is_skill_enabled": lambda x: False,
        # D7 规划段（默认关闭 = 旧行为；场景覆盖时开启）
        "_planning_enabled": False,
        "_planner": None,
        "_complexity_threshold": 1.0,
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


def _planner_ok(response_text="规划引擎响应", plan_summary=None, used_planning=True):
    planner = AsyncMock()
    planner.chat.return_value = ChatResult(
        response=response_text,
        used_planning=used_planning,
        plan_summary=plan_summary or {"steps": 3, "mode": "decompose"},
    )
    return planner


def _run_scenario(name, user_input, orch, **process_kwargs):
    """执行单个场景并返回断言结果"""
    reset_intent_layer_counts()
    t0 = time.perf_counter()
    try:
        resp = orch.process(user_input, trace_id="sim-%s" % name, **process_kwargs)
    except Exception as e:  # 场景断言失败时便于定位
        print("  [EXCEPTION] %s: %s" % (type(e).__name__, e))
        return False
    dur_ms = (time.perf_counter() - t0) * 1000
    meta = (resp or {}).get("metadata", {}) or {}
    data = (resp or {}).get("data", "")
    used = meta.get("used_planning", False)
    summary = meta.get("plan_summary")
    print("  response(data)      : %s" % (data[:60] or "<空>"))
    print("  used_planning       : %s" % used)
    print("  plan_summary        : %s" % ("有" if summary else "无"))
    print("  process 耗时        : %.1fms" % dur_ms)
    return resp, data, used, dur_ms


def main() -> int:
    results = []
    print("=" * 72)
    print("模拟复杂任务输入 → 规划引擎接入（D7）路由与降级验证")
    print("=" * 72)

    # ── S1: 复杂任务 + 规划成功 ──
    print("\n[S1] 复杂任务 + 规划成功（真实 _needs_planning 判定）")
    orch = _make_orch(_planning_enabled=True, _planner=_planner_ok(),
                      _config={"planning": {"timeout_seconds": 30}})
    _, data, used, _ = _run_scenario("S1", COMPLEX_INPUT, orch)
    ok = (data == "规划引擎响应") and (used is True)
    results.append(("S1 复杂任务走规划链路", ok))

    # ── S2: 复杂任务 + 规划异常 → 降级直答 ──
    print("\n[S2] 复杂任务 + 规划引擎抛异常 → 逃生通道降级")
    planner = AsyncMock()
    planner.chat.side_effect = RuntimeError("planning core crash")
    orch = _make_orch(_planning_enabled=True, _planner=planner,
                      _config={"planning": {"timeout_seconds": 30}})
    _, data, used, _ = _run_scenario("S2", COMPLEX_INPUT, orch)
    ok = (data == "LLM 直答响应") and (used is not True)
    results.append(("S2 规划异常降级直答", ok))

    # ── S3: 复杂任务 + 规划超时(0.1s) → 降级直答、耗时受控 ──
    print("\n[S3] 复杂任务 + 规划超时（timeout_seconds=0.1，引擎挂起 2s）")
    async def _hanging_chat(user_input, ctx):
        await asyncio.sleep(2)
        return ChatResult(response="too late")
    planner = MagicMock()
    planner.chat = _hanging_chat
    orch = _make_orch(_planning_enabled=True, _planner=planner,
                      _config={"planning": {"timeout_seconds": 0.1}})
    _, data, used, dur = _run_scenario("S3", COMPLEX_INPUT, orch)
    ok = (data == "LLM 直答响应") and (used is not True) and (dur < 1500)
    results.append(("S3 规划超时降级且耗时受控", ok))

    # ── S4: 简单任务 → 路由判定跳过规划 ──
    print("\n[S4] 简单任务（真实 _needs_planning 应判定不需要规划）")
    print("      （注：简单问候可能命中模板层直答——同样是合法的'跳过规划'路径）")
    orch = _make_orch(_planning_enabled=True, _planner=_planner_ok(),
                      _config={"planning": {"timeout_seconds": 30}})
    _, data, used, _ = _run_scenario("S4", SIMPLE_INPUT, orch)
    ok = (used is not True)
    results.append(("S4 简单任务跳过规划", ok))

    # ── S5: 灰度关闭（PLANNING_ENABLED=false 等价）→ 复杂任务仍走旧行为 ──
    print("\n[S5] 灰度关闭（_planning_enabled=False）+ 复杂任务 → 真实判定应返回 False")
    orch = _make_orch(_planning_enabled=False, _planner=_planner_ok(),
                      _config={"planning": {"timeout_seconds": 30}})
    _, data, used, _ = _run_scenario("S5", COMPLEX_INPUT, orch)
    ok = (data == "LLM 直答响应") and (used is not True)
    results.append(("S5 灰度关闭走旧行为", ok))

    # ── S6: 显式 planning_mode=True + 简单输入 → 强制进入规划 ──
    print("\n[S6] 显式 planning_mode=True + 简单输入 → 强制规划")
    orch = _make_orch(_planning_enabled=True, _planner=_planner_ok(),
                      _config={"planning": {"timeout_seconds": 30}})
    _, data, used, _ = _run_scenario("S6", SIMPLE_INPUT, orch, planning_mode=True)
    ok = (data == "规划引擎响应") and (used is True)
    results.append(("S6 显式入口强制规划", ok))

    # ── 汇总 ──
    print("\n" + "=" * 72)
    print("结果汇总")
    print("=" * 72)
    all_ok = True
    for name, ok in results:
        all_ok = all_ok and ok
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("-" * 72)
    print("总判定: %s" % ("全部符合预期" if all_ok else "存在不符合项"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
