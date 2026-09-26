"""工作流匹配器 — TF-IDF + 余弦相似度

实现要点:
    - 自实现 TF-IDF (避免引入 sklearn 等第三方依赖)
    - 分词与 learner 同源(F11-C)：中文 2 字滑窗 + 英文整词，索引侧/查询侧一致
    - 增量构建索引 (新工作流加入时重算)
    - 返回 Top-K 候选，按 (similarity * confidence * priority_factor) 排序
    - 【F11-C-2】门槛与排序**两分**（详见 `score_candidate` / `gate_on_evidence`）：
        * **门槛看证据**：`evidence = sim × confidence`，与 executor 的 `min_score` 比较；
        * **排序看偏好**：`combined = sim × confidence × (0.5 + priority/200)`，
          `priority` 是**偏好/排序权重**（默认 50，0~100），**不是证据** ——
          它只在候选之间排序，不再把候选的整体分数压低 25%~50%。
      默认按新口径（逃生开关 `WORKFLOW_LEARNING_GATE_ON_EVIDENCE=0` 回旧乘性门槛）。
    - 语义（F11-C-2 ④）：**自动执行只在「高相似 + 有一定信心」的重复场景成立；
      改写场景默认交给 LLM**（见 `WorkflowExecutor.try_execute` docstring）。
"""

from __future__ import annotations
import math
import os
import re
import time
import json
import uuid
import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from .models import LearnedWorkflow
from . import admission
# F11-C：分词器只有一处实现(learner)，matcher 复用它 —— 不写第二套分词。
# learner 不 import matcher，无循环依赖。
from .learner import term_stream
from .observability import logger, emit_metric, traced_action
from agent.logging_utils import log_dict


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


def _tokenize(text: str) -> List[str]:
    """索引侧与查询侧**共用**的分词口径（F11-C）

    复用 `learner.term_stream` —— 与任务签名 / 触发词是**同一个**分词器，
    不写第二套（F11-B 已把"怎么切词"收敛到 learner）：

        - 英文/标识符：整词，小写化，长度 >= 2；
        - 中文：按停用字切段后取 **2 字滑窗**(bigram)；单字一律丢弃。

    改前是 `re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")` —— 中文**按单字**切。
    那与 `learner.signature_tokens`/`trigger_tokens` 产出的 bigram 触发词
    **口径不一致**：`register` 明明把 bigram 触发词拼进了索引文本，
    但 `_tokenize` 又把它们**切回单字**，查询侧同样只产单字 ⇒
    doc 里的 bigram 特征**永远不可能被查询命中**(死特征)；
    且单字索引下"任意两段中文都有字重叠"，只能靠 idf 压制误召，
    而那正是召回塌陷的来源（见 `_idf`）。

    一致口径后：查询与文档都是 bigram，虚词(停用字)自然不产生特征，
    无关句的 bigram 重叠≈0 ⇒ 误召压力**结构性**消失（F11-C 实测
    far_max 由 0.1765 降到 0.0863，且与语料规模无关）。
    """
    return term_stream(text)


