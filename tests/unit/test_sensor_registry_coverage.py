# -*- coding: utf-8 -*-
"""`sensor/registry.py` 单元补测 —— TASK-09 · P0-3。

为什么这个文件必须用"伪目录 + 伪模块"来测
==========================================
`SensorRegistry.discover()` 会**真的遍历 `sensor/` 并 `import` 每一个模块**，
而其中 `cpu_sensor` / `gpu_sensor` / `memory_sensor` / `board_sensor` / `chassis_sensor` …
的 `__init__` 会触碰 WMI —— 本机实测 WMI 路径**偶发阻塞数十秒**
（`BodySensor().collect_all()` 曾单次超过 120s，见 TASK-09 §2.2）。
⇒ 因此本文件**绝不**对真实目录调用 `discover()`；一律替换
`os.listdir`（仅在参数等于真实 sensor 目录时返回伪文件名，其余调用原样透传）
与 `sensor.registry.importlib`（换成**私有 shim**，不动全局 `importlib`），
在 `types.ModuleType` 上造伪模块。这样 Linux CI 也能覆盖 Windows 相关分支（E7）。

本文件同时锁定三处**已核实的缺陷**（只锁定、不修；按 E6 在 docstring 内登记）：
  · **L-5**：`discover()` 的自发现过滤**不排除 `test_*.py`** ⇒ 生产启动会
    `import sensor.test_body_sensor`（真实目录里确实存在该文件）；
  · `_is_sensor_class` 里那段 `ismethod/isfunction` 判断是**空操作**（`pass`），
    于是 `collect = 5` 这种"非可调用的 collect"也会被判为合法传感器；
  · `_extract_capabilities` 抛异常（如 `CAPABILITIES` 字典含未知键）**不在 `try` 内**
    ⇒ 一个坏传感器的声明会**中断整轮自发现**，后续模块一个都不注册（与 D4 相悖）。

【不易】不使用 `importlib.reload`（重载会让本文件里的类身份与模块内不一致）。
【不易】不对真实 `sensor/` 目录调用 `discover()`；不 import `wmi`。
"""
from __future__ import annotations

import os
import platform
import re
import types

import pytest

import sensor.registry as reg_mod
from sensor.registry import (
    SensorCapabilities,
    SensorRegistry,
    _NAME_TO_CATEGORY,
)
from sensor.sensor_reading import Category

#: 真实 sensor 包目录（`discover` 计算出的 `package_path` 应当就是它）。
SENSOR_DIR = os.path.dirname(os.path.abspath(reg_mod.__file__))

#: `discover` 的辅助模块排除清单（源码 `registry.py:131-134`，共 9 个）。
EXCLUDED_MODULES = (
    "sensor_reading", "tags", "registry", "body_sensor", "hardware_blueprint",
    "change_detector", "file_watcher", "event_monitor", "counter_reader",
)


class _ImportSpy:
    """**私有 shim**：替换 `sensor.registry.importlib` 而不是全局 `importlib`。

    为什么不用 `monkeypatch.setattr(importlib, "import_module", ...)`：
    那会改到全局模块，测试期间 pytest 自身的惰性 import 也会被劫持（假失败风险）。
    这里只替换 `sensor.registry` 的模块级名字，作用域精确。
    """

    def __init__(self, modules: dict):
        self._modules = modules
        self.calls: list[str] = []

    def import_module(self, name):
        self.calls.append(name)
        if name in self._modules:
            return self._modules[name]
        raise ImportError(f"no fake module registered: {name}")


def _sensor_class(cls_name, caps=None, record=None, raises=None, collect=None):
    """造一个伪传感器类（类名必须以 `Sensor` 结尾才可能被认作传感器）。"""
    def _init(self, **kwargs):
        if record is not None:
            record.append(kwargs)
        if raises is not None:
            raise raises

    ns = {"__init__": _init}
    ns["collect"] = collect if collect is not None else (lambda self: [])
    if caps is not None:
        ns["CAPABILITIES"] = caps
    return type(cls_name, (), ns)


def _module(mod_name, classes):
    mod = types.ModuleType(mod_name)
    for cls in classes:
        setattr(mod, cls.__name__, cls)
    return mod


def _install(monkeypatch, files, modules, seen_paths=None):
    """安装伪 `os.listdir` 与伪 `importlib`，返回 `_ImportSpy`。

    `os.listdir` 的替换**只对真实 sensor 目录生效**，其他路径透传给真实实现
    ⇒ 不会干扰 pytest 的临时目录操作。
    """
    real_listdir = os.listdir

    def fake_listdir(path="."):
        if os.path.abspath(str(path)) == SENSOR_DIR:
            if seen_paths is not None:
                seen_paths.append(str(path))
            return list(files)
        return real_listdir(path)

    monkeypatch.setattr(reg_mod.os, "listdir", fake_listdir)
    spy = _ImportSpy(modules)
    monkeypatch.setattr(reg_mod, "importlib", spy)
    return spy


# ═══════════════════════════════════════════════════════════════
#  1. `SensorCapabilities` 数据类
# ═══════════════════════════════════════════════════════════════

class TestSensorCapabilities:
    """能力声明的默认值与"可变默认参数"防线。"""

    def test_defaults(self):
        caps = SensorCapabilities("cpu")
        assert caps.name == "cpu"
        assert caps.description == "cpu"        # 描述缺省回落为**名字**
        assert caps.category is None
        assert caps.platforms == []
        assert caps.dependencies == []
        assert caps.enabled_by_default is True
        assert caps.init_kwargs == {}

    def test_empty_description_falls_back_to_name(self):
        """空串描述也是 falsy ⇒ 回落名字（`description or name`）。"""
        assert SensorCapabilities("gpu", description="").description == "gpu"

    def test_explicit_values_are_kept(self):
        caps = SensorCapabilities(
            "cpu", description="处理器传感器", category=Category.CPU,
            platforms=["Windows", "Linux"], dependencies=["psutil"],
            enabled_by_default=False, init_kwargs={"top_n": 3})
        assert caps.description == "处理器传感器"
        assert caps.category is Category.CPU
        assert caps.platforms == ["Windows", "Linux"]
        assert caps.dependencies == ["psutil"]
        assert caps.enabled_by_default is False
        assert caps.init_kwargs == {"top_n": 3}

    @pytest.mark.parametrize("field", ["platforms", "dependencies", "init_kwargs"])
    def test_mutable_defaults_are_not_shared(self, field):
        """**可变默认参数防线**：两个实例的容器必须是**不同对象**。

        若哪天把签名写成 `platforms=[]` / `init_kwargs={}` 作为**真默认值**，
        所有实例会共享同一个容器 ⇒ 给一个传感器追加依赖会污染全部传感器。
        那种 bug 只在"两个以上实例"时才暴露，故显式回归。
        """
        a = SensorCapabilities("a")
        b = SensorCapabilities("b")
        assert getattr(a, field) is not getattr(b, field)
        assert getattr(a, field) == getattr(b, field)

    def test_explicit_containers_are_aliased(self):
        """显式传入的容器**不做拷贝**（别名语义；调用方后续改动会渗透）。"""
        platforms = ["Windows"]
        caps = SensorCapabilities("x", platforms=platforms)
        assert caps.platforms is platforms

    @pytest.mark.parametrize("field", ["platforms", "dependencies", "init_kwargs"])
    def test_empty_explicit_container_is_replaced(self, field):
        """传空容器 ⇒ 被 `or` 换成**新的**空容器（不共享调用方的对象）。"""
        empty = [] if field != "init_kwargs" else {}
        caps = SensorCapabilities("x", **{field: empty})
        assert getattr(caps, field) == empty
        assert getattr(caps, field) is not empty

    def test_category_accepts_string_too(self):
        """`category` 不做校验/归一（注释说"Category 枚举值 或 None"，但字符串也收）。"""
        assert SensorCapabilities("x", category="cpu").category == "cpu"


