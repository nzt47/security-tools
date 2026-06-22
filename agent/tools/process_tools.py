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


def start_process(program, args=None, cwd=None):
    """启动白名单程序"""
    prog_lower = program.lower()
    allowed = False
    wl = get_process_whitelist()
    for w in wl:
        if prog_lower == w or prog_lower.endswith("\\" + w):
            allowed = True
            break
    if not allowed:
        return {"ok": False, "error": f"程序不在白名单中。允许: {', '.join(wl)}"}

    try:
        cmd = [program] + (args if args else [])
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
