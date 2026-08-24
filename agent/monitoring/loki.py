#!/usr/bin/env python3
"""Loki 日志系统集成模块

提供与 Loki 的交互能力，支持日志查询、过滤和推送。
支持本地日志存储作为回退方案。
"""

import json
import logging
import os
import queue
import time
import uuid
import threading
import atexit
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

import requests
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  异步批量推送参数（2026-08-24 优化：同步 HTTP → 队列 + 后台批量）
#  Why: push_log 原为同步 requests.post，请求热路径上会阻塞调用方
#  （网络 I/O + 锁），高频日志场景放大延迟。改为入队即返回，
#  后台线程批量聚合后推送，兼顾吞吐与失败降级（本地回退）。
#  【不易】调用方契约不变：push_log 仍同步返回（入队即返回），
#          异常仍内部降级本地文件，不向上抛。
#  【变易】队列上限防内存膨胀：超限时退化为同步推送（背压），
#          不丢日志也不 OOM。
# ─────────────────────────────────────────────────────────────
_LOKI_QUEUE_MAX = 1000
_LOKI_BATCH_SIZE = 20
_LOKI_FLUSH_INTERVAL = 2.0  # 秒；队列未满时最长等待后批量推送
_LOKI_WORKER_ENV = "LOKI_ASYNC_PUSH"  # "0"/"false" 关闭异步，退化为同步


def _trace_id():
    """生成简短 trace_id"""
    return uuid.uuid4().hex[:16]


