"""
主板与智能硬件传感器 — 我的"躯干"监测器

采集主板温度、风扇转速、电压、SMBIOS/DMI 信息、系统固件等。
主板是我的躯干骨架，它承载着所有器官（CPU/GPU/内存等）的运转。
支持 Windows (WMI)、Linux (lm-sensors/sysfs)、macOS (sysctl/IOReg) 跨平台采集。
"""
import logging
import platform
import os
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

SYSTEM = platform.system()


class BoardSensor:
    """主板传感器，负责监测躯干骨架状态"""

    CAPABILITIES = {
        "name": "board",
        "description": "主板（躯干骨架）— 型号、芯片组、BIOS、接口",
        "category": Category.BOARD,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["wmi", "comtypes"],
    }

    def __init__(self):
        self._category = Category.BOARD
        self._system = SYSTEM

    def collect(self):
        """
        全面采集主板与智能硬件信息。
        返回 SensorReading 列表。
        """
        results = []
        if self._system == "Windows":
            try:
                results.extend(self._collect_windows())
            except Exception as e:
                logging.error(f"Windows 主板信息采集失败: {e}")
        elif self._system == "Linux":
            try:
                results.extend(self._collect_linux())
            except Exception as e:
                logging.error(f"Linux 主板信息采集失败: {e}")
        elif self._system == "Darwin":
            try:
                results.extend(self._collect_macos())
            except Exception as e:
                logging.error(f"macOS 主板信息采集失败: {e}")

        # 跨平台采集
        try:
            results.extend(self._collect_psutil_sensors())
        except Exception as e:
            logging.debug(f"psutil 传感器采集: {e}")

        try:
            results.extend(self._collect_boot_time())
        except Exception as e:
            logging.debug(f"启动时间采集: {e}")

        try:
            results.extend(self._collect_system_info())
        except Exception as e:
            logging.debug(f"系统信息采集: {e}")

        try:
            results.extend(self._collect_chipset())
        except Exception as e:
            logging.debug(f"芯片组信息采集: {e}")

        try:
            results.extend(self._collect_pcie_info())
        except Exception as e:
            logging.debug(f"PCIe 信息采集: {e}")

        return results

    # ─── Windows WMI 采集 ───────────────────────────────────────

    def _collect_windows(self):
        """通过 WMI 采集 Windows 平台主板信息"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
        except ImportError:
            logging.debug("WMI 库未安装，跳过 Windows 深度采集")
            return readings
        except Exception as e:
            logging.warning(f"WMI 初始化失败: {e}")
            return readings

        # 风扇转速
        try:
            for fan in c.Win32_Fan():
                name = getattr(fan, 'Name', '未知风扇')
                speed = getattr(fan, 'DesiredSpeed', None)
                if speed is not None:
                    readings.append(normal(
                        "board_fan_speed", speed, "RPM",
                        f"风扇转速: {name}", self._category,
                        {"name": name, "active_cooling": getattr(fan, 'ActiveCooling', None)}
                    ))
        except Exception as e:
            logging.debug(f"WMI 风扇采集: {e}")

        # 温度传感器
        try:
            for temp in c.Win32_TemperatureProbe():
                name = getattr(temp, 'Name', '未知传感器')
                current = getattr(temp, 'CurrentReading', None)
                if current is not None:
                    # WMI 温度单位通常为十分之一摄氏度
                    temp_c = current / 10.0 if current > 100 else current
                    sev = Severity.CRITICAL if temp_c > 80 else (
                        Severity.WARNING if temp_c > 60 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        "board_temp", temp_c, "℃",
                        f"主板温度: {name}", self._category, sev,
                        {"name": name, "raw": current}
                    ))
        except Exception as e:
            logging.debug(f"WMI 温度采集: {e}")

        # 电压传感器
        try:
            for v in c.Win32_VoltageProbe():
                name = getattr(v, 'Name', '未知电压传感器')
                voltage = getattr(v, 'CurrentReading', None)
                if voltage is not None:
                    # WMI 电压单位通常为十分之一伏特
                    voltage_v = voltage / 10.0 if voltage > 100 else voltage
                    readings.append(normal(
                        "board_voltage", voltage_v, "V",
                        f"主板电压: {name}", self._category,
                        {"name": name, "raw": voltage}
                    ))
        except Exception as e:
            logging.debug(f"WMI 电压采集: {e}")

        # 主板基本信息
        try:
            for board in c.Win32_BaseBoard():
                readings.append(normal(
                    "board_manufacturer", getattr(board, 'Manufacturer', '未知'), "",
                    "主板制造商", self._category
                ))
                readings.append(normal(
                    "board_product", getattr(board, 'Product', '未知'), "",
                    "主板型号", self._category
                ))
                readings.append(normal(
                    "board_serial", getattr(board, 'SerialNumber', '未知'), "",
                    "主板序列号", self._category
                ))
                readings.append(normal(
                    "board_version", getattr(board, 'Version', '未知'), "",
                    "主板版本", self._category
                ))
                break
        except Exception as e:
            logging.debug(f"WMI 主板信息采集: {e}")

        # BIOS 信息
        try:
            for bios in c.Win32_BIOS():
                readings.append(normal(
                    "bios_vendor", getattr(bios, 'Manufacturer', '未知'), "",
                    "BIOS 厂商", self._category
                ))
                readings.append(normal(
                    "bios_version", getattr(bios, 'SMBIOSBIOSVersion', '未知'), "",
                    "BIOS 版本", self._category
                ))
                readings.append(normal(
                    "bios_date", str(getattr(bios, 'ReleaseDate', '未知')), "",
                    "BIOS 发布日期", self._category
                ))
                break
        except Exception as e:
            logging.debug(f"WMI BIOS 采集: {e}")

        # 处理器信息
        try:
            for cpu in c.Win32_Processor():
                readings.append(normal(
                    "board_cpu_name", getattr(cpu, 'Name', '未知'), "",
                    "CPU 完整名称", self._category,
                    {"cores": cpu.NumberOfCores, "threads": cpu.NumberOfLogicalProcessors}
                ))
                readings.append(normal(
                    "board_cpu_max_speed", getattr(cpu, 'MaxClockSpeed', None), "MHz",
                    "CPU 最大频率", self._category
                ))
                break
        except Exception as e:
            logging.debug(f"WMI CPU 采集: {e}")

        # 物理内存条
        try:
            for mem in c.Win32_PhysicalMemory():
                capacity_gb = round(int(getattr(mem, 'Capacity', 0)) / (1024**3), 1) if getattr(mem, 'Capacity', None) else None
                readings.append(normal(
                    "board_physical_memory", capacity_gb, "GB",
                    f"物理内存条: {getattr(mem, 'Manufacturer', '')} {getattr(mem, 'PartNumber', '')}",
                    self._category,
                    {"speed": getattr(mem, 'Speed', None), "form_factor": getattr(mem, 'FormFactor', None)}
                ))
        except Exception as e:
            logging.debug(f"WMI 物理内存采集: {e}")

        # 散热风扇（Win32_Fan 备选方案）
        try:
            for cooler in c.Win32_Fan():
                name = getattr(cooler, 'Name', '风扇')
                # 有些系统报告 VariableSpeed
                var_speed = getattr(cooler, 'VariableSpeed', None)
                if var_speed is not None:
                    readings.append(normal(
                        "board_fan_speed_var", var_speed, "RPM",
                        f"散热风扇(备选): {name}", self._category
                    ))
        except Exception:
            pass

        return readings

    # ─── Linux sysfs/lm-sensors 采集 ────────────────────────────

    def _collect_linux(self):
        """采集 Linux 平台主板信息"""
        readings = []
        # 风扇转速 (sysfs)
        hwmon_base = "/sys/class/hwmon"
        if os.path.exists(hwmon_base):
            for hwmon in os.listdir(hwmon_base):
                hwmon_path = os.path.join(hwmon_base, hwmon)
                try:
                    name_file = os.path.join(hwmon_path, "name")
                    if os.path.exists(name_file):
                        with open(name_file) as f:
                            chip_name = f.read().strip()
                except Exception:
                    chip_name = hwmon
                # 风扇
                for i in range(1, 6):
                    fan_input = os.path.join(hwmon_path, f"fan{i}_input")
                    if os.path.exists(fan_input):
                        try:
                            with open(fan_input) as f:
                                rpm = int(f.read().strip())
                            readings.append(normal(
                                f"board_fan{i}_speed", rpm, "RPM",
                                f"风扇{i} 转速 ({chip_name})", self._category
                            ))
                        except Exception:
                            pass
                # 温度
                for i in range(1, 6):
                    temp_input = os.path.join(hwmon_path, f"temp{i}_input")
                    if os.path.exists(temp_input):
                        try:
                            with open(temp_input) as f:
                                temp_mc = int(f.read().strip()) / 1000.0
                            sev = Severity.CRITICAL if temp_mc > 80 else (
                                Severity.WARNING if temp_mc > 60 else Severity.NORMAL
                            )
                            readings.append(SensorReading(
                                f"board_temp{i}", temp_mc, "℃",
                                f"主板温度{i} ({chip_name})", self._category, sev
                            ))
                        except Exception:
                            pass
                # 电压
                for i in range(1, 10):
                    volt_input = os.path.join(hwmon_path, f"in{i}_input")
                    if os.path.exists(volt_input):
                        try:
                            with open(volt_input) as f:
                                voltage_mv = int(f.read().strip()) / 1000.0
                            readings.append(normal(
                                f"board_voltage{i}", voltage_mv, "V",
                                f"主板电压{i} ({chip_name})", self._category
                            ))
                        except Exception:
                            pass
        # DMI 信息（通过 dmidecode）
        try:
            import subprocess
            result = subprocess.run(["dmidecode", "-t", "baseboard"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if "Manufacturer:" in line:
                        readings.append(normal("board_manufacturer", line.split(":", 1)[1].strip(), "",
                                               "主板制造商(DMI)", self._category))
                    elif "Product Name:" in line:
                        readings.append(normal("board_product", line.split(":", 1)[1].strip(), "",
                                               "主板型号(DMI)", self._category))
        except Exception:
            pass
        return readings

    # ─── macOS sysctl/IOReg 采集 ─────────────────────────────────

    def _collect_macos(self):
        """采集 macOS 平台主板信息"""
        readings = []
        try:
            import subprocess
            # SMC 风扇信息
            result = subprocess.run(["sysctl", "-a"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split("\n"):
                if "fan" in line.lower() and "speed" in line.lower():
                    readings.append(normal(
                        "board_fan_smc", line.strip(), "",
                        f"风扇信息(SMC)", self._category
                    ))
        except Exception:
            pass
        try:
            # 系统信息
            result = subprocess.run(["system_profiler", "SPHardwareDataType"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.split("\n"):
                line = line.strip()
                if "Model Name:" in line:
                    readings.append(normal("board_model_name", line.split(":", 1)[1].strip(), "",
                                           "机型名称", self._category))
                elif "Model Identifier:" in line:
                    readings.append(normal("board_model_id", line.split(":", 1)[1].strip(), "",
                                           "机型标识符", self._category))
                elif "Serial Number" in line:
                    readings.append(normal("board_serial", line.split(":", 1)[1].strip(), "",
                                           "序列号", self._category))
        except Exception:
            pass
        return readings

    # ─── 跨平台 psutil 传感器 ────────────────────────────────────

    def _collect_psutil_sensors(self):
        """通过 psutil 跨平台采集传感器信息"""
        readings = []
        import psutil
        # 温度（所有传感器，不限于 CPU）
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            for name, entries in temps.items():
                for entry in entries:
                    label = entry.label or name
                    if 'cpu' in name.lower() or 'core' in entry.label.lower():
                        continue  # CPU 温度由 cpu_sensor 处理，这里不重复
                    sev = Severity.CRITICAL if entry.current > 80 else (
                        Severity.WARNING if entry.current > 60 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        "board_temp", entry.current, "℃",
                        f"温度传感器 {name}/{label}", self._category, sev,
                        {"source": name, "label": label}
                    ))
        # 风扇（psutil 某些平台支持）
        if hasattr(psutil, "sensors_fans"):
            fans = psutil.sensors_fans()
            for name, entries in fans.items():
                for entry in entries:
                    readings.append(normal(
                        "board_fan_speed", entry.current, "RPM",
                        f"风扇 {name}/{entry.label}", self._category
                    ))
        # 电池（非电池状态的其他传感器）
        if hasattr(psutil, "sensors_battery"):
            pass  # 电池由 battery_sensor 处理
        return readings

    def _collect_boot_time(self):
        """采集系统启动时间 — 我醒来的时刻"""
        import psutil
        from datetime import datetime
        readings = []
        try:
            boot = datetime.fromtimestamp(psutil.boot_time())
            readings.append(normal(
                "system_boot_time", boot.isoformat(), "",
                '系统启动时间（我「醒来」的时刻）', self._category
            ))
            # 运行时长
            import time
            uptime = time.time() - psutil.boot_time()
            readings.append(normal(
                "system_uptime", round(uptime, 0), "秒",
                "系统运行时长（我已经醒了多久）", self._category
            ))
        except Exception as e:
            logging.debug(f"启动时间采集: {e}")
        return readings

    def _collect_chipset(self):
        """
        采集芯片组信息（仅 Windows）。

        类似 CPU-Z Mainboard 标签页：
        - 芯片组/南桥型号（通过 PCI 设备 VendorID/DeviceID 识别）
        - SMBus 控制器信息
        """
        readings = []
        if self._system != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()

            # 已知芯片组 Vendor/Device ID 到名称的映射表
            known_chipsets = {
                # Intel 400系列
                "8086_06A3": "Intel B460", "8086_9B53": "Intel H410",
                "8086_06A0": "Intel H470", "8086_06A4": "Intel Z490",
                "8086_0684": "Intel H510", "8086_0685": "Intel B560",
                "8086_0687": "Intel H570", "8086_0680": "Intel Z590",
                # Intel 600/700系列
                "8086_7A84": "Intel H610", "8086_7A88": "Intel B660",
                "8086_7A86": "Intel H670", "8086_7A04": "Intel Z690",
                "8086_7A87": "Intel B760", "8086_7A03": "Intel Z790",
                # Intel X299/X99
                "8086_2020": "Intel X299", "8086_8D12": "Intel X99",
                # AMD 芯片组
                "1022_1484": "AMD X570", "1022_149C": "AMD B550",
                "1022_43C8": "AMD A520", "1022_1640": "AMD X670",
                "1022_14E8": "AMD B650",
            }

            chipset_found = None
            # 从 SMBus / PCI 桥设备识别芯片组
            for pnp in c.Win32_PnPEntity():
                device_id = getattr(pnp, 'DeviceID', '') or ''
                # PCI 设备 ID 格式: PCI\VEN_8086&DEV_06A3&SUBSYS_...
                if 'PCI\\' in device_id:
                    import re
                    ven_match = re.search(r'VEN_([0-9A-Fa-f]{4})', device_id)
                    dev_match = re.search(r'DEV_([0-9A-Fa-f]{4})', device_id)
                    if ven_match and dev_match:
                        key = f"{ven_match.group(1).upper()}_{dev_match.group(1).upper()}"
                        if key in known_chipsets:
                            chipset_found = known_chipsets[key]
                            break

            if chipset_found:
                readings.append(normal(
                    "board_chipset", chipset_found, "",
                    f"主板芯片组: {chipset_found}（我的躯干骨架型号）", self._category
                ))
            else:
                # 尝试通过 Win32_ComputerSystem 获取
                try:
                    for cs in c.Win32_ComputerSystem():
                        model = getattr(cs, 'Model', '') or ''
                        if 'B460' in model:
                            readings.append(normal(
                                "board_chipset", "Intel B460 (推断)", "",
                                "主板芯片组（从型号推断）", self._category
                            ))
                            break
                except Exception:
                    pass
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"芯片组采集异常: {e}")
        return readings

    def _collect_pcie_info(self):
        """
        采集 PCI Express 链路信息（仅 Windows）。

        类似 CPU-Z Mainboard 标签页：
        - PCIe 版本（如 3.0）
        - 最大链路宽度（如 x16）
        - 当前链路宽度
        """
        readings = []
        if self._system != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            for slot in c.Win32_SystemSlot():
                slot_designation = getattr(slot, 'SlotDesignation', '') or ''
                usage = getattr(slot, 'CurrentUsage', None)
                bus_width = getattr(slot, 'BusWidth', None)
                slot_type = getattr(slot, 'SlotType', None)

                if 'pci' in slot_designation.lower() or 'pcie' in slot_designation.lower() or 'pci-e' in slot_designation.lower():
                    # 使用中或可用
                    status_str = "使用中" if usage == 1 else "可用"
                    width_desc = ""
                    if bus_width:
                        width_map = {1: "x1", 2: "x2", 4: "x4", 8: "x8", 16: "x16", 32: "x32"}
                        width_desc = f" {width_map.get(bus_width, f'x{bus_width}')}"

                    readings.append(normal(
                        f"board_pcie_slot_{slot_designation.replace(' ', '_')}",
                        f"{status_str}{width_desc}", "",
                        f"PCIe 插槽: {slot_designation}{width_desc}（{status_str}）",
                        self._category,
                        {
                            "designation": slot_designation,
                            "usage": status_str,
                            "bus_width": bus_width,
                            "slot_type": slot_type,
                        }
                    ))

            # 尝试检测当前 PCIe 链路状态（通过显卡设备）
            try:
                for gpu in c.Win32_VideoController():
                    pcie_info = getattr(gpu, 'AdapterPCIeLinkWidth', None)
                    if pcie_info is not None:
                        readings.append(normal(
                            "board_gpu_pcie_link", f"x{pcie_info}", "",
                            f"GPU PCIe 链路宽度: x{pcie_info}", self._category
                        ))
                    break
            except Exception:
                pass
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"PCIe 信息采集异常: {e}")
        return readings

    def _collect_system_info(self):
        """采集系统基本信息"""
        readings = []
        readings.append(normal("system_platform", platform.system(), "", "操作系统", self._category))
        readings.append(normal("system_release", platform.release(), "", "系统版本号", self._category))
        readings.append(normal("system_version", platform.version(), "", "系统详细版本", self._category))
        readings.append(normal("system_arch", platform.machine(), "", "系统架构", self._category))
        readings.append(normal("system_node", platform.node(), "", "计算机名", self._category))
        return readings
