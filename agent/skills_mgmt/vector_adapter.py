"""技能向量检索适配器 — 复用 memory/vector_store/VectorStore

设计目的:
    为 SkillLoader 第一层匹配提供向量检索能力，解决 TF-IDF 对
    description 为空技能（如 self_reflection / memory_summary）的召回缺陷。

架构层级:
    SkillLoader (loader.py)
        ↓ 注入
    SkillVectorAdapter (本模块)
        ↓ 复用
    VectorStore (memory/vector_store/vector_store.py)
        ↓ 自动选择
    ChromaDB / BM25 倒排索引 / 字符匹配 fallback

核心策略:
    - 向量化输入 = front matter（= `loader._meta_to_meta_text()`，**唯一实现**，
                    含 `description_zh` 与否由 `CP_SKILL_META_INCLUDE_ZH` 单开关决定）
                    + body 摘要（前 500 字符）
      → 解决 description 空白导致的字面匹配失效
    - 增量索引：首次调用 search() 时延迟构建索引，避免 SkillLoader 初始化时拉起 ChromaDB
    - 失败降级：向量检索失败时返回空列表，由 SkillLoader 决定是否回退 TF-IDF；
      **且该空列表携带显式降级标记**（SkillVectorSearchResult.degraded /
      degrade_reason / vector_leg_empty）+ 结构化日志 event
      `skill_vector.search.degraded` + 指标 `yunshu_skill_vector_degraded_total`，
      使"故障导致的空召回"与"真的没有语义匹配"可区分（审计 P11）
    - 与 SkillFileStore 解耦：通过 SkillFileStore 实例读元数据 + body，不直接访问磁盘

【不易】不修改 VectorStore 类，不修改 skill.md 定义
【变易】collection_name / model_name / body_summary_chars 可配置
【简易】单一职责：embed skills + search by vector
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from .file_store import SkillFileStore
from .observability import emit_metric
from agent.logging_utils import log_dict

logger = logging.getLogger("agent.skills_mgmt.vector_adapter")

# 默认配置
_DEFAULT_COLLECTION = "skill_metadata"
# 【变易】默认模型从 paraphrase-multilingual-MiniLM-L12-v2 升级为 BGE-m3
# 原因：BGE-m3 多语言能力强，中文 query 召回质量显著优于 MiniLM
# 验证：test_bge_m3_load.py 显示中文相似度判别 0.62+ vs 0.22~0.39
_DEFAULT_MODEL = "BAAI/bge-m3"
_DEFAULT_PERSIST_DIR = "./data/skill_vectors"
_DEFAULT_BODY_SUMMARY_CHARS = 500  # body 摘要长度，平衡向量化质量与性能

# BGE-m3 向量维度（用于校验）
_BGE_M3_DIM = 1024

# ── 【C1 修(3)】向量腿降级出口 ──────────────────────────────────────────────
# 审计 P11：旧实现超时/异常/后端缺失/编码失败一律 `return []`，与"真的没有语义
# 匹配"在**返回对象上不可区分**（doc_ids 为空时连日志都没有）。此处为每个降级原因
# 定义稳定字符串，供结构化日志 event、指标 label、health() 与上层判定共用。
_DEGRADE_NO_BACKEND = "no_backend"
_DEGRADE_TIMEOUT = "timeout"
_DEGRADE_EXCEPTION = "exception"
_DEGRADE_INDEX_EMPTY = "index_empty"
_DEGRADE_QUERY_ENCODE_FAILED = "query_encode_failed"
_DEGRADE_SIMILARITY_FAILED = "similarity_compute_failed"
_DEGRADE_NATIVE_CHROMA_QUERY_FAILED = "native_chroma_query_failed"
_DEGRADE_NATIVE_CHROMA_EMPTY = "native_chroma_empty"
_DEGRADE_VECTOR_STORE_SEARCH_FAILED = "vector_store_search_failed"
#: BGE-m3 不可用 → 落到 data/skill_vectors/native_chroma 那份**落盘旧库**
_DEGRADE_FALLBACK_STORE_ENGAGED = "fallback_store_engaged"
#: …且该库覆盖率低于阈值（审计实测 8/30 = 26.7%，默认阈值 50%）
_DEGRADE_FALLBACK_STORE_LOW_COVERAGE = "fallback_store_low_coverage"

# ── 【C1 裁决2】降级落盘库的覆盖率阈值 ───────────────────────────────────────
# 覆盖率 = 库内条目 / 索引源技能数；**低于阈值 ⇒ 必须显式标记 degraded**（不得看起来正常）。
# 默认 0.5 的理由：
#   ① 实测该库覆盖率 8/30 = 26.7%（远低于任何合理阈值），50% 能把它明确判为"低覆盖"；
#   ② 覆盖率 50% 意味着语义层丢掉一半技能，对 Layer-1 召回已是不可接受的质量下降；
#   ③ 阈值只影响**标记/告警**，不改变返回内容（"改走 TF-IDF"属产品决策，见报告 §13 裁决2）。
# 可用 SKILL_VECTOR_FALLBACK_MIN_COVERAGE（0~1 比例）覆盖。
_FALLBACK_STORE_MIN_COVERAGE_ENV = "SKILL_VECTOR_FALLBACK_MIN_COVERAGE"
_FALLBACK_STORE_MIN_COVERAGE_DEFAULT = 0.5


# ── 【RET-1R · R-2】负样本正则（两档，作用域不同）────────────────────────────
#: 档 1（**与后端无关**）：纯数字 / 符号组合。例："12345" / "1 2 3" / "!!!"。
#: 依据：这类 query 没有任何语义，任何后端下都不构成合法技能检索意图 ⇒
#: 过滤它**不损失召回**（因此可以放在懒构建之前，避免垃圾 query 拉起 BGE-m3）。
_SYMBOL_ONLY_RE = re.compile(r"^[\d\s\W]+$")
#: 档 2（**只在字面匹配兜底后端**）：纯 ASCII 单词/词组。例："def print_hello"。
#: 依据：它要防的是"字面匹配把标识符误命中技能"，真向量后端由语义负责区分；
#: 改前它对**所有**后端无条件生效 ⇒ 合法英文 query（含空格的英文短语）被整条
#: 过滤，向量腿英文召回 1/8（RET-1R 实测；绕开该档直接比余弦是 8/8）。
_ASCII_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_ ]*$")


def _is_empty_or_symbol_only(query: str) -> bool:
    """【RET-1R】"后端无关"档负样本：空 / 单字符 / 纯数字符号（不含中文）

    为什么这一档与后端无关：空串、单字符、纯数字/符号都**没有语义**，任何后端
    （BGE-m3 语义 / chromadb embedding / 字面匹配兜底）下都不构成合法技能检索
    意图 ⇒ 过滤它们不损失召回。

    与 `SkillVectorAdapter._is_negative_query` 的关系：本函数是它的**前两条规则**，
    抽成独立函数是为了让 `search()` 把"不损失召回的部分"留在懒构建**之前**执行
    （垃圾 query 不再触发 ≈54s 的 BGE-m3 懒构建），而把"纯 ASCII 单词/词组 +
    编程关键字"（会误伤合法英文 query 的那部分）留给字面匹配兜底后端 ——
    见 `SkillVectorAdapter._negative_filter_applies`。

    【不易】中文豁免保留原语义：含中文的"123 安全"仍可能是合法查询，不算负样本。
    """
    if not query or not query.strip():
        return True
    q = query.strip()
    # 长度 < 2 的查询（单字符如 "啊" "a"）
    if len(q) < 2:
        return True
    if _SYMBOL_ONLY_RE.match(q) and not re.search(r"[\u4e00-\u9fa5]", q):
        return True
    return False


def _fallback_min_coverage() -> float:
    """读取降级库覆盖率阈值（非法值回退默认值，并夹到 [0,1]）"""
    raw = os.environ.get(_FALLBACK_STORE_MIN_COVERAGE_ENV)
    if raw is None or not str(raw).strip():
        return _FALLBACK_STORE_MIN_COVERAGE_DEFAULT
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'fallback_store.threshold_invalid', 'env': _FALLBACK_STORE_MIN_COVERAGE_ENV, 'value': str(raw)[:40], 'fallback': _FALLBACK_STORE_MIN_COVERAGE_DEFAULT}))
        return _FALLBACK_STORE_MIN_COVERAGE_DEFAULT
    return min(1.0, max(0.0, value))

# ── 【C1 修(4)】内容哈希增量的"阈值触发全量重建"参数 ────────────────────────
# 重编码代价 = 4.25 GB BGE-m3 模型 × CPU 单条推理（单条耗时本卡未实测：加载
# 4.25 GB 模型不在本卡资源预算内，故阈值**只以条数为唯一判据**，不估时间）。
# 增量路径：变更条数少时只重编码变更项 + 一次 numpy 行删除/堆叠；
# 全量路径：脏条数 >= max(下限, 比例 × 总数) 时整体重建，避免碎片化与多次 encode 调用。
_CONTENT_HASH_FULL_REBUILD_MIN_DIRTY = 8
_CONTENT_HASH_FULL_REBUILD_DIRTY_RATIO = 0.4


def _age_days_from_stamp(stamp: Optional[str]) -> Optional[float]:
    """把 chromadb 的 `embeddings.created_at` 文本时间戳转成"距今多少天"

    【不易】解析失败返回 None（调用方据此改用库文件 mtime，并如实标注 age_source）
    """
    if not stamp:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(str(stamp)[:19], fmt)
        except ValueError:
            continue
        return round((time.time() - parsed.timestamp()) / 86400.0, 1)
    return None


class SkillVectorSearchResult(list):
    """技能向量检索结果（list 子类，携带显式降级标记）

    【不易】仍然是 list：`if not results` / 下标 / 迭代 / json.dumps /
            isinstance(x, list) 全部与旧返回值一致 ⇒ loader._try_vector_match、
            loader._try_rrf_match 等现有消费方**零改动**即可继续工作
    【变易】额外携带 degraded / degrade_reason / vector_leg_empty / backend /
            coverage，让上层与运维无需改调用签名就能区分
            「向量腿**故障降级**为空」与「真的没有语义匹配」（审计 P11）
    【简易】纯数据载体，无副作用
    """

    __slots__ = ("degraded", "degrade_reason", "vector_leg_empty", "backend", "coverage")

    def __init__(
        self,
        items: Optional[List[Dict[str, Any]]] = None,
        *,
        degraded: bool = False,
        degrade_reason: Optional[str] = None,
        backend: str = "none",
        coverage: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(items or [])
        #: True = 本次检索走了降级路径（超时/异常/无后端/索引空/编码失败/旧库降级）
        self.degraded = bool(degraded)
        #: 降级原因（_DEGRADE_* 常量之一；未降级为 None）
        self.degrade_reason = degrade_reason
        #: True = 结果为空列表（degraded=True 时表示"故障导致的空召回"）
        self.vector_leg_empty = len(self) == 0
        #: 实际使用的后端：st_backend / native_chroma / vector_store / none
        self.backend = backend
        #: 覆盖度快照（索引源技能数 vs 已索引数 / 降级落盘库条目数）
        self.coverage = coverage or {}

    def state(self) -> Dict[str, Any]:
        """降级标记的字典视图（供日志 / 健康检查 / 上层透传，避免各处重复拼装）"""
        return {
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "vector_leg_empty": self.vector_leg_empty,
            "result_count": len(self),
            "backend": self.backend,
            "coverage": dict(self.coverage),
        }


class SkillVectorAdapter:
    """技能向量检索适配器 — 把 SkillFileStore 的元数据/正文向量化并支持语义搜索

    用法:
        adapter = SkillVectorAdapter(file_store=SkillFileStore())
        adapter.ensure_indexed()  # 延迟构建索引（首次调用 search 会自动触发）
        results = adapter.search("帮我反思刚才的回答", top_k=5, enabled_only=True)
        # results: [{"skill_id": "self_reflection", "score": 0.85, "metadata": {...}}]

    线程安全:
        - 索引构建由 **threading.RLock** 保护，避免并发重复构建；用 RLock 是因为
          公开入口存在同线程重入（普通 Lock 会静默自死锁 —— 见 __init__ 的注释
          与报告 C1.md §12）
        - VectorStore 自身的搜索/添加已内部加锁

    可观测性:
        - 关键操作通过 emit_metric 上报业务指标
        - 结构化日志记录 trace 链路
    """

    def __init__(
        self,
        file_store: SkillFileStore,
        *,
        collection_name: str = _DEFAULT_COLLECTION,
        model_name: str = _DEFAULT_MODEL,
        persist_dir: str = _DEFAULT_PERSIST_DIR,
        body_summary_chars: int = _DEFAULT_BODY_SUMMARY_CHARS,
        vector_store: Optional[Any] = None,
        use_native_chroma: bool = True,
        use_sentence_transformers: bool = True,
    ):
        """初始化向量适配器

        Args:
            file_store: SkillFileStore 实例，用于读取技能元数据与 body
            collection_name: 向量集合名称
            model_name: Sentence Transformers 模型名（默认 BGE-m3，多语言支持中文）
            persist_dir: 向量持久化目录
            body_summary_chars: body 摘要长度（向量化输入的一部分）
            vector_store: 可选的 VectorStore 实例（测试注入），None 则延迟创建
            use_native_chroma: True 则用 chromadb 原生 API（仅当 sentence-transformers
                不可用时启用，使用 onnxruntime embedding all-MiniLM-L6-v2）
            use_sentence_transformers: True 则优先用 BGE-m3 via sentence-transformers
                （已验证 Windows 上无 DLL 冲突，中文召回质量最优）；
                False 则跳过此路径，回退 native_chroma

        优先级（【变易】可配置）:
            1. use_sentence_transformers=True → BGE-m3 + 本地 numpy 向量库（最优）
            2. use_native_chroma=True → chromadb + onnxruntime（兜底）
            3. VectorStore → sentence-transformers + chromadb（最后兜底）
            4. 全部失败 → 返回 None，外层降级 TF-IDF
        """
        self.fs = file_store
        self.collection_name = collection_name
        self.model_name = model_name
        self.persist_dir = persist_dir
        self.body_summary_chars = body_summary_chars
        self.use_native_chroma = use_native_chroma
        self.use_sentence_transformers = use_sentence_transformers

        # 延迟创建 VectorStore，避免 SkillLoader 启动时拉起 ChromaDB/torch
        self._vector_store = vector_store
        self._native_chroma: Optional[tuple] = None  # (client, collection)
        # 【变易】BGE-m3 sentence-transformers 模式：自管理向量库
        # 存储 (model, doc_ids, doc_vectors, doc_metas)
        self._st_backend: Optional[tuple] = None
        self._indexed_skill_ids: set = set()  # 已索引的技能 ID（用于增量同步）
        # 【C1 修(4)】skill_id -> 向量化输入文本的 md5：增量判据从"id 集合差"改为
        # "内容哈希差"，消除"同 id 内容变更永不重编码"（审计 Q3 W2）
        self._indexed_content_hash: Dict[str, str] = {}
        # 【C1 复核修复】必须是 **RLock**：本适配器的公开入口存在同线程重入
        # （ensure_indexed / upsert 持锁时会调用删向量、查状态等内部方法）。
        # 用普通 Lock 时这种嵌套获取是**静默自死锁**：不抛异常、不打日志、线程
        # 永远不返回（本卡实测踩到过：ensure_indexed → _remove_skill_vector →
        # with self._lock 卡死，pytest 只能靠超时中断）。改 RLock 后该类缺陷在
        # 结构上不可能再现；同时保留 _xxx_locked 变体以避免无谓的重入。
        self._lock = threading.RLock()
        self._index_built = False
        # 【C1 修(3)】降级出口状态（只增可见性，不改变降级行为）
        # _last_search_state: 上一次 search() 的结局（degraded/reason/query/backend）
        # _degrade_counts: reason -> 次数（进程内累计，供 health() 与运维排查）
        # _active_backend_degraded: 当前活动后端是否已是降级态（BGE-m3 不可用→落盘旧库）
        self._last_search_state: Dict[str, Any] = {
            "degraded": False, "reason": None, "query": None, "backend": "none",
        }
        self._degrade_counts: Dict[str, int] = {}
        self._active_backend_degraded: bool = False
        self._active_degrade_reason: Optional[str] = None
        # 降级落盘库（native_chroma）的只读探针结果：条目数 / 索引源技能数 / 库年龄
        self._fallback_store: Dict[str, Any] = {}
        # 【变易】query embedding LRU 缓存 — 高频重复 query 跳过 BGE-m3 推理（5-10ms → 0ms）
        # 缓存 key: query 文本, value: 归一化向量 (dim=1024,)
        # 与 _st_backend 绑定：模型切换时调用 _invalidate_query_cache() 清空
        self._query_cache: OrderedDict[str, Any] = OrderedDict()
        self._query_cache_maxsize: int = 128  # LRU 容量，可配
        self._query_cache_hits: int = 0       # 命中计数（可观测性）
        self._query_cache_misses: int = 0     # 未命中计数
        # 【变易】per-key 锁 — 解决 Thundering herd 问题
        # 多线程同时请求同一未缓存 query 时，仅一个线程执行 model.encode
        # 其他线程等待 per-key 锁后走 double-checked locking 直接取缓存
        self._per_key_locks: Dict[str, threading.Lock] = {}
        self._per_key_locks_maxsize: int = 256  # 略大于 query_cache 防过早清理
        self._thundering_herd_avoided: int = 0  # 被 per-key 锁避免的重复推理次数

    # ──────────────────────────────────────────────
    #  索引构建
    # ──────────────────────────────────────────────

    def _build_vector_text(self, meta: Dict[str, Any], skill_id: str) -> str:
        """构建单个技能的向量化输入文本

        策略：front matter（核心）+ body 摘要（补充语义）
        - front matter: `loader._meta_to_meta_text()` 的输出（**唯一实现**）
        - body 摘要: 前 N 字符（弥补 description 为空的缺陷）

        【G1C-UA · front matter 不再自己拼字段】

        原先这里是 `loader._meta_to_meta_text()` 的**同形独立实现**（各自一份
        `name/description/tags/category` 列表）。G1C-U1 只给 loader 那份并入了
        `description_zh` ⇒ 三腿之中只有 TF-IDF 看到了中文收益，向量腿与 BM25 腿
        **口径不同**。本卡把 front matter 改为**调用同一函数**：

            · 字段：由 `loader._meta_to_meta_text` 决定；
            · 开关：`CP_SKILL_META_INCLUDE_ZH`，**不新造第二个开关** ——
              否则会出现"关了一个、另一个还开着"的假生效。
            · `name_fallback=skill_id`：保留本腿原有的"name 缺失时用 skill_id 兜底"
              口径（默认空串的调用方不受影响）。

        【代价（不许只说好话）】`description_zh` 进文本 ⇒ 内容哈希变 ⇒ 20 条双轨技能
        变脏；脏条数 20 ≥ max(8, 0.4×28=11) ⇒ 首次启动触发**一次全量重编码**
        （本卡实测：BGE-m3 28 条**纯编码 46.7 s**，单条 1.668 s；模型加载另计 ≈ 18 s。
        这是一次性代价，不是每次检索）。置 `CP_SKILL_META_INCLUDE_ZH=0` ⇒ 文本与
        哈希**逐字回到改前**，不会触发重编码（本卡已实测，见 G1C-UA.md §4）。

        【本卡实测：向量腿的中文召回与这条改动无关】改前向量腿中文 query 已经是
        **8/8**（BGE-m3 跨语言 + body 摘要里的中文），改后仍 **8/8**；本改动在向量腿
        上的收益是**口径统一**（消除"三条腿各有各的字段"），不是召回提升。

        【实现】导入放在函数内：避免 loader ↔ vector_adapter 的**导入期**循环依赖
        （loader 是在 `_get_vector_adapter()` 里延迟导入本模块的）。

        【变易】body 摘要长度可配置，平衡向量化质量与性能
        """
        from .loader import _meta_to_meta_text  # 延迟导入：见上「front matter 不再自己拼字段」
        front_text = _meta_to_meta_text(meta, name_fallback=skill_id)

        # 读 body 摘要（第二层），失败不影响向量化（用 front_text 兜底）
        body_summary = ""
        try:
            body = self.fs.load_instruction(skill_id)
            if body:
                body_summary = body[: self.body_summary_chars]
        except Exception as e:  # noqa: BLE001
            logger.debug(
                f"load_instruction failed for {skill_id}: {e}, "
                f"using front matter only"
            )

        if body_summary:
            return f"{front_text}\n{body_summary}"
        return front_text

    def _ensure_vector_store(self) -> Any:
        """延迟创建向量后端实例

        优先策略（按质量与稳定性排序）:
            1. BGE-m3 via sentence-transformers（多语言，中文召回最优，已验证无 DLL 冲突）
            2. chromadb 原生 API（onnxruntime embedding，all-MiniLM-L6-v2 兜底）
            3. VectorStore（sentence-transformers + chromadb，最后兜底）
            4. 全部失败 → 返回 None，外层降级 TF-IDF

        【变易】可通过 use_sentence_transformers / use_native_chroma 参数控制
        【简易】每个后端独立 try/except，失败降级下一档
        """
        if self._vector_store is not None:
            return self._vector_store

        # 优先尝试 BGE-m3 via sentence-transformers（最优路径）
        st_requested = bool(self.use_sentence_transformers)
        st_failed = False
        if self.use_sentence_transformers:
            st_backend = self._try_init_sentence_transformers()
            if st_backend is not None:
                self._st_backend = st_backend
                # 返回一个标识，让 ensure_indexed/search 走 _st_backend 分支
                # 这里返回 st_backend 本身作为非 None 标识
                self._vector_store = st_backend
                # 解除降级标记（上一次可能已落到 native_chroma）
                self._active_backend_degraded = False
                self._active_degrade_reason = None
                return st_backend
            st_failed = True

        # 回退到 chromadb 原生 API（避开 sentence-transformers/torch）
        if self.use_native_chroma:
            native = self._try_init_native_chroma()
            if native is not None:
                self._vector_store = native
                self._native_chroma = native
                # 【C1 修(3)】BGE-m3 路失败 ⇒ 落到 data/skill_vectors/native_chroma
                # 那份**落盘旧库**。旧实现静默使用它（审计实测只覆盖 8/30），
                # 此处必须显式告警并置降级标记，否则上层看不出语义层只剩一部分技能。
                if st_requested and st_failed:
                    self._warn_fallback_store(reason="sentence_transformers_init_failed")
                return native

        # 最后回退到 VectorStore（可能 DLL 冲突，但保留作为兼容路径）
        try:
            import sys
            from pathlib import Path

            project_root = Path(__file__).resolve().parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            from memory.vector_store.vector_store import VectorStore  # noqa: E402

            self._vector_store = VectorStore(
                collection_name=self.collection_name,
                persist_dir=self.persist_dir,
                model_name=self.model_name,
                cache_size=50,
                cache_ttl=600,
                enable_inverted_index=True,  # fallback 模式下用 BM25
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"VectorStore initialization failed: {e}, "
                f"vector search will return empty"
            )
            self._vector_store = None
        return self._vector_store

    def _try_init_sentence_transformers(self) -> Optional[Any]:
        """初始化 BGE-m3 via sentence-transformers

        使用 BAAI/bge-m3 模型（1024 维，多语言）。
        自管理一个简单的内存向量库（numpy 数组），避免 chromadb 依赖。

        已验证（test_bge_m3_load.py）:
            - Windows 上无 DLL 冲突（torch 2.13 + onnxruntime 1.20 共存）
            - 中文 query 召回质量显著优于 all-MiniLM-L6-v2
            - 模型加载耗时约 11 分钟（首次），后续从缓存加载 < 10s

        Returns:
            (model, [], [], []) 元组：(SentenceTransformer, doc_ids, doc_vectors, doc_metas)
            初始时 doc 列表为空，ensure_indexed 后填充
            失败返回 None
        """
        try:
            import os
            # 设置 HF 镜像（国内访问优化）
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

            from sentence_transformers import SentenceTransformer

            # device="cpu" 避免 CUDA 依赖；normalize_embeddings=True 让相似度=点积
            model = SentenceTransformer(self.model_name, device="cpu")
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'sentence_transformers.init.ok', 'model': self.model_name, 'dim': model.get_sentence_embedding_dimension()}))
            # 返回 4 元组：(model, doc_ids, doc_vectors, doc_metas)
            # doc_ids: List[str] - 技能 ID 顺序
            # doc_vectors: np.ndarray (N, dim) - 归一化后的文档向量
            # doc_metas: List[Dict] - 技能元数据
            return (model, [], [], [])
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'sentence_transformers.init.failed', 'model': self.model_name, 'error': str(e)[:300]}))
            return None

    def _try_init_native_chroma(self) -> Optional[Any]:
        """初始化 chromadb 原生 API 后端（避开 sentence-transformers）

        使用 chromadb 默认的 onnxruntime embedding (all-MiniLM-L6-v2)，
        已验证在 Windows 上无 DLL 冲突。

        返回 (client, collection) 元组，或 None（初始化失败）
        """
        try:
            import os
            from pathlib import Path

            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

            import chromadb
            from chromadb.config import Settings

            persist_path = str(Path(self.persist_dir) / "native_chroma")
            Path(persist_path).mkdir(parents=True, exist_ok=True)

            client = chromadb.PersistentClient(
                path=persist_path,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Skill retrieval index"},
            )
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'native_chroma.init.ok', 'persist_path': persist_path, 'collection': self.collection_name}))
            return (client, collection)
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'native_chroma.init.failed', 'error': str(e)}))
            return None

    # ──────────────────────────────────────────────
    #  【C1 修(3)】降级出口（对照工具链 EmbeddingIndex 的 degrade_to=bm25_only）
    # ──────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        """当前活动后端名（st_backend / native_chroma / vector_store / none）"""
        if self._st_backend is not None:
            return "st_backend"
        if self._native_chroma is not None:
            return "native_chroma"
        if self._vector_store is not None:
            return "vector_store"
        return "none"

    @property
    def degraded(self) -> bool:
        """是否处于降级态（① 活动后端是覆盖不足的落盘旧库 或 ② 上次检索降级）"""
        return bool(self._active_backend_degraded or
                    self._last_search_state.get("degraded"))

    def last_search_state(self) -> Dict[str, Any]:
        """上一次 search() 的结局快照（浅拷贝 + 降级计数副本，供上层/运维判定）"""
        with self._lock:
            state = dict(self._last_search_state)
            state["degrade_counts"] = dict(self._degrade_counts)
            return state

    def _collect_fallback_store_stats(self) -> Dict[str, Any]:
        """只读探测降级落盘向量库（native_chroma）的条目数与库年龄

        【不易】只读：不写入、不删除、不重建 data/skill_vectors/（运行数据）
        【简易】条目数优先 collection.count()；年龄优先只读 sqlite 的
                embeddings.created_at，sqlite 不可读时退回库文件 mtime
                （age_source 字段如实标注用的是哪一种）
        """
        stats: Dict[str, Any] = {
            "path": None, "entries": None, "source_count": None,
            "newest_entry": None, "oldest_entry": None,
            "age_days": None, "age_source": None,
        }
        try:
            from pathlib import Path
            local = Path(self.persist_dir) / "native_chroma"
            stats["path"] = str(local)
            if self._native_chroma is not None:
                try:
                    _, collection = self._native_chroma
                    stats["entries"] = int(collection.count())
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"fallback_store.count_failed: {e}")
            db_path = local / "chroma.sqlite3"
            if db_path.exists():
                try:
                    import sqlite3
                    uri = "file:%s?mode=ro" % str(db_path).replace("\\", "/")
                    con = sqlite3.connect(uri, uri=True)
                    try:
                        row = con.execute(
                            "select min(created_at), max(created_at), count(*) from embeddings"
                        ).fetchone()
                    finally:
                        con.close()
                    oldest, newest, cnt = (row + (None, None, None))[:3]
                    stats["oldest_entry"], stats["newest_entry"] = oldest, newest
                    if stats["entries"] is None:
                        stats["entries"] = int(cnt or 0)
                    stats["age_days"] = _age_days_from_stamp(newest)
                    if stats["age_days"] is not None:
                        stats["age_source"] = "embeddings.created_at"
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"fallback_store.sqlite_probe_failed: {e}")
                if stats["age_days"] is None:
                    stats["age_days"] = round((time.time() - db_path.stat().st_mtime) / 86400.0, 1)
                    stats["age_source"] = "chroma.sqlite3 mtime"
            try:
                stats["source_count"] = len(self.fs.load_metadata_index())
            except Exception as e:  # noqa: BLE001
                logger.debug(f"fallback_store.source_count_failed: {e}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"fallback_store.probe_failed: {e}")
        return stats

    def _warn_fallback_store(self, *, reason: str) -> None:
        """落到落盘旧库时的显式告警 + 降级标记（审计 P11 的"静默落到 8/30 旧库"）

        【不易】不阻止降级（仍可用，保持原行为），只把"覆盖数 / 库年龄 / 后果"
                写进结构化日志、指标与 health()，供上层与运维判定
        """
        stats = self._collect_fallback_store_stats()
        entries, source_count = stats.get("entries"), stats.get("source_count")
        # 【C1 裁决2】覆盖率阈值可配（默认 50%）：低于阈值 ⇒ 显式降级标记 + 额外告警指标
        threshold = _fallback_min_coverage()
        coverage_ratio = (float(entries) / float(source_count)
                          if isinstance(entries, int) and isinstance(source_count, int) and source_count > 0
                          else None)
        deficient = coverage_ratio is not None and coverage_ratio < threshold
        stats["coverage_ratio"] = (round(coverage_ratio, 4) if coverage_ratio is not None else None)
        stats["min_coverage_threshold"] = threshold
        stats["below_threshold"] = bool(deficient)
        with self._lock:
            self._fallback_store = stats
            self._active_backend_degraded = True
            self._active_degrade_reason = (_DEGRADE_FALLBACK_STORE_LOW_COVERAGE if deficient
                                           else _DEGRADE_FALLBACK_STORE_ENGAGED)
            self._degrade_counts[self._active_degrade_reason] = (
                self._degrade_counts.get(self._active_degrade_reason, 0) + 1)
        logger.warning(log_dict({
            'module_name': 'vector_adapter',
            'action': 'skill_vector.fallback_store.engaged',
            'event': 'skill_vector.fallback_store.engaged',
            'degrade_to': 'native_chroma_fallback_store',
            'reason': reason,
            'store_entries': entries,
            'source_count': source_count,
            'coverage': (None if not isinstance(entries, int) or not source_count
                         else round(100.0 * entries / source_count, 1)),
            # 【C1 裁决2】阈值与判定结果一并落日志，运维一眼可判"是否低到该拒绝"
            'coverage_ratio': stats.get('coverage_ratio'),
            'min_coverage_threshold': threshold,
            'below_threshold': bool(deficient),
            'store_age_days': stats.get('age_days'),
            'age_source': stats.get('age_source'),
            'newest_entry': stats.get('newest_entry'),
            'store_path': stats.get('path'),
            'impact': ('vector leg will only cover store_entries of source_count skills '
                       'until BGE-m3 loads'),
        }))
        if deficient:
            emit_metric("yunshu_skill_vector_fallback_store_low_coverage",
                        value=1, kind="counter", labels={"failure": "true",
                                                         "threshold": str(threshold)})
        else:
            # 覆盖率达标（但仍是从 BGE-m3 降级到旧库）→ 仍算降级，但打不同的 metric 标签
            emit_metric("yunshu_skill_vector_fallback_store_engaged",
                        value=1, kind="counter", labels={"failure": "true"})

    def _mark_degraded(self, reason: str, *, query: str = "",
                       extra: Optional[Dict[str, Any]] = None) -> None:
        """记录一次向量腿降级：结构化 WARN + 指标 + 状态快照

        【不易】本方法只**记录**状态（无返回值），调用方照旧返回空列表让外层
                SkillLoader 降级 TF-IDF/RRF 融合；它改变的是"可见性"不是"行为"
                —— 审计 P11 要求"不许再静默 return []"
        """
        with self._lock:
            self._degrade_counts[reason] = self._degrade_counts.get(reason, 0) + 1
            self._last_search_state = {
                "degraded": True,
                "reason": reason,
                "query": (query or "")[:50],
                "backend": self.backend_name,
            }
            if extra:
                self._last_search_state.update(extra)
        payload = {
            'module_name': 'vector_adapter',
            'action': 'skill_vector.search.degraded',
            'event': 'skill_vector.search.degraded',
            # 对照工具链：置位时显式声明后果（EmbeddingIndex.degrade_to='bm25_only'）
            'degrade_to': 'tfidf_bm25_fusion',
            'reason': reason,
            'query': (query or "")[:50],
            'backend': self.backend_name,
            # 对照工具链：同时给出"该后端覆盖了多少技能"，避免只报"降级"不报"影响面"
            'indexed_count': len(self._indexed_skill_ids),
        }
        if extra:
            payload.update(extra)
        logger.warning(log_dict(payload))
        emit_metric("yunshu_skill_vector_degraded_total", value=1, kind="counter",
                    labels={"reason": reason, "failure": "true"})

    def _reset_search_state(self, query: str) -> None:
        """重置"本次检索"的降级状态（每次 search 开始前调用）

        【不易】只改状态快照，不改任何检索行为
        """
        with self._lock:
            self._last_search_state = {
                "degraded": False,
                "reason": None,
                "query": (query or "")[:50],
                "backend": self.backend_name,
            }

    def _coverage_snapshot(self) -> Dict[str, Any]:
        """覆盖度快照：索引源技能数 / 已索引数 / 降级落盘库条目数（供结果与 health）"""
        snapshot: Dict[str, Any] = {
            "indexed_count": len(self._indexed_skill_ids),
            "active_backend_degraded": bool(self._active_backend_degraded),
        }
        if self._fallback_store:
            snapshot["fallback_store_entries"] = self._fallback_store.get("entries")
            snapshot["fallback_store_source_count"] = self._fallback_store.get("source_count")
            snapshot["fallback_store_age_days"] = self._fallback_store.get("age_days")
            # 【C1 裁决2】把"覆盖率 + 阈值 + 是否低于阈值"一并透出，
            # 让上层可以据此自行决定是否拒绝这份降级结果
            snapshot["fallback_store_coverage_ratio"] = self._fallback_store.get("coverage_ratio")
            snapshot["fallback_store_min_coverage_threshold"] = self._fallback_store.get("min_coverage_threshold")
            snapshot["fallback_store_below_threshold"] = self._fallback_store.get("below_threshold")
        return snapshot

    def _result(self, items: Optional[List[Dict[str, Any]]] = None) -> "SkillVectorSearchResult":
        """构造带降级标记的检索结果（成功路径：活动后端降级时同样打标）"""
        return SkillVectorSearchResult(
            items,
            degraded=bool(self._active_backend_degraded),
            degrade_reason=self._active_degrade_reason,
            backend=self.backend_name,
            coverage=self._coverage_snapshot(),
        )

    def _degraded_result(self, reason: Optional[str]) -> "SkillVectorSearchResult":
        """构造降级结果（空召回 + 显式原因）"""
        return SkillVectorSearchResult(
            [],
            degraded=bool(reason),
            degrade_reason=reason,
            backend=self.backend_name,
            coverage=self._coverage_snapshot(),
        )

    def _vector_text_and_hash(self, meta: Dict[str, Any], skill_id: str) -> Tuple[str, str]:
        """返回 (向量化输入文本, 内容哈希)

        【不易】哈希由 `_build_vector_text` 的**返回值**算出 ⇒ "哈希变了"严格等价于
                "送进模型的文本变了"，不会出现"哈希判新但文本相同"的空重编码
        """
        text = self._build_vector_text(meta, skill_id)
        return text, hashlib.md5(text.encode("utf-8")).hexdigest()

    def _reset_index_locked(self) -> None:
        """清空内存索引状态（调用方需持锁）

        【不易】只清内存与当前后端集合里的条目；**不删除落盘库文件**
        """
        if self._st_backend is not None:
            model, _doc_ids, _doc_vectors, _doc_metas = self._st_backend
            self._st_backend = (model, [], [], [])
        self._indexed_skill_ids.clear()
        self._indexed_content_hash.clear()
        self._index_built = False

    def _drop_native_chroma_ids_locked(self, skill_ids: Any) -> None:
        """从 chromadb 集合删除指定技能条目（调用方需持锁；条目不存在时是 no-op）

        用途：全量重建前清理同 id 旧条目 —— chromadb 的 collection.add 不接受重复 id。
        """
        if self._native_chroma is None:
            return
        ids = [f"skill_{sid}" for sid in sorted(skill_ids)]
        if not ids:
            return
        try:
            _, collection = self._native_chroma
            collection.delete(ids=ids)
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'native_chroma.reset.failed', 'error': str(e)[:200]}))

    def ensure_indexed(self, *, force: bool = False) -> int:
        """构建/刷新技能向量索引

        增量判据（【C1 修(4)】内容哈希，替代旧的 id 集合差）：
        - 旧实现用 `current_ids - self._indexed_skill_ids` ⇒ 同 id 的 skill.md 内容变了
          （git pull / 手工编辑 / 描述改造）**永不重编码**，向量永远是旧的（审计 Q3 W2）。
        - 新实现用内容哈希：哈希源 = `_build_vector_text(meta, sid)` 的返回值
          （= name + description + tags + category + body 前 N 字符，即**真正送进
          BGE-m3 的文本**）。文本没变不编码；变了则先删旧向量再重编码。
        - 已删除的技能：连同向量一起清理（旧实现不清理，残留向量会继续被召回）。

        成本策略（重编码代价 = 4.25 GB 模型 × CPU 单条推理）：
        - 常用路径 = **增量**：只有变更项重新编码（典型 0~2 条）；
        - **阈值触发全量**：脏条数 >= max(_CONTENT_HASH_FULL_REBUILD_MIN_DIRTY,
          _CONTENT_HASH_FULL_REBUILD_DIRTY_RATIO × 总数) 时整体重建，
          避免大量单条 numpy 行删除/堆叠与多次 encode 调用；
        - force=True 或首次构建 = 全量。
        单条编码耗时的**实测值本卡未取得**（不在加载 4.25 GB 模型的资源预算内），
        故阈值只以"条数"为判据，不使用任何未实测的时间估计。

        其他策略：
        - 维度不匹配：自动重建（模型切换/升级时保护）
        - 后端缺失：返回 0（`ensure_indexed.skipped_no_backend`，不抛异常）
        - 覆盖缺口：构建后仍不足源技能数时打 `ensure_indexed.coverage_gap` WARN

        Returns: 已索引的技能数量
        """
        vs = self._ensure_vector_store()
        if vs is None:
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'ensure_indexed.skipped_no_backend', 'reason': 'vector_store unavailable'}))
            return 0

        with self._lock:
            # [不易] 维度校验 — 模型切换/升级导致维度不一致时重建索引
            # 场景：persist_dir 中残留旧维度向量，但 model_name 已变更
            # 策略：检测到不一致则清空内存索引，触发全量重建（不报错）
            if self._st_backend is not None:
                model, doc_ids, doc_vectors, doc_metas = self._st_backend
                if len(doc_vectors) > 0:
                    try:
                        expected_dim = model.get_sentence_embedding_dimension()
                        actual_dim = int(doc_vectors.shape[1])
                        if actual_dim != expected_dim:
                            logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'dimension_mismatch_rebuild', 'expected': expected_dim, 'actual': actual_dim, 'model': self.model_name}))
                            # 【C1 修(4)】必须走 _reset_index_locked：它同时清空
                            # _indexed_content_hash。若只清 _indexed_skill_ids，下面的
                            # 内容哈希判增量会认为"内容没变"从而跳过重编码 ⇒ 维度重建后
                            # 索引反而永久为空
                            self._reset_index_locked()
                        else:
                            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'ensure_indexed.dim_check_ok', 'expected': expected_dim, 'actual': actual_dim, 'doc_count': len(doc_ids)}))
                    except Exception as e:  # noqa: BLE001
                        logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'dimension_check_failed', 'error': str(e)[:200]}))

            index = self.fs.load_metadata_index()
            current_ids = set(index.keys())

            # 【C1 修(4)】内容哈希：文本只在这里算一次，哈希与"送进模型的文本"同源
            texts: Dict[str, str] = {}
            hashes: Dict[str, str] = {}
            for skill_id in current_ids:
                texts[skill_id], hashes[skill_id] = self._vector_text_and_hash(
                    index[skill_id], skill_id)

            if force:
                # 强制重建：清空内存索引状态，重新全量构建
                self._reset_index_locked()
            else:
                # 已删除的技能：连向量一起清（旧实现残留，会被继续召回）
                for gone in sorted((set(self._indexed_skill_ids) |
                                    set(self._indexed_content_hash)) - current_ids):
                    self._remove_skill_vector_locked(gone)

            # 脏集合 = 内容哈希与已索引哈希不同的技能（含新增）
            dirty_ids = {sid for sid in current_ids
                         if self._indexed_content_hash.get(sid) != hashes[sid]}
            total_in_repo = len(current_ids)
            threshold = max(_CONTENT_HASH_FULL_REBUILD_MIN_DIRTY,
                            int(total_in_repo * _CONTENT_HASH_FULL_REBUILD_DIRTY_RATIO))
            full_rebuild = bool(force) or (not self._index_built)
            if not full_rebuild and dirty_ids and len(dirty_ids) >= threshold:
                full_rebuild = True

            if full_rebuild:
                # 阈值触发/首次/force：整体重建（避免大量单条 numpy 行操作与多次 encode）
                self._reset_index_locked()
                self._drop_native_chroma_ids_locked(current_ids)
                dirty_ids = set(current_ids)
            else:
                # 增量：DELETE + INSERT（同 id 旧向量必须先删，否则 chroma add 会撞 id）
                for sid in sorted(dirty_ids):
                    self._remove_skill_vector_locked(sid)

            if not dirty_ids and self._index_built:
                logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'ensure_indexed.content_unchanged', 'indexed_count': len(self._indexed_skill_ids), 'total_in_repo': total_in_repo, 'incremental_by': 'content_hash'}))
                return len(self._indexed_skill_ids)

            ordered_ids = sorted(dirty_ids)
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'ensure_indexed.building', 'dirty_skill_count': len(ordered_ids), 'already_indexed': len(self._indexed_skill_ids), 'total_in_repo': total_in_repo, 'full_rebuild': full_rebuild, 'rebuild_threshold': threshold, 'incremental_by': 'content_hash'}))

            # 批量构建向量文本并添加（内容文本在哈希阶段已算好，不重复读盘）
            items_to_add = []
            for skill_id in ordered_ids:
                meta = index[skill_id]
                metadata = {
                    "skill_id": skill_id,
                    "name": meta.get("name", skill_id),
                    "description": meta.get("description", ""),
                    "category": meta.get("category", ""),
                    "tags": ",".join(meta.get("tags", []) or []),
                    "enabled": meta.get("enabled", True),
                    "version": meta.get("version", ""),
                }
                items_to_add.append({
                    "content": texts[skill_id],
                    "metadata": metadata,
                    # 用 skill_id 作为稳定 ID，避免重复索引
                    "id": f"skill_{skill_id}",
                })

            if items_to_add:
                if self._st_backend is not None:
                    # BGE-m3 sentence-transformers 模式：自管理向量库
                    try:
                        model, doc_ids, doc_vectors, doc_metas = self._st_backend
                        # 批量编码脏技能的向量
                        contents = [item["content"] for item in items_to_add]
                        # normalize_embeddings=True 让相似度 = 点积
                        new_vectors = model.encode(
                            contents, normalize_embeddings=True,
                            show_progress_bar=False,
                        )
                        import numpy as np
                        if len(doc_vectors) == 0:
                            doc_vectors = new_vectors
                        else:
                            doc_vectors = np.vstack([doc_vectors, new_vectors])
                        for item, vec in zip(items_to_add, new_vectors):
                            sid = item["metadata"]["skill_id"]
                            doc_ids.append(sid)
                            doc_metas.append(item["metadata"])
                            self._indexed_skill_ids.add(sid)
                            # 只有编码+入列都成功才写哈希 ⇒ 失败项下次仍会被重试
                            self._indexed_content_hash[sid] = hashes[sid]
                        # 更新 backend 元组
                        self._st_backend = (model, doc_ids, doc_vectors, doc_metas)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'sentence_transformers.add.failed', 'error': str(e)[:300]}))
                elif self._native_chroma is not None:
                    # chromadb 原生模式：用 collection.add()
                    try:
                        _, collection = self._native_chroma
                        collection.add(
                            ids=[item["id"] for item in items_to_add],
                            documents=[item["content"] for item in items_to_add],
                            metadatas=[item["metadata"] for item in items_to_add],
                        )
                        for skill_id in ordered_ids:
                            self._indexed_skill_ids.add(skill_id)
                            self._indexed_content_hash[skill_id] = hashes[skill_id]
                    except Exception as e:  # noqa: BLE001
                        logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'native_chroma.add.failed', 'error': str(e)[:300]}))
                else:
                    # VectorStore 模式（fallback）
                    try:
                        added_ids = vs.batch_add(items_to_add)
                        # 【C1 修(4)】只把"确实已入列"的技能记为已索引：
                        # batch_add 返回 None（结果未知）时不冒充成功，让下次重试
                        if added_ids is None:
                            logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'vector_store.add.unknown_result', 'expected': len(ordered_ids), 'reason': 'batch_add returned None, entries left unmarked for retry'}))
                        else:
                            for skill_id in ordered_ids[:len(list(added_ids))]:
                                self._indexed_skill_ids.add(skill_id)
                                self._indexed_content_hash[skill_id] = hashes[skill_id]
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"batch_add failed: {e}, partial index may exist")

            self._index_built = True
            count = len(self._indexed_skill_ids)
            backend = 'st_backend' if self._st_backend is not None else 'native_chroma' if self._native_chroma is not None else 'vector_store'

            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'ensure_indexed.done', 'indexed_or_refreshed': len(ordered_ids), 'total_indexed': count, 'total_in_repo': total_in_repo, 'full_rebuild': full_rebuild, 'backend': backend}))

            # 覆盖缺口自检（审计 Q3 §7-E）：索引条数少于源技能数必须可见，
            # 否则"语义层只覆盖一部分技能"会像正常一样沉默（审计 P11 的同源问题）
            missing = sorted(current_ids - self._indexed_skill_ids)
            if missing:
                logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'ensure_indexed.coverage_gap', 'event': 'skill_vector.index.coverage_gap', 'source_count': total_in_repo, 'indexed_count': count, 'missing_count': len(missing), 'missing_sample': missing[:10], 'backend': backend}))

            emit_metric(
                "yunshu_skill_vector_index_count",
                value=count, kind="gauge",
                labels={"success": "true"},
            )
            return count

    # ──────────────────────────────────────────────
    #  增量更新（skill.md 变更钩子触发）
    # ──────────────────────────────────────────────

    def upsert(self, skill_id: str) -> bool:
        """增量更新单个技能的向量（由 SkillFileStore 写入钩子触发）

        策略（DELETE+INSERT 模拟 upsert，参考 project_memory 教训）:
            1. 刷新 meta 索引读最新 skill.md
            2. 若 skill 已被删除 → 清理残留向量后返回 False
            3. 否则删除旧向量 → 调用 ensure_indexed 自动添加新向量

        Args:
            skill_id: 技能 ID

        Returns:
            True 表示成功更新；False 表示后端不可用或 skill 已不存在

        【不易】不破坏 _st_backend 元组结构 (model, doc_ids, doc_vectors, doc_metas)
        【变易】同时支持 _st_backend / _native_chroma 两种后端；VectorStore 模式
               由 ensure_indexed 全量重建兜底
        【简易】复用 ensure_indexed 的添加逻辑，避免重复实现
        """
        vs = self._ensure_vector_store()
        if vs is None:
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'upsert.skipped_no_backend', 'skill_id': skill_id}))
            return False

        logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'upsert.start', 'skill_id': skill_id, 'backend': 'st_backend' if self._st_backend is not None else 'native_chroma' if self._native_chroma is not None else 'vector_store'}))

        # 刷新 meta 索引，确保读到最新 skill.md（钩子由 file_store 锁外触发，
        # 此刻 skill.md 已落盘，load_metadata_index(refresh=True) 可读到最新内容）
        index = self.fs.load_metadata_index(refresh=True)
        if skill_id not in index:
            # skill 已被删除 → 清理索引中的残留向量
            self._remove_skill_vector(skill_id)
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'upsert.skill_deleted_cleaned', 'skill_id': skill_id}))
            return False

        # 先删除旧向量（若存在），再让 ensure_indexed 增量添加新向量
        self._remove_skill_vector(skill_id)
        self.ensure_indexed()

        updated = skill_id in self._indexed_skill_ids
        logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'upsert.' + ('ok' if updated else 'failed'), 'skill_id': skill_id, 'indexed_count': len(self._indexed_skill_ids)}))
        return updated

    def _remove_skill_vector(self, skill_id: str) -> None:
        """从索引中删除单个技能的向量（自行加锁版本）"""
        with self._lock:
            self._remove_skill_vector_locked(skill_id)

    def _remove_skill_vector_locked(self, skill_id: str) -> None:
        """从索引中删除单个技能的向量（DELETE+INSERT 的 DELETE 部分；调用方需持锁）

        三路后端分别处理:
            - _st_backend: numpy 数组 + 列表同步移除
            - _native_chroma: collection.delete(ids=[...])
            - _indexed_skill_ids: discard，让 ensure_indexed 重新添加

        【不易】_st_backend 元组结构不变，只替换 doc_ids/doc_vectors/doc_metas 内容
        【不易】本方法**自身不加锁**：ensure_indexed 已持锁调用它，此处再加锁
                属于无谓重入（self._lock 现为 RLock，重入不会死锁，但保持"谁持锁谁
                负责"的单一职责更易审查）
        【简易】单 skill 移除是 O(N)，N=技能数（百级），可接受
        """
        # _st_backend 模式：numpy 数组 + 列表移除
        if self._st_backend is not None:
            model, doc_ids, doc_vectors, doc_metas = self._st_backend
            if skill_id in doc_ids:
                try:
                    import numpy as np
                    idx = doc_ids.index(skill_id)
                    doc_ids.pop(idx)
                    doc_metas.pop(idx)
                    if len(doc_vectors) > 1:
                        doc_vectors = np.delete(doc_vectors, idx, axis=0)
                    else:
                        # 最后一个：清空为 0 行数组，保留列数
                        doc_vectors = np.empty((0, doc_vectors.shape[1]))
                    self._st_backend = (model, doc_ids, doc_vectors, doc_metas)
                    logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'remove_skill.st_backend.ok', 'skill_id': skill_id, 'remaining_docs': len(doc_ids)}))
                except Exception as e:  # noqa: BLE001
                    logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'remove_skill.st_backend.failed', 'skill_id': skill_id, 'error': str(e)[:200]}))

        # _native_chroma 模式：collection.delete
        if self._native_chroma is not None:
            try:
                _, collection = self._native_chroma
                collection.delete(ids=[f"skill_{skill_id}"])
                logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'remove_skill.native_chroma.ok', 'skill_id': skill_id}))
            except Exception as e:  # noqa: BLE001
                logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'remove_skill.native_chroma.failed', 'skill_id': skill_id, 'error': str(e)[:200]}))

        # 从已索引集合移除（让 ensure_indexed 重新添加）
        self._indexed_skill_ids.discard(skill_id)
        # 【C1 修(4)】内容哈希一并丢弃 ⇒ ensure_indexed 一定会重编码该技能
        self._indexed_content_hash.pop(skill_id, None)

    # ──────────────────────────────────────────────
    #  查询编码
    # ──────────────────────────────────────────────

    def _get_per_key_lock(self, query: str) -> threading.Lock:
        """获取 query 专属锁（per-key lock，避免 Thundering herd）

        【不易】每个 query 有独立锁，不影响不同 query 的并发
        【变易】超容量时清理最旧的锁（仅从字典移除，不影响已持有的锁对象）
        【简易】dict + threading.Lock，无第三方依赖
        """
        with self._lock:
            if query not in self._per_key_locks:
                self._per_key_locks[query] = threading.Lock()
                # 超容量清理：移除最早的锁（不影响已持有的锁对象，仅从字典删除）
                while len(self._per_key_locks) > self._per_key_locks_maxsize:
                    oldest_key = next(iter(self._per_key_locks))
                    self._per_key_locks.pop(oldest_key)
            return self._per_key_locks[query]

    def _encode_query_cached(self, query: str) -> Optional[Any]:
        """编码 query 为归一化向量（带 LRU 缓存 + per-key 锁防 Thundering herd）

        【不易】编码方式与 encode_query 完全一致（normalize_embeddings=True）
        【变易】LRU 缓存高频 query + per-key 锁防 Thundering herd
               多线程同时请求同一未缓存 query 时，仅一个线程执行 model.encode
        【简易】double-checked locking 模式，逻辑清晰

        缓存策略（三阶段）:
        1. 快速路径: self._lock 检查缓存，命中则直接返回
        2. per-key 锁: 未命中时获取 query 专属锁，防止重复推理
        3. double-check: 获取锁后再次检查缓存（可能已被其他线程填充）

        Args:
            query: 用户查询文本
        Returns:
            归一化向量 (dim=1024,)，或 None（后端不可用/编码失败）
        """
        if self._st_backend is None:
            return None

        # ── 阶段 1: 快速路径（缓存命中检查）──
        with self._lock:
            if query in self._query_cache:
                self._query_cache.move_to_end(query)
                self._query_cache_hits += 1
                return self._query_cache[query]
            # 【不易】未命中计数在锁内执行，避免 += 竞态导致计数偏低
            self._query_cache_misses += 1

        # ── 阶段 2: per-key 锁（避免 Thundering herd）──
        # 多线程同时请求同一 query 时，仅第一个线程执行 model.encode
        # 其他线程等待锁释放后走 double-check 直接取缓存
        per_key_lock = self._get_per_key_lock(query)
        with per_key_lock:
            # ── 阶段 3: double-checked locking ──
            # 获取锁后再次检查缓存（可能已被其他线程填充）
            with self._lock:
                if query in self._query_cache:
                    self._query_cache.move_to_end(query)
                    self._thundering_herd_avoided += 1
                    return self._query_cache[query]

            # 仍然未命中：执行 model.encode（持 per-key 锁，不持 self._lock）
            try:
                model, _, _, _ = self._st_backend
                vec = model.encode(
                    [query], normalize_embeddings=True,
                    show_progress_bar=False,
                )[0]

                # 存入缓存（线程安全）
                with self._lock:
                    self._query_cache[query] = vec
                    # LRU 淘汰：超容量时删除最久未使用
                    while len(self._query_cache) > self._query_cache_maxsize:
                        self._query_cache.popitem(last=False)

                return vec
            except Exception as e:  # noqa: BLE001
                logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'encode_query.failed', 'error': str(e)[:300]}))
                return None

    def _invalidate_query_cache(self) -> None:
        """清空 query embedding 缓存 + per-key 锁（模型切换/索引重建时调用）"""
        with self._lock:
            evicted = len(self._query_cache)
            self._query_cache.clear()
            self._per_key_locks.clear()
            self._query_cache_hits = 0
            self._query_cache_misses = 0
            self._thundering_herd_avoided = 0
        if evicted > 0:
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'query_cache.invalidated', 'evicted': evicted}))

    def get_query_cache_stats(self) -> Dict[str, Any]:
        """获取 query embedding 缓存统计（可观测性）"""
        with self._lock:
            total = self._query_cache_hits + self._query_cache_misses
            hit_rate = (self._query_cache_hits / total * 100) if total > 0 else 0.0
            return {
                "size": len(self._query_cache),
                "maxsize": self._query_cache_maxsize,
                "hits": self._query_cache_hits,
                "misses": self._query_cache_misses,
                "hit_rate": round(hit_rate, 2),
                "per_key_locks": len(self._per_key_locks),
                "thundering_herd_avoided": self._thundering_herd_avoided,
            }

    def encode_query(self, query: str) -> Optional[Any]:
        """编码 query 为 BGE-m3 归一化向量（供 NegativeIntentDetector 使用）

        复用 _st_backend 的 SentenceTransformer 模型，编码方式与
        _search_sentence_transformers 完全一致，保证相似度计算无失真。

        【不易】编码方式必须与 search 一致（normalize_embeddings=True）
        【变易】带 LRU 缓存（_encode_query_cached），高频 query 跳过推理
        【简易】委托 _encode_query_cached，单次调用

        Args:
            query: 用户查询文本

        Returns:
            归一化向量 (dim=1024,)，或 None（后端不可用/编码失败）
        """
        return self._encode_query_cached(query)

    # ──────────────────────────────────────────────
    #  搜索
    # ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        enabled_only: bool = True,
        min_score: float = 0.01,
    ) -> List[Dict[str, Any]]:
        """向量搜索 — 返回 Top-K 候选技能

        Args:
            query: 用户意图文本
            top_k: 返回数量
            enabled_only: True 时过滤禁用技能（与 SkillLoader.match 一致）
            min_score: 最低相似度阈值，低于此分数的结果被丢弃
                      默认 0.01 与 SkillLoader.match 保持一致

        Returns:
            [{"skill_id": str, "score": float, "metadata": dict}, ...]
            失败时返回空列表（由调用方决定是否回退 TF-IDF），返回值类型为
            SkillVectorSearchResult（list 的子类）⇒ 旧消费方零改动。

        失败降级策略（每条都带显式降级标记，见【C1 修(3)】）:
            - 后端不可用 / 索引为空 / query 编码失败 / 相似度计算失败 /
              chroma 查询失败 / 超时 / 任意异常 → 返回**空结果 + degraded 标记**
              + 结构化日志 event `skill_vector.search.degraded`
              + 指标 `yunshu_skill_vector_degraded_total{reason}`
            - ChromaDB 搜索失败 → VectorStore 内部自动降级 BM25（本层不改其行为）
            - 负样本启发式命中 → 返回空结果但不标 degraded（这是**有意过滤**，
              不是故障；调用方据 degraded=False + vector_leg_empty=True 区分）

        负样本防御（【变易】针对 BM25 fallback 模式的启发式过滤；【RET-1R】修作用域）:
            - 纯数字 / 过短查询 / 编程关键字 → 不应召回任何技能
            - 这些场景在 ChromaDB 真实向量模式下不会误召回（语义不相似）
            - 但 BM25 字面匹配可能误命中，需要 adapter 层兜底

        【RET-1R · R-2 作用域修复】上述启发式**只在字面匹配兜底后端生效**，分两档。
        改前是一档、对**所有**后端无条件生效：纯 ASCII 英文 query（如
        "pitfalls of adding mocks in tests"）被 `^[a-zA-Z_][a-zA-Z0-9_ ]*$` 一律判为负样本
        ⇒ 向量腿英文召回 1/8（绕开该启发式直接比余弦是 8/8），与本方法及
        `_is_negative_query` 自己的 docstring（"只在 BM25 fallback 模式下生效"）矛盾。

            档 1（与后端无关，懒构建**之前**）：空 / 单字符 / 纯数字符号
                —— 这些 query 在任何后端下都没有合法技能意图，过滤**不损失召回**，
                   且避免垃圾 query 触发 BGE-m3 懒构建（实测 ≈54s）。
            档 2（**只在**字面匹配兜底后端，懒构建**之后**）：纯 ASCII 单词/词组、
                   全编程关键字 —— 见 `_negative_filter_applies()`。它们的"误召回"
                   只在字面匹配（VectorStore 倒排 BM25 / 字符匹配）下发生；真向量
                   后端由语义负责区分，静态过滤只会误伤合法英文 query。

        档 2 放在懒构建**之后**是为了让判据可判定：构建前 `_st_backend`/`_native_chroma`
        都还是 None，判不出后端（那样同一个 query 会"先返回空、建完索引后又返回结果"）。
        判据口径与 loader 对同一个后端的判据**逐字一致**（loader._try_vector_match /
        _try_rrf_match："BM25 fallback is not real vector search"），见 `_negative_filter_applies()`。
        """
        # ── 档 1：与后端无关的负样本（空 / 单字符 / 纯数字符号）──
        # 【RET-1R】为什么这一档可以与后端无关：纯数字/符号/空串没有任何语义，
        # 任何后端下都不构成合法技能检索意图 ⇒ 过滤它不会造成假阴
        #（R-2 的假阴来自"纯 ASCII 英文词"那条，不在本档）。
        if _is_empty_or_symbol_only(query):
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search.negative_query_filtered', 'query': query[:50], 'reason': 'empty / too short / symbol-only (backend independent tier)', 'degraded': False}))
            # 有意过滤（非故障）：degraded=False + vector_leg_empty=True
            return self._result([])

        # 延迟构建索引
        if not self._index_built:
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search.lazy_build_index', 'query': query[:50]}))
            # 【已知残留 · 本卡不改】首次懒构建发生在 _search_with_timeout **之外**，
            # 故 _SEARCH_TIMEOUT_SECONDS 覆盖不到"BGE-m3 加载 + 全量编码"这段耗时
            # （审计 P1 第①条后半）。改它要动 loader/app 的调用点（超出本卡文件范围），
            # 因此这里只把耗时打出来并在超预算时 WARN，避免"名义 2s、实际无界"继续隐形。
            _t_build = time.time()
            self.ensure_indexed()
            _build_ms = (time.time() - _t_build) * 1000.0
            if _build_ms > self._SEARCH_TIMEOUT_SECONDS * 1000.0:
                logger.warning(log_dict({'module_name': 'vector_adapter', 'action': 'search.lazy_build_slow', 'event': 'skill_vector.index.build_slow', 'query': query[:50], 'build_ms': round(_build_ms, 1), 'timeout_seconds': self._SEARCH_TIMEOUT_SECONDS, 'reason': 'lazy index build runs outside the search timeout budget'}))

        # ── 档 2：**只在字面匹配兜底后端**生效的负样本启发式（【RET-1R】R-2 修复点）──
        # 作用域 = 它声称的后端：`_negative_filter_applies()` 为真 ⇔ 活动后端不是真向量
        # （`_st_backend`/`_native_chroma` 都缺 ⇒ 走 `_search_impl` 第 3 档 VectorStore，
        #  内部可降级为倒排索引 BM25 / 字符匹配）。
        # 【不易】真向量后端下**不**做静态过滤：BGE-m3/onnx 语义层自己会区分；
        #        静态过滤会把合法英文 query 一起滤掉（改前实测英文 1/8）。
        if self._negative_filter_applies() and self._is_negative_query(query):
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search.negative_query_filtered', 'query': query[:50], 'reason': 'matched literal-backend negative heuristic', 'backend': self.backend_name, 'degraded': False}))
            # 有意过滤（非故障）：degraded=False + vector_leg_empty=True
            return self._result([])

        vs = self._vector_store
        if vs is None:
            # 【C1 修(3)】不再静默 return []：显式降级出口（审计 P11）
            self._mark_degraded(_DEGRADE_NO_BACKEND, query=query,
                                extra={"reason_detail": "vector backend unavailable"})
            return self._degraded_result(_DEGRADE_NO_BACKEND)

        logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search.start', 'query': query[:50], 'top_k': top_k, 'enabled_only': enabled_only, 'min_score': min_score, 'backend': self.backend_name}))

        # [变易] 2s 超时降级 — 模型 encode/向量计算卡住时返回空列表，
        # 触发外层 SkillLoader 降级 TF-IDF（守任务防御性要求）
        # Windows 兼容：signal.alarm 不适用，用 ThreadPoolExecutor.future.result(timeout=2)
        # 【C1 修(1)】该超时此前被 `with ThreadPoolExecutor` 的 __exit__ 抵消，
        # 现已在 _search_with_timeout 内改为显式 shutdown(wait=False) 真正生效
        # 【C1 修(3)】重置"本次检索"的降级状态：内层查询（索引空/编码失败/相似度失败/
        # chroma 查询失败/VectorStore 失败）会在工作线程内标记，外层据此把"空召回"
        # 带上真实原因返回（而不是一律当成"没有语义匹配"）
        self._reset_search_state(query)
        return self._search_with_timeout(query, top_k, enabled_only, min_score)

    # 向量检索默认超时（秒）— 超过则降级为空列表 → 外层 TF-IDF fallback
    _SEARCH_TIMEOUT_SECONDS = 2.0

    def _search_with_timeout(
        self,
        query: str,
        top_k: int,
        enabled_only: bool,
        min_score: float,
    ) -> "SkillVectorSearchResult":
        """包裹实际查询逻辑，超时降级返回空列表（带显式降级标记）

        【不易】超时不抛异常，返回空列表让外层 SkillLoader 降级 TF-IDF；
                返回类型是 list 的子类（SkillVectorSearchResult），旧消费方零改动
        【变易】超时阈值通过 _SEARCH_TIMEOUT_SECONDS 类属性可配
        【简易】ThreadPoolExecutor + future.result(timeout) 是 Windows 兼容的唯一方案
               （signal.alarm 仅 Unix；线程池超时后工作线程继续跑但结果被丢弃）

        【C1 修(1)】并发语义 —— 为什么不能写 `with ThreadPoolExecutor(...)`：
            with 退出时 __exit__ 会执行 executor.shutdown(wait=True)，而超时分支正是
            由"异常穿出 with 块"触发的 ⇒ 主线程会在 __exit__ 里**等满**那个卡住的
            任务，把"2s 软超时"抵消成"无界等待"（审计 P1 实测）。正确写法见
            agent/skills_mgmt/reranker.py:741-756（同仓库已有先例，作者已注明该陷阱）。
            此处改为手动管理 + finally 里 `shutdown(wait=False)`：
              - 主线程**立即**返回（本卡实测：慢桩 sleep 5s 时 search() < 3s 返回）；
              - 被放弃的工作线程**无法被终止**（Python 线程限制），它会继续跑完，
                期间占用 1 个线程与（若走到编码）per-key 锁；这是有界的资源占用，
                相比"阻塞请求线程无界久"是可接受的取舍；
              - 后台线程若最终成功，只会写入 query 缓存等内部状态，不影响已返回的结果；
              - 超时后退化为**非向量路**：search() 返回空结果 ⇒ loader 回落
                TF-IDF / RRF(tfidf+bm25) 融合（fallback_used=True），语义腿缺失由
                degraded 标记与结构化日志显式化。
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

        t0 = time.time()
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                self._search_impl, query, top_k, enabled_only, min_score,
            )
            results = future.result(timeout=self._SEARCH_TIMEOUT_SECONDS)
            elapsed_ms = (time.time() - t0) * 1000
            # 【C1 修(3)】内层查询在工作线程里标记过降级（索引空/编码失败/相似度失败/
            # chroma 失败/VectorStore 失败）→ 把原因带到返回对象上，不让它变成
            # 一个"看起来只是没匹配到"的空列表
            if not results and self._last_search_state.get("degraded"):
                reason = self._last_search_state.get("reason")
                logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search.done', 'query': query[:50], 'result_count': 0, 'elapsed_ms': round(elapsed_ms, 2), 'top1_skill_id': None, 'top1_score': None, 'backend': self.backend_name, 'degraded': True, 'degrade_reason': reason}))
                return self._degraded_result(reason)
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search.done', 'query': query[:50], 'result_count': len(results), 'elapsed_ms': round(elapsed_ms, 2), 'top1_skill_id': results[0]['skill_id'] if results else None, 'top1_score': round(results[0]['score'], 4) if results else None, 'backend': self.backend_name, 'degraded': self._active_backend_degraded}))
            return self._result(results)
        except FutureTimeout:
            # 尽力取消：仅对"尚未开始执行"的任务生效，运行中的线程无法终止
            future.cancel()
            self._mark_degraded(_DEGRADE_TIMEOUT, query=query, extra={
                "timeout_seconds": self._SEARCH_TIMEOUT_SECONDS,
                "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
            })
            return self._degraded_result(_DEGRADE_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            self._mark_degraded(_DEGRADE_EXCEPTION, query=query,
                                extra={"error": str(e)[:200]})
            return self._degraded_result(_DEGRADE_EXCEPTION)
        finally:
            # wait=False：不等待被放弃的工作线程，避免把软超时抵消成无界阻塞
            executor.shutdown(wait=False)

    def _search_impl(
        self,
        query: str,
        top_k: int,
        enabled_only: bool,
        min_score: float,
    ) -> List[Dict[str, Any]]:
        """实际查询逻辑（无超时保护，由 _search_with_timeout 包裹）

        三路后端按优先级分发:
            1. _st_backend（BGE-m3 + numpy）→ _search_sentence_transformers
            2. _native_chroma（chromadb PersistentClient）→ _search_native_chroma
            3. _vector_store（VectorStore fallback）→ vs.search + 后处理

        【不易】从原 search 方法抽取，逻辑完全等价（向后兼容）
        """
        vs = self._vector_store

        # 三路分发选择日志（排查"为什么走向量却没结果"的关键）
        if self._st_backend is not None:
            backend_chosen = "st_backend"
        elif self._native_chroma is not None:
            backend_chosen = "native_chroma"
        else:
            backend_chosen = "vector_store"
        logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'search_impl.dispatch', 'query': query[:50], 'backend': backend_chosen, 'indexed_count': len(self._indexed_skill_ids)}))

        # ── BGE-m3 sentence-transformers 模式：自管理向量库 ──
        if self._st_backend is not None:
            return self._search_sentence_transformers(
                query, top_k=top_k, enabled_only=enabled_only, min_score=min_score,
            )

        # ── chromadb 原生模式：用 collection.query() ──
        if self._native_chroma is not None:
            return self._search_native_chroma(
                query, top_k=top_k, enabled_only=enabled_only, min_score=min_score,
            )

        # ── VectorStore 模式 ──
        try:
            items = vs.search(query, top_k=top_k * 2)  # 多取一些用于 enabled 过滤
        except Exception as e:  # noqa: BLE001
            # 【C1 修(3)】显式降级出口（原来只有一句含糊的 warning，上层无从判定）
            self._mark_degraded(_DEGRADE_VECTOR_STORE_SEARCH_FAILED, query=query,
                                extra={"error": str(e)[:200]})
            return []

        results: List[Dict[str, Any]] = []
        seen_skills: set = set()
        for item in items:
            metadata = item.metadata or {}
            skill_id = metadata.get("skill_id")
            if not skill_id or skill_id in seen_skills:
                continue

            # enabled_only 过滤（与 SkillLoader.match 保持一致）
            if enabled_only and not metadata.get("enabled", True):
                continue

            # 提取相似度得分（VectorStore 在 metadata 中放 _score）
            score = metadata.get("_score", 0.5)
            # 归一化到 [0, 1] 范围（ChromaDB 距离越小越相似，已转为相似度）
            try:
                score = float(score)
                if score < 0:
                    score = 0.0
                elif score > 1:
                    score = 1.0
            except (TypeError, ValueError):
                score = 0.5

            if score < min_score:
                continue

            results.append({
                "skill_id": skill_id,
                "score": score,
                "metadata": metadata,
            })
            seen_skills.add(skill_id)

            if len(results) >= top_k:
                break

        return results

    def _search_sentence_transformers(
        self,
        query: str,
        *,
        top_k: int,
        enabled_only: bool,
        min_score: float,
    ) -> List[Dict[str, Any]]:
        """BGE-m3 sentence-transformers 模式查询

        使用 numpy 矩阵乘法计算 query 与所有文档的相似度（点积，已归一化）。
        BGE-m3 输出已经是归一化向量，相似度 ∈ [0, 1]。
        """
        if self._st_backend is None:
            self._mark_degraded(_DEGRADE_NO_BACKEND, query=query,
                                extra={"reason_detail": "_st_backend is None"})
            return []
        model, doc_ids, doc_vectors, doc_metas = self._st_backend

        if not doc_ids:
            # 【C1 修(3)】这一条以前**连日志都没有**（审计 Q3 §5.4 第 5 行）
            self._mark_degraded(_DEGRADE_INDEX_EMPTY, query=query, extra={
                "reason_detail": "BGE-m3 backend has no indexed documents",
                "indexed_count": 0,
            })
            return []

        try:
            import numpy as np
            # 【变易】编码 query（带 LRU 缓存，高频 query 跳过 BGE-m3 推理）
            q_vec = self._encode_query_cached(query)
            if q_vec is None:
                self._mark_degraded(_DEGRADE_QUERY_ENCODE_FAILED, query=query)
                return []

            # 计算相似度（点积，已归一化）
            sims = doc_vectors @ q_vec  # (N,)

            # 取 top_k * 3 用于 enabled 过滤
            n_candidates = min(len(sims), top_k * 3)
            top_idx = np.argsort(-sims)[:n_candidates]

            # 相似度计算结果日志（排查"为什么 top1 不是期望 skill"的关键）
            top1_idx = int(top_idx[0]) if len(top_idx) > 0 else -1
            logger.info(log_dict({'module_name': 'vector_adapter', 'action': 'st_backend.sims_computed', 'query': query[:50], 'doc_count': len(doc_ids), 'q_vec_dim': int(q_vec.shape[0]), 'top1_skill_id': doc_ids[top1_idx] if top1_idx >= 0 else None, 'top1_similarity': round(float(sims[top1_idx]), 4) if top1_idx >= 0 else None, 'candidates': n_candidates}))
        except Exception as e:  # noqa: BLE001
            # 【C1 修(3)】相似度计算失败也是降级，不能只留一句 warning
            self._mark_degraded(_DEGRADE_SIMILARITY_FAILED, query=query,
                                extra={"error": str(e)[:200]})
            return []

        results: List[Dict[str, Any]] = []
        seen_skills: set = set()
        for idx in top_idx:
            idx = int(idx)
            skill_id = doc_ids[idx]
            if skill_id in seen_skills:
                continue

            metadata = dict(doc_metas[idx] or {})
            # enabled_only 过滤
            if enabled_only and not metadata.get("enabled", True):
                continue

            similarity = float(sims[idx])
            # BGE-m3 cosine 相似度可能为负，截断到 [0, 1]
            similarity = max(0.0, min(1.0, similarity))

            if similarity < min_score:
                continue

            results.append({
                "skill_id": skill_id,
                "score": similarity,
                "metadata": metadata,
            })
            seen_skills.add(skill_id)

            if len(results) >= top_k:
                break

        return results

    def _search_native_chroma(
        self,
        query: str,
        *,
        top_k: int,
        enabled_only: bool,
        min_score: float,
    ) -> List[Dict[str, Any]]:
        """chromadb 原生 API 查询

        chromadb 返回 distance（越小越相似），需转为相似度分数：
            similarity = 1 - distance / 2  (cosine distance ∈ [0, 2])
        或更直观：
            similarity = max(0, 1 - distance)  (距离 0 = 完全相同 = 1.0)
        """
        if self._native_chroma is None:
            self._mark_degraded(_DEGRADE_NO_BACKEND, query=query,
                                extra={"reason_detail": "_native_chroma is None"})
            return []
        _, collection = self._native_chroma

        try:
            # 多取一些用于 enabled 过滤
            qr = collection.query(
                query_texts=[query],
                n_results=top_k * 3,
                include=["metadatas", "distances"],
            )
        except Exception as e:  # noqa: BLE001
            # 【C1 修(3)】显式降级出口（该路是 BGE-m3 不可用时的落盘旧库）
            self._mark_degraded(_DEGRADE_NATIVE_CHROMA_QUERY_FAILED, query=query,
                                extra={"error": str(e)[:200],
                                       "fallback_store": self._fallback_store.get("path")})
            return []

        if not qr["ids"] or not qr["ids"][0]:
            # 【C1 修(3)】空库以前同样静默；此处显式化并带上覆盖度
            self._mark_degraded(_DEGRADE_NATIVE_CHROMA_EMPTY, query=query, extra={
                "reason_detail": "native_chroma collection returned no ids",
            })
            return []

        ids = qr["ids"][0]
        distances = qr["distances"][0]
        metadatas = qr["metadatas"][0] if qr["metadatas"] and qr["metadatas"][0] else [{}] * len(ids)

        results: List[Dict[str, Any]] = []
        seen_skills: set = set()
        for i, (doc_id, dist, meta) in enumerate(zip(ids, distances, metadatas)):
            metadata = dict(meta or {})
            skill_id = metadata.get("skill_id")
            if not skill_id or skill_id in seen_skills:
                continue

            # enabled_only 过滤
            if enabled_only and not metadata.get("enabled", True):
                continue

            # 距离转相似度：cosine distance ∈ [0, 2]，相似度 = 1 - distance/2
            try:
                dist = float(dist)
                similarity = max(0.0, 1.0 - dist / 2.0)
            except (TypeError, ValueError):
                similarity = 0.0

            if similarity < min_score:
                continue

            results.append({
                "skill_id": skill_id,
                "score": similarity,
                "metadata": metadata,
            })
            seen_skills.add(skill_id)

            if len(results) >= top_k:
                break

        return results

    # ──────────────────────────────────────────────
    #  负样本启发式识别
    # ──────────────────────────────────────────────

    # 【RET-1R · R-2】负样本正则**分两档**，作用域不同（名字即作用域，防止再分叉）：
    #   档 1 `_BACKEND_AGNOSTIC_PATTERNS`：与后端无关（纯数字/符号组合：12345, 1 2 3）
    #   档 2 `_LITERAL_BACKEND_PATTERNS`：**只在字面匹配兜底后端**生效
    #        改前这条正则 `^[a-zA-Z_][a-zA-Z0-9_ ]*$` 对所有后端无条件生效，
    #        把任何"纯 ASCII 字母/数字/空格"的 query 一律当负样本 —— 英文
    #        "pitfalls of adding mocks in tests" 这类**合法查询**被整条过滤，
    #        实测向量腿英文召回 1/8（绕开后 8/8）。
    #        它声称的语义是"看起来像变量名的标识符"，真正需要它的场景只有
    #        字面匹配（倒排 BM25 / 字符匹配）——真向量后端由语义负责区分。
    # 【不易】保守策略：只过滤明确无语义的查询，避免误伤合法短查询
    _BACKEND_AGNOSTIC_PATTERNS = (_SYMBOL_ONLY_RE,)          # 纯数字/符号组合：12345, 1 2 3
    _LITERAL_BACKEND_PATTERNS = (_ASCII_IDENTIFIER_RE,)      # 纯 ASCII 单词/词组：def print_hello
    #: 兼容别名：= 两档之和（`_is_negative_query` 的完整判据）。**调用点必须**
    #: 按档使用：档 2 只在 `_negative_filter_applies()` 为真时调用（见 search()）。
    _NEGATIVE_PATTERNS = _BACKEND_AGNOSTIC_PATTERNS + _LITERAL_BACKEND_PATTERNS

    # 编程关键字（出现这些词的查询通常不是技能检索意图）
    _PROGRAMMING_KEYWORDS = {
        "def", "function", "class", "import", "return", "print",
        "python", "java", "javascript", "c++", "golang", "rust",
        "programming", "coding", "algorithm",
    }

    def _negative_filter_applies(self) -> bool:
        """【RET-1R · R-2】负样本启发式当前是否适用 —— 作用域判据（唯一实现）

        返回 True ⇔ 活动后端是「字面匹配兜底」而非真向量后端，即：
            · `_st_backend is None`（BGE-m3 语义腿未就位）；**且**
            · `_native_chroma is None`（chromadb onnx embedding 未就位）
        ⇒ `_search_impl` 会走第 3 档 `VectorStore`，它内部可降级为倒排索引 BM25 /
          字符匹配（`memory.vector_store.vector_store.VectorStore.search`），
          这才是 `_is_negative_query` docstring 里说的"BM25 fallback 模式"。

        【不易】判据与 **loader 对同一个后端的判据逐字一致**：
            loader._try_vector_match / _try_rrf_match::
                getattr(adapter, "_st_backend", None) is None and
                getattr(adapter, "_native_chroma", None) is None
                → 日志 action "vector.skipped_bm25_fallback" /
                  "rrf.vector.skipped_bm25_fallback"，
                  reason "BM25 fallback is not real vector search"
          两处必须给出同一个答案，否则会出现"loader 认为这条腿不是真向量、
          adapter 却按真向量放行（或反之）"的口径分叉。
        【变易】调用点只有 `search()` 一处（档 2）；改动这里即改动作用域。
        """
        return self._st_backend is None and self._native_chroma is None

    def _is_negative_query(self, query: str) -> bool:
        """识别负样本查询 — 用于**字面匹配兜底后端**的负样本误召回防御

        判定规则（保守策略，逐条短路）:
            1. 空或单字符 → True
            2. 纯数字/符号组合 → True
            3. 纯 ASCII 单词/词组（看起来像标识符/变量名）→ True
            4. 全部 token 都是编程关键字 → True
            5. 其他情况 → False（保留，走正常向量检索）

        【RET-1R】作用域（改前"实现与 docstring 不符"，本卡修的是**实现**）：
            规则 1/2 是"后端无关档"（`_BACKEND_AGNOSTIC_PATTERNS`），因为纯数字/符号
            没有语义，任何后端下过滤都不损失召回 —— `search()` 在懒构建**之前**
            用 `_is_empty_or_symbol_only()` 独立执行这一档。
            规则 3/4 属"字面匹配兜底档"（`_LITERAL_BACKEND_PATTERNS` + 编程关键字），
            **只在 `_negative_filter_applies()` 为真时生效**（`search()` 的档 2）。
        【变易】真向量后端（BGE-m3 / chromadb embedding）下本判据不参与；
                语义层自己区分，静态规则只会误伤合法英文 query（改前英文 1/8）。
        """
        # 档 1（与后端无关）：空 / 单字符 / 纯数字符号 —— 与 search() 的早退档同一实现
        if _is_empty_or_symbol_only(query):
            return True

        q = query.strip()

        # 档 2（只在字面匹配兜底后端生效）：纯 ASCII 单词/词组
        for pattern in self._LITERAL_BACKEND_PATTERNS:
            if pattern.match(q):
                return True

        # 全部 token 都是编程关键字
        tokens = set(q.lower().split())
        if tokens and tokens.issubset(self._PROGRAMMING_KEYWORDS):
            return True

        return False

    # ──────────────────────────────────────────────
    #  状态查询
    # ──────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """向量检索是否可用（后端已初始化且索引已构建）"""
        return self._vector_store is not None and self._index_built

    @property
    def indexed_count(self) -> int:
        """已索引技能数量"""
        return len(self._indexed_skill_ids)

    def health(self) -> Dict[str, Any]:
        """健康检查 — 供上层聚合与运维排查

        【C1 修(3)】补降级出口字段。旧实现只有 vector_available/indexed_count，与工具链
        的 EmbeddingIndex.worker_health()（mode/available/failure_total/retry_exhausted…）
        相比**没有任何降级原因出口**，上层与运维看不出"向量腿已空"或"已落到旧库"。
        新增（全部只读，不改既有字段语义）：
            degraded / degrade_reason      —— 当前是否降级、为什么
            vector_leg_empty               —— 上次检索是否为空召回
            last_search_state              —— 上次检索结局快照（含 reason/backend）
            degrade_counts                 —— reason -> 次数（进程内累计）
            coverage                       —— 索引源/已索引/落盘库条目数快照
            fallback_store                 —— 落盘旧库（native_chroma）的条目数与年龄
        """
        vs_available = self._vector_store is not None
        # 判断实际使用的引擎
        if self._st_backend is not None:
            engine = "sentence_transformers"
        elif self._native_chroma is not None:
            engine = "chromadb_native"
        elif vs_available and hasattr(self._vector_store, "_use_chroma"):
            engine = "chromadb" if self._vector_store._use_chroma else "bm25_fallback"
        else:
            engine = "unknown"
        last_state = self.last_search_state()
        return {
            "vector_available": vs_available,
            "engine": engine,
            "backend": self.backend_name,
            "indexed_count": self.indexed_count,
            "collection_name": self.collection_name,
            "model_name": self.model_name if self._native_chroma is None else "all-MiniLM-L6-v2 (onnx)",
            # ── 降级出口 ──
            "degraded": self.degraded,
            "degrade_reason": self._active_degrade_reason or last_state.get("reason"),
            "vector_leg_empty": bool(last_state.get("degraded")) or self.indexed_count == 0,
            "last_search_state": last_state,
            "degrade_counts": dict(self._degrade_counts),
            "coverage": self._coverage_snapshot(),
            "fallback_store": dict(self._fallback_store),
        }
