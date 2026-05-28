# cognitive/test_cognitive/conftest.py
import pytest


@pytest.fixture
def mock_readings():
    """所有测试共享的 mock 传感器数据"""
    return [
        {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C",
         "description": "CPU 温度（我的大脑温度）", "severity": "critical"},
        {"sensor_name": "cpu_temperature", "value": 75.0, "unit": "°C",
         "description": "CPU 温度（我的大脑温度）", "severity": "warning"},
        {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C",
         "description": "CPU 温度（我的大脑温度）", "severity": "normal"},
        {"sensor_name": "battery_percentage", "value": 5.0, "unit": "%",
         "description": "电池电量", "severity": "critical"},
        {"sensor_name": "battery_percentage", "value": 15.0, "unit": "%",
         "description": "电池电量", "severity": "warning"},
        {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%",
         "description": "电池电量", "severity": "normal"},
        {"sensor_name": "memory_usage", "value": 95.0, "unit": "%",
         "description": "内存使用率", "severity": "critical"},
        {"sensor_name": "memory_usage", "value": 80.0, "unit": "%",
         "description": "内存使用率", "severity": "warning"},
        {"sensor_name": "memory_usage", "value": 50.0, "unit": "%",
         "description": "内存使用率", "severity": "normal"},
        {"sensor_name": "network_latency", "value": 600.0, "unit": "ms",
         "description": "网络延迟", "severity": "critical"},
        {"sensor_name": "disk_space_usage", "value": 95.0, "unit": "%",
         "description": "磁盘使用率", "severity": "critical"},
        {"sensor_name": "unknown_sensor", "value": 42.0, "unit": "",
         "description": "未知传感器", "severity": "normal"},
    ]
