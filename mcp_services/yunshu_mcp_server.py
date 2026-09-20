"""云枢 MCP 服务端（stdio）—— 把云枢的**只读**工具暴露给外部 MCP 客户端

云枢此前只做 MCP **客户端**（``mcp_services/mcp_client.py`` 等三条链路，能连别人的
MCP server），因此 B 方向（Trae / DSH / Claude Code 调用云枢能力）没有落点。本模块
补上这个"服务端面"：一个符合 MCP 标准的 **stdio** 服务端，把云枢注册表里的工具
以 MCP ``tools/list`` / ``tools/call`` 的形式暴露出去。

零依赖手写实现（**不引入 ``mcp`` 官方 SDK**：本机未安装、``requirements.txt`` 也没有），
JSON-RPC 2.0 的手写口径与 ``mcp_services/multi_search_engine.py`` 保持一致——那里是
本仓既有的手写 stdio 服务端范本。

────────────────────────────────────────────────────────────────────────
启动方式
────────────────────────────────────────────────────────────────────────
    python mcp_services/yunshu_mcp_server.py

stdin 逐行读 JSON-RPC 请求，stdout 逐行写 JSON-RPC 响应（一行一帧 + 立即 flush）。

────────────────────────────────────────────────────────────────────────
安全边界（**改这个文件前先读这一节**）
────────────────────────────────────────────────────────────────────────
1. **默认只暴露只读工具**：``read_file`` / ``list_directory`` / ``get_file_info`` /
   ``search_files`` / ``grep``。MCP 客户端是**外部进程**（IDE、别的智能体），
   默认授权它写宿主文件或执行命令是不可接受的默认值，所以写入类（``write_file``
   / ``edit``）与执行类（``shell_execute`` / ``run_program`` / ``software_install``
   / ``delegate`` / ``todo_write`` 等）**一律不在默认清单里**，也不会出现在
   ``tools/list`` 响应中。
2. **放宽入口是环境变量 ``CP_MCP_SERVER_TOOLS``**（逗号分隔的真实注册工具名）。
   ⚠️ **放开写入/执行类工具 == 允许外部客户端改动宿主**：客户端进程能读写的，
   它就能读写；没有二次确认、没有人在回路。名字必须真实存在于注册表，否则
   **启动即失败**（写 stderr + 退出码 2），绝不静默忽略——静默忽略会让人以为
   "已经放开了"，实际整个清单被悄悄丢弃。
3. **不启动完整 DigitalLife**：只用一个 fail-closed 的最小替身 ``_MinimalDl``
   （任何属性访问都抛 ``_DlAccessError``）喂给 ``register_all(dl)``。若某个被放开的
   工具意外依赖 ``dl``，它会**当场报错**（收口成 ``isError: true`` 的工具结果），
   而不是静默跳过 ``dl._permission`` 之类的安全检查。
4. **所有调用都走 ``agent.tools.call()``**，不直接调 handler——``agent/tool_gate.py``
   的集中式闸门装在 ``call()`` 里（fail-open + 显式拒绝），绕开它等于绕开唯一的
   集中收口点。
5. **不做鉴权**：stdio 由客户端进程自己拉起，**信任边界在客户端侧**（谁启动谁负责）。
   因此 ⚠️ **不要把本服务端暴露到网络**：它没有 TLS、没有令牌、没有来源校验，
   stdio 之外没有任何保护（例如用 socat/端口转发把 stdio 接到 TCP 就等于把宿主
   只读文件系统对外开放）。要远程暴露，请另建带鉴权的 HTTP 入口，而不是转发 stdio。

────────────────────────────────────────────────────────────────────────
协议实现范围
────────────────────────────────────────────────────────────────────────
已实现：``initialize`` / ``tools/list`` / ``tools/call`` / ``ping``；未知方法返回
JSON-RPC ``-32601``；``notifications/*`` 是通知（无 ``id``、按 JSON-RPC 2.0 无响应），
静默接收不产生帧。

**未实现**：``resources`` 与 ``prompts``（本版不做），因此 ``initialize`` 的
``capabilities`` 里**只声明 ``tools``**——不声明就不返回 ``-32601``，客户端据此判断
能力；声明了却拒绝会直接误导客户端。同理 ``tools.listChanged = false``（工具清单在
进程生命周期内不变，不谎报会推送变更通知）。

────────────────────────────────────────────────────────────────────────
环境变量
────────────────────────────────────────────────────────────────────────
- ``CP_MCP_SERVER_TOOLS``：逗号分隔的工具名，覆盖默认只读清单；名字不存在 ⇒ 启动失败。
- ``CP_MCP_SERVER_LOG_LEVEL``：日志级别（默认 ``INFO``）。日志一律走 **stderr**。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

#: 仓库根（本文件位于 ``<root>/mcp_services/yunshu_mcp_server.py``）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 直接 ``python mcp_services/yunshu_mcp_server.py`` 运行时 ``sys.path[0]`` 是
# ``mcp_services/``，而本机 site-packages 里存在一个**无关的同名 ``agent`` 包**
# （"Agent internals -- extracted modules from run_agent.py"），会把云枢自己的
# ``agent`` 顶掉 ⇒ ``from agent import tools`` 直接 ImportError。因此把仓库根插到
# ``sys.path`` 最前面（``mcp_services/`` 下既有的 ``demo_mcp_integration.py:19``、
# ``test_mcp_windows.py:36`` 用的是同一手法）。
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger("yunshu_mcp_server")

# ════════════════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════════════════

#: ``serverInfo.name``
SERVER_NAME = "yunshu-mcp-server"
#: ``serverInfo.version``
SERVER_VERSION = "1.0.0"

#: 缺省协议版本（与 ``mcp_services/multi_search_engine.py`` 同口径）
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
#: 能正确应答的协议版本。``initialize`` 收到其中之一即回显（MCP 规定：服务端不支持的
#: 版本必须回一个它支持的版本，客户端据此决定是否断开），其余一律退回缺省值。
SUPPORTED_PROTOCOL_VERSIONS: Tuple[str, ...] = ("2024-11-05", "2025-03-26", "2025-06-18")

#: 默认暴露的工具（**只读**，全部来自 file_tools_reg / search_tools）
DEFAULT_EXPOSED_TOOLS: Tuple[str, ...] = (
    "read_file",
    "list_directory",
    "get_file_info",
    "search_files",
    "grep",
)

#: 放宽默认清单的环境变量（逗号分隔的真实注册工具名）
ENV_EXPOSED_TOOLS = "CP_MCP_SERVER_TOOLS"
#: 日志级别环境变量
ENV_LOG_LEVEL = "CP_MCP_SERVER_LOG_LEVEL"

#: 写入类/执行类工具名（放宽时在启动日志里额外告警；这些名字**绝不**进默认清单）
WRITE_OR_EXEC_TOOLS = frozenset({
    "write_file", "edit", "shell_execute", "run_program",
    "software_install", "delegate", "todo_write",
})

#: JSON-RPC 错误码（与 multi_search_engine.py 同口径）
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INTERNAL_ERROR = -32603

#: **真实** stdout 句柄。运行期 ``sys.stdout`` 会被换成 ``sys.stderr``，只有本句柄
#: 允许承载 JSON-RPC 帧。``main()`` 会在启动时重新抓取一次（覆盖宿主改过的情况）。
_ORIGINAL_STDOUT: Any = sys.stdout

#: ``initialize`` 返回的 instructions（给客户端侧模型的使用说明）
_INSTRUCTIONS = """
# 云枢（Yunshu）MCP 服务端

