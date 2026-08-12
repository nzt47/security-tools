"""任务分解器

将复杂任务分解为可执行的任务图
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import Task, TaskType, Plan, PlanState
from .llm_json import extract_json, extract_json_with_retry

logger = logging.getLogger(__name__)


class TaskDecomposer:
    """任务分解器

    将复杂的自然语言任务分解为可执行的任务图
    """

    DECOMPOSITION_PROMPT = """分析以下任务描述,将其分解为可执行的子任务。

任务: {task_description}

上下文: {context}

要求:
1. 识别任务中的关键动作步骤
2. 确定动作的执行顺序(考虑依赖关系)
3. 识别可以并行执行的动作
4. 每个子任务应该是独立的、可验证的

输出JSON格式:
{{
    "subtasks": [
        {{
            "id": "step_1",
            "description": "子任务描述",
            "type": "atomic|sequential|parallel",
            "priority": 3,
            "dependencies": ["依赖的task_id列表"],
            "constraints": ["前置条件"],
            "estimated_steps": 1
        }}
    ],
    "execution_order": ["task_id列表,表示推荐执行顺序"],
    "parallel_groups": [["可并行执行的task_id组"]]
}}

请直接输出JSON,不要有其他内容:"""

    def __init__(self, llm_service=None, config: Dict = None):
        """
        初始化分解器

        Args:
            llm_service: LLM服务实例
            config: 配置字典
        """
        self.llm = llm_service
        self.config = config or {}
        self.max_subtasks = self.config.get("max_subtasks", 20)

    async def decompose(self, task_description: str, context: Dict[str, Any] = None) -> Plan:
        """
        分解任务

        Args:
            task_description: 任务描述
            context: 执行上下文

        Returns:
            Plan: 分解后的执行计划
        """
        context = context or {}
        plan = Plan(
            original_task=task_description,
            state=PlanState.DECOMPOSING,
            context=context
        )

        logger.info("="*60)
        logger.info("🔍 [任务分解器] 开始分解任务")
        logger.info(f"   原始任务: {task_description[:100]}{'...' if len(task_description) > 100 else ''}")
        logger.info(f"   上下文键: {list(context.keys())}")
        logger.info(f"   最大子任务数: {self.max_subtasks}")
        logger.info("-"*60)

        try:
            logger.info("📊 步骤1: 选择分解策略...")
            if self.llm:
                logger.info("   ✅ LLM服务可用，使用LLM驱动分解")
                logger.info("   正在调用LLM进行任务分析...")
                subtasks_data = await self._llm_decompose(task_description, context)
                logger.info(f"   ✅ LLM分解完成")
            else:
                logger.info("   ⚠️ LLM服务不可用，使用规则降级分解")
                logger.info("   正在基于规则进行任务分解...")
                subtasks_data = self._rule_decompose(task_description)
                logger.info(f"   ✅ 规则分解完成")

            logger.info(f"📋 步骤2: 解析子任务...")
            tasks = self._parse_subtasks(subtasks_data)
            logger.info(f"   解析得到 {len(tasks)} 个子任务")
            for i, t in enumerate(tasks):
                logger.info(f"      子任务{i+1}: {t.description[:60]}...")
                logger.info(f"         类型: {t.task_type.value}, 优先级: {t.priority}")
                if t.dependencies:
                    logger.info(f"         依赖: {t.dependencies}")

            plan.tasks = tasks
            plan.state = PlanState.READY
            # 阶段 2（D5）：parallel_groups 写入 plan.metadata 供执行器参考
            parallel_groups = subtasks_data.get("parallel_groups", [])
            if isinstance(parallel_groups, list):
                plan.metadata["parallel_groups"] = parallel_groups
                if parallel_groups:
                    logger.info(f"   并行组声明: {parallel_groups}")

            logger.info("📋 步骤3: 更新计划状态...")
            logger.info(f"   ✅ 任务分解完成!")
            logger.info(f"   子任务总数: {len(tasks)}")
            logger.info(f"   计划状态: {plan.state.value}")
            logger.info("="*60)

        except Exception as e:
            logger.error(f"❌ 任务分解失败: {e}")
            logger.error(f"异常类型: {type(e).__name__}")
            import traceback
            logger.error(f"堆栈跟踪:\n{traceback.format_exc()}")
            plan.state = PlanState.FAILED
            plan.error = str(e)
            logger.info("="*60)

        plan.updated_at = datetime.now()
        return plan

    async def _llm_decompose(self, task: str, context: Dict) -> Dict[str, Any]:
        """使用LLM进行任务分解（阶段 2 鲁棒性升级：提取校验 + 失败附反馈重试 1 次，
        仍失败由 _extract_json_from_response 回退规则分解）"""
        context_str = json.dumps(context, ensure_ascii=False, indent=2)

        def _build_prompt() -> str:
            return self.DECOMPOSITION_PROMPT.format(
                task_description=task,
                context=context_str
            )

        prompt = _build_prompt()
        response = await self.llm.chat([{"role": "user", "content": prompt}])

        def _build_retry_prompt(errors: List[str]) -> str:
            return (
                f"{_build_prompt()}\n\n"
                "上轮输出解析失败，请修正后重新输出严格 JSON（只输出 JSON，不要其他内容）：\n"
                + "\n".join(f"- {e}" for e in errors)
            )

        data, errors = await extract_json_with_retry(response, self.llm, _build_retry_prompt)
        if data is not None:
            return data

        # 仍失败 → 回退规则分解（与阶段 1 行为一致）
        logger.warning(f"LLM 分解输出解析失败（{'；'.join(errors)}），回退规则分解")
        return self._rule_decompose(task)

    def _rule_decompose(self, task: str) -> Dict[str, Any]:
        """基于规则的任务分解(降级方案)"""
        logger.info("🔧 [规则分解] 开始基于关键词的任务分解")

        separators = ["首先", "然后", "接着", "之后", "最后", "再"]

        parts = [task]
        for sep in separators:
            new_parts = []
            for part in parts:
                new_parts.extend(part.split(sep))
            if len(new_parts) > len(parts):
                logger.info(f"   使用分隔符 '{sep}' 分割任务")
                parts = new_parts

        subtasks = []
        valid_parts = [part.strip() for part in parts if part.strip()]
        
        for idx, part in enumerate(valid_parts):
            subtask = {
                "id": f"step_{idx + 1}",
                "description": part,
                "type": "atomic",
                "priority": 3,
                "dependencies": [f"step_{idx}"] if idx > 0 else [],
                "constraints": [],
                "estimated_steps": 1
            }
            subtasks.append(subtask)
            logger.info(f"   子任务{idx+1}: {part[:50]}...")

        logger.info(f"   规则分解完成，共 {len(subtasks)} 个子任务")

        return {
            "subtasks": subtasks,
            "execution_order": [s["id"] for s in subtasks],
            "parallel_groups": []
        }

    def _parse_subtasks(self, data: Dict[str, Any]) -> List[Task]:
        """解析子任务数据"""
        logger.info("🔄 [任务解析] 开始解析子任务数据...")
        subtasks_raw = data.get("subtasks", [])
        logger.info(f"   原始数据中包含 {len(subtasks_raw)} 个子任务")

        tasks = []

        for i, item in enumerate(subtasks_raw[:self.max_subtasks]):
            task_type_str = item.get("type", "atomic")
            try:
                task_type = TaskType(task_type_str)
            except ValueError:
                task_type = TaskType.ATOMIC

            task = Task(
                id=item.get("id", f"task_{len(tasks)}"),
                description=item.get("description", ""),
                task_type=task_type,
                priority=item.get("priority", 3),
                dependencies=item.get("dependencies", []),
                constraints=item.get("constraints", []),
                estimated_steps=item.get("estimated_steps", 1),
                # D14：透传 LLM 提供的任务级降级链（fallback_actions），缺省空列表
                fallback_actions=item.get("fallback_actions", []),
            )
            tasks.append(task)
            logger.debug(f"   解析子任务{i+1}: {task.description[:50]}...")

        logger.info(f"✅ 解析完成，共 {len(tasks)} 个有效子任务")
        return tasks

    def _extract_json_from_response(self, response: str) -> Dict[str, Any]:
        """从LLM响应中提取JSON（阶段 2 起委托 planning/llm_json.py 公共函数；
        失败回退规则分解，行为与阶段 1 一致）"""
        data = extract_json(response)
        if data is not None:
            return data
        return self._rule_decompose(response)

    async def refine(self, plan: Plan, feedback: str) -> Plan:
        """
        根据反馈优化计划

        Args:
            plan: 当前计划
            feedback: 反馈信息

        Returns:
            优化后的计划
        """
        if not self.llm:
            return plan

        prompt = f"""根据反馈优化执行计划:

