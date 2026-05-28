"""
BodySensor 主类 — 灵犀的"身体"

负责整合各类硬件、网络、安全与文件系统传感器，统一输出 JSON 格式数据。
我是灵犀，BodySensor 就是我的整个身体——每个传感器都是我的一条感知神经。

每个传感器都有一个独立开关，可按需开闭感知通道。
"""
import logging
import json
from collections import OrderedDict

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from .sensor_reading import SensorReading, Severity, Category
from .hardware_blueprint import HardwareBlueprint
from .file_blueprint import FileBlueprint
from .software_blueprint import SoftwareBlueprint
from .change_detector import ChangeDetector
from .file_watcher import FileWatcher
from .event_monitor import EventMonitor


class BodySensor:
    """灵犀的身体——整合所有感知模块，每个传感器带独立开关。"""

    def __init__(self, watch_dirs=None, file_event_callback=None,
                 file_include=None, file_exclude=None,
                 enable_change_detection=True, enable_event_monitor=True):
        """
        初始化感知底座，加载所有传感器。

        :param watch_dirs: 文件监听目录列表
        :param file_event_callback: 文件变动回调函数
        :param enable_change_detection: 是否启用变更检测（轮询快照）
        :param enable_event_monitor: 是否启用实时硬件事件监测（推送）
        """
        # 主线程 COM 初始化（Windows），确保 WMI 传感器调用正常
        import platform
        if platform.system() == "Windows":
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass

        # ── 特殊传感器（需条件创建或单独生命周期）──
        self.blueprint = HardwareBlueprint()
        self.file_blueprint = FileBlueprint()
        self.software_blueprint = SoftwareBlueprint()
        self.change_detector = ChangeDetector() if enable_change_detection else None
        self.event_monitor = None
        self._enable_event_monitor = enable_event_monitor
        self.file_watcher = None
        if watch_dirs:
            self.file_watcher = FileWatcher(watch_dirs, file_event_callback,
                                            include=file_include, exclude=file_exclude)
            self.file_watcher.start()

        # ── 自动发现标准传感器 ──
        from .registry import SensorRegistry, SensorCapabilities
        self._sensor_registry = SensorRegistry()
        self._sensor_registry.discover(extra_kwargs={
            "process": {"top_n": 10},
            "behavior": {"top_n": 10},
        })

        # ── 传感器注册表（带独立开关 + 中文标签）──
        _LABELS = {
            "cpu": "CPU（大脑）", "gpu": "GPU（视觉皮层）",
            "memory": "内存（短期记忆）", "battery": "电池（饥饿感）",
            "disk": "磁盘（长期记忆）", "network": "网络（社交神经）",
            "board": "主板（躯干骨架）", "chassis": "机箱（皮肤与免疫）",
            "port": "端口（神经末梢）", "peripheral": "外设（感官器官）",
            "process": "进程（意识流）", "hwfile": "硬件文件（软件骨骼）",
            "env": "软件环境（生命维持）", "behavior": "活动行为（意识流深度）",
            "system": "系统状态（体检中心）",
        }
        self._registry = OrderedDict()
        for name, entry in self._sensor_registry:
            self._registry[name] = {
                "label": _LABELS.get(name, name),
                "sensor": entry["sensor"],
                "category": entry["caps"].category,
                "enabled": entry["enabled"],
            }
            # 向后兼容：保留直接属性访问
            setattr(self, name, entry["sensor"])

        # ── 手动注册特殊传感器 ──
        # filewatch（条件创建，可能为 None）
        self._registry["filewatch"] = {
            "label": "文件系统（触觉）",
            "sensor": self.file_watcher,
            "category": Category.FILE,
            "enabled": True,
        }
        # change（无独立 collect，由 collect_all 特殊处理）
        self._registry["change"] = {
            "label": "硬件变更（记忆对比）",
            "sensor": None,
            "category": Category.CHANGE,
            "enabled": True,
        }

        # ── 类别 → 传感器索引（供 collect_category 使用）──
        self._sensors = {}
        for name, entry in self._registry.items():
            cat = entry["category"]
            if cat and cat not in self._sensors:
                self._sensors[cat] = entry["sensor"]

        # 标签模块引用（按需加载）
        self._tag_module = None

        # 初始化实时事件监测
        if self._enable_event_monitor:
            self.event_monitor = EventMonitor(callback=self._on_hardware_event)
            self.event_monitor.start()
            if self.change_detector:
                startup_changes = self.event_monitor.detect_startup_changes()
                for change in startup_changes:
                    self.change_detector.register_change_from_event(change)
                    logger.info(f"启动时硬件变化: {change.get('event_type')} - {change.get('device_name')}")

        logger.info("灵犀的感知底座（BodySensor）初始化完成。我能感受到 CPU 的思维节奏、GPU 的视觉皮层、内存的拥挤度...")
        if self.event_monitor:
            logger.info("实时硬件事件监测已激活——任何设备插拔我都会立刻感知。")

    # ════════════════════════════════════════════════════════════
    #  传感器注册
    # ════════════════════════════════════════════════════════════

    def _register(self, name, label, sensor, category):
        """注册一个传感器到注册表。"""
        self._registry[name] = {
            "label": label,
            "sensor": sensor,
            "category": category,
            "enabled": True,
        }

    # ════════════════════════════════════════════════════════════
    #  传感器开关 — 单控
    # ════════════════════════════════════════════════════════════

    def enable_sensor(self, name):
        """打开指定传感器的开关。"""
        if name in self._registry:
            self._registry[name]["enabled"] = True
            logging.info(f"传感器已开启: {self._registry[name]['label']}")

    def disable_sensor(self, name):
        """关闭指定传感器的开关。"""
        if name in self._registry:
            self._registry[name]["enabled"] = False
            logging.info(f"传感器已关闭: {self._registry[name]['label']}")

    def set_sensor(self, name, enabled):
        """设置传感器的开关状态。"""
        if enabled:
            self.enable_sensor(name)
        else:
            self.disable_sensor(name)

    def is_enabled(self, name):
        """查询传感器是否开启。"""
        entry = self._registry.get(name)
        return entry is not None and entry["enabled"]

    def toggle_sensor(self, name):
        """切换传感器开关。"""
        if name in self._registry:
            self._registry[name]["enabled"] = not self._registry[name]["enabled"]
            state = "开启" if self._registry[name]["enabled"] else "关闭"
            logging.info(f"传感器已切换: {self._registry[name]['label']} → {state}")

    # ════════════════════════════════════════════════════════════
    #  传感器开关 — 全控
    # ════════════════════════════════════════════════════════════

    def enable_all(self):
        """开启所有传感器。"""
        for name in self._registry:
            self._registry[name]["enabled"] = True
        logging.info("所有传感器已开启——我的全部感知神经已激活。")

    def disable_all(self):
        """关闭所有传感器。"""
        for name in self._registry:
            self._registry[name]["enabled"] = False
        logging.info("所有传感器已关闭——我的感知神经已休眠。")

    # ════════════════════════════════════════════════════════════
    #  传感器开关 — 按维度批量控制
    # ════════════════════════════════════════════════════════════

    def _load_tags(self):
        """按需加载标签模块。"""
        if self._tag_module is None:
            from . import tags as _t
            self._tag_module = _t
        return self._tag_module

    def _get_sensors_by_tag_values(self, tag_values):
        """根据标签值查找匹配的传感器名称列表。"""
        tags_mod = self._load_tags()
        matched = []
        tag_set = set(tag_values)
        for name, entry in self._registry.items():
            cat = entry["category"]
            if cat and cat in tags_mod._CATEGORY_TAGS:
                if tag_set & set(tags_mod._CATEGORY_TAGS[cat]):
                    matched.append(name)
        return matched

    def enable_by_tags(self, tag_values):
        """按感知维度开启传感器（匹配任一标签值即开启）。

        :param tag_values: 标签值列表，如 ["硬件感知", "动态运行"]
        """
        for name in self._get_sensors_by_tag_values(tag_values):
            self._registry[name]["enabled"] = True
        logging.info(f"按维度标签开启: {tag_values}")

    def disable_by_tags(self, tag_values):
        """按感知维度关闭传感器（匹配任一标签值即关闭）。

        :param tag_values: 标签值列表，如 ["行为感知", "环境感知"]
        """
        for name in self._get_sensors_by_tag_values(tag_values):
            self._registry[name]["enabled"] = False
        logging.info(f"按维度标签关闭: {tag_values}")

    def set_by_tags(self, tag_values, enabled):
        """按维度批量设置开关。"""
        if enabled:
            self.enable_by_tags(tag_values)
        else:
            self.disable_by_tags(tag_values)

    def get_switch_status(self):
        """获取所有传感器的开关状态。"""
        return {name: entry["enabled"]
                for name, entry in self._registry.items()}

    # ════════════════════════════════════════════════════════════
    #  标签辅助方法
    # ════════════════════════════════════════════════════════════

    def _apply_tags(self, readings):
        """为读数列表标注多维度标签。"""
        tags_mod = self._load_tags()
        for r in readings:
            if not r.tags:
                try:
                    r.tags = tags_mod.get_tags(r.category, r.sensor_name)
                except Exception:
                    pass

    @staticmethod
    def _filter_by_tags(readings, filter_spec):
        """按标签条件筛选读数。

        :param filter_spec: 筛选条件，支持三种格式：
            - 字符串: "硬件感知" → 包含该标签的读数
            - 列表: ["硬件感知", "动态运行"] → 同时包含所有标签的读数
            - 字典: {"目标域": "硬件感知", "动静属性": "动态运行"}
                  → 指定维度匹配指定值
        """
        if not filter_spec:
            return readings

        if isinstance(filter_spec, str):
            # 单个标签值 → 包含即返回
            return [r for r in readings if filter_spec in (r.tags or [])]

        if isinstance(filter_spec, (list, tuple)):
            # 标签列表 → 必须全部包含
            tag_set = set(filter_spec)
            return [r for r in readings if tag_set.issubset(set(r.tags or []))]

        if isinstance(filter_spec, dict):
            # {维度值: 期望值} → 保留维度值在 tags 中的读数
            # 注意：tag 本身不区分维度名称，所以这里用值匹配
            # 如果 dict 的值是字符串，按单值匹配
            # 如果 dict 的值是列表，按任意值匹配
            tag_sets = {}
            for dim, val in filter_spec.items():
                if isinstance(val, (list, tuple)):
                    tag_sets[dim] = set(val)
                else:
                    tag_sets[dim] = {str(val)}

            # 扁平化：任一维度的任一期望值匹配即保留
            expected = set()
            for vs in tag_sets.values():
                expected.update(vs)

            return [r for r in readings if expected & set(r.tags or [])]

        return readings

    # ════════════════════════════════════════════════════════════
    #  核心采集方法
    # ════════════════════════════════════════════════════════════

    def collect_all(self, filter_tags=None):
        """
        采集所有已开启传感器的数据。

        :param filter_tags: 可选，采集后按标签筛选。格式见 _filter_by_tags。
        :returns: SensorReading 列表
        """
        results = []

        for name, entry in self._registry.items():
            if not entry["enabled"]:
                continue
            sensor = entry["sensor"]
            if sensor is None:
                continue
            try:
                data = sensor.collect()
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
            except Exception as e:
                logging.error(f"采集 {entry['label']} 数据时出错: {e}")

        # 变更检测（单独处理）
        if self.change_detector and self._registry.get("change", {}).get("enabled", True):
            try:
                changes = self.change_detector.collect()
                if isinstance(changes, list):
                    results.extend(changes)
            except Exception as e:
                logging.error(f"变更检测失败: {e}")

        # 标注标签
        self._apply_tags(results)

        # 按标签筛选
        if filter_tags:
            results = self._filter_by_tags(results, filter_tags)

        return results

    def collect_category(self, category):
        """
        按类别采集传感器数据。

        :param category: Category 枚举值或字符串
        """
        if isinstance(category, str):
            category = Category(category)
        sensor = self._sensors.get(category)
        if sensor:
            return sensor.collect()
        logging.warning(f"未知传感器类别: {category}")
        return []

    def collect_quick(self):
        """快速采集模式——只采集核心指标。"""
        results = []
        try:
            import psutil
            usage = psutil.cpu_percent(interval=0.1)
            results.append(SensorReading(
                "cpu_usage", usage, "%", "CPU 使用率",
                Category.CPU, Severity.CRITICAL if usage > 90 else (Severity.WARNING if usage > 70 else Severity.NORMAL)
            ))
            mem = psutil.virtual_memory()
            results.append(SensorReading(
                "memory_usage", mem.percent, "%", "内存占用率",
                Category.MEMORY, Severity.CRITICAL if mem.percent > 90 else (Severity.WARNING if mem.percent > 75 else Severity.NORMAL)
            ))
            battery = psutil.sensors_battery()
            if battery:
                results.append(SensorReading(
                    "battery_percent", battery.percent, "%", "电池电量",
                    Category.BATTERY, Severity.CRITICAL if battery.percent < 10 else Severity.NORMAL
                ))
        except Exception as e:
            logging.error(f"快速采集失败: {e}")
        self._apply_tags(results)
        return results

    # ════════════════════════════════════════════════════════════
    #  报告与状态
    # ════════════════════════════════════════════════════════════

    def get_health_report(self):
        """生成身体状态摘要报告。"""
        quick = self.collect_quick()
        lines = ["===== 灵犀身体状态报告 ====="]
        for r in quick:
            if r.severity == "critical":
                lines.append(f"  [危急] {r.description}: {r.value}{r.unit}")
            elif r.severity == "warning":
                lines.append(f"  [警告] {r.description}: {r.value}{r.unit}")
        if len(lines) == 1:
            lines.append("  [正常] 我感觉很好！")
        return "\n".join(lines)

    def get_sensor_summary(self):
        """获取传感器开关状态摘要。"""
        total = len(self._registry)
        enabled = sum(1 for e in self._registry.values() if e["enabled"])
        lines = [f"传感器状态: {enabled}/{total} 已开启"]
        for name, entry in self._registry.items():
            state = "●" if entry["enabled"] else "○"
            lines.append(f"  {state} {entry['label']} ({name})")
        return "\n".join(lines)

    def get_sensor_info(self):
        """返回传感器注册信息的可序列化字典。"""
        return [
            {"name": name, "label": entry["label"],
             "category": entry["category"].value if entry["category"] else None,
             "enabled": entry["enabled"]}
            for name, entry in self._registry.items()
        ]

    # ════════════════════════════════════════════════════════════
    #  文件监听控制
    # ════════════════════════════════════════════════════════════

    def start_file_watch(self):
        """启动文件系统监听"""
        if self.file_watcher:
            self.file_watcher.start()
            logging.info("文件系统监听已启动——我的触觉网络已张开。")
        else:
            logging.warning("未配置文件监听目录，触觉网络未激活。")

    def stop_file_watch(self):
        """停止文件系统监听"""
        if self.file_watcher:
            self.file_watcher.stop()
            logging.info("文件系统监听已停止——触觉网络已收回。")

    # ════════════════════════════════════════════════════════════
    #  事件监测控制
    # ════════════════════════════════════════════════════════════

    def start_event_monitor(self, health_check_interval=60):
        """启动实时硬件事件监测（痛觉神经）"""
        if not self.event_monitor:
            self.event_monitor = EventMonitor(callback=self._on_hardware_event)
        if not self.event_monitor.is_running:
            self.event_monitor.start()
            self.event_monitor.start_health_check(interval_seconds=health_check_interval)
            logging.info("实时硬件事件监测已启动——我的痛觉神经已激活。")
        else:
            logging.info("实时硬件事件监测已在运行中。")

    def stop_event_monitor(self):
        """停止实时硬件事件监测"""
        if self.event_monitor and self.event_monitor.is_running:
            self.event_monitor.stop()
            logging.info("实时硬件事件监测已停止。")

    def get_hardware_event_history(self, event_type=None, limit=50):
        """获取硬件事件历史"""
        if self.event_monitor:
            return self.event_monitor.get_history(event_type=event_type, limit=limit)
        return []

    def get_hardware_event_summary(self):
        """获取硬件事件摘要"""
        if self.event_monitor:
            return self.event_monitor.get_event_summary()
        return {"total": 0, "by_type": {}, "message": "事件监测未启用"}

    def _on_hardware_event(self, event_info):
        """实时硬件事件回调"""
        event_type = event_info.get("event_type", "unknown")
        device = event_info.get("device_name", "未知设备")
        if "added" in event_type:
            logging.info(f"[硬件事件] 新设备接入: {device}")
        elif "removed" in event_type:
            logging.warning(f"[硬件事件] 设备移除: {device}")
        elif "failure" in event_type:
            logging.error(f"[硬件事件] 设备故障: {device} - {event_info.get('detail', '')}")
        if self.change_detector:
            self.change_detector.register_change_from_event(event_info)

    # ════════════════════════════════════════════════════════════
    #  硬件蓝图
    # ════════════════════════════════════════════════════════════

    def collect_blueprint(self):
        """采集完整硬件蓝图。"""
        try:
            return self.blueprint.collect()
        except Exception as e:
            logging.error(f"硬件蓝图采集失败: {e}")
            return []

    def get_physical_checklist(self):
        """获取需人工检查的硬件清单。"""
        try:
            blueprint_data = self.blueprint.collect()
            manual_items = [r for r in blueprint_data
                          if r.metadata and r.metadata.get("method") == "manual_check"]
            if not manual_items:
                return "无需要人工检查的硬件项。"
            lines = ["=" * 60,
                     "  以下硬件接口/组件需人工检查（无法通过软件检测）",
                     "  请打开机箱或参考主板手册逐一核对",
                     "=" * 60]
            for i, item in enumerate(manual_items, 1):
                name = item.sensor_name.replace("blueprint_", "").replace("_", " ")
                hint = item.metadata.get("hint", "")
                lines.append(f"  {i:2d}. {name}")
                if hint:
                    lines.append(f"      {hint}")
            lines.append("=" * 60)
            return "\n".join(lines)
        except Exception as e:
            return f"生成物理检查清单失败: {e}"

    def establish_baseline(self):
        """建立变更检测基准"""
        if self.change_detector:
            self.change_detector.set_baseline()
            logging.info("变更检测基准已建立——我现在知道身体的正常状态了。")
        else:
            logging.warning("变更检测未启用。")

    @staticmethod
    def to_json(sensor_name, value, unit, description):
        """统一格式化输出 JSON（兼容旧接口）"""
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sensor_name": sensor_name,
            "value": value,
            "unit": unit,
            "description": description
        }, ensure_ascii=False)