# ═══════════════════════════════════════════════════════════════
#  2. `_NAME_TO_CATEGORY` 名称映射
# ═══════════════════════════════════════════════════════════════

class TestNameToCategory:
    """类名/文件名 → 类别 的映射表。"""

    def test_size_and_types(self):
        """恰好 19 条，键全小写，值全是 `Category` 成员。"""
        assert len(_NAME_TO_CATEGORY) == 19
        assert all(k == k.lower() for k in _NAME_TO_CATEGORY)
        assert all(isinstance(v, Category) for v in _NAME_TO_CATEGORY.values())

    def test_every_value_is_a_real_category(self):
        assert set(_NAME_TO_CATEGORY.values()) <= set(Category)

    @pytest.mark.parametrize("key,expected", [
        ("cpu", Category.CPU), ("gpu", Category.GPU), ("memory", Category.MEMORY),
        ("battery", Category.BATTERY), ("disk", Category.DISK),
        ("network", Category.NETWORK), ("board", Category.BOARD),
        ("chassis", Category.CHASSIS), ("port", Category.PORT),
        ("peripheral", Category.PERIPHERAL), ("process", Category.PROCESS),
        ("hardware_file", Category.FILE), ("hwfile", Category.FILE),
        ("environment", Category.ENVIRONMENT), ("env", Category.ENVIRONMENT),
        ("activity_behavior", Category.ACTIVITY), ("behavior", Category.ACTIVITY),
        ("system_state", Category.SYSTEM), ("system", Category.SYSTEM),
    ])
    def test_exact_mappings(self, key, expected):
        """19 条逐条锁定（键名是 `_extract_capabilities` 推断出的蛇形名的取值域）。"""
        assert _NAME_TO_CATEGORY[key] is expected

    def test_fifteen_distinct_categories_are_reachable(self):
        """19 个键只覆盖 **15** 个不同类别（4 组同义词各自指向同一类别）。

        【实测校正】我先按"3 组同义词"估算成 16，实测是 **15**
        （`FILE` 两个键、`ENVIRONMENT` 两个键、`ACTIVITY` 两个键、`SYSTEM` 两个键
        ⇒ 19 - 4 = 15）。再次印证"数出来的数字不能当基线"。
        """
        assert len(set(_NAME_TO_CATEGORY.values())) == 15
        assert len(_NAME_TO_CATEGORY) == 19
        synonyms = [k for k, v in _NAME_TO_CATEGORY.items()
                    if list(_NAME_TO_CATEGORY.values()).count(v) > 1]
        assert sorted(synonyms) == ["activity_behavior", "behavior", "env", "environment",
                                    "hardware_file", "hwfile", "system", "system_state"]

    def test_uncovered_categories(self):
        """有 **3** 个 `Category` 成员**没有**名称映射：`CHANGE` / `DISPLAY` / `AUDIO`。

        `CHANGE` 完全不在此表 ⇒ 任何从类名推断的传感器都不可能被归为 change 类
        （change 类由 `change_detector` 显式声明，而它本身在排除清单里）。
        `DISPLAY`/`AUDIO` 同理：显示与音频信息在 `system_sensor` 里以 `system_*`
        传感器名出现，类别是 `SYSTEM`（见 `sensor/tags.py` 的 `^system_display_` 规则），
        故这两个类别当前**没有任何传感器会归属**。
        记录该覆盖缺口（不是缺陷，但影响"新增传感器自动归类"的预期）。
        """
        missing = set(Category) - set(_NAME_TO_CATEGORY.values())
        assert missing == {Category.CHANGE, Category.DISPLAY, Category.AUDIO}
        assert Category.FILE in set(_NAME_TO_CATEGORY.values())


# ═══════════════════════════════════════════════════════════════
#  3. `_is_sensor_class` 判定
# ═══════════════════════════════════════════════════════════════

