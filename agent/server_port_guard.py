# -*- coding: utf-8 -*-
"""启动期端口占用清理 —— kill 之前先留痕（L23）。

【为什么必须有留痕】
    app_server.py 启动时对占用 5678 的 PID 直接 taskkill /F（旧实现整段包在
    except Exception: pass 里，零日志）。TASK-03 取证结论：15 个 restart 日志全部
    Traceback=0、MemoryError=0、**无 shutdown 行**，日志在正常流量中戛然而止
    ⇒ 重启只能被归类为"推断：人工/脚本"，原因永远不可自证。

【本模块做四件事】
    ① 在 kill **之前**写一条结构化日志：谁（actor/pid/ppid/argv）、目标 PID、
       为何（端口被占用）、何时（本地时区 ISO 时间戳）、以及将要执行的 kill 命令；
    ② 让"自杀式重启"（目标是本应用自己的另一个实例：命令行含 app_server.py）与
       "清理他进程"（foreign_process）在日志里可区分；
    ③ 【A1】`install_child_process_reaper()`：把本进程放进一个
       KILL_ON_JOB_CLOSE 的 Windows Job Object ⇒ embedding / reranker 等
       `subprocess.Popen` 子进程随父进程一起消亡（父进程被 /F 强杀、崩溃、
       正常退出三条路径全覆盖，无需逐个登记 PID）；
    ④ 【A1】`guarded_startup()`：把"就绪门 → 清理旧实例 → 启动服务"固定成一个
       不可调换的顺序，保证 **新实例启动失败 ⇒ 旧实例不被杀**。
    **`cleanup_port_listeners` 的 kill 行为未改**：win32 仍是
    taskkill /F /PID <pid>（timeout=3），其他平台仍是 os.kill(pid, SIGTERM)；
    调用方仍整体 try/except 兜底。③④ 新增的 `reap_children` 是**可选**参数
    且默认为 False ⇒ 默认调用路径逐字不变（回归锁
    tests/unit/test_server_port_guard.py 对 argv 的逐字断言因此仍然成立：
    本机实测该文件 9 项全绿，见 docs/audit_skill_governance/A1.md）。

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


# ════════════════════════════════════════════════════════════════════════════
# 【A1】残留子进程：Windows 下强杀父进程**不会**收走它的子进程
# ════════════════════════════════════════════════════════════════════════════
# 机制：embedding worker（agent/tool_router_hybrid.py:991）与 reranker
# （agent/skills_mgmt/reranker.py）都是 subprocess.Popen 起的独立 python 进程。
# taskkill /F == TerminateProcess，不投递信号、不跑 atexit、不触发 Job 之外的
# 任何清理 ⇒ 父进程一死子进程即被孤儿化（继续吃 ~0.5–0.9 GB 内存）。
#
# 本模块给两条互补的护栏：
#   ① install_child_process_reaper()：**预防**——父进程加入 KILL_ON_JOB_CLOSE
#      的 Job Object，子进程自动继承，父进程以任何方式消失都连带清理整棵树。
#      覆盖 /F、崩溃、正常退出三条路径，且不依赖"父进程能跑到清理代码"。
#   ② cleanup_port_listeners(reap_children=True)：**补救**——对**没有**加入过
#      Job 的历史实例（例如本次修复之前启动的那个），先记下其后代 PID、强杀
#      父进程、再逐个清掉残留后代。默认关闭 ⇒ 默认调用路径零行为变化。


#: 不视为"需要补杀的残留 worker"的子进程名（小写）。
#: conhost 是内核为**控制台程序**托管的宿主，父进程一死它自然消失；把它记成
#: "残留 embedding/reranker 子进程并已补杀"是**误导性留痕**。
#: 本机实测：A→B 交接时确实捕到过一条 parent=17776 / child=4080 的 conhost 记录
#: （见 docs/audit_skill_governance/A1.md 的实测片段），故在此显式排除。
_EXCLUDED_CHILD_NAMES = frozenset({"conhost.exe", "openconsole.exe", "conhost"})


def _default_children_getter(pid: str) -> List[str]:
    """列出 pid 的**全部后代** PID（psutil 递归 children；不可用则返回空列表）。

    必须在强杀父进程**之前**调用：父进程一死，父子线索就只能靠残留的 ppid 字段了。
    控制台宿主（conhost）被排除，理由见 _EXCLUDED_CHILD_NAMES。
    """
    try:
        import psutil  # 可选依赖：拿不到就如实返回空，不臆测
        out: List[str] = []
        for c in psutil.Process(int(pid)).children(recursive=True):
            try:
                if (c.name() or "").lower() in _EXCLUDED_CHILD_NAMES:
                    continue
            except Exception:  # noqa: BLE001 拿不到名字不阻止收集
                pass
            out.append(str(c.pid))
        return out
    except Exception:  # noqa: BLE001
        return []


def _default_child_guard(child_pid: str, parent_pid: str) -> bool:
    """防 PID 复用的安全闸：只有当 child 的 PPID 仍指向原目标父进程时才允许杀它。

    父进程已被强杀时 `ppid()` 读到的是**残留**父 PID（Windows 不会回收该字段），
    故该判据在父进程死后依然为真；而若 PID 已被复用给无关进程，ppid 不匹配 ⇒
    拒杀。没有这道闸，"先记 PID 后杀"就可能杀掉无关进程。
    """
    try:
        import psutil
        return int(psutil.Process(int(child_pid)).ppid()) == int(parent_pid)
    except Exception:  # noqa: BLE001
        return False


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
    reap_children: bool = False,
    children_getter: Optional[Callable[[str], List[str]]] = None,
    child_guard: Optional[Callable[[str, str], bool]] = None,
) -> List[Dict[str, Any]]:
    """清理占用 port 的进程；**先留痕、后 kill**，返回留痕记录列表。

    Args:
        port: 目标端口（默认 5678）
        runner: subprocess.run 替身（测试注入受控桩 ⇒ 不会真的 kill）
        cmdline_getter: 目标命令行读取器替身（默认 psutil，读不到返回空串）
        self_pid: 本进程 PID（默认 os.getpid()；测试可固定）
        audit: 留痕回调替身（默认写结构化日志）
        reap_children: **默认 False（默认路径行为零变化）**。为 True 时，在强杀
            每个目标之前先记下它的全部后代 PID，强杀之后再逐个清掉仍存活的后代
            （Windows 不会代劳）。用于清理**没有**加入过 Job Object 的历史实例。
        children_getter: 后代 PID 读取器替身（默认 psutil 递归 children）
        child_guard: 防 PID 复用闸替身（默认校验子进程 PPID 仍是原目标 PID）

    Returns:
        每条目标进程一条记录（即使 kill 失败也返回，便于事后自证）。
        reap_children=True 时，被清掉的每个后代进程另有一条
        action="startup.port_cleanup.reap_child" 的记录，便于事后自证。
    """
    run = runner or subprocess.run
    get_cmdline = cmdline_getter or _default_cmdline_getter
    get_children = children_getter or _default_children_getter
    guard_child = child_guard or _default_child_guard
    emit = audit or _default_audit
    me = os.getpid() if self_pid is None else int(self_pid)

    records: List[Dict[str, Any]] = []
    try:
        netstat = run(["netstat", "-ano"], capture_output=True, text=True)
        stdout = getattr(netstat, "stdout", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("[端口清理] netstat 执行失败（忽略）: %s", e)
        return records

    targets = parse_listening_pids(stdout, port)

    # 【A1】强杀之前先把后代记下来：父进程一死就没有第二次机会可靠地取到它们
    # （psutil.Process(dead).children() 直接抛 NoSuchProcess ⇒ 事后取必为空）
    descendants: List[str] = []
    parent_of: Dict[str, str] = {}
    if reap_children:
        for tpid in targets:
            try:
                kids = get_children(tpid) or []
            except Exception as e:  # noqa: BLE001
                logger.debug("[端口清理] 取 %s 的后代失败（忽略）: %s", tpid, e)
                continue
            for k in kids:
                k = str(k)
                if k in targets or k == str(me):
                    continue
                parent_of.setdefault(k, str(tpid))
                descendants.append(k)
        descendants = list(dict.fromkeys(descendants))

    for pid in targets:
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

    # 【A1】补救护栏：父进程已被强杀 ⇒ 后代成了孤儿。仅 reap_children=True 时执行，
    # 且每个后代都要先过"PPID 仍是原目标"这道闸（防 PID 复用误杀无关进程）。
    if reap_children and descendants:
        for dpid in descendants:
            tpid = parent_of.get(dpid)
            if tpid is None:
                continue
            try:
                if not guard_child(dpid, tpid):
                    continue  # 已消失 或 PPID 已不匹配 ⇒ 不碰
            except Exception:  # noqa: BLE001
                continue
            child_record = {
                "trace_id": _trace_id(),
                "module_name": "server_port_guard",
                "action": "startup.port_cleanup.reap_child",
                "actor": ACTOR,
                "actor_pid": me,
                "port": int(port),
                "reason": "orphan_child_of_killed_listener",
                "target_pid": dpid,
                "parent_pid": tpid,
                "kill_cmd": ("taskkill /F /PID %s" % dpid) if sys.platform == "win32"
                            else ("os.kill(%s, SIGTERM)" % dpid),
                # 注：这里刻意不用 "⇒"（U+21D2）—— 本机日志 handler 在 GBK 控制台上
                # 会抛 UnicodeEncodeError（logging 内部吞掉但不留正文），用ASCII箭头更稳。
                "note": "父进程已被强杀；Windows 不代收子进程 -> 显式补杀",
                "ts": _now_iso(),
            }
            try:
                emit(child_record)
            except Exception as e:  # noqa: BLE001
                logger.debug("[端口清理] 后代留痕失败（忽略）: %s", e)
            records.append(child_record)
            try:
                if sys.platform == "win32":
                    run(["taskkill", "/F", "/PID", dpid],
                        capture_output=True, timeout=3)
                else:
                    import signal
                    os.kill(int(dpid), signal.SIGTERM)
            except Exception:  # noqa: BLE001 清理失败不阻断启动
                pass

    if records:
        # 注意：records 里混着两类记录（父进程 kill_before，与可选的 reap_child），
        # 只有父进程记录才有 target_kind / is_self_app_restart ⇒ 必须按 action 过滤，
        # 否则这里会 KeyError（本文件初版就踩过一次，回归见
        # tests/unit/test_startup_no_gap.py::test_reap_records_use_own_action_name_for_audit）。
        listeners = [r for r in records
                     if r.get("action") == "startup.port_cleanup.kill_before"]
        logger.info(log_dict({
            "trace_id": _trace_id(),
            "module_name": "server_port_guard",
            "action": "startup.port_cleanup.done",
            "actor": ACTOR,
            "port": int(port),
            "targets": [r["target_pid"] for r in listeners],
            "kinds": sorted({r["target_kind"] for r in listeners if r.get("target_kind")}),
            "self_app_restart": any(r.get("is_self_app_restart") for r in listeners),
            "reaped_children": [r["target_pid"] for r in records
                                if r.get("action") == "startup.port_cleanup.reap_child"],
        }))
    return records

# ════════════════════════════════════════════════════════════════════════════
# 【A1-③】子进程预防：Windows Job Object（KILL_ON_JOB_CLOSE）
# ════════════════════════════════════════════════════════════════════════════
# 为什么选 Job Object 而不是把 taskkill 改成 /F /T：
#   ① /T 只在**父进程还活着**时才有意义（它按快照枚举子进程），而调用点拿到的
#      是"正在监听 5678 的 PID"，无法保证快照覆盖到已经 re-parent 的孙进程；
#   ② /T 走 subprocess ⇒ 会改变 cleanup_port_listeners 传给 runner 的 argv，
#      而回归锁 tests/unit/test_server_port_guard.py:111 对该 argv 是**逐字断言**
#      （"taskkill /F /PID 4321"），且该测试文件不在本次允许修改的清单内；
#   ③ Job Object 不产生任何 subprocess，覆盖 /F、崩溃、正常退出三条路径，
#      且由内核保证，不依赖父进程"来得及跑到清理代码"。
# 代价（如实登记）：只在**子进程被创建之前**就加入 Job 的父进程有效。修复之前
#   启动的历史实例没有 Job，其后代要靠 cleanup_port_listeners(reap_children=True)
#   这条补救路径收尾。

#: Win32 常量
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

#: Job 句柄必须由本模块长期持有：句柄一旦关闭（含被 GC 回收），Job 即刻销毁，
#: 内核会立刻杀掉所有成员进程 —— 那会把"退出时清理"变成"启动时误杀自己"。
_REAPER_JOB_HANDLE: Any = None


def _win_job_api() -> Dict[str, Any]:
    """构造 Windows Job Object 所需的 ctypes 调用面（只在 win32 下调用）。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    return {
        "ctypes": ctypes,
        "kernel32": kernel32,
        "ExtendedLimit": _EXTENDED_LIMIT_INFORMATION,
    }


