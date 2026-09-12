"""治理可观测面板 HTTP 面（TASK-S6-01）

【任务定位】
    §7「六面板 + 七动作」的 HTTP 落点。**全部在云枢工作台内扩展**（UI 五坑④：
    不建独立 Web App），本模块只提供 JSON；页面由 `yunshu-ui` 的
    `src/pages/hub/governance/*` 渲染。

【只读为主，写动作一律走既有审批（U4：不得旁路）】
    写路径只有两个入口，且都不新增权限实现：

    | 动作 | 落点 | 强制审批 |
    |---|---|---|
    | 批量裁决（审批收件箱） | 内部逐条复用 `routes_approval._do_decision` 的完整安全链 | §7.0 矩阵 + 会话/CSRF/链接/二次认证 |
    | 七动作（熔断/回滚/降级/摘除/审批/溯源 Diff/熔炉开关） | `POST /api/cp/actions/<action>` | 先 `guard_mod.authorize(...)`，再按动作走既有设施 |

    **回滚（U4）**：只接受 ``bundle_hash``，直接调
    `release_bundle.rollback_bundle(bundle_hash, applier=...)`；
    ``components`` 为真子集时由该函数自身抛 `PartialRollbackError`（L4 事故卡），
    本模块**不做也不允许做**子集回滚。L5 级 `requires_approval=True` 是
    `levels.LEVEL_SPECS` 的事实，本模块把它**原样回给前端**（前端据此强制审批 UI）。

【U1：前端常量单一来源】
    `GET /api/cp/security/render-state` 直接返回
    `safe_render.safe_render_state()` / `boundary_words.boundary_state()` /
    `injection_defense.defense_status()`——前端**不得**自定义五类边界词、60s 上限、
    TaintBadge class 名或审批区 z-index。

【身份】
    一律经 `agent/server_auth.resolve_request_identity()`；请求体里的任何
    actor / actor_type 字段**一律忽略**（前端不拥有额外权限，S4-01 裁定）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from flask import jsonify, make_response, request

from agent.security import approval_guard as guard_mod
from agent.security.actor_matrix import (
    OP_DENY,
    OP_REMOVE_SOURCE,
    OP_SWITCH_FORGE,
    OP_VIEW_PANEL,
)
from agent.server_auth import require_token, resolve_request_identity

logger = logging.getLogger(__name__)

PREFIX = "/api/cp"

#: 七动作 UI → 矩阵操作（**单一映射表**；前端不得自造操作名）
ACTION_OPERATIONS: Dict[str, str] = {
    "circuit_break": "capability.execute",       # 熔断（限流/断流）
    "rollback": OP_REMOVE_SOURCE,                # 回滚（整包；高风险 destructive 路径）
    "degrade": "capability.execute",             # 降级（native→borrowed 走回上游）
    "remove_source": OP_REMOVE_SOURCE,           # 摘除来源（reason 必填）
    "approve": "approval.approve",               # 审批
    "reject": OP_DENY,                           # 审批（驳回）
    "trace_diff": OP_VIEW_PANEL,                 # 溯源 Diff（只读）
    "switch_forge": OP_SWITCH_FORGE,             # 熔炉开关（二次认证）
}

#: 需要 reason 的动作（与 §7.0 矩阵 requires_reason 同源，缺时由矩阵兜底拒绝）
ACTIONS_REQUIRING_REASON = ("remove_source", "rollback")

#: 永不自动化五类动作（§7）——这些动作**必须**带 §5.7 机制 5 的单次确认凭据。
#: 名称与 `boundary_words.BOUNDARY_LABELS` 的类别键一一对应（下划线→连字符；
#: 连字符形式同时满足 Flask 的 ``<action>`` URL 规则，后者不接受下划线）。
NEVER_AUTOMATED_ACTIONS = ("publish", "transfer", "drop-database",
                           "permission-change", "force-push")

#: 五类动作的矩阵操作（**只走审批提案，不从 UI 直接执行**）
#: 理由：这五类"不可逆且超 workspace"，即便拿了 UI 单次确认，也不得由前端直接
#: 落地——故一律经 `OP_SUBMIT_APPROVAL` 送进审批收件箱，由 human 审批后另行执行。
#: 本端点因此**没有**这五类的执行分支（"没有那个能力"而非约定）。
NEVER_AUTOMATED_OPERATION = "approval.submit"


def _error(code: str, message: str, status: int = 403,
           **extra: Any) -> Tuple[Any, int]:
    body: Dict[str, Any] = {"ok": False, "code": code, "message": message}
    body.update({k: v for k, v in extra.items() if v is not None})
    return jsonify(body), status


def _public_decision(decision: Any) -> Dict[str, Any]:
    """矩阵判定对外投影（与 `routes_approval._public_decision` 同形，不扩大暴露）"""
    if decision is None:
        return {}
    return {
        "allowed": bool(getattr(decision, "allowed", False)),
        "reason": str(getattr(decision, "reason", "") or ""),
        "operation": str(getattr(decision, "operation", "") or ""),
        "actor_type": str(getattr(decision, "actor_type", "") or ""),
        "matrix_hit": bool(getattr(decision, "matrix_hit", False)),
        "denied_by_matrix": bool(getattr(decision, "denied_by_matrix", False)),
        "requires_second_factor": bool(
            getattr(decision, "requires_second_factor", False)),
        "requires_reason": bool(getattr(decision, "requires_reason", False)),
    }


def _actor_ctx() -> Any:
    """由**已认证身份**构造执行体上下文（请求体声明一律不采信）"""
    identity = resolve_request_identity(
        session_id=str(request.cookies.get("cp_approval_session", "") or ""))
    return guard_mod.ActorContext(
        actor=identity.actor, actor_type=identity.actor_type,
        identity_source=identity.identity_source, scope=identity.scope,
        session_id=identity.session_id,
        actor_ip=str(request.remote_addr or ""),
        degraded=bool(identity.degraded))


def _authorize(*, operation: str, object_type: str, object_id: str = "",
               reason: str = "", risk: str = "",
               second_factor_ok: bool = False) -> Tuple[Any, Optional[Any]]:
    """统一鉴权（返回 `(decision, error_response)`；error 非空即应直接 return）"""
    ctx = _actor_ctx()
    decision = guard_mod.authorize(
        operation=operation, actor_ctx=ctx, object_type=object_type,
        object_id=object_id, reason=reason, risk=risk,
        second_factor_ok=second_factor_ok, report=True)
    if not decision.allowed:
        return decision, _error("panel_denied", decision.reason, 403,
                                decision=_public_decision(decision))
    return decision, None


def _int_arg(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


# ════════════════════════════════════════════════════════════
#  只读面板
# ════════════════════════════════════════════════════════════


def register_routes(app: Any, state: Any = None) -> None:  # noqa: ARG001
    """把面板路由注册到 Flask app（与既有 `server_routes/*` 同款模式）"""

    from agent.ui_panels import data as D

    # ── 面板元信息（优先级 / 数据源台账；验收与前端"来源"入口共用）──

    @app.route(f"{PREFIX}/panels", methods=["GET"])
    @require_token
    def cp_panels_index():
        from agent.ui_panels.schema import PANEL_PRIORITY
        names = ["digestion_pipeline", "approval_inbox", "capability_map",
                 "roi", "incident", "memory_skills"]
        _dec, err = _authorize(operation=OP_VIEW_PANEL, object_type="panel.index")
        if err:
            return err
        return jsonify({
            "ok": True,
            "prefix": PREFIX,
            "panels": [D.panel_map(n) for n in names],
            "priority_order": PANEL_PRIORITY,
            "endpoints": {
                "digestion_pipeline": f"GET {PREFIX}/digestion/pipeline",
                "capability_map": f"GET {PREFIX}/descriptors/map",
                "approval_inbox": f"GET {PREFIX}/approvals/inbox",
                "approval_batch": f"POST {PREFIX}/approvals/batch",
                "roi": f"GET {PREFIX}/roi",
                "observability": f"GET {PREFIX}/observability/stream",
                "incident": f"GET {PREFIX}/healing/incidents",
                "memory_skills": f"GET {PREFIX}/memory/skills",
                "authz_alerts": f"GET {PREFIX}/security/authz-alerts",
                "render_state": f"GET {PREFIX}/security/render-state",
                "audit_export": f"GET {PREFIX}/audit/export",
                "audit_export_csv": f"GET {PREFIX}/audit/export.csv",
                "action": f"POST {PREFIX}/actions/<action>",
                "confirm_issue": f"POST {PREFIX}/confirmations/<action>",
            },
        })

    # ── 1. 消化流水线（P0） ──

    @app.route(f"{PREFIX}/digestion/pipeline", methods=["GET"])
    @require_token
    def cp_digestion_pipeline():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.digestion")
        if err:
            return err
        return jsonify(D.pipeline_view(
            days=_int_arg("days", 7, low=1, high=90),
            limit=_int_arg("limit", 50, low=1, high=500),
            events_dir=request.args.get("events_dir") or None,
            shadow_dir=request.args.get("shadow_dir") or None,
            promote_dir=request.args.get("promote_dir") or None))

    # ── 2. 能力地图（P1） ──

    @app.route(f"{PREFIX}/descriptors/map", methods=["GET"])
    @require_token
    def cp_descriptors_map():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.capability_map")
        if err:
            return err
        return jsonify(D.capability_map(
            stage=request.args.get("stage", "") or "",
            provenance=request.args.get("provenance", "") or "",
            risk=request.args.get("risk", "") or "",
            data_class=request.args.get("data_class", "") or "",
            query=request.args.get("q", "") or "",
            limit=_int_arg("limit", 200, low=1, high=500),
            offset=_int_arg("offset", 0, low=0, high=1_000_000)))

    # ── 3. 审批收件箱（P0） ──

    @app.route(f"{PREFIX}/approvals/inbox", methods=["GET"])
    @require_token
    def cp_approvals_inbox():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.approvals")
        if err:
            return err
        return jsonify(D.approval_inbox(
            limit=_int_arg("limit", 50, low=1, high=200),
            object_type=request.args.get("object_type", "") or ""))

    # ── 4. ROI / 成本（P1） ──

    @app.route(f"{PREFIX}/roi", methods=["GET"])
    @require_token
    def cp_roi():
        _dec, err = _authorize(operation=OP_VIEW_PANEL, object_type="panel.roi")
        if err:
            return err
        return jsonify(D.roi_view(
            days=_int_arg("days", 7, low=1, high=90),
            events_dir=request.args.get("events_dir") or None,
            shadow_dir=request.args.get("shadow_dir", "") or ""))

    # ── 5. 事件流消费（ACR/UTC/降级/逃逸；S2-03 遗留 #12） ──

    @app.route(f"{PREFIX}/observability/stream", methods=["GET"])
    @require_token
    def cp_observability_stream():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.observability")
        if err:
            return err
        return jsonify(D.observability_stream(
            days=_int_arg("days", 7, low=1, high=90),
            limit=_int_arg("limit", 200, low=1, high=500),
            events_dir=request.args.get("events_dir") or None))

    # ── 6. 自愈事故（P1） ──

    @app.route(f"{PREFIX}/healing/incidents", methods=["GET"])
    @require_token
    def cp_healing_incidents():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.incident")
        if err:
            return err
        return jsonify(D.incidents_view(
            incidents_dir=request.args.get("incidents_dir") or None,
            events_dir=request.args.get("events_dir") or None,
            limit=_int_arg("limit", 100, low=1, high=500),
            days=_int_arg("days", 7, low=1, high=90)))

    # ── 7. 记忆 / 技能库（P2；U5 优先级接线） ──

    @app.route(f"{PREFIX}/memory/skills", methods=["GET"])
    @require_token
    def cp_memory_skills():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.memory_skills")
        if err:
            return err
        layers = [x for x in (request.args.get("layers", "") or "").split(",") if x]
        return jsonify(D.memory_skills_view(
            layers=layers or None,
            tenant_id=request.args.get("tenant_id", "") or "",
            query=request.args.get("q", "") or "",
            limit=_int_arg("limit", 50, low=1, high=200)))

    # ── 8. 越权告警聚合（U3） ──

    @app.route(f"{PREFIX}/security/authz-alerts", methods=["GET"])
    @require_token
    def cp_authz_alerts():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.authz_alerts")
        if err:
            return err
        return jsonify(D.authz_alerts(
            limit=_int_arg("limit", 50, low=1, high=500),
            days=_int_arg("days", 7, low=1, high=90),
            events_dir=request.args.get("events_dir") or None))

    # ── 9. 安全渲染 / 边界词常量（U1：单一来源） ──

    @app.route(f"{PREFIX}/security/render-state", methods=["GET"])
    @require_token
    def cp_render_state():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.render_state")
        if err:
            return err
        return jsonify(D.security_render_state())

    # ── 10. 审计导出（含验签摘要） ──

    @app.route(f"{PREFIX}/audit/export", methods=["GET"])
    @require_token
    def cp_audit_export():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.audit_export")
        if err:
            return err
        # 导出属高危读取：要求 trace/memory 同级的查看权（同一 OP，但显式标注）
        scope = str(request.args.get("verify_scope", "exported") or "exported")
        if scope not in ("exported", "full", "head"):
            scope = "exported"
        payload = D.audit_export(
            limit=_int_arg("limit", 200, low=1, high=2000),
            start_seq=_opt_int("start_seq"),
            end_seq=_opt_int("end_seq"),
            action=request.args.get("action", "") or "",
            actor=request.args.get("actor", "") or "",
            day=request.args.get("day", "") or "",
            verify_scope=scope,
            verify=str(request.args.get("verify", "1")) not in ("0", "false", "no"))
        response = make_response(jsonify(payload))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route(f"{PREFIX}/audit/export.csv", methods=["GET"])
    @require_token
    def cp_audit_export_csv():
        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.audit_export")
        if err:
            return err
        payload = D.audit_export(
            limit=_int_arg("limit", 2000, low=1, high=2000),
            start_seq=_opt_int("start_seq"),
            end_seq=_opt_int("end_seq"),
            action=request.args.get("action", "") or "",
            actor=request.args.get("actor", "") or "",
            day=request.args.get("day", "") or "",
            verify=True)
        body = D.audit_export_csv(payload)
        # BOM 让 Excel 正确识别 UTF-8（中文列值不被乱码）
        response = make_response("\ufeff" + body)
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = (
            'attachment; filename="cp_audit_export.csv"')
        response.headers["Cache-Control"] = "no-store"
        return response

    # ── 11. 边界确认凭据签发（§5.7 机制 5：单次 action + 60s） ──

    @app.route(f"{PREFIX}/confirmations/<action>", methods=["POST"])
    @require_token
    def cp_issue_confirmation(action: str):
        """签发「永不自动化五类」操作的单次确认凭据

        ★ 本端点**只签发凭据**，不执行任何操作；执行在
        `POST /api/cp/actions/<action>`，且执行时必须回传该 token。
        ★ TTL 由 `boundary_words` 的 **60s 硬上限**决定，本模块**不接受**
          ttl 参数（前端不得自定义——U1）。
        """
        from agent.guardrails import boundary_words as bw

        body = request.get_json(silent=True) or {}
        target = str(body.get("target", "") or "")
        payload_hint = body.get("payload") if isinstance(body.get("payload"), dict) else None
        action_key = _action_digest_input(action, target, payload_hint)
        hits = bw.detect_boundary_words(str(body.get("action_text", "") or action_key))
        category = str(hits[0].category) if hits else ""
        store = bw.get_confirmation_store()
        confirmation = store.issue_to_ui(
            action_key, category=category, extra=payload_hint,
            note=f"UI 单次确认：{action}")
        public = dict(confirmation.to_public())
        # token 原文**只在此刻、只回给调用方一次**（供执行时回传）；
        # 之后的一切读取（boundary_state / 审计 / 拦截事件）都只有 to_public 形态。
        public["token"] = confirmation.token
        return jsonify({
            "ok": True,
            "action": action,
            "confirmation": public,
            "action_digest": confirmation.action_digest,
            "hits": [h.to_dict() for h in hits],
            "max_ttl_seconds": bw.MAX_CONFIRMATION_TTL_SECONDS,
            "single_action_bound": True,
            "accepts_text_approval": False,
            "single_use": True,
            "note": ("凭据绑定本次 action 摘要，单次有效、60s 内失效（token 只在此刻返回一次）；"
                     "文本形式的「已批准」一律不采信（§5.7 机制 5）"),
        })

    # ── 12. 七动作写入口（只读为主；写动作走既有审批，不得旁路） ──

    @app.route(f"{PREFIX}/actions/<action>", methods=["POST"])
    @require_token
    def cp_action(action: str):
        return _do_action(action)

    # ── 13. 审批收件箱批量裁决（签发 + 提交，两段式） ──
    register_batch(app)


def _opt_int(name: str) -> Optional[int]:
    raw = request.args.get(name)
    if raw is None or str(raw) == "":
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _action_digest_input(action: str, target: str,
                         payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """构造「动作摘要」的可判定内容（供 `boundary_words.action_digest` 绑定）"""
    return {"action": str(action or ""), "target": str(target or ""),
            "payload": dict(payload or {})}


# ════════════════════════════════════════════════════════════
#  写动作实现（**全部先鉴权，后执行**）
# ════════════════════════════════════════════════════════════


def _do_action(action: str) -> Any:
    """七动作统一入口

    执行顺序（**顺序即安全边界**）：
        1. 「永不自动化五类」→ **最先**过 §5.7 机制 5 闸门（凭据 = 唯一批准形式）：
           这类动作无论是否登记在矩阵表里，都必须先拿 UI 单次确认（60s）；
        2. 动作名必须登记在 `ACTION_OPERATIONS`（未登记 fail-closed）；
        3. 矩阵鉴权（human/auto/sub_agent 各自格 + reason/二次认证前置条件）；
        4. 执行：回滚走 `rollback_bundle`（整包，L5 强制审批）；其余为受控落点。
    """
    key = str(action or "").strip().lower()
    body = request.get_json(silent=True) or {}
    target = str(body.get("target", "") or "")
    reason = str(body.get("reason", "") or "")
    token = str(body.get("confirmation_token", "") or "")
    second_factor = str(body.get("second_factor", "") or "")
    payload_hint = body.get("payload") if isinstance(body.get("payload"), dict) else None
    action_key = _action_digest_input(key, target, payload_hint)

    # ── 1. 边界词闸门（永不自动化五类；UI 显式确认 + 单次 + 60s）──
    boundary: Dict[str, Any] = {}
    if key in NEVER_AUTOMATED_ACTIONS:
        from agent.guardrails.boundary_words import guard_execution
        verdict = guard_execution(action_key,
                                  action_text=str(body.get("action_text") or ""),
                                  token=token or None, enforce=False,
                                  actor=str(request.remote_addr or "ui"))
        boundary = verdict.to_dict()
        if not verdict.allowed:
            return _error("boundary_confirmation_required", verdict.reason, 428,
                          boundary=boundary)

    # ── 2. 动作登记（fail-closed）──
    if key in NEVER_AUTOMATED_ACTIONS:
        op = NEVER_AUTOMATED_OPERATION
    elif key in ACTION_OPERATIONS:
        op = ACTION_OPERATIONS[key]
    else:
        return _error("unknown_action",
                      f"未登记的动作（fail-closed）：{action}", 400,
                      known=sorted(ACTION_OPERATIONS) + list(NEVER_AUTOMATED_ACTIONS))

    # ── 3. 矩阵鉴权（§7.0 单表）──
    risk = str(body.get("risk", "") or "")
    decision, err = _authorize(
        operation=op, object_type=f"action.{key}", object_id=target,
        reason=reason, risk=risk,
        second_factor_ok=bool(second_factor))
    if err:
        return err

    # ── 4. 执行 ──
    if key == "rollback":
        return _do_rollback(target, body, decision=decision, boundary=boundary)
    if key == "trace_diff":
        return _do_trace_diff(target)
    if key in NEVER_AUTOMATED_ACTIONS:
        # 五类**没有执行分支**：拿确认凭据也不落地，只送审批提案（§7）
        return jsonify({
            "ok": True,
            "action": key,
            "target": target,
            "confirmed": True,
            "executed": False,
            "submitted_for_approval": True,
            "decision": _public_decision(decision),
            "boundary": boundary or None,
            "note": ("永不自动化五类（§7）：UI 单次确认仅表示「本人确认要发起」；"
                     "本端点**不执行**该操作——一律转审批提案，由 human 审批后另行执行。"
                     "这是「没有那个能力」而非约定"),
        })
    return _do_governance_action(key, target, reason=reason, body=body,
                                 decision=decision, boundary=boundary)


def _do_rollback(bundle_hash: str, body: Mapping[str, Any], *,
                 decision: Any, boundary: Mapping[str, Any]) -> Any:
    """整包回滚（**U4**）：只接受 bundle_hash；L5 强制审批；子集由下游拒绝

    ★ 本函数**不做**以下三件事（这是纪律，不是遗漏）：
        - 不接受 ``components`` 参数（整包是唯一原子单位，P7.2-15）；
        - 不提供 ``applier``（本模块没有执行能力；真正落地由部署侧注入）；
        - 不绕过 `rollback_bundle` 的原子性闸门与自校验。
    """
    from agent.self_healing import release_bundle as RB
    from agent.self_healing.levels import HealLevel, spec_for

    l5 = spec_for(HealLevel.L5)
    l5_requires_approval = bool(getattr(l5, "requires_approval", True))
    if not bundle_hash:
        return _error("missing_bundle_hash",
                      "整包回滚必须提供 bundle_hash（组件子集回滚不被支持）", 400,
                      level="L5", requires_approval=l5_requires_approval)
    if body.get("components"):
        # 显式拒绝并留痕：让下游的原子性闸门不被前端"顺手"绕过
        return _error("partial_rollback_rejected",
                      "整包回滚是唯一原子单位（P7.2-15）：不接受 components 子集", 400,
                      level="L5", requires_approval=l5_requires_approval)

    incident_id = str(body.get("approval_record_id", "") or "")
    dry_run = bool(body.get("dry_run", True))
    try:
        plan = RB.rollback_bundle(
            bundle_hash, components=None, dry_run=True,
            tenant_id=str(body.get("tenant_id", "default") or "default"),
            context={"via": "ui_panels", "actor": decision.actor,
                     "approval_record_id": incident_id})
    except Exception as e:  # noqa: BLE001 原子性/自校验失败 → 如实回传（含 L4）
        logger.warning("[UIPanels] 整包回滚被拒 bundle=%s: %s", bundle_hash, e)
        return _error("rollback_rejected", f"{type(e).__name__}: {e}", 409,
                      level="L5", requires_approval=l5_requires_approval,
                      partial=getattr(e, "partial", None))
    return jsonify({
        "ok": True,
        "action": "rollback",
        "level": "L5",
        "requires_approval": l5_requires_approval,   # L5 恒定 True（levels.LEVEL_SPECS）
        "automated": False,
        "dry_run": True,
        "requested_dry_run": dry_run,
        "plan": plan.to_dict(),
        "decision": _public_decision(decision),
        "boundary": boundary or None,
        "note": ("整包回滚（唯一原子单位）：本端点只出计划，落地由部署侧注入 applier；"
                 "L5 requires_approval=True 恒定，前端必须显式审批"),
    })


def _do_trace_diff(capability_id: str) -> Any:
    """溯源 Diff（只读）：能力当前 stage + 最近 stage 事件 + 治理字段

    UI 七动作里的"溯源 Diff"是**只读**动作：它不推进 stage，只把
    "目标 stage 与当前 stage 的差异 + 依据"摊开给人看（P7.2-24 可解释性）。
    """
    if not capability_id:
        return _error("missing_target", "溯源 Diff 需要 target=capability_id", 400)
    registry = None
    stage = ""
    governance: Dict[str, Any] = {}
    try:
        from agent.descriptors.registry import DescriptorRegistry
        registry = DescriptorRegistry(autosave=False)
        row = next((r for r in registry.list_with_trust()
                    if r.get("capability_id") == capability_id), None)
        stage = str((row or {}).get("stage") or "")
        from agent.security.governance_bridge import governance_trace_fields
        # object_type="capability" 才能命中 descriptor（见 governance_bridge 的
        # _DESCRIPTOR_OBJECT_TYPES），否则退回 payload 叶子字段 → unresolved
        governance = governance_trace_fields("capability", capability_id)
        governance["descriptor"] = {
            "risk_level": (row or {}).get("risk_level"),
            "data_class": (row or {}).get("data_class"),
            "requires_approval": (row or {}).get("requires_approval"),
            "provenance": (row or {}).get("provenance"),
            "audit_level": (row or {}).get("audit_level"),
            "sample_count": (row or {}).get("sample_count"),
        }
    except Exception as e:  # noqa: BLE001
        return _error("trace_diff_unavailable", f"{type(e).__name__}: {e}", 503)

    events = []
    try:
        from agent.observability.events import EV_DIGEST_STAGE, iter_events
        for env in iter_events(types=(EV_DIGEST_STAGE,), limit=200):
            p = env.payload or {}
            if str(p.get("capability_id") or "") != capability_id:
                continue
            events.append({"ts": env.ts, "from_stage": p.get("from_stage"),
                           "to_stage": p.get("to_stage"), "verdict": p.get("verdict"),
                           "applied": bool(p.get("applied")),
                           "scope": p.get("scope") or "",
                           "reasons": list(p.get("reasons") or [])[:8]})
    except Exception as e:  # noqa: BLE001
        logger.debug("[UIPanels] 溯源 Diff 事件读取失败: %s", e)
    events.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return jsonify({
        "ok": True,
        "action": "trace_diff",
        "capability_id": capability_id,
        "current_stage": stage or None,
        "governance": governance,
        "stage_events": events[:50],
        "read_only": True,
        "note": "溯源 Diff 为只读动作：不推进 stage，只呈现差异与依据",
    })


def _do_governance_action(key: str, target: str, *, reason: str,
                          body: Mapping[str, Any], decision: Any,
                          boundary: Mapping[str, Any]) -> Any:
    """熔断 / 降级 / 摘除 / 审批 / 熔炉开关 的**受控落点**

    设计取舍（如实声明）：
        熔断与降级在本任务里**不新建执行器**——既有执行点分别是
        `agent/circuit_breaker.py`（熔断）与
        `agent/observability/model_degrade.py`（降级，native→borrowed 走回上游）。
        本端点做的是"**鉴权 + 意图落账 + 返回既有执行入口**"，把真正的副作用
        留给既有组件（守不易：不改既有公开接口与行为）。摘除来源与熔炉开关同理
        （`policy` / `descriptor` 域的既有路径）。
    """
    intent = {
        "action": key, "target": target, "reason": reason,
        "operator": decision.actor, "operator_type": decision.actor_type,
        "requested_at": None,
        "payload": dict(body.get("payload") or {}),
    }
    executors: Dict[str, str] = {
        "circuit_break": "agent/circuit_breaker.py（既有熔断执行点）",
        "degrade": "agent/observability/model_degrade.py::report_model_degraded（既有降级发射）",
        "remove_source": "agent/descriptors/registry.py + agent/policy（来源摘除既有路径）",
        "approve": "agent/server_routes/routes_approval.py（审批唯一入口）",
        "reject": "agent/server_routes/routes_approval.py（审批唯一入口）",
        "switch_forge": "agent/policy（熔炉开关既有路径；需二次认证）",
    }
    # 意图入审计（**不含原文**，只放标识与理由摘要）
    audit_ref: Dict[str, Any] = {}
    try:
        from agent.audit.facade import audit
        entry = audit.record(
            f"ui.action.{key}",
            actor=decision.actor or "ui",
            subject=f"action:{key}:{target or '-'}",
            payload={"reason": str(reason or "")[:200],
                     "operation": decision.operation,
                     "requires_second_factor": bool(
                         getattr(decision, "requires_second_factor", False))},
            source="ui", status="requested",
            technical={"via": "agent.server_routes.routes_ui_panels"})
        if entry is not None:
            audit_ref = {"seq": int(getattr(entry, "seq", 0) or 0),
                         "self_hash": str(getattr(entry, "self_hash", "") or "")}
    except Exception as e:  # noqa: BLE001 审计失败不阻断（best-effort，与既有同纪律）
        logger.debug("[UIPanels] 动作审计写入失败: %s", e)

    if key in ("approve", "reject"):
        return _error("use_approval_endpoint",
                      "审批请走 POST /api/approval/<record_id>/approve|reject"
                      "（会话绑定 + CSRF + 链接 + 二次认证的唯一链路）", 400,
                      decision=_public_decision(decision))

    return jsonify({
        "ok": True,
        "action": key,
        "target": target,
        "authorized": True,
        "decision": _public_decision(decision),
        "boundary": boundary or None,
        "intent": intent,
        "audit": audit_ref,
        "executor": executors.get(key, ""),
        "second_factor_required": bool(
            getattr(decision, "requires_second_factor", False)),
        "note": ("写动作一律先经 §7.0 矩阵鉴权；本端点返回既有执行入口与已落账意图，"
                 "不新增执行器（不改既有公开接口与行为）"),
    })


# ════════════════════════════════════════════════════════════
#  审批收件箱：批量裁决（§7「同策略同风险一键批」）
# ════════════════════════════════════════════════════════════

#: 批量签发的链接集合（**仅编排层**：每个 token 本身仍是底层一次性链接）
#: 结构：``batch_id -> {"session_id": str, "tokens": {record_id: token},
#:                       "actor": str, "expires_at": float, "batch_key": str}``
#: 说明：这里**不保存任何审批判定**，只保存"这次批量用哪些一次性链接"；
#: 真正的授权判定始终由 `routes_approval._do_decision_with` 现场执行。
_BATCHES: Dict[str, Dict[str, Any]] = {}
_BATCH_TTL_SECONDS = 900.0          # 与 §5.7⑦ 链接时效硬上限同源（≤900s）
_BATCH_MAX = 200


def _purge_batches(now: Optional[float] = None) -> None:
    import time as _t
    moment = _t.time() if now is None else float(now)
    for bid in [k for k, v in _BATCHES.items() if v.get("expires_at", 0) <= moment]:
        _BATCHES.pop(bid, None)
    while len(_BATCHES) > _BATCH_MAX:      # 溢出按签发时间淘汰最旧
        oldest = min(_BATCHES, key=lambda k: _BATCHES[k].get("expires_at", 0))
        _BATCHES.pop(oldest, None)


def reset_batches() -> None:
    """清空批量链接集合（测试隔离用）"""
    _BATCHES.clear()


def register_batch(app: Any) -> None:
    """批量裁决端点（由 `register_routes` 调用，保持单一注册入口）

    两个端点，纪律与单条审批**完全一致**（会话绑定 + ≤900s 时效 + 一次性）：

    | 端点 | 作用 |
    |---|---|
    | ``POST /api/cp/approvals/batch/link`` | 为所选记录签发**逐条绑定的**一次性审批链接；返回 ``batch_id``，并把 token 集合放入 HttpOnly Cookie（不落到 JS 可读处） |
    | ``POST /api/cp/approvals/batch`` | 提交批量裁决；**逐条**复用单条审批链路（会话→CSRF→链接→二次认证→矩阵） |

    为什么两条：单条链接与 ``record_id`` **绑定**且**一次性**，故批量必须在
    服务端一次性签发 N 条绑定链接、再逐条核销。**不新增权限实现**——
    每条仍走 `_do_decision_with`（与单条端点同一函数）。
    """

    @app.route(f"{PREFIX}/approvals/batch/link", methods=["POST"])
    @require_token
    def cp_approvals_batch_link():
        import secrets
        import time as _t

        from agent.security import approval_session as session_mod
        from agent.server_routes import routes_approval as AR

        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.approvals")
        if err:
            return err
        body = request.get_json(silent=True) or {}
        ids = body.get("record_ids")
        if not isinstance(ids, list) or not ids:
            return _error("missing_record_ids", "record_ids 必须是非空数组", 400)
        ids = [str(x) for x in ids][:100]

        session_id = str(request.cookies.get(session_mod.SESSION_COOKIE_NAME, "") or "")
        store = session_mod.get_session_store()
        session = store.get_session(session_id)
        if session is None:
            return _error(session_mod.CHECK_SESSION_UNKNOWN,
                          "请先开启审批会话（POST /api/approval/session）", 401)
        csrf = store.verify_csrf(session_id, str(
            request.headers.get(session_mod.CSRF_HEADER_NAME, "") or ""))
        if not csrf.ok:
            return _error(csrf.code, csrf.message, 403)

        flow = AR.get_approval_flow()
        from agent.security import approval_guard as g
        tokens: Dict[str, str] = {}
        groups: Dict[str, int] = {}
        for rid in ids:
            rec = flow.get(rid)
            if rec is None:
                return _error("unknown_record", f"审批记录不存在: {rid}", 404)
            risk = g.resolve_risk(rec.object_type, rec.object_id, rec.payload)
            bkey = f"{rec.object_type}|{rec.level}|{risk or 'unknown'}"
            groups[bkey] = groups.get(bkey, 0) + 1
            link = store.issue_link(session_id=session_id, record_id=rid,
                                    actor=session.actor, risk=risk)
            tokens[rid] = link.token
        if len(groups) > 1:
            # 已签发的链接随会话 TTL 自然过期，不额外核销（不消费即未使用）
            return _error("mixed_batch",
                          "同策略同风险才可一键批（§7）：所选记录分属 "
                          f"{sorted(groups)}", 400,
                          groups=groups)

        _purge_batches()
        batch_id = "batch-" + secrets.token_hex(8)
        _BATCHES[batch_id] = {
            "session_id": session_id, "tokens": tokens, "actor": session.actor,
            "batch_key": next(iter(groups)),
            "expires_at": _t.time() + min(_BATCH_TTL_SECONDS,
                                          float(session_mod.link_ttl_seconds())),
        }
        response = make_response(jsonify({
            "ok": True,
            "batch_id": batch_id,
            "record_ids": ids,
            "batch_key": next(iter(groups)),
            "ttl_seconds": min(_BATCH_TTL_SECONDS,
                               float(session_mod.link_ttl_seconds())),
            "one_time_per_record": True,
            "note": ("逐条绑定的一次性链接已签发；token 集合只放在 HttpOnly Cookie 中"
                     "（后端单表校验，前端不拥有额外权限）"),
        }))
        # token 集合入 HttpOnly Cookie（与单条路径同款；不暴露给 JS）
        response.set_cookie(f"cp_batch_{batch_id}", json.dumps(tokens),
                            httponly=True, samesite="Strict",
                            max_age=int(min(_BATCH_TTL_SECONDS,
                                            float(session_mod.link_ttl_seconds()))))
        return response

    @app.route(f"{PREFIX}/approvals/batch", methods=["POST"])
    @require_token
    def cp_approvals_batch():
        """批量裁决（**逐条复用单条审批的完整安全链，不新增旁路**）

        请求体::

            {"batch_id": "batch-...",
             "decision": "approve" | "reject",
             "reason": "驳回必填 / 批准备注",
             "second_factor": {"<record_id>": "<code>"}}

        服务端保证：
            1. 记录必须**同一 batch_key**（object_type|level|risk）——签发时已校验，
               提交时再校验一次（防签发后状态漂移）；
            2. 每条都走 `routes_approval._do_decision_with`（会话/CSRF/链接/
               二次认证/矩阵），**任一条被拒不影响其余已处理结果**，逐条回传；
            3. 不产生新的审批语义记录——每条仍是它自己的审批记录。
        """
        from agent.security import approval_session as session_mod
        from agent.server_routes import routes_approval as AR

        _dec, err = _authorize(operation=OP_VIEW_PANEL,
                               object_type="panel.approvals")
        if err:
            return err
        body = request.get_json(silent=True) or {}
        decision = str(body.get("decision", "") or "").strip().lower()
        if decision not in ("approve", "reject"):
            return _error("bad_decision", "decision 必须是 approve 或 reject", 400)
        batch_id = str(body.get("batch_id", "") or "")
        _purge_batches()
        entry = _BATCHES.get(batch_id)
        if entry is None:
            return _error("unknown_batch",
                          "批量链接不存在或已过期（请重新签发）", 401)
        session_id = str(request.cookies.get(session_mod.SESSION_COOKIE_NAME, "") or "")
        if session_id != entry.get("session_id"):
            return _error(session_mod.CHECK_SESSION_MISMATCH,
                          "批量链接与当前审批会话不匹配（禁止分享式链接）", 403)
        tokens: Dict[str, str] = dict(entry.get("tokens") or {})
        reason = str(body.get("reason", "") or "")
        second = body.get("second_factor") or {}
        if not isinstance(second, dict):
            second = {}
        if decision == "reject" and not reason.strip():
            return _error("reason_required", "驳回必须提供 reason（审计要求）", 400)

        flow = AR.get_approval_flow()
        from agent.security import approval_guard as g
        for rid in tokens:
            rec = flow.get(rid)
            if rec is None:
                return _error("unknown_record", f"审批记录不存在: {rid}", 404)
            risk = g.resolve_risk(rec.object_type, rec.object_id, rec.payload)
            bkey = f"{rec.object_type}|{rec.level}|{risk or 'unknown'}"
            if bkey != entry.get("batch_key"):
                return _error("mixed_batch",
                              f"记录 {rid} 的策略/风险已变化（{bkey}），"
                              "批量裁决已拒绝（请重新签发）", 409)

        results: List[Dict[str, Any]] = []
        for rid, token in tokens.items():
            try:
                payload, status, _redeemed = AR._do_decision_with(
                    rid, approve=(decision == "approve"), note=reason,
                    second_factor=str(second.get(rid, "") or ""),
                    link_token=token, redeem=True)
            except Exception as e:  # noqa: BLE001 单条异常不影响其余
                results.append({"record_id": rid, "ok": False,
                                "code": type(e).__name__,
                                "message": str(e)[:200]})
                continue
            payload = dict(payload or {})
            payload["record_id"] = rid
            payload["http_status"] = status
            results.append(payload)

        succeeded = sum(1 for r in results if r.get("ok"))
        _BATCHES.pop(batch_id, None)
        response = make_response(jsonify({
            "ok": True,
            "decision": decision,
            "batch_id": batch_id,
            "requested": len(tokens),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "batch_key": entry.get("batch_key"),
            "results": results,
            "note": ("批量裁决=逐条走同一审批链（会话+CSRF+链接+二次认证+矩阵）；"
                     "无新增权限实现，无旁路"),
        }))
        response.delete_cookie(f"cp_batch_{batch_id}")
        return response


__all__ = [
    "register_routes", "register_batch", "reset_batches", "PREFIX",
    "ACTION_OPERATIONS", "NEVER_AUTOMATED_ACTIONS", "ACTIONS_REQUIRING_REASON",
]
