"""知识检索：混合检索 + 重排 + 双链扩展（任务4 · 检索层）。

召回管道（组合接线，不改存量模块）：
    1. 关键词路：BM25 倒排索引对卡片文本检索（知识包内实现，算法参数
       k1=1.5 / b=0.75 / CJK+英文混合分词 与 tool_router_hybrid.BM25Index 一致；
       同算法在知识包内落地，守【不易】"知识包不得反向依赖工具层"）。
    2. 向量路：注入的 VectorStore.search（ChromaDB 语义，缺失/异常自动降级）。
    3. 双链一跳扩展：命中卡片 links 字段对应卡片并入候选。
    → RRF 融合（rrf_fuse）→ ToolReranker.rerank 重排（sigmoid 概率）→ 阈值过滤。

【不易】
- 不 import / 不修改 `agent/tool_router_hybrid.py`、`agent/tool_router_reranker.py`；
  reranker 以鸭子类型注入（构造参数），组合接线而非重写。
- 误召回保护（project_memory 固化规范）：RRF 融合 top1 的
  max(各路原始分数) < min_score（默认 0.3）→ 返回空结果，不输出噪声。
- rerank_score 为 sigmoid 概率：raw logits 必须 sigmoid 转换以对齐 min_score
  阈值（默认与 SKILL_RERANKER_MIN_SCORE 一致 0.001）。
【变易】
- 向量库 / reranker 缺失、异常、子进程不可用 → 逐级降级，永不抛异常。
- 阈值与开关走 KNOWLEDGE_* 环境变量体系，构造参数可显式覆盖。
【简易】
- 三路召回统一为 [(slug, raw_score|None)]，RRF 只认排名不认量纲；
- 敏感素材（meta sensitive=true）在 snippet 标 [敏感]，可配置隐藏 snippet。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from agent.knowledge.card import Card, CardStore
from agent.knowledge.link_cache import LinkCache
from agent.knowledge.schema import Card as _SchemaCard  # noqa: F401  (类型别名，防误用)
from agent.utils.periodic_sampler import PeriodicSampler

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  默认配置（KNOWLEDGE_* 环境变量体系，与项目 SKILL_RERANKER_* 对齐）
# ════════════════════════════════════════════════════════════

_DEFAULT_RRF_K = 60               # RRF 平滑参数（与 skills_mgmt/loader.py _RRF_K 一致）
_DEFAULT_MIN_SCORE = 0.3          # 误召回保护：top1 max(各路原始分数) 阈值
_DEFAULT_RERANK_MIN_SCORE = 0.001  # rerank 过滤阈值（sigmoid 空间，与 SKILL_RERANKER_MIN_SCORE 一致）
_DEFAULT_RERANK_TOP_N = 20        # reranker 候选池上限（与 AGENT_RERANKER_TOP_N 一致）
_DEFAULT_CANDIDATE_MULT = 2       # 召回候选扩倍（RRF 受 rank 影响大，多取防漏召）
_SENSITIVE_MARKER = "[敏感]"
_SNIPPET_MAX_LEN = 200

_ENV_MIN_SCORE = "KNOWLEDGE_MIN_SCORE"
_ENV_RERANK_MIN_SCORE = "KNOWLEDGE_RERANK_MIN_SCORE"
_ENV_RERANK_TOP_N = "KNOWLEDGE_RERANK_TOP_N"
_ENV_RRF_K = "KNOWLEDGE_RRF_K"
_ENV_HIDE_SENSITIVE = "KNOWLEDGE_SENSITIVE_HIDE_SNIPPET"
_ENV_TIMING_SAMPLE_RATE = "KNOWLEDGE_TIMING_SAMPLE_RATE"
_DEFAULT_TIMING_SAMPLE_RATE = 0.1  # 耗时日志默认采样率：每 10 次 search 输出 1 条（生产降噪）

# ── 任务2：知识检索 KPI 观察埋点（record_semantic_query 观察模式）──
# 默认关闭：避免知识卡片检索（与 orchestrator 语义层技能匹配不同通道）污染 KPI#2
# Skill 命中率分母；显式开启（LEARNING_METRICS_OBSERVE_KNOWLEDGE_SEARCH=true，配置
# 见 config.yaml learning.metrics.observe_knowledge_search）后，每次 search 结果
# 作为一次语义层查询计入（saved_tokens=0，不改变 token 复用率口径）。
_ENV_OBSERVE_KNOWLEDGE_SEARCH = "LEARNING_METRICS_OBSERVE_KNOWLEDGE_SEARCH"
_OBSERVE_KNOWLEDGE_SEARCH: Optional[bool] = None


def _observe_knowledge_search_enabled() -> bool:
    """观察埋点开关（环境变量优先；进程内缓存避免反复读 env）"""
    global _OBSERVE_KNOWLEDGE_SEARCH
    if _OBSERVE_KNOWLEDGE_SEARCH is None:
        raw = os.environ.get(_ENV_OBSERVE_KNOWLEDGE_SEARCH)
        _OBSERVE_KNOWLEDGE_SEARCH = bool(
            raw and raw.strip().lower() in ("1", "true", "yes", "on"))
    return _OBSERVE_KNOWLEDGE_SEARCH


def _emit_knowledge_semantic_metric(hit: bool) -> None:
    """观察模式 KPI 埋点（KPI#1 语义查询路径；异常静默，绝不阻塞检索主链路）"""
    if not _observe_knowledge_search_enabled():
        return
    try:
        from agent.learning_metrics import get_learning_metrics
        get_learning_metrics().record_semantic_query(hit=hit, saved_tokens=0)
    except Exception:
        pass


