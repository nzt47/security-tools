"""app_server 认证与日志装饰器

从 app_server.py 提取，供主文件及路由模块共享。

【TASK-S4-01 身份层升级（Owner 裁定 A3「令牌 → 用户映射」，S2-02 遗留 #1）】
    既有：`require_token` 只比对**共享令牌**（`FLASK_API_TOKEN`），UI actor 只能降级为
    `ui:<remote_addr>`。本任务按裁定 A3 落地**每使用者独立令牌**：

      - 配置表 `CP_UI_TOKENS=<token>:<name>,...`（或 `CP_UI_TOKENS_FILE`）映射
        token → actor 名；命中即 `identity_source=token_map`（**权威**）；
      - `require_token` 接受**共享令牌**或**映射表内的独立令牌**；
      - 令牌校验成功后把身份写入审计上下文（`set_ui_actor`），使该请求的全部审计
        记录归因到真实 actor（与 S2-03 埋点同源，口径一致）；
      - **映射表为空时完全回退既有行为**：未配置任何令牌 ⇒ 不校验（与升级前逐字
        一致）；仅配置共享令牌 ⇒ 只认共享令牌。**新机制绝不导致后台无法审批**。

    【不做】完整 session 登录体系（A1）、不信任反代注入头（A2）。
    actor 解析层可替换（`agent/security/identity.py::set_resolver`），供 P5 升级。

【配置（.env / 环境变量）】
    FLASK_API_TOKEN      共享 API 令牌（既有；保持原语义）
    CP_UI_TOKENS         每使用者令牌映射表（裁定 A3）
    CP_UI_TOKENS_FILE    映射表文件路径（每行一条）

【安全纪律】
    令牌原文**绝不**进入日志/审计/异常消息；只出现 sha256 指纹（`tok_...`）。
"""

import functools
import logging
import os
import secrets
from typing import Any, Dict, Optional, Tuple

from flask import request, jsonify

from agent.security.identity import (
    SRC_TOKEN_MAP,
    ResolvedIdentity,
    extract_bearer_token,
    resolve_identity,
)

logger = logging.getLogger(__name__)

# ── API 令牌（**导入期取值**，保持既有语义；运行期以 `current_api_token()` 为准） ──
_API_TOKEN = os.environ.get("FLASK_API_TOKEN", "")
_API_TOKEN_ENABLED = bool(_API_TOKEN)

#: 身份来源口径：共享令牌（无用户区分）
SRC_SHARED_TOKEN = "shared_token"
#: 未配置任何令牌 ⇒ 不校验（既有行为）
SRC_NO_TOKEN_CONFIGURED = "no_token_configured"


def current_api_token() -> str:
    """当前共享令牌（运行期读环境变量，回退导入期取值）

    Why 运行期读：`.env` 由 `EnvConfigManager` 热重载（写入即更新 `os.environ`），
    导入期固化会让热更新失效。
    """
    return str(os.environ.get("FLASK_API_TOKEN", "") or _API_TOKEN or "")


def _bearer_or_header_token() -> str:
    """从请求取令牌原文（`Authorization: Bearer` 优先，其次 `X-API-Token`）"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return str(request.headers.get("X-API-Token", "") or "").strip()


def authorize_token(token: str) -> Tuple[bool, str, str]:
    """校验令牌 → (是否通过, actor, identity_source)

    规则（顺序即权限顺序）：
      1. 共享令牌匹配 ⇒ 通过（actor 留空，交由后续身份解析降级）；
      2. 映射表命中 ⇒ 通过 + **真实 actor**（`token_map`）；
      3. 二者皆未启用 ⇒ 通过（**既有行为**：未配置令牌即不校验）；
      4. 其余 ⇒ 拒绝。

    共享令牌的启用开关沿用**导入期** `_API_TOKEN_ENABLED`（与升级前逐字一致，
    含测试用 `monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)` 的旁路语义）；
    令牌取值本身运行期读环境变量，以支持 `.env` 热重载。
    """
    presented = str(token or "")
    shared = current_api_token() if _API_TOKEN_ENABLED else ""
    if shared and presented and secrets.compare_digest(presented, shared):
        return True, "", SRC_SHARED_TOKEN
    from agent.security.identity import current_token_map
    token_map = current_token_map()
    if presented and not token_map.empty:
        entry = token_map.lookup(presented)
        if entry is not None:
            return True, entry.actor, SRC_TOKEN_MAP
    if not shared and token_map.empty:
        # 未配置任何令牌：与升级前逐字一致（不校验）
        return True, "", SRC_NO_TOKEN_CONFIGURED
    return False, "", "denied"


def require_token(f):
    """需要 API 令牌认证的装饰器（支持共享令牌 + 每使用者独立令牌）"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        ok, actor, source = authorize_token(_bearer_or_header_token())
        if not ok:
            logger.warning(
                "[Auth] 令牌校验失败 path=%s source=%s（未记录令牌原文）",
                request.path, source)
            return jsonify({"error": "未授权：缺少或无效的 API 令牌"}), 401
        if actor:
            _bind_identity(actor, source)
        return f(*args, **kwargs)
    return decorated


