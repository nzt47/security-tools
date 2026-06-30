#!/usr/bin/env python3
"""
将自动关键词提取逻辑应用到项目配置文件
更新 tool_router_keywords.json 文件
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def apply_auto_keywords_to_config():
    """将自动提取的关键词应用到配置文件中"""
    print("=" * 80)
    print("应用自动关键词提取逻辑到配置文件")
    print("=" * 80)
    
    # 定义工具描述
    tools_descriptions = {
        "analyze_logs": "分析日志文件，提取关键信息和错误模式",
        "search_files": "搜索文件内容，支持正则表达式",
        "read_file": "读取文件内容并返回文本",
        "write_file": "写入文本到文件",
        "list_directory": "列出目录内容和文件信息",
        "get_file_info": "获取文件大小、修改时间等元信息",
    }
    
    # 基于工具描述自动提取关键词
    from scripts.demo_auto_keyword_extraction import ToolKeywordExtractor
    
    extractor = ToolKeywordExtractor()
    
    # 收集所有关键词
    all_keywords = {
        "web": [],
        "file": [],
        "code": [],
        "system": [],
        "extension": [],
        "pdf": [],
        "software": [],
        "async": [],
        "schedule": [],
        "v2": [],
    }
    
    # 添加日志相关关键词
    log_keywords = ["日志", "log", "logs", "debug", "分析日志", "日志分析"]
    all_keywords["file"].extend(log_keywords)
    
    # 处理每个工具
    for tool_name, description in tools_descriptions.items():
        suggestions = extractor.suggest_keywords(description)
        
        # 根据工具名称推断类别
        if "log" in tool_name.lower() or "file" in tool_name.lower():
            category = "file"
            all_keywords[category].extend(suggestions)
        elif "search" in tool_name.lower():
            category = "web"
            all_keywords[category].extend(suggestions)
    
    # 去重
    for category in all_keywords:
        all_keywords[category] = sorted(list(set(all_keywords[category])))
    
    # 配置文件路径
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "tool_router_keywords.json"
    )
    
    print(f"\n配置文件路径: {config_path}")
    
    # 读取现有配置
    existing_config = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            existing_config = json.load(f)
        print(f"已存在配置文件，将合并关键词")
    
    existing_keywords = existing_config.get("keywords", {})
    
    # 合并关键词
    for category, keywords in all_keywords.items():
        if category not in existing_keywords:
            existing_keywords[category] = []
        
        # 添加新关键词
        for kw in keywords:
            if kw not in existing_keywords[category]:
                existing_keywords[category].append(kw)
        
        # 重新排序
        existing_keywords[category] = sorted(existing_keywords[category])
    
    # 保存配置
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({"keywords": existing_keywords}, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 配置文件已更新")
    
    # 显示更新后的配置
    print("\n更新后的关键词配置:")
    for category, keywords in existing_keywords.items():
        print(f"\n{category} ({len(keywords)} 个):")
        print(f"  {keywords}")
    
    return existing_keywords


def test_extreme_log_keywords():
    """构造包含所有新增日志关键词的极端测试用例"""
    print("\n" + "=" * 80)
    print("极端日志关键词测试用例")
    print("=" * 80)
    
    from agent.tool_router import classify_user_input, get_tools_for_input
    
    # 所有新增的日志关键词
    new_log_keywords = ["日志", "log", "logs", "debug", "分析日志", "日志分析"]
    
    print(f"\n新增日志关键词: {new_log_keywords}")
    
    # 构造极端测试用例
    extreme_cases = [
        # 情况1: 所有关键词组合在一起
        {
            "name": "所有关键词组合",
            "input": "日志 log logs debug 分析日志 日志分析",
            "expected_categories": ["file"],
        },
        # 情况2: 关键词分散在不同位置
        {
            "name": "关键词分散",
            "input": "请分析日志文件，使用debug模式查看logs",
            "expected_categories": ["file"],
        },
        # 情况3: 重复关键词
        {
            "name": "重复关键词",
            "input": "日志 日志 日志 log log log logs logs logs",
            "expected_categories": ["file"],
        },
        # 情况4: 与其他类别混合
        {
            "name": "与其他类别混合",
            "input": "搜索天气 日志分析 执行命令",
            "expected_categories": ["file", "web", "code"],
        },
        # 情况5: 纯英文关键词
        {
            "name": "纯英文关键词",
            "input": "debug log logs",
            "expected_categories": ["file"],
        },
        # 情况6: 纯中文关键词
        {
            "name": "纯中文关键词",
            "input": "日志 分析日志 日志分析",
            "expected_categories": ["file"],
        },
        # 情况7: 特殊字符干扰
        {
            "name": "特殊字符干扰",
            "input": "!!!日志@@@log###logs$$$debug%%%分析日志^^^日志分析&&&",
            "expected_categories": ["file"],
        },
        # 情况8: 超长输入
        {
            "name": "超长输入",
            "input": "日志 " * 50 + "log " * 50 + "logs " * 50,
            "expected_categories": ["file"],
        },
        # 情况9: 中英混合长句
        {
            "name": "中英混合长句",
            "input": "我需要分析日志文件，使用debug模式查看logs输出，然后进行日志分析",
            "expected_categories": ["file"],
        },
        # 情况10: 空关键词边界
        {
            "name": "空关键词",
            "input": "",
            "expected_categories": ["core"],
        },
    ]
    
    print("\n测试结果:")
    print("-" * 80)
    
    all_passed = True
    
    for i, case in enumerate(extreme_cases, 1):
        input_text = case["input"]
        expected = set(case["expected_categories"])
        
        # 获取匹配的类别
        matched = classify_user_input(input_text)
        
        # 检查结果
        success = expected.issubset(matched) if input_text else (matched == expected)
        
        if not success:
            all_passed = False
        
        status = "✅" if success else "❌"
        
        print(f"\n{status} 测试 {i}: {case['name']}")
        print(f"   输入: {input_text[:50]}..." if len(input_text) > 50 else f"   输入: {input_text}")
        print(f"   预期类别: {expected}")
        print(f"   实际类别: {matched}")
        
        # 如果有输入，获取工具选择结果
        if input_text:
            tools = get_tools_for_input(input_text)
            print(f"   工具数量: {len(tools)}")
    
    return all_passed


def verify_priority_order():
    """验证 file 类别的匹配优先级"""
    print("\n" + "=" * 80)
    print("验证 file 类别的匹配优先级")
    print("=" * 80)
    
    from agent.tool_router import TOOL_CATEGORIES, get_tools_for_input, classify_user_input as classify_ui
    
    # 检查 file 类别的优先级
    file_priority = TOOL_CATEGORIES["file"].get("priority", 99)
    print(f"\nfile 类别优先级: {file_priority}")
    
    # 显示所有类别的优先级
    print("\n所有类别优先级:")
    for cat_key, cat_info in sorted(TOOL_CATEGORIES.items(), 
                                    key=lambda x: x[1].get("priority", 99)):
        priority = cat_info.get("priority", 99)
        icon = cat_info.get("icon", "⚙")
        print(f"  [{priority:2d}] {icon} {cat_key}")
    
    # 测试优先级效果
    print("\n优先级效果测试:")
    test_inputs = [
        ("搜索天气", ["web", "system"]),
        ("读取日志", ["file"]),
        ("执行命令", ["code", "system"]),
    ]
    
    for input_text, expected_categories in test_inputs:
        matched = classify_ui(input_text)
        tools = get_tools_for_input(input_text)
        
        print(f"\n  输入: {input_text}")
        print(f"  匹配类别: {matched}")
        print(f"  预期类别: {expected_categories}")
        print(f"  工具数量: {len(tools)}")
        
        # 检查优先级是否正确
        matched_with_priority = sorted(
            [cat for cat in matched if cat in TOOL_CATEGORIES],
            key=lambda c: TOOL_CATEGORIES[c].get("priority", 99)
        )
        print(f"  按优先级排序: {matched_with_priority}")


if __name__ == "__main__":
    # 应用自动关键词到配置
    keywords = apply_auto_keywords_to_config()
    
    # 运行极端测试
    all_passed = test_extreme_log_keywords()
    
    # 验证优先级
    verify_priority_order()
    
    print("\n" + "=" * 80)
    print("✅ 测试完成!")
    print("=" * 80)
    
    if all_passed:
        print("\n🎉 所有极端测试用例通过!")
    else:
        print("\n⚠️ 部分测试用例未通过，请检查关键词配置")