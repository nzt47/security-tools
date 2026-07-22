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

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    """混合分词：英文按词，中文按字"""
    return _WORD_RE.findall((text or "").lower())


def _match_score(meta_text: str, query_tokens: List[str]) -> float:
    """计算查询与元数据文本的匹配分"""
    if not query_tokens:
        return 0.0
    meta_tokens = _tokenize(meta_text)
    if not meta_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in meta_tokens)
    return hits / len(query_tokens)  # 命中率


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
                 score_breakdown: Optional[Dict[str, Any]] = None):
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

    def __init__(self, file_store: Optional[SkillFileStore] = None,
                 vector_adapter: Optional[Any] = None):
        self.fs = file_store or SkillFileStore()
        # 向量检索适配器（延迟创建，避免初始化时拉起 chromadb/torch）
        # 传入 None 时按需创建；显式传入便于测试 mock
        self._vector_adapter = vector_adapter

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
            use_bm25: 预留扩展：未来启用 BM25（当前忽略，记录 warning）
            use_reranker: 启用 Cross-Encoder 精排（BGE-reranker-v2-m3）
                         仅在 use_vector=True 且 fusion_mode="rrf" 时生效
                         失败时降级为无 rerank
            retrieval_weights: 预留扩展：未来多路融合（RRF）权重（当前忽略）
            fusion_mode: 融合模式，可选:
                - "none"（默认）：单路检索（use_vector 决定走 TF-IDF 或向量）
                - "rrf": Reciprocal Rank Fusion，同时调用 TF-IDF + 向量检索
                  用 score(d)=Σ 1/(k+rank_i(d)) 融合排序（k=60 业界标准）
                  仅在 use_vector=True 时生效；向量路失败时降级 TF-IDF 单路
                - "rrf_rerank": RRF + Cross-Encoder 精排
                  RRF 召回 top-N（N=2*top_k）→ Cross-Encoder 精排 → 取 top_k
                  仅在 use_vector=True 且 use_reranker=True 时生效

        Returns: MatchResult

        【不易】fusion_mode="none" 时行为完全等同旧版（向后兼容）
        【变易】新增 "rrf" / "rrf_rerank" 模式实现多路融合 + 精排
        【简易】融合/精排算法为独立私有方法，便于单元测试与可观测性
        """
        t0 = time.time()
        tid = _trace_id()

        # 扩展点防御：use_vector=True 时走向量检索分支，失败降级 TF-IDF
        # use_bm25 / use_reranker 仍保留 warning（未实现）
        fallback_used = False

        # ── RRF + Rerank 融合模式 ──
        # 【变易】使用 use_reranker=True 触发 rrf_rerank 模式
        # 自动将 fusion_mode 升级为 rrf_rerank
        if use_reranker and use_vector and fusion_mode == "rrf":
            fusion_mode = "rrf_rerank"

        # ── RRF 融合模式：TF-IDF + 向量双路融合 ──
        # 【变易】仅在 use_vector=True 且 fusion_mode in ("rrf", "rrf_rerank") 时启用
        # 失败降级到下方 TF-IDF 单路（守【不易】兼容性）
        if use_vector and fusion_mode in ("rrf", "rrf_rerank"):
            rrf_result = self._try_rrf_match(
                intent=intent,
                top_k=top_k,
                enabled_only=enabled_only,
                min_score=min_score,
                tid=tid,
                t0=t0,
                use_reranker=(fusion_mode == "rrf_rerank"),
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

        # use_bm25 / use_reranker 仍未实现，记录 warning
        if use_bm25 or use_reranker:
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "match.extension_not_implemented",
                "intent": intent[:100],
                "use_bm25": use_bm25,
                "use_reranker": use_reranker,
                "retrieval_weights": retrieval_weights,
                "fallback": "tfidf",
            }, ensure_ascii=False))
            fallback_used = True

        # 加载元数据索引（第一层，只读 front matter）
        index = self.fs.load_metadata_index()
        query_tokens = _tokenize(intent)

        candidates: List[SkillMatch] = []
        for skill_id, meta in index.items():
            # 过滤禁用技能
            if enabled_only and not meta.get("enabled", True):
                continue

            # 计算匹配分
            meta_text = _meta_to_meta_text(meta)
            score = _match_score(meta_text, query_tokens)

            if score < min_score:
                continue

            # 估算元数据 token 数（第一层成本）
            meta_str = json.dumps(meta, ensure_ascii=False)
            est_tokens = estimate_tokens(meta_str)

            candidates.append(SkillMatch(
                skill_id=skill_id,
                name=meta.get("name", skill_id),
                description=meta.get("description", ""),
                score=score,
                estimated_tokens=est_tokens,
                category=meta.get("category", ""),
                tags=meta.get("tags", []),
                version=meta.get("version", ""),
                enabled=meta.get("enabled", True),
            ))

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
        """
        if self._vector_adapter is None:
            try:
                from .vector_adapter import SkillVectorAdapter
                self._vector_adapter = SkillVectorAdapter(file_store=self.fs)
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
            ))

        if not matches:
            return None

        total_tokens = sum(m.estimated_tokens for m in matches)
        return MatchResult(
            matches=matches,
            total_scanned=len(index),
            elapsed_ms=0.0,  # 外层会重新计算并覆盖
            estimated_total_tokens=total_tokens,
            retrieval_method="vector",
            fallback_used=False,
        )

    # ──────────────────────────────────────────────
    #  RRF 融合检索（use_vector=True 且 fusion_mode="rrf" 时调用）
    # ──────────────────────────────────────────────

    # RRF 公式中 k 值的业界标准（Cormack et al. 2009）：60
    # k 越大，对低位排名的容错越强；k 越小，越偏向头部排名
    _RRF_K = 60

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

        # TF-IDF 路贡献
        for rank, m in enumerate(tfidf_matches, start=1):
            contrib = 1.0 / (k + rank)
            if m.skill_id not in fused:
                fused[m.skill_id] = {
                    "match": m,
                    "rrf_score": 0.0,
                    "tfidf_rank": rank,
                    "vector_rank": None,
                }
            fused[m.skill_id]["rrf_score"] += contrib
            fused[m.skill_id]["tfidf_rank"] = rank

        # 向量路贡献
        for rank, m in enumerate(vector_matches, start=1):
            contrib = 1.0 / (k + rank)
            if m.skill_id not in fused:
                fused[m.skill_id] = {
                    "match": m,
                    "rrf_score": 0.0,
                    "tfidf_rank": None,
                    "vector_rank": rank,
                }
            fused[m.skill_id]["rrf_score"] += contrib
            fused[m.skill_id]["vector_rank"] = rank

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
                    "rrf_score": round(info["rrf_score"], 6),
                    "rrf_normalized": round(normalized_score, 4),
                },
            ))

        # 按 RRF 归一化分数降序
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
    ) -> Optional[MatchResult]:
        """RRF 融合检索：TF-IDF + 向量双路并行 + 排名融合

        策略:
            1. TF-IDF 单路检索（取前 2*top_k，扩大候选池）
            2. 向量路检索（取前 2*top_k，扩大候选池）
            3. RRF 融合两路排名
            4. 按 RRF 分数降序取前候选池
            5. min_score 过滤 RRF 归一化分数
            6. （可选）use_reranker=True 时 Cross-Encoder 精排
            7. 取 top_k

        失败降级:
            - 向量路不可用 → 返回 None，外层降级 TF-IDF 单路
            - 两路均空 → 返回 None
            - TF-IDF 路异常 → 仅用向量路（保持单路降级语义）
            - Reranker 不可用 → 跳过精排，保留 RRF 顺序

        【不易】不修改 TF-IDF 与向量路各自的打分逻辑
        【变易】RRF k 值可调，扩大候选池倍率可配；可选 Cross-Encoder 精排
        【简易】融合逻辑集中在 _rrf_fuse，本方法仅编排

        Args:
            intent: 用户意图
            top_k: 最终返回数量
            enabled_only: 是否只匹配启用技能
            min_score: RRF 归一化分数阈值
            tid: trace_id
            t0: 起始时间戳
            use_reranker: True 则在 RRF 召回后调用 Cross-Encoder 精排

        Returns:
            MatchResult（retrieval_method="rrf"）或 None（降级）
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
        tfidf_matches: List[SkillMatch] = []
        try:
            for skill_id, meta in index.items():
                if enabled_only and not meta.get("enabled", True):
                    continue
                meta_text = _meta_to_meta_text(meta)
                score = _match_score(meta_text, query_tokens)
                # 应用 min_score 过滤，仅让有意义的结果参与融合
                if score < min_score:
                    continue
                meta_str = json.dumps(meta, ensure_ascii=False)
                est_tokens = estimate_tokens(meta_str)
                tfidf_matches.append(SkillMatch(
                    skill_id=skill_id,
                    name=meta.get("name", skill_id),
                    description=meta.get("description", ""),
                    score=score,
                    estimated_tokens=est_tokens,
                    category=meta.get("category", ""),
                    tags=meta.get("tags", []),
                    version=meta.get("version", ""),
                    enabled=meta.get("enabled", True),
                ))
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
            # 向量适配器不可用，RRF 无意义，返回 None 触发外层降级
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "rrf.vector_adapter_unavailable",
                "intent": intent[:100],
            }, ensure_ascii=False))
            return None

        # 两路均空，无法融合 → 返回 None 触发外层 TF-IDF 兜底
        # 【不易】负样本 query 场景：两路都过滤为空 → 返回空列表（而非随机召回）
        if not tfidf_matches and not vector_matches:
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
        if not tfidf_matches and vector_matches:
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
        fused = self._rrf_fuse(tfidf_matches, vector_matches, k=self._RRF_K)

        # 融合后不再二次过滤 min_score：各路已应用阈值，避免归一化分数压缩导致阈值失效

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
            "fused_count": len(fused),
            "match_count": len(top),
            "estimated_tokens": total_tokens,
            "retrieval_method": retrieval_method,
            "fallback_used": False,
            "retrieved_chunks_count": len(top),
            "rrf_k": self._RRF_K,
            "use_reranker": use_reranker,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_match_latency_ms",
                    value=elapsed, kind="histogram",
                    labels={"layer": "1", "method": retrieval_method, "success": "true"})
        emit_metric("yunshu_skill_match_count",
                    value=len(top), kind="gauge",
                    labels={"layer": "1", "method": retrieval_method})

        result = MatchResult(
            matches=top,
            total_scanned=len(index),
            elapsed_ms=elapsed,
            estimated_total_tokens=total_tokens,
            retrieval_method=retrieval_method,
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
                模型名可通过环境变量 SKILL_RERANK_MODEL 切换（如 bge-reranker-base）
        """
        if not hasattr(self, "_reranker_instance"):
            try:
                from .reranker import SkillReranker
                # 【变易】支持环境变量切换 reranker 模型（如 bge-reranker-base）
                # 默认 BAAI/bge-reranker-v2-m3（多语言，判别力最强）
                import os as _os
                model_name = _os.environ.get(
                    "SKILL_RERANK_MODEL",
                    "BAAI/bge-reranker-v2-m3",
                )
                self._reranker_instance = SkillReranker(model_name=model_name)
                logger.info(json.dumps({
                    "module_name": "loader",
                    "action": "reranker.init",
                    "model": model_name,
                    "rerank_min_score": self._reranker_instance.rerank_min_score,
                }, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "loader",
                    "action": "reranker_init_failed",
                    "error": str(e),
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
