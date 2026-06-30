#!/usr/bin/env python3
"""
诊断智能工具选择问题
检查为什么监控面板显示70个工具而不是智能选择后的数量
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def diagnose_smart_tool_selection():
    """诊断智能工具选择问题"""
    print("=" * 80)
    print("智能工具选择诊断")
    print("=" * 80)
    
    # 1. 检查配置文件
    print("\n1. 检查配置文件")
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "system_prompt_config.json"
    )
    
    print(f"配置文件路径: {cfg_path}")
    print(f"配置文件存在: {os.path.exists(cfg_path)}")
    
    if os.path.exists(cfg_path):
        import json
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        smart_enabled = cfg.get("sections", {}).get("smart_tool_selection", {}).get("enabled", False)
        print(f"\n智能工具选择启用状态: {'✅ 已启用' if smart_enabled else '❌ 未启用'}")
        
        # 检查工具定义配置
        tool_defs_enabled = cfg.get("sections", {}).get("tool_definitions", {}).get("enabled", False)
        print(f"工具定义启用状态: {'✅ 已启用' if tool_defs_enabled else '❌ 未启用'}")
    
    # 2. 测试智能工具选择
    print("\n" + "=" * 80)
    print("2. 测试智能工具选择")
    print("=" * 80)
    
    try:
        from agent.tool_router import get_tools_for_input, ALL_TOOLS_SET
        
        test_input = "帮我读取一个文件"
        smart_tools = get_tools_for_input(test_input)
        
        print(f"\n测试输入: '{test_input}'")
        print(f"智能选择工具数: {len(smart_tools)}")
        print(f"总工具数: {len(ALL_TOOLS_SET)}")
        print(f"\n智能选择的工具:")
        for tool in smart_tools[:10]:
            print(f"  - {tool}")
        if len(smart_tools) > 10:
            print(f"  ... 还有 {len(smart_tools) - 10} 个工具")
        
        # 验证是否真的过滤了
        if len(smart_tools) < len(ALL_TOOLS_SET):
            print("\n✅ 智能工具选择正常工作")
        else:
            print("\n❌ 智能工具选择没有过滤工具")
            
    except Exception as e:
        print(f"\n❌ 智能工具选择测试失败: {e}")
    
    # 3. 检查实际的工具定义获取
    print("\n" + "=" * 80)
    print("3. 检查工具定义获取")
    print("=" * 80)
    
    try:
        from agent import tools
        
        # 获取所有工具定义
        all_defs = tools.get_tool_defs()
        print(f"所有工具定义数: {len(all_defs)}")
        
        # 使用白名单获取
        whitelist = ["read_file", "write_file", "list_directory"]
        filtered_defs = tools.get_tool_defs(whitelist=whitelist)
        print(f"白名单过滤后工具定义数: {len(filtered_defs)}")
        
    except Exception as e:
        print(f"\n❌ 工具定义获取测试失败: {e}")
    
    # 4. 检查 Orchestrator 中的逻辑
    print("\n" + "=" * 80)
    print("4. 检查 Orchestrator 逻辑")
    print("=" * 80)
    
    try:
        from agent.orchestrator.task_dispatcher import TaskDispatcher
        
        # 创建一个简单的测试
        print("TaskDispatcher 模块加载成功")
        
        # 检查 _is_smart_tool_selection_enabled 方法
        dispatcher = TaskDispatcher.__new__(TaskDispatcher)
        
        # 模拟检查
        agent_dir = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(os.path.dirname(agent_dir), "data", "system_prompt_config.json")
        
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            
            enabled = cfg.get("sections", {}).get("smart_tool_selection", {}).get("enabled", False)
            print(f"\n智能工具选择配置: {'✅ 启用' if enabled else '❌ 禁用'}")
            
            if enabled:
                print("智能工具选择应该被触发")
            else:
                print("智能工具选择不会被触发 - 这可能是问题所在!")
    
    except Exception as e:
        print(f"\n❌ Orchestrator 检查失败: {e}")


if __name__ == "__main__":
    diagnose_smart_tool_selection()
    
    print("\n" + "=" * 80)
    print("诊断完成!")
    print("=" * 80)