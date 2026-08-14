"""阶段 4 测试：经验回灌（D17）+ 规划指标埋点（D16）+ 结构化计划摘要（D15）

评估标准对应：
1. 经验回灌：mock LLM 断言 prompt 在经验库非空时含"历史经验"段；空库时不含；
2. 埋点：一次完整 plan+execute 后 planning.plans.total/success/duration_ms 计数符合预期；
3. summary 输出断言；ChatResult.to_dict() 新增字段不破坏既有断言。
"""

import json
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from planning.core import PlanningCore, ChatResult
from planning.models import Plan, PlanState, Task, TaskStatus
from planning.models.action import ActionResult
from planning.reflector import (
    Reflector, Experience, Lesson, format_advice_section, classify_task,
)
from planning.summary import (
    build_plan_summary, build_plan_summary_markdown, build_react_summary,
    build_react_summary_markdown,
)


# ── 经验注入（D17）─────────────────────────────────────────────────────────

def test_format_advice_section_empty():
    """空建议/None 返回空串（调用方不注入，无经验场景零行为变化）"""
    assert format_advice_section(None) == ""
    assert format_advice_section({}) == ""


def test_classify_task_module_function():
    """模块级 classify_task 分类"""
    assert classify_task("检查系统状态") == "query"
    assert classify_task("创建一份报告") == "create"
    assert classify_task("删除临时文件") == "delete"
    assert classify_task("分析数据") == "analyze"
    assert classify_task("修改配置") == "modify"
    assert classify_task("随便说点什么") == "general"


@pytest.mark.asyncio
async def test_decompose_prompt_injects_experience():
    """经验库非空时，分解提示词包含【历史经验】段"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps({
        "subtasks": [
            {"id": "step_1", "description": "第一步", "type": "atomic", "priority": 3, "dependencies": []}
        ],
        "execution_order": ["step_1"],
        "parallel_groups": []
    })
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        core.reflector.experiences.append(Experience(
            id="e1", task_type="create", task_description="创建项目报告",
            success=True, output="成功创建", error=None, timestamp="t"
        ))
        plan = await core.plan("帮我创建一份项目报告")
        assert plan.state == PlanState.READY
        prompt = mock_llm.chat.call_args_list[0][0][0][0]["content"]
        # 注入段独有标记（模板本身不含，专用于断言注入）
        assert "成功模式（历史经验）" in prompt
        assert "创建项目报告" in prompt


@pytest.mark.asyncio
async def test_decompose_prompt_no_experience_no_injection():
    """经验库为空时，分解提示词不含经验段（不改变无经验场景行为）"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps({
        "subtasks": [
            {"id": "step_1", "description": "第一步", "type": "atomic", "priority": 3, "dependencies": []}
        ],
        "execution_order": ["step_1"],
        "parallel_groups": []
    })
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        await core.plan("帮我创建一份项目报告")
        prompt = mock_llm.chat.call_args_list[0][0][0][0]["content"]
        # 经验库为空 → 不注入经验段（无经验场景行为不变）
        assert "成功模式（历史经验）" not in prompt
        assert "常见陷阱（历史教训）" not in prompt


@pytest.mark.asyncio
async def test_think_prompt_injects_experience():
    """ReAct 思考提示词在经验库非空时包含【历史经验】段"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps({
        "reasoning": "分析任务", "action_type": "finish", "result": "任务完成"
    })
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        core.reflector.experiences.append(Experience(
            id="e1", task_type="create", task_description="创建项目报告",
            success=True, output="成功创建", error=None, timestamp="t"
        ))
        result = await core.chat("帮我创建一份报告")
        assert result.used_planning is True
        prompt = mock_llm.chat.call_args_list[0][0][0][0]["content"]
        assert "历史经验" in prompt


@pytest.mark.asyncio
async def test_learn_experience_dedup():
    """同任务（类型+描述+成功标志）重复学习去重"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(config={"max_experiences": 10}, persist_dir=tmp_dir)
        await reflector.learn_from_experience("查看文件状态", ActionResult.success_result(output="ok"))
        await reflector.learn_from_experience("查看文件状态", ActionResult.success_result(output="ok"))
        assert len(reflector.experiences) == 1


@pytest.mark.asyncio
async def test_learn_experience_cap():
    """经验库上限截断：超过 max_experiences 丢弃最旧记录"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(config={"max_experiences": 2}, persist_dir=tmp_dir)
        for desc in ("查看a", "查看b", "查看c"):
            await reflector.learn_from_experience(desc, ActionResult.success_result(output="ok"))
        assert len(reflector.experiences) == 2
        # 最旧的"查看a"被丢弃
        assert reflector.experiences[0].task_description == "查看b"
        assert reflector.experiences[1].task_description == "查看c"


@pytest.mark.asyncio
async def test_learn_lesson_cap():
    """教训库上限截断：超过 max_lessons 丢弃最旧记录"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(config={"max_lessons": 2}, persist_dir=tmp_dir)
        for desc in ("查看a", "查看b", "查看c"):
            await reflector.learn_from_experience(desc, ActionResult.failure_result("失败"))
        assert len(reflector.lessons_db) == 2
        assert reflector.lessons_db[0].task_description == "查看b"


