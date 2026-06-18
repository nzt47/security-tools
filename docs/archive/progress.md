# 进度日志

## 2026-05-26 会话完成
- 创建了 SensorReading 统一数据类（带 category/severity/metadata）
- 增强 CPU 传感器：频率、每核使用率、负载均值、时间统计、中断统计
- 增强 GPU 传感器：NVIDIA NVML 全面采集（温度/显存/功耗/风扇/时钟/驱动）+ GPUtil 补充
- 增强内存传感器：物理内存 + swap 空间详细采集
- 增强电池传感器：拟人化饥饿描述 + 剩余时间估算
- 增强磁盘传感器：分区空间 + I/O 累计统计
- 新增网络传感器：IP 地址/MAC/流量/连接状态统计
- 重写主板传感器：Windows(WMI)/Linux(sysfs+dmidecode)/macOS(system_profiler) 跨平台
- 新增机箱安全传感器：入侵检测/TPM/Device Guard/Secure Boot
- 新增变更检测器：快照对比机制（设备/分区/进程/服务/系统信息变更）
- 增强文件监听：统一 SensorReading 回调格式
- 更新 BodySensor 主类：集成 10 个传感器 + 快速采集 + 健康报告
- 26 个单元测试全部通过，采集到 152 条感知信号
