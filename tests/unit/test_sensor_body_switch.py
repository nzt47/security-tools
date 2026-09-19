"""BodySensor 聚合/开关/标签/分发逻辑测试 —— **不依赖任何真实硬件**。

【为什么能脱离硬件测】
  `sensor/body_sensor.py` 里真正"碰硬件"的只有一件事：`__init__` 调
  `SensorRegistry().discover()`（`body_sensor.py:90-92`）去自动发现并实例化 16 个传感器。
  而**聚合语义本身**（开关、按维度批量切换、标签标注/筛选、collect_all 分发、
  单传感器失败隔离、变更检测旁路、health/summary 组装）全是纯 Python，
  只需一个 `_registry` 字典。

  ⇒ 用 `BodySensor.__new__` 跳过 `__init__`，手工装 `_registry`，
  即可在 Linux CI 与 Windows 本机**同样**执行，且**零副作用**（不起线程、不读硬件）。

【不易】为什么不干脆 mock `SensorRegistry` 后走真 `__init__`：
  真 `__init__` 还会构造 `HardwareBlueprint()/FileBlueprint()/SoftwareBlueprint()`
  （`body_sensor.py:75-77`）并遍历文件系统，**慢且随机器变化**；
  单测要的是"聚合语义"，不是"蓝图能否构建"。蓝图另有归属（见补测计划 P1-7）。
"""
import pytest

from sensor.body_sensor import BodySensor
from sensor.sensor_reading import Category, SensorReading, Severity


# ════════════════════════════════════════════════════════════════════
#  测试替身
# ════════════════════════════════════════════════════════════════════

