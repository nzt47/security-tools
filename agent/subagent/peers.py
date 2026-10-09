"""子代理静态对端 + 心跳 + 离线回退显式登记（S5 通信面 · PR-E）

【这一版回答什么问题】
    通信面此前只有"共享任务看板"（母体写、就近读）。本模块补上**对端通信的地基**：
    运维用一条环境变量静态声明"有哪些对端、各自回调地址是什么"，母体按固定周期
    向它们发心跳；心跳成败如实折叠成健康态，并逐条写审计。

【为什么是"静态对端"而不是服务发现（本模块最重要的取舍）】
    ``CP_SUBAGENT_PEERS`` 就是唯一的对端来源。本模块**刻意不做**任何形式的
    服务发现：没有 DNS 查询、没有 SRV/mDNS、没有注册中心拉取、没有对端自注册。
    理由：自动发现会把"谁可以成为对端"从**声明式白名单**变成**运行时涌现**，
    而出站目标一旦可涌现，SSRF 与"意外把内网地址当对端"就无从拦截。宁可少一个
    便利，也不让"配置错了"与"被探测重定向了"在代码里无法区分。

【要解决的问题②：离线回退绝不伪装】
    心跳失败时最容易被糊弄的做法是：悄悄攒一个本地 outbox，或者假装"重放一下就好"。
    ``offline_fallback_report()`` 用**中性状态名** ``not_implemented`` 显式登记
    "重放与 outbox 都没做"——让 UI/审计看到的是"未做"，而不是把缺失化妆成一个空数组。

【依赖纪律】纯标准库。``agent.task_scheduler`` 只在 ``register_peer_heartbeats()``
    内部**惰性导入**（避免导入期把调度器及其一整条依赖链拉起来）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: 静态对端清单（JSON 数组，或 ``name=url,name=url``）——**唯一**的对端来源
ENV_PEERS = "CP_SUBAGENT_PEERS"
#: 心跳周期（秒）；缺失即**不注册**（本模块不写死数字默认值）
ENV_HEARTBEAT_INTERVAL = "CP_SUBAGENT_PEER_HEARTBEAT_SEC"

#: 心跳协议名（与 bundle 的 entrypoint 协议同源：task_file → JSON Lines）
PROTOCOL = "task_file-jsonl"

#: 离线回退状态：中性名，明确"未实现"，不冒充成支持/不支持
OFFLINE_FALLBACK_STATUS = "not_implemented"

#: 审计动作名与操作者（母体侧；对端不写审计）
AUDIT_ACTION = "subagent.peer.heartbeat"
AUDIT_ACTOR = "subagent.peers"

#: 心跳载荷的 kind 叶子（供对端路由/审计识别，不含任何凭据）
HEARTBEAT_KIND = "subagent.peer.heartbeat"

#: 布尔解析的真值集合（缺省 True：静态清单里显式写 enabled:false 才停发）
_FALSY = ("0", "false", "no", "off", "disabled")


def _as_bool(value: Any, default: bool = True) -> bool:
    """宽松布尔（None/空 → default；字符串按 _FALSY 判否）"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _FALSY