def _env_float(name: str, default: float) -> float:
    """读取环境变量 float，非法值回退默认（守【简易】）。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("环境变量 %s 非法值 %r，回退默认 %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    """读取环境变量 int，非法值回退默认。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("环境变量 %s 非法值 %r，回退默认 %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """读取环境变量 bool（1/true/yes/on → True），其余回退默认。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ════════════════════════════════════════════════════════════
#  BM25 倒排索引（知识包内实现，与 tool_router_hybrid.BM25Index 同算法）
# ════════════════════════════════════════════════════════════

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """CJK 单字 + 英文单词混合分词（与 tool_router_hybrid._tokenize 一致）。"""
    return _TOKEN_RE.findall((text or "").lower())


class BM25Index:
    """BM25 倒排索引 — 构建时一次性索引全库卡片，检索层无需增量删除。

    【不易】算法参数 k1=1.5、b=0.75 与 tool_router_hybrid.BM25Index /
    memory/vector_store.InvertedIndex 一致；只认排名做 RRF 融合。
    【简易】仅提供 add_document / search / size（T4 检索编排的最小充分接口）。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._index: dict[str, list[tuple[str, int]]] = {}  # term -> [(doc_id, freq)]
        self._doc_lengths: dict[str, int] = {}
        self._total_docs = 0
        self._avg_doc_length = 0.0
        # Why RLock 保护读写：add_document（写）与 search（读）并发时，
        # 倒排表/doc 长度/统计量存在读-改-写竞态（KnowledgeSearcher 构建期写、
        # 检索期读）。RLock 允许同线程重入，锁内仅内存 dict 操作，无 I/O。
        self._lock = threading.RLock()

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档（doc_id 重复时覆盖旧文档）。"""
        tokens = _tokenize(content)
        term_counts: dict[str, int] = {}
        for token in tokens:
            term_counts[token] = term_counts.get(token, 0) + 1

        with self._lock:
            if doc_id in self._doc_lengths:  # 覆盖语义：先移除旧文档
                for term in list(self._index.keys()):
                    self._index[term] = [
                        (did, freq) for did, freq in self._index[term] if did != doc_id
                    ]
                    if not self._index[term]:
                        del self._index[term]
                self._total_docs -= 1

            for term, freq in term_counts.items():
                self._index.setdefault(term, []).append((doc_id, freq))
            self._doc_lengths[doc_id] = len(tokens)
            self._total_docs += 1
            self._avg_doc_length = (
                sum(self._doc_lengths.values()) / self._total_docs
                if self._total_docs > 0 else 0.0
            )

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询，返回 [(doc_id, bm25_score)]（按分数降序）。"""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scores: dict[str, float] = {}
        with self._lock:
            for token in query_tokens:
                for doc_id, freq in self._index.get(token, []):
                    doc_length = self._doc_lengths.get(doc_id, 0)
                    if doc_length > 0:
                        scores[doc_id] = scores.get(doc_id, 0.0) + self._compute_bm25(
                            token, freq, doc_length
                        )
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _compute_bm25(self, term: str, term_freq: int, doc_length: int) -> float:
        """BM25 评分（与 tool_router_hybrid.BM25Index._compute_bm25 一致）。"""
        # Why 不加锁：调用方 search 已持锁（tool_router_hybrid 同约定）
        doc_count = len(self._index.get(term, []))
        idf = (self._total_docs - doc_count + 0.5) / (doc_count + 0.5)
        if idf <= 0:
            return 0.0
        numerator = term_freq * (self._k1 + 1)
        denominator = term_freq + self._k1 * (
            1 - self._b + self._b * doc_length / (self._avg_doc_length or 1)
        )
        return idf * numerator / denominator

    @property
    def size(self) -> int:
        """已索引文档数。"""
        with self._lock:
            return self._total_docs


# ════════════════════════════════════════════════════════════
#  RRF 融合与 sigmoid
# ════════════════════════════════════════════════════════════


def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """RRF 融合：score = Σ 1/(k + rank)。返回 {slug: score}。

    与项目既有 RRF 规范（skills_mgmt/loader.py._rrf_fuse）一致：
    - 只认排名不认原始量纲（BM25 / 向量 / 双链分数量纲不同）；
    - 多路命中的 slug 分数累加，自然获得排序提升；
    - 结果按分数降序，平分时保持先出现顺序稳定（可复现）。

    Args:
        ranked_lists: 各召回路按排名升序排列的 slug 列表（rank 从 1 开始）。
        k: RRF 平滑参数，默认 60。

    Returns:
        {slug: 融合分}，按融合分降序（dict 保序）。
    """
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    for ranked in ranked_lists:
        for rank, slug in enumerate(ranked, start=1):
            if slug not in scores:
                order[slug] = len(order)
            scores[slug] = scores.get(slug, 0.0) + 1.0 / (k + rank)
    return dict(sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]])))


def _sigmoid(x: float) -> float:
    """数值稳定 sigmoid：raw logits → [0,1] 概率。

    【不易】遵循项目已固化规范（skills_mgmt/reranker.py._sigmoid）：
    raw logits 必须 sigmoid 转换以对齐 min_score 阈值；分段实现避免
    math.exp 溢出（|x| > 700 时抛 OverflowError）。
    """
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ════════════════════════════════════════════════════════════
#  检索结果模型
# ════════════════════════════════════════════════════════════


@dataclass
class KnowledgeHit:
    """单条知识检索命中。"""

    slug: str
    title: str
    status: str
    type: str
    score: float            # RRF 融合分（归一化到 [0,1]）
    rerank_score: float     # reranker 分数（sigmoid 概率），未启用/降级为 0.0
    source_ref: str         # 形如 "wiki/concepts/驾驭工程.md"
    snippet: str


# ════════════════════════════════════════════════════════════
#  知识检索编排
# ════════════════════════════════════════════════════════════


def _card_text(card: Card) -> str:
    """卡片 → 检索文本（标题 + 标签 + 洞见 + 正文，BM25/reranker 共用）。"""
    parts = [card.title, " ".join(card.tags or []), card.insight or "", card.content or ""]
    return "\n".join(p for p in parts if p)


def _slug_from_item(item, known_slugs: set[str]) -> Optional[str]:
    """从向量条目提取卡片 slug（metadata.slug 或 id），非卡片条目返回 None。

    契约：向量库按卡片 slug 为键入库（组合接线），ChromaDB 的自动 mem_ID
    不匹配任何卡片 → 返回 None 被跳过（向量路降级为空）。
    """
    meta = getattr(item, "metadata", None)
    if isinstance(meta, dict):
        slug = meta.get("slug")
        if slug in known_slugs:
            return slug
    item_id = getattr(item, "id", None)
    if item_id in known_slugs:
        return item_id
    return None


def _raw_score(item) -> Optional[float]:
    """提取向量条目的原始分数（metadata._score / .score），无分数返回 None。

    Why None：VectorStore.search 的 ChromaDB 路径不返回分数，无法判断置信度
    时返回 None，误召回保护只统计有已知分数的路（无法判断即放行，守【简易】）。
    """
    meta = getattr(item, "metadata", None)
    if isinstance(meta, dict):
        s = meta.get("_score")
        if s is not None:
            try:
                return float(s)
            except (TypeError, ValueError):
                pass
    s = getattr(item, "score", None)
    if isinstance(s, (int, float)):
        return float(s)
    return None


class KnowledgeSearch:
    """知识语义检索：三路召回 → RRF 融合 → rerank → 阈值过滤。

    Usage:
        searcher = KnowledgeSearch(card_store, vector_store=vs, reranker=tool_reranker)
        hits = searcher.search("驾驭工程", top_k=5)

    降级链（逐级降级，永不抛异常）：
        reranker 缺失/子进程不可用/异常 → RRF 原序（rerank_score=0.0）；
        向量库缺失/异常 → 仅 BM25 + 双链扩展；
        top1 max(各路原始分数) < min_score → 空结果（误召回保护）。
    """

    def __init__(
        self,
        card_store: CardStore,
        vector_store=None,
        reranker=None,
        *,
        min_score: Optional[float] = None,
        rerank_min_score: Optional[float] = None,
        rerank_top_n: Optional[int] = None,
        rrf_k: Optional[int] = None,
        hide_sensitive_snippet: Optional[bool] = None,
        timing_sample_rate: Optional[float] = None,
    ):
        """构造检索器（一次性构建 BM25 索引 + 链接解析缓存）。

        Args:
            card_store: 卡片存储（CardStore，检索事实源）。
            vector_store: 向量存储（可选，鸭子类型需有 search(query, top_k)）。
            reranker: 精排器（可选，鸭子类型需有 rerank(query, candidates,
                tool_descriptions=..., top_k=...)，兼容 ToolReranker.rerank）。
            min_score: 误召回保护阈值（默认 KNOWLEDGE_MIN_SCORE / 0.3）。
            rerank_min_score: rerank 过滤阈值（默认 KNOWLEDGE_RERANK_MIN_SCORE / 0.001）。
            rerank_top_n: reranker 候选池上限（默认 KNOWLEDGE_RERANK_TOP_N / 20）。
            rrf_k: RRF 平滑参数（默认 KNOWLEDGE_RRF_K / 60）。
            hide_sensitive_snippet: 敏感命中是否隐藏 snippet
                （默认 KNOWLEDGE_SENSITIVE_HIDE_SNIPPET / False）。
            timing_sample_rate: search_stage_timing 耗时日志采样率 (0,1]，
                rate=1.0 全量，0<rate<1 按周期确定性抽样
                （默认 KNOWLEDGE_TIMING_SAMPLE_RATE / 0.1，生产降噪）。
        """
        self._card_store = card_store
        self._vector_store = vector_store
        self._reranker = reranker
        self._min_score = (
            min_score if min_score is not None
            else _env_float(_ENV_MIN_SCORE, _DEFAULT_MIN_SCORE)
        )
        self._rerank_min_score = (
            rerank_min_score if rerank_min_score is not None
            else _env_float(_ENV_RERANK_MIN_SCORE, _DEFAULT_RERANK_MIN_SCORE)
        )
        self._rerank_top_n = (
            rerank_top_n if rerank_top_n is not None
            else _env_int(_ENV_RERANK_TOP_N, _DEFAULT_RERANK_TOP_N)
        )
        self._rrf_k = rrf_k if rrf_k is not None else _env_int(_ENV_RRF_K, _DEFAULT_RRF_K)
        self._hide_sensitive = (
            hide_sensitive_snippet if hide_sensitive_snippet is not None
            else _env_bool(_ENV_HIDE_SENSITIVE)
        )
        self._timing_sample_rate = (
            timing_sample_rate if timing_sample_rate is not None
            else _env_float(_ENV_TIMING_SAMPLE_RATE, _DEFAULT_TIMING_SAMPLE_RATE)
        )
        self._timing_sampler = PeriodicSampler(self._timing_sample_rate)

        self._bm25 = BM25Index()
        self._cards: dict[str, Card] = {}
        self._link_cache = LinkCache({})  # _build_index 里基于快照重建
        self._build_index()

    # ---------- 内部：索引 ----------

    def _build_index(self) -> None:
        """从卡片库构建 BM25 索引 + slug→Card 映射 + 链接解析缓存（检索零 I/O）。

        链接缓存（Why 性能：双链扩展此前每次 search 逐条 resolve_link→store.get
        文件 I/O，实测占总耗时 99%+）：构造期一次性预计算为内存 slug（LinkCache），
        热路径 _link_recall 纯内存查缓存，零文件 I/O、零读锁等待。快照式语义
        （与 _cards/_bm25 同待遇）：构造后写入的卡不入缓存，重建 searcher 即刷新。
        """
        cards = self._card_store.list()
        for card in cards:
            self._cards[card.slug] = card
            self._bm25.add_document(card.slug, _card_text(card))
        self._link_cache = LinkCache(self._cards)
        logger.info(
            "KnowledgeSearch: 索引构建完成 卡片数=%d bm25_size=%d 链接缓存卡数=%d",
            len(self._cards), self._bm25.size, self._link_cache.size,
        )

    # ---------- 内部：三路召回 ----------

    def _vector_recall(self, query: str, top_k: int) -> list[tuple[str, Optional[float]]]:
        """向量路召回：VectorStore.search → [(slug, raw_score|None)]。

        向量库缺失 / 异常 / 条目未按 slug 入库 → 返回空（降级到 BM25 路）。
        """
        vs = self._vector_store
        if vs is None:
            return []
        try:
            items = vs.search(query, top_k=top_k)
        except Exception as exc:
            logger.warning("知识检索: 向量路异常，本次降级跳过: %s", exc)
            return []
        if not items:
            return []
        known_slugs = set(self._cards)
        results: list[tuple[str, Optional[float]]] = []
        seen: set[str] = set()
        for item in items:
            slug = _slug_from_item(item, known_slugs)
            if slug is None or slug in seen:
                continue
            seen.add(slug)
            results.append((slug, _raw_score(item)))
        logger.info(
            "知识检索-向量路: 原始条目=%d 有效卡片=%d → %s",
            len(items), len(results),
            [(s, round(sc, 3) if sc is not None else None) for s, sc in results],
        )
        return results

    def _link_recall(self, seeds: list[str], trace_id: Optional[str] = None) -> list[str]:
        """双链一跳扩展：取种子卡片 links 的一跳目标（查预计算缓存，纯内存零 I/O）。

        语义与实时 resolve_link 完全等价（_build_index 时以 _MemoryCardStore 解析，
        与 store.get 的容错语义对齐，守【不易】）：
        - 只取一跳，不递归；断链/归档目标跳过（缓存中解析为 None）；
        - 已见 slug（种子或已纳入）重复跳过，不双计。
        """
        expanded: list[str] = []
        seen = set(seeds)
        details: list[str] = []
        for seed in seeds:
            for target, resolved_slug in self._link_cache.expanded_links(seed):
                if resolved_slug is None:
                    reason = "归档跳过" if target.startswith("archives/") else "断链跳过"
                    details.append(f"{seed}→{target}({reason})")
                    continue
                if resolved_slug in seen:
                    details.append(f"{seed}→{resolved_slug}(重复跳过)")
                    continue
                seen.add(resolved_slug)
                expanded.append(resolved_slug)
                details.append(f"{seed}→{resolved_slug}(纳入)")
        logger.info(
            "知识检索-双链扩展: seeds=%s 明细=%s 纳入=%s trace=%s",
            seeds, details, expanded, trace_id or "",
        )
        return expanded

    # ---------- 内部：误召回保护 ----------

    @staticmethod
    def _is_mis_recall(top1: str, raw_scores: dict[str, Optional[float]],
                       min_score: float) -> bool:
        """误召回保护：top1 的 max(各路已知原始分数) < min_score → True。

        无任何已知原始分数（如仅向量路且无分数）→ False（无法判断即放行，
        守【简易】：不可判场景不误伤检索结果）。
        """
        known = [v for v in raw_scores.values() if v is not None]
        if not known:
            return False
        return max(known) < min_score

    # ---------- 内部：重排 ----------

    def _rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
        top_k: int,
    ) -> list[tuple[str, float, float]]:
        """对 RRF 融合候选重排，返回 [(slug, rrf_score, rerank_score)]。

        rerank_score 语义（项目固化规范）：
            - reranker 实际执行 → sigmoid(raw logit)；
            - reranker 缺失/异常/子进程不可用 → 0.0（RRF 原序兜底）；
            - raw == 0.0 是 ToolReranker 的降级标记（未实际打分）→ 保持 0.0，
              避免 sigmoid(0)=0.5 伪造高置信。
        阈值过滤后为空 → RRF 原序兜底（rerank_score=0.0），不抛异常。
        """
        fallback = [(s, sc, 0.0) for s, sc in candidates[:top_k]]

        reranker = self._reranker
        if reranker is None or not candidates:
            logger.info("知识检索-重排: reranker 未启用，使用 RRF 原序 (rerank_score=0.0)")
            return fallback

        pool = candidates[: self._rerank_top_n]
        descs = {slug: _card_text(self._cards[slug]) for slug, _ in pool}
        try:
            results = reranker.rerank(
                query, pool, tool_descriptions=descs, top_k=top_k
            )
        except Exception as exc:
            logger.warning("知识检索: reranker 异常，降级 RRF 原序: %s", exc)
            return fallback
        if not results:
            logger.info("知识检索-重排: reranker 返回空，回退 RRF 原序")
            return fallback

        ranked: list[tuple[str, float, float]] = []
        for slug, fused_score, raw in results:
            prob = 0.0 if raw == 0.0 else _sigmoid(raw)
            if prob < self._rerank_min_score:
                logger.info(
                    "知识检索-重排: %s raw=%.3f→sigmoid=%.6f 低于阈值 %.4f 过滤",
                    slug, raw, prob, self._rerank_min_score,
                )
                continue
            ranked.append((slug, fused_score, prob))
        if ranked:
            logger.info(
                "知识检索-重排: %s → %s",
                [s for s, _, _ in results],
                [(s, round(p, 3)) for s, _, p in ranked],
            )
            return ranked
        logger.info("知识检索-重排: reranker 全部低于阈值，回退 RRF 原序")
        return fallback

    # ---------- 内部：组装 ----------

    def _snippet(self, card: Card) -> str:
        """生成 snippet；敏感素材（meta sensitive=true）标 [敏感] 或按配置隐藏。"""
        sensitive = bool(card.metadata.get("sensitive"))
        if sensitive and self._hide_sensitive:
            return f"{_SENSITIVE_MARKER} 内容已隐藏"
        text = (card.content or "").strip() or (card.insight or "").strip()
        text = " ".join(text.split())
        if len(text) > _SNIPPET_MAX_LEN:
            text = text[:_SNIPPET_MAX_LEN].rstrip() + "…"
        if sensitive:
            return f"{_SENSITIVE_MARKER} {text}"
        return text

    def _make_hit(self, card: Card, fused_score: float, rerank_score: float) -> KnowledgeHit:
        """Card + 分数 → KnowledgeHit（source_ref 可追溯到物理文件）。"""
        return KnowledgeHit(
            slug=card.slug,
            title=card.title,
            status=card.status,
            type=card.type,
            score=round(fused_score, 3),
            rerank_score=round(rerank_score, 3),
            source_ref=f"wiki/{card.type}/{card.slug}.md",
            snippet=self._snippet(card),
        )

    # ---------- 公开接口 ----------

    def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        """同步搜索：RRF 融合 + rerank + 双链一跳扩展。

        Args:
            query: 查询文本（空串返回空结果）。
            top_k: 返回命中数上限（≥1）。

        Returns:
            KnowledgeHit 列表（按 rerank 后分数降序）；误召回保护触发时返回空。
        """
        query = (query or "").strip()
        if not query:
            return []
        if not self._cards:
            return []
        top_k = max(1, int(top_k))
        candidate_k = max(top_k * _DEFAULT_CANDIDATE_MULT, 10)
        _trace = uuid.uuid4().hex[:16]
        _t0 = time.perf_counter()
        logger.info(json.dumps({
            "trace_id": _trace,
            "module_name": "knowledge_search",
            "action": "search_start",
            "query": query,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "cards": len(self._cards),
            "min_score": self._min_score,
            "rerank_min_score": self._rerank_min_score,
            "rrf_k": self._rrf_k,
            "vector_store": self._vector_store is not None,
            "reranker": self._reranker is not None,
        }, ensure_ascii=False))

        # ── 三路召回（分段计时，定位性能瓶颈）──
        _t_stage = time.perf_counter()
        bm25_results = self._bm25.search(query, top_k=candidate_k)
        t_bm25 = (time.perf_counter() - _t_stage) * 1000
        _t_stage = time.perf_counter()
        vector_results = self._vector_recall(query, candidate_k)
        t_vector = (time.perf_counter() - _t_stage) * 1000
        seeds = list(dict.fromkeys(  # 保序去重：BM25 优先，向量补充
            [s for s, _ in bm25_results] + [s for s, _ in vector_results]
        ))
        _t_stage = time.perf_counter()
        link_slugs = self._link_recall(seeds, trace_id=_trace)
        t_link = (time.perf_counter() - _t_stage) * 1000

        ranked_lists = [
            [s for s, _ in bm25_results],
            [s for s, _ in vector_results],
            link_slugs,
        ]
        logger.info(
            "知识检索-三路召回: BM25=%s 向量=%s 双链=%s 并集=%s trace=%s",
            [(s, round(sc, 3)) for s, sc in bm25_results],
            [(s, round(sc, 3)) if sc is not None else (s, None)
             for s, sc in vector_results],
            link_slugs,
            list(dict.fromkeys(s for lst in ranked_lists for s in lst)),
            _trace,
        )
        _t_stage = time.perf_counter()
        fused = rrf_fuse(ranked_lists, k=self._rrf_k)
        if not fused:
            return []

        # ── 误召回保护（project_memory 固化规范）──
        top1 = next(iter(fused))
        raw_by_path: dict[str, dict[str, Optional[float]]] = {}
        for slug, score in bm25_results:
            raw_by_path.setdefault(slug, {})["bm25"] = score
        for slug, score in vector_results:
            raw_by_path.setdefault(slug, {})["vector"] = score
        if self._is_mis_recall(top1, raw_by_path.get(top1, {}), self._min_score):
            logger.warning(
                "知识检索: 误召回保护触发 top1=%s max(原始分数)=%s < min_score=%.2f → 返回空",
                top1,
                {k: v for k, v in raw_by_path.get(top1, {}).items() if v is not None},
                self._min_score,
            )
            return []

        # RRF 融合分归一化到 [0,1]（与项目 loader._rrf_fuse 归一化规范一致）
        n_active = len([lst for lst in ranked_lists if lst])
        max_possible = n_active / (self._rrf_k + 1)
        fused_norm = {
            slug: min(1.0, score / max_possible) for slug, score in fused.items()
        }
        candidates = list(fused_norm.items())
        # 结构化分解：每候选在各路的排名 + raw/norm（排查多路召回权重异常：
        # 一眼可见某 slug 经几路命中、各 rank 权重、双链贡献、归一化占比）
        path_ranks: dict[str, dict[str, int]] = {}
        for path_name, path_list in zip(("bm25", "vector", "link"), ranked_lists):
            for rank, slug in enumerate(path_list, start=1):
                path_ranks.setdefault(slug, {})[path_name] = rank
        logger.info(json.dumps({
            "trace_id": _trace,
            "module_name": "knowledge_search",
            "action": "rrf_fuse",
            "n_active": n_active,
            "max_possible": round(max_possible, 6),
            "detail": [
                {
                    "slug": slug,
                    "bm25_rank": path_ranks.get(slug, {}).get("bm25"),
                    "vector_rank": path_ranks.get(slug, {}).get("vector"),
                    "link_rank": path_ranks.get(slug, {}).get("link"),
                    "raw": round(raw, 6),
                    "norm": round(fused_norm[slug], 4),
                }
                for slug, raw in fused.items()
            ],
        }, ensure_ascii=False))
        t_rrf = (time.perf_counter() - _t_stage) * 1000  # rrf_fuse + 归一化 + 误召回保护

        # ── 重排（可选，失败降级原序）──
        _t_stage = time.perf_counter()
        ranked = self._rerank(query, candidates, top_k=top_k)
        t_rerank = (time.perf_counter() - _t_stage) * 1000

        # ── 组装 hits ──
        _t_stage = time.perf_counter()
        hits: list[KnowledgeHit] = []
        for slug, fused_score, rerank_score in ranked:
            card = self._cards.get(slug)
            if card is None:
                continue
            hits.append(self._make_hit(card, fused_score, rerank_score))
        t_hits = (time.perf_counter() - _t_stage) * 1000
        _total_ms = (time.perf_counter() - _t0) * 1000
        # 耗时日志采样（生产降噪，PeriodicSampler 线程安全）：rate=1.0 全量；
        # 0<rate<1 按周期确定性抽样（如 0.1 → 每 10 次 1 条）。
        if self._timing_sampler.should_sample():
            logger.info(json.dumps({
                "trace_id": _trace,
                "module_name": "knowledge_search",
                "action": "search_stage_timing",
                "query": query,
                "ms": {
                    "bm25": round(t_bm25, 2),
                    "vector": round(t_vector, 2),
                    "link": round(t_link, 2),
                    "rrf": round(t_rrf, 2),
                    "rerank": round(t_rerank, 2),
                    "assemble": round(t_hits, 2),
                    "total": round(_total_ms, 2),
                },
            }, ensure_ascii=False))
        logger.info(
            "知识检索: query=%r top_k=%d bm25=%d vector=%d links=%d fused=%d hits=%d "
            "trace=%s 耗时=%.2fms",
            query, top_k, len(bm25_results), len(vector_results),
            len(link_slugs), len(fused), len(hits), _trace, _total_ms,
        )
        # 任务2: KPI#1 语义查询路径——知识检索命中处观察埋点（saved_tokens=0，
        # 不改变 token 复用率口径；开关默认关，异常静默）
        _emit_knowledge_semantic_metric(hit=bool(hits))
        return hits[:top_k]

    async def search_async(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        """异步搜索（供前端/工作流调用）：后台线程执行，不阻塞事件循环。

        用 asyncio.to_thread 复用调用方事件循环的默认 executor；
        不自行 get_event_loop（在线程内创建 loop 且不关闭会造成资源泄漏）。
        """
        return await asyncio.to_thread(self.search, query, top_k)


# ════════════════════════════════════════════════════════════
#  结果格式化（供系统提示注入）
# ════════════════════════════════════════════════════════════


def format_context(hits: list[KnowledgeHit], top_k: int = 3) -> str:
    """输出带 [来源: slug|status] 标记的引用块，供系统提示注入。

    输出格式（任务4 约定）：
        【知识库检索结果】
        1. 标题
           [来源: wiki/concepts/<slug>.md | status | score=0.82]
           snippet: ...
    """
    if not hits:
        return "（知识库未检索到相关卡片）"
    lines = ["【知识库检索结果】"]
    for i, hit in enumerate(hits[:top_k], start=1):
        lines.append(f"{i}. {hit.title}")
        lines.append(f"   [来源: {hit.source_ref} | {hit.status} | score={hit.score:.2f}]")
        lines.append(f"   snippet: {hit.snippet}")
    return "\n".join(lines)


__all__ = ["BM25Index", "KnowledgeHit", "KnowledgeSearch", "format_context", "rrf_fuse"]
