"""功能单位监控 —— 统一登记 / 探测 / 汇总「每个功能单位的工作状况」。

【要解决什么】云枢的能力面很散（技能、工具、工作流、调度任务、记忆压缩、反思、
向量库、吞噬、审计链…），各自有没有在工作、最近一次动过没、有没有红，之前只能靠
逐个页面/日志去猜（2026-10-28 实测：记忆压缩 73% 产出空摘要、反思经验 0 注入、
三个学习调度器 last_run 一直 null —— 都没有任何一处统一视图能看出来）。

【设计（不易）】
- 只做**聚合与归一化**，不新建采集系统：每个单位是一个 probe（可调用），
  读已有的文件/库/单例，返回 {status, summary, metrics, issues, last_activity}；
- probe **不抛异常**：单测失败只影响该单位的 status=error，不影响其它单位；
- 单 probe 超时（默认 2s）记为 error 并给出 issue，避免一个卡死的读把整页拖住；
- 结果缓存 5s（页面刷新可传 refresh 绕过），避免每次请求都扫文件；
- 状态口径固定五档：ok / warn / off / never / error（见 STATUSES）。

【变易】新增功能单位 = 在 unit_probes 里加一个 probe + register 一行；
  前端按 family 过滤，不需要改 API。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

STATUSES = ("ok", "warn", "off", "never", "error")

#: probe 返回体的标准形状（缺省值在此收口，probe 只需给有内容的键）
_EMPTY_RESULT: Dict[str, Any] = {
    "status": "ok", "summary": "", "metrics": {}, "issues": [], "last_activity": None,
}

_CACHE_TTL_SEC = 5.0
_DEFAULT_PROBE_TIMEOUT = 2.0


@dataclass
class Unit:
    id: str
    name: str
    family: str
    probe: Callable[[], Dict[str, Any]]
    description: str = ""


@dataclass
class _Cache:
    at: float = 0.0
    payload: Optional[Dict[str, Any]] = None


_REGISTRY: Dict[str, Unit] = {}
_LOCK = threading.Lock()
_CACHE = _Cache()


def register(unit: Unit) -> None:
    with _LOCK:
        _REGISTRY[unit.id] = unit


def registered() -> List[Unit]:
    with _LOCK:
        return list(_REGISTRY.values())


def reset_registry() -> None:
    """仅供测试：清空登记与缓存。"""
    with _LOCK:
        _REGISTRY.clear()
    clear_cache()


def clear_cache() -> None:
    _CACHE.at = 0.0
    _CACHE.payload = None


def _normalize(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(_EMPTY_RESULT)
    if isinstance(data, dict):
        out.update({k: data[k] for k in _EMPTY_RESULT if k in data})
    if out["status"] not in STATUSES:
        out["status"] = "warn"
    return out


def _run_probe(fn: Callable[[], Dict[str, Any]], timeout: float) -> Dict[str, Any]:
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["data"] = fn()
        except Exception as exc:  # noqa: BLE001 单单位失败不影响整页
            box["error"] = exc

    t = threading.Thread(target=_target, name="unit-probe", daemon=True)
    t.start()
    t.join(max(0.05, float(timeout)))
    if t.is_alive():
        return _normalize({"status": "error", "summary": "探测超时",
                           "issues": ["探测超时：超过 %.1fs 未返回" % timeout]})
    if "error" in box:
        exc = box["error"]
        return _normalize({"status": "error",
                           "summary": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                           "issues": [str(exc)[:200]]})
    return _normalize(box.get("data"))


def collect(*, refresh: bool = False, families: Optional[List[str]] = None,
            timeout: float = _DEFAULT_PROBE_TIMEOUT) -> Dict[str, Any]:
    """跑一遍所有登记单位，返回 {units, summary, generated_at, cached}。"""
    now = time.monotonic()
    with _LOCK:
        cached_ok = (not refresh) and _CACHE.payload is not None and (now - _CACHE.at) < _CACHE_TTL_SEC
        if cached_ok:
            payload = _CACHE.payload
            units = payload["units"]
            if families:
                units = [u for u in units if u["family"] in families]
            return {"units": units, "summary": _summarize(units),
                    "generated_at": payload["generated_at"], "cached": True}
        units_snapshot = list(_REGISTRY.values())

    rows: List[Dict[str, Any]] = []
    for unit in units_snapshot:
        t0 = time.perf_counter()
        data = _run_probe(unit.probe, timeout)
        rows.append({
            "id": unit.id,
            "name": unit.name,
            "family": unit.family,
            "description": unit.description,
            "status": data["status"],
            "summary": str(data["summary"] or ""),
            "metrics": data["metrics"] or {},
            "issues": list(data["issues"] or []),
            "last_activity": data["last_activity"],
            "probe_ms": round((time.perf_counter() - t0) * 1000, 2),
        })

    payload = {"units": rows, "generated_at": datetime.now(timezone.utc).isoformat()}
    with _LOCK:
        _CACHE.at = time.monotonic()
        _CACHE.payload = payload

    units = rows
    if families:
        units = [u for u in rows if u["family"] in families]
    return {"units": units, "summary": _summarize(units),
            "generated_at": payload["generated_at"], "cached": False}


def _summarize(units: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for u in units:
        out[u["status"]] = out.get(u["status"], 0) + 1
    out["total"] = len(units)
    return out


def health_score(units: List[Dict[str, Any]]) -> float:
    """粗略健康分（0–100）：ok=1 / warn=0.6 / never=0.4 / off=0 / error=0。"""
    if not units:
        return 0.0
    weight = {"ok": 1.0, "warn": 0.6, "never": 0.4, "off": 0.0, "error": 0.0}
    return round(100.0 * sum(weight.get(u["status"], 0.0) for u in units) / len(units), 1)