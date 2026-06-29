#!/usr/bin/env python3
"""
配置应用脚本 - 将默认配置应用到当前项目
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def apply_default_config():
    """应用默认配置文件"""
    print("=" * 80)
    print("应用默认配置文件")
    print("=" * 80)
    
    # 默认配置文件路径
    default_config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "tool_router_default_config.json"
    )
    
    # 目标配置文件路径
    target_config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "tool_router_keywords.json"
    )
    
    print(f"\n默认配置文件: {default_config_path}")
    print(f"目标配置文件: {target_config_path}")
    
    # 读取默认配置
    with open(default_config_path, "r", encoding="utf-8") as f:
        default_config = json.load(f)
    
    # 提取关键词配置
    keywords_config = {
        "keywords": default_config["keywords_config"]["keywords"]
    }
    
    # 保存到目标配置文件
    os.makedirs(os.path.dirname(target_config_path), exist_ok=True)
    with open(target_config_path, "w", encoding="utf-8") as f:
        json.dump(keywords_config, f, ensure_ascii=False, indent=2)
    
    print("✅ 配置文件已应用")
    
    # 显示应用的配置摘要
    print("\n应用的配置摘要:")
    total_keywords = 0
    for category, keywords in keywords_config["keywords"].items():
        count = len(keywords)
        total_keywords += count
        print(f"  {category}: {count} 个关键词")
    print(f"\n  总计: {total_keywords} 个关键词")
    
    return True


def run_full_test():
    """运行全量测试"""
    print("\n" + "=" * 80)
    print("运行全量测试")
    print("=" * 80)
    
    from agent.tests.test_tool_router import ToolRouterTester
    
    tester = ToolRouterTester()
    results = tester.run_all_tests()
    
    # 打印详细报告
    print("\n测试报告:")
    print(tester.generate_report())
    
    return results["summary"]["success_rate"] == 100.0


def analyze_boundary_conditions():
    """分析边界条件"""
    print("\n" + "=" * 80)
    print("边界条件分析")
    print("=" * 80)
    
    from agent.tool_router import ALL_TOOLS_SET, TOOL_CATEGORIES, TOOL_ALIASES
    
    print("\n当前工具状态:")
    print(f"  工具总数: {len(ALL_TOOLS_SET)}")
    print(f"  类别总数: {len(TOOL_CATEGORIES)}")
    print(f"  别名规则数: {len(TOOL_ALIASES)}")
    
    # 分析潜在边界条件
    boundary_conditions = [
        {
            "name": "工具数量为0",
            "description": "当 ALL_TOOLS_SET 为空时的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "类别无工具",
            "description": "某个类别没有工具时的处理",
            "risk": "中",
            "status": "未测试",
        },
        {
            "name": "关键词为空",
            "description": "某个类别关键词为空时的处理",
            "risk": "中",
            "status": "已测试",
        },
        {
            "name": "工具动态添加",
            "description": "运行时动态添加工具的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "工具动态删除",
            "description": "运行时动态删除工具的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "优先级冲突",
            "description": "多个类别优先级相同的处理",
            "risk": "中",
            "status": "已测试",
        },
        {
            "name": "别名循环引用",
            "description": "别名形成循环的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "配置文件损坏",
            "description": "配置文件JSON格式错误的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "工具名称冲突",
            "description": "不同类别包含同名工具的处理",
            "risk": "中",
            "status": "已测试",
        },
        {
            "name": "极端关键词数量",
            "description": "单个类别包含大量关键词的性能影响",
            "risk": "中",
            "status": "已测试",
        },
    ]
    
    print("\n边界条件清单:")
    print("-" * 80)
    print(f"{'名称':<20} {'风险':<6} {'状态':<10} {'描述'}")
    print("-" * 80)
    
    for bc in boundary_conditions:
        print(f"{bc['name']:<20} {bc['risk']:<6} {bc['status']:<10} {bc['description']}")
    
    # 统计未测试项
    untested = [bc for bc in boundary_conditions if bc["status"] == "未测试"]
    print(f"\n未测试的边界条件: {len(untested)} 个")
    
    return boundary_conditions


if __name__ == "__main__":
    # 应用配置
    apply_default_config()
    
    # 运行全量测试
    success = run_full_test()
    
    # 分析边界条件
    analyze_boundary_conditions()
    
    print("\n" + "=" * 80)
    if success:
        print("🎉 配置应用成功，全量测试通过!")
    else:
        print("⚠️ 全量测试未通过")
    print("=" * 80)