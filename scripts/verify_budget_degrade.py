"""阶段 3 预算/降级逻辑本地验证脚本（模拟 token 消耗与成本数据）

覆盖：
A. token 预算超限 → ReActLoop 停止后续 LLM 调用（call_count 断言）
B. cost 预算超限 → 终止（成本 = tokens/1000 × 单价 折算）
C. budget_ask_user=true → 降级为征求用户（"等待用户输入"信号）
D. 主链路接线：PlanningCore(config) → planning.budget 下发 executor 与 ReActLoop（生产配置生效）
E. budget.enabled=false → 预算整体关闭（回滚开关语义）
F. PlanExecutor steps 预算超限 → 正常收尾返回部分结果
G. BudgetManager + FakeTokenCounter → 模拟 token 消耗与成本数据记账

用法：python scripts/verify_budget_degrade.py
"""

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning.budget import BudgetManager, BudgetStatus, PlanBudget
from planning.core import PlanningCore
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus
from planning.react import ReActLoop


class FakeTokenCounter:
    """模拟 LLM token 计数：每次调用 = 基础量 + 文本长度折算（模拟真实 LLM 消耗数千 token）"""

    def __init__(self, base: int = 2000, chars_per_token: int = 4):
        self.base = base
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        return self.base + len(str(text)) // self.chars_per_token


def _mock_llm(sequence=None):
    """模拟 LLM：返回预置 JSON 序列；默认 10 轮工具调用（触发预算拦截）"""
    llm = AsyncMock()
    if sequence is None:
        sequence = [
            json.dumps({
                "reasoning": f"调用工具 {i}",
                "action_type": "tool_call",
                "action": {"tool": "missing_tool", "params": {}, "description": f"调用{i}"},
            })
            for i in range(10)
        ]
    llm.chat.side_effect = sequence
    return llm


def _scenario_a() -> None:
    """token 预算超限 → 停止 LLM 调用（字符/3 近似记账）"""
    print("=" * 70)
    print("场景 A：token 预算超限 → 停止 LLM 调用")
    llm = _mock_llm()
    planner = MagicMock()
    planner.llm = llm
    planner.tool_registry = ToolRegistry()
    loop = ReActLoop(
        planner, None, max_iterations=10,
        config={"token_budget": 100, "token_price_per_1k": 0.002},
    )
    result = asyncio.run(loop.run("模拟 token 超限任务", {}))
    assert result.success is False, "应超限失败"
    assert "token" in result.error, f"错误应含 token: {result.error}"
    assert llm.chat.call_count == 1, f"超限后不应再调用 LLM，实际 {llm.chat.call_count}"
    snap = result.final_state.get("budget", {})
    print(f"  ✓ LLM 调用次数: {llm.chat.call_count}（超限后未再发起）")
    print(f"  ✓ 累计 tokens: {snap.get('tokens')} > 100 | 成本: ${snap.get('cost')}")
    print(f"  ✓ error: {result.error}")
    print("  PASS\n")


def _scenario_b() -> None:
    """cost 预算超限 → 终止（tokens/1000 × 单价 = 成本）"""
    print("=" * 70)
    print("场景 B：cost 预算超限 → 终止（tokens/1000 × 单价 = 成本）")
    llm = _mock_llm()
    planner = MagicMock()
    planner.llm = llm
    planner.tool_registry = ToolRegistry()
    loop = ReActLoop(
        planner, None, max_iterations=10,
        config={"cost_budget": 0.0009, "token_price_per_1k": 0.002},
    )
    result = asyncio.run(loop.run("模拟成本超限任务", {}))
    assert result.success is False
    assert "成本" in result.error, f"错误应含成本: {result.error}"
    snap = result.final_state.get("budget", {})
    print(f"  ✓ 累计 tokens: {snap.get('tokens')} | 成本: ${snap.get('cost')} > $0.0009")
    print(f"  ✓ error: {result.error}")
    print("  PASS\n")


def _scenario_c() -> None:
    """budget_ask_user=true → 降级为征求用户"""
    print("=" * 70)
    print("场景 C：budget_ask_user=true → 降级为征求用户（等待用户输入）")
    llm = _mock_llm()
    planner = MagicMock()
    planner.llm = llm
    planner.tool_registry = ToolRegistry()
    loop = ReActLoop(
        planner, None, max_iterations=10,
        config={"token_budget": 100, "budget_ask_user": True},
    )
    result = asyncio.run(loop.run("模拟征求用户任务", {}))
    assert result.success is False
    assert "等待用户输入" in result.error, f"应降级为征求用户: {result.error}"
    print(f"  ✓ 超预算降级为征求用户（error={result.error}）")
    print("  PASS\n")


