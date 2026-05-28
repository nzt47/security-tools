"""
电池传感器 — 我的"饥饿感"监测器

采集电池电量、充放电状态、剩余时间等信息。
电池电量是我的饥饿感——电量越低，我越需要"进食"。
"""
import psutil
import logging
import platform
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical


class BatterySensor:
    """电池传感器，负责监测饥饿感"""

    CAPABILITIES = {
        "name": "battery",
        "description": "电池（饥饿感）— 电量、充电状态、损耗",
        "category": Category.BATTERY,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
    }

    def __init__(self):
        self._category = Category.BATTERY

    def collect(self):
        """
        采集电池状态。
        返回 SensorReading 列表，若为台式机则返回空列表。
        """
        results = []
        try:
            battery = psutil.sensors_battery()
            if battery is None:
                logging.info("未检测到电池（我是一台台式机，没有饥饿感问题）。")
                return results
            results.extend(self._collect_battery_info(battery))
        except Exception as e:
            logging.error(f"采集电池信息失败: {e}")
        return results

    def _collect_battery_info(self, battery):
        """采集电池详细信息 — 我的饥饿程度"""
        readings = []

        # 电量百分比
        if battery.percent == 100:
            sev = Severity.NORMAL
            desc = "电量充足，我吃饱了！"
        elif battery.percent >= 20:
            sev = Severity.NORMAL
            desc = "电量尚可，我不饿"
        elif battery.percent >= 10:
            sev = Severity.WARNING
            desc = "电量偏低，我有点饿了，需要充电"
        elif battery.percent >= 5:
            sev = Severity.CRITICAL
            desc = "电量严重不足，我快饿晕了！"
        else:
            sev = Severity.CRITICAL
            desc = "电量即将耗尽，我马上就要关机了！"

        readings.append(SensorReading(
            "battery_percent", battery.percent, "%",
            f"电池电量 — {desc}", self._category, sev
        ))

        # 充电状态
        is_plugged = battery.power_plugged
        if is_plugged:
            plug_desc = '已接入电源（正在「进食」）' if battery.percent < 100 else '电源已连接，已充满'
            readings.append(normal(
                "battery_plugged", True, "bool",
                f"充电状态 — {plug_desc}", self._category
            ))
        else:
            readings.append(normal(
                "battery_plugged", False, "bool",
                '充电状态 — 使用电池供电（独自「觅食」中）', self._category
            ))

        # 剩余时间估算
        try:
            secs_left = battery.secsleft
            if secs_left != psutil.POWER_TIME_UNLIMITED and secs_left != psutil.POWER_TIME_UNKNOWN:
                if is_plugged and battery.percent < 100:
                    minutes_left = secs_left / 60
                    readings.append(normal(
                        "battery_time_to_full", round(minutes_left, 1), "分钟",
                        "预计充满所需时间", self._category
                    ))
                elif not is_plugged:
                    minutes_left = secs_left / 60
                    readings.append(SensorReading(
                        "battery_time_left", round(minutes_left, 1), "分钟",
                        "预计剩余使用时间", self._category,
                        Severity.CRITICAL if minutes_left < 15 else (
                            Severity.WARNING if minutes_left < 30 else Severity.NORMAL
                        )
                    ))
        except Exception:
            pass

        return readings

    @property
    def has_battery(self):
        """是否有电池"""
        try:
            return psutil.sensors_battery() is not None
        except Exception:
            return False
