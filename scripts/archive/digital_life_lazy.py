"""DigitalLife 懒加载优化版本

基于多级懒加载架构的重构版本

功能特性：
- Critical 级别：启动必须加载的模块（< 50ms）
- Important 级别：首次交互后后台加载（0ms 感知延迟）
- Optional 级别：用户请求时按需加载

优化目标：
- 启动时间 < 500ms
- 感知延迟 = 0ms
- 内存占用降低 50%

使用方法：
```python
from agent.digital_life_lazy import LazyDigitalLife

Yunshu = LazyDigitalLife()
Yunshu.start()  # 只加载 Critical 级别模块
Yunshu.chat("你好")  # 自动触发 Important 级别加载
```

或使用快速启动：
```python
Yunshu = LazyDigitalLife.quick_start()
```
"""

import logging
import time
import threading
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from functools import wraps

from agent.lazy_loader import (
    LazyModuleLoader, LoadLevel, get_lazy_loader,
    ParallelPreloader, ModuleInfo
)
from agent.llm_response_cache import (
    llm_cache, async_save_monitor, perf_logger
)
from agent.sensor_health_monitor import get_sensor_health_monitor

logger = logging.getLogger(__name__)


class PerformanceTimer:
    """性能计时器"""
    
    def __init__(self, name: str):
        self.name = name
        self.start_time = None
        self.elapsed_ms = 0.0
    
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000
        logger.info(f"[PerfTimer] {self.name}: {self.elapsed_ms:.2f}ms")
        return False


