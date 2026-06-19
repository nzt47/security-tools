"""工作区 & 系统工具 API 路由"""
import os
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.system_tools import (
    init_workspace, list_workspace, write_workspace, delete_workspace,
    read_file, write_file, list_directory, get_file_info, search_files,
    browser_navigate, browser_screenshot, browser_close,
    start_process, list_processes, stop_process,
    get_clipboard, set_clipboard,
    get_whitelist_detail, add_whitelist_entry, remove_whitelist_entry,
    PROCESS_WHITELIST, WORKSPACE_DIR,
)

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册所有工作区 & 系统工具路由"""

    # ═══════════════════════════════════════════════════
    #  工作区
    # ═══════════════════════════════════════════════════

    @app.route("/api/workspace")
    @log_request(show_response=False)
    def api_workspace_list():
        path = request.args.get("path", "")
        try:
            result = list_workspace(path)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 403
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/workspace/write", methods=["POST"])
    @require_token
    @log_request()
    def api_workspace_write():
        data = request.get_json() or {}
        path = data.get("path", "")
        content = data.get("content", "")
        if not path:
            return jsonify({"ok": False, "error": "缺少 path"}), 400
        safety = state.safety_guard.check(content)
        if safety["level"] == "critical":
            return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
        try:
            result = write_workspace(path, content)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 403

    @app.route("/api/workspace/delete", methods=["POST"])
    @require_token
    @log_request()
    def api_workspace_delete():
        data = request.get_json() or {}
        path = data.get("path", "")
        if not path:
            return jsonify({"ok": False, "error": "缺少 path"}), 400
        try:
            result = delete_workspace(path)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 403

    @app.route("/api/workspace/info")
    @log_request(show_response=False)
    def api_workspace_info():
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

    # ═══════════════════════════════════════════════════
    #  文件系统
    # ═══════════════════════════════════════════════════

    @app.route("/api/filesystem/read", methods=["POST"])
    @require_token
    @log_request()
    def api_filesystem_read():
        data = request.get_json() or {}
        path = data.get("path", "")
        encoding = data.get("encoding", "utf-8")
        max_size_mb = min(data.get("max_size_mb", 5), 50)
        if not path:
            return jsonify({"ok": False, "error": "缺少 path"}), 400
        result = read_file(path, encoding=encoding, max_size_mb=max_size_mb)
        if result.get("binary"):
            content_len = len(result.get("content", ""))
            if content_len > 100000:
                result["truncated"] = True
                result["content"] = result["content"][:100000]
                result["note"] = "二进制内容已截断，完整内容过大"
        return jsonify(result)

    @app.route("/api/filesystem/write", methods=["POST"])
    @require_token
    @log_request()
    def api_filesystem_write():
        data = request.get_json() or {}
        path = data.get("path", "")
        content = data.get("content", "")
        encoding = data.get("encoding", "utf-8")
        if not path:
            return jsonify({"ok": False, "error": "缺少 path"}), 400
        safety = state.safety_guard.check(content)
        if safety["level"] == "critical":
            return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
        result = write_file(path, content, encoding=encoding)
        return jsonify(result)

    @app.route("/api/filesystem/list", methods=["GET"])
    @log_request(show_response=False)
    def api_filesystem_list():
        path = request.args.get("path", ".")
        show_hidden = request.args.get("show_hidden", "false").lower() == "true"
        result = list_directory(path, show_hidden=show_hidden)
        return jsonify(result)

    @app.route("/api/filesystem/info", methods=["GET"])
    @log_request(show_response=False)
    def api_filesystem_info():
        path = request.args.get("path", "")
        if not path:
            return jsonify({"ok": False, "error": "缺少 path"}), 400
        return jsonify(get_file_info(path))

    @app.route("/api/filesystem/search", methods=["GET"])
    @log_request(show_response=False)
    def api_filesystem_search():
        pattern = request.args.get("pattern", "")
        root_path = request.args.get("root_path", ".")
        if not pattern:
            return jsonify({"ok": False, "error": "缺少 pattern"}), 400
        return jsonify(search_files(pattern, root_path=root_path))

    # ═══════════════════════════════════════════════════
    #  沙盒
    # ═══════════════════════════════════════════════════

    @app.route("/api/sandbox/run", methods=["POST"])
    @require_token
    @log_request()
    def api_sandbox_run():
        sandbox_enabled = os.getenv("YUNSHU_FEATURE_SANDBOX", "false").lower() == "true"
        if not sandbox_enabled:
            logger.warning("[沙盒] 访问被拒绝 - 沙盒功能已关闭 (YUNSHU_FEATURE_SANDBOX=%s)",
                           os.getenv("YUNSHU_FEATURE_SANDBOX", "未设置"))
            return jsonify({"blocked": True, "error": "沙盒功能已关闭，设置环境变量 YUNSHU_FEATURE_SANDBOX=true 可启用",
                            "sandbox_disabled": True}), 503
        try:
            from agent.system_tools import run_sandbox
        except ImportError as e:
            return jsonify({"error": f"沙盒模块加载失败: {e}", "sandbox_init_error": True}), 500

        data = request.get_json() or {}
        code = data.get("code", "")
        timeout = min(data.get("timeout", 5), 30)
        try:
            safety = state.safety_guard.check(code)
        except Exception as e:
            safety = {"level": "warning", "matches": [], "safe": True, "check_error": str(e)}

        if safety["level"] == "critical":
            return jsonify({"blocked": True, "safety": safety}), 403

        try:
            result = run_sandbox(code, timeout)
        except Exception as e:
            return jsonify({"error": f"沙盒执行引擎异常: {e}", "engine_error": True}), 500

        result["safety"] = safety
        return jsonify(result)

    # ═══════════════════════════════════════════════════
    #  浏览器
    # ═══════════════════════════════════════════════════

    @app.route("/api/browser/navigate", methods=["POST"])
    @require_token
    @log_request()
    def api_browser_navigate():
        data = request.get_json() or {}
        url = data.get("url", "")
        if not url:
            return jsonify({"ok": False, "error": "缺少 url"}), 400
        return jsonify(browser_navigate(url))

    @app.route("/api/browser/screenshot")
    @require_token
    @log_request()
    def api_browser_screenshot():
        result = browser_screenshot()
        return jsonify(result)

    @app.route("/api/browser/close", methods=["POST"])
    @require_token
    @log_request()
    def api_browser_close():
        browser_close()
        return jsonify({"ok": True})

    # ═══════════════════════════════════════════════════
    #  进程管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/process/list")
    @log_request(show_response=False)
    def api_process_list():
        return jsonify({"processes": list_processes()})

    @app.route("/api/process/whitelist")
    @log_request(show_response=False)
    def api_process_whitelist():
        return jsonify(get_whitelist_detail())

    @app.route("/api/process/whitelist/add", methods=["POST"])
    @require_token
    @log_request()
    def api_process_whitelist_add():
        data = request.get_json() or {}
        program = data.get("program", "")
        return jsonify(add_whitelist_entry(program))

    @app.route("/api/process/whitelist/remove", methods=["POST"])
    @require_token
    @log_request()
    def api_process_whitelist_remove():
        data = request.get_json() or {}
        program = data.get("program", "")
        return jsonify(remove_whitelist_entry(program))

    @app.route("/api/process/start", methods=["POST"])
    @require_token
    @log_request()
    def api_process_start():
        data = request.get_json() or {}
        program = data.get("program", "")
        args = data.get("args")
        if not program:
            return jsonify({"ok": False, "error": "缺少 program"}), 400
        return jsonify(start_process(program, args))

    @app.route("/api/process/stop", methods=["POST"])
    @require_token
    @log_request()
    def api_process_stop():
        data = request.get_json() or {}
        pid = data.get("pid")
        if not pid:
            return jsonify({"ok": False, "error": "缺少 pid"}), 400
        return jsonify(stop_process(pid))

    # ═══════════════════════════════════════════════════
    #  剪贴板
    # ═══════════════════════════════════════════════════

    @app.route("/api/clipboard")
    @require_token
    @log_request(show_response=False)
    def api_clipboard_get():
        return jsonify(get_clipboard())

    @app.route("/api/clipboard", methods=["POST"])
    @require_token
    @log_request()
    def api_clipboard_set():
        data = request.get_json() or {}
        text = data.get("text", "")
        return jsonify(set_clipboard(text))
