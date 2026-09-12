"""判定集**构建成本**采集与聚合（TASK-S7-06 步骤 1 / 收官审计 §8.5 残留 R1）

## 这份模块解决什么

S3-03 的 `ROIReport` 只消费 `utc.utc_window()` 的**持续运营成本**，判定集的
**一次性建造成本**（生成用例的 LLM 调用、人工抽检工时、回放算力）不在公式里
（审计 T1 残留）。本模块补齐**采集侧**：判定集构建路径写 `cost` 事件并标注
``stage=case_build``，聚合侧按能力/通道给出可追溯的金额、区间与估计方法。

## 为什么单独一条成本流（而不是写进 `data/events/`）

判定集构建是**一次性资产投入**，上游单位成本是**持续运营成本**；两者混进同一条
成本流会让 ``UTC = cost_normalized_cents / 任务数`` 被一次性投入污染 —— 而那正是
S3-03 条件③要比较的两侧（「月省 > 自研一次性投入 ÷ 12」）。故本模块把
``stage=case_build`` 的成本事件写进**独立目录**（默认 ``<判定集根>/_case_cost/``），
`utc_window()` 因此**看不到**它们，口径互不污染。

## 隔离约定（S3-02/S3-03 两次落盘污染的教训）

成本目录**由判定集根派生**（``CP_DIGESTION_CASE_DIR`` → ``<根>/_case_cost``），
单测只需把判定集根指到 ``tmp_path``，成本台账随之落进临时目录；``CP_DIGESTION_CASE_COST_DIR``
可显式覆盖。

## 不得编造数字（本模块的硬纪律）

- 无事件 ⇒ ``total_cents=None``（**不以 0 冒充**）；
- 人工工时/回放算力**费率未配置时计 0 并显式标注**为「未计价」⇒ 点值是**下界**，
  区间右端 ``high_cents=None``（不可估），绝不臆造费率；
- 每个金额都带 ``estimation_method``（估计方法）+ ``events``（样本量）+ ``source``。

**import 纪律**：与 `agent.digestion` 其余模块一致 —— `agent.observability.*`
一律在**函数体内**懒加载，导入期零副作用（本模块可被 `cases.py` 安全导入）。

**不引入循环依赖**：本模块**不导入** `agent.digestion.cases`；通道归因按
``CASE_KIND_*`` 的稳定字符串字面量映射（值有变更时由 `cases.py` 的回归测试兜住）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("agent.digestion.case_cost")

# ════════════════════════════════════════════════════════════
#  常量（口径单点定义）
# ════════════════════════════════════════════════════════════

#: 事件标注（R1 的验收点：``stage=case_build`` 必须可查）
CASE_BUILD_STAGE = "case_build"
#: 成本类别（与 stage 同值，便于按类别过滤而不必理解 stage 语义）
CASE_BUILD_COST_CLASS = "case_build"

#: 成本台账目录：判定集根下的子目录（**派生** ⇒ 测试隔离自动生效）
CASE_COST_SUBDIR = "_case_cost"
#: 显式覆盖成本台账目录（未设则按判定集根派生）
CASE_COST_DIR_ENV = "CP_DIGESTION_CASE_COST_DIR"
#: 判定集根环境变量（与 `cases.CASE_ROOT_ENV` 同值；此处复写常量以免反向导入）
CASE_ROOT_ENV = "CP_DIGESTION_CASE_DIR"

#: 人工复核工时单价（分/分钟）——**未配置即"未计价"（计 0 并标注下界）**，不臆造费率
MANUAL_RATE_ENV = "CP_DIGESTION_CASE_BUILD_MANUAL_RATE_CENTS"
#: 回放算力单价（分/CPU 秒）——同上
REPLAY_RATE_ENV = "CP_DIGESTION_CASE_BUILD_REPLAY_RATE_CENTS"

#: 构建通道（与 `cases.CASE_KIND_*` 对齐；通道名用于成本归因）
CHANNEL_SEED = "seed_pack"
CHANNEL_TRACE = "trace"
CHANNEL_LLM = "llm"
CHANNEL_MANUAL = "manual_review"
CHANNEL_OTHER = "other"
CHANNELS: Tuple[str, ...] = (CHANNEL_SEED, CHANNEL_TRACE, CHANNEL_LLM,
                            CHANNEL_MANUAL, CHANNEL_OTHER)

#: 用例 kind → 构建通道（`cases.CASE_KIND_*` 的值；保持字面量以免循环导入）
_KIND_TO_CHANNEL: Dict[str, str] = {
    "seed": CHANNEL_SEED,
    "trace": CHANNEL_TRACE,
    "llm": CHANNEL_LLM,
    "manual": CHANNEL_MANUAL,
}

#: 数据源标签（进 ROI 报告，便于追溯"这个数从哪来"）
SOURCE_CASE_BUILD_COST = "agent.digestion.case_cost（cost 事件 stage=case_build）"

#: 估计方法标签（逐项显式，报告不得只给数字不给方法）
METHOD_LLM_PRICED = ("LLM 调用：实测 token 数 × S2-03 价格锚定系数"
                     "（utc.normalize_cost，与 UTC 口径同源）")
METHOD_MANUAL_PRICED = "人工复核工时：实测分钟数 × 配置单价（{rate} 分/分钟）"
METHOD_MANUAL_UNPRICED = ("人工复核工时：实测 {minutes} 分钟，**单价未配置 ⇒ 计 0**"
                          "（点值为下界；配置 {env} 后区间右端可估）")
METHOD_REPLAY_PRICED = "回放算力：实测 CPU 秒 × 配置单价（{rate} 分/CPU 秒）"
METHOD_REPLAY_UNPRICED = ("回放算力：实测 {cpu_ms} ms，**单价未配置 ⇒ 计 0**"
                          "（点值为下界；配置 {env} 后区间右端可估）")
METHOD_EXPLICIT = "调用方显式给定金额（extra_cents）"
METHOD_MANUAL_UNRECORDED = ("人工复核工时：**未登记**（构建路径未记录工时）⇒ "
                            "点值不含人工成本，属**下界**（不臆造工时时长）")
METHOD_NO_EVENTS = ("无 case_build 成本事件 ⇒ **不可得（记 None，不以 0 冒充）**；"
                    "埋点在 CaseStore 落库路径（新版本判定集）")

#: 聚合结果的 caveat（进 ROI 报告的口径提醒）
CAVEAT_SINGLE_LINE = ("判定集构建成本**单列披露、不参与摊销**（一次性资产；混入摊销会"
                      "扭曲『是否内化』的月成本比较）——报告同时给出不含/含两种 ROI")
CAVEAT_NOT_UPSTREAM = ("本金额**不含**在上游单位成本（utc.utc_window）中："
                       "case_build 成本事件写在独立目录，CTA/UTC 口径不受影响")
CAVEAT_LOWER_BOUND = ("存在未计价/未登记项（人工工时/回放算力）⇒ "
                      "total_cents 是**下界**，不是完整成本")

#: 聚合输出的稳定键（供测试与报告引用）
SUMMARY_KEYS: Tuple[str, ...] = (
    "available", "total_cents", "low_cents", "high_cents", "events", "samples",
    "llm_cents", "manual_cents", "replay_cents", "explicit_cents",
    "repriced_manual_cents", "repriced_replay_cents",
    "by_capability", "by_channel", "channels", "unpriced",
    "estimated", "lower_bound", "estimation_method", "source", "directory",
    "window", "caveats", "cases",
)


# ════════════════════════════════════════════════════════════
#  目录解析与 store 复用
# ════════════════════════════════════════════════════════════


def default_case_root() -> str:
    """判定集根（与 `cases.default_case_root()` 同口径；此处复写以免反向导入）"""
    from .cases import default_case_root as _root
    return _root()


def case_cost_dir(*, case_root: str = "", directory: str = "") -> str:
    """成本台账目录：显式 ``directory`` > ``CP_DIGESTION_CASE_COST_DIR`` > ``<判定集根>/_case_cost``"""
    explicit = str(directory or os.environ.get(CASE_COST_DIR_ENV, "") or "").strip()
    if explicit:
        return explicit
    root = str(case_root or "").strip() or default_case_root()
    return os.path.join(root, CASE_COST_SUBDIR)


_STORE_LOCK = threading.Lock()
_STORES: Dict[str, Any] = {}


def _store_for(directory: str) -> Any:
    """取该目录的写侧 EventStore（**一目录一 writer**，§5.5 单写者纪律）

    本模块自己缓存 store（不走 `get_event_store()` 的进程单例），因为默认事件流
    与 case_build 成本流是**两条互不污染的流**。缓存键是规范化后的目录名，
    同一进程内重复调用复用同一 writer。
    """
    from agent.observability.events import EventStore, active_events_path
    key = os.path.normcase(os.path.abspath(directory))
    with _STORE_LOCK:
        store = _STORES.get(key)
        if store is not None and not getattr(store, "closed", False):
            return store
        # archive=False：成本台账不参与日志轮转（避免与既有归档器争同一批文件）
        store = EventStore(active_events_path(directory), archive=False)
        _STORES[key] = store
        return store


def reset_case_cost_stores() -> None:
    """关闭并清除本模块的 store 缓存（**测试专用**；不影响默认事件流）"""
    with _STORE_LOCK:
        stores = list(_STORES.values())
        _STORES.clear()
    for store in stores:
        try:
            store.close()
        except Exception as e:  # noqa: BLE001 收尾失败不影响断言
            logger.debug("case 成本 store 关闭失败: %s", e)


def _env_rate(name: str) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，按未配置处理（0）", name, raw)
        return 0.0
    return max(0.0, value)


# ════════════════════════════════════════════════════════════
#  通道归因
# ════════════════════════════════════════════════════════════


def channel_of_kind(kind: Any) -> str:
    """用例 kind → 构建通道（未知 kind 归 ``other``，**不猜**）"""
    return _KIND_TO_CHANNEL.get(str(kind or "").strip().lower(), CHANNEL_OTHER)


def channel_attribution(kind_counts: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    """``{kind: 用例数}`` → ``{通道: 用例数}``（多 kind 映射到同一通道时求和）"""
    out: Dict[str, int] = {}
    for kind, count in dict(kind_counts or {}).items():
        try:
            number = int(count or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        channel = channel_of_kind(kind)
        out[channel] = out.get(channel, 0) + number
    return {k: out[k] for k in sorted(out)}


# ════════════════════════════════════════════════════════════
#  成本事件模型
# ════════════════════════════════════════════════════════════


@dataclass
class CaseBuildCostInput:
    """一次判定集构建的成本输入（**逐项可追溯**；缺项即 0 并在事件里标注）"""

    capability_id: str = ""
    version: int = 1
    cases: int = 0
    channels: Dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    cache_hit: bool = False
    manual_review_minutes: float = 0.0
    replay_cpu_ms: float = 0.0
    extra_cents: float = 0.0
    source: str = ""
    note: str = ""

    def to_event_payload(self, *, manual_rate: float, replay_rate: float) -> Dict[str, Any]:
        """→ 事件载荷（含逐项金额与单价，并把"未计价"如实标出来）"""
        from agent.observability.utc import normalize_cost
        calc = normalize_cost(tokens_in=int(self.tokens_in or 0),
                              tokens_out=int(self.tokens_out or 0),
                              model=str(self.model or ""),
                              cache_hit=bool(self.cache_hit))
        minutes = max(0.0, float(self.manual_review_minutes or 0.0))
        cpu_ms = max(0.0, float(self.replay_cpu_ms or 0.0))
        manual_cents = round(minutes * max(0.0, manual_rate), 6)
        replay_cents = round((cpu_ms / 1000.0) * max(0.0, replay_rate), 6)
        explicit = round(max(0.0, float(self.extra_cents or 0.0)), 6)
        llm_cents = float(calc.get("cost_normalized_cents") or 0.0)
        total = round(llm_cents + manual_cents + replay_cents + explicit, 6)
        return {
            "stage": CASE_BUILD_STAGE,
            "cost_class": CASE_BUILD_COST_CLASS,
            "capability_id": str(self.capability_id or ""),
            "version": int(self.version or 0),
            "cases": int(self.cases or 0),
            "channels": {str(k): int(v) for k, v in dict(self.channels or {}).items()},
            "source": str(self.source or ""),
            "note": str(self.note or ""),
            "manual_review_minutes": round(minutes, 3),
            "manual_rate_cents_per_minute": round(max(0.0, manual_rate), 6),
            "manual_cost_cents": manual_cents,
            "replay_cpu_ms": round(cpu_ms, 3),
            "replay_rate_cents_per_cpu_sec": round(max(0.0, replay_rate), 6),
            "replay_cost_cents": replay_cents,
            "explicit_cents": explicit,
            "llm_cost_cents": round(llm_cents, 6),
            "total_cents": total,
            **calc,
        }


def record_case_build_cost(cost: Optional[CaseBuildCostInput] = None,
                           *, store: Any = None, directory: str = "",
                           case_root: str = "", ts: Optional[str] = None,
                           correlation_id: str = "", idempotency_key: str = "",
                           actor: str = "", **overrides: Any) -> Any:
    """记录一次判定集构建成本（``cost`` 事件，``stage=case_build``）

    幂等键默认 ``case_build:<capability_id>:v<version>``：同一判定集版本的重复落库
    **不重复计数**（events.v1 的幂等纪律）；需要按通道拆账时显式传 ``idempotency_key``。

    **best-effort**：埋点失败绝不阻断判定集构建主路径（返回 ``None`` 并记日志）。

    Args:
        cost: 成本输入；缺省用 ``overrides`` 构造（如 ``capability_id=...``）。
        store: 显式 EventStore（测试用）；缺省按目录取本模块缓存的 writer。
        directory/case_root: 成本台账目录/判定集根（见 `case_cost_dir`）。
        ts/correlation_id/actor: 透传给 events.v1 信封。

    Returns:
        ``EventEnvelope`` 或 ``None``（事件禁用/写入失败）。
    """
    try:
        data = cost if cost is not None else CaseBuildCostInput(**dict(overrides))
    except TypeError as e:  # 埋点是 telemetry：参数写错也只警告，不阻断构建
        logger.warning("判定集构建成本埋点参数非法（不阻断构建）: %s", e)
        return None
    if cost is not None and overrides:
        for key, value in overrides.items():
            setattr(data, key, value)
    manual_rate = _env_rate(MANUAL_RATE_ENV)
    replay_rate = _env_rate(REPLAY_RATE_ENV)
    payload = data.to_event_payload(manual_rate=manual_rate, replay_rate=replay_rate)
    target_dir = case_cost_dir(case_root=case_root, directory=directory)
    key = str(idempotency_key or "").strip() or (
        f"case_build:{payload['capability_id']}:v{payload['version']}")
    try:
        from agent.observability.events import ACTOR_AUTO, EV_COST, emit
        target = store if store is not None else _store_for(target_dir)
        return emit(EV_COST, payload, actor=str(actor or ACTOR_AUTO),
                    correlation_id=str(correlation_id or ""),
                    idempotency_key=key, ts=ts, store=target)
    except Exception as e:  # noqa: BLE001 埋点不得阻断构建主路径
        logger.warning("判定集构建成本埋点失败（不阻断构建）: %s: %s",
                       type(e).__name__, e)
        return None


def record_case_build_from_case_set(case_set: Any, *,
                                    cost_context: Optional[Mapping[str, Any]] = None,
                                    store: Any = None, directory: str = "",
                                    case_root: str = "", ts: Optional[str] = None,
                                    actor: str = "") -> Any:
    """从判定集对象记录构建成本（通道按用例 kind 自动归因）

    ``cost_context`` 可带：``tokens_in`` / ``tokens_out`` / ``model`` / ``cache_hit`` /
    ``manual_review_minutes`` / ``replay_cpu_ms`` / ``extra_cents`` / ``source`` / ``note``
    —— 都是**调用方实测**的量；缺省即 0 并在事件里如实为 0（不臆造成本）。
    """
    context = dict(cost_context or {})
    known = set(CaseBuildCostInput.__dataclass_fields__)
    cost = CaseBuildCostInput(
        capability_id=str(getattr(case_set, "capability_id", "") or ""),
        version=int(getattr(case_set, "version", 1) or 1),
        cases=int(getattr(case_set, "size", 0) or 0),
        channels=channel_attribution(
            (case_set.kind_counts() if hasattr(case_set, "kind_counts")
             else context.get("kind_counts")) or {}),
        **{k: v for k, v in context.items() if k in known})
    return record_case_build_cost(cost, store=store, directory=directory,
                                  case_root=case_root, ts=ts, actor=actor)


# ════════════════════════════════════════════════════════════
#  聚合（ROI 报告的数据源）
# ════════════════════════════════════════════════════════════


def _payload_num(payload: Mapping[str, Any], key: str) -> float:
    """载荷里的数值字段（非法/缺失一律 0；**不猜**）"""
    try:
        return float(payload.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _payload_int(payload: Mapping[str, Any], key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _fold_case_build(rows: Any, *, capability_id: str = "") -> Dict[str, Any]:
    totals: Dict[str, Any] = {
        "events": 0, "cases": 0, "llm_cents": 0.0, "manual_cents": 0.0,
        "replay_cents": 0.0, "explicit_cents": 0.0, "total_cents": 0.0,
        "manual_minutes": 0.0, "replay_cpu_ms": 0.0,
        "unpriced_manual_minutes": 0.0, "unpriced_replay_cpu_ms": 0.0,
        "by_capability": {}, "by_channel": {}, "capabilities": set(),
        "versions": {}, "models": {},
    }
    for env in rows:
        payload = dict(getattr(env, "payload", None) or {})
        if str(payload.get("stage") or "") != CASE_BUILD_STAGE:
            continue
        cid = str(payload.get("capability_id") or "")
        if capability_id and cid != capability_id:
            continue
        llm = _payload_num(payload, "llm_cost_cents") or _payload_num(
            payload, "cost_normalized_cents")
        manual = _payload_num(payload, "manual_cost_cents")
        replay = _payload_num(payload, "replay_cost_cents")
        explicit = _payload_num(payload, "explicit_cents")
        total = _payload_num(payload, "total_cents")
        if not total:
            total = llm + manual + replay + explicit
        minutes = _payload_num(payload, "manual_review_minutes")
        cpu_ms = _payload_num(payload, "replay_cpu_ms")
        cases = _payload_int(payload, "cases")

        totals["events"] += 1
        totals["cases"] += cases
        totals["llm_cents"] += llm
        totals["manual_cents"] += manual
        totals["replay_cents"] += replay
        totals["explicit_cents"] += explicit
        totals["total_cents"] += total
        totals["manual_minutes"] += minutes
        totals["replay_cpu_ms"] += cpu_ms
        if minutes > 0 and manual <= 0.0:
            totals["unpriced_manual_minutes"] += minutes
        if cpu_ms > 0 and replay <= 0.0:
            totals["unpriced_replay_cpu_ms"] += cpu_ms
        if cid:
            totals["capabilities"].add(cid)
            entry = totals["by_capability"].setdefault(
                cid, {"events": 0, "cases": 0, "total_cents": 0.0})
            entry["events"] += 1
            entry["cases"] += cases
            entry["total_cents"] = round(entry["total_cents"] + total, 6)
            versions = totals["versions"].setdefault(cid, [])
            version = _payload_int(payload, "version")
            if version and version not in versions:
                versions.append(version)
        for channel, count in dict(payload.get("channels") or {}).items():
            bucket = totals["by_channel"].setdefault(
                str(channel), {"cases": 0, "events": 0})
            try:
                bucket["cases"] += int(count or 0)
            except (TypeError, ValueError):
                pass
            bucket["events"] += 1
        model = str(payload.get("model") or "")
        if model:
            totals["models"][model] = totals["models"].get(model, 0) + 1
    return totals


def case_build_cost_window(*, start: str = "", end: str = "", capability_id: str = "",
                           directory: str = "", case_root: str = "",
                           manual_rate_cents: Optional[float] = None,
                           replay_rate_cents: Optional[float] = None,
                           rows: Any = None) -> Dict[str, Any]:
    """聚合判定集构建成本 → ROI 报告可用的**区间估计 + 方法标注**

    Args:
        start/end: 时间窗（ISO 前缀比较；``start=""`` 即**不设下界** —— 判定集是一次性
            资产，构建可能早于 ROI 评估窗口，故 ROI 侧默认按**全时**统计）。
        capability_id: 只统计该能力（空 = 全部能力）。
        directory/case_root: 台账目录/判定集根。
        manual_rate_cents/replay_rate_cents: 补估单价（**仅用于区间右端**，不覆盖事件里
            已计的金额）；未给则取环境变量；仍为 0 ⇒ 右端不可估（``high_cents=None``）。
        rows: 显式事件行（测试用；给定时不读盘）。

    Returns:
        dict（键见 `SUMMARY_KEYS`）：``total_cents`` 无事件时为 ``None``（不以 0 冒充）。
    """
    target_dir = case_cost_dir(case_root=case_root, directory=directory)
    events: List[Any] = []
    load_error = ""
    if rows is None:
        try:
            from agent.observability.events import EV_COST, iter_events
            events = iter_events(since=(start or None), until=(end or None),
                                 types=(EV_COST,), directory=target_dir)
        except Exception as e:  # noqa: BLE001 台账不可用 ⇒ 如实标注，不猜
            load_error = f"{type(e).__name__}: {e}"
    else:
        events = list(rows)

    totals = _fold_case_build(events, capability_id=capability_id)
    manual_rate = (_env_rate(MANUAL_RATE_ENV) if manual_rate_cents is None
                   else max(0.0, float(manual_rate_cents)))
    replay_rate = (_env_rate(REPLAY_RATE_ENV) if replay_rate_cents is None
                   else max(0.0, float(replay_rate_cents)))
    repriced_manual = round(totals["unpriced_manual_minutes"] * manual_rate, 6)
    repriced_replay = round((totals["unpriced_replay_cpu_ms"] / 1000.0) * replay_rate, 6)
    unpriced = (totals["unpriced_manual_minutes"] > 0
                or totals["unpriced_replay_cpu_ms"] > 0)
    available = totals["events"] > 0
    manual_unrecorded = available and totals["manual_minutes"] <= 0.0

    methods: List[str] = []
    if available:
        if totals["llm_cents"] > 0:
            methods.append(METHOD_LLM_PRICED)
        if totals["manual_minutes"] > 0:
            methods.append(METHOD_MANUAL_PRICED.format(rate=manual_rate)
                           if totals["manual_cents"] > 0 else
                           METHOD_MANUAL_UNPRICED.format(
                               minutes=round(totals["manual_minutes"], 3),
                               env=MANUAL_RATE_ENV))
        else:
            methods.append(METHOD_MANUAL_UNRECORDED)
        if totals["replay_cpu_ms"] > 0:
            methods.append(METHOD_REPLAY_PRICED.format(rate=replay_rate)
                           if totals["replay_cents"] > 0 else
                           METHOD_REPLAY_UNPRICED.format(
                               cpu_ms=round(totals["replay_cpu_ms"], 3),
                               env=REPLAY_RATE_ENV))
        if totals["explicit_cents"] > 0:
            methods.append(METHOD_EXPLICIT)
    else:
        methods.append(METHOD_NO_EVENTS)

    total_cents = round(totals["total_cents"], 6) if available else None
    high_cents = None
    if available:
        high_cents = round(total_cents + repriced_manual + repriced_replay, 6) \
            if not unpriced or (manual_rate > 0 or replay_rate > 0) else None
        if unpriced and high_cents is not None and high_cents == total_cents:
            # 未计价项存在但补估单价仍为 0 ⇒ 右端不可估（不臆造）
            high_cents = None
    caveats = [CAVEAT_SINGLE_LINE, CAVEAT_NOT_UPSTREAM]
    if unpriced or manual_unrecorded:
        caveats.append(CAVEAT_LOWER_BOUND)
    if load_error:
        caveats.append(f"成本台账读取失败（按不可得处理，不猜）: {load_error}")

    window: Dict[str, Any] = {"start": str(start or ""), "end": str(end or ""),
                              "scope": "all_time" if not start else "bounded"}
    return {
        "available": available,
        "total_cents": total_cents,
        "low_cents": total_cents,
        "high_cents": high_cents,
        "events": int(totals["events"]),
        "samples": int(totals["events"]),
        "cases": int(totals["cases"]),
        "llm_cents": round(totals["llm_cents"], 6),
        "manual_cents": round(totals["manual_cents"], 6),
        "replay_cents": round(totals["replay_cents"], 6),
        "explicit_cents": round(totals["explicit_cents"], 6),
        "repriced_manual_cents": repriced_manual,
        "repriced_replay_cents": repriced_replay,
        "by_capability": {k: dict(v) for k, v in sorted(totals["by_capability"].items())},
        "by_channel": {k: dict(v) for k, v in sorted(totals["by_channel"].items())},
        "channels": sorted(totals["by_channel"]),
        "capabilities": sorted(totals["capabilities"]),
        "versions": {k: sorted(v) for k, v in sorted(totals["versions"].items())},
        "models": dict(sorted(totals["models"].items())),
        "unpriced": {
            "manual_review_minutes": round(totals["unpriced_manual_minutes"], 3),
            "replay_cpu_ms": round(totals["unpriced_replay_cpu_ms"], 3),
            "manual_cost_unrecorded": bool(manual_unrecorded),
            "manual_rate_cents_per_minute": manual_rate,
            "replay_rate_cents_per_cpu_sec": replay_rate,
            "manual_rate_env": MANUAL_RATE_ENV,
            "replay_rate_env": REPLAY_RATE_ENV,
        },
        "estimated": bool(unpriced or manual_unrecorded),
        "lower_bound": bool(unpriced or manual_unrecorded),
        "estimation_method": "；".join(methods),
        "source": SOURCE_CASE_BUILD_COST,
        "directory": target_dir,
        "window": window,
        "caveats": caveats,
        "load_error": load_error,
    }


def case_build_cost_ledger(*, directory: str = "", case_root: str = "",
                           limit: int = 500) -> List[Dict[str, Any]]:
    """成本事件明细（供验收报告/面板取证：逐条带 stage/capability/金额/方法）"""
    target_dir = case_cost_dir(case_root=case_root, directory=directory)
    try:
        from agent.observability.events import EV_COST, iter_events
        rows = iter_events(types=(EV_COST,), directory=target_dir, limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("成本事件明细读取失败: %s: %s", type(e).__name__, e)
        return []
    out: List[Dict[str, Any]] = []
    for env in rows:
        payload = dict(getattr(env, "payload", None) or {})
        if str(payload.get("stage") or "") != CASE_BUILD_STAGE:
            continue
        out.append({"ts": str(getattr(env, "ts", "") or ""),
                    "event_id": str(getattr(env, "event_id", "") or ""),
                    "capability_id": str(payload.get("capability_id") or ""),
                    "version": int(payload.get("version") or 0),
                    "cases": int(payload.get("cases") or 0),
                    "channels": dict(payload.get("channels") or {}),
                    "total_cents": float(payload.get("total_cents") or 0.0),
                    "manual_review_minutes": float(
                        payload.get("manual_review_minutes") or 0.0),
                    "replay_cpu_ms": float(payload.get("replay_cpu_ms") or 0.0),
                    "stage": str(payload.get("stage") or "")})
    return out


def case_build_cost_markdown(summary: Mapping[str, Any]) -> str:
    """成本摘要的人读形式（进 ROI 报告；**方法与样本一并披露**）"""
    lines = [
        "### 判定集构建成本（一次性投入 · 单列披露）", "",
        "| 项 | 值 |", "|---|---|",
        f"| 可采集 | {bool(summary.get('available'))} |",
        f"| 点值（下界） | {_show(summary.get('total_cents'))} 分 |",
        f"| 区间右端 | {_show(summary.get('high_cents'))}"
        "（未配置单价时不可估 ⇒ 记 `None`） |",
        f"| LLM 调用 | {_show(summary.get('llm_cents'))} 分 |",
        f"| 人工复核工时 | {_show(summary.get('manual_cents'))} 分 |",
        f"| 回放算力 | {_show(summary.get('replay_cents'))} 分 |",
        f"| 显式给定 | {_show(summary.get('explicit_cents'))} 分 |",
        f"| 事件样本 | {summary.get('events')} 条（覆盖 {len(summary.get('capabilities') or [])} 个能力） |",
        f"| 估计方法 | {summary.get('estimation_method')} |",
        f"| 数据源 | `{summary.get('source')}` |",
        f"| 台账目录 | `{summary.get('directory')}` |",
    ]
    channels = dict(summary.get("by_channel") or {})
    if channels:
        lines += ["", "通道归因（用例数）："
                  + "、".join(f"{k}={v.get('cases')}" for k, v in channels.items())]
    if summary.get("caveats"):
        lines += ["", "口径提醒："] + [f"- {c}" for c in summary["caveats"]]
    return "\n".join(lines) + "\n"


def _show(value: Any) -> str:
    if value is None:
        return "—（不可得，不以 0 冒充）"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


__all__ = [
    "CASE_BUILD_STAGE", "CASE_BUILD_COST_CLASS", "CASE_COST_SUBDIR",
    "CASE_COST_DIR_ENV", "CASE_ROOT_ENV", "MANUAL_RATE_ENV", "REPLAY_RATE_ENV",
    "CHANNEL_SEED", "CHANNEL_TRACE", "CHANNEL_LLM", "CHANNEL_MANUAL",
    "CHANNEL_OTHER", "CHANNELS", "SOURCE_CASE_BUILD_COST", "SUMMARY_KEYS",
    "METHOD_LLM_PRICED", "METHOD_MANUAL_PRICED", "METHOD_MANUAL_UNPRICED",
    "METHOD_REPLAY_PRICED", "METHOD_REPLAY_UNPRICED", "METHOD_EXPLICIT",
    "METHOD_NO_EVENTS", "CAVEAT_SINGLE_LINE", "CAVEAT_NOT_UPSTREAM",
    "CAVEAT_LOWER_BOUND",
    "default_case_root", "case_cost_dir", "reset_case_cost_stores",
    "channel_of_kind", "channel_attribution", "CaseBuildCostInput",
    "record_case_build_cost", "record_case_build_from_case_set",
    "case_build_cost_window", "case_build_cost_ledger", "case_build_cost_markdown",
]
