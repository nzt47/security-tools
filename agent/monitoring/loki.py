#!/usr/bin/env python3
"""Loki 日志系统集成模块

提供与 Loki 的交互能力，支持日志查询、过滤和推送。
支持本地日志存储作为回退方案。
"""

import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

import requests
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


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
_loki_client = None


def get_loki_client() -> LokiClient:
    """获取 Loki 客户端实例"""
    global _loki_client
    if _loki_client is None:
        _loki_client = LokiClient()
    return _loki_client


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