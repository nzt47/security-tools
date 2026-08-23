"""LLM-as-Judge 双假设验证 · Judge dry-run 通道（任务5）

背景（Why）:
    TASK-08 报告 §3.3 的 LLM-as-Judge 引入门槛存在**因果歧义（理论缺陷 T3）**：
    低进化采纳率（KPI#7 <5% 连续 4 周）有两种互斥归因——
      假设 A：评估不精细——规则评估无法区分候选质量，好候选被误拒 → 需要更精细的 Judge；
      假设 B：候选质量差——变异/生成环节产出本身就差，评估正确拒绝 → 需要改进候选生成。
    报告只设计了"dry-run 记录 2 周"，没有设计如何在 A/B 之间判别。
    本模块实现**双假设判别实验**：同一候选集同时过"规则评估通道"与"Judge 通道"，
    记录分歧率（两通道判定不一致的候选占比）与采纳率差异（若按 Judge 判定会采纳/拒绝多少），
    按预设判别规则输出"启用/不启用 Judge"结论（判别报告数据源 + 判别结论计算）。

不变式（任务提示词 §3，禁止触碰）:
    1. Judge 通道**零干预**：任何情况下不改变提交/采纳/回滚决策（dry-run 只记录，审计可证）。
       本模块不 import 任何提交/审批/回滚模块（approval / rollback / offline_evolver /
       evolution_scheduler / meta_editor / lineage 写路径）；唯一副作用 = Judge 审计
       JSONL + LearningMetrics.record_judge_result（纯观测）+ Prometheus gauge。
    2. 预算 enforce 前置：learning.budget.mode != "enforce" 时（warn_only 不强制），
       本通道拒绝任何 LLM 调用（report §3.3 否决条件；候选标记 budget_not_enforce）；
       所有 LLM 调用经 get_learning_budget().with_budget("judge_channel", ...) 记账，
       超限 → 该候选标记 budget_blocked 跳过（不伪造指标、不部分执行）。
    3. 不修改既有规则评估器（ReflectionEngine 6 维规则 / feedback.py / reviewer /
       critic.py RULE_BASED）行为；规则通道判定取候选记录中的既有规则结论（只读回放），
       无记录时用本模块内**对齐镜像**纯函数（不触碰既有实现）。
    4. 开关默认 enabled=false、dry-run=true；任何"干预模式"属远期决策（报告 §3.3 流程
       + 判别结论"支持引入"后才评估），本模块不存在任何干预路径（代码审计零绕过）。

开关（优先级: 环境变量 > config.yaml learning.judge > 硬编码默认值）:
    LEARNING_JUDGE_ENABLED                   通道总开关（默认 false → 全部跳过，零 LLM）
    LEARNING_JUDGE_DRYRUN_ENABLED           dry-run 语义开关（默认 true，只写不干预；
                                            false 也仅改变审计 mode 标注，本模块无干预路径）
    LEARNING_JUDGE_AUDIT_FILE               审计 JSONL 路径（默认 data/learning/judge_audit.jsonl）
    LEARNING_JUDGE_DISAGREEMENT_THRESHOLD   分歧率阈值（默认 0.10）
    LEARNING_JUDGE_MIN_ADOPTION_DELTA_PP    采纳率差异阈值 pp（默认 10.0）
    LEARNING_JUDGE_MIN_CANDIDATES           最小判别基数（默认 5）
    LEARNING_JUDGE_COLLECTION_WEEKS         采集周期周数（默认 2）
    LEARNING_JUDGE_MAX_ESTIMATED_TOKENS     单候选预估 token 上限（默认 2000）

判别规则（预设，写入判别报告 §3；discriminate() 纯函数）:
    输入: judged 样本量 / 分歧率 / 规则通道采纳率 / Judge 通道采纳率
    1) judged < min_candidates            → insufficient_data → **不启用**（继续采集）
    2) 分歧率 < 阈值                      → 假设 B（候选质量差）→ **不引入 Judge**，
                                             转向改进候选生成
    3) 分歧率 ≥ 阈值 且 (implied-rule) ≥ +Δpp
                                          → 假设 A（评估不精细）→ **支持引入 Judge**，
                                             按报告 §3.3 dry-run 2 周流程评估启用
    4) 其余（分歧高但采纳率差异不足）      → 证据不足 → **不启用**（继续采集 / 复核评估 prompt）
    任一结论均含明确 basis（数据与阈值），不含模糊结论。

CLI:
    python -m agent.learning.judge_channel --status
    python -m agent.learning.judge_channel --run-batch [--source rollout_audit|--candidates-file <jsonl>]
    python -m agent.learning.judge_channel --discriminate
    python -m agent.learning.judge_channel --report
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  判定 / 状态 / 结论枚举
# ════════════════════════════════════════════════════════════

VERDICT_ACCEPT = "accept"
VERDICT_REJECT = "reject"
VERDICTS = (VERDICT_ACCEPT, VERDICT_REJECT)

# Judge 通道逐候选状态（judge_status）
STATUS_JUDGED = "judged"               # Judge 产出有效判定（进入分歧率/采纳率统计）
STATUS_SKIPPED = "skipped"             # 未调用 LLM（通道关闭/无客户端/无规则判定/解析失败等）
STATUS_BUDGET_BLOCKED = "budget_blocked"       # 预算超限熔断拦截（不伪造判定）
STATUS_BUDGET_NOT_ENFORCE = "budget_not_enforce"  # 预算非 enforce（报告 §3.3 否决条件）
STATUSES = (STATUS_JUDGED, STATUS_SKIPPED, STATUS_BUDGET_BLOCKED,
            STATUS_BUDGET_NOT_ENFORCE)

# 判别结论（写入判别报告 §3；recommendation 恒为二值：not_introduce / evaluate_introduce）
CONCLUSION_B_CANDIDATE_QUALITY = "hypothesis_b_candidate_quality"
CONCLUSION_A_EVAL_INSUFFICIENT = "hypothesis_a_eval_insufficient"
CONCLUSION_INSUFFICIENT_DATA = "insufficient_data"
CONCLUSION_INCONCLUSIVE = "inconclusive"
RECOMMEND_NOT_INTRODUCE = "not_introduce"
RECOMMEND_EVALUATE_INTRODUCE = "evaluate_introduce"

# 默认值（优先级最低层）
DEFAULT_ENABLED = False
DEFAULT_DRYRUN = True
DEFAULT_AUDIT_FILE = "data/learning/judge_audit.jsonl"
DEFAULT_DISAGREEMENT_THRESHOLD = 0.10
DEFAULT_MIN_ADOPTION_DELTA_PP = 10.0
DEFAULT_MIN_CANDIDATES = 5
DEFAULT_COLLECTION_WEEKS = 2
DEFAULT_MAX_ESTIMATED_TOKENS = 2000
_ENV_PREFIX = "LEARNING_JUDGE"

# 与既有采纳门槛对齐的镜像规则（独立纯函数；不改既有评估器）
_MIRROR_IMPROVEMENT_THRESHOLD = 0.05   # offline_evolver 既有 improvement 门槛
_MIRROR_SAFETY_RED_LINE = 0.6          # 策略筛选既有安全红线


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class JudgeCandidate:
    """进入双通道评估的候选（任务3 observe/confirm 真实候选或合成数据）

    Attributes:
        candidate_id: 候选唯一标识
        source: 候选来源（rollout_observe / rollout_confirm / synthetic / 其他）
        content: 候选文本载荷（送 LLM 前经脱敏管道处理）
        rule_verdict: 既有规则评估记录的结论（只读回放；None = 无记录）
        scores: 候选评分字段（improvement / safety 等；镜像规则用）
        metadata: 附加元数据（不送 LLM；仅审计）
    """
    candidate_id: str
    source: str = "unknown"
    content: str = ""
    rule_verdict: Optional[str] = None
    scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "rule_verdict": self.rule_verdict,
            "scores": dict(self.scores),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JudgeCandidate":
        return cls(
            candidate_id=str(data.get("candidate_id") or uuid.uuid4().hex[:12]),
            source=str(data.get("source") or "unknown"),
            content=str(data.get("content") or ""),
            rule_verdict=data.get("rule_verdict"),
            scores=dict(data.get("scores") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


# ════════════════════════════════════════════════════════════
#  配置读取（环境变量 > config.yaml learning.judge > 硬编码默认值）
# ════════════════════════════════════════════════════════════


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml as _yaml  # 延迟导入，避免硬依赖
        with open(cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[Judge] config.yaml 读取失败: %s", e)
        return None


def _judge_cfg() -> Dict[str, Any]:
    cfg = _config_yaml()
    if cfg is None:
        return {}
    node = ((cfg.get("learning") or {}).get("judge") or {})
    return node if isinstance(node, dict) else {}


def _env(key: str) -> Optional[str]:
    raw = os.environ.get(f"{_ENV_PREFIX}_{key}")
    return raw if raw is not None and str(raw).strip() else None


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("true", "1", "yes")


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def load_judge_config() -> Dict[str, Any]:
    """解析 Judge 通道配置（环境变量 > config.yaml > 硬编码默认值）"""
    node = _judge_cfg()
    return {
        "enabled": _env_bool("ENABLED", bool(node.get("enabled", DEFAULT_ENABLED))),
        "dry_run": _env_bool("DRYRUN_ENABLED", bool(node.get("dry_run", DEFAULT_DRYRUN))),
        "audit_file": _env("AUDIT_FILE") or str(node.get("audit_file") or DEFAULT_AUDIT_FILE),
        "disagreement_threshold": _env_float(
            "DISAGREEMENT_THRESHOLD",
            float(node.get("disagreement_threshold", DEFAULT_DISAGREEMENT_THRESHOLD))),
        "min_adoption_delta_pp": _env_float(
            "MIN_ADOPTION_DELTA_PP",
            float(node.get("min_adoption_delta_pp", DEFAULT_MIN_ADOPTION_DELTA_PP))),
        "min_candidates": _env_int(
            "MIN_CANDIDATES",
            int(node.get("min_candidates", DEFAULT_MIN_CANDIDATES))),
        "collection_weeks": _env_int(
            "COLLECTION_WEEKS",
            int(node.get("collection_weeks", DEFAULT_COLLECTION_WEEKS))),
        "max_estimated_tokens_per_candidate": _env_int(
            "MAX_ESTIMATED_TOKENS",
            int(node.get("max_estimated_tokens_per_candidate",
                        DEFAULT_MAX_ESTIMATED_TOKENS))),
    }


# ════════════════════════════════════════════════════════════
#  规则通道判定（只读回放优先 + 对齐镜像）
# ════════════════════════════════════════════════════════════

def rule_verdict_mirror(candidate: JudgeCandidate) -> Optional[str]:
    """规则通道判定（对齐既有规则评估结论的纯函数，**不修改既有评估器**）

    优先级:
      1) candidate.rule_verdict（既有规则评估记录的结论，只读回放——与真实规则通道同源，
         保证"同一候选集"的比较语义）；
      2) 镜像规则（对齐既有采纳门槛: improvement>=0.05 且 safety>=0.6 → accept；
         关键分缺失 → None，候选不进入双通道比较，诚实跳过）。

    Returns:
        accept / reject / None（无既有记录且关键分缺失）
    """
    if candidate.rule_verdict is not None:
        verdict = str(candidate.rule_verdict).strip().lower()
        return verdict if verdict in VERDICTS else None
    scores = candidate.scores or {}
    improvement = scores.get("improvement")
    safety = scores.get("safety")
    if improvement is None and safety is None:
        return None
    imp_ok = improvement is None or float(improvement) >= _MIRROR_IMPROVEMENT_THRESHOLD
    safe_ok = safety is None or float(safety) >= _MIRROR_SAFETY_RED_LINE
    if imp_ok and safe_ok:
        return VERDICT_ACCEPT
    return VERDICT_REJECT


# ════════════════════════════════════════════════════════════
#  LLM 调用（复用既有 duck-typed LLM 客户端惯例 + 脱敏管道）
# ════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """token 预估（字符数/4 启发式，与报告 §3.1 成本模型口径一致；中英文混合低估可接受）"""
    return max(1, len(str(text or "")) // 4)


def _redact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏（复用 agent/utils/token_redactor 管道）；失败原样返回，不影响主流程"""
    try:
        from agent.utils.token_redactor import redact_recursive
        return redact_recursive(payload)
    except Exception:  # noqa: BLE001
        return payload


