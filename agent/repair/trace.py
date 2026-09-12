"""五步全程可审计：Trace + 链式审计的唯一出口（TASK-S7-02 步骤 6 / 边界 ⑤）

【任务定位】
    任务书 §一 边界 ⑤：「体检/定位/派工/验证/产出每步都写 Trace 与链式审计，
    **不许出现不可见动作**（八条不变量之五）」。本模块是该要求的**唯一落点**：
    ``pipeline`` 的每一步都必须经 ``RepairRunLogger.run_step()`` 包一层，而
    ``run_step()`` 无论成功/失败/抛异常都必然留下一条 Trace + 一条审计。

【不易（为什么"必留痕"要写进 finally）】
    最容易漏的不是成功路径，而是**失败路径**——「补丁没过 → 丢弃」正是本任务最
    需要被看见的动作（否则"丢弃"就成了不可见动作，等于给自欺留门）。故
    ``run_step()`` 在 ``try/except/finally`` 三条路径上都写同一对记录，状态字段
    （``ok`` / ``error``）如实区分；异常**原样重抛**，不吞。

【不易（只放叶子字段）】
    Trace/审计载荷只放标量与小列表（见 ``models.to_dict()`` 的约定）。
    **绝不**把 Service/Trace 对象/整个 Report 塞进去——那是把活对象当 JSON 用。

【变易】
    ``UnifiedTraceStore`` / ``audit`` facade / 事件出口三个下游全部**可注入**，
    因此单测可以用 tmp_path 上的库文件隔离运行，绝不触碰生产库。
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, TypeVar

from agent.repair.models import (
    AUDIT_ACTIONS,
    AuditTrailEntry,
    STEP_DIAGNOSE,
    STEP_LOCATE,
    STEP_PROPOSE,
    STEP_VERIFY,
    REPAIR_STEPS,
)

logger = logging.getLogger("agent.repair.trace")

#: 修复流水线的 actor（编排者是 on-behalf-of-human 的自动化流程）
#: 为什么不是 ``human``：触发是人，但**动作是流程做的**；把它记成 human 会让
#: 「谁改的」这件事在审计里失真。子代理另有 ``sub_agent:<id>`` 的独立 Trace。
ACTOR_PIPELINE = "auto"
#: 子代理 actor 前缀（与 ``subagent.delegation`` 的 ``delegate_actor`` 口径一致）
ACTOR_SUB_AGENT_PREFIX = "sub_agent:"

#: Trace 的 capability 命名前缀（``repair.<step>``）
CAPABILITY_PREFIX = "repair."

T = TypeVar("T")


def utc_now_iso() -> str:
    """当前时刻 ISO-8601（UTC，秒精度足够；审计链自带毫秒精度 ts）"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """本次修复运行标识（``rep-<12hex>``）"""
    return f"rep-{uuid.uuid4().hex[:12]}"


def slugify(text: str, *, limit: int = 48) -> str:
    """把失败用例名压成可读 slug（分支名/产物名用；只留安全字符）"""
    out: List[str] = []
    for ch in str(text or "").strip():
        if ch.isalnum():
            out.append(ch.lower())
        elif ch in "-_. ":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug[:limit].strip("-")) or "issue"


def short_hash(text: str, *, length: int = 16) -> str:
    """文本哈希前 N 位（指纹/证据锚点用）"""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:length]


def _workspace_id(repo_root: str) -> str:
    """会话工作区 id（复用 S2-01 的 P7.1-19 派生口径，禁止自造一套）"""
    try:
        from agent.observability.trace_v2 import derive_workspace_id
        return derive_workspace_id(repo_root)
    except Exception as exc:  # noqa: BLE001 派生失败不得阻断流水线
        logger.warning("workspace_id 派生失败（%s）——本次 Trace 将降级", exc)
        return ""


@dataclass
class RunLoggerConfig:
    """留痕出口配置（全部可注入，便于隔离测试）

    Attributes:
        repo_root: 仓库根（workspace_id 派生源）。
        trace_db: 统一 Trace 库路径（缺省 S2-01 默认库）。
        trace_facade: 显式注入的 ``TraceFacade``（优先于 trace_db）。
        audit: 显式注入的审计 facade（缺省 ``agent.audit.facade.audit``）。
        events_dir: 事件流目录（缺省 S2-01 默认目录）。
        emit_events: 是否发事件（缺省 True；失败不影响主路径）。
        trace_enabled: 是否写 Trace（关闭时``trace_id``仍生成，仅无落库）。
    """

    repo_root: str = ""
    trace_db: str = ""
    trace_facade: Any = None
    audit: Any = None
    events_dir: str = ""
    emit_events: bool = True
    trace_enabled: bool = True


class RepairRunLogger:
    """一次修复运行的留痕器（五步各一条 Trace + 一条链式审计）

    用法::

        logger_ = RepairRunLogger(RunLoggerConfig(repo_root=root), run_id=rid)
        report = logger_.run_step("diagnose", "pytest 体检", lambda: diagnose(...))
        # ↑ 无论 lambda 成功/失败/抛异常，都会留下 Trace + 审计
    """

    def __init__(self, config: RunLoggerConfig, *, run_id: str = "") -> None:
        self.config = config
        self.run_id = run_id or new_run_id()
        self.workspace_id = _workspace_id(config.repo_root)
        self.task_id = f"repair:{self.run_id}"
        self.entries: List[AuditTrailEntry] = []
        self.notes: List[str] = []
        self._store: Any = None
        self._store_error: str = ""
        self._facade: Any = None
        self._facade_error: str = ""

    # ── 出口（懒加载 + 失败降级，绝不阻断主路径）──

    @property
    def store(self) -> Any:
        """统一 Trace 存储（懒加载；不可用返回 None 并记 note）"""
        if self._store is not None or self._store_error:
            self._note_store_issue()
            return self._store
        if not self.config.trace_enabled:
            self._store_error = "Trace 已显式关闭（trace_enabled=False）"
            self._note_store_issue()
            return None
        try:
            if self.config.trace_facade is not None:
                facade = self.config.trace_facade
                self._store = getattr(facade, "store", None) or getattr(facade, "_store", None)
                if self._store is None:
                    self._store_error = "注入的 TraceFacade 无 store 属性"
            else:
                from agent.observability.trace_v2 import UnifiedTraceStore
                self._store = UnifiedTraceStore(self.config.trace_db or None)
        except Exception as exc:  # noqa: BLE001
            self._store_error = f"{type(exc).__name__}: {exc}"
            logger.warning("统一 Trace 不可用，本次运行降级（%s）", self._store_error)
            self._store = None
        self._note_store_issue()
        return self._store

    def _note_store_issue(self) -> None:
        """把 Trace 降级原因记入 ``notes``（去重；供报告如实披露）"""
        if not self._store_error:
            return
        note = f"Trace 降级：{self._store_error}"
        if note not in self.notes:
            self.notes.append(note)

    @property
    def facade(self) -> Any:
        """链式审计 facade（懒加载；不可用返回 None）"""
        if self._facade is not None or self._facade_error:
            return self._facade
        try:
            if self.config.audit is not None:
                self._facade = self.config.audit
            else:
                from agent.audit.facade import audit as _audit
                self._facade = _audit
        except Exception as exc:  # noqa: BLE001
            self._facade_error = f"{type(exc).__name__}: {exc}"
            logger.warning("审计 facade 不可用，本次运行降级（%s）", self._facade_error)
            self._facade = None
        if self._facade is None and self._facade_error:
            self.notes.append(f"审计降级：{self._facade_error}")
        return self._facade

    # ── 写 ──

    def record_step(self, step: str, *, subject: str = "", status: str = "ok",
                    trace_id: str = "", duration_ms: float = 0.0,
                    detail: Optional[Mapping[str, Any]] = None,
                    error: str = "", capability_id: str = "") -> AuditTrailEntry:
        """写一条「一步」留痕（Trace + 审计 + 可选事件）

        Args:
            step: 步名（``models.REPAIR_STEPS`` 之一）。
            subject: 受影响对象（用例 id / 分支名 / 「丢弃」原因等）。
            status: ``ok`` / ``error`` / ``discarded`` / ``skipped``。
            trace_id: 该步 Trace 的 trace_id。
            duration_ms: 耗时。
            detail: **叶子字段**明细。
            error: 失败原因（status != ok 时必填才便于人读）。
            capability_id: 覆盖 Trace 的 capability 名（缺省 ``repair.<step>``）。

        Returns:
            ``AuditTrailEntry``（含审计链 seq，可为 None）。
        """
        detail_map: Dict[str, Any] = {str(k): v for k, v in dict(detail or {}).items()}
        if error:
            detail_map["error"] = str(error)[:400]
        entry = AuditTrailEntry(step=str(step), action=AUDIT_ACTIONS.get(
            step, f"repair.{step}"), actor=ACTOR_PIPELINE, subject=str(subject or ""),
            status=str(status or "ok"),
            # 每步恒有 trace_id：缺省即生成，使「一步一 Trace」成为结构保证，
            # 而不是"记得传"的约定（漏传会让 Trace 与审计对不上）。
            trace_id=str(trace_id or uuid.uuid4().hex[:32]),
            duration_ms=float(duration_ms), detail=detail_map)

        # ① 链式审计（唯一写入入口；best-effort，失败不阻断）
        audit = self.facade
        if audit is not None:
            try:
                written = audit.record(
                    entry.action, actor=ACTOR_PIPELINE, subject=entry.subject,
                    payload=dict(detail_map), trace_id=entry.trace_id,
                    workspace_id=self.workspace_id, status=entry.status)
                if written is not None:
                    entry.seq = int(getattr(written, "seq", 0) or 0) or None
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"审计写入失败（{entry.action}）：{type(exc).__name__}")
                logger.warning("审计写入失败（%s）：%s", entry.action, exc)

        # ② 统一 Trace（actor=auto；委派另有子代理子 Trace）
        self._record_trace(entry, capability_id=capability_id or f"{CAPABILITY_PREFIX}{step}")

        self.entries.append(entry)
        return entry

    def _record_trace(self, entry: AuditTrailEntry, *, capability_id: str) -> None:
        store = self.store
        if store is None:
            return
        try:
            from agent.observability.trace_v2 import (
                ACTOR_AUTO,
                STATUS_ERROR,
                STATUS_SUCCESS,
                Response,
                SideEffects,
                Tenancy,
                Timing,
                UnifiedTrace,
            )
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"Trace 数据类不可用：{type(exc).__name__}")
            return
        ok = entry.status in ("ok", "success")
        trace = UnifiedTrace(
            trace_id=entry.trace_id or uuid.uuid4().hex[:32],
            task_id=self.task_id,
            capability_id=capability_id,
            actor=ACTOR_AUTO,
            tenancy=Tenancy(tenant_id="default", workspace_id=self.workspace_id),
            response=Response(status=STATUS_SUCCESS if ok else STATUS_ERROR,
                              output_redacted={"step": entry.step, "status": entry.status},
                              error_code="" if ok else str(entry.status)),
            timing=Timing(started_at=time.time(),
                          finished_at=time.time(),
                          duration_ms=float(entry.duration_ms)),
            side_effects=SideEffects(
                notes=[f"repair_run={self.run_id}", f"subject={entry.subject}"]),
        )
        try:
            store.record(trace)
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"Trace 记录失败（{entry.step}）：{type(exc).__name__}")
            logger.warning("Trace 记录失败（%s）：%s", entry.step, exc)

    def emit_event(self, event_type: str, payload: Mapping[str, Any], *,
                   correlation_id: str = "") -> Optional[Any]:
        """发一条事件（best-effort；事件名须为 S2-01 已登记类型）"""
        if not self.config.emit_events:
            return None
        try:
            from agent.observability.events import emit
            kwargs: Dict[str, Any] = {"actor": ACTOR_PIPELINE,
                                      "correlation_id": correlation_id or self.run_id}
            if self.config.events_dir:
                from agent.observability.events import EventStore
                kwargs["store"] = EventStore(self.config.events_dir)
            return emit(str(event_type), {str(k): v for k, v in dict(payload).items()},
                        **kwargs)
        except Exception as exc:  # noqa: BLE001 事件失败不影响主路径
            self.notes.append(f"事件发射失败（{event_type}）：{type(exc).__name__}")
            return None

    # ── 包一层：无论成败必留痕 ──

    def run_step(self, step: str, subject: str, func: Callable[[], T], *,
                 detail: Optional[Mapping[str, Any]] = None) -> T:
        """执行一步并保证留痕（成功/失败/抛异常三条路径都写）

        Args:
            step: 步名。
            subject: 受影响对象。
            func: 该步主体（无参可调用）。
            detail: 叶子明细（随留痕一并写入）。

        Returns:
            ``func()`` 的返回值。

        Raises:
            原样重抛 ``func()`` 的异常（先留痕，再抛）。
        """
        trace_id = uuid.uuid4().hex[:32]
        started = time.perf_counter()
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 留痕后原样重抛
            self.record_step(step, subject=subject, status="error", trace_id=trace_id,
                             duration_ms=(time.perf_counter() - started) * 1000.0,
                             detail=detail, error=f"{type(exc).__name__}: {exc}")
            raise
        self.record_step(step, subject=subject, status="ok", trace_id=trace_id,
                         duration_ms=(time.perf_counter() - started) * 1000.0,
                         detail=detail)
        return result

    # ── 读 ──

    def trail(self) -> List[AuditTrailEntry]:
        """本次运行的全部留痕（顺序即执行顺序）"""
        return list(self.entries)

    def steps_recorded(self) -> Tuple[str, ...]:
        """已留痕的步名（去重保序）"""
        seen: List[str] = []
        for entry in self.entries:
            if entry.step not in seen:
                seen.append(entry.step)
        return tuple(seen)

    def missing_steps(self, steps: Tuple[str, ...] = REPAIR_STEPS) -> Tuple[str, ...]:
        """缺失留痕的步（审计完整性断言用）"""
        recorded = set(self.steps_recorded())
        return tuple(s for s in steps if s not in recorded)

    def flush(self) -> bool:
        """把 Trace 落盘（审计另有 writer 线程，由调用方 close）"""
        store = self.store
        if store is None:
            return False
        try:
            return bool(store.flush(timeout=2.0))
        except Exception:  # noqa: BLE001
            return False

    def verify_chain(self) -> Optional[bool]:
        """校验链式审计（``AuditFacade.verify`` → ``chain.verify_chain``）

        Returns:
            True/False（可校验）；None = 审计不可用或未启用（**不是"通过"**）。
        """
        facade = self.facade
        if facade is None:
            return None
        try:
            result = facade.verify()
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"审计链校验失败：{type(exc).__name__}")
            return None
        ok = getattr(result, "ok", None)
        if ok is None and isinstance(result, Mapping):
            ok = result.get("ok")
        return bool(ok) if ok is not None else None


#: 五步名（再次导出，便于调用方以 ``from agent.repair.trace import STEPS`` 取用）
STEPS: Tuple[str, ...] = REPAIR_STEPS


__all__ = [
    "ACTOR_PIPELINE", "ACTOR_SUB_AGENT_PREFIX", "CAPABILITY_PREFIX", "STEPS",
    "utc_now_iso", "new_run_id", "slugify", "short_hash",
    "RunLoggerConfig", "RepairRunLogger",
    "STEP_DIAGNOSE", "STEP_LOCATE", "STEP_VERIFY", "STEP_PROPOSE",
]
