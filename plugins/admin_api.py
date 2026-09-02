# -*- coding: utf-8 -*-
"""云枢管理后台 API 插件（2026-09-01 新增）：登录 / 用户 / 角色 / 菜单 / 审计 / 通知 / 仪表盘。

背景：develop 分支的完整管理后台前端（router/MainLayout/Sidebar/pages/system/*）已合回
主工作区。前端 API 契约与 yunshu-ui/src/mocks/devMock.ts 完全一致（该 mock 仅 dev
server 生效）。本插件把同一套接口在 Flask 后端（5678 生产模式）落地，数据为内存态
演示数据（与 devMock 同构），后续可替换为真实持久化存储。

约定（PLAN-1 §4）：
  - Blueprint 不设 url_prefix，路由保持 /api/... 原样；
  - 插件模块顶层只 import flask / plugin_api / 标准库；
  - 管理后台 token 为自包含格式 mock-token-<username>-<timestamp>（与 app_server 的
    FLASK_API_TOKEN 相互独立），从 Bearer 头解析用户名；Web 演示态不做强校验。
"""
import json
import re
import time

from flask import Blueprint, request, jsonify

from .plugin_api import Plugin, register_plugin

bp = Blueprint("admin_api", __name__)

# ════════════════════════════════════════════════════════════════════════════
#  内存态数据（与 devMock 同构）
# ════════════════════════════════════════════════════════════════════════════

_USERS = [
    {"id": 1, "username": "admin", "nickname": "本地管理员", "email": "admin@yunshu.local",
     "role": "admin", "status": 1, "createdAt": "2026-01-01 09:00:00",
     "permissions": ["dashboard:view", "workbench:use", "prompt-lab:use",
                     "system:view", "system:user:view", "system:role:view",
                     "system:audit:view", "system:notification:view", "system:log:export"]},
    {"id": 2, "username": "user", "nickname": "普通用户", "email": "user@yunshu.local",
     "role": "user", "status": 1, "createdAt": "2026-02-01 09:00:00",
     "permissions": ["dashboard:view", "workbench:use", "system:view", "system:notification:view"]},
]
for _i in range(3, 27):
    _USERS.append({
        "id": _i, "username": f"user{_i:02d}", "nickname": f"用户{_i}",
        "email": f"user{_i}@yunshu.local",
        "role": "admin" if _i == 1 else ("manager" if _i % 3 == 0 else "user"),
        "status": 0 if _i % 5 == 0 else 1,
        "createdAt": f"2026-0{(_i % 9) + 1}-{(_i % 27) + 1:02d} 10:30:00",
        "permissions": ["dashboard:view", "workbench:use"],
    })

_ROLES = [
    {"id": 1, "name": "admin", "label": "系统管理员", "description": "拥有全部权限",
     "permissions": ["*"], "dataScope": "all", "status": 1, "createdAt": "2026-01-01 09:00:00"},
    {"id": 2, "name": "manager", "label": "部门经理", "description": "部门数据权限",
     "permissions": ["dashboard:view", "system:view", "system:audit:view"], "dataScope": "dept",
     "status": 1, "createdAt": "2026-01-02 09:00:00"},
    {"id": 3, "name": "user", "label": "普通用户", "description": "基础使用权限",
     "permissions": ["dashboard:view", "workbench:use", "system:view",
                     "system:notification:view"], "dataScope": "self",
     "status": 1, "createdAt": "2026-01-03 09:00:00"},
]

_PERMISSIONS = [
    {"code": "dashboard:view", "label": "查看仪表盘", "group": "基础"},
    {"code": "workbench:use", "label": "使用工作台", "group": "基础"},
    {"code": "prompt-lab:use", "label": "使用提示词实验室", "group": "基础"},
    {"code": "system:view", "label": "查看系统管理", "group": "系统管理"},
    {"code": "system:user:view", "label": "查看用户列表", "group": "系统管理"},
    {"code": "system:role:view", "label": "查看角色权限", "group": "系统管理"},
    {"code": "system:audit:view", "label": "查看操作审计", "group": "系统管理"},
    {"code": "system:notification:view", "label": "查看消息中心", "group": "系统管理"},
    {"code": "system:log:export", "label": "导出系统日志", "group": "系统管理"},
]

