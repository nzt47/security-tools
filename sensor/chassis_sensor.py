"""
机箱安全传感器 — 我的"皮肤与免疫系统"

采集机箱入侵检测、物理安全状态、机箱状态等信息。
机箱是我的皮肤——它能感知是否被打开（入侵），保护内部器官安全。

支持:
- Windows: WMI Win32_SystemEnclosure, Win32_SecuritySettings
- Linux: DMI/SMBIOS chassis intrusion (dmidecode)
- macOS: IORegistry 机箱信息
"""
import logging
import platform
import os
import subprocess
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

SYSTEM = platform.system()


class ChassisSensor:
    """机箱安全传感器，负责监测皮肤与免疫系统"""

    CAPABILITIES = {
        "name": "chassis",
        "description": "机箱（皮肤与免疫）— 温度、风扇、电源",
        "category": Category.CHASSIS,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["wmi", "comtypes"],
    }

    def __init__(self):
        self._category = Category.CHASSIS

    def collect(self):
        """
        采集机箱安全状态。
        返回 SensorReading 列表。
        """
        results = []
        if SYSTEM == "Windows":
            try:
                results.extend(self._collect_windows())
            except Exception as e:
                logging.error(f"Windows 机箱信息采集失败: {e}")
        elif SYSTEM == "Linux":
            try:
                results.extend(self._collect_linux())
            except Exception as e:
                logging.error(f"Linux 机箱信息采集失败: {e}")
        elif SYSTEM == "Darwin":
            try:
                results.extend(self._collect_macos())
            except Exception as e:
                logging.error(f"macOS 机箱信息采集失败: {e}")

        return results

    def _collect_windows(self):
        """Windows WMI 机箱安全采集"""
        readings = []
        try:
            import wmi
            c = wmi.WMI()
        except ImportError:
            logging.debug("WMI 未安装")
            return readings
        except Exception as e:
            logging.warning(f"WMI 初始化失败: {e}")
            return readings

        # 机箱入侵检测 (Win32_SystemEnclosure)
        try:
            for enclosure in c.Win32_SystemEnclosure():
                # ChassisTypes: 机箱类型
                chassis_types_map = {
                    1: "其他", 2: "未知", 3: "台式机", 4: "薄型台式机",
                    5: "迷你塔式", 6: "塔式", 7: "便携式", 8: "笔记本电脑",
                    9: "笔记本", 10: "手持设备", 11: "扩展坞", 12: "一体机",
                    13: "平板", 14: "可旋转", 15: "可拆卸",
                }
                chassis_type = getattr(enclosure, 'ChassisTypes', None)
                if chassis_type is not None and isinstance(chassis_type, (list, tuple)):
                    type_id = chassis_type[0] if len(chassis_type) > 0 else 2
                    type_name = chassis_types_map.get(type_id, f"未知({type_id})")
                    readings.append(normal(
                        "chassis_type", type_id, "",
                        f"机箱类型: {type_name}（我的身体形态）", self._category,
                        {"name": type_name}
                    ))
                # 机箱序列号
                serial = getattr(enclosure, 'SerialNumber', None)
                if serial:
                    readings.append(normal(
                        "chassis_serial", serial, "",
                        "机箱序列号", self._category
                    ))
                # 制造商
                mfr = getattr(enclosure, 'Manufacturer', None)
                if mfr:
                    readings.append(normal(
                        "chassis_manufacturer", mfr, "",
                        "机箱制造商", self._category
                    ))
                # 锁定状态
                lock = getattr(enclosure, 'LockPresent', None)
                if lock is not None:
                    readings.append(normal(
                        "chassis_lock_present", lock, "bool",
                        "机箱是否支持物理锁定", self._category
                    ))
                # 安全状态 (SecurityBreach / SecurityStatus)
                breach = getattr(enclosure, 'SecurityBreach', None)
                if breach is not None:
                    # SecurityBreach: 1=Other, 2=Unknown, 3=No Breach, 4=Breach Attempted, 5=Breach Successful
                    breach_map = {1: "其他", 2: "未知", 3: "安全（未被打开）", 4: "检测到入侵尝试", 5: "侵入成功！"}
                    breach_desc = breach_map.get(breach, f"未知({breach})")
                    sev = Severity.CRITICAL if breach in (4, 5) else Severity.NORMAL
                    readings.append(SensorReading(
                        "chassis_intrusion", breach, "",
                        f"机箱入侵状态 — {breach_desc}", self._category, sev,
                        {"raw": breach, "description": breach_desc}
                    ))
                # SMBIOS 资产标签
                tag = getattr(enclosure, 'SMBIOSAssetTag', None)
                if tag:
                    readings.append(normal(
                        "chassis_asset_tag", tag, "",
                        "机箱资产标签", self._category
                    ))
                break
        except Exception as e:
            logging.debug(f"WMI 机箱采集: {e}")

        # 物理安全设置 (Win32_SecuritySettingOfLogicalFile 等)
        try:
            for security in c.Win32_SecuritySettings():
                # 系统安全设置概览
                pass
        except Exception:
            pass

        # TPM 安全芯片状态
        try:
            for tpm in c.Win32_Tpm():
                is_activated = getattr(tpm, 'IsActivated_InitialValue', None)
                is_enabled = getattr(tpm, 'IsEnabled_InitialValue', None)
                if is_activated is not None:
                    readings.append(normal(
                        "chassis_tpm_activated", is_activated, "bool",
                        "TPM 安全芯片已激活", self._category
                    ))
                if is_enabled is not None:
                    readings.append(normal(
                        "chassis_tpm_enabled", is_enabled, "bool",
                        "TPM 安全芯片已启用", self._category
                    ))
                break
        except Exception:
            pass

        # Secure Boot 状态
        try:
            for item in c.Win32_DeviceGuard():
                security_services = getattr(item, 'SecurityServicesRunning', None)
                if security_services is not None:
                    readings.append(normal(
                        "chassis_device_guard", security_services, "",
                        "Device Guard 安全服务状态", self._category
                    ))
                break
        except Exception:
            pass

        return readings

    def _collect_linux(self):
        """Linux 平台机箱安全采集"""
        readings = []
        # DMI 机箱入侵检测
        try:
            result = subprocess.run(
                ["dmidecode", "-t", "chassis"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if "Manufacturer:" in line:
                        readings.append(normal(
                            "chassis_manufacturer", line.split(":", 1)[1].strip(), "",
                            "机箱制造商(DMI)", self._category
                        ))
                    elif "Type:" in line:
                        readings.append(normal(
                            "chassis_type", line.split(":", 1)[1].strip(), "",
                            "机箱类型(DMI)", self._category
                        ))
                    elif "Lock:" in line:
                        readings.append(normal(
                            "chassis_lock", line.split(":", 1)[1].strip(), "",
                            "机箱锁状态(DMI)", self._category
                        ))
                    elif "Boot-up State:" in line:
                        state = line.split(":", 1)[1].strip()
                        readings.append(normal(
                            "chassis_boot_state", state, "",
                            "机箱启动状态(DMI)", self._category
                        ))
                    elif "Power Supply State:" in line:
                        readings.append(normal(
                            "chassis_power_state", line.split(":", 1)[1].strip(), "",
                            "机箱电源状态(DMI)", self._category
                        ))
                    elif "Thermal State:" in line:
                        readings.append(normal(
                            "chassis_thermal_state", line.split(":", 1)[1].strip(), "",
                            "机箱散热状态(DMI)", self._category
                        ))
                    elif "Security Status:" in line:
                        status = line.split(":", 1)[1].strip().lower()
                        sev = Severity.CRITICAL if "unauthorized" in status or "breach" in status else Severity.NORMAL
                        readings.append(SensorReading(
                            "chassis_security", status, "",
                            "机箱安全状态(DMI)", self._category, sev
                        ))
        except FileNotFoundError:
            logging.debug("dmidecode 不可用")
        except Exception as e:
            logging.debug(f"DMI 机箱采集: {e}")

        # /sys/class/dmi 备选
        dmi_dir = "/sys/class/dmi/id"
        if os.path.exists(dmi_dir):
            dmi_files = {
                "chassis_vendor": "机箱制造商(sysfs)",
                "chassis_type": "机箱类型编码(sysfs)",
                "chassis_version": "机箱版本(sysfs)",
                "chassis_serial": "机箱序列号(sysfs)",
            }
            for filename, desc in dmi_files.items():
                filepath = os.path.join(dmi_dir, filename)
                if os.path.exists(filepath):
                    try:
                        with open(filepath) as f:
                            val = f.read().strip()
                            if val and val != "None" and val != "To be filled by O.E.M.":
                                readings.append(normal(
                                    f"chassis_{filename}", val, "",
                                    desc, self._category
                                ))
                    except Exception:
                        pass

        return readings

    def _collect_macos(self):
        """macOS 平台机箱安全采集"""
        readings = []
        try:
            # 使用 system_profiler 获取安全信息
            result = subprocess.run(
                ["system_profiler", "SPHardwareDataType", "SPSecureElementDataType"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split("\n"):
                line = line.strip()
                if "Activation Lock Status:" in line:
                    readings.append(normal(
                        "chassis_activation_lock", line.split(":", 1)[1].strip(), "",
                        "激活锁状态", self._category
                    ))
        except Exception:
            pass
        return readings
