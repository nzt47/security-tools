"""云枢智能工作流学习系统 (Workflow Learning)

核心能力:
    1. learner: 从成功的 LLM 交互中提取方法 (工具调用序列 + 参数模板)
    2. generator: 自动生成可执行工作流 (LearnedWorkflow)
    3. repository: 本地工作流仓库 (data/learned_workflows.json)
    4. matcher: TF-IDF + 余弦相似度匹配新任务到已有工作流
    5. executor: 优先执行本地工作流，避免冗余 LLM 调用

设计原则:
    - 本地优先: 新任务到达时先查本地仓库；命中且置信度高时跳过 LLM
    - 可观测: 全程结构化日志 + 业务指标
    - 边界显性化: 学习失败/执行失败均抛 WorkflowLearningError
"""

from .service import WorkflowLearningService
from .models import (
    LearnedWorkflow,
    WorkflowStep,
    LearningRecord,
    WorkflowExecutionResult,
)
from .exceptions import (
    WorkflowLearningError,
    WorkflowNotFoundError,
    WorkflowExecutionError,
    ErrorCode,
)

__all__ = [
    "WorkflowLearningService",
    "LearnedWorkflow",
    "WorkflowStep",
    "LearningRecord",
    "WorkflowExecutionResult",
    "WorkflowLearningError",
    "WorkflowNotFoundError",
    "WorkflowExecutionError",
    "ErrorCode",
]

__version__ = "1.0.0"
