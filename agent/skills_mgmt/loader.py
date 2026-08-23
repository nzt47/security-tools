"""三层分层检索引擎 — Agent Skill 核心机制

文章描述的三层架构:
    第一层（元数据层）: 所有技能基础信息统一存放在 skill.md 的 front matter，
        单条约 100 TOKEN，几乎不占资源。用于快速匹配用户意图。
    第二层（使用说明层）: 匹配到技能后，才读取 skill.md 的完整 body（操作步骤），
        实现按需加载，而非全程占用上下文。
    第三层（工具资源层）: 技能 scripts/ 目录下的 Python 脚本。执行任务时，
        代码不在对话中传输，由后台直接运行，只将结果传给模型。

本模块实现:
    - match(intent, top_k): 第一层匹配 — 基于元数据索引，返回候选技能（不加载 body）
    - load_instruction(skill_id): 第二层 — 按需加载使用说明
    - get_script_paths(skill_id): 第三层 — 按需获取脚本路径
    - estimate_tokens(text): token 估算（用于预算管理）
    - get_layer_summary(): 三层架构统计信息

设计原则:
    - 按需加载: 只在需要时加载对应层的数据，大幅节省上下文
    - 可观测: 每层加载输出结构化日志（trace_id, module_name, action, duration_ms, layer, tokens）
    - 边界显性化: 匹配失败/技能不存在 → 抛出带业务码的 Error
    - token 预算: match() 返回预估 token 数，调用方可据此决定加载策略
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .file_store import SkillFileStore
from .observability import logger, emit_metric, traced_action
from .exceptions import SkillNotFoundError, SkillMgmtError


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════
#  Token 估算
# ════════════════════════════════════════════════════════════

# 经验值：中文约 1.5 字符/token，英文约 4 字符/token
# 使用简化估算：中文按 1.5，英文按 4
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（粗略，无需第三方依赖）

    中文: 约 1.5 字符/token
    英文: 约 4 字符/token
    """
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    other_chars = len(text) - cjk_chars
    # 中文部分 + 英文部分
    tokens = math.ceil(cjk_chars / 1.5) + math.ceil(other_chars / 4)
    return tokens


def _meta_to_meta_text(meta: Dict[str, Any]) -> str:
    """将元数据字典转为用于匹配的文本（第一层）"""
    parts = [
        meta.get("name", ""),
        meta.get("description", ""),
        " ".join(meta.get("tags", []) or []),
        meta.get("category", ""),
    ]
    return " ".join(p for p in parts if p)


# ════════════════════════════════════════════════════════════
#  分词与匹配（第一层）
# ════════════════════════════════════════════════════════════

# 英文整词 + 连续中文串（中文不再按单字切分，见 _tokenize 的 Why）
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def _tokenize(text: str) -> List[str]:
    """混合分词：英文按词，中文按相邻二元组（bigram）

    Why（不易）: 中文按单字切分时，命中率 hits/len(query_tokens) 对
        min_score=0.3 阈值形同虚设——元技能元数据覆盖大量常用字，
        任何中文输入的单字命中率都虚高（实测"费马小定理证明"命中
        "主动建议"技能并短路返回，ContextAssembler 组装流程不触发）。
        bigram 保留相邻字序信息，任务词（解析/总结/证明）才有区分度；
        孤立单字仍保留（守向后兼容，避免短查询分词为空）。
    """
    tokens: List[str] = []
    for seg in _WORD_RE.findall((text or "").lower()):
        if len(seg) > 1 and not seg.isascii():
            # 连续中文串 → 相邻二元组（如 "解析文件" → ["解析","析文","文件"]）
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            tokens.append(seg)
    return tokens


def _match_score(meta_text: str, query_tokens: List[str]) -> float:
    """计算查询与元数据文本的匹配分"""
    if not query_tokens:
        return 0.0
    meta_tokens = _tokenize(meta_text)
    if not meta_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in meta_tokens)
    return hits / len(query_tokens)  # 命中率


def _record_skill_match_prometheus(layer: str, method: str, success: bool, elapsed_ms: float):
    """[变易] prometheus_client 原生指标记录（供 HPA + Grafana dashboard 消费）

    与 emit_metric 并存（向后兼容）:
        - emit_metric → BusinessMetricsCollector（自管理字典，不支持 histogram_quantile）
        - 本函数 → prometheus_client 原生（支持 HPA 的 histogram_quantile / rate()）

    HPA 消费链路:
        - skill_match_latency_ms_bucket → Prometheus Adapter → skill_match_latency_p99
        - skill_match_count_total → Prometheus Adapter → skill_match_qps
    """
    try:
        from agent.monitoring.prometheus import (
            record_skill_match_latency, record_skill_match_count
        )
        record_skill_match_latency(layer=layer, method=method, success=success, duration_ms=elapsed_ms)
        record_skill_match_count(layer=layer, method=method, success=success)
    except Exception:  # noqa: BLE001  埋点失败不影响主流程
        pass


# ════════════════════════════════════════════════════════════
#  匹配结果数据模型
# ════════════════════════════════════════════════════════════

class SkillMatch:
    """单个技能匹配结果"""

    def __init__(self, skill_id: str, name: str, description: str,
                 score: float, estimated_tokens: int,
                 category: str = "", tags: Optional[List[str]] = None,
                 version: str = "", enabled: bool = True,
                 # 以下为预留扩展字段，向后兼容（默认 None 不影响现有调用）
                 score_breakdown: Optional[Dict[str, Any]] = None,
                 # [变易] 敏感技能隔离扩展字段（默认 False 不影响现有调用）
                 is_sensitive: bool = False,
                 isolation_strategy: str = "separate_turn"):
        self.skill_id = skill_id
        self.name = name
        self.description = description
        self.score = round(score, 4)
        self.estimated_tokens = estimated_tokens
        self.category = category
        self.tags = tags or []
        self.version = version
        self.enabled = enabled
        # 预留：未来多路检索（tfidf/vector/bm25）的分项得分
        # 示例: {"tfidf": 0.8, "vector": 0.9}，当前 TF-IDF 不填充
        self.score_breakdown = score_breakdown
        # [变易] 敏感技能隔离标记（ContextInjector 分流依据）
        self.is_sensitive = is_sensitive
        self.isolation_strategy = isolation_strategy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "score": self.score,
            "estimated_tokens": self.estimated_tokens,
            "category": self.category,
            "tags": self.tags,
            "version": self.version,
            "enabled": self.enabled,
            "score_breakdown": self.score_breakdown,
            "is_sensitive": self.is_sensitive,
            "isolation_strategy": self.isolation_strategy,
        }


class MatchResult:
    """匹配结果集合"""

    def __init__(self, matches: List[SkillMatch], total_scanned: int,
                 elapsed_ms: float, estimated_total_tokens: int,
                 # 以下为预留扩展字段，均为关键字参数且有默认值，向后兼容
                 *, retrieval_method: str = "tfidf",
                 score_breakdown: Optional[Dict[str, List[float]]] = None,
                 reranked: bool = False,
                 fallback_used: bool = False,
                 # [变易] 全链路可观测性扩展：检索召回分块详情
                 # 每项结构: {skill_id, score, layer, tokens}
                 # 缺省 None 保证旧调用方不受影响（守不易）
                 retrieved_chunks: Optional[List[Dict[str, Any]]] = None):
        self.matches = matches
        self.total_scanned = total_scanned
        self.elapsed_ms = round(elapsed_ms, 2)
        self.estimated_total_tokens = estimated_total_tokens
        # 预留扩展：检索方法标识 tfidf | vector | bm25 | fused
        self.retrieval_method = retrieval_method
        # 预留扩展：分路得分汇总 {"tfidf": [...], "vector": [...]}
        self.score_breakdown = score_breakdown
        # 预留扩展：是否经过 Reranker 二次排序
        self.reranked = reranked
        # 预留扩展：是否降级（向量检索失败回退 TF-IDF 时为 True）
        self.fallback_used = fallback_used
        # 可观测性：检索召回分块详情，供 Precision@K 监控与幻觉率分析
        # 未提供时按 matches 自动生成（保持向后兼容）
        if retrieved_chunks is None:
            retrieved_chunks = [
                {
                    "skill_id": m.skill_id,
                    "score": m.score,
                    "layer": 1,
                    "tokens": m.estimated_tokens,
                }
                for m in matches
            ]
        self.retrieved_chunks = retrieved_chunks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matches": [m.to_dict() for m in self.matches],
            "total_scanned": self.total_scanned,
            "match_count": len(self.matches),
            "elapsed_ms": self.elapsed_ms,
            "estimated_total_tokens": self.estimated_total_tokens,
            "layer": 1,
            "retrieval_method": self.retrieval_method,
            "score_breakdown": self.score_breakdown,
            "reranked": self.reranked,
            "fallback_used": self.fallback_used,
            # [变易] 可观测性扩展字段：retrieved_chunks（默认按 matches 自动生成）
            "retrieved_chunks": self.retrieved_chunks,
        }


# ════════════════════════════════════════════════════════════
#  三层检索引擎
# ════════════════════════════════════════════════════════════

