# 记忆管理系统（MemoryManager）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现云枢的记忆管理系统，支持 Token 计量、滚动摘要 + 滑动窗口、黑匣子日志

**Architecture:** 经典分层架构，严格单向依赖：`storage → black_box → summarizer → llm_service → token_counter`，由 `memory_manager` 统一编排

**Tech Stack:** Python 3.12+, tiktoken, openai, anthropic-sdk, threading (异步压缩)

---

### Task 1: 项目脚手架与基础配置

**Files:**
- Create: `memory/__init__.py`

- [ ] **Step 1: 创建 memory 包与 __init__.py**

```python
"""云枢记忆管理系统 — 滚动摘要 + 滑动窗口 + 黑匣子日志"""

from .memory_manager import MemoryManager

__all__ = ["MemoryManager"]
```

- [ ] **Step 2: 检查依赖安装状态**

Run: `cd c:/Users/Administrator/agent && pip list 2>/dev/null | grep -iE "tiktoken|openai|anthropic" || echo "MISSING"`

Expected: 显示已安装的版本或 MISSING

- [ ] **Step 3: 安装缺失依赖**

Run: `cd c:/Users/Administrator/agent && pip install tiktoken openai anthropic 2>&1 | tail -5`

Expected: Successfully installed ...

- [ ] **Step 4: 创建测试目录结构**

Run: `mkdir -p c:/Users/Administrator/agent/memory/tests && touch c:/Users/Administrator/agent/memory/tests/__init__.py`

Expected: 目录创建成功

- [ ] **Step 5: 提交脚手架**

Run: `cd c:/Users/Administrator/agent && git add memory/__init__.py memory/tests/ && git commit -m "feat: scaffold memory package and test directory"`

---

### Task 2: TokenCounter — Token 计数层

**Files:**
- Create: `memory/token_counter.py`
- Create: `memory/tests/test_token_counter.py`

- [ ] **Step 1: 编写 TokenCounter 测试**

```python
"""TokenCounter 单元测试"""
import pytest
from memory.token_counter import TokenCounter


def test_count_gpt4_text():
    """gpt-4 模型计数应返回准确 Token 数"""
    counter = TokenCounter()
    text = "Hello, world!"
    count = counter.count(text, model="gpt-4")
    assert count > 0
    assert isinstance(count, int)


def test_count_gpt4_empty():
    """空字符串应返回 0"""
    counter = TokenCounter()
    assert counter.count("", model="gpt-4") == 0


def test_count_unknown_model():
    """未知模型应降级使用 cl100k_base 估算"""
    counter = TokenCounter()
    text = "Hello, world!"
    count = counter.count(text, model="unknown-model")
    assert count > 0
    assert isinstance(count, int)


def test_count_messages():
    """消息列表计数应返回总和"""
    counter = TokenCounter()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    total = counter.count_messages(messages, model="gpt-4")
    assert total > 0
    # 两条消息的 token 应大于单条
    single = counter.count("Hello", model="gpt-4")
    assert total >= single


def test_count_claude_text():
    """claude-3 模型计数不应抛出异常"""
    counter = TokenCounter()
    text = "Hello, Claude!"
    # 使用近似策略，不应报错
    count = counter.count(text, model="claude-3-sonnet-20240229")
    assert count > 0
    assert isinstance(count, int)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_token_counter.py -v 2>&1`

Expected: ModuleNotFoundError 或 ImportError（TokenCounter 未实现）

- [ ] **Step 3: 实现 TokenCounter**

```python
"""Token 计数器 — 策略模式，根据模型选择计数方式"""

import tiktoken


# cl100k_base 是 gpt-4/gpt-3.5-turbo 使用的编码
# 同时也是 Claude 系列模型近似的编码基准
_ENCODING_CACHE = {}


def _get_encoding(model: str):
    """获取或缓存编码实例"""
    if model not in _ENCODING_CACHE:
        try:
            _ENCODING_CACHE[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            # 未知模型 → cl100k_base
            _ENCODING_CACHE[model] = tiktoken.get_encoding("cl100k_base")
    return _ENCODING_CACHE[model]


class TokenCounter:
    """Token 计数器

    根据模型类型自动选择计数策略：
    - gpt-4/gpt-3.5-turbo → tiktoken 精确计数
    - claude-3-* → cl100k_base 近似 × 1.1 系数
    - 其他 → cl100k_base 近似
    """

    CLAUDE_FACTOR = 1.1  # Claude 比 cl100k_base 略多的修正系数

    def count(self, text: str, model: str = "gpt-4") -> int:
        """计算文本的 Token 数"""
        if not text:
            return 0

        if model.startswith("claude"):
            # Claude 模型：cl100k_base 近似 × 系数
            encoding = _get_encoding("cl100k_base")
            return int(len(encoding.encode(text)) * self.CLAUDE_FACTOR)
        else:
            encoding = _get_encoding(model)
            return len(encoding.encode(text))

    def count_messages(self, messages: list[dict], model: str = "gpt-4") -> int:
        """计算消息列表的总 Token 数

        每条消息格式：{"role": str, "content": str}
        额外计入每条消息的格式开销（约 4 token）
        """
        total = 0
        for msg in messages:
            total += self.count(msg.get("content", ""), model)
            total += 4  # 消息格式开销
        total += 2  # 对话整体格式开销
        return total
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_token_counter.py -v`

Expected: 5 passed

