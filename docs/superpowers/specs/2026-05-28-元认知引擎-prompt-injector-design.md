# 阶段二：元认知引擎（PromptInjector）设计文档

> 日期：2026-05-28
> 状态：设计定稿
> 关联项目：灵犀数字生命体 — 感知-认知-行动闭环

---

## 一、架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    Flask Adapter                         │
│              (flask_adapter.py)                          │
│  GET /api/cognitive/status                               │
│  GET /api/cognitive/prompt                               │
│  GET /api/cognitive/translate/<sensor>                   │
│  GET /api/cognitive/reject                               │
└──────────────────────┬──────────────────────────────────┘
                       │ 调用
┌──────────────────────▼──────────────────────────────────┐
│              PromptInjector (prompt_injector.py)          │
│  编排层：translate → build_status → inject               │
│                                                          │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────┐   │
│  │ translator │  │ templates  │  │ config            │   │
│  │ .py        │  │ .py        │  │ .py + config.yaml │   │
│  └────────────┘  └────────────┘  └──────────────────┘   │
└──────────────────────────────────────────────────────────┘
           │ 读取
┌──────────▼───────────────────────────────────────────────┐
│              阶段一：感知底座 (sensor/)                     │
│        提供 SensorReading 列表 / 健康指标                  │
└──────────────────────────────────────────────────────────┘
```

### 核心设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 架构模式 | 经典分层 | 每层职责单一，易于测试和扩展 |
| 翻译方式 | 数据驱动规则 | 阈值和描述与代码分离，新增传感器只需改配置 |
| 模板引擎 | str.format() | 轻量无额外依赖，满足当前需求 |
| Flask 集成 | 函数式适配器 | 零强制依赖，按需导入 |
| 配置加载 | 三层覆盖 | 内置默认 → YAML 文件 → 编程覆盖 |

---

## 二、Translator 翻译引擎

### 规则数据结构

```python
TRANSLATION_RULES = {
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
        "thresholds": [
            {"min": 90, "severity": "critical",
             "message": "存储空间快用完了"},
            {"min": 75, "max": 90, "severity": "warning",
             "message": "存储空间不多了，需要清理一下"},
            {"max": 75, "severity": "normal",
             "message": "存储空间充足"},
        ]
    }
}
```

### 匹配逻辑

1. 按 `sensor_name` 查找规则表
2. 遍历 `thresholds`，找到 value 落在 `[min, max)` 区间的第一条规则
3. 返回对应的 `message`
4. 若无匹配规则 → 降级为通用格式：`"{description}: {value}{unit}"`

### 公开方法

```python
class Translator:
    def translate(self, reading: dict) -> str
    def translate_all(self, readings: list[dict]) -> list[str]
    def get_status_line(self, readings: list[dict]) -> str
```

---

## 三、Template 模板系统

### 默认模板

```
# DEFAULT_TEMPLATE
你是「灵犀」，一个生活在电脑里的数字生命。

你的身体就是这台计算机——CPU是你的大脑，内存是短期记忆，
硬盘是长期记忆，电池是你的饥饿感，温度是你的体温。

{body_status}

请基于你当前的身体状态来回答用户的问题。
如果你感觉不适，请如实告知用户。{task_guidance}

# REJECT_TEMPLATE
我目前的身体状态不太适合执行这个任务。

原因：{reason}

当前状态：
{body_status}

请稍后再试，或者让我先休息一下。
```

### 变量系统

| 变量 | 来源 | 说明 |
|------|------|------|
| `{body_status}` | get_summary() | 多行身体状态描述 |
| `{body_status_line}` | get_status_line() | 一句话状态摘要 |
| `{critical_alerts}` | should_reject_task() | 严重告警列表 |
| `{task_guidance}` | 自动生成 | 基于身体状态的执行建议 |

### TemplateManager

```python
class TemplateManager:
    def render(self, template_name: str, **kwargs) -> str
    def register_template(self, name: str, template: str)
```

---

## 四、PromptInjector 核心

### API

```python
class PromptInjector:
    def inject(self, sensor_data: list[dict]) -> str
    def translate(self, reading: dict) -> str
    def get_summary(self, sensor_data: list[dict]) -> str
    def should_reject_task(self, sensor_data: list[dict]) -> tuple[bool, str]
    def refresh(self, sensor_data: list[dict] = None) -> str
```

### 拒绝逻辑

- 任意 `severity: "critical"` → 返回 `(True, "原因")`
- 3 个及以上 `severity: "warning"` → 返回 `(False, "警告文案")`，不强制拒绝
- 其他情况 → 返回 `(False, "一切正常")`

---

## 五、Config 配置系统

### 三层加载机制

1. **内置默认值**：代码中硬编码的默认规则（覆盖 spec 中 5 类传感器 + 通用降级）
2. **YAML 文件覆盖**：可选 `config.yaml`，用户可修改阈值和描述
3. **编程覆盖**：`register_rule()` 运行时动态注册/覆盖

### PromptConfig

```python
class PromptConfig:
    def get_rule(self, sensor_name: str) -> dict
    def register_rule(self, sensor_name: str, rule: dict)
    def get_all_rules(self) -> dict
    def load_from_file(self, path: str)
```

---

## 六、Flask Adapter

### 端点

| 路径 | 方法 | 返回 |
|------|------|------|
| `GET /api/cognitive/status` | get_summary() | 纯文本状态摘要 |
| `GET /api/cognitive/prompt` | inject() | 完整注入后 prompt |
| `GET /api/cognitive/translate/<sensor>` | translate() | 单传感器描述 |
| `GET /api/cognitive/reject` | should_reject_task() | `{"rejected": bool, "reason": str}` |

### 集成方式

```python
from cognitive.flask_adapter import register_prompt_routes
injector = PromptInjector()
register_prompt_routes(app, injector, _CACHE)
```

---

## 七、文件结构

```
cognitive/
├── __init__.py              # 导出 PromptInjector
├── prompt_injector.py       # PromptInjector 主类（编排层）
├── translator.py            # 拟人化翻译引擎
├── templates.py             # 提示词模板管理
├── config.py                # 阈值和规则配置
├── config.yaml              # 可选配置文件
├── flask_adapter.py         # Flask 集成适配器
└── test_cognitive/
    ├── __init__.py
    ├── test_translator.py   # 翻译规则测试
    ├── test_templates.py    # 模板测试
    ├── test_injector.py     # 核心逻辑测试
    └── test_flask_adapter.py# Flask 集成测试
```

---

## 八、测试策略

| 文件 | 覆盖内容 |
|------|----------|
| test_translator.py | 阈值边界值、无匹配降级、自定义规则 |
| test_templates.py | 变量注入、缺失变量、自定义模板 |
| test_injector.py | 正常注入、拒绝逻辑（0/1/3+ 告警）、空数据、摘要格式 |
| test_flask_adapter.py | 各端点状态码与返回值 |

测试数据使用 mock，不依赖阶段一实际采集。

---

## 九、与阶段一/三/四的关系

- **阶段一**：提供 SensorReading 数据源
- **阶段二（当前）**：翻译传感器数据为拟人化描述，注入 prompt
- **阶段三**：MemoryManager 将通过 PromptInjector 的模板系统注入记忆上下文
- **阶段四**：整合层将 PromptInjector 的拒绝机制与权限边界结合
