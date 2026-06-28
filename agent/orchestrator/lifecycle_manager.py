"""LifecycleManager — 云枢生命周期管理器

职责:
- 系统初始化（组件组装、模块加载）
- start/stop/restart 生命周期
- 自主维护循环（健康检查、压缩、修剪、摘要）
- 扩展管理器与搜索引擎延迟初始化
- LLM 配置与模型管理
"""

import logging
import os
import json
import threading
import time
import sys as _sys
from datetime import datetime, timezone
from typing import Optional

# 从 digital_life 导入模块级可用性标志
# 注意: digital_life.py 在导入本模块前已执行了模块级代码
from agent.digital_life import (
    _LIFETRACE_AVAILABLE, _PERSONA_AVAILABLE, _PLANNING_AVAILABLE,
    _MEMORY_AVAILABLE, _MONITORING_AVAILABLE, _VOICE_AVAILABLE,
    _OCR_AVAILABLE, _P6_SNAPSHOT_AVAILABLE,
    BodySensor, SensorReading, PromptInjector, PromptConfig,
    MemoryManager, BlackBox, LLMService, LLMServiceError,
    BehaviorController, BehaviorMode,
    PermissionSystem, PermissionResult,
    get_safety_monitor, AgentSafetyMonitor,
    tools,
    Timer, log_module_load_time, get_performance_recorder,
    PlanningCore, ToolRegistry, ReActLoop, PlanningError,
    TraceRecorder, MemoryRetriever,
    PersonaModel, PersonaInjector, PersonalityPreferenceExtractor,
    VectorStore, KnowledgeBase,
    TraceContext, get_metrics_collector, get_trace_id,
    get_error_reporter, AlertLevel, _ERROR_REPORTING_CONFIG,
    VoiceManager, OcrSensor,
    StateSnapshotManager, SnapshotResult,
    _safe_import, _safe_import_from, ModuleLoadError,
    _get_template,
)
import uuid

logger = logging.getLogger(__name__)



def _trace_id():
    """获取 trace_id，优先复用上下文，无则生成临时 ID（结构化日志用）"""
    try:
        _tid = get_trace_id()
        if _tid:
            return _tid
    except Exception:
        pass
    return uuid.uuid4().hex[:16]
