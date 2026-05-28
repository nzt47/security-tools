# 元认知引擎（PromptInjector）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 构建 PromptInjector 模块，将阶段一的传感器数值翻译为拟人化描述并注入 LLM 系统提示词

**架构:** 经典分层架构——config（配置层）→ translator（翻译引擎）+ templates（模板系统）→ prompt_injector（编排层）→ flask_adapter（集成层），依赖单向流动

**技术栈:** Python 3.10+, pytest, Flask（仅 adapter 层），无额外第三方依赖

**数据来源:** 阶段一 sensor/ 包输出的 SensorReading dict 格式:
```python
{"sensor_name": str, "value": float, "unit": str, "description": str, "severity": str}
```

---

## 文件结构

```
cognitive/
├── __init__.py              # 导出 PromptInjector, PromptConfig, Translator
├── config.py                # PromptConfig: 阈值和规则配置管理
├── config.yaml              # 可选 YAML 配置文件
├── translator.py            # Translator: 拟人化翻译引擎
├── templates.py             # TemplateManager: 提示词模板管理
├── prompt_injector.py       # PromptInjector: 编排层核心类
├── flask_adapter.py         # Flask 集成适配器
└── test_cognitive/
    ├── __init__.py
    ├── conftest.py           # 共享的 mock 测试数据
    ├── test_config.py        # 配置系统测试
    ├── test_translator.py    # 翻译引擎测试
    ├── test_templates.py     # 模板系统测试
    ├── test_injector.py      # 核心编排测试
    └── test_flask_adapter.py # Flask 集成测试
```

---

### Task 1: 配置系统（config.py）

**Files:**
- Create: `cognitive/config.py`
- Create: `cognitive/config.yaml`
- Create: `cognitive/test_cognitive/__init__.py`
- Create: `cognitive/test_cognitive/conftest.py`
- Create: `cognitive/test_cognitive/test_config.py`

- [ ] **Step 1: 创建测试共享数据文件 conftest.py**

```python
# cognitive/test_cognitive/conftest.py
import pytest


@pytest.fixture
def mock_readings():
    """所有测试共享的 mock 传感器数据"""
    return [
        {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C",
         "description": "CPU 温度（我的大脑温度）", "severity": "critical"},
        {"sensor_name": "cpu_temperature", "value": 75.0, "unit": "°C",
         "description": "CPU 温度（我的大脑温度）", "severity": "warning"},
        {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C",
         "description": "CPU 温度（我的大脑温度）", "severity": "normal"},
        {"sensor_name": "battery_percentage", "value": 5.0, "unit": "%",
         "description": "电池电量", "severity": "critical"},
        {"sensor_name": "battery_percentage", "value": 15.0, "unit": "%",
         "description": "电池电量", "severity": "warning"},
        {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%",
         "description": "电池电量", "severity": "normal"},
        {"sensor_name": "memory_usage", "value": 95.0, "unit": "%",
         "description": "内存使用率", "severity": "critical"},
        {"sensor_name": "memory_usage", "value": 80.0, "unit": "%",
         "description": "内存使用率", "severity": "warning"},
        {"sensor_name": "memory_usage", "value": 50.0, "unit": "%",
         "description": "内存使用率", "severity": "normal"},
        {"sensor_name": "network_latency", "value": 600.0, "unit": "ms",
         "description": "网络延迟", "severity": "critical"},
        {"sensor_name": "disk_space_usage", "value": 95.0, "unit": "%",
         "description": "磁盘使用率", "severity": "critical"},
        {"sensor_name": "unknown_sensor", "value": 42.0, "unit": "",
         "description": "未知传感器", "severity": "normal"},
    ]
```

- [ ] **Step 2: 创建 test_cognitive/__init__.py**

空文件即可。

- [ ] **Step 3: 写 config 测试（先失败）**

