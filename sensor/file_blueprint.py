"""
文件蓝图 — 我的"文件系统解剖图"

穷尽所有文件系统结构、存储卷、目录挂载点和关键路径，
标注检测方式。
"""
import logging
import platform
import os
from .sensor_reading import SensorReading, Severity, Category, normal

SYSTEM = platform.system()


class FileBlueprint:
    """
    文件蓝图 — 文件系统解剖图

    枚举文件系统中的卷、目录结构、关键路径。
    """

    def __init__(self):
        self._category = Category.FILE

    def collect(self):
        readings = []
        for entry in self._build_blueprint():
            try:
                count = self._detect_count(entry)
                entry["detected_count"] = count
                readings.append(self._entry_to_reading(entry))
            except Exception as e:
                logging.debug(f"文件蓝图项 {entry['name']} 检测异常: {e}")
        return readings

    def _build_blueprint(self):
        blueprint = []

        # 存储卷
        blueprint.append({"name": "系统盘 (C:)", "type": "system_drive", "method": "software_detectable",
                          "sources": ["os.environ SystemDrive", "platform"]})
        blueprint.append({"name": "NTFS 文件系统", "type": "filesystem", "method": "software_detectable",
                          "sources": ["WMI Win32_LogicalFileSystem"]})
        blueprint.append({"name": "ReFS 文件系统", "type": "filesystem", "method": "software_detectable",
                          "sources": ["WMI Win32_LogicalFileSystem / fsutil"]})
        blueprint.append({"name": "FAT32 分区", "type": "filesystem", "method": "software_detectable",
                          "sources": ["WMI Win32_LogicalDisk"]})

        # 卷/分区
        blueprint.append({"name": "硬盘分区数", "type": "partition", "method": "software_detectable",
                          "sources": ["psutil.disk_partitions"]})
        blueprint.append({"name": "光驱/ISO 挂载", "type": "optical_volume", "method": "software_detectable",
                          "sources": ["psutil.disk_partitions (cdrom)"]})
        blueprint.append({"name": "网络映射驱动器", "type": "network_volume", "method": "software_detectable",
                          "sources": ["WMI Win32_MappedLogicalDisk"]})
        blueprint.append({"name": "BitLocker 加密卷", "type": "encrypted_volume", "method": "software_detectable",
                          "sources": ["WMI Win32_EncryptableVolume", "manage-bde"]})
        blueprint.append({"name": "内存盘/RAM Disk", "type": "ram_volume", "method": "software_detectable",
                          "sources": ["psutil.disk_partitions + 卷名分析"]})

        # 关键系统目录
        blueprint.append({"name": "Windows 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ SystemRoot"]})
        blueprint.append({"name": "Program Files", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ ProgramFiles"]})
        blueprint.append({"name": "Program Files (x86)", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ ProgramFiles(x86)"]})
        blueprint.append({"name": "Users 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ UserProfile"]})
        blueprint.append({"name": "AppData 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ APPDATA"]})
        blueprint.append({"name": "Temp 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ TEMP"]})
        blueprint.append({"name": "桌面目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ USERPROFILE + Desktop"]})
        blueprint.append({"name": "文档目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ USERPROFILE + Documents"]})
        blueprint.append({"name": "下载目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ USERPROFILE + Downloads"]})

        # 特殊目录
        blueprint.append({"name": "System32 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ SystemRoot + System32"]})
        blueprint.append({"name": "ProgramData 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ ProgramData"]})
        blueprint.append({"name": "Public 目录", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ PUBLIC"]})
        blueprint.append({"name": "Common AppData", "type": "sysdir", "method": "software_detectable",
                          "sources": ["os.environ CommonAppData"]})

        # 文件系统功能
        blueprint.append({"name": "系统还原/卷影复制", "type": "fs_feature", "method": "software_detectable",
                          "sources": ["WMI Win32_ShadowCopy"]})
        blueprint.append({"name": "文件压缩支持", "type": "fs_feature", "method": "software_detectable",
                          "sources": ["fsutil 压缩属性判定"]})
        blueprint.append({"name": "磁盘配额", "type": "fs_feature", "method": "software_detectable",
                          "sources": ["WMI Win32_DiskQuota"]})

        # 文件监控
        blueprint.append({"name": "文件变更监测", "type": "fs_watch", "method": "inference",
                          "sources": ["FileWatcher 可监测目录数"]})
        blueprint.append({"name": "实时文件同步状态", "type": "fs_sync", "method": "inference",
                          "sources": ["文件同步服务运行状态判定"]})

        return blueprint

    def _detect_count(self, entry):
        name = entry["name"]
        entry_type = entry["type"]

        if entry["method"] == "manual_check":
            return None

        try:
            import psutil
            if entry_type == "system_drive":
                sd = os.environ.get("SystemDrive", "C:")
                try:
                    usage = psutil.disk_usage(sd + "\\")
                    return f"{sd} ({usage.total // (1024**3)} GB)"
                except Exception:
                    return sd

            if entry_type == "filesystem":
                # 检查文件系统类型
                for part in psutil.disk_partitions():
                    if name == "NTFS 文件系统" and part.fstype.upper() == "NTFS":
                        return True
                    if name == "ReFS 文件系统" and "ReFS" in part.fstype:
                        return True
                    if name == "FAT32 分区" and part.fstype.upper() == "FAT32":
                        return True
                return False

            if entry_type == "partition":
                count = 0
                for _ in psutil.disk_partitions():
                    count += 1
                return count

            if entry_type == "optical_volume":
                count = 0
                for part in psutil.disk_partitions():
                    if "cdrom" in part.opts:
                        count += 1
                return count

            if entry_type == "network_volume":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        count = 0
                        for _ in c.Win32_MappedLogicalDisk():
                            count += 1
                        return count
                    except Exception:
                        return "见 WMI"
                return "非 Windows"

            if entry_type == "encrypted_volume":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        count = 0
                        for _ in c.Win32_EncryptableVolume():
                            count += 1
                        return count
                    except Exception:
                        return "见 manage-bde"
                return "非 Windows"

            if entry_type == "ram_volume":
                count = 0
                for part in psutil.disk_partitions():
                    if "RAM" in part.device.upper() or "TMPFS" in part.fstype.upper():
                        count += 1
                return count

            if entry_type == "sysdir":
                env_map = {
                    "Windows 目录": "SystemRoot",
                    "Program Files": "ProgramFiles",
                    "Program Files (x86)": "ProgramFiles(x86)",
                    "Users 目录": "USERPROFILE",
                    "AppData 目录": "APPDATA",
                    "Temp 目录": "TEMP" if SYSTEM == "Windows" else "TMP",
                    "桌面目录": "USERPROFILE",
                    "文档目录": "USERPROFILE",
                    "下载目录": "USERPROFILE",
                }
                env_var = env_map.get(name)
                if env_var:
                    val = os.environ.get(env_var, "")
                    if name == "桌面目录":
                        return os.path.join(val or "", "Desktop")
                    if name == "文档目录":
                        return os.path.join(val or "", "Documents")
                    if name == "下载目录":
                        return os.path.join(val or "", "Downloads")
                    if name == "System32 目录":
                        root = os.environ.get("SystemRoot", "")
                        return os.path.join(root, "System32")
                    if name == "ProgramData 目录":
                        return os.environ.get("ProgramData", "")
                    if name == "Public 目录":
                        return os.environ.get("PUBLIC", "")
                    if name == "Common AppData":
                        return os.environ.get("CommonAppData", "")
                    return val or "未设置"
                return "检测到"

            if entry_type == "fs_feature":
                if "卷影复制" in name:
                    if SYSTEM == "Windows":
                        try:
                            import wmi
                            c = wmi.WMI()
                            count = 0
                            for _ in c.Win32_ShadowCopy():
                                count += 1
                            return count
                        except Exception:
                            return "见 vssadmin"
                    return "非 Windows"
                return "检测到"

            if entry_type == "fs_watch":
                return "取决于 watcher 配置"
            if entry_type == "fs_sync":
                return "见同步服务状态"

            return "检测到"
        except Exception as e:
            logging.debug(f"文件蓝图检测 {name}: {e}")
            return "检测失败"

    def _entry_to_reading(self, entry):
        name = entry["name"]
        method = entry["method"]
        count = entry.get("detected_count")
        desc = entry.get("description", f"{name} — 检测方式: {method}")

        method_icon = {
            "software_detectable": "✅ 软件检测",
            "inference": "⚡ 推断",
            "manual_check": "🔧 需人工检查",
        }
        method_label = method_icon.get(method, method)

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

        value_display = f"{result} [{method_label}]"
        metadata = {"method": method, "device_type": entry["type"]}
        if entry.get("sources"):
            metadata["detection_source"] = entry["sources"]

        return SensorReading(
            f"file_blueprint_{name.replace(' ', '_').replace('/', '_')}",
            value_display, "",
            desc, self._category, sev, metadata
        )