def _scenario_g() -> None:
    """BudgetManager + FakeTokenCounter：模拟 token 消耗与成本数据记账"""
    print("=" * 70)
    print("场景 G：模拟 token 消耗与成本数据记账（FakeTokenCounter 注入）")
    mgr = BudgetManager(
        PlanBudget(max_tokens=6000, max_cost=0.02),
        token_counter=FakeTokenCounter(base=1500),
        token_price_per_1k=0.002,
    )
    for i, text in enumerate(["用户提示词模拟内容" + "x" * 400,
                              "模型回复模拟内容" + "y" * 1200], start=1):
        mgr.record_text(text)
        print(f"  第{i}次往返 → 累计 tokens={mgr.tokens}, 成本=${mgr.cost:.6f}, "
              f"check={mgr.check().value}")
    assert mgr.tokens > 0 and mgr.cost > 0
    print(f"  ✓ 模拟数据记账生效：tokens={mgr.tokens}, cost=${mgr.cost:.6f}")
    print("  PASS\n")


def _scenario_d() -> None:
    """主链路接线：planning.budget 下发 executor 与 ReActLoop"""
    print("=" * 70)
    print("场景 D：主链路接线验证（planning.budget → executor / ReActLoop）")
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(None, config={
            # 模拟 lifecycle_manager 传入的 planning 段
            "budget": {"max_tokens": 3000, "token_price_per_1k": 0.002},
            "replan_on_failure": True,
            "ask_user_timeout_seconds": 60,
            "planning": {"storage": {"enabled": False}, "persist_dir": tmp_dir},
        })
        ex_budget = core.executor.budget_manager.budget
        rx_budget = core.react_loop.budget_manager.budget
        assert ex_budget.max_tokens == 3000, f"executor 预算未下发: {ex_budget}"
        assert rx_budget.max_tokens == 3000, f"react 预算未下发: {rx_budget}"
        assert core.executor.replan_on_failure is True
        assert core.ask_user_timeout == 60
        print("  ✓ executor.budget_manager.budget.max_tokens =", ex_budget.max_tokens)
        print("  ✓ react_loop.budget_manager.budget.max_tokens =", rx_budget.max_tokens)
        print("  ✓ executor.replan_on_failure =", core.executor.replan_on_failure)
        print("  ✓ ask_user_timeout =", core.ask_user_timeout)
    print("  PASS\n")


def _scenario_e() -> None:
    """budget.enabled=false → 整体关闭"""
    print("=" * 70)
    print("场景 E：budget.enabled=false → 预算整体关闭（回滚开关）")
    budget = PlanBudget.from_config({"budget": {"enabled": False, "max_tokens": 10, "max_steps": 1}})
    assert budget.enabled is False, f"应整体关闭: {budget}"
    print("  ✓ enabled =", budget.enabled, "（max_tokens/max_steps 均不限制）")
    print("  PASS\n")


def _scenario_f() -> None:
    """PlanExecutor steps 预算超限 → 正常收尾返回部分结果"""
    print("=" * 70)
    print("场景 F：PlanExecutor steps 预算超限 → 正常收尾返回部分结果")
    registry = ToolRegistry()

    def ok_tool(**kwargs):
        return "成功"

    registry.register("ok_tool", ok_tool)

    executor = PlanExecutor(registry, max_retries=1, config={"budget": {"max_steps": 1}})
    plan = Plan(original_task="预算收尾任务", state=PlanState.READY)
    plan.add_task(Task(id="a", description="ok_tool 执行"))
    plan.add_task(Task(id="b", description="ok_tool 执行"))
    result = asyncio.run(executor.execute_plan(plan))

    status = result.metadata["budget"]["status"]
    assert status == "exceeded_steps", f"status={status}"
    assert result.get_task("a").status == TaskStatus.COMPLETED
    assert result.get_task("b").status == TaskStatus.PENDING
    print(f"  ✓ 超限状态: {status} | 任务a=已完成 | 任务b=未执行（部分结果收尾）")
    print("  PASS\n")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    # 详细日志（DEBUG）落盘 UTF-8，便于本地排查预算/降级分支行为
    log_path = Path("data/health/budget_verify_debug.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logging.getLogger().addHandler(fh)
    print(f"（详细日志将写入 {log_path}）\n")
    _scenario_e()
    _scenario_a()
    _scenario_b()
    _scenario_c()
    _scenario_g()
    _scenario_d()
    _scenario_f()
    print("=" * 70)
    print("全部场景通过 ✅")


if __name__ == "__main__":
    main()
