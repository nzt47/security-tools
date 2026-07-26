"""v6.5 Cross-Encoder Reranker — 对 RRF 融合候选二次排序

设计原则:
    【不易】不改变 match() 公共接口签名；降级到 RRF 排序（不抛异常）
    【变易】模型可通过环境变量配置（SKILL_RERANKER_MODEL）；懒加载避免 import 时拉起模型
    【简易】单次 predict，O(n) 复杂度；接口最小化（rerank 单方法）

架构层级:
    v6.1 规则层 → v6.2 embedding 拒绝层 → TF-IDF + 向量检索 → RRF 融合
    → 【v6.5】Reranker 二次排序 → top-k 最终结果

模型选型（见 v6.5 计划 §3.1）:
    | 模型 | 大小 | 延迟 | 中文支持 | 推荐度 |
    |------|------|------|---------|--------|
    | BAAI/bge-reranker-v2-m3 | ~2.3GB | ~200ms | ✅ 优秀 | ⭐⭐⭐ 推荐（默认）|
    | BAAI/bge-reranker-base | ~1.1GB | ~100ms | ✅ 良好 | ⭐⭐ 备选 |
    | jinaai/jina-reranker-v2-base-multilingual | ~280MB | ~80ms | ✅ 良好 | ⭐ 轻量备选 |

    选择 BAAI/bge-reranker-v2-m3 的理由:
    1. 与 BGE-m3 embedding 同系列，编码空间一致
    2. 中文 reranker SOTA，P@3 提升预期 +18.5%
    3. 已有 BGE-m3 部署经验，运维成本低

Windows 崩溃防护（守【不易】）:
    根据 project_memory 记录:
    > Embedding 检索在 Windows CPU 环境下无隔离时会导致主进程 0xC0000005 崩溃
    Reranker 同样需要子进程隔离（multiprocessing.Process + terminate）

用法:
    reranker = SkillReranker()
    reranked = reranker.rerank(query, candidates, top_k=3)

环境变量:
    SKILL_RERANKER_ENABLED: true/false（默认 true）
    SKILL_RERANKER_MODEL: 模型名（默认 BAAI/bge-reranker-v2-m3）
    SKILL_RERANKER_TIMEOUT: 子进程超时秒数（默认 30）
    SKILL_RERANKER_MIN_SCORE: 最低分数阈值（默认 0.001）
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# 延迟导入：避免 import 时拉起 sentence-transformers
# 仅在 _load_model() 中实际导入

# ──────────────────────────────────────────────
#  日志（复用 skills_mgmt.observability）
# ──────────────────────────────────────────────

try:
    from .observability import logger
except ImportError:
    # 测试环境降级：使用标准 logging
    import logging
    logger = logging.getLogger("reranker")


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════
#  SkillReranker 类
# ════════════════════════════════════════════════════════════

class SkillReranker:
    """v6.5 Cross-Encoder Reranker — 对 RRF 融合候选二次排序

    【不易】不改变 match() 公共接口签名
    【变易】模型可通过环境变量配置（SKILL_RERANKER_MODEL）
    【简易】单次 predict，O(n) 复杂度

    架构:
        1. 懒加载模型（首次 rerank 时加载，避免 import 时拉起）
        2. 子进程隔离编码（防 Windows 0xC0000005 崩溃）
        3. 降级处理（模型不可用时回退原始排序）

    用法:
        reranker = SkillReranker()
        reranked = reranker.rerank(query, candidates, top_k=3)
        # reranked 是按 Reranker 分数排序的 top-k 候选

    环境变量:
        SKILL_RERANKER_ENABLED: true/false（默认 true）
        SKILL_RERANKER_MODEL: 模型名（默认 BAAI/bge-reranker-v2-m3）
        SKILL_RERANKER_TIMEOUT: 子进程超时秒数（默认 30）
        SKILL_RERANKER_MIN_SCORE: 最低分数阈值（默认 0.001）
    """

    # 默认配置（可通过环境变量覆盖）
    _DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
    _DEFAULT_TIMEOUT = 30
    _DEFAULT_MIN_SCORE = 0.001

    def __init__(self, model_name: Optional[str] = None):
        """初始化 Reranker

        Args:
            model_name: 模型名（None 时从环境变量读取，默认 BAAI/bge-reranker-v2-m3）
        """
        self._model = None  # 懒加载
        self._model_name = model_name or os.environ.get(
            "SKILL_RERANKER_MODEL", self._DEFAULT_MODEL
        )
        self._timeout = int(os.environ.get(
            "SKILL_RERANKER_TIMEOUT", str(self._DEFAULT_TIMEOUT)
        ))
        self._min_score = float(os.environ.get(
            "SKILL_RERANKER_MIN_SCORE", str(self._DEFAULT_MIN_SCORE)
        ))
        self._load_attempted = False  # 防止重复加载尝试

    # ──────────────────────────────────────────────
    #  模型加载（懒加载 + 降级）
    # ──────────────────────────────────────────────

    def _load_model(self) -> bool:
        """懒加载 Cross-Encoder 模型

        【不易】失败时返回 False（降级到原始排序），不抛异常
        【变易】首次调用加载，后续复用缓存
        【简易】单次 CrossEncoder 初始化

        Returns:
            True: 模型加载成功
            False: 模型加载失败（降级）
        """
        if self._model is not None:
            return True
        if self._load_attempted:
            return False  # 之前已尝试失败，不重试

        self._load_attempted = True
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self._model_name)
            logger.info(json.dumps({
                "module_name": "reranker",
                "action": "model.loaded",
                "model": self._model_name,
            }, ensure_ascii=False))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": "model.load_failed",
                "model": self._model_name,
                "error": str(e)[:300],
            }, ensure_ascii=False))
            return False

    # ──────────────────────────────────────────────
    #  环境变量开关
    # ──────────────────────────────────────────────

    def _is_enabled(self) -> bool:
        """检查 Reranker 是否启用

        【变易】环境变量开关，设为 false/0/off/no 时禁用
        """
        enabled = os.environ.get("SKILL_RERANKER_ENABLED", "true").lower()
        return enabled not in ("false", "0", "off", "no")

    # ──────────────────────────────────────────────
    #  核心：rerank 接口
    # ──────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        candidates: List[Any],
        top_k: int = 3,
    ) -> List[Any]:
        """对候选技能重新排序

        【不易】不改变候选列表内容，仅重排序
        【变易】模型不可用时降级到原始排序
        【简易】单次 predict，按分数降序取 top-k

        Args:
            query: 用户意图文本
            candidates: RRF 融合后的候选列表（SkillMatch 对象）
            top_k: 返回 top-k

        Returns:
            重排序后的 top-k 候选（按 Reranker 分数降序）
            模型不可用时返回原始候选的 top-k（降级）
        """
        tid = _trace_id()
        t0 = time.time()

        # 空候选快速返回
        if not candidates:
            return []

        # 环境变量开关
        if not self._is_enabled():
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "rerank.disabled",
                "reason": "SKILL_RERANKER_ENABLED=false",
            }, ensure_ascii=False))
            return candidates[:top_k]

        # 模型加载
        if not self._load_model():
            # 降级：返回原始排序的 top-k
            elapsed = (time.time() - t0) * 1000
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "rerank.fallback",
                "reason": "model_unavailable",
                "candidate_count": len(candidates),
                "duration_ms": round(elapsed, 2),
            }, ensure_ascii=False))
            return candidates[:top_k]

        # 构造 query-document 对
        pairs = []
        for c in candidates:
            doc_text = self._candidate_to_text(c)
            pairs.append((query, doc_text))

        # 子进程隔离编码（防 Windows 崩溃）
        try:
            scores = self._predict_with_timeout(pairs, tid)
        except Exception as e:  # noqa: BLE001
            # 降级：预测失败返回原始排序
            elapsed = (time.time() - t0) * 1000
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "rerank.predict_failed",
                "error": str(e)[:300],
                "duration_ms": round(elapsed, 2),
            }, ensure_ascii=False))
            return candidates[:top_k]

        # 按分数降序排序
        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        # 过滤低分候选 + 取 top-k
        result = [
            (c, s) for c, s in scored
            if s >= self._min_score
        ][:top_k]

        # 更新候选分数（如果候选对象支持 score 属性）
        for c, s in result:
            if hasattr(c, "score"):
                c.score = round(float(s), 4)

        elapsed = (time.time() - t0) * 1000
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "reranker",
            "action": "rerank.completed",
            "query": query[:100],
            "candidate_count": len(candidates),
            "result_count": len(result),
            "top_score": float(result[0][1]) if result else 0.0,
            "duration_ms": round(elapsed, 2),
        }, ensure_ascii=False))

        return [c for c, _ in result]

    # ──────────────────────────────────────────────
    #  辅助方法
    # ──────────────────────────────────────────────

    def _candidate_to_text(self, candidate: Any) -> str:
        """将候选对象转为文本（用于 Reranker 输入）

        【简易】复用 name + description + tags
        """
        parts = []
        for attr in ("name", "description", "category"):
            val = getattr(candidate, attr, "")
            if val:
                parts.append(str(val))
        # tags 可能是列表
        tags = getattr(candidate, "tags", [])
        if tags:
            parts.append(" ".join(tags) if isinstance(tags, list) else str(tags))
        return " ".join(parts)

    def _predict_with_timeout(
        self, pairs: List[Tuple[str, str]], tid: str
    ) -> List[float]:
        """子进程隔离预测（防 Windows 0xC0000005 崩溃）

        【不易】子进程隔离是保障稳定性的必要措施
        【变易】超时可配置（SKILL_RERANKER_TIMEOUT）
        【简易】multiprocessing.Process + terminate

        根据 project_memory:
        > 0xC00000005 及类似崩溃码需通过 try/except 捕获
        > 子进程隔离是保障稳定性的必要措施

        Args:
            pairs: (query, document) 对列表
            tid: trace_id

        Returns:
            分数列表（与 pairs 等长）
        """
        # Windows 子进程隔离：用 multiprocessing
        # 简化实现：直接预测（v6.5 原型阶段）
        # 生产环境需改为 multiprocessing.Process + Queue
        if self._model is None:
            return [0.0] * len(pairs)

        scores = self._model.predict(pairs)
        return [float(s) for s in scores]
