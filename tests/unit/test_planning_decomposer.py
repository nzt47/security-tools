"""任务分解器单元测试"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from planning.decomposer import TaskDecomposer
from planning.models import Plan, PlanState, Task, TaskType


class TestTaskDecomposer:
    """任务分解器单元测试"""

    def test_decomposer_initialization(self):
        """测试分解器初始化"""
        decomposer = TaskDecomposer()
        assert decomposer is not None
        assert decomposer.llm is None
        assert decomposer.max_subtasks == 20
        
        decomposer = TaskDecomposer(config={"max_subtasks": 10})
        assert decomposer.max_subtasks == 10

    @pytest.mark.asyncio
    async def test_rule_decompose_simple(self):
        """测试规则分解 - 简单任务"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("完成报告")
        
        assert "subtasks" in result
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["description"] == "完成报告"
        assert result["subtasks"][0]["type"] == "atomic"

    @pytest.mark.asyncio
    async def test_rule_decompose_with_separator(self):
        """测试规则分解 - 使用分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("打开文件然后分析数据")
        
        assert len(result["subtasks"]) == 2
        assert result["subtasks"][0]["id"] == "step_1"
        assert result["subtasks"][1]["id"] == "step_2"
        assert "打开文件" in result["subtasks"][0]["description"]
        assert "分析数据" in result["subtasks"][1]["description"]
        assert result["subtasks"][1]["dependencies"] == ["step_1"]

    @pytest.mark.asyncio
    async def test_rule_decompose_multiple_separators(self):
        """测试规则分解 - 多个分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("打开文件然后处理数据最后保存结果")
        
        assert len(result["subtasks"]) == 3
        assert result["subtasks"][0]["id"] == "step_1"
        assert result["subtasks"][1]["id"] == "step_2"
        assert result["subtasks"][2]["id"] == "step_3"
        assert result["subtasks"][1]["dependencies"] == ["step_1"]
        assert result["subtasks"][2]["dependencies"] == ["step_2"]

    @pytest.mark.asyncio
    async def test_rule_decompose_consecutive_separators(self):
        """测试规则分解 - 连续分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("打开文件然后然后保存结果")
        
        assert len(result["subtasks"]) == 2
        assert "打开文件" in result["subtasks"][0]["description"]
        assert "保存结果" in result["subtasks"][1]["description"]

    @pytest.mark.asyncio
    async def test_rule_decompose_empty_string(self):
        """测试规则分解 - 空字符串"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("")
        
        assert len(result["subtasks"]) == 0

    @pytest.mark.asyncio
    async def test_rule_decompose_only_separators(self):
        """测试规则分解 - 只有分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("首先然后接着最后")
        
        assert len(result["subtasks"]) == 0

    @pytest.mark.asyncio
    async def test_rule_decompose_leading_separator(self):
        """测试规则分解 - 开头是分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("首先打开文件然后保存")
        
        assert len(result["subtasks"]) == 2
        assert "打开文件" in result["subtasks"][0]["description"]
        assert "保存" in result["subtasks"][1]["description"]

    @pytest.mark.asyncio
    async def test_rule_decompose_trailing_separator(self):
        """测试规则分解 - 结尾是分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("打开文件然后保存最后")
        
        assert len(result["subtasks"]) == 2
        assert "打开文件" in result["subtasks"][0]["description"]
        assert "保存" in result["subtasks"][1]["description"]

    @pytest.mark.asyncio
    async def test_rule_decompose_mixed_separators(self):
        """测试规则分解 - 混合使用不同分隔符"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("首先打开文件，接着处理数据，之后分析结果，最后保存")
        
        assert len(result["subtasks"]) == 4
        assert result["subtasks"][0]["id"] == "step_1"
        assert result["subtasks"][3]["id"] == "step_4"
        assert "打开文件" in result["subtasks"][0]["description"]
        assert "处理数据" in result["subtasks"][1]["description"]
        assert "分析结果" in result["subtasks"][2]["description"]
        assert "保存" in result["subtasks"][3]["description"]

    @pytest.mark.asyncio
    async def test_rule_decompose_complex_chinese(self):
        """测试规则分解 - 复杂中文句子"""
        decomposer = TaskDecomposer()
        
        result = decomposer._rule_decompose("请先创建一个新文档，然后在文档中写入用户信息，接着保存文档到指定目录，再发送通知邮件给管理员，最后记录操作日志")
        
        assert len(result["subtasks"]) >= 4

    def test_parse_subtasks(self):
        """测试解析子任务"""
        decomposer = TaskDecomposer()
        
        raw_data = {
            "subtasks": [
                {
                    "id": "task_1",
                    "description": "任务1",
                    "type": "atomic",
                    "priority": 3,
                    "dependencies": [],
                    "constraints": [],
                    "estimated_steps": 1
                },
                {
                    "id": "task_2",
                    "description": "任务2",
                    "type": "sequential",
                    "priority": 2,
                    "dependencies": ["task_1"],
                    "constraints": ["条件A"],
                    "estimated_steps": 2
                }
            ],
            "execution_order": ["task_1", "task_2"],
            "parallel_groups": []
        }
        
        tasks = decomposer._parse_subtasks(raw_data)
        
        assert len(tasks) == 2
        assert tasks[0].id == "task_1"
        assert tasks[0].description == "任务1"
        assert tasks[0].task_type == TaskType.ATOMIC
        assert tasks[0].priority == 3
        
        assert tasks[1].id == "task_2"
        assert tasks[1].description == "任务2"
        assert tasks[1].task_type == TaskType.SEQUENTIAL
        assert tasks[1].dependencies == ["task_1"]
        assert tasks[1].constraints == ["条件A"]

    def test_parse_subtasks_invalid_type(self):
        """测试解析子任务 - 无效任务类型"""
        decomposer = TaskDecomposer()
        
        raw_data = {
            "subtasks": [
                {
                    "id": "task_1",
                    "description": "任务1",
                    "type": "invalid_type"
                }
            ]
        }
        
        tasks = decomposer._parse_subtasks(raw_data)
        
        assert len(tasks) == 1
        assert tasks[0].task_type == TaskType.ATOMIC  # 默认为ATOMIC

    def test_parse_subtasks_max_limit(self):
        """测试解析子任务 - 超过最大数量限制"""
        decomposer = TaskDecomposer(config={"max_subtasks": 3})
        
        raw_data = {
            "subtasks": [
                {"id": f"task_{i}", "description": f"任务{i}", "type": "atomic"}
                for i in range(5)
            ]
        }
        
        tasks = decomposer._parse_subtasks(raw_data)
        
        assert len(tasks) == 3  # 只保留前3个

    def test_extract_json_from_response_with_code_block(self):
        """测试从响应中提取JSON - 带代码块"""
        decomposer = TaskDecomposer()
        
        response = """```json
{
    "subtasks": [{"id": "task_1", "description": "测试任务"}]
}
```"""
        
        result = decomposer._extract_json_from_response(response)
        
        assert "subtasks" in result
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["description"] == "测试任务"

    def test_extract_json_from_response_without_code_block(self):
        """测试从响应中提取JSON - 不带代码块"""
        decomposer = TaskDecomposer()
        
        response = '{"subtasks": [{"id": "task_1", "description": "测试任务"}]}'
        
        result = decomposer._extract_json_from_response(response)
        
        assert "subtasks" in result
        assert result["subtasks"][0]["id"] == "task_1"

    def test_extract_json_from_response_invalid_fallback(self):
        """测试从响应中提取JSON - 无效JSON时回退到规则分解"""
        decomposer = TaskDecomposer()
        
        response = "这是一个无效的JSON响应"
        
        result = decomposer._extract_json_from_response(response)
        
        assert "subtasks" in result
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["description"] == "这是一个无效的JSON响应"

    @pytest.mark.asyncio
    async def test_decompose_without_llm(self):
        """测试在没有LLM情况下的分解"""
        decomposer = TaskDecomposer()
        
        plan = await decomposer.decompose("打开文件然后保存")
        
        assert plan.state == PlanState.READY
        assert len(plan.tasks) == 2
        assert "打开文件" in plan.tasks[0].description
        assert "保存" in plan.tasks[1].description

    @pytest.mark.asyncio
    async def test_decompose_with_mock_llm(self):
        """测试使用模拟LLM的分解"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "subtasks": [
                {"id": "step_1", "description": "分析需求", "type": "atomic", "priority": 3, "dependencies": []},
                {"id": "step_2", "description": "编写代码", "type": "atomic", "priority": 3, "dependencies": ["step_1"]},
                {"id": "step_3", "description": "测试验证", "type": "atomic", "priority": 3, "dependencies": ["step_2"]}
            ],
            "execution_order": ["step_1", "step_2", "step_3"],
            "parallel_groups": []
        })
        
        decomposer = TaskDecomposer(llm_service=mock_llm)
        plan = await decomposer.decompose("完成一个功能开发")
        
        assert plan.state == PlanState.READY
        assert len(plan.tasks) == 3
        assert plan.tasks[0].description == "分析需求"
        assert plan.tasks[1].description == "编写代码"
        assert plan.tasks[2].description == "测试验证"
        assert plan.tasks[1].dependencies == ["step_1"]
        assert plan.tasks[2].dependencies == ["step_2"]

    @pytest.mark.asyncio
    async def test_decompose_failure(self):
        """测试分解失败情况"""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = Exception("LLM服务出错")
        
        decomposer = TaskDecomposer(llm_service=mock_llm)
        plan = await decomposer.decompose("测试任务")
        
        assert plan.state == PlanState.FAILED
        assert "LLM服务出错" in plan.error

    @pytest.mark.asyncio
    async def test_refine_without_llm(self):
        """测试在没有LLM情况下的优化"""
        decomposer = TaskDecomposer()
        plan = Plan(original_task="原始任务")
        
        result = await decomposer.refine(plan, "需要更多步骤")
        
        assert result == plan  # 没有LLM时返回原计划

    @pytest.mark.asyncio
    async def test_refine_with_mock_llm(self):
        """测试使用模拟LLM的优化"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "adjustments": [
                {
                    "task_id": "task_1",
                    "action": "modify",
                    "new_description": "修改后的任务描述"
                }
            ],
            "reasoning": "需要更详细的描述"
        })
        
        decomposer = TaskDecomposer(llm_service=mock_llm)
        plan = Plan(original_task="原始任务")
        plan.add_task(Task(id="task_1", description="原描述"))
        
        result = await decomposer.refine(plan, "描述不够详细")
        
        assert result.tasks[0].description == "修改后的任务描述"

    def test_format_plan(self):
        """测试格式化计划"""
        decomposer = TaskDecomposer()
        plan = Plan(original_task="测试计划")
        plan.add_task(Task(id="task_1", description="任务1"))
        plan.add_task(Task(id="task_2", description="任务2", dependencies=["task_1"]))
        
        formatted = decomposer._format_plan(plan)
        
        assert "任务数: 2" in formatted
        assert "[task_1]" in formatted
        assert "[task_2]" in formatted
        assert "依赖:task_1" in formatted
