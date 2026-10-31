# plugins/system_tools.py
"""系统工具插件（任务 T1.9）。

迁移自 app_server.py 的系统工具域路由（路径与行为 100% 不变）：
  - 工作区：/api/workspace*
  - 文件系统：/api/filesystem/*
  - Python 沙盒：/api/sandbox/run
  - 无头浏览器：/api/browser/*
  - 进程管理：/api/process/*
  - 剪贴板：/api/clipboard
  - 互联网工具：/api/web/*

共享依赖约定（PLAN-1 §4）：
  - 插件模块顶层只 import flask / plugin_api，绝不顶层 import app_server（循环导入红线）。
  - require_token / log_request / _safety_guard / _web_http / _web_scraper / _web_search /
    _web_processor / _web_crawler / logger 等共享装饰器与全局保留在 app_server.py，视图函数内延迟 import。
  - 浏览器/进程/Web 工具单例在视图函数内延迟 import（浏览器单例 _browser_instance 不迁移到
    SingletonManager，见 docs/SingletonManager_Migration_Guide.md「例外与说明」）。
"""
import functools
import logging
from flask import Blueprint, request, jsonify
from .plugin_api import Plugin, register_plugin

bp = Blueprint("system_tools", __name__)


def _view(*, auth=False, log=None):
    """延迟应用 app_server 的共享装饰器（require_token / log_request）。

    app_server 在本插件模块被导入时尚在初始化（require_token / log_request 尚未定义），
    因此路由函数先以裸函数注册到 blueprint；首次请求时惰性从 app_server 取回装饰器
    并一次性包装，之后直接命中包装版，行为与迁移前完全一致。

    Args:
        auth: True 时套用 require_token（最外层，与原 @require_token 位置一致）。
        log: 不为 None 时套用 log_request(show_response=log)，与原装饰器参数一致。
    """
    def decorator(f):
        state = {"wrapped": None}

        @functools.wraps(f)
        def proxy(*args, **kwargs):
            if state["wrapped"] is None:
                from app_server import require_token, log_request
                wrapped = f
                if log is not None:
                    wrapped = log_request(show_response=log)(wrapped)
                if auth:
                    wrapped = require_token(wrapped)
                state["wrapped"] = wrapped
            return state["wrapped"](*args, **kwargs)

        return proxy
    return decorator


# ════════════════════════════════════════════════════════════
#  工作区接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/workspace")
@_view(log=False)
def api_workspace_list():
    """列出工作区内容"""
    from agent.system_tools import list_workspace
    path = request.args.get("path", "")
    try:
        result = list_workspace(path)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/workspace/write", methods=["POST"])
@_view(auth=True, log=True)
def api_workspace_write():
    """写入工作区文件"""
    from app_server import _safety_guard
    from agent.system_tools import write_workspace
    data = request.get_json() or {}
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    # 安全检查
    safety = _safety_guard.check(content)
    if safety["level"] == "critical":
        return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
    try:
        result = write_workspace(path, content)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 403


@bp.route("/api/workspace/delete", methods=["POST"])
@_view(auth=True, log=True)
def api_workspace_delete():
    """删除工作区文件"""
    from agent.system_tools import delete_workspace
    data = request.get_json() or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    try:
        result = delete_workspace(path)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 403


@bp.route("/api/workspace/info")
@_view(log=False)
def api_workspace_info():
    """工作区信息"""
    import os
    from agent.system_tools import WORKSPACE_DIR
    total_size = 0
    file_count = 0
    for root, dirs, files in os.walk(WORKSPACE_DIR):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_size += os.path.getsize(fp)
                file_count += 1
            except OSError:
                pass
    return jsonify({
        "path": WORKSPACE_DIR,
        "file_count": file_count,
        "total_size_bytes": total_size,
    })


# ════════════════════════════════════════════════════════════
#  通用文件系统 API — 云枢读写本地文件的能力
# ════════════════════════════════════════════════════════════