class _FakeSensor:
    """最小可用传感器替身：只提供 collect()，可配置返回值或抛异常。"""

    def __init__(self, payload=None, raises=None):
        self.payload = payload
        self.raises = raises
        self.calls = 0

    def collect(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.payload


class _FakeChangeDetector:
    """ChangeDetector 替身：记录被调用的方法，便于断言旁路是否真的接通。"""

    def __init__(self, payload=None, raises=None):
        self.payload = payload
        self.raises = raises
        self.hook = "未设置"
        self.baseline_calls = 0
        self.event_calls = []

    def collect(self):
        if self.raises is not None:
            raise self.raises
        return self.payload

    def set_learning_hook(self, hook):
        self.hook = hook

    def set_baseline(self):
        self.baseline_calls += 1
        return {"baseline": True}

    def register_change_from_event(self, event):
        self.event_calls.append(event)


class _FakeTagsModule:
    """tags 模块替身，用于验证 `_load_tags` 的调用方逻辑而不依赖 tags 内部表。"""

    _CATEGORY_TAGS = {Category.CPU: ["硬件感知", "动态运行"],
                      Category.DISK: ["硬件感知", "静态配置"]}

    def __init__(self, raises=False):
        self.raises = raises
        self.seen = []

    def get_tags(self, category, sensor_name):
        if self.raises:
            raise RuntimeError("tags 模块炸了")
        self.seen.append((category, sensor_name))
        return ["来自替身", str(category)]


def _make_body(**overrides):
    """构造一个"已接线但没有硬件"的 BodySensor。

    不调 `__init__` 的全部理由见模块 docstring；这里显式补齐 `__init__` 会设置的
    每一个实例属性，避免测试漏设属性而误过。
    """
    body = BodySensor.__new__(BodySensor)
    body._registry = {}
    body._sensors = {}
    body._tag_module = None
    body._enable_change_detection = False
    body._enable_event_monitor = False
    body._watch_dirs = None
    body._file_event_callback = None
    body._file_include = None
    body._file_exclude = None
    body._lazy_load = True
    body._change_detector_initialized = False
    body._event_monitor_initialized = False
    body._file_watcher_initialized = False
    body.change_detector = None
    body.event_monitor = None
    body.file_watcher = None
    body._sensor_registry = None
    for k, v in overrides.items():
        setattr(body, k, v)
    return body


def _entry(sensor, category=Category.CPU, enabled=True, label="替身"):
    return {"label": label, "sensor": sensor, "category": category,
            "enabled": enabled}


@pytest.fixture
def body():
    return _make_body()


# ════════════════════════════════════════════════════════════════════
#  单控开关
# ════════════════════════════════════════════════════════════════════

class TestBodySensorSwitches:

    def test_register_adds_enabled_entry(self, body):
        body._register("cpu", "CPU（大脑）", _FakeSensor([]), Category.CPU)
        assert body._registry["cpu"]["label"] == "CPU（大脑）"
        assert body._registry["cpu"]["enabled"] is True

    def test_disable_then_enable(self, body):
        body._registry["cpu"] = _entry(_FakeSensor([]))
        body.disable_sensor("cpu")
        assert body.is_enabled("cpu") is False
        body.enable_sensor("cpu")
        assert body.is_enabled("cpu") is True

    def test_switch_ops_on_unknown_name_are_noops(self, body):
        """未知名字必须静默忽略，不能 KeyError —— UI 面板可传任意 name。"""
        body.enable_sensor("no_such")
        body.disable_sensor("no_such")
        body.toggle_sensor("no_such")
        assert body.is_enabled("no_such") is False

    def test_is_enabled_on_unknown_name_is_false(self, body):
        assert body.is_enabled("never-registered") is False

    def test_toggle_flips_state(self, body):
        body._registry["cpu"] = _entry(_FakeSensor([]))
        body.toggle_sensor("cpu")
        assert body._registry["cpu"]["enabled"] is False
        body.toggle_sensor("cpu")
        assert body._registry["cpu"]["enabled"] is True

    def test_set_sensor_delegates_both_ways(self, body):
        body._registry["cpu"] = _entry(_FakeSensor([]))
        body.set_sensor("cpu", False)
        assert body.is_enabled("cpu") is False
        body.set_sensor("cpu", True)
        assert body.is_enabled("cpu") is True

    def test_enable_all_and_disable_all(self, body):
        body._registry = {"cpu": _entry(_FakeSensor([]), enabled=False),
                          "disk": _entry(_FakeSensor([]), enabled=True)}
        body.enable_all()
        assert body.get_switch_status() == {"cpu": True, "disk": True}
        body.disable_all()
        assert body.get_switch_status() == {"cpu": False, "disk": False}

    def test_get_switch_status_is_plain_dict(self, body):
        body._registry["cpu"] = _entry(_FakeSensor([]))
        status = body.get_switch_status()
        assert status == {"cpu": True}
        status["cpu"] = False
        assert body._registry["cpu"]["enabled"] is True  # 返回的是快照，不是内部引用

    def test_get_sensor_info_serializes_enum_and_plain_category(self, body):
        body._registry = {
            "cpu": _entry(_FakeSensor([]), Category.CPU, label="CPU（大脑）"),
            "odd": _entry(_FakeSensor([]), "裸字符串类别", label="怪"),
        }
        info = {i["name"]: i for i in body.get_sensor_info()}
        assert info["cpu"]["category"] == "cpu"
        assert info["cpu"]["label"] == "CPU（大脑）"
        assert info["odd"]["category"] == "裸字符串类别"


# ════════════════════════════════════════════════════════════════════
#  按维度标签批量控制
# ════════════════════════════════════════════════════════════════════

class TestBodySensorTagSwitches:

    def test_load_tags_caches_module(self, body, monkeypatch):
        fake = _FakeTagsModule()
        monkeypatch.setattr(BodySensor, "_load_tags", lambda self: fake)
        assert body._load_tags() is fake

    def test_load_tags_uses_real_tags_module(self, body):
        """不 monkeypatch 时走真实 `from . import tags`，验证真实标签表可用。"""
        tags_mod = body._load_tags()
        assert hasattr(tags_mod, "_CATEGORY_TAGS")
        assert Category.CPU in tags_mod._CATEGORY_TAGS

    def test_get_sensors_by_tag_values_matches_any(self, body):
        body._registry = {"cpu": _entry(_FakeSensor([]), Category.CPU),
                          "disk": _entry(_FakeSensor([]), Category.DISK),
                          "odd": _entry(_FakeSensor([]), "没有标签的类别")}
        body._load_tags = lambda: _FakeTagsModule()
        assert body._get_sensors_by_tag_values(["动态运行"]) == ["cpu"]
        assert set(body._get_sensors_by_tag_values(["硬件感知"])) == {"cpu", "disk"}
        assert body._get_sensors_by_tag_values(["不存在的标签"]) == []

    def test_enable_by_tags_and_disable_by_tags(self, body):
        body._registry = {"cpu": _entry(_FakeSensor([]), Category.CPU, enabled=False),
                          "disk": _entry(_FakeSensor([]), Category.DISK, enabled=False)}
        body._load_tags = lambda: _FakeTagsModule()
        body.enable_by_tags(["动态运行"])
        assert body.get_switch_status() == {"cpu": True, "disk": False}
        body.disable_by_tags(["硬件感知"])
        assert body.get_switch_status() == {"cpu": False, "disk": False}

    def test_set_by_tags_both_directions(self, body):
        body._registry = {"cpu": _entry(_FakeSensor([]), Category.CPU, enabled=False)}
        body._load_tags = lambda: _FakeTagsModule()
        body.set_by_tags(["动态运行"], True)
        assert body.is_enabled("cpu") is True
        body.set_by_tags(["动态运行"], False)
        assert body.is_enabled("cpu") is False


# ════════════════════════════════════════════════════════════════════
#  collect_all：分发、隔离、变更检测旁路
# ════════════════════════════════════════════════════════════════════

class TestBodySensorCollectAll:

    def test_collect_all_flattens_lists_and_singles(self, body):
        r1 = SensorReading("cpu_usage", 10, "%", "CPU", Category.CPU)
        r2 = SensorReading("disk_usage", 50, "%", "磁盘", Category.DISK)
        r3 = SensorReading("mem_usage", 30, "%", "内存", Category.MEMORY)
        body._registry = {
            "cpu": _entry(_FakeSensor([r1, r2])),   # 返回 list ⇒ extend
            "memory": _entry(_FakeSensor(r3)),      # 返回单条 ⇒ append
        }
        out = body.collect_all()
        assert [r.sensor_name for r in out] == ["cpu_usage", "disk_usage", "mem_usage"]

    def test_collect_all_skips_disabled_sensor(self, body):
        sensor = _FakeSensor([SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)])
        body._registry = {"cpu": _entry(sensor, enabled=False)}
        assert body.collect_all() == []
        assert sensor.calls == 0  # 关掉的传感器**不能**被调用

    def test_collect_all_skips_none_sensor(self, body):
        """`filewatch`/`change` 两个占位条目 sensor 为 None（body_sensor.py:124,132）。"""
        body._registry = {"filewatch": _entry(None)}
        assert body.collect_all() == []

    def test_one_sensor_failure_does_not_break_the_rest(self, body):
        """单个传感器抛异常必须被隔离（并发采集时不能让整车感知瘫痪）。"""
        good = _FakeSensor([SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)])
        body._registry = {
            "cpu": _entry(good),
            "disk": _entry(_FakeSensor(raises=RuntimeError("采集炸了"))),
        }
        out = body.collect_all()
        assert [r.sensor_name for r in out] == ["cpu_usage"]

    def test_non_reading_payload_escapes_per_sensor_error_isolation(self, body):
        """✅ **2026-09-19 已修复**：传感器返回非 `SensorReading` 且非 list 时，
        `collect_all` 不再抛 `AttributeError`，而是**归一化为 `SensorReading`**。

        【本条测试的来历 —— 保留以便追溯】
        初版（覆盖率补测时）刻意写成 `pytest.raises(AttributeError, match="tags")`，
        并在 docstring 里注明"本测试锁定现状，是为了让修复这条 bug 时**这里会显式失败**"。
        原始缺陷：`collect_all` 的 `try/except`（`body_sensor.py:510-517`）**只包住
        `sensor.collect()`**，而 `_apply_tags(results)` 在 try 之外，且 `_apply_tags` 自己的
        try **不包住 `r.tags` 属性读取** ⇒ 一个返回裸值的传感器会让**所有其它传感器的
        采集结果一起丢失**，与"单传感器失败隔离"的设计意图相悖。

        【修复内容】
        1. `body_sensor.py` 在**唯一收集点**新增 `_normalize_reading()`：把裸 dict / 裸值
           归一化成 `SensorReading`（使该方法 docstring 声称的 `:returns: SensorReading 列表` 真正成立）；
        2. `_apply_tags()` 改为同时兼容 dict 与对象，且**单条标注失败只影响它自己**。

        实测（本机真实链路）：修复前 `BodySensor().collect_all()` 抛
        `AttributeError: 'dict' object has no attribute 'tags'`、**采集 0 条**；
        修复后采集 **657 条**、654 条带标签、全部为 `SensorReading`。
        """
        body._registry = {"cpu": _entry(_FakeSensor("我是一条裸字符串"))}
        out = body.collect_all()  # 修复前此处抛 AttributeError
        assert all(isinstance(r, SensorReading) for r in out), \
            "裸值必须被 _normalize_reading 归一化成 SensorReading"
        assert any(r.value == "我是一条裸字符串" for r in out), \
            "裸值的原值必须被保留（不得静默丢弃）"

    def test_other_sensors_results_are_lost_when_one_returns_bare_value(self, body):
        """✅ **2026-09-19 已修复**：好传感器的数据**不再**被一个坏传感器连累丢掉。

        原断言为 `pytest.raises(AttributeError)`（锁定"隔离失效"这一缺陷行为）。
        修复后：坏传感器返回的裸值被归一化成 `SensorReading`，**好传感器的读数与标签完整保留**。
        实测本场景：返回 3 条 —— `cpu_usage`（带 8 个标签）+ 归一化后的 `unknown`（原值 `'裸值'`）
        + 变更检测基线（`change_baseline_established`）。
        """
        good = _FakeSensor([SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)])
        body._registry = {
            "cpu": _entry(good),
            "disk": _entry(_FakeSensor("裸值")),
        }
        out = body.collect_all()  # 修复前此处抛 AttributeError
        names = [r.sensor_name for r in out]
        assert "cpu_usage" in names, "好传感器的数据必须存活（这正是修复的核心目标）"
        good_reading = next(r for r in out if r.sensor_name == "cpu_usage")
        assert good_reading.tags, "好传感器的标签必须被正常标注（隔离不再失效）"
        assert all(isinstance(r, SensorReading) for r in out), \
            "包括坏传感器在内的全部条目都必须是 SensorReading"

    def test_collect_all_merges_change_detector_output(self, body):
        body._registry = {"change": _entry(None)}
        body.change_detector = _FakeChangeDetector(
            [SensorReading("hw_changed", 1, "个", "变更", Category.CHANGE)])
        out = body.collect_all()
        assert [r.sensor_name for r in out] == ["hw_changed"]

    def test_collect_all_change_detector_failure_is_isolated(self, body):
        body._registry = {"change": _entry(None)}
        body.change_detector = _FakeChangeDetector(raises=RuntimeError("变更检测炸了"))
        assert body.collect_all() == []

    def test_collect_all_skips_change_when_change_entry_disabled(self, body):
        body._registry = {"change": _entry(None, enabled=False)}
        cd = _FakeChangeDetector([SensorReading("hw_changed", 1, "个", "变更", Category.CHANGE)])
        body.change_detector = cd
        body._change_detector_initialized = True
        assert body.collect_all() == []

    def test_collect_all_applies_filter_tags(self, body):
        r_cpu = SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU,
                              tags=["硬件感知", "动态运行"])
        r_disk = SensorReading("disk_usage", 1, "%", "磁盘", Category.DISK,
                               tags=["硬件感知", "静态配置"])
        body._registry = {"cpu": _entry(_FakeSensor([r_cpu])),
                          "disk": _entry(_FakeSensor([r_disk]))}
        out = body.collect_all(filter_tags="动态运行")
        assert [r.sensor_name for r in out] == ["cpu_usage"]

    def test_collect_all_annotates_tags_when_missing(self, body):
        """未带标签的读数必须被 `_apply_tags` 补上（tags 为空时）。"""
        r = SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)
        assert r.tags == []
        body._registry = {"cpu": _entry(_FakeSensor([r]))}
        out = body.collect_all()
        assert out[0].tags  # 真实 tags 模块已补标签
        assert "硬件感知" in out[0].tags


