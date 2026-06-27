"""混沌场景本地演示脚本 - Mock数据版

用于本地演示和调试各种混沌场景，特别是：
1. 内存压力混沌场景
2. 连接池耗尽混沌场景
3. 网络延迟/超时混沌场景
4. CPU压力混沌场景

运行方式:
    python tests/chaos/chaos_demo_mock.py
"""

import os
import sys
import time
import logging
import random
import queue
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault,
    with_chaos_injection
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger("chaos_demo")


class MockMemoryService:
    """模拟内存服务 - 用于演示内存压力混沌"""
    
    def __init__(self):
        self._cache = {}
        self._max_cache_size = 1000
        self._operation_count = 0
    
    def store_data(self, key: str, data: str) -> bool:
        """存储数据"""
        if len(self._cache) >= self._max_cache_size:
            raise MemoryError("Cache is full")
        self._cache[key] = data
        self._operation_count += 1
        return True
    
    def get_data(self, key: str):
        """获取数据"""
        self._operation_count += 1
        return self._cache.get(key)
    
    def get_stats(self):
        """获取统计信息"""
        return {
            "cache_size": len(self._cache),
            "operation_count": self._operation_count,
            "max_size": self._max_cache_size
        }


class MockConnectionPool:
    """模拟数据库连接池 - 用于演示连接池混沌"""
    
    def __init__(self, max_connections: int = 5):
        self._max_connections = max_connections
        self._connections = queue.Queue(maxsize=max_connections)
        self._active_connections = 0
        self._lock = threading.Lock()
        self._total_acquired = 0
        self._total_released = 0
        
        for i in range(max_connections):
            self._connections.put(f"conn_{i}")
    
    def acquire(self, timeout: float = 1.0):
        """获取连接"""
        try:
            conn = self._connections.get(timeout=timeout)
            with self._lock:
                self._active_connections += 1
                self._total_acquired += 1
            return conn
        except queue.Empty:
            raise TimeoutError("Failed to acquire connection: timeout")
    
    def release(self, conn: str):
        """释放连接"""
        with self._lock:
            self._active_connections -= 1
            self._total_released += 1
        self._connections.put(conn)
    
    def get_stats(self):
        """获取池统计"""
        return {
            "max_connections": self._max_connections,
            "active_connections": self._active_connections,
            "available": self._connections.qsize(),
            "total_acquired": self._total_acquired,
            "total_released": self._total_released
        }


class MockApiClient:
    """模拟API客户端 - 用于演示网络延迟/超时混沌"""
    
    def __init__(self):
        self._call_count = 0
        self._success_count = 0
        self._failure_count = 0
    
    @with_chaos_injection(FaultType.NETWORK_DELAY, target_service="api")
    @with_chaos_injection(FaultType.NETWORK_TIMEOUT, target_service="api")
    @with_chaos_injection(FaultType.SERVICE_UNAVAILABLE, target_service="api")
    def call_api(self, endpoint: str) -> dict:
        """调用API"""
        self._call_count += 1
        self._success_count += 1
        return {
            "endpoint": endpoint,
            "status": "success",
            "data": f"Response from {endpoint}"
        }
    
    def get_stats(self):
        """获取统计"""
        return {
            "total_calls": self._call_count,
            "success": self._success_count,
            "failure": self._failure_count
        }


def demo_memory_pressure():
    """演示内存压力混沌场景"""
    print("\n" + "="*70)
    print("🚀 场景1: 内存压力混沌演示")
    print("="*70)
    
    injector = get_chaos_injector()
    injector.clear_all()
    memory_service = MockMemoryService()
    
    print(f"\n[初始状态] 内存服务: {memory_service.get_stats()}")
    
    try:
        target_mb = 128
        print(f"\n[注入故障] 内存压力 - 目标占用: {target_mb}MB")
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=target_mb):
            stats = injector.get_stats()
            print(f"[故障状态] 内存压力激活: {stats['fault_types']['memory_pressure']}")
            print(f"[故障状态] 内存占用块数: {len(injector._memory_hold_list)}")
            
            print("\n[模拟操作] 在内存压力下执行存储操作...")
            for i in range(10):
                key = f"key_{i}"
                data = f"data_{i}_" + "x" * 1000
                try:
                    memory_service.store_data(key, data)
                    print(f"  ✓ 存储 {key} 成功")
                except Exception as e:
                    print(f"  ✗ 存储 {key} 失败: {e}")
            
            print(f"\n[操作后统计] {memory_service.get_stats()}")
        
        print(f"\n[故障清除后] 内存压力激活: {injector.get_stats()['fault_types']['memory_pressure']}")
        print(f"[故障清除后] 内存占用块数: {len(injector._memory_hold_list)}")
        
    except Exception as e:
        logger.error(f"内存压力演示出错: {e}")
    finally:
        injector.clear_all()
    
    print("\n✅ 内存压力混沌演示完成")


