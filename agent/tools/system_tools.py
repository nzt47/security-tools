"""工具注册模块 — 系统工具（天气、进程管理、Shell 执行）"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl):
    """注册所有系统工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    # ════════════════════════════════════════════════════════════
    #  天气查询工具
    # ════════════════════════════════════════════════════════════

    from agent.system_tools import get_weather

    @_tools.register("get_weather", "查询天气信息。使用 wttr.in 服务，无需 API Key。支持三种格式：text（简洁文本）、json（完整JSON数据）、full（完整文本预报）", schema={
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
        start_process, list_processes, stop_process, execute_shell,
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

    @_tools.register("shell_execute", "在本地执行 shell 命令。Windows 默认使用 cmd，Linux/Mac 使用 bash。支持自动检测或手动指定 shell 类型。返回 stdout、stderr 和退出码。注意：危险命令（如 rm -rf）会被安全系统阻止", schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "shell": {"type": "string", "description": "shell 类型: auto（自动检测）/ bash / cmd / powershell，默认 auto"},
            "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 120"},
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
        return execute_shell(command, shell=shell, cwd=cwd, timeout=timeout or 30)