def install_child_process_reaper(
    job_name: Optional[str] = None,
    *,
    platform: Optional[str] = None,
    api_factory: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """把**当前进程**放进一个 KILL_ON_JOB_CLOSE 的 Job Object。

    加入之后，本进程之后创建（含已创建但尚未创建的后代）的所有子进程都会继承
    Job 成员身份；本进程以**任何**方式消失（taskkill /F、崩溃、正常退出）时，
    内核都会连带终止整个 Job —— embedding / reranker 不会再被孤儿化。

    Args:
        job_name: 可选 Job 名（仅便于调试观察；None = 匿名 Job）
        platform: 平台替身（测试用；默认 sys.platform）
        api_factory: ctypes 调用面替身（测试用；默认 _win_job_api）

    Returns:
        {"installed": bool, "reason": str, "platform": str, ...}
        **任何失败都只返回 installed=False，不抛异常**（降级不得阻断启动）。
    """
    global _REAPER_JOB_HANDLE

    plat = sys.platform if platform is None else platform
    if plat != "win32":
        return {"installed": False, "reason": "platform_not_win32", "platform": plat}
    if _REAPER_JOB_HANDLE is not None:
        return {"installed": True, "reason": "already_installed", "platform": plat,
                "pid": os.getpid()}

    make_api = api_factory or _win_job_api
    try:
        api = make_api()
        ctypes_mod = api["ctypes"]
        k32 = api["kernel32"]
        limit_cls = api["ExtendedLimit"]

        handle = k32.CreateJobObjectW(None, job_name)
        if not handle:
            return {"installed": False, "platform": plat, "pid": os.getpid(),
                    "reason": "CreateJobObject_failed:err=%d" % ctypes_mod.get_last_error()}

        info = limit_cls()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
                handle, _JobObjectExtendedLimitInformation,
                ctypes_mod.byref(info), ctypes_mod.sizeof(info)):
            err = ctypes_mod.get_last_error()
            k32.CloseHandle(handle)
            return {"installed": False, "platform": plat, "pid": os.getpid(),
                    "reason": "SetInformationJobObject_failed:err=%d" % err}

        # 注意：GetCurrentProcess() 返回的是伪句柄(-1)，不能用于
        # AssignProcessToJobObject（实测 err=6 ERROR_INVALID_HANDLE）
        # ⇒ 必须用 OpenProcess 拿真句柄。
        self_handle = k32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, os.getpid())
        if not self_handle:
            err = ctypes_mod.get_last_error()
            k32.CloseHandle(handle)
            return {"installed": False, "platform": plat, "pid": os.getpid(),
                    "reason": "OpenProcess_failed:err=%d" % err}

        ok = k32.AssignProcessToJobObject(handle, self_handle)
        err = ctypes_mod.get_last_error()
        k32.CloseHandle(self_handle)
        if not ok:
            k32.CloseHandle(handle)
            # err=5 (ERROR_ACCESS_DENIED) 常见于"本进程已被外部放进一个不允许嵌套
            # 的 Job"（老 Windows / 受限启动器）⇒ 如实降级，不重试、不臆测成功。
            return {"installed": False, "platform": plat, "pid": os.getpid(),
                    "reason": "AssignProcessToJobObject_failed:err=%d" % err,
                    "note": "本进程可能已被外部 Job 限制；降级为不回收子进程"}

        _REAPER_JOB_HANDLE = handle  # 长期持有，禁止释放
        return {"installed": True, "reason": "ok", "platform": plat, "pid": os.getpid()}
    except Exception as e:  # noqa: BLE001 降级不得阻断启动
        return {"installed": False, "platform": plat, "pid": os.getpid(),
                "reason": "exception:%s" % e}


