"""tool_schema_pruner 单元测试

覆盖范围:
- deprecated optional 移除、required 保留
- 工具级 deprecated → 整工具移除
- description 截断(SCHEMA_DESC_MAX_LEN)
- additionalProperties 移除、enum 约束保留
- 深拷贝不修改原对象、异常降级、批量裁剪
- verbose fixture ≥30% 减幅
"""
import json
import copy
from unittest.mock import patch
import pytest

from agent.tool_schema_pruner import (
    prune_schema,
    prune_tool_defs,
    SCHEMA_DESC_MAX_LEN,
    SCHEMA_PROP_DESC_MAX_LEN,
    SCHEMA_PRUNE_ADDITIONAL_PROPS,
)


def _make_tool(name="test_tool", desc="测试工具", params=None, tool_deprecated=False):
    func = {"name": name, "description": desc, "parameters": params or {
        "type": "object",
        "required": ["required_field"],
        "properties": {
            "required_field": {"type": "string", "description": "必填字段"},
        },
    }}
    if tool_deprecated:
        func["deprecated"] = True
    return {"type": "function", "function": func}


class TestDeprecatedRemoval:
    """deprecated 字段移除"""

    def test_deprecated_optional_removed(self):
        td = _make_tool(params={
            "type": "object", "required": ["keep"],
            "properties": {
                "keep": {"type": "string"},
                "old_field": {"type": "string", "deprecated": True},
            },
        })
        result = prune_schema(td)
        props = result["function"]["parameters"]["properties"]
        assert "old_field" not in props
        assert "keep" in props

    def test_required_field_kept_even_if_deprecated(self):
        """守 [不易]: required 即便 deprecated:true 也保留"""
        td = _make_tool(params={
            "type": "object", "required": ["must_have"],
            "properties": {
                "must_have": {"type": "string", "deprecated": True},
            },
        })
        result = prune_schema(td)
        props = result["function"]["parameters"]["properties"]
        assert "must_have" in props

    def test_nested_deprecated_removed(self):
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {
                        "inner_dep": {"type": "string", "deprecated": True},
                        "inner_keep": {"type": "string"},
                    },
                },
            },
        })
        result = prune_schema(td)
        inner = result["function"]["parameters"]["properties"]["nested"]["properties"]
        assert "inner_dep" not in inner
        assert "inner_keep" in inner

    def test_tool_level_deprecated_returns_none(self):
        td = _make_tool(tool_deprecated=True)
        assert prune_schema(td) is None

    def test_array_items_deprecated_removed(self):
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {
                "list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_dep": {"type": "string", "deprecated": True},
                            "item_keep": {"type": "string"},
                        },
                    },
                },
            },
        })
        result = prune_schema(td)
        items = result["function"]["parameters"]["properties"]["list"]["items"]["properties"]
        assert "item_dep" not in items
        assert "item_keep" in items


class TestDescriptionTruncation:
    """description 截断"""

    def test_long_description_truncated(self):
        long_desc = "长" * (SCHEMA_DESC_MAX_LEN + 100)
        td = _make_tool(desc=long_desc)
        result = prune_schema(td)
        assert len(result["function"]["description"]) <= SCHEMA_DESC_MAX_LEN + 3
        assert result["function"]["description"].endswith("...")

    def test_short_description_not_truncated(self):
        td = _make_tool(desc="短描述")
        result = prune_schema(td)
        assert result["function"]["description"] == "短描述"

    def test_long_prop_description_truncated(self):
        long_prop = "长" * (SCHEMA_PROP_DESC_MAX_LEN + 100)
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {"p": {"type": "string", "description": long_prop}},
        })
        result = prune_schema(td)
        assert len(result["function"]["parameters"]["properties"]["p"]["description"]) <= SCHEMA_PROP_DESC_MAX_LEN + 3


class TestAdditionalProperties:
    """冗余字段移除"""

    def test_additional_props_true_removed(self):
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {"a": {"type": "string"}},
            "additionalProperties": True,
        })
        result = prune_schema(td)
        assert "additionalProperties" not in result["function"]["parameters"]

    def test_enum_constraints_kept(self):
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {
                "mode": {"type": "string", "enum": ["a", "b", "c"], "default": "a"},
            },
        })
        result = prune_schema(td)
        mode = result["function"]["parameters"]["properties"]["mode"]
        assert mode["enum"] == ["a", "b", "c"]
        assert mode["default"] == "a"


class TestSafety:
    """安全与降级"""

    def test_deepcopy_not_modify_original(self):
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {
                "old": {"type": "string", "deprecated": True},
                "keep": {"type": "string"},
            },
        })
        orig = copy.deepcopy(td)
        prune_schema(td)
        assert td == orig

    def test_non_dict_returns_as_is(self):
        assert prune_schema("string") == "string"
        assert prune_schema(None) is None

    def test_missing_function_returns_as_is(self):
        assert prune_schema({"type": "function"}) == {"type": "function"}

    def test_exception_falls_back_to_original(self):
        """裁剪异常 → 返回原 tool_def"""
        class Boom:
            pass
        boom_td = Boom()
        with patch("agent.tool_schema_pruner.copy.deepcopy", side_effect=RuntimeError("boom")):
            result = prune_schema(boom_td)
        assert result is boom_td

    def test_prune_disabled_keeps_deprecated(self):
        td = _make_tool(params={
            "type": "object", "required": [],
            "properties": {"old": {"type": "string", "deprecated": True}},
        })
        with patch("agent.tool_schema_pruner.SCHEMA_PRUNE_DEPRECATED", False):
            result = prune_schema(td)
        props = result["function"]["parameters"]["properties"]
        assert "old" in props


class TestBatch:
    """批量裁剪"""

    def test_prune_tool_defs_filters_deprecated_tools(self):
        defs = [
            _make_tool(name="a"),
            _make_tool(name="b", tool_deprecated=True),
            _make_tool(name="c"),
        ]
        result = prune_tool_defs(defs)
        names = [t["function"]["name"] for t in result]
        assert names == ["a", "c"]

    def test_prune_tool_defs_non_list(self):
        assert prune_tool_defs("not-list") == "not-list"

    def test_verbose_fixture_reduction_over_30_percent(self):
        """verbose fixture: 超长 description + deprecated → 减幅 ≥ 30%(验收)"""
        verbose_fixture = [{
            "type": "function",
            "function": {
                "name": "verbose_tool",
                "description": ("超长工具描述" * 50) + "…",
                "parameters": {
                    "type": "object", "required": ["core"],
                    "properties": {
                        "core": {"type": "string", "description": "核心参数"},
                        "old_a": {"type": "string", "deprecated": True, "description": "旧A"},
                        "old_b": {"type": "integer", "deprecated": True, "description": "旧B"},
                    },
                    "additionalProperties": True,
                },
            },
        }, {
            "type": "function",
            "function": {"name": "legacy", "deprecated": True, "description": "旧工具"},
        }]
        orig_len = len(json.dumps(verbose_fixture, ensure_ascii=False))
        pruned = prune_tool_defs(verbose_fixture)
        pruned_len = len(json.dumps(pruned, ensure_ascii=False))
        reduction = (orig_len - pruned_len) / orig_len * 100
        assert reduction >= 30, "verbose fixture 减幅应 ≥30%%,实际 %.2f%%" % reduction
