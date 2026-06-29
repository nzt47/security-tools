"""认知循环主控制器——集成到 Orchestrator

在任务执行完成后自动触发反思、知识沉淀、双 Agent 校验和多 Agent 辩论。
设计思想：设计文档 4.2~4.4 的完整认知闭环

数据流：
  Orchestrator.process() 执行完成
    ↓
  CognitiveLoop.evaluate(result)     ← 自动触发
    ├─→ 简单任务 → 仅运行 Reflection
    ├─→ 高风险任务 → Reflection + ActorCritic
    └─→ 复杂决策 → Reflection + Debate
         ↓
    生成 CognitiveRecord（含评估结果、修正建议、知识沉淀）
         ↓
    写入日志 + 如果失败则触发重试

任务复杂度分类：
  - SIMPLE:      短查询（<20 字符），仅反思
  - NORMAL:      常规任务，反思 + 知识沉淀
  - HIGH_RISK:   高风险工具调用，全部 + ActorCritic 校验
  - COMPLEX:     复杂决策（含分析/设计/评估关键词），全部 + 辩论
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from agent.cognitive.reflection import ReflectionEngine, ReflectionResult
from agent.cognitive.knowledge import KnowledgePrecipitator, KnowledgeRecord
from agent.cognitive.actor_critic import ActorCriticReviewer, ReviewResult
from agent.cognitive.debate import DebateEngine, DebateResult

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    """任务复杂度等级"""
    SIMPLE = "simple"
    NORMAL = "normal"
    HIGH_RISK = "high_risk"
    COMPLEX = "complex"


@dataclass
class CognitiveRecord:
    """认知循环完整记录

    包含一次认知循环评估的所有结果和决策。

    Attributes:
        task_id: 任务唯一标识
        complexity: 任务复杂度等级
        reflection: 反思结果（可选）
        knowledge: 知识沉淀结果（可选）
        review: 双 Agent 校验结果（可选）
        debate: 辩论结果（可选）
        final_decision: 最终决策（retry / continue / escalate）
        timestamp: 评估时间戳
    """
    task_id: str
    complexity: str
    reflection: Optional[ReflectionResult] = None
    knowledge: Optional[KnowledgeRecord] = None
    review: Optional[ReviewResult] = None
    debate: Optional[DebateResult] = None
    final_decision: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class CognitiveLoop:
    """认知循环主控制器

    根据任务复杂度自动选择合适的评估流程。
    是 Orchestrator 认知能力的核心组件。

    用法:
        loop = CognitiveLoop(memory_router)
        record = loop.evaluate(
            task_id="chat_123",
            task_type="chat",
            input_text="帮我分析这段代码",
            output="这段代码的时间复杂度是 O(n)...",
            execution_time_ms=1500,
        )
        if record.final_decision == "retry":
            # 重新执行
    """

    def __init__(self, memory_router=None):
        """初始化认知循环

        Args:
            memory_router: 可选的 MemoryRouter 实例，
                           用于知识沉淀的持久化
        """
        self._reflection = ReflectionEngine()
        self._knowledge = KnowledgePrecipitator(memory_router)
        self._reviewer = ActorCriticReviewer()
        self._debate = DebateEngine()

    def evaluate(self,
                 task_id: str,
                 task_type: str,
                 input_text: str,
                 output: str,
                 execution_time_ms: float = 0,
                 tool_calls: list = None,
                 tool_name: str = "",
                 tool_params: dict = None,
                 tool_result: dict = None) -> CognitiveRecord:
        """执行完整的认知循环评估

        根据任务复杂度自动决定运行哪些子模块。

        Args:
            task_id: 任务 ID
            task_type: 任务类型（chat, execute_shell, write_file 等）
            input_text: 原始输入
            output: 系统输出
            execution_time_ms: 执行耗时（毫秒）
            tool_calls: 工具调用记录列表
            tool_name: 工具名称（高风险任务时使用）
            tool_params: 工具调用参数（高风险任务时使用）
            tool_result: 工具执行结果（高风险任务时使用）

        Returns:
            CognitiveRecord 完整评估记录
        """
        complexity = self._classify_complexity(task_type, input_text)
        record = CognitiveRecord(task_id=task_id, complexity=complexity.value)

        logger.info("[Cognitive] Loop: task=%s, type=%s, complexity=%s",
                    task_id, task_type, complexity.value)

        # ── 1. 反思——始终执行 ──
        record.reflection = self._reflection.evaluate(
            task_id, input_text, output, execution_time_ms, tool_calls
        )

        # ── 2. 知识沉淀——NORMAL 及以上执行 ──
        if complexity in (TaskComplexity.NORMAL,
                          TaskComplexity.HIGH_RISK,
                          TaskComplexity.COMPLEX):
            record.knowledge = self._knowledge.precipitate(
                task_type, input_text, output
            )

        # ── 3. 双 Agent 校验——HIGH_RISK ──
        if complexity == TaskComplexity.HIGH_RISK and tool_name:
            record.review = self._reviewer.review(
                tool_name, tool_params or {}, tool_result or {}
            )

        # ── 4. 辩论——COMPLEX ──
        if complexity == TaskComplexity.COMPLEX:
            record.debate = self._debate.debate(output)

        # ── 5. 最终决策 ──
        record.final_decision = self._make_decision(record)

        logger.info("[Cognitive] Loop: task=%s, decision=%s, "
                    "reflection_score=%.2f, knowledge=%s, review=%s, debate=%s",
                    task_id,
                    record.final_decision,
                    record.reflection.score if record.reflection else -1,
                    "yes" if record.knowledge else "no",
                    record.review.approved if record.review else "N/A",
                    "yes" if record.debate else "no")

        return record

    def _classify_complexity(self, task_type: str, input_text: str) -> TaskComplexity:
        """评估任务复杂度

        根据任务类型和输入特征自动分类。

        Args:
            task_type: 任务类型
            input_text: 用户输入

        Returns:
            TaskComplexity 枚举
        """
        # 高风险工具 → HIGH_RISK
        if task_type in ActorCriticReviewer.HIGH_RISK_TASKS:
            return TaskComplexity.HIGH_RISK

        # 复杂输入（较长、含分析/决策要求）→ COMPLEX
        complex_keywords = [
            "分析", "比较", "评估", "设计", "规划", "plan",
            "对比", "优缺点", "方案", "策略",
        ]
        if len(input_text) > 100 or any(kw in input_text for kw in complex_keywords):
            return TaskComplexity.COMPLEX

        # 简单查询（较短）→ SIMPLE
        if len(input_text) < 20:
            return TaskComplexity.SIMPLE

        # 常规任务 → NORMAL
        return TaskComplexity.NORMAL

    def _make_decision(self, record: CognitiveRecord) -> str:
        """根据评估结果做出最终决策

        决策优先级：
        1. 反思失败且可重试 → retry
        2. ActorCritic 审核未通过 → escalate（升级给人工）
        3. 辩论不建议执行 → escalate
        4. 全部通过 → continue

        Args:
            record: 认知评估记录

        Returns:
            "retry" / "escalate" / "continue"
        """
        # 反思要求重试（且未超过最大次数）
        if record.reflection and record.reflection.should_retry:
            return "retry"

        # ActorCritic 审核未通过 → 升级
        if record.review and not record.review.approved:
            return "escalate"

        # 辩论不推荐执行 → 升级
        if record.debate and record.debate.consensus_score < 0.4:
            return "escalate"

        # 一切正常
        return "continue"

    @property
    def reflection_engine(self) -> ReflectionEngine:
        """获取反思引擎实例"""
        return self._reflection

    @property
    def knowledge_precipitator(self) -> KnowledgePrecipitator:
        """获取知识沉淀器实例"""
        return self._knowledge

    @property
    def reviewer(self) -> ActorCriticReviewer:
        """获取审核器实例"""
        return self._reviewer

    @property
    def debate_engine(self) -> DebateEngine:
        """获取辩论引擎实例"""
        return self._debate
