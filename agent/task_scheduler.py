"""
定时任务调度器 — 统一执行引擎

支持三种任务类型:
- python_func: Python callable 任务（代码注册）
- system_command: 系统命令任务（API创建, subprocess执行）
- heartbeat: 内置心跳健康检查

启动方式:
    scheduler = get_scheduler()
    scheduler.start_daemon(check_interval=10)  # 非阻塞 daemon 线程
"""

import logging
import time
import json
import uuid
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量 _scheduler 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton, is_initialized,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = is_initialized = None

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


def _record_rerun(task_id: str, task_name: str = "") -> None:
    """人工重跑任务 → ACR 介入埋点（§6.1 重跑=1；TASK-S2-03；best-effort）"""
    try:
        from agent.observability.acr import record_intervention
        record_intervention("rerun", task_id=str(task_id or ""),
                            actor="human", source_ref=f"scheduler:{task_id}",
                            extra={"task_name": str(task_name or "")})
    except Exception as e:  # noqa: BLE001 埋点不得影响调度执行
        logger.debug("[TaskScheduler] rerun 埋点失败: %s", e)


# 数据文件路径
DATA_DIR = Path(__file__).parent.parent / "data"
SCHEDULED_TASKS_FILE = DATA_DIR / "scheduled_tasks.json"
TASK_HISTORY_FILE = DATA_DIR / "task_history.jsonl"
HEARTBEAT_HISTORY_FILE = DATA_DIR / "heartbeat_history.json"

# 默认配置（向后兼容常量，实际值应通过便捷函数从 Config 读取）
DEFAULT_CHECK_INTERVAL = 10    # 向后兼容别名，实际值通过 get_scheduler_check_interval() 读取
COMMAND_TIMEOUT = 300          # 向后兼容别名，实际值通过 get_scheduler_command_timeout() 读取
MAX_HISTORY_LINES = 1000       # 向后兼容别名，实际值通过 get_scheduler_max_history_lines() 读取
HEARTBEAT_INTERVAL = 60        # 向后兼容别名，实际值通过 get_scheduler_heartbeat_interval() 读取
MAX_HEARTBEAT_HISTORY = 1440   # 向后兼容别名，实际值通过 get_scheduler_max_heartbeat_history() 读取

# ── 定时命令安全闸门的回退权限系统（模块级惰性单例）────────────────────────
# Why 需要回退：生产路径下 app_server 在启动时注入 scheduler._yunshu_ref = _Yunshu
# （app_server.py:1497），DigitalLife 上经 ._permission 暴露 LifecycleManager 构造的
# PermissionSystem（与 tools/system_tools.shell_execute 同源，含其运行时状态）。
# 但 _yunshu_ref 未注入的场景（单测、脚本直连调度器、任何先于注入就 tick 的路径）
# 若因此"取不到权限对象就跳过检查"，这条路径就退化成一条**无防护的无人值守命令
# 执行通道**——正是本次要堵的洞。故此处惰性创建并缓存一个真实 PermissionSystem()
# 兜底：策略/正则仍是真实规则，只是不含生产实例的运行时计数与告警历史。
_fallback_permission_system = None  # type: Optional[Any]


def _get_fallback_permission_system():
    """惰性创建并缓存回退用 PermissionSystem（只建一次；构造失败向上抛，由调用方 fail-closed）"""
    global _fallback_permission_system
    if _fallback_permission_system is None:
        from agent.permission_system import PermissionSystem
        _fallback_permission_system = PermissionSystem()
    return _fallback_permission_system


