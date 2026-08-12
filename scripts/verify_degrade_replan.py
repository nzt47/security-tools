"""阶段 3（D14）重规划模拟验证：主工具失败 + fallback 工具也失败 → 是否触发重规划

覆盖场景：
A. 高优先级任务（priority=5）：主工具失败 → 配置级降级链失败 → 任务级
   fallback_actions 也失败 → refine 有调整空间（remove 失败任务）→ 计划修正后继续完成
B. 同 A 但 refine 无调整空间（空 adjustments）→ 重规划不可用 → 走中断路径 → 计划失败
C. 低优先级任务（priority=3 < 4）失败 → 不触发重规划（阶段 3 规格仅 priority>=4 触发）

断言方式：
- 状态断言：任务状态 / plan.metadata["replanned"] / refine LLM 调用次数
- 日志断言：收集 planning.executor 日志，校验降级链进度序号与重规划触发点关键行

用法：python scripts/verify_degrade_replan.py
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning.decomposer import TaskDecomposer
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus

EXECUTOR_LOGGER = logging.getLogger("planning.executor")


class LogCapture(logging.Handler):
    """收集指定 logger 的日志消息，供断言降级链/重规划关键日志"""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _setup_executor(adjustments_json: str, task_a_priority: int) -> tuple:
    """构造执行器：主工具/备份工具全失败，ok_tool 成功；decomposer 返回给定 adjustments

    Returns:
        (executor, plan, refine_llm, reflector, task_a) —— task_a 为执行前捕获的引用，
        因重规划可能将其移出 plan.tasks，后续断言需用引用而非 plan.get_task。
    """
    registry = ToolRegistry()

    def failing_tool(**kwargs):
        # 主/备份工具一律失败（模拟外部依赖不可用）
        from agent.error_handler import RecoverableError
        raise RecoverableError(f"工具执行失败: {kwargs}")

    def ok_tool(**kwargs):
        return "收尾成功"

    registry.register("primary_tool", failing_tool)
    registry.register("backup_tool", failing_tool)
    registry.register("ok_tool", ok_tool)

    # refine LLM：返回预置 adjustments（remove 任务 a / 空调整）
    refine_llm = AsyncMock()
    refine_llm.chat.side_effect = [adjustments_json]
    decomposer = TaskDecomposer(llm_service=refine_llm)
    reflector = AsyncMock()

    executor = PlanExecutor(
        registry,
        max_retries=1,
        config={
            # 配置级降级链：主工具 primary_tool 失败 → 尝试 backup_tool
            "degrade_chain": {"primary_tool": ["backup_tool"]},
            "replan_on_failure": True,
        },
        decomposer=decomposer,
        reflector=reflector,
    )

    plan = Plan(original_task="模拟主工具与备份工具全部失败的重规划场景", state=PlanState.READY)
    task_a = Task(
        id="a",
        description="调用 primary_tool 执行主步骤",
        priority=task_a_priority,
        fallback_actions=["backup_tool"],  # 任务级降级链：备份工具也失败
    )
    plan.add_task(task_a)
    plan.add_task(Task(id="b", description="调用 ok_tool 完成收尾", priority=1))
    return executor, plan, refine_llm, reflector, task_a


def _scenario_a() -> None:
    """主工具失败 + fallback 也失败 → 重规划触发 → 计划修正后继续完成"""
    print("=" * 70)
    print("场景 A：主工具失败 + fallback 失败 → 触发重规划 → 计划修正后继续完成")
    capture = LogCapture()
    EXECUTOR_LOGGER.addHandler(capture)
    try:
        adjustments = json.dumps({
            "adjustments": [{"task_id": "a", "action": "remove"}],
            "reasoning": "任务 a 的主/备份工具全部失败，移除该任务并继续收尾",
        })
        executor, plan, refine_llm, reflector, task_a = _setup_executor(adjustments, task_a_priority=5)
        result = asyncio.run(executor.execute_plan(plan))

        task_b = plan.get_task("b")
        assert task_a.status == TaskStatus.FAILED, f"任务 a 应失败: {task_a.status}"
        assert task_a.metadata.get("failure_reason"), f"应有失败归因: {task_a.metadata}"
        assert refine_llm.chat.call_count == 1, f"refine 应被调用 1 次: {refine_llm.chat.call_count}"
        assert "replanned" in plan.metadata, f"应有重规划标记: {plan.metadata}"
        assert plan.metadata["replanned"]["failed_task"] == "a"
        assert task_b.status == TaskStatus.COMPLETED, f"任务 b 应继续执行成功: {task_b.status}"
        assert plan.state == PlanState.COMPLETED, f"重规划后计划应收尾 COMPLETED: {plan.state}"
        reflector.learn_from_experience.assert_called_once()

        msgs = capture.messages
        key_lines = [
            "[D14任务级降级链] 任务 a 主工具重试耗尽，尝试备份工具 1/1: backup_tool",
            "[D14任务级降级链] 任务 a 全部 1 个备份工具失败，保留主错误",
            "[重规划] 高优先级任务 a 失败（priority=5 ≥ 4，尝试重规划）",
            "[重规划] 高优先级任务 a 失败后计划已修正，继续执行",
        ]
        for line in key_lines:
            assert any(line in m for m in msgs), f"缺少关键日志: {line}"
        print(f"  ✓ 任务 a 状态: {task_a.status.value} | failure_reason: {task_a.metadata.get('failure_reason')}")
        print(f"  ✓ refine 调用次数: {refine_llm.chat.call_count} | replanned.failed_task: {plan.metadata['replanned']['failed_task']}")
        print(f"  ✓ 任务 b 状态: {task_b.status.value}（计划未被中断，继续执行）")
        print(f"  ✓ 计划收尾: {plan.state.value}（重规划修正后成功完成）")
        print(f"  ✓ 关键日志均已输出（降级链进度 1/1、重规划触发与修正确认）")
        print("  PASS\n")
    finally:
        EXECUTOR_LOGGER.removeHandler(capture)


def _scenario_b() -> None:
    """refine 无调整空间 → 重规划不可用 → 走中断路径 → 计划失败"""
    print("=" * 70)
    print("场景 B：refine 无调整空间 → 重规划不可用 → 走中断路径")
    capture = LogCapture()
    EXECUTOR_LOGGER.addHandler(capture)
    try:
        adjustments = json.dumps({
            "adjustments": [],
            "reasoning": "没有可调整的任务",
        })
        executor, plan, refine_llm, _, task_a = _setup_executor(adjustments, task_a_priority=5)
        result = asyncio.run(executor.execute_plan(plan))

        task_b = plan.get_task("b")
        assert task_a.status == TaskStatus.FAILED
        assert refine_llm.chat.call_count == 1, f"refine 应被调用: {refine_llm.chat.call_count}"
        assert "replanned" not in plan.metadata, f"无调整空间不应有 replanned: {plan.metadata}"
        assert task_b.status == TaskStatus.PENDING, f"任务 b 应因中断未执行: {task_b.status}"
        assert plan.state == PlanState.FAILED, f"走中断路径应收尾 FAILED: {plan.state}"

        msgs = capture.messages
        assert any("[重规划] refine 无调整空间（任务集未变化），走中断路径" in m for m in msgs), \
            "缺少无调整空间日志"
        assert any("重规划不可用或无调整空间 → 走中断路径" in m for m in msgs), "缺少中断路径日志"
        print(f"  ✓ refine 调用次数: {refine_llm.chat.call_count} | 任务集未变化 → 无 replanned 标记")
        print(f"  ✓ 任务 b: {task_b.status.value}（计划中断未执行）")
        print(f"  ✓ 计划收尾: {plan.state.value} | 关键日志（无调整空间 → 中断路径）均已输出")
        print("  PASS\n")
    finally:
        EXECUTOR_LOGGER.removeHandler(capture)


def _scenario_c() -> None:
    """低优先级任务失败 → 不触发重规划"""
    print("=" * 70)
    print("场景 C：低优先级任务（priority=3 < 4）失败 → 不触发重规划")
    capture = LogCapture()
    EXECUTOR_LOGGER.addHandler(capture)
    try:
        adjustments = json.dumps({
            "adjustments": [{"task_id": "a", "action": "remove"}],
            "reasoning": "即使有调整空间，低优先级也不应触发重规划",
        })
        executor, plan, refine_llm, _, task_a = _setup_executor(adjustments, task_a_priority=3)
        result = asyncio.run(executor.execute_plan(plan))

        assert refine_llm.chat.call_count == 0, f"低优先级不应调用 refine: {refine_llm.chat.call_count}"
        assert "replanned" not in plan.metadata, f"不应有 replanned: {plan.metadata}"
        assert plan.get_task("b").status == TaskStatus.COMPLETED, "低优先级失败不中断后续任务"

        msgs = capture.messages
        assert any("[重规划] 任务 a 失败（priority=3 < 4），不触发重规划" in m for m in msgs), \
            "缺少不触发重规划日志"
        print(f"  ✓ refine 调用次数: {refine_llm.chat.call_count}（未触发重规划）")
        print(f"  ✓ 任务 b: {plan.get_task('b').status.value}（后续任务正常执行）")
        print(f"  ✓ 关键日志（priority < 4 不触发重规划）已输出")
        print("  PASS\n")
    finally:
        EXECUTOR_LOGGER.removeHandler(capture)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    _scenario_a()
    _scenario_b()
    _scenario_c()
    print("=" * 70)
    print("全部场景通过 ✅")


if __name__ == "__main__":
    main()
