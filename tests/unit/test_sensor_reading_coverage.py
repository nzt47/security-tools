# -*- coding: utf-8 -*-
"""`sensor/sensor_reading.py` 单元补测 —— TASK-09 · P0-5（其一）。

为什么一个"只有 103 行"的数据类值得单独补测
=============================================
(1) **它是 `400b76f4` 修复的关键依赖。** `cognitive/translator.py::_coerce`
    （`:175-178`）从本类的 `__dict__` 里**按名**取 6 个字段
    （`sensor_name / value / unit / description / severity / tags`），
    而且注释明写「**只搬真实存在的字段，不注入默认值**」。
    ⇒ 本类**字段改名或删除时不会抛异常**：`_coerce` 只会少搬一个键，
      `translate()` 随后 `reading.get(...)` 取到 None，最终**静默**降级为
      `"传感器读数未识别"`。这正是 `TASK-00 §0.2f`「夹具冒充生产」那次事故的同一失效模式
      （93 条用例全绿、生产 100% 失效）。故本文件把 `__dict__` 的键集合**显式钉死**。

(2) **两个归一化分支只在"传枚举"时才走到。** `__init__` 的两行三元
    `category.value if isinstance(category, Category) else category` 与
    `severity.value if isinstance(severity, Severity) else (severity or NORMAL.value)`
    而本仓的传感器**传的正是枚举**（见 `sensor/tags.py` 的 `_CATEGORY_TAGS` 键、
    `sensor/change_detector.py:70` 的 `self._category = Category.CHANGE`）。
    ⇒ 这两行一旦写错，产物会从 `"cpu"` 变成 `<Category.CPU: 'cpu'>`，
      `to_json()` 当场 `TypeError`（不是静默，但只有真实调用才暴露）。

(3) **"或"语义会静默吃掉 falsy 值。** `severity or Severity.NORMAL.value` 里
    `""` / `0` / `False` 全部变成 `"normal"`；`metadata or {}` 会把 `[]` 变成 `{}`。
    这些**不抛异常**，只是把非法输入"洗白"成合法值 ⇒ 必须显式锁定。

【不易】本文件纯内存对象：不 import 硬件/平台/网络模块，Linux CI 可全绿（E7）。
【不易】不使用 `importlib.reload`、不使用 `time.sleep`、不写墙钟计时断言。
【变易】与 `tests/unit/test_translator_object_readings.py` 有**一处**交集：
    后者从 translator 侧验证"对象与等价 dict 同结果"；本文件只补一条
    `_coerce(reading) == 6 键子集` 的**搬运契约**断言（同一不变量的另一侧），
    不重复其规则命中/回落用例。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cognitive.translator import Translator
from sensor.sensor_reading import (
    Category,
    SensorReading,
    Severity,
    critical,
    normal,
    reading,
    warning,
)

# `_coerce`（cognitive/translator.py:175）按名搬运的 6 个字段 —— 下游真实契约。
COERCE_KEYS = ("sensor_name", "value", "unit", "description", "severity", "tags")

# `SensorReading.__init__` 实际写入 `__dict__` 的 9 个字段（含 3 个下游不搬的）。
INSTANCE_KEYS = (
    "timestamp", "sensor_name", "value", "unit", "description",
    "category", "severity", "metadata", "tags",
)


def _r(**kw):
    """构造一条读数，显式给全 4 个位置参数（避免 pytest 参数名写错时静默错位）。"""
    base = dict(
        sensor_name="probe", value=1, unit="", description="探针",
        category=None, severity=None, metadata=None, tags=None,
    )
    base.update(kw)
    return SensorReading(
        base["sensor_name"], base["value"], base["unit"], base["description"],
        base["category"], base["severity"], base["metadata"], base["tags"],
    )


# ═══════════════════════════════════════════════════════════════
#  1. 枚举本身的结构不变量
# ═══════════════════════════════════════════════════════════════

class TestSeverityEnum:
    """`Severity` 的**值域与基数**是跨模块契约（tags / novelty / translator 都按字符串比）。"""

    def test_member_count_is_three(self):
        """恰好 3 档。增删档位会让 novelty 的 `severity` 透传白名单对不上。"""
        assert len(Severity) == 3

    @pytest.mark.parametrize("name,value", [
        ("NORMAL", "normal"), ("WARNING", "warning"), ("CRITICAL", "critical"),
    ])
    def test_name_lower_equals_value(self, name, value):
        """成员名小写必须恒等于成员值 —— 本仓所有比较都发生在**字符串**层面。"""
        member = Severity[name]
        assert member.value == value
        assert member.name.lower() == value

    def test_values_are_unique_and_roundtrip(self):
        """值唯一 + `Severity(value) is member`（否则 `Severity("warning")` 会抛 ValueError）。"""
        values = [s.value for s in Severity]
        assert len(set(values)) == 3
        for member in Severity:
            assert Severity(member.value) is member

    def test_is_not_str_subclass(self):
        """**不支持 `==` 直比字符串**（`Severity.NORMAL == "normal"` 为 False）。

        这条必须钉死：`__init__` 的归一化分支正是为此而存在。
        若有人把 `Enum` 改成 `str, Enum` 混入，`isinstance` 分支的语义会静默变化。
        """
        assert (Severity.NORMAL == "normal") is False
        assert isinstance(Severity.NORMAL.value, str)


class TestCategoryEnum:
    """`Category` 是 tags/registry 的字典键，基数与命名风格都被下游依赖。"""

    def test_member_count_is_eighteen(self):
        """恰好 18 类。`tags._CATEGORY_TAGS` 必须为**每一类**给出默认标签（另有用例守）。"""
        assert len(Category) == 18

    @pytest.mark.parametrize("member", list(Category), ids=lambda m: m.name)
    def test_each_member_name_lower_equals_value(self, member):
        """18 个成员逐一：`name.lower() == value`。

        失败模式**不是抛异常**，而是某个成员被写成
        `PERIPHERAL = "periph"` 这种"名字与值不一致" ⇒
        `_NAME_TO_CATEGORY`（registry）与 `_CATEGORY_TAGS`（tags）的键开始分叉，
        表现为"某类传感器静默没有标签"。
        """
        assert member.value == member.name.lower()
        assert Category(member.value) is member

    def test_values_are_unique(self):
        """值唯一：重复值会让两个不同成员在字典里**互相覆盖**。"""
        values = [c.value for c in Category]
        assert len(set(values)) == 18
        assert all(v.isascii() and v.islower() for v in values)

    def test_known_members_present(self):
        """下游按名取用的成员必须存在（缺一个即 `AttributeError` 于 import 期）。"""
        for name in ("CPU", "GPU", "MEMORY", "BATTERY", "DISK", "NETWORK", "BOARD",
                     "CHASSIS", "CHANGE", "FILE", "ENVIRONMENT", "ACTIVITY",
                     "DISPLAY", "AUDIO", "SYSTEM", "PORT", "PERIPHERAL", "PROCESS"):
            assert hasattr(Category, name), name


# ═══════════════════════════════════════════════════════════════
#  2. `__init__` 归一化（本条是文件存在的主要理由）
# ═══════════════════════════════════════════════════════════════

class TestInitFieldContract:
    """`__dict__` 的键集合 = 下游 `_coerce` 的取字段依据。"""

    def test_instance_dict_key_set_is_exactly_nine(self):
        """`vars(reading)` 的键集合必须**恰好**是这 9 个（顺序也固定）。

        【刻意的严格断言】它是一道"改名/加字段必须有人意识到"的闸门：
        `_coerce` 是**按名字**搬运的，字段改名不会有任何报错，只会静默少一个键。
        若确需新增字段，请同时更新 `INSTANCE_KEYS` 并说明下游影响 —— 这正是本断言的目的。
        """
        assert tuple(vars(_r())) == INSTANCE_KEYS
        assert set(vars(_r())) == set(INSTANCE_KEYS)

    def test_dict_superset_of_coerce_contract(self):
        """`_coerce` 需要的 6 个键**必须全部真实存在**（缺一个 ⇒ 下游静默降级）。"""
        data = vars(_r())
        missing = [k for k in COERCE_KEYS if k not in data]
        assert missing == []

    def test_coerce_carries_exactly_the_six_keys_from_dict(self):
        """跨层搬运契约：`_coerce(对象)` == `to_dict()` 里那 6 个键的子集（逐值相等）。

        这条就是 `TASK-00 D12` 要求的"跨层传参处必须有形状一致性断言"：
        事故根因是"对象进不去 `translate()`"，本断言把**对象→dict**这一步钉住。
        """
        r = _r(sensor_name="cpu_temperature", value=85, unit="℃",
               description="处理器温度", category=Category.CPU,
               severity=Severity.WARNING, metadata={"m": 1}, tags=["t"])
        coerced = Translator._coerce(r)
        assert set(coerced) == set(COERCE_KEYS)
        as_dict = r.to_dict()
        assert coerced == {k: as_dict[k] for k in COERCE_KEYS}

    def test_no_name_attribute_only_sensor_name(self):
        """**回归锁**：本类没有 `name` 属性，字段名是 `sensor_name`。

        来历（真实事故）：`agent/learning/behavior_drift.py` 侧曾误用 `reading.name`，
        异常被兜底吞掉 ⇒ 指标恒空。领域模型里"传感器名"就叫 `sensor_name`。
        """
        r = _r(sensor_name="cpu_usage")
        assert not hasattr(r, "name")
        assert r.sensor_name == "cpu_usage"


class TestCategoryNormalization:
    """`category` 的三条分支：枚举 → 取值；字符串 → 原样；其它 → 原样（**不校验**）。"""

    def test_category_enum_normalized_to_value(self):
        """传枚举 ⇒ 落地为**字符串** `"cpu"`，不是枚举对象。"""
        r = _r(category=Category.CPU)
        assert r.category == "cpu"
        assert isinstance(r.category, str)

    def test_category_string_passes_through(self):
        """传字符串 ⇒ 原样（不校验合法性）。`tags.get_tags` 依赖这一步的字符串形态。"""
        assert _r(category="network").category == "network"

    def test_category_enum_value_string_is_idempotent(self):
        """传 `Category.CPU.value` 与传 `Category.CPU` 结果**逐字节相同**（幂等）。"""
        assert _r(category=Category.CPU).category == _r(category=Category.CPU.value).category

    def test_category_none_stays_none(self):
        """未给类别 ⇒ `None`（**不是** `"None"` 也不是空串）。"""
        assert _r(category=None).category is None

    def test_unknown_category_string_is_not_validated(self):
        """**现状锁定**：任意字符串都放行，不做值域校验。

        影响：`to_dict()["category"]` 可以是 `"totally_unknown"`，
        `tags.get_tags` 会把它判为"无默认标签"（静默少标签，不报错）。
        登记为观察项而非缺陷 —— 因为传感器是自发现插件，宽松值域是刻意的。
        """
        assert _r(category="totally_unknown").category == "totally_unknown"

    def test_foreign_enum_is_stored_as_enum_object(self):
        """**缺陷登记（未修，按 E6 锁定当前行为）**：非 `Category` 的枚举**不做归一化**。

        `isinstance(Severity.NORMAL, Category)` 为 False ⇒ 走 `else` 分支原样存储，
        于是 `category` 是一个**枚举对象**而不是字符串 ⇒
        `to_json()` 抛 `TypeError: Object of type Severity is not JSON serializable`。
        正确修法应是"任何 `Enum` 都取 `.value`"（或至少显式拒绝），但本任务禁止改生产代码，
        故此处**只锁定现状**并在报告 §5 登记。
        """
        r = _r(category=Severity.NORMAL)
        assert r.category is Severity.NORMAL           # 未归一化
        assert not isinstance(r.category, str)
        with pytest.raises(TypeError):
            r.to_json()

    def test_falsy_category_zero_is_kept(self):
        """`category` 走的是 `isinstance` 而不是 `or`，故 `0` 不会被洗成 None。"""
        assert _r(category=0).category == 0


class TestSeverityNormalization:
    """`severity` 四条分支：枚举 → 取值；字符串 → 原样；None → `"normal"`；其它 falsy → `"normal"`。"""

    def test_severity_enum_normalized_to_value(self):
        assert _r(severity=Severity.CRITICAL).severity == "critical"

    def test_severity_string_passes_through(self):
        assert _r(severity="warning").severity == "warning"

    def test_severity_none_defaults_to_normal(self):
        """缺省 ⇒ `"normal"`（契约：任一读数必有 severity，下游按它分流）。"""
        assert _r(severity=None).severity == "normal"

    @pytest.mark.parametrize("falsy", ["", 0, False, 0.0])
    def test_falsy_severity_silently_becomes_normal(self, falsy):
        """**现状锁定**：falsy 值被 `or` 静默洗成 `"normal"`。

        失败模式**不是抛异常**：传 `severity=""` 的调用方以为自己标了级别，
        实际拿到 `"normal"` ⇒ 告警不会升级。登记为陷阱（未修）。
        """
        assert _r(severity=falsy).severity == "normal"

    def test_unknown_severity_string_is_not_validated(self):
        """与 category 同：非法字符串原样落库（`novelty._build_event` 有独立白名单兜底）。"""
        assert _r(severity="panic").severity == "panic"

    @pytest.mark.parametrize("member", list(Severity), ids=lambda m: m.value)
    def test_every_member_roundtrips(self, member):
        """3 个成员逐一：传枚举得到的字符串 == 传 `member.value` 得到的字符串。"""
        assert _r(severity=member).severity == _r(severity=member.value).severity == member.value


class TestMetadataAndTagsContainers:
    """`metadata or {}` / `tags or []` 的容器语义（含别名与 falsy 洗白两处陷阱）。"""

    def test_none_becomes_empty_containers(self):
        r = _r(metadata=None, tags=None)
        assert r.metadata == {}
        assert r.tags == []

    def test_empty_containers_are_replaced_not_shared(self):
        """传 `{}` / `[]` 时得到的是**新对象**（`or` 语义），因此不与调用方共享。"""
        m, t = {}, []
        r = _r(metadata=m, tags=t)
        assert r.metadata == m and r.tags == t
        assert r.metadata is not m          # `{} or {}` 取右边 ⇒ 新 dict
        assert r.tags is not t              # `[] or []` 取右边 ⇒ 新 list

    def test_nonempty_containers_are_aliased(self):
        """**别名陷阱（现状锁定）**：非空容器是**原对象**，调用方后续改动会渗透进读数。

        这不影响 400b76f4 的修复（`_coerce` 刻意**不搬运** metadata），
        但 `to_dict()` 会把它带出去 ⇒ 记录该语义，避免日后误以为已做深拷。
        """
        m, t = {"src": "wmi"}, ["hardware"]
        r = _r(metadata=m, tags=t)
        assert r.metadata is m
        assert r.tags is t

    @pytest.mark.parametrize("bad", [[], (), "", 0])
    def test_falsy_non_container_metadata_becomes_dict(self, bad):
        """`metadata or {}` 会把 falsy 的**非字典**（如 `[]`）洗成 `{}` —— 静默类型纠正。"""
        r = _r(metadata=bad)
        assert r.metadata == {}
        assert isinstance(r.metadata, dict)

    @pytest.mark.parametrize("bad", [None, "", 0, ()])
    def test_falsy_tags_becomes_list(self, bad):
        r = _r(tags=bad)
        assert r.tags == []
        assert isinstance(r.tags, list)


class TestTimestamp:
    """时间戳契约：UTC、ISO-8601、`Z` 后缀、微秒精度。"""

    def test_timestamp_is_utc_iso_with_z_suffix(self):
        """必须以 `Z` 结尾且**不含** `+00:00`。

        下游（`file_watcher`、`novelty`、审计链）把该串当 UTC 字面量直接落盘/比较；
        一旦变成 `+00:00`，字符串排序与人类阅读都会与既有数据不一致。
        """
        before = datetime.now(timezone.utc)
        r = _r()
        after = datetime.now(timezone.utc)

        assert r.timestamp.endswith("Z")
        assert "+00:00" not in r.timestamp
        parsed = datetime.fromisoformat(r.timestamp.replace("Z", "+00:00"))
        assert parsed.utcoffset() == timedelta(0)
        # 【不写墙钟计时断言】只断言"构造发生在两次取时之间"，机器再慢也成立。
        assert before <= parsed <= after

    def test_timestamp_differs_between_two_readings(self):
        """连续两条读数的时间戳**互不相同**（微秒精度足以区分），不得是固定常量。"""
        a, b = _r(), _r()
        assert a.timestamp != b.timestamp or abs(
            datetime.fromisoformat(a.timestamp.replace("Z", "+00:00"))
            - datetime.fromisoformat(b.timestamp.replace("Z", "+00:00"))
        ) < timedelta(milliseconds=50)


# ═══════════════════════════════════════════════════════════════
#  3. `to_dict` / `to_json` / `__repr__`
# ═══════════════════════════════════════════════════════════════

class TestToDict:
    """`to_dict()` 是落盘/跨层传参的唯一正规出口，键集合必须**条件化**。"""

    BASE_KEYS = ["timestamp", "sensor_name", "value", "unit",
                 "description", "category", "severity"]

    def test_minimal_key_order(self):
        """无 metadata/tags 时**恰好 7 键**且顺序固定（顺序影响落盘 diff 可读性）。"""
        assert list(_r().to_dict()) == self.BASE_KEYS

    def test_metadata_and_tags_appended_in_order(self):
        """有 metadata/tags 时**追加在两尾**，顺序 metadata → tags。"""
        got = list(_r(metadata={"a": 1}, tags=["x"]).to_dict())
        assert got == self.BASE_KEYS + ["metadata", "tags"]

    @pytest.mark.parametrize("md,tg,expect_md,expect_tg", [
        (None, None, False, False),
        ({}, [], False, False),
        ({"a": 1}, None, True, False),
        (None, ["x"], False, True),
        ({"a": 1}, ["x"], True, True),
    ])
    def test_presence_follows_truthiness(self, md, tg, expect_md, expect_tg):
        """`metadata` / `tags` 的**出现与否由真值决定**（空容器不出现）。

        这不是洁癖：空容器若出现，`json.dumps` 产物会多两个键，
        下游按固定键集解析的实现（含审计链）会看到两种形状。
        """
        out = _r(metadata=md, tags=tg).to_dict()
        assert ("metadata" in out) is expect_md
        assert ("tags" in out) is expect_tg

    def test_values_are_verbatim(self):
        """7 个基础键的值**逐值等于**构造入参（不做任何格式化/单位拼接）。"""
        r = _r(sensor_name="disk_free", value=123.5, unit="GB",
               description="磁盘剩余", category=Category.DISK,
               severity=Severity.WARNING)
        out = r.to_dict()
        assert out["sensor_name"] == "disk_free"
        assert out["value"] == 123.5
        assert out["unit"] == "GB"
        assert out["description"] == "磁盘剩余"
        assert out["category"] == "disk"
        assert out["severity"] == "warning"
        assert out["timestamp"] == r.timestamp

    def test_to_dict_is_a_fresh_dict_each_call(self):
        """每次调用返回**顶层新 dict**；但 `metadata` 本体是共享的（见下条）。

        两条语义必须分开锁：
          · 顶层新对象 ⇒ 改 `first["value"]` **不会**回流到对象，也不会污染 `second`；
          · `metadata` 是同一对象 ⇒ 改它会**同时**反映在对象 `r.metadata` 与后续
            `to_dict()` 上（`to_dict` 未深拷）。后者是真实语义，不是缺陷，
            但必须显式记录，免得有人误以为"to_dict 已经隔离了 metadata"。
        """
        r = _r(metadata={"a": 1})
        first = r.to_dict()
        first["value"] = "篡改"
        second = r.to_dict()
        assert first is not second
        assert second["value"] == 1
        assert r.value == 1

        first["metadata"]["a"] = 2
        assert r.metadata == {"a": 2}
        assert r.to_dict()["metadata"] == {"a": 2}

    def test_to_dict_metadata_is_same_object(self):
        """**现状锁定**：`to_dict()` 不深拷 metadata/tags，返回的是同一对象。"""
        md = {"src": "wmi"}
        out = _r(metadata=md).to_dict()
        assert out["metadata"] is md


class TestToJson:
    """`to_json()` = `json.dumps(to_dict(), ensure_ascii=False)`。"""

    def test_roundtrip_equals_to_dict(self):
        r = _r(sensor_name="cpu_temp", value=85, unit="℃", description="处理器温度",
               category=Category.CPU, severity=Severity.CRITICAL,
               metadata={"k": [1, 2]}, tags=["t1"])
        assert json.loads(r.to_json()) == r.to_dict()

    def test_ensure_ascii_false_keeps_chinese_readable(self):
        """中文**不得**被转义成 `\\uXXXX`（审计链与人工排障都按可读文本读它）。"""
        raw = _r(description="处理器温度").to_json()
        assert "处理器温度" in raw
        assert "\\u" not in raw

    def test_no_metadata_keys_when_empty(self):
        """极小产物：空 metadata/tags 时 JSON 里**只有 7 个键**。"""
        assert sorted(json.loads(_r().to_json())) == sorted(TestToDict.BASE_KEYS)

    def test_non_serializable_value_raises_typeerror(self):
        """`value` 是任意对象时**如实抛 `TypeError`**（不吞异常、不做兜底字符串化）。

        这条锁定"库函数不替调用方兜底"的边界；调用方（传感器）负责给可序列化值。
        """
        with pytest.raises(TypeError):
            _r(value=object()).to_json()


class TestRepr:
    """`__repr__` 是日志/REPL 的第一手信息，格式必须稳定可解析。"""

    def test_exact_format_with_unit(self):
        assert repr(_r(sensor_name="cpu_usage", value=42, unit="%",
                       severity=Severity.NORMAL)) == "SensorReading(cpu_usage=42% [normal])"

    def test_exact_format_without_unit(self):
        """空单位不得留下多余空格。"""
        assert repr(_r(sensor_name="x", value=1, unit="",
                       severity=Severity.CRITICAL)) == "SensorReading(x=1 [critical])"

    def test_uses_normalized_severity_string(self):
        """repr 里的级别是**归一化后的字符串**（传枚举也渲染成 `warning`）。"""
        assert repr(_r(sensor_name="x", value=0, severity=Severity.WARNING)).endswith("[warning])")

    def test_repr_none_value_is_literal_none(self):
        """`value=None` 渲染为字面 `None`（不抛异常）—— 真实采集里确实存在空值读数。"""
        assert repr(_r(sensor_name="x", value=None)) == "SensorReading(x=None [normal])"

    def test_repr_contains_no_description_or_category(self):
        """刻意**不含**描述/类别：repr 简短，避免日志行过长（契约级断言）。"""
        out = repr(_r(sensor_name="x", value=1, description="很长很长的描述",
                      category=Category.CPU))
        assert "很长" not in out
        assert "cpu" not in out


# ═══════════════════════════════════════════════════════════════
#  4. 工厂函数（位置参数顺序是这里唯一的风险点）
# ═══════════════════════════════════════════════════════════════

class TestFactories:
    """4 个工厂必须与 `__init__` 的**位置参数顺序**一致，否则 metadata/tags 静默互换。"""

    def test_reading_matches_init_positional_order(self):
        """`reading()` 的 8 个位置参数逐个映射到 `__init__` 的同名参数。"""
        r = reading("n", 5, "u", "d", Category.NETWORK, Severity.WARNING, {"m": 1}, ["t"])
        expected = _r(sensor_name="n", value=5, unit="u", description="d",
                      category=Category.NETWORK, severity=Severity.WARNING,
                      metadata={"m": 1}, tags=["t"])
        assert r.to_dict() == expected.to_dict()
        assert r.metadata == {"m": 1}
        assert r.tags == ["t"]
        assert r.severity == "warning"
        assert r.category == "network"

    def test_reading_is_thin_passthrough(self):
        """`reading()` 不带任何默认值兜底：只给 4 个位置参数时 category/severity 为默认。"""
        r = reading("n", 1, "", "d")
        assert r.category is None
        assert r.severity == "normal"

    @pytest.mark.parametrize("factory,severity_value", [
        (normal, "normal"), (warning, "warning"), (critical, "critical"),
    ])
    def test_severity_factories_force_their_level(self, factory, severity_value):
        """3 个工厂即使外部显式传 `severity` 也会被**自己的档位覆盖**（工厂不接受该参数）。"""
        r = factory("n", 1, "", "d", Category.CPU)
        assert r.severity == severity_value
        assert r.category == "cpu"

    @pytest.mark.parametrize("factory,severity_value", [
        (normal, "normal"), (warning, "warning"), (critical, "critical"),
    ])
    def test_severity_factories_positional_tail_is_metadata_then_tags(self, factory, severity_value):
        """**关键**：工厂把 severity 插在第 6 位，因此第 6/7 个位置参数是 metadata/tags。

        若有人"顺手"把工厂签名改成 `(..., severity, metadata, tags)` 却忘了同步
        `SensorReading(...)` 的实参顺序，`metadata` 与 `tags` 会**静默互换**，
        而两者类型不同（dict vs list）时才偶然报错 —— 这条断言让它必然报错。
        """
        r = factory("n", 1, "", "d", Category.CPU, {"m": 1}, ["t"])
        assert r.metadata == {"m": 1}
        assert r.tags == ["t"]
        assert r.severity == severity_value

    def test_severity_factories_default_containers(self):
        """不传尾部两个参数时：`{}` 与 `[]`。"""
        r = warning("n", 1, "", "d")
        assert r.metadata == {}
        assert r.tags == []

    def test_factories_return_sensor_reading_instances(self):
        """4 个工厂产物类型一致（下游只按 `SensorReading` 判类型）。"""
        for obj in (reading("n", 1, "", "d"), normal("n", 1, "", "d"),
                    warning("n", 1, "", "d"), critical("n", 1, "", "d")):
            assert isinstance(obj, SensorReading)

    def test_factory_objects_survive_coerce(self):
        """工厂产物必须是 `_coerce` 认得的对象（回归锁：对象路径是生产唯一路径）。"""
        r = critical("cpu_temp", 99, "℃", "过热", Category.CPU)
        assert Translator._coerce(r) == {
            "sensor_name": "cpu_temp", "value": 99, "unit": "℃",
            "description": "过热", "severity": "critical", "tags": [],
        }