# ── 任务执行期的"定时任务来源"上报（contextvar；供 ABAC 规则识别来源）──────────────
# Why 需要（这是本机制存在的唯一理由）：
#   data/permission_policies.json 的 scheduled-no-write / scheduled-no-edit 两条 ABAC 规则
#   靠 ABACContext.session_source == "scheduled" 触发。但改动前**没有任何生产链路**报这个
#   来源——严格模式（agent/tool_gate.py 的 CP_TOOL_GATE_STRICT）里来源只来自**进程级**环境
#   变量 CP_PERMISSION_SESSION_SOURCE（默认 "cli"），无法区分"这次调用来自定时任务"还是
#   "来自人机对话" ⇒ 两条规则永远触发不了，是纯粹的摆设。这里在**任务执行期**把来源标为
#   scheduled，让规则第一次真的有来源可判。
# Why 设置在 run_task 体内（而不是 tick/_run_loop 里）：contextvars **不跨线程继承**——
#   定时任务由 tick() 在 daemon 线程（_run_loop）里执行，在调度线程里设置对执行线程无效，
#   新线程读到的永远是空值。故设置点必须落在**真正执行任务体**的那个线程内部。
# 作用范围（**默认零行为变化**）：该来源只有开启 CP_TOOL_GATE_STRICT 后才会被 ABAC 消费；
#   未开启时闸门默认 fail-open、根本不构造 ABACContext ⇒ 本上报对工具调用毫无影响。
#   _guard_scheduled_command（正则层）的判定口径**不涉及**本来源，未被改动。
# 失败姿态：上报/还原失败一律只告警，绝不阻断、也不改变任务的执行结果。
_SCHEDULED_SESSION_SOURCE = "scheduled"


def _enter_scheduled_session_source():
    """把**当前执行线程**的会话来源标为 ``scheduled``；返回还原句柄（失败 ⇒ ``None``）

    延迟导入 ``agent.tool_gate``（与本文件其它可选依赖同款写法）：导入失败只告警不抛，
    任务照常执行——上报是增强，不是任务的前置条件。
    """
    try:
        from agent.tool_gate import set_session_source
        return set_session_source(_SCHEDULED_SESSION_SOURCE)
    except Exception as e:  # noqa: BLE001 上报失败不得影响任务执行
        logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 会话来源上报失败（不影响任务执行）: {e}'}))
        return None


def _exit_scheduled_session_source(handle) -> None:
    """还原会话来源（**任何退出路径都必须调用**；失败只告警，不掩盖任务结果）

    Why 必须还原：contextvar 是本线程的，daemon 线程会持续跑下去，若不还原，后续
    人机对话（若复用同一线程执行）会继续被当成"定时任务来源"⇒ ABAC 规则误伤正常会话。
    """
    if handle is None:
        return
    try:
        from agent.tool_gate import reset_session_source
        reset_session_source(handle)
    except Exception as e:  # noqa: BLE001 还原失败不得影响任务结果（已告警）
        logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 会话来源还原失败: {e}'}))


# ── 【TASK-06】执行**身份**上报（与上面的"来源"上报配对）──────────────────────
# 【为什么来源之外还要身份】两者答的是不同问题：
#   来源 = "从哪条链路进来的"（cli/web/api/scheduled）；
#   身份 = "**谁**在调"（human/llm/system/service_account）。
# `tool_gate` 的两条判定读的是**身份**：
#   ① "无身份 + 非交互 ⇒ 拒绝（不挂单）"；② "SA 预授权"。
# 只报来源时，定时任务的 identity 仍是空串 ⇒ ①②都拿不到依据。
# 【为什么标 `system` 而不是 `service_account`】`system` 的语义是"平台内部的
#   签名执行体"（v1.4 §10.1：治理脚本、迁移、健康检查），而本调度器**正是**
#   平台自身的后台线程，它没有外部凭据、也不该有 SA 的 scope 预授权能力。
#   标成 SA 反而会**放宽**它对 L2 工具的访问面（SA 可凭 scope 通过），方向错了。
_SCHEDULED_EXECUTION_IDENTITY = "system"


def _enter_scheduled_execution_identity():
    """把**当前执行线程**的身份标为 ``system``；返回还原句柄（失败 ⇒ ``None``）"""
    try:
        from agent.tool_gate import set_execution_identity
        return set_execution_identity(_SCHEDULED_EXECUTION_IDENTITY)
    except Exception as e:  # noqa: BLE001 上报失败不得影响任务执行
        logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 执行身份上报失败（不影响任务执行）: {e}'}))
        return None


def _exit_scheduled_execution_identity(handle) -> None:
    """还原执行身份（**任何退出路径都必须调用**；失败只告警）"""
    if handle is None:
        return
    try:
        from agent.tool_gate import reset_execution_identity
        reset_execution_identity(handle)
    except Exception as e:  # noqa: BLE001
        logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 执行身份还原失败: {e}'}))


