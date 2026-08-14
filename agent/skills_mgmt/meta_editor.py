"""元智能体受控编辑器（MetaEditor）— 任务 EVO-T5 工具进化升级

【任务定位】
    将进化对象从"技能参数"升级为**工具代码与工具文档**：元智能体产出
    diff/patch 提案 → Reviewer 三重审核（critical 直接拒绝，不进入评估）→
    进程级沙盒真实评估 → 谱系 pending_review（**绝不自动合并**）→
    人工审批 → git 合并 → 可从 git 回滚。

【不易边界（来自任务 05_工具进化升级_元智能体受控编辑.md）】
    1. 只允许编辑白名单文件（data/skills_repo/ 下技能内容与文档），
       禁止触碰系统代码（agent/ 核心模块）—— 由 edit_policy.EditPolicy 双层防线保证；
    2. 所有编辑产物必经 Reviewer 三重审核 + 人工审批，评估达标才合并；
    3. 不开启"修改选择策略本身"的递归自修改（edit_policy 白名单不含进化策略文件）；
    4. 提案默认不合并：submit() 只落 pending_review，唯一合并入口 = 显式人工审批。

【执行管线（与任务 6 approval.py 状态机对齐）】
    propose → review_proposal → evaluate_proposal → submit_proposal
    → (人工) approve_proposal → merge_proposal → (可选) rollback
    任一环节拒绝：状态机 draft → rejected，并写入谱系 decision="rejected"。

【谱系记录（decision 与 lineage.DECISIONS 对齐）】
    pending_review   提案提交待审批（含真实评估快照，parent=上一有效代）
    rejected         审核/评估/人工任一环节拒绝（评估失败带 eval_result，供暂停判定）
    committed        审批通过后 git 合并（parent=pending_review）
    rolled_back      git 回滚（parent=committed）

【护栏（.env 可配置，全部带默认值）】
    META_EDIT_MAX_SKILLS_PER_ROUND   单轮可编辑技能数 N，默认 1
    META_EDIT_MAX_TOKENS_PER_ROUND   单轮 token 预算，默认 50000（0=不熔断）
    META_EDIT_STALL_ROUNDS           连续 K 轮无提升暂停该技能进化，默认 3
    META_EDIT_EVAL_MIN_SCORE         评估通过阈值，默认 0.3
"""

from __future__ import annotations

import json
import os
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .edit_policy import (
    EditPolicy,
    EditProposal,
    EditFile,
    EditStatus,
    EditType,
    EditPolicyError,
    EditStatusTransitionError,
)
from .observability import logger, emit_metric, traced_action

__all__ = ["MetaEditor", "MetaEditGenerator", "MetaEditError"]


class MetaEditError(Exception):
    """元智能体编辑流程异常（合并/回滚等）"""


# ════════════════════════════════════════════════════════════
#  .env 配置（与 lineage/evaluator 同模式：env 带默认值，非法值回退默认）
# ════════════════════════════════════════════════════════════

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_max_skills_per_round() -> int:
    # 每轮仅允许编辑 N 个技能（任务要求默认 1）
    return max(1, _env_int("META_EDIT_MAX_SKILLS_PER_ROUND", 1))


def _env_max_tokens_per_round() -> int:
    # 单轮 token 预算（0=不熔断）；复用任务 3 成本控制口径（EVOLUTION_*）
    return max(0, _env_int("META_EDIT_MAX_TOKENS_PER_ROUND", 50000))


def _env_stall_rounds() -> int:
    # 连续 K 轮无提升暂停该技能进化（防无效迭代烧钱）
    return max(1, _env_int("META_EDIT_STALL_ROUNDS", 3))


def _env_eval_min_score() -> float:
    return max(0.0, min(1.0, _env_float("META_EDIT_EVAL_MIN_SCORE", 0.3)))


# ════════════════════════════════════════════════════════════
#  提案生成器（AI 辅助，无 LLM 诚实降级）
# ════════════════════════════════════════════════════════════

_META_EDIT_PROMPT = """你是元智能体编辑器。基于当前技能内容、进化谱系评分与评估样本，
提出**一个**受控编辑提案（技能正文/参数默认值/工具文档）。

约束（守不易）:
- 只允许改动 {allowed_types}，禁止新增 import/依赖/执行逻辑核心；
- 输出必须是纯 JSON，格式:
{{
  "edit_type": "content|params|documentation",
  "files": [{{"file_path": "<相对项目根的技能仓库路径>", "new_content": "<编辑后完整内容>"}}],
  "change_summary": "一句话变更说明",
  "expected_gain": "预期收益（一句）"
}}

【技能 ID】{skill_id}
【当前内容】
{current_content}
【谱系历史评分（近 {lineage_n} 代）】
{lineage_summary}
【评估样本任务】
{sample_tasks}
"""