def _idf(n_docs: int, df: int) -> float:
    """平滑 IDF —— **加 1 平滑**，不再用"下界 0.001"

    历史与缺陷（F11-C 实测）：原公式在单文档退化（N==df==1）时恒为 0，
    导致所有权重归零、余弦恒为 0、首个工作流永远匹配不上（自动闭环首环失效）。
    旧修法是取 `max(..., 0.001)`，注释称"下界会相互抵消，相似度退化为纯 TF 余弦"。

    **该说法不成立**：下界只抵消了**文档侧**。查询里**索引文本中不存在**的
    词 df=0，权重仍是 `log(N+1)`（单文档时 **0.693**），比已见词的 0.001
    大 **693 倍** —— 查询向量被"未见词"主导，只要改写引入一个新词（"请帮我…"、
    换同义词），相似度就塌到 0.002；而索引里多一条工作流后同一句话又能到
    0.55。即**同一个用户查询的召回结果取决于索引里有几条工作流**，
    这是行为不稳定性，不只是召回率高低（F11-C §2.1）。

    现改用标准加 1 平滑 `log((1+N)/(1+df)) + 1`：
      - 任何词的权重恒 >= **1**（不退化，单文档也能匹配）；
      - 单文档时已见词 = 1、未见词 = 1+ln2 ≈ **1.693** ⇒ 最坏比值 **1.69 倍**
        （而非 693 倍），"引入一个新词就归零"的悬崖消失；
      - 多文档时 `+1` 相对项变小，趋近经典 idf，区分度不受影响。

    "为什么是 +1"：这是标准的**加性平滑**(additive smoothing) ——
    它把 idf 的下界从 0 平移到 1，使"没见过的词"相对"到处都有的词"最多只贵
    1.69 倍，而不是 693 倍；不引入任何本仓库特有的魔数。
    """
    return math.log((1 + n_docs) / (1 + df)) + 1.0


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


# ═══════════════════════════════════════════════════════════════
#  【F11-C-2】门槛（证据）与排序（偏好）两分
# ───────────────────────────────────────────────────────────────
#  改前的**单一**分数同时承担两件事：
#      combined = sim × confidence × (0.5 + priority/200)      # matcher.py 旧 :303
#  它既是候选之间的**排序键**，又是 executor 的**门槛判据**。于是
#  `priority`（默认 50，范围 0~100）在乘性门槛里把候选分数整体压低 25%~50%
#  （priority=50 ⇒ ×0.75、priority=0 ⇒ ×0.5），**等于把"偏好"当成了"证据"**。
#
#  死锁（F11-C-2 §1 实测，生产入口原始输出）：
#      · 门槛所需最小 sim = min_score / (conf_factor × priority_factor)；
#      · 而 confidence **只能靠"执行成功"增长**（models.record_execution），
#        执行又必须先过门槛 ⇒ 「低信心 ⇒ 需要更相似 ⇒ 执行不了 ⇒ 信心不涨」
#        的**闭环**。实测 workflow 条目（prio=80, conf 0.4→0.5）：
#        改写句 sim=0.3492 首轮执行成功（combined 0.3143 ≥ 0.25），
#        此后 confidence=0.5 ⇒ combined 0.1572 < 0.25 ⇒ **永远卡住**。
#
#  修复：两个分数**各自显式算出**，不再互相耦合：
#      evidence = sim × confidence                     # ← 门槛（与 min_score 比较）
#      combined = sim × confidence × priority_factor   # ← 排序（候选相对顺序不变）
#  因为 priority_factor ∈ (0.5, 1.0]，同一候选恒有 evidence ≥ combined ⇒
#  门槛**单调放宽**（不会出现"新口径反而更严"的候选）；排序键一字未改。
#
#  ⚠️ 冷启动口径保持原样（F11-C 之前的既有修复，本卡不改变）：
#     success_count == 0 的条目 confidence_factor 视为 1.0。若此处改用
#     条目自身的 confidence(0.4)，冷启动条目的 evidence = 0.4×sim，在
#     min_score=0.25 下需要 sim ≥ 0.625 ⇒ 刚学到的工作流**首跑必被卡**
#     （正是该既有修复要解决的问题）⇒ 语义上必须沿用同一个 confidence_factor。
# ═══════════════════════════════════════════════════════════════

#: 【F11-C-2】门槛口径逃生开关：置 0/false/no/off/disable ⇒ 回到**旧的乘性门槛**
#: （比较 combined）。默认（未设置）= 新口径（比较 evidence）。
#: 分级：置 0 是**恢复更严的旧行为**（收紧），不是"关掉即拆除防护" ⇒ A 级
#: （agent/settings/registry.py 已登记）。
_ENV_GATE_ON_EVIDENCE = "WORKFLOW_LEARNING_GATE_ON_EVIDENCE"

