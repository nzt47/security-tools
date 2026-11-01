# -*- coding: utf-8 -*-
"""技能转化器（Skill Transformer）——按 docs/rfc/技能转化器_2026-11-01.md 的分段实现。

P1 = 只读计划：内涵切分 → 与自身能力逐面对比 → 审核 → 卡片草案（不写文件）；
P2 = 落地：把产物落到 data/ingestion/transforms/、生成 CARD.md、可并入/新建技能、可开 PR；
P4 = 队列 + 回滚：把归档摆到界面上（判定/审核/卡片），并按 transform_id 反向补丁回滚；
P5 = LLM 重写：模型只准**逐字引用**原文，引用校验不过的条目整条丢弃（rewrite.py）。
"""

from .facets import extract_facets, load_text
from .compare import compare_with_repo
from .review import review_text
from .plan import build_plan
from .apply import apply_plan, render_card
from .queue import get_transform, list_transforms, rollback_transform
from .rewrite import draft_sections, llm_configured, verify_items

__all__ = ["extract_facets", "load_text", "compare_with_repo", "review_text", "build_plan",
           "apply_plan", "render_card", "list_transforms", "get_transform", "rollback_transform",
           "draft_sections", "verify_items", "llm_configured"]
