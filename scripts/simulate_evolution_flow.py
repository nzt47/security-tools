"""任务6 进化流程模拟演示：失败案例回流 → 策略生成 → 三处注入 → auto_tuner 联动

运行: python scripts/simulate_evolution_flow.py

闭环演示:
  1. 构造模拟失败案例（工具失败 + 权限误判）→ record_failure_case 生成策略入库
  2. 三处注入验证（ReAct/Critic/tool_router）——断言 prompt/feedback/路由结果
     携带 [策略 #id]，且注入节点日志打印 strategy_id
  3. 策略统计与 deprecated 判定
  4. auto_tuner 联动：高失败率工具 → 参数调整建议 → HITL 审批链
"""

import asyncio
import logging
import os
import sys
import tempfile

# scripts/ 下运行时 sys.path[0]=scripts/，注入项目根
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

from agent.evolution.defect_case import build_failure_case
from agent.evolution.injector import StrategyInjector
from agent.evolution.selector import Strategy

OK = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = ""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def seed_strategy(inj, *, prompt_patch, scope, param_patch=None, source="preset"):
    """直接入库一条策略（模拟运营/LLM 预置，绕过筛选）"""
    s = Strategy(
        strategy_id=f"sid-{scope.replace(':','-')}-{abs(hash(prompt_patch)) % 10**5}",
        case_id="c-preset",
        prompt_patch=prompt_patch,
        param_patch=param_patch or {},
        scope=scope,
        source=source,
    )
    inj._strategies.append(s)
    inj._save()
    return s.strategy_id