class TestIsSensorClass:
    """类是否算传感器：命名规则 + 必须有 `collect`。"""

    def _reg(self):
        return SensorRegistry()

    def test_valid_sensor_class(self):
        assert SensorRegistry()._is_sensor_class(_sensor_class("CPUSensor"), "cpu_sensor", "CPUSensor")

    @pytest.mark.parametrize("cls_name", ["Cpu", "CPUSense", "cpusensor", "SensorX", ""])
    def test_name_must_end_with_sensor(self, cls_name):
        """类名必须以 **`Sensor` 结尾**（大小写敏感：`cpusensor` 不算）。"""
        assert SensorRegistry()._is_sensor_class(_sensor_class(cls_name or "X"), "m", cls_name) is False

    def test_sensor_reading_is_excluded(self):
        """`SensorReading` 是数据类、恰好以 `Sensor` 开头，被**显式**排除。"""
        assert SensorRegistry()._is_sensor_class(_sensor_class("SensorReading"), "m", "SensorReading") is False

    def test_sensor_base_is_excluded(self):
        assert SensorRegistry()._is_sensor_class(_sensor_class("SensorBase"), "m", "SensorBase") is False

    def test_private_class_is_excluded(self):
        """下划线开头的类（如 `_Sensor`）被排除 —— 这是"内部基类"的约定。"""
        assert SensorRegistry()._is_sensor_class(_sensor_class("_HelperSensor"), "m", "_HelperSensor") is False

    def test_class_without_collect_is_excluded(self):
        cls = type("FooSensor", (), {})
        assert SensorRegistry()._is_sensor_class(cls, "m", "FooSensor") is False

    def test_inherited_collect_counts(self):
        """`collect` 定义在父类上也算（`hasattr` 走 MRO）。"""
        base = _sensor_class("BaseSensor")
        child = type("ChildSensor", (base,), {})
        assert SensorRegistry()._is_sensor_class(child, "m", "ChildSensor") is True

    def test_staticmethod_collect_counts(self):
        """`collect` 是 staticmethod 也算（`inspect.isfunction` 为真）。"""
        cls = type("FooSensor", (), {"collect": staticmethod(lambda: [])})
        assert SensorRegistry()._is_sensor_class(cls, "m", "FooSensor") is True

    def test_non_callable_collect_is_still_accepted(self):
        """**缺陷登记（未修）**：`collect` 是**非可调用**值（如 `5`）也判为合法传感器。

        源码里那段判断是：
            if not inspect.ismethod(getattr(cls_obj, "collect")) and \\
               not inspect.isfunction(getattr(cls_obj, "collect")):
                # 也可能是实例方法，在类层面是 function
                pass
        ⇒ `pass` 让这段判断**完全不产生效果**（作者本意应是 `return False`）。
        后果：`collect = 5` 的类会被实例化并注册，直到**第一次调用** `collect()`
        才 `TypeError: 'int' object is not callable` —— 错误从"注册期"推迟到"采集期"。
        本任务禁止改生产代码 ⇒ 锁定现状并在报告 §5 登记。
        """
        cls = type("FooSensor", (), {"collect": 5})
        assert SensorRegistry()._is_sensor_class(cls, "m", "FooSensor") is True

    def test_bare_sensor_class_name(self):
        """类名恰好是 `Sensor` 也合法（既非 `SensorReading` 也非 `SensorBase`）。"""
        assert SensorRegistry()._is_sensor_class(_sensor_class("Sensor"), "m", "Sensor") is True

    def test_non_class_object_with_collect_passes(self):
        """传入**实例**（非类）且带 `collect` ⇒ 也返回 True（函数不做类型校验）。

        `discover` 只会传类进来（`inspect.isclass` 过滤），故线上不可达；
        记录"该函数不校验 `cls_obj` 是不是类"这一事实。
        """
        class Holder:
            def collect(self):
                return []

        assert SensorRegistry()._is_sensor_class(Holder(), "m", "HolderSensor") is True

    def test_mod_name_argument_is_unused(self):
        """`mod_name` 是**未使用**形参（签名保留了它，但函数体从不读）。

        锁定该事实：调用方不应指望"模块名会影响判定"。
        """
        cls = _sensor_class("FooSensor")
        reg = SensorRegistry()
        assert reg._is_sensor_class(cls, "anything", "FooSensor") is True
        assert reg._is_sensor_class(cls, None, "FooSensor") is True


# ═══════════════════════════════════════════════════════════════
#  4. `_extract_capabilities` 能力提取与推断
# ═══════════════════════════════════════════════════════════════

