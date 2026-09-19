"""cognitive/ 元认知引擎测试 —— PromptConfig / Translator / TemplateManager / PromptInjector。

【为什么单独写这个文件，而不是直接用包内的 cognitive/test_cognitive/】
  `pytest.ini:52` 有 `--ignore=cognitive/test_cognitive`，且 `testpaths = tests`，
  因此包内那 8 个测试文件**永远不会被收集**（实测手跑 48 用例 / 2 失败，见
  docs/closeout/COVERAGE_GAP_PLAN_20260919.md §2.4b）。
  本文件把同样的被测面**放在 pytest 真正会收集的位置**，并且**不改任何生产文件**
  （pytest.ini 属本次任务的禁改清单）。

【2026-09-19 已决策并修复（遗留项 L-2 结案）】原版本文件**不断言"应该怎样"，只断言"现在怎样"**：
  `cognitive/translator.py` 定义了 `_fallback()`（无匹配规则时的通用描述），
  但 `translate()` 的 6 个兜底出口全部直接返回硬编码 "传感器读数未识别"，**从不调用它**。
  包内旧测试按"应该调用 _fallback"写，因此有 2 条失败。当时登记为需产品决策的遗留项 L-2，
  本文件刻意锁定**当前行为**，使"修实现 or 修断言"任选一边都会让断言**显式失败**。
  —— 这个设计**达到了目的**：修复落地时它精确报出 3 条失败（行 192/226/254），
     既没有静默通过，也没有伪装成回归。

**决策结果：修实现**（`cognitive/translator.py::translate` 的 docstring 载有完整理由）。
  决定性的并不是风格偏好，而是实测规模 —— 且成因有**两层，别只归因于一层**：
    · 第 1 层（决定性，最初被漏掉）：**类型不兼容**。真实读数以 `SensorReading` **对象**
      形态进来，撞上 `translate()` 首行 `isinstance(reading, dict)` 守卫 ⇒ 100% 走"未识别"，
      与规则覆盖率无关。修好后未识别率 100% → **0.15%**。
    · 第 2 层：**规则覆盖率低**。真实采集有 640+ 个不同 `sensor_name`，而默认配置只有 5 条规则。
  按**真实生产路径**（`digital_life_persona._build_body_status`：`to_dict()` → `inject()` → 截断 800 字符）
  复测：修复前那 800 字符**全是噪声行**，修复后为 **20 行真实读数、0 噪声**。

修复后 `translate()` 只剩两类出口，本文件两侧都有断言锁定：
  (a) **残缺输入**（非 dict／空名／None 名／缺值／None 值／NaN／非数值串）⇒ `UNRECOGNIZED`
      —— 这是**有意义的探测信号**，且编不出人话（`_fallback` 会产出 `": 50.0"` 这类垃圾）。
  (b) **有名字有值、只是无规则覆盖**（未知传感器／规则缺 thresholds／区间未命中）
      ⇒ `_fallback()` ⇒ `f"{description or sensor_name}: {value}{unit}"`。
  包内 `cognitive/test_cognitive/` 的旧断言已同步更新为 (b) 的期望。
"""
import sys

import pytest

from cognitive.config import DEFAULT_RULES, PromptConfig
from cognitive.prompt_injector import PromptInjector
from cognitive.templates import DEFAULT_TEMPLATE, REJECT_TEMPLATE, TemplateManager
from cognitive.translator import Translator

UNRECOGNIZED = "传感器读数未识别"


# ════════════════════════════════════════════════════════════════════
#  PromptConfig：默认规则、深拷贝隔离、注册校验、YAML 覆盖
# ════════════════════════════════════════════════════════════════════

