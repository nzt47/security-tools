"""
向量存储模块 - 基于ChromaDB的语义检索增强版（已整合优化版本）

支持真正的语义向量搜索和知识管理。
提供向后兼容接口，默认使用 ChromaDB 实现。

整合来源:
- vector_store_optimized.py  → LRU查询缓存
- vector_store_optimized_v2.py → 倒排索引 + BM25评分 + 异步查询 + 批量操作

功能：
- ChromaDB 语义搜索（首选）
- JSON Fallback + 倒排索引 + BM25 关键词搜索（次选）
- LRU 查询缓存（所有搜索路径共享）
- 批量添加、ID 查找、异步搜索
"""

import os
import sys
import json
import re
import heapq
import asyncio
import subprocess
import threading
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import OrderedDict, defaultdict

logger = logging.getLogger(__name__)

# BM25 参数（环境变量可配置，启动时一次性读取）
# Why: b=0.75 默认值对短文档虚高（短/长得分比 1.67x），降至 0.5 缓解（1.40x）；
# 保留可配置性供调优，可通过 BM25_B=0.75 回滚原行为
_DEFAULT_K1 = float(os.environ.get("BM25_K1", "1.5"))
_DEFAULT_B = float(os.environ.get("BM25_B", "0.5"))

# 延迟导入：chromadb / sentence_transformers（→ torch）是重量级依赖，
# CI 上首次导入可能需要 2-3 分钟。模块导入时不加载，仅在首次实例化 VectorStore 时检测。
# 通过 _check_chroma_available() 更新这两个标志。
HAS_CHROMA = False
HAS_SENTENCE_TRANSFORMERS = False

_chroma_deps_checked = False

# 【变易】依赖导入超时(秒)。chromadb 1.5.9 + pydantic 2.x 在部分环境 import 会
# 长时间卡死(非异常,try/except ImportError 无法拦截);sentence_transformers → torch
# 在 CI 上首次导入也可能 2-3 分钟。均用 daemon 线程 + join(timeout) 兜底降级。
_DEPS_IMPORT_TIMEOUT = 30.0


