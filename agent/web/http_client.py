"""
HTTP 请求引擎 — 会话管理、重试、代理、Cookie

提供统一的 HTTP/HTTPS 请求接口，支持同步/异步，
集成重试机制、超时控制、Cookie 持久化。
"""

import asyncio
import json
import logging
import os
import time
from contextlib import nullcontext as _nullcontext
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urlparse, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

#: 重定向状态码（`requests` 内部同款集合；手动跟随用）
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _is_redirect(resp: Any) -> bool:
    """响应是否是重定向（且带 Location）"""
    try:
        return int(getattr(resp, "status_code", 0)) in _REDIRECT_CODES \
            and bool((getattr(resp, "headers", {}) or {}).get("location"))
    except Exception:  # noqa: BLE001  响应对象形状异常 ⇒ 当作非重定向（不改变主流程）
        return False


def _env_disabled(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() \
        in ("0", "false", "no", "off")


def _ssrf_disabled() -> bool:
    """`CP_SSRF_GUARD` 是否被显式关闭（关闭 ⇒ 出站回到改动前行为）"""
    return _env_disabled("CP_SSRF_GUARD", "1")


def _egress_guard_disabled() -> bool:
    """`CP_POLICY_EGRESS_GUARD` 是否被显式关闭（既有开关）"""
    return _env_disabled("CP_POLICY_EGRESS_GUARD", "1")


def _guard_unavailable_result(url: str, module: str, exc: BaseException) -> dict:
    """守卫组件不可用时的**拒绝**结果（E3 fail-closed 的统一构造）"""
    return {
        "ok": False,
        "status_code": None,
        "headers": {},
        "content": None,
        "text": None,
        "content_length": 0,
        "url": url,
        "elapsed": 0.0,
        "error": (f"出站守卫组件不可用（{module} 导入失败：{type(exc).__name__}）"
                  f"⇒ 按 fail-closed 拒绝出站。如需临时放行请显式设置对应开关为 0。"),
        "blocked": True,
        "blocked_by": module,
        "guard_unavailable": True,
        "network_action_taken": False,
    }


def _ssrf_blocked_result(exc: BaseException, url: str) -> Optional[dict]:
    """把**连接期**抛出的 `SsrfBlocked` 转成拦截结果；其它异常返回 None（原样上抛）

    【为什么必须区分】若不区分，连接期拦截会被 `except requests.exceptions.*`
    或最外层 `except Exception` 吞成 `{"ok": False, "error": "连接失败: ..."}` ——
    调用方（与审计）就分不清"被安全拦下"与"网络不通"，这正是 §7「别让看板说谎」
    要禁的失真。
    """
    try:
        from agent.guardrails.ssrf_guard import SsrfBlocked
    except Exception:  # noqa: BLE001  守卫不可用 ⇒ 交回原异常处理
        return None
    if not isinstance(exc, SsrfBlocked):
        return None
    return {
        "ok": False,
        "status_code": None,
        "headers": {},
        "content": None,
        "text": None,
        "content_length": 0,
        "url": url,
        "elapsed": 0.0,
        "error": str(exc),
        "blocked": True,
        "blocked_by": "guardrails.ssrf_guard",
        "ssrf_stage": "connect",
        "network_action_taken": False,
    }


# 默认配置（向后兼容常量，实际值应通过便捷函数从 Config 读取）
DEFAULT_TIMEOUT = 30  # 向后兼容别名，实际值通过 get_http_timeout() 读取
DEFAULT_MAX_RETRIES = 3  # 向后兼容别名
DEFAULT_CONNECT_TIMEOUT = 10  # 向后兼容别名，实际值通过 get_http_connect_timeout() 读取
DEFAULT_POOL_SIZE = 20  # 向后兼容别名，实际值通过 get_http_pool_size() 读取


class HttpClient:
    """HTTP 请求引擎 — 云枢的互联网访问基础

    功能：
    - GET/POST/PUT/DELETE/HEAD 请求
    - 自动重试（指数退避）
    - Cookie 会话持久化
    - 请求头自定义
    - 代理支持（HTTP/HTTPS/SOCKS）
    - 响应自动解码
    - 流式下载
    """

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._session = self._build_session()
        self._cookies_file = self._config.get("cookies_file")
        self._load_cookies()
        self._stats = {
            "total_requests": 0,
            "success_count": 0,
            "error_count": 0,
            "blocked_count": 0,  # P7.1-20 出域执行点拦截计数（策略 deny/ask）
            "total_bytes": 0,
            "started_at": time.time(),
        }
        logger.info("HTTP 请求引擎已初始化")

    def _build_session(self) -> requests.Session:
        """构建 requests Session 含连接池和重试策略"""
        session = requests.Session()

        # ── 环境变量代理：**显式关闭**（TASK-07 第 1 步第 3 项）──────────────
        # 【为什么必须关】`requests.Session` 默认 `trust_env=True` ⇒ 会读
        # `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY`。这意味着**任何能改环境
        # 变量的主体**（含被注入的 shell 工具、子进程继承）都能把云枢的**全部出站**
        # 改道到自己的代理上 —— 那既是流量劫持，也是 SSRF 守卫的绕过面
        # （守卫校验的是 URL 里的主机，流量却去了代理）。
        # 【既有的正当代理需求怎么办】`agent/network_config.py` 有代理配置，
        # `web_tools.py` 把它作为构造参数传进来（`self._config["proxy"]`）——
        # 那是**显式配置的代理**，在下面按原样生效。本改动只切断"继承环境变量"这条路，
        # 不改变"显式配置代理"的行为（D2）。
        session.trust_env = False

        # 默认请求头
        session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })

        # 重试策略（包含 403 以应对反爬限制）
        # 配置化：从 Config 读取默认重试次数（支持热加载）
        from agent.monitoring.observability_config import get_http_max_retries
        retry_strategy = Retry(
            total=self._config.get("max_retries", get_http_max_retries()),
            backoff_factor=self._config.get("backoff_factor", 0.5),
            status_forcelist=[429, 500, 502, 503, 504, 403],
            allowed_methods=["GET", "POST", "HEAD"],
        )

        # HTTP 和 HTTPS 适配器
        # 配置化：从 Config 读取默认连接池大小（支持热加载）
        from agent.monitoring.observability_config import get_http_pool_size
        _pool_size = self._config.get("pool_size", get_http_pool_size())
        adapter = HTTPAdapter(
            pool_connections=_pool_size,
            pool_maxsize=_pool_size * 2,
            max_retries=retry_strategy,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 代理
        proxy = self._config.get("proxy")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}

        return session

    # ── 核心请求方法 ──────────────────────────────────────────────

    def _egress_block(self, method: str, url: str, *, params: Any = None,
                      data: Any = None, json_data: Any = None,
                      headers: Any = None, capability_id: str = "",
                      data_class: str = "") -> Optional[dict]:
        """出域执行点：返回拦截结果 dict，放行时返回 None

        与策略决策层（``agent/policy/egress.py``）通过
        ``agent/guardrails/egress_guard.py`` 解耦：本方法只负责「问 + 应用结果」。

        【TASK-07 改动：fail-open → **fail-closed**（E3）】
          原实现 `except Exception: return None`（守卫不可用 ⇒ 放行）。TASK-07 §3
          第 2 步第 4 项要求"出站守卫失效时必须**拒绝**请求，而非放行"。
          现改为：守卫组件导入失败 ⇒ 返回拒绝结果。
          **回滚口**：`CP_POLICY_EGRESS_GUARD=0`（既有开关）表示"运维显式关掉这一层"，
          此时按放行处理（与 `_egress_block` 的既有语义一致）。
        """
        try:
            from agent.guardrails.egress_guard import EgressGuard
        except Exception as exc:  # noqa: BLE001 守卫不可用
            if _egress_guard_disabled():
                return None
            logger.error("[HttpClient] 出域守卫组件不可用 ⇒ 按 fail-closed 拒绝: %s: %s",
                         type(exc).__name__, exc)
            return _guard_unavailable_result(
                url, "agent.guardrails.egress_guard", exc)
        decision = EgressGuard.precheck(
            method=method, url=url, params=params, data=data, json_data=json_data,
            headers=headers, capability_id=capability_id, data_class=data_class)
        if decision is None or decision.allowed:
            return None
        return EgressGuard.block_result(decision, url)

    def _ssrf_block(self, url: str, *, surface: str = "web.http_client") -> Optional[dict]:
        """**SSRF 出域执行点**：返回拦截结果 dict，放行时返回 None

        【它为什么与被保留的 egress 策略层并存而不是替换它】两者判**不同的问题**：
          · 本方法判"这个目标地址本身是否可信"（私有/保留/元数据网段、非标准 IP
            写法、非 http(s) 协议）——**默认拒绝，零基础**；
          · `_egress_block` 判"这一次出站的数据是否允许外发"（密钥/污点/策略）——
            依赖策略文件命中。
        策略层对 `external=False` 的目标**结构性地无覆盖**（三条规则都以
        `target.external==true` 为合取项 ⇒ 元数据地址根本走不到规则），
        所以 SSRF 这一层必须独立存在，不能靠策略层补。
        """
        try:
            from agent.guardrails import ssrf_guard
        except Exception as exc:  # noqa: BLE001
            if _ssrf_disabled():
                return None
            logger.error("[HttpClient] SSRF 守卫组件不可用 ⇒ 按 fail-closed 拒绝: %s: %s",
                         type(exc).__name__, exc)
            return _guard_unavailable_result(url, "agent.guardrails.ssrf_guard", exc)
        # 连接期第二道防线（DNS rebinding / 跳转后主机名的真实落点）
        ssrf_guard.install_connect_guard()
        verdict = ssrf_guard.check_url(url)
        if verdict.allowed:
            return None
        ssrf_guard.audit_block(verdict, url=url, surface=surface)
        return ssrf_guard.block_result(verdict, url)

    def _preflight_block(self, method: str, url: str, *, params: Any = None,
                         data: Any = None, json_data: Any = None,
                         headers: Any = None, capability_id: str = "",
                         data_class: str = "") -> Optional[dict]:
        """出站**前置闸门总入口**：SSRF 判定 → 策略出域判定（顺序固定）

        【为什么 SSRF 在前】地址判定是**更基础**的一层（"能不能碰这个地址"），
        且它零依赖策略文件；先判它可以在"策略文件损坏"时仍然拦住元数据地址。
        两层都放行才返回 None。**任一层的失效都不放行**（E3）。
        """
        blocked = self._ssrf_block(url)
        if blocked is not None:
            return blocked
        return self._egress_block(method, url, params=params, data=data,
                                  json_data=json_data, headers=headers,
                                  capability_id=capability_id, data_class=data_class)

    #: 一次请求允许跟随的最大重定向跳数（**每一跳都复检**，见 `_send_with_redirects`）
    MAX_REDIRECTS = 5

    def _send_with_redirects(self, *, method: str, url: str, params: Any, data: Any,
                             json_data: Any, headers: Any, cookies: Any, timeout: Any,
                             allow_redirects: bool, stream: bool, verify: bool,
                             safe_kwargs: dict):
        """发送请求并在**每一跳重定向前复检目标**（TASK-07 第 2 步第 3 项）

        ## 为什么必须手写重定向循环

        `requests` 默认 `allow_redirects=True`：它跟随 302 时**不会再问任何守卫**
        —— 于是"公网 URL 立刻 302 到 `http://169.254.169.254/`"这条经典 SSRF 链
        结构性地绕过了所有前置判定（TASK-07 §2.1 明确列为缺陷）。
        现改为 `allow_redirects=False` + 手动跟随，**每一跳都走一遍
        `_preflight_block`**（SSRF + 策略两层）。

        ## 返回

        `(resp, redirect_history, blocked)`：
          · 正常路径 → `blocked=None`，`redirect_history` 为**被跟随的中间 URL 列表**
            （与改动前 `[r.url for r in resp.history]` 同义，保证既有消费者不破，D2）；
          · 某一跳被拒 → `blocked` 为拦截结果 dict（`resp` 为最后一个响应）。
        """
        current_url = url
        current_method = str(method or "GET").upper()
        redirect_history: List[str] = []
        resp = None
        for hop in range(self.MAX_REDIRECTS + 1):
            try:
                # 【第二道防线的作用域】连接期地址校验只在"经过本次出站"的范围内生效，
                # 避免把进程内合法的环回访问（后端自身端口、Loki 等）一并拦死。
                from agent.guardrails import ssrf_guard as _ssrf
                _scope = _ssrf.guard_scope()
            except Exception:  # noqa: BLE001 守卫不可用 ⇒ 退化（第一道防线仍在）
                _scope = _nullcontext()
            try:
                with _scope:
                    resp = self._session.request(
                        method=current_method, url=current_url,
                        params=params if hop == 0 else None,
                        data=data if hop == 0 else None,
                        json=json_data if hop == 0 else None,
                        headers=headers, cookies=cookies, timeout=timeout,
                        # **不再交给 requests 跟随**：跟随会让跳转后的目标逃过守卫
                        allow_redirects=False, stream=stream, verify=verify,
                        **safe_kwargs)
            except Exception as exc:  # noqa: BLE001
                # 连接期拦截抛的 `SsrfBlocked` 要与普通网络异常区分开：
                # 前者是**安全结论**，必须落审计 + 返回 blocked 结果，不能退化成
                # "连接失败"（那会让"被拦住"和"网断了"无法分辨）。
                blocked = _ssrf_blocked_result(exc, current_url)
                if blocked is not None:
                    return resp, redirect_history, blocked
                raise
            if not allow_redirects or not _is_redirect(resp):
                break
            location = resp.headers.get("location") or ""
            if not location:
                break
            next_url = urljoin(current_url, location)
            hop_block = self._preflight_block(current_method, next_url,
                                              headers=headers)
            if hop_block is not None:
                hop_block["error"] = (
                    f"重定向目标被拒绝（第 {hop + 1} 跳）：{next_url}｜"
                    + str(hop_block.get("error") or ""))
                hop_block["redirect_history"] = list(redirect_history)
                hop_block["redirect_blocked_hop"] = hop + 1
                logger.warning("[HttpClient] 重定向被出域守卫拦截: %s → %s", current_url,
                               next_url)
                return resp, redirect_history, hop_block
            redirect_history.append(current_url)
            # 303 语义 + 301/302 下 POST→GET（与 requests 的既有行为对齐，避免
            # "改成手动跟随"变成一次行为变更，D2）
            if resp.status_code == 303 or (
                    resp.status_code in (301, 302) and current_method == "POST"):
                current_method = "GET"
                data, json_data = None, None
            current_url = next_url
        return resp, redirect_history, None

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        data: Any = None,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: Optional[int] = None,
        allow_redirects: bool = True,
        stream: bool = False,
        verify: bool = True,
        **kwargs,
    ) -> dict:
        """通用 HTTP 请求方法

        Args:
            method: HTTP 方法（GET/POST/PUT/DELETE/HEAD）
            url: 目标 URL
            params: URL 查询参数
            data: 表单数据或原始数据
            json_data: JSON 数据
            headers: 自定义请求头
            cookies: 自定义 Cookie
            timeout: 超时秒数
            allow_redirects: 是否跟随重定向
            stream: 是否流式响应
            verify: 是否验证 SSL
            **kwargs: 传递给 requests 的其他参数

        Returns:
            dict: {ok, status_code, headers, content, text, url, elapsed, error, ...}
        """
        self._stats["total_requests"] += 1
        start = time.time()

        # 合并请求头
        req_headers = {}
        if headers:
            req_headers.update(headers)

        # URL 校验
        if not url.startswith(("http://", "https://")):
            return self._error_result(url, "仅支持 http/https 协议", start)

        # ── 出域执行点（TASK-S4-02 / P7.1-20 + TASK-07 SSRF）────────────────
        # 判定顺序：SSRF 地址判定 → 策略出域判定（见 `_preflight_block`）。
        # 拦截时网络动作尚未发生（下面 self._session.request 未被调用）。
        blocked = self._preflight_block(method, url, params=params, data=data,
                                        json_data=json_data, headers=headers)
        if blocked is not None:
            self._stats["blocked_count"] += 1
            blocked["elapsed"] = round(time.time() - start, 3)
            return blocked

        try:
            # 过滤与显式参数同名的键，避免 **kwargs 展开冲突
            _http_reserved = {"method", "url", "params", "data", "json",
                              "headers", "cookies", "timeout",
                              "allow_redirects", "stream", "verify"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _http_reserved}
            # 配置化：从 Config 读取默认超时（支持热加载）
            from agent.monitoring.observability_config import get_http_timeout
            resp, redirect_history, blocked_hop = self._send_with_redirects(
                method=method, url=url, params=params, data=data, json_data=json_data,
                headers=req_headers or None, cookies=cookies,
                timeout=timeout or self._config.get("timeout", get_http_timeout()),
                allow_redirects=allow_redirects, stream=stream, verify=verify,
                safe_kwargs=safe_kwargs)
            if blocked_hop is not None:
                self._stats["blocked_count"] += 1
                blocked_hop["elapsed"] = round(time.time() - start, 3)
                return blocked_hop

            elapsed = time.time() - start
            content = resp.content
            self._stats["success_count"] += 1
            self._stats["total_bytes"] += len(content)

            # 尝试解码文本
            text = None
            encoding = None
            try:
                if resp.encoding:
                    encoding = resp.encoding
                    text = resp.text
                else:
                    # 自动检测编码
                    import chardet
                    detected = chardet.detect(content)
                    encoding = detected.get("encoding", "utf-8")
                    text = content.decode(encoding, errors="replace")
            except Exception:
                text = content.decode("utf-8", errors="replace")
                encoding = "utf-8 (with replacements)"

            result = {
                "ok": resp.ok,
                "status_code": resp.status_code,
                "reason": resp.reason,
                "headers": dict(resp.headers),
                "content_length": len(content),
                "content": None if stream else content,  # 流式不自动读取
                "text": text if not stream else None,
                "encoding": encoding,
                "url": resp.url,
                "elapsed": round(elapsed, 3),
                "cookies": dict(resp.cookies),
                "redirect_history": redirect_history,
            }

            if not resp.ok:
                result["error"] = f"HTTP {resp.status_code}: {resp.reason}"

            return result

        except requests.exceptions.Timeout as e:
            self._stats["error_count"] += 1
            return self._error_result(url, f"请求超时: {e}", start)
        except requests.exceptions.ConnectionError as e:
            self._stats["error_count"] += 1
            return self._error_result(url, f"连接失败: {e}", start)
        except requests.exceptions.RequestException as e:
            self._stats["error_count"] += 1
            return self._error_result(url, f"请求异常: {e}", start)
        except Exception as e:
            self._stats["error_count"] += 1
            logger.exception("HTTP 请求未知异常")
            return self._error_result(url, f"未知错误: {e}", start)

    def get(self, url: str, **kwargs) -> dict:
        """GET 请求"""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> dict:
        """POST 请求"""
        return self.request("POST", url, **kwargs)

    def head(self, url: str, **kwargs) -> dict:
        """HEAD 请求（获取响应头）"""
        return self.request("HEAD", url, **kwargs)

    # ── 高级功能 ──────────────────────────────────────────────────

    def download(self, url: str, filepath: str, chunk_size: int = 8192, **kwargs) -> dict:
        """下载文件到本地

        Args:
            url: 文件 URL
            filepath: 本地存储路径
            chunk_size: 分块大小

        Returns:
            dict: {ok, filepath, size, elapsed, error}
        """
        import os
        start = time.time()
        # 出域执行点：download 走 self._session.get 而非 request()，必须单独接线，
        # 否则「下载」会成为绕过策略的旁路（同类旁路见 agent/monitoring/*.py 直连
        # requests，那些不在本任务改动面内，已在验收报告「遗留问题」登记）。
        # kwargs 一并交给守卫**扫描**（auth/token 一类凭据常从 kwargs 传入）。
        blocked = self._preflight_block("GET", url, params=kwargs)
        if blocked is not None:
            self._stats["blocked_count"] += 1
            blocked["elapsed"] = round(time.time() - start, 3)
            return blocked
        try:
            _http_reserved = {"url", "stream", "timeout"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _http_reserved}
            # 配置化：从 Config 读取默认超时（支持热加载）
            from agent.monitoring.observability_config import get_http_timeout
            try:
                from agent.guardrails import ssrf_guard as _ssrf
                _scope = _ssrf.guard_scope()
            except Exception:  # noqa: BLE001
                _scope = _nullcontext()
            with _scope:
                resp = self._session.get(url, stream=True, timeout=get_http_timeout(),
                                         **safe_kwargs)
            resp.raise_for_status()

            os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
            total = 0
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            self._stats["total_bytes"] += total
            return {
                "ok": True,
                "filepath": filepath,
                "size": total,
                "elapsed": round(time.time() - start, 3),
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "elapsed": round(time.time() - start, 3)}

    def batch_request(self, urls: List[str], method: str = "GET",
                      max_concurrency: int = 5, **kwargs) -> List[dict]:
        """批量请求（同步，简单并发控制）

        Args:
            urls: URL 列表
            method: HTTP 方法
            max_concurrency: 最大并发数
            **kwargs: 传递给 request 的参数

        Returns:
            List[dict]: 请求结果列表
        """
        results = []
        for i in range(0, len(urls), max_concurrency):
            batch = urls[i:i + max_concurrency]
            batch_results = []
            for url in batch:
                result = self.request(method, url, **kwargs)
                batch_results.append(result)
            results.extend(batch_results)
        return results

    # ── Cookie 管理 ──────────────────────────────────────────────

    def set_cookies(self, cookies: dict, domain: Optional[str] = None):
        """手动设置 Cookie"""
        for name, value in cookies.items():
            if domain:
                from requests.cookies import create_cookie
                cookie = create_cookie(name=name, value=value, domain=domain)
                self._session.cookies.set_cookie(cookie)
            else:
                self._session.cookies.set(name, value)

    def get_cookies(self, domain: Optional[str] = None) -> dict:
        """获取当前会话的 Cookie"""
        if domain:
            return dict(self._session.cookies.get_dict(domain=domain))
        return dict(self._session.cookies.get_dict())

    def clear_cookies(self):
        """清空所有 Cookie"""
        self._session.cookies.clear()

    def _load_cookies(self):
        """从文件加载持久化 Cookie"""
        if not self._cookies_file:
            return
        try:
            import os
            if os.path.exists(self._cookies_file):
                with open(self._cookies_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                    for name, value in cookies.items():
                        self._session.cookies.set(name, value)
                logger.info("已加载 %d 个持久化 Cookie", len(cookies))
        except Exception as e:
            logger.warning("加载 Cookie 失败: %s", e)

    def save_cookies(self):
        """保存 Cookie 到文件"""
        if not self._cookies_file:
            return
        try:
            import os
            os.makedirs(os.path.dirname(self._cookies_file) or ".", exist_ok=True)
            with open(self._cookies_file, "w", encoding="utf-8") as f:
                json.dump(dict(self._session.cookies.get_dict()), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存 Cookie 失败: %s", e)

    # ── 会话管理 ──────────────────────────────────────────────────

    def update_headers(self, headers: dict):
        """更新会话默认请求头"""
        self._session.headers.update(headers)

    def set_proxy(self, proxy: Optional[str]):
        """动态设置/清除代理"""
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}
        else:
            self._session.proxies = {}

    def reset_session(self):
        """重置会话（新会话、清 Cookie）"""
        old_cookies = self._session.cookies.get_dict()
        self._session = self._build_session()
        # 恢复 Cookie 白名单
        for name, value in old_cookies.items():
            self._session.cookies.set(name, value)

    # ── URL 工具 ──────────────────────────────────────────────────

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """检查 URL 是否有效"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    @staticmethod
    def join_url(base: str, path: str) -> str:
        """拼接 URL"""
        return urljoin(base, path)

    # ── 统计 ──────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取请求统计"""
        uptime = time.time() - self._stats["started_at"]
        return {
            **self._stats,
            "uptime_sec": round(uptime, 1),
            "avg_speed_kbps": round(self._stats["total_bytes"] / 1024 / max(uptime, 1), 1),
        }

    def close(self):
        """关闭会话释放资源"""
        try:
            self.save_cookies()
            self._session.close()
        except Exception:
            pass

    def _error_result(self, url: str, error: str, start: float) -> dict:
        return {
            "ok": False,
            "error": error,
            "url": url,
            "elapsed": round(time.time() - start, 3),
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
