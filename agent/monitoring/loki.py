#!/usr/bin/env python3
"""Loki 日志系统集成模块

提供与 Loki 的交互能力，支持日志查询、过滤和推送。
支持本地日志存储作为回退方案。
"""

import json
import logging
import os
import re
import time
import uuid
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime

import requests
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  本地回退日志查询口径（**单一来源**：过滤与时间窗都只在这里判定）
# ════════════════════════════════════════════════════════════

#: 无显式时间窗时，本地回退默认往前扫多少天。
#: 【为什么不是 1 小时】实测线上 data/logs 里最新一天常是"两天前"（夜间/停机不写日志），
#: 写死 1 小时会让本地回退**恒为空**，前端只能显示空表 —— 本仓实测如此。
DEFAULT_LOCAL_WINDOW_DAYS = 7

#: 查询串里 `key=value` 字段过滤 → labels 里可接受的键（一个请求键可有多个别名）。
#: service 落到 labels.app 是实测到的真实字段名（日志里是 {"app": "yunshu-route", ...}）。
_FIELD_LABEL_ALIASES = {
    'level': ('level', 'severity'),
    'severity': ('severity', 'level'),
    'service': ('service', 'app', 'application'),
    'app': ('app', 'service'),
    'logger': ('logger', 'module', 'name'),
    'module': ('module', 'logger'),
    'event': ('event',),
    'trace_id': ('trace_id', 'traceid', 'trace_id_ctx'),
    'host': ('host', 'instance'),
    'env': ('env', 'environment'),
}

#: 路由把 level/service 拼成 `|~ 'VALUE'` 管道过滤（见 routes_logging）：
#: 这类片段不能对整串做子串匹配，要拆出 VALUE 作为文本词（对 message + labels 一起匹配）。
_PIPE_FILTER_RE = re.compile(r"\|~?\s*(?:'([^']*)'|\"([^\"]*)\")")
#: `key=value` / `key="value"` 字段过滤
_FIELD_FILTER_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'|]+))"
)

#: 被当作"全匹配"的空查询（路由在无任何筛选时传 ".+"）
_MATCH_ALL_QUERIES = {'.+', '.*', '.+?'}


def _parse_local_query(query: str):
    """把 LogQL 风味查询串拆成 `(field_filters, text_terms)`。

    - 空 / None / ".+" ⇒ `({}, [])`，即**全匹配**（修复"无 query 时永远 0 条"）；
    - `level=INFO` / `service=xxx` ⇒ 字段过滤（修复"对整串做子串匹配必然不中"）；
    - `keyword` / `|~ 'ERROR'` ⇒ 文本词，对 message 与序列化后的 labels 一起子串匹配。
    """
    q = (query or '').strip()
    if not q or q in _MATCH_ALL_QUERIES:
        return {}, []

    text_terms = []
    for m in _PIPE_FILTER_RE.finditer(q):
        value = next((g for g in m.groups() if g), '')
        if value.strip():
            text_terms.append(value.strip())
    rest = _PIPE_FILTER_RE.sub(' ', q)

    field_filters = {}
    for m in _FIELD_FILTER_RE.finditer(rest):
        key = m.group(1).lower()
        value = next((g for g in m.groups()[1:] if g is not None), '')
        if value:
            field_filters[key] = value
    rest = _FIELD_FILTER_RE.sub(' ', rest)

    for token in rest.replace('|', ' ').split():
        token = token.strip().strip('\'"')
        if token and token not in _MATCH_ALL_QUERIES:
            text_terms.append(token)
    return field_filters, text_terms


