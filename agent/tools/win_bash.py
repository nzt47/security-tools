r"""Windows 上调 bash 脚本的适配层 —— 把 Windows 绝对路径翻成 WSL 路径

【为什么需要（2026-10-28 实测，不是理论问题）】
    「吞噬」生成的工具按来源脚本的既有形状调用子进程：
        subprocess.run(["bash", "<C:\\...\\manage.sh>", "list"], shell=False)
    Windows 上 `bash` 由 **WSL** 提供（`C:\\Windows\\System32\\bash.exe` → 默认发行版），
    而 WSL 里的 bash **看不到 `C:\\...`**（Windows 路径在 Linux 侧就是普通字符串）。
    实测（本机，Ubuntu-24.04）：
        bash "C:\\Users\\...\\scripts\\manage.sh" list
        → /bin/bash: C:UsersAdministrator...scriptsmanage.sh: No such file or directory（exit=127）
    也就是：工具**登记成功、调用必失败**，而且失败原因看起来像"脚本不存在"，
    与凭据无关 —— 这类失败最容易误判成"环境没装 CLI"。

【口径】只在 Windows 上做最小翻译：参数里形如 `X:\...` / `X:/...` 的**绝对 Windows 路径**
    翻成 `/mnt/<盘>/...`；其余参数逐字保留；非 Windows 平台**逐字返回**（零影响）。
    不做"猜测"：只有匹配盘符前缀的才翻，URL/相对路径/普通单词一律不动。

【边界（本轮刻意不处理，写清楚免得后人踩）】
    1. `bash -c "..."` 里**字符串内部**的 Windows 路径不会被翻译（只翻 argv 元素）。
       需要那样做时，调用方自己用 `to_wsl_path()` 拼字符串。
    2. 环境变量**不会**从 Windows 进程跨进 WSL（实测：Python `env=` 传入的
       `BYTEPLUS_ACCESS_KEY` 在 WSL 里读不到；WSL 只在发行版启动时导一次 Windows 环境）。
       所以 `deploy.sh` 依赖的"env 里有 AK/SK"这条路径在 Windows 上不成立 ——
       正确做法是**在 WSL 里配置一次**（`nest config set -g cloud.access_key/secret_key`，
       或 `bash onboard.sh save-credentials <ak> <sk>`，AK/SK 走 argv 不走 env）。
"""

from __future__ import annotations

import os
import re
from typing import List, Sequence, Union

#: `C:\\a\\b` 或 `C:/a/b`（只认盘符开头的绝对路径）
_WIN_ABS = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")

#: 需要路径翻译的可执行名（`bash` 与 `sh` 都是 WSL 侧的）
_SHELL_NAMES = frozenset({"bash", "sh", "bash.exe"})

ArgList = Sequence[Union[str, "os.PathLike[str]"]]


def is_windows() -> bool:
    """当前进程是否在 Windows 上（单测可 monkeypatch 本函数）。"""
    return os.name == "nt"


def to_wsl_path(path: str) -> str:
    """`C:\\a\\b` → `/mnt/c/a/b`；非 Windows 绝对路径原样返回

    Examples:
        >>> to_wsl_path(r"C:\\Users\\x\\a.sh")
        '/mnt/c/Users/x/a.sh'
        >>> to_wsl_path("/mnt/c/Users/x/a.sh")
        '/mnt/c/Users/x/a.sh'
        >>> to_wsl_path("relative/a.sh")
        'relative/a.sh'
    """
    raw = "" if path is None else str(path)
    m = _WIN_ABS.match(raw)
    if not m:
        return raw
    drive = m.group("drive").lower()
    rest = m.group("rest").replace("\\", "/").lstrip("/")
    return "/mnt/{0}/{1}".format(drive, rest) if rest else "/mnt/{0}".format(drive)


def looks_like_shell(arg: object) -> bool:
    """第一个参数是不是要交给 WSL 的 shell（`bash` / `sh` / 带路径的 `...\\bash.exe`）。"""
    name = os.path.basename(str(arg or "")).lower()
    return name in _SHELL_NAMES


def bash_argv(args: ArgList) -> List[str]:
    """把"要交给 bash 的 argv"整理成在当前平台可执行的形状

    - 非 Windows：逐字返回（**不改任何东西**，CI 与 Linux 部署零影响）；
    - Windows 且第一个参数是 bash/sh：把所有形如 `X:\\...` 的参数翻成 `/mnt/x/...`；
    - Windows 但不是 shell 调用：同样只做路径翻译（宁可用得上，不做额外包装）。

    返回新列表，不修改入参。
    """
    out = [str(a) for a in (args or ())]
    if not is_windows():
        return out
    return [to_wsl_path(a) for a in out]


__all__ = ["is_windows", "to_wsl_path", "looks_like_shell", "bash_argv"]