class TestExtractCapabilities:
    """显式声明优先；否则从类名推断。"""

    def test_declared_capabilities_returned_identically(self):
        """`CAPABILITIES` 是 `SensorCapabilities` ⇒ **原对象**返回（不是拷贝）。

        别名含义：同一类的多次注册共享**同一个** caps 对象；
        若某处改了 `caps.enabled_by_default`，会同时影响所有引用它的注册项。
        锁定该语义（`discover` 也把 `caps` 直接放进入口）。
        """
        caps = SensorCapabilities("cpu", description="显式声明")
        cls = _sensor_class("CPUSensor", caps=caps)
        got = SensorRegistry()._extract_capabilities(cls, "cpu_sensor", "CPUSensor")
        assert got is caps

    def test_declared_dict_is_constructed(self):
        """`CAPABILITIES` 是 dict ⇒ `SensorCapabilities(**raw)` 构造。"""
        cls = _sensor_class("CPUSensor", caps={"name": "mycpu", "description": "来自字典",
                                               "platforms": ["Windows"]})
        got = SensorRegistry()._extract_capabilities(cls, "m", "CPUSensor")
        assert isinstance(got, SensorCapabilities)
        assert got.name == "mycpu"
        assert got.description == "来自字典"
        assert got.platforms == ["Windows"]
        assert got.category is None

    def test_declared_dict_defaults_apply(self):
        """dict 形式只给 name ⇒ 其余字段取默认值。"""
        cls = _sensor_class("CPUSensor", caps={"name": "only_name"})
        got = SensorRegistry()._extract_capabilities(cls, "m", "CPUSensor")
        assert got.description == "only_name"
        assert got.enabled_by_default is True
        assert got.init_kwargs == {}

    def test_declared_dict_with_unknown_key_raises_typeerror(self):
        """**缺陷链路（登记）**：`CAPABILITIES` 字典含**未知键** ⇒ `TypeError`。

        该异常发生在 `_extract_capabilities` 内，而 `discover` 调用它时
        **不在任何 try 块里** ⇒ 异常会一路上抛，**中断整轮自发现**：
        排在后面的模块一个都不会被 import/注册。
        这与 `TASK-00 D4`「新模块加载失败必须降级、不得阻塞平台启动」相悖。
        真实触发条件：某个传感器作者把 `CAPABILITIES` 写成含多余键的字典
        （例如把 `version` 顺手写进去）。本任务禁止改生产代码 ⇒
        锁定现状（此处只断言 TypeError），并在 `TestDiscover` 里用端到端用例
        证明"后续模块确实没被注册"，在报告 §5 登记。
        """
        cls = _sensor_class("CPUSensor", caps={"name": "x", "unknown_field": 1})
        with pytest.raises(TypeError):
            SensorRegistry()._extract_capabilities(cls, "m", "CPUSensor")

    @pytest.mark.parametrize("bad", ["cpu", 5, ["cpu"], ("cpu",), 5.5])
    def test_non_dict_non_caps_declaration_is_ignored(self, bad):
        """`CAPABILITIES` 既不是 `SensorCapabilities` 也不是 dict ⇒ **静默忽略**，走推断。

        即 `CAPABILITIES = "cpu"` 这种写法不会报错，也不会生效 ——
        作者会以为声明成功，实际拿到的是从类名推断的能力（类别可能因此不同）。
        锁定该静默行为。
        """
        cls = _sensor_class("CPUSensor", caps=bad)
        got = SensorRegistry()._extract_capabilities(cls, "m", "CPUSensor")
        assert isinstance(got, SensorCapabilities)
        assert got.name == "cpu"                     # 来自**推断**，不是声明
        assert got.category is Category.CPU

    @pytest.mark.parametrize("cls_name,expected_name,expected_cat", [
        ("CPUSensor", "cpu", Category.CPU),
        ("GPUSensor", "gpu", Category.GPU),
        ("MemorySensor", "memory", Category.MEMORY),
        ("BatterySensor", "battery", Category.BATTERY),
        ("DiskSensor", "disk", Category.DISK),
        ("NetworkSensor", "network", Category.NETWORK),
        ("BoardSensor", "board", Category.BOARD),
        ("ChassisSensor", "chassis", Category.CHASSIS),
        ("PortSensor", "port", Category.PORT),
        ("PeripheralSensor", "peripheral", Category.PERIPHERAL),
        ("ProcessSensor", "process", Category.PROCESS),
        ("HardwareFileSensor", "hardware_file", Category.FILE),
        ("EnvironmentSensor", "environment", Category.ENVIRONMENT),
        ("ActivityBehaviorSensor", "activity_behavior", Category.ACTIVITY),
        ("SystemStateSensor", "system_state", Category.SYSTEM),
    ])
    def test_inferred_name_and_category(self, cls_name, expected_name, expected_cat):
        """驼峰 → 蛇形 + 查表（`CPUSensor → cpu`、`ActivityBehaviorSensor → activity_behavior`）。"""
        got = SensorRegistry()._extract_capabilities(_sensor_class(cls_name), "m", cls_name)
        assert got.name == expected_name
        assert got.category is expected_cat
        assert got.description == cls_name.replace("Sensor", "传感器")

    @pytest.mark.parametrize("cls_name,expected_name,expected_cat", [
        ("FooSensor", "foo", None),
        ("ZZZSensor", "zzz", None),
        ("MyCustomThingSensor", "my_custom_thing", None),
        ("Sensor", "", None),
    ])
    def test_unknown_names_have_no_category(self, cls_name, expected_name, expected_cat):
        """不在映射表里的名字 ⇒ `category=None`（不是异常、不是猜一个）。"""
        got = SensorRegistry()._extract_capabilities(_sensor_class(cls_name), "m", cls_name)
        assert got.name == expected_name
        assert got.category is None

    def test_second_lookup_handles_abbreviation(self):
        """**两次查表的分工**：`HWFileSensor` 的蛇形是 `hw_file`（表里没有），
        但 `name.lower()` 是 `hwfile`（表里有）⇒ 命中 `Category.FILE`。

        这是 `_NAME_TO_CATEGORY.get(snake) or _NAME_TO_CATEGORY.get(name.lower())`
        后半段的唯一作用；若删掉后半段，此类传感器会**静默失去类别**。
        注意由此产生的**名实分离**：`name == "hw_file"` 而类别键来自 `"hwfile"`。
        """
        got = SensorRegistry()._extract_capabilities(_sensor_class("HWFileSensor"), "m", "HWFileSensor")
        assert got.name == "hw_file"
        assert got.category is Category.FILE
        assert "hw_file" not in _NAME_TO_CATEGORY

    def test_double_sensor_in_name_is_inconsistent(self):
        """**发现（登记，未修）**：类名含两个 `Sensor` 时，名与描述取自**不同位置**。

          · `name = cls_name.replace("Sensor", "", 1)` —— 只删**第一个** ⇒
            `"SensorHubSensor"` → `"HubSensor"` → 蛇形 `"hub_sensor"`
            （**尾部还留着 `sensor`**，且 `"hub_sensor"` 不在名称映射表里 ⇒ `category=None`）；
          · `description = cls_name.replace("Sensor", "传感器")` —— 替换**全部** ⇒
            `"SensorHubSensor"` → `"传感器Hub传感器"`。
        ⇒ 名字与描述对同一个类名做了**不同口径**的改写；下游若按 description
        反推 name（或反之）会得到不一致的结果。属真实缺陷（两处应统一为"只处理结尾的
        Sensor"）。本任务禁止改生产代码 ⇒ 锁定现状 + 报告 §5 登记。
        """
        got = SensorRegistry()._extract_capabilities(_sensor_class("SensorHubSensor"), "m",
                                                     "SensorHubSensor")
        assert got.name == "hub_sensor"                 # 仅删掉第一个 Sensor
        assert got.name.endswith("sensor")
        assert got.description == "传感器Hub传感器"      # 两个都替换
        assert got.category is None                     # "hub_sensor" 不在映射表里

    def test_all_caps_use_uppercase_abbreviation_branch_is_dead(self):
        """**死代码证据（登记）**：`if snake.isupper(): snake = snake.lower()` **永不成立**。

        上一行已经 `.lower()` 过，因此 `snake.isupper()` 恒为 False（`"".isupper()` 也是
        False）⇒ 该分支及其注释「处理全部大写的缩写 (CPU -> cpu)」是**多余的**：
        `CPU → cpu` 是 `.lower()` 完成的。锁定该事实（行为正确，但注释误导）。
        """
        got = SensorRegistry()._extract_capabilities(_sensor_class("CPUSensor"), "m", "CPUSensor")
        assert got.name == "cpu"
        # 直接证明：对任意推断结果，isupper() 都不可能为 True
        for cls_name in ("CPUSensor", "ABCSensor", "Sensor", "XYZSensor"):
            name = cls_name.replace("Sensor", "", 1)
            snake = re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', name).lower()
            assert snake.isupper() is False


# ═══════════════════════════════════════════════════════════════
#  5. `_check_platform`
# ═══════════════════════════════════════════════════════════════

class TestCheckPlatform:
    """平台兼容性检查（空列表 = 全平台）。"""

    def test_system_comes_from_platform(self):
        assert SensorRegistry()._system == platform.system()

    def test_empty_platforms_means_all(self):
        assert SensorRegistry()._check_platform(SensorCapabilities("x")) is True

    def test_matching_platform(self):
        reg = SensorRegistry()
        assert reg._check_platform(SensorCapabilities("x", platforms=[reg._system])) is True

    def test_non_matching_platform(self):
        reg = SensorRegistry()
        other = "Linux" if reg._system != "Linux" else "Windows"
        assert reg._check_platform(SensorCapabilities("x", platforms=[other])) is False

    def test_membership_is_case_sensitive(self):
        """`in` 是**大小写敏感**的字符串比较 ⇒ 写 `"windows"` 会导致该传感器永不注册。"""
        reg = SensorRegistry()
        assert reg._check_platform(SensorCapabilities("x", platforms=[reg._system.lower()])) is False

    def test_multi_platform_list(self):
        reg = SensorRegistry()
        assert reg._check_platform(
            SensorCapabilities("x", platforms=["Windows", "Linux", "Darwin"])) is True


# ═══════════════════════════════════════════════════════════════
#  6. `discover()`：扫描规则
# ═══════════════════════════════════════════════════════════════

