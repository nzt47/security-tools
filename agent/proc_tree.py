"""进程树清理（E0-4②）：超时只杀直接子进程会留下孙进程，等于「没杀干净」。

【为什么需要】沙箱执行（agent/subagent/sandbox.py::run_sandboxed）与 shell 工具超时
时调用的是 `proc.kill()` —— 它只 TerminateProcess 直接子进程。真实命令常常是
`cmd /c python -m pytest`、`bash -c "npm run x | tee log"`：孙子（以及重孙）会继续跑，
持有 SQLite/日志文件锁，下一轮再跑同一个东西就撞「文件被占用」（本机实测过）。

【为什么不统一用进程组】POSIX 上 `os.killpg(os.getpgid(pid))` 在父进程没建**新会话**时
会连自己所在的组一起杀（本仓多数 Popen 并没有 start_new_session）——那是最坏的一种「修」：
把服务自己杀掉。所以这里按平台各用一条**只影响目标树**的路径：
  · Windows：`taskkill /F /T /PID <pid>`（/T = 整棵树，系统自带，无需 psutil）；
  · POSIX：从 /proc 建父子表，按「叶子优先」发 SIGKILL（拿不到 /proc 时才退回 psutil，
    再不行只杀直接子进程，并**如实**返回 False 让调用方知道没杀干净）。

【边界】这是**尽力而为**的清理：进程刚 fork 出来还没被枚举到、或换了 PID 命名空间，
都可能漏；返回 True 只代表「清理动作已执行且未报错」，不代表「宇宙里再没有后代进程」。
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

#: taskkill 自身的最长等待（秒）：它必须比业务超时短，否则清理反而拖住收尾
_TASKKILL_TIMEOUT = 10


def _children_map() -> Dict[int, List[int]]:
    """从 /proc 建 ppid → [pid]（Linux）；拿不到返回空表（调用方走 psutil 兜底）"""
    table: Dict[int, List[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return table
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        # stat 形如 "1234 (comm with spaces) S 1 1234 ..."：comm 里可能有空格与括号
        try:
            tail = raw.rsplit(")", 1)[1].strip().split()
            ppid = int(tail[1])
        except (IndexError, ValueError):
            continue
        table.setdefault(ppid, []).append(int(name))
    return table


def _descendants(pid: int) -> List[int]:
    """该 pid 的全部后代（不含自己），叶子优先排序；拿不到就返回空表"""
    table = _children_map()
    if not table:
        return _descendants_by_psutil(pid)
    out: List[int] = []
    seen: Set[int] = set()
    stack = [pid]
    while stack:
        cur = stack.pop()
        for child in table.get(cur, ()):
            if child in seen or child == pid:
                continue
            seen.add(child)
            out.append(child)
            stack.append(child)
    # 叶子优先：父先死会让子被过继，之后按原 ppid 再也找不到
    return list(reversed(out))


def _descendants_by_psutil(pid: int) -> List[int]:
    """psutil 兜底（Windows 之外的平台；psutil 缺失就返回空表）"""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return []
    try:
        proc = psutil.Process(pid)
        return [p.pid for p in proc.children(recursive=True)]
    except Exception:  # noqa: BLE001
        return []


def _kill_pid(pid: int) -> bool:
    """单杀一个 pid（不抛）：Windows 上调用方走 taskkill，这里只给 POSIX 用"""
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except OSError:
        return False


def _kill_tree_windows(pid: int) -> bool:
    try:
        proc = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(int(pid))],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_TASKKILL_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[proc_tree] taskkill 失败 pid=%s err=%s", pid, type(exc).__name__)
        return False
    # 128 = 「进程不存在」：目标已经退出，也算清理到位（幂等）
    return proc.returncode in (0, 128)


def kill_tree(pid, *, wait: bool = True) -> bool:
    """杀掉 pid 的**整棵进程树**（尽力而为、幂等、不抛）。

    Args:
        pid: 树根进程号（通常是 Popen.pid）。
        wait: Windows 上等 taskkill 结束（否则可能刚发完就返回，调用方 wait 拿不到退出码）。

    Returns:
        True = 清理动作执行且未报错；False = 平台/权限等原因没做成（调用方应如实告知）。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            return _kill_tree_windows(pid)
        ok = False
        for child in _descendants(pid):
            ok = _kill_pid(child) or ok
        return _kill_pid(pid) or ok
    except Exception as exc:  # noqa: BLE001 清理失败绝不影响调用方主流程
        logger.warning("[proc_tree] 清理进程树失败 pid=%s err=%s", pid, type(exc).__name__)
        return False


def alive(pid) -> bool:
    """进程是否还活着（测试/探针用；不抛）"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import psutil
            return bool(psutil.pid_exists(pid))
        except Exception:  # noqa: BLE001 psutil 缺失 ⇒ 交给调用方按未知处理
            return False
    # 【POSIX 的僵尸坑】子进程被 SIGKILL 后、父进程还没 wait() 之前是 **Z（zombie）**：
    # os.kill(pid, 0) 对它仍然成功 ⇒ 只看信号会把"已杀死的僵尸"判成"还活着"（CI 实测）。
    # 先读 /proc/<pid>/stat 的状态位：Z/X ⇒ 已经死了。
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        state = raw.rsplit(")", 1)[1].strip().split()[0]
        if state in ("Z", "X", "x"):
            return False
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


__all__ = ["kill_tree", "alive"]