def _probe_import(code: str) -> bool:
    """在子进程中执行依赖导入探测，超时按"不可用"处理

    Why: chromadb 1.5.9 + pydantic 2.x 在部分环境 import 会长时间卡死（非异常，
    try/except ImportError 无法拦截）。早期用 daemon 线程 + join(timeout) 兜底，
    但卡死的 daemon 线程持有全局 import 锁——超时返回后主线程任何后续 import
    都会死锁（VectorStore 构造挂起的根因）。子进程隔离：卡死只发生在子进程，
    subprocess.run 超时后直接 terminate，不影响主进程 import 锁。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            timeout=_DEPS_IMPORT_TIMEOUT,
            capture_output=True,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _check_chroma_available():
    """延迟检测 chromadb + sentence_transformers 是否可用

    避免模块导入时拉起 torch/chromadb 等重量级依赖（CI 上首次导入 torch 可能需要 2-3 分钟）。
    首次调用时执行子进程探测，后续调用直接返回。检测结果会更新模块级
    HAS_CHROMA / HAS_SENTENCE_TRANSFORMERS 标志。
    """
    global HAS_CHROMA, HAS_SENTENCE_TRANSFORMERS, _chroma_deps_checked
    if _chroma_deps_checked:
        return
    _chroma_deps_checked = True

    if _probe_import("import chromadb; from chromadb.config import Settings"):
        HAS_CHROMA = True
        logger.info("[OK] ChromaDB loaded")
    else:
        logger.warning("[WARN] ChromaDB not installed or import timeout, using JSON fallback")

    if _probe_import("from sentence_transformers import SentenceTransformer"):
        HAS_SENTENCE_TRANSFORMERS = True
        logger.info("[OK] Sentence Transformers loaded")
    else:
        logger.warning("[WARN] Sentence Transformers not installed or import timeout, using keyword search")


_MODEL_AVAIL_CACHE: Dict[str, bool] = {}


def _is_model_fully_cached(model_name: str) -> bool:
    """检查 HF hub 本地缓存中该模型权重文件是否完整存在"""
    try:
        # HF 无 org 前缀模型（如 paraphrase-multilingual-MiniLM-L12-v2）实际存储为
        # sentence-transformers 组织名下，缓存目录带 sentence-transformers-- 前缀
        # （sentence_transformers 加载时自动补全 org）。两种形式都检查。
        dir_names = ["models--" + model_name.replace("/", "--")]
        if "/" not in model_name:
            dir_names.append("models--sentence-transformers--" + model_name)
        cache_root = os.environ.get("HF_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache", "huggingface"
        )
        weight_files = (
            "pytorch_model.bin",
            "model.safetensors",
            "model.safetensors.index.json",
        )
        for dir_name in dir_names:
            snapshots = os.path.join(cache_root, "hub", dir_name, "snapshots")
            if not os.path.isdir(snapshots):
                continue
            for entry in os.listdir(snapshots):
                snap_dir = os.path.join(snapshots, entry)
                if os.path.isdir(snap_dir) and any(
                    os.path.exists(os.path.join(snap_dir, f)) for f in weight_files
                ):
                    return True
        return False
    except Exception:
        return False


def _resolve_encoder_availability(model_name: str) -> bool:
    """判定编码模型是否可加载（结果缓存）

    Why: SentenceTransformer 加载模型时即使本地缓存完整，仍会对 HF 发 HEAD 请求
    检查 PEFT adapter 文件；HF 不可达时该请求重试 5 次（每次数十秒连接超时）导致
    VectorStore 构造挂起。策略：
    1. 缓存完整 → 启用 HF 离线模式，走本地加载（快速，无网络依赖）；
    2. 无缓存 → 子进程探测在线加载（有网环境可正常下载，无网/卡死 30s 后降级）。
    """
    if model_name in _MODEL_AVAIL_CACHE:
        return _MODEL_AVAIL_CACHE[model_name]
    if _is_model_fully_cached(model_name):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        ok = True
    else:
        code = (
            "from sentence_transformers import SentenceTransformer\n"
            f"m = SentenceTransformer({model_name!r})\n"
        )
        ok = _probe_import(code)
    _MODEL_AVAIL_CACHE[model_name] = ok
    return ok


_shared_encoder_cache: Dict[str, Any] = {}
_shared_encoder_lock = threading.Lock()


def _get_shared_encoder(model_name: str) -> Optional[Any]:
    """获取共享 SentenceTransformer 编码器（模块级单例）

    Why: VectorStore 每次构造都会执行 _init_sqlite_vec/_init_chroma，若每次都
    SentenceTransformer(model_name) 加载模型（~20s），同进程内多次构造会重复
    加载拖慢测试。共享单例后首次加载、后续直接复用。

    防污染：测试环境可能把 sentence_transformers 模块 patch 为 MagicMock
    （Mock 可调用不抛异常），直接 `SentenceTransformer(model)` 会得到 Mock
    并缓存进单例，后续所有 VectorStore 复用坏编码器（add 全部失败）。
    此处检测到 Mock 模块/类时返回 None 且**不缓存**，让调用方降级 JSON。
    """
    if model_name in _shared_encoder_cache:
        return _shared_encoder_cache[model_name]
    with _shared_encoder_lock:
        if model_name in _shared_encoder_cache:
            return _shared_encoder_cache[model_name]
        try:
            import sentence_transformers as _st_mod
            # duck-typing 检测 MagicMock：模块被 mock 时不应初始化编码器
            if hasattr(_st_mod, "mock_calls"):
                return None
            from sentence_transformers import SentenceTransformer
            # 类级 Mock 检测：模块真实但类被 patch 为 Mock 时同样不缓存
            if hasattr(SentenceTransformer, "mock_calls"):
                return None
            encoder = SentenceTransformer(model_name)
            _shared_encoder_cache[model_name] = encoder
            return encoder
        except Exception:
            return None


@dataclass
class MemoryItem:
    """记忆项数据类"""
    id: str
    content: str
    metadata: Dict[str, Any]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemoryItem':
        return cls(**data)


# ═══════════════════════════════════════════════════════════════
# 倒排索引 + BM25 评分（来自 vector_store_optimized_v2.py）
# ═══════════════════════════════════════════════════════════════

class InvertedIndex:
    """倒排索引 — 使用 BM25 算法进行关键词搜索评分

    相比原始的字符匹配评分（_search_fallback 中的评分逻辑），
    BM25 是业界标准的文本检索算法，能提供更准确的排序结果。
    """

    def __init__(self, k1: float = None, b: float = None):
        self._index: Dict[str, List[Tuple[str, int]]] = {}  # term -> [(doc_id, frequency), ...]
        self._doc_lengths: Dict[str, int] = {}  # doc_id -> number of terms
        self._total_docs = 0
        self._avg_doc_length = 0.0
        self._lock = threading.RLock()
        # BM25 参数：显式传入优先，否则用环境变量默认值（向后兼容无参调用）
        self._k1 = _DEFAULT_K1 if k1 is None else k1
        self._b = _DEFAULT_B if b is None else b

    def _tokenize(self, text: str) -> List[str]:
        """分词处理 — 仅提取有意义的英文单词（>=3 字符）

        中文搜索走原始的 _search_fallback 的字符重叠评分，效果已足够。
        BM25 专注英文关键词搜索，这是其优势场景。
        """
        return [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', text)]

    def _compute_bm25(self, term: str, term_freq: int, doc_length: int) -> float:
        """计算 BM25 评分（k1/b 由实例配置决定）

        参数:
            term: 查询词项（用于计算 IDF）
            term_freq: 词项在文档中的出现频率
            doc_length: 文档长度（词数）

        Note:
            k1（饱和度）和 b（长度归一化）由 __init__ 配置，可通过环境变量
            BM25_K1/BM25_B 调整。默认 b=0.5 缓解短文档虚高（原 0.75）。
        """
        if term not in self._index:
            return 0.0
        doc_count = len(self._index[term])
        idf = (self._total_docs - doc_count + 0.5) / (doc_count + 0.5)
        if idf <= 0:
            return 0.0

        k1, b = self._k1, self._b
        numerator = term_freq * (k1 + 1)
        denominator = term_freq + k1 * (1 - b + b * doc_length / (self._avg_doc_length or 1))
        return idf * numerator / denominator

    def add_document(self, doc_id: str, content: str):
        """添加文档到索引"""
        tokens = self._tokenize(content)
        term_counts = defaultdict(int)
        for token in tokens:
            term_counts[token] += 1

        with self._lock:
            for term, freq in term_counts.items():
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append((doc_id, freq))

            self._doc_lengths[doc_id] = len(tokens)
            self._total_docs += 1
            total_length = sum(self._doc_lengths.values())
            self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0

    def remove_document(self, doc_id: str):
        """从索引中移除文档"""
        with self._lock:
            if doc_id not in self._doc_lengths:
                return
            for term, postings in list(self._index.items()):
                new_postings = [(did, freq) for did, freq in postings if did != doc_id]
                self._index[term] = new_postings if new_postings else None
            self._index = {k: v for k, v in self._index.items() if v is not None}
            del self._doc_lengths[doc_id]
            self._total_docs -= 1
            if self._total_docs > 0:
                total_length = sum(self._doc_lengths.values())
                self._avg_doc_length = total_length / self._total_docs
            else:
                self._avg_doc_length = 0.0

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """搜索查询，返回 (doc_id, score) 列表"""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = defaultdict(float)
        with self._lock:
            for token in query_tokens:
                if token not in self._index:
                    continue
                for doc_id, freq in self._index[token]:
                    doc_length = self._doc_lengths.get(doc_id, 0)
                    if doc_length > 0:
                        scores[doc_id] += self._compute_bm25(token, freq, doc_length)

        # [TLM-L1] BM25 排序 — heapq.nlargest 替代 sorted[:top_k]
        # Why: top_k 通常远小于候选文档数（n >> k），heapq 维护大小为 k 的堆
        # 时间复杂度 O(n log k) vs sorted O(n log n)，n=500/k=5 时约 4 倍提速
        return heapq.nlargest(top_k, scores.items(), key=lambda x: x[1])

    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        return {
            'total_terms': len(self._index),
            'total_postings': sum(len(p) for p in self._index.values()),
            'total_docs': self._total_docs,
            'avg_doc_length': self._avg_doc_length,
        }


# ═══════════════════════════════════════════════════════════════
# LRU 查询缓存（来自 vector_store_optimized[_v2].py）
# ═══════════════════════════════════════════════════════════════

class LRUQueryCache:
    """LRU 查询缓存 — 避免重复查询的重复计算

    特性：
    - TTL 过期：缓存项在指定时间后自动失效
    - LRU 淘汰：超出最大容量时淘汰最久未使用的项
    - 命中率统计：便于监控缓存效率
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[Tuple[str, int], Tuple[List[Any], float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _is_expired(self, timestamp: float) -> bool:
        return (datetime.now().timestamp() - timestamp) > self.ttl_seconds

    def get(self, query: str, top_k: int) -> Optional[List[Any]]:
        """获取缓存结果（命中时自动更新访问时间）"""
        key = (query, top_k)
        if key in self._cache:
            results, timestamp = self._cache.pop(key)
            if not self._is_expired(timestamp):
                self._cache[key] = (results, datetime.now().timestamp())
                self.hits += 1
                return results
            self.misses += 1
        else:
            self.misses += 1
        return None

    def set(self, query: str, top_k: int, results: List[Any]):
        """设置缓存结果"""
        key = (query, top_k)
        if key in self._cache:
            self._cache.pop(key)
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (results, datetime.now().timestamp())

    def invalidate(self):
        """失效所有缓存（在添加/删除/清空记忆时调用）"""
        self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存命中统计"""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
            "size": len(self._cache),
        }


# ═══════════════════════════════════════════════════════════════
# 向量存储 — 统一实现
# ═══════════════════════════════════════════════════════════════

class VectorStore:
    """
    向量存储 — 统一实现

    根据环境自动选择存储引擎（优先级：sqlite-vec > chromadb > JSON）：
    - sqlite-vec（首选：轻量级，需 sqlite-vec + sentence-transformers，384 维）
    - ChromaDB（次选：需 chromadb + sentence-transformers）
    - JSON Fallback + 倒排索引 BM25（兜底：纯文本关键词搜索）

    优化特性（已整合）：
    - 倒排索引 + BM25 评分：替代原始字符匹配，排序更准确
    - LRU 查询缓存：重复查询直接从缓存返回，大幅提速
    - 批量添加（batch_add）：批量写入优化
    - ID 查找（get_by_id）：直接定位记忆项
    - 异步搜索（search_async）：非阻塞搜索

    线程安全：
    - _backend 字段在构造期确定后不可变，运行期不再修改
    - _use_chroma 为只读 property（基于 _backend 派生）
    """

    def __init__(self, collection_name: str = "agent_memory",
                 persist_dir: str = "./data/memory",
                 model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
                 cache_size: int = 100, cache_ttl: int = 300,
                 enable_inverted_index: bool = True):
        """
        初始化向量存储

        Args:
            collection_name: 集合名称
            persist_dir: 持久化目录
            model_name: Sentence Transformers 模型名称
            cache_size: 查询缓存最大条数（设为 0 禁用缓存）
            cache_ttl: 缓存过期时间（秒）
            enable_inverted_index: 是否启用倒排索引（仅 JSON fallback 模式生效）
        """
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self.model_name = model_name
        self._storage_path = os.path.join(persist_dir, f"{collection_name}.json")

        os.makedirs(persist_dir, exist_ok=True)

        # ── 查询缓存（两个搜索路径共享）──
        self._query_cache = LRUQueryCache(
            max_size=cache_size,
            ttl_seconds=cache_ttl,
        ) if cache_size > 0 else None

        # ── 存储引擎初始化（优先级：sqlite-vec > chromadb > JSON）──
        # _backend 在构造期确定后不可变，保证线程安全（运行期不再修改 _use_chroma）
        self._backend = "json"
        self._sqlite_vec_backend = None
        self._encoder = None  # sentence_transformers 编码器（sqlite-vec/chromadb 共用）
        self._items: List[MemoryItem] = []
        self._id_to_index: Dict[str, int] = {}
        self._inverted_index = None
        self._chroma_client = None
        self._chroma_collection = None

        _check_chroma_available()

        # 【变易】编码器可用性判定：模型缓存完整则离线加载（避免 HF 不可达时
        # HEAD 请求重试挂起）；无缓存则子进程探测在线加载，超时降级 JSON
        encoder_ok = _resolve_encoder_availability(self.model_name)
        # ST 模块子进程探测可能因并行测试高负载超时（30s 被杀），但模型缓存完整
        # 说明 sentence-transformers 曾可用且离线加载可行——不因探测超时降级 json
        st_ok = HAS_SENTENCE_TRANSFORMERS or encoder_ok

        # 优先级 1: sqlite-vec（轻量级，需 sentence_transformers 编码）
        if st_ok and self._init_sqlite_vec():
            self._backend = "sqlite_vec"
        # 优先级 2: ChromaDB（重量级，需 chromadb + sentence_transformers）
        elif st_ok and HAS_CHROMA:
            self._backend = "chromadb"
            self._init_chroma()  # 内部失败时会将 _backend 改为 "json"
        # 优先级 3: JSON Fallback + BM25
        else:
            self._backend = "json"
            self._load_from_file()
            if enable_inverted_index:
                self._inverted_index = InvertedIndex()
                self._rebuild_inverted_index()
                logger.info("[OK] 倒排索引已启用 (BM25)")

        logger.info(f"向量存储初始化完成: {collection_name}")
        logger.info(f"   ├─ 持久化目录: {persist_dir}")
        logger.info(f"   ├─ 存储后端: {self._backend}")

    @property
    def _use_chroma(self) -> bool:
        """是否使用 ChromaDB 后端（只读，基于 _backend 不可变字段派生）

        保留以兼容现有代码的 _use_chroma 检查。
        """
        return self._backend == "chromadb"

    def _init_sqlite_vec(self) -> bool:
        """初始化 sqlite-vec 后端

        Returns:
            True 表示成功初始化
        """
        try:
            import sqlite_vec  # noqa: F401
            # 延迟导入，避免模块导入时拉起 sqlite-vec 扩展
            from .sqlite_vec_backend import SqliteVecBackend

            # 复用共享编码器（避免每次构造重复加载模型）
            self._encoder = _get_shared_encoder(self.model_name)
            if self._encoder is None:
                logger.info("sentence-transformers 编码器加载失败，降级")
                return False
            dim = self._encoder.get_sentence_embedding_dimension()

            db_path = os.path.join(self.persist_dir, f"{self.collection_name}_vec.db")
            self._sqlite_vec_backend = SqliteVecBackend(
                db_path=db_path,
                collection_name=self.collection_name,
                dim=dim,
            )
            logger.info(f"✅ sqlite-vec 后端启用: {db_path} (dim={dim})")
            return True
        except ImportError as e:
            logger.info(f"sqlite-vec 不可用，降级: {e}")
            return False
        except Exception as e:
            logger.warning(f"sqlite-vec 初始化失败: {e}")
            return False

    def _init_chroma(self):
        """初始化 ChromaDB"""
        try:
            # 局部导入重量级依赖（chromadb / sentence_transformers → torch），
            # 避免在模块导入时拉起 torch。_check_chroma_available() 已确认这些模块可用。
            import chromadb
            from chromadb.config import Settings
            # chromadb 0.4.x：PersistentClient 才真正持久化到磁盘
            # 旧版用 chromadb.Client(Settings(persist_directory=...)) 实际创建的是 ephemeral 客户端，
            # 且 ephemeral client 有单例缓存，第二次以不同 settings 实例化会报
            # "An instance of Chroma already exists for ephemeral with different settings"
            self._chroma_client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(anonymized_telemetry=False)
            )
            self._chroma_collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "云枢智能体记忆库"}
            )
            # 复用共享编码器（避免每次构造重复加载模型）
            self._encoder = _get_shared_encoder(self.model_name)
            if self._encoder is None:
                raise RuntimeError("sentence-transformers 编码器加载失败")
            logger.info(f"✅ ChromaDB 集合创建成功: {self.collection_name}")
        except Exception as e:
            logger.warning(f"⚠️ ChromaDB 初始化失败: {e}，使用 fallback")
            # 构造期允许修改 _backend（尚未对外发布）
            self._backend = "json"
            self._items = []
            self._id_to_index = {}
            # fallback 必须加载磁盘 JSON，否则持久化失效（vs2 重新打开时 _items 为空）
            self._load_from_file()
            # fallback 必须重建倒排索引，否则 BM25 搜索返回 0 结果
            if self._inverted_index is None:
                self._inverted_index = InvertedIndex()
            self._rebuild_inverted_index()

    def _load_from_file(self):
        """从 JSON 文件加载记忆"""
        if os.path.exists(self._storage_path):
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._items = [MemoryItem.from_dict(item) for item in data]
                    self._id_to_index = {item.id: i for i, item in enumerate(self._items)}
                logger.info(f"📂 加载记忆: {len(self._items)} 条")
            except Exception as e:
                logger.warning(f"加载记忆失败: {e}")
                self._items = []
                self._id_to_index = {}
        else:
            self._items = []
            self._id_to_index = {}
            logger.info("📂 新建空记忆库")

    def _save_to_file(self):
        """保存记忆到 JSON 文件"""
        try:
            os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
            with open(self._storage_path, "w", encoding="utf-8") as f:
                data = [item.to_dict() for item in self._items]
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存记忆失败: {e}")

    def _rebuild_inverted_index(self):
        """重建倒排索引（从现有 items 重建）"""
        if not self._inverted_index:
            return
        for item in self._items:
            self._inverted_index.add_document(item.id, item.content)

    @property
    def count(self) -> int:
        """获取记忆数量"""
        if self._backend == "sqlite_vec":
            return self._sqlite_vec_backend.count()
        if self._use_chroma:
            try:
                return self._chroma_collection.count()
            except Exception:
                pass
        return len(self._items)

    @property
    def items(self) -> List[MemoryItem]:
        """获取所有记忆项"""
        if self._backend == "sqlite_vec":
            # sqlite-vec 不支持高效全量拉取，仅返回最近 N 条
            recent = self._sqlite_vec_backend.get_recent(limit=10000)
            return [
                MemoryItem(
                    id=r["id"], content=r["content"],
                    metadata=r["metadata"], timestamp=r["timestamp"],
                ) for r in recent
            ]
        if self._use_chroma:
            try:
                all_data = self._chroma_collection.get()
                return [
                    MemoryItem(
                        id=all_data["ids"][i],
                        content=all_data["documents"][i],
                        metadata=all_data["metadatas"][i],
                        timestamp=all_data["metadatas"][i].get("created_at", "")
                    )
                    for i in range(len(all_data["ids"]))
                ]
            except Exception:
                return []
        return self._items

    # ── 添加 ──

    def add(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        添加记忆项

        Args:
            content: 记忆内容
            metadata: 元数据

        Returns:
            记忆项ID
        """
        item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        metadata = metadata or {}
        metadata["created_at"] = datetime.now().isoformat()

        # 失效缓存
        if self._query_cache:
            self._query_cache.invalidate()

        if self._backend == "sqlite_vec":
            embedding = self._encoder.encode([content]).tolist()
            if self._sqlite_vec_backend.add(
                item_id=item_id,
                content=content,
                embedding=embedding[0],
                metadata=metadata,
                timestamp=metadata["created_at"],
            ):
                logger.info(f"✅ 添加记忆 [sqlite-vec]: {item_id}")
            else:
                logger.error(f"sqlite-vec 添加失败: {item_id}")
        elif self._backend == "chromadb":
            try:
                embedding = self._encoder.encode([content]).tolist()
                self._chroma_collection.add(
                    ids=[item_id],
                    documents=[content],
                    metadatas=[metadata],
                    embeddings=embedding
                )
                logger.info(f"✅ 添加记忆 [Chroma]: {item_id}")
            except Exception as e:
                # 不再修改 _backend（线程安全），仅本次降级到 JSON 路径
                logger.warning(f"ChromaDB 添加失败: {e}，本次降级到 JSON")
                self._add_fallback(item_id, content, metadata)
        else:  # json
            self._add_fallback(item_id, content, metadata)

        logger.debug(f"   ├─ 内容: {content[:60]}...")
        logger.debug(f"   └─ 当前总数: {self.count}")
        return item_id

    def batch_add(self, items: List[Dict[str, Any]]) -> List[str]:
        """批量添加记忆项

        Args:
            items: 记忆项列表，每项包含 content（必填）和 metadata（可选）

        Returns:
            记忆项ID列表
        """
        if self._backend == "sqlite_vec":
            # 失效缓存
            if self._query_cache:
                self._query_cache.invalidate()

            contents = [item.get("content", "") for item in items]
            embeddings = self._encoder.encode(contents).tolist()
            now_iso = datetime.now().isoformat()
            backend_items = []
            item_ids = []
            for i, item_data in enumerate(items):
                content = item_data.get("content", "")
                metadata = item_data.get("metadata", {})
                metadata["created_at"] = now_iso
                item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{i}"
                backend_items.append({
                    "id": item_id,
                    "content": content,
                    "embedding": embeddings[i],
                    "metadata": metadata,
                    "timestamp": now_iso,
                })
                item_ids.append(item_id)
            self._sqlite_vec_backend.batch_add(backend_items)
            logger.info(f"✅ 批量添加完成 [sqlite-vec]: {len(item_ids)} 条")
            return item_ids

        if self._use_chroma:
            # ChromaDB 模式下逐条添加
            return [self.add(item.get("content", ""), item.get("metadata")) for item in items]

        # 失效缓存
        if self._query_cache:
            self._query_cache.invalidate()

        item_ids = []
        for item_data in items:
            content = item_data.get("content", "")
            metadata = item_data.get("metadata", {})
            metadata["created_at"] = datetime.now().isoformat()

            item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            item = MemoryItem(
                id=item_id,
                content=content,
                metadata=metadata,
                timestamp=datetime.now().isoformat()
            )
            self._items.append(item)
            self._id_to_index[item_id] = len(self._items) - 1
            if self._inverted_index:
                self._inverted_index.add_document(item_id, content)
            item_ids.append(item_id)

        self._save_to_file()
        logger.info(f"✅ 批量添加完成: {len(items)} 条")
        return item_ids

    def _add_fallback(self, item_id: str, content: str, metadata: Dict):
        """Fallback 模式添加一条记忆"""
        item = MemoryItem(
            id=item_id,
            content=content,
            metadata=metadata,
            timestamp=datetime.now().isoformat()
        )
        self._items.append(item)
        self._id_to_index[item_id] = len(self._items) - 1
        if self._inverted_index:
            self._inverted_index.add_document(item_id, content)
        self._save_to_file()
        logger.info(f"✅ 添加记忆 [Fallback]: {item_id}")

    # ── 搜索 ──

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """
        搜索记忆

        搜索路径（按 _backend 自动选择）:
        1. sqlite-vec KNN 向量搜索（首选）
        2. ChromaDB 语义搜索（次选）
        3. 倒排索引 + BM25 关键词搜索（JSON fallback 模式）
        4. 原始字符匹配（兜底）

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            匹配的记忆项列表
        """
        logger.info(f"🔍 搜索记忆: query='{query[:50]}...', top_k={top_k}")

        # ── 查询缓存命中 ──
        if self._query_cache:
            cached = self._query_cache.get(query, top_k)
            if cached is not None:
                logger.info(f"   ├─ [缓存命中] 返回 {len(cached)} 条")
                return cached

        # ── sqlite-vec KNN 搜索 ──
        if self._backend == "sqlite_vec":
            try:
                query_vec = self._encoder.encode([query]).tolist()[0]
                raw_results = self._sqlite_vec_backend.search(query_vec, top_k=top_k)
                items = [
                    MemoryItem(
                        id=r["id"], content=r["content"],
                        metadata=r["metadata"], timestamp=r["timestamp"],
                    ) for r in raw_results
                ]
                logger.info(f"   ├─ sqlite-vec 匹配结果数: {len(items)}")
                if self._query_cache:
                    self._query_cache.set(query, top_k, items)
                return items
            except Exception as e:
                logger.error(f"sqlite-vec 搜索失败: {e}")
                return []

        # ── ChromaDB 搜索 ──
        if self._use_chroma:
            try:
                results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=top_k
                )
                items = []
                if results["ids"] and len(results["ids"][0]) > 0:
                    for i in range(len(results["ids"][0])):
                        items.append(MemoryItem(
                            id=results["ids"][0][i],
                            content=results["documents"][0][i],
                            metadata=results["metadatas"][0][i],
                            timestamp=results["metadatas"][0][i].get("created_at", "")
                        ))
                logger.info(f"   ├─ ChromaDB 匹配结果数: {len(items)}")

                # 写入缓存
                if self._query_cache:
                    self._query_cache.set(query, top_k, items)
                return items
            except Exception as e:
                # 不再修改 _backend（线程安全），仅本次降级到 JSON 搜索路径
                logger.warning(f"ChromaDB 搜索失败: {e}，本次降级到 JSON")

        # ── JSON Fallback 搜索 ──
        # 混合策略：BM25（英文精准搜索） + 原始评分（中英文兜底）
        if self._inverted_index:
            results = self._bm25_search(query, top_k)
            # BM25 无结果时降级到原始字符匹配
            if not results:
                results = self._search_fallback(query, top_k)
        else:
            results = self._search_fallback(query, top_k)

        # 写入缓存
        if self._query_cache:
            self._query_cache.set(query, top_k, results)

        logger.info(f"   └─ 返回: {len(results)} 条")
        return results

    def _bm25_search(self, query: str, top_k: int) -> List[MemoryItem]:
        """BM25 倒排索引搜索（替代原始 _search_fallback）"""
        doc_scores = self._inverted_index.search(query, top_k * 2)
        results = []
        seen_ids = set()
        for doc_id, score in doc_scores:
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            idx = self._id_to_index.get(doc_id)
            if idx is not None:
                item = self._items[idx]
                item.metadata['_score'] = round(score, 4)
                results.append(item)
            if len(results) >= top_k:
                break
        return results

    def _search_fallback(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """原始字符匹配搜索（兜底：当倒排索引不可用时）"""
        results = []
        query_lower = query.lower()
        for item in reversed(self._items):
            content_lower = item.content.lower()
            score = 0
            if query_lower in content_lower:
                score += 10
            match_count = sum(1 for char in query_lower if char in content_lower and char.strip())
            if match_count >= len(query_lower) * 0.3:
                score += match_count
            if score > 0:
                results.append((score, item))
        results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in results[:top_k]]

    async def search_async(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """异步搜索（在后台线程中执行搜索，不阻塞事件循环）"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, query, top_k)

    # ── ID 查找 ──

    def _has_english_tokens(self, query: str) -> bool:
        """检查查询是否包含有意义的英文 token"""
        # 使用 ASCII 模式检查英文字母（避免 Unicode 模式下中文也被算作 \w）
        return bool(re.search(r'[a-zA-Z]{3,}', query))

    def get_by_id(self, item_id: str) -> Optional[MemoryItem]:
        """根据 ID 获取记忆项"""
        if self._backend == "sqlite_vec":
            r = self._sqlite_vec_backend.get_by_id(item_id)
            if r is None:
                return None
            return MemoryItem(
                id=r["id"], content=r["content"],
                metadata=r["metadata"], timestamp=r["timestamp"],
            )
        if self._use_chroma:
            try:
                all_data = self._chroma_collection.get()
                for i, cid in enumerate(all_data["ids"]):
                    if cid == item_id:
                        return MemoryItem(
                            id=cid,
                            content=all_data["documents"][i],
                            metadata=all_data["metadatas"][i],
                            timestamp=all_data["metadatas"][i].get("created_at", "")
                        )
            except Exception:
                pass
            return None
        idx = self._id_to_index.get(item_id)
        if idx is not None:
            return self._items[idx]
        return None

    # ── 获取最近 ──

    def get_recent(self, limit: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        if self._backend == "sqlite_vec":
            rows = self._sqlite_vec_backend.get_recent(limit=limit)
            return [
                MemoryItem(
                    id=r["id"], content=r["content"],
                    metadata=r["metadata"], timestamp=r["timestamp"],
                ) for r in rows
            ]
        if self._use_chroma:
            try:
                all_items = self.items
                all_items.sort(key=lambda x: x.timestamp, reverse=True)
                return all_items[:limit]
            except Exception:
                pass
        return list(reversed(self._items[-limit:]))

    # ── 清空 ──

    def clear(self):
        """清空记忆"""
        if self._query_cache:
            self._query_cache.invalidate()

        if self._backend == "sqlite_vec":
            self._sqlite_vec_backend.clear()
            logger.info("🗑️ sqlite-vec 数据已清空")
            return

        if self._use_chroma:
            try:
                self._chroma_client.delete_collection(self.collection_name)
                self._chroma_collection = self._chroma_client.create_collection(
                    name=self.collection_name
                )
                logger.info("🗑️ ChromaDB 集合已清空")
            except Exception as e:
                logger.warning(f"清空失败: {e}")

        self._items = []
        self._id_to_index = {}
        if self._inverted_index:
            self._inverted_index = InvertedIndex()
        self._save_to_file()
        logger.info("🗑️ 记忆已清空")

    # ── 统计信息 ──

    def get_stats(self) -> Dict[str, Any]:
        """获取存储统计信息"""
        stats = {
            "backend": self._backend,
            "type": "sqlite_vec" if self._backend == "sqlite_vec"
                    else ("chroma" if self._use_chroma else "fallback"),
            "count": self.count,
            "persist_dir": self.persist_dir,
            "collection_name": self.collection_name,
        }
        if self._backend == "sqlite_vec" and self._sqlite_vec_backend:
            stats["sqlite_vec"] = self._sqlite_vec_backend.get_stats()
        if self._query_cache:
            stats["cache"] = self._query_cache.get_stats()
        if self._inverted_index:
            stats["inverted_index"] = self._inverted_index.get_stats()
        return stats

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取查询缓存统计"""
        return self._query_cache.get_stats() if self._query_cache else {"hits": 0, "misses": 0, "hit_rate": 0, "size": 0}

    def get_index_stats(self) -> Optional[Dict[str, Any]]:
        """获取倒排索引统计"""
        return self._inverted_index.get_stats() if self._inverted_index else None


# ═══════════════════════════════════════════════════════════════
# 知识库
# ═══════════════════════════════════════════════════════════════

class KnowledgeBase:
    """
    知识库 - 基于向量存储的知识管理

    用于管理和查询结构化知识文档。
    """

    def __init__(self, store: Optional[VectorStore] = None):
        """
        初始化知识库

        Args:
            store: 向量存储实例，默认创建新实例
        """
        self.store = store or VectorStore(collection_name="knowledge_base")

    def add_document(self, content: str, source: str, tags: Optional[List[str]] = None):
        """
        添加文档到知识库

        Args:
            content: 文档内容
            source: 文档来源
            tags: 标签列表
        """
        self.store.add(
            content=content,
            metadata={
                "type": "document",
                "source": source,
                "tags": tags or []
            }
        )
        logger.info(f"[KnowledgeBase] 添加文档: {source}")

    def _format_results(self, results: List[MemoryItem]) -> str:
        """格式化搜索结果"""
        if not results:
            return "（知识库中未找到相关信息）"
        context = "\n【知识库检索结果】\n"
        for i, item in enumerate(results, 1):
            context += f"\n{i}. {item.content}\n"
            if item.metadata.get("source"):
                context += f"   来源: {item.metadata['source']}\n"
        return context

    def query(self, question: str, top_k: int = 3) -> str:
        """
        查询知识库

        Args:
            question: 查询问题
            top_k: 返回结果数量

        Returns:
            格式化的查询结果
        """
        return self._format_results(self.store.search(question, top_k))

    async def query_async(self, question: str, top_k: int = 3) -> str:
        """异步查询知识库"""
        results = await self.store.search_async(question, top_k)
        return self._format_results(results)


VectorStore = VectorStore
MemoryItem = MemoryItem