class TaskScheduler:
    """增强型定时任务调度器"""

    def __init__(self):
        self.tasks: List[Dict[str, Any]] = []
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_func: Optional[Callable] = None  # 由外部注入的心跳函数
        self._yunshu_ref = None  # DigitalLife 引用，供心跳使用
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 初始化完成'}))

    def add_cron_task(self, name: str, func: Callable, day_of_week: int = None,
                      hour: int = 0, minute: int = 0) -> None:
        """添加 Python 函数 cron 任务"""
        task = {
            "name": name,
            "type": "python_func",
            "func": func,
            "cron": {"day_of_week": day_of_week, "hour": hour, "minute": minute},
            "last_run": None,
            "enabled": True,
            "task_id": self._generate_task_id("py"),
        }
        self.tasks.append(task)
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'name.cron', 'msg': f'[TaskScheduler] 添加任务: {name} (cron)'}))

    def add_interval_task(self, name: str, func: Callable, interval_seconds: int) -> None:
        """添加 Python 函数间隔任务"""
        task = {
            "name": name,
            "type": "python_func",
            "func": func,
            "interval": interval_seconds,
            "last_run": None,
            "enabled": True,
            "task_id": self._generate_task_id("py"),
        }
        self.tasks.append(task)
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'name.interval_seconds', 'msg': f'[TaskScheduler] 添加任务: {name} (每{interval_seconds}秒)'}))

    def add_command_task(self, name: str, command: str, interval_sec: int,
                         task_id: str = "", enabled: bool = True) -> None:
        """添加系统命令任务（由 API 创建时调用）"""
        task = {
            "name": name,
            "type": "system_command",
            "command": command,
            "interval": interval_sec,
            "last_run": None,
            "enabled": enabled,
            "task_id": task_id or self._generate_task_id("cmd"),
        }
        self.tasks.append(task)
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'name.interval_sec', 'msg': f'[TaskScheduler] 添加命令任务: {name} (每{interval_sec}秒)'}))

    def _generate_task_id(self, prefix: str = "task") -> str:
        """生成唯一任务 ID"""
        return f"{prefix}_{int(time.time() * 1000)}_{len(self.tasks)}"

    def remove_task(self, task_id: str) -> bool:
        """按 task_id 删除任务"""
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.get("task_id") != task_id]
        return len(self.tasks) < before

    def set_task_enabled(self, task_id: str, enabled: bool) -> bool:
        """启用/禁用任务"""
        for t in self.tasks:
            if t.get("task_id") == task_id:
                t["enabled"] = enabled
                return True
        return False

    def get_task(self, task_id: str) -> Optional[Dict]:
        """按 ID 查找任务"""
        for t in self.tasks:
            if t.get("task_id") == task_id:
                return t
        return None

    def _should_run(self, task: Dict[str, Any]) -> bool:
        """检查任务是否应该运行"""
        if not task.get("enabled", True):
            return False

        now = datetime.now()

        if task["type"] == "python_func":
            if "cron" in task:
                c = task["cron"]
                if c.get("day_of_week") is not None and now.weekday() != c["day_of_week"]:
                    return False
                if now.hour != c["hour"] or now.minute != c["minute"]:
                    return False
                if task.get("last_run"):
                    if task["last_run"].date() == now.date():
                        return False
                return True
            elif "interval" in task:
                if task.get("last_run") is None:
                    return True
                elapsed = (now - task["last_run"]).total_seconds()
                return elapsed >= task["interval"]
        elif task["type"] == "system_command":
            if task.get("last_run") is None:
                return True
            elapsed = (now - task["last_run"]).total_seconds()
            return elapsed >= task["interval"]
        elif task["type"] == "heartbeat":
            if task.get("last_run") is None:
                return True
            elapsed = (now - task["last_run"]).total_seconds()
            # 配置化：从 Config 读取心跳间隔（支持热加载）
            from agent.monitoring.observability_config import get_scheduler_heartbeat_interval
            return elapsed >= get_scheduler_heartbeat_interval()

        return False

    def _guard_scheduled_command(self, command: str) -> str:
        """定时命令执行前的安全闸门（与 tools/system_tools.shell_execute 同口径）

        system_command 任务由 API 创建、由 daemon 线程**无人值守**执行，此前直接
        ``subprocess.Popen(command, shell=True)``——没有任何权限校验，可绕过
        「危险命令会被安全系统阻止」这一承诺。本方法把交互式 shell_execute
        （agent/tools/system_tools.py:110-136）的判定口径原样搬过来，作为执行前的
        最后一道防线。

        判定顺序（与 shell_execute 完全一致）:
            1. check_text(command) 为 "critical" → 拒绝；
            2. 为 "warning" → check_action(...)，not allowed → 拒绝；
            3. 其余 → 放行；
            4. 空命令 → 拒绝；
            5. 任何异常 → fail-closed 拒绝（安全检查不可用时绝不放行）。

        权限对象取法（顺序敏感，决定既有良性命令测试是否仍能执行）:
            优先 ``getattr(self._yunshu_ref, "_permission", None)``——生产里
            app_server 启动时注入 _yunshu_ref = _Yunshu（app_server.py:1497），
            DigitalLife 上即 LifecycleManager 构造的真实权限系统（含运行时状态）；
            取不到时**回退**到模块级惰性缓存的 PermissionSystem()，而不是「跳过
            检查」——否则 _yunshu_ref 未注入的路径（单测/脚本直连/注入前 tick）
            就成了无防护通道，正是本次修复要堵的洞。

        Returns:
            str: "" = 放行；非空 = 拒绝原因（人可读，可直接写入 result["error"]）
        """
        if not command or not command.strip():
            return "命令为空"

        try:
            permission = getattr(self._yunshu_ref, "_permission", None)
            if permission is None:
                # 构造失败同样进 except → fail-closed（不把无权限对象当成放行）
                permission = _get_fallback_permission_system()
            check = permission.check_text(command)
            if check.get("level") == "critical":
                matches = [m.get("description", "") for m in check.get("matches", [])]
                return f"危险命令被安全系统阻止: {matches}"
            elif check.get("level") == "warning":
                desc = "; ".join(m.get("description", "") for m in check.get("matches", []))
                perm = permission.check_action(
                    f"system_command:warning:{desc[:100]}",
                    f"定时任务执行可能危险的命令: {desc}",
                )
                if not perm.allowed:
                    return f"权限系统拒绝: {perm.reason}"
        except Exception as e:  # noqa: BLE001 fail-closed：检查不可用 ⇒ 拒绝执行
            logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 定时命令安全检查异常，已拒绝执行: {e}'}))
            return "安全检查系统故障，拒绝执行定时命令任务"

        return ""

    def run_task(self, task: Dict[str, Any], *,
                 trigger: str = "schedule") -> Dict[str, Any]:
        """执行单个任务，返回执行结果

        **执行期会话来源上报（本次新增）**：任务体在 ``_run_task_body`` 里执行；本方法在
        **执行线程内**（``tick()`` 由 daemon 线程 ``_run_loop`` 调用，故执行线程即设置线程）
        把会话来源标为 ``scheduled``，并用 ``try/finally`` 在**任何退出路径**（成功 / 任务体
        抛异常 / 返回值中途出错）还原，杜绝污染后续人机对话的工具调用。

        作用范围：该来源只被 ``agent/tool_gate.py`` 的**严格模式**消费（``CP_TOOL_GATE_STRICT``，
        **默认关闭**）⇒ 不开严格模式时本上报对行为**零影响**；开启后，定时任务里的
        ``write_file`` / ``edit`` 会命中 ``data/permission_policies.json`` 的
        ``scheduled-no-write`` / ``scheduled-no-edit`` 两条 ABAC 规则而被拒绝。
        ``_guard_scheduled_command``（正则层）的判定口径**不涉及**本来源，未被改动。

        Args:
            trigger: 本次执行的触发来源。``"schedule"``（默认）= 调度器按周期自动触发；
                ``"manual"`` = 人工在 UI 上点"立即执行"（``execute_now``）。
                **只有 ``"schedule"`` 才上报 ``scheduled``**——那两条 ABAC 规则防的是
                **无人值守**任务乱改东西，而人工重跑时有人在场，不该被禁（否则手动重跑一个
                生成报告的任务会写不出文件）。非 ``"schedule"`` 时不上报，来源落到 ``cli``。
        """
        _source_handle = (
            _enter_scheduled_session_source() if str(trigger) == "schedule" else None
        )
        # 【TASK-06 面 1】身份与来源**同时**上报：来源对上 ABAC 的 session_source_in，
        # 身份对上闸门的"无身份 ⇒ 拒绝（不挂单）"与"SA 预授权"两条判定。
        # 与来源同一条件（只有 `schedule` 触发才是无人值守）；人工 `manual` 触发时
        # 人就在场，既不该被标成 system，也不该丢身份（保持"未声明"由上层决定）。
        _ident_handle = (
            _enter_scheduled_execution_identity()
            if str(trigger) == "schedule" else None
        )
        try:
            return self._run_task_body(task)
        finally:
            _exit_scheduled_session_source(_source_handle)
            _exit_scheduled_execution_identity(_ident_handle)

    def _run_task_body(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """任务体（原 ``run_task`` 实现逐字未改；执行期的会话来源由 ``run_task`` 包裹）"""
        start_time = datetime.now()
        result = {
            "task_id": task.get("task_id", ""),
            "name": task["name"],
            "type": task["type"],
            "start_time": start_time.isoformat(),
            "status": "running",
            "output": "",
            "error": "",
            "duration_ms": 0,
        }

        # 无论成功/失败都更新 last_run，避免失败任务每 tick 重试
        task["last_run"] = datetime.now()

        try:
            if task["type"] == "python_func":
                if "func" in task:
                    task["func"]()
                result["status"] = "success"

            elif task["type"] == "system_command":
                command = task.get("command", "")
                # 安全闸门（本次修复）：与交互式 shell_execute 同口径，见
                # _guard_scheduled_command。被拒时**不执行 Popen**，只把拒绝原因写进
                # result；因而不 return——函数尾部还要统一补 end_time/duration_ms 并
                # 走 _append_history 落盘（被拒任务同样要留痕，且不破坏既有尾部逻辑）。
                _guard_error = self._guard_scheduled_command(command)
                if _guard_error:
                    result["status"] = "failed"
                    result["error"] = _guard_error
                    logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'command', 'msg': f'[TaskScheduler] 定时命令被安全闸门拒绝: {task["name"]}: {_guard_error}'}))
                else:
                    logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'command', 'msg': f'[TaskScheduler] 执行命令: {command}'}))
                    # 保留 shell=True 的取舍：命令可能含管道/重定向/内建等 shell 语法，
                    # 改 shell=False 会把整串当单个 argv[0]、破坏既有用法；本任务的目的是
                    # 补上执行前的权限闸门，而不是重写执行方式（闸门在 Popen 之前生效）。
                    proc = subprocess.Popen(
                        command,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    try:
                        # 配置化：从 Config 读取命令超时（支持热加载）
                        from agent.monitoring.observability_config import get_scheduler_command_timeout
                        _cmd_timeout = get_scheduler_command_timeout()
                        stdout, stderr = proc.communicate(timeout=_cmd_timeout)
                        if proc.returncode == 0:
                            result["status"] = "success"
                            result["output"] = stdout.strip()[:500]
                        else:
                            result["status"] = "failed"
                            result["error"] = stderr.strip()[:500]
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        result["status"] = "failed"
                        result["error"] = f"命令执行超时 ({_cmd_timeout}秒)"

            elif task["type"] == "heartbeat":
                if self._heartbeat_func:
                    hb_result = self._heartbeat_func(self._yunshu_ref)
                    result["status"] = hb_result.get("status", "unknown")
                    result["output"] = json.dumps(hb_result, ensure_ascii=False)
                    self._save_heartbeat(hb_result)

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)[:500]
            logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'task', 'msg': f'[TaskScheduler] 任务执行失败: {task["name"]}: {e}'}))

        end_time = datetime.now()
        result["end_time"] = end_time.isoformat()
        result["duration_ms"] = int((end_time - start_time).total_seconds() * 1000)
        self._append_history(result)
        return result

    def _append_history(self, record: Dict[str, Any]) -> None:
        """追加执行记录到 JSONL"""
        try:
            TASK_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TASK_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._trim_history()
        except Exception as e:
            logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 写入历史失败: {e}'}))

    def _trim_history(self) -> None:
        """保留最近 N 条记录（N 从 Config 读取）"""
        try:
            if TASK_HISTORY_FILE.exists():
                with open(TASK_HISTORY_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                # 配置化：从 Config 读取最大行数（支持热加载）
                from agent.monitoring.observability_config import get_scheduler_max_history_lines
                _max_lines = get_scheduler_max_history_lines()
                if len(lines) > _max_lines:
                    with open(TASK_HISTORY_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines[-_max_lines:])
        except Exception as e:
            logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 裁剪历史失败: {e}'}))

    def get_history(self, limit: int = 100, offset: int = 0,
                    task_type: str = "") -> List[Dict[str, Any]]:
        """获取执行历史"""
        try:
            if not TASK_HISTORY_FILE.exists():
                return []
            with open(TASK_HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            records = []
            for line in lines:
                try:
                    records.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
            if task_type:
                records = [r for r in records if r.get("type") == task_type]
            records.reverse()
            return records[offset:offset + limit]
        except Exception as e:
            logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 读取历史失败: {e}'}))
            return []

    def _save_heartbeat(self, hb_data: Dict) -> None:
        """保存心跳历史"""
        try:
            HEARTBEAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            history = {"latest": hb_data, "history": []}
            if HEARTBEAT_HISTORY_FILE.exists():
                with open(HEARTBEAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    history["history"] = existing.get("history", [])
            # 添加简化记录到历史数组
            simplified = {
                "timestamp": hb_data.get("timestamp"),
                "status": hb_data.get("status"),
                "cpu": hb_data.get("checks", {}).get("system", {}).get("cpu"),
                "memory": hb_data.get("checks", {}).get("system", {}).get("memory"),
                "llm_latency_ms": hb_data.get("checks", {}).get("llm", {}).get("latency_ms"),
            }
            history["history"].append(simplified)
            # 配置化：从 Config 读取心跳历史保留条数（支持热加载）
            from agent.monitoring.observability_config import get_scheduler_max_heartbeat_history
            _max_hb_history = get_scheduler_max_heartbeat_history()
            if len(history["history"]) > _max_hb_history:
                history["history"] = history["history"][-_max_hb_history:]
            with open(HEARTBEAT_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 保存心跳失败: {e}'}))

    def get_heartbeat_status(self) -> Dict:
        """获取心跳概览"""
        try:
            if HEARTBEAT_HISTORY_FILE.exists():
                with open(HEARTBEAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"latest": {"status": "unknown"}, "history": []}

    def load_from_json(self, path: Optional[str] = None) -> int:
        """从 JSON 文件加载 API 创建的系统命令任务"""
        path = path or str(SCHEDULED_TASKS_FILE)
        count = 0
        try:
            if not os.path.exists(path):
                return 0
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t in data.get("tasks", []):
                if not t.get("enabled", True):
                    continue
                self.add_command_task(
                    name=t["name"],
                    command=t["command"],
                    interval_sec=t.get("interval_sec", 60),
                    task_id=t.get("id", ""),
                    enabled=t.get("enabled", True),
                )
                count += 1
            logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'json.count', 'msg': f'[TaskScheduler] 从 JSON 加载了 {count} 个任务'}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'json', 'msg': f'[TaskScheduler] 加载 JSON 任务失败: {e}'}))
        return count

    def start_daemon(self, check_interval: Optional[int] = None) -> None:
        """以 daemon 线程方式启动调度器（非阻塞）

        Args:
            check_interval: tick 检查间隔（秒），None 时从 Config 读取
        """
        # 配置化：从 Config 读取默认检查间隔（支持热加载）
        if check_interval is None:
            from agent.monitoring.observability_config import get_scheduler_check_interval
            check_interval = get_scheduler_check_interval()
        if self.running:
            logger.warning(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 调度器已在运行'}))
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(check_interval,),
            daemon=True,
            name="task-scheduler",
        )
        self._thread.start()
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'daemon.check_interval', 'msg': f'[TaskScheduler] 调度器 daemon 线程已启动 (检查间隔={check_interval}秒)'}))

    def _run_loop(self, check_interval: int) -> None:
        """调度器主循环"""
        while self.running:
            try:
                self.tick()
            except Exception as e:
                logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'tick', 'msg': f'[TaskScheduler] tick 错误: {e}'}))
            time.sleep(check_interval)
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 调度器已停止'}))

    def tick(self) -> None:
        """检查并执行到期的任务"""
        for task in self.tasks:
            if self._should_run(task):
                self.run_task(task)

    def execute_now(self, task_id: str) -> Optional[Dict[str, Any]]:
        """立即执行指定任务（手动触发）

        TASK-S2-03 §6.1：人工重跑 = 介入（``rerun`` 权重 1）——这是「重跑」在云枢的
        真实发生点（用户不经调度周期直接触发任务），埋点 best-effort 不影响执行。
        """
        task = self.get_task(task_id)
        if not task:
            return None
        _record_rerun(task_id, str(task.get("name") or ""))
        # 人工重跑 ⇒ trigger="manual"：**不**上报 scheduled 来源。
        # Why：scheduled-no-write / scheduled-no-edit 两条 ABAC 规则防的是"无人值守"任务
        # 乱改东西；而人工点"立即执行"时有人在场，若一并按 scheduled 处理，手动重跑一个
        # 生成报告的任务反而写不出文件（误伤）。
        return self.run_task(task, trigger="manual")

    def stop(self) -> None:
        """停止调度器"""
        self.running = False
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 调度器已停止'}))

    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务（序列化版本，不含 func）"""
        result = []
        for t in self.tasks:
            entry = {
                "task_id": t.get("task_id", ""),
                "name": t["name"],
                "type": t["type"],
                "enabled": t.get("enabled", True),
                "last_run": t["last_run"].isoformat() if t.get("last_run") else None,
            }
            if t["type"] == "system_command":
                entry["command"] = t.get("command", "")
                entry["interval_sec"] = t.get("interval", 60)
            elif t["type"] == "python_func":
                if "cron" in t:
                    entry["cron"] = t["cron"]
                elif "interval" in t:
                    entry["interval_sec"] = t["interval"]
            elif t["type"] == "heartbeat":
                # 配置化：从 Config 读取心跳间隔（支持热加载）
                from agent.monitoring.observability_config import get_scheduler_heartbeat_interval
                entry["interval_sec"] = get_scheduler_heartbeat_interval()
            result.append(entry)
        return result


_scheduler: Optional[TaskScheduler] = None  # 保留作为 fallback


def _create_scheduler(config=None):
    """TaskScheduler 工厂（含预注册任务，供 SingletonManager 使用）

    Why: 原 get_scheduler 内的创建 + 预注册逻辑整体移入工厂，
    确保 SingletonManager 首次创建时行为与旧实现一致。
    """
    sched = TaskScheduler()
    # 预注册 Python 函数任务
    sched.add_cron_task(
        name="生成周报",
        func=generate_weekly_report,
        day_of_week=0,
        hour=9,
        minute=0,
    )
    sched.add_cron_task(
        name="清理旧日志",
        func=cleanup_old_logs,
        day_of_week=None,
        hour=2,
        minute=0,
    )
    return sched


def _cleanup_scheduler(sched):
    """清理钩子：停止调度器线程（仅测试重置时调用）"""
    if sched is not None and sched.running:
        sched.stop()


def get_scheduler() -> TaskScheduler:
    """获取调度器实例（单例）"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("task_scheduler")
    global _scheduler
    if _scheduler is None:
        _scheduler = _create_scheduler()
    return _scheduler