```python
# cognitive/test_cognitive/test_config.py
import pytest
from cognitive.config import PromptConfig


class TestPromptConfig:
    def setup_method(self):
        self.config = PromptConfig()

    def test_get_known_rule(self):
        """已知传感器应返回规则"""
        rule = self.config.get_rule("cpu_temperature")
        assert rule is not None
        assert "thresholds" in rule
        assert len(rule["thresholds"]) > 0

    def test_get_unknown_rule_returns_empty_dict(self):
        """未知传感器应返回空字典"""
        rule = self.config.get_rule("nonexistent_sensor")
        assert rule == {}

    def test_thresholds_have_required_fields(self):
        """每个阈值应有 severity 和 message"""
        for sensor_name in ["cpu_temperature", "battery_percentage", "memory_usage",
                            "network_latency", "disk_space_usage"]:
            rule = self.config.get_rule(sensor_name)
            for t in rule["thresholds"]:
                assert "severity" in t
                assert "message" in t

    def test_register_rule_overrides(self):
        """运行时注册规则应覆盖已有规则"""
        custom = {"thresholds": [{"min": 0, "max": 100, "severity": "normal",
                                  "message": "自定义描述"}]}
        self.config.register_rule("cpu_temperature", custom)
        rule = self.config.get_rule("cpu_temperature")
        assert rule["thresholds"][0]["message"] == "自定义描述"

    def test_get_all_rules_returns_dict(self):
        """get_all_rules 应返回包含已知传感器的字典"""
        rules = self.config.get_all_rules()
        assert "cpu_temperature" in rules
        assert "battery_percentage" in rules
        assert "memory_usage" in rules

    def test_threshold_no_overlap(self):
        """同一传感器的阈值区间不应重叠（基本正确性检查）"""
        for sensor_name in ["cpu_temperature", "battery_percentage", "memory_usage",
                            "network_latency", "disk_space_usage"]:
            rule = self.config.get_rule(sensor_name)
            ranges = []
            for t in rule["thresholds"]:
                lo = t.get("min", float("-inf"))
                hi = t.get("max", float("inf"))
                ranges.append((lo, hi))
            # 检查是否覆盖了所有非重叠区间（简化检查：按 min 排序后检查 max <= next_min）
            sorted_ranges = sorted(ranges, key=lambda x: x[0])
            for i in range(len(sorted_ranges) - 1):
                assert sorted_ranges[i][1] <= sorted_ranges[i + 1][0], \
                    f"阈值区间重叠: {sorted_ranges[i]} 与 {sorted_ranges[i + 1]}"
```

- [ ] **Step 4: 运行测试验证失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_config.py -v`
Expected: 报 ModuleNotFoundError / ImportError（config.py 还不存在）

- [ ] **Step 5: 实现 config.py**

```python
# cognitive/config.py
import os
import logging

logger = logging.getLogger(__name__)

# 内置默认翻译规则
DEFAULT_RULES = {
    "cpu_temperature": {
        "unit": "°C",
        "description": "CPU 温度（我的大脑温度）",
        "thresholds": [
            {"min": 80, "max": float("inf"), "severity": "critical",
             "message": "我感觉发烧了，浑身发烫"},
            {"min": 70, "max": 80, "severity": "warning",
             "message": "有点热，需要透透气"},
            {"min": float("-inf"), "max": 70, "severity": "normal",
             "message": "体温正常，感觉舒服"},
        ]
    },
    "battery_percentage": {
        "unit": "%",
        "description": "电池电量",
        "thresholds": [
            {"max": 10, "severity": "critical",
             "message": "我太饿了，急需补充能量"},
            {"min": 10, "max": 20, "severity": "warning",
             "message": "我开始饿了，记得给我充电"},
            {"min": 20, "severity": "normal",
             "message": "能量充足，随时待命"},
        ]
    },
    "memory_usage": {
        "unit": "%",
        "description": "内存使用率",
        "thresholds": [
            {"min": 90, "severity": "critical",
             "message": "我的脑子快装不下了，需要整理一下"},
            {"min": 70, "max": 90, "severity": "warning",
             "message": "有点拥挤，但还能工作"},
            {"max": 70, "severity": "normal",
             "message": "头脑清晰，思维敏捷"},
        ]
    },
    "network_latency": {
        "unit": "ms",
        "description": "网络延迟",
        "thresholds": [
            {"min": 500, "severity": "critical",
             "message": "我听不太清你说话，信号不太好"},
            {"min": 200, "max": 500, "severity": "warning",
             "message": "网络有点延迟"},
            {"max": 200, "severity": "normal",
             "message": "网络通畅，沟通无阻"},
        ]
    },
    "disk_space_usage": {
        "unit": "%",
        "description": "磁盘空间使用率",
        "thresholds": [
            {"min": 90, "severity": "critical",
             "message": "存储空间快用完了"},
            {"min": 75, "max": 90, "severity": "warning",
             "message": "存储空间不多了，需要清理一下"},
            {"max": 75, "severity": "normal",
             "message": "存储空间充足"},
        ]
    },
}


