"""TASK-02 测试：反思沉淀与认知评估上线

评估标准对应（TASK-02_反思沉淀与认知评估上线.md §6）：
1. reflection_persist=true → self_reflect 后检索面出现反思记录（字段符合 schema）；
2. reflection_persist=false（默认）→ 零写入，行为与现状一致；
3. experience_persist=true → execute_plan 成功/失败后 experiences/lessons.json 落盘；
4. critic_evaluation_enabled=true → learning.eval_* 指标递增；评估器异常主链路不受影响；
5. 反思/评估写入失败 → 主链路正常返回（降级验证）。

设计说明（【简易】复用 test_planning_wire._wire_orch 模式：
Orchestrator.__new__ + 注入依赖，用实例属性遮蔽 _load_learning_config 类方法，
隔离真实 config.yaml；核心侧用真实 PlanningCore + tempdir 注入持久化目录）。
"""
import hashlib
import json
import logging
import os
import tempfile
from unittest.mock import ANY, MagicMock, patch

import pytest

from agent.orchestrator.orchestrator import Orchestrator
from planning.core import PlanningCore
from planning.models import Plan, PlanState


def _make_orch(learning_cfg=None, **overrides):
    """构造可触发 self_reflect 的 orchestrator（学习配置由测试注入，隔离 config.yaml）

    【不易】_load_learning_config 是类方法（读真实 config.yaml），
           测试用实例属性遮蔽为 lambda，仅注入 TASK-02 两开关，不依赖真实文件。
    """
    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")
    behavior.profile.enable_reflection = True

    orch = Orchestrator.__new__(Orchestrator)
    defaults = {
        "_interaction_count": 1,
        "_current_mode": MagicMock(value="test_mode"),
        "_memory": MagicMock(),
        "_reflection_history": [],
        "_v2_lifetrace": False,
        "_trace_recorder": None,
        "_vector_memory": None,
        "_load_learning_config": lambda: learning_cfg if learning_cfg is not None else {
            "reflection_persist": False,
            "critic_evaluation_enabled": False,
        },
    }
    for k, v in defaults.items():
        setattr(orch, k, v)
    for k, v in overrides.items():
        setattr(orch, k, v)
    return orch


@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestReflectionPersist:
    """用例 1/2/5：反思产物写入检索面（观察模式）"""

    def test_reflection_persist_enabled_writes_vector(self):
        """reflection_persist=true → self_reflect 后检索面写入记录且字段符合 schema"""
        vec = MagicMock()
        orch = _make_orch(
            learning_cfg={"reflection_persist": True, "critic_evaluation_enabled": False},
            _vector_memory=vec,
        )
        entry = orch.self_reflect("帮我写一份项目报告", "好的，以下是报告内容……")

        vec.add.assert_called_once()
        _, kwargs = vec.add.call_args
        metadata = kwargs["metadata"]
        assert metadata["type"] == "reflection"
        assert metadata["task_id"] == "1"
        assert metadata["input_hash"] == hashlib.sha1("帮我写一份项目报告".encode("utf-8")).hexdigest()[:12]
        assert metadata["score"] == 0.0  # 未启用评估时默认 0.0
        assert metadata["suggestions"] == []
        assert metadata["created_at"] == entry["timestamp"]
        assert "反思" in kwargs["content"]

    def test_reflection_persist_disabled_no_write(self):
        """reflection_persist=false（默认）→ 零写入，行为与现状一致"""
        vec = MagicMock()
        orch = _make_orch(
            learning_cfg={"reflection_persist": False, "critic_evaluation_enabled": False},
            _vector_memory=vec,
        )
        entry = orch.self_reflect("帮我写一份项目报告", "好的，以下是报告内容……")

        vec.add.assert_not_called()
        assert entry["interaction"] == 1, "反思产物仍照常产出（仅不写检索面）"

    def test_reflection_persist_failure_degrades_gracefully(self, caplog):
        """写入检索面失败 → 主链路正常返回（WARNING 降级，不中断）"""
        vec = MagicMock()
        vec.add.side_effect = RuntimeError("vector store down")
        orch = _make_orch(
            learning_cfg={"reflection_persist": True, "critic_evaluation_enabled": False},
            _vector_memory=vec,
        )
        with caplog.at_level(logging.WARNING, logger="agent.orchestrator.orchestrator"):
            entry = orch.self_reflect("写一份总结", "好的，总结如下：……")

        assert entry["interaction"] == 1, "写入失败不应中断反思主链路"
        assert any("反思产物写入失败" in r.getMessage() for r in caplog.records), \
            "降级应有 WARNING 日志记录原因"