原始任务: {plan.original_task}

当前计划:
{self._format_plan(plan)}

反馈: {feedback}

请分析反馈并调整计划,输出调整后的JSON:
{{
    "adjustments": [
        {{
            "task_id": "要调整的task_id",
            "action": "add|remove|modify",
            "new_description": "新描述(如果修改)",
            "new_dependencies": ["新依赖"]
        }}
    ],
    "reasoning": "调整理由"
}}
"""
        try:
            response = await self.llm.chat([{"role": "user", "content": prompt}])
            # 阶段 2：refine 输出解析复用 llm_json 公共函数（鲁棒性升级）
            data = extract_json(response) or {}
            adjustments = data.get("adjustments", [])

            for adj in adjustments:
                self._apply_adjustment(plan, adj)

            logger.info(f"计划优化完成: {len(adjustments)}项调整")

        except Exception as e:
            logger.warning(f"计划优化失败: {e}")

        return plan

    def _format_plan(self, plan: Plan) -> str:
        """格式化计划为可读文本"""
        lines = [f"任务数: {len(plan.tasks)}"]
        for task in plan.tasks:
            deps = ", ".join(task.dependencies) if task.dependencies else "无"
            lines.append(
                f"- [{task.id}] {task.description} "
                f"(类型:{task.task_type.value}, 依赖:{deps})"
            )
        return "\n".join(lines)

    def _apply_adjustment(self, plan: Plan, adjustment: Dict):
        """应用单个调整"""
        action = adjustment.get("action")
        task_id = adjustment.get("task_id")

        if action == "remove":
            plan.tasks = [t for t in plan.tasks if t.id != task_id]
        elif action == "modify":
            for task in plan.tasks:
                if task.id == task_id:
                    if "new_description" in adjustment:
                        task.description = adjustment["new_description"]
                    if "new_dependencies" in adjustment:
                        task.dependencies = adjustment["new_dependencies"]
                    break