class TestDiscoverScanning:
    """文件名过滤、排序、排除清单、`test_*.py` 漏洞（L-5）。"""

    def test_empty_directory(self, monkeypatch):
        _install(monkeypatch, [], {})
        reg = SensorRegistry().discover()
        assert reg.count == 0
        assert reg.names == []

    def test_returns_self_for_chaining(self, monkeypatch):
        _install(monkeypatch, [], {})
        reg = SensorRegistry()
        assert reg.discover() is reg

    def test_scans_the_real_sensor_directory_path(self, monkeypatch):
        """`discover` 必须扫**真实 `sensor/` 目录**（`os.path.dirname(__file__)`）。"""
        seen = []
        _install(monkeypatch, [], {}, seen_paths=seen)
        SensorRegistry().discover()
        assert seen == [SENSOR_DIR] or (len(seen) >= 1 and os.path.abspath(seen[0]) == SENSOR_DIR)

    @pytest.mark.parametrize("fname", ["readme.txt", "notes.md", "data.json",
                                       "cpu_sensor.pyc", "cpu_sensor.PY", "module"])
    def test_non_python_files_are_skipped(self, monkeypatch, fname):
        spy = _install(monkeypatch, [fname], {})
        SensorRegistry().discover()
        assert spy.calls == []

    @pytest.mark.parametrize("fname", ["__init__.py", "_private.py", "_a_sensor.py"])
    def test_underscore_prefixed_files_are_skipped(self, monkeypatch, fname):
        """下划线开头（含 `__init__.py`）一律跳过 —— 这是排除 `__init__` 的唯一机制。"""
        spy = _install(monkeypatch, [fname], {})
        SensorRegistry().discover()
        assert spy.calls == []

    @pytest.mark.parametrize("mod_name", EXCLUDED_MODULES)
    def test_helper_modules_are_never_imported(self, monkeypatch, mod_name):
        """9 个辅助模块逐一验证**不被 import**（否则会重复注册或循环依赖）。"""
        spy = _install(monkeypatch, [f"{mod_name}.py"], {})
        SensorRegistry().discover()
        assert spy.calls == []

    def test_exclusion_list_is_exactly_nine(self):
        """排除清单恰好 9 个名字（用源码常量核对，避免"我以为排除了"）。"""
        src = open(reg_mod.__file__, encoding="utf-8").read()
        block = src.split("if mod_name in (")[1].split("):")[0]
        listed = tuple(re.findall(r'"([a-z_]+)"', block))
        assert listed == EXCLUDED_MODULES

    def test_test_files_are_not_excluded_L5(self, monkeypatch):
        """**缺陷登记（L-5，未修）**：自发现**不排除 `test_*.py`**。

        `sensor/test_body_sensor.py` 是**真实存在**的文件（见下条断言），
        它以 `test_` 开头但**不以 `_` 开头、也不在排除清单里**
        ⇒ 生产启动时 `discover()` 会执行 `import sensor.test_body_sensor`。
        后果：把测试模块（及其 import 图）拉进生产进程；若该模块在 import 期有副作用，
        会在启动路径上执行。按 `TASK-09 §2.3`，这是 P0-3 明确要求测的点。
        锁定现状：**该模块会被 import**。
        """
        spy = _install(monkeypatch, ["test_body_sensor.py"], {})
        SensorRegistry().discover()
        assert spy.calls == ["sensor.test_body_sensor"]
        assert "test_body_sensor" not in EXCLUDED_MODULES

    def test_real_sensor_dir_really_contains_a_test_module(self):
        """**真实环境证据**：`sensor/test_body_sensor.py` 确实存在于工作树中。

        这条把 L-5 从"理论风险"变成"当前就在发生的路径"（E5：不用夹具冒充生产）。
        """
        assert os.path.isfile(os.path.join(SENSOR_DIR, "test_body_sensor.py"))

    def test_novelty_module_is_scanned(self, monkeypatch):
        """`novelty.py` **不在**排除清单里 ⇒ 会被 import（它无 `Sensor` 类，故不注册）。

        记录该事实：新增"非传感器"辅助模块时若忘了加进排除清单，
        就会被自发现 import 一次（目前无害，只增加启动成本）。
        """
        spy = _install(monkeypatch, ["novelty.py"], {"sensor.novelty": _module("sensor.novelty", [])})
        reg = SensorRegistry().discover()
        assert spy.calls == ["sensor.novelty"]
        assert reg.count == 0

    def test_files_are_processed_in_sorted_order(self, monkeypatch):
        """`sorted(os.listdir(...))` ⇒ 处理顺序与目录返回顺序无关，只按文件名排序。

        可复现性依赖这一点（注册顺序 = 文件名顺序）。
        """
        spy = _install(monkeypatch, ["z_sensor.py", "a_sensor.py", "m_sensor.py"], {})
        SensorRegistry().discover()
        assert spy.calls == ["sensor.a_sensor", "sensor.m_sensor", "sensor.z_sensor"]

    def test_import_error_is_skipped_and_others_continue(self, monkeypatch):
        """**降级**：某模块 `ImportError`（依赖缺失）⇒ 跳过它，**其余继续**。"""
        mod_b = _module("sensor.b_sensor", [_sensor_class("BSensor")])
        spy = _install(monkeypatch, ["a_sensor.py", "b_sensor.py", "c_sensor.py"],
                       {"sensor.b_sensor": mod_b})
        reg = SensorRegistry().discover()
        assert spy.calls == ["sensor.a_sensor", "sensor.b_sensor", "sensor.c_sensor"]
        assert reg.names == ["b"]

    def test_listdir_failure_propagates_OSError(self, monkeypatch):
        """**发现（登记，未修）**：`os.listdir` 抛 `OSError` 会**上抛**，不被捕获。

        `for fname in sorted(os.listdir(package_path))` 在**所有 try 之外** ⇒
        目录不可读（权限/被卸载/路径异常）会直接让 `discover()` 抛异常，
        调用方若未兜底则**感知层整体起不来**。与 `TASK-00 D4`
        「新模块加载失败必须降级、不得阻塞平台启动」相悖。
        本任务禁止改生产代码 ⇒ 锁定现状 + 报告 §5 登记。
        """
        def boom(path="."):
            raise PermissionError("拒绝访问")

        monkeypatch.setattr(reg_mod.os, "listdir", boom)
        with pytest.raises(PermissionError):
            SensorRegistry().discover()


# ═══════════════════════════════════════════════════════════════
#  7. `discover()`：注册语义
# ═══════════════════════════════════════════════════════════════

