"""反思引擎单元测试"""

import pytest
import json
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock
from planning.reflector import Reflector, Experience, Lesson, ReflectionResult
from planning.models import Task, TaskStatus, Plan, PlanState, ActionResult


class TestExperience:
    """经验记录单元测试"""

    def test_experience_creation(self):
        """测试经验记录创建"""
        exp = Experience(
            id="exp_001",
            task_type="query",
            task_description="测试任务",
            success=True,
            output="成功输出",
            error=None,
            timestamp="2024-01-01T00:00:00"
        )
        
        assert exp.id == "exp_001"
        assert exp.task_type == "query"
        assert exp.success is True
        assert exp.output == "成功输出"
        
        result = exp.to_dict()
        assert result["id"] == "exp_001"
        assert result["success"] is True


class TestLesson:
    """教训记录单元测试"""

    def test_lesson_creation(self):
        """测试教训记录创建"""
        lesson = Lesson(
            id="lesson_001",
            task_type="create",
            task_description="失败任务",
            failure_point="错误原因",
            solution=None,
            timestamp="2024-01-01T00:00:00"
        )
        
        assert lesson.id == "lesson_001"
        assert lesson.task_type == "create"
        assert lesson.failure_point == "错误原因"
        
        result = lesson.to_dict()
        assert result["failure_point"] == "错误原因"


class TestReflectionResult:
    """反思结果单元测试"""

    def test_reflection_result_creation(self):
        """测试反思结果创建"""
        result = ReflectionResult(
            assessment="评估结论",
            confidence=0.8,
            adjustments=["调整建议1", "调整建议2"],
            next_steps=["下一步1"]
        )
        
        assert result.assessment == "评估结论"
        assert result.confidence == 0.8
        assert len(result.adjustments) == 2
        assert len(result.next_steps) == 1
        
        dict_result = result.to_dict()
        assert dict_result["assessment"] == "评估结论"


