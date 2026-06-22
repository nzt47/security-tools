"""认知循环系统——反思、知识沉淀、双 Agent 校验、多 Agent 辩论

提供执行后的自我评估与纠错能力，让云枢具备高阶认知：
- 反思（Reflection）：任务执行后的质量评估与重试决策
- 知识沉淀（Knowledge）：从交互中提取关键信息并持久化
- 双 Agent 校验（ActorCritic）：高风险操作的双重审核
- 辩论（Debate）：复杂决策的多角度评估
"""

from agent.cognitive.loop import CognitiveLoop, CognitiveRecord, TaskComplexity
from agent.cognitive.reflection import ReflectionEngine, ReflectionResult
from agent.cognitive.knowledge import KnowledgePrecipitator, KnowledgeRecord
from agent.cognitive.actor_critic import ActorCriticReviewer, ReviewResult
from agent.cognitive.debate import DebateEngine, DebateResult, Perspective

__all__ = [
    "CognitiveLoop",
    "CognitiveRecord",
    "TaskComplexity",
    "ReflectionEngine",
    "ReflectionResult",
    "KnowledgePrecipitator",
    "KnowledgeRecord",
    "ActorCriticReviewer",
    "ReviewResult",
    "DebateEngine",
    "DebateResult",
    "Perspective",
]
