"""任务6 容错验证：trace_id 丢失/为空时，三处注入点的日志行为与降级

运行: python scripts/verify_trace_id_empty_fallback.py

场景: 不建立任何 TraceContext / set_trace_id 上下文（get_trace_id() 返回 None），
ReAct / Critic / tool_router 三处注入点应满足：
  1. 不抛异常 —— 注入降级不阻断主流程（【不易】边界保护）
  2. 日志打印 trace_id=（空值），绝不打印 "trace_id=None" —— 容错兜底 or ""
  3. 策略注入仍正常生效 —— 容错 ≠ 吞掉功能

结论速览（日志层面）:
  - [进化][ReAct注入]   trace_id= task_type=general 命中策略 1 条...
  - [进化][Critic注入]  trace_id= scope=critic 命中策略 1 条...
  - [进化][路由注入]    trace_id= 命中策略 1 条...
  - [evolution] 策略命中注入: trace_id= sid-xxx scope_key=...
"""

import asyncio
import logging
import os
import sys
import tempfile
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

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


class _LogCollector(logging.Handler):
    """收集 agent.evolution 命名空间下的 INFO 日志，供断言使用"""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record):
        try:
            self.messages.append(self.format(record))
        except Exception:
            pass


def main():
    print("=" * 72)
    print("【场景】无 TraceContext / set_trace_id（get_trace_id() 返回 None）")
    print("=" * 72)

    # 隔离注入器（临时 json 后端，不触碰真实 data/evolution）
    tmp = tempfile.mkdtemp(prefix="evo_tid_")
    inj = StrategyInjector(storage_path=os.path.join(tmp, "evolution"))
    import agent.evolution.injector as inj_mod
    inj_mod.get_injector = lambda required=False: inj

    # 灌入策略：global（ReAct/Critic 命中）+ tool:web_search（路由命中 + fallback）
    s_global = Strategy(
        strategy_id="sid-no-trace",
        case_id="c-fallback",
        prompt_patch="容错验证策略：trace_id 为空不阻断注入",
        scope="global",
        scores={"safety": 1.0},
    )
    s_tool = Strategy(
        strategy_id="sid-fallback",
        case_id="c-fallback",
        prompt_patch="web_search 失败时改用备用路径",
        scope="tool:web_search",
        param_patch={"fallback_tools": ["web_scrape"]},
        scores={"safety": 1.0},
    )
    inj._strategies.extend([s_global, s_tool])
    inj._save()
    print(f"  隔离注入器已就绪: {inj.storage_path}（策略 {len(inj.list_strategies())} 条）")

    # 收集日志（挂在 agent.evolution 命名空间上）
    collector = _LogCollector()
    logger = logging.getLogger("agent.evolution")
    logger.addHandler(collector)
    try:
        # 1. tool_router 注入（无 trace_id 上下文）
        print("\n  ▶ tool_router.get_tools_for_input（无 trace_id）")
        from agent import tool_router as tr
        tools = tr.get_tools_for_input("帮我搜索最新新闻", max_tools=50)
        check("路由注入不抛异常且备用工具生效", "web_scrape" in tools, str(tools))

        # 2. ReAct 注入
        print("\n  ▶ ReActLoop._think（无 trace_id）")
        from planning.react import ReActLoop

        captured = {}

        async def _chat(messages):
            captured["prompt"] = messages[0]["content"]
            return '{"reasoning":"ok","action_type":"finish","result":"完成"}'

        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = _chat
        planner = type("P", (), {})()
        planner.llm = mock_llm
        planner.tool_registry = type("T", (), {"list_tools": lambda self: []})()
        loop = ReActLoop(planner, reflector=None, max_iterations=3)
        asyncio.run(loop._think("网络检索任务", {}, []))
        check("ReAct 注入不抛异常且策略入 prompt",
              "[策略 #sid-no-trace]" in captured.get("prompt", ""),
              captured.get("prompt", "")[-200:])

        # 3. Critic 注入
        print("\n  ▶ CriticEvaluator.evaluate（无 trace_id）")
        from agent.cognitive.critic import CriticEvaluator

        evaluator = CriticEvaluator(threshold=70)
        result = evaluator.evaluate(
            user_query="请帮我写一段代码",
            response="我无法完成这个请求，因为权限不足",
            context={},
        )
        check("Critic 注入不抛异常且策略进 feedback",
              any("[策略 #sid-no-trace]" in f for f in result.feedback),
              str(result.feedback))
    finally:
        logger.removeHandler(collector)

    # ── 日志断言：trace_id 为空时打印空值，绝不为 None ──
    print("\n  ▶ 日志断言（trace_id 空值行为）")
    logs = "\n".join(collector.messages)
    check("日志中无 trace_id=None", "trace_id=None" not in logs,
          "出现 trace_id=None 说明 or '' 兜底失效")
    hit_lines = [m for m in collector.messages if "策略命中注入" in m]
    check("命中日志存在（注入链路可追溯）", len(hit_lines) >= 2, str(len(hit_lines)))
    check("命中日志 trace_id= 后为空值（非 None）",
          all("策略命中注入: trace_id= " in m for m in hit_lines),
          "\n" + "\n".join(hit_lines[:3]))
    react_lines = [m for m in collector.messages if "ReAct注入" in m]
    check("ReAct 注入日志 trace_id= 为空值（非 None）",
          all("trace_id= task_type=" in m for m in react_lines),
          "\n" + "\n".join(react_lines[:2]))

    # 打印关键日志行供人工核对
    print("\n  ── 关键日志行（trace_id 均为空值） ──")
    for m in collector.messages:
        if any(k in m for k in ("命中排查", "策略命中注入", "ReAct注入", "Critic注入", "路由注入")):
            print(f"    {m}")

    print("\n" + "=" * 72)
    print(f"结果: PASS {OK} / FAIL {FAIL}")
    print("=" * 72)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
