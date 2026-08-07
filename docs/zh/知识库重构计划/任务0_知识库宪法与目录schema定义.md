# 任务0：知识库宪法与目录/卡片 Schema 定义

**任务ID**: T0
**阶段**: 0（契约层）
**前置依赖**: 无
**可并行**: 是（与其他所有任务并行，本任务是全部契约来源）
**预计工作量**: 2-3 天
**输出类型**: 文档 + Python 常量/校验器

---

## 一、目标描述

为云枢建立知识层的"宪法"：定义物理目录结构、AGENTS.md 知识库规则、知识卡片 YAML Frontmatter Schema、知识生命周期状态机。本任务产出**契约文档 + Python 常量/校验器**，为后续所有阶段提供唯一事实源。

【不易】约束：不得修改任何现有记忆模块接口（`memory/`、`agent/memory/`、`MemoryManager`、`VectorStore` 等）。本任务只新增，不改动。

---

## 二、执行步骤

### Step 1：建立物理目录骨架

在项目根目录创建 `knowledge/` 目录结构：

```
knowledge/
├── raw/                  # 第一层：原始素材层（只读不改的"事实仓库"）
│   ├── articles/         # 文章、剪藏
│   ├── podcasts/         # 播客转录、会议纪要
│   └── assets/           # 图片、PDF 等附件
├── inbox/                # 收集箱（临时存放未处理碎片信息）
├── processed/            # 中间层：AI 提炼后的结构化笔记（待讨论）
├── wiki/                 # 知识层：AI+人协同维护的"成品库"
│   ├── concepts/         # 概念卡
│   ├── entities/         # 实体卡
│   └── insights/         # 洞察与对比分析
├── archives/             # 生命周期 Archive 态归档
├── index.md              # 全局内容索引（AI 自动维护）
├── log.md                # 操作时间线日志（AI 自动追加）
└── AGENTS.md             # 规则协议层：知识库"宪法"
```

每个目录放入 `.gitkeep` 以保持空目录可提交。`index.md`、`log.md`、`AGENTS.md` 提供初始模板内容。

### Step 2：编写 AGENTS.md 知识库宪法

创建 `knowledge/AGENTS.md`，必须固化以下规则（供 AI 与后续任务执行）：

1. **角色定义**：你是云枢的知识管理员与研究员，维护基于本地 Markdown 的知识库。
2. **目录结构与权限**：
   - `raw/`、`inbox/`：只读，绝对不可修改/删除/重命名其中文件（证据保留）。
   - `processed/`：可读写，AI 提炼的结构化笔记。
   - `wiki/`：可读写，所有提炼出的概念/实体/洞察卡片。
   - `archives/`：可读写，归档态卡片。
3. **索引规则**：每次在 wiki 中创建/大幅更新页面，必须同步更新 `index.md`（链接 + 一句话摘要）。
4. **日志规则**：每次执行任务后，必须在 `log.md` 顶部追加一条时间戳记录，格式：
   `## [YYYY-MM-DD] <action> | <slug> | <detail>`
5. **查询规则**：回答必须基于知识库；知识库中没有时明确告知，并使用通用知识补充（需标注来源）。
6. **人机边界**：AI 负责簿记（交叉引用、摘要更新、格式转换、矛盾标记）；人类负责判断与决策。AI 禁止替用户做人生决策或写入"个人日记"类内容。
7. **生命周期状态**：卡片状态由 frontmatter `status` 字段表达（见 Step 4），不得通过移动文件表达（除 Archive 归档）。

### Step 3：创建知识卡片 Schema

新建 `agent/knowledge/schema.py`（新包 `agent/knowledge/` 为本次重构的主包），定义：

```python
"""知识卡片 Schema 与校验器"""
from __future__ import annotations
from typing import Any, Optional
from dataclasses import dataclass, field

REQUIRED_FIELDS = ["title", "slug", "status", "type", "source", "date"]
VALID_TYPES = {"concepts", "entities", "insights"}
VALID_STATUS = {"draft", "current", "archive", "unknown"}


@dataclass
class Card:
    title: str
    slug: str
    status: str
    type: str
    source: str
    date: str
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)          # 双向链接目标 slug 列表
    contradictions: list[dict] = field(default_factory=list)  # [{target_slug, status: conflict/reviewed/resolved}]
    insight: str = ""          # 一句话核心洞见
    scope: str = ""            # 适用边界
    content: str = ""          # Markdown 正文
    metadata: dict = field(default_factory=dict)


def validate_card(card: dict) -> list[str]:
    """校验卡片，返回违规项列表（无违规返回空列表，绝不抛异常）"""
    ...


def slugify(title: str) -> str:
    """标题 → 文件名规范（全小写、连字符、去歧义后缀）"""
    ...
```