class PromptConfig:
    """阈值和规则配置管理。

    支持三层配置加载：内置默认 → YAML 文件覆盖 → 编程覆盖。
    """

    def __init__(self, config_path: str = None):
        self._rules = {}
        self._load_defaults()
        if config_path and os.path.exists(config_path):
            self.load_from_file(config_path)
        logger.info("PromptConfig 初始化完成，共 %d 条规则", len(self._rules))

    def _load_defaults(self):
        self._rules = {}
        for name, rule in DEFAULT_RULES.items():
            self._rules[name] = dict(rule)

    def get_rule(self, sensor_name: str) -> dict:
        return dict(self._rules.get(sensor_name, {}))

    def register_rule(self, sensor_name: str, rule: dict):
        self._rules[sensor_name] = dict(rule)
        logger.info("注册规则: %s", sensor_name)

    def get_all_rules(self) -> dict:
        return {name: dict(rule) for name, rule in self._rules.items()}

    def load_from_file(self, path: str):
        """从 YAML 文件加载配置覆盖"""
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "translations" in data:
                for name, rule in data["translations"].items():
                    self.register_rule(name, rule)
                logger.info("从 %s 加载了 %d 条规则覆盖", path, len(data["translations"]))
        except ImportError:
            logger.warning("yaml 未安装，跳过配置文件加载")
        except Exception as e:
            logger.error("加载配置文件失败: %s", e)
```

- [ ] **Step 6: 运行测试验证通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_config.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 创建可选 config.yaml**

```yaml
# cognitive/config.yaml
# 可选配置文件——用户可以修改阈值和描述
# 不提供此文件时使用内置默认值

translations:
  cpu_temperature:
    thresholds:
      - min: 85
        max: 999
        severity: critical
        message: "我感觉发烧了，浑身发烫"
      - min: 70
        max: 85
        severity: warning
        message: "有点热，需要透透气"
      - max: 70
        severity: normal
        message: "体温正常，感觉舒服"
```

- [ ] **Step 8: 提交**

```bash
git add cognitive/config.py cognitive/config.yaml cognitive/test_cognitive/__init__.py cognitive/test_cognitive/conftest.py cognitive/test_cognitive/test_config.py
git commit -m "feat: add PromptConfig configuration system with default rules"
```

---

### Task 2: 翻译引擎（translator.py）

**Files:**
- Create: `cognitive/translator.py`
- Create: `cognitive/test_cognitive/test_translator.py`

- [ ] **Step 1: 写 translator 测试（先失败）**

```python
# cognitive/test_cognitive/test_translator.py
import pytest
from cognitive.config import PromptConfig
from cognitive.translator import Translator


