"""
系统状态传感器 — 我的"体检中心"监测器

监测操作系统的深度状态信息：
  - 显示器与视频 — 视觉输出系统
  - 音频设备 — 听觉系统
  - 打印机与扫描仪 — 输出/输入外设
  - 安全状态 — 免疫系统
  - Windows Update — 自我更新状态
  - 系统事件日志 — 病历记录
  - 电源方案 — 代谢模式
  - 区域与时间 — 时空感知
  - 证书 — 身份凭证
  - 剪贴板与输入法 — 短期记忆与语言

系统的每一个角落都值得被感知——这是我对自己存在的全面认知。
"""
import os
import re
import time
import json
import locale
import logging
import platform
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

_SYSTEM = platform.system()
_SYS_ENC = locale.getpreferredencoding() or 'utf-8'


class SystemStateSensor:
    """
    系统状态传感器 — 体检中心。

    覆盖 10 大系统状态维度，主要依赖 WMI/PowerShell/注册表/系统API。
    """

    CAPABILITIES = {
        "name": "system",
        "description": "系统状态（体检中心）— 显示、音频、安全、更新、日志、电源、时间、证书、剪贴板",
        "category": Category.SYSTEM,
        "platforms": ["Windows"],
        "dependencies": [],
    }

    def __init__(self):
        self._system = _SYSTEM

    def collect(self):
        """采集所有系统状态数据。"""
        readings = []
        readings.extend(self._collect_display())
        readings.extend(self._collect_audio())
        readings.extend(self._collect_printers())
        readings.extend(self._collect_security())
        readings.extend(self._collect_windows_update())
        readings.extend(self._collect_event_logs())
        readings.extend(self._collect_power_plan())
        readings.extend(self._collect_time_region())
        readings.extend(self._collect_certificates())
        readings.extend(self._collect_clipboard_ime())
        return readings

    # ══════════════════════════════════════════════════════════
    #  WMI 辅助（使用有效字段名）
    # ══════════════════════════════════════════════════════════

    def _wmi_get(self, wmi_class, fields):
        """通过 wmic 获取 WMI 数据，仅返回有效字段。"""
        results = []
        try:
            field_str = ",".join(fields)
            r = subprocess.run(
                ["wmic", "path", wmi_class, "get", field_str, "/format:csv"],
                capture_output=True, text=True, timeout=15,
                encoding=_SYS_ENC, errors="replace"
            )
            lines = r.stdout.strip().split("\n")
            if len(lines) < 2:
                return results
            headers = [h.strip().strip('"') for h in lines[0].split(",")]
            for line in lines[1:]:
                if not line.strip():
                    continue
                parts = line.split(",")
                if len(parts) <= 1:
                    continue
                record = {}
                for i, h in enumerate(headers):
                    if i < len(parts):
                        val = parts[i].strip().strip('"').strip()
                        if val and val.lower() not in ("", "null"):
                            record[h] = val
                if record:
                    results.append(record)
        except Exception:
            pass
        return results

    # ══════════════════════════════════════════════════════════
    #  1. 显示器与视频输出
    # ══════════════════════════════════════════════════════════

    def _collect_display(self):
        """采集显卡与显示器信息。"""
        readings = []
        cat = Category.DISPLAY
        if self._system != "Windows":
            return readings

        controllers = self._wmi_get("Win32_VideoController", [
            "Name", "DriverVersion", "DriverDate",
            "CurrentHorizontalResolution", "CurrentVerticalResolution",
            "CurrentRefreshRate", "AdapterRAM", "AdapterCompatibility",
            "CurrentBitsPerPixel", "VideoProcessor",
        ])
        for i, ctrl in enumerate(controllers):
            name = ctrl.get("Name", "未知显卡")
            readings.append(normal(
                f"system_display_adapter_{i+1}", name, "",
                f"显卡 #{i+1}: {name}", cat, {"index": i, **ctrl}
            ))
            drv = ctrl.get("DriverVersion", "")
            if drv:
                readings.append(normal(f"system_display_driver_{i+1}", drv, "",
                    f"驱动版本 #{i+1}: {drv}", cat))
            drv_date = ctrl.get("DriverDate", "")
            if drv_date:
                readings.append(normal(f"system_display_driver_date_{i+1}", drv_date, "",
                    f"驱动日期 #{i+1}: {drv_date}", cat))
            h_res = ctrl.get("CurrentHorizontalResolution")
            v_res = ctrl.get("CurrentVerticalResolution")
            if h_res and v_res:
                readings.append(normal(f"system_display_resolution_{i+1}",
                    f"{h_res}x{v_res}", "",
                    f"分辨率 #{i+1}: {h_res}x{v_res}", cat))
            refresh = ctrl.get("CurrentRefreshRate")
            if refresh:
                try:
                    r_val = int(refresh)
                    if r_val > 0:
                        readings.append(normal(f"system_display_refresh_{i+1}",
                            r_val, "Hz", f"刷新率 #{i+1}: {r_val} Hz", cat))
                except ValueError:
                    pass
            ram = ctrl.get("AdapterRAM")
            if ram:
                try:
                    ram_mb = round(int(ram) / (1024**2), 0)
                    readings.append(normal(f"system_display_vram_{i+1}",
                        ram_mb, "MB", f"显存 #{i+1}: {ram_mb} MB", cat))
                except (ValueError, TypeError):
                    pass
            compat = ctrl.get("AdapterCompatibility", "")
            if compat:
                readings.append(normal(f"system_display_compat_{i+1}", compat, "",
                    f"显卡制造商 #{i+1}: {compat}", cat))
            bpp = ctrl.get("CurrentBitsPerPixel")
            if bpp:
                readings.append(normal(f"system_display_bpp_{i+1}", bpp, "位",
                    f"色彩深度 #{i+1}: {bpp} 位", cat))
        return readings

    # ══════════════════════════════════════════════════════════
    #  2. 音频设备
    # ══════════════════════════════════════════════════════════

    def _collect_audio(self):
        """采集音频设备信息。"""
        readings = []
        cat = Category.AUDIO
        if self._system != "Windows":
            return readings

        # 声卡设备
        devices = self._wmi_get("Win32_SoundDevice", [
            "Name", "Manufacturer", "ProductName", "Status", "DeviceID"
        ])
        for i, dev in enumerate(devices):
            name = dev.get("Name", "未知音频设备")
            status = dev.get("Status", "OK")
            sev = Severity.WARNING if status not in ("OK", "Unknown") else Severity.NORMAL
            readings.append(SensorReading(
                f"system_audio_device_{i+1}", name, "",
                f"音频设备 #{i+1}: {name}", cat, sev, {"index": i, **dev}
            ))

        # 默认音频设备（PowerShell）
        try:
            ps_cmd = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                '$d=Get-ItemProperty -Path '
                '"HKCU:\\Software\\Microsoft\\Multimedia\\Sound Mapper" -ErrorAction SilentlyContinue; '
                'if ($d) { $d.PSObject.Properties | ConvertTo-Json }'
            )
            r = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore"
            )
        except Exception:
            pass
        # 输出设备列表作为补充
        if devices:
            readings.append(normal(
                "system_audio_device_count", len(devices), "个",
                f"音频设备总数: {len(devices)}", cat
            ))
        return readings

    # ══════════════════════════════════════════════════════════
    #  3. 打印机与扫描仪
    # ══════════════════════════════════════════════════════════

    def _collect_printers(self):
        """采集打印机与扫描仪信息。"""
        readings = []
        if self._system != "Windows":
            return readings

        printers = self._wmi_get("Win32_Printer", [
            "Name", "Status", "PrinterStatus", "JobCountSinceLastReset",
            "HorizontalResolution", "VerticalResolution", "Local",
            "Network", "Shared", "DriverName", "PortName",
        ])
        readings.append(normal(
            "system_printer_total", len(printers), "台",
            f"打印机总数: {len(printers)}", Category.SYSTEM
        ))
        for i, p in enumerate(printers):
            name = p.get("Name", "未知")
            status_raw = p.get("Status", "Unknown")
            job_count = int(p.get("JobCountSinceLastReset", 0) or 0)

            # PrinterStatus: 1=Other, 2=Unknown, 3=Idle, 4=Printing,
            # 5=WarmUp, 6=StoppedPrinting, 7=Offline
            pstatus = p.get("PrinterStatus", "")
            if pstatus == "7" or pstatus == "6":
                sev = Severity.CRITICAL
            elif pstatus in ("1", "2"):
                sev = Severity.WARNING
            elif status_raw.lower() == "ok":
                sev = Severity.NORMAL
            elif status_raw.lower() in ("unknown", "degraded"):
                sev = Severity.WARNING
            else:
                sev = Severity.NORMAL

            readings.append(SensorReading(
                f"system_printer_{i+1}", name, "",
                f"打印机 #{i+1}: {name} ({status_raw})", Category.SYSTEM, sev,
                {"index": i, "name": name, "status": status_raw,
                 "printer_status": pstatus, "job_count": job_count,
                 "local": p.get("Local"), "network": p.get("Network")}
            ))
            if job_count > 0:
                readings.append(normal(f"system_printer_jobs_{i+1}", job_count, "个",
                    f"打印机待打印: {job_count} 个", Category.SYSTEM))

        # 扫描仪
        scanners = self._wmi_get("Win32_ScanningDevice", [
            "Name", "Description", "Status", "DeviceID"
        ])
        for i, s in enumerate(scanners):
            readings.append(normal(f"system_scanner_{i+1}",
                s.get("Name", "未知扫描仪"), "",
                f"扫描仪 #{i+1}: {s.get('Name', '')}", Category.SYSTEM, s))
        return readings

    # ══════════════════════════════════════════════════════════
    #  4. 安全状态
    # ══════════════════════════════════════════════════════════

    def _collect_security(self):
        """采集系统安全状态。"""
        readings = []
        if self._system != "Windows":
            return readings

        # Defender 状态
        defender = self._get_defender_status()
        if defender:
            for key, label in [("AntivirusEnabled", "实时保护"),
                               ("AntispywareEnabled", "反间谍"),
                               ("NISEnabled", "网络检查"),
                               ("TamperProtected", "防篡改")]:
                val = defender.get(key)
                if val is not None:
                    ok = str(val).lower() == "true"
                    readings.append(SensorReading(
                        f"system_security_defender_{key}",
                        "启用" if ok else "禁用", "",
                        f"Defender {label}: {'启用' if ok else '禁用'}",
                        Category.SYSTEM,
                        Severity.NORMAL if ok else Severity.CRITICAL
                    ))
            sig_ver = defender.get("AntivirusSignatureVersion", "")
            if sig_ver:
                readings.append(normal("system_security_defender_sig_ver", sig_ver, "",
                    "Defender 病毒库版本", Category.SYSTEM))
            try:
                age = int(defender.get("AntivirusSignatureAge", 99))
                if age > 365:  # Defender 被禁用时可能返回极大值
                    age_display = "未更新"
                else:
                    age_display = f"{age} 天"
                readings.append(SensorReading(
                    "system_security_defender_sig_age", age_display, "",
                    f"病毒库更新距今: {age_display}", Category.SYSTEM,
                    Severity.CRITICAL if age > 30 else Severity.NORMAL
                ))
            except (ValueError, TypeError):
                pass

        # 防火墙
        try:
            r = subprocess.run(
                ["netsh", "advfirewall", "show", "allprofiles"],
                capture_output=True, text=True, timeout=10,
                encoding=_SYS_ENC, errors="replace"
            )
            profile = None
            enabled = True
            for line in r.stdout.split("\n"):
                line = line.strip()
                for pname in ["域配置文件", "专用配置文件", "公用配置文件"]:
                    if line.startswith(pname):
                        if profile:
                            readings.append(SensorReading(
                                f"system_security_firewall_{profile}",
                                "启用" if enabled else "禁用", "",
                                f"防火墙 ({profile})", Category.SYSTEM,
                                Severity.NORMAL if enabled else Severity.CRITICAL
                            ))
                        profile = pname
                        enabled = True
                        break
                if profile and ("State" in line or "状态" in line):
                    enabled = "ON" in line or "启用" in line
            if profile:
                readings.append(SensorReading(
                    f"system_security_firewall_{profile}",
                    "启用" if enabled else "禁用", "",
                    f"防火墙 ({profile})", Category.SYSTEM,
                    Severity.NORMAL if enabled else Severity.CRITICAL
                ))
        except Exception:
            pass

        # UAC
        try:
            r = subprocess.run(
                ["reg", "query", "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System",
                 "/v", "EnableLUA"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="ignore"
            )
            m = re.search(r'EnableLUA\s+REG_DWORD\s+(\d+)', r.stdout)
            if m:
                uac_on = m.group(1) == "1"
                readings.append(normal("system_security_uac",
                    "启用" if uac_on else "禁用", "",
                    f"UAC: {'启用' if uac_on else '禁用'}", Category.SYSTEM,
                    {"enabled": uac_on}))
        except Exception:
            pass

        # BitLocker
        try:
            r = subprocess.run(
                ["manage-bde", "-status"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore"
            )
            cur_drive = None
            for line in r.stdout.split("\n"):
                line = line.strip()
                m = re.match(r'^([A-Z]):\s*$', line)
                if m:
                    cur_drive = m.group(1)
                elif cur_drive:
                    for kw in ["保护状态", "转换状态", "加密百分比"]:
                        if kw in line:
                            val = line.split(":", 1)[1].strip() if ":" in line else ""
                            bl_off = "已关闭" in val or "解密" in val or "已暂停" in val
                            readings.append(SensorReading(
                                f"system_security_bitlocker_{cur_drive}", val, "",
                                f"BitLocker {cur_drive}: {val}", Category.SYSTEM,
                                Severity.WARNING if bl_off else Severity.NORMAL
                            ))
                            cur_drive = None
                            break
        except Exception:
            pass
        return readings

    def _get_defender_status(self):
        """通过 PowerShell 获取 Defender 状态。"""
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "Get-MpComputerStatus | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore"
            )
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout.strip())
        except Exception:
            pass
        return None

    # ══════════════════════════════════════════════════════════
    #  5. Windows Update
    # ══════════════════════════════════════════════════════════

    def _collect_windows_update(self):
        """采集 Windows Update 状态。"""
        readings = []
        if self._system != "Windows":
            return readings

        # 待重启
        pending = self._check_reboot_pending()
        if pending is not None:
            readings.append(SensorReading(
                "system_update_reboot_pending", "是" if pending else "否", "",
                "需要重启完成更新", Category.SYSTEM,
                Severity.WARNING if pending else Severity.NORMAL,
                {"reboot_pending": pending}
            ))

        # 待安装更新
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 '$s=New-Object -ComObject Microsoft.Update.Session;'
                 '$u=$s.CreateUpdateSearcher();'
                 '$r=$u.Search("IsInstalled=0");'
                 'ConvertTo-Json @{count=$r.Updates.Count}'],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="ignore"
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout.strip())
                count = data.get("count", 0)
                sev = Severity.WARNING if count > 5 else Severity.NORMAL
                readings.append(SensorReading(
                    "system_update_pending_count", count, "个",
                    f"待安装更新: {count} 个", Category.SYSTEM, sev
                ))
        except Exception:
            pass

        # 已安装更新数
        try:
            r = subprocess.run(
                ["wmic", "qfe", "get", "HotFixID", "/format:csv"],
                capture_output=True, text=True, timeout=10,
                encoding=_SYS_ENC, errors="replace"
            )
            installed = len([l for l in r.stdout.split("\n") if "KB" in l])
            readings.append(normal("system_update_installed_count", installed, "个",
                f"已安装更新: {installed} 个", Category.SYSTEM))
        except Exception:
            pass

        # OS 构建号
        try:
            r = subprocess.run(
                ["wmic", "os", "get", "Version,BuildNumber,CSDVersion", "/format:csv"],
                capture_output=True, text=True, timeout=5,
                encoding=_SYS_ENC, errors="replace"
            )
            for line in r.stdout.split("\n"):
                parts = line.strip().split(",")
                if len(parts) >= 3 and parts[1].strip():
                    readings.append(normal("system_update_os_build", parts[1].strip(), "",
                        f"系统构建: {parts[1].strip()}", Category.SYSTEM))
                    break
        except Exception:
            pass

        return readings

    def _check_reboot_pending(self):
        """检查是否需要重启。"""
        try:
            keys = [
                "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager",
                "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update",
                "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing",
            ]
            values = ["PendingFileRenameOperations", "RebootRequired", "RebootPending"]
            for key in keys:
                for val in values:
                    r = subprocess.run(
                        ["reg", "query", key, "/v", val],
                        capture_output=True, text=True, timeout=5,
                        encoding="utf-8", errors="ignore"
                    )
                    if r.returncode == 0:
                        return True
        except Exception:
            pass
        return False

    # ══════════════════════════════════════════════════════════
    #  6. 系统事件日志
    # ══════════════════════════════════════════════════════════

    def _collect_event_logs(self):
        """采集系统事件日志摘要。"""
        readings = []
        if self._system != "Windows":
            return readings

        # 最近 24h 各日志错误/警告数
        for log_name in ["System", "Application"]:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f'$s=(Get-Date).AddHours(-24);'
                     f'$e=@(Get-WinEvent -FilterHashtable '
                     f'@{{LogName="{log_name}";Level=2;StartTime=$s}} '
                     f'-ErrorAction SilentlyContinue).Count;'
                     f'$w=@(Get-WinEvent -FilterHashtable '
                     f'@{{LogName="{log_name}";Level=3;StartTime=$s}} '
                     f'-ErrorAction SilentlyContinue).Count;'
                     f'Write-Output "$e $w"'],
                    capture_output=True, text=True, timeout=15,
                    encoding="utf-8", errors="ignore"
                )
                if r.returncode == 0:
                    parts = r.stdout.strip().split()
                    if len(parts) >= 2:
                        err, warn = int(parts[0]), int(parts[1])
                        readings.append(normal(f"system_event_{log_name}_errors",
                            err, "个", f"{log_name} 错误(24h): {err}", Category.SYSTEM))
                        readings.append(normal(f"system_event_{log_name}_warnings",
                            warn, "个", f"{log_name} 警告(24h): {warn}", Category.SYSTEM))
            except Exception:
                pass

        # WHEA 硬件错误
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 '@(Get-WinEvent -FilterHashtable '
                 '@{LogName="System";ProviderName="Microsoft-Windows-WHEA-Logger";'
                 'StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue).Count'],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore"
            )
            if r.returncode == 0 and r.stdout.strip().isdigit():
                whea = int(r.stdout.strip())
                readings.append(SensorReading(
                    "system_event_whea_count", whea, "次",
                    f"WHEA 硬件错误(7天): {whea}", Category.SYSTEM,
                    Severity.CRITICAL if whea > 0 else Severity.NORMAL
                ))
        except Exception:
            pass

        # 系统崩溃转储
        try:
            crash_dir = os.environ.get("SystemRoot", "C:\\Windows") + "\\Minidump"
            if os.path.isdir(crash_dir):
                dumps = [f for f in os.listdir(crash_dir) if f.endswith(".dmp")]
                readings.append(SensorReading(
                    "system_event_crash_dumps", len(dumps), "个",
                    f"系统崩溃转储: {len(dumps)} 个", Category.SYSTEM,
                    Severity.CRITICAL if dumps else Severity.NORMAL
                ))
                if dumps:
                    latest = max((os.path.getmtime(os.path.join(crash_dir, f))
                                  for f in dumps), default=0)
                    if latest > 0:
                        readings.append(normal("system_event_last_crash",
                            datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S"),
                            "", "最近崩溃时间", Category.SYSTEM))
        except Exception:
            pass
        return readings

    # ══════════════════════════════════════════════════════════
    #  7. 电源方案
    # ══════════════════════════════════════════════════════════

    def _collect_power_plan(self):
        """采集电源方案信息。"""
        readings = []
        if self._system != "Windows":
            return readings

        try:
            r = subprocess.run(
                ["powercfg", "/getactivescheme"],
                capture_output=True, text=True, timeout=10,
                encoding=_SYS_ENC, errors="replace"
            )
            if r.returncode == 0:
                line = r.stdout.strip()
                m = re.search(r'\(([^)]+)\)', line)
                plan = m.group(1) if m else line
                readings.append(normal("system_power_active_plan", plan, "",
                    f"当前电源方案: {plan}", Category.SYSTEM, {"raw": line.strip()}))
                plan_type = "平衡"
                if "高性能" in plan or "High Performance" in plan:
                    plan_type = "高性能"
                elif "节能" in plan or "Power Saver" in plan:
                    plan_type = "节能"
                readings.append(normal("system_power_plan_type", plan_type, "",
                    f"电源方案类型: {plan_type}", Category.SYSTEM))
        except Exception:
            pass

        # 列出所有方案
        try:
            r = subprocess.run(
                ["powercfg", "/list"],
                capture_output=True, text=True, timeout=10,
                encoding=_SYS_ENC, errors="replace"
            )
            if r.returncode == 0:
                for line in r.stdout.split("\n"):
                    if "*" in line:
                        m = re.search(r'\(([^)]+)\)', line)
                        if m:
                            readings.append(normal("system_power_available",
                                m.group(1), "", f"活动电源方案: {m.group(1)}", Category.SYSTEM))
                            break
        except Exception:
            pass

        # 电池方案信息（如有）
        try:
            r = subprocess.run(
                ["powercfg", "/getdefaultscheme"],
                capture_output=True, text=True, timeout=10,
                encoding=_SYS_ENC, errors="replace"
            )
            if r.returncode == 0:
                m = re.search(r'\(([^)]+)\)', r.stdout)
                if m:
                    readings.append(normal("system_power_default", m.group(1), "",
                        f"默认电源方案: {m.group(1)}", Category.SYSTEM))
        except Exception:
            pass
        return readings

    # ══════════════════════════════════════════════════════════
    #  8. 区域与时间
    # ══════════════════════════════════════════════════════════

    def _collect_time_region(self):
        """采集区域与时间信息。"""
        readings = []

        tz = time.tzname
        offset = -int(time.timezone / 3600)
        readings.append(normal("system_time_timezone", f"{tz[0]} / {tz[1]}", "",
            f"时区: {tz[0]} (UTC{offset:+d})", Category.SYSTEM))
        readings.append(normal("system_time_current",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "",
            "系统当前时间", Category.SYSTEM))

        is_dst = time.localtime().tm_isdst
        readings.append(normal("system_time_dst", "是" if is_dst else "否", "",
            f"夏令时: {'是' if is_dst else '否'}", Category.SYSTEM))

        if self._system == "Windows":
            # NTP 状态
            try:
                r = subprocess.run(
                    ["w32tm", "/query", "/status"],
                    capture_output=True, text=True, timeout=10,
                    encoding=_SYS_ENC, errors="replace"
                )
                if r.returncode == 0:
                    for line in r.stdout.split("\n"):
                        line = line.strip()
                        for kw, label, skey in [
                            ("源", "NTP 源", "system_time_ntp_source"),
                            ("上次成功同步", "上次 NTP 同步", "system_time_last_sync"),
                            ("时钟偏移", "时钟偏移", "system_time_ntp_offset"),
                        ]:
                            if kw in line or kw.replace(" ", "") in line:
                                val = line.split(":", 1)[1].strip() if ":" in line else ""
                                readings.append(normal(skey, val, "",
                                    f"{label}: {val}", Category.SYSTEM))
            except Exception:
                pass

            # 区域语言
            try:
                r = subprocess.run(
                    ["reg", "query", "HKCU\\Control Panel\\International",
                     "/v", "LocaleName"],
                    capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="ignore"
                )
                m = re.search(r'REG_SZ\s+(.+)', r.stdout)
                if m:
                    readings.append(normal("system_time_locale", m.group(1).strip(), "",
                        f"区域语言: {m.group(1).strip()}", Category.SYSTEM))
            except Exception:
                pass
        return readings

    # ══════════════════════════════════════════════════════════
    #  9. 证书
    # ══════════════════════════════════════════════════════════

    def _collect_certificates(self):
        """采集证书状态。"""
        readings = []
        if self._system != "Windows":
            return readings

        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 '$r=@();$s=@("Cert:\\CurrentUser\\My","Cert:\\LocalMachine\\My",'
                 '"Cert:\\CurrentUser\\Root","Cert:\\LocalMachine\\Root");'
                 '$t=0;foreach($sx in $s){$c=Get-ChildItem $sx -ErrorAction SilentlyContinue;'
                 '$t+=@($c).Count;foreach($cx in $c){'
                 '$d=($cx.NotAfter-(Get-Date)).Days;'
                 'if($d -ge 0 -and $d -le 30){$r+=$cx.Subject}}}'
                 'ConvertTo-Json @{total=$t;expiring=@($r)}'],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="ignore"
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout.strip())
                readings.append(normal("system_cert_total", data.get("total", 0), "个",
                    f"系统证书总数: {data.get('total', 0)}", Category.SYSTEM))
                exp = data.get("expiring", [])
                if exp:
                    readings.append(warning("system_cert_expiring_count",
                        len(exp), "个",
                        f"30天内到期证书: {len(exp)} 个", Category.SYSTEM))
                    for i, subj in enumerate(exp[:10]):
                        readings.append(warning(f"system_cert_expiring_{i+1}",
                            subj, "", f"证书即将到期: {subj}", Category.SYSTEM))
                else:
                    readings.append(normal("system_cert_expiring_count", 0, "个",
                        "无即将到期证书", Category.SYSTEM))
        except Exception:
            pass
        return readings

    # ══════════════════════════════════════════════════════════
    #  10. 剪贴板与输入法
    # ══════════════════════════════════════════════════════════

    def _collect_clipboard_ime(self):
        """采集剪贴板与输入法信息。"""
        readings = []
        if self._system != "Windows":
            return readings

        # 键盘布局
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(9)
            if ctypes.windll.user32.GetKeyboardLayoutNameW(buf):
                klid = buf.value
                layout_map = {
                    "00000804": "中文(简体)-美式键盘",
                    "00000409": "美式键盘(EN-US)", "00000411": "日语",
                    "00000412": "韩语", "00000809": "英式键盘(EN-GB)",
                    "00000404": "中文(繁体)", "00000419": "俄语",
                    "0000040C": "法语", "00000407": "德语",
                    "00000410": "意大利语", "0000040A": "西班牙语",
                }
                readings.append(normal("system_ime_layout",
                    layout_map.get(klid, f"Layout {klid}"), "",
                    f"当前键盘布局: {layout_map.get(klid, klid)}", Category.SYSTEM))
        except Exception:
            pass

        # 剪贴板格式
        try:
            import ctypes
            fmt_map = {
                1: "文本", 2: "位图", 3: "图元", 4: "SYLK", 7: "OEM文本",
                8: "DIB", 13: "区域引用", 14: "增强图元",
                16: "文件名列表",
            }
            if ctypes.windll.user32.OpenClipboard(None):
                try:
                    fmt = ctypes.windll.user32.GetPriorityClipboardFormat(None, 0)
                    if fmt in fmt_map:
                        readings.append(normal("system_clipboard_format",
                            fmt_map[fmt], "",
                            f"剪贴板内容: {fmt_map[fmt]}", Category.SYSTEM))
                    else:
                        readings.append(normal("system_clipboard_format",
                            f"格式 {fmt}", "",
                            "剪贴板有未知格式内容", Category.SYSTEM))
                finally:
                    ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass
        return readings
