"""运行中子代理登记表（E2-1「运行中控制」）

【要解决什么】\`delegate\` 是同步阻塞的：工具/UI 在委派跑起来之后既发现不了它，
也追加不了指令、打断不了。本模块提供**进程内运行期登记表**：

    run_id → RunHandle(subagent, delegation_id, status, rounds, cancel_event, inbox)

写盘只落只读快照（\`data/subagent_runs.json\`，运行期状态、不提交）；inbox 落
\`data/subagent_runs/<run_id>/inbox.jsonl\`（append-only）。进程重启即失效——
运行中的 run 本来就不跨重启，这与「让模型看得见正在跑什么」的目标不冲突。

【send 语义】不打断当前轮：消息进 inbox，执行器在**下一轮开始前** drain 成一条
user 消息（与 DSH 同口径；避免半截工具调用）。
【interrupt 语义】协作式：置 \`cancel_event\`，内部 LLM 执行器在**每轮之间**检查，
结束本轮、保留已产出，status=interrupted。不硬杀线程/进程（会破坏审计与工作区一致性）。
外部 CLI 通道没有逐轮钩子 ⇒ send/interrupt 对它**不生效**（如实标注，不假装支持）。

【开关】\`CP_SUBAGENT_CONTROL_ENABLED=0\` ⇒ 工具回 \`code="disabled"\`、路由 404。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from agent.jsonl_history import append_jsonl

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 运行期目录与快照（都在 data/ 下，属运行期状态，不提交）
DEFAULT_ROOT = os.path.join(_REPO_ROOT, "data", "subagent_runs")
DEFAULT_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "data", "subagent_runs.json")

#: 开关（默认开；=0 关闭）
ENABLED_ENV = "CP_SUBAGENT_CONTROL_ENABLED"

#: 状态取值（供工具/路由/用例对拍，防口径漂移）
STATUSES = ("running", "done", "interrupted", "failed")

#: last_output 摘要上限（不要把子代理正文灌回父上下文）
LAST_OUTPUT_MAX = 500

#: 单次 drain 上限（一次最多把多少条追加指令塞进下一轮）
DRAIN_LIMIT = 20

#: 已完成 run 在登记表里保留的秒数（列表默认只看 running；快照保留最近若干条）
FINISHED_TTL_SECONDS = 900.0


def enabled() -> bool:
    """运行中控制是否开启（\`=0/false/off\` 关闭；缺省开）"""
    raw = str(os.environ.get(ENABLED_ENV, "") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunHandle:
    """一次正在执行的委派（运行期句柄；不跨进程、不跨重启）"""

    run_id: str
    subagent: str = ""
    delegation_id: str = ""
    started_at: float = field(default_factory=_now)
    status: str = "running"
    rounds: int = 0
    last_output: str = ""
    reason: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    inbox_path: str = ""
    _cursor: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def elapsed_s(self) -> float:
        return round(max(0.0, _now() - float(self.started_at)), 1)

    def set_output(self, text: str) -> None:
        """记录最后输出摘要（截断；绝不整段回传）"""
        value = str(text or "").strip()
        self.last_output = value[:LAST_OUTPUT_MAX]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "subagent": self.subagent,
            "delegation_id": self.delegation_id,
            "status": self.status,
            "rounds": int(self.rounds),
            "elapsed_s": self.elapsed_s,
            "started_at": _iso(self.started_at),
            "last_output": self.last_output,
            "reason": self.reason,
            "cancelled": bool(self.cancel_event.is_set()),
        }


class RunRegistry:
    """进程内运行期登记表（线程安全；写盘只落快照）"""

    def __init__(self, root: str = "", snapshot_path: str = "") -> None:
        self._root = str(root or DEFAULT_ROOT)
        self._snapshot_path = str(snapshot_path or DEFAULT_SNAPSHOT_PATH)
        self._runs: Dict[str, RunHandle] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    # ── 生命周期 ──

    def register(self, subagent: str = "", delegation_id: str = "",
                 run_id: str = "") -> RunHandle:
        """登记一个 run（返回句柄；同名 delegation 已存在则复用）"""
        rid = str(run_id or "").strip() or ("r-" + uuid.uuid4().hex[:12])
        with self._lock:
            existing = self._by_delegation_locked(delegation_id)
            if existing is not None:
                return existing
            handle = RunHandle(
                run_id=rid,
                subagent=str(subagent or ""),
                delegation_id=str(delegation_id or ""),
                inbox_path=os.path.join(self._root, rid, "inbox.jsonl"),
            )
            self._runs[rid] = handle
        self._snapshot_safe()
        return handle

    def ensure(self, delegation_id: str, subagent: str = "") -> RunHandle:
        """按 delegation_id 取句柄；没有就登记（执行器咽喉调用这一支）"""
        with self._lock:
            handle = self._by_delegation_locked(delegation_id)
            if handle is not None:
                return handle
        return self.register(subagent=subagent, delegation_id=delegation_id)

    def get(self, run_id: str) -> Optional[RunHandle]:
        with self._lock:
            return self._runs.get(str(run_id or ""))

    def by_delegation(self, delegation_id: str) -> Optional[RunHandle]:
        with self._lock:
            return self._by_delegation_locked(delegation_id)

    def _by_delegation_locked(self, delegation_id: str) -> Optional[RunHandle]:
        target = str(delegation_id or "")
        if not target:
            return None
        for handle in self._runs.values():
            if handle.delegation_id == target:
                return handle
        return None

    def finish(self, handle: Optional[RunHandle], status: str = "done",
               reason: str = "") -> None:
        """标记结束（保留在登记表里一段时间供查询；不硬删即不丢证据）"""
        if handle is None:
            return
        handle.status = status if status in STATUSES else "failed"
        handle.reason = str(reason or "")
        self._snapshot_safe()

    def remove(self, run_id: str) -> bool:
        with self._lock:
            return self._runs.pop(str(run_id or ""), None) is not None

    def prune(self, ttl_seconds: float = FINISHED_TTL_SECONDS) -> int:
        """清掉过期或进程重启前遗留的已结束 run（只清已结束的）"""
        now = _now()
        removed = 0
        with self._lock:
            for rid in list(self._runs):
                handle = self._runs[rid]
                if handle.status != "running" and now - handle.started_at > ttl_seconds:
                    self._runs.pop(rid, None)
                    removed += 1
        return removed

    # ── 读 ──

    def list_runs(self, status: str = "running", limit: int = 50) -> List[Dict[str, Any]]:
        """列表（最新在前）：status='all' 看全部，否则只留该状态"""
        want = str(status or "running").strip().lower()
        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit = 50
        with self._lock:
            runs = list(self._runs.values())
        if want not in ("all", "*", ""):
            runs = [h for h in runs if h.status == want]
        runs.sort(key=lambda h: h.started_at, reverse=True)
        return [h.to_dict() for h in runs[:limit]]

    def snapshot(self) -> Dict[str, Any]:
        runs = self.list_runs(status="all", limit=50)
        return {
            "count": len(runs),
            "running": sum(1 for r in runs if r.get("status") == "running"),
            "enabled": enabled(),
            "runs": runs,
            "generated_at": _iso(_now()),
        }

    def _snapshot_safe(self) -> None:
        """落只读快照（失败只告警：登记失败绝不影响委派本身）"""
        try:
            payload = self.snapshot()
            tmp = self._snapshot_path + ".tmp"
            os.makedirs(os.path.dirname(self._snapshot_path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._snapshot_path)
        except Exception as e:  # noqa: BLE001
            logger.debug("[RunRegistry] 快照写入失败（不影响委派）: %s", e)

    # ── inbox ──

    def append_inbox(self, run_id: str, text: str, *, kind: str = "user",
                     source: str = "") -> Optional[Dict[str, Any]]:
        """追加一条运行中指令（append-only JSONL；失败返回 None，不抛出）"""
        handle = self.get(run_id)
        body = str(text or "").strip()
        if handle is None or not body:
            return None
        row = {
            "run_id": handle.run_id,
            "kind": str(kind or "user"),
            "text": body[:4000],
            "source": str(source or ""),
            "at": _iso(_now()),
        }
        try:
            os.makedirs(os.path.dirname(handle.inbox_path), exist_ok=True)
            ok = append_jsonl(handle.inbox_path, row)
        except Exception as e:  # noqa: BLE001
            logger.warning("[RunRegistry] inbox 写入失败: %s", e)
            ok = False
        return row if ok else None

    def drain_inbox(self, handle: Optional[RunHandle],
                    limit: int = DRAIN_LIMIT) -> List[Dict[str, Any]]:
        """取走尚未消费的指令（游标在进程内；读到多少消费多少）"""
        if handle is None:
            return []
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = DRAIN_LIMIT
        path = handle.inbox_path
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        except OSError as e:
            logger.debug("[RunRegistry] inbox 读取失败: %s", e)
            return []
        with handle._lock:
            start = handle._cursor
            chunk = lines[start:start + limit]
            handle._cursor = start + len(chunk)
        out: List[Dict[str, Any]] = []
        for line in chunk:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict):
                out.append(row)
        return out

    def inbox(self, run_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """只读查看 inbox 最近 N 条（用于工具/UI 的 inbox 动作，不消费）"""
        handle = self.get(run_id)
        if handle is None or not handle.inbox_path:
            return []
        try:
            with open(handle.inbox_path, "r", encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        except OSError:
            return []
        rows: List[Dict[str, Any]] = []
        for line in lines[-max(1, int(limit or 10)):]:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    # ── 线程绑定（执行器在通道调用期间绑定；LLM 循环据此取句柄）──

    def bind_thread(self, handle: Optional[RunHandle]) -> None:
        self._local.handle = handle

    def current(self) -> Optional[RunHandle]:
        return getattr(self._local, "handle", None)

    @contextmanager
    def bound(self, handle: Optional[RunHandle]) -> Iterator[None]:
        previous = self.current()
        self.bind_thread(handle)
        try:
            yield
        finally:
            self.bind_thread(previous)


#: 进程级单例（执行器绑定、工具与路由读写同一份）
run_registry = RunRegistry()


def current_run() -> Optional[RunHandle]:
    """当前线程正在执行的 run（无则 None）"""
    try:
        return run_registry.current()
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "RunHandle", "RunRegistry", "run_registry", "current_run",
    "STATUSES", "ENABLED_ENV", "LAST_OUTPUT_MAX", "DRAIN_LIMIT", "enabled",
]
