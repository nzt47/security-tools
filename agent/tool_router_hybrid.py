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

# 原生崩溃退出码(用于诊断 Embedding 子进程崩溃原因)
_WIN_ACCESS_VIOLATION = -1073741819   # 0xC0000005
_WIN_STACK_OVERFLOW = -1073741571     # 0xC00000FD
_WIN_ILLEGAL_INSTRUCTION = -1073741795 # 0xC000001D
_LINUX_SIGSEGV = -11
_LINUX_SIGILL = -4


def _diagnose_crash(returncode: Optional[int]) -> str:
    """根据子进程退出码诊断原生崩溃原因

    Why: 0xC0000005 / SIGILL 等原生崩溃不会抛 Python 异常,
        只能通过 returncode 识别。诊断信息写入日志便于排查。
    """
    if returncode is None or returncode == 0:
        return ""
    if returncode == _WIN_ACCESS_VIOLATION:
        return ("Windows ACCESS_VIOLATION (0xC0000005) - 原生内存访问违规,"
                "常见于 PyTorch C 扩展或 SentenceTransformer 加载大模型")
    if returncode == _WIN_STACK_OVERFLOW:
        return "Windows STACK_OVERFLOW (0xC00000FD) - 栈溢出,常见于递归过深"
    if returncode == _WIN_ILLEGAL_INSTRUCTION:
        return ("Windows ILLEGAL_INSTRUCTION (0xC000001D) - 非法指令,"
                "常见于 CPU 不支持 AVX/AVX2")
    if returncode == _LINUX_SIGSEGV:
        return "Linux SIGSEGV - 段错误,常见于 PyTorch C 扩展内存访问违规"
    if returncode == _LINUX_SIGILL:
        return "Linux SIGILL - 非法指令,常见于 CPU 不支持 AVX/AVX2"
    return f"未知退出码: {returncode}"

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
#  EmbeddingIndex — 子进程隔离 + 二进制序列化 + LRU 缓存
# ════════════════════════════════════════════════════════════

# Embedding worker 脚本(子进程隔离,通过 python -c 启动)
# 【不易】JSON Lines 通信协议,encode 请求 → embeddings 响应
# 【变易】二进制序列化:base64(numpy.tobytes()) 替代 JSON float 列表(省 ~2ms/次)
_WORKER_SCRIPT_EMBEDDING = """
import json, os, sys, base64
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "paraphrase-multilingual-MiniLM-L12-v2"
    try:
        from sentence_transformers import SentenceTransformer
        import time
        t0 = time.time()
        model = SentenceTransformer(model_name)
        load_time = time.time() - t0
        print(json.dumps({"type": "ready", "load_time_sec": round(load_time, 2),
                          "load_source": model_name}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "init_failed", "error": str(e)}), flush=True)
        sys.exit(1)

    import numpy as np
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("type") == "exit":
                break
            if req.get("type") == "encode":
                texts = req.get("texts", [])
                vecs = model.encode(texts, show_progress_bar=False)
                arr = np.array(vecs, dtype=np.float32)
                # 二进制序列化:base64(bytes) 比 JSON float 列表快 ~5x
                raw_bytes = arr.tobytes()
                b64_data = base64.b64encode(raw_bytes).decode("ascii")
                print(json.dumps({
                    "type": "embeddings",
                    "data": b64_data,
                    "shape": list(arr.shape),
                    "dtype": "float32",
                }), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "error": str(e)}), flush=True)

if __name__ == "__main__":
    main()
"""