def _bind_identity(actor: str, source: str) -> None:
    """把**已认证身份**暴露给本请求的后续代码（best-effort：失败不影响请求）

    【为什么只写 `flask.g`，而不写审计 ContextVar】
        `agent.audit.facade.set_ui_actor` 写的是 **ContextVar**，必须配一次复位，
        否则会泄漏到此后所有同线程请求（本任务全量回归实测：泄漏后
        `audit.facade.resolve_actor()` 把无 actor 的审计记录误归因到上一个请求的用户，
        2 例既有 `test_audit_facade` 用例因此失败）。而复位的唯一正确挂点是
        `teardown_request`（`after_this_request` 早于 `after_request`，会把 actor
        清在审计落账之前），Flask 3 又**禁止**在首个请求之后注册 teardown 钩子
        （实测报错：*The setup method 'teardown_request' can no longer be called on
        the application*）。故此处**刻意不写 ContextVar**：

        - 全局 UI 写路由的 actor 归因由 S2-02 的 `ui_middleware` 承担：其
          `before_request` 调 `resolve_ui_actor`（已统一走令牌映射表 ⇒ Bearer /
          `X-API-Token` 命中的真实 actor 自然生效），`teardown_request` 由它自己复位，
          生命周期闭合；
        - 路由内需要身份时读 `resolve_request_identity()`（本函数写入的 `g` 是最高
          优先来源）；审批动作即以该身份构造 `ActorContext`。

        回归守护：`test_s4_01_server_auth.py::test_authenticated_actor_is_exposed_to_routes`
        与 `test_no_ui_actor_contextvar_leak`。
    """
    try:
        from flask import g
        g._cp_identity = {"actor": actor, "identity_source": source}
    except Exception as e:  # noqa: BLE001 上下文绑定失败不得影响请求
        logger.debug("[Auth] 身份上下文绑定失败: %s", e)


def resolve_request_identity(*, session_id: str = "") -> ResolvedIdentity:
    """解析当前请求的执行体身份（**路由侧唯一入口**）

    优先级：**已认证令牌的 actor**（映射表命中）> 身份头/Cookie > 令牌指纹 >
    `ui:<remote_addr>` 降级。返回 `ResolvedIdentity`（含 `degraded` / `authority`），
    供审批矩阵与审计共同使用（口径一致）。
    """
    headers = dict(request.headers)
    cookies = dict(request.cookies)
    remote_addr = request.remote_addr or ""
    token = _bearer_or_header_token()
    claimed: Dict[str, Any] = {}
    try:
        from flask import g
        claimed = getattr(g, "_cp_identity", None) or {}
    except Exception:  # noqa: BLE001 无请求上下文
        claimed = {}
    actor = str(claimed.get("actor") or "")
    identity = resolve_identity(
        actor=actor, token=token, headers=headers, cookies=cookies,
        remote_addr=remote_addr, session_id=session_id)
    return identity


def log_request(show_body=True, show_response=True):
    """接口日志装饰器"""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            import time
            start_time = time.time()
            endpoint = f.__name__

            logs = []
            logs.append(f"[REQUEST] 接口: {endpoint}")
            logs.append(f"[REQUEST] 方法: {request.method}")
            logs.append(f"[REQUEST] 路径: {request.path}")
            logs.append(f"[REQUEST] 查询参数: {dict(request.args)}")

            if show_body and request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    body = request.get_json() if request.is_json else request.form.to_dict()
                    body_str = str(body)[:200] + ('...' if len(str(body)) > 200 else '')
                    logs.append(f"[REQUEST] 请求体: {body_str}")
                except Exception:
                    logs.append(f"[REQUEST] 请求体: 无法解析")

            try:
                response = f(*args, **kwargs)
                response_time = (time.time() - start_time) * 1000
                logs.append(f"[RESPONSE] 状态码: {response[1] if isinstance(response, tuple) else 200}")
                logs.append(f"[RESPONSE] 耗时: {response_time:.2f}ms")
                if show_response:
                    resp_body = response[0].get_data(as_text=True) if isinstance(response, tuple) and hasattr(response[0], 'get_data') else str(response)[:200]
                    logs.append(f"[RESPONSE] 内容: {resp_body[:200]}")
                logger.info("\n".join(logs))
                return response
            except Exception as e:
                response_time = (time.time() - start_time) * 1000
                logger.error("[ERROR] 接口 %s 异常: %s (耗时: %.2fms)", endpoint, e, response_time)
                raise
        return decorated
    return decorator


__all__ = [
    "require_token", "log_request", "authorize_token", "current_api_token",
    "resolve_request_identity", "extract_bearer_token",
    "SRC_SHARED_TOKEN", "SRC_NO_TOKEN_CONFIGURED",
]
