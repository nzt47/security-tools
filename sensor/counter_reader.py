"""
系统性能计数器读取器 — 灵犀的"神经系统"扩展接口

通过 PowerShell Get-Counter、wmic、注册表等途径获取
标准 Python 库无法直接读取的 Windows 性能计数器。

每条数据标注来源，确保可追溯。
"""
import logging
import subprocess
import platform
import re

_SYSTEM = platform.system()
_log = logging.getLogger(__name__)

# ─── PowerShell 性能计数器 ──────────────────────────────────────

_POWERSPOOL_AVAILABLE = None  # 懒惰检测


def _check_powershell():
    """检查 PowerShell 是否可用"""
    global _POWERSPOOL_AVAILABLE
    if _POWERSPOOL_AVAILABLE is None:
        try:
            r = subprocess.run(
                ["powershell", "-Command", "Get-Counter -ListSet Memory | Out-Null; $true"],
                capture_output=True, text=True, timeout=5
            )
            _POWERSPOOL_AVAILABLE = r.returncode == 0 and r.stdout.strip() == "True"
        except Exception:
            _POWERSPOOL_AVAILABLE = False
    return _POWERSPOOL_AVAILABLE


def get_memory_counters():
    """
    通过 PowerShell Get-Counter 获取全部内存性能计数器。

    返回: {counter_name: value_in_bytes, ...}
    来源: PowerShell Get-Counter \\Memory\\*

    包含: CacheBytes, PoolPagedBytes, PoolNonpagedBytes,
          StandbyCacheReserveBytes, FreeZeroPageListBytes,
          CommittedBytes, CommitLimit, AvailableBytes
    """
    if _SYSTEM != "Windows" or not _check_powershell():
        return {}

    counter_paths = [
        "\\Memory\\Cache Bytes",
        "\\Memory\\Pool Paged Bytes",
        "\\Memory\\Pool Nonpaged Bytes",
        "\\Memory\\Standby Cache Reserve Bytes",
        "\\Memory\\Free & Zero Page List Bytes",
        "\\Memory\\Committed Bytes",
        "\\Memory\\Commit Limit",
        "\\Memory\\Available Bytes",
    ]

    # 构建单条 PowerShell 命令，查询所有计数器
    paths_joined = ", ".join(f'"{p}"' for p in counter_paths)
    cmd = (
        f'Get-Counter -Counter @({paths_joined}) -SampleInterval 1 -MaxSamples 1 '
        f'| Select-Object -ExpandProperty CounterSamples '
        f'| ForEach-Object {{ $_.Path + "=" + $_.CookedValue }}'
    )

    try:
        r = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            return {}

        result = {}
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if "=" not in line:
                continue
            path, val_str = line.split("=", 1)
            try:
                val = float(val_str)
            except ValueError:
                continue

            # 从完整路径中提取计数器名
            # \\computer\memory\cache bytes → CacheBytes
            name_match = re.search(r'\\([^\\]+)$', path)
            if name_match:
                raw_name = name_match.group(1).strip()
                # 转换为驼峰标识符
                key = raw_name.replace(" & ", " ").title().replace(" ", "").replace("/", "Per")
                result[key] = val
        return result
    except subprocess.TimeoutExpired:
        _log.debug("PowerShell Get-Counter 超时")
        return {}
    except Exception as e:
        _log.debug(f"PowerShell Get-Counter 失败: {e}")
        return {}


# ─── WMI 启动项 ─────────────────────────────────────────────────

