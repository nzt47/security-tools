#!/usr/bin/env python3
"""
极端别名场景测试 - 验证所有别名合并逻辑的边缘情况
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tool_router import (
    get_tools_for_input,
    classify_user_input,
    estimate_tool_tokens,
    ALL_TOOLS_SET,
    TOOL_CATEGORIES,
    TOOL_ALIASES,
)
from agent.utils.decision_logger import DecisionLogger, SkipReason, create_decision_logger


def test_all_alias_scenarios():
    """测试所有别名场景"""
    print("=" * 80)
    print("极端别名场景测试 - 覆盖所有别名合并逻辑的边缘情况")
    print("=" * 80)
    
    print(f"\n工具总数: {len(ALL_TOOLS_SET)}")
    print(f"别名规则数: {len(TOOL_ALIASES)}")
    
    print("\n别名配置详情:")
    for main, aliases in TOOL_ALIASES.items():
        print(f"  {main} → {aliases}")
    
    # 构造极端测试场景
    extreme_scenarios = [
        # 场景1: 触发所有别名规则的输入
        {
            "name": "触发所有别名",
            "input": "执行命令 运行程序 读取文件 读取PDF 列出目录 列出进程 安装软件 安装扩展 卸载软件 卸载扩展 取消任务 取消定时任务",
            "expected_aliases": list(TOOL_ALIASES.keys()),
        },
        # 场景2: 多次触发同一个别名规则
        {
            "name": "重复触发同一别名",
            "input": "执行命令 执行命令 执行命令 读取文件 读取文件",
            "expected_aliases": ["shell_execute", "read_file"],
        },
        # 场景3: 触发别名但主工具不在列表中（理论上不应该发生）
        {
            "name": "别名无主工具（模拟）",
            "input": "执行命令",
            "expected_aliases": ["shell_execute"],
        },
        # 场景4: 多个别名同时触发同一主工具
        {
            "name": "多个别名触发同一主工具",
            "input": "列出目录 列出进程 列出异步任务 列出定时任务 列出软件列表 列出扩展列表",
            "expected_aliases": ["list_directory"],
        },
        # 场景5: 类别优先级导致别名被覆盖
        {
            "name": "优先级覆盖别名",
            "input": "执行命令 进程管理",
            "expected_aliases": ["shell_execute"],
        },
        # 场景6: 空输入边界测试
        {
            "name": "空输入",
            "input": "",
            "expected_aliases": [],
        },
        # 场景7: 特殊字符输入
        {
            "name": "特殊字符",
            "input": "!@#$%^&*()_+-=[]{}|;':\",./<>?",
            "expected_aliases": [],
        },
        # 场景8: 超长输入
        {
            "name": "超长输入",
            "input": "执行命令 " * 100,
            "expected_aliases": ["shell_execute"],
        },
        # 场景9: 中文和英文混合
        {
            "name": "中英混合",
            "input": "execute 命令 run 程序 read 文件",
            "expected_aliases": ["shell_execute", "read_file"],
        },
        # 场景10: 数字和符号干扰
        {
            "name": "数字符号干扰",
            "input": "1执行2命令3run4程序5读取6文件",
            "expected_aliases": ["shell_execute", "read_file"],
        },
    ]
    
    # 创建 JSON 格式的日志输出
    json_logger = create_decision_logger(verbose=False, output_format="json")
    
    all_results = []
    
    print("\n" + "=" * 80)
    print("开始极端场景测试")
    print("=" * 80)
    
    for i, scenario in enumerate(extreme_scenarios, 1):
        print(f"\n{'='*60}")
        print(f"场景 {i}: {scenario['name']}")
        print(f"{'='*60}")
        print(f"输入: {scenario['input'][:50]}...")
        
        # 开始 JSON 格式日志记录
        json_logger.start_log(
            context=f"极端测试: {scenario['name']}",
            input_data={"input": scenario['input'][:100]}
        )
        
        # 获取匹配类别
        categories = classify_user_input(scenario["input"])
        print(f"匹配类别: {categories}")
        
        # 获取选中的工具
        start_time = time.time()
        tools = get_tools_for_input(scenario["input"], verbose=False)
        duration_ms = (time.time() - start_time) * 1000
        
        tokens = estimate_tool_tokens(tools)
        
        # 检查别名合并效果
        merged_aliases = []
        for main, aliases in TOOL_ALIASES.items():
            if main in tools:
                for alias in aliases:
                    if alias not in tools:
                        merged_aliases.append((alias, main))
        
        # 记录结果
        result = {
            "scenario_id": i,
            "name": scenario["name"],
            "input": scenario["input"],
            "categories": list(categories),
            "selected_tools": tools,
            "tool_count": len(tools),
            "tokens_estimate": tokens,
            "duration_ms": duration_ms,
            "merged_aliases": [{"alias": a, "main": m} for a, m in merged_aliases],
            "expected_aliases": scenario["expected_aliases"],
        }
        all_results.append(result)
        
        # 记录到 JSON 日志
        for tool in tools:
            json_logger.log_selected(tool, source="tool_selection")
        
        for alias, main in merged_aliases:
            json_logger.log_skipped(alias, SkipReason.ALIAS, 
                                  source="alias_merge", 
                                  detail=f"是 {main} 的别名")
        
        json_logger.end_log({
            "tool_count": len(tools),
            "tokens_estimate": tokens,
            "duration_ms": duration_ms,
        })
        
        # 打印结果
        print(f"  选中工具: {len(tools)} 个")
        print(f"  Token估算: ~{tokens}")
        print(f"  耗时: {duration_ms:.2f}ms")
        print(f"  别名合并: {len(merged_aliases)} 个")
        
        if merged_aliases:
            for alias, main in merged_aliases:
                print(f"    - {alias} → {main}")
    
    return all_results


def test_alias_priority_interaction():
    """测试别名与优先级的交互"""
    print("\n" + "=" * 80)
    print("别名与优先级交互测试")
    print("=" * 80)
    
    # 构造一个会触发多个类别和别名的复杂场景
    complex_input = "执行shell命令 列出目录 读取文件 安装软件 取消定时任务"
    
    print(f"\n复杂输入: {complex_input}")
    
    # 获取类别
    categories = classify_user_input(complex_input)
    print(f"匹配类别: {categories}")
    
    # 获取工具
    tools = get_tools_for_input(complex_input, verbose=True)
    
    print(f"\n选中工具: {len(tools)} 个")
    print(f"工具列表: {tools}")


def generate_json_report(results: list):
    """生成 JSON 格式的测试报告"""
    print("\n" + "=" * 80)
    print("生成 JSON 格式测试报告")
    print("=" * 80)
    
    report = {
        "test_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_scenarios": len(results),
            "total_tools": len(ALL_TOOLS_SET),
            "alias_rules": len(TOOL_ALIASES),
        },
        "alias_configuration": {
            main: aliases for main, aliases in TOOL_ALIASES.items()
        },
        "results": results,
    }
    
    json_report = json.dumps(report, ensure_ascii=False, indent=2)
    
    # 保存到文件
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "extreme_alias_test_report.json")
    
    try:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(json_report)
        print(f"\n报告已保存到: {report_path}")
    except Exception as e:
        print(f"保存报告失败: {e}")
    
    return json_report


def analyze_results(results: list):
    """分析测试结果"""
    print("\n" + "=" * 80)
    print("测试结果分析")
    print("=" * 80)
    
    total_aliases = sum(len(r["merged_aliases"]) for r in results)
    avg_duration = sum(r["duration_ms"] for r in results) / len(results) if results else 0
    
    print(f"\n统计汇总:")
    print(f"  测试场景数: {len(results)}")
    print(f"  总别名合并数: {total_aliases}")
    print(f"  平均耗时: {avg_duration:.2f}ms")
    
    # 统计每个别名规则被触发的次数
    alias_trigger_count = {}
    for result in results:
        for merged in result["merged_aliases"]:
            alias = merged["alias"]
            alias_trigger_count[alias] = alias_trigger_count.get(alias, 0) + 1
    
    print(f"\n别名触发统计:")
    for alias, count in sorted(alias_trigger_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  {alias}: {count} 次")
    
    return {
        "total_scenarios": len(results),
        "total_alias_merges": total_aliases,
        "avg_duration_ms": avg_duration,
        "alias_trigger_count": alias_trigger_count,
    }


if __name__ == "__main__":
    # 执行所有测试
    results = test_all_alias_scenarios()
    
    # 测试别名与优先级交互
    test_alias_priority_interaction()
    
    # 分析结果
    analysis = analyze_results(results)
    
    # 生成 JSON 报告
    json_report = generate_json_report(results)
    
    print("\n" + "=" * 80)
    print("✅ 极端别名场景测试完成!")
    print("=" * 80)
    
    # 打印 JSON 报告摘要
    print("\nJSON 报告摘要:")
    print(json_report[:500] + "..." if len(json_report) > 500 else json_report)