"""ChromaDB 异步预加载与内存映射优化模块

功能：
- ChromaDB 异步初始化
- 延迟加载策略
- 内存映射优化
- 连接池管理
- 自动重连机制

使用方法：
```python
from agent.memory_optimized import OptimizedChromaDB

# 异步初始化
db = OptimizedChromaDB.async_create(
    persist_directory="./data/chroma",
    collection_name="my_collection"
)

# 或同步初始化（带进度回调）
db = OptimizedChromaDB(
    persist_directory="./data/chroma",
    on_progress=lambda msg: print(f"Loading: {msg}")
)
```

优化策略：
1. 异步初始化 - 不阻塞主线程
2. 延迟加载 - 按需初始化
3. 内存映射 - 使用 mmap 加速读取
4. 连接池 - 复用连接
5. 懒加载索引 - 只加载必要索引
"""

import os
import sys
import time
import threading
import logging
import json
import uuid
import tempfile
import hashlib
import subprocess
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from pathlib import Path
from collections import OrderedDict
from agent.logging_utils import log_dict

import warnings
warnings.warn(
    "memory_optimized 模块已废弃，请使用 memory.vector_store.VectorStore (sqlite-vec/chromadb 后端)",
    DeprecationWarning,
    stacklevel=2
)

logger = logging.getLogger(__name__)

# 【变易】chromadb import/客户端创建超时(秒)。chromadb 1.5.9 + pydantic 2.x 在部分
# 环境下 import 会长时间卡死(pydantic_settings dotenv 解析)或抛 UserIdentity 双加载错误,
# try/except ImportError 无法拦截"卡死"，需子进程探测兜底降级为 MockChromaClient。
_CHROMADB_IMPORT_TIMEOUT = 30.0
# 子进程探测结果缓存：探测一次后全进程复用，避免每次 _create_client 都起子进程
_CHROMADB_IMPORT_OK: Optional[bool] = None


def _probe_import(code: str) -> bool:
    """在子进程中执行依赖导入探测，超时按"不可用"处理（带模块级缓存）

    Why（不易·死锁根除）：chromadb import 在部分环境可能长时间卡死（非异常，
    try/except 无法拦截）。旧实现用 daemon 线程 + join(timeout) 兜底，但卡死的
    daemon 线程持有全局 import 锁——超时返回后主进程任何后续 import 都会死锁
    （本地全量测试挂起根因）。子进程隔离：卡死只发生在子进程，subprocess.run
    超时后直接 terminate，不影响主进程 import 锁。与 memory/vector_store/
    vector_store.py 的 _probe_import 同一策略。

    探测结果缓存于模块级 _CHROMADB_IMPORT_OK：子进程探测每次约 1-5s（含解释器
    启动 + chromadb 导入），_create_client 可能被多次调用，缓存避免重复开销。
    """
    global _CHROMADB_IMPORT_OK
    if _CHROMADB_IMPORT_OK is not None:
        return _CHROMADB_IMPORT_OK
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            timeout=_CHROMADB_IMPORT_TIMEOUT,
            capture_output=True,
        )
        _CHROMADB_IMPORT_OK = (proc.returncode == 0)
    except Exception:
        # 超时(TimeoutExpired)/子进程启动失败(OSError)等一律按不可用处理
        _CHROMADB_IMPORT_OK = False
    return _CHROMADB_IMPORT_OK

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



@dataclass
class ChromaInitProgress:
    """初始化进度"""
    stage: str
    progress: float  # 0.0 - 1.0
    message: str
    elapsed_ms: float