class TestBodySensorApplyTags:

    def test_apply_tags_uses_real_module(self, body):
        r = SensorReading("cpu_temp", 70, "°C", "CPU温度", Category.CPU)
        body._apply_tags([r])
        assert "硬件感知" in r.tags and "数值量" in r.tags

    def test_apply_tags_does_not_overwrite_existing(self, body):
        r = SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU, tags=["既有标签"])
        body._apply_tags([r])
        assert r.tags == ["既有标签"]

    def test_apply_tags_swallows_module_error(self, body):
        """tags 模块出错只能吞掉（`body_sensor.py:445`），否则整次采集全废。"""
        body._load_tags = lambda: _FakeTagsModule(raises=True)
        r = SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)
        body._apply_tags([r])
        assert r.tags == []


class TestBodySensorFilterByTags:

    def _readings(self):
        return [
            SensorReading("a", 1, "", "A", Category.CPU, tags=["硬件感知", "动态运行"]),
            SensorReading("b", 1, "", "B", Category.DISK, tags=["硬件感知", "静态配置"]),
            SensorReading("c", 1, "", "C", Category.SYSTEM, tags=[]),
        ]

    def test_falsy_filter_returns_all(self):
        readings = self._readings()
        assert BodySensor._filter_by_tags(readings, None) == readings
        assert BodySensor._filter_by_tags(readings, "") == readings
        assert BodySensor._filter_by_tags(readings, []) == readings

    def test_string_filter_is_contains(self):
        out = BodySensor._filter_by_tags(self._readings(), "硬件感知")
        assert [r.sensor_name for r in out] == ["a", "b"]

    def test_list_filter_requires_all_tags(self):
        out = BodySensor._filter_by_tags(self._readings(), ["硬件感知", "动态运行"])
        assert [r.sensor_name for r in out] == ["a"]

    def test_tuple_filter_works_like_list(self):
        out = BodySensor._filter_by_tags(self._readings(), ("硬件感知", "静态配置"))
        assert [r.sensor_name for r in out] == ["b"]

    def test_dict_filter_matches_any_expected_value(self):
        """dict 形态取"任一维度的任一期望值"命中即保留（body_sensor.py:482-487）。"""
        out = BodySensor._filter_by_tags(
            self._readings(), {"目标域": "硬件感知", "动静属性": "静态配置"})
        assert [r.sensor_name for r in out] == ["a", "b"]

    def test_dict_filter_accepts_list_values(self):
        out = BodySensor._filter_by_tags(self._readings(), {"维度": ["静态配置"]})
        assert [r.sensor_name for r in out] == ["b"]

    def test_dict_filter_no_match(self):
        assert BodySensor._filter_by_tags(self._readings(), {"维度": "不存在"}) == []

    def test_unknown_filter_type_returns_input_unchanged(self):
        readings = self._readings()
        assert BodySensor._filter_by_tags(readings, 12345) == readings