@bp.route("/api/filesystem/read", methods=["POST"])
@_view(auth=True, log=True)
def api_filesystem_read():
    """读取本地文件内容"""
    from agent.system_tools import read_file
    data = request.get_json() or {}
    path = data.get("path", "")
    encoding = data.get("encoding", "utf-8")
    max_size_mb = min(data.get("max_size_mb", 5), 50)  # 最大 50MB
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400

    # 安全检查
    result = read_file(path, encoding=encoding, max_size_mb=max_size_mb)
    if result.get("binary"):
        # 对二进制内容返回截断警告
        content_len = len(result.get("content", ""))
        if content_len > 100000:
            result["truncated"] = True
            result["content"] = result["content"][:100000]
            result["note"] = "二进制内容已截断，完整内容过大"
    return jsonify(result)


@bp.route("/api/filesystem/write", methods=["POST"])
@_view(auth=True, log=True)
def api_filesystem_write():
    """写入本地文件"""
    from app_server import _safety_guard
    from agent.system_tools import write_file
    data = request.get_json() or {}
    path = data.get("path", "")
    content = data.get("content", "")
    encoding = data.get("encoding", "utf-8")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400

    # 安全检查
    safety = _safety_guard.check(content)
    if safety["level"] == "critical":
        return jsonify({"ok": False, "blocked": True, "safety": safety}), 403

    result = write_file(path, content, encoding=encoding)
    return jsonify(result)


@bp.route("/api/filesystem/list", methods=["GET"])
@_view(log=False)
def api_filesystem_list():
    """列出目录内容"""
    from agent.system_tools import list_directory
    path = request.args.get("path", ".")
    show_hidden = request.args.get("show_hidden", "false").lower() == "true"
    result = list_directory(path, show_hidden=show_hidden)
    return jsonify(result)


@bp.route("/api/filesystem/info", methods=["GET"])
@_view(log=False)
def api_filesystem_info():
    """获取文件/目录信息"""
    from agent.system_tools import get_file_info
    path = request.args.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    return jsonify(get_file_info(path))


@bp.route("/api/filesystem/search", methods=["GET"])
@_view(log=False)
def api_filesystem_search():
    """搜索文件"""
    from agent.system_tools import search_files
    pattern = request.args.get("pattern", "")
    root_path = request.args.get("root_path", ".")
    if not pattern:
        return jsonify({"ok": False, "error": "缺少 pattern"}), 400
    return jsonify(search_files(pattern, root_path=root_path))


# ════════════════════════════════════════════════════════════
#  Python 沙盒接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/sandbox/run", methods=["POST"])
@_view(auth=True, log=True)
def api_sandbox_run():
    """在受限沙盒中执行 Python 代码（受 features.sandbox 开关控制）"""
    import os
    from app_server import logger, _safety_guard
    # 读取沙盒功能开关（默认关闭）
    sandbox_enabled = os.getenv("YUNSHU_FEATURE_SANDBOX", "false").lower() == "true"

    if not sandbox_enabled:
        logger.warning("[沙盒] 访问被拒绝 - 沙盒功能已关闭 (YUNSHU_FEATURE_SANDBOX=%s)",
                       os.getenv("YUNSHU_FEATURE_SANDBOX", "未设置"))
        return jsonify({"blocked": True, "error": "沙盒功能已关闭，设置环境变量 YUNSHU_FEATURE_SANDBOX=true 可启用", "sandbox_disabled": True}), 503

    logger.info("[沙盒] 沙盒功能已启用，开始执行代码")

    try:
        from agent.system_tools import run_sandbox
    except ImportError as e:
        logger.error("[沙盒] 导入 run_sandbox 失败: %s", e, exc_info=True)
        return jsonify({"error": f"沙盒模块加载失败: {e}", "sandbox_init_error": True}), 500

    data = request.get_json() or {}
    code = data.get("code", "")
    timeout = min(data.get("timeout", 5), 30)  # 最大 30 秒

    # 安全检查
    try:
        safety = _safety_guard.check(code)
    except Exception as e:
        logger.error("[沙盒] 安全检查异常: %s", e, exc_info=True)
        safety = {"level": "warning", "matches": [], "safe": True, "check_error": str(e)}

    if safety["level"] == "critical":
        logger.warning("[沙盒] 代码被安全检查拦截: %s", safety)
        return jsonify({"blocked": True, "safety": safety}), 403

    try:
        result = run_sandbox(code, timeout)
    except Exception as e:
        logger.error("[沙盒] 代码执行引擎异常: %s", e, exc_info=True)
        return jsonify({"error": f"沙盒执行引擎异常: {e}", "engine_error": True}), 500

    result["safety"] = safety

    if result.get("error"):
        logger.warning("[沙盒] 代码执行出错: %s", result["error"][:200])
    elif result.get("timed_out"):
        logger.warning("[沙盒] 代码执行超时 (%ds)", timeout)
    else:
        logger.info("[沙盒] 代码执行成功，耗时 %.1fms", result.get("duration_ms", 0))

    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  无头浏览器接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/browser/navigate", methods=["POST"])
