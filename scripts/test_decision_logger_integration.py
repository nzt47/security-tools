#!/usr/bin/env python3
"""
DecisionLogger 集成测试
验证决策日志工具类是否正确集成到主流程中
"""

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def test_decision_logger_integration():
    """测试 DecisionLogger 集成"""
    print("=" * 80)
    print("DecisionLogger 集成测试")
    print("=" * 80)
    
    # 测试 DecisionLogger 是否可用
    try:
        from agent.utils.decision_logger import DecisionLogger, SkipReason, create_decision_logger
        print("✅ DecisionLogger 模块导入成功")
    except ImportError as e:
        print(f"❌ DecisionLogger 模块导入失败: {e}")
        return
    
    # 测试创建决策日志器
    logger = create_decision_logger(verbose=True, logger_name="test_integration")
    print("✅ DecisionLogger 实例创建成功")
    
    # 模拟一个完整的决策流程
    print("\n模拟决策流程:")
    
    # 开始日志
    log = logger.start_log(
        context="测试工具选择决策",
        input_data={"user_input": "帮我搜索天气并读取文件"}
    )
    print("✅ 决策日志开始记录")
    
    # 记录类别处理
    logger.log_category("core", 0, "核心工具", 5)
    logger.log_selected("get_status", source="core")
    logger.log_selected("search_memory", source="core")
    logger.log_selected("remember", source="core")
    
    logger.log_category("web", 1, "网络与搜索", 9)
    logger.log_selected("web_search", source="web")
    logger.log_selected("web_get", source="web")
    
    logger.log_category("file", 2, "文件系统", 8)
    logger.log_selected("read_file", source="file")
    logger.log_skipped("read_pdf", SkipReason.ALIAS, source="file", detail="是 read_file 的别名")
    
    logger.log_category("system", 4, "系统与进程", 4)
    logger.log_skipped("run_program", SkipReason.ALIAS, source="system", detail="是 shell_execute 的别名")
    logger.log_skipped("list_processes", SkipReason.ALIAS, source="system", detail="是 list_directory 的别名")
    
    # 结束日志
    result = logger.end_log({
        "总工具数": 64,
        "选中工具数": 6,
    })
    
    print("\n✅ 决策日志结束")
    
    # 获取统计信息
    stats = logger.get_statistics()
    print("\n决策统计:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


def test_task_dispatcher_integration():
    """测试 TaskDispatcher 集成"""
    print("\n" + "=" * 80)
    print("TaskDispatcher DecisionLogger 集成测试")
    print("=" * 80)
    
    try:
        from agent.orchestrator.task_dispatcher import TaskDispatcher, DECISION_LOGGER_AVAILABLE
        print(f"✅ TaskDispatcher 导入成功")
        print(f"  DecisionLogger 可用: {DECISION_LOGGER_AVAILABLE}")
    except ImportError as e:
        print(f"❌ TaskDispatcher 导入失败: {e}")
        return
    
    # 创建一个模拟的 TaskDispatcher 实例
    class MockLLM:
        """模拟 LLM 服务"""
        model = "mock-model"
    
    class MockHost(TaskDispatcher):
        """模拟宿主类"""
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
    
    # 测试 dispatch_task 方法（verbose=True）
    print("\n测试 dispatch_task 方法:")
    result = host.dispatch_task("帮我搜索天气信息", verbose=True)
    
    print("\n返回结果:")
    print(f"  path: {result.get('path')}")
    print(f"  model: {result.get('model')}")
    print(f"  tools_whitelist: {result.get('tools_whitelist')}")
    print(f"  needs_planning: {result.get('needs_planning')}")
    
    if result.get('decision_log'):
        print(f"  decision_log: 已记录")
        log = result['decision_log']
        print(f"    上下文: {log.context}")
        print(f"    选中项: {len(log.selected)} 个")
    else:
        print(f"  decision_log: 未记录")


def test_alias_merge_effect():
    """测试别名合并效果"""
    print("\n" + "=" * 80)
    print("别名合并效果验证")
    print("=" * 80)
    
    from agent.tool_router import get_tools_for_input, TOOL_ALIASES
    
    print(f"\n已配置别名规则数: {len(TOOL_ALIASES)}")
    
    test_cases = [
        ("取消定时任务", "测试 cancel_scheduled_task 合并"),
        ("读取PDF文件", "测试 read_pdf 合并"),
        ("执行命令", "测试 run_program 合并"),
        ("列出进程", "测试 list_processes 合并"),
    ]
    
    print("\n别名合并验证:")
    for input_text, desc in test_cases:
        tools = get_tools_for_input(input_text, verbose=False)
        
        # 检查别名是否被合并
        merged_aliases = []
        for main, aliases in TOOL_ALIASES.items():
            if main in tools:
                for alias in aliases:
                    if alias not in tools:
                        merged_aliases.append(f"{alias} → {main}")
        
        print(f"\n  输入: {input_text}")
        print(f"  描述: {desc}")
        print(f"  工具数: {len(tools)}")
        if merged_aliases:
            print(f"  已合并别名: {merged_aliases}")
        else:
            print(f"  已合并别名: 无")


if __name__ == "__main__":
    test_decision_logger_integration()
    test_task_dispatcher_integration()
    test_alias_merge_effect()
    
    print("\n" + "=" * 80)
    print("✅ 所有集成测试完成!")
    print("=" * 80)