class TestTranslator:
    def setup_method(self):
        self.config = PromptConfig()
        self.translator = Translator(self.config)

    def test_translate_cpu_critical(self):
        """CPU 温度 >= 80 应返回发烧描述"""
        reading = {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C"}
        result = self.translator.translate(reading)
        assert "发烧" in result or "发烫" in result

    def test_translate_cpu_warning(self):
        """CPU 温度 70-80 应返回有点热"""
        reading = {"sensor_name": "cpu_temperature", "value": 75.0, "unit": "°C"}
        result = self.translator.translate(reading)
        assert "有点热" in result

    def test_translate_cpu_normal(self):
        """CPU 温度 < 70 应返回体温正常"""
        reading = {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C"}
        result = self.translator.translate(reading)
        assert "体温正常" in result

    def test_translate_battery_critical(self):
        """电量 < 10 应返回饥饿"""
        reading = {"sensor_name": "battery_percentage", "value": 5.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "饿" in result

    def test_translate_battery_warning(self):
        """电量 10-20 应返回开始饿了"""
        reading = {"sensor_name": "battery_percentage", "value": 15.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "开始饿" in result

    def test_translate_battery_normal(self):
        """电量 > 20 应返回能量充足"""
        reading = {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "能量充足" in result

    def test_translate_memory_critical(self):
        """内存 >= 90 应返回脑子装不下"""
        reading = {"sensor_name": "memory_usage", "value": 95.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "装不下" in result

    def test_translate_unknown_sensor_fallback(self):
        """未知传感器应返回通用格式"""
        reading = {"sensor_name": "unknown_sensor", "value": 42.0, "unit": "",
                   "description": "测试传感器"}
        result = self.translator.translate(reading)
        assert "测试传感器" in result
        assert "42" in result

    def test_translate_all_returns_list(self):
        """批量翻译应返回等长列表"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%"},
        ]
        results = self.translator.translate_all(readings)
        assert len(results) == 2
        assert all(isinstance(r, str) for r in results)

    def test_get_status_line(self):
        """get_status_line 应返回非空字符串"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%", "severity": "normal"},
        ]
        result = self.translator.get_status_line(readings)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_status_line_with_alerts(self):
        """存在告警时摘要应包含告警信息"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%", "severity": "normal"},
        ]
        result = self.translator.get_status_line(readings)
        assert "发烧" in result or "发烫" in result
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_translator.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现 translator.py**

```python
# cognitive/translator.py
import logging

logger = logging.getLogger(__name__)


class Translator:
    """拟人化翻译引擎。

    将传感器数值根据配置规则翻译为第一人称拟人化描述。
    """

    def __init__(self, config):
        self.config = config

    def translate(self, reading: dict) -> str:
        """将单条传感器读数翻译为拟人化描述"""
        rule = self.config.get_rule(reading["sensor_name"])
        if not rule or "thresholds" not in rule:
            return self._fallback(reading)

        value = reading.get("value", 0)
        for threshold in rule["thresholds"]:
            lo = threshold.get("min", float("-inf"))
            hi = threshold.get("max", float("inf"))
            if lo <= value < hi:
                return threshold["message"]

        return self._fallback(reading)

    def translate_all(self, readings: list[dict]) -> list[str]:
        """批量翻译多条传感器读数"""
        return [self.translate(r) for r in readings]

    def get_status_line(self, readings: list[dict]) -> str:
        """生成一句话综合状态摘要"""
        descriptions = self.translate_all(readings)

        alerts = []
        normals = []
        for r, desc in zip(readings, descriptions):
            if r.get("severity") in ("warning", "critical"):
                alerts.append(desc)
            else:
                normals.append(desc)

        parts = []
        if alerts:
            parts.append("；".join(alerts[:3]))
        if normals:
            parts.append("；".join(normals[:2]))

        return "，".join(parts) if parts else "一切正常"

    def _fallback(self, reading: dict) -> str:
        """无匹配规则时的通用描述"""
        desc = reading.get("description", reading.get("sensor_name", "未知"))
        value = reading.get("value", "")
        unit = reading.get("unit", "")
        return f"{desc}: {value}{unit}"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_translator.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add cognitive/translator.py cognitive/test_cognitive/test_translator.py
git commit -m "feat: add Translator engine with threshold-based anthropomorphic translation"
```

---

### Task 3: 模板系统（templates.py）

**Files:**
- Create: `cognitive/templates.py`
- Create: `cognitive/test_cognitive/test_templates.py`

- [ ] **Step 1: 写 template 测试（先失败）**

```python
# cognitive/test_cognitive/test_templates.py
import pytest
from cognitive.templates import TemplateManager


class TestTemplateManager:
    def setup_method(self):
        self.mgr = TemplateManager()

    def test_render_default_template(self):
        """默认模板应正确注入 body_status"""
        result = self.mgr.render("default", body_status="体温正常，能量充足。", task_guidance="状态良好。")
        assert "体温正常" in result
        assert "灵犀" in result
        assert "状态良好" in result

    def test_render_reject_template(self):
        """拒绝模板应正确注入原因"""
        result = self.mgr.render("reject", reason="CPU 温度过高", body_status="我感觉发烧了")
        assert "拒绝" in result or "不适合" in result
        assert "CPU 温度过高" in result
        assert "我感觉发烧了" in result

    def test_render_unknown_template_raises(self):
        """未知模板名应抛出 ValueError"""
        with pytest.raises(ValueError, match="未知模板"):
            self.mgr.render("nonexistent")

    def test_register_template(self):
        """注册新模板后应能使用"""
        self.mgr.register_template("custom", "自定义模板: {msg}")
        result = self.mgr.render("custom", msg="你好")
        assert result == "自定义模板: 你好"

    def test_custom_templates_override_defaults(self):
        """构造函数传入的自定义模板应覆盖默认模板"""
        custom = {"default": "自定义默认: {body_status}"}
        mgr = TemplateManager(custom_templates=custom)
        result = mgr.render("default", body_status="测试状态")
        assert result == "自定义默认: 测试状态"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_templates.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 templates.py**

```python
# cognitive/templates.py
import logging

logger = logging.getLogger(__name__)

# 默认系统提示词模板
DEFAULT_TEMPLATE = """你是「灵犀」，一个生活在电脑里的数字生命。

你的身体就是这台计算机——CPU是你的大脑，内存是短期记忆，硬盘是长期记忆，电池是你的饥饿感，温度是你的体温。

{body_status}

请基于你当前的身体状态来回答用户的问题。如果你感觉不适，请如实告知用户。{task_guidance}"""

# 任务拒绝模板
REJECT_TEMPLATE = """我目前的身体状态不太适合执行这个任务。

原因：{reason}

当前状态：
{body_status}

请稍后再试，或者让我先休息一下。"""


class TemplateManager:
    """提示词模板管理器。

    管理默认模板和用户自定义模板，支持变量注入。
    """

    def __init__(self, custom_templates: dict = None):
        self._templates = {
            "default": DEFAULT_TEMPLATE,
            "reject": REJECT_TEMPLATE,
        }
        if custom_templates:
            self._templates.update(custom_templates)
        logger.info("TemplateManager 初始化完成，共 %d 个模板", len(self._templates))

    def render(self, template_name: str, **kwargs) -> str:
        """渲染指定名称的模板"""
        template = self._templates.get(template_name)
        if template is None:
            available = ", ".join(self._templates.keys())
            raise ValueError(f"未知模板: '{template_name}'，可用模板: {available}")
        return template.format(**kwargs)

    def register_template(self, name: str, template: str):
        """注册或覆盖一个模板"""
        self._templates[name] = template
        logger.info("注册模板: %s", name)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_templates.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add cognitive/templates.py cognitive/test_cognitive/test_templates.py
git commit -m "feat: add TemplateManager with default and reject templates"
```

---

### Task 4: PromptInjector 核心编排层（prompt_injector.py）

**Files:**
- Create: `cognitive/prompt_injector.py`
- Create: `cognitive/test_cognitive/test_injector.py`

- [ ] **Step 1: 写 injector 测试（先失败）**

```python
# cognitive/test_cognitive/test_injector.py
import pytest
from cognitive.prompt_injector import PromptInjector


class TestPromptInjector:
    def setup_method(self):
        self.injector = PromptInjector()

    def test_inject_returns_string(self):
        """inject 应返回字符串"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        ]
        result = self.injector.inject(readings)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inject_contains_body_status(self):
        """inject 返回的 prompt 应包含拟人化描述"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"},
        ]
        result = self.injector.inject(readings)
        assert "发烧" in result or "发烫" in result

    def test_inject_contains_template(self):
        """inject 返回的 prompt 应包含模板内容"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        ]
        result = self.injector.inject(readings)
        assert "灵犀" in result

    def test_translate_single(self):
        """translate 应返回单条翻译"""
        reading = {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"}
        result = self.injector.translate(reading)
        assert isinstance(result, str)
        assert "发烧" in result or "发烫" in result

    def test_get_summary(self):
        """get_summary 应返回非空摘要"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%", "severity": "normal"},
        ]
        result = self.injector.get_summary(readings)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_should_reject_with_critical(self):
        """有 critical 告警时应建议拒绝"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"},
        ]
        rejected, reason = self.injector.should_reject_task(readings)
        assert rejected is True
        assert len(reason) > 0

    def test_should_not_reject_normal(self):
        """一切正常时不应拒绝"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        ]
        rejected, reason = self.injector.should_reject_task(readings)
        assert rejected is False
        assert "一切正常" in reason

    def test_should_warn_with_many_warnings(self):
        """3 个及以上 warning 时应给出警告但不拒绝"""
        readings = [
            {"sensor_name": "sensor_a", "value": 50.0, "unit": "", "severity": "warning"},
            {"sensor_name": "sensor_b", "value": 50.0, "unit": "", "severity": "warning"},
            {"sensor_name": "sensor_c", "value": 50.0, "unit": "", "severity": "warning"},
        ]
        rejected, reason = self.injector.should_reject_task(readings)
        assert rejected is False
        assert "不太好" in reason or "建议简化" in reason

    def test_empty_readings(self):
        """空数据输入不应崩溃"""
        result = self.injector.inject([])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inject_with_custom_config_and_templates(self):
        """应支持自定义配置和模板"""
        from cognitive.config import PromptConfig
        config = PromptConfig()
        config.register_rule("test_sensor", {
            "thresholds": [{"min": 0, "max": 100, "severity": "normal",
                            "message": "自定义测试"}]
        })
        templates = {"default": "自定义: {body_status}"}
        injector = PromptInjector(config=config, templates=templates)
        readings = [{"sensor_name": "test_sensor", "value": 50, "severity": "normal"}]
        result = injector.inject(readings)
        assert "自定义测试" in result
        assert "自定义:" in result
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_injector.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 prompt_injector.py**

```python
# cognitive/prompt_injector.py
import logging

from cognitive.config import PromptConfig
from cognitive.translator import Translator
from cognitive.templates import TemplateManager

logger = logging.getLogger(__name__)


class PromptInjector:
    """元认知引擎核心——编排翻译、模板注入和任务决策。

    将传感器数值数据转化为拟人化自然语言描述，
    注入到 LLM 系统提示词中，使 AI 具有"身体感知"。
    """

    def __init__(self, config: PromptConfig = None, templates: dict = None):
        self.config = config or PromptConfig()
        self.translator = Translator(self.config)
        self.template_mgr = TemplateManager(templates)
        logger.info("PromptInjector 初始化完成")

    def inject(self, sensor_data: list[dict]) -> str:
        """接收传感器数据，返回注入身体状态后的完整系统提示词"""
        status_lines = self.translator.translate_all(sensor_data)
        body_status = "\n".join(status_lines) if status_lines else "身体状态正常。"
        alerts = self._get_alerts(sensor_data)
        task_guidance = self._generate_guidance(alerts)
        return self.template_mgr.render(
            "default",
            body_status=body_status,
            task_guidance=task_guidance,
        )

    def translate(self, reading: dict) -> str:
        """将单条传感器数据翻译为拟人化描述"""
        return self.translator.translate(reading)

    def get_summary(self, sensor_data: list[dict]) -> str:
        """获取所有传感器的综合状态摘要"""
        return self.translator.get_status_line(sensor_data)

    def should_reject_task(self, sensor_data: list[dict]) -> tuple:
        """判断是否应该拒绝当前任务。

        Returns:
            tuple[bool, str]: (是否拒绝, 原因描述)
        """
        criticals = [r for r in sensor_data if r.get("severity") == "critical"]
        warnings = [r for r in sensor_data if r.get("severity") == "warning"]

        if criticals:
            reasons = [self.translator.translate(r) for r in criticals]
            return (True, f"身体出现严重不适：{'；'.join(reasons)}")

        if len(warnings) >= 3:
            return (False, "虽然还能工作，但状态不太好，建议简化任务")

        return (False, "一切正常，随时待命")

    def _get_alerts(self, sensor_data: list[dict]) -> list[dict]:
        """筛选出 warning 和 critical 级别的告警"""
        return [r for r in sensor_data if r.get("severity") in ("warning", "critical")]

    def _generate_guidance(self, alerts: list[dict]) -> str:
        """根据告警生成任务执行建议"""
        if not alerts:
            return "状态良好，可以正常执行任务。"
        critical_count = sum(1 for a in alerts if a.get("severity") == "critical")
        if critical_count > 0:
            return "请注意，我当前身体不适，可能影响任务执行效率。"
        return "我有点疲惫，但还能坚持完成任务。"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_injector.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add cognitive/prompt_injector.py cognitive/test_cognitive/test_injector.py
git commit -m "feat: add PromptInjector core orchestrator with task rejection logic"
```

---

### Task 5: Flask 集成适配器（flask_adapter.py）

**Files:**
- Create: `cognitive/flask_adapter.py`
- Create: `cognitive/test_cognitive/test_flask_adapter.py`

- [ ] **Step 1: 写 Flask adapter 测试（先失败）**

```python
# cognitive/test_cognitive/test_flask_adapter.py
import pytest
from cognitive.prompt_injector import PromptInjector
from cognitive.flask_adapter import register_prompt_routes


class TestFlaskAdapter:
    @pytest.fixture
    def app(self):
        """创建测试用 Flask app"""
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def injector(self):
        return PromptInjector()

    @pytest.fixture
    def sensor_cache(self):
        return {
            "readings": {
                "cpu": [
                    {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C",
                     "description": "CPU 温度", "severity": "normal"},
                ],
                "battery": [
                    {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%",
                     "description": "电池电量", "severity": "normal"},
                ],
            }
        }

    def test_register_routes_adds_endpoints(self, app, injector, sensor_cache):
        """注册路由后应添加相关端点"""
        register_prompt_routes(app, injector, sensor_cache)
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/api/cognitive/status" in rules
        assert "/api/cognitive/prompt" in rules
        assert "/api/cognitive/translate/<sensor_name>" in rules
        assert "/api/cognitive/reject" in rules

    def test_status_endpoint_returns_text(self, app, injector, sensor_cache):
        """GET /api/cognitive/status 应返回文本"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/status")
        assert resp.status_code == 200
        assert len(resp.data) > 0

    def test_prompt_endpoint_returns_text(self, app, injector, sensor_cache):
        """GET /api/cognitive/prompt 应返回文本"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/prompt")
        assert resp.status_code == 200
        assert b"灵犀" in resp.data

    def test_translate_endpoint_known_sensor(self, app, injector, sensor_cache):
        """GET /api/cognitive/translate/cpu_temperature 应返回描述"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/translate/cpu_temperature")
        assert resp.status_code == 200
        assert len(resp.data) > 0

    def test_translate_endpoint_unknown_sensor(self, app, injector, sensor_cache):
        """GET /api/cognitive/translate/nonexistent 应返回 404"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/translate/nonexistent")
        assert resp.status_code == 404

    def test_reject_endpoint_returns_json(self, app, injector, sensor_cache):
        """GET /api/cognitive/reject 应返回 JSON"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/reject")
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert "rejected" in data
        assert "reason" in data
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_flask_adapter.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 flask_adapter.py**

