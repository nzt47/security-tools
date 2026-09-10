"""ACR（介入率北极星）埋点与汇总视图（TASK-S2-03 / v7.2 §6.1–6.6 / P7.1-16）

本模块把 v7.2 §6.1 的 **ACR 介入计数口径**落地为可复现的埋点 + 汇总函数，
数据源统一为 `agent.observability.events` 的 events.v1 事件流。

§6.1 计数口径（**逐项硬编码，可单测断言**）：

======================  ======  ================================================
介入类型 (kind)          权重    触发点（云枢真实发生点）
======================  ======  ================================================
``approve``             1.0     审批通过（`ApprovalFlow.approve`）
``conditional``         0.5     条件通过（带 note/约束的通过、L2 人工执行标记）
``auto_pass``           0.0     L0 自动放行 / `APPROVAL_ENABLED=0` 直接放行
``timeout_deny``        2.0     超时未审批 → 拒绝（`ApprovalFlow.expire_pending`）
``rerun``               1.0     人工重跑任务（`TaskScheduler.execute_now`）
``escape``              1.0     逃逸：绕过治理路径（手工改受管任务文件等）
``view``                0.0     人工查看待审批项（`list_pending_approvals`）
======================  ======  ================================================

**分母规则（§6.1）**：``分母 = closed(成功) + failed``；``explore`` / ``consult``
两类意图的**任务整体排除**在分母之外（其介入同时从分子排除，避免比例失真），
改为**单列探索满意度**（P7.1-16）。``abandoned``（§3.8 任务生命周期）不计入分母，
单列披露。

**ACR 公式**：``ACR = Σ 介入权重 / 分母``，其中分子**只累加上表 §6.1 七项口径**
（`CORE_INTERVENTION_WEIGHTS`）且排除 explore/consult 任务的介入；无法归属任务的
介入单列 ``unattributed_weight``；``reject``（人工驳回）等 §6.1 未列出的介入
只进 ``interventions.extension_weight`` 单列，**不污染 §6.1 口径**。

**诚实口径（对齐审计报告 T5/T6）**：意图/难度分类是**启发式**，早期（W1–W9）
只披露不考核；难度起步**等权**，不做未经数据拟合的加权。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.observability import events as ev
from agent.observability.events import (
    ACTOR_AUTO,
    ACTOR_HUMAN,
    EV_APPROVAL,
    EV_COST,
    EV_ESCAPE,
    EV_INTERVENTION,
    EV_TASK_ABANDONED,
    EV_TASK_CLOSED,
    EventEnvelope,
    EventStore,
    emit,
    iter_events,
    shift_day,
)

logger = logging.getLogger("agent.observability.acr")

# ════════════════════════════════════════════════════════════
#  口径常量（§6.1）
# ════════════════════════════════════════════════════════════

KIND_APPROVE = "approve"
KIND_CONDITIONAL = "conditional"
KIND_AUTO_PASS = "auto_pass"
KIND_TIMEOUT_DENY = "timeout_deny"
KIND_RERUN = "rerun"
KIND_ESCAPE = "escape"
KIND_VIEW = "view"
#: §6.1 口径之外、但云枢治理面真实存在的介入（**不进入 ACR 分子**，单列披露）
KIND_REJECT = "reject"

#: §6.1 介入权重（Approve=1 / 条件=0.5 / 自动放行=0 / 超时 Deny=2 / 重跑=1 / 逃逸=1 / 查看=0）
#: ——这七项即 ACR 分子的**唯一**口径来源（`acr_numerator` 只累加这七类）。
CORE_INTERVENTION_WEIGHTS: Dict[str, float] = {
    KIND_APPROVE: 1.0,
    KIND_CONDITIONAL: 0.5,
    KIND_AUTO_PASS: 0.0,
    KIND_TIMEOUT_DENY: 2.0,
    KIND_RERUN: 1.0,
    KIND_ESCAPE: 1.0,
    KIND_VIEW: 0.0,
}

#: §6.1 之外的扩展介入（投票口径同「一次人工处置 = 1 单位注意力」）：
#: 人工驳回同样是介入，但它不在 §6.1 七项内，故**不计入 ACR 分子**，
#: 只在 `interventions.extension_*` 单列（既不丢信号，也不篡改 §6.1 口径）。
EXTENDED_INTERVENTION_WEIGHTS: Dict[str, float] = {
    KIND_REJECT: 1.0,
}

#: 全量权重表（查表/落盘用）= §6.1 七项 + 扩展项（§6.1 七项取值逐字不变）
INTERVENTION_WEIGHTS: Dict[str, float] = {
    **CORE_INTERVENTION_WEIGHTS, **EXTENDED_INTERVENTION_WEIGHTS,
}

#: 任务意图（explore/consult 为**分母排除**的两类，另计探索满意度）
INTENT_EXPLORE = "explore"
INTENT_CONSULT = "consult"
INTENT_FIX = "fix"
INTENT_BUILD = "build"
INTENT_OPERATE = "operate"
INTENT_OTHER = "other"
INTENTS = (INTENT_EXPLORE, INTENT_CONSULT, INTENT_FIX, INTENT_BUILD,
           INTENT_OPERATE, INTENT_OTHER)

#: 分母排除的意图（§6.1）
EXCLUDED_INTENTS = (INTENT_EXPLORE, INTENT_CONSULT)

#: 任务关闭状态（§3.8 生命周期：closed / failed / abandoned）
STATUS_CLOSED = "closed"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"
TASK_STATUSES = (STATUS_CLOSED, STATUS_FAILED, STATUS_ABANDONED)
#: 计入 ACR 分母的状态（§6.1：closed(成功) + failed）
DENOMINATOR_STATUSES = (STATUS_CLOSED, STATUS_FAILED)

DIFFICULTY_EASY = "easy"
DIFFICULTY_MEDIUM = "medium"
DIFFICULTY_HARD = "hard"
DIFFICULTIES = (DIFFICULTY_EASY, DIFFICULTY_MEDIUM, DIFFICULTY_HARD)

#: 审批疲劳分桶（§6.1 审批衰减率：审批迟滞 → 疲劳度）
FATIGUE_BUCKETS: Tuple[Tuple[str, float], ...] = (
    ("instant", 10_000.0),        # < 10s：秒批（可能是走过场）
    ("quick", 60_000.0),          # < 1min
    ("deliberate", 600_000.0),    # < 10min：正常审阅
    ("slow", 3_600_000.0),        # < 1h：迟滞
    ("stale", float("inf")),      # ≥ 1h：疑似积压
)

# ── 意图/难度启发式词典（T5：早期只披露不考核） ──

_INTENT_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (INTENT_CONSULT, ("建议", "意见", "该不该", "是否应该", "值不值得", "推荐",
                      "advice", "should i", "recommend", "你觉得")),
    (INTENT_EXPLORE, ("探索", "调研", "研究一下", "看看", "了解一下", "介绍一下",
                      "讲讲", "什么是", "怎么实现", "原理", "explore", "research",
                      "how does", "what is", "walk me through")),
    (INTENT_FIX, ("修复", "修一下", "报错", "错误", "异常", "失败", "排查", "定位",
                  "崩溃", "fix", "bug", "debug", "traceback", "error")),
    (INTENT_BUILD, ("实现", "新增", "添加", "开发", "写一个", "重构", "改造", "迁移",
                    "build", "implement", "add ", "refactor", "create")),
    (INTENT_OPERATE, ("运行", "执行", "部署", "启动", "重启", "跑一下", "安装",
                      "run ", "deploy", "start ", "restart", "install")),
)

_HARD_KEYWORDS = ("架构", "重构", "迁移", "并发", "死锁", "性能优化", "多文件",
                  "全链路", "设计", "分布式", "一致性", "architecture", "refactor",
                  "migration", "concurrency", "race", "distributed")
_MEDIUM_CHARS = 120
_HARD_CHARS = 600

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ACRRuleError(ValueError):
    """ACR 口径违规（未知介入 kind / 未知意图 / 未知状态）"""


# ════════════════════════════════════════════════════════════
#  分类器（启发式；T5「早期只披露不考核」）
# ════════════════════════════════════════════════════════════


def classify_intent(text: str) -> str:
    """任务意图启发式分类（explore / consult / fix / build / operate / other）

    **只返回标签**：调用方不得把原始文本写入事件载荷（载荷纪律见 `events`）。
    空/无匹配 → ``other``。
    """
    low = _CONTROL_CHARS_RE.sub(" ", str(text or "")).lower().strip()
    if not low:
        return INTENT_OTHER
    for intent, keywords in _INTENT_KEYWORDS:
        if any(kw in low for kw in keywords):
            return intent
    return INTENT_OTHER


def classify_difficulty(text: str, intent: str = "") -> str:
    """任务难度启发式分档（easy / medium / hard）

    规则（**等权起步**，T5 记录「赋值无理论推导且难度等权」，故不做人为加权）：
    命中复杂关键词或长度 ≥ 600 → hard；长度 ≥ 120 → medium；其余 → easy。
    ``explore`` / ``consult`` 单轮问答式任务降一档（不足 medium 即 easy）。
    """
    low = _CONTROL_CHARS_RE.sub(" ", str(text or "")).lower()
    length = len(low.strip())
    if any(kw in low for kw in _HARD_KEYWORDS) or length >= _HARD_CHARS:
        return DIFFICULTY_HARD
    if length >= _MEDIUM_CHARS:
        return DIFFICULTY_MEDIUM
    if intent in EXCLUDED_INTENTS:
        return DIFFICULTY_EASY
    return DIFFICULTY_EASY if length < 40 else DIFFICULTY_MEDIUM


def fatigue_bucket(latency_ms: Any) -> str:
    """审批迟滞 → 疲劳分桶（§6.1 审批衰减率）"""
    try:
        value = float(latency_ms)
    except (TypeError, ValueError):
        return "unknown"
    if value < 0:
        return "unknown"
    for name, upper in FATIGUE_BUCKETS:
        if value < upper:
            return name
    return "stale"


def intervention_weight(kind: str, *, strict: bool = False) -> float:
    """介入 kind → 权重（§6.1）

    Raises:
        ACRRuleError: `strict=True` 且 kind 未登记（默认返回 0.0 并计数，不静默丢失）。
    """
    weight = INTERVENTION_WEIGHTS.get(str(kind or ""))
    if weight is None:
        if strict:
            raise ACRRuleError(f"未登记的介入 kind（§6.1 口径）: {kind!r}")
        logger.warning("未登记的介入 kind（按 0 计，见 ACR 汇总 unknown_kinds）: %s", kind)
        return 0.0
    return float(weight)


# ════════════════════════════════════════════════════════════
#  埋点写入（真实发生点调用）
# ════════════════════════════════════════════════════════════


def _task_payload(task_id: str, correlation_id: str, extra: Dict[str, Any],
                  *, workspace_id: str = "", subject_id: str = "") -> Dict[str, Any]:
    """注入 S2-01 TraceContext 的叶子字段（task/workspace/subject）

    ``workspace_id`` / ``subject_id`` 显式传入时优先——任务收尾埋点发生在
    `TraceFacade.finish()` **之后**（finish 会清空 ContextVar），此时必须由调用方
    把上下文叶子带过来，否则 workspace_id 会静默丢失（本任务实现期实测到的缺陷）。
    """
    fields = ev.trace_fields()
    payload: Dict[str, Any] = {
        "task_id": str(task_id or fields.get("task_id") or ""),
        "workspace_id": str(workspace_id or fields.get("workspace_id") or ""),
        "subject_id": str(subject_id or fields.get("subject_id") or ""),
    }
    payload.update({k: v for k, v in (extra or {}).items() if v is not None})
    return payload


def record_intervention(kind: str, *, task_id: str = "", correlation_id: str = "",
                        actor: str = ACTOR_HUMAN, weight: Optional[float] = None,
                        intent: str = "", source_ref: str = "",
                        extra: Optional[Dict[str, Any]] = None,
                        idempotency_key: str = "",
                        store: Optional[EventStore] = None,
                        ts: Optional[str] = None) -> Optional[EventEnvelope]:
    """记录一次人工介入（§6.1 计数口径的唯一入口）

    权重缺省按 `INTERVENTION_WEIGHTS` 查表（Approve=1/条件=0.5/自动放行=0/
    超时 Deny=2/重跑=1/逃逸=1/查看=0）；`weight` 显式传入时以显式值为准
    （供 L2 人工执行等特殊场景标注）。

    幂等：`idempotency_key` 缺省取 ``f"{kind}:{source_ref}"``，同一治理动作重复
    上报不重复计数（§四验收：重放不重复计数）。
    """
    cid = correlation_id or ev.default_correlation_id()
    payload = _task_payload(task_id, cid, {
        "kind": str(kind or ""),
        "weight": float(intervention_weight(kind) if weight is None else weight),
        "intent": str(intent or ""),
        "source_ref": str(source_ref or ""),
        **(extra or {}),
    })
    key = idempotency_key or f"{kind}:{source_ref or cid}"
    return emit(EV_INTERVENTION, payload, actor=actor, correlation_id=cid,
                idempotency_key=key, ts=ts, store=store)


def record_approval(*, kind: str, record_id: str, state: str = "",
                    object_type: str = "", object_id: str = "",
                    level: str = "", actor: str = ACTOR_HUMAN,
                    latency_ms: Optional[float] = None, task_id: str = "",
                    correlation_id: str = "", intent: str = "",
                    count_intervention: bool = True,
                    extra: Optional[Dict[str, Any]] = None,
                    store: Optional[EventStore] = None,
                    ts: Optional[str] = None) -> List[Optional[EventEnvelope]]:
    """审批埋点（§6.6 approval：kind / latency_ms / fatigue_bucket / actor）

    同时产出两条事件：
      1. ``approval``：治理测量事件（kind / latency_ms / fatigue_bucket / level）；
      2. ``intervention``：ACR 计数事件（kind + weight + task 关联）
         —— ``count_intervention=False`` 时跳过（如 ``merged`` / ``archived``
         这类「系统执行审批结果」的动作只测量、不计介入）。

    返回 ``[approval_env, intervention_env]``（未写入的为 None）。
    """
    cid = correlation_id or ev.default_correlation_id()
    latency = None if latency_ms is None else float(latency_ms)
    approval_payload = _task_payload(task_id, cid, {
        "kind": str(kind or ""),
        "record_id": str(record_id or ""),
        "state": str(state or ""),
        "object_type": str(object_type or ""),
        "object_id": str(object_id or ""),
        "level": str(level or ""),
        "latency_ms": latency,
        "fatigue_bucket": fatigue_bucket(latency) if latency is not None else "unknown",
        "counted_as_intervention": bool(count_intervention),
        **(extra or {}),
    })
    approval_env = emit(
        EV_APPROVAL, approval_payload, actor=actor, correlation_id=cid,
        idempotency_key=f"approval:{record_id}:{kind}", ts=ts, store=store)
    if not count_intervention:
        return [approval_env, None]
    intervention_env = record_intervention(
        kind, task_id=task_id, correlation_id=cid, actor=actor, intent=intent,
        source_ref=f"approval:{record_id}", store=store, ts=ts)
    return [approval_env, intervention_env]


def record_escape(*, task_id: str = "", reason: str = "", capability_id: str = "",
                  path: str = "", digest_before: str = "", digest_after: str = "",
                  actor: str = ACTOR_HUMAN, correlation_id: str = "",
                  intent: str = "", extra: Optional[Dict[str, Any]] = None,
                  store: Optional[EventStore] = None,
                  ts: Optional[str] = None) -> List[Optional[EventEnvelope]]:
    """逃逸埋点（§6.6 escape：task_id / reason / capability_id）

    逃逸 = 绕过治理路径（例：用户手工编辑受管任务文件，未走受控写入能力）。
    同时产出 ``escape`` 与 ``intervention{kind=escape, weight=1}`` 两条事件。
    """
    cid = correlation_id or ev.default_correlation_id()
    payload = _task_payload(task_id, cid, {
        "reason": str(reason or ""),
        "capability_id": str(capability_id or ""),
        "path": str(path or ""),
        "digest_before": str(digest_before or ""),
        "digest_after": str(digest_after or ""),
        **(extra or {}),
    })
    key = f"escape:{path}:{digest_after}" if path else f"escape:{task_id}:{reason}"
    escape_env = emit(EV_ESCAPE, payload, actor=actor, correlation_id=cid,
                      idempotency_key=key, ts=ts, store=store)
    intervention_env = record_intervention(
        KIND_ESCAPE, task_id=task_id, correlation_id=cid, actor=actor,
        intent=intent, source_ref=key, store=store, ts=ts)
    return [escape_env, intervention_env]


def record_task_closed(*, task_id: str, status: str = STATUS_CLOSED,
                       intent: str = "", difficulty: str = "",
                       intervened: Optional[bool] = None,
                       intervention_kind: str = "",
                       duration_ms: Optional[float] = None,
                       cost_cents: Optional[float] = None,
                       trace_id: str = "", actor: str = ACTOR_AUTO,
                       correlation_id: str = "",
                       workspace_id: str = "", subject_id: str = "",
                       extra: Optional[Dict[str, Any]] = None,
                       store: Optional[EventStore] = None,
                       ts: Optional[str] = None) -> Optional[EventEnvelope]:
    """任务关闭埋点（§6.6 task.closed：intent / difficulty / intervened / intervention_kind）

    ``intervened`` 缺省时从本进程的介入台账推导（`EventStore.interventions_for`），
    并取首个介入 kind 作为 `intervention_kind`；显式传入则以显式值为准。
    幂等键 = ``task.closed:{task_id}``（同一任务重复收尾不重复计数）。
    """
    if status not in TASK_STATUSES:
        raise ACRRuleError(f"非法任务状态（§3.8）: {status!r}（允许 {TASK_STATUSES}）")
    if intent and intent not in INTENTS:
        raise ACRRuleError(f"非法任务意图: {intent!r}（允许 {INTENTS}）")
    if difficulty and difficulty not in DIFFICULTIES:
        raise ACRRuleError(f"非法难度: {difficulty!r}（允许 {DIFFICULTIES}）")
    cid = correlation_id or trace_id or ev.default_correlation_id()
    target = store if store is not None else ev.get_event_store()
    kinds: List[str] = []
    try:
        kinds = target.interventions_for(cid)
    except Exception:  # noqa: BLE001 介入台账异常 → 按未介入处理（如实披露）
        kinds = []
    resolved_intervened = bool(kinds) if intervened is None else bool(intervened)
    resolved_kind = intervention_kind or (kinds[0] if kinds else "")
    payload = _task_payload(task_id, cid, {
        "intent": intent or INTENT_OTHER,
        "difficulty": difficulty or DIFFICULTY_MEDIUM,
        "status": status,
        "intervened": resolved_intervened,
        "intervention_kind": resolved_kind,
        "intervention_kinds": list(kinds),
        "duration_ms": None if duration_ms is None else round(float(duration_ms), 3),
        "cost_cents": None if cost_cents is None else round(float(cost_cents), 6),
    }, workspace_id=workspace_id, subject_id=subject_id)
    if extra:
        payload.update(extra)
    event_type = EV_TASK_ABANDONED if status == STATUS_ABANDONED else EV_TASK_CLOSED
    env = emit(event_type, payload, actor=actor, correlation_id=cid,
               idempotency_key=f"{event_type}:{task_id}", ts=ts, store=store)
    # 收尾后清空该关联 id 的介入台账（避免泄漏到下一个任务）
    try:
        target.clear_interventions(cid)
    except Exception:  # noqa: BLE001
        pass
    return env


def record_task_abandoned(*, task_id: str, reason: str = "", **kwargs: Any
                          ) -> Optional[EventEnvelope]:
    """任务放弃埋点（§3.8 生命周期：abandoned；不计入 ACR 分母，单列披露）"""
    kwargs.setdefault("intent", INTENT_OTHER)
    extra = dict(kwargs.pop("extra", None) or {})
    extra["abandon_reason"] = str(reason or "")
    return record_task_closed(task_id=task_id, status=STATUS_ABANDONED,
                              extra=extra, **kwargs)


# ════════════════════════════════════════════════════════════
#  汇总视图（按日 / 按周）
# ════════════════════════════════════════════════════════════


def _empty_bucket() -> Dict[str, Any]:
    return {"total_weight": 0.0, "counts": {}, "by_kind": {}}


def _accumulate(bucket: Dict[str, Any], env: EventEnvelope) -> None:
    payload = env.payload or {}
    kind = str(payload.get("kind") or "")
    try:
        weight = float(payload.get("weight") or 0.0)
    except (TypeError, ValueError):
        weight = 0.0
    bucket["total_weight"] += weight
    bucket["counts"][kind] = bucket["counts"].get(kind, 0) + 1
    bucket["by_kind"][kind] = round(bucket["by_kind"].get(kind, 0.0) + weight, 6)


def acr_summary(*, day: Optional[str] = None, days: int = 1,
                since: Optional[str] = None, until: Optional[str] = None,
                week_of: Optional[str] = None, directory: Optional[str] = None,
                store: Optional[EventStore] = None,
                cost_field: str = "cost_normalized_cents") -> Dict[str, Any]:
    """ACR 汇总视图（默认「今日」；可给 since/until 或 week_of 取周）

    分母（§6.1）：``closed(成功) + failed``，**排除 explore/consult**；
    ``abandoned`` 单列。分子：介入事件的权重和（同口径排除 explore/consult 任务的介入）。
    探索满意度（P7.1-16）单列。

    Args:
        day: 单日（`YYYY-MM-DD`），等价 ``days=1`` 指向该日。
        days: 从 since（或今天）起的天数窗（7 = 周视图）。
        week_of: 取该日所属 **ISO 周（周一–周日）**。
        cost_field: 从 ``cost`` 事件取哪个字段累加成本（默认归一成本）。
    """
    window_start, window_end = _resolve_window(day=day, days=days, since=since,
                                                until=until, week_of=week_of)
    rows = iter_events(since=window_start, until=f"{window_end}\uffff",
                       directory=directory)
    return _summarize(rows, window_start, window_end, cost_field=cost_field)


def acr_daily(day: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """按日 ACR 视图"""
    return acr_summary(day=day or date.today().isoformat(), **kwargs)


def acr_weekly(anchor_day: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """按 ISO 周（周一–周日）ACR 视图"""
    return acr_summary(week_of=anchor_day or date.today().isoformat(), **kwargs)


def acr_snapshot(days: int = 7, **kwargs: Any) -> Dict[str, Any]:
    """日 + 周 + 窗口三视图快照（供面板/验收报告直接引用）"""
    today = date.today()
    daily = [acr_daily((today - timedelta(days=offset)).isoformat(), **kwargs)
             for offset in range(max(1, int(days)) - 1, -1, -1)]
    return {
        "today": daily[-1] if daily else {},
        "daily": daily,
        "week": acr_weekly(today.isoformat(), **kwargs),
        "window": acr_summary(days=days, **kwargs),
    }


def _resolve_window(*, day: Optional[str], days: int, since: Optional[str],
                    until: Optional[str], week_of: Optional[str]) -> Tuple[str, str]:
    if week_of:
        try:
            anchor = datetime.strptime(str(week_of)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            anchor = date.today()
        start = anchor - timedelta(days=anchor.weekday())            # 周一
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    if day:
        return str(day)[:10], str(day)[:10]
    end = str(until)[:10] if until else date.today().isoformat()
    start = str(since)[:10] if since else shift_day(end, -(max(1, int(days)) - 1))
    return start, end


def _summarize(rows: Sequence[EventEnvelope], window_start: str,
               window_end: str, *, cost_field: str = "cost_normalized_cents"
               ) -> Dict[str, Any]:
    """事件集合 → ACR 汇总（纯函数，便于单测）"""
    # 1) 任务意图索引（来自 task.closed / task.abandoned）
    task_intent: Dict[str, str] = {}
    task_status: Dict[str, str] = {}
    denominator = {STATUS_CLOSED: 0, STATUS_FAILED: 0}
    abandoned = 0
    excluded_tasks = 0
    by_intent: Dict[str, int] = {}
    by_difficulty: Dict[str, int] = {}
    intervened_tasks = 0
    total_cost = 0.0
    for env in rows:
        if env.type not in (EV_TASK_CLOSED, EV_TASK_ABANDONED):
            continue
        payload = env.payload or {}
        task_id = str(payload.get("task_id") or env.correlation_id or "")
        intent = str(payload.get("intent") or INTENT_OTHER)
        status = str(payload.get("status") or STATUS_CLOSED)
        difficulty = str(payload.get("difficulty") or DIFFICULTY_MEDIUM)
        if task_id:
            task_intent[task_id] = intent
            task_status[task_id] = status
        by_intent[intent] = by_intent.get(intent, 0) + 1
        by_difficulty[difficulty] = by_difficulty.get(difficulty, 0) + 1
        if payload.get("intervened"):
            intervened_tasks += 1
        if intent in EXCLUDED_INTENTS:
            excluded_tasks += 1
        elif status in DENOMINATOR_STATUSES:
            denominator[status] = denominator.get(status, 0) + 1
        elif status == STATUS_ABANDONED:
            abandoned += 1
        try:
            total_cost += float(payload.get("cost_cents") or 0.0)
        except (TypeError, ValueError):
            pass

    # 2) 介入事件归类
    overall = _empty_bucket()
    excluded_side = _empty_bucket()
    exploration_side = _empty_bucket()
    core_bucket = _empty_bucket()
    excluded_core = _empty_bucket()
    unattributed_weight = 0.0
    unknown_kinds: Dict[str, int] = {}
    intervention_count = 0
    for env in rows:
        if env.type != EV_INTERVENTION:
            continue
        payload = env.payload or {}
        kind = str(payload.get("kind") or "")
        if kind not in INTERVENTION_WEIGHTS:
            unknown_kinds[kind] = unknown_kinds.get(kind, 0) + 1
        _accumulate(overall, env)
        if kind in CORE_INTERVENTION_WEIGHTS:
            _accumulate(core_bucket, env)
        intervention_count += 1
        task_id = str(payload.get("task_id") or "")
        intent = str(payload.get("intent") or "") or task_intent.get(task_id, "")
        if intent in EXCLUDED_INTENTS:
            _accumulate(exploration_side, env)
            _accumulate(excluded_side, env)
            if kind in CORE_INTERVENTION_WEIGHTS:
                _accumulate(excluded_core, env)
            continue
        if not task_id:
            unattributed_weight += _weight_of(env)

    denominator_total = denominator.get(STATUS_CLOSED, 0) + denominator.get(STATUS_FAILED, 0)
    # ACR 分子：**只**累加 §6.1 七项口径，并排除 explore/consult 任务的介入
    numerator = round(core_bucket["total_weight"] - excluded_core["total_weight"], 6)
    extension_weight = round(overall["total_weight"] - core_bucket["total_weight"], 6)
    acr = round(numerator / denominator_total, 6) if denominator_total else None

    # 3) 探索满意度单列（P7.1-16）
    explore_closed = 0
    explore_failed = 0
    explore_abandoned = 0
    for task_id, intent in task_intent.items():
        if intent not in EXCLUDED_INTENTS:
            continue
        status = task_status.get(task_id, STATUS_CLOSED)
        if status == STATUS_CLOSED:
            explore_closed += 1
        elif status == STATUS_FAILED:
            explore_failed += 1
        elif status == STATUS_ABANDONED:
            explore_abandoned += 1
    explore_base = explore_closed + explore_failed
    exploration = {
        "tasks": excluded_tasks,
        "closed_success": explore_closed,
        "failed": explore_failed,
        "abandoned": explore_abandoned,
        "intervention_weight": round(exploration_side["total_weight"], 6),
        "intervention_counts": dict(exploration_side["counts"]),
        "satisfaction": (round(explore_closed / explore_base, 6)
                         if explore_base else None),
        "satisfaction_basis": "closure_success_ratio（关闭成功 / (关闭成功+失败)）",
        "note": "P7.1-16 探索满意度：源文档未给公式，本实现以「关闭成功率」为代理指标并同时披露介入权重",
    }

    # 4) 成本（同窗，来自 cost 事件，仅用于 task.closed 的 cost_cents 交叉核对）
    cost_total = 0.0
    cost_events = 0
    for env in rows:
        if env.type != EV_COST:
            continue
        try:
            cost_total += float((env.payload or {}).get(cost_field) or 0.0)
        except (TypeError, ValueError):
            continue
        cost_events += 1

    return {
        "window": {"start": window_start, "end": window_end},
        "denominator": {
            "closed": denominator.get(STATUS_CLOSED, 0),
            "failed": denominator.get(STATUS_FAILED, 0),
            "total": denominator_total,
        },
        "abandoned": abandoned,
        "excluded": {"intents": list(EXCLUDED_INTENTS), "tasks": excluded_tasks},
        "tasks": {
            "total": len(task_intent),
            "intervened": intervened_tasks,
            "by_intent": dict(sorted(by_intent.items())),
            "by_difficulty": dict(sorted(by_difficulty.items())),
            "cost_cents_total": round(total_cost, 6),
        },
        "interventions": {
            "events": intervention_count,
            "total_weight": round(overall["total_weight"], 6),
            "by_kind": dict(sorted(overall["by_kind"].items())),
            "counts": dict(sorted(overall["counts"].items())),
            "core_weight": round(core_bucket["total_weight"], 6),
            "extension_weight": extension_weight,
            "extension_kinds": sorted(EXTENDED_INTERVENTION_WEIGHTS.keys()),
            "unattributed_weight": round(unattributed_weight, 6),
        },
        "acr": acr,
        "acr_numerator": numerator,
        "acr_formula": ("ACR = Σ介入权重(§6.1 七项口径，排除 explore/consult 任务) "
                        "/ (closed + failed)"),
        "exploration": exploration,
        "unknown_kinds": dict(sorted(unknown_kinds.items())),
        "cost_events": {"events": cost_events, "total": round(cost_total, 6),
                        "field": cost_field},
        "weights": dict(CORE_INTERVENTION_WEIGHTS),
        "weights_extension": dict(EXTENDED_INTERVENTION_WEIGHTS),
    }


def _weight_of(env: EventEnvelope) -> float:
    try:
        return float((env.payload or {}).get("weight") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def acr_by_day(*, days: int = 7, directory: Optional[str] = None) -> List[Dict[str, Any]]:
    """按日 ACR 序列（面板用；缺日补空桶）"""
    today = date.today()
    out: List[Dict[str, Any]] = []
    for offset in range(max(1, int(days)) - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        out.append(acr_daily(day, directory=directory))
    return out


def write_acr_snapshot(path: Optional[str] = None, *, days: int = 7,
                       directory: Optional[str] = None) -> Dict[str, Any]:
    """输出 `data/acr_snapshot.json`（面板/验收报告的数据源声明）"""
    import json
    snapshot = acr_snapshot(days=days, directory=directory)
    target = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "acr_snapshot.json")
    try:
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    except OSError as e:  # noqa: BLE001 best-effort 摘要写出
        logger.warning("写出 acr_snapshot.json 失败: %s", e)
    return snapshot


__all__ = [
    "KIND_APPROVE", "KIND_CONDITIONAL", "KIND_AUTO_PASS", "KIND_TIMEOUT_DENY",
    "KIND_RERUN", "KIND_ESCAPE", "KIND_VIEW", "KIND_REJECT",
    "CORE_INTERVENTION_WEIGHTS", "EXTENDED_INTERVENTION_WEIGHTS",
    "INTERVENTION_WEIGHTS",
    "INTENT_EXPLORE", "INTENT_CONSULT", "INTENT_FIX", "INTENT_BUILD",
    "INTENT_OPERATE", "INTENT_OTHER", "INTENTS", "EXCLUDED_INTENTS",
    "STATUS_CLOSED", "STATUS_FAILED", "STATUS_ABANDONED", "TASK_STATUSES",
    "DENOMINATOR_STATUSES", "DIFFICULTY_EASY", "DIFFICULTY_MEDIUM",
    "DIFFICULTY_HARD", "DIFFICULTIES", "FATIGUE_BUCKETS", "ACRRuleError",
    "classify_intent", "classify_difficulty", "fatigue_bucket",
    "intervention_weight", "record_intervention", "record_approval",
    "record_escape", "record_task_closed", "record_task_abandoned",
    "acr_summary", "acr_daily", "acr_weekly", "acr_snapshot", "acr_by_day",
    "write_acr_snapshot",
]