# ════════════════════════════════════════════════════════════════════════════
# 【A1-④】就绪门：先证"新实例能服务"，再杀旧实例
# ════════════════════════════════════════════════════════════════════════════
# 旧行为（审计 §1.4）：app_server.py 在**启动早期**（模块导入后、调度器之前）
#   无条件 taskkill /F 掉 5678 上的旧实例；此后才构造引擎并 serve。旧实例已死而
#   新实例可能在任何后续步骤失败 ⇒ 结果是**完全无服务**，且没有人会发现。
# 新行为：把"清理旧实例"推迟到**全部可失败的初始化都成功之后**，并且只有在
#   ①端口上确实有旧实例、且 ②新实例通过自证健康探针 两个条件同时满足时才清理。
# 时序安全性：新实例在任何一步失败都发生在清理之前 ⇒ 旧实例继续服务；端口空闲
#   时根本不跑探针、零额外开销。残余风险（如实登记）：清理与 bind 之间仍有
#   实测 <1s 的窗口（单端口无法原子交接），该窗口由 scripts/watchdog_yunshu.py
#   看门狗兜底重启。详见 docs/audit_skill_governance/A1.md。

#: guarded_startup 的退出码（供调用方 sys.exit）
EXIT_OK = 0
EXIT_PREFLIGHT_FAILED = 3   # 新实例未就绪 ⇒ 已放弃，旧实例未被杀
EXIT_SERVE_FAILED = 4       # 清理后 bind/serve 失败 ⇒ 需要看门狗介入