@pytest.mark.asyncio
async def test_tool_failure_lesson_hint():
    """工具失败时从教训库查询同类教训写入 next_hint，下轮思考注入提示词"""
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "调用工具", "action_type": "tool_call",
            "action": {"tool": "bad_tool", "params": {}, "description": ""},
            "confidence": 0.8, "result": None, "next_hint": None,
        }),
        # 任务4（D12）：失败反思 LLM 调用（工具失败后新增强制反思）
        json.dumps({"root_cause": "bad_tool 抛异常", "confidence": 0.8,
                    "repair_actions": ["改用其他工具"], "avoid": ["继续调用 bad_tool"]}),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "成功完成"}),
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        core.reflector.lessons_db.append(Lesson(
            id="l1", task_type="general", task_description="bad_tool 使用",
            failure_point="bad_tool 抛异常", solution="改用其他工具", timestamp="t"
        ))

        def bad_tool():
            raise Exception("boom")
        core.register_tool("bad_tool", bad_tool)

        context = {"session": "test"}  # 非空 dict（chat 内部对空 dict 会重建，mutations 不可见）
        result = await core.chat("帮我完成一个复杂任务", context)
        assert result.used_planning is True
        # next_hint 已写入 context（基于教训的下一步提示）
        assert context.get("_next_hint") is not None
        assert "bad_tool" in context["_next_hint"]
        # 下轮思考提示词包含教训引导段（第 3 次 LLM 调用为第二轮 _think）
        prompt2 = mock_llm.chat.call_args_list[2][0][0][0]["content"]
        assert "下一步提示（基于历史教训）" in prompt2


@pytest.mark.asyncio
async def test_empty_lessons_silent_no_next_hint():
    """空经验库静默（P2）：教训库为空时工具失败不注入 _next_hint，下轮提示词无教训段

    对齐 scripts/verify_lesson_guidance.py 场景 B 口径：显式清空 lessons_db/experiences，
    验证"无同类教训时不注入"行为——不依赖宿主 data/reflection 残留数据。
    """
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "调用工具", "action_type": "tool_call",
            "action": {"tool": "bad_tool", "params": {}, "description": ""},
            "confidence": 0.8, "result": None, "next_hint": None,
        }),
        # 任务4（D12）：失败反思 LLM 调用（无修复建议，验证不注入）
        json.dumps({"root_cause": "boom", "confidence": 0.5,
                    "repair_actions": [], "avoid": []}),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "成功完成"}),
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        # 显式清空经验库（即使 tmp_dir 为空也加固，防未来默认数据注入）
        core.reflector.lessons_db.clear()
        core.reflector.experiences.clear()

        def bad_tool():
            raise Exception("boom")
        core.register_tool("bad_tool", bad_tool)

        context = {"session": "test"}  # 非空 dict（chat 内部对空 dict 会重建，mutations 不可见）
        result = await core.chat("帮我完成一个复杂任务", context)
        assert result.used_planning is True
        # 空库静默：不写入 next_hint
        assert context.get("_next_hint") is None
        # 下轮思考提示词不含教训引导段（第 3 次 LLM 调用为第二轮 _think）
        prompt2 = mock_llm.chat.call_args_list[2][0][0][0]["content"]
        assert "下一步提示（基于历史教训）" not in prompt2


# ── 规划指标埋点（D16）──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_after_execute_plan():
    """一次完整 plan+execute 后，plans.total/success/duration_ms 计数符合预期"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
        core.register_tool("test_tool", lambda: "工具结果")

        plan = await core.plan("使用test_tool")
        del core._active_plans[plan.id]
        executed = await core.execute_plan(plan)
        assert executed.is_success() is True

        metrics = core.get_planning_metrics()
        assert metrics["enabled"] is True
        assert metrics["plans"]["total"] == 1
        assert metrics["plans"]["success"] == 1
        assert metrics["plans"]["failed"] == 0
        assert metrics["plans"]["success_rate"] == 1.0
        assert metrics["duration_ms"]["count"] == 1


@pytest.mark.asyncio
async def test_metrics_disabled():
    """planning.metrics.enabled=false 时埋点静默关闭（零行为变化）"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(config={"metrics": {"enabled": False}, "reflector": {"persist_dir": tmp_dir}})
        assert core.get_planning_metrics() == {"enabled": False}


