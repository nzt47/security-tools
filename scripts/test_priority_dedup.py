#!/usr/bin/env python3
"""
优先级去重机制验证测试
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tool_router import (
    get_tools_for_input,
    classify_user_input,
    estimate_tool_tokens,
    ALL_TOOLS_SET,
    TOOL_CATEGORIES,
)


def test_priority_dedup():
    """测试优先级去重机制"""
    print("=" * 80)
    print("优先级去重机制验证测试")
    print("=" * 80)
    
    # 构造包含大量低优先级工具的复杂输入场景
    # 目的：触发多个类别，验证高优先级类别的工具优先被选中
    
    complex_scenarios = [
        # 场景1: 触发所有类别（极限测试）
        {
            "name": "极限多类别触发",
            "input": "搜索网页 读取文件 执行命令 进程管理 安装扩展 PDF处理 软件安装 异步任务 定时任务",
            "description": "触发所有10个类别，验证优先级排序",
        },
        # 场景2: 低优先级类别优先触发关键词
        {
            "name": "低优先级优先触发",
            "input": "安装扩展插件 查看扩展列表 安装软件 查看软件列表",
            "description": "extension(priority=5) 和 software(priority=7) 类别",
        },
        # 场景3: 高优先级与低优先级混合
        {
            "name": "高低优先级混合",
            "input": "执行shell命令 安装扩展插件 创建定时任务",
            "description": "code(priority=3), extension(priority=5), schedule(priority=9)",
        },
        # 场景4: 触发数量限制
        {
            "name": "数量限制触发",
            "input": "搜索网页 读取文件 执行命令 处理PDF 安装软件 创建定时任务 后台运行",
            "description": "触发超过25个工具，验证数量限制",
        },
    ]
    
    print(f"\n工具总数: {len(ALL_TOOLS_SET)}")
    print(f"类别总数: {len(TOOL_CATEGORIES)}")
    
    # 显示所有类别的优先级
    print("\n类别优先级表:")
    for cat_key, cat_info in sorted(TOOL_CATEGORIES.items(), key=lambda x: x[1].get('priority', 99)):
        priority = cat_info.get('priority', 99)
        always = " [始终发送]" if cat_info.get('always') else ""
        print(f"  [{priority:2d}] {cat_info['icon']} {cat_info['label']} ({cat_key}): {len(cat_info['tools'])} 个工具{always}")
    
    print()
    
    for scenario in complex_scenarios:
        print("-" * 80)
        print(f"场景: {scenario['name']}")
        print(f"描述: {scenario['description']}")
        print(f"输入: {scenario['input']}")
        
        # 获取匹配类别
        categories = classify_user_input(scenario["input"])
        print(f"\n匹配类别: {categories}")
        
        # 显示匹配类别的优先级
        cat_priorities = []
        for cat in categories:
            if cat in TOOL_CATEGORIES:
                priority = TOOL_CATEGORIES[cat].get('priority', 99)
                label = TOOL_CATEGORIES[cat]['label']
                cat_priorities.append((priority, cat, label))
        
        cat_priorities.sort()
        print("类别优先级排序:")
        for priority, cat, label in cat_priorities:
            print(f"  [{priority:2d}] {label} ({cat})")
        
        # 启用详细日志运行工具选择
        tools = get_tools_for_input(scenario["input"], verbose=True)
        tokens = estimate_tool_tokens(tools)
        
        print(f"\n【场景总结】")
        print(f"  工具数量: {len(tools)} 个")
        print(f"  Token估算: ~{tokens} tokens")
        print(f"  占比: {len(tools)/len(ALL_TOOLS_SET):.1%}")
        
        # 分析工具来源类别
        print("\n工具来源分析:")
        tool_sources = {}
        for cat_key, cat_info in TOOL_CATEGORIES.items():
            for tool in cat_info['tools']:
                if tool in tools:
                    if tool not in tool_sources:
                        tool_sources[tool] = cat_key
        
        source_counts = {}
        for tool, source in tool_sources.items():
            source_counts[source] = source_counts.get(source, 0) + 1
        
        for source, count in sorted(source_counts.items(), key=lambda x: TOOL_CATEGORIES.get(x[0], {}).get('priority', 99)):
            if source in TOOL_CATEGORIES:
                priority = TOOL_CATEGORIES[source].get('priority', 99)
                label = TOOL_CATEGORIES[source]['label']
                print(f"  [{priority:2d}] {label} ({source}): {count} 个工具")
        
        print()


def test_tool_overlap():
    """测试工具在不同类别中的重叠情况"""
    print("\n" + "=" * 80)
    print("工具重叠分析")
    print("=" * 80)
    
    # 找出所有工具在哪些类别中出现
    tool_appearance = {}
    for cat_key, cat_info in TOOL_CATEGORIES.items():
        for tool in cat_info['tools']:
            if tool not in tool_appearance:
                tool_appearance[tool] = []
            tool_appearance[tool].append((cat_key, cat_info.get('priority', 99)))
    
    # 找出在多个类别中出现的工具（理论上不应该有，因为每个工具只属于一个类别）
    overlapping_tools = {tool: cats for tool, cats in tool_appearance.items() if len(cats) > 1}
    
    if overlapping_tools:
        print("\n⚠️ 发现在多个类别中出现的工具:")
        for tool, cats in overlapping_tools.items():
            print(f"  {tool}:")
            for cat, priority in cats:
                print(f"    - {cat} (priority={priority})")
    else:
        print("\n✅ 所有工具都只属于一个类别，无重叠")
    
    # 统计每个类别的工具数量
    print("\n类别工具统计:")
    total_unique = len(tool_appearance)
    print(f"  唯一工具总数: {total_unique}")
    
    for cat_key, cat_info in sorted(TOOL_CATEGORIES.items(), key=lambda x: x[1].get('priority', 99)):
        priority = cat_info.get('priority', 99)
        print(f"  [{priority:2d}] {cat_info['label']}: {len(cat_info['tools'])} 个工具")


if __name__ == "__main__":
    test_priority_dedup()
    test_tool_overlap()
    
    print("\n" + "=" * 80)
    print("✅ 所有测试完成!")
    print("=" * 80)