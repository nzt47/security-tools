"""离线包下载定位（S5 / portability：把"可带走"从命令行推进到产品面）

【解决什么（不这样会怎样）】
    `scripts/build_offline_pack.py` 能把 bundle + wheelhouse 打成一个 tar.gz，但那是个
    **命令行产物**：运维构建完之后，产品面没有任何入口把它交付出去 —— 使用者仍然要
    "去那台机器上找文件"。本模块把"按分身名定位一个已构建离线包"收敛成**纯逻辑**，
    路由只做"回文件"，判定与错误码都在这里，便于单测（不 import app_server）。

【fail-closed（不假装有包）】
    · 目录未配置 ⇒ `E_OFFLINE_PACK_NOT_CONFIGURED`（不是"没有这个分身"）；
    · 名字非法（含路径分隔/`..`/控制字符）⇒ `E_OFFLINE_PACK_BAD_NAME`；
    · 解析后的真实路径必须仍在配置目录内（防符号链接/穿越）；
    · 文件不存在 ⇒ `E_OFFLINE_PACK_NOT_BUILT`，并给出构建命令提示；
    四者都**不**回 200/空文件，也绝不静默回落到别的文件。

【依赖纪律】纯标准库；不 import Flask / app_server（路由层只消费本模块）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "ENV_OFFLINE_PACK_DIR",
    "PACK_SUFFIX",
    "OfflinePackError",
    "offline_pack_dir",
    "safe_pack_name",
    "resolve_pack",
    "pack_status",
]

#: 离线包目录环境变量（在 agent/settings/registry.py 登记；空=未配置）
ENV_OFFLINE_PACK_DIR = "CP_SUBAGENT_OFFLINE_PACK_DIR"
#: 离线包后缀（与 scripts/build_offline_pack.py 的产物一致）
PACK_SUFFIX = ".tar.gz"


class OfflinePackError(Exception):
    """离线包不可下载（fail-closed；带稳定错误码供路由映射 HTTP 状态）"""

    code = "E_OFFLINE_PACK"

    def __init__(self, message: str, *, code: str = "E_OFFLINE_PACK") -> None:
        super().__init__(message)
        self.code = str(code or "E_OFFLINE_PACK")


def offline_pack_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    """配置的离线包目录（缺失/空白 ⇒ 空串；不抛）"""
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_OFFLINE_PACK_DIR, "") or "").strip()


def safe_pack_name(name: Any) -> str:
    """分身名 → 安全的包文件名（非法返回空串，调用方 fail-closed）

    拒绝：空 / `.` / `..` / 含 `/` 或 `\` / 含 `..` / 含 NUL 或其它控制字符。
    **不做**替换式"清洗"（那会悄悄把 a/../b 变成别的名字，等于猜测调用方意图）。
    """
    raw = str(name or "").strip()
    if not raw or raw in (".", "..") or ".." in raw:
        return ""
    if "/" in raw or "\\" in raw:
        return ""
    if any(ord(ch) < 32 or ch == "\x7f" for ch in raw):
        return ""
    return raw


def resolve_pack(name: Any, *, pack_dir: Optional[str] = None,
                 environ: Optional[Mapping[str, str]] = None) -> str:
    """按分身名定位离线包绝对路径（不可用即抛 ``OfflinePackError``）

    Raises:
        OfflinePackError: 目录未配置 / 名字非法 / 文件不存在 / 越出配置目录。
    """
    directory = str(pack_dir if pack_dir is not None else offline_pack_dir(environ)).strip()
    if not directory:
        raise OfflinePackError(
            "离线包目录未配置（%s 为空）：请先运行 scripts/build_offline_pack.py 并用该变量指向产物目录" % ENV_OFFLINE_PACK_DIR,
            code="E_OFFLINE_PACK_NOT_CONFIGURED")
    safe = safe_pack_name(name)
    if not safe:
        raise OfflinePackError("非法分身名（拒绝路径穿越/控制字符）",
                               code="E_OFFLINE_PACK_BAD_NAME")
    base = os.path.realpath(directory)
    target = os.path.realpath(os.path.join(base, safe + PACK_SUFFIX))
    if os.path.dirname(target) != base:
        raise OfflinePackError("非法分身名（解析后越出离线包目录）",
                               code="E_OFFLINE_PACK_BAD_NAME")
    if not os.path.isfile(target):
        # **不回显服务端绝对目录**：调用方只需要知道"没构建、怎么构建"；
        # 把配置目录打进响应体等于向任意（已鉴权）调用者泄露服务器路径布局。
        raise OfflinePackError(
            "离线包未构建：%s 不存在（请在联网机运行 scripts/build_offline_pack.py "
            "--pack-dir <目录>；服务端目录由 %s 配置）"
            % (os.path.basename(target), ENV_OFFLINE_PACK_DIR),
            code="E_OFFLINE_PACK_NOT_BUILT")
    return target


def pack_status(name: Any, *, pack_dir: Optional[str] = None,
                environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """可下载性投影（**只回状态，不回绝对路径**；供读面/诊断用，不抛）"""
    try:
        resolve_pack(name, pack_dir=pack_dir, environ=environ)
    except OfflinePackError as exc:
        return {"available": False, "code": exc.code, "reason": str(exc)}
    return {"available": True, "code": "", "reason": ""}

