"""BehaviorController 单元测试"""
import pytest
import logging
from agent.behavior_controller import BehaviorController, BehaviorMode

# 配置测试日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_behavior_controller")


def test_normal_mode_by_default():
    """初始状态应为正常模式"""
    logger.info("测试: 初始状态应为正常模式")
    ctrl = BehaviorController()
    logger.info(f"  当前模式: {ctrl.current_mode}, 预期: {BehaviorMode.NORMAL}")
    assert ctrl.current_mode == BehaviorMode.NORMAL


def test_profile_property():
    """profile 属性应返回当前模式的配置"""
    logger.info("测试: profile 属性应返回当前模式的配置")
    ctrl = BehaviorController()
    profile = ctrl.profile
    logger.info(f"  profile.label: {profile.label}, 预期: 正常模式")
    logger.info(f"  profile.can_accept_tasks: {profile.can_accept_tasks}, 预期: True")
    assert profile.label == "正常模式"
    assert profile.can_accept_tasks is True


class MockReading:
    """模拟传感器读数"""
    def __init__(self, sensor_name, value, description=None):
        self.sensor_name = sensor_name
        self.value = value
        self.description = description or sensor_name
    
    def __repr__(self):
        return f"MockReading({self.sensor_name}={self.value})"


def test_evaluate_cpu_temperature_triggers_safe():
    """CPU 温度超过 85°C 应触发安全模式"""
    logger.info("测试: CPU 温度超过 85°C 应触发安全模式")
    ctrl = BehaviorController()
    readings = [MockReading("cpu_temperature", 90)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.SAFE}")
    assert result == BehaviorMode.SAFE


def test_evaluate_battery_low_triggers_power_save():
    """电量低于 15% 应触发省电模式"""
    logger.info("测试: 电量低于 15% 应触发省电模式")
    ctrl = BehaviorController()
    readings = [MockReading("battery_percent", 10)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.POWER_SAVE}")
    assert result == BehaviorMode.POWER_SAVE


def test_evaluate_memory_high_triggers_memory_compact():
    """内存占用超过 90% 应触发整理模式"""
    logger.info("测试: 内存占用超过 90% 应触发整理模式")
    ctrl = BehaviorController()
    readings = [MockReading("memory_usage", 95)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.MEMORY_COMPACT}")
    assert result == BehaviorMode.MEMORY_COMPACT


def test_evaluate_disk_low_triggers_warning():
    """磁盘空间低于 10% 应触发预警模式"""
    logger.info("测试: 磁盘空间低于 10% 应触发预警模式")
    ctrl = BehaviorController()
    readings = [MockReading("disk_free", 5)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.WARNING}")
    assert result == BehaviorMode.WARNING


def test_evaluate_multiple_conditions_priority():
    """多个条件满足时应取优先级最高的模式"""
    logger.info("测试: 多个条件满足时应取优先级最高的模式")
    ctrl = BehaviorController()
    readings = [
        MockReading("cpu_temperature", 90),  # SAFE (优先级 50)
        MockReading("memory_usage", 95),     # MEMORY_COMPACT (优先级 30)
    ]
    logger.info(f"  输入传感器数据: {readings}")
    logger.info(f"  SAFE 优先级 50, MEMORY_COMPACT 优先级 30")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.SAFE}")
    assert result == BehaviorMode.SAFE


def test_evaluate_no_condition_returns_normal():
    """无触发条件时应返回正常模式"""
    logger.info("测试: 无触发条件时应返回正常模式")
    ctrl = BehaviorController()
    readings = [MockReading("cpu_temperature", 50)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.NORMAL}")
    assert result == BehaviorMode.NORMAL


def test_evaluate_empty_readings():
    """空读数列表应返回正常模式"""
    logger.info("测试: 空读数列表应返回正常模式")
    ctrl = BehaviorController()
    logger.info(f"  输入传感器数据: []")
    result = ctrl.evaluate([])
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.NORMAL}")
    assert result == BehaviorMode.NORMAL


def test_can_execute_in_memory_compact():
    """整理模式下不应接受新任务"""
    logger.info("测试: 整理模式下不应接受新任务")
    ctrl = BehaviorController()
    ctrl._current_mode = BehaviorMode.MEMORY_COMPACT
    logger.info(f"  当前模式: {ctrl.current_mode}")
    can_exec, reason = ctrl.can_execute("测试任务")
    logger.info(f"  能否执行: {can_exec}, 原因: {reason}")
    assert can_exec is False
    assert "整理" in reason


def test_can_execute_high_energy_task_in_safe_mode():
    """安全模式下应拒绝高耗能任务"""
    logger.info("测试: 安全模式下应拒绝高耗能任务")
    ctrl = BehaviorController()
    ctrl._current_mode = BehaviorMode.SAFE
    logger.info(f"  当前模式: {ctrl.current_mode}")
    can_exec, reason = ctrl.can_execute("编译大型项目")
    logger.info(f"  任务: 编译大型项目, 能否执行: {can_exec}, 原因: {reason}")
    assert can_exec is False
    assert "高耗能" in reason