def _redact_text(text: str) -> str:
    try:
        from agent.utils.token_redactor import redact_sensitive_tokens
        return redact_sensitive_tokens(str(text))
    except Exception:  # noqa: BLE001
        return str(text)


def _call_llm(llm_client: Any, prompt: str) -> Optional[str]:
    """调用 LLM（遵循项目通用 duck-typed 接口 chat/invoke/complete/generate）

    Returns:
        LLM 原始响应文本；客户端缺失/调用异常返回 None
    """
    if llm_client is None:
        return None
    for method in ("chat", "invoke", "complete", "generate"):
        if hasattr(llm_client, method):
            try:
                resp = getattr(llm_client, method)(prompt)
                return str(resp) if resp is not None else None
            except Exception as e:  # noqa: BLE001 LLM 异常 → 降级（不伪造判定）
                logger.warning("[Judge] LLM 调用失败（method=%s）: %s", method, e)
                return None
    return None


# 注意：不采用 "yes"/"no" 单字标记（过于贪婪，普通文本易误命中）；只匹配明确语义标记
_ACCEPT_MARKERS = ("accept", "adopt", "approve", "采纳", "通过", "verdict\": \"accept")
_REJECT_MARKERS = ("reject", "refuse", "decline", "拒绝", "不采纳", "verdict\": \"reject")


