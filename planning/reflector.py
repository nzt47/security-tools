"""反思引擎

执行后评估和经验学习
"""

import json
import logging
import os
import inspect
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict

from .models import Task, Plan, ActionResult

logger = logging.getLogger(__name__)


def format_advice_section(advice: Optional[Dict]) -> str:
    """将经验建议格式化为提示词注入段（标注"历史经验"）

    Returns:
        注入段文本（含【历史经验】标题）；无可用内容返回空串（调用方不注入）。
    """
    if not advice:
        return ""
    lines = []
    patterns = advice.get("successful_patterns") or []
    pitfalls = advice.get("common_pitfalls") or []
    if patterns:
        lines.append("成功模式（历史经验）:")
        for p in patterns:
            lines.append(
                f"- [{p.get('id')}] {p.get('description')} → {p.get('output')}"
            )
    if pitfalls:
        lines.append("常见陷阱（历史教训）:")
        for p in pitfalls:
            lines.append(
                f"- [{p.get('id')}] {p.get('description')}（失败点: {p.get('failure')}）"
            )
    if not lines:
        return ""
    return "【历史经验】\n" + "\n".join(lines)


def classify_task(task_description: str) -> str:
    """分类任务类型（模块级：供 TaskDecomposer/ReActLoop/PlanningCore 复用）"""
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
                 persist_dir: str = "./data/reflection", lesson_channel=None):
        """
        初始化反思引擎

        Args:
            llm_service: LLM服务
            memory_manager: 记忆管理器
            config: 配置
            persist_dir: 持久化目录
            lesson_channel: 可选输出通道（任务 EVO-T4 上下文进化闭环）：
                反思产出的 Lesson 命中可验证类别时，自动转交该通道
                进入评估验证 → 优化建议管道；None 时保持既有行为不变。
                接口约定（duck-typing）: submit_lesson(lesson) -> Optional[str]
                （同步或异步均可，本类自动适配）。
        """
        self.llm = llm_service
        self.memory = memory_manager
        self.config = config or {}
        self.persist_dir = persist_dir
        self.lesson_channel = lesson_channel

        self.reflection_history: List[Dict] = []
        self.learned_patterns: Dict[str, Any] = {}
        self.learned_lessons: Dict[str, Any] = {}
        
        self.experiences: List[Experience] = []
        self.lessons_db: List[Lesson] = []

        # 阶段 4（D17）：经验库数据管理——去重 + 上限（防无限膨胀），
        # 默认经验 500 / 教训 300，可经 planning.reflector.max_experiences/max_lessons 配置
        self.max_experiences = int(self.config.get("max_experiences", 500))
        self.max_lessons = int(self.config.get("max_lessons", 300))
        # 阶段 4（D17）：按任务类型的经验命中率统计（get_advice_for_task 每次调用计数）
        self._advice_queries: Dict[str, int] = {}
        self._advice_hits: Dict[str, int] = {}
        # 阶段 4（D16）：规划指标埋点（由 PlanningCore 注入 PlanningMetrics；未注入跳过）
        self.metrics = None
        
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

    def _classify_task(self, task_description: str) -> str:
        """分类任务类型（委托模块级 classify_task，保持既有调用兼容）"""
        return classify_task(task_description)

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
        total_queries = sum(self._advice_queries.values())
        total_hits = sum(self._advice_hits.values())
        return {
            "total_reflections": len(self.reflection_history),
            "learned_patterns_count": sum(len(p) for p in self.learned_patterns.values()),
            "learned_lessons_count": sum(len(p) for p in self.learned_lessons.values()),
            "pattern_types": list(self.learned_patterns.keys()),
            "lesson_types": list(self.learned_lessons.keys()),
            "total_experiences": len(self.experiences),
            "total_lessons": len(self.lessons_db),
            # 阶段 4（D17）：按任务类型的经验命中率
            "experience_hit_rate": {
                "total_queries": total_queries,
                "total_hits": total_hits,
                "overall": round(total_hits / total_queries, 4) if total_queries else 0.0,
                "by_task_type": {
                    t: round(self._advice_hits.get(t, 0) / self._advice_queries[t], 4)
                    if self._advice_queries.get(t, 0) else 0.0
                    for t in sorted(set(self._advice_queries) | set(self._advice_hits))
                },
            },
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

        阶段 4（D17）数据管理：
          - 去重：同 task_type + task_description + success 标志已存在时跳过写入
            （避免同一任务反复产生重复经验）；
          - 上限：超过 max_experiences/max_lessons 时丢弃最旧记录，防经验库无限膨胀。
        """
        task_type = self._classify_task(task_description)
        exp_id = f"exp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        if result.success:
            if self._has_experience(task_type, task_description, True):
                logger.debug(f"经验重复已跳过: [{task_type}] {task_description[:40]}")
                return
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
            self._trim_experiences()
            logger.info(f"✅ 保存成功经验: {exp_id} [{task_type}]")
            
            if task_type not in self.learned_patterns:
                self.learned_patterns[task_type] = []
            self.learned_patterns[task_type].append(experience.to_dict())
        else:
            if self._has_lesson(task_type, task_description):
                logger.debug(f"教训重复已跳过: [{task_type}] {task_description[:40]}")
                return
            lesson = Lesson(
                id=f"lesson_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
                task_type=task_type,
                task_description=task_description,
                failure_point=result.error or "未知错误",
                solution=None,
                timestamp=datetime.now().isoformat()
            )
            self.lessons_db.append(lesson)
            self._trim_lessons()
            logger.warning(f"⚠️ 记录失败教训: {lesson.id} [{task_type}]")
            
            if task_type not in self.learned_lessons:
                self.learned_lessons[task_type] = []
            self.learned_lessons[task_type].append(lesson.to_dict())
        
        self._save_to_persistence()
        
        if not result.success:
            # 任务 EVO-T4：失败教训转交进化验证管道（可选通道，不影响既有行为）
            await self._forward_lesson(lesson)
        
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

    def _has_experience(self, task_type: str, task_description: str, success: bool) -> bool:
        """经验去重检查：同类型 + 同描述 + 同成功标志"""
        return any(
            e.task_type == task_type and e.task_description == task_description and e.success == success
            for e in self.experiences
        )

    def _has_lesson(self, task_type: str, task_description: str) -> bool:
        """教训去重检查：同类型 + 同描述"""
        return any(
            l.task_type == task_type and l.task_description == task_description
            for l in self.lessons_db
        )

    def _trim_experiences(self) -> None:
        """经验库上限截断：超过 max_experiences 时丢弃最旧记录"""
        while len(self.experiences) > self.max_experiences:
            removed = self.experiences.pop(0)
            logger.debug(f"经验库达上限({self.max_experiences})，丢弃最旧: {removed.id}")

    def _trim_lessons(self) -> None:
        """教训库上限截断：超过 max_lessons 时丢弃最旧记录"""
        while len(self.lessons_db) > self.max_lessons:
            removed = self.lessons_db.pop(0)
            logger.debug(f"教训库达上限({self.max_lessons})，丢弃最旧: {removed.id}")
    
    async def _forward_lesson(self, lesson: Lesson) -> None:
        """任务 EVO-T4：Lesson → 进化验证管道（可选通道）

        命中可验证类别时自动转交 lesson_channel 验证其有效性，验证通过的
        Lesson 进入优化建议管道（PromptOptimizationProposal，不自动应用）。

        通道失败/异常不阻断反思主流程（守不易：可选能力，必须向后兼容）。
        同步/异步通道均适配（inspect.isawaitable 自动识别）。
        """
        channel = self.lesson_channel
        if channel is None:
            logger.debug("Lesson 未转交进化验证管道：未配置 lesson_channel lesson=%s",
                         lesson.id)
            return
        if not hasattr(channel, "submit_lesson"):
            logger.warning("Lesson 未转交进化验证管道：lesson_channel 缺少 submit_lesson "
                           "接口 lesson=%s", lesson.id)
            return
        try:
            logger.debug("Lesson 转交进化验证管道 lesson=%s task_type=%s",
                         lesson.id, lesson.task_type)
            result = channel.submit_lesson(lesson)
            if inspect.isawaitable(result):
                result = await result
            logger.info(
                f"Lesson 已转交进化验证管道 lesson={lesson.id} "
                f"task_type={lesson.task_type} result={result}"
            )
        except Exception as e:
            logger.warning(f"Lesson 转交进化验证管道失败 lesson={lesson.id}: {e}")

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
        """为任务获取建议

        阶段 4（D17）：每次检索计入按任务类型的命中率统计
        （_advice_queries/_advice_hits），并透出到规划指标
        （self.metrics 由 PlanningCore 注入；未注入时跳过，埋点失败不阻断）。
        """
        task_type = classify_task(task_description)

        related_experiences = self.query_experiences(task_type, limit=3)
        related_lessons = self.query_lessons(task_type, limit=3)

        hit = bool(related_experiences or related_lessons)
        self._advice_queries[task_type] = self._advice_queries.get(task_type, 0) + 1
        if hit:
            self._advice_hits[task_type] = self._advice_hits.get(task_type, 0) + 1
        if self.metrics is not None:
            try:
                self.metrics.record_experience_lookup(task_type, hit)
            except Exception as e:  # 埋点失败隔离：不阻断检索
                logger.warning(f"[经验回灌] 命中率埋点失败: {e}")

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
