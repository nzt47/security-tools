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

【本版补上的接线：心跳从"有入口"到"真的会跑"】
    · **启动期接线**：app_server 调 ``register_peer_heartbeats``；缺周期 / 缺对端 /
      缺投递器一律**不注册**并如实返回 reason，绝不写死默认周期、也绝不静默假装在发。
    · **读面共享**：``install_live_registry()`` 把心跳用的同一个 registry 交给读面。
      否则健康态写在一个"每请求重建"的临时对象上 —— 写归写，读面永远看到 attempts=0。
    · **健康持久化**：每次 tick 后把健康态落 JSON（``data/subagent_peer_health.json``），
      启动时回填；否则重启就丢"连续失败几次"，故障跨重启不可累积。
    · **审计真的写**：tick 把 ``audit`` 传给 ``send_heartbeats``；审计不可得时
      ``audited=False`` 如实投影，不把"尝试过"说成"留痕了"。

【依赖纪律】纯标准库。``agent.task_scheduler`` 与 ``agent.subagent.callback_channel``
    只在 ``register_peer_heartbeats()`` 内部**惰性导入**（避免导入期把调度器/出站
    策略链拉起来）。
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


def _as_int(value: Any) -> int:
    """宽松取**非负**整数（None/bool/不可解析 → 0；不抛）——持久化回填用"""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _as_float_or_none(value: Any) -> Optional[float]:
    """epoch 秒 → float；None/不可解析 → None（不臆造 0.0）"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
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

    def import_health(self, mapping: Mapping[str, Any]) -> int:
        """从持久化快照回填健康态，返回真正回填的对端数

        **只认当前声明的对端**：持久化文件可能来自旧配置，回填一个已删除的对端
        会凭空造出"幽灵健康态"，让读面显示一个根本不存在的对端。非法值一律
        归一（不抛），因为健康态是观测数据，不该让坏文件把启动打挂。
        """
        if not isinstance(mapping, Mapping):
            return 0
        restored = 0
        with self._lock:
            for name, data in mapping.items():
                health = self._health.get(str(name))
                if health is None or not isinstance(data, Mapping):
                    continue
                health.attempts = _as_int(data.get("attempts"))
                health.ok = bool(data.get("ok"))
                health.consecutive_failures = _as_int(
                    data.get("consecutive_failures"))
                health.last_status = _as_status(data.get("last_status"))
                health.last_error = str(data.get("last_error") or "")
                health.last_ok_at = _as_float_or_none(data.get("last_ok_at"))
                restored += 1
        return restored

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


#: 健康态持久化文件名（落在 repo/data/ 下，与 task_scheduler 的
#: heartbeat_history.json 同域；不含任何对端地址之外的凭据）
HEALTH_FILE_NAME = "subagent_peer_health.json"
#: 持久化载荷版本（结构一变 +1，读侧据此 fail-soft）
HEALTH_SCHEMA_VERSION = 1

_LIVE_REGISTRY: Optional[StaticPeerRegistry] = None
_LIVE_LOCK = threading.Lock()


def default_health_path() -> str:
    """默认健康态落盘路径：``<repo>/data/subagent_peer_health.json``

    【为什么按 __file__ 反推而不是 cwd】app_server 可能以任意 cwd 启动；按 cwd
    落盘会得到"换个目录启动就读不到上次健康态"的静默漂移。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(root, "data", HEALTH_FILE_NAME)


def save_health(registry: StaticPeerRegistry, path: Optional[str] = None) -> bool:
    """健康态落盘（best-effort：失败只 warning，绝不影响心跳）

    写临时文件再 ``os.replace``：避免进程在写到一半时被杀，留一份半截 JSON ——
    下次启动回填到一半的健康态比没有更坏。
    """
    target = str(path or default_health_path())
    try:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": HEALTH_SCHEMA_VERSION,
                       "health": registry.health()},
                      fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, target)
        return True
    except OSError as e:  # noqa: BLE001 磁盘问题不得拖垮周期任务
        logger.warning("[Peers] 健康态落盘失败（不影响心跳）: %s",
                       type(e).__name__)
        return False