class TestDiscoverRegistration:
    """成功注册的入口形状、实例化参数、平台/依赖过滤、每个模块只取一个类。"""

    def test_entry_shape(self, monkeypatch):
        """入口字典**恰好 3 个键**：`caps` / `sensor` / `enabled`。"""
        caps = SensorCapabilities("cpu", description="处理器")
        record = []
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps, record=record)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        reg = SensorRegistry().discover()
        assert reg.names == ["cpu"]
        entry = reg["cpu"]
        assert set(entry) == {"caps", "sensor", "enabled"}
        assert entry["caps"] is caps
        assert entry["enabled"] is True
        assert record == [{}]
        assert isinstance(entry["sensor"], mod.CPUSensor)

    def test_enabled_by_default_false(self, monkeypatch):
        """`enabled_by_default=False` ⇒ 注册但 `enabled=False`（可被 UI 选择性开启）。"""
        caps = SensorCapabilities("cpu", enabled_by_default=False)
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        reg = SensorRegistry().discover()
        assert reg["cpu"]["enabled"] is False
        assert reg.count == 1

    def test_init_kwargs_passed_to_constructor(self, monkeypatch):
        """`caps.init_kwargs` 会作为 `**kwargs` 传给传感器构造函数。"""
        caps = SensorCapabilities("cpu", init_kwargs={"top_n": 3, "deep": True})
        record = []
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps, record=record)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        SensorRegistry().discover()
        assert record == [{"top_n": 3, "deep": True}]

    def test_extra_kwargs_merge_and_override(self, monkeypatch):
        """`extra_kwargs[name]` **覆盖**同名 `init_kwargs`，并合并新键。"""
        caps = SensorCapabilities("cpu", init_kwargs={"a": 1, "b": 2})
        record = []
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps, record=record)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        SensorRegistry().discover(extra_kwargs={"cpu": {"b": 9, "c": 3}})
        assert record == [{"a": 1, "b": 9, "c": 3}]

    def test_extra_kwargs_for_unrelated_names_are_ignored(self, monkeypatch):
        """`extra_kwargs` 里不属于已注册传感器的键被忽略（不会误传给别的传感器）。"""
        caps = SensorCapabilities("cpu")
        record = []
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps, record=record)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        SensorRegistry().discover(extra_kwargs={"gpu": {"x": 1}})
        assert record == [{}]

    @pytest.mark.parametrize("extra", [None, {}])
    def test_extra_kwargs_default(self, monkeypatch, extra):
        """`extra_kwargs=None` 与 `{}` 等价（`extra = extra_kwargs or {}`）。"""
        caps = SensorCapabilities("cpu")
        record = []
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps, record=record)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        SensorRegistry().discover(extra_kwargs=extra)
        assert record == [{}]

    def test_platform_mismatch_skips_before_instantiation(self, monkeypatch):
        """平台不匹配 ⇒ 跳过，且**不实例化**（顺序：平台检查在实例化之前）。"""
        other = "Linux" if platform.system() != "Linux" else "Windows"
        caps = SensorCapabilities("cpu", platforms=[other])
        record = []
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps, record=record)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        reg = SensorRegistry().discover()
        assert reg.names == []
        assert record == []

    def test_platform_match_registers(self, monkeypatch):
        caps = SensorCapabilities("cpu", platforms=[platform.system()])
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor", caps=caps)])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        assert SensorRegistry().discover().names == ["cpu"]

    def test_instantiation_importerror_is_skipped(self, monkeypatch):
        """构造函数抛 `ImportError`（依赖缺失）⇒ 跳过（`logging.debug`，不抛）。"""
        caps = SensorCapabilities("cpu")
        mod = _module("sensor.cpu_sensor",
                      [_sensor_class("CPUSensor", caps=caps, raises=ImportError("no psutil"))])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        assert SensorRegistry().discover().names == []

    @pytest.mark.parametrize("exc", [RuntimeError, ValueError, OSError, TypeError])
    def test_instantiation_any_exception_is_skipped(self, monkeypatch, exc):
        """构造函数抛任何 `Exception` ⇒ 跳过（依赖检查兼作"能否构造"探针）。"""
        caps = SensorCapabilities("cpu")
        mod = _module("sensor.cpu_sensor",
                      [_sensor_class("CPUSensor", caps=caps, raises=exc("boom"))])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        assert SensorRegistry().discover().names == []

    def test_one_bad_sensor_does_not_block_others(self, monkeypatch):
        """某个传感器构造失败 ⇒ 其他模块照常注册（单点故障隔离）。"""
        bad = _module("sensor.bad_sensor",
                      [_sensor_class("BadSensor", caps=SensorCapabilities("bad"),
                                     raises=RuntimeError("boom"))])
        good = _module("sensor.good_sensor",
                       [_sensor_class("GoodSensor", caps=SensorCapabilities("good"))])
        _install(monkeypatch, ["bad_sensor.py", "good_sensor.py"],
                 {"sensor.bad_sensor": bad, "sensor.good_sensor": good})
        assert SensorRegistry().discover().names == ["good"]

    def test_only_first_sensor_class_per_module(self, monkeypatch):
        """**每个模块只取第一个**（`inspect.getmembers` 按名字排序 ⇒ 取名字最小的）。"""
        caps_a, caps_z = SensorCapabilities("alpha"), SensorCapabilities("zeta")
        mod = _module("sensor.multi_sensor",
                      [_sensor_class("ASensor", caps=caps_a),
                       _sensor_class("ZSensor", caps=caps_z)])
        _install(monkeypatch, ["multi_sensor.py"], {"sensor.multi_sensor": mod})
        reg = SensorRegistry().discover()
        assert reg.names == ["alpha"]
        assert "zeta" not in reg

    def test_non_sensor_classes_are_skipped_then_sensor_found(self, monkeypatch):
        """模块里排在传感器之前的非传感器类被跳过，**不会**因此漏掉真正的传感器。"""
        helper = type("AaaHelper", (), {})              # 无 collect ⇒ 非传感器
        caps = SensorCapabilities("zeta")
        mod = _module("sensor.multi_sensor",
                      [helper, _sensor_class("ZSensor", caps=caps)])
        _install(monkeypatch, ["multi_sensor.py"], {"sensor.multi_sensor": mod})
        assert SensorRegistry().discover().names == ["zeta"]

    def test_module_with_no_classes(self, monkeypatch):
        """空模块 ⇒ 不注册、不报错。"""
        _install(monkeypatch, ["empty_sensor.py"], {"sensor.empty_sensor": _module("sensor.empty_sensor", [])})
        assert SensorRegistry().discover().names == []

    def test_non_callable_collect_class_is_registered(self, monkeypatch):
        """**缺陷的端到端证据（L 类，未修）**：`collect = 5` 的类会被注册。

        与 `TestIsSensorClass.test_non_callable_collect_is_still_accepted` 配套：
        这里证明后果确实落到注册表上（`_entries` 里出现了一个"调不动的传感器"）。
        """
        caps = SensorCapabilities("broken")
        cls = _sensor_class("BrokenSensor", caps=caps, collect=5)
        del cls.collect
        cls.collect = 5
        mod = _module("sensor.broken_sensor", [cls])
        _install(monkeypatch, ["broken_sensor.py"], {"sensor.broken_sensor": mod})
        reg = SensorRegistry().discover()
        assert reg.names == ["broken"]
        with pytest.raises(TypeError):
            reg["broken"]["sensor"].collect()

    def test_extract_capabilities_returning_none_skips(self, monkeypatch):
        """`_extract_capabilities` 返回 `None` ⇒ 跳过（防御性分支）。"""
        mod = _module("sensor.cpu_sensor", [_sensor_class("CPUSensor")])
        _install(monkeypatch, ["cpu_sensor.py"], {"sensor.cpu_sensor": mod})
        monkeypatch.setattr(SensorRegistry, "_extract_capabilities",
                            lambda self, cls, m, c: None)
        assert SensorRegistry().discover().names == []

    def test_caps_dict_declaration_end_to_end(self, monkeypatch):
        """`CAPABILITIES` 用 dict 声明 ⇒ 端到端生效（名称/平台/初始化参数）。"""
        record = []
        cls = _sensor_class("WhateverSensor",
                            caps={"name": "from_dict", "description": "字典声明",
                                  "init_kwargs": {"k": 1}}, record=record)
        mod = _module("sensor.whatever_sensor", [cls])
        _install(monkeypatch, ["whatever_sensor.py"], {"sensor.whatever_sensor": mod})
        reg = SensorRegistry().discover()
        assert reg.names == ["from_dict"]
        assert reg["from_dict"]["caps"].description == "字典声明"
        assert record == [{"k": 1}]

    def test_name_collision_later_module_overwrites(self, monkeypatch):
        """**发现（登记）**：两个模块声明**同名**传感器 ⇒ 后者**静默覆盖**前者。

        `self._entries[caps.name] = {...}` 是普通 dict 赋值，没有冲突检测、没有告警。
        后果：`count` 少一个、先注册的实例被丢弃（其构造副作用已发生）。
        真实场景：两个模块都用推断名（例如 `util/CPUSensor` 与 `cpu/CPUSensor`）。
        锁定现状 + 报告 §5 登记（建议改为告警或加后缀）。
        """
        first = SensorCapabilities("same", description="第一个")
        second = SensorCapabilities("same", description="第二个")
        mod_a = _module("sensor.a_sensor", [_sensor_class("ASensor", caps=first)])
        mod_b = _module("sensor.b_sensor", [_sensor_class("BSensor", caps=second)])
        _install(monkeypatch, ["a_sensor.py", "b_sensor.py"],
                 {"sensor.a_sensor": mod_a, "sensor.b_sensor": mod_b})
        reg = SensorRegistry().discover()
        assert reg.count == 1
        assert reg["same"]["caps"] is second
        assert reg["same"]["caps"].description == "第二个"

    def test_registration_order_follows_filename_order(self, monkeypatch):
        """注册顺序 = 文件名排序（`names` 与 `__iter__` 都按此顺序）。"""
        mods = {}
        files = []
        for letter in ("c", "a", "b"):
            caps = SensorCapabilities(f"sensor_{letter}")
            name = f"{letter}_sensor"
            mods[f"sensor.{name}"] = _module(f"sensor.{name}",
                                            [_sensor_class(f"{letter.upper()}Sensor", caps=caps)])
            files.append(f"{name}.py")
        _install(monkeypatch, files, mods)
        reg = SensorRegistry().discover()
        assert reg.names == ["sensor_a", "sensor_b", "sensor_c"]

    def test_bad_capabilities_dict_aborts_whole_discovery(self, monkeypatch):
        """**缺陷端到端证据（登记，未修）**：一个坏声明会让**整轮自发现中断**。

        `_extract_capabilities` 抛 `TypeError`（`CAPABILITIES` 字典含未知键）时，
        异常不在 `try` 内 ⇒ 直接冒出 `discover()`；排在 `a_sensor` 之后的
        `b_sensor` / `c_sensor` **一个都不会被 import**。
        这是"一个坏传感器阻断全部传感器接入"的单点故障，与 D4 的降级要求相悖。
        本任务禁止改生产代码 ⇒ 锁定现状并登记；正确修法是把
        `_extract_capabilities`（至少其异常）纳入逐模块的 try/except 降级。
        """
        bad = _module("sensor.a_sensor",
                      [_sensor_class("ASensor", caps={"name": "x", "nope": 1})])
        spy = _install(monkeypatch, ["a_sensor.py", "b_sensor.py", "c_sensor.py"],
                       {"sensor.a_sensor": bad,
                        "sensor.b_sensor": _module("sensor.b_sensor", [_sensor_class("BSensor")]),
                        "sensor.c_sensor": _module("sensor.c_sensor", [_sensor_class("CSensor")])})
        with pytest.raises(TypeError):
            SensorRegistry().discover()
        assert spy.calls == ["sensor.a_sensor"]      # ← b/c 从未被 import

    def test_discover_can_be_called_twice_accumulating(self, monkeypatch):
        """**现状锁定**：重复 `discover()` **不重置** `_entries`（是累积，不是重建）。

        对同一批文件重复调用结果幂等（键相同）；但若两次扫描的文件集不同，
        旧条目会**保留** —— 即 `discover()` 不是"刷新"，而是"并入"。
        调用方若指望它清理已移除的传感器会失望。
        """
        mods = {"sensor.a_sensor": _module("sensor.a_sensor",
                                          [_sensor_class("ASensor", caps=SensorCapabilities("a"))])}
        _install(monkeypatch, ["a_sensor.py"], mods)
        reg = SensorRegistry()
        reg.discover()
        assert reg.names == ["a"]
        # 第二轮目录里没有 a 了，但旧条目仍在
        _install(monkeypatch, ["b_sensor.py"],
                 {"sensor.b_sensor": _module("sensor.b_sensor",
                                             [_sensor_class("BSensor", caps=SensorCapabilities("b"))])})
        reg.discover()
        assert reg.names == ["a", "b"]


