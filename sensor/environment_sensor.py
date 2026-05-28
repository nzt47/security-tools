"""
软件环境传感器 — 我的"生命维持系统"监测器

监测软件运行环境的健康状况：
  环境  — Python/OS/平台，我的"生存环境"
  依赖  — 关键库和包的可用性，我的"营养供给"
  API   — 系统 API 的可用状态，我的"神经通路"

每一条依赖都是一个生命线——断了一条，我就少一种感知能力。
"""
import os
import re
import sys
import time
import site
import logging
import platform
import importlib
import subprocess
from datetime import datetime, timezone
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

_SYSTEM = platform.system()

# ═══════════════════════════════════════════════════════════════
#  关键依赖模块清单
# ═══════════════════════════════════════════════════════════════

_KEY_MODULES = [
    # 系统
    ("psutil",       "psutil",       "系统与硬件信息",      True),
    ("platform",     "platform",     "平台信息",            True),

    # 硬件访问 (Windows)
    ("wmi",          "wmi",          "WMI 硬件接口",        _SYSTEM == "Windows"),
    ("comtypes",     "comtypes",     "COM 组件接口",        _SYSTEM == "Windows"),
    ("pythoncom",    "pythoncom",    "Python COM",          _SYSTEM == "Windows"),

    # GPU
    ("pynvml",       "pynvml",       "NVIDIA GPU 管理库",   False),
    ("GPUtil",       "GPUtil",       "GPU 工具库",          False),

    # 文件监控
    ("watchdog",     "watchdog",     "文件系统监听",        True),

    # Windows 特定
    ("win32api",     "win32api",     "Windows API",         _SYSTEM == "Windows"),
    ("win32file",    "win32file",    "Windows 文件 API",    _SYSTEM == "Windows"),
    ("winreg",       "winreg",       "Windows 注册表",      _SYSTEM == "Windows"),

    # Linux 特定
    ("pyudev",       "pyudev",       "udev 设备事件",       _SYSTEM == "Linux"),

    # 网络 / Web
    ("requests",     "requests",     "HTTP 请求库",         True),
    ("flask",        "flask",        "Web 框架",            False),

    # 标准库
    ("json",         "json",         "JSON 处理",           True),
    ("threading",    "threading",    "多线程",              True),
    ("hashlib",      "hashlib",      "哈希计算",            True),
    ("subprocess",   "subprocess",   "子进程管理",          True),
]

# ═══════════════════════════════════════════════════════════════
#  Windows 关键系统服务
# ═══════════════════════════════════════════════════════════════

_WINDOWS_SERVICES = [
    ("Audiosrv",        "Windows Audio",            "audio"),
    ("AudioEndpointBuilder", "音频终结点生成",      "audio"),
    ("WlanSvc",         "WLAN 自动配置",            "network"),
    ("LanmanServer",    "服务器",                   "network"),
    ("LanmanWorkstation", "工作站",                 "network"),
    ("Dhcp",            "DHCP 客户端",              "network"),
    ("Dnscache",        "DNS 客户端",               "network"),
    ("NvContainerLocalSystem", "NVIDIA 本地容器",   "gpu"),
    ("winmgmt",         "Windows 管理规范",          "system"),
    ("PlugPlay",        "即插即用",                 "system"),
    ("Power",           "电源管理",                 "system"),
    ("SysMain",         "SysMain",                  "system"),
    ("UALSVC",          "用户接入日志",              "system"),
]