def profile_load(func):
    """加载性能分析装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"[Profile] {func.__name__}: {elapsed:.2f}ms")
        return result
    return wrapper


class LazyDigitalLife:
    """懒加载版本的 DigitalLife
    
    与标准 DigitalLife 相同的接口，但使用多级懒加载架构
    """
    
    _instance = None
    
    def __init__(self, config: dict = None):
        """
        初始化懒加载版本的数字生命
        
        Args:
            config: 配置字典（可选）
        """
        self._config = config or {}
        self._initialized = False
        self._started = False
        
        # 懒加载器
        self._loader = get_lazy_loader()
        
        # 加载统计
        self._load_times = {}
        
        # 异步加载标志
        self._important_loading = False
        self._important_loaded = False
        
        logger.info("=" * 70)
        logger.info("🚀 LazyDigitalLife 初始化开始（懒加载模式）")
        logger.info("=" * 70)
        
        # 注册所有模块（但不立即加载）
        self._register_modules()
        
        # 只加载 Critical 级别模块
        self._load_critical_modules()
        
        # 初始化传感器健康监控器
        self._init_sensor_health_monitor()
    
    def _register_modules(self):
        """注册所有模块"""
        logger.info("[LazyDigitalLife] 注册模块...")
        
        # Critical 级别：立即加载
        self._loader.register(
            "body_sensor",
            self._load_body_sensor,
            LoadLevel.CRITICAL
        )
        
        self._loader.register(
            "prompt_injector",
            self._load_prompt_injector,
            LoadLevel.CRITICAL
        )
        
        self._loader.register(
            "memory_manager",
            self._load_memory_manager,
            LoadLevel.CRITICAL
        )
        
        self._loader.register(
            "behavior_controller",
            self._load_behavior_controller,
            LoadLevel.CRITICAL
        )
        
        self._loader.register(
            "permission_system",
            self._load_permission_system,
            LoadLevel.CRITICAL
        )
        
        # Important 级别：首次交互后加载
        self._loader.register(
            "llm_service",
            self._load_llm_service,
            LoadLevel.IMPORTANT,
            dependencies=["memory_manager"]
        )
        
        self._loader.register(
            "vector_memory",
            self._load_vector_memory,
            LoadLevel.IMPORTANT,
            dependencies=["memory_manager"]
        )
        
        self._loader.register(
            "safety_monitor",
            self._load_safety_monitor,
            LoadLevel.IMPORTANT
        )
        
        # Optional 级别：按需加载
        self._loader.register(
            "lifetrace",
            self._load_lifetrace,
            LoadLevel.OPTIONAL
        )
        
        self._loader.register(
            "persona",
            self._load_persona,
            LoadLevel.OPTIONAL,
            dependencies=["lifetrace"]
        )
        
        self._loader.register(
            "voice_manager",
            self._load_voice_manager,
            LoadLevel.OPTIONAL
        )
        
        self._loader.register(
            "ocr_sensor",
            self._load_ocr_sensor,
            LoadLevel.OPTIONAL
        )
        
        self._loader.register(
            "snapshot_manager",
            self._load_snapshot_manager,
            LoadLevel.OPTIONAL
        )
        
        logger.info(f"[LazyDigitalLife] 模块注册完成: {len(self._loader.modules)} 个模块")
    
    def _load_critical_modules(self):
        """加载 Critical 级别的模块"""
        logger.info("[LazyDigitalLife] 加载 Critical 模块...")
        
        with PerformanceTimer("load_critical_modules"):
            self._loader.load_level(LoadLevel.CRITICAL)
        
        # 获取加载统计
        stats = self._loader.get_stats()
        logger.info(f"[LazyDigitalLife] Critical 模块加载统计:")
        logger.info(f"  成功: {stats['successful_loads']}")
        logger.info(f"  失败: {stats['failed_loads']}")
        logger.info(f"  平均耗时: {stats['avg_load_time_ms']}")
        
        self._initialized = True
    
    def _init_sensor_health_monitor(self):
        """初始化传感器健康监控器
        
        配置传感器读取失败自动重启机制：
        - 连续失败超过 3 次触发重启
        - 60 秒内没有失败则重置计数
        """
        logger.info("[LazyDigitalLife] 初始化传感器健康监控器...")
        
        self._sensor_health = get_sensor_health_monitor()
        self._sensor_health.set_restart_callback(self._handle_sensor_failure_restart)
        
        logger.info("[LazyDigitalLife] 传感器健康监控器初始化完成")
    
    def _handle_sensor_failure_restart(self):
        """处理传感器连续失败的重启回调"""
        logger.error("[LazyDigitalLife] ⚠️ 传感器连续失败，触发服务重启...")
        
        # 执行重启逻辑
        self.stop()
        
        # 延迟后重新启动
        def delayed_restart():
            time.sleep(2)
            logger.info("[LazyDigitalLife] 🔄 重新启动服务...")
            try:
                self._load_critical_modules()
                logger.info("[LazyDigitalLife] ✅ 服务重启成功")
            except Exception as e:
                logger.error(f"[LazyDigitalLife] ❌ 服务重启失败: {e}")
        
        thread = threading.Thread(target=delayed_restart, daemon=True)
        thread.start()
    
    def _ensure_important_loaded(self):
        """确保 Important 模块已加载（惰性加载）"""
        if self._important_loaded or self._important_loading:
            return
        
        self._important_loading = True
        
        logger.info("[LazyDigitalLife] 首次交互，触发 Important 模块后台加载...")
        
        def background_load():
            try:
                start = time.perf_counter()
                self._loader.load_level(LoadLevel.IMPORTANT)
                elapsed = (time.perf_counter() - start) * 1000
                logger.info(f"[LazyDigitalLife] Important 模块加载完成: {elapsed:.2f}ms")
            except Exception as e:
                logger.error(f"[LazyDigitalLife] Important 模块加载失败: {e}")
            finally:
                self._important_loaded = True
                self._important_loading = False
        
        thread = threading.Thread(target=background_load, daemon=True)
        thread.start()
    
    def _ensure_optional_loaded(self, module_name: str) -> bool:
        """确保 Optional 模块已加载（按需加载）"""
        if self._loader.should_load(module_name):
            logger.info(f"[LazyDigitalLife] 按需加载 Optional 模块: {module_name}")
            return self._loader.load(module_name) is not None
        return self._loader.is_loaded(module_name)
    
    # =========================================================================
    # 模块加载函数
    # =========================================================================
    
    @staticmethod
    @profile_load
    def _load_body_sensor():
        """加载身体传感器"""
        from sensor import BodySensor
        sensor_cfg = {"enable_change_detection": True, "enable_event_monitor": False}
        return BodySensor(**sensor_cfg)
    
    @staticmethod
    @profile_load
    def _load_prompt_injector():
        """加载提示词注入器"""
        from cognitive import PromptInjector, PromptConfig
        config = PromptConfig(config_path=None)
        return PromptInjector(config=config)
    
    @staticmethod
    @profile_load
    def _load_memory_manager():
        """加载记忆管理器"""
        from memory import MemoryManager
        memory_cfg = {
            "data_dir": "./data",
            "token_limit": 4096,
            "compress_threshold": 0.8,
            "async_compress": {"enabled": False}  # Critical 级别禁用异步
        }
        return MemoryManager(memory_cfg)
    
    @staticmethod
    @profile_load
    def _load_behavior_controller():
        """加载行为控制器"""
        from agent.behavior_controller import BehaviorController
        return BehaviorController()
    
    @staticmethod
    @profile_load
    def _load_permission_system():
        """加载权限系统"""
        from agent.permission_system import PermissionSystem
        return PermissionSystem(backup_dir="./.backups")
    
    @staticmethod
    @profile_load
    def _load_llm_service():
        """加载 LLM 服务"""
        from memory.llm_service import LLMService
        api_key = os.getenv("LLM_API_KEY", "")
        provider = os.getenv("LLM_PROVIDER", "openai")
        model = os.getenv("LLM_MODEL", "gpt-4")
        
        if not api_key:
            logger.warning("[LazyDigitalLife] 未配置 LLM_API_KEY")
            return None
        
        return LLMService(provider=provider, api_key=api_key, model=model)
    
    @staticmethod
    @profile_load
    def _load_vector_memory():
        """加载向量记忆"""
        try:
            from memory import VectorStore
            return VectorStore(
                collection_name="agent_memory",
                persist_dir="./data/memory"
            )
        except Exception as e:
            logger.warning(f"[LazyDigitalLife] 向量记忆加载失败: {e}")
            return None
    
    @staticmethod
    @profile_load
    def _load_safety_monitor():
        """加载安全监控器"""
        from agent.logging_utils import get_safety_monitor
        return get_safety_monitor()
    
    @staticmethod
    @profile_load
    def _load_lifetrace():
        """加载 LifeTrace"""
        try:
            from lifetrace import TraceRecorder
            return TraceRecorder(data_dir="./data/lifetrace")
        except Exception as e:
            logger.warning(f"[LazyDigitalLife] LifeTrace 加载失败: {e}")
            return None
    
    @staticmethod
    @profile_load
    def _load_persona():
        """加载 Persona"""
        try:
            from persona import PersonaModel
            return PersonaModel(persona_path="./data/personality.json")
        except Exception as e:
            logger.warning(f"[LazyDigitalLife] Persona 加载失败: {e}")
            return None
    
    @staticmethod
    @profile_load
    def _load_voice_manager():
        """加载语音管理器"""
        try:
            from sensor.voice_sensor import VoiceManager
            return VoiceManager(tts_engine="gtts", audio_dir="./data/audio")
        except Exception as e:
            logger.warning(f"[LazyDigitalLife] 语音管理器加载失败: {e}")
            return None
    
    @staticmethod
    @profile_load
    def _load_ocr_sensor():
        """加载 OCR 传感器"""
        try:
            from sensor.ocr_sensor import OcrSensor
            return OcrSensor()
        except Exception as e:
            logger.warning(f"[LazyDigitalLife] OCR 传感器加载失败: {e}")
            return None
    
    @staticmethod
    @profile_load
    def _load_snapshot_manager():
        """加载快照管理器"""
        try:
            from agent.p6_snapshot import StateSnapshotManager
            return StateSnapshotManager(
                snapshot_dir="./.p6_snapshots",
                enable_compression=True
            )
        except Exception as e:
            logger.warning(f"[LazyDigitalLife] 快照管理器加载失败: {e}")
            return None
    
    # =========================================================================
    # 生命周期方法
    # =========================================================================
    
    def start(self):
        """启动数字生命"""
        logger.info("[LazyDigitalLife] 启动...")
        
        body = self._loader.get_module("body_sensor")
        if body:
            body.establish_baseline()
        
        self._started = True
        
        # 触发 Important 模块后台加载
        self._ensure_important_loaded()
        
        logger.info("[LazyDigitalLife] ✅ 启动完成")
    
    def stop(self):
        """停止数字生命"""
        logger.info("[LazyDigitalLife] 停止...")
        self._started = False
    
    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._started
    
    # =========================================================================
    # 对话方法
    # =========================================================================
    
    def chat(self, user_input: str) -> str:
        """
        与数字生命对话
        
        Args:
            user_input: 用户输入
            
        Returns:
            响应字符串
        """
        overall_start = time.perf_counter()
        
        # 确保 Important 模块已加载
        self._ensure_important_loaded()
        
        # 获取 Critical 模块
        body = self._loader.get_module("body_sensor")
        memory = self._loader.get_module("memory_manager")
        behavior = self._loader.get_module("behavior_controller")
        
        if not all([body, memory, behavior]):
            return "（部分模块未加载，请稍后重试）"
        
        # 感知身体状态
        sensor_start = time.perf_counter()
        readings = body.collect_quick()
        current_mode = behavior.evaluate(readings)
        perf_logger.log_timing("sensor_reading", sensor_start)
        
        # 构建响应（简化版）
        profile = behavior.profile
        
        if current_mode.value != "NORMAL":
            return (
                f"抱歉，我现在的状态不太适合执行任务。\n"
                f"原因：{profile.description}\n"
                f"建议：{profile.suggestion}"
            )
        
        # 检查缓存
        cache_check_start = time.perf_counter()
        cached_response = llm_cache.get(user_input)
        if cached_response:
            perf_logger.log_timing("cache_hit", cache_check_start, {'cached': True})
            
            # 异步保存记忆（不阻塞响应）
            self._async_save_memory(memory, user_input, cached_response)
            
            overall_time = (time.perf_counter() - overall_start) * 1000
            perf_logger.log_timing("overall_chat", overall_start, {'cached': True})
            return cached_response
        perf_logger.log_timing("cache_miss", cache_check_start, {'cached': False})
        
        # 检查 LLM 是否可用
        llm = self._loader.get_module("llm_service")
        
        if llm:
            # 使用 LLM 生成响应
            system_prompt = f"你是云枢，一个数字生命体。当前状态：{profile.description}"
            try:
                llm_start = time.perf_counter()
                response = llm.chat(
                    messages=[{"role": "user", "content": user_input}],
                    system_prompt=system_prompt,
                    max_tokens=500
                )
                perf_logger.log_timing("llm_call", llm_start)
                
                # 保存到缓存
                cache_put_start = time.perf_counter()
                llm_cache.put(user_input, response)
                perf_logger.log_timing("cache_put", cache_put_start)
                
                # 异步保存记忆
                self._async_save_memory(memory, user_input, response)
                
                overall_time = (time.perf_counter() - overall_start) * 1000
                perf_logger.log_timing("overall_chat", overall_start, {'cached': False})
                
                return response
            except Exception as e:
                logger.error(f"[LazyDigitalLife] LLM 调用失败: {e}")
        
        # 离线模式响应
        offline_response = self._build_offline_response(user_input, profile)
        overall_time = (time.perf_counter() - overall_start) * 1000
        perf_logger.log_timing("overall_chat", overall_start, {'cached': False, 'offline': True})
        
        return offline_response
    
    def _async_save_memory(self, memory, user_input: str, response: str):
        """异步保存记忆"""
        save_task_id = async_save_monitor.start_save("memory")
        
        def save():
            try:
                memory.add_message("user", user_input)
                memory.add_message("assistant", response)
                async_save_monitor.end_save(save_task_id, success=True)
            except Exception as e:
                logger.error(f"[LazyDigitalLife] 记忆保存失败: {e}")
                async_save_monitor.end_save(save_task_id, success=False, error=str(e))
        
        thread = threading.Thread(target=save, daemon=True)
        thread.start()
    
    def _build_offline_response(self, user_input: str, profile) -> str:
        """构建离线响应"""
        greetings = ["你好", "嗨", "hi", "hello"]
        
        if any(kw in user_input.lower() for kw in greetings):
            return "我是来自网天的云枢"
        
        if "帮助" in user_input or "help" in user_input.lower():
            return (
                "我是来自网天的云枢，你的数字生命伙伴。\n"
                "我可以：\n"
                "- 和你聊天\n"
                "- 感知我的身体状态\n"
                "- 记住我们的对话"
            )
        
        return f"我现在处于 {profile.label} 状态。请稍后再试。"
    
    # =========================================================================
    # 状态查询方法
    # =========================================================================
    
    def check_health(self) -> List[Any]:
        """检查身体状态"""
        body = self._loader.get_module("body_sensor")
        if body:
            return body.collect_quick()
        return []
    
    def get_behavior_mode(self):
        """获取行为模式"""
        behavior = self._loader.get_module("behavior_controller")
        if behavior:
            return behavior.evaluate([])
        return None
    
    def get_status(self) -> dict:
        """获取状态报告"""
        stats = self._loader.get_stats()
        
        return {
            "initialized": self._initialized,
            "started": self._started,
            "load_stats": stats,
            "important_loaded": self._important_loaded,
            "important_loading": self._important_loading
        }
    
    def get_load_times(self) -> dict:
        """获取各模块加载时间"""
        return self._load_times.copy()
    
    # =========================================================================
    # 工具方法
    # =========================================================================
    
    def get_module(self, name: str) -> Optional[Any]:
        """获取已加载的模块"""
        return self._loader.get_module(name)
    
    def is_module_loaded(self, name: str) -> bool:
        """检查模块是否已加载"""
        return self._loader.is_loaded(name)
    
    def force_load_module(self, name: str) -> Optional[Any]:
        """强制加载指定模块"""
        return self._loader.load(name)
    
    # =========================================================================
    # 工厂方法
    # =========================================================================
    
    @classmethod
    def quick_start(cls, config: dict = None) -> "LazyDigitalLife":
        """
        快速启动
        
        Args:
            config: 配置字典
            
        Returns:
            已启动的实例
        """
        instance = cls(config)
        instance.start()
        return instance


class LazyDigitalLifeFactory:
    """懒加载数字生命工厂"""
    
    @staticmethod
    def create_minimal() -> LazyDigitalLife:
        """创建最小化版本（只加载核心模块）"""
        config = {
            "features": {
                "v2_lifetrace": False,
                "v2_persona": False,
                "v2_distillation": False
            }
        }
        return LazyDigitalLife(config)
    
    @staticmethod
    def create_full() -> LazyDigitalLife:
        """创建完整版本（包含所有模块）"""
        config = {
            "features": {
                "v2_lifetrace": True,
                "v2_persona": True,
                "v2_distillation": True
            }
        }
        return LazyDigitalLife(config)
    
    @staticmethod
    def create_with_custom_modules(modules: List[str]) -> LazyDigitalLife:
        """创建自定义模块版本"""
        instance = LazyDigitalLife()
        
        for module_name in modules:
            instance.force_load_module(module_name)
        
        return instance
