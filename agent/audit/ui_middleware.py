"""UI 写路由统一审计包装（P7.2-24 审计平权：UI 写操作与 Agent 同表）

【任务定位】
    v7.2 P7.2-24：**UI 操作日志与 Agent 操作日志必须同表**——否则攻击者走管理
    后台即绕过治理。本模块把「UI 写路由」统一包一层，落进 `agent/audit/chain.py`
    的同一张链式审计表（`audit_chain`，`source="ui"`）。

【两条接线，覆盖互补】
    1. **全局写路由包装**（默认开启）：`install_flask_audit(app)` 注册
       `before_request` / `after_request` / `teardown_request`，凡 POST/PUT/PATCH/DELETE
       一律落账——**审计平权由构造保证**，新增路由无需逐个改造。
    2. **语义化装饰器**：`@audit_action("skill.delete", subject_arg="skill_id")` 给关键
       写路由（技能删除/发布、审批、设置写）可读的 action 名与 subject；被显式审计过的
       请求，全局包装不再重复记录（`g._audit_explicit` 去重）。

【身份（诚实口径）】
    本仓库当前**没有**登录用户概念（`agent/server_auth.py::require_token` 仅比对共享
    令牌，无 session / current_user）。因此 actor 按下列顺序解析，并**如实记录来源**
    （payload.actor_source / payload.identity_source），绝不臆造用户名：
      0. **令牌映射表**（S4-01 裁定 A3）：`CP_UI_TOKENS` 命中 → 真实 actor 名，
         来源 `token_map`（**权威**；权威度 `authoritative`）；
      1. 显式传入 actor（路由自身已知的操作者）；
      2. 请求头 `X-Audit-Actor` / `X-User` / `X-Username` / `X-Operator`；
      3. Cookie 中的 `user` / `username`（存在时）；
      4. `Authorization: Bearer <token>` → **令牌指纹**（sha256 前 12 位，不落令牌原文）；
      5. 降级：`ui:<remote_addr>`（来源标注 `remote_addr`）。
    2–5 均为**降级路径**（`identity_authority=degraded`）；映射表为空时行为与
    S2-02 逐字一致（新机制不得导致后台不可用）。

    S4-01 起解析统一走 `agent/security/identity.py::resolve_identity`
    （审计 / 埋点 / 审批共用同一口径，S2-03 #13 收口）。

【PII 口径（S4-01 裁定 B）】
    认证 IP **原始值不落盘**：链上只写 `actor_ip_masked`（`10.0.xxx.xxx`）与
    `actor_ip_hash`（HMAC-SHA256，密钥经 `CP_IP_HMAC_KEY` / SecretStore；
    **无密钥 → 显式标注 `degraded_no_key`，绝不退化为写原文**）。

【环境开关】
    AUDIT_UI_ENABLED          UI 写路由审计总开关，默认 1
    AUDIT_UI_SKIP_PREFIXES    额外跳过的路径前缀（逗号分隔）
    AUDIT_UI_MAX_BODY_BYTES   参与 body_hash 的最大请求体字节数，默认 1048576
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.audit.chain import SOURCE_UI
from agent.audit.facade import audit as default_facade
from agent.audit.facade import reset_ui_actor, set_ui_actor
from agent.security.identity import (
    ACTOR_HEADERS,
    COOKIE_KEYS,
    resolve_identity,
    token_fingerprint,
)
from agent.security.pii import ip_pii_fields

logger = logging.getLogger("agent.audit.ui_middleware")

_ENV_ENABLED = "AUDIT_UI_ENABLED"
_ENV_SKIP_PREFIXES = "AUDIT_UI_SKIP_PREFIXES"
_ENV_MAX_BODY = "AUDIT_UI_MAX_BODY_BYTES"

#: 计入审计的写方法（GET/HEAD/OPTIONS 为读，不产生审计记录）
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: 默认跳过的路径前缀（静态资源/健康检查/指标/本审计自身读接口）
DEFAULT_SKIP_PREFIXES: Tuple[str, ...] = (
    "/static", "/static-assets", "/favicon", "/health", "/api/health",
    "/metrics", "/api/audit",
)

#: 身份来源头（按序优先）——与 `agent.security.identity.ACTOR_HEADERS` 同源
_ACTOR_HEADERS = ACTOR_HEADERS
_COOKIE_KEYS = COOKIE_KEYS


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def _extra_skip_prefixes() -> List[str]:
    raw = os.getenv(_ENV_SKIP_PREFIXES, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _max_body_bytes() -> int:
    try:
        return max(0, int(os.getenv(_ENV_MAX_BODY, "1048576")))
    except ValueError:
        return 1048576


def resolve_ui_actor(*, headers: Optional[Dict[str, str]] = None,
                     cookies: Optional[Dict[str, str]] = None,
                     remote_addr: str = "") -> Tuple[str, str]:
    """解析 UI 操作者 → (actor, actor_source)

    解析统一走 `agent.security.identity.resolve_identity`（S4-01 裁定 A3）：
    映射表（`CP_UI_TOKENS`）命中 → 真实 actor + `token_map`；未命中 / 表为空 →
    **完全回退** S2-02 的既有降级链（头 → Cookie → 令牌指纹 → `ui:<remote_addr>`），
    返回值与历史实现逐字一致。

    需要权威度 / 执行体类型 / PII 的调用方请直接用
    `agent.security.identity.resolve_identity`（返回 `ResolvedIdentity`）。
    """
    identity = resolve_identity(headers=headers, cookies=cookies,
                                remote_addr=remote_addr)
    return identity.actor, identity.identity_source


def identity_facts(*, headers: Optional[Dict[str, str]] = None,
                   cookies: Optional[Dict[str, str]] = None,
                   remote_addr: str = "") -> Dict[str, Any]:
    """身份 + PII 叶子字段（**落盘用**；原始 IP 不在其中）

    返回键：`identity_source` / `identity_authority` / `identity_degraded` /
    `actor_type` / `actor_ip_masked` / `actor_ip_hash`（有密钥时）/
    `actor_ip_hash_status`。
    """
    identity = resolve_identity(headers=headers, cookies=cookies,
                                remote_addr=remote_addr)
    facts: Dict[str, Any] = dict(identity.to_audit_fields())
    facts["identity_source"] = identity.identity_source
    if remote_addr:
        facts.update(ip_pii_fields(remote_addr))
    return facts


def action_from_request(method: str, path: str, endpoint: str = "") -> str:
    """由方法/路径/端点推导语义化 action（`ui.<endpoint>.<verb>`）"""
    verb = {
        "POST": "post", "PUT": "put", "PATCH": "patch", "DELETE": "delete",
    }.get(str(method).upper(), str(method).lower())
    ep = (endpoint or "").strip()
    if not ep:
        # 无端点（404 等）：退化为路径片段
        ep = "_".join([p for p in str(path).strip("/").split("/")
                       if p and not p.startswith("<")][:3]) or "unmatched"
    return f"ui.{ep}.{verb}"


def _status_from_code(status_code: int) -> str:
    """HTTP 状态码 → 审计结果状态（4xx=rejected / 5xx=error / 其余 ok）"""
    try:
        code = int(status_code or 0)
    except (TypeError, ValueError):
        return "unknown"
    if code >= 500:
        return "error"
    if code >= 400:
        return "rejected"
    return "ok"


def _response_status_code(result: Any) -> int:
    """从视图返回值提取 HTTP 状态码

    兼容 Flask 三种返回形态：`Response` 对象、`(body, status)` 元组、裸 body。
    """
    if isinstance(result, (tuple, list)):
        for item in result[1:]:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                return int(item)
            if isinstance(item, str):
                from werkzeug.http import parse_options_header  # noqa: F401
                try:
                    from werkzeug.wrappers import Response as WResponse
                    return int(WResponse(status=item).status_code)
                except Exception:  # noqa: BLE001 非标准状态串
                    continue
        return 200
    return int(getattr(result, "status_code", 0) or 0)


class UIAuditRecorder:
    """Flask 写路由统一审计记录器（同表：source="ui"）

    用法::

        from agent.audit.ui_middleware import install_flask_audit
        install_flask_audit(app)          # 紧跟 app = Flask(...) 之后

    Args:
        facade: 审计门面（默认进程级 `agent.audit.facade.audit`）。
        skip_prefixes: 覆盖默认跳过前缀。
        include_methods: 计入审计的 HTTP 方法。
        enabled: 总开关（None → 环境变量 AUDIT_UI_ENABLED）。
        action_resolver: 自定义 action 推导（默认 `action_from_request`）。
    """

    def __init__(self, facade: Any = None, *,
                 skip_prefixes: Optional[Sequence[str]] = None,
                 include_methods: Iterable[str] = WRITE_METHODS,
                 enabled: Optional[bool] = None,
                 action_resolver: Optional[Callable[..., str]] = None,
                 max_body_bytes: Optional[int] = None):
        self._facade = facade or default_facade
        self._skip = tuple(skip_prefixes) if skip_prefixes is not None else DEFAULT_SKIP_PREFIXES
        self._methods = tuple(m.upper() for m in include_methods)
        self._enabled = _env_flag(_ENV_ENABLED) if enabled is None else bool(enabled)
        self._action_resolver = action_resolver or action_from_request
        self._max_body = _max_body_bytes() if max_body_bytes is None else int(max_body_bytes)
        self.registered = False
        self._recorded_count = 0
        self._skipped_count = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def recorded_count(self) -> int:
        return self._recorded_count

    @property
    def skipped_count(self) -> int:
        return self._skipped_count

    # ── 安装 ────────────────────────────────────────────────

    def register(self, app: Any) -> "UIAuditRecorder":
        """在 Flask app 上注册三个请求钩子（幂等：重复注册只生效一次）"""
        if self.registered:
            return self
        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)
        self.registered = True
        logger.info("UI 写路由审计已安装（source=%s，methods=%s）",
                    SOURCE_UI, ",".join(self._methods))
        return self

    # ── 钩子 ────────────────────────────────────────────────

    def _should_audit(self, method: str, path: str) -> bool:
        if not self._enabled:
            return False
        if str(method).upper() not in self._methods:
            return False
        p = str(path or "")
        for prefix in tuple(self._skip) + tuple(_extra_skip_prefixes()):
            if p.startswith(prefix):
                return False
        return True

    def _before_request(self) -> None:
        """采样请求（方法/路径/身份/请求体指纹）+ 绑定 UI 操作者上下文"""
        try:
            from flask import g, request
        except Exception:  # noqa: BLE001 非 Flask 环境（单测直接调用）→ 跳过
            return
        try:
            method, path = request.method, request.path
            if not self._should_audit(method, path):
                self._skipped_count += 1
                return
            actor, actor_source = resolve_ui_actor(
                headers=dict(request.headers), cookies=dict(request.cookies),
                remote_addr=request.remote_addr or "")
            remote_addr = request.remote_addr or ""
            g._audit_ui = {
                "started": time.time(),
                "method": method,
                "path": path,
                "endpoint": str(request.endpoint or ""),
                "actor": actor,
                "identity_source": actor_source,
                "body_hash": self._body_hash(request),
                "body_bytes": self._body_len(request),
                "query_keys": sorted(list(request.args.keys()))[:50],
                # PII（裁定 B）：只留叶子字段（掩码 + HMAC），**原始 IP 不落盘**
                "ip_facts": identity_facts(headers=dict(request.headers),
                                           cookies=dict(request.cookies),
                                           remote_addr=remote_addr),
            }
            g._audit_recorded = False
            g._audit_explicit = False
            g._audit_tokens = set_ui_actor(actor, endpoint=request.endpoint or "",
                                           actor_source=actor_source)
        except Exception as e:  # noqa: BLE001 审计采样失败不得影响请求
            logger.debug("UI 审计采样失败: %s", e)

    def _after_request(self, response: Any) -> Any:
        """正常返回路径：落账（状态码 + 耗时 + 请求体指纹）

        注：Flask 在 view 抛异常时仍会走 `finalize_request` → 本钩子照样执行，
        故 5xx 会以 `status=error` 落账；`teardown_request` 仅兜底「本钩子未跑到」
        的极端情形（如中间件自身异常）。
        """
        try:
            from flask import g, request
            info = getattr(g, "_audit_ui", None)
            if info and not getattr(g, "_audit_explicit", False):
                code = int(getattr(response, "status_code", 0) or 0)
                self._record(info, request, status_code=code,
                             status=_status_from_code(code))
                g._audit_recorded = True
        except Exception as e:  # noqa: BLE001
            logger.debug("UI 审计落账失败: %s", e)
        return response

    def _teardown_request(self, exc: Any) -> None:
        """异常/未落账路径兜底落账 + 还原 UI 操作者上下文"""
        try:
            from flask import g, request
            info = getattr(g, "_audit_ui", None)
            if exc is not None and info and not getattr(g, "_audit_recorded", False) \
                    and not getattr(g, "_audit_explicit", False):
                self._record(info, request, status_code=500, status="exception",
                             extra={"error_type": type(exc).__name__})
                g._audit_recorded = True
            tokens = getattr(g, "_audit_tokens", None)
            if tokens is not None:
                reset_ui_actor(tokens)
                g._audit_tokens = None
        except Exception as e:  # noqa: BLE001
            logger.debug("UI 审计 teardown 失败: %s", e)

    # ── 记录 ────────────────────────────────────────────────

    def _record(self, info: Dict[str, Any], request: Any, *, status_code: int,
                status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        action = self._action_resolver(info.get("method", ""), info.get("path", ""),
                                       info.get("endpoint", ""))
        facts: Dict[str, Any] = {
            "method": info.get("method"),
            "path": info.get("path"),
            "endpoint": info.get("endpoint"),
            "status_code": int(status_code or 0),
            "duration_ms": round((time.time() - info.get("started", time.time())) * 1000, 3),
            "body_hash": info.get("body_hash"),
            "body_bytes": info.get("body_bytes"),
            "query_keys": info.get("query_keys"),
            # 请求侧身份解析来源（与门面的 actor_source 区分：后者标「谁给的 actor」）
            "identity_source": info.get("identity_source"),
            "audit_scope": "ui_write_route",
        }
        # 身份权威度 + PII 叶子字段（裁定 A3/B；**原始 IP 不落盘**）
        facts.update(info.get("ip_facts") or {})
        if extra:
            facts.update(extra)
        if not status:
            status = _status_from_code(status_code)
        entry = self._facade.record(
            action, actor=info.get("actor"), subject=f"ui:{info.get('path')}",
            extra=facts, source=SOURCE_UI, status=status)
        if entry is not None:
            self._recorded_count += 1

    @staticmethod
    def _body_len(request: Any) -> int:
        try:
            return int(request.content_length or 0)
        except Exception:  # noqa: BLE001
            return 0

    def _body_hash(self, request: Any) -> str:
        """请求体指纹：只记 sha256 与字节数，**不落原文**（避免密钥/密码入链）

        超过 `max_body_bytes` 或多部分表单（文件上传）时跳过，避免内存与敏感面膨胀。
        """
        try:
            length = int(request.content_length or 0)
            ctype = str(request.content_type or "")
            if length <= 0 or length > self._max_body:
                return "" if length <= 0 else "(skipped:large)"
            if "multipart/form-data" in ctype:
                return "(skipped:multipart)"
            data = request.get_data(cache=True)
            return hashlib.sha256(data).hexdigest() if data else ""
        except Exception:  # noqa: BLE001
            return ""


def install_flask_audit(app: Any, recorder: Optional[UIAuditRecorder] = None,
                        **kwargs: Any) -> UIAuditRecorder:
    """在 Flask app 上安装写路由审计（幂等；已安装过则复用）"""
    rec = recorder or UIAuditRecorder(**kwargs)
    rec.register(app)
    if rec not in _recorders:
        _recorders.append(rec)
    return rec


def audit_action(action: str, *, subject: Optional[str] = None,
                 subject_arg: Optional[str] = None,
                 payload_keys: Sequence[str] = (),
                 view_args_keys: Sequence[str] = ()) -> Callable:
    """关键写路由的语义化审计装饰器（UI 侧显式动作名 + subject）

    用法::

        @app.route("/api/skills-mgmt/<skill_id>", methods=["DELETE"])
        @audit_action("skill.delete", subject_arg="skill_id")
        def delete_skill(skill_id): ...

    Args:
        action: 语义化动作名（如 `skill.delete` / `approval.approve` / `config.write`）。
        subject: 固定 subject；None 时用 `subject_arg` 或请求路径。
        subject_arg: 视图 kwargs 中作为 subject 的参数名（自动加 `type:` 前缀由调用方决定）。
        payload_keys: 从 JSON/表单请求体提取的叶子字段名（值会脱敏）。
        view_args_keys: 从视图 kwargs 提取的叶子字段名。

    被装饰的视图落账后置 `g._audit_explicit=True`，全局包装不再重复记录同一请求。

    落账时机：**先执行视图、再按结果落账**（追加即链，事后无法补写结果字段），
    因此成功记 `status=ok`+状态码、4xx 记 `rejected`、5xx 记 `error`；视图抛异常时
    记 `status=exception`（含 error_type）后原样抛出。仅**写方法**
    （POST/PUT/PATCH/DELETE）落账；同一路由兼挂 GET 时读请求不产生审计。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _is_write_request():
                return func(*args, **kwargs)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 异常也要留痕，然后原样抛出
                try:
                    _emit_explicit_audit(action, subject, subject_arg, payload_keys,
                                         view_args_keys, kwargs, status="exception",
                                         extra={"error_type": type(exc).__name__})
                except Exception as e:  # noqa: BLE001 审计失败不得影响异常传播
                    logger.debug("显式审计落账失败（action=%s）: %s", action, e)
                raise
            try:
                code = _response_status_code(result)
                _emit_explicit_audit(action, subject, subject_arg, payload_keys,
                                     view_args_keys, kwargs,
                                     status=_status_from_code(code),
                                     extra={"status_code": code} if code else None)
            except Exception as e:  # noqa: BLE001 审计失败不得影响业务返回
                logger.debug("显式审计落账失败（action=%s）: %s", action, e)
            return result
        return wrapper
    return decorator


