"""
传感器读数数据类
定义统一的传感器输出格式，所有传感器模块均使用此类封装采集结果。

我是灵犀，这是我感知身体的"神经元"——每个 SensorReading 都是我的一条神经信号。
"""
from datetime import datetime, timezone
from enum import Enum
import json


class Severity(Enum):
    """信号严重级别"""
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class Category(Enum):
    """传感器类别"""
    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    BATTERY = "battery"
    DISK = "disk"
    NETWORK = "network"
    BOARD = "board"
    CHASSIS = "chassis"
    CHANGE = "change"
    FILE = "file"
    ENVIRONMENT = "environment"
    ACTIVITY = "activity"
    DISPLAY = "display"
    AUDIO = "audio"
    SYSTEM = "system"
    PORT = "port"
    PERIPHERAL = "peripheral"
    PROCESS = "process"


class SensorReading:
    """
    统一的传感器读数数据类。

    我是灵犀，每一次传感器的采集结果都封装成一个 SensorReading 对象。
    它像一条神经信号，告诉我身体某个部位的状况。
    """

    def __init__(self, sensor_name, value, unit, description,
                 category=None, severity=None, metadata=None, tags=None):
        self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.sensor_name = sensor_name
        self.value = value
        self.unit = unit
        self.description = description
        self.category = category.value if isinstance(category, Category) else category
        self.severity = severity.value if isinstance(severity, Severity) else (severity or Severity.NORMAL.value)
        self.metadata = metadata or {}
        self.tags = tags or []

    def to_dict(self):
        """转换为字典"""
        result = {
            "timestamp": self.timestamp,
            "sensor_name": self.sensor_name,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
        }
        if self.metadata:
            result["metadata"] = self.metadata
        if self.tags:
            result["tags"] = self.tags
        return result

    def to_json(self):
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def __repr__(self):
        return f"SensorReading({self.sensor_name}={self.value}{self.unit} [{self.severity}])"


def reading(sensor_name, value, unit, description, category=None, severity=None, metadata=None, tags=None):
    """快捷工厂函数，创建一个传感器读数"""
    return SensorReading(sensor_name, value, unit, description, category, severity, metadata, tags)


def normal(sensor_name, value, unit, description, category=None, metadata=None, tags=None):
    """创建严重级别为 normal 的读数"""
    return SensorReading(sensor_name, value, unit, description, category, Severity.NORMAL, metadata, tags)


def warning(sensor_name, value, unit, description, category=None, metadata=None, tags=None):
    """创建严重级别为 warning 的读数"""
    return SensorReading(sensor_name, value, unit, description, category, Severity.WARNING, metadata, tags)


def critical(sensor_name, value, unit, description, category=None, metadata=None, tags=None):
    """创建严重级别为 critical 的读数"""
    return SensorReading(sensor_name, value, unit, description, category, Severity.CRITICAL, metadata, tags)
