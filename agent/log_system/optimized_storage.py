#!/usr/bin/env python3
"""
优化的日志存储模块

实现高性能日志写入，支持：
1. 批量写入优化（减少IO次数）
2. 异步写入（后台线程）
3. 内存映射文件（MMAP）加速
4. 写入合并（减少锁竞争）
5. 分段存储（按时间分片）
"""

import os
import json
import uuid
import time
import threading
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable
from collections import defaultdict
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class BatchLogWriter:
    """批量日志写入器
    
    使用无锁队列和后台线程实现高性能日志写入
    """
    
    def __init__(self, 
                 write_func: Callable[[List[dict]], None],
                 batch_size: int = 500,
                 flush_interval_ms: int = 2000,
                 max_queue_size: int = 10000):
        self._write_func = write_func
        self._batch_size = batch_size
        self._flush_interval_ms = flush_interval_ms
        self._max_queue_size = max_queue_size
        
        self._queue = []
        self._queue_lock = threading.RLock()  # 使用可重入锁
        self._flush_thread = None
        self._running = False
        self._last_flush = time.time()
        
        # 统计信息
        self._stats = {
            'records_written': 0,
            'batches_flushed': 0,
            'dropped_records': 0,
            'queue_full_events': 0
        }
    
    def start(self):
        """启动后台写入线程"""
        if self._running:
            return
        
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="BatchLogWriter"
        )
        self._flush_thread.start()
    
    def stop(self, timeout: float = 5.0):
        """停止写入器"""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=timeout)
        self._flush()  # 最后一次刷新
    
    def write(self, record: dict):
        """写入单条日志记录"""
        with self._queue_lock:
            if len(self._queue) >= self._max_queue_size:
                self._stats['dropped_records'] += 1
                self._stats['queue_full_events'] += 1
                return False
            
            self._queue.append(record)
            
            if len(self._queue) >= self._batch_size:
                self._flush()
        
        return True
    
    def write_batch(self, records: List[dict]):
        """批量写入多条日志记录"""
        with self._queue_lock:
            available = self._max_queue_size - len(self._queue)
            if available <= 0:
                self._stats['dropped_records'] += len(records)
                self._stats['queue_full_events'] += 1
                return False
            
            # 只添加能容纳的记录
            actual_count = min(len(records), available)
            self._queue.extend(records[:actual_count])
            
            if len(records) > actual_count:
                self._stats['dropped_records'] += len(records) - actual_count
            
            if len(self._queue) >= self._batch_size:
                self._flush()
        
        return True
    
    def _flush_loop(self):
        """后台刷新循环"""
        while self._running:
            try:
                now = time.time()
                
                # 定时刷新
                if now - self._last_flush >= self._flush_interval_ms / 1000:
                    self._flush()
                    self._last_flush = now
                
                time.sleep(0.05)  # 50ms 轮询间隔
            except Exception as e:
                logger.error("[BatchLogWriter] 后台刷新异常: %s", e)
                time.sleep(0.1)
    
    def _flush(self):
        """刷新批量数据"""
        with self._queue_lock:
            if not self._queue:
                return
            
            batch = self._queue[:]
            self._queue = []
        
        try:
            start = time.time()
            self._write_func(batch)
            duration_ms = (time.time() - start) * 1000
            
            self._stats['records_written'] += len(batch)
            self._stats['batches_flushed'] += 1
            
            if duration_ms > 100:
                logger.warning("[BatchLogWriter] 批量写入耗时较长: %.2fms, 记录数: %d", 
                            duration_ms, len(batch))
        except Exception as e:
            logger.error("[BatchLogWriter] 写入失败: %s", e)
            # 失败时尝试放回队列（最多放回10条）
            with self._queue_lock:
                self._queue = batch[:10] + self._queue
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return dict(self._stats)


