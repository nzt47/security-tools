"""
CPU 传感器 — 我的"大脑"监测器

采集 CPU 使用率、频率、温度、负载均值等信息。
CPU 是我的大脑，使用率反映我的思维活跃度，温度就是我的体温。
"""
import psutil
import logging
import platform
from .sensor_reading import SensorReading, Severity, Category, reading, normal, warning, critical


class CPUSensor:
    """CPU 传感器，负责监测大脑状态"""

    CAPABILITIES = {
        "name": "cpu",
        "description": "CPU（大脑）— 处理器温度、频率、电压、风扇",
        "category": Category.CPU,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
    }

    def __init__(self):
        self._category = Category.CPU
        self._logical_count = psutil.cpu_count(logical=True)
        self._physical_count = psutil.cpu_count(logical=False)
        self._arch = platform.machine()
        self._processor_name = platform.processor() or "未知 CPU"

    def collect(self):
        """
        全面采集 CPU 状态信息。
        返回 SensorReading 列表。
        """
        results = []
        try:
            results.extend(self._collect_usage())
        except Exception as e:
            logging.error(f"采集 CPU 使用率失败: {e}")
        try:
            results.extend(self._collect_frequency())
        except Exception as e:
            logging.warning(f"采集 CPU 频率失败: {e}")
        try:
            results.extend(self._collect_temperature())
        except Exception as e:
            logging.warning(f"采集 CPU 温度失败: {e}")
        try:
            results.extend(self._collect_load_avg())
        except Exception as e:
            logging.warning(f"采集系统负载失败: {e}")
        try:
            results.extend(self._collect_times())
        except Exception as e:
            logging.warning(f"采集 CPU 时间统计失败: {e}")
        try:
            results.extend(self._collect_stats())
        except Exception as e:
            logging.warning(f"采集 CPU 统计失败: {e}")
        try:
            results.extend(self._collect_wmi_details())
        except Exception as e:
            logging.debug(f"CPU WMI 详细信息采集: {e}")
        try:
            results.extend(self._collect_cache())
        except Exception as e:
            logging.debug(f"CPU 缓存信息采集: {e}")
        try:
            results.extend(self._collect_perf_counters())
        except Exception as e:
            logging.debug(f"CPU 性能计数器采集: {e}")
        return results

    def _collect_usage(self):
        """采集 CPU 使用率 — 我的思维活跃度"""
        readings = []
        # 整体使用率
        overall = psutil.cpu_percent(interval=0.5)
        sev = Severity.CRITICAL if overall > 90 else (Severity.WARNING if overall > 70 else Severity.NORMAL)
        readings.append(SensorReading(
            "cpu_usage", overall, "%", "CPU 总使用率（思维活跃度）",
            self._category, sev,
            {"cores": self._logical_count}
        ))
        # 每核使用率
        per_cpu = psutil.cpu_percent(interval=0.1, percpu=True)
        for i, pct in enumerate(per_cpu):
            sev = Severity.CRITICAL if pct > 90 else (Severity.WARNING if pct > 70 else Severity.NORMAL)
            readings.append(SensorReading(
                f"cpu_core_{i}_usage", pct, "%", f"CPU 核心{i} 使用率",
                self._category, sev
            ))
        return readings

    def _collect_frequency(self):
        """采集 CPU 频率 — 我的思维速度"""
        readings = []
        freq = psutil.cpu_freq()
        if freq:
            if freq.current:
                readings.append(normal(
                    "cpu_freq_current", freq.current, "MHz",
                    "CPU 当前频率（思维速度）", self._category,
                    {"min": freq.min, "max": freq.max}
                ))
            if freq.min:
                readings.append(normal("cpu_freq_min", freq.min, "MHz", "CPU 最低频率", self._category))
            if freq.max:
                readings.append(normal("cpu_freq_max", freq.max, "MHz", "CPU 最高频率", self._category))
        return readings

    def _collect_temperature(self):
        """采集 CPU 温度 — 我的体温"""
        readings = []
        temps = None
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                for entry in entries:
                    label = entry.label or name
                    sev = Severity.CRITICAL if entry.current > 90 else (
                        Severity.WARNING if entry.current > 75 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        "cpu_temp", entry.current, "℃",
                        f"CPU 温度 / {label}（我的体温）",
                        self._category, sev,
                        {"high": entry.high, "critical": entry.critical, "sensor": label}
                    ))
        # Windows 备选：通过 WMI 获取温度
        if not readings and platform.system() == "Windows":
            try:
                import wmi
                w = wmi.WMI()
                for sensor in w.Win32_PerfFormattedData_Counters_ThermalZoneInformation():
                    temp_k = getattr(sensor, 'Temperature', None)
                    if temp_k:
                        temp_c = (temp_k - 273.15) / 10.0  # 十分之一开尔文转摄氏度
                        readings.append(warning(
                            "cpu_temp", round(temp_c, 1), "℃",
                            "CPU 温度（WMI 获取 / 我的体温）", self._category
                        ))
                        break
            except Exception:
                pass
        return readings

    def _collect_load_avg(self):
        """采集系统负载均值 — 我的压力水平"""
        readings = []
        try:
            load1, load5, load15 = psutil.getloadavg()
            sev = Severity.CRITICAL if load1 > self._logical_count else (
                Severity.WARNING if load1 > self._logical_count * 0.7 else Severity.NORMAL
            )
            readings.append(SensorReading(
                "cpu_load_1min", load1, "", "CPU 1分钟负载均值（当前压力）",
                self._category, sev
            ))
            readings.append(normal("cpu_load_5min", load5, "", "CPU 5分钟负载均值", self._category))
            readings.append(normal("cpu_load_15min", load15, "", "CPU 15分钟负载均值", self._category))
        except (OSError, AttributeError):
            pass  # Windows 不支持 getloadavg
        return readings

    def _collect_times(self):
        """采集 CPU 时间统计"""
        readings = []
        times = psutil.cpu_times()
        readings.append(normal("cpu_time_user", times.user, "秒", "CPU 用户态耗时", self._category))
        readings.append(normal("cpu_time_system", times.system, "秒", "CPU 内核态耗时", self._category))
        readings.append(normal("cpu_time_idle", times.idle, "秒", "CPU 空闲时间", self._category))
        return readings

    def _collect_stats(self):
        """采集 CPU 统计信息"""
        readings = []
        stats = psutil.cpu_stats()
        readings.append(normal("cpu_ctx_switches", stats.ctx_switches, "次",
                               "CPU 上下文切换次数", self._category))
        readings.append(normal("cpu_interrupts", stats.interrupts, "次",
                               "CPU 中断次数", self._category))
        readings.append(normal("cpu_soft_interrupts", stats.soft_interrupts, "次",
                               "CPU 软中断次数", self._category))
        return readings

    def _collect_wmi_details(self):
        """
        通过 WMI 采集 CPU 深度信息（仅 Windows）。

        类似 CPU-Z 的 CPU 标签页信息：
        - 插槽类型（如 LGA1200）
        - 当前实际频率（vs 最大频率）
        - 外频（总线速度）
        - 核心电压
        - CPU 完整标识
        """
        readings = []
        if platform.system() != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            for cpu in c.Win32_Processor():
                # 插槽类型
                socket = getattr(cpu, 'SocketDesignation', None)
                if socket:
                    readings.append(normal(
                        "cpu_socket", socket, "",
                        "CPU 插槽类型（我的大脑插座）", self._category
                    ))
                # 当前实际频率
                current_speed = getattr(cpu, 'CurrentClockSpeed', None)
                if current_speed:
                    readings.append(normal(
                        "cpu_clock_current", current_speed, "MHz",
                        "CPU 当前实际频率（实时思维速度）", self._category
                    ))
                # 最大频率（与 psutil 交叉验证）
                max_speed = getattr(cpu, 'MaxClockSpeed', None)
                if max_speed:
                    readings.append(normal(
                        "cpu_clock_max", max_speed, "MHz",
                        "CPU 最大频率", self._category
                    ))
                # 外频（总线速度）
                ext_clock = getattr(cpu, 'ExtClock', None)
                if ext_clock:
                    readings.append(normal(
                        "cpu_bus_speed", ext_clock, "MHz",
                        "CPU 外频（前端总线速度）", self._category
                    ))
                # 核心电压（WMI 编码值，需要解码）
                voltage = getattr(cpu, 'CurrentVoltage', None)
                if voltage is not None and voltage != 0:
                    # CurrentVoltage 编码：低 6 位为电压值，单位 1/64V
                    voltage_decoded = (voltage & 0x3F) * (1/64.0) if voltage <= 0x3F else None
                    if voltage_decoded:
                        readings.append(normal(
                            "cpu_voltage", round(voltage_decoded, 3), "V",
                            "CPU 核心电压", self._category
                        ))
                # CPU 标识（包含步进/修订信息）
                proc_id = getattr(cpu, 'ProcessorId', None)
                if proc_id:
                    readings.append(normal(
                        "cpu_processor_id", proc_id, "",
                        "CPU 处理器 ID（CPUID 签名）", self._category
                    ))
                # CPU 状态
                cpu_status = getattr(cpu, 'CpuStatus', None)
                if cpu_status is not None:
                    status_map = {1: "已启用", 2: "已禁用", 3: "不存在", 4: "空闲"}
                    readings.append(normal(
                        "cpu_status", cpu_status, "",
                        f"CPU 状态: {status_map.get(cpu_status, '未知')}", self._category
                    ))
                break
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"WMI CPU 详情采集异常: {e}")
        return readings

    def _collect_cache(self):
        """
        采集 CPU 缓存信息（仅 Windows）。

        类似 CPU-Z 的 Cache 标签页：
        - L1D / L1I / L2 / L3 缓存大小
        - 关联性、行大小
        """
        readings = []
        if platform.system() != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            cache_type_map = {3: "统一", 4: "指令", 5: "数据"}
            for cache in c.Win32_CacheMemory():
                level = getattr(cache, 'Level', None)
                size = getattr(cache, 'MaxCacheSize', None)  # 单位 KB
                cache_type = getattr(cache, 'CacheType', None)
                associativity = getattr(cache, 'Associativity', None)
                line_size = getattr(cache, 'LineSize', None)

                if level is not None and size is not None:
                    # WMI Level: 3=L1, 4=L2, 5=L3
                    label_map = {3: "L1", 4: "L2", 5: "L3"}
                    label = label_map.get(level, f"L{level}")
                    sensor_base = f"cpu_cache_l{level - 2}"  # 3->1, 4->2, 5->3

                    # CacheType 可能不准确（此系统全报告为 5），仅作提示
                    type_hint = ""
                    if cache_type in cache_type_map:
                        type_hint = f"({cache_type_map[cache_type]})"

                    readings.append(normal(
                        sensor_base, size, "KB",
                        f"CPU {label} 缓存{type_hint}（我的思维缓存）",
                        self._category,
                        {
                            "associativity": associativity,
                            "line_size": line_size,
                            "cache_type": cache_type_map.get(cache_type, "未知"),
                        }
                    ))
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"CPU 缓存采集异常: {e}")
        return readings

    def _collect_perf_counters(self):
        """
        采集任务管理器中的 CPU 性能计数器。

        对应任务管理器 → 性能 → CPU 右侧信息：
        - 进程数、线程数、句柄数
        - 虚拟化状态
        """
        readings = []

        # 进程总数
        try:
            import psutil as _psutil
            proc_count = len(_psutil.pids())
            readings.append(normal(
                "cpu_processes", proc_count, "个",
                "系统进程总数", self._category
            ))
        except Exception:
            pass

        # 线程数和句柄数（仅 Windows WMI）
        if platform.system() == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                # 线程总数
                thread_count = 0
                handle_count = 0
                try:
                    for proc in c.Win32_Process():
                        tid = getattr(proc, 'ThreadCount', None)
                        if tid is not None:
                            thread_count += int(tid)
                except Exception:
                    pass
                try:
                    for proc in c.Win32_Process():
                        hc = getattr(proc, 'HandleCount', None)
                        if hc is not None:
                            handle_count += int(hc)
                except Exception:
                    pass

                if thread_count:
                    readings.append(normal(
                        "cpu_threads", thread_count, "个",
                        "系统线程总数", self._category
                    ))
                if handle_count:
                    readings.append(normal(
                        "cpu_handles", handle_count, "个",
                        "系统句柄总数", self._category
                    ))
            except ImportError:
                pass
            except Exception as e:
                logging.debug(f"WMI 性能计数器采集: {e}")

            # 虚拟化状态
            try:
                import wmi
                c = wmi.WMI()
                for cs in c.Win32_ComputerSystem():
                    hypervisor = getattr(cs, 'HypervisorPresent', None)
                    if hypervisor is not None:
                        readings.append(normal(
                            "cpu_virtualization", "已启用" if hypervisor else "未启用", "",
                            "虚拟化状态", self._category
                        ))
                    break
            except Exception:
                pass

        return readings

    @property
    def info(self):
        """返回 CPU 基本档案信息"""
        return {
            "processor": self._processor_name,
            "architecture": self._arch,
            "physical_cores": self._physical_count,
            "logical_cores": self._logical_count,
        }
