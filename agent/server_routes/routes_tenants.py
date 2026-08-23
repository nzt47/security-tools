"""多租户管理 API 路由（T8.1）

将 TenantManager / TenantConfigManager / BillingManager HTTP 化，
提供租户/用户/角色/配置/用量管理接口。

鉴权说明（T8.1 阶段）：
- 管理端点使用 require_token（FLASK_API_TOKEN，内部管理语义）
- T8.2 阶段升级为网关 API Key + RBAC（PermissionType 映射）

前缀：/api/open/tenants（网关 before_request 仅拦截已注册端点，
本模块未 register_endpoint 前由 Flask 原生路由分发，不受影响）。
"""
import logging

from flask import request, jsonify

from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route
from agent.multi_tenant import (
    tenant_manager,
    tenant_config_manager,
    billing_manager,
    TenantType,
    RoleType,
)

logger = logging.getLogger(__name__)


def _tenant_dict(tenant):
    """dataclass → dict（JSON 可序列化）"""
    return {
        "id": tenant.id,
        "name": tenant.name,
        "type": tenant.type.value if hasattr(tenant.type, "value") else str(tenant.type),
        "parent_id": tenant.parent_id,
        "metadata": tenant.metadata,
        "created_at": tenant.created_at,
        "updated_at": tenant.updated_at,
    }


