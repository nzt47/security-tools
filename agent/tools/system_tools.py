"""工具注册模块 — 系统工具（天气、进程管理、Shell 执行）"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def _submit_background_shell(command, shell, cwd, timeout):
    """把 shell 命令投递到 AsyncExecutor 后台执行（fire-and-forget），立即返回 task_id。

    【为什么复用 AsyncExecutor 而不是自己开线程】「句柄 + 状态 + 结果 TTL + 取消」这套
    语义已由 agent/async_executor.py 提供，并配有 get_task_status / get_task_result /
    cancel_task / list_async_tasks 四个既有工具与 /api/background/tasks* 路由；
    自建一套必然与它漂移。

    【边界（当前实现的真实口径，勿想当然）】
      · 并发：AsyncExecutor 线程池 max_workers（默认 3）；溢出任务排队而非拒绝。
      · 结果：保存在内存，TTL 默认 3600s；进程重启后内存里的任务丢失
        （data/async_tasks.jsonl 只作留痕，不用于恢复）。
      · 取消：cancel_task 只把状态改成 cancelled，线程池无法中断已在运行的子进程
        ⇒ 命令可能继续跑到自己的 timeout；要真正终止需按 PID 处理。
      · 输出/时长：仍走同一条 execute_shell，故输出 100KB 截断、timeout 上限 120s
        照旧生效。后台化解决的是「不阻塞对话」，不是「无限时长」。
      · 二次过闸：工作线程内经 agent.tools.call 再走一遍 tool_gate 与权限检查
        （与既有 submit_task 同口径）⇒ 需要交互式审批的命令不适合走后台。
      · 隐私：params（含命令原文）会被 AsyncExecutor 落进 data/async_tasks.jsonl
        （运行时台账、不入库）；与 shell_tools 审计只记摘要的口径不同，属已知边界。
    """
    try:
        from agent.async_executor import get_async_executor
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"后台执行器不可用: {exc}"}

    # 【身份透传】ThreadPoolExecutor 不继承 contextvars，必须在 submit 时显式带上
    # 来源与身份；否则闸门会把「模型发起的后台调用」记成「人从 CLI 调的」。
    # 口径与 agent/tools/code_tools.py 的 _submit_task 一致。
    _src = ""
    try:
        from agent.tool_gate import current_session_source as _cur_src
        _src = str(_cur_src() or "").strip()
    except Exception:  # noqa: BLE001
        _src = ""
    _ident = ""
    try:
        from agent.tool_gate import current_execution_identity as _cur_ident
        _ident = str(_cur_ident() or "").strip().lower()
    except Exception:  # noqa: BLE001
        _ident = ""

    try:
        res = get_async_executor().submit(
            name=f"shell_execute:{shell}",
            tool_name="shell_execute",
            # background 不入 params：否则工作线程会再次转后台，形成无限套娃。
            params={"command": command, "shell": shell, "cwd": cwd, "timeout": timeout},
            timeout=None,
            session_source=_src or "api",
            identity=_ident or "llm",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"后台任务提交失败: {exc}"}
    if not res.get("ok"):
        return res
    return {
        "ok": True,
        "background": True,
        "task_id": res.get("task_id"),
        "status": res.get("status", "pending"),
        "shell": shell,
        "hint": ("命令已在后台启动。用 get_task_status(task_id) 查状态，"
                 "用 get_task_result(task_id) 取结果，用 cancel_task(task_id) 取消。"),
    }


def _dispatch_shell(command, shell, cwd, timeout, background=False):
    """按 background 分流：前台阻塞执行 / 后台 fire-and-forget。

    单独抽出来是为了让「分流」本身可被单测直接覆盖（_shell_execute 是 register_all
    内部的闭包，不便直接调）。
    """
    if background:
        return _submit_background_shell(command, shell, cwd, timeout)
    from agent.tools.shell_tools import execute_shell
    return execute_shell(command, shell=shell, cwd=cwd, timeout=timeout)


def register_all(dl):
    """注册所有系统工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    # ════════════════════════════════════════════════════════════
    #  天气查询工具
    # ════════════════════════════════════════════════════════════

    from agent.system_tools import get_weather

    @_tools.register("get_weather", "查询天气信息。使用 wttr.in 服务，无需 API Key。支持三种格式：text（简洁文本）、json（完整JSON数据）、full（完整文本预报）。Get weather forecast, temperature for a city", schema={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称，如 Beijing、Shanghai、Tokyo，留空自动查询当前IP所在地天气"},
            "format": {"type": "string", "enum": ["text", "json", "full"], "description": "返回格式：text=简洁文本, json=完整JSON数据, full=完整文本预报"},
        },
    })
    def _get_weather(**kwargs):
        city = kwargs.get("city", "")
        fmt = kwargs.get("format", "text")
        return get_weather(city=city, format=fmt)

    # ════════════════════════════════════════════════════════════
    #  进程管理工具
    # ════════════════════════════════════════════════════════════

    from agent.system_tools import (
        start_process, list_processes, stop_process,
    )

    @_tools.register("run_program", "在本地运行白名单程序（如 notepad.exe, calc.exe, python.exe）。args 是参数列表，cwd 是工作目录", schema={
        "type": "object",
        "properties": {
            "program": {"type": "string", "description": "程序名称，如 notepad.exe"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "参数列表"},
            "cwd": {"type": "string", "description": "工作目录"},
        },
        "required": ["program"],
    })
    def _run_program(**kwargs):
        program = kwargs.get("program", "")
        args = kwargs.get("args")
        cwd = kwargs.get("cwd")
        if not program:
            return {"ok": False, "error": "请提供要运行的程序名（program）"}
        # 权限检查
        perm = dl._permission.check_action(f"run:{program}", f"运行程序 {program}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
        return start_process(program, args=args, cwd=cwd)

    @_tools.register("list_processes", "列出当前正在运行的白名单程序列表", schema={
        "type": "object",
        "properties": {},
    })
    def _list_processes(**kwargs):
        procs = list_processes()
        return {"ok": True, "processes": procs, "count": len(procs)}

    @_tools.register("stop_process", "终止指定 PID 的白名单程序", schema={
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "description": "进程 PID"},
        },
        "required": ["pid"],
    })
    def _stop_process(**kwargs):
        pid = kwargs.get("pid")
        if pid is None:
            return {"ok": False, "error": "请提供进程 PID"}
        # 权限检查
        perm = dl._permission.check_action(f"stop_process:{pid}", f"终止进程 {pid}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
        return stop_process(pid)

    # ════════════════════════════════════════════════════════════
    #  Shell 执行工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("shell_execute", "在本地执行 shell 命令。Windows 默认使用 cmd，Linux/Mac 使用 bash。支持自动检测或手动指定 shell 类型。返回 stdout、stderr 和退出码。background=true 时改为后台执行（fire-and-forget）：立即返回 task_id，稍后用 get_task_status/get_task_result 取结果、用 cancel_task 取消。注意：危险命令（如 rm -rf）会被安全系统阻止。Run shell commands, execute terminal commands, run in background", schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "shell": {"type": "string", "description": "shell 类型: auto（自动检测）/ bash / cmd / powershell，默认 auto"},
            "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 120"},
            "background": {"type": "boolean", "description": "是否后台执行（fire-and-forget）。true=立即返回 task_id，稍后用 get_task_status/get_task_result 取结果；false（默认）=阻塞等待本次执行结果"},
        },
        "required": ["command"],
    })
    def _shell_execute(**kwargs):
        command = kwargs.get("command", "")
        shell = kwargs.get("shell", "auto")
        cwd = kwargs.get("cwd")
        timeout = kwargs.get("timeout", 30)

        if not command:
            return {"ok": False, "error": "请提供要执行的命令（command）"}

        # PermissionSystem 扫描命令内容
        try:
            check = dl._permission.check_text(command)
            if check.get("level") == "critical":
                matches = [m.get("description", "") for m in check.get("matches", [])]
                return {
                    "ok": False,
                    "error": f"危险命令被安全系统阻止: {matches}",
                    "blocked": True,
                    "level": "critical",
                }
            elif check.get("level") == "warning":
                desc = "; ".join(m.get("description", "") for m in check.get("matches", []))
                perm = dl._permission.check_action(
                    f"shell_execute:warning:{desc[:100]}",
                    f"执行可能危险的命令: {desc}",
                )
                if not perm.allowed:
                    return {
                        "ok": False,
                        "error": f"权限系统拒绝: {perm.reason}",
                        "blocked": True,
                        "level": "warning",
                    }
        except Exception as e:
            logger.warning("[shell_execute] 安全检查异常: %s", e)
            return {"ok": False, "error": "安全检查系统故障，拒绝执行", "blocked": True}

        # 执行命令（timeout=None 时使用默认 30 秒）
        # 【后台执行】background=true 走 fire-and-forget：提交到 AsyncExecutor 后立即
        # 返回 task_id，不阻塞对话。边界（并发上限 / 结果 TTL / 取消语义 / 输出截断 /
        # 超时上限 / 二次过闸）见 _submit_background_shell 的 docstring。
        return _dispatch_shell(command, shell, cwd, timeout or 30,
                               background=bool(kwargs.get("background")))