class TestDiscoverWindowSensorSwitch:
    """`YUNSHU_DISABLE_WINDOW_SENSOR` 屏蔽开关（与 `app_server` 的开关互为双保险）。"""

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", " 1 ", "True"])
    def test_truthy_values_skip_window_sensor(self, monkeypatch, value):
        """`1/true/yes`（去空白、忽略大小写）⇒ 跳过 `window_sensor` 的 import。"""
        monkeypatch.setenv("YUNSHU_DISABLE_WINDOW_SENSOR", value)
        spy = _install(monkeypatch, ["window_sensor.py", "cpu_sensor.py"],
                       {"sensor.cpu_sensor": _module("sensor.cpu_sensor",
                                                     [_sensor_class("CPUSensor",
                                                                    caps=SensorCapabilities("cpu"))])})
        reg = SensorRegistry().discover()
        assert "sensor.window_sensor" not in spy.calls
        assert spy.calls == ["sensor.cpu_sensor"]
        assert reg.names == ["cpu"]

    @pytest.mark.parametrize("value", ["0", "false", "no", "on", "2", "", "  "])
    def test_falsy_values_do_not_skip(self, monkeypatch, value):
        """其余取值（含 `"on"`、空串、纯空白）⇒ **不**屏蔽。"""
        monkeypatch.setenv("YUNSHU_DISABLE_WINDOW_SENSOR", value)
        mods = {"sensor.window_sensor": _module("sensor.window_sensor", [])}
        spy = _install(monkeypatch, ["window_sensor.py"], mods)
        SensorRegistry().discover()
        assert spy.calls == ["sensor.window_sensor"]

    def test_unset_env_does_not_skip(self, monkeypatch):
        monkeypatch.delenv("YUNSHU_DISABLE_WINDOW_SENSOR", raising=False)
        mods = {"sensor.window_sensor": _module("sensor.window_sensor", [])}
        spy = _install(monkeypatch, ["window_sensor.py"], mods)
        SensorRegistry().discover()
        assert spy.calls == ["sensor.window_sensor"]

    def test_switch_only_affects_window_sensor(self, monkeypatch):
        """开关**只**影响 `window_sensor`，其他模块不受影响。"""
        monkeypatch.setenv("YUNSHU_DISABLE_WINDOW_SENSOR", "1")
        mods = {"sensor.gpu_sensor": _module("sensor.gpu_sensor",
                                             [_sensor_class("GPUSensor",
                                                            caps=SensorCapabilities("gpu"))])}
        spy = _install(monkeypatch, ["window_sensor.py", "gpu_sensor.py"], mods)
        reg = SensorRegistry().discover()
        assert spy.calls == ["sensor.gpu_sensor"]
        assert reg.names == ["gpu"]


