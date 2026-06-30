"""技能搜索器 — 高级搜索 (分类/标签/状态/全文/分页/排序)

设计:
    - 全文搜索: TF-IDF 风格的简单词频打分 (避免引入第三方依赖)
    - 多维过滤: category / tags / status / enabled / quality
    - 排序: updated_at / usage_count / quality_score / name
    - 可观测: 输出搜索耗时与命中数
"""

from __future__ import annotations
import re
import time
import json
import uuid
import logging
from typing import List

from .models import (
    Skill,
    SkillSearchParams,
    SkillSearchResult,
    SkillCategory,
    SkillStatus,
)
from .observability import logger, emit_metric, traced_action


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def _match_score(skill: Skill, query_tokens: List[str]) -> float:
    """计算查询与技能的匹配分 (名称权重最高，描述次之，标签第三)"""
    if not query_tokens:
        return 0.0
    name_tokens = _tokenize(skill.name)
    desc_tokens = _tokenize(skill.description)
    tag_tokens = _tokenize(" ".join(skill.tags))

    name_hits = sum(1 for t in query_tokens if t in name_tokens)
    desc_hits = sum(1 for t in query_tokens if t in desc_tokens)
    tag_hits = sum(1 for t in query_tokens if t in tag_tokens)

    # 归一化 (避免长描述天然得分高)
    name_norm = max(1, len(name_tokens))
    desc_norm = max(1, len(desc_tokens))
    tag_norm = max(1, len(tag_tokens))

    return (
        3.0 * name_hits / name_norm
        + 1.5 * desc_hits / desc_norm
        + 2.0 * tag_hits / tag_norm
    )


class SkillSearcher:
    """技能搜索器"""

    def search(self, skills: List[Skill], params: SkillSearchParams) -> SkillSearchResult:
        """执行搜索"""
        t0 = time.time()
        with traced_action("skill_search", query=params.query,
                           filters=len(params.categories) + len(params.tags)) as ctx:
            query_tokens = _tokenize(params.query)
            results: List[Skill] = []

            for s in skills:
                # 过滤
                if params.enabled_only and not s.enabled:
                    continue
                if params.categories:
                    cats = {c.value if isinstance(c, SkillCategory) else c
                            for c in params.categories}
                    if s.category not in cats:
                        continue
                if params.tags:
                    if not set(s.tags) & set(params.tags):
                        continue
                if params.statuses:
                    sts = {st.value if isinstance(st, SkillStatus) else st
                           for st in params.statuses}
                    if s.status not in sts:
                        continue
                if params.min_quality_score > 0:
                    q = s.review.quality_score if s.review else 0.0
                    if q < params.min_quality_score:
                        continue
                # 全文匹配
                if query_tokens:
                    score = _match_score(s, query_tokens)
                    if score <= 0:
                        continue
                results.append(s)

            # 排序
            reverse = params.sort_desc
            sort_key = params.sort_by
            if sort_key == "name":
                results.sort(key=lambda x: x.name, reverse=reverse)
            elif sort_key == "usage_count":
                results.sort(key=lambda x: x.metrics.usage_count, reverse=reverse)
            elif sort_by_quality := (sort_key == "quality_score"):
                results.sort(
                    key=lambda x: x.review.quality_score if x.review else 0.0,
                    reverse=reverse,
                )
            else:  # 默认 updated_at
                results.sort(key=lambda x: x.updated_at, reverse=reverse)

            total = len(results)
            # 分页
            start = (params.page - 1) * params.page_size
            end = start + params.page_size
            paged = results[start:end]
            elapsed = (time.time() - t0) * 1000

            ctx["total"] = total
            ctx["elapsed_ms"] = elapsed
            emit_metric("yunshu_skill_search_latency_ms",
                        value=elapsed, labels={"success": "true"},
                        kind="histogram")
            logger.info("[Searcher] query='%s' → %d/%d 命中, %.2fms",
                        params.query, len(paged), total, elapsed)
            return SkillSearchResult(
                items=paged,
                total=total,
                page=params.page,
                page_size=params.page_size,
                elapsed_ms=round(elapsed, 2),
            )


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "searcher",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