class MetaEditGenerator:
    """元智能体编辑提案生成器

    输入: 技能当前内容 + 谱系历史评分（任务 1）+ 评估器样本集（任务 2）
    输出: 生成 dict（files / edit_type / change_summary / expected_gain）；
         无 LLM 客户端时返回 None（诚实降级，不伪造提案）。
    LLM 输出先经 output_guard 检查（任务要求：生成内容不得含注入指令）。
    """

    def __init__(self, llm_client: Optional[Any] = None,
                 output_guard: Optional[Any] = None,
                 prompt_template: Optional[str] = None):
        self._llm = llm_client
        self._guard = output_guard
        self._template = prompt_template or _META_EDIT_PROMPT

    def generate(self, *, skill_id: str, current_content: str,
                 lineage_summary: str = "", sample_tasks: str = "",
                 allowed_types: str = "content, params, documentation",
                 current_meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """生成一个编辑提案；无 LLM → None（不伪造）"""
        if self._llm is None:
            logger.info("[MetaEditor] 无 LLM 客户端，跳过提案生成（诚实降级） skill=%s",
                        skill_id)
            return None
        prompt = self._template.format(
            skill_id=skill_id,
            current_content=(current_content or "")[:6000],
            lineage_summary=lineage_summary or "(无)",
            sample_tasks=sample_tasks or "(无样本)",
            lineage_n=5,
            allowed_types=allowed_types,
        )
        try:
            text = self._llm.chat(prompt)
        except Exception as e:  # noqa: BLE001 LLM 不可用 → 不产提案
            logger.warning("[MetaEditor] LLM 调用失败，跳过提案生成 skill=%s: %s",
                           skill_id, e)
            return None
        text = (text or "").strip()
        if not text:
            return None
        # 输出护栏：注入指令检查（提示词与技能代码隔离，守不易）
        if self._guard is not None:
            guard = self._guard.validate_llm_output(
                text, loaded_skills=[], intent="meta_edit")
            if guard.severity == "critical":
                logger.warning(
                    "[MetaEditor] 生成内容命中输出护栏 critical，丢弃提案 skill=%s: %s",
                    skill_id, [f.message for f in guard.findings])
                return None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[MetaEditor] 生成内容非合法 JSON，丢弃提案 skill=%s: %s",
                           skill_id, e)
            return None
        if not isinstance(data, dict) or not data.get("files"):
            logger.warning("[MetaEditor] 生成内容缺少 files，丢弃提案 skill=%s", skill_id)
            return None
        return {
            "edit_type": str(data.get("edit_type", EditType.CONTENT.value)),
            "files": data.get("files"),
            "change_summary": str(data.get("change_summary", "")),
            "expected_gain": str(data.get("expected_gain", "")),
        }


# ════════════════════════════════════════════════════════════
#  Reviewer 适配对象（不引入 pydantic 重依赖）
# ════════════════════════════════════════════════════════════


class _ReviewSubject:
    """把编辑后的文件内容适配为 SkillReviewer 可消费的对象

    Why 不用 pydantic Skill: Reviewer 只需 id/name/description/content/tags/
    dependencies/config_schema/content_type/version/author + 可写 review/status，
    用轻量对象避免强依赖与字段校验负担（简易）。
    """

    def __init__(self, *, skill_id: str, meta: Dict[str, Any],
                 content: str):
        self.id = skill_id
        self.name = str(meta.get("name") or skill_id)
        self.description = str(meta.get("description") or "")
        self.content = content
        self.tags = list(meta.get("tags") or [])
        self.dependencies = list(meta.get("dependencies") or [])
        self.config_schema = meta.get("config_schema") or {}
        self.content_type = "markdown"
        self.version = str(meta.get("version") or "0.1.0")
        self.author = str(meta.get("author") or "unknown")
        self.review = None
        self.status = None


# ════════════════════════════════════════════════════════════
#  元智能体受控编辑器
# ════════════════════════════════════════════════════════════


