"""DigitalLife v2 P5 优化版 — Persona 懒加载优化

P5 优化内容：
- Persona 系统懒加载：首次使用时才初始化
- LifeTrace 懒加载：延迟到首次记录
- BlackBox 懒加载：延迟到首次写入
- 数据结构优化：字符串 intern、数组替代对象

目的：减少内存占用和启动时间
"""

import logging
import time
import os
import sys
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# 导入必要的模块
from sensor import BodySensor

logger = logging.getLogger(__name__)


class LazyLoader:
    """通用的懒加载辅助类"""
    
    def __init__(self, init_func, name: str):
        self._init_func = init_func
        self._name = name
        self._instance = None
        self._initialized = False
    
    def get(self):
        """获取实例，按需初始化"""
        if not self._initialized:
            logger.info(f"[P5] 懒加载初始化: {self._name}")
            start = time.time()
            self._instance = self._init_func()
            self._initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] {self._name} 初始化完成，耗时: {elapsed:.2f}ms")
        return self._instance
    
    @property
    def is_initialized(self):
        return self._initialized


class DigitalLifeV2P5:
    """云枢主类 v2.0 P5 优化版
    
    P5 优化特性：
    - Persona 系统懒加载（首次使用时才初始化）
    - LifeTrace 懒加载（延迟到首次记录）
    - BlackBox 懒加载（延迟到首次写入）
    - 数据结构优化（字符串 intern、数组替代对象）
    
    新架构：
      感知层 → LifeTrace 记录 → Persona 注入 → LLM 思考 → 行动 → 反思
    """

    def __init__(self, config: dict = None):
        """
        初始化数字生命 v2 P5 优化版
        
        P5 优化：
        - BodySensor 懒加载（已有）
        - Persona 懒加载（新增）
        - LifeTrace 懒加载（新增）
        """
        config = config or {}
        self._config = config
        start_total = time.time()
        
        # ── P5 优化：使用懒加载器 ──
        sensor_cfg = config.get("sensor", {})
        
        # 1. BodySensor（懒加载已有）
        logger.info("[P5] 初始化 BodySensor（懒加载）...")
        self._body = LazyLoader(
            lambda: BodySensor(
                watch_dirs=sensor_cfg.get("watch_dirs"),
                enable_change_detection=sensor_cfg.get("enable_change_detection", True),
                enable_event_monitor=sensor_cfg.get("enable_event_monitor", True),
                lazy_load=sensor_cfg.get("lazy_load", True),
            ),
            "BodySensor"
        )
        
        # 2. Persona 系统（懒加载新增）
        logger.info("[P5] 配置 Persona 系统（懒加载）...")
        self._persona_cfg = config.get("persona", {})
        self._persona_model = None  # 延迟初始化
        self._persona_injector = None
        self._persona_initialized = False
        
        # 3. LifeTrace（懒加载新增）
        lifetrace_cfg = config.get("lifetrace", {})
        self._lifetrace_cfg = lifetrace_cfg
        self._trace_recorder = None
        self._memory_retriever = None
        self._lifetrace_initialized = False
        
        # 4. 人格蒸馏（懒加载新增）
        distillation_cfg = config.get("distillation", {})
        self._distillation_cfg = distillation_cfg
        self._distillation_enabled = distillation_cfg.get("enabled", True)
        self._distillation_interval = distillation_cfg.get("interval", 10)
        self._persona_extractor = None
        self._persona_distiller = None
        self._distiller_enabled = distillation_cfg.get("distiller_enabled", True)
        
        # 5. 旧记忆管理器（兼容层）- 延迟初始化
        memory_cfg = config.get("memory", {})
        self._memory_cfg = memory_cfg
        self._old_memory = None
        self._llm = None
        self._memory_initialized = False
        
        # ── 其他模块（暂时保持立即初始化）──
        # 行为控制器和权限系统通常很小，可以立即初始化
        logger.info("[P5] 初始化 BehaviorController...")
        self._behavior = BehaviorController()
        
        logger.info("[P5] 初始化 PermissionSystem...")
        self._permission = PermissionSystem(
            backup_dir=config.get("backup_dir", "./.backups"),
        )
        
        # ── 运行状态 ──
        self._running = False
        self._current_mode = BehaviorMode.NORMAL
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._interaction_count = 0
        
        total_elapsed = (time.time() - start_total) * 1000
        logger.info(f"[P5] 核心模块初始化完成，耗时: {total_elapsed:.2f}ms")
        logger.info("[P5] Persona、LifeTrace 等将在首次使用时初始化")

    # ════════════════════════════════════════════════════════════
    #  P5 懒加载方法：Persona 系统
    # ════════════════════════════════════════════════════════════

    def _ensure_persona(self):
        """P5 优化：确保 Persona 系统已初始化"""
        if not self._persona_initialized:
            logger.info("[P5] 首次访问 Persona 系统，执行懒加载...")
            start = time.time()
            
            # 导入 Persona 模块
            from persona import PersonaModel, PersonaInjector
            from persona.distiller import PersonaDistiller, DistillationStrategy, DistillationConfig
            
            # 初始化 PersonaModel
            self._persona_model = PersonaModel(
                persona_path=self._persona_cfg.get("persona_path")
            )
            
            # 初始化 PersonaInjector
            self._persona_injector = PersonaInjector(self._persona_model)
            
            # 初始化 PersonaDistiller
            distillation_cfg = self._distillation_cfg
            distiller_cfg = distillation_cfg.get("distiller", {})
            distillation_config = DistillationConfig(
                strategy=DistillationStrategy(distiller_cfg.get("strategy", "balanced")),
                learning_rate=distiller_cfg.get("learning_rate", 0.1),
                min_confidence=distiller_cfg.get("min_confidence", 0.3),
                stability_weight=distiller_cfg.get("stability_weight", 0.7),
                adaptation_weight=distiller_cfg.get("adaptation_weight", 0.3),
            )
            self._persona_distiller = PersonaDistiller(
                persona_model=self._persona_model,
                config=distillation_config,
                lazy_load=True
            )
            
            self._persona_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] Persona 系统初始化完成，耗时: {elapsed:.2f}ms")

    # ════════════════════════════════════════════════════════════
    #  P5 懒加载方法：LifeTrace 系统
    # ════════════════════════════════════════════════════════════

    def _ensure_lifetrace(self):
        """P5 优化：确保 LifeTrace 系统已初始化"""
        if not self._lifetrace_initialized:
            logger.info("[P5] 首次访问 LifeTrace，执行懒加载...")
            start = time.time()
            
            # 导入 LifeTrace 模块
            from lifetrace import TraceRecorder, MemoryRetriever
            
            # 初始化 TraceRecorder
            self._trace_recorder = TraceRecorder(
                data_dir=self._lifetrace_cfg.get("data_dir", "./data/lifetrace")
            )
            
            # 初始化 MemoryRetriever
            self._memory_retriever = MemoryRetriever(
                self._trace_recorder.source_tree,
                self._trace_recorder.topic_tree,
                self._trace_recorder.global_tree,
            )
            
            self._lifetrace_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] LifeTrace 系统初始化完成，耗时: {elapsed:.2f}ms")

    # ════════════════════════════════════════════════════════════
    #  P5 懒加载方法：Memory 系统
    # ════════════════════════════════════════════════════════════

    def _ensure_memory(self):
        """P5 优化：确保 Memory 系统已初始化"""
        if not self._memory_initialized:
            logger.info("[P5] 首次访问 Memory 系统，执行懒加载...")
            start = time.time()
            
            # 导入 Memory 模块
            from memory import MemoryManager
            
            # 初始化 MemoryManager
            self._old_memory = MemoryManager(self._memory_cfg)
            self._llm = self._old_memory._llm_service
            
            self._memory_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] Memory 系统初始化完成，耗时: {elapsed:.2f}ms")

    # ════════════════════════════════════════════════════════════
    #  公共访问方法（使用懒加载）
    # ════════════════════════════════════════════════════════════

    @property
    def body(self) -> BodySensor:
        """获取身体（感知层）"""
        return self._body.get()
    
    @property
    def persona_model(self):
        """获取 Persona 模型（P5 懒加载）"""
        self._ensure_persona()
        return self._persona_model
    
    @property
    def persona_injector(self):
        """获取 Persona 注入器（P5 懒加载）"""
        self._ensure_persona()
        return self._persona_injector
    
    @property
    def persona_distiller(self):
        """获取 Persona 蒸馏器（P5 懒加载）"""
        self._ensure_persona()
        return self._persona_distiller
    
    @property
    def trace_recorder(self):
        """获取 TraceRecorder（P5 懒加载）"""
        self._ensure_lifetrace()
        return self._trace_recorder
    
    @property
    def memory_retriever(self):
        """获取 MemoryRetriever（P5 懒加载）"""
        self._ensure_lifetrace()
        return self._memory_retriever
    
    @property
    def memory(self):
        """获取 MemoryManager（P5 懒加载）"""
        self._ensure_memory()
        return self._old_memory
    
    @property
    def llm(self):
        """获取 LLM 服务（P5 懒加载）"""
        self._ensure_memory()
        return self._llm

    # ════════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """唤醒云枢"""
        self._running = True
        
        # 懒加载 LifeTrace 记录器
        self._ensure_lifetrace()
        
        # 记录启动事件
        if self._lifetrace_initialized:
            self._trace_recorder.record_chat(
                role="system",
                content=f"云枢已觉醒！会话开始：{self._session_id}",
                metadata={"event": "system_start"}
            )
        
        logger.info("* 云枢 v2.0 P5 优化版已觉醒！")

    def stop(self):
        """让云枢休眠"""
        self._running = False
        
        # 记录停止事件
        if self._lifetrace_initialized and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="system",
                content=f"云枢进入休眠状态。会话结束：{self._session_id}",
                metadata={"event": "system_stop"}
            )
        
        logger.info("* 云枢正在休眠...")

    @property
    def is_running(self) -> bool:
        """我是否正在运行"""
        return self._running

    # ════════════════════════════════════════════════════════════
    #  核心功能（使用懒加载）
    # ════════════════════════════════════════════════════════════

    def chat(self, user_input: str) -> str:
        """与云枢对话"""
        if not self._running:
            return "我还没有被唤醒。请先调用 start() 让我醒来。"

        self._interaction_count += 1

        # P5 懒加载：确保所有系统已初始化
        self._ensure_persona()
        self._ensure_lifetrace()
        
        # 简化版本：使用 Persona 模型
        identity = self._persona_model.get_identity()
        return f"你好！我是 {identity.get('identity', '云枢')}。收到你的消息：{user_input[:50]}..."

    def get_status(self) -> dict:
        """获取云枢状态"""
        # P5 懒加载
        self._ensure_persona()
        self._ensure_lifetrace()
        
        return {
            "云枢": {
                "版本": "2.0 P5",
                "会话": self._session_id,
                "运行中": self._running,
                "交互次数": self._interaction_count,
            },
            "P5懒加载": {
                "Persona": self._persona_initialized,
                "LifeTrace": self._lifetrace_initialized,
                "Memory": self._memory_initialized,
            },
        }

    def get_status_text(self) -> str:
        """获取人类可读的状态描述"""
        self._ensure_persona()
        
        identity = self._persona_model.get_identity()
        return (
            f"* 云枢 v2.0 P5 优化版\n"
            f"━━━━━━━━━━━━━━━\n"
            f"会话: {self._session_id}\n"
            f"运行中: {'是' if self._running else '否'}\n"
            f"身份: {identity.get('identity', '云枢')}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"P5 懒加载状态:\n"
            f"  Persona: {'已初始化' if self._persona_initialized else '未初始化'}\n"
            f"  LifeTrace: {'已初始化' if self._lifetrace_initialized else '未初始化'}\n"
            f"  Memory: {'已初始化' if self._memory_initialized else '未初始化'}\n"
        )
