"""D10 复现测试：工具匹配与参数抽取脆弱

缺陷（P1）：ToolRegistry.find_tool 中文关键词表极小且过宽（"将"、"写入"），
导致无关描述误匹配；_extract_params 硬编码中文句式正则，鲁棒性差。

预期失败：含"将"的无关描述不应匹配 write_file
→ 当前"将"被过度匹配 → 断言失败即复现成功。
"""
import pytest

from planning.executor import ToolRegistry


class TestDefectD10:
    """D10：find_tool 不应过度匹配宽泛中文关键词"""

    def test_find_tool_no_over_broad_chinese_match(self):
        registry = ToolRegistry()
        registry.register("write_file", lambda filename, content: "ok")

        # 目标行为："将"仅为普通连词，不含写入意图，不应触发 write_file 匹配
        assert registry.find_tool("请将这段文字展示给我") is None
