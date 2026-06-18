# 云枢感知底座（BodySensor）实现计划

## 目标
搭建完整的感知底座，覆盖所有可采集的软硬件变化感知，统一 JSON 输出。

## 阶段

### 阶段 1：基础数据类
- [ ] 创建 `sensor_reading.py` — 统一传感器读数数据类 `SensorReading`
  - 字段：timestamp, sensor_name, value, unit, description, category, severity
  - 支持 JSON 序列化

### 阶段 2：核心传感器增强
- [ ] 增强 `cpu_sensor.py` — 增加频率、核心数、负载均值、每核使用率
- [ ] 增强 `gpu_sensor.py` — 增加风扇转速、总显存、GPU 名称/驱动版本
- [ ] 增强 `memory_sensor.py` — 增加 swap、总量/已用/可用值
- [ ] 增强 `battery_sensor.py` — 增加剩余时间估算、功耗率
- [ ] 增强 `disk_sensor.py` — 增加 I/O 统计、读写速率、总量

### 阶段 3：新增传感器模块
- [ ] 创建 `network_sensor.py` — 网络感知（IP、网卡状态、实时流量、连接数）
- [ ] 重写 `board_sensor.py` — 全面主板监测（风扇、电压、温度、跨平台）
- [ ] 创建 `chassis_sensor.py` — 机箱安全（入侵检测、物理安全状态）
- [ ] 创建 `change_detector.py` — 变更感知（硬件插拔、软件安装卸载、配置快照）

### 阶段 4：集成与测试
- [ ] 增强 `file_watcher.py` — 统一 JSON 输出格式
- [ ] 增强 `body_sensor.py` — 集成所有传感器，使用 SensorReading
- [ ] 更新 `main.py` — 完整示例入口
- [ ] 更新 `test_body_sensor.py` — 完整单元测试
- [ ] 更新 `requirements.txt`

## 决策日志
- 统一输出格式：使用 SensorReading 数据类封装所有传感器读数
- 传感器采集策略：每次 collect() 返回 SensorReading 列表
- 跨平台策略：优先 psutil，Windows 补充 WMI，Linux 补充 lm-sensors
- 变更检测：使用快照对比机制，记录硬件/软件配置变化
