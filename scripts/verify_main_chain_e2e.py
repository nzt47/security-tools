"""主入口端到端验证：真实 config.yaml planning 段下发 + 预算/重规划完整链路

模拟 orchestrator/lifecycle_manager 主入口接线方式：
    planning_cfg = config["planning"]            （lifecycle_manager._initialize_planning_engine）
    core = PlanningCore(llm_service, config=planning_cfg)

Phase 1  配置下发：planning.budget（max_seconds/token_price_per_1k）下发 executor 与
         ReActLoop；replan_on_failure / ask_user_timeout 生效
Phase 2  ReAct 主链路：core.chat() 走规划模式（_needs_planning → react_loop.run），
         mock LLM 完成「思考→工具调用→思考→finish」，验证预算记账与结果返回
Phase 3  重规划链路：core.create_plan() + core.execute_plan()，主工具失败 + fallback
         失败 → 触发重规划（refine remove 失败任务）→ 计划修正后成功完成

用法：python scripts/verify_main_chain_e2e.py
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from planning.core import PlanningCore
from planning.models import PlanState, TaskStatus
from agent.error_handler import RecoverableError

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_planning_cfg() -> dict:
    """与 lifecycle_manager 一致：从 config.yaml 取 planning 段"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw.get("planning", {}) or {}


def _register_tools(core: PlanningCore) -> None:
    """注册 e2e 工具：search（成功）、primary/backup（失败）、ok（成功）"""

    def ok_tool(**kwargs):
        return "查询结果: e2e 测试数据"

    def failing_tool(**kwargs):
        raise RecoverableError(f"工具执行失败: {kwargs}")

    def ok2_tool(**kwargs):
        return "收尾成功"

    core.tool_registry.register("search", ok_tool)
    core.tool_registry.register("primary_tool", failing_tool)
    core.tool_registry.register("backup_tool", failing_tool)
    core.tool_registry.register("ok_tool", ok2_tool)


def _phase1(cfg: dict, llm) -> None:
    """配置下发断言：planning.budget 真实生效"""
    print("=" * 70)
    print("Phase 1：真实 config.yaml planning 段配置下发")
    print(f"  加载配置: enabled={cfg.get('enabled')} | replan_on_failure={cfg.get('replan_on_failure')}")
    print(f"  budget 段: {cfg.get('budget')}")

    core = PlanningCore(llm_service=llm, config=cfg)
    ex_budget = core.executor.budget_manager.budget
    rx_budget = core.react_loop.budget_manager.budget

    assert ex_budget.enabled is True, f"executor 预算应开启: {ex_budget}"
    assert ex_budget.max_seconds == 30, f"executor max_seconds 应=30（来自 config.yaml）: {ex_budget.max_seconds}"
    assert core.executor.budget_manager._token_price_per_1k == 0.002
    assert rx_budget.enabled is True and rx_budget.max_seconds == 30, f"react 预算未下发: {rx_budget}"
    assert core.executor.replan_on_failure is True, "replan_on_failure 应来自 config.yaml=true"
    assert core.ask_user_timeout == 300, f"ask_user_timeout 应=300: {core.ask_user_timeout}"

    print(f"  ✓ executor.budget: enabled={ex_budget.enabled} max_seconds={ex_budget.max_seconds} "
          f"token_price_per_1k={core.executor.budget_manager._token_price_per_1k}")
    print(f"  ✓ react_loop.budget: enabled={rx_budget.enabled} max_seconds={rx_budget.max_seconds}")
    print(f"  ✓ executor.replan_on_failure={core.executor.replan_on_failure} | "
          f"ask_user_timeout={core.ask_user_timeout}")
    print("  PASS\n")
    return core


def _phase2(cfg: dict) -> None:
    """ReAct 主链路：core.chat() 规划模式端到端"""
    print("=" * 70)
    print("Phase 2：ReAct 主链路（core.chat → _needs_planning → react_loop.run）")
    calls = []
    llm = _counting_llm(calls, think_responses={
        1: json.dumps({
            "reasoning": "先搜索信息",
            "action_type": "tool_call",
            "action": {"tool": "search", "params": {"query": "e2e"}, "description": "搜索 e2e 数据"},
            "confidence": 0.9,
            "result": "",
        }),
        2: json.dumps({
            "reasoning": "信息已获取，任务完成",
            "action_type": "finish",
            "action": {},
            "confidence": 1.0,
            "result": "分析完成，报告已生成",
        }),
    })
    core = _phase1(cfg, llm)
    _register_tools(core)

    result = asyncio.run(core.chat("帮我完成一个分析流程然后生成报告", {}))
    think_count = sum(1 for c in calls if "action_type" in c)
    assert result.react_result is not None and result.react_result.success, \
        f"ReAct 主链路应成功: {result.react_result}"
    assert "报告已生成" in result.response, f"结果应含完成信号: {result.response}"
    assert think_count == 2, f"思考应调用 2 次（tool_call + finish）: {think_count}"

    snap = core.react_loop.budget_manager.snapshot()
    elapsed = snap.get("elapsed") or 0.0
    print(f"  ✓ _needs_planning 判定为规划模式（结果: {str(result.response)[:50]}...）")
    print(f"  ✓ 思考 LLM 调用次数: {think_count}（tool_call → finish）")
    print(f"  ✓ 预算记账: tokens={snap.get('tokens')} | elapsed={elapsed:.2f}s "
          f"| status={snap.get('status')}")
    print("  PASS\n")


