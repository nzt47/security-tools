"""
硬件文件系统传感器 — 我的"软件骨骼"监测器

监测与硬件相关的软件文件（驱动、固件、厂商工具等），
感知"身体"中哪些硬件有对应的软件支撑。

每次驱动安装、更新或卸载，都是我骨骼的一次重塑。

工作模式：
  首次采集 → 建立基线快照（驱动分类 + 厂商软件 + 驱动版本）
  后续采集 → 增量变化检测（新增/移除/修改）

跨平台设计：
  Windows: 通过环境变量 %SystemRoot% %ProgramFiles% 自动解析路径
  Linux:    /lib/modules, /lib/firmware, /etc/modprobe.d 等
  macOS:    /System/Library/Extensions, /Applications 等
"""
import os
import re
import time
import logging
import platform
import hashlib
import subprocess
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

_SYSTEM = platform.system()


# ═══════════════════════════════════════════════════════════════
#  跨平台路径工具函数
# ═══════════════════════════════════════════════════════════════

def _get_system_root():
    """获取系统根目录（跨平台）"""
    if _SYSTEM == "Windows":
        return os.environ.get("SystemRoot", "C:\\Windows")
    return "/"

def _get_program_files():
    """获取 Program Files 目录（Windows，防硬编码）"""
    if _SYSTEM != "Windows":
        return []
    dirs = []
    pf = os.environ.get("ProgramFiles", "C:\\Program Files")
    if pf:
        dirs.append(pf)
    pf86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    if pf86 and pf86 != pf:
        dirs.append(pf86)
    return dirs

def _safe_name(name):
    """将名称转为安全的传感器名"""
    s = name.lower().replace(" ", "_").replace("(", "").replace(")", "")
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s