本服务端把云枢的**只读**文件工具暴露给外部 MCP 客户端。

## 可用工具

- `read_file`：读取本地文本文件（可选编码 / 行范围）
- `list_directory`：列出目录内容
- `get_file_info`：文件或目录的元信息（大小、修改时间、权限等）
- `search_files`：按文件名 glob 搜索
- `grep`：按正则检索文件内容，返回相对路径 + 1-based 行号 + 该行原文

写入类与执行类工具（write_file / edit / shell_execute / ...）**默认不暴露**，
不出现在 tools/list 中；放宽需在启动前设置 `CP_MCP_SERVER_TOOLS`。

## 返回形状

`tools/call` 成功返回 `{"content": [{"type": "text", "text": "<工具结果 JSON>"}]}`；
工具结果 `ok=false`（含被集中式工具闸门拒绝）时额外带 `"isError": true`。
""".strip()


class ToolConfigError(RuntimeError):
    """工具清单配置非法（``CP_MCP_SERVER_TOOLS`` 含未注册的名字等）——启动即失败"""


class _DlAccessError(RuntimeError):
    """访问最小替身 ``dl`` 的任何属性时抛出

    **刻意不是 ``AttributeError``**：``getattr(dl, '_permission', None)`` 只吞
    ``AttributeError``。若替身抛 ``AttributeError``，"依赖 dl 的工具"会拿到
    ``None`` 并**静默跳过权限校验**——那正是 fail-open 的绕过。抛别的异常才能
    让问题当场暴露。
    """


class _MinimalDl:
    """``register_all(dl)`` 的最小替身：**任何属性访问都抛异常**（fail-closed）

    本服务端刻意不启动完整 DigitalLife（会拉起记忆/调度/模型等重组件，对一个只读
    MCP 端点毫无必要，还会把宿主进程状态一起带进来）。但 ``register_all(dl)`` 需要
    一个 ``dl``：例如 ``agent/tools/file_tools_reg.py:91`` 的 ``_write_file`` 用
    ``dl._permission`` 做权限校验。

    用"空对象"顶替是危险的：属性访问静默返回 ``None`` ⇒ 权限校验被跳过。所以这里
    的替身是 fail-closed 的——被暴露的工具一旦真的触碰 ``dl``，立即报错收口成
    ``isError: true`` 的工具结果，而不是静默放行。
    """

    def __getattribute__(self, name: str) -> Any:
        raise _DlAccessError(
            "本 MCP 服务端未启动 DigitalLife，被暴露的工具不得依赖 dl 的任何属性"
            f"（被访问属性: {name!r}）；请把该工具从暴露清单中移除"
        )


# ════════════════════════════════════════════════════════════════════
# 工具注册与暴露清单
# ════════════════════════════════════════════════════════════════════

def _register_exposed_tools() -> List[str]:
    """注册云枢文件/检索工具，返回注册后的全部工具名

    只调用 ``agent/tools/file_tools_reg.py`` 与 ``agent/tools/search_tools.py`` 的
    ``register_all(dl)``：两者在**注册期**都不触碰 ``dl``（只有它们注册出来的部分
    handler 会），因此可以直接喂 ``_MinimalDl()``。

    已注册过的工具不重复注册（同一进程内重复调用幂等）。
    """
    from agent import tools as _tools
    from agent.tools import file_tools_reg, search_tools

    known = {str(entry.get("name", "")) for entry in _tools.list_tools()}
    dl = _MinimalDl()
    if "read_file" not in known:
        file_tools_reg.register_all(dl)
    if "grep" not in known:
        search_tools.register_all(dl)
    return [str(entry.get("name", "")) for entry in _tools.list_tools()]


def _resolve_exposed_tools(available: Sequence[str]) -> Tuple[str, ...]:
    """解析暴露清单：未设环境变量 → 默认只读五件套；设了 → 逐个校验必须真实存在

    Raises:
        ToolConfigError: 环境变量解析不出任何工具名，或含未注册的工具名。
            **不静默忽略**：静默忽略会让人误以为已经放开，实际清单被悄悄丢弃。
    """
    raw = os.environ.get(ENV_EXPOSED_TOOLS)
    if raw is None or not str(raw).strip():
        return DEFAULT_EXPOSED_TOOLS

    names: List[str] = []
    for part in str(raw).split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ToolConfigError(
            f"环境变量 {ENV_EXPOSED_TOOLS} 已设置但没有解析出任何工具名: {raw!r}；"
            "如需使用默认清单请把该变量留空或删除"
        )

    known = set(available)
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ToolConfigError(
            f"环境变量 {ENV_EXPOSED_TOOLS} 含未注册的工具名: {unknown}；"
            f"已注册的工具: {sorted(known)}"
        )
    return tuple(names)


def _warn_if_widened(exposed: Sequence[str]) -> None:
    """暴露清单比默认只读集更宽时在 stderr 告警（含写入/执行类则额外点名）"""
    extra = [name for name in exposed if name not in DEFAULT_EXPOSED_TOOLS]
    if not extra:
        return
    risky = [name for name in extra if name in WRITE_OR_EXEC_TOOLS]
    logger.warning(
        "[yunshu-mcp] ⚠️ 暴露清单已被 %s 放宽，超出默认只读集: %s",
        ENV_EXPOSED_TOOLS, extra,
    )
    if risky:
        logger.warning(
            "[yunshu-mcp] ⚠️⚠️ 其中含写入/执行类工具 %s —— 等于**允许外部 MCP 客户端"
            "改动/执行宿主上的文件与命令**（无二次确认、无人在回路）",
            risky,
        )


def _normalize_schema(schema: Any) -> Dict[str, Any]:
    """把注册表里的 JSON Schema 规范成 MCP ``inputSchema``

    MCP 的 ``inputSchema`` 必须是 object 类型的 JSON Schema。用深拷贝而不是直接
    引用注册表里的 schema，避免把内部对象暴露出去被外部改写。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized: Dict[str, Any] = copy.deepcopy(schema)
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def _dump(value: Any) -> str:
    """把工具结果序列化为文本；不可序列化时退回 ``str()``（不因脏数据崩服务）"""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return json.dumps({"ok": True, "result": str(value)}, ensure_ascii=False, indent=2)


