"""教训引导闭环验证（阶段 4 / D17 经验回灌）

三个场景（mock LLM，不依赖外部服务）：
  场景 A（命中注入）：lessons_db 预置同类教训 → 工具失败 → query_lessons 命中 →
      next_hint 写入 context → 下轮 _think 提示词包含"下一步提示（基于历史教训）"段。
  场景 B（空库静默）：lessons_db 为空 → 工具失败 → 查询 0 条 → 不注入 next_hint →
      下轮提示词不含教训段（不改变无经验场景行为）。
  场景 C（失败学习闭环）：learn_from_experience 学习失败教训后，同类任务的
      get_advice_for_task 命中（经验命中率计数 +1），验证"失败 → 学习 → 复用"闭环。

用法:
    python scripts/verify_lesson_guidance.py
"""

import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from planning.core import PlanningCore
from planning.models.action import ActionResult
from planning.reflector import Lesson


def _make_core(tmp_dir, lessons=None):
    """构造带 mock LLM 与失败工具的 PlanningCore"""
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "调用工具", "action_type": "tool_call",
            "action": {"tool": "bad_tool", "params": {}, "description": ""},
            "confidence": 0.8, "result": None, "next_hint": None,
        }),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "成功完成"}),
    ]
    core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
    if lessons:
        core.reflector.lessons_db.extend(lessons)

    def bad_tool():
        raise Exception("boom")

    core.register_tool("bad_tool", bad_tool)
    return mock_llm, core


async def scenario_a_lesson_hit():
    """场景 A：教训命中 → next_hint 注入下轮提示词"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        lesson = Lesson(
            id="l1", task_type="general", task_description="bad_tool 使用",
            failure_point="bad_tool 抛异常", solution="改用其他工具", timestamp="t",
        )
        mock_llm, core = _make_core(tmp_dir, lessons=[lesson])
        context = {"session": "A"}
        await core.chat("帮我完成一个复杂任务", context)
        assert context.get("_next_hint"), "next_hint 未写入 context"
        assert "bad_tool" in context["_next_hint"], "next_hint 未包含失败工具信息"
        prompt2 = mock_llm.chat.call_args_list[1][0][0][0]["content"]
        assert "下一步提示（基于历史教训）" in prompt2, "下轮提示词未注入教训段"
        return "PASS: 教训命中 → next_hint 注入下轮提示词"


async def scenario_b_empty_lessons_skip():
    """场景 B：教训库无同类记录 → 静默跳过（不注入，不改变失败语义）

    注意：Reflector 默认 persist_dir=./data/reflection 会加载系统运行积累的
    历史教训（bad_tool 属 general 类可能命中真实教训），故此处显式清空，
    验证"无同类教训时不注入 next_hint"的行为。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        mock_llm, core = _make_core(tmp_dir, lessons=[])
        core.reflector.lessons_db.clear()
        core.reflector.experiences.clear()
        context = {"session": "B"}
        await core.chat("帮我完成一个复杂任务", context)
        assert "_next_hint" not in context, "空库不应写入 next_hint"
        prompt2 = mock_llm.chat.call_args_list[1][0][0][0]["content"]
        assert "下一步提示（基于历史教训）" not in prompt2, "空库不应注入教训段"
        return "PASS: 教训库为空 → 静默跳过（行为不变）"


async def scenario_c_learning_loop():
    """场景 C：失败学习 → 后续同类任务检索命中（经验命中率计数）"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
        core.reflector.lessons_db.clear()
        core.reflector.experiences.clear()
        # 模拟失败学习落库（executor/ReAct 失败路径的统一入口）
        await core.reflector.learn_from_experience(
            "调用 bad_tool 处理失败场景", ActionResult.failure_result("工具执行失败: boom")
        )
        assert len(core.reflector.lessons_db) == 1, "失败教训未落库"
        # 同类任务检索命中：经验命中率计数 +1（queries/hits 均可观测）
        advice = core.reflector.get_advice_for_task("帮我处理一个失败场景")
        assert advice is not None, "学习后同类任务应命中经验"
        stats = core.reflector.get_learning_stats()
        rate = stats["experience_hit_rate"]
        assert rate["total_queries"] == 1 and rate["total_hits"] == 1, f"命中率异常: {rate}"
        return "PASS: 失败学习 → 同类任务检索命中（命中率 1/1）"


async def main():
    print("=" * 60)
    print("教训引导闭环验证（阶段 4 / D17）")
    print("=" * 60)
    results = [
        await scenario_a_lesson_hit(),
        await scenario_b_empty_lessons_skip(),
        await scenario_c_learning_loop(),
    ]
    for r in results:
        print(f"[{r[:4]}] {r}")
    print("=" * 60)
    print(f"全部通过: {len(results)} 个场景")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
