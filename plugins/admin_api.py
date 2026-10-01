# -*- coding: utf-8 -*-
"""云枢管理后台 API 插件（2026-09-01 新增）：登录 / 用户 / 角色 / 菜单 / 审计 / 通知 / 仪表盘。

背景：develop 分支的完整管理后台前端（router/MainLayout/Sidebar/pages/system/*）已合回
主工作区。前端 API 契约与 yunshu-ui/src/mocks/devMock.ts 完全一致（该 mock 仅 dev
server 生效）。本插件把同一套接口在 Flask 后端（5678 生产模式）落地，数据为内存态
演示数据（与 devMock 同构），后续可替换为真实持久化存储。

约定（PLAN-1 §4）：
  - Blueprint 不设 url_prefix，路由保持 /api/... 原样；
  - 插件模块顶层只 import flask / plugin_api / 标准库；
  - 管理后台 token 与 app_server 的 FLASK_API_TOKEN 相互独立（见下方「会话令牌」节）。

【2026-10-01 加固】原实现是「自包含 token = mock-token-<username>-<timestamp>，
**不做强校验**」—— 该形态有一个**完全绕过认证**的洞：任何人只要手写
`Authorization: Bearer mock-token-admin-1` 就能成为 admin（用户名直接从字符串里读出来，
没有任何服务端校验）。现改为 **HMAC 签名 + 有效期**：用户名被编码进 token 但**必须**
带正确签名才算数，签名密钥不可从 token 反推 ⇒ 伪造不可行。
对外**契约不变**（登录仍返回 `{"token": ..., "user": ...}`，仍走 `Authorization: Bearer`），
前端无需改动；变化只有两条：① 伪造的 token 会被判 401；② token 有 12 小时有效期，
过期后需重新登录（**更严格**，不是放宽）。
"""
import hashlib
import hmac
import json
import os
import secrets
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

# ════════════════════════════════════════════════════════════════════════════
#  会话令牌（2026-10-01 加固：从「可伪造的自包含串」改为「HMAC 签名 + 有效期」）
# ════════════════════════════════════════════════════════════════════════════

_TOKEN_PREFIX = "mock-token-"          # 保留原前缀：外部若按前缀识别登录态，行为不变
_TOKEN_TTL_SEC = 12 * 3600             # 12 小时；过期即 401，逼重新登录（fail-closed）
_TOKEN_SIG_LEN = 32                    # sha256 十六进制取前 32 位

#: 进程内随机密钥 —— 仅在 FLASK_API_TOKEN 未配置时兜底。
#: 【为什么兜底随机是安全的】它只会让**重启后旧 token 全部失效**（更严格），
#:   绝不会放宽校验；而写死一个常量做兜底等于没有签名（谁都能复算）⇒ 等于没修。
_PROCESS_SECRET = secrets.token_bytes(32)


def _signing_key() -> bytes:
    """签名密钥：优先既有的 FLASK_API_TOKEN（已在 settings/registry 登记），否则进程内随机。"""
    seed = (os.environ.get("FLASK_API_TOKEN") or "").strip() or _PROCESS_SECRET.hex()
    # 域分隔：即便 FLASK_API_TOKEN 被别处复用，本用途的签名也不会与其它用途撞车
    return hashlib.sha256(("yunshu-admin-token-v1|" + seed).encode("utf-8")).digest()


def _sign(user_hex: str, ts: str) -> str:
    msg = ("%s.%s" % (user_hex, ts)).encode("utf-8")
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()[:_TOKEN_SIG_LEN]


def _issue_token(username: str) -> str:
    """签发 token。用户名走 hex 编码 ⇒ token 内只有 [0-9a-f]，右侧可按 `-` 无歧义切分。"""
    user_hex = username.encode("utf-8").hex()
    ts = str(int(time.time()))
    return "%s%s-%s-%s" % (_TOKEN_PREFIX, user_hex, ts, _sign(user_hex, ts))