# ════════════════════════════════════════════════════════════════════
#  collect_category
# ════════════════════════════════════════════════════════════════════

class TestBodySensorCollectCategory:

    def test_collect_category_with_enum(self, body):
        r = SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)
        body._sensors = {Category.CPU: _FakeSensor([r])}
        assert body.collect_category(Category.CPU) == [r]

    def test_collect_category_with_string_value(self, body):
        r = SensorReading("cpu_usage", 1, "%", "CPU", Category.CPU)
        body._sensors = {Category.CPU: _FakeSensor([r])}
        assert body.collect_category("cpu") == [r]

    def test_collect_category_unknown_returns_empty(self, body):
        body._sensors = {}
        assert body.collect_category(Category.AUDIO) == []


# ════════════════════════════════════════════════════════════════════
#  健康报告 / 摘要
# ════════════════════════════════════════════════════════════════════

class TestBodySensorHealthReport:

    def test_health_report_formats_each_reading(self, body):
        body.collect_quick = lambda: [
            SensorReading("cpu_usage", 42.5, "%", "CPU 使用率", Category.CPU,
                          Severity.WARNING),
        ]
        report = body.get_health_report()
        assert report["cpu_usage"]["值"] == "42.5%"
        assert report["cpu_usage"]["描述"] == "CPU 使用率"
        assert report["cpu_usage"]["严重程度"] == "warning"

    def test_sensor_summary_counts_enabled_and_disabled(self, body):
        body._registry = {"cpu": _entry(_FakeSensor([]), enabled=True, label="CPU"),
                          "disk": _entry(_FakeSensor([]), enabled=False, label="磁盘")}
        body.collect_quick = lambda: []
        summary = body.get_sensor_summary()
        assert summary["total_sensors"] == 2
        assert summary["enabled_sensors"] == 1
        assert summary["disabled_sensors"] == 1
        assert {s["name"] for s in summary["sensors"]} == {"cpu", "disk"}

    def test_sensor_summary_degrades_on_error(self, body):
        """任何异常都必须降级成 `{"total_sensors": 0, "error": ...}`，不能抛。"""
        def _boom():
            raise RuntimeError("信息组装失败")
        body.get_sensor_info = _boom
        summary = body.get_sensor_summary()
        assert summary["total_sensors"] == 0
        assert "信息组装失败" in summary["error"]


