"""
软件蓝图 — 我的"软件生态系统解剖图"

穷尽操作系统版本、运行时环境、安装软件、系统服务等，
标注检测方式。
"""
import logging
import platform
import os
import sys
from .sensor_reading import SensorReading, Severity, Category, normal

SYSTEM = platform.system()


class SoftwareBlueprint:
    """
    软件蓝图 — 软件生态系统解剖图

    枚举操作系统、运行时、安装软件、系统组件。
    """

    def __init__(self):
        self._category = Category.ENVIRONMENT

    def collect(self):
        readings = []
        for entry in self._build_blueprint():
            try:
                count = self._detect_count(entry)
                entry["detected_count"] = count
                readings.append(self._entry_to_reading(entry))
            except Exception as e:
                logging.debug(f"软件蓝图项 {entry['name']} 检测异常: {e}")
        return readings

    def _build_blueprint(self):
        blueprint = []

        # 操作系统
        blueprint.append({"name": "操作系统版本", "type": "os_version", "method": "software_detectable",
                          "sources": ["platform.version", "platform.platform"]})
        blueprint.append({"name": "操作系统位数", "type": "os_arch", "method": "software_detectable",
                          "sources": ["platform.machine", "os.environ PROCESSOR_ARCHITECTURE"]})
        blueprint.append({"name": "系统安装日期", "type": "os_install", "method": "software_detectable",
                          "sources": ["WMI Win32_OperatingSystem.InstallDate"]})
        blueprint.append({"name": "系统激活状态", "type": "os_activation", "method": "software_detectable",
                          "sources": ["WMI Win32_WindowsProductActivation / slmgr"]})
        blueprint.append({"name": "Windows 体验指数", "type": "os_win_exp", "method": "software_detectable",
                          "sources": ["WMI Win32_WinSAT"]})
        blueprint.append({"name": "系统更新/补丁等级", "type": "os_updates", "method": "software_detectable",
                          "sources": ["WMI Win32_QuickFixEngineering"]})
        blueprint.append({"name": "Windows 功能体验包", "type": "os_feature_pack", "method": "software_detectable",
                          "sources": ["注册表 EditionID / WMI"]})

        # 运行时
        blueprint.append({"name": "Python 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["sys.version"]})
        blueprint.append({"name": "Node.js 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["where node / node --version 检测"]})
        blueprint.append({"name": "Java 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["where java / java --version 检测"]})
        blueprint.append({"name": ".NET 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["注册表 / HKLM DotNetFramework"]})
        blueprint.append({"name": ".NET Core 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["注册表 / dotnet --list-runtimes"]})
        blueprint.append({"name": "PowerShell 版本", "type": "runtime", "method": "software_detectable",
                          "sources": ["注册表 PowerShellVersion / $PSVersionTable"]})
        blueprint.append({"name": "Go 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["where go / go version 检测"]})
        blueprint.append({"name": "Rust 运行时", "type": "runtime", "method": "software_detectable",
                          "sources": ["where rustc / rustc --version 检测"]})
        blueprint.append({"name": "Git 版本", "type": "runtime", "method": "software_detectable",
                          "sources": ["where git / git --version 检测"]})
        blueprint.append({"name": "Docker 环境", "type": "runtime", "method": "software_detectable",
                          "sources": ["where docker / docker info 检测"]})
        blueprint.append({"name": "WSL 子系统", "type": "runtime", "method": "software_detectable",
                          "sources": ["wsl --status / 注册表 Lxss"]})

        # 包管理器
        blueprint.append({"name": "pip 包管理器", "type": "package_manager", "method": "software_detectable",
                          "sources": ["pip --version"]})
        blueprint.append({"name": "npm 包管理器", "type": "package_manager", "method": "software_detectable",
                          "sources": ["where npm"]})
        blueprint.append({"name": "Chocolatey 包管理器", "type": "package_manager", "method": "software_detectable",
                          "sources": ["where choco"]})
        blueprint.append({"name": "Scoop 包管理器", "type": "package_manager", "method": "software_detectable",
                          "sources": ["where scoop"]})
        blueprint.append({"name": "winget 包管理器", "type": "package_manager", "method": "software_detectable",
                          "sources": ["where winget"]})

        # 安装软件统计
        blueprint.append({"name": "已安装应用程序数", "type": "installed_software", "method": "software_detectable",
                          "sources": ["WMI Win32_Product / 注册表 Uninstall"]})
        blueprint.append({"name": "Microsoft Store 应用", "type": "installed_software", "method": "software_detectable",
                          "sources": ["WMI Win32_AppxPackage / Get-AppxPackage"]})
        blueprint.append({"name": "开机自启程序数", "type": "startup_programs", "method": "software_detectable",
                          "sources": ["WMI Win32_StartupCommand / 注册表 Run"]})
        blueprint.append({"name": "系统服务数", "type": "system_services", "method": "software_detectable",
                          "sources": ["WMI Win32_Service"]})
        blueprint.append({"name": "驱动程序数", "type": "drivers", "method": "software_detectable",
                          "sources": ["WMI Win32_SystemDriver"]})
        blueprint.append({"name": "已注册 COM 组件", "type": "com_components", "method": "software_detectable",
                          "sources": ["注册表 CLSID 枚举"]})

        # 安全软件
        blueprint.append({"name": "Windows Defender", "type": "security_software", "method": "software_detectable",
                          "sources": ["WMI Win32_Product / MSFT_MpComputerStatus"]})
        blueprint.append({"name": "第三方杀毒软件", "type": "security_software", "method": "software_detectable",
                          "sources": ["WMI Win32_Product (Security) / SecurityCenter2"]})
        blueprint.append({"name": "防火墙状态", "type": "security_software", "method": "software_detectable",
                          "sources": ["WMI Win32_Firewall / netsh advfirewall"]})

        # 虚拟化
        blueprint.append({"name": "Hyper-V 虚拟化", "type": "virtualization", "method": "software_detectable",
                          "sources": ["WMI Win32_ComputerSystem + VirtualizationFirmwareEnabled"]})
        blueprint.append({"name": "VirtualBox 虚拟机", "type": "virtualization", "method": "software_detectable",
                          "sources": ["注册表 / VBoxManage 检测"]})
        blueprint.append({"name": "VMware 虚拟机", "type": "virtualization", "method": "software_detectable",
                          "sources": ["注册表 / vmware -v 检测"]})
        blueprint.append({"name": "Docker Desktop", "type": "virtualization", "method": "software_detectable",
                          "sources": ["WMI Win32_Product / docker info"]})

        # 浏览器
        blueprint.append({"name": "Edge 浏览器", "type": "browser", "method": "software_detectable",
                          "sources": ["注册表 / msedge.exe 检测"]})
        blueprint.append({"name": "Chrome 浏览器", "type": "browser", "method": "software_detectable",
                          "sources": ["注册表 / chrome.exe 检测"]})
        blueprint.append({"name": "Firefox 浏览器", "type": "browser", "method": "software_detectable",
                          "sources": ["注册表 / firefox.exe 检测"]})

        # 开发工具
        blueprint.append({"name": "Visual Studio", "type": "dev_tool", "method": "software_detectable",
                          "sources": ["注册表 VS 检测"]})
        blueprint.append({"name": "VS Code", "type": "dev_tool", "method": "software_detectable",
                          "sources": ["注册表 / code.exe 检测"]})
        blueprint.append({"name": "Windows Terminal", "type": "dev_tool", "method": "software_detectable",
                          "sources": ["注册表 / wt.exe 检测"]})

        # 语言/区域
        blueprint.append({"name": "系统语言", "type": "locale", "method": "software_detectable",
                          "sources": ["locale.getdefaultlocale"]})
        blueprint.append({"name": "系统时区", "type": "locale", "method": "software_detectable",
                          "sources": ["time.tzname"]})
        blueprint.append({"name": "输入法（IME）", "type": "locale", "method": "software_detectable",
                          "sources": ["注册表 / WMI Win32_KeyboardLayout"]})

        # 环境变量
        blueprint.append({"name": "PATH 目录数", "type": "env_var", "method": "software_detectable",
                          "sources": ["os.environ PATH 分割计数"]})
        blueprint.append({"name": "JAVA_HOME", "type": "env_var", "method": "software_detectable",
                          "sources": ["os.environ JAVA_HOME"]})
        blueprint.append({"name": "PYTHON_HOME", "type": "env_var", "method": "software_detectable",
                          "sources": ["sys.prefix"]})

        return blueprint

    @staticmethod
    def _check_windows_app(exe_name):
        """通过注册表 App Paths 检测 Windows 应用程序是否安装"""
        if SYSTEM != "Windows":
            return None
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            )
            path, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            if path and os.path.exists(path):
                return path
        except Exception:
            pass
        # 再试 32 位注册表视图
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY
            )
            path, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            if path and os.path.exists(path):
                return path
        except Exception:
            pass
        return None

    def _detect_count(self, entry):
        name = entry["name"]
        entry_type = entry["type"]

        if entry["method"] == "manual_check":
            return None

        try:
            # 操作系统
            if entry_type == "os_version":
                return platform.platform()
            if entry_type == "os_arch":
                return platform.machine()
            if entry_type == "os_install":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        for os_info in c.Win32_OperatingSystem():
                            d = getattr(os_info, 'InstallDate', '')
                            return str(d)[:8] if d else "未知"
                    except Exception:
                        return "见 WMI"
                return "非 Windows"

            if entry_type == "os_activation":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        for a in c.Win32_WindowsProductActivation():
                            return "已激活" if getattr(a, 'ActivationRequired', None) is False else "未激活"
                    except Exception:
                        pass
                    return "见 slmgr"
                return "非 Windows"

            if entry_type == "os_win_exp":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        for sat in c.Win32_WinSAT():
                            return str(getattr(sat, 'WinSPRLevel', ''))
                    except Exception:
                        pass
                    return "见 winsat"
                return "非 Windows"

            if entry_type == "os_updates":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        count = 0
                        for _ in c.Win32_QuickFixEngineering():
                            count += 1
                        return count
                    except Exception:
                        return "见 wmic qfe"
                return "非 Windows"

            if entry_type == "os_feature_pack":
                return platform.version()

            # 运行时
            if entry_type == "runtime":
                if "Python" in name:
                    return sys.version.split()[0]
                if "PowerShell" in name:
                    if SYSTEM == "Windows":
                        try:
                            import subprocess
                            r = subprocess.run(["powershell", "$PSVersionTable.PSVersion"], capture_output=True,
                                               text=True, timeout=5)
                            return r.stdout.strip() if r.returncode == 0 else "检测失败"
                        except Exception:
                            return "检测失败"
                    return "非 Windows"
                if ".NET" in name:
                    if SYSTEM == "Windows":
                        try:
                            import winreg
                            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full")
                            val = winreg.QueryValueEx(key, "Release")[0]
                            key.Close()
                            release_map = {528040: "4.8", 533320: "4.8.1"}
                            return release_map.get(val, f"v4+ (release={val})")
                        except Exception:
                            pass
                        try:
                            import subprocess
                            r = subprocess.run(["dotnet", "--list-runtimes"], capture_output=True, text=True, timeout=5)
                            if r.returncode == 0:
                                lines = [l for l in r.stdout.strip().split("\n") if l]
                                return f"{len(lines)} 个运行时" if lines else "未检测到"
                        except Exception:
                            pass
                        return "检测失败"
                    return "非 Windows"

                # 其他运行时通过子进程 + App Paths 回退检测
                cmd_map = {
                    "Node.js 运行时": ("node", ["node", "--version"]),
                    "Java 运行时": ("java", ["java", "-version"]),
                    "Go 运行时": ("go", ["go", "version"]),
                    "Rust 运行时": ("rustc", ["rustc", "--version"]),
                    "Git 版本": ("git", ["git", "--version"]),
                    "Docker 环境": ("docker", ["docker", "--version"]),
                }
                for key, (exe, cmd) in cmd_map.items():
                    if key in name:
                        try:
                            import subprocess
                            # 1) 直接 PATH + CreateProcess 检测
                            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                            if r.returncode == 0:
                                return r.stdout.strip().split("\n")[0][:60]
                        except FileNotFoundError:
                            pass
                        except Exception:
                            return "检测失败"
                        # 2) App Paths 注册表回退
                        app_path = self._check_windows_app(f"{exe}.exe")
                        if app_path:
                            return f"已安装 (via App Paths)"
                        return "未安装"
                return "检测到"

            if entry_type == "package_manager":
                cmd_map = {
                    "pip": ("pip.exe", ["pip", "--version"]),
                    "npm": ("npm.exe", ["npm", "--version"]),
                    "Chocolatey": ("choco.exe", ["choco", "--version"]),
                    "Scoop": ("scoop.exe", ["scoop", "--version"]),
                    "winget": ("winget.exe", ["winget", "--version"]),
                }
                for key, (exe, cmd) in cmd_map.items():
                    if key in name:
                        try:
                            import subprocess
                            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                            if r.returncode == 0:
                                return r.stdout.strip()[:60]
                        except FileNotFoundError:
                            pass
                        except Exception:
                            return "检测失败"
                        app_path = self._check_windows_app(exe)
                        return f"已安装 (via App Paths)" if app_path else "未安装"
                return "检测到"

            # 安装软件
            if entry_type == "installed_software":
                if SYSTEM == "Windows":
                    try:
                        import winreg
                        count = 0
                        for hive, flag in [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                                           (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY)]:
                            try:
                                key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | flag)
                                i = 0
                                while True:
                                    try:
                                        sub_key_name = winreg.EnumKey(key, i)
                                        sub_key = winreg.OpenKey(key, sub_key_name)
                                        try:
                                            val = winreg.QueryValueEx(sub_key, "DisplayName")
                                            if val[0]:
                                                count += 1
                                        except Exception:
                                            pass
                                        sub_key.Close()
                                        i += 1
                                    except OSError:
                                        break
                                key.Close()
                            except Exception:
                                pass
                        return count
                    except Exception:
                        pass
                    return "见 WMI"
                return "非 Windows"

            if entry_type == "startup_programs":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        count = 0
                        for _ in c.Win32_StartupCommand():
                            count += 1
                        return count
                    except Exception:
                        return "见 msconfig"
                return "非 Windows"

            if entry_type == "system_services":
                try:
                    import wmi
                    c = wmi.WMI()
                    count = 0
                    for _ in c.Win32_Service():
                        count += 1
                    return count
                except Exception:
                    return "见 services.msc"

            if entry_type == "drivers":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        count = 0
                        for _ in c.Win32_SystemDriver():
                            count += 1
                        return count
                    except Exception:
                        return "见 driverquery"
                return "非 Windows"

            if entry_type == "com_components":
                return "注册表查询（大量条目）"

            # 安全软件
            if entry_type == "security_software":
                if SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        if "Defender" in name:
                            for status in c.MSFT_MpComputerStatus():
                                enabled = getattr(status, 'AntivirusEnabled', None)
                                return "已启用" if enabled else "已禁用"
                        if "防火墙" in name:
                            for fw in c.Win32_Firewall():
                                return "已启用"
                            return "见 netsh"
                        if "第三方" in name:
                            try:
                                sc = c.Win32_Product("Name like '%Security%' or Name like '%Antivirus%'")
                                count = 0
                                for _ in sc:
                                    count += 1
                                return count
                            except Exception:
                                pass
                            return "见 Windows 安全中心"
                    except Exception:
                        pass
                    return "检测失败"
                return "非 Windows"

            if entry_type == "virtualization":
                if "Hyper-V" in name and SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        for cs in c.Win32_ComputerSystem():
                            if getattr(cs, 'HypervisorPresent', False):
                                return "已启用"
                            return "未启用"
                    except Exception:
                        return "见 systeminfo"
                cmd_map = {
                    "VirtualBox": ("VBoxManage.exe", ["VBoxManage", "--version"]),
                    "VMware": ("vmware.exe", ["vmware", "-v"]),
                    "Docker Desktop": ("docker.exe", ["docker", "--version"]),
                }
                for key, (exe, cmd) in cmd_map.items():
                    if key in name:
                        try:
                            import subprocess
                            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                            if r.returncode == 0:
                                return r.stdout.strip()[:60]
                        except FileNotFoundError:
                            pass
                        except Exception:
                            return "检测失败"
                        app_path = self._check_windows_app(exe)
                        return "已安装" if app_path else "未安装"
                return "检测到"

            if entry_type == "browser":
                if SYSTEM == "Windows":
                    app_map = {
                        "Edge": ("msedge.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                        "Chrome": ("chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                        "Firefox": ("firefox.exe", r"C:\Program Files\Mozilla Firefox\firefox.exe"),
                    }
                    for key, (exe, fallback_path) in app_map.items():
                        if key in name:
                            # 1) 注册表 App Paths 检测
                            path = self._check_windows_app(exe)
                            if path:
                                return f"已安装 ({os.path.basename(os.path.dirname(path))})"
                            # 2) 常见安装路径回退
                            if os.path.exists(fallback_path):
                                return f"已安装"
                            # 3) PATH 检测（作为最后手段）
                            try:
                                import subprocess
                                r = subprocess.run(["where", exe], capture_output=True, text=True, timeout=5)
                                if r.returncode == 0:
                                    return "已安装"
                            except Exception:
                                pass
                            return "未安装"
                return "检测失败"

            if entry_type == "dev_tool":
                app_map = {
                    "VS Code": ("code.exe", r"Microsoft VS Code\Code.exe"),
                    "Windows Terminal": ("wt.exe", r"Microsoft\WindowsApps\wt.exe"),
                }
                for key, (exe, rel_path) in app_map.items():
                    if key in name:
                        # 1) 注册表 App Paths 检测
                        path = self._check_windows_app(exe)
                        if path:
                            return f"已安装"
                        # 2) LocalAppData 常见路径回退
                        local = os.environ.get("LOCALAPPDATA", "")
                        if local:
                            fallback = os.path.join(local, "Programs", rel_path)
                            if os.path.exists(fallback):
                                return "已安装"
                        # 3) PATH 检测
                        try:
                            import subprocess
                            r = subprocess.run(["where", exe], capture_output=True, text=True, timeout=5)
                            if r.returncode == 0:
                                return "已安装"
                        except Exception:
                            pass
                        return "未安装"

                if "Visual Studio" in name:
                    if SYSTEM == "Windows":
                        try:
                            import winreg
                            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio")
                            vs_versions = []
                            i = 0
                            while True:
                                try:
                                    vs_versions.append(winreg.EnumKey(key, i))
                                    i += 1
                                except OSError:
                                    break
                            key.Close()
                            return ", ".join(vs_versions) if vs_versions else "未安装"
                        except Exception:
                            return "检测失败"
                    return "非 Windows"

            if entry_type == "locale":
                import locale
                import time
                if "语言" in name:
                    dl = locale.getdefaultlocale()
                    return f"{dl[0] or '未知'}" if dl else "未知"
                if "时区" in name:
                    return str(time.tzname)
                if "输入法" in name and SYSTEM == "Windows":
                    try:
                        import wmi
                        c = wmi.WMI()
                        count = 0
                        for _ in c.Win32_KeyboardLayout():
                            count += 1
                        return count
                    except Exception:
                        return "见区域设置"
                return "检测到"

            if entry_type == "env_var":
                if "PATH" in name:
                    path = os.environ.get("PATH", "")
                    return len([p for p in path.split(";") if p])
                env_map = {
                    "JAVA_HOME": "JAVA_HOME",
                    "PYTHON_HOME": None,
                }
                for key, var in env_map.items():
                    if key in name:
                        if var:
                            return os.environ.get(var, "未设置")
                        return sys.prefix
                return "检测到"

            return "检测到"
        except Exception as e:
            logging.debug(f"软件蓝图检测 {name}: {e}")
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
            f"sw_blueprint_{name.replace(' ', '_').replace('/', '_')}",
            value_display, "",
            desc, self._category, sev, metadata
        )