class TestPromptConfig:

    def test_defaults_loaded_with_expected_names(self):
        """内置 5 条规则必须全部就位（这是 Translator 的输入契约）。"""
        cfg = PromptConfig()
        assert set(cfg.get_all_rules()) == set(DEFAULT_RULES)
        assert len(cfg.get_all_rules()) == 5

    def test_get_rule_returns_detached_deepcopy(self):
        """get_rule 必须返回深拷贝：调用方改它不能污染配置内部状态。

        【为什么必须验】Translator 直接遍历 thresholds，若拿到的是内部引用，
        一次就地修改就会跨用例、跨请求串味（本仓单进程 waitress 16 线程）。
        """
        cfg = PromptConfig()
        rule = cfg.get_rule("cpu_temperature")
        rule["thresholds"][0]["message"] = "被改坏了"
        rule["thresholds"].append({"min": 0, "max": 1, "message": "注入"})
        fresh = cfg.get_rule("cpu_temperature")
        assert fresh["thresholds"][0]["message"] != "被改坏了"
        assert len(fresh["thresholds"]) == 3

    def test_get_rule_unknown_returns_empty_dict(self):
        assert PromptConfig().get_rule("no_such_sensor") == {}

    def test_get_all_rules_returns_detached_deepcopy(self):
        cfg = PromptConfig()
        all_rules = cfg.get_all_rules()
        all_rules["cpu_temperature"]["thresholds"].clear()
        assert len(cfg.get_rule("cpu_temperature")["thresholds"]) == 3

    def test_register_rule_accepts_valid_rule(self):
        cfg = PromptConfig()
        cfg.register_rule("disk_latency", {
            "unit": "ms",
            "thresholds": [{"min": 0, "max": 10, "message": "很快"}],
        })
        assert cfg.get_rule("disk_latency")["unit"] == "ms"

    def test_register_rule_rejects_non_dict(self):
        with pytest.raises(ValueError, match="thresholds"):
            PromptConfig().register_rule("x", ["not-a-dict"])

    def test_register_rule_rejects_missing_thresholds(self):
        with pytest.raises(ValueError, match="thresholds"):
            PromptConfig().register_rule("x", {"unit": "%"})

    def test_register_rule_rejects_non_list_thresholds(self):
        with pytest.raises(ValueError, match="列表"):
            PromptConfig().register_rule("x", {"thresholds": {"min": 1}})

    def test_register_rule_deepcopies_input(self):
        """注册后调用方再改原 dict，不得影响已注册规则。"""
        cfg = PromptConfig()
        rule = {"thresholds": [{"min": 0, "max": 1, "message": "原始"}]}
        cfg.register_rule("x", rule)
        rule["thresholds"][0]["message"] = "改过了"
        assert cfg.get_rule("x")["thresholds"][0]["message"] == "原始"

    def test_load_from_file_overrides_rules(self, tmp_path):
        p = tmp_path / "prompt_rules.yaml"
        p.write_text(
            "translations:\n"
            "  cpu_temperature:\n"
            "    unit: '°C'\n"
            "    thresholds:\n"
            "      - {min: 0, max: 100, message: 覆盖后的温度描述}\n"
            "  brand_new_sensor:\n"
            "    thresholds:\n"
            "      - {min: 0, max: 1, message: 新规则}\n",
            encoding="utf-8")
        cfg = PromptConfig(config_path=str(p))
        assert cfg.get_rule("cpu_temperature")["thresholds"][0]["message"] == "覆盖后的温度描述"
        assert cfg.get_rule("brand_new_sensor")["thresholds"][0]["message"] == "新规则"
        # 未被覆盖的规则必须保留
        assert cfg.get_rule("memory_usage")["thresholds"][0]["message"] == "我的脑子快装不下了，需要整理一下"

    def test_constructor_skips_nonexistent_path(self, tmp_path):
        """传了不存在的路径必须静默跳过，不能抛异常（启动链 D4：不得阻塞平台启动）。"""
        cfg = PromptConfig(config_path=str(tmp_path / "missing.yaml"))
        assert len(cfg.get_all_rules()) == 5

    def test_load_from_file_without_translations_key_is_noop(self, tmp_path):
        p = tmp_path / "other.yaml"
        p.write_text("something_else:\n  a: 1\n", encoding="utf-8")
        cfg = PromptConfig(config_path=str(p))
        assert len(cfg.get_all_rules()) == 5

    def test_load_from_file_malformed_yaml_is_swallowed(self, tmp_path):
        """坏 YAML 只能记日志，不能让构造失败（否则配置一改平台就起不来）。"""
        p = tmp_path / "broken.yaml"
        p.write_text("{{{{ not: valid: yaml", encoding="utf-8")
        cfg = PromptConfig(config_path=str(p))
        assert len(cfg.get_all_rules()) == 5

    def test_load_from_file_when_yaml_missing_is_swallowed(self, tmp_path, monkeypatch):
        """yaml 未安装时走 ImportError 分支：只告警，不抛。

        【变易】把 sys.modules['yaml'] 置 None 是 Python 标准的"模拟未安装"手法，
        比 patch open() 更贴近真实的 ImportError 路径。
        """
        p = tmp_path / "rules.yaml"
        p.write_text("translations: {}\n", encoding="utf-8")
        monkeypatch.setitem(sys.modules, "yaml", None)
        cfg = PromptConfig(config_path=str(p))
        assert len(cfg.get_all_rules()) == 5