def _is_write_request() -> bool:
    """当前请求是否为写方法（非 Flask 上下文 → True，允许离线直接调用落账）"""
    try:
        from flask import request
        return str(request.method).upper() in WRITE_METHODS
    except Exception:  # noqa: BLE001 无请求上下文
        return True


def _emit_explicit_audit(action: str, subject: Optional[str],
                         subject_arg: Optional[str],
                         payload_keys: Sequence[str],
                         view_args_keys: Sequence[str],
                         view_kwargs: Dict[str, Any], *,
                         status: str = "",
                         extra: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """执行显式审计落账（Flask 请求上下文可选：无上下文时也能落账）

    请求事实经 `extra` 落账（顶层可查、经脱敏），故 action/subject/字段与全局包装
    产出的记录结构一致。
    """
    from agent.audit.facade import get_ui_context

    subj = subject
    if subj is None and subject_arg:
        val = view_kwargs.get(subject_arg)
        if val is not None:
            subj = str(val)
    facts: Dict[str, Any] = {"audit_scope": "ui_write_route_explicit"}
    if extra:
        facts.update(extra)
    try:
        from flask import g, request
        subj = subj or f"ui:{request.path}"
        facts.update({
            "method": request.method,
            "path": request.path,
            "endpoint": str(request.endpoint or ""),
        })
        body: Dict[str, Any] = {}
        for key in payload_keys:
            if request.is_json:
                data = request.get_json(silent=True) or {}
                if isinstance(data, dict) and key in data:
                    body[key] = data[key]
            else:
                if key in request.form:
                    body[key] = request.form.get(key)
        if body:
            facts["request_fields"] = body
        facts["identity_source"] = getattr(g, "_audit_ui", {}).get("identity_source", "")
    except Exception:  # noqa: BLE001 非 Flask 上下文 → 用已有信息落账
        ctx = get_ui_context()
        if ctx:
            facts.setdefault("identity_source", ctx.get("identity_source", ""))
    for key in view_args_keys:
        if key in view_kwargs:
            facts.setdefault("view_args", {})[key] = str(view_kwargs[key])
    entry = default_facade.record(action, actor=None, subject=subj or "",
                                  extra=facts, source=SOURCE_UI, status=status)
    try:
        from flask import g
        g._audit_explicit = True
        g._audit_recorded = True
    except Exception:  # noqa: BLE001 无请求上下文
        pass
    return entry


_recorders: List[UIAuditRecorder] = []


def get_ui_recorders() -> List[UIAuditRecorder]:
    """已安装的记录器（诊断/测试）"""
    return list(_recorders)


def reset_ui_recorders() -> None:
    """清空记录器登记（**测试专用**）"""
    _recorders.clear()


__all__ = [
    "DEFAULT_SKIP_PREFIXES", "UIAuditRecorder", "WRITE_METHODS", "action_from_request",
    "audit_action", "get_ui_recorders", "identity_facts", "install_flask_audit",
    "reset_ui_recorders", "resolve_ui_actor", "token_fingerprint",
]
