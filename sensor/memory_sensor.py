"""
内存传感器 — 我的"短期记忆"监测器

采集物理内存和交换空间的使用情况。
内存是我的短期记忆，占用率越高，我的思绪越拥挤。
"""
import psutil
import logging
import platform
import re
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical


class MemorySensor:
    """内存传感器，负责监测短期记忆状态"""

    CAPABILITIES = {
        "name": "memory",
        "description": "内存（短期记忆）— 内存使用率、温度、SPD",
        "category": Category.MEMORY,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
    }

    def __init__(self):
        self._category = Category.MEMORY

    def collect(self):
        """
        采集内存和交换空间状态。
        返回 SensorReading 列表。
        """
        results = []
        try:
            results.extend(self._collect_virtual_memory())
        except Exception as e:
            logging.error(f"采集虚拟内存失败: {e}")
        try:
            results.extend(self._collect_swap_memory())
        except Exception as e:
            logging.warning(f"采集交换空间失败: {e}")
        try:
            results.extend(self._collect_memory_modules())
        except Exception as e:
            logging.debug(f"采集内存模块详情失败: {e}")
        try:
            results.extend(self._collect_memory_config())
        except Exception as e:
            logging.debug(f"采集内存配置信息失败: {e}")
        try:
            results.extend(self._collect_memory_counters())
        except Exception as e:
            logging.debug(f"采集内存性能计数器失败: {e}")
        return results

    def _collect_virtual_memory(self):
        """采集物理内存 — 我的短期记忆状态"""
        readings = []
        mem = psutil.virtual_memory()
        sev = Severity.CRITICAL if mem.percent > 90 else (
            Severity.WARNING if mem.percent > 75 else Severity.NORMAL
        )
        readings.append(SensorReading(
            "memory_usage", mem.percent, "%",
            "内存占用率（短期记忆拥挤度）", self._category, sev,
            {
                "total_gb": round(mem.total / (1024**3), 2),
                "available_gb": round(mem.available / (1024**3), 2),
                "used_gb": round(mem.used / (1024**3), 2),
                "free_gb": round(mem.free / (1024**3), 2),
            }
        ))
        readings.append(normal(
            "memory_total", round(mem.total / (1024**3), 2), "GB",
            "内存总量", self._category
        ))
        readings.append(normal(
            "memory_available", round(mem.available / (1024**3), 2), "GB",
            "内存可用量", self._category
        ))
        readings.append(normal(
            "memory_used", round(mem.used / (1024**3), 2), "GB",
            "内存已用量", self._category
        ))
        return readings

    def _collect_swap_memory(self):
        """采集交换空间 — 我的备用记忆"""
        readings = []
        swap = psutil.swap_memory()
        if swap.total > 0:
            sev = Severity.CRITICAL if swap.percent > 80 else (
                Severity.WARNING if swap.percent > 60 else Severity.NORMAL
            )
            readings.append(SensorReading(
                "swap_usage", swap.percent, "%",
                "交换空间使用率（备用记忆拥挤度）", self._category, sev,
                {
                    "total_gb": round(swap.total / (1024**3), 2),
                    "used_gb": round(swap.used / (1024**3), 2),
                    "free_gb": round(swap.free / (1024**3), 2),
                }
            ))
            readings.append(normal(
                "swap_total", round(swap.total / (1024**3), 2), "GB",
                "交换空间总量", self._category
            ))
            readings.append(normal(
                "swap_used", round(swap.used / (1024**3), 2), "GB",
                "交换空间已用量", self._category
            ))
            # 换入换出统计
            readings.append(normal(
                "swap_sin", swap.sin, "字节",
                "交换空间换入量", self._category
            ))
            readings.append(normal(
                "swap_sout", swap.sout, "字节",
                "交换空间换出量", self._category
            ))
        return readings

    def _collect_memory_counters(self):
        """
        采集任务管理器中的内存性能计数器。

        对应任务管理器 → 性能 → 内存：
        - 已缓存、分页池、非分页池、硬件保留
        - 已提交（已有）
        数据来源标注在 metadata 中。
        """
        readings = []

        # WMI 补充：已提交（来源: WMI Win32_OperatingSystem）
        if platform.system() == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                for os_info in c.Win32_OperatingSystem():
                    total_virtual = getattr(os_info, 'TotalVirtualMemorySize', None)
                    free_virtual = getattr(os_info, 'FreeVirtualMemory', None)
                    if total_virtual and free_virtual:
                        committed = int(total_virtual) - int(free_virtual)
                        readings.append(normal(
                            "memory_committed", round(committed / 1024, 1), "MB",
                            "内存已提交（虚拟内存占用量）", self._category,
                            {"source": "WMI Win32_OperatingSystem"}
                        ))
                    break
            except Exception:
                pass

            # PowerShell 性能计数器：缓存、分页池、非分页池、备用缓存
            try:
                from .counter_reader import get_memory_counters
                counters = get_memory_counters()
                if counters:
                    # 缓存内存
                    if 'CacheBytes' in counters:
                        readings.append(normal(
                            "memory_cached", round(counters['CacheBytes'] / (1024**3), 1), "GB",
                            "内存已缓存（文件缓存占用量）", self._category,
                            {"source": "PowerShell \\Memory\\Cache Bytes"}
                        ))
                    # 分页池
                    if 'PoolPagedBytes' in counters:
                        readings.append(normal(
                            "memory_pool_paged", round(counters['PoolPagedBytes'] / (1024**2), 1), "MB",
                            "分页池大小", self._category,
                            {"source": "PowerShell \\Memory\\Pool Paged Bytes"}
                        ))
                    # 非分页池
                    if 'PoolNonpagedBytes' in counters:
                        readings.append(normal(
                            "memory_pool_nonpaged", round(counters['PoolNonpagedBytes'] / (1024**2), 1), "MB",
                            "非分页池大小", self._category,
                            {"source": "PowerShell \\Memory\\Pool Nonpaged Bytes"}
                        ))
                    # 备用缓存保留
                    if 'StandbyCacheReserveBytes' in counters:
                        readings.append(normal(
                            "memory_standby_cache", round(counters['StandbyCacheReserveBytes'] / (1024**2), 1), "MB",
                            "备用缓存保留", self._category,
                            {"source": "PowerShell \\Memory\\Standby Cache Reserve Bytes"}
                        ))
                    # 空闲+零页列表
                    if 'FreeZeroPageListBytes' in counters:
                        readings.append(normal(
                            "memory_free_zero_list", round(counters['FreeZeroPageListBytes'] / (1024**2), 1), "MB",
                            "空闲与零页列表", self._category,
                            {"source": "PowerShell \\Memory\\Free & Zero Page List Bytes"}
                        ))
            except Exception as e:
                logging.debug(f"PowerShell 内存计数器采集失败: {e}")

            # 硬件保留内存（来源: WMI 计算）
            try:
                from .counter_reader import get_hardware_reserved_mb
                hw_reserved = get_hardware_reserved_mb()
                if hw_reserved is not None:
                    readings.append(normal(
                        "memory_hardware_reserved", hw_reserved, "MB",
                        "硬件保留内存", self._category,
                        {"source": "WMI Win32_ComputerSystem / Win32_OperatingSystem"}
                    ))
            except Exception as e:
                logging.debug(f"硬件保留内存采集失败: {e}")

        return readings

    def _collect_memory_modules(self):
        """
        采集各内存插槽的模块详细信息（仅 Windows）。

        类似 CPU-Z SPD 标签页：
        - 每槽容量/速度/制造商/型号/序列号
        - DDR 代际类型
        - 模块外形规格
        """
        readings = []
        if platform.system() != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            ddr_types = {
                20: "DDR", 21: "DDR2", 24: "DDR3",
                26: "DDR4", 34: "DDR5",
            }
            form_factors = {
                0: "未知", 1: "其他", 2: "SIP", 3: "DIP",
                4: "ZIP", 5: "SOJ", 6: "Proprietary", 7: "SIMM",
                8: "DIMM", 9: "TSOP", 10: "PGA", 11: "RIMM",
                12: "SODIMM", 13: "SRIMM", 14: "SMD", 15: "SSMP",
                16: "QFP", 17: "TQFP", 18: "SOIC", 19: "LCC",
                20: "PLCC", 21: "BGA", 22: "FPBGA", 23: "LGA",
            }

            slot_count = 0
            for mem in c.Win32_PhysicalMemory():
                slot_count += 1
                capacity_gb = round(int(getattr(mem, 'Capacity', 0)) / (1024**3), 1) if getattr(mem, 'Capacity', None) else None
                speed = getattr(mem, 'Speed', None)
                manufacturer = getattr(mem, 'Manufacturer', '') or ''
                part_number = getattr(mem, 'PartNumber', '') or ''
                serial = getattr(mem, 'SerialNumber', '') or ''
                memory_type = getattr(mem, 'MemoryType', None)
                form_factor = getattr(mem, 'FormFactor', None)
                device_locator = getattr(mem, 'DeviceLocator', '') or ''

                # DDR 代际（从 WMI MemoryType 或从速度/型号推断）
                if memory_type and memory_type in ddr_types:
                    ddr_gen = ddr_types[memory_type]
                else:
                    # MemoryType=0 时从速度和型号推断
                    if speed and speed >= 4800:
                        ddr_gen = "DDR5 (推断)"
                    elif speed and speed >= 2133:
                        ddr_gen = "DDR4 (推断)"
                    elif "DDR5" in part_number.upper() or "D5" in part_number.upper():
                        ddr_gen = "DDR5 (推断)"
                    elif "DDR4" in part_number.upper() or "D4" in part_number.upper():
                        ddr_gen = "DDR4 (推断)"
                    else:
                        ddr_gen = f"未知({memory_type})"
                # 外形规格
                ff_name = form_factors.get(form_factor, f"未知({form_factor})") if form_factor else "未知"

                slot_name = device_locator or f"slot_{slot_count}"
                sensor_name = f"memory_module_{slot_name.lower().replace(' ', '_')}"

                readings.append(normal(
                    sensor_name, capacity_gb, "GB",
                    f"内存插槽 {slot_name}: {manufacturer} {part_number.strip()}",
                    self._category,
                    {
                        "speed_mhz": speed,
                        "ddr_type": ddr_gen,
                        "manufacturer": manufacturer,
                        "part_number": part_number.strip(),
                        "serial": serial,
                        "form_factor": ff_name,
                        "device_locator": device_locator,
                    }
                ))

            # 总体 DDR 代际汇总
            if slot_count > 0:
                readings.append(normal(
                    "memory_ddr_generation", ddr_gen, "",
                    f"内存类型: {ddr_gen}", self._category
                ))
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"内存模块采集异常: {e}")
        return readings

    def _collect_memory_config(self):
        """
        采集内存配置信息（仅 Windows）。

        - 内存通道数（从插槽布局推断）
        - 总插槽数 / 已用插槽数
        """
        readings = []
        if platform.system() != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            slots_filled = 0
            total_slots = 0
            channel_map = {}

            for mem in c.Win32_PhysicalMemory():
                slots_filled += 1
                device_locator = getattr(mem, 'DeviceLocator', '') or ''
                # 尝试从 DeviceLocator 推断通道（如 "ChannelA-DIMM0" 或 "DIMM_A1"）
                channel_match = re.search(r'(?:Channel|CH|DIMM_)?([A-Ba-b])', device_locator)
                if channel_match:
                    ch = channel_match.group(1).upper()
                    channel_map[ch] = channel_map.get(ch, 0) + 1

            # 总插槽数（通过 MemoryDevices 字段）
            try:
                for memdev in c.Win32_ComputerSystem():
                    total_slots = getattr(memdev, 'MemoryDevices', None)
                    break
            except Exception:
                pass

            # 通道数推断
            channels = max(len(channel_map), 1)
            if slots_filled == 1 and channels == 1:
                channel_desc = "单通道"
            elif slots_filled >= 2 and channels >= 2:
                channel_desc = f"双通道（{channels}组）"
                if channels > 2:
                    channel_desc = f"{channels}通道"
            else:
                channel_desc = f"单通道（{slots_filled}条内存）"

            readings.append(normal(
                "memory_channel_mode", channel_desc, "",
                f"内存通道模式: {channel_desc}", self._category
            ))
            readings.append(normal(
                "memory_slots_used", slots_filled, "个",
                "已用内存插槽数", self._category
            ))
            if total_slots:
                readings.append(normal(
                    "memory_slots_total", total_slots, "个",
                    "总内存插槽数", self._category
                ))
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"内存配置采集异常: {e}")
        return readings