# ════════════════════════════════════════════════════════════════════
#  Translator：阈值区间翻译（左闭右开）
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def translator():
    return Translator(PromptConfig())


class TestTranslator:

    def test_known_sensor_maps_to_matching_threshold(self, translator):
        assert translator.translate(
            {"sensor_name": "cpu_temperature", "value": 85}) == "我感觉发烧了，浑身发烫"
        assert translator.translate(
            {"sensor_name": "cpu_temperature", "value": 75}) == "有点热，需要透透气"
        assert translator.translate(
            {"sensor_name": "cpu_temperature", "value": 60}) == "体温正常，感觉舒服"

    def test_threshold_interval_is_left_closed_right_open(self, translator):
        """区间约定 min <= value < max —— 边界值属于哪一档必须钉死。

        【不易】80 落在 [80, inf) ⇒ critical；70 落在 [70, 80) ⇒ warning。
        改这个约定会直接改变注入给模型的"身体状态"文案，属线上可感知行为。
        """
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 80}) == "我感觉发烧了，浑身发烫"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 70}) == "有点热，需要透透气"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 69.9}) == "体温正常，感觉舒服"

    def test_open_ended_threshold_forms(self, translator):
        """阈值三种形态：只给 max / 只给 min / 同时给。"""
        # 只给 max（battery 的 critical 档）
        assert translator.translate({"sensor_name": "battery_percentage", "value": 5}) == "我太饿了，急需补充能量"
        # 只给 min（battery 的 normal 档）
        assert translator.translate({"sensor_name": "battery_percentage", "value": 99}) == "能量充足，随时待命"
        # 四舍五入到边界
        assert translator.translate({"sensor_name": "battery_percentage", "value": 10}) == "我开始饿了，记得给我充电"

    @pytest.mark.parametrize("reading", [
        "not-a-dict",
        None,
        42,
        [],
    ])
    def test_non_dict_reading_is_unrecognized(self, translator, reading):
        assert translator.translate(reading) == UNRECOGNIZED

    def test_missing_or_empty_sensor_name_is_unrecognized(self, translator):
        assert translator.translate({"value": 42.0}) == UNRECOGNIZED
        assert translator.translate({"sensor_name": "", "value": 42.0}) == UNRECOGNIZED

    def test_unknown_sensor_is_unrecognized(self, translator):
        """✅ **2026-09-19 契约更新**：未知传感器**现在会调用 `_fallback()`**。

        【本条测试的来历 —— 保留以便追溯】
        初版 docstring 写的是：「【不易·当前行为】未知传感器**不**调用 `_fallback`，
        直接返回固定串。包内旧测试（`cognitive/test_cognitive/test_translator.py:59`）
        期望回显 description，因此失败。此处锁定当前实现，防止悄悄改变对模型输出的文案。」
        —— 即当时**刻意锁定缺陷行为**，并把"修实现 or 修断言"登记为产品决策（遗留项 L-2）。

        【决策结果：修实现】（理由见 `cognitive/translator.py::translate` 的 docstring）
        成因有**两层**，本测试只覆盖第 2 层，别把两者混为一谈：
          · 第 1 层（决定性，最初被漏掉）：真实读数是 `SensorReading` **对象**，撞上
            `translate()` 首行的 `isinstance(reading, dict)` 守卫 ⇒ 100% 走"未识别"。
            对象路径的回归测试在 `tests/unit/test_translator_object_readings.py`。
          · 第 2 层（本条）：即便类型修好，默认只有 5 条规则而真实 `sensor_name` 有 640+ 个，
            无规则命中时若不调 `_fallback()` 仍会返回固定串 ⇒ 注入内容被噪声刷满。
        两层修复后按真实生产路径复测：截断的 800 字符内为 **20 行真实读数、0 噪声**
        （`agent/digital_life_persona.py::_build_body_status`）。

        【修复后的语义分层】
        · **有名字、有值、只是没有匹配规则** ⇒ 走 `_fallback()` 给出可读描述（本条）
        · **name/value 残缺**（空名/None 名/缺名/缺值/NaN/非数值串）⇒ 仍返回 `UNRECOGNIZED`
          （那是有意义的探测信号，且编不出人话）
        """
        assert translator.translate({
            "sensor_name": "unknown_sensor", "value": 42.0,
            "unit": "", "description": "测试传感器",
        }) == "测试传感器: 42.0"

        # 反向：无 description 时 `_fallback` 回落到 `sensor_name`（同样比"未识别"有信息量）
        assert translator.translate({
            "sensor_name": "unknown_sensor", "value": 42.0, "unit": "",
        }) == "unknown_sensor: 42.0"

        # 对比：**残缺输入**仍返回未识别（语义未变）
        assert translator.translate({"sensor_name": "", "value": 42.0}) == UNRECOGNIZED
        assert translator.translate({"value": 42.0}) == UNRECOGNIZED

    def test_rule_without_thresholds_falls_back(self, translator):
        """规则存在但缺 thresholds ⇒ 视为"没有匹配规则"，走 `_fallback`。

        【2026-09-19 契约更新】此前期望 `UNRECOGNIZED`。修复后，**有名字有值**的读数
        即便规则残缺也给出可读描述（`_fallback` 输出 `f"{desc}: {value}{unit}"`，
        缺 `description` 时回落 `sensor_name`）⇒ `'broken: 1'`。
        理由与 `test_unknown_sensor_is_unrecognized` 同源：这类输入**不是**残缺输入，
        编得出人话；把它降级成"未识别"会让注入提示词的内容被噪声刷满
        （规模与两层成因见 `test_unknown_sensor_is_unrecognized` 的 docstring）。

        【变易】register_rule 强制要求 thresholds，所以这里直接注入内部表——
        目的是覆盖"配置文件能绕过 register_rule 校验"的真实可能（load_from_file 走的就是 register_rule，
        但 _load_defaults 之外的路径不保证）。
        """
        translator.config._rules["broken"] = {"unit": "%"}
        assert translator.translate({"sensor_name": "broken", "value": 1}) == "broken: 1"

    def test_none_value_is_unrecognized(self, translator):
        assert translator.translate({"sensor_name": "cpu_temperature", "value": None}) == UNRECOGNIZED

    def test_numeric_string_value_is_coerced(self, translator):
        """字符串数值必须能翻译（传感器经 JSON/SSE 过来时常是 str）。"""
        assert translator.translate({"sensor_name": "cpu_temperature", "value": "75"}) == "有点热，需要透透气"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": "85.5"}) == "我感觉发烧了，浑身发烫"

    def test_non_numeric_string_value_is_unrecognized(self, translator):
        assert translator.translate({"sensor_name": "cpu_temperature", "value": "很热"}) == UNRECOGNIZED

    def test_type_error_inside_threshold_loop_is_unrecognized(self, translator):
        """阈值本身类型坏了（min 是 str）⇒ 比较抛 TypeError ⇒ 必须降级不抛。"""
        translator.config._rules["bad_rule"] = {
            "thresholds": [{"min": "abc", "max": 100, "message": "永不命中"}],
        }
        assert translator.translate({"sensor_name": "bad_rule", "value": 50}) == UNRECOGNIZED

    def test_no_threshold_matches_falls_back_to_description(self, translator):
        """值落在所有区间之外（这里区间有洞）⇒ 同样走 `_fallback`。

        【2026-09-19 契约更新】此前期望 `UNRECOGNIZED`。修复后语义统一为：
        出口只有两类 ——（a）**残缺输入** ⇒ `UNRECOGNIZED`；（b）**有名字有值但无规则覆盖**
        ⇒ `_fallback`。「规则存在但没有区间命中」属于 (b)：这里 `999` 是个**完全正常**的读数，
        把它翻译成"未识别"会让 LLM 提示词丢掉真实数值（`gappy: 999` 至少保住了）。
        """
        translator.config._rules["gappy"] = {
            "thresholds": [{"min": 10, "max": 20, "message": "中段"}],
        }
        assert translator.translate({"sensor_name": "gappy", "value": 999}) == "gappy: 999"

        # 反向锁定：**残缺输入**的 `UNRECOGNIZED` 契约未被本次修复波及
        assert translator.translate({"sensor_name": "gappy", "value": None}) == UNRECOGNIZED
        assert translator.translate({"sensor_name": "gappy", "value": "很热"}) == UNRECOGNIZED

    def test_missing_value_defaults_to_zero(self, translator):
        """value 缺失时取 0 —— 0 落在 battery 的 critical 档。"""
        assert translator.translate({"sensor_name": "battery_percentage"}) == "我太饿了，急需补充能量"

    def test_translate_all_batch(self, translator):
        """【2026-09-19 契约更新】`unknown_sensor` 的期望由 `UNRECOGNIZED` 改为描述性输出。

        `translate()` 修复后（见 `cognitive/translator.py::translate` 的 docstring）：
        **有名字、有值、只是没有匹配规则**的读数走 `_fallback()` ⇒ `'unknown_sensor: 1'`。
        理由：真实采集有 640+ 个不同 `sensor_name`，默认配置却只有 5 条规则
        ⇒ 绝大多数读数无规则可命中；若一律返回 `UNRECOGNIZED`，
        `PromptInjector` 注入 LLM 提示词的 `body_status` 会被噪声刷满 ——
        而该注入器的存在意义就是"赋予 AI 身体感知"。
        （两层成因与真实生产口径的复测数据见 `test_unknown_sensor_is_unrecognized`。）

        `UNRECOGNIZED` 仍用于**残缺输入**（空名/None 名/缺名/缺值/NaN/非数值串），
        那些情形由本文件其余用例与 `tests/test_cognitive_boundary.py` 共同锁定。
        """
        out = translator.translate_all([
            {"sensor_name": "cpu_temperature", "value": 85},
            {"sensor_name": "unknown_sensor", "value": 1},
        ])
        assert out == ["我感觉发烧了，浑身发烫", "unknown_sensor: 1"]

    def test_translate_all_empty(self, translator):
        assert translator.translate_all([]) == []

    def test_is_valid_value_rejects_bool(self, translator):
        """【不易】bool 是 int 的子类，必须显式排除，否则 True 会被当成 1。"""
        assert translator._is_valid_value(1) is True
        assert translator._is_valid_value(1.5) is True
        assert translator._is_valid_value(True) is False
        assert translator._is_valid_value("1") is False
        assert translator._is_valid_value(None) is False