def reset_scheduler():
    """重置调度器单例（仅用于测试）

    Why(不易): 必须同时清空 SingletonManager 缓存与 fallback 变量。
    判定依据用 reset_singleton 是否可用，而非 _SINGLETON_AVAILABLE 的当前值——
    否则该标志被测试 monkeypatch 为 False 时跳过 reset_singleton，
    残留的 manager 缓存会让后续（顺序 shuffle 后）的并发测试拿到旧实例
    （created==0，而非预期的 1）。
    """
    global _scheduler
    if reset_singleton is not None:
        reset_singleton("task_scheduler")
    _scheduler = None


# ── 预定义任务函数 ──

def generate_weekly_report():
    """生成周报"""
    logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 生成周报任务'}))
    try:
        from agent.weekly_report_generator import run_weekly_report
        report, files = run_weekly_report(
            output_dir=str(DATA_DIR / "reports"),
            save_formats=["json", "html", "text"],
        )
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'len.files', 'msg': f'[TaskScheduler] 周报生成完成: {len(files)} 个文件'}))
    except Exception as e:
        logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 周报生成失败: {e}'}))


def cleanup_old_logs():
    """清理旧日志"""
    logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 清理旧日志任务'}))
    try:
        import shutil
        log_dir = DATA_DIR / "blackbox"
        if log_dir.exists():
            cutoff_date = datetime.now().timestamp() - (30 * 24 * 60 * 60)
            for file in log_dir.glob("blackbox_*.jsonl"):
                if file.stat().st_mtime < cutoff_date:
                    file.unlink()
                    logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'file.name', 'msg': f'[TaskScheduler] 删除旧日志: {file.name}'}))
        logger.info(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': '[TaskScheduler] 日志清理完成'}))
    except Exception as e:
        logger.error(log_dict({'module_name': 'task_scheduler', 'action': 'log', 'msg': f'[TaskScheduler] 日志清理失败: {e}'}))


# ── 心跳检测函数 ──

def perform_heartbeat_check(yunshu_instance=None) -> Dict[str, Any]:
    """执行全维度心跳健康检查"""
    timestamp = datetime.now().isoformat()
    checks = {}
    all_ok = True

    # 1. 系统资源检查
    try:
        if yunshu_instance and hasattr(yunshu_instance, 'body'):
            readings = yunshu_instance.body.collect_quick()
            cpu = memory = disk = None
            for r in readings:
                d = r.to_dict()
                name = d.get("sensor_name", "")
                if name == "cpu_usage":
                    cpu = d.get("value")
                elif name == "memory_usage":
                    memory = d.get("value")
            checks["system"] = {
                "status": "ok" if (cpu is None or cpu < 90) and (memory is None or memory < 90) else "warn",
                "cpu": cpu, "memory": memory,
            }
        else:
            import psutil
            cpu = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            checks["system"] = {
                "status": "ok" if cpu < 90 and memory < 90 and disk < 95 else "warn",
                "cpu": cpu, "memory": memory, "disk": disk,
            }
    except Exception as e:
        checks["system"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 2. LLM 连通性
    try:
        if yunshu_instance and hasattr(yunshu_instance, '_llm') and yunshu_instance._llm:
            llm = yunshu_instance._llm
            checks["llm"] = {
                "status": "ok",
                "provider": getattr(llm, 'provider', 'unknown'),
                "model": getattr(llm, 'model', 'unknown'),
                "latency_ms": 0,
            }
        else:
            checks["llm"] = {"status": "not_configured", "message": "LLM 未配置"}
    except Exception as e:
        checks["llm"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 3. 记忆系统
    try:
        if yunshu_instance and hasattr(yunshu_instance, '_memory'):
            memory_mgr = yunshu_instance._memory
            checks["memory"] = {
                "status": "ok",
                "message": "MemoryManager 正常运行",
            }
        else:
            checks["memory"] = {"status": "not_available", "message": "记忆系统不可用"}
    except Exception as e:
        checks["memory"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 4. 调度器状态
    try:
        if _SINGLETON_AVAILABLE and is_initialized("task_scheduler"):
            sched = get_singleton("task_scheduler")
        else:
            sched = _scheduler
        if sched and sched.running:
            checks["scheduler"] = {
                "status": "ok",
                "running": True,
                "tasks": len(sched.tasks),
            }
        else:
            checks["scheduler"] = {"status": "stopped", "running": False, "tasks": 0}
            all_ok = False
    except Exception as e:
        checks["scheduler"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 5. 关键线程
    try:
        main_threads = ["task-scheduler"]
        alive = []
        for t in threading.enumerate():
            if t.name in main_threads:
                alive.append(t.name)
        checks["threads"] = {
            "status": "ok" if len(alive) == len(main_threads) else "warn",
            "total": threading.active_count(),
            "alive": alive,
        }
    except Exception as e:
        checks["threads"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    overall_status = "healthy" if all_ok else "degraded"
    for c in checks.values():
        if c.get("status") == "error":
            overall_status = "unhealthy"
            break

    return {
        "timestamp": timestamp,
        "status": overall_status,
        "checks": checks,
    }


# 注册单例工厂（置于文件末尾，确保预注册任务函数已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("task_scheduler", _create_scheduler, cleanup_fn=_cleanup_scheduler)
