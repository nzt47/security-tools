"""BehaviorController 调试测试脚本 - 带详细日志打印"""
import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from behavior_controller import BehaviorController, BehaviorMode

# 配置详细日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger("BehaviorControllerDebug")


class MockReading:
    """模拟传感器读数"""
    def __init__(self, sensor_name, value, description=None):
        self.sensor_name = sensor_name
        self.value = value
        self.description = description or sensor_name
    
    def __repr__(self):
        return f"MockReading(sensor_name='{self.sensor_name}', value={self.value})"


def run_debug_test():
    """运行调试测试，展示状态切换逻辑"""
    logger.info("="*60)
    logger.info("开始 BehaviorController 调试测试")
    logger.info("="*60)
    
    # 初始化控制器
    ctrl = BehaviorController()
    logger.info(f"初始模式: {ctrl.current_mode} ({ctrl.profile.label})")
    logger.info(f"当前配置: can_accept_tasks={ctrl.profile.can_accept_tasks}, "
                f"use_lightweight_logic={ctrl.profile.use_lightweight_logic}, "
                f"enable_reflection={ctrl.profile.enable_reflection}")
    
    # 测试场景列表
    test_scenarios = [
        ("场景1: 正常状态 - CPU温度正常", [MockReading("cpu_temperature", 50)]),
        ("场景2: CPU过热 - 触发安全模式", [MockReading("cpu_temperature", 90)]),
        ("场景3: 电量不足 - 触发省电模式", [MockReading("battery_percent", 10)]),
        ("场景4: 内存过高 - 触发整理模式", [MockReading("memory_usage", 95)]),
        ("场景5: 磁盘不足 - 触发预警模式", [MockReading("disk_free", 5)]),
        ("场景6: 多条件同时满足 - 取最高优先级", [
            MockReading("cpu_temperature", 90),  # SAFE (优先级 50)
            MockReading("memory_usage", 95),     # MEMORY_COMPACT (优先级 30)
            MockReading("battery_percent", 10),  # POWER_SAVE (优先级 40)
        ]),
        ("场景7: 恢复正常状态", [MockReading("cpu_temperature", 50)]),
    ]
    
    for scenario_name, readings in test_scenarios:
        logger.info("-"*60)
        logger.info(f"测试场景: {scenario_name}")
        logger.info(f"输入传感器数据: {readings}")
        
        # 记录当前状态
        prev_mode = ctrl.current_mode
        prev_profile = ctrl.profile
        
        # 执行评估
        try:
            result = ctrl.evaluate(readings)
            
            # 记录结果
            logger.info(f"评估结果: {result} ({ctrl.profile.label})")
            logger.info(f"状态变化: {prev_mode} -> {result}")
            
            if prev_mode != result:
                # 从历史记录中获取最后一条记录的原因
                history = ctrl.get_history()
                if history:
                    reasons = history[-1].get('reasons', '无')
                    logger.info(f"触发原因: {reasons}")
            
            logger.info(f"当前配置: can_accept_tasks={ctrl.profile.can_accept_tasks}, "
                        f"use_lightweight_logic={ctrl.profile.use_lightweight_logic}, "
                        f"enable_reflection={ctrl.profile.enable_reflection}")
            
            # 检查是否可以执行任务
            test_task = "执行测试任务"
            can_exec, reason = ctrl.can_execute(test_task)
            logger.info(f"能否执行任务 '{test_task}': {can_exec}" + (f" (原因: {reason})" if reason else ""))
            
        except Exception as e:
            logger.error(f"评估失败: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"堆栈信息:\n{traceback.format_exc()}")
    
    # 打印历史记录
    logger.info("="*60)
    logger.info("模式切换历史记录:")
    history = ctrl.get_history()
    for i, entry in enumerate(history):
        logger.info(f"  {i+1}. {entry['from']} -> {entry['to']} | 原因: {entry.get('reasons', '无')}")
    
    logger.info("="*60)
    logger.info("调试测试完成")
    logger.info("="*60)


if __name__ == "__main__":
    run_debug_test()