class TestTranslatorStatusLine:

    def test_status_line_with_alerts_and_normals(self, translator):
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85, "severity": "critical"},
            {"sensor_name": "cpu_temperature", "value": 60, "severity": "normal"},
        ]
        line = translator.get_status_line(readings)
        assert "我感觉发烧了，浑身发烫" in line
        assert "体温正常，感觉舒服" in line
        assert "，" in line  # 告警段与正常段以中文逗号相连

    def test_status_line_truncates_alerts_to_three(self, translator):
        readings = [{"sensor_name": "cpu_temperature", "value": 85, "severity": "critical"}] * 5
        line = translator.get_status_line(readings)
        assert line.count("；") == 2  # 3 段用 2 个分号

    def test_status_line_truncates_normals_to_two(self, translator):
        readings = [{"sensor_name": "cpu_temperature", "value": 60, "severity": "normal"}] * 4
        line = translator.get_status_line(readings)
        assert line.count("；") == 1  # 2 段用 1 个分号

    def test_status_line_empty_is_all_normal(self, translator):
        assert translator.get_status_line([]) == "一切正常"

    def test_status_line_missing_severity_counts_as_normal(self, translator):
        line = translator.get_status_line([{"sensor_name": "cpu_temperature", "value": 60}])
        assert line == "体温正常，感觉舒服"


