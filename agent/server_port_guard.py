# -*- coding: utf-8 -*-
"""启动期端口占用清理 —— kill 之前先留痕（L23）。

【为什么必须有留痕】
    app_server.py 启动时对占用 5678 的 PID 直接 taskkill /F（旧实现整段包在
    except Exception: pass 里，零日志）。TASK-03 取证结论：15 个 restart 日志全部
    Traceback=0、MemoryError=0、**无 shutdown 行**，日志在正常流量中戛然而止
    ⇒ 重启只能被归类为"推断：人工/脚本"，原因永远不可自证。

【本模块只做两件事】
    ① 在 kill **之前**写一条结构化日志：谁（actor/pid/ppid/argv）、目标 PID、
       为何（端口被占用）、何时（本地时区 ISO 时间戳）、以及将要执行的 kill 命令；
    ② 让"自杀式重启"（目标是本应用自己的另一个实例：命令行含 app_server.py）与
       "清理他进程"（foreign_process）在日志里可区分。
    **不改 kill 行为**：win32 仍是 taskkill /F /PID <pid>（timeout=3），其他平台
    仍是 os.kill(pid, SIGTERM)；调用方 app_server.py 仍整体 try/except 兜底。

【与优雅关闭钩子的关系（L23 必答）】
    /F == TerminateProcess（无信号投递、无 atexit）⇒ app_server.py 的
    _install_graceful_shutdown_hooks（SIGTERM/SIGINT/SIGBREAK）在这条路径上
    **不可能执行**。这正是"restart 日志无 shutdown 行"的机制解释；本模块的留痕
    是该路径唯一可自证的事实源，与优雅关闭钩子互补，不重复、不冲突。

【可测性】cleanup_port_listeners 支持注入 runner / cmdline_getter / audit，
    回归测试用受控桩断言"留痕先于 kill 且内容完整"，绝不真的 kill 进程。
"""

import logging
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

#: 默认清理端口（app_server.py 的固定端口）
DEFAULT_PORT = 5678

#: 判定"目标就是本应用另一个实例"的命令行标记
SELF_APP_MARKERS = ("app_server.py",)

#: 目标分类
KIND_SELF_APP = "self_app_restart"   # 自杀式重启：目标是本应用自己的另一个实例
KIND_FOREIGN = "foreign_process"     # 清理他进程：目标命令行不像本应用
KIND_UNKNOWN = "unknown"             # 拿不到命令行（无法判定）

#: actor 标识（谁在执行清理）
ACTOR = "app_server.startup_port_cleanup"

_PORT_TOKEN_RE = re.compile(r"^(?:\[[^\]]*\]|[^\s:]+):(\d+)$")


def _trace_id() -> str:
    """生成 trace_id（与 skills_installer/registry 同风格）"""
    return uuid.uuid4().hex[:16]


def parse_listening_pids(netstat_stdout: str, port: int = DEFAULT_PORT) -> List[str]:
    """从 netstat -ano 输出里取出"监听 <port>"的 PID（去重 + 保持出现顺序）。

    只认**本地地址列端口精确等于 port**的 LISTENING 行：旧实现用
    "‘:5678’ in line" 匹配，会把 56789 之类的无关进程也算进来（误杀风险）。
    """
    pids: List[str] = []
    for line in (netstat_stdout or "").splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        local_addr = parts[1]
        m = _PORT_TOKEN_RE.match(local_addr)
        if not m or int(m.group(1)) != int(port):
            continue
        pid = parts[-1]
        if pid.isdigit() and pid not in pids:
            pids.append(pid)
    return pids


def classify_target(cmdline: Optional[str]) -> str:
    """按目标进程命令行分类：自杀式重启 / 清理他进程 / 未知。"""
    if not cmdline:
        return KIND_UNKNOWN
    low = str(cmdline).lower()
    if any(marker in low for marker in SELF_APP_MARKERS):
        return KIND_SELF_APP
    return KIND_FOREIGN


def _default_cmdline_getter(pid: str) -> str:
    """取目标进程命令行：psutil 可用则用它，否则返回空串（=> 归类 unknown）。"""
    try:
        import psutil  # 可选依赖：拿不到就如实记 unknown，不臆测
        return " ".join(psutil.Process(int(pid)).cmdline() or [])
    except Exception:  # noqa: BLE001
        return ""