def _which(name):
    """检查可执行文件是否在 PATH 中"""
    try:
        r = subprocess.run(["where", name] if _SYSTEM == "Windows" else ["which", name],
                          capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
#  跨平台驱动目录构建
# ═══════════════════════════════════════════════════════════════

def _build_windows_driver_dirs(system_root):
    """Windows 驱动目录（通过环境变量）"""
    sr = system_root  # %SystemRoot%
    return [
        os.path.join(sr, "System32", "drivers"),
        os.path.join(sr, "INF"),
        os.path.join(sr, "System32", "DriverStore", "FileRepository"),
        os.path.join(sr, "System32", "DriverStore", "Temp"),
        os.path.join(sr, "System32", "spool", "drivers"),
        os.path.join(sr, "System32", "spool", "prtprocs"),
        os.path.join(sr, "System32", "CatRoot",
                      "{F750E6C3-38EE-11D1-85E5-00C04FC295EE}"),
        os.path.join(sr, "System32", "firmware"),
    ]

def _build_linux_driver_dirs():
    """Linux 驱动/固件目录"""
    version = platform.uname().release
    return [
        os.path.join("/lib", "modules", version),
        os.path.join("/lib", "modules", version, "kernel"),
        "/lib/firmware",
        "/etc/modprobe.d",
        "/etc/udev/rules.d",
        "/opt",
    ]

def _build_macos_driver_dirs():
    """macOS 驱动/固件目录"""
    return [
        "/System/Library/Extensions",
        "/Library/Extensions",
        "/Library/Apple/System/Library/Extensions",
        "/usr/libexec/firmware",
        "/Applications",
    ]


# ═══════════════════════════════════════════════════════════════
#  跨平台厂商软件检测
# ═══════════════════════════════════════════════════════════════

def _build_windows_vendor_software(program_dirs):
    """Windows 厂商软件路径（通过 %ProgramFiles% 动态构建）"""
    pf = program_dirs[0] if program_dirs else "C:\\Program Files"
    pf86 = program_dirs[1] if len(program_dirs) > 1 else "C:\\Program Files (x86)"

    return [
        # GPU / 显示
        (os.path.join(pf, "NVIDIA Corporation"),              "NVIDIA",          "gpu_driver"),
        (os.path.join(pf, "NVIDIA GPU Computing Toolkit"),    "NVIDIA CUDA",     "gpu_compute"),
        (os.path.join(pf, "AMD"),                             "AMD",             "gpu_driver"),
        (os.path.join(pf, "AMD APP SDK"),                     "AMD APP SDK",     "gpu_compute"),

        # 芯片组
        (os.path.join(pf, "Intel"),                           "Intel",           "chipset"),
        (os.path.join(pf86, "Intel"),                         "Intel (x86)",     "chipset"),

        # 音频
        (os.path.join(pf86, "Realtek"),                       "Realtek",         "audio"),
        (os.path.join(pf, "Realtek"),                         "Realtek",         "audio"),

        # 网络
        (os.path.join(pf, "Intel", "Intel(R) WiFi"),          "Intel WiFi",      "network"),
        (os.path.join(pf, "Intel", "Intel(R) Ethernet"),      "Intel Ethernet",  "network"),

        # 主板厂商配套
        (os.path.join(pf86, "ASUS"),                          "ASUS",            "motherboard_utility"),
        (os.path.join(pf, "ASUS"),                            "ASUS",            "motherboard_utility"),
        (os.path.join(pf86, "ASRock"),                        "ASRock",          "motherboard_utility"),
        (os.path.join(pf86, "GIGABYTE"),                      "GIGABYTE",        "motherboard_utility"),
        (os.path.join(pf86, "MSI"),                           "MSI",             "motherboard_utility"),
        (os.path.join(pf, "MSI"),                             "MSI",             "motherboard_utility"),

        # 笔记本 OEM
        (os.path.join(pf, "Dell"),                            "Dell",            "oem_utility"),
        (os.path.join(pf86, "Dell"),                          "Dell",            "oem_utility"),
        (os.path.join(pf, "HP"),                              "HP",              "oem_utility"),
        (os.path.join(pf86, "HP"),                            "HP",              "oem_utility"),
        (os.path.join(pf86, "Lenovo"),                        "Lenovo",          "oem_utility"),
        (os.path.join(pf, "Lenovo"),                          "Lenovo",          "oem_utility"),

        # 外设
        (os.path.join(pf, "Logitech"),                        "Logitech",        "peripheral"),
        (os.path.join(pf, "Logitech Gaming Software"),        "Logitech Gaming", "peripheral"),
        (os.path.join(pf86, "Logitech"),                      "Logitech",        "peripheral"),
        (os.path.join(pf86, "Razer"),                         "Razer",           "peripheral"),
        (os.path.join(pf, "Razer"),                           "Razer",           "peripheral"),
        (os.path.join(pf86, "Corsair"),                       "Corsair",         "peripheral"),
        (os.path.join(pf, "Corsair"),                         "Corsair",         "peripheral"),
        (os.path.join(pf, "SteelSeries"),                     "SteelSeries",     "peripheral"),

        # 硬件监测 / 诊断
        (os.path.join(pf, "HWiNFO"),                          "HWiNFO",          "monitoring"),
        (os.path.join(pf, "HWiNFO64"),                        "HWiNFO64",        "monitoring"),
        (os.path.join(pf, "LibreHardwareMonitor"),            "LibreHardwareMonitor", "monitoring"),
        (os.path.join(pf, "CPU-Z"),                           "CPU-Z",           "diagnostic"),
        (os.path.join(pf, "GPU-Z"),                           "GPU-Z",           "diagnostic"),
        (os.path.join(pf, "AIDA64"),                          "AIDA64",          "diagnostic"),
        (os.path.join(pf, "CrystalDiskInfo"),                 "CrystalDiskInfo", "diagnostic"),

        # 超频 / 调校
        (os.path.join(pf, "Intel", "Intel(R) Extreme Tuning Utility"), "Intel XTU", "overclocking"),
        (os.path.join(pf86, "OCCT"),                          "OCCT",            "stability_test"),

        # 存储
        (os.path.join(pf86, "Samsung"),                       "Samsung",         "storage"),
        (os.path.join(pf, "Samsung"),                         "Samsung",         "storage"),
    ]

def _build_linux_vendor_software():
    """Linux 厂商软件检测（通过命令和路径）"""
    entries = []

    # GPU 厂商
    if _which("nvidia-smi"):
        entries.append(("/usr/bin/nvidia-smi", "NVIDIA Driver", "gpu_driver"))
    if os.path.isdir("/opt/amdgpu"):
        entries.append(("/opt/amdgpu", "AMD GPU", "gpu_driver"))

    # Intel
    if os.path.isdir("/opt/intel"):
        entries.append(("/opt/intel", "Intel", "chipset"))

    # 监测工具
    for cmd, name, typ in [
        ("nvtop", "nvtop", "monitoring"),
        ("htop", "htop", "monitoring"),
        ("btop", "btop", "monitoring"),
        ("sensors", "lm-sensors", "monitoring"),
        ("smartctl", "smartmontools", "diagnostic"),
    ]:
        if _which(cmd):
            entries.append((f"/usr/bin/{cmd}", name, typ))

    return entries

def _build_macos_vendor_software():
    """macOS 厂商软件检测"""
    entries = []

    # GPU 厂商
    nvidia_prefix = "/Library/Extensions/NVIDIA"
    amd_prefix = "/System/Library/Extensions/AMD"
    for prefix, name, typ in [
        ("/Library/Extensions/NVIDIA", "NVIDIA Driver", "gpu_driver"),
        ("/Applications/NVIDIA", "NVIDIA", "gpu_driver"),
        ("/System/Library/Extensions/AMD", "AMD", "gpu_driver"),
        ("/Applications/AMD", "AMD", "gpu_driver"),
        ("/Applications/Intel Power Gadget", "Intel Power Gadget", "monitoring"),
    ]:
        if os.path.isdir(prefix) or os.path.isfile(prefix):
            entries.append((prefix, name, typ))

    # 监测工具
    apps_dir = "/Applications"
    for app, name, typ in [
        ("Stats.app", "Stats", "monitoring"),
        ("iStat Menus.app", "iStat Menus", "monitoring"),
        ("MonitorControl.app", "MonitorControl", "monitoring"),
    ]:
        path = os.path.join(apps_dir, app)
        if os.path.isdir(path):
            entries.append((path, name, typ))

    return entries


# ═══════════════════════════════════════════════════════════════
#  跨平台驱动文件分类
# ═══════════════════════════════════════════════════════════════

def _build_driver_dirs():
    """自动检测当前平台的驱动目录"""
    if _SYSTEM == "Windows":
        sr = _get_system_root()
        return _build_windows_driver_dirs(sr)
    elif _SYSTEM == "Linux":
        return _build_linux_driver_dirs()
    elif _SYSTEM == "Darwin":
        return _build_macos_driver_dirs()
    return []

def _build_vendor_software():
    """自动检测当前平台的厂商软件路径"""
    if _SYSTEM == "Windows":
        return _build_windows_vendor_software(_get_program_files())
    elif _SYSTEM == "Linux":
        return _build_linux_vendor_software()
    elif _SYSTEM == "Darwin":
        return _build_macos_vendor_software()
    return []


# ═══════════════════════════════════════════════════════════════
#  驱动类 → 硬件类型映射
# ═══════════════════════════════════════════════════════════════

_DRIVER_CLASS_MAP = {
    "Display": "gpu", "Video": "gpu", "MEDIA": "gpu",
    "H264Enc": "gpu", "HEVCEnc": "gpu",
    "Net": "network", "NetClient": "network", "NetService": "network",
    "NetTrans": "network", "Wlan": "network",
    "Audio": "audio", "Media": "audio", "AudioEndpoint": "audio",
    "USB": "usb", "USBDevice": "usb",
    "SCSIAdapter": "storage", "HDC": "storage", "DiskDrive": "storage",
    "CDROM": "storage", "NVMe": "storage",
    "Bluetooth": "bluetooth",
    "HIDClass": "input", "Keyboard": "input", "Mouse": "input", "Pointer": "input",
    "System": "system", "ACPI": "system", "PCI": "system",
    "Chipset": "system", "Bus": "system",
    "Printer": "printer", "PrintQueue": "printer",
    "Volume": "volume", "VolumeSnapshot": "volume",
    "Battery": "battery", "Power": "battery",
    "Sensor": "sensor", "Biometric": "biometric",
    "SmartCard": "smartcard", "Security": "security",
    "SoftwareComponent": "software", "Extension": "extension",
    "Image": "camera", "Camera": "camera",
    "Monitor": "monitor",
    "Firmware": "firmware",
    "Unclassified": "other",
}

_FILE_PATTERN_HW = [
    (r'nvlddmkm|nvidia|nvdisp|nvgpu|nv_', "gpu"),
    (r'amdkmdag|amdkmpfd|ati|radeon|amdgp|amdgpu|amdk', "gpu"),
    (r'igfx|igdlh|intel.*graphics|iigd_dc|igd|i915', "gpu"),
    (r'e1d|e2f|e1g|e2w|netrt|netw|wlan|wifi', "network"),
    (r'ath(ler)?|rtl.*(nic|eth|net)|e1ce|e2x|r816|r812', "network"),
    (r'tcpip|ndis|netio|afd|tdx|bowser|mrx', "network"),
    (r'nwifi|vwifi|wlan|wdiwifi|netwlv|netwns', "network"),
    (r'hdaud|hdx|usbaud|rtkhd|intcaz|acs|portcls|snd|alsa', "audio"),
    (r'wdmaud|drmk|sndblst|cmudax|aacs|hda', "audio"),
    (r'stor.*|nvme|ahci|disk|partmgr|volsnap|fve|dxgkrnl', "storage"),
    (r'dump_|clipsp|spaceport|storahci|nvme|sd_mod', "storage"),
    (r'usb|xhci|ehci|ohci|uhci|usbhub|usbccgp|winusb|usb_storage', "usb"),
    (r'bth|bt|bluetooth|rfcomm|btusb|bluetooth', "bluetooth"),
    (r'kbd|hid|mou|i8042|ps2|sermou|serkbd|mouclass|kbdclass|input', "input"),
    (r'pci|acpi|wmi|intelppm|processr|msisadrv|pciide', "system"),
    (r'motherboard|chipset|amdiomgr|intelpep|windows.*acpi', "system"),
    (r'usbprint|msprint|winspool|localspl|oem.*\.inf', "printer"),
    (r'cmbatt|battery|composite.*battery|intelpmc', "battery"),
    (r'kscamera|usbvideo|uvcvideo|camera|video.*capture|uvc', "camera"),
    (r'sensor|accelerometer|gyro|als|sht|i2c.*hid', "sensor"),
    (r'firmware|uefi|bios|fw\.', "firmware"),
    (r'monitor|display.*color|edid|drm', "monitor"),
    (r'smartcard|scard|winscard|p9x', "smartcard"),
    (r'bitlocker|fvevol|cnghwassist|trusted.*platform|tpm', "security"),
]

_KEY_DRIVERS = [
    "nvlddmkm.sys", "nvidia.ko",         # NVIDIA GPU
    "amdgpu.sys", "amdgpu.ko",           # AMD GPU
    "i915.ko", "igfx.sys",               # Intel GPU
    "r8169.ko", "r8125.ko",              # Realtek 网卡
    "snd-hda-intel.ko",                  # Intel HDA 音频
    "tcpip.sys",                         # 网络协议栈
    "nvme.ko", "nvme.sys",               # NVMe 存储
    "usb-storage.ko", "usbhub.sys",      # USB
    "btusb.ko", "bthport.sys",           # 蓝牙
]

_RE_INF_CLASS = re.compile(r'^Class\s*=\s*"?(.+?)"?\s*$', re.MULTILINE)


class HardwareFileSensor:
    """硬件文件系统传感器 — 监测硬件相关的软件文件（跨平台）"""

    CAPABILITIES = {
        "name": "hwfile",
        "description": "硬件文件（软件骨骼）— 驱动、固件、厂商工具",
        "category": Category.FILE,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": [],
    }

    def __init__(self):
        self._category = Category.FILE
        self._baseline = {}
        self._baseline_time = 0
        self._baseline_hash = ""
        self._baseline_driver_count = 0
        self._prev_snapshot = {}
        self._initialized = False

        # 跨平台路径（运行时自动检测）
        self._driver_dirs = [d for d in _build_driver_dirs() if os.path.isdir(d)]
        self._vendor_entries = _build_vendor_software()
        self._system = _SYSTEM

    # ═══════════════════════════════════════════════════════════
    #  主入口
    # ═══════════════════════════════════════════════════════════

    def collect(self):
        if not self._initialized:
            return self._first_scan()
        return self._delta_scan()

    # ═══════════════════════════════════════════════════════════
    #  首次扫描（建立基线）
    # ═══════════════════════════════════════════════════════════

    def _first_scan(self):
        readings = []
        self._initialized = True
        self._baseline_time = time.time()

        # 驱动文件扫描
        driver_files = self._scan_driver_dirs()
        categorized = self._categorize_files_bulk(driver_files)
        self._baseline = categorized
        total_count = sum(v["count"] for v in categorized.values())
        self._baseline_driver_count = total_count

        # 厂商软件检测
        vendor_status = self._scan_vendor_software()

        # 关键驱动版本
        key_versions = self._check_key_driver_versions()

        readings.append(normal(
            "hwfile_driver_total", total_count, "个",
            f"硬件驱动文件总数（共 {len(categorized)} 类）", self._category,
            {"by_category": {k: v["count"] for k, v in sorted(categorized.items())},
             "platform": self._system, "source": "baseline_scan"}
        ))

        for hw_type, info in sorted(categorized.items()):
            readings.append(normal(
                f"hwfile_drivers_{hw_type}", info["count"], "个",
                f"{hw_type} 类驱动文件数", self._category,
                {"vendors": list(info["vendors"]) if info["vendors"] else None,
                 "files": info["files"][:20]}
            ))

        for vendor, status in sorted(vendor_status.items()):
            readings.append(normal(
                f"hwfile_vendor_{_safe_name(vendor)}",
                "已安装" if status["installed"] else "未安装", "",
                f"{status['type']}: {vendor}", self._category,
                {"software_type": status["type"]}
            ))

        for name, ver in sorted(key_versions.items()):
            if ver:
                readings.append(normal(
                    f"hwfile_driver_ver_{name.replace('.sys','').replace('.ko','')}",
                    ver, "", f"{name} 版本", self._category,
                    {"driver_file": name}
                ))

        baseline_data = {k: v["count"] for k, v in categorized.items()}
        baseline_data["_platform"] = self._system
        self._baseline_hash = hashlib.md5(str(baseline_data).encode()).hexdigest()[:8]
        readings.append(normal(
            "hwfile_baseline_hash", self._baseline_hash, "",
            "驱动基线哈希", self._category
        ))

        self._prev_snapshot = self._take_snapshot()
        return readings

    # ═══════════════════════════════════════════════════════════
    #  增量扫描（检测变化）
    # ═══════════════════════════════════════════════════════════

    def _delta_scan(self):
        readings = []
        current_snapshot = self._take_snapshot()

        added, removed, modified = [], [], []
        for d in self._prev_snapshot:
            old = self._prev_snapshot.get(d, {})
            new = current_snapshot.get(d, {})
            old_names, new_names = set(old.keys()), set(new.keys())
            for name in new_names - old_names:
                added.append((d, name))
            for name in old_names - new_names:
                removed.append((d, name))
            for name in old_names & new_names:
                if old[name] != new[name]:
                    modified.append((d, name))

        driver_files = self._scan_driver_dirs()
        categorized = self._categorize_files_bulk(driver_files)
        total_count = sum(v["count"] for v in categorized.values())

        readings.append(normal(
            "hwfile_driver_total", total_count, "个",
            "硬件驱动文件总数", self._category,
            {"by_category": {k: v["count"] for k, v in sorted(categorized.items())}}
        ))

        for d, name in added[:30]:
            readings.append(warning(
                f"hwfile_added_{self._categorize_file(os.path.join(d, name))}", name, "",
                f"新增驱动文件: {name}", self._category,
                {"path": os.path.join(d, name)}
            ))
        for d, name in removed[:20]:
            readings.append(critical(
                f"hwfile_removed_{self._categorize_file(os.path.join(d, name))}", name, "",
                f"驱动文件移除: {name}", self._category,
                {"path": os.path.join(d, name)}
            ))
        for d, name in modified[:20]:
            readings.append(warning(
                f"hwfile_modified_{self._categorize_file(os.path.join(d, name))}", name, "",
                f"驱动文件变更: {name}", self._category,
                {"path": os.path.join(d, name)}
            ))

        total_changes = len(added) + len(removed) + len(modified)
        if total_changes > 0:
            readings.append(warning(
                "hwfile_changes_total", total_changes, "项",
                f"驱动变化: +{len(added)} / -{len(removed)} / ~{len(modified)}",
                self._category,
                {"added": len(added), "removed": len(removed), "modified": len(modified)}
            ))
            baseline_data = {k: v["count"] for k, v in categorized.items()}
            new_hash = hashlib.md5(str(baseline_data).encode()).hexdigest()[:8]
            if new_hash != self._baseline_hash:
                readings.append(warning(
                    "hwfile_baseline_changed", new_hash, "",
                    f"驱动基线已变化: {self._baseline_hash} -> {new_hash}",
                    self._category
                ))
                self._baseline_hash = new_hash

        self._prev_snapshot = current_snapshot
        return readings

    # ═══════════════════════════════════════════════════════════
    #  快照
    # ═══════════════════════════════════════════════════════════

    def _take_snapshot(self):
        snapshot = {}
        for d in self._driver_dirs:
            snapshot[d] = {}
            if not os.path.isdir(d):
                continue
            try:
                for fname in os.listdir(d):
                    try:
                        st = os.stat(os.path.join(d, fname))
                        snapshot[d][fname] = (st.st_mtime, st.st_size)
                    except OSError:
                        continue
            except OSError:
                continue
        return snapshot

    # ═══════════════════════════════════════════════════════════
    #  扫描方法
    # ═══════════════════════════════════════════════════════════

    def _scan_driver_dirs(self):
        files = []
        for d in self._driver_dirs:
            if not os.path.isdir(d):
                continue
            try:
                for fname in os.listdir(d):
                    fpath = os.path.join(d, fname)
                    if os.path.isfile(fpath):
                        files.append(fpath)
            except PermissionError:
                continue
        return files

    def _scan_vendor_software(self):
        """检测厂商软件安装状态（跨平台）"""
        result = {}
        seen = set()
        for path, vendor, sw_type in self._vendor_entries:
            key = f"{vendor}|{sw_type}"
            if key in seen:
                continue
            seen.add(key)
            installed = os.path.isdir(path) and any(
                os.path.isfile(os.path.join(path, f))
                for f in os.listdir(path)[:50]
            ) if os.path.isdir(path) else os.path.isfile(path)
            if installed:
                result[vendor] = {"installed": True, "type": sw_type}
            elif vendor not in result:
                result[vendor] = {"installed": False, "type": sw_type}
        return result

    # ═══════════════════════════════════════════════════════════
    #  驱动版本查询
    # ═══════════════════════════════════════════════════════════

    def _check_key_driver_versions(self):
        """跨平台检测关键驱动版本"""
        versions = {}

        if self._system == "Windows":
            drv_dir = os.path.join(_get_system_root(), "System32", "drivers")
            if os.path.isdir(drv_dir):
                for name in _KEY_DRIVERS:
                    if not name.endswith(".sys"):
                        continue
                    fpath = os.path.join(drv_dir, name)
                    if os.path.isfile(fpath):
                        ver = self._get_file_version_windows(fpath)
                        if ver:
                            versions[name] = ver

        elif self._system == "Linux":
            # 通过 modinfo 获取内核模块版本
            for name in _KEY_DRIVERS:
                if not name.endswith(".ko"):
                    continue
                module_name = name.replace(".ko", "")
                try:
                    r = subprocess.run(
                        ["modinfo", "-F", "version", module_name],
                        capture_output=True, text=True, timeout=3
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        versions[name] = r.stdout.strip()
                except Exception:
                    continue

        elif self._system == "Darwin":
            # 通过 kextstat 获取已加载的 kext 版本
            try:
                r = subprocess.run(
                    ["kextstat", "-l"], capture_output=True, text=True, timeout=5
                )
                for line in r.stdout.split("\n")[1:]:
                    parts = line.strip().split()
                    if len(parts) >= 6:
                        # com.nvidia.web.GeForce 之类的标识
                        bundle = parts[6]
                        if "nvidia" in bundle.lower():
                            if len(parts) >= 7:
                                versions["nvidia.kext"] = parts[5]
                        if "amd" in bundle.lower():
                            if len(parts) >= 7:
                                versions["amd.kext"] = parts[5]
            except Exception:
                pass

        return versions

    def _get_file_version_windows(self, fpath):
        """Windows: 通过 PowerShell 读取文件版本"""
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 f'(Get-Item "{fpath}").VersionInfo.FileVersion'],
                capture_output=True, text=True, timeout=5
            )
            ver = r.stdout.strip()
            return ver if ver else None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════
    #  文件分类
    # ═══════════════════════════════════════════════════════════

    def _categorize_files_bulk(self, files):
        categorized = {}
        for fpath in files:
            hw_type = self._categorize_file(fpath)
            if hw_type not in categorized:
                categorized[hw_type] = {"files": [], "count": 0, "vendors": set()}
            categorized[hw_type]["files"].append(os.path.basename(fpath))
            categorized[hw_type]["count"] += 1
            vendor = self._detect_vendor(fpath)
            if vendor:
                categorized[hw_type]["vendors"].add(vendor)
        return categorized

    def _categorize_file(self, fpath):
        """
        将文件归类到硬件类型。
        优先级: INF Class (Windows) -> 文件名模式 -> 目录启发式
        """
        fname = os.path.basename(fpath).lower()
        ext = os.path.splitext(fname)[1].lower()

        # Windows INF 解析
        if self._system == "Windows" and ext == ".inf":
            hw_type = self._categorize_inf(fpath)
            if hw_type:
                return hw_type

        # 文件名模式
        for pattern, hw_type in _FILE_PATTERN_HW:
            if re.search(pattern, fname, re.IGNORECASE):
                return hw_type

        # 目录启发式
        d = os.path.dirname(fpath).lower()
        if self._system == "Windows":
            if "driverstore" in d:
                return self._categorize_driverstore(fname)
            if "spool" in d:
                return "printer"
            if "catroot" in d:
                return "security"
            if "firmware" in d:
                return "firmware"
        elif self._system == "Linux":
            if "firmware" in d:
                return "firmware"
            if "modules" in d:
                # kernel 模块通常在 drivers/ 子目录下
                if "/drivers/" in d:
                    sub_dir = d.split("/drivers/")[-1].split("/")[0]
                    dir_map = {
                        "gpu": "gpu", "drm": "gpu",
                        "net": "network", "wireless": "network",
                        "audio": "audio", "sound": "audio",
                        "usb": "usb", "input": "input",
                        "ata": "storage", "nvme": "storage", "scsi": "storage",
                        "bluetooth": "bluetooth",
                        "i2c": "sensor", "hid": "input",
                        "video": "camera", "media": "camera",
                        "pci": "system", "acpi": "system",
                    }
                    return dir_map.get(sub_dir, "system")
            if "udev" in d:
                return "system"

        return "system"

    def _categorize_inf(self, fpath):
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(4096)
        except Exception:
            return None
        m = _RE_INF_CLASS.search(content)
        return _DRIVER_CLASS_MAP.get(m.group(1).strip()) if m else None

    def _categorize_driverstore(self, fname):
        for pattern, hw_type in _FILE_PATTERN_HW:
            if re.search(pattern, fname, re.IGNORECASE):
                return hw_type
        prefix = fname.split("_")[0].lower()
        vendor_map = {
            "nvidia": "gpu", "amd": "gpu", "ati": "gpu",
            "intel": "system", "intelgfx": "gpu",
            "realtek": "audio",
            "broadcom": "network", "qualcomm": "network", "marvell": "network",
            "synaptics": "input", "wacom": "input",
            "dell": "system", "lenovo": "system", "hp": "system",
            "toshiba": "storage", "samsung": "storage", "micron": "storage",
            "sandisk": "storage", "seagate": "storage", "wdc": "storage",
            "logicool": "input", "microsoft": "input",
        }
        return vendor_map.get(prefix, "system")

    def _detect_vendor(self, fpath):
        lowpath = fpath.lower()
        if "nvidia" in lowpath:
            return "NVIDIA"
        if "amd" in lowpath or "ati" in lowpath or "amdgpu" in lowpath:
            return "AMD"
        if "intel" in lowpath:
            return "Intel"
        if "realtek" in lowpath:
            return "Realtek"
        if "broadcom" in lowpath or "bcm" in lowpath:
            return "Broadcom"
        if "qualcomm" in lowpath or "qca" in lowpath:
            return "Qualcomm"
        if "synaptics" in lowpath:
            return "Synaptics"
        if "wacom" in lowpath:
            return "Wacom"
        return None

    # ═══════════════════════════════════════════════════════════
    #  查询接口
    # ═══════════════════════════════════════════════════════════

    @property
    def driver_summary(self):
        if not self._baseline:
            return "尚未扫描"
        parts = [
            f"平台: {self._system}",
            f"驱动文件基线: {self._baseline_driver_count} 个文件",
        ]
        for hw_type, info in sorted(self._baseline.items()):
            parts.append(f"  {hw_type}: {info['count']} 个驱动")
            if info["vendors"]:
                parts.append(f"    厂商: {', '.join(sorted(info['vendors']))}")
        return "\n".join(parts)

    @property
    def is_initialized(self):
        return self._initialized
