# cognitive/translator.py
import logging

logger = logging.getLogger(__name__)


class Translator:
    """拟人化翻译引擎。

    将传感器数值根据配置规则翻译为第一人称拟人化描述。
    """

    def __init__(self, config):
        self.config = config

    def translate(self, reading: dict) -> str:
        """将单条传感器读数翻译为拟人化描述。

        匹配逻辑：按 sensor_name 查找规则 → 遍历 thresholds 找值所在区间 → 返回 message。
        区间约定：min <= value < max（左闭右开）。
        """
        rule = self.config.get_rule(reading.get("sensor_name", ""))
        if not rule or "thresholds" not in rule:
            return self._fallback(reading)

        value = reading.get("value", 0)
        for threshold in rule["thresholds"]:
            lo = threshold.get("min", float("-inf"))
            hi = threshold.get("max", float("inf"))
            if lo <= value < hi:
                return threshold["message"]

        return self._fallback(reading)

    def translate_all(self, readings: list[dict]) -> list[str]:
        """批量翻译多条传感器读数"""
        return [self.translate(r) for r in readings]

    def get_status_line(self, readings: list[dict]) -> str:
        """生成一句话综合状态摘要"""
        descriptions = self.translate_all(readings)
        alerts = []
        normals = []
        for r, desc in zip(readings, descriptions):
            if r.get("severity") in ("warning", "critical"):
                alerts.append(desc)
            else:
                normals.append(desc)
        parts = []
        if alerts:
            parts.append("；".join(alerts[:3]))
        if normals:
            parts.append("；".join(normals[:2]))
        return "，".join(parts) if parts else "一切正常"

    def _fallback(self, reading: dict) -> str:
        """无匹配规则时的通用描述"""
        desc = reading.get("description", reading.get("sensor_name", "未知"))
        value = reading.get("value", "")
        unit = reading.get("unit", "")
        return f"{desc}: {value}{unit}"
