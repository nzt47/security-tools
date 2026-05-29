"""
系统工具集 -- 沙盒、定时任务、浏览器、进程管理、剪贴板、工作区

我是灵犀的"工具箱"——提供受控的系统级操作能力。
"""
import os
import subprocess
import tempfile
import json
import logging
import shutil
import time

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  工作区管理
# ════════════════════════════════════════════════════════════

WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), "..", "workspace")


def init_workspace():
    """初始化受保护的工作区目录"""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    # 创建 .gitkeep
    gitkeep = os.path.join(WORKSPACE_DIR, ".gitkeep")
    if not os.path.exists(gitkeep):
        with open(gitkeep, "w") as f:
            f.write("# 灵犀受保护工作区\n")
    # 创建 readme
    readme = os.path.join(WORKSPACE_DIR, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("灵犀受保护工作区\n此目录内的文件操作受安全策略约束。\n")
    logger.info(f"工作区已初始化: {WORKSPACE_DIR}")
    return WORKSPACE_DIR


def list_workspace(path=""):
    """列出工作区内容"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    if not os.path.exists(full_path):
        return {"path": path, "items": [], "error": "路径不存在"}
    if os.path.isfile(full_path):
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(5000)
        return {"path": path, "type": "file", "size": os.path.getsize(full_path), "content": content}
    items = []
    for name in os.listdir(full_path):
        item_path = os.path.join(full_path, name)
        items.append({
            "name": name,
            "type": "dir" if os.path.isdir(item_path) else "file",
            "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
        })
    return {"path": path, "type": "dir", "items": sorted(items, key=lambda x: (x["type"], x["name"]))}


def write_workspace(path, content):
    """写入工作区文件"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": path, "size": len(content)}


