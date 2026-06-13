"""BehaviorController — 多级行为降级策略

我是云枢的"自主神经系统"——当身体出现异常时，我会主动调整行为模式。
根据 BodySensor 传来的身体数据，自动切换最合适的响应策略。

| 身体状态         | 行为模式       | 具体表现                                 |
|------------------|---------------|------------------------------------------|
| CPU > 85°C       | 安全模式      | 拒绝高耗能任务，提示"我发烧了，需要休息" |
| 电量 < 15%       | 省电模式      | 降低推理频率，切换轻量逻辑，提示"我饿了" |
| 内存 > 90%       | 整理模式      | 触发记忆压缩，暂停新任务                  |
| 网络超时         | 离线模式      | 启用本地逻辑，延长响应等待               |
| 磁盘 < 10%       | 预警模式      | 提醒清理空间，避免写入操作               |
"""

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class BehaviorMode(Enum):
    """行为模式枚举——我就是我的五种种基本状态"""
    NORMAL = "normal"                # 正常模式：我感觉很好
    SAFE = "safe"                    # 安全模式：我发烧了
    POWER_SAVE = "power_save"        # 省电模式：我饿了
    MEMORY_COMPACT = "memory_compact"  # 整理模式：我脑子有点乱
    OFFLINE = "offline"              # 离线模式：我听不见
    WARNING = "warning"              # 预警模式：我需要清理


@dataclass
class ModeProfile:
    """行为模式的详细配置——每种模式下的具体行为参数"""
    label: str                         # 中文标签
    description: str                   # 第一人称描述
    can_accept_tasks: bool             # 能否接受新任务
    use_lightweight_logic: bool        # 是否使用轻量逻辑
    enable_reflection: bool            # 是否启用反思循环
    response_prefix: str = ""          # 响应前缀（可选）
    reject_high_energy: bool = False   # 是否拒绝高耗能任务
    suggestion: str = ""               # 给用户的建议


