#!/usr/bin/env python3
"""
通用测试模板

使用方法：
1. 将此文件复制到新项目中
2. 修改测试函数以适应新项目
3. 运行测试：python test_template.py

测试覆盖：
- ✅ 日志系统测试
- ✅ 安全监控器测试
- ✅ 安全执行包装器测试
- ✅ 模块导出测试
- ✅ 主功能集成测试
"""

import sys
import os

# 添加当前目录到路径（模板测试用）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_test(test_name, test_func):
    """
    运行单个测试并返回结果

    Args:
        test_name: 测试名称
        test_func: 测试函数

    Returns:
        是否通过
    """
    try:
        result = test_func()
        print(f"✅ {test_name}")
        return True
    except Exception as e:
        print(f"❌ {test_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_logging():
    """测试日志系统"""
    print("测试日志系统...")
    
    from logging_utils_template import setup_logging, get_logger, LOGGING_CONFIG
    
    # 测试默认配置
    logger = setup_logging(debug_mode=False)
    assert logger is not None
    
    # 测试调试模式
    logger = setup_logging(debug_mode=True)
    assert logger is not None
    
    # 测试自定义配置
    custom_config = LOGGING_CONFIG.copy()
    custom_config['module_levels'] = {'test_module': 'DEBUG'}
    logger = setup_logging(config=custom_config)
    assert logger is not None
    
    # 测试日志记录器获取
    test_logger = get_logger("test_module")
    assert test_logger is not None
    
    # 测试日志输出
    test_logger.debug("DEBUG 测试")
    test_logger.info("INFO 测试")
    test_logger.warning("WARNING 测试")
    test_logger.error("ERROR 测试")
    
    return True


def test_safety_monitor():
    """测试安全监控器"""
    print("测试安全监控器...")
    
    from logging_utils_template import SafetyMonitor
    
    # 测试正常迭代
    monitor = SafetyMonitor(max_iterations_per_minute=100)
    result = monitor.record_iteration("normal_task")
    assert result is True, "正常迭代应该返回 True"
    
    # 测试快速循环检测
    fast_monitor = SafetyMonitor(max_iterations_per_minute=5)
    for i in range(6):
        result = fast_monitor.record_iteration("fast_loop")
        if i < 5:
            assert result is True, f"第{i+1}次迭代应该正常"
        else:
            assert result is False, "第6次迭代应该被拒绝"
    
    # 测试状态卡死检测
    import time
    stuck_monitor = SafetyMonitor(state_stuck_threshold_seconds=1)
    stuck_monitor.check_state("stuck_task", "running")
    time.sleep(1.1)
    result = stuck_monitor.check_state("stuck_task", "running")
    assert result is False, "状态卡死应该被检测"
    
    # 测试重置功能
    fast_monitor.reset("fast_loop")
    result = fast_monitor.record_iteration("fast_loop")
    assert result is True, "重置后应该正常"
    
    # 测试统计信息
    stats = monitor.get_stats()
    assert 'tracked_tasks' in stats
    assert 'max_iterations_per_minute' in stats
    assert 'state_stuck_threshold' in stats
    
    return True


def test_safe_execute():
    """测试安全执行包装器"""
    print("测试安全执行包装器...")
    
    from logging_utils_template import safe_execute
    
    # 测试正常执行
    def quick_task():
        return "任务完成"
    result = safe_execute(quick_task, timeout=1.0)
    assert result == "任务完成", "正常执行应该返回结果"
    
    # 测试超时保护
    def slow_task():
        import time
        time.sleep(3)
        return "任务完成"
    result = safe_execute(slow_task, timeout=0.5, default_return="超时了")
    assert result == "超时了", "超时应该返回默认值"
    
    # 测试异常传播
    def failing_task():
        raise ValueError("测试异常")
    try:
        safe_execute(failing_task, timeout=1.0)
        assert False, "应该抛出异常"
    except ValueError as e:
        assert "测试异常" in str(e)
    
    # 测试标识符参数
    result = safe_execute(quick_task, timeout=1.0, identifier="test_identifier")
    assert result == "任务完成"
    
    return True


def test_module_export():
    """测试模块导出"""
    print("测试模块导出...")
    
    from logging_utils_template import (
        LOGGING_CONFIG,
        setup_logging,
        get_logger,
        SafetyMonitor,
        safe_execute,
        TimeoutException,
        LoopDetectionException,
        StateStuckException,
    )
    
    # 验证所有导出
    assert LOGGING_CONFIG is not None
    assert setup_logging is not None
    assert get_logger is not None
    assert SafetyMonitor is not None
    assert safe_execute is not None
    assert TimeoutException is not None
    assert LoopDetectionException is not None
    assert StateStuckException is not None
    
    return True


def test_integration():
    """测试集成功能"""
    print("测试集成功能...")
    
    from logging_utils_template import (
        setup_logging,
        get_logger,
        SafetyMonitor,
        safe_execute,
    )
    
    # 配置日志
    setup_logging(debug_mode=True)
    logger = get_logger("integration_test")
    
    # 创建监控器
    monitor = SafetyMonitor()
    
    # 执行安全任务
    def integrated_task():
        # 记录迭代
        if not monitor.record_iteration("integration_task"):
            raise Exception("循环检测")
        
        # 检查状态
        if not monitor.check_state("integration_task", "running"):
            raise Exception("状态卡死")
        
        return "集成测试成功"
    
    result = safe_execute(integrated_task, timeout=5.0)
    assert result == "集成测试成功"
    
    logger.info("集成测试完成")
    
    return True


def run_all_tests():
    """运行所有测试"""
    print("=" * 70)
    print("运行通用测试套件")
    print("=" * 70)
    
    tests = [
        ("日志系统", test_logging),
        ("安全监控器", test_safety_monitor),
        ("安全执行包装器", test_safe_execute),
        ("模块导出", test_module_export),
        ("集成测试", test_integration),
    ]
    
    results = []
    for name, func in tests:
        results.append(run_test(name, func))
    
    # 打印总结
    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)
    
    passed = sum(results)
    total = len(results)
    
    for i, (name, _) in enumerate(tests):
        status = "✅ 通过" if results[i] else "❌ 失败"
        print(f"  {name:20s}: {status}")
    
    print("=" * 70)
    
    if passed == total:
        print(f"\n🎉 所有 {total} 个测试通过！")
        return 0
    else:
        print(f"\n⚠️ {passed}/{total} 测试通过，{total - passed} 测试失败")
        return 1


if __name__ == "__main__":
    try:
        exit_code = run_all_tests()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n测试被中断")
        sys.exit(1)
    except Exception as e:
        print(f"测试运行异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
