#!/usr/bin/env python3
"""
全量工具扫描测试 - 验证关键词配置更新后的效果
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
    get_keywords,
)


def test_log_keywords():
    """测试日志关键词配置"""
    print("=" * 80)
    print("全量工具扫描测试 - 验证日志关键词配置")
    print("=" * 80)
    
    print(f"\n工具总数: {len(ALL_TOOLS_SET)}")
    print(f"类别总数: {len(TOOL_CATEGORIES)}")
    
    # 检查 file 类别的关键词
    keywords = get_keywords()
    file_keywords = keywords.get("file", [])
    
    print("\nfile 类别关键词:")
    print(f"  关键词数量: {len(file_keywords)}")
    print(f"  关键词列表: {file_keywords}")
    
    # 检查日志相关关键词是否已添加
    log_keywords = ["日志", "log", "logs", "debug"]
    print("\n日志相关关键词检查:")
    for kw in log_keywords:
        status = "✅" if kw in file_keywords else "❌"
        print(f"  {status} {kw}")
    
    # 测试日志相关输入
    test_inputs = [
        "分析日志文件",
        "帮我分析 log 文件",
        "查找错误日志",
        "debug 日志分析",
        "读取日志并分析",
        "查看系统日志",
        "检查日志文件",
        "日志文件在哪里",
    ]
    
    print("\n" + "=" * 80)
    print("日志相关输入测试")
    print("=" * 80)
    
    print(f"\n测试输入列表:")
    for user_input in test_inputs:
        categories = classify_user_input(user_input)
        tools = get_tools_for_input(user_input)
        
        is_file_category = "file" in categories
        
        status = "✅" if is_file_category else "❌"
        print(f"\n{status} 输入: '{user_input}'")
        print(f"  匹配类别: {categories}")
        print(f"  file 类别: {'✅' if is_file_category else '❌'}")
        print(f"  选中工具数: {len(tools)}")
    
    return file_keywords


def test_all_categories():
    """测试所有类别的关键词"""
    print("\n" + "=" * 80)
    print("所有类别关键词扫描")
    print("=" * 80)
    
    keywords = get_keywords()
    
    total_keywords = 0
    for category, kw_list in keywords.items():
        if category in TOOL_CATEGORIES:
            total_keywords += len(kw_list)
            print(f"\n{TOOL_CATEGORIES[category]['icon']} {category} ({len(kw_list)} 个关键词):")
            print(f"  {kw_list}")
        else:
            print(f"\n⚠️ {category} 类别不存在于 TOOL_CATEGORIES")
    
    print(f"\n关键词总数: {total_keywords}")


def test_analyze_logs_tool():
    """测试 analyze_logs 工具识别"""
    print("\n" + "=" * 80)
    print("analyze_logs 工具识别测试")
    print("=" * 80)
    
    # 模拟添加 analyze_logs 工具
    TOOL_CATEGORIES["file"]["tools"].append("analyze_logs")
    ALL_TOOLS_SET.add("analyze_logs")
    
    print(f"\nfile 类别工具数: {len(TOOL_CATEGORIES['file']['tools'])}")
    print(f"analyze_logs 是否在集合中: {'analyze_logs' in ALL_TOOLS_SET}")
    
    # 测试
    test_inputs = [
        "分析日志文件",
        "帮我分析 log 文件",
        "查找错误日志",
        "debug 日志分析",
        "读取日志并分析",
    ]
    
    print("\n工具识别测试:")
    for user_input in test_inputs:
        tools = get_tools_for_input(user_input)
        is_recognized = "analyze_logs" in tools
        
        status = "✅" if is_recognized else "❌"
        print(f"\n{status} 输入: '{user_input}'")
        print(f"  analyze_logs: {'✅ 已识别' if is_recognized else '❌ 未识别'}")
        print(f"  选中工具数: {len(tools)}")


def generate_report():
    """生成测试报告"""
    print("\n" + "=" * 80)
    print("测试报告")
    print("=" * 80)
    
    keywords = get_keywords()
    
    report = {
        "测试时间": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "工具总数": len(ALL_TOOLS_SET),
        "类别总数": len(TOOL_CATEGORIES),
        "关键词配置": {},
        "测试结果": [],
    }
    
    for category, kw_list in keywords.items():
        report["关键词配置"][category] = {
            "关键词数": len(kw_list),
            "关键词列表": kw_list,
        }
    
    # 测试用例结果
    test_cases = [
        {"输入": "分析日志文件", "预期类别": ["file"], "预期工具": ["analyze_logs"]},
        {"输入": "搜索天气", "预期类别": ["web", "system"], "预期工具": ["get_weather"]},
        {"输入": "执行命令", "预期类别": ["code", "system"], "预期工具": ["shell_execute"]},
        {"输入": "读取PDF", "预期类别": ["file", "pdf"], "预期工具": ["read_file"]},
    ]
    
    for test_case in test_cases:
        categories = classify_user_input(test_case["输入"])
        tools = get_tools_for_input(test_case["输入"])
        
        category_match = set(test_case["预期类别"]).issubset(categories)
        tool_match = any(t in tools for t in test_case["预期工具"])
        
        report["测试结果"].append({
            "输入": test_case["输入"],
            "匹配类别": list(categories),
            "预期类别": test_case["预期类别"],
            "类别匹配": category_match,
            "匹配工具数": len(tools),
            "预期工具": test_case["预期工具"],
            "工具匹配": tool_match,
        })
    
    # 打印报告
    import json
    print("\nJSON 格式报告:")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_log_keywords()
    test_all_categories()
    test_analyze_logs_tool()
    generate_report()
    
    print("\n" + "=" * 80)
    print("✅ 全量工具扫描测试完成!")
    print("=" * 80)