def register_routes(app, state):
    """注册多租户管理路由"""
    # ═══════════════════════════════════════════════════
    #  租户 CRUD
    # ═══════════════════════════════════════════════════

    @app.route("/api/open/tenants", methods=["POST"])
    @trace_route("Tenant")
    @require_token
    @log_request()
    def api_tenant_create():
        """创建组织租户（自动创建 owner 用户并分配 OWNER 角色）"""
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        owner_email = (data.get("owner_email") or "").strip()
        owner_name = (data.get("owner_name") or owner_email).strip()
        if not name or not owner_email:
            return jsonify({"ok": False, "error": "缺少 name 或 owner_email"}), 400
        owner = tenant_manager.create_user(owner_email, owner_name)
        org = tenant_manager.create_organization(name, owner.id)
        return jsonify({"ok": True, "organization": _tenant_dict(org),
                        "owner": vars(owner)}), 201

    @app.route("/api/open/tenants", methods=["GET"])
    @trace_route("Tenant")
    @require_token
    @log_request(show_response=False)
    def api_tenant_list():
        """列出全部租户（可选 ?type=organization|workspace）"""
        t_type = request.args.get("type", "")
        tenant_type = None
        if t_type:
            try:
                tenant_type = TenantType(t_type)
            except ValueError:
                return jsonify({"ok": False, "error": f"未知租户类型: {t_type}"}), 400
        tenants = [_tenant_dict(t) for t in tenant_manager.list_tenants(tenant_type)]
        return jsonify({"ok": True, "tenants": tenants, "count": len(tenants)})

    @app.route("/api/open/tenants/<tenant_id>", methods=["GET"])
    @trace_route("Tenant")
    @require_token
    @log_request(show_response=False)
    def api_tenant_get(tenant_id):
        """获取租户详情"""
        tenant = tenant_manager.get_tenant(tenant_id)
        if not tenant:
            return jsonify({"ok": False, "error": "租户不存在"}), 404
        return jsonify({"ok": True, "tenant": _tenant_dict(tenant)})

    @app.route("/api/open/tenants/<tenant_id>", methods=["DELETE"])
    @trace_route("Tenant")
    @require_token
    @log_request()
    def api_tenant_delete(tenant_id):
        """删除租户（级联删除子租户与角色分配）"""
        if not tenant_manager.get_tenant(tenant_id):
            return jsonify({"ok": False, "error": "租户不存在"}), 404
        tenant_manager.delete_tenant(tenant_id)
        return jsonify({"ok": True, "deleted": tenant_id})

    # ═══════════════════════════════════════════════════
    #  用户与角色
    # ═══════════════════════════════════════════════════

    @app.route("/api/open/tenants/<tenant_id>/users", methods=["POST"])
    @trace_route("Tenant")
    @require_token
    @log_request()
    def api_tenant_user_create(tenant_id):
        """创建用户并加入租户 {email, name, role}（默认 member）"""
        if not tenant_manager.get_tenant(tenant_id):
            return jsonify({"ok": False, "error": "租户不存在"}), 404
        data = request.get_json() or {}
        email = (data.get("email") or "").strip()
        if not email:
            return jsonify({"ok": False, "error": "缺少 email"}), 400
        role_name = data.get("role", "member")
        try:
            role = RoleType(role_name)
        except ValueError:
            return jsonify({"ok": False, "error": f"未知角色: {role_name}"}), 400
        user = tenant_manager.create_user(email, data.get("name") or email)
        tenant_manager.assign_role(user.id, tenant_id, role)
        return jsonify({"ok": True, "user": vars(user), "role": role.value}), 201

    @app.route("/api/open/tenants/<tenant_id>/users/<user_id>/roles", methods=["POST"])
    @trace_route("Tenant")
    @require_token
    @log_request()
    def api_tenant_assign_role(tenant_id, user_id):
        """分配/更新用户在租户中的角色 {role}"""
        if not tenant_manager.get_tenant(tenant_id):
            return jsonify({"ok": False, "error": "租户不存在"}), 404
        data = request.get_json() or {}
        role_name = (data.get("role") or "").strip()
        try:
            role = RoleType(role_name)
        except ValueError:
            return jsonify({"ok": False, "error": f"未知角色: {role_name}"}), 400
        tenant_manager.assign_role(user_id, tenant_id, role)
        return jsonify({"ok": True, "user_id": user_id, "tenant_id": tenant_id,
                        "role": role.value})

    @app.route("/api/open/users/<user_id>/tenants", methods=["GET"])
    @trace_route("Tenant")
    @require_token
    @log_request(show_response=False)
    def api_user_tenants(user_id):
        """获取用户可访问的租户列表"""
        tenants = [_tenant_dict(t) for t in tenant_manager.get_user_tenants(user_id)]
        return jsonify({"ok": True, "user_id": user_id, "tenants": tenants})

    # ═══════════════════════════════════════════════════
    #  租户配置与用量（T8.3 配额的基础）
    # ═══════════════════════════════════════════════════

    @app.route("/api/open/tenants/<tenant_id>/config", methods=["GET", "POST"])
    @trace_route("Tenant")
    @require_token
    @log_request()
    def api_tenant_config(tenant_id):
        """获取（含继承）/ 设置租户配置"""
        if not tenant_manager.get_tenant(tenant_id):
            return jsonify({"ok": False, "error": "租户不存在"}), 404
        if request.method == "POST":
            data = request.get_json() or {}
            for key, value in data.items():
                tenant_config_manager.set_config(tenant_id, key, value)
            return jsonify({"ok": True,
                            "config": tenant_config_manager.get_all_configs(tenant_id)})
        return jsonify({"ok": True,
                        "config": tenant_config_manager.get_all_configs(tenant_id)})

    @app.route("/api/open/tenants/<tenant_id>/usage", methods=["GET"])
    @trace_route("Tenant")
    @require_token
    @log_request(show_response=False)
    def api_tenant_usage(tenant_id):
        """获取租户用量统计（billing，T8.3 配额依据）"""
        if not tenant_manager.get_tenant(tenant_id):
            return jsonify({"ok": False, "error": "租户不存在"}), 404
        period = request.args.get("period", "month")
        usage = billing_manager.get_usage(tenant_id, period=period)
        usage["plan"] = billing_manager._get_tenant_plan(tenant_id)
        return jsonify({"ok": True, "usage": usage})

    logger.info("[TenantRoutes] 已注册多租户管理路由 (/api/open/tenants/*)")