# ═══════════════════════════════════════════════════════════════
#  8. 注册表访问接口
# ═══════════════════════════════════════════════════════════════

class TestRegistryAccess:
    """`add` / `get` / 容器协议 / `repr`。"""

    def _reg_with(self, *names):
        reg = SensorRegistry()
        for n in names:
            reg.add(n, SensorCapabilities(n), {"fake": n}, enabled=(n != "off"))
        return reg

    def test_add_default_enabled_true(self):
        reg = SensorRegistry()
        caps = SensorCapabilities("cpu")
        instance = object()
        reg.add("cpu", caps, instance)
        assert set(reg["cpu"]) == {"caps", "sensor", "enabled"}
        assert reg["cpu"]["caps"] is caps
        assert reg["cpu"]["sensor"] is instance
        assert reg["cpu"]["enabled"] is True

    def test_add_explicit_disabled(self):
        reg = SensorRegistry()
        reg.add("cpu", SensorCapabilities("cpu"), object(), enabled=False)
        assert reg["cpu"]["enabled"] is False

    def test_add_overwrites_without_growing(self):
        """同名 `add` ⇒ 覆盖（`count` 不变、旧实例被丢弃）。"""
        reg = SensorRegistry()
        reg.add("cpu", SensorCapabilities("cpu"), "old")
        reg.add("cpu", SensorCapabilities("cpu"), "new")
        assert reg.count == 1
        assert reg["cpu"]["sensor"] == "new"

    def test_add_does_not_consult_caps_name(self):
        """**现状锁定**：`add` 用**传入的 `name`** 作键，忽略 `caps.name`。

        于是 `add("alias", SensorCapabilities("real"), inst)` 会产生
        "键与 `caps.name` 不一致"的条目 ⇒ 下游若按 `caps.name` 查表会找不到。
        `discover` 用的是 `caps.name` 作键，两条路径口径不同。锁定该差异。
        """
        reg = SensorRegistry()
        reg.add("alias", SensorCapabilities("real"), object())
        assert reg.names == ["alias"]
        assert reg["alias"]["caps"].name == "real"

    def test_names_returns_fresh_list(self):
        """`names` 每次返回**新列表** ⇒ 改它不影响注册表。"""
        reg = self._reg_with("a", "b")
        got = reg.names
        got.append("c")
        assert reg.names == ["a", "b"]
        assert got is not reg.names

    def test_names_preserves_insertion_order(self):
        assert self._reg_with("z", "a", "m").names == ["z", "a", "m"]

    def test_count_and_len_agree(self):
        reg = self._reg_with("a", "b", "c")
        assert reg.count == 3
        assert len(reg) == 3

    def test_empty_registry(self):
        reg = SensorRegistry()
        assert reg.count == 0
        assert len(reg) == 0
        assert reg.names == []
        assert list(reg) == []

    def test_get_existing_returns_same_entry(self):
        reg = self._reg_with("a")
        assert reg.get("a") is reg["a"]

    def test_get_missing_returns_none(self):
        assert self._reg_with("a").get("nope") is None

    def test_get_missing_returns_custom_default(self):
        """`get(name, default)` 支持自定义默认值（供调用方区分"缺失"与"None"）。"""
        sentinel = object()
        assert self._reg_with("a").get("nope", sentinel) is sentinel

    def test_getitem_missing_raises_keyerror(self):
        """`reg[name]` 缺失 ⇒ `KeyError`（与 `get` 的宽容形成对照）。"""
        with pytest.raises(KeyError):
            self._reg_with("a")["nope"]

    @pytest.mark.parametrize("present,expected", [("a", True), ("nope", False)])
    def test_contains(self, present, expected):
        assert (present in self._reg_with("a")) is expected

    def test_iter_yields_name_entry_pairs(self):
        """`for name, entry in reg` 是文档示例的用法 ⇒ 必须产出 `(name, entry)` 二元组。"""
        reg = self._reg_with("a", "b")
        pairs = list(reg)
        assert [n for n, _ in pairs] == ["a", "b"]
        assert all(e is reg[n] for n, e in pairs)

    def test_repr_empty(self):
        assert repr(SensorRegistry()) == "SensorRegistry(0 sensors, 0 enabled)"

    def test_repr_all_enabled(self):
        assert repr(self._reg_with("a", "b")) == "SensorRegistry(2 sensors, 2 enabled)"

    def test_repr_mixed_enabled(self):
        """`enabled` 计数只统计 `enabled=True` 的条目。"""
        reg = self._reg_with("a", "b", "off")
        assert reg.count == 3
        assert repr(reg) == "SensorRegistry(3 sensors, 2 enabled)"

    def test_repr_all_disabled(self):
        reg = SensorRegistry()
        reg.add("a", SensorCapabilities("a"), object(), enabled=False)
        assert repr(reg) == "SensorRegistry(1 sensors, 0 enabled)"

    def test_system_attribute_is_platform_system(self):
        assert SensorRegistry()._system == platform.system()

    def test_entries_is_ordered_dict(self):
        """`_entries` 必须是**有序**映射（注册顺序是 `names`/`__iter__` 的语义基础）。"""
        import collections

        assert isinstance(SensorRegistry()._entries, collections.OrderedDict)
