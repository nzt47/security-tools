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

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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

        try:
            if config.transport == "stdio":
                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                )
                transport_ctx = stdio_client(params)
            else:
                transport_ctx = sse_client(config.url)

            with transport_ctx as (read, write):
                with ClientSession(read, write) as session:
                    session.initialize()
                    result = session.list_tools()
                    return [self._tool_to_dict(t) for t in result.tools]
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

        try:
            if config.transport == "stdio":
                params_obj = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                )
                transport_ctx = stdio_client(params_obj)
            else:
                transport_ctx = sse_client(config.url)

            with transport_ctx as (read, write):
                with ClientSession(read, write) as session:
                    session.initialize()
                    result = session.call_tool(tool_name, params)
                    return self._extract_result(result)
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