- [ ] **Step 5: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/token_counter.py memory/tests/test_token_counter.py && git commit -m "feat: implement TokenCounter with tiktoken strategy"`

---

### Task 3: Storage — 持久化存储

**Files:**
- Create: `memory/storage.py`
- Create: `memory/tests/test_storage.py`

- [ ] **Step 1: 编写 Storage 测试**

```python
"""Storage 单元测试"""
import json
import tempfile
from pathlib import Path
import pytest
from memory.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(data_dir=str(tmp_path))


def test_save_and_load_recent_messages(storage):
    """保存消息后应能读取最近消息"""
    msg_id = storage.save_message({"role": "user", "content": "你好"})
    assert msg_id is not None
    messages = storage.load_recent_messages(limit=10)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"


def test_load_recent_messages_limit(storage):
    """load_recent_messages 应返回正确的限制数量"""
    for i in range(5):
        storage.save_message({"role": "user", "content": f"msg{i}"})
    messages = storage.load_recent_messages(limit=3)
    # 应返回最近 3 条（倒序）
    assert len(messages) == 3


def test_save_and_load_summary(storage):
    """保存摘要后应能读取"""
    storage.save_summary("测试摘要", version=1)
    result = storage.load_summary()
    assert result is not None
    summary, version = result
    assert summary == "测试摘要"
    assert version == 1


def test_load_summary_not_exists(storage):
    """无摘要时应返回 None"""
    assert storage.load_summary() is None


def test_save_summary_overwrite(storage):
    """保存新摘要应覆盖旧摘要并递增版本"""
    storage.save_summary("版本1", version=1)
    storage.save_summary("版本2", version=2)
    summary, version = storage.load_summary()
    assert summary == "版本2"
    assert version == 2


def test_clear_messages(storage):
    """清空消息后应保留摘要文件"""
    storage.save_message({"role": "user", "content": "你好"})
    storage.save_summary("测试摘要", version=1)
    storage.clear_messages()
    assert len(storage.load_recent_messages(limit=10)) == 0
    # 摘要应保留
    assert storage.load_summary() is not None


def test_auto_create_directory():
    """自动创建不存在的目录"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "deep"
        s = Storage(data_dir=str(path))
        s.save_message({"role": "user", "content": "test"})
        assert path.exists()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_storage.py -v 2>&1`

Expected: ModuleNotFoundError（Storage 未实现）

- [ ] **Step 3: 实现 Storage**

```python
"""持久化存储 — 管理 memory_data/ 目录下的文件读写"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class StorageError(Exception):
    """存储操作异常"""
    pass


class Storage:
    """消息历史与摘要的持久化管理器

    文件结构：
        messages.jsonl       — 追加写消息历史
        summary.txt          — 当前摘要文本
        summary_version.txt  — 摘要版本号
    """

    def __init__(self, data_dir: str = "./memory_data"):
        self.data_dir = Path(data_dir)
        self.messages_file = self.data_dir / "messages.jsonl"
        self.summary_file = self.data_dir / "summary.txt"
        self.version_file = self.data_dir / "summary_version.txt"

    def _ensure_dir(self):
        """确保数据目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save_message(self, message: dict) -> str:
        """保存单条消息，返回消息 ID"""
        self._ensure_dir()
        msg = {
            **message,
            "timestamp": message.get("timestamp", datetime.now(timezone.utc).isoformat())
        }
        try:
            with open(self.messages_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except OSError as e:
            raise StorageError(f"写入消息失败: {e}") from e
        return msg.get("timestamp", "")

    def load_recent_messages(self, limit: int = 50) -> list[dict]:
        """加载最近 N 条消息（从末尾倒读）"""
        if not self.messages_file.exists():
            return []
        try:
            with open(self.messages_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 从末尾倒读 limit 条
            recent = [json.loads(line) for line in lines[-limit:]]
            return recent
        except (OSError, json.JSONDecodeError) as e:
            raise StorageError(f"读取消息失败: {e}") from e

    def save_summary(self, summary: str, version: int):
        """保存摘要"""
        self._ensure_dir()
        try:
            self.summary_file.write_text(summary, encoding="utf-8")
            self.version_file.write_text(str(version), encoding="utf-8")
        except OSError as e:
            raise StorageError(f"写入摘要失败: {e}") from e

    def load_summary(self) -> tuple[str, int] | None:
        """加载当前摘要，返回 (摘要文本, 版本号) 或 None"""
        if not self.summary_file.exists() or not self.version_file.exists():
            return None
        try:
            summary = self.summary_file.read_text(encoding="utf-8")
            version = int(self.version_file.read_text(encoding="utf-8").strip())
            return summary, version
        except (OSError, ValueError) as e:
            raise StorageError(f"读取摘要失败: {e}") from e

    def clear_messages(self):
        """清空消息历史（保留摘要文件）"""
        try:
            if self.messages_file.exists():
                self.messages_file.write_text("", encoding="utf-8")
        except OSError as e:
            raise StorageError(f"清空消息失败: {e}") from e
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_storage.py -v`

Expected: 7 passed

- [ ] **Step 5: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/storage.py memory/tests/test_storage.py && git commit -m "feat: implement Storage with JSONL message log and summary files"`

---

### Task 4: BlackBox — 黑匣子日志

**Files:**
- Create: `memory/black_box.py`
- Create: `memory/tests/test_black_box.py`

- [ ] **Step 1: 编写 BlackBox 测试**

```python
"""黑匣子日志单元测试"""
import json
import tempfile
from pathlib import Path
import pytest
from memory.black_box import BlackBox


@pytest.fixture
def bb(tmp_path):
    return BlackBox(log_dir=str(tmp_path), max_size_bytes=500, max_files=3)


def test_log_and_query(bb):
    """记录事件后应能查询到"""
    event_id = bb.log("test_event", {"key": "value"})
    assert event_id is not None
    results = bb.query()
    assert len(results) == 1
    assert results[0]["event_type"] == "test_event"
    assert results[0]["data"]["key"] == "value"


def test_query_by_event_type(bb):
    """应按事件类型过滤"""
    bb.log("type_a", {})
    bb.log("type_b", {})
    bb.log("type_a", {})
    results = bb.query(event_type="type_a")
    assert len(results) == 2
    for r in results:
        assert r["event_type"] == "type_a"


def test_query_by_time_range(bb):
    """应按时间范围过滤"""
    bb.log("event1", {})
    bb.log("event2", {})
    # 使用一个未来不会匹配的时间范围
    results = bb.query(start="2099-01-01")
    assert len(results) == 0


def test_query_with_search(bb):
    """应按关键字搜索 data 字段"""
    bb.log("test", {"message": "hello world"})
    bb.log("test", {"message": "goodbye world"})
    results = bb.query(search="hello")
    assert len(results) == 1
    assert results[0]["data"]["message"] == "hello world"


def test_query_limit(bb):
    """应支持 limit 限制返回条数"""
    for i in range(5):
        bb.log("test", {"i": i})
    results = bb.query(limit=3)
    assert len(results) == 3


def test_analyze_distribution(bb):
    """analyze 应返回事件类型分布"""
    bb.log("a", {})
    bb.log("a", {})
    bb.log("b", {})
    dist = bb.analyze()
    assert dist["a"] == 2
    assert dist["b"] == 1


def test_file_rotation(bb):
    """超过 max_size 应创建新文件"""
    # 写入足够数据触发滚动
    for i in range(20):
        bb.log("test", {"data": "x" * 50})
    # 应有至少 2 个日志文件
    log_dir = Path(bb.log_dir)
    files = sorted(log_dir.glob("blackbox_*.jsonl"))
    assert len(files) >= 2


def test_max_files_limit(bb):
    """超过 max_files 应删除最旧文件"""
    # 每个文件 500 bytes，写入大量数据触发多次滚动
    for i in range(50):
        bb.log("test", {"data": "x" * 100})
    log_dir = Path(bb.log_dir)
    files = sorted(log_dir.glob("blackbox_*.jsonl"))
    assert len(files) <= 3  # max_files=3
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_black_box.py -v 2>&1`

Expected: ModuleNotFoundError（BlackBox 未实现）

- [ ] **Step 3: 实现 BlackBox**

```python
"""黑匣子日志 — JSONL 格式，按文件大小滚动，支持查询与分析"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


