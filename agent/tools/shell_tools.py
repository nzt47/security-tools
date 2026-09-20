"""Shell 执行工具——从 system_tools.py 拆出

包含：Shell 类型检测、输出截断、命令执行等操作。

【TASK-07 第 4 步：沙箱统一收口（`run_sandboxed` 从"零调用方"变成真实执行器）】

    改动前：本模块第 5 步直接 `subprocess.run(["bash","-c",command])`，
            docstring **自认**"不进行命令安全检查"；而仓库里已经有一个**默认拒绝**
            的实现 `agent/subagent/sandbox.py::Sandbox.run_sandboxed()`
            ——它**生产零调用方**（唯一引用是测试）。
    改动后：每次执行先过 `Sandbox.validate_command()`（默认拒绝 + 危险模式），
            再由 `Sandbox.run_sandboxed()` 作为**执行器**（子进程 + 超时 kill +
            输出截断 + 路径白名单）。

    **为什么默认是影子模式（`shadow`）而不是直接 `enforce`**：TASK-07 §6 明确要求
    "`shell_execute` 路径的沙箱化**必须**先用影子模式（只告警不拦）跑一个周期，
    因为**它会影响 LLM 的正常 shell 使用**"。故三档：
        `off`     完全不接线（等价改动前）
        `shadow`  **默认**：判定照跑、命中照记审计，但**不阻断**执行
        `enforce` 命中即拒绝
    **注意**：即使在 `shadow` 下，**执行器**也已经换成 `run_sandboxed`
    ——"接进真实执行路径"这件事不打折，打折的只是"判定是否阻断"这一档。

    **为什么保留 `bash -c` 而不是改成 argv 直连**：`run_sandboxed` 的 argv 形态
    （`shell=False`）无法表达管道/重定向/`&&`，直接换掉会让 LLM 的正常 shell 用法
    （`ls | head`）全线失效。TASK-07 §4 第 3 项要求"避免 `bash -c`"针对的是
    **命令拼接注入**；这里的 `command` 来自 LLM 决策层参数、且整串已过
    `validate_command`，故保留 shell 语义但**标注为高风险**（见下方审计字段
    `shell_semantics: true`），并把"白名单 + argv"列为遗留待办（`process_whitelist_custom.json`
    已有设施，但覆盖 LLM 的自由 shell 用法需要产品决策，不在本任务单方面收紧）。
"""
import hashlib
import os
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

#: Shell 沙箱化档位：off / shadow / enforce（默认 shadow，理由见模块 docstring）
SANDBOX_MODE_ENV = "CP_SANDBOX_SHELL_MODE"
_SANDBOX_MODES = ("off", "shadow", "enforce")

#: 输出上限（与改动前 `_truncate_output` 的 100KB 对齐，避免"接了沙箱"变成行为变更）
_MAX_OUTPUT_BYTES = 102400

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


def sandbox_mode() -> str:
    """当前 Shell 沙箱化档位（off / shadow / enforce；非法值回落 shadow 并告警）"""
    raw = str(os.environ.get(SANDBOX_MODE_ENV, "shadow")).strip().lower()
    if raw in _SANDBOX_MODES:
        return raw
    if raw in ("1", "true", "yes", "on"):
        # 兼容"用 1/0 表示开关"的习惯写法：1 = enforce（最强），0 = off
        return "enforce"
    if raw in ("0", "false", "no"):
        return "off"
    logger.warning("[shell] %s=%r 不是合法档位 %s，回落到 shadow",
                   SANDBOX_MODE_ENV, raw, list(_SANDBOX_MODES))
    return "shadow"


def _audit_sandbox(event: str, *, command: str, reason: str,
                   matched_pattern: str = "", mode: str = "",
                   surface: str = "") -> None:
    """沙箱判定入审计（best-effort；**不写命令原文**）

    【为什么只写摘要与长度】TASK-07 §3 第 5 步明确要求"拦截事件里不得写入完整敏感
    URL/命令原文（可能含密钥）"。这里留 `command_chars` + `command_digest`
    （sha256 前 16 位）：既能用摘要反查同一条命令的多次出现，又不落原文。
    """
    try:
        from agent.audit.facade import audit
        audit.record(
            event,
            actor="tools.shell_tools",
            subject=f"sandbox:{matched_pattern or 'ok'}",
            payload={
                "mode": mode,
                "reason": str(reason or "")[:300],
                "matched_pattern": str(matched_pattern or ""),
                "command_chars": len(str(command or "")),
                "command_digest": "sha256:" + hashlib.sha256(
                    str(command or "").encode("utf-8", errors="replace")).hexdigest()[:32],
                "command_recorded": False,
                "shell_semantics": True,
                "surface": str(surface or ""),
            },
            source="agent",
        )
    except Exception as exc:  # noqa: BLE001  审计失败不影响执行判定
        logger.debug("[shell] 沙箱审计写入失败: %s", exc)


def _legacy_execute(cmd: list, work_dir: str, timeout: int, shell: str) -> dict:
    """改动前的执行路径（`off` 档与影子模式回退用；行为逐字保持）"""
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


