"""PolicyEngine — 决策引擎（§5.6：``check(policy_ctx) -> {allow|deny|ask, policy_id}``）

【这个引擎能做什么、不能做什么（P7.1-20 是本模块的第一约束）】

    能做：
        - 读纯 JSON 的决策输入（``PolicyContext.input``），按 OPA 子集语义求值；
        - 返回一个**决策**：``allow`` / ``deny`` / ``ask`` + 命中的 ``policy_id``；
        - 把决策写进链式审计（S2-02）与事件埋点（S2-03 / §6.6）；
        - 缓存决策（LRU，策略变更即失效），并把耗时样本留下作为性能证据。

    **不能做**（写进类型与测试，不是靠自觉）：
        - 不做任何网络动作：本包不 import 网络/子进程库（AST 用例守着）；
          ``match`` 里出现 ``http.send`` 一类 token 在装载期就被拒
          （``models.FORBIDDEN_MATCH_TOKENS``）；
        - 不做文件动作、不写策略库、不改权限——**决策即止**；
        - 不审批 ``ask``：``ask`` 只是「要人」的信号，路由到审批/收件箱由执行点
          与 ``inbox.py`` 完成（START S4-02 §七.4：ask 与审批流是两个概念）。

【核心不变量：策略层只收敛，不放宽】
    这是本引擎与「策略即代码」最容易被做错的地方，单独写明：

        ``effect: allow``（以及未命中）**不等于**放行。它只表示「策略层对这次操作
        没有意见」。执行点在放行前**仍须**通过它自己既有的判定（权限网关的
        RBAC/ABAC/正则、egress guard 的网络白名单）。因此：

        - 引入策略引擎**不可能**让原本被拒的操作变成允许（不放宽）；
        - 策略未覆盖（``matched=False``）时执行点回落到既有判定，**零行为回归**。

    唯一的方向性例外是 break-glass：它是**由人显式授予、带 TTL、入审计**的临时
    例外，且只对 ``break_glass_ttl_min`` 非 null 的策略生效（见 ``grant_break_glass``）。

【判定顺序】
    1. **内置不变量**（``store.builtin_policies``）：契约级规则，排在最前，不可被
       文件策略遮蔽（否则一条宽泛 allow 就能静默关掉 secret 出域禁令）。
    2. 文件/API 装载的策略：**按策略库插入序**（即策略文件的书写顺序）自上而下。
    3. **首个命中生效**（任务书 §二 步骤 2 指定语义）。顺序敏感性由
       ``PolicyStore.shadow_report()`` 显式诊断，并由模拟器报告提示人工确认。

【缓存与失效】
    缓存键 = ``sha256(store.revision | grant_epoch | canonical(ctx.input))``：
    策略库任何写操作都推进 ``revision``，因此「策略变更 ⇒ 旧键全部不命中」是
    结构性成立的，不依赖调用方记得清缓存。``invalidate()`` 用于显式清空（并推进
    ``grant_epoch``）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from agent.policy.decisions import DecisionLog
from agent.policy.matcher import MatchEvaluator
from agent.policy.models import (
    EFFECT_ALLOW,
    EFFECT_ASK,
    EFFECT_DENY,
    Policy,
    PolicyContext,
    PolicyDecision,
    now_iso,
    render_message,
    sha256_hex,
)
from agent.policy.store import PolicyStore, get_policy_store

logger = logging.getLogger("agent.policy.engine")

#: 环境变量：决策缓存容量（"0" 关闭缓存）
ENV_CACHE_SIZE = "CP_POLICY_CACHE_SIZE"
#: 环境变量：是否写审计/事件埋点（"0" 关闭）
ENV_OBSERVE = "CP_POLICY_OBSERVE"
#: 环境变量：埋点范围（``all`` 默认 / ``governance``）
ENV_OBSERVE_SCOPE = "CP_POLICY_OBSERVE_SCOPE"

#: 原因码（机器可读；写进审计的 ``reason_code``）
REASON_NO_MATCH = "no_policy_match"
REASON_POLICY_ALLOW = "policy_allow"
REASON_POLICY_DENY = "policy_deny"
REASON_POLICY_ASK = "policy_ask"
REASON_BREAK_GLASS = "break_glass"
REASON_ENGINE_ERROR = "engine_error"

#: 默认缓存容量（条目）
DEFAULT_CACHE_SIZE = 2048
#: 耗时样本窗口（p99 证据；有界，不随调用次数增长）
DEFAULT_LATENCY_WINDOW = 4096


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default  # 非法值回退默认（通用硬约束 3）


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _is_governance_relevant(decision: PolicyDecision) -> bool:
    """决策是否值得进防篡改账本（``scope=governance`` 时的判据）

    算：deny / ask / break-glass / 命中策略的 allow（有策略参与即有人负责）。
    不算：``matched=False`` 的 allow（策略层没有意见，既无策略参与也无治理后果）。
    """
    if decision.effect in (EFFECT_DENY, EFFECT_ASK) or decision.break_glass:
        return True
    return bool(decision.matched)


# ════════════════════════════════════════════════════════════
#  break-glass 例外
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BreakGlassGrant:
    """一条 break-glass 例外（**必须由人显式授予**）

    Attributes:
        grant_id: 例外 id（入审计与决策记录的关联键）。
        policy_id: 被例外的策略 id。
        actor: 授予人（必填，非空）。
        reason: 授予理由（必填，非空；「为什么这次可以例外」是事后追责的唯一线索）。
        granted_at / expires_at: ISO-8601（本地带偏移）。
        ttl_min: 实际 TTL（分钟）；受策略 ``break_glass_ttl_min`` 上限约束。
        capability_id / tenant_id: 可选的绑定范围（空＝不绑定）。
    """

    grant_id: str
    policy_id: str
    actor: str
    reason: str
    granted_at: str
    expires_at: str
    ttl_min: int
    capability_id: str = ""
    tenant_id: str = ""

    def is_expired(self, ts: Optional[str] = None) -> bool:
        return (ts or now_iso()) > self.expires_at

    def covers(self, policy_id: str, *, capability_id: str = "",
               tenant_id: str = "") -> bool:
        """是否覆盖某次判定（策略 + 可选范围绑定 + 未过期）"""
        if self.policy_id != policy_id or self.is_expired():
            return False
        if self.capability_id and self.capability_id != capability_id:
            return False
        if self.tenant_id and self.tenant_id != tenant_id:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grant_id": self.grant_id, "policy_id": self.policy_id,
            "actor": self.actor, "reason": self.reason,
            "granted_at": self.granted_at, "expires_at": self.expires_at,
            "ttl_min": self.ttl_min, "capability_id": self.capability_id,
            "tenant_id": self.tenant_id,
        }


class BreakGlassError(Exception):
    """break-glass 授予失败（策略不存在 / 不允许例外 / 参数非法）"""


# ════════════════════════════════════════════════════════════
#  可观测性桥（审计 + 事件）
# ════════════════════════════════════════════════════════════


class DecisionObserver:
    """把决策写进链式审计（S2-02）与事件埋点（S2-03 / §6.6）

    **写入纪律**（与 S2 同款）：
    - 只用叶子投影 ``PolicyDecision.audit_leaves()``——绝不写 ``match`` 的匹配值；
    - best-effort：任何异常都被吞掉并计数，**绝不阻断决策路径**；
    - 一次决策固定产出：
        * 审计链动作 ``policy.decision``（§6.6 埋点字段）
        * 事件 ``policy.decision``
        * 若 deny：事件 ``policy.denied``（S2-03 既有治理事件，自动镜像入链）
        * 若 ask / break-glass：事件 ``intervention``（kind=``policy.ask`` /
          ``policy.break_glass``）——「收件箱只收例外」的信号位

    **埋点成本（实测，见 ``scripts/bench_policy_cache.py --with-io``）**：
        链式审计是 SQLite 追加，是这条路径的主要开销。实测命中路径 p50 从
        **0.022 ms（无埋点）升到 0.888 ms**，p99 从 **0.047 ms 升到 3.764 ms**；
        未命中路径 p99 从 0.232 ms 升到 7.97 ms（**越过 5 ms 预算**）。
        也就是说：§5.6 的「决策缓存 p99<5ms」在**决策本体**上余量约 105×，
        但**加上全量埋点后余量只剩 1.3×**（命中路径）。

        因此提供 ``scope`` 开关（``CP_POLICY_OBSERVE_SCOPE``）：默认 ``all``
        满足「决策入链式审计」的字面要求；``governance`` 只为治理相关决策
        （deny/ask/break-glass/命中策略的 allow）写链与事件，把
        ``matched=False`` 的「策略层无异议」剔出防篡改账本（仍写决策日志，
        模拟器数据不受影响）。**默认不变**——降噪是部署侧的显式选择，
        不是本任务替运维做的决定。
    """

    def __init__(self, *, enabled: Optional[bool] = None,
                 emit_events: bool = True, audit_enabled: bool = True,
                 scope: Optional[str] = None) -> None:
        self._enabled = _env_flag(ENV_OBSERVE, "1") if enabled is None else bool(enabled)
        self._emit_events = bool(emit_events)
        self._audit_enabled = bool(audit_enabled)
        #: 埋点范围（云枢裁定，实测依据见模块 docstring「埋点成本」）：
        #:
        #:   - ``all``（默认）：每条决策都写链式审计 + 事件。满足「决策入链式审计」
        #:     的字面要求，代价是**每次出域判定约 0.9~3.8 ms**（链式审计是 SQLite
        #:     追加，是这条路径的主要开销）。
        #:   - ``governance``：只为**治理相关**的决策写审计/事件——即 deny / ask /
        #:     break-glass / 命中策略的 allow。``matched=False`` 的「策略层无异议」
        #:     不写链与事件（但仍写决策日志，模拟器数据不受影响），因为它既没有
        #:     策略参与、也没有治理后果，写进防篡改账本只增加噪声与成本。
        raw = str(scope if scope is not None
                  else os.environ.get(ENV_OBSERVE_SCOPE) or "all").strip().lower()
        self._scope = raw if raw in ("all", "governance") else "all"
        self._lock = threading.Lock()
        self._audit_count = 0
        self._event_count = 0
        self._failure_count = 0
        self._skipped_count = 0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"enabled": self._enabled, "scope": self._scope,
                    "audit_count": self._audit_count,
                    "event_count": self._event_count,
                    "skipped_count": self._skipped_count,
                    "failure_count": self._failure_count,
                    "last_error": self._last_error}

    # ── 主入口 ──

    def observe(self, decision: PolicyDecision, ctx: PolicyContext, *,
                trace_id: str = "", workspace_id: str = "") -> None:
        if not self._enabled:
            return
        if self._scope == "governance" and not _is_governance_relevant(decision):
            with self._lock:
                self._skipped_count += 1
            return
        leaves = decision.audit_leaves()
        try:
            fields = self._trace_fields()
        except Exception:  # noqa: BLE001
            fields = {}
        tid = trace_id or str(fields.get("trace_id") or "")
        wid = workspace_id or str(fields.get("workspace_id") or "")
        self._write_audit(decision, leaves, tid, wid)
        self._write_events(decision, leaves, tid)

    # ── 审计 ──

    def _write_audit(self, decision: PolicyDecision, leaves: Dict[str, Any],
                     trace_id: str, workspace_id: str) -> None:
        if not self._audit_enabled:
            return
        try:
            from agent.audit.facade import audit
            audit.record(
                "policy.decision",
                actor=decision.actor or "policy.engine",
                subject=f"policy:{decision.policy_id}" if decision.policy_id
                else f"policy:<none>:{decision.capability_id or decision.action}",
                payload=leaves,
                source="agent",
                trace_id=trace_id,
                workspace_id=workspace_id,
                technical={"audit_ref": "policy.decision.v1"},
            )
            with self._lock:
                self._audit_count += 1
        except Exception as exc:  # noqa: BLE001 审计 best-effort
            self._note_failure(f"audit: {type(exc).__name__}: {exc}")

    # ── 事件 ──

    def _write_events(self, decision: PolicyDecision, leaves: Dict[str, Any],
                      trace_id: str) -> None:
        if not self._emit_events:
            return
        try:
            from agent.observability import events as ev
        except Exception as exc:  # noqa: BLE001
            self._note_failure(f"events import: {type(exc).__name__}: {exc}")
            return
        payload = dict(leaves)
        payload.pop("latency_ms", None)  # 埋点字段保留，但事件载荷用整数毫秒更省
        payload["latency_ms"] = round(float(decision.latency_ms), 3)
        payload["matched"] = bool(decision.matched)
        try:
            ev.emit("policy.decision", payload, actor=decision.actor or "policy.engine",
                    correlation_id=trace_id)
            with self._lock:
                self._event_count += 1
            if decision.denied:
                ev.emit(ev.EV_POLICY_DENIED, payload,
                        actor=decision.actor or "policy.engine",
                        correlation_id=trace_id)
                with self._lock:
                    self._event_count += 1
            if decision.effect == EFFECT_ASK or decision.break_glass:
                kind = "policy.ask" if decision.effect == EFFECT_ASK else "policy.break_glass"
                ev.emit(ev.EV_INTERVENTION, {
                    "kind": kind,
                    "policy_id": decision.policy_id,
                    "capability_id": decision.capability_id,
                    "actor": decision.actor,
                    "message": decision.message,
                }, actor=decision.actor or "policy.engine", correlation_id=trace_id)
                with self._lock:
                    self._event_count += 1
        except Exception as exc:  # noqa: BLE001 事件 best-effort
            self._note_failure(f"events: {type(exc).__name__}: {exc}")

    @staticmethod
    def _trace_fields() -> Dict[str, str]:
        try:
            from agent.observability.events import trace_fields
            return trace_fields()
        except Exception:  # noqa: BLE001
            return {}

    def _note_failure(self, detail: str) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_error = detail
        logger.warning("策略决策埋点失败（不影响决策）: %s", detail)


# ════════════════════════════════════════════════════════════
#  PolicyEngine
# ════════════════════════════════════════════════════════════


class PolicyEngine:
    """策略决策引擎（§5.6）

    Args:
        store: 策略库；``None`` ⇒ 进程级默认库（``get_policy_store()``）。
        cache_size: 决策缓存容量（0 ⇒ 关闭缓存）。
        decision_log: 决策日志（重放数据源）；``None`` ⇒ 按环境变量新建，
            显式传 ``decision_log=False`` 可完全关闭。
        observer: 审计/事件桥；``None`` ⇒ 默认桥。
        inbox: 例外收件箱适配器；``None`` ⇒ 延迟创建（``inbox.get_policy_inbox()``）。
    """

    def __init__(
        self,
        store: Optional[PolicyStore] = None,
        *,
        cache_size: Optional[int] = None,
        decision_log: Any = None,
        observer: Optional[DecisionObserver] = None,
        inbox: Any = None,
        latency_window: int = DEFAULT_LATENCY_WINDOW,
    ) -> None:
        self._store = store if store is not None else get_policy_store()
        self._cache_size = _env_int(ENV_CACHE_SIZE, DEFAULT_CACHE_SIZE) \
            if cache_size is None else max(0, int(cache_size))
        self._cache: "OrderedDict[str, PolicyDecision]" = OrderedDict()
        self._cache_lock = threading.RLock()
        self._cache_hits = 0
        self._cache_misses = 0
        self._epoch = 0
        #: 缓存建立时所依据的策略库 revision（用于内存回收，见
        #: ``_reclaim_if_store_changed``；``None`` ＝ 尚未读取过）
        self._cache_revision: Optional[int] = None

        self._grants: Dict[str, BreakGlassGrant] = {}
        self._grant_epoch = 0
        self._grant_lock = threading.RLock()

        if decision_log is None:
            self._log: Optional[DecisionLog] = DecisionLog()
        elif decision_log is False:
            self._log = None
        else:
            self._log = decision_log

        self._observer = observer if observer is not None else DecisionObserver()
        self._inbox = inbox
        #: ``inbox=False`` 是**显式关闭**（不是「未提供」）。只有未提供（None）才允许
        #: 延迟解析进程级默认收件箱——两种语义不能用一个 None 混表。
        self._inbox_enabled = inbox is not False

        self._latency: Deque[Tuple[bool, float]] = deque(maxlen=max(64, int(latency_window)))
        self._latency_lock = threading.Lock()
        self._decisions = 0
        self._errors = 0

    # ── 属性 ──

    @property
    def store(self) -> PolicyStore:
        return self._store

    @property
    def cache_size(self) -> int:
        return self._cache_size

    @property
    def decision_log(self) -> Optional[DecisionLog]:
        return self._log

    @property
    def stats(self) -> Dict[str, Any]:
        with self._cache_lock:
            cache = {"size": len(self._cache), "capacity": self._cache_size,
                     "hits": self._cache_hits, "misses": self._cache_misses}
        # 策略库读操作可能失败（库损坏/被替换）；stats 是**诊断入口**，它自己
        # 抛异常会让"引擎已经异常"的现场失去唯一可用的观测手段。
        try:
            store_revision: Any = self._store.revision
            store_fingerprint: Any = self._store.fingerprint()
            active_policies: Any = len(self._store.active())
        except Exception as exc:  # noqa: BLE001
            store_revision = store_fingerprint = None
            active_policies = None
            logger.debug("策略库统计不可用: %s", exc)
        return {
            "decisions": self._decisions,
            "errors": self._errors,
            "cache": cache,
            "store_revision": store_revision,
            "store_fingerprint": store_fingerprint,
            "active_policies": active_policies,
            "grants": len(self._grants),
            "observer": self._observer.stats if self._observer else None,
            "decision_log": self._log.stats if self._log else None,
            "latency": self.latency_percentiles(),
        }

    # ════════════════════════════════════════════════════════
    #  主入口：check
    # ════════════════════════════════════════════════════════

    def check(self, policy_ctx: Any, *, use_cache: bool = True,
              trace_id: str = "", workspace_id: str = "") -> PolicyDecision:
        """对一次操作做出决策（**决策即止，不做任何动作**）

        Args:
            policy_ctx: :class:`PolicyContext`、其 ``input`` dict、或 §3.2 descriptor
                （``from_descriptor`` 自动裁剪）。
            use_cache: 是否允许走决策缓存（默认允许；``False`` 用于「必须重算」的
                审计/复现场景）。

        Returns:
            :class:`PolicyDecision`。**调用方必须同时读 ``matched``**：
            ``matched=False`` ⇒ 策略未覆盖 ⇒ 回落到既有判定。
        """
        started = time.perf_counter()
        self._decisions += 1
        try:
            ctx = self._coerce_context(policy_ctx)
            decision = self._decide(ctx, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001 引擎异常绝不外泄成拒绝
            self._errors += 1
            logger.warning("策略决策异常（按未覆盖处理）: %s", exc)
            ctx = policy_ctx if isinstance(policy_ctx, PolicyContext) \
                else PolicyContext.from_input({})
            decision = PolicyDecision(
                effect=EFFECT_ALLOW, matched=False, reason_code=REASON_ENGINE_ERROR,
                message="策略引擎异常，已回落到既有判定",
                tenant_id=ctx.tenant_id, capability_id=ctx.capability_id,
                action=ctx.action, actor=ctx.actor)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        final = replace(decision, latency_ms=elapsed_ms)
        self._remember_latency(final.cache_hit, elapsed_ms)
        self._observe(final, ctx, trace_id=trace_id, workspace_id=workspace_id)
        self._journal(final, ctx)
        self._route_exception(final, ctx)
        return final

    def check_many(self, contexts: Iterable[Any], *, use_cache: bool = True
                   ) -> List[PolicyDecision]:
        """批量决策（模拟器/回归用；逐条调用 ``check``，保留相同语义）"""
        return [self.check(ctx, use_cache=use_cache) for ctx in contexts]

    # ── 决策核心 ──

    def _decide(self, ctx: PolicyContext, *, use_cache: bool) -> PolicyDecision:
        cache_key = self._cache_key(ctx)
        if use_cache and cache_key and self._cache_size:
            self._reclaim_if_store_changed()
            hit = self._cache_get(cache_key)
            if hit is not None:
                return replace(hit, cache_hit=True)

        decision = self._evaluate(ctx)
        if use_cache and cache_key and self._cache_size:
            self._cache_put(cache_key, replace(decision, cache_hit=False))
        return decision

    def _evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        payload = ctx.input if isinstance(ctx.input, dict) else {}

        for policy in self._store.active():
            if not policy.is_active(tenant_id=ctx.tenant_id,
                                    capability_id=ctx.capability_id):
                continue  # effective_range 不覆盖 ⇒ 视为不存在（不是 deny）
            evaluator = MatchEvaluator()
            matched = evaluator.evaluate(policy.match, payload)
            if not matched:
                continue
            result = evaluator.result(True)
            return self._build(policy, ctx, result)

        # 未命中任何策略：**不是拒绝**，而是「策略层无覆盖」⇒ 执行点回落既有判定
        return self._new_decision(ctx, effect=EFFECT_ALLOW, matched=False,
                                  reason_code=REASON_NO_MATCH, message="")

    @staticmethod
    def _new_decision(ctx: PolicyContext, **fields: Any) -> PolicyDecision:
        """按上下文补齐定位叶子后构造决策

        用显式形参而不是 ``**kwargs`` 展开：``PolicyDecision`` 是 frozen dataclass，
        展开一个 ``Dict[str, Any]`` 会让类型检查器无法逐字段核对（实测 mypy
        会对其余字段报 arg-type）。少一处便利，换类型层面的可核对性。
        """
        return PolicyDecision(
            effect=fields.pop("effect", EFFECT_ALLOW),
            policy_id=fields.pop("policy_id", ""),
            matched=fields.pop("matched", False),
            policy_version=fields.pop("policy_version", ""),
            reason_code=fields.pop("reason_code", ""),
            message=fields.pop("message", ""),
            break_glass=fields.pop("break_glass", False),
            break_glass_grant=fields.pop("break_glass_grant", ""),
            cache_hit=False,
            latency_ms=0.0,
            tenant_id=ctx.tenant_id,
            capability_id=ctx.capability_id,
            action=ctx.action,
            actor=ctx.actor,
            cache_key="",
        )

    def _build(self, policy: Policy, ctx: PolicyContext,
               result: Any) -> PolicyDecision:
        values = ctx.template_values()
        values.update({"policy_id": policy.id, "policy_version": policy.version,
                       "policy_owner": policy.owner, "effect": policy.effect.value,
                       "tenant_id": ctx.tenant_id,
                       "capability_id": ctx.capability_id, "actor": ctx.actor,
                       "action": ctx.action})
        message = render_message(policy.message_template, values)

        effect = policy.effect.value
        reason = {"allow": REASON_POLICY_ALLOW, "deny": REASON_POLICY_DENY,
                  "ask": REASON_POLICY_ASK}[effect]
        grant: Optional[BreakGlassGrant] = None

        if effect == EFFECT_DENY and policy.is_break_glassable:
            grant = self._active_grant(policy.id, ctx)
            if grant is not None:
                effect = EFFECT_ALLOW
                reason = REASON_BREAK_GLASS

        decision = self._new_decision(
            ctx, effect=effect, policy_id=policy.id, matched=True,
            policy_version=policy.version, reason_code=reason, message=message,
            break_glass=grant is not None,
            break_glass_grant=grant.grant_id if grant else "")
        if result.type_errors:
            logger.debug("策略 %s 求值含不可比叶子: %s", policy.id, result.type_errors)
        if result.missing_fields:
            logger.debug("策略 %s 求值含缺失字段: %s", policy.id,
                         result.missing_fields)
        return decision

    @staticmethod
    def _coerce_context(policy_ctx: Any) -> PolicyContext:
        if isinstance(policy_ctx, PolicyContext):
            return policy_ctx
        if isinstance(policy_ctx, dict):
            # 已是 input 骨架（含 capability/tenant/... 头）则直接当 input
            if any(k in policy_ctx for k in ("capability", "tenant", "actor",
                                            "action", "target", "attributes")):
                return PolicyContext.from_input(policy_ctx)
            return PolicyContext.from_input({"attributes": policy_ctx})
        if policy_ctx is None:
            return PolicyContext.from_input({})
        # §3.2 descriptor（有 capability_id 属性）⇒ 裁剪
        if hasattr(policy_ctx, "capability_id") or hasattr(policy_ctx, "trust"):
            return PolicyContext.from_descriptor(policy_ctx)
        raise TypeError(f"无法识别的 policy_ctx 类型: {type(policy_ctx).__name__}")

    # ── 缓存 ──

    def _cache_key(self, ctx: PolicyContext) -> str:
        material = ctx.cache_material()
        if not material:
            return ""  # 不可序列化 ⇒ 不缓存（宁可慢，不可错）
        header = f"v1|{self._store.revision}|{self._grant_epoch}"
        return sha256_hex(header + "|" + material)

    def _cache_get(self, key: str) -> Optional[PolicyDecision]:
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is None:
                self._cache_misses += 1
                return None
            self._cache.move_to_end(key)
            self._cache_hits += 1
            return hit

    def _cache_put(self, key: str, decision: PolicyDecision) -> None:
        with self._cache_lock:
            self._cache[key] = decision
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            try:
                self._cache_revision = self._store.revision
            except Exception:  # noqa: BLE001 库不可读 ⇒ 记 None，下次仍会尝试回收
                self._cache_revision = None

    def invalidate(self) -> None:
        """清空决策缓存并推进 epoch（策略变更/例外变更后调用）"""
        with self._cache_lock:
            self._cache.clear()
            self._epoch += 1
    def _reclaim_if_store_changed(self) -> None:
        """策略库 revision 变了就清空缓存条目（**内存回收**，不是正确性依赖）

        正确性由缓存键里的 ``revision`` 保证（旧键自然不再命中）；这里做的是让
        改策略后旧条目不要在 LRU 里白占容量——长驻进程里反复改策略会把缓存挤满
        永不命中的死键。开销是一次整数比较。
        """
        try:
            current = self._store.revision
        except Exception:  # noqa: BLE001 库不可读 ⇒ 跳过回收（正确性不受影响）
            return
        if current == self._cache_revision:
            return
        with self._cache_lock:
            if self._cache_revision != current:
                self._cache.clear()
                self._cache_revision = current

    # ── 耗时证据 ──

    def _remember_latency(self, cache_hit: bool, ms: float) -> None:
        with self._latency_lock:
            self._latency.append((bool(cache_hit), float(ms)))

    def latency_percentiles(self, *, cache_hit: Optional[bool] = None
                            ) -> Dict[str, Any]:
        """当前进程内决策耗时统计（毫秒）

        Args:
            cache_hit: ``True`` 只看缓存命中路径，``False`` 只看未命中路径，
                ``None`` 全部。**缓存命中路径的 p99 才是「决策缓存 p99<5ms」的
                口径**——把首次求值（含策略遍历）混进去会掩盖缓存是否真的起作用。
        """
        with self._latency_lock:
            samples = [ms for hit, ms in self._latency
                       if cache_hit is None or hit == bool(cache_hit)]
        if not samples:
            return {"count": 0, "p50": None, "p95": None, "p99": None,
                    "max": None, "mean": None, "cache_hit": cache_hit}
        ordered = sorted(samples)

        def _pct(p: float) -> float:
            if len(ordered) == 1:
                return ordered[0]
            index = min(len(ordered) - 1,
                        max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
            return ordered[index]

        return {
            "count": len(ordered),
            "p50": round(_pct(50), 4),
            "p95": round(_pct(95), 4),
            "p99": round(_pct(99), 4),
            "max": round(ordered[-1], 4),
            "mean": round(sum(ordered) / len(ordered), 4),
            "cache_hit": cache_hit,
        }

    def reset_latency(self) -> None:
        with self._latency_lock:
            self._latency.clear()

    # ── 埋点 / 日志 / 收件箱 ──

    def _observe(self, decision: PolicyDecision, ctx: PolicyContext, *,
                 trace_id: str, workspace_id: str) -> None:
        if self._observer is None:
            return
        try:
            self._observer.observe(decision, ctx, trace_id=trace_id,
                                   workspace_id=workspace_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("策略埋点失败（不影响决策）: %s", exc)

    def _journal(self, decision: PolicyDecision, ctx: PolicyContext) -> None:
        if self._log is None:
            return
        try:
            self._log.append(ctx, decision, fingerprint=self._store.fingerprint(),
                             revision=self._store.revision)
        except Exception as exc:  # noqa: BLE001
            logger.warning("决策日志写入失败（不影响决策）: %s", exc)

    def _route_exception(self, decision: PolicyDecision, ctx: PolicyContext) -> None:
        """「收件箱只收例外」：**只有** ask 与 break-glass 进人工收件箱

        §5.6 的反面同样重要：被策略明确覆盖的 allow/deny **不进收件箱**——否则
        「收件箱只收例外」会退化成「把策略已经判过的事再推给人」，那正是审批
        疲劳（R5）的成因。
        """
        if not (decision.effect == EFFECT_ASK or decision.break_glass):
            return
        if not self._inbox_enabled:
            return
        inbox = self._inbox
        if inbox is None:
            try:
                from agent.policy.inbox import get_policy_inbox
                inbox = get_policy_inbox()
                self._inbox = inbox
            except Exception:  # noqa: BLE001 收件箱不可用不阻断决策
                return
        if inbox is None:
            return
        try:
            inbox.submit(decision, ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("例外入收件箱失败（不影响决策）: %s", exc)

    # ════════════════════════════════════════════════════════
    #  break-glass
    # ════════════════════════════════════════════════════════

    def grant_break_glass(
        self,
        policy_id: str,
        *,
        actor: str,
        reason: str,
        ttl_min: Optional[int] = None,
        capability_id: str = "",
        tenant_id: str = "",
    ) -> BreakGlassGrant:
        """授予一条 break-glass 例外（**必须由人显式调用**）

        Raises:
            BreakGlassError: 策略不存在 / 该策略 ``break_glass_ttl_min`` 为 null
                （绝对 deny 不可例外）/ actor 或 reason 为空 / ttl 超出策略上限。
        """
        policy = self._store.get(policy_id)
        if policy is None:
            raise BreakGlassError(f"策略不存在: {policy_id}")
        if not policy.is_break_glassable:
            raise BreakGlassError(
                f"{policy.key()} 的 break_glass_ttl_min 为 null —— 绝对 deny 不可例外")
        if not str(actor or "").strip():
            raise BreakGlassError("break-glass 必须记录授予人（actor 非空）")
        if not str(reason or "").strip():
            raise BreakGlassError("break-glass 必须记录理由（reason 非空）")

        limit = int(policy.break_glass_ttl_min or 0)
        ttl = limit if ttl_min is None else int(ttl_min)
        if ttl <= 0:
            raise BreakGlassError(f"ttl_min 必须为正整数，got {ttl}")
        if ttl > limit:
            raise BreakGlassError(
                f"ttl_min={ttl} 超出策略上限 {limit}（{policy.key()}）")

        granted_at = now_iso()
        from datetime import datetime, timedelta
        expires = (datetime.now().astimezone()
                   + timedelta(minutes=ttl)).isoformat(timespec="milliseconds")
        grant = BreakGlassGrant(
            grant_id="bg_" + uuid.uuid4().hex[:16], policy_id=policy.id,
            actor=str(actor), reason=str(reason), granted_at=granted_at,
            expires_at=expires, ttl_min=ttl,
            capability_id=str(capability_id or ""), tenant_id=str(tenant_id or ""))
        with self._grant_lock:
            self._grants[grant.grant_id] = grant
            self._grant_epoch += 1
        self.invalidate()
        self._audit_break_glass(grant, action="policy.break_glass.grant")
        logger.warning("break-glass 已授予: %s policy=%s actor=%s ttl=%smin",
                       grant.grant_id, grant.policy_id, grant.actor, grant.ttl_min)
        return grant

    def revoke_break_glass(self, grant_id: str) -> bool:
        """撤销一条例外（返回是否确实撤销了）"""
        with self._grant_lock:
            grant = self._grants.pop(str(grant_id), None)
            if grant is None:
                return False
            self._grant_epoch += 1
        self.invalidate()
        self._audit_break_glass(grant, action="policy.break_glass.revoke")
        return True

    def active_grants(self, *, prune: bool = True) -> List[BreakGlassGrant]:
        """当前有效的例外（默认顺手清理过期项）"""
        with self._grant_lock:
            if prune:
                expired = [gid for gid, g in self._grants.items() if g.is_expired()]
                for gid in expired:
                    self._grants.pop(gid, None)
                if expired:
                    self._grant_epoch += 1
            return list(self._grants.values())

    def _active_grant(self, policy_id: str, ctx: PolicyContext
                      ) -> Optional[BreakGlassGrant]:
        with self._grant_lock:
            for grant in self._grants.values():
                if grant.covers(policy_id, capability_id=ctx.capability_id,
                                tenant_id=ctx.tenant_id):
                    return grant
        return None

    @staticmethod
    def _audit_break_glass(grant: BreakGlassGrant, *, action: str) -> None:
        try:
            from agent.audit.facade import audit
            audit.record(action, actor=grant.actor,
                         subject=f"policy:{grant.policy_id}",
                         payload={"grant_id": grant.grant_id, "ttl_min": grant.ttl_min,
                                  "capability_id": grant.capability_id,
                                  "tenant_id": grant.tenant_id,
                                  "reason_recorded": bool(grant.reason)},
                         source="ui")
        except Exception as exc:  # noqa: BLE001
            logger.warning("break-glass 审计写入失败: %s", exc)

    # ════════════════════════════════════════════════════════
    #  模拟（P7.2-19）——刻意**不**在本类提供便捷方法
    # ════════════════════════════════════════════════════════
    #
    #  原因不是 API 偏好，而是架构护栏：
    #
    #  ``simulator`` 必须在模块级 ``import engine``（它要用 ``PolicyEngine`` 造
    #  候选引擎），因此 engine 只要反过来引用 simulator——**哪怕是函数体内的延迟
    #  导入**——``agent.observability.arch_rules`` 的 ``no_circular_dependency``
    #  就会判为违规并阻断 CI（该规则统计函数级导入；repo 内既有的
    #  ``agent.error_handler → agent.monitoring.*`` 违规正是这个形态，靠
    #  ``docs/architecture/legacy_exemptions.json`` 豁免才通过）。
    #
    #  这里选择**消除环**而不是申请豁免：模拟器是决策引擎的**消费者**，依赖方向
    #  天然是 ``simulator → engine``；为一行语法糖引入一个需要长期豁免的环不划算。
    #  调用方直接写::
    #
    #      from agent.policy.simulator import simulate
    #      report = simulate(candidate, engine=engine, since_days=7)


# ════════════════════════════════════════════════════════════
#  进程级默认引擎
# ════════════════════════════════════════════════════════════

_DEFAULT_ENGINE: Optional[PolicyEngine] = None
_DEFAULT_ENGINE_LOCK = threading.Lock()


def get_policy_engine(reload: bool = False) -> PolicyEngine:
    """进程级默认引擎（懒加载）；``reload=True`` 丢弃重建"""
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None or reload:
        with _DEFAULT_ENGINE_LOCK:
            if _DEFAULT_ENGINE is None or reload:
                _DEFAULT_ENGINE = PolicyEngine()
    return _DEFAULT_ENGINE


def reset_policy_engine() -> None:
    """丢弃进程级默认引擎（测试隔离用）"""
    global _DEFAULT_ENGINE
    with _DEFAULT_ENGINE_LOCK:
        if _DEFAULT_ENGINE is not None:
            try:
                if _DEFAULT_ENGINE.decision_log is not None:
                    _DEFAULT_ENGINE.decision_log.close()
            except Exception:  # noqa: BLE001
                pass
        _DEFAULT_ENGINE = None


__all__ = [
    "ENV_CACHE_SIZE", "ENV_OBSERVE", "DEFAULT_CACHE_SIZE", "DEFAULT_LATENCY_WINDOW",
    "REASON_NO_MATCH", "REASON_POLICY_ALLOW", "REASON_POLICY_DENY",
    "REASON_POLICY_ASK", "REASON_BREAK_GLASS", "REASON_ENGINE_ERROR",
    "BreakGlassGrant", "BreakGlassError", "DecisionObserver", "PolicyEngine",
    "get_policy_engine", "reset_policy_engine",
]