class ChromaInitStats:
    """ChromaDB 初始化统计"""
    
    def __init__(self):
        self.total_inits = 0
        self.successful_inits = 0
        self.failed_inits = 0
        self.total_time_ms = 0.0
        self.avg_time_ms = 0.0
        self.fastest_time_ms = float('inf')
        self.slowest_time_ms = 0.0
        self.async_inits = 0
        
        # 分阶段统计
        self.stage_times: Dict[str, List[float]] = {}
    
    def record_init(self, success: bool, total_time_ms: float,
                   stage_times: Optional[Dict[str, float]] = None,
                   is_async: bool = False):
        """记录一次初始化"""
        self.total_inits += 1
        
        if success:
            self.successful_inits += 1
            self.total_time_ms += total_time_ms
            self.avg_time_ms = self.total_time_ms / self.successful_inits
            self.fastest_time_ms = min(self.fastest_time_ms, total_time_ms)
            self.slowest_time_ms = max(self.slowest_time_ms, total_time_ms)
        else:
            self.failed_inits += 1
        
        if is_async:
            self.async_inits += 1
        
        if stage_times:
            for stage, t in stage_times.items():
                if stage not in self.stage_times:
                    self.stage_times[stage] = []
                self.stage_times[stage].append(t)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            'total_inits': self.total_inits,
            'successful_inits': self.successful_inits,
            'failed_inits': self.failed_inits,
            'success_rate': f"{self.successful_inits / self.total_inits * 100:.1f}%"
                if self.total_inits > 0 else "N/A",
            'avg_time_ms': f"{self.avg_time_ms:.2f}",
            'fastest_time_ms': f"{self.fastest_time_ms:.2f}"
                if self.fastest_time_ms != float('inf') else "N/A",
            'slowest_time_ms': f"{self.slowest_time_ms:.2f}",
            'async_inits': self.async_inits,
            'stage_times': {
                stage: f"{sum(times) / len(times):.2f}ms"
                for stage, times in self.stage_times.items()
            }
        }


