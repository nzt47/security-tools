"""云枢外部记忆导入（memory_import）—— 与吞噬并列的第二个"外部资产入口"。

分工（不要混淆）：

| 入口 | 吸收什么 | 落哪里 |
|---|---|---|
| agent.ingestion（吞噬） | 技能包 / 文档 / 提示词 / JS 插件 | 技能库、工作流、工具 |
| agent.memory_import（本包） | Claude / Trae 的偏好、规则与会话轨迹 | 知识卡片、向量记忆 |

公开 API（默认全部只读）：

    preview(...)   采集 + 去噪/去重/脱敏/分批 -> 计划快照（不写记忆）
    commit(...)    按 batch 写入；默认 dry_run，真写必须 confirm=True 且写审计
    rollback(...)  按 batch 回滚（知识卡片 / 向量记忆；老批次按 msg_id）
    migrate(...)   把 §10 之前写进消息流的存量搬到向量记忆（默认 dry_run）

【载体口径（2026-10-25 起）】会话/提问进**向量记忆**（metadata 带 source/batch_id/
origin/period/digest_source），不再写长期记忆消息流：那会把外部转录伪装成本机对话，
折进滚动摘要且检索不到。

import 纪律：本包是叶子包 —— 模型/读取器/流水线只依赖标准库；知识库、
审计、长期记忆等重依赖一律在函数体内懒加载，保证导入期零副作用。
"""

from __future__ import annotations

from .models import (  # noqa: F401
    CARRIER_CARD,
    CARRIER_LABELS,
    CARRIER_MEMORY,
    CARRIERS,
    KIND_MESSAGE,
    KIND_PREFERENCE,
    KIND_PROMPT,
    SOURCES,
    SOURCE_CLAUDE,
    SOURCE_TRAE,
    STATUS_BLOCKED,
    STATUS_KEEP,
    Batch,
    ConfirmationRequired,
    DroppedRecord,
    MemoryImportError,
    MemoryRecord,
    PlanNotFound,
    UnknownBatch,
    plan_id_for,
)
from .redact import redact_text, scan as scan_secrets  # noqa: F401
from .service import (  # noqa: F401
    DEFAULT_ROOT,
    CollectResult,
    collect,
    commit,
    load_plan,
    preview,
    rollback,
)

__all__ = [
    "CARRIERS", "CARRIER_CARD", "CARRIER_LABELS", "CARRIER_MEMORY",
    "KIND_MESSAGE", "KIND_PREFERENCE", "KIND_PROMPT",
    "SOURCES", "SOURCE_CLAUDE", "SOURCE_TRAE",
    "STATUS_BLOCKED", "STATUS_KEEP",
    "Batch", "CollectResult", "ConfirmationRequired", "DEFAULT_ROOT",
    "DroppedRecord", "MemoryImportError", "MemoryRecord", "PlanNotFound",
    "UnknownBatch", "collect", "commit", "load_plan", "plan_id_for",
    "preview", "redact_text", "rollback", "scan_secrets",
]