def delete_workspace(path):
    """删除工作区文件/目录"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    if path in ("", ".", "/"):
        raise ValueError("不能删除工作区根目录")
    if os.path.isdir(full_path):
        shutil.rmtree(full_path)
    else:
        os.remove(full_path)
    return {"ok": True, "path": path}


# ════════════════════════════════════════════════════════════
#  Python 沙盒
# ════════════════════════════════════════════════════════════

# 沙盒允许的安全内置函数
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "chr": chr,
    "dict": dict, "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "isinstance": isinstance, "len": len, "list": list,
    "map": map, "max": max, "min": min, "ord": ord, "range": range,
    "reversed": reversed, "round": round, "set": set, "slice": slice,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "print": print, "isinstance": isinstance, "issubclass": issubclass,
    "hasattr": hasattr, "getattr": getattr, "TypeError": TypeError,
    "ValueError": ValueError, "KeyError": KeyError, "IndexError": IndexError,
    "Exception": Exception, "True": True, "False": False, "None": None,
}


def run_sandbox(code, timeout_sec=5):
    """在受限的 Python 沙盒中执行代码"""
    import sys
    import threading

    result = {"stdout": "", "stderr": "", "error": None, "timed_out": False}

    # 创建受限的全局命名空间
    safe_globals = {"__builtins__": _SAFE_BUILTINS}

    # 捕获输出
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    exc = [None]

    def _run():
        try:
            exec(code, safe_globals)
        except Exception as e:
            exc[0] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    result["stdout"] = sys.stdout.getvalue()[:10000]
    result["stderr"] = sys.stderr.getvalue()[:5000]
    result["timed_out"] = thread.is_alive()
    if exc[0]:
        result["error"] = exc[0]
    if result["timed_out"]:
        result["error"] = f"执行超时 ({timeout_sec}秒)"

    sys.stdout = old_stdout
    sys.stderr = old_stderr
    return result


# ════════════════════════════════════════════════════════════
#  定时任务管理 (Windows Task Scheduler)
# ════════════════════════════════════════════════════════════

SCHEDULED_TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "scheduled_tasks.json")


def _load_tasks():
    try:
        with open(SCHEDULED_TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tasks": []}


def _save_tasks(data):
    os.makedirs(os.path.dirname(SCHEDULED_TASKS_FILE), exist_ok=True)
    with open(SCHEDULED_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_scheduled_tasks():
    """列出所有已注册的定时任务"""
    return _load_tasks()


def create_scheduled_task(name, command, interval_sec=60, enabled=True):
    """创建受控的定时任务（仅限白名单命令）"""
    # 白名单检查
    allowed = ["python", "echo", "dir", "type", "curl", "ping"]
    cmd_lower = command.lower()
    if not any(cmd_lower.startswith(a) for a in allowed):
        return {"ok": False, "error": f"命令不在白名单中。允许的命令: {', '.join(allowed)}"}

    data = _load_tasks()
    task = {
        "id": str(int(time.time() * 1000)),
        "name": name,
        "command": command,
        "interval_sec": interval_sec,
        "enabled": enabled,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_run": None,
        "run_count": 0,
    }
    data["tasks"].append(task)
    _save_tasks(data)
    return {"ok": True, "task": task}


def delete_scheduled_task(task_id):
    """删除定时任务"""
    data = _load_tasks()
    before = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    _save_tasks(data)
    return {"ok": True, "deleted": before > len(data["tasks"])}


def toggle_scheduled_task(task_id, enabled):
    """启用/禁用定时任务"""
    data = _load_tasks()
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["enabled"] = enabled
            _save_tasks(data)
            return {"ok": True}
    return {"ok": False, "error": "任务不存在"}


# ════════════════════════════════════════════════════════════
#  无头浏览器控制
# ════════════════════════════════════════════════════════════

_browser_instance = None


def get_browser():
    """获取或创建无头浏览器实例（懒加载）"""
    global _browser_instance
    if _browser_instance is None:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-extensions")
            opts.add_argument("--disable-file-system")
            opts.add_argument("--remote-debugging-port=0")
            _browser_instance = webdriver.Chrome(options=opts)
            _browser_instance.set_page_load_timeout(15)
            logger.info("无头浏览器已启动")
        except ImportError:
            logger.warning("selenium 未安装，浏览器功能不可用")
            return None
        except Exception as e:
            logger.warning(f"无头浏览器启动失败: {e}")
            return None
    return _browser_instance


def browser_navigate(url):
    """导航到指定 URL（仅允许 http/https）"""
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "仅允许 http/https 协议"}
    # 禁止内网地址
    blocked = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172.16."]
    for b in blocked:
        if b in url.lower():
            return {"ok": False, "error": f"禁止访问内网地址"}

    browser = get_browser()
    if not browser:
        return {"ok": False, "error": "浏览器不可用（需要安装 selenium）"}
    try:
        browser.get(url)
        title = browser.title
        text = browser.find_element("tag name", "body").text[:5000]
        return {"ok": True, "title": title, "url": browser.current_url, "text": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_screenshot():
    """截取当前页面截图（返回 base64）"""
    import base64
    browser = get_browser()
    if not browser:
        return {"ok": False, "error": "浏览器不可用"}
    try:
        screenshot = browser.get_screenshot_as_base64()
        return {"ok": True, "screenshot_base64": screenshot[:500000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_close():
    """关闭浏览器"""
    global _browser_instance
    if _browser_instance:
        try:
            _browser_instance.quit()
        except Exception:
            pass
        _browser_instance = None


# ════════════════════════════════════════════════════════════
#  进程管理（白名单制）
# ════════════════════════════════════════════════════════════

PROCESS_WHITELIST = [
    "notepad.exe", "calc.exe", "mspaint.exe", "write.exe",
    "python.exe", "python3.exe", "pip.exe",
    "node.exe", "npm.cmd", "npx.cmd",
    "git.exe", "curl.exe", "wget.exe",
    "explorer.exe", "cmd.exe",
]


def start_process(program, args=None, cwd=None):
    """启动白名单程序"""
    prog_lower = program.lower()
    allowed = False
    for w in PROCESS_WHITELIST:
        if prog_lower == w or prog_lower.endswith("\\" + w):
            allowed = True
            break
    if not allowed:
        return {"ok": False, "error": f"程序不在白名单中。允许: {', '.join(PROCESS_WHITELIST)}"}

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
    for proc in psutil.process_iter(["pid", "name", "create_time", "status"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            if any(name == w.lower() for w in PROCESS_WHITELIST):
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
        if not any(name == w.lower() for w in PROCESS_WHITELIST):
            return {"ok": False, "error": f"进程 {name} 不在白名单中，拒绝终止"}
        proc.terminate()
        return {"ok": True, "pid": pid, "name": proc.name()}
    except psutil.NoSuchProcess:
        return {"ok": False, "error": "进程不存在"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
#  剪贴板接口
# ════════════════════════════════════════════════════════════

def get_clipboard():
    """读取剪贴板内容"""
    try:
        import pyperclip
        content = pyperclip.paste()
        return {"ok": True, "content": content[:10000]}
    except ImportError:
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=3,
            )
            return {"ok": True, "content": result.stdout[:10000]}
        except Exception as e:
            return {"ok": False, "error": f"剪贴板读取失败: {e}"}


def set_clipboard(text):
    """写入剪贴板（需要确认）"""
    if len(text) > 50000:
        return {"ok": False, "error": "内容过长（最大 50000 字符）"}
    try:
        import pyperclip
        pyperclip.copy(text)
        return {"ok": True}
    except ImportError:
        try:
            import subprocess
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard -Value '{text[:5000]}'"],
                capture_output=True, timeout=3,
            )
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"剪贴板写入失败: {e}"}
