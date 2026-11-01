# -*- coding: utf-8 -*-
"""技能转化器（Skill Transformer）——按 docs/rfc/技能转化器_2026-11-01.md 的分段实现。

P1 只做 **只读计划**：把外来技能拆成五个面（内涵），与**自身已有能力**逐面对比，
跑合规/安全审核，给出「增量在哪 / 要不要人拍板 / 能不能进」的结论，**不写任何文件**。
"""

from .facets import extract_facets, load_text
from .compare import compare_with_repo
from .review import review_text
from .plan import build_plan

__all__ = ["extract_facets", "load_text", "compare_with_repo", "review_text", "build_plan"]
