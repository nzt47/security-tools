"""审批 HTTP 面（v7.2 §5.7⑦ + §7.0）— TASK-S4-01

【任务定位】
    S2-02 盘点 #3：云枢此前**没有审批的 HTTP 路由**（审批只经服务层网关），
    于是「审批面安全」无处落地。本模块补齐审批面，并把 §5.7⑦ 的六条要求钉在
    同一处：

    | 要求 | 落点 |
    |---|---|
    | 会话绑定（禁分享式链接） | `POST /api/approval/session` 开会话；审批链接 token 与 `session_id` 绑定，换会话即 `session_mismatch` |
    | CSRF 保护 | 双重提交：`cp_approval_csrf` Cookie + `X-CSRF-Token` 头（`approval_session.verify_csrf`） |
    | 链接时效 ≤15min | `approval_session.link_ttl_seconds()`（硬上限 900s），过期 → 提示重新发起 |
    | destructive 二次认证 | 风险 destructive ⇒ 必须先取一次性确认码（`POST /api/approval/second-factor`）再审批 |
    | 越权 → 告警 + 审计 | 矩阵判定在 `ApprovalFlow`/`approval_guard`，本模块只把身份与来源如实传入 |
    | 前端 DOM 隔离 | `GET /api/approval/console` 提供的审批控制台（`templates/approval_console.html`） |

【审计口径（**刻意不双写**，对齐 S2-02 #3 / #10）】
    同一「批准」动作会产生**三条语义不同**的记录，各司其职、不重复：
      1. `ui.routes_approval_approve.post`（全局 UI 写路由包装，S2-02）
         —— **访问/尝试**语义（含被拒请求）；
      2. `approval.approved`（`ApprovalFlow._audit`，S2-02）
         —— **状态变更**语义，审批域唯一产出；
      3. `policy.denied`（越权时，经 S2-03 事件镜像）
         —— **越权**语义。
    故本模块**不使用** `@audit_action`（那会再产生一条语义重复记录）。

【身份（裁定 A3）】
    actor 一律经 `agent/server_auth.resolve_request_identity()`（映射表 → 头 →
    降级）解析；**请求体里的任何 actor / actor_type 字段一律忽略**——
    前端不拥有额外权限。原始 IP 只用于派生掩码 + HMAC（裁定 B），不落盘。

【配置】
    CP_APPROVAL_REQUIRE_AUTHORITATIVE  默认 0：为 1 时降级身份不得审批
    CP_APPROVAL_LINK_TTL_SECONDS / CP_APPROVAL_SESSION_TTL_SECONDS / CP_APPROVAL_CSRF_ENABLED
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from flask import jsonify, make_response, request

from agent.security import approval_guard as guard_mod
from agent.security import approval_session as session_mod
from agent.security.actor_matrix import OP_VIEW_PANEL
from agent.server_auth import require_token, resolve_request_identity

logger = logging.getLogger(__name__)

_ENV_REQUIRE_AUTHORITATIVE = "CP_APPROVAL_REQUIRE_AUTHORITATIVE"

#: 审批链接 Cookie（可分享的只有 token 本身，且**必须**与会话 Cookie 同现）
LINK_COOKIE_NAME = "cp_approval_link"

_flow: Any = None
_handlers_installed = False


# ════════════════════════════════════════════════════════════
#  依赖注入（测试隔离 / 服务侧注入）
# ════════════════════════════════════════════════════════════

def get_approval_flow() -> Any:
    """审批流单例（懒加载；首次访问时接线 descriptor 风险/治理字段）"""
    global _flow, _handlers_installed
    if _flow is None:
        from agent.skills_mgmt.approval import ApprovalFlow
        _flow = ApprovalFlow()
        logger.info("[ApprovalRoutes] 审批流已初始化（records_path=%s）",
                    getattr(_flow, "_records_path", ""))
    if not _handlers_installed:
        _handlers_installed = True
        try:
            from agent.security.governance_bridge import install_descriptor_resolvers
            install_descriptor_resolvers()
        except Exception as e:  # noqa: BLE001 接线失败 → 风险按未知处理
            logger.debug("[ApprovalRoutes] descriptor 接线失败: %s", e)
    return _flow


def set_approval_flow(flow: Any) -> Any:
    """注入审批流（测试隔离）；返回旧实例"""
    global _flow
    previous = _flow
    _flow = flow
    return previous


def _require_authoritative() -> bool:
    return str(os.getenv(_ENV_REQUIRE_AUTHORITATIVE, "0") or "").strip().lower() in (
        "1", "true", "yes", "on")


# ════════════════════════════════════════════════════════════
#  公共小工具
# ════════════════════════════════════════════════════════════

def _error(code: str, message: str, status: int = 403,
           **extra: Any) -> Tuple[Any, int]:
    body: Dict[str, Any] = {"ok": False, "code": code, "message": message}
    body.update({k: v for k, v in extra.items() if v is not None})
    return jsonify(body), status


def _session_id() -> str:
    return str(request.cookies.get(session_mod.SESSION_COOKIE_NAME, "") or "")


def _csrf_header() -> str:
    return str(request.headers.get(session_mod.CSRF_HEADER_NAME, "") or "")


def _actor_ctx(session_id: str) -> guard_mod.ActorContext:
    """由**已认证身份**构造执行体上下文（请求体声明一律不采信）"""
    identity = resolve_request_identity(session_id=session_id)
    return guard_mod.ActorContext(
        actor=identity.actor, actor_type=identity.actor_type,
        identity_source=identity.identity_source, scope=identity.scope,
        session_id=session_id, actor_ip=request.remote_addr or "",
        degraded=bool(identity.degraded))


def _attach_session_cookies(response: Any, session: session_mod.ApprovalSession) -> Any:
    response.set_cookie(session_mod.SESSION_COOKIE_NAME, session.session_id,
                        httponly=True, samesite="Strict", max_age=int(
                            max(0, session.expires_at - session.created_at)))
    # CSRF 采用双重提交：JS 需读取该 Cookie 并回填请求头
    response.set_cookie("cp_approval_csrf", session.csrf_token,
                        httponly=False, samesite="Strict",
                        max_age=int(max(0, session.expires_at - session.created_at)))
    return response


def _public_decision(decision: Any) -> Dict[str, Any]:
    """对外暴露的判定结果（**只含叶子**，不含任何令牌/密钥）"""
    if decision is None:
        return {}
    return {
        "allowed": bool(getattr(decision, "allowed", False)),
        "reason": str(getattr(decision, "reason", "") or ""),
        "operation": str(getattr(decision, "operation", "") or ""),
        "actor_type": str(getattr(decision, "actor_type", "") or ""),
        "matrix_hit": bool(getattr(decision, "matrix_hit", False)),
        "denied_by_matrix": bool(getattr(decision, "denied_by_matrix", False)),
    }


# ════════════════════════════════════════════════════════════
#  路由注册
# ════════════════════════════════════════════════════════════

def register_routes(app, state=None) -> None:
    """注册审批 HTTP 面"""

    # ── 身份自述（前端据此决定是否渲染审批按钮；**后端仍是唯一权威**） ──

    @app.route("/api/approval/whoami", methods=["GET"])
    @require_token
    def approval_whoami():
        session_id = _session_id()
        identity = resolve_request_identity(session_id=session_id)
        session = session_mod.get_session_store().get_session(session_id)
        return jsonify({
            "ok": True,
            "actor": identity.actor,
            "actor_type": identity.actor_type,
            "identity_source": identity.identity_source,
            "identity_authority": identity.authority,
            "identity_degraded": bool(identity.degraded),
            "is_human": identity.actor_type == "human",
            "session": session.to_public() if session else None,
            "link_ttl_seconds": session_mod.link_ttl_seconds(),
            "csrf_enabled": session_mod.csrf_enabled(),
            "second_factor_passphrase_set": bool(
                session_mod.second_factor_passphrase()),
        })

    # ── 会话（审批面安全的锚点） ──

    @app.route("/api/approval/session", methods=["POST"])
    @require_token
    def approval_open_session():
        identity = resolve_request_identity()
        ctx = guard_mod.ActorContext(
            actor=identity.actor, actor_type=identity.actor_type,
            identity_source=identity.identity_source, actor_ip=request.remote_addr or "")
        # 查看面板权限先行（sub_agent ❌；auto 仅自身 scope）——进审批面前的第一道闸
        decision = guard_mod.authorize(
            operation=OP_VIEW_PANEL, actor_ctx=ctx, object_type="approval.panel",
            target_scope=identity.scope, report=True)
        if not decision.allowed:
            return _error("panel_denied", decision.reason, 403,
                          decision=_public_decision(decision))
        session = session_mod.get_session_store().open_session(
            actor=identity.actor, actor_type=identity.actor_type,
            identity_source=identity.identity_source, scope=identity.scope,
            actor_ip=request.remote_addr or "")
        response = make_response(jsonify({
            "ok": True,
            "session": session.to_public(),
            "csrf_header": session_mod.CSRF_HEADER_NAME,
        }))
        return _attach_session_cookies(response, session)

    @app.route("/api/approval/session", methods=["DELETE"])
    @require_token
    def approval_close_session():
        session_id = _session_id()
        closed = session_mod.get_session_store().close_session(session_id)
        response = make_response(jsonify({"ok": True, "closed": bool(closed)}))
        response.delete_cookie(session_mod.SESSION_COOKIE_NAME)
        response.delete_cookie("cp_approval_csrf")
        response.delete_cookie(LINK_COOKIE_NAME)
        return response

    # ── 待审批清单（human ✅ / auto 自身 scope / sub_agent ❌） ──

    @app.route("/api/approval/pending", methods=["GET"])
    @require_token
    def approval_pending():
        session_id = _session_id()
        ctx = _actor_ctx(session_id)
        decision = guard_mod.authorize(
            operation=OP_VIEW_PANEL, actor_ctx=ctx, object_type="approval.panel",
            target_scope=str(request.args.get("scope", "") or ""), report=True)
        if not decision.allowed:
            return _error("panel_denied", decision.reason, 403,
                          decision=_public_decision(decision))
        flow = get_approval_flow()
        limit = request.args.get("limit", type=int) or 50
        records = flow.list({"state": "pending_review"}, limit=max(1, min(limit, 200)))
        from agent.security.governance_bridge import (
            governance_trace_fields,
            taint_flags,
        )
        items = []
        for r in records:
            item = {
                "record_id": r.record_id, "object_type": r.object_type,
                "object_id": r.object_id, "level": r.level, "action": r.action,
                "description": str(r.description or "")[:200],
                "actor": r.actor, "actor_type": r.actor_type,
                "manual_required": bool(r.manual_required),
                "created_at": r.created_at,
                # 治理可回溯（对齐 S1-02 governance：能不能退、怎么退）
                "risk": guard_mod.resolve_risk(r.object_type, r.object_id, r.payload),
            }
            item.update(governance_trace_fields(r.object_type, r.object_id, r.payload))
            # 外来内容进审批上下文 → 前端渲染 TaintBadge（§5.7⑦）
            item.update(taint_flags(r.object_type, r.object_id, r.payload))
            items.append(item)
        return jsonify({
            "ok": True,
            "count": len(records),
            "items": items,
            "identity": {"actor": ctx.actor, "actor_type": ctx.resolved_type(),
                         "identity_source": ctx.identity_source,
                         "degraded": bool(ctx.degraded)},
        })

    # ── 审批链接（会话绑定 + 一次性 + ≤15min） ──

    @app.route("/api/approval/link", methods=["POST"])
    @require_token
    def approval_issue_link():
        session_id = _session_id()
        session = session_mod.get_session_store().get_session(session_id)
        if session is None:
            return _error(session_mod.CHECK_SESSION_UNKNOWN,
                          "请先开启审批会话（POST /api/approval/session）", 401)
        csrf = session_mod.get_session_store().verify_csrf(
            session_id, _csrf_header())
        if not csrf.ok:
            guard_mod.report_denial(
                _pseudo_decision(session, csrf.message),
                actor_ctx=_actor_ctx(session_id), source="ui")
            return _error(csrf.code, csrf.message, 403)
        body = request.get_json(silent=True) or {}
        record_id = str(body.get("record_id", "") or "")
        if not record_id:
            return _error("missing_record_id", "缺少 record_id", 400)
        flow = get_approval_flow()
        record = flow.get(record_id)
        if record is None:
            return _error("unknown_record", f"审批记录不存在: {record_id}", 404)
        risk = guard_mod.resolve_risk(record.object_type, record.object_id,
                                      record.payload)
        link = session_mod.get_session_store().issue_link(
            session_id=session_id, record_id=record_id, actor=session.actor,
            risk=risk)
        logger.info("[ApprovalRoutes] 签发审批链接 record=%s actor=%s risk=%s",
                    record_id, session.actor, risk or "-")
        response = make_response(jsonify({"ok": True, "link": link.to_public()}))
        response.set_cookie(LINK_COOKIE_NAME, link.token, httponly=True,
                            samesite="Strict",
                            max_age=int(session_mod.link_ttl_seconds()))
        return response

    @app.route("/api/approval/link/<token>", methods=["GET"])
    @require_token
    def approval_inspect_link(token: str):
        """校验链接（**不消费**）：分享式链接在此即暴露 `session_mismatch`"""
        session_id = _session_id()
        record_id = str(request.args.get("record_id", "") or "")
        check = session_mod.get_session_store().check_link(
            token, session_id=session_id, record_id=record_id)
        if not check.ok and check.code == session_mod.CHECK_SESSION_MISMATCH:
            guard_mod.report_denial(
                _pseudo_decision(
                    session_mod.get_session_store().get_session(session_id),
                    "审批链接与发起会话不匹配（分享式链接）"),
                actor_ctx=_actor_ctx(session_id), source="ui")
        return jsonify({"ok": check.ok, "code": check.code,
                        "message": check.message,
                        "requires_second_factor": check.requires_second_factor})

    # ── 二次认证（destructive 强制） ──

    @app.route("/api/approval/second-factor", methods=["POST"])
    @require_token
    def approval_issue_second_factor():
        session_id = _session_id()
        session = session_mod.get_session_store().get_session(session_id)
        if session is None:
            return _error(session_mod.CHECK_SESSION_UNKNOWN,
                          "请先开启审批会话", 401)
        csrf = session_mod.get_session_store().verify_csrf(
            session_id, _csrf_header())
        if not csrf.ok:
            return _error(csrf.code, csrf.message, 403)
        body = request.get_json(silent=True) or {}
        record_id = str(body.get("record_id", "") or "")
        if not record_id:
            return _error("missing_record_id", "缺少 record_id", 400)
        code = session_mod.get_session_store().issue_second_factor(
            session_id=session_id, record_id=record_id)
        # 确认码只回给**持有该会话 Cookie**的调用方（分享链接拿不到这一步）
        return jsonify({"ok": True, "record_id": record_id, "code": code,
                        "note": "一次性确认码：仅当前审批会话可见/可用"})

    # ── 审批动作（approve / reject） ──

    @app.route("/api/approval/<record_id>/approve", methods=["POST"])
    @require_token
    def approval_approve(record_id: str) -> Any:
        return _do_decision(record_id, approve=True)

    @app.route("/api/approval/<record_id>/reject", methods=["POST"])
    @require_token
    def approval_reject(record_id: str) -> Any:
        return _do_decision(record_id, approve=False)

    # ── 审批控制台（前端审批按钮区；DOM 隔离见模板/样式） ──

    @app.route("/api/approval/console", methods=["GET"])
    @require_token
    def approval_console():
        """审批控制台（前端审批按钮区；DOM 隔离见模板/样式）"""
        from flask import render_template
        try:
            return render_template("approval_console.html")
        except Exception as e:  # noqa: BLE001 模板缺失不阻断（返回说明页）
            logger.warning("[ApprovalRoutes] 审批控制台模板渲染失败: %s", e)
            return ("<h1>审批控制台不可用</h1>"
                    "<p>templates/approval_console.html 缺失</p>", 500)


# ════════════════════════════════════════════════════════════
#  审批动作实现
# ════════════════════════════════════════════════════════════

def _pseudo_decision(session: Any, reason: str) -> Any:
    """构造一个仅供告警使用的「拒绝」判定（非矩阵产出，如实标注）"""
    from agent.security.actor_matrix import PermissionDecision
    return PermissionDecision(
        allowed=False, operation="approval.link",
        actor=str(getattr(session, "actor", "") or ""),
        actor_type=str(getattr(session, "actor_type", "") or ""),
        reason=str(reason or ""), identity_source=str(
            getattr(session, "identity_source", "") or ""),
        matrix_hit=False)


def _do_decision(record_id: str, *, approve: bool) -> Any:
    """审批动作的 HTTP 包装（**对外行为与升级前逐字一致**）

    自 TASK-S6-01 起，安全链主体抽到 `_do_decision_with`，以便「审批收件箱」的
    **批量裁决**逐条复用**同一条**链路（会话 → CSRF → 链接 → 二次认证 → 矩阵）。
    本函数仍独占 HTTP 层职责：读请求、写 Cookie、决定状态码。
    """
    body = request.get_json(silent=True) or {}
    payload, status, redeemed = _do_decision_with(
        record_id, approve=approve,
        note=str(body.get("note", "") or body.get("reason", "") or ""),
        second_factor=str(body.get("second_factor", "") or ""),
        link_token=str(body.get("link_token", "") or "") or str(
            request.cookies.get(LINK_COOKIE_NAME, "") or ""),
        redeem=True)
    response = make_response(jsonify(payload), int(status))
    if redeemed:
        response.delete_cookie(LINK_COOKIE_NAME)
    return response


def _do_decision_with(record_id: str, *, approve: bool, note: str = "",
                      second_factor: str = "", link_token: str = "",
                      redeem: bool = True) -> Tuple[Dict[str, Any], int, bool]:
    """审批安全链（**唯一实现**；单条端点与批量裁决共用）

    顺序即安全边界（与 TASK-S4-01 交付时逐字一致）：
        会话 → CSRF 双重提交 → 一次性链接（会话绑定 + record 绑定 + ≤900s）
        → destructive 二次认证 → §7.0 Actor 矩阵 → 审批状态机。

    Args:
        record_id: 审批记录 id。
        approve: True=批准，False=驳回（驳回必须给 note，否则 400）。
        note: 备注 / 驳回理由。
        second_factor: 二次认证确认码（destructive 必填）。
        link_token: 一次性审批链接 token；空则退化为读 Cookie（单条路径行为）。
        redeem: 成功后是否核销链接（单条路径恒 True；批量逐条核销）。

    Returns:
        ``(json_body, http_status, redeemed)``——**不构造 Flask 响应、不写 Cookie**；
        这两个 HTTP 层副作用留给调用方，从而单条与批量共用同一判定实现。
    """
    store = session_mod.get_session_store()
    session_id = _session_id()
    session = store.get_session(session_id)
    if session is None:
        _resp, status = _error(session_mod.CHECK_SESSION_UNKNOWN,
                               "审批会话不存在或已过期（请重新开启会话并重新发起审批）",
                               401)
        return _resp.get_json(), status, False

    csrf = store.verify_csrf(session_id, _csrf_header())
    if not csrf.ok:
        guard_mod.report_denial(_pseudo_decision(session, csrf.message),
                                actor_ctx=_actor_ctx(session_id), source="ui")
        _resp, status = _error(csrf.code, csrf.message, 403)
        return _resp.get_json(), status, False

    check = store.check_link(link_token, session_id=session_id, record_id=record_id)
    if not check.ok:
        guard_mod.report_denial(_pseudo_decision(session, check.message),
                                actor_ctx=_actor_ctx(session_id),
                                record_id=record_id, source="ui")
        status = 401 if check.code in (session_mod.CHECK_SESSION_EXPIRED,
                                       session_mod.CHECK_UNKNOWN) else 403
        _resp, _ = _error(check.code, check.message, status,
                          requires_second_factor=check.requires_second_factor)
        return _resp.get_json(), status, False

    second_factor_ok = False
    if check.requires_second_factor:
        verify = store.verify_second_factor(
            session_id=session_id, record_id=record_id, code=second_factor)
        if not verify.ok:
            _resp, status = _error(verify.code, verify.message, 403,
                                   requires_second_factor=True)
            return _resp.get_json(), status, False
        second_factor_ok = True

    actor_ctx = _actor_ctx(session_id)
    if _require_authoritative() and actor_ctx.degraded:
        _resp, status = _error("identity_degraded",
                               "当前身份为降级来源（未命中令牌映射表），"
                               "已配置为禁止其执行审批", 403,
                               identity_source=actor_ctx.identity_source)
        return _resp.get_json(), status, False

    flow = get_approval_flow()
    try:
        if approve:
            record = flow.approve(record_id, actor=actor_ctx.actor, note=note,
                                  actor_ctx=actor_ctx,
                                  second_factor_ok=second_factor_ok)
        else:
            if not str(note or "").strip():
                _resp, status = _error("reason_required",
                                       "驳回必须提供 reason（审计要求）", 400)
                return _resp.get_json(), status, False
            record = flow.reject(record_id, actor=actor_ctx.actor, reason=note,
                                 actor_ctx=actor_ctx,
                                 second_factor_ok=second_factor_ok)
    except Exception as e:  # noqa: BLE001 审批域异常（含越权）→ 统一 403/409
        decision = getattr(e, "decision", None)
        name = type(e).__name__
        status = 403 if decision is not None else 409
        logger.warning("[ApprovalRoutes] 审批动作失败 record=%s type=%s: %s",
                       record_id, name, e)
        _resp, _ = _error("approval_denied" if decision is not None
                          else "approval_failed", str(e), status,
                          decision=_public_decision(decision))
        return _resp.get_json(), status, False

    redeemed = False
    if redeem:
        store.redeem_link(link_token, session_id=session_id, record_id=record_id)
        redeemed = True
    logger.info("[ApprovalRoutes] 审批动作完成 record=%s state=%s actor=%s",
                record_id, record.state, actor_ctx.actor)
    return {
        "ok": True,
        "record": {
            "record_id": record.record_id, "state": record.state,
            "object_type": record.object_type, "object_id": record.object_id,
            "level": record.level, "actor": record.actor,
            "actor_type": record.actor_type,
            "identity_source": record.identity_source,
            "decision_reason": str(record.decision_reason or "")[:400],
            "manual_required": bool(record.manual_required),
        },
        "second_factor_ok": bool(second_factor_ok),
        "pii": record.pii_fields(),   # 只含掩码/HMAC，**原始 IP 不在其中**
    }, 200, redeemed


__all__ = [
    "register_routes", "get_approval_flow", "set_approval_flow",
    "LINK_COOKIE_NAME",
]