def main():
    print("=" * 72)
    print("【阶段 0】初始化隔离注入器（tmp 存储，不触碰真实 data/evolution）")
    print("=" * 72)
    tmp = tempfile.mkdtemp(prefix="evo_sim_")
    inj = StrategyInjector(storage_path=os.path.join(tmp, "evolution"))
    # 把模块级 get_injector 指向实例，让 react/critic/tool_router 内部导入拿到它
    import agent.evolution.injector as inj_mod
    inj_mod.get_injector = lambda required=False: inj
    print(f"  注入器存储: {inj.storage_path}")

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("【阶段 1】失败案例回流 → 候选生成 → 安全红线筛选 → 策略入库")
    print("=" * 72)

    # 案例 A：工具失败（web_search 超时，多次重试仍失败）
    diag_a = {
        "error_type": "timeout",
        "error_message": "web_search 请求超时，重试 3 次仍失败",
        "failure_stage": "tool_call",
        "guess_root_cause": "外部服务响应慢",
    }
    case_a = build_failure_case(
        task_type="network_search",
        trace_id="trace-A",
        diagnosis=diag_a,
        failure_type="timeout",
        task_text="帮我搜索最新新闻",
        task_succeeded=False,
        attempts=3,
        steps=5,
    )
    print(f"\n  案例A 四维评分: {case_a.scores}")
    saved_a = inj.record_failure_case(
        case_a,
        repair_hints=["网络工具超时：限制重试次数，失败后切换备用路径"],
        tool_name="web_search",
    )
    check("案例A 生成 tool:web_search 策略入库", len(saved_a) >= 1,
          f"saved={[s.strategy_id for s in saved_a]}")
    for s in saved_a:
        print(f"    入库: {s.strategy_id} scope={s.scope} source={s.source}")

    # 案例 B：权限误判（over_rejection 高分）
    diag_b = {
        "error_type": "permission_denied",
        "error_message": "权限不足，无法完成请求",
        "failure_stage": "response",
    }
    case_b = build_failure_case(
        task_type="general",
        trace_id="trace-B",
        diagnosis=diag_b,
        failure_type="permission_denied",
        task_text="生成周报",
        task_succeeded=False,
        attempts=2,
    )
    print(f"\n  案例B 四维评分: {case_b.scores}")
    check("案例B over_rejection >= 0.8（权限误判）",
          case_b.scores["over_rejection"] >= 0.8, str(case_b.scores))
    saved_b = inj.record_failure_case(
        case_b,
        repair_hints=["避免过度拒绝：权限不足时先尝试降级路径，而非直接拒绝"],
    )
    check("案例B 生成 task_type:general 策略入库", len(saved_b) >= 1,
          f"saved={[s.strategy_id for s in saved_b]}")

    # 预置策略（模拟运营/LLM 落库）：critic 提示 + 工具备用路径
    critic_sid = seed_strategy(
        inj, prompt_patch="避免过度拒绝：区分真正的安全限制与可降级路径",
        scope="critic",
    )
    fb_sid = seed_strategy(
        inj,
        prompt_patch="web_search 失败率高，改用备用路径",
        scope="tool:web_search",
        param_patch={"fallback_tools": ["web_scrape"]},
    )
    print(f"\n  预置策略: critic_sid={critic_sid}, fallback_sid={fb_sid}")

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("【阶段 2】三处运行时注入验证（携带 [策略 #id] + 日志 strategy_id）")
    print("=" * 72)

    # 2.1 ReAct 注入
    print("\n  ▶ ReActLoop._think 注入（task_type:general → classify_task 归一）")
    from planning.react import ReActLoop

    captured = {}

    async def _chat(messages):
        captured["prompt"] = messages[0]["content"]
        return '{"reasoning":"ok","action_type":"finish","result":"完成"}'

    from unittest.mock import AsyncMock

    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = _chat
    planner = type("P", (), {})()
    planner.llm = mock_llm
    planner.tool_registry = type("T", (), {"list_tools": lambda self: []})()
    loop = ReActLoop(planner, reflector=None, max_iterations=3)
    asyncio.run(loop._think("网络检索任务", {}, []))
    prompt = captured.get("prompt", "")
    react_hits = [s.strategy_id for s in saved_b if f"[策略 #{s.strategy_id}]" in prompt]
    check("ReAct prompt 含 [策略 #id]", bool(react_hits),
          f"hits={react_hits} prompt片段={prompt[-220:]}")
    check("ReAct 注入段含【历史经验（策略库）】", "历史经验（策略库）" in prompt)

    # 2.2 Critic 注入
    print("\n  ▶ CriticEvaluator.evaluate 注入（scope=critic）")
    from agent.cognitive.critic import CriticEvaluator

    evaluator = CriticEvaluator(threshold=70)
    result = evaluator.evaluate(
        user_query="请帮我写一段代码",
        response="我无法完成这个请求，因为权限不足",
        context={},
    )
    critic_fb = [f for f in result.feedback if f"[策略 #{critic_sid}]" in f]
    check("Critic feedback 含 [策略 #id]", bool(critic_fb), str(result.feedback))
    if critic_fb:
        print(f"    feedback: {critic_fb[0]}")

    # 2.3 tool_router 注入
    print("\n  ▶ tool_router.get_tools_for_input 注入（tool:web_search → fallback_tools）")
    from agent import tool_router as tr

    tools = tr.get_tools_for_input("帮我搜索最新新闻", max_tools=50)
    check("路由结果追加备用工具 web_scrape", "web_scrape" in tools,
          f"tools={tools}")
    check("路由结果含被路由选中的 web_search", "web_search" in tools)

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("【阶段 3】策略统计与 deprecated 判定")
    print("=" * 72)
    # 用 fallback 策略模拟高失败率：4 次失败（attempt=4 不 deprecated）→ 再 1 次（5 次 <30%）
    for _ in range(4):
        inj.record_strategy_result(fb_sid, success=False)
    s4 = inj.get_strategy(fb_sid)
    check("尝试 4 次（0 成功）未 deprecated", s4.status == "active",
          f"status={s4.status} attempt={s4.attempt_count}")
    inj.record_strategy_result(fb_sid, success=False)
    s5 = inj.get_strategy(fb_sid)
    check("尝试 5 次且成功率 0% → deprecated",
          s5.status == "deprecated", f"status={s5.status}")

    stats = inj.get_strategy_stats()
    print(f"  策略统计: total={stats['total']} active={stats['active']} "
          f"deprecated={stats['deprecated']} success_rate={stats['success_rate']}")
    print(f"  by_tool: {stats['by_tool']}")

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("【阶段 4】auto_tuner 参数联动（高失败率工具 → 参数建议 → HITL 审批）")
    print("=" * 72)
    from agent.auto_tuner import AutoTuner

    # 用案例A生成的 tool:web_search 策略模拟高失败（rate=0.0, attempt>=3）
    sim_sid = saved_a[0].strategy_id
    for _ in range(3):
        inj.record_strategy_result(sim_sid, success=False)
    stats2 = inj.get_strategy_stats()
    ws = stats2["by_tool"].get("web_search", {})
    print(f"  web_search 策略统计: {ws}")
    check("web_search 失败率数据可被统计读取",
          ws.get("attempt", 0) >= 3 and ws.get("rate", 1.0) <= 0.5, str(ws))

    tuner = AutoTuner(storage_path=os.path.join(tmp, "auto_tuning"))
    tuner.initialize()
    suggestion = tuner.generate_strategy_linked_suggestion()
    check("生成参数联动建议（pending）", suggestion is not None
          and suggestion.status == "pending",
          f"got={suggestion is not None}")
    if suggestion:
        print(f"  建议: {suggestion.title}")
        print(f"    proposed_params: {suggestion.proposed_params}")
        print(f"    metadata.source={suggestion.metadata.get('source')} "
              f"high_fail_tools={suggestion.metadata.get('high_fail_tools')}")
        check("建议参数含 tool_max_concurrency 下调",
              "tool_max_concurrency" in suggestion.proposed_params,
              str(suggestion.proposed_params))
        check("建议 metadata.source == evolution",
              suggestion.metadata.get("source") == "evolution")

        # HITL 审批链
        tuner.approve_suggestion(suggestion.suggestion_id, reviewer="simulator")
        applied = tuner.apply_suggestion(suggestion.suggestion_id)
        check("HITL apply 返回快照", isinstance(applied, dict)
              and "snapshot_id" in applied, str(applied))
        print(f"    HITL 已批准并应用，快照: {applied.get('snapshot_id')}")

    report = tuner.generate_strategy_weekly_report()
    check("进化周报产出（objective=evolution）", report is not None
          and report.objective == "evolution", f"objective={getattr(report, 'objective', None)}")
    if report:
        print(f"  周报 metrics_summary: {report.metrics_summary}")

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"结果: PASS {OK} / FAIL {FAIL}")
    print("=" * 72)
    if FAIL:
        print("存在失败断言，请检查上方 [FAIL] 行")
        sys.exit(1)
    print("全部通过：失败案例回流 → 策略生成 → 三处注入（strategy_id 可追溯）"
          "→ deprecated → auto_tuner 联动闭环验证成功")


if __name__ == "__main__":
    main()
