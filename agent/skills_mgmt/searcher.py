"""技能搜索器 — 高级搜索 (分类/标签/状态/全文/分页/排序)

设计:
    - 全文搜索: TF-IDF 风格的简单词频打分 (避免引入第三方依赖)
    - 多维过滤: category / tags / status / enabled / quality
    - 排序: updated_at / usage_count / quality_score / name
    - 可观测: 输出搜索耗时与命中数
"""

from __future__ import annotations
import os
import re
import time
import json
import uuid
import logging
from typing import Any, Dict, List, Optional

from .models import (
    Skill,
    SkillSearchParams,
    SkillSearchResult,
    SkillCategory,
    SkillStatus,
)
from .observability import logger, emit_metric, traced_action
from agent.logging_utils import log_dict


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


#: 【G1-C / R-d 逃生开关】与 `registry._desc_from_file_track()` 读**同一个**环境变量，
#: 保证"搜索"与"展示"能一起回滚。
#: 【为什么本地复制而不 import】守本包的"模块独立性"约定（本文件对 `_WORD_RE` 也采用
#: 同样的本地副本模式，见 `bm25_searcher.py` 的同款注释）；两处语义必须一致 ——
#: 故有一条断言直接比对两者（tests/unit/test_skill_search_description_source.py）。
_ENV_DESC_FROM_FILE_TRACK = "CP_SKILL_DESC_FROM_FILE_TRACK"


def _desc_from_file_track() -> bool:
    """描述是否取**文件轨优先**（默认 True；置 0/false/no/off 关闭）"""
    raw = os.environ.get(_ENV_DESC_FROM_FILE_TRACK)
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _match_score(skill: Skill, query_tokens: List[str],
                 file_desc: str = "", file_desc_zh: str = "") -> float:
    """计算查询与技能的匹配分 (名称权重最高，描述次之，标签第三)

    【G1-C/R-d：描述与"展示"同源】

    修复前本函数只吃 `Skill.description`（= **主轨**文案）。而 G1-B 之后管理页
    展示的是文件轨（`registry.as_legacy_rows()` 的文件轨优先 + `description_zh`，
    UI 读 `zh ‖ en`）⇒ 出现"**看到的**是文件轨文案、**搜到的**按主轨文案"。
    现在描述侧取数与展示侧**同口径**：

        desc_text = (文件轨 description 若可用且开关为 1，否则主轨 description)
                    + " " + 文件轨 description_zh（有则并入）

    · 英文侧受逃生开关 `CP_SKILL_DESC_FROM_FILE_TRACK` 控制 —— 与
      `registry.as_legacy_rows()` 完全一致，保证"展示与搜索一起回滚"；
    · 中文侧 `description_zh` **无条件**并入 —— 因为管理页真正显示的就是它
      （`yunshu-ui/.../skills.tsx` 渲染 `description_zh || description`）。
      这也是 G1-B 登记的残留 R-d 的正式修法。

    【G1-B/M4b 的延期项】**解除条件②已由本条满足**：文件轨元数据索引由
    `search()` **一次**取好（`meta_index` 入参），不在这里逐条查文件系统，
    故不会把 UI 列表搜索变成 IO 热点。原来的①②③三条延期理由里：
      ① 仍成立且与本条不冲突 —— 本器**仍不在生产检索链上**（生产走
         `loader.load_metadata_index()` 的 TF-IDF/向量/BM25）。本条修的是
         **管理页搜索**这条消费者，不声称提升生产召回。
      ② 已解除：不再依赖 `Skill` 模型带 `description_zh`，改由入参索引提供。
      ③ 已解除：中文取自**文件轨**（唯一源），不再把已废弃的主轨副本拉进打分。

    【向后兼容】`file_desc` / `file_desc_zh` 默认空串 ⇒ 行为与修复前**逐字相同**
    （调用方没给索引时不会改变任何得分）。

    【生产检索链的同一缺口（本卡未动，见 G1C.md 残留）】`loader._meta_to_meta_text()`
    仍只拼 `description`（英文），故 Layer-1 TF-IDF 对中文查询的召回不受本条影响。
    """
    if not query_tokens:
        return 0.0
    name_tokens = _tokenize(skill.name)
    desc_text = str(skill.description or "")
    if file_desc and _desc_from_file_track():
        desc_text = str(file_desc)          # 文件轨优先（与 as_legacy_rows 同口径）
    if file_desc_zh:
        # 中文展示文案无条件并入（界面显示的就是它；与 as_legacy_rows 一致）
        desc_text = (desc_text + " " + str(file_desc_zh)).strip()
    desc_tokens = _tokenize(desc_text)
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

    def search(self, skills: List[Skill], params: SkillSearchParams,
               meta_index: Optional[Dict[str, Dict[str, Any]]] = None,
               ) -> SkillSearchResult:
        """执行搜索

        Args:
            skills: 待搜索的技能（`SkillsMgmtService.store.list_all()` —— **主轨**，
                决定 id/name/tags/category/status 等字段）
            params: 搜索参数
            meta_index: 【G1-C/R-d】文件轨元数据索引
                （`file_store.load_metadata_index()` 的返回值，**一次**取好传入）。
                给出时，描述打分会改为"文件轨优先 + 中文 `description_zh` 并入"，
                即与 `registry.as_legacy_rows()`（= 管理页展示）同口径。
                None（默认）⇒ 行为与修复前逐字相同。

        【为什么索引由外层传而不是本函数自己读】解除条件②：按 id 逐条查文件系统
        会把列表搜索变成 IO 热点；一次索引的开销是 O(1) 次读取。
        """
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
                    _fm = (meta_index or {}).get(s.id) or {}
                    score = _match_score(
                        s, query_tokens,
                        file_desc=str(_fm.get("description") or ""),
                        file_desc_zh=str(_fm.get("description_zh") or ""),
                    )
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
        logger.error(log_dict({'module_name': 'searcher', 'action': action + '.failed', 'error': f'{type(e).__name__}: {e}'}))
        raise
