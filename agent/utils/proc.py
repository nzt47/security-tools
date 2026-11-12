"""跨平台"子进程不弹控制台窗口"的唯一判据（叶子模块，2026-11-13）。

【为什么需要（实测取证，见 docs/handoff/rounds/§11.102 与
 docs/handoff/evidence/console_windows_20261113.json）】
    本机后端由 deploy_workbench / watchdog 用 DETACHED_PROCESS 拉起 ⇒ **自身没有控制台**。
    在这种父进程里，`subprocess.run(["gh", ...])` 会让 Windows 给子进程**新分配一个控制台**：
    实测子进程 `GetConsoleWindow()` 非 0、`IsWindowVisible()=true`、窗口类 `ConsoleWindowClass`
    —— 于是「CI 状态同步」每 30 分钟、"维护·能力清单自检"等每天三次都会闪一下黑窗。
    同一个子进程加上 `CREATE_NO_WINDOW` 后：`GetConsoleWindow()==0`，枚举不到任何可见窗口。

【为什么不换 pythonw】
    pythonw 只作用于"启动器"这一层，并且会让 stdout/stderr 无处可写（日志被吞）。
    真因是**子进程被分配了控制台**，只能在创建子进程时用 flag 解决。

【怎么用（重要：不要封装 run/Popen）】
    调用点保持 `subprocess.run(...)` 原样，只把本模块的 kwargs 展开进去：

        from agent.utils import proc as _proc
        proc = subprocess.run(argv, **_proc.hidden_kwargs(), capture_output=True, text=True)

    为什么不封装成 `proc.run()`：本仓多处用例通过替换**模块属性** `mod.subprocess`
    来打桩（`monkeypatch.setattr(mod, "subprocess", fake)`）。一旦把调用改成
    `_proc.run()`，这些桩就被绕过 —— 2026-11-13 实测有 5 个分片共 30+ 条用例因此变红
    （安装器判定、隔离执行体、embedding/reranker worker、build_info）。展开 kwargs 的写法
    两边都成立：桩仍然拦得住调用，Windows 上也不会弹窗。

【口径（四条）】
    1. 只在 `sys.platform == "win32"` 时返回非空：CI 是 Linux，POSIX 上 `creationflags != 0`
       会直接 ValueError；非 Windows 返回**空字典**，调用参数与改造前逐字相同。
    2. 调用方自己已经传 `creationflags`/`startupinfo` 的**不要**再展开本函数的返回值
       （会 duplicate keyword 报错）—— 那类调用点单独显式处理，守卫用例会把它们列出来。
    3. 每次返回**新对象**：`startupinfo` 是可变结构，多个子进程共用会互相踩。
    4. 全仓哨兵 `tests/unit/test_proc_hidden_window.py` 用 AST 扫 `agent/`：任何新增的
       `subprocess.run/Popen/check_output/call/check_call` 要么显式给 creationflags/
       startupinfo，要么必须展开 `**_proc.hidden_kwargs()`，否则用例直接红。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict

__version__ = "1.1.0"

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
    """Windows 上"不弹窗"所需的 subprocess 关键字；其它平台返回**空字典**。

    用法：`subprocess.run(argv, **_proc.hidden_kwargs(), ...)`。
    """
    if not is_windows():
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= _STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = _SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": startupinfo}
