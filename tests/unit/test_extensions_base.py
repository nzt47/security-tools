"""Extensions Base 单元测试"""
import pytest
from datetime import datetime

from agent.extensions.base import (
    ExtensionType,
    ExtensionStatus,
    ExtensionMetadata,
    BUILTIN_EXTENSIONS,
)


class TestExtensionType:
    """测试扩展类型枚举"""

    def test_extension_type_values(self):
        """测试扩展类型值"""
        assert ExtensionType.SKILL.value == "skill"
        assert ExtensionType.CLAUDE_SKILL.value == "claude_skill"
        assert ExtensionType.MCP.value == "mcp"
        assert ExtensionType.CHANNEL.value == "channel"
        assert ExtensionType.PLUGIN.value == "plugin"


class TestExtensionStatus:
    """测试扩展状态枚举"""

    def test_extension_status_values(self):
        """测试状态值"""
        assert ExtensionStatus.PENDING.value == "pending"
        assert ExtensionStatus.INSTALLING.value == "installing"
        assert ExtensionStatus.INSTALLED.value == "installed"
        assert ExtensionStatus.ENABLED.value == "enabled"
        assert ExtensionStatus.DISABLED.value == "disabled"
        assert ExtensionStatus.ERROR.value == "error"


class TestExtensionMetadata:
    """测试扩展元数据"""

    def test_extension_metadata_creation(self):
        """测试元数据创建"""
        metadata = ExtensionMetadata(
            ext_id="test_extension",
            ext_type=ExtensionType.SKILL,
            name="Test Extension",
            version="1.0.0",
            description="A test extension",
            author="Test Author",
            homepage="https://example.com",
            license="MIT"
        )
        
        assert metadata.ext_id == "test_extension"
        assert metadata.name == "Test Extension"
        assert metadata.ext_type == ExtensionType.SKILL
        assert metadata.version == "1.0.0"

    def test_extension_metadata_to_dict(self):
        """测试转换为字典"""
        metadata = ExtensionMetadata(
            ext_id="test",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0",
            description="Test description"
        )
        
        d = metadata.to_dict()
        
        assert d["ext_id"] == "test"
        assert d["name"] == "Test"
        assert d["ext_type"] == "skill"
        assert d["version"] == "1.0.0"

    def test_extension_metadata_from_dict(self):
        """测试从字典创建"""
        data = {
            "ext_id": "test",
            "name": "Test",
            "ext_type": "skill",
            "version": "1.0.0",
            "description": "Test",
            "author": "Author",
            "status": "installed"
        }
        
        metadata = ExtensionMetadata.from_dict(data)
        
        assert metadata.ext_id == "test"
        assert metadata.ext_type == ExtensionType.SKILL
        assert metadata.author == "Author"
        assert metadata.status == ExtensionStatus.INSTALLED

    def test_extension_metadata_touch(self):
        """测试更新时间戳"""
        metadata = ExtensionMetadata(
            ext_id="test",
            ext_type=ExtensionType.SKILL,
            name="Test"
        )
        
        metadata.touch()
        
        assert metadata.created_at is not ""
        assert metadata.updated_at is not ""


class TestBuiltinExtensions:
    """测试内置扩展注册表"""

    def test_builtin_extensions_structure(self):
        """测试内置扩展结构"""
        assert "skill" in BUILTIN_EXTENSIONS
        assert "mcp" in BUILTIN_EXTENSIONS
        assert "channel" in BUILTIN_EXTENSIONS
        assert "plugin" in BUILTIN_EXTENSIONS

    def test_builtin_skills(self):
        """测试内置技能"""
        skills = BUILTIN_EXTENSIONS["skill"]
        
        assert len(skills) > 0
        
        skill_ids = [s["id"] for s in skills]
        assert "self_reflection" in skill_ids
        assert "memory_summary" in skill_ids
        assert "emotion_expression" in skill_ids

    def test_builtin_mcp(self):
        """测试内置 MCP"""
        mcps = BUILTIN_EXTENSIONS["mcp"]
        
        assert len(mcps) > 0
        
        mcp_ids = [m["id"] for m in mcps]
        assert "filesystem" in mcp_ids
        assert "github" in mcp_ids