class EmbeddingIndex:
    """子进程隔离的 SentenceTransformer 语义索引

    【不易】模型加载失败时 available=False,hybrid 降级到纯 BM25
    【变易】子进程隔离:避免 SentenceTransformer 原生崩溃(0xC0000005/SIGILL)影响主进程
    【变易】二进制序列化:base64+numpy.tobytes() 替代 JSON float 列表(省 ~2ms/次)
    【变易】query embedding LRU 缓存:重复查询跳过子进程通信
    【简易】Worker 只负责 encode,主进程存 numpy 数组 + 计算 cosine similarity
    """

    _WORKER_STARTUP_TIMEOUT = 60
    _DEFAULT_QUERY_CACHE_SIZE = 128

    def __init__(self, model_name: str = _DEFAULT_MODEL,
                 query_cache_size: int = _DEFAULT_QUERY_CACHE_SIZE) -> None:
        self._model_name = model_name
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._doc_ids: list[str] = []
        self._embeddings = None
        self._pending: list[tuple[str, str]] = []
        self._init_failed = False
        self._load_time_sec: Optional[float] = None
        self._load_source: Optional[str] = None
        self._project_root = _PROJECT_ROOT
        # query embedding LRU 缓存
        self._query_cache_size = max(query_cache_size, 1)
        self._query_cache: dict = {}
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def available(self) -> bool:
        """子进程存活 + embeddings 已计算 + doc_ids 非空 + 未失败"""
        if self._init_failed:
            return False
        if self._proc is None or self._proc.poll() is not None:
            return False
        return self._embeddings is not None and len(self._doc_ids) > 0

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档到 pending 列表(延迟编码)"""
        with self._lock:
            if doc_id in self._doc_ids:
                idx = self._doc_ids.index(doc_id)
                self._doc_ids.pop(idx)
                if self._embeddings is not None:
                    self._embeddings = np.delete(self._embeddings, idx, axis=0)
            self._pending = [(d, c) for d, c in self._pending if d != doc_id]
            self._pending.append((doc_id, content))

    def _ensure_worker(self) -> bool:
        """启动子进程 worker + 等待 ready 信号 + 编码 pending 文档"""
        if self._init_failed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True

        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _WORKER_SCRIPT_EMBEDDING, self._model_name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self._project_root,
            )
        except (OSError, ValueError) as e:
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.worker.popen_failed",
                "error": str(e),
            }, ensure_ascii=False))
            self._init_failed = True
            return False

        # 等待 ready 信号(带超时)
        json_errors = 0
        while True:
            try:
                line = self._proc.stdout.readline()
            except OSError as e:
                logger.warning(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.worker.stdout_read_failed",
                    "error": str(e),
                }, ensure_ascii=False))
                self._init_failed = True
                return False

            if not line:
                rc = self._proc.poll()
                diag = _diagnose_crash(rc)
                stderr_msg = ""
                try:
                    stderr_msg = self._proc.stderr.read()[:500] if self._proc.stderr else ""
                except Exception:
                    pass
                logger.warning(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.worker.crash",
                    "returncode": rc,
                    "diagnosis": diag,
                    "stderr_preview": stderr_msg,
                }, ensure_ascii=False))
                self._init_failed = True
                return False

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                json_errors += 1
                if json_errors >= 3:
                    logger.warning(json.dumps({
                        "module_name": "tool_router_hybrid",
                        "action": "embedding.worker.invalid_json",
                        "consecutive_errors": json_errors,
                    }, ensure_ascii=False))
                    self._init_failed = True
                    return False
                continue

            msg_type = msg.get("type")
            if msg_type == "ready":
                self._load_time_sec = msg.get("load_time_sec")
                self._load_source = msg.get("load_source")
                logger.info(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.worker.ready",
                    "model": self._model_name,
                    "load_time_sec": self._load_time_sec,
                    "load_source": self._load_source,
                }, ensure_ascii=False))
                # 编码 pending 文档
                if self._pending:
                    self._encode_pending_locked()
                return True
            elif msg_type == "init_failed":
                logger.warning(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.worker.init_failed",
                    "error": msg.get("error", "unknown"),
                }, ensure_ascii=False))
                self._init_failed = True
                return False
            else:
                logger.warning(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.worker.unknown_message",
                    "msg_type": msg_type,
                }, ensure_ascii=False))
                self._init_failed = True
                return False

    def _encode_via_worker(self, texts: list[str]) -> "list | None":
        """通过子进程编码文本,返回向量列表"""
        if self._init_failed or self._proc is None:
            return None
        if self._proc.poll() is not None:
            rc = self._proc.poll()
            diag = _diagnose_crash(rc)
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode.proc_dead",
                "returncode": rc,
                "diagnosis": diag,
            }, ensure_ascii=False))
            self._init_failed = True
            return None

        try:
            req = json.dumps({"type": "encode", "texts": texts})
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            rc = self._proc.poll()
            diag = _diagnose_crash(rc)
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode.write_failed",
                "error": str(e),
                "returncode": rc,
                "diagnosis": diag,
            }, ensure_ascii=False))
            self._init_failed = True
            return None

        try:
            line = self._proc.stdout.readline()
        except OSError as e:
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode.read_failed",
                "error": str(e),
            }, ensure_ascii=False))
            self._init_failed = True
            return None

        if not line:
            rc = self._proc.poll()
            diag = _diagnose_crash(rc)
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode.eof",
                "returncode": rc,
                "diagnosis": diag,
            }, ensure_ascii=False))
            self._init_failed = True
            return None

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode.invalid_json",
            }, ensure_ascii=False))
            return None

        if msg.get("type") != "embeddings":
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode.unexpected_msg",
                "msg_type": msg.get("type"),
                "error": msg.get("error", ""),
            }, ensure_ascii=False))
            return None

        # 优先解析二进制序列化(base64+numpy),fallback 到 JSON 列表
        import base64
        if "data" in msg and _HAS_NUMPY:
            try:
                raw = base64.b64decode(msg["data"])
                arr = np.frombuffer(raw, dtype=np.float32)
                shape = msg.get("shape")
                if shape:
                    arr = arr.reshape(shape)
                return arr.tolist()
            except Exception:
                pass
        return msg.get("vectors")

    def _encode_pending_locked(self) -> None:
        """编码所有 pending 文档(调用方持锁)"""
        if not self._pending:
            return
        contents = [c for _, c in self._pending]
        vectors = self._encode_via_worker(contents)
        if vectors is None or len(vectors) == 0:
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.encode_pending.failed",
                "n_pending": len(self._pending),
            }, ensure_ascii=False))
            return
        new_embeddings = np.array(vectors, dtype=np.float32)
        if self._embeddings is None:
            self._embeddings = new_embeddings
            self._doc_ids = [d for d, _ in self._pending]
        else:
            self._embeddings = np.vstack([self._embeddings, new_embeddings])
            self._doc_ids.extend(d for d, _ in self._pending)
        logger.info(json.dumps({
            "module_name": "tool_router_hybrid",
            "action": "embedding.encode_pending.complete",
            "n_pending": len(self._pending),
            "total_docs": len(self._doc_ids),
            "shape": list(new_embeddings.shape),
        }, ensure_ascii=False))
        self._pending.clear()

    def _cleanup_proc(self) -> None:
        """清理子进程资源"""
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"type": "exit"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
                    self._proc.wait(timeout=3)
        except Exception as e:
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.cleanup.error",
                "error": str(e),
            }, ensure_ascii=False))
        finally:
            self._proc = None

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询,返回 [(doc_id, cosine_similarity)] 列表(按相似度降序)

        【变易】query embedding LRU 缓存:重复查询跳过子进程通信
        """
        if not _HAS_NUMPY:
            return []
        if not self._ensure_worker():
            return []
        with self._lock:
            if self._embeddings is None or len(self._doc_ids) == 0:
                return []

            # LRU 缓存查找
            cache_hit = query in self._query_cache
            t_encode_start = time.perf_counter()
            if cache_hit:
                query_emb = self._query_cache.pop(query)
                self._query_cache[query] = query_emb
                self._cache_hits += 1
                t_encode_ms = (time.perf_counter() - t_encode_start) * 1000
                logger.debug(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.cache.hit",
                    "query_preview": query[:60],
                    "query_len": len(query),
                    "encode_ms": round(t_encode_ms, 4),
                    "cache_size": len(self._query_cache),
                    "cumulative_hits": self._cache_hits,
                    "cumulative_misses": self._cache_misses,
                }, ensure_ascii=False))
            else:
                self._cache_misses += 1
                logger.debug(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.cache.miss",
                    "query_preview": query[:60],
                    "query_len": len(query),
                    "cache_size_before": len(self._query_cache),
                }, ensure_ascii=False))
                query_vectors = self._encode_via_worker([query])
                t_encode_ms = (time.perf_counter() - t_encode_start) * 1000
                if query_vectors is None or len(query_vectors) == 0:
                    logger.warning(json.dumps({
                        "module_name": "tool_router_hybrid",
                        "action": "embedding.search.encode_failed",
                        "encode_ms": round(t_encode_ms, 2),
                        "query_len": len(query),
                    }, ensure_ascii=False))
                    return []
                query_emb = np.array(query_vectors[0], dtype=np.float32)
                self._query_cache[query] = query_emb
                # LRU 淘汰:缓存满时移除最久未使用的条目
                if len(self._query_cache) > self._query_cache_size:
                    evicted_key = next(iter(self._query_cache))
                    self._query_cache.pop(evicted_key)
                    logger.debug(json.dumps({
                        "module_name": "tool_router_hybrid",
                        "action": "embedding.cache.evict",
                        "evicted_preview": evicted_key[:60],
                        "cache_size": len(self._query_cache),
                        "cache_capacity": self._query_cache_size,
                    }, ensure_ascii=False))
                logger.debug(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.cache.miss_complete",
                    "query_preview": query[:60],
                    "encode_ms": round(t_encode_ms, 2),
                    "cache_size_after": len(self._query_cache),
                    "cumulative_hits": self._cache_hits,
                    "cumulative_misses": self._cache_misses,
                }, ensure_ascii=False))

            try:
                t_cosine_start = time.perf_counter()
                norms = np.linalg.norm(self._embeddings, axis=1)
                query_norm = np.linalg.norm(query_emb)
                if query_norm < 1e-9:
                    return []
                denom = norms * query_norm
                denom = np.where(denom < 1e-9, 1e-9, denom)
                sims = self._embeddings @ query_emb / denom
                top_indices = np.argsort(-sims)[:top_k]
                results = [(self._doc_ids[i], float(sims[i])) for i in top_indices]
                t_cosine_ms = (time.perf_counter() - t_cosine_start) * 1000
                total_cached = self._cache_hits + self._cache_misses
                hit_rate = self._cache_hits / max(total_cached, 1)
                logger.info(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.search.complete",
                    "encode_ms": round(t_encode_ms, 2),
                    "cosine_ms": round(t_cosine_ms, 2),
                    "total_ms": round(t_encode_ms + t_cosine_ms, 2),
                    "n_docs": len(self._doc_ids),
                    "top_k": top_k,
                    "returned": len(results),
                    "top1_score": round(results[0][1], 4) if results else 0.0,
                    "cache_hit": cache_hit,
                    "cache_hit_rate": round(hit_rate, 4),
                    "cache_size": len(self._query_cache),
                }, ensure_ascii=False))
                return results
            except Exception as e:
                logger.warning(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.search.failed",
                    "error": f"{type(e).__name__}: {e}",
                    "encode_ms": round(t_encode_ms, 2),
                    "query_len": len(query),
                }, ensure_ascii=False))
                return []

    def clear(self) -> None:
        """清空索引(不关闭子进程)"""
        with self._lock:
            self._doc_ids.clear()
            self._embeddings = None
            self._pending.clear()
            self._query_cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0

    def get_cache_stats(self) -> dict:
        """返回 LRU query 缓存统计信息(用于运行时缓存效率验证)

        Returns:
            {"hits", "misses", "hit_rate", "cache_size", "cache_capacity"}
        """
        with self._lock:
            total = self._cache_hits + self._cache_misses
            return {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": round(self._cache_hits / max(total, 1), 4),
                "cache_size": len(self._query_cache),
                "cache_capacity": self._query_cache_size,
            }

    def preheat(self) -> None:
        """预热:启动子进程 + 编码 pending 文档"""
        t0 = time.perf_counter()
        pending_count = len(self._pending)
        logger.info(json.dumps({
            "module_name": "tool_router_hybrid",
            "action": "embedding.preheat.start",
            "pending_docs": pending_count,
        }, ensure_ascii=False))
        try:
            ok = self._ensure_worker()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if ok:
                logger.info(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.preheat.success",
                    "elapsed_ms": round(elapsed_ms, 2),
                    "pending_docs": pending_count,
                    "encoded_docs": len(self._doc_ids),
                    "load_time_sec": self._load_time_sec,
                }, ensure_ascii=False))
            else:
                logger.warning(json.dumps({
                    "module_name": "tool_router_hybrid",
                    "action": "embedding.preheat.failed",
                    "elapsed_ms": round(elapsed_ms, 2),
                    "init_failed": self._init_failed,
                }, ensure_ascii=False))
        except Exception as e:
            logger.warning(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.preheat.error",
                "error": str(e),
            }, ensure_ascii=False))


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
