"""任务4 改造验证：失败链路是否已接入失败反思（D12 修复确认）

Part A：ReActLoop 接线——失败步骤后 failure_reflect 被调用（此前为 0）
Part B：真实 Reflector 规则兜底——无 LLM 时产出 root_cause/repair_actions，
       且失败教训沉淀 lessons_db 持久化成功

运行：python scripts/verify_task4_skip_reflection.py
"""
import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector, ReflectionResult
from planning.diagnostics import build_diagnosis


class CountingReflector(Reflector):
    """计数 reflector：记录 step_reflect / failure_reflect 调用"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step_reflect_calls = 0
        self.failure_reflect_calls = 0

    async def step_reflect(self, task, result, context=None, budget_manager=None):
        self.step_reflect_calls += 1
        return ReflectionResult(assessment="ok", confidence=0.8)

    async def failure_reflect(self, task, result, diagnosis, attempts):
        self.failure_reflect_calls += 1
        return None


def bad_high_tool():
    """模拟最近失败链路中的坏工具（lesson_20260813_223057：'运行 bad_high_tool' → boom）"""
    raise RuntimeError("boom")


async def part_a() -> bool:
    """Part A：ReActLoop 接线——失败步骤后 failure_reflect 被调用"""
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "使用工具执行",
            "action_type": "tool_call",
            "action": {"tool": "bad_high_tool", "params": {}, "description": "运行坏工具"},
        }),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "任务结束"}),
    ]

    planner = type("P", (), {})()
    planner.llm = mock_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("bad_high_tool", bad_high_tool)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = CountingReflector(persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        result = await loop.run("运行 bad_high_tool")

        failed_steps = [s for s in result.steps if not s.success]
        print("─" * 64)
        print("Part A：ReActLoop 失败反思接线")
        print(f"  失败步骤数             = {len(failed_steps)}")
        print(f"  step_reflect 调用次数  = {reflector.step_reflect_calls}")
        print(f"  failure_reflect 调用次数 = {reflector.failure_reflect_calls}")
        checks = [
            ("失败步骤确实发生（工具 boom）",
             len(failed_steps) >= 1 and "boom" in failed_steps[0].observation),
            ("失败后 failure_reflect 被调用（D12 修复）",
             reflector.failure_reflect_calls >= 1),
            ("Reflector.failure_reflect 方法存在",
             hasattr(Reflector, "failure_reflect")),
        ]
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        return all(ok for _, ok in checks)


async def part_b() -> bool:
    """Part B：真实 Reflector 规则兜底 + 教训沉淀"""
    print("─" * 64)
    print("Part B：Reflector.failure_reflect 规则兜底与教训沉淀")
    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(persist_dir=tmp_dir)  # 无 LLM → 规则兜底
        from planning.models.action import ActionResult
        from planning.models import Task

        task = Task(id="t_fail", description="运行 bad_high_tool")
        result = ActionResult.failure_result("工具执行失败: boom")
        diagnosis = build_diagnosis(result, attempts=1, tool_name="bad_high_tool")

        print(f"  classify_error('工具执行失败: boom') = {diagnosis.error_type}")
        print(f"  repair_hints = {diagnosis.repair_hints}")

        enhanced = await reflector.failure_reflect(task, result, diagnosis, attempts=1)
        print(f"  root_cause   = {enhanced.root_cause if enhanced else None}")
        print(f"  repair_actions = {enhanced.repair_actions if enhanced else None}")
        print(f"  avoid        = {enhanced.avoid if enhanced else None}")
        print(f"  lessons_db 新增数 = {len(reflector.lessons_db)}")

        lessons_file = os.path.join(tmp_dir, "lessons.json")
        persisted = os.path.exists(lessons_file) and len(reflector.lessons_db) >= 1

        checks = [
            ("错误分类为 unknown（boom 无特征）", diagnosis.error_type == "unknown"),
            ("规则兜底产出 root_cause", enhanced is not None and bool(enhanced.root_cause)),
            ("规则兜底产出 repair_actions（取自 hints 表）",
             enhanced is not None and isinstance(enhanced.repair_actions, list) and len(enhanced.repair_actions) >= 1),
            ("教训沉淀 lessons_db 并持久化", persisted),
        ]
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        return all(ok for _, ok in checks)


async def main() -> None:
    print("=" * 64)
    print("【任务4 改造验证】失败链路反思闭环")
    print("=" * 64)
    ok_a = await part_a()
    ok_b = await part_b()
    print("─" * 64)
    all_pass = ok_a and ok_b
    print(f"总体: {'PASS — 失败反思已接入闭环' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
