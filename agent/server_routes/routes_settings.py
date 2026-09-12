"""开关中心 HTTP 面（TASK-S7-01 步骤 3）

【端点（三组 + 一个双人确认端点）】
    | 方法 | 路径 | 作用 |
    |---|---|---|
    | GET  | `/api/cp/settings` | 全部条目：元数据 + 当前值 + **生效来源** + 是否被覆盖 + 置灰原因 |
    | POST | `/api/cp/settings/<key>` | 改值（A 直接 / B 二次认证 + 双人确认 / C 403） |
    | POST | `/api/cp/settings/<key>/reset` | 清除覆盖层（回落 config/default） |
    | POST | `/api/cp/settings/<key>/confirm` | B 级第二位人工确认（双人确认的落地口） |

【安全口径（与既有 `routes_ui_panels` 同款，不新增旁路）】
    - 全部路由 `@require_token`；
    - 身份一律经 `resolve_request_identity()`（**请求体里的 actor / actor_type 一律忽略**）；
    - 二次认证复用 S4-01 的 `approval_session`（一次性确认码或配置口令），
      绑定到该会话 + `setting:<key>` 的记录标识；
    - C 级（密钥/端点/路径）**响应体不含明文**（由 `masking` 保证，并有运行时守卫
      `assert_no_plaintext` 兜底）；
    - **没有批量端点**：请求体是数组或含 `keys` 字段 → 400 `batch_not_supported`
      （B 级因此不可能被"一次提交多个键"绕过）。

【口径纪律（S5-02/S6-01）】
    条目里的 `counts` 全部来自真实注册表统计；不出现不可追溯的数字。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from flask import jsonify, make_response, request

from agent.security import approval_guard as guard_mod
from agent.security.actor_matrix import OP_VIEW_PANEL
from agent.server_auth import require_token, resolve_request_identity
from agent.settings import masking
from agent.settings.registry import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    RISK_LABELS,
    all_specs,
    counts_by_risk,
)
from agent.settings.resolver import SOURCE_PRIORITY, resolve_all
from agent.settings.service import (
    CODE_BATCH_NOT_SUPPORTED,
    CODE_SECOND_FACTOR_INVALID,
    get_settings_service,
)
from agent.ui_panels.schema import panel_map

logger = logging.getLogger(__name__)

PREFIX = "/api/cp"

#: 面板标识（与 `agent/ui_panels/schema.py` 的 panel_map 约定一致）
PANEL_NAME = "settings_center"

#: 只读声明（响应里明确告知前端：C 级永不返回明文）
READ_ONLY_NOTICE = ("C 级（密钥/凭据/端点/绝对路径）只读脱敏，永不返回明文；"
                    "被环境变量锁定的项在 UI 置灰并注明原因")


# ════════════════════════════════════════════════════════════
#  公共助手
# ════════════════════════════════════════════════════════════

def _error(code: str, message: str, status: int = 403,
           **extra: Any) -> Tuple[Any, int]:
    body: Dict[str, Any] = {"ok": False, "code": code, "message": message}
    body.update({k: v for k, v in extra.items() if v is not None})
    return jsonify(body), status


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


def _public_decision(decision: Any) -> Dict[str, Any]:
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
    }


def _authorize_view() -> Optional[Any]:
    """只读面板查看权（§7.0「查看面板」行；auto 仅自身 scope，sub_agent ❌）"""
    ctx = _actor_ctx()
    decision = guard_mod.authorize(
        operation=OP_VIEW_PANEL, actor_ctx=ctx, object_type="panel.settings",
        report=True)
    if not decision.allowed:
        return _error("panel_denied", decision.reason, 403,
                      decision=_public_decision(decision))
    return None


def _second_factor_ok(key: str) -> Tuple[bool, str]:
    """复用 S4-01 的二次认证（会话绑定 + 一次性确认码/配置口令）

    【检查顺序＝提示顺序（S7-01 复核修正）】
        先判**会话**、再判**凭据**。理由：会话是更根本的前置条件，而前端只能提交
        二次认证码、**无法自行开会话**——若先报"缺少 second_factor"，首次使用的人
        会一直补码却始终不过（实测：无会话且未带码时，旧实现的提示是
        "B 级开关需二次认证（身体字段 second_factor）"，**完全没提要先开会话**）。
        两段提示都要求可执行，且都不回显任何凭据。

    Returns:
        `(ok, reason)`；`ok=False` 时 reason 为可读原因。
    """
    import agent.security.approval_session as session_mod

    body = request.get_json(silent=True) or {}
    code = str(body.get("second_factor", "") or "")
    session_id = str(request.cookies.get(session_mod.SESSION_COOKIE_NAME, "") or "")
    if not session_id:
        return False, ("B 级开关需二次认证，且必须先开启审批会话："
                       "请先 POST /api/approval/session，再带 second_factor 提交"
                       "（开关中心只能提交认证码，会话需由运维侧开启）")
    if not code:
        return False, "B 级开关需二次认证（请求体字段 second_factor）"
    store = session_mod.get_session_store()
    check = store.verify_second_factor(session_id=session_id,
                                       record_id=f"setting:{key}", code=code)
    if not check.ok:
        return False, f"{check.message or '二次认证未通过'}（{check.code}）"
    return True, ""


def _reject_batch() -> Optional[Any]:
    """**没有批量入口**：数组体或 keys 字段一律 400（防绕过双人确认）"""
    payload = request.get_json(silent=True)
    if isinstance(payload, list):
        return _error(CODE_BATCH_NOT_SUPPORTED,
                      "开关中心没有批量端点：一次只能改一个 key"
                      "（B 级双人确认不得被批量提交绕过）", 400)
    if isinstance(payload, dict) and ("keys" in payload or "items" in payload):
        return _error(CODE_BATCH_NOT_SUPPORTED,
                      "开关中心没有批量端点：不接受 keys / items 批量提交", 400)
    return None


def _identity_fields() -> Dict[str, str]:
    identity = resolve_request_identity(
        session_id=str(request.cookies.get("cp_approval_session", "") or ""))
    return {
        "actor": str(identity.actor or ""),
        "actor_type": str(identity.actor_type or "human"),
        "session_id": str(identity.session_id or ""),
        "identity_source": str(identity.identity_source or ""),
    }


def _build_index() -> Dict[str, Any]:
    """组装 `GET /api/cp/settings` 的响应体

    【口径纪律（复用 S6-01，而非重造）】
        - 面板台账**取自 `ui_panels.schema.panel_map()`**（唯一来源：不在本模块
          自造数据源清单）；
        - `counts` 的每个数字都在 `counts_provenance` 里给出**数据源 + 公式**
          （"每个数字都能在此找到出处"）；
        - C 级条目在序列化前就已是掩码形态（`masking.mask_display`），并有运行时
          守卫 `assert_no_plaintext` 再核对一次（双保险，绝不把明文写进响应）。

    注：`items` 子树的数值是注册表的**声明默认值**（含若干 0..1 比率），其出处由
    每条的 `owner_module` + `description` 给出，不是"算出来的指标"，故不套 `metric()`
    信封；`untraceable_scan()` 把 `items` 前缀按 pass-through 处理，因此它在 settings
    响应上是**空扫**（`checked=0`）——这一点在 `test_settings_routes` 里有显式说明，
    以免把"空扫通过"误读成"已通过口径自检"。
    """
    import os
    import time

    resolved = resolve_all()
    items = [r.to_public_dict() for r in resolved]
    # ★ 运行时守卫：C 级若漏了明文，这里直接抛错（而不是"悄悄发出去"）
    # 【2026-09-13 修正】只检查**承载值**的字段（value/display_value），不对整条
    # 投影做子串匹配。原因：元数据（default / validator.options / description）
    # 本身就是公开信息且与"当前值"无关，其中的字面量会与短值/枚举值撞车而误判：
    #   · ERROR_REPORTING_FILE_PATH 的 default 与 .env 值相同（按默认配置填写）
    #   · CP_POLICY_INBOX_BACKEND 的枚举值（如 "memory"）出现在 validator.options
    # 两者都会让 fail-closed 守卫误抛，使 GET /api/cp/settings 必现 500。
    # 精确到值字段后，"值泄漏"仍然 fail-closed（C 级 value 应恒为 None、
    # display_value 应为掩码形态），安全性不降低；C 级 default 亦已不投影。
    for r in resolved:
        spec = r.spec
        if spec.risk == "C" and spec.env_name:
            raw = os.environ.get(spec.env_name)
            if raw:
                masking.assert_no_plaintext(
                    {"value": r.value, "display_value": r.display_value},
                    raw, label=spec.key)
    by_category: Dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
    for r in resolved:
        by_category[r.spec.category] = by_category.get(r.spec.category, 0) + 1
    risk_counts = counts_by_risk()
    editable = sum(1 for r in resolved if r.editable)
    overridden = sum(1 for r in resolved if r.override_present)
    env_locked = sum(1 for r in resolved if r.env_locked)
    panel = panel_map(PANEL_NAME)
    panel["name"] = PANEL_NAME          # 兼容前端既有 PanelMeta 之外的可选字段
    panel["title"] = "开关中心"
    return {
        "ok": True,
        "prefix": PREFIX,
        "panel": panel,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_priority": list(SOURCE_PRIORITY),
        "categories": [
            {"id": c, "label": CATEGORY_LABELS[c], "count": by_category.get(c, 0)}
            for c in CATEGORY_ORDER
        ],
        "risk_labels": dict(RISK_LABELS),
        "counts": {
            "total": len(items),
            "by_category": by_category,
            "by_risk": risk_counts,
            "editable": editable,
            "locked": len(items) - editable,
            "overridden": overridden,
            "locked_by_env": env_locked,
            "env_only": sum(1 for r in resolved if r.spec.env_only),
            "needs_restart": sum(1 for r in resolved if r.spec.needs_restart),
            "secret": sum(1 for r in resolved if r.spec.secret),
        },
        "counts_provenance": {
            "source": ("agent/settings/registry.py::all_specs()"
                       " + agent/settings/resolver.py::resolve_all()"),
            "total": "注册表条目数（len(all_specs())）",
            "by_category": "按 SettingSpec.category 分组计数",
            "by_risk": "按 SettingSpec.risk（A/B/C）分组计数",
            "editable": ("resolve() 判定为可改的条目数（非 C 级、未被 env 锁定、"
                         "非纯 config.yaml 项）"),
            "locked": "total - editable",
            "overridden": "存在覆盖层记录（OverrideStore.has(key)）的条目数",
            "locked_by_env": "被运维注入的环境变量锁定的条目数",
            "env_only": ("有 env_name 而无 config_path 的条目数"
                         "（UI 标注『仅支持环境变量』）"),
            "needs_restart": "SettingSpec.needs_restart 为真的条目数",
            "secret": "SettingSpec.secret 为真的条目数（值永不返回明文）",
            "note": ("全部数字取自注册表真实统计，不含估算；"
                     "每条条目的默认值出处见其 owner_module"),
        },
        "items": items,
        "read_only_notice": READ_ONLY_NOTICE,
        "registry_size": len(all_specs()),
    }


# ════════════════════════════════════════════════════════════
#  路由注册
# ════════════════════════════════════════════════════════════

def register_routes(app: Any, state: Any = None) -> None:  # noqa: ARG001
    """把开关中心路由注册到 Flask app（与既有 `server_routes/*` 同款模式）"""

    @app.route(f"{PREFIX}/settings", methods=["GET"])
    @require_token
    def cp_settings_index():
        err = _authorize_view()
        if err:
            return err
        response = make_response(jsonify(_build_index()))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route(f"{PREFIX}/settings/<key>", methods=["POST"])
    @require_token
    def cp_settings_change(key: str):
        # 写路径**不做视图预筛**：由服务层用 §7.0 的 `settings.change` 行统一裁决
        # （否则 auto/sub_agent 会先被"查看面板"行拦下，错误码反而说不清原因）
        batch = _reject_batch()
        if batch:
            return batch
        body = request.get_json(silent=True) or {}
        if "value" not in body:
            return _error("missing_value", "请求体必须含 value 字段", 400)
        ident = _identity_fields()
        second_ok, why = (False, "")
        spec_risk = _risk_of(key)
        if spec_risk == "B":
            second_ok, why = _second_factor_ok(key)
        service = get_settings_service()
        outcome = service.change(
            key, body.get("value"), actor=ident["actor"],
            actor_type=ident["actor_type"], reason=str(body.get("reason", "") or ""),
            second_factor_ok=second_ok and spec_risk == "B",
            session_id=ident["session_id"],
            identity_source=ident["identity_source"],
            pending_id=str(body.get("pending_id", "") or ""))
        if not outcome.ok and outcome.code == "second_factor_required" and why:
            outcome.message = why
        response = make_response(jsonify(outcome.to_dict()), outcome.status)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route(f"{PREFIX}/settings/<key>/confirm", methods=["POST"])
    @require_token
    def cp_settings_confirm(key: str):
        batch = _reject_batch()
        if batch:
            return batch
        body = request.get_json(silent=True) or {}
        pending_id = str(body.get("pending_id", "") or "")
        if not pending_id:
            return _error("missing_pending_id",
                          "确认请求必须带 pending_id（由首次提交返回）", 400)
        ident = _identity_fields()
        second_ok, why = _second_factor_ok(key)
        if not second_ok:
            return _error(CODE_SECOND_FACTOR_INVALID, why, 403)
        outcome = get_settings_service().confirm(
            key, pending_id, actor=ident["actor"],
            actor_type=ident["actor_type"],
            reason=str(body.get("reason", "") or ""), second_factor_ok=True,
            session_id=ident["session_id"],
            identity_source=ident["identity_source"])
        response = make_response(jsonify(outcome.to_dict()), outcome.status)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route(f"{PREFIX}/settings/<key>/reset", methods=["POST"])
    @require_token
    def cp_settings_reset(key: str):
        batch = _reject_batch()
        if batch:
            return batch
        body = request.get_json(silent=True) or {}
        ident = _identity_fields()
        outcome = get_settings_service().reset(
            key, actor=ident["actor"], actor_type=ident["actor_type"],
            reason=str(body.get("reason", "") or ""),
            session_id=ident["session_id"],
            identity_source=ident["identity_source"])
        response = make_response(jsonify(outcome.to_dict()), outcome.status)
        response.headers["Cache-Control"] = "no-store"
        return response


def _risk_of(key: str) -> str:
    from agent.settings.registry import get_spec
    spec = get_spec(key)
    return spec.risk if spec is not None else ""


__all__ = ["register_routes", "PREFIX", "PANEL_NAME", "READ_ONLY_NOTICE"]
