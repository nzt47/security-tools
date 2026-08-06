"""agent.knowledge — 知识层主包（任务0 · 契约层）。

对外导出全部契约符号：卡片 Schema 校验器与生命周期状态机。
后续任务（1-7）只允许 import 本包，不得反向依赖。

来源说明（重建）:
    本包源码因历史事故丢失（仅剩 __pycache__/*.pyc），2026-08-05 依据
    .pyc 反汇编提取的结构/常量/逻辑重建入库。语义与字节码一致，
    注释尽量保留原始 docstring 文案。
"""

from __future__ import annotations

from agent.knowledge.schema import (REQUIRED_FIELDS,
                                    VALID_CONTRADICTION_STATUS, VALID_STATUS,
                                    VALID_TYPES, Card, slugify, validate_card)
from agent.knowledge.lifecycle import (TRANSITIONS, CardStatus, can_transition,
                                       validate_transition)
from agent.knowledge.logbook import append_log
from agent.knowledge.links import (find_broken_links, find_orphans,
                                   parse_links, resolve_link,
                                   rewrite_link_targets)
from agent.knowledge.card import (CardConflictError, CardNotFoundError,
                                  CardStore, InvalidTransitionError)
from agent.knowledge.index import rebuild_index, update_index_delta

__all__ = [
    # schema
    "REQUIRED_FIELDS", "VALID_CONTRADICTION_STATUS", "VALID_STATUS",
    "VALID_TYPES", "Card", "slugify", "validate_card",
    # lifecycle
    "TRANSITIONS", "CardStatus", "can_transition", "validate_transition",
    # logbook（任务1 契约最小落地）
    "append_log",
    # links（任务2）
    "parse_links", "find_orphans", "resolve_link", "find_broken_links",
    "rewrite_link_targets",
    # card（任务2 核心引擎）
    "CardStore", "CardConflictError", "CardNotFoundError",
    "InvalidTransitionError",
    # index（任务2）
    "rebuild_index", "update_index_delta",
]