def load_health(registry: StaticPeerRegistry, path: Optional[str] = None) -> int:
    """健康态回填（文件缺失/损坏 ⇒ 0，不抛；返回值 = 真正回填的对端数）"""
    target = str(path or default_health_path())
    try:
        with open(target, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return 0
    if not isinstance(doc, Mapping):
        return 0
    return registry.import_health(doc.get("health") or {})


def build_live_registry(path: Optional[str] = None) -> StaticPeerRegistry:
    """构造"心跳 + 读面"共用的 registry（按环境读声明，并回填上次健康态）"""
    registry = StaticPeerRegistry.from_env()
    load_health(registry, path)
    return registry


def install_live_registry(registry: Optional[StaticPeerRegistry]) -> None:
    """登记进程级 live registry —— 读面据此看到心跳写进去的健康态"""
    global _LIVE_REGISTRY
    with _LIVE_LOCK:
        _LIVE_REGISTRY = registry


def get_live_registry() -> Optional[StaticPeerRegistry]:
    """取进程级 live registry（未接线时为 None）"""
    with _LIVE_LOCK:
        return _LIVE_REGISTRY


def reset_live_registry() -> None:
    """清空 live registry（**测试专用**：避免用例之间串健康态）"""
    install_live_registry(None)


def read_snapshot() -> Dict[str, Any]:
    """读面载荷：优先 live registry（含心跳健康态），否则按当前环境现造

    【为什么不总是 live】未接线（单测 / 未启动 app_server）时，读面必须与改动前
    逐字一致：每请求读环境变量。只有启动期 install 过，才共享心跳健康态 ——
    这样"配置变更即时可见"的老口径在未接线场景不回归。
    """
    registry = get_live_registry()
    if registry is None:
        registry = StaticPeerRegistry.from_env()
    return registry.snapshot()


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
                             interval_seconds: Optional[Any] = None, *,
                             audit: Any = None,
                             health_path: Optional[str] = None) -> Dict[str, Any]:
    """把心跳注册成周期任务（**只在配了周期、确有对端、且有投递器时**）

    ``interval_seconds`` 缺省读 ``CP_SUBAGENT_PEER_HEARTBEAT_SEC``；缺失 ⇒ 不注册，
    且**不**用任何写死的数字兜底（宁可显式"未接线"，也不偷偷每 60 秒打一次）。
    ``dispatcher`` 缺省走 ``callback_channel.default_callback_dispatcher()``，复用
    既有出站策略（开关 + host 白名单 + 令牌 + 不跟随重定向）；拿不到投递器则
    返回 ``reason="dispatcher_unavailable"`` 且**不注册** —— 否则 tick 会以
    "None 不可调用"逐对端记失败，把"没接线"化妆成"对端全挂"。

    每次 tick 的顺序：尽发一轮心跳 → 写审计（``audit=None`` 时 audited=False，
    如实投影）→ 健康态落盘（``health_path`` 缺省 ``data/subagent_peer_health.json``）。
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

    disp = dispatcher
    if disp is None:
        from agent.subagent.callback_channel import default_callback_dispatcher
        disp = default_callback_dispatcher()
    if disp is None:
        logger.warning("[Peers] 无 HTTP 投递器（回调出站未配置齐）"
                       "⇒ 不注册心跳周期任务")
        return {"registered": False, "interval_seconds": interval,
                "peers": len(reg.targets), "task": "",
                "reason": "dispatcher_unavailable"}

    if scheduler is None:
        from agent.task_scheduler import get_scheduler  # 惰性：避免导入期拉整条链
        scheduler = get_scheduler()

    task_name = "subagent_peer_heartbeat"

    def _tick() -> None:
        try:
            send_heartbeats(reg, disp, audit=audit)
            save_health(reg, health_path)
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
    "HEALTH_FILE_NAME", "HEALTH_SCHEMA_VERSION",
    "PeerTarget", "PeerHealth", "StaticPeerRegistry",
    "heartbeat_payload", "offline_fallback_report", "send_heartbeats",
    "register_peer_heartbeats",
    "default_health_path", "save_health", "load_health", "build_live_registry",
    "install_live_registry", "get_live_registry", "reset_live_registry",
    "read_snapshot",
]
