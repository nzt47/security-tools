"""
系统工具集 —— 薄包装，向后兼容

实际实现拆分到 agent/tools/ 子模块。
此模块从子模块导入并重新导出，同时保留独立功能。

拆分结构：
  - file_tools.py     → 文件操作（读写、搜索、路径安全）
  - workspace_tools.py → 工作区管理
  - browser_tools.py   → 无头浏览器控制
  - process_tools.py   → 进程管理（白名单制）
  - task_tools.py      → 定时任务管理
  - shell_tools.py     → Shell 执行

本模块保留：
  - run_sandbox       → Python 沙盒执行
  - get_clipboard     → 剪贴板读取
  - set_clipboard     → 剪贴板写入
  - get_weather        → 天气查询
  - expand_context_from_memory → 记忆上下文扩展
"""
import logging

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  从子模块导入并重新导出
# ════════════════════════════════════════════════════════════

from agent.tools.file_tools import (
    is_protected_path, safe_resolve_path, is_binary_content, is_executable_extension,
    read_file, write_file, list_directory, get_file_info, search_files,
    PROTECTED_SYSTEM_DIRS_WIN, ALLOWED_WIN_SUBDIRS, PROTECTED_SYSTEM_DIRS_UNIX,
    BLOCKED_WRITE_EXTENSIONS, DEFAULT_MAX_READ_SIZE, DEFAULT_MAX_WRITE_SIZE,
    _guess_mime_type, _get_single_file_info,
)
from agent.tools.workspace_tools import (
    WORKSPACE_DIR, init_workspace, list_workspace, write_workspace, delete_workspace,
)
from agent.tools.browser_tools import (
    set_browser_config, get_browser, _cleanup_browser_instance,
    browser_navigate, browser_screenshot, browser_close,
)
from agent.tools.process_tools import (
    PROCESS_WHITELIST,
    get_process_whitelist, add_whitelist_entry, remove_whitelist_entry,
    get_whitelist_detail, start_process, list_processes, stop_process,
)
from agent.tools.task_tools import (
    list_scheduled_tasks, create_scheduled_task, delete_scheduled_task, toggle_scheduled_task,
    SCHEDULED_TASKS_FILE, _load_tasks, _save_tasks,
)
from agent.tools.shell_tools import (
    execute_shell, _detect_shell, _truncate_output,
)


# ════════════════════════════════════════════════════════════
#  Python 沙盒
# ════════════════════════════════════════════════════════════

# 沙盒拒绝的模式（类型属性遍历逃逸检测）
_SANDBOX_BLOCKED_PATTERNS = [
    ".__class__", ".__bases__", ".__mro__", ".__subclasses__",
    ".__globals__", ".__code__", ".__dict__", ".__builtins__",
    ".__init__", ".__getattribute__", ".__getitem__",
    "getattr(", "hasattr(", "eval(", "exec(", "compile(",
    "__import__(", "import ", "open(", "__builtins",
    "globals()", "locals()", "vars(", "type(",
]

# 沙盒允许的安全内置函数（去除了异常类和可反射的类型）
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "chr": chr,
    "dict": dict, "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "len": len, "list": list,
    "map": map, "max": max, "min": min, "ord": ord, "range": range,
    "reversed": reversed, "round": round, "set": set, "slice": slice,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "True": True, "False": False, "None": None,
}


def _sandbox_worker(code, safe_builtins, result_queue):
    """沙盒子进程入口函数

    在独立进程中执行用户代码，捕获 stdout/stderr 和异常，
    通过 Queue 回传结果。若进程被强制终止，Queue 不会有数据，
    主进程据此判断超时。
    """
    import sys
    import io

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    error = None
    try:
        safe_globals = {"__builtins__": safe_builtins}
        exec(code, safe_globals)
    except Exception as e:
        # 不暴露异常类型（防止类遍历攻击）
        error = f"{type(e).__name__}: {e}"

    stdout_val = sys.stdout.getvalue()[:10000]
    stderr_val = sys.stderr.getvalue()[:5000]

    sys.stdout = old_stdout
    sys.stderr = old_stderr

    result_queue.put({
        "stdout": stdout_val,
        "stderr": stderr_val,
        "error": error,
    })


def run_sandbox(code, timeout_sec=5):
    """在受限的 Python 沙盒中执行代码

    安全措施：
    - 仅暴露纯函数内置（无异常类、无反射函数）
    - 在独立进程中执行，带超时强杀（替代 threading 方案）
    - 预检查已知逃逸模式
    - 捕获 stdout/stderr 输出

    与旧版（threading）的区别：
    - 超时后进程被 terminate 强制终止，不会泄漏 CPU
    - 子进程的 stdout/stderr 修改不影响主进程
    - 代价：进程启动比线程慢约 50-100ms
    """
    import multiprocessing

    result = {"stdout": "", "stderr": "", "error": None, "timed_out": False}

    # 预检查：阻止已知的沙箱逃逸模式
    for pattern in _SANDBOX_BLOCKED_PATTERNS:
        if pattern in code:
            result["error"] = f"代码包含被禁止的模式: {pattern}"
            return result

    # 显式指定 spawn 方式，避免 fork 继承父进程锁状态
    ctx = multiprocessing.get_context("spawn")

    # Queue 必须与 Process 使用同一上下文，否则跨 fork/spawn 共享 SemLock 会报错
    result_queue = ctx.Queue()

    process = ctx.Process(
        target=_sandbox_worker,
        args=(code, _SAFE_BUILTINS, result_queue),
        daemon=True,
    )
    process.start()
    process.join(timeout=timeout_sec)

    if process.is_alive():
        # 超时：强制终止子进程
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)

        result["timed_out"] = True
        result["error"] = f"执行超时 ({timeout_sec}秒)"
        result_queue.close()
        result_queue.join_thread()
        return result

    # 正常结束：从 Queue 读取结果
    try:
        child_result = result_queue.get(timeout=1)
        result["stdout"] = child_result["stdout"]
        result["stderr"] = child_result["stderr"]
        result["error"] = child_result["error"]
    except Exception:
        result["error"] = "子进程异常终止，未返回结果"

    # 检查子进程退出码（负值表示被信号杀死）
    if process.exitcode is not None and process.exitcode < 0:
        if not result["error"]:
            result["error"] = f"子进程被信号 {-process.exitcode} 终止"

    result_queue.close()
    result_queue.join_thread()

    return result