def _entry_matches(entry: Dict, field_filters: Dict[str, str], text_terms: List[str]) -> bool:
    """条目是否命中已解析的过滤条件（labels 字段 + message/labels 文本）。"""
    labels = entry.get('labels') if isinstance(entry.get('labels'), dict) else {}
    lower_labels = {str(k).lower(): str(v) for k, v in labels.items()}

    for key, want in field_filters.items():
        candidates = _FIELD_LABEL_ALIASES.get(key, (key,))
        hit = any(
            cand in lower_labels and lower_labels[cand].lower() == want.lower()
            for cand in candidates
        )
        if not hit:
            return False

    if text_terms:
        haystack = (str(entry.get('message', '')) + ' ' + json.dumps(labels, ensure_ascii=False)).lower()
        if any(term.lower() not in haystack for term in text_terms):
            return False
    return True


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
        
        logger.info(log_dict({'module_name': 'loki', 'action': 'loki.client.init', 'enabled': self._enabled, 'url': self._url}))
    
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
        """推送日志到 Loki
        
        Args:
            labels: 日志标签
            message: 日志消息
            timestamp: 时间戳（可选）
        """
        if not self._enabled:
            # 回退到本地存储
            self._save_local_log({
                'timestamp': timestamp or time.time(),
                'labels': labels,
                'message': message
            })
            return
        
        try:
            timestamp_ns = int((timestamp or time.time()) * 1e9)
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
                    'timestamp': timestamp or time.time(),
                    'labels': labels,
                    'message': message
                })
            else:
                logger.debug(f"[Loki] 日志推送成功")
        except Exception as e:
            logger.error(log_dict({'module_name': 'loki', 'action': 'loki.push.exception', 'error': str(e)}))
            # 回退到本地存储
            self._save_local_log({
                'timestamp': timestamp or time.time(),
                'labels': labels,
                'message': message
            })
    
    def query_logs(self, query: str, start_time: float = None, end_time: float = None, limit: int = 100,
                   meta: Optional[Dict] = None) -> List[Dict]:
        """查询 Loki 日志
        
        Args:
            query: LogQL 查询语句
            start_time: 开始时间（Unix 时间戳）
            end_time: 结束时间（Unix 时间戳）
            limit: 返回条数限制
            meta: 可选出参 dict；本地回退时填入实际扫描窗口/文件（供端点如实上屏）
        
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
            results = self._query_local_logs(query, start_time, end_time, limit, meta=meta)
        
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
    
    def _list_local_log_files(self) -> List[tuple]:
        """列出本地目录里形如 YYYY-MM-DD.jsonl 的文件，按日期升序。

        【为什么只认日期名】同目录还有 observability_access.jsonl 等非按天切分的文件，
        把它们塞进"按天窗口"会读出与窗口不符的条目。
        """
        files = []
        try:
            for filename in os.listdir(self._local_log_dir):
                if not filename.endswith('.jsonl'):
                    continue
                stem = filename[:-len('.jsonl')]
                try:
                    datetime.strptime(stem, '%Y-%m-%d')
                except ValueError:
                    continue
                files.append((stem, os.path.join(self._local_log_dir, filename)))
        except OSError:
            return []
        files.sort(key=lambda item: item[0])
        return files

    def _query_local_logs(self, query: str, start_time: float = None, end_time: float = None,
                          limit: int = 100, meta: Optional[Dict] = None) -> List[Dict]:
        """从本地文件查询日志（过滤与时间窗的**单一来源**）。

        Args:
            query: LogQL 风味查询；空 / ".+" 视为**全匹配**，`key=value` 视为字段过滤
            start_time / end_time: Unix 秒；都缺省时用 DEFAULT_LOCAL_WINDOW_DAYS 天窗口
            limit: 返回条数限制
            meta: 可选出参 dict；填入实际扫描的窗口/文件，供端点如实上屏（诊断空结果）

        Returns:
            日志条目列表
        """
        results: List[Dict] = []
        field_filters, text_terms = _parse_local_query(query)

        try:
            explicit_start = start_time is not None
            now = time.time()
            # 默认时间窗放宽到 DEFAULT_LOCAL_WINDOW_DAYS 天：日志文件可能停写一两天。
            if start_time is None:
                start_time = now - DEFAULT_LOCAL_WINDOW_DAYS * 86400
            if end_time is None:
                end_time = now

            start_date = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d')
            end_date = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d')

            candidates = [
                (d, p) for d, p in self._list_local_log_files()
                if start_date <= d <= end_date
            ]
            # 默认窗口内一个文件都没有时，退回"磁盘上最新的 N 个"：
            # 日志停写几天（夜间/停机）不该让本地回退彻底失明。
            used_fallback_files = False
            if not candidates and not explicit_start:
                candidates = self._list_local_log_files()[-DEFAULT_LOCAL_WINDOW_DAYS:]
                used_fallback_files = True

            scanned = []
            # 由新到旧读，收满 limit 即停：默认扫 7 天，但通常只碰最新一两个文件。
            for date_str, file_path in reversed(candidates):
                scanned.append(os.path.basename(file_path))
                with open(file_path, 'r', encoding='utf-8') as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(entry, dict):
                            continue
                        entry_timestamp = entry.get('timestamp')
                        if isinstance(entry_timestamp, (int, float)):
                            if entry_timestamp < start_time or entry_timestamp > end_time:
                                continue
                        if not _entry_matches(entry, field_filters, text_terms):
                            continue
                        entry['source'] = 'local'
                        results.append(entry)
                if len(results) >= limit:
                    break

            # 按时间戳降序排序
            results.sort(key=lambda x: x.get('timestamp', 0) or 0, reverse=True)
            results = results[:limit]

            if meta is not None:
                meta.update({
                    'source': 'local',
                    'window_start': datetime.fromtimestamp(start_time).isoformat(timespec='seconds'),
                    'window_end': datetime.fromtimestamp(end_time).isoformat(timespec='seconds'),
                    'window_days': None if explicit_start else DEFAULT_LOCAL_WINDOW_DAYS,
                    'files_scanned': list(reversed(scanned)),
                    'files_in_window': [d for d, _ in candidates],
                    'used_latest_files_fallback': used_fallback_files,
                    'matched': len(results),
                    'filters': {**field_filters, 'text_terms': text_terms},
                })

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
    register_singleton("loki_client", _create_loki_client)


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


def query_loki_logs(query: str, start_time: float = None, end_time: float = None, limit: int = 100,
                    meta: Optional[Dict] = None) -> List[Dict]:
    """查询 Loki 日志的便捷函数
    
    Args:
        query: 查询字符串
        start_time: 开始时间
        end_time: 结束时间
        limit: 返回条数限制
        meta: 可选出参 dict；本地回退时填入实际扫描窗口/文件（供端点如实上屏）
    
    Returns:
        日志条目列表
    """
    client = get_loki_client()
    return client.query_logs(
        query=query,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        meta=meta,
    )


def get_loki_labels() -> Dict[str, List[str]]:
    """获取 Loki 标签的便捷函数"""
    client = get_loki_client()
    return client.get_labels()