# ════════════════════════════════════════════════════════════════════
#  懒加载编排与变更检测旁路
# ════════════════════════════════════════════════════════════════════

class TestBodySensorLazyWiring:

    def test_ensure_change_detector_disabled_does_nothing(self, body):
        body._ensure_change_detector()
        assert body.change_detector is None

    def test_ensure_change_detector_already_initialized_is_noop(self, body):
        body._change_detector_initialized = True
        existing = _FakeChangeDetector()
        body.change_detector = existing
        body._ensure_change_detector()
        assert body.change_detector is existing

    def test_init_change_detector_creates_real_one(self, body):
        """真实 ChangeDetector 构造不碰硬件（sensor/change_detector.py:60 纯逻辑）。"""
        body._init_change_detector()
        assert body._change_detector_initialized is True
        assert body.change_detector is not None
        assert hasattr(body.change_detector, "collect")

    def test_ensure_event_monitor_disabled_does_nothing(self, body):
        body._ensure_event_monitor()
        assert body.event_monitor is None

    def test_ensure_event_monitor_already_initialized_is_noop(self, body):
        body._event_monitor_initialized = True
        existing = object()
        body.event_monitor = existing
        body._ensure_event_monitor()
        assert body.event_monitor is existing

    def test_ensure_file_watcher_without_watch_dirs_does_nothing(self, body):
        """`watch_dirs=None` ⇒ 永不创建 FileWatcher（config.yaml 当前无 sensor.watch_dirs）。"""
        body._ensure_file_watcher()
        assert body.file_watcher is None

    def test_ensure_file_watcher_already_initialized_is_noop(self, body):
        body._file_watcher_initialized = True
        existing = object()
        body.file_watcher = existing
        body._ensure_file_watcher()
        assert body.file_watcher is existing

    def test_initialize_all_is_safe_when_everything_disabled(self, body):
        body.initialize_all()
        assert body.change_detector is None
        assert body.event_monitor is None
        assert body.file_watcher is None

    def test_attach_change_learning_hook_returns_false_without_detector(self, body):
        assert body.attach_change_learning_hook(lambda *a: None) is False

    def test_attach_change_learning_hook_wires_hook(self, body):
        cd = _FakeChangeDetector()
        body.change_detector = cd
        body._change_detector_initialized = True
        hook = lambda *a: None
        assert body.attach_change_learning_hook(hook) is True
        assert cd.hook is hook

    def test_attach_change_learning_hook_swallows_failure(self, body):
        """钩子挂载失败必须只返回 False（TASK-06：不得阻断感知初始化）。"""
        class _Boom(_FakeChangeDetector):
            def set_learning_hook(self, hook):
                raise RuntimeError("钩子炸了")

        body.change_detector = _Boom()
        body._change_detector_initialized = True
        assert body.attach_change_learning_hook(lambda *a: None) is False

    def test_establish_baseline_returns_none_without_detector(self, body):
        assert body.establish_baseline() is None

    def test_establish_baseline_delegates(self, body):
        cd = _FakeChangeDetector()
        body.change_detector = cd
        body._change_detector_initialized = True
        assert body.establish_baseline() == {"baseline": True}
        assert cd.baseline_calls == 1

    def test_on_hardware_event_forwards_to_change_detector(self, body):
        cd = _FakeChangeDetector()
        body.change_detector = cd
        body._enable_change_detection = True
        body._on_hardware_event({"event_type": "usb_insert", "device_name": "鼠标"})
        assert cd.event_calls == [{"event_type": "usb_insert", "device_name": "鼠标"}]

    def test_on_hardware_event_without_detector_is_noop(self, body):
        body._enable_change_detection = True
        body._on_hardware_event({"event_type": "x", "device_name": "y"})  # 不抛即通过

    def test_on_hardware_event_ignored_when_change_detection_off(self, body):
        cd = _FakeChangeDetector()
        body.change_detector = cd
        body._enable_change_detection = False
        body._on_hardware_event({"event_type": "x", "device_name": "y"})
        assert cd.event_calls == []