# ════════════════════════════════════════════════════════════════════
#  TemplateManager：模板渲染与错误语义
# ════════════════════════════════════════════════════════════════════

class TestTemplateManager:

    def test_default_templates_registered(self):
        mgr = TemplateManager()
        assert set(mgr._templates) == {"default", "reject"}

    def test_default_template_renders_all_variables(self):
        out = TemplateManager().render(
            "default", body_status="身体状态正常。", task_guidance="可以开工。")
        assert "身体状态正常。" in out
        assert "可以开工。" in out
        assert "{body_status}" not in out

    def test_reject_template_renders(self):
        out = TemplateManager().render("reject", reason="发烧了", body_status="很烫")
        assert "发烧了" in out and "很烫" in out

    def test_custom_template_overrides_builtin(self):
        mgr = TemplateManager({"default": "自定义：{body_status}"})
        assert mgr.render("default", body_status="OK") == "自定义：OK"

    def test_custom_template_adds_new_name(self):
        mgr = TemplateManager({"shout": "{body_status}!!!"})
        assert mgr.render("shout", body_status="烫") == "烫!!!"

    def test_unknown_template_raises_with_available_list(self):
        with pytest.raises(ValueError, match="未知模板"):
            TemplateManager().render("nope")

    def test_missing_variable_raises_value_error(self):
        with pytest.raises(ValueError, match="缺少必要变量"):
            TemplateManager().render("default", body_status="只有一半")

    def test_register_template(self):
        mgr = TemplateManager()
        mgr.register_template("extra", "E:{body_status}")
        assert mgr.render("extra", body_status="x") == "E:x"

    def test_module_level_templates_are_non_empty(self):
        assert "{body_status}" in DEFAULT_TEMPLATE
        assert "{reason}" in REJECT_TEMPLATE and "{body_status}" in REJECT_TEMPLATE