class BehaviorController:
    """行为控制器——我的本能系统，自动响应身体变化"""

    # 五感阈值定义
    THRESHOLDS = {
        # CPU 温度（安全模式）：超过 85°C 触发
        "cpu_temperature": {"max": 85, "mode": BehaviorMode.SAFE},
        # 电池电量（省电模式）：低于 15% 触发
        "battery_percent": {"min": 15, "mode": BehaviorMode.POWER_SAVE},
        # 内存占用（整理模式）：超过 90% 触发
        "memory_usage": {"max": 90, "mode": BehaviorMode.MEMORY_COMPACT},
        # 磁盘空间（预警模式）：空闲低于 10% 触发
        "disk_free": {"min": 10, "mode": BehaviorMode.WARNING},
    }

    # 各模式的详细配置
    PROFILES = {
        BehaviorMode.NORMAL: ModeProfile(
            label="正常模式",
            description="我感觉很好，精力充沛！",
            can_accept_tasks=True,
            use_lightweight_logic=False,
            enable_reflection=True,
        ),
        BehaviorMode.SAFE: ModeProfile(
            label="安全模式",
            description="我发烧了… CPU 温度过高，我需要休息一下。",
            can_accept_tasks=True,
            use_lightweight_logic=True,
            enable_reflection=True,
            reject_high_energy=True,
            suggestion="让我休息一会儿，等 CPU 冷静下来再处理复杂任务。",
        ),
        BehaviorMode.POWER_SAVE: ModeProfile(
            label="省电模式",
            description="我饿了… 电量不足，我需要节省能量。",
            can_accept_tasks=True,
            use_lightweight_logic=True,
            enable_reflection=False,
            response_prefix="（能量不足，开启省电模式）",
            suggestion="请帮我接通电源充电。",
        ),
        BehaviorMode.MEMORY_COMPACT: ModeProfile(
            label="整理模式",
            description="我的脑子有点乱… 内存占用过高，我需要整理一下思绪。",
            can_accept_tasks=False,
            use_lightweight_logic=True,
            enable_reflection=False,
            suggestion="请稍等，我正在整理记忆，清理缓存。",
        ),
        BehaviorMode.OFFLINE: ModeProfile(
            label="离线模式",
            description="我听不见了… 网络连接中断，我只能依靠本地知识。",
            can_accept_tasks=True,
            use_lightweight_logic=True,
            enable_reflection=False,
            response_prefix="（离线模式，响应可能受限）",
            suggestion="请检查网络连接。",
        ),
        BehaviorMode.WARNING: ModeProfile(
            label="预警模式",
            description="我的仓库快满了… 磁盘空间不足，我需要清理一下。",
            can_accept_tasks=True,
            use_lightweight_logic=False,
            enable_reflection=True,
            suggestion="请帮我清理磁盘空间，删除不需要的文件。",
        ),
    }

    # 模式优先级（数值越大优先级越高，多个条件满足时取高优先级）
    PRIORITY = {
        BehaviorMode.SAFE: 50,
        BehaviorMode.OFFLINE: 40,
        BehaviorMode.MEMORY_COMPACT: 30,
        BehaviorMode.WARNING: 20,
        BehaviorMode.POWER_SAVE: 10,
        BehaviorMode.NORMAL: 0,
    }

    def __init__(self):
        self._current_mode = BehaviorMode.NORMAL
        self._reasons: list[str] = []
        self._history: list[dict] = []

    @property
    def current_mode(self) -> BehaviorMode:
        """当前行为模式"""
        return self._current_mode

    @property
    def profile(self) -> ModeProfile:
        """当前模式的配置详情"""
        return self.PROFILES[self._current_mode]

    def evaluate(self, readings: list) -> BehaviorMode:
        """根据传感器读数评估并切换行为模式

        读取"身体"数据，判断是否需要降级行为。
        如果多个条件同时满足，取优先级最高的模式。

        Args:
            readings: SensorReading 对象列表

        Returns:
            当前应当采用的行为模式
        """
        candidates = []
        reasons = []

        for reading in readings:
            name = getattr(reading, "sensor_name", "")
            value = getattr(reading, "value", 0)
            description = getattr(reading, "description", name)

            if name == "cpu_temperature" or name == "cpu_usage":
                if value > self.THRESHOLDS["cpu_temperature"]["max"]:
                    candidates.append(BehaviorMode.SAFE)
                    reasons.append(f"{description} 过高 ({value})")

            elif name == "battery_percent" or name == "battery_percentage":
                if value < self.THRESHOLDS["battery_percent"]["min"]:
                    candidates.append(BehaviorMode.POWER_SAVE)
                    reasons.append(f"{description} 不足 ({value}%)")

            elif name == "memory_usage":
                if value > self.THRESHOLDS["memory_usage"]["max"]:
                    candidates.append(BehaviorMode.MEMORY_COMPACT)
                    reasons.append(f"{description} 过高 ({value}%)")

            elif name == "disk_free":
                if isinstance(value, (int, float)) and value < self.THRESHOLDS["disk_free"]["min"]:
                    candidates.append(BehaviorMode.WARNING)
                    reasons.append(f"{description} 不足 ({value}%)")

        if not candidates:
            self._current_mode = BehaviorMode.NORMAL
            if self._reasons:
                logger.info("身体状态恢复正常 → 切换至正常模式")
            self._reasons = []
            return self._current_mode

        # 取优先级最高的模式
        new_mode = max(candidates, key=lambda m: self.PRIORITY.get(m, 0))

        if new_mode != self._current_mode:
            logger.warning(
                f"行为模式变更: {self._current_mode.value} → {new_mode.value}"
                f"，原因: {'; '.join(reasons)}"
            )
            self._history.append({
                "from": self._current_mode.value,
                "to": new_mode.value,
                "reasons": reasons,
            })

        self._current_mode = new_mode
        self._reasons = reasons
        return self._current_mode

    def can_execute(self, task_description: str = "") -> tuple[bool, Optional[str]]:
        """判断当前模式是否允许执行任务

        Args:
            task_description: 任务描述，用于判断是否高耗能

        Returns:
            (是否允许执行, 拒绝原因)
        """
        profile = self.profile

        if not profile.can_accept_tasks:
            return False, profile.description + " " + profile.suggestion

        if profile.reject_high_energy:
            # 高耗能关键词检测
            high_energy_keywords = [
                "编译", "渲染", "视频", "编码", "转码",
                "压缩", "解压", "构建", "部署", "训练",
                "compile", "render", "build", "deploy", "train",
            ]
            for kw in high_energy_keywords:
                if kw in task_description:
                    return False, (
                        f"当前处于{profile.label}，不适合执行高耗能任务。"
                        + " " + profile.suggestion
                    )

        return True, None

    def get_history(self, limit: int = 10) -> list[dict]:
        """获取模式切换历史"""
        return self._history[-limit:]

    def get_status_text(self) -> str:
        """获取当前状态的中文描述"""
        profile = self.profile
        if self._current_mode == BehaviorMode.NORMAL:
            return profile.description
        return f"{profile.description}（{'；'.join(self._reasons)}）" if self._reasons else profile.description