def guarded_startup(
    serve_fn: Callable[[], Any],
    port: int = DEFAULT_PORT,
    *,
    preflight: Optional[Callable[[], Any]] = None,
    runner: Optional[Callable[..., Any]] = None,
    cmdline_getter: Optional[Callable[[str], str]] = None,
    self_pid: Optional[int] = None,
    audit: Optional[Callable[[Dict[str, Any]], Any]] = None,
    children_getter: Optional[Callable[[str], List[str]]] = None,
    child_guard: Optional[Callable[[str, str], bool]] = None,
) -> Dict[str, Any]:
    """就绪门 → 清理旧实例 → 启动服务（顺序不可调换）。

    **不变量**：只有 `preflight()` 判定新实例已就绪时才会清理旧实例；
    新实例未就绪 ⇒ 一个进程都不杀，旧实例继续服务。

    Args:
        serve_fn: 真正启动服务的可调用对象（app_server 传 waitress 的 serve(...)）
        port: 目标端口
        preflight: 就绪门；返回 bool，或 (bool, str) 原因串；抛异常一律视为
            "未就绪"（失败关闭）。端口空闲时**不会**被调用。
        runner / cmdline_getter / self_pid / audit / children_getter / child_guard:
            透传给 cleanup_port_listeners（测试注入受控桩）

    Returns:
        {"served": bool, "exit_code": int, "port": int, "targets": [...],
         "preflight": {...}, "cleanup_records": [...], "serve_error": str|None}
    """
    run = runner or subprocess.run
    me = os.getpid() if self_pid is None else int(self_pid)
    result: Dict[str, Any] = {
        "served": False, "exit_code": EXIT_OK, "port": int(port),
        "targets": [], "preflight": None, "cleanup_records": [], "serve_error": None,
    }

    # ① 只读探测：端口上有没有旧实例？（netstat，无副作用）
    targets: List[str] = []
    try:
        netstat = run(["netstat", "-ano"], capture_output=True, text=True)
        targets = parse_listening_pids(getattr(netstat, "stdout", "") or "", port)
    except Exception as e:  # noqa: BLE001
        logger.debug("[启动就绪门] netstat 执行失败（按端口空闲处理）: %s", e)
    result["targets"] = targets

    if not targets:
        # 端口空闲 ⇒ 没有旧实例可杀，零服务窗口不存在，也不值得付探针的代价
        result["preflight"] = {"ran": False, "ok": True,
                               "reason": "port_free_no_old_instance"}
    else:
        ok, reason = True, "no_preflight_configured"
        if preflight is not None:
            try:
                verdict = preflight()
                if isinstance(verdict, tuple):
                    ok = bool(verdict[0])
                    reason = str(verdict[1]) if len(verdict) > 1 else ""
                else:
                    ok = bool(verdict)
                    reason = "preflight_ok" if ok else "preflight_returned_false"
            except Exception as e:  # noqa: BLE001 探针自身出错 = 未就绪（失败关闭）
                ok, reason = False, "preflight_exception:%s" % e
        result["preflight"] = {"ran": preflight is not None, "ok": ok, "reason": reason}

        if not ok:
            # ── 【A1 不变量】新实例未就绪 ⇒ 绝不清理旧实例 ──
            try:
                logger.error(log_dict({
                    "trace_id": _trace_id(),
                    "module_name": "server_port_guard",
                    "action": "startup.gate.abort_before_cleanup",
                    "actor": "app_server.startup_gate",
                    "actor_pid": me,
                    "port": int(port),
                    "old_instance_pids": targets,
                    "reason": "new_instance_not_ready",
                    "preflight_reason": reason,
                    "killed_any": False,
                    "note": "新实例未通过就绪门：旧实例**未被清理**，继续对外服务",
                    "ts": _now_iso(),
                }))
            except Exception:  # noqa: BLE001
                pass
            result["exit_code"] = EXIT_PREFLIGHT_FAILED
            return result

        # 【契约保留 · 独立复核 A1-R1】"清理失败不阻断启动"是**三处既有契约**：
        #   ① 原 app_server.py 调用点整段包在 try/except 里（"清理失败不阻断启动
        #      （与旧行为一致）"）；② 本模块 docstring 明文写"调用方仍整体 try/except
        #      兜底"；③ 每个 kill 各自 try/except。
        # 新链路**不得比旧链路更严**：早期版本这里没有兜底，任何清理期异常都会
        # 在"旧实例已被杀、新实例还没 bind"之间穿出去 ⇒ 正好制造本卡要消灭的
        # 零服务窗口（实测确实发生过一次：records 混合记录取 target_kind 抛
        # KeyError，见 test_cleanup_exception_does_not_block_startup）。
        # 故此处显式吞掉清理异常：记一条结构化留痕，继续尝试 bind。端口没被释放
        # 时 bind 会失败并以 EXIT_SERVE_FAILED(4) 退出，交给看门狗处理 —— 比在
        # 半途崩掉更可观测。
        try:
            result["cleanup_records"] = cleanup_port_listeners(
                port, runner=runner, cmdline_getter=cmdline_getter, self_pid=me,
                audit=audit, reap_children=True,
                children_getter=children_getter, child_guard=child_guard)
        except Exception as e:  # noqa: BLE001 契约：清理失败不阻断启动
            result["cleanup_error"] = "%s: %s" % (type(e).__name__, e)
            try:
                logger.error(log_dict({
                    "trace_id": _trace_id(),
                    "module_name": "server_port_guard",
                    "action": "startup.port_cleanup.failed",
                    "actor": "app_server.startup_gate",
                    "actor_pid": me,
                    "port": int(port),
                    "old_instance_pids": targets,
                    "reason": "cleanup_raised",
                    "error": result["cleanup_error"],
                    "note": "清理抛异常但按既有契约**不阻断启动**：继续尝试 bind；"
                            "若端口未释放则 bind 失败并以 4 退出，由看门狗接手",
                    "ts": _now_iso(),
                }))
            except Exception:  # noqa: BLE001 留痕失败不得反过来阻断启动
                pass

    # ② 只有走到这里才真正 bind
    try:
        serve_fn()
    except Exception as e:  # noqa: BLE001 交由调用方按 exit_code 处理
        result["serve_error"] = "%s: %s" % (type(e).__name__, e)
        result["exit_code"] = EXIT_SERVE_FAILED
        try:
            logger.error("[启动] serve 失败（旧实例已在本次启动中被清理，需要看门狗介入）: %s",
                         result["serve_error"])
        except Exception:  # noqa: BLE001
            pass
        return result

    result["served"] = True
    return result