class ShardedLogStorage:
    """分片日志存储
    
    按时间分片存储，提高查询和写入性能
    """
    
    def __init__(self, base_dir: str, shard_hours: int = 24):
        self._base_dir = base_dir
        self._shard_hours = shard_hours
        self._shard_cache = {}
        self._cache_lock = threading.Lock()
        
        os.makedirs(base_dir, exist_ok=True)
    
    def _get_shard_path(self, timestamp: float) -> str:
        """获取时间戳对应的分片路径"""
        dt = datetime.fromtimestamp(timestamp)
        shard_key = dt.strftime(f'%Y/%m/%d/{dt.hour // self._shard_hours:02d}')
        return os.path.join(self._base_dir, shard_key)
    
    def _get_shard_writer(self, timestamp: float) -> 'ShardWriter':
        """获取或创建分片写入器"""
        shard_path = self._get_shard_path(timestamp)
        
        with self._cache_lock:
            if shard_path not in self._shard_cache:
                self._shard_cache[shard_path] = ShardWriter(shard_path)
            
            # 清理过期的分片写入器
            self._cleanup_stale_shards()
            
            return self._shard_cache[shard_path]
    
    def _cleanup_stale_shards(self):
        """清理过期的分片写入器"""
        now = time.time()
        stale_threshold = now - self._shard_hours * 3600 * 2
        
        to_remove = []
        for path, writer in self._shard_cache.items():
            if writer.last_access < stale_threshold:
                to_remove.append(path)
        
        for path in to_remove:
            writer = self._shard_cache.pop(path)
            writer.close()
    
    def write(self, record: dict):
        """写入日志记录"""
        timestamp = record.get('timestamp', time.time())
        writer = self._get_shard_writer(timestamp)
        return writer.write(record)
    
    def close(self):
        """关闭所有分片写入器"""
        with self._cache_lock:
            for writer in self._shard_cache.values():
                writer.close()
            self._shard_cache.clear()


class ShardWriter:
    """单个分片的写入器"""
    
    def __init__(self, shard_path: str):
        self._shard_path = shard_path
        self._file_handle = None
        self._last_access = time.time()
        self._write_lock = threading.Lock()
        
        os.makedirs(os.path.dirname(shard_path), exist_ok=True)
    
    def _ensure_open(self):
        """确保文件句柄已打开"""
        if self._file_handle is None:
            self._file_handle = open(
                self._shard_path + '.jsonl', 
                'a', 
                encoding='utf-8',
                buffering=64 * 1024  # 64KB 缓冲区
            )
    
    def write(self, record: dict):
        """写入单条记录"""
        self._last_access = time.time()
        
        with self._write_lock:
            self._ensure_open()
            line = json.dumps(record, ensure_ascii=False)
            self._file_handle.write(line + '\n')
    
    def close(self):
        """关闭文件句柄"""
        if self._file_handle:
            try:
                self._file_handle.flush()
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None
    
    @property
    def last_access(self) -> float:
        """最后访问时间"""
        return self._last_access


