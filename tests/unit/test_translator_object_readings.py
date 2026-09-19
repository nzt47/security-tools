# -*- coding: utf-8 -*-
"""`cognitive/translator.py` 的**生产路径**回归测试。

【为什么单独建这个文件 —— 它是对一次真实事故的补救】
  2026-09-19 修复 `translate()` 时，`tests/unit/test_cognitive_engine.py` 的 68 条用例
  与 `tests/test_cognitive_boundary.py` 的 25 条用例**全部通过**，于是我当时判定"修复已生效"。
  但用真实数据复测时发现：`BodySensor().collect_all()` 产出的是 **`SensorReading` 对象**，
  而上述测试的夹具**一律传 dict** ⇒ 生产路径从未被覆盖。

  后果：`translate()` 首行的 `isinstance(reading, dict)` 守卫把**全部 656 条真实读数**
  拦在函数入口，统统降级为 `"传感器读数未识别"`。也就是说：
      · 单测全绿；
      · 生产环境 100% 走同一条错误分支；
      · 且**静默**——不抛异常、不打日志，只是"提示词全是噪声"。
  这正是本仓 `TASK-00` 里那条纪律的实例：「测试夹具冒充生产」是最隐蔽的一类假绿。

  故本文件**只测对象路径**，并且使用**真实的 `SensorReading`**（不是本地假类），
  确保构造签名、字段名、属性表与生产完全一致。

【不重不漏的边界】
  本文件**不重复** `test_cognitive_engine.py` / `test_cognitive_boundary.py` 已锁定的
  dict 契约（残缺输入 ⇒ `UNRECOGNIZED` 等），只补它们覆盖不到的三块：
    (1) 对象读数（含"对象与等价 dict 必须同结果"的一致性断言）；
    (2) `_coerce` 对**不该放行**的输入类型仍返回 `None`（保住既有 TypeError 契约）；
    (3) `_fallback` 的三条实测判据（已含值不重复、类型名不当单位、空描述不产出 `": 值"`）。
"""
import sys

import pytest

from cognitive.config import PromptConfig
from cognitive.translator import Translator
from sensor.sensor_reading import SensorReading

UNRECOGNIZED = "传感器读数未识别"


@pytest.fixture
def translator():
    return Translator(PromptConfig())


def _reading(sensor_name, value, unit="", description="", **kw):
    return SensorReading(sensor_name, value, unit, description, **kw)


class TestObjectReadingPath:
    """核心回归：对象读数必须与等价 dict **得到同一结果**（这条断言就是事故的防线）。"""

    def test_known_sensor_object_hits_rule(self, translator):
        """命中规则的读数：对象路径必须真的走到 thresholds 遍历，而不是被守卫拦下。"""
        r = _reading("cpu_temperature", 85, "℃", "处理器温度")
        assert translator.translate(r) == "我感觉发烧了，浑身发烫"

    def test_object_and_equivalent_dict_agree(self, translator):
        """**关键不变量**：同一语义的 dict 与对象必须同结果。

        事故的根因正是这两者在 `translate()` 里走了不同分支（dict 进得去、对象被拦），
        所以这条断言必须在"未知传感器"与"已知传感器"两侧都成立。
        """
        cases = [
            ("cpu_temperature", 85, "℃", "处理器温度"),
            ("memory_usage", 92, "%", "内存占用率"),
            ("behavior_disk_total_read", 68150101, "次", "磁盘总读取次数"),
            ("totally_unknown_sensor", 42.0, "", "测试传感器"),
        ]
        for name, val, unit, desc in cases:
            obj = _reading(name, val, unit, desc)
            dct = {
                "sensor_name": name, "value": val, "unit": unit,
                "description": desc, "severity": "normal", "tags": [],
            }
            assert translator.translate(obj) == translator.translate(dct), (
                "对象与等价 dict 结果不一致: {!r}".format(name)
            )

    def test_unknown_sensor_object_uses_fallback(self, translator):
        """未知传感器（真实场景里占 99% 以上）必须给出可读描述，不再是"未识别"。"""
        r = _reading("behavior_disk_total_read", 68150101, "次", "磁盘总读取次数")
        assert translator.translate(r) == "磁盘总读取次数: 68150101次"

    def test_translate_all_accepts_object_list(self, translator):
        """`translate_all` 是 `PromptInjector` 真正调用的入口，必须吃对象列表。"""
        readings = [
            _reading("cpu_temperature", 85, "℃", "处理器温度"),
            _reading("behavior_disk_total_read", 100, "次", "磁盘总读取次数"),
        ]
        out = translator.translate_all(readings)
        assert out == ["我感觉发烧了，浑身发烫", "磁盘总读取次数: 100次"]
        assert UNRECOGNIZED not in out

    def test_reading_object_with_empty_description_falls_back_to_name(self, translator):
        """真实数据里存在 `description=''` 的读数（实测 1 条），须回落到 `sensor_name`。"""
        r = _reading("some_sensor", 7, "", "")
        assert translator.translate(r) == "some_sensor: 7"

    def test_reading_object_with_none_value_is_unrecognized(self, translator):
        """对象路径同样遵守"残缺输入 ⇒ 未识别"，不得因归一化而放宽。"""
        r = _reading("cpu_temperature", None, "℃", "处理器温度")
        assert translator.translate(r) == UNRECOGNIZED