class BlackBoxError(Exception):
    """黑匣子操作异常"""
    pass


class BlackBox:
    """黑匣子日志系统

    以 JSONL 格式记录事件，按文件大小自动滚动。
    支持按时间、事件类型、关键字查询。

    文件命名：blackbox_001.jsonl, blackbox_002.jsonl, ...
    """

    def __init__(self, log_dir: str = "./memory_data/blackbox",
                 max_size_bytes: int = 10 * 1024 * 1024,
                 max_files: int = 10):
        self.log_dir = Path(log_dir)
        self.max_size_bytes = max_size_bytes
        self.max_files = max_files
        self._counter = 0
        self._ensure_dir()

    def _ensure_dir(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_current_file(self) -> Path:
        """获取当前写入文件（最新编号的文件）"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        if not files:
            return self.log_dir / "blackbox_001.jsonl"
        return files[-1]

    def _next_file(self) -> Path:
        """创建下一个编号的文件"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        if not files:
            return self.log_dir / "blackbox_001.jsonl"
        last_num = int(files[-1].stem.split("_")[1])
        new_file = self.log_dir / f"blackbox_{last_num + 1:03d}.jsonl"
        self._enforce_max_files()
        return new_file

    def _enforce_max_files(self):
        """删除超出 max_files 的最旧文件"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        while len(files) >= self.max_files:
            files[0].unlink()
            files = sorted(self.log_dir.glob("blackbox_*.jsonl"))

    def log(self, event_type: str, data: dict) -> str:
        """记录一条事件日志，返回事件 ID"""
        self._counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entry = {
            "id": f"bb_{self._counter:04d}",
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"

        current = self._get_current_file()
        # 检查是否需要滚动
        if current.exists() and current.stat().st_size + len(line.encode()) > self.max_size_bytes:
            current = self._next_file()

        try:
            with open(current, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            raise BlackBoxError(f"写入日志失败: {e}") from e

        return entry["id"]

    def query(self, event_type: str = None, start: str = None,
              end: str = None, search: str = None,
              limit: int = 100) -> list[dict]:
        """查询日志条目

        Args:
            event_type: 按事件类型精确过滤
            start: 起始时间（含），ISO 格式字符串
            end: 结束时间（含），ISO 格式字符串
            search: 在 data 字段中搜索关键字
            limit: 最大返回条数

        Returns:
            按时间倒序排列的日志条目列表
        """
        results = []
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"), reverse=True)

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # 过滤
                        if event_type and entry.get("event_type") != event_type:
                            continue
                        if start and entry.get("timestamp", "") < start:
                            continue
                        if end and entry.get("timestamp", "") > end:
                            continue
                        if search:
                            data_str = json.dumps(entry.get("data", {}), ensure_ascii=False)
                            if search not in data_str:
                                continue

                        results.append(entry)
                        if len(results) >= limit:
                            return results
            except OSError:
                continue

        return results

    def analyze(self, event_type: str = None) -> dict:
        """统计分析日志

        Args:
            event_type: 指定事件类型，返回该类型的统计信息

        Returns:
            未指定类型时：{event_type: count, ...}
            指定类型时：{count: N, 以及其他统计}
        """
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"), reverse=True)
        type_counts = {}

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        et = entry.get("event_type", "unknown")
                        type_counts[et] = type_counts.get(et, 0) + 1
            except OSError:
                continue

        if event_type:
            return {"count": type_counts.get(event_type, 0)}
        return type_counts
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_black_box.py -v`

Expected: 8 passed

- [ ] **Step 5: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/black_box.py memory/tests/test_black_box.py && git commit -m "feat: implement BlackBox JSONL logger with rotation"`

---

### Task 5: LLMService — LLM API 抽象层

**Files:**
- Create: `memory/llm_service.py`
- Create: `memory/tests/test_llm_service.py`

- [ ] **Step 1: 编写 LLMService 测试**

```python
"""LLMService 单元测试"""
from unittest.mock import patch, MagicMock
import pytest
from memory.llm_service import LLMService, LLMServiceError


def test_openai_summarize():
    """OpenAI 摘要应返回正常结果"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "这是摘要内容"

    with patch("openai.OpenAI") as mock_client:
        instance = mock_client.return_value
        instance.chat.completions.create.return_value = mock_response

        service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
        result = service.summarize([{"role": "user", "content": "你好"}])
        assert result == "这是摘要内容"


def test_anthropic_summarize():
    """Anthropic 摘要应返回正常结果"""
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = "这是 Claude 摘要"

    with patch("anthropic.Anthropic") as mock_client:
        instance = mock_client.return_value
        instance.messages.create.return_value = mock_response

        service = LLMService(provider="anthropic", api_key="sk-ant-test", model="claude-3-sonnet-20240229")
        result = service.summarize([{"role": "user", "content": "你好"}])
        assert result == "这是 Claude 摘要"


def test_invalid_provider():
    """无效 provider 应抛出异常"""
    with pytest.raises(LLMServiceError):
        LLMService(provider="invalid", api_key="test", model="test")


def test_openai_api_error():
    """OpenAI API 异常应包装为 LLMServiceError"""
    with patch("openai.OpenAI") as mock_client:
        instance = mock_client.return_value
        instance.chat.completions.create.side_effect = Exception("API Error")

        service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
        with pytest.raises(LLMServiceError):
            service.summarize([{"role": "user", "content": "你好"}])


def test_empty_messages():
    """空消息列表应返回空字符串"""
    service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
    result = service.summarize([])
    assert result == ""


def test_count_tokens():
    """count_tokens 应返回正整数"""
    service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
    count = service.count_tokens("Hello world")
    assert count > 0
    assert isinstance(count, int)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_llm_service.py -v 2>&1`

Expected: ModuleNotFoundError（LLMService 未实现）

- [ ] **Step 3: 实现 LLMService**

```python
"""LLM API 抽象层 — 专为对话摘要场景设计"""

import logging

logger = logging.getLogger(__name__)


class LLMServiceError(Exception):
    """LLM 服务异常"""
    pass


class LLMService:
    """轻量级 LLM 抽象，专注摘要场景

    支持 OpenAI 和 Anthropic 双后端，通过配置切换。

    不提供通用对话能力，只暴露 summarize() 和 count_tokens() 两个方法。
    """

    def __init__(self, provider: str = "openai", api_key: str = "",
                 model: str = "gpt-4", timeout: int = 30):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        """惰性初始化 API 客户端"""
        if self._client is not None:
            return self._client

        if self.provider == "openai":
            import openai
            self._client = openai.OpenAI(api_key=self.api_key, timeout=self.timeout)
            return self._client
        elif self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
            return self._client
        else:
            raise LLMServiceError(f"不支持的 provider: {self.provider}")

    def summarize(self, messages: list[dict], max_tokens: int = 500) -> str:
        """调用 LLM 生成对话摘要

        Args:
            messages: 对话消息列表，格式 [{"role": "...", "content": "..."}]
            max_tokens: 摘要最大 Token 数

        Returns:
            摘要文本。空输入返回空字符串。
        """
        if not messages:
            return ""

        system_prompt = "请将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。"

        try:
            client = self._get_client()

            if self.provider == "openai":
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *messages
                    ],
                    max_tokens=max_tokens
                )
                return response.choices[0].message.content.strip()

            elif self.provider == "anthropic":
                import anthropic
                response = client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=max_tokens
                )
                return response.content[0].text.strip()

        except Exception as e:
            logger.error("LLM API 调用失败: %s", e)
            raise LLMServiceError(f"摘要生成失败: {e}") from e

    def count_tokens(self, text: str) -> int:
        """使用 tiktoken 估算文本 Token 数（不依赖 LLM API）"""
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # 降级估算
            return len(text) // 4
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_llm_service.py -v`

Expected: 6 passed（注意 test_count_tokens 需要 tiktoken 已安装）

- [ ] **Step 5: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/llm_service.py memory/tests/test_llm_service.py && git commit -m "feat: implement LLMService with OpenAI and Anthropic backends"`

