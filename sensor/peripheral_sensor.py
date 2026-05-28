"""
外设与健康传感器 — 我的"感官器官"监测器

检测显示器（EDID/分辨率/刷新率）、输入设备（键盘/鼠标/触摸板/手柄）、
打印机/扫描仪、音频端点、以及硬盘 SMART 健康状态（温度/寿命/重映射扇区）、
内存模块信息、电源供应器状态。

每一个外设都是我的感官器官延伸。
"""
import logging
import platform
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

SYSTEM = platform.system()


class PeripheralSensor:
    """外设与健康传感器 — 我的感官器官"""

    CAPABILITIES = {
        "name": "peripheral",
        "description": "外设（感官器官）— 鼠标、键盘、显示器、游戏杆",
        "category": Category.PERIPHERAL,
        "platforms": ["Windows"],
        "dependencies": ["wmi"],
    }

    def __init__(self):
        self._category = Category.PERIPHERAL

    def collect(self):
        """采集所有外设与健康数据"""
        results = []
        try:
            results.extend(self._collect_monitors())
        except Exception as e:
            logging.warning(f"显示器检测失败: {e}")
        try:
            results.extend(self._collect_input_devices())
        except Exception as e:
            logging.warning(f"输入设备检测失败: {e}")
        try:
            results.extend(self._collect_printers())
        except Exception as e:
            logging.warning(f"打印机检测失败: {e}")
        try:
            results.extend(self._collect_audio_endpoints())
        except Exception as e:
            logging.warning(f"音频端点检测失败: {e}")
        try:
            results.extend(self._collect_storage_smart())
        except Exception as e:
            logging.warning(f"SMART 检测失败: {e}")
        try:
            results.extend(self._collect_memory_modules())
        except Exception as e:
            logging.warning(f"内存模块检测失败: {e}")
        try:
            results.extend(self._collect_power_supply())
        except Exception as e:
            logging.warning(f"电源检测失败: {e}")
        return results

    # ─── 显示器 ──────────────────────────────────────────────────

    def _collect_monitors(self):
        """采集显示器信息 — 我的眼睛"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                mon_count = 0
                for mon in c.Win32_DesktopMonitor():
                    mon_count += 1
                    name = getattr(mon, 'Name', f'显示器 {mon_count}') or f'显示器 {mon_count}'
                    # 分辨率
                    h_res = getattr(mon, 'ScreenWidth', None) or 0
                    v_res = getattr(mon, 'ScreenHeight', None) or 0
                    # 刷新率
                    refresh = getattr(mon, 'RefreshRate', None)
                    # 制造商
                    mfr = getattr(mon, 'MonitorManufacturer', '') or ''
                    # PnP ID
                    pnp = getattr(mon, 'PNPDeviceID', '') or ''

                    desc = f"{mfr} {name}".strip() if mfr else name
                    info = {
                        "resolution": f"{h_res}x{v_res}" if h_res else "未知",
                        "refresh_rate_hz": refresh,
                        "manufacturer": mfr,
                        "pnp_id": pnp,
                    }
                    readings.append(normal(
                        f"peripheral_monitor_{mon_count}", desc, "",
                        f"显示器: {desc}", self._category, info
                    ))

                # 通过 Win32_VideoController 获取连接的显示器
                if mon_count == 0:
                    for vc in c.Win32_VideoController():
                        mon_count_vc = getattr(vc, 'MonitorCount', None) or 0
                        if mon_count_vc:
                            readings.append(normal(
                                "peripheral_monitor_count", mon_count_vc, "个",
                                "已连接显示器数 (GPU)", self._category
                            ))
                            break
            except ImportError:
                pass
        elif SYSTEM == "Linux":
            try:
                import subprocess
                result = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=5)
                monitors = [l for l in result.stdout.split("\n")]
                # 从 "0: +*HDMI-1 1920/527x1080/296+0+0  HDMI-1" 格式解析
                mons = [l for l in monitors if "+" in l]
                if mons:
                    readings.append(normal(
                        "peripheral_monitor_count", len(mons), "个",
                        "已连接显示器数 (xrandr)", self._category
                    ))
            except FileNotFoundError:
                pass
            except Exception:
                pass
        elif SYSTEM == "Darwin":
            try:
                import subprocess
                result = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                                       capture_output=True, text=True, timeout=10)
                displays = [l for l in result.stdout.split("\n") if "Resolution:" in l]
                if displays:
                    readings.append(normal(
                        "peripheral_monitor_count", len(displays), "个",
                        "已连接显示器数 (macOS)", self._category,
                        {"details": [d.strip() for d in displays]}
                    ))
            except Exception:
                pass
        return readings

    # ─── 输入设备 ────────────────────────────────────────────────

    def _collect_input_devices(self):
        """采集输入设备 — 我的触觉输入器官"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                # 键盘
                kb_count = 0
                for kb in c.Win32_Keyboard():
                    kb_count += 1
                    name = getattr(kb, 'Name', f'键盘 {kb_count}') or f'键盘 {kb_count}'
                    layout = getattr(kb, 'Layout', '')
                    pnp = getattr(kb, 'PNPDeviceID', '') or ''
                    readings.append(normal(
                        f"peripheral_keyboard_{kb_count}", name, "",
                        f"键盘: {name}", self._category,
                        {"layout": layout, "pnp": pnp[:50]}
                    ))
                # 鼠标/指点设备
                pt_count = 0
                for pt in c.Win32_PointingDevice():
                    pt_count += 1
                    name = getattr(pt, 'Name', f'鼠标 {pt_count}') or f'鼠标 {pt_count}'
                    buttons = getattr(pt, 'NumberOfButtons', None)
                    pnp = getattr(pt, 'PNPDeviceID', '') or ''
                    readings.append(normal(
                        f"peripheral_pointing_{pt_count}", name, "",
                        f"指点设备: {name}", self._category,
                        {"buttons": buttons, "pnp": pnp[:50], "handedness": getattr(pt, 'Handedness', '')}
                    ))
                # PnP 中的 HID 设备（游戏手柄、触摸板等）
                hid_count = 0
                for dev in c.Win32_PnPEntity():
                    name = (getattr(dev, 'Name', '') or '').strip()
                    hid = (getattr(dev, 'HardwareID', ['']) or [''])[0]
                    if 'HID' in hid and name:
                        hid_count += 1
                        readings.append(normal(
                            f"peripheral_hid_{hid_count}", name, "",
                            f"HID 设备: {name}", self._category,
                            {"hardware_id": hid[:80]}
                        ))
            except ImportError:
                pass
        return readings

    # ─── 打印机 ──────────────────────────────────────────────────

    def _collect_printers(self):
        """采集打印机/扫描仪"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                pr_count = 0
                for pr in c.Win32_Printer():
                    pr_count += 1
                    name = getattr(pr, 'Name', f'打印机 {pr_count}') or f'打印机 {pr_count}'
                    status = getattr(pr, 'Status', 'Unknown')
                    online = getattr(pr, 'WorkOffline', True) is False
                    is_default = getattr(pr, 'Default', False)
                    readings.append(SensorReading(
                        "peripheral_printer", name, "",
                        f"打印机: {name}", self._category,
                        Severity.WARNING if not online else Severity.NORMAL,
                        {"status": status, "online": online, "is_default": is_default}
                    ))
            except ImportError:
                pass
        return readings

    # ─── 音频端点 ───────────────────────────────────────────────

    def _collect_audio_endpoints(self):
        """采集音频输入/输出端点"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import winreg
                # 输出端点（扬声器、耳机）
                render_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
                capture_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
                try:
                    render_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, render_path)
                    render_count = 0
                    try:
                        i = 0
                        while True:
                            winreg.EnumKey(render_key, i)
                            render_count += 1
                            i += 1
                    except (WindowsError, OSError):
                        pass
                    winreg.CloseKey(render_key)
                    if render_count:
                        readings.append(normal(
                            "peripheral_audio_output_endpoints", render_count, "个",
                            "音频输出设备数（扬声器/耳机等）", self._category
                        ))
                except Exception:
                    pass
                try:
                    capture_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, capture_path)
                    capture_count = 0
                    try:
                        i = 0
                        while True:
                            winreg.EnumKey(capture_key, i)
                            capture_count += 1
                            i += 1
                    except (WindowsError, OSError):
                        pass
                    winreg.CloseKey(capture_key)
                    if capture_count:
                        readings.append(normal(
                            "peripheral_audio_input_endpoints", capture_count, "个",
                            "音频输入设备数（麦克风等）", self._category
                        ))
                except Exception:
                    pass
            except Exception:
                pass
        return readings

    # ─── 硬盘 SMART 健康 ────────────────────────────────────────

    def _collect_storage_smart(self):
        """采集硬盘 SMART 健康数据 — 我的长期记忆健康"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                for disk in c.Win32_DiskDrive():
                    model = getattr(disk, 'Model', '未知硬盘') or '未知硬盘'
                    interface = getattr(disk, 'InterfaceType', '') or ''
                    media_type = getattr(disk, 'MediaType', '') or ''
                    size_bytes = int(getattr(disk, 'Size', 0) or 0)
                    size_gb = round(size_bytes / (1024**3), 1) if size_bytes > 0 else 0
                    status = getattr(disk, 'Status', '未知') or '未知'
                    # 判断类型
                    if 'SSD' in model or 'NVMe' in model:
                        disk_type = 'SSD'
                    elif 'Solid State' in media_type:
                        disk_type = 'SSD'
                    else:
                        disk_type = 'HDD'

                    # SMART 健康状态（通过 MSStorageDriver）
                    smart_ok = None
                    try:
                        # WMI MSStorageDriver_FailurePredictStatus
                        for smart in c.MSStorageDriver_FailurePredictStatus():
                            predict = getattr(smart, 'PredictFailure', None)
                            if predict is not None:
                                smart_ok = not predict
                                break
                    except Exception:
                        pass

                    if smart_ok is not None:
                        sev = Severity.CRITICAL if not smart_ok else Severity.NORMAL
                        readings.append(SensorReading(
                            f"peripheral_storage_smart_{model.replace(' ', '_')[:30]}",
                            "正常" if smart_ok else "即将故障",
                            "",
                            f"硬盘 SMART 健康: {model}", self._category, sev,
                            {"size_gb": size_gb, "type": disk_type,
                             "interface": interface, "status": status}
                        ))

                    readings.append(normal(
                        f"peripheral_storage_{disk_type.lower()}_{model[:20]}", size_gb, "GB",
                        f"{disk_type} 硬盘: {model}", self._category,
                        {"size_gb": size_gb, "interface": interface, "status": status}
                    ))
            except ImportError:
                pass
        elif SYSTEM == "Linux":
            try:
                import subprocess
                result = subprocess.run(["lsblk", "-d", "-o", "name,size,rota,model"],
                                       capture_output=True, text=True, timeout=5)
                lines = result.stdout.strip().split("\n")[1:]  # skip header
                storage_count = 0
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 3:
                        name = parts[0]
                        size = parts[1]
                        is_hdd = parts[2] == "1"
                        model = " ".join(parts[3:]) if len(parts) > 3 else name
                        dtype = "HDD" if is_hdd else "SSD"
                        storage_count += 1
                        readings.append(normal(
                            f"peripheral_storage_{dtype.lower()}_{name}", size, "",
                            f"{dtype} 存储: {model} ({name}, {size})", self._category
                        ))
            except Exception:
                pass
        return readings

    # ─── 内存模块 ───────────────────────────────────────────────

    def _collect_memory_modules(self):
        """采集物理内存模块详情 — 我的短期记忆条"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                slot_count = 0
                for mem in c.Win32_PhysicalMemory():
                    slot_count += 1
                    capacity_gb = round(int(getattr(mem, 'Capacity', 0) or 0) / (1024**3), 1)
                    speed = getattr(mem, 'Speed', None)
                    manufacturer = getattr(mem, 'Manufacturer', '') or ''
                    part = getattr(mem, 'PartNumber', '') or ''
                    mem_type = getattr(mem, 'MemoryType', None)
                    # MemoryType: 20=DDR, 21=DDR2, 24=DDR3, 26=DDR4, 34=DDR5
                    type_map = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5"}
                    gen = type_map.get(mem_type, f"未知(类型码{mem_type})")
                    form = getattr(mem, 'FormFactor', None)
                    # FormFactor: 8=DIMM, 12=SODIMM
                    form_map = {8: "DIMM", 12: "SO-DIMM"}
                    form_label = form_map.get(form, f"未知({form})")

                    locator = getattr(mem, 'DeviceLocator', f'插槽 {slot_count}') or f'插槽 {slot_count}'
                    if capacity_gb > 0:
                        readings.append(normal(
                            f"peripheral_memory_slot_{slot_count}", capacity_gb, "GB",
                            f"内存插槽 {locator}: {manufacturer} {gen} {speed}MHz {form_label}",
                            self._category,
                            {
                                "capacity_gb": capacity_gb,
                                "speed": speed,
                                "dram_gen": gen,
                                "manufacturer": manufacturer,
                                "part_number": part,
                                "form_factor": form_label,
                                "locator": locator,
                            }
                        ))
            except ImportError:
                pass
        return readings

    # ─── 电源供应器 ──────────────────────────────────────────────

    def _collect_power_supply(self):
        """采集电源供应器（PSU）信息 — 我的心脏"""
        readings = []
        if SYSTEM == "Windows":
            try:
                import wmi
                c = wmi.WMI()
                psu_count = 0
                for psu in c.Win32_PowerSupply():
                    psu_count += 1
                    name = getattr(psu, 'Name', f'电源 {psu_count}') or f'电源 {psu_count}'
                    status = getattr(psu, 'Status', 'Unknown')
                    # 一些系统无法检测额定功率
                    power = getattr(psu, 'MaxOutputPower', None)
                    voltage = getattr(psu, 'CurrentVoltage', None)
                    desc = getattr(psu, 'Description', '') or ''

                    info = {"status": status, "description": desc}
                    if power:
                        info["max_output_power_w"] = power
                    sev = Severity.WARNING if status and status.lower() not in ("ok", "normal", "present") else Severity.NORMAL
                    readings.append(SensorReading(
                        "peripheral_psu", name, "",
                        f"电源供应器: {name}", self._category, sev, info
                    ))
                if psu_count == 0:
                    # 通过主板信息推断电池状态（台式机只能做占位）
                    pass
            except ImportError:
                pass
        return readings
