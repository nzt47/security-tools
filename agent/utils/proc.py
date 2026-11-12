"""跨平台子进程启动的叶子模块 —— Windows 上不许弹出控制台窗口（2026-11-13）。

【为什么需要（实测取证，见 docs/handoff/rounds/§11.102）】
    本机后端由 deploy_workbench / watchdog 用 DETACHED_PROCESS 拉起 ⇒ **自身没有控制台**。
    在这种父进程里，`subprocess.run(["gh", ...])` 会让 Windows 给子进程**新分配一个控制台**：
    实测子进程 `GetConsoleWindow()` 非 0、`IsWindowVisible()=true`、窗口类 `ConsoleWindowClass`
    —— 于是「CI 状态同步」每 30 分钟、"维护·能力清单自检"等每天三次都会闪一下黑窗。
    同一个子进程加 `CREATE_NO_WINDOW` 后：`GetConsoleWindow()==0`，枚举不到任何可见窗口。

【为什么不换 pythonw】
    pythonw 只对"启动器"这一层有效，并且会让 stdout/stderr 无处可写（日志被吞）。
    真正产生窗口的是**子进程被分配控制台**这件事，只能在创建子进程时用 flag 解决 ——
    本模块就是那个唯一入口。

【口径（三条，改动前先读）】
    1. 只在 `sys.platform == "win32"` 时注入：CI 是 Linux，POSIX 上 `creationflags != 0`
       会直接 ValueError；因此非 Windows 返回**空字典**（调用参数与改造前逐字相同）。
    2. 调用方显式传 `creationflags` / `startupinfo` 时**以调用方为准**（如 DETACHED_PROCESS、
       进程组）；本模块只补缺省。
    3. 全仓哨兵：`tests/unit/test_proc_hidden_window.py` 用 AST 扫 `agent/`，
       任何新增的裸 `subprocess.run/Popen/check_output` 都会失败 —— 新增调用点请走本模块。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, Sequence

__version__ = "1.0.0"

#: Windows「不分配控制台」标志（0x08000000）。取不到时用同值兜底（老版本 Python / 文档工具）。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

#: STARTUPINFO.dwFlags 位：本次启动使用 wShowWindow
_STARTF_USESHOWWINDOW = 0x00000001

#: wShowWindow 取值：隐藏窗口
_SW_HIDE = 0


def is_windows() -> bool:
    """当前进程是否在 Windows 上（判据只有一处，别在各调用点重写）。"""
    return sys.platform == "win32"


def hidden_kwargs() -> Dict[str, Any]:
    """Windows 上"不弹窗"所需的 subprocess 关键字；其它平台返回空字典。

    返回值是**新建**的：`startupinfo` 是可变对象，多个子进程共用一个实例会互相踩。
    """
    if not is_windows():
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= _STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = _SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": startupinfo}


def _with_hidden(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """合并缺省：调用方显式给的键覆盖本模块补的键。"""
    merged = hidden_kwargs()
    merged.update(kwargs)
    return merged


def run(argv: Sequence[Any], **kwargs: Any) -> Any:
    """同 `subprocess.run`，Windows 上不弹控制台窗口。"""
    return subprocess.run(argv, **_with_hidden(kwargs))


def popen(argv: Sequence[Any], **kwargs: Any) -> Any:
    """同 `subprocess.Popen`，Windows 上不弹控制台窗口。"""
    return subprocess.Popen(argv, **_with_hidden(kwargs))


def check_output(argv: Sequence[Any], **kwargs: Any) -> Any:
    """同 `subprocess.check_output`，Windows 上不弹控制台窗口。"""
    return subprocess.check_output(argv, **_with_hidden(kwargs))