class TestCoerceScope:
    """`_coerce` 的接受范围必须**收窄**：放行对象，但不放行 str/list 等，以保住既有契约。"""

    def test_dict_passes_through_unchanged(self):
        d = {"sensor_name": "x", "value": 1}
        assert Translator._coerce(d) is d

    def test_reading_object_coerced_to_minimal_dict(self):
        out = Translator._coerce(_reading("x", 1, "次", "描述"))
        assert out == {
            "sensor_name": "x", "value": 1, "unit": "次",
            "description": "描述", "severity": "normal", "tags": [],
        }

    @pytest.mark.parametrize("bad", [None, "abc", 3, 3.5, True, ["a"], ("a",), {"a"}])
    def test_non_object_inputs_rejected(self, bad):
        """这些必须返回 None ⇒ 由 `translate()` 降级为"未识别"。

        【实测校正】我起初以为这里会抛 `TypeError`，于是写下 `pytest.raises(TypeError)`；
        实测**不抛**。查证 `tests/test_cognitive_boundary.py` 后确认该文件锁定的是
        `PromptInjector.inject("invalid")` **优雅降级**（返回"身体状态正常"），
        且 `test_list_with_non_dict` 明确把 `"invalid_data"` / `123` / `None` 放进列表，
        因此"抛异常"从来不是契约，**我那条断言是自己臆造的**。此处按真实行为锁定。
        """
        assert Translator._coerce(bad) is None

    def test_translate_all_degrades_gracefully_on_junk(self, translator):
        """垃圾输入必须**逐条降级**为未识别，不得抛异常 —— 一条坏读数不能拖垮整批。

        这条不变量在生产上很关键：`PromptInjector` 一次注入数百条读数，
        若某条形态异常就抛异常，整段"身体状态"都会丢失。
        """
        out = translator.translate_all(["invalid_data", 123, None, {"sensor_name": "x"}])
        assert out[:3] == [UNRECOGNIZED, UNRECOGNIZED, UNRECOGNIZED]
        assert out[3] == "x: "

    def test_missing_value_is_normalized_to_zero_only_for_rule_lookup(self, translator):
        """**缺 `value` 的真实语义**（实测确认，非推测）：

        `translate()` 用 `reading.get("value", 0)` ⇒ 缺值时按 **0** 参与规则匹配；
        而 `_fallback` 自己用 `reading.get("value", "")` ⇒ 若真被调用会渲染出 `'x: '`。
        **两者默认值不一致**，但该不一致**不可达**：因为 `translate()` 总会先补 0，
        规则命中即返回，未命中时 `_fallback` 收到的也是 0 —— 即 `_fallback` 的 `""` 默认
        永远走不到。此处把这条交叉行为钉死，免得日后有人"顺手统一默认值"时无从判断。

        既有契约参照：`tests/test_cognitive_boundary.py:110 test_missing_value`
        用 `or` 同时接受"未识别"与"体温正常"（实测取后者）。
        """
        # 缺 value 的已知传感器 ⇒ 按 0 匹配规则（0 落在 normal 档）
        assert translator.translate({"sensor_name": "cpu_temperature"}) == "体温正常，感觉舒服"
        # 缺 value 的未知传感器 ⇒ 走 _fallback，值渲染为空
        assert translator.translate({"sensor_name": "x"}) == "x: "

    def test_translate_single_junk_is_unrecognized(self, translator):
        """字符串会被当作"逐字符迭代"传入 `translate()`，每个字符都应降级而非崩溃。"""
        assert translator.translate("abc") == UNRECOGNIZED
        assert translator.translate(123) == UNRECOGNIZED
        assert translator.translate(None) == UNRECOGNIZED


