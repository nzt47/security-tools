"""工具注册模块 — 已实现但从未接线的能力补登记

本模块把仓库里**已实现、已验证、却从未注册进工具表**的一批能力接进主线，
这是「能力存在 ≠ 模型能用」的堵漏（同 process_distill / knowledge 的历史教训）：

    run_sandbox           ← agent/system_tools.py:run_sandbox
    get_clipboard/set_clipboard ← agent/system_tools.py
    browser_*             ← agent/tools/browser_tools.py
    workspace_*           ← agent/tools/workspace_tools.py
    read_pdf_tables       ← agent/pdf_tools.py
    list_mcp_connections  ← agent/tools/mcp_connector.McpConnector.list_connections
    look_at_screen        ← agent/orchestrator/voice_vision.VoiceVision
    weekly_report         ← agent/weekly_report_generator.run_weekly_report

【不易】
    - 每个 handler 自行捕获异常并返回 ``{"ok": False, "error": ...}``：
      ``agent.tools.call()`` 会把逃逸异常包成 ``ToolError`` 抛给调用方，
      工具循环应拿到结构化失败而非中断。
    - 权限子系统（``dl._permission``）是**可选依赖**：用 ``getattr`` 探测，
      缺失时 fail-open 并记 warning。注册期因权限模块未就绪而让整块能力消失，
      正是本模块要修的那类故障。
【变易】权限校验点（``_action_allowed`` / ``_text_allowed``）集中在此，便于治理口径统一调整。
【简易】与 ``file_tools_reg.py`` / ``system_tools.py`` 同构：``register_all(dl)`` + 装饰器。
"""
import logging

from agent import tools as _tools

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  权限子系统适配（可选依赖）
# ════════════════════════════════════════════════════════════

def _permission_of(dl):
    """取权限子系统实例；未接线时返回 None。"""
    return getattr(dl, "_permission", None)


def _action_allowed(dl, action: str, context: str = "") -> dict | None:
    """动作级权限校验（``PermissionSystem.check_action``）

    Args:
        dl: DigitalLife 实例
        action: 动作标识（会被权限系统的黑名单/危险模式正则匹配）
        context: 人类可读的上下文说明

    Returns:
        ``None`` 表示放行；返回 dict 表示调用方应直接把它当作工具返回值（被拒）。
    """
    check = getattr(_permission_of(dl), "check_action", None)
    if not callable(check):
        logger.warning("[extra_tools] 权限子系统缺失，跳过动作校验: %s", action)
        return None
    try:
        result = check(action, context or action)
    except Exception as e:  # noqa: BLE001 校验故障不阻断（已有 confirm/白名单等其它闸门）
        logger.warning("[extra_tools] 权限校验异常，按放行处理: %s — %s", action, e)
        return None
    if isinstance(result, dict):
        allowed, reason = result.get("allowed"), result.get("reason", "")
    else:
        allowed, reason = getattr(result, "allowed", None), getattr(result, "reason", "")
    if allowed is False:
        return {"ok": False, "error": f"权限系统拒绝: {reason}", "blocked": True}
    return None