class SkillLoader:
    """三层分层检索引擎

    核心理念:
        - 第一层匹配只读元数据（~100 token/技能），不加载完整内容
        - 第二层只在匹配后按需加载使用说明
        - 第三层只在执行时按需加载脚本

    用法:
        loader = SkillLoader()
        result = loader.match("帮我解析PDF文件")  # 第一层
        for m in result.matches:
            instruction = loader.load_instruction(m.skill_id)  # 第二层
            scripts = loader.list_scripts(m.skill_id)  # 第三层
    """

    # 【变易】TF-IDF 倒排索引开关 — 默认启用，将 O(n) 全量遍历降为 O(k) k=命中数
    # 关闭时回退到全量遍历（守【不易】向后兼容）
    _DEFAULT_USE_INVERTED_INDEX = True

    def __init__(self, file_store: Optional[SkillFileStore] = None,
                 vector_adapter: Optional[Any] = None):
        self.fs = file_store or SkillFileStore()
        # 向量检索适配器（延迟创建，避免初始化时拉起 chromadb/torch）
        # 传入 None 时按需创建；显式传入便于测试 mock
        self._vector_adapter = vector_adapter
        # 【变易】TF-IDF 倒排索引缓存 — token → Set[skill_id]
        # 与 _meta_index 引用绑定：load_metadata_index(refresh=True) 后自动重建
        # _inverted_index_meta_id 记录构建时的 index 对象 id，用于检测失效
        self._inverted_index: Optional[Dict[str, set]] = None
        self._inverted_index_meta_id: Optional[int] = None

    # ──────────────────────────────────────────────
    #  TF-IDF 倒排索引（O(n)→O(k) 加速）
    # ──────────────────────────────────────────────

    def _get_inverted_index(self, index: Dict[str, Dict[str, Any]],
                           ) -> Dict[str, set]:
        """构建/获取 TF-IDF 倒排索引 — token → Set[skill_id]

        【不易】复用 _tokenize / _meta_to_meta_text，保证分词与匹配一致
        【变易】与 _meta_index 引用绑定（id 检测），refresh 后自动重建
        【简易】首次调用 O(n) 构建，后续 O(1) 返回缓存

        Args:
            index: load_metadata_index() 返回的元数据索引
        Returns:
            {token: {skill_id, ...}} 倒排索引
        """
        # 检查缓存有效性：id(index) 变化说明 _meta_index 已 refresh
        if self._inverted_index is not None and \
           self._inverted_index_meta_id == id(index):
            return self._inverted_index

        # 重建倒排索引：遍历所有技能，对 meta_text 分词建倒排
        inverted: Dict[str, set] = {}
        for skill_id, meta in index.items():
            meta_text = _meta_to_meta_text(meta)
            # set 去重：同一技能同一 token 只计一次（与 _match_score 的 in 判断一致）
            for token in set(_tokenize(meta_text)):
                inverted.setdefault(token, set()).add(skill_id)

        self._inverted_index = inverted
        self._inverted_index_meta_id = id(index)
        logger.info(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "loader",
            "action": "inverted_index.built",
            "skill_count": len(index),
            "token_count": len(inverted),
        }, ensure_ascii=False))
        return inverted

    def _tfidf_scan(self, index: Dict[str, Dict[str, Any]],
                    query_tokens: List[str],
                    enabled_only: bool,
                    min_score: float,
                    use_inverted_index: bool,
                    candidate_limit: int = 0,
                    ) -> List[SkillMatch]:
        """TF-IDF 扫描 — 用倒排索引筛选候选集，再精确计算匹配分

        【不易】_match_score 计算逻辑不变，仅用倒排索引减少不必要的遍历
               候选集 = ∪(token → skill_ids)，至少有一个 query token 命中
        【变易】use_inverted_index=True 时 O(k) k=命中技能数；
               use_inverted_index=False 或 query_tokens 为空时 O(n) 全量遍历（向后兼容）
               candidate_limit>0 时按 token 命中数降序截断候选集（降级方案，精度换速度）
        【简易】提取公共逻辑，match() 和 _try_rrf_match() 共用

        Args:
            index: 元数据索引
            query_tokens: 查询分词结果
            enabled_only: 是否只匹配启用技能
            min_score: 最低匹配分阈值
            use_inverted_index: 是否启用倒排索引加速
            candidate_limit: 候选集上限（0=不限制）。>0 时按 token 命中数降序截断，
                             适用于 5000+ 技能规模降级（推荐值 200）
        Returns:
            匹配的 SkillMatch 列表（未排序）
        """
        matches: List[SkillMatch] = []

        # 确定候选集：倒排索引筛选 or 全量遍历
        if use_inverted_index and query_tokens:
            inverted = self._get_inverted_index(index)
            # 候选集 = 所有 query token 命中的技能并集，同时记录命中数
            candidate_hits: Dict[str, int] = {}  # skill_id → token 命中数
            for token in query_tokens:
                for sid in inverted.get(token, set()):
                    candidate_hits[sid] = candidate_hits.get(sid, 0) + 1

            # 【变易】candidate_limit 截断：按命中数降序取前 N 个（降级方案）
            # 命中数越多 → _match_score 越高 → 保留高分候选，精度损失最小
            if candidate_limit > 0 and len(candidate_hits) > candidate_limit:
                sorted_ids = sorted(
                    candidate_hits.keys(),
                    key=lambda sid: candidate_hits[sid],
                    reverse=True,
                )[:candidate_limit]
                candidate_ids = set(sorted_ids)
                logger.info(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "loader",
                    "action": "tfidf_scan.candidate_limit_applied",
                    "total_candidates": len(candidate_hits),
                    "limit": candidate_limit,
                    "truncated": len(candidate_hits) - candidate_limit,
                }, ensure_ascii=False))
            else:
                candidate_ids = set(candidate_hits.keys())

            # 对候选集遍历（通常 << n）
            scan_items = [(sid, index[sid]) for sid in candidate_ids if sid in index]
        else:
            # fallback：全量遍历（query_tokens 为空或显式关闭倒排索引）
            scan_items = list(index.items())

        for skill_id, meta in scan_items:
            if enabled_only and not meta.get("enabled", True):
                continue
            meta_text = _meta_to_meta_text(meta)
            score = _match_score(meta_text, query_tokens)
            if score < min_score:
                continue
            meta_str = json.dumps(meta, ensure_ascii=False)
            est_tokens = estimate_tokens(meta_str)
            matches.append(SkillMatch(
                skill_id=skill_id,
                name=meta.get("name", skill_id),
                description=meta.get("description", ""),
                score=score,
                estimated_tokens=est_tokens,
                category=meta.get("category", ""),
                tags=meta.get("tags", []),
                version=meta.get("version", ""),
                enabled=meta.get("enabled", True),
                is_sensitive=bool(meta.get("is_sensitive", False)),
                isolation_strategy=meta.get("isolation_strategy", "separate_turn"),
            ))
        return matches

    # ──────────────────────────────────────────────
    #  第一层：元数据匹配
    # ──────────────────────────────────────────────

    def match(self, intent: str, *, top_k: int = 5,
              enabled_only: bool = True,
              min_score: float = 0.01,
              # 以下为预留扩展点，当前不实现，仅占位（默认 False 保证向后兼容）
              use_vector: bool = False,
              use_bm25: bool = False,
              use_reranker: bool = False,
              retrieval_weights: Optional[Dict[str, float]] = None,
              fusion_mode: str = "none",
              # 【变易】TF-IDF 倒排索引开关 — 默认启用，O(n)→O(k) 加速
              # 关闭时回退全量遍历（守【不易】向后兼容）
              use_inverted_index: bool = True,
              # 【变易】候选集上限（0=不限制）— 5000+ 技能降级方案
              # >0 时按 token 命中数降序截断，精度换速度（推荐值 200）
              candidate_limit: int = 0,
              ) -> MatchResult:
        """第一层匹配 — 当前仅 TF-IDF，接口已预留向量/BM25/Reranker 扩展点

        只读取 skill.md 的 front matter（约 100 token/技能），
        不加载 body 或脚本，大幅节省上下文成本。

        Args:
            intent: 用户意图文本（如"帮我解析PDF文件"）
            top_k: 返回前 K 个匹配
            enabled_only: 是否只匹配启用状态的技能
            min_score: 最低匹配分阈值
            use_vector: 启用向量检索（BGE-m3 via sentence-transformers）
            use_bm25: 启用 BM25 辅助检索（rank_bm25，纯 Python 无 native 依赖）
                      匹配专有名词与确定性锚点；fusion_mode="none" 时自动升 "rrf"
                      与 TF-IDF（+向量）融合；rank_bm25 未安装时静默降级
            use_reranker: 启用 Cross-Encoder 精排（BGE-reranker-v2-m3）
                         仅在 use_vector=True 且 fusion_mode="rrf" 时生效
                         失败时降级为无 rerank
            retrieval_weights: 多路融合（RRF）权重 {"tfidf","vector","bm25"}；
                      None 则用 config.yaml skills_mgmt.retrieval.fusion.weights
                      或默认 {tfidf:0.2, vector:0.6, bm25:0.2}；仅 use_bm25=True 时生效
                      （use_bm25=False 走双路无权重 _rrf_fuse，守旧版兼容）
            fusion_mode: 融合模式，可选:
                - "none"（默认）：单路检索（use_vector 决定走 TF-IDF 或向量）
                  注意：use_bm25=True 时自动升级为 "rrf"
                - "rrf": Reciprocal Rank Fusion，融合 TF-IDF + 向量（+ BM25）
                  用 score(d)=Σ w_i/(k+rank_i(d)) 融合排序（k=60 业界标准）
                  use_vector 或 use_bm25 任一启用即可触发；向量路失败时
                  有 BM25 兜底则走 tfidf+bm25，否则降级 TF-IDF 单路
                - "rrf_rerank": RRF + Cross-Encoder 精排
                  RRF 召回 top-N（N=2*top_k）→ Cross-Encoder 精排 → 取 top_k
                  仅在 use_vector=True 且 use_reranker=True 时生效
            use_inverted_index: TF-IDF 倒排索引开关（默认 True）。
                True 时用 token→skill_id 倒排索引筛选候选集，O(n)→O(k) 加速；
                False 时全量遍历（向后兼容，用于对比测试）。
                匹配语义不变：仍用 _match_score 精确计算，仅减少不必要的遍历。

        Returns: MatchResult

        【不易】fusion_mode="none" 且 use_bm25=False 时行为完全等同旧版（向后兼容）
        【变易】use_bm25=True 触发三路加权 RRF 融合（tfidf+vector+bm25）
        【简易】融合/精排算法为独立私有方法，便于单元测试与可观测性
        """
        t0 = time.time()
        tid = _trace_id()

        # 扩展点防御：use_vector=True 时走向量检索分支，失败降级 TF-IDF
        # 【变易】use_bm25 已实现：触发 RRF 多路融合（tfidf+bm25 或 tfidf+vector+bm25）
        fallback_used = False

        # ── BM25 自动启用 RRF 融合 ──
        # 【变易】use_bm25=True 且 fusion_mode="none" 时自动升级为 "rrf"
        #         （与 use_reranker 自动升级 rrf→rrf_rerank 同模式，让用户 use_bm25=True 即可生效）
        # BM25 需与 TF-IDF 融合才有意义（单路 BM25 无排名融合价值）
        if use_bm25 and fusion_mode == "none":
            fusion_mode = "rrf"

        # ── RRF + Rerank 融合模式 ──
        # 【变易】使用 use_reranker=True 触发 rrf_rerank 模式
        # 自动将 fusion_mode 升级为 rrf_rerank
        if use_reranker and use_vector and fusion_mode == "rrf":
            fusion_mode = "rrf_rerank"

        # ── RRF 融合模式：TF-IDF + 向量（+ BM25）多路融合 ──
        # 【变易】use_vector 或 use_bm25 任一启用即可触发 RRF（BM25 可独立于 vector 工作）
        # 失败降级到下方 TF-IDF 单路（守【不易】兼容性）
        if (use_vector or use_bm25) and fusion_mode in ("rrf", "rrf_rerank"):
            rrf_result = self._try_rrf_match(
                intent=intent,
                top_k=top_k,
                enabled_only=enabled_only,
                min_score=min_score,
                tid=tid,
                t0=t0,
                use_reranker=(fusion_mode == "rrf_rerank"),
                use_bm25=use_bm25,
                retrieval_weights=retrieval_weights,
                use_inverted_index=use_inverted_index,
                candidate_limit=candidate_limit,
            )
            if rrf_result is not None:
                return rrf_result
            # RRF 融合失败（向量路不可用或两路均空），降级 TF-IDF 单路
            fallback_used = True
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "match.rrf_fallback_to_tfidf",
                "intent": intent[:100],
                "fallback": "tfidf",
            }, ensure_ascii=False))

        if use_vector and fusion_mode not in ("rrf", "rrf_rerank"):
            # 尝试向量检索，失败则降级 TF-IDF
            vector_results = self._try_vector_match(
                intent=intent,
                top_k=top_k,
                enabled_only=enabled_only,
                min_score=min_score,
                tid=tid,
            )
            if vector_results is not None:
                # 向量检索成功，记录可观测性并返回
                elapsed = (time.time() - t0) * 1000
                total_tokens = sum(m.estimated_tokens for m in vector_results.matches)

                logger.info(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "match.layer1.vector.ok",
                    "duration_ms": round(elapsed, 2),
                    "layer": 1,
                    "intent": intent[:100],
                    "total_scanned": vector_results.total_scanned,
                    "match_count": len(vector_results.matches),
                    "estimated_tokens": total_tokens,
                    "retrieval_method": "vector",
                    "fallback_used": False,
                    "retrieved_chunks_count": len(vector_results.matches),
                }, ensure_ascii=False))

                emit_metric("yunshu_skill_match_latency_ms",
                            value=elapsed, kind="histogram",
                            labels={"layer": "1", "method": "vector", "success": "true"})
                emit_metric("yunshu_skill_match_count",
                            value=len(vector_results.matches), kind="gauge",
                            labels={"layer": "1", "method": "vector"})
                # [变易] prometheus_client 原生指标（供 HPA histogram_quantile / rate()）
                _record_skill_match_prometheus("1", "vector", True, elapsed)

                from .observability import report_retrieval_observability
                report_retrieval_observability(
                    vector_results.retrieved_chunks, trace_id=tid,
                )
                return vector_results
            # 向量检索失败，降级 TF-IDF
            fallback_used = True
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "match.vector_fallback_to_tfidf",
                "intent": intent[:100],
                "fallback": "tfidf",
            }, ensure_ascii=False))

        # 【变易】use_bm25 已在 RRF 分支实现（自动升 rrf），此处不再警告
        # use_reranker 未生效场景：需 use_vector=True 才能精排，请求未满足时记录 warning
        if use_reranker and not use_vector:
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "match.reranker_not_applied",
                "intent": intent[:100],
                "use_reranker": use_reranker,
                "reason": "reranker requires use_vector=True",
                "fallback": "tfidf",
            }, ensure_ascii=False))
            fallback_used = True

        # 加载元数据索引（第一层，只读 front matter）
        index = self.fs.load_metadata_index()
        query_tokens = _tokenize(intent)

        # 【变易】TF-IDF 扫描 — 倒排索引加速（O(n)→O(k)），匹配语义不变
        candidates = self._tfidf_scan(
            index=index,
            query_tokens=query_tokens,
            enabled_only=enabled_only,
            min_score=min_score,
            use_inverted_index=use_inverted_index,
            candidate_limit=candidate_limit,
        )

        # 按匹配分降序排列
        candidates.sort(key=lambda m: m.score, reverse=True)
        top = candidates[:top_k]

        elapsed = (time.time() - t0) * 1000
        total_tokens = sum(m.estimated_tokens for m in top)

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "match.layer1.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 1,
            "intent": intent[:100],
            "total_scanned": len(index),
            "match_count": len(top),
            "estimated_tokens": total_tokens,
            "retrieval_method": "tfidf",
            "fallback_used": fallback_used,
            # [变易] 可观测性：仅记录召回数，完整 chunks 通过 span 持久化
            "retrieved_chunks_count": len(top),
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_match_latency_ms",
                    value=elapsed, kind="histogram",
                    labels={"layer": "1", "success": "true"})
        emit_metric("yunshu_skill_match_count",
                    value=len(top), kind="gauge",
                    labels={"layer": "1"})
        # [变易] prometheus_client 原生指标（供 HPA histogram_quantile / rate()）
        _record_skill_match_prometheus("1", "tfidf", True, elapsed)

        result = MatchResult(
            matches=top,
            total_scanned=len(index),
            elapsed_ms=elapsed,
            estimated_total_tokens=total_tokens,
            retrieval_method="tfidf",
            fallback_used=fallback_used,
        )

        # [变易] 可观测性：将 retrieved_chunks 持久化到 trace span
        # 失败不影响主流程（report_retrieval_observability 内部已 try/except）
        from .observability import report_retrieval_observability
        report_retrieval_observability(
            result.retrieved_chunks, trace_id=tid,
        )

        # [Observability] INFO 级别：retrieved_chunks 详情，正式环境可观测
        logger.info(
            "[Observability] loader.match retrieved_chunks | trace_id=%s | "
            "count=%d | chunks=%s",
            tid, len(result.retrieved_chunks), result.retrieved_chunks,
        )

        return result

    # ──────────────────────────────────────────────
    #  向量检索扩展（use_vector=True 时调用）
    # ──────────────────────────────────────────────

    def _get_vector_adapter(self):
        """延迟创建向量适配器（首次 use_vector=True 时实例化）

        【变易】避免 SkillLoader.__init__ 拉起 chromadb/torch；
                测试可通过构造函数注入 mock 适配器
        【变易】创建后注册 upsert 到 file_store 写入钩子，
                skill.md 变更时自动增量更新向量索引
        """
        if self._vector_adapter is None:
            try:
                from .vector_adapter import SkillVectorAdapter
                adapter = SkillVectorAdapter(file_store=self.fs)
                self._vector_adapter = adapter
                # [变易] 注册写入钩子 — skill.md create/update/delete 后触发 adapter.upsert
                # 增量同步向量索引，避免索引与 skill.md 不同步
                # 包装为 lambda 忽略 action 参数（upsert 只需 skill_id，delete 也走 _remove）
                try:
                    self.fs.register_write_hook(
                        lambda sid, action: adapter.upsert(sid)
                    )
                    logger.info(json.dumps({
                        "module_name": "loader",
                        "action": "vector_adapter_hook_registered",
                        "hook": "upsert",
                    }, ensure_ascii=False))
                except Exception as hook_e:  # noqa: BLE001
                    # 钩子注册失败不影响向量检索主流程，仅丧失增量同步能力
                    logger.warning(json.dumps({
                        "module_name": "loader",
                        "action": "vector_adapter_hook_register_failed",
                        "error": str(hook_e)[:200],
                    }, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "loader",
                    "action": "vector_adapter_init_failed",
                    "error": str(e),
                }, ensure_ascii=False))
                self._vector_adapter = None
        return self._vector_adapter

    def _try_vector_match(
        self,
        *,
        intent: str,
        top_k: int,
        enabled_only: bool,
        min_score: float,
        tid: str,
    ) -> Optional[MatchResult]:
        """尝试向量检索，失败返回 None（由调用方降级 TF-IDF）

        【不易】返回 None 而非抛异常，保证 match() 主流程不被向量失败拖垮
        【简易】只做查询 → SkillMatch 转换，索引构建由适配器内部延迟完成
        """
        adapter = self._get_vector_adapter()
        if adapter is None:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "vector.adapter_unavailable",
                "intent": intent[:100],
                "reason": "_get_vector_adapter returned None",
            }, ensure_ascii=False))
            return None

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "vector.adapter_ready",
            "intent": intent[:100],
            "adapter_type": type(adapter).__name__,
        }, ensure_ascii=False))

        # 【不易】BM25 fallback 模式不是真正的向量语义检索
        # 在 SKILLS_OFFLINE=1 的 CI 环境中，sentence_transformers/chromadb 被禁用，
        # VectorStore 会降级到 BM25 倒排索引模式。这不是用户请求 use_vector=True 时
        # 期望的向量语义检索，应返回 None 让调用方降级到 TF-IDF
        # （保持 fallback_used=True 的语义正确性，测试 test_match_fallback_flag_when_vector_requested 守卫此契约）
        if getattr(adapter, '_st_backend', None) is None and \
           getattr(adapter, '_native_chroma', None) is None:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "vector.skipped_bm25_fallback",
                "reason": "BM25 fallback is not real vector search",
            }, ensure_ascii=False))
            return None

        try:
            results = adapter.search(
                intent, top_k=top_k, enabled_only=enabled_only,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "vector_search.exception",
                "intent": intent[:100],
                "error": str(e),
            }, ensure_ascii=False))
            return None

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "vector_search.results",
            "intent": intent[:100],
            "result_count": len(results),
            "top1_skill_id": results[0]["skill_id"] if results else None,
            "top1_score": round(results[0]["score"], 4) if results else None,
        }, ensure_ascii=False))

        if not results:
            return None

        # 加载元数据索引（用于补全 SkillMatch 字段，避免向量结果中 metadata 不全）
        index = self.fs.load_metadata_index()

        matches: List[SkillMatch] = []
        for r in results:
            skill_id = r["skill_id"]
            score = r["score"]
            if score < min_score:
                continue
            meta = index.get(skill_id, {})
            meta_str = json.dumps(meta, ensure_ascii=False)
            est_tokens = estimate_tokens(meta_str)
            matches.append(SkillMatch(
                skill_id=skill_id,
                name=meta.get("name", skill_id),
                description=meta.get("description", ""),
                score=score,
                estimated_tokens=est_tokens,
                category=meta.get("category", ""),
                tags=meta.get("tags", []),
                version=meta.get("version", ""),
                enabled=meta.get("enabled", True),
                is_sensitive=bool(meta.get("is_sensitive", False)),
                isolation_strategy=meta.get("isolation_strategy", "separate_turn"),
            ))

        if not matches:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "vector_search.all_filtered_by_min_score",
                "min_score": min_score,
                "raw_result_count": len(results),
            }, ensure_ascii=False))
            return None

        total_tokens = sum(m.estimated_tokens for m in matches)
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "vector_match.done",
            "intent": intent[:100],
            "matches_count": len(matches),
            "top1_skill_id": matches[0].skill_id if matches else None,
            "top1_score": round(matches[0].score, 4) if matches else None,
            "retrieval_method": "vector",
        }, ensure_ascii=False))
        return MatchResult(
            matches=matches,
            total_scanned=len(index),
            elapsed_ms=0.0,  # 外层会重新计算并覆盖
            estimated_total_tokens=total_tokens,
            retrieval_method="vector",
            fallback_used=False,
        )

    # ──────────────────────────────────────────────
    #  BM25 检索扩展（use_bm25=True 时调用）
    # ──────────────────────────────────────────────

    def _get_bm25_searcher(self):
        """延迟创建 BM25 检索器（首次 use_bm25=True 时实例化）

        【变易】避免 SkillLoader.__init__ 拉起 rank_bm25；
                测试可通过构造函数注入 mock searcher（setattr _bm25_searcher_instance）
        【简易】rank_bm25 未安装时返回 None，调用方静默降级
        """
        if not hasattr(self, "_bm25_searcher_instance"):
            try:
                from .bm25_searcher import BM25SkillSearcher
                searcher = BM25SkillSearcher()
                # 立即用当前元数据索引构建（懒加载语义：首次启用即建索引）
                index = self.fs.load_metadata_index()
                # load_metadata_index 返回 {skill_id: meta_dict}，补 id 字段后传入
                skills_for_index = []
                for skill_id, meta in index.items():
                    meta_with_id = dict(meta)
                    meta_with_id.setdefault("id", skill_id)
                    skills_for_index.append(meta_with_id)
                searcher.build_index(skills_for_index)
                self._bm25_searcher_instance = searcher
                logger.info(json.dumps({
                    "module_name": "loader",
                    "action": "bm25_searcher.init",
                    "indexed_count": len(index),
                    "available": searcher.is_available(),
                }, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "loader",
                    "action": "bm25_searcher_init_failed",
                    "error": str(e)[:200],
                }, ensure_ascii=False))
                self._bm25_searcher_instance = None
        return self._bm25_searcher_instance

    def _try_bm25_match(
        self,
        *,
        intent: str,
        top_k: int,
        enabled_only: bool,
        tid: str,
    ) -> List[SkillMatch]:
        """BM25 检索，失败/不可用时返回空列表（由调用方决定是否降级）

        【不易】返回空列表而非 None，与 TF-IDF/向量路的"失败降级"语义对齐
                —— 三路融合时某路失败不阻塞，用其余两路结果
        【简易】BM25Match → SkillMatch 转换，复用元数据补全字段
        """
        searcher = self._get_bm25_searcher()
        if searcher is None or not searcher.is_available():
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "bm25.adapter_unavailable",
                "intent": intent[:100],
            }, ensure_ascii=False))
            return []

        try:
            results = searcher.search(intent, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "bm25_search.exception",
                "intent": intent[:100],
                "error": str(e)[:200],
            }, ensure_ascii=False))
            return []

        if not results:
            return []

        # 加载元数据索引补全 SkillMatch 字段（与 _try_vector_match 同模式）
        index = self.fs.load_metadata_index()
        matches: List[SkillMatch] = []
        for r in results:
            skill_id = r.skill_id
            meta = index.get(skill_id, {})
            meta_str = json.dumps(meta, ensure_ascii=False)
            est_tokens = estimate_tokens(meta_str)
            matches.append(SkillMatch(
                skill_id=skill_id,
                name=meta.get("name", skill_id),
                description=meta.get("description", ""),
                score=r.score,
                estimated_tokens=est_tokens,
                category=meta.get("category", ""),
                tags=meta.get("tags", []),
                version=meta.get("version", ""),
                enabled=meta.get("enabled", True),
                score_breakdown={"bm25_raw": round(r.score, 4)},
                is_sensitive=bool(meta.get("is_sensitive", False)),
                isolation_strategy=meta.get("isolation_strategy", "separate_turn"),
            ))

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "bm25_match.done",
            "intent": intent[:100],
            "matches_count": len(matches),
            "top1_skill_id": matches[0].skill_id if matches else None,
            "top1_score": round(matches[0].score, 4) if matches else None,
        }, ensure_ascii=False))
        return matches

    # ──────────────────────────────────────────────
    #  RRF 融合检索（use_vector=True 且 fusion_mode="rrf" 时调用）
    # ──────────────────────────────────────────────

    # RRF 公式中 k 值的业界标准（Cormack et al. 2009）：60
    # k 越大，对低位排名的容错越强；k 越小，越偏向头部排名
    _RRF_K = 60

    # 【不易】负样本质量门禁阈值：RRF top1 的 max(各路原始分数) 低于此值判定为负样本误召回
    # 根因：RRF 只看排名，归一化分数（top1 恒为 1.0）无法反映绝对匹配质量。
    #       负样本两路都低分召回时（如 TF-IDF 0.14 + 向量 0.14），RRF 归一化后 score=0.5
    #       误判为高质量，需用原始分数兜底拦截。
    # 数据支撑（FakeModel + 100 技能数据集）:
    #   - 负样本「帮我订一张机票」TF-IDF 0.1429 + 向量 0.1429 → max=0.1429 < 0.3，拦截 ✓
    #   - 正样本「解析PDF文件」TF-IDF 0.7143 → max=0.7143 > 0.3，保留 ✓
    #   - 正样本「检查代码bug」TF-IDF/向量均 >0.5 → 保留 ✓
    # 阈值 0.3 在负样本(0.14)与正样本(0.5+)之间留有安全边际
    _RRF_QUALITY_MIN = 0.3

    def _rrf_fuse(
        self,
        tfidf_matches: List[SkillMatch],
        vector_matches: List[SkillMatch],
        *,
        k: int = _RRF_K,
    ) -> List[SkillMatch]:
        """Reciprocal Rank Fusion — 融合两路检索结果

        RRF 公式: score(d) = Σ 1/(k + rank_i(d))，rank 从 1 开始

        特性:
            - 不依赖原始分数量纲（TF-IDF 与 cosine 相似度量级不同），仅看排名
            - 两路都命中的技能分数累加，自然获得提升
            - 单路命中的技能保留单次贡献，作为补充召回

        Args:
            tfidf_matches: TF-IDF 路检索结果（按 score 降序）
            vector_matches: 向量路检索结果（按 similarity 降序）
            k: RRF 平滑参数，默认 60

        Returns:
            融合后的 SkillMatch 列表（按 RRF 分数降序），
            每个 SkillMatch 的 score 字段为 RRF 归一化分数（0~1），
            score_breakdown 透出 {"tfidf": rank, "vector": rank, "rrf": score}
        """
        # skill_id -> (融合分, 原始 SkillMatch, 各路排名)
        fused: Dict[str, Dict[str, Any]] = {}

        # 【可观测性】_rrf_fuse 入口日志：记录两路候选数与 k 值
        # 排查排序异常时, 配合 _try_rrf_match 的 rrf.paths_before_fuse 日志定位输入
        logger.debug(json.dumps({
            "module_name": "loader",
            "action": "_rrf_fuse.input",
            "tfidf_count": len(tfidf_matches),
            "vector_count": len(vector_matches),
            "k": k,
        }, ensure_ascii=False))

        # TF-IDF 路贡献
        # 【变易】记录原始分数 tfidf_score，供融合后负样本质量门禁使用
        #         RRF 只看排名，归一化分数无法反映绝对匹配质量，需原始分数兜底
        for rank, m in enumerate(tfidf_matches, start=1):
            contrib = 1.0 / (k + rank)
            if m.skill_id not in fused:
                fused[m.skill_id] = {
                    "match": m,
                    "rrf_score": 0.0,
                    "tfidf_rank": rank,
                    "vector_rank": None,
                    "tfidf_score": None,
                    "vector_score": None,
                }
            fused[m.skill_id]["rrf_score"] += contrib
            fused[m.skill_id]["tfidf_rank"] = rank
            fused[m.skill_id]["tfidf_score"] = round(m.score, 6)

        # 向量路贡献
        for rank, m in enumerate(vector_matches, start=1):
            contrib = 1.0 / (k + rank)
            if m.skill_id not in fused:
                fused[m.skill_id] = {
                    "match": m,
                    "rrf_score": 0.0,
                    "tfidf_rank": None,
                    "vector_rank": rank,
                    "tfidf_score": None,
                    "vector_score": None,
                }
            fused[m.skill_id]["rrf_score"] += contrib
            fused[m.skill_id]["vector_rank"] = rank
            fused[m.skill_id]["vector_score"] = round(m.score, 6)

        # 【可观测性】两路都命中文档的贡献详情（排查并列排序问题的关键）
        # both_paths=true 的文档 rrf_score 累加两次, 应严格高于单路命中文档
        # 若两者相等, 说明 max_possible 上界计算有误（参见 _rrf_fuse_weighted 修复）
        both_paths_docs = [
            {
                "skill_id": sid,
                "tfidf_rank": info["tfidf_rank"],
                "vector_rank": info["vector_rank"],
                "rrf_score": round(info["rrf_score"], 6),
            }
            for sid, info in fused.items()
            if info["tfidf_rank"] is not None and info["vector_rank"] is not None
        ]
        logger.debug(json.dumps({
            "module_name": "loader",
            "action": "_rrf_fuse.both_paths_contrib",
            "both_paths_count": len(both_paths_docs),
            "both_paths_docs_top5": both_paths_docs[:5],
            "max_possible": round(2.0 / (k + 1), 6),
        }, ensure_ascii=False))

        # 构造融合后的 SkillMatch（保留首个出现的 SkillMatch 元数据字段）
        # RRF 分数归一化到 [0, 1]：最大可能分数 = 2/(k+1)（两路均为 rank 1）
        max_possible = 2.0 / (k + 1)
        result: List[SkillMatch] = []
        for skill_id, info in fused.items():
            m: SkillMatch = info["match"]
            normalized_score = min(1.0, info["rrf_score"] / max_possible)
            # 复制原 SkillMatch 字段，替换 score 与 score_breakdown
            # 【简易】直接构造新对象而非原地修改，避免影响两路原始结果
            result.append(SkillMatch(
                skill_id=m.skill_id,
                name=m.name,
                description=m.description,
                score=normalized_score,
                estimated_tokens=m.estimated_tokens,
                category=m.category,
                tags=m.tags,
                version=m.version,
                enabled=m.enabled,
                score_breakdown={
                    "tfidf_rank": info["tfidf_rank"],
                    "vector_rank": info["vector_rank"],
                    # 【变易】透出各路原始分数，供负样本质量门禁与排查使用
                    "tfidf_score": info["tfidf_score"],
                    "vector_score": info["vector_score"],
                    "rrf_score": round(info["rrf_score"], 6),
                    "rrf_normalized": round(normalized_score, 4),
                },
                is_sensitive=m.is_sensitive,
                isolation_strategy=m.isolation_strategy,
            ))

        # 按 RRF 归一化分数降序
        result.sort(key=lambda x: x.score, reverse=True)
        return result

    # 默认三路融合权重（与 config.yaml skills_mgmt.retrieval.fusion.weights 同源）
    # 【变易】可通过 match(retrieval_weights=...) 运行时覆盖；
    #         分层配置优先级: .env > config.yaml > 硬编码默认值，见 _get_default_weights()
    _DEFAULT_RETRIEVAL_WEIGHTS: Dict[str, float] = {
        "tfidf": 0.2,
        "vector": 0.6,
        "bm25": 0.2,
    }

    # ════════════════════════════════════════════════════════════
    #  分层配置缓存（类变量实现，所有实例共享，等效模块级缓存）
    # ════════════════════════════════════════════════════════════
    # config.yaml mtime 缓存: (mtime_timestamp, weights_dict)
    # mtime 变化时自动失效，避免每次 match() 都读 YAML
    _CONFIG_YAML_CACHE: Optional[Tuple[float, Dict[str, float]]] = None
    _CONFIG_YAML_PATH: Optional[Any] = None  # Path 对象，延迟初始化
    # .env 进程级缓存: weights_dict（进程内环境变量不变，无需 mtime 检测）
    _ENV_CACHE: Optional[Dict[str, float]] = None

    # ════════════════════════════════════════════════════════════
    #  缓存指标计数器（供 Prometheus exporter 读取，运维监控用）
    # ════════════════════════════════════════════════════════════
    _CONFIG_CACHE_HITS: int = 0          # config.yaml 缓存命中次数
    _CONFIG_CACHE_MISSES: int = 0        # config.yaml 缓存未命中次数（首次读取/失效后重建）
    _CONFIG_CACHE_INVALIDATIONS: int = 0 # config.yaml 缓存失效次数（mtime 变化/文件删除）
    _ENV_CACHE_HITS: int = 0             # .env 缓存命中次数
    _CONFIG_READ_FAILURES: int = 0       # config.yaml 读取失败次数（解析错误/IO异常）

    @classmethod
    def _parse_config_yaml_weights(cls, config_path: Any) -> Dict[str, float]:
        """解析 config.yaml 中的 fusion weights（纯解析，不含缓存逻辑）

        【不易】任何异常都静默降级，返回空 dict（不影响硬编码默认值）
        【简易】与 verify_config_yaml_degradation.py 同源，覆盖 6 种失败场景
        """
        weights: Dict[str, float] = {}
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            fusion_weights = (
                config.get("skills_mgmt", {})
                .get("retrieval", {})
                .get("fusion", {})
                .get("weights", {})
            )
            for key in ("tfidf", "vector", "bm25"):
                if key in fusion_weights:
                    val = fusion_weights[key]
                    if val is not None:
                        fval = float(val)
                        # 【变易】业务校验：融合权重必须落在 (0, 1] 合法区间。
                        # 负数/超界权重视为篡改（如负数注入、bm25=999），
                        # 抛异常走 except 统一降级路径并递增 READ_FAILURES 告警。
                        if not (0.0 < fval <= 1.0):
                            raise ValueError(
                                f"融合权重 {key}={fval} 超出合法区间 (0, 1]"
                            )
                        weights[key] = fval
        except Exception as e:
            cls._CONFIG_READ_FAILURES += 1
            logger.warning(json.dumps({
                "module_name": "loader",
                "action": "_parse_config_yaml_weights.failed",
                "error": str(e)[:200],
                "fallback": "empty dict (降级到硬编码默认值)",
            }, ensure_ascii=False))
        return weights

    @classmethod
    def _load_weights_from_config_yaml_cached(cls) -> Dict[str, float]:
        """从 config.yaml 读取 fusion weights（带 mtime 缓存）

        缓存策略:
        - 首次调用: 读 config.yaml，记录 mtime + weights
        - 后续调用: 比对 mtime，未变则返回缓存，变了则重新读取
        - 文件删除: 清除缓存，返回空 dict（降级到硬编码默认值）

        【不易】mtime 变化是缓存失效的唯一触发条件
        【变易】模块级缓存避免每次 match() 都解析 YAML（性能提升 ~15x）
        【简易】stat().st_mtime 比对是 O(1) 操作，无需 TTL 定时器
        """
        from pathlib import Path

        # 延迟初始化路径（只算一次）
        if cls._CONFIG_YAML_PATH is None:
            cls._CONFIG_YAML_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        config_path = cls._CONFIG_YAML_PATH

        # 文件不存在 → 清除缓存，返回空
        if not config_path.exists():
            if cls._CONFIG_YAML_CACHE is not None:
                cls._CONFIG_CACHE_INVALIDATIONS += 1
                logger.debug(json.dumps({
                    "module_name": "loader",
                    "action": "_load_weights_from_config_yaml_cached.file_deleted",
                    "fallback": "empty dict",
                }, ensure_ascii=False))
            cls._CONFIG_YAML_CACHE = None
            return {}

        # 获取当前 mtime
        try:
            current_mtime = config_path.stat().st_mtime
        except OSError:
            current_mtime = 0.0

        # 缓存命中检查（mtime 未变 → 返回副本）
        if cls._CONFIG_YAML_CACHE is not None:
            cached_mtime, cached_weights = cls._CONFIG_YAML_CACHE
            if cached_mtime == current_mtime:
                cls._CONFIG_CACHE_HITS += 1
                return dict(cached_weights)
            else:
                cls._CONFIG_CACHE_INVALIDATIONS += 1

        # 缓存未命中或失效 → 重新解析
        cls._CONFIG_CACHE_MISSES += 1
        weights = cls._parse_config_yaml_weights(config_path)
        cls._CONFIG_YAML_CACHE = (current_mtime, weights)
        return dict(weights)

    @classmethod
    def _load_weights_from_env_cached(cls) -> Dict[str, float]:
        """从 .env 读取 fusion weights（带进程级缓存）

        缓存策略:
        - 进程级缓存，首次读取后不再重复读取
        - 环境变量在进程内不变（除非 os.environ 显式修改）
        - 测试中需调用 _clear_env_cache() 清除缓存

        【不易】进程内环境变量不变，缓存无需 mtime 检测
        【变易】与 config.yaml 缓存保持一致的 API 风格
        【简易】os.environ.get 开销极低，缓存主要为一致性

        ⚠️ 风险: 运维在进程运行期间通过 os.environ 修改环境变量时，
           缓存不会感知（需重启进程或手动调用 _clear_env_cache()）
        """
        if cls._ENV_CACHE is not None:
            cls._ENV_CACHE_HITS += 1
            return dict(cls._ENV_CACHE)

        weights: Dict[str, float] = {}
        for key, env_name in [
            ("tfidf", "SKILLS_FUSION_WEIGHT_TFIDF"),
            ("vector", "SKILLS_FUSION_WEIGHT_VECTOR"),
            ("bm25", "SKILLS_FUSION_WEIGHT_BM25"),
        ]:
            raw = os.environ.get(env_name)
            if raw is None or not raw.strip():
                continue
            try:
                weights[key] = float(raw)
            except (ValueError, TypeError):
                logger.warning(json.dumps({
                    "module_name": "loader",
                    "action": "_load_weights_from_env_cached.invalid",
                    "env_name": env_name,
                    "raw_value": raw[:50],
                    "fallback": "skip (降级到下层)",
                    "reason": "non-float value",
                }, ensure_ascii=False))

        cls._ENV_CACHE = weights
        return dict(weights)

    @classmethod
    def _clear_config_cache(cls) -> None:
        """手动清除 config.yaml 缓存（测试用 / config.yaml 修改后强制刷新）"""
        cls._CONFIG_YAML_CACHE = None

    @classmethod
    def _clear_env_cache(cls) -> None:
        """手动清除 .env 缓存（测试用 / 运维 hotfix 后强制刷新）"""
        cls._ENV_CACHE = None

    @classmethod
    def _clear_all_caches(cls) -> None:
        """清除所有缓存（测试用：config.yaml + .env）"""
        cls._clear_config_cache()
        cls._clear_env_cache()

    @classmethod
    def _get_default_weights(cls) -> Dict[str, float]:
        """获取默认三路融合权重 — 优先级: .env > config.yaml > 硬编码默认值

        分层配置架构:
            层0: 硬编码默认值（_DEFAULT_RETRIEVAL_WEIGHTS，最终兜底）
            层1: config.yaml（业务配置主源，可版本控制，带 mtime 缓存）
            层2: .env（运维覆盖，优先级最高，带进程级缓存）

        config.yaml 路径: skills_mgmt.retrieval.fusion.weights
        环境变量: SKILLS_FUSION_WEIGHT_TFIDF / _VECTOR / _BM25

        权重不必和为 1，_rrf_fuse_weighted 内部自动归一化（缺失路不参与，剩余路重分配）。
        例：config.yaml bm25=0.5, 其他默认 →
            total=0.2+0.6+0.5=1.3 → 归一化后 tfidf=0.154, vector=0.462, bm25=0.385

        【不易】_DEFAULT_RETRIEVAL_WEIGHTS 作为 fallback 不可删除（守旧版默认行为）
        【变易】config.yaml 让权重可版本控制；.env 允许运维临时覆盖（优先级更高）
        【简易】逐层覆盖，每层失败静默降级到下层；每次调用返回新 dict（线程安全）
        """
        # 层0: 硬编码默认值（最终兜底）
        weights = dict(cls._DEFAULT_RETRIEVAL_WEIGHTS)

        # 层1: config.yaml（带 mtime 缓存，失败返回空 dict 不影响硬编码）
        yaml_weights = cls._load_weights_from_config_yaml_cached()
        weights.update(yaml_weights)

        # 层2: .env（带进程级缓存，优先级最高）
        env_weights = cls._load_weights_from_env_cached()
        weights.update(env_weights)

        return weights

    def _rrf_fuse_weighted(
        self,
        paths: List[Tuple[str, List["SkillMatch"], float]],
        *,
        k: int = _RRF_K,
    ) -> List[SkillMatch]:
        """加权 RRF 融合 — 支持任意路数（tfidf/vector/bm25 三路或任意子集）

        公式: score(d) = Σ w_i / (k + rank_i(d))，rank 从 1 开始

        与 _rrf_fuse 的区别:
            - _rrf_fuse: 两路无权重（隐式 0.5/0.5），守【不易】旧版双路行为不变
            - _rrf_fuse_weighted: N 路加权，权重自动归一化（缺失路不参与，剩余路重分配）

        特性:
            - 某路结果为空时自动跳过（不阻塞融合，守防御性要求）
            - 权重不必和为 1，内部自动归一化
            - score_breakdown 透出各路排名与加权 RRF 分数

        Args:
            paths: [(name, matches, weight), ...]
                   - name: 路径名 "tfidf" / "vector" / "bm25"
                   - matches: 该路按 score 降序的 SkillMatch 列表
                   - weight: 该路权重（>0）
            k: RRF 平滑参数，默认 60

        Returns:
            融合后的 SkillMatch 列表（按加权 RRF 分数降序），
            score 字段归一化到 [0, 1]
        """
        # 过滤空结果路径，仅保留有候选的路参与融合
        active_paths = [
            (name, matches, weight)
            for name, matches, weight in paths
            if matches and weight > 0
        ]
        if not active_paths:
            return []

        # 权重归一化（缺失路不参与，剩余路按比例重分配）
        total_weight = sum(w for _, _, w in active_paths)

        # 【可观测性】_rrf_fuse_weighted 入口日志：记录 active_paths 与归一化权重
        # 排查排序异常的关键：确认失败路被正确过滤、权重正确归一化重分配
        # 例：vector 路不可用时, active_paths 应只剩 tfidf+bm25, 权重重分配为 0.5/0.5
        active_paths_info = [
            {
                "name": name,
                "matches_count": len(matches),
                "raw_weight": round(weight, 4),
                "normalized_weight": round(weight / total_weight, 4) if total_weight > 0 else 0.0,
            }
            for name, matches, weight in active_paths
        ]
        logger.debug(json.dumps({
            "module_name": "loader",
            "action": "_rrf_fuse_weighted.input",
            "active_paths": active_paths_info,
            "total_weight": round(total_weight, 4),
            "skipped_paths_count": len(paths) - len(active_paths),
            "k": k,
        }, ensure_ascii=False))

        # skill_id -> {融合信息}
        fused: Dict[str, Dict[str, Any]] = {}

        for name, matches, weight in active_paths:
            normalized_weight = weight / total_weight if total_weight > 0 else 0.0
            for rank, m in enumerate(matches, start=1):
                contrib = normalized_weight / (k + rank)
                if m.skill_id not in fused:
                    fused[m.skill_id] = {
                        "match": m,
                        "rrf_score": 0.0,
                        "ranks": {},  # {path_name: rank}
                        "scores": {},  # {path_name: original_score} 负样本门禁用
                    }
                fused[m.skill_id]["rrf_score"] += contrib
                fused[m.skill_id]["ranks"][name] = rank
                fused[m.skill_id]["scores"][name] = round(m.score, 6)

        # 【可观测性】多路命中文档的贡献详情（排查加权排序问题的关键）
        # 多路命中文档 rrf_score 累加多次, 应严格高于单路命中文档
        # 若 max_possible 计算错误（如旧版用单路最大权重/(k+1)）, 多路命中文档会被
        # 错误 cap 到 1.0, 与单路命中文档 score 相等, 丢失排序区分度
        multi_path_docs = [
            {
                "skill_id": sid,
                "ranks": dict(info["ranks"]),
                "path_count": len(info["ranks"]),
                "rrf_score": round(info["rrf_score"], 6),
            }
            for sid, info in fused.items()
            if len(info["ranks"]) >= 2
        ]
        logger.debug(json.dumps({
            "module_name": "loader",
            "action": "_rrf_fuse_weighted.multi_path_contrib",
            "multi_path_count": len(multi_path_docs),
            "multi_path_docs_top5": multi_path_docs[:5],
            "max_possible": round(1.0 / (k + 1), 6),
            "note": "max_possible=1.0/(k+1) is the upper bound (doc ranked #1 in all active paths)",
        }, ensure_ascii=False))

        # 归一化到 [0, 1]：最大可能分数 = Σ w_i_normalized / (k+1) = 1.0/(k+1)
        # 【不易修复】上界应是"所有 active 路归一化权重之和 / (k+1)"（某文档在所有路都 rank=1），
        # 而非单路最大权重/(k+1)。修复前多路命中文档的 rrf_score 会超过上界被错误 cap 到 1.0，
        # 与单路命中文档 score 相等，丢失排序区分度。
        # 归一化后 Σ normalized_weight = 1.0，故 max_possible 恒为 1.0/(k+1)
        max_possible = 1.0 / (k + 1)

        result: List[SkillMatch] = []
        for skill_id, info in fused.items():
            m: SkillMatch = info["match"]
            normalized_score = min(1.0, info["rrf_score"] / max_possible) if max_possible > 0 else 0.0
            # 各路排名透出到 score_breakdown（缺失路为 None）
            ranks = info["ranks"]
            scores = info["scores"]
            breakdown = {f"{name}_rank": ranks.get(name) for name, _, _ in paths}
            # 【变易】透出各路原始分数，供负样本质量门禁与排查使用
            breakdown.update({f"{name}_score": scores.get(name) for name, _, _ in paths})
            breakdown["rrf_score"] = round(info["rrf_score"], 6)
            breakdown["rrf_normalized"] = round(normalized_score, 4)
            result.append(SkillMatch(
                skill_id=m.skill_id,
                name=m.name,
                description=m.description,
                score=normalized_score,
                estimated_tokens=m.estimated_tokens,
                category=m.category,
                tags=m.tags,
                version=m.version,
                enabled=m.enabled,
                score_breakdown=breakdown,
                is_sensitive=m.is_sensitive,
                isolation_strategy=m.isolation_strategy,
            ))

        result.sort(key=lambda x: x.score, reverse=True)
        return result

    def _try_rrf_match(
        self,
        *,
        intent: str,
        top_k: int,
        enabled_only: bool,
        min_score: float,
        tid: str,
        t0: float,
        use_reranker: bool = False,
        use_bm25: bool = False,
        retrieval_weights: Optional[Dict[str, float]] = None,
        # 【变易】TF-IDF 倒排索引开关 — 透传 match() 的 use_inverted_index
        use_inverted_index: bool = True,
        # 【变易】候选集上限 — 透传 match() 的 candidate_limit（降级方案）
        candidate_limit: int = 0,
    ) -> Optional[MatchResult]:
        """RRF 融合检索：TF-IDF + 向量（+ BM25）多路并行 + 排名融合

        策略:
            1. TF-IDF 单路检索（取前 2*top_k，扩大候选池）
            2. 向量路检索（取前 2*top_k，扩大候选池）
            3. （可选）use_bm25=True 时 BM25 路检索（匹配专有名词/确定性锚点）
            4. RRF 融合：use_bm25 走加权三路 _rrf_fuse_weighted，否则走双路 _rrf_fuse
            5. 按 RRF 分数降序取前候选池
            6. min_score 过滤 RRF 归一化分数
            7. （可选）use_reranker=True 时 Cross-Encoder 精排
            8. 取 top_k

        失败降级:
            - 向量路不可用 + 无 BM25 兜底 → 返回 None，外层降级 TF-IDF 单路
            - 向量路不可用 + 有 BM25 兜底 → 走 tfidf+bm25 两路加权融合
            - 三路均空 → 返回 None
            - TF-IDF 路异常 → 仅用其余路（保持单路降级语义）
            - Reranker 不可用 → 跳过精排，保留 RRF 顺序

        【不易】use_bm25=False 时行为与旧版双路 RRF 完全一致（_rrf_fuse 不变）
        【变易】use_bm25=True 时三路加权融合，权重由 retrieval_weights 或 config 配置
        【简易】融合逻辑集中在 _rrf_fuse / _rrf_fuse_weighted，本方法仅编排

        Args:
            intent: 用户意图
            top_k: 最终返回数量
            enabled_only: 是否只匹配启用技能
            min_score: RRF 归一化分数阈值
            tid: trace_id
            t0: 起始时间戳
            use_reranker: True 则在 RRF 召回后调用 Cross-Encoder 精排
            use_bm25: True 则追加 BM25 第三路（专有名词/确定性锚点匹配）
            retrieval_weights: 三路融合权重 {"tfidf","vector","bm25"}；
                None 则用 _DEFAULT_RETRIEVAL_WEIGHTS（与 config.yaml 同源）

        Returns:
            MatchResult（retrieval_method="rrf"/"rrf_rerank"）或 None（降级）
        """
        # 候选池扩大倍率：RRF 受 rank 影响大，多取候选避免漏召
        # 【变易】2 倍是经验值，平衡召回率与计算成本
        candidate_k = max(top_k * 2, 10)

        # 加载元数据索引（两路共用，避免重复 I/O）
        index = self.fs.load_metadata_index()
        query_tokens = _tokenize(intent)

        # ── TF-IDF 路 ──
        # 【不易修复】TF-IDF 路必须应用 min_score 阈值过滤
        # 原因：若不过滤，低分技能也会获得 RRF 排名，导致负样本 query 被误召回
        # （例："12345" 在 TF-IDF 中 score 极低但仍会被 RRF 赋予 rank 1）
        # 【变易】倒排索引加速：use_inverted_index=True 时 O(k) k=命中数，语义不变
        tfidf_matches: List[SkillMatch] = []
        try:
            tfidf_matches = self._tfidf_scan(
                index=index,
                query_tokens=query_tokens,
                enabled_only=enabled_only,
                min_score=min_score,
                use_inverted_index=use_inverted_index,
                candidate_limit=candidate_limit,
            )
            tfidf_matches.sort(key=lambda m: m.score, reverse=True)
            tfidf_matches = tfidf_matches[:candidate_k]
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "rrf.tfidf_path.exception",
                "intent": intent[:100],
                "error": str(e),
            }, ensure_ascii=False))
            tfidf_matches = []

        # ── 向量路 ──
        # 【不易修复】向量路同样应用 min_score 阈值过滤
        # 原因：让向量自身过滤掉低相似度的负样本，避免无意义候选参与融合
        vector_matches: List[SkillMatch] = []
        adapter = self._get_vector_adapter()
        # 【不易】与 _try_vector_match 同步：检测 BM25-fallback 模式（非真向量后端）
        # fresh SkillVectorAdapter 的 _st_backend/_native_chroma 均为 None，
        # search() 会触发 ensure_indexed → 真模型下载；此处 fast-exit 避免
        # CI/测试环境拉起 BGE-m3（守 project_memory：Embedding 无隔离会崩溃）
        # 注入的 mock adapter 设置 _st_backend 非 None 即可绕过此检测
        if adapter is not None and \
           getattr(adapter, '_st_backend', None) is None and \
           getattr(adapter, '_native_chroma', None) is None:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "rrf.vector.skipped_bm25_fallback",
                "intent": intent[:100],
                "reason": "BM25 fallback is not real vector search",
            }, ensure_ascii=False))
            adapter = None
        if adapter is not None:
            try:
                results = adapter.search(
                    intent, top_k=candidate_k,
                    enabled_only=enabled_only,
                    min_score=min_score,
                )
                for r in results:
                    skill_id = r["skill_id"]
                    score = r["score"]
                    meta = index.get(skill_id, {})
                    meta_str = json.dumps(meta, ensure_ascii=False)
                    est_tokens = estimate_tokens(meta_str)
                    vector_matches.append(SkillMatch(
                        skill_id=skill_id,
                        name=meta.get("name", skill_id),
                        description=meta.get("description", ""),
                        score=score,
                        estimated_tokens=est_tokens,
                        category=meta.get("category", ""),
                        tags=meta.get("tags", []),
                        version=meta.get("version", ""),
                        enabled=meta.get("enabled", True),
                        is_sensitive=bool(meta.get("is_sensitive", False)),
                        isolation_strategy=meta.get("isolation_strategy", "separate_turn"),
                    ))
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.vector_path.exception",
                    "intent": intent[:100],
                    "error": str(e),
                }, ensure_ascii=False))
                vector_matches = []
        else:
            # 向量适配器不可用
            if not use_bm25:
                # 无 BM25 兜底，RRF 无意义，返回 None 触发外层降级（守【不易】旧版语义）
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.vector_adapter_unavailable",
                    "intent": intent[:100],
                }, ensure_ascii=False))
                return None
            # 【变易】有 BM25 兜底：向量路置空，继续走 tfidf+bm25 两路加权融合
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "rrf.vector_unavailable_bm25_fallback",
                "intent": intent[:100],
                "fallback": "tfidf+bm25",
            }, ensure_ascii=False))

        # ── BM25 路（use_bm25=True 时启用）──
        # 【变易】BM25 擅长精确字面匹配，补充向量对专有名词/确定性锚点的召回不足
        # 失败返回空列表（_try_bm25_match 内部已 try/except），不阻塞融合
        bm25_matches: List[SkillMatch] = []
        if use_bm25:
            try:
                bm25_matches = self._try_bm25_match(
                    intent=intent,
                    top_k=candidate_k,
                    enabled_only=enabled_only,
                    tid=tid,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.bm25_path.exception",
                    "intent": intent[:100],
                    "error": str(e)[:200],
                }, ensure_ascii=False))
                bm25_matches = []

        # 三路均空，无法融合 → 返回 None 触发外层 TF-IDF 兜底
        # 【不易】负样本 query 场景：所有路都过滤为空 → 返回 None（而非随机召回）
        if not tfidf_matches and not vector_matches and not bm25_matches:
            return None

        # ── 单路兜底阈值检查 ──
        # 【不易】防御 embedding 模型对中文负样本的误召回
        # 场景：TF-IDF 路过滤为空（字面无匹配），但向量路召回了相似度较低的技能
        # 策略：单路召回时要求向量路 top1 分数 >= 单路阈值，否则认为误召回
        # 阈值经验值：0.45
        # 数据支撑（BGE-m3，all-MiniLM-L6-v2 已被 BGE-m3 替换）:
        #   - case_038 "今天天气真好" 向量 top1 = 0.3612 → 误召回，应拒绝
        #   - case_042 "帮我订一张机票" 向量 top1 = 0.4414 → 误召回，应拒绝
        #   - case_043 "请帮我反思" 向量 top1 = 0.6030 → 真匹配，应保留
        #   - case_007 "帮我梳理历史记忆并压缩" 向量 top1 = 0.6346 → 真匹配，应保留
        #   - case_006 "请总结一下之前的对话历史" 向量 top1 = 0.5102 → 真匹配，应保留
        SINGLE_PATH_MIN_TOP1 = 0.45
        # 【不易】use_bm25=False 时 bm25_matches 恒为空，条件等价旧版 `not tfidf and vector`
        # 【变易】use_bm25=True 且 bm25 有结果时跳过此阈值（BM25 提供独立专有名词信号，
        #         不属于"向量单路误召回"场景）
        if not tfidf_matches and vector_matches and not bm25_matches:
            vec_top1_score = vector_matches[0].score
            if vec_top1_score < SINGLE_PATH_MIN_TOP1:
                logger.info(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.single_path_low_score_rejected",
                    "intent": intent[:100],
                    "vector_top1_score": round(vec_top1_score, 4),
                    "threshold": SINGLE_PATH_MIN_TOP1,
                    "reason": "tfidf empty + vector top1 below single-path threshold",
                }, ensure_ascii=False))
                return None

        # ── RRF 融合 ──
        # 记录各路原始排名（排查 RRF 排序异常的关键日志）
        # 重点标注冲突场景：TF-IDF top1 ≠ 向量 top1（字面匹配与语义匹配分歧）
        tfidf_top3 = [
            {"rank": i + 1, "skill_id": m.skill_id, "score": round(m.score, 4)}
            for i, m in enumerate(tfidf_matches[:3])
        ]
        vector_top3 = [
            {"rank": i + 1, "skill_id": m.skill_id, "score": round(m.score, 4)}
            for i, m in enumerate(vector_matches[:3])
        ]
        # 【变易】BM25 路 top3（use_bm25=True 时记录，便于排查专有名词匹配）
        bm25_top3 = [
            {"rank": i + 1, "skill_id": m.skill_id, "score": round(m.score, 4)}
            for i, m in enumerate(bm25_matches[:3])
        ]
        tfidf_top1_id = tfidf_matches[0].skill_id if tfidf_matches else None
        vector_top1_id = vector_matches[0].skill_id if vector_matches else None
        bm25_top1_id = bm25_matches[0].skill_id if bm25_matches else None
        conflict = (
            tfidf_top1_id is not None
            and vector_top1_id is not None
            and tfidf_top1_id != vector_top1_id
        )
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "rrf.paths_before_fuse",
            "intent": intent[:100],
            "tfidf_top1": tfidf_top1_id,
            "vector_top1": vector_top1_id,
            "bm25_top1": bm25_top1_id,
            "top1_conflict": conflict,
            "tfidf_top3": tfidf_top3,
            "vector_top3": vector_top3,
            "bm25_top3": bm25_top3,
            "tfidf_candidate_count": len(tfidf_matches),
            "vector_candidate_count": len(vector_matches),
            "bm25_candidate_count": len(bm25_matches),
            "use_bm25": use_bm25,
            "rrf_k": self._RRF_K,
        }, ensure_ascii=False))

        # 【不易】use_bm25=False 时走 _rrf_fuse 双路无权重（行为与旧版完全一致）
        # 【变易】use_bm25=True 时走 _rrf_fuse_weighted 加权多路
        #         权重优先级: retrieval_weights 参数 > .env (SKILLS_FUSION_WEIGHT_*) > 硬编码默认值
        #         _get_default_weights() 读取 .env，符合"配置走 .env"硬约束
        if use_bm25:
            weights = retrieval_weights or self._get_default_weights()
            fused = self._rrf_fuse_weighted(
                [
                    ("tfidf", tfidf_matches, weights.get("tfidf", 0.2)),
                    ("vector", vector_matches, weights.get("vector", 0.6)),
                    ("bm25", bm25_matches, weights.get("bm25", 0.2)),
                ],
                k=self._RRF_K,
            )
        else:
            fused = self._rrf_fuse(tfidf_matches, vector_matches, k=self._RRF_K)

        # 记录 RRF 融合结果详情（排查排序异常的关键日志）
        # 每个候选的 tfidf_rank/vector_rank/bm25_rank/rrf_score/rrf_normalized 都在 score_breakdown 中
        fused_detail = []
        for i, m in enumerate(fused[:5], start=1):  # top5 足够排查
            bd = m.score_breakdown or {}
            # 【变易】use_bm25 时 breakdown 用 {name}_rank 形式；旧版双路用 tfidf_rank/vector_rank
            tfidf_rank = bd.get("tfidf_rank")
            vector_rank = bd.get("vector_rank")
            bm25_rank = bd.get("bm25_rank")
            fused_detail.append({
                "final_rank": i,
                "skill_id": m.skill_id,
                "tfidf_rank": tfidf_rank,
                "vector_rank": vector_rank,
                "bm25_rank": bm25_rank,
                "rrf_score": bd.get("rrf_score"),
                "rrf_normalized": bd.get("rrf_normalized"),
                "both_paths": tfidf_rank is not None and vector_rank is not None,
            })
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "rrf.fused_detail",
            "intent": intent[:100],
            "fused_count": len(fused),
            "top5_detail": fused_detail,
            "use_bm25": use_bm25,
            "note": "both_paths=true 表示 tfidf+vector 两路都命中（RRF 分数累加，排名更靠前）",
        }, ensure_ascii=False))

        # 融合后不再二次过滤 min_score：各路已应用阈值，避免归一化分数压缩导致阈值失效

        # ── 负样本质量门禁（基于原始分数阈值过滤）──
        # 【不易】RRF 只看排名，归一化分数（top1 恒为 1.0）无法反映绝对匹配质量。
        #         负样本两路都低分召回时（如 TF-IDF 0.14 + 向量 0.14），RRF 归一化后
        #         score=0.5 误判为高质量，需用各路原始分数的 max 兜底拦截。
        # 【变易】门禁检查 top1 的 max(各路原始分数) < _RRF_QUALITY_MIN 时判定为误召回，
        #         返回空 MatchResult（不触发 TF-IDF fallback，避免引入新误召回路径）。
        # 【简易】仅检查 top1：负样本的典型特征是所有候选原始分数都低，top1 即可代表。
        #         阈值通过类常量 _RRF_QUALITY_MIN 配置，可未来下沉到 config.yaml。
        if fused and self._RRF_QUALITY_MIN > 0:
            top1 = fused[0]
            bd = top1.score_breakdown or {}
            # 提取各路原始分数（双路: tfidf_score/vector_score; 三路: +bm25_score）
            # 排除 rrf_score / rrf_normalized（非各路原始分数）
            raw_scores = [
                v for k, v in bd.items()
                if k.endswith("_score")
                and k not in ("rrf_score",)
                and v is not None
            ]
            max_raw_score = max(raw_scores) if raw_scores else 0.0
            # 记录详细日志：原始分数 + 归一化分数，便于验证负样本过滤逻辑
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "rrf.quality_gate.check",
                "intent": intent[:100],
                "top1_skill_id": top1.skill_id,
                "top1_rrf_normalized": bd.get("rrf_normalized"),
                "raw_scores": {
                    k: v for k, v in bd.items()
                    if k.endswith("_score") and k != "rrf_score" and v is not None
                },
                "max_raw_score": round(max_raw_score, 6),
                "threshold": self._RRF_QUALITY_MIN,
                "use_bm25": use_bm25,
                "decision": "reject" if max_raw_score < self._RRF_QUALITY_MIN else "pass",
            }, ensure_ascii=False))
            if max_raw_score < self._RRF_QUALITY_MIN:
                # 【变易】返回空 MatchResult（retrieval_method="rrf"），不触发 TF-IDF fallback
                # 原因：负样本 query 在 TF-IDF 路本身就是低分召回，fallback 会引入新误召回
                elapsed = (time.time() - t0) * 1000
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.quality_gate.rejected",
                    "intent": intent[:100],
                    "top1_skill_id": top1.skill_id,
                    "max_raw_score": round(max_raw_score, 6),
                    "threshold": self._RRF_QUALITY_MIN,
                    "fused_count": len(fused),
                    "reason": "all_paths_low_raw_score_negative_sample",
                }, ensure_ascii=False))
                emit_metric("yunshu_skill_rrf_quality_gate_rejected",
                            value=1, kind="counter",
                            labels={"layer": "1", "method": "rrf"})
                return MatchResult(
                    matches=[],
                    total_scanned=len(index),
                    elapsed_ms=elapsed,
                    estimated_total_tokens=0,
                    retrieval_method="rrf",
                    fallback_used=False,
                )

        # ── 可选：Cross-Encoder 精排 ──
        # 【变易】use_reranker=True 时，先取较大候选池（2*top_k）做精排，再取 top_k
        # 失败降级：reranker 不可用 → 跳过精排，保留 RRF 顺序
        retrieval_method = "rrf"
        if use_reranker:
            # 取候选池（至少 2*top_k 用于 reranker 排序）
            rerank_pool_size = max(top_k * 2, 10)
            rerank_pool = fused[:rerank_pool_size]

            # 转 dict 列表给 reranker（reranker 输出 dict 列表）
            pool_dicts = []
            for m in rerank_pool:
                pool_dicts.append({
                    "skill_id": m.skill_id,
                    "name": m.name,
                    "description": m.description,
                    "score": m.score,
                    "estimated_tokens": m.estimated_tokens,
                    "category": m.category,
                    "tags": m.tags,
                    "version": m.version,
                    "enabled": m.enabled,
                    "score_breakdown": m.score_breakdown,
                    "metadata": {
                        "skill_id": m.skill_id,
                        "name": m.name,
                        "description": m.description,
                        "category": m.category,
                        "tags": ",".join(m.tags) if m.tags else "",
                        "enabled": m.enabled,
                        "version": m.version,
                    },
                })

            reranker = self._get_reranker()
            if reranker is not None:
                reranked_dicts = reranker.rerank(intent, pool_dicts, top_k=None)
                # 取 top_k
                reranked_dicts = reranked_dicts[:top_k]
                # 重建 SkillMatch（含 rerank_score 透出到 score_breakdown）
                top = []
                for item in reranked_dicts:
                    # 找到原 SkillMatch 以保留 estimated_tokens
                    orig_match = next(
                        (m for m in rerank_pool if m.skill_id == item["skill_id"]),
                        None,
                    )
                    if orig_match is None:
                        continue
                    # 合并 rerank_score 到 score_breakdown
                    new_breakdown = dict(orig_match.score_breakdown or {})
                    new_breakdown["rerank_score"] = item.get("rerank_score", 0.0)
                    new_breakdown["original_rrf_rank"] = item.get("original_rank", 0)
                    top.append(SkillMatch(
                        skill_id=orig_match.skill_id,
                        name=orig_match.name,
                        description=orig_match.description,
                        score=item.get("rerank_score", orig_match.score),
                        estimated_tokens=orig_match.estimated_tokens,
                        category=orig_match.category,
                        tags=orig_match.tags,
                        version=orig_match.version,
                        enabled=orig_match.enabled,
                        score_breakdown=new_breakdown,
                        is_sensitive=orig_match.is_sensitive,
                        isolation_strategy=orig_match.isolation_strategy,
                    ))
                retrieval_method = "rrf_rerank"
                logger.info(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.rerank.applied",
                    "intent": intent[:100],
                    "pool_size": len(rerank_pool),
                    "final_count": len(top),
                }, ensure_ascii=False))
            else:
                # reranker 不可用，降级用 RRF 顺序
                top = fused[:top_k]
                logger.info(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.rerank.skipped",
                    "intent": intent[:100],
                    "reason": "reranker_unavailable",
                }, ensure_ascii=False))
        else:
            top = fused[:top_k]

        # 【变易】阈值过滤语义：reranker 主动过滤后为空，说明无高置信度匹配
        # 不应触发 TF-IDF fallback（会引入新误召回），而是返回空 MatchResult
        # 仅在 use_reranker=True 时启用此语义；RRF 召回本身为空时仍 return None 触发 fallback
        if not top:
            if use_reranker and fused:
                # reranker 阈值过滤导致空结果，返回空 MatchResult（不 fallback）
                logger.info(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "rrf.rerank.filtered_empty",
                    "intent": intent[:100],
                    "fused_count": len(fused),
                    "reason": "all_candidates_below_threshold",
                }, ensure_ascii=False))
                elapsed = (time.time() - t0) * 1000
                return MatchResult(
                    matches=[],
                    total_scanned=len(index),
                    elapsed_ms=elapsed,
                    estimated_total_tokens=0,
                    retrieval_method="rrf_rerank",
                    fallback_used=False,
                )
            # RRF 召回本身为空，触发外层 fallback
            return None

        elapsed = (time.time() - t0) * 1000
        total_tokens = sum(m.estimated_tokens for m in top)

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "match.layer1.rrf.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 1,
            "intent": intent[:100],
            "total_scanned": len(index),
            "tfidf_candidates": len(tfidf_matches),
            "vector_candidates": len(vector_matches),
            "bm25_candidates": len(bm25_matches),
            "fused_count": len(fused),
            "match_count": len(top),
            "estimated_tokens": total_tokens,
            "retrieval_method": retrieval_method,
            "fallback_used": False,
            "retrieved_chunks_count": len(top),
            "rrf_k": self._RRF_K,
            "use_reranker": use_reranker,
            "use_bm25": use_bm25,
            "final_top_skill_ids": [
                {"rank": i + 1, "skill_id": m.skill_id, "score": round(m.score, 4)}
                for i, m in enumerate(top)
            ],
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_match_latency_ms",
                    value=elapsed, kind="histogram",
                    labels={"layer": "1", "method": retrieval_method, "success": "true"})
        emit_metric("yunshu_skill_match_count",
                    value=len(top), kind="gauge",
                    labels={"layer": "1", "method": retrieval_method})
        # [变易] prometheus_client 原生指标（供 HPA histogram_quantile / rate()）
        _record_skill_match_prometheus("1", retrieval_method, True, elapsed)

        result = MatchResult(
            matches=top,
            total_scanned=len(index),
            elapsed_ms=elapsed,
            estimated_total_tokens=total_tokens,
            retrieval_method=retrieval_method,
            # 【不易修复】use_reranker=True 且 reranker 实际被调用时（retrieval_method
            # 已升级为 "rrf_rerank"），必须设置 reranked=True，否则评估脚本读取
            # getattr(result, "reranked", False) 会得到 False，误判 reranker 未生效。
            # 原因：retrieval_method="rrf_rerank" 已表明 reranker 被调用并产出结果，
            # reranked 字段应与之同步（守评估契约一致性）。
            reranked=(retrieval_method == "rrf_rerank"),
            fallback_used=False,
        )

        from .observability import report_retrieval_observability
        report_retrieval_observability(
            result.retrieved_chunks, trace_id=tid,
        )

        return result

    def _get_reranker(self):
        """延迟创建 Cross-Encoder 精排器（首次 use_reranker=True 时实例化）

        【变易】避免 SkillLoader.__init__ 拉起 BGE-reranker；
                测试可通过 monkeypatch 替换；
                模型名/ONNX 开关由 SkillReranker 自行从 .env 读取（SKILL_RERANKER_*）
        【不易】不在此处硬编码模型路径或环境变量名，配置权下沉到 SkillReranker
        """
        if not hasattr(self, "_reranker_instance"):
            try:
                from .reranker import SkillReranker
                # 【简易】不传 model_name，让 SkillReranker 从 .env 读取所有配置
                # 避免配置重复（loader.py 和 reranker.py 各读一遍容易不一致）
                self._reranker_instance = SkillReranker()
                logger.info(json.dumps({
                    "module_name": "loader",
                    "action": "reranker.init",
                    "model": self._reranker_instance._model_name,
                    "use_onnx": self._reranker_instance._use_onnx_env,
                    "onnx_variant": self._reranker_instance._onnx_variant,
                    "min_score": self._reranker_instance._min_score,
                }, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "loader",
                    "action": "reranker_init_failed",
                    "error": str(e)[:300],
                }, ensure_ascii=False))
                self._reranker_instance = None
        return self._reranker_instance

    def list_all_metadata(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """列出所有技能的元数据（第一层，不加载 body）

        用于 UI 展示技能列表，只读 front matter。
        """
        index = self.fs.load_metadata_index(refresh=True)
        result = []
        for skill_id, meta in index.items():
            if enabled_only and not meta.get("enabled", True):
                continue
            meta["skill_id"] = skill_id
            meta["scripts"] = self.fs.list_scripts(skill_id)
            result.append(meta)
        return result

    # ──────────────────────────────────────────────
    #  第二层：按需加载使用说明
    # ──────────────────────────────────────────────

    def load_instruction(self, skill_id: str) -> Dict[str, Any]:
        """第二层 — 按需加载技能的完整使用说明

        只在第一层匹配到技能后才调用。
        Returns: {skill_id, instruction, estimated_tokens, layer}
        """
        t0 = time.time()
        tid = _trace_id()

        body = self.fs.load_instruction(skill_id)
        est_tokens = estimate_tokens(body)

        elapsed = (time.time() - t0) * 1000
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "load_instruction.layer2.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 2,
            "skill_id": skill_id,
            "instruction_chars": len(body),
            "estimated_tokens": est_tokens,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_instruction_tokens",
                    value=est_tokens, kind="histogram",
                    labels={"skill_id": skill_id, "layer": "2"})

        return {
            "skill_id": skill_id,
            "instruction": body,
            "estimated_tokens": est_tokens,
            "instruction_chars": len(body),
            "layer": 2,
        }

    # ──────────────────────────────────────────────
    #  第三层：按需获取脚本路径
    # ──────────────────────────────────────────────

    def list_scripts(self, skill_id: str) -> List[Dict[str, Any]]:
        """第三层 — 列出技能的所有脚本（不加载代码内容）

        Returns: [{name, path, size_bytes}]
        """
        t0 = time.time()
        tid = _trace_id()

        script_names = self.fs.list_scripts(skill_id)
        result = []
        for name in script_names:
            try:
                path = self.fs.get_script_path(skill_id, name)
                result.append({
                    "name": name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                })
            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "list_scripts.skip",
                    "skill_id": skill_id,
                    "script": name,
                    "error": str(e),
                }, ensure_ascii=False))

        elapsed = (time.time() - t0) * 1000
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "list_scripts.layer3.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 3,
            "skill_id": skill_id,
            "script_count": len(result),
        }, ensure_ascii=False))

        return result

    def list_temp_files(self, skill_id: str) -> List[Dict[str, Any]]:
        """第三层 — 列出技能的业务模板文件"""
        temp_names = self.fs.list_temp_files(skill_id)
        result = []
        for name in temp_names:
            try:
                path = self.fs.get_temp_path(skill_id, name)
                result.append({
                    "name": name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                })
            except Exception:
                pass
        return result

    # ──────────────────────────────────────────────
    #  三层统计
    # ──────────────────────────────────────────────

    def get_layer_summary(self) -> Dict[str, Any]:
        """获取三层架构统计信息"""
        index = self.fs.load_metadata_index()

        # 第一层统计
        layer1_tokens = sum(
            estimate_tokens(json.dumps(m, ensure_ascii=False))
            for m in index.values()
        )

        # 第三层统计
        total_scripts = 0
        total_temp_files = 0
        for skill_id in index:
            total_scripts += len(self.fs.list_scripts(skill_id))
            total_temp_files += len(self.fs.list_temp_files(skill_id))

        return {
            "layer1_metadata": {
                "skill_count": len(index),
                "estimated_tokens_per_skill": (
                    layer1_tokens / len(index) if index else 0
                ),
                "estimated_total_tokens": layer1_tokens,
                "description": "元数据层（front matter），约 100 token/技能",
            },
            "layer2_instruction": {
                "description": "使用说明层（skill.md body），按需加载",
                "on_demand": True,
            },
            "layer3_tools": {
                "total_scripts": total_scripts,
                "total_temp_files": total_temp_files,
                "description": "工具资源层（scripts/ + temp/），后台执行",
                "on_demand": True,
            },
            "total_skills": len(index),
        }

    # ──────────────────────────────────────────────
    #  全量加载（调试用，不推荐生产环境）
    # ──────────────────────────────────────────────

    def load_full(self, skill_id: str) -> Dict[str, Any]:
        """加载技能完整信息（三层全部加载，仅调试用）

        生产环境应分层加载以节省上下文。
        """
        meta, body, scripts, temp_files = self.fs.read(skill_id)
        return {
            "skill_id": skill_id,
            "metadata": meta,
            "instruction": body,
            "instruction_tokens": estimate_tokens(body),
            "scripts": scripts,
            "temp_files": temp_files,
            "layer": "all",
        }