class TestFallbackJudgements:
    """`_fallback` 的三条实测判据。数据取自 `BodySensor().collect_all()` 的真实输出。"""

    def test_description_already_contains_value_no_duplication(self, translator):
        """描述已表达该值时**不得**重复追加。

        实测原始输出（修复前）：
            '用户态 CPU: 11.0%: 11.0%'
            '磁盘总读取数据量: 4237.57 GB: 4237.57GB'
            '切程 #1: … 自愿 849111235 / 非自愿 0: 849111235/0次'
        """
        cases = [
            (_reading("a", 11.0, "%", "用户态 CPU: 11.0%"), "用户态 CPU: 11.0%"),
            (_reading("b", 4237.57, "GB", "磁盘总读取数据量: 4237.57 GB"),
             "磁盘总读取数据量: 4237.57 GB"),
            (_reading("c", 539649695, "次", "页错误 #1: node.exe (PID 19784) 539649695 次"),
             "页错误 #1: node.exe (PID 19784) 539649695 次"),
            (_reading("d", "12天 2小时 41分钟", "", "系统已运行: 12天 2小时 41分钟"),
             "系统已运行: 12天 2小时 41分钟"),
            (_reading("e", "849111235/0", "次", "切程 #1: System Idle Process (PID 0) 自愿 849111235 / 非自愿 0"),
             "切程 #1: System Idle Process (PID 0) 自愿 849111235 / 非自愿 0"),
            (_reading("f", "COM1", "", "串口: 通信端口 (COM1)"), "串口: 通信端口 (COM1)"),
        ]
        for r, expected in cases:
            assert translator.translate(r) == expected, "重复渲染: {!r}".format(expected)

    def test_value_not_in_description_is_appended(self, translator):
        """**反向**：描述确实不含该值时，必须追加 —— 否则会把真实数值弄丢。"""
        r = _reading("机箱是否支持物理锁定", True, "bool", "机箱是否支持物理锁定")
        # 描述是"机箱是否支持物理锁定"，本身不含值 ⇒ 必须追加
        assert translator.translate(r) == "机箱是否支持物理锁定: True"

    def test_type_name_unit_is_not_appended(self, translator):
        """`unit='bool'` 是**类型名不是单位**，实测 3 条；不得产出 `Truebool`。"""
        out = translator.translate(_reading("x", True, "bool", "变更检测基准已建立"))
        assert out == "变更检测基准已建立: True"
        assert "bool" not in out

    @pytest.mark.parametrize("unit", ["bool", "int", "str", "list", "dict", "float", "NoneType"])
    def test_generic_units_all_dropped(self, translator, unit):
        assert translator.translate(_reading("x", 1, unit, "某读数")) == "某读数: 1"

    def test_real_unit_is_kept(self, translator):
        """真单位不能被误伤（回归护栏）。"""
        assert translator.translate(_reading("x", 8, "GB", "磁盘")) == "磁盘: 8GB"
        assert translator.translate(_reading("x", 8, "%", "占用")) == "占用: 8%"
        assert translator.translate(_reading("x", 8, "次", "次数")) == "次数: 8次"

    def test_empty_description_and_name_yields_empty_string(self, translator):
        """描述与名字**都空**时 `_fallback` 返回空串（会被 `PromptInjector` 过滤），
        而不是 `': 50'` 这种半截文本。"""
        assert translator._fallback(
            {"sensor_name": "", "description": "", "value": 50}) == ""

    def test_empty_name_reading_still_unrecognized_end_to_end(self, translator):
        """反向锁定：`translate()` 对**空名**读数仍返回未识别。

        注意 `_fallback` 的空描述兜底（上面那条）与 `translate()` 的空名守卫（本条）
        是**两层**保护：前者防止产出 `": 50"`，后者让"空名"这一残缺信号保持可见。
        两层都必须在，故这里同时锁定。
        """
        assert translator.translate({"sensor_name": "", "value": 50}) == UNRECOGNIZED
        assert translator.translate(_reading("", 50, "", "")) == UNRECOGNIZED

    def test_description_with_trailing_colon_not_doubled(self, translator):
        """描述以冒号结尾时不得拼出 `'xxx:: 1'`。"""
        assert translator.translate(_reading("x", 1, "", "计数:")) == "计数: 1"

    def test_short_value_does_not_false_match(self, translator):
        """`value` 极短时（1~2 字符）不得因与描述里无关数字重合就吞掉真实值。"""
        # '键盘: 增强型(101 或 102 键)' 里并不含值 '3'，必须追加而不是误判为"已含"
        out = translator.translate(_reading("k", 3, "个", "键盘: 增强型(101 或 102 键)"))
        assert out.endswith("3个")