# ════════════════════════════════════════════════════════════
#  剪贴板接口
# ════════════════════════════════════════════════════════════

def get_clipboard():
    """读取剪贴板内容"""
    try:
        import pyperclip
        content = pyperclip.paste()
        return {"ok": True, "content": content[:10000]}
    except Exception:
        # pyperclip 在无 xclip/xsel 的 Linux CI 会抛 PyperclipException（非 ImportError）
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
    except Exception:
        # 同 get_clipboard：覆盖 PyperclipException
        try:
            import subprocess
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard -Value '{text[:5000]}'"],
                capture_output=True, timeout=3,
            )
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"剪贴板写入失败: {e}"}


# ════════════════════════════════════════════════════════════
#  天气查询 — 使用 wttr.in 服务，无需 API Key
# ════════════════════════════════════════════════════════════

def get_weather(city: str = "", format: str = "text") -> dict:
    """查询天气信息

    使用 wttr.in 服务，无需 API Key。

    Args:
        city: 城市名称，如 "Beijing"、"Shanghai"、"Tokyo"，留空则自动查询当前 IP 所在地天气
        format: 返回格式
            - "text": 简洁文本格式（如 "Beijing: ☀️ +25°C"）
            - "json": 完整 JSON 数据格式
            - "full": 完整文本预报格式

    Returns:
        dict: {ok, data, format, city, error}
    """
    import json
    import urllib.request
    import urllib.error
    import urllib.parse

    if not city:
        city = ""

    # 根据 format 选择 URL
    if format == "json":
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1" if city else "https://wttr.in?format=j1"
    elif format == "full":
        url = f"https://wttr.in/{urllib.parse.quote(city)}?lang=zh" if city else "https://wttr.in?lang=zh"
    else:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3&lang=zh" if city else "https://wttr.in?format=3&lang=zh"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "curl/7.68.0",
        })
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()

        if format == "json":
            data = json.loads(raw.decode("utf-8"))
        else:
            data = raw.decode("utf-8").strip()

        return {
            "ok": True,
            "data": data,
            "format": format,
            "city": city or "auto",
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP 错误: {e.code} {e.reason}", "city": city}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"网络连接失败: {e.reason}", "city": city}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON 解析失败: {e}", "city": city}
    except Exception as e:
        return {"ok": False, "error": f"未知错误: {e}", "city": city}


def expand_context_from_memory(digital_life, query, max_items=5):
    """从记忆库中查找更多与当前话题相关的上下文信息"""
    try:
        if hasattr(digital_life, '_vector_memory') and digital_life._vector_memory:
            results = digital_life._vector_memory.search(query, top_k=max_items)
            context_items = []
            for item in results:
                if hasattr(item, 'content'):
                    context_items.append({
                        'content': item.content,
                        'score': getattr(item, 'score', 0)
                    })
                elif isinstance(item, dict) and 'content' in item:
                    context_items.append({
                        'content': item['content'],
                        'score': item.get('score', 0)
                    })
            return {
                "ok": True,
                "query": query,
                "count": len(context_items),
                "items": context_items
            }
        else:
            return {
                "ok": False,
                "error": "向量记忆系统未启用",
                "query": query
            }
    except Exception as e:
        logger.error(f"expand_context_from_memory 错误: {e}")
        return {
            "ok": False,
            "error": str(e),
            "query": query
        }


# 向后兼容：保留 __all__
__all__ = [
    # 文件操作
    "is_protected_path", "safe_resolve_path",
    "read_file", "write_file", "list_directory", "get_file_info", "search_files",
    # 工作区
    "WORKSPACE_DIR", "init_workspace", "list_workspace", "write_workspace", "delete_workspace",
    # 浏览器
    "set_browser_config", "get_browser", "browser_navigate", "browser_screenshot", "browser_close",
    # 进程管理
    "get_process_whitelist", "add_whitelist_entry", "remove_whitelist_entry",
    "start_process", "list_processes", "stop_process",
    # 定时任务
    "list_scheduled_tasks", "create_scheduled_task", "delete_scheduled_task", "toggle_scheduled_task",
    # Shell
    "execute_shell",
    # 沙盒
    "run_sandbox",
    # 剪贴板
    "get_clipboard", "set_clipboard",
    # 天气
    "get_weather",
    # 记忆
    "expand_context_from_memory",
]