@pytest.mark.asyncio
async def test_metrics_experience_hit_rate():
    """按任务类型的经验命中率统计"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
        core.reflector.experiences.append(Experience(
            id="e1", task_type="query", task_description="查看状态",
            success=True, output="正常", error=None, timestamp="t"
        ))
        assert core.reflector.get_advice_for_task("查看服务器状态") is not None   # hit
        assert core.reflector.get_advice_for_task("创建一份文档") is None         # miss
        m = core.planning_metrics.get_metrics()
        assert m["experience_hit_rate"]["overall"] == 0.5
        assert m["experience_hit_rate"]["by_task_type"]["query"]["hit_rate"] == 1.0
        assert m["experience_hit_rate"]["by_task_type"]["query"]["queries"] == 1
        # 学习统计同步暴露命中率
        stats = core.reflector.get_learning_stats()
        assert stats["experience_hit_rate"]["total_queries"] == 2
        assert stats["experience_hit_rate"]["total_hits"] == 1


# ── 结构化计划摘要（D15）────────────────────────────────────────────────────

def test_build_plan_summary():
    """build_plan_summary 输出目标/任务/预算/失败原因等结构化字段"""
    plan = Plan(original_task="测试目标", state=PlanState.COMPLETED)
    task = Task(id="t1", description="子任务1", status=TaskStatus.COMPLETED)
    task.started_at = datetime.now() - timedelta(seconds=1)
    task.completed_at = datetime.now()
    plan.add_task(task)
    plan.metadata["budget"] = {"steps": 5, "iterations": 3, "cost": 0.01}

    summary = build_plan_summary(plan)
    assert summary["goal"] == "测试目标"
    assert summary["state"] == "completed"
    assert summary["tasks"][0]["id"] == "t1"
    assert summary["tasks"][0]["duration_ms"] == 1000
    assert summary["budget"]["cost"] == 0.01
    assert summary["failure_reasons"] == []

    md = build_plan_summary_markdown(summary)
    assert "计划目标" in md
    assert "预算消耗" in md


def test_build_react_summary():
    """ReAct 路径结构化摘要（goal/state/tasks）"""
    from planning.models.react import ReActStep, ReActResult
    step = ReActStep(iteration=0, thought="思考", action="动作", observation="观察", success=True)
    result = ReActResult(success=True, result="完成", steps=[step], iterations=1, total_duration_ms=100)
    summary = build_react_summary("任务描述", result)
    assert summary["goal"] == "任务描述"
    assert summary["state"] == "completed"
    assert summary["tasks"][0]["id"] == "react_0"


def test_build_plan_summary_markdown_failure_highlight():
    """失败原因以醒目引用块输出（> **[失败原因]**）"""
    plan = Plan(original_task="测试目标", state=PlanState.FAILED)
    task = Task(id="t1", description="子任务1", status=TaskStatus.FAILED, error="boom")
    task.metadata = {"failure_reason": "逻辑错误"}
    plan.add_task(task)
    md = build_plan_summary_markdown(build_plan_summary(plan))
    assert "> **[失败原因]**" in md
    assert "t1(逻辑错误)" in md


def test_build_plan_summary_markdown_reflection_highlight():
    """反思结论以醒目引用块输出（> **[反思结论]**）"""
    plan = Plan(original_task="测试目标", state=PlanState.COMPLETED)
    plan.metadata["budget"] = {"cost": 0.01}

    class FakeReflector:
        reflection_history = [
            {"type": "plan", "reflection": {"lessons": ["lesson-1", "lesson-2"]}}
        ]

    md = build_plan_summary_markdown(build_plan_summary(plan, reflector=FakeReflector()))
    assert "> **[反思结论]**" in md
    assert "lesson-1; lesson-2" in md


def test_build_react_summary_markdown_failure():
    """ReAct 摘要失败时输出醒目的失败原因行"""
    from planning.models.react import ReActResult
    result = ReActResult(success=False, result=None, steps=[], iterations=1,
                         total_duration_ms=100, error="boom")
    summary = build_react_summary("失败任务", result)
    md = build_react_summary_markdown(summary)
    assert "> **[失败原因]**" in md
    assert "react_loop(执行失败)" in md


@pytest.mark.asyncio
async def test_chat_result_plan_summary():
    """规划模式对话结果携带结构化计划摘要"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps({
        "reasoning": "分析任务", "action_type": "finish", "result": "任务完成"
    })
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        result = await core.chat("帮我完成一个复杂任务")
        d = result.to_dict()
        assert d["plan_summary"] is not None
        assert d["plan_summary"]["goal"] == "帮我完成一个复杂任务"
        assert d["plan_summary"]["state"] == "completed"


def test_chat_result_to_dict_compat():
    """ChatResult.to_dict() 新增 plan_summary 键不破坏既有字段"""
    r = ChatResult(response="回复")
    d = r.to_dict()
    for key in ("response", "used_planning", "plan_id", "iterations", "success", "timestamp", "plan_summary"):
        assert key in d
    assert d["plan_summary"] is None


@pytest.mark.asyncio
async def test_chat_result_response_includes_summary():
    """规划路径响应流追加 markdown 计划摘要（控制台直接可见格式化摘要）"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps({
        "reasoning": "分析任务", "action_type": "finish", "result": "任务完成"
    })
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(llm_service=mock_llm, config={"reflector": {"persist_dir": tmp_dir}})
        result = await core.chat("帮我完成一个复杂任务")
        assert result.used_planning is True
        # response 含摘要分隔线与关键字段（目标/状态）
        assert "\n---\n" in result.response
        assert "**计划目标**" in result.response
        assert "帮我完成一个复杂任务" in result.response