class LokiClient:
    """Loki 日志客户端
    
    提供日志查询和推送功能，支持本地回退存储。
    """
    
    def __init__(self, url: str = None, enabled: bool = True):
        """初始化 Loki 客户端
        
        Args:
            url: Loki 服务地址
            enabled: 是否启用 Loki 集成
        """
        self._url = url or os.environ.get("LOKI_URL", "http://localhost:3100")
        self._enabled = enabled and self._url
        self._session = requests.Session()

        # 配置化超时（支持热加载，每次初始化时读取最新值）
        try:
            from agent.monitoring.observability_config import get_loki_push_timeout, get_loki_query_timeout
            self._push_timeout = get_loki_push_timeout()
            self._query_timeout = get_loki_query_timeout()
        except Exception:
            self._push_timeout = 10
            self._query_timeout = 30
        
        # 本地日志存储目录
        self._local_log_dir = os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'logs'
        )
        os.makedirs(self._local_log_dir, exist_ok=True)

        # 【变易】异步批量推送（默认开启，LOKI_ASYNC_PUSH=0 退化为同步）
        self._async_enabled = os.environ.get(_LOKI_WORKER_ENV, "1").lower() not in (
            "0", "false", "off", "no"
        )
        self._push_queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=_LOKI_QUEUE_MAX)
        self._worker_stop = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()  # 保护 worker 生命周期（幂等启动/停止）
        if self._async_enabled:
            self._start_worker()

        logger.info(log_dict({'module_name': 'loki', 'action': 'loki.client.init', 'enabled': self._enabled, 'url': self._url, 'async_push': self._async_enabled}))

    # ── 异步批量推送 worker ──

    def _start_worker(self) -> None:
        """启动后台批量推送线程（幂等：已启动则 no-op）"""
        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_stop.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="loki-push-worker",
                daemon=True,  # 不阻塞进程退出；未 flush 的日志由 _shutdown 兜底
            )
            self._worker_thread.start()

    def _worker_loop(self) -> None:
        """后台线程：批量聚合队列日志 → 推送（按 labels 分组，减少请求数）"""
        while not self._worker_stop.is_set():
            batch: list[tuple] = []
            deadline = time.monotonic() + _LOKI_FLUSH_INTERVAL
            # 收集一批（最多 _LOKI_BATCH_SIZE 条，或等待 flush 间隔）
            while len(batch) < _LOKI_BATCH_SIZE and time.monotonic() < deadline:
                try:
                    item = self._push_queue.get(timeout=max(0.1, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if item is None:  # 哨兵：shutdown 信号
                    self._flush_batch(batch)
                    return
                batch.append(item)
            if batch:
                self._flush_batch(batch)
        # 收到停止信号后，把队列剩余全部 flush
        self._drain_and_flush()

    def _drain_and_flush(self) -> None:
        """shutdown 后把队列剩余条目全部推送（不丢失已入队日志）"""
        remaining: list[tuple] = []
        while True:
            try:
                item = self._push_queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                remaining.append(item)
        if remaining:
            self._flush_batch(remaining)

    def _flush_batch(self, batch: list[tuple]) -> None:
        """把一批日志按 labels 分组合并推送（同 labels 合并为多 values 单请求）"""
        if not batch:
            return
        grouped: dict[tuple, list[tuple]] = {}
        for labels, message, timestamp in batch:
            key = tuple(sorted(labels.items()))
            grouped.setdefault(key, []).append((timestamp, message))
        for key, values in grouped.items():
            labels_dict = dict(key)
            try:
                payload = {
                    "streams": [{
                        "stream": labels_dict,
                        "values": [[str(int(ts * 1e9)), msg] for ts, msg in values],
                    }]
                }
                response = self._session.post(
                    f"{self._url}/loki/api/v1/push",
                    json=payload,
                    timeout=self._push_timeout,
                )
                if response.status_code != 204:
                    logger.error(log_dict({'module_name': 'loki', 'action': 'loki.push.failed', 'status_code': response.status_code, 'response_text': response.text[:200]}))
                    # 回退到本地存储（逐条，保留时间戳）
                    for ts, msg in values:
                        self._save_local_log({
                            'timestamp': ts,
                            'labels': labels_dict,
                            'message': msg,
                        })
                else:
                    logger.debug(f"[Loki] 日志批量推送成功 batch={len(values)}")
            except Exception as e:
                logger.error(log_dict({'module_name': 'loki', 'action': 'loki.push.exception', 'error': str(e)}))
                for ts, msg in values:
                    self._save_local_log({
                        'timestamp': ts,
                        'labels': labels_dict,
                        'message': msg,
                    })

    def _shutdown(self) -> None:
        """关闭 worker：发送哨兵 + 等待 drain（进程退出/单例重置时调用）"""
        if not self._async_enabled:
            return
        with self._worker_lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                return
            try:
                self._push_queue.put_nowait(None)  # 哨兵触发 drain
            except queue.Full:
                pass  # 队列满时 worker 会在 next loop 的 drain 兜底
            self._worker_stop.set()
            self._worker_thread.join(timeout=_LOKI_FLUSH_INTERVAL + 1.0)
            self._worker_thread = None
    
    def is_enabled(self) -> bool:
        """检查 Loki 是否启用"""
        return self._enabled
    
    def _save_local_log(self, log_entry: Dict):
        """保存日志到本地文件（回退方案）"""
        try:
            timestamp = log_entry.get('timestamp', time.time())
            date_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
            file_path = os.path.join(self._local_log_dir, f'{date_str}.jsonl')
            
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(log_dict({'module_name': 'loki', 'action': 'loki.save_local.failed', 'error': str(e)}))
    
    def push_log(self, labels: Dict[str, str], message: str, timestamp: float = None):
        """推送日志到 Loki（入队即返回，后台线程批量推送）

        Args:
            labels: 日志标签
            message: 日志消息
            timestamp: 时间戳（可选）

        【不易】调用方契约不变：同步返回、异常内部降级本地文件、不向上抛。
        【变易】异步模式入队即返回；队列满（背压）或 LOKI_ASYNC_PUSH=0
                退化为同步推送（不丢日志，代价是阻塞调用方）。
        """
        if not self._enabled:
            # 回退到本地存储
            self._save_local_log({
                'timestamp': timestamp or time.time(),
                'labels': labels,
                'message': message
            })
            return

        ts = timestamp or time.time()
        if self._async_enabled and self._worker_thread is not None:
            try:
                self._push_queue.put_nowait((labels, message, ts))
                return  # 入队成功，异步推送
            except queue.Full:
                # 背压：队列满，退化为同步推送（不丢日志）
                logger.warning(log_dict({
                    'module_name': 'loki', 'action': 'loki.push.queue_full_sync_fallback',
                }))
        # 同步路径（禁用异步 / worker 未启动 / 队列满背压）
        self._push_sync(labels, message, ts)

    def _push_sync(self, labels: Dict[str, str], message: str, timestamp: float) -> None:
        """同步推送单条日志（异步禁用/背压回退路径，保留原实现语义）"""
        try:
            timestamp_ns = int(timestamp * 1e9)
            payload = {
                "streams": [{
                    "stream": labels,
                    "values": [[str(timestamp_ns), message]]
                }]
            }

            response = self._session.post(
                f"{self._url}/loki/api/v1/push",
                json=payload,
                timeout=self._push_timeout
            )

            if response.status_code != 204:
                logger.error(log_dict({'module_name': 'loki', 'action': 'loki.push.failed', 'status_code': response.status_code, 'response_text': response.text[:200]}))
                # 回退到本地存储
                self._save_local_log({
                    'timestamp': timestamp,
                    'labels': labels,
                    'message': message
                })
            else:
                logger.debug(f"[Loki] 日志推送成功")
        except Exception as e:
            logger.error(log_dict({'module_name': 'loki', 'action': 'loki.push.exception', 'error': str(e)}))
            # 回退到本地存储
            self._save_local_log({
                'timestamp': timestamp,
                'labels': labels,
                'message': message
            })
    
    def query_logs(self, query: str, start_time: float = None, end_time: float = None, limit: int = 100) -> List[Dict]:
        """查询 Loki 日志
        
        Args:
            query: LogQL 查询语句
            start_time: 开始时间（Unix 时间戳）
            end_time: 结束时间（Unix 时间戳）
            limit: 返回条数限制
        
        Returns:
            日志条目列表
        """
        results = []
        
        if self._enabled:
            try:
                params = {
                    'query': query,
                    'limit': limit
                }
                
                if start_time:
                    params['start'] = int(start_time * 1e9)
                else:
                    params['start'] = int((time.time() - 3600) * 1e9)  # 默认过去1小时
                
                if end_time:
                    params['end'] = int(end_time * 1e9)
                else:
                    params['end'] = int(time.time() * 1e9)
                
                response = self._session.get(
                    f"{self._url}/loki/api/v1/query_range",
                    params=params,
                    timeout=self._query_timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    results = self._parse_loki_response(data)
                else:
                    logger.error(log_dict({'module_name': 'loki', 'action': 'loki.query.failed', 'status_code': response.status_code, 'response_text': response.text[:200]}))
            except Exception as e:
                logger.error(log_dict({'module_name': 'loki', 'action': 'loki.query.exception', 'error': str(e)}))
        
        # 如果 Loki 查询失败或未启用，从本地存储查询
        if not results:
            results = self._query_local_logs(query, start_time, end_time, limit)
        
        return results
    
    def _parse_loki_response(self, data: Dict) -> List[Dict]:
        """解析 Loki 响应数据"""
        results = []
        
        try:
            if data.get('status') == 'success':
                for result in data.get('data', {}).get('result', []):
                    for point in result.get('values', []):
                        timestamp_ns = int(point[0])
                        message = point[1]
                        
                        results.append({
                            'timestamp': timestamp_ns / 1e9,
                            'labels': result.get('stream', {}),
                            'message': message,
                            'source': 'loki'
                        })
        except Exception as e:
            logger.error(log_dict({'module_name': 'loki', 'action': 'loki.parse_response.failed', 'error': str(e)}))
        
        return results
    
    def _query_local_logs(self, query: str, start_time: float = None, end_time: float = None, limit: int = 100) -> List[Dict]:
        """从本地文件查询日志
        
        Args:
            query: 查询条件（简单字符串匹配）
            start_time: 开始时间
            end_time: 结束时间
            limit: 返回条数限制
        
        Returns:
            日志条目列表
        """
        results = []
        
        try:
            # 确定要搜索的日期范围
            if start_time:
                start_date = datetime.fromtimestamp(start_time)
            else:
                start_date = datetime.now() - timedelta(hours=1)
            
            if end_time:
                end_date = datetime.fromtimestamp(end_time)
            else:
                end_date = datetime.now()
            
            # 遍历日期范围内的日志文件
            current_date = start_date
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                file_path = os.path.join(self._local_log_dir, f'{date_str}.jsonl')
                
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                entry = json.loads(line.strip())
                                entry_timestamp = entry.get('timestamp', 0)
                                
                                # 时间范围过滤
                                if start_time and entry_timestamp < start_time:
                                    continue
                                if end_time and entry_timestamp > end_time:
                                    continue
                                
                                # 查询匹配（简单字符串匹配）
                                message = entry.get('message', '')
                                labels_str = json.dumps(entry.get('labels', {}))
                                
                                if query.lower() in message.lower() or \
                                   query.lower() in labels_str.lower():
                                    entry['source'] = 'local'
                                    results.append(entry)
                            except json.JSONDecodeError:
                                continue
                
                current_date += timedelta(days=1)
            
            # 按时间戳降序排序
            results.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            results = results[:limit]
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'loki', 'action': 'loki.query_local.failed', 'error': str(e)}))
        
        return results
    
    def get_labels(self) -> Dict[str, List[str]]:
        """获取所有可用的标签"""
        labels = {}
        
        if self._enabled:
            try:
                response = self._session.get(
                    f"{self._url}/loki/api/v1/labels",
                    timeout=self._query_timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('status') == 'success':
                        labels = data.get('data', {})
            except Exception as e:
                logger.error(log_dict({'module_name': 'loki', 'action': 'loki.get_labels.failed', 'error': str(e)}))
        
        # 如果 Loki 查询失败，从本地存储获取标签
        if not labels:
            labels = self._get_local_labels()
        
        return labels
    
    def _get_local_labels(self) -> Dict[str, List[str]]:
        """从本地日志文件获取标签"""
        labels = {}
        
        try:
            for filename in os.listdir(self._local_log_dir):
                if filename.endswith('.jsonl'):
                    file_path = os.path.join(self._local_log_dir, filename)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                entry = json.loads(line.strip())
                                entry_labels = entry.get('labels', {})
                                for key, value in entry_labels.items():
                                    if key not in labels:
                                        labels[key] = []
                                    if value not in labels[key]:
                                        labels[key].append(value)
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.error(log_dict({'module_name': 'loki', 'action': 'loki.get_local_labels.failed', 'error': str(e)}))
        
        return labels


# 全局单例
_loki_client = None  # 保留作为 fallback
# [2026-08-13 并发审计] fallback 单例双检锁：防并发首调创建多个实例
_loki_client_lock = threading.Lock()

try:
    from agent.utils.singleton_manager import register_singleton, get_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None


def _create_loki_client(config=None):
    """LokiClient 工厂函数（供 SingletonManager 使用）"""
    return LokiClient()


def _cleanup_loki_client(inst: LokiClient) -> None:
    """单例清理：flush 队列 + 停 worker（防退出丢日志）"""
    try:
        inst._shutdown()
    except Exception:  # noqa: BLE001
        pass


def get_loki_client() -> LokiClient:
    """获取 Loki 客户端实例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("loki_client")
    global _loki_client
    if _loki_client is None:
        # [2026-08-13 并发审计] fallback 双检锁：防并发首调创建多个实例
        with _loki_client_lock:
            if _loki_client is None:
                _loki_client = _create_loki_client()
    return _loki_client


if _SINGLETON_AVAILABLE:
    register_singleton(
        "loki_client", _create_loki_client, cleanup_fn=_cleanup_loki_client,
    )


def _atexit_flush_loki() -> None:
    """进程退出兜底：flush 队列防丢日志（单例已创建时才需要）"""
    if _SINGLETON_AVAILABLE:
        try:
            from agent.utils.singleton_manager import get_singleton
            inst = get_singleton("loki_client")
            if inst is not None:
                _cleanup_loki_client(inst)
        except Exception:  # noqa: BLE001
            pass
    else:
        global _loki_client
        if _loki_client is not None:
            _cleanup_loki_client(_loki_client)


atexit.register(_atexit_flush_loki)


def log_to_loki(message: str, labels: Dict[str, str] = None, timestamp: float = None):
    """推送日志到 Loki 的便捷函数
    
    Args:
        message: 日志消息
        labels: 标签字典
        timestamp: 时间戳
    """
    client = get_loki_client()
    client.push_log(
        labels=labels or {},
        message=message,
        timestamp=timestamp
    )


def query_loki_logs(query: str, start_time: float = None, end_time: float = None, limit: int = 100) -> List[Dict]:
    """查询 Loki 日志的便捷函数
    
    Args:
        query: 查询字符串
        start_time: 开始时间
        end_time: 结束时间
        limit: 返回条数限制
    
    Returns:
        日志条目列表
    """
    client = get_loki_client()
    return client.query_logs(
        query=query,
        start_time=start_time,
        end_time=end_time,
        limit=limit
    )


def get_loki_labels() -> Dict[str, List[str]]:
    """获取 Loki 标签的便捷函数"""
    client = get_loki_client()
    return client.get_labels()