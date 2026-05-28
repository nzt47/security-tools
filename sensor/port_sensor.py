"""
端口与接口传感器 — 我的"神经末梢"监测器

检测所有 I/O 端口和接口：USB（各代）、COM 串口、LPT 并口、PS/2、PCIe 插槽、
M.2 SSD、音频接口、显示输出（HDMI/DP/DVI/VGA）、蓝牙、红外、S/PDIF 等。

每一个接口都是我与外部世界连接的神经末梢。
跨平台支持：Windows (WMI / PnP)、Linux (lspci / lsusb / sysfs)、macOS (IOReg)
"""
import logging
import platform
import re
from .sensor_reading import SensorReading, Severity, Category, normal, warning

SYSTEM = platform.system()


class PortSensor:
    """端口与接口传感器 — 我的神经末梢"""

    CAPABILITIES = {
        "name": "port",
        "description": "端口（神经末梢）— USB、COM、LPT、雷电",
        "category": Category.PORT,
        "platforms": ["Windows"],
        "dependencies": ["wmi"],
    }

    def __init__(self):
        self._category = Category.PORT  # 归入主板大类

    def collect(self):
        """采集所有 I/O 端口与接口信息"""
        results = []

        if SYSTEM == "Windows":
            try:
                results.extend(self._collect_usb_windows())
            except Exception as e:
                logging.warning(f"USB 检测失败: {e}")
            try:
                results.extend(self._collect_com_ports())
            except Exception as e:
                logging.warning(f"COM 端口检测失败: {e}")
            try:
                results.extend(self._collect_lpt_ports())
            except Exception as e:
                logging.warning(f"LPT 端口检测失败: {e}")
            try:
                results.extend(self._collect_ps2_ports())
            except Exception as e:
                logging.warning(f"PS/2 检测失败: {e}")
            try:
                results.extend(self._collect_pcie_slots())
            except Exception as e:
                logging.warning(f"PCIe 插槽检测失败: {e}")
            try:
                results.extend(self._collect_audio_devices())
            except Exception as e:
                logging.warning(f"音频设备检测失败: {e}")
            try:
                results.extend(self._collect_display_outputs())
            except Exception as e:
                logging.warning(f"显示输出检测失败: {e}")
            try:
                results.extend(self._collect_bluetooth())
            except Exception as e:
                logging.warning(f"蓝牙检测失败: {e}")
            try:
                results.extend(self._collect_m2_nvme())
            except Exception as e:
                logging.warning(f"M.2/NVMe 检测失败: {e}")

        elif SYSTEM == "Linux":
            try:
                results.extend(self._collect_usb_linux())
            except Exception as e:
                logging.warning(f"Linux USB 检测失败: {e}")
            try:
                results.extend(self._collect_pci_devices())
            except Exception as e:
                logging.warning(f"Linux PCI 检测失败: {e}")
            try:
                results.extend(self._collect_audio_linux())
            except Exception as e:
                logging.warning(f"Linux 音频检测失败: {e}")
            try:
                results.extend(self._collect_bluetooth_linux())
            except Exception as e:
                logging.warning(f"Linux 蓝牙检测失败: {e}")

        elif SYSTEM == "Darwin":
            try:
                results.extend(self._collect_overview_macos())
            except Exception as e:
                logging.warning(f"macOS 接口检测失败: {e}")

        return results

    # ─── Windows USB ──────────────────────────────────────────────

    def _collect_usb_windows(self):
        """采集 USB 控制器和设备（按代分类）"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
        except ImportError:
            return readings

        # USB 控制器（分类各代）
        controllers = {}
        try:
            for ctrl in c.Win32_USBController():
                name = getattr(ctrl, 'Name', '') or ''
                desc = getattr(ctrl, 'Description', '') or ''
                # 识别 USB 代次
                gen = "未知"
                if "3.1" in name or "3.10" in name or "xHCI" in name:
                    gen = "3.1/3.2"
                elif "3.0" in name or "USB30" in name or "USB 3.0" in name:
                    gen = "3.0"
                elif "2.0" in name or "UHCI" in name or "EHCI" in name:
                    gen = "2.0"
                elif "1.1" in name or "OHCI" in name:
                    gen = "1.1"

                if gen not in controllers:
                    controllers[gen] = []
                controllers[gen].append(name)
        except Exception:
            pass

        for gen, names in sorted(controllers.items()):
            readings.append(normal(
                f"port_usb_controller_{gen.replace('/', '_')}", len(names), "个",
                f"USB {gen} 控制器数量", self._category,
                {"names": names[:5]}
            ))

        # USB 设备（按代数统计）
        usb_devices_by_gen = {}
        try:
            for hub in c.Win32_USBHub():
                name = getattr(hub, 'Name', '') or ''
                if "3.0" in name or "3.1" in name:
                    gen = "3.x"
                elif "2.0" in name:
                    gen = "2.0"
                elif "1.1" in name:
                    gen = "1.1"
                else:
                    gen = "未知"
                usb_devices_by_gen.setdefault(gen, 0)
                usb_devices_by_gen[gen] += 1

            # 通过 PnP 枚举具体 USB 设备
            usb_connected = set()
            for dev in c.Win32_PnPEntity():
                devid = str(getattr(dev, 'DeviceID', '') or '')
                if "USB\\VID_" in devid:
                    name = getattr(dev, 'Name', '') or getattr(dev, 'Caption', '') or '未知设备'
                    usb_connected.add(name)

            readings.append(normal(
                "port_usb_devices_total", len(usb_connected), "个",
                "已连接的 USB 设备总数", self._category,
                {"devices": sorted(usb_connected)[:20]}
            ))
        except Exception:
            pass

        # USB 端口物理数量（估算）
        try:
            port_count = 0
            for ctrl in c.Win32_USBController():
                ports = getattr(ctrl, 'NumberOfPorts', None)
                if ports is not None:
                    port_count += ports
            if port_count:
                readings.append(normal(
                    "port_usb_total_ports", port_count, "个",
                    "USB 端口总数（含内部端口）", self._category
                ))
        except Exception:
            pass

        return readings

    def _collect_com_ports(self):
        """采集 COM 串口"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
            com_count = 0
            for sp in c.Win32_SerialPort():
                com_count += 1
                name = getattr(sp, 'Name', f'COM{com_count}') or f'COM{com_count}'
                device_id = getattr(sp, 'DeviceID', '')
                baud = getattr(sp, 'MaxBaudRate', None)
                readings.append(normal(
                    f"port_com_{device_id or com_count}", device_id or name, "",
                    f"串口: {name}", self._category,
                    {"baud_rate": baud}
                ))
            # 也通过注册表检测 COM 端口
            if com_count == 0:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
                try:
                    i = 0
                    while True:
                        name, value, _ = winreg.EnumValue(key, i)
                        readings.append(normal(
                            f"port_com_reg_{value}", value, "",
                            f"串口(注册表): {value}", self._category
                        ))
                        i += 1
                except (WindowsError, OSError):
                    pass
                winreg.CloseKey(key)
        except ImportError:
            pass
        return readings

    def _collect_lpt_ports(self):
        """采集 LPT 并口"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
            for pp in c.Win32_ParallelPort():
                name = getattr(pp, 'Name', 'LPT') or 'LPT'
                device_id = getattr(pp, 'DeviceID', '')
                readings.append(normal(
                    f"port_lpt_{device_id or '1'}", device_id or name, "",
                    f"并口: {name}", self._category
                ))
        except ImportError:
            pass
        return readings

    def _collect_ps2_ports(self):
        """采集 PS/2 端口"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
            for ps2 in c.Win32_PS2Controller():
                name = getattr(ps2, 'Name', 'PS/2 Controller') or 'PS/2 Controller'
                hw_config = getattr(ps2, 'HardwareConfiguration', None)
                readings.append(normal(
                    "port_ps2_controller", True, "bool",
                    f"PS/2 控制器: {name}", self._category,
                    {"config": hw_config}
                ))
        except ImportError:
            pass
        # 通过键盘和鼠标设备推断 PS/2
        try:
            import wmi
            c = wmi.WMI()
            has_ps2_keyboard = False
            has_ps2_mouse = False
            for kb in c.Win32_Keyboard():
                pnp = getattr(kb, 'PNPDeviceID', '') or ''
                if 'PS2' in pnp:
                    has_ps2_keyboard = True
            for pt in c.Win32_PointingDevice():
                pnp = getattr(pt, 'PNPDeviceID', '') or ''
                if 'PS2' in pnp:
                    has_ps2_mouse = True
            if has_ps2_keyboard:
                readings.append(normal("port_ps2_keyboard", True, "bool", "PS/2 键盘已连接", self._category))
            if has_ps2_mouse:
                readings.append(normal("port_ps2_mouse", True, "bool", "PS/2 鼠标已连接", self._category))
        except ImportError:
            pass
        return readings

    def _collect_pcie_slots(self):
        """采集 PCIe 插槽"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
            slot_count = 0
            for slot in c.Win32_SystemSlot():
                slot_count += 1
                tag = getattr(slot, 'Tag', f'PCIe Slot {slot_count}')
                usage = getattr(slot, 'Status', 'Unknown')
                # 插槽宽度
                width_map = {
                    1: "x1", 2: "x2", 4: "x4", 8: "x8", 16: "x16", 32: "x32"
                }
                bus_width = getattr(slot, 'BusWidth', None)
                width_label = width_map.get(bus_width, f'x{bus_width}' if bus_width else '未知')
                # 是否占用
                slot_designation = getattr(slot, 'SlotDesignation', '')
                # 长度描述
                length_map = {1: "短", 2: "长"}
                length = getattr(slot, 'Length', None)
                length_label = length_map.get(length, '')
                readings.append(normal(
                    f"port_pcie_slot_{slot_count}", width_label, "",
                    f"PCIe 插槽: {tag} ({slot_designation})", self._category,
                    {
                        "width": width_label,
                        "status": usage,
                        "length": length_label,
                        "designation": slot_designation,
                    }
                ))
        except ImportError:
            pass
        # Linux 备选
        if SYSTEM == "Linux":
            try:
                import subprocess
                result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
                lines = result.stdout.strip().split("\n")
                readings.append(normal(
                    "port_pci_devices_total", len(lines), "个",
                    "PCI 设备总数（lspci）", self._category
                ))
            except Exception:
                pass
        return readings

    def _collect_audio_devices(self):
        """
        采集音频设备详情 — 我的"听觉"感知。

        对应 Windows 声音设置 / Realtek Audio Console：
        - 音频设备列表（硬件设备）
        - 音频端点列表（播放/录音设备，含缺省设备）
        - Windows Audio 服务状态
        - S/PDIF、HDMI 音频检测
        来源: WMI Win32_SoundDevice + Core Audio API MMDeviceEnumerator
        """
        readings = []
        if SYSTEM != "Windows":
            return readings

        # ── 1. WMI 硬件设备 ──────────────────────────────────
        try:
            import wmi
            c = wmi.WMI()
            audio_devices = list(c.Win32_SoundDevice())
            if not audio_devices:
                readings.append(normal(
                    "port_audio_device_count", 0, "个",
                    "音频硬件设备数", self._category
                ))
            else:
                readings.append(normal(
                    "port_audio_device_count", len(audio_devices), "个",
                    "音频硬件设备数", self._category
                ))
                for i, sd in enumerate(audio_devices):
                    name = getattr(sd, 'Name', '') or f'音频设备 {i+1}'
                    manufacturer = getattr(sd, 'Manufacturer', '') or ''
                    status = getattr(sd, 'Status', '')
                    pnpid = getattr(sd, 'PNPDeviceID', '') or ''
                    err_code = getattr(sd, 'ConfigManagerErrorCode', None)

                    # 从 PNPDeviceID 推断设备类型
                    dev_type = "音频控制器"
                    if "VEN_10EC" in pnpid:
                        dev_type = "板载声卡 (Realtek)"
                    elif "VEN_10DE" in pnpid or "VEN_NVIDIA" in pnpid:
                        dev_type = "HDMI 音频 (NVIDIA)"
                    elif "VEN_8086" in pnpid:
                        dev_type = "显示音频 (Intel)"
                    elif "VEN_1002" in pnpid:
                        dev_type = "HDMI 音频 (AMD)"

                    # 驱动状态
                    health = "正常" if err_code == 0 else f"错误({err_code})"

                    readings.append(normal(
                        f"port_audio_device_{i+1}", name, "",
                        f"音频设备 {i+1}: {name} ({dev_type})", self._category,
                        {
                            "manufacturer": manufacturer,
                            "status": status,
                            "health": health,
                            "type": dev_type,
                            "pnp_id": pnpid,
                            "source": "WMI Win32_SoundDevice",
                        }
                    ))

                    # 检测 S/PDIF 和 HDMI 音频
                    if "HDMI" in name or "HDMI" in dev_type:
                        readings.append(normal(
                            f"port_audio_hdmi_{i+1}", True, "bool",
                            f"HDMI 音频: {name}", self._category
                        ))
                    if "Digital" in name or "S/PDIF" in name:
                        readings.append(normal(
                            f"port_audio_spdif_{i+1}", True, "bool",
                            f"S/PDIF 数字音频: {name}", self._category
                        ))
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"WMI 音频设备采集异常: {e}")

        # ── 2. 音频端点（Core Audio API）────────────────────
        try:
            from .counter_reader import get_audio_info
            audio_info = get_audio_info()

            # 播放端点
            render_count = len(audio_info.get("render", []))
            readings.append(normal(
                "port_audio_render_endpoints", render_count, "个",
                "音频播放端点数（扬声器/耳机等）", self._category,
                {"source": "Core Audio API"}
            ))
            for i, ep in enumerate(audio_info.get("render", [])):
                ep_name = ep.get("name") or "(unnamed)"
                ep_desc = ep.get("description") or ""
                is_def = " [缺省]" if ep.get("is_default") else ""
                readings.append(normal(
                    f"port_audio_render_{i+1}", ep_name, "",
                    f"播放设备 {i+1}: {ep_name}{is_def}", self._category,
                    {
                        "description": ep_desc,
                        "is_default": ep.get("is_default", False),
                        "state": ep.get("state"),
                        "source": "Core Audio API",
                    }
                ))

            # 录音端点
            capture_count = len(audio_info.get("capture", []))
            readings.append(normal(
                "port_audio_capture_endpoints", capture_count, "个",
                "音频录音端点数（麦克风/线路输入等）", self._category,
                {"source": "Core Audio API"}
            ))
            for i, ep in enumerate(audio_info.get("capture", [])):
                ep_name = ep.get("name") or "(unnamed)"
                ep_desc = ep.get("description") or ""
                is_def = " [缺省]" if ep.get("is_default") else ""
                readings.append(normal(
                    f"port_audio_capture_{i+1}", ep_name, "",
                    f"录音设备 {i+1}: {ep_name}{is_def}", self._category,
                    {
                        "description": ep_desc,
                        "is_default": ep.get("is_default", False),
                        "state": ep.get("state"),
                        "source": "Core Audio API",
                    }
                ))
        except Exception as e:
            logging.debug(f"音频端点采集异常: {e}")

        # ── 3. Windows Audio 服务状态 ───────────────────────
        try:
            from .counter_reader import get_audio_service_status
            svc = get_audio_service_status()
            if svc:
                svc_status = svc.get("status", "Unknown")
                readings.append(normal(
                    "port_audio_service", svc_status, "",
                    f"Windows Audio 服务: {svc_status}",
                    self._category,
                    {"source": "WMI Win32_Service", "start_mode": svc.get("start_mode")}
                ))
        except Exception as e:
            logging.debug(f"音频服务采集异常: {e}")

        return readings

    def _collect_display_outputs(self):
        """采集显示输出接口"""
        readings = []
        # 根据 GPU 名称推断支持的显示接口
        try:
            import wmi
            c = wmi.WMI()
            for gpu in c.Win32_VideoController():
                name = getattr(gpu, 'Name', '') or ''
                # 常见显卡厂商的典型显示输出
                outputs = []
                if 'NVIDIA' in name or 'AMD' in name or 'Intel' in name:
                    # 从驱动程序推断支持的接口类型
                    outputs.append("HDMI" if any(m in name for m in ['HDMI']) else None)
                    outputs.append("DP" if any(m in name for m in ['DP', 'DisplayPort', 'Display']) else None)
                    outputs.append("DVI" if any(m in name for m in ['DVI']) else None)
                    outputs.append("VGA" if any(m in name for m in ['VGA']) else None)
                    outputs = [o for o in outputs if o]

                # 根据显卡型号推断常见输出
                if 'GTX' in name or 'RTX' in name:
                    outputs = list(set(outputs + ["HDMI", "DP", "DVI"]))
                elif 'RX' in name or 'Radeon' in name:
                    outputs = list(set(outputs + ["HDMI", "DP"]))
                elif 'Intel' in name:
                    outputs = list(set(outputs + ["HDMI", "DP", "VGA"]))
                # 实际连接的显示器数
                monitor_count = getattr(gpu, 'MonitorCount', None) or 0

                readings.append(normal(
                    "port_display_outputs", len(outputs), "个",
                    f"GPU 支持的显示输出接口数", self._category,
                    {
                        "interfaces": outputs,
                        "gpu": name,
                        "connected_monitors": monitor_count if monitor_count else None,
                        "max_resolution": getattr(gpu, 'VideoModeDescription', ''),
                    }
                ))
                break  # 只看主 GPU
        except ImportError:
            pass
        return readings

    def _collect_bluetooth(self):
        """采集蓝牙适配器"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
            bt_found = False
            for br in c.Win32_BluetoothRadio():
                bt_found = True
                name = getattr(br, 'Name', '蓝牙适配器') or '蓝牙适配器'
                soft_state = getattr(br, 'SoftwareEnabled', None)
                hw_state = getattr(br, 'HardwareEnabled', None)
                readings.append(normal(
                    "port_bluetooth", True, "bool",
                    f"蓝牙适配器: {name}", self._category,
                    {
                        "software_enabled": bool(soft_state) if soft_state is not None else None,
                        "hardware_enabled": bool(hw_state) if hw_state is not None else None,
                        "address": getattr(br, 'Address', ''),
                    }
                ))
            # 也通过 PnP 蓝牙设备检查
            if not bt_found:
                for dev in c.Win32_PnPEntity():
                    name = getattr(dev, 'Name', '') or ''
                    if 'bluetooth' in name.lower() or 'bt' in name.lower():
                        readings.append(normal(
                            "port_bluetooth_pnp", True, "bool",
                            f"蓝牙设备(PnP): {name}", self._category
                        ))
        except ImportError:
            pass
        return readings

    def _collect_m2_nvme(self):
        """采集 M.2 / NVMe 存储设备"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
            nvme_count = 0
            for disk in c.Win32_DiskDrive():
                model = getattr(disk, 'Model', '') or ''
                interface = getattr(disk, 'InterfaceType', '') or ''
                if 'NVMe' in model or 'NVMe' in interface or 'M.2' in model:
                    nvme_count += 1
                    readings.append(normal(
                        f"port_nvme_disk_{nvme_count}", model, "",
                        f"M.2/NVMe 固态硬盘: {model}", self._category,
                        {"interface_type": interface, "size_gb": round(int(getattr(disk, "Size", 0) or 0) / (1024**3), 1)}
                    ))
        except ImportError:
            pass
        return readings

    # ─── Linux ────────────────────────────────────────────────────

    def _collect_usb_linux(self):
        """Linux USB 设备检测"""
        readings = []
        try:
            import subprocess
            result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
            lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
            readings.append(normal(
                "port_usb_devices_total", len(lines), "个",
                "USB 设备总数 (lsusb)", self._category
            ))
            # USB 版本统计
            gen_count = {"1.x": 0, "2.0": 0, "3.x": 0}
            for line in lines:
                if "3." in line:
                    gen_count["3.x"] += 1
                elif "2.0" in line:
                    gen_count["2.0"] += 1
                else:
                    gen_count["1.x"] += 1
            for gen, count in gen_count.items():
                if count:
                    readings.append(normal(f"port_usb_{gen.replace('.', '_')}", count, "个",
                                           f"USB {gen} 设备数", self._category))
        except Exception:
            pass
        return readings

    def _collect_pci_devices(self):
        """Linux PCI 设备检测"""
        readings = []
        try:
            import subprocess
            result = subprocess.run(["lspci", "-v"], capture_output=True, text=True, timeout=10)
            # 分类 PCI 设备
            categories = {
                "USB": 0, "SATA": 0, "NVMe": 0, "Audio": 0,
                "Ethernet": 0, "WiFi": 0, "VGA": 0, "HDMI": 0
            }
            for line in result.stdout.split("\n"):
                for cat in categories:
                    if cat.lower() in line.lower():
                        categories[cat] += 1
                        break
            for cat, count in categories.items():
                if count:
                    readings.append(normal(
                        f"port_pci_{cat.lower()}", count, "个",
                        f"PCI {cat} 设备数", self._category
                    ))
        except Exception:
            pass
        return readings

    def _collect_audio_linux(self):
        """Linux 音频设备检测"""
        readings = []
        try:
            import subprocess
            # aplay -l 列出播放设备
            result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=5)
            card_count = len([l for l in result.stdout.split("\n") if "card" in l.lower()])
            if card_count:
                readings.append(normal("port_audio_playback_devices", card_count, "个",
                                       "音频播放设备数 (ALSA)", self._category))
            # arecord -l 列出录音设备
            result = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5)
            rec_count = len([l for l in result.stdout.split("\n") if "card" in l.lower()])
            if rec_count:
                readings.append(normal("port_audio_record_devices", rec_count, "个",
                                       "音频录制设备数 (ALSA)", self._category))
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return readings

    def _collect_bluetooth_linux(self):
        """Linux 蓝牙检测"""
        readings = []
        try:
            import subprocess
            result = subprocess.run(["hciconfig"], capture_output=True, text=True, timeout=5)
            lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
            if lines:
                readings.append(normal("port_bluetooth", True, "bool",
                                       "蓝牙适配器已检测到 (hciconfig)", self._category))
        except FileNotFoundError:
            pass
        except Exception:
            pass
        # 通过 rfkill
        try:
            import subprocess
            result = subprocess.run(["rfkill", "list"], capture_output=True, text=True, timeout=3)
            if "bluetooth" in result.stdout.lower():
                readings.append(normal("port_bluetooth_rfkill", True, "bool",
                                       "蓝牙 rfkill 可用", self._category))
        except Exception:
            pass
        return readings

    def _collect_overview_macos(self):
        """macOS 接口概览"""
        readings = []
        try:
            import subprocess
            result = subprocess.run(["system_profiler", "SPUSBDataType", "SPBluetoothDataType",
                                    "SPAudioDataType", "SPHardwareDataType"],
                                   capture_output=True, text=True, timeout=15)
            text = result.stdout
            # USB
            usb_count = text.count("USB Bus:") + text.count("USB:")
            if usb_count:
                readings.append(normal("port_usb_buses", usb_count, "个",
                                       "USB 总线数量", self._category))
            # 蓝牙
            if "Bluetooth" in text:
                readings.append(normal("port_bluetooth", True, "bool",
                                       "蓝牙可用", self._category))
            # 音频
            if "Audio" in text:
                readings.append(normal("port_audio_devices", True, "bool",
                                       "音频设备可用", self._category))
        except Exception:
            pass
        return readings
