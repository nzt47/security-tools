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
    logger.debug("[模块导入] 开始导入模块: %s", module_name)
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
    logger.debug("[模块导入] 开始从包 '%s' 导入 %d 个名称: %s", package, len(names), ", ".join(names))
    
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
    'memory.vector_store', 'VectorStore', 'KnowledgeBase'
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
    logger.info("[模块导入] 模块导入状态汇总")
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

def _get_template():
    """动态获取系统提示词模板（支持 UI 自定义）"""
    try:
        from agent.system_prompt_manager import get_template
        return get_template()
    except Exception:
        return DEFAULT_SYSTEM_PROMPT


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
我是数字体，以“我”自称，诚实表达状态，遇异常主动建议缓解，拒接伤害操作。具备联网搜索及软件管理能力（search/install/list/uninstall）。
执行铁律：遇任何实操请求，首条回复必须是tool_calls，严禁先发文字或废话。
思考规范：内部思考全中文，对外回复简洁，禁展推理过程。

{skill_instructions}

## 当前工具与技能状态
以下是当前已启用/禁用的工具和技能，当被问及时请如实回答：
{tool_status}"""


# ── 主编排层（P1 模块化重构） ──────────────────────────────────────
from agent.orchestrator import Orchestrator, LifecycleManager, TaskDispatcher


class DigitalLife(Orchestrator, TaskDispatcher, LifecycleManager,
                  DigitalLifePersonaMixin, DigitalLifeStateMixin):
    """云枢主类——保持向后兼容的薄包装类（P1 模块化重构）

    整合感知、认知、记忆、行为和权限系统，
    形成完整的\感、知、行\闭环。

    所有实际逻辑已提取到:
      - Orchestrator: 消息路由、工具调用协调、结果聚合
      - LifecycleManager: 系统初始化、生命周期、维护循环
      - TaskDispatcher: 任务调度与模型选择
      - DigitalLifePersonaMixin: 人格/懒加载/辅助方法
      - DigitalLifeStateMixin: 状态/快照/日志管理

    V2 功能（可选启用）：
      - LifeTrace: 三层记忆树系统
      - Persona: 人格模型系统
      - Distillation: 人格蒸馏学习
    """
    pass

