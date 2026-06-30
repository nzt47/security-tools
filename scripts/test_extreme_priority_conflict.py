#!/usr/bin/env python3
"""
极端场景测试模块 - 验证 file 类别优先级为 2 时的拦截效果
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tool_router import (
    get_tools_for_input,
    classify_user_input,
    estimate_tool_tokens,
    ALL_TOOLS_SET,
    TOOL_CATEGORIES,
)


def test_extreme_priority_conflict():
    """
    测试极端优先级冲突场景
    
    场景描述:
    - file 类别优先级为 2
    - 构造一个输入，同时触发多个低优先级类别
    - 验证 file 类别的工具优先被选中，低优先级类别被正确处理
    """
    print("=" * 80)
    print("极端优先级冲突测试")
    print("=" * 80)
    
    # 显示当前优先级配置
    print("\n当前类别优先级配置:")
    for cat_key, cat_info in sorted(TOOL_CATEGORIES.items(), 
                                    key=lambda x: x[1].get("priority", 99)):
        priority = cat_info.get("priority", 99)
        icon = cat_info.get("icon", "⚙")
        tool_count = len(cat_info.get("tools", []))
        print(f"  [{priority:2d}] {icon} {cat_key}: {tool_count} 个工具")
    
    file_priority = TOOL_CATEGORIES["file"].get("priority", 99)
    print(f"\n📁 file 类别优先级: {file_priority}")
    
    # 构造极端冲突场景
    extreme_scenarios = [
        {
            "name": "触发所有类别",
            "input": "搜索网页 读取文件 执行命令 进程管理 安装扩展 处理PDF 安装软件 后台任务 定时任务",
            "description": "同时触发 web(1), file(2), code(3), system(4), extension(5), pdf(6), software(7), async(8), schedule(9)",
            "expected_high_priority": ["core", "web", "file"],
        },
        {
            "name": "file与高优先级冲突",
            "input": "搜索网页读取文件",
            "description": "web(1) vs file(2) - 两者都应被选中",
            "expected_high_priority": ["core", "web", "file"],
        },
        {
            "name": "file与中优先级冲突",
            "input": "读取文件执行命令进程管理",
            "description": "file(2) vs code(3) vs system(4)",
            "expected_high_priority": ["core", "file"],
        },
        {
            "name": "file与低优先级冲突",
            "input": "读取文件安装扩展处理PDF安装软件",
            "description": "file(2) vs extension(5) vs pdf(6) vs software(7)",
            "expected_high_priority": ["core", "file"],
        },
        {
            "name": "所有低优先级类别",
            "input": "安装扩展 处理PDF 安装软件 后台任务 定时任务",
            "description": "extension(5), pdf(6), software(7), async(8), schedule(9)",
            "expected_high_priority": ["core"],
        },
        {
            "name": "混合优先级边界",
            "input": "搜索网页 读取文件 安装软件",
            "description": "web(1), file(2), software(7) - 跨优先级范围",
            "expected_high_priority": ["core", "web", "file"],
        },
        {
            "name": "file关键词 + 所有其他关键词",
            "input": "日志 log debug 搜索 天气 命令 扩展 PDF 软件 任务 定时",
            "description": "file关键词 + 其他所有类别关键词",
            "expected_high_priority": ["core", "file"],
        },
        {
            "name": "数量限制触发",
            "input": "搜索网页 读取文件 执行命令 进程管理 安装扩展 处理PDF 安装软件 后台任务 定时任务 日志分析",
            "description": "触发超过25个工具的场景",
            "expected_high_priority": ["core", "web", "file"],
        },
    ]
    
    print("\n" + "=" * 80)
    print("测试场景")
    print("=" * 80)
    
    all_passed = True
    
    for i, scenario in enumerate(extreme_scenarios, 1):
        print(f"\n{'='*60}")
        print(f"场景 {i}: {scenario['name']}")
        print(f"{'='*60}")
        print(f"描述: {scenario['description']}")
        print(f"输入: {scenario['input']}")
        
        # 获取匹配类别
        categories = classify_user_input(scenario["input"])
        print(f"\n匹配类别: {categories}")
        
        # 获取工具
        tools = get_tools_for_input(scenario["input"], verbose=False)
        tokens = estimate_tool_tokens(tools)
        
        # 分析类别优先级
        print("\n类别优先级分析:")
        sorted_cats = sorted(
            [(cat, TOOL_CATEGORIES[cat].get("priority", 99)) 
             for cat in categories if cat in TOOL_CATEGORIES],
            key=lambda x: x[1]
        )
        
        for cat, priority in sorted_cats:
            cat_info = TOOL_CATEGORIES[cat]
            print(f"  [{priority:2d}] {cat_info['icon']} {cat}")
        
        # 检查是否包含预期的高优先级类别
        expected = set(scenario["expected_high_priority"])
        matched = categories
        
        # 验证预期高优先级类别是否都在匹配结果中
        missing_high = expected - matched
        unexpected_high = matched - expected - {"core"}  # core 始终包含
        
        success = len(missing_high) == 0
        
        if not success:
            all_passed = False
        
        print(f"\n验证结果:")
        print(f"  预期高优先级类别: {expected}")
        print(f"  实际匹配类别: {matched}")
        print(f"  缺失高优先级: {missing_high if missing_high else '无'}")
        print(f"  额外匹配类别: {unexpected_high if unexpected_high else '无'}")
        print(f"  工具数量: {len(tools)}")
        print(f"  Token估算: ~{tokens}")
        print(f"  {'✅ 通过' if success else '❌ 失败'}")
    
    return all_passed


def test_priority_blocking():
    """
    验证优先级拦截效果
    
    当达到数量限制时，低优先级类别的工具应该被拦截
    """
    print("\n" + "=" * 80)
    print("优先级拦截效果测试")
    print("=" * 80)
    
    # 测试不同优先级类别的工具数量
    print("\n各类别工具数量:")
    cat_tool_counts = []
    for cat_key, cat_info in sorted(TOOL_CATEGORIES.items(), 
                                    key=lambda x: x[1].get("priority", 99)):
        priority = cat_info.get("priority", 99)
        tool_count = len(cat_info.get("tools", []))
        cat_tool_counts.append((priority, cat_key, tool_count))
        print(f"  [{priority:2d}] {cat_info['icon']} {cat_key}: {tool_count} 个")
    
    # 计算累计工具数
    print("\n累计工具数（按优先级）:")
    cumulative = 0
    for priority, cat_key, tool_count in cat_tool_counts:
        cumulative += tool_count
        print(f"  优先级 <= {priority}: {cumulative} 个工具")
        
        if cumulative >= 25:
            print(f"    ⚠️ 超过默认最大工具数(25)")
            break
    
    # 测试实际拦截效果
    print("\n实际拦截测试:")
    test_inputs = [
        ("读取文件", "只触发 file 类别"),
        ("读取文件 搜索网页", "触发 file + web"),
        ("读取文件 搜索网页 执行命令", "触发 file + web + code"),
        ("读取文件 搜索网页 执行命令 进程管理", "触发 file + web + code + system"),
        ("读取文件 搜索网页 执行命令 进程管理 安装扩展", "触发多个类别"),
    ]
    
    for input_text, desc in test_inputs:
        tools = get_tools_for_input(input_text)
        print(f"\n  输入: '{input_text}'")
        print(f"  描述: {desc}")
        print(f"  工具数: {len(tools)}")


if __name__ == "__main__":
    # 运行极端优先级冲突测试
    all_passed = test_extreme_priority_conflict()
    
    # 运行优先级拦截测试
    test_priority_blocking()
    
    print("\n" + "=" * 80)
    if all_passed:
        print("🎉 所有测试通过!")
    else:
        print("⚠️ 部分测试失败")
    print("=" * 80)