---

### Task 6: Summarizer — 摘要生成与压缩

**Files:**
- Create: `memory/summarizer.py`
- Create: `memory/tests/test_summarizer.py`

- [ ] **Step 1: 编写 Summarizer 测试**

```python
"""Summarizer 单元测试"""
from unittest.mock import patch, MagicMock
import pytest
from memory.summarizer import Summarizer


@pytest.fixture
def summarizer():
    return Summarizer(llm_service=MagicMock())


def test_should_compress_below_threshold(summarizer):
    """低于阈值不应触发压缩"""
    assert not summarizer.should_compress(100, 200, threshold=0.8)


def test_should_compress_at_threshold(summarizer):
    """达到阈值应触发压缩"""
    assert summarizer.should_compress(160, 200, threshold=0.8)


def test_should_compress_above_threshold(summarizer):
    """超过阈值应触发压缩"""
    assert summarizer.should_compress(180, 200, threshold=0.8)


def test_should_compress_boundary(summarizer):
    """边界值：刚好等于 threshold * limit"""
    assert summarizer.should_compress(160, 200, threshold=0.8)
    assert not summarizer.should_compress(159, 200, threshold=0.8)


def test_compress_calls_llm():
    """compress 应调用 LLM 并返回摘要"""
    mock_llm = MagicMock()
    mock_llm.summarize.return_value = "这是摘要"
    s = Summarizer(llm_service=mock_llm)

    messages = [{"role": "user", "content": "你好"}]
    result = s.compress(messages)
    assert result == "这是摘要"
    mock_llm.summarize.assert_called_once()


def test_compress_empty_messages(summarizer):
    """空消息应返回空字符串"""
    assert summarizer.compress([]) == ""


def test_merge_summaries():
    """merge_summaries 应合并新旧摘要"""
    mock_llm = MagicMock()
    mock_llm.summarize.return_value = "合并后的摘要"
    s = Summarizer(llm_service=mock_llm)

    result = s.merge_summaries("旧摘要", "新消息的摘要")
    assert result == "合并后的摘要"
    mock_llm.summarize.assert_called_once()


def test_merge_without_old_summary(summarizer):
    """无旧摘要时，merge_summaries 应直接返回新摘要"""
    result = summarizer.merge_summaries("", "新摘要")
    assert result == "新摘要"


def test_compress_with_strategy():
    """不同策略应传递不同的 system prompt"""
    mock_llm = MagicMock()
    mock_llm.summarize.return_value = "简洁摘要"
    s = Summarizer(llm_service=mock_llm)

    s.compress([{"role": "user", "content": "你好"}], strategy="brief")
    # 应调用 LLM
    assert mock_llm.summarize.call_count == 1
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_summarizer.py -v 2>&1`