def _phase3(cfg: dict) -> None:
    """重规划链路：create_plan + execute_plan，主/备份工具全失败 → 重规划修正"""
    print("=" * 70)
    print("Phase 3：重规划链路（create_plan + execute_plan，主/备份工具全失败）")
    calls = []
    llm = _counting_llm(
        calls,
        decompose_responses={
            1: json.dumps({
                "subtasks": [
                    {"id": "a", "description": "调用 primary_tool 执行主步骤",
                     "type": "atomic", "priority": 5, "dependencies": [],
                     "fallback_actions": ["backup_tool"]},
                    {"id": "b", "description": "调用 ok_tool 完成收尾",
                     "type": "atomic", "priority": 1, "dependencies": []},
                ],
                "execution_order": ["a", "b"],
                "parallel_groups": [],
            }),
        },
        refine_responses={
            1: json.dumps({
                "adjustments": [{"task_id": "a", "action": "remove"}],
                "reasoning": "任务 a 主/备份工具全部失败，移除后继续收尾",
            }),
        },
    )
    core = _phase1(cfg, llm)
    _register_tools(core)

    plan = asyncio.run(core.plan("帮我构建一个系统流程", {}))
    assert plan.state == PlanState.READY, f"创建后应为 READY: {plan.state}"
    assert len(plan.tasks) == 2

    plan = asyncio.run(core.execute_plan(plan))

    task_b = plan.get_task("b")
    assert "replanned" in plan.metadata, f"应触发重规划: {plan.metadata}"
    assert plan.metadata["replanned"]["failed_task"] == "a"
    assert task_b.status == TaskStatus.COMPLETED, f"任务 b 应完成: {task_b.status}"
    assert plan.state == PlanState.COMPLETED, f"重规划修正后应收尾 COMPLETED: {plan.state}"
    assert sum(1 for c in calls if "分析以下任务描述" in c) == 1, "decompose 应调用 1 次"
    assert sum(1 for c in calls if "根据反馈优化执行计划" in c) == 1, "refine 应调用 1 次"

    print(f"  ✓ 创建计划: 2 个任务（a 高优先级 + b 收尾）")
    print(f"  ✓ 任务 a（priority=5）主工具+fallback 全失败 → 触发重规划")
    print(f"  ✓ replanned.failed_task={plan.metadata['replanned']['failed_task']}（refine 移除失败任务）")
    print(f"  ✓ 任务 b: {task_b.status.value} | 计划收尾: {plan.state.value} | LLM 总调用: {len(calls)}")
    print("  PASS\n")


def _counting_llm(calls: list, think_responses: dict = None,
                  decompose_responses: dict = None, refine_responses: dict = None):
    """mock LLM：按 prompt 特征分流响应，避免 step_reflect/advice 等辅助调用抢占序号

    - 思考（THINKING_PROMPT 含 action_type）→ think_responses 按序
    - 分解（DECOMPOSITION_PROMPT 含「分析以下任务描述」）→ decompose_responses 按序
    - 优化（refine prompt 含「根据反馈优化执行计划」）→ refine_responses 按序
    - 其他（step_reflect/plan_reflect 等）→ "{}" 兜底
    """

    class _MockLLM:
        async def chat(self, messages):
            content = messages[0].get("content", "") if messages else ""
            calls.append(content)
            if think_responses is not None and "action_type" in content:
                idx = sum(1 for c in calls if "action_type" in c)
                return think_responses.get(idx, "{}")
            if decompose_responses is not None and "分析以下任务描述" in content:
                idx = sum(1 for c in calls if "分析以下任务描述" in c)
                return decompose_responses.get(idx, "{}")
            if refine_responses is not None and "根据反馈优化执行计划" in content:
                idx = sum(1 for c in calls if "根据反馈优化执行计划" in c)
                return refine_responses.get(idx, "{}")
            return "{}"

    return _MockLLM()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    cfg = _load_planning_cfg()
    # Phase 2/3 各自独立构造 core，避免共享状态（llm 各自创建）
    _phase2(cfg)
    _phase3(cfg)
    print("=" * 70)
    print("端到端验证全部通过 ✅（真实 config.yaml 配置已应用到 PlanningCore 主链路）")


if __name__ == "__main__":
    main()
