"""进程管理工具——从 system_tools.py 拆出

包含：白名单管理、进程启动、列表、停止等操作。
"""
import os
import json
import subprocess
import logging

logger = logging.getLogger(__name__)

# 内置默认白名单（不可删除）
_DEFAULT_WHITELIST = [
    "notepad.exe", "calc.exe", "mspaint.exe", "write.exe",
    "python.exe", "python3.exe", "pip.exe",
    "node.exe", "npm.cmd", "npx.cmd",
    "git.exe", "curl.exe", "wget.exe",
    "explorer.exe", "cmd.exe",
]
PROCESS_WHITELIST = _DEFAULT_WHITELIST  # 向后兼容

_WHITELIST_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'process_whitelist_custom.json')
WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'workspace')


def _load_custom_whitelist() -> list[str]:
    """加载用户自定义白名单条目"""
    try:
        with open(_WHITELIST_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("custom", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_custom_whitelist(entries: list[str]):
    """保存用户自定义白名单条目"""
    os.makedirs(os.path.dirname(_WHITELIST_CONFIG_FILE), exist_ok=True)
    with open(_WHITELIST_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({"custom": entries}, f, ensure_ascii=False, indent=2)


def get_process_whitelist() -> list[str]:
    """获取完整白名单（默认 + 自定义）"""
    return _DEFAULT_WHITELIST + _load_custom_whitelist()


def add_whitelist_entry(program: str) -> dict:
    """添加自定义白名单条目"""
    program = program.strip().lower()
    if not program:
        return {"ok": False, "error": "程序名不能为空"}
    if program in _DEFAULT_WHITELIST:
        return {"ok": False, "error": f"「{program}」已在默认白名单中"}
    custom = _load_custom_whitelist()
    if program in custom:
        return {"ok": False, "error": f"「{program}」已存在"}
    custom.append(program)
    _save_custom_whitelist(custom)
    logger.info(f"白名单新增: {program}")
    return {"ok": True, "program": program}


def remove_whitelist_entry(program: str) -> dict:
    """移除自定义白名单条目"""
    program = program.strip().lower()
    if not program:
        return {"ok": False, "error": "程序名不能为空"}
    if program in _DEFAULT_WHITELIST:
        return {"ok": False, "error": f"「{program}」是默认条目，不能删除"}
    custom = _load_custom_whitelist()
    if program not in custom:
        return {"ok": False, "error": f"「{program}」不在自定义白名单中"}
    custom.remove(program)
    _save_custom_whitelist(custom)
    logger.info(f"白名单移除: {program}")
    return {"ok": True, "program": program}


def get_whitelist_detail() -> dict:
    """获取白名单详情（区分默认和自定义）"""
    return {
        "default": _DEFAULT_WHITELIST,
        "custom": _load_custom_whitelist(),
        "all": get_process_whitelist(),
    }


_MAX_ARGS = 64
_MAX_ARG_LEN = 4096


def _resolve_whitelisted_program(program):
    """把用户输入的程序名**换成白名单里的那一条**（命中则返回白名单元素，否则 None）。

    [不易] CodeQL py/command-line-injection（告警 #598）：直接把 program 交给
    Popen，等于让用户字符串决定「执行哪个可执行文件」。这里改成只拿用户串做**比较**，
    真正进 argv[0] 的是白名单元素本身（`_DEFAULT_WHITELIST` 常量，或
    操作者经 add_whitelist_entry 显式登记过的自定义条目）—— 用户输入不进命令。
    兼容原有两种写法：`notepad.exe` 与带目录的 `C:/Tools/notepad.exe`（按尾段等值匹配）。
    """
    if not isinstance(program, str):
        return None
    prog_lower = program.strip().lower()
    if not prog_lower:
        return None
    for w in get_process_whitelist():
        if prog_lower == w or prog_lower.endswith("\\" + w) or prog_lower.endswith("/" + w):
            return w
    return None


def _sanitize_args(args):
    """校验参数形态；不合法返回 None。

    只做「形状」约束（必须是字符串列表、长度有上界、不含 NUL），不改写内容：
    Popen 走 shell=False 的列表形式，参数不会被 shell 解释，无需转义。
    """
    if args is None:
        return []
    if isinstance(args, str) or not isinstance(args, (list, tuple)):
        return None
    if len(args) > _MAX_ARGS:
        return None
    out = []
    for a in args:
        if not isinstance(a, str) or len(a) > _MAX_ARG_LEN or "\x00" in a:
            return None
        out.append(a)
    return out


def start_process(program, args=None, cwd=None):
    """启动白名单程序（argv[0] 取白名单元素，不取用户字符串）"""
    resolved = _resolve_whitelisted_program(program)
    if resolved is None:
        return {"ok": False, "error": f"程序不在白名单中。允许: {', '.join(get_process_whitelist())}"}

    argv = _sanitize_args(args)
    if argv is None:
        return {"ok": False, "error": f"args 不合法：需为字符串列表（≤{_MAX_ARGS} 项、每项 ≤{_MAX_ARG_LEN} 字符、不含 NUL）"}

    try:
        cmd = [resolved, *argv]
        proc = subprocess.Popen(
            cmd, cwd=cwd or WORKSPACE_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"ok": True, "pid": proc.pid, "program": program}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_processes():
    """列出运行中的白名单进程"""
    import psutil
    result = []
    wl = get_process_whitelist()
    for proc in psutil.process_iter(["pid", "name", "create_time", "status"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            if any(name == w.lower() for w in wl):
                result.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "status": info["status"],
                })
        except Exception:
            pass
    return result


def stop_process(pid):
    """终止指定进程（仅限白名单程序）"""
    import psutil
    try:
        proc = psutil.Process(pid)
        name = (proc.name() or "").lower()
        wl = get_process_whitelist()
        if not any(name == w.lower() for w in wl):
            return {"ok": False, "error": f"进程 {name} 不在白名单中，拒绝终止"}
        proc.terminate()
        return {"ok": True, "pid": pid, "name": proc.name()}
    except psutil.NoSuchProcess:
        return {"ok": False, "error": "进程不存在"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__all__ = [
    "PROCESS_WHITELIST",
    "get_process_whitelist", "add_whitelist_entry", "remove_whitelist_entry",
    "get_whitelist_detail", "start_process", "list_processes", "stop_process",
]