class TestReflector:
    """反思引擎单元测试"""

    def test_reflector_initialization(self):
        """测试反思引擎初始化"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            assert reflector is not None
            assert reflector.llm is None
            assert reflector.memory is None
            assert reflector.persist_dir == tmp_dir
            assert reflector.experiences == []
            assert reflector.lessons_db == []

    def test_classify_task(self):
        """测试任务分类"""
        reflector = Reflector(persist_dir=tempfile.mkdtemp())
        
        assert reflector._classify_task("检查系统状态") == "query"
        assert reflector._classify_task("创建新文件") == "create"
        assert reflector._classify_task("删除临时文件") == "delete"
        assert reflector._classify_task("分析数据") == "analyze"
        assert reflector._classify_task("修改配置") == "modify"
        assert reflector._classify_task("普通任务") == "general"

    def test_parse_step_reflection(self):
        """测试解析步骤反思"""
        reflector = Reflector(persist_dir=tempfile.mkdtemp())
        
        json_response = json.dumps({
            "assessment": "步骤成功",
            "confidence": 0.9,
            "adjustments": [],
            "next_steps": ["继续执行"]
        })
        
        result = reflector._parse_step_reflection(json_response)
        
        assert result.assessment == "步骤成功"
        assert result.confidence == 0.9
        assert result.next_steps == ["继续执行"]

    def test_parse_step_reflection_invalid_json(self):
        """测试解析无效JSON的步骤反思"""
        reflector = Reflector(persist_dir=tempfile.mkdtemp())
        
        result = reflector._parse_step_reflection("无效的JSON响应")
        
        assert result.assessment == "无效的JSON响应"
        assert result.confidence == 0.5

    def test_generate_execution_summary(self):
        """测试生成执行摘要"""
        reflector = Reflector(persist_dir=tempfile.mkdtemp())
        
        plan = Plan(original_task="测试计划")
        plan.add_task(Task(id="task1", description="任务1"))
        plan.add_task(Task(id="task2", description="任务2"))
        plan.tasks[0].mark_completed()
        plan.tasks[1].mark_failed("失败原因")
        
        summary = reflector._generate_execution_summary(plan)
        
        assert "总任务数: 2" in summary
        assert "完成: 1" in summary
        assert "失败: 1" in summary
        assert "✓ 任务1" in summary
        assert "✗ 任务2" in summary

    @pytest.mark.asyncio
    async def test_step_reflect_without_llm_success(self):
        """测试步骤反思 - 无LLM且成功"""
        reflector = Reflector(persist_dir=tempfile.mkdtemp())
        
        task = Task(id="task1", description="测试任务")
        result = ActionResult.success_result(output="成功")
        
        reflection = await reflector.step_reflect(task, result)
        
        assert reflection.assessment == "步骤执行成功"
        assert reflection.confidence == 0.8

    @pytest.mark.asyncio
    async def test_step_reflect_without_llm_failure(self):
        """测试步骤反思 - 无LLM且失败"""
        reflector = Reflector(persist_dir=tempfile.mkdtemp())
        
        task = Task(id="task1", description="测试任务")
        result = ActionResult.failure_result("失败原因")
        
        reflection = await reflector.step_reflect(task, result)
        
        assert "步骤执行失败" in reflection.assessment
        assert reflection.confidence == 0.9
        assert "检查失败原因" in reflection.adjustments

    @pytest.mark.asyncio
    async def test_step_reflect_with_mock_llm(self):
        """测试步骤反思 - 使用模拟LLM"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "assessment": "LLM评估",
            "confidence": 0.85,
            "adjustments": ["调整1"],
            "next_steps": ["下一步"]
        })
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(llm_service=mock_llm, persist_dir=tmp_dir)
            
            task = Task(id="task1", description="测试任务")
            result = ActionResult.success_result(output="成功")
            
            reflection = await reflector.step_reflect(task, result)
            
            assert reflection.assessment == "LLM评估"
            assert reflection.confidence == 0.85

    @pytest.mark.asyncio
    async def test_plan_reflect_without_llm_success(self):
        """测试计划反思 - 无LLM且成功"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            plan = Plan(original_task="测试计划", state=PlanState.COMPLETED)
            plan.add_task(Task(id="task1"))
            plan.tasks[0].mark_completed()
            
            result = await reflector.plan_reflect(plan)
            
            assert result["overall_score"] == 8.0
            assert result["effectiveness"] == "计划执行成功"

    @pytest.mark.asyncio
    async def test_plan_reflect_without_llm_failure(self):
        """测试计划反思 - 无LLM且失败"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            plan = Plan(original_task="测试计划", state=PlanState.COMPLETED)
            plan.add_task(Task(id="task1"))
            plan.tasks[0].mark_failed("失败")
            
            result = await reflector.plan_reflect(plan)
            
            assert result["overall_score"] == 5.0
            assert result["effectiveness"] == "计划部分失败"

    @pytest.mark.asyncio
    async def test_learn_from_experience_success(self):
        """测试从成功经验学习"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            result = ActionResult.success_result(output="成功输出")
            await reflector.learn_from_experience("检查状态", result)
            
            assert len(reflector.experiences) == 1
            assert reflector.experiences[0].success is True
            assert reflector.experiences[0].task_type == "query"

    @pytest.mark.asyncio
    async def test_learn_from_experience_failure(self):
        """测试从失败经验学习"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            result = ActionResult.failure_result("失败原因")
            await reflector.learn_from_experience("创建文件", result)
            
            assert len(reflector.lessons_db) == 1
            assert reflector.lessons_db[0].failure_point == "失败原因"
            assert reflector.lessons_db[0].task_type == "create"

    def test_query_experiences(self):
        """测试查询经验库"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            exp1 = Experience(
                id="exp_1",
                task_type="query",
                task_description="任务1",
                success=True,
                output="结果1",
                error=None,
                timestamp="2024-01-01T00:00:00"
            )
            exp2 = Experience(
                id="exp_2",
                task_type="create",
                task_description="任务2",
                success=True,
                output="结果2",
                error=None,
                timestamp="2024-01-02T00:00:00"
            )
            reflector.experiences = [exp1, exp2]
            
            query_result = reflector.query_experiences(task_type="query")
            assert len(query_result) == 1
            assert query_result[0].task_type == "query"
            
            all_result = reflector.query_experiences()
            assert len(all_result) == 2

    def test_query_lessons(self):
        """测试查询教训库"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            lesson1 = Lesson(
                id="lesson_1",
                task_type="create",
                task_description="失败任务1",
                failure_point="错误1",
                solution=None,
                timestamp="2024-01-01T00:00:00"
            )
            reflector.lessons_db = [lesson1]
            
            result = reflector.query_lessons(task_type="create")
            assert len(result) == 1

    def test_get_advice_for_task(self):
        """测试为任务获取建议"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            exp1 = Experience(
                id="exp_1",
                task_type="query",
                task_description="检查系统状态",
                success=True,
                output="状态正常",
                error=None,
                timestamp="2024-01-01T00:00:00"
            )
            reflector.experiences = [exp1]
            
            advice = reflector.get_advice_for_task("检查系统状态")
            
            assert advice is not None
            assert advice["task_type"] == "query"
            assert advice["related_experiences"] == 1

    def test_get_advice_no_match(self):
        """测试为无匹配经验的任务获取建议"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            advice = reflector.get_advice_for_task("未知任务")
            
            assert advice is None

    def test_get_learning_stats(self):
        """测试获取学习统计"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            
            stats = reflector.get_learning_stats()
            
            assert "total_reflections" in stats
            assert "learned_patterns_count" in stats
            assert "total_experiences" in stats
