"""SSRF 守卫 —— 出站目标的**唯一判定口径**（TASK-07 第 2 步）

【为什么需要这个模块（而不是继续用 agent/policy/egress.py 的分类器）】

现有 `agent/policy/egress.py::classify_target()` 只把主机分成 "外部 / 内部"，
结果**仅用于算一个布尔** `target.external`，再交给策略引擎判定；而策略文件里的
三条规则**全部**以 `target.external == true` 为合取项 ⇒ 私有网段（`external=False`）
**根本走不到任何规则**，`engine.py` 的"未命中即放行"把 `169.254.169.254`
（云元数据）这类致命目标放了过去。实测证据：`data/policies/decisions.jsonl`
的 97 条判定 100% 为 `no_policy_match` ⇒ 该守卫**从未拦截过一次**。

本模块因此把"目标能不能出站"做成**独立、默认拒绝、可单测**的判定，并补上
`classify_target` 自认没做的两件事：
    1. **DNS 解析后校验**（`egress.py:95` 自认"纯字符串处理，不做 DNS"）；
    2. **非标准 IP 写法归一**（十进制 / 十六进制 / 八进制 / 少段 / 尾点）。

【两道防线（**这是本模块最重要的设计，别删**）】

    第一道（`check_url`，在发请求**之前**）：
        语法检查 + IP 字面量归一 + 网段判定 + 能解析时校验**全部**解析结果 + 白名单。
    第二道（`guard_scope()` + `create_connection` 拦截，在**真正建连时**）：
        校验**实际要连接的地址**。

    【为什么必须有第二道】只有第一道时，"校验用的 IP"与"实际连接用的 IP"可以是
    两个值 —— 攻击者让第一次解析返回公网 IP（过检），真正建连时再返回 `127.0.0.1`，
    这就是 **DNS rebinding**，第一道**结构性拦不住**。第二道把判定点挪到
    "地址已经定下来、连接尚未建立"的那一瞬，rebinding 与"302 跳到内网主机名"
    都在这条线上被拦。
    【为什么用 `guard_scope()` 圈定范围而不是全进程常开】本进程**合法**地使用环回
    地址（后端自身 `127.0.0.1:5678`、告警通道、Loki 等）。全进程常开会把它们一并拦死。
    故第二道只在"经过 HttpClient 的出站请求"这个作用域内生效——而**所有** LLM
    可控的出站都必经那里（`web_get` / `web_search` / `web_download` / `web_batch`
    / `web_extract` 全部走 `agent/web/http_client.py`）。

【fail-closed 口径（E3）】

    - 判定**内部异常** ⇒ **拒绝**（`SsrfVerdict.allowed=False`），不是放行。
      理由：这个模块的判定逻辑只有几十行纯计算，它出错意味着"我们证不出目标安全"，
      而"证不出"在这条路径上等于"可能连到元数据服务"。
    - 连接期发现地址被禁 ⇒ **抛 `SsrfBlocked`**（绝不静默放行）。
    - 但**解析失败**（`gaierror`）默认**不阻断**：那说明目标本来就不可达，阻断只会把
      "网络不通"变成"安全拦截"，污染排障。要连"解析不出结论"也拒绝，设
      `CP_SSRF_STRICT_DNS=1`。**注意**：解析失败**不影响**第二道防线 ——
      真正建连时地址一定会被校验。

【与被保留的既有防线的关系】

    `agent/tools/browser_tools.py` 原来自己维护一份**子串黑名单**
    （`localhost/127.0.0.1/0.0.0.0/::1/192.168./10./172.16.`）——它漏
    `169.254.169.254`、172.17–172.31、十进制/十六进制 IP。本模块把它换成对
    `check_url` 的调用（**同一套判定**），不再保留第二份黑名单（D1）。

【不易】判定表是**数据**，新增网段只需往 `FORBIDDEN_NETWORKS` 加一条。
【变易】开关 `CP_SSRF_GUARD=0` 可整体回滚到"改动前行为"（不再有任何出站判定）。
【简易】纯标准库（`ipaddress` / `socket` / `urllib.parse`）；连接期拦截用一次性
        幂等 patch，作用域外零开销。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("agent.guardrails.ssrf_guard")

# ════════════════════════════════════════════════════════════
#  开关
# ════════════════════════════════════════════════════════════

#: 总开关（默认开）。置 0/false/no/off ⇒ 本模块所有判定返回"放行"，
#: 且连接期拦截**完全不安装** ⇒ 出站行为回到改动前。
ENV_SSRF_GUARD = "CP_SSRF_GUARD"

#: 严格 DNS 模式：解析不出结论时**也**拒绝（默认关，理由见模块 docstring）
ENV_SSRF_STRICT_DNS = "CP_SSRF_STRICT_DNS"

#: 域名/IP 白名单（逗号分隔）。命中的目标**跳过网段判定**（但仍做语法检查）。
#: 用途：部署侧确有必须访问的内网服务（如自建检索服务）时**显式**开口子，
#: 而不是把整个守卫关掉。
ENV_SSRF_ALLOW_HOSTS = "CP_SSRF_ALLOW_HOSTS"

#: DNS 解析超时（秒）——只作用于 `getaddrinfo` 的调用点（线程 + join 实现，
#: 不引第三方依赖）。默认 5s。
ENV_SSRF_DNS_TIMEOUT = "CP_SSRF_DNS_TIMEOUT"

_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})

#: 拦截结论（审计/返回体共用，稳定文案）
BLOCKED_ERROR_PREFIX = "出站被 SSRF 守卫拒绝"

#: 允许的 URL 协议（其余一律拒：`file:` / `gopher:` / `dict:` 等是 SSRF 的经典载荷）
ALLOWED_SCHEMES: Tuple[str, ...] = ("http", "https")

#: 视为"回环/私有"的主机后缀（无点单段名也在此列）
_LOCAL_HOST_SUFFIXES: Tuple[str, ...] = (
    ".local", ".localhost", ".internal", ".lan", ".corp", ".intranet", ".home.arpa",
)
_LOCAL_BARE_HOSTS: Tuple[str, ...] = ("localhost", "localhost.localdomain")


# ════════════════════════════════════════════════════════════
#  禁止网段（数据；新增一条即可）
# ════════════════════════════════════════════════════════════

#: 明确禁止出站的网段 → 中文说明。
#: 【为什么这么长】TASK-07 §3 第 2 步第 2 项要求"至少覆盖"的清单在这里逐条落地，
#: 并补上几个同类（TEST-NET / 组播 / 保留段）——它们同样是"不该被 LLM 打"的地址，
#: 且 `ipaddress.is_private` 对其中一部分**返回 False**（如 192.88.99.0/24），
#: 只依赖 `is_private` 会漏。
FORBIDDEN_NETWORKS: Tuple[Tuple[str, str], ...] = (
    # ── IPv4 ──
    ("0.0.0.0/8", "本网络（0/8，含 0.0.0.0）"),
    ("10.0.0.0/8", "私有网段 A 类"),
    ("100.64.0.0/10", "运营商级 NAT（CGNAT）"),
    ("127.0.0.0/8", "环回"),
    ("169.254.0.0/16", "链路本地（**含云元数据 169.254.169.254**）"),
    ("172.16.0.0/12", "私有网段 B 类（含 172.17–172.31，Docker 默认桥接）"),
    ("192.0.0.0/24", "IETF 协议分配"),
    ("192.0.2.0/24", "TEST-NET-1"),
    ("192.88.99.0/24", "6to4 中继任播"),
    ("192.168.0.0/16", "私有网段 C 类"),
    ("198.18.0.0/15", "基准测试网段"),
    ("198.51.100.0/24", "TEST-NET-2"),
    ("203.0.113.0/24", "TEST-NET-3"),
    ("224.0.0.0/4", "组播"),
    ("240.0.0.0/4", "保留（含 255.255.255.255 广播）"),
    # ── IPv6 ──
    ("::/128", "未指定地址"),
    ("::1/128", "环回"),
    ("fc00::/7", "唯一本地地址（ULA）"),
    ("fe80::/10", "链路本地"),
    ("ff00::/8", "组播"),
    ("2001:db8::/32", "文档用网段"),
    ("64:ff9b::/96", "NAT64 前缀（内嵌 IPv4，须按内嵌地址判定）"),
    ("2002::/16", "6to4（内嵌 IPv4，须按内嵌地址判定）"),
    # 【为什么单独列 `::ffff:0:0/96`】IPv4-mapped IPv6（`::ffff:127.0.0.1`）
    # 是绕过"只看点分十进制"的经典写法；`ipaddress` 的 `is_loopback` 对
    # `::ffff:127.0.0.1` 返回 **False**，必须显式按内嵌 IPv4 再判一次。
    ("::ffff:0:0/96", "IPv4 映射地址（须按内嵌 IPv4 判定）"),
)

_FORBIDDEN_PARSED: Tuple[Tuple[Any, str], ...] = tuple(
    (ipaddress.ip_network(cidr, strict=False), note) for cidr, note in FORBIDDEN_NETWORKS
)


# ════════════════════════════════════════════════════════════
#  异常与结论
# ════════════════════════════════════════════════════════════


class SsrfBlocked(PermissionError):
    """出站被 SSRF 守卫拒绝（连接期与预检共用同一异常类型）"""

    def __init__(self, message: str, *, verdict: Any = None) -> None:
        super().__init__(message)
        self.verdict = verdict


@dataclass
class SsrfVerdict:
    """一次出站判定的完整结论

    Attributes:
        allowed: 是否放行。
        reason: 人类可读原因（放行时为空或提示）。
        host: 归一后的主机名。
        scheme: 协议。
        category: 拒绝类别（`scheme` / `host_syntax` / `ip_literal` /
            `dns_resolved` / `dns_failed` / `connect_peer` /
            `allowlist_hit` / `guard_error` / `disabled`）。
        resolved_ips: 本次解析出的 IP（脱敏无必要——IP 不是秘密，且是判定证据）。
        pinned_ip: 本次**钉住**用于连接的 IP（防 rebinding 的产物）。
        note: 命中的网段说明。
    """

    allowed: bool
    reason: str = ""
    host: str = ""
    scheme: str = ""
    category: str = ""
    resolved_ips: Tuple[str, ...] = ()
    pinned_ip: str = ""
    note: str = ""
    #: 拒绝是否因为"目标是内网/本机地址"（区别于协议/语法错误）
    #: 【为什么单独给一个布尔】`agent/tools/browser_tools.py` 的既有对外文案是
    #: "禁止访问内网地址"；调用方需要据此保留这条**稳定文案**（同时把精确原因附在后面），
    #: 否则"内网拦截"这条语义在错误信息里就找不到了（既有的 8 条用例正是靠它断言）。
    is_internal: bool = False
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed, "category": self.category,
            "host": self.host, "scheme": self.scheme, "reason": self.reason,
            "note": self.note, "resolved_ips": list(self.resolved_ips),
            "pinned_ip": self.pinned_ip, "is_internal": self.is_internal,
        }


# ════════════════════════════════════════════════════════════
#  开关读取
# ════════════════════════════════════════════════════════════


def _env_flag(name: str, default: str) -> bool:
    return str(os.environ.get(name, default)).strip().lower() not in _DISABLED_VALUES


def guard_enabled() -> bool:
    """总开关是否启用（默认开）"""
    return _env_flag(ENV_SSRF_GUARD, "1")


def strict_dns_enabled() -> bool:
    """解析不出结论时是否也拒绝（默认关）"""
    return _env_flag(ENV_SSRF_STRICT_DNS, "0")


def dns_timeout_seconds() -> float:
    try:
        value = float(str(os.environ.get(ENV_SSRF_DNS_TIMEOUT, "5")).strip() or "5")
    except (TypeError, ValueError):
        value = 5.0
    return value if value > 0 else 5.0


def allowlist() -> Tuple[str, ...]:
    """白名单条目（小写去空白；支持 `host` 与 `*.suffix` 两种形态）

    【为什么白名单是"显式开口子"而不是"默认模式"】本仓库的既定纪律是默认拒绝
    （D1/§10.3 私有网段默认拒绝）。白名单只用于部署侧确有**必须访问的内网服务**
    的场景（如自建检索服务），且必须由运维显式配置 —— 不给"默认允许内网"的选项。
    """
    raw = str(os.environ.get(ENV_SSRF_ALLOW_HOSTS, "") or "")
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


# ════════════════════════════════════════════════════════════
#  主机归一与非标准 IP 写法
# ════════════════════════════════════════════════════════════


def normalize_host(host: Any) -> str:
    """主机名归一：去括号/去尾点/去 zone id/小写

    【为什么必须去尾点】`127.0.0.1.`（FQDN 形态）在 `ipaddress.ip_address` 里
    解析**失败**⇒ 会被当成"域名"⇒ 逃过 IP 字面量判定，而操作系统会把它解析成
    回环地址。这是 TASK-07 §3 第 2 步第 2 项点名的一类绕过。
    """
    text = str(host or "").strip().strip("[]").lower()
    if text.endswith("."):
        text = text.rstrip(".")
    # IPv6 zone id（fe80::1%25eth0 / fe80::1%eth0）
    if "%" in text:
        text = text.split("%", 1)[0]
    return text


def _parse_int_part(part: str) -> Optional[int]:
    """按 inet_aton 的进制规则解析一段整数（0x 十六进制 / 前导 0 八进制 / 十进制）"""
    text = str(part or "").strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        if len(text) > 1 and text.startswith("0"):
            return int(text, 8)
        return int(text, 10)
    except ValueError:
        return None


def parse_ip_literal(host: Any) -> Optional[Any]:
    """把主机名解析成 IP 对象（**含全部非标准写法**）；不是 IP 字面量时返回 None

    覆盖（TASK-07 §3 第 2 步第 2 项点名）：
        · 标准点分十进制      `127.0.0.1`
        · 十进制整数          `2130706433`
        · 十六进制            `0x7f000001`
        · 八进制段            `0177.0.0.1`
        · 十六进制段          `0x7f.0.0.1`
        · 少段（inet_aton）   `127.1` → `127.0.0.1`
        · 带尾点              `127.0.0.1.`
        · IPv6（含压缩形式）  `::1` / `fe80::1`
        · IPv4 映射 IPv6      `::ffff:127.0.0.1`
    归一后**一律返回 `IPv4Address` 或 `IPv6Address`**，交给网段判定。

    【为什么不用 `socket.inet_aton` 直接算】它对 `2130706433` 也会成功，但**不做**
    IPv6 与 `0x` 段；且它是 C 实现，行为随平台（Windows 历史上对少段写法更宽松）。
    Python 侧显式实现可单测、可解释，且两台机器结果一致（D7）。
    """
    text = normalize_host(host)
    if not text:
        return None

    # 1) 标准写法（含全部 IPv6 形态）
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass

    # 2) IPv4 的非标准写法（点分 + 各段可带进制 + 允许少于 4 段）
    if ":" in text:
        return None                      # 含冒号但不是合法 IPv6 ⇒ 非字面量
    parts = text.split(".")
    if not parts or len(parts) > 4:
        return None
    values: List[int] = []
    for part in parts:
        value = _parse_int_part(part)
        if value is None or value < 0:
            return None
        values.append(value)

    # inet_aton 语义：最后一段占用剩余字节数（1 段 → 4 字节，2 段 → 3+1，3 段 → 2+1+1）
    if len(values) == 1:
        if values[0] > 0xFFFFFFFF:
            return None
        packed = values[0]
    else:
        head, tail = values[:-1], values[-1]
        remaining = 4 - len(head)
        for item in head:
            if item > 0xFF:
                return None
        if tail > (0x100 ** remaining) - 1:
            return None
        packed = 0
        for byte in head:
            packed = (packed << 8) | byte
        packed = (packed << (8 * remaining)) | tail
    try:
        return ipaddress.IPv4Address(packed)
    except (ipaddress.AddressValueError, ValueError):
        return None


def _embedded_ipv4(addr: Any) -> Optional[Any]:
    """取出 IPv6 地址里**内嵌**的 IPv4（映射 / 6to4 / NAT64）；无则 None

    【为什么必须考虑内嵌】`::ffff:127.0.0.1` 与 `2002:7f00:1::` 在字面上都不是
    环回地址，但落到内核就是 `127.0.0.1` —— "只看字面"是这类绕过的根因。
    """
    if not isinstance(addr, ipaddress.IPv6Address):
        return None
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    # 6to4：2002:AABB:CCDD::/48 —— 第 2、3 组即内嵌 IPv4
    if addr.sixtofour is not None:
        return addr.sixtofour
    # NAT64：64:ff9b::/96 —— 末 32 位即内嵌 IPv4（`ipaddress` 无属性，手工取）
    if addr in ipaddress.ip_network("64:ff9b::/96"):
        packed = int(addr) & 0xFFFFFFFF
        return ipaddress.IPv4Address(packed)
    return None


def forbidden_reason(addr: Any) -> Optional[str]:
    """地址是否落在禁止网段；命中返回中文说明，否则 None

    【不易】先判内嵌 IPv4（映射/6to4/NAT64），再判字面 —— 顺序反了会让
    `::ffff:127.0.0.1` 在 `::ffff:0:0/96` 这条上被"命中但不说明真实原因"，
    排查时看不懂为什么被拦。
    """
    if addr is None:
        return None
    embedded = _embedded_ipv4(addr)
    if embedded is not None and embedded != addr:
        inner = forbidden_reason(embedded)
        if inner is not None:
            return f"{addr}（内嵌 IPv4 {embedded}：{inner}）"
    for network, note in _FORBIDDEN_PARSED:
        try:
            if addr.version == network.version and addr in network:
                return note
        except TypeError:
            continue
    return None


def host_is_local_name(host: Any) -> Optional[str]:
    """主机名是否是"显然是本机"的名字（`localhost` / `*.local` 等）；否返回 None"""
    text = normalize_host(host)
    if not text:
        return None
    if text in _LOCAL_BARE_HOSTS:
        return "本机名（localhost）"
    for suffix in _LOCAL_HOST_SUFFIXES:
        if text.endswith(suffix):
            return f"本地域名后缀 {suffix}"
    return None


def allowlist_hit(host: Any) -> bool:
    """白名单命中判定（`host` 精确 或 `*.suffix` 后缀）"""
    text = normalize_host(host)
    if not text:
        return False
    for entry in allowlist():
        if entry.startswith("*."):
            if text == entry[2:] or text.endswith(entry[1:]):
                return True
        elif text == entry:
            return True
    return False


# ════════════════════════════════════════════════════════════
#  DNS 解析（带超时，不引第三方依赖）
# ════════════════════════════════════════════════════════════


def resolve_host(host: Any, *, port: Optional[int] = None) -> List[str]:
    """解析主机名 → 去重后的 IP 字符串列表（失败抛 `socket.gaierror`）

    【为什么加超时】`getaddrinfo` 在 DNS 不可达时可能阻塞数十秒，而它位于
    **每个出站请求之前** ⇒ 直接把工具超时吃掉。用一个守护线程 + join 超时实现，
    超时后抛 `gaierror`（与"解析失败"同一处置，不引入新的失败语义）。
    【为什么不用 `socket.setdefaulttimeout`】那是**进程级**全局设置，会改变
    其它模块所有 socket 的行为（D4 风险）。
    """
    text = normalize_host(host)
    if not text:
        raise socket.gaierror(socket.EAI_NONAME, "空主机名")
    target_port = int(port) if port else None
    result: Dict[str, Any] = {}

    def _work() -> None:
        try:
            infos = socket.getaddrinfo(text, target_port, proto=socket.IPPROTO_TCP)
            result["ok"] = [str(info[4][0]) for info in infos]
        except Exception as exc:  # noqa: BLE001  交给主线程按 gaierror 处置
            result["err"] = exc

    worker = threading.Thread(target=_work, name="ssrf-dns", daemon=True)
    worker.start()
    worker.join(dns_timeout_seconds())
    if worker.is_alive():
        raise socket.gaierror(socket.EAI_AGAIN, f"DNS 解析超时（>{dns_timeout_seconds():.0f}s）")
    if "err" in result:
        raise result["err"]
    seen: List[str] = []
    for ip in result.get("ok") or ():
        if ip not in seen:
            seen.append(ip)
    if not seen:
        raise socket.gaierror(socket.EAI_NONAME, "解析结果为空")
    return seen


# ════════════════════════════════════════════════════════════
#  判定
# ════════════════════════════════════════════════════════════


def check_host(host: Any, *, resolve: bool = True,
               port: Optional[int] = None) -> SsrfVerdict:
    """判定单个主机（**浏览器 / MCP / 任意非 HttpClient 调用点复用同一口径**）

    Args:
        host: 主机名或 IP 字面量。
        resolve: 是否做 DNS 解析后校验（False 时只判字面量与本地名）。

    Returns:
        `SsrfVerdict`（**不抛**；内部异常 ⇒ `allowed=False`，fail-closed）。
    """
    if not guard_enabled():
        return SsrfVerdict(allowed=True, category="disabled", reason="SSRF 守卫未启用",
                           host=normalize_host(host))
    try:
        text = normalize_host(host)
        if not text:
            return SsrfVerdict(allowed=False, category="host_syntax",
                               reason="出站目标缺少主机名", host=text)
        if allowlist_hit(text):
            return SsrfVerdict(allowed=True, category="allowlist_hit", host=text,
                               reason=f"命中 SSRF 白名单（{ENV_SSRF_ALLOW_HOSTS}）")

        literal = parse_ip_literal(text)
        if literal is not None:
            note = forbidden_reason(literal)
            if note is not None:
                return SsrfVerdict(
                    allowed=False, category="ip_literal", host=text, note=note,
                    is_internal=True,
                    resolved_ips=(str(literal),), pinned_ip=str(literal),
                    reason=f"目标 IP {literal} 属禁止网段（{note}）——私有/保留地址不得出站")
            return SsrfVerdict(allowed=True, category="ip_literal", host=text,
                               resolved_ips=(str(literal),), pinned_ip=str(literal),
                               reason="公网 IP 字面量")

        note = host_is_local_name(text)
        if note is not None:
            return SsrfVerdict(allowed=False, category="host_syntax", host=text,
                               note=note, is_internal=True,
                               reason=f"目标主机名 {text} 指向本机（{note}）")

        if not resolve:
            return SsrfVerdict(allowed=True, category="unresolved_skipped", host=text,
                               reason="未做 DNS 解析校验")

        try:
            ips = resolve_host(text, port=port)
        except Exception as exc:  # noqa: BLE001  gaierror / 超时 / 其它解析异常
            if strict_dns_enabled():
                return SsrfVerdict(
                    allowed=False, category="dns_failed", host=text,
                    reason=(f"无法解析主机 {text}（{type(exc).__name__}: {exc}），而 "
                            f"{ENV_SSRF_STRICT_DNS}=1 ⇒ 按 fail-closed 拒绝"))
            return SsrfVerdict(
                allowed=True, category="dns_failed", host=text,
                reason=(f"主机 {text} 解析失败（{type(exc).__name__}）——放行由连接期"
                        f"第二道防线接管（真地址仍会被校验）"))

        bad: List[Tuple[str, str]] = []
        for ip in ips:
            addr = parse_ip_literal(ip)
            why = forbidden_reason(addr)
            if why is not None:
                bad.append((ip, why))
        if bad:
            detail = "；".join(f"{ip}（{why}）" for ip, why in bad)
            return SsrfVerdict(
                allowed=False, category="dns_resolved", host=text,
                resolved_ips=tuple(ips), note=bad[0][1], is_internal=True,
                reason=f"主机 {text} 解析到禁止网段地址：{detail}")
        return SsrfVerdict(allowed=True, category="dns_resolved", host=text,
                           resolved_ips=tuple(ips),
                           pinned_ip=(ips[0] if len(ips) == 1 else ""),
                           reason="公网主机")
    except Exception as exc:  # noqa: BLE001  判定本身出错 ⇒ fail-closed（E3）
        logger.warning("[ssrf] 判定异常，按 fail-closed 拒绝: %s: %s",
                       type(exc).__name__, exc)
        return SsrfVerdict(
            allowed=False, category="guard_error", host=normalize_host(host),
            reason=(f"SSRF 守卫内部错误（{type(exc).__name__}: {exc}）——"
                    f"无法证明目标安全 ⇒ fail-closed 拒绝"))


def check_url(url: Any, *, resolve: bool = True) -> SsrfVerdict:
    """判定一个完整 URL 能否出站（**所有出站调用点的统一入口**）

    只做语法 + 主机判定，**不发网络请求**。
    """
    raw = str(url or "")
    if not guard_enabled():
        return SsrfVerdict(allowed=True, category="disabled", reason="SSRF 守卫未启用")
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        return SsrfVerdict(allowed=False, category="scheme",
                           reason=f"URL 无法解析（{exc}）")
    scheme = str(parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return SsrfVerdict(allowed=False, category="scheme", scheme=scheme,
                           reason=(f"协议 {scheme or '（空）'} 不在允许列表 "
                                   f"{list(ALLOWED_SCHEMES)} 内（file/gopher/dict 一类"
                                   f"协议是 SSRF 的经典载荷）"))
    try:
        host = parts.hostname or ""
    except ValueError as exc:
        return SsrfVerdict(allowed=False, category="host_syntax", scheme=scheme,
                           reason=f"URL 主机名无法解析（{exc}）")
    port: Optional[int] = None
    try:
        port = parts.port
    except ValueError:
        port = None
    verdict = check_host(host, resolve=resolve, port=port)
    verdict.scheme = scheme
    return verdict


def block_result(verdict: SsrfVerdict, url: str = "") -> Dict[str, Any]:
    """拦截结果（与 `HttpClient._error_result` 同形，调用方可直接返回）"""
    return {
        "ok": False,
        "status_code": None,
        "headers": {},
        "content": None,
        "text": None,
        "content_length": 0,
        "url": url,
        "elapsed": 0.0,
        "error": f"{BLOCKED_ERROR_PREFIX}｜{verdict.reason}",
        "blocked": True,
        "blocked_by": "guardrails.ssrf_guard",
        "ssrf_category": verdict.category,
        "ssrf_note": verdict.note,
        "host": verdict.host,
        "network_action_taken": False,
    }


# ════════════════════════════════════════════════════════════
#  审计（复用一个函数，避免三处各写一遍）
# ════════════════════════════════════════════════════════════


def audit_block(verdict: SsrfVerdict, *, url: str = "", surface: str = "",
                capability: str = "") -> None:
    """拦截入审计（best-effort；**不含完整 URL**——URL 可能带凭据查询串）

    【为什么只记 host 不记完整 URL】§3 第 5 步明确要求"拦截事件里不得写入完整
    敏感 URL"。查询串里常有 `?token=…`／签名参数，故只留 scheme+host，
    路径与查询串**一律不落**。
    """
    try:
        from agent.audit.facade import audit
        audit.record(
            "egress_blocked",
            actor="guardrails.ssrf_guard",
            subject=f"egress:{verdict.host or '-'}",
            payload={
                "category": verdict.category,
                "scheme": verdict.scheme,
                "host": verdict.host,
                "note": verdict.note,
                "resolved_ips": list(verdict.resolved_ips)[:8],
                "surface": str(surface or ""),
                "capability": str(capability or ""),
                "url_recorded": False,
                "verdict": "block",
                "enforced": True,
                "network_action_taken": False,
            },
            source="agent",
        )
    except Exception as exc:  # noqa: BLE001  审计失败不影响拦截本身
        logger.debug("[ssrf] 拦截审计写入失败: %s", exc)


# ════════════════════════════════════════════════════════════
#  第二道防线：连接期地址校验（DNS rebinding / 302 跳内网的真实落点）
# ════════════════════════════════════════════════════════════

_SCOPE = threading.local()

#: patch 安装状态（一次性、幂等）
_PATCH_LOCK = threading.Lock()
_PATCH_INSTALLED = False
_ORIGINAL_CREATE_CONNECTION: Any = None


def _scope_depth() -> int:
    return int(getattr(_SCOPE, "depth", 0) or 0)


@contextmanager
def guard_scope() -> Iterator[None]:
    """进入"连接期校验"作用域（**仅此作用域内**启用第二道防线）

    线程局部（`threading.local`）：`requests` 在调用线程内建连，故作用域随线程走。
    """
    _SCOPE.depth = _scope_depth() + 1
    try:
        yield
    finally:
        _SCOPE.depth = max(0, _scope_depth() - 1)


def scope_active() -> bool:
    """当前线程是否处于连接期校验作用域（测试与诊断用）"""
    return _scope_depth() > 0


def validate_peer_address(address: Any) -> Optional[str]:
    """校验**即将连接**的地址；禁止时返回原因，允许时 None

    【这是反 DNS rebinding 的唯一可靠落点】地址已经由 libc 解析完毕、socket
    尚未创建 —— 此刻校验的地址**就是**内核要连的地址，攻击者无法再换一次。
    """
    if not guard_enabled() or not scope_active():
        return None
    try:
        host = address[0] if isinstance(address, (tuple, list)) and address else address
        text = str(host or "")
        literal = parse_ip_literal(text)
        if literal is None:
            # host 不是 IP（极端情况：调用方直接传主机名）⇒ 自己解析一次再判
            try:
                resolved = resolve_host(text)
            except Exception:  # noqa: BLE001  解析失败交由原连接逻辑报错
                return None
            for ip in resolved:
                why = forbidden_reason(parse_ip_literal(ip))
                if why is not None:
                    return f"目标 {text} 解析到 {ip}（{why}）"
            return None
        if allowlist_hit(text):
            return None
        why = forbidden_reason(literal)
        if why is not None:
            return f"实际连接地址 {literal} 属禁止网段（{why}）"
        return None
    except Exception as exc:  # noqa: BLE001  校验自身异常 ⇒ fail-closed
        return f"连接期地址校验内部错误（{type(exc).__name__}: {exc}）"


def install_connect_guard() -> bool:
    """安装连接期拦截（**幂等**；返回是否已安装）

    【为什么 patch `urllib3.util.connection.create_connection` 而不是换掉 HttpClient
    的 adapter】`requests` 的 HTTPS 证书校验与 SNI 都取自**连接主机名**；若为了
    防 rebinding 把 URL 改成 IP 直连，SNI 就变成 IP、证书校验必然失败
    ⇒ 那等于"为了安全把 HTTPS 全废掉"（E10 会抓住）。patch 建连函数则**只换地址、
    不换主机名**：SNI / Host 头 / 证书校验全部按原样走。

    【为什么同时 patch `urllib3.connection` 里的名字】`urllib3/connection.py` 在
    模块顶部 `from .util.connection import create_connection`，之后调用的是**它自己
    命名空间里的那个名字**。只 patch `util.connection` 不会生效（实测过）。
    """
    global _PATCH_INSTALLED, _ORIGINAL_CREATE_CONNECTION
    with _PATCH_LOCK:
        if _PATCH_INSTALLED:
            return True
        try:
            from urllib3.util import connection as _u3conn
        except Exception as exc:  # noqa: BLE001  urllib3 不可用 ⇒ 退化为只有第一道防线
            logger.warning("[ssrf] urllib3 不可用，连接期拦截未安装（仅第一道防线）: %s", exc)
            return False

        original = _u3conn.create_connection
        _ORIGINAL_CREATE_CONNECTION = original

        def _guarded_create_connection(address, *args, **kwargs):
            reason = validate_peer_address(address)
            if reason is not None:
                logger.warning("[ssrf] 连接期拦截: %s", reason)
                raise SsrfBlocked(f"{BLOCKED_ERROR_PREFIX}｜{reason}")
            return original(address, *args, **kwargs)

        _u3conn.create_connection = _guarded_create_connection
        # urllib3/connection.py 顶部的 `from .util.connection import create_connection`
        # 造成的"名字已绑定"问题：那里的 `create_connection` 是 patch 之前取的值，
        # 必须把同一个包装**再绑定一次**到该模块命名空间上。
        try:
            from urllib3 import connection as _u3c
            _u3c.create_connection = _guarded_create_connection
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ssrf] urllib3.connection 命名空间绑定失败: %s", exc)
        _PATCH_INSTALLED = True
        logger.info("[ssrf] 连接期地址校验已安装（作用域内生效）")
        return True


def uninstall_connect_guard() -> None:
    """卸载连接期拦截（**测试专用**；生产不需要）"""
    global _PATCH_INSTALLED, _ORIGINAL_CREATE_CONNECTION
    with _PATCH_LOCK:
        if not _PATCH_INSTALLED or _ORIGINAL_CREATE_CONNECTION is None:
            return
        try:
            from urllib3.util import connection as _u3conn
            _u3conn.create_connection = _ORIGINAL_CREATE_CONNECTION
        except Exception:  # noqa: BLE001
            pass
        try:
            from urllib3 import connection as _u3c
            _u3c.create_connection = _ORIGINAL_CREATE_CONNECTION
        except Exception:  # noqa: BLE001
            pass
        _PATCH_INSTALLED = False


def ssrf_state() -> Dict[str, Any]:
    """守卫状态快照（诊断/验收报告）"""
    return {
        "enabled": guard_enabled(),
        "strict_dns": strict_dns_enabled(),
        "allowlist": list(allowlist()),
        "dns_timeout_seconds": dns_timeout_seconds(),
        "forbidden_network_count": len(FORBIDDEN_NETWORKS),
        "forbidden_networks": [cidr for cidr, _ in FORBIDDEN_NETWORKS],
        "connect_guard_installed": _PATCH_INSTALLED,
        "scope_active": scope_active(),
        "allowed_schemes": list(ALLOWED_SCHEMES),
    }


def check_and_pin_sequence(host: Any, *, samples: Sequence[Any] = ()) -> List[SsrfVerdict]:
    """对一串主机做判定（**测试辅助**：模拟多跳/多次解析）

    生产代码不需要它；保留是为了让"302 每一跳都复检"与"多 A 记录全查"
    这两条不变量能用一行写下断言。
    """
    return [check_host(item) for item in ([host] + list(samples))]


__all__ = [
    "ENV_SSRF_GUARD", "ENV_SSRF_STRICT_DNS", "ENV_SSRF_ALLOW_HOSTS",
    "ENV_SSRF_DNS_TIMEOUT", "BLOCKED_ERROR_PREFIX", "ALLOWED_SCHEMES",
    "FORBIDDEN_NETWORKS", "SsrfBlocked", "SsrfVerdict",
    "guard_enabled", "strict_dns_enabled", "dns_timeout_seconds", "allowlist",
    "normalize_host", "parse_ip_literal", "forbidden_reason", "host_is_local_name",
    "allowlist_hit", "resolve_host",
    "check_host", "check_url", "block_result", "audit_block",
    "guard_scope", "scope_active", "install_connect_guard", "uninstall_connect_guard",
    "validate_peer_address", "ssrf_state", "check_and_pin_sequence",
]