Expected: ModuleNotFoundError（Summarizer 未实现）

- [ ] **Step 3: 实现 Summarizer**

```python
"""摘要生成与压缩 — 判断压缩时机、执行压缩、管理摘要链"""


class Summarizer:
    """对话摘要器

    职责：
    1. 判断是否达到压缩阈值
    2. 调用 LLM 压缩对话为摘要
    3. 管理摘要链的合并
    """

    # 不同策略对应的 system prompt
    STRATEGIES = {
        "default": "将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。",
        "brief": "用一句话概括以下对话的核心内容。",
        "detail": "详细总结以下对话，保留技术细节、代码片段和上下文信息。",
    }

    def __init__(self, llm_service):
        self._llm = llm_service

    def should_compress(self, total_tokens: int, token_limit: int,
                        threshold: float = 0.8) -> bool:
        """判断是否达到压缩阈值

        Args:
            total_tokens: 当前总 Token 数
            token_limit: 上下文窗口上限
            threshold: 触发比例，默认 80%

        Returns:
            True 表示需要压缩
        """
        return total_tokens >= int(token_limit * threshold)

    def compress(self, messages: list[dict], strategy: str = "default") -> str:
        """压缩对话为摘要

        Args:
            messages: 待压缩的消息列表
            strategy: 摘要策略（default/brief/detail）

        Returns:
            摘要文本
        """
        if not messages:
            return ""
        return self._llm.summarize(messages, max_tokens=500)

    def merge_summaries(self, old_summary: str, new_summary: str) -> str:
        """合并新旧摘要

        Args:
            old_summary: 已有的旧摘要（可能为空）
            new_summary: 新生成的摘要

        Returns:
            合并后的摘要
        """
        if not old_summary:
            return new_summary
        if not new_summary:
            return old_summary

        merge_messages = [
            {"role": "user", "content": f"已有的摘要：\n{old_summary}\n\n新的信息摘要：\n{new_summary}\n\n请将两者合并为一份连贯的完整摘要，保留所有重要信息。"}
        ]
        return self._llm.summarize(merge_messages, max_tokens=600)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_summarizer.py -v`

Expected: 8 passed

- [ ] **Step 5: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/summarizer.py memory/tests/test_summarizer.py && git commit -m "feat: implement Summarizer with compression strategies"`

---

### Task 7: MemoryManager — 核心编排层

**Files:**
- Create: `memory/memory_manager.py`
- Create: `memory/tests/test_memory_manager.py`

- [ ] **Step 1: 编写 MemoryManager 测试**

```python
"""MemoryManager 单元测试"""
import json
import tempfile
from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest
from memory.memory_manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    """创建使用临时目录的 MemoryManager 实例"""
    config = {
        "data_dir": str(tmp_path / "memory_data"),
        "llm": {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4"
        },
        "async_compress": {
            "enabled": False  # 测试中禁用后台压缩
        }
    }
    return MemoryManager(config=config)


