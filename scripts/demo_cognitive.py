#!/usr/bin/env python3
"""认知系统演示脚本 - 展示 PromptInjector 的实际效果"""

import logging
import sys

# 设置详细日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S"
)

# 创建演示专用日志记录器
demo_logger = logging.getLogger('demo')
demo_logger.setLevel(logging.INFO)

from cognitive import PromptInjector

def log_sensor_mapping(sensor_name, value, unit, severity, description):
    """记录传感器值到拟人化描述的映射"""
    demo_logger.info(f"[映射] sensor={sensor_name}, value={value}{unit}, severity={severity} → {description}")

def demo():
    print("\n" + "="*60)
    print("🧠 认知系统演示")
    print("="*60 + "\n")
    
    # 创建注入器实例
    demo_logger.info("[初始化] 开始创建 PromptInjector 实例")
    injector = PromptInjector()
    demo_logger.info("[初始化] PromptInjector 初始化完成")
    print("✅ 认知引擎初始化完成\n")
    
    # 演示场景 1: 正常状态
    print("┌─────────────────────────────────────────────────────┐")
    print("│ 场景 1: 身体状态良好                               │")
    print("└─────────────────────────────────────────────────────┘")
    demo_logger.info("[场景1] 开始处理正常状态传感器数据")
    
    normal_readings = [
        {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        {"sensor_name": "battery_percentage", "value": 80.0, "unit": "%", "severity": "normal"},
        {"sensor_name": "memory_usage", "value": 45.0, "unit": "%", "severity": "normal"},
        {"sensor_name": "network_latency", "value": 50.0, "unit": "ms", "severity": "normal"},
        {"sensor_name": "disk_space_usage", "value": 40.0, "unit": "%", "severity": "normal"},
    ]
    
    # 记录输入传感器数据
    for reading in normal_readings:
        demo_logger.debug(f"[场景1] 输入数据: {reading}")
    
    prompt = injector.inject(normal_readings)
    demo_logger.info("[场景1] 系统提示词生成完成，长度: %d 字符", len(prompt))
    
    print("生成的系统提示词:\n")
    print(prompt)
    print("\n" + "-"*60 + "\n")
    
    # 演示场景 2: 警告状态
    print("┌─────────────────────────────────────────────────────┐")
    print("│ 场景 2: 身体状态警告                               │")
    print("└─────────────────────────────────────────────────────┘")
    demo_logger.info("[场景2] 开始处理警告状态传感器数据")
    
    warning_readings = [
        {"sensor_name": "cpu_temperature", "value": 75.0, "unit": "°C", "severity": "warning"},
        {"sensor_name": "battery_percentage", "value": 15.0, "unit": "%", "severity": "warning"},
        {"sensor_name": "memory_usage", "value": 85.0, "unit": "%", "severity": "warning"},
    ]
    
    summary = injector.get_summary(warning_readings)
    demo_logger.info("[场景2] 状态摘要生成: %s", summary)
    print(f"状态摘要: {summary}\n")
    
    rejected, reason = injector.should_reject_task(warning_readings)
    demo_logger.info("[场景2] 任务拒绝判断: rejected=%s, reason=%s", rejected, reason)
    print(f"任务拒绝: {'是' if rejected else '否'}")
    print(f"原因: {reason}")
    print("\n" + "-"*60 + "\n")
    
    # 演示场景 3: 严重状态
    print("┌─────────────────────────────────────────────────────┐")
    print("│ 场景 3: 身体状态严重                               │")
    print("└─────────────────────────────────────────────────────┘")
    demo_logger.info("[场景3] 开始处理严重状态传感器数据")
    
    critical_readings = [
        {"sensor_name": "cpu_temperature", "value": 90.0, "unit": "°C", "severity": "critical"},
        {"sensor_name": "battery_percentage", "value": 5.0, "unit": "%", "severity": "critical"},
    ]
    
    summary = injector.get_summary(critical_readings)
    demo_logger.info("[场景3] 状态摘要生成: %s", summary)
    print(f"状态摘要: {summary}\n")
    
    rejected, reason = injector.should_reject_task(critical_readings)
    demo_logger.info("[场景3] 任务拒绝判断: rejected=%s, reason=%s", rejected, reason)
    print(f"任务拒绝: {'是' if rejected else '否'}")
    print(f"原因: {reason}")
    print("\n" + "-"*60 + "\n")
    
    # 演示场景 4: 单条传感器翻译（带详细映射日志）
    print("┌─────────────────────────────────────────────────────┐")
    print("│ 场景 4: 传感器值 → 拟人化描述映射                   │")
    print("└─────────────────────────────────────────────────────┘")
    demo_logger.info("[场景4] 开始单条传感器翻译演示")
    
    test_cases = [
        ("cpu_temperature", 45.0, "°C", "normal"),
        ("cpu_temperature", 65.0, "°C", "normal"),
        ("cpu_temperature", 72.0, "°C", "warning"),
        ("cpu_temperature", 85.0, "°C", "critical"),
        ("cpu_temperature", 100.0, "°C", "critical"),
        ("battery_percentage", 5.0, "%", "critical"),
        ("battery_percentage", 15.0, "%", "warning"),
        ("battery_percentage", 50.0, "%", "normal"),
        ("battery_percentage", 95.0, "%", "normal"),
        ("memory_usage", 30.0, "%", "normal"),
        ("memory_usage", 75.0, "%", "warning"),
        ("memory_usage", 95.0, "%", "critical"),
        ("network_latency", 30.0, "ms", "normal"),
        ("network_latency", 200.0, "ms", "warning"),
        ("network_latency", 600.0, "ms", "critical"),
        ("disk_space_usage", 30.0, "%", "normal"),
        ("disk_space_usage", 85.0, "%", "warning"),
        ("disk_space_usage", 98.0, "%", "critical"),
    ]
    
    print("┌────────────────────────────────────────────────────────────┐")
    print("│ 传感器名称          │ 值    │ 单位 │ 级别    │ 拟人化描述           │")
    print("├─────────────────────┼───────┼──────┼─────────┼──────────────────────┤")
    
    for sensor_name, value, unit, severity in test_cases:
        reading = {"sensor_name": sensor_name, "value": value, "unit": unit, "severity": severity}
        translation = injector.translate(reading)
        
        # 记录映射过程
        log_sensor_mapping(sensor_name, value, unit, severity, translation)
        
        # 格式化输出
        print(f"│ {sensor_name:19} │ {value:5} │ {unit:4} │ {severity:7} │ {translation:20} │")
    
    print("└────────────────────────────────────────────────────────────┘")
    demo_logger.info("[场景4] 单条传感器翻译演示完成，共 %d 个测试用例", len(test_cases))
    
    print("\n" + "="*60)
    print("🎉 演示完成！")
    print("="*60 + "\n")

if __name__ == "__main__":
    demo()
