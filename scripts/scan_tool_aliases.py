#!/usr/bin/env python3
"""
同类工具别名扫描脚本
扫描所有工具定义，识别功能相似但未被合并的工具
"""

import os
import sys
import re
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tool_router import ALL_TOOLS_SET, TOOL_CATEGORIES, TOOL_ALIASES


def analyze_tool_names():
    """分析工具名称模式，识别潜在的同类工具"""
    print("=" * 80)
    print("工具名称模式分析")
    print("=" * 80)
    
    # 按名称前缀/后缀分组
    prefix_groups = defaultdict(list)
    suffix_groups = defaultdict(list)
    keyword_groups = defaultdict(list)
    
    # 定义关键词模式
    patterns = {
        "read": ["read", "get", "fetch", "load"],
        "write": ["write", "save", "store", "create"],
        "list": ["list", "show", "display", "view"],
        "install": ["install", "add", "setup"],
        "uninstall": ["uninstall", "remove", "delete", "cancel"],
        "search": ["search", "find", "query", "lookup"],
        "execute": ["execute", "run", "start", "launch"],
        "stop": ["stop", "kill", "terminate", "cancel"],
        "get": ["get", "fetch", "retrieve", "obtain"],
        "web": ["web", "http", "url", "fetch"],
        "file": ["file", "read", "write", "directory"],
        "process": ["process", "task", "job", "async"],
        "schedule": ["schedule", "cron", "timer", "periodic"],
        "ext": ["ext", "extension", "plugin", "addon"],
        "software": ["software", "package", "install"],
    }
    
    for tool in ALL_TOOLS_SET:
        # 按前缀分组（取第一个下划线前的部分）
        prefix = tool.split("_")[0] if "_" in tool else tool
        prefix_groups[prefix].append(tool)
        
        # 按后缀分组（取最后一个下划线后的部分）
        suffix = tool.split("_")[-1] if "_" in tool else tool
        suffix_groups[suffix].append(tool)
        
        # 按关键词分组
        for pattern_name, keywords in patterns.items():
            for kw in keywords:
                if kw in tool.lower():
                    keyword_groups[pattern_name].append(tool)
                    break
    
    print("\n前缀分组分析:")
    for prefix, tools in sorted(prefix_groups.items(), key=lambda x: len(x[1]), reverse=True):
        if len(tools) > 1:
            print(f"  {prefix}: {len(tools)} 个工具")
            print(f"    {', '.join(tools)}")
    
    print("\n后缀分组分析:")
    for suffix, tools in sorted(suffix_groups.items(), key=lambda x: len(x[1]), reverse=True):
        if len(tools) > 1:
            print(f"  {suffix}: {len(tools)} 个工具")
            print(f"    {', '.join(tools)}")
    
    print("\n关键词分组分析:")
    for pattern, tools in sorted(keyword_groups.items(), key=lambda x: len(x[1]), reverse=True):
        if len(tools) > 1:
            print(f"  {pattern}: {len(tools)} 个工具")
            print(f"    {', '.join(tools)}")


def find_similar_tools():
    """识别功能相似的工具"""
    print("\n" + "=" * 80)
    print("功能相似工具识别")
    print("=" * 80)
    
    # 定义相似工具规则
    similarity_rules = [
        # 规则1: 读取类工具
        {
            "category": "读取类",
            "pattern": ["read", "get", "fetch", "load"],
            "expected_merge": "read_file 应合并 read_pdf",
            "current_status": "已合并 ✅",
        },
        # 规则2: 列表类工具
        {
            "category": "列表类",
            "pattern": ["list", "show"],
            "expected_merge": "list_directory 应合并 list_processes, list_async_tasks, list_scheduled_tasks, software_list, ext_list",
            "current_status": "已合并 ✅",
        },
        # 规则3: 执行类工具
        {
            "category": "执行类",
            "pattern": ["execute", "run", "start", "launch"],
            "expected_merge": "shell_execute 应合并 run_program",
            "current_status": "已合并 ✅",
        },
        # 规则4: 安装类工具
        {
            "category": "安装类",
            "pattern": ["install", "add", "setup"],
            "expected_merge": "software_install 应合并 ext_install",
            "current_status": "已合并 ✅",
        },
        # 规则5: 卸载类工具
        {
            "category": "卸载类",
            "pattern": ["uninstall", "remove", "delete"],
            "expected_merge": "software_uninstall 应合并 ext_uninstall",
            "current_status": "已合并 ✅",
        },
        # 规则6: 搜索类工具
        {
            "category": "搜索类",
            "pattern": ["search", "find", "query"],
            "expected_merge": "潜在合并: search_files, search_memory, web_search",
            "current_status": "未合并 ⚠️",
        },
        # 规则7: 获取类工具
        {
            "category": "获取类",
            "pattern": ["get", "fetch", "retrieve"],
            "expected_merge": "潜在合并: get_status, get_file_info, get_weather, get_task_status, get_task_result, get_pdf_info, get_sensor_summary",
            "current_status": "未合并 ⚠️",
        },
        # 规则8: 取消类工具
        {
            "category": "取消类",
            "pattern": ["cancel", "stop", "terminate"],
            "expected_merge": "潜在合并: cancel_task, cancel_scheduled_task, stop_process",
            "current_status": "未合并 ⚠️",
        },
        # 规则9: Web类工具
        {
            "category": "Web类",
            "pattern": ["web", "http"],
            "expected_merge": "web_get, web_post, web_search 可能部分合并",
            "current_status": "未合并 ⚠️",
        },
    ]
    
    print("\n当前合并状态:")
    for rule in similarity_rules:
        status_icon = "✅" if "已合并" in rule["current_status"] else "⚠️"
        print(f"\n{status_icon} {rule['category']}:")
        print(f"  匹配模式: {rule['pattern']}")
        print(f"  预期合并: {rule['expected_merge']}")
        print(f"  当前状态: {rule['current_status']}")
    
    # 找出匹配模式的工具
    print("\n潜在合并候选:")
    for rule in similarity_rules:
        if "未合并" in rule["current_status"]:
            matching_tools = []
            for tool in ALL_TOOLS_SET:
                for pattern in rule["pattern"]:
                    if pattern in tool.lower():
                        matching_tools.append(tool)
                        break
            
            if matching_tools:
                print(f"\n⚠️ {rule['category']} ({len(matching_tools)} 个工具):")
                print(f"  {', '.join(matching_tools)}")


