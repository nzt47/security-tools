# 任务1：素材层 Ingest 管道（低摩擦收集）

**任务ID**: T1
**阶段**: 1（管道层）
**前置依赖**: 任务0（目录结构 + AGENTS.md + `agent/knowledge/schema.py` 的 `slugify`）
**可并行**: 与任务2 并行
**预计工作量**: 2-3 天
**输出类型**: Python 模块 + CLI + 测试

---

## 一、目标描述

实现"收集即入库"：将外部资料（文章、剪藏、会议转写、随手想法）以**原样只读**方式落入 `knowledge/raw|inbox/`，并登记到 `log.md`。不改变 raw/inbox 内容（【不易】约束：素材层是证据仓库，只读不改），仅追加元数据与日志。

本阶段可独立运行（不依赖任务2 的卡片引擎），但使用任务0 定义的目录与日志格式。

---

## 二、执行步骤

### Step 1：创建 ingest 模块

新建 `agent/knowledge/ingest.py`：

```python
"""素材层 Ingest 管道：低摩擦收集 → 原样入库 + 元数据登记"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

# 目标层：inbox / raw
VALID_LAYERS = {"inbox", "raw"}


def ingest_file(
    src_path: str | Path,
    dest_layer: str = "inbox",
    source_type: Optional[str] = None,
) -> dict:
    """复制（非移动）文件到目标层，生成 .meta.json。

    返回登记结果 dict（含 dest_path、meta_path、sensitive 标记等）。
    """
    ...


def list_inbox() -> list[dict]:
    """列出 inbox 中待处理素材（含 meta 信息）"""
    ...


def list_raw() -> list[dict]:
    """列出 raw 中原始素材（含 meta 信息）"""
    ...
```

行为要求：
- **复制而非移动**：源文件保持不变；目标文件名以 slug 化时间戳 + 原文件名保证唯一（`YYYYMMDD-HHMMSS-<basename>`）。
- 生成 `.meta.json`：`{source_path, source_type, captured_at, sha256, sensitive}`。
- `sha256` 计算源文件内容哈希，供后续幂等与去重（任务3 使用）。
- 敏感检测：调用 `agent.memory.filter.SensitiveDataFilter.detect()`（仅读取，不改内容），命中则 meta 中 `sensitive=true` 并返回提示信息，**不阻断入库**（素材层保留证据）。

### Step 2：实现 log.md 登记

实现 `agent/knowledge/logbook.py`：

```python
def append_log(action: str, slug: str, detail: str = "") -> bool:
    """在 log.md 顶部追加时间戳记录（带文件锁防并发写）"""
    ...
```

日志行格式（与任务0 AGENTS.md 一致）：
`## [YYYY-MM-DD] ingest | <slug> | <source_type>: <detail>`

要求：
- 使用 `threading.Lock()`（进程内）或原子写（写临时文件后 rename），保证并发安全。
- **幂等**：同一文件重复 ingest 不产生重复 log 行（按 meta 中 sha256 去重）。

### Step 3：文件监听自动入库

新建 `agent/knowledge/watcher.py`：

```python
def start_knowledge_watcher(knowledge_root: str | Path) -> None:
    """监听 knowledge/inbox 与知识根目录新增文件，自动登记 log.md"""
    ...
```

- 复用 `sensor/file_watcher.py` 的监听机制（若接口不匹配，可参考其实现做最小封装）。
- 新文件落入 inbox 时自动调用 `append_log("ingest", ...)`。
- 进程内单例：重复调用 `start_knowledge_watcher` 不重复启动（参考项目现有单例模式）。

### Step 4：提供 CLI 入口

新建 `agent/knowledge/__main__.py`，支持：

```bash
python -m agent.knowledge.ingest <path> [--layer inbox|raw] [--type articles|podcasts|assets]
python -m agent.knowledge.ingest --list-inbox
```

（注：若包入口约定不同，可采用 `python -m agent.knowledge <cmd>` 风格，保持与项目其他 CLI 一致。）

### Step 5：编写单元测试

新建 `tests/unit/test_knowledge_ingest.py`，覆盖：
- 复制只读性（源文件 hash 前后一致）。
- meta.json 生成正确（sha256、sensitive 标记）。
- log.md 追加幂等（重复 ingest 不重复登记）。
- 并发入库 10 个文件无日志损坏/丢失。
- 敏感素材被标记 `sensitive=true`（用 mock 的 SensitiveDataFilter 命中路径）。
- 非法 `dest_layer` 抛 `ValueError`。

运行命令：

```bash
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_knowledge_ingest.py -p no:cacheprovider --no-header --cov=agent.knowledge --cov-report=term
```

---

## 三、预期成果

1. `agent/knowledge/ingest.py`、`logbook.py`、`watcher.py`、`__main__.py` 模块。
2. CLI 可手动入库任意文件。
3. 文件监听自动登记 log.md（增量、加锁、幂等）。
4. 素材层完整，任何素材可回溯来源（meta 含来源路径与哈希）。

## 四、评估标准

- [ ] 测试全绿，覆盖率 ≥ 80%。
- [ ] **【不易】验收线：raw/inbox 内源文件字节不变**（复制前后 sha256 一致）。
- [ ] 同一文件重复 ingest 不产生重复 log 行（幂等）。
- [ ] 敏感素材在 meta 中有 `sensitive=true` 标记，且检索阶段可见该标记（任务4 验收该字段）。
- [ ] 并发 ingest 10 个文件无日志损坏/丢失。
- [ ] 既有测试回归通过。

## 五、交付物清单

| 文件 | 说明 |
|------|------|
| `agent/knowledge/ingest.py` | 入库主逻辑 + CLI 命令 |
| `agent/knowledge/logbook.py` | log.md 追加（线程安全 + 幂等） |
| `agent/knowledge/watcher.py` | 文件监听自动登记 |
| `agent/knowledge/__main__.py` | CLI 入口 |
| `tests/unit/test_knowledge_ingest.py` | 测试 |
