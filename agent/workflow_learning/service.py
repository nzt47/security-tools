"""工作流学习总服务 — 组合 learner/generator/repository/matcher/executor"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from .models import (
    LearnedWorkflow,
    LearningRecord,
    WorkflowExecutionResult,
    WorkflowStatus,
)
from .exceptions import (
    WorkflowNotFoundError,
    WorkflowLearningError,
)
from .observability import logger, traced_action
from .repository import WorkflowRepository
from .matcher import WorkflowMatcher
from .learner import WorkflowLearner
from .generator import WorkflowGenerator
from .executor import WorkflowExecutor, ToolExecutor


class WorkflowLearningService:
    """工作流学习总服务"""

    def __init__(self, *, repo_path: Optional[str] = None,
                 min_similarity: float = 0.3,
                 min_confidence: float = 0.4,
                 min_score: float = 0.3,
                 tool_validator: Optional[Callable[[str], bool]] = None,
                 tool_executor: Optional[ToolExecutor] = None):
        self.repo = WorkflowRepository(path=repo_path)
        self.matcher = WorkflowMatcher(
            min_similarity=min_similarity,
            min_confidence=min_confidence,
        )
        self.learner = WorkflowLearner()
        self.generator = WorkflowGenerator(
            self.repo, self.matcher, tool_validator=tool_validator,
        )
        self.executor = WorkflowExecutor(
            self.repo, self.matcher,
            min_score=min_score, tool_executor=tool_executor,
        )
        # 启动时从仓库重建索引
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """从仓库重建匹配器索引"""
        workflows = self.repo.list_all()
        self.matcher.rebuild(workflows)
        logger.info("[Service] 已加载 %d 个本地工作流到索引", len(workflows))

    # ─── 学习入口 ───

    def learn_from_interaction(self, record: LearningRecord) -> LearnedWorkflow:
        """从一次成功的 LLM 交互中学习方法并保存"""
        with traced_action("svc_learn", session_id=record.session_id):
            wf = self.learner.learn(record)
            return self.generator.generate_and_store(wf)

    # ─── 匹配执行入口 (主接口) ───

    def try_execute(self, task_text: str, *,
                    params: Optional[Dict[str, Any]] = None) -> WorkflowExecutionResult:
        """新任务到达时先尝试本地工作流"""
        return self.executor.try_execute(task_text, params=params)

    def execute_by_id(self, wf_id: str, task_text: str, *,
                      params: Optional[Dict[str, Any]] = None) -> WorkflowExecutionResult:
        return self.executor.execute_by_id(wf_id, task_text, params=params)

    # ─── 查询 ───

    def list_workflows(self, *, enabled_only: bool = False) -> List[LearnedWorkflow]:
        return self.repo.list_all(enabled_only=enabled_only)

    def get(self, wf_id: str) -> LearnedWorkflow:
        wf = self.repo.get(wf_id)
        if not wf:
            raise WorkflowNotFoundError(wf_id)
        return wf

    def search(self, task_text: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
        """模拟匹配，返回候选列表 (不执行)"""
        candidates = self.matcher.match(task_text, top_k=top_k)
        return [{
            "workflow_id": wf.id,
            "workflow_name": wf.name,
            "similarity": score,
            "confidence": wf.confidence,
            "priority": wf.priority,
            "steps": len(wf.steps),
            "description": wf.description,
        } for wf, score in candidates]

    # ─── 管理 ───

    def set_enabled(self, wf_id: str, enabled: bool) -> LearnedWorkflow:
        wf = self.get(wf_id)
        wf.enabled = enabled
        wf.touch()
        self.repo.upsert(wf)
        self.matcher.register(wf)
        return wf

    def delete(self, wf_id: str) -> bool:
        wf = self.repo.get(wf_id)
        if not wf:
            raise WorkflowNotFoundError(wf_id)
        self.repo.remove(wf_id)
        self.matcher.unregister(wf_id)
        return True

    def update_priority(self, wf_id: str, priority: int) -> LearnedWorkflow:
        wf = self.get(wf_id)
        wf.priority = max(0, min(100, priority))
        wf.touch()
        self.repo.upsert(wf)
        self.matcher.register(wf)
        return wf

    # ─── 健康检查 ───

    def health(self) -> Dict[str, Any]:
        repo_health = self.repo.health()
        all_wf = self.repo.list_all()
        return {
            "ok": repo_health.get("ok", False),
            "module": "workflow_learning",
            "version": "1.0.0",
            "repo": repo_health,
            "stats": {
                "total": len(all_wf),
                "enabled": sum(1 for w in all_wf if w.enabled),
                "active": sum(
                    1 for w in all_wf
                    if w.status == WorkflowStatus.ACTIVE.value),
                "total_success": sum(w.success_count for w in all_wf),
                "total_failure": sum(w.failure_count for w in all_wf),
                "avg_confidence": (
                    sum(w.confidence for w in all_wf) / len(all_wf)
                    if all_wf else 0.0
                ),
            },
            "matcher": {
                "min_similarity": self.matcher.min_similarity,
                "min_confidence": self.matcher.min_confidence,
                "indexed": len(self.matcher._workflows),
            },
            "executor": {
                "min_score": self.executor.min_score,
                "tool_executor_set": self.executor._tool_executor is not None,
            },
        }

    # ─── 工具执行器注入 ───

    def set_tool_executor(self, executor: ToolExecutor) -> None:
        self.executor.set_tool_executor(executor)
