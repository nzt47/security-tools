"""api_gateway Flask 适配层

将 agent.api_gateway.ApiGateway 作为 Flask 的开放 API 网关接入（中间层模式，
不替换 Flask 原生路由）：

- 拦截 /api/open/* 请求：走 ApiGateway 的认证（X-API-Key / Bearer）、
  scope 校验、限流、配额、中间件与 handler 分发；
- 内部 API（/api/* 其余路径）保持 app_server 现有 require_token 认证，
  与网关双轨并存，互不干扰；
- 提供 /api/docs Swagger 文档与 API Key 管理端点（/api/open/keys）。

用法（app_server 接线）：
    from agent.api_gateway_flask import register_gateway
    register_gateway(app)
"""

import logging
import sys

from flask import request, jsonify

from agent.api_gateway import get_api_gateway

logger = logging.getLogger(__name__)

# 网关端点前缀（开放 API）
GATEWAY_PREFIX = "/api/open"


def _resolve_user_token_compat(auth_header):
    """从宿主模块取管理后台用户 token 解析函数（不触发重导入）

    Why：app_server 以 `python app_server.py` 运行时模块名是 __main__，
    若直接 `from app_server import _resolve_user_token` 会重新执行整个
    app_server 模块（重复初始化/注册，导致 /api/audit/logs 500）。
    从 sys.modules 已加载模块取函数引用，测试场景（import app_server）
    走 app_server 名，运行场景走 __main__ 名，均不触发重导入。
    """
    for mod_name in ("__main__", "app_server"):
        mod = sys.modules.get(mod_name)
        fn = getattr(mod, "_resolve_user_token", None) if mod else None
        if fn is not None:
            return fn(auth_header)
    return None, "missing"

# T8.4 第一批灰度开放的内部只读端点（GET，无 require_token，返回 JSON）
# 灰度原则：只开放只读/低危端点，写操作保持 FLASK_API_TOKEN 双轨不变
_INTERNAL_OPEN_BATCH1 = [
    ("/api/news", "GET", "read"),
    ("/api/search-performance/status", "GET", "read"),
    ("/api/search-performance/history", "GET", "read"),
    ("/api/search-performance/summary", "GET", "read"),
]

# T8.4 第二批灰度开放的内部只读端点（含新增 /api/audit/logs 审计日志查询）
# 选择标准：GET + 无 require_token + 返回 JSON（避免网关 Key 与 FLASK_API_TOKEN 双重认证冲突）
_INTERNAL_OPEN_BATCH2 = [
    ("/api/audit/logs", "GET", "read"),
    ("/api/schedules", "GET", "read"),
    ("/api/skills", "GET", "read"),
    ("/api/tasks", "GET", "read"),
]


def should_gateway_handle(path: str, method: str, gw) -> bool:
    """判定某请求是否应由网关处理（T8.4 扩展内部开放端点）

    - /api/open/* 网关端点：一律拦截（含 auth_required=False 的探活端点）
    - 内部端点：仅拦截显式开放（auth_required=True）端点；
      文档占位端点（auth_required=False）与其余内部路由放行走原生路由
    """
    if not path.startswith("/api/"):
        return False
    ep = gw._endpoints.get(f"{method.upper()}:{path}")
    if ep is None:
        return False
    if path.startswith(GATEWAY_PREFIX):
        return True
    return bool(ep.get("auth_required"))


def _find_view_func(app, path: str, method: str):
    """按 path+method 在 Flask 路由表中查找视图函数（不依赖端点名约定）"""
    for rule in app.url_map.iter_rules():
        if rule.rule == path and method.upper() in rule.methods:
            return app.view_functions.get(rule.endpoint)
    return None


