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
    routes=[
        "/api/workspace",
        "/api/workspace/write",
        "/api/workspace/delete",
        "/api/workspace/info",
        "/api/filesystem/read",
        "/api/filesystem/write",
        "/api/filesystem/list",
        "/api/filesystem/info",
        "/api/filesystem/search",
        "/api/sandbox/run",
        "/api/browser/navigate",
        "/api/browser/screenshot",
        "/api/browser/close",
        "/api/process/list",
        "/api/process/whitelist",
        "/api/process/whitelist/add",
        "/api/process/whitelist/remove",
        "/api/process/start",
        "/api/process/stop",
        "/api/clipboard",
        "/api/web/get",
        "/api/web/post",
        "/api/web/xpath",
        "/api/web/css",
        "/api/web/search",
        "/api/web/clean",
        "/api/web/download",
        "/api/web/stats",
        "/api/web/search/status",
    ],
))