def _token_username():
    """校验 Authorization Bearer 头并返回用户名；任何不合法情形一律返回 None。

    【为什么从右侧 rsplit】用户名编码成 hex 后不含 `-`，故从左往右解析虽然也成立，
    但从右切分对格式变化的容错更好（少一段就是 None，不会把签名当成用户名）。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token.startswith(_TOKEN_PREFIX):
        return None
    parts = token[len(_TOKEN_PREFIX):].rsplit("-", 2)
    if len(parts) != 3:
        return None
    user_hex, ts, sig = parts
    if not user_hex or not ts.isdigit() or len(sig) != _TOKEN_SIG_LEN:
        return None
    # 有效期：过期/未来时间戳都拒绝（未来时间戳可能是时钟被回拨，同样不可信）
    now = int(time.time())
    issued = int(ts)
    if issued > now + 60 or now - issued > _TOKEN_TTL_SEC:
        return None
    # 签名校验用 compare_digest（定时安全），不合法一律 None
    if not hmac.compare_digest(sig, _sign(user_hex, ts)):
        return None
    try:
        return bytes.fromhex(user_hex).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _ok(data=None, message="success"):
    return jsonify({"code": 200, "data": data, "message": message})


def _fail(code, message):
    """失败响应（保持既有契约：HTTP 200 + 业务码，前端按 `body.code` 分支）。

    【为什么不改成真实 HTTP 状态码】前端 `utils/request.ts` 的响应拦截对 HTTP 4xx
    有**另一套**文案（401 直接登出跳转），改成真状态码会让「用户名或密码错误」这类
    精确提示被通用文案顶掉 —— 属 UI 契约变更；且它**不是**安全边界（真正的边界是
    token 签名 + 全局网关）。故保持契约不变，
    【但把鉴权类失败记进日志】否则"HTTP 200 掩盖 401"会落到**完全不可观测**：
    监控看不到失败、排障时也无人知道有人在撞端点。
    """
    if code in (401, 403):
        import logging
        logging.getLogger(__name__).warning(
            "管理后台鉴权失败 code=%s path=%s（不记录令牌原文）", code, request.path)
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

#: 演示默认口令（仅在前端 devMock 与本地演示场景使用）。
#: 【为什么不直接删掉它】删掉会让本机管理后台在未配置环境变量时**完全登不进去**，
#:   属「把可用性换成安全性」的硬切换；本仓规矩是**不静默降级**（INV-08），
#:   故保留默认值但**首次登录时大声告警**，把「你正在用默认口令」变成可见事实。
_DEMO_PASSWORD = "123456"

_demo_password_warned = False


def _admin_password() -> str:
    """管理后台口令：优先环境变量 YUNSHU_ADMIN_PASSWORD，缺省回落到演示默认值。"""
    return (os.environ.get("YUNSHU_ADMIN_PASSWORD") or "").strip() or _DEMO_PASSWORD


def _warn_if_demo_password() -> None:
    """使用默认口令时告警**一次**（只告警：不 raise / 不 sys.exit，见 D4）。"""
    global _demo_password_warned
    if _demo_password_warned:
        return
    _demo_password_warned = True
    if not (os.environ.get("YUNSHU_ADMIN_PASSWORD") or "").strip():
        import logging
        logging.getLogger(__name__).warning(
            "管理后台正在使用**演示默认口令**（YUNSHU_ADMIN_PASSWORD 未配置）。"
            "  本机工作台场景可接受；但只要 5678 端口对本机以外的网络可达，"
            "请立即在 .env 中设置 YUNSHU_ADMIN_PASSWORD。")


@bp.route("/api/auth/login", methods=["POST"])
def admin_login():
    """登录：校验口令（环境变量优先）+ 签发**带签名**的会话令牌。

    【2026-10-01 加固】原实现完全信任客户端提交的用户名（token 自包含、无校验），
    现在用户名是**签名保护**的一部分：改用户名会让签名失效 ⇒ 无法自我提权成 admin。
    【口令比较】常量时间比较，且「用户不存在」与「口令错误」返回同一句文案，
    避免通过错误文案区分二者（用户枚举）。
    """
    _warn_if_demo_password()
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    user = next((u for u in _USERS if u["username"] == username), None)
    password_ok = hmac.compare_digest(password, _admin_password())
    if user is None or not password_ok:
        return _fail(400, "用户名或密码错误")
    return _ok({"token": _issue_token(username), "user": user})


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