_FALSY_ENV_VALUES = ("0", "false", "no", "off", "disable", "disabled")


def gate_on_evidence() -> bool:
    """自动执行门槛是否按**证据分**(sim × confidence)比较（默认 True）

    每次调用读环境变量（与 F3-1 的 `volatile_tail_move_enabled` 同构），
    不改代码即可回滚：`WORKFLOW_LEARNING_GATE_ON_EVIDENCE=0` ⇒ 旧乘性门槛。
    """
    raw = os.environ.get(_ENV_GATE_ON_EVIDENCE)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY_ENV_VALUES


@dataclass(frozen=True)
class MatchScore:
    """一个候选的**完整打分明细**（门槛可审计的唯一读数源）

    两个分数**各自显式算出**（`score_candidate`），**不做任何除法还原**：
    任何一处口径改动都只会改这里的算式，不会让另一个分数被静默带偏。

        evidence = similarity × confidence_factor          # 门槛：sim × confidence
        combined = similarity × confidence_factor × priority_factor   # 排序：× 偏好

    Attributes:
        similarity: TF-IDF 余弦相似度（召回层读数）
        confidence: 条目自身 confidence（原值，便于复盘）
        confidence_factor: 计分**实际使用**的置信因子（冷启动条目 = 1.0，见模块 §F11-C-2）
        priority: 条目 priority（0~100，偏好/排序权重）
        priority_factor: 0.5 + priority/200 ∈ [0.5, 1.0]
        evidence: 证据分 —— 门槛与此值比较
        combined: 排序分 —— 候选相对顺序与此值比较
        cold_start: 是否走了"从未执行过"的冷启动口径（success_count == 0）
    """

    similarity: float
    confidence: float
    confidence_factor: float
    priority: int
    priority_factor: float
    evidence: float
    combined: float
    cold_start: bool

    def as_dict(self) -> Dict[str, object]:
        """审计载荷（**只含数值与口径标记，不含任何用户原文**）"""
        return {
            "similarity": round(self.similarity, 6),
            "confidence": round(self.confidence, 6),
            "confidence_factor": round(self.confidence_factor, 6),
            "priority": self.priority,
            "priority_factor": round(self.priority_factor, 6),
            "evidence": round(self.evidence, 6),
            "combined": round(self.combined, 6),
            "cold_start": self.cold_start,
        }