def _wrap_internal_view(view_func):
    """把 Flask 内部视图函数包装为网关 handler（返回 dict 契约）

    Why：内部视图返回 Flask Response（jsonify），而网关 handle_request 契约
    要求 handler 返回 dict；此处调用原视图并解析 JSON 响应，保持原响应体。
    """
    def handler(req):
        resp = view_func()
        if hasattr(resp, "get_json"):
            data = resp.get_json() or {}
            out = dict(data)
            out.setdefault("status_code", resp.status_code)
            return out
        return {"ok": True, "data": resp, "status_code": 200}
    return handler


def register_internal_endpoints(app, gw, batch=None) -> int:
    """T8.4 内部只读端点逐批开放（网关认证接管）

    开放端点带 Key 可访问（无 Key 401 / 无权限 403 / 超额 429）；
    覆盖 _scan_internal_routes 登记的占位端点（auth_required False→True）。
    """
    count = 0
    for path, method, scope in (batch or _INTERNAL_OPEN_BATCH1):
        view_func = _find_view_func(app, path, method)
        if view_func is None:
            logger.warning("[APIGateway] 内部端点未找到视图，跳过开放: %s %s", method, path)
            continue
        gw.register_endpoint(
            path=path, method=method,
            handler=_wrap_internal_view(view_func),
            auth_required=True,
            scopes=[scope],
            summary=f"开放内部端点（网关认证）: {path}",
            description="T8.4 从内部 API 灰度开放的只读端点，Key 认证 + scope=read",
        )
        count += 1
    return count


def _scan_internal_routes(app, gw) -> int:
    """扫描 Flask 内部 API 路由，登记到网关（仅文档/统计用途）

    Why：/api/docs 需展示全量 API 清单（内部 + 开放）。内部路由登记为
    auth_required=False 的占位端点后，generate_swagger_doc 会自动包含；
    但 before_request 只拦截 /api/open/* 前缀，内部路由仍走 Flask 原生
    路由与 require_token 认证，行为不变（守【不易】）。
    """
    count = 0
    for rule in app.url_map.iter_rules():
        path = rule.rule
        # 只扫描 /api/*，排除网关前缀与页面/静态路由
        if not path.startswith("/api/") or path.startswith(GATEWAY_PREFIX):
            continue
        methods = {m for m in rule.methods if m not in ("HEAD", "OPTIONS")}
        if not methods:
            continue
        method = sorted(methods)[0]
        # 从视图函数 docstring 提取接口摘要
        summary = path
        vf = app.view_functions.get(rule.endpoint)
        if vf and getattr(vf, "__doc__", None):
            first_line = vf.__doc__.strip().splitlines()[0].strip()
            if first_line:
                summary = first_line[:100]
        gw.register_endpoint(
            path=path, method=method,
            handler=lambda req: None,  # 仅文档占位，不会被网关拦截调用
            auth_required=False,
            summary=summary,
            description=f"内部 API（Flask 原生路由，require_token 认证）: {path}",
        )
        count += 1
    return count


