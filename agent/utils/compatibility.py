import sys
import os
import platform
from typing import Tuple, Optional, Dict, Any

REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MIN_MINOR = 8
REQUIRED_PYTHON_MAX_MINOR = 12

SUPPORTED_PLATFORMS = {
    "Windows": ["nt"],
    "Linux": ["posix"],
}

KNOWN_ISSUES: Dict[str, Dict[str, str]] = {
    "Windows": {
        "wmi": "wmi模块仅在Windows平台可用，Linux下会被自动跳过",
        "pythoncom": "pythoncom仅在Windows平台可用",
    },
    "Linux": {
        "pyttsx3": "pyttsx3在Linux下需要安装espeak或其他语音引擎",
        "pygetwindow": "pygetwindow在Linux下功能受限",
    },
}


def get_python_version() -> Tuple[int, int, int]:
    return (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)


def get_python_version_string() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def get_platform() -> str:
    return platform.system()


def get_os_name() -> str:
    return os.name


def is_python_version_compatible() -> bool:
    major, minor, _ = get_python_version()
    return (
        major == REQUIRED_PYTHON_MAJOR
        and REQUIRED_PYTHON_MIN_MINOR <= minor <= REQUIRED_PYTHON_MAX_MINOR
    )


def is_platform_supported() -> bool:
    os_name = get_os_name()
    platform_name = get_platform()
    return os_name in SUPPORTED_PLATFORMS.get(platform_name, [])


def get_python_version_requirement() -> str:
    return f">= 3.{REQUIRED_PYTHON_MIN_MINOR}, < 3.{REQUIRED_PYTHON_MAX_MINOR + 1}"


def check_compatibility() -> Dict[str, Any]:
    python_ver = get_python_version()
    python_str = get_python_version_string()
    platform_name = get_platform()
    os_name = get_os_name()
    
    python_ok = is_python_version_compatible()
    platform_ok = is_platform_supported()
    
    issues = KNOWN_ISSUES.get(platform_name, {})
    
    return {
        "python_version": python_str,
        "python_version_tuple": python_ver,
        "python_compatible": python_ok,
        "platform": platform_name,
        "os_name": os_name,
        "platform_supported": platform_ok,
        "known_issues": issues,
        "recommended_python_versions": get_python_version_requirement(),
        "supported_platforms": list(SUPPORTED_PLATFORMS.keys()),
    }


def assert_python_version() -> None:
    if not is_python_version_compatible():
        raise RuntimeError(
            f"Python版本不兼容。当前版本: {get_python_version_string()}, "
            f"要求版本: {get_python_version_requirement()}"
        )


def assert_platform() -> None:
    if not is_platform_supported():
        raise RuntimeError(
            f"平台不支持。当前平台: {get_platform()} ({get_os_name()}), "
            f"支持的平台: {', '.join(SUPPORTED_PLATFORMS.keys())}"
        )


def import_with_fallback(
    module_name: str, 
    fallback_module: Optional[str] = None,
    fallback_value: Any = None
) -> Any:
    try:
        return __import__(module_name)
    except ImportError:
        if fallback_module:
            return __import__(fallback_module)
        return fallback_value


def get_platform_specific_import(module_name: str, platform_module_map: Dict[str, str]) -> Any:
    current_platform = get_platform()
    if current_platform in platform_module_map:
        module_to_import = platform_module_map[current_platform]
        try:
            return __import__(module_to_import)
        except ImportError:
            pass
    raise ImportError(f"无法导入平台特定模块: {module_name}")


def apply_multiprocess_compat_patch() -> bool:
    """应用层兼容补丁: 为 multiprocess 0.70.x 修复 Python 3.12 shutdown 告警

    Why(背景):
        multiprocess.resource_tracker 的 ``ResourceTracker._stop_locked`` 在
        shutdown 时调用 ``self._lock._recursion_count()``(threading.RLock 私有方法)。
        Python 3.12 中 RLock 返回的是 C 实现的 ``_thread.RLock`` 实例,
        该方法已被移除, 触发 "Exception ignored" + AttributeError 告警。
        site-packages 手工补丁会在 pip 重装 multiprocess 后丢失,
        本函数在应用层整体替换 ``_stop_locked`` 为 getattr 防御的等价实现
        (与原版逻辑一致, 仅递归检查兼容 3.12), 重装后自动兜底生效。

    Returns:
        True=本次注入成功; False=已注入或注入失败(幂等/静默降级)
    """
    try:
        import multiprocess.resource_tracker as _rt
    except Exception:
        return False  # 未安装 multiprocess, 无需补丁
    if getattr(_rt.ResourceTracker, "_yc_compat_patched", False):
        return False  # 已注入, 幂等返回

    def _compat_stop_locked(self):
        # 与 multiprocess.resource_tracker.ResourceTracker._stop_locked 等价,
        # 唯一差异: 递归重入检查用 getattr 防御(Python 3.12 移除了 _recursion_count)
        lock = getattr(self, "_lock", None)
        if lock is not None and getattr(lock, "_recursion_count", lambda: 0)() > 1:
            return self._reentrant_call_error()
        if self._fd is None:
            # not running
            return
        if self._pid is None:
            return
        os.close(self._fd)
        self._fd = None
        os.waitpid(self._pid, 0)
        self._pid = None

    try:
        _rt.ResourceTracker._stop_locked = _compat_stop_locked
        _rt.ResourceTracker._yc_compat_patched = True
        return True
    except Exception:
        return False  # 注入失败静默降级, 不阻断启动


def get_compatibility_report() -> str:
    result = check_compatibility()
    report_lines = [
        "=" * 60,
        "云枢系统兼容性检查报告",
        "=" * 60,
        f"Python 版本: {result['python_version']}",
        f"Python 兼容: {'✓' if result['python_compatible'] else '✗'}",
        f"要求版本: {result['recommended_python_versions']}",
        "",
        f"操作系统: {result['platform']}",
        f"OS 名称: {result['os_name']}",
        f"平台支持: {'✓' if result['platform_supported'] else '✗'}",
        "",
        "已知问题:",
    ]
    
    if result["known_issues"]:
        for issue, desc in result["known_issues"].items():
            report_lines.append(f"  - {issue}: {desc}")
    else:
        report_lines.append("  无")
    
    report_lines.extend([
        "",
        "支持的平台:",
        "  - " + "\n  - ".join(result["supported_platforms"]),
        "=" * 60,
        f"整体状态: {'✓ 兼容' if (result['python_compatible'] and result['platform_supported']) else '✗ 不兼容'}",
        "=" * 60,
    ])
    
    return "\n".join(report_lines)