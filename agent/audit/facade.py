"""统一审计门面 `audit.record(...)`（P7.2-24 审计平权：UI 与 Agent 同表）

【任务定位】
    v7.2 §3.5 链式审计的**唯一写入入口**。UI 操作日志与 Agent 操作日志落到
    **同一张链式审计表**（`agent/audit/chain.py` 的 `audit_chain`），否则攻击者
    走管理后台即可绕过治理（P7.2-24）。

【门面契约】
    audit.record(action, actor=None, subject="", payload=None, source="agent"|"ui")
    - actor 缺省时按上下文解析：UI 请求上下文 → Agent TraceContext → "system"
      （解析来源随记录落 `actor_source`，不做无来源的臆造）；
    - payload **先脱敏再入链**（复用 `trace_v2.redact`，不可用时内置兜底掩码），
      敏感原文不入链；
    - **best-effort**：审计失败不阻断主路径（返回 None + 计数 + 告警），
      失败原因可经 `snapshot()["last_error"]` 查；链本身有降级 ring buffer 不丢记录。

【单写者】
    门面内部走 `chain.get_audit_chain()`（进程内每路径唯一 writer），因此路由 /
    Scheduler / 后台线程统一调用本门面即为「进程内 IPC 提交」，不会各自开 writer。

【环境开关（全部带默认值，可运行时降级/回滚）】
    AUDIT_CHAIN_ENABLED   链式审计总开关，默认 1（0 = 完全停写，直接返回 None）
    AUDIT_DUAL_WRITE      双写过渡开关（旧 JSONL + 新链），默认 1；置 0 = 仅旧轨（回滚窗口）
    AUDIT_DB_PATH         链式台账路径，默认 data/audit/audit_chain.db
    AUDIT_ROOTS_PATH      每日 Merkle 根路径，默认 data/audit/daily_roots.jsonl
    AUDIT_SIGNING_KEY     每日根 ed25519 私钥路径，默认 data/audit/audit_signing_key.pem
"""

from __future__ import annotations

import logging
import os
import threading
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from agent.audit.chain import (
    DEFAULT_DB_PATH,
    DEFAULT_KEY_PATH,
    DEFAULT_ROOTS_PATH,
    SOURCE_AGENT,
    SOURCE_UI,
    SOURCES,
    AuditChain,
    AuditEntry,
    ChainVerification,
    get_audit_chain,
)

logger = logging.getLogger("agent.audit.facade")

_ENV_ENABLED = "AUDIT_CHAIN_ENABLED"
_ENV_DUAL_WRITE = "AUDIT_DUAL_WRITE"
_ENV_DB_PATH = "AUDIT_DB_PATH"
_ENV_ROOTS_PATH = "AUDIT_ROOTS_PATH"
_ENV_KEY_PATH = "AUDIT_SIGNING_KEY"

#: UI 请求上下文中的操作者（由 `agent.audit.ui_middleware` 设置；未设置时 None）
_ui_actor_var: ContextVar = ContextVar("audit_ui_actor", default=None)
#: UI 请求上下文中的来源信息（ip / endpoint / 身份来源等，仅叶子字段）
_ui_context_var: ContextVar = ContextVar("audit_ui_context", default=None)


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def set_ui_actor(actor: str, **context: Any) -> Any:
    """设置当前上下文的 UI 操作者（返回 token，供 reset）"""
    token_actor = _ui_actor_var.set(str(actor or ""))
    token_ctx = _ui_context_var.set({k: v for k, v in context.items() if v is not None})
    return (token_actor, token_ctx)


def get_ui_context() -> Dict[str, Any]:
    """当前 UI 请求上下文（无 → 空 dict）"""
    return dict(_ui_context_var.get() or {})


def reset_ui_actor(tokens: Any) -> None:
    """还原 UI 操作者上下文"""
    try:
        token_actor, token_ctx = tokens
        _ui_actor_var.reset(token_actor)
        _ui_context_var.reset(token_ctx)
    except Exception:  # noqa: BLE001 上下文 token 跨上下文重置失败 → 显式清空
        _ui_actor_var.set(None)
        _ui_context_var.set(None)


def redact_payload(payload: Any) -> Any:
    """载荷脱敏（主路径复用 trace_v2.redact；不可用时内置兜底掩码）"""
    try:
        from agent.observability.trace_v2 import redact as _redact
        return _redact(payload)
    except Exception:  # noqa: BLE001 依赖缺失/异常 → 兜底（绝不放原文入链）
        try:
            from agent.observability.trace_v2 import _fallback_redact
            return _fallback_redact(payload)
        except Exception:  # noqa: BLE001
            return _minimal_redact(payload)