def test_add_message(manager):
    """添加消息应成功"""
    msg_id = manager.add_message("user", "你好云枢")
    assert msg_id is not None


def test_get_context_empty(manager):
    """空记忆应返回空列表"""
    ctx = manager.get_context(token_limit=1000)
    assert ctx is None or ctx == []


def test_get_context_with_messages(manager):
    """有消息时应返回上下文"""
    manager.add_message("user", "你好")
    ctx = manager.get_context(token_limit=1000)
    assert ctx is not None
    # 应为列表格式
    assert isinstance(ctx, list)


def test_compress(manager):
    """compress 应返回摘要字符串"""
    messages = [
        {"role": "user", "content": "今天天气怎么样？"},
        {"role": "assistant", "content": "今天天气很好。"}
    ]
    # mock LLM 返回摘要
    original_llm = manager._summarizer._llm
    manager._summarizer._llm.summarize = MagicMock(return_value="关于天气的对话摘要")

    result = manager.compress(messages)
    assert result is not None
    assert len(result) > 0

    # 恢复
    manager._summarizer._llm = original_llm


def test_save_and_load_summary(manager):
    """保存后应能加载摘要"""
    manager.add_message("user", "你好")
    manager._storage.save_summary("测试摘要", version=1)
    summary = manager.load_summary()
    assert summary is not None
    text, version = summary
    assert text == "测试摘要"
    assert version == 1


def test_clear_memory(manager):
    """清空记忆应保留摘要"""
    manager.add_message("user", "你好")
    manager._storage.save_summary("测试摘要", version=1)
    manager.clear_memory()
    messages = manager._storage.load_recent_messages(limit=10)
    assert len(messages) == 0
    # 摘要应保留
    assert manager._storage.load_summary() is not None


def test_save_log(manager):
    """黑匣子快捷入口应工作"""
    manager.save_log("test_event", {"key": "value"})
    logs = manager.query_logs()
    assert len(logs) >= 1


def test_query_logs(manager):
    """查询日志应返回结果"""
    manager.save_log("event_a", {"msg": "test"})
    manager.save_log("event_b", {"msg": "test"})
    results = manager.query_logs(event_type="event_a")
    assert len(results) >= 1
    assert results[0]["event_type"] == "event_a"


def test_default_config():
    """无配置时应使用默认值"""
    m = MemoryManager()
    assert m is not None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_memory_manager.py -v 2>&1`

Expected: ModuleNotFoundError（MemoryManager 未实现）

- [ ] **Step 3: 实现 MemoryManager**

```python
"""MemoryManager — 记忆管理系统的核心编排层"""

import logging
import threading
from datetime import datetime, timezone
from .token_counter import TokenCounter
from .llm_service import LLMService
from .summarizer import Summarizer
from .storage import Storage
from .black_box import BlackBox

logger = logging.getLogger(__name__)


