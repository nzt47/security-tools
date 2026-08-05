"""知识卡片生命周期状态机（任务0 · 契约层）。

规则（不易）：
- 状态迁移**不移动文件**（仅更新 frontmatter）；唯一例外：Archive 态卡片
  物理移入 `knowledge/archives/`（由任务2 处理重链）。
- 合法迁移表以本模块 `TRANSITIONS` 为唯一事实源，与 `knowledge/AGENTS.md`、
  `agent/knowledge/schema.py::VALID_STATUS` 保持一致。
- 非法迁移判定绝不抛异常：`validate_transition` 返回原因字符串。

来源说明（重建）:
    2026-08-05 依据 .pyc 反汇编提取的结构/常量/逻辑重建，语义与字节码一致。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class CardStatus(str, Enum):
    """卡片生命周期状态（值即 frontmatter `status` 字段取值）。"""

    DRAFT = "draft"
    CURRENT = "current"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


# 合法迁移表: 当前状态 → 可达目标状态集合
# 语义:
#   draft   → current（草稿转当前）/ unknown（草稿状态未知）
#   current → archive（归档）/ draft（回退草稿）
#   archive → ∅（终态, 不可再迁移）
#   unknown → draft / current（状态重识别）
TRANSITIONS: dict[CardStatus, set[CardStatus]] = {
    CardStatus.DRAFT: {CardStatus.CURRENT, CardStatus.UNKNOWN},
    CardStatus.CURRENT: {CardStatus.ARCHIVE, CardStatus.DRAFT},
    CardStatus.ARCHIVE: set(),
    CardStatus.UNKNOWN: {CardStatus.DRAFT, CardStatus.CURRENT},
}


def can_transition(current: CardStatus, target: CardStatus) -> bool:
    """判断状态迁移是否合法（不抛异常）"""
    return target in TRANSITIONS.get(current, set())


def validate_transition(current: CardStatus,
                        target: CardStatus) -> Optional[str]:
    """校验状态迁移，非法时返回原因字符串，合法返回 None（绝不抛异常）"""
    if not isinstance(current, CardStatus):
        return f"非法当前状态: {current!r}（必须是 CardStatus 枚举）"
    if not isinstance(target, CardStatus):
        return f"非法目标状态: {target!r}（必须是 CardStatus 枚举）"
    if target not in TRANSITIONS[current]:
        return (f"非法状态迁移: {current.value} → {target.value}")
    return None
