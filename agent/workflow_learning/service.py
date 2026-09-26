"""工作流学习总服务 — 组合 learner/generator/repository/matcher/executor"""

from __future__ import annotations
from pathlib import Path
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
from .observability import logger, traced_action, track_event
from .repository import WorkflowRepository, SOURCE_SESSIONS_MAX
from .matcher import WorkflowMatcher
from .learner import (
    WorkflowLearner,
    SIGNATURE_FALLBACK,
    canonical_signature,
)
from .generator import WorkflowGenerator
from .executor import WorkflowExecutor, ToolExecutor
from . import admission
from . import retirement


# ═══════════════════════════════════════════════════════════════
#  【F11-C-1】config.yaml 接线（消灭「死键」治理陷阱）
# ───────────────────────────────────────────────────────────────
#  改前实测（docs/audit_skill_governance/F11C1.md §1）：`config.yaml` 的
#  `workflow_learning.matcher|executor|learner` 三块**全仓无任何读取点**；
#  以 instrumentation 拦截 `open` 实测 `WorkflowLearningService()`
#  （state_manager.py:748 无参构造 = 生产路径）构造期间**从未打开 config.yaml**，
#  阈值只来自 `WorkflowMatcher.__init__` / `WorkflowExecutor.__init__` 的形参默认值。
#
#  本卡接线范围（其余按键逐个裁定，见审计文档 §2）：
#      matcher.min_similarity / matcher.min_confidence / matcher.top_k
#  【不易】config.yaml 缺失 / 非法 YAML / 单键类型非法 / 越界 → 逐键回落
#  `_CONFIG_DEFAULTS`（= 改前的真实生效值），**绝不抛异常中断构造**。
#  【变易】显式传参 > config.yaml > `_CONFIG_DEFAULTS`（优先级与 orchestrator
#  各层配置一致；既有调用方显式传的值不受影响）。
#  【简易】不读 `workflow_learning.executor.min_score`：该键全仓无读取点且与代码
#  构造默认（0.3）漂移，已按本卡裁定删除；真实生效的执行门槛是
#  `orchestrator.workflow_learning_layer.min_score`（orchestrator.py:2030 每次
#  显式传给 `svc.try_execute`，env `ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE`
#  可覆盖）。服务级构造默认 `min_score` **保持 0.3 不变** —— 若改读配置的 0.25，
#  服务级执行门槛会 0.3→0.25（更多工作流被自动执行），属行为变更，本卡不改。
# ═══════════════════════════════════════════════════════════════

#: config.yaml 的**绝对**路径（L8：不得相对 CWD —— 见
#: tests/unit/test_config_yaml_anchor.py 的结构守卫）
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

#: 接线键的兜底默认值 —— **必须**等于改前的真实生效值：
#:   min_similarity/min_confidence = `WorkflowMatcher.__init__` 形参默认
#:   top_k = `WorkflowMatcher.match` 的默认（executor 侧等价口径见下）
_CONFIG_DEFAULTS: Dict[str, Any] = {
    "min_similarity": 0.3,
    "min_confidence": 0.4,
    "top_k": 5,
}

#: key -> (config.yaml 子节, 类型转换, 合法区间)。越界即回落默认并告警。
_CONFIG_SPEC: Dict[str, Any] = {
    "min_similarity": ("matcher", float, (0.0, 1.0)),
    "min_confidence": ("matcher", float, (0.0, 1.0)),
    "top_k": ("matcher", int, (1, 50)),
}