_SENSITIVE_KEYS = ("password", "passwd", "pwd", "secret", "api_key", "apikey", "token",
                   "auth", "credential", "private_key", "authorization")
_REDACTED = "********"


def _minimal_redact(data: Any) -> Any:
    """最小兜底掩码（仅按字段名，不做文本级规则）"""
    if isinstance(data, dict):
        out: Dict[Any, Any] = {}
        for k, v in data.items():
            key = str(k).lower()
            if any(p in key for p in _SENSITIVE_KEYS):
                out[k] = _REDACTED
            else:
                out[k] = _minimal_redact(v)
        return out
    if isinstance(data, list):
        return [_minimal_redact(it) for it in data]
    return data


class AuditFacade:
    """统一审计门面（UI / Agent 同表；链式追加由 AuditChain 保证）

    Args:
        db_path: 链式台账路径（None → 环境变量 → 默认 `data/audit/audit_chain.db`）。
        roots_path: 每日根路径。
        enabled: 总开关（None → 环境变量 AUDIT_CHAIN_ENABLED）。
        dual_write: 双写过渡开关（None → 环境变量 AUDIT_DUAL_WRITE），置 False 即
            回滚为「仅旧轨」。
        chain: 直接注入台账实例（测试用；None → 懒加载进程单例）。
        strict: True 时 append 异常向上抛（默认 False = best-effort 不阻断主路径）。
    """

    def __init__(self, *, db_path: Optional[str] = None, roots_path: Optional[str] = None,
                 signing_key_path: Optional[str] = None,
                 enabled: Optional[bool] = None, dual_write: Optional[bool] = None,
                 chain: Optional[AuditChain] = None, strict: bool = False,
                 auto_seal: Optional[bool] = None):
        self._db_path = db_path or os.getenv(_ENV_DB_PATH) or DEFAULT_DB_PATH
        self._roots_path = roots_path or os.getenv(_ENV_ROOTS_PATH) or DEFAULT_ROOTS_PATH
        self._key_path = (signing_key_path or os.getenv(_ENV_KEY_PATH)
                          or DEFAULT_KEY_PATH)
        self._enabled = _env_flag(_ENV_ENABLED) if enabled is None else bool(enabled)
        self._dual_write = (_env_flag(_ENV_DUAL_WRITE) if dual_write is None
                            else bool(dual_write))
        self._strict = bool(strict)
        self._auto_seal = auto_seal
        self._chain: Optional[AuditChain] = chain
        self._lock = threading.Lock()
        self._failure_count = 0
        self._success_count = 0
        self._last_error = ""

    # ── 属性/懒加载 ─────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def dual_write(self) -> bool:
        """双写过渡开关（旧 JSONL 轨 + 新链轨同时写）"""
        return self._dual_write

    @dual_write.setter
    def dual_write(self, value: bool) -> None:
        self._dual_write = bool(value)

    @property
    def chain(self) -> Optional[AuditChain]:
        """链式台账（懒加载进程单例；已关闭则重建；enabled=False 时不创建）"""
        if not self._enabled:
            return None
        if self._chain is None or self._chain.closed:
            with self._lock:
                if self._chain is None or self._chain.closed:
                    kwargs: Dict[str, Any] = {"roots_path": self._roots_path,
                                              "signing_key_path": self._key_path}
                    if self._auto_seal is not None:
                        kwargs["auto_seal"] = self._auto_seal
                    self._chain = get_audit_chain(self._db_path, **kwargs)
        return self._chain

    def bind(self, chain: Optional[AuditChain]) -> Optional[AuditChain]:
        """注入/替换台账实例（测试隔离用）；返回被替换的旧实例"""
        with self._lock:
            previous = self._chain
            self._chain = chain
            return previous

    # ── 上下文解析 ──────────────────────────────────────────

    def resolve_actor(self, actor: Optional[str] = None,
                      source: str = SOURCE_AGENT) -> Any:
        """解析操作者 → (actor, actor_source)

        顺序：显式 actor > UI 请求上下文 > Agent TraceContext > "system"。
        """
        if actor:
            return str(actor), "explicit"
        ui_actor = _ui_actor_var.get()
        if ui_actor:
            return str(ui_actor), "ui_request_context"
        ctx = _trace_context_leaf()
        # TraceContext 无 actor 字段（S2-01 裁定）：用其 subject_id 作操作者标识，
        # 来源如实标注 agent_trace_subject，不把 subject 冒充为 actor
        if ctx.get("subject_id"):
            return str(ctx["subject_id"]), "agent_trace_subject"
        return "system", "default_system"

    # ── 写入 ────────────────────────────────────────────────

    def record(self, action: str, actor: Optional[str] = None, subject: str = "",
               payload: Optional[Dict[str, Any]] = None, *, source: str = SOURCE_AGENT,
               trace_id: str = "", workspace_id: str = "", status: str = "",
               ts: Any = None, extra: Optional[Dict[str, Any]] = None,
               technical: Optional[Dict[str, Any]] = None) -> Optional[AuditEntry]:
        """记录一条审计（UI 与 Agent 同表；**唯一写入入口**）

        Args:
            action: 动作名（如 `skill.delete` / `approval.approve` / `trace.redact`）。
            actor: 操作者；None → 上下文解析（UI 用户 / Trace actor / "system"）。
            subject: 受影响资源（如 `skill:foo` / `approval:appr-...` / 路径）。
            payload: 载荷（自动脱敏；原文不入链）。
            source: "agent" / "ui" / "system" / "migration"。
            trace_id / workspace_id: 关联字段（None/"" → 尝试从 TraceContext 取）。
            status: 结果状态（落 payload.status，便于查询）。
            ts: 覆盖时间戳（测试/回填用）。
            extra: 追加到 payload 的叶子字段（如 endpoint/status_code/duration_ms）；
                **同样经过脱敏**。
            technical: 内部生成的技术字段（如 `audit_ref`/`audit_track` 关联键），
                **不经脱敏**——脱敏启发式会误伤随机 hex 关联键（实测 24 位 hex 被
                部分掩码），故关联键单独走此通道。**切勿放入用户输入或密钥**。

        Returns:
            AuditEntry（成功）或 None（审计关闭/失败；best-effort 不阻断主路径）。
        """
        if not self._enabled:
            return None
        try:
            src = str(source or SOURCE_AGENT)
            if src not in SOURCES:
                src = SOURCE_AGENT
            resolved_actor, actor_source = self.resolve_actor(actor, src)
            ctx = _trace_context_leaf()
            body: Dict[str, Any] = {}
            if payload:
                red = redact_payload(payload)
                body["payload"] = red if isinstance(red, dict) else {"value": red}
            if status:
                body["status"] = str(status)
            if extra:
                red_extra = redact_payload(extra)
                if isinstance(red_extra, dict):
                    body.update(red_extra)
            if technical:
                body.update({str(k): v for k, v in technical.items()})
            body["actor_source"] = actor_source
            body.setdefault("schema", "audit.chain.v1")
            chain = self.chain
            if chain is None:
                return None
            entry = chain.append(
                action=str(action), actor=resolved_actor, subject=str(subject or ""),
                payload=body, source=src,
                trace_id=trace_id or ctx.get("trace_id", ""),
                workspace_id=workspace_id or ctx.get("workspace_id", ""),
                ts=ts)
            with self._lock:
                self._success_count += 1
            return entry
        except Exception as e:  # noqa: BLE001 审计 best-effort：绝不阻断主路径
            with self._lock:
                self._failure_count += 1
                self._last_error = f"{type(e).__name__}: {e}"
            logger.warning("审计写入失败（已计数，不影响主路径）: %s", e)
            if self._strict:
                raise
            return None

    # 语义化别名（UI/Agent 两侧调用点自解释，便于审计口径审查）
    def record_agent(self, action: str, actor: str = "", subject: str = "",
                     payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Optional[AuditEntry]:
        kwargs.pop("source", None)
        return self.record(action, actor, subject, payload, source=SOURCE_AGENT, **kwargs)

    def record_ui(self, action: str, actor: str = "", subject: str = "",
                  payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Optional[AuditEntry]:
        kwargs.pop("source", None)
        return self.record(action, actor, subject, payload, source=SOURCE_UI, **kwargs)

    # ── S2-01 Trace 关键事件接线（含遗留 #3 trace.redact 入链） ──

    def record_trace_event(self, trace: Any, *, event: str = "trace.closed",
                           extra: Optional[Dict[str, Any]] = None) -> Optional[AuditEntry]:
        """把一条统一 Trace（UnifiedTrace 或 dict）的关键叶子上链

        只读叶子字段（trace_id / task_id / capability_id / actor / status /
        workspace_id / cost.*），不序列化 live 对象、不搬运 live 数据。
        """
        try:
            d = trace.to_dict() if hasattr(trace, "to_dict") else dict(trace or {})
        except Exception:  # noqa: BLE001 live 对象不可序列化 → 放弃本次审计
            return None
        tenancy = d.get("tenancy") or {}
        pricing = d.get("cost") or {}
        response = d.get("response") or {}
        timing = d.get("timing") or {}
        payload: Dict[str, Any] = {
            "capability_id": str(d.get("capability_id") or ""),
            "task_id": str(d.get("task_id") or ""),
            "trace_status": str(response.get("status") or ""),
            "error_code": str(response.get("error_code") or ""),
            "duration_ms": timing.get("duration_ms"),
            "total_tokens": pricing.get("total_tokens"),
            "cost_usd": pricing.get("cost_usd"),
        }
        if extra:
            payload.update(extra)
        return self.record(
            event, actor=str(d.get("actor") or ""),
            subject=f"trace:{d.get('trace_id') or ''}", payload=payload,
            source=SOURCE_AGENT, trace_id=str(d.get("trace_id") or ""),
            workspace_id=str(tenancy.get("workspace_id") or ""))

    def record_redact_event(self, *, field_count: int = 0, kinds: Optional[List[str]] = None,
                            trace_id: str = "", actor: str = "",
                            workspace_id: str = "") -> Optional[AuditEntry]:
        """S2-01 遗留 #3：脱敏动作入链（只记计数与字段类别，**绝不记被脱敏的值**）"""
        return self.record(
            "trace.redact", actor=actor, subject=f"trace:{trace_id}" if trace_id else "trace",
            payload={"field_count": int(field_count),
                     "kinds": sorted(set(kinds or [])),
                     "values_recorded": False},
            source=SOURCE_AGENT, trace_id=trace_id, workspace_id=workspace_id)

    # ── 读/验 ───────────────────────────────────────────────

    def recent(self, limit: int = 50, **filters: Any) -> List[AuditEntry]:
        """最近记录（seq 升序取尾部 limit 条；支持 source/action/actor/day 过滤）"""
        chain = self.chain
        if chain is None:
            return []
        rows = chain.entries(**filters)
        return rows[-int(limit):] if limit else rows

    def verify(self, **kwargs: Any) -> ChainVerification:
        """链式验签（委托 AuditChain.verify_chain）"""
        chain = self.chain
        if chain is None:
            return ChainVerification(ok=True, checked=0, reason="disabled",
                                     detail="链式审计未启用（AUDIT_CHAIN_ENABLED=0）")
        return chain.verify_chain(**kwargs)

    def daily_merkle_root(self, date: Any = None, **kwargs: Any) -> Any:
        chain = self.chain
        return None if chain is None else chain.daily_merkle_root(date, **kwargs)

    def snapshot(self) -> Dict[str, Any]:
        """门面自身状态（写入计数/失败/开关/链摘要），供面板与验收报告引用"""
        chain = self._chain
        out: Dict[str, Any] = {
            "enabled": self._enabled,
            "dual_write": self._dual_write,
            "db_path": self._db_path,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "last_error": self._last_error,
        }
        if chain is not None:
            out["chain"] = chain.stats(verify=False)
        return out

    def reset_counters(self) -> None:
        """清零门面计数（测试用）"""
        with self._lock:
            self._success_count = 0
            self._failure_count = 0
            self._last_error = ""

    def close(self) -> None:
        """关闭台账（幂等；不清理进程单例，供测试显式收尾）"""
        chain = self._chain
        if chain is not None:
            chain.close()


def _trace_context_leaf() -> Dict[str, str]:
    """读取 TraceContext 的叶子字段（trace_id/workspace_id/subject_id）；不可用 → 空"""
    try:
        from agent.observability.trace_v2 import TraceContext
        ctx = TraceContext.current()
        if ctx is None:
            return {}
        return {
            "trace_id": str(getattr(ctx, "trace_id", "") or ""),
            "subject_id": str(getattr(ctx, "subject_id", "") or ""),
            "workspace_id": str(getattr(ctx, "workspace_id", "") or ""),
        }
    except Exception:  # noqa: BLE001 TraceContext 不可用 → 无上下文（不臆造）
        return {}


#: 进程级统一审计门面（UI/Agent/Scheduler 共用；**唯一推荐入口**）
audit = AuditFacade()


def record(action: str, actor: Optional[str] = None, subject: str = "",
           payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Optional[AuditEntry]:
    """模块级便捷入口：`from agent.audit import audit; audit.record(...)` 的等价形式"""
    return audit.record(action, actor, subject, payload, **kwargs)


def get_audit() -> AuditFacade:
    """取得进程级审计门面"""
    return audit


def reset_audit_facade() -> None:
    """重置进程级门面状态（**测试专用**：解除注入台账 + 清零计数 + 关链）"""
    audit.close()
    audit.bind(None)
    audit.reset_counters()


__all__ = [
    "AuditFacade", "audit", "get_audit", "get_ui_context", "record", "redact_payload",
    "reset_audit_facade", "reset_ui_actor", "set_ui_actor",
]
