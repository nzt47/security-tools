"""开关变更服务——风险分流 + 二次认证 + 双人确认 + 审计入链（TASK-S7-01 步骤 3）

【一次变更的完整链路（顺序即安全边界）】
    1. `get_spec(key)`：未登记 → fail-closed（`unknown_key`）；
    2. `resolve(key)`：算出真实来源与**能不能改**——被 env 锁定 / C 级 / 仅 config.yaml
       → 直接拒（`locked_by_env` / `read_only_secret` / `not_editable`），
       并在响应里回**可读原因**（禁止出现"UI 改了但没生效"）；
    3. §7.0 矩阵：`settings.change` 是 **human 专属**（auto / sub_agent 一律拒，
       越权尝试由 `approval_guard.authorize(report=True)` 自动告警 + 审计）；
    4. 值校验（按 `SettingSpec.validator`）：非法值 400，且**不改任何状态**；
    5. 风险分流：
       - **A 级**：直接落覆盖层 + 落运行态（热生效）；
       - **B 级**：必须有**二次认证**，且必须有**第二位人工确认**（双人确认）。
         第一位提交只产生 pending（202），**不写覆盖层、不改运行态**；
         第二位（不同 actor + 独立二次认证）确认后才落地；
       - **C 级**：已在上一步 403，响应体不含明文。
    6. 留痕：`audit.record("settings.change")` + `policy.decision` + 事件 `settings.change`
       （**不重复留痕**：这两个动作名都不在 `AUDIT_MIRROR_TYPES` 里，不存在镜像双写）；
    7. 回执：`effect` 标注生效方式（`hot` / `needs_restart` / `next_task`）。

【批量提交不被绕过】
    本服务只接受单 key 语义；路由把数组/`keys` 请求体直接 400（`batch_not_supported`）。
    B 级的双人确认因此无法被"一次提交多个键"绕过——**不存在**可批量的入口。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.security.actor_matrix import (
    ACTOR_HUMAN,
    OP_SETTINGS_CHANGE,
    PermissionContext,
    decide,
)
from agent.settings import masking
from agent.settings.overrides import OverrideStore, get_override_store
from agent.settings.registry import (
    RISK_A,
    RISK_B,
    RISK_C,
    SettingSpec,
    get_spec,
    EFFECT_NEXT_TASK,
    EFFECT_RESTART,
)
from agent.settings.resolver import (
    ResolvedSetting,
    apply_override_to_runtime,
    resolve,
    restore_runtime,
    SOURCE_OVERRIDE,
)

logger = logging.getLogger(__name__)

#: 审计动作名（单一来源；**不在 `AUDIT_MIRROR_TYPES` 内**，故不会被镜像双写）
AUDIT_ACTION_CHANGE = "settings.change"
AUDIT_ACTION_RESET = "settings.reset"
AUDIT_ACTION_POLICY = "policy.decision"

#: 事件类型
EVENT_SETTINGS_CHANGE = "settings.change"

#: 双人确认待办时效（秒）与上限
PENDING_TTL_SECONDS = 900.0
PENDING_MAX = 64

#: 错误码（前端只按 code 分流，不猜文案）
CODE_UNKNOWN_KEY = "unknown_key"
CODE_LOCKED_BY_ENV = "locked_by_env"
CODE_READ_ONLY_SECRET = "read_only_secret"
CODE_NOT_EDITABLE = "not_editable"
CODE_INVALID_VALUE = "invalid_value"
CODE_SETTINGS_DENIED = "settings_denied"
CODE_SECOND_FACTOR_REQUIRED = "second_factor_required"
CODE_SECOND_FACTOR_INVALID = "second_factor_invalid"
CODE_DUAL_APPROVAL_REQUIRED = "dual_approval_required"
CODE_SAME_ACTOR = "second_approver_must_differ"
CODE_UNKNOWN_PENDING = "unknown_pending"
CODE_BATCH_NOT_SUPPORTED = "batch_not_supported"


@dataclass
class ChangeOutcome:
    """一次变更尝试的结果（路由据此投影为 HTTP 响应）"""

    ok: bool
    code: str = ""
    message: str = ""
    status: int = 200
    applied: bool = False
    pending: bool = False
    pending_id: str = ""
    key: str = ""
    old: Any = None
    new: Any = None
    source: str = ""
    effect: str = ""
    effect_label: str = ""
    decision: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)
    receipt: Dict[str, Any] = field(default_factory=dict)
    resolved: Optional[ResolvedSetting] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"ok": self.ok, "applied": self.applied,
                               "key": self.key}
        if not self.ok:
            out["code"] = self.code
            out["message"] = self.message
            if self.decision:
                out["decision"] = self.decision
            return out
        if self.pending:
            out.update({
                "pending": True,
                "pending_id": self.pending_id,
                "requires_dual_approval": True,
                "message": self.message,
            })
            if self.resolved is not None:
                out["item"] = self.resolved.to_public_dict()
            return out
        out.update({
            "pending": False,
            "old": self.old,
            "new": self.new,
            "source": self.source,
            "effect": self.effect,
            "effect_label": self.effect_label,
            "audit": self.audit,
            "receipt": self.receipt,
            "decision": self.decision,
        })
        if self.resolved is not None:
            out["item"] = self.resolved.to_public_dict()
        return out


# ════════════════════════════════════════════════════════════
#  值校验
# ════════════════════════════════════════════════════════════

class ValueRejected(Exception):
    """值校验失败（携带可读原因与校验器元数据）"""

    def __init__(self, message: str, *, validator: Dict[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.validator = validator


def coerce_value(spec: SettingSpec, raw: Any) -> Any:
    """按声明类型归一化并校验入参

    Raises:
        ValueRejected: 类型/范围/枚举不合法（**此时不得改动任何状态**）。
    """
    v = spec.validator
    if spec.risk == RISK_C:
        raise ValueRejected(
            "C 级（只读脱敏）开关不接受 UI 写入", validator=v.to_dict())
    if v.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)) and raw in (0, 1):
            return bool(raw)
        if isinstance(raw, str):
            text = raw.strip().lower()
            if text in ("true", "1", "yes", "on"):
                return True
            if text in ("false", "0", "no", "off"):
                return False
        raise ValueRejected(
            f"{spec.key} 需要布尔值（true/false）", validator=v.to_dict())
    if v.kind in ("int", "float"):
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise ValueRejected(
                f"{spec.key} 需要数值", validator=v.to_dict()) from None
        if v.kind == "int" and number != int(number):
            raise ValueRejected(
                f"{spec.key} 需要整数", validator=v.to_dict())
        value: Any = int(number) if v.kind == "int" else float(number)
        if v.min is not None and value < v.min:
            raise ValueRejected(
                f"{spec.key} 不得小于 {v.min}", validator=v.to_dict())
        if v.max is not None and value > v.max:
            raise ValueRejected(
                f"{spec.key} 不得大于 {v.max}", validator=v.to_dict())
        return value
    if v.kind == "enum":
        text = str(raw)
        if text not in v.choices:
            raise ValueRejected(
                f"{spec.key} 只接受 {'/'.join(v.choices)}", validator=v.to_dict())
        return text
    if v.kind == "path":
        raise ValueRejected(
            f"{spec.key} 是路径项（UI 只读）", validator=v.to_dict())
    if raw is None:
        raise ValueRejected(f"{spec.key} 不接受空值", validator=v.to_dict())
    return str(raw)


# ════════════════════════════════════════════════════════════
#  双人确认（进程内待办；与既有 `_BATCHES` 同款口径）
# ════════════════════════════════════════════════════════════

@dataclass
class PendingChange:
    """B 级待第二位人工确认的变更"""

    pending_id: str
    key: str
    value: Any
    digest: str
    actor: str
    actor_type: str
    session_id: str
    reason: str
    created_at: float
    expires_at: float
    risk: str = RISK_B

    def to_public(self) -> Dict[str, Any]:
        return {
            "pending_id": self.pending_id, "key": self.key,
            "risk": self.risk, "requested_by": self.actor,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(self.expires_at)),
            "requires_dual_approval": True,
            "ttl_seconds": int(self.expires_at - self.created_at),
        }


_PENDING: Dict[str, PendingChange] = {}
_PENDING_LOCK = threading.RLock()


def _purge_pending(now: Optional[float] = None) -> None:
    moment = float(time.time() if now is None else now)
    with _PENDING_LOCK:
        for pid in [k for k, v in _PENDING.items() if v.expires_at <= moment]:
            _PENDING.pop(pid, None)
        while len(_PENDING) > PENDING_MAX:
            oldest = min(_PENDING, key=lambda k: _PENDING[k].created_at)
            _PENDING.pop(oldest, None)


def reset_pending() -> None:
    """清空待办（测试隔离用）"""
    with _PENDING_LOCK:
        _PENDING.clear()


def _digest(key: str, value: Any) -> str:
    """把「键 + 值」绑成摘要：确认时值不得被换掉（换值即摘要不匹配）"""
    material = f"{key}|{value!r}|{type(value).__name__}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


# ════════════════════════════════════════════════════════════
#  服务
# ════════════════════════════════════════════════════════════

class SettingsService:
    """开关变更服务（治理动作的唯一落点）"""

    def __init__(self, store: Optional[OverrideStore] = None) -> None:
        self._store = store or get_override_store()

    # ── 鉴权 ──

    @staticmethod
    def _actor_ctx(*, actor: str, actor_type: str, session_id: str,
                   identity_source: str = "") -> PermissionContext:
        return PermissionContext(actor=actor, actor_type=actor_type,
                                 session_id=session_id,
                                 identity_source=identity_source)

    def _authorize(self, *, spec: SettingSpec, actor: str, actor_type: str,
                   session_id: str, identity_source: str,
                   second_factor_ok: bool, reason: str
                   ) -> Tuple[Any, Optional[ChangeOutcome]]:
        """§7.0 矩阵鉴权（human 专属；越权必拒 + 告警 + 审计）"""
        ctx = self._actor_ctx(actor=actor, actor_type=actor_type,
                             session_id=session_id,
                             identity_source=identity_source)
        decision = decide(
            OP_SETTINGS_CHANGE, ctx, object_type="setting",
            object_id=spec.key, reason=reason,
            second_factor_ok=second_factor_ok, risk=spec.risk,
            # 二次认证的真值由本服务按风险级裁量（A 级不需要），
            # 故矩阵层只判"矩阵格"，不替服务层下结论
            enforce_preconditions=False)
        if not decision.allowed:
            return decision, ChangeOutcome(
                ok=False, code=CODE_SETTINGS_DENIED, message=decision.reason,
                status=403, key=spec.key, decision=decision.to_dict())
        return decision, None

    # ── 变更 ──

    def change(self, key: str, value: Any, *, actor: str,
               actor_type: str = ACTOR_HUMAN, reason: str = "",
               second_factor_ok: bool = False, session_id: str = "",
               identity_source: str = "", pending_id: str = ""
               ) -> ChangeOutcome:
        """改开关（A 直接生效 / B 二次认证 + 双人确认 / C 拒绝）"""
        spec = get_spec(key)
        if spec is None:
            return ChangeOutcome(
                ok=False, code=CODE_UNKNOWN_KEY,
                message=f"未登记的开关（fail-closed）：{key}", status=404,
                key=str(key))

        resolved = resolve(spec.key, store=self._store)
        assert resolved is not None                          # noqa: S101
        if not resolved.editable:
            code = (CODE_READ_ONLY_SECRET if spec.risk == RISK_C
                    else CODE_LOCKED_BY_ENV if resolved.env_locked
                    else CODE_NOT_EDITABLE)
            status = 403
            return ChangeOutcome(
                ok=False, code=code, message=resolved.locked_reason, status=status,
                key=spec.key, old=resolved.value, resolved=resolved)

        decision, denial = self._authorize(
            spec=spec, actor=actor, actor_type=actor_type, session_id=session_id,
            identity_source=identity_source, second_factor_ok=second_factor_ok,
            reason=reason)
        if denial is not None:
            self._audit_denial(spec=spec, actor=actor, decision=decision,
                               reason=reason, code=denial.code)
            return denial

        if pending_id:
            return self.confirm(spec.key, pending_id, actor=actor,
                                actor_type=actor_type,
                                second_factor_ok=second_factor_ok,
                                reason=reason, session_id=session_id,
                                identity_source=identity_source)

        try:
            clean_value = coerce_value(spec, value)
        except ValueRejected as e:
            return ChangeOutcome(ok=False, code=CODE_INVALID_VALUE,
                                 message=e.message, status=400, key=spec.key,
                                 decision={"validator": e.validator})

        if spec.risk == RISK_B:
            if not second_factor_ok:
                return ChangeOutcome(
                    ok=False, code=CODE_SECOND_FACTOR_REQUIRED,
                    message=(f"{spec.key} 是 B 级开关：需二次认证 + 第二位人工"
                             "确认后方可生效（本端口不产生任何变更）"),
                    status=403, key=spec.key, resolved=resolved,
                    decision=decision.to_dict())
            return self._open_pending(spec=spec, value=clean_value, actor=actor,
                                      actor_type=actor_type, reason=reason,
                                      session_id=session_id,
                                      identity_source=identity_source,
                                      decision=decision, resolved=resolved)

        return self._apply(spec=spec, value=clean_value, actor=actor,
                           actor_type=actor_type, reason=reason,
                           session_id=session_id, identity_source=identity_source,
                           decision=decision, resolved=resolved,
                           second_approver="")

    # ── B 级：开待办 ──

    def _open_pending(self, *, spec: SettingSpec, value: Any, actor: str,
                      actor_type: str, reason: str, session_id: str,
                      identity_source: str, decision: Any,
                      resolved: ResolvedSetting) -> ChangeOutcome:
        _purge_pending()
        pid = "setp-" + secrets.token_hex(8)
        now = time.time()
        with _PENDING_LOCK:
            _PENDING[pid] = PendingChange(
                pending_id=pid, key=spec.key, value=value,
                digest=_digest(spec.key, value), actor=actor,
                actor_type=actor_type, session_id=session_id, reason=reason,
                created_at=now, expires_at=now + PENDING_TTL_SECONDS)
        logger.info("[Settings] B 级变更待第二位人工确认 key=%s by=%s",
                    spec.key, actor)
        return ChangeOutcome(
            ok=True, pending=True, pending_id=pid, key=spec.key,
            message=(f"{spec.key} 是 B 级开关：已登记待确认（{int(PENDING_TTL_SECONDS)}s 内"
                     "有效），需**另一位人工**用独立二次认证确认后才生效；"
                     "本次提交未改动任何状态"),
            status=202, resolved=resolved, decision=decision.to_dict(),
            effect=spec.effect, effect_label=spec.effect_label)

    # ── B 级：第二位确认 ──

    def confirm(self, key: str, pending_id: str, *, actor: str,
                actor_type: str = ACTOR_HUMAN, reason: str = "",
                second_factor_ok: bool = False, session_id: str = "",
                identity_source: str = "") -> ChangeOutcome:
        """双人确认（第二位人工）：actor 必须与发起者不同"""
        _purge_pending()
        with _PENDING_LOCK:
            pending = _PENDING.get(str(pending_id))
        if pending is None:
            return ChangeOutcome(
                ok=False, code=CODE_UNKNOWN_PENDING,
                message="待确认记录不存在或已过期（请重新发起）", status=404,
                key=str(key))
        if pending.key != str(key):
            return ChangeOutcome(
                ok=False, code=CODE_UNKNOWN_PENDING,
                message=(f"待确认记录 {pending_id} 对应的开关是 {pending.key}，"
                         f"与请求的 {key} 不一致"), status=409, key=str(key))
        if str(actor) == pending.actor:
            return ChangeOutcome(
                ok=False, code=CODE_SAME_ACTOR,
                message=("双人确认要求第二位确认人与发起人不同"
                         f"（发起人={pending.actor}）"), status=403, key=str(key))
        if not second_factor_ok:
            return ChangeOutcome(
                ok=False, code=CODE_SECOND_FACTOR_REQUIRED,
                message="确认人必须提供自己的二次认证凭据", status=403,
                key=str(key))
        spec = get_spec(pending.key)
        if spec is None:                                     # pragma: no cover
            return ChangeOutcome(ok=False, code=CODE_UNKNOWN_KEY,
                                 message=f"未登记的开关：{pending.key}",
                                 status=404, key=str(key))
        resolved = resolve(spec.key, store=self._store)
        assert resolved is not None                          # noqa: S101
        ctx = self._actor_ctx(actor=actor, actor_type=actor_type,
                              session_id=session_id,
                              identity_source=identity_source)
        decision = decide(OP_SETTINGS_CHANGE, ctx, object_type="setting",
                          object_id=spec.key, reason=reason,
                          second_factor_ok=True, risk=spec.risk,
                          enforce_preconditions=False)
        if not decision.allowed:
            return ChangeOutcome(ok=False, code=CODE_SETTINGS_DENIED,
                                 message=decision.reason, status=403,
                                 key=spec.key, decision=decision.to_dict())
        with _PENDING_LOCK:
            _PENDING.pop(pending.pending_id, None)
        return self._apply(spec=spec, value=pending.value, actor=actor,
                           actor_type=actor_type, reason=reason,
                           session_id=session_id,
                           identity_source=identity_source, decision=decision,
                           resolved=resolved,
                           second_approver=pending.actor,
                           requested_by=pending.actor)

    # ── 落地（唯一真正改变状态的地方）──

    def _apply(self, *, spec: SettingSpec, value: Any, actor: str,
               actor_type: str, reason: str, session_id: str,
               identity_source: str, decision: Any, resolved: ResolvedSetting,
               second_approver: str, requested_by: str = "") -> ChangeOutcome:
        old_value = resolved.value
        old_source = resolved.source
        self._store.set(spec.key, value, actor=actor, risk=spec.risk,
                        reason=reason, previous=old_value)
        after = resolve(spec.key, store=self._store)
        assert after is not None                             # noqa: S101
        landing = apply_override_to_runtime(after, store=self._store)
        after = resolve(spec.key, store=self._store)
        assert after is not None                             # noqa: S101

        audit_ref = self._audit_change(
            spec=spec, old=old_value, new=value, source=after.source,
            old_source=old_source, actor=actor, actor_type=actor_type,
            reason=reason, landing=landing, decision=decision,
            second_approver=second_approver, session_id=session_id,
            identity_source=identity_source, action=AUDIT_ACTION_CHANGE)
        self._emit_change_event(spec=spec, old=old_value, new=value,
                                actor=actor, landing=landing,
                                second_approver=second_approver)

        effect = spec.effect
        receipt_note = landing.get("detail", "")
        if spec.risk == RISK_B and not second_approver:      # pragma: no cover
            receipt_note += "（注意：B 级未经双人确认，不应到达此处）"
        return ChangeOutcome(
            ok=True, applied=bool(landing.get("applied")), key=spec.key,
            old=old_value, new=value, source=after.source, effect=effect,
            effect_label=spec.effect_label, audit=audit_ref,
            decision=decision.to_dict(), resolved=after,
            receipt={
                "effect": effect,
                "effect_label": spec.effect_label,
                "landing": landing.get("target", ""),
                "detail": receipt_note,
                "overlay_path": str(self._store.path),
                "never_touched": [".env", "config.yaml"],
                "second_approver": second_approver,
                "requested_by": requested_by,
                "risk": spec.risk,
                "needs_restart": bool(spec.needs_restart),
                "apply_mode": spec.effect,
                "note": ("覆盖层已落盘；生效方式见 effect。"
                         ".env / config.yaml 未被修改（守不易）"),
            })

    # ── 重置 ──

    def reset(self, key: str, *, actor: str, actor_type: str = ACTOR_HUMAN,
              reason: str = "", session_id: str = "",
              identity_source: str = "") -> ChangeOutcome:
        """清除覆盖层（回落到 config/default）"""
        spec = get_spec(key)
        if spec is None:
            return ChangeOutcome(ok=False, code=CODE_UNKNOWN_KEY,
                                 message=f"未登记的开关（fail-closed）：{key}",
                                 status=404, key=str(key))
        decision, denial = self._authorize(
            spec=spec, actor=actor, actor_type=actor_type, session_id=session_id,
            identity_source=identity_source, second_factor_ok=False,
            reason=reason)
        if denial is not None:
            self._audit_denial(spec=spec, actor=actor, decision=decision,
                               reason=reason, code=denial.code)
            return denial
        before = resolve(spec.key, store=self._store)
        assert before is not None                            # noqa: S101
        if not self._store.has(spec.key):
            return ChangeOutcome(
                ok=True, applied=False, key=spec.key, old=before.value,
                new=before.value, source=before.source,
                effect=spec.effect, effect_label=spec.effect_label,
                message="该开关没有覆盖层记录，无需重置", resolved=before,
                receipt={"reset": False, "detail": "无覆盖层记录",
                         "overlay_path": str(self._store.path),
                         "never_touched": [".env", "config.yaml"]})
        old_value = before.value
        self._store.clear(spec.key)
        restore = restore_runtime(spec.key, store=self._store)
        after = resolve(spec.key, store=self._store)
        assert after is not None                             # noqa: S101
        audit_ref = self._audit_change(
            spec=spec, old=old_value, new=after.value, source=after.source,
            old_source=before.source, actor=actor, actor_type=actor_type,
            reason=reason, landing=restore, decision=decision,
            second_approver="", session_id=session_id,
            identity_source=identity_source, action=AUDIT_ACTION_RESET)
        return ChangeOutcome(
            ok=True, applied=True, key=spec.key, old=old_value, new=after.value,
            source=after.source, effect=spec.effect,
            effect_label=spec.effect_label, audit=audit_ref,
            decision=decision.to_dict(), resolved=after,
            receipt={"reset": True, "detail": restore.get("detail", ""),
                     "overlay_path": str(self._store.path),
                     "never_touched": [".env", "config.yaml"]})

    # ── 留痕 ──

    def _audit_change(self, *, spec: SettingSpec, old: Any, new: Any,
                      source: str, old_source: str, actor: str, actor_type: str,
                      reason: str, landing: Dict[str, Any], decision: Any,
                      second_approver: str, session_id: str,
                      identity_source: str, action: str) -> Dict[str, Any]:
        """写链式审计 + `policy.decision`（**不重复留痕**，见模块 docstring）"""
        payload = {
            "old": _audit_leaf(spec, old),
            "new": _audit_leaf(spec, new),
            "source": source,
            "previous_source": old_source,
            "risk": spec.risk,
            "category": spec.category,
            "effect": spec.effect,
            "applied": bool(landing.get("applied")),
            "landing": landing.get("target", ""),
            "second_approver": second_approver,
            "reason": str(reason or "")[:200],
            "never_touched": [".env", "config.yaml"],
        }
        ref: Dict[str, Any] = {}
        try:
            from agent.audit.facade import audit
            entry = audit.record(
                action, actor=actor or "settings-center",
                subject=f"setting:{spec.key}", payload=payload, source="ui",
                status=("applied" if landing.get("applied") else "recorded"),
                technical={"audit_ref": f"{action}.v1",
                           "integrity_source": identity_source,
                           "session": bool(session_id)})
            if entry is not None:
                ref = {"seq": int(getattr(entry, "seq", 0) or 0),
                       "self_hash": str(getattr(entry, "self_hash", "") or "")}
        except Exception as e:                               # noqa: BLE001
            logger.warning("[Settings] 审计写入失败（不阻断，如实回传）: %s", e)
        # policy.decision：与 `DecisionObserver` 同款（链上一次 + 事件一次）
        try:
            from agent.audit.facade import audit
            audit.record(
                AUDIT_ACTION_POLICY, actor=actor or "settings-center",
                subject=f"setting:{spec.key}",
                payload={
                    "operation": OP_SETTINGS_CHANGE,
                    "result": "allow",
                    "allowed": True,
                    "actor_type": actor_type,
                    "risk": spec.risk,
                    "requires_second_factor": spec.requires_second_factor,
                    "second_factor_ok": bool(spec.requires_second_factor),
                    "reason_code": action,
                },
                source="ui",
                technical={"audit_ref": "settings.policy_decision.v1"})
        except Exception as e:                               # noqa: BLE001
            logger.debug("[Settings] policy.decision 写入失败: %s", e)
        return ref

    def _audit_denial(self, *, spec: SettingSpec, actor: str, decision: Any,
                      reason: str, code: str) -> None:
        """越权/被拒动作留痕（矩阵拒绝时 `approval_guard` 已告警）"""
        try:
            from agent.audit.facade import audit
            audit.record(
                AUDIT_ACTION_CHANGE, actor=actor or "unknown",
                subject=f"setting:{spec.key}",
                payload={"denied": True, "code": code,
                         "reason": str(getattr(decision, "reason", "") or "")[:200],
                         "actor_type": str(getattr(decision, "actor_type", "") or ""),
                         "risk": spec.risk, "note": str(reason or "")[:200]},
                source="ui", status="denied",
                technical={"audit_ref": "settings.change.v1.denied"})
        except Exception as e:                               # noqa: BLE001
            logger.debug("[Settings] 拒绝留痕失败: %s", e)

    @staticmethod
    def _emit_change_event(*, spec: SettingSpec, old: Any, new: Any, actor: str,
                           landing: Dict[str, Any],
                           second_approver: str) -> None:
        """事件回执（best-effort；与 `DecisionObserver` 同款，未被镜像入链）"""
        try:
            from agent.observability.events import emit
            emit(EVENT_SETTINGS_CHANGE,
                 {"key": spec.key, "category": spec.category, "risk": spec.risk,
                  "old": _audit_leaf(spec, old), "new": _audit_leaf(spec, new),
                  "effect": spec.effect, "landing": landing.get("target", ""),
                  "second_approver": second_approver,
                  "never_touched": [".env", "config.yaml"]},
                 actor=actor or "ui")
        except Exception as e:                               # noqa: BLE001
            logger.debug("[Settings] 事件写入失败: %s", e)


def _audit_leaf(spec: SettingSpec, value: Any) -> Any:
    """审计叶子值：C 级只留指纹（明文绝不入链）"""
    if spec.risk == RISK_C:
        return masking.mask_for_log(value) if str(value or "") else ""
    return value


#: 单例
_GLOBAL_SERVICE: Optional[SettingsService] = None
_SERVICE_LOCK = threading.Lock()


def get_settings_service() -> SettingsService:
    global _GLOBAL_SERVICE
    with _SERVICE_LOCK:
        if _GLOBAL_SERVICE is None:
            _GLOBAL_SERVICE = SettingsService()
        return _GLOBAL_SERVICE


def reset_settings_service() -> None:
    global _GLOBAL_SERVICE
    with _SERVICE_LOCK:
        _GLOBAL_SERVICE = None
    reset_pending()


__all__ = [
    "AUDIT_ACTION_CHANGE", "AUDIT_ACTION_RESET", "AUDIT_ACTION_POLICY",
    "EVENT_SETTINGS_CHANGE", "PENDING_TTL_SECONDS",
    "CODE_UNKNOWN_KEY", "CODE_LOCKED_BY_ENV", "CODE_READ_ONLY_SECRET",
    "CODE_NOT_EDITABLE", "CODE_INVALID_VALUE", "CODE_SETTINGS_DENIED",
    "CODE_SECOND_FACTOR_REQUIRED", "CODE_SECOND_FACTOR_INVALID",
    "CODE_DUAL_APPROVAL_REQUIRED", "CODE_SAME_ACTOR", "CODE_UNKNOWN_PENDING",
    "CODE_BATCH_NOT_SUPPORTED",
    "ChangeOutcome", "ValueRejected", "coerce_value", "PendingChange",
    "SettingsService", "get_settings_service", "reset_settings_service",
    "reset_pending", "RISK_A", "RISK_B", "RISK_C", "EFFECT_RESTART",
    "EFFECT_NEXT_TASK", "SOURCE_OVERRIDE",
]
