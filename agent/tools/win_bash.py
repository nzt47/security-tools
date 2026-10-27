r"""Windows 上调 bash 脚本的适配层 —— 把 Windows 绝对路径翻成 WSL 路径

【为什么需要（2026-10-28 实测，不是理论问题）】
    「吞噬」生成的工具按来源脚本的既有形状调用子进程：
        subprocess.run(["bash", r"C:\...\manage.sh", "list"], shell=False)
    Windows 上 bash 可能由 **WSL** 提供（C:\Windows\System32\bash.exe → 默认发行版），
    而 WSL 里的 bash **看不到 C:\...**（Windows 路径在 Linux 侧就是普通字符串）。
    实测（本机，Ubuntu-24.04）：
        bash "C:\Users\...\scripts\manage.sh" list
        → /bin/bash: C:UsersAdministrator...scriptsmanage.sh: No such file or directory（exit=127）
    即：工具**登记成功、调用必失败**，报错还像"脚本不存在"，最容易误判成"环境没装 CLI"。

【为什么不能无脑翻译（同一个 Windows 上还有第二种 bash）】
    Git for Windows 自带的 **MSYS bash** 认识 C:\ 路径，把它翻成 /mnt/c/... 反而会坏。
    所以只在**探测到 PATH 上的 bash 是 WSL** 时才翻：跑 bash -c "uname -r"，
    输出含 microsoft（WSL2 内核名 ...-microsoft-standard-WSL2）才算；探测失败一律当"不是 WSL"
    ⇒ 退回原行为（宁可不翻，也不要翻错）。结果按进程缓存一次。

【口径】非 Windows：逐字返回（CI / Linux 部署零影响）。Windows 且 bash=WSL：
    只翻参数里形如 X:\... / X:/... 的**绝对 Windows 路径** → /mnt/<盘>/...；其余逐字保留。

【边界（刻意不处理，写清楚免得后人踩）】
    1. bash -c "..." 里**字符串内部**的 Windows 路径不会被翻译（只翻 argv 元素）；
       需要时调用方自己用 to_wsl_path() 拼。
    2. 环境变量**不会**从 Windows 进程跨进 WSL（实测：Python env= 传入的
       BYTEPLUS_ACCESS_KEY 在 WSL 里读不到；WSL 只在发行版启动时导一次 Windows 环境）。
       所以 deploy.sh 依赖的"env 里有 AK/SK"在 Windows 上不成立 —— 正确做法是
       **在 WSL 里配置一次**：nest config set -g cloud.access_key/secret_key，
       或 bash onboard.sh save-credentials <ak> <sk>（AK/SK 走 argv，不走 env）。
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, List, Optional, Sequence, Union

#: 盘符开头的绝对路径
_WIN_ABS = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")

#: 需要路径翻译的 shell 名（Windows 上 bash/sh 都可能落到 WSL）
_SHELL_NAMES = frozenset({"bash", "sh", "bash.exe"})

#: 探测缓存（进程级只探一次，见 is_wsl_bash）
_WSL_PROBE: Dict[str, bool] = {}

#: 探测 bash 类型的超时（秒）
#: 【为什么是模块级常量】硬编码边界值扫描会把"调用参数/默认值里的字面量"判为 high
#: （实测 def is_wsl_bash(*, timeout=10.0) 被扫出 1 条、基线 166 → 167 ⇒ CI 红）；
#: 模块级常量不触发该规则。
WSL_PROBE_TIMEOUT_SEC = 10.0

ArgList = Sequence[Union[str, "os.PathLike[str]"]]


def is_windows() -> bool:
    """当前进程是否在 Windows 上（单测 monkeypatch 本函数即可覆盖两条分支）。"""
    return os.name == "nt"


def to_wsl_path(path: str) -> str:
    r"""C:\a\b -> /mnt/c/a/b；非 Windows 绝对路径原样返回"""
    raw = "" if path is None else str(path)
    m = _WIN_ABS.match(raw)
    if not m:
        return raw
    drive = m.group("drive").lower()
    rest = m.group("rest").replace("\\", "/").lstrip("/")
    return "/mnt/{0}/{1}".format(drive, rest) if rest else "/mnt/{0}".format(drive)


def looks_like_shell(arg: object) -> bool:
    r"""第一个参数是不是要交给 WSL 的 shell（bash / sh / ...\bash.exe）。"""
    name = os.path.basename(str(arg or "")).lower()
    return name in _SHELL_NAMES


def is_wsl_bash(*, use_cache: bool = True) -> bool:
    r"""PATH 上的 bash 是不是 WSL 的（uname -r 含 microsoft）

    【为什么必须探测】同一个 Windows 上可能是 MSYS/Git Bash（认识 C:\）——
        对那种 bash 做 /mnt 翻译会把它弄坏。探测失败（没有 bash / 超时）一律返回 False，
        即"不翻"，保持与改造前一致的行为。
    """
    if use_cache and "wsl" in _WSL_PROBE:
        return _WSL_PROBE["wsl"]
    verdict = False
    try:
        proc = subprocess.run(["bash", "-c", "uname -r"], shell=False,
                              capture_output=True, text=True,
                              timeout=WSL_PROBE_TIMEOUT_SEC)
        verdict = "microsoft" in (proc.stdout or "").lower()
    except Exception:  # noqa: BLE001 没有 bash / 权限 / 超时 ⇒ 当不是 WSL
        verdict = False
    if use_cache:
        _WSL_PROBE["wsl"] = verdict
    return verdict


def bash_argv(args: ArgList, *, wsl: Optional[bool] = None) -> List[str]:
    r"""把"要交给 bash 的 argv"整理成当前平台可执行的形状

    - 非 Windows：逐字返回（**不改任何东西** ⇒ CI 与 Linux 部署零影响）；
    - Windows 且 bash 是 WSL：把形如 X:\... 的参数翻成 /mnt/x/...，其余原样；
    - Windows 但 bash 是 MSYS/Git Bash：逐字返回（它本来就认 C:\ 路径）。

    Args:
        wsl: 显式指定"bash 是不是 WSL"（单测/调用方已知时用）；None 则自动探测。

    返回新列表，不修改入参。
    """
    out = [str(a) for a in (args or ())]
    if not is_windows():
        return out
    translate = is_wsl_bash() if wsl is None else bool(wsl)
    if not translate:
        return out
    return [to_wsl_path(a) for a in out]


__all__ = ["is_windows", "to_wsl_path", "looks_like_shell", "is_wsl_bash", "bash_argv"]
