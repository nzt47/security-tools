"""技能 BM25 检索器 — 匹配专有名词与确定性锚点

设计目的:
    向量检索擅长语义相似，但对技能标题/专有名词等"确定性锚点"不够精确
    （例：query="PDF解析" 时向量可能召回"文档处理"而非"PDF解析"本身）。
    BM25 反向：对精确字面匹配强，对语义弱。两者融合可覆盖两类场景。

架构层级:
    SkillLoader (loader.py)
        ↓ 注入
    BM25SkillSearcher (本模块)
        ↓ 复用
    rank_bm25.BM25Okapi (纯 Python，无 native 依赖)

核心策略:
    - 文档 = 技能名称 + 描述 + tags + category（与 TF-IDF/向量路同源）
    - 混合分词：英文按词、中文按字（与 loader._tokenize 一致，保证三路同尺度）
    - 延迟构建：首次 search() 时构建索引
    - 失败降级：rank_bm25 未安装 → is_available=False，search 返回空列表

【不易】纯 Python 实现，不引入 native 依赖；不修改 SkillLoader.match 签名
【变易】文档字段可扩展；与 TF-IDF/向量路正交，可独立启用或禁用
【简易】单一职责：build_index + search，复用 rank_bm25 成熟实现
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .observability import emit_metric
from agent.logging_utils import log_dict

logger = logging.getLogger("agent.skills_mgmt.bm25_searcher")

# rank_bm25 为可选依赖：未安装时降级为不可用（守【不易】无 native 依赖约束）
try:
    from rank_bm25 import BM25Okapi
    _RANK_BM25_AVAILABLE = True
except ImportError:  # noqa: BLE001
    BM25Okapi = None  # type: ignore[assignment,misc]
    _RANK_BM25_AVAILABLE = False
    logger.warning(log_dict({'module_name': 'bm25_searcher', 'action': 'rank_bm25.unavailable', 'reason': 'rank_bm25 not installed; BM25 path disabled (fallback to tfidf+vector)'}))


# ════════════════════════════════════════════════════════════
#  分词（与 loader._tokenize 同尺度 — 2026-08-12 同步 bigram）
# ════════════════════════════════════════════════════════════
# 【简易】本地副本而非跨模块导入，避免 loader ↔ bm25_searcher 循环依赖
# （searcher.py 也采用同样的本地副本模式，保持模块独立性）
# Why（不易）: loader._tokenize 已改为中文 bigram（修复中文输入误命中元技能
#      导致语义层短路）；bm25 路必须同步，否则 RRF 融合中 bm25 路仍按单字
#      激进命中，短路问题复发（实测"费马小定理证明" bm25_rank=1 命中）。
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def _tokenize(text: str) -> List[str]:
    """混合分词：英文按词，中文按相邻二元组（bigram，与 loader._tokenize 同尺度）"""
    tokens: List[str] = []
    for seg in _WORD_RE.findall((text or "").lower()):
        if len(seg) > 1 and not seg.isascii():
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            tokens.append(seg)
    return tokens


def _skill_to_doc(skill: Union[Dict[str, Any], Any]) -> str:
    """将技能对象/元数据字典转为 BM25 文档文本

    支持 duck-typing：既能接收 Skill pydantic 模型，也能接收 file_store 的 meta dict。
    字段与 _meta_to_meta_text (loader.py) 同源，保证三路检索同尺度。

    【变易】字段缺失时容忍降级，不抛异常
    """
    # 统一为 dict 访问（Skill pydantic 模型也支持 model_dump，但 dict 访问更通用）
    if isinstance(skill, dict):
        meta = skill
    elif hasattr(skill, "model_dump"):
        meta = skill.model_dump()
    else:
        meta = {
            "name": getattr(skill, "name", ""),
            "description": getattr(skill, "description", ""),
            "tags": getattr(skill, "tags", []) or [],
            "category": getattr(skill, "category", ""),
        }
    parts = [
        meta.get("name", "") or "",
        meta.get("description", "") or "",
        " ".join(meta.get("tags", []) or []),
        meta.get("category", "") or "",
    ]
    return " ".join(p for p in parts if p)


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class BM25Match:
    """BM25 检索单条结果

    与 SkillVectorAdapter.search 返回结构对齐：
    {"skill_id", "score", "metadata"}，便于 loader 统一转换。
    """
    skill_id: str
    score: float
    name: str = ""
    description: str = ""
    category: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "score": round(self.score, 4),
            "metadata": {
                "name": self.name,
                "description": self.description,
                "category": self.category,
                "tags": self.tags,
            },
        }


# ════════════════════════════════════════════════════════════
#  BM25 检索器
# ════════════════════════════════════════════════════════════

class BM25SkillSearcher:
    """BM25 技能检索器 — 匹配专有名词与确定性锚点

    用途:
        - 向量检索擅长语义相似，但对技能标题/专有名词不够精确
        - BM25 反向：对精确匹配强，对语义弱
        - 两者融合可覆盖两类场景

    用法:
        searcher = BM25SkillSearcher()
        searcher.build_index(skills)  # skills: List[Skill] 或 List[dict]
        results = searcher.search("PDF解析", top_k=5)
        # results: [BM25Match(skill_id="pdf_parser", score=2.31, ...)]

    【不易】纯 Python，无 native 依赖；rank_bm25 未安装时降级为不可用
    【变易】文档字段组合与分词策略可调整；与 TF-IDF/向量路正交
    【简易】复用 rank_bm25 成熟实现，不自造 BM25
    """

    def __init__(self):
        self._bm25: Optional[Any] = None  # BM25Okapi 实例
        self._skill_ids: List[str] = []
        self._tokenized_docs: List[List[str]] = []
        # 元数据缓存：search 时回填 name/description，避免 loader 重复查 file_store
        self._skill_metas: Dict[str, Dict[str, Any]] = {}

    def build_index(self, skills: List[Union[Dict[str, Any], Any]]) -> None:
        """构建 BM25 索引（文档 = 技能名称 + 描述 + tags + category）

        Args:
            skills: 技能列表，元素可为 Skill pydantic 模型或元数据 dict
                    （loader 传入 file_store.load_metadata_index() 的 values）

        【不易】空列表时清空索引并标记不可用，不报错
        【变易】重复调用重建索引（支持技能变更后刷新）
        """
        # rank_bm25 未安装 → 不构建，保持 is_available=False
        if not _RANK_BM25_AVAILABLE:
            logger.info(log_dict({'module_name': 'bm25_searcher', 'action': 'build_index.skipped', 'reason': 'rank_bm25 not installed'}))
            return

        # 重置状态（支持重建）
        self._bm25 = None
        self._skill_ids = []
        self._tokenized_docs = []
        self._skill_metas = {}

        # 【可观测性】分词阶段计时（排查索引构建耗时与文档规模关系）
        _tokenize_t0 = time.time()
        _skipped_empty = 0  # 空文档跳过计数（监控数据质量）

        for skill in skills:
            # 提取 skill_id：dict 取 "id" 字段，对象取 .id 属性
            if isinstance(skill, dict):
                skill_id = skill.get("id") or skill.get("skill_id") or ""
                meta = skill
            else:
                skill_id = getattr(skill, "id", "") or getattr(skill, "skill_id", "")
                meta = {
                    "name": getattr(skill, "name", ""),
                    "description": getattr(skill, "description", ""),
                    "category": getattr(skill, "category", ""),
                    "tags": getattr(skill, "tags", []) or [],
                }
            if not skill_id:
                continue

            doc_text = _skill_to_doc(skill)
            tokens = _tokenize(doc_text)
            # 跳过空文档（避免 BM25Okapi 对空文档报错或产生 NaN）
            if not tokens:
                _skipped_empty += 1
                logger.debug(log_dict({'module_name': 'bm25_searcher', 'action': 'build_index.skip_empty_doc', 'skill_id': skill_id, 'doc_length': len(doc_text), 'reason': 'tokenization yielded empty tokens'}))
                continue

            self._skill_ids.append(skill_id)
            self._tokenized_docs.append(tokens)
            self._skill_metas[skill_id] = meta

            # 【可观测性】每个 skill 的分词详情（DEBUG 级别，避免 INFO 噪音）
            # 排查 BM25 召回异常时, 可确认 doc_text 与 tokens 是否符合预期
            # 例：专有名词 "k8s" 应被分词为 ["k8s"] 而非 ["k", "8", "s"]
            logger.debug(log_dict({'module_name': 'bm25_searcher', 'action': 'build_index.tokenize', 'skill_id': skill_id, 'doc_length': len(doc_text), 'tokens_count': len(tokens), 'tokens_preview': tokens[:10]}))

        _tokenize_elapsed = (time.time() - _tokenize_t0) * 1000

        if not self._skill_ids:
            logger.info(log_dict({'module_name': 'bm25_searcher', 'action': 'build_index.empty', 'reason': 'no valid skills with non-empty docs', 'input_count': len(skills), 'skipped_empty': _skipped_empty, 'tokenize_elapsed_ms': round(_tokenize_elapsed, 2)}))
            return

        # 【可观测性】BM25Okapi 构建阶段计时（排查 native 调用耗时）
        _build_t0 = time.time()
        try:
            self._bm25 = BM25Okapi(self._tokenized_docs)
        except Exception as e:  # noqa: BLE001
            # 构建失败 → 标记不可用，不抛异常（守防御性要求）
            _build_elapsed = (time.time() - _build_t0) * 1000
            logger.warning(log_dict({'module_name': 'bm25_searcher', 'action': 'build_index.failed', 'error': str(e)[:200], 'doc_count': len(self._skill_ids), 'build_elapsed_ms': round(_build_elapsed, 2)}))
            self._bm25 = None
            self._skill_ids = []
            self._tokenized_docs = []
            self._skill_metas = {}
            return

        _build_elapsed = (time.time() - _build_t0) * 1000
        # 平均文档 token 数（监控索引规模增长）
        avg_doc_tokens = (
            round(sum(len(t) for t in self._tokenized_docs) / len(self._tokenized_docs), 2)
            if self._tokenized_docs else 0
        )
        logger.info(log_dict({'module_name': 'bm25_searcher', 'action': 'build_index.ok', 'indexed_count': len(self._skill_ids), 'input_count': len(skills), 'skipped_empty': _skipped_empty, 'tokenize_elapsed_ms': round(_tokenize_elapsed, 2), 'build_elapsed_ms': round(_build_elapsed, 2), 'avg_doc_tokens': avg_doc_tokens, 'total_tokens': sum((len(t) for t in self._tokenized_docs))}))
        emit_metric("yunshu_skill_bm25_index_count",
                    value=len(self._skill_ids), kind="gauge",
                    labels={"layer": "1", "method": "bm25"})

    def search(self, query: str, top_k: int = 5) -> List[BM25Match]:
        """BM25 检索

        Args:
            query: 用户意图文本
            top_k: 返回前 K 条

        Returns:
            按 BM25 得分降序的 BM25Match 列表；索引为空或不可用时返回空列表

        【不易】索引为空时返回空列表（不报错）
        【简易】get_scores 返回所有文档分数，排序后取 top_k
        """
        if not self.is_available():
            return []
        if not query or not query.strip():
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        try:
            scores = self._bm25.get_scores(query_tokens)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'bm25_searcher', 'action': 'search.get_scores.failed', 'query': query[:100], 'error': str(e)[:200]}))
            return []

        # 按分数降序排序，过滤零分（无任何词项命中的文档）
        # 【不易】零分文档不参与召回，避免无意义结果污染融合
        ranked = sorted(
            enumerate(scores),
            key=lambda x: x[1],
            reverse=True,
        )

        results: List[BM25Match] = []
        for idx, score in ranked:
            if score <= 0:
                continue  # 零分跳过
            if len(results) >= top_k:
                break
            skill_id = self._skill_ids[idx]
            meta = self._skill_metas.get(skill_id, {})
            results.append(BM25Match(
                skill_id=skill_id,
                score=float(score),
                name=meta.get("name", skill_id) if isinstance(meta, dict) else skill_id,
                description=meta.get("description", "") if isinstance(meta, dict) else "",
                category=meta.get("category", "") if isinstance(meta, dict) else "",
                tags=meta.get("tags", []) if isinstance(meta, dict) else [],
            ))

        emit_metric("yunshu_skill_bm25_match_count",
                    value=len(results), kind="gauge",
                    labels={"layer": "1", "method": "bm25"})
        return results

    def is_available(self) -> bool:
        """BM25 检索器是否可用（rank_bm25 已安装且索引已构建）"""
        return self._bm25 is not None