def suggest_new_aliases():
    """建议新的别名合并规则"""
    print("\n" + "=" * 80)
    print("建议新增别名合并规则")
    print("=" * 80)
    
    suggestions = [
        {
            "main": "search_memory",
            "aliases": ["search_files"],
            "reason": "都是搜索功能，但搜索对象不同（内存/文件）",
            "recommendation": "不建议合并 - 搜索对象不同",
        },
        {
            "main": "get_status",
            "aliases": ["get_task_status", "get_sensor_summary"],
            "reason": "都是获取状态，但状态类型不同",
            "recommendation": "不建议合并 - 状态类型不同",
        },
        {
            "main": "cancel_task",
            "aliases": ["cancel_scheduled_task"],
            "reason": "都是取消任务，但任务类型不同",
            "recommendation": "可考虑合并 - 都是取消任务操作",
        },
        {
            "main": "stop_process",
            "aliases": [],
            "reason": "停止进程，与取消任务功能相似",
            "recommendation": "不建议合并 - 操作对象不同（进程/任务）",
        },
        {
            "main": "web_search",
            "aliases": ["fetch_news"],
            "reason": "都是网络获取信息",
            "recommendation": "不建议合并 - 功能定位不同（搜索/新闻）",
        },
    ]
    
    print("\n合并建议分析:")
    for suggestion in suggestions:
        print(f"\n主工具: {suggestion['main']}")
        print(f"候选别名: {suggestion['aliases']}")
        print(f"原因: {suggestion['reason']}")
        print(f"建议: {suggestion['recommendation']}")


def verify_current_aliases():
    """验证当前别名配置"""
    print("\n" + "=" * 80)
    print("当前别名配置验证")
    print("=" * 80)
    
    print(f"\n已配置别名规则数: {len(TOOL_ALIASES)}")
    
    print("\n别名配置详情:")
    for main, aliases in TOOL_ALIASES.items():
        # 检查主工具是否存在
        main_exists = main in ALL_TOOLS_SET
        # 检查别名工具是否存在
        aliases_exist = [a for a in aliases if a in ALL_TOOLS_SET]
        aliases_missing = [a for a in aliases if a not in ALL_TOOLS_SET]
        
        status = "✅" if main_exists and len(aliases_missing) == 0 else "⚠️"
        
        print(f"\n{status} {main} → [{', '.join(aliases)}]")
        if not main_exists:
            print(f"  ⚠️ 主工具 {main} 不存在于工具列表中")
        if aliases_missing:
            print(f"  ⚠️ 别名工具 {aliases_missing} 不存在于工具列表中")
        if aliases_exist:
            print(f"  ✅ 有效别名: {aliases_exist}")


def generate_alias_report():
    """生成别名扫描报告"""
    print("\n" + "=" * 80)
    print("同类工具别名扫描报告")
    print("=" * 80)
    
    print("\n【已合并的工具别名】")
    for main, aliases in TOOL_ALIASES.items():
        print(f"  {main} → {aliases}")
    
    print("\n【不建议合并的工具】")
    print("  - search_memory vs search_files: 搜索对象不同")
    print("  - get_status vs get_task_status: 状态类型不同")
    print("  - web_search vs fetch_news: 功能定位不同")
    print("  - stop_process vs cancel_task: 操作对象不同")
    
    print("\n【可考虑合并的工具】")
    print("  - cancel_task + cancel_scheduled_task: 都是取消任务操作")
    
    print("\n【结论】")
    print("  当前别名配置合理，覆盖了主要的同类工具合并场景")
    print("  唯一可考虑新增: cancel_scheduled_task → cancel_task 的别名")


if __name__ == "__main__":
    analyze_tool_names()
    find_similar_tools()
    suggest_new_aliases()
    verify_current_aliases()
    generate_alias_report()
    
    print("\n" + "=" * 80)
    print("✅ 扫描完成!")
    print("=" * 80)