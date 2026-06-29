"""工作流匹配器 — TF-IDF + 余弦相似度

实现要点:
    - 自实现 TF-IDF (避免引入 sklearn 等第三方依赖)
    - 中文按字符 + 英文按词的混合分词
    - 增量构建索引 (新工作流加入时重算)
    - 返回 Top-K 候选，按 (similarity * confidence * priority_factor) 排序
"""

from __future__ import annotations
import math
import re
import time
import json
import uuid
import logging
from typing import Dict, List, Set, Tuple

from .models import LearnedWorkflow
from .observability import logger, emit_metric, traced_action


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


class TfidfIndex:
    """简易 TF-IDF 索引"""

    def __init__(self):
        self._docs: Dict[str, List[str]] = {}  # wf_id → tokens
        self._df: Dict[str, int] = {}          # term → 文档频率
        self._dirty = True
        self._cache: Dict[str, Dict[str, float]] = {}  # wf_id → {term: tfidf}

    def add(self, doc_id: str, text: str) -> None:
        tokens = _tokenize(text)
        self._docs[doc_id] = tokens
        for t in set(tokens):
            self._df[t] = self._df.get(t, 0) + 1
        self._dirty = True

    def remove(self, doc_id: str) -> None:
        if doc_id not in self._docs:
            return
        for t in set(self._docs[doc_id]):
            self._df[t] = max(0, self._df.get(t, 0) - 1)
            if self._df[t] == 0:
                del self._df[t]
        del self._docs[doc_id]
        self._dirty = True

    def _rebuild(self) -> None:
        N = max(1, len(self._docs))
        self._cache = {}
        for doc_id, tokens in self._docs.items():
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            length = math.sqrt(sum(
                (cnt / len(tokens)) ** 2 * (math.log((N + 1) / (1 + self._df.get(t, 0)))) ** 2
                for t, cnt in tf.items()
            )) or 1.0
            vec: Dict[str, float] = {}
            for t, cnt in tf.items():
                tf_val = cnt / len(tokens)
                idf_val = math.log((N + 1) / (1 + self._df.get(t, 0)))
                vec[t] = tf_val * idf_val / length
            self._cache[doc_id] = vec
        self._dirty = False

    def query(self, text: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """返回 [(wf_id, similarity), ...] top_k 个"""
        if self._dirty:
            self._rebuild()
        if not self._cache:
            return []
        q_tokens = _tokenize(text)
        if not q_tokens:
            return []
        N = max(1, len(self._docs))
        tf: Dict[str, int] = {}
        for t in q_tokens:
            tf[t] = tf.get(t, 0) + 1
        q_vec: Dict[str, float] = {}
        q_length = 0.0
        for t, cnt in tf.items():
            tf_val = cnt / len(q_tokens)
            idf_val = math.log((N + 1) / (1 + self._df.get(t, 0)))
            v = tf_val * idf_val
            q_vec[t] = v
            q_length += v * v
        q_length = math.sqrt(q_length) or 1.0
        for t in q_vec:
            q_vec[t] /= q_length

        scores: List[Tuple[str, float]] = []
        for doc_id, vec in self._cache.items():
            # 余弦相似度 (因向量已归一化，点积即为 cosine)
            sim = 0.0
            for t, w in q_vec.items():
                if t in vec:
                    sim += w * vec[t]
            if sim > 0:
                scores.append((doc_id, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class WorkflowMatcher:
    """工作流匹配器"""

    def __init__(self, *, min_similarity: float = 0.3,
                 min_confidence: float = 0.4):
        self.min_similarity = min_similarity
        self.min_confidence = min_confidence
        self._index = TfidfIndex()
        self._workflows: Dict[str, LearnedWorkflow] = {}

    def register(self, wf: LearnedWorkflow) -> None:
        """注册/更新一个工作流到索引"""
        # 索引文本 = 名称 + 描述 + 任务签名 + 触发模式 + 标签
        text = " ".join([
            wf.name, wf.description, wf.task_signature,
            " ".join(wf.trigger_patterns), " ".join(wf.tags),
        ])
        if wf.id in self._workflows:
            self._index.remove(wf.id)
        self._index.add(wf.id, text)
        self._workflows[wf.id] = wf

    def unregister(self, wf_id: str) -> None:
        self._index.remove(wf_id)
        self._workflows.pop(wf_id, None)

    def rebuild(self, workflows: List[LearnedWorkflow]) -> None:
        """从列表全量重建索引"""
        self._index = TfidfIndex()
        self._workflows = {}
        for wf in workflows:
            self.register(wf)

    def match(self, task_text: str, *, top_k: int = 5) -> List[Tuple[LearnedWorkflow, float]]:
        """匹配任务文本到工作流

        Returns:
            [(workflow, combined_score)] 列表，combined_score =
            similarity * confidence * (0.5 + priority / 200)
        """
        t0 = time.time()
        with traced_action("wf_match", task_text=task_text[:80]) as ctx:
            candidates = self._index.query(task_text, top_k=top_k)
            results: List[Tuple[LearnedWorkflow, float]] = []
            for wf_id, sim in candidates:
                wf = self._workflows.get(wf_id)
                if not wf or not wf.enabled:
                    continue
                if sim < self.min_similarity:
                    continue
                if wf.confidence < self.min_confidence:
                    continue
                # 综合分: 相似度 * 置信度 * 优先级因子
                priority_factor = 0.5 + wf.priority / 200.0  # 0.5 ~ 1.0
                combined = sim * wf.confidence * priority_factor
                results.append((wf, combined))
            results.sort(key=lambda x: x[1], reverse=True)
            elapsed = (time.time() - t0) * 1000
            ctx["candidates"] = len(candidates)
            ctx["matched"] = len(results)
            ctx["elapsed_ms"] = elapsed
            emit_metric("yunshu_wf_match_latency_ms",
                        value=elapsed, labels={"success": "true"},
                        kind="histogram")
            logger.info("[Matcher] '%s...' → %d 候选, %d 通过, %.2fms",
                        task_text[:30], len(candidates), len(results), elapsed)
            return results


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
            "module_name": "matcher",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