def _load_workflow_learning_config() -> Dict[str, Any]:
    """读取 config.yaml `workflow_learning.matcher`（F11-C-1 接线）

    Returns:
        {"min_similarity": float, "min_confidence": float, "top_k": int}
        任何异常 / 键缺失 → 该键回落 `_CONFIG_DEFAULTS`（= 改前真实生效值）。

    【不易】本函数**不抛异常**：配置文件损坏不得让工作流服务初始化失败
    （它挂在主链路的拦截层上）。
    """
    cfg = dict(_CONFIG_DEFAULTS)
    try:
        if not _CONFIG_PATH.exists():
            return cfg
        import yaml as _yaml
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        section = data.get("workflow_learning") or {}
        for key, (sub, cast, (lo, hi)) in _CONFIG_SPEC.items():
            raw = (section.get(sub) or {}).get(key)
            if raw is None:
                continue
            try:
                value = cast(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "[Service] workflow_learning.%s.%s 非法值已忽略"
                    "（回落默认 %r）: %r", sub, key, _CONFIG_DEFAULTS[key], raw)
                continue
            if not (lo <= value <= hi):
                logger.warning(
                    "[Service] workflow_learning.%s.%s 越界 [%s, %s] 已忽略"
                    "（回落默认 %r）: %r", sub, key, lo, hi,
                    _CONFIG_DEFAULTS[key], raw)
                continue
            cfg[key] = value
    except Exception as e:  # noqa: BLE001 — 配置读取失败只降级，不影响主链路
        logger.warning("[Service] workflow_learning 配置读取失败，"
                       "回落构造默认值: %s", e)
    return cfg


