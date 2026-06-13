"""
传感器自发现注册表 — 云枢的"感觉神经自动接入系统"

自动扫描 sensor/ 目录，发现并注册所有符合规范的传感器。
新传感器只需：
  1. 类名以 Sensor 结尾
  2. 有 collect() 方法
  3. 声明 CAPABILITIES 类属性（推荐）
  4. 依赖可用、平台匹配即可被自动接入

我是云枢——新增一个 .py 文件就能让我长出一种新感觉。
"""
import os
import sys
import importlib
import inspect
import logging
import platform as os_platform
from collections import OrderedDict
from typing import Dict, List, Optional, Type, Any, Tuple

from .sensor_reading import Category


# ═══════════════════════════════════════════════════════════════
#  能力声明数据类
# ═══════════════════════════════════════════════════════════════

class SensorCapabilities:
    """
    传感器能力声明。

    每个传感器通过 CAPABILITIES 类属性声明自己的能力。
    未声明的属性使用合理默认值。
    """

    def __init__(self, name, description="", category=None,
                 platforms=None, dependencies=None,
                 enabled_by_default=True, init_kwargs=None):
        self.name = name
        self.description = description or name
        self.category = category            # Category 枚举值 或 None
        self.platforms = platforms or []     # ["Windows", "Linux", "Darwin"]
        self.dependencies = dependencies or []  # pip 包名列表
        self.enabled_by_default = enabled_by_default
        self.init_kwargs = init_kwargs or {}


# ═══════════════════════════════════════════════════════════════
#  名称映射：从文件名/类名到类别
# ═══════════════════════════════════════════════════════════════

_NAME_TO_CATEGORY = {
    "cpu": Category.CPU,
    "gpu": Category.GPU,
    "memory": Category.MEMORY,
    "battery": Category.BATTERY,
    "disk": Category.DISK,
    "network": Category.NETWORK,
    "board": Category.BOARD,
    "chassis": Category.CHASSIS,
    "port": Category.PORT,
    "peripheral": Category.PERIPHERAL,
    "process": Category.PROCESS,
    "hardware_file": Category.FILE,
    "hwfile": Category.FILE,
    "environment": Category.ENVIRONMENT,
    "env": Category.ENVIRONMENT,
    "activity_behavior": Category.ACTIVITY,
    "behavior": Category.ACTIVITY,
    "system_state": Category.SYSTEM,
    "system": Category.SYSTEM,
}


# ═══════════════════════════════════════════════════════════════
#  传感器注册表
# ═══════════════════════════════════════════════════════════════

