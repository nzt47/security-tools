"""工具混合检索器 — BM25 + Embedding 双路融合

【不易】
  - 复用 tool_router.TOOL_ALIASES 合并 + 优先级去重 + 25 上限逻辑
    (通过 _apply_alias_merge_and_priority_sort helper)
  - 复用 memory/vector_store 的 HAS_SENTENCE_TRANSFORMERS 延迟检测机制
  - 任何异常都返回 None,让调用方回退到 get_tools_for_input(关键词分类)
  - 不破坏 workflow_learning/matcher.py 的 TF-IDF 索引(独立模块)
【变易】
  - alpha 可配,默认 0.5(BM25 与 Embedding 等权)
  - 索引重建:工具 YAML 变更时,通过 sync_tool_index.py 重生成 tool_index.json,
    HybridRetriever 重新加载即可
【简易】
  - EmbeddingIndex 用 SentenceTransformer 直连,内存存 numpy 数组(80×384≈122KB)
    偏离字面「复用 VectorStore」:VectorStore.search() 不返回分数(融合必需),
    .add() 自动生成 mem_ID 不支持工具名作主键。复用其延迟检测机制即可。
  - 降级链清晰:Hybrid → 纯 BM25 → None(调用方回退到关键词分类)

性能预算(80 工具):
  - 模型加载 ~2-3 秒(后台 daemon thread,不阻塞)
  - Query 编码 ~10-20ms + BM25 <1ms + 余弦相似度 <1ms = <25ms(满足 50ms)

原生崩溃隔离:
  - torch/SentenceTransformer 在部分环境(Windows 0xC0000005 / Linux SIGILL)
    加载模型时会触发原生访问违规,Python try/except 无法捕获。
  - 解决方案:子进程探测 + 结果缓存。探测在子进程运行,崩溃不影响主进程。
  - 探测结果缓存到 data/.embedding_probe,后续启动直接读取,无需重复探测。
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  路径与默认配置
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_PATH = os.path.join(_PROJECT_ROOT, "data", "tool_index.json")
_PROBE_CACHE = os.path.join(_PROJECT_ROOT, "data", ".embedding_probe")

# 与 memory/vector_store/vector_store.py L277 一致(多语言 MiniLM,384 维)
_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

_DEFAULT_ALPHA = 0.5      # BM25 与 Embedding 等权
_DEFAULT_TOP_K = 10       # 默认返回 10 个候选
_COSINE_CUTOFF = 0.2      # Embedding 余弦相似度剪枝阈值(低于此值不进入融合)
_PROBE_TIMEOUT = 60       # 子进程探测超时(秒)

# 安全导入 ToolTraceRecorder(不可用时降级)
try:
    from agent.observability.tool_trace import ToolTraceRecorder
except ImportError:
    ToolTraceRecorder = None  # type: ignore[assignment]

# 安全导入 helper(不可用时 hybrid 不可用)
try:
    from agent.tool_router import _apply_alias_merge_and_priority_sort, TOOL_CATEGORIES
    _HELPER_AVAILABLE = True
except ImportError:
    _HELPER_AVAILABLE = False
    _apply_alias_merge_and_priority_sort = None  # type: ignore[assignment]
    TOOL_CATEGORIES = {}  # type: ignore[assignment]

# 安全导入 numpy(EmbeddingIndex 必需)
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ════════════════════════════════════════════════════════════
#  SentenceTransformer 可用性探测(子进程隔离 + 结果缓存)
# ════════════════════════════════════════════════════════════

# 模块级状态:None=未探测, True=可用, False=不可用
_PROBE_RESULT: Optional[bool] = None
_PROBE_LOCK = threading.Lock()


def _read_probe_cache() -> Optional[bool]:
    """读取持久化的探测结果缓存"""
    try:
        if os.path.exists(_PROBE_CACHE):
            with open(_PROBE_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "available" in data:
                return bool(data["available"])
    except Exception:
        pass
    return None


def _write_probe_cache(available: bool) -> None:
    """写入探测结果缓存"""
    try:
        os.makedirs(os.path.dirname(_PROBE_CACHE), exist_ok=True)
        with open(_PROBE_CACHE, "w", encoding="utf-8") as f:
            json.dump({"available": available, "probed_at": time.time()}, f)
    except Exception:
        pass


def _run_embedding_probe(model_name: str) -> bool:
    """在子进程中探测 SentenceTransformer 模型加载是否安全

    Why: torch 在部分环境(Windows 0xC0000005 / Linux SIGILL)加载模型时
         触发原生访问违规,Python try/except 无法捕获,会终止整个进程。
         子进程隔离确保主进程不受影响。

    Returns:
        True=模型可安全加载; False=加载失败或崩溃
    """
    probe_script = (
        "import sys; "
        f"from sentence_transformers import SentenceTransformer; "
        f"m = SentenceTransformer({model_name!r}); "
        "m.encode(['probe test']); "
        "print('PROBE_OK')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe_script],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            cwd=_PROJECT_ROOT,
        )
        if result.returncode == 0 and "PROBE_OK" in (result.stdout or ""):
            return True
        # 非零退出(含崩溃)或输出不含 PROBE_OK
        logger.warning(
            "[tool_router_hybrid] Embedding 探测失败(退出码 %d): %s",
            result.returncode,
            (result.stderr or "")[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[tool_router_hybrid] Embedding 探测超时(%ds)", _PROBE_TIMEOUT)
        return False
    except Exception as e:
        logger.warning("[tool_router_hybrid] Embedding 探测异常: %s", e)
        return False


def _ensure_st_checked() -> bool:
    """检测 sentence_transformers + 模型加载是否安全可用(子进程探测 + 缓存)

    优先级:
      1. 环境变量 AGENT_HYBRID_EMBEDDING 强制覆盖(0=禁用, 1=启用)
      2. 内存缓存(_PROBE_RESULT)
      3. 文件缓存(data/.embedding_probe)
      4. 子进程探测(首次或缓存失效时)
    """
    global _PROBE_RESULT
    if _PROBE_RESULT is not None:
        return _PROBE_RESULT

    with _PROBE_LOCK:
        if _PROBE_RESULT is not None:
            return _PROBE_RESULT

        # 1. 环境变量强制覆盖
        env_val = os.environ.get("AGENT_HYBRID_EMBEDDING", "").strip().lower()
        if env_val in ("0", "false", "no", "off"):
            _PROBE_RESULT = False
            logger.info("[tool_router_hybrid] AGENT_HYBRID_EMBEDDING=0,禁用 Embedding(纯 BM25)")
            return False
        if env_val in ("1", "true", "yes", "on"):
            _PROBE_RESULT = True
            logger.info("[tool_router_hybrid] AGENT_HYBRID_EMBEDDING=1,强制启用 Embedding")
            return True

        # 2. 文件缓存
        cached = _read_probe_cache()
        if cached is not None:
            _PROBE_RESULT = cached
            logger.info(
                "[tool_router_hybrid] Embedding 探测结果(缓存): available=%s", cached
            )
            return cached

        # 3. 子进程探测
        logger.info("[tool_router_hybrid] 首次启动,子进程探测 Embedding 可用性...")
        available = _run_embedding_probe(_DEFAULT_MODEL)
        _PROBE_RESULT = available
        _write_probe_cache(available)
        if not available:
            logger.warning(
                "[tool_router_hybrid] Embedding 不可用,降级到纯 BM25(缓存已写入 %s)",
                _PROBE_CACHE,
            )
        return available


# ════════════════════════════════════════════════════════════
#  分词器(借鉴 workflow_learning/matcher.py:27,CJK+英文混合)
# ════════════════════════════════════════════════════════════

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """CJK 单字 + 英文单词混合分词

    Why: vector_store.InvertedIndex 的分词器只认 [a-zA-Z]{3,},不适合中文工具描述。
         借鉴 workflow_learning/matcher.py 的 CJK+英文混合分词模式。
    """
    return _TOKEN_RE.findall((text or "").lower())


# ════════════════════════════════════════════════════════════
#  BM25Index — 倒排索引 + BM25 评分
# ════════════════════════════════════════════════════════════


class BM25Index:
    """BM25 倒排索引 — 索引工具 name + parameter_names + description

    【不易】BM25 算法参数 k1=1.5, b=0.75 与 vector_store.InvertedIndex 一致
    【变易】CJK+英文混合分词,支持中文工具描述检索
    【简易】纯内存倒排表,RLock 保护并发读写
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        # term -> [(doc_id, term_freq), ...]
        self._index: dict[str, list[tuple[str, int]]] = {}
        self._doc_lengths: dict[str, int] = {}  # doc_id -> token count
        self._total_docs = 0
        self._avg_doc_length = 0.0
        self._lock = threading.RLock()

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档到索引(doc_id 重复时覆盖旧文档)"""
        tokens = _tokenize(content)
        term_counts: dict[str, int] = {}
        for token in tokens:
            term_counts[token] = term_counts.get(token, 0) + 1

        with self._lock:
            # 覆盖语义:先移除旧文档(若存在)
            if doc_id in self._doc_lengths:
                self._remove_document_locked(doc_id)

            for term, freq in term_counts.items():
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append((doc_id, freq))

            self._doc_lengths[doc_id] = len(tokens)
            self._total_docs += 1
            total_length = sum(self._doc_lengths.values())
            self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0

    def _remove_document_locked(self, doc_id: str) -> None:
        """从索引移除文档(调用方持锁)"""
        if doc_id not in self._doc_lengths:
            return
        for term in list(self._index.keys()):
            self._index[term] = [(did, freq) for did, freq in self._index[term] if did != doc_id]
            if not self._index[term]:
                del self._index[term]
        del self._doc_lengths[doc_id]
        self._total_docs -= 1
        if self._total_docs > 0:
            total_length = sum(self._doc_lengths.values())
            self._avg_doc_length = total_length / self._total_docs
        else:
            self._avg_doc_length = 0.0

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询,返回 [(doc_id, score)] 列表(按分数降序)"""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: dict[str, float] = {}
        with self._lock:
            for token in query_tokens:
                if token not in self._index:
                    continue
                for doc_id, freq in self._index[token]:
                    doc_length = self._doc_lengths.get(doc_id, 0)
                    if doc_length > 0:
                        scores[doc_id] = scores.get(doc_id, 0.0) + self._compute_bm25(
                            token, freq, doc_length
                        )

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _compute_bm25(self, term: str, term_freq: int, doc_length: int) -> float:
        """计算 BM25 评分(与 vector_store.InvertedIndex._compute_bm25 一致)"""
        if term not in self._index:
            return 0.0
        doc_count = len(self._index[term])
        idf = (self._total_docs - doc_count + 0.5) / (doc_count + 0.5)
        if idf <= 0:
            return 0.0
        numerator = term_freq * (self._k1 + 1)
        denominator = term_freq + self._k1 * (
            1 - self._b + self._b * doc_length / (self._avg_doc_length or 1)
        )
        return idf * numerator / denominator

    def clear(self) -> None:
        """清空索引"""
        with self._lock:
            self._index.clear()
            self._doc_lengths.clear()
            self._total_docs = 0
            self._avg_doc_length = 0.0

    @property
    def size(self) -> int:
        """已索引文档数"""
        with self._lock:
            return self._total_docs