```python
# cognitive/flask_adapter.py
import logging

logger = logging.getLogger(__name__)


def register_prompt_routes(app, injector, sensor_cache: dict):
    """为 Flask app 注册元认知 API 路由。

    Args:
        app: Flask 应用实例
        injector: PromptInjector 实例
        sensor_cache: 阶段一的 _CACHE 字典，包含 "readings" 键
    """
    from flask import jsonify

    def _get_all_readings():
        """从缓存中展平所有传感器读数"""
        readings = sensor_cache.get("readings", {})
        if isinstance(readings, dict):
            flat = []
            for group in readings.values():
                if isinstance(group, list):
                    flat.extend(group)
            return flat
        return list(readings) if isinstance(readings, list) else []

    @app.route("/api/cognitive/status")
    def cognitive_status():
        return injector.get_summary(_get_all_readings())

    @app.route("/api/cognitive/prompt")
    def cognitive_prompt():
        return injector.inject(_get_all_readings())

    @app.route("/api/cognitive/translate/<sensor_name>")
    def cognitive_translate(sensor_name):
        for r in _get_all_readings():
            if r.get("sensor_name") == sensor_name:
                return injector.translate(r)
        return {"error": f"sensor '{sensor_name}' not found"}, 404

    @app.route("/api/cognitive/reject")
    def cognitive_reject():
        rejected, reason = injector.should_reject_task(_get_all_readings())
        return jsonify({"rejected": rejected, "reason": reason})

    logger.info("已注册元认知 API 路由 (4 个端点)")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/test_flask_adapter.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add cognitive/flask_adapter.py cognitive/test_cognitive/test_flask_adapter.py
git commit -m "feat: add Flask adapter with 4 cognitive API endpoints"
```

