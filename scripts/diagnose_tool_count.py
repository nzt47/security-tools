#!/usr/bin/env python3
"""
工具数量差异诊断脚本
比较 list_tools() 和 TOOL_CATEGORIES 中的工具数量差异
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import tools as _tools
from agent.tool_router import ALL_TOOLS_SET, TOOL_CATEGORIES


def diagnose_tool_count():
    """诊断工具数量差异"""
    print("=" * 80)
    print("工具数量差异诊断")
    print("=" * 80)
    
    # 获取 list_tools() 返回的工具
    all_registered_tools = _tools.list_tools()
    print(f"\nlist_tools() 返回的工具数: {len(all_registered_tools)}")
    
    # 获取 TOOL_CATEGORIES 中的工具
    categorized_tools = set()
    for cat_info in TOOL_CATEGORIES.values():
        for tool in cat_info.get("tools", []):
            categorized_tools.add(tool)
    
    print(f"TOOL_CATEGORIES 中的工具数: {len(categorized_tools)}")
    print(f"ALL_TOOLS_SET 中的工具数: {len(ALL_TOOLS_SET)}")
    
    # 计算差异
    registered_names = {t["name"] for t in all_registered_tools}
    difference = registered_names - categorized_tools
    
    print(f"\n差异工具数量: {len(difference)}")
    
    if difference:
        print(f"\n在 list_tools() 中但不在 TOOL_CATEGORIES 中的工具:")
        for tool_name in sorted(difference):
            # 查找工具来源
            tool_info = None
            for t in all_registered_tools:
                if t["name"] == tool_name:
                    tool_info = t
                    break
            print(f"  - {tool_name}")
            if tool_info:
                print(f"    描述: {tool_info.get('description', 'N/A')[:50]}...")
    
    # 按来源分组
    print("\n按来源分组:")
    from agent.tools import SOURCE_BUILTIN, SOURCE_MCP, SOURCE_PLUGIN
    
    sources = {
        "内置工具 (SOURCE_BUILTIN)": SOURCE_BUILTIN,
        "MCP 工具 (SOURCE_MCP)": SOURCE_MCP,
        "插件工具 (SOURCE_PLUGIN)": SOURCE_PLUGIN,
    }
    
    for source_name, source_id in sources.items():
        source_tools = _tools.list_tools_by_source(source_id)
        print(f"\n{source_name}: {len(source_tools)} 个")
        for t in source_tools[:5]:  # 只显示前5个
            print(f"  - {t['name']}")
        if len(source_tools) > 5:
            print(f"  ... 还有 {len(source_tools) - 5} 个")


def analyze_tool_classification():
    """分析工具分类"""
    print("\n" + "=" * 80)
    print("工具分类分析")
    print("=" * 80)
    
    all_tools = _tools.list_tools()
    registered_names = {t["name"] for t in all_tools}
    
    print(f"\n已注册工具总数: {len(registered_names)}")
    print(f"已分类工具总数: {len(ALL_TOOLS_SET)}")
    print(f"未分类工具数: {len(registered_names - ALL_TOOLS_SET)}")
    
    # 统计各类别工具数
    print("\n各类别工具统计:")
    for cat_key, cat_info in sorted(TOOL_CATEGORIES.items(), 
                                    key=lambda x: x[1].get("priority", 99)):
        tools = cat_info.get("tools", [])
        print(f"  {cat_info['icon']} {cat_key}: {len(tools)} 个")


def show_unclassified_tools():
    """显示未分类的工具"""
    print("\n" + "=" * 80)
    print("未分类工具列表")
    print("=" * 80)
    
    all_tools = _tools.list_tools()
    registered_names = {t["name"] for t in all_tools}
    
    unclassified = registered_names - ALL_TOOLS_SET
    
    if unclassified:
        print(f"\n未分类的工具 ({len(unclassified)} 个):")
        for tool_name in sorted(unclassified):
            # 查找工具信息
            tool_info = None
            for t in all_tools:
                if t["name"] == tool_name:
                    tool_info = t
                    break
            
            print(f"\n  名称: {tool_name}")
            if tool_info:
                print(f"  描述: {tool_info.get('description', 'N/A')}")


if __name__ == "__main__":
    diagnose_tool_count()
    analyze_tool_classification()
    show_unclassified_tools()
    
    print("\n" + "=" * 80)
    print("诊断完成!")
    print("=" * 80)
    print("""
结论:
- list_tools() 返回的是所有已注册的工具（包括 MCP 扩展和插件）
- TOOL_CATEGORIES 只包含内置工具的分类
- 工具集成页面显示的是 list_tools() 的结果
- 智能工具选择使用 TOOL_CATEGORIES 进行分类
""")