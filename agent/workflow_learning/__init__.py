"""云枢智能工作流学习系统 (Workflow Learning)

核心能力:
    1. learner: 从成功的 LLM 交互中提取方法 (工具调用序列 + 参数模板)
    2. generator: 自动生成可执行工作流 (LearnedWorkflow)
    3. repository: 本地工作流仓库 (data/learned_workflows.json)
    4. matcher: TF-IDF + 余弦相似度匹配新任务到已有工作流
    5. executor: 优先执行本地工作流，避免冗余 LLM 调用
    6. blackboard: 步骤间类型化数据传递 (SharedBlackboard)
    7. mode_classifier: DAG vs Agent 模式分类 (docs/workflow_dag_vs_agent.md)
    8. admission: 准入门槛（单字触发词 / 步骤数下限 / 跨会话样本数）——
       不达标者不入匹配候选、不得自动转技能，只留草稿态 (TASK-S10-01)
    9. retirement: 存量脏工作流退役（只标记不删除 + 追加式审计台账）

设计原则:
    - 本地优先: 新任务到达时先查本地仓库；命中且置信度高时跳过 LLM
    - 可观测: 全程结构化日志 + 业务指标
    - 边界显性化: 学习失败/执行失败均抛 WorkflowLearningError
    - 准入显性化 (TASK-S10-01): 判定条件集中一处 (admission)，三处复用
      (matcher / learner / skill_converter)，`force` 只越过统计门控
"""

from .service import WorkflowLearningService
from . import admission
from . import retirement
from .models import (
    LearnedWorkflow,
    WorkflowStep,
    LearningRecord,
    WorkflowExecutionResult,
)
from .blackboard import SharedBlackboard
from .mode_classifier import (
    classify_workflow_mode,
    count_branches,
    AGENT_BRANCH_THRESHOLD,
    AGENT_STEP_THRESHOLD,
)
from .agent_executor import AgentExecutor, AgentRunner
from .exceptions import (
    WorkflowLearningError,
    WorkflowNotFoundError,
    WorkflowExecutionError,
    WorkflowSchemaError,
    ErrorCode,
)

__all__ = [
    "WorkflowLearningService",
    "admission",
    "retirement",
    "LearnedWorkflow",
    "WorkflowStep",
    "LearningRecord",
    "WorkflowExecutionResult",
    "SharedBlackboard",
    "classify_workflow_mode",
    "count_branches",
    "AGENT_BRANCH_THRESHOLD",
    "AGENT_STEP_THRESHOLD",
    "AgentExecutor",
    "AgentRunner",
    "WorkflowLearningError",
    "WorkflowNotFoundError",
    "WorkflowExecutionError",
    "WorkflowSchemaError",
    "ErrorCode",
]

__version__ = "1.2.0"