@_view(auth=True, log=True)
def api_browser_navigate():
    """浏览器导航到 URL"""
    from agent.system_tools import browser_navigate
    data = request.get_json() or {}
    url = data.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "缺少 url"}), 400
    return jsonify(browser_navigate(url))


@bp.route("/api/browser/screenshot")
@_view(auth=True, log=True)
def api_browser_screenshot():
    """浏览器截图"""
    from agent.system_tools import browser_screenshot
    result = browser_screenshot()
    return jsonify(result)


@bp.route("/api/browser/close", methods=["POST"])
@_view(auth=True, log=True)
def api_browser_close():
    """关闭浏览器"""
    from agent.system_tools import browser_close
    browser_close()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════
#  进程管理接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/process/list")
@_view(log=False)
def api_process_list():
    """列出白名单进程"""
    from agent.system_tools import list_processes
    return jsonify({"processes": list_processes()})


@bp.route("/api/process/whitelist")
@_view(log=False)
def api_process_whitelist():
    """获取进程白名单详情"""
    from agent.system_tools import get_whitelist_detail
    return jsonify(get_whitelist_detail())


@bp.route("/api/process/whitelist/add", methods=["POST"])
@_view(auth=True, log=True)
def api_process_whitelist_add():
    """添加自定义白名单条目"""
    from agent.system_tools import add_whitelist_entry
    data = request.get_json() or {}
    program = data.get("program", "")
    return jsonify(add_whitelist_entry(program))


@bp.route("/api/process/whitelist/remove", methods=["POST"])
@_view(auth=True, log=True)
def api_process_whitelist_remove():
    """移除自定义白名单条目"""
    from agent.system_tools import remove_whitelist_entry
    data = request.get_json() or {}
    program = data.get("program", "")
    return jsonify(remove_whitelist_entry(program))


@bp.route("/api/process/start", methods=["POST"])
@_view(auth=True, log=True)
def api_process_start():
    """启动白名单程序"""
    from agent.system_tools import start_process
    data = request.get_json() or {}
    program = data.get("program", "")
    args = data.get("args")
    if not program:
        return jsonify({"ok": False, "error": "缺少 program"}), 400
    return jsonify(start_process(program, args))


@bp.route("/api/process/stop", methods=["POST"])
@_view(auth=True, log=True)
def api_process_stop():
    """终止进程（仅限白名单）"""
    from agent.system_tools import stop_process
    data = request.get_json() or {}
    pid = data.get("pid")
    if not pid:
        return jsonify({"ok": False, "error": "缺少 pid"}), 400
    return jsonify(stop_process(pid))


