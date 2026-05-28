# 阶段三：记忆管理系统（MemoryManager）— 设计文档

> 灵犀的数字记忆架构 — 滚动摘要 + 滑动窗口 + 黑匣子日志

---

## 一、架构概览

```
memory/
├── __init__.py              ← 导出 MemoryManager
├── memory_manager.py         ← 编排层核心类
├── token_counter.py          ← Token 计数器（策略模式）
├── llm_service.py            ← LLM API 抽象层
├── summarizer.py             ← 摘要生成 + 压缩逻辑
├── black_box.py              ← 黑匣子日志
└── storage.py                ← 持久化存储
```

### 核心数据流

```
User/System
    │
    ▼
┌─────────────────────────────────────────────────┐
│  MemoryManager (编排层)                          │
│  - add_message()        → storage.save_message() │
│                        → black_box.log()         │
│  - get_context(limit)   → check need_compress    │
│                        → load_summary()          │
│                        → assemble_context()      │
│  - compress(old_msgs)   → summarizer.compress()  │
│                        → storage.save_summary()  │
│                        → black_box.log()         │
└─────────────────────────────────────────────────┘
    │            │              │
    ▼            ▼              ▼
storage.py   black_box.py   summarizer.py
                              │
                              ▼
                         llm_service.py
                              │
                          ┌───┴───┐
                          ▼       ▼
                      OpenAI   Anthropic
```

### 关键异步流（压缩）

```
add_message → 标记需压缩(flag)
       ↓ (后台线程)
async_compressor → 检测 flag → summarizer.compress()
       ↓
storage.save_summary() → black_box.log("memory_compress")
```

### 依赖方向

严格单向依赖：`storage → black_box → summarizer → llm_service → token_counter`，全部被 `memory_manager` 编排。无循环依赖。

---

## 二、TokenCounter — Token 计数层

### 职责

根据模型类型选择不同的 Token 计数策略，对外提供统一的计数接口。

### 接口

```python
class TokenCounter:
    def count(self, text: str, model: str = "gpt-4") -> int
    def count_messages(self, messages: list[dict], model: str = "gpt-4") -> int
```

### 策略映射

| 模型 | 计数方式 | 精度 |
|------|----------|------|
| `gpt-4` / `gpt-3.5-turbo` | tiktoken `encoding_for_model()` | 精确 |
| `claude-3-*` | Anthropic SDK `count_tokens()` | 精确（需 API） |
| 其他/未知 | tiktoken `cl100k_base` 近似 | 估算 |

### 设计要点

- 首次调用时惰性初始化对应的编码/客户端
- Anthropic 计数若 API 调用失败，降级为 `cl100k_base` × 1.1 系数修正
- 缓存已加载的编码实例，避免重复加载
- 不依赖任何项目配置文件，通过 `model` 字符串参数自动选择策略

---

## 三、LLMService — LLM API 抽象层

### 职责

轻量级 LLM 抽象，专为对话摘要场景设计。不提供通用对话能力。

### 接口

```python
class LLMService:
    def summarize(self, messages: list[dict], max_tokens: int = 500) -> str
    def count_tokens(self, text: str) -> int
```

### 后端配置

```python
# OpenAI
LLMService(provider="openai", model="gpt-4", api_key="...")

# Anthropic
LLMService(provider="anthropic", model="claude-3-sonnet-20240229", api_key="...")
```

### 设计要点

- 只暴露 `summarize()` 和 `count_tokens()` 两个方法，不做通用 LLM 调用器
- API Key 通过构造函数传入，不隐式读取环境变量（调用方决定来源）
- 请求超时默认 30 秒
- 调用失败抛出 `LLMServiceError`，由上层处理重试/降级
- 每个 `summarize()` 调用使用独立的 system prompt 指示摘要风格

---

## 四、Summarizer — 摘要生成与压缩

### 职责

判断压缩时机、执行压缩、管理摘要链。

### 接口

```python
class Summarizer:
    def should_compress(self, total_tokens: int, token_limit: int, threshold: float = 0.8) -> bool
    def compress(self, messages: list[dict], strategy: str = "default") -> str
    def merge_summaries(self, old_summary: str, new_summary: str) -> str
```

### 压缩策略（`strategy` 参数）

| 策略 | 说明 |
|------|------|
| `default` | "将以下对话总结为核心要点，保留关键决策、问题和结论" |
| `brief` | "用一句话概括以下对话" |
| `detail` | "详细总结以下对话，保留技术细节和上下文" |

