#!/usr/bin/env python3
"""
智能工具选择 - 复杂场景测试
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tool_router import (
    get_tools_for_input,
    classify_user_input,
    estimate_tool_tokens,
    ALL_TOOLS_SET,
    TOOL_ALIASES,
)


def test_complex_scenario():
    """测试复杂输入场景"""
    print("=" * 80)
    print("智能工具选择 - 复杂场景测试")
    print("=" * 80)
    
    # 复杂场景测试用例
    complex_scenarios = [
        # 场景1: 多类别触发 - 搜索+文件操作+命令执行
        {
            "name": "多类别复杂任务",
            "input": "帮我搜索最新的Python教程，下载下来，然后读取文件内容并执行分析",
            "expected_categories": ["core", "web", "file", "code"],
        },
        # 场景2: PDF处理
        {
            "name": "PDF处理任务",
            "input": "帮我读取PDF文件内容，提取关键信息",
            "expected_categories": ["core", "file", "pdf"],
        },
        # 场景3: 系统管理
        {
            "name": "系统管理任务",
            "input": "列出当前运行的进程，停止某个进程，然后打开记事本",
            "expected_categories": ["core", "system", "code"],
        },
        # 场景4: 软件安装
        {
            "name": "软件安装任务",
            "input": "搜索Python软件包，安装最新版本，然后查看已安装软件列表",
            "expected_categories": ["core", "software", "web"],
        },
        # 场景5: 定时任务
        {
            "name": "定时任务任务",
            "input": "创建一个每天定时执行的任务，然后查看所有定时任务",
            "expected_categories": ["core", "schedule", "code"],
        },
    ]
    
    print(f"\n工具总数: {len(ALL_TOOLS_SET)}")
    print(f"同类工具合并规则: {len(TOOL_ALIASES)} 组")
    print()
    
    for scenario in complex_scenarios:
        print("-" * 80)
        print(f"场景: {scenario['name']}")
        print(f"输入: {scenario['input']}")
        
        # 获取匹配类别
        categories = classify_user_input(scenario["input"])
        print(f"匹配类别: {categories}")
        
        # 启用详细日志运行工具选择
        tools = get_tools_for_input(scenario["input"], verbose=True)
        tokens = estimate_tool_tokens(tools)
        
        print(f"\n【场景总结】")
        print(f"  工具数量: {len(tools)} 个")
        print(f"  Token估算: ~{tokens} tokens")
        print(f"  占比: {len(tools)/len(ALL_TOOLS_SET):.1%}")
        print()


def test_optimization_effect():
    """测试优化效果对比"""
    print("\n" + "=" * 80)
    print("优化效果对比分析")
    print("=" * 80)
    
    test_cases = [
        ("执行命令", "测试命令执行场景"),
        ("读取PDF文件", "测试PDF读取场景"),
        ("列出目录", "测试目录列表场景"),
        ("安装软件", "测试软件安装场景"),
        ("搜索并读取文件", "测试复合场景"),
    ]
    
    print(f"\n{'场景':<15} {'工具数':<8} {'Token':<10} {'优化说明'}")
    print("-" * 60)
    
    for input_text, desc in test_cases:
        tools = get_tools_for_input(input_text)
        tokens = estimate_tool_tokens(tools)
        
        # 分析优化效果
        optimization_note = ""
        if "PDF" in input_text and "read_pdf" not in tools:
            optimization_note = "✓ read_pdf 被 read_file 合并"
        elif "命令" in input_text and "run_program" not in tools:
            optimization_note = "✓ run_program 被 shell_execute 合并"
        elif "目录" in input_text and "list_processes" not in tools:
            optimization_note = "✓ list_processes 被 list_directory 合并"
        
        print(f"{input_text:<15} {len(tools):<8} {tokens:<10} {optimization_note}")


def test_edge_cases():
    """测试边界情况"""
    print("\n" + "=" * 80)
    print("边界情况测试")
    print("=" * 80)
    
    edge_cases = [
        ("", "空输入"),
        ("帮助", "简单请求"),
        ("搜索搜索搜索", "重复关键词"),
        ("非常长的输入" * 10, "超长输入"),
        ("@#$%^&*()", "特殊字符"),
    ]
    
    for input_text, desc in edge_cases:
        tools = get_tools_for_input(input_text)
        print(f"输入: {desc} → 工具数: {len(tools)}")


if __name__ == "__main__":
    test_complex_scenario()
    test_optimization_effect()
    test_edge_cases()
    
    print("\n" + "=" * 80)
    print("✅ 所有测试完成!")
    print("=" * 80)