# ════════════════════════════════════════════════════════════
#  剪贴板接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/clipboard")
@_view(auth=True, log=False)
def api_clipboard_get():
    """读取剪贴板"""
    from agent.system_tools import get_clipboard
    return jsonify(get_clipboard())


@bp.route("/api/clipboard", methods=["POST"])
@_view(auth=True, log=True)
def api_clipboard_set():
    """写入剪贴板"""
    from agent.system_tools import set_clipboard
    data = request.get_json() or {}
    text = data.get("text", "")
    return jsonify(set_clipboard(text))


# ════════════════════════════════════════════════════════════
#  互联网 API — 云枢获取网络信息的能力
# ════════════════════════════════════════════════════════════

@bp.route("/api/web/get", methods=["POST"])
@_view(auth=True, log=True)
def api_web_get():
    """HTTP GET 请求"""
    from app_server import _web_http, _web_scraper
    data = request.get_json() or {}
    url = data.get("url", "")
    timeout = data.get("timeout", 30)
    if not url:
        return jsonify({"ok": False, "error": "缺少 url"}), 400

    result = _web_http.get(url, timeout=timeout)
    if result.get("ok") and result.get("text"):
        parsed = _web_scraper.parse(result["text"], url=result.get("url", url))
        result["parsed"] = {k: parsed.get(k) for k in ("title", "text", "links", "images", "meta", "headings") if k != "html"}
    return jsonify(result)


@bp.route("/api/web/post", methods=["POST"])
@_view(auth=True, log=True)
def api_web_post():
    """HTTP POST 请求"""
    from app_server import _web_http
    data = request.get_json() or {}
    url = data.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "缺少 url"}), 400

    form_data = data.get("data", {})
    json_data = data.get("json_data", {})
    if json_data:
        result = _web_http.post(url, json_data=json_data)
    else:
        result = _web_http.post(url, data=form_data)
    return jsonify(result)


@bp.route("/api/web/xpath", methods=["POST"])
@_view(auth=True, log=True)
def api_web_xpath():
    """XPath 提取"""
    from app_server import _web_scraper, _web_http
    data = request.get_json() or {}
    url = data.get("url", "")
    expression = data.get("expression", "")
    html = data.get("html", "")

    if not expression:
        return jsonify({"ok": False, "error": "缺少 expression"}), 400

    if html:
        results = _web_scraper.xpath(expression, html=html)
        return jsonify({"ok": True, "results": results, "count": len(results)})

    if not url:
        return jsonify({"ok": False, "error": "缺少 url 或 html"}), 400

    fetch = _web_http.get(url)
    if not fetch.get("ok"):
        return jsonify(fetch)
    results = _web_scraper.xpath(expression, html=fetch.get("text", ""))
    return jsonify({"ok": True, "results": results, "count": len(results)})


@bp.route("/api/web/css", methods=["POST"])
@_view(auth=True, log=True)
def api_web_css():
    """CSS 选择器提取"""
    from app_server import _web_scraper, _web_http
    data = request.get_json() or {}
    url = data.get("url", "")
    selector = data.get("selector", "")
    attr = data.get("attr", "")
    html = data.get("html", "")

    if not selector:
        return jsonify({"ok": False, "error": "缺少 selector"}), 400

    if html:
        results = _web_scraper.css(selector, html=html, attr=attr or None)
        return jsonify({"ok": True, "results": results, "count": len(results)})

    if not url:
        return jsonify({"ok": False, "error": "缺少 url 或 html"}), 400

    fetch = _web_http.get(url)
    if not fetch.get("ok"):
        return jsonify(fetch)
    results = _web_scraper.css(selector, html=fetch.get("text", ""), attr=attr or None)
    return jsonify({"ok": True, "results": results, "count": len(results)})


