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
import threading
from typing import Dict, List, Set, Tuple

from .models import LearnedWorkflow
from . import admission
from .observability import logger, emit_metric, traced_action
from agent.logging_utils import log_dict


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _idf(n_docs: int, df: int) -> float:
    """平滑 IDF：原公式在单文档退化（N==df==1）时恒为 0，
    导致所有权重归零、余弦相似度恒为 0、索引永远无法匹配首个工作流
    （自动闭环首环失效）。加下界 0.001 保证恒正；多文档场景原值>0 不受影响。

    归一化后 idf 下界会相互抵消，相似度退化为纯 TF 余弦（词重叠度），
    完全相同的文本相似度≈1.0，部分重叠>0。
    """
    return max(math.log((n_docs + 1) / (1 + df)), 0.001)


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
                (cnt / len(tokens)) ** 2 * (_idf(N, self._df.get(t, 0))) ** 2
                for t, cnt in tf.items()
            )) or 1.0
            vec: Dict[str, float] = {}
            for t, cnt in tf.items():
                tf_val = cnt / len(tokens)
                vec[t] = tf_val * _idf(N, self._df.get(t, 0)) / length
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
            v = tf_val * _idf(N, self._df.get(t, 0))
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
        # 准入否决计数（TASK-S10-01）：{拒绝码: 条数}，供 health()/审计读数
        self._admission_rejected: Dict[str, int] = {}
        # Why RLock 保护索引与工作流表：register/unregister 的 add/remove（写
        # _docs/_df）与 match 触发的 _rebuild（遍历 _docs）并发会抛 RuntimeError
        # （dictionary changed size during iteration），add 的 df 读-改-写也会丢
        # 计数。锁内仅内存 dict 变更与 _tokenize（纯字符串处理）；match 的
        # 观测/日志/metrics 在锁外（持锁纪律）。
        self._lock = threading.RLock()

    # ─── 准入（TASK-S10-01）───

    @staticmethod
    def _record_rejection(decision) -> None:
        """记录准入否决（结构化日志 + 指标；不进候选、不写索引）"""
        for code in decision.codes:
            emit_metric("yunshu_wf_admission_rejected_total",
                        labels={"code": code}, kind="counter")

    def register(self, wf: LearnedWorkflow) -> bool:
        """注册/更新一个工作流到索引

        【TASK-S10-01】准入否决的条目**不得进入匹配候选**：单字触发词
        （无区分度）或步骤数 < `admission.MIN_STEPS` 的条目直接从索引与工作流表
        中剔除（若此前注册过则一并移除），只留在仓库里（草稿/归档态）。
        返回 True 表示已进入候选池。
        """
        decision = admission.check_match_eligibility(wf)
        with self._lock:
            if not decision.admitted:
                # 曾有版本已进索引（如状态/触发词被改坏）→ 同步剔除，避免残留候选
                self._index.remove(wf.id)
                self._workflows.pop(wf.id, None)
                for code in decision.codes:
                    self._admission_rejected[code] = (
                        self._admission_rejected.get(code, 0) + 1)
                rejected = True
            else:
                # 索引文本 = 名称 + 描述 + 任务签名 + 触发模式 + 标签 +
                #           步骤工具名 + 来源用户输入
                # 【变易】冷启动匹配质量修复：原索引不含 steps 工具名与
                #   source_user_input——学到的工作流连"原样复述请求"都匹配不到
                #   （单字 TF-IDF 下签名/触发词过短）。补上工具名与原始请求
                #   后，复现场景相似度 0.3→0.8+，可过 orchestrator min_score。
                # 【TASK-S10-01】触发词只取**有区分度**的那些：单字触发词
                #   （如 ["列","出","当","前","工"]）不携带区分信息，作为特征
                #   等价于"任意中文输入都可能命中"，故不得进入索引文本。
                step_tools = " ".join(
                    s.tool_name for s in (wf.steps or []) if s.tool_name)
                text = " ".join([
                    wf.name, wf.description, wf.task_signature,
                    " ".join(admission.effective_trigger_patterns(
                        wf.trigger_patterns)),
                    " ".join(wf.tags),
                    step_tools,
                    wf.source_user_input or "",
                ])
                if wf.id in self._workflows:
                    self._index.remove(wf.id)
                self._index.add(wf.id, text)
                self._workflows[wf.id] = wf
                rejected = False
        if rejected:
            # 锁外记日志/指标（持锁纪律）
            self._record_rejection(decision)
            logger.info(log_dict({
                'module_name': 'matcher', 'action': 'wf_admission.rejected',
                'workflow_id': wf.id, 'codes': list(decision.codes),
                'reasons': decision.reason_text,
                'level': 'INFO'}))
        return not rejected

    def admission_rejected_counts(self) -> Dict[str, int]:
        """被准入否决的条数（按拒绝码）——健康检查/审计读数"""
        with self._lock:
            return dict(self._admission_rejected)

    def unregister(self, wf_id: str) -> None:
        with self._lock:
            self._index.remove(wf_id)
            self._workflows.pop(wf_id, None)

    def rebuild(self, workflows: List[LearnedWorkflow]) -> None:
        """从列表全量重建索引"""
        with self._lock:
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
            # query 可能触发 _rebuild（遍历 _docs），须与 register/unregister 的
            # 写互斥（锁内仅内存计算，无 I/O）
            with self._lock:
                candidates = self._index.query(task_text, top_k=top_k)
            # 锁外过滤：workflows 弱一致读取（并发 unregister 时 get 返回 None 即
            # 跳过，安全），日志/metrics 不占锁（持锁纪律）
            results: List[Tuple[LearnedWorkflow, float]] = []
            for wf_id, sim in candidates:
                wf = self._workflows.get(wf_id)
                if not wf or not wf.enabled:
                    continue
                # 【TASK-S10-01】准入复核：单字触发词 / 步骤数下限 / 非 ACTIVE
                # 一律不得进入候选（防"注册后触发词或状态被改坏"的残留候选；
                # 结构性判定为 O(触发词数+步骤数)，无 I/O）
                if not admission.check_match_eligibility(wf).admitted:
                    continue
                if sim < self.min_similarity:
                    continue
                if wf.confidence < self.min_confidence:
                    continue
                # 综合分: 相似度 * 置信度 * 优先级因子
                priority_factor = 0.5 + wf.priority / 200.0  # 0.5 ~ 1.0
                # 【变易】冷启动执行通道（死锁修复）：从未执行过的 workflow
                #   （success_count=0）按"文本相似度 × 优先级"计分（置信度因子
                #   视为 1）——冷启动 conf=0.4 会把 combined 压到难达 executor
                #   min_score 的程度（sim 0.8×0.4×0.75≈0.24 < 0.25），导致刚学
                #   的工作流永远无法首跑、无法积累执行记录。执行 1 次后
                #   success_count>0，恢复 conf 参与计分（按真实成功率演化）。
                conf_factor = (wf.confidence if wf.success_count > 0
                               else 1.0)
                combined = sim * conf_factor * priority_factor
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
        logger.error(log_dict({'module_name': 'matcher', 'action': action + '.failed', 'error': f'{type(e).__name__}: {e}'}))
        raise
