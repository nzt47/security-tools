"""委派回收三件套（v7.2 §3.9）

【不易（§3.9 逐字）】
    「回收三件套：产物 + 轨迹 + 反思（上游自评 + 云枢复评）。**缺任一 → 不计成本
    核算、视为浪费、阻塞 stage 推进**。」

    这句话在实现上拆成三条**机器可判定**的语义，本模块逐条落地：

    1. **齐全判定**：``CollectedTriad.is_complete`` —— 三件套缺任一即为 False。
       轨迹另有两项附加缺陷（``actor != sub_agent`` / ``parent_trace_id`` 为空）也
       计入不齐全：§3.9 要求轨迹是「本次委派的子 Trace」，一条没有父链或执行体
       标错的轨迹不构成可回收的证据，拿它凑数等于把「有轨迹」冒充成「有证据」。
    2. **不计成本核算**：``CostLedger.account()`` 在不齐全时把本次消耗记入
       ``wasted_*`` 而不是 ``counted_*``——浪费**可见**（否则成本凭空消失，问题被
       掩盖），但**不进入**成本核算口径。
    3. **阻塞 stage 推进**：``StageGate`` 在存在不齐全委派时拒绝放行（抛
       ``StageBlocked``），与 S3 消化流水线的 stage 迁移证据链一致。

【依赖纪律】
    标准库 + 可选惰性导入 ``agent.cognitive.reflection``（既有反思评估器，§3.9
    「复用既有反思评估器或 LLM judge」）。惰性 + 失败回退，保证本模块导入**不**
    连带拉起认知子系统，也保证评估器不可用时仍产出确定性的云枢复评。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.security.actor_matrix import ACTOR_SUB_AGENT

logger = logging.getLogger(__name__)

# ── 三件套部件名（唯一清单）──

TRIAD_ARTIFACTS = "artifacts"
TRIAD_TRACE = "trace"
TRIAD_REFLECTION = "reflection"

#: 三件套（文档顺序即缺失报告顺序）
TRIAD_PARTS: Tuple[str, ...] = (TRIAD_ARTIFACTS, TRIAD_TRACE, TRIAD_REFLECTION)

#: 错误码
E_WASTED_DELEGATION = "E_WASTED_DELEGATION"
E_STAGE_BLOCKED = "E_STAGE_BLOCKED"

#: 反思的两半（§3.9「上游自评 + 云枢复评」）
REFLECTION_UPSTREAM = "upstream_self_eval"
REFLECTION_CLOUD = "cloudpivot_review"

#: 上游载荷里承载自评的候选键（上游写法不完全统一，按序取第一个命中）
_UPSTREAM_SELF_EVAL_KEYS: Tuple[str, ...] = (
    "self_eval", "self_evaluation", "self_reflection", "reflection",
    "upstream_reflection", "self_review",
)


# ════════════════════════════════════════════════════════════
#  反思两半
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class UpstreamSelfEval:
    """上游自评（三件套·反思的第一半）

    子代理**自己**给出的评估。缺这一半 = 反思不完整（§3.9 明确要求两半）。
    """

    verdict: str = "unstated"      # pass / partial / fail / unstated
    score: float = -1.0            # <0 表示未给出量化分数
    summary: str = ""
    issues: Tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "score": round(float(self.score), 4),
            "summary": self.summary,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class CloudPivotReview:
    """云枢复评（三件套·反思的第二半）——由云枢侧评估器产出，**不是**上游自述"""

    verdict: str = "fail"          # pass / partial / fail
    score: float = 0.0
    passed: bool = False
    issues: Tuple[str, ...] = ()
    suggestions: Tuple[str, ...] = ()
    reviewer: str = "reflection_engine"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "score": round(float(self.score), 4),
            "passed": bool(self.passed),
            "issues": list(self.issues),
            "suggestions": list(self.suggestions),
            "reviewer": self.reviewer,
        }


@dataclass(frozen=True)
class Reflection:
    """反思 = 上游自评 + 云枢复评（两半齐备才计入三件套）"""

    upstream_self_eval: Optional[UpstreamSelfEval] = None
    cloudpivot_review: Optional[CloudPivotReview] = None

    @property
    def is_complete(self) -> bool:
        return self.upstream_self_eval is not None and self.cloudpivot_review is not None

    @property
    def missing_halves(self) -> Tuple[str, ...]:
        out: List[str] = []
        if self.upstream_self_eval is None:
            out.append(REFLECTION_UPSTREAM)
        if self.cloudpivot_review is None:
            out.append(REFLECTION_CLOUD)
        return tuple(out)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "complete": self.is_complete,
            "missing_halves": list(self.missing_halves),
            REFLECTION_UPSTREAM: (self.upstream_self_eval.to_dict()
                                  if self.upstream_self_eval else None),
            REFLECTION_CLOUD: (self.cloudpivot_review.to_dict()
                               if self.cloudpivot_review else None),
        }


def _coerce_score(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return -1.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return -1.0


def _coerce_str_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(v) for v in value if str(v).strip())
    return (str(value),)


def build_upstream_self_eval(payload: Any) -> Optional[UpstreamSelfEval]:
    """从上游载荷中提取自评；**未提供 → None**（不臆造「自评通过」）

    支持两形态：结构化字典（``verdict``/``score``/``summary``/``issues``）与纯文本
    （只有 summary，``verdict="unstated"``）。
    """
    if not isinstance(payload, Mapping):
        return None
    raw: Any = None
    for key in _UPSTREAM_SELF_EVAL_KEYS:
        if key in payload and payload.get(key) not in (None, "", [], {}):
            raw = payload.get(key)
            break
    if raw is None:
        return None
    if isinstance(raw, str):
        return UpstreamSelfEval(verdict="unstated", score=-1.0,
                                summary=raw.strip()[:2000], raw={"text": raw})
    if not isinstance(raw, Mapping):
        return UpstreamSelfEval(verdict="unstated", score=-1.0,
                                summary=str(raw)[:2000], raw={"value": str(raw)})
    verdict = str(raw.get("verdict") or raw.get("status") or "unstated").strip().lower()
    score = _coerce_score(raw.get("score"))
    summary = str(raw.get("summary") or raw.get("note") or raw.get("text") or "")[:2000]
    issues = _coerce_str_tuple(raw.get("issues") or raw.get("problems"))
    if score < 0 and verdict in ("pass", "passed", "ok", "success"):
        score = 1.0
    elif score < 0 and verdict in ("fail", "failed", "error"):
        score = 0.0
    return UpstreamSelfEval(verdict=verdict or "unstated", score=score,
                            summary=summary, issues=issues,
                            raw={str(k): raw[k] for k in raw})


def _verdict_from_score(score: float, passed: bool) -> str:
    if passed:
        return "pass"
    return "partial" if score >= 0.3 else "fail"


def reflection_engine_reviewer(*, task_id: str, input_text: str, output: str,
                               execution_time_ms: float = 0.0,
                               tool_calls: Sequence[Mapping[str, Any]] = (),
                               ) -> CloudPivotReview:
    """云枢复评（默认实现）：**复用既有 ``ReflectionEngine``**（§3.9 指定的复用点）

    惰性导入 + 失败回退到 ``rule_reviewer``：认知子系统不可用不得阻断回收路径。
    """
    try:
        from agent.cognitive.reflection import ReflectionEngine  # 惰性：勿连带拉起认知子系统
    except Exception as e:  # noqa: BLE001  回退铁律
        logger.warning("[Collect] ReflectionEngine 不可用（%s），回退规则复评", e)
        return rule_reviewer(task_id=task_id, input_text=input_text, output=output,
                             execution_time_ms=execution_time_ms,
                             tool_calls=tool_calls)
    try:
        result = ReflectionEngine().evaluate(
            task_id, input_text, output, execution_time_ms, list(tool_calls))
    except Exception as e:  # noqa: BLE001  回退铁律
        logger.warning("[Collect] ReflectionEngine 评估异常（%s），回退规则复评", e)
        return rule_reviewer(task_id=task_id, input_text=input_text, output=output,
                             execution_time_ms=execution_time_ms,
                             tool_calls=tool_calls)
    score = float(getattr(result, "score", 0.0))
    passed = bool(getattr(result, "passed", False))
    return CloudPivotReview(
        verdict=_verdict_from_score(score, passed), score=score, passed=passed,
        issues=_coerce_str_tuple(getattr(result, "issues", ())),
        suggestions=_coerce_str_tuple(getattr(result, "suggestions", ())),
        reviewer="reflection_engine")


def rule_reviewer(*, task_id: str, input_text: str, output: str,
                  execution_time_ms: float = 0.0,
                  tool_calls: Sequence[Mapping[str, Any]] = (),
                  ) -> CloudPivotReview:
    """云枢复评（内置确定性实现）：云枢自有规则，零 Token、零外部依赖

    与 ``ReflectionEngine`` 同量级但不依赖认知子系统；作为回退与单测基准。
    """
    issues: List[str] = []
    score = 1.0
    text = str(output or "")
    if not text.strip():
        issues.append("产物为空")
        score -= 0.5
    if len(str(input_text or "")) > 50 and len(text.strip()) < 10:
        issues.append("产物过短，可能未完成目标")
        score -= 0.2
    if "error" in text.lower() or "失败" in text:
        issues.append("产物含错误标记")
        score -= 0.3
    score = max(0.0, min(1.0, score))
    passed = score >= 0.6
    return CloudPivotReview(verdict=_verdict_from_score(score, passed), score=score,
                            passed=passed, issues=tuple(issues),
                            reviewer="rule_reviewer")


#: 复评器签名（可注入）
CloudReviewer = Callable[..., CloudPivotReview]


# ════════════════════════════════════════════════════════════
#  三件套
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CollectedTriad:
    """回收三件套（产物 + 轨迹 + 反思）

    Attributes:
        delegation_id: 委派标识（计费与审计的关联键）。
        artifacts: 产物条目（空元组 = 缺失）。
        trace: 轨迹（映射形式；**必须有 trace_id** 才算存在）。
        reflection: 反思（两半齐备才算存在）。
    """

    delegation_id: str = ""
    artifacts: Tuple[Dict[str, Any], ...] = ()
    trace: Optional[Mapping[str, Any]] = None
    reflection: Optional[Reflection] = None

    # ── 判定 ──

    @property
    def trace_problems(self) -> Tuple[str, ...]:
        """轨迹的附加缺陷（actor 标注 / 父链缺失）——与「轨迹存在」是两件事"""
        problems: List[str] = []
        if not self.trace:
            return ()
        trace_id = str(self.trace.get("trace_id") or "").strip()
        if not trace_id:
            return ()
        actor = str(self.trace.get("actor") or "").strip()
        if actor != ACTOR_SUB_AGENT:
            problems.append(f"actor 应为 {ACTOR_SUB_AGENT}，实际 {actor or '未声明'}")
        if not str(self.trace.get("parent_trace_id") or "").strip():
            problems.append("parent_trace_id 为空（委派子 Trace 必须挂在编排任务下）")
        return tuple(problems)

    @property
    def missing_parts(self) -> Tuple[str, ...]:
        """缺失的三件套部件（``TRIAD_PARTS`` 顺序）"""
        out: List[str] = []
        if not self.artifacts:
            out.append(TRIAD_ARTIFACTS)
        if not (isinstance(self.trace, Mapping) and str(self.trace.get("trace_id") or "").strip()):
            out.append(TRIAD_TRACE)
        if self.reflection is None or not self.reflection.is_complete:
            out.append(TRIAD_REFLECTION)
        return tuple(out)

    @property
    def is_complete(self) -> bool:
        """三件套齐全（缺任一部件 / 轨迹有附加缺陷 → False）"""
        return not self.missing_parts and not self.trace_problems

    @property
    def is_wasted(self) -> bool:
        """是否应视为浪费（= 不齐全）；语义别名，便于调用点自解释"""
        return not self.is_complete

    @property
    def trace_id(self) -> str:
        if isinstance(self.trace, Mapping):
            return str(self.trace.get("trace_id") or "")
        return ""

    def missing_detail(self) -> Dict[str, Any]:
        """缺失明细（拒绝原因必须点名缺哪一件）"""
        detail: Dict[str, Any] = {
            "missing_parts": list(self.missing_parts),
            "trace_problems": list(self.trace_problems),
        }
        if self.reflection is not None and not self.reflection.is_complete:
            detail["reflection_missing_halves"] = list(self.reflection.missing_halves)
        return detail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "complete": self.is_complete,
            "wasted": self.is_wasted,
            "part_count": len(TRIAD_PARTS) - len(self.missing_parts),
            "artifact_count": len(self.artifacts),
            "trace_id": self.trace_id,
            "missing": self.missing_detail(),
            "reflection": (self.reflection.to_dict() if self.reflection else None),
        }


def collect(agent_id: str = "", *,
            artifacts: Optional[Iterable[Any]] = None,
            trace: Any = None,
            reflection: Optional[Reflection] = None,
            ) -> CollectedTriad:
    """收集三件套（**收集不判定**；判定在 ``CollectedTriad`` 上）

    Args:
        agent_id: 委派标识（子代理 id / delegation_id）。
        artifacts: 产物条目（None / 空 → 该件缺失）。
        trace: 轨迹（``UnifiedTrace`` / ``to_dict()`` 对象 / ``Mapping``）。
        reflection: 反思。
    """
    return CollectedTriad(
        delegation_id=str(agent_id or ""),
        artifacts=_coerce_artifacts(artifacts),
        trace=_coerce_trace(trace),
        reflection=reflection,
    )


def _coerce_artifacts(artifacts: Optional[Iterable[Any]]) -> Tuple[Dict[str, Any], ...]:
    if artifacts is None:
        return ()
    out: List[Dict[str, Any]] = []
    for item in artifacts:
        if isinstance(item, Mapping):
            out.append({str(k): item[k] for k in item})
        else:
            text = str(item)
            if text.strip():
                out.append({"value": text})
    return tuple(out)


def _coerce_trace(trace: Any) -> Optional[Mapping[str, Any]]:
    """轨迹统一为映射（``UnifiedTrace`` 经 ``to_dict()``；其余按鸭子类型）"""
    if trace is None:
        return None
    if isinstance(trace, Mapping):
        return {str(k): trace[k] for k in trace}
    to_dict = getattr(trace, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return {str(k): data[k] for k in data}
    logger.warning("[Collect] 无法识别的轨迹类型 %s —— 判为缺失", type(trace).__name__)
    return None


# ════════════════════════════════════════════════════════════
#  收集器（从执行结果 → 三件套）
# ════════════════════════════════════════════════════════════


class TriadCollector:
    """三件套收集器：把一次委派执行结果收敛为 ``CollectedTriad``

    职责边界：**只收集、不判定、不计费、不放行**（判定/计费/放行分别由
    ``CollectedTriad`` / ``CostLedger`` / ``StageGate`` 承担，便于分别断言）。
    """

    def __init__(self, *, reviewer: Optional[CloudReviewer] = None,
                 trace_lookup: Optional[Callable[[str], Any]] = None) -> None:
        """
        Args:
            reviewer: 云枢复评器（缺省 ``reflection_engine_reviewer``）。
            trace_lookup: ``trace_id -> trace`` 查询函数（通常为
                ``TraceFacade`` 的封装；缺省从执行结果的 ``trace`` 字段取）。
        """
        self._reviewer: CloudReviewer = reviewer or reflection_engine_reviewer
        self._trace_lookup = trace_lookup

    # ── 主入口 ──

    def collect_from_outcome(self, outcome: Any, *,
                             upstream_payload: Optional[Mapping[str, Any]] = None,
                             reflection: Optional[Reflection] = None,
                             input_text: str = "",
                             ) -> CollectedTriad:
        """从执行结果（``ExecutionOutcome`` / 任意鸭子类型）收集三件套

        读取的字段（全部 ``getattr`` 软取，缺省即视为缺失）：
        ``delegation_id`` / ``artifacts`` / ``payload`` / ``trace`` / ``trace_id`` /
        ``output_text`` / ``duration_ms`` / ``tool_calls``。

        Args:
            outcome: 执行结果。
            upstream_payload: 覆盖上游载荷（缺省取 ``outcome.payload``）。
            reflection: 显式指定反思（缺省由上游自评 + 云枢复评**现场合成**）。
            input_text: 委派目标文本（供复评的「输入长度 vs 输出长度」维度）。
        """
        delegation_id = str(getattr(outcome, "delegation_id", "") or "")
        payload = upstream_payload
        if payload is None:
            candidate = getattr(outcome, "payload", None)
            payload = candidate if isinstance(candidate, Mapping) else {}

        artifacts = getattr(outcome, "artifacts", None)
        if artifacts is None:
            artifacts = payload.get("artifacts") if isinstance(payload, Mapping) else None

        trace = getattr(outcome, "trace", None)
        if trace is None:
            trace_id = str(getattr(outcome, "trace_id", "") or "")
            if trace_id and self._trace_lookup is not None:
                trace = self._trace_lookup(trace_id)

        if reflection is None:
            reflection = self.build_reflection(
                payload=payload,
                task_id=delegation_id or str(getattr(outcome, "trace_id", "") or ""),
                output=str(getattr(outcome, "output_text", "") or ""),
                input_text=input_text,
                execution_time_ms=float(getattr(outcome, "duration_ms", 0.0) or 0.0),
                tool_calls=getattr(outcome, "tool_calls", ()) or (),
            )

        return collect(delegation_id,
                       artifacts=artifacts if not isinstance(artifacts, Mapping) else [artifacts],
                       trace=trace,
                       reflection=reflection)

    # ── 反思合成 ──

    def build_reflection(self, *, payload: Any, task_id: str = "", output: str = "",
                         input_text: str = "", execution_time_ms: float = 0.0,
                         tool_calls: Sequence[Mapping[str, Any]] = (),
                         ) -> Reflection:
        """合成反思 = 上游自评（取自载荷，**缺失即 None**）+ 云枢复评（云枢侧产出）"""
        upstream = build_upstream_self_eval(payload)
        try:
            cloud = self._reviewer(
                task_id=task_id, input_text=input_text, output=output,
                execution_time_ms=execution_time_ms, tool_calls=tool_calls)
        except Exception as e:  # noqa: BLE001  复评失败不得阻断收集；记 None 即「缺这一半」
            logger.warning("[Collect] 云枢复评失败（%s）——反思记为不完整", e)
            cloud = None
        return Reflection(upstream_self_eval=upstream, cloudpivot_review=cloud)


def build_trace_lookup(facade: Any) -> Callable[[str], Any]:
    """由 ``TraceFacade`` 构造 ``trace_id -> UnifiedTrace`` 查询函数

    轨 ``query`` 无 ``trace_id`` 参数（按 capability/task/parent 过滤），故用
    ``chain(trace_id)`` 取该 id 自身的链节点——链首即目标 Trace；找不到返回 None
    （**不臆造轨迹**）。
    """
    def _lookup(trace_id: str) -> Any:
        target = str(trace_id or "").strip()
        if not target or facade is None:
            return None
        try:
            chain = facade.chain(target)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Collect] 轨迹查询失败 %s: %s", target, e)
            return None
        for item in chain or ():
            if str(getattr(item, "trace_id", "") or "") == target:
                return item
        return None
    return _lookup


# ════════════════════════════════════════════════════════════
#  成本核算（缺三件套 → 不计核算，只记浪费）
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CostRecord:
    """一次委派的成本记账（``counted=False`` 时消耗全部进 ``wasted_*``）"""

    delegation_id: str
    counted: bool
    wasted: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    counted_tokens: int = 0
    counted_cost_usd: float = 0.0
    reason: str = ""
    missing_parts: Tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "counted": bool(self.counted),
            "wasted": bool(self.wasted),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "total_tokens": self.total_tokens,
            "cost_usd": round(float(self.cost_usd), 6),
            "counted_tokens": int(self.counted_tokens),
            "counted_cost_usd": round(float(self.counted_cost_usd), 6),
            "reason": self.reason,
            "missing_parts": list(self.missing_parts),
        }


class CostLedger:
    """委派成本台账（§3.9「缺任一 → 不计成本核算、视为浪费」）

    两栏分离：``counted_*``（进入成本核算）与 ``wasted_*``（不进入，但**可见**）。
    合并两者会让浪费消失在总数里；只留 counted 又看不到浪费规模。
    """

    def __init__(self, *, audit: Any = None) -> None:
        self._records: List[CostRecord] = []
        self._lock = threading.RLock()
        self._audit = audit

    def account(self, triad: CollectedTriad, *,
                input_tokens: int = 0,
                output_tokens: int = 0,
                cost_usd: float = 0.0,
                ) -> CostRecord:
        """记账：三件套齐全 → 计入核算；否则标记浪费、不计入"""
        complete = triad.is_complete
        missing = triad.missing_parts
        if complete:
            record = CostRecord(
                delegation_id=triad.delegation_id, counted=True, wasted=False,
                input_tokens=int(input_tokens), output_tokens=int(output_tokens),
                cost_usd=float(cost_usd), counted_tokens=int(input_tokens) + int(output_tokens),
                counted_cost_usd=float(cost_usd), reason="三件套齐全",
                missing_parts=())
        else:
            detail = triad.missing_detail()
            why = "缺：" + "、".join(detail["missing_parts"]) if detail["missing_parts"] else ""
            if detail["trace_problems"]:
                why = (why + "；" if why else "") + "轨迹缺陷：" + "、".join(detail["trace_problems"])
            record = CostRecord(
                delegation_id=triad.delegation_id, counted=False, wasted=True,
                input_tokens=int(input_tokens), output_tokens=int(output_tokens),
                cost_usd=float(cost_usd), counted_tokens=0, counted_cost_usd=0.0,
                reason=f"{E_WASTED_DELEGATION}——{why or '三件套不齐全'}", missing_parts=missing)
        with self._lock:
            self._records.append(record)
        self._emit(record)
        return record

    def _emit(self, record: CostRecord) -> None:
        """审计（best-effort：审计失败不得阻断成本记账主路径）"""
        if self._audit is None:
            return
        try:
            self._audit.record(
                "subagent.delegation.cost", actor=ACTOR_SUB_AGENT,
                subject=f"delegation:{record.delegation_id}",
                payload=record.to_dict(),
                status="counted" if record.counted else "wasted")
        except Exception as e:  # noqa: BLE001
            logger.warning("[Collect] 成本审计写入失败: %s", e)

    # ── 视图 ──

    def records(self) -> Tuple[CostRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def totals(self) -> Dict[str, Any]:
        """台账合计（counted / wasted 两栏分离）"""
        with self._lock:
            records = list(self._records)
        counted = [r for r in records if r.counted]
        wasted = [r for r in records if r.wasted]
        return {
            "delegations": len(records),
            "counted_delegations": len(counted),
            "wasted_delegations": len(wasted),
            "counted_tokens": sum(r.counted_tokens for r in counted),
            "counted_cost_usd": round(sum(r.counted_cost_usd for r in counted), 6),
            "wasted_tokens": sum(r.total_tokens for r in wasted),
            "wasted_cost_usd": round(sum(r.cost_usd for r in wasted), 6),
            "waste_rate": round(len(wasted) / len(records), 4) if records else 0.0,
        }

    def wasted(self) -> Tuple[CostRecord, ...]:
        with self._lock:
            return tuple(r for r in self._records if r.wasted)


# ════════════════════════════════════════════════════════════
#  stage 闸门（缺三件套 → 阻塞推进）
# ════════════════════════════════════════════════════════════


class StageBlocked(Exception):
    """stage 推进被阻塞（存在三件套不齐全的委派）

    Attributes:
        stage: 目标 stage。
        blockers: 阻塞项明细（delegation_id + 缺失部件）。
    """

    code = E_STAGE_BLOCKED

    def __init__(self, stage: str, blockers: Sequence[Mapping[str, Any]]) -> None:
        self.stage = str(stage or "")
        self.blockers = tuple(dict(b) for b in blockers)
        detail = "；".join(
            f"{b.get('delegation_id') or '<未知委派>'} 缺 {', '.join(b.get('missing_parts') or [])}"
            + (f"（轨迹缺陷：{'; '.join(b.get('trace_problems') or [])}）"
               if b.get("trace_problems") else "")
            for b in self.blockers)
        super().__init__(
            f"{E_STAGE_BLOCKED}: stage {self.stage or '<未命名>'} 被 {len(self.blockers)} "
            f"个不齐全委派阻塞——§3.9「缺任一 → 阻塞 stage 推进」：{detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "stage": self.stage,
                "blockers": [dict(b) for b in self.blockers]}


def blocks_stage(triad: CollectedTriad) -> bool:
    """单件是否阻塞 stage 推进（= 三件套不齐全）"""
    return not triad.is_complete


@dataclass(frozen=True)
class StageGateDecision:
    """stage 放行判定结果"""

    stage: str
    can_advance: bool
    blockers: Tuple[Dict[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "can_advance": self.can_advance,
                "blocker_count": len(self.blockers),
                "blockers": [dict(b) for b in self.blockers]}


class StageGate:
    """stage 推进闸门（与 S3 消化流水线的 stage 迁移证据链一致）

    用法::

        gate = StageGate("internalized")
        gate.require(triads)      # 不齐全 → 抛 StageBlocked
        ...                       # 放行
    """

    def __init__(self, stage: str = "") -> None:
        self._stage = str(stage or "")

    @property
    def stage(self) -> str:
        return self._stage

    def evaluate(self, triads: Iterable[CollectedTriad]) -> StageGateDecision:
        """判定是否可以推进（不抛异常）"""
        blockers: List[Dict[str, Any]] = []
        for triad in triads:
            if triad.is_complete:
                continue
            detail = triad.missing_detail()
            blockers.append({
                "delegation_id": triad.delegation_id,
                "missing_parts": detail["missing_parts"],
                "trace_problems": detail["trace_problems"],
                **({"reflection_missing_halves": detail["reflection_missing_halves"]}
                   if "reflection_missing_halves" in detail else {}),
            })
        return StageGateDecision(stage=self._stage, can_advance=not blockers,
                                 blockers=tuple(blockers))

    def require(self, triads: Iterable[CollectedTriad]) -> StageGateDecision:
        """判定并断言放行；被阻塞时抛 ``StageBlocked``（点名缺哪一件）"""
        decision = self.evaluate(triads)
        if not decision.can_advance:
            raise StageBlocked(self._stage, list(decision.blockers))
        return decision


__all__ = [
    # 部件
    "TRIAD_ARTIFACTS", "TRIAD_TRACE", "TRIAD_REFLECTION", "TRIAD_PARTS",
    "REFLECTION_UPSTREAM", "REFLECTION_CLOUD",
    "E_WASTED_DELEGATION", "E_STAGE_BLOCKED",
    # 反思
    "UpstreamSelfEval", "CloudPivotReview", "Reflection",
    "build_upstream_self_eval", "reflection_engine_reviewer", "rule_reviewer",
    "CloudReviewer",
    # 三件套
    "CollectedTriad", "collect", "TriadCollector", "build_trace_lookup",
    # 成本
    "CostRecord", "CostLedger",
    # stage
    "StageBlocked", "StageGate", "StageGateDecision", "blocks_stage",
]