class WorkflowLearningService:
    """工作流学习总服务"""

    def __init__(self, *, repo_path: Optional[str] = None,
                 min_similarity: Optional[float] = None,
                 min_confidence: Optional[float] = None,
                 min_score: Optional[float] = None,
                 top_k: Optional[int] = None,
                 tool_validator: Optional[Callable[[str], bool]] = None,
                 tool_executor: Optional[ToolExecutor] = None,
                 llm_step_runner=None,
                 agent_executor=None):
        # 【F11-C-1】阈值优先级: 显式传参 > config.yaml > 兜底默认（改前生效值）
        # 传 None = "未指定" ⇒ 读配置。既有调用方（显式传值的脚本/测试/路由）
        # 行为不变；无参构造的生产单例从"硬编码默认"变为"真读 config.yaml"。
        _cfg = _load_workflow_learning_config()
        if min_similarity is None:
            min_similarity = _cfg["min_similarity"]
        if min_confidence is None:
            min_confidence = _cfg["min_confidence"]
        if top_k is None:
            top_k = _cfg["top_k"]
        # 【F11-C-1】服务级执行门槛**不读配置**（见模块顶部【简易】）：
        # 构造默认 0.3 维持不变；真实生效的执行门槛在 orchestrator 拦截层
        # （workflow_learning_layer.min_score，env 可覆盖）。
        if min_score is None:
            min_score = 0.3
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
            min_score=min_score, top_k=top_k, tool_executor=tool_executor,
            agent_executor=agent_executor,
            llm_step_runner=llm_step_runner,
        )
        # 启动时从仓库重建索引（准入否决的条目不会进索引）
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """从仓库重建匹配器索引"""
        workflows = self.repo.list_all()
        self.matcher.rebuild(workflows)
        # 读数据：索引里的是**通过准入**的条数（≠ 仓库条数，含草稿/归档）
        logger.info("[Service] 仓库 %d 个本地工作流，准入入索引 %d 个",
                    len(workflows), len(self.matcher._workflows))

    # ─── 学习入口 ───

    def learn_from_interaction(self, record: LearningRecord) -> LearnedWorkflow:
        """从一次成功的 LLM 交互中学习方法并保存

        【F11】同任务**去重**：规范签名（`learner.canonical_signature`）相同的
        重复观察**不再新建条目**，而是并入既有条目并累加 `observed_count`。
        去重键是**从 `entry.source_user_input` 重算**的规范签名，而不是库里
        存的字符串 —— 存量条目的 `task_signature` 是旧"字符清单"口径，
        直接比字符串永远不相等。

        Returns: 本次学习对应的条目（新建的，或并入后的既有条目）。
        Raises: WorkflowLearningError（仅成功交互可学 / 无工具步骤 / 无实词签名）
        """
        with traced_action("svc_learn", session_id=record.session_id):
            wf = self.learner.learn(record)
            existing = self._find_same_task(wf.task_signature)
            if existing is not None:
                return self._merge_observation(existing, record, wf)
            return self.generator.generate_and_store(wf)

    # ─── 同任务去重（F11）───

    @staticmethod
    def _dedup_rank(wf: LearnedWorkflow):
        """同签名多条目时的"取哪一条"(确定性)：active > draft > archived，
        其次执行次数多者优先，再按 created_at / id 升序。"""
        status = str(getattr(wf.status, "value", wf.status))
        status_rank = {"active": 0, "draft": 1}.get(status, 2)
        return (status_rank, -int(wf.success_count or 0),
                str(wf.created_at or ""), str(wf.id))

    def _find_same_task(self, signature: str) -> Optional[LearnedWorkflow]:
        """按**规范签名**查找既有同任务条目（无则 None）

        - 跳过 `source_user_input` 为空的条目（无来源输入 ⇒ 无法重算签名，
          不参与去重，避免把不相关条目误并到一起）；
        - 跳过退化签名（`SIGNATURE_FALLBACK`）：它是"无实词"的占位值，
          不代表任何任务，不该成为公共去重桶。
        """
        if not signature or signature == SIGNATURE_FALLBACK:
            return None
        same = [
            w for w in self.repo.list_all()
            if str(w.source_user_input or "")
            and canonical_signature(w.source_user_input) == signature
        ]
        if not same:
            return None
        return min(same, key=self._dedup_rank)

    def _merge_observation(self, existing: LearnedWorkflow,
                           record: LearningRecord,
                           fresh: LearnedWorkflow) -> LearnedWorkflow:
        """把一次重复观察并入既有条目（**不新建**、不覆盖既有 steps）

        - `observed_count` 累加（这就是"同任务重复出现累加计数"的载体）；
        - 来源会话并入 `source_sessions`（去重、保留最近
          `repository.SOURCE_SESSIONS_MAX` 个），使跨会话样本数仍可统计；
        - `task_signature` 对齐到规范签名（去重键就是它）：存量"字符清单"
          签名在被重新观察到之后自然收敛，否则 `count_distinct_sessions`
          永远看不到这条；
        - 既有 steps / name / status / 统计字段一律不动（新观察不覆盖旧资产，
          状态由治理侧决定，合并不得把 archived 复活为 active）。
        """
        existing.observed_count = int(existing.observed_count or 0) + 1
        sessions = list(existing.source_sessions or [])
        for sid in (existing.source_session_id, record.session_id):
            sid = str(sid or "")
            if sid and sid not in sessions:
                sessions.append(sid)
        existing.source_sessions = sessions[-SOURCE_SESSIONS_MAX:]
        existing.task_signature = fresh.task_signature
        existing.touch()
        self.repo.upsert(existing)
        # 只有结构+状态达标者才在索引里。不达标者（草稿/归档）从未进索引，
        # 调 register 只会白增一次"准入否决"计数（matcher.admission_rejected_counts）。
        if admission.check_match_eligibility(existing).admitted:
            self.matcher.register(existing)
        track_event("wf_learn_dedup", {
            "workflow_id": existing.id,
            "signature": existing.task_signature,
            "observed_count": existing.observed_count,
            "support_sessions": len(existing.source_sessions),
        })
        logger.info(
            "[Service] 同任务重复观察并入 %s（observed_count=%d, 会话 %d 个）",
            existing.id, existing.observed_count, len(existing.source_sessions))
        return existing

    # ─── 匹配执行入口 (主接口) ───

    def try_execute(self, task_text: str, *,
                    params: Optional[Dict[str, Any]] = None,
                    min_score: Optional[float] = None) -> WorkflowExecutionResult:
        """新任务到达时先尝试本地工作流

        min_score: 覆盖本次执行的匹配阈值（如 orchestrator 拦截层按层配置），
                   None 时使用构造时默认值

        【F11-C-2】本方法只透传给 `WorkflowExecutor.try_execute`：门槛默认比较
        **证据分** `evidence = sim × confidence`（priority 只用于排序）；语义上
        自动执行只在「**高相似 + 有一定信心的重复场景**」成立，**改写场景默认
        交给 LLM**（返回 matched=False 即降级信号）。逃生开关
        `WORKFLOW_LEARNING_GATE_ON_EVIDENCE`（置 0 ⇒ 回到改前的乘性门槛）。
        """
        return self.executor.try_execute(task_text, params=params, min_score=min_score)

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
        """模拟匹配，返回候选列表 (不执行)

        【F11-C-2】这里返回的 `score` 是**排序分** combined（sim × confidence ×
        priority_factor），不是门槛用的证据分 —— 门槛口径见
        `WorkflowExecutor.try_execute`；需要两个分数请用
        `matcher.match(with_evidence=True)` / `matcher.match_scored()`。
        """
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
                "admission_rejected": self.matcher.admission_rejected_counts(),
            },
            "admission": {
                "MIN_STEPS": admission.MIN_STEPS,
                "MIN_TRIGGER_CHARS": admission.MIN_TRIGGER_CHARS,
                "MIN_CROSS_SESSION_SUPPORT":
                    admission.MIN_CROSS_SESSION_SUPPORT,
                "draft": sum(1 for w in all_wf
                             if w.status == WorkflowStatus.DRAFT.value),
                "archived": sum(1 for w in all_wf
                                if w.status == WorkflowStatus.ARCHIVED.value),
            },
            "executor": {
                "min_score": self.executor.min_score,
                # 【F11-C-1】候选池深度（config.yaml workflow_learning.matcher.top_k
                # 接线后可见；改前为硬编码 3，配置里的 5 从未生效）
                "top_k": self.executor.top_k,
                "tool_executor_set": self.executor._tool_executor is not None,
            },
        }

    # ─── 工具执行器注入 ───

    def set_tool_executor(self, executor: ToolExecutor) -> None:
        self.executor.set_tool_executor(executor)

    def set_llm_step_runner(self, runner) -> None:
        """注入步骤级 LLM runner：(prompt_text, ctx) → str

        供 workflow_type='hybrid' 的 need_llm 步骤使用。
        """
        self.executor.set_llm_step_runner(runner)

    def set_agent_executor(self, executor) -> None:
        """注入整条 Agent 模式执行器（>10步/复杂分支工作流用）"""
        self.executor.set_agent_executor(executor)

    # ─── 工作流 → 技能 转换 ───

    def convert_to_skill(self, wf_id: str, *,
                         skills_service=None,
                         force: bool = False,
                         auto_review: Optional[bool] = None) -> Dict[str, Any]:
        """把指定工作流抽象为 Skill 并注册到 skills_mgmt

        Args:
            wf_id: 工作流ID
            skills_service: SkillsMgmtService 实例（None 时延迟导入全局单例）
            force: 是否跳过质量门控
            auto_review: 转换成功后是否执行权威「评审」
                （None 读取 SKILLS_ASSESS_AUTO_REVIEW_AFTER_WORKFLOW_CONVERT /
                 config.yaml skills_mgmt.assess.auto_review_after_workflow_convert，
                 默认 False——转换本身已通过 create_manual 自动携带咨询性评估；
                 兼容旧参数名 auto_digest 与旧环境变量 SKILLS_DIGEST_*
                 读取见 assessor.py 兼容层）

        Returns:
            {workflow_id, skill_id, skill_name, version, action, review?}
            review: {verdict, status} 或 {error}
            （≤1 minor 兼容键 digest 亦随附，语义同 review——评审语义，与
             v7.2 内化语义无关）

        Raises:
            WorkflowNotFoundError: 工作流不存在
            WorkflowConvertError: 未通过质量门控
        """
        svc = skills_service or self._resolve_skills_service()
        converter = self._build_converter(svc)
        result = converter.convert_workflow_to_skill(wf_id, force=force)
        if result.get("action") == "created":
            if auto_review is None:
                try:
                    from agent.skills_mgmt.assessor import assess_flag
                    auto_review = assess_flag("auto_review_after_workflow_convert", False)
                except Exception:
                    auto_review = False
            if auto_review:
                try:
                    rv = svc.review_skill(result["skill_id"])
                    review_view = {
                        "verdict": getattr(rv.review_verdict, "value",
                                           rv.review_verdict),
                        "status": getattr(rv.status, "value", rv.status),
                    }
                    result["review"] = review_view
                    result["digest"] = review_view  # 兼容旧键（≤1 minor）
                except Exception as e:  # noqa: BLE001
                    logger.warning("工作流转换后评审失败 wf=%s: %s", wf_id, e)
                    err = {"error": str(e)}
                    result["review"] = err
                    result["digest"] = err  # 兼容旧键（≤1 minor）
        return result

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
        """列出当前可转换为 Skill 的工作流（满足质量门控且未转换过）

        【TASK-S10-01】**自动升格**路径的门槛 = 结构性准入（步骤数下限 +
        触发词区分度，`force` 也不可越过）+ 统计性门控 + 跨会话样本数
        ≥ `admission.MIN_CROSS_SESSION_SUPPORT`（"最少样本数"：单会话/单轮
        来源无从判断是否会复现，不得自动沉淀为资产）。
        逐条未通过原因见 `admission_report()`。
        """
        from .skill_converter import quality_gate_reasons
        candidates = []
        for wf in self.repo.list_all(enabled_only=True):
            if wf.converted_to_skill_id:
                continue
            if self._promotion_blockers(wf) or quality_gate_reasons(wf):
                continue
            candidates.append({
                "workflow_id": wf.id,
                "name": wf.name,
                "success_count": wf.success_count,
                "failure_count": wf.failure_count,
                "confidence": wf.confidence,
                "priority": wf.priority,
                "support_sessions": self.repo.count_distinct_sessions(
                    wf.task_signature),
                "last_used_at": wf.last_used_at,
            })
        return candidates

    def _promotion_blockers(self, wf: LearnedWorkflow) -> Dict[str, Any]:
        """自动升格的结构性/样本数阻断项（空 dict = 无阻断）"""
        decision = admission.check_structure(
            steps=wf.steps, trigger_patterns=wf.trigger_patterns)
        blockers: Dict[str, Any] = {}
        if not decision.admitted:
            blockers["structure"] = decision.to_dict()
        support = self.repo.count_distinct_sessions(wf.task_signature)
        sample = admission.check_cross_session_support(
            support_sessions=support)
        if not sample.admitted:
            blockers["support"] = sample.to_dict()
            blockers["support_sessions"] = support
        return blockers

    def admission_report(self) -> Dict[str, Any]:
        """准入读数（可复算）——存量条数 / 命中条件 / 拒绝码分布

        Returns:
            {thresholds, total, match_candidates, draft_or_blocked: [...],
             rejected_by_code: {...}, with_dirty_triggers: [...],
             single_step: [...], single_session: [...]}
        """
        from .skill_converter import quality_gate_reasons
        all_wf = self.repo.list_all()
        report: Dict[str, Any] = {
            "thresholds": {
                "MIN_STEPS": admission.MIN_STEPS,
                "MIN_TRIGGER_CHARS": admission.MIN_TRIGGER_CHARS,
                "MIN_CROSS_SESSION_SUPPORT":
                    admission.MIN_CROSS_SESSION_SUPPORT,
            },
            "total": len(all_wf),
            "match_candidates": 0,
            "disabled_or_blocked": [],
            "rejected_by_code": {},
            "single_char_trigger_workflows": [],
            "single_step_workflows": [],
            "single_session_workflows": [],
            "convertible": [],
        }
        for wf in sorted(all_wf, key=lambda w: w.id):
            decision = admission.check_match_eligibility(wf)
            if decision.admitted:
                report["match_candidates"] += 1
            else:
                report["disabled_or_blocked"].append({
                    "workflow_id": wf.id,
                    "status": str(getattr(wf.status, "value", wf.status)),
                    **decision.to_dict(),
                })
            for code in decision.codes:
                report["rejected_by_code"][code] = (
                    report["rejected_by_code"].get(code, 0) + 1)
            bad = admission.single_char_triggers(wf.trigger_patterns)
            if bad:
                report["single_char_trigger_workflows"].append({
                    "workflow_id": wf.id, "single_char_triggers": bad,
                    "trigger_patterns": list(wf.trigger_patterns),
                })
            if len(wf.steps or []) < admission.MIN_STEPS:
                report["single_step_workflows"].append(wf.id)
            support = self.repo.count_distinct_sessions(wf.task_signature)
            if support < admission.MIN_CROSS_SESSION_SUPPORT:
                report["single_session_workflows"].append({
                    "workflow_id": wf.id,
                    "task_signature": wf.task_signature,
                    "support_sessions": support,
                })
            if (not wf.converted_to_skill_id
                    and not self._promotion_blockers(wf)
                    and not quality_gate_reasons(wf)):
                report["convertible"].append(wf.id)
        return report

    def retire_dirty_workflows(self, *, apply: bool = False) -> Dict[str, Any]:
        """存量脏工作流退役（默认 dry-run；只标记不删除 + 追加台账）

        【不易】**不在服务构造时自动写盘**：仓库里若混入脏条目，构造服务会
        改写 `data/learned_workflows.json` 并追加台账——那正是"测试/构造覆盖
        真实运行期数据"的复发面（参见 P0「测试不再覆盖真实 .env」）。故退役
        只走**显式**入口：本方法（人工/运维调用）或
        `scripts/retire_dirty_workflows.py --apply`。
        运行时对脏条目的隔离**不依赖**写盘：`matcher` 的结构性准入已在
        每次 register/match 时生效（与 status 无关）。
        """
        return retirement.retire_dirty_workflows(self.repo, apply=apply)

    # ─── 批量 LLM 转换外部 agent 技能 ───

    @staticmethod
    def _created_review_view(svc, skill_id: str) -> Dict[str, Any]:
        """新建技能创建后的自动评审结论（供批量结果展示；失败返回空）。

        术语纪律（TASK-S0-01）：键 review_verdict 为主，旧兼容键 digest_verdict
        随附（≤1 minor；评审语义，与 v7.2 内化语义无关）。
        """
        try:
            skill = svc.get(skill_id)
            review = getattr(skill, "review", None)
            if review is None:
                return {}
            return {
                "review_verdict": getattr(review.review_verdict, "value",
                                           review.review_verdict)
                or "",
                "auto_assessed": bool(getattr(review, "auto_assessed", False)),
                # 兼容旧键（≤1 minor）：评审语义旧名 digest_verdict
                "digest_verdict": getattr(review.digest_verdict, "value",
                                          review.digest_verdict)
                or "",
            }
        except Exception:  # noqa: BLE001 结果视图尽力而为
            return {}

    def batch_convert_external_skills(self, external_skills: List[Dict[str, Any]],
                                       *, llm_client=None,
                                       skills_service=None,
                                       merge_threshold: float = 0.85,
                                       strengthen_threshold: float = 0.7,
                                       queue_mode: bool = False) -> Dict[str, Any]:
        """批量把外部 agent 的技能转换为本地技能并自动合并/加强/新建

        对每个外部技能执行:
            1. 调用 convert_external_skill 翻译 + 注册（LLM 或规则）
            2. 用 find_duplicates_for 检测与现有技能的 Jaccard 相似度
            3. 根据相似度选择动作:
                - Jaccard ≥ merge_threshold → 合并到现有技能（新建的作为 src 被删）
                - strengthen_threshold ≤ Jaccard < merge_threshold → 加强现有技能
                  （合并 tags/dependencies 到现有技能，删除新建的临时技能）
                - Jaccard < strengthen_threshold → 保留新建技能

        queue_mode=True（「先存草稿再人工逐个放行」）时跳过第 2/3 步的自动
        合并/加强：每个外部技能一律转为独立草稿（含自动评审-评估结论）进入
        待放行队列，由人工逐个放行/驳回。

        Args:
            external_skills: 外部技能列表（每个元素是 dict）
            llm_client: LLM 客户端（None 时走规则转换）
            skills_service: SkillsMgmtService 实例
            merge_threshold: 触发合并的 Jaccard 阈值（默认 0.85）
            strengthen_threshold: 触发加强的 Jaccard 阈值（默认 0.7）
            queue_mode: True=仅入草稿队列（不自动合并/加强）

        Returns:
            {total_input, converted, merged: [...], strengthened: [...],
             created: [...], failed: [...]}
        """
        with traced_action("svc_batch_convert_external",
                           total=len(external_skills),
                           merge_threshold=merge_threshold,
                           strengthen_threshold=strengthen_threshold,
                           queue_mode=queue_mode):
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

                    if queue_mode:
                        # 草稿队列模式：一律保留为草稿，不做自动合并/加强
                        summary["created"].append({
                            "skill_id": new_skill_id,
                            "skill_name": conv["skill_name"],
                            "source_format": conv.get("source_format", "unknown"),
                            "queued": True,
                            **self._created_review_view(svc, new_skill_id),
                        })
                        continue

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
                            **self._created_review_view(svc, new_skill_id),
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