_ALL_MENUS = [
    {"path": "/dashboard", "title": "仪表盘", "icon": "dashboard"},
    {"path": "/workbench", "title": "工作台", "icon": "workbench"},
    {"path": "/demo", "title": "组件演示", "icon": "demo"},
    {"path": "/export", "title": "数据导出", "icon": "export"},
    {"path": "/system", "title": "系统管理", "icon": "system", "authority": "system:view",
     "children": [
         {"path": "/system/user", "title": "用户列表", "icon": "user", "authority": "system:user:view"},
         {"path": "/system/role", "title": "角色权限", "icon": "role", "authority": "system:role:view"},
         {"path": "/system/menu", "title": "菜单管理", "icon": "menu", "authority": "system:role:view"},
         {"path": "/system/audit", "title": "操作审计", "icon": "audit", "authority": "system:audit:view"},
         {"path": "/system/notification", "title": "消息中心", "icon": "notification", "authority": "system:notification:view"},
         {"path": "/system/log", "title": "系统日志", "icon": "log", "authority": "system:view"},
     ]},
]

_AUDIT_LOGS = [
    {"id": i, "action": ("login", "create", "update", "delete", "export")[i % 5],
     "operator": ("admin", "manager", "user")[i % 3],
     "target": f"user{ (i % 20) + 1:02d}", "ip": f"127.0.0.{i % 255}",
     "createdAt": f"2026-08-{ (i % 28) + 1:02d} { (i % 24):02d}:{(i * 7) % 60:02d}:00"}
    for i in range(1, 33)
]

_NOTIFICATIONS = [
    {"id": i, "type": ("system", "audit", "approval", "alert")[i % 4],
     "title": f"通知消息 {i}", "content": f"这是第 {i} 条通知内容",
     "read": i % 3 == 0, "createdAt": f"2026-08-{ (i % 28) + 1:02d} {(i * 3) % 24:02d}:00:00"}
    for i in range(1, 21)
]


# ════════════════════════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════════════════════════

