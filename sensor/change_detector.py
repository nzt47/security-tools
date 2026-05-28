"""
变更检测传感器 — 我的"免疫记忆"监测器

检测软硬件配置的变化，记录每一次变更事件。
像免疫系统一样，我能感知"身体"中任何不寻常的变化：
- 硬件变更：设备插拔、驱动更新、新硬件安装
- 软件变更：进程新增/消失、服务变化、注册表/配置变更
- 文件变更：关键目录中的文件增删改（由 file_watcher 补充）

变更检测采用"快照对比"机制：
1. 首次采集时生成基准快照
2. 后续采集时与基准对比
3. 差异即为变更事件
"""
import logging
import platform
import hashlib
import json
import os
from datetime import datetime
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

DEFAULT_LOG_DIR = os.path.expanduser("~/.lingxi")

SYSTEM = platform.system()

# ── 注册表监控路径 ──────────────────────────────────────────────
REGISTRY_WATCH_PATHS = [
    # 开机启动项
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", None),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", None),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run", None),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce", None),
    # Windows 版本信息（关键字段）
    ("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
     ["ProductName", "CurrentBuild", "DisplayVersion", "EditionID", "InstallDate",
      "CurrentMajorVersionNumber", "CurrentMinorVersionNumber"]),
    # 用户环境变量（持久化值）
    ("HKCU", r"Environment", None),
    # 系统环境变量（持久化值 — PATH 子集）
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
     ["PATH", "TMP", "TEMP", "PATHEXT"]),
    # 启动配置
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager", ["BootExecute"]),
    # Windows 功能配置
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
     ["EnableLUA", "ConsentPromptBehaviorAdmin", "EnableVirtualization"]),
]

REGISTRY_HIVE_MAP = {
    "HKLM": None,  # winreg.HKEY_LOCAL_MACHINE
    "HKCU": None,  # winreg.HKEY_CURRENT_USER
}


