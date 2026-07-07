"""agentskills.io 标准兼容 — 双向适配单元测试

覆盖:
    - to_agentskills_io 正向转换(字段映射 + _yunshu 命名空间)
    - from_agentskills_io 反向转换(字段还原)
    - 双向往返一致性
    - 枚举值清洗
    - 边界情况(无 front matter / 空 text)
"""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.skills_mgmt.models import Skill, SkillCategory, ContentType
from agent.skills_mgmt.file_store import SkillMDParser
from agent.skills_mgmt.exceptions import SkillFileError, ErrorCode


class TestToAgentskillsIo:
    """正向转换: 云枢 Skill → agentskills.io"""

    def test_basic_conversion(self):
        """基本字段映射正确"""
        s = Skill(
            id="pdf-counter",
            name="PDF计数器",
            description="统计PDF页数",
            version="1.2.0",
            author="yunshu",
            tags=["pdf", "counter"],
        )
        text = SkillMDParser.to_agentskills_io(s.to_storage_dict())

        # 验证 front matter 包含标准字段
        assert "name: pdf-counter" in text
        assert "title: PDF计数器" in text
        assert "description: 统计PDF页数" in text
        assert "version: 1.2.0" in text
        assert "author: yunshu" in text
        assert "license: MIT" in text  # 默认补的

    def test_yunshu_namespace(self):
        """云枢特有字段写入 _yunshu 命名空间"""
        s = Skill(
            id="test",
            name="Test",
            category="custom",
            content_type="markdown",
            output_schema={"type": "object"},
            default_params={"x": 1},
        )
        text = SkillMDParser.to_agentskills_io(s.to_storage_dict())

        # _yunshu 子对象存在
        assert "_yunshu:" in text
        assert "category: custom" in text
        assert "output_schema:" in text

    def test_license_default(self):
        """无 license 时默认补 MIT"""
        s = Skill(id="t", name="T")
        text = SkillMDParser.to_agentskills_io(s.to_storage_dict())
        assert "license: MIT" in text

    def test_empty_fields_skipped(self):
        """空字段不写入 front matter"""
        s = Skill(id="t", name="T", description="", tags=[])
        text = SkillMDParser.to_agentskills_io(s.to_storage_dict())
        assert "description:" not in text
        assert "tags:" not in text

    def test_body_preserved(self):
        """body(使用说明)正确保留"""
        s = Skill(id="t", name="T", content="# 使用说明\n正文")
        text = SkillMDParser.to_agentskills_io(s.to_storage_dict())
        assert "# 使用说明" in text
        assert "正文" in text

    def test_custom_body_override(self):
        """传入 body 参数覆盖 content"""
        s = Skill(id="t", name="T", content="原始内容")
        text = SkillMDParser.to_agentskills_io(
            s.to_storage_dict(), body="自定义body",
        )
        assert "自定义body" in text
        assert "原始内容" not in text

    def test_enum_coercion(self):
        """枚举值自动转为 .value"""
        s = Skill(
            id="t", name="T",
            category=SkillCategory.MCP,
            content_type=ContentType.JSON,
        )
        # 不应抛 RepresenterError
        text = SkillMDParser.to_agentskills_io(s.to_storage_dict())
        assert "category: mcp" in text
        assert "content_type: json" in text


class TestFromAgentskillsIo:
    """反向转换: agentskills.io → 云枢 Skill"""

    def test_basic_parse(self):
        """基本字段反向映射"""
        text = """---
name: my-skill
description: A test skill
version: 2.0.0
author: external
tags:
- foo
- bar
license: Apache-2.0
---

# 使用说明
"""
        data = SkillMDParser.from_agentskills_io(text)

        assert data["id"] == "my-skill"  # name → id
        assert data["description"] == "A test skill"
        assert data["version"] == "2.0.0"
        assert data["author"] == "external"
        assert data["tags"] == ["foo", "bar"]
        assert "# 使用说明" in data["content"]

    def test_yunshu_namespace_restore(self):
        """_yunshu 命名空间字段还原"""
        text = """---
name: test
description: test
_yunshu:
  category: mcp
  output_schema:
    type: object
    required:
    - count
  default_params:
    timeout: 30
---

body
"""
        data = SkillMDParser.from_agentskills_io(text)

        assert data["category"] == "mcp"
        assert data["output_schema"] == {"type": "object", "required": ["count"]}
        assert data["default_params"] == {"timeout": 30}

    def test_defaults_filled(self):
        """缺失字段补默认值"""
        text = """---
name: bare
description: minimal
---

body
"""
        data = SkillMDParser.from_agentskills_io(text)

        assert data["category"] == "community"  # 默认
        assert data["content_type"] == "markdown"  # 默认
        assert data["version"] == "0.1.0"  # 默认

    def test_no_front_matter(self):
        """无 front matter → 整体视为 body"""
        text = "这是纯文本,没有 front matter"
        data = SkillMDParser.from_agentskills_io(text)
        assert data == {"content": text}

    def test_empty_text(self):
        """空文本 → 空 dict"""
        assert SkillMDParser.from_agentskills_io("") == {}

    def test_unclosed_front_matter(self):
        """front matter 未闭合 → SkillFileError"""
        text = "---\nname: test\nno closing"
        with pytest.raises(SkillFileError) as exc_info:
            SkillMDParser.from_agentskills_io(text)
        assert exc_info.value.code == ErrorCode.MD_NO_FRONTMATTER


class TestRoundTrip:
    """双向往返一致性"""

    def test_full_round_trip(self):
        """完整往返: 云枢 → agentskills.io → 云枢"""
        original = Skill(
            id="pdf-page-counter",
            name="PDF页数统计",
            description="统计 PDF 总页数",
            category="custom",
            tags=["pdf", "parse"],
            version="1.0.0",
            author="yunshu",
            content="# 使用说明\n脚本输出 JSON。",
            output_schema={"type": "object", "required": ["page_count"]},
            default_params={"file_path": ""},
        )

        # 导出
        text = SkillMDParser.to_agentskills_io(original.to_storage_dict())
        # 导入
        data = SkillMDParser.from_agentskills_io(text)
        restored = Skill.from_storage_dict(data)

        # 验证字段一致
        assert original.id == restored.id
        assert original.name == restored.name
        assert original.description == restored.description
        assert original.version == restored.version
        assert original.author == restored.author
        assert original.tags == restored.tags
        assert original.category == restored.category
        assert original.output_schema == restored.output_schema
        assert original.default_params == restored.default_params
        assert original.content == restored.content

    def test_round_trip_mcp_category(self):
        """MCP 类别技能往返"""
        original = Skill(
            id="mcp-foo-bar",
            name="Foo Bar (MCP/test)",
            category="mcp",
            default_params={"mcp_tool_name": "bar", "mcp_server": "test"},
        )
        text = SkillMDParser.to_agentskills_io(original.to_storage_dict())
        data = SkillMDParser.from_agentskills_io(text)
        restored = Skill.from_storage_dict(data)

        assert original.id == restored.id
        assert original.category == restored.category
        assert original.default_params == restored.default_params
