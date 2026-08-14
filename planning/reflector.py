"""反思引擎

执行后评估和经验学习
"""

import json
import logging
import os
import inspect
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict

from .models import Task, Plan, ActionResult
from .diagnostics import FailureDiagnosis

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

    FAILURE_REFLECTION_PROMPT = """作为云枢的反思引擎，分析这次行动失败的根本原因并给出修复建议。

原始任务: {task_description}
失败动作: {action}
失败结果: {error}
结构化诊断: {diagnosis}
此前尝试过什么、为何未成功（修复历史）:
{history}

要求:
1. 找出根本原因（root_cause），1-2 句话；
2. 给出可执行的修复动作（repair_actions），最多 3 条，必须与失败原因直接相关；
3. 明确要避免的无效动作（avoid），最多 2 条；
4. 给出根因置信度（confidence），0-1。

输出JSON格式:
{{
    "root_cause": "根本原因",
    "confidence": 0.5,
    "repair_actions": ["修复动作1", "修复动作2"],
    "avoid": ["应避免的动作"]
}}"""

    def __init__(self, llm_service=None, memory_manager=None, config: Dict = None, 
                 persist_dir: str = "./data/reflection", lesson_channel=None,
                 budget_manager=None):
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
            budget_manager: 预算管理器（TD-4：LLM 反思成本记账；None 时保持
                无记账行为向后兼容。step_reflect/plan_reflect 调用方可显式传入
                实例覆盖此默认值——ReAct 路径记 react 实例、计划路径记 executor 实例）
        """
        self.llm = llm_service
        self.memory = memory_manager
        self.config = config or {}
        self.persist_dir = persist_dir
        self.lesson_channel = lesson_channel
        self.budget_manager = budget_manager

        self.reflection_history: List[Dict] = []
        self.learned_patterns: Dict[str, Any] = {}
        self.learned_lessons: Dict[str, Any] = {}
        # 失败反思路径统计：LLM 路径 vs 规则兜底路径（混合异常场景下可观测触发比例）
        self._failure_reflect_stats: Dict[str, int] = {"llm": 0, "fallback": 0}
        
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

    async def step_reflect(self, task: Task, result: ActionResult, context: Dict = None,
                           budget_manager=None) -> ReflectionResult:
        """
        步骤级反思

        在每个子任务完成后调用

        Args:
            budget_manager: TD-4 可选记账实例（ReAct 路径传入 react 实例；
                未传时回退 self.budget_manager）
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
                # TD-4：步骤反思 LLM 成本记账
                self._bill_llm(prompt, response, budget_manager)
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

    async def plan_reflect(self, plan: Plan, budget_manager=None) -> Dict[str, Any]:
        """
        计划级反思

        在整个计划完成后调用

        Args:
            budget_manager: TD-4 可选记账实例（计划路径传入 executor 实例；
                未传时回退 self.budget_manager）
        """
        summary = self._generate_execution_summary(plan)

        prompt = self.PLAN_REFLECTION_PROMPT.format(
            original_task=plan.original_task,
            execution_summary=summary
        )

        if self.llm:
            try:
                response = await self.llm.chat([{"role": "user", "content": prompt}])
                # TD-4：计划反思 LLM 成本记账
                self._bill_llm(prompt, response, budget_manager)
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

    def _bill_llm(self, prompt: Any, response: Any, budget_manager=None) -> None:
        """TD-4：LLM 反思成本记账（调用方显式实例 > 默认实例；均未注入则跳过）"""
        bm = budget_manager or self.budget_manager
        if bm is None:
            return
        bm.record_text(prompt)
        bm.record_text(response)

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

    async def failure_reflect(self, task, result: ActionResult, diagnosis: FailureDiagnosis,
                              attempts: int) -> Optional[FailureDiagnosis]:
        """失败反思（任务4 D12）：对行动失败做根因分析与修复建议。

        - LLM 可用：调用 FAILURE_REFLECTION_PROMPT，解析 JSON 产出；
        - LLM 不可用/抛异常：规则兜底（基于 diagnosis.repair_hints 表）；
        - 反思产物增强 FailureDiagnosis 字段（root_cause/confidence/repair_actions/avoid）；
        - 失败教训沉淀 lessons_db（复用 learn_from_experience 失败分支，确保持久化）；
        - reflection_history 增加 type="failure" 条目。

        Args:
            task: Task 或带 description 的对象（失败任务）
            result: ActionResult（失败结果）
            diagnosis: FailureDiagnosis（build_diagnosis 产出）
            attempts: 当前失败反思次数（1-based）

        Returns:
            增强后的 FailureDiagnosis；反思无产出返回 None（不阻断主循环，守不易）。
        """
        try:
            _t0 = time.monotonic()
            logger.info(
                f"[失败反思#{attempts}] 进入 failure_reflect"
                f" | task={str(getattr(task, 'description', task))[:60]}"
                f" | error_type={diagnosis.error_type}"
                f" | error_message={diagnosis.error_message[:80]}"
            )
            reflection = await self._run_failure_llm(task, result, diagnosis, attempts)
            logger.info(
                f"[失败反思#{attempts}] 步骤1/3 LLM 分析完成"
                f" | 耗时={(time.monotonic() - _t0) * 1000:.0f}ms"
                f" | 产出={'有效' if reflection else '无（转规则兜底）'}"
            )
        except Exception as e:
            logger.warning(
                f"[失败反思#{attempts}] 步骤1/3 LLM 调用异常，切换规则兜底"
                f" | 耗时={(time.monotonic() - _t0) * 1000:.0f}ms | error={e}"
            )
            reflection = None
        if reflection is None:
            reflection = self._rule_based_failure_reflect(diagnosis)
            self._failure_reflect_stats["fallback"] += 1
            logger.info(
                f"[失败反思#{attempts}] 步骤1/3 规则兜底完成"
                f" | 产出={'有效' if reflection else '无'}"
            )
        else:
            self._failure_reflect_stats["llm"] += 1
        if reflection is None:
            logger.info("[失败反思] 无兜底产出（repair_hints 为空），跳过反思注入")
            return None

        # 增强诊断字段
        diagnosis.root_cause = reflection.get("root_cause")
        diagnosis.confidence = float(reflection.get("confidence", 0.0))
        diagnosis.repair_actions = [str(a) for a in reflection.get("repair_actions", [])][:3]
        diagnosis.avoid = [str(a) for a in reflection.get("avoid", [])][:2]
        logger.info(
            f"[失败反思#{attempts}] 步骤2/3 诊断增强"
            f" | confidence={diagnosis.confidence}"
            f" | repair_actions={diagnosis.repair_actions}"
            f" | avoid={diagnosis.avoid}"
        )

        # 历史记录：reflection_history 增加 type="failure" 条目（携带诊断摘要）
        self._record_reflection("failure", getattr(task, "id", "?"), diagnosis.to_dict())

        # 失败经验沉淀：复用 learn_from_experience 失败分支（确保持久化）
        await self._persist_failure_lesson(task, result, diagnosis)
        logger.info(
            f"[失败反思#{attempts}] 步骤3/3 历史记录+lessons 沉淀完成"
            f" | lessons_db={len(self.lessons_db)}"
            f" | reflection_history={len(self.reflection_history)}"
        )

        total = self._failure_reflect_stats["llm"] + self._failure_reflect_stats["fallback"]
        llm_pct = 100.0 * self._failure_reflect_stats["llm"] / total if total else 0.0
        logger.info(
            f"[失败反思#{attempts}] 完成"
            f" | root_cause={diagnosis.root_cause}"
            f" | repair_actions={diagnosis.repair_actions}"
            f" | 路径统计: llm={self._failure_reflect_stats['llm']}"
            f" fallback={self._failure_reflect_stats['fallback']}"
            f" (LLM占比 {llm_pct:.0f}%)"
        )
        return diagnosis

    async def _run_failure_llm(self, task, result, diagnosis: FailureDiagnosis,
                               attempts: int) -> Optional[Dict[str, Any]]:
        """失败反思 LLM 调用：组装 FAILURE_REFLECTION_PROMPT 并解析 JSON。

        LLM 未配置或输出非 JSON 时返回 None（交规则兜底），不抛异常。
        """
        if not self.llm:
            logger.info(f"[失败反思#{attempts}] 分支: LLM 未配置，直接走规则兜底")
            return None
        task_desc = getattr(task, "description", None) or str(task)
        history_lines = "\n".join(
            f"- 第{h.get('attempt')}次: {h.get('action') or '?'} → "
            f"{h.get('error') or '?'}（猜测根因: {h.get('guess') or '未知'}）"
            for h in diagnosis.history
        ) or "(无历史)"
        diagnosis_json = json.dumps({
            "error_type": diagnosis.error_type,
            "tool_name": diagnosis.tool_name,
            "repair_hints": diagnosis.repair_hints,
        }, ensure_ascii=False)
        prompt = self.FAILURE_REFLECTION_PROMPT.format(
            task_description=task_desc[:200],
            action=str(getattr(result, "observation", "") or getattr(result, "error", ""))[:200],
            error=diagnosis.error_message,
            diagnosis=diagnosis_json,
            history=history_lines,
        )
        logger.info(
            f"[失败反思#{attempts}] 分支: LLM prompt 组装完成"
            f" | prompt_len={len(prompt)}"
            f" | 历史条数={len(diagnosis.history)}"
            f" | diagnosis={diagnosis_json}"
        )
        response = await self.llm.chat([{"role": "user", "content": prompt}])
        logger.info(
            f"[失败反思#{attempts}] 分支: LLM 返回 | response_len={len(str(response))}"
            f" | response={str(response)[:120]}"
        )
        self._bill_llm(prompt, response)
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning(
                f"[失败反思#{attempts}] 分支: LLM 输出 JSON 解析失败，交规则兜底"
                f" | response={str(response)[:200]}"
            )
            return None
        return {
            "root_cause": str(data.get("root_cause", ""))[:200] or None,
            "confidence": float(data.get("confidence", 0.0)),
            "repair_actions": [str(a) for a in data.get("repair_actions", [])][:3],
            "avoid": [str(a) for a in data.get("avoid", [])][:2],
        }

    def _rule_based_failure_reflect(self, diagnosis: FailureDiagnosis) -> Optional[Dict[str, Any]]:
        """规则兜底：基于 repair_hints 表 + 历史重复检测生成根因与修复建议（零 LLM 依赖）。

        历史中存在同 error_type 的反思记录时合并其建议（避免重复建议）。
        """
        hints = diagnosis.repair_hints
        if not hints:
            return None
        root_cause = f"{diagnosis.error_type} 类型失败: {diagnosis.error_message[:80]}"
        # 历史重复检测：合并此前同 error_type 的修复建议，保持建议多样性
        merged = list(hints)
        for entry in self.reflection_history:
            if entry.get("type") != "failure":
                continue
            ref = entry.get("reflection") or {}
            if ref.get("error_type") == diagnosis.error_type:
                for a in ref.get("repair_actions") or []:
                    if str(a) not in merged:
                        merged.append(str(a))
        return {
            "root_cause": root_cause,
            "confidence": 0.5,
            "repair_actions": merged[:3],
            "avoid": [],
        }

    async def _persist_failure_lesson(self, task, result: ActionResult,
                                      diagnosis: FailureDiagnosis) -> None:
        """失败反思结果沉淀 lessons_db。

        复用 learn_from_experience 失败分支（确保持久化与 EVO-T4 通道转交）；
        同任务基础 lesson 已存在（executor 失败归因已记录）时去重命中，则追加一条
        带根因/修复建议的 lesson_fail_ 记录，确保"失败反思后 lessons_db 新增 lesson"。
        任何异常仅告警，不阻断反思主流程。
        """
        task_desc = getattr(task, "description", None) or str(task)
        task_type = self._classify_task(task_desc)
        enhanced_error = (
            f"{diagnosis.error_message}"
            f" | 根因: {diagnosis.root_cause or '未知'}"
            f" | 修复建议: {'; '.join(diagnosis.repair_actions) or '无'}"
        )[:500]
        try:
            if self._has_lesson(task_type, task_desc):
                lesson = Lesson(
                    id=f"lesson_fail_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
                    task_type=task_type,
                    task_description=task_desc,
                    failure_point=enhanced_error,
                    solution="; ".join(diagnosis.repair_actions) or None,
                    timestamp=datetime.now().isoformat(),
                )
                self.lessons_db.append(lesson)
                self._trim_lessons()
                self._save_to_persistence()
                logger.warning(f"⚠️ 失败反思沉淀（基础教训已存在，追加根因版）: {lesson.id}")
                return
            await self.learn_from_experience(task_desc, ActionResult.failure_result(enhanced_error))
        except Exception as e:
            logger.warning(f"[失败反思] 教训沉淀失败（不阻断主流程）: {e}")
