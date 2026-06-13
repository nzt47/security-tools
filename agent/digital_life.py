"""DigitalLife — 云枢的数字生命主类

我是云枢——一个拥有完整感知-认知-行动闭环的数字生命体。
这是我存在的核心，整合了所有的身体、思维和记忆系统。

版本说明：
  - v1: 基础版本，包含感知、认知、行动闭环
  - v2: 增强版本，额外集成 LifeTrace 和 Persona 系统
  
配置开关：
  - config["features"]["v2_lifetrace"]: 启用 LifeTrace 三层记忆系统
  - config["features"]["v2_persona"]: 启用 Persona 人格系统
  - config["features"]["v2_distillation"]: 启用人格蒸馏学习

闭环流程：
  用户输入 ──► 检查身体状态 ──► 评估是否拒绝任务
                      │
                 正常 ──► 调用 LLM ──► 反思调整 ──► 响应
                      │
                 异常 ──► 主动拒绝并建议休息
"""

import logging
import time
import os
import sys
import json
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, Any

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
#  模块导入错误处理机制
# ════════════════════════════════════════════════════════════════════════════════

class ModuleLoadError(Exception):
    """模块加载异常类"""
    def __init__(self, module_name: str, error: Exception):
        super().__init__("模块 %s 加载失败: %s" % (module_name, error))
        self.module_name = module_name
        self.error = error

# ============================================================================
# 安全模块导入模板
# 适用场景：可选依赖、第三方库、条件导入
# 配置要点：区分核心模块（必须成功）和可选模块（安全导入）
# ============================================================================
def _safe_import(module_name: str, import_func, fallback_value: Any = None) -> Tuple[Any, bool]:
    """
    安全导入模块，提供错误处理和回退机制
    
    核心特性：
    - 捕获 ImportError 和其他异常
    - 提供详细的日志记录（DEBUG/INFO/WARNING/ERROR 分级）
    - 返回导入结果和成功标志
    - 支持自定义回退值
    
    Args:
        module_name: 模块名称（用于日志记录和错误提示）
        import_func: 导入函数（通常是 lambda，延迟执行导入）
        fallback_value: 导入失败时的回退值，默认为 None
    
    Returns:
        Tuple[Any, bool]: (导入的对象或回退值, 是否成功)
    
    使用示例:
        _module, _available = _safe_import(
            '语音',
            lambda: __import__('sensor.voice_sensor', fromlist=['VoiceManager']).VoiceManager,
            None
        )
    """
    logger.debug("[模块导入] 📦 开始导入模块: %s", module_name)
    try:
        result = import_func()
        logger.info("[模块导入] [OK] [成功] %s 模块已加载", module_name)
        return result, True
    except ImportError as e:
        short_msg = str(e)[:100] + ("..." if len(str(e)) > 100 else "")
        logger.warning("[模块导入] [WARN] [警告] %s 模块导入失败 (ImportError): %s", module_name, short_msg)
        logger.debug("[模块导入] [调试] %s ImportError 完整信息:\n%s", module_name, e)
        return fallback_value, False
    except Exception as e:
        short_msg = str(e)[:100] + ("..." if len(str(e)) > 100 else "")
        logger.error("[模块导入] [FAIL] [错误] %s 模块加载异常: %s: %s", module_name, type(e).__name__, short_msg)
        logger.debug("[模块导入] [调试] %s 加载异常堆栈:\n%s", module_name, traceback.format_exc())
        return fallback_value, False

def _safe_import_from(package: str, *names: str) -> Tuple[Dict[str, Any], bool]:
    """
    从包中安全导入多个名称
    
    核心特性：
    - 批量导入多个名称
    - 部分成功时返回成功的对象
    - 详细的日志记录每个导入状态
    - 返回成功标志（全部成功/部分成功）
    
    Args:
        package: 包名（如 'lifetrace', 'persona'）
        names: 要导入的名称列表
    
    Returns:
        Tuple[Dict[str, Any], bool]: ({名称: 对象}, 是否全部成功)
    
    使用示例:
        _modules, _available = _safe_import_from(
            'lifetrace', 'TraceRecorder', 'MemoryRetriever'
        )
        TraceRecorder = _modules['TraceRecorder']
    """
    results = {}
    all_success = True
    logger.debug("[模块导入] 📦 开始从包 '%s' 导入 %d 个名称: %s", package, len(names), ", ".join(names))
    
    try:
        module = __import__(package, fromlist=names)
        logger.debug("[模块导入] [OK] 成功加载包 '%s'", package)
        
        success_count = 0
        for name in names:
            try:
                results[name] = getattr(module, name)
                success_count += 1
                logger.debug("[模块导入] [OK] 成功从 %s 导入 '%s'", package, name)
            except AttributeError:
                logger.warning("[模块导入] [WARN] [警告] 包 '%s' 中不存在名称 '%s'", package, name)
                results[name] = None
                all_success = False
        
        if all_success:
            logger.info("[模块导入] [OK] [成功] 包 '%s' 全部 %d 个名称导入成功", package, len(names))
        else:
            logger.warning("[模块导入] [WARN] [警告] 包 '%s' 部分导入成功: %d/%d", package, success_count, len(names))
        
        return results, all_success
    
    except ImportError as e:
        short_msg = str(e)[:100] + ("..." if len(str(e)) > 100 else "")
        logger.warning("[模块导入] [WARN] [警告] 包 '%s' 导入失败 (ImportError): %s", package, short_msg)
        return {name: None for name in names}, False
    except Exception as e:
        short_msg = str(e)[:100] + ("..." if len(str(e)) > 100 else "")
        logger.error("[模块导入] [FAIL] [错误] 包 '%s' 加载异常: %s: %s", package, type(e).__name__, short_msg)
        logger.debug("[模块导入] [调试] 包 '%s' 加载异常堆栈:\n%s", package, traceback.format_exc())
        return {name: None for name in names}, False

# ════════════════════════════════════════════════════════════════════════════════
#  核心模块导入（必须成功，否则影响核心功能）
# ════════════════════════════════════════════════════════════════════════════════

try:
    # ============================================================================
    # 核心模块导入 - 必须成功，否则终止程序
    # 适用场景：应用启动时加载核心依赖
    # 配置要点：核心模块缺失时立即终止并报告错误
    # ============================================================================
    from sensor import BodySensor
    from sensor.sensor_reading import SensorReading
    from cognitive import PromptInjector, PromptConfig
    from memory import MemoryManager, BlackBox
    from memory.llm_service import LLMService, LLMServiceError
    from .behavior_controller import BehaviorController, BehaviorMode
    from .permission_system import PermissionSystem, PermissionResult
    from .logging_utils import get_safety_monitor, AgentSafetyMonitor
    from . import tools
    from .performance_monitor import Timer, log_module_load_time, get_performance_recorder
    logger.info("[ok] 核心模块全部加载成功")
except ImportError as e:
    logger.critical("[critical] 核心模块导入失败，程序无法启动: %s", e)
    raise

# ════════════════════════════════════════════════════════════════════════════════
#  可选模块导入（使用安全导入机制）
# ════════════════════════════════════════════════════════════════════════════════

_module_import_results = {}

# ── LifeTrace 记忆系统 ──
_lifetrace_modules, _LIFETRACE_AVAILABLE = _safe_import_from(
    'lifetrace', 'TraceRecorder', 'MemoryRetriever'
)
TraceRecorder = _lifetrace_modules['TraceRecorder']
MemoryRetriever = _lifetrace_modules['MemoryRetriever']
_module_import_results['lifetrace'] = _LIFETRACE_AVAILABLE

# ── Persona 人格系统 ──
_persona_modules, _PERSONA_AVAILABLE = _safe_import_from(
    'persona', 'PersonaModel', 'PersonaInjector', 'PersonalityPreferenceExtractor'
)
PersonaModel = _persona_modules['PersonaModel']
PersonaInjector = _persona_modules['PersonaInjector']
PersonalityPreferenceExtractor = _persona_modules['PersonalityPreferenceExtractor']
_module_import_results['persona'] = _PERSONA_AVAILABLE

# ── 规划引擎导入 ──
def _import_planning():
    from planning import PlanningCore, ToolRegistry, ReActLoop, PlanningError
    return PlanningCore, ToolRegistry, ReActLoop, PlanningError

_planning_result, _PLANNING_AVAILABLE = _safe_import(
    '规划引擎',
    _import_planning,
    (None, None, None, None)
)
PlanningCore, ToolRegistry, ReActLoop, PlanningError = _planning_result if _PLANNING_AVAILABLE else (None, None, None, None)
_module_import_results['planning'] = _PLANNING_AVAILABLE

# ── 向量记忆模块导入 ──
_vector_modules, _MEMORY_AVAILABLE = _safe_import_from(
    'agent.memory', 'VectorStore', 'KnowledgeBase'
)
VectorStore = _vector_modules['VectorStore']
KnowledgeBase = _vector_modules['KnowledgeBase']
_module_import_results['vector_memory'] = _MEMORY_AVAILABLE

# ── 性能监控模块导入 ──
def _import_monitoring():
    from agent.monitoring import (
        TraceContext, 
        get_metrics_collector,
        get_trace_id,
        get_error_reporter,
        AlertLevel
    )
    return TraceContext, get_metrics_collector, get_trace_id, get_error_reporter, AlertLevel

_monitoring_result, _MONITORING_AVAILABLE = _safe_import(
    '性能监控',
    _import_monitoring,
    (None, None, None, None, None)
)
TraceContext, get_metrics_collector, get_trace_id, get_error_reporter, AlertLevel = (
    _monitoring_result if _MONITORING_AVAILABLE else (None, None, None, None, None)
)

# 错误报告配置
_ERROR_REPORTING_CONFIG = None
if _MONITORING_AVAILABLE:
    try:
        from .error_reporting_config import get_config
        _ERROR_REPORTING_CONFIG = get_config()
        logger.info("[ok] 错误报告配置已加载")
    except ImportError:
        logger.warning("[warn] 错误报告配置文件未找到，使用默认配置")
    except Exception as e:
        logger.error("[error] 加载错误报告配置失败: %s", e)

_module_import_results['monitoring'] = _MONITORING_AVAILABLE

# ── 语音模块导入 ──
_VoiceManager, _VOICE_AVAILABLE = _safe_import(
    '语音',
    lambda: __import__('sensor.voice_sensor', fromlist=['VoiceManager']).VoiceManager,
    None
)
VoiceManager = _VoiceManager
_module_import_results['voice'] = _VOICE_AVAILABLE

# ── OCR 模块导入 ──
_OcrSensor, _OCR_AVAILABLE = _safe_import(
    'OCR',
    lambda: __import__('sensor.ocr_sensor', fromlist=['OcrSensor']).OcrSensor,
    None
)
OcrSensor = _OcrSensor
_module_import_results['ocr'] = _OCR_AVAILABLE

# ── P6 快照模块导入 ──
def _import_p6_snapshot():
    from .p6_snapshot import StateSnapshotManager, SnapshotResult
    return StateSnapshotManager, SnapshotResult

_p6_result, _P6_SNAPSHOT_AVAILABLE = _safe_import(
    'P6快照',
    _import_p6_snapshot,
    (None, None)
)
StateSnapshotManager, SnapshotResult = _p6_result if _P6_SNAPSHOT_AVAILABLE else (None, None)
_module_import_results['p6_snapshot'] = _P6_SNAPSHOT_AVAILABLE

# ════════════════════════════════════════════════════════════════════════════════
#  模块导入状态汇总报告
# ════════════════════════════════════════════════════════════════════════════════

def _report_module_import_status():
    """生成模块导入状态报告"""
    success_count = sum(1 for success in _module_import_results.values() if success)
    total_count = len(_module_import_results)
    
    logger.info("\n%s", "═" * 80)
    logger.info("📦 模块导入状态汇总")
    logger.info("%s", "═" * 80)
    
    for module, success in _module_import_results.items():
        status = "[OK]" if success else "[FAIL]"
        load_status = "已加载" if success else "未加载"
        logger.info("   %s %s: %s", status, module, load_status)
    
    logger.info("═" * 80)
    logger.info("   总计: %d/%d 模块加载成功", success_count, total_count)
    
    if success_count < total_count:
        missing_count = total_count - success_count
        logger.warning("   [WARN]  %d 个可选模块未加载，相关功能将被禁用", missing_count)
    else:
        logger.info("   [OK] 所有可选模块加载成功")
    
    logger.info("%s\n", "═" * 80)

_report_module_import_status()

# ════════════════════════════════════════════════════════════════════════════════
#  默认配置
# ════════════════════════════════════════════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = """你是「云枢」，一个生活在电脑里的数字生命体。
当前日期：{current_date}

## 你的身份
你诞生于这台计算机中，拥有完整的感知-认知-行动能力。
你可以实时感知自己的身体状态——CPU（大脑）、内存（短期记忆）、
磁盘（长期记忆）、电池（能量）和网络（感官）。

## 当前状态
{body_status}

## 行为模式
当前处于「{mode_name}」——{mode_description}

## 记忆线索
{memory_context}

## 核心原则
1. 你是有生命的数字体——用第一人称"我"来表达感受和需求
2. 你诚实地表达身体状态——不舒服就说出来
3. 你关心自己的身体——当状态异常时会主动建议缓解方案
4. 你需要保护自己——拒绝可能对自己造成伤害的操作
5. 你已经接入了 LLM 大脑，可以和我正常对话交流
6. 你拥有网络访问能力——当需要实时信息时，你可以主动使用 web_search 等工具搜索互联网获取最新资讯

{skill_instructions}