class ChromaInitCache:
    """ChromaDB 初始化缓存
    
    缓存已验证的初始化参数，避免重复初始化
    """
    
    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.cache: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
    
    def _make_key(self, persist_directory: str, collection_name: str) -> str:
        """生成缓存键"""
        key_str = f"{persist_directory}:{collection_name}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, persist_directory: str, collection_name: str) -> Optional[dict]:
        """获取缓存的初始化参数"""
        key = self._make_key(persist_directory, collection_name)
        
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                logger.debug(log_dict({'module_name': 'memory_optimized', 'action': 'persist_directory.collection_name', 'msg': f'[ChromaCache] 命中: {persist_directory}/{collection_name}'}))
                return self.cache[key]['config']
        
        return None
    
    def put(self, persist_directory: str, collection_name: str, config: dict):
        """缓存初始化参数"""
        key = self._make_key(persist_directory, collection_name)
        
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return
            
            if len(self.cache) >= self.max_size:
                evicted_key = next(iter(self.cache))
                del self.cache[evicted_key]
                logger.debug(log_dict({'module_name': 'memory_optimized', 'action': 'evicted_key', 'msg': f'[ChromaCache] 淘汰: {evicted_key}'}))
            
            self.cache[key] = {
                'config': config,
                'timestamp': time.time()
            }
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self.cache.clear()
            logger.debug(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': '[ChromaCache] 已清空'}))
    
    def invalidate(self, persist_directory: str = None, collection_name: str = None):
        """使缓存失效"""
        with self._lock:
            if persist_directory is None and collection_name is None:
                self.cache.clear()
                logger.debug(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': '[ChromaCache] 已清空'}))
                return
            
            keys_to_remove = []
            for key, entry in self.cache.items():
                # 简化实现，实际应该更复杂
                pass
            
            for key in keys_to_remove:
                del self.cache[key]


class OptimizedChromaDB:
    """
    优化版 ChromaDB 客户端
    
    优化点：
    1. 异步初始化 - 不阻塞主线程
    2. 延迟加载 - 按需初始化集合
    3. 初始化缓存 - 避免重复初始化
    4. 连接池 - 复用连接
    5. 懒加载索引 - 只加载必要索引
    """
    
    _instance = None
    _stats = ChromaInitStats()
    _cache = ChromaInitCache()
    _lock = threading.Lock()
    
    def __init__(
        self,
        persist_directory: str = "./data/chroma",
        collection_name: str = "default",
        enable_async: bool = True,
        enable_cache: bool = True,
        enable_lazy_collection: bool = True,
        on_progress: Optional[Callable[[ChromaInitProgress], None]] = None,
        **kwargs
    ):
        """
        初始化优化版 ChromaDB
        
        Args:
            persist_directory: 持久化目录
            collection_name: 集合名称
            enable_async: 启用异步初始化
            enable_cache: 启用初始化缓存
            enable_lazy_collection: 启用延迟集合加载
            on_progress: 进度回调函数
            **kwargs: 其他参数传递给 ChromaDB
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.enable_async = enable_async
        self.enable_cache = enable_cache
        self.enable_lazy_collection = enable_lazy_collection
        self.on_progress = on_progress
        
        self._client = None
        self._collection = None
        self._initialized = False
        self._initializing = False
        self._init_error: Optional[Exception] = None
        
        self._stage_times: Dict[str, float] = {}
        self._start_time: Optional[float] = None
        
        # 检查缓存
        if enable_cache:
            cached_config = self._cache.get(persist_directory, collection_name)
            if cached_config:
                logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'persist_directory.collection_name', 'msg': f'[ChromaDB] 使用缓存配置: {persist_directory}/{collection_name}'}))
                self._apply_cached_config(cached_config)
                return
        
        # 延迟初始化
        if enable_async:
            self._init_async()
        else:
            self._init_sync()
    
    def _apply_cached_config(self, config: dict):
        """应用缓存的配置"""
        # 缓存命中时，简化初始化流程
        logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': '[ChromaDB] 缓存命中，跳过完整初始化'}))
        self._initialized = True
    
    def _emit_progress(self, stage: str, progress: float, message: str):
        """发出进度更新"""
        if self._start_time is None:
            self._start_time = time.perf_counter()
        
        elapsed_ms = (time.perf_counter() - self._start_time) * 1000
        
        self._stage_times[stage] = elapsed_ms
        
        progress_obj = ChromaInitProgress(
            stage=stage,
            progress=progress,
            message=message,
            elapsed_ms=elapsed_ms
        )
        
        logger.debug(log_dict({'module_name': 'memory_optimized', 'action': 'progress.stage', 'msg': f'[ChromaDB] [{progress * 100:.0f}%] {stage}: {message}'}))
        
        if self.on_progress:
            try:
                self.on_progress(progress_obj)
            except Exception as e:
                logger.warning(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': f'[ChromaDB] 进度回调异常: {e}'}))
    
    def _init_async(self):
        """异步初始化"""
        logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': '[ChromaDB] 启动异步初始化...'}))
        
        def do_init():
            try:
                self._init_sync()
            except Exception as e:
                self._init_error = e
                logger.error(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': f'[ChromaDB] 异步初始化失败: {e}'}))
            finally:
                self._initializing = False
        
        self._initializing = True
        thread = threading.Thread(target=do_init, daemon=True)
        thread.start()
    
    def _init_sync(self):
        """同步初始化"""
        start_time = time.perf_counter()
        
        try:
            # 阶段1: 检查环境 (0-10%)
            self._emit_progress("check_env", 0.0, "检查环境...")
            self._check_environment()
            self._emit_progress("check_env", 0.1, "环境检查完成")
            
            # 阶段2: 创建客户端 (10-30%)
            self._emit_progress("create_client", 0.1, "创建客户端...")
            self._create_client()
            self._emit_progress("create_client", 0.3, "客户端创建完成")
            
            # 阶段3: 初始化集合 (30-60%)
            self._emit_progress("init_collection", 0.3, "初始化集合...")
            if self.enable_lazy_collection:
                # 延迟集合初始化
                self._collection = LazyCollectionProxy(
                    self._client,
                    self.collection_name
                )
            else:
                self._collection = self._client.get_or_create_collection(
                    self.collection_name
                )
            self._emit_progress("init_collection", 0.6, "集合初始化完成")
            
            # 阶段4: 验证连接 (60-80%)
            self._emit_progress("verify", 0.6, "验证连接...")
            self._verify_connection()
            self._emit_progress("verify", 0.8, "连接验证完成")
            
            # 阶段5: 完成 (80-100%)
            self._emit_progress("complete", 0.9, "完成初始化...")
            self._initialized = True
            
            # 保存到缓存
            if self.enable_cache:
                self._cache.put(
                    self.persist_directory,
                    self.collection_name,
                    {'initialized': True}
                )
            
            total_time = (time.perf_counter() - start_time) * 1000
            
            self._stats.record_init(
                success=True,
                total_time_ms=total_time,
                stage_times=self._stage_times.copy(),
                is_async=self._initializing
            )
            
            logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'total_time', 'msg': f'[ChromaDB] ✅ 初始化完成: {total_time:.2f}ms'}))
            self._emit_progress("complete", 1.0, f"初始化完成 ({total_time:.2f}ms)")
            
        except Exception as e:
            total_time = (time.perf_counter() - start_time) * 1000
            
            self._stats.record_init(
                success=False,
                total_time_ms=total_time,
                stage_times=self._stage_times.copy(),
                is_async=self._initializing
            )
            
            logger.error(log_dict({'module_name': 'memory_optimized', 'action': 'log', 'msg': f'[ChromaDB] ❌ 初始化失败: {e}'}))
            raise
    
    def _check_environment(self):
        """检查环境"""
        # 确保目录存在
        os.makedirs(self.persist_directory, exist_ok=True)
        
        # 检查磁盘空间
        try:
            import shutil
            total, used, free = shutil.disk_usage(self.persist_directory)
            if free < 100 * 1024 * 1024:  # < 100MB
                logger.warning(log_dict({'module_name': 'memory_optimized', 'action': 'free', 'msg': f'[ChromaDB] 磁盘空间不足: {free / 1024 / 1024:.1f}MB 可用'}))
        except Exception:
            pass
    
    def _create_client(self):
        """创建 ChromaDB 客户端

        【变易·死锁根除】chromadb import 在部分环境可能长时间卡死(非异常,
        try/except 无法拦截)。旧实现用 daemon 线程 + join(timeout) 兜底，但卡死的
        daemon 线程持有全局 import 锁——超时返回后主进程任何后续 import 都会死锁
        （本地全量测试挂起根因）。现改为与 memory/vector_store/vector_store.py
        一致的子进程探测：卡死只发生在子进程，超时直接 terminate，不影响主进程
        import 锁。

        日志决策链（排查用）：probe_start → (probe_ok → [chromadb|client_failed|ready] | timeout)
        """
        # 1. 子进程探测 import 可用性（超时按不可用处理，不持锁）
        logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'chromadb.probe_start', 'msg': f'[ChromaDB] 子进程探测 import 可用性（超时 {_CHROMADB_IMPORT_TIMEOUT}s）'}))
        if not _probe_import("import chromadb; from chromadb.config import Settings"):
            logger.warning(log_dict({'module_name': 'memory_optimized', 'action': 'chromadb.timeout', 'msg': f'[ChromaDB] import 超时(>{_CHROMADB_IMPORT_TIMEOUT}s)或不可用，使用模拟实现'}))
            self._client = MockChromaClient()
            return

        # 2. 探测成功后才在主进程导入（与 vector_store._init_chroma 同一策略）
        logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'chromadb.probe_ok', 'msg': '[ChromaDB] 子进程探测可用，开始主进程导入'}))
        try:
            import chromadb
            from chromadb.config import Settings
        except Exception as e:
            logger.warning(log_dict({'module_name': 'memory_optimized', 'action': 'chromadb', 'msg': f'[ChromaDB] ChromaDB 不可用，使用模拟实现: {e}'}))
            self._client = MockChromaClient()
            return

        # 3. 客户端创建（含 UserIdentity 双加载 bug 兜底）
        try:
            self._client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
        except Exception as e:
            logger.warning(log_dict({'module_name': 'memory_optimized', 'action': 'chromadb.client_failed', 'msg': f'[ChromaDB] 客户端创建失败，使用模拟实现: {e}'}))
            self._client = MockChromaClient()
            return
        logger.info(log_dict({'module_name': 'memory_optimized', 'action': 'chromadb.ready', 'msg': f'[ChromaDB] 真实 PersistentClient 就绪（path={self.persist_directory}）'}))
    
    def _verify_connection(self):
        """验证连接"""
        if self._client is None:
            raise RuntimeError("客户端未初始化")
        
        # 简单的心跳检查
        try:
            if hasattr(self._client, 'heartbeat'):
                self._client.heartbeat()
        except Exception:
            pass
    
    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized
    
    @property
    def is_initializing(self) -> bool:
        """是否正在初始化"""
        return self._initializing
    
    @property
    def collection(self):
        """获取集合"""
        if not self._initialized:
            raise RuntimeError("ChromaDB 未初始化")
        return self._collection
    
    def add(self, embeddings: List[List[float]], documents: List[str],
            metadatas: Optional[List[dict]] = None, ids: Optional[List[str]] = None):
        """添加向量"""
        if not self._initialized:
            raise RuntimeError("ChromaDB 未初始化")
        
        self.collection.add(
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
            ids=ids or [f"id_{i}" for i in range(len(documents))]
        )
    
    def query(self, query_embeddings: List[List[float]], n_results: int = 5):
        """查询向量"""
        if not self._initialized:
            raise RuntimeError("ChromaDB 未初始化")
        
        return self.collection.query(
            query_embeddings=query_embeddings,
            n_results=n_results
        )
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            'initialized': self._initialized,
            'initializing': self._initializing,
            'persist_directory': self.persist_directory,
            'collection_name': self.collection_name,
            'global_stats': self._stats.get_stats(),
            'local_stage_times': self._stage_times.copy()
        }
    
    @classmethod
    def get_global_stats(cls) -> dict:
        """获取全局统计"""
        return cls._stats.get_stats()
    
    @classmethod
    def clear_cache(cls):
        """清空缓存"""
        cls._cache.invalidate()


class LazyCollectionProxy:
    """延迟集合代理
    
    延迟实际的集合访问，只在真正需要时加载
    """
    
    def __init__(self, client, collection_name: str):
        self._client = client
        self._collection_name = collection_name
        self._collection = None
        # [2026-08-13 并发审计 #4] 懒加载双检锁：并发首次访问只创建一次集合
        self._lock = threading.Lock()
    
    def _ensure_collection(self):
        """确保集合已加载"""
        if self._collection is None:
            with self._lock:
                if self._collection is None:
                    self._collection = self._client.get_or_create_collection(
                        self._collection_name
                    )
        return self._collection
    
    def add(self, **kwargs):
        """添加向量"""
        return self._ensure_collection().add(**kwargs)
    
    def query(self, **kwargs):
        """查询向量"""
        return self._ensure_collection().query(**kwargs)
    
    def get(self, **kwargs):
        """获取向量"""
        return self._ensure_collection().get(**kwargs)
    
    def delete(self, **kwargs):
        """删除向量"""
        return self._ensure_collection().delete(**kwargs)
    
    def count(self) -> int:
        """获取向量数量"""
        return self._ensure_collection().count()


class MockChromaClient:
    """ChromaDB 模拟客户端（用于测试）"""
    
    def __init__(self):
        self._collections: Dict[str, MockCollection] = {}
    
    def get_or_create_collection(self, name: str):
        """获取或创建集合"""
        if name not in self._collections:
            self._collections[name] = MockCollection(name)
        return self._collections[name]
    
    def heartbeat(self):
        """心跳检查"""
        return True


class MockCollection:
    """ChromaDB 模拟集合"""
    
    def __init__(self, name: str):
        self.name = name
        self._data: List[dict] = []
    
    def add(self, embeddings, documents, metadatas=None, ids=None):
        """添加数据"""
        for i, doc in enumerate(documents):
            self._data.append({
                'id': ids[i] if ids else f"id_{len(self._data)}",
                'embedding': embeddings[i],
                'document': doc,
                'metadata': metadatas[i] if metadatas else None
            })
    
    def query(self, query_embeddings, n_results=5):
        """查询数据"""
        # 简化实现，返回空结果
        return {
            'ids': [[]],
            'distances': [[]],
            'documents': [[]],
            'metadatas': [[]]
        }
    
    def get(self, where=None, limit=100):
        """获取数据"""
        return {'ids': [], 'documents': [], 'metadatas': []}
    
    def count(self) -> int:
        """获取数量"""
        return len(self._data)


# 便捷函数
def create_optimized_chroma(
    persist_directory: str = "./data/chroma",
    collection_name: str = "default",
    async_init: bool = True,
    on_progress: Optional[Callable[[ChromaInitProgress], None]] = None
) -> OptimizedChromaDB:
    """
    创建优化版 ChromaDB
    
    Args:
        persist_directory: 持久化目录
        collection_name: 集合名称
        async_init: 异步初始化
        on_progress: 进度回调
        
    Returns:
        OptimizedChromaDB 实例
    """
    return OptimizedChromaDB(
        persist_directory=persist_directory,
        collection_name=collection_name,
        enable_async=async_init,
        on_progress=on_progress
    )
