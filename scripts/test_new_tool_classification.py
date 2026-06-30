#!/usr/bin/env python3
"""
新工具自动分类机制测试 - 模拟添加 analyze_logs 工具
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
    TOOL_ALIASES,
    get_categorized_tools,
)


def simulate_add_new_tool():
    """模拟添加新工具并测试分类"""
    print("=" * 80)
    print("新工具自动分类机制测试")
    print("=" * 80)
    
    # 新工具定义
    new_tool_name = "analyze_logs"
    new_tool_info = {
        "name": new_tool_name,
        "description": "分析日志文件，提取关键信息和错误模式",
        "category": "file",  # 预期分类
        "keywords": ["日志", "log", "分析", "analyze", "错误", "error", "debug"],
    }
    
    print(f"\n新工具: {new_tool_name}")
    print(f"描述: {new_tool_info['description']}")
    print(f"预期分类: {new_tool_info['category']}")
    print(f"相关关键词: {new_tool_info['keywords']}")
    
    # 模拟添加工具到分类
    print("\n1. 模拟将工具添加到 file 类别")
    TOOL_CATEGORIES["file"]["tools"].append(new_tool_name)
    print(f"   file 类别工具数: {len(TOOL_CATEGORIES['file']['tools'])}")
    print(f"   工具列表: {TOOL_CATEGORIES['file']['tools']}")
    
    # 更新 ALL_TOOLS_SET
    ALL_TOOLS_SET.add(new_tool_name)
    print(f"\n2. 更新工具集合")
    print(f"   工具总数: {len(ALL_TOOLS_SET)}")
    print(f"   {new_tool_name} 是否在集合中: {new_tool_name in ALL_TOOLS_SET}")
    
    return new_tool_name, new_tool_info


def test_new_tool_recognition():
    """测试新工具能否被正确识别"""
    print("\n" + "=" * 80)
    print("测试新工具识别")
    print("=" * 80)
    
    new_tool_name, new_tool_info = simulate_add_new_tool()
    
    # 测试触发关键词
    test_inputs = [
        "分析日志文件",
        "帮我分析 log 文件",
        "查找错误日志",
        "debug 日志分析",
        "读取日志并分析",
    ]
    
    print(f"\n测试触发 {new_tool_name} 的输入:")
    
    for user_input in test_inputs:
        categories = classify_user_input(user_input)
        tools = get_tools_for_input(user_input)
        
        is_recognized = new_tool_name in tools
        is_file_category = "file" in categories
        
        status = "✅" if is_recognized else "❌"
        print(f"\n{status} 输入: '{user_input}'")
        print(f"  匹配类别: {categories}")
        print(f"  file 类别: {'✅' if is_file_category else '❌'}")
        print(f"  {new_tool_name}: {'✅ 已识别' if is_recognized else '❌ 未识别'}")
        print(f"  选中工具数: {len(tools)}")
    
    return new_tool_name


def test_end_to_end():
    """端到端测试 - 验证完整流程"""
    print("\n" + "=" * 80)
    print("端到端测试 - 验证完整流程")
    print("=" * 80)
    
    from agent.utils.decision_logger import DecisionLogger, SkipReason, create_decision_logger
    
    # 创建 JSON 格式的决策日志器
    logger = create_decision_logger(verbose=True, output_format="json")
    
    # 开始日志
    logger.start_log(
        context="端到端测试: 日志分析工具选择",
        input_data={"user_input": "帮我分析日志文件"}
    )
    
    # 获取工具选择结果
    user_input = "帮我分析日志文件"
    categories = classify_user_input(user_input)
    tools = get_tools_for_input(user_input, verbose=False)
    
    # 记录决策过程
    logger.log_category("file", 2, "文件系统", 9)  # 新增工具后是9个
    for tool in tools:
        if "analyze_logs" in tool:
            logger.log_selected(tool, source="file", extra_info="新工具已识别")
        else:
            logger.log_selected(tool, source="file")
    
    # 记录别名合并
    for alias, main in [("read_pdf", "read_file"), ("run_program", "shell_execute")]:
        if main in tools and alias not in tools:
            logger.log_skipped(alias, SkipReason.ALIAS, source="alias_merge", detail=f"是 {main} 的别名")
    
    # 结束日志
    summary = {
        "total_tools": len(tools),
        "categories": list(categories),
        "new_tool_recognized": "analyze_logs" in tools,
    }
    decision_log = logger.end_log(summary)
    
    # 输出 JSON 格式结果
    print("\n最终 JSON 输出:")
    print(decision_log.to_json())
    
    return decision_log


def test_decision_logger_integration():
    """测试 DecisionLogger 在实际流程中的集成"""
    print("\n" + "=" * 80)
    print("DecisionLogger 集成测试")
    print("=" * 80)
    
    from agent.orchestrator.task_dispatcher import TaskDispatcher
    
    # 创建模拟宿主类
    class MockLLM:
        model = "test-model"
    
    class MockHost(TaskDispatcher):
        def __init__(self):
            self._llm = MockLLM()
            self._llm_pro = None
            self._model_router = None
            self._planner = None
            self._planning_enabled = False
        
        def _get_enabled_tools_whitelist(self):
            return None
        
        def _set_thinking_mode(self, mode='standby'):
            pass
    
    host = MockHost()
    
    # 测试 dispatch_task 方法
    print("\n测试 dispatch_task (verbose=True):")
    result = host.dispatch_task("帮我分析日志文件", verbose=True)
    
    print("\n返回结果:")
    print(f"  path: {result.get('path')}")
    print(f"  model: {result.get('model')}")
    print(f"  tools_whitelist: {result.get('tools_whitelist')}")
    print(f"  needs_planning: {result.get('needs_planning')}")
    
    if result.get('decision_log'):
        log = result['decision_log']
        print(f"\n决策日志:")
        print(f"  ID: {log.id}")
        print(f"  上下文: {log.context}")
        print(f"  选中项: {len(log.selected)} 个")
        print(f"  耗时: {log.duration_ms:.2f}ms")
        print(f"\nJSON 格式输出:")
        print(log.to_json())


if __name__ == "__main__":
    # 测试新工具自动分类
    new_tool = test_new_tool_recognition()
    
    # 端到端测试
    decision_log = test_end_to_end()
    
    # DecisionLogger 集成测试
    test_decision_logger_integration()
    
    print("\n" + "=" * 80)
    print("✅ 所有测试完成!")
    print("=" * 80)