# -*- coding: utf-8 -*-
"""报警器采集（2026-11-11）：从**已经在跑**的采集器里取"确实有问题"的信号。

【为什么不新造探测】
系统里已有三处真实测量，报警器只做投影（一遍扫描、不重复探测）：
1. 健康探针（agent/health/collector.py，60s 一轮，落 data/health/history-*.jsonl）
   → L1/L2/L3/L4 分数 **低于阈值**才报警；`score=None`（无数据）**不报警**（L5：
   无数据不是故障），也不替人把旧告警 resolve 掉（那等于伪造"已恢复"）。
2. 功能单位看板（agent/monitoring/unit_monitor.py）→ `warn`/`error` 的单位报警；
   `never`（从未产生数据）**不报警**，`off`（已关闭）不报警 —— 后者可能是有意为之，
   拿它报警会把"设计如此"变成噪音（需要时由 Owner 显式调 `UNIT_ALARM_STATUSES`）。
3. 审计链（agent/audit/chain.py）→ 尾部窗口重算不一致 = critical。
   全链核验很贵（8 万条），这里只验尾部 `AUDIT_TAIL_WINDOW` 条且**每小时最多一次**；
   全量核验仍由 `scripts/handoff_snapshot.py` 的交接卡负责，两者不互相替代。

【纪律】
- 单轮失败只记日志，不中断循环（与健康采集线程同族）；
- 采集到"数据源不可用"时**跳过该源**，不让它把旧告警冲掉；
- 开关 `CP_ALARM_COLLECTOR`（默认开，已进 agent/settings/registry.py）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

from agent.alarms.store import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    AlarmStore,
    get_store,
)

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 采集间隔（秒）：与健康采集线程同频（探针 60s 一轮，再快也拿不到新数据）
COLLECT_INTERVAL = 60

#: 低于该分数才算"降级"（与健康仪表盘 levelOf 的 good 阈值同源：0.8）
HEALTH_DEGRADE_SCORE = 0.8
#: 低于该分数算 critical（float 判断，不做四舍五入，避免 0.795 被当成 0.8）
HEALTH_CRITICAL_SCORE = 0.5

#: 单位看板里算"有问题"的状态（never/off 不报警，理由见模块 docstring）
UNIT_ALARM_STATUSES = ("warn", "error")

#: 审计链尾部核验窗口（条）与最小间隔（秒）
AUDIT_TAIL_WINDOW = 500
AUDIT_RECHECK_SEC = 3600

#: 健康层的中文名（与前端 PROBE_LAYERS 的 label 同源；两边都改才算改）
LAYER_LABELS: Dict[str, str] = {
    "l1_process": "L1 进程",
    "l2_dependency": "L2 依赖",
    "l3_llm_tool": "L3 LLM/工具",
    "l4_business": "L4 业务",
    "l5_semantic": "L5 语义",
}

ENV_FLAG = "CP_ALARM_COLLECTOR"
_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()

#: 审计链节流状态（进程内）：上次核验时刻 / 结果
_last_audit_at: float = 0.0
_last_audit_ok: Optional[bool] = None


def enabled() -> bool:
    raw = (os.environ.get(ENV_FLAG) or "").strip().lower()
    if not raw:
        return True
    return raw in _TRUTHY


def reset_audit_throttle() -> None:
    """清审计节流（测试隔离用）"""
    global _last_audit_at, _last_audit_ok
    _last_audit_at = 0.0
    _last_audit_ok = None


# ════════════════════════════════════════════════════════════
#  事件源（每个函数返回 candidate 列表；空列表 = "这一源没有问题"）
# ════════════════════════════════════════════════════════════

def health_candidates(point: Optional[dict]) -> List[dict]:
    """健康探针：把"分数低于阈值"的层转成报警（无数据的层跳过）。"""
    if not point:
        return []
    out: List[dict] = []
    for layer, detail in (point.get("probe_details") or {}).items():
        if not isinstance(detail, dict):
            continue
        score = detail.get("score")
        if score is None:
            continue  # L5：无数据不是故障
        try:
            value = float(score)
        except (TypeError, ValueError):
            continue
        if value >= HEALTH_DEGRADE_SCORE:
            continue
        severity = SEVERITY_CRITICAL if value < HEALTH_CRITICAL_SCORE else SEVERITY_WARNING
        label = LAYER_LABELS.get(layer, layer)
        out.append({
            "key": "health." + layer,
            "kind": "health",
            "severity": severity,
            "title": label + " 降级（" + format(value * 100, ".0f") + " 分）",
            "detail": str(detail.get("detail") or ""),
            "subject": layer,
            "source": "/api/health/probe-trend",
        })
    return out


def unit_candidates(units_payload: Optional[dict]) -> List[dict]:
    """功能单位看板：warn / error 的单位报警（never / off 不报警）。"""
    if not units_payload:
        return []
    out: List[dict] = []
    for unit in units_payload.get("units") or []:
        status = str(unit.get("status") or "")
        if status not in UNIT_ALARM_STATUSES:
            continue
        severity = SEVERITY_CRITICAL if status == "error" else SEVERITY_WARNING
        name = str(unit.get("name") or unit.get("id") or "单位")
        issues = unit.get("issues") or []
        out.append({
            "key": "unit." + str(unit.get("id") or name),
            "kind": "unit",
            "severity": severity,
            "title": name + " 异常（" + status + "）",
            "detail": str(unit.get("summary") or "") or "; ".join(str(i) for i in issues[:3]),
            "subject": str(unit.get("id") or ""),
            "source": "/api/system/units",
        })
    return out


def audit_candidate(verification: Optional[dict]) -> List[dict]:
    """审计链尾部核验：不一致 ⇒ critical。"""
    if not verification or verification.get("ok", True):
        return []
    return [{
        "key": "audit.chain",
        "kind": "audit",
        "severity": SEVERITY_CRITICAL,
        "title": "审计链尾部核验不一致",
        "detail": str(verification.get("detail") or verification.get("reason") or ""),
        "subject": "audit_chain",
        "source": "/api/audit/logs",
    }]


# ════════════════════════════════════════════════════════════
#  真实数据源（默认实现；测试可注入替身）
# ════════════════════════════════════════════════════════════

def read_health_point() -> Optional[dict]:
    """最近一次健康采集记录（没有记录 ⇒ None ⇒ 该源本轮跳过）"""
    try:
        from agent.health.storage import health_storage
        points = health_storage.query_history(days=1)
        return points[-1] if points else None
    except Exception as exc:  # noqa: BLE001 采集读失败不影响其它源
        logger.warning("报警器：健康历史读取失败（%s）", type(exc).__name__)
        return None


def read_units() -> Optional[dict]:
    """单位看板快照（探针有 60s 缓存，不会因为报警再打一轮重探测）"""
    try:
        from agent.monitoring import unit_monitor
        return unit_monitor.collect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("报警器：单位看板读取失败（%s）", type(exc).__name__)
        return None


def read_audit_verification() -> Optional[dict]:
    """审计链尾部核验（每小时最多一次；其余轮次复用上次结果）"""
    global _last_audit_at, _last_audit_ok
    now = time.monotonic()
    if _last_audit_ok is not None and (now - _last_audit_at) < AUDIT_RECHECK_SEC:
        return {"ok": _last_audit_ok, "detail": "（节流：沿用上一轮核验结果）"}
    try:
        from agent.audit.chain import get_audit_chain
        chain = get_audit_chain()
        tail = chain.tail_entries(limit=1)
        if not tail:
            _last_audit_at, _last_audit_ok = now, True
            return {"ok": True, "detail": "空链"}
        max_seq = int(getattr(tail[-1], "seq", 0) or 0)
        start_seq = max(1, max_seq - AUDIT_TAIL_WINDOW + 1)
        result = chain.verify_chain(start_seq=start_seq, end_seq=max_seq)
        ok = bool(getattr(result, "ok", True))
        detail = "" if ok else str(getattr(result, "detail", "") or getattr(result, "reason", ""))
    except Exception as exc:  # noqa: BLE001 核验失败不误报（也不当成"链坏"）
        logger.warning("报警器：审计链核验失败（%s）", type(exc).__name__)
        return None
    _last_audit_at, _last_audit_ok = now, ok
    return {"ok": ok, "detail": detail}


# ════════════════════════════════════════════════════════════
#  一轮采集
# ════════════════════════════════════════════════════════════

def collect_once(store: Optional[AlarmStore] = None, *,
                 sources: Optional[Dict[str, Callable[[], object]]] = None) -> Dict[str, object]:
    """跑一轮：读三处信号 → 对齐台账（新的上报、消失的恢复）。

    Args:
        store: 台账（缺省 = 进程内默认台账）
        sources: 可注入的数据源（键：health / units / audit；测试用）

    Returns:
        {"checked": {源: bool 是否有数据}, "raised": n, "resolved": m}
    """
    target = store or get_store()
    readers: Dict[str, Callable[[], object]] = {
        "health": read_health_point,
        "units": read_units,
        "audit": read_audit_verification,
    }
    if sources:
        readers.update(sources)

    candidates: Dict[str, dict] = {}
    checked: Dict[str, bool] = {}
    for name, reader in readers.items():
        try:
            payload = reader()
        except Exception as exc:  # noqa: BLE001 单源失败不中断整轮
            logger.warning("报警器：数据源 %s 失败（%s）", name, type(exc).__name__)
            checked[name] = False
            continue
        checked[name] = payload is not None
        if name == "health":
            rows = health_candidates(payload)  # type: ignore[arg-type]
        elif name == "units":
            rows = unit_candidates(payload)  # type: ignore[arg-type]
        else:
            rows = audit_candidate(payload)  # type: ignore[arg-type]
        for row in rows:
            candidates[row["key"]] = row

    # 【关键】只在"该源确实有数据"时才允许它 resolve 旧告警：
    #   数据源不可用时传空 candidates 会让旧告警被误判为恢复（伪造"已恢复"）。
    keep = set()
    for name, prefix in (("health", "health."), ("units", "unit."), ("audit", "audit.")):
        if not checked.get(name):
            keep |= {k for k in _firing_keys(target) if k.startswith(prefix)}

    stats = target.reconcile(candidates, keep=keep)
    return {"checked": checked, "raised": stats["raised"],
            "resolved": stats["resolved"], "kept": sorted(keep)}


def _firing_keys(store: AlarmStore) -> List[str]:
    return [row["key"] for row in store.list(state="firing", limit=1000)]


# ════════════════════════════════════════════════════════════
#  后台线程
# ════════════════════════════════════════════════════════════

def _loop(interval: int) -> None:
    while True:
        try:
            if enabled():
                collect_once()
        except Exception as exc:  # noqa: BLE001 采集异常不中断循环
            logger.error("报警器采集异常: %s", exc)
        time.sleep(interval)


def start_collector(interval: int = COLLECT_INTERVAL) -> Optional[threading.Thread]:
    """启动报警采集 daemon 线程（幂等；开关关闭时返回 None）"""
    global _thread
    if not enabled():
        logger.info("报警器采集未启用（%s=0）", ENV_FLAG)
        return None
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = threading.Thread(target=_loop, args=(interval,),
                                   name="alarm_collector", daemon=True)
        _thread.start()
        logger.info("报警器采集线程已启动 (interval=%ss)", interval)
        return _thread
