"""子代理回调反向通道（S5 / 阶段计划 P5 · 通信面）

【解决什么】
    DelegationExecutor 早已有 callback_dispatcher 注入点（契约⑧），但**缺省只写
    审计**（_dispatch_callback 的 else 分支）。本模块把它接成**可选 HTTP 投递**：

      出站：HttpCallbackDispatcher —— 过 CallbackPolicy 白名单 → 过密钥闸门
            （credentials.assert_manifest_secret_free）→ 带 Bearer token POST →
            超时 + ≤1 次重试 → 返回 {delivered, status, error, mode}。
      入站：POST /api/subagent/callback（@require_token，见 routes_subagent.py）
            —— 幂等落一条委派记录，**不执行任何工具、不建/改容器**。

【为什么默认关闭（fail-closed）】
    CP_SUBAGENT_CALLBACK_HTTP 未开启时：
      · build_callback_dispatcher() 返回 None ⇒ 执行器仍是「只写审计」，行为逐字不变；
      · CallbackPolicy.check() 一律拒绝，即使有人绕过工厂直接 new 了 dispatcher。
    回调地址 callback_url 可能来自**用户输入**（bundle / delegate / fan_out 的八要素），
    是典型 SSRF 风险点：若不设白名单，攻击者能让母体向内网/元数据端点发请求。

【SSRF 策略（本模块的唯一出站判据）】
    1. 开关默认关（CP_SUBAGENT_CALLBACK_HTTP）；
    2. 只允许 http / https（file:// / gopher:// / ftp:// 等显式拒绝）；
    3. host 必须落在 CP_SUBAGENT_CALLBACK_ALLOW（逗号分隔）：
         · 精确 host（api.partner.com）与后缀 host（.partner.com，匹配子域）两种写法；
         · 地址里的 userinfo（http://user@host/）显式拒绝；
       host 之外的部分（路径 / 端口）不构成授权 —— 白名单就是唯一的口径。
    4. 出站**不跟随重定向**（allow_redirects=False）：跟随会让跳转后的目标逃过白名单。

【密钥纪律】
    CP_SUBAGENT_CALLBACK_TOKEN 原文**绝不**出现在返回值 / 异常消息 / 日志里；
    payload 落网前先过 assert_manifest_secret_free，检出密钥形态即抛
    ManifestSecretLeak（由 executor fail-soft 接住，委派结果不变）。

【依赖纪律】
    仅标准库 + agent.subagent.credentials（纯标准库）；requests 在默认传输里惰性导入，
    便于单测注入假传输且不给导入期增加网络库负担。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from agent.subagent.credentials import (
    ManifestSecretLeak,
    assert_manifest_secret_free,
)
from agent.subagent.delegation_history import GOAL_MAX_CHARS

logger = logging.getLogger(__name__)

#: 出站总开关（默认关）：只有显式置真才允许任何 HTTP 回调
ENV_HTTP = "CP_SUBAGENT_CALLBACK_HTTP"
#: host 白名单（逗号分隔；精确 host 或 .example.com 后缀）
ENV_ALLOW = "CP_SUBAGENT_CALLBACK_ALLOW"
#: 入站/出站共享令牌（**绝不回显/记录**）
ENV_TOKEN = "CP_SUBAGENT_CALLBACK_TOKEN"
#: 出站超时（秒）
ENV_TIMEOUT = "CP_SUBAGENT_CALLBACK_TIMEOUT"

#: 默认超时（秒）
DEFAULT_TIMEOUT = 5.0
#: 重试次数上界（任务口径：≤1 次）
MAX_RETRIES = 1
#: 允许的 scheme
ALLOWED_SCHEMES = ("http", "https")
#: 入站回调短摘要截断长度（summary 只作短摘要，绝不执行）
CALLBACK_SUMMARY_MAX_CHARS = 1000
#: 视为成功的回调 status（其余一律 ok=False）
_SUCCESS_STATUSES = ("success", "ok", "done", "completed")
#: 真值集合
_TRUTHY = ("1", "true", "yes", "on")


def _truthy(value: Any) -> bool:
    """环境变量原文 → 布尔（缺省/无法识别一律 False，fail-closed）

    【为什么叫 _truthy 而不是 _env_flag】scan_settings.py 会把形如 _env_flag(x)
    的调用当成"读取名为 x 的开关"；本函数接的是**已取出的字符串**，若沿用该名会被
    误判成动态开关家族。改名后，真正的读取点是内层 os.environ.get(常量) ，可被机械解析。
    """
    return str(value or "").strip().lower() in _TRUTHY


def _split_allow(raw: Any) -> Tuple[str, ...]:
    """逗号分隔白名单 → 去空白、小写、去重保序元组"""
    out = []
    for item in str(raw or "").split(","):
        host = item.strip().lower()
        if host and host not in out:
            out.append(host)
    return tuple(out)


def _env_timeout() -> float:
    """读超时（非法/非正数回退默认；不抛）"""
    try:
        value = float(str(os.environ.get(ENV_TIMEOUT, "") or DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def host_allowed(host: str, allowed: Sequence[str]) -> bool:
    """host 是否命中白名单（精确，或 .example.com 后缀匹配子域）

    【为什么后缀写法要点号开头】evil-example.com 不以 .example.com 结尾 ⇒ 不命中；
    若把 example.com 当后缀做 endswith，notexample.com 就会假绿。
    """
    host = str(host or "").strip().lower()
    if not host:
        return False
    for entry in allowed:
        entry = str(entry or "").strip().lower()
        if not entry:
            continue
        if entry == host:
            return True
        if entry.startswith(".") and host.endswith(entry):
            return True
    return False


class CallbackPolicy:
    """出站回调判据（纯逻辑，可单测）

    Args:
        enabled: 显式覆盖开关；None = 读 CP_SUBAGENT_CALLBACK_HTTP。
        allowed_hosts: 显式覆盖白名单；None = 读 CP_SUBAGENT_CALLBACK_ALLOW。
    """

    def __init__(self, *, enabled: Optional[bool] = None,
                 allowed_hosts: Optional[Iterable[str]] = None) -> None:
        self._enabled = (_truthy(os.environ.get(ENV_HTTP, ""))
                         if enabled is None else bool(enabled))
        if allowed_hosts is None:
            self._allowed: Tuple[str, ...] = _split_allow(
                os.environ.get(ENV_ALLOW, ""))
        else:
            self._allowed = _split_allow(",".join(str(h) for h in allowed_hosts))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def allowed_hosts(self) -> Tuple[str, ...]:
        return self._allowed

    def check(self, callback_url: Any) -> Tuple[bool, str]:
        """(allowed, reason)：不允许时 reason 必非空（可被审计/回显）"""
        if not self._enabled:
            return False, ("HTTP 回调未开启（未显式置真 " + ENV_HTTP + "）")
        url = str(callback_url or "").strip()
        if not url:
            return False, "回调地址为空"
        try:
            parsed = urlsplit(url)
        except ValueError as e:  # 畸形 URL 一律拒绝，不静默当空
            return False, f"回调地址无法解析：{type(e).__name__}"
        scheme = str(parsed.scheme or "").lower()
        if scheme not in ALLOWED_SCHEMES:
            return False, (f"回调 scheme 不被允许：{scheme or '(空)'}"
                           "（仅 http/https）")
        if parsed.username or parsed.password:
            return False, "回调地址不得携带 userinfo（user:pass@host）"
        host = str(parsed.hostname or "").strip().lower()
        if not host:
            return False, "回调地址缺少 host"
        if not self._allowed:
            return False, (ENV_ALLOW + " 未配置 host 白名单 ⇒ 一律拒绝")
        if not host_allowed(host, self._allowed):
            return False, f"host 不在白名单：{host}"
        return True, ""


class HttpCallbackDispatcher:
    """可选 HTTP 回调投递器（(callback_url, payload) -> dict）

    Returns:
        {"delivered": bool, "status": Optional[int], "error": str, "mode": str}
        · mode="blocked" = 被 CallbackPolicy 拒绝（**零出站**）；
        · mode="http" = 真的尝试过投递。

    Raises:
        ManifestSecretLeak: payload 含密钥形态（**拒绝投递**，由执行器 fail-soft 接住）。
    """

    def __init__(self, *, policy: Optional[CallbackPolicy] = None,
                 token: Optional[str] = None,
                 timeout: Optional[float] = None,
                 retries: int = MAX_RETRIES,
                 transport: Optional[Callable[..., Any]] = None) -> None:
        self._policy = policy if policy is not None else CallbackPolicy()
        # token 只存私有属性：绝不进入返回值/日志/异常
        self._token = (str(os.environ.get(ENV_TOKEN, "") or "")
                       if token is None else str(token or ""))
        self._timeout = _env_timeout() if timeout is None else float(timeout)
        self._retries = max(0, min(int(retries), MAX_RETRIES))
        self._transport = transport if transport is not None else self._requests_transport

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def retries(self) -> int:
        return self._retries

    # ── 默认传输（惰性导入 requests；不跟随重定向） ──

    def _requests_transport(self, url: str, body: str, headers: Mapping[str, str],
                            timeout: float) -> int:
        import requests  # 惰性：导入期不拉网络库

        resp = requests.post(url, data=body.encode("utf-8"), headers=dict(headers),
                             timeout=timeout, allow_redirects=False)
        return int(getattr(resp, "status_code", 0) or 0)

    # ── 调用 ──

    def __call__(self, callback_url: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
        allowed, reason = self._policy.check(callback_url)
        if not allowed:
            logger.warning("[CallbackChannel] 出站被拒（零出站）：%s", reason)
            return {"delivered": False, "status": None, "error": reason,
                    "mode": "blocked"}
        # 密钥闸门：检出密钥形态即拒绝投递（抛给执行器 fail-soft 接住）
        assert_manifest_secret_free(payload)
        url = str(callback_url).strip()
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        body = json.dumps(dict(payload), ensure_ascii=False)

        last_status: Optional[int] = None
        last_error = ""
        attempts = 0
        # 重试口径：传输异常或 5xx 时重试，总尝试次数 = retries + 1
        while attempts <= self._retries:
            attempts += 1
            try:
                raw_status = self._transport(url, body, headers, self._timeout)
                status = int(getattr(raw_status, "status_code", raw_status) or 0)
            except Exception as e:  # noqa: BLE001 传输失败不抛出，如实记录
                last_status = None
                last_error = f"{type(e).__name__}: {e}"
                continue
            last_status = status
            if 200 <= status < 300:
                return {"delivered": True, "status": status, "error": "",
                        "mode": "http", "attempts": attempts}
            last_error = f"HTTP {status}"
            if status < 500:
                break  # 4xx 是确定性拒绝，重试无意义
        return {"delivered": False, "status": last_status, "error": last_error,
                "mode": "http", "attempts": attempts}


def build_callback_dispatcher(*, transport: Optional[Callable[..., Any]] = None,
                              **kwargs: Any) -> Optional[HttpCallbackDispatcher]:
    """工厂：**未开启或配置不齐 ⇒ 返回 None**（生产默认仍只写审计）

    「配置齐全」= 开关开启 + host 白名单非空 + 令牌非空（入站 require_token 的对称面）。
    任一缺失都 fail-closed 回退 None，并在日志里说明缺哪一项（**绝不打印令牌**）。
    """
    policy = CallbackPolicy()
    if not policy.enabled:
        return None
    missing = []
    if not policy.allowed_hosts:
        missing.append(ENV_ALLOW)
    if not str(os.environ.get(ENV_TOKEN, "") or "").strip():
        missing.append(ENV_TOKEN)
    if missing:
        logger.warning("[CallbackChannel] HTTP 回调已开启但配置不齐（%s）"
                       "⇒ 回退为只写审计（fail-closed）", "、".join(missing))
        return None
    return HttpCallbackDispatcher(policy=policy, transport=transport, **kwargs)


def default_callback_dispatcher() -> Optional[HttpCallbackDispatcher]:
    """工厂的安全包装：任何异常都回退 None（只写审计），**不阻断**执行器构造"""
    try:
        return build_callback_dispatcher()
    except Exception as e:  # noqa: BLE001 回调机制故障不得阻断主流程
        logger.warning("[CallbackChannel] 投递器工厂不可用，回退只写审计: %s", e)
        return None


def _as_int(value: Any) -> int:
    """宽松取整（None/bool/非数字一律 0，不抛）"""
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_callback_record(data: Mapping[str, Any], delegation_id: str, *,
                          now: Optional[str] = None) -> Dict[str, Any]:
    """入站回调请求体 → 委派记录（与 delegation_history.build_record 同一字段口径）

    summary 只作**短摘要**（截断 ≤1000），**不执行、不解析**为工具调用；本函数是纯函数，
    可单测截断与状态映射。追加已存在的字段语义（ok/source/... ），外加两个只读标注
    （callback_status / reporter），不改变既有 JSONL 契约。
    """
    summary = str(data.get("summary") or "")
    status = str(data.get("status") or "")
    return {
        "delegation_id": str(delegation_id or ""),
        "subagent": "",
        "source": "callback",
        "ok": status.strip().lower() in _SUCCESS_STATUSES,
        "tier": "",
        "goal": summary[:GOAL_MAX_CHARS],
        "duration_ms": 0.0,
        "trace_id": str(data.get("trace_id") or ""),
        "error_code": "",
        "error": "",
        "artifact_count": max(0, _as_int(data.get("artifact_count"))),
        "tokens": None,
        "created_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "callback_status": status,
        "reporter": str(data.get("source") or ""),
        "summary": summary[:CALLBACK_SUMMARY_MAX_CHARS],
    }


__all__ = [
    "ENV_HTTP", "ENV_ALLOW", "ENV_TOKEN", "ENV_TIMEOUT",
    "DEFAULT_TIMEOUT", "MAX_RETRIES", "ALLOWED_SCHEMES",
    "CALLBACK_SUMMARY_MAX_CHARS",
    "CallbackPolicy", "HttpCallbackDispatcher", "build_callback_dispatcher",
    "default_callback_dispatcher", "build_callback_record",
    "host_allowed",
]