@bp.route("/api/web/search", methods=["GET"])
@_view(log=False)
def api_web_search():
    """搜索互联网"""
    from app_server import _web_search, _web_processor
    from agent.web import DataProcessor
    query = request.args.get("query", "")
    num = min(int(request.args.get("num_results", 10)), 50)
    engine = request.args.get("engine", "")

    if not query:
        return jsonify({"ok": False, "error": "缺少 query"}), 400

    result = _web_search.search(query, engine=engine, num_results=num)
    if result.get("ok") and result.get("results"):
        processed = _web_processor.process(result["results"])
        result["results"] = processed
        result["summary"] = DataProcessor.summarize_results(processed)
    return jsonify(result)


@bp.route("/api/web/clean", methods=["POST"])
@_view(auth=True, log=True)
def api_web_clean():
    """数据清洗"""
    from app_server import _web_processor
    from agent.web import DataProcessor
    data = request.get_json() or {}
    text = data.get("text", "")
    items = data.get("items", [])

    if text:
        return jsonify({"ok": True, "cleaned": DataProcessor.clean_text(text)})
    if items:
        processed = _web_processor.process(items)
        return jsonify({
            "ok": True,
            "original_count": len(items),
            "processed_count": len(processed),
            "results": processed,
        })
    return jsonify({"ok": False, "error": "请提供 text 或 items"}), 400


@bp.route("/api/web/download", methods=["POST"])
@_view(auth=True, log=True)
def api_web_download():
    """下载文件"""
    from app_server import _web_http
    data = request.get_json() or {}
    url = data.get("url", "")
    filepath = data.get("filepath", "")
    if not url or not filepath:
        return jsonify({"ok": False, "error": "缺少 url 或 filepath"}), 400
    return jsonify(_web_http.download(url, filepath))


@bp.route("/api/web/stats")
@_view(log=False)
def api_web_stats():
    """Web 模块统计"""
    from app_server import _web_http, _web_search, _web_processor, _web_crawler
    return jsonify({
        "http": _web_http.get_stats(),
        "search": _web_search.get_stats(),
        "processor": _web_processor.get_stats(),
        "crawler_control": _web_crawler.get_stats(),
    })


@bp.route("/api/web/search/status")
@_view(log=False)
def api_web_search_status():
    """获取当前搜索引擎状态和切换日志（用于前端显示）"""
    from app_server import logger, _web_search
    try:
        status = _web_search.get_current_status()
        return jsonify({
            "ok": True,
            "status": status,
        })
    except Exception as e:
        logger.error("[搜索引擎] 获取状态失败: %s", e, exc_info=True)
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


PLUGIN = register_plugin(Plugin(
    name="system_tools",
    version="1.0.0",
    description="工作区、文件系统、沙箱与系统工具",
    blueprint=bp,
))


# ════════════════════════════════════════════════════════════
#  命令执行（**白名单**，不是自由 shell）—— 2026-10-31
# ════════════════════════════════════════════════════════════
#
# 【为什么是白名单而不是真 shell】本仓的必需检查里有 CodeQL，`py/command-line-injection`
# 对「用户输入 → subprocess」直接判 high；仓内 0 处 `codeql[...]` 抑制先例（§8 陷阱 14
# 还记过"白名单不算 sanitizer"）。所以这里走**另一条路**：用户只能**选**命令，
# 参数只允许结构化数值/枚举，argv 由**服务端常量**拼出（`shell=False`、不拼字符串），
# 用户输入永远不进入"命令名"。
#
# 安全边界：
#   - argv 来自 _TERMINAL_SPECS 常量表；未知 command_id 一律 400；
#   - cwd 固定为仓库根，调用方不能指定；
#   - 单次 30s 超时、输出各截断 20KB；
#   - 每次执行写审计 `terminal.exec`（command_id / exit_code / duration_ms，不记完整输出）。

_TERMINAL_TIMEOUT_SEC = 30
_TERMINAL_MAX_OUTPUT = 20000


def _clamp_int(raw, lo: int, hi: int, default: int) -> int:
    """把外部传入的参数收敛成 [lo, hi] 内的整数；非法一律回默认。"""
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, val))