def _token_username():
    """从 Authorization Bearer 头解析用户名（管理后台自包含 token）。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        m = re.match(r"^mock-token-(.+)-(\d+)$", token)
        if m:
            return m.group(1)
    return None


def _ok(data=None, message="success"):
    return jsonify({"code": 200, "data": data, "message": message})


def _fail(code, message):
    return jsonify({"code": code, "data": None, "message": message}), 200


def _paginate(items, page, page_size, keyword_field=None, keyword=None):
    page = max(int(page or 1), 1)
    page_size = max(int(page_size or 10), 1)
    if keyword and keyword_field:
        items = [x for x in items if keyword.lower() in str(x.get(keyword_field, "")).lower()]
    total = len(items)
    start = (page - 1) * page_size
    return {"list": items[start:start + page_size], "total": total}


def _filter_menus(nodes, username):
    """按角色/权限过滤菜单树（admin 通配）。"""
    user = next((u for u in _USERS if u["username"] == username), None)
    role = user["role"] if user else "user"
    perms = user["permissions"] if user else []

    def _ok_node(node):
        authority = node.get("authority")
        if authority and role != "admin" and authority not in perms:
            return False
        children = node.get("children")
        if children:
            kept = [c for c in children if _ok_node(c)]
            if not kept:
                return False
            node = dict(node)
            node["children"] = kept
        return True

    return [dict(n) for n in nodes if _ok_node(n)]


# ════════════════════════════════════════════════════════════════════════════
#  认证与用户
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/auth/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    # 演示态：admin/123456 与 user/123456（与 devMock 一致）
    if password != "123456":
        return _fail(400, "用户名或密码错误")
    user = next((u for u in _USERS if u["username"] == username), None)
    if not user:
        return _fail(400, "用户名或密码错误")
    token = f"mock-token-{username}-{int(time.time() * 1000)}"
    return _ok({"token": token, "user": user})


@bp.route("/api/user/info", methods=["GET"])
def admin_user_info():
    username = _token_username()
    if not username:
        return _fail(401, "未登录或登录已过期")
    user = next((u for u in _USERS if u["username"] == username), None)
    if not user:
        return _fail(401, "用户不存在")
    return _ok(user)


@bp.route("/api/auth/menus", methods=["GET"])
def admin_menus():
    username = _token_username()
    if not username:
        return _fail(401, "未登录或登录已过期")
    return _ok(_filter_menus(_ALL_MENUS, username))


@bp.route("/api/user/list", methods=["GET"])
def admin_user_list():
    page = request.args.get("page", 1)
    page_size = request.args.get("pageSize", 10)
    keyword = request.args.get("keyword", "")
    return _ok(_paginate(_USERS, page, page_size, "username", keyword))


@bp.route("/api/user/<int:user_id>", methods=["DELETE"])
def admin_user_delete(user_id):
    global _USERS
    if user_id == 1:
        return _fail(400, "内置管理员不可删除")
    _USERS = [u for u in _USERS if u["id"] != user_id]
    return _ok()


@bp.route("/api/user", methods=["POST"])
def admin_user_create():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        return _fail(400, "用户名不能为空")
    if any(u["username"] == username for u in _USERS):
        return _fail(400, "用户名已存在")
    new_id = max((u["id"] for u in _USERS), default=0) + 1
    _USERS.append({
        "id": new_id, "username": username,
        "nickname": data.get("nickname") or username,
        "email": data.get("email") or f"{username}@yunshu.local",
        "role": data.get("role") or "user", "status": data.get("status", 1),
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "permissions": ["dashboard:view", "workbench:use"],
    })
    return _ok(_USERS[-1])


@bp.route("/api/user/<int:user_id>", methods=["PUT"])
def admin_user_update(user_id):
    data = request.get_json(silent=True) or {}
    user = next((u for u in _USERS if u["id"] == user_id), None)
    if not user:
        return _fail(404, "用户不存在")
    for key in ("nickname", "email", "role", "status"):
        if key in data:
            user[key] = data[key]
    return _ok(user)


# ════════════════════════════════════════════════════════════════════════════
#  角色与权限
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/role/list", methods=["GET"])
def admin_role_list():
    page = request.args.get("page", 1)
    page_size = request.args.get("pageSize", 10)
    keyword = request.args.get("keyword", "")
    return _ok(_paginate(_ROLES, page, page_size, "name", keyword))


@bp.route("/api/permissions", methods=["GET"])
def admin_permissions():
    return _ok(_PERMISSIONS)


@bp.route("/api/role", methods=["POST"])
def admin_role_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return _fail(400, "角色名不能为空")
    if any(r["name"] == name for r in _ROLES):
        return _fail(400, "角色已存在")
    new_id = max((r["id"] for r in _ROLES), default=0) + 1
    _ROLES.append({
        "id": new_id, "name": name, "label": data.get("label") or name,
        "description": data.get("description") or "", "permissions": data.get("permissions") or [],
        "dataScope": data.get("dataScope") or "self", "status": 1,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return _ok(_ROLES[-1])


@bp.route("/api/role/<int:role_id>/permissions", methods=["PUT"])
def admin_role_permissions(role_id):
    data = request.get_json(silent=True) or {}
    role = next((r for r in _ROLES if r["id"] == role_id), None)
    if not role:
        return _fail(404, "角色不存在")
    if role["name"] == "admin":
        role["permissions"] = ["*"]
    else:
        role["permissions"] = data.get("permissions") or []
    return _ok(role)


@bp.route("/api/role/<int:role_id>/data-scope", methods=["PUT"])
def admin_role_data_scope(role_id):
    data = request.get_json(silent=True) or {}
    role = next((r for r in _ROLES if r["id"] == role_id), None)
    if not role:
        return _fail(404, "角色不存在")
    role["dataScope"] = data.get("dataScope", "self")
    return _ok(role)


@bp.route("/api/role/<int:role_id>", methods=["PUT"])
def admin_role_update(role_id):
    data = request.get_json(silent=True) or {}
    role = next((r for r in _ROLES if r["id"] == role_id), None)
    if not role:
        return _fail(404, "角色不存在")
    for key in ("label", "description"):
        if key in data:
            role[key] = data[key]
    return _ok(role)


@bp.route("/api/role/<int:role_id>", methods=["DELETE"])
def admin_role_delete(role_id):
    global _ROLES
    role = next((r for r in _ROLES if r["id"] == role_id), None)
    if not role:
        return _fail(404, "角色不存在")
    if role["name"] == "admin":
        return _fail(400, "内置管理员角色不可删除")
    _ROLES = [r for r in _ROLES if r["id"] != role_id]
    return _ok()


# ════════════════════════════════════════════════════════════════════════════
#  菜单管理
# ════════════════════════════════════════════════════════════════════════════

_MENU_TABLE = [
    {"id": 1, "parentId": 0, "title": "仪表盘", "path": "/dashboard", "icon": "LayoutDashboard",
     "authority": "", "order": 1, "hideInMenu": False},
    {"id": 2, "parentId": 0, "title": "工作台", "path": "/workbench", "icon": "Workflow",
     "authority": "", "order": 2, "hideInMenu": False},
    {"id": 3, "parentId": 0, "title": "系统管理", "path": "/system", "icon": "Settings",
     "authority": "system:view", "order": 10, "hideInMenu": False},
    {"id": 4, "parentId": 3, "title": "用户列表", "path": "/system/user", "icon": "Users",
     "authority": "system:user:view", "order": 1, "hideInMenu": False},
    {"id": 5, "parentId": 3, "title": "角色权限", "path": "/system/role", "icon": "ShieldCheck",
     "authority": "system:role:view", "order": 2, "hideInMenu": False},
]


@bp.route("/api/menu/tree", methods=["GET"])
def admin_menu_tree():
    return _ok(_MENU_TABLE)


# ════════════════════════════════════════════════════════════════════════════
#  通知 / 仪表盘 / 导出
#  （审计日志 /api/audit/logs 由 plugins/admin.py 提供——真实审计日志查询，
#   2026-09-01 已修复其 filter_by_key 500 并兼容前端分页契约，此处不重复注册）
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/notification/list", methods=["GET"])
def admin_notification_list():
    page = request.args.get("page", 1)
    page_size = request.args.get("pageSize", 10)
    return _ok(_paginate(_NOTIFICATIONS, page, page_size))


@bp.route("/api/notification/unread-count", methods=["GET"])
def admin_notification_unread():
    return _ok({"count": sum(1 for n in _NOTIFICATIONS if not n["read"])})


@bp.route("/api/notification/<int:notif_id>/read", methods=["POST"])
def admin_notification_read(notif_id):
    for n in _NOTIFICATIONS:
        if n["id"] == notif_id:
            n["read"] = True
            return _ok()
    return _fail(404, "通知不存在")


@bp.route("/api/notification/read-all", methods=["POST"])
def admin_notification_read_all():
    for n in _NOTIFICATIONS:
        n["read"] = True
    return _ok()


@bp.route("/api/dashboard/summary", methods=["GET"])
def admin_dashboard_summary():
    return _ok({
        "totalUsers": len(_USERS),
        "activeUsers": sum(1 for u in _USERS if u["status"] == 1),
        "totalRoles": len(_ROLES),
        "totalNotifications": len(_NOTIFICATIONS),
        "unreadNotifications": sum(1 for n in _NOTIFICATIONS if not n["read"]),
        "todayAuditCount": 12,
        "sensorCount": 18,
        "systemStatus": "healthy",
    })


@bp.route("/api/export/users", methods=["GET"])
def admin_export_users():
    return _ok({"list": _USERS, "total": len(_USERS)})


PLUGIN = register_plugin(Plugin(
    name="admin_api",
    version="1.0.0",
    description="管理后台 API（登录/用户/角色/菜单/审计/通知/仪表盘）",
    blueprint=bp,
    routes=[
        "/api/auth/login",
        "/api/auth/menus",
        "/api/user/info",
        "/api/user/list",
        "/api/user",
        "/api/user/<id>",
        "/api/role/list",
        "/api/role",
        "/api/role/<id>",
        "/api/role/<id>/permissions",
        "/api/role/<id>/data-scope",
        "/api/permissions",
        "/api/menu/tree",
        "/api/notification/list",
        "/api/notification/unread-count",
        "/api/notification/<id>/read",
        "/api/notification/read-all",
        "/api/dashboard/summary",
        "/api/export/users",
    ],
))