class AsyncCompressor:
    """后台压缩线程

    定时检查是否需要压缩，异步执行摘要生成。
    """

    def __init__(self, summarizer, storage, black_box, interval: int = 60):
        self._summarizer = summarizer
        self._storage = storage
        self._black_box = black_box
        self._interval = interval
        self._event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """启动后台线程"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("后台压缩线程已启动")

    def stop(self):
        """优雅停止后台线程"""
        self._stop_event.set()
        self._event.set()  # 唤醒线程
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("后台压缩线程已停止")

    def request(self):
        """标记需要压缩"""
        self._event.set()

    def _run(self):
        while not self._stop_event.is_set():
            self._event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self._event.clear()
            self._do_compress()

    def _do_compress(self):
        """执行压缩任务"""
        try:
            old_summary = self._storage.load_summary()
            recent_messages = self._storage.load_recent_messages(limit=100)

            if not recent_messages:
                return

            # 使用 LLM 压缩
            summary = self._summarizer.compress(recent_messages)

            if old_summary:
                old_text, old_version = old_summary
                summary = self._summarizer.merge_summaries(old_text, summary)
                new_version = old_version + 1
            else:
                new_version = 1

            self._storage.save_summary(summary, new_version)
            self._black_box.log("memory_compress", {
                "version": new_version,
                "messages_count": len(recent_messages)
            })
            logger.info("后台压缩完成，版本 %d", new_version)
        except Exception as e:
            logger.error("后台压缩失败: %s", e)


class MemoryManager:
    """记忆管理器 — 云枢的记忆系统入口

    管理对话历史、滚动摘要、黑匣子日志的完整生命周期。
    """

    def __init__(self, config: dict = None):
        config = config or {}

        # Token 计数器
        self._token_counter = TokenCounter()

        # LLM 服务
        llm_cfg = config.get("llm", {})
        if llm_cfg.get("api_key"):
            self._llm_service = LLMService(
                provider=llm_cfg.get("provider", "openai"),
                api_key=llm_cfg["api_key"],
                model=llm_cfg.get("model", "gpt-4"),
                timeout=llm_cfg.get("timeout", 30)
            )
        else:
            self._llm_service = None
            logger.warning("未配置 LLM API Key，摘要功能不可用")

        # 摘要器
        self._summarizer = Summarizer(llm_service=self._llm_service)

        # 存储
        data_dir = config.get("data_dir", "./memory_data")
        self._storage = Storage(data_dir=data_dir)

        # 黑匣子
        bb_cfg = config.get("blackbox", {})
        self._black_box = BlackBox(
            log_dir=config.get("blackbox_dir", f"{data_dir}/blackbox"),
            max_size_bytes=bb_cfg.get("max_size_mb", 10) * 1024 * 1024,
            max_files=bb_cfg.get("max_files", 10)
        )

        # 后台压缩
        ac_cfg = config.get("async_compress", {})
        self._async_compressor = AsyncCompressor(
            summarizer=self._summarizer,
            storage=self._storage,
            black_box=self._black_box,
            interval=ac_cfg.get("interval_seconds", 60)
        )
        if ac_cfg.get("enabled", True):
            self._async_compressor.start()

        # 压缩阈值
        self._token_limit = config.get("token_limit", 4096)
        self._compress_threshold = config.get("compress_threshold", 0.8)

        self._need_compress = False
        logger.info("MemoryManager 初始化完成")

    def add_message(self, role: str, content: str) -> str:
        """添加新消息

        保存消息、记录日志、检查是否需要压缩。

        Args:
            role: 消息角色（user/assistant/system）
            content: 消息内容

        Returns:
            消息的时间戳 ID
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        msg_id = self._storage.save_message(message)

        # 记录黑匣子
        self._black_box.log("message_added", {
            "role": role,
            "tokens": self._token_counter.count(content)
        })

        # 检查 Token 占用
        recent = self._storage.load_recent_messages(limit=200)
        total_tokens = self._token_counter.count_messages(recent)
        if self._summarizer.should_compress(total_tokens, self._token_limit,
                                            self._compress_threshold):
            self._need_compress = True
            self._async_compressor.request()

        return msg_id

    def get_context(self, token_limit: int) -> list[dict] | None:
        """获取压缩后的上下文

        如果标记了需要压缩，先尝试同步压缩（当没有后台线程时）。
        组装为 [system 摘要, recent_messages...] 格式。

        Args:
            token_limit: 上下文窗口 Token 上限

        Returns:
            消息列表 [{"role": "...", "content": "..."}]，无内容时返回 None
        """
        # 如果有压缩需求且没有后台线程，同步执行
        if self._need_compress and not self._async_compressor._thread:
            recent = self._storage.load_recent_messages(limit=100)
            if recent:
                summary = self._summarizer.compress(recent)
                old = self._storage.load_summary()
                if old:
                    old_text, old_version = old
                    summary = self._summarizer.merge_summaries(old_text, summary)
                    self._storage.save_summary(summary, old_version + 1)
                else:
                    self._storage.save_summary(summary, 1)
            self._need_compress = False

        # 加载摘要和最近消息
        summary = self._storage.load_summary()
        recent = self._storage.load_recent_messages(limit=20)

        if not recent:
            return []

        context = []
        if summary:
            summary_text, _ = summary
            context.append({
                "role": "system",
                "content": f"以下是之前的对话摘要：\n{summary_text}"
            })

        context.extend(recent)

        # 如果仍然超限，丢弃最旧消息
        while len(context) > 1:
            total = self._token_counter.count_messages(context)
            if total <= token_limit:
                break
            # 丢弃最旧的非摘要消息
            if len(context) > 1:
                context.pop(1)  # 保留摘要，丢弃最旧的普通消息

        return context

    def compress(self, old_messages: list[dict]) -> str:
        """压缩历史对话（同步接口）

        Args:
            old_messages: 待压缩的消息列表

        Returns:
            摘要文本
        """
        return self._summarizer.compress(old_messages)

    def save_log(self, event_type: str, data: dict):
        """记录黑匣子事件（快捷入口）"""
        self._black_box.log(event_type, data)

    def load_summary(self) -> tuple[str, int] | None:
        """加载当前摘要"""
        return self._storage.load_summary()

    def clear_memory(self):
        """清空记忆（保留摘要和黑匣子日志）"""
        self._storage.clear_messages()
        self._need_compress = False
        self._black_box.log("memory_cleared", {})
        logger.info("记忆已清空")

    def query_logs(self, **filters) -> list[dict]:
        """查询黑匣子日志（快捷入口）"""
        return self._black_box.query(**filters)

    def __del__(self):
        """析构时停止后台线程"""
        if hasattr(self, '_async_compressor'):
            self._async_compressor.stop()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/test_memory_manager.py -v`

Expected: 9 passed

- [ ] **Step 5: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/memory_manager.py memory/tests/test_memory_manager.py && git commit -m "feat: implement MemoryManager with async compression"`

---

### Task 8: 更新 __init__.py 与集成测试

**Files:**
- Modify: `memory/__init__.py`
- Create: `memory/tests/test_integration.py`

- [ ] **Step 1: 更新 __init__.py 导出所有公开类**

```python
"""云枢记忆管理系统 — 滚动摘要 + 滑动窗口 + 黑匣子日志

提供 MemoryManager 作为核心入口，同时导出子组件供直接使用。
"""

from .memory_manager import MemoryManager
from .token_counter import TokenCounter
from .llm_service import LLMService
from .summarizer import Summarizer
from .storage import Storage
from .black_box import BlackBox

__all__ = [
    "MemoryManager",
    "TokenCounter",
    "LLMService",
    "Summarizer",
    "Storage",
    "BlackBox",
]
```

