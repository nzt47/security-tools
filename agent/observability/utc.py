"""UTC（单位任务成本）成本埋点与口径归一（TASK-S2-03 / v7.2 §6.2·§6.6 / P7.1-18 / T6 修正）

本模块把 v7.2 §6.6 的 **cost 埋点清单**与 §6.2 的 **UTC（单位任务成本）**落地，
产出日/周聚合与归一化成本，供 S5-03（日/周双层刹车 + 断食）直接消费。

## 埋点字段（§6.6 cost）

``task_id`` / ``workspace_id`` / ``subject_id`` / ``model`` / ``tokens_in`` /
``tokens_out`` / ``retries`` / ``shadow_overhead`` / ``cents``
→ 本模块额外落 ``cache_hit`` / ``cost_raw_cents`` / ``cost_normalized_cents`` /
``anchor_model`` / ``coefficient_in`` / ``coefficient_out``。

## 两条硬口径

1. **缓存命中不计 token（P7.1-18）**：``cache_hit=True`` 时计费 token 归零
   （``billable_tokens_* = 0``），仅 shadow_overhead 计入成本。
2. **归一化口径（T6 修正）**：以**主力模型为锚价**，配**模型换算系数表**：

   ``cost_normalized_cents = Σ (billable_tokens/1000) × anchor_price_cents × coefficient(model)
                             + shadow_overhead_cents``

   默认系数取「模型单价 ÷ 锚模型单价」（``source="price_ratio"``）——此时归一成本与
   既有成本监控口径（`agent.model_router.cost_tracker.MODEL_COSTS`）**逐分一致**，
   可直接对账（`reconcile_pricing()` 提供机器可验的一致性证明）；系数可经
   ``CP_UTC_COEFFICIENTS`` 覆盖（``source="override"``），供后续数据拟合后校准。

   **诚实口径（对齐 T6/T5）**：默认不做人为校准——早期（W1–W9）尚无拟合数据，
   把未经拟合的系数当真值会引入新的失真；归一化在此阶段的作用是**统一口径与
   可对账**，而不是制造"看起来更准"的数字。

   **【S2-03 #5 · Owner 裁定 2026-09-11：沿用锚价系数表，本次不校准】**
   校准需要「同一批标准任务在多模型实跑」的经验成本比，其量尺是 **L2 Core-50
   基线**（由 S5-02 产出）；尺子不存在时无法校准。故本任务只标注口径版本，
   校准评估的触发条件见 `CALIBRATION_TRIGGER`（不得丢失）。

3. **【S2-03 #9 · Owner 裁定 2026-09-11：双成本轨收敛到事件流】**
   成本**唯一数据源 ＝ 事件流**（本模块 `record_cost()` → `data/events/`）。
   旧轨 `data/cost_log.jsonl` 已**停写**（`agent.model_router.cost_tracker` 的
   `CostTracker.record()` 默认不再落盘，`CP_COST_LEGACY_LOG_WRITE=1` 可回滚，
   停写期间有告警、不留静默丢账），仅保留**只读解析兼容 ≤1 minor**（归档不删）；
   `reconcile_cost_log()` **降级为纯对账工具**（返回 `role="reconciliation_only"`）。

## UTC 定义

``UTC = cost_normalized_cents / 任务数``（单位任务成本，cents/任务）。
默认任务口径 = 当日 ``closed + failed``（全意图）；另给 ACR 同口径变体
``utc_cents_per_task_acr_cohort``（排除 explore/consult）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.observability import events as ev
from agent.observability.acr import EXCLUDED_INTENTS, DENOMINATOR_STATUSES
from agent.observability.events import (
    EV_COST,
    EV_TASK_ABANDONED,
    EV_TASK_CLOSED,
    ACTOR_AUTO,
    EventEnvelope,
    EventStore,
    emit,
    iter_events,
)

logger = logging.getLogger("agent.observability.utc")

# ════════════════════════════════════════════════════════════
#  口径版本标注（裁定 C / D 的落地锚点；**指标输出必须带这些标注**）
# ════════════════════════════════════════════════════════════

#: 本模块口径版本（任何口径变更须改此号，便于追溯"当时按哪版算的"）
COST_SCHEMA_VERSION = "utc.v1"
#: 归一化系数口径版本 —— **当前＝价格锚定系数（未经经验校准）**
CALIBRATION_VERSION = "price_anchor.v1"
#: 口径说明（进文档与指标输出；裁定 C 要求显式标注）
CALIBRATION_NOTE = ("当前口径＝价格锚定系数（主力模型锚价 + 各模型价格比例，"
                    "source=price_ratio），**未经经验校准**")
#: 校准触发条件（裁定 C 的后续触发条件，**勿丢**）
CALIBRATION_TRIGGER = ("待 S5-02 产出的 L2 Core-50 基线就绪后，启动校准评估"
                       "（跨模型成本-效果实测 → 经验系数 → 替换价格系数）；"
                       "届时须定义校准数据来源与复校周期（建议季度复校）")
#: 成本唯一数据源（裁定 D）
COST_SOURCE_OF_TRUTH = "events"
#: 旧轨角色（已停写、只作对账）
LEGACY_COST_LOG_ROLE = "reconciliation_only"

#: 锚模型（主力模型）缺省与解析优先级：env > config.yaml llm.model > 默认
DEFAULT_ANCHOR_MODEL = "gpt-4o-mini"
ENV_ANCHOR_MODEL = "CP_UTC_ANCHOR_MODEL"
ENV_COEFFICIENTS = "CP_UTC_COEFFICIENTS"
ENV_PRICE_OVERRIDES = "CP_UTC_PRICES"

#: 未计价模型的单价兜底（**必须与 `cost_tracker.CostTracker.record` 的默认一致**，
#: 否则归一成本与既有成本监控无法对账）
DEFAULT_MODEL_PRICE_USD_PER_1K: Dict[str, float] = {"input": 0.01, "output": 0.02}

#: 内置价格兜底副本（仅当 `agent.model_router.cost_tracker` 不可导入时使用；
#: 与 `MODEL_COSTS` 保持同值，`reconcile_pricing()` 会断言二者一致）
_BUILTIN_MODEL_COSTS: Dict[str, Dict[str, float]] = {
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}

_CONFIG_CACHE: Dict[str, Any] = {}


def calibration_block() -> Dict[str, Any]:
    """口径标注块（**所有成本/UTC 指标输出都应带上它**）

    裁定 C：显式标注「当前口径＝价格锚定系数，待 L2 基线后校准」+ 口径版本号。
    裁定 D：显式标注「成本唯一数据源＝事件流」+ 旧轨角色。
    """
    anchor, source = resolve_anchor_model()
    return {
        "cost_schema_version": COST_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "calibration_note": CALIBRATION_NOTE,
        "calibration_trigger": CALIBRATION_TRIGGER,
        "calibrated": False,
        "anchor_model": anchor,
        "anchor_source": source,
        "source_of_truth": COST_SOURCE_OF_TRUTH,
        "legacy_cost_log_role": LEGACY_COST_LOG_ROLE,
    }


class UTCNormalizationError(ValueError):
    """归一化参数非法（系数/价格配置坏值）"""


# ════════════════════════════════════════════════════════════
#  价格 / 锚模型 / 系数表
# ════════════════════════════════════════════════════════════


def model_costs() -> Dict[str, Dict[str, float]]:
    """模型单价表（USD / 1k tokens）——**唯一来源**为既有成本监控 `MODEL_COSTS`

    惰性导入：`agent.model_router.cost_tracker` 在导入期会构造进程单例
    `CostTracker`（建目录 + 读 `./data/cost_log.jsonl`），本模块避免把该副作用
    带上；导入失败时退化为同值副本（`reconcile_pricing()` 会比对两者是否一致）。
    """
    try:
        from agent.model_router.cost_tracker import MODEL_COSTS
        if isinstance(MODEL_COSTS, dict) and MODEL_COSTS:
            return {str(k): dict(v) for k, v in MODEL_COSTS.items()}
    except Exception as e:  # noqa: BLE001 导入失败 → 内置副本
        logger.debug("MODEL_COSTS 不可用，使用内置同值副本: %s", e)
    return {k: dict(v) for k, v in _BUILTIN_MODEL_COSTS.items()}


def price_usd_per_1k(model: str) -> Dict[str, float]:
    """模型单价（USD / 1k tokens；未知模型 → 与既有监控同款兜底价）"""
    table = dict(model_costs())
    for key, value in _price_overrides().items():
        table[key] = dict(value)
    entry = table.get(str(model or ""))
    if not entry:
        entry = dict(DEFAULT_MODEL_PRICE_USD_PER_1K)
    try:
        return {"input": float(entry.get("input", 0.0)),
                "output": float(entry.get("output", 0.0))}
    except (TypeError, ValueError) as e:
        raise UTCNormalizationError(f"模型单价非法 model={model!r} entry={entry!r}") from e


def _price_overrides() -> Dict[str, Dict[str, float]]:
    raw = os.getenv(ENV_PRICE_OVERRIDES)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("%s 非法 JSON，已忽略", ENV_PRICE_OVERRIDES)
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out[str(key)] = {"input": float(value.get("input", 0.0)),
                             "output": float(value.get("output", 0.0))}
    return out


def resolve_anchor_model() -> Tuple[str, str]:
    """解析主力模型（锚）：返回 ``(model, source)``

    优先级：``CP_UTC_ANCHOR_MODEL`` > ``config.yaml:llm.model`` > 默认
    （``gpt-4o-mini``）。结果进程内缓存（配置热加载不重算，避免每条事件读盘）。
    """
    cached = _CONFIG_CACHE.get("anchor")
    if cached:
        return cached
    env_model = str(os.getenv(ENV_ANCHOR_MODEL) or "").strip()
    if env_model:
        resolved = (env_model, "env")
    else:
        config_model, source = _config_llm_model()
        resolved = (config_model or DEFAULT_ANCHOR_MODEL,
                    source if config_model else "default")
    _CONFIG_CACHE["anchor"] = resolved
    return resolved


def reset_config_cache() -> None:
    """清空锚模型解析缓存（**测试 / 配置热加载**用）"""
    _CONFIG_CACHE.clear()


def _config_llm_model() -> Tuple[str, str]:
    try:
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "config.yaml")
        if not os.path.exists(path):
            return "", "default"
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        model = str(((data.get("llm") or {}).get("model")) or "").strip()
        return model, "config.yaml"
    except Exception as e:  # noqa: BLE001 配置不可读 → 默认
        logger.debug("config.yaml 读取失败（锚模型回退默认）: %s", e)
        return "", "default"


def anchor_prices_cents() -> Dict[str, float]:
    """锚模型单价（**cents / 1k tokens**）"""
    anchor, _ = resolve_anchor_model()
    usd = price_usd_per_1k(anchor)
    return {"input": usd["input"] * 100.0, "output": usd["output"] * 100.0}


def _coefficient_overrides() -> Dict[str, Dict[str, float]]:
    raw = os.getenv(ENV_COEFFICIENTS)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("%s 非法 JSON，已忽略（沿用价格比系数）", ENV_COEFFICIENTS)
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            try:
                out[str(key)] = {"in": float(value.get("in", 1.0)),
                                 "out": float(value.get("out", 1.0))}
            except (TypeError, ValueError):
                logger.warning("系数非法（已忽略）: %s=%r", key, value)
    return out


def coefficient(model: str) -> Dict[str, Any]:
    """模型换算系数（相对锚价；T6「系数表（模型 × 计价）」）

    Returns:
        ``{"in": k_in, "out": k_out, "source": "override"|"price_ratio"}``
    """
    anchor, _ = resolve_anchor_model()
    overrides = _coefficient_overrides()
    if str(model or "") in overrides:
        item = overrides[str(model)]
        return {"in": float(item["in"]), "out": float(item["out"]),
                "source": "override"}
    base = anchor_prices_cents()
    try:
        own = price_usd_per_1k(model)
    except UTCNormalizationError:
        own = dict(DEFAULT_MODEL_PRICE_USD_PER_1K)
    own_cents = {"input": own["input"] * 100.0, "output": own["output"] * 100.0}
    k_in = own_cents["input"] / base["input"] if base["input"] else 1.0
    k_out = own_cents["output"] / base["output"] if base["output"] else 1.0
    return {"in": k_in, "out": k_out, "source": "price_ratio"}


def coefficient_table(models: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """系数表（模型 × 计价）→ 供指标字典（§6.7 口径表）与验收报告引用"""
    names = list(models) if models else sorted(model_costs().keys())
    anchor, source = resolve_anchor_model()
    table: Dict[str, Dict[str, Any]] = {}
    for name in names:
        table[str(name)] = {**coefficient(str(name)),
                            "price_usd_per_1k": price_usd_per_1k(str(name))}
    return {"anchor_model": anchor, "anchor_source": source,
            "anchor_price_cents_per_1k": anchor_prices_cents(),
            "models": table,
            "calibration": calibration_block()}


# ════════════════════════════════════════════════════════════
#  归一化公式
# ════════════════════════════════════════════════════════════


def normalize_cost(tokens_in: int = 0, tokens_out: int = 0, model: str = "",
                   *, cache_hit: bool = False,
                   retry_tokens_in: int = 0, retry_tokens_out: int = 0,
                   shadow_overhead_ms: float = 0.0,
                   shadow_overhead_cents: float = 0.0) -> Dict[str, Any]:
    """成本归一（T6 修正公式；可复现、可对账）

    ``billable = 0 if cache_hit else tokens``（P7.1-18：缓存命中不计 token 成本）。
    重试 token 默认**不重复计**：若上游已把重试消耗并入 ``tokens_in/out``，
    ``retry_tokens_*`` 应保持 0；仅在「重试消耗未被上游计入」时显式传入。

    Returns:
        dict（含 ``cost_raw_cents`` / ``cost_normalized_cents`` 及中间量）
    """
    anchor, anchor_source = resolve_anchor_model()
    coef = coefficient(model)
    base = anchor_prices_cents()
    billable_in = 0 if cache_hit else max(0, int(tokens_in or 0))
    billable_out = 0 if cache_hit else max(0, int(tokens_out or 0))
    billable_in += max(0, int(retry_tokens_in or 0))
    billable_out += max(0, int(retry_tokens_out or 0))
    shadow = max(0.0, float(shadow_overhead_cents or 0.0))

    normalized = ((billable_in / 1000.0) * base["input"] * coef["in"]
                  + (billable_out / 1000.0) * base["output"] * coef["out"]
                  + shadow)
    own = price_usd_per_1k(model)
    raw = ((billable_in / 1000.0) * own["input"] * 100.0
           + (billable_out / 1000.0) * own["output"] * 100.0
           + shadow)
    return {
        "model": str(model or ""),
        "anchor_model": anchor,
        "anchor_source": anchor_source,
        "cache_hit": bool(cache_hit),
        "tokens_in": max(0, int(tokens_in or 0)),
        "tokens_out": max(0, int(tokens_out or 0)),
        "billable_tokens_in": billable_in,
        "billable_tokens_out": billable_out,
        "coefficient_in": round(coef["in"], 9),
        "coefficient_out": round(coef["out"], 9),
        "coefficient_source": coef["source"],
        "shadow_overhead_ms": round(max(0.0, float(shadow_overhead_ms or 0.0)), 3),
        "shadow_overhead_cents": round(shadow, 6),
        "cost_raw_cents": round(raw, 6),
        "cost_normalized_cents": round(normalized, 6),
    }


def record_cost(*, model: str = "", provider: str = "", source: str = "",
                tokens_in: int = 0, tokens_out: int = 0,
                cache_hit: bool = False, retries: int = 0,
                shadow_overhead_ms: float = 0.0,
                shadow_overhead_cents: float = 0.0,
                retry_tokens_in: int = 0, retry_tokens_out: int = 0,
                task_id: str = "", correlation_id: str = "",
                actor: str = ACTOR_AUTO, interaction_id: str = "",
                duration_ms: Optional[float] = None, error: str = "",
                store: Optional[EventStore] = None,
                ts: Optional[str] = None) -> Optional[EventEnvelope]:
    """记录一次 LLM 调用的成本（§6.6 cost 埋点）

    幂等键 = ``cost:llm:{interaction_id}``（`LLMInteraction.id` 为每次交互唯一 id），
    因此「同一次交互重复记账」被折叠，而「同参多次调用」各自计数。
    """
    calc = normalize_cost(
        tokens_in=tokens_in, tokens_out=tokens_out, model=model,
        cache_hit=cache_hit, retry_tokens_in=retry_tokens_in,
        retry_tokens_out=retry_tokens_out,
        shadow_overhead_ms=shadow_overhead_ms,
        shadow_overhead_cents=shadow_overhead_cents)
    fields = ev.trace_fields()
    cid = correlation_id or fields.get("trace_id") or ""
    payload = {
        "task_id": str(task_id or fields.get("task_id") or ""),
        "workspace_id": fields.get("workspace_id") or "",
        "subject_id": fields.get("subject_id") or "",
        "model": str(model or ""),
        "provider": str(provider or ""),
        "source": str(source or ""),
        "retries": max(0, int(retries or 0)),
        "duration_ms": None if duration_ms is None else round(float(duration_ms), 3),
        "error": str(error or "")[:200],
        "interaction_id": str(interaction_id or ""),
        **calc,
    }
    key = f"cost:llm:{interaction_id}" if interaction_id else ""
    return emit(EV_COST, payload, actor=actor, correlation_id=cid,
                idempotency_key=key, ts=ts, store=store)


# ════════════════════════════════════════════════════════════
#  聚合（日 / 周 / 快照）
# ════════════════════════════════════════════════════════════


def _empty_totals() -> Dict[str, Any]:
    return {
        "cost_raw_cents": 0.0,
        "cost_normalized_cents": 0.0,
        "llm_calls": 0,
        "cache_hits": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "billable_tokens_in": 0,
        "billable_tokens_out": 0,
        "retries": 0,
        "shadow_overhead_ms": 0.0,
        "shadow_overhead_cents": 0.0,
        "errors": 0,
        "by_model": {},
    }


def _fold(totals: Dict[str, Any], env: EventEnvelope) -> None:
    payload = env.payload or {}
    model = str(payload.get("model") or "unknown")
    try:
        raw = float(payload.get("cost_raw_cents") or 0.0)
        norm = float(payload.get("cost_normalized_cents") or 0.0)
    except (TypeError, ValueError):
        raw = norm = 0.0
    totals["cost_raw_cents"] += raw
    totals["cost_normalized_cents"] += norm
    totals["llm_calls"] += 1
    totals["cache_hits"] += 1 if payload.get("cache_hit") else 0
    for field in ("tokens_in", "tokens_out", "billable_tokens_in",
                  "billable_tokens_out", "retries"):
        try:
            totals[field] += int(payload.get(field) or 0)
        except (TypeError, ValueError):
            pass
    for field in ("shadow_overhead_ms", "shadow_overhead_cents"):
        try:
            totals[field] += float(payload.get(field) or 0.0)
        except (TypeError, ValueError):
            pass
    if payload.get("error"):
        totals["errors"] += 1
    entry = totals["by_model"].setdefault(
        model, {"calls": 0, "cost_normalized_cents": 0.0, "tokens_in": 0,
                "tokens_out": 0, "cache_hits": 0})
    entry["calls"] += 1
    entry["cost_normalized_cents"] = round(entry["cost_normalized_cents"] + norm, 6)
    try:
        entry["tokens_in"] += int(payload.get("tokens_in") or 0)
        entry["tokens_out"] += int(payload.get("tokens_out") or 0)
    except (TypeError, ValueError):
        pass
    entry["cache_hits"] += 1 if payload.get("cache_hit") else 0


def _task_counts(rows: Sequence[EventEnvelope]) -> Dict[str, int]:
    all_tasks = 0
    acr_cohort = 0
    excluded = 0
    abandoned = 0
    seen: set = set()
    for env in rows:
        if env.type not in (EV_TASK_CLOSED, EV_TASK_ABANDONED):
            continue
        payload = env.payload or {}
        task_id = str(payload.get("task_id") or env.correlation_id or "")
        if task_id in seen:
            continue
        seen.add(task_id)
        status = str(payload.get("status") or "closed")
        intent = str(payload.get("intent") or "other")
        if status == "abandoned":
            abandoned += 1
            continue
        if status in DENOMINATOR_STATUSES:
            all_tasks += 1
            if intent in EXCLUDED_INTENTS:
                excluded += 1
            else:
                acr_cohort += 1
    return {"closed_and_failed": all_tasks, "acr_cohort": acr_cohort,
            "excluded_explore_consult": excluded, "abandoned": abandoned}


def _finalize(totals: Dict[str, Any], tasks: Dict[str, int]) -> Dict[str, Any]:
    calls = totals["llm_calls"] or 0
    out = dict(totals)
    out["cost_raw_cents"] = round(totals["cost_raw_cents"], 6)
    out["cost_normalized_cents"] = round(totals["cost_normalized_cents"], 6)
    out["tokens_total"] = totals["tokens_in"] + totals["tokens_out"]
    out["billable_tokens_total"] = (totals["billable_tokens_in"]
                                    + totals["billable_tokens_out"])
    out["shadow_overhead_ms"] = round(totals["shadow_overhead_ms"], 3)
    out["shadow_overhead_cents"] = round(totals["shadow_overhead_cents"], 6)
    out["cache_hit_rate"] = round(totals["cache_hits"] / calls, 6) if calls else None
    out["by_model"] = {k: v for k, v in sorted(totals["by_model"].items())}
    out["tasks"] = tasks
    out["utc_cents_per_task"] = (
        round(out["cost_normalized_cents"] / tasks["closed_and_failed"], 6)
        if tasks["closed_and_failed"] else None)
    out["utc_cents_per_task_acr_cohort"] = (
        round(out["cost_normalized_cents"] / tasks["acr_cohort"], 6)
        if tasks["acr_cohort"] else None)
    out["utc_formula"] = ("UTC = cost_normalized_cents / 任务数"
                          "（closed+failed；ACR 同口径变体排除 explore/consult）")
    out["calibration"] = calibration_block()
    return out


def utc_daily(day: Optional[str] = None, *, directory: Optional[str] = None) -> Dict[str, Any]:
    """按日 UTC / 成本聚合"""
    target = str(day or date.today().isoformat())[:10]
    rows = iter_events(day=target, directory=directory)
    totals = _empty_totals()
    for env in rows:
        if env.type == EV_COST:
            _fold(totals, env)
    out = _finalize(totals, _task_counts(rows))
    out["date"] = target
    out["anchor_model"] = resolve_anchor_model()[0]
    return out


def utc_window(*, start: str, end: str, directory: Optional[str] = None) -> Dict[str, Any]:
    """窗口（含端点）UTC / 成本聚合"""
    rows = iter_events(since=str(start)[:10], until=f"{str(end)[:10]}\uffff",
                       directory=directory)
    totals = _empty_totals()
    for env in rows:
        if env.type == EV_COST:
            _fold(totals, env)
    out = _finalize(totals, _task_counts(rows))
    out["window"] = {"start": str(start)[:10], "end": str(end)[:10]}
    out["anchor_model"] = resolve_anchor_model()[0]
    return out


def utc_weekly(anchor_day: Optional[str] = None,
               directory: Optional[str] = None) -> Dict[str, Any]:
    """按 ISO 周（周一–周日）UTC / 成本聚合"""
    try:
        anchor = datetime.strptime(
            str(anchor_day or date.today().isoformat())[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        anchor = date.today()
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    out = utc_window(start=start.isoformat(), end=end.isoformat(), directory=directory)
    out["iso_week"] = f"{start.isocalendar()[0]}-W{start.isocalendar()[1]:02d}"
    return out


def utc_snapshot(days: int = 7, *, directory: Optional[str] = None) -> Dict[str, Any]:
    """日 + 周 + 滚动基线快照（含 §7 断食规则所需的日均成本与比值）

    ``baseline_cents_per_day`` = 近 ``days`` 日（不含今日）归一成本日均值；
    ``ratio`` = 今日归一成本 / 基线（§7「进 UTC>1.3× / 出 24h≤1.1×」的输入量）。
    """
    today = date.today()
    span = max(1, int(days))
    daily = [utc_daily((today - timedelta(days=offset)).isoformat(), directory=directory)
             for offset in range(span - 1, -1, -1)]
    history = daily[:-1] if len(daily) > 1 else []
    baseline = (sum(d["cost_normalized_cents"] for d in history) / len(history)
                if history else None)
    today_row = daily[-1] if daily else {}
    today_cost = float(today_row.get("cost_normalized_cents") or 0.0)
    ratio = round(today_cost / baseline, 6) if baseline else None
    return {
        "today": today_row,
        "daily": daily,
        "week": utc_weekly(today.isoformat(), directory=directory),
        "baseline_cents_per_day": None if baseline is None else round(baseline, 6),
        "baseline_days": len(history),
        "ratio": ratio,
        "thresholds": {"fasting_in": 1.3, "fasting_out": 1.1, "cooldown_hours": 12,
                       "owner": ("S5-03 已实现断食状态机："
                                 "agent.monitoring.cost_brake（本快照是其数据源）")},
        "anchor_model": resolve_anchor_model()[0],
        "calibration": calibration_block(),
    }


def write_utc_snapshot(path: Optional[str] = None, *, days: int = 7,
                       directory: Optional[str] = None) -> Dict[str, Any]:
    """输出 `data/utc_snapshot.json`（S5-03 断食/刹车的数据源声明）"""
    snapshot = utc_snapshot(days=days, directory=directory)
    target = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "utc_snapshot.json")
    try:
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    except OSError as e:  # noqa: BLE001 best-effort
        logger.warning("写出 utc_snapshot.json 失败: %s", e)
    return snapshot


# ════════════════════════════════════════════════════════════
#  与既有成本监控口径对账（§四验收：与既有数据源一致）
# ════════════════════════════════════════════════════════════

#: 对账用探针（每种模型的同一 token 组合）
RECONCILE_PROBE = {"tokens_in": 1234, "tokens_out": 567}


def reconcile_pricing(models: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """归一化公式 vs 既有成本监控公式 → **逐模型对账**（默认系数下应逐分一致）

    既有口径（`agent.model_router.cost_tracker.CostTracker.record`）：
    ``cost = in/1000*price_in + out/1000*price_out``（USD，round 6）。
    本模块归一成本（默认价格比系数）应与之在 **cents** 上相等（USD×100）。
    """
    names = list(models) if models else sorted(set(model_costs().keys())
                                               | {"unknown-model-probe"})
    rows: List[Dict[str, Any]] = []
    consistent = True
    for name in names:
        own = price_usd_per_1k(name)
        legacy_usd = (RECONCILE_PROBE["tokens_in"] / 1000.0 * own["input"]
                      + RECONCILE_PROBE["tokens_out"] / 1000.0 * own["output"])
        calc = normalize_cost(tokens_in=RECONCILE_PROBE["tokens_in"],
                             tokens_out=RECONCILE_PROBE["tokens_out"], model=name)
        legacy_cents = round(legacy_usd * 100.0, 6)
        delta = round(calc["cost_normalized_cents"] - legacy_cents, 9)
        ok = abs(delta) < 1e-6
        consistent = consistent and ok
        rows.append({
            "model": name,
            "legacy_cents": legacy_cents,
            "normalized_cents": calc["cost_normalized_cents"],
            "delta_cents": delta,
            "consistent": ok,
            "coefficient_source": calc["coefficient_source"],
        })
    return {
        "probe": dict(RECONCILE_PROBE),
        "anchor_model": resolve_anchor_model()[0],
        "consistent": consistent,
        "rows": rows,
        "note": ("默认系数（price_ratio）下归一成本与既有 cost_tracker 口径逐分一致；"
                 "经 CP_UTC_COEFFICIENTS 覆盖系数后按校准口径偏离属预期"),
    }


def reconcile_cost_log(path: Optional[str] = None, *,
                       directory: Optional[str] = None) -> Dict[str, Any]:
    """与旧成本轨**数据文件**对账（`data/cost_log.jsonl`）—— **纯对账工具**

    【S2-03 #9 · Owner 裁定 D（2026-09-11）】本函数**已降级**：旧轨 `cost_log.jsonl`
    已**停写**，成本唯一数据源是事件流（`record_cost()` → `data/events/`）。
    因此本函数**不再是数据源**，只在下列场景使用：

    - 迁移期核对「旧轨历史金额」与「同 token 组合按现行归一公式重算」是否一致
      （`delta_cents` / `consistent`）；
    - 排查"收敛后是否还有调用方往旧轨记账"（配合 `cost_tracker.legacy_track_status()`）。

    返回里显式带 `role` / `authoritative_source` / `calibration`，避免被误当数据源。
    文件缺失时如实返回 ``rows=0`` 而非伪造一致（不臆造）。
    """
    target = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "cost_log.jsonl")
    base: Dict[str, Any] = {
        "path": target,
        "role": LEGACY_COST_LOG_ROLE,
        "authoritative_source": COST_SOURCE_OF_TRUTH,
        "calibration": calibration_block(),
    }
    if not os.path.exists(target):
        return {**base, "rows": 0, "consistent": None,
                "note": ("cost_log.jsonl 不存在（旧轨已停写，属预期；"
                         "成本唯一数据源＝事件流）")}
    legacy_cents = 0.0
    rows = 0
    normalized_cents = 0.0
    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                rows += 1
                legacy_cents += float(rec.get("cost_usd") or 0.0) * 100.0
                calc = normalize_cost(
                    tokens_in=int(rec.get("input_tokens") or 0),
                    tokens_out=int(rec.get("output_tokens") or 0),
                    model=str(rec.get("model") or ""))
                normalized_cents += calc["cost_normalized_cents"]
    except OSError as e:
        return {**base, "rows": 0, "consistent": None, "note": f"读取失败: {e}"}
    delta = round(normalized_cents - legacy_cents, 6)
    return {
        **base,
        "rows": rows,
        "legacy_cents": round(legacy_cents, 6),
        "normalized_cents": round(normalized_cents, 6),
        "delta_cents": delta,
        "consistent": abs(delta) < 1e-6 if rows else None,
        "note": ("逐行按同一 token 组合重算比对（空文件 → consistent=None）；"
                 "本函数为**只读对账**，不参与成本口径"),
    }


__all__ = [
    "DEFAULT_ANCHOR_MODEL", "ENV_ANCHOR_MODEL", "ENV_COEFFICIENTS",
    "ENV_PRICE_OVERRIDES", "DEFAULT_MODEL_PRICE_USD_PER_1K", "UTCNormalizationError",
    "COST_SCHEMA_VERSION", "CALIBRATION_VERSION", "CALIBRATION_NOTE",
    "CALIBRATION_TRIGGER", "COST_SOURCE_OF_TRUTH", "LEGACY_COST_LOG_ROLE",
    "calibration_block",
    "model_costs", "price_usd_per_1k", "resolve_anchor_model", "anchor_prices_cents",
    "coefficient", "coefficient_table", "normalize_cost", "record_cost",
    "reset_config_cache",
    "utc_daily", "utc_window", "utc_weekly", "utc_snapshot", "write_utc_snapshot",
    "reconcile_pricing", "reconcile_cost_log", "RECONCILE_PROBE",
]