校验规则至少覆盖：
- 缺失 `REQUIRED_FIELDS` 中任一字段。
- `type` 不在 `VALID_TYPES` 中。
- `status` 不在 `VALID_STATUS` 中。
- `slug` 与 `slugify(title)` 不一致（除非显式允许）。
- `contradictions` 中条目缺 `target_slug` 或 `status` 非法。
- `insight` 为空时报"缺少一句话核心洞见"。

### Step 4：定义生命周期状态机

新建 `agent/knowledge/lifecycle.py`：

```python
"""知识卡片生命周期状态机"""
from enum import Enum
from typing import Optional


class CardStatus(str, Enum):
    DRAFT = "draft"        # 草稿：讨论中/未校准
    CURRENT = "current"    # 当前有效：经过人机确认
    ARCHIVE = "archive"    # 历史归档：被替代/失效
    UNKNOWN = "unknown"    # 未知待整理：LLM 降级产物等


# 合法迁移表
TRANSITIONS: dict[CardStatus, set[CardStatus]] = {
    CardStatus.DRAFT:    {CardStatus.CURRENT, CardStatus.ARCHIVE, CardStatus.UNKNOWN},
    CardStatus.CURRENT:  {CardStatus.ARCHIVE, CardStatus.DRAFT},
    CardStatus.ARCHIVE:  set(),          # 归档不可回迁（除非人工强制）
    CardStatus.UNKNOWN:  {CardStatus.DRAFT, CardStatus.CURRENT},
}


def can_transition(current: CardStatus, target: CardStatus) -> bool:
    ...


def validate_transition(current: CardStatus, target: CardStatus) -> Optional[str]:
    """返回 None 表示合法；否则返回原因字符串"""
    ...
```

规则：
- `Draft→Current→Archive` 主链；`Unknown` 只能进入 `Draft` 或 `Current`；拒绝 `Draft→Archive` 直跳等非法迁移。
- 状态迁移**不移动文件**（仅更新 frontmatter）；唯一例外：Archive 态卡片物理移入 `archives/`（由任务2 处理重链）。

### Step 5：编写单元测试

新建：
- `tests/unit/test_knowledge_schema.py`：校验器正反例、`slugify` 规则、Card dataclass 默认值。
- `tests/unit/test_knowledge_lifecycle.py`：状态机迁移矩阵全分支、`can_transition`/`validate_transition`。

测试运行命令：

```bash
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_knowledge_schema.py tests/unit/test_knowledge_lifecycle.py -p no:cacheprovider --no-header --cov=agent.knowledge --cov-report=term
```

---

## 三、预期成果

1. `knowledge/` 物理目录 + `AGENTS.md` + `index.md`/`log.md` 模板。
2. `agent/knowledge/` 新包：`__init__.py`、`schema.py`、`lifecycle.py`（含 type hints 与 docstring）。
3. 至少 20 个测试用例（schema 校验正反例 + 状态机迁移矩阵），覆盖率 ≥ 85%。

## 四、评估标准

- [ ] `pytest tests/unit/test_knowledge_schema.py tests/unit/test_knowledge_lifecycle.py` 全绿，覆盖率 ≥ 85%。
- [ ] `validate_card` 对缺失必填字段/非法 status/非法 type 返回明确错误字符串列表，不抛异常。
- [ ] 状态机拒绝 `Draft→Archive` 等非法迁移；`Unknown` 只能进入 `Draft` 或 `Current`。
- [ ] 现有全部测试回归通过（零破坏）。
- [ ] `AGENTS.md` 与 `schema.py` 中类型/状态命名完全一致（单一事实源）。
- [ ] `slugify` 幂等：`slugify(slugify(x)) == slugify(x)`。

## 五、交付物清单

| 文件 | 说明 |
|------|------|
| `knowledge/AGENTS.md` | 知识库宪法 |
| `knowledge/index.md` | 内容索引模板 |
| `knowledge/log.md` | 时间线日志模板 |
| `agent/knowledge/__init__.py` | 包导出（Card, validate_card, slugify, CardStatus, can_transition 等） |
| `agent/knowledge/schema.py` | Schema + 校验器 |
| `agent/knowledge/lifecycle.py` | 生命周期状态机 |
| `tests/unit/test_knowledge_schema.py` | Schema 测试 |
| `tests/unit/test_knowledge_lifecycle.py` | 状态机测试 |