def _as_status(value: Any) -> Optional[int]:
    """dispatcher 的 status 归一化为 int；不可解析时为 None（不臆造 0）"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PeerTarget:
    """一个静态对端（**只来自配置**；不做任何探测/发现）"""

    name: str
    callback_url: str
    enabled: bool = True


@dataclass
class PeerHealth:
    """一个对端的健康折叠态（心跳逐次更新；默认即"从未尝试"）"""

    name: str
    attempts: int = 0
    ok: bool = False
    consecutive_failures: int = 0
    last_status: Optional[int] = None
    last_error: str = ""
    last_ok_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "attempts": self.attempts,
            "ok": self.ok,
            "consecutive_failures": self.consecutive_failures,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
        }


def heartbeat_payload(peer_name: str, protocol: str = PROTOCOL,
                      now: Optional[float] = None) -> Dict[str, Any]:
    """一次心跳的载荷（纯函数；**不含任何凭据/令牌**）

    ``ts`` 用 epoch 秒（数值，便于对端排序与折算距离）；``service_discovery`` 恒 False
    ——把"我们不做服务发现"写进线上载荷，避免消费方脑补出一个不存在的发现机制。
    """
    stamp = time.time() if now is None else float(now)
    return {
        "kind": HEARTBEAT_KIND,
        "peer": str(peer_name or ""),
        "protocol": str(protocol or ""),
        "ts": stamp,
        "service_discovery": False,
    }


def offline_fallback_report() -> Dict[str, Any]:
    """离线回退的**显式登记**（现状是什么就说什么，绝不粉饰）"""
    return {
        "status": OFFLINE_FALLBACK_STATUS,
        "replay": False,
        "outbox": False,
        "note": ("离线回退未实现：心跳失败只更新健康态并写审计，本地 outbox 与稍后重放"
                 "均未接线；此字段是中性显式登记，既不表示已支持，也不表示已关闭。"),
    }


def _parse_peer_items(text: str) -> Tuple[PeerTarget, ...]:
    """配置原文 → 对端元组（**绝不抛**；畸形 → 空表 + warning）

    支持两种写法（二选一，整体判定）：
      ① JSON 数组：[{"name":"a","callback_url":"http://...","enabled":true}, ...]
                    也接受数组元素是 "a=http://..." 字符串；
      ② 逗号分隔：a=http://host/a,b=http://host/b
    """
    raw = str(text or "").strip()
    if not raw:
        return ()

    parsed_items: Any = None
    is_json = False
    try:
        parsed_items = json.loads(raw)
        is_json = True
    except (TypeError, ValueError):
        parsed_items = None

    items: Iterable[Any]
    if is_json:
        if isinstance(parsed_items, list):
            items = parsed_items
        elif isinstance(parsed_items, dict):
            # 宽容：{"a": "http://..."} 映射也收（仍是静态声明，不是发现）
            items = [{"name": str(k), "callback_url": v}
                     for k, v in parsed_items.items()]
        else:
            logger.warning("[Peers] %s 的值不是 JSON 数组/对象 ⇒ 视为空表（不做探测）",
                           ENV_PEERS)
            return ()
    else:
        items = [part for part in raw.split(",") if part.strip()]

    out: list[PeerTarget] = []
    seen: set[str] = set()
    for item in items:
        target = _coerce_target(item)
        if target is None:
            logger.warning("[Peers] %s 含无法解析的对端条目，已跳过（不做探测）",
                           ENV_PEERS)
            continue
        if target.name in seen:
            continue
        seen.add(target.name)
        out.append(target)
    return tuple(out)


def _coerce_target(item: Any) -> Optional[PeerTarget]:
    """单个条目 → PeerTarget（解析不出返回 None）"""
    if isinstance(item, Mapping):
        name = str(item.get("name") or item.get("peer") or "").strip()
        url = str(item.get("callback_url") or item.get("url") or "").strip()
        enabled = _as_bool(item.get("enabled"), default=True)
    else:
        text = str(item or "").strip()
        if not text:
            return None
        if "=" not in text:
            return None
        name, url = text.split("=", 1)
        name, url = name.strip(), url.strip()
        enabled = True
    # 结构校验：名字非空、地址非空且带 scheme（"h:8080/x" 这类半截地址判畸形）。
    # 这**不是**服务发现，只是把明显不是地址的串挡在出站之前（fail-closed）。
    if not name or not url or "://" not in url:
        return None
    return PeerTarget(name=name, callback_url=url, enabled=enabled)


class StaticPeerRegistry:
    """静态对端表 + 每对端健康态（进程内对象；不做任何网络探测）"""

    def __init__(self, peers: Iterable[PeerTarget] = ()) -> None:
        self._targets: Tuple[PeerTarget, ...] = tuple(peers)
        self._health: Dict[str, PeerHealth] = {
            p.name: PeerHealth(name=p.name) for p in self._targets}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, raw: Optional[str] = None) -> "StaticPeerRegistry":
        """从环境变量构造（畸形 → 空表 + warning，**绝不抛**；不做服务发现）"""
        text = os.environ.get(ENV_PEERS, "") if raw is None else raw
        try:
            return cls(_parse_peer_items(text))
        except Exception as e:  # noqa: BLE001 解析故障不得让调用方挂掉
            logger.warning("[Peers] %s 解析失败 ⇒ 空表（不做探测）: %s",
                           ENV_PEERS, type(e).__name__)
            return cls(())

    @property
    def targets(self) -> Tuple[PeerTarget, ...]:
        return self._targets

    def enabled_targets(self) -> Tuple[PeerTarget, ...]:
        return tuple(t for t in self._targets if t.enabled)

    def get(self, name: str) -> Optional[PeerTarget]:
        for target in self._targets:
            if target.name == name:
                return target
        return None

    def health(self) -> Dict[str, Dict[str, Any]]:
        """{name: 健康态字典}（只读快照）"""
        with self._lock:
            return {name: h.to_dict() for name, h in self._health.items()}

    def note_result(self, name: str, *, ok: bool, status: Optional[int] = None,
                    error: str = "", now: Optional[float] = None) -> PeerHealth:
        """把一次心跳结果折叠进健康态并返回新值（未知名字 → 现造一条，不抛）"""
        with self._lock:
            health = self._health.get(name)
            if health is None:
                health = PeerHealth(name=name)
                self._health[name] = health
            health.attempts += 1
            health.ok = bool(ok)
            health.last_status = _as_status(status)
            health.last_error = str(error or "")
            if ok:
                health.consecutive_failures = 0
                health.last_ok_at = time.time() if now is None else float(now)
            else:
                health.consecutive_failures += 1
            return health

    def snapshot(self) -> Dict[str, Any]:
        """读面载荷：对端清单（url/enabled）+ 健康态 + 离线回退登记"""
        peers = [{"name": t.name, "url": t.callback_url, "enabled": t.enabled}
                 for t in self._targets]
        return {
            "peers": peers,
            "count": len(peers),
            "enabled_count": sum(1 for t in self._targets if t.enabled),
            "health": self.health(),
            "offline_fallback": offline_fallback_report(),
            # 显式声明：本表是静态声明，不是发现结果
            "service_discovery": False,
        }


def _audit_heartbeat(audit: Any, target: PeerTarget, *, ok: bool,
                     status: Optional[int], attempts: int,
                     consecutive_failures: int, protocol: str) -> bool:
    """写一条心跳审计；返回"是否真的写入"（audit 缺失/异常 → False，不抛）"""
    if audit is None:
        return False
    try:
        entry = audit.record(
            AUDIT_ACTION,
            actor=AUDIT_ACTOR,
            subject="peer:" + target.name,
            payload={
                "peer": target.name,
                "ok": bool(ok),
                "status": status,
                "attempts": attempts,
                "consecutive_failures": consecutive_failures,
                "protocol": protocol,
            },
            status="ok" if ok else "failed",
        )
        return entry is not None
    except Exception as e:  # noqa: BLE001 审计 best-effort，绝不阻断心跳
        logger.warning("[Peers] 心跳审计写入失败（不影响心跳）: %s",
                       type(e).__name__)
        return False


def send_heartbeats(registry: StaticPeerRegistry, dispatcher: Callable[..., Any], *,
                    audit: Any = None,
                    now_fn: Callable[[], float] = time.time) -> Dict[str, Any]:
    """向所有**启用**的对端各发一次心跳（逐对端 fail-soft，绝不抛）

    Returns:
        {sent, delivered, failed, skipped, audited, peers: [{name, attempted,
         ok, status, error, audited}]}
        —— ``audited`` 是"审计是否**真的**写入"（audit 缺失或写失败即 False），
        不是"尝试过审计"。返回体**不含** callback_url 与任何凭据。
    """
    results: list[Dict[str, Any]] = []
    sent = delivered = failed = skipped = audited = 0
    for target in registry.targets:
        if not target.enabled:
            skipped += 1
            results.append({"name": target.name, "attempted": False, "ok": False,
                            "status": None, "error": "disabled", "audited": False})
            continue

        ok = False
        status: Optional[int] = None
        error = ""
        payload = heartbeat_payload(target.name, now=now_fn())
        try:
            raw = dispatcher(target.callback_url, payload)
            info = dict(raw) if isinstance(raw, Mapping) else {}
            ok = bool(info.get("delivered", info.get("ok", False)))
            status = _as_status(info.get("status"))
            error = str(info.get("error") or "")
        except Exception as e:  # noqa: BLE001 单对端失败不影响其它对端
            ok, status, error = False, None, type(e).__name__ + ": " + str(e)

        sent += 1
        delivered += 1 if ok else 0
        failed += 0 if ok else 1
        health = registry.note_result(target.name, ok=ok, status=status,
                                      error=error, now=now_fn())
        wrote = _audit_heartbeat(
            audit, target, ok=ok, status=status, attempts=health.attempts,
            consecutive_failures=health.consecutive_failures,
            protocol=str(payload.get("protocol") or ""))
        audited += 1 if wrote else 0
        results.append({"name": target.name, "attempted": True, "ok": ok,
                        "status": status, "error": error, "audited": wrote})
    return {"sent": sent, "delivered": delivered, "failed": failed,
            "skipped": skipped, "audited": audited, "peers": results}


def _read_interval_seconds(explicit: Optional[Any] = None) -> Optional[int]:
    """心跳周期：显式值优先，否则读环境变量；缺失/非法/非正 → None（不写死默认）"""
    if explicit is not None:
        try:
            value = int(explicit)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    raw = os.environ.get(ENV_HEARTBEAT_INTERVAL, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def register_peer_heartbeats(scheduler: Any = None,
                             registry: Optional[StaticPeerRegistry] = None,
                             dispatcher: Optional[Callable[..., Any]] = None,
                             interval_seconds: Optional[Any] = None) -> Dict[str, Any]:
    """把心跳注册成周期任务（**只在配了周期且确有对端时**）

    ``interval_seconds`` 缺省读 ``CP_SUBAGENT_PEER_HEARTBEAT_SEC``；缺失 ⇒ 不注册，
    且**不**用任何写死的数字兜底（宁可显式"未接线"，也不偷偷每 60 秒打一次）。
    启动期接入（在 app_server/生命周期里调用本函数）属后续工作，本 PR 只提供入口。
    """
    interval = _read_interval_seconds(interval_seconds)
    if interval is None:
        logger.info("[Peers] 未配置 %s ⇒ 不注册心跳周期任务（显式未接线）",
                    ENV_HEARTBEAT_INTERVAL)
        return {"registered": False, "interval_seconds": None, "peers": 0,
                "task": "", "reason": "interval_not_configured"}

    reg = registry if registry is not None else StaticPeerRegistry.from_env()
    if not reg.targets:
        return {"registered": False, "interval_seconds": interval, "peers": 0,
                "task": "", "reason": "no_peers"}

    if scheduler is None:
        from agent.task_scheduler import get_scheduler  # 惰性：避免导入期拉整条链
        scheduler = get_scheduler()

    task_name = "subagent_peer_heartbeat"

    def _tick() -> None:
        try:
            send_heartbeats(reg, dispatcher)
        except Exception as e:  # noqa: BLE001 周期任务绝不因单次失败崩掉
            logger.warning("[Peers] 心跳周期任务异常（已吞，等待下轮）: %s",
                           type(e).__name__)

    scheduler.add_interval_task(name=task_name, func=_tick,
                                interval_seconds=interval)
    return {"registered": True, "interval_seconds": interval,
            "peers": len(reg.targets), "task": task_name, "reason": ""}


__all__ = [
    "ENV_PEERS", "ENV_HEARTBEAT_INTERVAL", "PROTOCOL",
    "OFFLINE_FALLBACK_STATUS", "AUDIT_ACTION", "AUDIT_ACTOR", "HEARTBEAT_KIND",
    "PeerTarget", "PeerHealth", "StaticPeerRegistry",
    "heartbeat_payload", "offline_fallback_report", "send_heartbeats",
    "register_peer_heartbeats",
]
