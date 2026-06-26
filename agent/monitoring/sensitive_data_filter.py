#!/usr/bin/env python3
"""
敏感数据自动过滤模块（向后兼容层）

核心功能已迁移至 agent.utils.sensitive_data_filter，
本模块提供向后兼容的导入接口。
"""

import logging
from typing import Dict, List, Any

from agent.utils.sensitive_data_filter import (
    SensitiveDataFilter,
    filter_sensitive_data,
    filter_dict as _filter_dict,
    filter_string as _filter_string,
    sensitive_filter,
    create_filter,
    REDACTED_VALUE,
    REDACTED_PARTIAL,
)

logger = logging.getLogger(__name__)


SENSITIVE_PATTERNS = [
    r'password', r'passwd', r'pwd', r'secret',
    r'api_?key', r'token', r'auth', r'credential',
    r'private_?key', r'privatekey', r'rsa_?key', r'ssh_?key',
    r'db_?pass', r'database_?password', r'mongo_?uri', r'redis_?password',
    r'jwt_?token', r'bearer_?token', r'access_?token', r'refresh_?token',
    r'authorization', r'x_api_key', r'x_auth',
    r'signature', r'sign', r'encrypt',
    r'session_?id', r'session_?token',
    r'certificate', r'cert_?key',
    r'client_?secret', r'app_?secret'
]

REDACT_PATTERNS = [
    r'^secret$', r'^token$', r'^password$', r'^pwd$',
    r'^api_?key$', r'^private_?key$', r'^access_?token$',
    r'^db_?pass(word)?$', r'^redis_?pass(word)?$',
    r'^mongo_?uri$', r'^client_?secret$'
]


def filter_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """过滤字典中的敏感字段（向后兼容）"""
    return _filter_dict(data)


def filter_string(text: str) -> str:
    """过滤字符串中的敏感信息（向后兼容）"""
    return _filter_string(text)


class AccessLogger:
    """可观测性端点访问日志记录器

    专门用于记录对可观测性端点的访问行为，
    支持审计和分析访问模式。
    """

    def __init__(self, log_file: str = None):
        """
        初始化访问日志记录器

        Args:
            log_file: 访问日志文件路径
        """
        import os
        self.log_file = log_file or os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'logs', 'observability_access.jsonl'
        )
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        self._logger = logging.getLogger(f"{__name__}.AccessLogger")

    def log_access(self, endpoint: str, method: str, client_ip: str,
                   user_agent: str = None, user_id: str = None,
                   trace_id: str = None, status_code: int = None,
                   response_time_ms: float = None, query_params: Dict = None):
        """记录端点访问

        Args:
            endpoint: 端点路径
            method: HTTP 方法
            client_ip: 客户端 IP
            user_agent: 用户代理
            user_id: 用户 ID（如果已认证）
            trace_id: 追踪 ID
            status_code: 响应状态码
            response_time_ms: 响应时间（毫秒）
            query_params: 查询参数
        """
        import time
        import json

        log_entry = {
            "timestamp": time.time(),
            "datetime": __import__('datetime').datetime.now().isoformat(),
            "endpoint": endpoint,
            "method": method,
            "client_ip": self._mask_ip(client_ip) if client_ip else None,
            "user_agent": user_agent,
            "user_id": user_id,
            "trace_id": trace_id,
            "status_code": status_code,
            "response_time_ms": round(response_time_ms, 2) if response_time_ms else None,
            "query_params": self._sanitize_params(query_params) if query_params else {}
        }

        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            self._logger.debug(f"[AccessLog] 记录访问: {endpoint} from {client_ip}")
        except Exception as e:
            self._logger.error(f"[AccessLog] 写入访问日志失败: {e}")

    def _mask_ip(self, ip: str) -> str:
        """脱敏 IP 地址（保留前两位）"""
        if not ip:
            return None
        parts = ip.split('.')
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.xxx.xxx"
        return ip

    def _sanitize_params(self, params: Dict) -> Dict:
        """清理查询参数中的敏感字段"""
        return filter_sensitive_data(params)

    def get_recent_access(self, limit: int = 100, endpoint: str = None,
                          start_time: float = None, end_time: float = None) -> List[Dict]:
        """获取最近的访问记录

        Args:
            limit: 返回条数
            endpoint: 按端点过滤
            start_time: 开始时间戳
            end_time: 结束时间戳

        Returns:
            访问记录列表
        """
        import json
        results = []

        if not __import__('os').path.exists(self.log_file):
            return results

        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())

                        if start_time and entry.get('timestamp', 0) < start_time:
                            continue
                        if end_time and entry.get('timestamp', 0) > end_time:
                            continue

                        if endpoint and endpoint not in entry.get('endpoint', ''):
                            continue

                        results.append(entry)
                    except json.JSONDecodeError:
                        continue

            results.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            return results[:limit]

        except Exception as e:
            self._logger.error(f"[AccessLog] 读取访问日志失败: {e}")
            return []

    def get_access_stats(self, start_time: float = None, end_time: float = None) -> Dict:
        """获取访问统计

        Args:
            start_time: 开始时间戳
            end_time: 结束时间戳

        Returns:
            统计信息
        """
        records = self.get_recent_access(limit=10000, start_time=start_time, end_time=end_time)

        if not records:
            return {
                "total_accesses": 0,
                "unique_endpoints": 0,
                "unique_ips": 0,
                "avg_response_time_ms": 0,
                "error_rate": 0
            }

        endpoints = set(r.get('endpoint') for r in records)
        ips = set(r.get('client_ip') for r in records if r.get('client_ip'))
        response_times = [r.get('response_time_ms', 0) for r in records if r.get('response_time_ms')]
        errors = [r for r in records if r.get('status_code', 200) >= 400]

        return {
            "total_accesses": len(records),
            "unique_endpoints": len(endpoints),
            "unique_ips": len(ips),
            "avg_response_time_ms": sum(response_times) / len(response_times) if response_times else 0,
            "error_rate": len(errors) / len(records) if records else 0,
            "status_codes": self._count_status_codes(records)
        }

    def _count_status_codes(self, records: List[Dict]) -> Dict[int, int]:
        """统计状态码分布"""
        codes = {}
        for r in records:
            code = r.get('status_code', 0)
            codes[code] = codes.get(code, 0) + 1
        return codes


_access_logger = None


def get_access_logger() -> AccessLogger:
    """获取全局访问日志记录器"""
    global _access_logger
    if _access_logger is None:
        _access_logger = AccessLogger()
    return _access_logger


__all__ = [
    'SensitiveDataFilter',
    'filter_sensitive_data',
    'filter_dict',
    'filter_string',
    'sensitive_filter',
    'create_filter',
    'AccessLogger',
    'get_access_logger',
    'REDACTED_VALUE',
    'REDACTED_PARTIAL',
    'SENSITIVE_PATTERNS',
    'REDACT_PATTERNS',
]
