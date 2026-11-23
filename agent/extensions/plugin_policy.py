"""第三方插件包的安装/加载策略（P0 安全收口，2026-11-22）。

【为什么单独一个叶子模块】
- 安装、加载、启停、UI 展示、并发闸门都要读同一组环境变量。
  分散读会重演本仓“同一口径抄多份”的老问题（§M-40）。
- 本模块只做**读取 + 判定**，不碰文件、不 import 插件代码，便于单测。

【默认值为什么是“关”】
- 当前部署 0 个第三方插件包；插件包一旦加载，就在宿主主进程里以完整权限运行
  （见 plugins_installer.load_plugin），依赖还会 pip install 进宿主环境。
- 在“进程隔离 + 签名/白名单”完成之前，默认关闭是零业务影响的唯一安全默认。
- 需要开发调试时显式设 CP_PLUGIN_PACKAGES_ENABLED=1，并配 CP_PLUGIN_INSTALL_ALLOWLIST。

【白名单语义】
- 逗号分隔，大小写不敏感；每一项是完整来源，如 github:owner/repo、local:/path。
- 只做**精确匹配**；不做 URL 前缀匹配，也不接受未实现的 @ref 变体。
- 白名单为空 = 一律拒绝；宁可让调试者显式登记，也不做“空=全放行”。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Tuple

ENV_PLUGIN_PACKAGES_ENABLED = "CP_PLUGIN_PACKAGES_ENABLED"
ENV_PLUGIN_CLIENT_UI_ENABLED = "CP_PLUGIN_CLIENT_UI_ENABLED"
ENV_PLUGIN_INSTALL_ALLOWLIST = "CP_PLUGIN_INSTALL_ALLOWLIST"
ENV_PLUGIN_MAX_CONCURRENT = "CP_PLUGIN_MAX_CONCURRENT"
ENV_PLUGIN_PROCESS_ISOLATION = "CP_PLUGIN_PROCESS_ISOLATION"
ENV_PLUGIN_PROCESS_TIMEOUT = "CP_PLUGIN_PROCESS_TIMEOUT_SEC"
ENV_PLUGIN_SIGNATURE_MODE = "CP_PLUGIN_SIGNATURE_MODE"
ENV_PLUGIN_SANDBOX = "CP_PLUGIN_SANDBOX"
ENV_PLUGIN_SANDBOX_MEMORY_MB = "CP_PLUGIN_SANDBOX_MEMORY_MB"
ENV_PLUGIN_SANDBOX_MAX_PROCESSES = "CP_PLUGIN_SANDBOX_MAX_PROCESSES"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DEFAULT_MAX_CONCURRENT = 4
_MAX_CONCURRENT_CAP = 64
_DEFAULT_PROCESS_TIMEOUT_SEC = 30.0
_MAX_PROCESS_TIMEOUT_SEC = 300.0
_SIGNATURE_MODES = ("off", "warn", "enforce")
_DEFAULT_SIGNATURE_MODE = "enforce"
_SANDBOX_MODES = ("off", "appcontainer")
_DEFAULT_SANDBOX_MEMORY_MB = 512
_MIN_SANDBOX_MEMORY_MB = 64
_MAX_SANDBOX_MEMORY_MB = 8192
_DEFAULT_SANDBOX_MAX_PROCESSES = 8
_MAX_SANDBOX_PROCESSES_CAP = 64

#: 受信公钥表固定路径（不提供环境变量改信任根；改它必须走代码 review）。
TRUSTED_KEYS_PATH = Path(__file__).resolve().parents[2] / "config" / "plugin_trusted_keys.json"


def _env_bool(name: str, default: bool = False) -> bool:
    """读布尔开关；未设置/非法 ⇒ default（不抛异常）。"""
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUTHY


def plugin_packages_enabled() -> bool:
    """第三方插件包安装/加载总开关（默认关）。"""
    return _env_bool(ENV_PLUGIN_PACKAGES_ENABLED, False)


def plugin_client_ui_enabled() -> bool:
    """插件自带前端 UI（client_slot）总开关（默认关；隔离未完成）。"""
    return _env_bool(ENV_PLUGIN_CLIENT_UI_ENABLED, False)


def install_allowlist() -> Tuple[str, ...]:
    """来源白名单（小写、去空）；空元组表示“一个都不允许”。"""
    raw = str(os.environ.get(ENV_PLUGIN_INSTALL_ALLOWLIST, "") or "")
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def split_github_ref(source: str) -> Tuple[str, str]:
    """拆 github:owner/repo@ref → (github:owner/repo, ref)；非 github 原样返回。"""
    s = str(source or "").strip()
    if s.lower().startswith("github:") and "@" in s:
        base, ref = s.rsplit("@", 1)
        return base, ref.strip()
    return s, ""


_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def valid_github_ref(ref: str) -> bool:
    """插件安装 ref 只接受分支/标签/commit 形态的安全字符。"""
    r = str(ref or "").strip()
    if not r or r.startswith("-") or ".." in r or r.endswith("/") or r.endswith(".lock"):
        return False
    return bool(_REF_RE.match(r))


def source_allowed(source: str) -> bool:
    """来源是否在白名单内。

    只做精确匹配；不做 URL 前缀匹配（https://a/b 匹配 https://a/bc 是经典越权），
    也不接受当前安装器并不支持的 @ref 变体。
    """
    allow = install_allowlist()
    if not allow:
        return False
    s, ref = split_github_ref(source)
    s = s.lower()
    if not s:
        return False
    if ref and not valid_github_ref(ref):
        return False
    return s in set(allow)


def plugin_process_isolation_enabled() -> bool:
    """第三方插件包是否走独立子进程（默认开；关 = 回退旧的进程内 import）。"""
    return _env_bool(ENV_PLUGIN_PROCESS_ISOLATION, True)


def process_timeout() -> float:
    """单次插件工具调用的墙钟上界（秒）；超时即杀子进程。"""
    raw = str(os.environ.get(ENV_PLUGIN_PROCESS_TIMEOUT, "") or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_PROCESS_TIMEOUT_SEC
    except (TypeError, ValueError):
        value = _DEFAULT_PROCESS_TIMEOUT_SEC
    return max(1.0, min(value, _MAX_PROCESS_TIMEOUT_SEC))


def signature_mode() -> str:
    """插件包签名校验模式：off / warn / enforce（默认 enforce，非法值也按 enforce）。

    - off：不验签（仅排障；等于放弃供应链校验）
    - warn：验签失败只记日志，仍放行（灰度期）
    - enforce：缺失/失败一律拒绝安装与加载
    """
    raw = str(os.environ.get(ENV_PLUGIN_SIGNATURE_MODE, "") or "").strip().lower()
    return raw if raw in _SIGNATURE_MODES else _DEFAULT_SIGNATURE_MODE


def sandbox_mode() -> str:
    """插件包子进程的 OS 级沙箱：off（默认，仅进程边界）/ appcontainer（Windows）。

    appcontainer 需要 Windows 10+ 与管理员首次建 profile；不可用时 fail-closed，
    绝不悄悄回退到无沙箱进程。
    """
    raw = str(os.environ.get(ENV_PLUGIN_SANDBOX, "") or "").strip().lower()
    return raw if raw in _SANDBOX_MODES else "off"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(low), min(int(value), int(high)))


def sandbox_memory_mb() -> int:
    """每个 AppContainer 插件子进程的内存上限（MB）；默认 512，范围 64–8192。"""
    return _env_int(ENV_PLUGIN_SANDBOX_MEMORY_MB, _DEFAULT_SANDBOX_MEMORY_MB,
                    _MIN_SANDBOX_MEMORY_MB, _MAX_SANDBOX_MEMORY_MB)


def sandbox_max_processes() -> int:
    """每个 AppContainer 插件子进程可派生的进程数上限；默认 8，范围 1–64。"""
    return _env_int(ENV_PLUGIN_SANDBOX_MAX_PROCESSES,
                    _DEFAULT_SANDBOX_MAX_PROCESSES, 1, _MAX_SANDBOX_PROCESSES_CAP)


def max_concurrent() -> int:
    """每个插件允许的并发请求数；0 = 关闭插件级并发闸门。"""
    raw = str(os.environ.get(ENV_PLUGIN_MAX_CONCURRENT, "") or "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_MAX_CONCURRENT
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_CONCURRENT
    return max(0, min(value, _MAX_CONCURRENT_CAP))