def _sandboxed_execute(cmd: list, work_dir: str, timeout: int, shell: str) -> dict:
    """经 `Sandbox.run_sandboxed()` 执行（**真实执行路径的统一收口**）

    【为什么用 argv 形态而不是把整串再交给 shell】`run_sandboxed` 的语义是
    "Popen 无 shell"（避免 shell 解析带来的二次注入面）。而 `execute_shell` 的语义
    是"在 shell 里执行一条命令"，故 argv 就是 `[shell, "-c", command]`
    —— `shell` 由白名单常量 `_SHELL_COMMANDS` 给出，`command` 是**单个** argv 元素，
    不会再被本进程解析一次。
    """
    from agent.subagent.sandbox import (SandboxResourceLimits, get_tool_sandbox)
    limits = SandboxResourceLimits(timeout_s=float(timeout),
                                   max_output_bytes=_MAX_OUTPUT_BYTES)
    result = get_tool_sandbox().run_sandboxed(
        cmd, limits=limits,
        # 【关于 allowed_paths】工具层没有"工作区根"概念（那是 file_tools/
        # subagent 的东西）。这里把**调用方给出的 cwd 自身**作为允许根，
        # 使 `run_sandboxed` 的路径白名单从"必然拒绝带 cwd 的调用"变成"按 cwd 约束"；
        # 路径穿越的真实防线在 `safe_resolve_path`（file_tools）与本函数上游。
        allowed_paths=[work_dir] if work_dir else None,
        cwd=work_dir,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if not result.allowed:
        return {
            "ok": False,
            "error": f"沙箱拒绝执行: {result.reason}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
            "blocked": True,
            "blocked_by": "subagent.sandbox",
        }
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
        "shell": shell,
        "cwd": work_dir,
        "sandboxed": True,
        "timed_out": result.timed_out,
        **({"error": f"命令执行超时（{timeout}秒）", "exit_code": -1}
           if result.timed_out else {}),
    }


def execute_shell(command: str, shell: str = "auto", cwd: str = None, timeout: int = 30) -> dict:
    """在 shell 中执行命令并返回结果

    【安全边界（TASK-07 更新）】
      改动前本函数 docstring 写的是"不进行命令安全检查，调用方应负责"。
      现状是**三层**：
        ① 调用方（`agent/tools/system_tools.py:110-136`）的 `check_text` /
           `check_action` —— **一字未改**，仍在最前面；
        ② 本函数的 `Sandbox.validate_command()`（默认拒绝 + 危险模式，
           新增于 TASK-07，受 `CP_SANDBOX_SHELL_MODE` 控制，默认影子模式）；
        ③ `Sandbox.run_sandboxed()` 的执行期约束（子进程 / 超时 kill / 输出截断
           / 工作目录白名单 / 无 shell=True）。

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

    # 5. 沙箱判定 + 沙箱执行（TASK-07 第 4 步）
    mode = sandbox_mode()
    if mode == "off":
        return _legacy_execute(cmd, work_dir, timeout, shell)

    sandbox = None
    verdict = None
    try:
        from agent.subagent.sandbox import get_tool_sandbox
        sandbox = get_tool_sandbox()
        verdict = sandbox.validate_command(" ".join(cmd))
    except Exception as exc:  # noqa: BLE001  沙箱不可用
        # 【fail-open 还是 fail-closed】沙箱是**新增层**；它自身故障时按既有路径执行
        # （通用硬约束 1：新增机制失败不得阻断主流程），但**记审计**，让"沙箱没生效"
        # 这件事可见（而不是静默退化成无沙箱）。
        _audit_sandbox("sandbox_unavailable", command=command,
                       reason=f"{type(exc).__name__}: {exc}", mode=mode)
        logger.warning("[shell] 沙箱不可用（按既有路径执行并记审计）: %s", exc)
        return _legacy_execute(cmd, work_dir, timeout, shell)

    if verdict is not None and not verdict.allowed:
        if mode == "enforce":
            _audit_sandbox("sandbox_denied", command=command, reason=verdict.reason,
                           matched_pattern=verdict.matched_pattern or "", mode=mode)
            return {
                "ok": False,
                "error": f"沙箱拒绝执行: {verdict.reason}",
                "exit_code": -1,
                "shell": shell,
                "cwd": work_dir,
                "blocked": True,
                "blocked_by": "subagent.sandbox",
                "matched_pattern": verdict.matched_pattern or "",
            }
        # 影子模式：只记不拦（TASK-07 §6 规定的灰度期语义）
        _audit_sandbox("sandbox_would_deny", command=command, reason=verdict.reason,
                       matched_pattern=verdict.matched_pattern or "", mode=mode)
        return _legacy_execute(cmd, work_dir, timeout, shell)

    return _sandboxed_execute(cmd, work_dir, timeout, shell)


__all__ = [
    "execute_shell", "_detect_shell", "_truncate_output", "sandbox_mode",
    "SANDBOX_MODE_ENV",
]
