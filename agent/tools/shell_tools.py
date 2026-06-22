"""Shell 执行工具——从 system_tools.py 拆出

包含：Shell 类型检测、输出截断、命令执行等操作。
"""
import os
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

# Shell 类型与执行命令的映射
_SHELL_COMMANDS = {
    "bash": ["bash", "-c"],
    "cmd": ["cmd", "/c"],
    "powershell": ["powershell", "-Command"],
}

# Unix 风格特征（检测到这些则倾向使用 bash）
_UNIX_SHELL_PATTERNS = [
    r"\$\(.*\)",      # $() 命令替换
    r"grep\s+",       # grep
    r"ls\s+-[lahr]",  # ls -l/a/h/r
    r"ps\s+\-?(aux|ef)", # ps aux/ef/-ef
    r"chmod\s+",      # chmod
    r"chown\s+",      # chown
    r"rm\s+-[rf]",    # rm -r/-f
    r"mv\s+",         # mv
    r"cp\s+",         # cp
    r"cat\s+",        # cat
    r"less\s+",       # less
    r"tail\s+",       # tail
    r"head\s+",       # head
    r"which\s+",      # which
    r"whoami",        # whoami
    r"pwd",           # pwd
]

# PowerShell cmdlet 特征（检测到这些则使用 powershell）
_PS_CMDLET_PATTERNS = [
    r"(Get|Set|Write|Read|Invoke|Remove|New|Add|Select|Where|ForEach)-",
    r"\$Env:",         # PowerShell 环境变量
    r"\$_\s*\.",      # PowerShell 管道变量
    r"\$\w+\s*=",     # PowerShell 变量赋值
    r"\bWrite-(Host|Output|Error|Warning)",
    r"\bGet-(Process|Service|ChildItem|Content|Date|Item)",
    r"\bSet-(ExecutionPolicy|Location|Content)",
    r"\bRemove-Item",
]


def _detect_shell(command: str) -> str:
    """根据命令内容智能检测适合的 shell 类型

    Args:
        command: 要执行的命令字符串

    Returns:
        str: "bash", "cmd" 或 "powershell"
    """
    # 先检测 PowerShell cmdlet（特征最明显）
    for pattern in _PS_CMDLET_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "powershell"

    # 再检测 Unix 风格特征
    for pattern in _UNIX_SHELL_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "bash"

    # Windows 环境下的 cmd 常见命令
    if os.name == "nt":
        cmd_only_patterns = [
            r"\bdir\s+",
            r"\btype\s+",
            r"\bfind\s+",
        ]
        for pattern in cmd_only_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return "cmd"

    # 默认使用 bash（云枢运行在 Git Bash 环境）
    return "bash"


def _truncate_output(text: str, max_bytes: int = 102400) -> str:
    """截断过长输出，防止爆内存

    Args:
        text: 原始输出文本
        max_bytes: 最大字节数，默认 100KB

    Returns:
        str: 截断后的文本（可能附加 truncated 标注）
    """
    if not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + f"\n...（输出已截断，共 {len(encoded)} 字节）"


def execute_shell(command: str, shell: str = "auto", cwd: str = None, timeout: int = 30) -> dict:
    """在 shell 中执行命令并返回结果

    注意：此函数本身不进行命令安全检查（如危险命令过滤）。
    调用方（如 digital_life.py 中的工具注册层）应负责使用 SafetyGuard
    和 PermissionSystem 执行安全扫描。

    Args:
        command: 要执行的命令字符串
        shell: "auto" / "bash" / "cmd" / "powershell"
        cwd: 工作目录，默认使用当前目录
        timeout: 超时秒数，会被限制在 1-120 范围内，默认 30

    Returns:
        dict: {ok: bool, stdout: str, stderr: str, exit_code: int, shell: str, cwd: str}
    """
    if not command or not command.strip():
        return {"ok": False, "error": "命令不能为空", "exit_code": -1}

    # 1. 确定 shell 类型
    shell = _detect_shell(command) if shell == "auto" else shell.lower()
    if shell not in _SHELL_COMMANDS:
        return {"ok": False, "error": f"不支持的 shell 类型: {shell}，可选: auto/bash/cmd/powershell", "exit_code": -1}

    # 2. 构建执行命令
    shell_cmd = _SHELL_COMMANDS[shell]
    cmd = shell_cmd + [command]

    # 3. 确定工作目录
    work_dir = cwd or os.getcwd()

    # 4. 限制超时
    timeout = max(1, min(timeout, 120))

    # 5. 执行
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
        stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))

        return {
            "ok": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "shell": shell,
            "cwd": work_dir,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"命令执行超时（{timeout}秒）",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
    except FileNotFoundError as e:
        return {
            "ok": False,
            "error": f"找不到 shell 程序: {e}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"执行失败: {e}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }


__all__ = [
    "execute_shell", "_detect_shell", "_truncate_output",
]