@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", True)
class TestCriticEvaluation:
    """用例 4：规则评估上线（保守模式）"""

    def test_critic_eval_enabled_records_metrics(self):
        """critic_evaluation_enabled=true → learning.eval_* 指标递增，响应不被拦截"""
        collector = MagicMock()
        orch = _make_orch(
            learning_cfg={"reflection_persist": False, "critic_evaluation_enabled": True},
            _vector_memory=None,
        )
        with patch("agent.orchestrator.orchestrator.get_metrics_collector", return_value=collector):
            entry = orch.self_reflect("写一份总结", "好的，总结如下：……")

        assert entry["interaction"] == 1
        collector.increment_counter.assert_any_call("learning.eval.total")
        collector.increment_counter.assert_any_call("learning.eval.passed")
        collector.record_latency.assert_any_call("learning.eval.score", ANY)

    def test_critic_eval_exception_does_not_break_main(self, caplog):
        """评估器抛异常 → 主链路不受影响（WARNING 降级，不埋点）"""
        collector = MagicMock()
        orch = _make_orch(
            learning_cfg={"reflection_persist": False, "critic_evaluation_enabled": True},
            _vector_memory=None,
        )
        with patch("agent.orchestrator.orchestrator.get_metrics_collector", return_value=collector), \
             patch("agent.cognitive.reflection.ReflectionEngine.evaluate",
                   side_effect=RuntimeError("eval engine crash")):
            with caplog.at_level(logging.WARNING, logger="agent.orchestrator.orchestrator"):
                entry = orch.self_reflect("写一份总结", "好的，总结如下：……")

        assert entry["interaction"] == 1, "评估异常不应中断反思主链路"
        collector.increment_counter.assert_not_called()
        assert any("规则评估异常" in r.getMessage() for r in caplog.records), \
            "评估异常应有 WARNING 降级日志"


@pytest.mark.asyncio
class TestExperiencePersist:
    """用例 3（+验收补强）：execute_plan 收尾接线 learn_from_experience（观察模式）"""

    async def test_execute_plan_success_persists_experience(self):
        """experience_persist=true → execute_plan 成功后 experiences.json 新增条目"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
            # Reflector 的 persist_dir 只取显式参数（PlanningCore 不转发 config），
            # 测试直接覆盖为临时目录，隔离真实 data/reflection（防污染运行时数据）
            core.reflector.persist_dir = tmp_dir
            core.reflector.experiences.clear()
            core.reflector.lessons_db.clear()
            core.register_tool("test_tool", lambda: "工具结果")

            plan = await core.plan("使用test_tool")
            del core._active_plans[plan.id]
            with patch.object(PlanningCore, "_load_experience_persist_config", return_value=True):
                executed = await core.execute_plan(plan)

            assert executed.is_success() is True
            exp_file = os.path.join(tmp_dir, "experiences.json")
            assert os.path.exists(exp_file), "成功后应落盘 experiences.json"
            with open(exp_file, encoding="utf-8") as f:
                experiences = json.load(f)
            assert len(experiences) == 1
            assert experiences[0]["success"] is True
            assert experiences[0]["task_description"] == "使用test_tool"

    async def test_execute_plan_failure_persists_lesson(self):
        """experience_persist=true → execute_plan 失败后 lessons.json 新增条目"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
            # 同上：显式注入临时持久化目录并清空内存库（防污染真实 data/reflection）
            core.reflector.persist_dir = tmp_dir
            core.reflector.experiences.clear()
            core.reflector.lessons_db.clear()
            plan = Plan(original_task="执行一个必然失败的任务",
                        state=PlanState.FAILED, error="boom")
            with patch.object(PlanningCore, "_load_experience_persist_config", return_value=True):
                await core._record_experience(plan, success=False)

            lessons_file = os.path.join(tmp_dir, "lessons.json")
            assert os.path.exists(lessons_file), "失败后应落盘 lessons.json"
            with open(lessons_file, encoding="utf-8") as f:
                lessons = json.load(f)
            assert len(lessons) == 1
            assert lessons[0]["failure_point"] == "boom"
            assert lessons[0]["task_description"] == "执行一个必然失败的任务"

    async def test_execute_plan_experience_persist_off_no_write(self):
        """experience_persist=false（默认，观察模式）→ 不落盘，行为与现状一致"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(config={"reflector": {"persist_dir": tmp_dir}})
            core.reflector.persist_dir = tmp_dir
            core.reflector.experiences.clear()
            core.reflector.lessons_db.clear()
            core.register_tool("test_tool", lambda: "工具结果")

            plan = await core.plan("使用test_tool")
            del core._active_plans[plan.id]
            executed = await core.execute_plan(plan)

            assert executed.is_success() is True
            assert not os.path.exists(os.path.join(tmp_dir, "experiences.json")), \
                "观察模式下经验不应落盘"