def demo_connection_pool_exhaustion():
    """演示连接池耗尽混沌场景"""
    print("\n" + "="*70)
    print("🚀 场景2: 连接池耗尽混沌演示")
    print("="*70)
    
    injector = get_chaos_injector()
    injector.clear_all()
    pool = MockConnectionPool(max_connections=3)
    
    print(f"\n[初始状态] 连接池: {pool.get_stats()}")
    
    try:
        print("\n[注入故障] 连接池耗尽 - 剩余连接数: 1")
        
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=1):
            stats = injector.get_stats()
            print(f"[故障状态] 连接池耗尽激活: {stats['fault_types']['connection_pool_exhausted']}")
            
            config = injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
            print(f"[故障状态] 配置pool_size: {config.pool_size}")
            
            print("\n[模拟操作] 并发获取连接...")
            results = []
            
            def worker(worker_id):
                try:
                    conn = pool.acquire(timeout=0.5)
                    time.sleep(0.2)
                    pool.release(conn)
                    results.append((worker_id, "success", conn))
                except Exception as e:
                    results.append((worker_id, "failed", str(e)))
            
            threads = []
            for i in range(5):
                t = threading.Thread(target=worker, args=(i,))
                threads.append(t)
                t.start()
            
            for t in threads:
                t.join()
            
            for wid, status, detail in sorted(results):
                emoji = "✓" if status == "success" else "✗"
                print(f"  {emoji} worker_{wid}: {status} - {detail}")
            
            print(f"\n[操作后统计] 连接池: {pool.get_stats()}")
        
        print(f"\n[故障清除后] 连接池耗尽激活: {injector.get_stats()['fault_types']['connection_pool_exhausted']}")
        
    except Exception as e:
        logger.error(f"连接池演示出错: {e}")
    finally:
        injector.clear_all()
    
    print("\n✅ 连接池混沌演示完成")


def demo_network_latency():
    """演示网络延迟混沌场景"""
    print("\n" + "="*70)
    print("🚀 场景3: 网络延迟/超时混沌演示")
    print("="*70)
    
    injector = get_chaos_injector()
    injector.clear_all()
    api_client = MockApiClient()
    
    print(f"\n[初始状态] API客户端: {api_client.get_stats()}")
    
    try:
        print("\n[阶段1] 无故障 - 正常调用")
        for i in range(3):
            start = time.time()
            result = api_client.call_api(f"/api/test/{i}")
            elapsed = (time.time() - start) * 1000
            print(f"  ✓ 调用 {i}: {elapsed:.0f}ms - {result['status']}")
        
        print("\n[注入故障] 网络延迟 - 200ms")
        
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=200):
            stats = injector.get_stats()
            print(f"[故障状态] 网络延迟激活: {stats['fault_types']['network_delay']}")
            
            print("\n[模拟操作] 带延迟的API调用...")
            for i in range(3):
                start = time.time()
                result = api_client.call_api(f"/api/delayed/{i}")
                elapsed = (time.time() - start) * 1000
                print(f"  ✓ 调用 {i}: {elapsed:.0f}ms - {result['status']}")
        
        print(f"\n[故障清除后] 网络延迟激活: {injector.get_stats()['fault_types']['network_delay']}")
        
        print("\n[注入故障] 网络超时 - 概率50%")
        
        with chaos_fault(FaultType.NETWORK_TIMEOUT, probability=0.5):
            stats = injector.get_stats()
            print(f"[故障状态] 网络超时激活: {stats['fault_types']['network_timeout']}")
            
            print("\n[模拟操作] 可能超时的API调用...")
            success_count = 0
            timeout_count = 0
            for i in range(6):
                try:
                    start = time.time()
                    result = api_client.call_api(f"/api/timeout/{i}")
                    elapsed = (time.time() - start) * 1000
                    success_count += 1
                    print(f"  ✓ 调用 {i}: {elapsed:.0f}ms - 成功")
                except TimeoutError as e:
                    timeout_count += 1
                    print(f"  ✗ 调用 {i}: 超时 - {e}")
            
            print(f"\n[统计] 成功: {success_count}, 超时: {timeout_count}")
        
        print(f"\n[故障清除后] 网络超时激活: {injector.get_stats()['fault_types']['network_timeout']}")
        
    except Exception as e:
        logger.error(f"网络延迟演示出错: {e}", exc_info=True)
    finally:
        injector.clear_all()
    
    print("\n✅ 网络延迟/超时混沌演示完成")


