"""
磁盘传感器 — 我的"长期记忆"监测器

采集磁盘空间使用率、I/O 读写统计等信息。
磁盘是我的长期记忆仓库，空间使用率反映记忆存储的拥挤程度。
"""
import psutil
import logging
import platform
import time
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical


class DiskSensor:
    """磁盘传感器，负责监测长期记忆存储环境"""

    CAPABILITIES = {
        "name": "disk",
        "description": "磁盘（长期记忆）— 分区使用、SMART 健康、IO",
        "category": Category.DISK,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
    }

    def __init__(self):
        self._category = Category.DISK
        # 记录上次 I/O 计数和时间戳，用于计算速率和活动时间
        self._prev_io = {}  # {disk_name: (read_count, write_count, read_bytes, write_bytes, read_time, write_time, busy_time)}
        self._prev_time = 0

    def collect(self):
        """
        采集磁盘空间和 I/O 状态。
        返回 SensorReading 列表。
        """
        results = []
        try:
            results.extend(self._collect_partitions())
        except Exception as e:
            logging.error(f"采集磁盘分区失败: {e}")
        try:
            results.extend(self._collect_io_with_stats())
        except Exception as e:
            logging.warning(f"采集磁盘 I/O 失败: {e}")
        return results

    def _collect_partitions(self):
        """采集各分区的磁盘空间使用率 — 我的长期记忆仓库状态"""
        readings = []
        partitions = psutil.disk_partitions()
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                # 安全的设备名
                safe_name = part.device.replace("\\", "_").replace(":", "").replace("/", "_")
                sev = Severity.CRITICAL if usage.percent > 95 else (
                    Severity.WARNING if usage.percent > 85 else Severity.NORMAL
                )
                readings.append(SensorReading(
                    f"disk_usage_{safe_name}", usage.percent, "%",
                    f"分区 {part.device} ({part.mountpoint}) 空间使用率",
                    self._category, sev,
                    {
                        "mountpoint": part.mountpoint,
                        "device": part.device,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "used_gb": round(usage.used / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                    }
                ))
            except PermissionError:
                logging.debug(f"无权限读取分区 {part.device}")
            except Exception as e:
                logging.warning(f"采集分区 {part.device} 失败: {e}")
        return readings

    def _collect_io_with_stats(self):
        """
        采集磁盘 I/O 统计，含任务管理器级指标。

        对应任务管理器 → 性能 → 磁盘：
        - 读写速率（当前）
        - 活动时间 %
        - 平均响应时间
        - IOPS
        """
        readings = []
        now = time.time()
        try:
            io = psutil.disk_io_counters(perdisk=True)
            if not io:
                return readings

            for disk_name, stats in io.items():
                # 累计值
                readings.append(normal(
                    f"disk_io_read_count_{disk_name}", stats.read_count, "次",
                    f"磁盘 {disk_name} 累计读取次数", self._category
                ))
                readings.append(normal(
                    f"disk_io_write_count_{disk_name}", stats.write_count, "次",
                    f"磁盘 {disk_name} 累计写入次数", self._category
                ))
                readings.append(normal(
                    f"disk_io_read_bytes_{disk_name}",
                    float(round(stats.read_bytes / (1024**3), 2)), "GB",
                    f"磁盘 {disk_name} 累计读取量", self._category
                ))
                readings.append(normal(
                    f"disk_io_write_bytes_{disk_name}",
                    float(round(stats.write_bytes / (1024**3), 2)), "GB",
                    f"磁盘 {disk_name} 累计写入量", self._category
                ))

                        # 差值计算（速率/活动时间/响应时间）
                is_first = self._prev_time == 0
                if not is_first and disk_name in self._prev_io:
                    p_read_c, p_write_c, p_read_b, p_write_b, p_read_t, p_write_t, _ = self._prev_io[disk_name]
                    dt = max(now - self._prev_time, 0.001)

                    d_read_c = stats.read_count - p_read_c
                    d_write_c = stats.write_count - p_write_c
                    d_read_b = stats.read_bytes - p_read_b
                    d_write_b = stats.write_bytes - p_write_b
                    d_read_t = stats.read_time - p_read_t
                    d_write_t = stats.write_time - p_write_t

                    # 读写速率（MB/s）
                    if d_read_b > 0:
                        read_speed = d_read_b / dt / (1024**2)
                        readings.append(normal(
                            f"disk_read_speed_{disk_name}", round(read_speed, 2), "MB/s",
                            f"磁盘 {disk_name} 当前读取速率", self._category
                        ))
                    if d_write_b > 0:
                        write_speed = d_write_b / dt / (1024**2)
                        readings.append(normal(
                            f"disk_write_speed_{disk_name}", round(write_speed, 2), "MB/s",
                            f"磁盘 {disk_name} 当前写入速率", self._category
                        ))

                    # 磁盘活动时间 %（任务管理器 "Active time"）
                    # read_time/write_time 单位为毫秒
                    # 活动时间 = (读取耗时 + 写入耗时) / 实际经过时间 * 100
                    active_time = (d_read_t + d_write_t) / (dt * 1000) * 100
                    act = min(round(active_time, 1), 100.0)
                    act_sev = Severity.CRITICAL if act > 95 else (Severity.WARNING if act > 80 else Severity.NORMAL)
                    readings.append(SensorReading(
                        f"disk_active_time_{disk_name}", act, "%",
                        f"磁盘 {disk_name} 活动时间百分比",
                        self._category, act_sev,
                        {"source": "psutil disk_io_counters"}
                    ))

                    # IOPS
                    iops = (d_read_c + d_write_c) / dt
                    readings.append(normal(
                        f"disk_iops_{disk_name}", round(iops, 1), "IOPS",
                        f"磁盘 {disk_name} 当前 IOPS", self._category
                    ))

                    # 平均响应时间（毫秒/操作）
                    if d_read_c > 0:
                        avg_read_resp = d_read_t / d_read_c
                        readings.append(normal(
                            f"disk_avg_read_response_{disk_name}", round(avg_read_resp, 2), "ms",
                            f"磁盘 {disk_name} 平均读取响应时间", self._category
                        ))
                    if d_write_c > 0:
                        avg_write_resp = d_write_t / d_write_c
                        readings.append(normal(
                            f"disk_avg_write_response_{disk_name}", round(avg_write_resp, 2), "ms",
                            f"磁盘 {disk_name} 平均写入响应时间", self._category
                        ))

                # 保存当前值作为下次对比基准
                busy_time = getattr(stats, 'busy_time', 0) or 0
                self._prev_io[disk_name] = (stats.read_count, stats.write_count,
                                            stats.read_bytes, stats.write_bytes,
                                            stats.read_time, stats.write_time, busy_time)

            # 整体汇总（本次有差值数据才输出）
            if self._prev_time > 0:
                total_iops = sum(
                    (io[dn].read_count - self._prev_io[dn][0] + io[dn].write_count - self._prev_io[dn][1])
                    / max(now - self._prev_time, 0.001)
                    for dn in io if dn in self._prev_io
                )
                readings.append(normal(
                    "disk_total_iops", round(total_iops, 1), "IOPS",
                    "磁盘总 IOPS", self._category
                ))

            self._prev_time = now
        except Exception as e:
            logging.debug(f"采集磁盘 I/O 失败: {e}")
        return readings