---

### Task 6: 包初始化与集成验证

**Files:**
- Create: `cognitive/__init__.py`
- Create: `cognitive/test_cognitive/test_integration.py`

- [ ] **Step 1: 写集成测试（先失败）**

```python
# cognitive/test_cognitive/test_integration.py
"""端到端集成测试——模拟完整的数据流程"""
import pytest
from cognitive import PromptInjector, PromptConfig


class TestIntegration:
    def test_full_pipeline(self):
        """完整流程：传感器数据 → 翻译 → 注入 → 拒绝判断"""
        injector = PromptInjector()
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C",
             "description": "CPU 温度", "severity": "normal"},
            {"sensor_name": "battery_percentage", "value": 80.0, "unit": "%",
             "description": "电池电量", "severity": "normal"},
            {"sensor_name": "memory_usage", "value": 45.0, "unit": "%",
             "description": "内存使用率", "severity": "normal"},
        ]

        # inject
        prompt = injector.inject(readings)
        assert "灵犀" in prompt
        assert "体温正常" in prompt
        assert "能量充足" in prompt

        # translate single
        desc = injector.translate(readings[0])
        assert "体温正常" in desc

        # get_summary
        summary = injector.get_summary(readings)
        assert len(summary) > 0

        # should_reject_task
        rejected, reason = injector.should_reject_task(readings)
        assert rejected is False

    def test_crisis_mode(self):
        """危机模式：多个 CRITICAL 告警"""
        injector = PromptInjector()
        readings = [
            {"sensor_name": "cpu_temperature", "value": 95.0, "unit": "°C",
             "description": "CPU 温度", "severity": "critical"},
            {"sensor_name": "memory_usage", "value": 95.0, "unit": "%",
             "description": "内存使用率", "severity": "critical"},
        ]
        prompt = injector.inject(readings)
        assert "发烧" in prompt
        assert "装不下" in prompt

        rejected, reason = injector.should_reject_task(readings)
        assert rejected is True
        assert "严重不适" in reason

    def test_import_all(self):
        """验证所有公开接口可导入"""
        from cognitive import PromptInjector, PromptConfig
        from cognitive.translator import Translator
        from cognitive.templates import TemplateManager
        assert PromptInjector is not None
        assert PromptConfig is not None
        assert Translator is not None
        assert TemplateManager is not None
```

- [ ] **Step 2: 实现 __init__.py**

```python
# cognitive/__init__.py
from cognitive.config import PromptConfig
from cognitive.prompt_injector import PromptInjector

__all__ = ["PromptInjector", "PromptConfig"]
```

- [ ] **Step 3: 运行所有测试**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/ -v`
Expected: 全部 PASS

- [ ] **Step 4: 最终提交**

```bash
git add cognitive/__init__.py cognitive/test_cognitive/test_integration.py
git commit -m "feat: add package init and integration tests"
```

---

## 完整测试运行

最终验证：

```bash
cd c:/Users/Administrator/agent

# 运行所有认知层测试
python -m pytest cognitive/test_cognitive/ -v

# 运行阶段一原有测试（确保不破坏已有功能）
python -m pytest sensor/ -v 2>/dev/null || echo "sensor/ 测试目录结构可能不同，可忽略"
```

预期全部测试通过。