class EnvironmentSensor:
    """软件环境传感器 — 监测我的生命维持系统"""

    CAPABILITIES = {
        "name": "env",
        "description": "环境（生命维持）— Python、依赖、系统服务、API",
        "category": Category.ENVIRONMENT,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": [],
    }

    def __init__(self):
        self._category = Category.ENVIRONMENT
        self._boot_time = None
        self._pip_cache = None
        self._pip_cache_time = 0
        self._system = _SYSTEM

    def collect(self):
        """
        全面采集软件环境状态。

        返回 SensorReading 列表，包含：
          - 运行时环境（Python/OS）
          - 依赖可用性
          - API 状态
          - 服务状态（Windows）
        """
        readings = []

        # 环境
        readings.extend(self._collect_runtime())
        readings.extend(self._collect_environment())

        # 依赖
        readings.extend(self._collect_modules())
        readings.extend(self._collect_pip_packages())

        # API 服务
        readings.extend(self._collect_api_availability())
        readings.extend(self._collect_services())

        # 系统运行时
        readings.extend(self._collect_uptime())

        return readings

    # ═══════════════════════════════════════════════════════════
    #  运行时环境
    # ═══════════════════════════════════════════════════════════

    def _collect_runtime(self):
        """采集 Python / OS 运行时信息"""
        readings = []

        # Python 版本
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        readings.append(normal(
            "env_python_version", py_ver, "",
            f"Python {py_ver} ({platform.python_implementation()})",
            self._category
        ))

        # Python 路径
        readings.append(normal(
            "env_python_path", sys.executable, "",
            "Python 解释器路径", self._category
        ))

        # Python 架构
        arch_map = {"64bit": "x64", "32bit": "x86"}
        py_arch = arch_map.get(platform.architecture()[0], platform.architecture()[0])
        readings.append(normal(
            "env_python_arch", py_arch, "",
            f"Python 架构: {py_arch}", self._category
        ))

        # OS 信息
        if self._system == "Windows":
            os_ver = platform.version()
            os_release = platform.release()
            readings.append(normal(
                "env_os_version",
                f"Windows {os_release} (build {os_ver})", "",
                f"Windows {os_release} 版本 {os_ver}", self._category
            ))
        elif self._system == "Linux":
            try:
                import distro
                os_id = distro.name(pretty=True)
            except ImportError:
                os_id = platform.platform()
            readings.append(normal(
                "env_os_version", os_id, "",
                f"Linux 发行版: {os_id}", self._category
            ))
            # 内核版本
            readings.append(normal(
                "env_kernel_version", platform.release(), "",
                f"内核: {platform.release()}", self._category
            ))
        elif self._system == "Darwin":
            readings.append(normal(
                "env_os_version", platform.mac_ver()[0], "",
                f"macOS: {platform.mac_ver()[0]}", self._category
            ))

        # 机器名
        readings.append(normal(
            "env_hostname", platform.node(), "",
            "主机名", self._category
        ))

        return readings

    def _collect_environment(self):
        """采集关键环境变量"""
        readings = []

        # PATH 长度（字符数，过长可能导致问题）
        path_val = os.environ.get("PATH", "")
        readings.append(normal(
            "env_path_length", len(path_val), "字符",
            "PATH 环境变量长度", self._category
        ))

        # HOME / USERPROFILE
        home = os.path.expanduser("~")
        readings.append(normal(
            "env_home_dir", home, "",
            "用户主目录", self._category
        ))

        # TEMP / TMP
        temp = os.environ.get("TEMP", os.environ.get("TMP", "/tmp"))
        readings.append(normal(
            "env_temp_dir", temp, "",
            "临时目录", self._category
        ))

        # 系统根（Windows）或根目录
        if self._system == "Windows":
            sr = os.environ.get("SystemRoot", "C:\\Windows")
            readings.append(normal(
                "env_system_root", sr, "",
                "系统根目录", self._category
            ))
            # 用户名
            user = os.environ.get("USERNAME", "")
            if user:
                readings.append(normal(
                    "env_username", user, "",
                    "当前用户", self._category
                ))
            # 处理器数量（环境变量）
            proc_count = os.environ.get("NUMBER_OF_PROCESSORS", "")
            if proc_count:
                readings.append(normal(
                    "env_num_processors", int(proc_count), "核",
                    "逻辑处理器数", self._category
                ))

        return readings

    # ═══════════════════════════════════════════════════════════
    #  依赖模块检测
    # ═══════════════════════════════════════════════════════════

    def _collect_modules(self):
        """检测关键 Python 模块的可用性"""
        readings = []

        available = 0
        unavailable = 0

        for name, module, desc, required in _KEY_MODULES:
            try:
                importlib.import_module(module)
                available += 1
                readings.append(normal(
                    f"env_module_{name}", "可用", "",
                    f"{desc} ({name})", self._category,
                    {"module": module, "available": True, "required": required}
                ))
            except ImportError:
                unavailable += 1
                sev = Severity.CRITICAL if required else Severity.NORMAL
                readings.append(SensorReading(
                    f"env_module_{name}", "不可用", "",
                    f"{desc} ({name}) - 缺少依赖", self._category, sev,
                    {"module": module, "available": False, "required": required}
                ))

        readings.append(normal(
            "env_modules_summary", f"{available}/{available + unavailable}", "",
            f"模块可用性: {available}/{available + unavailable}", self._category,
            {"available": available, "unavailable": unavailable}
        ))

        return readings

    def _collect_pip_packages(self):
        """采集 pip 包概况"""
        readings = []
        packages = self._get_pip_packages()

        # 总数
        readings.append(normal(
            "env_pip_total", len(packages), "个",
            "已安装 Python 包总数", self._category
        ))

        # 最近安装的 5 个包（按版本排序）
        recent = sorted(packages,
                        key=lambda x: x[2] if len(x) > 2 else "")[-5:]
        if recent:
            recent_str = "; ".join(f"{p[0]}=={p[1]}" for p in recent)
            readings.append(normal(
                "env_pip_recent", recent_str, "",
                "最近安装的包", self._category
            ))

        return readings

    def _get_pip_packages(self):
        """获取已安装 pip 包列表，缓存 60 秒"""
        now = time.time()
        if self._pip_cache and (now - self._pip_cache_time) < 60:
            return self._pip_cache

        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=freeze"],
                capture_output=True, text=True, timeout=15
            )
            # 可能没有 pip
            packages = []
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if line and "==" in line:
                    name, ver = line.split("==", 1)
                    packages.append((name, ver, ""))
            # 按名称排序
            packages.sort(key=lambda x: x[0].lower())
            self._pip_cache = packages
            self._pip_cache_time = now
            return packages
        except Exception:
            return []

    # ═══════════════════════════════════════════════════════════
    #  API 可用性
    # ═══════════════════════════════════════════════════════════

    def _collect_api_availability(self):
        """检测系统 API / 接口的可用性"""
        readings = []

        # WMI 可用性
        if self._system == "Windows":
            wmi_ok = self._check_wmi()
            readings.append(normal(
                "env_api_wmi", "可用" if wmi_ok else "不可用", "",
                "WMI 服务", self._category,
                {"available": wmi_ok}
            ))

            # PowerShell 可用性
            ps_ok = self._check_powershell()
            readings.append(normal(
                "env_api_powershell", "可用" if ps_ok else "不可用", "",
                "PowerShell", self._category,
                {"available": ps_ok}
            ))

            # COM
            com_ok = self._check_com()
            readings.append(normal(
                "env_api_com", "可用" if com_ok else "不可用", "",
                "COM 组件模型", self._category,
                {"available": com_ok}
            ))

        # NVIDIA ML API
        nv_ok = self._check_nvidia_ml()
        readings.append(normal(
            "env_api_nvidia_ml", "可用" if nv_ok else "不可用", "",
            "NVIDIA 管理库 (NVML)", self._category,
            {"available": nv_ok}
        ))

        # watchdog
        wd_ok = self._check_import("watchdog")
        readings.append(normal(
            "env_api_watchdog", "可用" if wd_ok else "不可用", "",
            "Watchdog 文件监听", self._category,
            {"available": wd_ok}
        ))

        # ctypes (系统调用)
        readings.append(normal(
            "env_api_ctypes", "可用", "",
            "ctypes 系统调用接口", self._category
        ))

        return readings

    def _check_wmi(self):
        """检查 WMI 是否可用"""
        try:
            import subprocess
            r = subprocess.run(
                ["wmic", "os", "get", "name", "/format:csv"],
                capture_output=True, text=True, timeout=5
            )
            return r.returncode == 0 and "Name" in r.stdout
        except Exception:
            return False

    def _check_powershell(self):
        """检查 PowerShell 是否可用"""
        try:
            r = subprocess.run(
                ["powershell", "-Command", "$true"],
                capture_output=True, text=True, timeout=5
            )
            return r.returncode == 0 and r.stdout.strip() == "True"
        except Exception:
            return False

    def _check_com(self):
        """检查 COM 是否可初始化"""
        try:
            import pythoncom
            import ctypes
            # 静默测试，不留下 COM 状态污染
            pythoncom.CoInitialize()
            pythoncom.CoUninitialize()
            return True
        except Exception:
            return False

    def _check_nvidia_ml(self):
        """检查 NVIDIA ML API 是否可用"""
        try:
            import pynvml
            pynvml.nvmlInit()
            pynvml.nvmlShutdown()
            return True
        except Exception:
            return False

    @staticmethod
    def _check_import(module):
        """检查模块是否可导入"""
        try:
            importlib.import_module(module)
            return True
        except ImportError:
            return False

    # ═══════════════════════════════════════════════════════════
    #  系统服务
    # ═══════════════════════════════════════════════════════════

    def _collect_services(self):
        """采集关键系统服务的运行状态"""
        readings = []

        if self._system != "Windows":
            return readings

        for svc_name, display_name, hw_type in _WINDOWS_SERVICES:
            status = self._query_service_status(svc_name)
            if status is None:
                continue

            sev = Severity.NORMAL if status == "RUNNING" else Severity.CRITICAL
            readings.append(SensorReading(
                f"env_service_{svc_name}", status, "",
                f"{display_name} ({svc_name}) - {hw_type}",
                self._category, sev,
                {"service": svc_name, "expected_status": "Running",
                 "actual_status": status, "hardware_type": hw_type}
            ))

        return readings

    def _query_service_status(self, svc_name):
        """查询 Windows 服务状态"""
        try:
            r = subprocess.run(
                ["sc", "query", svc_name],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                m = re.search(r'STATE\s*:\s*\d+\s+(\w+)', r.stdout)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return None

    # ═══════════════════════════════════════════════════════════
    #  系统运行时
    # ═══════════════════════════════════════════════════════════

    def _collect_uptime(self):
        """采集系统运行时间"""
        readings = []
        try:
            import psutil
            boot = psutil.boot_time()
            now = time.time()
            uptime_secs = now - boot
            uptime_hours = uptime_secs / 3600

            # 可读格式
            days = int(uptime_hours // 24)
            hours = int(uptime_hours % 24)
            minutes = int((uptime_secs % 3600) // 60)
            if days > 0:
                uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
            else:
                uptime_str = f"{hours}小时 {minutes}分钟"

            readings.append(normal(
                "env_uptime", uptime_str, "",
                f"系统已运行: {uptime_str}", self._category,
                {"uptime_seconds": round(uptime_secs, 0),
                 "boot_time": datetime.fromtimestamp(boot).isoformat()}
            ))

            # 启动时间戳
            readings.append(normal(
                "env_boot_time",
                datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M:%S"), "",
                "系统启动时间", self._category
            ))
        except Exception:
            pass

        return readings