# ════════════════════════════════════════════════════════════
#  EmbeddingIndex — SentenceTransformer 语义索引
# ════════════════════════════════════════════════════════════


class EmbeddingIndex:
    """SentenceTransformer 语义索引 — 索引工具 description

    【不易】模型加载失败时 available=False,hybrid 降级到纯 BM25
    【变易】延迟加载:首次 search 时才加载模型 + 编码文档
    【简易】内存存 numpy 数组(80×384≈122KB),余弦相似度用 numpy 矩阵乘
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL):
        self._model_name = model_name
        self._model = None  # 延迟加载
        self._doc_ids: list[str] = []  # doc_id 列表(与 embeddings 行对齐)
        self._embeddings = None  # numpy 数组 (N, dim)
        self._pending: list[tuple[str, str]] = []  # [(doc_id, content)] 待编码
        self._lock = threading.RLock()
        self._load_failed = False  # 加载失败标志(避免重复尝试)

    @property
    def available(self) -> bool:
        """模型已加载且有已编码文档"""
        with self._lock:
            return self._model is not None and self._embeddings is not None and len(self._doc_ids) > 0

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档(延迟编码,首次 search 时统一编码)"""
        with self._lock:
            # 覆盖语义:若 doc_id 已存在,先移除
            if doc_id in self._doc_ids:
                idx = self._doc_ids.index(doc_id)
                self._doc_ids.pop(idx)
                if self._embeddings is not None:
                    self._embeddings = np.delete(self._embeddings, idx, axis=0)
            # 同时移除 pending 中的旧条目(避免重复编码同一 doc_id)
            self._pending = [(d, c) for d, c in self._pending if d != doc_id]
            self._pending.append((doc_id, content))

    def _ensure_model(self) -> bool:
        """加载模型 + 编码所有 pending 文档。成功返回 True。"""
        with self._lock:
            if self._model is not None:
                # 模型已加载,只需编码 pending
                if self._pending:
                    self._encode_pending_locked()
                return True

            if self._load_failed:
                return False  # 避免重复尝试加载

            # 检测 sentence_transformers 可用性
            if not _ensure_st_checked():
                self._load_failed = True
                return False

            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
                logger.info(
                    "[tool_router_hybrid] SentenceTransformer 加载成功: %s",
                    self._model_name,
                )
                # 编码 pending 文档
                if self._pending:
                    self._encode_pending_locked()
                return True
            except Exception as e:
                logger.warning(
                    "[tool_router_hybrid] SentenceTransformer 加载失败: %s(降级到纯 BM25)", e
                )
                self._load_failed = True
                self._model = None
                return False

    def _encode_pending_locked(self) -> None:
        """编码所有 pending 文档(调用方持锁)"""
        if not self._pending or self._model is None:
            return
        try:
            contents = [c for _, c in self._pending]
            new_embeddings = self._model.encode(contents, show_progress_bar=False)
            new_embeddings = np.array(new_embeddings, dtype=np.float32)

            if self._embeddings is None:
                self._embeddings = new_embeddings
                self._doc_ids = [d for d, _ in self._pending]
            else:
                self._embeddings = np.vstack([self._embeddings, new_embeddings])
                self._doc_ids.extend(d for d, _ in self._pending)
            self._pending.clear()
        except Exception as e:
            logger.warning("[tool_router_hybrid] 文档编码失败: %s", e)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询,返回 [(doc_id, cosine_similarity)] 列表(按相似度降序)"""
        if not _HAS_NUMPY:
            return []
        if not self._ensure_model():
            return []
        with self._lock:
            if self._embeddings is None or len(self._doc_ids) == 0:
                return []
            try:
                query_emb = self._model.encode([query], show_progress_bar=False)
                query_emb = np.array(query_emb, dtype=np.float32)[0]  # (dim,)

                # 余弦相似度 = dot(a, b) / (|a| * |b|)
                # 矩阵化:embeddings (N, dim) @ query (dim,) / (norms * query_norm)
                norms = np.linalg.norm(self._embeddings, axis=1)  # (N,)
                query_norm = np.linalg.norm(query_emb)  # scalar
                if query_norm < 1e-9:
                    return []
                denom = norms * query_norm
                # 避免除零
                denom = np.where(denom < 1e-9, 1e-9, denom)
                sims = self._embeddings @ query_emb / denom  # (N,)

                # 按相似度降序取 top_k
                top_indices = np.argsort(-sims)[:top_k]
                return [(self._doc_ids[i], float(sims[i])) for i in top_indices]
            except Exception as e:
                logger.warning("[tool_router_hybrid] Embedding 搜索失败: %s", e)
                return []

    def clear(self) -> None:
        """清空索引(不卸载模型,避免重复加载)"""
        with self._lock:
            self._doc_ids.clear()
            self._embeddings = None
            self._pending.clear()

    def preheat(self) -> None:
        """预热:加载模型 + 编码所有 pending 文档(后台线程调用)"""
        try:
            self._ensure_model()
        except Exception as e:
            logger.warning("[tool_router_hybrid] 预热失败: %s", e)


# ════════════════════════════════════════════════════════════
#  HybridRetriever — BM25 + Embedding 分数融合
# ════════════════════════════════════════════════════════════


def _min_max_normalize(scores: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """min-max 归一化到 [0,1]

    Why: BM25 分数无界,Embedding 余弦在 [-1,1],需归一化才能融合。
         min-max 保留"最高分=1,最低分=0"语义,多候选时能拉开差距。
    """
    if not scores:
        return []
    values = [s for _, s in scores]
    min_v, max_v = min(values), max(values)
    if max_v - min_v < 1e-9:
        # 所有分数相同:归一化为 1.0(避免除零,保留候选)
        return [(doc_id, 1.0) for doc_id, _ in scores]
    return [(doc_id, (v - min_v) / (max_v - min_v)) for doc_id, v in scores]


class HybridRetriever:
    """混合检索器 — BM25 + Embedding 分数融合

    【不易】单例 + 双重检查锁,线程安全
    【变易】alpha 可配,候选合并后过 TOOL_ALIASES 合并 + 优先级去重 + 25 上限
    【简易】查询路径 <25ms,后台 daemon thread 预热 EmbeddingIndex
    """

    def __init__(
        self,
        alpha: float = _DEFAULT_ALPHA,
        index_path: str = _INDEX_PATH,
    ):
        self._alpha = alpha
        self._index_path = index_path
        self._bm25 = BM25Index()
        self._embedding = EmbeddingIndex()
        self._lock = threading.RLock()
        self._tools_loaded = False
        self._all_categories: set = set()
        # 上次查询的中间统计(bm25/embed/fused 召回数),供 hybrid_select_tools 读取
        self._last_query_stats: dict = {}

        # 加载工具定义并构建双索引
        self._load_and_build_index()

        # 启动后台 daemon thread 预热 EmbeddingIndex
        if self._tools_loaded and self._embedding is not None:
            t = threading.Thread(
                target=self._embedding.preheat,
                name="hybrid-embedding-preheat",
                daemon=True,
            )
            t.start()

    def _load_and_build_index(self) -> None:
        """从 tool_index.json 加载工具定义,构建 BM25 + Embedding 双索引"""
        if not os.path.exists(self._index_path):
            logger.warning(
                "[tool_router_hybrid] tool_index.json 不存在: %s(hybrid 不可用)",
                self._index_path,
            )
            return

        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        except Exception as e:
            logger.warning("[tool_router_hybrid] tool_index.json 加载失败: %s", e)
            return

        tools = index_data.get("tools", [])
        if not tools:
            logger.warning("[tool_router_hybrid] tool_index.json 无工具定义")
            return

        # 收集所有类别(用于 helper 优先级查询)
        if TOOL_CATEGORIES:
            self._all_categories = set(TOOL_CATEGORIES.keys())

        self.rebuild(tools)
        self._tools_loaded = True
        logger.info(
            "[tool_router_hybrid] 索引构建完成: %d 个工具(BM25=%d, Embedding pending=%d)",
            len(tools),
            self._bm25.size,
            len(self._embedding._pending) if self._embedding._pending else 0,
        )

    def rebuild(self, tools: list[dict]) -> None:
        """重建双索引

        Args:
            tools: 工具定义列表,每项含 name/description/parameter_names(可选)
        """
        with self._lock:
            self._bm25.clear()
            self._embedding.clear()
            for tool in tools:
                name = tool.get("name", "")
                if not name:
                    continue
                description = tool.get("description", "")
                # parameter_names 可能缺失(旧索引),兜底为空列表
                param_names = tool.get("parameter_names", []) or []
                if not isinstance(param_names, list):
                    param_names = []

                # BM25 索引内容:name + parameter_names + description
                bm25_content = name + " " + " ".join(param_names) + " " + description
                self._bm25.add_document(name, bm25_content)

                # Embedding 索引内容:description(语义匹配)
                self._embedding.add_document(name, description)

    @property
    def available(self) -> bool:
        """BM25 必须可用,Embedding 可选"""
        return self._tools_loaded and self._bm25.size > 0

    def query(
        self,
        text: str,
        top_k: int = _DEFAULT_TOP_K,
    ) -> Optional[list[tuple[str, float]]]:
        """混合检索:BM25 + Embedding 分数融合

        Args:
            text: 查询文本
            top_k: 返回候选数

        Returns:
            [(tool_name, fused_score)] 列表(按分数降序);None 表示检索失败
        """
        if not text or not text.strip():
            return []
        if not self.available:
            return None

        # 重建期间不阻塞查询:try acquire,失败返回 None
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._query_locked(text, top_k)
        except Exception as e:
            logger.warning("[tool_router_hybrid] 查询异常: %s", e)
            return None
        finally:
            self._lock.release()

    def _query_locked(self, text: str, top_k: int) -> list[tuple[str, float]]:
        """执行查询(调用方持锁)"""
        # 候选扩展:取 top_k*2 避免融合后丢失相关结果
        candidate_k = max(top_k * 2, top_k + 5)
        degraded = not self._embedding.available

        # [logger] 查询开始:打印 query + 参数 + 降级标志(排查退化问题用)
        logger.info(
            "[tool_router_hybrid] query 开始: text=%r top_k=%d candidate_k=%d degraded=%s alpha=%.2f",
            text, top_k, candidate_k, degraded, self._alpha,
        )

        # BM25 检索
        bm25_results = self._bm25.search(text, top_k=candidate_k)
        bm25_norm = _min_max_normalize(bm25_results)

        # [logger] BM25 召回结果 top-5(排查召回缺失型退化)
        logger.info(
            "[tool_router_hybrid] BM25 召回: total=%d top5=%s",
            len(bm25_results),
            [(d, round(s, 4)) for d, s in bm25_results[:5]],
        )

        # Embedding 检索(可选)
        embed_results: list[tuple[str, float]] = []
        embed_norm: list[tuple[str, float]] = []
        if self._embedding.available:
            embed_results = self._embedding.search(text, top_k=candidate_k)
            # cosine 剪枝:低于阈值的候选不进入融合
            embed_results = [(d, s) for d, s in embed_results if s >= _COSINE_CUTOFF]
            embed_norm = _min_max_normalize(embed_results)

            # [logger] Embedding 召回结果 top-5(排查 Embedding 路径退化)
            logger.info(
                "[tool_router_hybrid] Embedding 召回: total=%d top5=%s",
                len(embed_results),
                [(d, round(s, 4)) for d, s in embed_results[:5]],
            )

        # 分数融合
        all_candidates: set[str] = set()
        all_candidates.update(d for d, _ in bm25_norm)
        all_candidates.update(d for d, _ in embed_norm)

        # 记录中间统计(供 hybrid_select_tools 写入 trace)
        self._last_query_stats = {
            "bm25_candidates": len(bm25_results),
            "embed_candidates": len(embed_results),
            "fused_candidates": len(all_candidates),
        }

        bm25_map = dict(bm25_norm)
        embed_map = dict(embed_norm)

        fused: list[tuple[str, float]] = []
        for doc_id in all_candidates:
            bm25_score = bm25_map.get(doc_id, 0.0)
            embed_score = embed_map.get(doc_id, 0.0)
            # 若 Embedding 不可用,只用 BM25(alpha=1.0 等效)
            if not self._embedding.available or not embed_norm:
                final = bm25_score
            else:
                final = self._alpha * bm25_score + (1 - self._alpha) * embed_score
            fused.append((doc_id, final))

        fused.sort(key=lambda x: x[1], reverse=True)

        # [logger] 融合结果 top-5(最终返回,排查整体退化)
        logger.info(
            "[tool_router_hybrid] 融合结果: total=%d top5=%s",
            len(fused),
            [(d, round(s, 4)) for d, s in fused[:5]],
        )

        return fused[:top_k]

    @property
    def degraded(self) -> bool:
        """是否降级到纯 BM25(Embedding 不可用)"""
        return self._tools_loaded and not self._embedding.available


# ════════════════════════════════════════════════════════════
#  模块级单例 + 公共入口
# ════════════════════════════════════════════════════════════

_hybrid_instance: Optional[HybridRetriever] = None
_hybrid_lock = threading.Lock()


def get_hybrid_retriever() -> Optional[HybridRetriever]:
    """获取 HybridRetriever 单例(双重检查锁,线程安全)

    Returns:
        HybridRetriever 实例;初始化失败返回 None
    """
    global _hybrid_instance
    if _hybrid_instance is not None:
        return _hybrid_instance
    with _hybrid_lock:
        if _hybrid_instance is not None:
            return _hybrid_instance
        try:
            _hybrid_instance = HybridRetriever()
        except Exception as e:
            logger.warning("[tool_router_hybrid] HybridRetriever 初始化失败: %s", e)
            _hybrid_instance = None
        return _hybrid_instance


def reset_hybrid_retriever() -> None:
    """重置单例(测试用)

    Why: 测试间需隔离单例状态,避免索引残留
    """
    global _hybrid_instance
    with _hybrid_lock:
        _hybrid_instance = None


def hybrid_select_tools(
    user_input: str,
    enabled_whitelist: Optional[list[str]] = None,
    max_tools: int = 25,
    top_k: int = _DEFAULT_TOP_K,
    alpha: float = _DEFAULT_ALPHA,
) -> Optional[list[str]]:
    """混合检索选择工具 — 失败返回 None 让调用方回退

    【不易】任何异常都返回 None,让调用方回退到 get_tools_for_input(关键词分类)
    【变易】alpha 可配,默认 0.5;top_k 默认 10
    【简易】调用方 1 行改造:`hybrid_select_tools(...) or get_tools_for_input(...)`

    Args:
        user_input: 用户原始输入文本
        enabled_whitelist: 启用工具白名单,None 表示不限制
        max_tools: 返回工具数上限,默认 25
        top_k: 检索候选数,默认 10
        alpha: BM25/Embedding 融合权重,默认 0.5

    Returns:
        排序+截断后的工具名列表;None 表示本次未启用/检索失败/无候选
    """
    # helper 不可用 → 直接返回 None
    if not _HELPER_AVAILABLE:
        return None

    retriever = get_hybrid_retriever()
    if retriever is None or not retriever.available:
        return None

    start_time = time.perf_counter()
    bm25_count = 0
    embed_count = 0
    fused_count = 0
    degraded = retriever.degraded
    tools_preview: list[str] = []

    try:
        # 覆盖 alpha(若调用方指定了非默认值)
        if alpha != _DEFAULT_ALPHA:
            retriever._alpha = alpha

        results = retriever.query(user_input, top_k=top_k)
        if results is None:
            return None
        if not results:
            return None  # 空结果让调用方回退

        # 候选工具集合
        selected: set[str] = {tool_name for tool_name, _ in results}

        # 统计从 HybridRetriever._query_locked 写入的中间统计读取
        # Why: results 是融合后 top_k,无法反映 BM25/Embedding 各自召回数;
        #      HybridRetriever._query_locked 在融合前已记录到 _last_query_stats
        stats = getattr(retriever, "_last_query_stats", {}) or {}
        bm25_count = int(stats.get("bm25_candidates", 0))
        embed_count = int(stats.get("embed_candidates", 0))
        fused_count = int(stats.get("fused_candidates", 0))

        # 白名单交集
        if enabled_whitelist is not None:
            whitelist_set = set(enabled_whitelist)
            selected &= whitelist_set
            if not selected:
                return None  # 白名单过滤后无候选,让调用方回退

        # 别名合并 + 优先级排序 + 数量截断(复用 tool_router helper)
        # 传入所有类别,确保每个工具取到正确 priority
        categories = retriever._all_categories or set(TOOL_CATEGORIES.keys())
        result = _apply_alias_merge_and_priority_sort(selected, categories, max_tools)

        if not result:
            return None

        tools_preview = result[:10]
        return result
    except Exception as e:
        logger.warning("[tool_router_hybrid] hybrid_select_tools 异常: %s", e)
        return None
    finally:
        # 记录检索指标(安全降级:recorder 不可用不影响主路径)
        latency_ms = (time.perf_counter() - start_time) * 1000
        if ToolTraceRecorder is not None:
            try:
                ToolTraceRecorder.instance().record_tool_retrieval(
                    query=user_input,
                    top_k=top_k,
                    latency_ms=latency_ms,
                    bm25_candidates=bm25_count,
                    embed_candidates=embed_count,
                    fused_candidates=fused_count,
                    alpha=alpha,
                    degraded=degraded,
                    tools_preview=tools_preview,
                )
            except Exception:
                pass


__all__ = [
    "BM25Index",
    "EmbeddingIndex",
    "HybridRetriever",
    "get_hybrid_retriever",
    "reset_hybrid_retriever",
    "hybrid_select_tools",
]
