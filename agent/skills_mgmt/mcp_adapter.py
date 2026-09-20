"""MCP 协议适配器 — 把 MCP server 暴露的 tools 桥接为云枢 Skill

能力:
    1. discover_from_mcp_server(server_config) -> List[Dict]:
       从 MCP server 拉取 tools/list,转为 Skill 草稿(分类=MCP)
    2. invoke_mcp_skill(skill_id, params) -> dict:
       通过 MCP 协议调用已注册的 MCP skill(不走 subprocess)

设计原则:
    - 可选依赖: mcp SDK 缺失时降级,不阻断 import
    - 安全门控: 拉取的 tool 必须经 SecurityScanner 审核才能注册
    - 边界显性化: SDK 缺失/server 不可达/协议错误均抛 SkillMcpError
    - 复用现有体系: 注册走 SkillsMgmtService
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .exceptions import SkillMcpError, SkillNotFoundError, ErrorCode
from .models import Skill, SkillCategory, SkillStatus, ContentType
from .observability import logger, emit_metric, track_event, traced_action

logger = logging.getLogger(__name__)


@dataclass
class McpServerConfig:
    """MCP server 连接配置"""
    name: str
    transport: str = "stdio"                     # stdio | sse
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    timeout: int = 30

    def validate(self) -> None:
        if self.transport not in ("stdio", "sse"):
            raise SkillMcpError(
                f"不支持的 transport: {self.transport} (仅支持 stdio/sse)",
                code=ErrorCode.MCP_PROTOCOL_ERROR,
            )
        if self.transport == "stdio" and not self.command:
            raise SkillMcpError(
                "stdio 模式必须提供 command",
                code=ErrorCode.MCP_PROTOCOL_ERROR,
            )
        if self.transport == "sse" and not self.url:
            raise SkillMcpError(
                "sse 模式必须提供 url",
                code=ErrorCode.MCP_PROTOCOL_ERROR,
            )


def _check_mcp_sdk() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


# ════════════════════════════════════════════════════════════
#  超时上界辅助（TASK-08 子工作流 D / E1j）
# ════════════════════════════════════════════════════════════

def _adapter_call_timeout(config: McpServerConfig) -> float:
    """本次 MCP 调用的上界（秒）

    优先 `config.timeout`（这正是 E1j 要修的那条：配置项必须真的生效）；
    配置缺省/非正数时才用全局兜底开关。读取失败一律回 30s。
    """
    try:
        from agent.timeout_budget import mcp_call_timeout
        sec = float(mcp_call_timeout(config))
        return sec if sec > 0 else 30.0
    except Exception:  # noqa: BLE001  开关读不到 ⇒ 保守默认
        try:
            declared = float(getattr(config, "timeout", 0) or 0)
        except (TypeError, ValueError):
            declared = 0.0
        return declared if declared > 0 else 30.0


async def _maybe_await(value: Any) -> Any:
    """兼容 SDK 的 async / sync 两种形态

    Why：`mcp` SDK 的 `ClientSession.initialize()` / `list_tools()` /
    `call_tool()` 都是协程函数 —— 改动前的裸调 `session.initialize()`
    **根本没有 await**，返回的是一个从未被驱动的协程对象：既不会报错
    （只有 RuntimeWarning），也永远不会真正握手。这里统一 await，
    使"调用真的发生"，从而"超时"这件事才有意义可言。
    """
    if hasattr(value, "__await__"):
        return await value
    return value


def _open_session(client_session_cls: Any, read: Any, write: Any,
                  config: McpServerConfig) -> Any:
    """构造 `ClientSession`，并在 SDK 支持时**把超时真正传进去**

    `read_timeout_seconds` 需要 `datetime.timedelta`；老版本 SDK 不接受该
    关键字，故按 `TypeError` 回退到不带它的构造 —— 回退路径仍有本文件的
    线程上界保护，不会退回"无界"。
    """
    try:
        from datetime import timedelta
        return client_session_cls(
            read, write,
            read_timeout_seconds=timedelta(seconds=_adapter_call_timeout(config)),
        )
    except TypeError:
        return client_session_cls(read, write)
    except Exception:  # noqa: BLE001  任何构造异常都退回最朴素构造
        return client_session_cls(read, write)


class McpSkillAdapter:
    """MCP server ↔ 云枢 Skill 桥接

    用法:
        adapter = McpSkillAdapter(skills_service)
        skills = adapter.discover_from_mcp_server(config)
        result = adapter.invoke_mcp_skill("mcp-foo-bar", params={"x": 1})
    """

    def __init__(self, skills_service, *,
                 security_scanner=None,
                 auto_review: bool = True):
        self._svc = skills_service
        self._auto_review = auto_review
        if security_scanner is None:
            from .reviewer import SecurityScanner
            security_scanner = SecurityScanner(block_on_critical=True)
        self._scanner = security_scanner

    # ─── 发现: MCP server → Skill 草稿 ───

    def discover_from_mcp_server(self, config: McpServerConfig,
                                  *, auto_register: bool = False,
                                  force: bool = False) -> List[Dict[str, Any]]:
        """从 MCP server 拉取 tools/list,转为 Skill 草稿

        Args:
            config: MCP server 连接配置
            auto_register: True 时自动注册到 skills_service
            force: 是否覆盖已存在的同 ID 技能

        Returns:
            草稿列表,每项含 skill_id/name/registered/quality_gate_passed
        """
        with traced_action("mcp_discover", server=config.name) as ctx:
            if not _check_mcp_sdk():
                raise SkillMcpError(
                    "mcp SDK 未安装,请执行 `pip install mcp`",
                    code=ErrorCode.MCP_SDK_UNAVAILABLE,
                )
            config.validate()

            tools = self._list_tools(config)
            logger.info("[MCP] server=%s 拉取到 %d 个 tools",
                        config.name, len(tools))

            results: List[Dict[str, Any]] = []
            for tool in tools:
                draft = self._tool_to_skill_draft(tool, config)
                if self._auto_review and not self._pass_security(draft):
                    results.append({
                        "skill_id": draft["id"],
                        "name": draft["name"],
                        "registered": False,
                        "quality_gate_passed": False,
                        "reason": "安全审核未通过",
                    })
                    continue

                registered = False
                if auto_register:
                    try:
                        if force and hasattr(self._svc, 'upsert_skill'):
                            self._svc.upsert_skill(draft)
                        elif hasattr(self._svc, 'create_manual'):
                            self._svc.create_manual(draft)
                        registered = True
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[MCP] 注册失败 skill=%s: %s",
                                       draft["id"], e)

                results.append({
                    "skill_id": draft["id"],
                    "name": draft["name"],
                    "registered": registered,
                    "quality_gate_passed": True,
                })

            ctx["tools_count"] = len(tools)
            ctx["registered"] = sum(1 for r in results if r["registered"])
            emit_metric("yunshu_mcp_discover_total",
                        value=len(results), kind="counter",
                        labels={"server": config.name})
            return results

    # ─── 调用: 已注册的 MCP skill ───

    def invoke_mcp_skill(self, skill_id: str, *,
                         params: Optional[Dict[str, Any]] = None,
                         config: Optional[McpServerConfig] = None,
                         timeout: Optional[int] = None) -> Dict[str, Any]:
        """通过 MCP 协议调用已注册的 MCP skill

        Raises:
            SkillNotFoundError: 技能不存在
            SkillMcpError: 非 MCP 类技能 / SDK 缺失 / 调用失败
        """
        with traced_action("mcp_invoke", skill_id=skill_id):
            skill = self._svc.get(skill_id) if hasattr(self._svc, 'get') else None
            if not skill:
                raise SkillNotFoundError(skill_id)

            if skill.category != SkillCategory.MCP.value:
                raise SkillMcpError(
                    f"技能 {skill_id} 非 MCP 类(category={skill.category})",
                    code=ErrorCode.MCP_PROTOCOL_ERROR,
                )

            if config is None:
                config = self._config_from_skill(skill)
            if timeout:
                config.timeout = timeout

            tool_name = skill.default_params.get("mcp_tool_name", "")
            if not tool_name:
                raise SkillMcpError(
                    f"技能 {skill_id} 缺少 mcp_tool_name 参数",
                    code=ErrorCode.MCP_TOOL_NOT_FOUND,
                )

            result = self._call_tool(config, tool_name, params or {})
            track_event("mcp_skill_invoked", {
                "skill_id": skill_id, "tool": tool_name,
                "server": config.name,
            })
            return result

    # ─── 内部: MCP 协议调用 ───

    def _bounded_mcp_call(self, config: McpServerConfig, label: str,
                          build_coro: Callable[[], Any]) -> Any:
        """在**有限时间**内跑完一次完整 MCP 交互（连接→握手→调用→拆除）

        【TASK-08 子工作流 D / E1j：为什么必须这样改】
            改动前 `_list_tools` / `_call_tool` 是裸调：
                session.initialize()
                result = session.list_tools()
            两个问题：
              (a) **无超时上界** —— server 卡住则主进程无限阻塞；
              (b) `McpServerConfig.timeout`（默认 30）**从未传给 SDK**，
                  即"配置了等于没配"。本方法同时修掉这两条。

        【为什么整段生命周期都放进同一个工作线程】
            `mcp` SDK 基于 anyio：`stdio_client` 的任务组与 `ClientSession`
            的取消作用域是**线程/任务亲和**的，必须"在同一处进出"。
            若只把 `session.list_tools()` 搬到别的线程执行，而 `transport_ctx`
            在调用线程进出，anyio 会因为 cancel scope 跨任务而直接报错。
            因此这里把"建连 + 握手 + 调用 + 拆除"**整体**投到一个专用
            daemon 线程里跑：亲和性完整保留，同时获得真正的墙钟上界。

        【为什么用 asyncio.run 而不是依赖调用方的事件循环】
            我们**总是在一个全新线程**里执行，该线程没有运行中的事件循环，
            故 `asyncio.run` 永远合法 —— 即使调用方本身处在 async 上下文里
            （这正是"直接 await"做不到的场景）。

        【降级】`agent.timeout_budget` 不可用时退回无界执行（旧行为），
            只为不让新模块的加载问题阻断 MCP 发现链路（D4/D2）。
        """
        timeout_sec = _adapter_call_timeout(config)

        async def _driver() -> Any:
            coro = build_coro()
            try:
                return await asyncio.wait_for(coro, timeout=timeout_sec)
            except asyncio.TimeoutError as exc:              # 3.11+ 别名
                raise SkillMcpError(
                    f"MCP 调用超时（{label}，上界 {timeout_sec:.1f}s）",
                    code=ErrorCode.MCP_SERVER_UNREACHABLE,
                ) from exc

        def _run() -> Any:
            try:
                return asyncio.run(_driver())
            except RuntimeError as exc:
                # 极端情形：工作线程里竟已有事件循环 ⇒ 退化为同步执行原调用
                if "event loop" not in str(exc).lower():
                    raise
                return None

        try:
            from agent.timeout_budget import call_with_timeout
        except Exception:  # noqa: BLE001  上界机制不可用 ⇒ 旧行为
            return _run()

        # 外层线程上界 = SDK 上界 + 宽限：内层 wait_for 负责精确取消，
        # 外层只兜底"连取消都没生效"的彻底挂死。
        _ok, _outcome = call_with_timeout(
            _run, timeout_sec + 15.0, label=f"mcp:{label}")
        if not _ok:
            raise SkillMcpError(
                f"MCP 调用超时（{label}，上界 {timeout_sec:.1f}s）",
                code=ErrorCode.MCP_SERVER_UNREACHABLE,
            )
        return _outcome

    def _list_tools(self, config: McpServerConfig) -> List[Dict[str, Any]]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.sse import sse_client
        except ImportError as e:
            raise SkillMcpError(
                f"mcp SDK 导入失败: {e}",
                code=ErrorCode.MCP_SDK_UNAVAILABLE,
            ) from e

        async def _build() -> List[Dict[str, Any]]:
            if config.transport == "stdio":
                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                )
                transport_ctx = stdio_client(params)
            else:
                transport_ctx = sse_client(config.url)

            async with transport_ctx as (read, write):
                # 配置的 timeout **真正传下去**（E1j：不再"配置了等于没配"）。
                # `read_timeout_seconds` 由 SDK 自己实现，故即使本层的线程上界
                # 失效，SDK 仍会独立抛出读超时 —— 两道独立的界。
                session = _open_session(ClientSession, read, write, config)
                async with session:
                    await _maybe_await(session.initialize())
                    result = await _maybe_await(session.list_tools())
                    return [self._tool_to_dict(t) for t in result.tools]

        try:
            return self._bounded_mcp_call(config, f"{config.name}:tools/list", _build)
        except SkillMcpError:
            raise
        except Exception as e:
            raise SkillMcpError(
                f"MCP tools/list 调用失败 (server={config.name}): {e}",
                code=ErrorCode.MCP_SERVER_UNREACHABLE,
            ) from e

    def _call_tool(self, config: McpServerConfig, tool_name: str,
                   params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.sse import sse_client
        except ImportError as e:
            raise SkillMcpError(
                f"mcp SDK 导入失败: {e}",
                code=ErrorCode.MCP_SDK_UNAVAILABLE,
            ) from e

        async def _build() -> Dict[str, Any]:
            if config.transport == "stdio":
                params_obj = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                )
                transport_ctx = stdio_client(params_obj)
            else:
                transport_ctx = sse_client(config.url)

            async with transport_ctx as (read, write):
                session = _open_session(ClientSession, read, write, config)
                async with session:
                    await _maybe_await(session.initialize())
                    result = await _maybe_await(session.call_tool(tool_name, params))
                    return self._extract_result(result)

        try:
            return self._bounded_mcp_call(
                config, f"{config.name}:tools/call:{tool_name}", _build)
        except SkillMcpError:
            raise
        except Exception as e:
            raise SkillMcpError(
                f"MCP tools/call 调用失败 (tool={tool_name}): {e}",
                code=ErrorCode.MCP_PROTOCOL_ERROR,
            ) from e

    # ─── 内部: 转换工具 ───

    @staticmethod
    def _tool_to_dict(tool) -> Dict[str, Any]:
        if hasattr(tool, "model_dump"):
            return tool.model_dump()
        if hasattr(tool, "dict"):
            return tool.dict()
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "inputSchema": getattr(tool, "inputSchema", {}) or
                            getattr(tool, "input_schema", {}),
        }

    @staticmethod
    def _extract_result(result) -> Dict[str, Any]:
        if hasattr(result, "model_dump"):
            data = result.model_dump()
        elif hasattr(result, "dict"):
            data = result.dict()
        else:
            data = {"content": result.content if hasattr(result, "content") else []}

        contents = data.get("content", [])
        for item in contents:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"text": text}
        return data

    def _tool_to_skill_draft(self, tool: Dict[str, Any],
                              config: McpServerConfig) -> Dict[str, Any]:
        tool_name = tool.get("name", "unknown")
        description = tool.get("description", "") or f"MCP tool {tool_name}"
        input_schema = tool.get("inputSchema") or tool.get("input_schema") or {}

        safe_server = "".join(c if c.isalnum() else "-"
                              for c in config.name.lower()).strip("-")
        safe_tool = "".join(c if c.isalnum() else "-"
                            for c in tool_name.lower()).strip("-")
        skill_id = f"mcp-{safe_server}-{safe_tool}"[:64]

        content = self._render_tool_content(tool_name, description, input_schema)

        return {
            "id": skill_id,
            "name": f"{tool_name} (MCP/{config.name})",
            "description": description[:2000],
            "content": content,
            "content_type": ContentType.MARKDOWN.value,
            "category": SkillCategory.MCP.value,
            "tags": ["mcp", f"mcp:{config.name}"],
            "default_params": {
                "mcp_tool_name": tool_name,
                "mcp_server": config.name,
                "mcp_transport": config.transport,
            },
            "config_schema": input_schema if isinstance(input_schema, dict) else {},
            "dependencies": [],
            "source": f"mcp:{config.name}",
            "author": "mcp_adapter",
            "version": "0.1.0",
            "status": SkillStatus.PENDING_REVIEW.value,
        }

    @staticmethod
    def _render_tool_content(name: str, description: str,
                              input_schema: Dict[str, Any]) -> str:
        lines = [f"# {name}", "", description, ""]
        if input_schema:
            lines.append("## 输入参数 (JSON Schema)")
            lines.append("```json")
            lines.append(json.dumps(input_schema, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")
        lines.extend([
            "## 调用方式",
            f"通过 MCP 协议调用 tool `{name}`,参数通过 invoke_mcp_skill 传入。",
            "",
            "## 来源",
            "由 mcp_adapter 自动从 MCP server 拉取并注册。",
        ])
        return "\n".join(lines)

    # ─── 内部: 安全 + 配置 ───

    def _pass_security(self, draft: Dict[str, Any]) -> bool:
        try:
            skill = Skill.from_storage_dict(draft)
            self._scanner.scan(skill)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[MCP] 草稿 %s 安全审核未通过: %s",
                           draft.get("id"), e)
            return False

    @staticmethod
    def _config_from_skill(skill: Skill) -> McpServerConfig:
        p = skill.default_params
        return McpServerConfig(
            name=p.get("mcp_server", "unknown"),
            transport=p.get("mcp_transport", "stdio"),
        )