# ════════════════════════════════════════════════════════════════════
#  collect_quick：psutil 已 mock，可在任何平台跑
# ════════════════════════════════════════════════════════════════════

class TestBodySensorCollectQuick:
    """`collect_quick` 是唯一**直接**读 psutil 的方法（其余走各传感器模块）。

    【变易】monkeypatch `psutil` 的单个函数而不是整个模块：
    这样被测代码路径不变（仍是 `import psutil; psutil.cpu_percent(...)`），
    只把"取值来源"换成确定值 ⇒ **不随机器波动**、CI 与本机同结果。
    """

    def test_quick_collect_returns_cpu_memory_battery(self, monkeypatch):
        import psutil

        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 95.0)
        monkeypatch.setattr(psutil, "virtual_memory",
                            lambda: type("M", (), {"percent": 80.0})())
        monkeypatch.setattr(psutil, "sensors_battery",
                            lambda: type("B", (), {"percent": 5.0})())
        body = _make_body()

        out = {r.sensor_name: r for r in body.collect_quick()}
        assert out["cpu_usage"].value == 95.0
        assert out["cpu_usage"].severity == "critical"      # > 90
        assert out["memory_usage"].severity == "warning"    # > 75
        assert out["battery_percent"].severity == "critical"  # < 10
        # 标签标注在 collect_quick 内部也会执行（body_sensor.py:579）
        assert "硬件感知" in out["cpu_usage"].tags

    def test_quick_collect_normal_thresholds(self, monkeypatch):
        import psutil

        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 10.0)
        monkeypatch.setattr(psutil, "virtual_memory",
                            lambda: type("M", (), {"percent": 20.0})())
        monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
        body = _make_body()

        out = body.collect_quick()
        assert [r.sensor_name for r in out] == ["cpu_usage", "memory_usage"]
        assert all(r.severity == "normal" for r in out)

    def test_quick_collect_warning_band_boundaries(self, monkeypatch):
        """边界：cpu > 70 即 warning；memory > 75 即 warning（`:564,569`）。"""
        import psutil

        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 70.0)
        monkeypatch.setattr(psutil, "virtual_memory",
                            lambda: type("M", (), {"percent": 75.0})())
        monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
        body = _make_body()

        out = {r.sensor_name: r.severity for r in body.collect_quick()}
        assert out["cpu_usage"] == "normal"      # 70 不 > 70
        assert out["memory_usage"] == "normal"   # 75 不 > 75

    def test_quick_collect_swallows_psutil_failure(self, monkeypatch):
        """psutil 炸了必须返回空列表而不是抛（否则 /health 端点会 500）。"""
        import psutil

        def _boom(interval=None):
            raise RuntimeError("psutil 炸了")

        monkeypatch.setattr(psutil, "cpu_percent", _boom)
        body = _make_body()
        assert body.collect_quick() == []