### 摘要链结构

```
第 1 轮: [消息1..N]                     → 摘要1
第 2 轮: [摘要1] + [消息N+1..M]        → 摘要2
第 3 轮: [摘要2] + [消息M+1..K]        → 摘要3
```

每次压缩时，将旧摘要 + 新消息送入 LLM，产生新摘要覆盖旧摘要。版本号递增。

### 上下文重装示例

```
原对话（Token 溢出）：
[消息1] [消息2] [消息3] [消息4] [消息5]
                                  ↑ 超过 80%

压缩后：
[摘要：消息1-3] + [消息4] + [消息5]
```

---

## 五、Storage — 持久化存储

### 职责

管理 memory_data/ 目录下的文件读写，包括消息历史和摘要。

### 接口

```python
class Storage:
    def save_message(self, message: dict) -> str
    def load_recent_messages(self, limit: int = 50) -> list[dict]
    def save_summary(self, summary: str, version: int)
    def load_summary(self) -> tuple[str, int] | None
    def clear_messages(self)
```

### 文件结构

```
memory_data/
├── messages.jsonl           ← 追加写，消息历史
├── summary.txt              ← 当前摘要文本
├── summary_version.txt      ← 摘要版本号（递增整数）
└── summary_history.jsonl    ← 摘要变更历史（可选）
```

### 设计要点

- `messages.jsonl`：尾追加，读取时从末尾倒读 N 行，适合滑动窗口
- `summary.txt` + `summary_version.txt`：覆盖写，读时按版本号判断是否存在
- 数据目录路径通过构造函数传入，默认 `./memory_data/`
- 首次使用时自动创建目录

---

## 六、BlackBox — 黑匣子日志

### 职责

记录系统运行过程中的关键事件，支持查询和分析。

### 接口

```python
class BlackBox:
    def log(self, event_type: str, data: dict) -> str
    def query(self, event_type: str = None, start: str = None, end: str = None,
              search: str = None, limit: int = 100) -> list[dict]
    def analyze(self, event_type: str = None) -> dict
```

### 日志格式

```jsonl
{"id": "bb_001", "timestamp": "2026-05-28T14:00:00.000Z", "event_type": "memory_compress", "data": {"before": 4500, "after": 800}}
{"id": "bb_002", "timestamp": "2026-05-28T14:01:00.000Z", "event_type": "message_added", "data": {"role": "user", "tokens": 120}}
{"id": "bb_003", "timestamp": "2026-05-28T14:02:00.000Z", "event_type": "sensor_update", "data": {"sensor": "cpu_temperature", "value": 85.0}}
```

### 文件滚动策略

- 单个文件达到 `max_size`（默认 10MB）时创建新文件
- 文件名：`blackbox_001.jsonl`, `blackbox_002.jsonl`, ...
- 最多保留 `max_files`（默认 10 个），超出删除最旧的

### 查询能力

- **时间范围**：`start` / `end` 参数过滤
- **事件类型**：`event_type` 精确匹配
- **关键字搜索**：`search` 在 data 字段中全文搜索
- **分页**：`limit` 控制返回条数，按时间倒序
- **组合**：以上条件可自由组合

### 分析能力

```python
# 全部分布
bb.analyze() → {"memory_compress": 12, "sensor_update": 45, ...}

# 指定类型
bb.analyze(event_type="memory_compress") → {"count": 12, "avg_before": 4200, "avg_after": 750}
```

---

## 七、MemoryManager — 编排层

### 职责

对外总入口，协调所有子组件，提供简洁的 API。

### 接口

```python
class MemoryManager:
    def __init__(self, config: dict = None)
    def add_message(self, role: str, content: str) -> str
    def get_context(self, token_limit: int) -> list[dict]
    def compress(self, old_messages: list[dict]) -> str
    def save_log(self, event_type: str, data: dict)
    def load_summary(self) -> str | None
    def clear_memory(self)
    def query_logs(self, **filters) -> list[dict]
```

### 内部行为

**add_message 流程：**
1. 构造消息字典 `{"role": role, "content": content, "timestamp": now}`
2. 调用 `storage.save_message()`
3. 调用 `black_box.log("message_added", stats)`
4. 计算当前总 Token（最近消息 + 摘要），若超过阈值标记需压缩