def demo_cpu_pressure():
    """演示CPU压力混沌场景"""
    print("\n" + "="*70)
    print("🚀 场景4: CPU压力混沌演示")
    print("="*70)
    
    injector = get_chaos_injector()
    injector.clear_all()
    
    try:
        import psutil
        cpu_before = psutil.cpu_percent(interval=0.5)
        print(f"\n[初始状态] CPU使用率: {cpu_before:.1f}%")
        
        print("\n[注入故障] CPU压力 - 持续500ms (单线程模式)")
        
        stress_thread = threading.Thread(target=lambda: injector.inject_cpu_pressure(duration_ms=500), daemon=True)
        start_time = time.time()
        stress_thread.start()
        
        time.sleep(0.2)
        cpu_during = psutil.cpu_percent(interval=0.3)
        print(f"[故障状态] CPU使用率: {cpu_during:.1f}%")
        
        stress_thread.join(timeout=2)
        time.sleep(0.5)
        
        cpu_after = psutil.cpu_percent(interval=0.3)
        print(f"[故障清除后] CPU使用率: {cpu_after:.1f}%")
        
    except ImportError:
        print("\n⚠️  未安装 psutil，跳过CPU使用率测量")
        print("[注入故障] CPU压力 - 持续300ms")
        injector.inject_cpu_pressure(duration_ms=300)
        time.sleep(0.5)
        print("[故障清除完成]")
    except Exception as e:
        logger.error(f"CPU压力演示出错: {e}")
    finally:
        injector.clear_all()
    
    print("\n✅ CPU压力混沌演示完成")


def demo_combined_scenarios():
    """演示组合混沌场景"""
    print("\n" + "="*70)
    print("🚀 场景5: 组合混沌场景 (内存+网络)")
    print("="*70)
    
    injector = get_chaos_injector()
    injector.clear_all()
    memory_service = MockMemoryService()
    api_client = MockApiClient()
    
    try:
        print("\n[注入故障] 内存压力(64MB) + 网络延迟(100ms)")
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=64):
            with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=100):
                stats = injector.get_stats()
                active_faults = [k for k, v in stats['fault_types'].items() if v]
                print(f"[故障状态] 活跃故障: {active_faults}")
                
                print("\n[模拟操作] 组合故障下的操作...")
                for i in range(3):
                    start = time.time()
                    
                    memory_service.store_data(f"combo_key_{i}", f"data_{i}")
                    
                    try:
                        api_result = api_client.call_api(f"/api/combo/{i}")
                        api_status = "success"
                    except Exception as e:
                        api_status = f"failed: {e}"
                    
                    elapsed = (time.time() - start) * 1000
                    print(f"  操作 {i}: 内存存储+API调用 - {elapsed:.0f}ms - API: {api_status}")
        
        stats_after = injector.get_stats()
        active_after = [k for k, v in stats_after['fault_types'].items() if v]
        print(f"\n[故障清除后] 活跃故障: {active_after if active_after else '无'}")
        
    except Exception as e:
        logger.error(f"组合场景演示出错: {e}", exc_info=True)
    finally:
        injector.clear_all()
    
    print("\n✅ 组合混沌场景演示完成")


def main():
    """主函数"""
    print("\n" + "="*70)
    print("🎯 混沌工程场景本地演示 (Mock数据版)")
    print("="*70)
    print("""
本演示脚本用于在本地环境中安全地测试和调试各种混沌场景。
所有场景都使用Mock数据，不会对真实系统产生影响。

演示场景:
  1. 内存压力混沌 - 模拟高内存占用下的系统行为
  2. 连接池耗尽混沌 - 模拟数据库连接池耗尽时的请求处理
  3. 网络延迟/超时混沌 - 模拟网络延迟和超时故障
  4. CPU压力混沌 - 模拟CPU高负载场景
  5. 组合混沌场景 - 多个故障同时注入
""")
    
    random.seed(42)
    
    try:
        demo_memory_pressure()
        time.sleep(0.5)
        
        demo_connection_pool_exhaustion()
        time.sleep(0.5)
        
        demo_network_latency()
        time.sleep(0.5)
        
        demo_cpu_pressure()
        time.sleep(0.5)
        
        demo_combined_scenarios()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断，正在清理...")
        get_chaos_injector().clear_all()
    
    print("\n" + "="*70)
    print("🎉 所有混沌场景演示完成！")
    print("="*70)
    
    print("\n📊 故障注入历史统计:")
    injector = get_chaos_injector()
    records = injector.get_injection_history()
    type_counts = {}
    for r in records:
        t = r.fault_type.value
        type_counts[t] = type_counts.get(t, 0) + 1
    
    for fault_type, count in sorted(type_counts.items()):
        print(f"  {fault_type}: {count} 次")
    
    print(f"\n  总计: {len(records)} 次注入")
    
    injector.clear_all()


if __name__ == "__main__":
    main()