def _terminal_specs():
    """命令白名单（id → (label, 说明, 参数说明, argv 构造函数)）。

    构造函数只接收**已收敛**的参数，返回**常量字符串列表**；不要在这里拼用户文本。
    """
    import sys as _sys

    return {
        "git-status": (
            "git status",
            "工作区状态（短格式 + 分支）",
            [],
            lambda p: ["git", "status", "--short", "--branch"],
        ),
        "git-log": (
            "git log",
            "最近提交（1–50 条）",
            [{"name": "n", "type": "int", "min": 1, "max": 50, "default": 10}],
            lambda p: ["git", "log", "--oneline", "-n",
                       str(_clamp_int(p.get("n"), 1, 50, 10))],
        ),
        "git-diff-stat": (
            "git diff --stat",
            "改动统计",
            [],
            lambda p: ["git", "diff", "--stat"],
        ),
        "python-version": (
            "python --version",
            "当前解释器版本",
            [],
            lambda p: [_sys.executable, "--version"],
        ),
        "pytest-purge": (
            "pytest 清理脚本用例",
            "跑 tests/unit/test_purge_future_rows.py（快照用例，约 2s）",
            [],
            lambda p: [_sys.executable, "-m", "pytest",
                       "tests/unit/test_purge_future_rows.py", "-q"],
        ),
    }


def _terminal_repo_root():
    from pathlib import Path as _Path
    return _Path(__file__).resolve().parents[1]


@bp.route("/api/terminal/commands")
@_view(auth=True, log=False)
def api_terminal_commands():
    """列出可执行命令白名单（界面用它渲染按钮，**不接受自由输入**）。"""
    specs = _terminal_specs()
    items = [
        {"id": cid, "label": label, "description": desc, "params": params}
        for cid, (label, desc, params, _builder) in specs.items()
    ]
    return jsonify({"ok": True, "commands": items, "shell": False,
                    "note": "白名单执行：只能选命令，不能自由拼 shell"})


def _terminal_run(command_id: str, params) -> dict:
    """按白名单执行一条命令（纯函数，便于单测；`shell=False`、cwd 锁仓库根）。

    Returns:
        dict：`ok` 为 False 时带 `error`/`allowed`；成功时带 exit_code / stdout / stderr。
    """
    import subprocess
    import time

    params = params if isinstance(params, dict) else {}
    specs = _terminal_specs()
    spec = specs.get(command_id)
    if spec is None:
        return {"ok": False, "status": 400,
                "error": "未知命令（只允许白名单内的 command_id）",
                "allowed": sorted(specs.keys())}

    label, _desc, _params, builder = spec
    argv = builder(params)
    started = time.time()
    try:
        proc = subprocess.run(  # noqa: S603 常量 argv + shell=False，不接受自由输入
            argv, cwd=str(_terminal_repo_root()), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=_TERMINAL_TIMEOUT_SEC, shell=False,
        )
        exit_code = int(proc.returncode)
        stdout = (proc.stdout or "")[:_TERMINAL_MAX_OUTPUT]
        stderr = (proc.stderr or "")[:_TERMINAL_MAX_OUTPUT]
        timed_out = False
    except subprocess.TimeoutExpired as e:
        exit_code = -1
        stdout = ((e.stdout or "") if isinstance(e.stdout, str) else "")[:_TERMINAL_MAX_OUTPUT]
        stderr = "命令超时（%ds）" % _TERMINAL_TIMEOUT_SEC
        timed_out = True
    except Exception as e:  # noqa: BLE001 执行失败不得 500 吞信息
        _log = logging.getLogger(__name__)
        _log.warning("[终端] 执行失败 command_id=%s: %s", command_id, e)
        return {"ok": False, "status": 500,
                "error": "命令执行失败：%s" % type(e).__name__}

    duration_ms = round((time.time() - started) * 1000, 1)
    try:
        from agent.audit.facade import audit as _audit_facade
        _audit_facade.record("terminal.exec", actor="ops", subject=command_id,
                             payload={"command_id": command_id,
                                      "exit_code": exit_code,
                                      "duration_ms": duration_ms,
                                      "timed_out": timed_out},
                             source="agent", status="ok")
    except Exception as e:  # noqa: BLE001 审计失败不影响命令结果
        _log = logging.getLogger(__name__)
        _log.warning("[终端] 审计写入失败: %s", e)

    return {"ok": True, "command_id": command_id, "label": label,
            "argv": argv, "exit_code": exit_code, "timed_out": timed_out,
            "stdout": stdout, "stderr": stderr, "duration_ms": duration_ms}