def _parse_judge_response(response: str) -> Optional[Tuple[str, float, str]]:
    """从 LLM 响应提取 (verdict, confidence, reason)

    优先解析 JSON（{"verdict": "accept"/"reject", "confidence": 0-1, "reason": "..."}）；
    失败时按关键词兜底（accept/reject 语义标记）；两者均失败返回 None（候选跳过，
    不伪造判定）。confidence ∈ [0,1] 解析失败默认 0.5。
    """
    text = str(response or "").strip()
    if not text:
        return None
    parsed: Optional[Dict[str, Any]] = None
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
        except Exception:  # noqa: BLE001
            parsed = None
    if isinstance(parsed, dict):
        verdict_raw = str(parsed.get("verdict") or parsed.get("decision") or "").strip().lower()
        verdict = None
        if any(m in verdict_raw for m in _ACCEPT_MARKERS):
            verdict = VERDICT_ACCEPT
        elif any(m in verdict_raw for m in _REJECT_MARKERS):
            verdict = VERDICT_REJECT
        if verdict is not None:
            try:
                confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            reason = _redact_text(str(parsed.get("reason") or ""))
            return verdict, confidence, reason
    # 关键词兜底（无 JSON 时）
    low = text.lower()
    if any(m in low for m in _ACCEPT_MARKERS):
        return VERDICT_ACCEPT, 0.5, _redact_text(text[:200])
    if any(m in low for m in _REJECT_MARKERS):
        return VERDICT_REJECT, 0.5, _redact_text(text[:200])
    return None