def _now_iso() -> str:
    """本地时区 ISO 时间戳（秒精度）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_audit(record: Dict[str, Any]) -> None:
    """默认留痕方式：结构化日志（WARNING，保证默认级别可见）。"""
    logger.warning(log_dict(record))


def _actor_cmdline() -> str:
    """本进程命令行（argv 拼接；仅用于留痕自证，不做任何判定）。"""
    try:
        return " ".join(sys.argv)
    except Exception:  # noqa: BLE001
        return ""


def _build_record(pid: str, port: int, cmdline: str, me: int) -> Dict[str, Any]:
    """构造"kill 之前"的留痕记录（谁 / 目标 PID / 为何 / 何时）。"""
    kind = classify_target(cmdline)
    if sys.platform == "win32":
        kill_cmd = "taskkill /F /PID %s" % pid
    else:
        kill_cmd = "os.kill(%s, SIGTERM)" % pid
    if kind == KIND_SELF_APP:
        note = ("自杀式重启：目标是本应用(app_server.py)的另一个实例；"
                "/F 强杀 ⇒ 无信号处理器、无 atexit、无 shutdown 日志")
    elif kind == KIND_FOREIGN:
        note = "清理他进程：目标命令行不含 app_server.py"
    else:
        note = "目标命令行不可读 ⇒ 无法区分自杀式重启与他进程"
    return {
        "trace_id": _trace_id(),
        "module_name": "server_port_guard",
        "action": "startup.port_cleanup.kill_before",
        "actor": ACTOR,
        "actor_pid": me,
        "actor_ppid": os.getppid(),
        "actor_cmdline": _actor_cmdline(),
        "port": int(port),
        "reason": "port_in_use",
        "target_pid": pid,
        "target_cmdline": cmdline,
        "target_kind": kind,
        "is_self_app_restart": kind == KIND_SELF_APP,
        "kill_cmd": kill_cmd,
        "graceful_shutdown_possible": False,
        "note": note,
        "ts": _now_iso(),
    }


def cleanup_port_listeners(
    port: int = DEFAULT_PORT,
    *,
    runner: Optional[Callable[..., Any]] = None,
    cmdline_getter: Optional[Callable[[str], str]] = None,
    self_pid: Optional[int] = None,
    audit: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    """清理占用 port 的进程；**先留痕、后 kill**，返回留痕记录列表。

    Args:
        port: 目标端口（默认 5678）
        runner: subprocess.run 替身（测试注入受控桩 ⇒ 不会真的 kill）
        cmdline_getter: 目标命令行读取器替身（默认 psutil，读不到返回空串）
        self_pid: 本进程 PID（默认 os.getpid()；测试可固定）
        audit: 留痕回调替身（默认写结构化日志）

    Returns:
        每条目标进程一条记录（即使 kill 失败也返回，便于事后自证）
    """
    run = runner or subprocess.run
    get_cmdline = cmdline_getter or _default_cmdline_getter
    emit = audit or _default_audit
    me = os.getpid() if self_pid is None else int(self_pid)

    records: List[Dict[str, Any]] = []
    try:
        netstat = run(["netstat", "-ano"], capture_output=True, text=True)
        stdout = getattr(netstat, "stdout", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("[端口清理] netstat 执行失败（忽略）: %s", e)
        return records

    for pid in parse_listening_pids(stdout, port):
        try:
            cmdline = get_cmdline(pid) or ""
        except Exception:  # noqa: BLE001
            cmdline = ""
        record = _build_record(pid, port, cmdline, me)
        # ① 先留痕（本模块存在的理由）：留痕失败不得阻断清理，但绝不允许"先杀后记"
        try:
            emit(record)
        except Exception as e:  # noqa: BLE001
            logger.debug("[端口清理] 留痕失败（忽略）: %s", e)
        records.append(record)
        # ② 再 kill（命令与旧实现逐字一致）
        try:
            if sys.platform == "win32":
                run(["taskkill", "/F", "/PID", pid],
                    capture_output=True, timeout=3)
            else:
                import signal
                os.kill(int(pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001 与旧实现一致：清理失败不阻断启动
            pass

    if records:
        logger.info(log_dict({
            "trace_id": _trace_id(),
            "module_name": "server_port_guard",
            "action": "startup.port_cleanup.done",
            "actor": ACTOR,
            "port": int(port),
            "targets": [r["target_pid"] for r in records],
            "kinds": sorted({r["target_kind"] for r in records}),
            "self_app_restart": any(r["is_self_app_restart"] for r in records),
        }))
    return records