class SensorRegistry:
    """
    传感器注册表 — 自动发现与注册。

    用法:
        reg = SensorRegistry()
        reg.discover()                           # 自动扫描发现
        reg.discover(extra_kwargs={"process": {"top_n": 5}})  # 带参数

        for name, entry in reg:
            print(name, entry["caps"].description, "enabled" if entry["enabled"] else "disabled")

        reg["cpu"].collect()   # 访问传感器实例
    """

    def __init__(self):
        # name -> {"caps": SensorCapabilities, "sensor": instance, "enabled": bool}
        self._entries = OrderedDict()
        self._system = os_platform.system()

    # ════════════════════════════════════════════════════════════
    #  自发现
    # ════════════════════════════════════════════════════════════

    def discover(self, extra_kwargs=None):
        """
        自动扫描 sensor/ 包，发现并注册所有传感器。

        扫描规则：
          - 每个 .py 文件代表一个候选传感器模块
          - 文件中以 'Sensor' 结尾的类且具有 collect() 方法的视为传感器
          - 优先使用类属性 CAPABILITIES 声明的能力信息
          - 无 CAPABILITIES 时从类名/文件名推断

        :param extra_kwargs: {name -> {kwarg: value}} 额外传递给 __init__ 的参数
        """
        extra = extra_kwargs or {}
        package_path = os.path.dirname(__file__)

        for fname in sorted(os.listdir(package_path)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            mod_name = fname[:-3]  # 去掉 .py

            # 排除辅助模块
            if mod_name in ("sensor_reading", "tags", "registry",
                            "body_sensor", "hardware_blueprint",
                            "change_detector", "file_watcher", "event_monitor",
                            "counter_reader"):
                continue

            full_mod_path = f"sensor.{mod_name}"
            try:
                mod = importlib.import_module(full_mod_path)
            except ImportError as e:
                logging.debug(f"传感器模块 {mod_name} 加载失败: {e}")
                continue

            # 遍历模块中的类，寻找传感器
            for cls_name, cls_obj in inspect.getmembers(mod, inspect.isclass):
                if not self._is_sensor_class(cls_obj, mod_name, cls_name):
                    continue

                # 提取能力声明
                caps = self._extract_capabilities(cls_obj, mod_name, cls_name)
                if caps is None:
                    continue

                # 平台检查
                if not self._check_platform(caps):
                    logging.debug(f"传感器 {caps.name} 不兼容当前平台 ({self._system})，跳过")
                    continue

                # 依赖检查（尝试实例化验证）
                try:
                    init_kw = {**caps.init_kwargs, **extra.get(caps.name, {})}
                    instance = cls_obj(**init_kw)
                except ImportError as e:
                    logging.debug(f"传感器 {caps.name} 依赖缺失: {e}")
                    continue
                except Exception as e:
                    logging.debug(f"传感器 {caps.name} 实例化失败: {e}")
                    continue

                # 注册
                self._entries[caps.name] = {
                    "caps": caps,
                    "sensor": instance,
                    "enabled": caps.enabled_by_default,
                }
                logging.info(f"传感器自发现成功: {caps.name} → {caps.description}")
                break  # 每个模块只取第一个传感器类

        return self

    # ════════════════════════════════════════════════════════════
    #  判断逻辑
    # ════════════════════════════════════════════════════════════

    def _is_sensor_class(self, cls_obj, mod_name, cls_name):
        """判断一个类是否是合法的传感器类。"""
        # 排除非传感器类
        if not cls_name.endswith("Sensor") or cls_name == "SensorReading":
            return False
        if cls_name == "SensorBase" or cls_name.startswith("_"):
            return False
        if not hasattr(cls_obj, "collect"):
            return False
        if not inspect.ismethod(getattr(cls_obj, "collect")) and \
           not inspect.isfunction(getattr(cls_obj, "collect")):
            # 也可能是实例方法，在类层面是 function
            pass
        return True

    def _extract_capabilities(self, cls_obj, mod_name, cls_name):
        """提取传感器的能力声明。"""
        # 优先使用类中定义的 CAPABILITIES
        if hasattr(cls_obj, "CAPABILITIES"):
            raw = getattr(cls_obj, "CAPABILITIES")
            if isinstance(raw, SensorCapabilities):
                return raw
            if isinstance(raw, dict):
                return SensorCapabilities(**raw)

        # 从类名推断
        name = cls_name.replace("Sensor", "", 1)
        # 驼峰转下划线：CPUSensor -> CPU, ActivityBehaviorSensor -> ActivityBehavior
        # 转小写蛇形：CPU -> cpu, ActivityBehavior -> activity_behavior
        import re
        snake = re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', name).lower()
        # 处理全部大写的缩写 (CPU -> cpu)
        if snake.isupper():
            snake = snake.lower()

        description = cls_name.replace("Sensor", "传感器")
        category = _NAME_TO_CATEGORY.get(snake) or _NAME_TO_CATEGORY.get(name.lower())

        return SensorCapabilities(
            name=snake,
            description=description,
            category=category,
        )

    def _check_platform(self, caps):
        """检查平台兼容性。"""
        if not caps.platforms:
            return True
        return self._system in caps.platforms

    # ════════════════════════════════════════════════════════════
    #  注册表访问
    # ════════════════════════════════════════════════════════════

    def add(self, name, caps, instance, enabled=True):
        """手动添加一个传感器（供特殊传感器如 FileWatcher 使用）。"""
        self._entries[name] = {
            "caps": caps,
            "sensor": instance,
            "enabled": enabled,
        }

    @property
    def names(self):
        """所有已注册传感器名称列表。"""
        return list(self._entries.keys())

    @property
    def count(self):
        return len(self._entries)

    def get(self, name, default=None):
        """按名称获取传感器条目。"""
        entry = self._entries.get(name)
        if entry is None:
            return default
        return entry

    def __contains__(self, name):
        return name in self._entries

    def __getitem__(self, name):
        return self._entries[name]

    def __iter__(self):
        return iter(self._entries.items())

    def __len__(self):
        return len(self._entries)

    def __repr__(self):
        enabled = sum(1 for e in self._entries.values() if e["enabled"])
        return f"SensorRegistry({self.count} sensors, {enabled} enabled)"