**get_context 流程：**
1. 若有待压缩标记，触发 `summarizer.compress()`（同步兜底）
2. 调用 `storage.load_summary()` 获取当前摘要
3. 调用 `storage.load_recent_messages()` 获取最近 N 轮
4. 组装为 `[{"role": "system", "content": f"摘要：{summary}"}, ...recent_messages]`
5. 若组装后仍超限，递归压缩最旧的段落

**compress 流程：**
1. 加载旧摘要（如有）
2. 调用 `summarizer.merge_summaries()` 合并新旧摘要
3. 调用 `storage.save_summary()` 写入新版
4. 调用 `black_box.log("memory_compress", stats)`

### AsyncCompressor — 后台压缩

```python
class AsyncCompressor:
    def start(self)          # 启动后台线程
    def stop(self)           # 优雅关闭
    def request(self)        # 标记需要压缩
```

- 后台线程默认每 60 秒检查一次待压缩标记
- 检测到标记后，调用 `summarizer.compress()` + `storage.save_summary()` + `black_box.log()`
- 通过 Event 或 Condition 实现线程间通信
- 支持 `stop()` 优雅退出（设置事件标志 + join）

---

## 八、配置系统

### MemoryManager 配置结构

```python
config = {
    "token_limit": 4096,           # 上下文窗口上限
    "compress_threshold": 0.8,     # 压缩触发阈值（80%）
    "compress_strategy": "default", # 摘要策略
    "data_dir": "./memory_data",   # 数据存储路径
    "llm": {
        "provider": "openai",      # 或 "anthropic"
        "model": "gpt-4",
        "api_key": "",             # 由调用方提供
        "timeout": 30
    },
    "blackbox": {
        "max_size_mb": 10,         # 单文件最大 10MB
        "max_files": 10            # 最多保留 10 个文件
    },
    "async_compress": {
        "enabled": True,           # 启用后台压缩
        "interval_seconds": 60     # 检查间隔
    }
}
```

---

## 九、与阶段二的集成

### 在 sensor_server.py 中的集成方式

```python
from cognitive import PromptInjector
from cognitive.flask_adapter import register_prompt_routes
from memory import MemoryManager

_injector = PromptInjector()
_memory = MemoryManager()

# 注册含记忆上下文的模板
_injector.template_mgr.register_template("memory_aware", """
你是「灵犀」，一个生活在电脑里的数字生命。

当前身体状态：
{body_status}

近期记忆摘要：
{memory_summary}

{task_guidance}
""")

# 扩展 prompt 端点
@app.route("/api/cognitive/prompt")
def get_prompt():
    summary = _memory.load_summary()
    sensor_data = get_sensor_data()
    return _injector.inject(sensor_data, memory_summary=summary)
```

### PromptInjector 兼容性

MemoryManager 不直接依赖 Phase 2 的任何内部模块，仅通过数据传递（字符串摘要）实现集成。这保证了两个模块可以独立开发、独立测试。

---

## 十、错误处理策略

| 异常场景 | 处理方式 |
|----------|----------|
| LLM API 调用超时 | 重试 1 次 → 失败则使用上次摘要，标记降级 |
| LLM API 返回空摘要 | 使用原始消息的前 500 字符作为降级摘要 |
| Token 计数失败 | 使用 len(text) / 4 估算 |
| 文件写入失败 | 抛出 StorageError，调用方处理 |
| 黑匣子写入失败 | 不影响主流程，打印警告 |
| 后台线程异常 | 捕获日志后自动重启 |

---

## 十一、测试策略

### 单元测试（预计 ~20 个）

| 测试文件 | 覆盖内容 | 预计数量 |
|----------|----------|----------|
| test_token_counter.py | gpt-4/claude/未知计数、空文本、消息列表 | 4 |
| test_llm_service.py | 摘要 mock、超时异常、配置切换 | 3 |
| test_summarizer.py | 阈值判断、压缩策略、边界值、摘要合并 | 4 |
| test_storage.py | 读写消息、摘要、清空、目录不存在 | 4 |
| test_black_box.py | 日志写入、查询过滤、分析统计、文件滚动 | 4 |
| test_memory_manager.py | 全流程编排、异步压缩、配置注入 | 3 |
| test_integration.py | 真实文件读写（tmpdir）、积压压缩 | 2 |

### 测试原则

- LLM API 调用使用 mock（`unittest.mock.patch`），不实际调用 API
- 文件操作用 `tempfile.TemporaryDirectory`，不污染真实目录
- 异步压缩使用 `threading.Event` 控制时序，避免竞态
- tiktoken 在测试中使用真实编码（已安装为依赖）
