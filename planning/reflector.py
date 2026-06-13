"""反思引擎

执行后评估和经验学习
"""

import json
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict

from .models import Task, Plan, ActionResult

logger = logging.getLogger(__name__)


@dataclass
class Experience:
    """经验记录"""
    id: str
    task_type: str
    task_description: str
    success: bool
    output: Optional[str]
    error: Optional[str]
    timestamp: str
    metadata: Optional[Dict] = None
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Lesson:
    """教训记录"""
    id: str
    task_type: str
    task_description: str
    failure_point: str
    solution: Optional[str]
    timestamp: str
    metadata: Optional[Dict] = None
    
    def to_dict(self) -> dict:
        return asdict(self)


class ReflectionResult:
    """反思结果"""

    def __init__(self, assessment: str, confidence: float, adjustments: List[str] = None, next_steps: List[str] = None):
        self.assessment = assessment
        self.confidence = confidence
        self.adjustments = adjustments or []
        self.next_steps = next_steps or []

    def to_dict(self) -> dict:
        return {
            "assessment": self.assessment,
            "confidence": self.confidence,
            "adjustments": self.adjustments,
            "next_steps": self.next_steps
        }


class Reflector:
    """反思引擎

    在任务执行过程中和完成后,进行效果评估和经验学习
    """

    STEP_REFLECTION_PROMPT = """作为云枢的反思引擎,分析当前执行步骤的效果。

原始任务: {task_description}
执行动作: {action}
执行结果: {result}
观察结果: {observation}

请分析:
1. 当前步骤是否达到预期目标?
2. 如果继续执行,需要注意什么?
3. 是否需要调整后续计划?

输出JSON格式:
{{
    "assessment": "评估结论(1-2句话)",
    "confidence": 0.0-1.0,
    "adjustments": ["如果有调整建议,列出"],
    "next_steps": ["下一步建议"]
}}"""

    PLAN_REFLECTION_PROMPT = """反思这次计划执行的完整过程:

原始任务: {original_task}
执行摘要: {execution_summary}

分析维度:
1. 计划有效性: 原计划是否合理?
2. 执行效率: 各步骤耗时是否合理?
3. 决策质量: 每步决策是否正确?
4. 经验总结: 有哪些可以改进的地方?

输出JSON格式:
{{
    "overall_score": 0.0-10.0,
    "effectiveness": "计划有效性评估",
    "efficiency": "执行效率评估",
    "lessons": ["经验教训"],
    "improvements": ["改进建议"]
}}"""

    def __init__(self, llm_service=None, memory_manager=None, config: Dict = None, 
                 persist_dir: str = "./data/reflection"):
        """
        初始化反思引擎

        Args:
            llm_service: LLM服务
            memory_manager: 记忆管理器
            config: 配置
            persist_dir: 持久化目录
        """
        self.llm = llm_service
        self.memory = memory_manager
        self.config = config or {}
        self.persist_dir = persist_dir

        self.reflection_history: List[Dict] = []
        self.learned_patterns: Dict[str, Any] = {}
        self.learned_lessons: Dict[str, Any] = {}
        
        self.experiences: List[Experience] = []
        self.lessons_db: List[Lesson] = []
        
        self._ensure_persist_dir()
        self._load_from_persistence()

    async def step_reflect(self, task: Task, result: ActionResult, context: Dict = None) -> ReflectionResult:
        """
        步骤级反思

        在每个子任务完成后调用
        """
        context = context or {}

        prompt = self.STEP_REFLECTION_PROMPT.format(
            task_description=task.description,
            action=task.description,
            result=str(result.output) if result.output else "N/A",
            observation=result.observation
        )

        if self.llm:
            try:
                response = await self.llm.chat([{"role": "user", "content": prompt}])
                reflection = self._parse_step_reflection(response)

                self._record_reflection("step", task.id, reflection)
                return reflection
            except Exception as e:
                logger.warning(f"步骤反思失败: {e}")

        if result.success:
            return ReflectionResult(assessment="步骤执行成功", confidence=0.8)
        else:
            return ReflectionResult(
                assessment=f"步骤执行失败: {result.error}",
                confidence=0.9,
                adjustments=["检查失败原因", "考虑重试"]
            )

    async def plan_reflect(self, plan: Plan) -> Dict[str, Any]:
        """
        计划级反思

        在整个计划完成后调用
        """
        summary = self._generate_execution_summary(plan)

        prompt = self.PLAN_REFLECTION_PROMPT.format(
            original_task=plan.original_task,
            execution_summary=summary
        )

        if self.llm:
            try:
                response = await self.llm.chat([{"role": "user", "content": prompt}])
                reflection = json.loads(response)

                self._record_reflection("plan", plan.id, reflection)
                await self._store_learning(plan, reflection)
                return reflection
            except Exception as e:
                logger.warning(f"计划反思失败: {e}")

        if plan.is_success():
            return {
                "overall_score": 8.0,
                "effectiveness": "计划执行成功",
                "lessons": ["继续保持"],
                "improvements": []
            }
        else:
            return {
                "overall_score": 5.0,
                "effectiveness": "计划部分失败",
                "lessons": ["需要分析失败原因"],
                "improvements": ["改进错误处理"]
            }

    async def learn_from_experience(self, task_description: str, result: ActionResult) -> None:
        """
        从经验中学习

        将成功或失败的经验保存到知识库
        """
        task_type = self._classify_task(task_description)

        if result.success:
            pattern = {
                "task_type": task_type,
                "task_description": task_description,
                "successful_pattern": True,
                "output": str(result.output)[:200] if result.output else None,
                "timestamp": datetime.now().isoformat()
            }

            if task_type not in self.learned_patterns:
                self.learned_patterns[task_type] = []
            self.learned_patterns[task_type].append(pattern)

            logger.info(f"保存成功经验: {task_type}")

        else:
            lesson = {
                "task_type": task_type,
                "task_description": task_description,
                "failure_point": result.error,
                "timestamp": datetime.now().isoformat()
            }

            if task_type not in self.learned_lessons:
                self.learned_lessons[task_type] = []
            self.learned_lessons[task_type].append(lesson)

            logger.warning(f"记录失败教训: {task_type}")

        if self.memory:
            try:
                await self.memory.save_log("experience", {
                    "type": "success" if result.success else "failure",
                    "task_type": task_type,
                    "description": task_description,
                    "result": str(result.output) if result.output else result.error
                })
            except Exception as e:
                logger.warning(f"保存到记忆失败: {e}")

    def _classify_task(self, task_description: str) -> str:
        """分类任务类型"""
        task_lower = task_description.lower()

        if any(kw in task_lower for kw in ["检查", "查看", "获取"]):
            return "query"
        elif any(kw in task_lower for kw in ["创建", "生成", "制作"]):
            return "create"
        elif any(kw in task_lower for kw in ["删除", "移除", "清理"]):
            return "delete"
        elif any(kw in task_lower for kw in ["分析", "评估", "判断"]):
            return "analyze"
        elif any(kw in task_lower for kw in ["修改", "更新", "调整"]):
            return "modify"
        else:
            return "general"

    def _parse_step_reflection(self, response: str) -> ReflectionResult:
        """解析步骤反思结果"""
        try:
            data = json.loads(response)
            return ReflectionResult(
                assessment=data.get("assessment", ""),
                confidence=data.get("confidence", 0.5),
                adjustments=data.get("adjustments", []),
                next_steps=data.get("next_steps", [])
            )
        except json.JSONDecodeError:
            return ReflectionResult(assessment=response[:100], confidence=0.5)

    def _generate_execution_summary(self, plan: Plan) -> str:
        """生成执行摘要"""
        lines = [
            f"总任务数: {len(plan.tasks)}",
            f"完成: {sum(1 for t in plan.tasks if t.status.value == 'completed')}",
            f"失败: {sum(1 for t in plan.tasks if t.status.value == 'failed')}",
            f"跳过: {sum(1 for t in plan.tasks if t.status.value == 'skipped')}",
            "",
            "任务详情:"
        ]

        for task in plan.tasks:
            status_icon = {
                "completed": "✓",
                "failed": "✗",
                "skipped": "-",
                "pending": "○",
                "running": "◐"
            }.get(task.status.value, "?")

            lines.append(f"  {status_icon} {task.description[:40]}")

        return "\n".join(lines)

    def _record_reflection(self, reflection_type: str, target_id: str, reflection: Any) -> None:
        """记录反思"""
        entry = {
            "type": reflection_type,
            "target_id": target_id,
            "reflection": reflection.to_dict() if hasattr(reflection, "to_dict") else reflection,
            "timestamp": datetime.now().isoformat()
        }
        self.reflection_history.append(entry)

    async def _store_learning(self, plan: Plan, reflection: Dict) -> None:
        """保存学习结果"""
        if not self.memory:
            return

        try:
            learning = {
                "task": plan.original_task,
                "score": reflection.get("overall_score"),
                "lessons": reflection.get("lessons", []),
                "improvements": reflection.get("improvements", []),
                "timestamp": datetime.now().isoformat()
            }

            await self.memory.save_log("learning", learning)
            logger.info(f"学习结果已保存到记忆")

        except Exception as e:
            logger.warning(f"保存学习结果失败: {e}")

    def get_learning_stats(self) -> Dict[str, Any]:
        """获取学习统计"""
        return {
            "total_reflections": len(self.reflection_history),
            "learned_patterns_count": sum(len(p) for p in self.learned_patterns.values()),
            "learned_lessons_count": sum(len(p) for p in self.learned_lessons.values()),
            "pattern_types": list(self.learned_patterns.keys()),
            "lesson_types": list(self.learned_lessons.keys()),
            "total_experiences": len(self.experiences),
            "total_lessons": len(self.lessons_db)
        }
    
    def _ensure_persist_dir(self):
        """确保持久化目录存在"""
        os.makedirs(self.persist_dir, exist_ok=True)
        logger.info(f"经验库目录: {self.persist_dir}")
    
    def _load_from_persistence(self):
        """从持久化存储加载"""
        experiences_file = os.path.join(self.persist_dir, "experiences.json")
        lessons_file = os.path.join(self.persist_dir, "lessons.json")
        
        if os.path.exists(experiences_file):
            try:
                with open(experiences_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.experiences = [Experience(**item) for item in data]
                logger.info(f"加载成功: {len(self.experiences)} 条经验")
            except Exception as e:
                logger.warning(f"加载经验库失败: {e}")
        
        if os.path.exists(lessons_file):
            try:
                with open(lessons_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.lessons_db = [Lesson(**item) for item in data]
                logger.info(f"加载成功: {len(self.lessons_db)} 条教训")
            except Exception as e:
                logger.warning(f"加载教训库失败: {e}")
    
    def _save_to_persistence(self):
        """保存到持久化存储"""
        experiences_file = os.path.join(self.persist_dir, "experiences.json")
        lessons_file = os.path.join(self.persist_dir, "lessons.json")
        
        try:
            with open(experiences_file, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self.experiences], f, ensure_ascii=False, indent=2)
            logger.debug(f"保存成功: {len(self.experiences)} 条经验")
        except Exception as e:
            logger.error(f"保存经验库失败: {e}")
        
        try:
            with open(lessons_file, "w", encoding="utf-8") as f:
                json.dump([l.to_dict() for l in self.lessons_db], f, ensure_ascii=False, indent=2)
            logger.debug(f"保存成功: {len(self.lessons_db)} 条教训")
        except Exception as e:
            logger.error(f"保存教训库失败: {e}")
    
    async def learn_from_experience(self, task_description: str, result: ActionResult) -> None:
        """
        从经验中学习
        
        将成功或失败的经验保存到知识库
        """
        task_type = self._classify_task(task_description)
        exp_id = f"exp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        if result.success:
            experience = Experience(
                id=exp_id,
                task_type=task_type,
                task_description=task_description,
                success=True,
                output=str(result.output)[:500] if result.output else None,
                error=None,
                timestamp=datetime.now().isoformat()
            )
            self.experiences.append(experience)
            logger.info(f"✅ 保存成功经验: {exp_id} [{task_type}]")
            
            if task_type not in self.learned_patterns:
                self.learned_patterns[task_type] = []
            self.learned_patterns[task_type].append(experience.to_dict())
        else:
            lesson = Lesson(
                id=f"lesson_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
                task_type=task_type,
                task_description=task_description,
                failure_point=result.error or "未知错误",
                solution=None,
                timestamp=datetime.now().isoformat()
            )
            self.lessons_db.append(lesson)
            logger.warning(f"⚠️ 记录失败教训: {lesson.id} [{task_type}]")
            
            if task_type not in self.learned_lessons:
                self.learned_lessons[task_type] = []
            self.learned_lessons[task_type].append(lesson.to_dict())
        
        self._save_to_persistence()
        
        if self.memory:
            try:
                await self.memory.save_log("experience", {
                    "id": exp_id,
                    "type": "success" if result.success else "failure",
                    "task_type": task_type,
                    "description": task_description,
                    "result": str(result.output) if result.output else result.error
                })
            except Exception as e:
                logger.warning(f"保存到记忆失败: {e}")
    
    def query_experiences(self, task_type: Optional[str] = None, limit: int = 10) -> List[Experience]:
        """查询经验库"""
        if task_type:
            filtered = [e for e in self.experiences if e.task_type == task_type]
            return list(reversed(filtered[-limit:]))
        return list(reversed(self.experiences[-limit:]))
    
    def query_lessons(self, task_type: Optional[str] = None, limit: int = 10) -> List[Lesson]:
        """查询教训库"""
        if task_type:
            filtered = [l for l in self.lessons_db if l.task_type == task_type]
            return list(reversed(filtered[-limit:]))
        return list(reversed(self.lessons_db[-limit:]))
    
    def get_advice_for_task(self, task_description: str) -> Optional[Dict]:
        """为任务获取建议"""
        task_type = self._classify_task(task_description)
        
        related_experiences = self.query_experiences(task_type, limit=3)
        related_lessons = self.query_lessons(task_type, limit=3)
        
        if not related_experiences and not related_lessons:
            return None
        
        return {
            "task_type": task_type,
            "related_experiences": len(related_experiences),
            "related_lessons": len(related_lessons),
            "successful_patterns": [
                {"id": e.id, "description": e.task_description[:50], "output": e.output[:50] if e.output else None}
                for e in related_experiences[:3]
            ],
            "common_pitfalls": [
                {"id": l.id, "description": l.task_description[:50], "failure": l.failure_point[:100]}
                for l in related_lessons[:3]
            ]
        }
