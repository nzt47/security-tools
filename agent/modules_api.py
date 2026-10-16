"""云枢模块聚合 API（S2：拓扑 + 状态 + 统一干预）

对外提供 3 个端点：
- GET  /api/modules/topology              — 六域模块树 + 实时状态 + 指标（供拓扑视图）
- GET  /api/modules/<module_id>/detail    — 节点详情（状态/指标/可执行动作/近期审计）
- POST /api/modules/<module_id>/actions   — 统一干预入口（映射转发既有 API）

【不易】契约：
- 只做"只读状态聚合 + 写操作适配转发"，不新建业务逻辑、不绕过安全边界
- 转发目标一律是 ACTION_ROUTES 中已声明的既有 API（见 modules_registry.py）
- 高危动作（danger=high）后端强制 reason 非空（前端二次确认只是第一道，后端兜底）
- 紧急动作（emergency_*）action 参数为固定值（const:），前端无法篡改
- 干预操作全部写结构化审计日志（可被 log_system 采集回溯）

【变易】状态源采用 provider 注册表：默认内置 health/config/scheduler 三个
可独立运行的采集器；依赖 _Yunshu 等运行上下文的源（如 /api/sensors）由
app_server 接线时通过 register_status_provider() 注入；其余"已有真实 GET 路由"的
源由 register_client_status_providers() 用进程内 test_client 兜底（200 才算有数据）。
**未覆盖**的 api: 源仍显示离线 —— 这是有意的诚实口径，并由
tests/unit/test_modules_registry_selfcheck.py 守"不存在未覆盖的声明"。

【简易】复用 Flask test_client 进程内转发（无需网络/token 配置），
blueprint 视图函数与既有 app_server 路由互不干扰。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from flask import Blueprint, current_app, jsonify, request

# 【P1-front 第二批 · 2026-10-05】统一响应信封（阶段 2 / R3）。
# 该模块只依赖 flask/标准库，顶层 import 信封模块不引入循环。
from agent.api_envelope import ok as _ok

from agent.modules_registry import (
    ACTION_ROUTES,
    DOMAINS,
    ModuleNode,
    get_node,
    node_actions,
)
# 与既有写接口保持一致：FLASK_API_TOKEN 启用时干预端点需认证（未启用时放行）
from agent.server_auth import require_token

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

# ════════════════════════════════════════════════════════════
#  常量与全局状态
# ════════════════════════════════════════════════════════════

modules_bp = Blueprint("modules", __name__, url_prefix="/api/modules")

CONFIG_YAML = Path(__file__).resolve().parent.parent / "config.yaml"

# 状态采集 provider 注册表：{"api路径": callable() -> dict | None}
# 未注册的 api:xxx 状态源 → 节点显示离线（与探针"无数据不假满分"契约一致）
_status_providers: Dict[str, Callable[[], Optional[dict]]] = {}
_providers_lock = threading.Lock()

# API token 提供者（app_server 接线时注入，用于转发受 require_token 保护的内部路由）
_api_token_provider: Optional[Callable[[], Optional[str]]] = None

# 干预审计环形缓冲（内存保留最近 200 条，detail 面板回溯用）
_audit_history: deque = deque(maxlen=200)
_audit_lock = threading.Lock()

# 干预限流（模块级复用，REJECT 策略）
try:
    from agent.rate_limiter import RateLimiter, RateLimitStrategy
    _rate_limiter = RateLimiter(max_concurrent=30, strategy=RateLimitStrategy.REJECT)
except Exception:  # noqa: BLE001 - 限流不可用不阻断功能
    _rate_limiter = None

_TRACE_PREFIX = "modules_api"


def _trace_id() -> str:
    """生成 trace_id（结构化审计日志用）"""
    return uuid.uuid4().hex[:16]


def _audit(module_id: str, action: str, reason: str,
           ok: bool, detail: str, forwarded: str = "") -> str:
    """干预操作审计：结构化日志（log_system 可采集）+ 内存环形缓冲"""
    record = {
        "trace_id": _trace_id(),
        "module_name": _TRACE_PREFIX,
        "action": f"module_action.{action}",
        "module_id": module_id,
        "reason": reason,
        "ok": ok,
        "detail": detail,
        "forwarded": forwarded,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if ok:
        logger.info(json.dumps(record, ensure_ascii=False))
    else:
        logger.warning(json.dumps(record, ensure_ascii=False))
    with _audit_lock:
        _audit_history.append(record)
    return record["trace_id"]


def register_status_provider(api_path: str, fn: Callable[[], Optional[dict]]) -> None:
    """注册状态采集 provider（app_server 接线时注入真实数据源）

    Args:
        api_path: 注册表中 status_source 的 api 路径，如 "/api/sensors"
        fn: 无参采集函数，返回 dict（可用 available=False 表示无数据）或 None
    """
    with _providers_lock:
        _status_providers[api_path] = fn


def reset_status_providers() -> None:
    """清空 provider 注册表（测试隔离用）"""
    with _providers_lock:
        _status_providers.clear()


def register_modules_api(app, api_token_provider: Optional[Callable[[], Optional[str]]] = None):
    """注册模块聚合蓝图（app_server 接线入口）

    Args:
        app: Flask 实例
        api_token_provider: 返回 FLASK_API_TOKEN 的可调用对象（None=不注入 token 头）
    """
    global _api_token_provider
    _api_token_provider = api_token_provider
    app.register_blueprint(modules_bp)
    logger.info("[启动] 模块聚合 API 路由已注册 (/api/modules/*)")
    return modules_bp


# ════════════════════════════════════════════════════════════
#  状态采集（只读）
# ════════════════════════════════════════════════════════════

def _read_config_path(path: str) -> Optional[dict]:
    """读取 config.yaml 指定路径（点分隔），失败返回 None"""
    try:
        import yaml
        with open(CONFIG_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        cur = data
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        return {"value": cur}
    except Exception as e:  # noqa: BLE001 - 配置读取失败降级为无数据
        logger.warning("config 状态读取失败 %s: %s", path, e)
        return None


def _default_provider_health() -> Optional[dict]:
    """默认状态源：五层健康探针（独立运行，不依赖 _Yunshu）"""
    try:
        from agent.health.dashboard import get_probe_overview
        return get_probe_overview()
    except Exception as e:  # noqa: BLE001
        logger.debug("health provider 不可用: %s", e)
        return None


def _default_provider_scheduler() -> Optional[dict]:
    """默认状态源：定时任务列表（独立运行）"""
    try:
        from agent.system_tools import list_scheduled_tasks
        return {"tasks": list_scheduled_tasks()}
    except Exception as e:  # noqa: BLE001
        logger.debug("scheduler provider 不可用: %s", e)
        return None


# 内置默认 provider（可被注入覆盖）
_default_providers: Dict[str, Callable[[], Optional[dict]]] = {
    "/api/health": _default_provider_health,
    "/api/health/dashboard": _default_provider_health,
    "/api/scheduler/tasks": _default_provider_scheduler,
}


# ── 进程内 client 兜底 provider（2026-10-16）────────────────────────
# 【解决什么】拓扑图长期把 17 个节点画成"离线（无数据）"，实测其中 14 个对应的路由
#   其实是 200 —— 它们只是没有专用 provider。status_source 声明的是"状态该从哪取"，
#   而聚合器只认已注册的采集器；没注册就如实说"无数据"，于是拓扑图说了与事实不符的
#   "离线"（由 tests/unit/test_modules_registry_selfcheck.py 的接线覆盖守卫防复发）。
# 【怎么做】对**已有真实 GET 路由、不需要专用运行上下文**的源，统一用进程内 test_client
#   取一次：200 → 交给 _classify_status（信封 dict 无 value/overall 时按"运行中"）；
#   非 200 / 异常 → None（仍如实离线，不假满分），并留 WARNING 痕迹。
# 【为什么不再写 14 个专用函数】与统一干预入口 _forward 同源：复用真实视图
#   （含鉴权/信封/降级），不在聚合层重写业务逻辑。
CLIENT_STATUS_SOURCES: Dict[str, str] = {
    # 声明源 -> 实际 GET 的路径（通配声明取该片下最轻的真实路由）
    "/api/system-prompt": "/api/system-prompt",
    "/api/memory/overview": "/api/memory/overview",
    "/api/context/status": "/api/context/status",
    "/api/permission/status": "/api/permission/status",
    "/api/tools/health": "/api/tools/health",
    "/api/skills": "/api/skills",
    "/api/extensions/list": "/api/extensions/list",
    "/api/process/list": "/api/process/list",
    "/api/llm/instances": "/api/llm/instances",
    "/api/mcp/services": "/api/mcp/services",
    "/api/network-config": "/api/network-config",
    "/api/safety/alerts": "/api/safety/alerts",
    "/api/personality": "/api/personality",
    "/api/search-performance/status": "/api/search-performance/status",
    "/api/knowledge/*": "/api/knowledge/index",
    # 【已知非 200】日志看板未就绪时 /logs/api/stats 返回 503：仍登记，让它以
    # "非 200 ⇒ 无数据"如实离线并留痕，而不是靠"没注册"来碰巧离线。
    "/logs/api/stats": "/logs/api/stats",
}

#: 由 app_server 注入**专用** provider 的状态源（走 register_status_provider，不走 client 兜底）
INJECTED_STATUS_SOURCES: Tuple[str, ...] = ("/api/sensors", "/api/status", "/api/panorama")


def make_client_provider(api_path: str) -> Callable[[], Optional[dict]]:
    """造一个"进程内 GET api_path、200 才算有数据"的状态采集器。"""
    def _provider() -> Optional[dict]:
        try:
            client = current_app.test_client()
            headers: Dict[str, str] = {}
            token = _api_token_provider() if _api_token_provider else None
            if token:
                headers["X-API-Token"] = token
            resp = client.get(api_path, headers=headers)
        except Exception as e:  # noqa: BLE001 - 采集失败降级为离线（但必须留痕）
            logger.warning("[模块聚合] %s 采集异常: %s", api_path, e)
            return None
        if resp.status_code != 200:
            logger.warning("[模块聚合] %s 返回 %s，节点按离线处理", api_path, resp.status_code)
            return None
        return resp.get_json(silent=True)
    return _provider


def register_client_status_providers(sources: Optional[Dict[str, str]] = None) -> int:
    """为 CLIENT_STATUS_SOURCES 注册 client 采集器；返回注册条数（app_server 启动时调用）。"""
    mapping = CLIENT_STATUS_SOURCES if sources is None else sources
    for declared, fetch_path in mapping.items():
        register_status_provider(declared, make_client_provider(fetch_path))
    return len(mapping)


def _collect_status(source: str) -> dict:
    """按 status_source 声明采集节点状态

    Returns:
        {"available": bool, "data": dict|None, "source": str}
    """
    if source.startswith("config:"):
        path = source[len("config:"):]
        data = _read_config_path(path)
        return {"available": data is not None, "data": data, "source": source}

    if source.startswith("api:"):
        api_path = source[len("api:"):]
        with _providers_lock:
            fn = _status_providers.get(api_path) or _default_providers.get(api_path)
        if fn is None:
            return {"available": False, "data": None, "source": source}
        try:
            data = fn()
        except Exception as e:  # noqa: BLE001 - 单节点采集失败不阻断拓扑
            logger.warning("状态采集失败 %s: %s", api_path, e)
            return {"available": False, "data": None, "source": source}
        return {"available": data is not None, "data": data, "source": source}

    return {"available": False, "data": None, "source": source}


def _classify_status(collected: dict) -> dict:
    """归一化状态：健康/警告/故障/离线/未启用

    - 无数据 → offline（禁止假满分）
    - config 布尔值 False → disabled（未启用）
    - health overall 分数 → healthy(>=0.8) / warning(>=0.5) / fault(<0.5)
    - 数据为 list/其他类型（如传感器信息列表）→ 视为运行中
    """
    if not collected.get("available"):
        return {"status": "offline", "detail": "无数据"}
    data = collected.get("data")
    if not isinstance(data, dict):
        # 非 dict 载荷（list/标量）：有数据即可用，不假满分也不误报
        return {"status": "healthy", "detail": "运行中"}
    # config 布尔开关
    if "value" in data and isinstance(data["value"], bool):
        if not data["value"]:
            return {"status": "disabled", "detail": "未启用"}
        return {"status": "healthy", "detail": "已启用"}
    # 健康分数
    if "overall" in data and isinstance(data.get("overall"), (int, float)):
        score = float(data["overall"])
        if score >= 0.8:
            return {"status": "healthy", "detail": f"健康分 {score:.2f}"}
        if score >= 0.5:
            return {"status": "warning", "detail": f"健康分 {score:.2f}"}
        return {"status": "fault", "detail": f"健康分 {score:.2f}"}
    # 显式无数据标记
    if data.get("available") is False:
        return {"status": "offline", "detail": str(data.get("detail", "无数据"))}
    return {"status": "healthy", "detail": "运行中"}


def _pick_metrics(node: ModuleNode, collected: dict) -> List[dict]:
    """从状态源响应中按 node.metrics 键挑取指标（非 dict 载荷或缺键自动跳过）"""
    data = collected.get("data")
    if not isinstance(data, dict):
        return []
    result = []
    for key in node.metrics:
        if key in data:
            result.append({"key": key, "value": data[key]})
    return result


# ════════════════════════════════════════════════════════════
#  干预转发（写操作适配，不新建业务逻辑）
# ════════════════════════════════════════════════════════════

def _build_request_body(route, user_params: dict) -> dict:
    """按映射表构建转发请求体（const: 固定值优先，忽略前端同名入参）"""
    body = {}
    for api_field, source in route.params.items():
        if source.startswith("const:"):
            body[api_field] = source[len("const:"):]
        else:
            value = user_params.get(source)
            if value is not None:
                body[api_field] = value
    return body


def _forward(action_key: str, body: dict) -> dict:
    """进程内转发到既有 API（Flask test_client，无需网络与独立端口）"""
    route = ACTION_ROUTES[action_key]
    client = current_app.test_client()
    headers = {}
    token = _api_token_provider() if _api_token_provider else None
    if token:
        # require_token 同时接受 X-API-Token 头（见 app_server.require_token）
        headers["X-API-Token"] = token
    resp = client.post(route.url, json=body, headers=headers)
    data = resp.get_json(silent=True) or {}
    data.setdefault("_status_code", resp.status_code)
    return data


def _do_action(module_id: str, action: str, user_params: dict, reason: str) -> tuple:
    """执行干预动作：校验 → 限流 → 转发 → 审计"""
    node = get_node(module_id)
    if node is None:
        return {"ok": False, "error": f"未知模块: {module_id}"}, 404
    if action not in node.actions:
        return {"ok": False, "error": f"模块 {module_id} 未声明动作 {action}"}, 400
    route = ACTION_ROUTES.get(action)
    if route is None:
        return {"ok": False, "error": f"未注册动作: {action}"}, 500

    # 高危动作：后端强制 reason 非空（兜底，前端已二次确认）
    if route.danger == "high" and not (reason and reason.strip()):
        return {"ok": False, "error": "高危操作必须提供 reason（操作原因）"}, 400

    # 限流（REJECT 策略，超出返回 429）
    if _rate_limiter is not None:
        if not _rate_limiter.check(endpoint=f"modules.{action}"):
            return {"ok": False, "error": "干预操作过于频繁，请稍后再试"}, 429

    body = _build_request_body(route, user_params)
    try:
        result = _forward(action, body)
    except Exception as e:  # noqa: BLE001 - 转发异常不崩接口
        _audit(module_id, action, reason, ok=False, detail=f"转发异常: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"转发失败: {e}"}, 502

    ok = bool(result.get("ok", True)) and int(result.get("_status_code", 200)) < 400
    _audit(module_id, action, reason, ok=ok,
           detail=str(result)[:300], forwarded=route.url)
    return {
        "ok": ok,
        "module_id": module_id,
        "action": action,
        "forwarded": f"{route.method} {route.url}",
        "result": result,
        "status_code": int(result.get("_status_code", 200)),
    }, (200 if ok else 502)


# ════════════════════════════════════════════════════════════
#  REST 端点
# ════════════════════════════════════════════════════════════

@modules_bp.route("/topology")
def topology():
    """六域模块树 + 实时状态 + 指标（拓扑视图数据源）。

    【P1-front 第二批】载荷**一字未动**（仍是 `{generated_at, overall_health, domains}`），
    只是装进 `ok()` 的 data 并带上 `X-Envelope: v2` —— 前端据此可显式判断形态。
    消费方只有 `pages/hub/panorama/monitor.tsx` 一个文件（原 `module-list.tsx` 与其
    总览已于 2026-10-15 合并为同一页，仍是单一消费方）。
    """
    domains_out = []
    health_scores = []
    for domain in DOMAINS:
        nodes_out = []
        for node in domain.nodes:
            collected = _collect_status(node.status_source)
            state = _classify_status(collected)
            if state["status"] == "healthy" and "overall" in (collected.get("data") or {}):
                health_scores.append(float(collected["data"]["overall"]))
            nodes_out.append({
                "module_id": node.module_id,
                "name": node.name,
                "path": node.path,
                "type": node.type,
                "status": state["status"],
                "status_detail": state["detail"],
                "metrics": _pick_metrics(node, collected),
                "actions": [a["action"] for a in node_actions(node)],
                "danger": node.danger,
            })
        domains_out.append({
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "icon": domain.icon,
            "nodes": nodes_out,
        })
    return _ok({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "overall_health": round(sum(health_scores) / len(health_scores), 2) if health_scores else None,
        "domains": domains_out,
    })


@modules_bp.route("/<module_id>/detail")
def detail(module_id: str):
    """节点详情：状态/指标/可执行动作/近期审计"""
    node = get_node(module_id)
    if node is None:
        return jsonify({"error": f"未知模块: {module_id}"}), 404
    domain = next((d for d in DOMAINS if any(n.module_id == module_id for n in d.nodes)), None)
    collected = _collect_status(node.status_source)
    state = _classify_status(collected)
    with _audit_lock:
        recent = [r for r in _audit_history if r["module_id"] == module_id][-10:]
    return jsonify({
        "module_id": node.module_id,
        "name": node.name,
        "domain": domain.domain_name if domain else "",
        "path": node.path,
        "type": node.type,
        "description": node.description,
        "status": state["status"],
        "status_detail": state["detail"],
        "metrics": _pick_metrics(node, collected),
        "actions": node_actions(node),
        "recent_actions": recent,
    })


@modules_bp.route("/<module_id>/actions", methods=["POST"])
@require_token
def actions(module_id: str):
    """统一干预入口（写操作，FLASK_API_TOKEN 启用时需认证）"""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    reason = data.get("reason", "")
    params = data.get("params", {}) or {}
    if not action:
        return jsonify({"ok": False, "error": "缺少 action"}), 400
    payload, status = _do_action(module_id, action, params, reason)
    return jsonify(payload), status
