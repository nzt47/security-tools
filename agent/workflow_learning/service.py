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

    # ─── 工作流 → 技能 转换 ───

    def convert_to_skill(self, wf_id: str, *,
                         skills_service=None,
                         force: bool = False) -> Dict[str, Any]:
        """把指定工作流抽象为 Skill 并注册到 skills_mgmt

        Args:
            wf_id: 工作流ID
            skills_service: SkillsMgmtService 实例（None 时延迟导入全局单例）
            force: 是否跳过质量门控

        Returns:
            {workflow_id, skill_id, skill_name, version, action}

        Raises:
            WorkflowNotFoundError: 工作流不存在
            WorkflowConvertError: 未通过质量门控
        """
        svc = skills_service or self._resolve_skills_service()
        converter = self._build_converter(svc)
        return converter.convert_workflow_to_skill(wf_id, force=force)

    def convert_external_skill(self, external_data: Dict[str, Any],
                               *, llm_client=None,
                               skills_service=None,
                               target_id: str = "") -> Dict[str, Any]:
        """把外部 agent 的技能描述翻译为云枢 SKILL 并注册

        Args:
            external_data: 外部技能描述 (JSON dict)
            llm_client: 可选 LLM 客户端（None 走规则转换）
            skills_service: SkillsMgmtService 实例
            target_id: 指定目标 skill_id

        Returns:
            {skill_id, skill_name, source_format, action}
        """
        svc = skills_service or self._resolve_skills_service()
        converter = self._build_converter(svc)
        return converter.convert_external_skill(
            external_data, llm_client, target_id=target_id,
        )

    def list_convertible_workflows(self) -> List[Dict[str, Any]]:
        """列出当前可转换为 Skill 的工作流（满足质量门控且未转换过）"""
        from .skill_converter import (
            MIN_SUCCESS_COUNT, MIN_CONFIDENCE, MIN_PRIORITY,
        )
        candidates = []
        for wf in self.repo.list_all(enabled_only=True):
            if wf.converted_to_skill_id:
                continue
            if (wf.status == WorkflowStatus.ACTIVE.value
                    and wf.success_count >= MIN_SUCCESS_COUNT
                    and wf.confidence >= MIN_CONFIDENCE
                    and wf.priority >= MIN_PRIORITY):
                candidates.append({
                    "workflow_id": wf.id,
                    "name": wf.name,
                    "success_count": wf.success_count,
                    "failure_count": wf.failure_count,
                    "confidence": wf.confidence,
                    "priority": wf.priority,
                    "last_used_at": wf.last_used_at,
                })
        return candidates

    # ─── 批量 LLM 转换外部 agent 技能 ───

    def batch_convert_external_skills(self, external_skills: List[Dict[str, Any]],
                                       *, llm_client=None,
                                       skills_service=None,
                                       merge_threshold: float = 0.85,
                                       strengthen_threshold: float = 0.7) -> Dict[str, Any]:
        """批量把外部 agent 的技能转换为本地技能并自动合并/加强/新建

        对每个外部技能执行:
            1. 调用 convert_external_skill 翻译 + 注册（LLM 或规则）
            2. 用 find_duplicates_for 检测与现有技能的 Jaccard 相似度
            3. 根据相似度选择动作:
                - Jaccard ≥ merge_threshold → 合并到现有技能（新建的作为 src 被删）
                - strengthen_threshold ≤ Jaccard < merge_threshold → 加强现有技能
                  （合并 tags/dependencies 到现有技能，删除新建的临时技能）
                - Jaccard < strengthen_threshold → 保留新建技能

        Args:
            external_skills: 外部技能列表（每个元素是 dict）
            llm_client: LLM 客户端（None 时走规则转换）
            skills_service: SkillsMgmtService 实例
            merge_threshold: 触发合并的 Jaccard 阈值（默认 0.85）
            strengthen_threshold: 触发加强的 Jaccard 阈值（默认 0.7）

        Returns:
            {total_input, converted, merged: [...], strengthened: [...],
             created: [...], failed: [...]}
        """
        with traced_action("svc_batch_convert_external",
                           total=len(external_skills),
                           merge_threshold=merge_threshold,
                           strengthen_threshold=strengthen_threshold):
            svc = skills_service or self._resolve_skills_service()
            converter = self._build_converter(svc)

            summary: Dict[str, Any] = {
                "total_input": len(external_skills),
                "converted": 0,
                "merged": [],
                "strengthened": [],
                "created": [],
                "failed": [],
            }

            for ext in external_skills:
                ext_name = ext.get("name", "") if isinstance(ext, dict) else ""
                try:
                    # 1. LLM 翻译 + 注册
                    conv = converter.convert_external_skill(ext, llm_client)
                    new_skill_id = conv["skill_id"]
                    summary["converted"] += 1

                    # 2. 检测与现有技能的相似度
                    try:
                        dups = svc.find_duplicates_for(
                            new_skill_id, min_jaccard=strengthen_threshold,
                        )
                    except Exception as e:
                        logger.warning(
                            "[BatchConvert] 重复检测失败 skill=%s: %s",
                            new_skill_id, e,
                        )
                        dups = []

                    if not dups:
                        summary["created"].append({
                            "skill_id": new_skill_id,
                            "skill_name": conv["skill_name"],
                            "source_format": conv.get("source_format", "unknown"),
                        })
                        continue

                    # 选相似度最高的（find_duplicates_for 返回的列表已按相似度降序）
                    best = max(
                        dups, key=lambda d: d.get("jaccard", 0.0),
                    )
                    jaccard = best.get("jaccard", 0.0)
                    # find_duplicates_for 返回的条目用 other_id 标识重复技能
                    # （兼容老接口的 skill_a/skill_b 字段）
                    existing_id = best.get("other_id") or best.get("skill_b")
                    if not existing_id:
                        # 兜底：跳过这一对（数据结构异常）
                        summary["created"].append({
                            "skill_id": new_skill_id,
                            "skill_name": conv["skill_name"],
                            "fallback": "no_existing_id",
                        })
                        continue

                    if jaccard >= merge_threshold:
                        # 3a. 合并：新建技能作为 src 被合并到 existing
                        try:
                            merge_result = svc.merge_duplicate_skills(
                                new_skill_id, existing_id,
                                strategy="keep_dst",
                            )
                            summary["merged"].append({
                                "external_name": ext_name,
                                "new_skill_id": new_skill_id,
                                "merged_into": existing_id,
                                "jaccard": round(jaccard, 4),
                                "merged_fields": merge_result.get(
                                    "merged_fields", [],
                                ),
                            })
                        except Exception as e:
                            logger.warning(
                                "[BatchConvert] 合并失败 %s → %s: %s",
                                new_skill_id, existing_id, e,
                            )
                            summary["created"].append({
                                "skill_id": new_skill_id,
                                "skill_name": conv["skill_name"],
                                "fallback": "merge_failed",
                                "error": str(e),
                            })
                    else:
                        # 3b. 加强：把新技能的 tags/dependencies 合并到 existing
                        try:
                            new_skill = svc.get(new_skill_id)
                            existing = svc.get(existing_id)
                            added_tags = [
                                t for t in new_skill.tags
                                if t not in existing.tags
                            ]
                            added_deps = [
                                d for d in new_skill.dependencies
                                if d not in existing.dependencies
                            ]
                            patch: Dict[str, Any] = {}
                            if added_tags:
                                patch["tags"] = list(existing.tags) + added_tags
                            if added_deps:
                                patch["dependencies"] = (
                                    list(existing.dependencies) + added_deps
                                )
                            if patch:
                                svc.update(existing_id, patch)
                            # 删除新建的临时技能
                            svc.delete(new_skill_id)
                            summary["strengthened"].append({
                                "external_name": ext_name,
                                "strengthened_skill_id": existing_id,
                                "jaccard": round(jaccard, 4),
                                "added_tags": added_tags,
                                "added_deps": added_deps,
                            })
                        except Exception as e:
                            logger.warning(
                                "[BatchConvert] 加强失败 %s → %s: %s",
                                new_skill_id, existing_id, e,
                            )
                            summary["created"].append({
                                "skill_id": new_skill_id,
                                "skill_name": conv["skill_name"],
                                "fallback": "strengthen_failed",
                                "error": str(e),
                            })
                except Exception as e:
                    summary["failed"].append({
                        "external_name": ext_name,
                        "error": str(e),
                    })

            # 埋点
            try:
                from .observability import track_event
                track_event("batch_convert_external_skills", {
                    "total_input": summary["total_input"],
                    "converted": summary["converted"],
                    "merged_count": len(summary["merged"]),
                    "strengthened_count": len(summary["strengthened"]),
                    "created_count": len(summary["created"]),
                    "failed_count": len(summary["failed"]),
                })
            except Exception:
                pass

            logger.info(
                "[BatchConvert] 完成: 输入=%d, 转换=%d, 合并=%d, 加强=%d, "
                "新建=%d, 失败=%d",
                summary["total_input"], summary["converted"],
                len(summary["merged"]), len(summary["strengthened"]),
                len(summary["created"]), len(summary["failed"]),
            )
            return summary

    # ─── 内部辅助 ───

    def _resolve_skills_service(self):
        """延迟导入 SkillsMgmtService 全局单例（避免循环依赖）"""
        try:
            from agent.state_manager import get_skills_mgmt_service
            return get_skills_mgmt_service()
        except Exception:
            # 兜底：直接构造（使用默认存储）
            from agent.skills_mgmt.service import SkillsMgmtService
            return SkillsMgmtService()

    def _build_converter(self, skills_service):
        """构造 SkillConverter 实例"""
        from .skill_converter import WorkflowToSkillConverter
        return WorkflowToSkillConverter(skills_service, self.repo)
