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
from agent.common.stop_mixin import StopMixin

logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量 _performance_monitor 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None

# 性能检测数据文件
PERFORMANCE_DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "search_performance.json"
)


class SearchPerformanceMonitor(StopMixin):
    """搜索引擎性能监控器

    继承 StopMixin 统一线程优雅关闭范式 [TLM-AUDIT-002]：
    原 stop()+join() 逻辑保留，新增 _stop_event 支持循环内立即唤醒。
    """

    def __init__(self, base_url: str = "http://localhost:5678"):
        super().__init__()  # 初始化 StopMixin 的 _stop_event / _registered_threads
        # Why RLock：监控循环线程与 HTTP 路由线程并发访问共享状态（_check_count/
        # _performance_history/_running），读-改-写非原子；RLock 允许 _perform_check
        # 内部重入 _save_performance_data/get_status 等。
        self._lock = threading.RLock()
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
            # 锁内构造快照（防并发 append/计数变化读到半更新状态），文件写入移出锁外
            with self._lock:
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
        with self._lock:
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
        # Why 锁内检查+设置：并发两次 start 的 if self._running 检查-赋值非原子（TOCTOU），
        # 不加锁会启动两个监控线程
        with self._lock:
            if self._running:
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "search_monitor",
                    "action": "start_duplicate",
                    "duration_ms": 0,
                }, ensure_ascii=False))
                return
            self._stop_event.clear()  # [TLM-AUDIT-002] 重置停止信号（支持重启）
            self._running = True
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            self.register_thread(self._thread)  # [TLM-AUDIT-002] 注册到 StopMixin
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "search_monitor",
            "action": "start",
            "duration_ms": 0,
            "interval_sec": self._interval,
        }, ensure_ascii=False))

    def stop(self):
        """停止性能监控"""
        # [TLM-AUDIT-002] 调用 StopMixin.stop 统一设置 _stop_event + join
        # 用 super() 显式调用父类方法，避免递归
        self._running = False
        super().stop(timeout=self._thread_join_timeout)
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
        # [TLM-AUDIT-002] 用 _should_stop() + _stop_event.wait 替代 _running + sleep
        # 优势：stop() 时 Event.set 自动唤醒 wait，无需等到下次 sleep 超时
        while not self._should_stop():
            try:
                self._perform_check()
                # 等待 interval 秒，可被 stop_event 立即唤醒（替代 for+sleep 循环）
                if self._stop_event.wait(timeout=self._interval):
                    break
            except Exception as e:
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "search_monitor",
                    "action": "monitor_loop_error",
                    "duration_ms": 0,
                    "error": str(e),
                }, ensure_ascii=False))
                # 异常后等待 60 秒，可被 stop_event 唤醒
                if self._stop_event.wait(timeout=60):
                    break

    def _perform_check(self):
        """执行性能检测"""
        import requests

        # Why 锁内取 check_id + 时间戳：_check_count 读-改-写非原子，并发调用会丢计数
        # 且产生重复 check_id；网络 I/O 在锁外执行（可达 timeout 秒级，不得持锁）
        with self._lock:
            self._check_count += 1
            check_id = self._check_count
            self._last_check_time = datetime.now()
            timestamp = self._last_check_time.isoformat()

        # 合并原分隔线日志为单条结构化日志（跳过纯分隔线 logger.info("=" * 80)）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "search_monitor",
            "action": "check_start",
            "duration_ms": 0,
            "check_id": check_id,
        }, ensure_ascii=False))

        check_result = {
            'check_id': check_id,
            'timestamp': timestamp,
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

            # 5. 记录历史（锁内 append，防并发读取 history 时 RuntimeError）
            with self._lock:
                self._performance_history.append(check_result)
            self._save_performance_data()

            # 合并原分隔线日志为单条结构化日志（跳过纯分隔线 logger.info("=" * 80)）
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "check_complete",
                "duration_ms": 0,
                "check_id": check_id,
                "status": check_result['status'],
                "errors_count": len(check_result['errors']),
            }, ensure_ascii=False))

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "search_monitor",
                "action": "check_error",
                "duration_ms": 0,
                "check_id": check_id,
                "error": str(e),
            }, ensure_ascii=False))
            check_result['status'] = 'error'
            check_result['errors'].append(f"检测异常: {e}")
            with self._lock:
                self._performance_history.append(check_result)
            self._save_performance_data()

    def run_manual_check(self) -> Dict:
        """手动执行一次性能检测"""
        self._perform_check()
        with self._lock:
            return self._performance_history[-1] if self._performance_history else {}

    def get_status(self) -> Dict:
        """获取监控器状态（锁内快照，防读-改-写中途读到半更新状态）"""
        with self._lock:
            return {
                'running': self._running,
                'interval': self._interval,
                'check_count': self._check_count,
                'last_check': self._last_check_time.isoformat() if self._last_check_time else None,
                'history_count': len(self._performance_history),
            }

    def get_recent_history(self, limit: int = 10) -> List[Dict]:
        """获取最近的历史记录（锁内切片快照）"""
        with self._lock:
            return self._performance_history[-limit:]

    def get_performance_summary(self) -> Dict:
        """获取性能摘要（锁内取快照，锁外计算，缩短持锁时间）"""
        with self._lock:
            history_snapshot = self._performance_history[-10:]
        if not history_snapshot:
            return {'status': 'no_data', 'message': '暂无性能数据'}

        recent = history_snapshot

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
_performance_monitor: Optional[SearchPerformanceMonitor] = None  # 保留作为 fallback


def _create_performance_monitor(config=None):
    """SearchPerformanceMonitor 工厂（供 SingletonManager 使用）"""
    return SearchPerformanceMonitor()


def _cleanup_performance_monitor(monitor):
    """清理钩子：停止性能监控线程（仅测试重置时调用）"""
    if monitor is not None:
        try:
            monitor.stop()
        except Exception:
            pass


def get_performance_monitor() -> SearchPerformanceMonitor:
    """获取全局性能监控器实例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("search_performance_monitor")
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = _create_performance_monitor()
    return _performance_monitor


def reset_performance_monitor():
    """重置全局性能监控器单例（仅用于测试）"""
    global _performance_monitor
    if _SINGLETON_AVAILABLE:
        reset_singleton("search_performance_monitor")
    _performance_monitor = None


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


# 注册单例工厂（置于文件末尾，确保类已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("search_performance_monitor", _create_performance_monitor,
                       cleanup_fn=_cleanup_performance_monitor)