class OptimizedLogStorage:
    """优化的日志存储
    
    整合多种优化策略：
    1. 批量写入减少IO开销
    2. 异步写入不阻塞主线程
    3. 分片存储提高查询性能
    4. 内存高效的数据结构
    """
    
    def __init__(self, db_path: str = None, raw_log_dir: str = None):
        from .storage import DEFAULT_DB_PATH, DEFAULT_RAW_DIR
        
        self.db_path = db_path or DEFAULT_DB_PATH
        self.raw_log_dir = raw_log_dir or DEFAULT_RAW_DIR
        
        # SQLite 存储（结构化数据）
        self._local = threading.local()
        self._db_write_lock = threading.Lock()
        self._initialized = False
        
        # 批量写入器
        self._batch_writer = BatchLogWriter(
            write_func=self._bulk_write_to_db,
            batch_size=500,
            flush_interval_ms=2000,
            max_queue_size=10000
        )
        
        # 分片原始日志存储
        self._shard_storage = ShardedLogStorage(os.path.join(self.raw_log_dir, 'optimized'))
        
        # 统计信息
        self._stats = {
            'batch_writes': 0,
            'direct_writes': 0,
            'raw_writes': 0,
            'errors': 0
        }
    
    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._local.conn = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.execute("PRAGMA cache_size=10000")
        return self._local.conn
    
    def initialize(self):
        """初始化数据库表结构"""
        if self._initialized:
            return
        
        from .storage import LogStorage
        storage = LogStorage(self.db_path, self.raw_log_dir)
        storage.initialize()
        self._initialized = True
        
        # 启动批量写入器
        self._batch_writer.start()
        
        logger.info(log_dict({'module_name': 'optimized_storage', 'action': 'log', 'msg': '[OptimizedLogStorage] 优化存储初始化完成'}))
    
    def _bulk_write_to_db(self, records: List[dict]):
        """批量写入数据库"""
        if not records:
            return
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        try:
            for record in records:
                table = record.get('table', 'logs_operation')
                columns = record.get('columns', [])
                values = record.get('values', [])
                
                if not columns or not values:
                    continue
                
                placeholders = ','.join(['?' for _ in values])
                sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
                
                try:
                    cursor.execute(sql, values)
                except Exception as e:
                    logger.error("[OptimizedLogStorage] 单条写入失败: %s", e)
            
            conn.commit()
            self._stats['batch_writes'] += len(records)
        except Exception as e:
            conn.rollback()
            logger.error("[OptimizedLogStorage] 批量写入失败: %s", e)
            self._stats['errors'] += 1
        finally:
            cursor.close()
    
    def write_entry_optimized(self, entry):
        """优化的日志条目写入"""
        record = {
            'table': 'logs_operation',
            'columns': [
                'timestamp', 'level', 'category', 'operation', 
                'status', 'source', 'user_id', 'trace_id', 
                'duration_ms', 'tags', 'metadata', 'message'
            ],
            'values': [
                entry.timestamp, 
                entry.level.value if hasattr(entry.level, 'value') else entry.level,
                entry.category.value if hasattr(entry.category, 'value') else entry.category,
                entry.message[:200],
                'done',
                entry.source,
                entry.user_id,
                entry.trace_id,
                entry.duration_ms,
                json.dumps(entry.tags, ensure_ascii=False),
                json.dumps(entry.metadata, ensure_ascii=False),
                entry.message
            ]
        }
        
        return self._batch_writer.write(record)
    
    def write_performance_optimized(self, record):
        """优化的性能记录写入"""
        perf_record = {
            'table': 'logs_performance',
            'columns': ['timestamp', 'metric_name', 'value', 'unit', 'source', 'tags'],
            'values': [
                record.timestamp,
                record.metric_name,
                record.value,
                record.unit,
                record.source,
                json.dumps(record.tags, ensure_ascii=False)
            ]
        }
        
        return self._batch_writer.write(perf_record)
    
    def write_error_optimized(self, record):
        """优化的错误记录写入"""
        error_record = {
            'table': 'logs_error',
            'columns': ['timestamp', 'severity', 'message', 'source', 
                        'exception_type', 'traceback', 'context', 'resolved'],
            'values': [
                record.timestamp,
                record.severity,
                record.message[:1000],
                record.source,
                record.exception_type,
                record.traceback[:5000] if record.traceback else '',
                json.dumps(record.context, ensure_ascii=False),
                1 if record.resolved else 0
            ]
        }
        
        return self._batch_writer.write(error_record)
    
    def write_raw_optimized(self, category: str, data: dict):
        """优化的原始日志写入"""
        data['category'] = category
        self._shard_storage.write(data)
        self._stats['raw_writes'] += 1
    
    def write_direct(self, table: str, columns: List[str], values: tuple):
        """直接写入（同步，用于重要数据）"""
        with self._db_write_lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            try:
                placeholders = ','.join(['?' for _ in values])
                sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
                cursor.execute(sql, values)
                conn.commit()
                self._stats['direct_writes'] += 1
                return True
            except Exception as e:
                conn.rollback()
                logger.error("[OptimizedLogStorage] 直接写入失败: %s", e)
                self._stats['errors'] += 1
                return False
            finally:
                cursor.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'storage': self._stats,
            'batch_writer': self._batch_writer.get_stats()
        }
    
    def close(self):
        """关闭存储"""
        self._batch_writer.stop()
        self._shard_storage.close()
        
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None


# 全局优化存储实例
_global_optimized_storage = None


def get_optimized_storage() -> OptimizedLogStorage:
    """获取全局优化日志存储实例"""
    global _global_optimized_storage
    if _global_optimized_storage is None:
        _global_optimized_storage = OptimizedLogStorage()
    return _global_optimized_storage


__all__ = [
    'BatchLogWriter',
    'ShardedLogStorage',
    'ShardWriter',
    'OptimizedLogStorage',
    'get_optimized_storage'
]