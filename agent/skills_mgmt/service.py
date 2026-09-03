"""技能管理总服务 — 组合所有子服务为一个易用门面

提供:
    - SkillsMgmtService.create_via_ai / create_manual / install
    - SkillsMgmtService.review / search / get / list_all / delete
    - SkillsMgmtService.bump_version / list_versions / rollback_version
    - SkillsMgmtService.optimize_params / record_execution / set_enabled
    - SkillsMgmtService.health
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from .lineage import (
    EvolutionArchive,
    EvolutionRecord,
    get_default_archive,
    print_lineage,
)
from .models import (
    Skill,
    SkillSearchParams,
    SkillSearchResult,
    SkillStatus,
    ReviewResult,
    ReviewStatus,
    SkillVersion,
)
from .exceptions import (
    SkillNotFoundError,
    SkillMgmtError,
    ErrorCode,
)
from .observability import logger, traced_action, emit_metric
from .store import SkillStore
from .creator import SkillCreator
from .reviewer import SkillReviewer, ReviewThresholds
from .searcher import SkillSearcher
from .enhancer import SkillEnhancer, VersionBump, IntegrationHook
from .file_store import SkillFileStore
from .index_cache import SkillIndexCache
from .loader import SkillLoader, MatchResult
from .executor import SkillExecutor, ExecutionResult
from .context_injector import ContextInjector
from .output_guard import SkillOutputGuard


class SkillsMgmtService:
    """技能管理总服务 (单例建议)"""

    def __init__(self, *, store_path: Optional[str] = None,
                 llm_client: Optional[Any] = None,
                 http_timeout: int = 15,
                 review_thresholds: Optional[ReviewThresholds] = None,
                 repo_path: Optional[str] = None):
        self.store = SkillStore(path=store_path)
        self.creator = SkillCreator(self.store, llm_client=llm_client,
                                    http_timeout=http_timeout)
        self.reviewer = SkillReviewer(thresholds=review_thresholds)
        self.searcher = SkillSearcher()
        self.enhancer = SkillEnhancer(self.store)
        # EVO-T1 谱系档案库：默认取全局单例，测试可注入隔离实例
        self._lineage_archive = get_default_archive()

        # 三层架构组件
        self.file_store = SkillFileStore(repo_path=repo_path)
        # [变易] 技能索引缓存：预解析 front matter，启动时加载
        # （缓存加载失败自动降级为运行时解析，不影响功能；守【不易】接口不变）
        self.index_cache = SkillIndexCache(self.file_store)
        self.index_cache.load_on_startup()
        self.loader = SkillLoader(self.file_store)
        self.executor = SkillExecutor(self.file_store)
        self.injector = ContextInjector(self.loader)
        self._mcp_adapter = None  # 延迟初始化 MCP 适配器

        # EVO-T6 安全护栏组件（懒加载，测试可注入隔离实例）
        self._approval = None  # ApprovalFlow 实例（None=未接入审批流）
        self._auto_rollback = None  # AutoRollback 懒加载单例缓存

    @property
    def mcp_adapter(self):
        """延迟初始化 MCP 适配器 (首次访问时创建)"""
        if self._mcp_adapter is None:
            from .mcp_adapter import McpSkillAdapter
            self._mcp_adapter = McpSkillAdapter(self)
        return self._mcp_adapter

    # ─── EVO-T6 安全护栏网关（供 UI/CLI 消费）───

    @property
    def auto_rollback(self) -> "AutoRollback":
        """自动回滚懒加载单例（默认 restorer=None=仅记录不实际恢复）"""
        if self._auto_rollback is None:
            from .rollback import AutoRollback
            self._auto_rollback = AutoRollback(archive=self._lineage_archive)
        return self._auto_rollback

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """列出全部待审批记录（未接入审批流时返回空列表）"""
        if self._approval is None:
            return []
        return [
            {
                "record_id": r.record_id,
                "object_type": r.object_type,
                "object_id": r.object_id,
                "level": r.level,
                "state": r.state,
                "created_at": r.created_at,
            }
            for r in self._approval.list({"state": "pending_review"})
        ]

    def approve_change(self, record_id: str, actor: str = "reviewer",
                       note: str = "") -> Dict[str, Any]:
        """审批通过网关（透传 ApprovalFlow.approve）"""
        if self._approval is None:
            raise RuntimeError("审批流未接入（_approval 未注入），无法审批")
        rec = self._approval.approve(record_id, actor=actor, note=note)
        return {"record_id": rec.record_id, "state": rec.state,
                "level": rec.level}

    def reject_change(self, record_id: str, actor: str = "reviewer",
                      reason: str = "") -> Dict[str, Any]:
        """驳回网关（透传 ApprovalFlow.reject，reason 必填）"""
        if self._approval is None:
            raise RuntimeError("审批流未接入（_approval 未注入），无法驳回")
        rec = self._approval.reject(record_id, actor=actor, reason=reason)
        return {"record_id": rec.record_id, "state": rec.state,
                "decision_reason": rec.decision_reason}

    def get_evolution_audit(self, days: int = 7) -> Dict[str, Any]:
        """进化审计视图网关（透传 dashboard.get_evolution_audit）"""
        from agent.health.dashboard import get_evolution_audit as _audit
        return _audit(days=days, archive=self._lineage_archive,
                      approvals=self._approval)

    def run_auto_rollback(self, object_id: str, version: str,
                          metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行自动回滚判定网关（无基线安全返回 triggered=False）"""
        result = self.auto_rollback.check_and_rollback(
            object_id, version, metrics)
        return {
            "triggered": result.triggered,
            "reason": result.reason,
            "suppressed": result.suppressed,
            "halted": result.halted,
            "details": result.details,
        }

    # ─── 创建 ───

    def create_via_ai(self, *, name: str, intent: str,
                      category: str = "custom",
                      tags: Optional[list] = None) -> Skill:
        skill = self.creator.create_via_ai(
            name=name, intent=intent, category=category, tags=tags)
        self._advisory_digest(skill.id)   # 自动执行评审-消化（咨询性，不改状态）
        return self._require(skill.id)    # 返回含自动评估报告的最新对象

    def create_manual(self, data: Dict[str, Any]) -> Skill:
        skill = self.creator.create_manual(data)
        self._advisory_digest(skill.id)   # 自动执行评审-消化（咨询性，不改状态）
        return self._require(skill.id)

    def install(self, source: str, *, force: bool = False) -> Skill:
        skill = self.creator.install(source, force=force)
        self._advisory_digest(skill.id)   # 外来技能进入即自动评审-消化
        return self._require(skill.id)

    def install_from_zip(self, zip_path: str) -> Dict[str, Any]:
        """从 zip 技能包安装到三层架构文件仓库

        Args:
            zip_path: zip 文件路径

        Returns:
            dict — {skill_id, name, version, scripts_count}
        """
        from .skill_manager import SkillManager
        mgr = SkillManager(repo_path=str(self.file_store.repo_path))
        skill_id = mgr.install_from_zip(zip_path)
        meta = self.file_store.get_metadata(skill_id) or {}
        scripts = self.file_store.list_scripts(skill_id)
        return {
            "skill_id": skill_id,
            "name": meta.get("name", skill_id),
            "version": meta.get("version", "0.0.0"),
            "scripts_count": len(scripts),
        }

    # ─── 审核 ───

    def review(self, skill_id: str) -> ReviewResult:
        """审核指定技能 (与所有其他技能做重复检测)"""
        with traced_action("svc_review", skill_id=skill_id):
            skill = self._require(skill_id)
            others = [s for s in self.store.list_all() if s.id != skill_id]
            result = self.reviewer.review(skill, others=others)
            self.store.upsert(skill)  # 持久化审核结果
            return result

    def review_all_pending(self) -> List[Dict[str, Any]]:
        """批量审核所有 pending_review 状态的技能"""
        results = []
        for s in self.store.list_all():
            if s.status == SkillStatus.PENDING_REVIEW.value:
                try:
                    r = self.review(s.id)
                    results.append({
                        "skill_id": s.id, "status": r.status, "score": r.score,
                    })
                except SkillMgmtError as e:
                    results.append({"skill_id": s.id, "error": e.message})
        return results

    # ─── 评审-消化（自动评估钩子 + 批量）───

    def _advisory_digest(self, skill_id: str) -> Optional[ReviewResult]:
        """新增/外来技能进入时自动执行扩展评估（权限/攻击面/数据合规 + 兼容性）。

        设计（守现有契约）：
            - 仅在技能尚无 review 时写入咨询性报告（status 保持 draft 等原语义），
              不改技能/审核状态——正式审核（review/digest_skill）才是权威判据；
            - 自动钩子在 create_via_ai / create_manual / install / update 后触发，
              让“新增及现有技能”一进来就有评估报告可见。
        """
        try:
            skill = self._require(skill_id)
            if skill.review is not None:
                return skill.review
            from .assessor import SkillDigestAssessor
            others = [s for s in self.store.list_all() if s.id != skill_id]
            digest = SkillDigestAssessor().assess(skill, others=others)
            skill.review = ReviewResult(
                status=ReviewStatus.PENDING,
                findings=digest.findings,
                compatibility_score=digest.compatibility_score,
                auto_assessed=True,
                digest_verdict="block" if digest.blocked else "ok",
                dimension_summary=digest.dimension_summary,
                summary=("自动评审-消化：扩展评估未发现阻断项，可执行正式审核后发布"
                         if not digest.blocked
                         else "自动评审-消化：扩展评估存在阻断项(权限/合规/兼容性)，待人工复核"),
            )
            skill.touch()
            self.store.upsert(skill)
            return skill.review
        except Exception as e:  # noqa: BLE001 评估失败不阻断创建/安装主流程
            logger.warning("[Service] advisory digest 失败 skill=%s: %s",
                           skill_id, e)
            return None

    def digest_skill(self, skill_id: str) -> ReviewResult:
        """对单个技能执行权威「评审-消化」= 完整审核链（三审 + 扩展评估）。

        即 review() 的语义别名：便于 UI/路由按“消化”心智调用；
        扩展阻断项会把审核状态置 WARN、技能置 PENDING_REVIEW（发布门禁保持）。
        """
        return self.review(skill_id)

    def digest_all(self) -> Dict[str, Any]:
        """对“尚无审核结果的现有技能”批量执行咨询性自动评审-消化。

        Returns:
            {total, assessed, blocked, with_review} — assessed 为本次新增评估数；
            blocked 为存在阻断项的技能数。已有人工/正式审核记录的不覆盖。
        """
        total = 0
        assessed = 0
        blocked = 0
        for s in self.store.list_all():
            total += 1
            if s.review is not None:
                continue
            r = self._advisory_digest(s.id)
            if r is not None:
                assessed += 1
                if r.digest_verdict == "block":
                    blocked += 1
        return {"total": total, "assessed": assessed, "blocked": blocked}

    def audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取人工复核/强制发布审计记录（供技能中心可视化）"""
        try:
            from .review_gate import read_audit_log
            return read_audit_log(limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 读取审计日志失败: %s", e)
            return []

    # ─── 发布（TASK-04 Step 3 强制审核链）───

    def publish(self, skill_id: str, *, force: bool = False,
                actor: str = "manual", reason: str = "") -> Skill:
        """发布技能：终态/驳回态拒绝 → 强制审核链 → 置 PUBLISHED

        Step 3 发布强制审核链：enforce_before_publish 默认 true（无 PASSED
        ReviewResult 禁止发布）；配置关闭或 force=True 显式豁免必须写审计日志
        （event=review_waiver_publish）。
        """
        from .exceptions import SkillReviewError

        skill = self._require(skill_id)
        if skill.status in (SkillStatus.PUBLISHED, SkillStatus.ARCHIVED,
                            SkillStatus.REJECTED):
            raise SkillReviewError("当前状态不可发布")
        from .review_gate import enforce_review
        enforce_review(skill, force=force, actor=actor, reason=reason)
        skill.status = SkillStatus.PUBLISHED
        skill.touch()
        self.store.upsert(skill)
        emit_metric("yunshu_skill_publish_total")
        logger.info("[Service] 技能已发布: %s (force=%s, actor=%s)",
                    skill_id, force, actor)
        return skill

    # ─── 搜索 ───

    def search(self, params: SkillSearchParams) -> SkillSearchResult:
        return self.searcher.search(self.store.list_all(), params)

    def list_all(self) -> List[Skill]:
        return self.store.list_all()

    def get(self, skill_id: str) -> Skill:
        return self._require(skill_id)

    # ─── 增删改 ───

    def update(self, skill_id: str, patch: Dict[str, Any]) -> Skill:
        """部分更新技能字段"""
        skill = self._require(skill_id)
        data = skill.model_dump()
        # 白名单字段
        allowed = {"name", "description", "tags", "content", "content_type",
                   "config_schema", "default_params", "dependencies",
                   "author", "enabled",
                   # [变易] 敏感技能隔离字段透传（create/update 双向闭环）
                   "is_sensitive", "isolation_strategy"}
        for k, v in patch.items():
            if k in allowed:
                data[k] = v
        updated = Skill.from_storage_dict(data)
        updated.touch()
        self.store.upsert(updated)
        self._advisory_digest(updated.id)  # 修改后自动重新评估（尚无正式审核时）
        return updated

    def delete(self, skill_id: str) -> bool:
        ok = self.store.remove(skill_id)
        if not ok:
            raise SkillNotFoundError(skill_id)
        logger.info("[Service] 技能已删除: %s", skill_id)
        return True

    # ─── 增强器代理 ───

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "", content: Optional[str] = None,
                     eval_result: Optional[Dict[str, Any]] = None) -> VersionBump:
        return self.enhancer.bump_version(
            skill_id, kind, changelog=changelog, content=content,
            eval_result=eval_result)

    def list_versions(self, skill_id: str) -> List[SkillVersion]:
        return self.enhancer.list_versions(skill_id)

    def rollback_version(self, skill_id: str, target_version: str) -> Skill:
        return self.enhancer.rollback_version(skill_id, target_version)

    def optimize_params(self, skill_id: str,
                        feedback_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.enhancer.optimize_params(skill_id,
                                             feedback_summary=feedback_summary)

    def record_execution(self, skill_id: str, *,
                         success: bool, latency_ms: float,
                         feedback_rating: int = 0,
                         feedback_id: str = "",
                         trace_id: str = "",
                         params_used: Optional[Dict[str, Any]] = None,
                         # [变易] 全链路可观测性扩展：端到端评估得分（可选）
                         # 缺省 None 保证旧调用方不受影响（守不易）
                         eval_score: Optional[Dict[str, Any]] = None,
                         # [变易] Dynamic Few-shot 自动采集（可选）
                         # 仅 feedback_rating=5 且 success=True 且提供输入/输出文本时
                         # 才采集成功案例入示例库；缺省 None 不影响旧调用方（守不易）
                         input_text: Optional[str] = None,
                         output_text: Optional[str] = None) -> None:
        """记录一次技能执行并可选持久化端到端评估得分

        Args:
            eval_score: 可选评估得分 {task_success, instruction_followed,
                hallucination_detected, score}；提供时发射
                yunshu_skill_eval_score / yunshu_skill_hallucination_total
                指标并持久化到 trace span（失败不影响主流程）。
            input_text: 可选用户输入文本；feedback_rating=5 且 success=True 时
                自动采集为 Few-shot 示例（带去重）。
            output_text: 可选执行输出文本；同上，仅成功案例采集。
        """
        self.enhancer.record_execution(
            skill_id, success=success, latency_ms=latency_ms,
            feedback_rating=feedback_rating,
            feedback_id=feedback_id, trace_id=trace_id,
            params_used=params_used)

        # [变易] 可观测性扩展：发射 eval_score 指标并持久化 span
        # 内部全部 try/except，失败不影响主流程
        if eval_score is not None:
            from .observability import emit_eval_score_metric
            emit_eval_score_metric(skill_id, eval_score, trace_id=trace_id)
            # [Observability] INFO 级别：eval_score 详情，正式环境可观测
            logger.info(
                "[Observability] service.record_execution eval_score | skill_id=%s | "
                "trace_id=%s | task_success=%s | hallucination=%s | score=%s",
                skill_id, trace_id or "(none)",
                eval_score.get("task_success"),
                eval_score.get("hallucination_detected"),
                eval_score.get("score"),
            )

        # [变易] Dynamic Few-shot 示例自动采集：
        # 仅 rating=5 的成功案例（宁缺毋滥），失败不影响主流程
        if feedback_rating == 5 and success and input_text and output_text:
            try:
                self._collect_few_shot_example(
                    skill_id, input_text=input_text,
                    output_text=output_text, trace_id=trace_id,
                )
            except Exception as e:  # noqa: BLE001 采集失败不影响主流程
                logger.warning(
                    "[Service] few-shot 示例采集失败 skill_id=%s: %s",
                    skill_id, e,
                )

    # ─── 反馈绑定 ───

    def submit_skill_feedback(self, skill_id: str, *,
                               trace_id: str,
                               feedback_type: str,
                               rating: int = 0,
                               comment: str = "",
                               category: str = "other",
                               user_id: str = "",
                               session_id: str = "",
                               context: Optional[Dict[str, Any]] = None,
                               workflow_id: str = "") -> Dict[str, Any]:
        """提交针对某技能的用户反馈

        将 feedback 模块与 skills_mgmt 模块打通：
        - 反馈落库到 feedback 表（带 skill_id）
        - 触发 enhancer.record_execution 同步指标
        - 返回 feedback 记录 + 后续建议动作

        Raises:
            SkillNotFoundError: 技能不存在
            ValueError: 参数非法
        """
        with traced_action("svc_submit_skill_feedback",
                           skill_id=skill_id,
                           feedback_type=feedback_type,
                           rating=rating):
            # 先校验技能存在（边界显性化）
            self._require(skill_id)

            from agent.feedback import get_feedback_manager
            mgr = get_feedback_manager()
            record = mgr.submit_feedback(
                trace_id=trace_id,
                feedback_type=feedback_type,
                rating=rating,
                comment=comment,
                category=category,
                user_id=user_id,
                session_id=session_id,
                context=context or {"skill_id": skill_id},
                skill_id=skill_id,
                workflow_id=workflow_id,
            )

            # 同步更新技能指标
            self.enhancer.record_execution(
                skill_id,
                success=(feedback_type == "like"),
                latency_ms=0.0,
                feedback_rating=rating,
                feedback_id=record.feedback_id,
                trace_id=trace_id,
            )

            summary = mgr.get_skill_feedback_summary(skill_id)
            return {
                "feedback": record.to_dict(),
                "summary": summary,
            }

    def get_skill_feedback_summary(self, skill_id: str,
                                  days: int = 30) -> Dict[str, Any]:
        """获取技能反馈聚合统计"""
        self._require(skill_id)
        return self.enhancer.get_skill_feedback_summary(skill_id, days=days)

    def optimize_with_feedback(self, skill_id: str,
                               days: int = 30) -> Dict[str, Any]:
        """一键式：拉取反馈 + 触发参数优化"""
        self._require(skill_id)
        return self.enhancer.optimize_with_feedback(skill_id, days=days)

    # ─── 进化谱系只读路由（EVO-T1）───

    @property
    def _evolution_archive(self) -> EvolutionArchive:
        """当前谱系档案库（测试可注入隔离实例）"""
        return self._lineage_archive

    @_evolution_archive.setter
    def _evolution_archive(self, archive: EvolutionArchive) -> None:
        self._lineage_archive = archive

    def set_lineage_hook(self, hook: Optional[Callable[[dict], None]]) -> None:
        """注入外部谱系钩子（透传 enhancer，供审批/审计流复用）"""
        self.enhancer.set_lineage_hook(hook)

    def get_evolution_lineage(self, skill_id: str) -> List[EvolutionRecord]:
        """查询技能完整进化链（只读路由；无记录返回空列表，不抛异常）"""
        return self._lineage_archive.get_lineage(skill_id)

    def print_evolution_lineage(self, skill_id: str) -> str:
        """打印技能进化链文本（审计/CLI 用）"""
        return print_lineage(skill_id, self._lineage_archive)

    # ─── 进化循环网关（EVO-T3）───

    def _new_evolver(self) -> "OfflineEvolver":
        """构建共享同一档案库实例的进化器（延迟导入避免循环依赖）"""
        from .offline_evolver import OfflineEvolver
        return OfflineEvolver(
            self.store, self.enhancer, archive=self._evolution_archive)

    def _build_real_evaluator(self, skill: Any) -> Optional[Any]:
        """构建任务 2 真实评估器；失败返回 None（evolver 回退启发式）"""
        try:
            from .evaluator import get_default_evaluator
            return get_default_evaluator(skill)
        except Exception as e:  # noqa: BLE001 评估器不可用不阻塞进化
            logger.warning("[SkillsMgmtService] 真实评估器构建失败: %s", e)
            return None

    def evolve_skill(self, skill_id: str, *,
                     strategies: Optional[List[str]] = None,
                     trigger: str = "api") -> Dict[str, Any]:
        """手动触发单技能进化（默认接入真实评估器，EVO-T3）

        触发即写谱系（提交/拒绝/跳过均产生 EvolutionRecord）。

        Returns:
            dict — {skill_id, committed, decision, improvement, score,
                    new_version, parent_record_id, record_id, cost_tokens}
        """
        self._require(skill_id)
        from .offline_evolver import EvolutionStrategy
        strategies_enum = None
        if strategies:
            strategies_enum = [
                EvolutionStrategy(s) for s in strategies
                if EvolutionStrategy(s) in EvolutionStrategy]
        evolver = self._new_evolver()
        result = evolver.evolve_once(
            skill_id,
            strategies=strategies_enum,
            evaluator=self._build_real_evaluator(self.store.get(skill_id)),
            trigger=trigger)
        return {
            "skill_id": result.skill_id,
            "committed": result.committed,
            "decision": result.decision,
            "improvement": result.improvement,
            "score": result.score,
            "new_version": result.new_version,
            "parent_record_id": result.parent_record_id,
            "record_id": result.record_id,
            "cost_tokens": result.cost_tokens,
            "budget_breached": result.budget_breached,
            "error": result.error,
        }

    def evolve_batch(self, skill_ids: Optional[List[str]] = None, *,
                     max_rounds: int = 1,
                     trigger: str = "api") -> Dict[str, Any]:
        """手动触发批量进化（默认接入真实评估器，EVO-T3）

        Returns:
            dict — 批量报告摘要（含成本汇总与评分序列）
        """
        evolver = self._new_evolver()
        evaluator = None
        if skill_ids:
            try:
                evaluator = self._build_real_evaluator(
                    self.store.get(skill_ids[0]))
            except Exception as e:  # noqa: BLE001
                logger.warning("[SkillsMgmtService] 批量评估器构建失败: %s", e)
        else:
            candidates = evolver._select_candidates()
            if candidates:
                evaluator = self._build_real_evaluator(candidates[0])
        report = evolver.evolve_batch(
            skill_ids, max_rounds=max_rounds, evaluator=evaluator,
            trigger=trigger)
        return {
            "trigger": report.trigger,
            "total_skills": report.total_skills,
            "evolved_count": report.evolved_count,
            "skipped_count": report.skipped_count,
            "failed_count": report.failed_count,
            "avg_improvement": report.avg_improvement,
            "cost_tokens": report.cost_tokens,
            "total_duration_ms": report.total_duration_ms,
            "budget_usage_ratio": report.budget_usage_ratio,
            "budget_breached": report.budget_breached,
            "score_series": report.score_series,
            "finished_at": report.finished_at,
        }

    def schedule_evolution(self, cron_expr: str = "0 2 * * *", *,
                           skill_ids: Optional[List[str]] = None,
                           max_rounds: int = 1) -> Dict[str, Any]:
        """注册进化 cron 调度（EVO-T3，默认关闭 — 安全底线）

        EVOLUTION_SCHEDULE_ENABLED=false 时只返回 disabled 信息；
        开启需 .env 配置 EVOLUTION_SCHEDULE_ENABLED=true。
        """
        return self._new_evolver().schedule(
            cron_expr, skill_ids=skill_ids, max_rounds=max_rounds)

    def unschedule_evolution(self) -> bool:
        """注销进化 cron 调度"""
        return self._new_evolver().unschedule()

    # ─── 重复技能检测与合并 ───

    def list_duplicates(self, min_jaccard: float = 0.7) -> List[Dict[str, Any]]:
        """扫描整个技能库，列出 Jaccard≥阈值的重复对

        Args:
            min_jaccard: 最小相似度阈值（默认 0.7）

        Returns:
            [{skill_a, skill_b, name_a, name_b, jaccard,
              content_hash_match, recommend_action}, ...]
            recommend_action: "merge" | "review"
        """
        skills = self.store.list_all()
        return self.reviewer.find_duplicates(skills, min_jaccard=min_jaccard)

    def find_duplicates_for(self, skill_id: str,
                            min_jaccard: float = 0.7) -> List[Dict[str, Any]]:
        """找出与指定技能重复的其他技能"""
        target = self._require(skill_id)
        others = [s for s in self.store.list_all() if s.id != skill_id]
        return self.reviewer.find_duplicates_for(
            target, others, min_jaccard=min_jaccard,
        )

    def merge_duplicate_skills(self, src_id: str, dst_id: str, *,
                                strategy: str = "auto",
                                rebind_feedback: bool = True) -> Dict[str, Any]:
        """合并两个重复技能

        Args:
            src_id: 被合并方ID（将被删除）
            dst_id: 合并保留方ID
            strategy: auto | keep_dst | keep_src
            rebind_feedback: 是否同时改绑 feedback 表（默认 True）

        Returns:
            {merged_id, removed_id, merged_fields, version_added,
             feedback_rebound_count}

        Raises:
            SkillNotFoundError: 任一技能不存在
            ValueError: src_id == dst_id
        """
        with traced_action("svc_merge_skills",
                           src_id=src_id, dst_id=dst_id,
                           strategy=strategy):
            # 边界显性化
            self._require(src_id)
            self._require(dst_id)
            if src_id == dst_id:
                raise ValueError(
                    f"src_id 与 dst_id 不能相同: {src_id}"
                )

            feedback_mgr = None
            if rebind_feedback:
                try:
                    from agent.feedback import get_feedback_manager
                    feedback_mgr = get_feedback_manager()
                except Exception as e:
                    logger.warning(
                        "[Service] 获取 feedback_manager 失败，跳过反馈改绑: %s", e
                    )

            result = self.store.merge_skills(
                src_id, dst_id,
                strategy=strategy,
                feedback_manager=feedback_mgr,
            )

            try:
                from .observability import track_event
                track_event("skills_merged", {
                    "merged_id": result["merged_id"],
                    "removed_id": result["removed_id"],
                    "merged_fields": result["merged_fields"],
                    "strategy": strategy,
                })
            except Exception:
                pass
            return result

    def auto_merge_duplicates(self, min_jaccard: float = 0.85,
                               max_merges: int = 10) -> Dict[str, Any]:
        """自动合并高相似度技能对（Jaccard ≥ min_jaccard）

        仅合并 recommend_action == "merge" 的对，避免误伤 review 类。
        每次合并后重新扫描，因为合并会改变剩余技能的相似度关系。

        Args:
            min_jaccard: 自动合并阈值（默认 0.85，比 review 的 0.7 更严格）
            max_merges: 单次最多合并多少对（防止意外批量操作）

        Returns:
            {scanned_pairs, merged_pairs: [...], skipped: int}
        """
        with traced_action("svc_auto_merge_duplicates",
                           min_jaccard=min_jaccard,
                           max_merges=max_merges):
            merged_pairs: List[Dict[str, Any]] = []
            total_scanned = 0

            # 迭代合并：每次合并一对后重新扫描，直到没有可合并对或达到上限
            while len(merged_pairs) < max_merges:
                duplicates = self.list_duplicates(min_jaccard=min_jaccard)
                merge_candidates = [
                    d for d in duplicates
                    if d["recommend_action"] == "merge"
                ]
                total_scanned = max(total_scanned, len(duplicates))

                if not merge_candidates:
                    break

                # 取相似度最高的一对
                dup = merge_candidates[0]
                a, b = dup["skill_a"], dup["skill_b"]
                try:
                    result = self.merge_duplicate_skills(a, b)
                    merged_pairs.append(result)
                except Exception as e:
                    logger.warning(
                        "[Service] 自动合并失败 %s ↔ %s: %s",
                        a, b, e,
                    )
                    break

            return {
                "scanned_pairs": total_scanned,
                "merged_pairs": merged_pairs,
                "skipped": 0,
            }

    def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        return self.enhancer.set_enabled(skill_id, enabled)

    def register_hook(self, hook: IntegrationHook) -> None:
        self.enhancer.register_hook(hook)

    # ─── 三层架构代理 (Layer 1/2/3) ───

    def match_skills(self, intent: str, *, top_k: int = 5,
                     enabled_only: bool = True,
                     min_score: float = 0.01) -> MatchResult:
        """Layer 1: 意图匹配 — 在元数据索引上做快速检索

        Args:
            intent: 用户意图文本 (自然语言或关键词)
            top_k: 返回前 K 个匹配结果
            enabled_only: 是否仅返回启用状态的技能
            min_score: 最低匹配分阈值 (低于此值过滤掉)

        Returns:
            MatchResult — 包含 matches 列表与统计信息

        Raises:
            SkillMgmtError: 匹配过程出错时抛出
        """
        with traced_action("svc_match_skills", intent=intent[:80],
                           top_k=top_k, layer=1) as ctx:
            try:
                result = self.loader.match(
                    intent, top_k=top_k,
                    enabled_only=enabled_only,
                    min_score=min_score,
                )
                ctx["matched"] = len(result.matches)
                ctx["elapsed_ms"] = result.elapsed_ms
                ctx["estimated_tokens"] = result.estimated_total_tokens
                # [变易] 可观测性扩展：透传 retrieved_chunks 到 traced_action
                # observability 层会自动对 >50 项截断
                ctx["retrieved_chunks"] = result.retrieved_chunks
                logger.info("[Service] Layer1 match intent='%s' → %d 命中, %.2fms",
                            intent[:40], len(result.matches), result.elapsed_ms)
                return result
            except SkillMgmtError:
                raise
            except Exception as e:
                logger.error("[Service] Layer1 match 失败: %s", e)
                raise SkillMgmtError(
                    f"意图匹配失败: {e}",
                    code=ErrorCode.INTERNAL_ERROR,
                    details={"intent": intent[:200]},
                ) from e

    def load_skill_instruction(self, skill_id: str) -> Dict[str, Any]:
        """Layer 2: 按需加载技能使用说明 (skill.md 正文)

        仅在 Layer 1 命中后才应调用此方法，避免无谓加载。

        Args:
            skill_id: 技能ID

        Returns:
            dict — {skill_id, instruction, estimated_tokens, layer}

        Raises:
            SkillNotFoundError: 技能不存在
            SkillFileError: skill.md 读取失败
        """
        with traced_action("svc_load_instruction", skill_id=skill_id, layer=2):
            return self.loader.load_instruction(skill_id)

    def execute_skill_script(self, skill_id: str,
                             script_name: str = "main.py",
                             params: Optional[Dict[str, Any]] = None,
                             timeout: Optional[float] = None) -> ExecutionResult:
        """Layer 3: 沙箱执行技能脚本

        脚本在独立子进程中执行，stdin 接收 JSON 参数，stdout 返回 JSON 结果。
        代码不进入 LLM 上下文，只有执行结果进入。

        Args:
            skill_id: 技能ID
            script_name: 脚本文件名 (必须位于技能的 scripts/ 目录)
            params: 传入脚本的 JSON 参数
            timeout: 执行超时秒数 (None=使用默认 30s)

        Returns:
            ExecutionResult — 包含 success/result/error/duration_ms

        Raises:
            SkillNotFoundError: 技能/脚本不存在
            SkillExecutionError: 执行超时或失败
        """
        with traced_action("svc_execute_script", skill_id=skill_id,
                           script=script_name, layer=3) as ctx:
            result = self.executor.execute(
                skill_id, script_name=script_name,
                params=params, timeout=timeout,
            )
            ctx["success"] = result.success
            ctx["duration_ms"] = result.duration_ms
            ctx["exit_code"] = result.exit_code
            # 埋点: 脚本执行成功率/延迟
            try:
                emit_metric("yunshu_skill_script_exec_total",
                            value=1,
                            labels={"success": str(result.success).lower(),
                                    "skill_id": skill_id},
                            kind="counter")
                emit_metric("yunshu_skill_script_latency_ms",
                            value=result.duration_ms,
                            labels={"skill_id": skill_id},
                            kind="histogram")
            except Exception:  # noqa: BLE001 埋点失败不影响主流程
                pass
            if not result.success:
                logger.warning(
                    "[Service] Layer3 exec %s/%s 失败 exit=%s dur=%.0fms",
                    skill_id, script_name, result.exit_code, result.duration_ms)
            else:
                logger.info(
                    "[Service] Layer3 exec %s/%s 成功 dur=%.0fms",
                    skill_id, script_name, result.duration_ms)
            return result

    def build_skill_context(self, intent: str, *,
                            max_tokens: int = 6000,
                            top_k: int = 5,
                            auto_load_instruction: bool = False,
                            skill_id: Optional[str] = None) -> Dict[str, Any]:
        """一站式构建 LLM 上下文 (Layer 1 + Layer 2)

        流程:
            1. Layer 1: 元数据匹配 → 返回 top_k 候选技能
            2. (可选) Layer 2: 若指定 skill_id 或 auto_load_instruction，
               则按需加载该技能的使用说明

        Args:
            intent: 用户意图
            max_tokens: 上下文 token 预算上限
            top_k: Layer 1 返回的最大候选数
            auto_load_instruction: 是否自动加载 top-1 技能的说明
            skill_id: 显式指定要加载说明的技能ID (优先于 auto_load_instruction)

        Returns:
            dict — {prompt, matches, instruction?, estimated_tokens, layers_used}
        """
        with traced_action("svc_build_context", intent=intent[:80],
                           max_tokens=max_tokens, layer="1+2") as ctx:
            result = self.injector.build_context(
                intent, max_tokens=max_tokens, top_k=top_k,
                auto_load_instruction=auto_load_instruction,
                skill_id=skill_id,
            )
            # [变易] 可观测性扩展：透传 retrieved_chunks 到 traced_action
            ctx["retrieved_chunks"] = result.get("retrieved_chunks", [])
            return result

    def validate_llm_output(self, llm_output: str, *,
                            loaded_skills: List[str],
                            intent: str) -> Dict[str, Any]:
        """校验 LLM 输出 (供 orchestrator 在收到 LLM 回复后调用)

        [变易] 不阻塞主流程: 返回的 GuardResult.passed 始终 True,
        severity=critical 时由调用方决策重试或降级。

        检测项:
            - 幻觉检测: LLM 是否声称调用了未加载的技能
            - 格式校验: 期望 JSON 时是否合法
            - 合规校验: PII / 密钥 / Prompt Injection
            - 越界检测: 危险动作关键词

        Args:
            llm_output: LLM 回复文本
            loaded_skills: 已加载的技能 ID 列表 (来自 build_skill_context)
            intent: 用户原始意图

        Returns:
            dict — {passed, severity, findings, sanitized_output}
            findings: [{category, severity, message, location}, ...]
        """
        with traced_action("svc_validate_llm_output",
                           intent=intent[:80]) as ctx:
            guard = SkillOutputGuard()
            result = guard.validate_llm_output(
                llm_output, loaded_skills, intent,
            )
            ctx["severity"] = result.severity
            ctx["findings_count"] = len(result.findings)
            ctx["passed"] = result.passed
            logger.info(
                "[Service] validate_llm_output severity=%s findings=%d",
                result.severity, len(result.findings),
            )
            emit_metric("yunshu_skill_llm_guard_total",
                        value=1, kind="counter",
                        labels={"severity": result.severity})
            return result.to_dict()

    def get_layer_summary(self) -> Dict[str, Any]:
        """三层架构统计摘要 — 供前端可视化与 /health 使用"""
        return self.loader.get_layer_summary()

    def list_skill_scripts(self, skill_id: str) -> List[Dict[str, Any]]:
        """列出技能的脚本文件 (Layer 3 元信息，不加载代码)"""
        return self.loader.list_scripts(skill_id)

    def list_skill_temp_files(self, skill_id: str) -> List[Dict[str, Any]]:
        """列出技能的 temp/ 文件"""
        return self.loader.list_temp_files(skill_id)

    # ─── 健康检查 ───

    def health(self) -> Dict[str, Any]:
        """健康检查 (供 /api/skills-mgmt/health 调用)"""
        store_health = self.store.health()
        all_skills = self.store.list_all()
        # 三层架构健康状态
        try:
            file_store_health = self.file_store.health()
        except Exception as e:  # noqa: BLE001
            file_store_health = {"ok": False, "error": str(e)}
        try:
            executor_health = self.executor.health()
        except Exception as e:  # noqa: BLE001
            executor_health = {"ok": False, "error": str(e)}
        try:
            layer_summary = self.loader.get_layer_summary()
        except Exception as e:  # noqa: BLE001
            layer_summary = {"error": str(e)}
        return {
            "ok": store_health.get("ok", False) and file_store_health.get("ok", False),
            "module": "skills_mgmt",
            "version": "1.1.0",  # 三层架构版本
            "store": store_health,
            "three_layer": {
                "file_store": file_store_health,
                "executor": executor_health,
                "layer_summary": layer_summary,
            },
            "stats": {
                "total": len(all_skills),
                "enabled": sum(1 for s in all_skills if s.enabled),
                "approved": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.APPROVED.value),
                "pending_review": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.PENDING_REVIEW.value),
                "rejected": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.REJECTED.value),
                # [变易] 全链路可观测性字段统计：声明已沉淀的可观测性字段
                # 实际指标值通过 metrics 系统（Prometheus/BusinessMetricsCollector）
                # 与结构化日志聚合（Loki/ELK）查询，此处仅暴露字段元信息
                "observability": {
                    "fields": [
                        "retrieved_chunks",
                        "retrieval_precision_at_k",
                        "eval_score",
                        "user_feedback",
                    ],
                    "metrics": [
                        "yunshu_skill_retrieval_precision_at_k",
                        "yunshu_skill_eval_score",
                        "yunshu_skill_hallucination_total",
                    ],
                    "retrieved_chunks_max": 50,
                    "truncation_enabled": True,
                    "span_persistence": "structured_log",
                },
            },
            # 规模监控：当技能数增长到阈值时建议升级检索方式
            # 当前仅 TF-IDF，未来扩展点已在 match() 接口预留（use_vector/use_bm25/use_reranker）
            "scale_monitoring": {
                "total_skills": len(all_skills),
                "upgrade_threshold": 30,
                "upgrade_recommended": len(all_skills) >= 30,
                "current_method": "tfidf",
                "available_methods": ["tfidf"],  # 未来扩展: ["tfidf", "vector", "bm25", "reranker"]
            },
        }

    # ─── 内部 ───

    def _collect_few_shot_example(self, skill_id: str, *,
                                  input_text: str, output_text: str,
                                  trace_id: str = "") -> bool:
        """将一次成功执行采集为 Few-shot 示例（带去重）

        仅由 record_execution 在 feedback_rating=5 且 success=True 时调用；
        示例库写入失败由调用方兜底，不影响主流程。
        """
        import uuid
        from datetime import datetime
        from .few_shot_injector import FewShotInjector, FewShotExample

        injector = FewShotInjector()
        example = FewShotExample(
            example_id=f"ex_{uuid.uuid4().hex[:8]}",
            intent=input_text,
            input=input_text,
            output=output_text,
            rating=5,
            tags=[skill_id],
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        added = injector.add_example(skill_id, example)
        logger.info(
            "[Service] few-shot 示例%s采集 skill_id=%s trace_id=%s",
            "" if added else "（重复，跳过）",
            skill_id, trace_id or "(none)",
        )
        return added

    def _require(self, skill_id: str) -> Skill:
        skill = self.store.get(skill_id)
        if not skill:
            raise SkillNotFoundError(skill_id)
        return skill
