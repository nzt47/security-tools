# -*- coding: utf-8 -*-
"""技能转化器（Skill Transformer）——按 docs/rfc/技能转化器_2026-11-01.md 的分段实现。

P1 = 只读计划：内涵切分 → 与自身能力逐面对比 → 审核 → 卡片草案（不写文件）；
P2 = 落地：把产物落到 data/ingestion/transforms/、生成 CARD.md、可并入/新建技能、可开 PR。
"""

from .facets import extract_facets, load_text
from .compare import compare_with_repo
from .review import review_text
from .plan import build_plan
from .apply import apply_plan, render_card

__all__ = ["extract_facets", "load_text", "compare_with_repo", "review_text", "build_plan",
           "apply_plan", "render_card"]
