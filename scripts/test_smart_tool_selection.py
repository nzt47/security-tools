#!/usr/bin/env python3
"""
身份提示词配置面板 - 智能工具选择功能测试（含优化对比）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.system_prompt_config import get_manager, get_registry_meta, get_token_estimate
from agent.tool_router import (
    classify_user_input, 
    get_tools_for_input, 
    get_categorized_tools,
    get_keywords,
    ALL_TOOLS_SET,
    TOOL_CATEGORIES,
    TOOL_ALIASES,
    get_tool_alias_info,
    estimate_tool_tokens
)


def test_config_manager():
    """测试配置管理器"""
    print("=" * 70)
    print("1. 测试配置管理器")
    print("=" * 70)
    
    mgr = get_manager()
    config = mgr.get_config_with_stats()
    
    print(f"✓ 配置版本: {config.get('version')}")
    print(f"✓ 组件总数: {len(config.get('sections', {}))}")
    
    smart_tool_config = config.get('sections', {}).get('smart_tool_selection', {})
    print(f"\n智能工具选择配置:")
    print(f"  - 启用状态: {'✅ 已启用' if smart_tool_config.get('enabled') else '❌ 未启用'}")
    print(f"  - 描述: {smart_tool_config.get('description', 'N/A')}")
    
    print(f"\n所有组件状态概览:")
    for key, stats in config.get('stats', {}).items():
        status = "✅" if stats.get('enabled') else "❌"
        print(f"  {status} {key}: {stats.get('tokens', 0)} tokens")


def test_tool_router_keywords():
    """测试工具路由关键词"""
    print("\n" + "=" * 70)
    print("2. 测试工具路由关键词")
    print("=" * 70)
    
    keywords = get_keywords()
    print(f"✓ 关键词类别数: {len(keywords)}")
    
    for cat, kw_list in keywords.items():
        print(f"\n  {cat}: {len(kw_list)} 个关键词")
        print(f"    示例: {kw_list[:5]}")


def test_tool_classification():
    """测试工具分类功能"""
    print("\n" + "=" * 70)
    print("3. 测试工具分类功能")
    print("=" * 70)
    
    test_inputs = [
        "帮我搜索一下天气",
        "读取文件内容",
        "执行shell命令",
        "安装软件",
        "帮我写一段代码",
        "创建定时任务",
        "搜索记忆",
        "普通聊天",
    ]
    
    for user_input in test_inputs:
        categories = classify_user_input(user_input)
        tools = get_tools_for_input(user_input)
        tokens = estimate_tool_tokens(tools)
        print(f"\n输入: {user_input}")
        print(f"  匹配类别: {categories}")
        print(f"  选择工具: {len(tools)} 个 (约 {tokens} tokens)")


def test_optimization_effect():
    """测试优化效果对比"""
    print("\n" + "=" * 70)
    print("4. 优化效果对比分析")
    print("=" * 70)
    
    print(f"工具总数: {len(ALL_TOOLS_SET)}")
    print(f"同类工具合并规则数: {len(TOOL_ALIASES)}")
    
    alias_info = get_tool_alias_info()
    print("\n同类工具合并映射:")
    for main, aliases in alias_info['aliases'].items():
        print(f"  {main} → [{', '.join(aliases)}]")
    
    test_cases = [
        ("搜索天气", ["core", "web", "system"]),
        ("读取文件", ["core", "file"]),
        ("执行命令", ["core", "code", "system"]),
        ("安装软件包", ["core", "software"]),
        ("处理PDF文件", ["core", "file", "pdf"]),
        ("创建定时任务", ["core", "schedule"]),
        ("后台运行任务", ["core", "code", "async"]),
        ("安装扩展插件", ["core", "extension"]),
        ("列出目录内容", ["core", "file"]),
    ]
    
    print("\n" + "=" * 50)
    print("优化前后对比表")
    print("=" * 50)
    print(f"{'输入场景':<12} {'匹配类别':<20} {'工具数':<6} {'Token估算':<10}")
    print("-" * 50)
    
    for input_text, expected_cats in test_cases:
        categories = classify_user_input(input_text)
        tools = get_tools_for_input(input_text)
        tokens = estimate_tool_tokens(tools)
        
        print(f"{input_text:<12} {str(categories)[:18] + '..':<20} {len(tools):<6} {tokens:<10}")


def test_categorized_tools():
    """测试分类工具列表"""
    print("\n" + "=" * 70)
    print("5. 测试分类工具列表")
    print("=" * 70)
    
    categorized = get_categorized_tools()
    total_tools = sum(len(cat['tools']) for cat in categorized)
    
    print(f"✓ 类别数: {len(categorized)}")
    print(f"✓ 工具总数: {total_tools}")
    
    print("\n类别详情（含优先级）:")
    for cat in categorized:
        priority = cat.get('priority', 99)
        always = " [始终发送]" if cat.get('always') else ""
        print(f"  [{priority}] {cat['icon']} {cat['label']}{always}: {len(cat['tools'])} 个工具")


def test_max_tools_limit():
    """测试最大工具数量限制"""
    print("\n" + "=" * 70)
    print("6. 测试最大工具数量限制")
    print("=" * 70)
    
    test_input = "帮我搜索并读取文件然后执行命令安装软件"
    
    for limit in [10, 15, 20, 25, 30]:
        tools = get_tools_for_input(test_input, max_tools=limit)
        tokens = estimate_tool_tokens(tools)
        print(f"  max_tools={limit:2d} → 选中 {len(tools):2d} 个工具 (约 {tokens} tokens)")


def test_alias_merging():
    """测试同类工具合并效果"""
    print("\n" + "=" * 70)
    print("7. 测试同类工具合并效果")
    print("=" * 70)
    
    test_cases = [
        ("执行shell命令", "shell_execute", "run_program"),
        ("读取pdf文件", "read_file", "read_pdf"),
        ("列出目录", "list_directory", "list_processes"),
    ]
    
    for input_text, main_tool, alias_tool in test_cases:
        tools = get_tools_for_input(input_text)
        has_main = main_tool in tools
        has_alias = alias_tool in tools
        
        status_main = "✅" if has_main else "❌"
        status_alias = "✅" if has_alias else "❌"
        merged = "✓" if has_main and not has_alias else "⚠️"
        
        print(f"\n输入: {input_text}")
        print(f"  主工具 {main_tool}: {status_main}")
        print(f"  别名工具 {alias_tool}: {status_alias}")
        print(f"  合并效果: {merged} (主工具保留，别名被过滤)")


def main():
    """主测试函数"""
    print("=" * 70)
    print("身份提示词配置面板 - 智能工具选择功能测试（优化版）")
    print("=" * 70)
    
    try:
        test_config_manager()
        test_tool_router_keywords()
        test_tool_classification()
        test_optimization_effect()
        test_categorized_tools()
        test_max_tools_limit()
        test_alias_merging()
        
        print("\n" + "=" * 70)
        print("✅ 所有测试完成!")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())