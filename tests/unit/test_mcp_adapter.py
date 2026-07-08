"""MCP 适配器 — 单元测试

覆盖:
    - McpServerConfig 验证
    - _check_mcp_sdk 降级
    - _tool_to_skill_draft 转换
    - _extract_result 结果提取
    - _render_tool_content 内容渲染
    - discover_from_mcp_server SDK 缺失时抛 SkillMcpError
    - invoke_mcp_skill 非 MCP 类技能抛 SkillMcpError
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.skills_mgmt.mcp_adapter import (
    McpSkillAdapter, McpServerConfig, _check_mcp_sdk,
)
from agent.skills_mgmt.models import Skill, SkillCategory
from agent.skills_mgmt.exceptions import (
    SkillMcpError, SkillNotFoundError, ErrorCode,
)


class TestMcpServerConfig:
    """MCP server 配置验证"""

    def test_valid_stdio(self):
        """合法 stdio 配置"""
        c = McpServerConfig(name="test", transport="stdio", command="python")
        c.validate()  # 不抛异常

    def test_valid_sse(self):
        """合法 sse 配置"""
        c = McpServerConfig(name="test", transport="sse", url="http://localhost:8080")
        c.validate()

    def test_invalid_transport(self):
        """不支持的 transport"""
        c = McpServerConfig(name="test", transport="websocket")
        with pytest.raises(SkillMcpError) as exc:
            c.validate()
        assert exc.value.code == ErrorCode.MCP_PROTOCOL_ERROR

    def test_stdio_missing_command(self):
        """stdio 模式缺 command"""
        c = McpServerConfig(name="test", transport="stdio")
        with pytest.raises(SkillMcpError) as exc:
            c.validate()
        assert exc.value.code == ErrorCode.MCP_PROTOCOL_ERROR

    def test_sse_missing_url(self):
        """sse 模式缺 url"""
        c = McpServerConfig(name="test", transport="sse")
        with pytest.raises(SkillMcpError) as exc:
            c.validate()
        assert exc.value.code == ErrorCode.MCP_PROTOCOL_ERROR


class TestCheckMcpSdk:
    """MCP SDK 探测"""

    def test_returns_bool(self):
        """返回布尔值"""
        result = _check_mcp_sdk()
        assert isinstance(result, bool)

    def test_returns_false_when_missing(self):
        """SDK 缺失时返回 False"""
        with patch.dict("sys.modules", {"mcp": None}):
            assert _check_mcp_sdk() is False


class TestToolToSkillDraft:
    """MCP tool → Skill 草稿转换"""

    def setup_method(self):
        self.adapter = object.__new__(McpSkillAdapter)
        self.config = McpServerConfig(
            name="test-server", transport="stdio", command="python",
        )

    def test_basic_conversion(self):
        """基本字段转换"""
        tool = {
            "name": "search",
            "description": "Search the web",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
        draft = self.adapter._tool_to_skill_draft(tool, self.config)

        assert draft["id"] == "mcp-test-server-search"
        assert draft["name"] == "search (MCP/test-server)"
        assert draft["description"] == "Search the web"
        assert draft["category"] == SkillCategory.MCP.value
        assert draft["default_params"]["mcp_tool_name"] == "search"
        assert draft["default_params"]["mcp_server"] == "test-server"
        assert draft["default_params"]["mcp_transport"] == "stdio"
        assert draft["source"] == "mcp:test-server"
        assert draft["config_schema"] == tool["inputSchema"]

    def test_id_sanitization(self):
        """tool name 含特殊字符 → kebab-case"""
        tool = {"name": "search.web!foo", "description": "test"}
        draft = self.adapter._tool_to_skill_draft(tool, self.config)
        assert "!" not in draft["id"]
        assert "." not in draft["id"]
        assert draft["id"] == "mcp-test-server-search-web-foo"

    def test_id_length_limit(self):
        """skill_id 不超过 64 字符"""
        tool = {"name": "a" * 100, "description": "test"}
        draft = self.adapter._tool_to_skill_draft(tool, self.config)
        assert len(draft["id"]) <= 64

    def test_empty_description(self):
        """description 为空时用默认值"""
        tool = {"name": "tool1", "description": ""}
        draft = self.adapter._tool_to_skill_draft(tool, self.config)
        assert "MCP tool tool1" in draft["description"]

    def test_input_schema_snake_case(self):
        """input_schema (snake_case) 也兼容"""
        tool = {
            "name": "t",
            "description": "d",
            "input_schema": {"type": "object"},
        }
        draft = self.adapter._tool_to_skill_draft(tool, self.config)
        assert draft["config_schema"] == {"type": "object"}


class TestExtractResult:
    """MCP CallToolResult 提取"""

    def test_extract_text_json(self):
        """TextContent 中的 JSON → 解析为 dict"""
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "content": [{"type": "text", "text": '{"count": 5}'}],
        }
        result = McpSkillAdapter._extract_result(mock_result)
        assert result == {"count": 5}

    def test_extract_text_plain(self):
        """TextContent 中的非 JSON → 原样返回"""
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "content": [{"type": "text", "text": "hello world"}],
        }
        result = McpSkillAdapter._extract_result(mock_result)
        assert result == {"text": "hello world"}

    def test_extract_no_text_content(self):
        """无 TextContent → 返回原始 data"""
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "content": [{"type": "image", "data": "base64..."}],
        }
        result = McpSkillAdapter._extract_result(mock_result)
        assert "content" in result


class TestDiscoverFromMcpServer:
    """discover_from_mcp_server"""

    def test_sdk_missing_raises(self):
        """MCP SDK 缺失 → SkillMcpError(MCP_SDK_UNAVAILABLE)"""
        adapter = McpSkillAdapter(skills_service=MagicMock())
        config = McpServerConfig(name="t", transport="stdio", command="python")

        with patch("agent.skills_mgmt.mcp_adapter._check_mcp_sdk",
                   return_value=False):
            with pytest.raises(SkillMcpError) as exc:
                adapter.discover_from_mcp_server(config)
        assert exc.value.code == ErrorCode.MCP_SDK_UNAVAILABLE


class TestInvokeMcpSkill:
    """invoke_mcp_skill"""

    def test_skill_not_found(self):
        """技能不存在 → SkillNotFoundError"""
        mock_svc = MagicMock()
        mock_svc.get.return_value = None
        adapter = McpSkillAdapter(skills_service=mock_svc)

        with pytest.raises(SkillNotFoundError):
            adapter.invoke_mcp_skill("nonexistent")

    def test_non_mcp_skill_raises(self):
        """非 MCP 类技能 → SkillMcpError"""
        mock_svc = MagicMock()
        mock_svc.get.return_value = Skill(
            id="custom-skill", name="Custom", category="custom",
        )
        adapter = McpSkillAdapter(skills_service=mock_svc)

        with pytest.raises(SkillMcpError) as exc:
            adapter.invoke_mcp_skill("custom-skill")
        assert exc.value.code == ErrorCode.MCP_PROTOCOL_ERROR

    def test_missing_tool_name_raises(self):
        """MCP 技能缺 mcp_tool_name → SkillMcpError(MCP_TOOL_NOT_FOUND)"""
        mock_svc = MagicMock()
        mock_svc.get.return_value = Skill(
            id="mcp-test", name="Test", category="mcp",
            default_params={},  # 无 mcp_tool_name
        )
        adapter = McpSkillAdapter(skills_service=mock_svc)

        with pytest.raises(SkillMcpError) as exc:
            adapter.invoke_mcp_skill("mcp-test")
        assert exc.value.code == ErrorCode.MCP_TOOL_NOT_FOUND


class TestConfigFromSkill:
    """_config_from_skill"""

    def test_restore_config(self):
        """从 skill 的 default_params 还原 McpServerConfig"""
        skill = Skill(
            id="mcp-foo", name="Foo", category="mcp",
            default_params={
                "mcp_server": "my-server",
                "mcp_transport": "sse",
            },
        )
        config = McpSkillAdapter._config_from_skill(skill)
        assert config.name == "my-server"
        assert config.transport == "sse"

    def test_default_transport(self):
        """default_params 缺 mcp_transport → 默认 stdio"""
        skill = Skill(
            id="mcp-foo", name="Foo", category="mcp",
            default_params={"mcp_server": "srv"},
        )
        config = McpSkillAdapter._config_from_skill(skill)
        assert config.transport == "stdio"