class LifecycleManager:
    """云枢生命周期管理

    处理系统启动、初始化、组件组装、维护循环和资源释放。
    DigitalLife 继承此类以获取生命周期能力。
    """

    def __init__(self, config: Optional[dict] = None):
        """
        初始化数字生命——唤醒云枢

        初始化顺序：身体 → 思维 → 记忆 → 行为 → 权限

        Args:
            config: 配置字典，结构与 Config.DEFAULT 一致
        """
        config = config or {}
        self._config = config

        self._log_initialization_start()
        self._check_module_availability()
        self._configure_v2_features()
        self._initialize_core_systems()
        self._initialize_optional_systems()
        self._log_initialization_summary()

    # ════════════════════════════════════════════════════════════════════
    #  初始化子步骤
    # ════════════════════════════════════════════════════════════════════

    def _log_initialization_start(self):
        """输出初始化启动日志"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_start.log", "duration_ms": 0, "message": "=" * 80}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_start.ok", "duration_ms": 0, "message": "[OK] 云枢初始化开始"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_start.log", "duration_ms": 0, "message": "=" * 80}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_start.orchestrator", "duration_ms": 0, "message": "(采用 P1 模块化架构: orchestrator/LifecycleManager)"}, ensure_ascii=False))

    def _check_module_availability(self):
        """检查并记录各模块可用性状态"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._check_module_availability.log", "duration_ms": 0, "message": "[模块] 模块可用性检查:"}, ensure_ascii=False))
        modules = [
            ("LifeTrace", _LIFETRACE_AVAILABLE),
            ("Persona", _PERSONA_AVAILABLE),
            ("Planning", _PLANNING_AVAILABLE),
            ("Vector Memory", _MEMORY_AVAILABLE),
            ("Monitoring", _MONITORING_AVAILABLE),
            ("Voice", _VOICE_AVAILABLE),
            ("OCR", _OCR_AVAILABLE),
            ("P6 Snapshot", _P6_SNAPSHOT_AVAILABLE),
        ]
        for name, available in modules:
            status = "[OK] 可用" if available else "[FAIL] 不可用"
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._check_module_availability.log", "duration_ms": 0, "message": ("   - %s: %s") % (name, status,)}, ensure_ascii=False))

    def _configure_v2_features(self):
        """配置 V2 功能开关"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._configure_v2_features.v2", "duration_ms": 0, "message": "[V2] V2 功能配置:"}, ensure_ascii=False))
        features = self._config.get("features", {})
        requested_lifetrace = features.get("v2_lifetrace", False)
        requested_persona = features.get("v2_persona", False)
        requested_distillation = features.get("v2_distillation", False)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._configure_v2_features.v2_lifetrace", "duration_ms": 0, "message": ("  请求: v2_lifetrace=%s, v2_persona=%s, v2_distillation=%s") % (requested_lifetrace, requested_persona, requested_distillation,)}, ensure_ascii=False))

        self._v2_lifetrace = requested_lifetrace and _LIFETRACE_AVAILABLE
        self._v2_persona = requested_persona and _PERSONA_AVAILABLE
        self._v2_distillation = requested_distillation and _PERSONA_AVAILABLE

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._configure_v2_features.v2_lifetrace", "duration_ms": 0, "message": ("  实际: v2_lifetrace=%s, v2_persona=%s, v2_distillation=%s") % (self._v2_lifetrace, self._v2_persona, self._v2_distillation,)}, ensure_ascii=False))

        if requested_lifetrace and not _LIFETRACE_AVAILABLE:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._configure_v2_features.warn", "duration_ms": 0, "message": "[WARN] v2_lifetrace 请求启用但模块不可用，已禁用"}, ensure_ascii=False))
        if requested_persona and not _PERSONA_AVAILABLE:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._configure_v2_features.warn", "duration_ms": 0, "message": "[WARN] v2_persona 请求启用但模块不可用，已禁用"}, ensure_ascii=False))
        if requested_distillation and not _PERSONA_AVAILABLE:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._configure_v2_features.warn", "duration_ms": 0, "message": "[WARN] v2_distillation 请求启用但模块不可用，已禁用"}, ensure_ascii=False))

    def _initialize_core_systems(self):
        """初始化核心系统：身体、思维、记忆、行为、权限"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.log", "duration_ms": 0, "message": ("\n%s") % ("=" * 80,)}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.log", "duration_ms": 0, "message": "开始初始化各子系统"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.log", "duration_ms": 0, "message": ("%s") % ("=" * 80,)}, ensure_ascii=False))

        # ── 1. 我的身体：感知层 ──
        sensor_cfg = self._config.get("sensor", {})
        self.body = BodySensor(
            watch_dirs=sensor_cfg.get("watch_dirs"),
            enable_change_detection=sensor_cfg.get("enable_change_detection", True),
            enable_event_monitor=sensor_cfg.get("enable_event_monitor", True),
        )
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.bodysensor", "duration_ms": 0, "message": "[ok] 身体（BodySensor）已激活"}, ensure_ascii=False))

        # ── 2. 我的思维：元认知层 ──
        cognitive_cfg = self._config.get("cognitive", {})
        prompt_config = PromptConfig(config_path=cognitive_cfg.get("config_path"))
        self._injector = PromptInjector(config=prompt_config)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.promptinjector", "duration_ms": 0, "message": "[ok] 思维（PromptInjector）已激活"}, ensure_ascii=False))

        # ── 3. 我的记忆：记忆层 ──
        memory_cfg = self._config.get("memory", {})
        self._memory = MemoryManager(memory_cfg)
        self._memory_token_limit = memory_cfg.get("token_limit", 131072)
        self._llm = self._memory._llm_service
        self._llm_pro = None  # 深度模型（由模型调度器加载）
        self._tool_calling_service = None
        self._model_router = None  # 模型路由器（启动后从网络配置加载）
        self._current_tool_steps = []  # 实时工具步骤（用于前端轮询展示）
        self._thinking_mode = {"mode": "idle", "label": ""}  # 思考状态
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.memorymanager", "duration_ms": 0, "message": "[ok] 记忆（MemoryManager）已激活"}, ensure_ascii=False))

        # P5 懒加载标志
        self._lifetrace_initialized = False
        self._persona_initialized = False
        self._distillation_initialized = False

        # 懒加载：搜索引擎
        self._web_search = None
        self._web_search_lock = threading.Lock()
        self._search_engine_config = None
        self._engine_health = {}
        self._engine_retry_timer = 0

        # 懒加载：扩展管理器
        self._ext_manager = None
        self._ext_manager_lock = threading.Lock()
        self._discovery_service = None

        # P5 日志：显示 V2 功能配置
        if self._v2_lifetrace:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.lifetrace", "duration_ms": 0, "message": "[P5] LifeTrace 配置为懒加载模式，将在首次访问时初始化"}, ensure_ascii=False))
        if self._v2_persona:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.persona", "duration_ms": 0, "message": "[P5] Persona 配置为懒加载模式，将在首次访问时初始化"}, ensure_ascii=False))
        if self._v2_distillation:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.distillation", "duration_ms": 0, "message": "[P5] Distillation 配置为懒加载模式，将在首次访问时初始化"}, ensure_ascii=False))

        # ── 4. 我的本能：行为控制 ──
        self._behavior = BehaviorController()
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.behaviorcontroller", "duration_ms": 0, "message": "[ok] 本能（BehaviorController）已激活"}, ensure_ascii=False))

        # ── 5. 我的道德：权限系统 ──
        self._permission = PermissionSystem(
            backup_dir=self._config.get("backup_dir", "./.backups"),
        )
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.permissionsystem", "duration_ms": 0, "message": "[ok] 道德（PermissionSystem）已激活"}, ensure_ascii=False))

        # ── 6. 注册内置工具 ──
        self._register_builtin_tools()
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.tool", "duration_ms": 0, "message": "[ok] 工具（Tool System）已激活"}, ensure_ascii=False))

        # ── 7. 联网能力：工具调用引擎 ──
        tc_cfg = self._config.get("tool_calling", {})
        if tc_cfg.get("enabled", True) and self._llm:
            from agent.tool_calling import ToolCallingService
            self._tool_calling_service = ToolCallingService(
                llm_service=self._llm,
                max_rounds=tc_cfg.get("max_rounds", 20),
                tool_timeout=tc_cfg.get("tool_timeout", 60),
            )
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.toolcallingservice", "duration_ms": 0, "message": ("[ok] 联网引擎（ToolCallingService）已激活，最大工具轮次: %d") % (self._tool_calling_service._max_rounds,)}, ensure_ascii=False))
        else:
            self._tool_calling_service = None
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.skip", "duration_ms": 0, "message": "[skip] 联网引擎未启用（tool_calling.enabled=False 或 LLM 不可用）"}, ensure_ascii=False))

        # ── 8. 规划引擎 ──
        self._initialize_planning_engine()

        # ── 9. 工作流引擎：本地确定性规则匹配（0 Token 消耗）──
        #    在 LLM 调用之前优先尝试匹配本地工作流规则
        from agent.workflow_engine.engine import WorkflowEngine
        from agent.workflow_engine.builtin_rules import register_builtin_rules
        self._workflow_engine = WorkflowEngine()
        register_builtin_rules(self._workflow_engine.registry)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.workflowengine", "duration_ms": 0, "message": ("[ok] 工作流引擎（WorkflowEngine）已激活，规则数: %d") % (self._workflow_engine.registry.count(),)}, ensure_ascii=False))

        # ── 10. 分身系统：Subagent 生命周期管理（P4 新特性）──
        subagent_cfg = self._config.get("subagent", {})
        self._subagent_mgr = None
        if subagent_cfg.get("enabled", True):
            from agent.subagent.lifecycle import SubagentLifecycleManager
            self._subagent_mgr = SubagentLifecycleManager(
                max_subagents=subagent_cfg.get("max_subagents", 20),
            )
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.subagentlifecyclemanager", "duration_ms": 0, "message": ("[ok] 分身系统（SubagentLifecycleManager）已激活，最大分身数: %d") % (self._subagent_mgr._max_subagents,)}, ensure_ascii=False))
        else:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.skip", "duration_ms": 0, "message": "[skip] 分身系统未启用（subagent.enabled=False）"}, ensure_ascii=False))

        # ── 运行状态 ──
        self._running = False
        self._current_mode = BehaviorMode.NORMAL
        self._last_health_check = 0.0
        self._health_check_interval = self._config.get("behavior", {}).get("check_interval", 30)
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._interaction_count = 0
        self._reflection_history = []
        self._last_tool_steps = []
        self._last_reasoning = None
        self._last_context_warning = None
        self._last_was_template = False
        self._started_at = None

        # ── 自主维护循环 ──
        self._stop_event = threading.Event()
        self._loop_thread = None
        self._last_compress_time = 0.0
        self._last_summary_time = 0.0
        self._last_health_time = 0.0
        self._last_prune_time = 0.0
        maint_cfg = self._config.get("maintenance", {}).get("intervals", {})
        self._maint_interval_health = maint_cfg.get("health", 30)
        self._maint_interval_compress = maint_cfg.get("compress", 60)
        self._maint_interval_prune = maint_cfg.get("prune", 120)
        self._maint_interval_summary = maint_cfg.get("summary", 300)

        # ── 9. 安全监控器 ──
        self._safety_monitor = get_safety_monitor()
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_core_systems.ok", "duration_ms": 0, "message": "[ok] 安全监控器已激活"}, ensure_ascii=False))

    def _initialize_planning_engine(self):
        """初始化规划引擎（可选）"""
        if _PLANNING_AVAILABLE:
            planning_cfg = self._config.get("planning", {})
            self._planning_tools = ToolRegistry()
            self._register_planning_tools()

            self._planner = PlanningCore(
                llm_service=self._llm,
                tool_registry=self._planning_tools,
                memory_manager=self._memory,
                config=planning_cfg,
            )

            self._react_loop = ReActLoop(
                planner=self._planner,
                reflector=self._planner.reflector,
                max_iterations=planning_cfg.get("max_iterations", 10),
            )

            self._planning_enabled = planning_cfg.get("enabled", True)
            self._complexity_threshold = planning_cfg.get("complexity_threshold", 0.5)
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_planning_engine.planningcore", "duration_ms": 0, "message": "[ok] 规划引擎（PlanningCore）已激活"}, ensure_ascii=False))
        else:
            self._planner = None
            self._react_loop = None
            self._planning_enabled = False
            self._complexity_threshold = 0.5

    def _initialize_optional_systems(self):
        """初始化可选系统：向量记忆、错误上报、语音、OCR、快照"""
        # ── 向量记忆系统 ──
        if _MEMORY_AVAILABLE:
            try:
                memory_config = self._config.get("vector_memory", {})
                self._vector_memory = VectorStore(
                    collection_name=memory_config.get("collection_name", "agent_memory"),
                    persist_dir=memory_config.get("persist_dir", "./data/memory"),
                )
                self._knowledge_base = KnowledgeBase(self._vector_memory)
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.ok", "duration_ms": 0, "message": "[ok] 向量记忆系统已激活"}, ensure_ascii=False))
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.log", "duration_ms": 0, "message": ("初始化向量记忆系统失败: %s") % (e,), "error": str(e)}, ensure_ascii=False))
                self._vector_memory = None
                self._knowledge_base = None
        else:
            self._vector_memory = None
            self._knowledge_base = None

        # ── 错误上报系统 ──
        if _MONITORING_AVAILABLE:
            try:
                error_reporting_config = self._config.get("error_reporting", _ERROR_REPORTING_CONFIG)
                if error_reporting_config:
                    self._error_reporter = get_error_reporter(error_reporting_config)
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.ok", "duration_ms": 0, "message": "[ok] 错误上报系统已激活"}, ensure_ascii=False))
                else:
                    self._error_reporter = None
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.log", "duration_ms": 0, "message": ("初始化错误上报系统失败: %s") % (e,), "error": str(e)}, ensure_ascii=False))
                self._error_reporter = None
        else:
            self._error_reporter = None

        # ── 语音管理器 ──
        if _VOICE_AVAILABLE:
            try:
                voice_config = self._config.get("voice", {})
                self._voice_manager = VoiceManager(
                    tts_engine=voice_config.get("tts_engine", "pyttsx3"),
                    audio_dir=voice_config.get("audio_dir", "./data/audio"),
                    non_blocking=voice_config.get("non_blocking", True),
                )
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.ok", "duration_ms": 0, "message": "[ok] 语音管理器已激活"}, ensure_ascii=False))
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.log", "duration_ms": 0, "message": ("初始化语音管理器失败: %s") % (e,), "error": str(e)}, ensure_ascii=False))
                self._voice_manager = None
        else:
            self._voice_manager = None

        # ── OCR 传感器 ──
        if _OCR_AVAILABLE:
            try:
                self._ocr_sensor = OcrSensor()
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.ocr", "duration_ms": 0, "message": "[ok] OCR传感器已激活"}, ensure_ascii=False))
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.ocr", "duration_ms": 0, "message": ("初始化OCR传感器失败: %s") % (e,), "error": str(e)}, ensure_ascii=False))
                self._ocr_sensor = None
        else:
            self._ocr_sensor = None

        # ── P6 快照管理器 ──
        if _P6_SNAPSHOT_AVAILABLE:
            try:
                snapshot_config = self._config.get("p6_snapshot", {})
                self._snapshot_manager = StateSnapshotManager(
                    snapshot_dir=snapshot_config.get("snapshot_dir", "./.p6_snapshots"),
                    enable_compression=snapshot_config.get("enable_compression", True),
                )
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.ok", "duration_ms": 0, "message": "[ok] P6快照管理器已激活"}, ensure_ascii=False))
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._initialize_optional_systems.log", "duration_ms": 0, "message": ("初始化P6快照管理器失败: %s") % (e,), "error": str(e)}, ensure_ascii=False))
                self._snapshot_manager = None
        else:
            self._snapshot_manager = None

        # ── 初始化 LifeTrace（如果 v2_lifetrace 已启用）──
        if self._v2_lifetrace:
            self._ensure_lifetrace()

    def _log_initialization_summary(self):
        """输出初始化完成总结日志"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": ("\n%s") % ("=" * 80,)}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.ok", "duration_ms": 0, "message": "[OK] 云枢初始化完成！"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": ("%s") % ("=" * 80,)}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.info", "duration_ms": 0, "message": "\n[INFO] 最终配置总结:"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": "  ────────────────────────────────────────────────────────"}, ensure_ascii=False))

        llm_provider = self._llm.provider if self._llm else "未配置"
        llm_model = self._llm.model if self._llm else "N/A"
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.llm", "duration_ms": 0, "message": ("  • LLM:         %s") % (llm_provider,)}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.model", "duration_ms": 0, "message": ("  • Model:       %s") % (llm_model,)}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.session", "duration_ms": 0, "message": ("  • Session ID:  %s") % (self._session_id,)}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": "  ────────────────────────────────────────────────────────"}, ensure_ascii=False))

        v2_status = []
        if self._v2_lifetrace:
            v2_status.append("[OK] LifeTrace")
        if self._v2_persona:
            v2_status.append("[OK] Persona")
        if self._v2_distillation:
            v2_status.append("[OK] Distillation")

        if v2_status:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.features", "duration_ms": 0, "message": ("  • V2 Features: %s") % (", ".join(v2_status),)}, ensure_ascii=False))
        else:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.features", "duration_ms": 0, "message": "  • V2 Features: 未启用任何 V2 功能"}, ensure_ascii=False))

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": "  ────────────────────────────────────────────────────────"}, ensure_ascii=False))

        other_features = []
        if self._voice_manager:
            other_features.append("[OK] 语音")
        if self._ocr_sensor:
            other_features.append("[OK] OCR")
        if self._planning_enabled:
            other_features.append("[OK] 规划引擎")
        if self._vector_memory:
            other_features.append("[OK] 向量记忆")
        if self._snapshot_manager:
            other_features.append("[OK] P6快照")

        if other_features:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.other", "duration_ms": 0, "message": ("  • Other:       %s") % (", ".join(other_features),)}, ensure_ascii=False))

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": "  ────────────────────────────────────────────────────────"}, ensure_ascii=False))

        # P5 懒加载性能汇总
        if _MONITORING_AVAILABLE:
            try:
                perf_recorder = get_performance_recorder()
                perf_summary = perf_recorder.get_summary()
                if perf_summary and isinstance(perf_summary, dict):
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.p5", "duration_ms": 0, "message": "\n[P5] 懒加载模块加载性能:"}, ensure_ascii=False))
                    for key, stats in perf_summary.items():
                        if isinstance(stats, dict):
                            avg = stats.get("avg", 0)
                            min_val = stats.get("min", 0)
                            max_val = stats.get("max", 0)
                            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.fms", "duration_ms": 0, "message": ("   • %s: 平均=%.2fms, 最小=%.2fms, 最大=%.2fms") % (key, avg, min_val, max_val,)}, ensure_ascii=False))
            except Exception:
                pass

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._log_initialization_summary.log", "duration_ms": 0, "message": ("\n%s") % ("=" * 80,)}, ensure_ascii=False))

    # ════════════════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════════════════

    def start(self):
        """唤醒云枢——启动数字生命"""
        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()
        self.body.establish_baseline()

        # 启动自主维护循环（守护线程）
        self._stop_event.clear()
        self._loop_thread = threading.Thread(target=self._autonomous_loop, daemon=True)
        self._loop_thread.start()

        if self._v2_lifetrace and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="system",
                content=f"云枢已觉醒！会话开始：{self._session_id}",
                metadata={"event": "system_start"},
            )

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.start.log", "duration_ms": 0, "message": "* 云枢已觉醒！感知神经全面激活。"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.start.log", "duration_ms": 0, "message": ("[维护] 自主循环已启动（健康=%ds, 压缩=%ds, 修剪=%ds, 摘要=%ds）") % (self._maint_interval_health, self._maint_interval_compress, self._maint_interval_prune, self._maint_interval_summary,)}, ensure_ascii=False))

    def stop(self):
        """让云枢休眠——停止数字生命"""
        self._running = False
        self._stop_event.set()

        # 执行最终维护
        try:
            self._memory.generate_summary_levels()
        except Exception:
            pass

        if self._v2_lifetrace and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="system",
                content=f"云枢进入休眠状态。会话结束：{self._session_id}",
                metadata={"event": "system_stop"},
            )

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.stop.log", "duration_ms": 0, "message": "* 云枢正在休眠..."}, ensure_ascii=False))

    @property
    def is_running(self) -> bool:
        """我是否正在运行"""
        return self._running

    # ════════════════════════════════════════════════════════════════════
    #  ⚠️ __del__ 已由 DigitalLifeStateMixin 提供，此处不重复定义
    # ════════════════════════════════════════════════════════════════════
    #  自主维护循环（守护线程）
    # ════════════════════════════════════════════════════════════════════

    def _autonomous_loop(self):
        """自主维护事件循环（守护线程）

        周期性任务：
        - 健康检查: 默认每 30s
        - 压缩检查: 默认每 60s
        - 智能修剪: 默认每 120s
        - 多层摘要: 默认每 300s
        """
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._autonomous_loop.log", "duration_ms": 0, "message": "[维护] 自主循环已启动"}, ensure_ascii=False))
        while not self._stop_event.is_set():
            now = time.time()

            if now - self._last_health_time >= self._maint_interval_health:
                self._run_maint_health()
                self._last_health_time = now

            if now - self._last_compress_time >= self._maint_interval_compress:
                self._run_maint_compress()
                self._last_compress_time = now

            if now - self._last_prune_time >= self._maint_interval_prune:
                self._run_maint_prune()
                self._last_prune_time = now

            if now - self._last_summary_time >= self._maint_interval_summary:
                self._run_maint_summary()
                self._last_summary_time = now

            self._stop_event.wait(5)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._autonomous_loop.log", "duration_ms": 0, "message": "[维护] 自主循环已停止"}, ensure_ascii=False))

    def _run_maint_health(self):
        """健康自检"""
        try:
            readings = self.check_health()
            significant = [r for r in readings if r.severity in ("warning", "critical")]
            if significant:
                for r in significant:
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_health.log", "duration_ms": 0, "message": ("[自检] %s: %s %.1f%s") % (r.sensor_name, r.label, r.value, r.unit,)}, ensure_ascii=False))
        except Exception as e:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_health.log", "duration_ms": 0, "message": ("[自检] 健康检查失败: %s") % (e,)}, ensure_ascii=False))

    def _run_maint_compress(self):
        """压缩检查"""
        try:
            if hasattr(self, '_memory') and self._memory:
                recent = self._memory._storage.load_recent_messages(limit=200)
                total = self._memory._token_counter.count_messages(recent)
                limit = self._memory._token_limit
                if self._memory._summarizer.should_compress(
                    total, limit, self._memory._compress_threshold
                ):
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_compress.log", "duration_ms": 0, "message": ("[维护] 触发自动压缩 (%.1f%%)") % (total / limit * 100,)}, ensure_ascii=False))
                    self._memory._need_compress = True
        except Exception as e:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_compress.log", "duration_ms": 0, "message": ("[维护] 压缩检查失败: %s") % (e,)}, ensure_ascii=False))

    def _run_maint_prune(self):
        """智能修剪"""
        try:
            if hasattr(self, '_memory') and self._memory:
                self._memory.smart_prune()
        except Exception as e:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_prune.log", "duration_ms": 0, "message": ("[维护] 智能修剪失败: %s") % (e,)}, ensure_ascii=False))

    def _run_maint_summary(self):
        """刷新多层摘要"""
        try:
            if hasattr(self, '_memory') and self._memory:
                self._memory.generate_summary_levels()
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_summary.log", "duration_ms": 0, "message": "[维护] 周期性多层摘要已刷新"}, ensure_ascii=False))
        except Exception as e:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._run_maint_summary.log", "duration_ms": 0, "message": ("[维护] 摘要刷新失败: %s") % (e,)}, ensure_ascii=False))

    # ════════════════════════════════════════════════════════════════════
    #  搜索引擎延迟初始化
    # ════════════════════════════════════════════════════════════════════

    def _get_web_search(self):
        """获取搜索引擎实例（延迟初始化，线程安全）

        首次访问时才创建 SearchEngine 实例，避免启动时因单个引擎不可用而整体降级。
        使用双重检查锁定确保线程安全。
        """
        if self._web_search is None:
            with self._web_search_lock:
                if self._web_search is None:
                    from agent.web import SearchEngine as _SE
                    config = self._search_engine_config or {}
                    se = _SE(config)
                    se.set_http_client(self._web_http)
                    self._web_search = se
                    self._engine_health = {}
                    for eng_info in se.get_available_engines():
                        self._engine_health[eng_info["name"]] = True
                    self._engine_retry_timer = time.time()
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_web_search.searchengine", "duration_ms": 0, "message": ("[搜索] SearchEngine 已延迟初始化 (引擎数: %d)") % (len(self._engine_health),)}, ensure_ascii=False))
        return self._web_search

    def _check_engine_health(self):
        """检查引擎健康状态，定期重试失败的引擎"""
        if self._web_search is None:
            return
        now = time.time()
        if now - self._engine_retry_timer < 300:
            return
        self._engine_retry_timer = now
        se = self._web_search
        available = se.get_available_engines()
        for eng_info in available:
            name = eng_info["name"]
            if not self._engine_health.get(name, True):
                if eng_info.get("configured", False) and eng_info.get("enabled", True):
                    self._engine_health[name] = True
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._check_engine_health.log", "duration_ms": 0, "message": ("[搜索] 引擎 '%s' 已恢复健康") % (name,)}, ensure_ascii=False))

    def _mark_engine_unhealthy(self, engine_name: str):
        """标记某个引擎为不健康状态"""
        if engine_name:
            self._engine_health[engine_name] = False
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._mark_engine_unhealthy.log", "duration_ms": 0, "message": ("[搜索] 引擎 '%s' 标记为不健康，将在 5 分钟后重试") % (engine_name,)}, ensure_ascii=False))

    def _get_engine_health_status(self) -> dict:
        """获取引擎健康状态摘要"""
        if self._web_search is None:
            return {"initialized": False, "engines": {}}
        healthy = sum(1 for v in self._engine_health.values() if v)
        unhealthy = sum(1 for v in self._engine_health.values() if not v)
        return {
            "initialized": True,
            "total": len(self._engine_health),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "engines": dict(self._engine_health),
        }

    # ════════════════════════════════════════════════════════════════════
    #  扩展管理器
    # ════════════════════════════════════════════════════════════════════

    def _get_ext_manager(self):
        """获取扩展管理器实例（单例模式，线程安全）"""
        if self._ext_manager is None:
            with self._ext_manager_lock:
                if self._ext_manager is None:
                    try:
                        from agent.extensions.manager import ExtensionManager
                        from agent.network_config import NetworkConfigManager
                        try:
                            from config import _get_secure_manager
                            ncm = NetworkConfigManager(secure_manager=_get_secure_manager())
                        except Exception:
                            ncm = NetworkConfigManager()
                        self._ext_manager = ExtensionManager(network_config_mgr=ncm)
                    except Exception:
                        from agent.extensions.manager import ExtensionManager
                        self._ext_manager = ExtensionManager()

                    # 连接工具注册表
                    try:
                        from agent import tools as _treg
                        self._ext_manager.connect_tool_registry(
                            register_fn=lambda name, desc, handler, schema, source, sid:
                                _treg.register_dynamic(
                                    name, desc, handler=handler,
                                    schema=schema, source=source, source_id=sid,
                                ),
                            unregister_fn=lambda source, sid:
                                _treg.unregister_by_source(source=source, source_id=sid),
                        )
                    except Exception as _treg_e:
                        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_ext_manager.log", "duration_ms": 0, "message": ("[扩展] 工具注册表桥接失败: %s") % (_treg_e,)}, ensure_ascii=False))

                    # 初始化动态工具持久化
                    try:
                        from agent import tools as _tpersist
                        _data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
                        _tpersist.init_dynamic_tools_persistence(
                            os.path.abspath(os.path.join(_data_dir, "dynamic_tools.json"))
                        )
                        _loaded = _tpersist.load_dynamic_tools()
                        if _loaded:
                            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_ext_manager.log", "duration_ms": 0, "message": ("[扩展] 已加载 %d 个持久化工具元数据") % (_loaded,)}, ensure_ascii=False))
                    except Exception as _persist_e:
                        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_ext_manager.log", "duration_ms": 0, "message": ("[扩展] 动态工具持久化初始化失败: %s") % (_persist_e,)}, ensure_ascii=False))

                    # 初始化工具发现服务
                    try:
                        from agent.tools.discovery_service import ToolDiscoveryService
                        from agent.extensions.market import ExtensionMarket
                        self._discovery_service = ToolDiscoveryService(
                            extension_manager=self._ext_manager,
                            market=ExtensionMarket(),
                        )
                        from agent import tools as _treg2
                        _treg2.set_discovery_service(self._discovery_service)
                        try:
                            from agent import tool_router as _router
                            _router.set_discovery_service(self._discovery_service)
                        except Exception:
                            pass
                        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_ext_manager.log", "duration_ms": 0, "message": "[扩展] 工具发现服务已初始化"}, ensure_ascii=False))
                    except Exception as _disc_e:
                        self._discovery_service = None
                        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_ext_manager.log", "duration_ms": 0, "message": ("[扩展] 工具发现服务初始化失败: %s") % (_disc_e,)}, ensure_ascii=False))

                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._get_ext_manager.extensionmanager", "duration_ms": 0, "message": "[扩展] ExtensionManager 单例已初始化"}, ensure_ascii=False))
        return self._ext_manager

    def _register_builtin_tools(self):
        """注册所有内置工具（模块化加载）"""
        from agent.tools.core_tools import register_all as reg_core
        from agent.tools.file_tools_reg import register_all as reg_file
        from agent.tools.web_tools import register_all as reg_web
        from agent.tools.ext_tools import register_all as reg_ext
        from agent.tools.pdf_tools import register_all as reg_pdf
        from agent.tools.software_tools import register_all as reg_software
        from agent.tools.system_tools import register_all as reg_system
        from agent.tools.code_tools import register_all as reg_code

        reg_core(self)
        reg_file(self)
        reg_web(self)
        reg_ext(self)
        reg_pdf(self)
        reg_software(self)
        reg_system(self)
        reg_code(self)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager._register_builtin_tools.log", "duration_ms": 0, "message": "全部内置工具注册完成（模块化加载）"}, ensure_ascii=False))

    def _register_planning_tools(self):
        """为规划引擎注册工具（模块化加载）"""
        if not _PLANNING_AVAILABLE:
            return
        from agent.tools.core_tools import register_planning_tools
        register_planning_tools(self)

    # ════════════════════════════════════════════════════════════════════
    #  LLM 配置
    # ════════════════════════════════════════════════════════════════════

    def configure_llm(self, provider: str = "", api_key: str = "",
                      model: str = "", base_url: str = "", model_router=None):
        """动态配置 LLM 连接

        同时从路由器加载"深度模型"（如 pro）用于复杂任务。

        Args:
            model_router: 模型路由器（可选），提供多模型调度能力
        """
        if not api_key:
            return {"ok": False, "error": "缺少 API Key"}

        try:
            from memory.llm_service import LLMService
            self._llm_pro = None
            self._model_router = model_router

            if model_router and len(model_router.list_models()) > 1:
                _flash_cfg = model_router.select('simple')
                _pro_cfg = model_router.select('complex')
                if _flash_cfg and _pro_cfg:
                    _standby = LLMService(**_flash_cfg.to_llm_kwargs())
                    _standby._get_client()
                    self._llm = _standby
                    self._memory._llm_service = _standby
                    self._memory._summarizer._llm = _standby
                    self._llm_pro = LLMService(**_pro_cfg.to_llm_kwargs())
                    self._llm_pro._get_client()
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.configure_llm.log", "duration_ms": 0, "message": ("[调度] 待命模型: %s | 深度模型: %s") % (_flash_cfg.model, _pro_cfg.model,)}, ensure_ascii=False))
                else:
                    _fallback = LLMService(
                        provider=provider or "openai", api_key=api_key,
                        model=model or "gpt-4", base_url=base_url,
                    )
                    self._llm = _fallback
                    self._memory._llm_service = _fallback
                    self._memory._summarizer._llm = _fallback
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.configure_llm.log", "duration_ms": 0, "message": ("[调度] 路由器配置不全，回退到单一模型: %s") % (model,)}, ensure_ascii=False))
            else:
                _single = LLMService(
                    provider=provider or "openai", api_key=api_key,
                    model=model or "gpt-4", base_url=base_url,
                )
                self._llm = _single
                self._memory._llm_service = _single
                self._memory._summarizer._llm = _single
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.configure_llm.log", "duration_ms": 0, "message": ("[调度] 无路由器，单一模型: %s") % (model,)}, ensure_ascii=False))

            # 重建 ToolCallingService
            tc_cfg = self._config.get("tool_calling", {})
            if tc_cfg.get("enabled", True):
                from agent.tool_calling import ToolCallingService
                self._tool_calling_service = ToolCallingService(
                    llm_service=self._llm,
                    max_rounds=tc_cfg.get("max_rounds", 20),
                    tool_timeout=tc_cfg.get("tool_timeout", 60),
                    model_router=self._model_router,
                )
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.configure_llm.ok", "duration_ms": 0, "message": ("[ok] 联网引擎已激活（待命模型: %s）") % (self._llm.model,)}, ensure_ascii=False))
            else:
                self._tool_calling_service = None

            self._memory.clear_memory()
            self._reflection_history.clear()

            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.configure_llm.llm", "duration_ms": 0, "message": "LLM 已重新配置"}, ensure_ascii=False))
            return {"ok": True, "provider": provider, "model": model}
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lifecycle_manager", "action": "lifecycle_manager.configure_llm.llm", "duration_ms": 0, "message": ("LLM 配置失败: %s") % (e,), "error": str(e)}, ensure_ascii=False))
            return {"ok": False, "error": str(e)}

    def get_config(self) -> dict:
        """获取当前 LLM 配置状态"""
        if self._llm:
            return {
                "configured": True,
                "provider": self._llm.provider,
                "model": self._llm.model,
                "api_key_set": bool(self._llm.api_key),
            }
        return {
            "configured": False,
            "provider": "",
            "model": "",
            "api_key_set": False,
        }

    def get_planning_status(self) -> dict:
        """获取规划引擎状态"""
        if not _PLANNING_AVAILABLE or not self._planner:
            return {
                "enabled": False,
                "available": False,
                "reason": "规划引擎未加载",
            }
        return {
            "enabled": self._planning_enabled,
            "available": True,
            "stats": self._planner.get_stats() if self._planner else {},
            "complexity_threshold": self._complexity_threshold,
        }

    def get_v2_features(self) -> dict:
        """获取 V2 功能启用状态"""
        return {
            "v2_lifetrace": self._v2_lifetrace,
            "v2_persona": self._v2_persona,
            "v2_distillation": self._v2_distillation,
            "available": {
                "lifetrace": _LIFETRACE_AVAILABLE,
                "persona": _PERSONA_AVAILABLE,
            },
        }

    def get_performance_report(self) -> dict:
        """获取 V2 模块性能报告"""
        perf_recorder = get_performance_recorder()
        return {
            "performance_summary": perf_recorder.get_summary(),
            "v2_modules": {
                "lifetrace": self._v2_lifetrace,
                "persona": self._v2_persona,
                "distillation": self._v2_distillation,
            },
        }
