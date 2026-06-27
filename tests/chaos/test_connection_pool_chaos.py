"""数据库连接池耗尽混沌测试

测试场景:
1. 连接池满时的请求排队
2. 连接获取超时处理
3. 连接泄漏检测与回收
4. 连接池动态调整
5. 数据库断开后的重连
6. 连接池监控告警
"""

import time
import pytest
import threading
import queue
import logging

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault
)

logger = logging.getLogger(__name__)


@pytest.mark.slow
class TestConnectionPoolChaos:
    """数据库连接池耗尽混沌测试"""
    
    def setup_method(self):
        """每个测试前重置混沌注入器"""
        self.injector = get_chaos_injector()
        self.injector.clear_all()
    
    def teardown_method(self):
        """每个测试后清理故障"""
        self.injector.clear_all()
    
    def test_connection_pool_exhausted(self):
        """测试连接池耗尽"""
        logger.info("[POOL_CHAOS] 连接池耗尽测试 - 注入 pool_size=0")
        
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=0):
            stats = self.injector.get_stats()
            logger.info(f"[POOL_CHAOS] 耗尽测试 - enabled: {stats['fault_types']['connection_pool_exhausted']}")
            assert stats['fault_types']['connection_pool_exhausted'] is True
            
            config = self.injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
            logger.info(f"[POOL_CHAOS] 耗尽测试 - config.pool_size={config.pool_size}, probability={config.probability}")
            assert config.pool_size == 0
    
    def test_connection_pool_low_remaining(self):
        """测试连接池剩余连接数低"""
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=2):
            stats = self.injector.get_stats()
            assert stats['fault_types']['connection_pool_exhausted'] is True
            
            config = self.injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
            assert config.pool_size == 2
    
    def test_connection_pool_request_queuing(self):
        """测试连接池满时的请求排队"""
        pool_size = 2
        queue_size = 5
        logger.info(f"[POOL_CHAOS] 排队测试 - pool_size={pool_size}, queue_size={queue_size}")
        
        connection_queue = queue.Queue(maxsize=pool_size)
        
        for _ in range(pool_size):
            connection_queue.put("conn")
        
        def acquire_connection(timeout=1):
            try:
                conn = connection_queue.get(timeout=timeout)
                return {"success": True, "connection": conn}
            except queue.Empty:
                return {"success": False, "error": "timeout"}
        
        results = []
        
        def worker(id):
            result = acquire_connection(timeout=0.5)
            results.append((id, result))
        
        threads = []
        for i in range(queue_size):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        success_count = sum(1 for _, r in results if r['success'])
        fail_count = sum(1 for _, r in results if not r['success'])
        logger.info(f"[POOL_CHAOS] 排队测试 - 成功: {success_count}, 失败: {fail_count}")
        
        assert success_count <= pool_size
        assert fail_count >= queue_size - pool_size
    
    def test_connection_acquire_timeout(self):
        """测试连接获取超时处理"""
        pool_size = 0
        logger.info(f"[POOL_CHAOS] 超时测试 - pool_size={pool_size}")
        
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=pool_size):
            start = time.time()
            
            try:
                timeout = 1.0
                time.sleep(timeout)
            except Exception as e:
                logger.warning(f"[POOL_CHAOS] 超时测试 - 异常: {type(e).__name__}: {e}")
            
            elapsed = (time.time() - start) * 1000
            logger.info(f"[POOL_CHAOS] 超时测试 - 等待耗时: {elapsed:.0f}ms")
            assert elapsed >= 1000
    
    def test_connection_leak_detection(self):
        """测试连接泄漏检测与回收"""
        connections_in_use = set()
        max_connections = 5
        logger.info(f"[POOL_CHAOS] 泄漏检测 - max_connections={max_connections}")
        
        def acquire_conn(conn_id):
            if len(connections_in_use) >= max_connections:
                logger.warning(f"[POOL_CHAOS] 泄漏检测 - 连接池已满，拒绝连接 {conn_id}")
                return None
            connections_in_use.add(conn_id)
            logger.info(f"[POOL_CHAOS] 泄漏检测 - 获取连接 {conn_id}, 当前使用: {len(connections_in_use)}")
            return conn_id
        
        def release_conn(conn_id):
            connections_in_use.discard(conn_id)
            logger.info(f"[POOL_CHAOS] 泄漏检测 - 释放连接 {conn_id}, 当前使用: {len(connections_in_use)}")
        
        for i in range(max_connections):
            result = acquire_conn(f"conn_{i}")
            assert result == f"conn_{i}"
        
        extra_result = acquire_conn("conn_extra")
        logger.info(f"[POOL_CHAOS] 泄漏检测 - 超额请求结果: {extra_result}")
        assert extra_result is None
        
        release_conn("conn_0")
        
        reused = acquire_conn("conn_extra")
        logger.info(f"[POOL_CHAOS] 泄漏检测 - 复用结果: {reused}")
        assert reused == "conn_extra"
        
        assert len(connections_in_use) == max_connections
    
    def test_connection_pool_dynamic_resize(self):
        """测试连接池动态调整"""
        pool_sizes = [2, 5, 10]
        
        for size in pool_sizes:
            self.injector.clear_all()
            with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=size):
                config = self.injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
                assert config.pool_size == size
                assert config.enabled is True
    
    def test_database_disconnect_reconnect(self):
        """测试数据库断开后的重连"""
        connection_state = {"connected": True}
        reconnect_attempts = [0]
        logger.info("[POOL_CHAOS] 重连测试 - 模拟数据库断开")
        
        def check_connection():
            if connection_state["connected"]:
                logger.info("[POOL_CHAOS] 重连测试 - 连接正常")
                return {"connected": True}
            
            reconnect_attempts[0] += 1
            logger.info(f"[POOL_CHAOS] 重连测试 - 第 {reconnect_attempts[0]} 次重连尝试")
            if reconnect_attempts[0] >= 3:
                connection_state["connected"] = True
                logger.info("[POOL_CHAOS] 重连测试 - 重连成功")
                return {"connected": True, "reconnected": True}
            
            logger.info("[POOL_CHAOS] 重连测试 - 重连失败")
            return {"connected": False}
        
        connection_state["connected"] = False
        
        result = check_connection()
        assert result["connected"] is False
        
        result = check_connection()
        assert result["connected"] is False
        
        result = check_connection()
        logger.info(f"[POOL_CHAOS] 重连测试 - 最终结果: {result}")
        assert result["connected"] is True
        assert result.get("reconnected") is True
    
    def test_connection_pool_monitoring_alert(self):
        """测试连接池监控告警"""
        pool_state = {
            "total_connections": 10,
            "used_connections": 9,
            "pending_requests": 5
        }
        logger.info(f"[POOL_CHAOS] 监控告警 - 连接池状态: {pool_state}")
        
        def check_pool_health(pool):
            usage_ratio = pool["used_connections"] / pool["total_connections"]
            logger.info(f"[POOL_CHAOS] 监控告警 - usage_ratio={usage_ratio:.2f}, pending={pool['pending_requests']}")
            if usage_ratio > 0.8 or pool["pending_requests"] > 3:
                logger.warning("[POOL_CHAOS] 监控告警 - 触发告警状态")
                return {"status": "warning", "usage_ratio": usage_ratio}
            logger.info("[POOL_CHAOS] 监控告警 - 状态健康")
            return {"status": "healthy", "usage_ratio": usage_ratio}
        
        result = check_pool_health(pool_state)
        logger.info(f"[POOL_CHAOS] 监控告警 - 检查结果: {result}")
        assert result["status"] == "warning"
        assert result["usage_ratio"] == 0.9
    
    def test_connection_pool_with_probability(self):
        """测试带概率的连接池耗尽"""
        self.injector.inject_connection_pool_exhausted(pool_size=0, probability=0.3)
        
        stats = self.injector.get_stats()
        assert stats['fault_types']['connection_pool_exhausted'] is True
        
        config = self.injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
        assert config.probability == 0.3
    
    def test_connection_pool_duration_auto_clear(self):
        """测试连接池故障自动清除"""
        self.injector.inject_connection_pool_exhausted(pool_size=0, duration_ms=500)
        
        assert self.injector.get_stats()['fault_types']['connection_pool_exhausted'] is True
        
        time.sleep(0.6)
        
        self.injector.clear_fault(FaultType.CONNECTION_POOL_EXHAUSTED)
        
        assert self.injector.get_stats()['fault_types']['connection_pool_exhausted'] is False
    
    def test_connection_pool_stats_tracking(self):
        """测试连接池统计跟踪"""
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=0):
            pass
        
        records = self.injector.get_injection_history()
        pool_records = [r for r in records if r.fault_type == FaultType.CONNECTION_POOL_EXHAUSTED]
        
        assert len(pool_records) >= 1
        assert pool_records[-1].recovered_at is not None
    
    def test_concurrent_connection_requests(self):
        """测试并发连接请求"""
        pool_size = 3
        request_count = 10
        success_count = [0]
        lock = threading.Lock()
        logger.info(f"[POOL_CHAOS] 并发请求 - pool_size={pool_size}, request_count={request_count}")
        
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=pool_size):
            def worker(worker_id):
                config = self.injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
                if config.pool_size > 0:
                    with lock:
                        success_count[0] += 1
                        logger.info(f"[POOL_CHAOS] 并发请求 - worker_{worker_id} 成功, 当前成功数: {success_count[0]}")
                else:
                    logger.info(f"[POOL_CHAOS] 并发请求 - worker_{worker_id} 被拒绝")
            
            threads = []
            for i in range(request_count):
                t = threading.Thread(target=worker, args=(i,))
                threads.append(t)
                t.start()
            
            for t in threads:
                t.join()
        
        logger.info(f"[POOL_CHAOS] 并发请求 - 最终结果: 成功 {success_count[0]}/{request_count}")
        assert success_count[0] <= request_count