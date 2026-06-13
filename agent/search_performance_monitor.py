"""
搜索引擎性能检测定时任务模块

功能：
1. 定期自动检测搜索引擎性能
2. 记录性能统计数据
3. 检测降级机制是否正常工作
4. 生成性能报告
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

# 设置路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# 性能检测数据文件
PERFORMANCE_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "search_performance.json")


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
        
        # 加载历史数据
        self._load_performance_data()
    
    def _load_performance_data(self):
        """加载性能历史数据"""
        try:
            if os.path.exists(PERFORMANCE_DATA_FILE):
                with open(PERFORMANCE_DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._performance_history = data.get('history', [])
                    self._check_count = data.get('check_count', 0)
                    logger.info("[性能监控] 已加载 %d 条历史记录", len(self._performance_history))
        except Exception as e:
            logger.warning("[性能监控] 加载历史数据失败: %s", e)
    
    def _save_performance_data(self):
        """保存性能历史数据"""
        try:
            os.makedirs(os.path.dirname(PERFORMANCE_DATA_FILE), exist_ok=True)
            data = {
                'history': self._performance_history[-100:],  # 只保留最近 100 条
                'check_count': self._check_count,
                'last_check': self._last_check_time.isoformat() if self._last_check_time else None,
            }
            with open(PERFORMANCE_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("[性能监控] 保存历史数据失败: %s", e)
    
    def set_interval(self, interval_sec: int):
        """设置检测间隔"""
        self._interval = interval_sec
        logger.info("[性能监控] 检测间隔已设置为 %d 秒", interval_sec)
    
    def start(self):
        """启动性能监控"""
        if self._running:
            logger.warning("[性能监控] 已经在运行中")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("[性能监控] 已启动，检测间隔: %d 秒", self._interval)
    
    def stop(self):
        """停止性能监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[性能监控] 已停止")
    
    def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                # 执行性能检测
                self._perform_check()
                
                # 等待下一次检测
                for _ in range(self._interval):
                    if not self._running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error("[性能监控] 监控循环异常: %s", e)
                time.sleep(60)  # 异常后等待 1 分钟再继续
    
    def _perform_check(self):
        """执行性能检测"""
        import requests
        
        self._check_count += 1
        self._last_check_time = datetime.now()
        
        logger.info("=" * 80)
        logger.info("[性能监控] 开始第 %d 次性能检测", self._check_count)
        logger.info("=" * 80)
        
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
                r = requests.post(f"{self.base_url}/api/apply-network-config", timeout=10)
                if r.json().get('ok'):
                    logger.info("[性能监控] 配置已应用")
                else:
                    logger.warning("[性能监控] 配置应用失败")
                    check_result['errors'].append("配置应用失败")
            except Exception as e:
                logger.warning("[性能监控] 配置应用异常: %s", e)
                check_result['errors'].append(f"配置应用异常: {e}")
            
            # 2. 测试 Tavily 搜索
            try:
                start_time = time.time()
                r = requests.get(
                    f"{self.base_url}/api/web/search",
                    params={'query': '人工智能最新发展', 'num_results': 3, 'engine': 'tavily'},
                    timeout=30
                )
                elapsed = time.time() - start_time
                result = r.json()
                
                if result.get('ok') and result.get('results'):
                    logger.info("[性能监控] Tavily 搜索成功: 耗时=%.2fs, 结果数=%d",
                               elapsed, len(result.get('results', [])))
                    check_result['engines']['tavily'] = {
                        'status': 'success',
                        'elapsed': elapsed,
                        'api_elapsed': result.get('elapsed', 0),
                        'results_count': len(result.get('results', [])),
                    }
                else:
                    error = result.get('error', '未知错误')
                    logger.warning("[性能监控] Tavily 搜索失败: %s", error)
                    check_result['engines']['tavily'] = {
                        'status': 'failed',
                        'elapsed': elapsed,
                        'error': error,
                    }
                    check_result['errors'].append(f"Tavily 搜索失败: {error}")
                    
            except Exception as e:
                logger.error("[性能监控] Tavily 搜索异常: %s", e)
                check_result['engines']['tavily'] = {
                    'status': 'error',
                    'error': str(e),
                }
                check_result['errors'].append(f"Tavily 搜索异常: {e}")
            
            # 3. 获取搜索引擎状态
            try:
                r = requests.get(f"{self.base_url}/api/web/search/status", timeout=10)
                status = r.json().get('status', {})
                
                # 记录统计信息
                stats = status.get('stats', {})
                timing = stats.get('engine_timing', {})
                
                for engine, timing_data in timing.items():
                    if timing_data.get('count', 0) > 0:
                        logger.info("[性能监控] %s 统计: 平均=%.2fs, 最小=%.2fs, 最大=%.2fs, 调用=%d 次",
                                   engine.upper(),
                                   timing_data.get('avg', 0),
                                   timing_data.get('min', 0),
                                   timing_data.get('max', 0),
                                   timing_data.get('count', 0))
                
                check_result['engine_stats'] = stats
                
            except Exception as e:
                logger.warning("[性能监控] 获取状态失败: %s", e)
                check_result['errors'].append(f"获取状态失败: {e}")
            
            # 4. 判断整体状态
            if check_result['errors']:
                check_result['status'] = 'warning' if len(check_result['errors']) <= 2 else 'error'
            else:
                check_result['status'] = 'ok'
            
            # 5. 记录历史
            self._performance_history.append(check_result)
            self._save_performance_data()
            
            logger.info("=" * 80)
            logger.info("[性能监控] 第 %d 次检测完成，状态: %s", self._check_count, check_result['status'])
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error("[性能监控] 性能检测异常: %s", e)
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
        
        # 统计最近 10 次检测
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


if __name__ == "__main__":
    # 测试性能监控器
    print("启动性能监控器...")
    monitor = SearchPerformanceMonitor()
    
    # 手动执行一次检测
    result = monitor.run_manual_check()
    print("检测结果:", json.dumps(result, indent=2, ensure_ascii=False))
    
    # 获取性能摘要
    summary = monitor.get_performance_summary()
    print("性能摘要:", json.dumps(summary, indent=2, ensure_ascii=False))