def register_gateway(app):
    """将 ApiGateway 挂载到 Flask 应用（中间层模式）

    Args:
        app: Flask 应用实例
    Returns:
        ApiGateway 实例（供外部继续注册端点/中间件）
    """
    gw = get_api_gateway()

    # ── 注册一个演示/探活端点，验证整条网关链路（认证+限流+handler） ──
    gw.register_endpoint(
        path=f"{GATEWAY_PREFIX}/echo",
        method="GET",
        handler=lambda req: {"ok": True, "message": "api_gateway alive", "path": req.path},
        auth_required=False,
        summary="网关存活探测",
        description="无需鉴权，返回网关工作状态",
    )

    @app.before_request
    def _gateway_before_request():
        """拦截网关已注册端点，委托给 ApiGateway 处理

        T8.4 扩展：除 /api/open/* 外，内部端点中显式开放（auth_required=True）
        的只读端点也由网关认证接管；文档占位端点（auth_required=False）与
        其余内部路由放行，走 Flask 原生 require_token（守【不易】双轨不变）。

        管理后台双轨认证（2026-08-20 修复审计接口 401）：内部开放端点同时被
        管理后台前端（用户登录 token）与开放 API 客户端（API Key）访问——
        - 带有效用户 token 的请求放行到 Flask 原生路由，由视图内部
          _token_error_response 校验（管理后台会话）；
        - 否则走网关 API Key 认证（T8.4 开放 API 契约不变）。
        /api/open/* 开放 API 一律走网关 Key 认证，不放行用户 token。
        """
        if not should_gateway_handle(request.path, request.method, gw):
            return None
        # 管理后台会话放行：仅内部开放端点生效（/api/open/* 保持网关认证）
        if not request.path.startswith(GATEWAY_PREFIX):
            # 【Why】经 sys.modules 取函数，不重导入 app_server（避免重复初始化 500）
            username, err = _resolve_user_token_compat(request.headers.get("Authorization", ""))
            if err is None and username:
                return None
        result = gw.handle_request(request)
        status = result.get("status_code", 200) if isinstance(result, dict) else 200
        if status != 200:
            return jsonify(result), status
        return jsonify(result)

    @app.route("/api/docs")
    def gateway_swagger_docs():
        """Swagger 文档（开放 API 清单）"""
        return jsonify(gw.generate_swagger_doc())

    @app.route("/api/open/keys", methods=["POST"])
    def gateway_create_key():
        """创建 API Key（仅返回一次明文，之后不可再查）

        T8.2 扩展：可绑定 tenant_id + role（RBAC），绑定后权限走角色权限表。
        """
        data = request.get_json(silent=True) or {}
        user_id = (data.get("user_id") or "").strip()
        if not user_id:
            return jsonify({"ok": False, "error": "缺少 user_id"}), 400
        key_info = gw._api_key_manager.create_key(
            user_id=user_id,
            description=data.get("description", ""),
            scopes=data.get("scopes"),
            tenant_id=data.get("tenant_id", ""),
            role=data.get("role", ""),
        )
        return jsonify({"ok": True, "api_key": key_info["key"], "user_id": user_id,
                        "tenant_id": key_info.get("tenant_id", ""),
                        "role": key_info.get("role", "")}), 201

    @app.route("/api/open/keys", methods=["GET"])
    def gateway_list_keys():
        """列出 API Key（脱敏，不含明文）"""
        user_id = request.args.get("user_id", "")
        keys = gw._api_key_manager.list_keys(user_id or None)
        for k in keys:
            if k.get("key"):
                k["key"] = "***" + k["key"][-4:]
        return jsonify({"ok": True, "keys": keys})

    @app.route("/api/open/stats")
    def gateway_stats():
        """网关运行统计"""
        return jsonify({"ok": True, "stats": gw.get_stats()})

    # 自动扫描内部 API 路由，生成全量 Swagger 文档（仅文档用途，不改变内部认证）
    try:
        scanned = _scan_internal_routes(app, gw)
        logger.info("[APIGateway] 已扫描 %d 个内部 API 路由并入文档", scanned)
    except Exception as e:  # noqa: BLE001 文档功能失败不阻断网关挂载
        logger.warning("[APIGateway] 内部路由扫描失败: %s", e)

    # T8.4 第一批+第二批灰度：开放只读内部端点（覆盖占位端点，网关认证接管）
    try:
        opened = register_internal_endpoints(app, gw, _INTERNAL_OPEN_BATCH1 + _INTERNAL_OPEN_BATCH2)
        logger.info("[APIGateway] T8.4 已开放 %d 个内部只读端点（两批）", opened)
    except Exception as e:  # noqa: BLE001 开放失败不阻断网关挂载
        logger.warning("[APIGateway] T8.4 内部端点开放失败: %s", e)

    logger.info("[APIGateway] 适配层已挂载: %s/*, /api/docs, /api/open/keys", GATEWAY_PREFIX)
    return gw
