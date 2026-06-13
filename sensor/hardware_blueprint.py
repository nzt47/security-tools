"""
硬件蓝图 — 我的"身体解剖图"

这是云枢硬件结构的完整解剖图鉴，穷尽所有可能的硬件接口和组件。
每一项都标注检测方式：
  ✅ software_detectable — 可以通过软件自动检测
  ⚡ inference — 可根据已有传感器数据推断
  🔧 manual_check — 需人工开箱检查确认

这份蓝图不会采集"实时数据"，而是提供完整的硬件拓扑清单，
让用户知道哪些部分已被感知到，哪些需要人工补充。
"""
import logging
import platform
from .sensor_reading import SensorReading, Severity, Category, normal

SYSTEM = platform.system()


class HardwareBlueprint:
    """
    硬件蓝图 — 我的身体解剖图

    穷尽所有可能的硬件组件和接口，标记检测方式。
    它像一本医学解剖手册，告诉我身体的结构。
    """

    def __init__(self):
        self._category = Category.BOARD

    def collect(self):
        """
        采集完整硬件蓝图。
        返回 SensorReading 列表，每个条目对应一个硬件接口或组件。
        """
        readings = []

        # 按系统区域组织蓝图条目
        for entry in self._build_blueprint():
            try:
                count = self._detect_count(entry)
                entry["detected_count"] = count
                readings.append(self._entry_to_reading(entry))
            except Exception as e:
                logging.debug(f"蓝图项 {entry['name']} 检测异常: {e}")

        # 添加无法软件检测的纯物理接口
        for entry in self._build_physical_only():
            readings.append(self._entry_to_reading(entry))

        return readings

    # ─── 可软件检测的硬件接口 ──────────────────────────────────

    def _build_blueprint(self):
        """构建完整硬件蓝图 — 可软件检测的部分"""
        blueprint = []

        # 主板核心
        blueprint.append({"name": "主芯片组", "type": "chipset", "method": "software_detectable",
                          "sources": ["WMI Win32_BaseBoard", "dmidecode -t baseboard"]})
        blueprint.append({"name": "BIOS/UEFI 固件", "type": "firmware", "method": "software_detectable",
                          "sources": ["WMI Win32_BIOS", "dmidecode -t bios"]})
        blueprint.append({"name": "CMOS 电池状态", "type": "battery", "method": "software_detectable",
                          "sources": ["第三方工具", "hwmon 电压监控"]})

        # CPU 相关
        blueprint.append({"name": "CPU 处理器", "type": "cpu", "method": "software_detectable",
                          "sources": ["psutil", "WMI Win32_Processor"]})
        blueprint.append({"name": "CPU 散热器风扇转速", "type": "cpu_cooler", "method": "software_detectable",
                          "sources": ["WMI Win32_Fan", "hwmon fan1_input"]})
        blueprint.append({"name": "CPU 插槽类型", "type": "cpu_socket", "method": "inference",
                          "sources": ["根据 CPU 型号推断 LGA1700/AM5 等"]})
        blueprint.append({"name": "CPU 核心电压", "type": "cpu_voltage", "method": "software_detectable",
                          "sources": ["hwmon in0_input", "WMI Win32_VoltageProbe"]})

        # 内存相关
        blueprint.append({"name": "内存插槽（物理条数）", "type": "memory", "method": "software_detectable",
                          "sources": ["WMI Win32_PhysicalMemory"]})
        blueprint.append({"name": "内存类型（DDR4/DDR5）", "type": "memory_type", "method": "software_detectable",
                          "sources": ["WMI Win32_PhysicalMemory.MemoryType"]})
        blueprint.append({"name": "内存频率", "type": "memory_speed", "method": "software_detectable",
                          "sources": ["WMI Win32_PhysicalMemory.Speed"]})
        blueprint.append({"name": "内存总容量", "type": "memory_size", "method": "software_detectable",
                          "sources": ["psutil.virtual_memory()"]})
        blueprint.append({"name": "ECC 内存校验", "type": "memory_ecc", "method": "software_detectable",
                          "sources": ["WMI Win32_PhysicalMemory", "dmidecode -t memory"]})

        # GPU / 显示
        blueprint.append({"name": "独立显卡（dGPU）", "type": "gpu", "method": "software_detectable",
                          "sources": ["GPUtil", "pynvml", "WMI Win32_VideoController"]})
        blueprint.append({"name": "集成显卡（iGPU）", "type": "igpu", "method": "software_detectable",
                          "sources": ["WMI Win32_VideoController", "lspci"]})
        blueprint.append({"name": "HDMI 输出接口", "type": "display_output", "method": "inference",
                          "sources": ["根据 GPU 型号和驱动信息推断"]})
        blueprint.append({"name": "DisplayPort 输出接口", "type": "display_output", "method": "inference",
                          "sources": ["根据 GPU 型号和驱动信息推断"]})
        blueprint.append({"name": "DVI 输出接口", "type": "display_output", "method": "inference",
                          "sources": ["根据 GPU 型号推断"]})
        blueprint.append({"name": "VGA 输出接口", "type": "display_output", "method": "inference",
                          "sources": ["根据 GPU 型号推断"]})
        blueprint.append({"name": "已连接显示器", "type": "monitor", "method": "software_detectable",
                          "sources": ["WMI Win32_DesktopMonitor", "EDID"]})

        # 存储
        blueprint.append({"name": "SATA 控制器", "type": "storage_controller", "method": "software_detectable",
                          "sources": ["WMI Win32_IDEController", "lspci SATA"]})
        blueprint.append({"name": "NVMe 控制器", "type": "storage_controller", "method": "software_detectable",
                          "sources": ["lspci", "WMI Win32_DiskDrive (NVMe)"]})
        blueprint.append({"name": "M.2 插槽（NVMe/SATA）", "type": "m2_slot", "method": "software_detectable",
                          "sources": ["根据 NVMe 控制器数量 + 主板型号推断"]})
        blueprint.append({"name": "硬盘（HDD/SSD）", "type": "storage", "method": "software_detectable",
                          "sources": ["psutil.disk_partitions", "WMI Win32_DiskDrive"]})
        blueprint.append({"name": "硬盘 SMART 健康", "type": "storage_health", "method": "software_detectable",
                          "sources": ["WMI MSStorageDriver_FailurePredictStatus", "smartctl"]})
        blueprint.append({"name": "光驱（ODD）", "type": "optical_drive", "method": "software_detectable",
                          "sources": ["WMI Win32_CDROMDrive", "lshw"]})
        blueprint.append({"name": "SD 读卡器", "type": "card_reader", "method": "software_detectable",
                          "sources": ["WMI Win32_USBHub / PnPEntity"]})

        # USB 相关
        blueprint.append({"name": "USB 2.0 控制器", "type": "usb", "method": "software_detectable",
                          "sources": ["WMI Win32_USBController (UHCI/EHCI)"]})
        blueprint.append({"name": "USB 3.0 控制器", "type": "usb", "method": "software_detectable",
                          "sources": ["WMI Win32_USBController (xHCI)"]})
        blueprint.append({"name": "USB 3.1/3.2/4.0 控制器", "type": "usb", "method": "software_detectable",
                          "sources": ["WMI Win32_USBController (xHCI 3.1+)"]})
        blueprint.append({"name": "USB Type-C 接口", "type": "usb_type_c", "method": "software_detectable",
                          "sources": ["WMI USBHub (Type-C)", "UCSI 驱动"]})
        blueprint.append({"name": "前置 USB 接口（机箱前面板）", "type": "usb_front", "method": "inference",
                          "sources": ["根据 USB 控制器端口数减去后置推断，需人工确认"]})
        blueprint.append({"name": "USB 端口总数", "type": "usb_total", "method": "software_detectable",
                          "sources": ["WMI Win32_USBController.NumberOfPorts"]})

        # 音频
        blueprint.append({"name": "HD Audio 音频控制器", "type": "audio", "method": "software_detectable",
                          "sources": ["WMI Win32_SoundDevice", "lspci Audio"]})
        blueprint.append({"name": "前置音频接口（机箱前面板）", "type": "audio_front", "method": "inference",
                          "sources": ["主板型号推断是否支持前置音频（通常都支持）"]})
        blueprint.append({"name": "后置音频接口（Line In/Out/Mic）", "type": "audio_rear", "method": "inference",
                          "sources": ["主板 I/O 挡板规格推断（通常3孔）"]})
        blueprint.append({"name": "S/PDIF 光纤输出", "type": "spdif", "method": "software_detectable",
                          "sources": ["WMI Win32_SoundDevice (S/PDIF)", "注册表音频端点"]})
        blueprint.append({"name": "S/PDIF 同轴输出", "type": "spdif_coax", "method": "software_detectable",
                          "sources": ["WMI Win32_SoundDevice (S/PDIF Coaxial)"]})
        blueprint.append({"name": "麦克风输入", "type": "audio_mic", "method": "software_detectable",
                          "sources": ["MMDevices Audio/Capture"]})
        blueprint.append({"name": "音频输出设备数", "type": "audio_output", "method": "software_detectable",
                          "sources": ["MMDevices Audio/Render"]})

        # 网络
        blueprint.append({"name": "以太网卡（RJ45）", "type": "ethernet", "method": "software_detectable",
                          "sources": ["psutil.net_if_addrs", "WMI Win32_NetworkAdapter"]})
        blueprint.append({"name": "WiFi 无线网卡", "type": "wifi", "method": "software_detectable",
                          "sources": ["WMI Win32_NetworkAdapter (Wireless)"]})
        blueprint.append({"name": "蓝牙适配器", "type": "bluetooth", "method": "software_detectable",
                          "sources": ["WMI Win32_BluetoothRadio", "hciconfig"]})

        # 接口
        blueprint.append({"name": "COM 串口", "type": "serial_port", "method": "software_detectable",
                          "sources": ["WMI Win32_SerialPort", "注册表 SERIALCOMM"]})
        blueprint.append({"name": "LPT 并口", "type": "parallel_port", "method": "software_detectable",
                          "sources": ["WMI Win32_ParallelPort"]})
        blueprint.append({"name": "PS/2 键盘口", "type": "ps2", "method": "software_detectable",
                          "sources": ["WMI Win32_PS2Controller / Win32_Keyboard PNP"]})
        blueprint.append({"name": "PS/2 鼠标口", "type": "ps2", "method": "software_detectable",
                          "sources": ["WMI Win32_PS2Controller / Win32_PointingDevice PNP"]})

        # PCIe 扩展
        blueprint.append({"name": "PCIe x16 插槽", "type": "pcie_slot", "method": "software_detectable",
                          "sources": ["WMI Win32_SystemSlot (BusWidth=16)"]})
        blueprint.append({"name": "PCIe x8 插槽", "type": "pcie_slot", "method": "software_detectable",
                          "sources": ["WMI Win32_SystemSlot (BusWidth=8)"]})
        blueprint.append({"name": "PCIe x4/x1 插槽", "type": "pcie_slot", "method": "software_detectable",
                          "sources": ["WMI Win32_SystemSlot (BusWidth=4/1)"]})
        blueprint.append({"name": "PCI 传统插槽", "type": "pci_slot", "method": "software_detectable",
                          "sources": ["WMI Win32_SystemSlot (BusWidth=32/64)"]})
        blueprint.append({"name": "M.2 插槽（PCIe/NVMe）", "type": "m2_pcie", "method": "software_detectable",
                          "sources": ["PCIe NVMe 控制器存在即可推断"]})

        # 外设
        blueprint.append({"name": "键盘", "type": "input", "method": "software_detectable",
                          "sources": ["WMI Win32_Keyboard"]})
        blueprint.append({"name": "鼠标/指点设备", "type": "input", "method": "software_detectable",
                          "sources": ["WMI Win32_PointingDevice"]})
        blueprint.append({"name": "触摸板", "type": "input", "method": "software_detectable",
                          "sources": ["WMI Win32_PointingDevice (PNP touchpad)"]})
        blueprint.append({"name": "打印机/扫描仪", "type": "peripheral", "method": "software_detectable",
                          "sources": ["WMI Win32_Printer"]})
        blueprint.append({"name": "游戏手柄/HID 设备", "type": "input", "method": "software_detectable",
                          "sources": ["WMI Win32_PnPEntity (HID)"]})
        blueprint.append({"name": "红外（IR）接收器", "type": "ir", "method": "software_detectable",
                          "sources": ["WMI Win32_InfraredDevice"]})

        # 系统
        blueprint.append({"name": "电源供应器（PSU）", "type": "psu", "method": "software_detectable",
                          "sources": ["WMI Win32_PowerSupply"]})
        blueprint.append({"name": "主板温度传感器", "type": "board_sensor", "method": "software_detectable",
                          "sources": ["psutil.sensors_temperatures", "hwmon", "WMI Win32_TemperatureProbe"]})
        blueprint.append({"name": "主板电压传感器", "type": "board_sensor", "method": "software_detectable",
                          "sources": ["hwmon in*_input", "WMI Win32_VoltageProbe"]})
        blueprint.append({"name": "TPM 安全芯片", "type": "security", "method": "software_detectable",
                          "sources": ["WMI Win32_Tpm", "dmidecode -t 43"]})
        blueprint.append({"name": "Secure Boot", "type": "security", "method": "software_detectable",
                          "sources": ["WMI Win32_DeviceGuard"]})
        blueprint.append({"name": "机箱入侵检测", "type": "security", "method": "software_detectable",
                          "sources": ["WMI Win32_SystemEnclosure.SecurityBreach"]})

        return blueprint

    # ─── 纯物理（无法软件检测）接口 ────────────────────────────

    def _build_physical_only(self):
        """构建纯物理接口清单 — 这些需要人工检查"""
        blueprint = []

        physical_items = [
            # 供电接口
            ("24pin ATX 主板主供电", "power_connector", "主板主供电接口，连接 PSU"),
            ("8pin EPS CPU 供电", "power_connector", "CPU 辅助供电（部分主板 4+4pin）"),
            ("6+2pin PCIe 显卡供电", "power_connector", "独立显卡供电接口"),
            ("SATA 供电线", "power_connector", "给硬盘/SSD 供电"),
            ("Molex 大4pin 供电", "power_connector", "旧式设备供电（风扇/灯带等）"),
            ("3pin/4pin 风扇供电", "power_connector", "机箱/CPU 散热器供电接口"),
            ("主板 12V RGB 供电 (4pin)", "power_connector", "RGB 灯带/风扇灯效供电"),
            ("主板 5V ARGB 供电 (3pin)", "power_connector", "可寻址 RGB 灯效供电"),

            # 机箱前面板
            ("前面板 USB 2.0 插针", "front_panel", "机箱前置 USB 2.0 接口"),
            ("前面板 USB 3.0 插针", "front_panel", "机箱前置 USB 3.0 接口"),
            ("前面板 USB Type-C 插针", "front_panel", "机箱前置 USB-C 接口"),
            ("前面板音频插针 (HD Audio)", "front_panel", "前置耳机/麦克风接口"),
            ("前面板电源按钮", "front_panel", "PWRSW — 开机键"),
            ("前面板重启按钮", "front_panel", "RESET — 重启键"),
            ("电源指示灯 LED (PWR LED)", "front_panel", "开机指示灯"),
            ("硬盘指示灯 LED (HDD LED)", "front_panel", "硬盘读写指示灯"),
            ("机箱蜂鸣器 (Speaker)", "front_panel", "主板蜂鸣器/报警扬声器"),

            # 主板跳线/调试
            ("CMOS 清空跳线 (CLR_CMOS)", "jumper", "清除 BIOS 设置"),
            ("BIOS 烧录/恢复跳线", "jumper", "BIOS 应急恢复"),
            ("Debug LED 诊断灯", "jumper", "主板自检错误码指示灯"),
            ("Power On (免开机测试)", "jumper", "短接启动主板的针脚"),

            # 其他物理接口
            ("IR 红外接收器（如需）", "ir_physical", "CIR 红外接收模块，需检查机箱或主板"),
            ("机箱侧板/挡板", "chassis_physical", "检查机箱侧板是否牢固"),
            ("扩展挡板（PCIe 槽位挡板）", "chassis_physical", "检查未使用槽位的挡板是否安装"),
            ("天线接口（WiFi/BT）", "antenna", "主板/网卡附带天线"),
            ("主板 I/O 挡板", "chassis_physical", "后置接口挡板（已装配）"),
        ]

        for item_name, item_type, desc in physical_items:
            blueprint.append({
                "name": item_name,
                "type": item_type,
                "method": "manual_check",
                "sources": ["需打开机箱目视检查或参考主板手册"],
                "description": desc,
                "detected_count": None,
            })

        return blueprint

    # ─── 检测计数 ──────────────────────────────────────────────

    def _detect_count(self, entry):
        """
        尝试检测某个硬件接口的存在/数量。
        返回: None（无法检测）、整数（数量）、True/False（是否存在）、字符串（描述）
        """
        name = entry["name"]
        entry_type = entry["type"]

        if entry["method"] == "manual_check":
            return None  # 无法软件检测

        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
            except ImportError:
                return "WMI 不可用"
        else:
            return "非 Windows 平台"

        try:
            # CPU
            if entry_type == "cpu":
                for cpu in c.Win32_Processor():
                    return getattr(cpu, 'Name', '检测到')
                return None
            if entry_type == "chipset":
                for board in c.Win32_BaseBoard():
                    return getattr(board, 'Product', '检测到')
                return None
            if entry_type == "firmware":
                for bios in c.Win32_BIOS():
                    return f"{getattr(bios, 'SMBIOSBIOSVersion', '检测到')}"
                return None

            # 内存
            if entry_type == "memory":
                count = 0
                for _ in c.Win32_PhysicalMemory():
                    count += 1
                return count
            if entry_type == "memory_type" or entry_type == "memory_speed" or entry_type == "memory_ecc":
                for mem in c.Win32_PhysicalMemory():
                    if "memory" in entry_type:
                        return f"{getattr(mem, 'MemoryType', '')}"
                    return "检测到"
                return None
            if entry_type == "memory_size":
                import psutil
                return f"{round(psutil.virtual_memory().total / (1024**3), 1)} GB"

            # 存储
            if entry_type == "storage":
                count = 0
                for _ in c.Win32_DiskDrive():
                    count += 1
                return count
            if entry_type == "storage_controller":
                count = 0
                try:
                    for _ in c.Win32_IDEController():
                        count += 1
                except Exception:
                    pass
                return count if count else "检测到"
            if entry_type == "optical_drive":
                count = 0
                for _ in c.Win32_CDROMDrive():
                    count += 1
                return count

            # GPU
            if entry_type in ("gpu", "igpu"):
                for vc in c.Win32_VideoController():
                    return getattr(vc, 'Name', '检测到')
                return None

            # USB
            if entry_type in ("usb", "usb_type_c"):
                count = 0
                for _ in c.Win32_USBController():
                    count += 1
                return count
            if entry_type == "usb_total":
                total = 0
                for ctrl in c.Win32_USBController():
                    p = getattr(ctrl, 'NumberOfPorts', None)
                    if p:
                        total += p
                return total or "检测到"

            # 音频
            if entry_type in ("audio", "spdif", "spdif_coax"):
                count = 0
                for _ in c.Win32_SoundDevice():
                    count += 1
                return count

            # 网络
            if entry_type in ("ethernet", "wifi"):
                count = 0
                for nic in c.Win32_NetworkAdapter():
                    enabled = getattr(nic, 'NetEnabled', None)
                    if enabled:
                        count += 1
                return count
            if entry_type == "bluetooth":
                count = 0
                for _ in c.Win32_BluetoothRadio():
                    count += 1
                return count if count else "未检测到"

            # 接口
            if entry_type == "serial_port":
                count = 0
                for _ in c.Win32_SerialPort():
                    count += 1
                return count
            if entry_type == "parallel_port":
                count = 0
                for _ in c.Win32_ParallelPort():
                    count += 1
                return count
            if entry_type == "ps2":
                for _ in c.Win32_PS2Controller():
                    return True
                return False

            # PCIe
            if "pcie" in entry_type or "pci" in entry_type:
                count = 0
                for _ in c.Win32_SystemSlot():
                    count += 1
                return count

            # 外设
            if entry_type == "input":
                if "键盘" in name or "Keyboard" in name or "PS/2 键盘" in name:
                    count = 0
                    for _ in c.Win32_Keyboard():
                        count += 1
                    return count
                if "鼠标" in name or "Mouse" in name or "触摸" in name:
                    count = 0
                    for _ in c.Win32_PointingDevice():
                        count += 1
                    return count
                if "手柄" in name or "HID" in name:
                    return "见 HID 设备列表"
                return "检测到"
            if entry_type == "peripheral":
                count = 0
                for _ in c.Win32_Printer():
                    count += 1
                return count

            # 传感器
            if entry_type == "board_sensor":
                import psutil
                if hasattr(psutil, "sensors_temperatures"):
                    temps = psutil.sensors_temperatures()
                    total = sum(len(v) for v in temps.values())
                    return total
                return "检测到"
            if entry_type == "security":
                if "TPM" in name:
                    for tpm in c.Win32_Tpm():
                        return "已启用" if getattr(tpm, 'IsEnabled_InitialValue', None) else "未启用"
                    return None
                return "检测到"

            if entry_type == "psu":
                count = 0
                for _ in c.Win32_PowerSupply():
                    count += 1
                return count or "参见电源传感器"

            if entry_type == "card_reader":
                return "见 USB 设备列表"

            if entry_type == "cpu_cooler":
                count = 0
                for _ in c.Win32_Fan():
                    count += 1
                return count

            if entry_type == "cpu_voltage":
                count = 0
                for _ in c.Win32_VoltageProbe():
                    count += 1
                return count

            if entry_type == "storage_health":
                try:
                    for smart in c.MSStorageDriver_FailurePredictStatus():
                        return "正常" if not getattr(smart, 'PredictFailure', False) else "预警"
                except Exception:
                    pass
                return "无法检测"

            return "检测到"

        except Exception as e:
            logging.debug(f"蓝图检测 {name}: {e}")
            return "检测失败"

    def _entry_to_reading(self, entry):
        """将蓝图条目转换为 SensorReading"""
        name = entry["name"]
        method = entry["method"]
        count = entry.get("detected_count")
        desc = entry.get("description",
                        f"{name} — 检测方式: {method}")

        # 方法标记
        method_icon = {
            "software_detectable": "✅ 软件检测",
            "inference": "⚡ 推断",
            "manual_check": "🔧 需人工检查",
        }
        method_label = method_icon.get(method, method)

        # 检测结果
        if count is None:
            result = "待检查"
            sev = Severity.NORMAL
        elif count is False:
            result = "未检测到"
            sev = Severity.WARNING
        elif count is True:
            result = "已检测到"
            sev = Severity.NORMAL
        else:
            result = str(count)
            sev = Severity.NORMAL

        # 数据来源分类：software → 从软件读取, direct → 硬件直读, inference → 推断, manual → 人工检查
        sources = entry.get("sources", [])
        any_hwmon = any("hwmon" in (s or "").lower() for s in sources) if sources else False
        if method == "software_detectable":
            data_origin = "direct" if any_hwmon else "software"
        elif method == "inference":
            data_origin = "inference"
        else:
            data_origin = "manual"

        value_display = f"{result} [{method_label}]"
        metadata = {
            "method": method,
            "device_type": entry["type"],
            "data_origin": data_origin,
        }
        if method == "manual_check":
            metadata["hint"] = "需打开机箱目视检查或查阅主板/硬件手册"
        if sources:
            metadata["detection_source"] = sources

        return SensorReading(
            f"blueprint_{name.replace(' ', '_').replace('/', '_')}",
            value_display, "",
            desc, self._category, sev, metadata
        )