def get_startup_commands():
    """
    通过 WMI 获取开机自启动程序。

    返回: [{"name": str, "command": str, "location": str, "user": str}, ...]
    来源: wmic Win32_StartupCommand
    """
    if _SYSTEM != "Windows":
        return []
    try:
        r = subprocess.run(
            ["wmic", "path", "Win32_StartupCommand", "get", "Name,Caption,Command,Location,User", "/format:csv"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []

        lines = r.stdout.strip().split("\n")
        if len(lines) < 2:
            return []

        result = []
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split(",", 5)
            if len(parts) >= 6:
                result.append({
                    "name": (parts[1] or "").strip(),     # Caption → Name
                    "caption": (parts[1] or "").strip(),  # Caption
                    "command": (parts[2] or "").strip(),  # Command
                    "location": (parts[3] or "").strip(), # Location
                    "user": (parts[5] or "").strip(),     # User
                })
        return result
    except Exception as e:
        _log.debug(f"WMI 启动项查询失败: {e}")
        return []


# ─── nvidia-smi GPU 信息 ────────────────────────────────────────

# nvidia-smi 可查询字段（已验证在本系统可用）
_NVIDIA_SMI_FIELDS = (
    "index,name,"
    "pcie.link.gen.current,pcie.link.gen.max,"
    "pcie.link.width.current,pcie.link.width.max,"
    "temperature.gpu,"
    "clocks.current.graphics,clocks.current.memory,"
    "clocks.max.graphics,clocks.max.memory,"
    "clocks.current.sm,clocks.max.sm,"
    "power.draw,power.limit,"
    "fan.speed,"
    "vbios_version,"
    "utilization.gpu,utilization.memory,utilization.encoder,utilization.decoder,"
    "pstate,"
    "compute_cap"
)


def get_nvidia_smi():
    """
    通过 nvidia-smi 获取 GPU 深度信息。

    返回: [{
        index, name,
        pcie_gen_current, pcie_gen_max,
        pcie_width_current, pcie_width_max,
        temp_gpu, temp_memory,
        clock_graphics, clock_memory, clock_sm,
        clock_graphics_max, clock_memory_max, clock_sm_max,
        power_draw_w, power_limit_w,
        fan_speed_pct,
        vbios_version,
        util_gpu, util_mem, util_encoder, util_decoder,
        pstate, compute_cap
    }, ...]
    来源: nvidia-smi --query-gpu=...
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + _NVIDIA_SMI_FIELDS, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []

        result = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 22:
                entry = {
                    "index": int(parts[0]) if parts[0].isdigit() else -1,
                    "name": parts[1],

                    # PCIe 链路
                    "pcie_gen_current": _safe_int(parts[2]),
                    "pcie_gen_max": _safe_int(parts[3]),
                    "pcie_width_current": _safe_int(parts[4]),
                    "pcie_width_max": _safe_int(parts[5]),

                    # 温度
                    "temp_gpu": _safe_float(parts[6]),

                    # 时钟
                    "clock_graphics": _safe_float(parts[7]),
                    "clock_memory": _safe_float(parts[8]),
                    "clock_graphics_max": _safe_float(parts[9]),
                    "clock_memory_max": _safe_float(parts[10]),
                    "clock_sm": _safe_float(parts[11]),
                    "clock_sm_max": _safe_float(parts[12]),

                    # 功耗
                    "power_draw_w": _safe_nvidia_val(parts[13]),
                    "power_limit_w": _safe_nvidia_val(parts[14]),

                    # 风扇
                    "fan_speed_pct": _safe_int(parts[15]),

                    # VBIOS
                    "vbios_version": parts[16],

                    # 利用率
                    "util_gpu": _safe_float(parts[17]),
                    "util_mem": _safe_float(parts[18]),
                    "util_encoder": _safe_float(parts[19]),
                    "util_decoder": _safe_float(parts[20]),

                    # 状态
                    "pstate": parts[21],
                    "compute_cap": parts[22] if len(parts) > 22 else None,
                }
                result.append(entry)
        return result
    except FileNotFoundError:
        return []
    except Exception as e:
        _log.debug(f"nvidia-smi 查询失败: {e}")
        return []


def _safe_nvidia_val(val):
    """nvidia-smi 值转换，处理 [N/A] 为 None"""
    if not val or val.strip() == "[N/A]":
        return None
    try:
        return float(val.replace(" MiB", "").replace(" W", "").strip())
    except ValueError:
        return None


# ─── 硬件保留内存 ──────────────────────────────────────────────

def get_hardware_reserved_mb():
    """
    计算硬件保留内存。

    硬件保留 = TotalPhysicalMemory - TotalVisibleMemorySize
    来源: WMI Win32_ComputerSystem / Win32_OperatingSystem
    """
    if _SYSTEM != "Windows":
        return 0
    try:
        import wmi
        c = wmi.WMI()
        total_bytes = None
        visible_kb = None
        for cs in c.Win32_ComputerSystem():
            total_bytes = getattr(cs, 'TotalPhysicalMemory', None)
            if total_bytes:
                total_bytes = int(total_bytes)
            break
        for os_info in c.Win32_OperatingSystem():
            visible_kb = getattr(os_info, 'TotalVisibleMemorySize', None)
            if visible_kb:
                visible_kb = int(visible_kb)
            break
        if total_bytes and visible_kb:
            total_mb = total_bytes / 1024 / 1024
            visible_mb = visible_kb / 1024
            return round(total_mb - visible_mb, 1)
        return None
    except Exception as e:
        _log.debug(f"硬件保留内存计算失败: {e}")
        return 0


# ─── 音频设备信息 ────────────────────────────────────────────────


def get_audio_info():
    """
    通过 Windows Core Audio API (MMDeviceEnumerator) 获取音频设备详情。

    返回: {
        "render": [{"id", "name", "description", "state", "is_default"}, ...],
        "capture": [{"id", "name", "description", "state", "is_default"}, ...],
        "default_render": str or None,
        "default_capture": str or None,
    }
    来源: Core Audio API MMDeviceEnumerator
    """
    result = {"render": [], "capture": [], "default_render": None, "default_capture": None}
    if _SYSTEM != "Windows":
        return result

    try:
        import comtypes
        from comtypes import CLSCTX_ALL, GUID, IUnknown, HRESULT, COMMETHOD, CoCreateInstance
        from ctypes import c_int, c_uint16, c_int16, c_uint32, POINTER, c_wchar_p, c_void_p, Structure, cast

        # ── COM 接口与结构定义 ──────────────────────────────────
        class PROPERTYKEY(Structure):
            _fields_ = [("fmtid", GUID), ("pid", c_uint32)]

        class PROPVARIANT(Structure):
            _fields_ = [
                ("vt", c_uint16), ("wReserved1", c_uint16),
                ("wReserved2", c_uint16), ("wReserved3", c_int16),
                ("pointerVal", c_void_p),
            ]

        # PKEY_Device_FriendlyName
        PKEY_FriendlyName = PROPERTYKEY(
            GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14
        )
        PKEY_DeviceDesc = PROPERTYKEY(
            GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 2
        )

        eRender = 0; eCapture = 1; eConsole = 0
        DEVICE_STATE_ACTIVE = 1; DEVICE_STATE_ALL = 0xF
        VT_LPWSTR = 31

        # 接口桩
        class IMMDeviceCollection(IUnknown):
            _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CCF539BB7B2E}")
        class IMMDevice(IUnknown):
            _iid_ = GUID("{D666063F-1587-4E43-81F1-BB9483F7A9B0}")
        class IPropertyStore(IUnknown):
            _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
        class IMMDeviceEnumerator(IUnknown):
            _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")

        IMMDeviceEnumerator._methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                (["in"], c_int, "dataFlow"), (["in"], c_int, "stateMask"),
                (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                (["in"], c_int, "dataFlow"), (["in"], c_int, "role"),
                (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
        ]
        IMMDeviceCollection._methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "pcDevices")),
            COMMETHOD([], HRESULT, "Item", (["in"], c_int, "nDevice"),
                (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
        ]
        IMMDevice._methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"),
                (["in"], c_int, "dwClsCtx"), (["in"], c_void_p, "pActivationParams"),
                (["out"], POINTER(c_void_p), "ppInterface")),
            COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], c_int, "stgmAccess"),
                (["out"], POINTER(POINTER(IPropertyStore)), "ppProperties")),
            COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p), "ppstrId")),
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "pdwState")),
        ]
        IPropertyStore._methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "cProps")),
            COMMETHOD([], HRESULT, "GetAt", (["in"], c_int, "iProp"),
                (["out"], POINTER(PROPERTYKEY), "pkey")),
            COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                (["out"], POINTER(PROPVARIANT), "pv")),
        ]

        def _get_prop_string(props, pkey):
            """从 IPropertyStore 读取字符串属性"""
            try:
                pv = props.GetValue(pkey)
                if hasattr(pv, 'vt') and pv.vt == VT_LPWSTR and pv.pointerVal:
                    return cast(pv.pointerVal, c_wchar_p).value
            except Exception:
                pass
            return None

        def _describe_endpoint(enumerator, dev, label):
            """获取单个音频端点的详细信息"""
            info = {"id": None, "name": None, "description": None, "state": None, "is_default": False}
            try:
                info["id"] = dev.GetId()
                info["state"] = dev.GetState()
                props = dev.OpenPropertyStore(0)
                if props:
                    info["name"] = _get_prop_string(props, PKEY_FriendlyName)
                    info["description"] = _get_prop_string(props, PKEY_DeviceDesc)
            except Exception:
                pass
            return info

        comtypes.CoInitialize()
        clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        enumerator = CoCreateInstance(clsid, interface=IMMDeviceEnumerator, clsctx=CLSCTX_ALL)

        # 缺省设备 ID
        try:
            default_render = enumerator.GetDefaultAudioEndpoint(eRender, eConsole)
            if default_render:
                result["default_render"] = default_render.GetId()
        except Exception:
            pass
        try:
            default_capture = enumerator.GetDefaultAudioEndpoint(eCapture, eConsole)
            if default_capture:
                result["default_capture"] = default_capture.GetId()
        except Exception:
            pass

        # 枚举播放设备
        try:
            coll = enumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ALL)
            count = coll.GetCount()
            for i in range(count):
                dev = coll.Item(i)
                info = _describe_endpoint(enumerator, dev, "render")
                info["is_default"] = (info["id"] == result["default_render"])
                result["render"].append(info)
        except Exception:
            pass

        # 枚举录音设备
        try:
            coll = enumerator.EnumAudioEndpoints(eCapture, DEVICE_STATE_ALL)
            count = coll.GetCount()
            for i in range(count):
                dev = coll.Item(i)
                info = _describe_endpoint(enumerator, dev, "capture")
                info["is_default"] = (info["id"] == result["default_capture"])
                result["capture"].append(info)
        except Exception:
            pass

        comtypes.CoUninitialize()
    except ImportError:
        _log.debug("comtypes 未安装，音频端点查询不可用")
    except Exception as e:
        _log.debug(f"音频端点查询失败: {e}")

    return result


def get_audio_service_status():
    """
    获取 Windows Audio 服务状态。

    返回: {"name": "Audiosrv", "display_name": str, "status": str, "start_mode": str}
    来源: WMI Win32_Service
    """
    if _SYSTEM != "Windows":
        return None
    try:
        import wmi
        c = wmi.WMI()
        for svc in c.Win32_Service(Name="Audiosrv"):
            return {
                "name": svc.Name,
                "display_name": svc.DisplayName,
                "status": svc.State,
                "start_mode": svc.StartMode,
            }
    except Exception as e:
        _log.debug(f"音频服务查询失败: {e}")
    return None


def _safe_int(val):
    """安全的整数转换"""
    if not val or val.strip() == "[N/A]":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_float(val):
    """安全的浮点数转换"""
    if not val:
        return None
    try:
        return float(val.replace(" MiB", "").replace(" W", "").strip())
    except ValueError:
        return None