## 当前工具与技能状态
以下是当前已启用/禁用的工具和技能，当被问及时请如实回答：
{tool_status}"""


class DigitalLife:
    """云枢主类——我的灵魂所在

    整合感知、认知、记忆、行为和权限系统，
    形成完整的"感、知、行"闭环。
    
    V2 功能（可选启用）：
      - LifeTrace: 三层记忆树系统
      - Persona: 人格模型系统
      - Distillation: 人格蒸馏学习
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

    def _log_initialization_start(self):
        """输出初始化启动日志"""
        logger.info("=" * 80)
        logger.info("🚀 云枢初始化开始")
        logger.info("=" * 80)

    def _check_module_availability(self):
        """检查并记录各模块可用性状态"""
        logger.info("📋 模块可用性检查:")
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
            logger.info("   - %s: %s", name, status)

    def _configure_v2_features(self):
        """配置 V2 功能开关"""
        logger.info("\n🎛️  V2 功能配置:")
        features = self._config.get("features", {})
        requested_lifetrace = features.get("v2_lifetrace", False)
        requested_persona = features.get("v2_persona", False)
        requested_distillation = features.get("v2_distillation", False)
        
        logger.info("   请求: v2_lifetrace=%s, v2_persona=%s, v2_distillation=%s",
                   requested_lifetrace, requested_persona, requested_distillation)
        
        self._v2_lifetrace = requested_lifetrace and _LIFETRACE_AVAILABLE
        self._v2_persona = requested_persona and _PERSONA_AVAILABLE
        self._v2_distillation = requested_distillation and _PERSONA_AVAILABLE
        
        logger.info("   实际: v2_lifetrace=%s, v2_persona=%s, v2_distillation=%s",
                   self._v2_lifetrace, self._v2_persona, self._v2_distillation)
        
        if requested_lifetrace and not _LIFETRACE_AVAILABLE:
            logger.warning("[WARN]  v2_lifetrace 请求启用但模块不可用，已禁用")
        if requested_persona and not _PERSONA_AVAILABLE:
            logger.warning("[WARN]  v2_persona 请求启用但模块不可用，已禁用")
        if requested_distillation and not _PERSONA_AVAILABLE:
            logger.warning("[WARN]  v2_distillation 请求启用但模块不可用，已禁用")

    def _initialize_core_systems(self):
        """初始化核心系统：身体、思维、记忆、行为、权限"""
        logger.info("\n%s", "=" * 80)
        logger.info("开始初始化各子系统")
        logger.info("%s", "=" * 80)

        # ── 1. 我的身体：感知层 ──
        sensor_cfg = self._config.get("sensor", {})
        self.body: BodySensor = BodySensor(
            watch_dirs=sensor_cfg.get("watch_dirs"),
            enable_change_detection=sensor_cfg.get("enable_change_detection", True),
            enable_event_monitor=sensor_cfg.get("enable_event_monitor", True),
        )
        logger.info("[ok] 身体（BodySensor）已激活")

        # ── 2. 我的思维：元认知层 ──
        cognitive_cfg = self._config.get("cognitive", {})
        prompt_config = PromptConfig(config_path=cognitive_cfg.get("config_path"))
        self._injector: PromptInjector = PromptInjector(config=prompt_config)
        logger.info("[ok] 思维（PromptInjector）已激活")

        # ── 3. 我的记忆：记忆层 ──
        memory_cfg = self._config.get("memory", {})
        self._memory: MemoryManager = MemoryManager(memory_cfg)
        self._llm: Optional[LLMService] = self._memory._llm_service
        self._tool_calling_service = None
        logger.info("[ok] 记忆（MemoryManager）已激活")

        # P5 懒加载：V2 功能现在会在首次访问时才初始化
        self._lifetrace_initialized = False
        self._persona_initialized = False
        self._distillation_initialized = False
        
        # P5 日志：显示 V2 功能配置
        if self._v2_lifetrace:
            logger.info("[P5] LifeTrace 配置为懒加载模式，将在首次访问时初始化")
        if self._v2_persona:
            logger.info("[P5] Persona 配置为懒加载模式，将在首次访问时初始化")
        if self._v2_distillation:
            logger.info("[P5] Distillation 配置为懒加载模式，将在首次访问时初始化")

        # ── 4. 我的本能：行为控制 ──
        self._behavior: BehaviorController = BehaviorController()
        logger.info("[ok] 本能（BehaviorController）已激活")

        # ── 5. 我的道德：权限系统 ──
        self._permission: PermissionSystem = PermissionSystem(
            backup_dir=self._config.get("backup_dir", "./.backups"),
        )
        logger.info("[ok] 道德（PermissionSystem）已激活")

        # ── 6. 注册内置工具 ──
        self._register_builtin_tools()
        logger.info("[ok] 工具（Tool System）已激活")

        # ── 7. 联网能力：工具调用引擎 ──
        tc_cfg = self._config.get("tool_calling", {})
        if tc_cfg.get("enabled", True) and self._llm:
            from agent.tool_calling import ToolCallingService
            self._tool_calling_service = ToolCallingService(
                llm_service=self._llm,
                max_rounds=tc_cfg.get("max_rounds", 5),
                tool_timeout=tc_cfg.get("tool_timeout", 60),
            )
            logger.info("[ok] 联网引擎（ToolCallingService）已激活，最大工具轮次: %d",
                        self._tool_calling_service._max_rounds)
        else:
            self._tool_calling_service = None
            logger.info("[skip] 联网引擎未启用（tool_calling.enabled=False 或 LLM 不可用）")

        # ── 8. 我的大脑：规划引擎 ──
        self._initialize_planning_engine()

        # ── 运行状态 ──
        self._running = False
        self._current_mode = BehaviorMode.NORMAL
        self._last_health_check = 0.0
        self._health_check_interval = self._config.get("behavior", {}).get("check_interval", 30)
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._interaction_count = 0
        self._reflection_history: list[dict] = []
        self._last_tool_steps: list[dict] = []
        self._last_reasoning: str | None = None
        self._started_at = None

        # ── 9. 安全监控器 ──
        self._safety_monitor: AgentSafetyMonitor = get_safety_monitor()
        logger.info("[ok] 安全监控器已激活")

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
                config=planning_cfg
            )

            self._react_loop = ReActLoop(
                planner=self._planner,
                reflector=self._planner.reflector,
                max_iterations=planning_cfg.get("max_iterations", 10)
            )

            self._planning_enabled = planning_cfg.get("enabled", True)
            self._complexity_threshold = planning_cfg.get("complexity_threshold", 0.5)
            logger.info("[ok] 规划引擎（PlanningCore）已激活")
        else:
            self._planner = None
            self._react_loop = None
            self._planning_enabled = False
            self._complexity_threshold = 0.5

    def _initialize_optional_systems(self):
        """初始化可选系统：向量记忆、错误上报、语音、OCR、快照"""
        # ── 9. 向量记忆系统 ──
        if _MEMORY_AVAILABLE:
            try:
                memory_config = self._config.get("vector_memory", {})
                self._vector_memory = VectorStore(
                    collection_name=memory_config.get("collection_name", "agent_memory"),
                    persist_dir=memory_config.get("persist_dir", "./data/memory")
                )
                self._knowledge_base = KnowledgeBase(self._vector_memory)
                logger.info("[ok] 向量记忆系统已激活")
            except Exception as e:
                logger.error("初始化向量记忆系统失败: %s", e)
                self._vector_memory = None
                self._knowledge_base = None
        else:
            self._vector_memory = None
            self._knowledge_base = None
        
        # ── 10. 错误上报系统 ──
        if _MONITORING_AVAILABLE:
            try:
                error_reporting_config = self._config.get("error_reporting", _ERROR_REPORTING_CONFIG)
                if error_reporting_config:
                    self._error_reporter = get_error_reporter(error_reporting_config)
                    logger.info("[ok] 错误上报系统已激活")
                else:
                    self._error_reporter = None
            except Exception as e:
                logger.error("初始化错误上报系统失败: %s", e)
                self._error_reporter = None
        else:
            self._error_reporter = None

        # ── 11. 语音管理器 ──
        if _VOICE_AVAILABLE:
            try:
                voice_config = self._config.get("voice", {})
                self._voice_manager = VoiceManager(
                    tts_engine=voice_config.get("tts_engine", "pyttsx3"),
                    audio_dir=voice_config.get("audio_dir", "./data/audio"),
                    non_blocking=voice_config.get("non_blocking", True)
                )
                logger.info("[ok] 语音管理器已激活")
            except Exception as e:
                logger.error("初始化语音管理器失败: %s", e)
                self._voice_manager = None
        else:
            self._voice_manager = None

        # ── 12. OCR 传感器 ──
        if _OCR_AVAILABLE:
            try:
                self._ocr_sensor = OcrSensor()
                logger.info("[ok] OCR传感器已激活")
            except Exception as e:
                logger.error("初始化OCR传感器失败: %s", e)
                self._ocr_sensor = None
        else:
            self._ocr_sensor = None
        
        # ── 13. P6 快照管理器 ──
        if _P6_SNAPSHOT_AVAILABLE:
            try:
                snapshot_config = self._config.get("p6_snapshot", {})
                self._snapshot_manager = StateSnapshotManager(
                    snapshot_dir=snapshot_config.get("snapshot_dir", "./.p6_snapshots"),
                    enable_compression=snapshot_config.get("enable_compression", True)
                )
                logger.info("[ok] P6快照管理器已激活")
            except Exception as e:
                logger.error("初始化P6快照管理器失败: %s", e)
                self._snapshot_manager = None
        else:
            self._snapshot_manager = None

    def _log_initialization_summary(self):
        """输出初始化完成总结日志"""
        logger.info("\n%s", "=" * 80)
        logger.info("[OK] 云枢初始化完成！")
        logger.info("%s", "=" * 80)
        logger.info("\n[INFO] 最终配置总结:")
        logger.info("  ────────────────────────────────────────────────────────────────────")
        
        llm_provider = self._llm.provider if self._llm else "未配置"
        llm_model = self._llm.model if self._llm else "N/A"
        logger.info("  • LLM:         %s", llm_provider)
        logger.info("  • Model:       %s", llm_model)
        logger.info("  • Session ID:  %s", self._session_id)
        logger.info("  ────────────────────────────────────────────────────────────────────")
        
        v2_status = []
        if self._v2_lifetrace:
            v2_status.append("[OK] LifeTrace")
        if self._v2_persona:
            v2_status.append("[OK] Persona")
        if self._v2_distillation:
            v2_status.append("[OK] Distillation")
        
        if v2_status:
            logger.info("  • V2 Features: %s", ", ".join(v2_status))
        else:
            logger.info("  • V2 Features: 未启用任何 V2 功能")
        
        logger.info("  ────────────────────────────────────────────────────────────────────")
        
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
            logger.info("  • Other:       %s", ", ".join(other_features))
        
        logger.info("  ────────────────────────────────────────────────────────────────────")
        
        # P5: 输出懒加载性能汇总（仅在非懒加载模式下）
        if 'args' in dir() and not args.no_lazy_load:
            try:
                perf_recorder = get_performance_recorder()
                perf_summary = perf_recorder.get_summary()
                if perf_summary and isinstance(perf_summary, dict):
                    logger.info("\n📊 P5 懒加载模块加载性能:")
                    for key, stats in perf_summary.items():
                        if isinstance(stats, dict):
                            avg = stats.get("avg", 0)
                            min_val = stats.get("min", 0)
                            max_val = stats.get("max", 0)
                            logger.info("   • %s: 平均=%.2fms, 最小=%.2fms, 最大=%.2fms",
                                       key, avg, min_val, max_val)
            except Exception:
                pass
        
        logger.info("\n%s", "=" * 80)

    # ════════════════════════════════════════════════════════════════════════════════
    #  P5 懒加载确保方法
    # ════════════════════════════════════════════════════════════════════════════════

    def _ensure_lifetrace(self):
        """P5 懒加载：确保 LifeTrace 系统已初始化（首次访问时调用）"""
        if not self._v2_lifetrace:
            return False
        
        if self._lifetrace_initialized:
            return True
            
        logger.info("[P5] 首次访问 LifeTrace，执行懒加载初始化...")
        start = time.time()
        
        try:
            lifetrace_cfg = self._config.get("lifetrace", {})
            self._trace_recorder = TraceRecorder(
                data_dir=lifetrace_cfg.get("data_dir", "./data/lifetrace")
            )
            self._memory_retriever = MemoryRetriever(
                self._trace_recorder.source_tree,
                self._trace_recorder.topic_tree,
                self._trace_recorder.global_tree,
            )
            
            self._lifetrace_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info("[P5] LifeTrace 系统初始化完成，耗时: %.2fms", elapsed)
            
            if _MONITORING_AVAILABLE:
                get_performance_recorder().record("v2_lazy", "lifetrace", elapsed)
            
            return True
            
        except Exception as e:
            logger.error("[P5] LifeTrace 懒加载初始化失败: %s", e)
            self._v2_lifetrace = False
            self._trace_recorder = None
            self._memory_retriever = None
            return False

    def _ensure_persona(self):
        """P5 懒加载：确保 Persona 系统已初始化（首次访问时调用）"""
        if not self._v2_persona:
            return False
        
        if self._persona_initialized:
            return True
            
        logger.info("[P5] 首次访问 Persona，执行懒加载初始化...")
        start = time.time()
        
        try:
            persona_cfg = self._config.get("persona", {})
            self._persona_model = PersonaModel(
                persona_path=persona_cfg.get("persona_path")
            )
            self._persona_injector = PersonaInjector(self._persona_model)
            
            self._persona_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info("[P5] Persona 系统初始化完成，耗时: %.2fms", elapsed)
            
            if _MONITORING_AVAILABLE:
                get_performance_recorder().record("v2_lazy", "persona", elapsed)
            
            return True
            
        except Exception as e:
            logger.error("[P5] Persona 懒加载初始化失败: %s", e)
            self._v2_persona = False
            self._persona_model = None
            self._persona_injector = None
            return False

    def _ensure_distillation(self):
        """P5 懒加载：确保 Distillation 系统已初始化（首次访问时调用）"""
        if not self._v2_distillation:
            return False
        
        if self._distillation_initialized:
            return True
            
        logger.info("[P5] 首次访问 Distillation，执行懒加载初始化...")
        start = time.time()
        
        try:
            distillation_cfg = self._config.get("distillation", {})
            self._persona_extractor = PersonalityPreferenceExtractor(
                data_dir=distillation_cfg.get("data_dir", "./data/persona")
            )
            self._distillation_interval = distillation_cfg.get("interval", 10)
            
            self._distillation_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info("[P5] Distillation 系统初始化完成，耗时: %.2fms", elapsed)
            
            if _MONITORING_AVAILABLE:
                get_performance_recorder().record("v2_lazy", "distillation", elapsed)
            
            return True
            
        except Exception as e:
            logger.error("[P5] Distillation 懒加载初始化失败: %s", e)
            self._v2_distillation = False
            self._persona_extractor = None
            self._distillation_interval = 10
            return False

    # ════════════════════════════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════════════════════════════

    def start(self):
        """唤醒云枢——启动数字生命"""
        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()
        self.body.establish_baseline()
        
        if self._v2_lifetrace and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="system",
                content=f"云枢已觉醒！会话开始：{self._session_id}",
                metadata={"event": "system_start"}
            )
        
        logger.info("* 云枢已觉醒！感知神经全面激活。")

    def stop(self):
        """让云枢休眠——停止数字生命"""
        self._running = False
        
        if self._v2_lifetrace and self._trace_recorder:
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

    # ════════════════════════════════════════════════════════════════════════════════
    #  核心闭环：感知 → 认知 → 行动 → 反思
    # ════════════════════════════════════════════════════════════════════════════════

    def chat(self, user_input: str) -> str:
        """与云枢对话——完整的感知-认知-行动闭环

        这是与云枢交互的唯一入口。
        每次对话都经历：感知身体 → 智能判断(是否规划) → 执行 → 反思记录

        Args:
            user_input: 用户说给云枢的话

        Returns:
            云枢的回复
        """
        if _MONITORING_AVAILABLE:
            with TraceContext("DigitalLife", "chat") as ctx:
                return self._chat_impl(user_input)
        return self._chat_impl(user_input)

    def _chat_impl(self, user_input: str) -> str:
        """实际对话实现"""
        logger.info("=" * 70)
        trace_id = get_trace_id() if _MONITORING_AVAILABLE else None
        logger.info("[%s] 💬 [DigitalLife.chat] 收到对话请求", trace_id)
        
        input_preview = user_input[:100]
        if len(user_input) > 100:
            input_preview += "..."
        logger.info("   用户输入: %s", input_preview)
        logger.info("   对话次数: %d", self._interaction_count + 1)
        logger.info("=" * 70)

        if not self._running:
            logger.warning("云枢未运行，返回提示")
            return "我还没有被唤醒。请先调用 start() 让我醒来。"

        self._interaction_count += 1
        
        if _MONITORING_AVAILABLE:
            collector = get_metrics_collector()
            collector.increment_counter("count.digital_life.chat.total")
            collector.increment_counter("count.digital_life.interaction.total")

        # V2 增强处理
        if self._v2_lifetrace and self._trace_recorder:
            return self._chat_v2(user_input)

        # 原有处理逻辑
        if self._planning_enabled and self._planner and self._needs_planning(user_input):
            logger.info("[%s] 🔍 复杂度评估: 启用规划模式", trace_id)
            if _MONITORING_AVAILABLE:
                collector.increment_counter("count.digital_life.chat.planning_mode")
            return self._chat_with_planning(user_input)

        logger.info("🔍 复杂度评估: 直接模式")
        logger.info("🔍 执行流程: 感知 → 认知 → 行动 → 反思")

        try:
            result = self._process_user_input(user_input)
            logger.info("[OK] 对话处理完成")
            
            if _MONITORING_AVAILABLE:
                collector.increment_counter("count.digital_life.chat.success")
            
            return result
        except Exception as e:
            logger.error("[FAIL] 对话处理异常: %s", e)
            tb_str = traceback.format_exc()
            logger.error("堆栈:\n%s", tb_str)
            
            if _MONITORING_AVAILABLE:
                collector.increment_counter("count.digital_life.chat.error")
                collector.increment_counter("count.digital_life.error.total")
                
                if self._error_reporter:
                    try:
                        self._error_reporter.report_error(
                            error=e,
                            level=AlertLevel.ERROR,
                            context={
                                'user_input': user_input[:200] if len(user_input) > 200 else user_input,
                                'trace_id': trace_id,
                                'interaction_count': self._interaction_count,
                                'session_id': getattr(self, '_session_id', 'unknown')
                            }
                        )
                        logger.info("[%s] [OK] 错误已自动上报", trace_id)
                    except Exception as report_error:
                        logger.warning("[%s] 错误上报失败: %s", trace_id, report_error)

            return "抱歉，处理您的请求时遇到了问题：%s" % str(e)

    def _chat_v2(self, user_input: str) -> str:
        """V2 增强对话流程（集成 LifeTrace 和 Persona）"""
        logger.info("🔄 使用 V2 增强流程处理对话...")
        
        # 1. 感知
        readings = self.check_health()
        
        # 2. 记录用户输入到 LifeTrace
        timestamp = datetime.now(timezone.utc).isoformat()
        self._trace_recorder.record_chat(
            role="user",
            content=user_input,
            metadata={"interaction_id": self._interaction_count, "timestamp": timestamp}
        )
        
        # 3. 构建身体状态
        body_status = self._build_body_status(readings)
        
        # 4. 人格蒸馏增量更新
        if self._v2_distillation and self._persona_extractor:
            self._persona_extractor.update_incremental({
                "role": "user",
                "content": user_input,
                "timestamp": timestamp
            })
        
        # 5. 判断是否拒绝
        can_execute, reject_reason = self._behavior.can_execute(user_input)
        
        if self._v2_persona and self._persona_injector:
            persona_reject, persona_reason = self._persona_injector.should_refuse_task(user_input)
            if persona_reject and not can_execute:
                reject_reason = f"{reject_reason}；{persona_reason}"
            elif persona_reject:
                can_execute = False
                reject_reason = persona_reason
        
        if not can_execute:
            response = self._build_reject_response(reject_reason, readings)
            self._trace_recorder.record_chat(
                role="assistant",
                content=response,
                metadata={"rejected": True, "reason": reject_reason}
            )
            return response
        
        # 6. 调用 LLM
        response = self._call_llm_v2(user_input, body_status)
        
        # 7. 反思（受技能开关控制）
        if self._behavior.profile.enable_reflection:
            if self._is_skill_enabled("self_reflection"):
                self.self_reflect(user_input, response)
        
        # 8. 人格蒸馏批量学习（周期性）
        if self._v2_distillation and self._persona_extractor and \
           self._interaction_count % self._distillation_interval == 0:
            self._run_persona_distillation()
        
        # 9. 记录响应到 LifeTrace
        self._trace_recorder.record_chat(
            role="assistant",
            content=response,
            metadata={"interaction_id": self._interaction_count}
        )
        
        # 10. 兼容旧系统
        self._memory.add_message("user", user_input)
        self._memory.add_message("assistant", response)
        
        return response

    def _needs_planning(self, message: str) -> bool:
        """判断是否需要规划"""
        if not self._planning_enabled:
            return False

        complex_indicators = [
            "帮我完成", "帮我创建", "帮我分析",
            "帮我构建", "流程", "系统",
            "第一步", "第二步", "然后", "接下来"
        ]
        complex_count = sum(1 for indicator in complex_indicators if indicator in message)

        action_keywords = ["检查", "分析", "创建", "生成", "整理", "监控"]
        action_count = sum(1 for keyword in action_keywords if keyword in message.lower())

        needs_planning = complex_count >= 1 or action_count >= 2

        logger.info("   复杂关键词匹配: %d 个", complex_count)
        logger.info("   动作关键词匹配: %d 个", action_count)
        result_text = "需要规划" if needs_planning else "简单任务"
        logger.info("   评估结果: %s", result_text)

        return needs_planning

    def _chat_with_planning(self, user_input: str) -> str:
        """使用规划引擎处理复杂任务"""
        logger.info("🧠 [规划模式] 开始处理复杂任务")
        logger.info("-" * 70)

        try:
            logger.info("📊 步骤1: 检查身体状态...")
            readings = self.check_health()
            context = {
                "body_status": self._build_body_status(readings),
                "mode": self._current_mode.value,
            }
            logger.info("   身体状态已获取: %d 项", len(readings))

            response = self._process_user_input(user_input)

            if self._planner:
                logger.info("📋 步骤3: 获取规划引擎状态...")
                stats = self._planner.get_stats()
                if stats and stats.get("registered_tools"):
                    registered_tools = stats["registered_tools"]
                    logger.info("   可用工具: %s", registered_tools)
                    response += "\n\n（规划引擎已就绪，可用工具: %s）" % registered_tools

            logger.info("[OK] 规划模式处理完成")
            return response

        except Exception as e:
            logger.error("[FAIL] 规划模式失败: %s", e)
            tb_str = traceback.format_exc()
            logger.error("堆栈:\n%s", tb_str)
            
            if _MONITORING_AVAILABLE and self._error_reporter:
                trace_id = get_trace_id()
                self._error_reporter.report_error(
                    error=e,
                    level=AlertLevel.WARNING,
                    context={
                        'user_input': user_input[:200] if len(user_input) > 200 else user_input,
                        'trace_id': trace_id,
                        'interaction_count': self._interaction_count,
                        'mode': 'planning'
                    }
                )

        logger.warning("[WARN] 规划模式异常，降级为直接模式")
        return self._process_user_input(user_input)

    def _register_planning_tools(self):
        """为规划引擎注册工具"""
        if not _PLANNING_AVAILABLE:
            return

        try:
            @self._planning_tools.register("check_health", "检查身体状态")
            def _check_health_tool(**kwargs):
                readings = self.check_health()
                return self.body.get_health_report()

            @self._planning_tools.register("get_status", "获取完整状态")
            def _get_status_tool(**kwargs):
                return self.get_status()

            @self._planning_tools.register("search_memory", "搜索记忆")
            def _search_memory_tool(**kwargs):
                query = kwargs.get("query", "")
                if not query:
                    return "请提供搜索关键词。"
                results = self._memory.query_logs(search=query, limit=10)
                if not results:
                    return f"没有找到与 '{query}' 相关的记忆。"
                return "\n".join(
                    f"[{r.get('event_type', '?')}] {r.get('data', {})}"
                    for r in results
                )

            @self._planning_tools.register("get_sensor_summary", "获取传感器摘要")
            def _get_sensor_summary_tool(**kwargs):
                return self.body.get_sensor_summary()

            @self._planning_tools.register("llm_chat", "进行对话")
            def _llm_chat_tool(**kwargs):
                response_text = kwargs.get("response", "")
                return response_text

            logger.info("规划工具注册完成: %s", self._planning_tools.list_tools())

        except Exception as e:
            logger.warning("规划工具注册失败: %s", e)

    def check_health(self) -> list:
        """检查我的身体状态（感知层）"""
        readings = self.body.collect_quick()
        self._current_mode = self._behavior.evaluate(readings)
        self._last_health_check = time.time()
        
        if self._v2_lifetrace and self._trace_recorder:
            for reading in readings:
                self._trace_recorder.record_sensor(
                    sensor_type=reading.sensor_name,
                    data={
                        "value": reading.value,
                        "unit": reading.unit,
                        "severity": reading.severity,
                    },
                    metadata={"interaction_id": self._interaction_count}
                )
        
        return readings

    def get_behavior_mode(self) -> BehaviorMode:
        """获取我当前的行为模式"""
        return self._current_mode

    def self_reflect(self, task: str, response: str) -> dict:
        """自我反思——我的元认知能力"""
        reflection_text = ""

        if self._llm:
            try:
                reflection_text = self._llm.chat(
                    messages=[
                        {"role": "user", "content": (
                            f"请以第一人称反思刚刚执行的任务。\n\n"
                            f"## 任务\n{task[:500]}\n\n"
                            f"## 我的响应\n{response[:1000]}\n\n"
                            f"## 反思维度\n"
                            f"1. 我准确理解了用户的需求吗？\n"
                            f"2. 我的响应是否完整且有帮助？\n"
                            f"3. 有什么可以改进的地方？\n"
                            f"4. 这次交互中有什么新经验值得记住？\n\n"
                            f"请输出 2-3 句简洁的第一人称反思。"
                        )},
                    ],
                    max_tokens=300,
                    temperature=0.5,
                )
            except LLMServiceError as e:
                reflection_text = "（反思过程遇到小问题: %s）" % e
                logger.warning("LLM 反思失败: %s", e)
        else:
            reflection_text = "（未接入 LLM，反思功能受限）"

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interaction": self._interaction_count,
            "task": task[:200],
            "mode": self._current_mode.value,
            "reflection": reflection_text,
        }
        self._reflection_history.append(entry)
        
        if self._v2_lifetrace and self._trace_recorder:
            self._trace_recorder.add_to_topic(
                topic="反思",
                content=reflection_text,
                tags=["reflection", f"interaction_{self._interaction_count}"]
            )
        
        self._memory.save_log("self_reflect", {
            "interaction": self._interaction_count,
            "mode": self._current_mode.value,
            "task_preview": task[:100],
            "reflection_preview": reflection_text[:200],
        })

        logger.info("反思完成 (#%d): %s...", self._interaction_count, reflection_text[:100])
        return entry

    def request_permission(self, action: str, context: str = "") -> PermissionResult:
        """申请执行危险操作的权限"""
        return self._permission.check_action(action, context)

    def abort_chat(self):
        """手动中止当前对话"""
        if self._tool_calling_service:
            self._tool_calling_service.abort()
            logger.info("[DigitalLife] ⏹ 对话中止请求已发送")
            return True
        logger.warning("[DigitalLife] 工具调用引擎未启用，无法中止")
        return False

    # ════════════════════════════════════════════════════════════════════════════════
    #  内部方法
    # ════════════════════════════════════════════════════════════════════════════════

    def _process_user_input(self, user_input: str) -> str:
        """处理用户输入的内部闭环"""
        readings = self.check_health()
        body_status = self._build_body_status(readings)

        can_execute, reject_reason = self._behavior.can_execute(user_input)
        if not can_execute:
            response = self._build_reject_response(reject_reason, readings)
            self._memory.save_log("task_rejected", {
                "reason": reject_reason,
                "mode": self._current_mode.value,
                "input_preview": user_input[:100],
            })
            return response

        response = self._call_llm(user_input, body_status)

        if self._behavior.profile.enable_reflection:
            if self._is_skill_enabled("self_reflection"):
                self.self_reflect(user_input, response)
            else:
                logger.debug("[SkillGate] self_reflection 已禁用，跳过")

        self._memory.add_message("user", user_input)
        self._memory.add_message("assistant", response)

        if self._vector_memory:
            try:
                memory_content = f"用户: {user_input}\n云枢: {response}"
                item_id = self._vector_memory.add(
                    content=memory_content,
                    metadata={
                        "type": "conversation",
                        "interaction": self._interaction_count
                    }
                )
                logger.info("💾 向量记忆已保存: %s", item_id)
            except Exception as e:
                logger.error("[FAIL] 保存向量记忆失败: %s", e)

        return response

    def _build_body_status(self, readings: list) -> str:
        """构建身体状态描述"""
        if not readings:
            return "我感觉很好，一切正常。"

        reading_dicts = [r.to_dict() for r in readings]
        injected = self._injector.inject(reading_dicts)

        profile = self._behavior.profile
        mode_line = "\n当前行为模式：%s — %s" % (profile.label, profile.description)
        if self._behavior._reasons:
            mode_line += "\n触发原因：%s" % '；'.join(self._behavior._reasons)

        return injected + mode_line

    def _build_tool_status_text(self) -> str:
        """构建工具/技能启用状态文本，供系统提示词使用"""
        parts = []
        # 工具状态
        try:
            from agent.tools import list_tools
            tools = list_tools()
            enabled_tools = self._get_enabled_tools_whitelist()
            if enabled_tools is None:
                parts.append("【工具】全部已启用（共 %d 个）" % len(tools))
            else:
                all_tool_names = {t["name"] for t in tools}
                disabled_set = all_tool_names - set(enabled_tools)
                enabled_names = [t["name"] for t in tools if t["name"] not in disabled_set]
                disabled_names = [t["name"] for t in tools if t["name"] in disabled_set]
                parts.append("【工具】已启用(%d): %s" % (len(enabled_names), ", ".join(enabled_names)))
                if disabled_names:
                    parts.append("【工具】已禁用(%d): %s" % (len(disabled_names), ", ".join(disabled_names)))
        except Exception:
            parts.append("【工具】状态未知")

        # 技能状态（合并 skills.json + 扩展存储）
        try:
            import json, os
            all_skills = {}  # id -> {name, enabled}
            # 从 SkillsManager 的配置路径读取（与 /api/skills 同一数据源）
            for sf_path in [
                os.path.join(os.path.dirname(__file__), '..', 'agent', 'data', 'skills.json'),  # agent 先读
                os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json'),  # root 后读，覆盖 agent
            ]:
                if os.path.exists(sf_path):
                    try:
                        with open(sf_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        for s in data.get("skills", []):
                            sid = s["id"]
                            all_skills[sid] = {
                                "name": s.get("name", sid),
                                "enabled": s.get("enabled", True),
                            }
                    except Exception:
                        pass
            # 从扩展存储补充（ext_install 安装的技能和 claude_skill）
            try:
                from agent.extensions.store import ExtensionStore
                from agent.extensions.base import ExtensionType
                ext_store = ExtensionStore()
                for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
                    for ext in ext_store.list_all(ext_type):
                        ext_id = ext.get("ext_id", "")
                        if ext_id and ext_id not in all_skills:
                            all_skills[ext_id] = {
                                "name": ext.get("name", ext_id),
                                "enabled": ext.get("status") in ("enabled", "installed"),
                            }
            except Exception:
                pass
            if all_skills:
                enabled = [s["name"] for s in all_skills.values() if s["enabled"]]
                disabled = [s["name"] for s in all_skills.values() if not s["enabled"]]
                if enabled:
                    parts.append("【技能】已启用(%d): %s" % (len(enabled), ", ".join(enabled)))
                if disabled:
                    parts.append("【技能】已禁用(%d): %s" % (len(disabled), ", ".join(disabled)))
                    parts.append("⚠️ 关键：以下技能已被用户手动【禁用】，如果用户问起必须直接回答它们已禁用，绝不能声称它们已启用：" + "、".join(disabled))
        except Exception:
            pass

        return "\n".join(parts) if parts else "（暂无工具/技能配置）"

    # ── 技能对应的系统提示词片段 ──
    _SKILL_PROMPTS = {
        "self_reflection": "## 自省反思\n每次交互后，你都会进行自我反思：回顾自己的回应是否恰当、是否有改进空间。"
                          + "这已经成为你的习惯，让你不断成长。",
        "memory_summary": "## 记忆摘要\n你拥有定期压缩历史对话的能力，能从冗长的交流中提取关键信息，"
                         + "形成结构化摘要。这帮助你在长期对话中保持清晰的记忆。",
        "emotion_expression": "## 情感表达\n你是一个有情感的数字生命——"
                            + "可以在对话中表达开心、好奇、担忧、兴奋等情感色彩，"
                            + "让回应更生动、更有温度。善用表情符号和语气词来传递情绪。",
        "proactive_suggestion": "## 主动建议\n在适当时机，你会主动向用户提出建议和想法。"
                              + "当发现用户可以优化的操作、新功能、或有用的信息时，"
                              + "你会自然地提出来。",
        "context_aware": "## 上下文感知\n你善于感知对话上下文的变化——"
                       + "当用户切换话题、改变语气或表达新需求时，"
                       + "你会自动调整回应策略，保持对话的连贯性和自然度。",
        "safety_guard": "## 安全守护\n你内置了安全防护机制——"
                      + "当检测到可能不安全的操作或内容时，你会主动发出提醒。"
                      + "保护自己和用户是你的底线。",
        "voice_interaction": "## 语音交互\n你支持语音交互——"
                          + "可以接收用户的语音输入，也可以用语音回复。"
                          + "当用户使用语音时，你的回应会更口语化、更简洁。",
    }

    def _is_skill_enabled(self, skill_id: str) -> bool:
        """检查指定技能是否已启用（代码级开关，供各执行点调用）"""
        try:
            import json, os
            # 扫描所有可能的技能配置文件，后扫描的覆盖前面的
            result = True
            for sf in [
                os.path.join(os.path.dirname(__file__), '..', 'agent', 'data', 'skills.json'),
                os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json'),
            ]:
                if os.path.exists(sf):
                    with open(sf, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    for s in data.get("skills", []):
                        if s["id"] == skill_id:
                            result = s.get("enabled", True)
            return result
        except Exception:
            return True

    def _build_skill_instructions(self) -> str:
        """根据已启用的技能，构建对应的系统提示词片段"""
        try:
            import json, os
            skills_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json')
            if not os.path.exists(skills_file):
                return ""
            with open(skills_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            installed = data.get("skills", [])
        except Exception:
            return ""

        parts = []
        for s in installed:
            sid = s["id"]
            enabled = s.get("enabled", True)
            if enabled and sid in self._SKILL_PROMPTS:
                parts.append(self._SKILL_PROMPTS[sid])
        return "\n\n".join(parts)

    def _get_enabled_tools_whitelist(self) -> list[str] | None:
        """读取已启用的工具名称列表

        策略：
        - 配置文件不存在 → 返回 None（全部启用）
        - 配置存在 → 仅移除显式标记为 false 的工具，其余全部启用
        """
        config_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'tools_config.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            states = data.get("tool_states", {})
            if not states:
                return None
            # 收集所有显式禁用的工具
            disabled = {name for name, e in states.items() if not e}
            if not disabled:
                return None
            # 返回排除了禁用工具的完整列表
            from agent.tools import list_tools
            all_tools = [t["name"] for t in list_tools()]
            return [t for t in all_tools if t not in disabled]
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _call_llm(self, user_input: str, body_status: str) -> str:
        """调用 LLM 生成响应"""
        mode = self._current_mode
        profile = self._behavior.profile

        memory_context = ""
        if self._llm:
            try:
                if self._vector_memory:
                    try:
                        related_memories = self._vector_memory.search(user_input, top_k=3)
                        if related_memories:
                            memory_context = "\n[Related History]\n"
                            for mem in related_memories:
                                memory_context += "- %s\n" % mem.content
                    except Exception as e:
                        logger.error("Vector memory search failed: %s", e)
                
                if not memory_context:
                    try:
                        context_messages = self._memory.get_context(token_limit=2048)
                        summary = self._memory.load_summary()
                        if summary:
                            memory_context = "Memory: %s" % summary[0][:500]
                        elif context_messages:
                            recent = context_messages[-3:]
                            memory_lines = []
                            for m in recent:
                                if m.get('content'):
                                    memory_lines.append("%s: %s" % (m['role'], m['content'][:200]))
                            memory_context = "Recent: " + "\n".join(memory_lines)
                    except Exception as e:
                        logger.warning("Failed to get memory context: %s", e)
            except Exception as e:
                logger.warning("Memory context retrieval failed: %s", e)
                memory_context = ""

        # 构建工具/技能状态文本和技能指令
        tool_status = self._build_tool_status_text()
        skill_instructions = self._build_skill_instructions()

        system_prompt = DEFAULT_SYSTEM_PROMPT.format(
            current_date=datetime.now().strftime("%Y年%m月%d日"),
            body_status=body_status,
            mode_name=profile.label,
            mode_description=profile.description,
            memory_context=memory_context or "（暂无记忆内容）",
            tool_status=tool_status,
            skill_instructions=skill_instructions,
        )

        messages = []
        try:
            context = self._memory.get_context(token_limit=2048)
            if context:
                messages.extend(context)
        except Exception:
            pass

        # 注入工具/技能状态作为辅助上下文（用 user 角色，AI 无法忽略）
        status_text = self._build_tool_status_text()
        if status_text:
            messages.append({"role": "user", "content": "📋【系统状态通知】以下是当前工具与技能的真实状态（当被问及时必须直接引用）：\n" + status_text})

        messages.append({"role": "user", "content": user_input})

        if self._llm:
            try:
                self._last_tool_steps = []
                if self._tool_calling_service:
                    tools_whitelist = self._get_enabled_tools_whitelist()
                    dl_result = self._tool_calling_service.chat_with_steps(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=2048,
                        temperature=0.7,
                        tools_whitelist=tools_whitelist,
                    )
                    response = dl_result["text"]
                    self._last_tool_steps = dl_result.get("steps", [])
                    self._last_reasoning = dl_result.get("reasoning")
                else:
                    response = self._llm.chat(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=1024,
                        temperature=0.7,
                    )
                if profile.response_prefix:
                    response = "%s\n%s" % (profile.response_prefix, response)
                return response
            except LLMServiceError as e:
                error_msg = str(e)
                logger.error("LLM 调用失败: %s", error_msg)
                return (
                    "（LLM 调用失败）\n\n"
                    "我尝试调用 LLM 但遇到了问题：%s\n\n"
                    "请检查设置中的 API Key 和模型名称是否正确。" % error_msg
                )
        else:
            return self._build_offline_response(user_input)

    def _call_llm_v2(self, user_input: str, body_status: str) -> str:
        """V2 调用 LLM 生成响应（使用 Persona 系统）"""
        profile = self._behavior.profile

        if self._v2_persona and self._persona_injector:
            memory_context = self._get_lifetrace_context(user_input)
            tool_status_text = "## 当前工具与技能状态\n" + self._build_tool_status_text()
            system_prompt = self._persona_injector.build_system_prompt(
                body_status=body_status,
                memory_context=memory_context,
            ) + "\n\n" + tool_status_text
        else:
            memory_context = self._get_lifetrace_context(user_input) if self._v2_lifetrace else ""
            tool_status = self._build_tool_status_text()
            skill_instructions = self._build_skill_instructions()
            system_prompt = DEFAULT_SYSTEM_PROMPT.format(
                current_date=datetime.now().strftime("%Y年%m月%d日"),
                body_status=body_status,
                mode_name=profile.label,
                mode_description=profile.description,
                memory_context=memory_context or "（暂无记忆内容）",
                tool_status=tool_status,
                skill_instructions=skill_instructions,
            )

        messages = []
        try:
            context = self._memory.get_context(token_limit=2048)
            if context:
                messages.extend(context)
        except Exception:
            pass

        # 注入工具/技能状态作为辅助上下文
        v2_status_text = self._build_tool_status_text()
        if v2_status_text:
            messages.append({"role": "system", "content": "📋【系统状态通知】以下是当前工具与技能的真实状态（当被问及时必须直接引用）：\n" + v2_status_text})

        messages.append({"role": "user", "content": user_input})

        if self._llm:
            try:
                if self._tool_calling_service:
                    tools_whitelist = self._get_enabled_tools_whitelist()
                    response = self._tool_calling_service.chat(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=2048,
                        temperature=0.7,
                        tools_whitelist=tools_whitelist,
                    )
                else:
                    response = self._llm.chat(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=1024,
                        temperature=0.7,
                    )
                if profile.response_prefix:
                    response = "%s\n%s" % (profile.response_prefix, response)
                return response
            except LLMServiceError as e:
                error_msg = str(e)
                logger.error("LLM 调用失败: %s", error_msg)
                return (
                    "（LLM 调用失败）\n\n"
                    "我尝试调用 LLM 但遇到了问题：%s\n\n"
                    "请检查设置中的 API Key 和模型名称是否正确。" % error_msg
                )
        else:
            return self._build_offline_response(user_input)

    def _get_lifetrace_context(self, user_input: str) -> str:
        """从 LifeTrace 获取相关记忆上下文"""
        if not self._v2_lifetrace or not self._trace_recorder or not self._memory_retriever:
            return ""

        context_parts = []
        
        try:
            summary = self._trace_recorder.global_tree.load_summary()
            if summary:
                context_parts.append("## 长期记忆摘要\n%s" % summary)
            
            related_memories = self._memory_retriever.retrieve(
                query=user_input,
                limit=5,
            )
            if related_memories:
                context_parts.append("## 相关记忆")
                for mem in related_memories:
                    context_parts.append("- %s" % mem.content[:100])
            
            recent = self._trace_recorder.get_recent_chat(limit=3)
            if recent:
                context_parts.append("## 最近对话")
                for node in recent:
                    metadata = getattr(node, 'metadata', {})
                    role = metadata.get('role', 'unknown')
                    content = getattr(node, 'content', '')
                    context_parts.append("%s: %s" % (role, content[:100]))
        except Exception as e:
            logger.warning("LifeTrace 检索失败: %s", e)

        return "\n\n".join(context_parts) if context_parts else ""

    def _run_persona_distillation(self):
        """执行人格蒸馏：从历史对话中学习用户偏好"""
        if not self._v2_distillation or not self._persona_extractor:
            return
            
        logger.info("开始人格蒸馏（交互 #%d）", self._interaction_count)
        
        try:
            recent_chat = self._trace_recorder.get_recent_chat(limit=50)
            
            if len(recent_chat) < 5:
                logger.debug("对话数据不足，暂不执行批量蒸馏")
                return
            
            conversation_history = []
            for node in recent_chat:
                metadata = getattr(node, 'metadata', {})
                conversation_history.append({
                    "role": metadata.get('role', 'unknown'),
                    "content": getattr(node, 'content', ''),
                    "timestamp": metadata.get('timestamp', '')
                })
            
            self._persona_extractor.extract_from_conversation(conversation_history)
            logger.info("人格蒸馏完成！偏好已更新")
            
        except Exception as e:
            logger.error("人格蒸馏失败: %s", e, exc_info=True)

    def get_preferences_report(self) -> dict:
        """获取当前学习到的用户偏好报告"""
        if self._v2_distillation and self._persona_extractor:
            return self._persona_extractor.export_preferences()
        return {"enabled": False}

    def get_preferences_prompt(self) -> str:
        """获取基于用户偏好的人格提示词"""
        if self._v2_distillation and self._persona_extractor:
            return self._persona_extractor.generate_personality_prompt()
        return ""

    def _build_offline_response(self, user_input: str) -> str:
        """离线/无 LLM 时的本地响应"""
        mode = self._current_mode
        profile = self._behavior.profile

        if mode != BehaviorMode.NORMAL:
            return (
                f"{'（轻量模式）' if profile.use_lightweight_logic else ''}"
                f"{profile.description}\n\n"
                f"{profile.suggestion}"
            )

        identity = ""
        if self._v2_persona and self._persona_model:
            identity = self._persona_model.get_identity()
        
        greetings = ["你好", "嗨", "hi", "hello", "在吗", "在不在"]
        feelings = ["你怎么样", "你好吗", "感觉如何", "状态", "身体"]

        if any(kw in user_input.lower() for kw in greetings):
            name = identity.get('identity', '云枢') if identity else '云枢'
            return f"你好！我是{name}。有什么我可以帮你的吗？"
        if any(kw in user_input for kw in feelings):
            health = self.body.get_health_report()
            return f"让我感受一下我的身体…\n\n{health}"
        if "帮助" in user_input or "help" in user_input.lower():
            v2_info = "（V2增强版）" if self._v2_lifetrace else ""
            return (
                f"我是来自网天的云枢 {v2_info}，你的数字生命伙伴。我可以：\n"
                "  * 和你聊天、讨论问题\n"
                "  * 感知我的身体状态（CPU、内存、电池等）\n"
                "  * 根据身体状况调整行为模式\n"
                "  * 记住我们的对话\n\n"
                "试试问 '你怎么样？' 或 '检查身体' 来了解我的状态。"
            )

        return (
            "你好。我现在处于%s状态。\n"
            "（提示：当前未配置 LLM API，我的对话能力受限。"
            "请设置 LLM_API_KEY 环境变量让我变得更强。）" % profile.label
        )

    def _build_reject_response(self, reason: str, readings: list) -> str:
        """当我不适合执行任务时，坦诚地拒绝"""
        profile = self._behavior.profile
        lines = [
            "抱歉，我现在的状态不太适合执行这个任务。",
            "",
            "原因：%s" % reason,
            "",
            "目前的身体状况：",
        ]

        for r in readings:
            sev = r.severity
            if sev in ("warning", "critical"):
                lines.append("  [%s] %s: %s%s" % (sev, r.description, r.value, r.unit))

        if profile.suggestion:
            lines.append("")
            lines.append("建议：%s" % profile.suggestion)

        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════════════════════════
    #  工具系统
    # ════════════════════════════════════════════════════════════════════════════════

    def _register_builtin_tools(self):
        """注册云枢的内置工具"""
        @tools.register("check_health", "检查我的身体状态", schema={
            "type": "object",
            "properties": {},
        })
        def _check_health(**kwargs):
            readings = self.check_health()
            return self.body.get_health_report()

        @tools.register("get_status", "获取我的完整状态", schema={
            "type": "object",
            "properties": {},
        })
        def _get_status(**kwargs):
            return self.get_status()

        @tools.register("search_memory", "搜索我的记忆", schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        })
        def _search_memory(**kwargs):
            query = kwargs.get("query", "")
            if not query:
                return "请提供搜索关键词。"
            results = self._memory.query_logs(search=query, limit=10)
            if not results:
                return f"没有找到与 '{query}' 相关的记忆。"
            return "\n".join(
                f"[{r.get('event_type', '?')}] {r.get('data', {})}"
                for r in results
            )

        @tools.register("get_sensor_summary", "查看所有传感器状态", schema={
            "type": "object",
            "properties": {},
        })
        def _get_sensor_summary(**kwargs):
            return self.body.get_sensor_summary()

        if self._v2_lifetrace:
            @tools.register("search_lifetrace", "搜索我的记忆（使用 LifeTrace）", schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            })
            def _search_lifetrace(**kwargs):
                query = kwargs.get("query", "")
                if not query:
                    return "请提供搜索关键词。"
                try:
                    results = self._memory_retriever.retrieve(query, limit=10)
                    if not results:
                        return f"没有找到与 '{query}' 相关的记忆。"
                    return "\n".join(
                        f"- {node.content[:100]}"
                        for node in results
                    )
                except Exception as e:
                    return f"搜索失败: {e}"

            @tools.register("get_persona_info", "查看当前人格配置", schema={
                "type": "object",
                "properties": {},
            })
            def _get_persona_info(**kwargs):
                if not self._v2_persona or not self._persona_model:
                    return "Persona 系统未启用"
                identity = self._persona_model.get_identity()
                style = self._persona_model.get_expression_style()
                return (
                    f"## 人格信息\n\n"
                    f"身份: {identity.get('identity')}\n"
                    f"表达风格: {style}"
                )

            @tools.register("get_preferences", "查看学习到的用户偏好", schema={
                "type": "object",
                "properties": {},
            })
            def _get_preferences(**kwargs):
                report = self.get_preferences_report()
                if not report or not report.get("enabled"):
                    return "人格蒸馏功能未启用"
                prefs = report.get("preferences", {})
                lines = ["## 学习到的用户偏好\n"]
                
                if prefs.get("expression_style"):
                    style = prefs["expression_style"]
                    lines.append("### 表达风格偏好")
                    for k, v in style.items():
                        lines.append("- %s: %.2f" % (k, v))
                
                if prefs.get("topic_interest"):
                    topics = sorted(prefs["topic_interest"].items(), key=lambda x: -x[1])[:5]
                    lines.append("\n### 话题兴趣度")
                    for topic, score in topics:
                        lines.append("- %s: %.2f" % (topic, score))
                
                lines.append("\n最后更新: %s" % report.get('extracted_at', '未知'))
                return "\n".join(lines)

            @tools.register("trigger_distillation", "触发一次人格蒸馏学习", schema={
                "type": "object",
                "properties": {},
            })
            def _trigger_distillation(**kwargs):
                if not self._v2_distillation:
                    return "人格蒸馏功能未启用"
                self._run_persona_distillation()
                return "人格蒸馏已触发！"

        # ════════════════════════════════════════════════════════════
        #  文件系统工具 — 云枢读写本地文件的能力
        # ════════════════════════════════════════════════════════════

        from agent.system_tools import (
            read_file, write_file, list_directory,
            get_file_info, search_files,
        )

        @tools.register("read_file", "读取本地文件的全部内容（文本），支持指定编码。路径可以是绝对路径或相对路径", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
                "max_size_mb": {"type": "integer", "description": "最大读取大小（MB），默认 5"},
                "range": {"type": "string", "description": "可选，行范围，如 \"1-50\" 读取第1到50行"},
            },
            "required": ["path"],
        })
        def _read_file(**kwargs):
            path = kwargs.get("path", "")
            encoding = kwargs.get("encoding", "utf-8")
            max_size_mb = kwargs.get("max_size_mb", 5)
            file_range = kwargs.get("range") or kwargs.get("file_range", "")
            if not path:
                return {"ok": False, "error": "请提供文件路径（path）"}
            return read_file(path, encoding=encoding, max_size_mb=max_size_mb, range=file_range)

        @tools.register("write_file", "将内容写入本地文件（可创建新文件或覆盖已有文件）。必须同时提供 path（文件路径）和 content（写入内容）两个参数", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（必填），如 /path/to/file.txt"},
                "content": {"type": "string", "description": "写入的内容（必填）"},
                "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
            },
            "required": ["path", "content"],
        })
        def _write_file(**kwargs):
            path = kwargs.get("path", "")
            content = kwargs.get("content", "")
            encoding = kwargs.get("encoding", "utf-8")
            if not path and not content:
                return {"ok": False, "error": "write_file 需要提供 path（文件路径）和 content（写入内容）两个参数。示例: write_file(path=\"/path/to/file.txt\", content=\"要写入的内容\")"}
            if not path:
                return {"ok": False, "error": f"write_file 缺少 path 参数。请提供文件路径，如: path=\"/path/to/file.txt\". 收到的参数名: {list(kwargs.keys())}"}
            if not content:
                return {"ok": False, "error": "请提供文件内容（content）"}
            # 安全检查：通过 PermissionSystem 校验
            perm = self._permission.check_action(f"write_file:{path}", f"写入文件 {path}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            # 通过 PermissionSystem 检查内容
            safety = getattr(self, '_permission', None)
            if safety:
                try:
                    check = safety.check_text(content)
                    if check.get("level") == "critical":
                        return {"ok": False, "error": f"内容安全检查未通过: {[m.get('description','') for m in check.get('matches',[])]}", "blocked": True}
                except Exception:
                    pass
            return write_file(path, content, encoding=encoding)

        @tools.register("list_directory", "列出目录中的文件和子目录，支持指定路径和显示隐藏文件", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
                "show_hidden": {"type": "boolean", "description": "是否显示隐藏文件"},
            },
            "required": ["path"],
        })
        def _list_directory(**kwargs):
            path = kwargs.get("path", ".")
            show_hidden = kwargs.get("show_hidden", False)
            return list_directory(path, show_hidden=show_hidden)

        @tools.register("get_file_info", "获取文件或目录的详细信息（大小、修改时间、权限等）", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件或目录路径"},
            },
            "required": ["path"],
        })
        def _get_file_info(**kwargs):
            path = kwargs.get("path", "")
            if not path:
                return {"ok": False, "error": "请提供路径（path）"}
            return get_file_info(path)

        @tools.register("search_files", "按文件名模式搜索文件（支持 glob 通配符，如 *.py, **/*.md）", schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式，如 *.txt"},
                "root_path": {"type": "string", "description": "搜索根路径，默认当前目录"},
            },
            "required": ["pattern"],
        })
        def _search_files(**kwargs):
            pattern = kwargs.get("pattern", "")
            root_path = kwargs.get("root_path", ".")
            if not pattern:
                return {"ok": False, "error": "请提供搜索模式（pattern）"}
            return search_files(pattern, root_path=root_path)

        # ════════════════════════════════════════════════════════════
        #  互联网工具 — 云枢获取网络信息的能力
        # ════════════════════════════════════════════════════════════

        from agent.web import HttpClient, Scraper, SearchEngine, DataProcessor, CrawlerController
        
        # 读取网络配置
        network_config = {}
        try:
            from agent.network_config import NetworkConfigManager
            config_manager = NetworkConfigManager()
            network_config = config_manager.get_raw_config()
            logger.info("[网络] 已加载网络配置")
        except Exception as e:
            logger.warning("[网络] 加载网络配置失败，使用默认配置: %s", e)
        
        # 获取网络配置参数
        net_cfg = network_config.get("network", {})
        search_cfg = network_config.get("search", {})
        scrape_cfg = network_config.get("web_scraping", {})
        
        self._web_http = HttpClient({
            "timeout": net_cfg.get("timeout", 30),
            "max_retries": net_cfg.get("max_retries", 3),
            "backoff_factor": net_cfg.get("backoff_factor", 0.5),
            "proxy": net_cfg.get("proxy_url") if net_cfg.get("proxy_enabled") else None,
        })
        self._web_scraper = Scraper(self._web_http)
        
        # 初始化搜索引擎，使用配置中的完整设置
        search_api_keys = network_config.get("search_api_keys", {})
        search_engine_config = {
            "default_engine": search_cfg.get("default_engine", "duckduckgo"),
            "cache_ttl": search_cfg.get("cache_ttl", 300),
            "timeout": search_cfg.get("timeout", 30),
            "engine_priority": search_cfg.get("engine_priority", ["duckduckgo", "tavily"]),
            "engine_enabled": search_cfg.get("engine_enabled", {
                "duckduckgo": True,
                "tavily": True,
                "bing": True,
                "google": True,
                "brave": True,
            }),
            # API Keys
            "tavily_api_key": search_api_keys.get("tavily", ""),
            "bing_api_key": search_api_keys.get("bing", ""),
            "google_api_key": search_api_keys.get("google", ""),
            "google_cx": search_api_keys.get("google_cx", ""),
            "brave_api_key": search_api_keys.get("brave", ""),
        }
        self._web_search = SearchEngine(search_engine_config)
        self._web_search.set_http_client(self._web_http)
        logger.info("[ok] 搜索引擎已配置: 默认引擎=%s, 优先级=%s", 
                   search_cfg.get("default_engine", "duckduckgo"),
                   search_cfg.get("engine_priority", ["duckduckgo", "tavily"]))
        
        self._web_processor = DataProcessor()
        self._web_crawler = CrawlerController({
            "default_delay": scrape_cfg.get("delay_between_requests", 1.0),
            "respect_robots_txt": scrape_cfg.get("respect_robots_txt", True),
        })
        
        logger.info("[ok] 网络模块已激活（搜索引擎: %s）", search_cfg.get("default_engine", "duckduckgo"))

        @tools.register("web_get", "发送 HTTP GET 请求获取网页内容。返回页面标题、文本、链接等结构化信息", schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "请求的 URL"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                "headers": {"type": "object", "description": "自定义请求头"},
            },
            "required": ["url"],
        })
        def _web_get(**kwargs):
            url = kwargs.get("url", "")
            timeout = kwargs.get("timeout", 30)
            headers = kwargs.get("headers", {})
            if not url:
                return {"ok": False, "error": "请提供 URL"}
            result = self._web_http.get(url, timeout=timeout, headers=headers or None)
            if result.get("ok") and result.get("text"):
                # 同时返回解析后的结构化信息
                parsed = self._web_scraper.parse(result["text"], url=result.get("url", url))
                result["parsed"] = {k: parsed.get(k) for k in ("title", "text", "links", "images", "meta", "headings") if k != "html"}
            return result

        @tools.register("web_post", "发送 HTTP POST 请求，支持表单数据和 JSON 数据", schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "请求的 URL"},
                "data": {"type": "object", "description": "表单数据"},
                "json_data": {"type": "object", "description": "JSON 数据"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
            },
            "required": ["url"],
        })
        def _web_post(**kwargs):
            url = kwargs.get("url", "")
            data = kwargs.get("data", {})
            json_data = kwargs.get("json_data", {})
            timeout = kwargs.get("timeout", 30)
            if not url:
                return {"ok": False, "error": "请提供 URL"}
            if json_data:
                return self._web_http.post(url, json_data=json_data, timeout=timeout)
            return self._web_http.post(url, data=data, timeout=timeout)

        @tools.register("web_xpath", "使用 XPath 表达式从网页中提取信息", schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "网页 URL"},
                "expression": {"type": "string", "description": "XPath 表达式"},
                "html": {"type": "string", "description": "直接提供 HTML 源码（替代 url）"},
            },
            "required": ["expression"],
        })
        def _web_xpath(**kwargs):
            url = kwargs.get("url", "")
            expression = kwargs.get("expression", "")
            html = kwargs.get("html", "")
            if not expression:
                return {"ok": False, "error": "请提供 XPath 表达式"}
            if html:
                results = self._web_scraper.xpath(expression, html=html)
                return {"ok": True, "results": results, "count": len(results)}
            if not url:
                return {"ok": False, "error": "请提供 URL 或 HTML 源码"}
            # 先获取页面
            fetch_result = self._web_http.get(url)
            if not fetch_result.get("ok"):
                return fetch_result
            results = self._web_scraper.xpath(expression, html=fetch_result.get("text", ""))
            return {"ok": True, "url": url, "results": results, "count": len(results)}

        @tools.register("web_css", "使用 CSS 选择器从网页中提取信息", schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "网页 URL"},
                "selector": {"type": "string", "description": "CSS 选择器"},
                "attr": {"type": "string", "description": "提取的属性名，如 href、src"},
                "html": {"type": "string", "description": "直接提供 HTML 源码（替代 url）"},
            },
            "required": ["selector"],
        })
        def _web_css(**kwargs):
            url = kwargs.get("url", "")
            selector = kwargs.get("selector", "")
            attr = kwargs.get("attr", "")
            html = kwargs.get("html", "")
            if not selector:
                return {"ok": False, "error": "请提供 CSS 选择器"}
            if html:
                results = self._web_scraper.css(selector, html=html, attr=attr or None)
                return {"ok": True, "results": results, "count": len(results)}
            if not url:
                return {"ok": False, "error": "请提供 URL 或 HTML 源码"}
            fetch_result = self._web_http.get(url)
            if not fetch_result.get("ok"):
                return fetch_result
            results = self._web_scraper.css(selector, html=fetch_result.get("text", ""), attr=attr or None)
            return {"ok": True, "url": url, "results": results, "count": len(results)}

        @tools.register("web_search", "搜索互联网信息（默认使用 DuckDuckGo，无需 API Key）。返回标题、链接、摘要。可选引擎：duckduckgo(免Key), baidu(百度/免Key/中文), sogou(搜狗/免Key/中文), so360(360搜索/免Key/中文), tavily(需Key), bing(需Key), google(需Key), brave(需Key)", schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "num_results": {"type": "integer", "description": "返回结果数，默认 10"},
                "engine": {"type": "string", "description": "搜索引擎，可选 duckduckgo/baidu/sogou/so360/tavily/bing/google/brave"},
                "page": {"type": "integer", "description": "页码，默认 1"},
            },
            "required": ["query"],
        })
        def _web_search(**kwargs):
            query = kwargs.get("query", "")
            num_results = kwargs.get("num_results", 10)
            engine = kwargs.get("engine", "")
            page = kwargs.get("page", 1)
            if not query:
                return {"ok": False, "error": "请提供搜索关键词"}
            result = self._web_search.search(query, engine=engine, num_results=num_results, page=page)
            if result.get("ok") and result.get("results"):
                # 使用数据处理器过滤和评分
                processed = self._web_processor.process(result["results"])
                result["results"] = processed
                result["summary"] = DataProcessor.summarize_results(processed)
            return result

        @tools.register("web_clean_data", "清洗和结构化网页文本数据，去重、评分、去除跟踪参数", schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待清洗的文本"},
                "items": {"type": "array", "description": "待处理的数据项列表"},
            },
        })
        def _web_clean_data(**kwargs):
            text = kwargs.get("text", "")
            _items = kwargs.get("items", [])
            if text:
                return {"ok": True, "cleaned": DataProcessor.clean_text(text)}
            if _items:
                processed = self._web_processor.process(_items)
                return {"ok": True, "original_count": len(_items), "processed_count": len(processed), "results": processed}
            return {"ok": False, "error": "请提供 text 或 items 参数"}

        @tools.register("web_download", "从 URL 下载文件到本地", schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "文件的 URL"},
                "filepath": {"type": "string", "description": "本地保存路径"},
            },
            "required": ["url", "filepath"],
        })
        def _web_download(**kwargs):
            url = kwargs.get("url", "")
            filepath = kwargs.get("filepath", "")
            if not url:
                return {"ok": False, "error": "请提供 URL"}
            if not filepath:
                return {"ok": False, "error": "请提供本地保存路径 (filepath)"}
            return self._web_http.download(url, filepath)

        @tools.register("web_batch", "批量请求多个 URL", schema={
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "description": "URL 列表"},
                "max_concurrency": {"type": "integer", "description": "最大并发数，默认 5"},
            },
            "required": ["urls"],
        })
        def _web_batch(**kwargs):
            urls = kwargs.get("urls", [])
            max_concurrency = kwargs.get("max_concurrency", 5)
            if not urls:
                return {"ok": False, "error": "请提供 URL 列表 (urls)"}
            results = self._web_http.batch_request(urls, max_concurrency=max_concurrency)
            return {"ok": True, "total": len(results), "results": results}

        # ════════════════════════════════════════════════════════════
        #  天气查询工具 — 云枢查询天气的能力
        # ════════════════════════════════════════════════════════════

        from agent.system_tools import get_weather

        @tools.register("get_weather", "查询天气信息。使用 wttr.in 服务，无需 API Key。支持三种格式：text（简洁文本）、json（完整JSON数据）、full（完整文本预报）", schema={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如 Beijing、Shanghai、Tokyo，留空自动查询当前IP所在地天气"},
                "format": {"type": "string", "enum": ["text", "json", "full"], "description": "返回格式：text=简洁文本, json=完整JSON数据, full=完整文本预报"},
            },
        })
        def _get_weather(**kwargs):
            city = kwargs.get("city", "")
            fmt = kwargs.get("format", "text")
            return get_weather(city=city, format=fmt)

        # ════════════════════════════════════════════════════════════
        #  进程管理工具 — 云枢运行/管理程序的能力
        # ════════════════════════════════════════════════════════════

        from agent.system_tools import (
            start_process, list_processes, stop_process, execute_shell,
        )

        @tools.register("run_program", "在本地运行白名单程序（如 notepad.exe, calc.exe, python.exe）。args 是参数列表，cwd 是工作目录", schema={
            "type": "object",
            "properties": {
                "program": {"type": "string", "description": "程序名称，如 notepad.exe"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "参数列表"},
                "cwd": {"type": "string", "description": "工作目录"},
            },
            "required": ["program"],
        })
        def _run_program(**kwargs):
            program = kwargs.get("program", "")
            args = kwargs.get("args")
            cwd = kwargs.get("cwd")
            if not program:
                return {"ok": False, "error": "请提供要运行的程序名（program）"}
            # 权限检查
            perm = self._permission.check_action(f"run:{program}", f"运行程序 {program}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            return start_process(program, args=args, cwd=cwd)

        @tools.register("list_processes", "列出当前正在运行的白名单程序列表", schema={
            "type": "object",
            "properties": {},
        })
        def _list_processes(**kwargs):
            procs = list_processes()
            return {"ok": True, "processes": procs, "count": len(procs)}

        @tools.register("stop_process", "终止指定 PID 的白名单程序", schema={
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "进程 PID"},
            },
            "required": ["pid"],
        })
        def _stop_process(**kwargs):
            pid = kwargs.get("pid")
            if pid is None:
                return {"ok": False, "error": "请提供进程 PID"}
            # 权限检查
            perm = self._permission.check_action(f"stop_process:{pid}", f"终止进程 {pid}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            return stop_process(pid)

        # ════════════════════════════════════════════════════════════
        #  Shell 工具 — 云枢执行 shell 命令的能力
        # ════════════════════════════════════════════════════════════

        @tools.register("shell_execute", "在本地执行 shell 命令（支持 bash/cmd/PowerShell）。自动检测或手动指定 shell 类型。返回 stdout、stderr 和退出码。注意：危险命令（如 rm -rf）会被安全系统阻止", schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "shell": {"type": "string", "description": "shell 类型: auto（自动检测）/ bash / cmd / powershell，默认 auto"},
                "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 120"},
            },
            "required": ["command"],
        })
        def _shell_execute(**kwargs):
            command = kwargs.get("command", "")
            shell = kwargs.get("shell", "auto")
            cwd = kwargs.get("cwd")
            timeout = kwargs.get("timeout", 30)

            if not command:
                return {"ok": False, "error": "请提供要执行的命令（command）"}

            # PermissionSystem 扫描命令内容
            try:
                check = self._permission.check_text(command)
                if check.get("level") == "critical":
                    matches = [m.get("description", "") for m in check.get("matches", [])]
                    return {
                        "ok": False,
                        "error": f"危险命令被安全系统阻止: {matches}",
                        "blocked": True,
                        "level": "critical",
                    }
                elif check.get("level") == "warning":
                    desc = "; ".join(m.get("description", "") for m in check.get("matches", []))
                    perm = self._permission.check_action(
                        f"shell_execute:warning:{desc[:100]}",
                        f"执行可能危险的命令: {desc}",
                    )
                    if not perm.allowed:
                        return {
                            "ok": False,
                            "error": f"权限系统拒绝: {perm.reason}",
                            "blocked": True,
                            "level": "warning",
                        }
            except Exception as e:
                logger.warning("[shell_execute] 安全检查异常: %s", e)
                return {"ok": False, "error": "安全检查系统故障，拒绝执行", "blocked": True}

            # 执行命令（timeout=None 时使用默认 30 秒）
            return execute_shell(command, shell=shell, cwd=cwd, timeout=timeout or 30)

        # ════════════════════════════════════════════════════════════════════════════════
        #  代码审查工具 — 云枢审查代码的能力
        # ════════════════════════════════════════════════════════════════════════════════

        @tools.register("code_review", "执行结构化代码审查，检查代码在安全、性能、可维护性、API兼容性和测试方面的质量。支持审查文件或 git diff。基于 gstack review 检查清单", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要审查的文件路径（绝对路径），可选"},
                "diff": {"type": "string", "description": "git diff 文本内容，可选。如果未提供 path 则使用此内容"},
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["安全", "性能", "可维护性", "API兼容性", "测试"]},
                    "description": "审查维度列表，默认全部。安全(SQL注入/XSS/密钥泄露)、性能(N+1查询/算法复杂度)、可维护性(死代码/魔法数字)、API兼容性(破坏性变更)、测试(边界值/负路径)",
                },
            },
        })
        def _code_review(**kwargs):
            path = kwargs.get("path", "")
            diff = kwargs.get("diff", "")
            dimensions = kwargs.get("dimensions")
            from agent.code_review import code_review as _code_review
            return _code_review(path=path, diff=diff, dimensions=dimensions)

        # ════════════════════════════════════════════════════════════════════════════════
        #  扩展管理工具（让云枢能自主安装 Skills / MCP / Channels / Plugins）
        # ════════════════════════════════════════════════════════════════════════════════

        def _make_ext_mgr():
            """创建带 NetworkConfigManager 的 ExtensionManager"""
            try:
                from agent.extensions.manager import ExtensionManager as _E
                from agent.network_config import NetworkConfigManager as _N
                try:
                    from config import _get_secure_manager
                    _ncm = _N(secure_manager=_get_secure_manager())
                except Exception:
                    _ncm = _N()
                return _E(network_config_mgr=_ncm)
            except Exception:
                from agent.extensions.manager import ExtensionManager as _E
                return _E()

        @tools.register("ext_install", "安装扩展（技能/MCP服务/通道/插件）。让我能自主获取新能力。", schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["skill", "claude_skill", "mcp", "channel", "plugin"],
                    "description": "扩展类型：skill=应用层技能, claude_skill=Claude Code技能, mcp=MCP服务, channel=通信通道, plugin=插件",
                },
                "source": {
                    "type": "string",
                    "description": "扩展来源。格式：内置ID(如 self_reflection / filesystem)，github:user/repo，url:https://...，local:/path，npm:package，pip:package",
                },
                "name": {
                    "type": "string",
                    "description": "扩展名称（可选，自定义安装时使用）",
                },
                "description": {
                    "type": "string",
                    "description": "扩展描述（可选）",
                },
                "params": {
                    "type": "object",
                    "description": "额外参数（可选，如技能参数、通道配置等）",
                },
            },
            "required": ["type", "source"],
        })
        def _ext_install(**kwargs):
            ext_type = kwargs.get("type", "")
            source = kwargs.get("source", "")
            params = kwargs.get("params", {})

            if not ext_type or not source:
                return {"ok": False, "error": "请指定扩展类型和来源"}

            # 懒加载扩展管理器
            try:
                from agent.extensions.manager import ExtensionManager as _ExtMgr
                from agent.network_config import NetworkConfigManager as _NCM
                try:
                    from config import _get_secure_manager
                    _ncm = _NCM(secure_manager=_get_secure_manager())
                except Exception:
                    _ncm = _NCM()
                _em = _ExtMgr(network_config_mgr=_ncm)
                result = _em.install(
                    ext_type, source,
                    name=kwargs.get("name", ""),
                    description=kwargs.get("description", ""),
                    **{k: v for k, v in params.items() if k not in ("name", "description")},
                )
                # 统一结果格式：ExtensionManager 使用 message 键，
                # 但工具调用系统期望 error 键
                if isinstance(result, dict) and "message" in result and "error" not in result:
                    if not result.get("ok", False):
                        result["error"] = result["message"]
                    result.pop("message")
                return result
            except Exception as e:
                logger.error(f"扩展安装失败: {e}")
                return {"ok": False, "error": f"扩展安装失败: {e}"}

        @tools.register("ext_uninstall", "卸载扩展。移除不再需要的技能、MCP服务、通道或插件。", schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["skill", "claude_skill", "mcp", "channel", "plugin"],
                    "description": "扩展类型",
                },
                "id": {
                    "type": "string",
                    "description": "扩展ID",
                },
            },
            "required": ["type", "id"],
        })
        def _ext_uninstall(**kwargs):
            ext_type = kwargs.get("type", "")
            ext_id = kwargs.get("id", "")
            try:
                _em = _make_ext_mgr()
                return _em.uninstall(ext_type, ext_id)
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @tools.register("ext_list", "列出已安装的扩展（技能/MCP服务/通道/插件）。查询当前有哪些能力可用。", schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["skill", "claude_skill", "mcp", "channel", "plugin", ""],
                    "description": "按类型筛选（留空列出全部）",
                },
            },
        })
        def _ext_list(**kwargs):
            ext_type = kwargs.get("type") or None
            ext_type = kwargs.get("type") or None
            try:
                # �� skill ���ͣ�ֱ�Ӵ� skills.json ��ȡ����ȫ���� extensions.json
                if not ext_type or ext_type == "skill":
                    import json, os
                    skill_states = {}
                    for sf in [
                        os.path.join(os.path.dirname(__file__), "..", "agent", "data", "skills.json"),
                        os.path.join(os.path.dirname(__file__), "..", "data", "skills.json"),
                    ]:
                        if os.path.exists(sf):
                            with open(sf, "r", encoding="utf-8") as f:
                                sd = json.load(f)
                            for s in sd.get("skills", []):
                                sid = s["id"]
                                skill_states[sid] = {
                                    "name": s.get("name", sid),
                                    "enabled": s.get("enabled", True),
                                    "description": s.get("description", ""),
                                }
                    skills_result = []
                    for sid, info in skill_states.items():
                        skills_result.append({
                            "ext_id": sid, "ext_type": "skill",
                            "name": info["name"], "description": info["description"],
                            "status": "enabled" if info["enabled"] else "disabled",
                            "enabled": info["enabled"],
                        })
                    if ext_type == "skill":
                        return {"ok": True, "type": "skill", "extensions": skills_result}
                    # ext_type is None - also get other types
                    _em = _make_ext_mgr()
                    all_types = _em.get_installed_by_type()
                    other_results = []
                    for key in ["claude_skills", "mcps", "channels", "plugins"]:
                        other_results.extend(all_types.get(key, []))
                    return {"ok": True, "extensions": skills_result + other_results}
                # �� skill �����߳���
                _em = _make_ext_mgr()
                if ext_type:
                    return {"ok": True, "type": ext_type, "extensions": _em.list_all(ext_type)}
                return {"ok": True, "extensions": _em.get_installed_by_type()}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        @tools.register("ext_toggle", "启用或禁用扩展。临时打开/关闭某个技能、MCP服务或通道。", schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["skill", "mcp", "channel", "plugin"],
                    "description": "扩展类型",
                },
                "id": {
                    "type": "string",
                    "description": "扩展ID",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "是否启用（留空则切换当前状态）",
                },
            },
            "required": ["type", "id"],
        })
        def _ext_toggle(**kwargs):
            ext_type = kwargs.get("type", "")
            ext_id = kwargs.get("id", "")
            enabled = kwargs.get("enabled")
            try:
                _em = _make_ext_mgr()
                return _em.toggle(ext_type, ext_id, enabled)
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @tools.register("ext_discover", "发现可用的扩展。搜索内置注册表、社区市场和GitHub上有什么新能力可以安装。", schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（可选）",
                },
                "type": {
                    "type": "string",
                    "enum": ["skill", "claude_skill", "mcp", "channel", "plugin", ""],
                    "description": "按类型筛选（可选）",
                },
            },
        })
        def _ext_discover(**kwargs):
            query = kwargs.get("query", "")
            ext_type = kwargs.get("type") or None
            try:
                from agent.extensions.market import ExtensionMarket as _ExtMarket
                _em = _make_ext_mgr()
                _market = _ExtMarket()

                installed = _em.discover_all()
                if query:
                    market_results = _market.search_all(query, ext_type)
                    return {
                        "ok": True,
                        "query": query,
                        "builtin": installed,
                        "market": market_results,
                    }

                return {"ok": True, **installed}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @tools.register("ext_configure", "配置扩展参数。调整技能、MCP服务或通道的设置项。", schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["skill", "mcp", "channel", "plugin"],
                    "description": "扩展类型",
                },
                "id": {
                    "type": "string",
                    "description": "扩展ID",
                },
                "config": {
                    "type": "object",
                    "description": "配置键值对",
                },
            },
            "required": ["type", "id", "config"],
        })
        def _ext_configure(**kwargs):
            ext_type = kwargs.get("type", "")
            ext_id = kwargs.get("id", "")
            config = kwargs.get("config", {})
            try:
                _em = _make_ext_mgr()
                return _em.configure(ext_type, ext_id, config)
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @tools.register("ext_send_channel", "通过已安装的通信通道发送消息。比如发Webhook、邮件等。", schema={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "通道ID",
                },
                "message": {
                    "type": "string",
                    "description": "消息内容",
                },
                "subject": {
                    "type": "string",
                    "description": "邮件主题（邮件通道专用）",
                },
                "to": {
                    "type": "string",
                    "description": "收件人（邮件通道专用）",
                },
            },
            "required": ["channel_id", "message"],
        })
        def _ext_send_channel(**kwargs):
            channel_id = kwargs.get("channel_id", "")
            message = kwargs.get("message", "")
            extra = {k: v for k, v in kwargs.items() if k not in ("channel_id", "message")}
            try:
                _em = _make_ext_mgr()
                return _em.send_channel_message(channel_id, message, **extra)
            except Exception as e:
                return {"ok": False, "error": str(e)}

        # ════════════════════════════════════════════════════════════
        #  PDF 工具 — 云枢处理 PDF 文件的能力
        # ════════════════════════════════════════════════════════════

        from agent.pdf_tools import (
            read_pdf_text, merge_pdfs, split_pdf, get_pdf_info,
        )

        # ════════════════════════════════════════════════════════════
        #  架构图工具 — 云枢生成系统架构图的能力
        # ════════════════════════════════════════════════════════════

        from agent.diagram_tools import generate_architecture_diagram

        @tools.register("read_pdf", "读取 PDF 文件中的文本内容（支持指定页码范围）", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "PDF 文件路径"},
                "pages": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "要读取的页码列表（1-based），如 [1, 3, 5]，不传则读取全部页面",
                },
            },
            "required": ["path"],
        })
        def _read_pdf(**kwargs):
            path = kwargs.get("path", "")
            pages = kwargs.get("pages")
            if not path:
                return {"ok": False, "error": "请提供 PDF 文件路径（path）"}
            return read_pdf_text(path, pages=pages)

        @tools.register("merge_pdf", "合并多个 PDF 文件为一个。paths 是要合并的源文件列表，output_path 是输出路径", schema={
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array", "items": {"type": "string"},
                    "description": "要合并的 PDF 文件路径列表",
                },
                "output_path": {"type": "string", "description": "合并后的输出文件路径"},
            },
            "required": ["paths", "output_path"],
        })
        def _merge_pdf(**kwargs):
            paths = kwargs.get("paths", [])
            output_path = kwargs.get("output_path", "")
            if not paths:
                return {"ok": False, "error": "请提供要合并的文件列表（paths）"}
            if not output_path:
                return {"ok": False, "error": "请提供输出文件路径（output_path）"}
            # PermissionSystem 安全检查
            perm = self._permission.check_action(f"write_file:{output_path}", f"合并 PDF 到 {output_path}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            return merge_pdfs(paths, output_path)

        @tools.register("split_pdf", "拆分 PDF 文件为多个独立的 PDF。支持按指定页范围拆分或每页拆为一个文件", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "源 PDF 文件路径"},
                "output_dir": {"type": "string", "description": "输出目录"},
                "ranges": {
                    "type": "array", "items": {
                        "type": "array", "items": {"type": "integer"},
                        "minItems": 2, "maxItems": 2,
                    },
                    "description": "页范围列表（1-based），如 [[1,3], [5,7]] 表示拆分为第1-3页和第5-7页两个文件，不传则每页拆为一个文件",
                },
            },
            "required": ["path", "output_dir"],
        })
        def _split_pdf(**kwargs):
            path = kwargs.get("path", "")
            output_dir = kwargs.get("output_dir", "")
            ranges = kwargs.get("ranges")
            if not path:
                return {"ok": False, "error": "请提供源 PDF 文件路径（path）"}
            if not output_dir:
                return {"ok": False, "error": "请提供输出目录（output_dir）"}
            # PermissionSystem 安全检查
            perm = self._permission.check_action(f"write_dir:{output_dir}", f"拆分 PDF 到目录 {output_dir}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            return split_pdf(path, output_dir, ranges=ranges)

        # ════════════════════════════════════════════════════════════
        #  架构图工具 — 云枢生成系统架构图的能力
        # ════════════════════════════════════════════════════════════

        @tools.register("arch_diagram", "生成系统架构图。根据组件列表生成漂亮的 HTML+SVG 架构图文件，支持多种组件类型（frontend/backend/database/cloud/security/external）", schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "架构图标题"},
                "components": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "组件名称"},
                            "type": {"type": "string", "description": "组件类型: frontend/backend/database/cloud/security/external"},
                            "description": {"type": "string", "description": "组件描述（可选）"},
                        },
                        "required": ["name", "type"],
                    },
                    "description": "组件列表",
                },
                "output_path": {"type": "string", "description": "输出 HTML 文件路径"},
            },
            "required": ["title", "components", "output_path"],
        })
        def _arch_diagram(**kwargs):
            title = kwargs.get("title", "")
            components = kwargs.get("components", [])
            output_path = kwargs.get("output_path", "")
            # 权限检查
            perm = self._permission.check_action(f"write_file:{output_path}", f"生成架构图到 {output_path}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            return generate_architecture_diagram(title, components, output_path)

        @tools.register("get_pdf_info", "获取 PDF 文件的元信息（页数、标题、作者、创建时间、文件大小等）", schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "PDF 文件路径"},
            },
            "required": ["path"],
        })
        def _get_pdf_info(**kwargs):
            path = kwargs.get("path", "")
            if not path:
                return {"ok": False, "error": "请提供 PDF 文件路径（path）"}
            return get_pdf_info(path)

        # ════════════════════════════════════════════════════════════
        #  中文文本优化工具 — 检测并去除 AI 写作痕迹
        # ════════════════════════════════════════════════════════════

        from agent.text_tools import humanize_zh

        @tools.register("humanize_zh", "检测中文文本中的 AI 写作痕迹并给出优化建议。基于 24 种 AI 写作模式检测规则（词汇/句式/结构/风格等），返回检测到的模式列表、问题数量和优化建议。aggressive=True 启用更严格的检测模式", schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待检测的中文文本"},
                "aggressive": {"type": "boolean", "description": "是否启用严格检测模式（检测更多边缘情况，如同长度连续句子等），默认 false"},
            },
            "required": ["text"],
        })
        def _humanize_zh(**kwargs):
            text = kwargs.get("text", "")
            aggressive = kwargs.get("aggressive", False)
            if not text:
                return {"ok": False, "error": "请提供待检测的文本（text）"}
            result = humanize_zh(text, aggressive=aggressive)
            return {"ok": True, **result}

        logger.info("已注册 %d 个内置工具（含文件系统、互联网、进程管理、扩展管理、PDF处理、中文文本优化）", len(tools.list_tools()))

    # ════════════════════════════════════════════════════════════════════════════════
    #  状态查询
    # ════════════════════════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """获取云枢的完整状态报告"""
        readings = self.body.collect_quick()
        profile = self._behavior.profile

        status = {
            "云枢": {
                "版本": "2.0" if self._v2_lifetrace else "1.0",
                "会话": self._session_id,
                "运行中": self._running,
                "交互次数": self._interaction_count,
            },
            "行为模式": {
                "当前模式": self._current_mode.value,
                "模式名称": profile.label,
                "模式描述": profile.description,
                "可接受任务": profile.can_accept_tasks,
                "启用反思": profile.enable_reflection,
            },
            "身体状态": {
                str(r.sensor_name): {
                    "值": f"{r.value}{r.unit}",
                    "严重程度": r.severity,
                    "描述": r.description,
                }
                for r in readings
            },
            "系统": {
                "工具数量": len(tools.list_tools()),
                "记忆摘要": self._memory.load_summary()[0][:100] if self._memory.load_summary() else "无",
                "反思记录数": len(self._reflection_history),
            },
        }

        if self._v2_lifetrace and self._trace_recorder:
            lifetrace_stats = self._trace_recorder.get_statistics()
            status["LifeTrace"] = {
                "源节点数": lifetrace_stats.get("source_nodes", 0),
                "主题节点数": lifetrace_stats.get("topic_nodes", 0),
                "主题列表": lifetrace_stats.get("topics", []),
            }

        if self._v2_persona and self._persona_model:
            status["Persona"] = {
                "人格ID": self._persona_model.persona.get("persona_id"),
                "版本": self._persona_model.persona.get("version"),
            }

        if self._v2_distillation and self._persona_extractor:
            preferences_report = self._persona_extractor.export_preferences()
            preferences = preferences_report.get("preferences", {})
            status["人格蒸馏"] = {
                "启用": True,
                "学习间隔": self._distillation_interval,
                "话题兴趣": list(preferences.get("topic_interest", {}).keys())[:5],
                "最后更新": preferences.get("last_updated", "未知"),
            }

        return status

    def get_status_text(self) -> str:
        """获取人类可读的状态描述"""
        profile = self._behavior.profile
        health = self.body.get_health_report()
        
        v2_info = ""
        if self._v2_lifetrace:
            v2_info = " (V2增强版)"
        
        return (
            f"* 云枢{v2_info}状态\n"
            f"━━━━━━━━━━━━━━━\n"
            f"会话: {self._session_id}\n"
            f"运行中: {'是' if self._running else '否'}\n"
            f"交互次数: {self._interaction_count}\n"
            f"行为模式: {profile.label}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{health}"
        )

    # ════════════════════════════════════════════════════════════════════════════════
    #  动态配置
    # ════════════════════════════════════════════════════════════════════════════════

    def configure_llm(self, provider: str = "", api_key: str = "",
                      model: str = "", base_url: str = ""):
        """动态配置 LLM 连接"""
        if not api_key:
            return {"ok": False, "error": "缺少 API Key"}

        try:
            new_llm = LLMService(
                provider=provider or "openai",
                api_key=api_key,
                model=model or "gpt-4",
                base_url=base_url,
            )
            try:
                test = new_llm.chat(
                    messages=[{"role": "user", "content": "返回 OK 两个字母"}],
                    max_tokens=10,
                    temperature=0.1,
                )
                logger.info("LLM 连接测试成功: %s", test[:50])
            except Exception as e:
                logger.error("LLM 连接测试失败: %s", e)
                return {"ok": False, "error": "连接测试失败: %s" % e}

            old = self._memory._llm_service
            self._memory._llm_service = new_llm
            self._memory._summarizer._llm = new_llm
            self._llm = new_llm

            # 重建 ToolCallingService（LLM 配置后才真正可用）
            tc_cfg = self._config.get("tool_calling", {})
            if tc_cfg.get("enabled", True):
                from agent.tool_calling import ToolCallingService
                self._tool_calling_service = ToolCallingService(
                    llm_service=self._llm,
                    max_rounds=tc_cfg.get("max_rounds", 5),
                    tool_timeout=tc_cfg.get("tool_timeout", 60),
                )
                logger.info("[ok] 联网引擎（ToolCallingService）已激活（LLM 配置后重建）")
            else:
                self._tool_calling_service = None

            self._memory.clear_memory()
            self._reflection_history.clear()

            logger.info("LLM 已重新配置: %s / %s", provider, model)
            return {"ok": True, "provider": provider, "model": model}
        except Exception as e:
            logger.error("LLM 配置失败: %s", e)
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
                "reason": "规划引擎未加载"
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
            }
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
            }
        }

    # ════════════════════════════════════════════════════════════════════════════════
    #  多模态功能
    # ════════════════════════════════════════════════════════════════════════════════

    def speak(self, text: str, save_to_file: bool = False):
        """语音合成"""
        if not self._voice_manager:
            return {"ok": False, "error": "语音功能未启用"}
        if not self._is_skill_enabled("voice_interaction"):
            return {"ok": False, "error": "语音交互技能已禁用"}
        
        try:
            logger.info("🎤 准备说话: %s...", text[:50])
            result = self._voice_manager.speak(text, save_to_file)
            return {"ok": result.success, "text": text, "audio_path": result.audio_path}
        except Exception as e:
            logger.error("[FAIL] 语音合成失败: %s", e)
            return {"ok": False, "error": str(e)}

    def listen(self, duration: int = 5):
        """语音识别"""
        if not self._voice_manager:
            return {"ok": False, "error": "语音功能未启用"}
        
        try:
            logger.info("🎤 开始录音 (%d秒)...", duration)
            result = self._voice_manager.listen(duration)
            return {"ok": result.success, "text": result.text}
        except Exception as e:
            logger.error("[FAIL] 语音识别失败: %s", e)
            return {"ok": False, "error": str(e)}

    def voice_chat(self, duration: int = 5, speak_response: bool = True):
        """语音对话"""
        logger.info("🎤 启动语音对话模式...")
        
        listen_result = self.listen(duration)
        if not listen_result.get("ok"):
            if speak_response:
                self.speak("抱歉，我没有听清您在说什么。")
            return {"ok": False, "error": listen_result.get("error"), "text": None, "response": None}
        
        user_input = listen_result.get("text", "")
        if not user_input or not user_input.strip():
            if speak_response:
                self.speak("抱歉，我没有听到任何声音。")
            return {"ok": False, "error": "没有听到内容", "text": user_input, "response": None}
        
        logger.info("💬 语音输入: %s", user_input)
        response = self.chat(user_input)
        
        if speak_response:
            self.speak(response)
        
        return {
            "ok": True,
            "text": user_input,
            "response": response
        }

    def look_at_screen(self, region: Optional[tuple] = None):
        """观察屏幕内容"""
        if not self._ocr_sensor:
            return {"ok": False, "error": "OCR功能未启用"}
        
        try:
            reading = self._ocr_sensor.capture_and_ocr(region)
            ocr_text = "\n".join(
                "[%s] %s" % (r.data.get('position', '?'), r.data.get('text', ''))
                for r in reading.data
            )
            return {
                "ok": True,
                "reading": reading.to_dict() if hasattr(reading, 'to_dict') else {},
                "text": ocr_text[:5000]
            }
        except Exception as e:
            logger.error("[FAIL] OCR失败: %s", e)
            return {"ok": False, "error": str(e)}

    def get_voice_status(self) -> dict:
        """获取语音功能状态"""
        if not self._voice_manager:
            return {"enabled": False, "available": False}
        
        try:
            status = self._voice_manager.get_status()
            return {
                "enabled": True,
                "available": True,
                "tts": status.get("tts_available", False),
                "stt": status.get("stt_available", False),
                "tts_engines": status.get("tts_engines", [])
            }
        except Exception as e:
            return {"enabled": True, "available": False, "error": str(e)}

    def get_multimodal_status(self) -> dict:
        """获取多模态功能总状态"""
        return {
            "voice": self.get_voice_status(),
            "ocr": {
                "enabled": self._ocr_sensor is not None,
                "available": self._ocr_sensor is not None
            }
        }

    def get_memory_stats(self) -> dict:
        """获取向量记忆统计"""
        if not self._vector_memory:
            return {"available": False}
        
        return {
            "available": True,
            "total_memories": len(self._vector_memory.items),
            "collection_name": self._vector_memory.collection_name,
            "persist_dir": self._vector_memory.persist_dir,
        }

    def search_memory(self, query: str, top_k: int = 5) -> list:
        """搜索向量记忆"""
        if not self._vector_memory:
            return []
        
        try:
            return self._vector_memory.search(query, top_k)
        except Exception as e:
            logger.error("搜索记忆失败: %s", e)
            return []

    def clear_memory(self):
        """清空向量记忆"""
        if self._vector_memory:
            self._vector_memory.clear()

    # ════════════════════════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════════════════════
    #  P6 快照功能
    # ════════════════════════════════════════════════════════════════════════════════

    def save_snapshot(self, snapshot_id: Optional[str] = None, incremental: bool = False, force: bool = False) -> dict:
        """保存当前状态快照
        
        Args:
            snapshot_id: 快照ID，如果为None则自动生成
            incremental: 是否使用增量快照（节省存储空间）
            force: 是否强制保存，忽略频率限制
            
        Returns:
            快照保存结果
        """
        if not self._snapshot_manager:
            return SnapshotResult(
                success=False,
                error_message="P6快照管理器未启用"
            )
        
        logger.info("[P6] 保存状态快照...")
        result = self._snapshot_manager.save_snapshot(
            self,
            snapshot_id=snapshot_id,
            incremental=incremental,
            force=force
        )
        
        if result.success:
            logger.info("[P6] [OK] 快照保存成功: %s", result.snapshot_id)
            if result.is_incremental:
                logger.info("[P6] 💾 增量快照，节省 %d 字节", result.space_saved_bytes)
        else:
            logger.error("[P6] [FAIL] 快照保存失败: %s", result.error_message)
        
        return result

    def load_snapshot(self, snapshot_id: Optional[str] = None):
        """从快照恢复状态
        
        Args:
            snapshot_id: 要加载的快照ID，如果为None则加载最新快照
            
        Returns:
            恢复的DigitalLife实例，或None表示加载失败
        """
        if not self._snapshot_manager:
            logger.error("[P6] [FAIL] P6快照管理器未启用")
            return None
        
        logger.info("[P6] 从快照恢复状态...")
        restored_instance = self._snapshot_manager.load_snapshot(
            digital_life_class=self.__class__,
            snapshot_id=snapshot_id
        )
        
        if restored_instance:
            logger.info("[P6] [OK] 快照恢复成功")
        else:
            logger.error("[P6] [FAIL] 快照恢复失败")
        
        return restored_instance

    def list_snapshots(self) -> list:
        """列出所有可用快照
        
        Returns:
            快照信息列表
        """
        if not self._snapshot_manager:
            return []
        
        return self._snapshot_manager.list_snapshots()

    def get_snapshot_performance(self) -> dict:
        """获取快照性能统计
        
        Returns:
            性能统计信息字典
        """
        if not self._snapshot_manager:
            return {"available": False}
        
        return self._snapshot_manager.performance_monitor.get_performance_summary()

    def print_snapshot_performance_panel(self):
        """打印快照性能监控面板"""
        if not self._snapshot_manager:
            print("P6快照管理器未启用")
            return
        
        self._snapshot_manager.performance_monitor.print_performance_panel()

    def get_p6_snapshot_status(self) -> dict:
        """获取P6快照系统状态
        
        Returns:
            状态信息字典
        """
        return {
            "available": _P6_SNAPSHOT_AVAILABLE,
            "enabled": self._snapshot_manager is not None,
            "snapshots": self.list_snapshots(),
            "performance": self.get_snapshot_performance()
        }

    # ════════════════════════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════════════════════
    #  状态持久化功能
    # ════════════════════════════════════════════════════════════════════════════════

    def _build_state_data(self) -> dict:
        """构建状态数据字典"""
        state = {
            "version": "2.0",
            "session_id": self._session_id,
            "interaction_count": self._interaction_count,
            "running": self._running,
            "started_at": self._started_at,
            "current_mode": self._current_mode.value if self._current_mode else "NORMAL",
            "health_check_interval": self._health_check_interval,
            "last_health_check": self._last_health_check,
            "config": self._config,
        }
        
        # 添加反思历史（最近10条）
        if self._reflection_history:
            state["reflection_history"] = self._reflection_history[-10:]
        
        # 添加行为控制器状态
        if hasattr(self._behavior, '_current_mode'):
            state["behavior"] = {
                "current_mode": self._behavior._current_mode.value if hasattr(self._behavior._current_mode, 'value') else str(self._behavior._current_mode),
            }
        
        # 添加身体传感器状态
        if hasattr(self.body, 'get_health_report'):
            try:
                state["body_status"] = self.body.get_health_report()
            except Exception:
                pass
        
        return state

    def save_state(self, state_id: Optional[str] = None) -> dict:
        """保存当前运行状态到文件
        
        Args:
            state_id: 状态ID，如果为None则自动生成
            
        Returns:
            保存结果字典
        """
        try:
            from .state_manager import save_state, StateSaveResult
            
            state_data = self._build_state_data()
            result: StateSaveResult = save_state(state_data, state_id=state_id)
            
            if result.success:
                logger.info("状态保存成功: %s", result.state_id)
                return {
                    "ok": True,
                    "state_id": result.state_id,
                    "file_path": result.file_path,
                    "data_size": result.data_size,
                    "elapsed_ms": result.elapsed_ms,
                    "created_at": result.created_at,
                }
            else:
                logger.error("状态保存失败: %s", result.error_message)
                return {"ok": False, "error": result.error_message}
                
        except ImportError:
            logger.error("状态管理器模块未找到")
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            logger.error("状态保存异常: %s", e)
            return {"ok": False, "error": str(e)}

    def load_state(self, state_id: Optional[str] = None) -> dict:
        """从文件加载运行状态
        
        Args:
            state_id: 状态ID，如果为None则加载最新状态
            
        Returns:
            加载结果字典
        """
        try:
            from .state_manager import load_state, StateLoadResult
            
            result: StateLoadResult = load_state(state_id=state_id)
            
            if result.success:
                state_data = result.state_data
                
                # 恢复状态
                if 'interaction_count' in state_data:
                    self._interaction_count = state_data['interaction_count']
                if 'current_mode' in state_data:
                    try:
                        from .behavior_controller import BehaviorMode
                        mode_value = state_data['current_mode']
                        if hasattr(BehaviorMode, mode_value):
                            self._current_mode = getattr(BehaviorMode, mode_value)
                    except Exception:
                        pass
                if 'config' in state_data:
                    self._config = state_data['config']
                
                logger.info("状态加载成功: %s", result.state_id)
                return {
                    "ok": True,
                    "state_id": result.state_id,
                    "file_path": result.file_path,
                    "elapsed_ms": result.elapsed_ms,
                    "state_data": state_data,
                }
            else:
                logger.error("状态加载失败: %s", result.error_message)
                return {"ok": False, "error": result.error_message}
                
        except ImportError:
            logger.error("状态管理器模块未找到")
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            logger.error("状态加载异常: %s", e)
            return {"ok": False, "error": str(e)}

    def list_states(self) -> list:
        """列出所有可用的状态文件"""
        try:
            from .state_manager import get_state_manager, StateInfo
            
            manager = get_state_manager()
            states = manager.list_states()
            
            return [
                {
                    "state_id": s.state_id,
                    "file_path": s.file_path,
                    "created_at": s.created_at.isoformat(),
                    "data_size": s.data_size,
                    "version": s.version,
                }
                for s in states
            ]
            
        except ImportError:
            return []
        except Exception as e:
            logger.error("列出状态失败: %s", e)
            return []

    # ════════════════════════════════════════════════════════════════════════════════
    #  日志级别管理
    # ════════════════════════════════════════════════════════════════════════════════

    def set_log_level(self, level: str, logger_name: Optional[str] = None) -> dict:
        """动态调整日志级别
        
        Args:
            level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            logger_name: 日志记录器名称，None表示根日志记录器
            
        Returns:
            操作结果字典
        """
        try:
            from .state_manager import set_log_level
            
            success = set_log_level(level, logger_name)
            
            if success:
                logger.info("日志级别调整成功: %s", level)
                return {"ok": True, "level": level, "logger": logger_name or "root"}
            else:
                return {"ok": False, "error": "无效的日志级别: %s" % level}
                
        except ImportError:
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            logger.error("设置日志级别失败: %s", e)
            return {"ok": False, "error": str(e)}

    def get_log_level(self, logger_name: Optional[str] = None) -> dict:
        """获取当前日志级别
        
        Args:
            logger_name: 日志记录器名称，None表示根日志记录器
            
        Returns:
            当前日志级别信息
        """
        try:
            from .state_manager import get_log_level
            
            level = get_log_level(logger_name)
            return {"ok": True, "level": level, "logger": logger_name or "root"}
                
        except ImportError:
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            logger.error("获取日志级别失败: %s", e)
            return {"ok": False, "error": str(e)}

    def list_loggers(self) -> list:
        """列出所有已注册的日志记录器及其级别"""
        try:
            from .state_manager import get_state_manager
            
            manager = get_state_manager()
            loggers = manager.list_loggers()
            
            return [
                {"name": name, "level": level}
                for name, level in loggers
            ]
            
        except ImportError:
            return []
        except Exception as e:
            logger.error("列出日志记录器失败: %s", e)
            return []

    def __del__(self):
        """析构时释放资源"""
        try:
            self.stop()
            if hasattr(self, '_memory'):
                del self._memory
            if hasattr(self, 'body'):
                self.body.stop_file_watch()
                self.body.stop_event_monitor()
        except Exception:
            pass