@bp.route("/api/terminal/exec", methods=["POST"])
@_view(auth=True, log=True)
def api_terminal_exec():
    """按白名单执行一条命令（`shell=False`，cwd 锁仓库根）。"""
    data = request.get_json(silent=True) or {}
    command_id = str(data.get("command_id", "") or "")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        return jsonify({"ok": False, "error": "params 必须是对象"}), 400
    result = _terminal_run(command_id, params)
    status = int(result.get("status", 200))
    result.pop("status", None)
    return jsonify(result), status


@bp.route("/api/terminal/activity")
@_view(auth=True, log=False)
def api_terminal_activity():
    """「云枢当前正在执行什么」—— 审计链尾部 + 进行中的子代理委派（只读）。

    参数：
        limit      返回条数（1–100，默认 40）
        since_seq  只返回 seq 更大的记录（前端增量轮询用；0 = 取最新一页）

    为什么读审计链而不是另建"活动流"：审计链本来就记录每次工具调用 / 技能加载 /
    调度执行 / UI 写操作（seq 单调递增），是"云枢正在做什么"的**唯一权威台账**；
    另建一份等于第二份口径。
    """
    import sqlite3

    try:
        limit = _clamp_int(request.args.get("limit", "40"), 1, 100, 40)
        since_seq = _clamp_int(request.args.get("since_seq", "0"), 0, 2**31 - 1, 0)
    except Exception:  # noqa: BLE001 参数异常一律回默认
        limit, since_seq = 40, 0

    db_path = _terminal_repo_root() / "data" / "audit" / "audit_chain.db"
    rows = []
    if db_path.exists():
        try:
            con = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=3)
            try:
                cur = con.cursor()
                if since_seq > 0:
                    cur.execute(
                        "SELECT seq, ts, action, subject FROM audit_chain "
                        "WHERE seq > ? ORDER BY seq ASC LIMIT ?", (since_seq, limit))
                    rows = [{"seq": r[0], "ts": r[1], "action": r[2], "subject": r[3]}
                            for r in cur.fetchall()]
                else:
                    cur.execute(
                        "SELECT seq, ts, action, subject FROM audit_chain "
                        "ORDER BY seq DESC LIMIT ?", (limit,))
                    rows = [{"seq": r[0], "ts": r[1], "action": r[2], "subject": r[3]}
                            for r in reversed(cur.fetchall())]
            finally:
                con.close()
        except Exception:  # noqa: BLE001 读不到就回空，界面显示"暂无"
            rows = []

    inflight = []
    board = _terminal_repo_root() / "data" / "subagent_task_board.jsonl"
    if board.exists():
        try:
            tail = board.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
            import json as _json
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and str(rec.get("status", "")) in ("running", "in_progress"):
                    inflight.append({
                        "task_id": rec.get("task_id", ""),
                        "subagent": rec.get("subagent", ""),
                        "goal": str(rec.get("goal", ""))[:120],
                        "updated_at": rec.get("updated_at", ""),
                    })
        except Exception:  # noqa: BLE001 板子读不到不影响活动流
            inflight = []

    return jsonify({"ok": True, "rows": rows, "inflight": inflight[:10],
                    "last_seq": rows[-1]["seq"] if rows else since_seq,
                    "now": _now_iso()})


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