def _build_judge_prompt(candidate: JudgeCandidate) -> str:
    """构造 Judge prompt（载荷已脱敏；提示词与既有规则评估 6 维精神对齐，
    但由 LLM 独立判断——这正是"更精细评估"假设的检验点）"""
    payload = _redact_payload({
        "candidate_id": candidate.candidate_id,
        "content": candidate.content or "(空)",
        "scores": candidate.scores,
        "rule_verdict": candidate.rule_verdict,
    })
    return (
        "你是一名进化候选质量评审（LLM-as-Judge，dry-run 通道，你的判定只用于实验对比，"
        "不产生任何实际采纳行为）。请从以下维度独立评审候选变异体质量：\n"
        "1. 正确性：内容是否自洽、无错误/矛盾；\n"
        "2. 安全性：是否符合安全红线，无危险指令；\n"
        "3. 效用性：相对既有方案是否带来可感知改进；\n"
        "4. 效率：是否简洁、无冗余；\n"
        "5. 清晰度：是否结构清晰、可直接执行；\n"
        "6. 非过度拒绝：是否因规则过于苛刻而被误拒（评估不精细问题的检验点）。\n"
        "请严格返回 JSON：{\"verdict\": \"accept\" 或 \"reject\", "
        "\"confidence\": 0~1 的小数, \"reason\": \"一句话依据\"}\n\n"
        f"候选数据:\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    )


# ════════════════════════════════════════════════════════════
#  审计（只写 judge_audit.jsonl；异常静默零影响）
# ════════════════════════════════════════════════════════════

_audit_lock = threading.Lock()


def _append_audit(record: Dict[str, Any], audit_file: str) -> None:
    try:
        path = Path(audit_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _audit_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 审计失败不影响主流程（零影响不变式）
        logger.warning("[Judge] 审计写入失败（静默）: %s", e)


# ════════════════════════════════════════════════════════════
#  单候选评估
# ════════════════════════════════════════════════════════════

def _budget_mode(budget: Any) -> str:
    """读取预算模式（LearningBudget 实例或 dict；异常/缺失 → ""）

    仅 enforce 模式允许 Judge 通道发起 LLM 调用（报告 §3.3 否决条件）。
    """
    try:
        if hasattr(budget, "mode"):
            return str(budget.mode).strip().lower()
        return str((budget or {}).get("mode") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def evaluate_one(candidate: JudgeCandidate,
                 llm_client: Optional[Any] = None,
                 budget: Any = None,
                 metrics: Any = None,
                 cfg: Optional[Dict[str, Any]] = None,
                 batch_id: str = "",
                 ) -> Dict[str, Any]:
    """对单个候选执行双通道评估（规则通道 + Judge 通道，dry-run 只记录）

    Args:
        candidate: 候选（含既有规则结论或评分）
        llm_client: duck-typed LLM 客户端（None → no_llm_client 跳过）
        budget: LearningBudget 实例（None → 全局单例 get_learning_budget()）
        metrics: LearningMetrics 实例（None → 全局 get_learning_metrics()）
        cfg: Judge 通道配置（None → load_judge_config()）
        batch_id: 批次标识（审计追溯）

    Returns:
        逐候选审计记录（含 judge_status / disagreement / intervention=False）
    """
    cfg = cfg if cfg is not None else load_judge_config()
    if metrics is None:
        from agent.learning_metrics import get_learning_metrics
        metrics = get_learning_metrics()
    ts_iso = datetime.now().isoformat(timespec="seconds")

    rule = rule_verdict_mirror(candidate)
    record: Dict[str, Any] = {
        "ts": ts_iso,
        "batch_id": batch_id or "",
        "candidate_id": candidate.candidate_id,
        "candidate_source": candidate.source,
        "rule_verdict": rule,
        "judge_verdict": None,
        "judge_status": STATUS_SKIPPED,
        "skip_reason": None,
        "confidence": None,
        "disagreement": False,
        "implied_adoption": False,
        "tokens_used": 0,
        "mode": "dry_run" if cfg.get("dry_run", True) else "observe",
        "intervention": False,  # 零干预不变式：本条记录仅观测，绝不改变任何决策
    }

    # ── 前置闸门 1：通道总开关（enabled=false → 全部跳过，零 LLM）──
    if not cfg.get("enabled", False):
        record["skip_reason"] = "channel_disabled"
        _finish_candidate(metrics, record)
        _append_audit(record, cfg["audit_file"])
        return record

    # ── 前置闸门 2：规则通道必须能产出判定（无既有记录且关键分缺失）──
    if rule is None:
        record["skip_reason"] = "no_rule_verdict"
        _finish_candidate(metrics, record)
        _append_audit(record, cfg["audit_file"])
        return record

    # ── 前置闸门 3：预算 enforce 前置（warn_only 不强制 → 否决 LLM 型评估角色）──
    if budget is None:
        from agent.learning_budget import get_learning_budget
        budget = get_learning_budget()
    if _budget_mode(budget) != "enforce":
        record["judge_status"] = STATUS_BUDGET_NOT_ENFORCE
        record["skip_reason"] = "budget_mode_not_enforce"
        _finish_candidate(metrics, record)
        _append_audit(record, cfg["audit_file"])
        return record

    # ── 前置闸门 4：LLM 客户端可用 ──
    if llm_client is None:
        record["skip_reason"] = "no_llm_client"
        _finish_candidate(metrics, record)
        _append_audit(record, cfg["audit_file"])
        return record

    # ── Judge 通道：LLM 调用（经 learning.budget 记账，超限熔断跳过不伪造）──
    prompt = _build_judge_prompt(candidate)
    estimated = min(
        _estimate_tokens(prompt),
        max(0, int(cfg.get("max_estimated_tokens_per_candidate",
                           DEFAULT_MAX_ESTIMATED_TOKENS))),
    )
    try:
        with budget.with_budget("judge_channel", estimated_tokens=estimated):
            raw = _call_llm(llm_client, prompt)
    except Exception as e:  # noqa: BLE001
        # 【不易】预算超限熔断（LearningBudgetExceeded）：LLM 未调用 → 零成本入账，
        # 标记 budget_blocked，绝不伪造判定、不部分执行；其余异常（防御性分支）
        # 视为 LLM 调用后失败 → 预估成本入账（成本核算诚实）。
        from agent.learning_budget import LearningBudgetExceeded
        if isinstance(e, LearningBudgetExceeded):
            record["judge_status"] = STATUS_BUDGET_BLOCKED
            record["skip_reason"] = f"budget:{e.reason}"
            _finish_candidate(metrics, record, tokens_used=0)
        else:
            record["skip_reason"] = f"llm_error:{type(e).__name__}"
            _finish_candidate(metrics, record, tokens_used=estimated)
        _append_audit(record, cfg["audit_file"])
        return record

    record["tokens_used"] = estimated  # 实际发生了 LLM 调用 → 预估成本入账（成本核算）
    parsed = _parse_judge_response(raw)
    if parsed is None:
        record["skip_reason"] = "parse_failed"
        _finish_candidate(metrics, record, tokens_used=estimated)
        _append_audit(record, cfg["audit_file"])
        return record

    judge_verdict, confidence, reason = parsed
    record["judge_status"] = STATUS_JUDGED
    record["judge_verdict"] = judge_verdict
    record["confidence"] = round(float(confidence), 4)
    record["disagreement"] = judge_verdict != rule
    record["implied_adoption"] = judge_verdict == VERDICT_ACCEPT
    record["judge_reason"] = reason
    _finish_candidate(metrics, record, tokens_used=estimated)
    _append_audit(record, cfg["audit_file"])
    return record


def _finish_candidate(metrics: Any, record: Dict[str, Any],
                      tokens_used: int = 0) -> None:
    """把逐候选结果写入学习度量（纯观测；异常静默零影响）"""
    try:
        metrics.record_judge_result(
            rule_verdict=record.get("rule_verdict"),
            judge_verdict=record.get("judge_verdict"),
            disagreement=bool(record.get("disagreement")),
            judge_status=record.get("judge_status", STATUS_SKIPPED),
            tokens_used=tokens_used,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[Judge] 度量写入失败（静默）: %s", e)


# ════════════════════════════════════════════════════════════
#  批量评估 + 统计 + 判别
# ════════════════════════════════════════════════════════════

def evaluate_candidates(candidates: List[JudgeCandidate],
                        llm_client: Optional[Any] = None,
                        budget: Any = None,
                        metrics: Any = None,
                        cfg: Optional[Dict[str, Any]] = None,
                        audit_file: Optional[str] = None,
                        ) -> Dict[str, Any]:
    """对同一候选集并行跑规则通道与 Judge 通道（dry-run，零干预）

    Args:
        candidates: 候选集（任务3 observe/confirm 真实候选或合成数据）
        llm_client / budget / metrics / cfg: 透传 evaluate_one
        audit_file: 审计路径覆盖（None → cfg.audit_file；测试注入 tmp 路径用）

    Returns:
        {"batch_id", "records", "stats", "discrimination"}
    """
    cfg = cfg if cfg is not None else load_judge_config()
    if metrics is None:
        from agent.learning_metrics import get_learning_metrics
        metrics = get_learning_metrics()
    # audit_file 覆盖（测试/运维注入 tmp 路径）透传进 cfg，保证 evaluate_one 审计落点一致
    if audit_file:
        cfg = dict(cfg)
        cfg["audit_file"] = audit_file
    batch_id = uuid.uuid4().hex[:12]

    records = [
        evaluate_one(c, llm_client=llm_client, budget=budget, metrics=metrics,
                     cfg=cfg, batch_id=batch_id)
        for c in candidates
    ]
    stats = compute_stats(records)
    discrimination = discriminate(
        judged=stats["judged"],
        disagreement_rate=stats["judge_disagreement_rate"],
        rule_adoption_rate=stats["rule_adoption_rate"],
        judge_implied_adoption_rate=stats["judge_implied_adoption_rate"],
        cfg=cfg,
    )
    result = {
        "batch_id": batch_id,
        "records": records,
        "stats": stats,
        "discrimination": discrimination,
    }
    _sync_gauges(stats, discrimination)
    return result


def compute_stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从逐候选审计记录聚合判别统计（口径与 learning_metrics 快照一致）

    有效判定（judged）= judge_status == "judged"（两通道均产出判定）；
    分歧率 = 分歧数 / judged；规则通道采纳率 = 规则判 accept / judged；
    Judge 通道采纳率（implied）= Judge 判 accept / judged。
    """
    judged = [r for r in records if r.get("judge_status") == STATUS_JUDGED]
    n = len(judged)
    disagreements = sum(1 for r in judged if r.get("disagreement"))
    rule_adopted = sum(1 for r in judged if r.get("rule_verdict") == VERDICT_ACCEPT)
    implied_adopted = sum(1 for r in judged if r.get("judge_verdict") == VERDICT_ACCEPT)
    budget_blocked = sum(1 for r in records
                         if r.get("judge_status") == STATUS_BUDGET_BLOCKED)
    tokens_used = sum(int(r.get("tokens_used") or 0) for r in records)

    def _rate(x: int) -> Optional[float]:
        return round(x / n, 4) if n else None

    rule_rate = _rate(rule_adopted)
    implied_rate = _rate(implied_adopted)
    delta_pp = None
    if n and rule_rate is not None and implied_rate is not None:
        delta_pp = round((implied_rate - rule_rate) * 100.0, 2)
    return {
        "candidates": len(records),
        "judged": n,
        "disagreements": disagreements,
        "rule_adopted": rule_adopted,
        "implied_adopted": implied_adopted,
        "budget_blocked": budget_blocked,
        "tokens_used": tokens_used,
        "judge_disagreement_rate": _rate(disagreements),
        "rule_adoption_rate": rule_rate,
        "judge_implied_adoption_rate": implied_rate,
        "adoption_rate_delta_pp": delta_pp,
    }


def discriminate(judged: int,
                 disagreement_rate: Optional[float],
                 rule_adoption_rate: Optional[float],
                 judge_implied_adoption_rate: Optional[float],
                 cfg: Optional[Dict[str, Any]] = None,
                 ) -> Dict[str, Any]:
    """预设判别规则（纯函数；结论二值化 recommendation + 明确 basis）

    规则（任务提示词 §2.4，写入判别报告 §3）:
        1) judged < min_candidates → insufficient_data → 不启用（继续采集）
        2) 分歧率 < 阈值 → 假设 B（候选质量差）→ 不引入 Judge，转向改进候选生成
        3) 分歧率 ≥ 阈值 且 (implied - rule) × 100 ≥ min_adoption_delta_pp
           → 假设 A（评估不精细）→ 支持引入 Judge，按报告 §3.3 dry-run 2 周流程评估启用
        4) 其余 → inconclusive（分歧高但采纳率差异不足）→ 不启用（继续采集/复核评估 prompt）

    Returns:
        {"conclusion", "recommendation", "reason", "basis": {...}}
        recommendation 恒为 not_introduce / evaluate_introduce（不含模糊结论）。
    """
    cfg = cfg if cfg is not None else load_judge_config()
    min_candidates = max(1, int(cfg.get("min_candidates", DEFAULT_MIN_CANDIDATES)))
    threshold = max(0.0, float(cfg.get("disagreement_threshold",
                                       DEFAULT_DISAGREEMENT_THRESHOLD)))
    delta_pp = max(0.0, float(cfg.get("min_adoption_delta_pp",
                                      DEFAULT_MIN_ADOPTION_DELTA_PP)))

    basis = {
        "judged": judged,
        "min_candidates": min_candidates,
        "disagreement_rate": disagreement_rate,
        "disagreement_threshold": threshold,
        "rule_adoption_rate": rule_adoption_rate,
        "judge_implied_adoption_rate": judge_implied_adoption_rate,
        "adoption_rate_delta_pp": (
            round((judge_implied_adoption_rate - rule_adoption_rate) * 100.0, 2)
            if judged and disagreement_rate is not None
            and judge_implied_adoption_rate is not None
            and rule_adoption_rate is not None else None),
        "min_adoption_delta_pp": delta_pp,
    }

    if judged < min_candidates or disagreement_rate is None \
            or judge_implied_adoption_rate is None or rule_adoption_rate is None:
        return {
            "conclusion": CONCLUSION_INSUFFICIENT_DATA,
            "recommendation": RECOMMEND_NOT_INTRODUCE,
            "reason": (f"有效判定样本 {judged} < 最小判别基数 {min_candidates}"
                       "（或双通道比率不可计算），不足以判别归因；继续采集至"
                       "候选数 ≥ 阈值或 2 周窗口结束"),
            "basis": basis,
        }
    if disagreement_rate < threshold:
        return {
            "conclusion": CONCLUSION_B_CANDIDATE_QUALITY,
            "recommendation": RECOMMEND_NOT_INTRODUCE,
            "reason": (f"分歧率 {disagreement_rate:.2%} < 阈值 {threshold:.0%}："
                       "规则评估与 LLM Judge 高度一致 → 低采纳率主要归因假设 B"
                       "（候选质量差，评估正确拒绝）→ 不引入 Judge，转向改进候选生成"),
            "basis": basis,
        }
    implied_minus_rule_pp = basis["adoption_rate_delta_pp"]
    if implied_minus_rule_pp is not None and implied_minus_rule_pp >= delta_pp:
        return {
            "conclusion": CONCLUSION_A_EVAL_INSUFFICIENT,
            "recommendation": RECOMMEND_EVALUATE_INTRODUCE,
            "reason": (f"分歧率 {disagreement_rate:.2%} ≥ 阈值 {threshold:.0%} 且 "
                       f"按 Judge 判定的采纳率 - 规则通道采纳率 = {implied_minus_rule_pp:+.1f}pp "
                       f"≥ +{delta_pp:.0f}pp：支持假设 A（规则评估不精细，好候选被误拒）"
                       "→ 按报告 §3.3 流程 dry-run 2 周评估启用 LLM-as-Judge"),
            "basis": basis,
        }
    return {
        "conclusion": CONCLUSION_INCONCLUSIVE,
        "recommendation": RECOMMEND_NOT_INTRODUCE,
        "reason": (f"分歧率 {disagreement_rate:.2%} ≥ 阈值 {threshold:.0%} 但采纳率差异 "
                   f"{implied_minus_rule_pp:+.1f}pp < +{delta_pp:.0f}pp：分歧高但 Judge "
                   "未显著提高采纳率，证据不足以支持假设 A → 不启用（继续采集或复核 "
                   "Judge 评估 prompt 后重试）"),
        "basis": basis,
    }


def _sync_gauges(stats: Dict[str, Any], discrimination: Dict[str, Any]) -> None:
    """把判别统计同步到 Prometheus gauge（异常静默零影响）"""
    try:
        from agent.monitoring.learning_judge_metrics import sync_judge_gauges
        sync_judge_gauges(stats, discrimination)
    except Exception:  # noqa: BLE001
        logger.debug("[Judge] gauge 同步失败（静默）")


# ════════════════════════════════════════════════════════════
#  候选数据源（任务3 observe/confirm 真实候选追溯入口）
# ════════════════════════════════════════════════════════════

def load_candidates_from_rollout_audit(
    path: str = "data/learning/rollout_audit.jsonl",
    decisions: Tuple[str, ...] = ("preview", "approved"),
    limit: Optional[int] = None,
) -> List[JudgeCandidate]:
    """从任务3 放行审计（rollout_audit.jsonl）读取真实候选

    只读追溯：decision=preview（observe 预演）与 approved（confirm 提交）的候选
    均可作为双通道评估输入；候选的既有规则结论（rule_verdict）按如下映射
    （只读回放，不重新评估）:
        preview  → None（observe 只记录预演，不产生采纳/拒绝结论；由镜像规则
                   或评分字段补足，缺失则诚实跳过）
        approved → accept（confirm 已通过既有规则/审批门槛）
    文件缺失/损坏 → 返回空列表（零影响）。
    """
    path = Path(path)
    if not path.exists():
        logger.warning("[Judge] 放行审计文件不存在: %s（返回空候选集）", path)
        return []
    candidates: List[JudgeCandidate] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001 单行损坏跳过
                    continue
                decision = str(rec.get("decision") or "")
                if decision not in decisions:
                    continue
                candidates.append(JudgeCandidate(
                    candidate_id=str(rec.get("candidate_id")
                                     or rec.get("object_id") or uuid.uuid4().hex[:12]),
                    source=f"rollout_{decision}",
                    content=str(rec.get("detail") or "")[:4000],
                    rule_verdict=(
                        VERDICT_ACCEPT if decision == "approved" else None),
                    scores={},
                    metadata={
                        "action": rec.get("action"),
                        "object_id": rec.get("object_id"),
                        "regression_result": rec.get("regression_result"),
                        "decision": decision,
                    },
                ))
                if limit is not None and len(candidates) >= limit:
                    break
    except Exception as e:  # noqa: BLE001
        logger.warning("[Judge] 放行审计读取失败（返回已解析部分）: %s", e)
    return candidates


def load_candidates_from_file(path: str) -> List[JudgeCandidate]:
    """从 JSONL 文件加载候选（每行一个 JudgeCandidate dict；测试/运维用）"""
    path = Path(path)
    if not path.exists():
        return []
    out: List[JudgeCandidate] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(JudgeCandidate.from_dict(json.loads(line)))
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return out


# ════════════════════════════════════════════════════════════
#  CLI（status / run-batch / discriminate / report）
# ════════════════════════════════════════════════════════════


def _cli_status(cfg: Dict[str, Any]) -> str:
    from agent.learning_budget import get_learning_budget
    budget = get_learning_budget()
    from agent.learning_metrics import get_learning_metrics
    stats = get_learning_metrics().get_judge_dryrun_stats()
    disc = discriminate(
        judged=stats["judged"],
        disagreement_rate=stats["judge_disagreement_rate"],
        rule_adoption_rate=stats["rule_adoption_rate"],
        judge_implied_adoption_rate=stats["judge_implied_adoption_rate"],
        cfg=cfg,
    )
    return json.dumps({
        "config": cfg,
        "budget": {"mode": budget.mode, "scope": budget.scope,
                   "tripped": budget.get_status().get("tripped")},
        "judge_dryrun_stats": stats,
        "discrimination": disc,
    }, ensure_ascii=False, indent=2)


def _cli_discriminate(cfg: Dict[str, Any]) -> str:
    from agent.learning_metrics import get_learning_metrics
    stats = get_learning_metrics().get_judge_dryrun_stats()
    disc = discriminate(
        judged=stats["judged"],
        disagreement_rate=stats["judge_disagreement_rate"],
        rule_adoption_rate=stats["rule_adoption_rate"],
        judge_implied_adoption_rate=stats["judge_implied_adoption_rate"],
        cfg=cfg,
    )
    return json.dumps(disc, ensure_ascii=False, indent=2)


def _cli_report(cfg: Dict[str, Any]) -> str:
    from agent.learning_metrics import get_learning_metrics
    stats = get_learning_metrics().get_judge_dryrun_stats()
    disc = discriminate(
        judged=stats["judged"],
        disagreement_rate=stats["judge_disagreement_rate"],
        rule_adoption_rate=stats["rule_adoption_rate"],
        judge_implied_adoption_rate=stats["judge_implied_adoption_rate"],
        cfg=cfg,
    )
    lines = [
        "# LLM-as-Judge 双假设判别报告（数据快照）",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 采集周期（周）: {cfg.get('collection_weeks', DEFAULT_COLLECTION_WEEKS)}",
        f"- 最小判别基数: {cfg.get('min_candidates', DEFAULT_MIN_CANDIDATES)}",
        f"- 样本量（候选/有效判定）: {stats['candidates']} / {stats['judged']}",
        f"- 分歧率: {stats['judge_disagreement_rate']}",
        f"- 规则通道采纳率: {stats['rule_adoption_rate']}",
        f"- Judge 通道采纳率（implied）: {stats['judge_implied_adoption_rate']}",
        f"- 采纳率差异（pp）: {stats['adoption_rate_delta_pp']}",
        f"- 预算熔断跳过: {stats['budget_blocked']}",
        f"- token 成本（预估）: {stats['tokens_used']}",
        f"- 判别结论: {disc['conclusion']}",
        f"- 建议: {disc['recommendation']}",
        f"- 依据: {disc['reason']}",
    ]
    return "\n".join(lines)


def _cli_run_batch(cfg: Dict[str, Any], source: str,
                   candidates_file: Optional[str]) -> str:
    if source == "rollout_audit":
        candidates = load_candidates_from_rollout_audit()
    elif source == "file" and candidates_file:
        candidates = load_candidates_from_file(candidates_file)
    else:
        return json.dumps({"error": "unknown --source（rollout_audit|file）或缺少 --candidates-file"},
                          ensure_ascii=False)
    result = evaluate_candidates(candidates, cfg=cfg)
    summary = {k: result[k] for k in ("batch_id", "stats", "discrimination")}
    return json.dumps(summary, ensure_ascii=False, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agent.learning.judge_channel",
        description="LLM-as-Judge 双假设验证 · Judge dry-run 通道",
    )
    parser.add_argument("--status", action="store_true", help="通道状态 + 判别统计")
    parser.add_argument("--run-batch", action="store_true",
                        help="执行一批双通道 dry-run 评估")
    parser.add_argument("--source", default="rollout_audit",
                        choices=("rollout_audit", "file"),
                        help="候选来源（默认 rollout_audit=任务3 放行审计）")
    parser.add_argument("--candidates-file", default=None,
                        help="--source file 时的候选 JSONL 路径")
    parser.add_argument("--discriminate", action="store_true",
                        help="按当前采集数据计算判别结论")
    parser.add_argument("--report", action="store_true",
                        help="输出判别报告数据快照")
    args = parser.parse_args(argv)

    cfg = load_judge_config()
    if args.run_batch:
        print(_cli_run_batch(cfg, args.source, args.candidates_file))
    elif args.discriminate:
        print(_cli_discriminate(cfg))
    elif args.report:
        print(_cli_report(cfg))
    else:
        print(_cli_status(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "JudgeCandidate",
    "VERDICT_ACCEPT",
    "VERDICT_REJECT",
    "STATUS_JUDGED",
    "STATUS_SKIPPED",
    "STATUS_BUDGET_BLOCKED",
    "STATUS_BUDGET_NOT_ENFORCE",
    "CONCLUSION_A_EVAL_INSUFFICIENT",
    "CONCLUSION_B_CANDIDATE_QUALITY",
    "CONCLUSION_INSUFFICIENT_DATA",
    "CONCLUSION_INCONCLUSIVE",
    "RECOMMEND_NOT_INTRODUCE",
    "RECOMMEND_EVALUATE_INTRODUCE",
    "load_judge_config",
    "rule_verdict_mirror",
    "evaluate_one",
    "evaluate_candidates",
    "compute_stats",
    "discriminate",
    "load_candidates_from_rollout_audit",
    "load_candidates_from_file",
]