def _text_allowed(dl, text: str) -> dict | None:
    """内容级安全检查（``PermissionSystem.check_text``）

    Returns:
        ``None`` 表示放行；critical 命中时返回拒绝结果。
    """
    check = getattr(_permission_of(dl), "check_text", None)
    if not callable(check):
        return None
    try:
        result = check(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("[extra_tools] 内容安全检查异常，按放行处理: %s", e)
        return None
    if isinstance(result, dict) and result.get("level") == "critical":
        matches = [
            m.get("description", "") for m in result.get("matches", [])
            if isinstance(m, dict)
        ]
        return {
            "ok": False,
            "error": f"内容安全检查未通过: {matches}",
            "blocked": True,
            "level": "critical",
        }
    return None


def _fail(prefix: str, exc: BaseException) -> dict:
    """统一的失败返回：记录 warning（不进 error 日志噪声），返回结构化错误。"""
    logger.warning("[extra_tools] %s: %s", prefix, exc)
    return {"ok": False, "error": f"{prefix}: {exc}"}


def _as_int(value, default, low=None, high=None) -> int:
    """宽松取整：非法值退回 default，并夹在 [low, high] 区间内。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def register_all(dl):
    """注册所有补登记能力工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性与权限子系统）
    """

    # ════════════════════════════════════════════════════════════
    #  Python 沙盒执行
    # ════════════════════════════════════════════════════════════

    @_tools.register("run_sandbox", "在受限 Python 沙盒中执行纯计算代码片段。独立子进程 + 超时强杀；仅暴露安全内置函数，禁用 import、open、eval、getattr 及一切类型属性遍历。注意：沙盒不提供 print，输出通道仅有异常信息，因此它适合验证纯计算逻辑是否报错。Run Python code in a restricted sandbox, execute python snippet", schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码（纯计算；含 import/open/eval/双下划线属性 等模式会被直接拒绝）"},
            "timeout_sec": {"type": "integer", "description": "执行超时秒数，默认 5，范围 1-60"},
        },
        "required": ["code"],
    })
    def _run_sandbox(**kwargs):
        try:
            code = kwargs.get("code", "")
            if not code or not str(code).strip():
                return {"ok": False, "error": "请提供要执行的代码（code）"}
            code = str(code)
            timeout_sec = _as_int(kwargs.get("timeout_sec", 5), 5, low=1, high=60)

            blocked = _text_allowed(dl, code)
            if blocked:
                return blocked
            denied = _action_allowed(dl, "run_sandbox", "在受限沙盒中执行代码")
            if denied:
                return denied

            from agent.system_tools import run_sandbox as _impl
            result = _impl(code, timeout_sec=timeout_sec)
            if not isinstance(result, dict):
                return {"ok": False, "error": f"沙盒返回了非预期结果类型: {type(result).__name__}"}
            out = dict(result)
            out["ok"] = not out.get("error") and not out.get("timed_out")
            return out
        except Exception as e:  # noqa: BLE001
            return _fail("沙盒执行失败", e)

    # ════════════════════════════════════════════════════════════
    #  剪贴板
    # ════════════════════════════════════════════════════════════

    @_tools.register("get_clipboard", "获取系统剪贴板（clipboard）里已有的文本，最多 10000 字符。仅用于「把剪贴板里的东西拿过来」这类需求；要读文件请用 read_file，要读 PDF 请用 read_pdf。Get clipboard text.", schema={
        "type": "object",
        "properties": {},
    })
    def _get_clipboard(**kwargs):
        try:
            from agent.system_tools import get_clipboard as _impl
            result = _impl()
            if isinstance(result, dict):
                return result
            return {"ok": True, "content": str(result)}
        except Exception as e:  # noqa: BLE001
            return _fail("剪贴板读取失败", e)

    @_tools.register("set_clipboard", "把文本放进系统剪贴板（clipboard），最多 50000 字符。会覆盖用户剪贴板里已有的内容，属有副作用的写操作，需通过权限与内容安全检查。Set clipboard text.", schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要写入剪贴板的文本内容（最长 50000 字符）"},
        },
        "required": ["text"],
    })
    def _set_clipboard(**kwargs):
        try:
            text = kwargs.get("text", "")
            if text is None or text == "":
                return {"ok": False, "error": "请提供要写入剪贴板的文本（text）"}
            text = str(text)
            if len(text) > 50000:
                return {"ok": False, "error": "内容过长（最大 50000 字符）"}

            blocked = _text_allowed(dl, text)
            if blocked:
                return blocked
            denied = _action_allowed(dl, "set_clipboard", "写入系统剪贴板")
            if denied:
                return denied

            from agent.system_tools import set_clipboard as _impl
            result = _impl(text)
            if isinstance(result, dict):
                return result
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return _fail("剪贴板写入失败", e)

    # ════════════════════════════════════════════════════════════
    #  浏览器（selenium；仅 http/https，禁内网地址）
    # ════════════════════════════════════════════════════════════

    @_tools.register("browser_navigate", "用受控浏览器打开网页并返回标题与正文文本（最多 5000 字符）。仅允许 http/https，且拒绝 localhost 与内网地址。需要 selenium，未安装时返回明确错误。Open a web page with a browser, browse URL", schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址，必须以 http:// 或 https:// 开头"},
        },
        "required": ["url"],
    })
    def _browser_navigate(**kwargs):
        try:
            url = kwargs.get("url", "")
            if not url:
                return {"ok": False, "error": "请提供要打开的网址（url）"}
            from agent.tools.browser_tools import browser_navigate as _impl
            return _impl(str(url))
        except Exception as e:  # noqa: BLE001
            return _fail("浏览器导航失败", e)

    @_tools.register("browser_screenshot", "截取当前浏览器页面的截图，返回 base64 编码的 PNG（最多 500000 字符）。需先调用 browser_navigate 打开页面。Take a screenshot of the current page", schema={
        "type": "object",
        "properties": {},
    })
    def _browser_screenshot(**kwargs):
        try:
            from agent.tools.browser_tools import browser_screenshot as _impl
            return _impl()
        except Exception as e:  # noqa: BLE001
            return _fail("浏览器截图失败", e)

    @_tools.register("browser_close", "关闭受控浏览器并释放驱动进程。浏览器不可用或已关闭时同样返回成功（幂等）。Close the browser", schema={
        "type": "object",
        "properties": {},
    })
    def _browser_close(**kwargs):
        try:
            from agent.tools.browser_tools import browser_close as _impl
            _impl()
            return {"ok": True, "closed": True}
        except Exception as e:  # noqa: BLE001
            return _fail("关闭浏览器失败", e)

    # ════════════════════════════════════════════════════════════
    #  受保护工作区（workspace/，路径越界一律拒绝）
    # ════════════════════════════════════════════════════════════

    @_tools.register("workspace_init", "初始化云枢受保护工作区目录（workspace/），创建 .gitkeep 与 README。幂等：已存在时不会覆盖文件。Initialize the protected workspace directory", schema={
        "type": "object",
        "properties": {},
    })
    def _workspace_init(**kwargs):
        try:
            from agent.tools.workspace_tools import init_workspace as _impl
            path = _impl()
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            return _fail("工作区初始化失败", e)

    @_tools.register("workspace_list", "列出受保护工作区内的文件与子目录；传入文件路径则返回该文件的前 5000 字符内容。路径越出工作区会被拒绝。List files in the protected workspace", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区内的相对路径，留空表示工作区根目录"},
        },
    })
    def _workspace_list(**kwargs):
        try:
            path = kwargs.get("path", "") or ""
            from agent.tools.workspace_tools import list_workspace as _impl
            result = _impl(str(path))
            if isinstance(result, dict):
                out = dict(result)
                out.setdefault("ok", "error" not in out)
                return out
            return {"ok": True, "result": result}
        except Exception as e:  # noqa: BLE001
            return _fail("工作区列表失败", e)

    @_tools.register("workspace_write", "把内容写入受保护工作区内的文件（自动创建父目录，覆盖同名文件）。相对路径越出工作区会被拒绝。Write a file inside the protected workspace", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区内的相对文件路径，如 notes/plan.md"},
            "content": {"type": "string", "description": "要写入的文本内容"},
        },
        "required": ["path", "content"],
    })
    def _workspace_write(**kwargs):
        try:
            path = kwargs.get("path", "")
            content = kwargs.get("content", "")
            if not path:
                return {"ok": False, "error": "请提供工作区内的相对路径（path）"}
            if content is None:
                return {"ok": False, "error": "请提供写入内容（content）"}

            blocked = _text_allowed(dl, str(content))
            if blocked:
                return blocked
            denied = _action_allowed(dl, f"write_workspace:{path}", f"写入受保护工作区文件 {path}")
            if denied:
                return denied

            from agent.tools.workspace_tools import write_workspace as _impl
            result = _impl(str(path), str(content))
            if isinstance(result, dict):
                return result
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            return _fail("工作区写入失败", e)

    @_tools.register("workspace_delete", "删除受保护工作区内的文件或目录（目录递归删除），不可撤销。禁止删除工作区根目录，路径越界会被拒绝。Delete a file or directory inside the protected workspace", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区内的相对路径（不允许空值或工作区根目录）"},
        },
        "required": ["path"],
    })
    def _workspace_delete(**kwargs):
        try:
            path = kwargs.get("path", "")
            if not path or str(path).strip() in (".", "/", "\\"):
                return {"ok": False, "error": "请提供工作区内要删除的相对路径（path），不允许删除工作区根目录"}
            denied = _action_allowed(dl, f"delete_workspace:{path}", f"删除受保护工作区路径 {path}")
            if denied:
                return denied
            from agent.tools.workspace_tools import delete_workspace as _impl
            result = _impl(str(path))
            if isinstance(result, dict):
                return result
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            return _fail("工作区删除失败", e)

    # ════════════════════════════════════════════════════════════
    #  PDF 表格提取
    # ════════════════════════════════════════════════════════════

    @_tools.register("read_pdf_tables", "提取 PDF 文件中的表格数据，返回每页每个表格的二维单元格数组。依赖 pdfplumber，未安装时返回明确错误。Extract tables from PDF files, parse PDF table data", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "PDF 文件路径"},
            "pages": {
                "type": "array", "items": {"type": "integer"},
                "description": "要提取的页码列表（1-based），如 [1, 3]，不传则提取全部页面",
            },
        },
        "required": ["path"],
    })
    def _read_pdf_tables(**kwargs):
        try:
            path = kwargs.get("path", "")
            if not path:
                return {"ok": False, "error": "请提供 PDF 文件路径（path）"}
            pages = kwargs.get("pages")
            if pages is not None and not isinstance(pages, list):
                return {"ok": False, "error": "pages 必须是页码数组，如 [1, 3]"}
            from agent.pdf_tools import read_pdf_tables as _impl
            return _impl(str(path), pages=pages)
        except Exception as e:  # noqa: BLE001
            return _fail("PDF 表格提取失败", e)

    # ════════════════════════════════════════════════════════════
    #  MCP 连接清单
    # ════════════════════════════════════════════════════════════

    @_tools.register("list_mcp_connections", "列出当前活跃的 MCP 服务连接（连接 ID、名称、传输方式、已注册工具数与工具名）。用于确认 MCP 服务是否接上、其工具是否已进入工具表。List active MCP connections", schema={
        "type": "object",
        "properties": {},
    }, source=_tools.SOURCE_MCP_ADMIN)
    def _list_mcp_connections(**kwargs):
        try:
            discovery = getattr(dl, "_discovery_service", None)
            if discovery is None:
                return {
                    "ok": True,
                    "connections": [],
                    "count": 0,
                    "note": "工具发现服务未初始化，当前进程没有 MCP 连接管理器",
                }
            lister = getattr(discovery, "list_mcp_connections", None)
            if not callable(lister):
                # 兼容旧接口：直接取连接管理器
                lister = getattr(getattr(discovery, "mcp", None), "list_connections", None)
            if not callable(lister):
                return {"ok": False, "error": "该发现服务实例不支持列出 MCP 连接", "connections": []}
            connections = lister() or []
            if not isinstance(connections, list):
                connections = [connections]
            return {"ok": True, "connections": connections, "count": len(connections)}
        except Exception as e:  # noqa: BLE001
            return _fail("列出 MCP 连接失败", e)

    # ════════════════════════════════════════════════════════════
    #  屏幕 OCR（多模态）
    # ════════════════════════════════════════════════════════════

    @_tools.register("look_at_screen", "对当前屏幕（或指定区域）做 OCR，返回带坐标位置的文字。屏幕内容可能含敏感信息，请谨慎转述。需要 OCR 传感器已启用。Read the screen with OCR, see what is on screen", schema={
        "type": "object",
        "properties": {
            "region": {
                "type": "array", "items": {"type": "integer"},
                "description": "可选区域 [left, top, width, height]（4 个整数），不传则截取整个屏幕",
            },
        },
    })
    def _look_at_screen(**kwargs):
        try:
            region = kwargs.get("region")
            if region is not None:
                if not isinstance(region, (list, tuple)) or len(region) != 4:
                    return {"ok": False, "error": "region 必须是 4 个整数的数组 [left, top, width, height]"}
                try:
                    region = tuple(int(v) for v in region)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "region 的元素必须都是整数"}
            try:
                voice = getattr(dl, "voice", None)
            except Exception as e:  # noqa: BLE001 属性访问本身可能因懒加载失败
                return _fail("语音/视觉模块不可用", e)
            if voice is None or not callable(getattr(voice, "look_at_screen", None)):
                return {"ok": False, "error": "语音/视觉模块不可用（dl.voice 未就绪）"}
            result = voice.look_at_screen(region)
            if isinstance(result, dict):
                return result
            return {"ok": True, "text": str(result)}
        except Exception as e:  # noqa: BLE001
            return _fail("屏幕 OCR 失败", e)

    # ════════════════════════════════════════════════════════════
    #  周报生成
    # ════════════════════════════════════════════════════════════

    @_tools.register("weekly_report", "生成本周工作总结报告（含统计、洞察与建议）并按指定格式落盘。默认写入 data/reports，支持 json/html/text 三种格式。Generate a weekly work report", schema={
        "type": "object",
        "properties": {
            "output_dir": {"type": "string", "description": "报告输出目录，默认 data/reports（相对路径按项目根目录解析）"},
            "save_formats": {
                "type": "array", "items": {"type": "string", "enum": ["json", "html", "text"]},
                "description": "保存格式列表，默认 [\"json\", \"html\", \"text\"]",
            },
        },
    })
    def _weekly_report(**kwargs):
        try:
            import os

            output_dir = kwargs.get("output_dir") or "data/reports"
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if not os.path.isabs(str(output_dir)):
                output_dir = os.path.join(repo_root, str(output_dir))

            raw_formats = kwargs.get("save_formats")
            if raw_formats is None:
                formats = ["json", "html", "text"]
            elif isinstance(raw_formats, str):
                formats = [raw_formats]
            elif isinstance(raw_formats, (list, tuple)):
                formats = [str(f) for f in raw_formats]
            else:
                return {"ok": False, "error": "save_formats 必须是格式数组，如 [\"json\", \"text\"]"}
            allowed = {"json", "html", "text"}
            formats = [f for f in formats if f in allowed]
            if not formats:
                return {"ok": False, "error": f"没有合法格式，可选值: {sorted(allowed)}"}

            denied = _action_allowed(dl, f"write_dir:{output_dir}", f"写入周报目录 {output_dir}")
            if denied:
                return denied

            from agent.weekly_report_generator import run_weekly_report as _impl
            report, saved_files = _impl(output_dir=output_dir, save_formats=formats)
            return {
                "ok": True,
                "output_dir": str(output_dir),
                "files": list(saved_files or []),
                "report": report,
            }
        except Exception as e:  # noqa: BLE001
            return _fail("周报生成失败", e)