class MetaEditor:
    """元智能体受控编辑完整链路

    用法:
        editor = MetaEditor()
        editor.start_round()
        proposal = editor.propose("my_skill")       # 生成草案（护栏内）
        proposal = editor.review_proposal(proposal)  # 三重审核（critical 拒绝）
        proposal = editor.evaluate_proposal(proposal)  # 沙盒真实评估
        proposal = editor.submit_proposal(proposal)  # pending_review + 谱系
        proposal = editor.approve_proposal(proposal)  # 人工审批
        proposal = editor.merge_proposal(proposal)    # git 合并（唯一合并入口）
        editor.rollback(proposal)                     # git 回滚（可选）
    """

    def __init__(self, policy: Optional[EditPolicy] = None, *,
                 file_store: Optional[Any] = None,
                 archive: Optional[Any] = None,
                 evaluator: Optional[Any] = None,
                 reviewer: Optional[Any] = None,
                 git: Optional[Any] = None,
                 llm_client: Optional[Any] = None,
                 proposal_generator: Optional[Any] = None,
                 output_guard: Optional[Any] = None,
                 token_estimator: Optional[Callable[[EditProposal], int]] = None,
                 max_skills_per_round: Optional[int] = None,
                 max_tokens_per_round: Optional[int] = None,
                 stall_rounds: Optional[int] = None,
                 eval_min_score: Optional[float] = None):
        self._policy = policy or EditPolicy()
        self._file_store = file_store
        self._archive = archive
        self._evaluator = evaluator
        self._reviewer = reviewer
        self._git = git
        self._llm_client = llm_client
        self._proposal_generator = proposal_generator
        self._output_guard = output_guard
        self._token_estimator = token_estimator
        self.max_skills_per_round = (
            max_skills_per_round if max_skills_per_round is not None
            else _env_max_skills_per_round())
        self.max_tokens_per_round = (
            max_tokens_per_round if max_tokens_per_round is not None
            else _env_max_tokens_per_round())
        self.stall_rounds = (
            stall_rounds if stall_rounds is not None else _env_stall_rounds())
        self.eval_min_score = (
            eval_min_score if eval_min_score is not None else _env_eval_min_score())
        self._lock = threading.RLock()
        # 单轮状态（start_round() 重置）
        self._round_edited: Dict[str, bool] = {}
        self._round_tokens: int = 0

    # ──────────────────────────────────────────────
    #  护栏：轮次状态
    # ──────────────────────────────────────────────

    def start_round(self) -> None:
        """开启新的一轮（重置单轮编辑技能数与 token 消耗）"""
        with self._lock:
            self._round_edited.clear()
            self._round_tokens = 0

    def is_stalled(self, skill_id: str) -> bool:
        """连续 K 轮无提升 → 暂停该技能进化（验收 5）"""
        return self._consecutive_no_gain(skill_id) >= self.stall_rounds

    def _consecutive_no_gain(self, skill_id: str) -> int:
        """从谱系评分代倒序统计连续"无提升"轮数（含 pending_review/被拒评估记录）

        判定口径: 新代得分 ≤ 旧代得分 记 1 轮无提升；出现提升即停止。
        无评分记录（首代）→ 0。
        """
        try:
            recs = [r for r in self._archive.list_by_object(skill_id)
                    if r.get_score() is not None]
        except Exception as e:  # noqa: BLE001 谱系不可用不阻断
            logger.warning("[MetaEditor] 谱系读取失败（按未暂停处理） %s: %s",
                           skill_id, e)
            return 0
        if not recs:
            return 0
        recs.sort(key=lambda r: r.created_at)
        count = 0
        newer_score = float(recs[-1].get_score())
        for older in reversed(recs[:-1]):
            s = float(older.get_score())
            if newer_score <= s:
                count += 1
                newer_score = s
            else:
                break
        return count

    def _round_allows(self, skill_id: str) -> bool:
        """单轮护栏：技能数上限 + 技能去重（验收 5）"""
        with self._lock:
            if skill_id in self._round_edited:
                logger.info("[MetaEditor] 该技能本轮已编辑过，跳过 skill=%s", skill_id)
                return False
            if len(self._round_edited) >= self.max_skills_per_round:
                logger.info(
                    "[MetaEditor] 单轮编辑技能数达上限 %d，停止产生新提案",
                    self.max_skills_per_round)
                return False
            return True

    def _spend_tokens(self, tokens: int) -> bool:
        """单轮 token 预算熔断；0=不熔断。返回 False 表示超限"""
        if self.max_tokens_per_round <= 0:
            return True
        with self._lock:
            if self._round_tokens + tokens > self.max_tokens_per_round:
                logger.info(
                    "[MetaEditor] 单轮 token 预算熔断 used=%d+%d > budget=%d",
                    self._round_tokens, tokens, self.max_tokens_per_round)
                return False
            self._round_tokens += tokens
            return True

    # ──────────────────────────────────────────────
    #  提案生成（一次仅一个提案）
    # ──────────────────────────────────────────────

    def propose(self, skill_id: str) -> Optional[EditProposal]:
        """生成一个编辑提案（draft）

        输入: 技能当前内容 + 谱系历史评分 + 评估器样本集
        输出: EditProposal 或 None（护栏拦截 / 无生成能力 / 技能不存在）
        """
        # 注意（守 project_memory 硬约束）：本方法不整体持锁——
        # 文件读取/生成器调用属 I/O 与外部回调，锁内仅保护内存轮次状态
        # （由 _round_allows / _spend_tokens / 末尾注册各自加锁）。
        if not self._round_allows(skill_id):
            return None
        logger.debug("[MetaEditor] 生成提案开始 skill=%s", skill_id)
        if self.is_stalled(skill_id):
            logger.info("[MetaEditor] 技能连续 %d 轮无提升，暂停进化 skill=%s",
                        self.stall_rounds, skill_id)
            return None

        meta = self._read_meta(skill_id)
        if meta is None:
            logger.warning("[MetaEditor] 技能不存在，跳过 skill=%s", skill_id)
            return None
        raw_path = self._primary_file_path(skill_id)
        raw_content = self._read_file_raw(raw_path)
        if raw_content is None:
            logger.warning("[MetaEditor] 技能文件读取失败 skill=%s path=%s",
                           skill_id, raw_path)
            return None

        gen = self._proposal_generator or MetaEditGenerator(
            llm_client=self._llm_client, output_guard=self._output_guard)
        logger.debug("[MetaEditor] 调用生成器 skill=%s generator=%s",
                     skill_id, type(gen).__name__)
        # 生成器（LLM）是进化链路最大耗时源，前后打点统计性能瓶颈
        _t_gen0 = time.perf_counter()
        result = gen.generate(
            skill_id=skill_id,
            current_content=raw_content,
            lineage_summary=self._lineage_summary(skill_id),
            sample_tasks=self._sample_tasks(),
            current_meta=meta,
        )
        _t_gen1 = time.perf_counter()
        logger.debug(
            "[MetaEditor] 生成器返回 skill=%s duration_ms=%.2f result=%s "
            "generator=%s",
            skill_id, (_t_gen1 - _t_gen0) * 1000,
            "proposal" if result is not None else "None",
            type(gen).__name__)
        if result is None:
            logger.warning("[MetaEditor] 生成器未产出提案 skill=%s"
                           "（无 LLM / 输出护栏 / 非法输出）", skill_id)
            return None

        files: List[EditFile] = []
        for item in result.get("files") or []:
            file_path = str(item.get("file_path") or "")
            new_content = str(item.get("new_content") or "")
            if not file_path or not new_content:
                continue
            old_content = self._read_file_raw(file_path)
            if old_content is None:
                # 新文件不允许：白名单内文件必须已存在（防元智能体凭空造文件）
                logger.warning(
                    "[MetaEditor] 编辑目标不存在，丢弃提案 skill=%s path=%s",
                    skill_id, file_path)
                return None
            files.append(EditFile(
                file_path=file_path,
                old_content=old_content,
                new_content=new_content,
            ))
        if not files:
            return None

        proposal = EditProposal(
            object_type="tool_doc"
            if str(result.get("edit_type")) == EditType.DOCUMENTATION.value
            else "tool_code",
            object_id=skill_id,
            files=files,
            edit_type=str(result.get("edit_type", EditType.CONTENT.value)),
            change_summary=str(result.get("change_summary", "")),
            expected_gain=str(result.get("expected_gain", "")),
            parent_record_id=self._latest_record_id(skill_id),
        )
        # 政策校验（白名单/类型/范围/内容/文件数，越界直接抛）
        self._policy.validate_proposal(proposal)
        logger.debug("[MetaEditor] 政策校验通过 proposal=%s edit_type=%s files=%s",
                     proposal.proposal_id, proposal.edit_type,
                     [f.file_path for f in proposal.files])

        tokens = self._estimate_tokens(proposal)
        logger.debug("[MetaEditor] token 估算 proposal=%s tokens=%d budget=%d",
                     proposal.proposal_id, tokens, self.max_tokens_per_round)
        if not self._spend_tokens(tokens):
            return None
        proposal.cost_tokens = tokens
        with self._lock:
            self._round_edited[skill_id] = True
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "proposed", "skill_id": skill_id},
                    kind="counter")
        logger.info(
            "[MetaEditor] 提案已生成 %s skill=%s edit_type=%s files=%s",
            proposal.proposal_id, skill_id, proposal.edit_type,
            [f.file_path for f in files])
        return proposal

    # ──────────────────────────────────────────────
    #  三重审核（critical 直接拒绝，不进入评估）
    # ──────────────────────────────────────────────

    def review_proposal(self, proposal: EditProposal,
                        others: Optional[List[Any]] = None) -> EditProposal:
        """Reviewer 三重审核（重复/安全/质量）

        任一 critical 级问题 → 提案直接拒绝（status=rejected，不进入评估，验收 2）。
        """
        if proposal.status_enum != EditStatus.DRAFT:
            logger.warning("[MetaEditor] 提案 %s 状态 %s 不允许进入审核（需 draft）",
                           proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"只有 draft 提案可进入审核流程（当前 {proposal.status}）")
        from .reviewer import SkillReviewer
        reviewer = self._reviewer or SkillReviewer()
        subject = _ReviewSubject(
            skill_id=proposal.object_id,
            meta=self._read_meta(proposal.object_id) or {},
            content="\n\n".join(f.new_content for f in proposal.files),
        )
        logger.debug(
            "[MetaEditor] 进入三重审核 proposal=%s reviewer=%s files=%d "
            "content_bytes=%d",
            proposal.proposal_id, type(reviewer).__name__,
            len(proposal.files), len(subject.content))
        result = reviewer.review(subject, others=others or [])
        proposal.review = self._serialize_review(result)
        # findings 明细逐条记录（severity/code/message），定位具体问题
        for _f in proposal.review["findings"]:
            logger.debug(
                "[MetaEditor] review finding severity=%s code=%s location=%s "
                "message=%s",
                _f.get("severity"), _f.get("code"), _f.get("location"),
                _f.get("message"))

        status = str(getattr(getattr(result, "status", None), "value", ""))
        critical = any(
            f.get("severity") == "critical"
            for f in proposal.review["findings"])
        if status != "passed" or critical:
            reason = (f"review_critical: {proposal.review['summary']}"
                      if critical else f"review_failed: {proposal.review['summary']}")
            proposal.reject(reason)
            self._append_lineage(proposal, "rejected", reason)
            emit_metric("yunshu_skill_meta_edit_total",
                        labels={"stage": "review_rejected",
                                "reason": "critical" if critical else "failed"},
                        kind="counter")
            _sev: Dict[str, int] = {}
            for _f in proposal.review["findings"]:
                _s = _f.get("severity") or "unknown"
                _sev[_s] = _sev.get(_s, 0) + 1
            logger.info("[MetaEditor] 提案 %s 被审核拒绝：%s severities=%s score=%.2f",
                        proposal.proposal_id, reason, _sev,
                        proposal.review.get("score", 0.0))
        else:
            emit_metric("yunshu_skill_meta_edit_total",
                        labels={"stage": "review_passed"}, kind="counter")
            logger.info(
                "[MetaEditor] 提案 %s 审核通过 status=passed score=%.2f "
                "findings=%d dup=%.2f sec=%.2f quality=%.2f",
                proposal.proposal_id, proposal.review.get("score", 0.0),
                len(proposal.review["findings"]),
                proposal.review.get("duplicate_score", 0.0),
                proposal.review.get("security_score", 0.0),
                proposal.review.get("quality_score", 0.0))
        return proposal

    # ──────────────────────────────────────────────
    #  沙盒真实评估（任务 2 评估器）
    # ──────────────────────────────────────────────

    def evaluate_proposal(self, proposal: EditProposal) -> EditProposal:
        """在进程级沙盒中跑任务 2 真实评估，结果写入 proposal.eval_result

        评估器默认 SkillExecutorEvaluator（executor 层 multiprocessing 沙盒执行
        scripts/main.py，真实 success/latency/输出，绝不伪造指标）。
        """
        if proposal.status_enum != EditStatus.DRAFT:
            logger.warning("[MetaEditor] 提案 %s 状态 %s 不允许评估（需 draft）",
                           proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"只有 draft 提案可评估（当前 {proposal.status}）")
        candidate, new_params = self._build_candidate(proposal)
        logger.debug("[MetaEditor] 评估候选构建完成 proposal=%s params=%s",
                     proposal.proposal_id, new_params)
        evaluator = self._evaluator or self._default_evaluator(candidate)
        result = evaluator.evaluate(candidate, params=new_params)
        proposal.eval_result = self._serialize_eval(result)
        proposal.cost_tokens += int(getattr(result, "cost_tokens", 0) or 0)
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "evaluated", "status": proposal.eval_result["status"]},
                    kind="counter")
        logger.info(
            "[MetaEditor] 提案 %s 评估完成 skill=%s status=%s score=%.4f "
            "samples=%d cost=%d",
            proposal.proposal_id, proposal.object_id,
            proposal.eval_result.get("status"),
            proposal.eval_result.get("score", 0.0),
            proposal.eval_result.get("sample_count", 0),
            proposal.eval_result.get("cost_tokens", 0))
        return proposal

    # ──────────────────────────────────────────────
    #  提交（pending_review，绝不自动合并）
    # ──────────────────────────────────────────────

    def submit_proposal(self, proposal: EditProposal) -> EditProposal:
        """评估通过 + 审核通过 → pending_review + 落谱系（不自动合并，验收 3）"""
        if proposal.status_enum != EditStatus.DRAFT:
            logger.warning("[MetaEditor] 提案 %s 状态 %s 不允许提交（需 draft）",
                           proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"只有 draft 提案可提交（当前 {proposal.status}）")
        if not (proposal.review and self._review_passed(proposal.review)):
            logger.warning("[MetaEditor] 提案 %s 未过审核，禁止提交 review=%s",
                           proposal.proposal_id, proposal.review)
            raise EditPolicyError("提案未通过 Reviewer 三重审核，禁止提交")
        if not proposal.eval_result:
            logger.warning("[MetaEditor] 提案 %s 未评估，禁止提交",
                           proposal.proposal_id)
            raise EditPolicyError("提案未评估，禁止提交")
        if float(proposal.eval_result.get("score", 0.0)) < self.eval_min_score:
            logger.warning(
                "[MetaEditor] 提案 %s 评估得分 %.4f 低于阈值 %.4f，禁止提交",
                proposal.proposal_id,
                float(proposal.eval_result.get("score", 0.0)),
                self.eval_min_score)
            raise EditPolicyError(
                f"提案评估得分 {proposal.eval_result.get('score')} "
                f"低于阈值 {self.eval_min_score}，禁止提交")
        proposal.submit()  # draft → pending_review
        proposal.lineage_record_id = self._append_lineage(
            proposal, "pending_review", "等待人工审批（任务 6 审批流）",
            eval_result=proposal.eval_result)
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "pending_review"}, kind="counter")
        logger.info("[MetaEditor] 提案 %s 已提交待审批 lineage=%s",
                    proposal.proposal_id, proposal.lineage_record_id)
        return proposal

    # ──────────────────────────────────────────────
    #  人工审批接口（供任务 6 UI/API 消费）
    # ──────────────────────────────────────────────

    def approve_proposal(self, proposal: EditProposal,
                         actor: str = "user") -> EditProposal:
        """人工审批通过（pending_review → approved；仍不合并，需显式 merge）"""
        if proposal.status_enum != EditStatus.PENDING_REVIEW:
            logger.warning("[MetaEditor] 提案 %s 状态 %s 不允许审批（需 pending_review）",
                           proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"只有 pending_review 提案可审批（当前 {proposal.status}）")
        proposal.approve()
        proposal.decision_reason = f"人工审批通过 (actor={actor})"
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "approved", "actor": actor}, kind="counter")
        logger.info("[MetaEditor] 提案 %s 人工审批通过 actor=%s",
                    proposal.proposal_id, actor)
        return proposal

    def reject_proposal(self, proposal: EditProposal, reason: str = "",
                        actor: str = "user") -> EditProposal:
        """人工拒绝（pending_review → rejected）"""
        if proposal.status_enum != EditStatus.PENDING_REVIEW:
            logger.warning("[MetaEditor] 提案 %s 状态 %s 不允许人工拒绝（需 pending_review）",
                           proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"只有 pending_review 提案可人工拒绝（当前 {proposal.status}）")
        proposal.reject(reason)
        self._append_lineage(
            proposal, "rejected", f"人工拒绝 (actor={actor}): {reason}",
            eval_result=proposal.eval_result,
            parent_record_id=proposal.lineage_record_id)
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "human_rejected", "actor": actor},
                    kind="counter")
        logger.info("[MetaEditor] 提案 %s 人工拒绝 actor=%s reason=%s",
                    proposal.proposal_id, actor, reason)
        return proposal

    # ──────────────────────────────────────────────
    #  合并（唯一入口 = 显式审批）与 git 回滚
    # ──────────────────────────────────────────────

    def merge_proposal(self, proposal: EditProposal) -> EditProposal:
        """审批通过后应用补丁并 git 提交（验收 3：无审批无合并；验收 4：可回滚）"""
        if not proposal.is_mergeable:
            logger.warning(
                "[MetaEditor] 提案 %s 状态 %s 不可合并（仅 approved，无自动合并路径）",
                proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"仅审批通过（approved）的提案可合并（当前 {proposal.status}），"
                "不存在自动合并路径")
        # 合并前二次校验（守不易：越界/内容黑名单在写盘前再次拦截）
        logger.debug("[MetaEditor] 开始合并 proposal=%s files=%s",
                     proposal.proposal_id,
                     [f.file_path for f in proposal.files])
        self._policy.validate_proposal(proposal)

        git = self._git or self._default_git()
        for f in proposal.files:
            resolved = self._policy.validate_file_path(f.file_path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(f.new_content, encoding="utf-8")

        rel_paths = [self._rel_to_repo(git, f.file_path) for f in proposal.files]
        git.add(rel_paths)
        git.commit(f"meta-edit: {proposal.proposal_id} {proposal.change_summary or ''}")
        try:
            sha = git.log(limit=1)[0].sha
        except Exception as e:  # noqa: BLE001 提交后取 SHA 失败不致命，回滚需人工定位
            sha = ""
            logger.error("[MetaEditor] 提交后获取 SHA 失败 %s: %s", proposal.proposal_id, e)

        proposal.merge_commit_sha = sha
        proposal.mark_merged()  # approved → merged
        self._append_lineage(
            proposal, "committed",
            f"人工审批通过后 git 合并 (sha={sha or 'n/a'})",
            parent_record_id=proposal.lineage_record_id)
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "merged"}, kind="counter")
        logger.info("[MetaEditor] 提案 %s 已合并 sha=%s files=%s",
                    proposal.proposal_id, sha, rel_paths)
        return proposal

    def rollback(self, proposal: EditProposal) -> EditProposal:
        """从 git 回滚已合并提案（验收 4）

        回滚语义: 读取合并 commit 的父提交（<merge_sha>^）中该文件内容恢复，
        再提交 rollback commit；提案状态 merged → archived。
        """
        if proposal.status_enum != EditStatus.MERGED:
            logger.warning("[MetaEditor] 提案 %s 状态 %s 不允许回滚（需 merged）",
                           proposal.proposal_id, proposal.status)
            raise EditStatusTransitionError(
                f"只有 merged 提案可回滚（当前 {proposal.status}）")
        if not proposal.merge_commit_sha:
            logger.error("[MetaEditor] 提案 %s 缺少 merge_commit_sha，无法回滚",
                         proposal.proposal_id)
            raise MetaEditError("提案缺少 merge_commit_sha，无法从 git 回滚")
        git = self._git or self._default_git()
        rel_paths: List[str] = []
        for f in proposal.files:
            rel = self._rel_to_repo(git, f.file_path)
            parent_content = git.show(f"{proposal.merge_commit_sha}^", rel)
            resolved = self._policy.validate_file_path(f.file_path)
            resolved.write_text(parent_content, encoding="utf-8")
            rel_paths.append(rel)
            logger.debug("[MetaEditor] 从父提交恢复 path=%s ref=%s^ bytes=%d",
                         rel, proposal.merge_commit_sha, len(parent_content))
        git.add(rel_paths)
        git.commit(f"rollback {proposal.proposal_id}")
        self._append_lineage(
            proposal, "rolled_back",
            f"git 回滚 (merge_sha={proposal.merge_commit_sha})",
            parent_record_id=self._latest_record_id(proposal.object_id))
        proposal.archive()  # merged → archived
        emit_metric("yunshu_skill_meta_edit_total",
                    labels={"stage": "rolled_back"}, kind="counter")
        logger.info("[MetaEditor] 提案 %s 已回滚 files=%s",
                    proposal.proposal_id, rel_paths)
        return proposal

    # ──────────────────────────────────────────────
    #  读取辅助
    # ──────────────────────────────────────────────

    @property
    def _file_store_ref(self) -> Any:
        if self._file_store is None:
            from .file_store import SkillFileStore
            self._file_store = SkillFileStore()
        return self._file_store

    @property
    def _archive_ref(self) -> Any:
        if self._archive is None:
            from .lineage import get_default_archive
            self._archive = get_default_archive()
        return self._archive

    def _default_git(self) -> Any:
        if self._git is None:
            from .git_sync import GitSync
            self._git = GitSync(self._policy.project_root)
        return self._git

    def _default_evaluator(self, candidate: Any) -> Any:
        from .evaluator import EvaluatorRegistry
        return EvaluatorRegistry().get(candidate)  # 分阶段真实评估

    def _read_meta(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """读取技能元数据（front matter）；不存在返回 None"""
        try:
            meta, _body, _scripts, _temps = self._file_store_ref.read(skill_id)
            return meta or {}
        except Exception as e:  # noqa: BLE001 技能不存在/损坏 → None
            logger.debug("[MetaEditor] 读取技能失败 skill=%s: %s", skill_id, e)
            return None

    def _primary_file_path(self, skill_id: str) -> str:
        """技能主文件路径（相对项目根）：<白名单相对>/<skill_id>/skill.md"""
        wl = self._policy.whitelist_dirs[0]
        try:
            rel = wl.relative_to(self._policy.project_root)
        except ValueError:
            rel = Path(wl.name)
        return f"{rel.as_posix()}/{skill_id}/skill.md"

    def _read_file_raw(self, file_path: str) -> Optional[str]:
        """读取白名单内文件原始文本（先政策校验再读盘，防越界）"""
        try:
            resolved = self._policy.validate_file_path(file_path)
        except Exception as e:  # noqa: BLE001 越界/禁止 → None
            logger.warning("[MetaEditor] 读取被拦截 path=%s: %s", file_path, e)
            return None
        try:
            return resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("[MetaEditor] 文件读取失败 path=%s: %s", file_path, e)
            return None

    def _lineage_summary(self, skill_id: str) -> str:
        """谱系历史评分摘要（近 5 代，供生成器参考）"""
        try:
            recs = self._archive_ref.list_by_object(skill_id)
        except Exception:  # noqa: BLE001
            return "(无)"
        lines = []
        for r in recs[-5:]:
            score = r.get_score()
            lines.append(f"{r.created_at} {r.record_id} {r.decision} "
                         f"score={score if score is not None else '-'}")
        return "\n".join(lines) or "(无)"

    def _sample_tasks(self) -> str:
        """评估器样本集任务摘要（供生成器参考，任务 2 样本）"""
        try:
            pool = getattr(self._evaluator, "pool", None)
            if pool is None:
                return "(无样本)"
            tasks = []
            for cat in (pool.categories() or [])[:5]:
                for s in (pool.get(cat) or [])[:1]:
                    tasks.append(f"[{cat}] {s.task}")
            return "\n".join(tasks) or "(无样本)"
        except Exception:  # noqa: BLE001
            return "(无样本)"

    def _latest_record_id(self, skill_id: str) -> Optional[str]:
        """该技能最新谱系记录 ID（作为新提案父代）"""
        try:
            recs = self._archive_ref.list_by_object(skill_id)
        except Exception:  # noqa: BLE001
            return None
        return recs[-1].record_id if recs else None

    def _estimate_tokens(self, proposal: EditProposal) -> int:
        if self._token_estimator is not None:
            return max(0, int(self._token_estimator(proposal)))
        from .evaluator import TokenBudget
        return TokenBudget.estimate(proposal.patch, proposal.change_summary)

    def _build_candidate(self, proposal: EditProposal) -> tuple:
        """构建评估候选对象 + 覆盖参数

        PARAMS 编辑: 从编辑后的 skill.md front matter 提取 default_params，
        评估器以新参数真实执行 scripts/main.py（真实证据，非预测值）。
        """
        meta = self._read_meta(proposal.object_id) or {}
        new_params = dict(meta.get("default_params") or {})
        if proposal.edit_type == EditType.PARAMS.value:
            for f in proposal.files:
                if f.file_path.endswith("skill.md"):
                    try:
                        from .file_store import SkillMDParser
                        new_meta, _ = SkillMDParser.parse(f.new_content)
                        if isinstance(new_meta.get("default_params"), dict):
                            new_params = dict(new_meta["default_params"])
                    except Exception as e:  # noqa: BLE001 解析失败保持原参数
                        logger.warning(
                            "[MetaEditor] 编辑后 front matter 解析失败，沿用原参数 %s: %s",
                            proposal.proposal_id, e)
        candidate = types.SimpleNamespace(
            id=proposal.object_id,
            tags=list(meta.get("tags") or []),
            default_params=new_params,
        )
        return candidate, new_params

    @staticmethod
    def _serialize_review(result: Any) -> Dict[str, Any]:
        """Reviewer 结果 → 可 JSON 化 dict（防御性：pydantic 或普通对象）"""
        findings = []
        for f in getattr(result, "findings", None) or []:
            try:
                findings.append(f.model_dump())
            except Exception:  # noqa: BLE001 非 pydantic finding
                findings.append({
                    "severity": getattr(f, "severity", ""),
                    "category": getattr(f, "category", ""),
                    "code": getattr(f, "code", ""),
                    "message": getattr(f, "message", ""),
                    "location": getattr(f, "location", None),
                })
        status = getattr(result, "status", "")
        return {
            "status": getattr(status, "value", status),
            "score": getattr(result, "score", 0.0),
            "summary": getattr(result, "summary", ""),
            "findings": findings,
            "duplicate_score": getattr(result, "duplicate_score", 0.0),
            "security_score": getattr(result, "security_score", 0.0),
            "quality_score": getattr(result, "quality_score", 0.0),
        }

    @staticmethod
    def _serialize_eval(result: Any) -> Dict[str, Any]:
        """EvaluationResult → 与 EvolutionRecord.eval_result 对齐的 dict"""
        base: Dict[str, Any] = {}
        try:
            base = result.to_eval_result_dict()
        except Exception:  # noqa: BLE001
            base = {"score": getattr(result, "score", 0.0)}
        base.update({
            "status": getattr(result, "status", ""),
            "success_rate": getattr(result, "success_rate", 0.0),
            "latency_ms": getattr(result, "latency_ms", 0.0),
            "satisfaction": getattr(result, "satisfaction", 0.0),
            "cost_tokens": getattr(result, "cost_tokens", 0),
        })
        return base

    @staticmethod
    def _review_passed(review: Dict[str, Any]) -> bool:
        return str(review.get("status", "")).lower() == "passed"

    def _append_lineage(self, proposal: EditProposal, decision: str,
                        reason: str, *, eval_result: Optional[Dict[str, Any]] = None,
                        parent_record_id: Optional[str] = None) -> str:
        """写入一条谱系记录（object_type 按编辑类型区分 tool_code/tool_doc）"""
        from .lineage import EvolutionRecord
        meta = self._read_meta(proposal.object_id) or {}
        version = str(meta.get("version") or "")
        rec = EvolutionRecord(
            object_type="tool_doc"
            if proposal.edit_type == EditType.DOCUMENTATION.value else "tool_code",
            object_id=proposal.object_id,
            parent_record_id=parent_record_id or proposal.parent_record_id,
            parent_version=version,
            new_version=version,
            strategy="meta_edit",
            change_summary=proposal.change_summary,
            decision_reason=reason,
            decision=decision,
            trigger="manual",
            actor="system",
            eval_result=eval_result,
            cost={"tokens": proposal.cost_tokens},
            params={
                "proposal_id": proposal.proposal_id,
                "edit_type": proposal.edit_type,
                "files": [f.file_path for f in proposal.files],
            },
        )
        return self._archive_ref.append(rec)

    def _rel_to_repo(self, git: Any, file_path: str) -> str:
        """文件路径 → 相对 git 仓库根路径（posix 分隔，供 git add/show）

        相对路径统一按 project_root 解析（不依赖 CWD），再换算到 git 仓库根。
        """
        abs_path = (self._policy.project_root / file_path).resolve()
        try:
            return abs_path.relative_to(Path(git.repo_path).resolve()).as_posix()
        except ValueError:
            raise MetaEditError(f"编辑文件不在 git 仓库内: {file_path}")


def get_default_meta_editor(**kwargs: Any) -> MetaEditor:
    """便捷入口：进程内默认元智能体编辑器"""
    return MetaEditor(**kwargs)
