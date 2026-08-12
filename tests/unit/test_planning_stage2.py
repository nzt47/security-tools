"""阶段 2 重构新增能力测试：统一执行模型 / 并行执行 / 规划验证 / 持久化恢复 / LLM 输出鲁棒性

对应《阶段2_统一执行模型与规划闭环.md》评估标准：
1. 新增测试覆盖：并行（≥2 任务并发完成且结果正确）、验证器 3 类失败场景、
   持久化→重启→恢复（模拟新 PlanningCore 实例读取）、JSON 畸形输入
   （含 markdown 围栏/裸 JSON/带噪文本）。
2. ReAct 反思闭环：reflection_history 有记录且 adjustments 生效（refine 首次激活）。
4. 默认配置行为与重构前一致：parallel_execution=false、planning.storage.enabled 可关闭。
"""

import asyncio
import json
import os
import tempfile
import pytest
from unittest.mock import AsyncMock

from planning.core import PlanningCore, PlanningError
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task
from planning.storage import PlanningStorage
from planning.validator import validate_plan, validate_plan_or_raise, PlanValidationError
from planning.llm_json import extract_json, validate_subtasks, extract_json_with_retry


class TestExecutionRecordUnified:
    """统一执行记录模型：ExecutionRecord 新字段 + 向后兼容（D4/D12）"""

    @staticmethod
    def _mk_record(**kwargs):
        from planning.models.record import ExecutionRecord
        from planning.models.action import Action, ActionResult
        return ExecutionRecord(
            step=1,
            task_id="t1",
            action=Action.llm_action(prompt="p", description="思考"),
            result=ActionResult(success=True, output="o"),
            **kwargs,
        )

    def test_to_dict_backward_compatible_without_new_fields(self):
        """重构前用法（不传新字段）：to_dict() 含 thought/observation 键且不抛错"""
        d = self._mk_record().to_dict()
        assert d["step"] == 1
        assert d["task_id"] == "t1"
        assert "thought" in d
        assert d["thought"] == ""
        assert "observation" in d  # 未显式赋值时回退 result.observation（与重构前一致）

    def test_to_dict_preserves_new_fields(self):
        d = self._mk_record(thought="思考", observation="观察").to_dict()
        assert d["thought"] == "思考"
        assert d["observation"] == "观察"

    @pytest.mark.asyncio
    async def test_react_steps_write_thought_observation(self):
        """ReAct 路径写入统一执行记录且携带 thought/observation（D4 双路径共享）"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "reasoning": "直接完成",
            "action_type": "finish",
            "result": "任务完成",
        })
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(
                llm_service=mock_llm,
                config={"reflector": {"persist_dir": tmp_dir}},
            )
            await core.chat("帮我完成一个复杂的任务")
            records = core.executor.execution_history
            assert len(records) > 0
            for r in records:
                d = r.to_dict()
                assert "thought" in d
                assert "observation" in d


class TestParallelExecution:
    """并行执行（D5）：默认串行、配置开启后并行、结果正确"""

    def test_parallel_disabled_by_default(self):
        """评估标准 4：parallel_execution 默认 false（默认行为与重构前一致）"""
        assert PlanExecutor(ToolRegistry()).parallel_execution is False

    @pytest.mark.asyncio
    async def test_parallel_batch_completes_correctly(self):
        """评估标准 1：≥2 互不依赖任务并发完成且结果正确"""
        registry = ToolRegistry()
        events = []

        async def tool_a():
            events.append("start_a")
            await asyncio.sleep(0.1)
            events.append("end_a")
            return "A"

        async def tool_b():
            events.append("start_b")
            await asyncio.sleep(0.1)
            events.append("end_b")
            return "B"

        registry.register("ta", tool_a)
        registry.register("tb", tool_b)
        executor = PlanExecutor(registry, config={"parallel_execution": True})

        plan = Plan(original_task="并行任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用ta"))
        plan.add_task(Task(id="b", description="调用tb"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        assert str(result.get_task("a").result) == "A"
        assert str(result.get_task("b").result) == "B"
        assert events.index("start_b") < events.index("end_a")

    @pytest.mark.asyncio
    async def test_serial_default_executes_sequentially(self):
        """默认串行：互不依赖任务也按序执行（start_b 必在 end_a 之后）"""
        registry = ToolRegistry()
        events = []

        async def tool_a():
            events.append("start_a")
            await asyncio.sleep(0.05)
            events.append("end_a")

        async def tool_b():
            events.append("start_b")
            await asyncio.sleep(0.05)
            events.append("end_b")

        registry.register("ta", tool_a)
        registry.register("tb", tool_b)
        executor = PlanExecutor(registry)  # 未配置 → 默认串行

        plan = Plan(original_task="串行任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用ta"))
        plan.add_task(Task(id="b", description="调用tb"))
        plan.state = PlanState.READY

        await executor.execute_plan(plan)
        assert events.index("start_b") > events.index("end_a")


class TestValidator:
    """规划验证器（D11）：三类失败场景 + core 入口集成"""

    def test_dangling_dependency_detected(self):
        plan = Plan(original_task="悬空依赖", state=PlanState.READY)
        plan.add_task(Task(id="a", description="x", dependencies=["ghost"]))
        issues = validate_plan(plan)
        assert any(i.code == "dangling_dependency" for i in issues)

    def test_circular_dependency_detected(self):
        plan = Plan(original_task="循环依赖", state=PlanState.READY)
        plan.add_task(Task(id="a", description="x", dependencies=["b"]))
        plan.add_task(Task(id="b", description="x", dependencies=["a"]))
        issues = validate_plan(plan)
        assert any(i.code == "circular_dependency" for i in issues)

    def test_empty_description_detected(self):
        plan = Plan(original_task="空描述", state=PlanState.READY)
        plan.add_task(Task(id="a", description="   "))
        issues = validate_plan(plan)
        assert any(i.code == "empty_description" for i in issues)

    def test_clean_plan_passes(self):
        plan = Plan(original_task="正常计划", state=PlanState.READY)
        plan.add_task(Task(id="a", description="x"))
        plan.add_task(Task(id="b", description="y", dependencies=["a"]))
        assert validate_plan(plan) == []

    def test_validate_plan_or_raise_aggregates_messages(self):
        plan = Plan(original_task="坏计划", state=PlanState.READY)
        plan.add_task(Task(id="a", description="", dependencies=["ghost"]))
        with pytest.raises(PlanValidationError) as ei:
            validate_plan_or_raise(plan)
        assert "依赖" in str(ei.value)

    @pytest.mark.asyncio
    async def test_core_plan_rejects_dangling_dependency(self):
        """core.plan() 入口：LLM 分解出悬空依赖 → 标记 FAILED + PlanningError（创建期拦截）"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "subtasks": [
                {"id": "a", "description": "任务A", "type": "atomic", "priority": 3, "dependencies": []},
                {"id": "b", "description": "任务B", "type": "atomic", "priority": 3,
                 "dependencies": ["ghost"]},
            ],
            "execution_order": ["a", "b"],
            "parallel_groups": [],
        })
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(
                llm_service=mock_llm,
                config={"reflector": {"persist_dir": tmp_dir}},
            )
            with pytest.raises(PlanningError) as ei:
                await core.plan("复杂任务")
            assert "验证失败" in str(ei.value)

    @pytest.mark.asyncio
    async def test_core_execute_rejects_invalid_plan(self):
        """core.execute_plan() 入口：非法计划 → FAILED + error 指明原因（不进入执行期卡死）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
            plan = Plan(original_task="坏计划", state=PlanState.READY)
            plan.add_task(Task(id="a", description="x", dependencies=["ghost"]))
            plan.state = PlanState.READY
            result = await core.execute_plan(plan)
            assert result.state == PlanState.FAILED
            assert "依赖" in (result.error or "")


class TestPersistenceRecovery:
    """持久化-重启-恢复（D9）：存储开关 / 新实例恢复 / metadata 往返 / 转换历史落库"""

    @pytest.mark.asyncio
    async def test_storage_disabled_switch(self):
        """评估标准 4：planning.storage.enabled=false 时存储整体关闭（行为与重构前一致）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(config={
                "reflector": {"persist_dir": tmp_dir},
                "planning": {"storage": {"enabled": False}},
            })
            assert core.db is None
            assert core.executor.persistence is None
            plan = Plan(original_task="x", state=PlanState.READY)
            # 落库调用静默跳过（不抛异常），路径返回值保持调用方语义
            assert core.save_plan_checkpoint(plan) == core.persist_db_path
            assert core._load_plans_from_disk() == {}

    @pytest.mark.asyncio
    async def test_restart_recovers_plan_and_metadata(self):
        """评估标准 1：持久化→重启→恢复（模拟新 PlanningCore 实例读取）且 metadata 往返一致"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = {
                "reflector": {"persist_dir": tmp_dir},
                "planning": {"persist_dir": tmp_dir},
            }
            core1 = PlanningCore(config=cfg)
            plan = await core1.plan("首先打开文件然后保存")
            plan.metadata["parallel_groups"] = [["step_1"], ["step_2"]]
            plan.state = PlanState.EXECUTING  # 模拟未完成计划
            core1.save_plan_checkpoint(plan)

            core2 = PlanningCore(config=cfg)  # 模拟进程重启
            recovered = core2._active_plans.get(plan.id)
            assert recovered is not None
            assert recovered.state == PlanState.EXECUTING
            assert recovered.metadata.get("parallel_groups") == [["step_1"], ["step_2"]]

    def test_transition_history_persisted(self):
        """状态转换历史增量落库（可独立读取校验）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = PlanningStorage(os.path.join(tmp_dir, "plans.db"))
            storage.record_transition(
                plan_id="p1", from_state="ready", to_state="executing", reason="开始执行")
            history = storage.get_transition_history(plan_id="p1")
            assert len(history) == 1
            assert history[0]["to_state"] == "executing"
            assert history[0]["reason"] == "开始执行"
            storage.close()

    @pytest.mark.asyncio
    async def test_completed_plan_excluded_from_recovery(self):
        """D9 恢复正确性：终态落库 + EXECUTING 幂等转换 + 重启排除已完成计划。

        此前 plans.state 停留 READY（仅 transition_history 落库），而恢复状态
        集合含 READY，崩溃恢复会把已完成的计划误判为未完成恢复；且恢复的
        EXECUTING 计划重新执行时 EXECUTING->EXECUTING 非法转换被误判失败。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = {
                "reflector": {"persist_dir": tmp_dir},
                "planning": {"persist_dir": tmp_dir},
            }

            async def fake_tool(*args, **kwargs):
                return "ok"

            core1 = PlanningCore(config=cfg)
            core1.tool_registry.register("test_tool", fake_tool)
            plan = await core1.plan("使用test_tool")
            plan.state = PlanState.EXECUTING  # 模拟执行中崩溃
            core1.save_plan_checkpoint(plan)

            core2 = PlanningCore(config=cfg)  # 模拟进程重启
            recovered = core2._active_plans.get(plan.id)
            assert recovered is not None
            assert recovered.state == PlanState.EXECUTING
            # 恢复的计划可继续执行到 COMPLETED（幂等放行 EXECUTING，不被误判失败）
            core2.tool_registry.register("test_tool", fake_tool)
            await core2.execute_plan(recovered)
            assert recovered.state == PlanState.COMPLETED

            core3 = PlanningCore(config=cfg)  # 再重启：已完成计划必须被排除
            assert plan.id not in core3._active_plans


class TestLlmJson:
    """LLM 输出解析鲁棒性：markdown 围栏 / 裸 JSON / 带噪文本 / 校验 / 重试"""

    def test_fenced_json_with_lang(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_json_no_lang(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_bare_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_noisy_text_stripped(self):
        resp = '好的，这是结果：\n{"subtasks": [{"id": "s1", "description": "第一步"}]}\n请查收'
        data = extract_json(resp)
        assert data["subtasks"][0]["id"] == "s1"

    def test_invalid_returns_none(self):
        assert extract_json("这根本不是JSON") is None

    def test_validate_subtasks_ok(self):
        assert validate_subtasks({"subtasks": [{"id": "a", "description": "x"}]}) == []

    def test_validate_subtasks_missing_fields(self):
        errors = validate_subtasks({"subtasks": [{"id": ""}]})
        assert any("id" in e for e in errors)
        assert any("description" in e for e in errors)

    @pytest.mark.asyncio
    async def test_retry_once_then_success(self):
        """首次畸形 → 附错误反馈重试 1 次 → 返回成功数据"""
        class FakeLLM:
            """可控 async chat：第 1 次返回围栏 JSON（修正结果），后续返回垃圾（不应被调用）"""
            def __init__(self):
                self.calls = 0

            async def chat(self, messages):
                self.calls += 1
                assert self.calls == 1, "修正成功后不应再次调用 LLM"
                return "修正后的输出：```json\n{\"subtasks\": [{\"id\": \"s1\", \"description\": \"第一步\"}]}\n```"

        llm = FakeLLM()
        data, errors = await extract_json_with_retry("这不是JSON", llm, lambda errs: "请修正")
        assert data is not None
        assert data["subtasks"][0]["id"] == "s1"
        assert llm.calls == 1  # 首次传入即畸形 → 仅重试 1 次调用 LLM

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_errors(self):
        """重试后仍失败 → 返回 None + 错误列表（调用方回退规则分解）"""
        async def fake_chat(messages):
            return "还是不行"

        llm = type("LLM", (), {"chat": fake_chat})()
        data, errors = await extract_json_with_retry("垃圾", llm, lambda errs: "请修正")
        assert data is None
        assert errors


class TestRefineActivation:
    """计划级反思闭环：decomposer.refine() 首次激活（D4 反思闭环）"""

    @pytest.mark.asyncio
    async def test_refine_applies_adjustments_after_execution(self):
        """execute_plan 反思后：refine 依据反馈修改任务；reflection_history 有记录"""
        class Stage2LLM:
            """按 prompt 标识分发：分解 / 反思 / refine 三路 mock（async 可控）"""
            async def chat(self, messages):
                content = messages[0]["content"]
                if "分析以下任务描述" in content:  # 分解
                    return json.dumps({
                        "subtasks": [
                            {"id": "step_1", "description": "echo 任务", "type": "atomic",
                             "priority": 3, "dependencies": []},
                        ],
                        "execution_order": ["step_1"],
                        "parallel_groups": [],
                    })
                if "反思这次计划执行的完整过程" in content:  # 计划级反思
                    return json.dumps({
                        "overall_score": 7.0,
                        "effectiveness": "基本有效",
                        "improvements": ["改进执行顺序"],
                    })
                if "根据反馈优化执行计划" in content:  # refine
                    return json.dumps({
                        "adjustments": [
                            {"task_id": "step_1", "action": "modify",
                             "new_description": "修改后的描述"},
                        ],
                        "reasoning": "反思改进",
                    })
                raise AssertionError(f"未预期 prompt: {content[:60]}")

        mock_llm = Stage2LLM()
        registry = ToolRegistry()
        registry.register("echo", lambda: "ok")

        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(
                llm_service=mock_llm,
                tool_registry=registry,
                config={
                    "reflector": {"persist_dir": tmp_dir},
                    "planning": {"persist_dir": tmp_dir},
                },
            )
            plan = await core.plan("echo 任务", {})
            result = await core.execute_plan(plan)

            assert result.state == PlanState.COMPLETED
            # 评估标准 2：reflection_history 有记录
            stats = core.reflector.get_learning_stats()
            assert stats["total_reflections"] >= 1
            # refine adjustments 生效：任务描述被修改
            assert result.get_task("step_1").description == "修改后的描述"
