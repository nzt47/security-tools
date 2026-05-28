# 研究发现

## 2026-05-26 最终实现总结

### 文件结构
```
sensor/
├── __init__.py           # 包初始化，导出所有公开接口
├── sensor_reading.py     # 统一 SensorReading 数据类 + Severity/Category 枚举
├── body_sensor.py        # BodySensor 主类，集成所有传感器
├── cpu_sensor.py         # CPU 传感器（使用率/频率/温度/负载/统计）
├── gpu_sensor.py         # GPU 传感器（NVML + GPUtil 双数据源）
├── memory_sensor.py      # 内存传感器（物理内存 + swap）
├── battery_sensor.py     # 电池传感器（拟人化饥饿描述）
├── disk_sensor.py        # 磁盘传感器（分区空间 + I/O 统计）
├── network_sensor.py     # 网络传感器（IP/MAC/流量/连接）
├── board_sensor.py       # 主板传感器（跨平台：WMI/sysfs/system_profiler）
├── chassis_sensor.py     # 机箱安全传感器（入侵检测/TPM/Secure Boot）
├── change_detector.py    # 变更检测传感器（快照对比机制）
├── file_watcher.py       # 文件系统监听（watchdog + SensorReading 回调）
├── main.py               # 完整示例入口
├── test_body_sensor.py   # 26 个单元测试
└── requirements.txt      # 依赖清单
```

### 技术选型确认
- psutil 7.2.2: CPU/内存/磁盘/网络/电池/传感器，核心依赖
- GPUtil 1.4.0: GPU 补充数据源
- pynvml 13.0.1: NVIDIA GPU 详细数据（已处理弃用警告）
- watchdog 6.0.0: 文件系统监听
- wmi: Windows 平台主板/机箱深度采集

### 跨平台实现
- CPU 温度: psutil sensors_temperatures + Windows WMI 备选
- 主板/风扇/电压: Windows WMI / Linux sysfs hwmon / macOS system_profiler
- 机箱入侵: Windows WMI Win32_SystemEnclosure / Linux dmidecode / macOS limited
- GPU: NVIDIA NVML (Windows+Linux), AMD unsupported, Intel iGPU unsupported

### 待改进
- AMD GPU 支持（需 ROCm/pyrsmi）
- Intel 核显支持
- pynvml → nvidia-ml-py 迁移（pynvml 已弃用但 nvidia-ml-py 需额外安装）
- 变更检测首次调用耗时较长（需采集完整设备/进程/服务列表）