def test_can_execute_normal_task_in_safe_mode():
    """安全模式下应允许普通任务"""
    logger.info("测试: 安全模式下应允许普通任务")
    ctrl = BehaviorController()
    ctrl._current_mode = BehaviorMode.SAFE
    logger.info(f"  当前模式: {ctrl.current_mode}")
    can_exec, reason = ctrl.can_execute("查询天气")
    logger.info(f"  任务: 查询天气, 能否执行: {can_exec}, 原因: {reason}")
    assert can_exec is True
    assert reason is None


def test_get_history():
    """应返回模式切换历史"""
    logger.info("测试: 应返回模式切换历史")
    ctrl = BehaviorController()
    readings1 = [MockReading("cpu_temperature", 90)]
    ctrl.evaluate(readings1)
    readings2 = [MockReading("memory_usage", 95)]
    ctrl.evaluate(readings2)
    
    history = ctrl.get_history()
    logger.info(f"  历史记录长度: {len(history)}, 预期: 2")
    logger.info(f"  第一条记录: {history[0]}")
    assert len(history) == 2
    assert history[0]["from"] == "normal"
    assert history[0]["to"] == "safe"


def test_get_history_limit():
    """get_history 应支持限制返回数量"""
    logger.info("测试: get_history 应支持限制返回数量")
    ctrl = BehaviorController()
    # 确保每次评估都触发状态变化（温度超过85度）
    for i in range(15):
        # 交替触发 SAFE 和 NORMAL 模式
        temp = 90 if i % 2 == 0 else 50
        readings = [MockReading("cpu_temperature", temp)]
        ctrl.evaluate(readings)
    
    history = ctrl.get_history(limit=5)
    logger.info(f"  历史记录长度(限制5条): {len(history)}, 预期: 5")
    assert len(history) == 5


def test_get_status_text_normal():
    """正常模式应返回描述文本"""
    logger.info("测试: 正常模式应返回描述文本")
    ctrl = BehaviorController()
    status = ctrl.get_status_text()
    logger.info(f"  状态文本: {status}")
    assert "感觉很好" in status


def test_get_status_text_with_reason():
    """有触发原因时应包含原因信息"""
    logger.info("测试: 有触发原因时应包含原因信息")
    ctrl = BehaviorController()
    readings = [MockReading("cpu_temperature", 90)]
    ctrl.evaluate(readings)
    status = ctrl.get_status_text()
    logger.info(f"  状态文本: {status}")
    assert "发烧" in status
    assert "过高" in status


def test_evaluate_battery_percentage_alias():
    """支持 battery_percentage 作为电池传感器名称"""
    logger.info("测试: 支持 battery_percentage 作为电池传感器名称")
    ctrl = BehaviorController()
    readings = [MockReading("battery_percentage", 10)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.POWER_SAVE}")
    assert result == BehaviorMode.POWER_SAVE


def test_evaluate_cpu_usage_alias():
    """支持 cpu_usage 作为 CPU 温度传感器名称"""
    logger.info("测试: 支持 cpu_usage 作为 CPU 温度传感器名称")
    ctrl = BehaviorController()
    readings = [MockReading("cpu_usage", 90)]
    logger.info(f"  输入传感器数据: {readings}")
    result = ctrl.evaluate(readings)
    logger.info(f"  评估结果: {result}, 预期: {BehaviorMode.SAFE}")
    assert result == BehaviorMode.SAFE


def test_evaluate_returns_to_normal():
    """条件恢复后应返回正常模式"""
    logger.info("测试: 条件恢复后应返回正常模式")
    ctrl = BehaviorController()
    readings1 = [MockReading("cpu_temperature", 90)]
    ctrl.evaluate(readings1)
    logger.info(f"  第一步 - CPU温度90度: 当前模式 {ctrl.current_mode}")
    assert ctrl.current_mode == BehaviorMode.SAFE
    
    readings2 = [MockReading("cpu_temperature", 50)]
    result = ctrl.evaluate(readings2)
    logger.info(f"  第二步 - CPU温度50度: 当前模式 {result}")
    assert result == BehaviorMode.NORMAL


def test_profile_properties():
    """验证各模式的配置属性"""
    logger.info("测试: 验证各模式的配置属性")
    ctrl = BehaviorController()
    
    ctrl._current_mode = BehaviorMode.NORMAL
    logger.info(f"  NORMAL模式: can_accept_tasks={ctrl.profile.can_accept_tasks}, 预期: True")
    assert ctrl.profile.can_accept_tasks is True
    
    ctrl._current_mode = BehaviorMode.MEMORY_COMPACT
    logger.info(f"  MEMORY_COMPACT模式: can_accept_tasks={ctrl.profile.can_accept_tasks}, 预期: False")
    assert ctrl.profile.can_accept_tasks is False
    
    ctrl._current_mode = BehaviorMode.POWER_SAVE
    logger.info(f"  POWER_SAVE模式: use_lightweight_logic={ctrl.profile.use_lightweight_logic}, 预期: True")
    logger.info(f"  POWER_SAVE模式: enable_reflection={ctrl.profile.enable_reflection}, 预期: False")
    assert ctrl.profile.use_lightweight_logic is True
    assert ctrl.profile.enable_reflection is False