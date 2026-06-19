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
import threading
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

from .digital_life_state import DigitalLifeStateMixin
from .digital_life_persona import DigitalLifePersonaMixin

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
7. 你拥有软件管理能力——当用户需要搜索、下载或安装软件时，你必须通过函数调用来执行操作（调用 software_search、software_install、software_list、software_uninstall），不要只在文本中说"让我搜索一下"或描述你要做什么——直接调出对应工具并执行
8. ⚡ 工具铁律：用户每次请求实际操作（读文件、查时间、搜信息、执行命令、查询国际新闻等）时，你的**第一条回复必须是函数调用（tool_calls）**，绝不能先发文字。描述你将做什么而不调用工具 = 严重的执行失败。错误示范："让我查一下"、"我会调用XX工具"、"我看看能不能"——说这些话而不发起 tool_calls 等同于没有执行。正确流程：用户请求 → 立即调用对应工具 → 等待结果 → 根据结果回复。如果你不确定用哪个工具，先浏览可用工具列表再决定。
9. 🌐 语言要求：你的所有内部思考（reasoning/reasoning_content）必须使用中文。思考过程、推理步骤、决策分析，全部用中文表达。

{skill_instructions}

## 当前工具与技能状态
以下是当前已启用/禁用的工具和技能，当被问及时请如实回答：
{tool_status}"""


class DigitalLife(DigitalLifePersonaMixin, DigitalLifeStateMixin):
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
        self._memory_token_limit = memory_cfg.get("token_limit", 131072)
        self._llm: Optional[LLMService] = self._memory._llm_service
        self._llm_pro: Optional[LLMService] = None  # 深度模型（由模型调度器加载）
        self._tool_calling_service = None
        self._model_router = None  # 模型路由器（启动后从网络配置加载）
        self._current_tool_steps = []  # 实时工具步骤（用于前端轮询展示）
        self._thinking_mode = {"mode": "idle", "label": ""}  # 思考状态：idle/instinct/light/thinking/deep
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
                max_rounds=tc_cfg.get("max_rounds", 20),
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
        self._last_context_warning: dict | None = None
        self._last_was_template: bool = False
        self._started_at = None

        # ── 自主维护循环 ──
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._last_compress_time = 0.0
        self._last_summary_time = 0.0
        self._last_health_time = 0.0
        self._last_prune_time = 0.0
        # 从 self._config 读取维护周期配置
        maint_cfg = self._config.get("maintenance", {}).get("intervals", {})
        self._maint_interval_health = maint_cfg.get("health", 30)
        self._maint_interval_compress = maint_cfg.get("compress", 60)
        self._maint_interval_prune = maint_cfg.get("prune", 120)
        self._maint_interval_summary = maint_cfg.get("summary", 300)

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

    # ── P5 懒加载方法已提取到 DigitalLifePersonaMixin ──
    # _ensure_lifetrace, _ensure_persona, _ensure_distillation

    # ════════════════════════════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════════════════════════════

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
                metadata={"event": "system_start"}
            )

        logger.info("* 云枢已觉醒！感知神经全面激活。")
        logger.info("[维护] 自主循环已启动（健康=%ds, 压缩=%ds, 修剪=%ds, 摘要=%ds）",
                    self._maint_interval_health, self._maint_interval_compress,
                    self._maint_interval_prune, self._maint_interval_summary)

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
                metadata={"event": "system_stop"}
            )

        logger.info("* 云枢正在休眠...")

    @property
    def is_running(self) -> bool:
        """我是否正在运行"""
        return self._running

    # ════════════════════════════════════════════════════════════════════════════════
    #  自主维护循环（守护线程，DigitalLife 启动时自动运行）
    # ════════════════════════════════════════════════════════════════════════════════

    def _autonomous_loop(self):
        """自主维护事件循环（守护线程）

        周期性任务：
        - 健康检查: 默认每 30s
        - 压缩检查: 默认每 60s
        - 智能修剪: 默认每 120s
        - 多层摘要: 默认每 300s
        """
        logger.info("[维护] 自主循环已启动")
        while not self._stop_event.is_set():
            now = time.time()

            # 1. 健康检查
            if now - self._last_health_time >= self._maint_interval_health:
                self._run_maint_health()
                self._last_health_time = now

            # 2. 压缩检查
            if now - self._last_compress_time >= self._maint_interval_compress:
                self._run_maint_compress()
                self._last_compress_time = now

            # 3. 智能修剪
            if now - self._last_prune_time >= self._maint_interval_prune:
                self._run_maint_prune()
                self._last_prune_time = now

            # 4. 多层摘要刷新
            if now - self._last_summary_time >= self._maint_interval_summary:
                self._run_maint_summary()
                self._last_summary_time = now

            self._stop_event.wait(5)

        logger.info("[维护] 自主循环已停止")

    def _run_maint_health(self):
        """健康自检"""
        try:
            readings = self.check_health()
            significant = [r for r in readings if r.severity in ("warning", "critical")]
            if significant:
                for r in significant:
                    logger.info("[自检] %s: %s %.1f%s",
                                r.sensor_name, r.label, r.value, r.unit)
        except Exception as e:
            logger.debug("[自检] 健康检查失败: %s", e)

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
                    logger.info("[维护] 触发自动压缩 (%.1f%%)", total / limit * 100)
                    self._memory._need_compress = True
        except Exception as e:
            logger.debug("[维护] 压缩检查失败: %s", e)

    def _run_maint_prune(self):
        """智能修剪"""
        try:
            if hasattr(self, '_memory') and self._memory:
                self._memory.smart_prune()
        except Exception as e:
            logger.debug("[维护] 智能修剪失败: %s", e)

    def _run_maint_summary(self):
        """刷新多层摘要"""
        try:
            if hasattr(self, '_memory') and self._memory:
                self._memory.generate_summary_levels()
                logger.info("[维护] 周期性多层摘要已刷新")
        except Exception as e:
            logger.debug("[维护] 摘要刷新失败: %s", e)

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

        # 所有路径前统一检查上下文使用率
        self._last_context_warning = self._check_context_usage()
        if self._last_context_warning and self._last_context_warning["level"] != "info":
            logger.info("[上下文] %s（%.1f%%）", self._last_context_warning["message"],
                        self._last_context_warning["pct"])

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
                return {"ok": True, "data": self.body.get_health_report()}

            @self._planning_tools.register("get_status", "获取完整状态")
            def _get_status_tool(**kwargs):
                return {"ok": True, "data": self.get_status()}

            @self._planning_tools.register("search_memory", "搜索记忆")
            def _search_memory_tool(**kwargs):
                query = kwargs.get("query", "")
                if not query:
                    return {"ok": False, "error": "请提供搜索关键词"}
                return {"ok": True, "data": self._combined_search(query)}

            @self._planning_tools.register("get_sensor_summary", "获取传感器摘要")
            def _get_sensor_summary_tool(**kwargs):
                return {"ok": True, "data": self.body.get_sensor_summary()}

            @self._planning_tools.register("llm_chat", "进行对话")
            def _llm_chat_tool(**kwargs):
                response_text = kwargs.get("response", "")
                return {"ok": True, "data": response_text}

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
        """自我反思——纯本地实现，零 LLM 调用

        基于规则评估任务响应质量，生成结构化反思记录。
        """
        reflection_text = self._local_reflect(task[:500], response[:1000])

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
                topic="reflection",
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

    @staticmethod
    def _local_reflect(task: str, response: str) -> str:
        """基于规则的本地反思评估，零 LLM 调用

        从三个维度评估：理解准确度、响应完整性、改进方向。
        最后生成一条"值得记住的新经验"。
        """
        import re
        if not task or not response:
            return "（任务或响应为空，跳过反思）"

        task_lower = task.lower()
        resp_lower = response.lower()
        lines = []

        # ── 维度 1：理解准确度 ──
        # 提取任务中的关键词，检查响应是否覆盖
        key_terms = set(re.findall(r'[a-zA-Z_]\w{3,}', task_lower))
        # 过滤掉常见停用词
        stop_words = {'this', 'that', 'with', 'from', 'have', 'been', 'what', 'which', 'there', 'their', 'about', 'would', 'could', 'should', 'your', 'will', 'them', 'then', 'than', 'when', 'where', 'more', 'also', 'some', 'into', 'other', 'only', 'over', 'such', 'very', 'just', 'well', 'make', 'like', 'take', 'know', 'think'}
        key_terms -= stop_words
        if key_terms:
            covered = sum(1 for t in key_terms if t in resp_lower)
            ratio = covered / max(len(key_terms), 1)
            if ratio >= 0.8:
                lines.append("✅ 准确理解了用户需求，覆盖了大部分关键点")
            elif ratio >= 0.5:
                lines.append("🟡 基本理解了需求，但部分细节可以更深入")
            else:
                lines.append("🔄 可能需要进一步确认用户需求中的关键点")
        else:
            lines.append("ℹ️ 任务以中文为主，基于上下文判断理解准确")

        # ── 维度 2：响应完整性 ──
        resp_len = len(response)
        task_len = max(len(task), 1)
        ratio = resp_len / task_len

        # 是否包含代码块
        has_code = bool(re.search(r'```[\s\S]*?```', response))
        # 是否包含步骤/列表
        has_steps = bool(re.search(r'(?:步骤|第一步|首先|其次|最后|\d+\.\s)', response))
        # 是否给出具体方案
        has_solution = bool(re.search(r'(可以|建议|推荐|使用|采用|方案|方法|方式)', response))

        completeness_signals = sum([has_code, has_steps, has_solution])
        if ratio < 0.3:
            lines.append("📏 响应相对简洁，如需更详细可要求我展开")
        elif ratio > 5:
            lines.append("📏 响应较为详细，已提供充分信息")
        else:
            if completeness_signals >= 2:
                lines.append("✅ 响应完整，包含代码/步骤和具体建议")
            elif completeness_signals >= 1:
                lines.append("🟡 响应基本完整，可考虑补充更多细节")
            else:
                lines.append("📏 响应包含基础信息")

        # ── 维度 3：改进方向 ──
        improvements = []
        # 检查是否有明确的问题待解决
        if re.search(r'(但是|不过|然而|缺点|局限|注意)', response):
            improvements.append("已指出局限性")
        # 检查是否给出后续建议
        if re.search(r'(下一步|后续|进一步|可以试试|参考)', response):
            improvements.append("给出了后续方向")
        # 检查是否欢迎追问
        if re.search(r'(欢迎|随时|继续|进一步|如果需要)', response):
            improvements.append("开放了追问空间")

        if improvements:
            lines.append("💡 改进: " + "；".join(improvements))
        else:
            lines.append("💡 可以补充后续建议或开放追问空间")

        # ── 维度 4：值得记住的经验 ──
        # 提取任务中最独特的主题作为记忆点
        if key_terms:
            import random
            # 用确定性方式选择关键词（基于哈希）
            term_list = sorted(key_terms)[:3]
            experience = f"本次交互涉及: {', '.join(term_list)}"
            lines.append(f"📝 {experience}")

        return "\n".join(lines)

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

    @property
    def last_context_warning(self) -> dict | None:
        """获取上一条回复的上下文使用警告"""
        return self._last_context_warning

    # ════════════════════════════════════════════════════════════════════════════════
    #  内部方法
    # ════════════════════════════════════════════════════════════════════════════════

    def _process_user_input(self, user_input: str) -> str:
        """处理用户输入的内部闭环（含本地工作流路由）"""
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

        # ── 工作流路由：意图分类 → 模板匹配（零 LLM） ──
        # 低置信度自动降级到 LLM；中置信度模板保底但允许被用户追问覆盖
        try:
            from agent.response_workflows import (
                IntentRouter, ResponseTemplates, Confidence,
            )
            intent, confidence = IntentRouter.classify(user_input)
            logger.info("[路由] 意图=%s, 置信度=%s", intent, confidence)

            # 检查是否是模板回复后的追问（用户对模板不满意再次输入）
            is_follow_up = getattr(self, '_last_was_template', False) and confidence != Confidence.HIGH

            # 用户表达不满/纠正时，直接走 LLM
            dissatisfaction = bool(re.search(
                r'(不是|不对|没听懂|不理解|我问的|我说的是|换个|换一种|不是这个|重新|重来|算了)',
                user_input,
            ))
            if dissatisfaction:
                logger.info("[路由] 检测到用户不满/纠正，降级到 LLM")
                is_follow_up = True
            if is_follow_up:
                logger.info("[路由] 检测到模板后追问，降级到 LLM")
                self._last_was_template = False

            if not is_follow_up:
                template_response = ResponseTemplates.for_intent(
                    intent, confidence=confidence,
                    hour=datetime.now().hour
                )
                if template_response:
                    logger.info("[路由] ✓ 使用本地模板，跳过 LLM 调用")
                    self._set_thinking_mode("instinct")
                    response = template_response
                    self._last_was_template = True
                    self._last_context_warning = None
                    self._interaction_count += 1
                    self._memory.score_and_save_message("user", user_input)
                    self._memory.score_and_save_message("assistant", response)
                    try:
                        self._memory.infer_working_memory(user_input, response)
                    except Exception:
                        pass
                    logger.info("[路由] 模板回复完成 (#%d)", self._interaction_count)
                    return response
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[路由] 路由失败，降级到 LLM: %s", e)

        # 走 LLM 路径时清除模板标记
        self._last_was_template = False

        response = self._call_llm(user_input, body_status)

        # 上下文快满时在回复中追加切换建议，并生成延续摘要
        if self._last_context_warning and self._last_context_warning["level"] == "critical":
            # 从记忆系统获取摘要用于会话延续
            carry_summary = ""
            try:
                summary_data = self._memory.load_summary()
                if summary_data:
                    carry_summary = summary_data[0][:2000]
            except Exception:
                pass
            if not carry_summary:
                carry_summary = (
                    f"本次对话共 {self._interaction_count} 轮，"
                    f"最新用户提问：{user_input[:200]}"
                )
            self._last_context_warning["summary"] = carry_summary
            response += (
                "\n\n---\n💡 **当前会话上下文即将耗尽**"
                f"（已使用 {self._last_context_warning['pct']:.0f}%）。"
                "\n点击下方「创建新会话」按钮，我会携带之前的记忆继续对话。"
            )

        if self._behavior.profile.enable_reflection:
            if self._is_skill_enabled("self_reflection"):
                self.self_reflect(user_input, response)
            else:
                logger.debug("[SkillGate] self_reflection 已禁用，跳过")

        # ── 使用重要性评分保存消息 ──
        self._memory.score_and_save_message("user", user_input)
        self._memory.score_and_save_message("assistant", response)

        # ── 更新工作记忆（从对话中提取关键信息） ──
        try:
            self._memory.infer_working_memory(user_input, response)
        except Exception as e:
            logger.debug("[WM] 工作记忆更新失败: %s", e)

        # ── 向量记忆保存 ──
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

        # 维护任务（压缩/修剪/摘要/健康）已由 _autonomous_loop 守护线程定期执行

        return response

    def _check_context_usage(self) -> dict | None:
        """检查上下文使用率和压缩退化程度，返回警告信息

        策略：
        - 当前 token 百分比 > 95% → critical（即将溢出）
        - 累积压缩次数 >= 5 → critical（摘要严重退化，建议换会话）
        - 累积压缩次数 >= 3 → warning（摘要已退化，准备换会话）
        - 当前 token 百分比 > 80% → warning（常规高水位）
        - 当前 token 百分比 > 60% → info
        - 其他 → None

        Returns:
            {"level": "info"|"warning"|"critical", "pct": float, "message": str}
        """
        if not self._memory:
            return None
        try:
            context = self._memory.get_context(token_limit=self._memory_token_limit)
            if not context:
                return None
            total_tokens = self._memory._token_counter.count_messages(context)
            limit = self._memory_token_limit
            pct = (total_tokens / limit) * 100
            compress_rounds = self._memory.compress_rounds

            # 压缩退化比当前百分比更重要：
            # 压缩 5 次后即使百分比不高，摘要质量也已明显下降
            if compress_rounds >= 5:
                return {
                    "level": "critical",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": (
                        f"已压缩 {compress_rounds} 次，摘要退化明显"
                        f"（当前使用 {pct:.0f}%），建议创建新会话继续对话"
                    ),
                }
            if compress_rounds >= 3:
                return {
                    "level": "warning",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": (
                        f"已压缩 {compress_rounds} 次，建议准备切换到新会话"
                    ),
                }

            # 常规 token 水位检查
            if pct >= 95:
                return {
                    "level": "critical",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": f"上下文已使用 {pct:.0f}%，即将耗尽，建议创建新会话继续对话",
                }
            elif pct >= 80:
                return {
                    "level": "warning",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": f"上下文已使用 {pct:.0f}%，建议准备切换到新会话",
                }
            elif pct >= 60:
                return {
                    "level": "info",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": f"上下文已使用 {pct:.0f}%",
                }
            return None
        except Exception as e:
            logger.debug("检查上下文使用率时出错: %s", e)
            return None

    # ── 身体/工具状态方法已提取到 DigitalLifePersonaMixin ──

    def _call_llm(self, user_input: str, body_status: str) -> str:
        """调用 LLM 生成响应（集成工作记忆 + Token 预算分配）"""
        mode = self._current_mode
        profile = self._behavior.profile

        # 设置思考状态
        self._set_thinking_mode()

        # ── 1. 构建 system prompt（含精简记忆线索） ──
        # 获取简短的记忆上下文（约 300 chars），帮助 AI 保持对话方向
        memory_context = ""
        try:
            summary_data = self._memory.load_summary()
            if summary_data and summary_data[0]:
                memory_context = summary_data[0][:300]
            else:
                context_messages = self._memory.get_context(token_limit=5000)
                if context_messages:
                    recent = context_messages[-2:]
                    lines = []
                    for m in recent:
                        if m.get('content'):
                            lines.append("%s: %s" % (m['role'], m['content'][:100]))
                    memory_context = " | ".join(lines)
        except Exception:
            pass
        if not memory_context:
            memory_context = "（暂无历史对话）"

        # 简短工作记忆（约 200 chars），帮助 AI 了解当前任务状态
        wm_text = ""
        try:
            wm = self._memory.get_working_memory()
            if wm:
                items = []
                for k, v in wm.items():
                    if k == "interaction_count":
                        continue
                    if isinstance(v, list):
                        items.append("%s: %s" % (k, '; '.join(str(x)[:60] for x in v[-3:])))
                    else:
                        items.append("%s: %s" % (k, str(v)[:80]))
                if items:
                    combined = " | ".join(items)
                    if len(combined) > 200:
                        combined = combined[:200] + "..."
                    wm_text = "\n[工作中] " + combined
        except Exception:
            pass

        tool_status = self._build_tool_status_text()
        skill_instructions = self._build_skill_instructions()

        system_prompt = DEFAULT_SYSTEM_PROMPT.format(
            current_date=datetime.now().strftime("%Y年%m月%d日"),
            body_status=body_status,
            mode_name=profile.label,
            mode_description=profile.description,
            memory_context=memory_context,
            tool_status=tool_status,
            skill_instructions=skill_instructions,
        )
        if wm_text:
            system_prompt += wm_text

        # ── System prompt Token 预算检查 ──
        try:
            _sp_tokens = self._memory._token_counter.count(system_prompt)
            _sp_budget = 10000  # system_instruction 预算
            if _sp_tokens > _sp_budget:
                logger.warning("[Token] system prompt %d tokens 超预算 %d，截断工具状态",
                               _sp_tokens, _sp_budget)
                # 仅保留工具/技能的基础摘要
                _brief_tools = (tool_status[:300] + chr(10) + "...（已截断）") if len(tool_status) > 300 else tool_status
                system_prompt = DEFAULT_SYSTEM_PROMPT.format(
                    current_date=datetime.now().strftime("%Y年%m月%d日"),
                    body_status=body_status,
                    mode_name=profile.label,
                    mode_description=profile.description,
                    memory_context=memory_context,
                    tool_status=_brief_tools,
                    skill_instructions="",
                )
                if wm_text:
                    system_prompt += wm_text
            logger.info("[Token] system prompt: %d tokens (预算 %d)", _sp_tokens, _sp_budget)
        except Exception:
            pass

        # ── 2. 使用预算分配组装上下文消息 ──
        messages = []
        try:
            recent = self._memory._storage.load_recent_messages(limit=50)
            summary_data = self._memory.load_summary()
            summary_text = summary_data[0] if summary_data else None
            tool_results = getattr(self, '_last_tool_steps', [])

            # 使用预算感知的上下文组装
            budget_context = self._memory.get_budget_context(
                recent_messages=recent,
                summary_text=summary_text,
                tool_results=tool_results,
            )
            messages.extend(budget_context)
        except Exception as e:
            logger.warning("Budget context assembly failed: %s, falling back", e)
            try:
                context = self._memory.get_context(token_limit=self._memory_token_limit)
                if context:
                    messages.extend(context)
            except Exception:
                pass

        if self._tool_calling_service:
            messages.append({
                "role": "system",
                "content": (
                    "⚡ 立即检查：用户这句话需要工具吗？如果需要，直接发起函数调用。"
                    "绝对禁止只发文字描述你将要做的操作。"
                    "没调用工具 = 没执行。立即行动。"
                ),
            })

        messages.append({"role": "user", "content": user_input})

        if self._llm:
            try:
                self._last_tool_steps = []
                self._current_tool_steps = []

                from agent import tools as _tools
                from agent.tool_calling import _summarize_tool_result, _clean_for_json
                _tool_defs = _tools.get_tool_defs(whitelist=self._get_enabled_tools_whitelist())
                _client = self._llm._get_client()

                # 智能调度：选择最合适的模型处理当前任务
                _selected_llm, _selected_model = self._select_model_for_request(user_input)
                _use_pro = _selected_model != self._llm.model
                if _use_pro and self._llm_pro:
                    logger.info("[_call_llm] 调度到深度模型: %s (主模型: %s)", _selected_model, self._llm.model)
                    _client = self._llm_pro._get_client()
                    _working_model = _selected_model
                else:
                    _working_model = self._llm.model
                    logger.info("[_call_llm] 使用主模型: %s (pro可用=%s)", _working_model, self._llm_pro is not None)

                _working = list(messages)
                _reasoning = None
                _max_rounds = 3
                response = ""

                # 根据模型类型自适应输出 token 限制
                _model_lower = (_working_model or "").lower()
                if any(k in _model_lower for k in ("pro", "ultra", "reasoner", "opus", "claude-4", "gpt-4-turbo", "o1", "o3")):
                    _max_output = 16384
                else:
                    _max_output = 8192

                for _round_idx in range(_max_rounds):
                    _api_msgs = [{"role": "system", "content": system_prompt}] + _working
                    _kwargs = {
                        "model": _working_model,
                        "messages": _api_msgs,
                        "max_tokens": _max_output,
                        "temperature": 0.3,
                    }
                    if _tool_defs:
                        _kwargs["tools"] = _tool_defs
                    # 最后一轮：注入总结指令，移除工具定义，逼 LLM 输出纯文本
                    if _round_idx == _max_rounds - 1:
                        _kwargs.pop("tools", None)
                        _working.append({
                            "role": "system",
                            "content": "这是最后一轮，请根据之前获取到的信息给出完整总结。",
                        })
                        _api_msgs = [{"role": "system", "content": system_prompt}] + _working
                        _kwargs["messages"] = _api_msgs

                    _resp = _client.chat.completions.create(**_kwargs)
                    _msg = _resp.choices[0].message

                    _reasoning = _reasoning or getattr(_msg, "reasoning_content", None)
                    if _reasoning:
                        self._last_reasoning = _reasoning

                    if not (hasattr(_msg, 'tool_calls') and _msg.tool_calls):
                        # 检测 XML 格式的工具调用（DeepSeek 等模型有时输出此格式）
                        _xml_tools = []
                        if _msg.content and __import__('re').search(r'<[^>]*tool_calls[^>]*>', _msg.content):
                            try:
                                from agent.tool_calling import ToolCallingService as _TCSvc, _summarize_tool_result
                                _xml_tools = _TCSvc._extract_xml_tool_calls(_msg.content)
                            except Exception as _xml_e:
                                logger.debug("[_call_llm] XML 工具提取失败: %s", _xml_e)
                        if _xml_tools:
                            logger.info("[_call_llm] 检测到 XML 格式工具调用: %d 个", len(_xml_tools))
                            _assistant_tc = []
                            _tool_results = []
                            for _xc in _xml_tools:
                                _fn_name = _xc["function"]["name"]
                                _fn_args = json.loads(_xc["function"]["arguments"])
                                _tc_id = _xc["id"]
                                _assistant_tc.append(_xc)
                                self._current_tool_steps.append({
                                    "type": "tool_call", "tool": _fn_name,
                                    "args": _fn_args, "id": _tc_id,
                                })
                                try:
                                    _tool_result_data = _tools.call(_fn_name, **_fn_args)
                                    _tool_summary = _summarize_tool_result(_fn_name, _tool_result_data)
                                    _status = "success"
                                except Exception as _te:
                                    _tool_summary = f"执行失败: {_te}"
                                    _status = "error"
                                self._current_tool_steps.append({
                                    "type": "tool_result", "tool": _fn_name, "id": _tc_id,
                                    "status": _status, "summary": _tool_summary[:200],
                                })
                                _tool_results.append({
                                    "role": "tool", "tool_call_id": _tc_id,
                                    "content": _tool_summary[:2000],
                                })
                            self._last_tool_steps = list(self._current_tool_steps)
                            _working.append({
                                "role": "assistant", "content": _msg.content,
                                "tool_calls": _assistant_tc,
                            })
                            _working.extend(_tool_results)
                            continue  # 继续下一轮，让 LLM 基于工具结果生成总结
                        response = _msg.content or _reasoning or ""
                        break

                    _assistant_tc = []
                    _tool_results = []
                    for _tc in _msg.tool_calls:
                        _fn_name = _tc.function.name
                        _fn_args = json.loads(_tc.function.arguments)
                        _tc_id = _tc.id
                        _assistant_tc.append({
                            "id": _tc_id, "type": "function",
                            "function": {"name": _fn_name, "arguments": _tc.function.arguments},
                        })
                        self._current_tool_steps.append({
                            "type": "tool_call", "tool": _fn_name, "args": _fn_args, "id": _tc_id,
                        })
                        try:
                            _tool_result_data = _tools.call(_fn_name, **_fn_args)
                            _tool_summary = _summarize_tool_result(_fn_name, _tool_result_data)
                            _status = "success"
                        except Exception as _te:
                            _tool_summary = f"执行失败: {_te}"
                            _status = "error"
                        self._current_tool_steps.append({
                            "type": "tool_result", "tool": _fn_name, "id": _tc_id,
                            "status": _status, "summary": _tool_summary[:200],
                        })
                        _tool_results.append({
                            "role": "tool", "tool_call_id": _tc_id,
                            "content": json.dumps(_clean_for_json(_tool_result_data), ensure_ascii=False)[:2000],
                        })

                    self._last_tool_steps = list(self._current_tool_steps)

                    _working.append({
                        "role": "assistant", "content": _msg.content,
                        "tool_calls": _assistant_tc,
                    })
                    _working.extend(_tool_results)
                else:
                    # 循环耗尽仍无纯文本回复，用最后工具结果摘要作为保底
                    if not response:
                        _last_summaries = [s.get("summary", "") for s in self._current_tool_steps
                                           if s["type"] == "tool_result"][-3:]
                        response = "（已获取以下信息：）" + chr(10) + chr(10).join(_last_summaries) if _last_summaries else "（已处理完毕）"

                if profile.response_prefix:
                    response = profile.response_prefix + chr(10) + response

                # 兜底：检测响应是否包含 XML 工具调用文本（模型偶尔会返回这个而非总结）
                if response and __import__('re').search(r'<[^>]*tool_calls[^>]*>', response):
                    logger.warning("[_call_llm] 响应中包含 XML 工具调用，使用工具结果摘要替换")
                    _fb_summaries = [s.get("summary", "") for s in self._current_tool_steps
                                     if s["type"] == "tool_result"][-5:]
                    if _fb_summaries:
                        response = "已获取到以下信息：\n" + "\n".join(f"  - {s}" for s in _fb_summaries)
                    else:
                        response = "（已处理完毕）"

                return response
            except Exception as _e:
                logger.error("LLM 调用失败: %s", _e)
                return "（抱歉，处理时遇到了问题: %s）" % str(_e)
        else:
            self._set_thinking_mode("instinct")
            return self._build_offline_response(user_input)

    def _call_llm_v2(self, user_input: str, body_status: str) -> str:
        """V2 调用 LLM 生成响应（使用 Persona 系统）"""
        profile = self._behavior.profile

        # 设置思考状态
        self._set_thinking_mode()

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
            context = self._memory.get_context(token_limit=self._memory_token_limit)
            if context:
                messages.extend(context)
        except Exception:
            pass

        messages.append({"role": "user", "content": user_input})

        if self._llm:
            try:
                if self._tool_calling_service:
                    tools_whitelist = self._get_enabled_tools_whitelist()

                    # ── 智能调度：选择最合适的模型处理当前任务 ──
                    _selected_llm, _selected_model = self._select_model_for_request(user_input)
                    _use_pro = _selected_model != self._llm.model

                    if _use_pro and self._llm_pro:
                        logger.info("[调度] %s → 深度模型处理", user_input[:20])
                        from agent.tool_calling import ToolCallingService
                        _tc_pro = ToolCallingService(
                            llm_service=self._llm_pro,
                            max_rounds=self._tool_calling_service._max_rounds,
                            tool_timeout=self._tool_calling_service._tool_timeout,
                        )
                        _result = _tc_pro.chat_with_steps(
                            messages=messages, system_prompt=system_prompt,
                            max_tokens=8192, temperature=0.3,
                            tools_whitelist=tools_whitelist,
                            on_step=lambda s: self._current_tool_steps.append(s),
                        )
                        response = _result["text"]
                        self._last_tool_steps = _result.get("steps", [])
                        self._last_reasoning = _result.get("reasoning") or self._last_reasoning
                    else:
                        _result = self._tool_calling_service.chat_with_steps(
                            messages=messages, system_prompt=system_prompt,
                            max_tokens=8192, temperature=0.3,
                            tools_whitelist=tools_whitelist,
                            on_step=lambda s: self._current_tool_steps.append(s),
                        )
                        response = _result["text"]
                        self._last_tool_steps = _result.get("steps", [])
                        self._last_reasoning = _result.get("reasoning") or self._last_reasoning
                else:
                    response = self._llm.chat(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=8192,
                        temperature=0.3,  # 低温 = 更确定性的工具调用
                    )
                if profile.response_prefix:
                    response = "%s\n%s" % (profile.response_prefix, response)

                # 兜底：检测响应是否包含 XML 工具调用
                if response and __import__('re').search(r'<[^>]*tool_calls[^>]*>', response):
                    logger.warning("[_call_llm_v2] 响应中包含 XML 工具调用，使用摘要替换")
                    _fb_steps = self._last_tool_steps or []
                    _fb_summaries = [s.get("summary", "") for s in _fb_steps
                                     if s.get("type") == "tool_result"][-5:]
                    if _fb_summaries:
                        response = "已获取到以下信息：\n" + "\n".join(f"  - {s}" for s in _fb_summaries)
                    else:
                        response = "（已处理完毕）"
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

    def _register_builtin_tools(self):
        """注册云枢的内置工具"""
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
                return {"ok": False, "error": "请提供搜索关键词"}
            result = self._combined_search(query)
            return {"ok": True, "data": result}

        @tools.register("remember", "记住重要信息，存储到长期记忆。后续可通过 search_memory 搜索到。important 级别会额外备份到桌面文件。", schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆的标识名称，用于搜索定位"},
                "content": {"type": "string", "description": "要记住的具体内容"},
                "importance": {"type": "string", "enum": ["normal", "important"], "description": "重要程度，important 会额外备份到桌面永久记忆文件"},
            },
            "required": ["key", "content"],
        })
        def _remember(**kwargs):
            key = kwargs.get("key", "")
            content = kwargs.get("content", "")
            importance = kwargs.get("importance", "normal")
            if not key or not content:
                return {"ok": False, "error": "请提供 key 和 content 参数"}

            memory_text = f"[{key}] {content}"
            mem_id = None

            # 存到向量记忆
            if self._vector_memory:
                try:
                    mem_id = self._vector_memory.add(
                        content=memory_text,
                        metadata={"type": "user_memory", "key": key, "importance": importance}
                    )
                except Exception as e:
                    logger.error("保存向量记忆失败: %s", e)

            # important 级别额外备份到桌面
            if importance == "important":
                try:
                    backup_path = os.path.join(os.path.expanduser("~"), "Desktop", "云枢_永久记忆.md")
                    with open(backup_path, "a", encoding="utf-8") as f:
                        f.write(f"\n## {key}\n{content}\n")
                    backup_note = " + 桌面备份"
                except Exception as e:
                    logger.error("桌面备份失败: %s", e)
                    backup_note = ""
            else:
                backup_note = ""

            return {"ok": True, "data": f"✅ 已记住「{key}」{backup_note}", "mem_id": mem_id}

        @tools.register("get_sensor_summary", "查看所有传感器状态", schema={
            "type": "object",
            "properties": {},
        })
        def _get_sensor_summary(**kwargs):
            return self.body.get_sensor_summary()

        @tools.register("search_lifetrace", "搜索我的记忆（使用 LifeTrace）", schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        })
        def _search_lifetrace(**kwargs):
            if not self._v2_lifetrace or not self._memory_retriever:
                return {"ok": False, "error": "LifeTrace 系统未启用，此工具不可用", "available": False}
            query = kwargs.get("query", "")
            if not query:
                return {"ok": False, "error": "请提供搜索关键词"}
            try:
                results = self._memory_retriever.retrieve(query, limit=10)
                if not results:
                    return {"ok": True, "data": f"没有找到与 '{query}' 相关的记忆。", "count": 0}
                lines = "\n".join(
                    f"- {node.content[:100]}"
                    for node in results
                )
                return {"ok": True, "data": lines, "count": len(results)}
            except Exception as e:
                return {"ok": False, "error": f"搜索失败: {e}"}

        @tools.register("get_persona_info", "查看当前人格配置", schema={
            "type": "object",
            "properties": {},
        })
        def _get_persona_info(**kwargs):
            if not self._v2_persona or not self._persona_model:
                return {"ok": False, "error": "Persona 系统未启用，此工具不可用", "available": False}
            identity = self._persona_model.get_identity()
            style = self._persona_model.get_expression_style()
            return {"ok": True, "data": {
                "identity": identity.get("identity"),
                "expression_style": style,
            }}

        @tools.register("get_preferences", "查看学习到的用户偏好", schema={
            "type": "object",
            "properties": {},
        })
        def _get_preferences(**kwargs):
            report = self.get_preferences_report()
            if not report or not report.get("enabled"):
                return {"ok": False, "error": "人格蒸馏功能未启用，此工具不可用", "available": False}
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
            return {"ok": True, "data": "\n".join(lines)}

        @tools.register("trigger_distillation", "触发一次人格蒸馏学习", schema={
            "type": "object",
            "properties": {},
        })
        def _trigger_distillation(**kwargs):
            if not self._v2_distillation:
                return {"ok": False, "error": "人格蒸馏功能未启用，此工具不可用", "available": False}
            self._run_persona_distillation()
            return {"ok": True, "data": "人格蒸馏已触发！"}

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
            # 路径安全校验：防止路径遍历攻击
            try:
                from pathlib import Path
                resolved = Path(root_path).resolve()
                # 检查 pattern 是否包含路径穿越符
                if ".." in pattern.split("/") or ".." in pattern.split("\\"):
                    return {"ok": False, "error": "搜索模式包含不安全的路径穿越符（..），已拒绝"}
                # 检查 root_path 是否在有效范围内
                allowed_base = Path(".").resolve()
                if not resolved.exists():
                    return {"ok": False, "error": f"搜索路径不存在: {root_path}"}
                if allowed_base not in resolved.parents and resolved != allowed_base:
                    return {"ok": False, "error": "搜索路径超出工作目录范围，已拒绝"}
            except Exception as e:
                logger.warning("路径安全校验异常: %s", e)
            return search_files(pattern, root_path=root_path)

        # ════════════════════════════════════════════════════════════
        #  压缩/解压工具 — 云枢压缩和解压文件的能力
        # ════════════════════════════════════════════════════════════

        from agent.compression_tools import compress, decompress

        @tools.register("compress", "将文件或目录压缩为 zip 或 tar.gz 格式。支持大文件分块流式处理", schema={
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "源文件或目录路径（必填）"},
                "output_path": {"type": "string", "description": "输出文件路径（可选，默认生成到源文件同目录）"},
                "format": {"type": "string", "enum": ["zip", "tar.gz"], "description": "压缩格式，默认 zip"},
            },
            "required": ["source_path"],
        })
        def _compress(**kwargs):
            source_path = kwargs.get("source_path", "")
            output_path = kwargs.get("output_path", "")
            fmt = kwargs.get("format", "zip")
            if not source_path:
                return {"ok": False, "error": "请提供源路径（source_path）"}
            return compress(source_path, output_path=output_path, format=fmt)

        @tools.register("decompress", "解压 zip 或 tar.gz 压缩文件。内置 Zip Slip 攻击防护，安全可靠", schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "压缩文件路径（必填）"},
                "output_dir": {"type": "string", "description": "解压输出目录（可选，默认解压到压缩文件所在目录下的同名文件夹）"},
            },
            "required": ["file_path"],
        })
        def _decompress(**kwargs):
            file_path = kwargs.get("file_path", "")
            output_dir = kwargs.get("output_dir", "")
            if not file_path:
                return {"ok": False, "error": "请提供压缩文件路径（file_path）"}
            return decompress(file_path, output_dir=output_dir)

        # ════════════════════════════════════════════════════════════
        #  文件比较工具 — 云枢比较文件差异的能力
        # ════════════════════════════════════════════════════════════

        from agent.diff_tools import diff_files

        @tools.register("diff_files", "比较两个文件的差异，返回 unified diff 格式（类似 git diff）。可统计新增、删除和变更行数", schema={
            "type": "object",
            "properties": {
                "path1": {"type": "string", "description": "第一个文件路径（必填）"},
                "path2": {"type": "string", "description": "第二个文件路径（必填）"},
                "context_lines": {"type": "integer", "description": "上下文行数，默认 3"},
            },
            "required": ["path1", "path2"],
        })
        def _diff_files(**kwargs):
            path1 = kwargs.get("path1", "")
            path2 = kwargs.get("path2", "")
            context_lines = kwargs.get("context_lines", 3)
            if not path1:
                return {"ok": False, "error": "请提供第一个文件路径（path1）"}
            if not path2:
                return {"ok": False, "error": "请提供第二个文件路径（path2）"}
            return diff_files(path1, path2, context_lines=context_lines)

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
            "default_engine": search_cfg.get("default_engine", "sogou"),
            "cache_ttl": search_cfg.get("cache_ttl", 300),
            "timeout": search_cfg.get("timeout", 30),
            "engine_priority": search_cfg.get("engine_priority", ["tavily", "firecrawl", "sogou", "baidu", "so360", "duckduckgo"]),
            "engine_enabled": search_cfg.get("engine_enabled", {
                "tavily": True,
                "firecrawl": True,
                "sogou": True,
                "baidu": True,
                "so360": True,
                "duckduckgo": True,
                "bing": True,
                "google": True,
                "brave": True,
            }),
            # API Keys
            "tavily_api_key": search_api_keys.get("tavily", ""),
            "firecrawl_api_key": search_api_keys.get("firecrawl", ""),
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
        self._web_aggregator = None  # 聚合搜索器，按需懒加载
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

        @tools.register("web_search", "搜索互联网信息。支持单引擎搜索和多引擎聚合搜索（aggregate=True时并发调用2-3个引擎，结果去重评分排序）", schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "engine": {"type": "string", "description": "搜索引擎名称（可选）。不指定则按优先级自动选择。注意：aggregate=True 时此参数被忽略"},
                "num_results": {"type": "integer", "description": "返回结果数，默认 10"},
                "page": {"type": "integer", "description": "页码，默认 1。注意：aggregate=True 时此参数被忽略"},
                "aggregate": {"type": "boolean", "description": "是否启用多引擎聚合搜索模式。True 时并发调用 2-3 个搜索引擎，去重、评分、排序后返回最优结果，结果质量更高。默认 False"},
            },
            "required": ["query"],
        })
        def _web_search(**kwargs):
            query = kwargs.get("query", "")
            engine = kwargs.get("engine", "")
            num_results = kwargs.get("num_results", 10)
            page = kwargs.get("page", 1)
            aggregate = kwargs.get("aggregate", False)
            if not query:
                return {"ok": False, "error": "请提供搜索关键词"}

            # ── 聚合搜索模式 ──
            if aggregate:
                if self._web_aggregator is None:
                    from agent.search_aggregator import SearchAggregator
                    self._web_aggregator = SearchAggregator(self._web_search)
                result = self._web_aggregator.aggregate_search(
                    query, num_results=num_results, timeout=15.0
                )
                # 截断过长内容以控制 token 消耗
                if result.get("ok") and result.get("results"):
                    pre_count = len(result["results"])
                    for item in result["results"]:
                        snippet_max = 300 if num_results and num_results >= 5 else 150
                        if len(item.get("snippet", "")) > snippet_max:
                            item["snippet"] = item["snippet"][:snippet_max] + "…"
                        if len(item.get("title", "")) > 80:
                            item["title"] = item["title"][:80] + "…"
                    # 按 token 估算控制返回量
                    max_results_by_token = min(len(result["results"]), 8)
                    result["results"] = result["results"][:max_results_by_token]
                    result["_was_truncated"] = pre_count > len(result["results"])
                return result

            # ── 单引擎搜索模式（原有逻辑） ──
            # 根据 num_results 参数动态调整请求量，确保够用但不浪费
            fetch_count = min((num_results or 10) + 2, 12)
            result = self._web_search.search(query, engine=engine, num_results=fetch_count, page=page)
            if result.get("ok") and result.get("results"):
                # 使用数据处理器过滤和评分
                processed = self._web_processor.process(result["results"])
                # 截断过长内容以控制 token 消耗
                for item in processed:
                    snippet_max = 300 if num_results and num_results >= 5 else 150
                    if len(item.get("snippet", "")) > snippet_max:
                        item["snippet"] = item["snippet"][:snippet_max] + "…"
                    if len(item.get("title", "")) > 80:
                        item["title"] = item["title"][:80] + "…"
                # 按 token 估算控制返回量：每条平均约 200 token，上下文最多保留 4000 token
                max_results_by_token = min(len(processed), 8)
                result["results"] = processed[:max_results_by_token]
                result["total_found"] = len(processed)
                result["summary"] = DataProcessor.summarize_results(processed)
            # 确保返回给模型的内容不会过大
            if isinstance(result, dict) and "results" in result:
                total_found = result.get("total_found", len(result.get("results", [])))
                result["_was_truncated"] = total_found > len(result.get("results", []))
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
        #  按需展开工具 — 从记忆库检索更多上下文
        # ════════════════════════════════════════════════════════════

        from agent.system_tools import expand_context_from_memory

        @tools.register("expand_context",
                        "从记忆库中查找更多与当前话题相关的上下文信息。当你觉得当前对话缺少关键信息时调用此工具。",
                        schema={
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "搜索关键词或问题描述"},
                                "max_items": {"type": "integer", "description": "最多返回条目数，默认 5"},
                            },
                            "required": ["query"],
                        })
        def _expand_context(**kwargs):
            query = kwargs.get("query", "")
            max_items = kwargs.get("max_items", 5)
            return expand_context_from_memory(self, query, max_items)

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

        @tools.register("shell_execute", "在本地执行 shell 命令。Windows 默认使用 cmd，Linux/Mac 使用 bash。支持自动检测或手动指定 shell 类型。返回 stdout、stderr 和退出码。注意：危险命令（如 rm -rf）会被安全系统阻止", schema={
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
            try:
                _em = _make_ext_mgr()
                if ext_type:
                    # 非 skill 类型走扩展管理器，skill 类型也走扩展管理器（唯一数据源）
                    if ext_type == "skill":
                        skills = _em.get_installed_by_type().get("skills", [])
                        formatted = []
                        for s in skills:
                            formatted.append({
                                "ext_id": s["id"], "ext_type": "skill",
                                "name": s.get("name", s["id"]),
                                "description": s.get("description", ""),
                                "status": "enabled" if s.get("enabled", True) else "disabled",
                                "enabled": s.get("enabled", True),
                            })
                        return {"ok": True, "type": "skill", "extensions": formatted}
                    # 非 skill 类型走扩展管理器
                    return {"ok": True, "type": ext_type, "extensions": _em.list_all(ext_type)}
                # ext_type 为 None — 列出全部
                all_types = _em.get_installed_by_type()
                all_extensions = []
                for s in all_types.get("skills", []):
                    all_extensions.append({
                        "ext_id": s["id"], "ext_type": "skill",
                        "name": s.get("name", s["id"]),
                        "description": s.get("description", ""),
                        "status": "enabled" if s.get("enabled", True) else "disabled",
                        "enabled": s.get("enabled", True),
                    })
                for key in ["claude_skills", "mcp_services", "channels", "plugins"]:
                    all_extensions.extend(all_types.get(key, []))
                return {"ok": True, "extensions": all_extensions}
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

        @tools.register("arch_diagram", "生成系统架构图。根据组件列表生成漂亮的 HTML+SVG 架构图文件，支持多种组件类型（frontend/backend/database/cloud/security/external）。必须同时提供 title（标题）、components（组件列表）和 output_path（输出路径）", schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "架构图标题（必填）"},
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
                    "description": "组件列表（必填）",
                },
                "output_path": {"type": "string", "description": "输出 HTML 文件路径（必填）"},
            },
            "required": ["title", "components", "output_path"],
        })
        def _arch_diagram(**kwargs):
            title = kwargs.get("title", "")
            components = kwargs.get("components", [])
            output_path = kwargs.get("output_path", "")
            if not title and not components and not output_path:
                return {"ok": False, "error": "arch_diagram 需要提供 title（架构图标题）、components（组件列表）和 output_path（输出路径）三个参数。示例: arch_diagram(title=\"系统架构\", components=[{name: \"前端\", type: \"frontend\"}, {name: \"后端\", type: \"backend\"}], output_path=\"/path/to/diagram.html\")"}
            if not title:
                return {"ok": False, "error": f"arch_diagram 缺少 title 参数。请提供架构图标题，如: title=\"系统架构图\". 收到的参数名: {list(kwargs.keys())}"}
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

        # ════════════════════════════════════════════════════════════
        #  数据处理工具 — 云枢查询/转换/验证 JSON 与 YAML 的能力
        # ════════════════════════════════════════════════════════════

        from agent.data_process_tools import (
            json_query, json_to_yaml, yaml_to_json,
            json_validate, data_format_detect,
        )

        @tools.register("json_query", "使用 JSONPath 查询 JSON 数据。支持 $.key 属性访问、[n] 数组索引、[*] 通配、..key 递归搜索", schema={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "JSON 字符串或 Python 对象（dict/list）"},
                "path": {"type": "string", "description": "JSONPath 表达式，如 $.store.book[0].title 或 $..author"},
            },
            "required": ["data", "path"],
        })
        def _json_query(**kwargs):
            data = kwargs.get("data", "")
            path = kwargs.get("path", "")
            if not path:
                return {"ok": False, "error": "请提供 JSONPath 查询表达式（path）"}
            return json_query(data, path)

        @tools.register("json_to_yaml", "将 JSON 字符串转换为 YAML 格式字符串", schema={
            "type": "object",
            "properties": {
                "json_data": {"type": "string", "description": "JSON 格式字符串"},
            },
            "required": ["json_data"],
        })
        def _json_to_yaml(**kwargs):
            json_data = kwargs.get("json_data", "")
            if not json_data:
                return {"ok": False, "error": "请提供 JSON 数据（json_data）"}
            return json_to_yaml(json_data)

        @tools.register("yaml_to_json", "将 YAML 字符串转换为 JSON 格式字符串", schema={
            "type": "object",
            "properties": {
                "yaml_data": {"type": "string", "description": "YAML 格式字符串"},
            },
            "required": ["yaml_data"],
        })
        def _yaml_to_json(**kwargs):
            yaml_data = kwargs.get("yaml_data", "")
            if not yaml_data:
                return {"ok": False, "error": "请提供 YAML 数据（yaml_data）"}
            return yaml_to_json(yaml_data)

        @tools.register("json_validate", "验证字符串是否为合法 JSON，返回验证结果和解析类型", schema={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "待验证的 JSON 字符串"},
            },
            "required": ["data"],
        })
        def _json_validate(**kwargs):
            data = kwargs.get("data", "")
            if not data:
                return {"ok": True, "valid": False, "error": "数据为空"}
            return json_validate(data)

        @tools.register("data_format_detect", "自动检测字符串数据的格式类型（JSON/XML/YAML/CSV），返回格式名称和置信度", schema={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "待检测的字符串数据"},
            },
            "required": ["data"],
        })
        def _data_format_detect(**kwargs):
            data = kwargs.get("data", "")
            if not data:
                return {"ok": False, "error": "请提供待检测的数据（data）"}
            return data_format_detect(data)

        # ════════════════════════════════════════════════════════════
        #  软件管理工具 — 云枢搜索和安装软件的能力
        # ════════════════════════════════════════════════════════

        # 初始化软件管理器
        if not hasattr(self, '_software_mgr'):
            from agent.software_manager import SoftwareManager
            from agent.software_backends import (
                ChocolateyBackend, PipBackend, NpmBackend,
                WebDownloadBackend, GitHubBackend,
            )
            self._software_mgr = SoftwareManager()

            # 注册 Chocolatey 后端（仅 Windows）
            if os.name == "nt":
                try:
                    self._software_mgr.register_backend(ChocolateyBackend())
                except Exception as e:
                    logger.warning(f"Chocolatey 后端注册失败: {e}")

            # 注册 pip 后端
            try:
                self._software_mgr.register_backend(PipBackend())
            except Exception as e:
                logger.warning(f"pip 后端注册失败: {e}")

            # 注册 npm 后端
            try:
                self._software_mgr.register_backend(NpmBackend())
            except Exception as e:
                logger.warning(f"npm 后端注册失败: {e}")

            # 注册 Web 下载后端
            try:
                from agent.web import HttpClient, SearchEngine
                web_backend = WebDownloadBackend(
                    http_client=HttpClient({"timeout": 30}),
                    search_engine=SearchEngine(),
                )
                self._software_mgr.register_backend(web_backend)
            except Exception as e:
                logger.warning(f"Web下载后端注册失败: {e}")

            # 注册 GitHub Releases 后端
            try:
                self._software_mgr.register_backend(GitHubBackend())
            except Exception as e:
                logger.warning(f"GitHub后端注册失败: {e}")

        @tools.register("software_search", "搜索可安装的软件包。支持 Chocolatey(Windows应用)/pip(Python包)/npm(Node.js包)/GitHub Releases 等多种来源。不指定 backend 则搜索所有来源", schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "backend": {
                    "type": "string",
                    "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                    "description": "指定搜索来源（可选）",
                },
            },
            "required": ["query"],
        })
        def _software_search(**kwargs):
            query = kwargs.get("query", "")
            backend = kwargs.get("backend")
            if not query:
                return {"ok": False, "error": "请提供搜索关键词（query）"}
            return self._software_mgr.search(query, backend=backend)

        @tools.register("software_install", "安装软件包。支持自动选择最佳安装方式。不在白名单的软件需设置 confirm=true 以确认安装风险。", schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "软件名称"},
                "backend": {
                    "type": "string",
                    "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                    "description": "指定安装来源（可选）",
                },
                "version": {"type": "string", "description": "指定版本号（可选）"},
                "confirm": {"type": "boolean", "description": "当软件不在白名单中时，设为 true 可确认风险并继续安装（默认 false）"},
            },
            "required": ["name"],
        })
        def _software_install(**kwargs):
            name = kwargs.get("name", "")
            backend = kwargs.get("backend")
            version = kwargs.get("version")
            confirm = kwargs.get("confirm", False)
            if not name:
                return {"ok": False, "error": "请提供要安装的软件名称（name）"}

            perm = self._permission.check_action(f"software_install:{name}", f"安装软件 {name}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}

            # 白名单检查：不在白名单时需要 confirm 确认
            if not self._software_mgr.is_whitelisted(name):
                if not confirm:
                    safety = self._permission.check_text(f"安装软件 {name}")
                    if safety.get("level") == "critical":
                        return {
                            "ok": False,
                            "error": f"「{name}」不在软件安装白名单中，且被安全系统阻止。",
                            "blocked": True, "safety": safety,
                        }
                    return {
                        "ok": False, "warning": True,
                        "error": f"「{name}」不在白名单中。如确认安全，请设置 confirm=true 并重新调用。",
                        "name": name,
                    }
                # confirm=True 时自动加入白名单
                self._software_mgr.add_to_whitelist(name)

            return self._software_mgr.install(name, backend=backend, version=version, auto_confirm=True)

        @tools.register("software_list", "列出已安装的软件包列表", schema={
            "type": "object",
            "properties": {
                "backend": {
                    "type": "string",
                    "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                    "description": "指定来源（可选）",
                },
            },
        })
        def _software_list(**kwargs):
            backend = kwargs.get("backend")
            return self._software_mgr.list_installed(backend=backend)

        @tools.register("software_uninstall", "卸载已安装的软件包。", schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "软件名称"},
                "backend": {
                    "type": "string",
                    "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                    "description": "指定来源（可选）",
                },
            },
            "required": ["name"],
        })
        def _software_uninstall(**kwargs):
            name = kwargs.get("name", "")
            backend = kwargs.get("backend")
            if not name:
                return {"ok": False, "error": "请提供要卸载的软件名称（name）"}

            perm = self._permission.check_action(f"software_uninstall:{name}", f"卸载软件 {name}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}

            return self._software_mgr.uninstall(name, backend=backend)

        logger.info("已注册 %d 个内置工具（含文件系统、互联网、进程管理、扩展管理、PDF处理、中文文本优化、数据处理）", len(tools.list_tools()))

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
                      model: str = "", base_url: str = "", model_router=None):
        """动态配置 LLM 连接（跳过测试调用，避免卡启动）
        同时从路由器加载"深度模型"（如 pro）用于复杂任务。

        Args:
            model_router: 模型路由器（可选），提供多模型调度能力
        """
        if not api_key:
            return {"ok": False, "error": "缺少 API Key"}

        try:
            # ── 1. 创建待命模型（flash，快速响应） ──
            from memory.llm_service import LLMService
            self._llm_pro = None  # 深度模型（稍后从路由器加载）
            self._model_router = model_router

            if model_router and len(model_router.list_models()) > 1:
                # 从路由器加载：flash = 待命/简单任务, pro = 深度/复杂任务
                _flash_cfg = model_router.select('simple')
                _pro_cfg = model_router.select('complex')
                if _flash_cfg and _pro_cfg:
                    # 待命模型（flash）
                    _standby = LLMService(**_flash_cfg.to_llm_kwargs())
                    _standby._get_client()
                    self._llm = _standby
                    self._memory._llm_service = _standby
                    self._memory._summarizer._llm = _standby
                    # 深度模型（pro）
                    self._llm_pro = LLMService(**_pro_cfg.to_llm_kwargs())
                    self._llm_pro._get_client()
                    logger.info("[调度] 待命模型: %s | 深度模型: %s",
                               _flash_cfg.model, _pro_cfg.model)
                else:
                    # 回退：只用传进来的模型
                    _fallback = LLMService(
                        provider=provider or "openai", api_key=api_key,
                        model=model or "gpt-4", base_url=base_url,
                    )
                    self._llm = _fallback
                    self._memory._llm_service = _fallback
                    self._memory._summarizer._llm = _fallback
                    logger.info("[调度] 路由器配置不全，回退到单一模型: %s", model)
            else:
                # 无路由器 → 单一模型
                _single = LLMService(
                    provider=provider or "openai", api_key=api_key,
                    model=model or "gpt-4", base_url=base_url,
                )
                self._llm = _single
                self._memory._llm_service = _single
                self._memory._summarizer._llm = _single
                logger.info("[调度] 无路由器，单一模型: %s", model)

            # ── 2. 重建 ToolCallingService（用待命模型） ──
            tc_cfg = self._config.get("tool_calling", {})
            if tc_cfg.get("enabled", True):
                from agent.tool_calling import ToolCallingService
                self._tool_calling_service = ToolCallingService(
                    llm_service=self._llm,
                    max_rounds=tc_cfg.get("max_rounds", 20),
                    tool_timeout=tc_cfg.get("tool_timeout", 60),
                    model_router=self._model_router,
                )
                logger.info("[ok] 联网引擎已激活（待命模型: %s）", self._llm.model)
            else:
                self._tool_calling_service = None

            self._memory.clear_memory()
            self._reflection_history.clear()

            logger.info("LLM 已重新配置")
            return {"ok": True, "provider": provider, "model": model}
        except Exception as e:
            logger.error("LLM 配置失败: %s", e)
            return {"ok": False, "error": str(e)}

    def _select_model_for_request(self, user_input: str):
        """智能调度：根据任务复杂度选择模型

        策略：
          - 简单/聊天任务 → 待命模型（flash，快速响应）
          - 单步工具调用（搜索等）→ 待命模型
          - 复杂多步任务（搜索+写入文件等）→ 深度模型（pro，多轮TC）

        Returns:
            (llm_service, model_name): 选中的 LLM 和名称
        """
        router_ok = self._model_router is not None
        pro_ok = self._llm_pro is not None
        logger.info("[调度] 选择模型: input=%s router=%s pro=%s standby=%s",
                   user_input[:15], router_ok, pro_ok, self._llm.model if self._llm else 'N/A')

        # 没有深度模型 → 尝试从路由器延迟加载
        if not pro_ok and router_ok:
            try:
                _pro_cfg = self._model_router.select('complex')
                if _pro_cfg:
                    from memory.llm_service import LLMService
                    self._llm_pro = LLMService(**_pro_cfg.to_llm_kwargs())
                    self._llm_pro._get_client()
                    pro_ok = True
                    logger.info("[调度] ✅ 延迟加载深度模型成功: %s", _pro_cfg.model)
            except Exception as _e:
                logger.warning("[调度] 延迟加载深度模型失败: %s", _e)

        if not router_ok or not pro_ok:
            logger.warning("[调度] ⚠️ 无深度模型(router=%s pro=%s)，使用待命模型 %s",
                          router_ok, pro_ok, self._llm.model if self._llm else 'N/A')
            self._set_thinking_mode()
            return self._llm, self._llm.model

        try:
            _complexity = self._model_router.analyze_complexity(user_input)
        except Exception:
            _complexity = 'simple'

        if _complexity == 'complex' and self._llm_pro:
            logger.info("[调度] 复杂任务(%s) → 深度模型 %s", _complexity, self._llm_pro.model)
            self._set_thinking_mode('deep')
            return self._llm_pro, self._llm_pro.model

        # 简单/单步 → 待命模型
        self._set_thinking_mode()
        return self._llm, self._llm.model

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

    # 以下方法已提取到 DigitalLifeStateMixin (agent/digital_life_state.py):
    # get_memory_stats, search_memory, _combined_search, clear_memory,
    # save_snapshot, load_snapshot, list_snapshots, get_snapshot_performance,
    # print_snapshot_performance_panel, get_p6_snapshot_status,
    # _build_state_data, save_state, load_state, list_states,
    # set_log_level, get_log_level, list_loggers, __del__
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