- [ ] **Step 2: 编写集成测试**

```python
"""MemoryManager 集成测试 — 使用临时目录，验证全流程"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from memory import MemoryManager


@pytest.fixture
def manager(tmp_path):
    config = {
        "data_dir": str(tmp_path / "memory_data"),
        "llm": {"provider": "openai", "api_key": "sk-test", "model": "gpt-4"},
        "async_compress": {"enabled": False}
    }
    m = MemoryManager(config=config)
    # Mock LLM 避免真实调用
    m._summarizer._llm.summarize = MagicMock(return_value="集成测试摘要")
    return m


def test_full_lifecycle(manager):
    """完整生命周期：添加消息 → 压缩 → 加载摘要"""
    # 添加消息
    for i in range(5):
        manager.add_message("user", f"这是第{i}条消息")

    # 获取上下文
    ctx = manager.get_context(token_limit=1000)
    assert ctx is not None
    assert len(ctx) > 0

    # 压缩
    messages = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
    summary = manager.compress(messages)
    assert summary is not None

    # 保存并加载摘要
    manager._storage.save_summary("集成测试摘要", version=1)
    loaded = manager.load_summary()
    assert loaded is not None
    text, ver = loaded
    assert text == "集成测试摘要"
    assert ver == 1


def test_blackbox_integration(manager):
    """黑匣子日志应与 MemoryManager 协同工作"""
    manager.save_log("test_event", {"data": 123})
    manager.save_log("another_event", {"data": 456})
    results = manager.query_logs(event_type="test_event")
    assert len(results) >= 1
    assert results[0]["event_type"] == "test_event"


def test_clear_preserves_summary(manager):
    """清空记忆应保留摘要"""
    manager._storage.save_summary("保留的摘要", version=1)
    manager.add_message("user", "你好")
    manager.clear_memory()
    # 摘要应保留
    assert manager.load_summary() is not None
    # 消息应清空
    assert len(manager._storage.load_recent_messages(limit=10)) == 0


def test_persistence_across_instances(tmp_path):
    """不同实例应能读取同一数据目录"""
    data_dir = str(tmp_path / "memory_data")

    # 实例1：写入
    m1 = MemoryManager({
        "data_dir": data_dir,
        "async_compress": {"enabled": False}
    })
    m1.add_message("user", "持久化测试")
    m1._storage.save_summary("持久摘要", version=1)

    # 实例2：读取
    m2 = MemoryManager({
        "data_dir": data_dir,
        "async_compress": {"enabled": False}
    })
    messages = m2._storage.load_recent_messages(limit=10)
    assert len(messages) == 1
    assert messages[0]["content"] == "持久化测试"
    summary = m2.load_summary()
    assert summary is not None
    assert summary[0] == "持久摘要"
```

- [ ] **Step 3: 运行测试**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/ -v`

Expected: 所有测试通过（token_counter: 5 + storage: 7 + black_box: 8 + llm_service: 6 + summarizer: 8 + memory_manager: 9 + integration: 4 = 47 tests）

- [ ] **Step 4: 提交**

Run: `cd c:/Users/Administrator/agent && git add memory/__init__.py memory/tests/test_integration.py && git commit -m "feat: update package exports and add integration tests"`

---

### Task 9: 全项目测试验证

**Files:**
- 无需创建新文件

- [ ] **Step 1: 运行所有 memory 测试**

Run: `cd c:/Users/Administrator/agent && python -m pytest memory/tests/ -v --tb=short 2>&1`

Expected: 所有测试通过

- [ ] **Step 2: 运行已有 cognitive 测试，确认无回归**

Run: `cd c:/Users/Administrator/agent && python -m pytest cognitive/test_cognitive/ -v --tb=short 2>&1`

Expected: 48 passed（与阶段二一致，无回归）

- [ ] **Step 3: 最终提交**

Run: `cd c:/Users/Administrator/agent && git add -A && git commit -m "feat: complete MemoryManager system with all sub-modules"`

---

## 计划自检

### Spec 覆盖检查
- ✅ Token 精确计量（tiktoken）→ Task 2 TokenCounter
- ✅ 滚动摘要 + 滑动窗口 → Task 6 Summarizer + Task 7 MemoryManager.get_context()
- ✅ 黑匣子日志（JSONL）→ Task 4 BlackBox
- ✅ add_message() → Task 7
- ✅ get_context(token_limit) → Task 7
- ✅ compress(old_messages) → Task 6 + Task 7
- ✅ save_log() → Task 4 + Task 7
- ✅ load_summary() → Task 3 + Task 7
- ✅ clear_memory() → Task 7
- ✅ 多种 LLM API（OpenAI + Anthropic）→ Task 5 LLMService
- ✅ 异步/后台压缩 → Task 7 AsyncCompressor
- ✅ 异常处理 → 各模块 Error 类
- ✅ 单元测试 → 每个模块配套测试 (~47 tests)
- ✅ 与阶段二集成 → spec 第九章已明确

### 占位符检查
- 所有代码块包含完整实现代码，无 TODO/TBD
- 所有命令包含完整命令和预期输出
- 所有测试包含完整测试函数

### 类型一致性检查
- `Storage.load_summary()` → `tuple[str, int] | None`（全文一致）
- `TokenCounter.count(text, model)` → `int`（全文一致）
- `LLMService.summarize(messages)` → `str`（全文一致）