class ChangeDetector:
    """变更检测传感器，负责监测身体变化并产生免疫记忆"""

    def __init__(self):
        self._category = Category.CHANGE
        self._baseline = None  # 基准快照
        self._last_check = None  # 上次检查结果
        self._change_log = []  # 变更日志（内存）
        self._persistent_log_dir = os.path.join(DEFAULT_LOG_DIR, "changes")
        self._persistent_log_path = os.path.join(self._persistent_log_dir, "change_log.json")
        self._load_persistent_log()

    def set_baseline(self):
        """建立基准快照 — 记录我当前的身体状态"""
        self._baseline = self._capture_snapshot()
        self._last_check = self._baseline
        logging.info("已建立变更检测基准快照。")
        return self._baseline

    def collect(self):
        """
        采集自基准快照以来的所有变更。
        返回 SensorReading 列表。
        首次调用时自动建立基准。
        """
        if self._baseline is None:
            self.set_baseline()
            return [normal(
                "change_baseline_established", True, "bool",
                "变更检测基准已建立（免疫系统初始化）", self._category
            )]

        current = self._capture_snapshot()
        changes = self._compare_snapshots(self._last_check or self._baseline, current)
        self._last_check = current

        results = []
        for change in changes:
            sev = Severity.WARNING if change.get("severity") == "warning" else (
                Severity.CRITICAL if change.get("severity") == "critical" else Severity.NORMAL
            )
            results.append(SensorReading(
                change["name"], change["value"], "",
                change["description"], self._category, sev,
                {"change_type": change["type"], "detail": change.get("detail", ""),
                 "previous": change.get("previous"), "current": change.get("current")}
            ))
            self._change_log.append(change)

        return results

    def _capture_snapshot(self):
        """捕获当前时刻的完整快照"""
        snapshot = {}
        snapshot["timestamp"] = __import__('datetime').datetime.now().isoformat()
        snapshot["devices"] = self._list_devices()
        snapshot["disk_partitions"] = self._list_disk_partitions()
        snapshot["processes"] = self._list_processes()
        snapshot["services"] = self._list_services()
        snapshot["system_info"] = self._get_system_info()
        snapshot["registry"] = self._capture_registry()
        snapshot["environment"] = self._capture_environment()
        # 生成快照摘要哈希
        snapshot["hash"] = hashlib.sha256(
            json.dumps(snapshot, default=str, sort_keys=True).encode()
        ).hexdigest()[:16]
        return snapshot

    def _compare_snapshots(self, old, new):
        """对比两个快照，返回变更列表"""
        changes = []
        if old is None:
            return changes
        # 设备变更
        changes.extend(self._diff_devices(old.get("devices", {}), new.get("devices", {})))
        # 分区变更
        changes.extend(self._diff_partitions(old.get("disk_partitions", {}), new.get("disk_partitions", {})))
        # 进程变更
        changes.extend(self._diff_processes(old.get("processes", []), new.get("processes", [])))
        # 服务变更
        changes.extend(self._diff_services(old.get("services", []), new.get("services", [])))
        # 系统信息变更
        changes.extend(self._diff_system_info(old.get("system_info", {}), new.get("system_info", {})))
        # 注册表变更
        changes.extend(self._diff_registry(old.get("registry", {}), new.get("registry", {})))
        # 环境变量变更
        changes.extend(self._diff_environment(old.get("environment", {}), new.get("environment", {})))
        return changes

    # ─── 快照采集方法 ────────────────────────────────────────────

    def _list_devices(self):
        """列出当前设备"""
        import psutil
        devices = {}
        try:
            if SYSTEM == "Windows":
                try:
                    import wmi
                    c = wmi.WMI()
                    for dev in c.Win32_PnPEntity():
                        name = getattr(dev, 'Name', '') or getattr(dev, 'Caption', '')
                        status = getattr(dev, 'Status', '')
                        class_guid = getattr(dev, 'ClassGuid', '')
                        if name:
                            key = f"{name}|{class_guid}"
                            devices[key] = {"name": name, "status": status, "class": class_guid}
                except ImportError:
                    pass
            elif SYSTEM == "Linux":
                # /sys/devices 下的设备
                pci_devs = "/sys/bus/pci/devices"
                if os.path.exists(pci_devs):
                    for dev in os.listdir(pci_devs):
                        devices[dev] = {"name": dev, "type": "pci"}
                usb_devs = "/sys/bus/usb/devices"
                if os.path.exists(usb_devs):
                    for dev in os.listdir(usb_devs):
                        devices[dev] = {"name": dev, "type": "usb"}
            elif SYSTEM == "Darwin":
                try:
                    import subprocess
                    result = subprocess.run(["system_profiler", "SPUSBDataType", "-xml"], capture_output=True, text=True, timeout=10)
                    devices["usb"] = {"raw": result.stdout[:500]}  # 截断防止过大
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"设备列表采集失败: {e}")
        return devices

    def _list_disk_partitions(self):
        """列出当前磁盘分区"""
        import psutil
        parts = {}
        try:
            for part in psutil.disk_partitions():
                parts[part.device] = {"mountpoint": part.mountpoint, "fstype": part.fstype}
        except Exception as e:
            logging.warning(f"磁盘分区列表采集失败: {e}")
        return parts

    def _list_processes(self):
        """列出当前运行进程（仅名称和 PID，不包含路径等敏感信息）"""
        import psutil
        procs = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'status']):
                try:
                    info = proc.info
                    procs.append({"pid": info["pid"], "name": info["name"], "status": info["status"]})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logging.warning(f"进程列表采集失败: {e}")
        return procs

    def _list_services(self):
        """列出关键系统服务"""
        services = []
        try:
            if SYSTEM == "Windows":
                try:
                    import wmi
                    c = wmi.WMI()
                    for svc in c.Win32_Service():
                        services.append({
                            "name": getattr(svc, 'Name', ''),
                            "state": getattr(svc, 'State', ''),
                            "start_mode": getattr(svc, 'StartMode', ''),
                        })
                except ImportError:
                    pass
            elif SYSTEM == "Linux":
                import subprocess
                result = subprocess.run(["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"],
                                       capture_output=True, text=True, timeout=5)
                for line in result.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 3:
                        services.append({"name": parts[0], "state": parts[2], "description": " ".join(parts[4:]) if len(parts) > 4 else ""})
            elif SYSTEM == "Darwin":
                import subprocess
                result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 3:
                        services.append({"pid": parts[0], "name": parts[2], "state": "running" if parts[1] == "0" else "error"})
        except Exception as e:
            logging.debug(f"服务列表采集失败: {e}")
        return services

    def _get_system_info(self):
        """获取系统版本信息"""
        return {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "hostname": platform.node(),
        }

    # ─── 差异计算 ─────────────────────────────────────────────────

    def _diff_devices(self, old_devices, new_devices):
        """计算设备变更"""
        changes = []
        old_keys = set(old_devices.keys())
        new_keys = set(new_devices.keys())

        added = new_keys - old_keys
        removed = old_keys - new_keys
        changed = set()

        for key in old_keys & new_keys:
            if old_devices[key] != new_devices[key]:
                changed.add(key)

        for key in added:
            info = new_devices[key]
            changes.append({
                "name": "change_device_added",
                "value": info.get("name", key),
                "type": "device_added",
                "severity": "normal",
                "description": f"新设备接入: {info.get('name', key)}（我感知到了新的硬件）",
                "detail": json.dumps(info, ensure_ascii=False),
                "current": info,
            })
        for key in removed:
            info = old_devices[key]
            changes.append({
                "name": "change_device_removed",
                "value": info.get("name", key),
                "type": "device_removed",
                "severity": "warning",
                "description": f"设备移除: {info.get('name', key)}（我的某个硬件被移除了）",
                "previous": info,
            })
        for key in changed:
            changes.append({
                "name": "change_device_modified",
                "value": key,
                "type": "device_modified",
                "severity": "warning",
                "description": f"设备状态变更: {key}",
                "previous": old_devices[key],
                "current": new_devices[key],
            })
        return changes

    def _diff_partitions(self, old_parts, new_parts):
        """计算磁盘分区变更"""
        changes = []
        old_keys = set(old_parts.keys())
        new_keys = set(new_parts.keys())

        for key in new_keys - old_keys:
            info = new_parts[key]
            changes.append({
                "name": "change_disk_mounted",
                "value": key,
                "type": "disk_mounted",
                "severity": "normal",
                "description": f"新磁盘挂载: {key} -> {info.get('mountpoint', '')}",
                "current": info,
            })
        for key in old_keys - new_keys:
            info = old_parts[key]
            changes.append({
                "name": "change_disk_unmounted",
                "value": key,
                "type": "disk_unmounted",
                "severity": "warning",
                "description": f"磁盘卸载: {key} ({info.get('mountpoint', '')})",
                "previous": info,
            })
        return changes

    def _diff_processes(self, old_procs, new_procs):
        """计算进程变更（只报告显著变化）"""
        changes = []
        old_names = {p["name"] for p in old_procs}
        new_names = {p["name"] for p in new_procs}

        # 只关注显著的进程增减（超过阈值才报告，避免噪音）
        added = new_names - old_names
        removed = old_names - new_names

        # 过滤系统临时进程
        ignore_patterns = {"ThreadPoolForegroundWorker", "conhost", "dllhost"}
        added = {a for a in added if a not in ignore_patterns}
        removed = {r for r in removed if r not in ignore_patterns}

        if added:
            names = ", ".join(sorted(added)[:10])
            if len(added) > 10:
                names += f" 等共{len(added)}个"
            changes.append({
                "name": "change_process_started",
                "value": len(added),
                "type": "process_started",
                "severity": "normal",
                "description": f"新进程启动: {names}",
                "detail": sorted(added)[:50],
                "count": len(added),
            })
        if removed:
            names = ", ".join(sorted(removed)[:10])
            if len(removed) > 10:
                names += f" 等共{len(removed)}个"
            changes.append({
                "name": "change_process_stopped",
                "value": len(removed),
                "type": "process_stopped",
                "severity": "normal",
                "description": f"进程终止: {names}",
                "detail": sorted(removed)[:50],
                "count": len(removed),
            })
        return changes

    def _diff_services(self, old_services, new_services):
        """计算服务变更"""
        changes = []
        old_map = {s["name"]: s.get("state", "") for s in old_services}
        new_map = {s["name"]: s.get("state", "") for s in new_services}

        for name in set(list(old_map.keys())[:100]) & set(list(new_map.keys())[:100]):
            if old_map.get(name) != new_map.get(name):
                changes.append({
                    "name": "change_service_state",
                    "value": name,
                    "type": "service_state_changed",
                    "severity": "critical" if "stop" in str(new_map.get(name, "")).lower() else "warning",
                    "description": f"服务状态变更: {name} ({old_map.get(name)} -> {new_map.get(name)})",
                    "previous": old_map.get(name),
                    "current": new_map.get(name),
                })
        return changes

    def _diff_system_info(self, old_info, new_info):
        """计算系统信息变更"""
        changes = []
        for key in old_info:
            if key in new_info and old_info[key] != new_info[key]:
                changes.append({
                    "name": "change_system_info",
                    "value": key,
                    "type": "system_info_changed",
                    "severity": "critical",
                    "description": f"系统信息变更: {key} ({old_info[key]} -> {new_info[key]})",
                    "previous": old_info[key],
                    "current": new_info[key],
                })
        return changes

    # ── 注册表快照 ────────────────────────────────────────────────

    @staticmethod
    def _ensure_hive_map():
        """懒加载 winreg hive 常量（避免 import 时 winreg 不可用）"""
        if REGISTRY_HIVE_MAP["HKLM"] is None:
            import winreg
            REGISTRY_HIVE_MAP["HKLM"] = winreg.HKEY_LOCAL_MACHINE
            REGISTRY_HIVE_MAP["HKCU"] = winreg.HKEY_CURRENT_USER

    def _capture_registry(self):
        """捕获关键注册表路径的快照"""
        if SYSTEM != "Windows":
            return {}
        self._ensure_hive_map()
        import winreg
        result = {}
        for hive_name, subkey, names in REGISTRY_WATCH_PATHS:
            hive = REGISTRY_HIVE_MAP.get(hive_name)
            if hive is None:
                continue
            path_key = f"{hive_name}\\{subkey}"
            try:
                key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
                result[path_key] = {}
                if names is None:
                    # 枚举所有值
                    i = 0
                    while True:
                        try:
                            vname, vdata, vtype = winreg.EnumValue(key, i)
                            result[path_key][vname or "(Default)"] = self._sanitize_reg_value(vdata, vtype)
                            i += 1
                        except OSError:
                            break
                else:
                    # 读取指定字段
                    for name in names:
                        try:
                            vdata, vtype = winreg.QueryValueEx(key, name)
                            result[path_key][name] = self._sanitize_reg_value(vdata, vtype)
                        except FileNotFoundError:
                            result[path_key][name] = None
                winreg.CloseKey(key)
            except Exception as e:
                logging.debug(f"注册表读取失败 {path_key}: {e}")
        return result

    @staticmethod
    def _sanitize_reg_value(data, vtype):
        """将注册表值序列化为可 JSON/hash 的格式"""
        if vtype == 4:  # REG_DWORD
            return data
        if vtype == 3:  # REG_BINARY
            return f"<binary {len(data)} bytes>"
        if isinstance(data, str):
            return data[:1000] if len(data) > 1000 else data
        if isinstance(data, bytes):
            try:
                s = data.decode("utf-8", errors="replace")
                return s[:1000] if len(s) > 1000 else s
            except Exception:
                return f"<bytes {len(data)}>"
        return str(data)

    def _capture_environment(self):
        """捕获当前进程环境变量快照"""
        return dict(os.environ)

    # ── 注册表差异 ────────────────────────────────────────────────

    def _diff_registry(self, old_reg, new_reg):
        """计算注册表变更"""
        changes = []
        all_keys = set(old_reg.keys()) | set(new_reg.keys())
        for key in sorted(all_keys):
            old_vals = old_reg.get(key, {})
            new_vals = new_reg.get(key, {})
            all_names = set(old_vals.keys()) | set(new_vals.keys())
            for name in sorted(all_names):
                old_v = old_vals.get(name)
                new_v = new_vals.get(name)
                if old_v != new_v:
                    changes.append({
                        "name": "change_registry",
                        "value": f"{key}\\{name}",
                        "type": "registry_changed",
                        "severity": "warning",
                        "description": f"注册表变更: {key} → {name}",
                        "previous": old_v,
                        "current": new_v,
                    })
        return changes

    def _diff_environment(self, old_env, new_env):
        """计算环境变量变更"""
        changes = []
        all_keys = set(old_env.keys()) | set(new_env.keys())
        for key in sorted(all_keys):
            old_v = old_env.get(key)
            new_v = new_env.get(key)
            if old_v != new_v:
                changes.append({
                    "name": "change_environment",
                    "value": key,
                    "type": "environment_changed",
                    "severity": "warning",
                    "description": f"环境变量变更: {key}",
                    "previous": old_v,
                    "current": new_v,
                })
        return changes

    @property
    def change_log(self):
        """返回变更日志（内存）"""
        return self._change_log

    @property
    def persistent_change_log(self):
        """返回持久化变更日志（磁盘）"""
        return self._persistent_log

    @property
    def baseline_hash(self):
        """返回基准快照哈希"""
        return self._baseline.get("hash") if self._baseline else None

    # ═══════════════════════════════════════════════════════════
    #  持久化变更日志
    # ═══════════════════════════════════════════════════════════

    def _load_persistent_log(self):
        """从磁盘加载持久化变更日志"""
        try:
            if os.path.exists(self._persistent_log_path):
                with open(self._persistent_log_path, "r", encoding="utf-8") as f:
                    self._persistent_log = json.load(f)
                logging.info(f"已加载 {len(self._persistent_log)} 条历史变更记录。")
            else:
                self._persistent_log = []
        except Exception as e:
            logging.debug(f"加载持久化变更日志失败: {e}")
            self._persistent_log = []

    def _save_to_persistent_log(self, change_entry):
        """将变更记录保存到磁盘"""
        try:
            os.makedirs(self._persistent_log_dir, exist_ok=True)
            self._persistent_log.append(change_entry)
            if len(self._persistent_log) > 10000:
                self._persistent_log = self._persistent_log[-10000:]
            with open(self._persistent_log_path, "w", encoding="utf-8") as f:
                json.dump(self._persistent_log, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.debug(f"持久化变更日志写入失败: {e}")

    def register_change_from_event(self, change_entry):
        """
        注册来自 EventMonitor 的实时变更事件。

        将事件编码为标准的变更日志格式，写入持久化日志。
        """
        entry = {
            "timestamp": change_entry.get("timestamp", datetime.now().isoformat()),
            "event_type": change_entry.get("event_type", "unknown"),
            "value": change_entry.get("device_name", ""),
            "type": f"hardware_{change_entry.get('event_type', 'event')}",
            "severity": "critical" if "failure" in str(change_entry.get("event_type", "")) else "warning",
            "description": change_entry.get("detail", f"硬件{change_entry.get('event_type', '变化')}"),
            "detail": change_entry,
        }
        self._change_log.append(entry)
        self._save_to_persistent_log(entry)
        return entry
