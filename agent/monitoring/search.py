"""
搜索引擎性能检测模块

功能：
1. 定期自动检测搜索引擎性能
2. 记录性能统计数据
3. 检测降级机制是否正常工作
4. 生成性能报告

合并自：agent/search_performance_monitor.py
"""

import os
import json
import time
import logging
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

# 结构化日志必需：get_trace_id() 提供上下文追踪 ID
# set_trace_id() 用于跨线程传递 trace_id（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id

logger = logging.getLogger(__name__)

# 性能检测数据文件
PERFORMANCE_DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "search_performance.json"
)


class SearchPerformanceMonitor:
    """搜索引擎性能监控器"""

    def __init__(self, base_url: str = "http://localhost:5678"):
        self.base_url = base_url
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._interval = 300  # 默认 5 分钟检测一次
        self._performance_history: List[Dict] = []
        self._last_check_time: Optional[datetime] = None
        self._check_count = 0
        # 模块专属 trace_id，用于后台线程中保持结构化日志的追踪链路
        # Python ContextVar 不自动继承到子线程，需在 _monitor_loop 入口显式 set_trace_id
        self._monitor_trace_id = f"search-monitor-{uuid.uuid4().hex[:16]}"

        # 配置化超时（支持热加载，每次初始化时读取最新值）
        try:
            from agent.monitoring.observability_config import (
                get_search_thread_join_timeout,
                get_search_config_apply_timeout,
                get_search_web_search_timeout,
                get_search_status_check_timeout,
            )
            self._thread_join_timeout = get_search_thread_join_timeout()
            self._config_apply_timeout = get_search_config_apply_timeout()
            self._web_search_timeout = get_search_web_search_timeout()
            self._status_check_timeout = get_search_status_check_timeout()
        except Exception:
            self._thread_join_timeout = 5
            self._config_apply_timeout = 10
            self._web_search_timeout = 30
            self._status_check_timeout = 10

        self._load_performance_data()

    def _load_performance_data(self):
        """加载性能历史数据"""
        try:
            if os.path.exists(PERFORMANCE_DATA_FILE):
                with open(PERFORMANCE_DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._performance_history = data.get('history', [])
                    self._check_count = data.get('check_count', 0)
                    logger.info(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "search_monitor",
                        "action": "load_history",
                        "duration_ms": 0,
                        "history_count": len(self._performance_history),
                    }, ensure_ascii=False))
        except Exception as e:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "load_history_error",
                "duration_ms": 0,
                "error": str(e),
            }, ensure_ascii=False))

    def _save_performance_data(self):
        """保存性能历史数据"""
        try:
            os.makedirs(os.path.dirname(PERFORMANCE_DATA_FILE), exist_ok=True)
            data = {
                'history': self._performance_history[-100:],
                'check_count': self._check_count,
                'last_check': self._last_check_time.isoformat() if self._last_check_time else None,
            }
            with open(PERFORMANCE_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "save_history_error",
                "duration_ms": 0,
                "error": str(e),
            }, ensure_ascii=False))

    def set_interval(self, interval_sec: int):
        """设置检测间隔"""
        self._interval = interval_sec
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "search_monitor",
            "action": "set_interval",
            "duration_ms": 0,
            "interval_sec": interval_sec,
        }, ensure_ascii=False))

    def start(self):
        """启动性能监控"""
        if self._running:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "start_duplicate",
                "duration_ms": 0,
            }, ensure_ascii=False))
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "search_monitor",
            "action": "start",
            "duration_ms": 0,
            "interval_sec": self._interval,
        }, ensure_ascii=False))

    def stop(self):
        """停止性能监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._thread_join_timeout)
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "search_monitor",
            "action": "stop",
            "duration_ms": 0,
        }, ensure_ascii=False))

    def _monitor_loop(self):
        """监控循环"""
        # 后台线程入口：显式设置 trace_id，解决 ContextVar 不跨线程继承导致 get_trace_id() 返回 None 的问题
        set_trace_id(self._monitor_trace_id)
        while self._running:
            try:
                self._perform_check()
                for _ in range(self._interval):
                    if not self._running:
                        break
                    time.sleep(1)
            except Exception as e:
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "search_monitor",
                    "action": "monitor_loop_error",
                    "duration_ms": 0,
                    "error": str(e),
                }, ensure_ascii=False))
                time.sleep(60)

    def _perform_check(self):
        """执行性能检测"""
        import requests

        self._check_count += 1
        self._last_check_time = datetime.now()

        # 合并原分隔线日志为单条结构化日志（跳过纯分隔线 logger.info("=" * 80)）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "search_monitor",
            "action": "check_start",
            "duration_ms": 0,
            "check_id": self._check_count,
        }, ensure_ascii=False))

        check_result = {
            'check_id': self._check_count,
            'timestamp': self._last_check_time.isoformat(),
            'engines': {},
            'status': 'ok',
            'errors': [],
        }

        try:
            # 1. 应用配置
            try:
                r = requests.post(f"{self.base_url}/api/apply-network-config", timeout=self._config_apply_timeout)
                if r.json().get('ok'):
                    logger.info(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "search_monitor",
                        "action": "config_applied",
                        "duration_ms": 0,
                    }, ensure_ascii=False))
                else:
                    logger.warning(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "search_monitor",
                        "action": "config_apply_failed",
                        "duration_ms": 0,
                    }, ensure_ascii=False))
                    check_result['errors'].append("配置应用失败")
            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "search_monitor",
                    "action": "config_apply_error",
                    "duration_ms": 0,
                    "error": str(e),
                }, ensure_ascii=False))
                check_result['errors'].append(f"配置应用异常: {e}")

            # 2. 测试 Tavily 搜索
            try:
                start_time = time.time()
                r = requests.get(
                    f"{self.base_url}/api/web/search",
                    params={'query': '人工智能最新发展', 'num_results': 3, 'engine': 'tavily'},
                    timeout=self._web_search_timeout
                )
                elapsed = time.time() - start_time
                result = r.json()

                if result.get('ok') and result.get('results'):
                    logger.info(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "search_monitor",
                        "action": "tavily_search_success",
                        "duration_ms": round(elapsed * 1000, 2),
                        "elapsed_sec": round(elapsed, 2),
                        "results_count": len(result.get('results', [])),
                    }, ensure_ascii=False))
                    check_result['engines']['tavily'] = {
                        'status': 'success', 'elapsed': elapsed,
                        'api_elapsed': result.get('elapsed', 0),
                        'results_count': len(result.get('results', [])),
                    }
                else:
                    error = result.get('error', '未知错误')
                    logger.warning(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "search_monitor",
                        "action": "tavily_search_failed",
                        "duration_ms": round(elapsed * 1000, 2),
                        "error": str(error),
                    }, ensure_ascii=False))
                    check_result['engines']['tavily'] = {
                        'status': 'failed', 'elapsed': elapsed, 'error': error,
                    }
                    check_result['errors'].append(f"Tavily 搜索失败: {error}")
            except Exception as e:
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "search_monitor",
                    "action": "tavily_search_error",
                    "duration_ms": 0,
                    "error": str(e),
                }, ensure_ascii=False))
                check_result['engines']['tavily'] = {'status': 'error', 'error': str(e)}
                check_result['errors'].append(f"Tavily 搜索异常: {e}")

            # 3. 获取搜索引擎状态
            try:
                r = requests.get(f"{self.base_url}/api/web/search/status", timeout=self._status_check_timeout)
                status = r.json().get('status', {})
                stats = status.get('stats', {})
                timing = stats.get('engine_timing', {})

                for engine, timing_data in timing.items():
                    if timing_data.get('count', 0) > 0:
                        logger.info(json.dumps({
                            "trace_id": get_trace_id(),
                            "module_name": "search_monitor",
                            "action": "engine_stats",
                            "duration_ms": 0,
                            "engine": engine.upper(),
                            "avg_sec": timing_data.get('avg', 0),
                            "min_sec": timing_data.get('min', 0),
                            "max_sec": timing_data.get('max', 0),
                            "count": timing_data.get('count', 0),
                        }, ensure_ascii=False))

                check_result['engine_stats'] = stats
            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "search_monitor",
                    "action": "get_status_error",
                    "duration_ms": 0,
                    "error": str(e),
                }, ensure_ascii=False))
                check_result['errors'].append(f"获取状态失败: {e}")

            # 4. 判断整体状态
            if check_result['errors']:
                check_result['status'] = 'warning' if len(check_result['errors']) <= 2 else 'error'
            else:
                check_result['status'] = 'ok'

            # 5. 记录历史
            self._performance_history.append(check_result)
            self._save_performance_data()

            # 合并原分隔线日志为单条结构化日志（跳过纯分隔线 logger.info("=" * 80)）
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "check_complete",
                "duration_ms": 0,
                "check_id": self._check_count,
                "status": check_result['status'],
                "errors_count": len(check_result['errors']),
            }, ensure_ascii=False))

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "check_error",
                "duration_ms": 0,
                "check_id": self._check_count,
                "error": str(e),
            }, ensure_ascii=False))
            check_result['status'] = 'error'
            check_result['errors'].append(f"检测异常: {e}")
            self._performance_history.append(check_result)
            self._save_performance_data()

    def run_manual_check(self) -> Dict:
        """手动执行一次性能检测"""
        self._perform_check()
        return self._performance_history[-1] if self._performance_history else {}

    def get_status(self) -> Dict:
        """获取监控器状态"""
        return {
            'running': self._running,
            'interval': self._interval,
            'check_count': self._check_count,
            'last_check': self._last_check_time.isoformat() if self._last_check_time else None,
            'history_count': len(self._performance_history),
        }

    def get_recent_history(self, limit: int = 10) -> List[Dict]:
        """获取最近的历史记录"""
        return self._performance_history[-limit:]

    def get_performance_summary(self) -> Dict:
        """获取性能摘要"""
        if not self._performance_history:
            return {'status': 'no_data', 'message': '暂无性能数据'}

        recent = self._performance_history[-10:]

        tavily_success = 0
        tavily_failed = 0
        tavily_avg_time = 0

        for record in recent:
            tavily_data = record.get('engines', {}).get('tavily', {})
            if tavily_data.get('status') == 'success':
                tavily_success += 1
                tavily_avg_time += tavily_data.get('elapsed', 0)
            else:
                tavily_failed += 1

        if tavily_success > 0:
            tavily_avg_time = tavily_avg_time / tavily_success

        return {
            'total_checks': len(recent),
            'tavily_success_rate': tavily_success / len(recent) * 100 if recent else 0,
            'tavily_avg_time': tavily_avg_time,
            'tavily_success_count': tavily_success,
            'tavily_failed_count': tavily_failed,
            'last_status': recent[-1].get('status', 'unknown') if recent else 'unknown',
        }


# 全局监控器实例
_performance_monitor: Optional[SearchPerformanceMonitor] = None


def get_performance_monitor() -> SearchPerformanceMonitor:
    """获取全局性能监控器实例"""
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = SearchPerformanceMonitor()
    return _performance_monitor


def start_performance_monitor(interval_sec: int = 300):
    """启动性能监控"""
    monitor = get_performance_monitor()
    monitor.set_interval(interval_sec)
    monitor.start()
    return monitor.get_status()


def stop_performance_monitor():
    """停止性能监控"""
    monitor = get_performance_monitor()
    monitor.stop()
    return monitor.get_status()


def run_manual_performance_check() -> Dict:
    """手动执行性能检测"""
    monitor = get_performance_monitor()
    return monitor.run_manual_check()


def get_performance_monitor_status() -> Dict:
    """获取性能监控器状态"""
    monitor = get_performance_monitor()
    return monitor.get_status()


def get_performance_history(limit: int = 10) -> List[Dict]:
    """获取性能历史记录"""
    monitor = get_performance_monitor()
    return monitor.get_recent_history(limit)


def get_performance_summary() -> Dict:
    """获取性能摘要"""
    monitor = get_performance_monitor()
    return monitor.get_performance_summary()
