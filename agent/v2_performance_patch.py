"""
V2 性能优化补丁
提供懒加载和异步初始化功能
"""

import logging
import json
import uuid
import time
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import threading

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class LazyInitializer:
    """懒加载初始化器"""
    
    def __init__(self, init_func, *args, **kwargs):
        """
        延迟初始化对象
        
        Args:
            init_func: 初始化函数
            *args, **kwargs: 初始化参数
        """
        self._init_func = init_func
        self._args = args
        self._kwargs = kwargs
        self._instance = None
        self._initialized = False
        self._lock = threading.Lock()
    
    def get(self):
        """获取实例（延迟初始化）"""
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "self._init_func.__name__", "msg": f"懒加载初始化: {self._init_func.__name__}"}, ensure_ascii=False))
                    start_time = time.time()
                    self._instance = self._init_func(*self._args, **self._kwargs)
                    self._initialized = True
                    elapsed = time.time() - start_time
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "self._init_func.__name__", "msg": f"{self._init_func.__name__} 初始化完成，耗时: {elapsed:.3f}s"}, ensure_ascii=False))
        return self._instance
    
    def is_initialized(self):
        """检查是否已初始化"""
        return self._initialized
    
    def force_init(self):
        """强制立即初始化"""
        return self.get()


class AsyncInitializer:
    """异步初始化器"""
    
    def __init__(self, max_workers: int = 3):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures = {}
        self._results = {}
        self._lock = threading.Lock()
    
    def submit(self, name: str, init_func, *args, **kwargs):
        """
        提交异步初始化任务
        
        Args:
            name: 任务名称
            init_func: 初始化函数
            *args, **kwargs: 初始化参数
        """
        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "name", "msg": f"提交异步初始化任务: {name}"}, ensure_ascii=False))
        future = self._executor.submit(init_func, *args, **kwargs)
        self._futures[name] = future
        return future
    
    def wait(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        等待所有任务完成
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            Dict[str, Any]: 任务结果
        """
        results = {}
        for name, future in self._futures.items():
            try:
                start_time = time.time()
                result = future.result(timeout=timeout)
                elapsed = time.time() - start_time
                results[name] = result
                logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "name.elapsed", "msg": f"{name} 异步初始化完成，耗时: {elapsed:.3f}s"}, ensure_ascii=False))
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "name", "msg": f"{name} 异步初始化失败: {e}"}, ensure_ascii=False))
                results[name] = None
        
        return results
    
    def shutdown(self, wait: bool = True):
        """关闭线程池"""
        self._executor.shutdown(wait=wait)
    
    def get_result(self, name: str, timeout: Optional[float] = None) -> Any:
        """获取单个任务结果"""
        if name in self._futures:
            return self._futures[name].result(timeout=timeout)
        return None


def optimize_v2_initialization(V2Class):
    """
    优化 V2 初始化的装饰器
    
    使用方法:
        V2Optimized = optimize_v2_initialization(DigitalLifeV2)
        v2 = V2Optimized(config)
    """
    
    original_init = V2Class.__init__
    
    def optimized_init(self, config: dict = None):
        """
        优化后的初始化
        
        优化策略:
        1. 延迟初始化非核心模块
        2. 使用懒加载器
        3. 并行初始化独立模块
        """
        start_time = time.time()
        config = config or {}
        
        # 第一阶段：核心模块初始化（必须同步）
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "log", "msg": "V2 优化初始化 - 第一阶段：核心模块"}, ensure_ascii=False))
        
        # 初始化基本配置
        self._running = False
        self._current_mode = None
        self._session_id = None
        self._interaction_count = 0
        self._reflection_history = []
        self._started_at = None
        
        # 第二阶段：并行初始化可选模块
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "log", "msg": "V2 优化初始化 - 第二阶段：并行初始化"}, ensure_ascii=False))
        
        # 使用懒加载器延迟初始化非核心模块
        self._lazy_modules = {}
        
        # Lazy BodySensor
        sensor_cfg = config.get("sensor", {})
        self._lazy_modules['body'] = LazyInitializer(
            lambda: self._init_body_sensor(sensor_cfg),
        )
        
        # Lazy MemoryManager
        memory_cfg = config.get("memory", {})
        self._lazy_modules['memory'] = LazyInitializer(
            lambda: self._init_memory_manager(memory_cfg),
        )
        
        # Lazy LifeTrace
        lifetrace_cfg = config.get("lifetrace", {})
        self._lazy_modules['lifetrace'] = LazyInitializer(
            lambda: self._init_lifetrace(lifetrace_cfg),
        )
        
        # 立即初始化核心模块
        self._init_core_modules(config)
        
        elapsed = time.time() - start_time
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "elapsed", "msg": f"V2 优化初始化完成，核心模块耗时: {elapsed:.3f}s"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "log", "msg": "非核心模块将在首次使用时懒加载"}, ensure_ascii=False))
    
    def _init_core_modules(self, config: dict):
        """初始化核心模块（同步）"""
        from .behavior_controller import BehaviorController, BehaviorMode
        from .permission_system import PermissionSystem
        
        # 行为控制器（必须立即初始化）
        self._behavior: BehaviorController = BehaviorController()
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "behaviorcontroller", "msg": "[ok] 本能（BehaviorController）已激活"}, ensure_ascii=False))
        
        # 权限系统（必须立即初始化）
        self._permission: PermissionSystem = PermissionSystem(
            backup_dir=config.get("backup_dir", "./.backups"),
        )
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "permissionsystem", "msg": "[ok] 道德（PermissionSystem）已激活"}, ensure_ascii=False))
        
        # 状态初始化
        self._current_mode = BehaviorMode.NORMAL
        self._health_check_interval = config.get("behavior", {}).get("check_interval", 30)
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._data_flow_enabled = config.get("data_flow", {}).get("enabled", True)
    
    def _init_body_sensor(self, sensor_cfg: dict):
        """初始化 BodySensor"""
        from sensor import BodySensor
        return BodySensor(
            watch_dirs=sensor_cfg.get("watch_dirs"),
            enable_change_detection=sensor_cfg.get("enable_change_detection", True),
            enable_event_monitor=sensor_cfg.get("enable_event_monitor", True),
        )
    
    def _init_memory_manager(self, memory_cfg: dict):
        """初始化 MemoryManager"""
        from memory import MemoryManager
        return MemoryManager(memory_cfg)
    
    def _init_lifetrace(self, lifetrace_cfg: dict):
        """初始化 LifeTrace"""
        from lifetrace import TraceRecorder, MemoryRetriever
        
        trace_recorder = TraceRecorder(
            data_dir=lifetrace_cfg.get("data_dir", "./data/lifetrace")
        )
        memory_retriever = MemoryRetriever(
            trace_recorder.source_tree,
            trace_recorder.topic_tree,
            trace_recorder.global_tree,
        )
        return trace_recorder, memory_retriever
    
    # 替换 __init__ 方法
    V2Class.__init__ = optimized_init
    
    # 添加懒加载属性访问器
    original_getattr = V2Class.__getattribute__
    
    def optimized_getattribute(self, name):
        """优化的属性访问（支持懒加载）"""
        # 拦截懒加载模块的访问
        if name in ['body', '_trace_recorder', '_memory_retriever', '_old_memory']:
            if hasattr(self, '_lazy_modules') and name in self._lazy_modules:
                lazy_loader = self._lazy_modules[name]
                if isinstance(lazy_loader, LazyInitializer):
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "v2_performance_patch", "action": "name", "msg": f"懒加载模块: {name}"}, ensure_ascii=False))
                    return lazy_loader.get()
        
        return original_getattr(self, name)
    
    V2Class.__getattribute__ = optimized_getattribute
    
    return V2Class


# 使用示例
"""
from agent.v2_performance_patch import optimize_v2_initialization

V2Optimized = optimize_v2_initialization(DigitalLifeV2)
v2 = V2Optimized(config)
v2.start()  # 非核心模块在首次使用时自动加载
"""