# ════════════════════════════════════════════════════════════════════
#  PromptInjector：编排（注入 / 告警 / 建议 / 拒绝决策）
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def injector():
    return PromptInjector()


class TestPromptInjector:

    def test_init_builds_collaborators(self, injector):
        assert isinstance(injector.config, PromptConfig)
        assert isinstance(injector.translator, Translator)
        assert isinstance(injector.template_mgr, TemplateManager)

    @pytest.mark.parametrize("bad", [None, "not-a-list", 42, {"a": 1}])
    def test_inject_non_list_returns_default_body_status(self, injector, bad):
        out = injector.inject(bad)
        assert "身体状态正常。" in out
        assert "状态良好，可以正常执行任务。" in out

    def test_inject_empty_list_returns_default_body_status(self, injector):
        out = injector.inject([])
        assert "身体状态正常。" in out

    def test_inject_translates_readings_into_prompt(self, injector):
        out = injector.inject([
            {"sensor_name": "cpu_temperature", "value": 85, "severity": "critical"},
        ])
        assert "我感觉发烧了，浑身发烫" in out
        # critical ⇒ 走 _generate_guidance 的"身体不适"分支
        assert "请注意，我当前身体不适，可能影响任务执行效率。" in out

    def test_inject_warning_only_guidance(self, injector):
        out = injector.inject([
            {"sensor_name": "cpu_temperature", "value": 75, "severity": "warning"},
        ])
        assert "我有点疲惫，但还能坚持完成任务。" in out

    def test_inject_drops_non_dict_entries(self, injector):
        """非 dict 项被 :33 过滤，不应让整次注入失败。"""
        out = injector.inject([
            "垃圾数据",
            {"sensor_name": "cpu_temperature", "value": 60, "severity": "normal"},
        ])
        assert "体温正常，感觉舒服" in out

    def test_translate_delegates_to_translator(self, injector):
        assert injector.translate({"sensor_name": "cpu_temperature", "value": 85}) == "我感觉发烧了，浑身发烫"

    def test_get_summary_delegates_to_status_line(self, injector):
        summary = injector.get_summary([{"sensor_name": "cpu_temperature", "value": 60, "severity": "normal"}])
        assert summary == "体温正常，感觉舒服"

    def test_should_reject_task_on_critical(self, injector):
        reject, reason = injector.should_reject_task([
            {"sensor_name": "cpu_temperature", "value": 85, "severity": "critical"},
        ])
        assert reject is True
        assert reason.startswith("身体出现严重不适：")
        assert "我感觉发烧了，浑身发烫" in reason

    def test_should_reject_task_joins_multiple_criticals(self, injector):
        _, reason = injector.should_reject_task([
            {"sensor_name": "cpu_temperature", "value": 85, "severity": "critical"},
            {"sensor_name": "memory_usage", "value": 95, "severity": "critical"},
        ])
        assert "；" in reason

    def test_should_not_reject_on_exactly_three_warnings(self, injector):
        """【不易】阈值是 >= 3，不是 > 3。"""
        readings = [{"sensor_name": "cpu_temperature", "value": 75, "severity": "warning"}] * 3
        reject, reason = injector.should_reject_task(readings)
        assert reject is False
        assert reason == "虽然还能工作，但状态不太好，建议简化任务"

    def test_should_not_reject_on_two_warnings(self, injector):
        readings = [{"sensor_name": "cpu_temperature", "value": 75, "severity": "warning"}] * 2
        reject, reason = injector.should_reject_task(readings)
        assert reject is False
        assert reason == "一切正常，随时待命"

    def test_should_not_reject_on_empty(self, injector):
        assert injector.should_reject_task([]) == (False, "一切正常，随时待命")

    def test_get_alerts_filters_warning_and_critical(self, injector):
        alerts = injector._get_alerts([
            {"severity": "normal"}, {"severity": "warning"},
            {"severity": "critical"}, {},
        ])
        assert alerts == [{"severity": "warning"}, {"severity": "critical"}]

    def test_generate_guidance_without_alerts(self, injector):
        assert injector._generate_guidance([]) == "状态良好，可以正常执行任务。"

    def test_generate_guidance_with_critical(self, injector):
        guidance = injector._generate_guidance([{"severity": "critical"}, {"severity": "warning"}])
        assert guidance == "请注意，我当前身体不适，可能影响任务执行效率。"

    def test_generate_guidance_with_warnings_only(self, injector):
        guidance = injector._generate_guidance([{"severity": "warning"}] * 4)
        assert guidance == "我有点疲惫，但还能坚持完成任务。"

    def test_accepts_explicit_config_and_templates(self):
        cfg = PromptConfig()
        cfg.register_rule("custom_metric", {
            "thresholds": [{"min": 0, "max": 10, "message": "自定义档位"}],
        })
        inj = PromptInjector(config=cfg, templates={"default": "B={body_status}|T={task_guidance}"})
        out = inj.inject([{"sensor_name": "custom_metric", "value": 5, "severity": "normal"}])
        assert out == "B=自定义档位|T=状态良好，可以正常执行任务。"