def _tool_text(payload: Any, is_error: bool) -> Dict[str, Any]:
    """构造 MCP ``tools/call`` 结果：``content`` + 失败时的 ``isError``

    ``ok=false`` 时置 ``isError: true``（MCP 客户端据此判断失败）；成功时**不带**
    ``isError`` 字段。
    """
    result: Dict[str, Any] = {"content": [{"type": "text", "text": _dump(payload)}]}
    if is_error:
        result["isError"] = True
    return result


def _tool_error(message: str, error_code: str = "TOOL_CALL_FAILED") -> Dict[str, Any]:
    """构造失败的工具结果（结构对齐本仓既有口径 ``{"ok": False, "error": ...}``）"""
    return _tool_text({"ok": False, "error": message, "error_code": error_code}, True)


def _tool_success(payload: Any) -> Dict[str, Any]:
    """构造工具结果；``payload`` 的 ``ok`` 为 False 时同样置 ``isError``"""
    is_error = isinstance(payload, dict) and payload.get("ok") is False
    return _tool_text(payload, is_error)


# ════════════════════════════════════════════════════════════════════
# MCP 协议处理器
# ════════════════════════════════════════════════════════════════════

class YunshuMCPHandler:
    """云枢 MCP 协议处理器

    实现 ``initialize`` / ``tools/list`` / ``tools/call`` / ``ping``；未知方法返回
    ``-32601``。``resources`` / ``prompts`` 未实现，故 ``capabilities`` 里不声明。
    """

    def __init__(self, exposed_tools: Optional[Sequence[str]] = None) -> None:
        """
        Args:
            exposed_tools: 显式指定暴露清单（测试用）；缺省时由
                ``CP_MCP_SERVER_TOOLS`` 或 ``DEFAULT_EXPOSED_TOOLS`` 决定。

        Raises:
            ToolConfigError: 清单含未注册的工具名。
        """
        available = _register_exposed_tools()
        self.available_tools: Tuple[str, ...] = tuple(available)

        if exposed_tools is None:
            self.exposed_tools: Tuple[str, ...] = _resolve_exposed_tools(available)
        else:
            known = set(available)
            unknown = [name for name in exposed_tools if name not in known]
            if unknown:
                raise ToolConfigError(f"未注册的工具名: {unknown}；已注册的工具: {sorted(known)}")
            self.exposed_tools = tuple(exposed_tools)

        #: 只声明 tools（resources/prompts 未实现 —— 不声明就不会返回 -32601）
        self.capabilities: Dict[str, Any] = {"tools": {"listChanged": False}}
        _warn_if_widened(self.exposed_tools)

    # ── 请求路由 ────────────────────────────────────────────────────

    def handle_request(self, request: Any) -> Optional[Dict[str, Any]]:
        """处理一帧 MCP 请求

        Returns:
            JSON-RPC 响应 dict；``notifications/*`` 是通知，按协议返回 ``None``
            （调用方不得写出任何帧）。
        """
        if not isinstance(request, dict):
            return self._error(None, JSONRPC_INVALID_REQUEST, "请求必须是 JSON 对象")

        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}

        if not isinstance(method, str) or not method:
            return self._error(req_id, JSONRPC_INVALID_REQUEST, "请求缺少 method")

        # 通知（无 id、按 JSON-RPC 2.0 不产生响应）：客户端握手会发
        # notifications/initialized，回 -32601 会污染握手。
        if method.startswith("notifications/"):
            logger.debug("[yunshu-mcp] 收到通知（无需响应）: %s", method)
            return None

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list(params)
            elif method == "tools/call":
                result = self._handle_tools_call(params)
            elif method == "ping":
                result = {"pong": True}  # 与 multi_search_engine.py 同口径
            else:
                return self._error(req_id, JSONRPC_METHOD_NOT_FOUND, f"方法不存在: {method}")
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:  # noqa: BLE001 任何未预期异常都收口成 -32603，绝不崩服务
            logger.error("[yunshu-mcp] 处理请求失败: method=%s, error=%s", method, e, exc_info=True)
            return self._error(req_id, JSONRPC_INTERNAL_ERROR, f"内部错误: {e}")

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        """构造 JSON-RPC 错误响应"""
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    # ── initialize ─────────────────────────────────────────────────

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """初始化握手（serverInfo/capabilities 结构与 multi_search_engine.py 一致）"""
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            protocol_version = requested
        else:
            protocol_version = DEFAULT_PROTOCOL_VERSION
        logger.info("[yunshu-mcp] initialize: client=%s, 协议版本=%s",
                    params.get("clientInfo", {}), protocol_version)
        return {
            "protocolVersion": protocol_version,
            "capabilities": copy.deepcopy(self.capabilities),
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "description": "云枢（Yunshu）只读工具 MCP 服务端（stdio）",
            },
            "instructions": _INSTRUCTIONS,
        }

    # ── tools/list ─────────────────────────────────────────────────

    def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """列出**被允许暴露**的工具（未列入清单的工具不出现）

        字段映射：注册表（``name`` / ``description`` / ``schema``）→ MCP
        （``name`` / ``description`` / ``inputSchema``）。注意注册表里的
        ``get_tool_defs()`` 产出的是 OpenAI function-calling 形状
        （``{"type": "function", "function": {...}}``），不能直接喂给 MCP。
        """
        from agent import tools as _tools

        descriptions = {
            str(entry.get("name", "")): str(entry.get("description", ""))
            for entry in _tools.list_tools()
        }
        return {
            "tools": [
                {
                    "name": name,
                    "description": descriptions.get(name, ""),
                    "inputSchema": _normalize_schema(_tools.get_tool_schema(name)),
                }
                for name in self.exposed_tools
            ]
        }

    # ── tools/call ─────────────────────────────────────────────────

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具（**只允许调用暴露清单内的工具**，且必经 ``agent.tools.call``）

        两条硬边界：
        1. 清单白名单在**调用之前**短路 —— 未暴露的工具连 handler 都不会碰到
           （``agent.tools.call`` 的闸门是 fail-open 的，不能指望它拦住写入类工具）；
        2. 真调用一律走 ``agent.tools.call()``，从而经过 ``agent/tool_gate.py``
           的集中式闸门，而不是直接调 handler。
        """
        tool_name = params.get("name")
        arguments = params.get("arguments")

        if not isinstance(tool_name, str) or not tool_name:
            return _tool_error("缺少工具名（name）", "INVALID_ARGUMENTS")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _tool_error("工具参数（arguments）必须是 JSON 对象", "INVALID_ARGUMENTS")
        if tool_name not in self.exposed_tools:
            return _tool_error(
                f"工具 '{tool_name}' 未在本服务端暴露；已暴露: {', '.join(self.exposed_tools)}",
                "TOOL_NOT_EXPOSED",
            )

        from agent import tools as _tools

        logger.info("[yunshu-mcp] tools/call: %s, 参数键=%s", tool_name, sorted(arguments))
        # 【TASK-05 身份标注】把"调用来自外部 MCP 客户端"写进 tool_gate 的上下文变量。
        # 【不易·为什么必须显式标注】闸门读不到上下文变量时会落到环境变量缺省值
        #   "cli" ⇒ 一次**外部 MCP 客户端**的调用会被记成"人从 CLI 调的"，
        #   在审计与 ABAC 上都是**错误归因**。MCP 协议本身不含认证，
        #   协议层给不出的身份，至少要在**来源维度**上如实标注。
        # 【D2】"mcp" 不在既有值域（cli/web/api/scheduled）内，但该值只被 ABAC 的
        #   `session_source_in` 规则消费；新增取值只会"不匹配任何既有规则"，
        #   与现有数据无冲突。这条登记在 agent/capregistry/call_sites.py 的例外表里。
        _src_handle = None
        try:
            from agent.tool_gate import set_session_source as _set_src
            _src_handle = _set_src("mcp")
        except Exception:  # noqa: BLE001  闸门不可用 ⇒ 身份标注降级（不影响调用）
            _src_handle = None
        try:
            payload = _tools.call(tool_name, **arguments)
        except Exception as e:  # noqa: BLE001 工具异常收口成错误结果，绝不崩服务
            logger.warning("[yunshu-mcp] 工具执行失败: %s — %s: %s", tool_name, type(e).__name__, e)
            return _tool_error(f"工具 '{tool_name}' 执行失败: {type(e).__name__}: {e}")
        finally:
            if _src_handle is not None:
                try:
                    _src_handle.reset()
                except Exception:  # noqa: BLE001
                    pass
        return _tool_success(payload)


# ════════════════════════════════════════════════════════════════════
# stdout 纯净化 + STDIO 主循环
# ════════════════════════════════════════════════════════════════════

@contextmanager
def _stdout_guard() -> Iterator[None]:
    """导入与运行期把 ``sys.stdout`` 临时换成 ``sys.stderr``

    本仓多处有 ``print(...)``（默认落 stdout），而 stdio MCP 的 stdout 是**协议流**：
    混进一行非 JSON 内容，客户端整条连接即报废。因此除 ``_emit()`` 外谁都不许写
    真实 stdout——重定向后，任何 ``print`` 都落到 stderr，日志不丢、协议不脏。
    """
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = real_stdout


def _emit(response: Dict[str, Any]) -> None:
    """把一帧 JSON-RPC 写到**真实 stdout**（本模块唯一允许写 stdout 的地方）

    每帧一行 JSON + 立即 flush（stdio 客户端按行解析，不 flush 会卡死等帧）。
    """
    stream = _ORIGINAL_STDOUT
    stream.write(json.dumps(response, ensure_ascii=False) + "\n")
    stream.flush()


def _configure_logging() -> None:
    """把根日志器固定到 **stderr**（stdout 只许出现 JSON-RPC 帧）

    ``force=True`` 是刻意的：这是独立 stdio 服务进程，任何继承自宿主的 stdout
    handler 都会污染协议流；此处宁可覆盖宿主的基本配置。
    """
    level_name = str(os.environ.get(ENV_LOG_LEVEL, "INFO")).strip().upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def main() -> int:
    """MCP 服务主入口 —— STDIO 通信模式

    Returns:
        进程退出码：0 正常结束（stdin EOF）；2 工具清单配置非法（拒绝启动）。
    """
    global _ORIGINAL_STDOUT
    previously = _ORIGINAL_STDOUT
    # 真实 stdout 必须在重定向**之前**抓取
    _ORIGINAL_STDOUT = sys.stdout
    try:
        with _stdout_guard():
            _configure_logging()
            try:
                handler = YunshuMCPHandler()
            except ToolConfigError as e:
                logger.error("[yunshu-mcp] 启动失败（工具清单配置非法，拒绝启动）: %s", e)
                return 2

            logger.info(
                "[yunshu-mcp] %s v%s 启动 (stdio)；已暴露 %d 个工具: %s",
                SERVER_NAME, SERVER_VERSION,
                len(handler.exposed_tools), ", ".join(handler.exposed_tools),
            )

            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("[yunshu-mcp] JSON 解析失败: %s", e)
                    _emit({
                        "jsonrpc": "2.0", "id": None,
                        "error": {"code": JSONRPC_PARSE_ERROR, "message": f"解析错误: {e}"},
                    })
                    continue

                try:
                    response = handler.handle_request(request)
                except Exception as e:  # noqa: BLE001 处理器已自兜底，这里再兜一层
                    logger.error("[yunshu-mcp] 请求处理异常: %s", e, exc_info=True)
                    req_id = request.get("id") if isinstance(request, dict) else None
                    response = {
                        "jsonrpc": "2.0", "id": req_id,
                        "error": {"code": JSONRPC_INTERNAL_ERROR, "message": f"内部错误: {e}"},
                    }
                if response is not None:  # 通知无响应
                    _emit(response)
    finally:
        _ORIGINAL_STDOUT = previously
    return 0


if __name__ == "__main__":
    sys.exit(main())
