"""云枢 API 网关 Flask 适配层（/api/open/* + /api/docs）。

【它是什么】agent/api_gateway.py 是网关**核心**（API Key 管理 / 配额 / 限流 / 访问日志 /
Swagger 生成），但它不依赖 Flask；本模块是把它接到 Flask app 上的**适配层**。

【为什么此前一直缺失 —— 审计 H-2】app_server.py:2174-2195 一直写着
    from agent.api_gateway_flask import register_gateway as reg_gateway
    reg_gateway(app)
但本模块并不在仓库里 ⇒ 每次启动都走 except ImportError 分支：**/api/open/* 与 /api/docs
整体不可用**，而进程照常报告健康、外部不可区分。这是审计 H-2 的一半（另一半是
agent/modules_registry.py 声明了本文件却不存在，被契约对拍工具检出）。

【设计要点，逐条对应 app_server 的既有注释与审计结论】
  1. **中间层模式**：只拦截 /api/open/* 前缀；内部 /api/* 的鉴权链路**完全不变**。
  2. **注册时机**：register_gateway 必须在全部内部路由注册完成后调用 —— app_server 把它
     放在文件末尾正是为此，因为 _scan_internal_routes 要遍历 app.url_map 才能生成完整文档。
  3. **不绕过安全边界**（关键）：内部路由只被登记进网关用于**生成文档**，其 handler 是一个
     不可调用的占位；开放端点必须在 /api/open/ 前缀下**显式注册**才会被网关分发。
     因此「扫描内部路由」不会顺带把内部 API 变成公开 API。
  4. **默认不开放任何端点**：开放面默认是空的（fail-closed）。要让某个能力对外，
     必须显式调用 register_open_endpoint()，或通过环境变量
     YUNSHU_OPEN_API_ENDPOINTS（形如 "GET /api/status,POST /api/chat"）逐条登记。
     【为什么不提供「整棵 /api/* 一键开放」】那会绕过内部 require_token，等于把
     审计 H-5 的形态放大成整个 API 面 —— 方案第 6.1 节「Schema 泄露即攻击面暴露」说的就是这件事。
  5. **失败不阻断**：本模块任何一步异常都不应让 app 起不来（app_server 侧已有兜底，
     这里也不再向外抛 ImportError 以外的东西）。

路由：
  ANY /api/open/<path:subpath>   开放端点入口（网关统一做 鉴权 → 限流 → 配额 → 分发）
  GET /api/docs                  OpenAPI 3.0 文档（默认覆盖全部内部路由 + 已开放端点）

【/api/docs 的可见性】它**不在** CP_API_AUTH_ALLOW 里 ⇒ 默认受全局闸门保护，
未带令牌访问会 401。若某天要把它对外开放，请先想清楚方案第 6.1 节那条
「OpenAPI 会列出**所有**接口」的后果。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from flask import jsonify, request

logger = logging.getLogger(__name__)

__all__ = ["register_gateway", "register_open_endpoint", "open_endpoints"]

OPEN_PREFIX = "/api/open"

#: 已登记开放端点："METHOD:path" -> {"handler": callable, "summary": str}
_open_endpoints: Dict[str, Dict[str, Any]] = {}


def _stub_handler(_request):
    """内部路由的**文档占位** handler —— 刻意不可调用。

    【为什么不用真转发】转发要么带上内部令牌（等于绕过内部鉴权边界），要么不带
    （必然 401）。两者都不对，故内部路由只进文档、不进分发面。
    """
    raise RuntimeError("内部路由仅用于文档登记，不可经 /api/open/* 调用")


def _scan_internal_routes(app, gateway) -> int:
    """把 app.url_map 里的内部路由登记进网关**用于文档**，返回登记条数。

    只登记 /api/ 前缀、且排除 /api/open/*（那是本适配层自己的前缀）。
    """
    count = 0
    seen = set()
    for rule in app.url_map.iter_rules():
        path = str(rule.rule)
        if not path.startswith("/api/") or path.startswith(OPEN_PREFIX):
            continue
        for method in sorted(set(rule.methods or ()) - {"HEAD", "OPTIONS"}):
            key = method + ":" + path
            if key in seen:
                continue
            seen.add(key)
            gateway.register_endpoint(
                path, method, _stub_handler,
                auth_required=True,
                summary="内部路由（%s）" % (rule.endpoint or ""),
                description=("内部 API，经全局鉴权闸门保护；**不**通过 /api/open/ 暴露。"
                             "此条目仅为使 /api/docs 完整。"),
            )
            count += 1
    return count


def register_open_endpoint(method: str, path: str, handler: Callable,
                           summary: str = "", auth_required: bool = True) -> None:
    """显式登记一个**对外开放**的端点（必须位于 /api/open/ 前缀下）。

    Args:
        method: HTTP 方法，如 "GET"。
        path: 开放路径，必须形如 /api/open/xxx（前缀不符直接拒绝，避免误把内部路径登记进来）。
        handler: 处理函数，签名 handler(request) -> dict（可含 "status_code"）。
        auth_required: 是否需要 API Key（默认 True；设 False 请想清楚无鉴权暴露的后果）。

    Raises:
        ValueError: path 不在 /api/open/ 前缀下 —— 这是**刻意**的硬检查。
    """
    if not str(path).startswith(OPEN_PREFIX + "/"):
        raise ValueError(
            "开放端点必须位于 " + OPEN_PREFIX + "/ 前缀下，收到: " + str(path) +
            "（该检查刻意存在：防止把内部路由误登记成公开端点）"
        )
    key = method.upper() + ":" + path
    _open_endpoints[key] = {"handler": handler, "summary": summary}
    gateway = _gateway_ref
    if gateway is not None:
        gateway.register_endpoint(path, method.upper(), handler,
                                  auth_required=auth_required, summary=summary)
    logger.info("[APIGateway] 已登记开放端点 %s %s", method.upper(), path)


#: register_gateway 时记下的网关实例（供运行期显式登记开放端点使用）
_gateway_ref = None


def _parse_env_endpoints(raw: str) -> List[str]:
    """"GET /api/x,POST /api/y" -> ["GET /api/x", "POST /api/y"]（忽略空白与非法项）。"""
    out = []
    for item in str(raw or "").split(","):
        parts = item.strip().split()
        if len(parts) == 2 and parts[0].isalpha():
            out.append(parts[0].upper() + " " + parts[1])
    return out


def open_endpoints() -> List[str]:
    """当前已登记的开放端点（"METHOD path"），供诊断/测试读取。"""
    return sorted(k.replace(":", " ", 1) for k in _open_endpoints)


def register_gateway(app) -> Dict[str, Any]:
    """把 API 网关挂到 Flask app 上（app_server 在**全部内部路由注册完成后**调用）。

    Returns:
        {"internal_scanned": int, "open_declared": int, "open_prefix": str}

    Raises:
        ImportError: 仅当网关核心不可用时（由 app_server 的 except ImportError 处理）。
    """
    global _gateway_ref
    from agent.api_gateway import get_api_gateway  # 延迟导入：本模块被 app_server 末尾才调用

    gateway = get_api_gateway()
    _gateway_ref = gateway

    scanned = _scan_internal_routes(app, gateway)

    # 环境变量声明的开放端点（默认空 ⇒ 默认不开放任何能力，fail-closed）
    declared = 0
    for spec in _parse_env_endpoints(os.environ.get("YUNSHU_OPEN_API_ENDPOINTS", "")):
        method, path = spec.split(" ", 1)
        if not path.startswith(OPEN_PREFIX + "/"):
            logger.warning("[APIGateway] 忽略非开放前缀的声明项: %s", spec)
            continue
        register_open_endpoint(method, path, _make_bridge_handler(path), summary="环境变量声明")
        declared += 1

    @app.route(OPEN_PREFIX + "/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def _api_open_entry(subpath):  # noqa: ANN001
        """开放端点统一入口：全部交给网关（鉴权 → 限流 → 配额 → 分发）。

        【为什么不在本函数里做鉴权】那是网关核心 handle_request 的职责；重复实现会导致
        两处口径漂移（本仓已有过多份鉴权包裹器的前车之鉴，审计 M-40）。
        """
        result = gateway.handle_request(request)
        status = int(result.get("status_code", 200) or 200)
        return jsonify(result), status

    @app.route("/api/docs", methods=["GET"])
    def _api_docs():
        """OpenAPI 3.0 文档（覆盖全部内部路由 + 已开放端点）。"""
        return jsonify(gateway.generate_swagger_doc())

    logger.info(
        "[APIGateway] 适配层已挂载：文档登记内部路由 %d 条、开放端点 %d 条（%s/*）",
        scanned, declared, OPEN_PREFIX,
    )
    return {"internal_scanned": scanned, "open_declared": declared, "open_prefix": OPEN_PREFIX}


def _make_bridge_handler(open_path: str) -> Callable:
    """环境变量声明的开放端点用的 handler：**明确拒绝**并提示正确做法。

    【为什么不做真转发】转发到内部路由需要带上内部令牌，那就绕过了内部鉴权边界；
    不带又必然 401。故这里返回 501 并说明「请用 register_open_endpoint 提供真实 handler」，
    而不是给一个看起来能用、实则绕过安全边界的转发。
    """
    def _handler(_request):
        return {
            "error": "not_implemented",
            "detail": ("开放端点 " + open_path + " 已声明但未提供 handler。"
                       "请用 agent.api_gateway_flask.register_open_endpoint(method, path, handler) "
                       "提供真实实现；**不提供**自动转发，因为那会绕过内部 require_token。"),
            "status_code": 501,
        }
    return _handler