def score_candidate(sim: float, wf: LearnedWorkflow) -> MatchScore:
    """把一个 (相似度, 工作流) 候选算成两个分数（F11-C-2 唯一算式出处）

    - `confidence_factor`：`success_count == 0` ⇒ 1.0（冷启动口径，与改前一致）；
      否则取条目 `confidence`；
    - `evidence` 与 `combined` **分别**由 `sim` 与两个因子相乘得到，
      **不通过 `combined / priority_factor` 反推**（避免两个分数隐式耦合）。
    """
    priority_factor = 0.5 + wf.priority / 200.0          # 0.5 ~ 1.0
    cold_start = not (wf.success_count > 0)
    confidence_factor = 1.0 if cold_start else wf.confidence
    evidence = sim * confidence_factor                   # 门槛口径
    combined = sim * confidence_factor * priority_factor  # 排序口径
    return MatchScore(
        similarity=sim, confidence=wf.confidence,
        confidence_factor=confidence_factor, priority=wf.priority,
        priority_factor=priority_factor, evidence=evidence,
        combined=combined, cold_start=cold_start,
    )


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

    def match(self, task_text: str, *, top_k: int = 5,
              with_evidence: bool = False
              ) -> List[Tuple[LearnedWorkflow, float]]:
        """匹配任务文本到工作流（**默认形状/行为与改前逐位一致**）

        Args:
            task_text: 任务文本
            top_k: 候选池深度
            with_evidence: 【F11-C-2】False（默认）⇒ 返回 [(wf, combined)]，
                形状与改前**完全一致**（orchestrator / service / 既有测试不变）；
                True ⇒ 返回 [(wf, combined, evidence)]，两个分数**各自显式返回**
                （供需要门槛读数的调用方使用，无需自己除 priority_factor 反推）。

        Returns:
            [(workflow, combined_score)] 或 [(workflow, combined_score, evidence)]，
            排序键始终是 combined_score = similarity * confidence * (0.5 + priority/200)
            （F11-C-2 后 combined 只用于**排序**；门槛改用 evidence，
             见 WorkflowExecutor.try_execute 与 gate_on_evidence）。
        """
        scored = self.match_scored(task_text, top_k=top_k)
        if with_evidence:
            return [(wf, s.combined, s.evidence) for wf, s in scored]
        return [(wf, s.combined) for wf, s in scored]

    def match_scored(self, task_text: str, *, top_k: int = 5
                     ) -> List[Tuple[LearnedWorkflow, MatchScore]]:
        """匹配任务文本到工作流，返回 (workflow, MatchScore) 明细

        【F11-C-2】新增方法（不改既有 match() 的默认形状）：两个分数
        （evidence / combined）在 MatchScore 里**各自显式给出**，
        executor 用 evidence 做门槛、用 combined 做排序，并把两者一起写进
        追踪上下文（traced_action），使一次执行能复盘"为什么过/没过"。
        """
        return self._match_scored(task_text, top_k=top_k)

    def _match_scored(self, task_text: str, *, top_k: int = 5
                      ) -> List[Tuple[LearnedWorkflow, MatchScore]]:
        """召回 + 过滤 + 双分数计算（match / match_scored 的唯一实现）"""
        t0 = time.time()
        with traced_action("wf_match", task_text=task_text[:80]) as ctx:
            # query 可能触发 _rebuild（遍历 _docs），须与 register/unregister 的
            # 写互斥（锁内仅内存计算，无 I/O）
            with self._lock:
                candidates = self._index.query(task_text, top_k=top_k)
            # 锁外过滤：workflows 弱一致读取（并发 unregister 时 get 返回 None 即
            # 跳过，安全），日志/metrics 不占锁（持锁纪律）
            results: List[Tuple[LearnedWorkflow, MatchScore]] = []
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
                # 【F11-C-2】两个分数**各自显式算出**（score_candidate）：
                #   evidence = sim × conf_factor        → 门槛（executor 与 min_score 比较）
                #   combined = sim × conf_factor × pf  → 排序（候选相对顺序，与改前一致）
                # 【变易】冷启动执行通道（死锁修复，口径保持）：从未执行过的
                #   workflow（success_count=0）置信因子视为 1.0 —— 冷启动
                #   conf=0.4 会把分数压到难达 executor min_score 的程度
                #   （sim 0.8×0.4×0.75≈0.24 < 0.25），导致刚学的工作流永远无法
                #   首跑、无法积累执行记录。该口径**同时**作用于两个分数
                #   （见 score_candidate；若门槛另用条目 confidence，则冷启动
                #   条目 evidence=0.4×sim 需要 sim≥0.625，首跑仍会被卡）。
                ms = score_candidate(sim, wf)
                results.append((wf, ms))
            # 排序键**一字未改**：仍是 combined（priority 只在这里起作用）
            results.sort(key=lambda x: x[1].combined, reverse=True)
            elapsed = (time.time() - t0) * 1000
            ctx["candidates"] = len(candidates)
            ctx["matched"] = len(results)
            ctx["elapsed_ms"] = elapsed
            ctx["gate_on_evidence"] = gate_on_evidence()
            if results:
                top = results[0][1]
                ctx["top1_workflow_id"] = results[0][0].id
                ctx["top1_similarity"] = round(top.similarity, 6)
                ctx["top1_evidence"] = round(top.evidence, 6)
                ctx["top1_combined"] = round(top.combined, 6)
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
